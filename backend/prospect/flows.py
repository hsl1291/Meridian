"""Where the population is going.

`migration_flow` holds 109,044 IRS SOI county-to-county rows with income attached
to every flow -- the only free source that says what movers earn -- and it was
read in exactly one place, to build a summary string. The `out` direction was
ingested and never surfaced anywhere.

The column names are a trap, and it is worth stating plainly because reading them
literally produces numbers that look plausible and are wrong:

    dest_fips    the county the row is ABOUT   (arrivals for 'in', departures for 'out')
    origin_fips  the counterparty

So `origin_fips` is the *destination* on an outbound row. Both directions roll up
to the same subject key, which is why `ix_flow_dest (dest_fips, year)` is the
right index for every query here and no second one is needed.

Three limits travel with every response rather than sitting in a footnote,
because each changes how a number should be read:

  * SOI lags about two years. This is not a current-conditions indicator.
  * Flows under ten returns are suppressed, so small corridors are missing
    entirely and the visible ones do not sum to the county total.
  * It counts tax filers, not people. Non-filers -- low income, many elderly --
    are invisible, which biases exactly the populations a relocation plan has to
    deal with.
"""
from __future__ import annotations

import sqlite3

# IRS SOI reports AGI in thousands of dollars.
AGI_UNIT = 1_000

LIMITS = [
    "IRS SOI migration lags roughly two years; this is not a current-conditions "
    "indicator.",
    "Flows below ten returns are suppressed at source, so small corridors are "
    "absent and the visible flows do not sum to the county total.",
    "It counts tax filers, not people. Non-filers — low income, many elderly — "
    "are invisible.",
]


def _agi_per_return(agi: float | None, returns: int | None) -> float | None:
    """Average AGI of a moving household, in dollars."""
    if not returns or agi is None:
        return None
    return round(agi * AGI_UNIT / returns)


def _cbsa_of(con: sqlite3.Connection) -> dict[str, str]:
    return {r["fips"]: r["cbsa"]
            for r in con.execute("SELECT fips, cbsa FROM county WHERE cbsa IS NOT NULL")}


def _names(con: sqlite3.Connection) -> dict[str, str]:
    return {r["cbsa"]: r["name"] for r in con.execute("SELECT cbsa, name FROM market")}


def national(con: sqlite3.Connection, min_pop: int = 0, limit: int = 100) -> dict:
    """Every metro's net gain in households and in income.

    Reported as two rankings rather than one composite, because the gap between
    them is the finding: a metro gaining households while losing AGI is gaining
    poor and losing rich, and no single number shows that.
    """
    cbsa_of, names = _cbsa_of(con), _names(con)
    agg: dict[str, dict] = {}

    for r in con.execute(
            "SELECT dest_fips, origin_fips, direction, returns, agi FROM migration_flow"):
        market = cbsa_of.get(r["dest_fips"])
        counter = cbsa_of.get(r["origin_fips"])
        if not market or market == counter:
            continue                       # intra-metro moves are not migration
        a = agg.setdefault(market, {"in_returns": 0, "out_returns": 0,
                                    "in_agi": 0.0, "out_agi": 0.0})
        side = "in" if r["direction"] == "in" else "out"
        a[f"{side}_returns"] += r["returns"] or 0
        a[f"{side}_agi"] += r["agi"] or 0

    pops = {r["cbsa"]: r["population"] for r in con.execute(
        "SELECT cbsa, population FROM market")}

    rows = []
    for cbsa, a in agg.items():
        pop = pops.get(cbsa) or 0
        if pop < min_pop:
            continue
        net_returns = a["in_returns"] - a["out_returns"]
        net_agi = (a["in_agi"] - a["out_agi"]) * AGI_UNIT
        rows.append({
            "cbsa": cbsa, "name": names.get(cbsa, cbsa), "population": pop,
            "in_returns": a["in_returns"], "out_returns": a["out_returns"],
            "net_returns": net_returns,
            "net_returns_per_1k": round(1000 * net_returns / pop, 2) if pop else None,
            "in_agi_per_return": _agi_per_return(a["in_agi"], a["in_returns"]),
            "out_agi_per_return": _agi_per_return(a["out_agi"], a["out_returns"]),
            "net_agi": round(net_agi),
            # The two can disagree, and that disagreement is the point.
            "gaining_households_losing_income": net_returns > 0 and net_agi < 0,
            "losing_households_gaining_income": net_returns < 0 and net_agi > 0,
        })

    rows.sort(key=lambda r: r["net_returns"], reverse=True)
    return {
        "metros": len(rows),
        "by_households": rows[:limit],
        "by_income": sorted(rows, key=lambda r: r["net_agi"], reverse=True)[:limit],
        "divergent": [r for r in rows if r["gaining_households_losing_income"]
                      or r["losing_households_gaining_income"]][:limit],
        "limits": LIMITS,
    }


def metro(con: sqlite3.Connection, cbsa: str, limit: int = 15) -> dict:
    """One metro's corridors: who feeds it, who drains it, and what each carries.

    Netted per counterparty, so a corridor that runs both ways -- most do --
    reports the balance rather than two numbers the reader has to subtract.
    """
    if not con.execute("SELECT 1 FROM market WHERE cbsa=?", (cbsa,)).fetchone():
        raise LookupError(f"no metro {cbsa}")
    cbsa_of, names = _cbsa_of(con), _names(con)
    fips = [f for f, c in cbsa_of.items() if c == cbsa]
    if not fips:
        raise LookupError(f"no counties for metro {cbsa}")

    corridors: dict[str, dict] = {}
    totals = {"in_returns": 0, "out_returns": 0, "in_agi": 0.0, "out_agi": 0.0}
    ph = ",".join("?" * len(fips))
    for r in con.execute(
            f"SELECT origin_fips, direction, returns, agi FROM migration_flow "
            f"WHERE dest_fips IN ({ph})", fips):
        counter = cbsa_of.get(r["origin_fips"])
        if counter == cbsa:
            continue
        side = "in" if r["direction"] == "in" else "out"
        totals[f"{side}_returns"] += r["returns"] or 0
        totals[f"{side}_agi"] += r["agi"] or 0
        # Counterparties outside any metro are real moves and must not be
        # dropped from the totals -- they are pooled rather than named.
        key = counter or "__nonmetro__"
        c = corridors.setdefault(key, {"in_returns": 0, "out_returns": 0,
                                       "in_agi": 0.0, "out_agi": 0.0})
        c[f"{side}_returns"] += r["returns"] or 0
        c[f"{side}_agi"] += r["agi"] or 0

    rows = []
    for key, c in corridors.items():
        net = c["in_returns"] - c["out_returns"]
        rows.append({
            "cbsa": None if key == "__nonmetro__" else key,
            "name": "Outside any metro" if key == "__nonmetro__" else names.get(key, key),
            "in_returns": c["in_returns"], "out_returns": c["out_returns"],
            "net_returns": net,
            "in_agi_per_return": _agi_per_return(c["in_agi"], c["in_returns"]),
            "out_agi_per_return": _agi_per_return(c["out_agi"], c["out_returns"]),
            "net_agi": round((c["in_agi"] - c["out_agi"]) * AGI_UNIT),
        })

    gaining = sorted([r for r in rows if r["net_returns"] > 0],
                     key=lambda r: r["net_returns"], reverse=True)[:limit]
    losing = sorted([r for r in rows if r["net_returns"] < 0],
                    key=lambda r: r["net_returns"])[:limit]
    return {
        "cbsa": cbsa, "name": names.get(cbsa, cbsa),
        "totals": {
            "in_returns": totals["in_returns"], "out_returns": totals["out_returns"],
            "net_returns": totals["in_returns"] - totals["out_returns"],
            "in_agi_per_return": _agi_per_return(totals["in_agi"], totals["in_returns"]),
            "out_agi_per_return": _agi_per_return(totals["out_agi"], totals["out_returns"]),
            "net_agi": round((totals["in_agi"] - totals["out_agi"]) * AGI_UNIT),
        },
        "gaining_from": gaining,
        "losing_to": losing,
        "corridors": len(rows),
        "limits": LIMITS,
    }
