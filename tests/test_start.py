"""The launcher.

Two failures that actually happened on a real Windows machine, both of which
this file exists to stop recurring:

  * `python -m venv` created the environment and quietly left pip out of it --
    a known behaviour of the Microsoft Store build and several corporate images
    -- and the launcher reported it as "no internet connection", sending someone
    to debug a network that was fine.

  * An older install auto-starting at logon held port 8012, so the launcher
    called the port "already running" and opened a browser onto the OLD copy.
    The folder you just downloaded looked broken while a different one showed
    you stale code.
"""
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("startmod", ROOT / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)

SRC = (ROOT / "start.py").read_text()


# ── pip ────────────────────────────────────────────────────────────────────

def test_a_venv_without_pip_is_repaired_not_reported_as_a_network_problem():
    """ensurepip, then the official bootstrap, then a real error naming both."""
    fn = SRC[SRC.index("def ensure_pip("):SRC.index("def ensure_venv(")]
    assert "ensurepip" in fn
    assert "get-pip.py" in fn
    assert "Microsoft Store" in fn, "the usual Windows cause should be named"


def test_the_installer_never_guesses_at_a_cause_it_can_read():
    """The original said 'no internet connection' for a missing ensurepip."""
    assert "no internet" not in SRC.lower()
    deps = SRC[SRC.index("def ensure_deps("):SRC.index("def do_update(")]
    assert "pip said" in deps, "print what pip actually said"
    assert "r.stderr or r.stdout" in deps


def test_pip_is_verified_after_the_venv_is_built():
    """python.exe existing is not the same as the environment working — that is
    exactly the state the reported failure was in."""
    ev = SRC[SRC.index("def ensure_venv("):SRC.index("def stamp(")]
    assert "ensure_pip(py)" in ev


def test_get_pip_is_fetched_with_urllib_not_a_shell():
    """Matching INVOCATIONS, not the word: the function's own comment says it
    uses urllib rather than PowerShell, and a test that trips on the promise
    punishes documenting the decision."""
    import re
    fn = SRC[SRC.index("def ensure_pip("):SRC.index("def ensure_venv(")]
    assert "urllib.request.urlopen" in fn
    for banned in (r"powershell(\.exe)?\s+[-/]", r"\bpwsh\b", r"Invoke-WebRequest",
                   r"\bos\.system\b", r"subprocess\.\w+\([\"']curl"):
        assert not re.search(banned, fn, re.I), banned


# ── which copy owns the port ───────────────────────────────────────────────

def test_an_unused_port_reports_nobody():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert start.whoever_is_on_the_port(free) is None


def test_a_free_port_is_found_when_the_default_is_taken():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        alt = start.free_port(port)
        assert alt is not None and alt != port


def test_the_launcher_compares_roots_rather_than_just_liveness():
    """Something answering on 8012 is not the same as THIS copy answering."""
    fn = SRC[SRC.index("def serve("):SRC.index("def main(")]
    assert 'holder["root"]' in fn
    assert "ROOT" in fn, "it must compare against this folder"
    # An f-string, so the literal is "port {port} is taken by".
    assert "is taken by" in fn, "and say whose it is"


def test_a_foreign_instance_does_not_get_a_browser_opened_onto_it():
    fn = SRC[SRC.index("def serve("):SRC.index("def main(")]
    same = fn.index("same =")
    opened = fn.index("webbrowser.open", same)
    guard = fn[same:opened]
    assert "if same" in guard, "the browser opens only when it is this copy"


def test_an_instance_too_old_to_identify_itself_is_still_detected():
    """A previous version has no /api/instance. It still holds the port, and
    moving aside is better than colliding with it."""
    fn = SRC[SRC.index("def whoever_is_on_the_port("):SRC.index("def free_port(")]
    assert "HTTPError" in fn
    assert '"known": False' in fn


def test_the_instance_route_reports_the_folder_it_runs_from():
    from fastapi.testclient import TestClient

    from backend.app import app
    d = TestClient(app).get("/api/instance").json()
    assert d["app"] == "Meridian"
    assert Path(d["root"]).resolve() == ROOT
    assert d["pid"] > 0


def test_a_port_can_be_chosen_explicitly():
    assert "--port" in SRC


def test_setup_only_installs_without_starting_a_server():
    """install.bat's one-click chain depends on this: it must be able to
    finish the .venv/deps bootstrap and hand back control without also
    launching uvicorn, or a double-click would leave a server orphaned before
    install.py ever runs."""
    fn = SRC[SRC.index("def main("):]
    assert "--setup-only" in fn
    assert "setup_only" in fn
    assert (
        "if args.setup_only:\n"
        '        say("ready. Run this again without --setup-only to start.")\n'
        "        return 0\n"
    ) in fn, "the setup_only branch must return before falling through to serve()"


# ── the one-click installer ──────────────────────────────────────────────────

INSTALL_BAT = (ROOT / "install.bat").read_text()


def test_install_bat_exists_and_is_a_real_batch_file():
    assert INSTALL_BAT.lower().startswith("@echo off")


def test_install_bat_bootstraps_the_venv_before_installing_the_shortcut():
    """The whole point: one double-click on a bare clone (no .venv yet) must
    both build the environment AND install the shortcut, not require running
    start.bat first and install.py by hand second."""
    assert "start.py --setup-only" in INSTALL_BAT
    setup_idx = INSTALL_BAT.index("start.py --setup-only")
    install_idx = INSTALL_BAT.index("python.exe install.py")
    assert install_idx > setup_idx, "install.py must run after the venv is set up"


def test_install_bat_uses_no_powershell():
    import re
    for banned in (r"powershell(\.exe)?\s+[-/]", r"\bpwsh\b", r"Invoke-WebRequest",
                   r"Start-Process"):
        assert not re.search(banned, INSTALL_BAT, re.I), banned


def test_install_bat_reports_missing_python_the_same_way_start_bat_does():
    """Copy-paste drift check: both launchers hit the same failure mode (no
    python on PATH) and should send the user to the same fix."""
    start_bat = (ROOT / "start.bat").read_text()
    assert "python.org/downloads" in INSTALL_BAT
    assert "Add python.exe to PATH" in INSTALL_BAT
    assert "python.org/downloads" in start_bat  # sanity: still true of the sibling script


def test_install_bat_pauses_so_the_window_does_not_vanish_before_its_read():
    """A .bat launched by double-click runs in a console that closes itself
    the moment the script ends -- without a trailing pause, "Installed" would
    flash and disappear before anyone could read it."""
    assert INSTALL_BAT.rstrip().splitlines()[-2:] != []
    assert "pause" in INSTALL_BAT.lower()


def test_install_bat_tells_the_user_whether_it_actually_worked():
    assert "Installed" in INSTALL_BAT
    assert "errorlevel" in INSTALL_BAT.lower() or "%errorlevel%" in INSTALL_BAT.lower()


# ── auto-update ──────────────────────────────────────────────────────────────

INSTALL_PY = (ROOT / "install.py").read_text()
LAUNCH_PY = (ROOT / "launch.py").read_text()


def test_the_recurring_task_is_on_by_default_not_opt_in():
    """Auto-update only actually recurs while the machine is on if the 15-min
    task is registered -- the one-click install.bat path calls install.py
    with no flags, so the task has to be the default rather than something
    that used to require typing --task."""
    fn = INSTALL_PY[INSTALL_PY.index("if __name__"):]
    assert '"--no-task" not in sys.argv' in fn
    assert '"--task" in sys.argv' not in fn, "the flag must not still be opt-in"


def test_the_recurring_task_can_still_be_declined():
    assert "--no-task" in INSTALL_PY


def test_launch_rate_limits_the_github_check_so_the_15min_task_is_safe():
    """The self-heal task install.py registers fires every 15 minutes; without
    its own rate limit, wiring auto-update into it would hit the GitHub API
    roughly 96 times a day per installed copy."""
    fn = LAUNCH_PY[LAUNCH_PY.index("def check_for_update("):LAUNCH_PY.index("def open_window(")]
    assert "_update_due()" in fn
    assert "UPDATE_CHECK_INTERVAL_HOURS" in LAUNCH_PY


def test_an_applied_update_restarts_the_server_not_just_a_dead_one():
    """The whole point: swapping files on disk does nothing to a process that
    already has the old ones loaded, so main() must restart on an applied
    update even when the (stale) server is still answering health checks."""
    fn = LAUNCH_PY[LAUNCH_PY.index("def main("):]
    assert "updated = check_for_update()" in fn
    assert "if updated or not is_healthy():" in fn


def test_launch_uses_no_powershell_for_the_update_check():
    import re
    fn = LAUNCH_PY[LAUNCH_PY.index("def check_for_update("):LAUNCH_PY.index("def open_window(")]
    for banned in (r"powershell(\.exe)?\s+[-/]", r"\bpwsh\b", r"Invoke-WebRequest"):
        assert not re.search(banned, fn, re.I), banned


# ── the name ───────────────────────────────────────────────────────────────

def test_nothing_user_facing_still_says_the_old_name():
    """The app is Meridian. The only places the old name may appear are the
    back-compat paths that deliberately honour it."""
    allowed = {
        "backend/updater.py",        # GROUNDWORK_* env vars still honoured
        "backend/shared_paths.py",   # ~/.groundwork store still found
        "install.py",                # removes the old shortcut and logon task
        "README.md",                 # explains that it does
        "tests/test_start.py",       # this test
    }
    exts = {".py", ".js", ".html", ".css", ".md", ".json", ".bat", ".sh", ".command", ".yml"}
    skip = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".backup", "node_modules"}
    offenders = []
    for f in ROOT.rglob("*"):
        if not f.is_file() or any(p in skip for p in f.parts):
            continue
        if f.suffix not in exts and f.name != "Dockerfile":
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel in allowed:
            continue
        try:
            body = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "roundwork" in body.lower():
            offenders.append(rel)
    assert not offenders, f"still name the old app: {offenders}"


def test_the_old_environment_variables_are_still_honoured(monkeypatch):
    """A rename that silently ignores a variable somebody already set is a
    rename that costs them an afternoon."""
    from backend import updater
    monkeypatch.delenv("MERIDIAN_BRANCH", raising=False)
    monkeypatch.setenv("GROUNDWORK_BRANCH", "old-name")
    assert updater._repo()[1] == "old-name"
    monkeypatch.setenv("MERIDIAN_BRANCH", "new-name")
    assert updater._repo()[1] == "new-name", "the current name wins"


def test_the_old_shortcut_and_logon_task_are_removed_on_install():
    """A stale logon task starting the old folder is how you end up looking at
    old code on port 8012 and wondering why nothing changed."""
    src = (ROOT / "install.py").read_text()
    assert "Groundwork.lnk" in src
    assert "Groundwork Server" in src


def test_the_app_identifies_itself_by_the_current_name():
    from fastapi.testclient import TestClient

    from backend.app import app
    assert TestClient(app).get("/api/instance").json()["app"] == "Meridian"
