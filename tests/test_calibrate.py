"""Measuring the score against buildings that actually terminated.

The weights have never been checked against an outcome. This is the machinery
that checks them — and the part that matters most is that it refuses to overstate
what a handful of positives can support.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("cal", ROOT / "scripts/prospect/calibrate.py")
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)

DDL = """
CREATE TABLE target (group_key TEXT PRIMARY KEY, project_number TEXT, condo_name TEXT,
  units_nal INTEGER, score REAL, score_age REAL, score_scale REAL,
  score_concentration REAL, score_absentee REAL);
CREATE TABLE dbpr_association (project_number TEXT PRIMARY KEY, primary_status TEXT,
  secondary_status TEXT);
"""


def db(rows):
    """rows: (key, terminated, age, scale, conc, absentee)"""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    for key, term, *c in rows:
        score = sum(c) / 4
        con.execute("INSERT INTO target VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, f"P{key}", f"Condo {key}", 100, score, *c))
        con.execute("INSERT INTO dbpr_association VALUES (?,?,?)",
                    (f"P{key}", "Terminated" if term else "Active", ""))
    con.commit()
    return con


def rows_for(pairs):
    return [{"terminated": t, "score": s, "group_key": str(i),
             "score_age": s, "score_scale": s,
             "score_concentration": s, "score_absentee": s}
            for i, (t, s) in enumerate(pairs)]


# ── AUC ────────────────────────────────────────────────────────────────────

def test_perfect_separation_is_one():
    assert cal.auc(rows_for([(1, 90), (0, 10)]), lambda r: r["score"]) == 1.0


def test_backwards_ranking_is_below_a_half():
    """A term below 0.5 is not weak — it is pointing the wrong way, which is a
    different and more useful finding."""
    assert cal.auc(rows_for([(1, 10), (0, 90)]), lambda r: r["score"]) == 0.0


def test_a_tie_counts_as_half():
    assert cal.auc(rows_for([(1, 50), (0, 50)]), lambda r: r["score"]) == 0.5


def test_auc_is_none_without_both_classes():
    assert cal.auc(rows_for([(1, 50), (1, 60)]), lambda r: r["score"]) is None
    assert cal.auc([], lambda r: r["score"]) is None


def test_auc_is_the_probability_a_positive_outranks_a_negative():
    # 2 positives x 2 negatives: 90 beats both, 40 beats one -> 3 of 4.
    rows = rows_for([(1, 90), (1, 40), (0, 50), (0, 10)])
    assert cal.auc(rows, lambda r: r["score"]) == pytest.approx(0.75)


# ── the labelled set ───────────────────────────────────────────────────────

def test_terminated_associations_are_found_through_the_registry():
    con = db([("a", 1, 90, 90, 90, 90), ("b", 0, 10, 10, 10, 10)])
    rows, pos = cal.labelled(con)
    assert len(rows) == 2 and len(pos) == 1
    assert pos[0]["group_key"] == "a"


def test_the_status_match_is_case_insensitive_and_substring():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    for i, status in enumerate(["TERMINATED", "terminated - voluntary",
                                "Dissolved", "Active", ""]):
        con.execute("INSERT INTO target VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(i), f"P{i}", "x", 10, 50, 50, 50, 50, 50))
        con.execute("INSERT INTO dbpr_association VALUES (?,?,?)", (f"P{i}", status, ""))
    con.commit()
    _, pos = cal.labelled(con)
    assert len(pos) == 3, "TERMINATED, terminated - voluntary and Dissolved"


def test_a_building_with_no_registry_match_is_not_labelled_either_way():
    """An unmatched building is not a negative — it is unknown, and counting it
    as a negative would quietly inflate every AUC in the report."""
    con = db([("a", 1, 90, 90, 90, 90)])
    con.execute("INSERT INTO target VALUES ('z','NOPE','x',10,80,80,80,80,80)")
    con.commit()
    rows, _ = cal.labelled(con)
    assert {r["group_key"] for r in rows} == {"a"}


# ── the blend ──────────────────────────────────────────────────────────────

def test_the_blend_reproduces_the_scoring_formula():
    w = {"age": 0.25, "scale": 0.20, "concentration": 0.35, "absentee": 0.20}
    r = {"score_age": 80, "score_scale": 40, "score_concentration": 100, "score_absentee": 0}
    assert cal.blended(w)(r) == pytest.approx(0.25 * 80 + 0.20 * 40 + 0.35 * 100)


def test_a_missing_component_scores_zero_rather_than_raising():
    w = {t: 0.25 for t in cal.TERMS}
    r = {"score_age": None, "score_scale": None,
         "score_concentration": None, "score_absentee": None}
    assert cal.blended(w)(r) == 0


# ── refusing to overstate ──────────────────────────────────────────────────

def test_suggest_refuses_to_fit_a_handful_of_positives(capsys):
    con = db([("a", 1, 90, 90, 90, 90)] + [(str(i), 0, 10, 10, 10, 10) for i in range(9)])
    with pytest.raises(SystemExit) as e:
        cal.cmd_suggest(con, None)
    assert "too few to fit" in str(e.value)


def test_report_says_so_when_nothing_is_labelled(capsys):
    con = db([("a", 0, 90, 90, 90, 90), ("b", 0, 10, 10, 10, 10)])
    with pytest.raises(SystemExit) as e:
        cal.cmd_report(con, None)
    msg = str(e.value)
    assert "none labelled terminated" in msg
    assert "--labels" in msg, "it should point at the command that shows the real wording"


def test_report_names_a_term_that_ranks_backwards(capsys):
    """age ranks correctly, absentee is inverted."""
    rows = [("t%d" % i, 1, 90, 50, 50, 10) for i in range(4)]
    rows += [("n%d" % i, 0, 10, 50, 50, 90) for i in range(8)]
    cal.cmd_report(db(rows), None)
    out = capsys.readouterr().out
    assert "age" in out and "absentee" in out
    assert "pointing the wrong way" in out
    assert "biased" in out, "the sampling bias belongs in the report, not a footnote"


def test_labels_prints_what_the_registry_actually_contains(capsys):
    con = db([("a", 1, 90, 90, 90, 90), ("b", 0, 10, 10, 10, 10)])
    cal.cmd_labels(con, None)
    out = capsys.readouterr().out
    assert "Terminated" in out and "Active" in out
    assert "YES" in out
    assert "TERMINATED_TERMS" in out, "it must say where to correct the match list"
