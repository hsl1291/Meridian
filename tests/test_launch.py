"""The launcher's auto-update path.

launch.py is invoked every 15 minutes by the scheduled task install.py
registers (self-heal), so the actual GitHub check inside it has to be
rate-limited itself or an installed copy would hit the API every 15 minutes
forever. It reuses .version's own mtime for that -- written by
backend.updater.apply() on every reachable run -- rather than a second stamp
file that could drift out of sync with it.

When an update actually changes files, the running server (a separate OS
process that already has the old code loaded) has to be restarted for it to
take effect -- writing new files to disk does nothing to a process already
running. That is the one behavior this file cannot get wrong without the
whole feature being invisible.
"""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_launch():
    """A fresh module object per test -- launch.py keeps module-level state
    (PYTHON, VERSION_STAMP) that must not leak between tests."""
    spec = importlib.util.spec_from_file_location("launchmod", ROOT / "launch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def launch(tmp_path, monkeypatch):
    mod = _load_launch()
    # Redirect everything file-based into a scratch dir so a test run never
    # touches this checkout's own .version or logs/.
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(mod, "UPDATE_LOG", tmp_path / "logs" / "update.log")
    monkeypatch.setattr(mod, "VERSION_STAMP", tmp_path / ".version")
    return mod


# ── rate limiting ────────────────────────────────────────────────────────────

def test_update_is_due_when_no_stamp_exists_yet(launch):
    assert launch._update_due() is True


def test_update_is_not_due_right_after_a_check(launch):
    launch.VERSION_STAMP.write_text("{}")
    assert launch._update_due() is False


def test_update_is_due_again_after_the_interval_elapses(launch):
    launch.VERSION_STAMP.write_text("{}")
    stale = time.time() - (launch.UPDATE_CHECK_INTERVAL_HOURS * 3600 + 60)
    import os
    os.utime(launch.VERSION_STAMP, (stale, stale))
    assert launch._update_due() is True


# ── check_for_update: the network path is skipped when not due ─────────────

def test_check_for_update_does_nothing_when_not_due(launch, monkeypatch):
    launch.VERSION_STAMP.write_text("{}")  # just checked -> not due again yet

    def _boom(*a, **kw):
        raise AssertionError("apply() must not be called when an update is not due")

    monkeypatch.setitem(sys.modules, "backend.updater",
                        type(sys)("backend.updater"))
    sys.modules["backend.updater"].apply = _boom
    assert launch.check_for_update() is False


# ── check_for_update: applying an update ────────────────────────────────────

class _FakeCompletedProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _stub_updater(monkeypatch, result: dict):
    """backend.updater is imported lazily inside check_for_update() (`from
    backend.updater import apply`), so stubbing sys.modules is what actually
    intercepts it -- monkeypatching an attribute on the real module would not,
    since the real module may not even be importable in a bare test env."""
    fake_mod = type(sys)("backend.updater")
    fake_mod.apply = lambda: result
    monkeypatch.setitem(sys.modules, "backend.updater", fake_mod)
    fake_pkg = sys.modules.get("backend") or type(sys)("backend")
    monkeypatch.setitem(sys.modules, "backend", fake_pkg)


def test_check_for_update_restarts_when_files_actually_changed(launch, monkeypatch):
    _stub_updater(monkeypatch, {
        "ok": True, "updated": True, "changed_count": 3,
        "from_sha": "aaa", "to_sha": "bbb", "merged": False,
    })
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **kw: _FakeCompletedProcess(0))
    assert launch.check_for_update() is True
    assert "applied 3 file(s)" in launch.UPDATE_LOG.read_text()


def test_check_for_update_does_not_restart_when_nothing_changed(launch, monkeypatch):
    """apply() still succeeds and still wrote .version (rate-limiting the
    next check) even when this copy was already current -- there is just
    nothing that requires restarting a server over."""
    _stub_updater(monkeypatch, {"ok": True, "updated": False})
    calls = []
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **kw: calls.append(1) or _FakeCompletedProcess(0))
    assert launch.check_for_update() is False
    assert not calls, "no reason to reinstall dependencies when nothing changed"


def test_check_for_update_logs_and_returns_false_when_github_is_unreachable(launch, monkeypatch):
    _stub_updater(monkeypatch, {"ok": False, "error": "Could not reach GitHub: timed out"})
    assert launch.check_for_update() is False
    assert "Could not reach GitHub" in launch.UPDATE_LOG.read_text()


def test_check_for_update_still_signals_a_restart_if_dependency_setup_fails(launch, monkeypatch):
    """A code update that needs a new package the setup step failed to
    install should fail LOUDLY (the new code visibly not starting) rather
    than quietly keep the old server running as if nothing happened."""
    _stub_updater(monkeypatch, {
        "ok": True, "updated": True, "changed_count": 1,
        "from_sha": "a", "to_sha": "b", "merged": False,
    })
    monkeypatch.setattr(launch.subprocess, "run",
                        lambda *a, **kw: _FakeCompletedProcess(1))
    assert launch.check_for_update() is True
    assert "dependency setup after update failed" in launch.UPDATE_LOG.read_text()


# ── main(): an applied update forces a restart even if the old server is
#    still answering health checks ──────────────────────────────────────────

def test_main_restarts_the_server_when_an_update_applied_even_if_it_was_healthy(launch, monkeypatch):
    """The regression this whole feature is for: updating files on disk does
    nothing to a process that already loaded the old ones. If main() only
    restarted on a failed health check, an update would apply and then just
    sit there unused until the next crash."""
    monkeypatch.setattr(sys, "argv", ["launch.py", "--server-only"])
    monkeypatch.setattr(launch, "check_for_update", lambda: True)
    monkeypatch.setattr(launch, "is_healthy", lambda: True)  # old server, still up
    started = []
    monkeypatch.setattr(launch, "start_server", lambda: started.append(1))
    launch.main()
    assert started, "an applied update must restart the server even though it was healthy"


def test_main_does_not_restart_when_nothing_changed_and_server_is_healthy(launch, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py", "--server-only"])
    monkeypatch.setattr(launch, "check_for_update", lambda: False)
    monkeypatch.setattr(launch, "is_healthy", lambda: True)
    started = []
    monkeypatch.setattr(launch, "start_server", lambda: started.append(1))
    launch.main()
    assert not started
