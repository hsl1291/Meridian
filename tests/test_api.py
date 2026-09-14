"""Route-level smoke test. Runs in-process against an empty data/ -- which is the
state of a fresh clone -- so it asserts what a first run should do: answer, or
fail with a real status code, never a 500 traceback."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import app, unbuilt_detail  # noqa: E402

client = TestClient(app)


def test_shell_serves():
    assert client.get("/").status_code == 200


def test_zoning_overlay_returns_a_feature_collection():
    r = client.get("/api/zoning-overlay",
                   params={"bbox": "-80.145,25.780,-80.115,25.800"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)


def test_zoning_overlay_rejects_a_bad_bbox():
    assert client.get("/api/zoning-overlay", params={"bbox": "nope"}).status_code == 400


@pytest.mark.skipif((Path(__file__).resolve().parents[1] / "data" / "prospect.db").exists(),
                    reason="needs an unbuilt store; data/prospect.db is present. "
                           "Note connect() CREATES the schema, so running any "
                           "pipeline script once puts the install in a third state: "
                           "built-but-empty, which this route cannot distinguish "
                           "from never-built.")
def test_condo_routes_report_an_unbuilt_store():
    """A fresh clone has an empty data/ and no shared store, so these routes are
    querying something that does not exist. That must read as "not built yet",
    not as a 500 -- from the UI a traceback is indistinguishable from the app
    being broken."""
    for path in ("/api/condo/stats", "/api/targets", "/api/condo/cities", "/api/metros"):
        r = client.get(path)
        assert r.status_code == 503, f"{path} -> {r.status_code}"
        body = r.json()
        assert body["state"] in ("table_not_built", "shared_store_missing"), path
        assert body["detail"], path


def test_missing_table_names_the_script_that_builds_it():
    import sqlite3
    info = unbuilt_detail(sqlite3.OperationalError("no such table: target"))
    assert info["state"] == "table_not_built"
    assert info["table"] == "target"
    assert info["builder"] == "scripts/prospect/build_targets.py"
    # Schema-qualified names resolve to the bare table.
    assert unbuilt_detail(sqlite3.OperationalError(
        "no such table: shared.market"))["builder"] == "scripts/prospect/build_markets.py"


def test_missing_shared_store_is_reported_separately():
    import sqlite3
    info = unbuilt_detail(sqlite3.OperationalError(
        r"unable to open database: C:\Apps\_shared/shared.db"))
    assert info["state"] == "shared_store_missing"
    assert "APPS_SHARED_DB" in info["detail"]


def test_a_real_sqlite_fault_is_not_swallowed():
    """Only the two first-run states are translated. Everything else must return
    None so the handler re-raises, or genuine faults hide behind a friendly
    message."""
    import sqlite3
    for msg in ("no such column: nope", "database is locked",
                "attempt to write a readonly database", "syntax error near \"FROM\""):
        assert unbuilt_detail(sqlite3.OperationalError(msg)) is None, msg
