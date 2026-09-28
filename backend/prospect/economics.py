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

Statutory floor (FS 718.117)
----------------------------
In a termination where one owner holds 80% or more of the voting interests
(the assembler's route), the statute sets three minimums the estimate applies:

  * every owner other than the bulk owner gets at least 100% of fair market
    value -- which the ladder above already is;
  * a HOMESTEADED owner who is current on assessments gets at least the
    original purchase price paid for the unit, when that is higher;
  * a homesteaded owner also gets a relocation payment of 1% of the proceeds
    allocated to the unit, paid by the 80% owner.

`cost_statutory` is the FMV total plus those two uplifts, and the holdout
premium is layered on top of it. The purchase price is the unit's last
recorded sale -- the current owner's acquisition -- but ONLY where that deed
conveyed this one unit: a bulk deed stamps its whole package price on every
folio it covers, and reading that as one owner's purchase price would inflate
the floor many times over. A homestead flag the roll could not resolve is
reported as unknown, not as "not homesteaded". Whether an owner is current on
assessments, and owner-occupied operating businesses (which the statute also
protects), are not on the roll; the estimate says so. Verify against the
current statute text before relying on the floor in a deal.

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
RELOCATION_SHARE = 0.01      # FS 718.117: 1% of proceeds allocated to a homestead unit

BASIS_ORDER = ("comp_psf", "building_median", "nearby_psf", "assessed_ratio", "none")

# Printed on every estimate, including one that could price nothing -- the limits
# do not go away because the arithmetic did.
_STANDARD_LIMITS = (
    "Screening estimate from recorded sales, not an appraisal. It excludes mortgages "
    "and liens, which the tax roll does not carry. The FS 718.117 homestead floor "
    "assumes every homesteaded owner is current on assessments, and cannot see "
    "owner-occupied operating businesses, which the statute also protects.")


@dataclass
class UnitValue:
    folio: str
    living_sf: float | None
    jv: float | None
    value: float | None
    basis: str
    controlled: bool = False
    homestead: bool | None = None      # None = the roll could not say
    purchase_price: float | None = None  # last sale, only when it conveyed this unit alone


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
    homestead_units: int = 0            # among the units to acquire
    homestead_unknown: int = 0
    purchase_price_uplift: float = 0.0  # original-purchase-price floor above FMV
    relocation_payments: float = 0.0    # 1% of proceeds on homestead units
    cost_statutory: float | None = None
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

    cols = {r[1] for r in con.execute("PRAGMA table_info(nal_condo_unit)")}
    hs_col = "homestead" if "homestead" in cols else "NULL AS homestead"
    units = [dict(r) for r in con.execute(
        f"SELECT folio, owner_norm, owner_addr_norm, tot_lvg_area, jv, {hs_col}, "
        "sale_prc1, or_book1, or_page1 FROM nal_condo_unit WHERE group_key=?", (group_key,))]
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

    # A deed that conveyed several units carries the package price on each of
    # them; only a single-unit deed is one owner's purchase price.
    deed_units: dict = {}
    for u in units:
        if u["or_book1"] and u["or_page1"]:
            k = (u["or_book1"], u["or_page1"])
            deed_units[k] = deed_units.get(k, 0) + 1

    def own_purchase(u):
        if not u["sale_prc1"] or u["sale_prc1"] <= 0:
            return None
        k = (u["or_book1"], u["or_page1"])
        if not (u["or_book1"] and u["or_page1"]) or deed_units.get(k, 0) != 1:
            return None     # no instrument to check, or a bulk deed
        return float(u["sale_prc1"])

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
        hs = None if u["homestead"] is None else bool(u["homestead"])
        values.append(UnitValue(u["folio"], sf, jv, val, basis, controlled,
                                hs, own_purchase(u) if hs else None))

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

    # FS 718.117 floor for homesteaded owners: at least their purchase price,
    # plus a 1% relocation payment on what their unit is paid.
    b.homestead_units = sum(1 for v in to_buy if v.homestead)
    b.homestead_unknown = sum(1 for v in to_buy if v.homestead is None)
    payout = {}
    for v in priced:
        p = v.value
        if v.homestead:
            if v.purchase_price and v.purchase_price > p:
                b.purchase_price_uplift += v.purchase_price - p
                p = v.purchase_price
            b.relocation_payments += p * RELOCATION_SHARE
        payout[v.folio] = p
    b.purchase_price_uplift = round(b.purchase_price_uplift, 2)
    b.relocation_payments = round(b.relocation_payments, 2)
    b.cost_statutory = round(b.cost_at_fmv + b.purchase_price_uplift + b.relocation_payments, 2)

    # The last units cost more than the first. Applied to the priciest tail of
    # what each unit must actually be paid, because a holdout with leverage is
    # usually not the cheapest unit.
    n_hold = max(1, round(len(priced) * holdout_share)) if holdout_share > 0 else 0
    tail = sorted(payout.values(), reverse=True)[:n_hold]
    b.cost_with_holdout = round(b.cost_statutory + sum(tail) * holdout_premium, 2)

    if b.homestead_units:
        b.caveats.append(
            f"{b.homestead_units} unit(s) to acquire are homesteaded. FS 718.117 guarantees "
            f"them at least their original purchase price and a 1% relocation payment: "
            f"+${b.purchase_price_uplift:,.0f} and +${b.relocation_payments:,.0f} over fair "
            f"market value here.")
    if b.homestead_unknown:
        b.caveats.append(
            f"Homestead status is unknown for {b.homestead_unknown} unit(s) -- the roll's "
            f"exemption column could not be read -- so their statutory floor is not included.")

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
