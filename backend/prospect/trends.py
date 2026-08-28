"""Metro trend surfaces for the map — one shaded layer per indicator.

Serves `market` joined to CBSA geometry, with every metric a map layer can
shade by, plus the percentile breaks the legend needs so the colour ramp
matches the real distribution instead of a guessed range.

On "strong market" indicators
-----------------------------
There is no single number that says a market is good, so this exposes the
drivers separately and blends them only where the blend is defensible.

The individual surfaces are raw measurements: population growth, net migration,
the income arrivals bring, wage growth, permitting, prices, rents,
affordability. Those are facts.

Two composites are derived, and both are judgment rather than measurement:

`tightness` — demand percentile minus supply percentile. This is the one that
matters most for rents. A metro pulling people in faster than it permits homes
has pricing power; one permitting into weak migration is building into a
vacancy problem. It is a difference of two percentiles, so it needs no weights
and is trivially auditable: +40 means the metro ranks 40 points higher on
in-migration than on permitting.

`strength` — a weighted blend of five drivers. The weights below are a
considered judgment about what leads rent and value growth, not a fitted model,
and no backtest is claimed for them. They are stated here so anyone can
disagree with a specific number rather than with a black box:

    migration      0.20   net migration rate — the direct source of absorption
    tightness      0.25   demand against permitting — whether supply answers it
    pay growth     0.20   whether tenants can pay more next year
    income premium 0.15   whether arrivals raise or dilute the income base
    affordability  0.20   room before rent growth hits a ceiling

Migration is deliberately weighted lower than it looks like it deserves,
because `tightness` already contains it — the two together give migration an
effective weight near 0.33, not 0.45. Raising the direct term to 0.30 was
tried and mostly amplified an existing concentration without separating
metros any better.

Every component is a percentile within the 469-metro set, so the composite is a
rank blend and cannot be dragged around by one metro's outlier value. Because
the inputs are ranks, `strength` says where a metro sits *relative to the
others* — in a nationally weak year the top of this scale is still the top of a
weak field.

Two honest caveats about what comes out of it:

The top of `strength` is heavily Florida — usually seven or eight of the top
ten among large metros. That is not a weighting artifact; it survives dropping
the migration term, dropping tightness, and doubling affordability. It is what
this data vintage says about in-migration, wage growth and income premium. It
does mean the composite mostly rewards a pattern already visible from a
distance.

`tightness` is the more useful screen for anything non-obvious. Because it is
demand *relative to* supply, it surfaces constrained markets that never appear
near the top of a migration ranking — Scranton, San Jose, Providence, Hartford,
Detroit — where in-migration is unremarkable but permitting is far below it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEO = ROOT / "data" / "cbsa.geojson"
ZORI = ROOT / "data" / "zori_rents.json"

# key -> (label, source column, higher_is_better, unit, description)
METRICS: dict[str, tuple] = {
    "pop_growth":   ("Population growth", "pop_cagr_3yr", True, "%/yr",
                     "Three-year population CAGR."),
    "migration":    ("Net migration", "net_mig_total_rate", True, "/1k",
                     "Net in-migration per 1,000 residents, domestic plus international."),
    "domestic_mig": ("Domestic migration", "net_domestic_mig_rate", True, "/1k",
                     "Net domestic migration per 1,000 — excludes international arrivals."),
    "wealth":       ("Arrivals' income", "inflow_agi_per_return", True, "$",
                     "Average AGI per in-migrating tax return."),
    "agi_premium":  ("Income premium", "agi_premium_pct", True, "%",
                     "Arrivals' AGI against departures'. Negative means the "
                     "in-flow earns less than the out-flow."),
    "pay":          ("Wage level", "avg_annual_pay", True, "$",
                     "Average annual pay across covered employment."),
    "pay_growth":   ("Wage growth", "pay_growth_3yr", True, "%",
                     "Three-year growth in average annual pay."),
    "permits":      ("Permitting", "permits_per_1k", True, "n/1k",
                     "Housing units permitted per 1,000 residents. High is more "
                     "supply — read it with demand, not alone."),
    "mf_share":     ("Multifamily share", "permits_5plus_share", True, "pct",
                     "Share of permitted units in 5+ unit buildings."),
    "home_price":   ("Home price", "zhvi", True, "$",
                     "Zillow typical home value."),
    "price_growth": ("Price growth", "zhvi_yoy", True, "%",
                     "Year-over-year change in typical home value."),
    "affordability": ("Affordability", "price_to_income", False, "x",
                      "Home price over average pay. Lower is more affordable, so "
                      "the ramp is inverted."),
    "rent":         ("Market rent", None, True, "$",
                     "Zillow Observed Rent Index, metro level."),
    "rent_growth":  ("Rent growth", None, True, "%",
                     "Year-over-year change in observed rent."),
    "tightness":    ("Demand vs supply", None, True, "pts",
                     "Migration percentile minus permitting percentile. Positive "
                     "means people are arriving faster than homes are permitted."),
    "strength":     ("Market strength", None, True, "pts",
                     "Weighted blend of migration, demand-vs-supply, wage growth, "
                     "arrivals' income and affordability headroom. Judgment "
                     "weights, not a fitted model."),
}

STRENGTH_WEIGHTS = [
    ("migration", 0.20), ("tightness", 0.25), ("pay_growth", 0.20),
    ("agi_premium", 0.15), ("affordability", 0.20),
]

_cache: dict | None = None


def _pctile_map(values: dict[str, float | None], higher_is_better: bool = True) -> dict[str, float]:
    """cbsa -> 0..100 rank. Ties share the average rank so a flat run of equal
    values does not fan out into a false gradient."""
    have = [(k, v) for k, v in values.items() if v is not None]
    if len(have) < 20:
        return {}
    have.sort(key=lambda kv: kv[1])
    n = len(have)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and have[j + 1][1] == have[i][1]:
            j += 1
        rank = 100.0 * ((i + j) / 2) / max(n - 1, 1)
        for k, _ in have[i:j + 1]:
            out[k] = rank if higher_is_better else 100.0 - rank
        i = j + 1
    return out


def _breaks(values: list[float], n: int = 6) -> list[float]:
    """Quantile cut points, so each colour band holds a similar number of
    metros. An equal-interval ramp would put almost everything in one band —
    these distributions are heavily skewed."""
    vals = sorted(v for v in values if v is not None)
    if len(vals) < n:
        return vals
    return [vals[round(i * (len(vals) - 1) / n)] for i in range(1, n)]


def _load_rents() -> dict[str, dict]:
    """ZORI is keyed by metro name, the market table by CBSA code — match on the
    lead city and state, which is how both sources spell a metro."""
    if not ZORI.exists():
        return {}
    try:
        return (json.loads(ZORI.read_text(encoding="utf-8")).get("metro") or {})
    except Exception:                                       # noqa: BLE001
        return {}


def _rent_key(name: str) -> str:
    head, _, tail = (name or "").partition(",")
    return f"{head.split('-')[0].strip()}, {tail.strip().split('-')[0]}"


def build(con: sqlite3.Connection) -> dict:
    """The whole surface: geometry + every metric + legend breaks."""
    global _cache
    if _cache is not None:
        return _cache
    if not GEO.exists():
        raise FileNotFoundError(
            "data/cbsa.geojson is missing — run scripts/fetch_cbsa_geo.py")

    con.row_factory = sqlite3.Row
    rows = {r["cbsa"]: dict(r) for r in con.execute("select * from market")}

    rents = _load_rents()
    for cb, r in rows.items():
        hit = rents.get(_rent_key(r.get("name") or ""))
        r["rent"] = (hit or {}).get("rent")
        r["rent_growth"] = (hit or {}).get("yoy")

    # Percentiles for the raw metrics, then the two composites on top of them.
    pcts: dict[str, dict[str, float]] = {}
    for key, (_, col, higher, _u, _d) in METRICS.items():
        src = col or key
        if key in ("tightness", "strength"):
            continue
        pcts[key] = _pctile_map({cb: r.get(src) for cb, r in rows.items()}, higher)

    demand, supply = pcts.get("migration", {}), pcts.get("permits", {})
    for cb, r in rows.items():
        d, s = demand.get(cb), supply.get(cb)
        r["tightness"] = round(d - s, 1) if d is not None and s is not None else None
    pcts["tightness"] = _pctile_map({cb: r["tightness"] for cb, r in rows.items()}, True)

    for cb, r in rows.items():
        parts = [(pcts[k].get(cb), w) for k, w in STRENGTH_WEIGHTS]
        # Require most of the blend to be present; a metro missing three of the
        # five inputs should be blank, not confidently mid-scale.
        got = [(v, w) for v, w in parts if v is not None]
        weight = sum(w for _, w in got)
        r["strength"] = (round(sum(v * w for v, w in got) / weight, 1)
                         if weight >= 0.6 else None)
    pcts["strength"] = _pctile_map({cb: r["strength"] for cb, r in rows.items()}, True)

    geo = json.loads(GEO.read_text(encoding="utf-8"))
    feats = []
    for f in geo["features"]:
        cb = f["properties"]["cbsa"]
        r = rows.get(cb)
        if not r:
            continue                                # geometry with no market row
        props = {"cbsa": cb, "name": r.get("name") or f["properties"].get("name"),
                 "stage": r.get("stage"), "population": r.get("population"),
                 "score": r.get("score")}
        for key, (_, col, _h, _u, _d) in METRICS.items():
            props[key] = r.get(col or key)
            p = pcts.get(key, {}).get(cb)
            props[key + "_p"] = round(p) if p is not None else None
        feats.append({"type": "Feature", "id": f["id"],
                      "geometry": f["geometry"], "properties": props})

    legend = {}
    for key, (label, col, higher, unit, desc) in METRICS.items():
        src = col or key
        vals = [r.get(src) for r in rows.values()]
        legend[key] = {
            "label": label, "unit": unit, "higher_is_better": higher,
            "description": desc,
            "breaks": [round(b, 2) if isinstance(b, float) else b
                       for b in _breaks([v for v in vals if v is not None])],
            "covered": sum(1 for v in vals if v is not None),
        }

    _cache = {
        "type": "FeatureCollection",
        "metrics": legend,
        "weights": {k: w for k, w in STRENGTH_WEIGHTS},
        "count": len(feats),
        "features": feats,
    }
    return _cache


def top(con: sqlite3.Connection, metric: str = "strength", limit: int = 15,
        min_pop: int = 0) -> list[dict]:
    """The leaders on one indicator — what the map is pointing at, as a list."""
    if metric not in METRICS:
        raise KeyError(metric)
    data = build(con)
    rows = [f["properties"] for f in data["features"]
            if f["properties"].get(metric) is not None
            and (f["properties"].get("population") or 0) >= min_pop]
    higher = METRICS[metric][2]
    rows.sort(key=lambda r: r[metric], reverse=higher)
    keep = ("cbsa", "name", "population", "stage", metric, metric + "_p",
            "strength", "tightness", "migration", "permits", "rent")
    return [{k: r.get(k) for k in keep} for r in rows[:limit]]
