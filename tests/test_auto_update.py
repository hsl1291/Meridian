"""The auto-update experience: what the app says about it, how it treats a git
clone, and that an updated server does not leave a stale page behind."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.updater as updater  # noqa: E402
from backend.app import app  # noqa: E402

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway install folder the updater is pointed at."""
    (tmp_path / "backend" / "prospect").mkdir(parents=True)
    monkeypatch.setattr(updater, "APP_ROOT", tmp_path)
    monkeypatch.setattr(updater, "STAMP", tmp_path / ".version")
    monkeypatch.delenv("MERIDIAN_AUTO_UPDATE", raising=False)
    return tmp_path


def cfg(home, update):
    (home / "backend" / "prospect" / "config.json").write_text(
        json.dumps({"update": update}), encoding="utf-8")


# ── status the Version card shows ───────────────────────────────────────────

def test_auto_status_defaults_to_on_and_hourly(home):
    s = updater.auto_status()
    assert s["enabled"] is True and s["check_hours"] == 1 and s["mode"] == "download"
    assert s["last_check"] is None


def test_auto_status_reports_the_last_check_from_the_stamp(home):
    (home / ".version").write_text("{}", encoding="utf-8")
    assert updater.auto_status()["last_check"]


def test_auto_status_reflects_the_switches(home, monkeypatch):
    cfg(home, {"auto": False, "check_hours": 6})
    s = updater.auto_status()
    assert s["enabled"] is False and s["check_hours"] == 6
    cfg(home, {"check_hours": 0.01})
    assert updater.auto_status()["check_hours"] == 0.25, "never faster than the 15-minute tick"
    cfg(home, {})
    monkeypatch.setenv("MERIDIAN_AUTO_UPDATE", "0")
    s = updater.auto_status()
    assert s["enabled"] is False and s["off_by_environment"] is True


def test_the_launcher_and_the_updater_agree_on_the_defaults():
    import importlib.util
    spec = importlib.util.spec_from_file_location("launch_for_defaults", ROOT / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    assert launch.UPDATE_CHECK_INTERVAL_HOURS == 1
    assert launch.MIN_CHECK_HOURS == 0.25


def test_the_check_route_carries_the_auto_status(home, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda: {"ok": True, "repo": "r", "branch": "b"})
    body = client.get("/api/update/check").json()
    assert body["ok"] is True and body["auto"]["enabled"] is True


# ── git clones ──────────────────────────────────────────────────────────────

def git_clone(home, sha="c" * 40, packed=False):
    g = home / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    if packed:
        (g / "packed-refs").write_text(f"# pack-refs\n{sha} refs/heads/main\n", encoding="utf-8")
    else:
        (g / "refs" / "heads" / "main").write_text(sha + "\n", encoding="utf-8")


def test_a_clones_version_is_read_from_git(home):
    git_clone(home)
    assert updater.installed()["sha"] == "c" * 40
    assert updater.auto_status()["mode"] == "git"


def test_a_clone_with_packed_refs_is_read_too(home):
    git_clone(home, sha="d" * 40, packed=True)
    assert updater.installed()["sha"] == "d" * 40


def test_a_detached_head_is_read(home):
    git_clone(home)
    (home / ".git" / "HEAD").write_text("e" * 40 + "\n", encoding="utf-8")
    assert updater.installed()["sha"] == "e" * 40


def test_the_install_button_refuses_to_overwrite_a_clone(home):
    """Replacing a clone's files from a zip leaves every one showing as a local
    change and makes the next git pull refuse."""
    git_clone(home)
    (home / "backend" / "app.py").write_text("mine\n", encoding="utf-8")
    r = updater.apply()
    assert r["ok"] is False and "git pull" in r["error"]
    assert (home / "backend" / "app.py").read_text(encoding="utf-8") == "mine\n"


# ── the page notices an update ──────────────────────────────────────────────

APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
WS_JS = (ROOT / "frontend" / "workspace.js").read_text(encoding="utf-8")


def test_the_page_offers_a_reload_when_the_server_process_changes():
    start = APP_JS.index("function watchForUpdate")
    body = APP_JS[start:APP_JS.index("\n}\n", start)]
    assert "d.pid === startPid" in body, "a new server process is the signal that new code is running"
    assert "location.reload()" in body
    assert "setInterval(check, 60000)" in body and "visibilitychange" in body
    assert "watchForUpdate(d.pid)" in APP_JS


def test_the_page_never_reloads_by_itself():
    """An automatic reload could discard a note someone is typing."""
    start = APP_JS.index("function watchForUpdate")
    body = APP_JS[start:APP_JS.index("\n}\n", start)]
    assert body.count("location.reload()") == 1
    assert "addEventListener('click', () => location.reload())" in body


def test_the_version_card_says_whether_it_updates_itself():
    assert "function autoLine" in WS_JS
    assert "Updates itself" in WS_JS and "Automatic updates are off" in WS_JS
    assert WS_JS.count("autoLine(d.auto)") == 2, "shown whether or not GitHub was reachable"


# ── a browser never runs stale scripts against a new backend ────────────────

def test_frontend_files_are_revalidated_but_the_pinned_map_library_is_cacheable():
    for path in ("/app.js", "/workspace.js", "/index.html", "/"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path
    lib = client.get("/vendor/maplibre-gl-4.7.1/maplibre-gl.js")
    assert lib.status_code == 200
    assert "no-cache" not in lib.headers.get("cache-control", "")


def test_an_unchanged_file_revalidates_to_a_304():
    first = client.get("/app.js")
    etag = first.headers["etag"]
    again = client.get("/app.js", headers={"If-None-Match": etag})
    assert again.status_code == 304, "no-cache must stay cheap"
