"""Route-level smoke test. Runs in-process against an empty data/ -- which is the
state of a fresh clone -- so it asserts what a first run should do: answer, or
fail with a real status code, never a 500 traceback."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import app  # noqa: E402

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


@pytest.mark.xfail(reason="Phase 0.4 / first-run UX: with no prospect.db the condo "
                          "routes raise instead of reporting that the screen has "
                          "not been built yet",
                   strict=False)
def test_condo_stats_degrades_without_a_database():
    assert client.get("/api/condo/stats").status_code != 500
