"""
FastAPI app for the South Florida Zoning Map.

Responsibilities:
- Serve the static frontend from /
- Serve pre-baked GeoJSON layers from /data/<name>.geojson (gzipped via middleware)
- Multi-county routing for /api/parcel and /api/zoning (Miami-Dade + Broward)
- Miami-Dade Property Appraiser proxy: full sales history, owners, assessment trend
- Live Local Act helper: find tallest currently-allowed height within 1 mile
- Underwriting helpers: nearest transit, comps from comps-aggregator
"""
from __future__ import annotations

import asyncio
from itertools import zip_longest
import contextlib
import difflib
import json
import re
import html as _html
import math
import sqlite3
import time
from pathlib import Path

from .shared_paths import APP_ROOT, shared_layers

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .florida_lookups import county_name, use_description
from . import shared_data

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# Polygon layers (zoning, FLU, transit, incentive districts) are facts about the
# world that Prospect needs too, so they live in the shared store alongside
# shared.db rather than inside either app. App-private state (marks.db,
# geom_cache.db, the harvested registries) stays in DATA_DIR.
# Falls back to DATA_DIR so an install that never ran the mover still works.
import os as _os



LAYERS_DIR = shared_layers()


def layer_path(name: str) -> Path:
    """Shared layers first, then this app's own data dir."""
    p = LAYERS_DIR / name
    return p if p.exists() else DATA_DIR / name
FRONTEND_DIR = ROOT / "frontend"
COMPS_DB = (ROOT / ".." / "comps-aggregator" / "data" / "comps.db").resolve()

MDC_ROOT = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services"
BRO_ROOT = "https://services.arcgis.com/JMAJrTsHNLrSsWf5/arcgis/rest/services"
PA_PROXY = "https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx"

# Statewide FDOR cadastral (10.8M parcels, all 67 counties) — FGIO public service.
FL_CADASTRAL = ("https://services9.arcgis.com/Gh9awoU677aKree0/ArcGIS/rest/services/"
                "Florida_Statewide_Cadastral/FeatureServer/0")
# FEMA National Flood Hazard Layer — Flood Hazard Zones (layer 28), national coverage.
FEMA_NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"

# Metro zoning adapters — verified-in-Florida services, keyed by DOR county number.
# Each county maps to a LIST of adapters tried in order (city cores first, county
# unincorporated services last). Every entry was verified with a downtown point query
# before being wired — public catalogs are full of same-named services from other
# states. A city's zoning only covers the incorporated city, so a miss falls through
# to the next adapter, then to the DOR use code.
# ArcGIS reprojects an inSR=4326 query point automatically, so native SR is irrelevant.
METRO_ZONING = {
    58: [  # Orange County
        {
            "muni": "City of Orlando",
            "url": "https://services5.arcgis.com/mMuoPCaIYD4wEgDl/arcgis/rest/services/OrlandoLUZoning/FeatureServer/0",
            "code": "Zoning", "desc": None,
            "extra": {"PD_NAME": "Planned development"},
        },
        {
            "muni": "Orange County (unincorporated)",
            "url": "https://ocgis4.ocfl.net/arcgis/rest/services/AGOL_Open_Data/MapServer/51",
            "code": "ZONING", "desc": None,
        },
    ],
    39: [  # Hillsborough County
        {
            "muni": "City of Tampa",
            "url": "https://arcgis.tampagov.net/arcgis/rest/services/OpenData/Planning/MapServer/28",
            "code": "ZONECLASS", "desc": "ZONEDESC", "height": "HEIGHT",
        },
        {
            "muni": "Hillsborough County (unincorporated)",
            "url": "https://maps.hillsboroughcounty.org/arcgis/rest/services/DSD_Viewer_Services/DSD_Viewer_Zoning_Regulatory/FeatureServer/0",
            "code": "NZONE", "desc": "NZONE_DESC",
        },
    ],
    26: [  # Duval County — Jacksonville is consolidated city-county
        {
            "muni": "City of Jacksonville",
            "url": "https://services5.arcgis.com/jgLNfcs6nbSXketL/arcgis/rest/services/CoJ_Zoning/FeatureServer/0",
            "code": "LABEL", "desc": None,
            "extra": {"ORDINANCE_": "Ordinance"},
        },
    ],
    62: [  # Pinellas County
        {
            "muni": "City of St. Petersburg",
            "url": "https://services2.arcgis.com/9qPLjNtocjo438CJ/arcgis/rest/services/ZoningDistricts_view/FeatureServer/0",
            "code": "ZONECLASS", "desc": "ZONEDESC",
        },
        {
            "muni": "City of Clearwater",
            "url": "https://gis.myclearwater.com/arcgis/rest/services/EconomicDevelopment/Zoning/MapServer/0",
            "code": "ZONING", "desc": "ZONING_DESC",
        },
    ],
    68: [  # Sarasota County
        {
            "muni": "City of Sarasota",
            "url": "https://services3.arcgis.com/AWDwYUpli8WqpWxQ/arcgis/rest/services/Zoning_Districts_(View_Only)/FeatureServer/0",
            "code": "ZONECLASS", "desc": "ZONEDESC",
        },
        {
            "muni": "Sarasota County (unincorporated)",
            "url": "https://ags3.scgov.net/server/rest/services/Hosted/CountyZoning/FeatureServer/0",
            "code": "zoningdesignation", "desc": None,
        },
    ],
    47: [  # Leon County — Tallahassee-Leon consolidated GIS
        {
            "muni": "Tallahassee-Leon County",
            "url": "https://intervector.leoncountyfl.gov/intervector/rest/services/MapServices/TLC_OverlayZoning_D_WM/MapServer/0",
            "code": "ZONING", "desc": None,
        },
    ],
    11: [  # Alachua County
        {
            "muni": "City of Gainesville",
            "url": "https://services2.arcgis.com/Zzhtlau4ccHkQgTu/arcgis/rest/services/Gainesville_Existing_Zoning_01_05_2025/FeatureServer/1",
            "code": "ZONINGCODE", "desc": "ZONINGDESCRIPT",
        },
    ],
    60: [  # Palm Beach County
        {
            "muni": "City of West Palm Beach",
            "url": "https://wpbgisportal.wpb.org/server/rest/services/Planning/Zoning_/FeatureServer/0",
            "code": "ZONECLASS", "desc": "ZONEDESC",
        },
    ],
    52: [  # Marion County
        {
            "muni": "City of Ocala",
            "url": "https://gis.ocalafl.org/arcgis/rest/services/Public/GrowthManagement/FeatureServer/15",
            "code": "ZONECLASS", "desc": "ZONEDESC",
        },
    ],
    21: [  # Collier County — countywide layer; incorporated areas carry a
        # "CITY OF ..." placeholder instead of a district, so skip those.
        {
            "muni": "Collier County",
            "url": "https://services7.arcgis.com/cUdn1W8S2D0Igfpm/arcgis/rest/services/Collier_County_General_Zoning/FeatureServer/0",
            "code": "ZONING", "desc": None,
            "skip_rx": r"^CITY OF|^EVERGLADES|^MARCO",
        },
    ],
    46: [  # Lee County — one county service hosts every jurisdiction as layers
        {
            "muni": "City of Cape Coral",
            "url": "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DCD_Zoning/MapServer/1",
            "code": "LMLUZN", "desc": "Zoning_Desc",
        },
        {
            "muni": "City of Fort Myers",
            "url": "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DCD_Zoning/MapServer/2",
            "code": "ZONING", "desc": "ZONING_DES",
        },
        {
            "muni": "Village of Estero",
            "url": "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DCD_Zoning/MapServer/5",
            "code": "ZONING", "desc": None,
        },
        {
            "muni": "City of Bonita Springs",
            "url": "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DCD_Zoning/MapServer/7",
            "code": "ZONING", "desc": None,
        },
        {
            "muni": "Lee County (unincorporated)",
            "url": "https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DCD_Zoning/MapServer/0",
            "code": "ZONING", "desc": None,
        },
    ],
    66: [  # St. Lucie County — City of Port St. Lucie
        {
            "muni": "City of Port St. Lucie",
            "url": "https://services1.arcgis.com/YdUP5V6WwzeG8T8r/arcgis/rest/services/Zoning/FeatureServer/1",
            "code": "ZONING", "desc": None,
        },
    ],
}


# Rough WGS84 bounding boxes for counties with metro adapters. Used to pick which
# adapters to try when the click lands on a street/ROW and the FDOR cadastral has no
# parcel (so no CO_NO). Overlap is harmless — an adapter miss just returns None.
COUNTY_BBOX = {
    11: (-82.66, 29.42, -82.05, 29.95),   # Alachua
    21: (-81.85, 25.80, -80.87, 26.53),   # Collier
    26: (-82.05, 30.10, -81.32, 30.59),   # Duval
    39: (-82.65, 27.57, -82.05, 28.17),   # Hillsborough
    47: (-84.72, 30.26, -83.99, 30.69),   # Leon
    52: (-82.61, 28.91, -81.64, 29.61),   # Marion
    58: (-81.66, 28.34, -80.86, 28.79),   # Orange
    60: (-80.89, 26.32, -79.97, 27.03),   # Palm Beach
    62: (-82.90, 27.57, -82.54, 28.17),   # Pinellas
    68: (-82.65, 26.94, -82.05, 27.39),   # Sarasota
    46: (-82.35, 26.32, -81.56, 26.79),   # Lee
    66: (-80.68, 27.13, -80.21, 27.56),   # St. Lucie
}


def _counties_by_bbox(lon: float, lat: float) -> list[int]:
    return [c for c, (x0, y0, x1, y1) in COUNTY_BBOX.items()
            if x0 <= lon <= x1 and y0 <= lat <= y1]


async def _query_metro_zoning(co_no: int, lon: float, lat: float, client: httpx.AsyncClient):
    for cfg in METRO_ZONING.get(co_no, []):
        res = await _query_one_metro_adapter(cfg, lon, lat, client)
        if res:
            return res
    return None


async def _query_one_metro_adapter(cfg: dict, lon: float, lat: float, client: httpx.AsyncClient):
    out_fields = [cfg["code"]]
    if cfg.get("desc"):
        out_fields.append(cfg["desc"])
    if cfg.get("height"):
        out_fields.append(cfg["height"])
    out_fields += list(cfg.get("extra", {}).keys())
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(out_fields), "returnGeometry": "false", "f": "json",
    }
    try:
        r = await client.get(f"{cfg['url']}/query", params=params)
        r.raise_for_status()
        feats = (r.json().get("features") or [])
    except (httpx.HTTPError, ValueError):  # ValueError: non-JSON body (WAF/maintenance page)
        return None
    if not feats:
        return None
    a = feats[0].get("attributes") or {}
    code = a.get(cfg["code"])
    if code in (None, "", " "):
        return None
    if cfg.get("skip_rx") and re.match(cfg["skip_rx"], str(code).strip(), re.IGNORECASE):
        return None  # placeholder value (e.g. countywide layer labeling city areas)
    res = {
        "ZONE": str(code).strip(),
        "ZONEDESC": (str(a.get(cfg["desc"])).strip() if cfg.get("desc") and a.get(cfg["desc"]) else None),
        "MUNICNAME": cfg["muni"],
        "source": f"{cfg['muni']} zoning",
    }
    if cfg.get("height") and a.get(cfg["height"]) not in (None, "", 0):
        res["MAXHEIGHT"] = a.get(cfg["height"])
    # Append any extra labeled fields into the description
    extras = []
    for fld, label in cfg.get("extra", {}).items():
        v = a.get(fld)
        if v not in (None, "", " "):
            extras.append(f"{label}: {str(v).strip()}")
    if extras:
        res["ZONEDESC"] = " · ".join(filter(None, [res.get("ZONEDESC")] + extras))
    return res

# Rough north/south split for routing the *rich* tri-county adapters.
# Miami-Dade county tops out near lat 25.97; Broward tops out near 26.40.
MDC_LAT_MAX = 25.975
BRO_LAT_MAX = 26.405

app = FastAPI(title="Meridian")
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    # Same-origin app served by this process — never expose the API (incl. saved
    # marks) to arbitrary websites via a wildcard.
    allow_origins=["http://localhost:8012", "http://127.0.0.1:8012"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── unbuilt data store ─────────────────────────────────────────────────────
# A fresh clone has an empty data/ (see data/README.md) and the Docker image
# ships map layers only, so the condo and metro routes are querying a store that
# has not been built. SQLite reports that as OperationalError, which FastAPI
# turns into a 500 and a traceback -- indistinguishable, from the UI, from the
# app being broken.
#
# Two distinct first-run states, and the fix differs, so they are reported
# differently rather than collapsed into one "no data" message:
#   * the shared store is missing entirely -> ATTACH fails before any query
#   * the store is there but a table has not been built yet
# Every other OperationalError keeps raising, because those are real faults.

_TABLE_BUILDER = {
    "nal_condo_unit": "scripts/prospect/ingest_nal.py",
    "dbpr_association": "scripts/prospect/ingest_dbpr.py",
    "pa_parcel": "scripts/prospect/ingest_pa.py",
    "condo_group": "scripts/prospect/build_targets.py",
    "target": "scripts/prospect/build_targets.py",
    "ingest_log": "scripts/prospect/build_targets.py",
    "market": "scripts/prospect/build_markets.py",
    "county": "scripts/prospect/ingest_market.py",
    "county_pop": "scripts/prospect/ingest_market.py",
    "county_permits": "scripts/prospect/ingest_market.py",
    "county_wage": "scripts/prospect/ingest_market.py",
    "metro_price": "scripts/prospect/ingest_market.py",
    "migration_flow": "scripts/prospect/ingest_market.py",
}
_NO_SUCH_TABLE = re.compile(r"no such table:\s*([\w.]+)")
_CANNOT_OPEN = re.compile(r"unable to open database(?: file)?:?\s*(.*)")


def unbuilt_detail(exc: BaseException) -> dict | None:
    """Classify a SQLite error as a first-run state, or None if it is a real
    fault. Kept separate from the handler so it can be tested without a request."""
    msg = str(exc)
    m = _NO_SUCH_TABLE.search(msg)
    if m:
        table = m.group(1).split(".")[-1]
        builder = _TABLE_BUILDER.get(table)
        return {
            "state": "table_not_built",
            "table": table,
            "builder": builder,
            "detail": (f"`{table}` has not been built yet."
                       + (f" Run {builder} to build it." if builder else "")
                       + " See the README's first-run sequence."),
        }
    m = _CANNOT_OPEN.search(msg)
    if m:
        path = m.group(1).strip() or "the shared store"
        return {
            "state": "shared_store_missing",
            "path": path,
            "builder": "scripts/prospect/migrate_to_shared.py",
            "detail": (f"The shared store could not be opened at {path}. The national "
                       "market tables and the Miami-Dade parcel roll live outside the "
                       "app folder; set APPS_SHARED_DB to the shared.db file, or "
                       "APPS_SHARED to the folder holding it. The map itself does not "
                       "need it."),
        }
    return None


@app.exception_handler(sqlite3.OperationalError)
async def _unbuilt_store(request: Request, exc: sqlite3.OperationalError):
    info = unbuilt_detail(exc)
    if info is None:
        raise exc
    return JSONResponse(info, status_code=503)


# National site-screening (free federal services) + saved-address CSV export.
try:
    from .site_screen import router as site_screen_router
except ImportError:  # when run as a top-level module rather than a package
    from site_screen import router as site_screen_router
app.include_router(site_screen_router)


# ── updates ────────────────────────────────────────────────────────────────
# Download the current branch from GitHub and swap the application files. No git
# needed, so a folder that came from "Download ZIP" updates the same way a clone
# does, and no PowerShell anywhere -- this is urllib and zipfile.

@app.get("/api/instance")
def instance():
    """Which copy of Meridian is answering on this port.

    start.py checks this before deciding a port is "already running": an older
    install auto-starting at logon holds 8012, and without this the launcher
    happily opens a browser onto that one while the folder you just updated sits
    there doing nothing. That is an hour of confusion, and it is one route.
    """
    import os as _o
    version = None
    try:
        import json as _j
        version = _j.loads((APP_ROOT / ".version").read_text(encoding="utf-8")).get("sha")
    except (OSError, ValueError):
        pass
    return {"app": "Meridian", "root": str(APP_ROOT), "pid": _o.getpid(),
            "version": version}


@app.get("/api/update/check")
def update_check():
    from .updater import check
    return check()


@app.post("/api/update/apply")
def update_apply(dry_run: bool = Query(False)):
    """Apply the update. `dry_run` lists what WOULD change and touches nothing.

    data/, .venv/ and logs/ are never replaced, config.json is merged rather
    than overwritten, and every replaced file is backed up first.
    """
    from .updater import apply
    return apply(dry_run=dry_run)


# Acquisitions — condo termination targets, the metro screener, offering
# memoranda and development capacity. These were the standalone Prospect app;
# folding them in here is what makes the map and the table one product instead
# of two windows that link at each other.
try:
    from .prospect.routes import router as acquisitions_router
except ImportError:
    from prospect.routes import router as acquisitions_router
app.include_router(acquisitions_router)


# ---------- helpers ----------

def county_for_point(lat: float, lon: float = -80.2) -> str:
    """Route to the rich tri-county adapters only for the SE-coast bounding boxes.
    Everything else (incl. Gulf-coast points at the same latitude, e.g. Naples)
    goes to the statewide cadastral. Longitude gate prevents west-coast misroutes."""
    # Miami-Dade + Broward sit east of ~lon -80.9.
    if lon >= -80.95:
        if 25.10 <= lat <= MDC_LAT_MAX:
            return "miami-dade"
        if MDC_LAT_MAX < lat <= BRO_LAT_MAX:
            return "broward"
    return "statewide"


def _haversine_ft(lat1, lon1, lat2, lon2):
    R = 20902231.0  # earth radius in ft
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------- static GeoJSON ----------

@app.get("/data/{name}.geojson")
def serve_geojson(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in "_-")
    fp = layer_path(f"{safe}.geojson")
    if not fp.exists():
        raise HTTPException(404, f"{safe}.geojson not found")
    return FileResponse(
        fp,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/data/zcta_pop.json")
def serve_zcta_pop():
    fp = DATA_DIR / "zcta_pop.json"
    if not fp.exists():
        raise HTTPException(404, "zcta_pop.json not built — run scripts/fetch_zcta_population.py")
    return FileResponse(fp, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=86400"})


_ZCTA_POP_CACHE: dict | None = None


@app.get("/api/zip-growth")
def zip_growth(zip: str = Query(..., min_length=5, max_length=5)):
    """One ZIP's ACS population series — a light lookup for the diligence report
    (the full 3.6 MB series file stays lazy-loaded by the map layer only)."""
    global _ZCTA_POP_CACHE
    if _ZCTA_POP_CACHE is None:
        try:
            _ZCTA_POP_CACHE = json.loads((DATA_DIR / "zcta_pop.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _ZCTA_POP_CACHE = {}
    years = _ZCTA_POP_CACHE.get("years") or []
    rec = (_ZCTA_POP_CACHE.get("zctas") or {}).get(zip.strip())
    if not years or not rec:
        return {"found": False}
    pairs = [(y, p) for y, p in zip_longest(years, rec.get("pop") or []) if p]
    if len(pairs) < 2:
        return {"found": False}
    (y0, p0), (y1, p1) = pairs[0], pairs[-1]
    return {"found": True, "zip": zip, "from_year": y0, "to_year": y1,
            "pop_from": p0, "pop_to": p1,
            "pct": round(100 * (p1 - p0) / p0, 1) if p0 else None,
            "series": {str(y): p for y, p in pairs}}


def _load_zcta_pop() -> dict:
    global _ZCTA_POP_CACHE
    if _ZCTA_POP_CACHE is None:
        try:
            _ZCTA_POP_CACHE = json.loads((DATA_DIR / "zcta_pop.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _ZCTA_POP_CACHE = {}
    return _ZCTA_POP_CACHE


# Census TIGERweb ZIP Code Tabulation Area polygons — public, no token. GEOID is
# the 5-digit ZIP, which joins straight to zcta_pop.json's keys.
TIGER_ZCTA = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2"


@app.get("/api/zcta-pop")
async def zcta_pop(bbox: str = Query(...)):
    """ZIP-area (ZCTA) polygons for the viewport, each carrying its ACS population
    series, for the population-growth *choropleth*. Geometry comes from Census
    TIGERweb; the series is the local zcta_pop.json. The frontend computes the
    % change for whichever year window the sliders pick — so panning refetches
    geometry, but dragging the sliders just recolors (no network)."""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")
    data = _load_zcta_pop()
    years = data.get("years") or []
    zseries = data.get("zctas") or {}
    if not years or not zseries:
        return {"type": "FeatureCollection", "features": [], "years": [],
                "note": "Population series not built — run scripts/fetch_zcta_population.py"}
    env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                      "spatialReference": {"wkid": 4326}})
    # Simplify to the viewport scale so a metro pull is tens of KB, not MB.
    offset = round(max(maxx - minx, maxy - miny) / 800.0, 6)
    params = {
        "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID",
        "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
        "maxAllowableOffset": str(offset), "resultRecordCount": "600", "f": "geojson",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(f"{TIGER_ZCTA}/query", params=params)
            r.raise_for_status()
            gj = r.json()
        except (httpx.HTTPError, ValueError):
            return {"type": "FeatureCollection", "features": [], "years": years,
                    "note": "ZIP boundary service unavailable right now."}
    feats = []
    for ft in gj.get("features") or []:
        z = str((ft.get("properties") or {}).get("GEOID") or "")
        rec = zseries.get(z)
        if not rec or not ft.get("geometry"):
            continue
        feats.append({"type": "Feature", "geometry": ft["geometry"],
                      "properties": {"z": z, "pop": rec.get("pop")}})
    return {"type": "FeatureCollection", "features": feats, "years": years}


# ---------- area rents overlay (Zillow ZORI by ZIP, HUD FMR by FMR area) ----
# Two different answers to "what does this area rent for", deliberately kept
# separate rather than blended: ZORI is the *market* asking rent (what a unit
# actually lists at), HUD FMR is the *program* rent (the 40th-percentile ceiling
# that Section 8 / LIHTC underwriting keys off). Where they diverge is the
# affordable-housing spread, which is the whole reason to look at both.

HUD_FMR_LAYER = ("https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/"
                 "services/Fair_Market_Rents/FeatureServer/0")

_ZORI_CACHE: dict | None = None


def _load_zori() -> dict:
    global _ZORI_CACHE
    if _ZORI_CACHE is None:
        try:
            _ZORI_CACHE = json.loads(
                (DATA_DIR / "zori_rents.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _ZORI_CACHE = {}
    return _ZORI_CACHE


@app.get("/api/rents-overlay")
async def rents_overlay(
    bbox: str = Query(...),
    source: str = Query("zori", pattern="^(zori|fmr)$"),
    beds: int = Query(2, ge=0, le=4),
):
    """Rent choropleth for the viewport.

    `zori` joins the local Zillow series onto Census ZCTA polygons — the same
    geometry pipeline the population layer uses, so panning costs one small
    request. `fmr` pulls HUD's own FMR-area polygons, which are county- or
    metro-sized, and reports the chosen bedroom count.
    """
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")

    env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                      "spatialReference": {"wkid": 4326}})
    offset = round(max(maxx - minx, maxy - miny) / 800.0, 6)

    if source == "fmr":
        params = {
            "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FMR_CODE,FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR",
            "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
            "maxAllowableOffset": str(offset), "resultRecordCount": "400", "f": "geojson",
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                r = await client.get(f"{HUD_FMR_LAYER}/query", params=params)
                r.raise_for_status()
                gj = r.json()
            except (httpx.HTTPError, ValueError):
                return {"type": "FeatureCollection", "features": [],
                        "source": "fmr", "note": "HUD FMR service unavailable right now."}
        key = f"FMR_{beds}BDR"
        feats = []
        for ft in gj.get("features") or []:
            p = ft.get("properties") or {}
            rent = p.get(key)
            if rent in (None, "") or not ft.get("geometry"):
                continue
            feats.append({"type": "Feature", "geometry": ft["geometry"], "properties": {
                "name": p.get("FMR_AREANAME"), "rent": round(float(rent)),
                "beds": beds,
                "all": {b: p.get(f"FMR_{b}BDR") for b in range(5)},
            }})
        return {"type": "FeatureCollection", "features": feats, "source": "fmr",
                "beds": beds, "attribution": "HUD Fair Market Rents (40th percentile)"}

    # ---- ZORI by ZIP ----
    z = _load_zori()
    zips = z.get("zip") or {}
    if not zips:
        return {"type": "FeatureCollection", "features": [], "source": "zori",
                "note": "Rent series not built — run scripts/fetch_zori.py"}
    params = {
        "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID",
        "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
        "maxAllowableOffset": str(offset), "resultRecordCount": "600", "f": "geojson",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(f"{TIGER_ZCTA}/query", params=params)
            r.raise_for_status()
            gj = r.json()
        except (httpx.HTTPError, ValueError):
            return {"type": "FeatureCollection", "features": [], "source": "zori",
                    "note": "ZIP boundary service unavailable right now."}
    feats = []
    for ft in gj.get("features") or []:
        code = str((ft.get("properties") or {}).get("GEOID") or "")
        rec = zips.get(code)
        if not rec or not ft.get("geometry"):
            continue
        feats.append({"type": "Feature", "geometry": ft["geometry"], "properties": {
            "name": f"ZIP {code}", "zip": code, "rent": rec.get("rent"),
            "yoy": rec.get("yoy"), "city": rec.get("city"), "metro": rec.get("metro"),
        }})
    return {"type": "FeatureCollection", "features": feats, "source": "zori",
            "asof": z.get("asof"),
            "attribution": z.get("attribution") or "Data: Zillow Research (ZORI)"}


@app.get("/api/rents-at")
async def rents_at(lon: float = Query(...), lat: float = Query(...)):
    """Both rent benchmarks at one point — what the selection panel shows."""
    out: dict = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(f"{HUD_FMR_LAYER}/query", params={
                "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                "outFields": "FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR",
                "returnGeometry": "false", "f": "json"})
            a = ((r.json().get("features") or [{}])[0].get("attributes") or {})
            if a.get("FMR_AREANAME"):
                out["fmr"] = {
                    "area": a.get("FMR_AREANAME"),
                    "studio": a.get("FMR_0BDR"), "br1": a.get("FMR_1BDR"),
                    "br2": a.get("FMR_2BDR"), "br3": a.get("FMR_3BDR"),
                    "br4": a.get("FMR_4BDR"),
                    "source": "HUD Fair Market Rents",
                }
        except (httpx.HTTPError, ValueError, IndexError, KeyError):
            pass
    return out


@app.get("/data/manifest.json")
def manifest():
    files = sorted({p.name for p in LAYERS_DIR.glob("*.geojson")} |
                   {p.name for p in DATA_DIR.glob("*.geojson")})
    return {"layers": files}


# ---------- parcel lookup (multi-county) ----------

# Some county/state parcel fabrics store one assessor ID as multiple separate
# polygon rows — e.g. an assembled development site that consolidates several
# original platted lots keeps each lot's original boundary as its own map
# feature while the tax roll treats them as a single folio (confirmed live on
# Miami-Dade folio 3040350001435 — two rows, same folio, same lot size,
# disjoint geometry). A point-click only ever intersects the ONE row under
# your pixel, so the drawn selection outline silently shows a fragment of the
# real property instead of the whole parcel — worse, WHICH fragment you get
# depends on exactly where you clicked. Cached per (service, id) for the life
# of the process since a parcel's boundary never changes mid-session.
_GEOM_MERGE_CACHE: dict[tuple, dict | None] = {}
_GEOM_MERGE_CACHE_MAX = 5000

# Disk layer under the in-memory cache. Parcel boundaries are effectively
# static, but the dict above dies with the process — so every restart re-paid
# the cold WHERE-by-ID query (observed up to ~14s) on the first click of each
# folio. Verdicts persist here instead, including the common "single row, no
# merge needed" null verdict, so any folio ever clicked upgrades instantly for
# the life of the install. Upstream failures are never written.
GEOM_CACHE_DB = DATA_DIR / "geom_cache.db"


def _geom_cache_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(GEOM_CACHE_DB, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geom_merge (
            service    TEXT NOT NULL,
            id_field   TEXT NOT NULL,
            id_value   TEXT NOT NULL,
            merged     TEXT,              -- GeoJSON geometry; NULL = single row, keep original
            fetched_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (service, id_field, id_value)
        )
    """)
    return conn


def _geom_cache_get(key: tuple) -> tuple[bool, dict | None]:
    """(hit, verdict) — hit=False on miss or any db error, so callers fall through."""
    try:
        with contextlib.closing(_geom_cache_conn()) as conn:
            row = conn.execute(
                "SELECT merged FROM geom_merge WHERE service=? AND id_field=? AND id_value=?",
                key,
            ).fetchone()
    except (sqlite3.Error, ValueError):
        return False, None
    if row is None:
        return False, None
    try:
        return True, (json.loads(row[0]) if row[0] else None)
    except ValueError:
        return False, None


def _geom_cache_put(key: tuple, merged: dict | None) -> None:
    try:
        with contextlib.closing(_geom_cache_conn()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO geom_merge (service, id_field, id_value, merged)"
                " VALUES (?, ?, ?, ?)",
                (*key, json.dumps(merged) if merged else None),
            )
    except sqlite3.Error:
        pass  # cache is best-effort; the verdict is still returned to the caller


async def _assembled_geometry(url: str, id_field: str,
                              id_value, numeric: bool = False) -> dict | None:
    """Re-query by ID and union every matching row into one MultiPolygon. Returns
    None (keep the caller's original single-feature geometry) when there's only
    one row — the overwhelming common case — so this never *shrinks* a result.
    Builds its own HTTP client, and only on a full cache miss: an AsyncClient
    costs ~270ms to construct (SSL context), which would dwarf the ~3ms
    cached path this endpoint serves almost all of the time."""
    if not id_value:
        return None
    key = (url, id_field, str(id_value))
    if key in _GEOM_MERGE_CACHE:
        return _GEOM_MERGE_CACHE[key]
    hit, cached = _geom_cache_get(key)
    if hit:
        if len(_GEOM_MERGE_CACHE) < _GEOM_MERGE_CACHE_MAX:
            _GEOM_MERGE_CACHE[key] = cached
        return cached
    lit = str(id_value) if numeric else "'" + str(id_value).replace("'", "''") + "'"
    try:
        # Observed live up to ~14s on a cold cache for an unindexed attribute
        # query — generous on purpose since this now runs off the click's
        # critical path (see /api/parcel-geometry).
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{url}/query", params={
                "where": f"{id_field}={lit}", "outFields": "OBJECTID",
                "returnGeometry": "true", "f": "geojson",
            }, timeout=18.0)
            r.raise_for_status()
            feats = r.json().get("features") or []
    except (httpx.HTTPError, ValueError):
        return None
    polys = []
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon" and g.get("coordinates"):
            polys.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon" and g.get("coordinates"):
            polys.extend(g["coordinates"])
    merged = {"type": "MultiPolygon", "coordinates": polys} if len(polys) > 1 else None
    if len(_GEOM_MERGE_CACHE) < _GEOM_MERGE_CACHE_MAX:
        _GEOM_MERGE_CACHE[key] = merged
    _geom_cache_put(key, merged)
    return merged


async def _query_mdc_parcel(lon: float, lat: float, client: httpx.AsyncClient):
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
    }
    url = f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query"
    r = await client.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    feats = data.get("features") or []
    if not feats:
        return None
    p = feats[0]
    props = p.get("properties") or {}
    folio = props.get("FOLIO")
    out = {
        "county": "miami-dade",
        "folio": folio,
        "address": props.get("TRUE_SITE_ADDR"),
        "unit": props.get("TRUE_SITE_UNIT"),
        "city": props.get("TRUE_SITE_CITY"),
        "zip": props.get("TRUE_SITE_ZIP_CODE"),
        "owner": " / ".join(filter(None, [props.get("TRUE_OWNER1"), props.get("TRUE_OWNER2"), props.get("TRUE_OWNER3")])),
        "lot_size_sf": props.get("LOT_SIZE"),
        "building_area_sf": props.get("BUILDING_ACTUAL_AREA"),
        "year_built": props.get("YEAR_BUILT"),
        "legal": props.get("LEGAL"),
        "municipality_code": props.get("MUNICIPALITY_CODE"),
        "geometry": p.get("geometry"),
        "raw": props,
    }
    if folio:
        clean = folio.replace("-", "").replace(" ", "")
        out["pa_url"] = f"https://www.miamidade.gov/Apps/PA/propertysearch/#/?folio={clean}"
    return out


async def _query_bro_parcel(lon: float, lat: float, client: httpx.AsyncClient):
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry,
        "geometryType": "esriGeometryPoint",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FOLIO,USE_CODE,FULL_SITE_ADDRESS,CITY_NAME",
        "returnGeometry": "true",
        "f": "geojson",
    }
    url = f"{BRO_ROOT}/BCPA_Parcels/FeatureServer/53/query"
    r = await client.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    feats = data.get("features") or []
    if not feats:
        return None
    p = feats[0]
    props = p.get("properties") or {}
    folio = props.get("FOLIO")
    out = {
        "county": "broward",
        "folio": folio,
        "address": props.get("FULL_SITE_ADDRESS"),
        "city": props.get("CITY_NAME"),
        "use_code": props.get("USE_CODE"),
        "geometry": p.get("geometry"),
        "raw": props,
    }
    if folio:
        clean = folio.replace("-", "").replace(" ", "")
        out["pa_url"] = f"https://web.bcpa.net/bcpaclient/#/Record-Search?folio={clean}"
    return out


async def _query_statewide_parcel(lon: float, lat: float, client: httpx.AsyncClient,
                                  timeout: float | None = None):
    """Fallback for any of the 67 counties: FDOR statewide cadastral. Returns the
    full tax-roll record (owner, just/assessed/taxable value, two latest sales,
    building, legal, use code)."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    fields = (
        "PARCEL_ID,CO_NO,OWN_NAME,OWN_ADDR1,OWN_CITY,OWN_STATE,OWN_ZIPCD,"
        "PHY_ADDR1,PHY_CITY,PHY_ZIPCD,DOR_UC,JV,LND_VAL,AV_SD,TV_SD,"
        "LND_SQFOOT,TOT_LVG_AR,NO_BULDNG,NO_RES_UNT,ACT_YR_BLT,EFF_YR_BLT,"
        "SALE_PRC1,SALE_YR1,SALE_MO1,OR_BOOK1,OR_PAGE1,QUAL_CD1,"
        "SALE_PRC2,SALE_YR2,SALE_MO2,OR_BOOK2,OR_PAGE2,QUAL_CD2,S_LEGAL"
    )
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint",
        "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": fields, "returnGeometry": "true", "f": "geojson",
    }
    if timeout is not None:
        r = await client.get(f"{FL_CADASTRAL}/query", params=params, timeout=timeout)
    else:
        r = await client.get(f"{FL_CADASTRAL}/query", params=params)
    r.raise_for_status()
    feats = (r.json().get("features") or [])
    if not feats:
        return None
    f = feats[0]
    a = f.get("properties") or {}
    co = a.get("CO_NO")
    sales = []
    for n in ("1", "2"):
        prc = a.get(f"SALE_PRC{n}")
        yr = a.get(f"SALE_YR{n}")
        if prc and yr and int(yr) > 0:
            sales.append({
                "price": prc,
                "date": f"{str(a.get(f'SALE_MO{n}', '')).strip() or '?'}/{yr}",
                "or_book": str(a.get(f"OR_BOOK{n}", "")).strip(),
                "or_page": str(a.get(f"OR_PAGE{n}", "")).strip(),
                "qual": str(a.get(f"QUAL_CD{n}", "")).strip(),
            })
    try:
        co_no_int = int(co)
    except (TypeError, ValueError):
        co_no_int = None
    parcel_id = str(a.get("PARCEL_ID", "")).strip()
    return {
        "county": county_name(co).lower().replace(" ", "-"),
        "county_name": county_name(co),
        "co_no": co_no_int,
        "source": "statewide",
        "folio": parcel_id,
        "address": a.get("PHY_ADDR1"),
        "city": a.get("PHY_CITY"),
        "zip": a.get("PHY_ZIPCD"),
        "owner": a.get("OWN_NAME"),
        "owner_mailing": ", ".join(filter(None, [a.get("OWN_ADDR1"), a.get("OWN_CITY"), a.get("OWN_STATE"), str(a.get("OWN_ZIPCD") or "")])),
        "use_code": str(a.get("DOR_UC", "")).strip(),
        "use_description": use_description(a.get("DOR_UC")),
        "lot_size_sf": a.get("LND_SQFOOT") or None,
        "building_area_sf": a.get("TOT_LVG_AR") or None,
        "buildings": a.get("NO_BULDNG"),
        "res_units": a.get("NO_RES_UNT"),
        "year_built": a.get("ACT_YR_BLT") or None,
        "effective_year": a.get("EFF_YR_BLT") or None,
        "just_value": a.get("JV"),
        "land_value": a.get("LND_VAL"),
        "assessed_value": a.get("AV_SD"),
        "taxable_value": a.get("TV_SD"),
        "sales": sales,
        "legal": a.get("S_LEGAL"),
        "geometry": f.get("geometry"),
    }


# ======================================================================
# NATIONAL hybrid parcel layer
#   1. Florida tri-county rich adapters (unchanged)
#   2. Florida statewide FDOR cadastral
#   3. Free statewide parcel services for states that publish them
#   4. Regrid commercial API (env-gated REGRID_TOKEN) as universal gap-fill
# ======================================================================

import os

REGRID_TOKEN = os.environ.get("REGRID_TOKEN", "").strip()

# States where the sale price is NOT a public record (deed records the transfer,
# but consideration is legally withheld). No tool shows a real price here.
NON_DISCLOSURE_STATES = {"TX", "KS", "MO", "MT", "NM", "ND", "UT", "WY", "ID", "MS", "LA", "AK"}

# Free statewide parcel services, keyed by 2-letter state. Each config maps that
# service's field names onto our normalized shape. Verified by downtown point query
# before wiring (see scripts/statehunt.js). Populated incrementally.
STATE_PARCELS: dict[str, dict] = {
    "MA": {
        "url": "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0",
        "source": "MassGIS statewide property tax parcels",
        "fields": {
            "parcel_id": ["MAP_PAR_ID", "LOC_ID"], "owner": ["OWNER1"],
            "address": ["SITE_ADDR"], "city": ["CITY"], "zip": ["ZIP"],
            "use": ["USE_CODE"], "bldg_sf": ["BLD_AREA"], "year": ["YEAR_BUILT"],
            "value": ["TOTAL_VAL"], "land_value": ["LAND_VAL"],
            "sale_price": ["LS_PRICE"], "sale_date": ["LS_DATE"],
        },
    },
    "WI": {
        "url": "https://services3.arcgis.com/n6uYoouQZW75n5WI/arcgis/rest/services/Wisconsin_Statewide_Parcels/FeatureServer/0",
        "source": "Wisconsin statewide parcels (V8)",
        "fields": {
            "parcel_id": ["PARCELID", "TAXPARCELID"], "owner": ["OWNERNME1"],
            "address": ["SITEADRESS"], "zip": ["ZIPCODE"],
            "value": ["ESTFMKVALUE"], "land_value": ["LNDVALUE"],
            "lot_acres": ["GISACRES", "ASSDACRES", "DEEDACRES"],
            "county": ["CONAME"],
        },
    },
    "MT": {
        "url": "https://services.arcgis.com/qnjIrwR8z5Izc0ij/arcgis/rest/services/Montana_Cadastral_Framework/FeatureServer/1",
        "source": "Montana statewide cadastral",
        "fields": {
            "parcel_id": ["PARCELID"], "owner": ["OwnerName"],
            "address": ["AddressLine1"], "county": ["CountyName"],
            "value": ["TotalValue"], "land_value": ["TotalLandValue"],
            "lot_acres": ["TotalAcres", "GISAcres"], "legal": ["LegalDescriptionShort"],
        },
    },
    "CT": {
        "url": "https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer/0",
        "source": "Connecticut statewide CAMA + parcels",
        "fields": {
            "parcel_id": ["Parcel_ID"], "owner": ["Owner"],
            "address": ["Location_1", "Full_Address"], "city": ["Property_City"],
            "zip": ["Property_Zip"], "county": ["Town_Name"],
            "use": ["State_Use_Description"], "lot_acres": ["Land_Acres"],
            "sale_price": ["Sale_Price"], "sale_date": ["Sale_Date"],
        },
    },
    "VT": {
        "url": "https://services1.arcgis.com/BkFxaEFNwHqX3tAw/arcgis/rest/services/FS_VCGI_OPENDATA_Cadastral_VTPARCELS_poly_standardized_parcels_SP_v1/FeatureServer/0",
        "source": "Vermont statewide standardized parcels",
        "fields": {
            "parcel_id": ["PARCID", "SPAN"], "owner": ["OWNER1"],
            "address": ["E911ADDR", "LOCAPROP", "ADDRGL1"], "city": ["CITYGL"],
            "zip": ["ZIPGL"], "county": ["TOWN"], "lot_acres": ["ACRESGL"],
        },
    },
    "NJ": {
        "url": "https://maps.nj.gov/arcgis/rest/services/Framework/Cadastral/MapServer/0",
        "source": "NJ statewide parcels + MOD-IV composite",
        "fields": {
            "parcel_id": ["PAMS_PIN"], "owner": ["OWNER_NAME"],
            "address": ["PROP_LOC", "ST_ADDRESS"], "zip": ["ZIP5"], "county": ["COUNTY"],
            "use": ["PROP_CLASS"], "value": ["NET_VALUE"], "land_value": ["LAND_VAL"],
            "lot_acres": ["CALC_ACRE"], "sale_price": ["SALE_PRICE"], "sale_date": ["DEED_DATE"],
            "or_book": ["DEED_BOOK"], "or_page": ["DEED_PAGE"],
        },
    },
    "AR": {
        "url": "https://gis.arkansas.gov/arcgis/rest/services/FEATURESERVICES/Planning_Cadastre/FeatureServer/6",
        "source": "Arkansas statewide parcels (County Assessor Mapping Program)",
        "fields": {
            "parcel_id": ["parcelid"], "owner": ["ownername"],
            "address": ["adrlabel"], "city": ["adrcity"], "zip": ["adrzip5"],
            "county": ["county"], "value": ["totalvalue"], "land_value": ["landvalue"],
            "legal": ["parcellgl"],
        },
    },
    "OH": {
        "url": "https://gis.ohiodnr.gov/arcgis/rest/services/OIT_Services/odnr_landbase_v2/MapServer/4",
        "source": "Ohio statewide parcels (ODNR)",
        "fields": {
            "parcel_id": ["STATEWIDE_PIN", "PIN"], "owner": ["OWNER1"],
            "county": ["COUNTY"], "lot_acres": ["ASSR_ACRES"],
        },
    },
    "ND": {
        "url": "https://services1.arcgis.com/GOcSXpzwBHyk2nog/arcgis/rest/services/NDGISHUB_Parcels/FeatureServer/0",
        "source": "North Dakota statewide parcels (NDGISHUB)",
        "fields": {
            "parcel_id": ["UniqueGISID", "GISID"], "owner": ["Ownership"],
            "county": ["CountyName"], "lot_acres": ["CalculatedAcres"],
        },
    },
    "IA": {
        "url": "https://services3.arcgis.com/kd9gaiUExYqUbnoq/arcgis/rest/services/Iowa_Parcels_2017/FeatureServer/0",
        "source": "Iowa statewide parcels (2017)",
        "fields": {
            "parcel_id": ["PARCELNUMB", "STATEPARID"], "owner": ["DEEDHOLDER"],
            "county": ["COUNTYNAME"], "use": ["PARCELCLAS"],
        },
    },
}


async def _state_for_point(lon: float, lat: float, client: httpx.AsyncClient):
    """Resolve a point to (state_abbrev, county_name) via the free FCC Area API."""
    try:
        r = await client.get(
            "https://geo.fcc.gov/api/census/area",
            params={"lat": lat, "lon": lon, "format": "json"}, timeout=8.0,
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if results:
            return results[0].get("state_code"), results[0].get("county_name")
    except (httpx.HTTPError, ValueError, KeyError):
        return None, None
    return None, None


def _g(attrs: dict, *names):
    """First non-empty attribute among candidate field names (case-insensitive)."""
    lower = {k.lower(): v for k, v in attrs.items()}
    for n in names:
        if n is None:
            continue
        v = lower.get(n.lower())
        if v not in (None, "", " "):
            return v
    return None


def _g_field(attrs: dict, *names):
    """Like _g but also returns which candidate field name matched — needed to
    re-query the same service by that exact field for multi-part geometry assembly."""
    lower_keys = {k.lower(): k for k in attrs.keys()}
    for n in names:
        if n is None:
            continue
        actual = lower_keys.get(n.lower())
        if actual is not None and attrs[actual] not in (None, "", " "):
            return actual, attrs[actual]
    return None, None


async def _query_state_parcel(state: str, lon: float, lat: float, client: httpx.AsyncClient):
    cfg = STATE_PARCELS.get(state)
    if not cfg:
        return None
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint",
        "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*", "returnGeometry": "true", "f": "geojson",
    }
    try:
        r = await client.get(f"{cfg['url']}/query", params=params, timeout=12.0)
        r.raise_for_status()
        feats = (r.json().get("features") or [])
    except (httpx.HTTPError, ValueError):
        return None
    if not feats:
        # The layer answered — there is genuinely no parcel at this point (street/
        # park/water). Distinct from None (no layer / upstream error) so the caller
        # doesn't show the Regrid upsell for a covered state.
        return {"_miss": True}
    f = feats[0]
    a = f.get("properties") or {}
    fm = cfg["fields"]
    sale_price = _g(a, *fm.get("sale_price", []))
    sale_date = _g(a, *fm.get("sale_date", []))
    sales = []
    # Only surface real arm's-length-ish sales (skip $0 placeholders).
    if sale_price and float(sale_price) > 0:
        sales.append({"price": sale_price, "date": str(sale_date) if sale_date else None,
                      "or_book": str(_g(a, *fm.get("or_book", [])) or "").strip(),
                      "or_page": str(_g(a, *fm.get("or_page", [])) or "").strip(), "qual": ""})
    _, parcel_id_val = _g_field(a, *fm.get("parcel_id", []))
    folio = str(parcel_id_val or "").strip() or None
    return {
        "county": (state.lower() + "-state"),
        "county_name": _g(a, *fm.get("county", [])) or f"{state} (statewide)",
        "state": state,
        "source": cfg.get("source", f"{state} statewide parcels"),
        "folio": folio,
        "address": _g(a, *fm.get("address", [])),
        "city": _g(a, *fm.get("city", [])),
        "zip": _g(a, *fm.get("zip", [])),
        "owner": _g(a, *fm.get("owner", [])),
        "use_description": _g(a, *fm.get("use", [])),
        "lot_size_sf": _g(a, *fm.get("lot_sf", [])) or (
            round(float(_g(a, *fm.get("lot_acres", [])) or 0) * 43560) or None),
        "building_area_sf": _g(a, *fm.get("bldg_sf", [])),
        "year_built": _g(a, *fm.get("year", [])),
        "just_value": _g(a, *fm.get("value", [])),
        "land_value": _g(a, *fm.get("land_value", [])),
        "sales": sales,
        "legal": _g(a, *fm.get("legal", [])),
        "geometry": f.get("geometry"),
    }


async def _query_regrid(lon: float, lat: float, client: httpx.AsyncClient):
    """Universal commercial gap-fill. Returns a status dict (never None) so the
    caller can surface 'set REGRID_TOKEN' when the key is absent."""
    if not REGRID_TOKEN:
        return {"_needs_key": True}
    try:
        r = await client.get(
            "https://app.regrid.com/api/v2/parcels/point",
            params={"lat": lat, "lon": lon, "token": REGRID_TOKEN, "return_geometry": "true"},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"_error": str(e)}
    feats = (data.get("parcels") or data).get("features") if isinstance(data, dict) else None
    feats = feats or []
    if not feats:
        return None
    f = feats[0]
    fl = (f.get("properties") or {}).get("fields") or {}
    sales = []
    if fl.get("saleprice") or fl.get("saledate"):
        sales.append({"price": fl.get("saleprice"), "date": fl.get("saledate"),
                      "or_book": str(fl.get("book") or "").strip(),
                      "or_page": str(fl.get("page") or "").strip(), "qual": ""})
    return {
        "county": (fl.get("county") or "").lower().replace(" ", "-") or "regrid",
        "county_name": fl.get("county") or "—",
        "state": fl.get("state2") or fl.get("state"),
        "source": "Regrid (national)",
        "folio": fl.get("parcelnumb") or fl.get("parcelnumb_no_formatting"),
        "address": fl.get("address") or fl.get("saddno") and f"{fl.get('saddno','')} {fl.get('saddstr','')}".strip(),
        "city": fl.get("scity"), "zip": fl.get("szip") or fl.get("szip5"),
        "owner": fl.get("owner"),
        "owner_mailing": fl.get("mailadd"),
        "use_code": fl.get("usecode"),
        "use_description": fl.get("usedesc"),
        "zoning_hint": fl.get("zoning"),
        "zoning_desc_hint": fl.get("zoning_description"),
        "lot_size_sf": fl.get("ll_gissqft") or (fl.get("ll_gisacre") and round(fl["ll_gisacre"] * 43560)),
        "building_area_sf": fl.get("ll_bldg_footprint_sqft") or fl.get("sqft"),
        "year_built": fl.get("yearbuilt"),
        "just_value": fl.get("parval") or fl.get("total_value"),
        "land_value": fl.get("landval"),
        "improvement_value": fl.get("improvval"),
        "sales": sales,
        "legal": fl.get("legaldesc"),
        "geometry": f.get("geometry"),
    }


NYC_BBOX = (-74.27, 40.48, -73.68, 40.93)
NYC_PLUTO = "https://services5.arcgis.com/GfwWNkhOj9bNBqoJ/arcgis/rest/services/MAPPLUTO/FeatureServer/0"
# PLUTO LandUse code -> label
# NYC DOF building-class families (first letter of the 2-char class code).
NYC_BLDG_CLASS = {
    "A": "One-family dwelling", "B": "Two-family dwelling", "C": "Walk-up apartments",
    "D": "Elevator apartments", "E": "Warehouse", "F": "Factory / industrial",
    "G": "Garage / gas station", "H": "Hotel", "I": "Hospital / health facility",
    "J": "Theatre", "K": "Retail store", "L": "Loft building", "M": "Religious",
    "N": "Asylum / home", "O": "Office building", "P": "Public assembly",
    "Q": "Outdoor recreation", "R": "Condominium unit", "S": "Mixed residence & store",
    "T": "Transportation / terminal", "U": "Utility", "V": "Vacant land",
    "W": "Educational", "Y": "Government", "Z": "Miscellaneous",
}

PLUTO_LANDUSE = {
    "01": "1-2 family residential", "02": "Multi-family walk-up", "03": "Multi-family elevator",
    "04": "Mixed residential/commercial", "05": "Commercial/office", "06": "Industrial/manufacturing",
    "07": "Transportation/utility", "08": "Public facility/institution", "09": "Open space/recreation",
    "10": "Parking facility", "11": "Vacant land",
}


async def _query_nyc_pluto(lon: float, lat: float, client: httpx.AsyncClient):
    """NYC MapPLUTO — owner, zoning, FAR (resid/comm/built), units, floors, lot/bldg area,
    assessed value, year, building class — the richest single-layer parcel data in the US."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    fields = ("Address,OwnerName,ZoneDist1,ZoneDist2,Overlay1,BldgClass,LandUse,LotArea,"
              "BldgArea,NumFloors,UnitsTotal,UnitsRes,YearBuilt,AssessTot,AssessLand,BBL,"
              "ResidFAR,CommFAR,BuiltFAR,Borough,ZipCode,HistDist,Landmark,"
              "LotFront,LotDepth,CD,Council,SchoolDist,PolicePrct,Block,Lot")
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": fields,
        "returnGeometry": "true", "f": "geojson",
    }
    try:
        r = await client.get(f"{NYC_PLUTO}/query", params=params, timeout=15.0)
        r.raise_for_status()
        feats = (r.json().get("features") or [])
    except (httpx.HTTPError, ValueError):
        return None
    if not feats:
        # PLUTO answered with no lot here (street ROW/park/water) — not a coverage gap.
        return {"_miss": True}
    f = feats[0]; a = f.get("properties") or {}
    zd = " / ".join(filter(None, [a.get("ZoneDist1"), a.get("ZoneDist2"), a.get("Overlay1")]))
    far_parts = []
    for lbl, k in (("Resid", "ResidFAR"), ("Comm", "CommFAR"), ("Built", "BuiltFAR")):
        if a.get(k):
            far_parts.append(f"{lbl} {a[k]}")
    bbl = str(a.get("BBL") or "").split(".")[0]
    boro = bbl[0] if len(bbl) == 10 else ""
    block = bbl[1:6].lstrip("0") if len(bbl) == 10 else ""
    lot = bbl[6:10].lstrip("0") if len(bbl) == 10 else ""
    links = {}
    if boro and block and lot:
        links["zola"] = f"https://zola.planning.nyc.gov/lot/{boro}/{block}/{lot}"
        links["acris"] = f"https://a836-acris.nyc.gov/DS/DocumentSearch/BBL?bbl={bbl}"  # deeds / property records
        links["dob"] = (f"https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet"
                        f"?boro={boro}&block={block}&lot={lot}")  # DOB building/permits
        links["dtm"] = "https://propertyinformationportal.nyc.gov/"  # digital tax map
    return {
        "found": True, "county": "nyc", "county_name": f"NYC ({a.get('Borough', '')})", "state": "NY",
        "source": "NYC MapPLUTO (DCP)",
        "folio": bbl or None,
        "address": a.get("Address"), "city": "New York", "zip": str(a.get("ZipCode") or "").split(".")[0],
        "owner": a.get("OwnerName"),
        "use_code": a.get("LandUse"),
        "use_description": PLUTO_LANDUSE.get(str(a.get("LandUse") or "").zfill(2), a.get("BldgClass")),
        "building_class": a.get("BldgClass"),
        "building_class_desc": NYC_BLDG_CLASS.get(str(a.get("BldgClass") or "")[:1].upper()),
        "lot_size_sf": a.get("LotArea"), "building_area_sf": a.get("BldgArea"),
        "lot_frontage_ft": a.get("LotFront") or None, "lot_depth_ft": a.get("LotDepth") or None,
        "res_units": a.get("UnitsRes"), "buildings": None,
        "units_total": a.get("UnitsTotal"), "floors": a.get("NumFloors"),
        "year_built": a.get("YearBuilt") or None,
        "just_value": a.get("AssessTot"), "land_value": a.get("AssessLand"),
        "zoning_hint": zd or None, "far_note": " · ".join(far_parts) or None,
        "historic": a.get("HistDist") or (a.get("Landmark") if a.get("Landmark") else None),
        "community_district": a.get("CD"), "council_district": a.get("Council"),
        "school_district": a.get("SchoolDist"), "police_precinct": a.get("PolicePrct"),
        "nyc_links": links,
        "geometry": f.get("geometry"),
    }


@app.get("/api/parcel")
async def parcel_at_point(lon: float = Query(...), lat: float = Query(...)):
    # NYC first — PLUTO is richer than anything else.
    nyc_miss = False
    if NYC_BBOX[0] <= lon <= NYC_BBOX[2] and NYC_BBOX[1] <= lat <= NYC_BBOX[3]:
        async with httpx.AsyncClient(timeout=18.0) as client:
            try:
                p = await _query_nyc_pluto(lon, lat, client)
            except (httpx.HTTPError, ValueError):
                p = None
        if p and not p.get("_miss"):
            return p
        nyc_miss = bool(p and p.get("_miss"))
    county = county_for_point(lat, lon)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            if county == "miami-dade":
                p = await _query_mdc_parcel(lon, lat, client)
            elif county == "broward":
                p = await _query_bro_parcel(lon, lat, client)
            else:
                p = await _query_statewide_parcel(lon, lat, client)  # FL FDOR
            if not p and county in ("miami-dade", "broward"):
                p = await _query_statewide_parcel(lon, lat, client)

            # Outside Florida → national hybrid: free state layer, then Regrid.
            if not p:
                state, fcc_county = await _state_for_point(lon, lat, client)
                if state and state != "FL":
                    p = await _query_state_parcel(state, lon, lat, client)
                    state_miss = bool(p and p.get("_miss"))
                    if state_miss:
                        p = None
                    if p and fcc_county and (not p.get("county_name") or "(statewide)" in (p.get("county_name") or "")):
                        p["county_name"] = fcc_county  # for recorder/deed link
                    if not p:
                        # A covered free layer (state or NYC PLUTO) that answered with
                        # zero features means there's genuinely no parcel here — say so
                        # instead of upselling a Regrid key that wouldn't help.
                        if nyc_miss or state_miss:
                            return {"found": False, "state": state, "county_name": fcc_county,
                                    "region": "national",
                                    "message": "No parcel at this point (street/park/water)."}
                        rg = await _query_regrid(lon, lat, client)
                        if rg and "_needs_key" in rg:
                            return {"found": False, "state": state, "county_name": fcc_county,
                                    "region": "national",
                                    "message": ("No free statewide parcel layer for "
                                                f"{state}. Set REGRID_TOKEN to enable national "
                                                "parcel gap-fill (commercial)."),
                                    "needs_regrid_key": True}
                        if rg and "_error" in rg:
                            return {"found": False, "state": state, "message": f"Regrid error: {rg['_error']}"}
                        if rg and not rg.get("county_name") and fcc_county:
                            rg["county_name"] = fcc_county
                        p = rg
        except (httpx.HTTPError, ValueError) as e:
            return {"found": False, "county": county, "message": f"Upstream error: {e}"}
    if not p:
        return {"found": False, "county": county}
    return {"found": True, **p}


@app.get("/api/parcel-geometry")
async def parcel_geometry(folio: str = Query(..., min_length=1), county: str | None = Query(None),
                          state: str | None = Query(None), source: str | None = Query(None)):
    """Progressive-enhancement follow-up to /api/parcel: checks whether this
    folio/BBL is split across multiple map-feature rows (see _assembled_geometry)
    and returns the merged outline if so. Deliberately NOT called synchronously
    from /api/parcel — the underlying WHERE-by-ID query is fast on a warm cache
    but has been observed up to ~14s cold on some of these hosted layers, and
    that's not an acceptable delay to add to every single parcel click
    nationally. The frontend fires this after rendering and silently upgrades
    the selection outline if a merge is found."""
    # `source == "statewide"` must be checked BEFORE the county match: when
    # Miami-Dade's or Broward's own live GIS service is unavailable, /api/parcel
    # falls back to the FL statewide cadastral for that same point, and that
    # response still carries county="miami-dade"/"broward" — but its folio is a
    # FDOR PARCEL_ID from a completely different service, not an MDC/BCPA FOLIO.
    if source == "statewide":
        svc, field, numeric = FL_CADASTRAL, "PARCEL_ID", False
    elif county == "miami-dade":
        svc, field, numeric = f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0", "FOLIO", False
    elif county == "broward":
        svc, field, numeric = f"{BRO_ROOT}/BCPA_Parcels/FeatureServer/53", "FOLIO", False
    elif county == "nyc":
        svc, field, numeric = NYC_PLUTO, "BBL", True
    elif state and STATE_PARCELS.get(state):
        cfg = STATE_PARCELS[state]
        svc, field, numeric = cfg["url"], cfg["fields"]["parcel_id"][0], False
    else:
        return {"merged": False}  # Regrid and unrecognized sources: no attribute-query capability
    merged = await _assembled_geometry(svc, field, folio, numeric=numeric)
    return {"merged": bool(merged), "geometry": merged} if merged else {"merged": False}


# ---------- recorded transactions (county recorder/assessor, free where public) ----------

def _to_price(v):
    if v in (None, "", 0, "0"):
        return None
    try:
        n = float(str(v).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n > 100 else None


def _to_date(v):
    """Normalize an Esri date (epoch ms or string) to YYYY-MM-DD-ish."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)) and v > 10_000_000:  # epoch ms
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    return str(v).strip()[:10]


@app.get("/api/transactions")
async def transactions(lon: float = Query(...), lat: float = Query(...)):
    """Recorded sale(s) from the county recorder/assessor where a free
    machine-readable endpoint exists (county_sales.json). Returns sales[] in the
    same shape the parcel panel renders. Non-disclosure states (e.g. TX) return
    the transfer/owner without a price — that's state law, not a gap."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # resolve point -> state + county (FIPS preferred for a robust registry match)
        state = county_name = fips = None
        try:
            r = await client.get("https://geo.fcc.gov/api/census/area",
                                  params={"lat": lat, "lon": lon, "format": "json"}, timeout=8.0)
            r.raise_for_status()
            res = (r.json().get("results") or [])
            if res:
                state = res[0].get("state_code")
                county_name = res[0].get("county_name")
                fips = res[0].get("county_fips")
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        if not state:
            return {"found": False}

        entry = _COUNTY_SALES_BY_FIPS.get(fips)
        if not entry and county_name and state:
            nc = _norm_county(county_name)
            entry = next((c for c in COUNTY_SALES
                          if c.get("state") == state and _norm_county(c.get("label", "")) == nc), None)
        if not entry or not entry.get("url"):  # tolerate malformed harvested entries
            return {"found": False, "state": state, "county_name": county_name,
                    "disclosure": state not in NON_DISCLOSURE_STATES,
                    "non_disclosure_note": (
                        None if state not in NON_DISCLOSURE_STATES else
                        f"{state} is a non-disclosure state — sale prices are not public "
                        "record. The recorded deed (transfer + parties) is still available "
                        "via the county recorder link above; price requires a private feed.")}

        # query the registered service at the point
        geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
        fields = [entry.get(k) for k in ("price", "date", "grantor", "grantee", "book", "addr", "parcel")]
        fields = [f for f in fields if f]
        params = {
            "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": ",".join(fields) if fields else "*",
            "returnGeometry": "false", "f": "json",
        }
        try:
            r = await client.get(f"{entry['url']}/query", params=params, timeout=12.0)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError) as e:
            return {"found": False, "state": state, "county_name": county_name,
                    "message": f"County service error: {e}"}
        if not feats:
            return {"found": False, "state": state, "county_name": county_name,
                    "source": entry.get("label")}

        a = feats[0].get("attributes") or {}
        price = _to_price(a.get(entry["price"])) if entry.get("price") else None
        sale = {
            "price": price,
            "date": _to_date(a.get(entry["date"])) if entry.get("date") else None,
            "grantor": (str(a.get(entry["grantor"])).strip() if entry.get("grantor") and a.get(entry["grantor"]) else None),
            "grantee": (str(a.get(entry["grantee"])).strip() if entry.get("grantee") and a.get(entry["grantee"]) else None),
            "or_book": (str(a.get(entry["book"])).strip() if entry.get("book") and a.get(entry["book"]) else ""),
            "or_page": "",
            "qual": "",
        }
        has_any = any(v for v in (sale["price"], sale["date"], sale["grantor"], sale["grantee"]))
        return {
            "found": has_any,
            "state": state,
            "county_name": county_name or entry.get("label"),
            "source": entry.get("label"),
            "disclosure": state not in NON_DISCLOSURE_STATES,
            "non_disclosure_note": (
                None if state not in NON_DISCLOSURE_STATES else
                f"{state} is a non-disclosure state — sale prices are not public record; "
                "the transfer/owner is shown but not the price."),
            "parcel_id": (str(a.get(entry["parcel"])).strip() if entry.get("parcel") and a.get(entry["parcel"]) else None),
            "address": (str(a.get(entry["addr"])).strip() if entry.get("addr") and a.get(entry["addr"]) else None),
            "sales": [sale] if has_any else [],
        }


# ---------- zoning at point (multi-county) ----------

async def _query_mdc_zoning(lon: float, lat: float, client: httpx.AsyncClient):
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "MUNICNAME,ZONE,ZONEDESC,SHORTDESC,FAR,MAXHEIGHT,MINLOTSIZE,DENSITY,MIXUSE,GENRLLUTYPE,MAXLOTCOV",
        "returnGeometry": "false", "f": "json",
    }
    url = f"{MDC_ROOT}/MunicipalZone_gdb/FeatureServer/0/query"
    r = await client.get(url, params=params)
    r.raise_for_status()
    feats = (r.json().get("features") or [])
    if not feats:
        return None
    return {"county": "miami-dade", **(feats[0].get("attributes") or {})}


async def _query_bro_zoning(lon: float, lat: float, client: httpx.AsyncClient):
    """For Broward we only have BMSD (unincorporated) zoning. For city zoning we fall back to FLU."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    # Try BMSD zoning first
    bmsd_params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ZONING,DESCRIPTION,AREANAME,BCPA_USE,USE_CODE",
        "returnGeometry": "false", "f": "json",
    }
    url = f"{BRO_ROOT}/Broward_Municipal_Service_District_Zoning/FeatureServer/2/query"
    r = await client.get(url, params=bmsd_params)
    r.raise_for_status()
    feats = (r.json().get("features") or [])
    if feats:
        a = feats[0].get("attributes") or {}
        return {
            "county": "broward", "source": "BMSD zoning",
            "ZONE": a.get("ZONING"),
            "ZONEDESC": a.get("DESCRIPTION"),
            "MUNICNAME": a.get("AREANAME") or "Unincorporated Broward",
        }
    # Fall back to FLU
    flu_params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "BPC_BCFUTU,SLUC1,DENSITY",
        "returnGeometry": "false", "f": "json",
    }
    url = f"{BRO_ROOT}/FutureLandUse/FeatureServer/0/query"
    r = await client.get(url, params=flu_params)
    r.raise_for_status()
    feats = (r.json().get("features") or [])
    if not feats:
        return None
    a = feats[0].get("attributes") or {}
    sluc = a.get("SLUC1")
    bpc = a.get("BPC_BCFUTU")
    return {
        "county": "broward", "source": "Broward Future Land Use (city zoning not in unified service)",
        "ZONE": str(sluc) if sluc is not None else (str(bpc) if bpc is not None else "—"),
        "ZONEDESC": "Broward FLU code " + (str(sluc) if sluc is not None else str(bpc or "")),
        "DENSITY": str(a.get("DENSITY") or "").strip() or None,
        "MUNICNAME": "Broward County",
    }


async def _query_city_zoning(lon: float, lat: float, client: httpx.AsyncClient):
    """Generic national zoning: try harvested city services near the point, return the
    first hit with definition (desc) + any FAR/height/density standards + bonus info."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    # Cap the serial fan-out at the 6 nearest cities — a regional outage must not
    # stack N×8s timeouts on a single click.
    for c in _nearby_cities(CITY_ZONING, lon, lat)[:6]:
        if not c.get("url") or not c.get("code"):
            continue  # tolerate malformed harvested entries
        fields = [c["code"]] + [c[k] for k in ("desc", "far", "height", "density") if c.get(k)]
        params = {
            "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(fields),
            "returnGeometry": "false", "f": "json",
        }
        try:
            r = await client.get(f"{c['url']}/query", params=params, timeout=8.0)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError):
            continue
        if not feats:
            continue
        a = feats[0].get("attributes") or {}
        zone = a.get(c["code"])
        if zone in (None, "", " "):
            continue
        muni = f"{c.get('city')}, {c.get('state')}"
        return {
            "found": True, "county": (c.get("city") or "").lower().replace(" ", "-"),
            "source": f"{muni} zoning", "state": c.get("state"),
            "ZONE": str(zone).strip(),
            "ZONEDESC": (str(a.get(c["desc"])).strip() if c.get("desc") and a.get(c["desc"]) else None),
            "FAR": (str(a.get(c["far"])).strip() if c.get("far") and a.get(c["far"]) not in (None, "", 0) else None),
            "MAXHEIGHT": (a.get(c["height"]) if c.get("height") and a.get(c["height"]) not in (None, "", 0) else None),
            "DENSITY": (str(a.get(c["density"])).strip() if c.get("density") and a.get(c["density"]) not in (None, "", 0) else None),
            "MUNICNAME": muni,
            "code_link": _code_link(c.get("city") or "", c.get("state") or "", str(zone).strip()),
            "bonuses": BONUS_NOTES.get(c.get("state")),
        }
    return None


@app.get("/api/zoning")
async def zoning_at_point(lon: float = Query(...), lat: float = Query(...)):
    county = county_for_point(lat, lon)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            if county == "miami-dade":
                z = await _query_mdc_zoning(lon, lat, client)
                if not z:  # in the lat/lon box but no MDC polygon (Keys/water/ROW)
                    county = "statewide"
            elif county == "broward":
                z = await _query_bro_zoning(lon, lat, client)
                if not z:  # point in the lat/lon box but not actually in Broward
                    county = "statewide"
            if county == "statewide":
                # Outside tri-county. Get the statewide parcel (use code + county no),
                # then try a real metro zoning service if the parcel's county has one.
                # When the click hits a street/ROW (no FDOR parcel), fall back to
                # county bounding boxes to decide which metro adapters to try.
                # Short timeout: a slow FDOR no-parcel scan must not starve the
                # metro adapters of the client's time budget.
                # Total deadline for the statewide chain — a regional outage must not
                # stack serial adapter timeouts into a minutes-long click.
                deadline = time.monotonic() + 12.0
                try:
                    p = await _query_statewide_parcel(lon, lat, client, timeout=6.0)
                except (httpx.HTTPError, ValueError):
                    p = None
                co_no = p.get("co_no") if p else None
                candidates = [co_no] if co_no in METRO_ZONING else _counties_by_bbox(lon, lat)
                for cand in candidates:
                    if time.monotonic() > deadline:
                        break
                    mz = await _query_metro_zoning(cand, lon, lat, client)
                    if mz:
                        return {"found": True,
                                "county": (p.get("county") if p else county_name(cand).lower().replace(" ", "-")),
                                **mz}
                # Outside FL (or no FL metro hit): try harvested national city zoning.
                cz = None
                if time.monotonic() <= deadline:
                    cz = await _query_city_zoning(lon, lat, client)
                if cz:
                    return cz
                # Fall back to DOR use code as the land-use signal (FL only).
                if p and p.get("use_code"):
                    return {
                        "found": True,
                        "county": p.get("county"),
                        "source": "Assessor land-use code (tax classification — NOT zoning; no published zoning service here)",
                        "ZONE": p.get("use_code"),
                        "ZONEDESC": p.get("use_description"),
                        "MUNICNAME": p.get("county_name"),
                        "is_use_code": True,
                    }
                return {"found": False, "county": county}
        except (httpx.HTTPError, ValueError) as e:
            return {"found": False, "county": county, "message": str(e)}
    if not z:
        return {"found": False, "county": county}
    return {"found": True, **z}


# ---------- FEMA National Flood Hazard Layer (statewide / national) ----------

@app.get("/api/flood")
async def flood_at_point(lon: float = Query(...), lat: float = Query(...)):
    """Query FEMA NFHL flood hazard zone at a point. Works anywhere in the US."""
    geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DEPTH,V_DATUM",
        "returnGeometry": "false", "f": "json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(f"{FEMA_NFHL}/query", params=params)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError) as e:
            return {"found": False, "message": str(e)}
    if not feats:
        return {"found": False}
    a = feats[0].get("attributes") or {}
    bfe = a.get("STATIC_BFE")
    return {
        "found": True,
        "zone": a.get("FLD_ZONE"),
        "subtype": a.get("ZONE_SUBTY"),
        "sfha": a.get("SFHA_TF") == "T",
        "bfe": bfe if (bfe is not None and bfe != -9999) else None,
        "datum": a.get("V_DATUM"),
    }


# ---------- Miami-Dade Property Appraiser deep dive ----------

@app.get("/api/property-details")
async def property_details(folio: str = Query(..., min_length=10)):
    """Full Miami-Dade Property Appraiser record: sales history, owner history,
    assessment 3-year trend, building details, additional district memberships."""
    clean = folio.replace("-", "").replace(" ", "")
    params = {
        "Operation": "GetPropertySearchByFolio",
        "folioNumber": clean,
        "clientAppName": "PropertySearch",
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            r = await client.get(PA_PROXY, params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"PA proxy error: {e}")
        try:
            d = r.json()
        except ValueError:
            raise HTTPException(502, "PA proxy returned non-JSON")

    # Normalize what we care about; pass everything else under `raw`.
    pi = d.get("PropertyInfo") or {}
    owners = d.get("OwnerInfos") or []
    sales = d.get("SalesInfos") or []
    assess = (d.get("Assessment") or {}).get("AssessmentInfos") or []
    taxable = (d.get("Taxable") or {}).get("TaxableInfos") or []
    benefit = (d.get("Benefit") or {}).get("BenefitInfos") or []
    bldg = (d.get("Building") or {}).get("BuildingInfos") or []
    land = (d.get("Land") or {}).get("Landlines") or []
    legal = (d.get("LegalDescription") or {}).get("Description") or []
    mailing = d.get("MailingAddress") or {}
    site = d.get("SiteAddress") or [{}]
    district = d.get("District")
    if not isinstance(district, dict):
        district = {}

    return {
        # found=False → the folio query returned no rows (all-null skeleton follows,
        # keeping every key so existing consumers don't break).
        "found": bool(pi.get("FolioNumber")),
        "folio": pi.get("FolioNumber"),
        "property": {
            "year_built": pi.get("YearBuilt"),
            "effective_year": pi.get("EffectiveYearBuilt"),
            "bedrooms": pi.get("BedroomCount"),
            "bathrooms": pi.get("BathroomCount"),
            "half_baths": pi.get("HalfBathroomCount"),
            "floors": pi.get("FloorCount"),
            "units": pi.get("UnitCount"),
            "actual_area_sf": pi.get("BuildingActualArea"),
            "base_area_sf": pi.get("BuildingBaseArea"),
            "effective_area_sf": pi.get("BuildingEffectiveArea"),
            "heated_area_sf": pi.get("BuildingHeatedArea"),
            "lot_size_sf": pi.get("LotSize"),
            "dor_code": pi.get("DORCode"),
            "dor_description": pi.get("DORDescription"),
            "primary_zone": pi.get("PrimaryZone"),
            "primary_zone_desc": pi.get("PrimaryZoneDescription"),
            "neighborhood": pi.get("NeighborhoodDescription"),
            "subdivision": pi.get("SubdivisionDescription"),
            "municipality": pi.get("Municipality"),
            "parent_folio": pi.get("ParentFolio"),
            "plat_book": pi.get("PlatBook"),
            "plat_page": pi.get("PlatPage"),
            "status": pi.get("Status"),
        },
        "owners": [{"name": o.get("Name"), "pct_owned": o.get("PercentageOwn")} for o in owners],
        "mailing_address": {
            "line1": mailing.get("Address1"),
            "line2": mailing.get("Address2"),
            "city": mailing.get("City"),
            "state": mailing.get("State"),
            "zip": mailing.get("ZipCode"),
            "country": mailing.get("Country"),
        },
        "site_address": [{"line1": s.get("Address"), "unit": s.get("Unit"), "city": s.get("City"), "zip": s.get("ZipCode")} for s in (site if isinstance(site, list) else [site])],
        "legal_description": legal,
        "sales": [
            {
                "date": s.get("DateOfSale"),
                "price": s.get("SalePrice") or s.get("DocumentStamps"),
                "grantor": " / ".join(filter(None, [s.get("GrantorName1"), s.get("GrantorName2")])),
                "grantee": " / ".join(filter(None, [s.get("GranteeName1"), s.get("GranteeName2")])),
                "or_book": s.get("OfficialRecordBook"),
                "or_page": s.get("OfficialRecordPage"),
                "qualification": s.get("QualificationDescription"),
                "qualified_flag": s.get("QualifiedFlag"),
            } for s in sales
        ],
        "assessment_history": [
            {
                "year": a.get("Year"),
                "land_value": a.get("LandValue"),
                "building_value": a.get("BuildingOnlyValue"),
                "extra_feature_value": a.get("ExtraFeatureValue"),
                "total_value": a.get("TotalValue"),
                "assessed_value": a.get("AssessedValue"),
            } for a in assess
        ],
        "taxable_history": [
            {
                "year": t.get("Year"),
                "county_taxable": t.get("CountyTaxableValue"),
                "school_taxable": t.get("SchoolTaxableValue"),
                "city_taxable": t.get("CityTaxableValue"),
                "regional_taxable": t.get("RegionalTaxableValue"),
                "county_exemption": t.get("CountyExemptionValue"),
                "school_exemption": t.get("SchoolExemptionValue"),
            } for t in taxable
        ],
        "exemptions": [{"name": b.get("Description"), "amount": b.get("Value"), "type": b.get("Type")} for b in benefit],
        "buildings": bldg,
        "land_lines": land,
        "district_flags": {
            "cra": district.get("CRA"),
            "opportunity_zone": district.get("OPPZ"),
            "enterprise_zone": district.get("EZ"),
            "empowerment_zone": district.get("EMPZ"),
            "community_dev": district.get("CDD"),
            "urban_dev_boundary": district.get("UDB"),
        },
    }


# ---------- Condo / multi-unit building roster (Miami-Dade) ----------

# The Property Appraiser's PARENT_FOLIO links every saleable unit to a shared
# building/common-element folio — one WHERE clause returns the whole roster,
# no per-unit calls needed for unit/owner/sqft.
CONDO_ENRICH_CAP = 400  # PA deep-dive is a per-unit call (~0.3-0.5s); cap keeps worst case ~10s

_LLC_RX = re.compile(r"\b(LLC|L\.L\.C\.|LP|L\.P\.|INC|CORP|TRUST|TRS?\b|LTD|HOLDINGS|"
                     r"PARTNERS|ENTERPRISES|PROPERTIES|CAPITAL|GROUP)\b", re.I)


def _natural_unit_key(u: str):
    """Sort '2' < '10' < '101A' < 'CU 1' — numeric-prefix aware, not lexicographic."""
    u = (u or "").strip()
    m = re.match(r"(\d+)(.*)", u)
    return (0, int(m.group(1)), m.group(2)) if m else (1, 0, u)


async def _condo_roster(building_folio: str, client: httpx.AsyncClient) -> list[dict]:
    params = {
        "where": f"PARENT_FOLIO='{building_folio}'",
        "outFields": ("FOLIO,TRUE_SITE_UNIT,TRUE_SITE_ADDR,TRUE_OWNER1,TRUE_OWNER2,"
                      "TRUE_MAILING_STATE,BUILDING_ACTUAL_AREA,YEAR_BUILT"),
        "resultRecordCount": "2000", "f": "json",
    }
    r = await client.get(f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query", params=params)
    r.raise_for_status()
    return r.json().get("features") or []


def _clean_folio(folio: str, field: str = "folio") -> str:
    clean = folio.replace("-", "").replace(" ", "")
    if not clean.isdigit() or not (10 <= len(clean) <= 13):
        raise HTTPException(422, f"{field} must be 10-13 digits (dashes/spaces ok)")
    return clean


@app.get("/api/condo-units")
async def condo_units(folio: str = Query(..., min_length=10)):
    """Every unit sharing this Miami-Dade folio's PARENT_FOLIO (condo/apartment
    building roster) — unit, owner, sqft, year built — in one fast query."""
    clean = _clean_folio(folio)
    async with httpx.AsyncClient(timeout=15.0) as client:
        self_r = await client.get(f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query", params={
            "where": f"FOLIO='{clean}'", "outFields": "FOLIO,PARENT_FOLIO,TRUE_SITE_ADDR", "f": "json"})
        self_r.raise_for_status()
        self_feats = self_r.json().get("features") or []
        self_a = (self_feats[0].get("attributes") if self_feats else {}) or {}
        building_folio = self_a.get("PARENT_FOLIO") or None
        feats = await _condo_roster(building_folio, client) if building_folio else []
        if not feats:
            # Either `clean` IS the master/common-element folio, or this genuinely
            # isn't part of a multi-unit building.
            building_folio = clean
            feats = await _condo_roster(building_folio, client)
        if not feats:
            return {"is_condo": False}
        # The master/common-element row's address has no unit suffix — a real
        # unit row's TRUE_SITE_ADDR often embeds its own unit ("... BLVD 2001").
        master_addr = None
        try:
            master_r = await client.get(f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query", params={
                "where": f"FOLIO='{building_folio}'", "outFields": "TRUE_SITE_ADDR", "f": "json"})
            master_r.raise_for_status()
            master_feats = master_r.json().get("features") or []
            master_addr = (master_feats[0].get("attributes") or {}).get("TRUE_SITE_ADDR") if master_feats else None
        except (httpx.HTTPError, ValueError):
            pass

    units = []
    for f in feats:
        a = f.get("attributes") or {}
        owner = " / ".join(filter(None, [a.get("TRUE_OWNER1"), a.get("TRUE_OWNER2")]))
        units.append({
            "folio": a.get("FOLIO"),
            "unit": a.get("TRUE_SITE_UNIT") or "—",
            "owner": owner or None,
            "is_llc": bool(_LLC_RX.search(owner)),
            "out_of_state": bool(a.get("TRUE_MAILING_STATE")) and a.get("TRUE_MAILING_STATE") != "FL",
            "sqft": a.get("BUILDING_ACTUAL_AREA") or None,
            "year_built": int(a["YEAR_BUILT"]) if a.get("YEAR_BUILT") else None,
        })
    units.sort(key=lambda u: _natural_unit_key(u["unit"]))

    owner_counts: dict[str, int] = {}
    for u in units:
        if u["owner"]:
            owner_counts[u["owner"]] = owner_counts.get(u["owner"], 0) + 1
    multi_unit_owners = sorted(
        ({"owner": o, "count": n} for o, n in owner_counts.items() if n > 1),
        key=lambda x: -x["count"])[:15]

    sqfts = [u["sqft"] for u in units if u["sqft"]]
    building_address = master_addr or self_a.get("TRUE_SITE_ADDR") or (feats[0].get("attributes") or {}).get("TRUE_SITE_ADDR")

    return {
        "is_condo": True,
        "building_folio": building_folio,
        "building_address": building_address,
        "unit_count": len(units),
        "units": units,
        "summary": {
            "total_sf": round(sum(sqfts)) if sqfts else None,
            "avg_sf": round(sum(sqfts) / len(sqfts)) if sqfts else None,
            "llc_owned": sum(1 for u in units if u["is_llc"]),
            "out_of_state_owned": sum(1 for u in units if u["out_of_state"]),
            "distinct_owners": len(owner_counts),
            "multi_unit_owners": multi_unit_owners,
        },
    }


@app.get("/api/condo-unit-values")
async def condo_unit_values(building_folio: str = Query(..., min_length=10)):
    """Per-unit assessed value + last sale via the PA deep-dive proxy. The free
    proxy handles concurrency-15 bursts fine (~0.4s/call) but not hundreds at
    once unbounded, hence the cap — surfaced to the client as `truncated`."""
    clean = _clean_folio(building_folio, "building_folio")
    async with httpx.AsyncClient(timeout=15.0) as client:
        feats = await _condo_roster(clean, client)
    if not feats:
        return {"values": {}, "enriched_count": 0, "truncated": False}
    folios = [a for a in (f.get("attributes", {}).get("FOLIO") for f in feats) if a]
    truncated = len(folios) > CONDO_ENRICH_CAP
    folios = folios[:CONDO_ENRICH_CAP]

    sem = asyncio.Semaphore(20)  # measured ~7-8 units/sec at this concurrency; 400 units ~50s

    async def one(client: httpx.AsyncClient, unit_folio: str):
        async with sem:
            try:
                r = await client.get(PA_PROXY, params={
                    "Operation": "GetPropertySearchByFolio", "folioNumber": unit_folio,
                    "clientAppName": "PropertySearch"}, timeout=12.0)
                r.raise_for_status()
                d = r.json()
            except (httpx.HTTPError, ValueError):
                return unit_folio, None
            pi = d.get("PropertyInfo") or {}
            sales = d.get("SalesInfos") or []
            assess = (d.get("Assessment") or {}).get("AssessmentInfos") or []
            last_sale = sales[0] if sales else {}
            cur_assess = assess[0] if assess else {}
            return unit_folio, {
                "beds": pi.get("BedroomCount"), "baths": pi.get("BathroomCount"),
                "assessed_value": cur_assess.get("AssessedValue"),
                "total_value": cur_assess.get("TotalValue"),
                "assess_year": cur_assess.get("Year"),
                "last_sale_price": last_sale.get("SalePrice"),
                "last_sale_date": last_sale.get("DateOfSale"),
            }

    async with httpx.AsyncClient(timeout=15.0) as client:
        results = await asyncio.gather(*[one(client, f) for f in folios])
    values = {f: v for f, v in results if v is not None}
    return {"values": values, "enriched_count": len(values), "truncated": truncated}


# ---------- Live Local Act: tallest height within 1 mile ----------

@app.get("/api/live-local")
async def live_local(lon: float = Query(..., ge=-180, le=180),
                     lat: float = Query(..., ge=-90, le=90),
                     muni: str | None = Query(None)):
    """Florida Live Local Act (SB 102, 2023) lets qualifying affordable housing
    developments use the highest currently-allowed height for a commercial or
    residential zone within 1 mile of the proposed site.

    The benchmark is restricted to the site's OWN jurisdiction: the entitlement
    is granted by the local government the parcel sits in, so a neighboring
    city's heights don't apply — and mixing jurisdictions also mixes units
    (City of Miami stores MAXHEIGHT in stories, some cities in feet). When `muni`
    isn't supplied we resolve it from the polygon under the point."""
    url = f"{MDC_ROOT}/MunicipalZone_gdb/FeatureServer/0/query"
    # 1 mile in degrees latitude
    pad_lat = 5280.0 / 364320.0
    pad_lon = 5280.0 / (364320.0 * max(math.cos(math.radians(lat)), 0.01))
    envelope = {
        "xmin": lon - pad_lon, "ymin": lat - pad_lat,
        "xmax": lon + pad_lon, "ymax": lat + pad_lat,
        "spatialReference": {"wkid": 4326},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Resolve the site's jurisdiction from the polygon under the point.
        if not muni:
            try:
                pr = await client.get(url, params={
                    "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
                    "geometryType": "esriGeometryPoint", "inSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects", "where": "1=1",
                    "outFields": "MUNICNAME", "returnGeometry": "false", "f": "json"})
                pf = (pr.json().get("features") or [])
                if pf:
                    muni = (pf[0].get("attributes") or {}).get("MUNICNAME")
            except (httpx.HTTPError, ValueError):
                muni = None

        where = "MAXHEIGHT > 0"
        if muni:
            where += f" AND MUNICNAME='{str(muni).replace(chr(39), chr(39)*2)}'"
        params = {
            "geometry": json.dumps(envelope),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "where": where,
            "outFields": "MUNICNAME,ZONE,ZONEDESC,MAXHEIGHT,FAR,DENSITY,GENRLLUTYPE",
            "returnGeometry": "false",
            "outSR": "4326",
            "f": "json",
        }
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, str(e))

    feats = data.get("features") or []
    if not feats:
        # Only blame Broward when the click is actually in Broward's box — the same
        # miss fires for Denver, NYC, or open ocean.
        in_broward = lon >= -80.95 and MDC_LAT_MAX < lat <= BRO_LAT_MAX
        note = ("No zoning polygons with MAXHEIGHT in radius (likely Broward — Live Local "
                "height benchmark not yet implemented for that county)"
                if in_broward else
                "Live Local screening needs a Florida parcel with zoned height data.")
        return {"found": False, "note": note}

    # Find the polygon with max height
    by_height = sorted(
        (f.get("attributes") or {} for f in feats),
        key=lambda a: a.get("MAXHEIGHT") or 0,
        reverse=True,
    )
    top = by_height[0]
    # Also compute distribution: count of distinct zones at this height
    max_h = top.get("MAXHEIGHT") or 0
    matching_at_top = [a for a in by_height if (a.get("MAXHEIGHT") or 0) == max_h]
    # Distinct zone codes in the radius (preview)
    seen = set()
    distinct_zones = []
    for a in by_height:
        z = a.get("ZONE")
        if z and z not in seen:
            seen.add(z)
            distinct_zones.append({
                "zone": z, "desc": a.get("ZONEDESC"), "muni": a.get("MUNICNAME"),
                "max_height_ft": a.get("MAXHEIGHT"), "far": a.get("FAR"), "density": a.get("DENSITY"),
                "category": a.get("GENRLLUTYPE"),
            })

    return {
        "found": True,
        "max_height_ft": max_h,
        "jurisdiction": muni,
        "matching_zones_at_max": [
            {"zone": a.get("ZONE"), "desc": a.get("ZONEDESC"), "muni": a.get("MUNICNAME"),
             "far": a.get("FAR"), "density": a.get("DENSITY")}
            for a in matching_at_top[:5]
        ],
        "all_zones_in_radius": distinct_zones[:50],
        "total_polys_checked": len(feats),
        "radius_miles": 1.0,
    }


# ---------- development-capacity analysis ----------

RULES_PATH = DATA_DIR / "zoning_rules.json"
_RULES_CACHE: dict | None = None


def _load_rules() -> dict:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        try:
            _RULES_CACHE = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _RULES_CACHE = {}
    return _RULES_CACHE


def _fnum(v):
    if v in (None, "", " "):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _match_rule_zone(zones_cfg: dict, zone: str):
    """Match a GIS ZONE value against rulebook keys. Miami 21 codes carry a
    frontage suffix (-O/-L/-R) and an FLR sub-designation letter attached to the
    story number without a dash (T6-48A-O). Generate candidates by peeling the
    dash-suffixes AND stripping a trailing letter off the last segment, so
    T6-48A-O -> T6-48A -> T6-48 (match) -> T6."""
    z = (zone or "").strip().upper()
    cands: list[str] = []
    parts = z
    while parts:
        cands.append(parts)
        segs = parts.split("-")
        stripped = re.sub(r"[A-Za-z]+$", "", segs[-1])
        if stripped and stripped != segs[-1]:
            cands.append("-".join(segs[:-1] + [stripped]))
        if "-" not in parts:
            break
        parts = parts.rsplit("-", 1)[0]
    for c in cands:
        if c in zones_cfg:
            return c, zones_cfg[c]
    return None, None


def _dev_envelope(lot_sf, far, density, cov_pct, height_raw, stories_override=None,
                  far_mult=1.0, assumptions=None):
    """Screening math for a zoning envelope. height_raw <= 100 reads as stories,
    larger as feet (per assumptions.ft_per_story). Returns every cap separately
    plus the min and which constraint binds — screening, not entitlement advice."""
    a = assumptions or {}
    ftps = a.get("ft_per_story") or 11
    avg_unit = a.get("avg_unit_sf") or 1000
    eff = a.get("efficiency") or 0.85

    stories = feet = None
    if stories_override is not None:
        stories = float(stories_override)
        feet = stories * ftps
    elif height_raw is not None:
        if height_raw <= 100:
            stories = float(height_raw)
            feet = stories * ftps
        else:
            feet = float(height_raw)
            stories = round(feet / ftps)

    acres = (lot_sf / 43560.0) if lot_sf else None
    footprint = (cov_pct / 100.0 * lot_sf) if (cov_pct and lot_sf) else None
    far_eff = (far * far_mult) if far else None
    far_gsf = (far_eff * lot_sf) if (far_eff and lot_sf) else None
    massing_gsf = (footprint * stories) if (footprint and stories) else None

    gsf_caps = {k: v for k, v in (("FAR", far_gsf), ("height x footprint", massing_gsf)) if v}
    env_gsf = min(gsf_caps.values()) if gsf_caps else None
    gsf_binding = min(gsf_caps, key=gsf_caps.get) if gsf_caps else None

    units_density = math.floor(density * acres) if (density and acres) else None
    units_envelope = math.floor(env_gsf * eff / avg_unit) if env_gsf else None
    unit_caps = {k: v for k, v in (("density", units_density), ("envelope", units_envelope)) if v}
    max_units = min(unit_caps.values()) if unit_caps else None
    units_binding = min(unit_caps, key=unit_caps.get) if unit_caps else None

    return {
        "stories": stories, "height_ft_est": feet,
        "far_effective": far_eff, "max_far_gsf": round(far_gsf) if far_gsf else None,
        "footprint_sf": round(footprint) if footprint else None,
        "massing_gsf": round(massing_gsf) if massing_gsf else None,
        "envelope_gsf": round(env_gsf) if env_gsf else None,
        "gsf_binding": gsf_binding,
        "units_by_density": units_density, "units_by_envelope": units_envelope,
        "max_units": max_units, "units_binding": units_binding,
    }


# Live Local benchmark caches — jurisdiction maxima never change within a server
# run, and the 1-mile height envelope barely moves across a ~400 ft grid cell.
_LL_JUR_CACHE: dict[str, tuple[float | None, float | None]] = {}
_LL_HEIGHT_CACHE: dict[tuple[float, float], dict] = {}


def _ll_zone_eligible(primary: dict) -> bool:
    """Live Local requires a commercial, industrial, or mixed-use site. MDC's
    GENRLLUTYPE codes residential-only zones as RSF/RMF; MIXUSE is authoritative
    where set."""
    if str(primary.get("MIXUSE") or "").strip().upper() in ("YES", "Y", "TRUE"):
        return True
    lu = str(primary.get("GENRLLUTYPE") or "").strip().upper()
    if lu:
        return lu not in ("RSF", "RMF", "R")
    return False  # no land-use signal — don't advertise an entitlement we can't support


async def _live_local_scenario(muni: str | None, lon: float, lat: float,
                               client: httpx.AsyncClient) -> dict | None:
    """Live Local entitlement benchmarks: jurisdiction-wide max density and FAR
    (statute: highest allowed; FAR floor is 150% of highest), plus the existing
    1-mile height benchmark. Miami-Dade municipalities only for now."""
    if not muni:
        return None
    esc = str(muni).replace("'", "''")

    async def distinct_max(field):
        params = {"where": f"MUNICNAME='{esc}'", "outFields": field,
                  "returnDistinctValues": "true", "returnGeometry": "false", "f": "json"}
        try:
            r = await client.get(f"{MDC_ROOT}/MunicipalZone_gdb/FeatureServer/0/query",
                                 params=params, timeout=12.0)
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError):
            return None
        vals = [_fnum((f.get("attributes") or {}).get(field)) for f in feats]
        vals = [v for v in vals if v is not None and 0 < v < 100000]
        return max(vals) if vals else None

    jur_key = str(muni).upper()
    if jur_key in _LL_JUR_CACHE:
        density_max, far_max = _LL_JUR_CACHE[jur_key]
    else:
        density_max, far_max = await asyncio.gather(distinct_max("DENSITY"), distinct_max("FAR"))
        if density_max is not None or far_max is not None:  # don't cache a total miss
            _LL_JUR_CACHE[jur_key] = (density_max, far_max)

    h_key = (round(lon, 3), round(lat, 3), str(muni or ""))
    height = _LL_HEIGHT_CACHE.get(h_key)
    if height is None:
        try:
            # Benchmark within the parcel's own jurisdiction (units + legal scope).
            height = await live_local(lon=lon, lat=lat, muni=muni)
        except HTTPException:
            height = {"found": False}
        if height.get("found"):
            if len(_LL_HEIGHT_CACHE) > 4096:
                _LL_HEIGHT_CACHE.clear()
            _LL_HEIGHT_CACHE[h_key] = height
    if not (density_max or far_max or height.get("found")):
        return None
    rules_ll = _load_rules().get("live_local") or {}
    # Statutory height entitlement is the benchmark OR 3 stories, whichever is
    # higher (SB 328). MDC MAXHEIGHT is expressed in stories.
    bench_stories = None
    if height.get("found") and height.get("max_height_ft") is not None:
        raw = height["max_height_ft"]
        bench_stories = raw if raw <= 100 else round(raw / 11)
    height_stories = max(3, bench_stories) if bench_stories is not None else 3
    return {
        "label": rules_ll.get("label") or "FL Live Local Act",
        "height_benchmark": height if height.get("found") else None,
        "height_stories": height_stories,
        "height_floored": bench_stories is not None and bench_stories < 3,
        "density_max_du_ac": density_max,
        "density_note": ("GIS jurisdiction max; density-increase overlays and bonus "
                         "programs may allow more — verify the true maximum."),
        "far_max_in_jurisdiction": far_max,
        "far_150pct": round(far_max * 1.5, 2) if far_max else None,
        "requirements": rules_ll.get("requirements") or [],
        "entitlements": rules_ll.get("entitlements") or [],
        "source_url": rules_ll.get("source_url"),
    }


class DevParcel(BaseModel):
    lon: float
    lat: float
    folio: str | None = None
    lot_size_sf: float | None = None
    # Client-known zoning attrs (from the click's /api/zoning call) — when present
    # with a ZONE, the server skips its own lookup for this parcel.
    zoning: dict | None = None


class DevAnalysisIn(BaseModel):
    parcels: list[DevParcel]


@app.post("/api/dev-analysis")
async def dev_analysis(body: DevAnalysisIn):
    """Development-capacity screening for one parcel or an assemblage. Base
    envelope from GIS zoning attributes (works for every jurisdiction whose
    zoning service publishes dimensions — all of Miami-Dade's municipalities);
    setbacks/bonuses layered from data/zoning_rules.json where curated; Live
    Local shown as a secondary scenario for FL sites."""
    parcels = body.parcels[:20]
    if not parcels:
        raise HTTPException(400, "provide at least one parcel")

    async def zoning_for(p: DevParcel):
        if p.zoning and p.zoning.get("ZONE"):
            return {"found": True, **p.zoning}
        return await zoning_at_point(lon=p.lon, lat=p.lat)

    zonings = await asyncio.gather(*[zoning_for(p) for p in parcels])
    warnings: list[str] = []

    lots = [p.lot_size_sf for p in parcels]
    if any(v is None for v in lots):
        warnings.append(f"{sum(1 for v in lots if v is None)} parcel(s) missing lot size — "
                        "site area understates the true total.")
    lot_sf = sum(_fnum(v) or 0 for v in lots) or None

    zones = []
    for z in zonings:
        if z.get("found") and z.get("ZONE"):
            zones.append(str(z.get("ZONE")).strip())
    primary = next((z for z in zonings if z.get("found")), None)
    if primary is None:
        return {"found": False, "message": "No zoning data at any of the site's parcels."}
    if primary.get("is_use_code"):
        return {"found": False, "zoning": primary,
                "message": "Only an assessor land-use code is available here — no zoning "
                           "dimensions to analyze."}
    distinct_zones = sorted(set(zones))
    if len(distinct_zones) > 1:
        warnings.append(f"Mixed zoning across the site ({', '.join(distinct_zones)}) — "
                        "analysis uses the first parcel's zone; a unified development "
                        "typically needs the most restrictive or a rezoning.")

    muni = primary.get("MUNICNAME")
    zone = str(primary.get("ZONE") or "").strip()
    far = _fnum(primary.get("FAR"))
    density = _fnum(primary.get("DENSITY"))
    cov = _fnum(primary.get("MAXLOTCOV"))
    height_raw = _fnum(primary.get("MAXHEIGHT"))

    rules = _load_rules()
    assumptions = rules.get("assumptions") or {}
    jur = (rules.get("jurisdictions") or {}).get(str(muni or "").upper()) or {}
    rule_key, rule = _match_rule_zone(jur.get("zones") or {}, zone)

    # Rulebook fills GIS gaps (never overrides a GIS number).
    if far is None and rule and rule.get("flr_base"):
        far = _fnum(rule["flr_base"])
    stories_override = None
    if height_raw is None and rule and rule.get("stories_base"):
        stories_override = rule["stories_base"]

    base = _dev_envelope(lot_sf, far, density, cov, height_raw,
                         stories_override=stories_override, assumptions=assumptions)

    if not any([far, density, height_raw, stories_override]):
        warnings.append("This jurisdiction's zoning service publishes no dimensional "
                        "attributes — only the zone code is known. Envelope math unavailable.")

    bonuses = (rule or {}).get("bonuses") or []
    setbacks = (rule or {}).get("setbacks_ft")
    if not rule and muni:
        warnings.append(f"No curated rulebook entry for {muni} / {zone} yet — "
                        "setbacks and bonus programs not shown (GIS base envelope only).")

    # Live Local: FL statute, benchmarks resolvable for Miami-Dade munis; only
    # meaningful on commercial/industrial/mixed-use sites — never advertise a
    # residential-zone entitlement that doesn't exist.
    ll = None
    if primary.get("county") == "miami-dade":
        if _ll_zone_eligible(primary):
            async with httpx.AsyncClient(timeout=15.0) as client:
                ll = await _live_local_scenario(muni, parcels[0].lon, parcels[0].lat, client)
            if ll:
                ll["eligible"] = True
        else:
            ll = {"eligible": False,
                  "note": "Not Live Local-eligible: site is zoned residential-only. The Act "
                          "applies to commercial, industrial, and mixed-use sites (SB 102/328)."}

    return {
        "found": True,
        "site": {
            "parcels": len(parcels), "lot_sf": round(lot_sf) if lot_sf else None,
            "acres": round(lot_sf / 43560.0, 3) if lot_sf else None,
            "zones": distinct_zones, "muni": muni,
            "folios": [p.folio for p in parcels if p.folio],
        },
        "zoning": {"zone": zone, "desc": primary.get("ZONEDESC"), "muni": muni,
                   "county": primary.get("county"),
                   "far": far, "density_du_ac": density, "lot_coverage_pct": cov,
                   "max_height_raw": height_raw,
                   "source": "county GIS zoning attributes",
                   # click-through to the governing code text: the curated source
                   # where we have one, else a targeted code search for this zone
                   "code_url": (jur.get("source_url") if rule else
                                _code_link(str(muni or ""), "FL" if primary.get("county") else "", zone))},
        "base": base,
        "setbacks_ft": setbacks,
        "bonuses": bonuses,
        "rulebook": ({"matched": f"{str(muni).upper()}/{rule_key}",
                      "code_name": jur.get("code_name"),
                      "source_url": jur.get("source_url"),
                      "verify": bool((rule or {}).get("verify"))} if rule else None),
        "live_local": ll,
        "assumptions": assumptions,
        "warnings": warnings,
    }


# ---------- transit proximity ----------

_TRANSIT_CACHE: dict[str, list[dict]] | None = None

# Beyond this a "nearest station" is noise, not context — the layers are Miami-Dade
# only, so a Denver click must not surface a Miami station 1,700 miles away.
MAX_TRANSIT_FT = 25 * 5280  # 25 miles


def _load_transit() -> dict[str, list[dict]]:
    global _TRANSIT_CACHE
    if _TRANSIT_CACHE is not None:
        return _TRANSIT_CACHE
    out: dict[str, list[dict]] = {}
    for key, file in (("metrorail", "mdc_metrorail_stations.geojson"), ("metromover", "mdc_metromover_stations.geojson")):
        fp = layer_path(file)
        stations: list[dict] = []
        try:
            if fp.exists():
                gj = json.loads(fp.read_text(encoding="utf-8"))
                for f in gj.get("features", []):
                    geom = f.get("geometry") or {}
                    if geom.get("type") != "Point":
                        continue
                    coords = geom.get("coordinates") or []
                    if len(coords) < 2:
                        continue
                    lon, lat = coords[0], coords[1]  # tolerate Z-enabled exports
                    props = f.get("properties") or {}
                    name = props.get("STATION") or props.get("NAME") or props.get("STATIONNAME") or f"{key} station"
                    stations.append({"name": name, "lat": lat, "lon": lon})
        except (OSError, ValueError, TypeError, KeyError):
            stations = []  # one bad/corrupt file degrades to "no data", never a 500
        out[key] = stations
    _TRANSIT_CACHE = out
    return out


NTM_STOPS_QUERY = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
                   "NTAD_National_Transit_Map_Stops/FeatureServer/0/query")

# GTFS route/stop types: 0 tram, 1 subway, 2 rail, 3 bus, 4 ferry, 5 cable,
# 6 gondola, 7 funicular, 11 trolleybus, 12 monorail.
_RAIL_STOP_TYPES = {"0", "1", "2", "5", "6", "7", "12"}
_BUS_STOP_TYPES = {"3", "11"}
_FERRY_STOP_TYPES = {"4"}


async def _ntm_nearest(lon: float, lat: float) -> dict:
    """Nearest transit stop per mode family from the BTS National Transit Map."""
    common = {
        "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "units": "esriSRUnit_Meter",
        "outFields": "stop_name,stop_type,stop_type_text", "returnGeometry": "true",
        "outSR": "4326", "f": "json",
    }

    async def q(client: httpx.AsyncClient, where: str, dist_m: int, n: int) -> list:
        r = await client.get(NTM_STOPS_QUERY, params={**common, "where": where,
                                                      "distance": str(dist_m),
                                                      "resultRecordCount": str(n)})
        r.raise_for_status()
        return r.json().get("features") or []

    out: dict = {"rail": None, "bus": None, "ferry": None}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            fixed, bus = await asyncio.gather(
                q(client, "stop_type NOT IN ('3','11')", 16093, 400),  # rail + ferry, 10 mi
                q(client, "stop_type IN ('3','11')", 2414, 400),       # bus, 1.5 mi
            )

        def best(feats: list, types: set) -> dict | None:
            cands = []
            for ft in feats:
                a = ft.get("attributes") or {}
                g = ft.get("geometry") or {}
                if str(a.get("stop_type")) not in types or g.get("x") is None or g.get("y") is None:
                    continue
                cands.append((round(_haversine_ft(lat, lon, g["y"], g["x"])), a))
            if not cands:
                return None
            feet, a = min(cands, key=lambda c: c[0])
            # BTS wraps mode text in literal quotes ('"Bus"') — strip them
            mode = (a.get("stop_type_text") or "").strip().strip('"') or "Transit"
            return {"name": (a.get("stop_name") or "").strip() or "Transit stop",
                    "feet": feet, "mode": mode}

        out["rail"] = best(fixed, _RAIL_STOP_TYPES)
        out["ferry"] = best(fixed, _FERRY_STOP_TYPES)
        out["bus"] = best(bus, _BUS_STOP_TYPES)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        pass  # national lookup is best-effort; the local Miami layers still answer
    return out


@app.get("/api/nearest-transit")
async def nearest_transit(lon: float = Query(...), lat: float = Query(...)):
    stations = _load_transit()
    result = {}
    any_near = False
    for key, group in stations.items():
        if not group:
            result[key] = None
            continue
        best = min(group, key=lambda s: _haversine_ft(lat, lon, s["lat"], s["lon"]))
        feet = round(_haversine_ft(lat, lon, best["lat"], best["lon"]))
        if feet > MAX_TRANSIT_FT:
            result[key] = None  # nearest station is irrelevantly far
            continue
        result[key] = {"name": best["name"], "feet": feet}
        any_near = True
    national = await _ntm_nearest(lon, lat)
    result.update(national)
    if any(national.values()):
        any_near = True
    result["available"] = any_near
    if not any_near:
        result["reason"] = "no_nearby_stations"
    return result


# ---------- address / folio search ----------

async def _mdc_search(q_norm: str, limit: int, client: httpx.AsyncClient,
                      force_address: bool = False):
    """Miami-Dade parcel search by folio or site address (parcel-precise, FL only)."""
    # A folio is digits-only (13 digits, optionally dash/space/dot separated). Merely
    # *containing* 10+ digits would hijack addresses like '19501 Biscayne Blvd … 33180'.
    stripped = re.sub(r"[\s\-\.]", "", q_norm)
    is_folio = not force_address and stripped.isdigit() and len(stripped) >= 10
    if is_folio:
        where = f"FOLIO LIKE '{stripped}%'"
    else:
        safe = q_norm.replace("'", "''")
        where = f"UPPER(TRUE_SITE_ADDR) LIKE '%{safe}%'"
    params = {
        "where": where,
        "outFields": "FOLIO,TRUE_SITE_ADDR,TRUE_SITE_CITY,TRUE_SITE_ZIP_CODE",
        "outSR": "4326", "returnGeometry": "true",
        "resultRecordCount": str(min(limit, 20)), "f": "geojson",
    }
    url = f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query"
    r = await client.get(url, params=params)
    r.raise_for_status()
    data = r.json()

    def centroid(geom):
        if not geom: return None, None
        if geom["type"] == "Polygon":
            ring = geom["coordinates"][0]
        elif geom["type"] == "MultiPolygon":
            ring = geom["coordinates"][0][0]
        else:
            return None, None
        if not ring: return None, None
        lons = [p[0] for p in ring]; lats = [p[1] for p in ring]
        return sum(lons) / len(lons), sum(lats) / len(lats)

    out = []
    for f in (data.get("features") or [])[:limit]:
        props = f.get("properties") or {}
        lon, lat = centroid(f.get("geometry"))
        if lon is None: continue
        out.append({
            "folio": props.get("FOLIO"), "address": props.get("TRUE_SITE_ADDR"),
            "city": props.get("TRUE_SITE_CITY"), "zip": props.get("TRUE_SITE_ZIP_CODE"),
            "lat": lat, "lon": lon, "source": "Miami-Dade parcel",
        })
    return out


async def _census_geocode(q: str, limit: int, client: httpx.AsyncClient):
    """National address search via the free US Census geocoder (works anywhere)."""
    params = {
        "address": q, "benchmark": "Public_AR_Current", "format": "json",
    }
    r = await client.get(
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
        params=params,
    )
    r.raise_for_status()
    matches = (r.json().get("result") or {}).get("addressMatches") or []
    out = []
    for m in matches[:limit]:
        c = m.get("coordinates") or {}
        comp = m.get("addressComponents") or {}
        if c.get("x") is None:
            continue
        out.append({
            "folio": None, "address": m.get("matchedAddress"),
            "city": comp.get("city"), "zip": comp.get("zip"),
            "lat": c.get("y"), "lon": c.get("x"), "source": "Census geocoder",
        })
    return out


@app.get("/api/search")
async def search(q: str = Query(..., min_length=3), limit: int = 8):
    """National address/folio search. A digits-only ≥10-digit query hits the
    Miami-Dade parcel index first (parcel-precise); everything else — including a
    folio query that matched nothing — runs the national Census geocoder and the
    MDC address index concurrently so any US address resolves and the map flies there."""
    q_norm = q.strip().upper()
    stripped = re.sub(r"[\s\-\.]", "", q_norm)
    is_folio = stripped.isdigit() and len(stripped) >= 10
    # 6s per upstream call; the two legs run in parallel so a search stays snappy.
    async with httpx.AsyncClient(timeout=6.0) as client:
        # 1) digits-only → Miami-Dade folio; a miss falls through instead of returning []
        if is_folio:
            try:
                folio_hits = await _mdc_search(q_norm, limit, client)
            except (httpx.HTTPError, ValueError):
                folio_hits = []
            if folio_hits:
                return {"results": folio_hits}

        # 2+3) national Census geocoder + parcel-precise MDC address search, concurrent
        async def census_leg():
            try:
                return await _census_geocode(q, limit, client)
            except (httpx.HTTPError, ValueError):
                return []

        async def mdc_leg():
            try:
                return await _mdc_search(q_norm, limit, client, force_address=is_folio)
            except (httpx.HTTPError, ValueError):
                return []

        census_out, mdc_out = await asyncio.gather(census_leg(), mdc_leg())

    out = list(census_out[:limit])
    seen = {(round(o["lat"], 4), round(o["lon"], 4)) for o in out}
    for m in mdc_out:
        key = (round(m["lat"], 4), round(m["lon"], 4))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return {"results": out[:limit]}


# ---------- comps from comps-aggregator ----------

@app.get("/api/comps")
def comps_in_radius(
    lon: float = Query(...),
    lat: float = Query(...),
    radius_ft: int = Query(2000, ge=100, le=10560),
    listing_type: str = Query("sale", pattern="^(sale|rent|all)$"),
    limit: int = Query(8, ge=1, le=50),
):
    if not COMPS_DB.exists():
        return {"available": False, "reason": "comps.db not found"}
    lat_pad = radius_ft / 364320.0
    lng_pad = radius_ft / (364320.0 * max(math.cos(math.radians(lat)), 0.01))
    # Pull ALL in-radius listings — stats (sale + rent + rent_by_bed) must reflect
    # everything nearby; the requested listing_type only filters the returned cards.
    args = [lat - lat_pad, lat + lat_pad, lon - lng_pad, lon + lng_pad]
    conn = sqlite3.connect(COMPS_DB); conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, source, url, listing_type, address, city, lat, lng,
                      price, beds, baths, sqft, price_per_sqft, year_built,
                      property_type, days_on_market, status
               FROM listings
               WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
            args,
        ).fetchall()
        bld_rows = conn.execute(
            """SELECT id, name, address, city, lat, lng, status, pct_sold,
                      available_units, unit_count, height_stories,
                      developer, architect, website, sales_launch_date, neighborhood
               FROM buildings
               WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
            [lat - lat_pad, lat + lat_pad, lon - lng_pad, lon + lng_pad],
        ).fetchall()
    finally:
        conn.close()

    def attach(r):
        d = dict(r)
        d["distance_ft"] = round(_haversine_ft(lat, lon, r["lat"], r["lng"]))
        return d

    listings = [attach(r) for r in rows]
    listings = [l for l in listings if l["distance_ft"] <= radius_ft]
    listings.sort(key=lambda x: x["distance_ft"])
    bldgs = [attach(r) for r in bld_rows]
    bldgs = [b for b in bldgs if b["distance_ft"] <= radius_ft]
    bldgs.sort(key=lambda x: x["distance_ft"])

    def _median(xs):
        xs = sorted(x for x in xs if x)
        return round(xs[len(xs) // 2], 2) if xs else None

    def agg(group):
        psf = [l.get("price_per_sqft") for l in group if l.get("price_per_sqft")]
        return {
            "count": len(group),
            "median_psf": _median(psf),
            "min_psf": round(min(psf), 2) if psf else None,
            "max_psf": round(max(psf), 2) if psf else None,
        }

    # Rental rates by bedroom count (studio..3BR+) from rent listings
    rents = [l for l in listings if l["listing_type"] == "rent"]

    def rent_bucket(beds):
        try:
            b = int(float(beds))
        except (TypeError, ValueError):
            return None
        return "Studio" if b == 0 else (f"{b} BR" if b < 3 else "3+ BR")

    rent_by_bed = {}
    for label in ["Studio", "1 BR", "2 BR", "3+ BR"]:
        grp = [l for l in rents if rent_bucket(l.get("beds")) == label]
        if grp:
            rent_by_bed[label] = {
                "count": len(grp),
                "median_rent": _median([l.get("price") for l in grp]),
                "median_psf": _median([l.get("price_per_sqft") for l in grp]),
            }

    # Only the returned cards honor the requested type; stats above cover everything.
    returned = listings if listing_type == "all" else [l for l in listings if l["listing_type"] == listing_type]

    return {
        "available": True,
        "radius_ft": radius_ft,
        "stats": {
            "sale": agg([l for l in listings if l["listing_type"] == "sale"]),
            "rent": agg(rents),
        },
        "rent_by_bed": rent_by_bed,
        "listings": returned[:limit],
        "buildings": bldgs[:limit],
    }


# ---------- saved addresses (marks) ----------

MARKS_DB = DATA_DIR / "marks.db"


def _marks_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MARKS_DB, timeout=10)  # ride out brief OneDrive/sync locks
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                lon        REAL NOT NULL,
                lat        REAL NOT NULL,
                folio      TEXT,
                address    TEXT,
                zone       TEXT,
                county     TEXT,
                note       TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _marks_busy() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "marks db busy"})


class MarkIn(BaseModel):
    lon: float
    lat: float
    folio: str | None = None
    address: str | None = None
    zone: str | None = None
    county: str | None = None
    note: str = ""


class MarkNote(BaseModel):
    note: str


@app.get("/api/marks")
def list_marks():
    # contextlib.closing guarantees the handle closes on every path (sqlite3's own
    # context manager only commits/rolls back); a locked db returns 503, not 500.
    try:
        with contextlib.closing(_marks_conn()) as conn, conn:
            rows = conn.execute("SELECT * FROM marks ORDER BY created_at DESC").fetchall()
            return {"marks": [dict(r) for r in rows]}
    except sqlite3.OperationalError:
        return _marks_busy()


@app.post("/api/marks")
def create_mark(m: MarkIn):
    try:
        with contextlib.closing(_marks_conn()) as conn, conn:
            # Don't duplicate an existing mark for the same folio (or same exact point)
            if m.folio:
                row = conn.execute("SELECT id FROM marks WHERE folio = ?", (m.folio,)).fetchone()
                if row:
                    return {"created": False, "reason": "already saved", "id": row["id"]}
            cur = conn.execute(
                "INSERT INTO marks (lon, lat, folio, address, zone, county, note) VALUES (?,?,?,?,?,?,?)",
                (m.lon, m.lat, m.folio, m.address, m.zone, m.county, m.note),
            )
            row = conn.execute("SELECT * FROM marks WHERE id = ?", (cur.lastrowid,)).fetchone()
            return {"created": True, "mark": dict(row)}
    except sqlite3.OperationalError:
        return _marks_busy()


@app.patch("/api/marks/{mark_id}")
def update_mark(mark_id: int, body: MarkNote):
    try:
        with contextlib.closing(_marks_conn()) as conn, conn:
            cur = conn.execute("UPDATE marks SET note = ? WHERE id = ?", (body.note, mark_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "mark not found")
            return {"updated": True}
    except sqlite3.OperationalError:
        return _marks_busy()


@app.delete("/api/marks/{mark_id}")
def delete_mark(mark_id: int):
    try:
        with contextlib.closing(_marks_conn()) as conn, conn:
            cur = conn.execute("DELETE FROM marks WHERE id = ?", (mark_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "mark not found")
            return {"deleted": True}
    except sqlite3.OperationalError:
        return _marks_busy()


# ---------- Sunbiz LLC / corporate entity lookup (Florida Division of Corporations) ----------

SUNBIZ = "https://search.sunbiz.org/Inquiry/CorporationSearch"
_TAG_RX = re.compile(r"<[^>]+>")
_ENTITY_HINT = re.compile(r"\b(LLC|L\.?L\.?C|INC|CORP|CO|LTD|LP|LLLP|LLP|PA|PLLC|"
                          r"TRUST|HOLDINGS|PARTNERS|GROUP|PROPERTIES|INVESTMENTS|"
                          r"REALTY|VENTURES|CAPITAL|ENTERPRISES|ASSOCIATES)\b", re.I)


def _clean(s: str) -> str:
    return _TAG_RX.sub(" ", s or "").replace("&nbsp;", " ").replace("&amp;", "&").replace("\n", " ").strip()


def _addr_block(html_fragment: str) -> str:
    m = re.search(r"<div>(.*?)</div>", html_fragment, re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1).replace("<br/>", ", ").replace("<br>", ", ")).strip().strip(",").strip()


def looks_like_entity(name: str) -> bool:
    return bool(name and _ENTITY_HINT.search(name))


def _parse_sunbiz_detail(html: str) -> dict:
    out: dict = {}
    # Filing info: label for="Detail_X"><label>...</label><span>VALUE</span>
    for key, label in (("document_number", "Document Number"), ("fei_ein", "FEI/EIN Number"),
                       ("date_filed", "Date Filed"), ("status", "Status"),
                       ("last_event", "Last Event")):
        m = re.search(rf"{re.escape(label)}</label>\s*<span>([^<]*)</span>", html)
        if m:
            out[key] = m.group(1).strip()
    # Entity name + type (corporationName section: header span then name span)
    m = re.search(r'detailSection corporationName">\s*<span>([^<]+)</span>\s*<span>([^<]+)</span>', html)
    if m:
        out["entity_type"] = m.group(1).strip()
        out["name"] = m.group(2).strip()

    def section_after(header: str) -> str | None:
        i = html.find(header)
        if i < 0:
            return None
        start = html.rfind('detailSection', 0, i)
        end = html.find('class="detailSection', i)
        return html[start: end if end > 0 else i + 800]

    # Principal + mailing address
    for key, hdr in (("principal_address", "Principal Address"), ("mailing_address", "Mailing Address")):
        sec = section_after(hdr)
        if sec:
            a = _addr_block(sec)
            if a:
                out[key] = a
    # Registered agent
    ra = section_after("Registered Agent Name")
    if ra:
        spans = re.findall(r"<span>(.*?)</span>", ra, re.S)
        agent_name = _clean(spans[1]) if len(spans) > 1 else None
        out["registered_agent"] = {"name": agent_name, "address": _addr_block(ra)}
    # Authorized persons / officers
    ap = section_after("Authorized Person")
    if ap:
        people = []
        # Each person: Title&nbsp;CODE ... NAME <span><div>ADDR</div></span>
        for chunk in re.split(r"Title(?:&nbsp;|\s)+", ap)[1:]:
            title_m = re.match(r"([A-Z]+)", _clean(chunk))  # short code only (MGR, P, VP…)
            title = title_m.group(1).strip() if title_m else ""
            # name = text between the title and the first <span><div>
            nm = re.search(r"<br\s*/?>\s*<br\s*/?>\s*([^<]+?)\s*<span>", chunk)
            name = nm.group(1).strip() if nm else None
            addr = _addr_block(chunk)
            if name:
                people.append({"title": title, "name": name, "address": addr})
        if people:
            out["officers"] = people
    return out


import subprocess
import urllib.parse

# Sunbiz sits behind a WAF that fingerprints the TLS/HTTP client — httpx is 403'd
# but the system curl passes. Shell out to curl for these two GETs.
_CURL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _curl_get(url: str, timeout: int = 15) -> str | None:
    try:
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-A", _CURL_UA, url],
            capture_output=True, text=True, timeout=timeout + 5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.stdout if proc.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


OPENCORPORATES_TOKEN = os.environ.get("OPENCORPORATES_TOKEN", "").strip()


# ---- National city registries (auto-harvested zoning + permit services) ----
def _load_registry(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []  # wrong shape (e.g. wrapper object) — degrade, don't crash the import
    return [e for e in data if isinstance(e, dict)]


CITY_ZONING = _load_registry(DATA_DIR / "city_zoning.json")
CITY_PERMITS = _load_registry(DATA_DIR / "city_permits.json")
COUNTY_SALES = _load_registry(DATA_DIR / "county_sales.json")
_COUNTY_SALES_BY_FIPS = {c.get("fips"): c for c in COUNTY_SALES if c.get("fips")}


def _norm_county(name: str) -> str:
    return re.sub(r"\s+county.*$", "", (name or "").lower()).strip()


def _nearby_cities(registry: list, lon: float, lat: float, deg: float = 0.5) -> list:
    out = [c for c in registry if abs(c.get("lon", 999) - lon) < deg and abs(c.get("lat", 999) - lat) < deg]
    out.sort(key=lambda c: (c.get("lon", 999) - lon) ** 2 + (c.get("lat", 999) - lat) ** 2)
    return out


def _code_link(city: str, state: str, zone: str) -> str:
    """Universal deep link to the jurisdiction's zoning code — full definition + bonus
    provisions. Lands on Municode/American Legal/the city ordinance."""
    q = urllib.parse.quote(f"{city} {state} zoning code {zone} district regulations bonus")
    return f"https://www.google.com/search?q={q}"


# Curated, codified density/height bonus programs by state (the ones worth surfacing
# without reading each ordinance). The code link covers the rest.
# Summaries only — programs change; always confirm current local code + with counsel.
BONUS_NOTES = {
    "FL": "Florida Live Local Act (SB 102, as amended through 2024): a qualifying project with "
          "≥40% of units affordable at ≤120% AMI for 30+ years may build to the greater of the "
          "highest height allowed within 1 mile or 150% of the tallest existing building within "
          "1 mile, plus highest allowed density and 150% of highest allowed FAR — with carve-outs "
          "(airport, military, some historic). Verify eligibility/caps with counsel.",
    "CA": "California Density Bonus Law (Gov. Code 65915, incl. AB 1287/2023): up to ~100% density "
          "bonus, multiple concessions, and reduced parking for affordable units; SB 35/AB 2011 "
          "streamlining may apply.",
    "NY": "NYC offers Inclusionary Housing / Mandatory Inclusionary Housing (MIH) floor-area "
          "bonuses, plus 2024 'City of Yes' changes and special-district bonuses.",
    "WA": "Washington (HB 1110 / Seattle MHA): missing-middle upzoning and affordable-housing "
          "floor-area bonuses in many zones.",
    "OR": "Oregon (HB 2001) requires middle housing in most residential zones; Portland offers "
          "affordable-housing bonuses.",
}


def _oc_website_url(state: str | None, name: str) -> str:
    """Free, user-facing OpenCorporates search — works for any US jurisdiction."""
    juris = f"us_{state.lower()}" if state else "us"
    return f"https://opencorporates.com/companies/{juris}?q={urllib.parse.quote(name)}"


def _query_opencorporates(state: str, name: str) -> dict | None:
    """Structured entity lookup for any state when OPENCORPORATES_TOKEN is set."""
    juris = f"us_{state.lower()}"
    url = (f"https://api.opencorporates.com/v0.4/companies/search?q={urllib.parse.quote(name)}"
           f"&jurisdiction_code={juris}&api_token={OPENCORPORATES_TOKEN}")
    raw = _curl_get(url)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        comps = data["results"]["companies"]
    except (ValueError, KeyError):
        return None
    if not comps:
        return None
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    target = norm(name)
    best = next((c["company"] for c in comps if norm(c["company"].get("name")) == target),
                comps[0]["company"])
    return {
        "found": True, "source": "OpenCorporates",
        "name": best.get("name"),
        "status": best.get("current_status"),
        "entity_type": best.get("company_type"),
        "date_filed": best.get("incorporation_date"),
        "document_number": best.get("company_number"),
        "principal_address": best.get("registered_address_in_full"),
        "officers": [],  # officer detail needs a second OC call; link out for the full record
    }


@app.get("/api/entity")
def entity_lookup(name: str = Query(..., min_length=3), state: str | None = Query(None)):
    """Look up a property owner that is a business entity. Florida → Sunbiz (structured,
    free). Any other state → OpenCorporates API when OPENCORPORATES_TOKEN is set; always
    return a free OpenCorporates website deep link for that state."""
    st = (state or "").upper() or None
    oc_url = _oc_website_url(st, name)

    # Non-Florida: OpenCorporates (structured if key, else deep link).
    if st and st != "FL":
        if OPENCORPORATES_TOKEN:
            oc = _query_opencorporates(st, name)
            if oc:
                oc["query"] = name
                oc["oc_url"] = oc_url
                return oc
            return {"found": False, "query": name, "oc_url": oc_url,
                    "message": f"No OpenCorporates match in {st}."}
        return {"found": False, "query": name, "state": st, "oc_url": oc_url,
                "needs_oc_key": True,
                "message": (f"Structured registry data for {st} needs OPENCORPORATES_TOKEN. "
                            "Use the link for the free OpenCorporates search.")}

    # Florida → Sunbiz (structured, free).
    q = urllib.parse.quote(name)
    html = _curl_get(f"{SUNBIZ}/SearchResults?inquiryType=EntityName&searchTerm={q}")
    if not html:
        return {"found": False, "message": "Sunbiz unreachable", "oc_url": oc_url}

    rows = re.findall(r'<a href="(/Inquiry/CorporationSearch/SearchResultDetail[^"]+)"[^>]*>([^<]+)</a>', html)
    if not rows:
        return {"found": False, "query": name, "message": "No Sunbiz match"}

    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    target = norm(name)
    best_row = next((r for r in rows if norm(r[1]) == target), None)
    if best_row is None:
        # Sunbiz pads results with alphabetical neighbors — only accept a fuzzy
        # fallback that actually resembles the query (prefix either way, or strong
        # char similarity), never a random first row.
        def _close(row_name: str) -> bool:
            cand = norm(row_name)
            if not cand or not target:
                return False
            if cand.startswith(target) or target.startswith(cand):
                return True
            return difflib.SequenceMatcher(None, target, cand).ratio() >= 0.6
        best_row = next((r for r in rows if _close(r[1])), None)
    if best_row is None:
        return {"found": False, "query": name, "oc_url": oc_url,
                "suggestions": [_html.unescape(r[1]).strip() for r in rows[:5]],
                "search_url": f"{SUNBIZ}/SearchResults?inquiryType=EntityName&searchTerm={q}",
                "message": "No close Sunbiz match — nearest registry entries listed."}
    best = best_row[0].replace("&amp;", "&")
    row_name = best_row[1].strip()

    detail_html = _curl_get(f"https://search.sunbiz.org{best}")
    if not detail_html:
        return {"found": False, "message": "Sunbiz detail unreachable"}
    detail = _parse_sunbiz_detail(detail_html)
    detail.setdefault("name", row_name)

    if not detail.get("name") and not detail.get("officers") and not detail.get("registered_agent"):
        return {"found": False, "query": name, "message": "Entity page could not be parsed"}
    detail["found"] = True
    detail["query"] = name
    detail["sunbiz_url"] = f"https://search.sunbiz.org{best}"
    detail["oc_url"] = oc_url
    detail["match_count"] = len(rows)
    return detail


# ---------- search by owner / connected party (FL: Sunbiz + parcels) ----------

def _sunbiz_officer_search(name: str, limit: int = 40) -> list[dict]:
    """Reverse Sunbiz lookup: every entity where `name` is an officer or registered
    agent. Returns [{entity, doc, officer, url}]. This is the 'connected parties' hop."""
    q = urllib.parse.quote(name)
    html = _curl_get(f"{SUNBIZ}/SearchResults?inquiryType=OfficerRegisteredAgentName&searchTerm={q}")
    if not html:
        return []
    out = []
    # Each result row: <a href=DETAIL>OFFICER</a></td> <td>ENTITY</td> <td>DOC#</td>
    pat = re.compile(
        r'<a href="(/Inquiry/CorporationSearch/SearchResultDetail[^"]+)"[^>]*>([^<]+)</a>(.*?)'
        r'(?=<a href="/Inquiry/CorporationSearch/SearchResultDetail|</table>)', re.S)
    for href, officer, tail in pat.findall(html):
        tds = [t.strip() for t in re.findall(r"<td[^>]*>([^<]*)</td>", tail) if t.strip()]
        entity = tds[0] if tds else None
        doc = next((t for t in tds[1:] if re.match(r"^[A-Z]?\d{5,}$", t)), (tds[1] if len(tds) > 1 else None))
        if not entity:
            continue
        out.append({"entity": _html.unescape(entity), "doc": doc,
                    "officer": _html.unescape(officer.strip()),
                    "url": "https://search.sunbiz.org" + href.replace("&amp;", "&")})
        if len(out) >= limit:
            break
    return out


@app.get("/api/related-entities")
def related_entities(name: str = Query(..., min_length=3), state: str | None = Query(None)):
    """Connected parties: every entity where `name` is an officer / registered agent.
    Florida → Sunbiz officer search (structured, free). Elsewhere → OpenCorporates
    officer-search deep link."""
    st = (state or "FL").upper()
    if st != "FL":
        url = f"https://opencorporates.com/officers?q={urllib.parse.quote(name)}&jurisdiction_code=us_{st.lower()}"
        return {"found": False, "query": name, "state": st, "oc_url": url,
                "message": f"Officer search for {st} is via OpenCorporates (link)."}
    ents = _sunbiz_officer_search(name)
    # de-dup by entity name, keep the strongest doc#
    seen, uniq = set(), []
    for e in ents:
        k = e["entity"].upper()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return {"found": bool(uniq), "query": name, "count": len(uniq), "entities": uniq,
            "source": "Sunbiz officer / registered-agent search",
            "search_url": f"{SUNBIZ}/SearchResults?inquiryType=OfficerRegisteredAgentName&searchTerm={urllib.parse.quote(name)}"}


def _ring_centroid(geom: dict):
    if not geom:
        return None, None
    t = geom.get("type")
    if t == "Polygon":
        ring = geom["coordinates"][0]
    elif t == "MultiPolygon":
        ring = geom["coordinates"][0][0]
    elif t == "Point":
        return geom["coordinates"][0], geom["coordinates"][1]
    else:
        return None, None
    if not ring:
        return None, None
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


@app.get("/api/owner-properties")
async def owner_properties(name: str = Query(..., min_length=3), limit: int = Query(40, le=100)):
    """Every parcel whose owner name matches `name`. Florida only (free public layers):
    Miami-Dade Property Appraiser + FDOR statewide cadastral. The owner→holdings pivot.
    Both legs run concurrently with tight per-leg timeouts so the endpoint answers in
    seconds with whatever succeeded (a slow/dead leg just contributes nothing)."""
    nm = name.strip().upper().replace("'", "''")
    async with httpx.AsyncClient(timeout=18.0) as client:
        # Miami-Dade (richest)
        async def mdc_leg():
            try:
                r = await client.get(
                    f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0/query",
                    params={"where": f"UPPER(TRUE_OWNER1) LIKE '%{nm}%'",
                            "outFields": "FOLIO,TRUE_OWNER1,TRUE_SITE_ADDR,TRUE_SITE_CITY",
                            "outSR": "4326", "returnGeometry": "true",
                            "resultRecordCount": str(limit), "f": "geojson"},
                    timeout=9.0)
                r.raise_for_status()
                return r.json().get("features") or []
            except (httpx.HTTPError, ValueError):
                return []

        # FDOR statewide cadastral (rest of FL) — the owner LIKE scan often runs to
        # its timeout, so cap it hard rather than letting it stall the response.
        async def fdor_leg():
            try:
                r = await client.get(
                    f"{FL_CADASTRAL}/query",
                    params={"where": f"UPPER(OWN_NAME) LIKE '%{nm}%'",
                            "outFields": "PARCEL_ID,OWN_NAME,PHY_ADDR1,PHY_CITY,CO_NO",
                            "outSR": "4326", "returnGeometry": "true",
                            "resultRecordCount": str(limit), "f": "geojson"},
                    timeout=8.0)
                r.raise_for_status()
                return r.json().get("features") or []
            except (httpx.HTTPError, ValueError):
                return []

        mdc_feats, fdor_feats = await asyncio.gather(mdc_leg(), fdor_leg())

    props: list[dict] = []
    seen = set()
    for f in mdc_feats:
        p = f.get("properties") or {}
        lon, lat = _ring_centroid(f.get("geometry"))
        if lon is None or p.get("FOLIO") in seen:
            continue
        seen.add(p.get("FOLIO"))
        props.append({"folio": p.get("FOLIO"), "owner": p.get("TRUE_OWNER1"),
                      "address": p.get("TRUE_SITE_ADDR"), "city": p.get("TRUE_SITE_CITY"),
                      "county": "Miami-Dade", "lon": lon, "lat": lat, "source": "Miami-Dade PA"})
    for f in fdor_feats:
        if len(props) >= limit:
            break
        p = f.get("properties") or {}
        lon, lat = _ring_centroid(f.get("geometry"))
        pid = p.get("PARCEL_ID")
        if lon is None or pid in seen:
            continue
        seen.add(pid)
        props.append({"folio": pid, "owner": p.get("OWN_NAME"),
                      "address": p.get("PHY_ADDR1"), "city": p.get("PHY_CITY"),
                      "county": None, "lon": lon, "lat": lat, "source": "FL FDOR cadastral"})
    return {"found": bool(props), "query": name, "count": len(props), "properties": props[:limit]}


@app.get("/api/flood-overlay")
async def flood_overlay(bbox: str = Query(...), limit: int = Query(2000, le=4000)):
    """Proxy FEMA NFHL flood polygons for a viewport bbox. Server-side because FEMA's
    server sends no CORS headers (browser fetch is blocked) and is connection-flaky."""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")
    env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                      "spatialReference": {"wkid": 4326}})
    params = {
        "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY",
        "returnGeometry": "true", "geometryPrecision": "5",
        "resultRecordCount": str(limit), "f": "geojson",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        for attempt in range(2):  # FEMA resets connections; one retry
            try:
                r = await client.get(f"{FEMA_NFHL}/query", params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError):  # ValueError: 200 + HTML maintenance page
                if attempt == 1:
                    return {"type": "FeatureCollection", "features": [], "error": "FEMA unavailable"}
    return {"type": "FeatureCollection", "features": []}


# ---------- live zoning overlay (renders wired metro zoning by viewport) ----------

# Miami 21 is a form-based code, so the district name encodes the intensity:
# T6-8 and T6-80 are the same transect and eight versus eighty storeys. Colouring
# them identically — which is what a single "downtown" family does — hides the
# one attribute that decides whether a termination target can be rebuilt bigger
# than it stands. `_zone_stories` recovers the cap the code itself declares, and
# nothing is inferred for codes that do not declare one.
MIAMI21_STORIES = {"T3": 2, "T4": 3, "T5": 5}
_T6_RE = re.compile(r"^T6-(\d{1,2})")
_T345_RE = re.compile(r"^T([345])\b")

# Codes whose letters collide with a generic family and mean something else in
# the Miami 21 / Miami-Dade vocabulary. Applied only where that vocabulary is in
# force, because "CS" is Civic Space here and Commercial Service elsewhere.
MIAMI21_CATEGORY = {
    "CS": "open",        # Civic Space / parks — NOT commercial
    "CI": "special",     # Civic Institutional
    "CI-HD": "special",  # Civic Institutional, Health District
    "D1": "industrial",  # Work Place
    "D2": "industrial",  # Industrial
    "D3": "industrial",  # Marine
}
# Miami-Dade county codes that fall through the generic rules.
DADE_CATEGORY = {"GU": "special", "EU": "residential", "AU": "agricultural"}

_MIAMI21_MUNI_RE = re.compile(r"\b(miami|dade)\b", re.I)


def _zone_stories(code: str) -> int | None:
    """Maximum storeys where the district code states one. None means the code
    does not declare a height and the map must not pretend otherwise."""
    c = (code or "").strip().upper()
    m = _T6_RE.match(c)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 99 else None
    m = _T345_RE.match(c)
    if m:
        return MIAMI21_STORIES.get("T" + m.group(1))
    return None


def _zone_category(code: str, muni: str | None = None) -> str:
    """Normalize a raw zone code into a ZoLa-style family for map coloring.

    `muni` opts the polygon into the local vocabulary. Without it the generic
    rules apply, which is right for the other wired cities and wrong for Miami:
    the bare letter rules read CS as commercial and drop D1/D2/D3 into `other`.
    """
    c = (code or "").strip().upper()
    if not c:
        return "other"

    if muni and _MIAMI21_MUNI_RE.search(muni):
        if c in MIAMI21_CATEGORY:
            return MIAMI21_CATEGORY[c]
        head = c.split("-")[0]
        if head in DADE_CATEGORY:
            return DADE_CATEGORY[head]

    m = re.match(r"^T([3-6])", c)  # form-based transect (Miami 21 / SmartCode)
    if m:
        return {"3": "residential", "4": "residential", "5": "mixed", "6": "downtown"}[m.group(1)]
    if re.match(r"^(MU|MX|MXD|RMX|CMX|UMU|UV|FBC|TOD)", c):
        return "mixed"
    if re.match(r"^(CBD|DT|DC|DX|DD|DOC|D-|UPTOWN)", c) or c == "D" or "DOWNTOWN" in c:
        return "downtown"
    if re.match(r"^(OS|PR|REC|GREEN|CONS|OPEN|PARK|P-?\d)", c) or c in ("P", "OS"):
        return "open"
    if re.match(r"^(AG|AR|RR|RA|FARM)", c) or c == "A":
        return "agricultural"
    if re.match(r"^(PUD|PD|PDD|SPI|SP|MPD|MPC|TND|PLAN)", c):
        return "special"
    if re.match(r"^(M|I|IL|IH|IND|LI|HI|IG|IP|MFG)", c):
        return "industrial"
    if re.match(r"^(O|OR|OB|OP|OFC)", c):
        return "office"
    if re.match(r"^(C|B|GC|NC|HC|CC|CG|CB|CR|COM|RET)", c):
        return "commercial"
    if re.match(r"^(R|SF|MF|MH|TH|DUP|RES)", c):
        return "residential"
    return "other"


@app.get("/api/cities")
def cities():
    """Completed cities (those with a live zoning service). Powers the place picker
    and the coverage view. Deduped + sorted by state, then city."""
    seen = set()
    out = []
    for c in CITY_ZONING:
        key = (c.get("state"), (c.get("city") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"city": c.get("city"), "state": c.get("state"),
                    "lon": c.get("lon"), "lat": c.get("lat")})
    out.sort(key=lambda c: (c.get("state") or "", c.get("city") or ""))
    return {"count": len(out), "cities": out}


# States with a free statewide parcel layer (owner/value inline) — FL handled separately.
_DEEP_PARCEL_STATES = set()


@app.get("/api/markets")
def markets():
    """Every market with wired data, with capability flags — powers the national
    'data coverage' dots so it's obvious what works where. Merges the zoning +
    permit registries with the hand-wired specials (NYC, Chicago, FL, county sales)."""
    mkts: dict[tuple, dict] = {}

    def m(city, state, lon=None, lat=None):
        key = ((city or "").lower(), state)
        if key not in mkts:
            mkts[key] = {"city": city, "state": state, "lon": lon, "lat": lat, "caps": set()}
        e = mkts[key]
        if e["lon"] is None and lon is not None:
            e["lon"], e["lat"] = lon, lat
        return e

    for c in CITY_ZONING:
        if c.get("city") and c.get("lon") is not None:
            m(c["city"], c.get("state"), c["lon"], c.get("lat"))["caps"].add("zoning")
    for c in CITY_PERMITS:
        if c.get("city") and c.get("lon") is not None:
            m(c["city"], c.get("state"), c["lon"], c.get("lat"))["caps"].update(("permits", "permit heat"))
    for c in COUNTY_SALES:
        if c.get("lon") is not None:
            label = (c.get("label") or "").replace(" County", "")
            m(label, c.get("state"), c["lon"], c.get("lat"))["caps"].add("recorded sales")

    # Hand-wired specials
    nyc = m("New York", "NY", -73.9857, 40.7484)
    nyc["caps"].update(("zoning", "deep parcels", "deeds & mortgages (ACRIS)", "investment sales"))
    chi = m("Chicago", "IL", -87.6298, 41.8781)
    chi["caps"].add("investment sales")
    # Florida: every FL market gets the deep stack
    for e in mkts.values():
        if e["state"] == "FL":
            e["caps"].update(("deep parcels", "sales history", "brokers", "owner search"))
        if e["state"] in _DEEP_PARCEL_STATES:
            e["caps"].add("deep parcels")
    # Statewide-parcel states get flagged via their covered cities
    for e in mkts.values():
        if e["state"] in ("MA", "NJ", "WI", "OH", "MT", "CT", "VT", "AR", "ND", "IA"):
            e["caps"].add("deep parcels")

    out = []
    for e in mkts.values():
        if e["lon"] is None:
            continue
        caps = sorted(e["caps"])
        out.append({"city": e["city"], "state": e["state"], "lon": e["lon"], "lat": e["lat"],
                    "caps": caps, "depth": len(caps)})
    out.sort(key=lambda x: (-x["depth"], x["state"], x["city"]))
    return {"count": len(out), "markets": out,
            "national": ("Everywhere in the US regardless of dots: FEMA flood, elevation, "
                         "environmental screening (EPA), historic register, disaster history, "
                         "demographics, market rents (ZORI), amenities, schools, airports, "
                         "LIHTC/QCT/DDA, seismic & wildfire hazard.")}


def _zoning_candidates(minx, miny, maxx, maxy):
    """Which sources to query for this viewport, metro (hand-wired) ones first.

    Capped at 8 total -- the ceiling this endpoint has always had -- so a huge
    bbox still bounds its fan-out; it just runs that fan-out CONCURRENTLY now
    instead of one request at a time.
    """
    def intersects(b):
        x0, y0, x1, y1 = b
        return not (maxx < x0 or minx > x1 or maxy < y0 or miny > y1)

    out = []
    for co_no, cfgs in METRO_ZONING.items():
        cb = COUNTY_BBOX.get(co_no)
        if cb is None or intersects(cb):
            out.extend(("metro", cfg) for cfg in cfgs)

    bx0, by0, bx1, by1 = minx - 0.35, miny - 0.35, maxx + 0.35, maxy + 0.35
    for c in CITY_ZONING:
        if not c.get("url") or not c.get("code"):
            continue  # tolerate malformed harvested entries
        clon, clat = c.get("lon", 999), c.get("lat", 999)
        if bx0 <= clon <= bx1 and by0 <= clat <= by1:
            out.append(("city", c))
    return out[:8]


async def _fetch_zoning_source(client, kind, cfg, env, limit):
    """One upstream zoning query, fully self-contained -- so it can run
    concurrently with the others via asyncio.wait instead of one at a time."""
    code_field, desc_field = cfg["code"], cfg.get("desc")
    out_fields = [code_field] + ([desc_field] if desc_field else [])
    params = {
        "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(out_fields), "returnGeometry": "true",
        "geometryPrecision": "5", "resultRecordCount": str(limit), "f": "geojson",
    }
    try:
        r = await client.get(f"{cfg['url']}/query", params=params)
        r.raise_for_status()
        gj = r.json()
    except (httpx.HTTPError, ValueError):
        return []

    muni = cfg["muni"] if kind == "metro" else f"{cfg.get('city')}, {cfg.get('state')}"
    skip_rx = cfg.get("skip_rx") if kind == "metro" else None
    feats = []
    for ft in gj.get("features") or []:
        props = ft.get("properties") or {}
        code = props.get(code_field)
        if code in (None, "", " "):
            continue
        if skip_rx and re.match(skip_rx, str(code).strip(), re.I):
            continue
        zone = str(code).strip()
        ft["properties"] = {
            "zone": zone,
            "desc": (str(props.get(desc_field)).strip() if desc_field and props.get(desc_field) else None),
            "muni": muni,
            "category": _zone_category(zone, muni),
            "max_stories": _zone_stories(zone),
        }
        feats.append(ft)
    return feats


@app.get("/api/zoning-overlay")
async def zoning_overlay(bbox: str = Query(...), limit: int = Query(1500, le=4000)):
    """Return zoning polygons (as GeoJSON) for every wired metro service whose data
    intersects the viewport bbox. Expands the visual zoning layer beyond tri-county
    to all the metros in METRO_ZONING. bbox = 'minLon,minLat,maxLon,maxLat'.

    Fanned out CONCURRENTLY across up to 8 upstream services, bounded by one
    overall ~12s deadline via asyncio.wait rather than a per-call await in a
    for-loop -- the previous version could take up to 8 x 12s = 96s worst case
    on a viewport that intersects every wired source, because each upstream call
    waited for the last one to finish before starting. Panning with the live
    zoning layer on was the single slowest thing on the map because of this.
    """
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")

    candidates = _zoning_candidates(minx, miny, maxx, maxy)
    if not candidates:
        return {"type": "FeatureCollection", "features": []}

    env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                      "spatialReference": {"wkid": 4326}})
    async with httpx.AsyncClient(timeout=12.0) as client:
        tasks = [asyncio.ensure_future(_fetch_zoning_source(client, kind, cfg, env, limit))
                 for kind, cfg in candidates]
        done, pending = await asyncio.wait(tasks, timeout=12.0)
        for t in pending:
            t.cancel()
        features: list = []
        for t in done:
            try:
                features.extend(t.result())
            except Exception:  # noqa: BLE001 -- one bad source must not drop the rest
                continue
    return {"type": "FeatureCollection", "features": features[:limit]}


# ---------- parcel boundary overlay (default-on parcel fabric lines) ----------

# Rough FL bbox — routes viewport parcel-fabric queries to the FL services.
FL_BBOX = (-87.65, 24.35, -79.95, 31.05)

# FCC point→state lookups cached on a ~7mi grid so panning doesn't re-geocode.
_STATE_POINT_CACHE: dict[tuple[float, float], str | None] = {}


async def _state_abbrev_cached(lon: float, lat: float, client: httpx.AsyncClient) -> str | None:
    key = (round(lon, 1), round(lat, 1))
    if key in _STATE_POINT_CACHE:
        return _STATE_POINT_CACHE[key]
    st, _co = await _state_for_point(lon, lat, client)
    if len(_STATE_POINT_CACHE) > 4096:
        _STATE_POINT_CACHE.clear()
    _STATE_POINT_CACHE[key] = st
    return st


@app.get("/api/parcels-overlay")
async def parcels_overlay(bbox: str = Query(...), limit: int = Query(6000, le=12000)):
    """Parcel polygons for the viewport, feeding the default-on parcel-lines layer.
    FL: MDC ParcelsView / Broward BCPA, with the FDOR statewide cadastral as the
    fallback; elsewhere, the state's STATE_PARCELS service where one is wired.
    The frontend gates calls to zoom >= 15, but clamp the bbox anyway so a huge
    extent can't melt an upstream."""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")
    if (maxx - minx) > 0.14 or (maxy - miny) > 0.14:
        return {"type": "FeatureCollection", "features": [], "note": "Zoom in to load parcels."}
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

    async with httpx.AsyncClient(timeout=12.0) as client:
        sources: list[tuple[str, str, list[str]]] = []  # (url, id_field, addr_fields)
        if FL_BBOX[0] <= cx <= FL_BBOX[2] and FL_BBOX[1] <= cy <= FL_BBOX[3]:
            co = county_for_point(cy, cx)
            if co == "miami-dade":
                sources.append((f"{MDC_ROOT}/ParcelsView_gdb/FeatureServer/0", "FOLIO", ["TRUE_SITE_ADDR"]))
            elif co == "broward":
                sources.append((f"{BRO_ROOT}/BCPA_Parcels/FeatureServer/53", "FOLIO", ["FULL_SITE_ADDRESS"]))
            sources.append((FL_CADASTRAL, "PARCEL_ID", ["PHY_ADDR1"]))
        else:
            st = await _state_abbrev_cached(cx, cy, client)
            cfg = STATE_PARCELS.get(st or "")
            if cfg:
                flds = cfg.get("fields") or {}
                id_field = (flds.get("parcel_id") or ["OBJECTID"])[0]
                sources.append((cfg["url"], id_field, (flds.get("address") or [])[:1]))
        if not sources:
            return {"type": "FeatureCollection", "features": [],
                    "note": "No parcel fabric wired for this area."}

        env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                          "spatialReference": {"wkid": 4326}})
        PAGE = 2000  # ArcGIS maxRecordCount for these services

        async def fetch_page(url, id_field, addr_fields, offset):
            params = {
                "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                "outFields": ",".join([id_field] + addr_fields), "returnGeometry": "true",
                # Generalize server-side: for boundary lines at these zooms ~1m is
                # plenty and roughly halves the payload (fewer vertices per lot).
                "geometryPrecision": "5", "maxAllowableOffset": "0.00001",
                "resultRecordCount": str(PAGE),
                "resultOffset": str(offset), "f": "geojson",
            }
            try:
                r = await client.get(f"{url}/query", params=params)
                r.raise_for_status()
                gj = r.json()
            except (httpx.HTTPError, ValueError):
                return None
            return None if gj.get("error") else (gj.get("features") or [])

        async def fetch_count(url):
            params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                      "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true", "f": "json"}
            try:
                r = await client.get(f"{url}/query", params=params)
                return (r.json() or {}).get("count")
            except (httpx.HTTPError, ValueError):
                return None

        for url, id_field, addr_fields in sources:
            # Fetch page 0 first. The common zoomed-in view fits in one page, so
            # we skip the count round-trip entirely. Only when page 0 fills (dense
            # box) do we size the fan-out from a fast count and pull the remaining
            # pages CONCURRENTLY. Full paging keeps dense blocks from losing lines;
            # deep resultOffset is slow server-side, so we page shallowly.
            page0 = await fetch_page(url, id_field, addr_fields, 0)
            if page0 is None:
                continue  # dead source — try the next
            if not page0:
                continue  # this source has no parcels here — try the next
            raw_pages = [page0]
            count = None
            if len(page0) >= PAGE:
                count = await fetch_count(url)
                n_pages = (min((count + PAGE - 1) // PAGE, (limit + PAGE - 1) // PAGE)
                           if count else (limit + PAGE - 1) // PAGE)
                if n_pages > 1:
                    extra = await asyncio.gather(*[
                        fetch_page(url, id_field, addr_fields, i * PAGE)
                        for i in range(1, n_pages)])
                    raw_pages += [p for p in extra if p]

            feats: list = []
            for raw in raw_pages:
                for ft in raw:
                    props = ft.get("properties") or {}
                    ft["properties"] = {
                        "pid": props.get(id_field),
                        "addr": next((props[f] for f in addr_fields if props.get(f)), None),
                    }
                    feats.append(ft)
            if feats:
                total = count if count is not None else len(feats)
                return {"type": "FeatureCollection", "features": feats[:limit],
                        "truncated": total > limit}
    return {"type": "FeatureCollection", "features": [], "note": "No parcels here."}


# ---------- building permits (Miami-Dade public permit data) ----------

# BuildingPermit_gdb went token-required upstream (observed 2026-07). Folio permit
# history now reads the public miamidade_permit_data table (no geometry); the
# spatial endpoints below keep MDC as a first try and fall through to harvested
# city point services (City of Miami covers most Miami views).
MDC_PERMITS = f"{MDC_ROOT}/BuildingPermit_gdb/FeatureServer/0"
MDC_PERMITS_TABLE = f"{MDC_ROOT}/miamidade_permit_data/FeatureServer/0"
COUNTY_BBOX_MDC = (-80.88, 25.13, -80.11, 25.98)  # Miami-Dade rough WGS84 bbox


def _permit_value(v) -> int | None:
    try:
        n = int(str(v).lstrip("0") or "0")
        return n or None
    except (TypeError, ValueError):
        return None


def _permit_date(ms) -> str | None:
    if not ms:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _permit_yyyymmdd(s) -> str | None:
    """MDC CCDATE/BLDCMPDT are YYYYMMDD *strings* with '00000000' as the null
    sentinel — not epoch ms. Running them through _permit_date renders 1970-01-01."""
    s = (str(s) if s is not None else "").strip()
    if not s or s == "00000000":
        return None
    try:
        from datetime import datetime
        return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


async def _query_city_permits(lon: float, lat: float, client: httpx.AsyncClient, limit: int):
    """Recent building permits near a point from the nearest harvested city permit service."""
    d = 0.004  # ~450 m box
    env = json.dumps({"xmin": lon - d, "ymin": lat - d, "xmax": lon + d, "ymax": lat + d,
                      "spatialReference": {"wkid": 4326}})
    for c in _nearby_cities(CITY_PERMITS, lon, lat, deg=0.6):
        if not c.get("url"):
            continue  # tolerate malformed harvested entries
        wanted = [c[k] for k in ("num", "type", "desc", "status", "date", "value", "contractor", "address") if c.get(k)]
        params = {
            "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(wanted),
            "returnGeometry": "false", "resultRecordCount": str(limit), "f": "json",
        }
        if c.get("date"):
            params["orderByFields"] = f"{c['date']} DESC"
        try:
            r = await client.get(f"{c['url']}/query", params=params, timeout=9.0)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError):
            continue
        if not feats:
            continue
        out = []
        for ft in feats:
            a = ft.get("attributes") or {}
            raw_date = a.get(c["date"]) if c.get("date") else None
            issued = _permit_date(raw_date) if isinstance(raw_date, (int, float)) else (str(raw_date)[:10] if raw_date else None)
            val = a.get(c["value"]) if c.get("value") else None
            try:
                val = int(float(val)) if val not in (None, "", " ") else None
            except (TypeError, ValueError):
                val = None
            out.append({
                "permit_number": a.get(c["num"]) if c.get("num") else None,
                "type": a.get(c["type"]) if c.get("type") else None,
                "scope": (str(a.get(c["desc"])).strip() if c.get("desc") and a.get(c["desc"]) else None),
                "status": a.get(c["status"]) if c.get("status") else None,
                "est_value": val,
                "contractor": (str(a.get(c["contractor"])).strip() if c.get("contractor") and a.get(c["contractor"]) else None),
                "issued": issued,
                "address": (str(a.get(c["address"])).strip() if c.get("address") and a.get(c["address"]) else None),
            })
        return {"available": True, "city": f"{c.get('city')}, {c.get('state')}", "count": len(out), "permits": out}
    return None


@app.get("/api/permits")
async def permits(folio: str | None = Query(None), lon: float | None = Query(None),
                  lat: float | None = Query(None), limit: int = Query(12, le=50)):
    """Building permits — what's on the public permit docs (type, scope, status, est value,
    contractor, dates, permit #). Miami-Dade by folio; other cities by point (nearest
    harvested permit service)."""
    # National city permits by point when no MDC folio.
    if (not folio or len(folio) < 10) and lon is not None and lat is not None:
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await _query_city_permits(lon, lat, client, limit)
        return res or {"available": False, "permits": [], "message": "No permit service wired for this area."}
    if not folio:
        raise HTTPException(400, "provide folio (Miami-Dade) or lon+lat")
    clean = folio.replace("-", "").replace(" ", "")
    # Folio feeds the upstream where clause verbatim — accept digits only (10-16),
    # which both rejects garbage and closes the quote-injection path.
    if not clean.isdigit() or not (10 <= len(clean) <= 16):
        raise HTTPException(422, "folio must be 10-16 digits (dashes/spaces ok)")
    fields = ("PermitNumber,ProcessNumber,MasterPermitNumber,PermitType,"
              "ApplicationTypeDescription,ProposedUseDescription,DetailDescriptionComments,"
              "ResidentialCommercial,EstimatedValue,ContractorName,PermitIssuedDate,"
              "CoCcDate,PropertyAddress,StructureUnits,StructureFloors")
    params = {
        "where": f"FolioNumber='{clean}'", "outFields": fields,
        "orderByFields": "PermitIssuedDate DESC", "resultRecordCount": str(limit), "f": "json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(f"{MDC_PERMITS_TABLE}/query", params=params)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
        except (httpx.HTTPError, ValueError) as e:
            return {"available": True, "folio": clean, "permits": [], "message": str(e)}

    def _dateonly(v):
        # esriFieldTypeDateOnly serializes as 'YYYY-MM-DD'; guard epoch-ms anyway.
        if v in (None, "", " "):
            return None
        if isinstance(v, (int, float)):
            return _permit_date(v)
        return str(v)[:10]

    out = []
    for f in feats:
        a = f.get("attributes") or {}
        scope = " · ".join(str(s).strip() for s in (a.get("ProposedUseDescription"),
                                                    a.get("DetailDescriptionComments"))
                           if s and str(s).strip())
        out.append({
            "permit_number": a.get("PermitNumber") or a.get("ProcessNumber") or a.get("MasterPermitNumber"),
            "type": a.get("PermitType"),
            "app_type": (a.get("ApplicationTypeDescription") or "").strip() or None,
            "scope": scope or None,
            "status": None,  # the public table carries no status column
            "res_comm": a.get("ResidentialCommercial"),
            "est_value": _permit_value(a.get("EstimatedValue")),
            "contractor": (a.get("ContractorName") or "").strip() or None,
            "issued": _dateonly(a.get("PermitIssuedDate")),
            "completed": _dateonly(a.get("CoCcDate")),
            "units": a.get("StructureUnits"),
            "floors": a.get("StructureFloors"),
            "address": (a.get("PropertyAddress") or "").strip() or None,
        })
    # The county table skews to county-issued permits — municipal parcels (City of
    # Miami etc.) often have zero rows there. Fall back to the nearest harvested
    # city point service when the caller also sent coordinates.
    if not out and lon is not None and lat is not None:
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await _query_city_permits(lon, lat, client, limit)
        if res and res.get("permits"):
            res["folio"] = clean
            res["note"] = "County permit table had no rows for this folio — showing nearby city permits."
            return res
    return {"available": True, "county": "miami-dade", "folio": clean,
            "count": len(out), "permits": out}


# ---------- new-build stats (development pulse from permits) ----------

@app.get("/api/build-stats")
async def build_stats(lon: float = Query(...), lat: float = Query(...),
                      radius_ft: int = Query(2640, le=26400)):
    """Development pulse for an area: count + total declared value of NEW-construction
    permits, vs all permits, within radius. Uses MDC permits or the nearest harvested
    city permit service."""
    d = radius_ft / 364320.0  # deg
    env = json.dumps({"xmin": lon - d, "ymin": lat - d, "xmax": lon + d, "ymax": lat + d,
                      "spatialReference": {"wkid": 4326}})
    in_mdc = COUNTY_BBOX_MDC[0] <= lon <= COUNTY_BBOX_MDC[2] and COUNTY_BBOX_MDC[1] <= lat <= COUNTY_BBOX_MDC[3]

    # Ordered sources: MDC first inside its bbox, then the nearest harvested city.
    # Tight net (~15 mi): the 0.6-deg net used for permit *lists* attributes a
    # neighboring city's zeros (Denver → Lakewood) as a false "no activity".
    sources = []
    if in_mdc:
        sources.append(("mdc", MDC_PERMITS, "ESTVALUE", "Miami-Dade County",
                        ["APPTYPE", "DESC1", "TYPE"]))
    cfg = next((c for c in _nearby_cities(CITY_PERMITS, lon, lat, deg=0.22) if c.get("url")), None)
    if cfg:
        sources.append(("city", cfg["url"], cfg.get("value"),
                        f"{cfg.get('city')}, {cfg.get('state')}",
                        [cfg[k] for k in ("type", "desc") if cfg.get(k)]))
    if not sources:
        return {"available": False, "message": "No permit service wired for this area."}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for kind, url, val_field, src, new_fields in sources:
            # "new construction" = any of the descriptive fields mentioning NEW
            new_where = " OR ".join(f"UPPER({f}) LIKE '%NEW%'" for f in new_fields) or "1=1"

            async def total_count(where="1=1"):
                params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                          "spatialRel": "esriSpatialRelIntersects", "where": where,
                          "returnCountOnly": "true", "f": "json"}
                try:
                    r = await client.get(f"{url}/query", params=params)
                    body = r.json() or {}
                    if body.get("error"):
                        return None
                    return body.get("count")
                except (httpx.HTTPError, ValueError):
                    return None

            all_n = await total_count()
            if all_n is None:
                continue  # source dead (e.g. MDC went token-required) — try the next
            new_n = await total_count(new_where)

            # Sum of declared value for new-construction permits
            new_val = None
            if val_field:
                stats = json.dumps([{"statisticType": "sum", "onStatisticField": val_field,
                                     "outStatisticFieldName": "v"}])
                params = {"geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                          "spatialRel": "esriSpatialRelIntersects", "where": new_where,
                          "outStatistics": stats, "f": "json"}
                try:
                    r = await client.get(f"{url}/query", params=params)
                    feats = (r.json().get("features") or [])
                    if feats:
                        raw = (feats[0].get("attributes") or {}).get("v")
                        new_val = _permit_value(raw) if kind == "mdc" else (int(raw) if raw else None)
                except (httpx.HTTPError, ValueError, TypeError):
                    new_val = None

            return {
                "available": True, "source": src, "radius_ft": radius_ft,
                "new_build_permits": new_n, "all_permits": all_n,
                "new_build_value": new_val,
            }
    return {"available": False, "message": "Permit sources unavailable right now."}


# ---------- construction-permit heat map ----------

# City-of-Miami WorkItems vocabulary for an actual new *building* — the master
# building permit — as opposed to the trade sub-permits, pools, driveways, and
# site/survey tickets that also carry ScopeofWork='NEW CONSTRUCTION' because they
# ride the same job. Each token is matched as a WorkItems substring. Verified
# against the live "Building_Permits_Since_2014" service (Jul 2026): this whitelist
# cuts ~228k raw permits to ~2.8k real new buildings.
_NEWBUILD_TYPE_LIKES = [
    "FAMILY RESIDENCE", "FAMILY ANCILLARY", "MULTI-FAMILY",        # residential (incl. duplex, condo, rental)
    "OFFICE", "RETAIL", "RESTAURANT", "WAREHOUS", "INDUSTRIAL",    # commercial
    "SCHOOL", "HOSPITAL", "CLINIC", "CHURCH", "RELIGIOUS",         # institutional
    "PARKING GARAGE", "LODGING", "HOTEL", "GAS STATION",
    "GENERAL COMMERCIAL", "MERCANTILE",
]


def _miami_newbuild_where(scope_f: str, wi_f: str, include_sfr: bool) -> str:
    """New-construction *master building* permits in the City-of-Miami schema.
    Single-family is a 1-unit house (kept out of the multi-family match) — hidden
    unless include_sfr; two-family/duplex stays in either way."""
    types = " OR ".join(f"{wi_f} LIKE '%{t}%'" for t in _NEWBUILD_TYPE_LIKES)
    where = f"{scope_f}='NEW CONSTRUCTION' AND ({types})"
    if not include_sfr:
        where += f" AND NOT ({wi_f} LIKE '%SINGLE FAMILY%' AND {wi_f} NOT LIKE '%MULTI-FAMILY%')"
    return where


def _generic_newbuild_where(cfg: dict, include_sfr: bool) -> str | None:
    """Best-effort new-build filter for harvested city sources that lack the Miami
    WorkItems vocabulary: match 'NEW' in whatever descriptive field(s) exist, and
    (best effort) drop rows that name a single-family house. Returns None when the
    source has no descriptive field to filter on (then the heat is unfiltered)."""
    fields = [f for f in (cfg.get("desc"), cfg.get("type")) if f]
    if not fields:
        return None
    where = "(" + " OR ".join(f"UPPER({f}) LIKE '%NEW%'" for f in fields) + ")"
    if not include_sfr:
        for f in fields:
            where += (f" AND UPPER({f}) NOT LIKE '%SINGLE FAMILY%'"
                      f" AND UPPER({f}) NOT LIKE '%SINGLE-FAMILY%'")
    return where


@app.get("/api/permit-heat")
async def permit_heat(bbox: str = Query(...), limit: int = Query(2000, le=2000),
                      include_sfr: bool = Query(False)):
    """Permit points for a MapLibre heatmap layer, restricted to NEW-CONSTRUCTION
    master building permits and weighted by building size (floor area), so the map
    reads as "where new buildings are going up, and how big." Single-family houses
    are excluded unless include_sfr=true.

    Sources: Miami-Dade county permits inside its bbox (currently token-locked, so
    it falls through), else the nearest harvested city service. The City-of-Miami
    source carries the rich WorkItems/TotalSQFT schema; other cities degrade to a
    'NEW'-text filter weighted by declared value."""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")
    env = json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                      "spatialReference": {"wkid": 4326}})
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    in_mdc = COUNTY_BBOX_MDC[0] <= cx <= COUNTY_BBOX_MDC[2] and COUNTY_BBOX_MDC[1] <= cy <= COUNTY_BBOX_MDC[3]

    # Ordered candidates: MDC first inside its bbox (kept in case public access
    # returns), then every harvested city service near the view. Each candidate
    # carries its registry cfg so the per-source filter can use its real fields.
    # The loop falls through on token errors, outages, and empty results alike.
    candidates = []
    if in_mdc:
        candidates.append({"url": MDC_PERMITS, "src": "Miami-Dade County",
                           "cfg": {"desc": "DESC1", "type": "APPTYPE", "value": "ESTVALUE"}})
    candidates += [{"url": c["url"], "src": f"{c.get('city')}, {c.get('state')}", "cfg": c}
                   for c in _nearby_cities(CITY_PERMITS, cx, cy, deg=0.45) if c.get("url")]
    if not candidates:
        return {"type": "FeatureCollection", "features": [],
                "note": "No permit source covers this view."}

    def _size_weight(sqft) -> float | None:
        # Floor area -> heat weight. 25k sf ≈ 1.0; caps at 12 (~300k sf) so one
        # mega-project can't nuke the scale. This is the "by size" dial.
        try:
            s = float(sqft or 0)
        except (TypeError, ValueError):
            return None
        return round(min(12.0, max(0.4, s / 25000.0)), 2) if s > 0 else None

    def _value_weight(val) -> float:
        # Fallback when a kept building has no floor area (or the source has no sqft
        # field at all): declared $ on a comparable 0.4–12 curve. $10k→~2, $10M→~5.
        try:
            v = float(val or 0)
        except (TypeError, ValueError):
            return 1.0
        return round(min(12.0, max(0.4, math.log10(v) - 2)), 2) if v > 0 else 1.0

    async with httpx.AsyncClient(timeout=14.0) as client:
        for cand in candidates:
            cfg = cand["cfg"]
            miami = cfg.get("newbuild_vocab") == "miami"
            f_sqft = cfg.get("sqft")
            f_val = cfg.get("value")
            if miami:
                where = _miami_newbuild_where(cfg["scope"], cfg["workitems"], include_sfr)
            else:
                where = _generic_newbuild_where(cfg, include_sfr)
            out_fields = ",".join(f for f in (f_sqft, f_val) if f)
            params = {
                "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects", "outFields": out_fields,
                "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "5",
                "resultRecordCount": str(limit), "f": "geojson",
            }
            if where:
                params["where"] = where
            try:
                r = await client.get(f"{cand['url']}/query", params=params)
                r.raise_for_status()
                gj = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            if gj.get("error") or not (gj.get("features") or []):
                continue
            feats = []
            for ft in gj["features"]:
                g = ft.get("geometry") or {}
                if g.get("type") != "Point":
                    continue
                props = ft.get("properties") or {}
                w = _size_weight(props.get(f_sqft)) if f_sqft else None
                if w is None:
                    w = _value_weight(props.get(f_val)) if f_val else 1.0
                feats.append({"type": "Feature", "geometry": g, "properties": {"w": w}})
            if feats:
                return {"type": "FeatureCollection", "features": feats, "source": cand["src"],
                        "mode": "new-construction buildings" + ("" if include_sfr else ", excl. single-family"),
                        "weight": "building floor area (sq ft)" if miami else "declared value"}
    return {"type": "FeatureCollection", "features": [], "note": "Permit source unavailable here."}


# ---------- supply pipeline: competing nearby new-construction permits ----------

# Multifamily/mixed-use signal words checked against whatever descriptive fields a
# permit source has. Bare "NEW" is too weak alone (matches "new water service",
# "new subpanel") — it only counts combined with a building noun, or paired with a
# real est_value over the threshold (checked separately in the filter below).
# A permit is "new supply" if it names a multifamily/mixed-use building type AND
# carries a new-construction signal (not a renovation/alteration of one). Either
# half alone over-matches: "APARTMENT" hits re-roofs; bare "NEW" hits new water
# heaters. Checked together in the filter via _matches_new_supply().
_BLDG_RX = re.compile(r"MULTI.?FAM|APARTMENT|CONDOMINIUM|CONDO\b|MIXED.?USE|"
                      r"RESIDENTIAL TOWER|HIGH.?RISE|MID.?RISE|TOWNHOME|TOWNHOUSE|DWELLING", re.I)
_NEWSIG_RX = re.compile(r"\bNEW\b|CONSTRUCT|ERECT|GROUND.?UP|\bBUILD\b|\bADDITION\b", re.I)
# Strong standalone signals — unambiguous new construction on their own.
_STRONG_RX = re.compile(r"NEW\s+\S*\s*(BUILDING|CONSTRUCTION|APARTMENT|CONDOMINIUM|TOWER|"
                        r"MULTIFAMILY|RESIDENCE|DWELLING)|GROUND.?UP|NEW HIGH.?RISE", re.I)


def _matches_new_supply(text: str) -> bool:
    if _STRONG_RX.search(text):
        return True
    return bool(_BLDG_RX.search(text) and _NEWSIG_RX.search(text))


@app.get("/api/supply-pipeline")
async def supply_pipeline(lon: float = Query(...), lat: float = Query(...),
                          radius_ft: int = Query(5280, le=26400),
                          min_value: int = Query(1_000_000, ge=0)):
    """Competing new-construction nearby: large-value multifamily/mixed-use permits
    within radius, from the same permit sources /api/permits already uses. This is
    a coverage-limited signal (MDC + harvested cities only), not a full pipeline."""
    d = radius_ft / 364320.0
    env = json.dumps({"xmin": lon - d, "ymin": lat - d, "xmax": lon + d, "ymax": lat + d,
                      "spatialReference": {"wkid": 4326}})
    in_mdc = COUNTY_BBOX_MDC[0] <= lon <= COUNTY_BBOX_MDC[2] and COUNTY_BBOX_MDC[1] <= lat <= COUNTY_BBOX_MDC[3]

    # Ordered sources: MDC first inside its bbox, then the nearest harvested city.
    sources = []
    if in_mdc:
        # MDC's DESC1/APPTYPE/TYPE are all coded fields with no free-text "NEW"
        # signal (verified empirically: 0 county-wide rows match) — value
        # threshold is the only reliable filter here.
        sources.append(("mdc", {"url": MDC_PERMITS, "src": "Miami-Dade County",
                                "num": "PROCNUM", "desc": "DESC1", "value": "ESTVALUE",
                                "date": "ISSUDATE", "address": "ADDRESS", "type": None}))
    cfg = next((c for c in _nearby_cities(CITY_PERMITS, lon, lat, deg=0.22) if c.get("url")), None)
    if cfg:
        sources.append(("city", {"url": cfg["url"], "src": f"{cfg.get('city')}, {cfg.get('state')}",
                                 "num": cfg.get("num"), "desc": cfg.get("desc") or cfg.get("type"),
                                 "value": cfg.get("value"), "date": cfg.get("date"),
                                 "address": cfg.get("address"),
                                 "type": cfg.get("type") if cfg.get("type") != (cfg.get("desc") or cfg.get("type")) else None}))
    if not sources:
        return {"available": False, "message": "No permit service wired for this area."}

    feats = kind = src = None
    f_num = f_desc = f_desc2 = f_val = f_date = f_addr = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for k, s in sources:
            f_num, f_desc, f_val = s["num"], s["desc"], s["value"]
            f_date, f_addr, f_desc2 = s["date"], s["address"], s["type"]
            if not f_desc and not f_val:
                continue
            fields = [f for f in (f_num, f_desc, f_desc2, f_val, f_date, f_addr) if f]
            new_fields = [f for f in (f_desc, f_desc2) if f]
            # OR, not AND: a big-dollar permit counts even with generic scope text
            # (true of MDC), and a clearly-named new-build permit counts even under
            # the value bar (true of thinner-value-data harvested city sources).
            or_parts = [f"UPPER({f}) LIKE '%NEW%'" for f in new_fields]
            if f_val:
                or_parts.append(f"{f_val} > {min_value}")
            params = {
                "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(fields),
                "where": "(" + " OR ".join(or_parts) + ")", "returnGeometry": "true", "outSR": "4326",
                "resultRecordCount": "60", "f": "geojson",
            }
            if f_date:
                params["orderByFields"] = f"{f_date} DESC"
            try:
                r = await client.get(f"{s['url']}/query", params=params)
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            if body.get("error"):
                continue  # e.g. MDC BuildingPermit_gdb went token-required upstream
            feats, kind, src = (body.get("features") or []), k, s["src"]
            break
    if feats is None:
        return {"available": False, "message": "Permit source unavailable right now."}

    def _amt(v):
        try:
            n = float(v)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    projects = []
    for ft in feats:
        a = ft.get("properties") or {}  # f=geojson -> attributes live under "properties"
        desc = str(a.get(f_desc) or "").strip()
        desc2 = str(a.get(f_desc2) or "").strip() if f_desc2 else ""
        val = _amt(a.get(f_val)) if f_val else None
        if not (_matches_new_supply(desc) or _matches_new_supply(desc2) or (val and val >= min_value)):
            continue
        if desc2 and desc2.upper() not in desc.upper():
            desc = f"{desc2} — {desc}" if desc else desc2
        g = ft.get("geometry") or {}
        lon_g, lat_g = (g.get("coordinates") or [None, None]) if g.get("type") == "Point" else (None, None)
        if lon_g is None and g.get("type") in ("Polygon", "MultiPolygon"):
            try:
                ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
                lon_g = sum(p[0] for p in ring) / len(ring)
                lat_g = sum(p[1] for p in ring) / len(ring)
            except (KeyError, IndexError, ZeroDivisionError, TypeError):
                pass
        projects.append({
            "permit_number": a.get(f_num) if f_num else None,
            "scope": desc or None,
            "value": round(val) if val else None,
            "date": (_permit_date(a.get(f_date)) if kind == "mdc" else
                     (str(a.get(f_date))[:10] if f_date and a.get(f_date) else None)),
            "address": (str(a.get(f_addr)).strip() if f_addr and a.get(f_addr) else None),
            "lon": lon_g, "lat": lat_g,
        })
    projects.sort(key=lambda p: p.get("value") or 0, reverse=True)
    return {
        "available": True, "source": src, "radius_ft": radius_ft, "min_value": min_value,
        "count": len(projects), "projects": projects[:40],
        "note": "Signal, not a census: limited to jurisdictions with a wired permit feed.",
    }


# ---------- brokers active in the area (from comps-aggregator) ----------

@app.get("/api/brokers")
def brokers(lon: float = Query(...), lat: float = Query(...), radius_ft: int = Query(10560, le=52800)):
    """Brokers/agents active nearby — distinct broker firms from marketing materials tied
    to buildings within radius (comps-aggregator)."""
    if not COMPS_DB.exists():
        return {"available": False, "brokers": []}
    lat_pad = radius_ft / 364320.0
    lng_pad = radius_ft / (364320.0 * max(math.cos(math.radians(lat)), 0.01))
    conn = sqlite3.connect(COMPS_DB)
    conn.row_factory = sqlite3.Row
    try:
        # comps-aggregator hasn't ingested broker_firm yet — say so quickly rather
        # than implying "no brokers active nearby" from an empty column.
        has_broker_rows = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM materials WHERE broker_firm IS NOT NULL AND broker_firm != '')"
        ).fetchone()[0]
        if not has_broker_rows:
            return {"available": False, "note": "no broker data loaded", "brokers": []}
        rows = conn.execute(
            """SELECT m.broker_firm, m.broker_name, m.broker_email, b.name AS building, b.lat, b.lng
               FROM materials m JOIN buildings b ON m.building_id = b.id
               WHERE m.broker_firm IS NOT NULL AND m.broker_firm != ''
                 AND b.lat BETWEEN ? AND ? AND b.lng BETWEEN ? AND ?""",
            [lat - lat_pad, lat + lat_pad, lon - lng_pad, lon + lng_pad],
        ).fetchall()
    except sqlite3.Error:
        return {"available": True, "brokers": []}
    finally:
        conn.close()

    firms: dict[str, dict] = {}
    for r in rows:
        if _haversine_ft(lat, lon, r["lat"], r["lng"]) > radius_ft:
            continue
        f = firms.setdefault(r["broker_firm"], {"firm": r["broker_firm"], "contacts": set(), "buildings": set()})
        if r["broker_name"]:
            f["contacts"].add(r["broker_name"] + (f' · {r["broker_email"]}' if r["broker_email"] else ""))
        if r["building"]:
            f["buildings"].add(r["building"])
    out = [{"firm": v["firm"], "contacts": sorted(v["contacts"])[:4],
            "buildings": sorted(v["buildings"])[:5], "building_count": len(v["buildings"])}
           for v in firms.values()]
    out.sort(key=lambda x: -x["building_count"])
    return {"available": True, "count": len(out), "brokers": out}


# ---------- area context: demographics (Census/ACS) + wetlands (USFWS NWI) ----------

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()  # no longer required — kept for reference

# Esri Living Atlas ACS 2020-2024 "(Latest)" view — keyless, layer 2 = tracts.
# (The Census API itself now rejects keyless requests.)
ACS_LIVING_ATLAS = ("https://P3ePLMYs2RVChkJx.svcs.arcgis.com/P3ePLMYs2RVChkJx/arcgis/"
                    "rest/services/ACS_Population_and_Housing_Basics_View_Boundaries/FeatureServer")
NWI = "https://www.fws.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0"
HUD_FMR = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Fair_Market_Rents/FeatureServer/0"
# National Qualified Opportunity Zones (designated 2018 census tracts)
NATIONAL_OZ = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/Opportunity_Zones_1/FeatureServer/0"

# State-adopted building code (IBC edition) — curated; link out for full text + local amendments.
STATE_BUILDING_CODE = {
    "FL": "Florida Building Code 8th Ed. (2023), based on IBC 2021",
    "NY": "2020 Uniform Code / NYC Building Code (2022, based on IBC 2015)",
    "CA": "2022 California Building Code (Title 24), based on IBC 2021",
    "TX": "No statewide code; IBC adopted locally (most cities IBC 2018/2021)",
    "IL": "IBC adopted locally; Chicago Building Code (2019)",
    "WA": "2021 Washington State Building Code (IBC 2021)",
    "MA": "9th Ed. Massachusetts State Building Code (IBC 2015/2021)",
    "GA": "Georgia State Minimum Codes (IBC 2018 w/ amendments)",
    "AZ": "No statewide code; IBC adopted locally",
    "CO": "No statewide code; IBC adopted locally",
    "NC": "2018 NC State Building Code (IBC 2015 base)",
    "OH": "Ohio Building Code (IBC 2021)", "PA": "PA UCC (IBC 2018)",
    "OR": "2022 Oregon Structural Specialty Code (IBC 2021)",
    "TN": "IBC 2018 (state-adopted)", "WI": "Wisconsin Commercial Building Code (IBC 2015)",
}


def building_code_for_state(state: str | None) -> dict | None:
    if not state:
        return None
    slug = {"FL": "florida", "NY": "new-york", "CA": "california", "TX": "texas", "IL": "illinois",
            "WA": "washington", "MA": "massachusetts", "GA": "georgia", "AZ": "arizona",
            "CO": "colorado", "NC": "north-carolina", "OH": "ohio", "PA": "pennsylvania",
            "OR": "oregon", "TN": "tennessee", "WI": "wisconsin"}.get(state)
    return {
        "adopted": STATE_BUILDING_CODE.get(state, "IBC (edition varies — adopted at state/local level)"),
        "code_url": f"https://up.codes/codes/{slug}" if slug else "https://codes.iccsafe.org",
    }
ACS_VARS = {  # ACS 5-yr variable -> label
    "B01003_001E": "population", "B19013_001E": "median_household_income",
    "B25064_001E": "median_gross_rent", "B25077_001E": "median_home_value",
    "B01002_001E": "median_age", "B25003_003E": "renter_occupied",
    "B25003_001E": "occupied_units",
}


@app.get("/api/area-context")
async def area_context(lon: float = Query(...), lat: float = Query(...)):
    out: dict = {"found": True}
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Census geography (free, no key): point -> state/county/tract + place
        tract = state = county = None
        try:
            r = await client.get(
                "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
                params={"x": lon, "y": lat, "benchmark": "Public_AR_Current",
                        "vintage": "Current_Current", "format": "json"})
            r.raise_for_status()
            g = (r.json().get("result") or {}).get("geographies") or {}
            t = (g.get("Census Tracts") or [{}])[0]
            tract, state, county = t.get("TRACT"), t.get("STATE"), t.get("COUNTY")
            place = (g.get("Incorporated Places") or [{}])[0].get("NAME")
            out["geography"] = {"tract_geoid": t.get("GEOID"), "tract": t.get("NAME"),
                                "place": place, "county_fips": (state or "") + (county or "")}
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            out["geography"] = None

        # 2. ACS demographics — keyless via Esri Living Atlas (ACS 2020-2024
        # "(Latest)" view; the Census API itself now requires a key). One tract
        # point-query covers income/rent/value/population/age/tenure.
        try:
            r = await client.get(f"{ACS_LIVING_ATLAS}/2/query", params={
                "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
                "geometryType": "esriGeometryPoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": ("GEOID,NAME,B01001_001E,B01002_001E,B19049_001E,"
                              "B25003_001E,B25003_003E,B25058_001E,B25077_001E"),
                "returnGeometry": "false", "f": "json"}, timeout=12.0)
            r.raise_for_status()
            feats = (r.json().get("features") or [])
            a = (feats[0].get("attributes") or {}) if feats else {}

            def _n(v):
                try:
                    f = float(v)
                    return f if f >= 0 else None
                except (TypeError, ValueError):
                    return None

            if a:
                demo = {
                    "population": _n(a.get("B01001_001E")),
                    "median_age": _n(a.get("B01002_001E")),
                    "median_household_income": _n(a.get("B19049_001E")),
                    # B25058 is median CONTRACT rent (excludes utilities) — the only
                    # keyless national rent variable; vintage label carries the caveat.
                    "median_gross_rent": _n(a.get("B25058_001E")),
                    "median_home_value": _n(a.get("B25077_001E")),
                }
                occ, rent_occ = _n(a.get("B25003_001E")), _n(a.get("B25003_003E"))
                if occ and rent_occ is not None:
                    demo["renter_pct"] = round(100 * rent_occ / occ)
                demo["vintage"] = "ACS 2020-2024 (tract)"
                out["demographics"] = demo
            else:
                out["demographics"] = None
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            out["demographics"] = None

        # 3. Wetlands (USFWS NWI, free)
        geometry = json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
        try:
            r = await client.get(f"{NWI}/query", params={
                "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "WETLAND_TYPE", "returnGeometry": "false", "f": "json"})
            r.raise_for_status()
            feats = (r.json().get("features") or [])
            out["wetland"] = (feats[0].get("attributes") or {}).get("WETLAND_TYPE") if feats else None
        except (httpx.HTTPError, ValueError):
            out["wetland"] = None

        # 3b. Opportunity Zone (national, free) — designated census tract
        try:
            r = await client.get(f"{NATIONAL_OZ}/query", params={
                "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "CENSUSTRAC,COUNTYNAME", "returnGeometry": "false", "f": "json"})
            r.raise_for_status()
            ozf = (r.json().get("features") or [])
            out["opportunity_zone"] = ((ozf[0].get("attributes") or {}).get("CENSUSTRAC")) if ozf else None
        except (httpx.HTTPError, ValueError):
            out["opportunity_zone"] = None

        # 4. HUD Fair Market Rents (national, free) — max rent by bedroom for the FMR area
        try:
            r = await client.get(f"{HUD_FMR}/query", params={
                "geometry": geometry, "geometryType": "esriGeometryPoint", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR",
                "returnGeometry": "false", "f": "json"})
            r.raise_for_status()
            ff = (r.json().get("features") or [])
            if ff:
                a = ff[0].get("attributes") or {}
                out["fair_market_rent"] = {
                    "area": a.get("FMR_AREANAME"),
                    "studio": a.get("FMR_0BDR"), "br1": a.get("FMR_1BDR"),
                    "br2": a.get("FMR_2BDR"), "br3": a.get("FMR_3BDR"), "br4": a.get("FMR_4BDR"),
                }
        except (httpx.HTTPError, ValueError):
            pass

    # 5. Building code — derive state from the county FIPS (first 2 digits) the geocoder returned.
    county_fips = (out.get("geography") or {}).get("county_fips") or ""
    out["building_code"] = building_code_for_state(_fips_to_state(county_fips[:2]))

    # 6. County economy: BLS QCEW employment (latest annual) + 10-yr population
    # growth from two ACS county vintages. Both keyless, county-level (stable FIPS).
    if len(county_fips) == 5:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(
                    f"https://data.bls.gov/cew/data/api/2024/a/area/{county_fips}.csv",
                    headers={"User-Agent": "us-property-map/1.0"})
                if r.status_code == 200:
                    # total-covered = own_code 0, industry_code 10; CSV, header row present
                    import csv as _csv
                    import io as _io
                    for row in _csv.DictReader(_io.StringIO(r.text)):
                        if row.get("own_code") == "0" and row.get("industry_code") == "10":
                            out["employment"] = {
                                "jobs": int(row["annual_avg_emplvl"]) if row.get("annual_avg_emplvl") else None,
                                "establishments": int(row["annual_avg_estabs"]) if row.get("annual_avg_estabs") else None,
                                "avg_annual_pay": int(row["avg_annual_pay"]) if row.get("avg_annual_pay") else None,
                                "year": row.get("year"), "source": "BLS QCEW",
                            }
                            break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            try:
                cur, old = await asyncio.gather(
                    client.get(f"{ACS_LIVING_ATLAS}/1/query", params={
                        "where": f"GEOID='{county_fips}'", "outFields": "B01001_001E,NAME",
                        "returnGeometry": "false", "f": "json"}),
                    client.get("https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
                               "ACS_10_14_Highlights_Population_Housing_Basics_Boundaries/FeatureServer/1/query",
                               params={"where": f"GEOID='{county_fips}'", "outFields": "B01001_001E",
                                       "returnGeometry": "false", "f": "json"}),
                )
                cn = ((cur.json().get("features") or [{}])[0].get("attributes") or {})
                on = ((old.json().get("features") or [{}])[0].get("attributes") or {})
                p_now = float(cn.get("B01001_001E")) if cn.get("B01001_001E") is not None else None
                p_old = float(on.get("B01001_001E")) if on.get("B01001_001E") is not None else None
                if p_now and p_old and p_old > 0:
                    out["population_growth"] = {
                        "now": int(p_now), "decade_ago": int(p_old),
                        "pct": round(100 * (p_now - p_old) / p_old, 1),
                        "county": cn.get("NAME"), "basis": "ACS 2010-14 → 2020-24",
                    }
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                pass
    return out


_FIPS_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
    "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
    "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
    "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
}


def _fips_to_state(fips2: str) -> str | None:
    return _FIPS_STATE.get(fips2)


# ---------- shared store (see backend/shared_data.py) ----------
# The datasets too large to ship inside the app folder, read from one database
# outside it. Read-only here; the scripts/prospect/ ingest pipeline is the
# producer. Every route answers with empty/None when the store is missing, so
# the app still runs where it has never been built.

@app.get("/api/shared/status")
def shared_status():
    """What the shared store holds, so the UI can say 'not available' instead
    of silently rendering nothing."""
    return shared_data.stats()


@app.get("/api/shared/markets")
def shared_markets(limit: int = Query(100, le=500), min_pop: int = 0,
                   stage: str | None = None, state: str | None = None):
    """National metro scores (469 markets) from the shared store."""
    return {"markets": shared_data.markets(limit=limit, min_pop=min_pop,
                                           stage=stage, state=state)}


@app.get("/api/shared/market/{cbsa}")
def shared_market(cbsa: str):
    m = shared_data.market(cbsa)
    if not m:
        raise HTTPException(404, f"no market {cbsa}")
    return m


@app.get("/api/shared/market-for-county/{fips}")
def shared_market_for_county(fips: str):
    """Metro score for whatever county a parcel sits in -- works anywhere in
    the US, so a click outside Florida still gets market context."""
    m = shared_data.market_for_county(fips)
    if not m:
        raise HTTPException(404, f"no market for county {fips}")
    return m


@app.get("/api/shared/owner-units")
def shared_owner_units(name: str = Query(..., min_length=3),
                       limit: int = Query(60, le=200)):
    """Miami-Dade condo units by owner, straight from the local roll. The live
    ArcGIS owner query takes seconds and carries no condo units at all; this
    is indexed and covers all 391,210."""
    return {"units": shared_data.owner_units(name, limit=limit),
            "rollup": shared_data.owner_rollup(name)}


@app.get("/api/shared/building/{group_key}")
def shared_building(group_key: str):
    """Every unit in a building (units of one condo share the first 9 folio
    digits)."""
    units = shared_data.units_in_building(group_key)
    if not units:
        raise HTTPException(404, f"no units for {group_key}")
    return {"group_key": group_key, "count": len(units), "units": units}


# ---------- static frontend ----------

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
