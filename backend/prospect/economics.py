"""What it costs to buy a condominium out, unit by unit.

The building drawer already shows every input to this number — units, age, every
owner and their share, and both comp sets — and nothing multiplied them
together. This does, and nothing else: no redevelopment residual, no margin, no
breakeven. Those need a revenue per new unit that is nowhere in this data, and a
number invented to fill that hole would be the least reliable figure in the app
sitting at the bottom of the page where the eye lands.

So the question here is only: **what would the units cost.**

Valuation ladder
----------------
Per-unit fair market value is a price per square foot against each unit's own
living area, which is what makes it defensible unit by unit rather than a
building-wide average. Where that is not available the estimate steps down, and
every unit carries the step it landed on so the result can report its own
quality:

    comp_psf         unit has living area, and the building has recent
                     single-unit sales to draw a $/SF from
    building_median  unit has no living area on the roll — use the building's
                     median single-unit price
    nearby_psf       the building itself has no recent single-unit sales — use
                     the $/SF from comparable buildings within the radius
    assessed_ratio   neither — assessed value scaled by the ratio this building's
                     own sales show between price and assessment

On assessed value
-----------------
Florida just value is not market value. It runs below market, and Save Our Homes
caps compress homesteaded units further, which biases exactly the units whose
payout floor matters most. So JV is never used as a price directly: where it is
all there is, it is scaled by a ratio measured from this building's own recorded
sales, and the unit is flagged as the weakest basis in the mix. If that ratio
cannot be measured, the estimate says so rather than assuming one.

Bulk deeds
----------
Prices come from `memo.building_sales`, which groups folios into instruments
first, so a deed conveying ten units cannot stamp its package price on ten
comps. Only single-unit sales set the $/SF, because those are the arm's-length
signal — a bulk median is what someone already paid to assemble, not what the
remaining owners will take.

Nothing here is an appraisal. It is a screening estimate whose inputs are all
visible, and it refuses to produce a figure it cannot source.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .memo import _median, building_sales

# Share of units at the top of the buyout that hold out, and what the last ones
# cost. Defaults, not findings -- every caller can override them, and the result
# always reports which numbers it used.
DEFAULT_HOLDOUT_SHARE = 0.10
DEFAULT_HOLDOUT_PREMIUM = 0.25

BASIS_ORDER = ("comp_psf", "building_median", "nearby_psf", "assessed_ratio", "none")

# Printed on every estimate, including one that could price nothing -- the limits
# do not go away because the arithmetic did.
_STANDARD_LIMITS = (
    "Screening estimate from recorded sales, not an appraisal. It excludes mortgages "
    "and liens, which the tax roll does not carry, and the statutory payout floor for "
    "homestead owners under FS 718.117, which needs homestead status on the roll.")


@dataclass
class UnitValue:
    folio: str
    living_sf: float | None
    jv: float | None
    value: float | None
    basis: str
    controlled: bool = False


@dataclass
class Buyout:
    group_key: str
    name: str | None = None
    units: int = 0
    psf: float | None = None
    psf_source: str | None = None
    assessed_ratio: float | None = None
    units_valued: int = 0
    basis_mix: dict = field(default_factory=dict)
    median_unit_value: float | None = None
    controlled_units: int = 0
    controlled_by: str | None = None
    units_to_acquire: int = 0
    cost_at_fmv: float | None = None
    holdout_share: float = DEFAULT_HOLDOUT_SHARE
    holdout_premium: float = DEFAULT_HOLDOUT_PREMIUM
    cost_with_holdout: float | None = None
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        # units_to_acquire, not units_valued: basis_mix also carries a "none"
        # bucket for the unvalued units, so units_valued alone excludes them
        # from the denominator while their count still sits in the numerator
        # sum -- every basis's percentage came out too high, and "none" came
        # out measured against the wrong population entirely.
        d["basis_summary"] = basis_summary(self.basis_mix, self.units_to_acquire)
        return d


def basis_summary(mix: dict, total: int) -> str | None:
    """'68% of units valued from in-building comps, 22% nearby, 10% assessed.'
    An estimate that will not say how it was built is a guess with a dollar sign."""
    if not total:
        return None
    label = {"comp_psf": "in-building comps", "building_median": "the building median",
             "nearby_psf": "nearby comps", "assessed_ratio": "assessed value",
             "none": "not valued"}
    parts = [f"{round(100 * mix[b] / total)}% {label[b]}"
             for b in BASIS_ORDER if mix.get(b)]
    return ", ".join(parts) if parts else None


def _assessed_ratio(sales: list[dict], con: sqlite3.Connection, group_key: str) -> float | None:
    """Price-to-assessment measured on this building's own single-unit sales.

    Derived rather than assumed: a statewide constant would be wrong per building
    and unfalsifiable, and this one is checkable against the rows it came from.
    """
    singles = [s for s in sales if not s["bulk"] and s["sale_prc1"]]
    if not singles:
        return None
    ratios = []
    for s in singles:
        folio = s["folios"][0]
        row = con.execute("SELECT jv FROM nal_condo_unit WHERE folio=?", (folio,)).fetchone()
        jv = row["jv"] if row else None
        if jv and jv > 0:
            ratios.append(s["sale_prc1"] / jv)
    return _median(ratios)


def estimate(group_key: str, con: sqlite3.Connection,
             holdout_share: float = DEFAULT_HOLDOUT_SHARE,
             holdout_premium: float = DEFAULT_HOLDOUT_PREMIUM,
             nearby_psf: float | None = None) -> Buyout:
    """Cost to acquire every unit not already controlled, at recorded-sale prices."""
    t = con.execute(
        "SELECT condo_name, addr_primary, units_nal, top_owner, top_owner_pct, "
        "top_mail_pct FROM target WHERE group_key=?", (group_key,)).fetchone()
    if not t:
        raise LookupError(f"no target {group_key}")

    b = Buyout(group_key=group_key,
               name=t["condo_name"] or t["addr_primary"],
               holdout_share=holdout_share, holdout_premium=holdout_premium)

    comps = building_sales(group_key, con)
    sales, summary = comps["sales"], comps["summary"]

    # $/SF from single-unit sales only. A bulk median is what someone paid to
    # assemble, not what the remaining owners will take.
    singles_psf = _median([s["psf"] for s in sales
                           if not s["bulk"] and s["psf"] and
                           (s["sale_yr1"] or 0) >= int(summary["window"].split("-")[0])])
    if singles_psf:
        b.psf, b.psf_source = singles_psf, "building_single_unit_sales"
    elif nearby_psf:
        b.psf, b.psf_source = nearby_psf, "nearby_buildings"
        b.caveats.append(
            "This building has no single-unit sale in the comp window, so the price "
            "per square foot comes from comparable buildings nearby.")
    else:
        b.caveats.append(
            "No price per square foot could be sourced from this building or its "
            "neighbours, so units fall back to the building median or to assessed value.")

    building_median = summary.get("single_unit_median") or summary.get("last5_median_per_unit")
    b.assessed_ratio = _assessed_ratio(sales, con, group_key)

    units = [dict(r) for r in con.execute(
        "SELECT folio, owner_norm, owner_addr_norm, tot_lvg_area, jv "
        "FROM nal_condo_unit WHERE group_key=?", (group_key,))]
    b.units = len(units)
    if not units:
        b.caveats.append("No units on the roll for this folio prefix.")
        return b

    # Units already in one hand need no buyout. Matched on the owner name the
    # screen scored, and on shared mailing address, which is how one buyer behind
    # several LLCs shows up before it shows up in any single owner name.
    top_owner = t["top_owner"]
    # HAVING COUNT(*) > 1, because a mailing address covering one unit is just an
    # owner's home address -- it is only evidence of assembly when it is shared.
    row = con.execute(
        "SELECT owner_addr_norm FROM nal_condo_unit WHERE group_key=? AND owner_addr_norm<>'' "
        "GROUP BY owner_addr_norm HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC LIMIT 1",
        (group_key,)).fetchone()
    mail_pct, owner_pct = (t["top_mail_pct"] or 0), (t["top_owner_pct"] or 0)
    top_mail = row["owner_addr_norm"] if (row and mail_pct > 0 and mail_pct >= owner_pct) else None

    values: list[UnitValue] = []
    for u in units:
        controlled = bool((top_owner and u["owner_norm"] == top_owner)
                          or (top_mail and u["owner_addr_norm"] == top_mail))
        sf, jv = u["tot_lvg_area"], u["jv"]
        if b.psf and sf:
            val, basis = b.psf * sf, ("comp_psf" if b.psf_source == "building_single_unit_sales"
                                      else "nearby_psf")
        elif building_median:
            val, basis = building_median, "building_median"
        elif jv and b.assessed_ratio:
            val, basis = jv * b.assessed_ratio, "assessed_ratio"
        else:
            val, basis = None, "none"
        values.append(UnitValue(u["folio"], sf, jv, val, basis, controlled))

    b.controlled_units = sum(1 for v in values if v.controlled)
    b.controlled_by = top_owner if b.controlled_units else None
    to_buy = [v for v in values if not v.controlled]
    b.units_to_acquire = len(to_buy)

    priced = [v for v in to_buy if v.value is not None]
    b.units_valued = len(priced)
    for v in priced:
        b.basis_mix[v.basis] = b.basis_mix.get(v.basis, 0) + 1
    unvalued = len(to_buy) - len(priced)
    if unvalued:
        b.basis_mix["none"] = unvalued
        b.caveats.append(
            f"{unvalued} of {len(to_buy)} units to acquire carry no living area, no "
            f"assessment and no comparable sale, so they are absent from the total.")

    if not priced:
        b.caveats.append("Nothing in this building could be priced from recorded data.")
        b.caveats.append(_STANDARD_LIMITS)
        return b

    b.median_unit_value = _median([v.value for v in priced])
    b.cost_at_fmv = round(sum(v.value for v in priced), 2)

    # The last units cost more than the first. Applied to the priciest tail,
    # because a holdout with leverage is usually not the cheapest unit.
    n_hold = max(1, round(len(priced) * holdout_share)) if holdout_share > 0 else 0
    tail = sorted((v.value for v in priced), reverse=True)[:n_hold]
    b.cost_with_holdout = round(b.cost_at_fmv + sum(tail) * holdout_premium, 2)

    if b.basis_mix.get("assessed_ratio"):
        b.caveats.append(
            "Some units are priced from assessed value scaled by this building's own "
            "price-to-assessment ratio. Florida just value runs below market and Save "
            "Our Homes compresses it further on homesteaded units, so those figures are "
            "the weakest in the mix.")
    if summary.get("bulk_instruments"):
        b.caveats.append(
            f"{summary['bulk_instruments']} bulk deed(s) conveying "
            f"{summary['bulk_units']} units were excluded from the price per square "
            f"foot. They are what an assembler already paid, not what the remaining "
            f"owners will take.")
    b.caveats.append(_STANDARD_LIMITS)
    return b
