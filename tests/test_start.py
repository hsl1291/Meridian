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
