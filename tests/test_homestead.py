"""Homestead ingest and the resistance term.

FS 718.117 needs 80% approval AND no more than 5% objecting, and the screen had
nothing on the objection side -- `absentee_pct` was standing in, but absentee is
a mailing-address comparison that puts a Brickell landlord and a retiree in Ohio
in the same bucket. Homestead is the legal fact.

The real roll cannot be downloaded here, so these run against a synthetic NAL zip
with the same shape. That covers the wiring; the column NAME still has to be
confirmed against a real roll with `ingest_nal.py --show-header`.
"""
import csv
import importlib.util
import io
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NAL_COLUMNS = ["PARCEL_ID", "DOR_UC", "OWN_NAME", "OWN_ADDR1", "OWN_CITY", "OWN_STATE",
               "OWN_ZIPCD", "PHY_ADDR1", "PHY_CITY", "PHY_ZIPCD", "ACT_YR_BLT",
               "EFF_YR_BLT", "JV", "LND_VAL", "TOT_LVG_AREA", "SALE_PRC1", "SALE_YR1",
               "OR_BOOK1", "OR_PAGE1", "S_LEGAL"]


def nal_columns():
    """Column order of nal_condo_unit, from the schema itself.

    The ingest yields positional tuples, so a test that hardcodes an index breaks
    the moment a column is appended -- which is exactly what happened when the
    qualification codes went in. Resolving by name tests the real contract.
    """
    import re

    from backend.prospect.db import SCHEMA
    m = re.search(r"CREATE TABLE IF NOT EXISTS nal_condo_unit\s*\((.*?)\n\);", SCHEMA, re.S)
    return [ln.strip().split()[0] for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith("--")]


def col(rows, name):
    """One column out of the ingest's positional tuples, by name."""
    i = nal_columns().index(name)
    return [r[i] for r in rows]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_roll(tmp_path, rows, extra_columns=()):
    """A zipped NAL CSV with the layout the ingest expects."""
    cols = NAL_COLUMNS + list(extra_columns)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    z = tmp_path / "roll.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("nal.csv", buf.getvalue())
    return z


def condo(folio, **kw):
    base = {"PARCEL_ID": folio, "DOR_UC": "004", "OWN_NAME": "SMITH JOHN",
            "OWN_ADDR1": "1 TEST AVE", "PHY_ADDR1": "1 TEST AVE",
            "ACT_YR_BLT": "1975", "JV": "300000", "S_LEGAL": "TEST CONDO"}
    base.update(kw)
    return base


# ── column resolution ──────────────────────────────────────────────────────

def test_homestead_resolves_from_the_header(tmp_path, capsys):
    nal = load("nal_a", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [
        condo("0101010000001", JV_HMSTD="50000"),
        condo("0101010000002", JV_HMSTD="0"),
        condo("0101010000003", JV_HMSTD=""),
    ], extra_columns=["JV_HMSTD"])

    rows = list(nal.rows_from_zip())
    assert "homestead resolved to column 'JV_HMSTD'" in capsys.readouterr().out
    assert col(rows, "homestead") == [1, 0, 0]
    assert col(rows, "homestead_val")[0] == 50000.0


def test_a_roll_without_the_column_reports_unknown_not_zero(tmp_path, capsys):
    """The failure that matters. A column name that silently stops matching would
    leave every unit reading "not homesteaded", and nothing downstream could tell
    that apart from a building where nobody is homesteaded."""
    nal = load("nal_b", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [condo("0101010000001")])

    rows = list(nal.rows_from_zip())
    out = capsys.readouterr().out
    assert "no homestead column found" in out
    assert "--show-header" in out
    assert col(rows, "homestead")[0] is None
    assert col(rows, "homestead_val")[0] is None


def test_candidates_are_tried_in_order(tmp_path, capsys):
    nal = load("nal_c", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [condo("0101010000001", AV_HMSTD="25000")],
                        extra_columns=["AV_HMSTD"])
    rows = list(nal.rows_from_zip())
    assert "'AV_HMSTD'" in capsys.readouterr().out
    assert col(rows, "homestead")[0] == 1


def test_non_condo_rows_are_still_skipped(tmp_path):
    nal = load("nal_d", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [condo("1", DOR_UC="001"), condo("2", DOR_UC="004")],
                        extra_columns=["JV_HMSTD"])
    assert [r[0] for r in nal.rows_from_zip()] == ["2"]


# ── aggregation ────────────────────────────────────────────────────────────

GROUP_DDL = """
CREATE TABLE nal_condo_unit (group_key TEXT, owner_name TEXT, owner_norm TEXT,
  owner_addr_norm TEXT, owner_state TEXT, phy_addr_norm TEXT, phy_city TEXT,
  phy_zip TEXT, act_yr_blt INTEGER, jv REAL, lnd_val REAL, tot_lvg_area REAL,
  sale_yr1 INTEGER, is_entity INTEGER, is_absentee INTEGER, homestead INTEGER);
"""


def group_pct(homesteads):
    """Run the aggregation the way build_targets does, on one building."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(GROUP_DDL)
    for h in homesteads:
        con.execute("INSERT INTO nal_condo_unit (group_key, homestead) VALUES ('k', ?)", (h,))
    us = [dict(r) for r in con.execute("SELECT * FROM nal_condo_unit")]
    known = [u for u in us if u["homestead"] is not None]
    units = sum(1 for u in known if u["homestead"])
    return (units if known else None,
            round(100.0 * units / len(known), 2) if known else None)


def test_homestead_share_is_over_units_the_roll_can_answer_for():
    assert group_pct([1, 1, 0, 0]) == (2, 50.0)
    assert group_pct([1, 1, 1, 1]) == (4, 100.0)


def test_unknown_units_are_excluded_from_the_denominator_not_counted_as_no():
    """Two homesteaded of two knowable is 100%, not 50% -- counting the unknown
    unit as 'not homesteaded' would understate resistance, in the direction that
    makes a building look easier to terminate than it is."""
    assert group_pct([1, 1, None, None]) == (2, 100.0)


def test_a_building_with_no_homestead_data_reports_none():
    assert group_pct([None, None, None]) == (None, None)


# ── the score is deliberately untouched ────────────────────────────────────

def test_resistance_is_stored_but_not_folded_into_the_score():
    """Adding a fifth term without re-deriving the weights would silently move
    every building in the app. The weights stay as they are until Phase 1.4."""
    import json
    cfg = json.loads((ROOT / "backend" / "prospect" / "config.json").read_text(encoding="utf-8"))
    assert set(cfg["score_weights"]) == {"age", "scale", "concentration", "absentee"}
    assert sum(cfg["score_weights"].values()) == pytest.approx(1.0)

    src = (ROOT / "scripts" / "prospect" / "build_targets.py").read_text(encoding="utf-8")
    total_line = next(l for l in src.splitlines() if l.strip().startswith("total = round("))
    assert "s_resist" not in total_line


# ── positional inserts must track the schema ───────────────────────────────

@pytest.mark.parametrize("table,script,const", [
    ("nal_condo_unit", "scripts/prospect/ingest_nal.py", "COLS"),
    ("condo_group", "scripts/prospect/build_targets.py", "GROUP_COLS"),
    ("target", "scripts/prospect/build_targets.py", "TARGET_COLS"),
])
def test_insert_arity_matches_the_schema(table, script, const):
    """These scripts insert positionally, so a column added to the schema and not
    to the row tuple is an opaque mid-load failure. Caught here instead."""
    import re
    from backend.prospect.db import SCHEMA
    m = re.search(r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\((.*?)\n\);", SCHEMA, re.S)
    cols = [l.strip().split()[0] for l in m.group(1).splitlines()
            if l.strip() and not l.strip().startswith("--")]
    assert len(cols) == getattr(load(script.replace("/", "_"), script), const)


# ── sale qualification and the second prior sale ───────────────────────────

def test_qualification_codes_are_captured_when_the_roll_carries_them(tmp_path, capsys):
    """The README says the roll has no qualification code; the published NAL
    layout documents QUAL_CD1/VI_CD1. Whichever is true for a given roll, the
    ingest reports it rather than assuming."""
    nal = load("nal_q", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [
        condo("0101010000001", QUAL_CD1="01", VI_CD1="I", SALE_MO1="6",
              SALE_PRC2="310000", SALE_YR2="2019"),
    ], extra_columns=["QUAL_CD1", "VI_CD1", "SALE_MO1", "SALE_PRC2", "SALE_YR2"])
    rows = list(nal.rows_from_zip())
    out = capsys.readouterr().out
    assert "QUAL_CD1" in out and "arm's-length filtering is available" in out
    assert col(rows, "qual_cd1")[0] == "01"
    assert col(rows, "sale_mo1")[0] == 6
    assert col(rows, "sale_prc2")[0] == 310000.0


def test_a_roll_without_them_stores_null_not_a_default(tmp_path, capsys):
    """NULL means the roll carried no such column, which is not the same as a
    sale being unqualified — and a filter must not treat it as one."""
    nal = load("nal_nq", "scripts/prospect/ingest_nal.py")
    nal.RAW = make_roll(tmp_path, [condo("0101010000001")])
    rows = list(nal.rows_from_zip())
    assert "optional columns absent" in capsys.readouterr().out
    for c in ("qual_cd1", "vi_cd1", "sale_mo1", "sale_prc2", "qual_cd2"):
        assert col(rows, c)[0] is None, c
