"""One-off/refresh: build data/zcta_pop.json for the population-growth layer.

Pulls total population (ACS 5-year, B01003_001E) for every ZCTA for each
available vintage, plus ZCTA internal-point centroids from the Census
Gazetteer, and writes one compact JSON the frontend loads lazily:

    {"years": [2012, ..., 2023],
     "zctas": {"33127": {"lat": 25.81, "lon": -80.20, "pop": [p2012, ..., p2023]}, ...}}

pop entries are null where a vintage has no value for that ZCTA.
Run with the app venv:  venv\\Scripts\\python.exe scripts\\fetch_zcta_population.py
Re-run any year to refresh (new ACS vintages appear each December)."""
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "zcta_pop.json"

# ACS 5-year vintages with ZCTA-level B01003. 2011's ZCTA support is flaky —
# start at 2012 (period 2008-2012) which is plenty of runway for a growth slider.
YEARS = list(range(2012, 2024))
GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_zcta_national.zip"


def fetch_gazetteer(client: httpx.Client) -> dict[str, tuple[float, float]]:
    print(f"gazetteer: {GAZ_URL}")
    r = client.get(GAZ_URL, timeout=120.0)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    name = zf.namelist()[0]
    out: dict[str, tuple[float, float]] = {}
    with zf.open(name) as fh:
        rdr = csv.DictReader(io.TextIOWrapper(fh, "utf-8-sig"), delimiter="\t")
        # Gazetteer headers carry trailing whitespace — normalize.
        for row in rdr:
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            zcta = row.get("GEOID")
            try:
                out[zcta] = (round(float(row["INTPTLAT"]), 4), round(float(row["INTPTLONG"]), 4))
            except (KeyError, TypeError, ValueError):
                continue
    print(f"  {len(out)} ZCTA centroids")
    return out


def fetch_year(client: httpx.Client, year: int) -> dict[str, int | None]:
    # api.census.gov now requires an API key; data.census.gov's own backend
    # (cedsci) serves the same detailed tables keyless.
    url = "https://data.census.gov/api/access/data/table"
    params = {"g": "010XX00US$8600000", "id": f"ACSDT5Y{year}.B01003"}
    r = client.get(url, params=params, timeout=180.0)
    r.raise_for_status()
    rows = (r.json().get("response") or {}).get("data") or []
    head = rows[0]
    i_pop = head.index("B01003_001E")
    i_geo = head.index("GEO_ID")
    out: dict[str, int | None] = {}
    for row in rows[1:]:
        # GEO_ID is '8600000US33127' (older vintages) or '860Z200US33127' —
        # the ZCTA is whatever follows 'US'.
        geo = str(row[i_geo])
        zcta = geo.split("US", 1)[-1]
        try:
            out[zcta] = int(row[i_pop]) if row[i_pop] not in (None, "", "null") else None
        except (TypeError, ValueError):
            out[zcta] = None
    return out


def main() -> int:
    with httpx.Client(follow_redirects=True) as client:
        centroids = fetch_gazetteer(client)
        series: dict[int, dict] = {}
        for y in YEARS:
            try:
                series[y] = fetch_year(client, y)
                print(f"acs5 {y}: {len(series[y])} ZCTAs")
            except (httpx.HTTPError, ValueError, IndexError) as e:
                print(f"acs5 {y}: FAILED ({e}) — skipping vintage")
    years = sorted(series)
    if len(years) < 2:
        print("not enough vintages fetched — aborting without writing")
        return 1

    zctas = {}
    for z, (lat, lon) in centroids.items():
        pops = [series[y].get(z) for y in years]
        if not any(p for p in pops):
            continue
        zctas[z] = {"lat": lat, "lon": lon, "pop": pops}
    OUT.write_text(json.dumps({"years": years, "zctas": zctas}, separators=(",", ":")))
    print(f"wrote {OUT} — {len(zctas)} ZCTAs x {len(years)} vintages, {OUT.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
