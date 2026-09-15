r"""Load Miami-Dade 40-year recertification status.

`milestone_due` is `age_years >= 30` -- a guess at a fact the county publishes.
Post-Surfside that guess is the weakest part of the screen: it lumps a building
that already passed recertification together with one staring at a six-figure
per-unit assessment its owners cannot fund, and only the second has motivated
sellers.

RETRIEVAL IS MANUAL, like the declarations and for the same reason. The county's
open-data endpoints move, and a fetch URL hardcoded here would be a silent
failure the first time it changed. Download the recertification / unsafe
structures dataset as CSV and point this at it:

    venv\Scripts\python.exe scripts\prospect\ingest_recert.py --show-header --file X.csv
    venv\Scripts\python.exe scripts\prospect\ingest_recert.py --file X.csv

Column names are resolved from CANDIDATES rather than hardcoded, and the run
prints which ones matched -- so a published file with different headings is a
one-line edit here, not a silent load of empty columns.
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.prospect.db import connect, log_ingest  # noqa: E402

# Tried in order. The first heading present wins; --show-header lists what a file
# actually carries so this can be corrected by looking rather than guessing.
CANDIDATES = {
    "folio": ("FOLIO", "FOLIO_NUMBER", "FOLIONUMBER", "PARCEL_ID", "PARCELID",
              "FOLIO_NO", "PROPERTY_FOLIO"),
    "status": ("RECERT_STATUS", "RECERTIFICATION_STATUS", "STATUS", "CASE_STATUS",
               "CURRENT_STATUS", "COMPLIANCE_STATUS"),
    "due_date": ("DUE_DATE", "RECERT_DUE", "RECERTIFICATION_DUE", "DUE",
                 "NEXT_RECERT_DATE", "EXPIRATION_DATE"),
    "unsafe": ("UNSAFE", "UNSAFE_STRUCTURE", "UNSAFE_CASE", "IS_UNSAFE",
               "UNSAFE_STRUCTURES_CASE"),
    "address": ("ADDRESS", "SITE_ADDRESS", "PROPERTY_ADDRESS", "LOCATION"),
}

# Status wording that means the building is NOT in the clear. Matched as a
# substring, case-insensitively; anything unmatched is stored verbatim and
# treated as no distress, because inventing a meaning for an unknown status is
# worse than reporting it unread.
OPEN_TERMS = ("open", "overdue", "past due", "delinquent", "non-compliant",
              "noncompliant", "unsafe", "pending", "notice", "violation", "fail")
TRUE_TERMS = ("y", "yes", "true", "1", "open", "active")


def resolve(header):
    ix = {h.strip().upper(): h for h in header}
    out, missing = {}, []
    for field, names in CANDIDATES.items():
        hit = next((ix[n] for n in names if n in ix), None)
        out[field] = hit
        if hit is None:
            missing.append(field)
    return out, missing


def cmd_show_header(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    print(f"{len(header)} columns:")
    for i in range(0, len(header), 4):
        print("   " + "  ".join(f"{c:<28}" for c in header[i:i + 4]))
    got, missing = resolve(header)
    print("\nresolved:")
    for k, v in got.items():
        print(f"  {k:<10} {v or '— not found —'}")
    if missing:
        print(f"\nAdd the real heading(s) for {missing} to CANDIDATES in this file.")
    if not got["folio"]:
        print("\nWithout a folio column nothing can be joined to a building.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="downloaded recertification CSV")
    ap.add_argument("--show-header", action="store_true")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    if args.show_header:
        return cmd_show_header(path)

    started = datetime.now().isoformat(timespec="seconds")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        got, missing = resolve(reader.fieldnames or [])
        if not got["folio"]:
            raise SystemExit(
                "No folio column found — nothing can be joined to a building. "
                "Run --show-header to see what this file carries, then add the "
                "heading to CANDIDATES['folio'].")
        print("resolved: " + ", ".join(f"{k}={v}" for k, v in got.items() if v))
        if missing:
            print(f"  ! not found, stored NULL: {missing}")

        rows, skipped = [], 0
        for r in reader:
            folio = "".join(ch for ch in (r.get(got["folio"]) or "") if ch.isdigit())
            if len(folio) < 9:
                skipped += 1
                continue
            status = (r.get(got["status"]) or "").strip() if got["status"] else None
            raw_unsafe = (r.get(got["unsafe"]) or "").strip().lower() if got["unsafe"] else ""
            # Two independent signals: an explicit unsafe column, and status
            # wording that reads as open. Either is enough.
            unsafe = int(raw_unsafe in TRUE_TERMS
                         or any(t in (status or "").lower() for t in OPEN_TERMS))
            rows.append((
                folio, folio[:9], status or None,
                (r.get(got["due_date"]) or "").strip() or None if got["due_date"] else None,
                unsafe,
                (r.get(got["address"]) or "").strip() or None if got["address"] else None,
                path.name, datetime.now().date().isoformat()))

    con = connect()
    con.execute("DELETE FROM building_recert")
    con.executemany("INSERT OR REPLACE INTO building_recert VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    matched = con.execute(
        "SELECT COUNT(DISTINCT group_key) FROM building_recert WHERE group_key IN "
        "(SELECT group_key FROM target)").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM target").fetchone()[0]
    log_ingest(con, "recert", started, datetime.now().isoformat(timespec="seconds"),
               len(rows), path.name)
    print(f"\n{len(rows):,} recert rows, {skipped:,} skipped (no usable folio)")
    print(f"{matched:,} of {total:,} scored buildings matched")
    if total and matched / total < 0.2:
        print("  ! Fewer than a fifth matched. The folio in this file may be the LAND "
              "parcel rather than a condo folio — check a few against the roll before "
              "trusting score_distress.")
    print("\nRun build_targets.py to fold this into the score.")
    con.close()


if __name__ == "__main__":
    main()
