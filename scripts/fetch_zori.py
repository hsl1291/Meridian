"""Build data/zori_rents.json — Zillow Observed Rent Index by ZIP and metro.

ZORI is Zillow's repeat-rent index: the typical asking rent for the whole
market, smoothed and seasonally adjusted, published monthly as a free public
CSV with no key or account. It is the rent side of the map's area-rents layer
and the benchmark the parcel panel quotes.

    venv\\Scripts\\python.exe scripts\\fetch_zori.py

Writes:
    {"asof": "2026-06",
     "attribution": "Data: Zillow Research (ZORI)",
     "zip":   {"33127": {"rent": 2450, "yoy": 4.2, "city": "Miami",
                          "state": "FL", "metro": "Miami-Fort Lauderdale…"}},
     "metro": {"Miami-Fort Lauderdale, FL": {"rent": 2810, "yoy": 3.1}}}

Re-run any time; Zillow refreshes around the middle of each month.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "zori_rents.json"

BASE = "https://files.zillowstatic.com/research/public_csvs/zori"
# "uc_sfrcondomfr_sm_month" = all rental housing types (SFR + condo + multifamily),
# smoothed, monthly. The broadest series Zillow publishes, which is what you want
# as a market benchmark rather than a single-product index.
ZIP_URL = f"{BASE}/Zip_zori_uc_sfrcondomfr_sm_month.csv"
METRO_URL = f"{BASE}/Metro_zori_uc_sfrcondomfr_sm_month.csv"

# Non-month columns vary slightly between the two files; anything that parses as
# a YYYY-MM-DD header is a data month.
def _month_cols(header: list[str]) -> list[str]:
    return [h for h in header if len(h) == 10 and h[4] == "-" and h[7] == "-"]


def _series(row: dict, months: list[str]) -> tuple[float | None, float | None, str | None]:
    """Latest value, year-over-year %, and the month it came from.

    Trailing months are often blank for thinner ZIPs, so walk back from the end
    to the last populated month rather than assuming the final column is filled.
    """
    vals = [(m, row.get(m)) for m in months]
    latest = None
    for m, v in reversed(vals):
        if v not in (None, "", "NA"):
            try:
                latest = (m, float(v))
            except ValueError:
                continue
            break
    if latest is None:
        return None, None, None
    m_latest, rent = latest
    # Same month one year earlier, if present.
    yoy = None
    try:
        idx = months.index(m_latest)
    except ValueError:
        idx = -1
    if idx >= 12:
        prior = row.get(months[idx - 12])
        if prior not in (None, "", "NA"):
            try:
                p = float(prior)
                if p > 0:
                    yoy = round(100 * (rent - p) / p, 1)
            except ValueError:
                pass
    return round(rent), yoy, m_latest[:7]


def fetch(client: httpx.Client, url: str) -> tuple[list[dict], list[str]]:
    print(f"  {url}")
    r = client.get(url, timeout=180.0, follow_redirects=True)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows:
        raise SystemExit(f"empty CSV at {url}")
    return rows, _month_cols(list(rows[0].keys()))


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out: dict = {"attribution": "Data: Zillow Research (ZORI)", "zip": {}, "metro": {}}
    asof = None

    with httpx.Client(headers={"User-Agent": "meridian/1.0"}) as client:
        print("ZIP-level ZORI:")
        rows, months = fetch(client, ZIP_URL)
        for row in rows:
            z = (row.get("RegionName") or "").strip()
            if len(z) != 5 or not z.isdigit():
                continue
            rent, yoy, m = _series(row, months)
            if rent is None:
                continue
            asof = max(asof or m, m)
            rec = {"rent": rent}
            if yoy is not None:
                rec["yoy"] = yoy
            for key, col in (("city", "City"), ("state", "State"), ("metro", "Metro")):
                v = (row.get(col) or "").strip()
                if v:
                    rec[key] = v
            out["zip"][z] = rec
        print(f"  {len(out['zip'])} ZIPs")

        print("Metro-level ZORI:")
        rows, months = fetch(client, METRO_URL)
        for row in rows:
            name = (row.get("RegionName") or "").strip()
            if not name:
                continue
            rent, yoy, m = _series(row, months)
            if rent is None:
                continue
            asof = max(asof or m, m)
            rec = {"rent": rent}
            if yoy is not None:
                rec["yoy"] = yoy
            st = (row.get("StateName") or "").strip()
            if st:
                rec["state"] = st
            out["metro"][name] = rec
        print(f"  {len(out['metro'])} metros")

    out["asof"] = asof
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    size_mb = OUT.stat().st_size / 1e6
    print(f"\nwrote {OUT}  ({size_mb:.1f} MB, as of {asof})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
