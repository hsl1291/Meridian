r"""Measure the stage-1 score against buildings that actually terminated.

The weights in config.json are a considered judgment and have never been checked
against an outcome, because nothing in this app knew which buildings terminated.
They do: `ingest_dbpr.py` has always written `Primary Status` and
`Secondary Status` into `dbpr_association`, and until now nothing read them.

    venv\Scripts\python.exe scripts\prospect\calibrate.py --labels
    venv\Scripts\python.exe scripts\prospect\calibrate.py --report
    venv\Scripts\python.exe scripts\prospect\calibrate.py --suggest
    venv\Scripts\python.exe scripts\prospect\calibrate.py --apply

--labels   what the registry's status fields actually contain, so the terms that
           mean "terminated" can be chosen by looking rather than guessing
--report   how well the current weights rank the labelled set
--suggest  weights that would rank it better, with the overfitting warning that
           a sample this size demands
--apply    adopt the suggested weights -- ONLY if they held up out of sample --
           backing up config.json and rescoring every target in place. The
           Reference tab in the app runs the same thing.

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
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.prospect.calibration import (  # noqa: E402,F401  (re-exported for tests)
    COMPONENT, MIN_POSITIVES_TO_FIT, TERMINATED_TERMS, TERMS, apply, auc, blended,
    label_counts, labelled, percentile_of, report, suggest)
from backend.prospect.db import connect_query  # noqa: E402

CFG_PATH = ROOT / "backend" / "prospect" / "config.json"


def _weights():
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))["score_weights"]


def cmd_labels(con, args):
    rows = label_counts(con)
    if not rows:
        raise SystemExit("dbpr_association is empty — run scripts/prospect/ingest_dbpr.py first")
    print(f"{'primary':<28}{'secondary':<28}{'n':>7}   matches?")
    hit = 0
    for r in rows:
        hit += r["n"] if r["terminated"] else 0
        print(f"{r['primary'][:27]:<28}{r['secondary'][:27]:<28}{r['n']:>7}   "
              f"{'YES' if r['terminated'] else ''}")
    print(f"\n{hit:,} association(s) match TERMINATED_TERMS {TERMINATED_TERMS}.")
    print("If that is wrong, edit TERMINATED_TERMS in backend/prospect/calibration.py — "
          "the registry's wording is the ground truth, not this list.")


def cmd_report(con, args):
    r = report(con, _weights())
    if r["state"] == "no_matches":
        raise SystemExit("no targets matched to the registry — run build_targets.py first")
    if r["state"] == "no_positives":
        raise SystemExit(
            f"{r['matched']:,} matched buildings, none labelled terminated.\n"
            "Run --labels to see what the registry's status fields contain; the "
            "terms this script looks for may not be the wording in your roll.")
    a, n, npos = r["auc"], r["matched"], r["positives"]
    print(f"{n:,} matched buildings, {npos} labelled terminated ({100 * npos / n:.2f}%)\n")
    print(f"AUC of the current score:  {a:.3f}"
          f"   ({'no better than chance' if a <= 0.55 else 'ranks them above average'})")
    print(f"Top decile captures:       {r['top_decile_hits']} of {npos} "
          f"({100 * r['top_decile_hits'] / npos:.0f}%) — a random decile would capture 10%")
    print(f"Median percentile of a terminated building: {r['median_percentile']:.0f}\n")
    print("Each term on its own:")
    for t, v in r["terms"].items():
        print(f"  {t:<16} weight {v['weight']:.2f}   AUC {v['auc']:.3f}")
    print("\nA term whose own AUC is near 0.5 is carrying weight it has not earned.")
    print("A term below 0.5 is ranking backwards — it is pointing the wrong way.")
    print("\nThe labelled set is biased and no arithmetic fixes it: terminations that")
    print("COMPLETED are visible, and a building where somebody tried and failed looks")
    print("exactly like one nobody ever approached.")


def cmd_suggest(con, args):
    s = suggest(con, _weights())
    if s["state"] == "too_few":
        raise SystemExit(f"only {s['positives']} labelled positive(s) — too few to fit anything. "
                         "Run --report to see where the current weights stand.")
    print(f"{s['positives']} labelled positives, {s['matched']:,} buildings\n")
    print(f"current weights   AUC {s['current_auc']:.3f}   {s['current']}")
    print(f"fitted weights    AUC {s['fitted_auc']:.3f}   {s['fitted']}")
    print(f"leave-one-out     AUC {s['loo_auc']:.3f}")
    print()
    if s["verdict"] == "memorising":
        print(f"The fit beats its own leave-one-out estimate by {s['fitted_auc'] - s['loo_auc']:.3f}.")
        print("That is memorising these particular buildings, not learning what predicts a")
        print("termination. DO NOT adopt these weights.")
    elif s["verdict"] == "no_gain":
        print("The fitted weights do not beat the current ones once the fit is")
        print("honest about generalisation. Leave config.json alone.")
    else:
        print(f"The fitted weights hold up out of sample (+{s['loo_auc'] - s['current_auc']:.3f} AUC).")
        print("Run --apply to adopt them (config.json is backed up first).")
    return s


def cmd_apply(con, args):
    s = cmd_suggest(con, args)
    if s["verdict"] != "adopt":
        raise SystemExit("\nNothing applied.")
    r = apply(con, CFG_PATH, s)
    print(f"\nApplied {r['applied']}; {r['rescored']:,} targets rescored. "
          f"Previous config saved as {r['backup']}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    con = connect_query()
    con.row_factory = __import__("sqlite3").Row
    try:
        if args.labels:
            cmd_labels(con, args)
        elif args.apply:
            cmd_apply(con, args)
        elif args.suggest:
            cmd_suggest(con, args)
        else:
            cmd_report(con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
