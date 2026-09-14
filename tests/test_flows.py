"""Migration flows.

The column names in `migration_flow` are a trap: `dest_fips` is the county the
row is ABOUT and `origin_fips` is the counterparty, so on an outbound row
`origin_fips` holds the DESTINATION. Reading them literally produces numbers that
look plausible and are wrong, which is what most of these tests are about.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.prospect.flows import metro, national  # noqa: E402

DDL = """
CREATE TABLE county (fips TEXT PRIMARY KEY, name TEXT, state TEXT, state_fips TEXT,
  cbsa TEXT, cbsa_name TEXT, cbsa_type TEXT);
CREATE TABLE market (cbsa TEXT PRIMARY KEY, name TEXT, population INTEGER);
CREATE TABLE migration_flow (dest_fips TEXT, origin_fips TEXT, year INTEGER,
  direction TEXT, returns INTEGER, exemptions INTEGER, agi REAL,
  PRIMARY KEY (dest_fips, origin_fips, year, direction));
"""

# Two metros and a county outside any metro.
COUNTIES = [("12086", "A1", "MIA"), ("12011", "A2", "MIA"),
            ("48201", "B1", "HOU"), ("99001", "C1", None)]
MARKETS = [("MIA", "Miami", 6_100_000), ("HOU", "Houston", 7_100_000)]


def db(flows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    for fips, name, cbsa in COUNTIES:
        con.execute("INSERT INTO county (fips, name, cbsa) VALUES (?,?,?)", (fips, name, cbsa))
    for cbsa, name, pop in MARKETS:
        con.execute("INSERT INTO market VALUES (?,?,?)", (cbsa, name, pop))
    for f in flows:
        con.execute("INSERT INTO migration_flow (dest_fips, origin_fips, year, "
                    "direction, returns, agi) VALUES (?,?,?,?,?,?)", f)
    con.commit()
    return con


def flow(subject, counterparty, direction, returns, agi_thousands):
    """agi is in $000s at source, as IRS publishes it."""
    return (subject, counterparty, 2022, direction, returns, agi_thousands)


# ── the column-name trap ───────────────────────────────────────────────────

def test_an_outbound_row_is_attributed_to_the_county_it_is_about():
    """On an 'out' row, origin_fips is the DESTINATION. Attributing the row to it
    would credit Houston with Miami's departures and count arrivals twice."""
    con = db([flow("12086", "48201", "out", 500, 40_000)])
    nat = national(con)
    by = {r["cbsa"]: r for r in nat["by_households"]}
    assert by["MIA"]["out_returns"] == 500
    assert "HOU" not in by, "an outbound row must not create a flow for the counterparty"


def test_inbound_and_outbound_roll_up_to_the_same_subject():
    con = db([flow("12086", "48201", "in", 900, 90_000),
              flow("12086", "48201", "out", 400, 60_000)])
    r = {x["cbsa"]: x for x in national(con)["by_households"]}["MIA"]
    assert r["in_returns"] == 900 and r["out_returns"] == 400
    assert r["net_returns"] == 500


# ── what the flows mean ────────────────────────────────────────────────────

def test_intra_metro_moves_are_not_migration():
    """A move between two counties of the same metro is not in- or out-migration
    and would otherwise inflate both sides of the same market."""
    con = db([flow("12086", "12011", "in", 5_000, 500_000)])
    assert national(con)["by_households"] == []


def test_agi_is_converted_from_thousands_to_dollars():
    con = db([flow("12086", "48201", "in", 100, 8_000)])   # $8.0M over 100 households
    r = {x["cbsa"]: x for x in national(con)["by_households"]}["MIA"]
    assert r["in_agi_per_return"] == 80_000


def test_gaining_households_while_losing_income_is_flagged():
    """The finding no single ranking shows: more households, less money."""
    con = db([flow("12086", "48201", "in", 1_000, 40_000),    # $40k each
              flow("12086", "48201", "out", 600, 120_000)])   # $200k each
    r = {x["cbsa"]: x for x in national(con)["by_households"]}["MIA"]
    assert r["net_returns"] == 400
    assert r["net_agi"] < 0
    assert r["gaining_households_losing_income"] is True
    assert national(con)["divergent"][0]["cbsa"] == "MIA"


def test_the_two_rankings_are_reported_separately():
    con = db([flow("12086", "48201", "in", 1_000, 20_000),
              flow("48201", "12086", "in", 200, 200_000)])
    nat = national(con)
    assert nat["by_households"][0]["cbsa"] == "MIA"
    assert nat["by_income"][0]["cbsa"] == "HOU"


def test_small_metros_can_be_filtered_out():
    con = db([flow("12086", "48201", "in", 10, 1_000)])
    assert national(con, min_pop=7_000_000)["by_households"] == []


# ── one metro's corridors ──────────────────────────────────────────────────

def test_corridors_are_netted_and_split_by_direction():
    con = db([flow("12086", "48201", "in", 900, 90_000),
              flow("12086", "48201", "out", 400, 60_000),
              flow("12011", "48201", "out", 700, 70_000)])
    m = metro(con, "MIA")
    assert m["totals"]["in_returns"] == 900
    assert m["totals"]["out_returns"] == 1_100
    assert m["totals"]["net_returns"] == -200
    assert [c["cbsa"] for c in m["losing_to"]] == ["HOU"]
    assert m["losing_to"][0]["net_returns"] == -200
    assert m["gaining_from"] == []


def test_counterparties_outside_any_metro_are_pooled_not_dropped():
    """They are real moves. Dropping them would make the corridors fail to sum to
    the totals printed above them."""
    con = db([flow("12086", "99001", "in", 300, 15_000)])
    m = metro(con, "MIA")
    assert m["totals"]["in_returns"] == 300
    assert m["gaining_from"][0]["name"] == "Outside any metro"
    assert m["gaining_from"][0]["cbsa"] is None


def test_every_county_of_a_metro_contributes():
    con = db([flow("12086", "48201", "in", 100, 10_000),
              flow("12011", "48201", "in", 250, 25_000)])
    assert metro(con, "MIA")["totals"]["in_returns"] == 350


def test_an_unknown_metro_raises():
    with pytest.raises(LookupError):
        metro(db([]), "NOPE")


# ── the limits travel with the data ────────────────────────────────────────

@pytest.mark.parametrize("call", [
    lambda con: national(con),
    lambda con: metro(con, "MIA"),
])
def test_the_soi_limits_are_in_the_payload_not_a_footnote(call):
    out = call(db([flow("12086", "48201", "in", 100, 10_000)]))
    joined = " ".join(out["limits"])
    assert "lags" in joined
    assert "ten returns" in joined
    assert "filers" in joined


def test_a_corridor_with_no_income_does_not_divide_by_zero():
    con = db([flow("12086", "48201", "in", 0, 0)])
    m = metro(con, "MIA")
    assert m["totals"]["in_agi_per_return"] is None
