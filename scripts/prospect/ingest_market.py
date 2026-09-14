"""Module C — ingest the national market dataset.

Every source here is a free federal bulk file (plus Zillow's public research CSV).
Nothing is scraped, nothing needs a key, and all of it is nationwide at county
level, so any market can be selected later — South Florida is just the default view.

    crosswalk  Census delineation file      county -> CBSA (metro)
    pop        Census population estimates  components of change, incl. domestic migration
    irs        IRS SOI migration            county-to-county flows WITH AGI
    permits    Census Building Permits      annual units by county
    zhvi       Zillow ZHVI                  metro home value index, monthly
    qcew       BLS QCEW                     employment + average pay by county and NAICS

    venv\\Scripts\\python.exe scripts\\ingest_market.py --all
    venv\\Scripts\\python.exe scripts\\ingest_market.py pop irs      # just these
"""
import argparse
import csv
import io
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.prospect.db import connect, log_ingest  # noqa: E402

RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "Mozilla/5.0 (Prospect market ingest)"}

SOURCES = {
    "crosswalk": ("https://www2.census.gov/programs-surveys/metro-micro/geographies/"
                  "reference-files/2023/delineation-files/list1_2023.xlsx",
                  "cbsa_delineation_2023.xlsx"),
    "pop":       ("https://www2.census.gov/programs-surveys/popest/datasets/"
                  "2020-2024/counties/totals/co-est2024-alldata.csv",
                  "co-est2024-alldata.csv"),
    "irs_in":    ("https://www.irs.gov/pub/irs-soi/countyinflow2122.csv",
                  "countyinflow2122.csv"),
    "irs_out":   ("https://www.irs.gov/pub/irs-soi/countyoutflow2122.csv",
                  "countyoutflow2122.csv"),
    "permits":   ("https://www2.census.gov/econ/bps/County/co2024a.txt", "bps_co2024a.txt"),
    "zhvi":      ("https://files.zillowstatic.com/research/public_csvs/zhvi/"
                  "Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
                  "zillow_metro_zhvi.csv"),
}
# Two years so employment and pay GROWTH are measurable, not just levels.
QCEW_YEARS = [2021, 2024]
for _y in QCEW_YEARS:
    SOURCES[f"qcew{_y}"] = (
        f"https://data.bls.gov/cew/data/files/{_y}/csv/{_y}_annual_by_area.zip",
        f"qcew_{_y}_annual_by_area.zip")

IRS_YEAR = 2022        # countyinflow2122 = filings moving into 2022
AGG_STATES = {"57", "58", "59", "96", "97", "98"}
PERMIT_YEAR = 2024



def fetch(key, force=False):
    url, name = SOURCES[key]
    path = RAW / name
    if path.exists() and not force:
        print(f"  cached {name} ({path.stat().st_size/1e6:.1f} MB)")
        return path
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {name} ...", flush=True)
    with requests.get(url, stream=True, timeout=900, headers=UA) as r:
        r.raise_for_status()
        with open(path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    print(f"    {path.stat().st_size/1e6:.1f} MB")
    return path


def _int(v):
    try:
        s = str(v).strip().replace(",", "")
        return int(float(s)) if s not in ("", "-", ".", "N/A") else None
    except (TypeError, ValueError):
        return None


# ── crosswalk ──────────────────────────────────────────────────────────────
def do_crosswalk(con, force):
    from openpyxl import load_workbook
    path = fetch("crosswalk", force)
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows, header, hidx = [], None, {}
    for r in ws.iter_rows(values_only=True):
        cells = [("" if c is None else str(c).strip()) for c in r]
        if header is None:
            # the real header row is the one naming the CBSA code column
            if any("CBSA Code" == c for c in cells):
                header = cells
                hidx = {c: i for i, c in enumerate(cells)}
            continue
        if not cells or not cells[hidx["CBSA Code"]].isdigit():
            continue
        sf = cells[hidx["FIPS State Code"]].zfill(2)
        cf = cells[hidx["FIPS County Code"]].zfill(3)
        rows.append((sf + cf, cells[hidx["County/County Equivalent"]],
                     cells[hidx["State Name"]], sf,
                     cells[hidx["CBSA Code"]], cells[hidx["CBSA Title"]],
                     cells[hidx["Metropolitan/Micropolitan Statistical Area"]]))
    con.execute("DELETE FROM county")
    con.executemany("INSERT OR REPLACE INTO county (fips,name,state,state_fips,cbsa,"
                    "cbsa_name,cbsa_type) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    metros = con.execute("SELECT COUNT(DISTINCT cbsa) FROM county").fetchone()[0]
    print(f"  {len(rows):,} counties in {metros:,} CBSAs")
    return len(rows)


# ── population ─────────────────────────────────────────────────────────────
def do_pop(con, force):
    path = fetch("pop", force)
    rows = []
    with open(path, encoding="latin-1", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("COUNTY", "000") == "000":
                continue          # state total row
            fips = r["STATE"].zfill(2) + r["COUNTY"].zfill(3)
            for year in range(2020, 2025):
                pop = _int(r.get(f"POPESTIMATE{year}"))
                if pop is None:
                    continue
                rows.append((fips, year, pop,
                             _int(r.get(f"BIRTHS{year}")), _int(r.get(f"DEATHS{year}")),
                             _int(r.get(f"DOMESTICMIG{year}")),
                             _int(r.get(f"INTERNATIONALMIG{year}")),
                             _int(r.get(f"NATURALCHG{year}"))))
    con.execute("DELETE FROM county_pop")
    con.executemany("INSERT OR REPLACE INTO county_pop (fips,year,population,births,"
                    "deaths,domestic_mig,international_mig,natural_change) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  {len(rows):,} county-year rows")
    return len(rows)


# ── IRS migration ──────────────────────────────────────────────────────────
def _irs(con, key, direction):
    """IRS county files: y1_* is the origin, y2_* the destination for inflow;
    reversed for outflow. Rows where the state code is 96/97/98 are US/region
    aggregates, not counties, and are dropped."""
    path = fetch(key, False)
    rows = []
    with open(path, encoding="latin-1", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                y1s, y1c = r["y1_statefips"].strip(), r["y1_countyfips"].strip()
                y2s, y2c = r["y2_statefips"].strip(), r["y2_countyfips"].strip()
            except KeyError:
                continue
            # 57/58/59 are IRS roll-up pseudo-counties (total US, total foreign,
            # etc.) and 96/97/98 are region aggregates. Leaving them in double
            # counts every real flow and invents origins like "59000".
            if y1s in AGG_STATES or y2s in AGG_STATES:
                continue
            if not (y1s.isdigit() and y2s.isdigit()):
                continue
            a = y1s.zfill(2) + y1c.zfill(3)
            b = y2s.zfill(2) + y2c.zfill(3)
            if a == b:
                continue                      # non-migrant row
            ret, ex = _int(r.get("n1")), _int(r.get("n2"))
            agi = _int(r.get("agi"))
            if not ret or ret < 0:
                continue
            # inflow file: y2 is the destination. outflow file: y1 is the origin.
            dest, orig = (b, a) if direction == "in" else (a, b)
            rows.append((dest, orig, IRS_YEAR, direction, ret, ex, agi))
    con.executemany("INSERT OR REPLACE INTO migration_flow (dest_fips,origin_fips,year,"
                    "direction,returns,exemptions,agi) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def do_irs(con, force):
    con.execute("DELETE FROM migration_flow")
    con.commit()
    n_in = _irs(con, "irs_in", "in")
    n_out = _irs(con, "irs_out", "out")
    print(f"  {n_in:,} inbound flows, {n_out:,} outbound flows ({IRS_YEAR})")
    return n_in + n_out


# ── permits ────────────────────────────────────────────────────────────────
def do_permits(con, force):
    """BPS county file: two header lines, then positional columns. Units live at
    fixed offsets after the survey-date/FIPS/name block."""
    path = fetch("permits", force)
    rows, bad = [], 0
    with open(path, encoding="latin-1") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    for ln in lines[2:]:
        parts = [p.strip().strip('"') for p in ln.split(",")]
        if len(parts) < 15 or not parts[1].isdigit():
            bad += 1
            continue
        try:
            fips = parts[1].zfill(2) + parts[2].zfill(3)
            # layout: date, st, cty, region, division, name, then
            # (bldgs, units, value) x {1-unit, 2-unit, 3-4 unit, 5+ unit}
            u1 = _int(parts[7]); u2 = _int(parts[10])
            u34 = _int(parts[13]); u5 = _int(parts[16]) if len(parts) > 16 else None
        except (IndexError, ValueError):
            bad += 1
            continue
        tot = sum(v for v in (u1, u2, u34, u5) if v)
        rows.append((fips, PERMIT_YEAR, tot, u1, (u2 or 0) + (u34 or 0), u5))
    con.execute("DELETE FROM county_permits")
    con.executemany("INSERT OR REPLACE INTO county_permits (fips,year,units_total,"
                    "units_1,units_2_4,units_5plus) VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  {len(rows):,} counties ({bad} unparsed lines), {PERMIT_YEAR}")
    return len(rows)


# ── Zillow ─────────────────────────────────────────────────────────────────
def do_zhvi(con, force):
    """Zillow's metro file is keyed by its own RegionName ('Miami-Fort
    Lauderdale-West Palm Beach, FL'), not CBSA code, so it is joined to the
    Census delineation by normalized metro title."""
    path = fetch("zhvi", force)

    def norm(s):
        s = re.sub(r"[^A-Za-z0-9 ]", " ", (s or "").upper())
        return re.sub(r"\s+", " ", s).strip()

    def anchor(title):
        """Key a metro on its FIRST principal city + FIRST state.

        Zillow and Census disagree on the trailing cities: Zillow still says
        "Miami-Fort Lauderdale-West Palm Beach, FL" where the 2023 delineation
        says "Miami-Fort Lauderdale-Pompano Beach, FL". Full-title matching drops
        237 metros including Miami. The lead city and state are stable."""
        if not title:
            return ""
        name, _, st = title.partition(",")
        city = re.split(r"[-/]", name)[0]
        state = re.split(r"[-/]", st.strip())[0]
        return f"{norm(city)}|{norm(state)}"

    lookup, anchors = {}, {}
    for r in con.execute("SELECT DISTINCT cbsa, cbsa_name FROM county"):
        lookup[norm(r["cbsa_name"])] = r["cbsa"]
        a = anchor(r["cbsa_name"])
        # Ambiguous anchors are dropped rather than guessed at.
        anchors[a] = None if a in anchors and anchors[a] != r["cbsa"] else r["cbsa"]

    rows, matched, unmatched = [], set(), 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        months = [(i, h) for i, h in enumerate(header) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", h)]
        name_i = header.index("RegionName")
        for row in reader:
            cbsa = lookup.get(norm(row[name_i])) or anchors.get(anchor(row[name_i]))
            if not cbsa:
                unmatched += 1
                continue
            matched.add(cbsa)
            for i, m in months[-48:]:      # last 4 years is all the scoring needs
                try:
                    v = float(row[i])
                except (ValueError, IndexError):
                    continue
                rows.append((cbsa, m[:7], round(v, 2)))
    con.execute("DELETE FROM metro_price")
    con.executemany("INSERT OR REPLACE INTO metro_price (cbsa,month,zhvi) VALUES (?,?,?)", rows)
    con.commit()
    print(f"  {len(rows):,} metro-month prices across {len(matched):,} CBSAs "
          f"({unmatched} Zillow regions unmatched)")
    return len(rows)


# ── QCEW ───────────────────────────────────────────────────────────────────
def do_qcew(con, force):
    con.execute("DELETE FROM county_wage")
    con.commit()
    total = 0
    for y in QCEW_YEARS:
        total += _qcew_year(con, y, force)
    return total


def _qcew_year(con, year, force):
    """QCEW 'annual by area' zip holds one CSV per area. Keep private-sector
    (own_code 5) county rows at NAICS-sector level (agglvl 74) plus the county
    total (agglvl 70), which is what the market-level average pay comes from."""
    path = fetch(f"qcew{year}", force)
    keep_agg = {"70", "74"}
    rows, files = [], 0
    t0 = time.time()
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        for n in names:
            files += 1
            with z.open(n) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline="")):
                    agg = (r.get("agglvl_code") or "").strip()
                    if agg not in keep_agg:
                        continue
                    own = (r.get("own_code") or "").strip()
                    if agg == "74" and own != "5":
                        continue
                    if agg == "70" and own != "0":
                        continue
                    area = (r.get("area_fips") or "").strip()
                    if len(area) != 5 or not area.isdigit():
                        continue
                    emp = _int(r.get("annual_avg_emplvl"))
                    pay = _int(r.get("avg_annual_pay"))
                    if emp is None:
                        continue
                    naics = (r.get("industry_code") or "").strip() or "TOTAL"
                    if agg == "70":
                        naics = "TOTAL"
                    rows.append((area, year, naics,
                                 (r.get("industry_title") or "").strip(), emp, pay))
            if files % 1500 == 0:
                print(f"    {year}: {files}/{len(names)} area files", flush=True)
    con.executemany("INSERT OR REPLACE INTO county_wage (fips,year,naics,industry,"
                    "employment,avg_annual_pay) VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  {year}: {len(rows):,} county-industry rows from {files} area files "
          f"({time.time()-t0:.0f}s)")
    return len(rows)


STEPS = {"crosswalk": do_crosswalk, "pop": do_pop, "irs": do_irs,
         "permits": do_permits, "zhvi": do_zhvi, "qcew": do_qcew}
ORDER = ["crosswalk", "pop", "irs", "permits", "zhvi", "qcew"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="*", choices=ORDER, help="default: all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download cached files")
    args = ap.parse_args()
    steps = args.steps if args.steps and not args.all else ORDER

    con = connect()
    try:
        for s in steps:
            print(f"\n[{s}]")
            started = datetime.now().isoformat(timespec="seconds")
            n = STEPS[s](con, args.refresh)
            log_ingest(con, f"market_{s}", started,
                       datetime.now().isoformat(timespec="seconds"), n, "")
    finally:
        con.close()
    print("\nnow run: scripts\\build_markets.py")


if __name__ == "__main__":
    main()
