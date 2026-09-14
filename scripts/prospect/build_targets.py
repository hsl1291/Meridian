"""Rebuild condo_group and target from the raw tables.

Three passes:

  1. GROUP    391,210 NAL condo units -> ~6,160 condos, keyed on the 9-digit
              folio prefix that units of one condo share. Computes the ownership
              concentration and absentee/investor shares.

  2. MATCH    condo_group <-> dbpr_association, on building address. The tax
              roll's S_LEGAL is abbreviated and carries no condo name, so name
              matching is not available on the NAL side; address is the key and
              unit-count agreement is the confidence signal. Unmatched groups are
              KEPT (match_method='unmatched') rather than dropped -- a condo the
              DBPR registry spells differently is still a real building.

  3. SCORE    Stage-1 ranking. Age, scale, ownership concentration, absentee
              share -- weights in config.json.

Idempotent: drops and rebuilds both derived tables every run.

    venv\\Scripts\\python.exe scripts\\prospect\\build_targets.py
"""
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.prospect.db import connect, log_ingest  # noqa: E402
from backend.prospect.norm import condo_name_from_legal, normalize_name  # noqa: E402

# Windows consoles default to cp1252 and raise on the box-drawing characters in
# the summary output; the data is committed by then, so the crash is cosmetic.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CFG = json.loads((ROOT / "backend" / "prospect" / "config.json").read_text(encoding="utf-8"))
THIS_YEAR = datetime.now().year

# Owners that are the association itself rather than an acquirer.
ASSOC_RE = re.compile(r"\b(ASSOC|ASSN|ASSOCIATION|HOA|HOMEOWNERS?|CONDOMINIUM)\b")


def modal(values):
    vals = [v for v in values if v not in (None, "", 0)]
    return Counter(vals).most_common(1)[0][0] if vals else None


def pct(part, whole):
    return round(100.0 * part / whole, 2) if whole else 0.0


# ── pass 1: group ──────────────────────────────────────────────────────────
def legal_names(con):
    """group_key -> condo name, recovered from the LAND parcel legals that share
    the group's 9-digit folio prefix. Only legals that actually name a condo vote,
    so unrelated lots in the same plat block (rec areas, city parks) can't win."""
    votes = defaultdict(Counter)
    q = ("SELECT p.subdivision AS k, p.legal FROM pa_parcel p "
         "WHERE p.legal LIKE '%CONDO%' AND p.subdivision IN "
         "(SELECT group_key FROM nal_condo_unit)")
    for r in con.execute(q):
        nm = condo_name_from_legal(r["legal"])
        if nm:
            votes[r["k"]][nm] += 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


def site_coords(con):
    """group_key -> (lon, lat), reprojected from the site's land parcels.
    The county publishes X/Y in State Plane Florida East (EPSG:2236, feet);
    Sitefolio wants WGS84. Averaged across the site's parcels so a sprawling
    complex lands in its middle rather than on one corner lot."""
    from pyproj import Transformer
    tf = Transformer.from_crs(2236, 4326, always_xy=True)
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    q = ("SELECT subdivision AS k, x_coord, y_coord FROM pa_parcel "
         "WHERE x_coord IS NOT NULL AND y_coord IS NOT NULL "
         "AND subdivision IN (SELECT group_key FROM nal_condo_unit)")
    for r in con.execute(q):
        a = acc[r["k"]]
        a[0] += r["x_coord"]
        a[1] += r["y_coord"]
        a[2] += 1
    out = {}
    for k, (sx, sy, n) in acc.items():
        if n:
            lon, lat = tf.transform(sx / n, sy / n)
            # Guard against bad source coords landing outside the county.
            if -81.0 < lon < -80.0 and 25.0 < lat < 26.1:
                out[k] = (round(lon, 6), round(lat, 6))
    return out


def build_groups(con):
    print("pass 1: grouping condo units ...")
    con.execute("DELETE FROM condo_group")
    names = legal_names(con)
    coords = site_coords(con)
    print(f"  site coordinates resolved: {len(coords):,}")
    print(f"  condo names recovered from land-parcel legals: {len(names):,}")
    units = defaultdict(list)
    q = ("SELECT group_key, owner_name, owner_norm, owner_addr_norm, owner_state, "
         "phy_addr_norm, phy_city, phy_zip, act_yr_blt, jv, lnd_val, tot_lvg_area, "
         "sale_yr1, is_entity, is_absentee FROM nal_condo_unit")
    for r in con.execute(q):
        units[r["group_key"]].append(r)

    rows = []
    for key, us in units.items():
        n = len(us)
        addrs = Counter(u["phy_addr_norm"] for u in us if u["phy_addr_norm"])
        distinct_addrs = sorted(addrs)

        owner_counts = Counter(u["owner_norm"] for u in us if u["owner_norm"])
        assoc_units = sum(c for o, c in owner_counts.items() if ASSOC_RE.search(o))
        acquirers = Counter({o: c for o, c in owner_counts.items() if not ASSOC_RE.search(o)})
        top_owner, top_owner_units = (acquirers.most_common(1)[0] if acquirers else (None, 0))

        # Same mailing address across different owner names = one buyer behind
        # several LLCs. Association mail is excluded for the same reason as above.
        mail_counts = Counter(
            u["owner_addr_norm"] for u in us
            if u["owner_addr_norm"] and not ASSOC_RE.search(u["owner_norm"] or "")
        )
        top_mail, top_mail_units = (mail_counts.most_common(1)[0] if mail_counts else (None, 0))

        jvs = [u["jv"] for u in us if u["jv"]]
        lnds = [u["lnd_val"] for u in us if u["lnd_val"]]
        areas = [u["tot_lvg_area"] for u in us if u["tot_lvg_area"]]
        absentee = sum(1 for u in us if u["is_absentee"])
        oos = sum(1 for u in us if (u["owner_state"] or "FL").upper() != "FL")
        corp = sum(1 for u in us if u["is_entity"])
        recent = [u for u in us if u["sale_yr1"] and u["sale_yr1"] >= THIS_YEAR - 3]

        nm = names.get(key, "")
        rows.append((
            key, n, nm or None, normalize_name(nm) or None,
            modal(u["phy_addr_norm"] for u in us),
            "|".join(distinct_addrs), len(distinct_addrs),
            modal(u["phy_city"] for u in us), modal(u["phy_zip"] for u in us),
            modal(u["act_yr_blt"] for u in us),
            round(sum(jvs), 2) if jvs else None,
            round(sum(jvs) / len(jvs), 2) if jvs else None,
            round(sum(lnds), 2) if lnds else None,
            round(sum(lnds) / len(lnds), 2) if lnds else None,
            round(sum(areas) / len(areas), 1) if areas else None,
            top_owner, top_owner_units, pct(top_owner_units, n),
            top_mail, top_mail_units, pct(top_mail_units, n),
            len(owner_counts),
            assoc_units, pct(assoc_units, n),
            absentee, pct(absentee, n),
            oos, pct(oos, n),
            corp, pct(corp, n),
            len(recent), sum(1 for u in recent if u["is_entity"]),
            *(coords.get(key) or (None, None)),
        ))

    con.executemany(
        f"INSERT INTO condo_group VALUES ({','.join('?' * 34)})", rows)
    con.commit()
    print(f"  {len(rows):,} condo groups")
    return len(rows)


# ── pass 2: match ──────────────────────────────────────────────────────────
def match(con):
    """Match condo groups to DBPR associations. Name first (the stronger key),
    address second. DBPR's address field is often the management company's rather
    than the building's, which is why address alone left 61% unmatched."""
    print("pass 2: matching to the DBPR registry ...")
    by_name, by_addr, by_name_all = defaultdict(list), defaultdict(list), []
    for r in con.execute("SELECT project_number, name, name_norm, addr_norm, units, "
                         "recorded_year FROM dbpr_association"):
        if r["name_norm"]:
            by_name[r["name_norm"]].append(r)
            by_name_all.append(r)
        if r["addr_norm"]:
            by_addr[r["addr_norm"]].append(r)

    out, stats = {}, Counter()
    q = ("SELECT group_key, name_norm, addr_primary, addr_all, unit_folios "
         "FROM condo_group")
    for g in con.execute(q):
        n = g["unit_folios"] or 0

        def closeness(c):
            u = c["units"] or 0
            return abs(u - n) / max(u, n, 1)

        def pick(cands, key, base):
            cands = sorted(cands, key=closeness)
            best = cands[0]
            gap = closeness(best)
            if gap <= 0.15:
                return best, f"{key}+units", round(base - gap, 3)
            if len(cands) == 1:
                return best, key, round(base - 0.15, 3)
            return best, f"{key} (ambiguous)", round(base - 0.35, 3)

        def dedupe(cands):
            seen, uniq = set(), []
            for c in cands:
                if c["project_number"] not in seen:
                    seen.add(c["project_number"])
                    uniq.append(c)
            return uniq

        chosen = None
        if g["name_norm"] and by_name.get(g["name_norm"]):
            chosen = pick(by_name[g["name_norm"]], "name", 0.98)
        if not chosen:
            cands = []
            for a in (g["addr_all"] or "").split("|"):
                if a:
                    cands.extend(by_addr.get(a, []))
            if cands:
                chosen = pick(dedupe(cands), "address", 0.90)
        if not chosen and g["name_norm"] and n:
            # Last resort: one name's tokens fully contain the other's AND the
            # unit counts agree closely. Both conditions are required -- token
            # overlap alone matches "SHOMA" to "SHOMA HOMES AT COUNTRY CLUB"
            # (575 units vs 111), which is a different property.
            toks = set(g["name_norm"].split())
            near = [c for c in by_name_all
                    if c["name_norm"] and closeness(c) <= 0.10
                    and (toks <= set(c["name_norm"].split())
                         or set(c["name_norm"].split()) <= toks)]
            if near:
                chosen = pick(dedupe(near), "name-subset", 0.70)

        if not chosen:
            stats["unmatched"] += 1
            out[g["group_key"]] = (None, "unmatched", 0.0, None, None, None)
            continue
        best, method, conf = chosen
        stats[method] += 1
        out[g["group_key"]] = (best["project_number"], method, conf,
                               best["name"], best["units"], best["recorded_year"])

    total = sum(stats.values())
    for k, v in stats.most_common():
        print(f"  {k:<24} {v:>5,}  ({pct(v, total):.1f}%)")
    return out


# ── pass 3: score ──────────────────────────────────────────────────────────
def curve(value, lo, hi):
    """Linear 0-100 between lo and hi."""
    if value is None:
        return 0.0
    return round(max(0.0, min(100.0, (value - lo) / (hi - lo) * 100)), 1)


def log_curve(value, lo, hi):
    """Log-scaled 0-100 between lo and hi -- for unit counts, where 20 vs 40
    units matters more than 400 vs 420."""
    if not value or value <= lo:
        return 0.0
    if value >= hi:
        return 100.0
    return round(math.log(value / lo) / math.log(hi / lo) * 100, 1)


def score_all(con, matches):
    print("pass 3: scoring ...")
    con.execute("DELETE FROM target")
    w = CFG["score_weights"]
    ac, sc, cc = CFG["age_curve"], CFG["scale_curve"], CFG["concentration_curve"]
    milestone_age = CFG["milestone_age_years"]

    rows = []
    for g in con.execute("SELECT * FROM condo_group"):
        key = g["group_key"]
        proj, method, conf, dbpr_name, units_dbpr, rec_year = matches[key]

        units_nal = g["unit_folios"] or 0
        yb = g["act_yr_blt"]
        # Building age drives structural obsolescence and the milestone clock.
        # Declaration year is the fallback and is what governs the threshold.
        basis = yb or rec_year
        age = (THIS_YEAR - basis) if basis else None

        conc_pct = max(g["top_owner_pct"] or 0, g["top_mail_pct"] or 0)
        s_age = curve(age, ac["min_years"], ac["max_years"])
        s_scale = log_curve(units_nal, sc["min_units"], sc["max_units"])
        s_conc = curve(conc_pct, 0, cc["full_score_pct"])
        s_abs = round(min(100.0, 0.5 * (g["absentee_pct"] or 0)
                          + 0.3 * (g["corporate_pct"] or 0)
                          + 0.2 * (g["out_of_state_pct"] or 0)), 1)
        total = round(w["age"] * s_age + w["scale"] * s_scale
                      + w["concentration"] * s_conc + w["absentee"] * s_abs, 2)

        rows.append((
            key, proj, method, conf,
            dbpr_name or g["addr_primary"], g["addr_primary"], g["city"],
            units_dbpr, units_nal, rec_year, yb, age,
            1 if (age is not None and age >= milestone_age) else 0,
            g["jv_per_unit"], g["lnd_val_per_unit"],
            g["top_owner"], g["top_owner_pct"], g["top_mail_pct"],
            g["absentee_pct"], g["out_of_state_pct"], g["corporate_pct"],
            g["entity_sales_last_3yr"],
            total, s_age, s_scale, s_conc, s_abs,
            None, None, None, None, None, None, 0, None,
            g["lon"], g["lat"],
        ))

    con.executemany(f"INSERT INTO target VALUES ({','.join('?' * 37)})", rows)
    con.commit()
    print(f"  {len(rows):,} targets scored")
    return len(rows)


def main():
    started = datetime.now().isoformat(timespec="seconds")
    con = connect()
    if not con.execute("SELECT COUNT(*) FROM nal_condo_unit").fetchone()[0]:
        raise SystemExit("nal_condo_unit is empty -- run scripts/prospect/ingest_nal.py first")
    if not con.execute("SELECT COUNT(*) FROM dbpr_association").fetchone()[0]:
        raise SystemExit("dbpr_association is empty -- run scripts/prospect/ingest_dbpr.py first")

    build_groups(con)
    matches = match(con)
    n = score_all(con, matches)
    log_ingest(con, "build_targets", started,
               datetime.now().isoformat(timespec="seconds"), n, "derived rebuild")

    print("\n── top 15 by score ─────────────────────────────────────────────")
    q = """SELECT condo_name, city, units_nal, age_years, top_owner_pct, top_mail_pct,
                  absentee_pct, score, match_method
           FROM target ORDER BY score DESC LIMIT 15"""
    print(f"  {'building':<38}{'city':<15}{'un':>5}{'age':>5}{'own%':>6}"
          f"{'mail%':>7}{'abs%':>6}{'score':>7}")
    for r in con.execute(q):
        print(f"  {(r['condo_name'] or '')[:37]:<38}{(r['city'] or '')[:14]:<15}"
              f"{r['units_nal']:>5}{r['age_years'] or 0:>5}{r['top_owner_pct']:>6.1f}"
              f"{r['top_mail_pct']:>7.1f}{r['absentee_pct']:>6.1f}{r['score']:>7.1f}")
    con.close()


if __name__ == "__main__":
    main()
