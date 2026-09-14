"""National site-screening for developers — free federal services, no keys.

One endpoint aggregates, in parallel with per-source guards (a dead upstream
degrades that one section, never the response):
  - USGS EPQS ground elevation
  - EPA Superfund NPL sites nearby (1 mi detail + 3 mi count)
  - NPS National Register: historic-district point-in-polygon + listed properties near
  - USFWS critical habitat point check
  - FAA nearest airport (ADIP national layer)
  - NCES public schools nearby (grade level + enrollment)
  - OpenFEMA disaster declarations for the county (risk history)
Also serves /api/marks.csv — saved-addresses export.

All service URLs verified live 2026-07-06.
"""
import asyncio
import csv
import io
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

router = APIRouter()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MARKS_DB = DATA_DIR / "marks.db"

EPQS = "https://epqs.nationalmap.gov/v1/json"
NPL = ("https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/"
       "Superfund_National_Priorities_List_(NPL)_Sites_with_Status_Information/FeatureServer/0")
NRHP = "https://mapservices.nps.gov/arcgis/rest/services/cultural_resources/nrhp_locations/MapServer"
HABITAT = ("https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/"
           "USFWS_Critical_Habitat/FeatureServer/0")
AIRPORTS = ("https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/"
            "US_Airport/FeatureServer/0")
# NCES school locations (AGO mirror — the nces.ed.gov CCD MapServer rejects
# point-distance queries, so we use this layer: name/address only, no level).
SCHOOLS = ("https://services1.arcgis.com/Ua5sjt3LWTPigjyD/arcgis/rest/services/"
           "Public_School_Locations_Current/FeatureServer/0")
# Miami-Dade Public Schools attendance boundaries (county AGOL org, keyless).
# Point-in-polygon = the schools a parcel is actually ZONED for, which is not
# the same as "nearby schools". Returns nothing outside Miami-Dade.
# Verified live 2026-07-15 (Dadeland → Vineland K-8 / Palmetto Middle / Palmetto Sr).
_MDC_ZONES_ORG = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services"
MDC_SCHOOL_ZONES = {
    "elementary": f"{_MDC_ZONES_ORG}/ElementaryAttendanceBoundary_gdb/FeatureServer/0",
    "middle": f"{_MDC_ZONES_ORG}/MiddleAttendanceBoundary_gdb/FeatureServer/0",
    "high": f"{_MDC_ZONES_ORG}/HighAttendanceBoundary_gdb/FeatureServer/0",
}
OPENFEMA = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
FCC_AREA = "https://geo.fcc.gov/api/census/area"

# ---- Risk & hazard (verified 2026-07-06, all keyless) ----
WILDFIRE_WHP = ("https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/"
                "USFS_EDW_RMRS_WildfireHazardPotentialClassified/ImageServer")
WHP_LABELS = {1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High", 6: "Non-burnable", 7: "Water"}
SEISMIC_DESIGNMAPS = "https://earthquake.usgs.gov/ws/designmaps/asce7-16.json"
CBRS = "https://gis1.wim.usgs.gov/server/rest/services/CBRSMapper/CoastalBarrierResourcesSystem/MapServer/3"
NOAA_SLR_TPL = "https://coast.noaa.gov/arcgis/rest/services/dc_slr/slr_{ft}ft/MapServer/0"

# ---- Environmental expansion (verified 2026-07-06, all keyless, EPA_GEO org) ----
EPA_BROWNFIELDS = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/FRS_INTERESTS_ACRES/FeatureServer/0"
EPA_LUST = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/UST_Finder_Feature_Layer_2/FeatureServer/1"
EPA_RCRA = "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/FRS_INTERESTS_RCRA_ACTIVE/FeatureServer/0"

# ---- Affordable housing / LIHTC incentives (verified 2026-07-06, HUD org, keyless) ----
HUD_ORG = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
HUD_LIHTC = f"{HUD_ORG}/LIHTC/FeatureServer/0"
HUD_QCT = f"{HUD_ORG}/QUALIFIED_CENSUS_TRACTS/FeatureServer/0"
HUD_DDA = f"{HUD_ORG}/Difficult_Development_Areas/FeatureServer/0"


def _pt(lon: float, lat: float) -> str:
    return json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})


def _arcgis_params(lon: float, lat: float, distance_m: int | None, out_fields: str,
                   where: str | None = None, count_only: bool = False) -> dict:
    p = {
        "geometry": _pt(lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": out_fields,
        "returnGeometry": "false", "f": "json",
    }
    if distance_m:
        p["distance"] = str(distance_m)
        p["units"] = "esriSRUnit_Meter"
    if where:
        p["where"] = where
    if count_only:
        p["returnCountOnly"] = "true"
    return p


def _feats(resp: httpx.Response) -> list[dict]:
    try:
        data = resp.json()
    except ValueError:
        return []
    if not isinstance(data, dict) or data.get("error"):
        return []
    return [f.get("attributes") or {} for f in (data.get("features") or [])]


def _haversine_mi(lon1, lat1, lon2, lat2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _elevation(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(EPQS, params={"x": lon, "y": lat, "units": "Feet", "wkid": "4326"})
    try:
        v = r.json().get("value")
        return {"elevation_ft": round(float(v), 1)} if v is not None else None
    except (ValueError, TypeError):
        return None


async def _superfund(client: httpx.AsyncClient, lon: float, lat: float):
    fields = "Site_Name,Status,City,State,Listing_Date,Latitude,Longitude"
    near, cnt3mi = await asyncio.gather(
        client.get(f"{NPL}/query", params=_arcgis_params(lon, lat, 1609, fields)),
        client.get(f"{NPL}/query", params=_arcgis_params(lon, lat, 4828, "OBJECTID", count_only=True)),
    )
    sites = [{
        "name": a.get("Site_Name"), "status": a.get("Status"),
        "city": a.get("City"), "state": a.get("State"),
        "miles": (round(_haversine_mi(lon, lat, a["Longitude"], a["Latitude"]), 2)
                  if a.get("Latitude") is not None else None),
    } for a in _feats(near)[:6]]
    try:
        count_3mi = cnt3mi.json().get("count", 0)
    except ValueError:
        count_3mi = None
    return {"within_1mi": sites, "count_3mi": count_3mi}


async def _historic(client: httpx.AsyncClient, lon: float, lat: float):
    districts, listed = await asyncio.gather(
        client.get(f"{NRHP}/1/query", params=_arcgis_params(lon, lat, None, "RESNAME,Is_NHL,NRIS_Refnum")),
        client.get(f"{NRHP}/0/query", params=_arcgis_params(lon, lat, 500, "RESNAME,Address,CertDate")),
    )
    d = _feats(districts)
    return {
        "in_district": bool(d),
        "district_name": d[0].get("RESNAME") if d else None,
        "is_nhl": bool(d and d[0].get("Is_NHL")),
        "listed_nearby": [{"name": a.get("RESNAME"), "address": a.get("Address")}
                          for a in _feats(listed)[:5]],
    }


async def _habitat(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(f"{HABITAT}/query",
                         params=_arcgis_params(lon, lat, None, "comname,listing_status"))
    hits = _feats(r)
    return {
        "in_critical_habitat": bool(hits),
        "species": [{"name": a.get("comname"), "status": a.get("listing_status")} for a in hits[:4]],
    }


async def _airport(client: httpx.AsyncClient, lon: float, lat: float):
    # LATITUDE/LONGITUDE attributes are DMS strings on this layer — use the
    # returned point geometry (outSR=4326) for the distance instead.
    params = _arcgis_params(lon, lat, 40000, "IDENT,ICAO_ID,NAME,SERVCITY", where="TYPE_CODE='AD'")
    params["returnGeometry"] = "true"
    params["outSR"] = "4326"
    r = await client.get(f"{AIRPORTS}/query", params=params)
    try:
        feats = (r.json().get("features") or [])
    except ValueError:
        return None
    best, best_mi = None, 1e9
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("x") is None:
            continue
        mi = _haversine_mi(lon, lat, g["x"], g["y"])
        if mi < best_mi:
            best, best_mi = (f.get("attributes") or {}), mi
    if not best:
        return None
    return {"ident": best.get("ICAO_ID") or best.get("IDENT"), "name": best.get("NAME"),
            "city": best.get("SERVCITY"), "miles": round(best_mi, 1)}


async def _schools(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(f"{SCHOOLS}/query", params=_arcgis_params(
        lon, lat, 1609, "NAME,STREET,CITY"))
    return [{"name": a.get("NAME"), "street": a.get("STREET"), "city": a.get("CITY")}
            for a in _feats(r)[:6]]


async def _school_zones(client: httpx.AsyncClient, lon: float, lat: float):
    """Assigned (zoned) MDCPS schools — point-in-polygon on attendance boundaries.
    None outside Miami-Dade (no polygon hit at any level)."""
    async def one(url: str):
        r = await client.get(f"{url}/query", params=_arcgis_params(lon, lat, None, "NAME,GRADES"))
        f = _feats(r)
        return {"name": f[0].get("NAME"), "grades": f[0].get("GRADES")} if f else None
    elem, mid, high = await asyncio.gather(
        *(one(u) for u in MDC_SCHOOL_ZONES.values()))
    if not (elem or mid or high):
        return None
    return {"elementary": elem, "middle": mid, "high": high}


async def _wildfire(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(f"{WILDFIRE_WHP}/identify", params={
        "geometry": _pt(lon, lat), "geometryType": "esriGeometryPoint", "sr": "4326",
        "returnCatalogItems": "false", "f": "json"})
    try:
        val = int(float(r.json().get("value")))
    except (ValueError, KeyError, TypeError):
        return None
    if val not in WHP_LABELS:
        return None
    return {"class": WHP_LABELS[val], "code": val}


async def _seismic(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(SEISMIC_DESIGNMAPS, params={
        "latitude": lat, "longitude": lon, "riskCategory": "II", "siteClass": "D", "title": "site-screen"},
        follow_redirects=True)
    try:
        # Offshore / out-of-grid points return response as a string (or null), not a
        # dict — .get on it would raise AttributeError and escape _guard, 500-ing
        # the whole site-screen. Guard the type.
        resp = r.json().get("response")
        d = (resp if isinstance(resp, dict) else {}).get("data") or {}
        pga = d.get("pga")
        return {"pga_g": round(float(pga), 3), "sdc": d.get("sdc")} if pga is not None else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


async def _cbrs(client: httpx.AsyncClient, lon: float, lat: float):
    r = await client.get(f"{CBRS}/query", params=_arcgis_params(lon, lat, None, "Unit,Name,Unit_Type"))
    hits = _feats(r)
    if not hits:
        return {"in_cbrs": False}
    return {"in_cbrs": True, "unit": hits[0].get("Unit"), "name": hits[0].get("Name"), "type": hits[0].get("Unit_Type")}


async def _sea_level_rise(client: httpx.AsyncClient, lon: float, lat: float):
    """Lowest NOAA SLR scenario (ft above MHHW) at which this point floods."""
    for ft in (1, 2, 3, 4, 5, 6):
        try:
            r = await client.get(f"{NOAA_SLR_TPL.format(ft=ft)}/query",
                                 params=_arcgis_params(lon, lat, None, "OBJECTID"))
            if _feats(r):
                return {"floods_at_ft": ft}
        except httpx.HTTPError:
            continue
    return {"floods_at_ft": None}


async def _environmental_extra(client: httpx.AsyncClient, lon: float, lat: float):
    fields = "PRIMARY_NAME,LOCATION_ADDRESS,CITY_NAME,STATE_CODE"
    bf, lust, rcra = await asyncio.gather(
        client.get(f"{EPA_BROWNFIELDS}/query", params=_arcgis_params(lon, lat, 8047, fields)),
        client.get(f"{EPA_LUST}/query", params=_arcgis_params(lon, lat, 4828, "Name,City,State,Status")),
        client.get(f"{EPA_RCRA}/query", params=_arcgis_params(lon, lat, 4828, fields + ",INTEREST_TYPE")),
    )
    return {
        "brownfields_5mi": [{"name": a.get("PRIMARY_NAME"), "city": a.get("CITY_NAME")} for a in _feats(bf)[:5]],
        "lust_3mi_count": len(_feats(lust)),
        "rcra_3mi": [{"name": a.get("PRIMARY_NAME"), "type": a.get("INTEREST_TYPE")} for a in _feats(rcra)[:5]],
    }


async def _affordable_housing(client: httpx.AsyncClient, lon: float, lat: float):
    lihtc, qct, dda = await asyncio.gather(
        client.get(f"{HUD_LIHTC}/query", params=_arcgis_params(
            lon, lat, 3218, "PROJECT,PROJ_ADD,N_UNITS,LI_UNITS,YR_PIS")),
        client.get(f"{HUD_QCT}/query", params=_arcgis_params(lon, lat, None, "GEOID")),
        client.get(f"{HUD_DDA}/query", params=_arcgis_params(lon, lat, None, "DDA_NAME")),
    )
    lihtc_hits = _feats(lihtc)

    def _yr(v):  # HUD uses 8888/9999 as "unknown year" sentinels
        s = str(v or "").strip()
        return None if s in ("", "8888", "9999") else v

    return {
        "nearby_lihtc": [{"project": a.get("PROJECT"), "address": a.get("PROJ_ADD"),
                          "units": a.get("N_UNITS"), "low_income_units": a.get("LI_UNITS"),
                          "year": _yr(a.get("YR_PIS"))} for a in lihtc_hits[:5]],
        "is_qct": bool(_feats(qct)),
        "is_dda": bool(_feats(dda)),
        "dda_name": (_feats(dda)[0].get("DDA_NAME") if _feats(dda) else None),
    }


async def _disasters(client: httpx.AsyncClient, lon: float, lat: float):
    """County FIPS via FCC, then OpenFEMA declarations for that county (newest first)."""
    try:
        fcc = await client.get(FCC_AREA, params={"lat": lat, "lon": lon, "format": "json"})
        res = (fcc.json().get("results") or [])
    except (httpx.HTTPError, ValueError):
        return None
    if not res:
        return None
    fips = res[0].get("county_fips") or ""
    county_name = res[0].get("county_name")
    if len(fips) != 5:
        return None
    filt = f"fipsStateCode eq '{fips[:2]}' and fipsCountyCode eq '{fips[2:]}'"
    r = await client.get(OPENFEMA, params={
        "$filter": filt, "$orderby": "declarationDate desc", "$top": "8", "$format": "json"})
    try:
        decls = r.json().get("DisasterDeclarationsSummaries") or []
    except ValueError:
        decls = []
    return {
        "county": county_name,
        "declarations": [{
            "id": d.get("femaDeclarationString"), "type": d.get("incidentType"),
            "title": d.get("declarationTitle"),
            "date": (d.get("declarationDate") or "")[:10],
        } for d in decls],
        # 10-year NOAA severe-weather counts for the county (local dataset)
        "storm_history": _storm_events().get(fips),
    }


_STORMS: dict | None = None


def _storm_events() -> dict:
    global _STORMS
    if _STORMS is None:
        try:
            _STORMS = (json.loads((DATA_DIR / "storm_events_county.json").read_text(encoding="utf-8"))
                       .get("counties") or {})
        except (OSError, ValueError, AttributeError):
            _STORMS = {}
    return _STORMS


async def _guard(coro):
    """Per-source guard: any failure -> None for that section only (never 500 the
    whole site-screen). Broad on purpose — one flaky upstream must not sink 12 others."""
    try:
        return await coro
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None


@router.get("/api/site-screen")
async def site_screen(lon: float = Query(...), lat: float = Query(...)):
    """Developer site screening at a point — elevation, environmental, historic,
    habitat, airport access, schools, county disaster history. All free national
    sources; each section degrades to null independently."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        (elev, superfund, historic, habitat, airport, schools, zones, disasters,
         wildfire, seismic, cbrs, slr, enviro, afford) = await asyncio.gather(
            _guard(_elevation(client, lon, lat)),
            _guard(_superfund(client, lon, lat)),
            _guard(_historic(client, lon, lat)),
            _guard(_habitat(client, lon, lat)),
            _guard(_airport(client, lon, lat)),
            _guard(_schools(client, lon, lat)),
            _guard(_school_zones(client, lon, lat)),
            _guard(_disasters(client, lon, lat)),
            _guard(_wildfire(client, lon, lat)),
            _guard(_seismic(client, lon, lat)),
            _guard(_cbrs(client, lon, lat)),
            _guard(_sea_level_rise(client, lon, lat)),
            _guard(_environmental_extra(client, lon, lat)),
            _guard(_affordable_housing(client, lon, lat)),
        )
    # NOAA's queryable SLR layer 0 is "low-lying areas", which in places behaves as a
    # coastal coverage mask (confirmed: downtown LA at 284 ft "floods at 1 ft"). A hit
    # is only credible when the site is actually near sea level — gate on elevation.
    if slr and slr.get("floods_at_ft") is not None:
        e = (elev or {}).get("elevation_ft")
        if e is not None and e > slr["floods_at_ft"] + 10:
            slr = {"floods_at_ft": None}
    return {
        "elevation": elev,
        "superfund": superfund,
        "historic": historic,
        "critical_habitat": habitat,
        "nearest_airport": airport,
        "schools_1mi": schools,
        "school_zones": zones,
        "disaster_history": disasters,
        "wildfire_hazard": wildfire,
        "seismic": seismic,
        "coastal_barrier": cbrs,
        "sea_level_rise": slr,
        "environmental": enviro,
        "affordable_housing": afford,
        "sources": ("USGS EPQS/Seismic · EPA NPL/Brownfields/LUST/RCRA · NPS NRHP · USFWS · "
                    "FAA ADIP · NCES CCD · MDCPS zones · OpenFEMA · USFS WHP · NOAA SLR · HUD LIHTC/QCT/DDA"),
    }


# ---------- nearby amenities (OpenStreetMap Overpass, national) ----------

# Public Overpass instances. Live-tested 2026-07: overpass-api.de is the only
# reliably fast one (2 slots/IP, ~3-10s); kumi hangs intermittently; private.coffee
# is heavily queued (last resort). mail.ru and osm.jp are dead — omitted.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_OSM_UA = {"User-Agent": "us-property-map/1.0 (local dev tool)"}

# One spatial union (fast) — categorization happens in Python from tags.
# Per-category around-filters were measured ~3x slower on the same data.
_AMENITY_UNION = (
    'nw(around:{r},{lat},{lon})["amenity"~"^(restaurant|cafe|school|pharmacy|bank|bar|pub|hospital|clinic)$"];'
    'nw(around:{r},{lat},{lon})["shop"~"^(supermarket|greengrocer|convenience)$"];'
    'nwr(around:{r},{lat},{lon})["leisure"~"^(fitness_centre|park)$"];'
    'nw(around:{r},{lat},{lon})["public_transport"="station"];'
)

# tiny in-process cache — amenities barely change; also protects mirror rate limits
_AMEN_CACHE: dict = {}
_AMEN_CACHE_MAX = 500


@router.get("/api/amenities")
async def amenities(lon: float = Query(...), lat: float = Query(...), radius_m: int = Query(800, le=2000)):
    """Walking-distance amenities from OpenStreetMap (ODbL). Counts by category +
    a few named places per category. Tries several public Overpass mirrors."""
    ck = (round(lat, 4), round(lon, 4), radius_m)
    if ck in _AMEN_CACHE:
        return _AMEN_CACHE[ck]
    q = ("[out:json][timeout:20];("
         + _AMENITY_UNION.format(r=radius_m, lat=lat, lon=lon)
         + ");out tags center 250;")
    data = None
    used = None

    async def _try(client: httpx.AsyncClient, base: str):
        r = await client.post(base, data={"data": q})
        r.raise_for_status()
        j = r.json()
        if not j.get("elements") and j.get("remark"):
            raise ValueError(j["remark"])  # server-side error remark (busy/timeout)
        return base, j

    # Race all mirrors concurrently — first good answer wins, losers are cancelled.
    # (Serially, one hung instance eats the whole request budget.)
    async with httpx.AsyncClient(timeout=12.0, headers=_OSM_UA) as client:
        tasks = [asyncio.create_task(_try(client, b)) for b in OVERPASS_MIRRORS]
        try:
            for fut in asyncio.as_completed(tasks, timeout=14.0):
                try:
                    used, data = await fut
                    break
                except (httpx.HTTPError, ValueError):
                    continue
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()
    if not data:
        return {"available": False, "message": "OSM Overpass mirrors unavailable right now."}

    def _cat(tags: dict) -> str | None:
        a, s, l, pt = tags.get("amenity"), tags.get("shop"), tags.get("leisure"), tags.get("public_transport")
        if s in ("supermarket", "greengrocer", "convenience"):
            return "grocery"
        if a == "restaurant":
            return "restaurant"
        if a == "cafe":
            return "cafe"
        if a in ("bar", "pub"):
            return "bar"
        if a == "school":
            return "school"
        if a == "pharmacy":
            return "pharmacy"
        if a == "bank":
            return "bank"
        if l == "fitness_centre":
            return "gym"
        if l == "park":
            return "park"
        if a in ("hospital", "clinic"):
            return "health"
        if pt == "station":
            return "transit"
        return None

    counts: dict = {}
    names: dict = {}
    for el in (data.get("elements") or []):
        tags = el.get("tags") or {}
        c = _cat(tags)
        if not c:
            continue
        counts[c] = counts.get(c, 0) + 1
        nm = tags.get("name")
        if nm and len(names.get(c, [])) < 4 and nm not in names.get(c, []):
            names.setdefault(c, []).append(nm)
    out = {"available": True, "radius_m": radius_m, "counts": counts, "names": names,
           "source": "OpenStreetMap (ODbL)", "mirror": used}
    if len(_AMEN_CACHE) >= _AMEN_CACHE_MAX:
        _AMEN_CACHE.clear()
    _AMEN_CACHE[ck] = out
    return out


# ---------- market rents (Zillow Research ZORI — public data) ----------

_ZORI: dict | None = None


def _zori() -> dict:
    global _ZORI
    if _ZORI is None:
        try:
            _ZORI = json.loads((DATA_DIR / "zori_rents.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _ZORI = {}
    return _ZORI


_SBA: dict | None = None


def _sba() -> dict:
    global _SBA
    if _SBA is None:
        try:
            _SBA = json.loads((DATA_DIR / "sba_re_loans.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _SBA = {}
    return _SBA


@router.get("/api/financing")
def financing(zip: str = Query(..., min_length=5, max_length=5)):
    """SBA 7(a)/504 real-estate loans recorded in a ZIP (public FOIA data) — a
    financing-activity signal. Loan rows: [zip,state,county,city,name,amount,ym,program,naics,jobs]."""
    z = _sba()
    loans = z.get("loans") or []
    if not loans:
        return {"available": False, "message": "SBA dataset not loaded."}
    z5 = (zip or "").strip()[:5]
    hits = [l for l in loans if l and l[0] == z5]
    if not hits:
        return {"found": False, "zip": z5, "available": True,
                "attribution": z.get("attribution"), "message": "No SBA real-estate loans in this ZIP."}
    hits.sort(key=lambda l: l[5] or 0, reverse=True)
    total = sum((l[5] or 0) for l in hits)
    return {
        "found": True, "available": True, "zip": z5, "count": len(hits),
        "total_amount": total,
        "loans": [{"name": l[4], "amount": l[5], "date": l[6], "program": l[7],
                   "use": l[8], "jobs": l[9]} for l in hits[:12]],
        "attribution": z.get("attribution"), "asof": z.get("asof"),
    }


@router.get("/api/market-rents")
def market_rents(zip: str | None = Query(None, max_length=10), metro: str | None = Query(None)):
    """Market asking-rent benchmark (Zillow Research ZORI, public). Looks up by
    ZIP first, falls back to metro-name contains-match."""
    z = _zori()
    if not z:
        return {"available": False, "message": "ZORI dataset not loaded (data/zori_rents.json missing)."}
    out = {"available": True, "asof": z.get("asof"), "attribution": z.get("attribution") or "Data: Zillow Research (ZORI)"}
    zip5 = (zip or "").strip()[:5]
    if zip5 and (z.get("zip") or {}).get(zip5):
        out["zip"] = {"code": zip5, **z["zip"][zip5]}
    if metro:
        m = metro.strip().lower()
        hit = next(((k, v) for k, v in (z.get("metro") or {}).items() if m in k.lower()), None)
        if hit:
            out["metro"] = {"name": hit[0], **hit[1]}
    if "zip" not in out and "metro" not in out:
        out["found"] = False
    return out


# ---------- NYC ACRIS: deeds & mortgages by BBL (NYC Open Data / Socrata, keyless) ----------

SOCRATA = "https://data.cityofnewyork.us/resource"
ACRIS_LEGALS = f"{SOCRATA}/8h5j-fqxa.json"
ACRIS_MASTER = f"{SOCRATA}/bnx9-e6tj.json"
ACRIS_PARTIES = f"{SOCRATA}/636b-3b5g.json"
ACRIS_REFS = f"{SOCRATA}/pwkr-dpni.json"
NYC_SALES = f"{SOCRATA}/usep-8jbt.json"

# doc types worth surfacing (sales + financing). Master's doc_type is free text —
# some carry embedded commas ("DEED, TS"), handled by quoting each value.
_ACRIS_DOCS = ["DEED", "DEEDO", "DEED, LE", "DEED, TS", "DEED, RC", "DEEDP", "CORRD",
               "MTGE", "CORRM", "AGMT", "M&CON", "SPRD", "AL&R", "ASST", "SAT"]


def _soql_in(values: list[str]) -> str:
    return "in(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def _acris_date(m: dict) -> str:
    return ((m.get("document_date") or m.get("recorded_datetime") or ""))[:10]


@router.get("/api/acris")
async def acris(bbl: str = Query(..., min_length=10, max_length=11)):
    """NYC ACRIS recorded documents for a BBL: latest deeds (who bought, from whom,
    for how much) and mortgages with satisfaction status (open vs satisfied via the
    References dataset). Keyless NYC Open Data."""
    digits = re.sub(r"\D", "", bbl)
    if len(digits) != 10:
        return {"found": False, "message": "BBL must be 10 digits (borough+block+lot)."}
    boro, block, lot = int(digits[0]), int(digits[1:6]), int(digits[6:10])
    acris_url = f"https://a836-acris.nyc.gov/DS/DocumentSearch/BBL?bbl={digits}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Legals: BBL -> document ids (borough/block/lot are Socrata NUMBERs)
        try:
            r = await client.get(ACRIS_LEGALS, params={
                "$where": f"borough={boro} AND block={block} AND lot={lot}",
                "$select": "distinct document_id", "$limit": "400"})
            r.raise_for_status()
            doc_ids = [row["document_id"] for row in r.json() if row.get("document_id")]
        except (httpx.HTTPError, ValueError, KeyError):
            return {"found": False, "message": "ACRIS unavailable right now.", "acris_url": acris_url}
        if not doc_ids:
            return {"found": False, "message": "No ACRIS documents for this lot.", "acris_url": acris_url}

        # 2. Master rows for those docs (chunked in(...) queries), sales/financing types only
        masters: list[dict] = []
        for i in range(0, min(len(doc_ids), 320), 80):
            chunk = doc_ids[i:i + 80]
            try:
                r = await client.get(ACRIS_MASTER, params={
                    "$where": f"document_id {_soql_in(chunk)} AND doc_type {_soql_in(_ACRIS_DOCS)}",
                    "$select": "document_id,crfn,doc_type,document_amt,document_date,recorded_datetime",
                    "$limit": "400"})
                r.raise_for_status()
                masters.extend(r.json())
            except (httpx.HTTPError, ValueError):
                continue
        if not masters:
            return {"found": False, "message": "No deed/mortgage documents on this lot.", "acris_url": acris_url}
        masters.sort(key=_acris_date, reverse=True)

        deeds = [m for m in masters if (m.get("doc_type") or "").startswith("DEED") or m.get("doc_type") == "CORRD"]
        mtges = [m for m in masters if m.get("doc_type") in ("MTGE", "CORRM", "AGMT", "M&CON", "SPRD")]
        sats = [m for m in masters if m.get("doc_type") == "SAT"]
        asts = [m for m in masters if m.get("doc_type") in ("ASST", "AL&R")]

        # 3. Parties for the interesting docs (latest 3 deeds + all mortgages + sats)
        want = [m["document_id"] for m in (deeds[:3] + mtges[:8] + sats[:8])]
        parties: dict[str, dict] = {}
        for i in range(0, len(want), 80):
            chunk = want[i:i + 80]
            if not chunk:
                break
            try:
                r = await client.get(ACRIS_PARTIES, params={
                    "$where": f"document_id {_soql_in(chunk)}",
                    "$select": "document_id,party_type,name", "$limit": "800"})
                r.raise_for_status()
                for row in r.json():
                    d = parties.setdefault(row.get("document_id") or "", {"1": [], "2": []})
                    pt = str(row.get("party_type") or "")
                    if pt in d and row.get("name") and len(d[pt]) < 4 and row["name"] not in d[pt]:
                        d[pt].append(row["name"])
            except (httpx.HTTPError, ValueError):
                continue

        # 4. Satisfaction linkage: SAT docs reference the satisfied mortgage's CRFN
        satisfied_crfns: set = set()
        sat_ids = [m["document_id"] for m in sats[:12]]
        if sat_ids:
            try:
                r = await client.get(ACRIS_REFS, params={
                    "$where": f"document_id {_soql_in(sat_ids)}",
                    "$select": "reference_by_crfn_", "$limit": "200"})
                r.raise_for_status()
                satisfied_crfns = {row.get("reference_by_crfn_") for row in r.json() if row.get("reference_by_crfn_")}
            except (httpx.HTTPError, ValueError):
                pass

    def _amt(m):
        try:
            v = float(m.get("document_amt") or 0)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def _p(m, pt):
        return (parties.get(m["document_id"], {}).get(pt) or [])

    out_deeds = [{
        "date": _acris_date(m), "type": m.get("doc_type"), "amount": _amt(m),
        "seller": _p(m, "1"), "buyer": _p(m, "2"),
    } for m in deeds[:3]]
    out_mtges = [{
        "date": _acris_date(m), "type": m.get("doc_type"), "amount": _amt(m),
        "borrower": _p(m, "1"), "lender": _p(m, "2"),
        "satisfied": bool(m.get("crfn") and m["crfn"] in satisfied_crfns),
    } for m in mtges[:6]]
    return {
        "found": True, "bbl": digits, "acris_url": acris_url,
        "documents_on_lot": len(doc_ids),
        "deeds": out_deeds,
        "mortgages": out_mtges,
        "open_mortgages": sum(1 for m in out_mtges if m["amount"] and not m["satisfied"]),
        "satisfactions": len(sats), "assignments": len(asts),
        "note": ("Satisfaction matching is CRFN-based (post-2003 docs); older mortgages may "
                 "show as open even if paid off — confirm in ACRIS."),
        "source": "NYC ACRIS (Open Data)",
    }


# Cook County (Chicago) — Assessor Parcel Sales + Parcel Addresses (Socrata),
# verified 2026-07-06: no zip field on the sales table, so address lookup by zip
# happens first, then sales are fetched for those PINs.
COOK_SALES = "https://datacatalog.cookcountyil.gov/resource/wvhk-k5uv.json"
COOK_ADDR = "https://datacatalog.cookcountyil.gov/resource/3723-97qp.json"


async def _cook_investment_sales(client: httpx.AsyncClient, zip: str, min_price: int):
    try:
        ra = await client.get(COOK_ADDR, params={
            "$where": f"prop_address_zipcode_1='{zip}'",
            "$select": "distinct pin,prop_address_full,prop_address_city_name",
            "$limit": "500"})
        ra.raise_for_status()
        addr_rows = ra.json()
    except (httpx.HTTPError, ValueError):
        return {"found": False, "message": "Cook County Assessor data unavailable right now."}
    pins = [row["pin"] for row in addr_rows if row.get("pin")]
    if not pins:
        return {"found": False, "zip": zip, "message": "No Cook County parcels found for this ZIP."}
    addr_by_pin = {row["pin"]: row.get("prop_address_full") for row in addr_rows}

    pin_in = "(" + ",".join("'" + p + "'" for p in pins[:500]) + ")"
    # 3-year floor: Cook's table reaches back to 2005 — without it the stats mix
    # decade-old prices and aren't comparable to NYC's rolling window.
    floor = (date.today() - timedelta(days=3 * 365)).isoformat()
    try:
        rs = await client.get(COOK_SALES, params={
            "$where": (f"pin in {pin_in} AND sale_price>{int(min_price)} "
                       f"AND sale_date>='{floor}' "
                       "AND sale_filter_less_than_10k=false"),
            "$select": "pin,sale_date,sale_price,class,deed_type,seller_name,buyer_name",
            "$order": "sale_date DESC", "$limit": "300"})
        rs.raise_for_status()
        rows = rs.json()
    except (httpx.HTTPError, ValueError):
        return {"found": False, "message": "Cook County sales data unavailable right now."}
    if not rows:
        return {"found": False, "zip": zip, "message": "No qualifying Cook County sales found."}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    prices = sorted(p for p in (_f(x.get("sale_price")) for x in rows) if p)
    med = lambda a: a[len(a) // 2] if a else None
    cats: dict = {}
    for x in rows:
        c = (x.get("class") or "").strip()
        if c:
            cats[c] = cats.get(c, 0) + 1
    top_cats = sorted(cats.items(), key=lambda kv: -kv[1])[:6]
    sales = [{
        "address": addr_by_pin.get(x.get("pin"), x.get("pin")),
        "price": _f(x.get("sale_price")), "date": (x.get("sale_date") or "")[:10],
        "category": f"Class {x.get('class')}" if x.get("class") else None,
        "gsf": None, "units": None,
    } for x in rows[:40]]
    dates = [x.get("sale_date", "")[:10] for x in rows if x.get("sale_date")]
    return {
        "found": True, "zip": zip,
        "window": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
        "stats": {
            "count": len(rows), "volume": round(sum(prices)),
            "truncated": len(rows) >= 300,  # stats describe only the newest 300 when hit
            "median_price": round(med(prices)) if prices else None,
            "max_price": round(prices[-1]) if prices else None,
            "median_ppsf": None,  # Cook's sales table has no square-footage field
            "top_categories": [{"category": f"Class {c}", "count": n} for c, n in top_cats],
        },
        "sales": sales,
        "source": "Cook County Assessor Parcel Sales (Open Data)",
        "note": (f"Arm's-length filter: sale price > ${min_price:,} and Cook's own "
                 "sale_filter_less_than_10k flag. No square-footage field on this dataset."),
    }


@router.get("/api/investment-sales")
async def investment_sales(zip: str = Query(..., min_length=5, max_length=5),
                           min_price: int = Query(100000, ge=0),
                           state: str | None = Query(None)):
    """Recent recorded sales + investment statistics by ZIP. NYC: DOF Rolling
    Sales (default). Illinois: Cook County Assessor Parcel Sales."""
    if not zip.isdigit():
        return {"found": False, "message": "zip must be 5 digits"}
    if (state or "").upper() == "IL":
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await _cook_investment_sales(client, zip, min_price)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(NYC_SALES, params={
                "$where": f"zip_code='{zip}' AND sale_price>{int(min_price)}",
                "$select": ("address,apartment_number,sale_price,sale_date,building_class_category,"
                            "gross_square_feet,residential_units,commercial_units,total_units"),
                "$order": "sale_date DESC", "$limit": "500"})
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError):
            return {"found": False, "message": "NYC Rolling Sales unavailable right now."}
    if not rows:
        return {"found": False, "zip": zip, "message": "No qualifying sales in the rolling window."}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    prices = sorted(p for p in (_f(x.get("sale_price")) for x in rows) if p)
    ppsf = sorted(p / s for p, s in ((_f(x.get("sale_price")), _f(x.get("gross_square_feet"))) for x in rows)
                  if p and s and s > 500)
    med = lambda a: a[len(a) // 2] if a else None
    cats: dict = {}
    for x in rows:
        c = (x.get("building_class_category") or "").strip()
        if c:
            cats[c] = cats.get(c, 0) + 1
    top_cats = sorted(cats.items(), key=lambda kv: -kv[1])[:6]
    def _addr(x):  # DOF `address` already embeds the unit — only append if missing
        a = (x.get("address") or "").strip()
        apt = (x.get("apartment_number") or "").strip()
        return a if (not apt or a.upper().endswith(apt.upper())) else f"{a} {apt}"
    sales = [{
        "address": _addr(x),
        "price": _f(x.get("sale_price")), "date": (x.get("sale_date") or "")[:10],
        "category": (x.get("building_class_category") or "").strip(),
        "gsf": _f(x.get("gross_square_feet")),
        "units": _f(x.get("total_units")) or _f(x.get("residential_units")),
    } for x in rows[:40]]
    dates = [x.get("sale_date", "")[:10] for x in rows if x.get("sale_date")]
    return {
        "found": True, "zip": zip,
        "window": {"from": min(dates) if dates else None, "to": max(dates) if dates else None},
        "stats": {
            "count": len(rows), "volume": round(sum(prices)),
            "truncated": len(rows) >= 500,  # stats describe only the newest 500 when hit
            "median_price": round(med(prices)) if prices else None,
            "max_price": round(prices[-1]) if prices else None,
            "median_ppsf": round(med(ppsf)) if ppsf else None,
            "top_categories": [{"category": c, "count": n} for c, n in top_cats],
        },
        "sales": sales,
        "source": "NYC DOF Rolling Sales (Open Data)",
        "note": f"Arm's-length filter: sale price > ${min_price:,}.",
    }


# ---------- licensed broker directory (FL DBPR public extract; links elsewhere) ----------

_BROKERS: list | None = None

# Official state real-estate license lookup portals (public records).
STATE_LICENSE_LOOKUP = {
    "FL": "https://www.myfloridalicense.com/wl11.asp?mode=0&SID=",
    "NY": "https://appext20.dos.ny.gov/nydos/selSearchType.do",
    "TX": "https://www.trec.texas.gov/apps/license-holder-search/",
    "CA": "https://www2.dre.ca.gov/PublicASP/pplinfo.asp",
    "GA": "https://ata.grec.state.ga.us/",
    "NC": "https://www.ncrec.gov/licensing/licenseesearch",
    "CO": "https://apps2.colorado.gov/dora/licensing/lookup/licenselookup.aspx",
    "WA": "https://professions.dol.wa.gov/s/license-lookup",
    "TN": "https://verify.tn.gov/",
    "IL": "https://online-dfpr.micropact.com/lookup/licenselookup.aspx",
}


def _brokers() -> list:
    global _BROKERS
    if _BROKERS is None:
        try:
            _BROKERS = json.loads((DATA_DIR / "brokers_fl.json").read_text(encoding="utf-8"))
            if not isinstance(_BROKERS, list):
                _BROKERS = []
        except (OSError, ValueError):
            _BROKERS = []
    return _BROKERS


def _norm_cnty(s: str) -> str:
    c = (s or "").strip().lower().replace("miami-dade", "dade").replace(" county", "")
    return c


@router.get("/api/broker-directory")
def broker_directory(county: str | None = Query(None), q: str | None = Query(None, min_length=2),
                     state: str | None = Query(None), limit: int = Query(25, le=100),
                     btype: str | None = Query(None, pattern="^(broker|brokerage)$")):
    """Licensed real-estate brokers. Florida: full public DBPR licensee extract,
    searchable by county and name. Other states: official license-lookup link."""
    st = (state or "FL").upper()
    if st != "FL":
        return {"available": False, "state": st,
                "lookup_url": STATE_LICENSE_LOOKUP.get(st),
                "message": f"{st} broker roster isn't bundled — use the official {st} license lookup."}
    rows = _brokers()
    if not rows:
        return {"available": False, "message": "FL broker extract not loaded (data/brokers_fl.json missing)."}
    cn = _norm_cnty(county) if county else None
    qn = (q or "").strip().lower()
    out = []
    total_county = 0
    for b in rows:
        if cn and _norm_cnty(b.get("county") or "") != cn:
            continue
        if btype and b.get("type") != btype:
            continue
        total_county += 1
        if qn and qn not in (b.get("name") or "").lower() and qn not in (b.get("dba") or "").lower():
            continue
        out.append(b)
        if len(out) >= limit and not qn:
            # keep counting total_county but stop collecting
            continue
    return {"available": True, "count": total_county if cn else len(rows),
            "matches": len(out), "brokers": out[:limit],
            "source": "FL DBPR public licensee extract"}


@router.get("/api/marks.csv")
def marks_csv():
    """Export saved addresses as CSV (Excel-friendly)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "address", "folio", "zone", "county", "note", "lat", "lon", "created_at"])
    if MARKS_DB.exists():
        with closing(sqlite3.connect(MARKS_DB)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id,address,folio,zone,county,note,lat,lon,created_at "
                    "FROM marks ORDER BY created_at DESC").fetchall()
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                w.writerow([r["id"], r["address"], r["folio"], r["zone"], r["county"],
                            r["note"], r["lat"], r["lon"], r["created_at"]])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=saved-addresses.csv"})
