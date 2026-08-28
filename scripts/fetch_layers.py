"""Download polygon layers from county ArcGIS REST services for South Florida.

Python port of the old fetch-layers.js — same layers, same output files, no
Node dependency. Uses httpx, which is already in requirements.txt.

    venv\\Scripts\\python.exe scripts\\fetch_layers.py            fetch missing layers
    venv\\Scripts\\python.exe scripts\\fetch_layers.py --force    re-fetch everything
    venv\\Scripts\\python.exe scripts\\fetch_layers.py --only mdc_zoning,bro_flu
    venv\\Scripts\\python.exe scripts\\fetch_layers.py --list     show layers and exit

Tri-county scope: Miami-Dade (full), Broward (FLU + BMSD zoning + munis + flood).
Parcels are not downloaded — too large; queried on demand via /api/parcel.

The server builds /data/manifest.json by globbing this directory, so whatever
lands here shows up in the map's layer list automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

MDC_ROOT = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services"
BRO_ROOT = "https://services.arcgis.com/JMAJrTsHNLrSsWf5/arcgis/rest/services"

# key, service root, service name, layer index, outFields, simplify tolerance
LAYERS: list[dict] = [
    # ---- Miami-Dade --------------------------------------------------------
    {"key": "mdc_zoning", "root": MDC_ROOT, "name": "MunicipalZone_gdb", "layer": 0,
     "fields": "OBJECTID,MUNICNAME,ZONE,ZONEDESC,SHORTDESC,MINLOTSIZE,DENSITY,FAR,"
               "MAXHEIGHT,MIXUSE,GENRLLUTYPE,MAXLOTCOV"},
    {"key": "mdc_flu", "root": MDC_ROOT, "name": "MunicipalFutureLandUse_gdb", "layer": 0,
     "fields": "OBJECTID,MUNICNAME,FLUCODE,FLUDESC,DENSITY,GENLUTYPE"},
    {"key": "mdc_flood", "root": MDC_ROOT, "name": "FEMAFloodZone_gdb", "layer": 0,
     "fields": "OBJECTID,FZONE,ZONESUBTY,ELEV", "simplify": 0.0002},
    {"key": "mdc_historic_districts", "root": MDC_ROOT, "name": "HistoricDistrict_gdb",
     "layer": 0, "fields": "*"},
    {"key": "mdc_metrorail_stations", "root": MDC_ROOT, "name": "MetroRailStations_gdb",
     "layer": 0, "fields": "*"},
    {"key": "mdc_metromover_stations", "root": MDC_ROOT, "name": "MetroMoverStations_gdb",
     "layer": 0, "fields": "*"},
    {"key": "mdc_rapid_transit_zones", "root": MDC_ROOT, "name": "RapidTransitZone_gdb",
     "layer": 0, "fields": "*"},
    {"key": "mdc_municipalities", "root": MDC_ROOT, "name": "Municipalitypoly_gdb",
     "layer": 0, "fields": "OBJECTID,NAME,MUNICID"},
    {"key": "mdc_opportunity_zones", "root": MDC_ROOT, "name": "QualifiedOpportunityZones_gdb",
     "layer": 0, "fields": "OBJECTID,GEOID10,NAME10,NAMELSAD10,POP100"},
    {"key": "mdc_brownfields", "root": MDC_ROOT, "name": "Brownfield_gdb", "layer": 0,
     "fields": "OBJECTID,NAME,SITEID,SITENAME,JURISDTN,ACRE"},
    {"key": "mdc_cra", "root": MDC_ROOT, "name": "CommunityRedevelopmentArea_gdb",
     "layer": 0, "fields": "OBJECTID,LOCATION,ACRE"},
    {"key": "mdc_enterprise_zones", "root": MDC_ROOT, "name": "EnterpriseZone_gdb",
     "layer": 0, "fields": "*"},
    {"key": "mdc_schools", "root": MDC_ROOT, "name": "SchoolSite_gdb", "layer": 0,
     "fields": "OBJECTID,NAME,ADDRESS,CITY,ZIPCODE,TYPE,GRADES,ENROLLMNT,LAT,LON"},
    {"key": "mdc_parks", "root": MDC_ROOT, "name": "MunicipalParkBoundary_gdb", "layer": 0,
     "fields": "OBJECTID,NAME,ADDRESS,CITY,CLASS,TOTACRE,TYPE,MNGTAGCY"},

    # ---- Broward -----------------------------------------------------------
    {"key": "bro_zoning_bmsd", "root": BRO_ROOT,
     "name": "Broward_Municipal_Service_District_Zoning", "layer": 2,
     "fields": "OBJECTID,ZONING,DESCRIPTION,AREANAME,BCPA_USE,USE_CODE,SITUSADD,FOLIO,ACRES"},
    {"key": "bro_flu", "root": BRO_ROOT, "name": "FutureLandUse", "layer": 0,
     "fields": "OBJECTID,BPC_BCFUTU,SLUC1,ACRES,DENSITY"},
    # This layer's object-ID field is FID, not OBJECTID — asking for OBJECTID
    # makes ArcGIS reject the whole outFields list (the old fetch-layers.js had
    # this wrong, so the layer never downloaded).
    {"key": "bro_municipalities", "root": BRO_ROOT,
     "name": "Municipality_Boundaries_with_BMSD", "layer": 0, "fields": "FID,CITYNAME"},
    {"key": "bro_flood", "root": BRO_ROOT, "name": "FEMAFloodZones2024", "layer": 0,
     "fields": "OBJECTID,FLD_ZONE,ZONE_SUBTY,STATIC_BFE", "simplify": 0.0002},
]

def _shared_root() -> Path:
    r"""Same resolution order the apps use (see backend/shared_data.py):
      1. APPS_SHARED env var
      2. a `_shared` folder beside the app folder   <- the shareable layout
      3. C:\Apps\_shared                            <- the original install
    """
    env = os.environ.get("APPS_SHARED")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parent.parent.parent / "_shared"
    if sibling.is_dir():
        return sibling
    return Path(r"C:\Apps\_shared")


# These polygons are shared: Sitefolio draws them, Prospect resolves zoning
# against them for development capacity. Writing them into Sitefolio's own
# data/ would leave Prospect with nothing to read.
OUT_DIR = Path(os.environ.get("APPS_SHARED_LAYERS") or _shared_root() / "layers")
PAGE = 2000
RETRIES = 3
TIMEOUT = 90.0


def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    """GET with retry. ArcGIS returns 200 with an {"error": ...} body on failure,
    so a clean status code is not on its own proof the query worked."""
    last = None
    for attempt in range(RETRIES):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(str(data["error"])[:120])
            return data
        except Exception as e:  # noqa: BLE001 - retry anything transient
            last = e
            if attempt < RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")


def fetch_layer(client: httpx.Client, spec: dict) -> bool:
    key = spec["key"]
    base = f"{spec['root']}/{spec['name']}/FeatureServer/{spec.get('layer', 0)}"

    try:
        meta = _get_json(client, f"{base}/query",
                         {"where": "1=1", "returnCountOnly": "true", "f": "json"})
        count = meta.get("count")
        if not isinstance(count, int):
            raise RuntimeError("no count in response")
    except Exception as e:  # noqa: BLE001
        print(f"  {key}: SKIP (count failed: {e})")
        return False

    print(f"  {key}: {count:,} features ", end="", flush=True)

    params = {
        "where": "1=1",
        "outFields": spec["fields"],
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": str(PAGE),
    }
    if spec.get("simplify"):
        params["maxAllowableOffset"] = str(spec["simplify"])

    features: list[dict] = []
    offset = 0
    while offset < count:
        try:
            page = _get_json(client, f"{base}/query", {**params, "resultOffset": str(offset)})
        except Exception as e:  # noqa: BLE001
            print(f" FAIL @ offset {offset}: {e}")
            return False

        got = page.get("features")
        if got is None:
            print(f" no features key ({json.dumps(page)[:100]})")
            return False
        if not got:
            break

        features.extend(got)
        offset += len(got)
        print(".", end="", flush=True)

        # Do NOT stop just because a page came back short. Several of these
        # services cap pages below our requested size (mdc_flood serves 100 at
        # a time), so a short page is normal mid-run — only an empty one means
        # the end. offset always advances here, so the loop still terminates.

    # Write via a temp file so an interrupted run cannot leave a truncated
    # .geojson behind that the map would happily load as authoritative.
    out = OUT_DIR / f"{key}.geojson"
    tmp = out.with_suffix(".geojson.tmp")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": features}),
                   encoding="utf-8")
    tmp.replace(out)
    print(f" wrote {len(features):,} -> {out.name} ({out.stat().st_size/1024:,.0f} KB)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Sitefolio map layers from ArcGIS.")
    ap.add_argument("--force", action="store_true", help="re-fetch layers that already exist")
    ap.add_argument("--only", help="comma-separated layer keys to fetch")
    ap.add_argument("--list", action="store_true", help="list layer keys and exit")
    args = ap.parse_args()

    if args.list:
        for s in LAYERS:
            print(f"  {s['key']:26} {s['name']}")
        return 0

    todo = LAYERS
    if args.only:
        want = {k.strip() for k in args.only.split(",") if k.strip()}
        unknown = want - {s["key"] for s in LAYERS}
        if unknown:
            print(f"unknown layer key(s): {', '.join(sorted(unknown))}")
            return 1
        todo = [s for s in LAYERS if s["key"] in want]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.force:
        skipped = [s["key"] for s in todo if (OUT_DIR / f"{s['key']}.geojson").exists()]
        todo = [s for s in todo if not (OUT_DIR / f"{s['key']}.geojson").exists()]
        if skipped:
            print(f"{len(skipped)} already present (use --force to re-fetch): "
                  f"{', '.join(skipped)}")

    if not todo:
        print("Nothing to fetch.")
        return 0

    print(f"Fetching {len(todo)} layer(s) into {OUT_DIR} ...")
    t0 = time.time()
    ok = 0
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "Sitefolio/1.0 (+layer-fetch)"}) as client:
        for spec in todo:
            try:
                ok += bool(fetch_layer(client, spec))
            except Exception as e:  # noqa: BLE001 - one bad layer must not stop the rest
                print(f"  ! {spec['key']} failed: {type(e).__name__}: {str(e)[:100]}")

    failed = len(todo) - ok
    print(f"\nDone in {time.time()-t0:.0f}s - {ok} fetched, {failed} failed/skipped.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
