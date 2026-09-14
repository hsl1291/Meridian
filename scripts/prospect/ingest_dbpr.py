"""Load the DBPR Division of Condominiums registry (Dade) into SQLite.

The tax roll knows who owns what today. It does NOT know when the declaration
was recorded -- and declaration vintage is the single best predictor of what
termination threshold the document contains, because the statutory default
changed over time and pre-1990s declarations are where original Kaufman
language lives. That date comes from here.

    Condo_MD.csv -- Dade + Monroe, 5,810 rows (5,456 Dade)
    Columns: Project Number, File Number, Condo Name, County, Street City State
             Zip, Units, Recorded Date, Primary Status, Secondary Status,
             Managing Entity {Number,Name,Route,Street,City,State,Zip}

    venv\\Scripts\\python.exe scripts\\prospect\\ingest_dbpr.py [--refresh]
"""
import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.prospect.db import connect, log_ingest  # noqa: E402
from backend.prospect.norm import building_addr, normalize_name  # noqa: E402

DBPR_URL = "https://www2.myfloridalicense.com/sto/file_download/extracts/Condo_MD.csv"
RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "Condo_MD.csv"
COUNTY = "Dade"

# "8450 S.W. 133 AVENUE ROAD, MIAMI, FL 33183"
ADDR_RE = re.compile(r"^(?P<street>.*?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s*(?P<zip>\d{5})?", re.I)


def download(force=False):
    if RAW.exists() and not force:
        print(f"using cached {RAW.name} ({RAW.stat().st_size/1e3:.0f} KB)")
        return
    RAW.parent.mkdir(parents=True, exist_ok=True)
    print("downloading Condo_MD.csv ...")
    r = requests.get(DBPR_URL, timeout=180)
    r.raise_for_status()
    RAW.write_bytes(r.content)
    print(f"  saved {len(r.content)/1e3:.0f} KB")


def split_addr(raw):
    """DBPR packs street/city/state/zip into one field."""
    if not raw:
        return None, None, None
    m = ADDR_RE.match(raw.strip())
    if not m:
        return raw.strip(), None, None
    return m.group("street").strip(), (m.group("city") or "").strip().upper() or None, m.group("zip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    download(force=args.refresh)

    started = datetime.now().isoformat(timespec="seconds")
    rows, skipped_county = [], 0
    with open(RAW, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("County") or "").strip() != COUNTY:
                skipped_county += 1
                continue
            street, city, zipc = split_addr(r.get("Street City State Zip"))
            try:
                units = int((r.get("Units") or "").strip() or 0)
            except ValueError:
                units = 0
            rec = (r.get("Recorded Date") or "").strip()
            year = None
            if rec:
                try:
                    year = int(rec.split("/")[-1])
                except ValueError:
                    year = None
            name = (r.get("Condo Name") or "").strip()
            rows.append((
                (r.get("Project Number") or "").strip(),
                (r.get("File Number") or "").strip(),
                name, normalize_name(name), COUNTY,
                (r.get("Street City State Zip") or "").strip(),
                building_addr(street), city, zipc,
                units, rec, year,
                (r.get("Primary Status") or "").strip(),
                (r.get("Secondary Status") or "").strip(),
                (r.get("Managing Entity Number") or "").strip(),
                (r.get("Managing Entity Name") or "").strip(),
                (r.get("Managing Entity Street") or "").strip(),
                (r.get("Managing Entity City") or "").strip(),
                (r.get("Managing Entity State") or "").strip(),
                (r.get("Managing Entity Zip") or "").strip(),
            ))

    con = connect()
    con.execute("DELETE FROM dbpr_association")
    con.executemany(
        f"INSERT OR REPLACE INTO dbpr_association VALUES ({','.join('?' * 20)})", rows)
    con.commit()

    got = con.execute("SELECT COUNT(*) FROM dbpr_association").fetchone()[0]
    units = con.execute("SELECT SUM(units) FROM dbpr_association").fetchone()[0] or 0
    with_year = con.execute(
        "SELECT COUNT(*) FROM dbpr_association WHERE recorded_year IS NOT NULL").fetchone()[0]
    with_addr = con.execute(
        "SELECT COUNT(*) FROM dbpr_association WHERE addr_norm <> ''").fetchone()[0]
    log_ingest(con, "dbpr_condo_md", started,
               datetime.now().isoformat(timespec="seconds"), got,
               f"{skipped_county} non-Dade rows skipped")
    print(f"\ndone: {got:,} Dade associations, {units:,} units")
    print(f"  with recorded_year: {with_year:,} ({100*with_year/max(got,1):.1f}%)")
    print(f"  with parsed address: {with_addr:,} ({100*with_addr/max(got,1):.1f}%)")
    con.close()


if __name__ == "__main__":
    main()
