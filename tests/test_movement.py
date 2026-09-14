"""Assembly velocity, and one buyer behind several LLCs.

The screen ranks concentration as a LEVEL. A building at 45% single-owner has
probably already been found by somebody who knows what they are doing; one that
went 6% to 19% since the last roll is the one to be early on -- and the flat
screen ranks the first one higher.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.prospect.beneficial import cluster, stem  # noqa: E402

spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/prospect/build_targets.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


def snap(**kw):
    base = {"roll_year": 2025, "top_owner": "A LLC", "top_owner_pct": 10.0,
            "top_mail_pct": 0.0, "distinct_owners": 50, "corporate_pct": 20.0}
    base.update(kw)
    return base


def group(**kw):
    base = {"top_owner": "A LLC", "top_owner_pct": 10.0, "top_mail_pct": 0.0,
            "distinct_owners": 50, "corporate_pct": 20.0}
    base.update(kw)
    return base


# ── movement ───────────────────────────────────────────────────────────────

def test_no_prior_vintage_reports_unknown_not_zero():
    """The first build, and any building absent from the last roll. Rendering
    that as 0.0 would say "nothing is happening here", which is a claim the data
    does not support."""
    assert bt.movement(group(), None) == (None, None, None, None, None, None)


def test_rising_concentration_against_a_falling_owner_count_is_assembly():
    prior = snap(top_owner_pct=6.0, distinct_owners=58)
    now = group(top_owner_pct=19.0, distinct_owners=47)
    year, conc, owners, corp, changed, flag = bt.movement(now, prior)
    assert year == 2025
    assert conc == pytest.approx(13.0)
    assert owners == -11
    assert flag == 1


def test_concentration_alone_is_not_assembly():
    """One sale moves concentration. Without consolidation it is noise, and a
    flag that fires on noise is a flag nobody reads."""
    prior = snap(top_owner_pct=6.0, distinct_owners=50)
    now = group(top_owner_pct=19.0, distinct_owners=50)
    assert bt.movement(now, prior)[5] == 0


def test_a_falling_owner_count_alone_is_not_assembly():
    """Owner counts drift with data cleanup and estate transfers."""
    prior = snap(top_owner_pct=10.0, distinct_owners=58)
    now = group(top_owner_pct=10.0, distinct_owners=47)
    assert bt.movement(now, prior)[5] == 0


def test_concentration_uses_whichever_signal_is_stronger():
    """A buyer holding units under several names shows in shared mailing before
    any single owner name, so movement must watch both."""
    prior = snap(top_owner_pct=5.0, top_mail_pct=5.0, distinct_owners=60)
    now = group(top_owner_pct=5.0, top_mail_pct=28.0, distinct_owners=51)
    _, conc, _, _, _, flag = bt.movement(now, prior)
    assert conc == pytest.approx(23.0)
    assert flag == 1


def test_a_change_of_hands_is_recorded_separately_from_a_change_of_share():
    prior = snap(top_owner="OLD LLC")
    assert bt.movement(group(top_owner="NEW LLC"), prior)[4] == 1
    assert bt.movement(group(top_owner="OLD LLC"), prior)[4] == 0


def test_a_sale_away_shows_as_negative_movement():
    prior = snap(top_owner_pct=30.0, distinct_owners=40)
    _, conc, owners, _, _, flag = bt.movement(group(top_owner_pct=12.0, distinct_owners=55), prior)
    assert conc == pytest.approx(-18.0)
    assert owners == 15 and flag == 0


# ── the prior vintage is chosen strictly before this roll ──────────────────

SNAP_DDL = """
CREATE TABLE target_snapshot (group_key TEXT, roll_year INTEGER, roll_type TEXT,
  captured TEXT, unit_folios INTEGER, top_owner TEXT, top_owner_units INTEGER,
  top_owner_pct REAL, top_mail_addr TEXT, top_mail_pct REAL, distinct_owners INTEGER,
  corporate_pct REAL, absentee_pct REAL, entity_sales_last_3yr INTEGER,
  homestead_pct REAL, PRIMARY KEY (group_key, roll_year));
"""


def snap_db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SNAP_DDL)
    for key, year, pct in rows:
        con.execute("INSERT INTO target_snapshot (group_key, roll_year, top_owner_pct, "
                    "distinct_owners) VALUES (?,?,?,50)", (key, year, pct))
    con.commit()
    return con


def test_rerunning_the_same_roll_compares_against_the_previous_one():
    """Not against itself, which would report no movement on every rebuild."""
    con = snap_db([("k", 2024, 6.0), ("k", 2026, 19.0)])
    prior = bt.prior_snapshot(con, 2026)
    assert prior["k"]["roll_year"] == 2024
    assert prior["k"]["top_owner_pct"] == 6.0


def test_the_most_recent_earlier_vintage_wins():
    con = snap_db([("k", 2020, 1.0), ("k", 2023, 7.0), ("k", 2025, 11.0)])
    assert bt.prior_snapshot(con, 2026)["k"]["roll_year"] == 2025


def test_a_building_new_to_the_roll_has_no_prior():
    con = snap_db([("other", 2025, 5.0)])
    assert "k" not in bt.prior_snapshot(con, 2026)


# ── beneficial ownership ───────────────────────────────────────────────────

def unit(owner, addr="X", entity=1, n=1):
    return [{"owner_norm": owner, "owner_addr_norm": addr, "is_entity": entity}] * n


def test_transitive_clustering_finds_what_neither_rule_finds_alone():
    """Name series links the Flaglers; a shared address links one Flagler to
    Brickell. Only the transitive closure puts all three together."""
    units = (unit("FLAGLER HOLDINGS I LLC", "PO BOX 1", n=3)
             + unit("FLAGLER HOLDINGS II LLC", "PO BOX 2", n=2)
             + unit("BRICKELL 27 LLC", "PO BOX 2", n=4)
             + unit("SMITH JOHN", "HOME", entity=0, n=1))
    r = cluster(units)
    top = r["groups"][0]
    assert top["units"] == 9 and top["member_count"] == 3
    assert top["pct"] == pytest.approx(90.0)
    # The single-name figure the screen scores on would be 4 of 10.
    assert {e["rule"] for e in top["evidence"]} == {"shared_mailing", "name_series"}


def test_every_cluster_carries_the_evidence_for_each_link():
    """A cluster must be disbelievable on its specifics, not on principle."""
    units = unit("A HOLDINGS LLC", "PO BOX 9", n=2) + unit("B VENTURES LLC", "PO BOX 9", n=2)
    ev = cluster(units)["groups"][0]["evidence"]
    assert len(ev) == 1
    assert ev[0]["rule"] == "shared_mailing" and ev[0]["evidence"] == "PO BOX 9"


def test_individuals_sharing_a_surname_are_not_merged():
    """Two people with the same last name are a family, not a buyer -- exactly
    the false positive normalize_owner refuses to make."""
    units = unit("SMITH JOHN", "HOME A", entity=0, n=3) + unit("SMITH MARY", "HOME B", entity=0, n=3)
    assert cluster(units)["beneficial_owners"] == 2


def test_a_mail_drop_is_not_a_buyer():
    """An address shared by dozens of distinct owners is a management company or
    a mailbox service. Clustering on it would merge a whole building."""
    units = []
    for i in range(20):
        units += unit(f"OWNER {i} LLC", "MGMT CO 100 MAIN", n=1)
    assert cluster(units)["beneficial_owners"] == 20


def test_a_stem_too_thin_to_trust_is_not_used():
    assert stem("SOBE LLC") == ""
    assert stem("BRICKELL 27 LLC") == ""
    assert stem("FLAGLER HOLDINGS II LLC") == "FLAGLER HOLDINGS"
    # Descriptive words stay: stripping them would match any owner with FLAGLER
    # in the name -- a street, a neighbourhood, and a dozen unrelated firms.
    assert "HOLDINGS" in stem("FLAGLER HOLDINGS II LLC")


def test_a_series_marker_is_removed_but_the_name_is_not():
    for name in ("OCEAN VIEW PROPERTIES 3 INC", "OCEAN VIEW PROPERTIES II LLC",
                 "OCEAN VIEW PROPERTIES INC"):
        assert stem(name) == "OCEAN VIEW PROPERTIES", name


def test_a_building_of_unrelated_owners_clusters_to_itself():
    names = ["CORAL GABLES EQUITY LLC", "NORTH BAY TRUST HOLDINGS LLC",
             "PALMETTO RIDGE VENTURES LLC", "SUNNY ISLES CAPITAL LLC",
             "DOWNTOWN ARTS PARTNERS LLC", "KENDALL LAKES REALTY LLC"]
    units = []
    for i, n in enumerate(names):
        units += unit(n, f"ADDR {i}", n=1)
    r = cluster(units)
    assert r["beneficial_owners"] == 6
    assert all(g["member_count"] == 1 for g in r["groups"])


def test_a_numbered_series_of_the_same_stem_is_one_buyer():
    """The case the fixture above accidentally produced, kept deliberately:
    OCEAN VIEW PROPERTIES 1/2/3 differ only by a series marker, which is how a
    single sponsor holds units one entity at a time."""
    units = (unit("OCEAN VIEW PROPERTIES 1 LLC", "A", n=2)
             + unit("OCEAN VIEW PROPERTIES 2 LLC", "B", n=2)
             + unit("OCEAN VIEW PROPERTIES 3 LLC", "C", n=2))
    r = cluster(units)
    assert r["beneficial_owners"] == 1
    assert r["groups"][0]["units"] == 6


def test_a_bare_place_name_plus_a_number_is_not_a_series():
    """BRICKELL 900 and BRICKELL 1200 are two addresses, not one sponsor. The
    stem is a single token, which is below the floor, so no edge is drawn."""
    units = unit("BRICKELL 900 LLC", "A", n=3) + unit("BRICKELL 1200 LLC", "B", n=3)
    assert cluster(units)["beneficial_owners"] == 2


def test_an_empty_building_does_not_divide_by_zero():
    r = cluster([])
    assert r["total_units"] == 0 and r["groups"] == []
