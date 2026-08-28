"""Module C — aggregate counties to markets, score the growth drivers, and
classify how early each market is.

WHAT THE RESEARCH SAYS DRIVES POPULATION GROWTH, and how each factor is measured
here from free federal data:

  Employment opportunity      Moretti (2010): one tradable-sector job creates
                              ~1.6 non-tradable jobs (~2.6 total); high-tech and
                              skilled multipliers run ~2.5x, unskilled ~1.0x.
                              -> QCEW employment growth + pay level.

  Housing cost advantage      NBER (2024): falling US mobility is driven by
                              households no longer moving from high-price to
                              low-price places; the pull is being cheap RELATIVE
                              to the origin. OECD's gravity models weight wages
                              against housing cost the same way.
                              -> Zillow ZHVI against local pay, and the price gap
                                 versus the metros actually sending people.

  Supply response             A market with strong demand and permissive supply
                              absorbs growth into units instead of prices. High
                              permits-per-capita means the response has already
                              happened -> the market is no longer early.
                              -> Census Building Permits per 1,000 residents.

  Realized migration          The outcome variable, so it is measured, not
                              modelled: Census components-of-change domestic and
                              international net migration.

  Income of the movers        IRS SOI county-to-county flows carry AGI, so the
                              income of arriving households is observed rather
                              than inferred.

EARLY-STAGE IS A DIVERGENCE, NOT A LEVEL. A market is early when the drivers are
firing but supply and price have NOT yet responded. Established markets score
just as well on drivers; they have simply already been bid up.

    venv\\Scripts\\python.exe scripts\\build_markets.py
"""
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.db import connect, log_ingest  # noqa: E402

# Console output carries box-drawing characters; Windows consoles default to
# cp1252 and raise UnicodeEncodeError on them. The data is already committed by
# then, so the crash is cosmetic — but it looks like a failed run.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CFG = json.loads((ROOT / "config.json").read_text())
MC = CFG.get("market", {})
LATEST_POP_YEAR = 2024
QCEW_BASE, QCEW_LATEST = 2021, 2024
MIN_POP = MC.get("min_population", 75_000)


def curve(v, lo, hi):
    if v is None:
        return None
    return round(max(0.0, min(100.0, (v - lo) / (hi - lo) * 100)), 1)


def inv_curve(v, lo, hi):
    """100 at lo, 0 at hi — for metrics where lower is better."""
    c = curve(v, lo, hi)
    return None if c is None else round(100 - c, 1)


def mean(vals, default=None):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else default


def build(con):
    print("aggregating counties to markets ...")
    counties = {r["fips"]: r for r in con.execute("SELECT * FROM county")}
    cbsa_of = {f: c["cbsa"] for f, c in counties.items() if c["cbsa"]}

    agg = defaultdict(lambda: {
        "pop": 0, "pop_prev": 0, "dom": 0, "intl": 0, "permits": 0, "p5": 0,
        "emp": 0, "emp_prev": 0, "paysum": 0.0, "paysum_prev": 0.0,
        "counties": 0, "states": set(), "name": None, "type": None})

    for f, c in counties.items():
        if not c["cbsa"]:
            continue
        a = agg[c["cbsa"]]
        a["counties"] += 1
        a["states"].add(c["state"])
        a["name"] = c["cbsa_name"]
        a["type"] = c["cbsa_type"]

    for r in con.execute("SELECT * FROM county_pop WHERE year IN (?,?)",
                         (LATEST_POP_YEAR, LATEST_POP_YEAR - 3)):
        cb = cbsa_of.get(r["fips"])
        if not cb:
            continue
        a = agg[cb]
        if r["year"] == LATEST_POP_YEAR:
            a["pop"] += r["population"] or 0
            a["dom"] += r["domestic_mig"] or 0
            a["intl"] += r["international_mig"] or 0
        else:
            a["pop_prev"] += r["population"] or 0

    for r in con.execute("SELECT * FROM county_permits"):
        cb = cbsa_of.get(r["fips"])
        if cb:
            agg[cb]["permits"] += r["units_total"] or 0
            agg[cb]["p5"] += r["units_5plus"] or 0

    for r in con.execute("SELECT fips, year, employment, avg_annual_pay FROM county_wage "
                         "WHERE naics='TOTAL' AND year IN (?,?)", (QCEW_BASE, QCEW_LATEST)):
        cb = cbsa_of.get(r["fips"])
        if not cb:
            continue
        a = agg[cb]
        emp, pay = r["employment"] or 0, r["avg_annual_pay"] or 0
        if r["year"] == QCEW_LATEST:
            a["emp"] += emp
            a["paysum"] += emp * pay          # employment-weighted
        else:
            a["emp_prev"] += emp
            a["paysum_prev"] += emp * pay

    # ── migration flows rolled up to CBSA, excluding within-market moves ──
    flows = defaultdict(lambda: {"in_ret": 0, "in_agi": 0.0, "out_ret": 0, "out_agi": 0.0})
    origins = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    # ingest_market stores dest_fips = the market the row is ABOUT (arrivals for
    # 'in', departures for 'out') and origin_fips = the counterparty. Both
    # directions therefore roll up to the SAME market key. Attributing outbound
    # rows to the counterparty instead would measure arrivals twice and drive
    # every market's AGI premium to zero.
    for r in con.execute("SELECT * FROM migration_flow"):
        market = cbsa_of.get(r["dest_fips"])
        counter = cbsa_of.get(r["origin_fips"])
        if not market or market == counter:
            continue                      # skip intra-market moves
        f = flows[market]
        if r["direction"] == "in":
            f["in_ret"] += r["returns"] or 0
            f["in_agi"] += r["agi"] or 0
            if counter:
                e = origins[market][counter]
                e[0] += r["returns"] or 0
                e[1] += r["agi"] or 0
        else:
            f["out_ret"] += r["returns"] or 0
            f["out_agi"] += r["agi"] or 0

    prices = {}
    for r in con.execute("SELECT cbsa, month, zhvi FROM metro_price ORDER BY cbsa, month"):
        prices.setdefault(r["cbsa"], []).append((r["month"], r["zhvi"]))

    rows = []
    for cbsa, a in agg.items():
        pop = a["pop"]
        if pop < MIN_POP:
            continue
        f = flows.get(cbsa, {})
        in_ret, out_ret = f.get("in_ret", 0), f.get("out_ret", 0)
        in_agi = 1000 * f.get("in_agi", 0) / in_ret if in_ret else None
        out_agi = 1000 * f.get("out_agi", 0) / out_ret if out_ret else None
        premium = (100 * (in_agi - out_agi) / out_agi) if (in_agi and out_agi) else None

        dom, intl = a["dom"], a["intl"]
        rate = lambda v: round(1000 * v / pop, 2)  # noqa: E731

        series = prices.get(cbsa, [])
        zhvi = series[-1][1] if series else None
        zhvi_yoy = zhvi_3yr = None
        if len(series) >= 13 and series[-13][1]:
            zhvi_yoy = round(100 * (zhvi / series[-13][1] - 1), 2)
        if len(series) >= 37 and series[-37][1]:
            zhvi_3yr = round(100 * (zhvi / series[-37][1] - 1), 2)

        pay = a["paysum"] / a["emp"] if a["emp"] else None
        pay_prev = a["paysum_prev"] / a["emp_prev"] if a["emp_prev"] else None
        pay_growth = round(100 * (pay / pay_prev - 1), 2) if (pay and pay_prev) else None
        emp_growth = (round(100 * (a["emp"] / a["emp_prev"] - 1), 2)
                      if a["emp"] and a["emp_prev"] else None)
        pop_cagr = (round(100 * ((pop / a["pop_prev"]) ** (1 / 3) - 1), 2)
                    if a["pop_prev"] else None)
        p2i = round(zhvi / pay, 2) if (zhvi and pay) else None
        permits_per_1k = round(1000 * a["permits"] / pop, 2)

        top = sorted(origins.get(cbsa, {}).items(), key=lambda kv: -kv[1][0])[:8]
        top_origins = "|".join(
            f"{agg[o]['name']}:{v[0]}:{round(1000*v[1]/v[0]) if v[0] else 0}"
            for o, v in top if o in agg)

        # ── driver scores ────────────────────────────────────────────────
        # Total net migration, not domestic only — see the note in db.py.
        s_demand = curve(rate(dom + intl), -5, 25)
        s_income = mean([curve(in_agi, 45_000, 130_000), curve(premium, -10, 25)])
        # Cheap relative to pay is the pull; 3x pay is cheap, 11x is not.
        s_afford = inv_curve(p2i, 3, 11)
        s_jobs = mean([curve(emp_growth, -2, 14), curve(pay_growth, 5, 30)])

        # Headroom: supply and price have NOT responded yet. This is what makes
        # a market early rather than merely good.
        s_headroom = mean([inv_curve(permits_per_1k, 1.5, 14),
                           inv_curve(zhvi_3yr, 5, 55)])

        w = MC.get("weights", {"demand": .35, "income": .25, "afford": .20, "jobs": .20})
        parts = [(s_demand, w["demand"]), (s_income, w["income"]),
                 (s_afford, w["afford"]), (s_jobs, w["jobs"])]
        avail = [(v, wt) for v, wt in parts if v is not None]
        drivers = (round(sum(v * wt for v, wt in avail) / sum(wt for _, wt in avail), 1)
                   if avail else None)

        hr = s_headroom if s_headroom is not None else 50.0
        score = round(0.65 * (drivers if drivers is not None else 0) + 0.35 * hr, 1)

        if drivers is None:
            stage = "Unknown"
        elif drivers >= 55 and hr >= 55:
            stage = "Early"
        elif drivers >= 55 and hr >= 35:
            stage = "Emerging"
        elif drivers >= 55:
            stage = "Established"
        elif drivers >= 35:
            stage = "Steady"
        else:
            stage = "Cooling"

        rows.append((
            cbsa, a["name"], a["type"], ", ".join(sorted(a["states"]))[:120],
            a["counties"], pop, pop_cagr,
            dom, rate(dom), None, intl, rate(intl), dom + intl, rate(dom + intl),
            in_ret, round(in_agi) if in_agi else None,
            round(out_agi) if out_agi else None,
            round(premium, 2) if premium is not None else None, top_origins,
            a["permits"], permits_per_1k,
            round(100 * a["p5"] / a["permits"], 1) if a["permits"] else None,
            zhvi, zhvi_yoy, zhvi_3yr, p2i,
            a["emp"] or None, round(pay) if pay else None, pay_growth,
            s_demand, s_income, s_afford, s_jobs, s_headroom, score, stage,
        ))

    con.execute("DELETE FROM market")
    con.executemany(
        "INSERT INTO market (cbsa,name,cbsa_type,states,counties,population,pop_cagr_3yr,"
        "net_domestic_mig,net_domestic_mig_rate,net_dom_mig_3yr,net_intl_mig,"
        "net_intl_mig_rate,net_mig_total,net_mig_total_rate,inflow_returns,"
        "inflow_agi_per_return,outflow_agi_per_return,agi_premium_pct,top_origins,"
        "permits_total,permits_per_1k,permits_5plus_share,zhvi,zhvi_yoy,zhvi_3yr,"
        "price_to_income,employment,avg_annual_pay,pay_growth_3yr,score_demand,"
        "score_income,score_affordability,score_jobs,score_headroom,score,stage) "
        "VALUES (" + ",".join("?" * 36) + ")", rows)
    con.commit()
    print(f"  {len(rows):,} markets scored (population >= {MIN_POP:,})")
    return len(rows)


def main():
    started = datetime.now().isoformat(timespec="seconds")
    con = connect()
    try:
        if not con.execute("SELECT COUNT(*) FROM county").fetchone()[0]:
            raise SystemExit("county table empty — run scripts/ingest_market.py --all first")
        n = build(con)
        log_ingest(con, "build_markets", started,
                   datetime.now().isoformat(timespec="seconds"), n, "derived rebuild")

        print("\n── EARLY-stage markets, 250k+ population ───────────────────────")
        hdr = f"  {'market':<44}{'pop':>10}{'mig/1k':>8}{'inAGI':>9}{'perm/1k':>9}{'3yr px':>8}{'score':>7}"
        print(hdr)
        q = """SELECT * FROM market WHERE stage='Early' AND population>=250000
               ORDER BY score DESC LIMIT 15"""
        for r in con.execute(q):
            print(f"  {r['name'][:43]:<44}{r['population']:>10,}"
                  f"{r['net_mig_total_rate']:>8.1f}"
                  f"{(r['inflow_agi_per_return'] or 0)/1000:>8.0f}k"
                  f"{r['permits_per_1k']:>9.1f}{(r['zhvi_3yr'] or 0):>7.0f}%{r['score']:>7.1f}")

        print("\n── stage distribution ──")
        for r in con.execute("SELECT stage, COUNT(*) n FROM market GROUP BY stage "
                             "ORDER BY n DESC"):
            print(f"  {r['stage']:<14}{r['n']:>5}")

        print("\n── South Florida ───────────────────────────────────────────────")
        print(hdr)
        for r in con.execute("SELECT * FROM market WHERE cbsa IN ('33100','35840','36740','38940')"
                             " ORDER BY population DESC"):
            print(f"  {r['name'][:43]:<44}{r['population']:>10,}"
                  f"{r['net_mig_total_rate']:>8.1f}"
                  f"{(r['inflow_agi_per_return'] or 0)/1000:>8.0f}k"
                  f"{r['permits_per_1k']:>9.1f}{(r['zhvi_3yr'] or 0):>7.0f}%{r['score']:>7.1f}"
                  f"  {r['stage']}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
