r"""Measure the stage-1 score against buildings that actually terminated.

The weights in config.json are a considered judgment and have never been checked
against an outcome, because nothing in this app knew which buildings terminated.
They do: `ingest_dbpr.py` has always written `Primary Status` and
`Secondary Status` into `dbpr_association`, and until now nothing read them.

    venv\Scripts\python.exe scripts\prospect\calibrate.py --labels
    venv\Scripts\python.exe scripts\prospect\calibrate.py --report
    venv\Scripts\python.exe scripts\prospect\calibrate.py --suggest

--labels   what the registry's status fields actually contain, so the terms that
           mean "terminated" can be chosen by looking rather than guessing
--report   how well the current weights rank the labelled set
--suggest  weights that would rank it better, with the overfitting warning that
           a sample this size demands

On what this can and cannot tell you
------------------------------------
A handful of terminations is not a training set. With four weights and twenty
positives, a search will find weights that fit those twenty and generalise to
nothing -- so --suggest reports a LEAVE-ONE-OUT score alongside the fitted one.
When the two diverge, the fit is memorising. That comparison is the whole point
of the command; the suggested numbers on their own are close to meaningless.

The labelled set is also biased in a way no amount of arithmetic fixes:
terminations that completed are visible, and buildings where somebody tried and
failed look identical to buildings nobody ever approached.
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.prospect.db import connect_query  # noqa: E402

CFG_PATH = ROOT / "backend" / "prospect" / "config.json"

# Registry status wording that reads as a terminated or dissolved association.
# Matched case-insensitively against both status fields; --labels prints what
# your roll actually contains so this can be corrected by looking.
TERMINATED_TERMS = ("terminat", "dissol", "merged out", "withdrawn")

TERMS = ("age", "scale", "concentration", "absentee")
COMPONENT = {"age": "score_age", "scale": "score_scale",
             "concentration": "score_concentration", "absentee": "score_absentee"}


def cmd_labels(con, args):
    rows = con.execute(
        "SELECT COALESCE(primary_status,'') p, COALESCE(secondary_status,'') s, "
        "COUNT(*) n FROM dbpr_association GROUP BY p, s ORDER BY n DESC").fetchall()
    if not rows:
        raise SystemExit("dbpr_association is empty — run scripts/prospect/ingest_dbpr.py first")
    print(f"{'primary':<28}{'secondary':<28}{'n':>7}   matches?")
    hit = 0
    for r in rows:
        blob = f"{r['p']} {r['s']}".lower()
        m = any(t in blob for t in TERMINATED_TERMS)
        hit += r["n"] if m else 0
        print(f"{r['p'][:27]:<28}{r['s'][:27]:<28}{r['n']:>7}   {'YES' if m else ''}")
    print(f"\n{hit:,} association(s) match TERMINATED_TERMS {TERMINATED_TERMS}.")
    print("If that is wrong, edit TERMINATED_TERMS at the top of this file — the "
          "registry's wording is the ground truth, not this list.")


def labelled(con):
    """(scored rows, positives) — targets joined to the registry's status."""
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
    """Probability a terminated building outranks one that did not.

    0.5 is a coin flip; the score is worth nothing below it. Computed by
    counting concordant pairs, which is exact and needs no library.
    """
    pos = [score_of(r) for r in rows if r["terminated"]]
    neg = [score_of(r) for r in rows if not r["terminated"]]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def blended(weights):
    return lambda r: sum(weights[t] * (r[COMPONENT[t]] or 0) for t in TERMS)


def percentile_of(rows, r):
    below = sum(1 for x in rows if (x["score"] or 0) < (r["score"] or 0))
    return 100.0 * below / max(1, len(rows) - 1)


def cmd_report(con, args):
    rows, pos = labelled(con)
    if not rows:
        raise SystemExit("no targets matched to the registry — run build_targets.py first")
    if not pos:
        raise SystemExit(
            f"{len(rows):,} matched buildings, none labelled terminated.\n"
            "Run --labels to see what the registry's status fields contain; the "
            "terms this script looks for may not be the wording in your roll.")

    cfg = json.loads(CFG_PATH.read_text())
    w = cfg["score_weights"]
    a = auc(rows, lambda r: r["score"] or 0)
    print(f"{len(rows):,} matched buildings, {len(pos)} labelled terminated "
          f"({100 * len(pos) / len(rows):.2f}%)\n")
    print(f"AUC of the current score:  {a:.3f}"
          f"   ({'no better than chance' if a <= 0.55 else 'ranks them above average'})")

    ranked = sorted(rows, key=lambda r: -(r["score"] or 0))
    n_top = max(1, len(ranked) // 10)
    in_top = sum(1 for r in ranked[:n_top] if r["terminated"])
    print(f"Top decile captures:       {in_top} of {len(pos)} "
          f"({100 * in_top / len(pos):.0f}%) — a random decile would capture 10%")
    pcts = sorted(percentile_of(rows, r) for r in pos)
    print(f"Median percentile of a terminated building: {pcts[len(pcts) // 2]:.0f}\n")

    print("Each term on its own:")
    for t in TERMS:
        ta = auc(rows, lambda r, c=COMPONENT[t]: r[c] or 0)
        print(f"  {t:<16} weight {w[t]:.2f}   AUC {ta:.3f}")
    print("\nA term whose own AUC is near 0.5 is carrying weight it has not earned.")
    print("A term below 0.5 is ranking backwards — it is pointing the wrong way.")

    print("\nThe labelled set is biased and no arithmetic fixes it: terminations that")
    print("COMPLETED are visible, and a building where somebody tried and failed looks")
    print("exactly like one nobody ever approached.")


def cmd_suggest(con, args):
    rows, pos = labelled(con)
    if len(pos) < 5:
        raise SystemExit(f"only {len(pos)} labelled positive(s) — too few to fit anything. "
                         "Run --report to see where the current weights stand.")
    cfg = json.loads(CFG_PATH.read_text())
    current = cfg["score_weights"]

    # Weights on a 0.05 grid summing to 1. Small enough to enumerate exactly,
    # which beats an optimiser nobody can audit.
    step = 20
    grid = [c for c in itertools.product(range(step + 1), repeat=len(TERMS))
            if sum(c) == step]

    def best_over(sample):
        best, best_a = None, -1.0
        for c in grid:
            w = {t: c[i] / step for i, t in enumerate(TERMS)}
            a = auc(sample, blended(w))
            if a is not None and a > best_a:
                best, best_a = w, a
        return best, best_a

    fitted, fitted_auc = best_over(rows)

    # Leave-one-out: refit without each positive, then score that positive. If
    # this collapses against the fitted number, the fit is memorising.
    loo = []
    for p in pos:
        sample = [r for r in rows if r["group_key"] != p["group_key"]]
        w, _ = best_over(sample)
        loo.append(auc(rows, blended(w)))
    loo_auc = sum(loo) / len(loo)

    cur_auc = auc(rows, blended(current))
    print(f"{len(pos)} labelled positives, {len(rows):,} buildings\n")
    print(f"current weights   AUC {cur_auc:.3f}   {current}")
    print(f"fitted weights    AUC {fitted_auc:.3f}   "
          f"{ {k: round(v, 2) for k, v in fitted.items()} }")
    print(f"leave-one-out     AUC {loo_auc:.3f}")
    gap = fitted_auc - loo_auc
    print()
    if gap > 0.05:
        print(f"The fit beats its own leave-one-out estimate by {gap:.3f}. That is")
        print("memorising these particular buildings, not learning what predicts a")
        print("termination. DO NOT adopt these weights.")
    elif loo_auc <= cur_auc + 0.01:
        print("The fitted weights do not beat the current ones once the fit is")
        print("honest about generalisation. Leave config.json alone.")
    else:
        print(f"The fitted weights hold up out of sample (+{loo_auc - cur_auc:.3f} AUC).")
        print("Worth considering — and worth writing the reasoning into the config's")
        print("_weights_note, the way the existing one does, rather than just the numbers.")
    print("\nNothing is written. Edit config.json by hand and re-run build_targets.py.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--suggest", action="store_true")
    args = ap.parse_args()
    con = connect_query()
    con.row_factory = __import__("sqlite3").Row
    try:
        if args.labels:
            cmd_labels(con, args)
        elif args.suggest:
            cmd_suggest(con, args)
        else:
            cmd_report(con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
