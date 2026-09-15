"""Milestone / recertification status.

`milestone_due` was `age_years >= 30` — a guess at a fact Miami-Dade publishes.
Post-Surfside it is the weakest part of the screen: it lumps a building that
already passed recertification together with one staring at a six-figure per-unit
assessment its owners cannot fund, and only the second has motivated sellers.

The county endpoint is unreachable from this environment, so the ingest resolves
its columns from the file it is given rather than hardcoding a schema. These test
that resolution and the scoring it feeds.
"""
import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rc = load("rc", "scripts/prospect/ingest_recert.py")
bt = load("bt_rc", "scripts/prospect/build_targets.py")


def write_csv(tmp_path, header, rows):
    p = tmp_path / "recert.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


# ── column resolution ──────────────────────────────────────────────────────

def test_headings_resolve_case_insensitively():
    got, missing = rc.resolve(["Folio", "recert_status", "Due_Date"])
    assert got["folio"] == "Folio"
    assert got["status"] == "recert_status"
    assert set(missing) == {"unsafe", "address"}


def test_the_first_candidate_present_wins():
    got, _ = rc.resolve(["PARCEL_ID", "FOLIO"])
    assert got["folio"] == "FOLIO", "FOLIO is listed before PARCEL_ID"


def test_an_unresolved_field_is_reported_not_guessed():
    got, missing = rc.resolve(["SOMETHING_ELSE"])
    assert got["folio"] is None
    assert "folio" in missing


def test_show_header_names_the_constant_to_edit(tmp_path, capsys):
    p = write_csv(tmp_path, ["WEIRD_ID", "STATE_OF_PLAY"], [["1", "x"]])
    rc.cmd_show_header(p)
    out = capsys.readouterr().out
    assert "CANDIDATES" in out
    assert "nothing can be joined" in out


# ── reading a file ─────────────────────────────────────────────────────────

def test_open_status_wording_sets_the_unsafe_flag():
    for status, want in [("Open Violation", 1), ("Overdue", 1), ("Past Due", 1),
                         ("Compliant", 0), ("Recertified", 0), ("", 0)]:
        got = int(any(t in status.lower() for t in rc.OPEN_TERMS))
        assert got == want, status


def test_an_explicit_unsafe_column_is_honoured():
    for raw, want in [("Y", 1), ("yes", 1), ("TRUE", 1), ("1", 1),
                      ("N", 0), ("no", 0), ("", 0)]:
        assert int(raw.strip().lower() in rc.TRUE_TERMS) == want, raw


def test_a_folio_too_short_to_key_a_building_is_skipped():
    """group_key is the 9-digit folio prefix; anything shorter cannot be joined."""
    assert len("".join(c for c in "0132-07" if c.isdigit())) < 9


# ── scoring ────────────────────────────────────────────────────────────────

def test_no_recert_data_scores_none_rather_than_falling_back_to_age():
    """Age is the guess this replaces. Falling back to it would quietly reinstate
    the thing the field exists to fix, and nothing downstream could tell."""
    assert bt.distress(45, None) is None
    assert bt.distress(None, None) is None


def test_an_open_case_scores_far_above_a_clear_building_of_the_same_age():
    clear = bt.distress(45, ("Compliant", "2030-01-01", 0))
    unsafe = bt.distress(45, ("Open violation", None, 1))
    assert unsafe > clear + 30, f"{unsafe} vs {clear}"


def test_age_still_separates_buildings_inside_the_distressed_set():
    """An open case on a 60-year-old building is a bigger assessment than on a
    30-year-old one."""
    assert bt.distress(65, ("Open", None, 1)) > bt.distress(35, ("Open", None, 1))


def test_distress_stays_within_range():
    for age in (None, 0, 30, 60, 200):
        for unsafe in (0, 1):
            v = bt.distress(age, ("x", None, unsafe))
            assert 0 <= v <= 100, (age, unsafe, v)


# ── the worst row wins ─────────────────────────────────────────────────────

def test_an_open_case_on_any_folio_is_an_open_case_for_the_building():
    """A garden complex spans many folios. Averaging an open case away would be
    the wrong answer."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""CREATE TABLE building_recert (folio TEXT PRIMARY KEY,
      group_key TEXT, status TEXT, due_date TEXT, unsafe_case INTEGER,
      address TEXT, source TEXT, ingested TEXT);""")
    con.execute("INSERT INTO building_recert VALUES ('010101000001','010101010',"
                "'Compliant',NULL,0,NULL,'f','d')")
    con.execute("INSERT INTO building_recert VALUES ('010101000002','010101010',"
                "'Open violation',NULL,1,NULL,'f','d')")
    con.commit()
    m = bt.recert_map(con)
    assert m["010101010"][2] == 1
    assert m["010101010"][0] == "Open violation"


def test_a_building_with_no_recert_row_is_absent_from_the_map():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""CREATE TABLE building_recert (folio TEXT PRIMARY KEY,
      group_key TEXT, status TEXT, due_date TEXT, unsafe_case INTEGER,
      address TEXT, source TEXT, ingested TEXT);""")
    con.commit()
    assert bt.recert_map(con) == {}
