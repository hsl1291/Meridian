"""Sunbiz officers as a beneficial-owner link -- and every way it must NOT link.

A false merge fabricates the concentration score this screen is ranked on, so
most of these tests are about refusing: fuzzy name matches, registered agents,
companies as officers, nominees on everything, individuals, and network blips
being cached as 'not registered'."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.prospect import sunbiz  # noqa: E402
from backend.prospect.beneficial import cluster, top_beneficial  # noqa: E402


def search_page(*names):
    rows = "".join(f'<tr><td><a href="/Inquiry/CorporationSearch/SearchResultDetail?x={i}&amp;y=1">{n}</a></td></tr>'
                   for i, n in enumerate(names))
    return f"<table>{rows}</table>"


def detail_page(name, officers, agent="REGISTERED AGENTS INC"):
    people = "".join(f"<br/><br/>Title&nbsp;{t}<br/><br/>{n}<span><div>1 X ST<br/>MIAMI, FL</div></span>"
                     for n, t in officers)
    return (f'<div class="detailSection corporationName"><span>Florida Limited Liability Company</span>'
            f'<span>{name}</span></div>'
            f'<div class="detailSection filingInformation"><label>Document Number</label><span>L1900{len(name)}</span>'
            f'<label>Status</label><span>ACTIVE</span></div>'
            f'<div class="detailSection"><span>Registered Agent Name &amp; Address</span><span>{agent}</span>'
            f'<span><div>9 AGENT WAY<br/>TALLAHASSEE, FL</div></span></div>'
            f'<div class="detailSection"><span>Authorized Person(s) Detail</span><span>Name &amp; Address</span>'
            f'{people}</div>')


class Registry:
    """A fake search.sunbiz.org: {entity name: [(officer, title), ...]}."""

    def __init__(self, entities, neighbours=(), down=False):
        self.entities, self.neighbours, self.down, self.calls = entities, list(neighbours), down, 0

    def __call__(self, url):
        self.calls += 1
        if self.down:
            return None
        if "SearchResults" in url:
            import urllib.parse
            q = urllib.parse.unquote(url.split("searchTerm=")[1])
            self.last_query = sunbiz.norm_entity(q)
            names = [n for n in self.entities if sunbiz.norm_entity(n) == sunbiz.norm_entity(q)]
            return search_page(*(self.neighbours + names))
        # A detail link's index points into the search page that produced it:
        # neighbours first, then the matching entity.
        i = int(url.split("x=")[1].split("&")[0])
        page = self.neighbours + [n for n in self.entities
                                  if sunbiz.norm_entity(n) == self.last_query]
        name = page[i] if i < len(page) else "?"
        officers = self.entities.get(name, [("SOMEONE ELSE, NOT RELEVANT", "MGR")])
        return detail_page(name, officers)


# ── looking one entity up ───────────────────────────────────────────────────

def test_an_exact_match_is_resolved_with_its_people():
    reg = Registry({"BRICKELL 27 LLC": [("GARCIA, MARIA", "MGR")]})
    rec = sunbiz.lookup("Brickell 27, L.L.C.", reg)
    assert rec["found"] is True
    assert rec["officers"] == [{"name": "GARCIA, MARIA", "title": "MGR"}]
    assert rec["agent"] == "REGISTERED AGENTS INC"


def test_an_alphabetical_neighbour_is_never_taken_for_the_owner():
    """Sunbiz pads results with neighbours; BRICKELL 270 LLC is a different company."""
    reg = Registry({}, neighbours=["BRICKELL 270 LLC", "BRICKELL 27TH STREET LLC"])
    assert sunbiz.lookup("BRICKELL 27 LLC", reg) == {"found": False}


def test_an_unreachable_registry_is_none_not_not_found():
    assert sunbiz.lookup("BRICKELL 27 LLC", Registry({}, down=True)) is None


# ── resolving a building's owners ───────────────────────────────────────────

def test_resolve_caches_entities_skips_people_and_never_caches_a_blip():
    con = sqlite3.connect(":memory:")
    reg = Registry({"ALPHA ONE LLC": [("DOE, JANE", "MGR")]})
    out = sunbiz.resolve(con, ["ALPHA ONE LLC", "BETA TWO LLC", "SMITH JOHN"], reg)
    assert out == {"looked_up": 2, "found": 1, "unreachable": 0, "remaining": 0}
    calls = reg.calls
    sunbiz.resolve(con, ["ALPHA ONE LLC", "BETA TWO LLC"], reg)
    assert reg.calls == calls, "cached names are not looked up again"

    con2 = sqlite3.connect(":memory:")
    out = sunbiz.resolve(con2, ["GAMMA LLC"], Registry({}, down=True))
    assert out["unreachable"] == 1 and out["looked_up"] == 0
    assert sunbiz.pending(con2, ["GAMMA LLC"]) == 1, "a network failure must stay pending"


def test_resolve_works_in_bounded_batches():
    con = sqlite3.connect(":memory:")
    names = [f"OWNER {i} LLC" for i in range(5)]
    out = sunbiz.resolve(con, names, Registry({}), batch=2)
    assert out["looked_up"] == 2 and out["remaining"] == 3


# ── the clustering rule ─────────────────────────────────────────────────────

def units(*owners):
    return [{"owner_norm": o, "owner_addr_norm": f"ADDR {i}", "is_entity": int(e)}
            for i, (o, e) in enumerate(owners)]


def test_two_llcs_with_the_same_manager_are_one_buyer():
    res = cluster(units(("ALPHA ONE LLC", 1), ("BRAVO SEVEN LLC", 1), ("CHARLIE LLC", 1)),
                  {"ALPHA ONE LLC": [{"name": "DOE, JANE", "title": "MGR"}],
                   "BRAVO SEVEN LLC": [{"name": "Doe, Jane", "title": "AMBR"}],
                   "CHARLIE LLC": [{"name": "ROE, RICHARD", "title": "MGR"}]})
    top = res["groups"][0]
    assert set(top["members"]) == {"ALPHA ONE LLC", "BRAVO SEVEN LLC"}
    assert top["evidence"][0]["rule"] == "sunbiz_officer"
    assert top["evidence"][0]["evidence"] == "DOE, JANE"


def test_a_company_as_officer_never_links():
    res = cluster(units(("ALPHA ONE LLC", 1), ("BRAVO SEVEN LLC", 1)),
                  {"ALPHA ONE LLC": [{"name": "CORPORATE MANAGERS INC", "title": "MGR"}],
                   "BRAVO SEVEN LLC": [{"name": "CORPORATE MANAGERS INC", "title": "MGR"}]})
    assert res["beneficial_owners"] == 2


def test_a_person_on_every_owner_is_a_nominee_not_a_buyer():
    n = sunbiz.MAX_OWNERS_PER_OFFICER + 1
    owners = [(f"OWNER {i} LLC", 1) for i in range(n)]
    offs = {o: [{"name": "NOMINEE, NICK", "title": "MGR"}] for o, _ in owners}
    assert cluster(units(*owners), offs)["beneficial_owners"] == n


def test_individual_owners_are_never_linked_through_officers():
    res = cluster(units(("SMITH JOHN", 0), ("SMITH JANE", 0)),
                  {"SMITH JOHN": [{"name": "SMITH, JOHN", "title": "P"}],
                   "SMITH JANE": [{"name": "SMITH, JOHN", "title": "P"}]})
    assert res["beneficial_owners"] == 2


# ── end to end through the roll ─────────────────────────────────────────────

def test_top_beneficial_uses_the_cache_and_reports_what_is_pending():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE nal_condo_unit (group_key TEXT, owner_name TEXT, owner_norm TEXT, "
                "owner_addr_norm TEXT, is_entity INTEGER)")
    rows = [("G", "Alpha One, LLC", "ALPHA ONE LLC", "A1", 1)] * 3
    rows += [("G", "Bravo Seven LLC", "BRAVO SEVEN LLC", "B7", 1)] * 3
    rows += [("G", "Delta Nine LLC", "DELTA NINE LLC", "D9", 1)]
    rows += [("G", f"PERSON {i}", f"PERSON {i}", f"P{i}", 0) for i in range(5)]
    con.executemany("INSERT INTO nal_condo_unit VALUES (?,?,?,?,?)", rows)

    before = top_beneficial(con, "G")
    assert before["sunbiz"] == {"resolved": 0, "pending": 3}
    assert before["groups"][0]["units"] == 3

    reg = Registry({"ALPHA ONE LLC": [("DOE, JANE", "MGR")],
                    "BRAVO SEVEN LLC": [("DOE, JANE", "MGR")]})
    sunbiz.resolve(con, [r[1] for r in rows], reg)
    after = top_beneficial(con, "G")
    assert after["sunbiz"] == {"resolved": 2, "pending": 0}
    assert after["groups"][0]["units"] == 6, "the two LLCs are one buyer with 6 of 12 units"
    assert after["groups"][0]["pct"] == pytest.approx(50.0)


# ── the route ───────────────────────────────────────────────────────────────

def test_the_lookup_route_resolves_a_building_and_reports_an_outage(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.prospect.routes as routes
    from backend.app import app
    dbfile = tmp_path / "p.db"
    con = sqlite3.connect(dbfile)
    con.execute("CREATE TABLE target (group_key TEXT)")
    con.execute("INSERT INTO target VALUES ('G')")
    con.execute("CREATE TABLE nal_condo_unit (group_key TEXT, owner_name TEXT)")
    con.executemany("INSERT INTO nal_condo_unit VALUES ('G', ?)",
                    [("ALPHA ONE LLC",), ("BRAVO SEVEN LLC",), ("SMITH JOHN",)])
    con.commit()
    con.close()

    def db():
        c = sqlite3.connect(dbfile)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(routes, "db", db)
    client = TestClient(app)

    monkeypatch.setattr(sunbiz, "_default_fetch", Registry({"ALPHA ONE LLC": [("DOE, JANE", "MGR")]}))
    monkeypatch.setattr(sunbiz.resolve, "__defaults__",
                        (sunbiz._default_fetch, sunbiz.DEFAULT_BATCH, sunbiz.MAX_AGE_DAYS))
    r = client.post("/api/target/G/beneficial/sunbiz")
    assert r.status_code == 200 and r.json()["looked_up"] == 2

    down = Registry({}, down=True)
    monkeypatch.setattr(sunbiz.resolve, "__defaults__", (down, sunbiz.DEFAULT_BATCH, sunbiz.MAX_AGE_DAYS))
    con = sqlite3.connect(dbfile)
    con.execute("DELETE FROM sunbiz_entity")
    con.commit()
    con.close()
    r = client.post("/api/target/G/beneficial/sunbiz")
    assert r.status_code == 502 and "Sunbiz" in r.json()["detail"]
