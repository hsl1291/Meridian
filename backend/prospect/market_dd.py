"""Market due-diligence report — name a metro, get macro and micro trends.

    resolve("columbus ohio")  ->  candidate metros, best first
    gather("18140")           ->  the whole picture as a dict
    render(doc)               ->  print-ready HTML

Everything comes from the shared database that the market screener already
builds (Census population components, BLS QCEW wages by industry, Census
building permits, IRS county-to-county migration, Zillow ZHVI and ZORI). No
new downloads, no API keys — if `setup.py` ran, this works.

The report is deliberately split:

  macro — the metro against the other 468, over whatever years the sources
          actually cover. Population trajectory and what is driving it,
          employment and pay, home prices and rents, affordability.
  micro — the structure underneath that number. Which industries, how
          concentrated, which counties are actually growing, where the
          in-movers come from and what income they bring, what is being built.

Coverage is uneven across sources and that is stated rather than smoothed
over: permits are a single year, wages run 2021-2024, population 2020-2024.
Anything the data cannot support is listed in `gaps` and printed in the
report instead of being quietly omitted.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

from .memo import CSS, _table, money, num, pct

ROOT = Path(__file__).resolve().parents[2]

# QCEW supersectors worth naming in a mix table. Everything else rolls into
# "all other" so the table stays readable at a glance.
INDUSTRY_ORDER = [
    "31-33", "54", "52", "62", "48-49", "51", "23", "42", "44-45",
    "56", "72", "71", "61", "81", "11", "21", "22", "53", "55",
]
INDUSTRY_SHORT = {
    "11": "Agriculture", "21": "Mining & extraction", "22": "Utilities",
    "23": "Construction", "31-33": "Manufacturing", "42": "Wholesale trade",
    "44-45": "Retail trade", "48-49": "Transportation & warehousing",
    "51": "Information", "52": "Finance & insurance", "53": "Real estate",
    "54": "Professional & technical", "55": "Management of companies",
    "56": "Administrative & waste", "61": "Educational services",
    "62": "Health care & social assistance", "71": "Arts & recreation",
    "72": "Accommodation & food services", "81": "Other services",
}

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "puerto rico": "PR",
}
ABBRS = set(STATE_ABBR.values())


# ── resolve ────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace(",", " ").replace("-", " ").split())


def _split_state(q: str) -> tuple[str, str | None]:
    """Pull a trailing state out of the query. 'columbus ohio' -> ('columbus','OH')

    Two-word states are checked first so 'kansas city missouri' does not lose
    its state to 'kansas', and 'north carolina' is not read as 'north'.
    """
    n = _norm(q)
    for name, ab in sorted(STATE_ABBR.items(), key=lambda kv: -len(kv[0])):
        if n == name:
            return "", ab
        if n.endswith(" " + name):
            return n[: -len(name) - 1].strip(), ab
        if n.startswith(name + " "):
            return n[len(name) + 1:].strip(), ab
    parts = n.split()
    if len(parts) > 1 and parts[-1].upper() in ABBRS:
        return " ".join(parts[:-1]), parts[-1].upper()
    return n, None


def _score(query_city: str, state: str | None, row: dict) -> float:
    """How well does this metro answer the query? 0 = no match."""
    name = row["name"] or ""
    head, _, tail = name.partition(",")
    states = [s.strip() for s in tail.split("-") if s.strip()]
    # The metro's principal cities, e.g. "Dallas-Fort Worth-Arlington".
    cities = [_norm(c) for c in head.split("-") if c.strip()]

    if state and state not in states:
        return 0.0

    best = 0.0
    for i, city in enumerate(cities):
        if city == query_city:
            hit = 1.0
        elif city.startswith(query_city + " ") or city.endswith(" " + query_city):
            hit = 0.82
        elif query_city and query_city in city:
            hit = 0.6
        elif query_city and city in query_city:
            hit = 0.5
        else:
            continue
        # A match on the lead city counts for more than on a trailing one.
        best = max(best, hit * (1.0 if i == 0 else 0.86))

    if not best:
        return 0.0
    if state:
        best += 0.35                       # the state was named and it agreed
    # Break genuine ties toward the larger metro — someone typing "columbus"
    # with no state almost always means the 2.2 M one.
    return best + min(math.log10(max(row.get("population") or 1, 1)) / 100, 0.07)


def resolve(query: str, con: sqlite3.Connection, limit: int = 6) -> list[dict]:
    """Free text in, ranked metro candidates out.

    Returns [] when nothing matches, so callers can say so rather than
    silently reporting on the wrong city.
    """
    city, state = _split_state(query)
    if not city and not state:
        return []
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "select cbsa, name, states, population, score, stage from market")]

    scored = []
    for r in rows:
        s = _score(city, state, r)
        if s > 0:
            scored.append({**r, "match": round(s, 3)})
    scored.sort(key=lambda r: (-r["match"], -(r["population"] or 0)))

    if not scored and state:
        # Named a state but no city we know — offer that state's biggest metros.
        for r in rows:
            if state in (r["states"] or "") or f", {state}" in (r["name"] or "") \
                    or f"-{state}" in (r["name"] or ""):
                scored.append({**r, "match": 0.2})
        scored.sort(key=lambda r: -(r["population"] or 0))
    return scored[:limit]


# ── helpers ────────────────────────────────────────────────────────────────

def _pctile(value, series: list[float]) -> int | None:
    """Where this metro sits among all metros that reported the same metric."""
    if value is None:
        return None
    vals = sorted(v for v in series if v is not None)
    if len(vals) < 20:
        return None
    below = sum(1 for v in vals if v < value)
    return round(100 * below / len(vals))


def _cagr(first: float, last: float, years: int) -> float | None:
    if not first or first <= 0 or last is None or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 2)


# A high location quotient on a sector employing 0.4% of the metro is a
# statistical curiosity, not a concentration anyone should underwrite against.
# Require it to be both specialised and big enough to matter.
LQ_MIN, LQ_SHARE_MIN = 1.25, 1.0


def _concentrations(industries: list[dict]) -> list[dict]:
    return [i for i in industries
            if (i.get("lq") or 0) >= LQ_MIN and (i.get("share") or 0) >= LQ_SHARE_MIN]


def _rank_word(p: int | None) -> str:
    if p is None:
        return "not comparable"
    if p >= 90:
        return "top 10% of US metros"
    if p >= 75:
        return "top quartile"
    if p >= 60:
        return "above the median"
    if p >= 40:
        return "near the median"
    if p >= 25:
        return "below the median"
    if p >= 10:
        return "bottom quartile"
    return "bottom 10%"


# ── gather ─────────────────────────────────────────────────────────────────

def gather(cbsa: str, con: sqlite3.Connection) -> dict:
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]

    m = q("select * from market where cbsa=?", cbsa)
    if not m:
        raise LookupError(f"no metro with CBSA {cbsa}")
    m = m[0]
    gaps: list[str] = []

    counties = q("select fips, name, state from county where cbsa=? order by name", cbsa)
    fips = [c["fips"] for c in counties]
    ph = ",".join("?" * len(fips)) or "''"

    # ── population trajectory and its components ──────────────────────────
    pop_years = q(
        f"""select year, sum(population) pop, sum(births) births, sum(deaths) deaths,
                   sum(domestic_mig) dom, sum(international_mig) intl,
                   sum(natural_change) nat
              from county_pop where fips in ({ph})
             group by year order by year""", *fips)
    pop_first = pop_years[0] if pop_years else {}
    pop_last = pop_years[-1] if pop_years else {}
    span = (pop_last.get("year", 0) - pop_first.get("year", 0)) or 0
    pop_cagr = _cagr(pop_first.get("pop"), pop_last.get("pop"), span)

    # Cumulative components tell you *why* it grew, which is the part that
    # actually predicts anything.
    comp = {k: sum(r.get(k) or 0 for r in pop_years[1:]) for k in ("nat", "dom", "intl")}
    comp_total = sum(comp.values())
    growth_abs = (pop_last.get("pop") or 0) - (pop_first.get("pop") or 0)

    # ── employment and pay ────────────────────────────────────────────────
    emp_years = q(
        f"""select year, sum(employment) emp,
                   sum(employment * avg_annual_pay) / nullif(sum(employment),0) pay
              from county_wage where fips in ({ph}) and naics='TOTAL'
             group by year order by year""", *fips)
    emp_first, emp_last = (emp_years[0], emp_years[-1]) if emp_years else ({}, {})
    emp_span = (emp_last.get("year", 0) - emp_first.get("year", 0)) or 0
    emp_cagr = _cagr(emp_first.get("emp"), emp_last.get("emp"), emp_span)
    pay_cagr = _cagr(emp_first.get("pay"), emp_last.get("pay"), emp_span)

    # ── industry mix, with location quotient against the nation ───────────
    latest_wage_year = emp_last.get("year")
    industries = []
    if latest_wage_year:
        local = q(
            f"""select naics, industry, sum(employment) emp,
                       sum(employment*avg_annual_pay)/nullif(sum(employment),0) pay
                  from county_wage
                 where fips in ({ph}) and year=? and naics<>'TOTAL'
                 group by naics""", *fips, latest_wage_year)
        nat = {r["naics"]: r["emp"] for r in q(
            "select naics, sum(employment) emp from county_wage "
            "where year=? and naics<>'TOTAL' group by naics", latest_wage_year)}
        nat_total = sum(nat.values()) or 1
        loc_total = sum(r["emp"] or 0 for r in local) or 1
        prior = {r["naics"]: r["emp"] for r in q(
            f"""select naics, sum(employment) emp from county_wage
                 where fips in ({ph}) and year=? and naics<>'TOTAL'
                 group by naics""", *fips, emp_first.get("year") or latest_wage_year)}
        for r in local:
            e = r["emp"] or 0
            share = 100 * e / loc_total
            nat_share = 100 * (nat.get(r["naics"]) or 0) / nat_total
            was = prior.get(r["naics"])
            industries.append({
                "naics": r["naics"],
                "industry": INDUSTRY_SHORT.get(r["naics"])
                            or (r["industry"] or "").replace("NAICS ", ""),
                "emp": e,
                "share": round(share, 1),
                "lq": round(share / nat_share, 2) if nat_share else None,
                "pay": round(r["pay"]) if r["pay"] else None,
                "growth": _cagr(was, e, emp_span) if was and emp_span else None,
            })
        industries.sort(key=lambda r: -(r["emp"] or 0))
    else:
        gaps.append("No QCEW wage data for these counties — industry mix omitted.")

    # ── county detail inside the metro ────────────────────────────────────
    county_rows = []
    for c in counties:
        cp = q("select year, population from county_pop where fips=? order by year", c["fips"])
        if not cp:
            continue
        f, l = cp[0], cp[-1]
        yrs = (l["year"] - f["year"]) or 0
        pk = q("select units_total, units_5plus from county_permits where fips=? "
               "order by year desc limit 1", c["fips"])
        county_rows.append({
            "name": c["name"], "state": c["state"],
            "pop": l["population"],
            "share": round(100 * (l["population"] or 0) / (pop_last.get("pop") or 1), 1),
            "cagr": _cagr(f["population"], l["population"], yrs),
            "added": (l["population"] or 0) - (f["population"] or 0),
            "permits": pk[0]["units_total"] if pk else None,
            "mf_share": (round(100 * (pk[0]["units_5plus"] or 0) / pk[0]["units_total"], 0)
                         if pk and pk[0]["units_total"] else None),
        })
    county_rows.sort(key=lambda r: -(r["pop"] or 0))

    # ── where in-movers come from, and what they earn ─────────────────────
    origins = []
    for chunk in (m.get("top_origins") or "").split("|"):
        bits = chunk.split(":")
        if len(bits) >= 3 and bits[1].isdigit():
            origins.append({"origin": bits[0], "returns": int(bits[1]),
                            "agi": float(bits[2]) if bits[2] else None})
    local_agi = m.get("inflow_agi_per_return")
    for o in origins:
        o["vs_local"] = (round(100 * (o["agi"] - local_agi) / local_agi, 1)
                         if o["agi"] and local_agi else None)

    # ── prices and rents ──────────────────────────────────────────────────
    price = q("select month, zhvi from metro_price where cbsa=? order by month", cbsa)
    peak = max((p["zhvi"] for p in price if p["zhvi"]), default=None)
    now = price[-1]["zhvi"] if price else None
    from_peak = round(100 * (now - peak) / peak, 1) if peak and now else None

    rent = None
    zori_path = ROOT / "data" / "zori_rents.json"
    if zori_path.exists():
        try:
            z = json.loads(zori_path.read_text(encoding="utf-8"))
            metros = z.get("metro") or {}
            head = (m["name"] or "").partition(",")[0].split("-")[0].strip()
            st = (m["name"] or "").rpartition(",")[2].strip().split("-")[0]
            rent = metros.get(f"{head}, {st}")
            us = metros.get("United States") or {}
            if rent and us.get("rent"):
                rent = {**rent, "vs_us": round(100 * (rent["rent"] - us["rent"]) / us["rent"], 1),
                        "us_rent": us["rent"], "us_yoy": us.get("yoy")}
        except Exception:
            rent = None
    if not rent:
        gaps.append("No Zillow rent index for this metro — rent lines omitted. "
                    "ZORI covers roughly the largest 900 metros and ZIPs.")

    # rent-to-income, the one affordability measure that matters for rentals
    rent_burden = None
    if rent and emp_last.get("pay"):
        rent_burden = round(100 * (rent["rent"] * 12) / emp_last["pay"], 1)

    # ── peer position across all 469 metros ───────────────────────────────
    allm = q("select population, pop_cagr_3yr, net_mig_total_rate, permits_per_1k, "
             "zhvi, zhvi_yoy, price_to_income, avg_annual_pay, pay_growth_3yr, score "
             "from market")
    col = lambda k: [r[k] for r in allm]
    ranks = {
        "population":      _pctile(m.get("population"), col("population")),
        "pop_growth":      _pctile(m.get("pop_cagr_3yr"), col("pop_cagr_3yr")),
        "net_migration":   _pctile(m.get("net_mig_total_rate"), col("net_mig_total_rate")),
        "supply":          _pctile(m.get("permits_per_1k"), col("permits_per_1k")),
        "home_price":      _pctile(m.get("zhvi"), col("zhvi")),
        "price_growth":    _pctile(m.get("zhvi_yoy"), col("zhvi_yoy")),
        "pay":             _pctile(m.get("avg_annual_pay"), col("avg_annual_pay")),
        "pay_growth":      _pctile(m.get("pay_growth_3yr"), col("pay_growth_3yr")),
        "score":           _pctile(m.get("score"), col("score")),
    }
    # price_to_income: lower is better, so invert for a "more affordable" reading
    pti = _pctile(m.get("price_to_income"), col("price_to_income"))
    ranks["affordability"] = (100 - pti) if pti is not None else None

    # ── coverage notes, stated rather than smoothed over ──────────────────
    permit_years = q(f"select min(year) a, max(year) b from county_permits "
                     f"where fips in ({ph})", *fips)
    py = permit_years[0] if permit_years else {}
    if py.get("a") and py["a"] == py.get("b"):
        gaps.append(f"Building permits cover {py['a']} only — the supply figures are a "
                    "single-year snapshot, not a trend.")
    if pop_years and span < 5:
        gaps.append(f"Population components span {pop_first['year']}–{pop_last['year']} "
                    f"({span} year{'s' if span != 1 else ''}) — a short base for a CAGR.")
    if len(emp_years) < 3 and emp_years:
        yrs = ", ".join(str(r["year"]) for r in emp_years)
        gaps.append(f"QCEW wage data is loaded for {yrs} only — not an annual series. "
                    "Job and pay growth are endpoint-to-endpoint, so they cannot show "
                    "what happened in between.")
    if not m.get("net_dom_mig_3yr"):
        gaps.append("Three-year domestic migration is not populated for this metro; "
                    "the single-year rate is used instead.")
    if not price:
        gaps.append("No Zillow home-value series for this metro — the price section is "
                    "omitted entirely, and affordability rests on the screener's "
                    "price-to-income figure alone. ZHVI thins out below roughly the "
                    "largest 900 metros.")
    elif len(price) < 36:
        gaps.append(f"Home-price history is {len(price)} months — shorter than the "
                    "three-year window the growth figures assume.")

    return {
        "generated": date.today().isoformat(),
        "market": m,
        "counties": counties,
        "county_rows": county_rows,
        "pop_years": pop_years,
        "pop_cagr": pop_cagr,
        "pop_growth_abs": growth_abs,
        "components": comp,
        "components_total": comp_total,
        "emp_years": emp_years,
        "emp_cagr": emp_cagr,
        "pay_cagr": pay_cagr,
        "industries": industries,
        "wage_year": latest_wage_year,
        "origins": origins,
        "price": price,
        "price_peak": peak,
        "price_now": now,
        "price_from_peak": from_peak,
        "rent": rent,
        "rent_burden": rent_burden,
        "ranks": ranks,
        "gaps": gaps,
    }


# ── narrative ──────────────────────────────────────────────────────────────

def _headline(d: dict) -> list[str]:
    """Three or four sentences a person would actually say out loud."""
    m, out = d["market"], []
    name = (m["name"] or "").partition(",")[0]
    comp, tot = d["components"], d["components_total"]

    if d["pop_cagr"] is not None and d["pop_years"]:
        yrs = f"{d['pop_years'][0]['year']}–{d['pop_years'][-1]['year']}"
        # Two different measures, kept apart on purpose: the rate below is
        # computed over the years actually loaded, the percentile is the
        # screener's own three-year CAGR, which is what the 469 are ranked on.
        out.append(
            f"{name} grew {d['pop_growth_abs']:+,} people over {yrs}, a "
            f"{d['pop_cagr']:+.2f}% annual rate. On the screener's three-year "
            f"measure ({pct(m.get('pop_cagr_3yr'), 2)}) that is "
            f"{_rank_word(d['ranks']['pop_growth'])}.")

    if tot:
        parts = sorted((("natural increase", comp["nat"]),
                        ("domestic migration", comp["dom"]),
                        ("international migration", comp["intl"])),
                       key=lambda kv: -abs(kv[1]))
        lead, lead_v = parts[0]
        neg = [k for k, v in parts if v < 0]
        s = (f"Growth is carried by {lead} ({lead_v:+,}), "
             f"with {parts[1][0]} at {parts[1][1]:+,} and {parts[2][0]} at {parts[2][1]:+,}.")
        if neg and lead_v > 0:
            s += (f" {neg[0].capitalize()} is negative, so the headline number depends on "
                  f"{lead} continuing.")
        out.append(s)

    if d["industries"]:
        conc = _concentrations(d["industries"])[:8]
        if conc:
            out.append(
                "The employment base is concentrated in "
                + ", ".join(f"{i['industry'].lower()} ({i['lq']}× the national share)"
                            for i in conc[:3])
                + ". Concentration cuts both ways — it is the reason for the growth and "
                  "the exposure if that sector turns.")
        else:
            out.append("No sector runs meaningfully above its national share — the "
                       "employment base is broad, which limits both upside surprise "
                       "and single-sector risk.")

    supply, dem = d["ranks"]["supply"], d["ranks"]["net_migration"]
    if supply is not None and dem is not None:
        if dem >= 60 and supply <= 40:
            out.append("Demand is running ahead of permitted supply — the setup that "
                       "supports rent growth.")
        elif supply >= 75 and dem <= 50:
            out.append(f"Permitting sits in the {_rank_word(supply)} while migration does "
                       "not — supply is being added faster than people are arriving.")
        elif supply >= 60 and dem >= 60:
            out.append("Both demand and permitting are elevated; the question is which "
                       "one turns first.")
    return out


# ── render ─────────────────────────────────────────────────────────────────

EXTRA_CSS = """
.spark{display:block;width:100%;height:54px;margin:10px 0 2px}
.bars{display:flex;align-items:flex-end;gap:3px;height:56px;margin:10px 0 2px}
.bars i{flex:1;background:#1f3b57;opacity:.78;min-height:1px}
.bars i.neg{background:#c8791d}
.axis{display:flex;justify-content:space-between;font-size:7.4pt;color:#8a99a9;
  font-family:"Helvetica Neue",Arial,sans-serif}
.rank{display:flex;align-items:center;gap:9px;margin:5px 0}
.rank .lab{width:1.75in;font-size:8.6pt;color:#4a5b6e}
.rank .track{flex:1;height:7px;background:#eef2f6;position:relative}
.rank .fill{position:absolute;top:0;left:0;height:7px;background:#1f3b57}
.rank .val{width:.62in;text-align:right;font-size:8.4pt;font-variant-numeric:tabular-nums;
  color:#16212e}
.lede{font-size:11pt;line-height:1.62;color:#26374a}
.lede p{margin:0 0 11px}
.split{display:flex;gap:18px}
.split>div{flex:1}
"""


def _bars(rows, key, labels=None):
    vals = [r.get(key) or 0 for r in rows]
    if not vals:
        return ""
    hi = max(abs(v) for v in vals) or 1
    bars = "".join(
        f'<i class="{"neg" if v < 0 else ""}" style="height:{max(abs(v)/hi*100,1.5):.0f}%"'
        f' title="{v:,.0f}"></i>' for v in vals)
    lab = labels or [str(r.get("year", "")) for r in rows]
    return (f'<div class="bars">{bars}</div>'
            f'<div class="axis"><span>{lab[0]}</span><span>{lab[-1]}</span></div>')


def _sparkline(points, w=520, h=54):
    vals = [p for p in points if p is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i*step:.1f},{h - (v-lo)/rng*(h-6) - 3:.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="#1f3b57" stroke-width="2"/>'
            f'</svg>')


def _ranks_block(ranks: dict) -> str:
    order = [("Population", "population"), ("Population growth", "pop_growth"),
             ("Net migration", "net_migration"), ("Wage level", "pay"),
             ("Wage growth", "pay_growth"), ("Home price", "home_price"),
             ("Price growth", "price_growth"), ("Affordability", "affordability"),
             ("Permitting", "supply"), ("Composite score", "score")]
    out = []
    for label, key in order:
        p = ranks.get(key)
        if p is None:
            continue
        out.append(f'<div class="rank"><span class="lab">{escape(label)}</span>'
                   f'<span class="track"><i class="fill" style="width:{p}%"></i></span>'
                   f'<span class="val">{p}</span></div>')
    return "".join(out)


def render(doc: dict) -> str:
    m = doc["market"]
    name = m["name"] or m["cbsa"]
    p: list[str] = []

    # ── cover ─────────────────────────────────────────────────────────────
    stage = m.get("stage") or "—"
    p.append(
        '<div class="cover">'
        '<div><div class="eyebrow">Market due diligence</div>'
        f'<h1>{escape(name)}</h1>'
        f'<p class="addr">{escape(m.get("cbsa_type") or "")} · CBSA {escape(m["cbsa"])} · '
        f'{m.get("counties") or 0} counties · {escape(m.get("states") or "")}</p>'
        '<div class="rule"></div></div>'
        '<div>'
        '<div class="statband">'
        f'<div><b>{num(m.get("population"))}</b><span>Population</span></div>'
        f'<div><b>{pct(m.get("pop_cagr_3yr"),2)}</b><span>Pop CAGR 3yr</span></div>'
        f'<div><b>{money(m.get("zhvi"))}</b><span>Median home</span></div>'
        f'<div><b>{money(m.get("avg_annual_pay"))}</b><span>Avg pay</span></div>'
        f'<div><b>{escape(stage)}</b><span>Cycle stage</span></div>'
        '</div>'
        f'<p class="src" style="margin-top:14px">Generated {doc["generated"]} · '
        'Census · BLS QCEW · IRS SOI · Zillow · public sources only</p>'
        '</div></div>')

    p.append('<div class="pad">')

    # ── the read ──────────────────────────────────────────────────────────
    p.append('<section><h2>The read</h2><div class="lede">')
    for s in _headline(doc):
        p.append(f"<p>{escape(s)}</p>")
    p.append("</div></section>")

    # ── position vs other metros ──────────────────────────────────────────
    p.append('<section><h2>Where it sits</h2>'
             '<p class="sub">Percentile against all 469 US metros. 50 = median.</p>'
             + _ranks_block(doc["ranks"]) + "</section>")

    # ══ MACRO ═════════════════════════════════════════════════════════════
    p.append('<h2 style="border-top:2px solid #1f3b57;padding-top:14px">Macro</h2>')

    # population
    py = doc["pop_years"]
    if py:
        comp, tot = doc["components"], doc["components_total"]
        p.append('<section><h3>Population and what is driving it</h3>')
        p.append(_sparkline([r["pop"] for r in py]))
        p.append(f'<div class="axis"><span>{py[0]["year"]}: {num(py[0]["pop"])}</span>'
                 f'<span>{py[-1]["year"]}: {num(py[-1]["pop"])}</span></div>')
        p.append('<div class="kv">'
                 f'<div><b>{doc["pop_growth_abs"]:+,}</b><span>People added</span></div>'
                 f'<div><b>{pct(doc["pop_cagr"],2)}</b><span>Annual rate</span></div>'
                 f'<div><b>{comp["nat"]:+,}</b><span>Natural increase</span></div>'
                 f'<div><b>{comp["dom"]:+,}</b><span>Domestic migration</span></div>'
                 f'<div><b>{comp["intl"]:+,}</b><span>Intl migration</span></div>'
                 '</div>')
        if tot:
            p.append('<p>Share of the change: '
                     + ", ".join(
                         f"{lab} {100*comp[k]/tot:+.0f}%"
                         for lab, k in (("natural", "nat"), ("domestic", "dom"),
                                        ("international", "intl")))
                     + ". A metro leaning on international migration carries different "
                       "policy risk than one leaning on domestic in-migration.</p>")
        p.append(_table(py, [
            ("Year", "year", False, lambda v: str(v)),
            ("Population", "pop", True, num),
            ("Births", "births", True, num),
            ("Deaths", "deaths", True, num),
            ("Domestic", "dom", True, lambda v: f"{v:+,}" if v is not None else "—"),
            ("International", "intl", True, lambda v: f"{v:+,}" if v is not None else "—"),
        ]))
        p.append('<p class="src">Census Population Estimates, county components of change. '
                 f'The {py[0]["year"]} row is a partial period — the vintage counts from the '
                 'April 1 decennial base, so its births, deaths and migration cover about '
                 'three months and are not comparable to the full years below it. The '
                 'cumulative figures above exclude it.</p>')
        p.append("</section>")

    # employment
    ey = doc["emp_years"]
    if ey:
        p.append('<section><h3>Employment and pay</h3>')
        # Two loaded years is two points. Drawing a line through them would
        # look like a trend nobody measured, so show the endpoints as a table.
        if len(ey) >= 4:
            p.append('<div class="split"><div>'
                     + _sparkline([r["emp"] for r in ey]) +
                     f'<div class="axis"><span>Jobs {ey[0]["year"]}</span>'
                     f'<span>{num(ey[-1]["emp"])}</span></div></div><div>'
                     + _sparkline([r["pay"] for r in ey]) +
                     f'<div class="axis"><span>Avg pay {ey[0]["year"]}</span>'
                     f'<span>{money(ey[-1]["pay"])}</span></div></div></div>')
        else:
            p.append(_table(ey, [
                ("Year", "year", False, lambda v: str(v)),
                ("Covered jobs", "emp", True, num),
                ("Average annual pay", "pay", True, money),
            ]))
            p.append('<p class="sub" style="margin:2px 0 0">Endpoints only — see the '
                     'coverage note at the end.</p>')
        p.append('<div class="kv">'
                 f'<div><b>{num(ey[-1]["emp"])}</b><span>Covered jobs</span></div>'
                 f'<div><b>{pct(doc["emp_cagr"],2)}</b><span>Job growth /yr</span></div>'
                 f'<div><b>{money(ey[-1]["pay"])}</b><span>Average pay</span></div>'
                 f'<div><b>{pct(doc["pay_cagr"],2)}</b><span>Pay growth /yr</span></div>'
                 '</div>')
        p.append('<p class="src">BLS Quarterly Census of Employment and Wages, '
                 'all-industry totals summed across the metro\'s counties.</p>')
        p.append("</section>")

    # prices and rents
    price = doc["price"]
    if price:
        p.append('<section><h3>Home prices and rents</h3>')
        p.append(_sparkline([r["zhvi"] for r in price]))
        p.append(f'<div class="axis"><span>{price[0]["month"]}</span>'
                 f'<span>{price[-1]["month"]}</span></div>')
        cells = [f'<div><b>{money(doc["price_now"])}</b><span>Typical home</span></div>',
                 f'<div><b>{pct(m.get("zhvi_yoy"),1)}</b><span>YoY</span></div>',
                 f'<div><b>{pct(m.get("zhvi_3yr"),1)}</b><span>3-year</span></div>',
                 f'<div><b>{pct(doc["price_from_peak"],1)}</b><span>From peak</span></div>',
                 f'<div><b>{m.get("price_to_income") or "—"}×</b><span>Price to income</span></div>']
        r = doc["rent"]
        if r:
            cells += [f'<div><b>{money(r["rent"])}</b><span>Typical rent</span></div>',
                      f'<div><b>{pct(r.get("yoy"),1)}</b><span>Rent YoY</span></div>']
            if r.get("vs_us") is not None:
                cells.append(f'<div><b>{r["vs_us"]:+.0f}%</b><span>vs US rent</span></div>')
        if doc["rent_burden"] is not None:
            cells.append(f'<div><b>{doc["rent_burden"]:.0f}%</b>'
                         '<span>Rent to avg pay</span></div>')
        p.append('<div class="kv">' + "".join(cells) + "</div>")
        if doc["rent_burden"] is not None:
            verdict = ("comfortably inside the 30% affordability line"
                       if doc["rent_burden"] < 30 else
                       "above the 30% line commonly treated as cost-burdened")
            p.append(f"<p>Annualised typical rent is {doc['rent_burden']:.0f}% of the "
                     f"average covered wage — {verdict}. This compares a metro-wide rent "
                     "to a metro-wide wage, so treat it as a directional screen rather "
                     "than a household-level statistic.</p>")
        p.append('<p class="src">Zillow ZHVI (home values, monthly) and ZORI '
                 '(observed rents). Price-to-income from the screener build.</p>')
        p.append("</section>")

    # ══ MICRO ═════════════════════════════════════════════════════════════
    p.append('<h2 style="border-top:2px solid #1f3b57;padding-top:14px">Micro</h2>')

    ind = doc["industries"]
    if ind:
        p.append(f'<section><h3>Industry mix ({doc["wage_year"]})</h3>'
                 '<p class="sub">LQ = share of local jobs ÷ share of national jobs. '
                 'Above 1.25 means the metro is specialised in it.</p>')
        p.append(_table(ind[:14], [
            ("Industry", "industry", False, None),
            ("Jobs", "emp", True, num),
            ("Share", "share", True, lambda v: f"{v:.1f}%" if v is not None else "—"),
            ("LQ", "lq", True, lambda v: f"{v:.2f}" if v is not None else "—"),
            ("Avg pay", "pay", True, money),
            ("Growth /yr", "growth", True, lambda v: f"{v:+.1f}%" if v is not None else "—"),
        ]))
        top = _concentrations(ind)[:5]
        if top:
            p.append('<div class="callout"><b>Concentrated in:</b> '
                     + "; ".join(f"{escape(i['industry'])} — {i['lq']}× national, "
                                 f"{num(i['emp'])} jobs at {money(i['pay'])}"
                                 for i in top) + "</div>")
        p.append('<p class="src">BLS QCEW by NAICS supersector. National shares computed '
                 'from the same release, so the ratio is internally consistent.</p>')
        p.append("</section>")

    cr = doc["county_rows"]
    if cr:
        p.append('<section><h3>Counties inside the metro</h3>'
                 '<p class="sub">Metro averages hide the fact that growth is usually '
                 'one or two counties.</p>')
        p.append(_table(cr, [
            ("County", "name", False, None),
            ("State", "state", False, None),
            ("Population", "pop", True, num),
            ("% of metro", "share", True, lambda v: f"{v:.1f}%" if v is not None else "—"),
            ("Growth /yr", "cagr", True, lambda v: f"{v:+.2f}%" if v is not None else "—"),
            ("Added", "added", True, lambda v: f"{v:+,}" if v is not None else "—"),
            ("Permits", "permits", True, num),
            ("MF %", "mf_share", True, lambda v: f"{v:.0f}%" if v is not None else "—"),
        ]))
        p.append('<p class="src">Census population estimates and building permits survey.</p>')
        p.append("</section>")

    orig = doc["origins"]
    if orig:
        p.append('<section><h3>Where the in-movers come from</h3>'
                 '<p class="sub">IRS return-to-return migration. AGI is per return, '
                 'so it reads as the income the flow carries.</p>')
        p.append(_table(orig[:10], [
            ("Origin metro", "origin", False, None),
            ("Returns", "returns", True, num),
            ("AGI / return", "agi", True, money),
            ("vs local inflow", "vs_local", True,
             lambda v: f"{v:+.0f}%" if v is not None else "—"),
        ]))
        prem = m.get("agi_premium_pct")
        if prem is not None:
            direction = ("higher-earning than the households leaving"
                         if prem > 0 else "lower-earning than the households leaving")
            p.append(f"<p>Households moving in report AGI {pct(prem,1)} against those "
                     f"moving out — arrivals are {direction}. Sustained negative premium "
                     "erodes the local income base even when the headcount grows.</p>")
        p.append('<p class="src">IRS Statistics of Income county-to-county migration.</p>')
        p.append("</section>")

    # supply
    p.append('<section><h3>Supply</h3>')
    p.append('<div class="kv">'
             f'<div><b>{num(m.get("permits_total"))}</b><span>Units permitted</span></div>'
             f'<div><b>{m.get("permits_per_1k") or "—"}</b><span>Per 1,000 people</span></div>'
             f'<div><b>{pct(m.get("permits_5plus_share"),0)}</b><span>Multifamily share</span></div>'
             '</div>')
    if m.get("permits_per_1k") is not None:
        p.append(f"<p>Permitting sits in the {_rank_word(doc['ranks']['supply'])} nationally. "
                 f"Multifamily is {pct(m.get('permits_5plus_share'),0)} of permitted units — "
                 "the share that competes directly with rental stock.</p>")
    p.append('<p class="src">Census Building Permits Survey, county totals.</p>')
    p.append("</section>")

    # score decomposition
    p.append('<section><h3>Screener score</h3>'
             '<p class="sub">The composite the market screener ranks on, broken into '
             'its parts. Each is 0–100 within the 469-metro set.</p>')
    sc = lambda k: (f"{m[k]:.1f}" if isinstance(m.get(k), (int, float)) else "—")
    p.append('<div class="kv">'
             f'<div><b>{sc("score_demand")}</b><span>Demand</span></div>'
             f'<div><b>{sc("score_income")}</b><span>Income</span></div>'
             f'<div><b>{sc("score_affordability")}</b><span>Affordability</span></div>'
             f'<div><b>{sc("score_jobs")}</b><span>Jobs</span></div>'
             f'<div><b>{sc("score_headroom")}</b><span>Headroom</span></div>'
             f'<div><b>{sc("score")}</b><span>Composite</span></div>'
             '</div>')
    p.append(f'<p>Cycle stage: <b>{escape(stage)}</b>. The stage label is derived from price '
             'and migration momentum together, so it moves before the price series alone does.</p>')
    p.append("</section>")

    # gaps
    if doc["gaps"]:
        p.append('<div class="gap"><b>What this report could not confirm</b><ul>'
                 + "".join(f"<li>{escape(g)}</li>" for g in doc["gaps"])
                 + "</ul></div>")

    p.append(
        '<div class="foot">Informational only. Compiled from public federal and '
        'commercial-research sources (US Census Bureau population estimates and building '
        'permits, BLS Quarterly Census of Employment and Wages, IRS Statistics of Income '
        'migration, Zillow Research ZHVI and ZORI). Figures carry the vintage and revision '
        'schedule of those sources and are not independently verified. Not investment, '
        'valuation, legal, or tax advice. Confirm every figure that would change a decision '
        'before relying on it.</div>')
    p.append("</div>")

    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{escape(name)} — market due diligence</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>{CSS}{EXTRA_CSS}</style></head><body>"
            "<button class='noprint' onclick='window.print()'>Print / Save PDF</button>"
            f"<div class='sheet'>{''.join(p)}</div></body></html>")


def generate(cbsa: str, con: sqlite3.Connection) -> str:
    return render(gather(cbsa, con))
