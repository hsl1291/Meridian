"""Offering memorandum for a condo termination / bulk-acquisition target.

Everything in the document comes from data this app already holds — the scored
``target`` row, the unit-level tax roll, recorded sales, the zoning envelope,
and the metro screener — so a memo is one click from a row in Records and needs
no other application installed.

Two comp sets, because a bulk buyout is priced on both:

  in-building   every recorded sale inside the building, which is what you are
                actually buying out, unit by unit
  nearby        comparable condo buildings within a radius, screened to a
                similar vintage and scale, with their own recorded-sale medians

Sale prices are the FDOR tax-roll SALE_PRC1/SALE_YR1 fields — the most recent
recorded consideration per folio. They are recorded facts, not estimates, but
they are *last* sales spread across many years, so the memo always prints the
year alongside the price and never blends vintages into a single "market price".
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
OUT_DIR = ROOT / "data" / "memos"

MEMO_CFG = CFG.get("memo") or {}
FIRM = MEMO_CFG.get("firm") or ""
CONFIDENTIALITY = MEMO_CFG.get("confidentiality") or (
    "Confidential — for the recipient's evaluation only. Not an offer to sell securities.")


# ── formatting ─────────────────────────────────────────────────────────────

def money(v, dp=0):
    return "—" if v in (None, "") else f"${v:,.{dp}f}"


def num(v):
    return "—" if v in (None, "") else f"{v:,.0f}"


def pct(v, dp=0):
    return "—" if v in (None, "") else f"{v:.{dp}f}%"


def _median(vals: list[float]) -> float | None:
    vs = sorted(v for v in vals if v is not None)
    if not vs:
        return None
    n = len(vs)
    return vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2


def _miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def building_sales(group_key: str, con) -> dict:
    """Recorded sales inside one building, grouped into instruments.

    Lifted out of gather() so the memo and the buyout estimate read the same
    numbers: two comp engines that drift apart is how a memo and a screen end up
    quoting different prices for the same building.

    Returns {"sales": [...], "summary": {...}, "gaps": [...]}.
    """
    out: dict = {}
    gaps: list[str] = []
    # ---- comp set 1: recorded sales inside the building ----
    #
    # The roll's SALE_PRC1 is the consideration on the *instrument*, and a deed
    # conveying ten units stamps the same package price on all ten folios. Left
    # alone that inflates a per-unit median by an order of magnitude — one real
    # building here shows 148 folios carrying prices up to $4.9M each, which are
    # really a handful of bulk deeds. So sales are grouped into instruments by
    # (year, price) and a package's per-unit figure is the consideration divided
    # by the folios it covers. Only per-unit figures are ever medianed.
    try:
        units_rows = [dict(r) for r in con.execute(
            "SELECT folio, owner_name, sale_yr1, sale_prc1, tot_lvg_area, jv, "
            "or_book1, or_page1, is_entity, qual_cd1 FROM nal_condo_unit "
            "WHERE group_key=? AND sale_prc1>0 AND sale_yr1 IS NOT NULL "
            "ORDER BY sale_yr1 DESC, sale_prc1 DESC", (group_key,))]
    except sqlite3.OperationalError:
        # A roll ingested before qual_cd1 existed. Every sale is kept, which is
        # the same outcome as a roll that carries no qualification column.
        units_rows = [dict(r, qual_cd1=None) for r in con.execute(
            "SELECT folio, owner_name, sale_yr1, sale_prc1, tot_lvg_area, jv, "
            "or_book1, or_page1, is_entity FROM nal_condo_unit "
            "WHERE group_key=? AND sale_prc1>0 AND sale_yr1 IS NOT NULL "
            "ORDER BY sale_yr1 DESC, sale_prc1 DESC", (group_key,))]

    # Arm's-length filter, applied only where the roll actually carries a code.
    # A NULL means the column was absent, and dropping those would silently empty
    # the comp set on every roll that predates the field -- "we cannot tell" is
    # not "disqualified".
    arms = set((CFG.get("comps") or {}).get("arms_length_codes") or [])
    non_arms = 0
    if arms:
        keep = []
        for r in units_rows:
            code = (r.get("qual_cd1") or "").strip()
            if code and code not in arms:
                non_arms += 1
                continue
            keep.append(r)
        units_rows = keep
    if not units_rows:
        gaps.append("No recorded sale carries a price for any unit in this building.")

    grouped: dict[tuple, dict] = {}
    for r in units_rows:
        # Group by the recorded instrument where the roll names one. OR book/page
        # IS the instrument, so it separates ten folios on one deed from ten
        # separate deeds that happen to share a price.
        #
        # (year, price) is the fallback, and it is a lossy one: three units that
        # each sold for $440,000 in the same year collapse into a single
        # "$440,000 for three units" and price out at a third of what they cost.
        # Identical round prices are not exotic -- a tower of identical
        # floorplans, or the nominal $10 considerations on quitclaim transfers,
        # collide exactly this way.
        book, page = (r["or_book1"] or "").strip(), (r["or_page1"] or "").strip()
        k = ("or", book, page) if (book and page) else ("yp", r["sale_yr1"], r["sale_prc1"])
        g_ = grouped.setdefault(k, {
            "sale_yr1": r["sale_yr1"], "sale_prc1": r["sale_prc1"],
            "folios": [], "buyer": r["owner_name"], "is_entity": r["is_entity"],
            "living_sf": 0.0, "or_book1": r["or_book1"], "or_page1": r["or_page1"],
        })
        g_["folios"].append(r["folio"])
        g_["living_sf"] += r.get("tot_lvg_area") or 0.0

    sales = []
    for g_ in grouped.values():
        n = len(g_["folios"])
        g_["units"] = n
        g_["bulk"] = n > 1
        g_["per_unit"] = g_["sale_prc1"] / n
        g_["psf"] = (g_["sale_prc1"] / g_["living_sf"]) if g_["living_sf"] else None
        g_["folio"] = g_["folios"][0] + (f" +{n - 1}" if n > 1 else "")
        sales.append(g_)
    sales.sort(key=lambda s: (-(s["sale_yr1"] or 0), -s["sale_prc1"]))
    out["sales"] = sales

    this_year = date.today().year
    recent = [s for s in sales if (s["sale_yr1"] or 0) >= this_year - 5]
    singles = [s for s in recent if not s["bulk"]]
    bulk = [s for s in recent if s["bulk"]]
    out["summary"] = {
        "instruments_recorded": len(sales),
        "units_with_price": len(units_rows),
        "last5_instruments": len(recent),
        "last5_units": sum(s["units"] for s in recent),
        "last5_median_per_unit": _median([s["per_unit"] for s in recent]),
        "last5_median_psf": _median([s["psf"] for s in recent if s["psf"]]),
        "single_unit_count": len(singles),
        "single_unit_median": _median([s["per_unit"] for s in singles]),
        "bulk_instruments": len(bulk),
        "bulk_units": sum(s["units"] for s in bulk),
        "bulk_median_per_unit": _median([s["per_unit"] for s in bulk]),
        "entity_buy_units": sum(s["units"] for s in recent if s.get("is_entity")),
        "window": f"{this_year - 5}-{this_year}",
        "non_arms_length_excluded": non_arms,
    }
    if non_arms:
        gaps.append(
            f"{non_arms} recorded sale(s) carry a qualification code outside the "
            f"arm's-length set and were excluded from the comps. Intra-family and "
            f"corrective deeds are not market evidence.")
    if bulk:
        gaps.append(
            f"{len(bulk)} recorded instrument(s) in the last five years convey "
            f"{sum(s['units'] for s in bulk)} units together. Their per-unit figures "
            f"are the package price divided by unit count, not separately negotiated "
            f"prices — price the single-unit sales, not these.")

    out["gaps"] = gaps
    return out


# ── gather ─────────────────────────────────────────────────────────────────

def gather(group_key: str, con: sqlite3.Connection,
           radius_mi: float = 2.0, lot_sf: float | None = None) -> dict:
    """Assemble everything the memo prints. Missing pieces become entries in
    ``gaps`` rather than exceptions — a partial memo is still useful, so long as
    it says out loud what it could not find."""
    doc: dict = {"group_key": group_key, "generated": date.today().isoformat(),
                 "firm": FIRM, "gaps": []}

    t = con.execute("SELECT * FROM target WHERE group_key=?", (group_key,)).fetchone()
    if not t:
        raise LookupError(f"no target {group_key}")
    doc["target"] = dict(t)

    g = con.execute("SELECT * FROM condo_group WHERE group_key=?", (group_key,)).fetchone()
    doc["group"] = dict(g) if g else None

    if t["project_number"]:
        r = con.execute("SELECT * FROM dbpr_association WHERE project_number=?",
                        (t["project_number"],)).fetchone()
        doc["dbpr"] = dict(r) if r else None
    else:
        doc["dbpr"] = None
        doc["gaps"].append(
            "No DBPR registry match — declaration recording date and the managing "
            "entity are unknown for this building.")

    # ---- ownership ----
    doc["owners"] = [dict(r) for r in con.execute(
        "SELECT owner_name, owner_city, owner_state, COUNT(*) units, SUM(jv) jv, "
        "MAX(is_entity) is_entity FROM nal_condo_unit WHERE group_key=? "
        "GROUP BY owner_norm ORDER BY units DESC LIMIT 12", (group_key,))]
    doc["shared_mailing"] = [dict(r) for r in con.execute(
        "SELECT owner_addr_norm, COUNT(*) units, COUNT(DISTINCT owner_norm) names "
        "FROM nal_condo_unit WHERE group_key=? AND owner_addr_norm<>'' "
        "GROUP BY owner_addr_norm HAVING units>1 ORDER BY units DESC LIMIT 8",
        (group_key,))]

    # ---- comp set 1: recorded sales inside the building ----
    _s = building_sales(group_key, con)
    doc["sales_in_building"] = _s["sales"]
    doc["sales_summary"] = _s["summary"]
    doc["gaps"].extend(_s["gaps"])

    # ---- comp set 2: comparable buildings nearby ----
    comps: list[dict] = []
    if t["lon"] is not None and t["lat"] is not None:
        # Degree box first (indexed-friendly), exact distance after.
        dlat = radius_mi / 69.0
        dlon = radius_mi / (69.0 * max(0.2, math.cos(math.radians(t["lat"]))))
        cand = con.execute(
            "SELECT group_key, condo_name, addr_primary, city, units_nal, act_yr_blt, "
            "age_years, jv_per_unit, top_owner_pct, score, lon, lat FROM target "
            "WHERE group_key<>? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? "
            "AND units_nal >= ?",
            (group_key, t["lon"] - dlon, t["lon"] + dlon,
             t["lat"] - dlat, t["lat"] + dlat,
             max(10, int((t["units_nal"] or 0) * 0.3)))).fetchall()
        for c in cand:
            d = _miles(t["lat"], t["lon"], c["lat"], c["lon"])
            if d > radius_mi:
                continue
            row = dict(c)
            row["distance_mi"] = round(d, 2)
            comps.append(row)
        comps.sort(key=lambda r: r["distance_mi"])
        comps = comps[:12]
        # Recorded-sale medians per comp building, last 5 years.
        this_year = date.today().year
        for c in comps:
            rows = con.execute(
                "SELECT sale_yr1, sale_prc1, tot_lvg_area FROM nal_condo_unit "
                "WHERE group_key=? AND sale_prc1>0 AND sale_yr1>=?",
                (c["group_key"], this_year - 5)).fetchall()
            # Same package-price collapse as the subject building, so a comp
            # with one bulk deed does not read as a market of $4M units.
            buckets: dict[tuple, list] = {}
            for r in rows:
                buckets.setdefault((r["sale_yr1"], r["sale_prc1"]), []).append(r)
            per_unit, psf = [], []
            for (_, price), members in buckets.items():
                per_unit.append(price / len(members))
                sf = sum(m["tot_lvg_area"] or 0 for m in members)
                if sf:
                    psf.append(price / sf)
            c["sale_n"] = len(rows)
            c["sale_instruments"] = len(buckets)
            c["sale_median"] = _median(per_unit)
            c["sale_psf"] = _median(psf)
    else:
        doc["gaps"].append("This building has no coordinates, so no nearby comparables "
                           "could be pulled.")
    doc["comps"] = comps
    doc["comps_radius_mi"] = radius_mi

    # ---- redevelopment capacity ----
    doc["capacity"] = None
    if t["lon"] is not None:
        try:
            from . import capacity as capacity_mod
            cap = capacity_mod.analyze(t["lon"], t["lat"], lot_sf)
            doc["capacity"] = cap if cap.get("resolved") else None
            if not doc["capacity"]:
                doc["gaps"].append("No zoning polygon covers this point — the "
                                   "redevelopment envelope could not be computed.")
        except Exception:  # noqa: BLE001 — a capacity failure must not kill the memo
            doc["gaps"].append("Zoning layer unavailable — redevelopment envelope omitted.")

    # ---- metro context ----
    cbsa = (CFG.get("market") or {}).get("home_cbsa")
    doc["market"] = None
    if cbsa:
        try:
            m = con.execute("SELECT * FROM market WHERE cbsa=?", (cbsa,)).fetchone()
            doc["market"] = dict(m) if m else None
        except sqlite3.Error:
            pass
    if not doc["market"]:
        doc["gaps"].append("Metro screener data unavailable — market context omitted.")

    # ---- termination posture ----
    if not t["stage2_verified"]:
        doc["gaps"].append(
            "Stage 2 is not complete: the recorded declaration has not been read, so "
            "the termination threshold and Kaufman language are unconfirmed. Every "
            "termination statement in this memo is conditional on that review.")
    return doc


# ── render ─────────────────────────────────────────────────────────────────

CSS = """
/* Print geometry.
   The side inset is set in ONE place per medium. On paper that is @page; on
   screen it is .pad and .cover. They used to stack -- @page inset 0.7in and then
   .pad added another 0.7in inside it, leaving about 5.7in of live width on
   Letter for tables built for more. */
@page { size: Letter; margin: 0.75in 0.7in 0.85in; }
*{box-sizing:border-box}
body{margin:0;font:10.5pt/1.55 Georgia,"Times New Roman",serif;color:#16212e;background:#f2f4f7;
  orphans:3;widows:3}
.sheet{max-width:7.6in;margin:0 auto;padding:0 0 40px;background:#fff;
  box-shadow:0 1px 30px rgba(15,23,42,.08)}
.pad{padding:0 .7in}
h1,h2,h3,.eyebrow,table,.kv,.statband{font-family:"Helvetica Neue",Arial,sans-serif}

/* 11in sheet less 0.75in + 0.85in of @page margin leaves 9.4in of live height.
   This was 9.6in, so it overflowed by two tenths of an inch and
   page-break-after then added a second break -- every memo had a blank page 2. */
.cover{min-height:9.2in;display:flex;flex-direction:column;justify-content:space-between;
  page-break-after:always;padding:1.1in .7in .6in}
.eyebrow{letter-spacing:.24em;text-transform:uppercase;font-size:8.5pt;color:#77879a;font-weight:600}
.cover h1{font-size:36pt;line-height:1.04;margin:16px 0 8px;font-weight:600;letter-spacing:-.6px}
.cover .addr{font-size:12.5pt;color:#4a5b6e;margin:0}
.rule{height:3px;background:#1f3b57;width:88px;margin:24px 0}
.statband{display:flex;border-top:1px solid #d5dde5;border-bottom:1px solid #d5dde5}
.statband div{flex:1;padding:14px 14px 12px;border-right:1px solid #eef2f6}
.statband div:last-child{border-right:none}
.statband b{display:block;font:600 18pt/1.1 "Helvetica Neue",Arial,sans-serif;color:#1f3b57}
.statband span{font-size:8pt;letter-spacing:.12em;text-transform:uppercase;color:#77879a}

/* Break avoidance belongs on rows and small blocks. It used to sit on `section`,
   which can be taller than a sheet -- where it either does nothing or pushes a
   large blank gap ahead of the section. */
section{margin:0 0 26px}
h2,h3{break-after:avoid;page-break-after:avoid}
h2{font-size:14.5pt;margin:32px 0 3px;color:#1f3b57;font-weight:600;letter-spacing:-.2px}
h2+.sub{color:#77879a;font-size:9pt;margin:0 0 12px}
h3{font-size:10.5pt;margin:18px 0 5px;color:#16212e;font-weight:600}
p{margin:0 0 10px}
table{border-collapse:collapse;width:100%;font-size:9.2pt;margin:9px 0 4px}
/* A forty-row sales table runs onto the next page. Without this it arrives
   there with no column labels. */
thead{display:table-header-group}
tfoot{display:table-footer-group}
tr{break-inside:avoid;page-break-inside:avoid}
th{text-align:left;font-size:7.6pt;letter-spacing:.09em;text-transform:uppercase;color:#77879a;
  border-bottom:1.5px solid #1f3b57;padding:6px 7px;font-weight:600}
td{padding:5.5px 7px;border-bottom:1px solid #e8edf2}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.subject td{background:#f2f7fc;font-weight:600}
tr.total td{border-top:1.5px solid #1f3b57;border-bottom:none;font-weight:700;background:#f7f9fb}
/* Fixed track count: auto-fill with 1fr stretched a leftover cell across the
   whole final row whenever the count did not divide evenly. */
.kv{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
  background:#e8edf2;border:1px solid #e8edf2;margin:12px 0}
.kv div{background:#fff;padding:9px 11px;break-inside:avoid}
.kv b{display:block;font:600 12.5pt/1.2 "Helvetica Neue",Arial,sans-serif;color:#1f3b57}
.kv span{font-size:7.6pt;letter-spacing:.09em;text-transform:uppercase;color:#77879a}
.callout{border-left:3px solid #1f3b57;background:#f7f9fb;padding:11px 14px;margin:14px 0;
  font-size:9.8pt;break-inside:avoid;page-break-inside:avoid}
.gap{border-left:3px solid #c8791d;background:#fdf7ef;padding:10px 13px;margin:12px 0;
  font-size:9.2pt;color:#7a5312;font-family:"Helvetica Neue",Arial,sans-serif;
  break-inside:avoid;page-break-inside:avoid}
.gap ul{margin:6px 0 0;padding-left:18px}
.foot{margin-top:36px;padding-top:12px;border-top:1px solid #d5dde5;font-size:7.8pt;
  color:#8a99a9;font-family:"Helvetica Neue",Arial,sans-serif;break-inside:avoid}
.src{font-size:7.8pt;color:#8a99a9;font-family:"Helvetica Neue",Arial,sans-serif;margin:3px 0 0}
.noprint{position:fixed;top:14px;right:16px;background:#1f3b57;color:#fff;border:none;
  padding:9px 15px;border-radius:6px;font:600 12px "Helvetica Neue",Arial;cursor:pointer;
  box-shadow:0 2px 10px rgba(15,23,42,.2)}

@media print{
  body{background:#fff}
  .sheet{box-shadow:none;max-width:none}
  .noprint{display:none}
  /* @page already owns the side inset on paper. */
  .pad{padding:0}
  .cover{padding:0.35in 0 0.2in}
  .kv{grid-template-columns:repeat(4,1fr)}
}
"""


def _table(rows, cols, mark_subject=None):
    """cols = [(header, key, numeric, formatter)]"""
    head = "".join(f'<th class="{"n" if c[2] else ""}">{escape(c[0])}</th>' for c in cols)
    body = []
    for r in rows:
        tds = []
        for _, key, isnum, fmt in cols:
            v = r.get(key)
            txt = fmt(v) if fmt else escape("—" if v in (None, "") else str(v))
            tds.append(f'<td class="{"n" if isnum else ""}">{txt}</td>')
        cls = ' class="subject"' if mark_subject and r.get(mark_subject) else ""
        body.append(f"<tr{cls}>" + "".join(tds) + "</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body) or '<tr><td>—</td></tr>'}</tbody></table>")


def render(doc: dict) -> str:
    t = doc["target"]
    name = t.get("condo_name") or t.get("addr_primary") or doc["group_key"]
    addr = ", ".join(x for x in (t.get("addr_primary"), t.get("city")) if x)
    ss = doc["sales_summary"]
    parts = []

    # ── cover ──────────────────────────────────────────────────────────────
    stats = [
        (num(t.get("units_nal")), "Residential units"),
        (str(t.get("act_yr_blt") or "—"), "Year built"),
        (money(t.get("jv_per_unit")), "Assessed / unit"),
        (pct(t.get("top_owner_pct")), "Largest owner"),
    ]
    eyebrow = (f"{escape(doc['firm'])} &nbsp;·&nbsp; " if doc.get("firm") else "")
    parts.append(f"""
<div class="cover">
  <div>
    <div class="eyebrow">{eyebrow}Condominium Acquisition Memorandum</div>
    <div class="rule"></div>
    <h1>{escape(name)}</h1>
    <p class="addr">{escape(addr)}</p>
  </div>
  <div>
    <div class="statband">
      {''.join(f'<div><b>{v}</b><span>{l}</span></div>' for v, l in stats)}
    </div>
    <p class="src" style="margin-top:16px">Prepared {doc['generated']} ·
      Folio prefix {escape(doc['group_key'])} · {escape(CONFIDENTIALITY)}</p>
  </div>
</div>""")

    parts.append('<div class="pad">')

    if doc["gaps"]:
        parts.append('<div class="gap"><b>What this draft could not confirm</b><ul>'
                     + "".join(f"<li>{escape(g)}</li>" for g in doc["gaps"])
                     + "</ul></div>")

    # ── the opportunity ────────────────────────────────────────────────────
    units = t.get("units_nal") or 0
    agg_jv = (t.get("jv_per_unit") or 0) * units or None
    thesis = [
        f"<b>{escape(name)}</b> is a {num(units)}-unit condominium"]
    if t.get("act_yr_blt"):
        thesis.append(f" completed in {t['act_yr_blt']}")
    if t.get("age_years") is not None:
        thesis.append(f", now {t['age_years']} years old")
    if addr:
        thesis.append(f", at {escape(addr)}")
    thesis.append(". ")
    if t.get("top_owner_pct"):
        thesis.append(
            f"Its largest single owner controls {pct(t['top_owner_pct'], 1)} of units"
            + (f" ({escape(t['top_owner'])})" if t.get("top_owner") else "") + ". ")
    if t.get("absentee_pct") is not None:
        thesis.append(f"{pct(t['absentee_pct'])} of owners are absentee")
        if t.get("corporate_pct") is not None:
            thesis.append(f" and {pct(t['corporate_pct'])} are entities rather than individuals")
        thesis.append(" — both raise the share of the building reachable through a "
                      "single negotiation rather than door-to-door. ")
    if agg_jv:
        thesis.append(f"At the county's assessed values the building carries "
                      f"{money(agg_jv)} of just value, or {money(t.get('jv_per_unit'))} per unit.")

    parts.append(f"""
<section><h2>The opportunity</h2>
<div class="sub">Why this building screens as a termination or bulk-acquisition play</div>
<p>{''.join(thesis)}</p>
<div class="kv">
  <div><b>{num(units)}</b><span>Units (tax roll)</span></div>
  <div><b>{t.get('units_dbpr') or '—'}</b><span>Units (DBPR)</span></div>
  <div><b>{t.get('act_yr_blt') or '—'}</b><span>Year built</span></div>
  <div><b>{t.get('recorded_year') or '—'}</b><span>Declaration recorded</span></div>
  <div><b>{money(t.get('jv_per_unit'))}</b><span>Assessed / unit</span></div>
  <div><b>{money(t.get('lnd_val_per_unit'))}</b><span>Land value / unit</span></div>
  <div><b>{pct(t.get('top_owner_pct'), 1)}</b><span>Largest owner</span></div>
  <div><b>{pct(t.get('top_mail_pct'), 1)}</b><span>Shared mailing</span></div>
  <div><b>{pct(t.get('absentee_pct'))}</b><span>Absentee</span></div>
  <div><b>{pct(t.get('corporate_pct'))}</b><span>Entity-owned</span></div>
  <div><b>{'' if t.get('score') is None else f"{t['score']:.1f}"}</b><span>Screen score</span></div>
  <div><b>{'Yes' if t.get('milestone_due') else 'No'}</b><span>Milestone due</span></div>
</div>
<p class="src">Screen score = {int(CFG['score_weights']['age']*100)}% age ·
  {int(CFG['score_weights']['scale']*100)}% scale ·
  {int(CFG['score_weights']['concentration']*100)}% ownership concentration ·
  {int(CFG['score_weights']['absentee']*100)}% absentee. Sources: FDOR NAL tax roll
  ({CFG['roll_year']} {CFG['roll_type']}), DBPR condominium registry.</p>
</section>""")

    # ── ownership ──────────────────────────────────────────────────────────
    g = doc.get("group") or {}
    own_cols = [("Owner", "owner_name", False, lambda v: escape(str(v or "—"))),
                ("Location", "_loc", False, lambda v: escape(str(v or "—"))),
                ("Type", "is_entity", False, lambda v: "Entity" if v else "Individual"),
                ("Units", "units", True, num),
                ("Share", "_share", True, lambda v: pct(v, 1)),
                ("Assessed", "jv", True, money)]
    owners = []
    for o in doc["owners"]:
        row = dict(o)
        row["_loc"] = ", ".join(x for x in (o.get("owner_city"), o.get("owner_state")) if x)
        row["_share"] = 100 * o["units"] / units if units else None
        owners.append(row)
    assoc_note = ""
    if g.get("assoc_owned_units"):
        assoc_note = (f" Association-owned units ({num(g['assoc_owned_units'])}, "
                      f"{pct(g.get('assoc_owned_pct'))}) are excluded from the "
                      f"largest-owner figure — the HOA is not an acquirer.")
    mail_block = ""
    if doc["shared_mailing"]:
        mail_block = ("<h3>Mailing addresses shared by more than one owner of record</h3>"
                      "<p>One buyer behind several LLCs shows up here before it shows up in "
                      "any single owner name.</p>"
                      + _table(doc["shared_mailing"], [
                          ("Mailing address", "owner_addr_norm", False,
                           lambda v: escape(str(v or "—"))),
                          ("Distinct names", "names", True, num),
                          ("Units", "units", True, num)]))
    parts.append(f"""
<section><h2>Ownership</h2>
<div class="sub">Who you would have to buy from</div>
<p>{num(g.get('distinct_owners'))} distinct owners hold {num(units)} units.{assoc_note}</p>
{_table(owners, own_cols)}
{mail_block}
<p class="src">Source: FDOR NAL tax roll, owner of record as of the
  {CFG['roll_year']} {CFG['roll_type']} roll.</p>
</section>""")

    # ── sales comps: inside the building ───────────────────────────────────
    recent_rows = [s for s in doc["sales_in_building"]
                   if (s["sale_yr1"] or 0) >= date.today().year - 5][:20]
    for s in recent_rows:
        s["_or"] = f"{s['or_book1']}-{s['or_page1']}" if s.get("or_book1") else None
    sale_cols = [("Year", "sale_yr1", False, lambda v: str(v or "—")),
                 ("Folio", "folio", False, lambda v: escape(str(v or "—"))),
                 ("Buyer", "buyer", False, lambda v: escape((str(v or "—"))[:34])),
                 ("Units", "units", True, num),
                 ("Consideration", "sale_prc1", True, money),
                 ("Per unit", "per_unit", True, money),
                 ("$/SF", "psf", True, lambda v: money(v, 0)),
                 ("OR book-page", "_or", False, lambda v: escape(str(v or "—")))]
    bulk_note = ""
    if ss["bulk_instruments"]:
        bulk_note = f"""
<div class="callout"><b>Read the single-unit line, not the blended one.</b>
{num(ss['bulk_instruments'])} instrument(s) in this window convey
{num(ss['bulk_units'])} units together at a median of
{money(ss['bulk_median_per_unit'])} per unit implied. Package consideration is
stamped on every folio it covers, so those rows are one negotiation, not many.
{f"The {num(ss['single_unit_count'])} single-unit sales — median {money(ss['single_unit_median'])} — are the arm's-length signal."
  if ss['single_unit_count'] else
  "There are no single-unit sales in this window, so the building has no arm's-length benchmark of its own; price it off the nearby comparables below."}</div>"""
    parts.append(f"""
<section><h2>Sales comparables — inside the building</h2>
<div class="sub">Recorded consideration on the units themselves, {escape(ss['window'])}</div>
<div class="kv">
  <div><b>{num(ss['units_with_price'])}</b><span>Units with a recorded price</span></div>
  <div><b>{num(ss['last5_instruments'])}</b><span>Instruments, {escape(ss['window'])}</span></div>
  <div><b>{num(ss['single_unit_count'])}</b><span>Single-unit sales</span></div>
  <div><b>{money(ss['single_unit_median'])}</b><span>Median, single-unit</span></div>
  <div><b>{money(ss['last5_median_psf'])}</b><span>Median $/SF</span></div>
  <div><b>{money(t.get('jv_per_unit'))}</b><span>Assessed / unit</span></div>
</div>
{bulk_note}
{_table(recent_rows, sale_cols)}
<p class="src">Most recent recorded sale per folio (FDOR SALE_PRC1 / SALE_YR1), grouped
  into instruments where one price covers several folios; "per unit" on those rows is the
  consideration divided by the units conveyed. These are each folio's <i>last</i> sale, not
  simultaneous transactions — read every line against its year. The roll carries no
  qualification code, so intra-family and other non-arm's-length transfers are not filtered
  out; confirm the deed before relying on any single line.</p>
</section>""")

    # ── sales comps: nearby buildings ──────────────────────────────────────
    comp_rows = [{
        "_subject": True, "condo_name": name, "distance_mi": 0.0,
        "units_nal": t.get("units_nal"), "act_yr_blt": t.get("act_yr_blt"),
        "jv_per_unit": t.get("jv_per_unit"), "top_owner_pct": t.get("top_owner_pct"),
        "sale_n": ss["last5_units"], "sale_median": ss["last5_median_per_unit"],
        "sale_psf": ss["last5_median_psf"],
    }] + doc["comps"]
    comp_cols = [("Building", "condo_name", False,
                  lambda v: escape((str(v or "—"))[:34])),
                 ("Miles", "distance_mi", True,
                  lambda v: "—" if v is None else ("subject" if v == 0 else f"{v:.2f}")),
                 ("Units", "units_nal", True, num),
                 ("Built", "act_yr_blt", False, lambda v: str(v or "—")),
                 ("Assessed/unit", "jv_per_unit", True, money),
                 ("Units sold 5y", "sale_n", True, num),
                 ("Median/unit", "sale_median", True, money),
                 ("Median $/SF", "sale_psf", True, lambda v: money(v, 0)),
                 ("Top owner", "top_owner_pct", True, lambda v: pct(v))]
    parts.append(f"""
<section><h2>Sales comparables — nearby buildings</h2>
<div class="sub">Condominiums within {doc['comps_radius_mi']:g} miles at comparable scale</div>
{_table(comp_rows, comp_cols, mark_subject="_subject")}
<p class="src">Comparable set: condominiums within {doc['comps_radius_mi']:g} miles holding at
  least 30% of the subject's unit count, nearest first. Median per unit and $/SF are the
  medians of recorded instruments in that building over the last five years, with package
  deeds divided down to a per-unit basis the same way as the subject. Assessed value is the
  county's, not a valuation.</p>
</section>""")

    # ── termination path ───────────────────────────────────────────────────
    kauf = ("present in the original declaration" if t.get("kaufman_original")
            else "added by amendment" if t.get("kaufman_by_amendment")
            else "not present" if t.get("kaufman_original") == 0
            else "not yet reviewed")
    thr = t.get("termination_threshold") or "not yet reviewed"
    decl = (f"OR {t['declaration_or_book']}-{t.get('declaration_or_page') or ''}"
            if t.get("declaration_or_book") else "not on file")
    milestone = ("<p><b>The milestone-inspection window is open.</b> Under FS 553.899 a "
                 "building of this age owes a structural milestone inspection, and the "
                 "reserve study that follows it. Special assessments arising from either "
                 "are the single most common trigger for owners to entertain a buyout — "
                 "and the most common source of a surprise liability. Verify the current "
                 "statute text and the building's inspection status.</p>"
                 if t.get("milestone_due") else "")
    parts.append(f"""
<section><h2>Termination path</h2>
<div class="sub">FS 718.117 and what still has to be verified</div>
<p>Florida Statute 718.117 permits termination of a condominium on the approval of
<b>80%</b> of total voting interests, provided no more than 5% object. Many older
declarations set a higher bar, frequently unanimity. Whether the statutory 80%
reaches this building turns on whether its declaration adopts the Condominium Act
<i>as amended from time to time</i> — "Kaufman language", after
<i>Kaufman v. Shere</i>, 347 So. 2d 627 (Fla. 3d DCA 1977).</p>
<div class="kv">
  <div><b>{escape(str(thr))}</b><span>Threshold in declaration</span></div>
  <div><b>{escape(kauf)}</b><span>Kaufman language</span></div>
  <div><b>{escape(decl)}</b><span>Declaration instrument</span></div>
  <div><b>{'Verified' if t.get('stage2_verified') else 'Not verified'}</b><span>Stage 2 review</span></div>
</div>
{milestone}
<div class="callout"><b>The distinction that decides the deal.</b> Florida's Third DCA
ruled against a developer that acquired 183 of 192 units and then <i>amended</i> the
declaration to insert Kaufman language and drop the threshold from unanimous to 80%.
Kaufman in the <i>original</i> recorded declaration carries weight; Kaufman added later
by a bulk owner is the fact pattern that lost. Those two are tracked as separate
findings here and are never collapsed into one.</div>
{f'<p><b>Reviewer notes.</b> {escape(t["stage2_notes"])}</p>' if t.get("stage2_notes") else ""}
<p class="src">Statutory summary only, current as of drafting and not legal advice.
  Confirm the statute text and read the recorded declaration and every amendment
  with Florida counsel before acting.</p>
</section>""")

    # ── redevelopment capacity ─────────────────────────────────────────────
    cap = doc.get("capacity")
    if cap:
        z = cap.get("zoning") or {}
        scen_cols = [("Development path", "path", False, lambda v: escape(str(v or "—"))),
                     ("Units", "units", True, num),
                     ("Key requirement", "requirement", False,
                      lambda v: escape(str(v or "—"))),
                     ("Basis", "basis", False, lambda v: escape(str(v or "—")))]
        rebuild = ""
        rb = cap.get("rebuild")
        if rb:
            rebuild = (f'<div class="callout"><b>{escape(rb["verdict"])}</b>'
                       + (f' — the {num(rb["existing_units"])} standing units exceed today\'s '
                          f'by-right {num(rb["by_right_units"])}, so the site is legally '
                          f'non-conforming and a base-density rebuild loses units.'
                          if rb.get("non_conforming") else "") + "</div>")
        parts.append(f"""
<section><h2>Redevelopment capacity</h2>
<div class="sub">What the site allows once assembled</div>
<div class="kv">
  <div><b>{escape(str(z.get('zone') or '—'))}</b><span>Zoning{(' · ' + escape(z['municipality'])) if z.get('municipality') else ''}</span></div>
  <div><b>{num(z.get('density_per_acre'))}</b><span>Base density (u/ac)</span></div>
  <div><b>{(str(z['far']) + '×') if z.get('far') else '—'}</b><span>Max FAR</span></div>
  <div><b>{num(z.get('max_height_stories'))}</b><span>Max stories</span></div>
</div>
{rebuild}
{_table(cap.get('scenarios') or [], scen_cols)}
<p class="src">{escape(str(cap.get('caveat') or ''))}</p>
</section>""")

    # ── market context ─────────────────────────────────────────────────────
    m = doc.get("market")
    if m:
        parts.append(f"""
<section><h2>Market context</h2>
<div class="sub">{escape(m.get('name') or '')}</div>
<div class="kv">
  <div><b>{num(m.get('population'))}</b><span>Population</span></div>
  <div><b>{'' if m.get('net_mig_total_rate') is None else f"{m['net_mig_total_rate']:+.1f}"}</b><span>Net migration / 1k</span></div>
  <div><b>{money(m.get('inflow_agi_per_return'))}</b><span>Arrivals' avg AGI</span></div>
  <div><b>{money(m.get('zhvi'))}</b><span>Median home value</span></div>
  <div><b>{'' if m.get('zhvi_3yr') is None else f"{m['zhvi_3yr']:+.0f}%"}</b><span>3-yr price change</span></div>
  <div><b>{'' if m.get('permits_per_1k') is None else f"{m['permits_per_1k']:.1f}"}</b><span>Permits / 1k</span></div>
</div>
<p class="src">Sources: Census population estimates and Building Permits Survey;
  IRS SOI county-to-county migration with AGI; Zillow ZHVI; BLS QCEW.</p>
</section>""")

    # ── sources ────────────────────────────────────────────────────────────
    parts.append(f"""
<section><h2>Sources</h2>
<div class="sub">Everything above is traceable to a public record</div>
<table><tbody>
<tr><td><b>Units, owners, assessed values, recorded sales</b></td>
    <td>Florida Department of Revenue NAL tax roll, {CFG['county']} County,
        {CFG['roll_year']} {CFG['roll_type']}</td></tr>
<tr><td><b>Declaration recording date, association, unit count</b></td>
    <td>Florida DBPR condominium registry</td></tr>
<tr><td><b>Zoning envelope, FAR, density, height</b></td>
    <td>{CFG['county']} County municipal zoning layer</td></tr>
<tr><td><b>Metro demographics and pricing</b></td>
    <td>US Census, IRS SOI, Zillow Research, BLS QCEW</td></tr>
<tr><td><b>Termination threshold and Kaufman finding</b></td>
    <td>{'Recorded declaration, reviewed' if t.get('stage2_verified') else 'Not yet pulled — stage 2 outstanding'}</td></tr>
</tbody></table>
</section>

<div class="foot">
{escape(name)} · Condominium acquisition memorandum · generated {doc['generated']} by Meridian.<br>
{escape(CONFIDENTIALITY)}
Assembled from public records; figures are subject to verification and do not constitute
an offer, a commitment, a valuation, or legal or investment advice.
</div>
</div>""")

    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(name)} — Acquisition Memorandum</title>'
            f"<style>{CSS}</style></head><body>"
            f'<button class="noprint" onclick="window.print()">Print / Save as PDF</button>'
            f'<div class="sheet">{"".join(parts)}</div></body></html>')


def generate(group_key: str, con, radius_mi: float = 2.0,
             lot_sf: float | None = None, write: bool = False):
    doc = gather(group_key, con, radius_mi=radius_mi, lot_sf=lot_sf)
    html = render(doc)
    path = None
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = "".join(c for c in (doc["target"].get("condo_name") or group_key)
                       if c.isalnum() or c in " -_").strip().replace(" ", "-")[:48]
        path = OUT_DIR / f"{slug or group_key}_{date.today():%Y%m%d}.html"
        path.write_text(html, encoding="utf-8")
    return html, doc, path
