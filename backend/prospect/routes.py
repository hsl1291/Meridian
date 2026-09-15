"""HTTP surface for the acquisitions modules.

Mounted into the main app as a router rather than its own FastAPI instance, so
Groundwork is one process on one port. Two route names had to move when the
apps merged — the map side already owned ``/api/cities`` (zoning-coverage
cities) and ``/api/markets`` (data-coverage map dots), which mean something
different here:

    condo city list   /api/cities        ->  /api/condo/cities
    metro screener    /api/markets       ->  /api/metros
                      /api/market/{cbsa} ->  /api/metro/{cbsa}
                      /api/market-stats  ->  /api/metro-stats
    xlsx export       /api/export.xlsx   ->  /api/targets.xlsx

Everything else keeps the path it had.
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import capacity as capacity_mod
from .db import connect, connect_query

router = APIRouter()

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent.parent
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def db() -> sqlite3.Connection:
    # connect_query, not a bare sqlite3.connect: the national market tables and
    # the parcel/owner roll live in the shared store and need it attached.
    return connect_query()


# ═══ MODULE A — condo termination targets ══════════════════════════════════

@router.get("/api/condo/stats")
def condo_stats():
    con = db()
    try:
        g = con.execute("SELECT COUNT(*) n, SUM(units_nal) u FROM target").fetchone()
        matched = con.execute(
            "SELECT COUNT(*) FROM target WHERE match_method<>'unmatched'").fetchone()[0]
        shortlist = con.execute(
            "SELECT COUNT(*) FROM target WHERE units_nal>=? AND age_years>=?",
            (CFG["shortlist"]["min_units"], CFG["shortlist"]["min_age_years"])).fetchone()[0]
        conc = con.execute(
            "SELECT COUNT(*) FROM target WHERE top_owner_pct>=20").fetchone()[0]
        verified = con.execute(
            "SELECT COUNT(*) FROM target WHERE stage2_verified=1").fetchone()[0]
        ing = [dict(r) for r in con.execute(
            "SELECT source, finished, rows FROM ingest_log ORDER BY id DESC LIMIT 6")]
        return {
            "condos": g["n"], "units": g["u"], "matched": matched,
            "shortlist": shortlist, "concentrated_20pct": conc,
            "stage2_verified": verified,
            "roll": f"{CFG['roll_year']} {CFG['roll_type']}",
            "roll_year": CFG["roll_year"], "roll_type": CFG["roll_type"],
            "county": CFG["county"], "ingests": ing,
            # A score means nothing without the vintage it was computed from, and
            # "preliminary" is a materially different claim from a certified roll.
            "provenance": (f"{CFG['county']} · {CFG['roll_year']} {CFG['roll_type']} roll"
                           + (f" · last built {ing[0]['finished'][:10]}" if ing else "")),
        }
    finally:
        con.close()


SORTS = {
    "score": "score DESC", "units": "units_nal DESC", "age": "age_years DESC",
    "concentration": "top_owner_pct DESC", "mail": "top_mail_pct DESC",
    "absentee": "absentee_pct DESC", "name": "condo_name ASC",
    "value": "jv_per_unit ASC",
    # Movement, not level. NULLS LAST so buildings with no prior vintage -- which
    # is "no comparison available", not "no change" -- sort below real movement
    # instead of above it.
    "assembling": "conc_delta DESC NULLS LAST",
    "consolidating": "owners_delta ASC NULLS LAST",
}


def _target_filter(q: str, city: str, min_units: int, max_units: int,
                   min_age: int, min_score: float, min_conc: float,
                   milestone_only: bool, matched_only: bool,
                   unverified_only: bool, assembling_only: bool = False) -> tuple[str, list]:
    where, params = ["units_nal BETWEEN ? AND ?"], [min_units, max_units]
    if q:
        where.append("(condo_name LIKE ? OR addr_primary LIKE ? OR top_owner LIKE ?)")
        params += [f"%{q}%"] * 3
    if city:
        where.append("city = ?")
        params.append(city)
    if min_age:
        where.append("age_years >= ?")
        params.append(min_age)
    if min_score:
        where.append("score >= ?")
        params.append(min_score)
    if min_conc:
        where.append("MAX(COALESCE(top_owner_pct,0), COALESCE(top_mail_pct,0)) >= ?")
        params.append(min_conc)
    if milestone_only:
        where.append("milestone_due = 1")
    if matched_only:
        where.append("match_method <> 'unmatched'")
    if unverified_only:
        where.append("stage2_verified = 0")
    if assembling_only:
        where.append("assembly_flag = 1")
    return " AND ".join(where), params


@router.get("/api/targets")
def targets(
    q: str = "", city: str = "", limit: int = 100, offset: int = 0,
    min_units: int = 0, max_units: int = 100000,
    min_age: int = 0, min_score: float = 0, min_conc: float = 0,
    milestone_only: bool = False, matched_only: bool = False,
    unverified_only: bool = False, assembling_only: bool = False,
    sort: str = "score",
):
    clause, params = _target_filter(q, city, min_units, max_units, min_age,
                                    min_score, min_conc, milestone_only,
                                    matched_only, unverified_only, assembling_only)
    order = SORTS.get(sort, SORTS["score"])
    con = db()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM target WHERE {clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM target WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [min(limit, 1000), offset]).fetchall()
        return {"total": total, "rows": [dict(r) for r in rows]}
    finally:
        con.close()


@router.get("/api/targets.geojson")
def targets_geojson(
    q: str = "", city: str = "", cap: int = Query(4000, le=8000),
    min_units: int = 0, max_units: int = 100000,
    min_age: int = 0, min_score: float = 0, min_conc: float = 0,
    milestone_only: bool = False, matched_only: bool = False,
    unverified_only: bool = False, assembling_only: bool = False,
):
    """Every target matching the current filters, as map points.

    The table pages 100 at a time; the map is supposed to show the whole result
    set at once, which is the entire point of having them side by side. 6,160
    points is small enough to hand over in one go.
    """
    clause, params = _target_filter(q, city, min_units, max_units, min_age,
                                    min_score, min_conc, milestone_only,
                                    matched_only, unverified_only, assembling_only)
    con = db()
    try:
        rows = con.execute(
            "SELECT group_key, condo_name, addr_primary, city, units_nal, "
            "age_years, act_yr_blt, top_owner_pct, jv_per_unit, score, "
            "milestone_due, stage2_verified, lon, lat "
            f"FROM target WHERE {clause} AND lon IS NOT NULL "
            "ORDER BY score DESC LIMIT ?", params + [cap]).fetchall()
    finally:
        con.close()
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {k: r[k] for k in r.keys() if k not in ("lon", "lat")},
    } for r in rows]
    return {"type": "FeatureCollection", "features": feats, "count": len(feats)}


@router.get("/api/condo/cities")
def condo_cities():
    con = db()
    try:
        return [dict(r) for r in con.execute(
            "SELECT city, COUNT(*) n FROM target WHERE city IS NOT NULL "
            "GROUP BY city ORDER BY n DESC")]
    finally:
        con.close()


@router.get("/api/target/{group_key}")
def target_detail(group_key: str):
    con = db()
    try:
        t = con.execute("SELECT * FROM target WHERE group_key=?", (group_key,)).fetchone()
        if not t:
            raise HTTPException(404, "no such condo group")
        g = con.execute("SELECT * FROM condo_group WHERE group_key=?", (group_key,)).fetchone()
        owners = [dict(r) for r in con.execute(
            "SELECT owner_name, owner_norm, owner_addr1, owner_city, owner_state, "
            "COUNT(*) units, SUM(jv) jv, MAX(is_entity) is_entity "
            "FROM nal_condo_unit WHERE group_key=? GROUP BY owner_norm "
            "ORDER BY units DESC LIMIT 25", (group_key,))]
        mail = [dict(r) for r in con.execute(
            "SELECT owner_addr_norm, COUNT(*) units, COUNT(DISTINCT owner_norm) names "
            "FROM nal_condo_unit WHERE group_key=? AND owner_addr_norm<>'' "
            "GROUP BY owner_addr_norm HAVING units>1 ORDER BY units DESC LIMIT 15",
            (group_key,))]
        recent = [dict(r) for r in con.execute(
            "SELECT folio, owner_name, sale_yr1, sale_prc1, or_book1, or_page1, is_entity "
            "FROM nal_condo_unit WHERE group_key=? AND sale_yr1 IS NOT NULL "
            "ORDER BY sale_yr1 DESC, sale_prc1 DESC LIMIT 25", (group_key,))]
        dbpr = None
        if t["project_number"]:
            r = con.execute("SELECT * FROM dbpr_association WHERE project_number=?",
                            (t["project_number"],)).fetchone()
            dbpr = dict(r) if r else None
        return {"target": dict(t), "group": dict(g) if g else None,
                "owners": owners, "shared_mailing": mail, "recent_sales": recent,
                "dbpr": dbpr}
    finally:
        con.close()


class Stage2(BaseModel):
    termination_threshold: str | None = None
    kaufman_original: bool | None = None
    kaufman_by_amendment: bool | None = None
    declaration_or_book: str | None = None
    declaration_or_page: str | None = None
    declaration_source_url: str | None = None
    stage2_verified: bool = False
    stage2_notes: str | None = None


@router.post("/api/target/{group_key}/stage2")
def save_stage2(group_key: str, body: Stage2):
    con = connect()
    try:
        if not con.execute("SELECT 1 FROM target WHERE group_key=?", (group_key,)).fetchone():
            raise HTTPException(404, "no such condo group")
        con.execute(
            """UPDATE target SET termination_threshold=?, kaufman_original=?,
               kaufman_by_amendment=?, declaration_or_book=?, declaration_or_page=?,
               declaration_source_url=?, stage2_verified=?, stage2_notes=?
               WHERE group_key=?""",
            (body.termination_threshold,
             None if body.kaufman_original is None else int(body.kaufman_original),
             None if body.kaufman_by_amendment is None else int(body.kaufman_by_amendment),
             body.declaration_or_book, body.declaration_or_page,
             body.declaration_source_url, int(body.stage2_verified),
             body.stage2_notes, group_key))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


class DealState(BaseModel):
    stage: str
    note: str | None = None


@router.get("/api/target/{group_key}/deal")
def get_deal(group_key: str):
    con = db()
    try:
        row = con.execute("SELECT stage, updated FROM deal_state WHERE group_key=?",
                          (group_key,)).fetchone()
        notes = [dict(x) for x in con.execute(
            "SELECT id, created, body FROM deal_note WHERE group_key=? "
            "ORDER BY created DESC, id DESC LIMIT 50", (group_key,))]
        return {"group_key": group_key,
                "stage": row["stage"] if row else "screened",
                "updated": row["updated"] if row else None,
                "stages": CFG["pipeline"]["stages"], "notes": notes}
    finally:
        con.close()


@router.post("/api/target/{group_key}/deal")
def set_deal(group_key: str, body: DealState):
    """Move a building along, optionally with a note.

    Deal state lives outside `target` on purpose: a rebuild rewrites that table
    every time a new tax roll lands, and losing where a deal had got to because
    the data refreshed would be the worst bug in this app.
    """
    stages = CFG["pipeline"]["stages"]
    if body.stage not in stages:
        raise HTTPException(422, f"stage must be one of {stages}")
    con = connect()
    try:
        if not con.execute("SELECT 1 FROM target WHERE group_key=?", (group_key,)).fetchone():
            raise HTTPException(404, "no such condo group")
        con.execute(
            "INSERT INTO deal_state (group_key, stage, updated) VALUES (?,?,datetime('now')) "
            "ON CONFLICT(group_key) DO UPDATE SET stage=excluded.stage, updated=excluded.updated",
            (group_key, body.stage))
        if body.note and body.note.strip():
            con.execute("INSERT INTO deal_note (group_key, created, body) "
                        "VALUES (?, datetime('now'), ?)", (group_key, body.note.strip()))
        con.commit()
        return {"ok": True, "stage": body.stage}
    finally:
        con.close()


@router.get("/api/selftest")
def selftest():
    """Which datasets are actually present, and what builds each missing one.

    The app needs ~53MB of fetched layers plus a ~330MB shared store, and had no
    way to say which of them resolved. It also closes the gap a 503 cannot: a
    table that exists with zero rows is a screen that was BUILT and found
    nothing, which is a different fact from one that was never built, and the two
    are indistinguishable from the UI.
    """
    import sqlite3 as _sq

    from ..shared_paths import shared_db, shared_layers
    from .db import DB_PATH

    out = {"checks": [], "ok": True}

    def add(name, present, detail, fix=None, rows=None):
        out["checks"].append({"name": name, "present": bool(present), "detail": detail,
                              "rows": rows, "fix": fix})
        if not present:
            out["ok"] = False

    add("prospect database", DB_PATH.exists(), str(DB_PATH),
        "scripts/prospect/build_targets.py")
    sdb = shared_db()
    add("shared store", sdb.exists(),
        f"{str(sdb)}{f' ({sdb.stat().st_size / 1e6:.0f}MB)' if sdb.exists() else ''}",
        "set APPS_SHARED_DB, or APPS_SHARED to the folder holding shared.db")
    lay = shared_layers()
    n_layers = len(list(lay.glob("*.geojson"))) if lay.is_dir() else 0
    add("map layers", n_layers > 0, f"{n_layers} layer file(s) in {lay}",
        "scripts/fetch_layers.py", rows=n_layers)
    for fname, fix in (("zori_rents.json", "scripts/fetch_zori.py"),
                       ("zcta_pop.json", "scripts/fetch_zcta_population.py"),
                       ("cbsa.geojson", "scripts/fetch_cbsa_geo.py")):
        f = ROOT_DIR / "data" / fname
        add(fname, f.exists(), str(f), fix)

    # Row counts separate "built and empty" from "never built".
    tables = [("target", "scripts/prospect/build_targets.py"),
              ("nal_condo_unit", "scripts/prospect/ingest_nal.py"),
              ("dbpr_association", "scripts/prospect/ingest_dbpr.py"),
              ("market", "scripts/prospect/build_markets.py"),
              ("migration_flow", "scripts/prospect/ingest_market.py"),
              ("declaration_doc", "upload a declaration in the building drawer"),
              ("target_snapshot", "scripts/prospect/build_targets.py")]
    try:
        con = db()
        for t, fix in tables:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                add(f"table {t}", n > 0,
                    f"{n:,} rows" if n else "table exists but is empty — built and "
                                            "found nothing, or never populated",
                    fix, rows=n)
            except _sq.OperationalError:
                add(f"table {t}", False, "table does not exist", fix, rows=0)
        con.close()
    except _sq.OperationalError as exc:
        add("database connection", False, str(exc),
            "see the shared store check above")
    return out


# ═══ declaration review, in the app ════════════════════════════════════════
# Retrieval stays manual -- the Clerk's site is a client-rendered SPA with no
# documented query API, and a brittle scraper that breaks silently is worse than
# a human with a search box. The REVIEW being CLI-only was the part that made no
# sense: an analyst ran --worklist, got an xlsx, saved PDFs into a folder named
# by folio prefix, and ran --extract from a terminal.

DECL_DIR = ROOT_DIR / "data" / "declarations"
MAX_PDF_BYTES = 80 * 1024 * 1024


def _resynthesise(con, group_key: str) -> dict:
    """Recompute the operative terms from every document held for a building and
    write them onto the target row."""
    from .declaration import synthesise
    docs = [dict(r) for r in con.execute(
        "SELECT * FROM declaration_doc WHERE group_key=? ORDER BY recorded_year, id",
        (group_key,))]
    syn = synthesise(docs)
    if syn:
        con.execute(
            """UPDATE target SET termination_threshold=?, kaufman_original=?,
               kaufman_by_amendment=?, rofr=?, leasehold=?, age_restricted=?,
               declaration_docs=? WHERE group_key=?""",
            (syn["termination_threshold"], syn["kaufman_original"],
             syn["kaufman_by_amendment"], syn["rofr"], syn["leasehold"],
             syn["age_restricted"], syn["declaration_docs"], group_key))
        con.commit()
    return syn


@router.post("/api/target/{group_key}/declaration")
async def upload_declaration(group_key: str, file: UploadFile = File(...)):
    """Read a recorded declaration and file its findings against the building.

    Each document is stored whole. The target row carries the SYNTHESIS across
    them, because an amendment that drops the vote to 80% and an original that
    never had Kaufman language are two different facts and collapsing them loses
    the distinction the whole screen turns on.
    """
    from .declaration import analyze
    from .docs import OcrUnavailable, extract

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "Upload the recorded instrument as a PDF.")
    blob = await file.read()
    if len(blob) > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF is larger than {MAX_PDF_BYTES // (1024*1024)}MB.")

    con = connect()
    try:
        if not con.execute("SELECT 1 FROM target WHERE group_key=?", (group_key,)).fetchone():
            raise HTTPException(404, "no such condo group")
        DECL_DIR.mkdir(parents=True, exist_ok=True)
        # Named by folio prefix so the CLI path and the upload path share a
        # folder; suffixed when a building has several instruments.
        stem = "".join(c for c in group_key if c.isalnum())
        dest = DECL_DIR / f"{stem}.pdf"
        n = 2
        while dest.exists():
            dest = DECL_DIR / f"{stem}-{n}.pdf"
            n += 1
        dest.write_bytes(blob)

        try:
            text, source = extract(dest)
        except OcrUnavailable as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(503, str(exc))

        if len(text.strip()) < 200:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                422, "Almost no text came out of this PDF even after OCR. It may be "
                     "a cover page, a very poor scan, or an image at too low a "
                     "resolution to read.")

        f = analyze(text)
        row = f.as_doc_row()
        # OCR output is noisier; a high-confidence read off guessed characters is
        # not a high-confidence read.
        if source.endswith("ocr") and row["confidence"] == "high":
            row["confidence"] = "medium"
        rel = str(dest.relative_to(ROOT_DIR)).replace("\\", "/")
        con.execute(
            """INSERT OR REPLACE INTO declaration_doc
               (group_key, source, doc_type, termination_threshold, threshold_pct,
                kaufman_present, rofr, leasehold, age_restricted, text_source,
                confidence, notes, reviewed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,date('now'))""",
            (group_key, rel, row["doc_type"], row["termination_threshold"],
             row["threshold_pct"], row["kaufman_present"], row["rofr"],
             row["leasehold"], row["age_restricted"], source, row["confidence"],
             row["notes"]))
        con.commit()
        syn = _resynthesise(con, group_key)
        return {
            "ok": True, "stored": rel, "text_source": source,
            "characters": len(text),
            "document": row,
            "snippets": {
                "threshold": f.threshold_snippet,
                "kaufman": (f.kaufman_snippets or [None])[0],
                "rofr": f.rofr_snippet, "leasehold": f.leasehold_snippet,
                "age": f.age_snippet,
            },
            "warnings": f.warnings,
            "synthesis": syn,
        }
    finally:
        con.close()


@router.get("/api/target/{group_key}/declarations")
def list_declarations(group_key: str):
    """Every document reviewed for a building, with the operative synthesis."""
    from .declaration import synthesise
    con = db()
    try:
        docs = [dict(r) for r in con.execute(
            "SELECT * FROM declaration_doc WHERE group_key=? ORDER BY recorded_year, id",
            (group_key,))]
        return {"group_key": group_key, "documents": docs, "synthesis": synthesise(docs)}
    finally:
        con.close()


@router.delete("/api/target/{group_key}/declaration/{doc_id}")
def delete_declaration(group_key: str, doc_id: int):
    """Remove one misread document. The synthesis is recomputed without it —
    a bad OCR pass should not be permanent."""
    con = connect()
    try:
        cur = con.execute("DELETE FROM declaration_doc WHERE id=? AND group_key=?",
                          (doc_id, group_key))
        con.commit()
        if not cur.rowcount:
            raise HTTPException(404, "no such document for this building")
        return {"ok": True, "synthesis": _resynthesise(con, group_key)}
    finally:
        con.close()


EXPORT_COLS = [
    ("condo_name", "Condo"), ("addr_primary", "Address"), ("city", "City"),
    ("units_nal", "Units (roll)"), ("units_dbpr", "Units (DBPR)"),
    ("act_yr_blt", "Year built"), ("recorded_year", "Decl. recorded"),
    ("age_years", "Age"), ("milestone_due", "Milestone due"),
    ("top_owner", "Largest owner"), ("top_owner_pct", "Largest owner %"),
    ("top_mail_pct", "Shared-mailing %"), ("absentee_pct", "Absentee %"),
    ("corporate_pct", "Corporate %"), ("out_of_state_pct", "Out-of-state %"),
    ("entity_sales_last_3yr", "Entity buys 3yr"),
    ("jv_per_unit", "Just value/unit"), ("lnd_val_per_unit", "Land value/unit"),
    ("score", "Score"), ("termination_threshold", "Threshold"),
    ("kaufman_original", "Kaufman (original)"),
    ("kaufman_by_amendment", "Kaufman (by amendment)"),
    ("stage2_verified", "Stage 2 verified"), ("stage2_notes", "Notes"),
    ("match_method", "Match"), ("match_confidence", "Match conf."),
    ("group_key", "Folio prefix"),
]


@router.get("/api/targets.xlsx")
def export_xlsx(limit: int = 500, min_score: float = 0, min_units: int = 0):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    con = db()
    try:
        rows = con.execute(
            "SELECT * FROM target WHERE score>=? AND units_nal>=? "
            "ORDER BY score DESC LIMIT ?",
            (min_score, min_units, min(limit, 5000))).fetchall()
    finally:
        con.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Termination targets"
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3B57")
    for c, (_, label) in enumerate(EXPORT_COLS, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font, cell.fill = head, fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r, row in enumerate(rows, 2):
        for c, (key, _) in enumerate(EXPORT_COLS, 1):
            ws.cell(row=r, column=c, value=row[key])
    ws.freeze_panes = "A2"
    widths = {1: 34, 2: 26, 3: 16, 10: 34, 24: 40}
    for i, _ in enumerate(EXPORT_COLS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(i, 13)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"groundwork_targets_{datetime.now():%Y%m%d}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ═══ MODULE C — metro screener ═════════════════════════════════════════════

METRO_SORTS = {
    "score": "score DESC", "demand": "score_demand DESC",
    "income": "inflow_agi_per_return DESC", "population": "population DESC",
    "migration": "net_mig_total_rate DESC", "headroom": "score_headroom DESC",
    "affordability": "score_affordability DESC", "jobs": "score_jobs DESC",
    "price": "zhvi DESC", "permits": "permits_per_1k ASC", "name": "name ASC",
}


@router.get("/api/metros")
def metros(q: str = "", stage: str = "", state: str = "", min_pop: int = 0,
           metro_only: bool = True, sort: str = "score",
           limit: int = 100, offset: int = 0):
    where, params = ["population >= ?"], [min_pop]
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if state:
        where.append("states LIKE ?")
        params.append(f"%{state}%")
    if metro_only:
        where.append("cbsa_type LIKE 'Metro%'")
    clause = " AND ".join(where)
    con = db()
    try:
        total = con.execute(f"SELECT COUNT(*) FROM market WHERE {clause}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM market WHERE {clause} "
            f"ORDER BY {METRO_SORTS.get(sort, METRO_SORTS['score'])} LIMIT ? OFFSET ?",
            params + [min(limit, 1000), offset]).fetchall()
        return {"total": total, "home": CFG["market"]["home_cbsa"],
                "rows": [dict(r) for r in rows]}
    finally:
        con.close()


@router.get("/api/metro-stats")
def metro_stats():
    con = db()
    try:
        stages = {r["stage"]: r["n"] for r in con.execute(
            "SELECT stage, COUNT(*) n FROM market GROUP BY stage")}
        n = con.execute("SELECT COUNT(*) FROM market").fetchone()[0]
        states = [r["s"] for r in con.execute(
            "SELECT DISTINCT TRIM(states) s FROM market WHERE states NOT LIKE '%,%' "
            "ORDER BY s")]
        return {"markets": n, "stages": stages, "states": states,
                "home": CFG["market"]["home_cbsa"]}
    finally:
        con.close()


@router.get("/api/metro/{cbsa}")
def metro_detail(cbsa: str):
    con = db()
    try:
        m = con.execute("SELECT * FROM market WHERE cbsa=?", (cbsa,)).fetchone()
        if not m:
            raise HTTPException(404, "no such market")
        counties = [dict(r) for r in con.execute(
            "SELECT c.fips, c.name, c.state, p.population, p.domestic_mig, "
            "p.international_mig FROM county c LEFT JOIN county_pop p "
            "ON p.fips=c.fips AND p.year=(SELECT MAX(year) FROM county_pop) "
            "WHERE c.cbsa=? ORDER BY p.population DESC", (cbsa,))]
        # Where the arrivals come from, and what they earn.
        origins = []
        for chunk in (m["top_origins"] or "").split("|"):
            if not chunk:
                continue
            parts = chunk.rsplit(":", 2)
            if len(parts) == 3:
                origins.append({"name": parts[0], "returns": int(parts[1]),
                                "avg_agi": int(parts[2])})
        price = [dict(r) for r in con.execute(
            "SELECT month, zhvi FROM metro_price WHERE cbsa=? ORDER BY month", (cbsa,))]
        industries = [dict(r) for r in con.execute(
            "SELECT w.naics, w.industry, SUM(w.employment) employment, "
            "SUM(w.employment*w.avg_annual_pay)/SUM(w.employment) avg_pay "
            "FROM county_wage w JOIN county c ON c.fips=w.fips "
            "WHERE c.cbsa=? AND w.naics<>'TOTAL' AND w.year=(SELECT MAX(year) FROM county_wage) "
            "GROUP BY w.naics HAVING employment>0 ORDER BY employment DESC LIMIT 20", (cbsa,))]
        return {"market": dict(m), "counties": counties, "origins": origins,
                "price_series": price, "industries": industries}
    finally:
        con.close()


class JobsQuery(BaseModel):
    cbsa: str
    naics: str
    jobs: int
    avg_pay: float | None = None


@router.post("/api/jobs-impact")
def jobs_impact(body: JobsQuery):
    from .multiplier import estimate
    if body.jobs <= 0 or body.jobs > 1_000_000:
        raise HTTPException(400, "jobs must be between 1 and 1,000,000")
    con = db()
    try:
        return estimate(con, body.cbsa, body.naics, body.jobs, body.avg_pay).dict()
    finally:
        con.close()


@router.get("/api/naics")
def naics_sectors():
    from .multiplier import SECTORS, MULTIPLIER
    seen, out = set(), []
    for code, (label, tier) in SECTORS.items():
        if label in seen:
            continue
        seen.add(label)
        out.append({"naics": code, "label": label, "tier": tier,
                    "multiplier": MULTIPLIER[tier]})
    return sorted(out, key=lambda x: -x["multiplier"])


# ═══ MODULE B — offering memorandum ════════════════════════════════════════
# Generated per condo target, from data this app already holds. Nothing here
# reaches into another application.

DEFAULT_RADIUS = float((CFG.get("memo") or {}).get("comps_radius_mi") or 2.0)


@router.get("/api/memo/{group_key}/inspect")
def memo_inspect(group_key: str, radius_mi: float = Query(DEFAULT_RADIUS, ge=0.25, le=10)):
    """What the memo will contain and what it could not confirm — shown before
    generating so nobody sends out a document whose gaps they haven't read."""
    from .memo import gather
    con = db()
    try:
        doc = gather(group_key, con, radius_mi=radius_mi)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()
    t = doc["target"]
    return {
        "group_key": group_key,
        "name": t.get("condo_name") or t.get("addr_primary") or group_key,
        "address": t.get("addr_primary"), "city": t.get("city"),
        "units": t.get("units_nal"), "year_built": t.get("act_yr_blt"),
        "gaps": doc["gaps"],
        "has": {
            "ownership": bool(doc["owners"]),
            "shared_mailing": bool(doc["shared_mailing"]),
            "sales_in_building": bool(doc["sales_in_building"]),
            "nearby_comps": bool(doc["comps"]),
            "redevelopment_capacity": bool(doc["capacity"]),
            "market_context": bool(doc["market"]),
            "declaration_review": bool(t.get("stage2_verified")),
        },
        "counts": {
            "owners": len(doc["owners"]),
            "sales_in_building": len(doc["sales_in_building"]),
            "nearby_comps": len(doc["comps"]),
        },
        "sales_summary": doc["sales_summary"],
    }


@router.get("/api/memo/{group_key}")
def memo_render(group_key: str, radius_mi: float = Query(DEFAULT_RADIUS, ge=0.25, le=10),
                lot_sf: float | None = None, save: bool = False):
    from .memo import generate
    con = db()
    try:
        html, doc, path = generate(group_key, con, radius_mi=radius_mi,
                                   lot_sf=lot_sf, write=save)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()
    headers = {"X-Memo-Gaps": str(len(doc["gaps"]))}
    if path:
        headers["X-Memo-Saved"] = path.name
    return HTMLResponse(html, headers=headers)


# ═══ MODULE C — market due diligence ═══════════════════════════════════════
# Name a metro in plain text, get a macro + micro trend report built from the
# same shared tables the screener ranks on.

@router.get("/api/dd/resolve")
def dd_resolve(q: str = Query(..., min_length=2), limit: int = Query(6, ge=1, le=20)):
    """'columbus ohio' -> candidate metros, best first.

    Always returns the list rather than auto-picking, so an ambiguous name
    ('columbus' matches OH, GA-AL, IN, MS, NE) is a question to the user
    instead of a silent guess.
    """
    from .market_dd import resolve
    con = db()
    try:
        hits = resolve(q, con, limit=limit)
    finally:
        con.close()
    return {
        "query": q,
        "count": len(hits),
        "unambiguous": len(hits) == 1
                       or (len(hits) > 1 and hits[0]["match"] - hits[1]["match"] >= 0.25),
        "candidates": hits,
    }


@router.get("/api/dd/{cbsa}")
def dd_json(cbsa: str):
    """The report's contents as JSON, for the UI preview and for export."""
    from .market_dd import _headline, gather
    con = db()
    try:
        doc = gather(cbsa, con)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()
    m = doc["market"]
    return {
        "cbsa": cbsa, "name": m["name"], "generated": doc["generated"],
        "population": m.get("population"), "stage": m.get("stage"),
        "score": m.get("score"),
        "headline": _headline(doc),
        "ranks": doc["ranks"],
        "pop_years": doc["pop_years"], "components": doc["components"],
        "emp_years": doc["emp_years"], "industries": doc["industries"],
        "counties": doc["county_rows"], "origins": doc["origins"],
        "price_now": doc["price_now"], "price_from_peak": doc["price_from_peak"],
        "rent": doc["rent"], "rent_burden": doc["rent_burden"],
        "gaps": doc["gaps"],
    }


@router.get("/api/dd/{cbsa}/report")
def dd_report(cbsa: str):
    from .market_dd import generate
    con = db()
    try:
        html = generate(cbsa, con)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()
    return HTMLResponse(html)


@router.get("/api/metro-trends")
def metro_trends():
    """Every metro as a shaded polygon, with all indicators on each feature.

    Sent once and switched client-side: the geometry is the expensive part and
    it does not change when you pick a different metric.
    """
    from .trends import build
    con = db()
    try:
        data = build(con)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc))
    finally:
        con.close()
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/api/metro-trends/top")
def metro_trends_top(metric: str = "strength", limit: int = Query(15, ge=1, le=100),
                     min_pop: int = Query(0, ge=0)):
    """Leaders on one indicator — the map's shading as a ranked list."""
    from .trends import top
    con = db()
    try:
        return {"metric": metric, "rows": top(con, metric, limit, min_pop)}
    except KeyError:
        raise HTTPException(404, f"unknown metric {metric!r}")
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc))
    finally:
        con.close()


@router.get("/api/condo-comps")
def condo_comps(group_key: str, radius_mi: float = Query(DEFAULT_RADIUS, ge=0.25, le=10)):
    """The memo's two comp sets as JSON, for the Records drawer."""
    from .memo import gather
    con = db()
    try:
        doc = gather(group_key, con, radius_mi=radius_mi)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()
    return {"summary": doc["sales_summary"],
            "in_building": doc["sales_in_building"][:40],
            "nearby": doc["comps"], "radius_mi": radius_mi}


@router.get("/api/movement")
def movement_digest(limit: int = Query(10, ge=1, le=50)):
    """What changed between the two most recent roll vintages.

    The screen's other views answer "what does this building look like now".
    This one answers "what moved", which is the question a second vintage makes
    askable at all -- and the reason build_targets no longer deletes its history.
    """
    con = db()
    try:
        years = [r[0] for r in con.execute(
            "SELECT DISTINCT roll_year FROM target_snapshot ORDER BY roll_year DESC LIMIT 2")]
        if len(years) < 2:
            return {"comparable": False, "vintages": years,
                    "note": "Only one roll vintage is held, so there is nothing to compare "
                            "yet. A second becomes available the next time a new tax roll is "
                            "ingested and build_targets.py is re-run."}
        now, prior = years[0], years[1]
        counts = con.execute(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN assembly_flag=1 THEN 1 ELSE 0 END) assembling, "
            "SUM(CASE WHEN conc_delta > 0 THEN 1 ELSE 0 END) concentrating, "
            "SUM(CASE WHEN conc_delta < 0 THEN 1 ELSE 0 END) dispersing, "
            "SUM(CASE WHEN top_owner_changed=1 THEN 1 ELSE 0 END) changed_hands "
            "FROM target WHERE conc_delta IS NOT NULL").fetchone()
        cols = ("group_key, condo_name, addr_primary, city, units_nal, "
                "top_owner, top_owner_pct, conc_delta, owners_delta, "
                "top_owner_changed, assembly_flag, score")
        movers = [dict(r) for r in con.execute(
            f"SELECT {cols} FROM target WHERE conc_delta IS NOT NULL "
            "ORDER BY conc_delta DESC LIMIT ?", (limit,))]
        assembling = [dict(r) for r in con.execute(
            f"SELECT {cols} FROM target WHERE assembly_flag=1 "
            "ORDER BY conc_delta DESC LIMIT ?", (limit,))]
        return {
            "comparable": True, "roll_year": now, "prior_roll_year": prior,
            "compared": counts["n"], "assembling": counts["assembling"],
            "concentrating": counts["concentrating"], "dispersing": counts["dispersing"],
            "changed_hands": counts["changed_hands"],
            "biggest_movers": movers, "newly_assembling": assembling,
        }
    finally:
        con.close()


@router.get("/api/target/{group_key}/beneficial")
def beneficial_owners(group_key: str):
    """One buyer behind several LLCs, clustered transitively.

    `top_owner_pct` counts units under one owner NAME, which an assembler defeats
    by holding each unit in its own entity. Every edge here is an auditable rule
    and carries its evidence, because a false merge fabricates the number this
    whole tool is ranked on.
    """
    from .beneficial import top_beneficial
    con = db()
    try:
        if not con.execute("SELECT 1 FROM target WHERE group_key=?", (group_key,)).fetchone():
            raise HTTPException(404, "no such condo group")
        return top_beneficial(con, group_key)
    finally:
        con.close()


@router.get("/api/target/{group_key}/history")
def target_history(group_key: str):
    """Every roll vintage held for this building, oldest first."""
    con = db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM target_snapshot WHERE group_key=? ORDER BY roll_year",
            (group_key,))]
        return {"group_key": group_key, "vintages": len(rows), "snapshots": rows}
    finally:
        con.close()


# ═══ migration flows ═══════════════════════════════════════════════════════
# 109,044 IRS SOI county-to-county rows with income attached to every flow, read
# until now in exactly one place to build a summary string. The `out` direction
# was ingested and never surfaced at all.

@router.get("/api/flows/national")
def flows_national(min_pop: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)):
    """Every metro's net gain in households and in income, ranked separately.

    Two rankings rather than one composite: the gap between them is the finding.
    A metro gaining households while losing AGI is gaining poor and losing rich,
    and no single number shows that.
    """
    from .flows import national
    con = db()
    try:
        return national(con, min_pop=min_pop, limit=limit)
    finally:
        con.close()


@router.get("/api/flows/{cbsa}")
def flows_metro(cbsa: str, limit: int = Query(15, ge=1, le=100)):
    """One metro's corridors: who feeds it, who drains it, what each carries."""
    from .flows import metro
    con = db()
    try:
        return metro(con, cbsa, limit=limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()


# ═══ buyout cost ═══════════════════════════════════════════════════════════
# What the units cost, from recorded sales. Deliberately NOT a development pro
# forma: the residual needs a revenue per new unit that is nowhere in this data,
# and an invented one would be the least reliable figure in the app sitting where
# the eye lands.

@router.get("/api/economics/{group_key}")
def economics(group_key: str,
              holdout_share: float = Query(0.10, ge=0, le=0.5),
              holdout_premium: float = Query(0.25, ge=0, le=2.0),
              radius_mi: float = Query(DEFAULT_RADIUS, ge=0.25, le=10)):
    """Buyout cost for one building, with the assumptions as query parameters so a
    sensitivity pass is three URL changes rather than a rebuild."""
    from .economics import estimate
    from .memo import gather

    con = db()
    try:
        # Nearby comps are only consulted when the building itself has no
        # single-unit sale to price from, so the cost of gathering them is paid
        # only in the case that needs them.
        nearby_psf = None
        try:
            doc = gather(group_key, con, radius_mi=radius_mi)
            psfs = [c["sale_psf"] for c in doc.get("comps", []) if c.get("sale_psf")]
            nearby_psf = sorted(psfs)[len(psfs) // 2] if psfs else None
        except LookupError:
            raise
        except Exception:  # noqa: BLE001 -- comps are optional here, not required
            nearby_psf = None
        return estimate(group_key, con, holdout_share=holdout_share,
                        holdout_premium=holdout_premium, nearby_psf=nearby_psf).as_dict()
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        con.close()


# ═══ development capacity ══════════════════════════════════════════════════
# What can be built on the site, once you have it. Zoning envelope from the
# shared Miami-Dade layer, unit yield under each Florida path that raises it.
#
# lot_sf is an INPUT, deliberately. Condo land is held in common, so the tax
# roll carries no usable site area: only 12% of targets have a land parcel with
# lot_size > 0, and those figures are plat-wide (274 acres against a 25-unit
# building), not the building's footprint. Reporting them as site area would be
# worse than asking.

@router.get("/api/capacity")
def capacity(
    group_key: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    lot_sf: float | None = None,
):
    """Capacity at a target (group_key) or an arbitrary point (lat/lon)."""
    con = db()
    try:
        site = {}
        if group_key:
            row = con.execute(
                "SELECT group_key, condo_name, addr_primary, city, units_nal, "
                "act_yr_blt, lon, lat FROM target WHERE group_key = ?",
                (group_key,),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"no target {group_key}")
            if row["lon"] is None:
                raise HTTPException(422, f"target {group_key} has no coordinates")
            lon, lat = row["lon"], row["lat"]
            site = {
                "group_key": row["group_key"],
                "name": row["condo_name"] or row["addr_primary"],
                "address": row["addr_primary"],
                "city": row["city"],
                "existing_units": row["units_nal"],
                "year_built": row["act_yr_blt"],
            }
            # Land parcels sharing this folio prefix. Surfaced as a reference
            # figure with its own label - NOT silently used as the site area.
            p = con.execute(
                "SELECT count(*) n, sum(lot_size) s FROM pa_parcel "
                "WHERE subdivision = ? AND lot_size > 0",
                (group_key,),
            ).fetchone()
            if p and p["s"]:
                site["land_parcels"] = {
                    "count": p["n"],
                    "sum_lot_sf": p["s"],
                    "note": "plat-wide land parcels on this folio prefix; "
                            "usually larger than the building's site",
                }
        if lat is None or lon is None:
            raise HTTPException(422, "need group_key, or both lat and lon")

        out = capacity_mod.analyze(lon, lat, lot_sf)
        out["target"] = site or None

        # The number that decides a termination play: can you rebuild what is
        # already standing? Older condos routinely exceed today's density, so
        # the by-right envelope is SMALLER than the existing building. That
        # makes the site legally non-conforming and the bonus paths the only
        # route to breaking even on unit count.
        existing = site.get("existing_units")
        if existing and out.get("resolved"):
            best = max((s["units"] for s in out["scenarios"] if s.get("units")),
                       default=None)
            by_right = next((s["units"] for s in out["scenarios"]
                             if "by-right" in s["path"]), None)
            if best is not None:
                out["rebuild"] = {
                    "existing_units": existing,
                    "by_right_units": by_right,
                    "best_case_units": best,
                    "delta_vs_existing": best - existing,
                    "non_conforming": bool(by_right and by_right < existing),
                    "verdict": (
                        f"best case {best:,} units vs {existing:,} standing "
                        f"({best - existing:+,})"
                    ),
                }

        if lot_sf is None:
            out["needs"] = ("lot_sf - supply the site area to get unit counts; "
                            "the zoning envelope above is independent of it")
        return out
    finally:
        con.close()


@router.get("/api/acquisitions/health")
def health():
    try:
        con = db()
        n = con.execute("SELECT COUNT(*) FROM target").fetchone()[0]
        con.close()
        return {"ok": True, "targets": n}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
