"""Measure the stage-1 score against buildings that actually terminated.

The weights in config.json were a considered judgment never checked against an
outcome. The outcome has been in the database all along: `ingest_dbpr.py`
writes each association's `Primary Status` / `Secondary Status` into
`dbpr_association`, and a terminated or dissolved association is a labelled
positive at zero collection cost.

Shared by scripts/prospect/calibrate.py (command line) and the Reference tab
(/api/calibration), so both report the same numbers.

What this can and cannot tell you
---------------------------------
A handful of terminations is not a training set. With four weights and twenty
positives a search will find weights that fit those twenty and generalise to
nothing -- so every fit is scored LEAVE-ONE-OUT as well, and weights are only
ever offered for adoption when the out-of-sample number holds up. The labelled
set is also biased in a way no arithmetic fixes: terminations that completed are
visible, and a building where somebody tried and failed looks exactly like one
nobody ever approached.
"""
from __future__ import annotations

import itertools
import json
import shutil
import statistics
from datetime import datetime
from pathlib import Path

# Registry wording that reads as a terminated or dissolved association.
# Matched case-insensitively as a substring of either status field; the label
# counts show what a given roll actually contains so this can be checked.
TERMINATED_TERMS = ("terminat", "dissol", "merged out", "withdrawn")

TERMS = ("age", "scale", "concentration", "absentee")
COMPONENT = {"age": "score_age", "scale": "score_scale",
             "concentration": "score_concentration", "absentee": "score_absentee"}
MIN_POSITIVES_TO_FIT = 5
GRID_STEP = 20          # weights on a 0.05 grid summing to 1 -- enumerable, auditable


def is_terminated(primary: str | None, secondary: str | None) -> bool:
    blob = f"{primary or ''} {secondary or ''}".lower()
    return any(t in blob for t in TERMINATED_TERMS)


def label_counts(con) -> list[dict]:
    rows = con.execute(
        "SELECT COALESCE(primary_status,'') p, COALESCE(secondary_status,'') s, "
        "COUNT(*) n FROM dbpr_association GROUP BY p, s ORDER BY n DESC").fetchall()
    return [{"primary": r[0], "secondary": r[1], "n": r[2],
             "terminated": is_terminated(r[0], r[1])} for r in rows]


def labelled(con):
    """(scored rows, positives): targets joined to the registry's status.

    Only buildings matched to a registry entry are included. An unmatched
    building is not a negative -- it is unknown, and counting it as one would
    quietly inflate every AUC."""
    like = " OR ".join(
        ["LOWER(COALESCE(d.primary_status,'')||' '||COALESCE(d.secondary_status,'')) "
         f"LIKE '%{t}%'" for t in TERMINATED_TERMS])
    rows = [dict(r) for r in con.execute(
        f"""SELECT t.group_key, t.condo_name, t.score, t.score_age, t.score_scale,
                   t.score_concentration, t.score_absentee, t.units_nal,
                   CASE WHEN {like} THEN 1 ELSE 0 END AS terminated
              FROM target t JOIN dbpr_association d
                ON d.project_number = t.project_number
             WHERE t.score IS NOT NULL""")]
    return rows, [r for r in rows if r["terminated"]]


def auc(rows, score_of):
    """Probability a terminated building outranks one that did not (ties
    count half). 0.5 is a coin flip; below it the score points backwards.

    Rank-sum (Mann-Whitney) form: O(n log n). The pairwise form it replaces
    was exact too, but the weight search evaluates this thousands of times and
    the pairwise version took minutes on a full county roll."""
    scored = [(score_of(r), 1 if r["terminated"] else 0) for r in rows]
    n_pos = sum(t for _, t in scored)
    n_neg = len(scored) - n_pos
    if not n_pos or not n_neg:
        return None
    scored.sort(key=lambda x: x[0])
    rank_sum = 0.0
    i = 0
    while i < len(scored):
        j = i
        while j + 1 < len(scored) and scored[j + 1][0] == scored[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1           # 1-based average rank of the tie group
        rank_sum += avg_rank * sum(t for _, t in scored[i:j + 1])
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def blended(weights):
    return lambda r: sum(weights[t] * (r[COMPONENT[t]] or 0) for t in TERMS)


def percentile_of(rows, r):
    below = sum(1 for x in rows if (x["score"] or 0) < (r["score"] or 0))
    return 100.0 * below / max(1, len(rows) - 1)


def report(con, weights: dict) -> dict:
    """How well the current weights rank the labelled set."""
    rows, pos = labelled(con)
    out = {"matched": len(rows), "positives": len(pos), "weights": weights}
    if not rows:
        out["state"] = "no_matches"
        return out
    if not pos:
        out["state"] = "no_positives"
        return out
    ranked = sorted(rows, key=lambda r: -(r["score"] or 0))
    n_top = max(1, len(ranked) // 10)
    out.update({
        "state": "ok",
        "auc": auc(rows, lambda r: r["score"] or 0),
        "top_decile_hits": sum(1 for r in ranked[:n_top] if r["terminated"]),
        "median_percentile": statistics.median(percentile_of(rows, r) for r in pos),
        "terms": {t: {"weight": weights[t],
                      "auc": auc(rows, lambda r, c=COMPONENT[t]: r[c] or 0)} for t in TERMS},
    })
    return out


def suggest(con, weights: dict) -> dict:
    """Best weights on the grid, and whether they survive leave-one-out.

    verdict: "adopt" (holds up out of sample and beats the current weights),
    "memorising" (the fit beats its own leave-one-out estimate by > 0.05), or
    "no_gain" (honestly scored, no better than what is there)."""
    rows, pos = labelled(con)
    if len(pos) < MIN_POSITIVES_TO_FIT:
        return {"state": "too_few", "positives": len(pos), "matched": len(rows)}
    grid = [c for c in itertools.product(range(GRID_STEP + 1), repeat=len(TERMS))
            if sum(c) == GRID_STEP]

    def best_over(sample):
        best, best_a = None, -1.0
        for c in grid:
            w = {t: c[i] / GRID_STEP for i, t in enumerate(TERMS)}
            a = auc(sample, blended(w))
            if a is not None and a > best_a:
                best, best_a = w, a
        return best, best_a

    fitted, fitted_auc = best_over(rows)
    loo = []
    for p in pos:
        sample = [r for r in rows if r["group_key"] != p["group_key"]]
        w, _ = best_over(sample)
        loo.append(auc(rows, blended(w)))
    loo_auc = sum(loo) / len(loo)
    cur_auc = auc(rows, blended(weights))
    if fitted_auc - loo_auc > 0.05:
        verdict = "memorising"
    elif loo_auc <= cur_auc + 0.01:
        verdict = "no_gain"
    else:
        verdict = "adopt"
    return {"state": "ok", "positives": len(pos), "matched": len(rows),
            "current": weights, "current_auc": cur_auc,
            "fitted": {k: round(v, 2) for k, v in fitted.items()},
            "fitted_auc": fitted_auc, "loo_auc": loo_auc, "verdict": verdict}


def apply(con, cfg_path: Path, suggestion: dict) -> dict:
    """Adopt fitted weights: back up config.json, write the weights with a note
    recording the evidence, and rescore every target in place.

    Refuses anything but an "adopt" verdict -- the leave-one-out check is the
    whole safeguard, and it is not optional from the UI either. Rescoring needs
    no rebuild: `score` is a fixed linear blend of four stored components."""
    if suggestion.get("verdict") != "adopt":
        raise ValueError("only weights that held up out of sample can be applied")
    w = {t: float(suggestion["fitted"][t]) for t in TERMS}
    if abs(sum(w.values()) - 1.0) > 1e-6:
        raise ValueError("weights must sum to 1")

    cfg_path = Path(cfg_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = cfg_path.with_name(f"{cfg_path.stem}.{stamp}.bak{cfg_path.suffix}")
    shutil.copy2(cfg_path, backup)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    before = cfg.get("score_weights")
    cfg["score_weights"] = w
    cfg["_weights_note"] = (
        f"Calibrated {datetime.now():%Y-%m-%d} against {suggestion['positives']} terminated "
        f"associations in the DBPR registry: AUC {suggestion['current_auc']:.3f} -> "
        f"{suggestion['loo_auc']:.3f} (leave-one-out). Previous weights {before} are in "
        f"{backup.name}. Re-check after each new roll -- a small labelled set moves.")
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    con.execute(
        "UPDATE target SET score = ROUND(? * COALESCE(score_age,0) + ? * COALESCE(score_scale,0)"
        " + ? * COALESCE(score_concentration,0) + ? * COALESCE(score_absentee,0), 2)",
        tuple(w[t] for t in TERMS))
    n = con.execute("SELECT changes()").fetchone()[0]
    con.commit()
    return {"applied": w, "previous": before, "backup": backup.name, "rescored": n}
