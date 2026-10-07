"""The in-app data build: the same scripts the README lists, run as one
background job the Reference tab can start, watch and cancel."""
import sqlite3
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.app as app_mod  # noqa: E402
import backend.data_build as db  # noqa: E402

client = TestClient(app_mod.app)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Fake scripts and a private store, so nothing real is built or touched."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    store = tmp_path / "shared.db"
    monkeypatch.setattr(db, "SCRIPTS", scripts)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    monkeypatch.setattr(db, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(db, "shared_db", lambda: store)
    monkeypatch.setattr(db, "_job", None)
    monkeypatch.setattr(db, "GROUPS", {"markets": {
        "title": "Markets", "detail": "d", "size": "s", "store": "shared", "check": "market",
        "steps": [("Download", "one.py", ["--all"]), ("Score", "two.py", [])]}})
    (scripts / "one.py").write_text(textwrap.dedent(f"""
        import sqlite3, sys
        print("args", sys.argv[1:])
        c = sqlite3.connect({str(store)!r}); c.execute("CREATE TABLE market (cbsa)"); c.commit()
    """), encoding="utf-8")
    (scripts / "two.py").write_text(textwrap.dedent(f"""
        import sqlite3
        c = sqlite3.connect({str(store)!r}); c.execute("INSERT INTO market VALUES ('33100')"); c.commit()
        print("scored 1")
    """), encoding="utf-8")
    return scripts, store


def _wait():
    db._thread.join(timeout=30)
    assert not db._thread.is_alive()


def test_status_says_not_built_before_anything_runs(sandbox):
    g = client.get("/api/build").json()["groups"][0]
    assert g["built"] is False and g["rows"] is None
    assert client.get("/api/build").json()["job"] is None


def test_a_build_runs_every_step_in_order_and_reports_built(sandbox):
    assert client.post("/api/build/markets").status_code == 200
    _wait()
    st = client.get("/api/build").json()
    assert st["job"]["state"] == "done", st["job"]
    assert st["groups"][0]["built"] is True and st["groups"][0]["rows"] == 1
    log = Path(st["job"]["log"]).read_text(encoding="utf-8")
    assert "args ['--all']" in log and log.index("Download") < log.index("Score")


def test_a_failing_step_stops_the_build_and_shows_why(sandbox):
    scripts, _ = sandbox
    (scripts / "one.py").write_text("print('fetching'); raise SystemExit('503 from census.gov')\n",
                                    encoding="utf-8")
    client.post("/api/build/markets")
    _wait()
    job = client.get("/api/build").json()["job"]
    assert job["state"] == "failed"
    assert "Download failed" in job["error"]
    assert "503 from census.gov" in job["error"], "the reason itself, not just 'exit 1'"
    assert "503 from census.gov" in "\n".join(job["tail"])
    assert "scored 1" not in Path(job["log"]).read_text(encoding="utf-8"), "step 2 must not run"


def test_only_one_build_runs_at_a_time(sandbox):
    scripts, _ = sandbox
    (scripts / "one.py").write_text("import time; time.sleep(30)\n", encoding="utf-8")
    assert client.post("/api/build/markets").status_code == 200
    try:
        r = client.post("/api/build/markets")
        assert r.status_code == 409 and "still building" in r.json()["detail"]
    finally:
        assert client.post("/api/build-cancel").json() == {"cancelled": True}
        _wait()
    assert client.get("/api/build").json()["job"]["state"] == "cancelled"
    assert client.post("/api/build-cancel").json() == {"cancelled": False}


def test_an_unknown_dataset_is_a_404(sandbox):
    assert client.post("/api/build/nope").status_code == 404


def test_a_cross_site_post_cannot_start_a_build(sandbox):
    r = client.post("/api/build/markets", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/build").json()["job"] is None


def test_the_background_python_has_no_console_window(tmp_path, monkeypatch):
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(db.sys, "executable", str(tmp_path / "pythonw.exe"))
    assert db._python().endswith("python.exe") and not db._python().endswith("pythonw.exe")


def test_every_real_step_is_a_script_that_exists():
    for gid, g in db.GROUPS.items():
        for label, script, _ in g["steps"]:
            assert (ROOT / "scripts" / "prospect" / script).is_file(), (gid, script)


def test_unbuilt_errors_point_at_the_button_not_at_env_vars():
    for exc in (sqlite3.OperationalError("unable to open database file: C:\\x\\shared.db"),
                sqlite3.OperationalError("no such table: market")):
        d = app_mod.unbuilt_detail(exc)["detail"]
        assert "Build data" in d
