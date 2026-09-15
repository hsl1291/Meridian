"""Development capacity — what can actually be built on a site.

The question that follows a termination target: you assemble the building, then
what? This resolves a point against the county's zoning layer, reads the
by-right envelope off it, and lays out unit yield under each Florida path that
raises it.

Every number carries a `basis` string naming the field or statute it came from.
Nothing here is a hand-entered constant except the statutory thresholds cited
inline, because an entitlement number nobody can trace is worse than no number.

Scope: whichever county `config.county` names, using that county's
`zoning_layer` from `config.counties`. It was hardcoded to Miami-Dade's layer,
so every other county resolved against Miami's polygons. Where no layer is
wired, `analyze` reports that rather than borrowing another jurisdiction's --
a confident envelope for the wrong county is worse than no envelope.

NOT a substitute for a zoning attorney. By-right envelopes here ignore
setbacks, lot coverage interaction, parking, unit-mix rules and site geometry,
all of which cut real yield. Treat output as screening, not entitlement.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from ..shared_paths import shared_layers
from threading import RLock




LAYERS_DIR = shared_layers()

SQFT_PER_ACRE = 43_560.0

# The zoning layer this county publishes. Was hardcoded to Miami-Dade's, so
# every other county resolved against Miami's polygons or nothing at all.
_CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))


def _zoning_layer() -> str:
    counties = _CFG.get("counties") or {}
    key = (_CFG.get("county") or "").upper()
    entry = counties.get(key) or next(
        (v for v in counties.values()
         if isinstance(v, dict) and v.get("dbpr_county", "").upper() == key), {})
    return entry.get("zoning_layer") or ""


ZONING_LAYER = _zoning_layer()
COUNTY_NAME = _CFG.get("county") or "this county"

# FS 166.04151(7) "Live Local Act". A qualifying project gets the highest
# density allowed on ANY land in the jurisdiction where residential is allowed,
# and the highest height allowed within 1 mile (floor of 3 stories), through
# administrative approval -- no rezoning, no comp-plan amendment.
LIVE_LOCAL_AFFORDABLE_SHARE = 0.40   # of total units
LIVE_LOCAL_AMI_CEILING = 120         # percent of AMI
LIVE_LOCAL_TERM_YEARS = 30
LIVE_LOCAL_HEIGHT_RADIUS_MI = 1.0
LIVE_LOCAL_MIN_STORIES = 3

# Zoning categories the Act reaches: commercial, industrial, mixed-use.
_LL_LUTYPES = {"C", "I", "IPA"}
_LL_MIXUSE = {"YES", "MIX"}

_cache: dict[str, list] = {}
# RLock, not Lock: _grid() and _jurisdiction_ceiling() hold the lock and then
# call _load(), which takes it again. A plain Lock deadlocks there.
_lock = RLock()


# ── geometry ────────────────────────────────────────────────────────────────

def _bbox(coords) -> tuple:
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for sub in c:
                walk(sub)

    walk(coords)
    return min(xs), min(ys), max(xs), max(ys)


def _in_ring(x: float, y: float, ring) -> bool:
    """Ray casting. Counts crossings of the horizontal ray to the right."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi:
                inside = not inside
        j = i
    return inside


def _in_polygon(x: float, y: float, poly) -> bool:
    """poly is a GeoJSON Polygon coordinate array: [outer, hole, hole...]."""
    if not poly or not _in_ring(x, y, poly[0]):
        return False
    return not any(_in_ring(x, y, hole) for hole in poly[1:])


def _in_geometry(x: float, y: float, geom: dict) -> bool:
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Polygon":
        return _in_polygon(x, y, c)
    if t == "MultiPolygon":
        return any(_in_polygon(x, y, p) for p in c)
    return False


def _load(layer: str) -> list:
    """Features plus a precomputed bbox, so a point test skips almost
    everything before touching ring math."""
    with _lock:
        if layer in _cache:
            return _cache[layer]
        if not layer:
            # No zoning layer wired for this county. Reporting nothing found is
            # correct; falling back to another county's polygons would produce a
            # confident envelope for the wrong jurisdiction.
            _cache[layer] = []
            return []
        fp = LAYERS_DIR / f"{layer}.geojson"
        out: list = []
        if fp.exists():
            try:
                gj = json.loads(fp.read_text(encoding="utf-8"))
                for f in gj.get("features", []):
                    g = f.get("geometry") or {}
                    if not g.get("coordinates"):
                        continue
                    try:
                        out.append((_bbox(g["coordinates"]), g, f.get("properties") or {}))
                    except (ValueError, IndexError, TypeError):
                        continue
            except (OSError, ValueError):
                out = []
        _cache[layer] = out
        return out


_GRID = 0.01          # ~1.1 km cells; zoning polygons are far smaller
_grids: dict[str, dict] = {}


def _cell(lon: float, lat: float) -> tuple:
    return (int(lon / _GRID), int(lat / _GRID))


def _grid(layer: str) -> dict:
    """Bucket features by grid cell. Scanning 4,606 bboxes per point test was
    the whole cost of an analysis; this makes it a dict lookup."""
    with _lock:
        g = _grids.get(layer)
        if g is not None:
            return g
        g = {}
        for item in _load(layer):
            (x0, y0, x1, y1) = item[0]
            for cx in range(int(x0 / _GRID), int(x1 / _GRID) + 1):
                for cy in range(int(y0 / _GRID), int(y1 / _GRID) + 1):
                    g.setdefault((cx, cy), []).append(item)
        _grids[layer] = g
        return g


def _hit(layer: str, lon: float, lat: float) -> dict | None:
    for (x0, y0, x1, y1), geom, props in _grid(layer).get(_cell(lon, lat), ()):
        if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_geometry(lon, lat, geom):
            return props
    return None


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


# ── jurisdiction ceilings (what Live Local grants) ──────────────────────────────

_ceilings: dict[str, dict] | None = None


def _jurisdiction_ceiling(munic: str) -> dict:
    """Highest density allowed anywhere in the municipality. This is exactly
    what the Act grants, so it is derived from the zoning layer rather than
    assumed. Computed once for all 35 municipalities, then cached."""
    global _ceilings
    with _lock:
        if _ceilings is None:
            acc: dict[str, float] = {}
            for _, _, p in _load(ZONING_LAYER):
                d = _num(p.get("DENSITY"))
                if not d:
                    continue
                m = p.get("MUNICNAME") or ""
                if d > acc.get(m, 0.0):
                    acc[n] = d
            _ceilings = {m: {"max_density": v} for m, v in acc.items()}
    return _ceilings.get(munic, {"max_density": None})


_height_cache: dict[tuple, float | None] = {}


def _height_within(lon: float, lat: float, miles: float) -> float | None:
    key = (round(lon, 3), round(lat, 3), miles)
    if key in _height_cache:
        return _height_cache[key]
    _height_cache[key] = v = _height_within_uncached(lon, lat, miles)
    return v


def _height_within_uncached(lon: float, lat: float, miles: float) -> float | None:
    """Tallest MAXHEIGHT (stories) among zoning polygons whose bbox falls
    within `miles`. Degrees-to-miles is latitude-corrected."""
    dlat = miles / 69.0
    dlon = miles / (69.0 * max(math.cos(math.radians(lat)), 0.1))
    best = 0.0
    for (x0, y0, x1, y1), _, p in _load(ZONING_LAYER):
        if x1 < lon - dlon or x0 > lon + dlon or y1 < lat - dlat or y0 > lat + dlat:
            continue
        h = _num(p.get("MAXHEIGHT"))
        if h and h > best:
            best = h
    return best or None


# ── the analysis ────────────────────────────────────────────────────────────

def analyze(lon: float, lat: float, lot_sf: float | None = None) -> dict:
    z = _hit(ZONING_LAYER, lon, lat)
    if not z:
        return {"resolved": False,
                "reason": (f"no {COUNTY_NAME} zoning polygon covers this point"
                           if ZONING_LAYER else
                           f"no zoning layer is wired for {COUNTY_NAME} — add one to "
                           f"config.counties, or capacity cannot be resolved here"),
                "lon": lon, "lat": lat}

    munic = z.get("MUNICNAME") or ""
    density = _num(z.get("DENSITY"))
    far = _num(z.get("FAR"))
    height = _num(z.get("MAXHEIGHT"))
    lot_acres = (lot_sf / SQFT_PER_ACRE) if lot_sf else None

    overlays = {
        "rapid_transit_zone": _hit("mdc_rapid_transit_zones", lon, lat) is not None,
        "opportunity_zone": _hit("mdc_opportunity_zones", lon, lat) is not None,
        "cra": _hit("mdc_cra", lon, lat) is not None,
        "brownfield": _hit("mdc_brownfields", lon, lat) is not None,
        "historic_district": _hit("mdc_historic_districts", lon, lat) is not None,
    }

    lutype = (z.get("GENRLLUTYPE") or "").upper()
    mixuse = (z.get("MIXUSE") or "").upper()
    ll_eligible = lutype in _LL_LUTYPES or mixuse in _LL_MIXUSE

    base_units = int(lot_acres * density) if (lot_acres and density) else None
    scenarios = []

    scenarios.append({
        "path": f"{z.get('ZONE')} by-right",
        "units": base_units,
        "requirement": "Standard entitlement",
        "basis": (
            f"DENSITY {density:g} u/ac x {lot_acres:.2f} ac" if base_units
            # Distinguish the two reasons a unit count is missing -- saying
            # "no DENSITY" when the district clearly has one is just wrong.
            else f"DENSITY {density:g} u/ac; enter a site area for a unit count"
            if density else "zoning layer carries no DENSITY for this district"
        ),
    })

    ceiling = _jurisdiction_ceiling(munic) if ll_eligible else {"max_density": None}
    ll_density = ceiling.get("max_density")
    if ll_eligible and lot_acres and ll_density:
        ll_units = int(lot_acres * ll_density)
        ll_height = _height_within(lon, lat, LIVE_LOCAL_HEIGHT_RADIUS_MI)
        scenarios.append({
            "path": "Live Local Act (FS 166.04151(7))",
            "units": ll_units,
            "requirement": (f"{LIVE_LOCAL_AFFORDABLE_SHARE:.0%} of units at or below "
                            f"{LIVE_LOCAL_AMI_CEILING}% AMI for {LIVE_LOCAL_TERM_YEARS} years"),
            "basis": (f"highest density in {munic.title()} "
                      f"({ll_density:g} u/ac) x {lot_acres:.2f} ac; "
                      f"height to {max(ll_height or 0, LIVE_LOCAL_MIN_STORIES):g} stories "
                      f"(tallest within {LIVE_LOCAL_HEIGHT_RADIUS_MI:g} mi)"),
            "administrative": True,
        })
    elif not ll_eligible:
        scenarios.append({
            "path": "Live Local Act (FS 166.04151(7))",
            "units": None,
            "requirement": "Not eligible",
            "basis": (f"Act reaches commercial / industrial / mixed-use land; this is "
                      f"{lutype or 'unclassified'}"
                      + (f", MIXUSE={mixuse.title()}" if mixuse else "")),
        })

    if overlays["rapid_transit_zone"]:
        scenarios.append({
            "path": "Rapid Transit Zone",
            "units": None,
            "requirement": "Density set by the RTZ subzone regulations",
            "basis": ("inside a Miami-Dade Rapid Transit Zone; the RTZ standards "
                      "supersede municipal zoning but are not carried in this layer"),
        })

    commercial_sf = int(far * lot_sf) if (far and lot_sf) else None

    return {
        "resolved": True,
        "lon": lon, "lat": lat,
        "zoning": {
            "zone": z.get("ZONE"),
            "description": z.get("ZONEDESC") or z.get("SHORTDESC"),
            "municipality": munic.title(),
            "land_use_type": lutype,
            "mixed_use": mixuse.title() or None,
            "density_per_acre": density,
            "far": far,
            "max_height_stories": height,
            "min_lot_sf": _num(z.get("MINLOTSIZE")),
            "max_lot_coverage": _num(z.get("MAXLOTCOV")),
        },
        "site": {
            "lot_sf": lot_sf,
            "lot_acres": round(lot_acres, 3) if lot_acres else None,
        },
        "commercial_envelope_sf": commercial_sf,
        "overlays": overlays,
        "scenarios": scenarios,
        "caveat": ("Screening only. Ignores setbacks, lot coverage, parking, unit mix "
                   "and site geometry, each of which reduces achievable yield."),
    }


def analyze_state_plane(x: float, y: float, lot_sf: float | None = None) -> dict:
    """Same, from the county's native State Plane Florida East feet (EPSG:2236)
    as stored on pa_parcel."""
    from pyproj import Transformer
    tf = Transformer.from_crs(2236, 4326, always_xy=True)
    lon, lat = tf.transform(x, y)
    return analyze(lon, lat, lot_sf)
