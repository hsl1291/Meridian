"""Regression tests from the line-by-line review of backend/app.py.

Each one drives the real route or helper with upstream HTTP replaced by an
httpx.MockTransport, so nothing here touches the network.
"""
import math
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.app as app_mod  # noqa: E402

client = TestClient(app_mod.app)


def _mock_upstream(monkeypatch, handler):
    """Every httpx.AsyncClient the app builds talks to `handler` instead of the network."""
    real = httpx.AsyncClient

    class Mocked(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(app_mod.httpx, "AsyncClient", Mocked)


# ── statewide parcel layers with text-typed numbers ─────────────────────────

class _FakeClient:
    def __init__(self, props):
        self.props = props

    async def get(self, *a, **kw):
        props = self.props

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": [{"properties": props,
                                      "geometry": {"type": "Polygon", "coordinates": []}}]}
        return R()


@pytest.mark.anyio
async def test_a_text_sale_price_does_not_sink_the_whole_parcel():
    """NJ-style layer returning "$125,000" and acreage as a string: float()
    raised, and /api/parcel reported "Upstream error" for the parcel."""
    fc = _FakeClient({"PAMS_PIN": "1234_5_6", "OWNER_NAME": "ACME LLC",
                      "SALE_PRICE": "$125,000", "DEED_DATE": "2021-05-01",
                      "CALC_ACRE": "0.5"})
    p = await app_mod._query_state_parcel("NJ", -74.0, 40.7, fc)
    assert p["owner"] == "ACME LLC"
    assert len(p["sales"]) == 1
    assert p["lot_size_sf"] == round(0.5 * 43560)


@pytest.mark.anyio
async def test_a_zero_or_negative_text_price_is_still_skipped():
    for price in ("$0", "-5", "n/a"):
        fc = _FakeClient({"PAMS_PIN": "x", "SALE_PRICE": price})
        p = await app_mod._query_state_parcel("NJ", -74.0, 40.7, fc)
        assert p["sales"] == [], price


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── condo roster: upstream outage is a 502 that says so ─────────────────────

def test_condo_units_reports_an_upstream_outage_as_502(monkeypatch):
    def down(request):
        raise httpx.ConnectError("boom", request=request)
    _mock_upstream(monkeypatch, down)
    r = client.get("/api/condo-units", params={"folio": "0141390001234"})
    assert r.status_code == 502
    assert "Miami-Dade parcel service" in r.json()["detail"]

    r = client.get("/api/condo-unit-values", params={"building_folio": "0141390001234"})
    assert r.status_code == 502


# ── medians ─────────────────────────────────────────────────────────────────

def test_comps_median_is_the_true_median_for_an_even_count(tmp_path, monkeypatch):
    db = tmp_path / "comps.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE listings (id, source, url, listing_type, address, city, lat, lng,
                   price, beds, baths, sqft, price_per_sqft, year_built, property_type,
                   days_on_market, status)""")
    con.execute("""CREATE TABLE buildings (id, name, address, city, lat, lng, status, pct_sold,
                   available_units, unit_count, height_stories, developer, architect, website,
                   sales_launch_date, neighborhood)""")
    for i, psf in enumerate((400, 500, 600, 700)):
        con.execute("INSERT INTO listings (id, listing_type, lat, lng, price_per_sqft) "
                    "VALUES (?, 'sale', 25.77, -80.19, ?)", (i, psf))
    con.commit()
    con.close()
    monkeypatch.setattr(app_mod, "COMPS_DB", db)

    r = client.get("/api/comps", params={"lon": -80.19, "lat": 25.77})
    assert r.json()["stats"]["sale"]["median_psf"] == 550


def test_no_upper_middle_median_left_in_the_backend():
    """sorted(x)[len(x) // 2] is the UPPER middle of an even-length list. It
    was in five places, one of them feeding the buyout estimate's $/sf."""
    import re
    offenders = []
    for base in ("backend", "scripts"):
        for f in (ROOT / base).rglob("*.py"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\[len\((\w+)\) // 2\]", line):
                    offenders.append(f"{f.relative_to(ROOT)}:{n}")
    assert offenders == []


# ── harvested registries with null coordinates ──────────────────────────────

def test_registry_entries_with_null_coordinates_are_skipped(monkeypatch):
    reg = [{"city": "Nowhere", "url": "u", "code": "Z", "lon": None, "lat": None},
           {"city": "Stringy", "url": "u", "code": "Z", "lon": "-80.1", "lat": "25.8"},
           {"city": "Real", "url": "u", "code": "Z", "lon": -80.19, "lat": 25.77}]
    assert [c["city"] for c in app_mod._nearby_cities(reg, -80.2, 25.8)] == ["Real"]
    monkeypatch.setattr(app_mod, "CITY_ZONING", reg)
    got = app_mod._zoning_candidates(-80.3, 25.7, -80.1, 25.9)
    assert [c.get("city") for kind, c in got if kind == "city"] == ["Real"]


# ── search boxes reach the radius east-west too ─────────────────────────────

def test_box_env_reaches_the_radius_in_both_directions():
    import json
    lon, lat, radius = -80.19, 25.77, 5280
    env = json.loads(app_mod._box_env(lon, lat, radius))
    ew = app_mod._haversine_ft(lat, lon, lat, env["xmax"])
    ns = app_mod._haversine_ft(lat, lon, env["ymax"], lon)
    assert math.isclose(ew, radius, rel_tol=0.01), ew
    assert math.isclose(ns, radius, rel_tol=0.01), ns


def test_build_stats_and_supply_pipeline_use_the_corrected_box():
    import inspect
    for fn in (app_mod.build_stats, app_mod.supply_pipeline):
        assert "_box_env(lon, lat, radius_ft)" in inspect.getsource(fn), fn.__name__


# ── BLS: newest published year, not a hardcoded one ─────────────────────────

def test_area_context_falls_back_to_the_newest_published_bls_year(monkeypatch):
    from datetime import date
    newest = date.today().year - 1
    asked = []

    def handler(request):
        url = str(request.url)
        if "geocoding.geo.census.gov" in url:
            return httpx.Response(200, json={"result": {"geographies": {
                "Census Tracts": [{"STATE": "12", "COUNTY": "086", "GEOID": "12086000100"}]}}})
        if "data.bls.gov" in url:
            yr = int(url.split("/api/")[1].split("/")[0])
            asked.append(yr)
            if yr == newest:
                return httpx.Response(404)
            return httpx.Response(200, text=("own_code,industry_code,annual_avg_emplvl,"
                                             "annual_avg_estabs,avg_annual_pay,year\n"
                                             f"0,10,1200000,100000,70000,{yr}\n"))
        return httpx.Response(500)
    _mock_upstream(monkeypatch, handler)

    body = client.get("/api/area-context", params={"lon": -80.19, "lat": 25.77}).json()
    assert asked[:2] == [newest, newest - 1]
    assert body["employment"]["year"] == str(newest - 1)
    assert body["employment"]["jobs"] == 1200000


# ── cross-site writes ───────────────────────────────────────────────────────

def test_a_cross_site_post_is_refused(monkeypatch):
    import backend.updater as updater
    ran = []
    monkeypatch.setattr(updater, "apply", lambda dry_run=False: ran.append(dry_run) or {"ok": True})

    r = client.post("/api/update/apply", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/update/apply", headers={"Origin": "null"})
    assert r.status_code == 403
    assert ran == [], "the update must not have run"

    # Same origin, and no Origin at all (scripts, curl), still work.
    assert client.post("/api/update/apply", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/update/apply").status_code == 200
    assert ran == [False, False]


def test_cross_site_reads_are_untouched():
    assert client.get("/api/instance", headers={"Origin": "https://evil.example"}).status_code == 200


# ── saved addresses: a busy database says so ────────────────────────────────

def test_a_locked_marks_db_explains_itself(monkeypatch):
    """The 503 carried only {"error": ...}; the UI reads `detail`, so all it
    could say was "is the server up?" -- about a server that was up."""
    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(app_mod, "_marks_conn", locked)
    r = client.get("/api/marks")
    assert r.status_code == 503
    assert "locked" in r.json()["detail"]
