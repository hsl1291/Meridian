"""Score calibration in the app: the rank-sum AUC, the adopt-only-if-it-holds
safeguard, rescoring in place, and the Reference tab's routes."""
import json
import random
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.prospect.calibration as cal  # noqa: E402
import backend.prospect.routes as routes  # noqa: E402
from backend.app import app  # noqa: E402

DDL = """
CREATE TABLE target (group_key TEXT PRIMARY KEY, project_number TEXT, condo_name TEXT,
  units_nal INTEGER, score REAL, score_age REAL, score_scale REAL,
  score_concentration REAL, score_absentee REAL);
CREATE TABLE dbpr_association (project_number TEXT PRIMARY KEY, primary_status TEXT,
  secondary_status TEXT);
"""


def make_db(path, rows):
    """rows: (key, terminated, age, scale, conc, absentee); score uses the
    config's current weights so the fixture matches a real build."""
    w = json.loads((ROOT / "backend/prospect/config.json").read_text(encoding="utf-8"))["score_weights"]
    con = sqlite3.connect(path)
    con.executescript(DDL)
    for key, term, a, s, c, ab in rows:
        score = round(w["age"] * a + w["scale"] * s + w["concentration"] * c + w["absentee"] * ab, 2)
        con.execute("INSERT INTO target VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, f"P{key}", f"Condo {key}", 100, score, a, s, c, ab))
        con.execute("INSERT INTO dbpr_association VALUES (?,?,?)",
                    (f"P{key}", "TERMINATED" if term else "ACTIVE", ""))
    con.commit()
    con.close()


def backwards_rows():
    """Terminated buildings are old/large/concentrated LOW but absentee HIGH, so
    the current blend ranks them backwards and absentee alone separates them."""
    rows = [(f"t{i}", 1, 10 + i, 10, 10, 90 - i) for i in range(6)]
    rows += [(f"n{i}", 0, 80 + i % 7, 85, 80, 10 + i % 5) for i in range(30)]
    return rows


def test_rank_sum_auc_equals_the_pairwise_definition():
    rng = random.Random(7)
    rows = [{"terminated": rng.random() < 0.2, "s": rng.choice(range(20))} for _ in range(300)]
    pos = [r["s"] for r in rows if r["terminated"]]
    neg = [r["s"] for r in rows if not r["terminated"]]
    pairwise = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))
    assert cal.auc(rows, lambda r: r["s"]) == pytest.approx(pairwise)


def test_apply_refuses_anything_but_an_adopt_verdict(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"score_weights": {}}', encoding="utf-8")
    for verdict in ("memorising", "no_gain", None):
        with pytest.raises(ValueError):
            cal.apply(sqlite3.connect(":memory:"), cfg, {"verdict": verdict})


@pytest.fixture
def wired(tmp_path, monkeypatch):
    dbfile = tmp_path / "prospect.db"
    make_db(dbfile, backwards_rows())
    here = tmp_path / "prospect"
    here.mkdir()
    shutil.copy(ROOT / "backend/prospect/config.json", here / "config.json")

    def db():
        con = sqlite3.connect(dbfile)
        con.row_factory = sqlite3.Row
        return con
    monkeypatch.setattr(routes, "db", db)
    monkeypatch.setattr(routes, "HERE", here)
    monkeypatch.setattr(routes, "_CAL_FIT", None)
    monkeypatch.setitem(routes.CFG, "score_weights", dict(routes.CFG["score_weights"]))
    from backend.prospect import memo
    monkeypatch.setitem(memo.CFG, "score_weights", dict(memo.CFG["score_weights"]))
    return {"db": dbfile, "cfg": here / "config.json", "client": TestClient(app)}


def test_status_reports_how_the_current_score_ranks_terminations(wired):
    d = wired["client"].get("/api/calibration").json()
    assert d["terminated_associations"] == 6
    assert d["report"]["state"] == "ok"
    assert d["report"]["auc"] < 0.5, "this fixture's current score ranks them backwards"
    assert d["report"]["terms"]["absentee"]["auc"] == 1.0
    assert d["fit"] is None


def test_apply_without_a_fit_is_refused(wired):
    r = wired["client"].post("/api/calibration/apply")
    assert r.status_code == 409


def test_fit_then_adopt_rescores_and_records_the_evidence(wired):
    c = wired["client"]
    f = c.post("/api/calibration/fit").json()
    assert f["verdict"] == "adopt", f
    assert f["loo_auc"] > f["current_auc"]

    out = c.post("/api/calibration/apply").json()
    assert out["rescored"] == 36
    assert abs(sum(out["applied"].values()) - 1) < 1e-9

    cfg = json.loads(wired["cfg"].read_text(encoding="utf-8"))
    assert cfg["score_weights"] == out["applied"]
    assert "leave-one-out" in cfg["_weights_note"]
    assert (wired["cfg"].parent / out["backup"]).exists(), "the old config is kept"
    assert routes.CFG["score_weights"] == out["applied"], "the running app uses them now"

    con = sqlite3.connect(wired["db"])
    w = out["applied"]
    for a, s, cc, ab, score in con.execute(
            "SELECT score_age, score_scale, score_concentration, score_absentee, score FROM target"):
        assert score == pytest.approx(round(w["age"] * a + w["scale"] * s
                                            + w["concentration"] * cc + w["absentee"] * ab, 2))
    after = c.get("/api/calibration").json()["report"]
    assert after["auc"] > 0.9
    assert c.post("/api/calibration/apply").status_code == 409, "a fit is applied once"


def test_a_fit_goes_stale_when_the_targets_change(wired):
    c = wired["client"]
    c.post("/api/calibration/fit")
    con = sqlite3.connect(wired["db"])
    con.execute("UPDATE target SET score = score + 1 WHERE group_key = 'n0'")
    con.commit()
    con.close()
    assert c.get("/api/calibration").json()["fit"] is None
    assert c.post("/api/calibration/apply").status_code == 409
