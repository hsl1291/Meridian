"""Pull the whole Miami-Dade Property Appraiser parcel view into SQLite.

Why the whole table and not a filtered query: the service's own condo markers are
unusable. CONDO_FLAG is populated on 91 of 595,849 rows, and DOR_CODE_CUR='0407'
("CONDOMINIUM - RESIDENTIAL") returns 21 rows, one of which is a single-family
lot. Server-side filtering here would silently drop most of the county's condos.
So we pull everything once (~4 min) and classify locally in build_targets.py,
where the heuristic is visible and re-runnable.

Paging is by OBJECTID window, not resultOffset. Offset paging on this service
costs ~21s/page because the server rescans; an indexed OBJECTID range is ~0.8s.

    venv\\Scripts\\python.exe scripts\\ingest_pa.py
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.prospect.db import connect, log_ingest  # noqa: E402

QUERY = ("https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services/"
         "PaParcelView_gdb/FeatureServer/0/query")

FIELDS = [
    "OBJECTID", "FOLIO", "SUBDIVISION", "TRUE_OWNER1", "TRUE_MAILING_ADDR1",
    "TRUE_MAILING_CITY", "TRUE_MAILING_STATE", "TRUE_MAILING_ZIP_CODE",
    "YEAR_BUILT", "ASSESSED_VAL_CUR", "LOT_SIZE", "TRUE_SITE_ADDR",
    "TRUE_SITE_UNIT", "DOR_CODE_CUR", "LEGAL", "DOS_1", "PRICE_1",
    "X_COORD", "Y_COORD",
]

PAGE = 2000
WORKERS = 6


def oid_range(session):
    r = session.get(QUERY, params={
        "where": "1=1", "f": "json", "returnGeometry": "false",
        "outStatistics": (
            '[{"statisticType":"min","onStatisticField":"OBJECTID","outStatisticFieldName":"mn"},'
            '{"statisticType":"max","onStatisticField":"OBJECTID","outStatisticFieldName":"mx"}]'
        ),
    }, timeout=60)
    r.raise_for_status()
    a = r.json()["features"][0]["attributes"]
    return int(a["mn"]), int(a["mx"])


def fetch_window(session, lo, hi, attempt=0):
    try:
        r = session.get(QUERY, params={
            "where": f"OBJECTID>={lo} AND OBJECTID<{hi}",
            "outFields": ",".join(FIELDS),
            "returnGeometry": "false",
            "resultRecordCount": PAGE,
            "f": "json",
        }, timeout=120)
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            raise RuntimeError(d["error"])
        return [f["attributes"] for f in d.get("features", [])]
    except Exception as exc:
        if attempt >= 4:
            print(f"  !! window {lo}-{hi} failed after 5 tries: {exc}", flush=True)
            return []
        time.sleep(2 ** attempt)
        return fetch_window(session, lo, hi, attempt + 1)


def to_row(a):
    def s(k):
        v = a.get(k)
        return None if v is None else str(v).strip() or None

    def n(k):
        v = a.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    folio = s("FOLIO")
    if not folio:
        return None
    # DOS_1 is an epoch-ms date from ArcGIS; keep it as ISO or leave null.
    dos = a.get("DOS_1")
    sale_date = None
    if isinstance(dos, (int, float)) and dos:
        try:
            sale_date = datetime.fromtimestamp(dos / 1000, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            sale_date = None
    elif dos:
        sale_date = str(dos).strip() or None

    yb = n("YEAR_BUILT")
    return (
        folio,
        s("SUBDIVISION") or folio[:9],
        s("TRUE_OWNER1"),
        s("TRUE_MAILING_ADDR1"),
        s("TRUE_MAILING_CITY"),
        s("TRUE_MAILING_STATE"),
        s("TRUE_MAILING_ZIP_CODE"),
        int(yb) if yb else None,
        n("ASSESSED_VAL_CUR"),
        n("LOT_SIZE"),
        s("TRUE_SITE_ADDR"),
        s("TRUE_SITE_UNIT"),
        s("DOR_CODE_CUR"),
        s("LEGAL"),
        sale_date,
        n("PRICE_1"),
        n("X_COORD"),
        n("Y_COORD"),
    )


def main():
    started = datetime.now().isoformat(timespec="seconds")
    session = requests.Session()
    lo, hi = oid_range(session)
    windows = [(x, min(x + PAGE, hi + 1)) for x in range(lo, hi + 1, PAGE)]
    print(f"OBJECTID {lo}..{hi} -> {len(windows)} windows of {PAGE}", flush=True)

    con = connect()
    con.execute("DELETE FROM pa_parcel")
    con.commit()

    total = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_window, session, a, b) for a, b in windows]
        for i, fut in enumerate(futures, 1):
            rows = [r for r in (to_row(a) for a in fut.result()) if r]
            if rows:
                con.executemany(
                    "INSERT OR REPLACE INTO pa_parcel VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                total += len(rows)
            if i % 25 == 0 or i == len(windows):
                con.commit()
                el = time.time() - t0
                print(f"  {i}/{len(windows)} windows | {total:,} rows | {el:.0f}s", flush=True)
    con.commit()

    finished = datetime.now().isoformat(timespec="seconds")
    log_ingest(con, "mdc_pa_parcelview", started, finished, total,
               f"{len(windows)} windows, {time.time()-t0:.0f}s")
    got = con.execute("SELECT COUNT(*) FROM pa_parcel").fetchone()[0]
    print(f"\ndone: {got:,} parcels in pa_parcel (expected ~{hi:,})")
    if got < hi * 0.98:
        print(f"WARNING: {hi-got:,} rows missing — some windows failed. Re-run to fill.")
    con.close()


if __name__ == "__main__":
    main()
