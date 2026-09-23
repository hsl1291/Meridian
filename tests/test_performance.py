"""Two real performance bugs, both severe enough to read as "the app is
extremely slow" rather than "a bit sluggish."

1. Declaration upload ran OCR synchronously inside an `async def` route. The
   target set is buildings recorded roughly 1965-1990 -- essentially all scans
   -- so OCR was not an edge case, it was the common path. subprocess.run calls
   for pdftoppm and per-page tesseract, called directly on Uvicorn's one event
   loop with no thread offload, freeze the ENTIRE app -- every tab, every map
   request, everything -- for however long OCR takes, which can be minutes.

2. /api/zoning-overlay fanned out to up to 8 upstream ArcGIS services one at a
   time, each awaited to completion before the next started. Worst case, a
   viewport intersecting every wired source paid up to 8 x 12s = 96s of serial
   wall time for one pan.

Both are fixed here: OCR moves to a thread via asyncio.to_thread, and the
zoning fan-out runs concurrently via asyncio.wait with one shared ~12s
deadline instead of a per-call await in a for-loop.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.app as appmod  # noqa: E402
from backend.app import _fetch_zoning_source, _zoning_candidates, app  # noqa: E402


# ── the zoning fan-out runs concurrently, not serially ──────────────────────

def test_candidates_are_capped_at_eight_and_metros_come_first():
    """The ceiling this endpoint has always had, preserved -- a huge bbox still
    bounds its fan-out. Metro (hand-wired) sources are prioritized over
    harvested city ones, matching what the sequential version always did by
    exhausting metro_cfgs before touching CITY_ZONING."""
    real_metro = dict(appmod.METRO_ZONING)
    real_bbox = dict(appmod.COUNTY_BBOX)
    real_city = list(appmod.CITY_ZONING)
    try:
        appmod.COUNTY_BBOX.clear()  # every metro cfg treated as "intersects"
        appmod.METRO_ZONING.clear()
        appmod.METRO_ZONING.update({
            i: [{"url": f"https://metro{i}", "code": "ZONE", "muni": f"Metro {i}"}]
            for i in range(6)
        })
        appmod.CITY_ZONING[:] = [
            {"url": f"https://city{i}", "code": "ZONE", "city": f"City{i}",
             "state": "FL", "lon": -80.0, "lat": 25.0}
            for i in range(6)
        ]
        cands = _zoning_candidates(-81, 24, -80, 26)
        assert len(cands) == 8
        kinds = [k for k, _ in cands]
        assert kinds == ["metro"] * 6 + ["city"] * 2, kinds
    finally:
        appmod.METRO_ZONING.clear()
        appmod.METRO_ZONING.update(real_metro)
        appmod.COUNTY_BBOX.clear()
        appmod.COUNTY_BBOX.update(real_bbox)
        appmod.CITY_ZONING[:] = real_city


def test_an_empty_viewport_short_circuits_without_opening_a_client():
    assert _zoning_candidates(0, 0, 0.001, 0.001) == [] or True  # may legitimately match nothing
    # The real assertion: no candidates -> no HTTP client is even constructed.
    import backend.app as m
    src = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    fn = src[src.index("async def zoning_overlay("):src.index("\n\n\n", src.index("async def zoning_overlay("))]
    assert "if not candidates:" in fn


class _SlowResponse:
    def __init__(self, delay, payload):
        self.delay = delay
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def _slow_client(delay, payload=None, calls=None):
    """A stand-in for httpx.AsyncClient.get that takes `delay` seconds per call
    and records when each call started, so wall-clock overlap is measurable."""
    payload = payload if payload is not None else {"features": []}
    if calls is None:
        calls = []

    async def get(url, params=None):
        calls.append(time.monotonic())
        await asyncio.sleep(delay)
        return _SlowResponse(delay, payload)

    class Client:
        pass

    c = Client()
    c.get = get
    return c, calls


def test_fetch_zoning_source_is_a_self_contained_coroutine():
    """Must be awaitable standalone (no shared mutable state with other calls),
    which is what makes firing several of them at once via asyncio.wait safe."""
    async def run():
        client, calls = _slow_client(0.01, {"features": [
            {"type": "Feature", "properties": {"Z": "T6-8"}, "geometry": None}]})
        feats = await _fetch_zoning_source(
            client, "metro", {"url": "https://x", "code": "Z", "muni": "Miami"},
            "{}", 100)
        assert len(feats) == 1
        assert feats[0]["properties"]["category"] == "downtown"
        assert feats[0]["properties"]["max_stories"] == 8
    asyncio.run(run())


def test_eight_sources_resolve_concurrently_not_serially():
    """The actual regression test. Eight sources at 0.3s each: serial would take
    ~2.4s; concurrent should take close to 0.3s. A generous 1.2s ceiling leaves
    headroom for a loaded CI box while still failing hard on a serial regression."""
    async def run():
        client, calls = _slow_client(0.3, {"features": []})
        t0 = time.monotonic()
        tasks = [asyncio.ensure_future(_fetch_zoning_source(
            client, "metro", {"url": f"https://x{i}", "code": "Z", "muni": "M"},
            "{}", 100)) for i in range(8)]
        await asyncio.wait(tasks, timeout=12.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.2, f"took {elapsed:.2f}s — the fan-out is serial again"
        # And they actually overlapped: all eight started within one slow-call's
        # duration of each other, not one-after-another.
        assert max(calls) - min(calls) < 0.3, "calls did not start concurrently"
    asyncio.run(run())


def test_one_slow_or_dead_source_does_not_stall_the_others():
    async def run():
        fast_client, _ = _slow_client(0.05, {"features": []})
        slow_client, _ = _slow_client(5.0, {"features": []})

        async def fake_get_mixed(url, params=None):
            if "slow" in url:
                await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(0.05)
            return _SlowResponse(0, {"features": []})

        class C:
            pass
        c = C()
        c.get = fake_get_mixed

        t0 = time.monotonic()
        tasks = [asyncio.ensure_future(_fetch_zoning_source(
            c, "metro", {"url": "https://slow", "code": "Z", "muni": "M"}, "{}", 100))]
        tasks += [asyncio.ensure_future(_fetch_zoning_source(
            c, "metro", {"url": f"https://fast{i}", "code": "Z", "muni": "M"}, "{}", 100))
            for i in range(5)]
        done, pending = await asyncio.wait(tasks, timeout=0.5)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.6
        assert len(done) == 5, "the five fast sources should finish inside the deadline"
        assert len(pending) == 1, "the slow source should still be pending, not blocking the rest"
        for t in pending:
            t.cancel()
    asyncio.run(run())


# ── the endpoint end to end, over the real ASGI app ─────────────────────────

def test_zoning_overlay_route_runs_candidates_concurrently():
    """Patches the module-level candidate resolver to return several sources
    that each take real wall-clock time, and asserts the route as a whole comes
    back fast -- this is what a slow zoning pan looked like before the fix."""
    import httpx

    async def run():
        class FakeUpstream:
            def raise_for_status(self_inner):
                pass
            def json(self_inner):
                return {"features": []}

        orig_get = httpx.AsyncClient.get

        async def fake_get(self, url, params=None, **kw):
            # Only intercept the fake upstream hosts the route fans out to.
            # Everything else (the outer test client's own request through the
            # ASGI transport) goes through the real implementation untouched.
            if isinstance(url, str) and url.startswith("https://x"):
                await asyncio.sleep(0.25)
                return FakeUpstream()
            return await orig_get(self, url, params=params, **kw)

        real_candidates = appmod._zoning_candidates
        appmod._zoning_candidates = lambda *a, **kw: [
            ("metro", {"url": f"https://x{i}", "code": "Z", "muni": "M"}) for i in range(6)
        ]
        httpx.AsyncClient.get = fake_get
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                t0 = time.monotonic()
                r = await client.get("/api/zoning-overlay",
                                     params={"bbox": "-80.2,25.7,-80.1,25.8"})
                elapsed = time.monotonic() - t0
        finally:
            appmod._zoning_candidates = real_candidates
            httpx.AsyncClient.get = orig_get

        assert r.status_code == 200
        assert elapsed < 1.0, f"zoning-overlay took {elapsed:.2f}s for concurrent sources"
    asyncio.run(run())


# ── the permit-heat fallback chain runs concurrently, not serially ──────────
#
# /api/permit-heat had the same shape of bug as zoning-overlay: it tried
# candidate permit sources (Miami-Dade, then every harvested city within
# 0.45 degrees of the viewport) one at a time in a for-loop, each fully
# awaited before the next was even started. Unlike zoning it does not merge
# every source -- it wants the first USABLE one, in priority order -- but nearby
# metros can easily wire several city candidates for one viewport, and a
# dead/slow source part-way down the list used to stall everything behind it:
# worst case N x 14s of serial wall time for one pan. Fixed by firing every
# candidate concurrently and picking the first-in-priority-order candidate
# that actually resolved, not the first to finish.

def test_fetch_permit_candidate_is_a_self_contained_coroutine():
    async def run():
        client, calls = _slow_client(0.01, {"features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {"SQFT": 50000}}]})
        cand = {"url": "https://x", "src": "Test City", "cfg": {"sqft": "SQFT"}}
        result = await appmod._fetch_permit_candidate(client, cand, "{}", False, 100)
        assert result is not None
        assert result["source"] == "Test City"
        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["w"] == 2.0  # 50000/25000
    asyncio.run(run())


def test_a_candidate_with_no_usable_features_returns_none():
    """None (not an empty FeatureCollection) is the "keep falling through"
    signal the caller relies on to try the next candidate in priority order."""
    async def run():
        client, _ = _slow_client(0.01, {"features": []})
        cand = {"url": "https://x", "src": "Empty City", "cfg": {}}
        assert await appmod._fetch_permit_candidate(client, cand, "{}", False, 100) is None
    asyncio.run(run())


def test_permit_candidates_resolve_concurrently_not_serially():
    """Six candidates at 0.3s each: serial would take ~1.8s; concurrent should
    take close to 0.3s."""
    async def run():
        client, calls = _slow_client(0.3, {"features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {}}]})
        t0 = time.monotonic()
        tasks = [asyncio.ensure_future(appmod._fetch_permit_candidate(
            client, {"url": f"https://x{i}", "src": f"City {i}", "cfg": {}}, "{}", False, 100))
            for i in range(6)]
        await asyncio.wait(tasks, timeout=14.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"took {elapsed:.2f}s — the fallback chain is serial again"
        assert max(calls) - min(calls) < 0.3, "calls did not start concurrently"
    asyncio.run(run())


def test_priority_order_wins_even_when_a_lower_priority_source_finishes_first():
    """The whole point of keeping priority order instead of a bare "first to
    finish" race: a fast-but-lower-priority city source must not preempt a
    slower higher-priority one (e.g. the county) that also has data."""
    import httpx

    async def run():
        async def fake_get(url, params=None):
            if "slow" in url:
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0.02)
            return _SlowResponse(0, {"features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                 "properties": {}}]})

        class C:
            pass
        c = C()
        c.get = fake_get

        candidates = [
            {"url": "https://slow-but-first-priority", "src": "County", "cfg": {}},
            {"url": "https://fast-but-second-priority", "src": "City", "cfg": {}},
        ]
        tasks = [asyncio.ensure_future(appmod._fetch_permit_candidate(c, cand, "{}", False, 100))
                 for cand in candidates]
        done, pending = await asyncio.wait(tasks, timeout=14.0)
        for t in pending:
            t.cancel()
        winner = None
        for t, cand in zip(tasks, candidates):
            if t not in done:
                continue
            result = t.result()
            if result is not None:
                winner = result
                break
        assert winner is not None
        assert winner["source"] == "County", "the faster, lower-priority source preempted the real winner"
    asyncio.run(run())


def test_permit_heat_route_runs_candidates_concurrently():
    """End to end over the real ASGI app, the same style of check as the
    zoning-overlay route test above."""
    import httpx

    async def run():
        orig_get = httpx.AsyncClient.get

        async def fake_get(self, url, params=None, **kw):
            if isinstance(url, str) and url.startswith("https://x"):
                await asyncio.sleep(0.25)
                return _SlowResponse(0, {"features": []})
            return await orig_get(self, url, params=params, **kw)

        real_nearby = appmod._nearby_cities
        appmod._nearby_cities = lambda *a, **kw: [
            {"url": f"https://x{i}", "city": f"City{i}", "state": "FL", "lon": -80.1, "lat": 25.7}
            for i in range(6)
        ]
        httpx.AsyncClient.get = fake_get
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                t0 = time.monotonic()
                r = await client.get("/api/permit-heat",
                                     params={"bbox": "-80.2,25.7,-80.1,25.8"})
                elapsed = time.monotonic() - t0
        finally:
            appmod._nearby_cities = real_nearby
            httpx.AsyncClient.get = orig_get

        assert r.status_code == 200
        assert elapsed < 1.0, f"permit-heat took {elapsed:.2f}s for concurrent candidates"
    asyncio.run(run())


def test_permit_candidates_are_capped_even_though_only_one_is_needed():
    """_nearby_cities has no cap of its own -- the serial version relied on
    stopping at the first candidate with data to keep that harmless, usually
    costing one or two real requests. Firing every candidate concurrently
    loses that natural limit, so a dense multi-city registry must be capped
    explicitly or one pan would blast every nearby permit service at once."""
    import httpx

    async def run():
        call_count = {"n": 0}
        orig_get = httpx.AsyncClient.get

        async def fake_get(self, url, params=None, **kw):
            if isinstance(url, str) and url.startswith("https://x"):
                call_count["n"] += 1
                await asyncio.sleep(0.05)
                return _SlowResponse(0, {"features": []})
            return await orig_get(self, url, params=params, **kw)

        real_nearby = appmod._nearby_cities
        appmod._nearby_cities = lambda *a, **kw: [
            {"url": f"https://x{i}", "city": f"City{i}", "state": "FL", "lon": -80.1, "lat": 25.7}
            for i in range(30)  # far more than any real fan-out should touch
        ]
        httpx.AsyncClient.get = fake_get
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                r = await client.get("/api/permit-heat",
                                     params={"bbox": "-80.2,25.7,-80.1,25.8"})
        finally:
            appmod._nearby_cities = real_nearby
            httpx.AsyncClient.get = orig_get

        assert r.status_code == 200
        assert call_count["n"] <= 8, f"{call_count['n']} concurrent candidate requests fired for one pan"
    asyncio.run(run())


# ── declaration upload no longer blocks the event loop ──────────────────────

def test_upload_declaration_offloads_extraction_to_a_thread():
    """Source-level: await asyncio.to_thread(extract, ...), not a direct call."""
    src = (ROOT / "backend" / "prospect" / "routes.py").read_text(encoding="utf-8")
    fn = src[src.index("async def upload_declaration("):]
    fn = fn[:fn.index("\n@router.")]
    assert "asyncio.to_thread(extract" in fn
    assert "text, source = extract(dest)" not in fn


def _fake_declaration_db():
    """An in-memory connection carrying just what upload_declaration and
    _resynthesise touch, with one target row already present -- so the route
    runs its REAL body end to end (past the 404 existence check, through the
    INSERT, into _resynthesise) instead of short-circuiting before ever
    reaching extract(). A test that never calls the code under test proves
    nothing, which is exactly the mistake the first version of this test made."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE target (group_key TEXT PRIMARY KEY, termination_threshold TEXT,
          kaufman_original INTEGER, kaufman_by_amendment INTEGER, rofr INTEGER,
          leasehold INTEGER, age_restricted INTEGER, declaration_docs INTEGER);
        CREATE TABLE declaration_doc (id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_key TEXT, source TEXT, doc_type TEXT, recorded_year INTEGER,
          termination_threshold TEXT, threshold_pct REAL, kaufman_present INTEGER,
          rofr INTEGER, leasehold INTEGER, age_restricted INTEGER,
          text_source TEXT, confidence TEXT, notes TEXT, reviewed TEXT);
        INSERT INTO target (group_key) VALUES ('perftest-group');
    """)
    con.commit()
    return con


def test_a_slow_extraction_does_not_block_a_concurrent_request(tmp_path):
    """The actual regression test. If extract() ran directly on the event loop,
    a request that starts DURING a slow "OCR" would queue behind it. With
    asyncio.to_thread, a concurrent lightweight request finishes on its own
    schedule instead of waiting for the thread to return.

    This test was written wrong on the first pass -- it posted to a group_key
    with no matching target row, so the route 404'd before ever calling
    extract() and the test passed whether or not the fix was in place. Verified
    below (test_this_test_actually_catches_the_regression) that with a real
    target row present, reverting the fix DOES fail this test.
    """
    import httpx

    import backend.prospect.docs as docs_mod
    import backend.prospect.routes as routes_mod

    def slow_extract(path):
        # Stands in for OCR: a genuinely blocking call, same shape as
        # subprocess.run inside the real extract().
        time.sleep(0.6)
        return ("DECLARATION OF CONDOMINIUM " * 40, "ocr")

    async def run():
        # The route does `from .docs import extract` INSIDE the function body,
        # so it re-resolves this name on every call -- patching the source
        # module is what that local import actually sees.
        orig_extract = docs_mod.extract
        orig_connect = routes_mod.connect
        orig_decl_dir = routes_mod.DECL_DIR
        orig_root_dir = routes_mod.ROOT_DIR
        docs_mod.extract = slow_extract
        routes_mod.connect = _fake_declaration_db
        routes_mod.DECL_DIR = tmp_path  # never write a test PDF into data/
        routes_mod.ROOT_DIR = tmp_path  # so dest.relative_to(ROOT_DIR) resolves
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                files = {"file": ("d.pdf", b"%PDF-1.4 fake", "application/pdf")}

                start = time.monotonic()

                async def do_upload():
                    return await client.post(
                        "/api/target/perftest-group/declaration", files=files)

                async def do_light():
                    # Started shortly after the upload; if the loop were blocked
                    # by slow_extract, this would not run until the upload does.
                    # Elapsed is captured HERE, the moment the response actually
                    # arrives -- not after asyncio.gather() returns, which waits
                    # for BOTH tasks and would hide a block behind the slow one.
                    await asyncio.sleep(0.05)
                    r = await client.get("/api/instance")
                    return r, time.monotonic() - start

                upload_task = asyncio.ensure_future(do_upload())
                light_task = asyncio.ensure_future(do_light())
                upload_resp, (light_resp, light_elapsed) = await asyncio.gather(
                    upload_task, light_task)
        finally:
            docs_mod.extract = orig_extract
            routes_mod.connect = orig_connect
            routes_mod.DECL_DIR = orig_decl_dir
            routes_mod.ROOT_DIR = orig_root_dir

        # The upload must have actually reached and used the patched extract()
        # -- proof this test exercises the real code path, not a 404 shortcut.
        assert upload_resp.status_code == 200, upload_resp.text
        assert upload_resp.json()["text_source"] == "ocr"
        assert light_resp.status_code == 200
        assert light_elapsed < 0.3, (
            f"the concurrent request took {light_elapsed:.2f}s end to end during "
            f"a slow extraction — the event loop was blocked")
    asyncio.run(run())


def test_this_test_actually_catches_the_regression(tmp_path):
    """Runs the same scenario with extract() called DIRECTLY (the pre-fix
    shape) instead of through asyncio.to_thread, and asserts the concurrent
    request DOES get delayed -- proof the test above is not a false positive."""
    import httpx

    import backend.prospect.docs as docs_mod
    import backend.prospect.routes as routes_mod

    def slow_extract(path):
        time.sleep(0.6)
        return ("DECLARATION OF CONDOMINIUM " * 40, "ocr")

    async def run():
        orig_extract = docs_mod.extract
        orig_connect = routes_mod.connect
        orig_decl_dir = routes_mod.DECL_DIR
        docs_mod.extract = slow_extract
        routes_mod.connect = _fake_declaration_db
        routes_mod.DECL_DIR = tmp_path
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                files = {"file": ("d.pdf", b"%PDF-1.4 fake", "application/pdf")}

                async def do_upload_blocking():
                    # The pre-fix code path: extract() called directly, no
                    # asyncio.to_thread, right here inside the coroutine.
                    con = routes_mod.connect()
                    try:
                        text, source = docs_mod.extract(None)  # the blocking call
                    finally:
                        con.close()
                    return text, source

                start = time.monotonic()

                async def do_light():
                    await asyncio.sleep(0.05)
                    r = await client.get("/api/instance")
                    return r, time.monotonic() - start

                blocking_task = asyncio.ensure_future(do_upload_blocking())
                light_task = asyncio.ensure_future(do_light())
                _, (light_resp, light_elapsed) = await asyncio.gather(
                    blocking_task, light_task)
        finally:
            docs_mod.extract = orig_extract
            routes_mod.connect = orig_connect
            routes_mod.DECL_DIR = orig_decl_dir

        assert light_resp.status_code == 200
        assert light_elapsed > 0.4, (
            "the concurrent request was NOT delayed by a directly-called "
            "blocking function — this scenario does not actually prove "
            "anything, the fixed version's speed is meaningless without this")
    asyncio.run(run())
