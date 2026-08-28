"""Build data/cbsa.geojson — metro and micropolitan boundaries for the trends map.

    venv\\Scripts\\python.exe scripts\\fetch_cbsa_geo.py
    venv\\Scripts\\python.exe scripts\\fetch_cbsa_geo.py --force     re-download
    venv\\Scripts\\python.exe scripts\\fetch_cbsa_geo.py --detail 0.01

Source is the Census TIGERweb map service, which serves the same boundaries as
the TIGER shapefiles but as GeoJSON, so nothing here needs a shapefile reader.

Geometry is generalised server-side. The default tolerance (0.02 degrees, very
roughly 2 km) is chosen for a map that never zooms past a metro outline — it
keeps the whole country near 400 KB. Nothing in this file is used for
measurement, only for shading, so the loss of precision costs nothing. Pass
--detail for a finer trade if you want it.

Only the join key and the name are kept. Every number on the map comes from the
`market` table at request time, so the metrics can be rebuilt without
re-downloading geometry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cbsa.geojson"

SERVICE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
           "tigerWMS_Current/MapServer")
# (layer id, label). 93 = metropolitan statistical areas, 91 = micropolitan.
LAYERS = [(93, "metro"), (91, "micro")]
PAGE = 150


def fetch_layer(client: httpx.Client, layer: int, kind: str, detail: float) -> list[dict]:
    base = f"{SERVICE}/{layer}/query"
    total = client.get(base, params={"where": "1=1", "returnCountOnly": "true",
                                     "f": "json"}).json().get("count", 0)
    print(f"  layer {layer} ({kind}): {total} areas", flush=True)

    feats: list[dict] = []
    for offset in range(0, total, PAGE):
        r = client.get(base, params={
            "where": "1=1", "outFields": "GEOID,NAME,CENTLAT,CENTLON",
            "maxAllowableOffset": detail, "geometryPrecision": 5,
            "resultOffset": offset, "resultRecordCount": PAGE,
            "outSR": 4326, "f": "geojson",
        })
        r.raise_for_status()
        got = r.json().get("features") or []
        for f in got:
            pr = f.get("properties") or {}
            geoid = pr.get("GEOID")
            if not geoid or not f.get("geometry"):
                continue
            try:
                lat, lon = float(pr.get("CENTLAT")), float(pr.get("CENTLON"))
            except (TypeError, ValueError):
                lat = lon = None
            feats.append({
                "type": "Feature",
                "id": int(geoid),          # numeric id: MapLibre feature-state needs it
                "geometry": f["geometry"],
                "properties": {"cbsa": geoid, "name": pr.get("NAME") or "",
                               "kind": kind, "lat": lat, "lon": lon},
            })
        print(f"    {min(offset + PAGE, total)}/{total}", end="\r", flush=True)
    print(f"    {len(feats)} with geometry      ")
    return feats


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch CBSA boundaries.")
    ap.add_argument("--force", action="store_true", help="re-download if present")
    ap.add_argument("--detail", type=float, default=0.02,
                    help="generalisation tolerance in degrees (smaller = finer)")
    args = ap.parse_args()

    if OUT.exists() and not args.force:
        mb = OUT.stat().st_size / 1e6
        print(f"{OUT.name} already present ({mb:.1f} MB) — use --force to re-download.")
        return 0

    print("Fetching CBSA boundaries from Census TIGERweb")
    feats: list[dict] = []
    with httpx.Client(timeout=120, headers={"User-Agent": "Groundwork/1.0"}) as client:
        for layer, kind in LAYERS:
            try:
                feats += fetch_layer(client, layer, kind, args.detail)
            except Exception as exc:                       # noqa: BLE001
                print(f"  layer {layer} failed: {type(exc).__name__}: {exc}")

    if not feats:
        print("Nothing downloaded — leaving any existing file alone.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                              separators=(",", ":")), encoding="utf-8")
    mb = OUT.stat().st_size / 1e6
    print(f"\nwrote {OUT}  ({mb:.1f} MB, {len(feats)} areas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
