"""Buyout estimate, against a fixture that reproduces the trap in the README.

One real building in this data shows 148 folios carrying prices up to $4.9M each
which are really a handful of bulk deeds. An estimator that prices a buyout off
those numbers is off by an order of magnitude, so that case is the first test.
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.prospect.economics import basis_summary, estimate  # noqa: E402

YEAR = date.today().year

DDL = """
CREATE TABLE target (group_key TEXT PRIMARY KEY, condo_name TEXT, addr_primary TEXT,
  units_nal INTEGER, top_owner TEXT, top_owner_pct REAL, top_mail_pct REAL);
CREATE TABLE nal_condo_unit (folio TEXT PRIMARY KEY, group_key TEXT, owner_name TEXT,
  owner_norm TEXT, owner_addr_norm TEXT, tot_lvg_area REAL, jv REAL,
  sale_prc1 REAL, sale_yr1 INTEGER, or_book1 TEXT, or_page1 TEXT, is_entity INTEGER);
"""


def build(units, top_owner=None, top_owner_pct=0, top_mail_pct=0, key="0101010"):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    con.execute("INSERT INTO target VALUES (?,?,?,?,?,?,?)",
                (key, "Test Towers", "1 Test Ave", len(units), top_owner,
                 top_owner_pct, top_mail_pct))
    for i, u in enumerate(units):
        con.execute(
            "INSERT INTO nal_condo_unit VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{key}{i:06d}", key, u.get("owner", "SMITH JOHN"),
             u.get("owner_norm", u.get("owner", "SMITH JOHN")),
             u.get("mail", f"MAIL {i}"), u.get("sf"), u.get("jv"),
             u.get("price"), u.get("year"),
             u.get("book"), u.get("page"), u.get("entity", 0)))
    con.commit()
    return con


def unit(sf=1000, jv=300_000, price=None, year=None, **kw):
    return {"sf": sf, "jv": jv, "price": price, "year": year, **kw}


# ── the bulk-deed trap ─────────────────────────────────────────────────────

def test_bulk_deeds_do_not_set_the_price_per_square_foot():
    """Ten folios conveyed on one $9.6M instrument stamp $960k on every folio.
    Taken as ten comps that is a $960/SF building. It is not."""
    units = [unit(sf=1000, price=9_600_000, year=YEAR - 1) for _ in range(10)]
    # ...against three genuine single-unit sales around $440/SF. Distinct prices,
    # because the comp engine keys instruments on (year, price) -- see below.
    units += [unit(sf=1000, price=p, year=YEAR - 1) for p in (435_000, 440_000, 445_000)]
    units += [unit(sf=1000) for _ in range(7)]
    b = estimate("0101010", build(units))

    assert b.psf == pytest.approx(440.0)
    assert b.psf_source == "building_single_unit_sales"
    # 20 units x 1000sf x $440 -- not the $960/SF the naive read would produce.
    assert b.cost_at_fmv == pytest.approx(20 * 1000 * 440.0)
    assert any("bulk deed" in c for c in b.caveats)


def test_the_recorded_instrument_separates_same_price_sales():
    """OR book/page IS the instrument, so three units that each sold for $440,000
    in the same year are three sales, not one $440,000 deed for three units."""
    units = [unit(sf=1000, price=440_000, year=YEAR - 1, book="100", page=str(pg))
             for pg in (11, 22, 33)]
    units += [unit(sf=1000) for _ in range(3)]
    b = estimate("0101010", build(units))

    assert b.psf == pytest.approx(440.0)
    assert b.basis_mix.get("comp_psf") == 6
    assert b.cost_at_fmv == pytest.approx(6 * 1000 * 440.0)


def test_without_a_recorded_instrument_it_falls_back_and_says_so():
    """Rolls that carry no OR book/page keep the old (year, price) key, which
    collapses identical-price sales into a phantom bulk deed and prices them at a
    third of what they cost. Pinned so the fallback's cost stays visible."""
    units = [unit(sf=1000, price=440_000, year=YEAR - 1) for _ in range(3)]
    units += [unit(sf=1000) for _ in range(3)]
    b = estimate("0101010", build(units))

    assert b.psf is None
    assert b.median_unit_value == pytest.approx(440_000 / 3)


def test_price_per_square_foot_uses_each_units_own_area():
    """A $/SF against the unit's own living area is the point -- a building-wide
    average would price a 500sf studio and a 2,000sf penthouse identically."""
    units = [unit(sf=500, price=220_000, year=YEAR - 1),   # $440/SF
             unit(sf=2000), unit(sf=1000)]
    b = estimate("0101010", build(units))
    assert b.psf == pytest.approx(440.0)
    assert b.cost_at_fmv == pytest.approx((500 + 2000 + 1000) * 440.0)


# ── control ────────────────────────────────────────────────────────────────

def test_units_already_controlled_are_not_bought_twice():
    units = [unit(owner="BULK LLC", owner_norm="BULK LLC", sf=1000) for _ in range(6)]
    units += [unit(sf=1000, price=400_000, year=YEAR - 1)]
    units += [unit(sf=1000) for _ in range(3)]
    b = estimate("0101010", build(units, top_owner="BULK LLC", top_owner_pct=60))

    assert b.units == 10
    assert b.controlled_units == 6
    assert b.units_to_acquire == 4
    assert b.cost_at_fmv == pytest.approx(4 * 1000 * 400.0)


def test_shared_mailing_counts_as_control_when_it_is_the_stronger_signal():
    """One buyer behind several LLCs shows up in the mailing address before it
    shows up in any single owner name."""
    units = [unit(owner=f"LLC {i}", owner_norm=f"LLC {i}", mail="PO BOX 1", sf=1000)
             for i in range(5)]
    units += [unit(sf=1000, price=400_000, year=YEAR - 1, mail="HOME")]
    units += [unit(sf=1000, mail=f"OTHER {i}") for i in range(4)]
    b = estimate("0101010", build(units, top_owner="LLC 0",
                                 top_owner_pct=10, top_mail_pct=50))
    assert b.controlled_units == 5
    assert b.units_to_acquire == 5


# ── the valuation ladder ───────────────────────────────────────────────────

def test_units_without_living_area_fall_back_to_the_building_median():
    units = [unit(sf=1000, price=400_000, year=YEAR - 1),
             unit(sf=None), unit(sf=None)]
    b = estimate("0101010", build(units))
    assert b.basis_mix["comp_psf"] == 1
    assert b.basis_mix["building_median"] == 2
    assert b.cost_at_fmv == pytest.approx(400_000 * 3)


def test_a_building_with_no_sales_uses_nearby_comps_when_offered():
    units = [unit(sf=1000, price=None) for _ in range(4)]
    b = estimate("0101010", build(units), nearby_psf=350.0)
    assert b.psf_source == "nearby_buildings"
    assert set(b.basis_mix) == {"nearby_psf"}
    assert b.cost_at_fmv == pytest.approx(4 * 1000 * 350.0)
    assert any("comparable buildings nearby" in c for c in b.caveats)


def test_assessed_ratio_is_measured_from_the_building_not_assumed():
    """A statewide price-to-assessment constant would be wrong per building and
    unfalsifiable. This one is checkable against the rows it came from."""
    units = [unit(sf=None, jv=200_000, price=300_000, year=YEAR - 1)]  # ratio 1.5
    units += [unit(sf=None, jv=100_000)]
    b = estimate("0101010", build(units))
    assert b.assessed_ratio == pytest.approx(1.5)


def test_it_refuses_to_price_what_it_cannot_source():
    units = [unit(sf=None, jv=None) for _ in range(3)]
    b = estimate("0101010", build(units))
    assert b.cost_at_fmv is None
    assert b.basis_mix.get("none") == 3
    assert any("could be priced" in c for c in b.caveats)


# ── holdouts ───────────────────────────────────────────────────────────────

def test_the_holdout_premium_lands_on_the_priciest_tail():
    units = [unit(sf=1000, price=400_000, year=YEAR - 1)]
    units += [unit(sf=sf) for sf in (500, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 3000)]
    b = estimate("0101010", build(units), holdout_share=0.10, holdout_premium=0.25)
    # 10 units -> one holdout, and it is the 3,000sf unit at $1.2M, not a studio.
    assert b.cost_with_holdout == pytest.approx(b.cost_at_fmv + 3000 * 400.0 * 0.25)


def test_holdout_assumptions_are_reported_back():
    b = estimate("0101010", build([unit(sf=1000, price=400_000, year=YEAR - 1)]),
                 holdout_share=0.2, holdout_premium=0.5)
    d = b.as_dict()
    assert d["holdout_share"] == 0.2 and d["holdout_premium"] == 0.5


def test_zero_holdout_share_is_honoured():
    units = [unit(sf=1000, price=400_000, year=YEAR - 1)] + [unit(sf=1000)] * 9
    b = estimate("0101010", build(units), holdout_share=0)
    assert b.cost_with_holdout == pytest.approx(b.cost_at_fmv)


# ── reporting its own quality ──────────────────────────────────────────────

def test_the_estimate_says_how_it_was_built():
    assert basis_summary({"comp_psf": 68, "nearby_psf": 22, "assessed_ratio": 10}, 100) \
        == "68% in-building comps, 22% nearby comps, 10% assessed value"
    assert basis_summary({}, 0) is None


def test_every_estimate_carries_the_limits_it_cannot_see():
    b = estimate("0101010", build([unit(sf=1000, price=400_000, year=YEAR - 1)]))
    joined = " ".join(b.caveats)
    assert "not an appraisal" in joined
    assert "mortgages and liens" in joined
    assert "718.117" in joined


def test_unknown_building_raises():
    with pytest.raises(LookupError):
        estimate("nope", build([unit()]))
