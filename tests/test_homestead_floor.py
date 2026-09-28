"""FS 718.117's floor for homesteaded owners in the buyout estimate.

In a termination with an 80% owner, a homesteaded owner who is current on
assessments gets at least their original purchase price, plus a 1% relocation
payment on what their unit is paid. The estimate's FMV total understated the
cost of exactly the owners most likely to object.
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.prospect.economics import RELOCATION_SHARE, estimate  # noqa: E402

YEAR = date.today().year
KEY = "0202020"
DDL = """
CREATE TABLE target (group_key TEXT PRIMARY KEY, condo_name TEXT, addr_primary TEXT,
  units_nal INTEGER, top_owner TEXT, top_owner_pct REAL, top_mail_pct REAL);
CREATE TABLE nal_condo_unit (folio TEXT PRIMARY KEY, group_key TEXT, owner_name TEXT,
  owner_norm TEXT, owner_addr_norm TEXT, tot_lvg_area REAL, jv REAL,
  sale_prc1 REAL, sale_yr1 INTEGER, or_book1 TEXT, or_page1 TEXT, is_entity INTEGER,
  homestead INTEGER);
"""


def build(units):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    con.execute("INSERT INTO target VALUES (?,?,?,?,?,?,?)",
                (KEY, "Floor Towers", "2 Test Ave", len(units), None, 0, 0))
    for i, u in enumerate(units):
        con.execute("INSERT INTO nal_condo_unit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{KEY}{i:06d}", KEY, f"OWNER {i}", f"OWNER {i}", f"MAIL {i}",
                     1000, 300_000, u.get("price"), u.get("year"),
                     u.get("book"), u.get("page"), 0, u.get("hs")))
    con.commit()
    return con


def fixture():
    return [
        # comps: recent single-unit sales at $400/sf -> every unit's FMV is $400k
        {"price": 400_000, "year": YEAR - 1, "book": "B1", "page": "1", "hs": 0},
        {"price": 400_000, "year": YEAR - 1, "book": "B1", "page": "2", "hs": 0},
        # homesteaded, bought for more than today's value -> floor lifts it to $600k
        {"price": 600_000, "year": YEAR - 15, "book": "B2", "page": "1", "hs": 1},
        # homesteaded, bought for less -> FMV stands; relocation still owed
        {"price": 250_000, "year": YEAR - 20, "book": "B2", "page": "2", "hs": 1},
        # homesteaded, but the "price" is a bulk deed's package total on 2 folios
        {"price": 2_000_000, "year": YEAR - 12, "book": "B3", "page": "9", "hs": 1},
        {"price": 2_000_000, "year": YEAR - 12, "book": "B3", "page": "9", "hs": 1},
        # not homesteaded, bought high -> no floor at all
        {"price": 900_000, "year": YEAR - 10, "book": "B4", "page": "1", "hs": 0},
    ]


def test_the_purchase_price_floor_and_relocation_are_added():
    b = estimate(KEY, build(fixture()))
    assert b.cost_at_fmv == pytest.approx(7 * 400_000)
    assert b.homestead_units == 4
    assert b.purchase_price_uplift == pytest.approx(200_000), "only the $600k owner is lifted"
    # 1% of what each homestead unit is paid: 600k + 400k + 400k + 400k
    assert b.relocation_payments == pytest.approx(RELOCATION_SHARE * 1_800_000)
    assert b.cost_statutory == pytest.approx(7 * 400_000 + 200_000 + 18_000)


def test_a_bulk_deed_is_not_read_as_one_owners_purchase_price():
    """$2M on two folios is what an assembler paid for both. Taking it as each
    homeowner's purchase price would add $3.2M of floor that does not exist."""
    b = estimate(KEY, build(fixture()))
    assert b.purchase_price_uplift < 1_000_000


def test_the_holdout_premium_lands_on_what_units_must_actually_be_paid():
    b = estimate(KEY, build(fixture()), holdout_share=0.1, holdout_premium=0.25)
    # one holdout unit (round(7 * 0.1) = 1): the priciest payout is the $600k floor
    assert b.cost_with_holdout == pytest.approx(b.cost_statutory + 0.25 * 600_000)


def test_unknown_homestead_is_reported_not_treated_as_no():
    units = fixture()
    for u in units[2:]:
        u["hs"] = None
    b = estimate(KEY, build(units))
    assert b.homestead_units == 0
    assert b.homestead_unknown == 5
    assert b.cost_statutory == b.cost_at_fmv
    assert any("unknown" in c for c in b.caveats)


def test_the_estimate_explains_the_floor_it_applied():
    b = estimate(KEY, build(fixture()))
    text = " ".join(b.caveats)
    assert "718.117" in text and "relocation" in text
    assert "current on assessments" in text, "the assumption it cannot check is stated"
    d = b.as_dict()
    for k in ("cost_statutory", "purchase_price_uplift", "relocation_payments",
              "homestead_units", "homestead_unknown"):
        assert k in d
