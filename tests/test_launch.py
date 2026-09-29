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
import json
import os
import subprocess
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
    launch.VERSION_STAMP.write_text("{}", encoding="utf-8")
    assert launch._update_due() is False


def test_update_is_due_again_after_the_interval_elapses(launch):
    launch.VERSION_STAMP.write_text("{}", encoding="utf-8")
    stale = time.time() - (launch.UPDATE_CHECK_INTERVAL_HOURS * 3600 + 60)
    import os
    os.utime(launch.VERSION_STAMP, (stale, stale))
    assert launch._update_due() is True


# ── check_for_update: the network path is skipped when not due ─────────────

def test_check_for_update_does_nothing_when_not_due(launch, monkeypatch):
    launch.VERSION_STAMP.write_text("{}", encoding="utf-8")  # just checked -> not due again yet

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
    assert "applied 3 file(s)" in launch.UPDATE_LOG.read_text(encoding="utf-8")


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
    assert "Could not reach GitHub" in launch.UPDATE_LOG.read_text(encoding="utf-8")


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
    assert "dependency setup after update failed" in launch.UPDATE_LOG.read_text(encoding="utf-8")


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


# ── how often, and whether at all ───────────────────────────────────────────

def write_cfg(launch, update):
    d = launch.ROOT / "backend" / "prospect"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"update": update}), encoding="utf-8")


def test_the_default_check_interval_is_an_hour_not_a_day(launch, monkeypatch):
    """At 24h a change pushed to GitHub could take a day to reach the app, which
    reads as 'auto-update does not work'."""
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    assert launch._check_hours() == 1
    launch.VERSION_STAMP.write_text("{}", encoding="utf-8")
    os.utime(launch.VERSION_STAMP, (time.time() - 3500, time.time() - 3500))
    assert launch._update_due() is False
    os.utime(launch.VERSION_STAMP, (time.time() - 3700, time.time() - 3700))
    assert launch._update_due() is True


def test_the_interval_is_configurable_with_a_floor(launch):
    write_cfg(launch, {"check_hours": 6})
    assert launch._check_hours() == 6
    write_cfg(launch, {"check_hours": 0.001})
    assert launch._check_hours() == launch.MIN_CHECK_HOURS, "not faster than the 15-minute tick"
    write_cfg(launch, {"check_hours": "soon"})
    assert launch._check_hours() == launch.UPDATE_CHECK_INTERVAL_HOURS


def test_auto_update_can_be_switched_off(launch, monkeypatch):
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    assert launch._update_due() is True            # no stamp yet
    write_cfg(launch, {"auto": False})
    assert launch._update_due() is False
    write_cfg(launch, {"auto": True})
    monkeypatch.setenv("MERIDIAN_AUTO_UPDATE", "0")
    assert launch._update_due() is False, "the environment switch works without editing config"


def test_a_missing_or_broken_config_falls_back_to_the_defaults(launch, monkeypatch):
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    assert launch._auto_enabled() is True and launch._check_hours() == 1
    d = launch.ROOT / "backend" / "prospect"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{ broken", encoding="utf-8")
    assert launch._auto_enabled() is True and launch._check_hours() == 1


# ── a git clone updates with git pull ───────────────────────────────────────

class FakeGit:
    """Stands in for the git binary: records calls, answers rev-parse."""

    def __init__(self, heads, pull_rc=0, pull_err=""):
        self.heads, self.pull_rc, self.pull_err, self.calls = list(heads), pull_rc, pull_err, []

    def __call__(self, *args, timeout=90):
        self.calls.append(args)
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, self.heads.pop(0) + "\n", "")
        return subprocess.CompletedProcess(args, self.pull_rc, "", self.pull_err)


@pytest.fixture
def clone(launch, monkeypatch):
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    (launch.ROOT / ".git").mkdir()
    monkeypatch.setattr(launch, "GIT_STAMP", launch.ROOT / ".update-check")
    monkeypatch.setattr(launch.shutil, "which", lambda name: "/usr/bin/git")
    return launch


def test_a_clone_is_updated_with_a_fast_forward_pull_not_a_zip(clone, monkeypatch):
    """Overwriting a clone's working tree from a zip would leave every updated
    file showing as a local change and make the next git pull conflict."""
    git = FakeGit(["a" * 40, "b" * 40])
    monkeypatch.setattr(clone, "_git", git)
    monkeypatch.setattr(clone, "_zip_update", lambda: pytest.fail("a clone must not be zip-updated"))
    ran = []
    monkeypatch.setattr(clone.subprocess, "run", lambda *a, **k: ran.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    assert clone.check_for_update() is True
    assert ("pull", "--ff-only", "--quiet") in git.calls
    assert clone.GIT_STAMP.exists(), "rate-limited by its own stamp, like a zip copy"
    assert any("--setup-only" in str(c) for c in ran), "dependencies are refreshed after an update"
    assert "aaaaaaa -> bbbbbbb" in clone.UPDATE_LOG.read_text(encoding="utf-8")


def test_a_clone_that_is_already_current_does_not_restart_anything(clone, monkeypatch):
    monkeypatch.setattr(clone, "_git", FakeGit(["a" * 40, "a" * 40]))
    assert clone.check_for_update() is False
    assert clone._update_due() is False, "and it is not asked again until the interval passes"


def test_a_refused_pull_is_logged_and_never_forced(clone, monkeypatch):
    git = FakeGit(["a" * 40], pull_rc=1, pull_err="error: Your local changes would be overwritten")
    monkeypatch.setattr(clone, "_git", git)
    assert clone.check_for_update() is False
    assert "local changes would be overwritten" in clone.UPDATE_LOG.read_text(encoding="utf-8")
    assert not any(c[0] in ("reset", "checkout", "stash") for c in git.calls)
    assert not clone.GIT_STAMP.exists(), "a failed attempt is retried on the next tick"


def test_a_clone_without_git_installed_says_so(clone, monkeypatch):
    monkeypatch.setattr(clone.shutil, "which", lambda name: None)
    assert clone.check_for_update() is False
    assert "git is not installed" in clone.UPDATE_LOG.read_text(encoding="utf-8")


def test_a_zip_copy_still_uses_the_zip_updater(launch, monkeypatch):
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(launch, "_git_update", lambda: pytest.fail("no .git here"))
    called = []
    monkeypatch.setattr(launch, "_zip_update", lambda: called.append(1) or False)
    launch.check_for_update()
    assert called == [1]


# ── is_healthy: only THIS folder's server counts ────────────────────────────

def _serve(payload, status=200):
    """A throwaway HTTP server answering every GET with `payload`."""
    import http.server
    import json as _json
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("who, expected", [
    ("this", True),
    ("other", False),   # an older install still holding the port
    ("none", False),    # too old to have /api/instance's root field
])
def test_is_healthy_only_for_this_folders_server(launch, monkeypatch, tmp_path, who, expected):
    other = tmp_path.parent / (tmp_path.name + "-old-install")
    payload = {"app": "Meridian", "pid": 1,
               **({"root": str(launch.ROOT)} if who == "this" else
                  {"root": str(other)} if who == "other" else {})}
    srv = _serve(payload)
    try:
        monkeypatch.setattr(launch, "HEALTH_URL",
                            f"http://127.0.0.1:{srv.server_port}/api/instance")
        assert launch.is_healthy() is expected
    finally:
        srv.shutdown()


def test_is_healthy_is_false_when_nothing_answers(launch, monkeypatch):
    monkeypatch.setattr(launch, "HEALTH_URL", "http://127.0.0.1:9/api/instance")
    assert launch.is_healthy() is False


def test_the_health_check_asks_which_folder_is_serving():
    src = (ROOT / "launch.py").read_text(encoding="utf-8")
    assert 'HEALTH_PATH = "api/instance"' in src
