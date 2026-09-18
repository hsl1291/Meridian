"""do_zhvi's anchor-collision detection.

Zillow's metro file is keyed by title ("Miami-Fort Lauderdale-West Palm
Beach, FL"), which drifts out of sync with the Census delineation's own
titles, so unmatched Zillow rows fall back to a looser "lead city + lead
state" anchor. Two (or more) metros sharing an anchor are dropped as
ambiguous rather than guessed at. The accumulating expression that decides
this is a single dense ternary that is easy to misread as reverting to a
concrete CBSA once a THIRD metro collides on an already-ambiguous anchor --
it does not (None can never equal a later cbsa string, so the ambiguous
branch keeps re-selecting itself), but it is subtle enough, and load-bearing
enough for data correctness, to pin down explicitly rather than trust by
inspection alone.
"""
import csv
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_ingest_market():
    spec = importlib.util.spec_from_file_location(
        "ingest_market_mod", ROOT / "scripts" / "prospect" / "ingest_market.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DDL = """
CREATE TABLE county (fips TEXT PRIMARY KEY, name TEXT, state TEXT, state_fips TEXT,
  cbsa TEXT, cbsa_name TEXT, cbsa_type TEXT);
CREATE TABLE metro_price (cbsa TEXT, month TEXT, zhvi REAL, PRIMARY KEY (cbsa, month));
"""


def _db(cbsa_rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    for i, (cbsa, cbsa_name) in enumerate(cbsa_rows):
        con.execute("INSERT INTO county VALUES (?,?,?,?,?,?,?)",
                    (f"999{i:02d}", cbsa_name, "IL", "17", cbsa, cbsa_name, "Metropolitan"))
    con.commit()
    return con


def _zhvi_csv(tmp_path, region_name):
    """A minimal Zillow-shaped CSV: RegionName plus one dated column, since
    do_zhvi only keeps rows whose header matches r'\\d{4}-\\d{2}-\\d{2}'."""
    path = tmp_path / "zillow_metro_zhvi.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["RegionName", "2024-01-31"])
        w.writerow([region_name, "350000"])
    return path


def test_a_third_colliding_metro_does_not_undo_an_already_ambiguous_anchor(tmp_path, monkeypatch):
    """Pins the subtle case described in the module docstring above: a third
    collision must not read as "differs from None, so store it" and revert
    an anchor that two earlier metros already made ambiguous."""
    mod = _load_ingest_market()
    # Three distinct CBSAs whose cbsa_name all reduce to the same
    # "lead city + lead state" anchor (SPRINGFIELD|IL), none of which is the
    # exact title the Zillow row uses -- forcing the anchor fallback path.
    con = _db([
        ("11111", "Springfield-Metro One, IL"),
        ("22222", "Springfield-Metro Two, IL"),
        ("33333", "Springfield-Metro Three, IL"),
    ])
    csv_path = _zhvi_csv(tmp_path, "Springfield-Zillow Title, IL")
    monkeypatch.setattr(mod, "fetch", lambda key, force: csv_path)

    mod.do_zhvi(con, force=False)

    rows = con.execute("SELECT * FROM metro_price").fetchall()
    assert rows == [], (
        "a region matching three colliding metros' anchor must be dropped as "
        "ambiguous, not attributed to whichever CBSA was read third"
    )


def test_two_metros_sharing_an_anchor_are_still_dropped(tmp_path, monkeypatch):
    """The original, already-working case, kept as a companion so a fix for
    the three-way collision can't accidentally break the two-way one."""
    mod = _load_ingest_market()
    con = _db([
        ("11111", "Springfield-Metro One, IL"),
        ("22222", "Springfield-Metro Two, IL"),
    ])
    csv_path = _zhvi_csv(tmp_path, "Springfield-Zillow Title, IL")
    monkeypatch.setattr(mod, "fetch", lambda key, force: csv_path)

    mod.do_zhvi(con, force=False)

    assert con.execute("SELECT * FROM metro_price").fetchall() == []


def test_an_unambiguous_anchor_still_matches(tmp_path, monkeypatch):
    """Companion sanity check: the fallback path must still work when there
    is no collision at all."""
    mod = _load_ingest_market()
    con = _db([("11111", "Springfield-Metro One, IL")])
    csv_path = _zhvi_csv(tmp_path, "Springfield-Zillow Title, IL")
    monkeypatch.setattr(mod, "fetch", lambda key, force: csv_path)

    mod.do_zhvi(con, force=False)

    rows = con.execute("SELECT cbsa, zhvi FROM metro_price").fetchall()
    assert [(r["cbsa"], r["zhvi"]) for r in rows] == [("11111", 350000.0)]
