"""Stage 2 — declaration review for the shortlist.

Stage 1 ranks all 6,160 Miami-Dade condos from free bulk data. Stage 2 answers
the two questions that need the actual recorded document, and it only runs on
buildings that survive the stage-1 gate, because reading declarations is the
expensive part.

    --worklist   emit the shortlist as .xlsx, one row per building, with the
                 Clerk search link and the search terms to use
    --extract    read every PDF in data/declarations/, analyze it, and write the
                 findings back to the target table
    --status     how much of the shortlist has been reviewed

RETRIEVAL IS MANUAL, ON PURPOSE. The Miami-Dade Clerk's Official Records site is
a client-rendered SPA with no documented query API; every route returns the same
shell, so there is nothing stable to automate against. Rather than ship a
brittle scraper that silently breaks, the worklist hands an analyst a prefilled
search and the extractor does the reading. Drop the PDF in as
data/declarations/<group_key>.pdf and run --extract.

    venv\\Scripts\\python.exe scripts\\stage2.py --worklist
    venv\\Scripts\\python.exe scripts\\stage2.py --extract
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.db import connect  # noqa: E402
from backend.declaration import analyze  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())
DECL_DIR = ROOT / "data" / "declarations"
CLERK_SEARCH = "https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch"


def shortlist(con, min_score=0.0, limit=250):
    s = CFG["shortlist"]
    return con.execute(
        """SELECT * FROM target
           WHERE units_nal >= ? AND age_years >= ? AND score >= ?
           ORDER BY score DESC LIMIT ?""",
        (s["min_units"], s["min_age_years"], min_score, limit)).fetchall()


def cmd_worklist(con, args):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    rows = shortlist(con, args.min_score, args.limit)
    wb = Workbook()
    ws = wb.active
    ws.title = "Stage 2 worklist"
    cols = ["Rank", "Condo", "Address", "City", "Units", "Built", "Decl. recorded",
            "Largest owner", "Owner %", "Score", "Search the Clerk for",
            "Clerk search", "Save PDF as", "Folio prefix"]
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F3B57")
    for c, label in enumerate(cols, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font, cell.fill = head, fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    for i, t in enumerate(rows, 1):
        # The declaration is indexed under the condo's name; the association name
        # is the fallback when the roll and the registry disagree.
        term = t["condo_name"] or t["addr_primary"]
        ws.append([
            i, t["condo_name"], t["addr_primary"], t["city"], t["units_nal"],
            t["act_yr_blt"], t["recorded_year"], t["top_owner"], t["top_owner_pct"],
            t["score"], f'"{term}" — DECLARATION OF CONDOMINIUM',
            CLERK_SEARCH, f"data/declarations/{t['group_key']}.pdf", t["group_key"],
        ])
        ws.cell(row=i + 1, column=12).hyperlink = CLERK_SEARCH
        ws.cell(row=i + 1, column=12).style = "Hyperlink"

    ws.freeze_panes = "A2"
    for i, wdt in enumerate([6, 34, 26, 16, 7, 7, 11, 32, 9, 8, 44, 18, 34, 14], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = wdt

    out = ROOT / "data" / f"stage2_worklist_{datetime.now():%Y%m%d}.xlsx"
    wb.save(out)
    DECL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"wrote {len(rows)} rows -> {out}")
    print(f"drop each declaration PDF into {DECL_DIR} named <folio prefix>.pdf, "
          f"then run: scripts\\stage2.py --extract")


def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("pypdf is not installed -- venv\\Scripts\\python.exe -m pip install pypdf")
    reader = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def cmd_extract(con, args):
    DECL_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(DECL_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs in {DECL_DIR} -- run --worklist first, then save declarations there")
        return
    done = skipped = 0
    for p in pdfs:
        key = p.stem
        row = con.execute("SELECT condo_name FROM target WHERE group_key=?", (key,)).fetchone()
        if not row:
            print(f"  ?? {p.name}: no target with folio prefix {key} — skipped")
            skipped += 1
            continue
        text = pdf_text(p)
        if len(text.strip()) < 200:
            print(f"  !! {p.name}: almost no extractable text — this is likely a SCANNED "
                  f"image PDF and needs OCR before it can be read")
            skipped += 1
            continue
        f = analyze(text)
        r = f.as_row()
        con.execute(
            """UPDATE target SET termination_threshold=?, kaufman_original=?,
               kaufman_by_amendment=?, declaration_source_url=?, stage2_notes=?,
               stage2_verified=? WHERE group_key=?""",
            (r["termination_threshold"], r["kaufman_original"], r["kaufman_by_amendment"],
             str(p.relative_to(ROOT)).replace("\\", "/"), r["stage2_notes"],
             1 if (args.mark_verified and f.confidence == "high") else 0, key))
        done += 1
        print(f"  ok {row['condo_name'][:38]:<38} threshold={f.termination_threshold or '?':<18}"
              f" kaufman(orig/amend)={f.kaufman_original}/{f.kaufman_by_amendment}"
              f" doc={f.doc_type} conf={f.confidence}")
        for wmsg in f.warnings:
            print(f"       ! {wmsg}")
    con.commit()
    print(f"\n{done} extracted, {skipped} skipped.")
    print("stage2_verified is only auto-set on high-confidence reads with --mark-verified; "
          "everything else stays unverified until a human ticks it in the UI.")


def cmd_status(con, args):
    s = CFG["shortlist"]
    tot = len(shortlist(con, 0, 100000))
    rev = con.execute(
        """SELECT COUNT(*) FROM target WHERE units_nal>=? AND age_years>=?
           AND termination_threshold IS NOT NULL""",
        (s["min_units"], s["min_age_years"])).fetchone()[0]
    ver = con.execute(
        """SELECT COUNT(*) FROM target WHERE units_nal>=? AND age_years>=?
           AND stage2_verified=1""", (s["min_units"], s["min_age_years"])).fetchone()[0]
    print(f"shortlist ({s['min_units']}+ units, {s['min_age_years']}+ years): {tot:,}")
    print(f"  declaration read:      {rev:,}")
    print(f"  human-verified:        {ver:,}")
    rows = con.execute(
        """SELECT termination_threshold, COUNT(*) n FROM target
           WHERE termination_threshold IS NOT NULL
           GROUP BY termination_threshold ORDER BY n DESC""").fetchall()
    if rows:
        print("  thresholds found:")
        for r in rows:
            print(f"    {r['termination_threshold']:<22} {r['n']:>4}")
    k = con.execute(
        "SELECT SUM(kaufman_original) o, SUM(kaufman_by_amendment) a FROM target").fetchone()
    print(f"  kaufman original: {k['o'] or 0}   by amendment: {k['a'] or 0}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worklist", action="store_true")
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--mark-verified", action="store_true",
                    help="auto-tick stage2_verified on high-confidence reads")
    args = ap.parse_args()
    con = connect()
    try:
        if args.worklist:
            cmd_worklist(con, args)
        elif args.extract:
            cmd_extract(con, args)
        else:
            cmd_status(con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
