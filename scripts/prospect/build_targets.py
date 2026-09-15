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


def county_config(name: str | None = None) -> dict:
    """The active county's settings. `county` in config.json picks it; everything
    that used to be a Miami-Dade constant lives under config.counties."""
    key = (name or CFG.get("county") or "DADE").upper()
    counties = CFG.get("counties") or {}
    if key not in counties:
        # Accept the display name too: config's `county` is "Miami-Dade".
        key = next((k for k, v in counties.items()
                    if isinstance(v, dict) and v.get("dbpr_county", "").upper() == key), key)
    if key not in counties:
        raise SystemExit(
            f"county {key!r} is not in config.counties. Add it there — the NAL and "
            f"DBPR files are statewide, so a new county is an entry plus a zoning "
            f"layer, not new code.")
    return counties[key]

# Owners that are the association itself rather than an acquirer.
ASSOC_RE = re.compile(r"\b(ASSOC|ASSN|ASSOCIATION|HOA|HOMEOWNERS?|CONDOMINIUM)\b")

# Positional inserts, so these must track the schema. Asserted against the live
# tables in main() rather than trusted -- a silent drift here is the "table has N
# columns but M values were supplied" that only shows up mid-load.
GROUP_COLS = 36
TARGET_COLS = 53
SNAPSHOT_COLS = 15


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
    Counties publish X/Y in their own State Plane zone (feet); the map wants
    WGS84. Averaged across the site's parcels so a sprawling complex lands in its
    middle rather than on one corner lot. The EPSG and the sanity bbox come from
    config.counties, because they are the two things that were hardcoded to
    Miami-Dade and would silently put another county's buildings in the sea."""
    from pyproj import Transformer
    county = county_config()
    tf = Transformer.from_crs(county["state_plane"], 4326, always_xy=True)
    x0, y0, x1, y1 = county["bbox"]
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
            if x0 < lon < x1 and y0 < lat < y1:
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
         "sale_yr1, is_entity, is_absentee, homestead FROM nal_condo_unit")
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
        # Counted over units the roll could actually answer for. A building whose
        # roll carries no exemption column reports None, not 0% homesteaded --
        # "we could not tell" must not read as "nobody objects".
        hs_known = [u for u in us if u["homestead"] is not None]
        hs_units = sum(1 for u in hs_known if u["homestead"])
        hs_pct = pct(hs_units, len(hs_known)) if hs_known else None

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
            hs_units if hs_known else None, hs_pct,
        ))

    con.executemany(
        f"INSERT INTO condo_group VALUES ({','.join('?' * GROUP_COLS)})", rows)
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


# ── movement ───────────────────────────────────────────────────────────────

def prior_snapshot(con, roll_year):
    """The most recent snapshot STRICTLY BEFORE this roll year, per building.

    Strictly before, so re-running the build for the same roll compares against
    the previous vintage rather than against itself and reporting no movement.
    """
    rows = con.execute(
        """SELECT s.* FROM target_snapshot s
           JOIN (SELECT group_key, MAX(roll_year) y FROM target_snapshot
                 WHERE roll_year < ? GROUP BY group_key) m
             ON m.group_key = s.group_key AND m.y = s.roll_year""",
        (roll_year,)).fetchall()
    return {r["group_key"]: r for r in rows}


def movement(g, prior):
    """Change against the previous roll. All None when there is nothing to
    compare against -- which is not the same as no change, and the UI must not
    render it as zero."""
    if not prior:
        return (None, None, None, None, None, None)
    now_conc = max(g["top_owner_pct"] or 0, g["top_mail_pct"] or 0)
    was_conc = max(prior["top_owner_pct"] or 0, prior["top_mail_pct"] or 0)
    conc_delta = round(now_conc - was_conc, 2)
    owners_delta = ((g["distinct_owners"] or 0) - (prior["distinct_owners"] or 0))
    corp_delta = round((g["corporate_pct"] or 0) - (prior["corporate_pct"] or 0), 2)
    changed = int((g["top_owner"] or "") != (prior["top_owner"] or ""))
    # Concentration rising while the owner count falls. Either alone is noise --
    # one sale moves concentration, and owner counts drift with data cleanup --
    # but together they are somebody buying the building.
    flag = int(conc_delta >= 2.0 and owners_delta < 0)
    return (prior["roll_year"], conc_delta, owners_delta, corp_delta, changed, flag)


def write_snapshot(con, roll_year, roll_type):
    """One row per building for this vintage. REPLACE so a re-run of the same
    roll corrects itself rather than failing on the primary key."""
    captured = datetime.now().date().isoformat()
    rows = [(g["group_key"], roll_year, roll_type, captured, g["unit_folios"],
             g["top_owner"], g["top_owner_units"], g["top_owner_pct"],
             g["top_mail_addr"], g["top_mail_pct"], g["distinct_owners"],
             g["corporate_pct"], g["absentee_pct"], g["entity_sales_last_3yr"],
             g["homestead_pct"])
            for g in con.execute("SELECT * FROM condo_group")]
    con.executemany(
        f"INSERT OR REPLACE INTO target_snapshot VALUES ({','.join('?' * SNAPSHOT_COLS)})", rows)
    con.commit()
    return len(rows)


def recert_map(con):
    """group_key -> (status, due_date, unsafe_case). Worst row wins where a
    building has several: an open case on one folio of a complex is an open case
    for the building, and averaging it away would be the wrong answer."""
    out = {}
    for r in con.execute(
            "SELECT group_key, status, due_date, unsafe_case FROM building_recert "
            "WHERE group_key IS NOT NULL ORDER BY unsafe_case DESC"):
        out.setdefault(r["group_key"], (r["status"], r["due_date"], r["unsafe_case"]))
    return out


def distress(age, recert):
    """0-100. Structural distress is the post-Surfside motivation to sell, and it
    is a FACT where recert data exists -- so where it does not, this returns None
    rather than falling back to age, which is the guess it replaces."""
    if recert is None:
        return None
    _status, _due, unsafe = recert
    base = 55.0 if unsafe else 10.0
    # Age still matters inside the distressed set: an open case on a 60-year-old
    # building is a bigger assessment than on a 30-year-old one.
    if age:
        base += min(45.0, max(0.0, (age - 30) * 1.5))
    return round(min(100.0, base), 1)


def restore_declarations(con):
    """Put reviewed declaration findings back after the rebuild.

    build_targets drops and rewrites `target`, so a rebuild would otherwise erase
    every stage-2 finding an analyst entered -- the most expensive data in the
    app, since each one cost somebody reading a recorded instrument.
    declaration_doc is never touched by a rebuild, so the synthesis is recomputed
    from it rather than preserved, which also picks up any change to the
    precedence rules since the last run.
    """
    from backend.prospect.declaration import synthesise
    keys = [r[0] for r in con.execute(
        "SELECT DISTINCT group_key FROM declaration_doc")]
    restored = 0
    for key in keys:
        docs = [dict(r) for r in con.execute(
            "SELECT * FROM declaration_doc WHERE group_key=? ORDER BY recorded_year, id",
            (key,))]
        syn = synthesise(docs)
        if not syn:
            continue
        cur = con.execute(
            """UPDATE target SET termination_threshold=?, kaufman_original=?,
               kaufman_by_amendment=?, rofr=?, leasehold=?, age_restricted=?,
               declaration_docs=? WHERE group_key=?""",
            (syn["termination_threshold"], syn["kaufman_original"],
             syn["kaufman_by_amendment"], syn["rofr"], syn["leasehold"],
             syn["age_restricted"], syn["declaration_docs"], key))
        restored += cur.rowcount
    con.commit()
    return restored


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


def score_all(con, matches, prior=None):
    print("pass 3: scoring ...")
    prior = prior or {}
    recerts = recert_map(con)
    if recerts:
        print(f"  recertification status for {len(recerts):,} building(s)")
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
        # Resistance to termination: the homesteaded owner-occupant is the one
        # who objects under the 5% rule and the one whose payout floor is
        # protected. Stored and sortable, but NOT folded into `score` -- adding a
        # fifth term without re-deriving the weights would silently move every
        # building in the app. That is Phase 1.4, and it wants the labelled set
        # of actual terminations first.
        s_resist = (round(g["homestead_pct"], 1)
                    if g["homestead_pct"] is not None else None)
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
            g["homestead_pct"], s_resist,
            *movement(g, prior.get(key)),
            # rofr, leasehold, age_restricted, declaration_docs — owned by
            # declaration review, not by the build. Written null here because the
            # whole table is rebuilt, then restored by restore_declarations()
            # below from declaration_doc, which a rebuild never touches.
            None, None, None, None,
            *( (lambda rc: (rc[0] if rc else None, rc[1] if rc else None,
                            rc[2] if rc else None, distress(age, rc))
                )(recerts.get(key)) ),
        ))

    con.executemany(f"INSERT INTO target VALUES ({','.join('?' * TARGET_COLS)})", rows)
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

    for table, expect in (("condo_group", GROUP_COLS), ("target", TARGET_COLS),
                          ("target_snapshot", SNAPSHOT_COLS)):
        actual = len(con.execute(f"PRAGMA main.table_info({table})").fetchall())
        if actual != expect:
            raise SystemExit(
                f"{table} has {actual} columns but this script writes {expect}. "
                f"Update the constant and the row tuple together.")

    roll_year = CFG["roll_year"]
    # Read the previous vintage BEFORE this run writes its own snapshot.
    prior = prior_snapshot(con, roll_year)
    print(f"prior vintage: {len(prior):,} buildings to compare against")

    build_groups(con)
    matches = match(con)
    n = score_all(con, matches, prior)
    snaps = write_snapshot(con, roll_year, CFG.get("roll_type", ""))
    print(f"snapshot: {snaps:,} rows written for roll {roll_year}")
    restored = restore_declarations(con)
    if restored:
        print(f"declaration review restored on {restored:,} building(s)")
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
