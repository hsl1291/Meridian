"""Load the FDOR NAL tax roll (Dade, DOR_UC='004' condominium) into SQLite.

The NAL is the authoritative source for owner-per-condo-unit. It is the only
free, complete one: Miami-Dade's PaParcelView service has no condo unit folios,
the statewide FDOR FeatureServer rejects attribute queries, and the PA's own bulk
BBS is behind paid credits.

    Dade 23 Preliminary NAL 2026.zip -- 59.5 MB zipped / 466 MB CSV
    938,308 parcels total, 391,210 of them DOR_UC='004'

Re-download with --refresh; otherwise the cached zip in data/raw is reused.

    venv\\Scripts\\python.exe scripts\\prospect\\ingest_nal.py [--refresh]
"""
import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.prospect.db import connect, log_ingest  # noqa: E402
from backend.prospect.norm import (  # noqa: E402
    building_addr, is_entity, normalize_addr, normalize_owner,
)

NAL_URL = (
    "https://floridarevenue.com/property/dataportal/Documents/PTO%20Data%20Portal/"
    "Tax%20Roll%20Data%20Files/NAL/2026P/Dade%2023%20Preliminary%20NAL%202026.zip"
)
RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "dade_nal_2026p.zip"
CONDO_UC = {"004"}          # FDOR use code for condominium
BATCH = 20_000


def download(force=False):
    if RAW.exists() and not force:
        print(f"using cached {RAW.name} ({RAW.stat().st_size/1e6:.1f} MB)")
        return
    RAW.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {NAL_URL.rsplit('/', 1)[-1]} ...")
    with requests.get(NAL_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(RAW, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    print(f"  saved {RAW.stat().st_size/1e6:.1f} MB")


def rows_from_zip():
    z = zipfile.ZipFile(RAW)
    name = z.namelist()[0]
    with z.open(name) as fh:
        reader = csv.reader(io.TextIOWrapper(fh, encoding="latin-1", newline=""))
        header = next(reader)
        ix = {k: i for i, k in enumerate(header)}
        need = ["PARCEL_ID", "DOR_UC", "OWN_NAME", "OWN_ADDR1", "OWN_CITY",
                "OWN_STATE", "OWN_ZIPCD", "PHY_ADDR1", "PHY_CITY", "PHY_ZIPCD",
                "ACT_YR_BLT", "EFF_YR_BLT", "JV", "LND_VAL", "TOT_LVG_AREA",
                "SALE_PRC1", "SALE_YR1", "OR_BOOK1", "OR_PAGE1", "S_LEGAL"]
        missing = [n for n in need if n not in ix]
        if missing:
            raise SystemExit(f"NAL header is missing expected columns: {missing}")

        def g(row, key):
            v = row[ix[key]].strip().strip('"')
            return v or None

        def num(row, key):
            v = g(row, key)
            if v is None:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        def integer(row, key):
            v = num(row, key)
            return int(v) if v else None

        total = 0
        for row in reader:
            total += 1
            if len(row) <= ix["S_LEGAL"]:
                continue
            if g(row, "DOR_UC") not in CONDO_UC:
                continue
            folio = g(row, "PARCEL_ID")
            if not folio:
                continue
            owner = g(row, "OWN_NAME")
            owner_addr = g(row, "OWN_ADDR1")
            phy = g(row, "PHY_ADDR1")
            phy_norm = building_addr(phy)
            owner_addr_norm = normalize_addr(owner_addr)
            # Absentee = the tax bill goes somewhere other than the unit itself.
            # Compared on the *unit* address, not the building address, so a
            # resident owner in the same building still reads as owner-occupied.
            absentee = 1 if (owner_addr_norm and normalize_addr(phy)
                             and owner_addr_norm != normalize_addr(phy)) else 0
            yield (
                folio, folio[:9], owner, normalize_owner(owner),
                owner_addr, owner_addr_norm, g(row, "OWN_CITY"),
                g(row, "OWN_STATE"), g(row, "OWN_ZIPCD"),
                phy, phy_norm, g(row, "PHY_CITY"), g(row, "PHY_ZIPCD"),
                integer(row, "ACT_YR_BLT"), integer(row, "EFF_YR_BLT"),
                num(row, "JV"), num(row, "LND_VAL"), num(row, "TOT_LVG_AREA"),
                num(row, "SALE_PRC1"), integer(row, "SALE_YR1"),
                g(row, "OR_BOOK1"), g(row, "OR_PAGE1"), g(row, "S_LEGAL"),
                1 if is_entity(owner) else 0, absentee, COUNTY,
            )
        print(f"  scanned {total:,} NAL rows")


# Must match nal_condo_unit's column count in backend/db.py. The table carries
# a `county` column because the FDOR roll is published per county; this script
# loads Dade, so every row is stamped with it.
COUNTY = "DADE"
COLS = 26
INSERT = f"INSERT OR REPLACE INTO nal_condo_unit VALUES ({','.join('?' * COLS)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download the zip")
    args = ap.parse_args()

    download(force=args.refresh)
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.time()

    con = connect()
    # Fail loudly and immediately if the schema gained a column and this
    # script's tuple did not. Without it the mismatch only surfaces mid-load,
    # as an opaque "table has N columns but M values were supplied".
    actual = len(con.execute("PRAGMA shared.table_info(nal_condo_unit)").fetchall()) \
        or len(con.execute("PRAGMA main.table_info(nal_condo_unit)").fetchall())
    if actual != COLS:
        raise SystemExit(
            f"nal_condo_unit has {actual} columns but this script supplies {COLS}. "
            f"Update COLS and the tuple in rows_from_zip() to match backend/db.py.")
    con.execute("DELETE FROM nal_condo_unit")
    con.commit()

    batch, n = [], 0
    for row in rows_from_zip():
        batch.append(row)
        if len(batch) >= BATCH:
            con.executemany(INSERT, batch)
            n += len(batch)
            batch.clear()
            print(f"  {n:,} condo units loaded", flush=True)
    if batch:
        con.executemany(INSERT, batch)
        n += len(batch)
    con.commit()

    got = con.execute("SELECT COUNT(*) FROM nal_condo_unit").fetchone()[0]
    owners = con.execute(
        "SELECT COUNT(*) FROM nal_condo_unit WHERE owner_name IS NOT NULL").fetchone()[0]
    groups = con.execute(
        "SELECT COUNT(DISTINCT group_key) FROM nal_condo_unit").fetchone()[0]
    el = time.time() - t0
    log_ingest(con, "fdor_nal_2026p_dade", started,
               datetime.now().isoformat(timespec="seconds"), got, f"{el:.0f}s")
    print(f"\ndone in {el:.0f}s: {got:,} condo units, {groups:,} groups, "
          f"{owners:,} with owner names ({100*owners/max(got,1):.1f}%)")
    con.close()


if __name__ == "__main__":
    main()
