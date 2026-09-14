"""One-time migration: move the shared tables out of prospect.db into the
shared store that Sitefolio also reads.

    venv\\Scripts\\python.exe scripts\\prospect\\migrate_to_shared.py            migrate
    venv\\Scripts\\python.exe scripts\\prospect\\migrate_to_shared.py --status   report only

Moves (not copies) the tables listed in backend.db.SHARED_TABLES, so there is
exactly one source of truth and no drift. Prospect's queries keep working
untouched because connect() attaches the shared DB and SQLite resolves an
unqualified table name against main first, then attached databases.

Safe to re-run: tables already living in shared are left alone.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.prospect.db import DB_PATH, SHARED_DB, SHARED_TABLES, connect  # noqa: E402


def _rows(con, db: str, table: str) -> int | None:
    try:
        return con.execute(f'SELECT count(*) FROM "{db}"."{table}"').fetchone()[0]
    except Exception:
        return None


def _exists(con, db: str, table: str) -> bool:
    q = f"SELECT 1 FROM {db}.sqlite_master WHERE type='table' AND name=?"
    return con.execute(q, (table,)).fetchone() is not None


def status(con) -> None:
    print(f"main:   {DB_PATH}")
    print(f"shared: {SHARED_DB}\n")
    print(f"  {'table':22} {'main':>10} {'shared':>10}")
    for t in sorted(SHARED_TABLES):
        m = _rows(con, "main", t)
        s = _rows(con, "shared", t)
        print(f"  {t:22} {('-' if m is None else f'{m:,}'):>10} "
              f"{('-' if s is None else f'{s:,}'):>10}")


def migrate(con) -> int:
    moved = 0
    for t in sorted(SHARED_TABLES):
        if not _exists(con, "main", t):
            continue
        main_n = _rows(con, "main", t) or 0
        shared_n = _rows(con, "shared", t) or 0
        if shared_n and not main_n:
            continue

        t0 = time.time()
        # The shared table already exists (connect() created it from the routed
        # schema); fill it from main, then drop main's copy so unqualified
        # lookups fall through to shared.
        con.execute(f'DELETE FROM shared."{t}"')
        con.execute(f'INSERT INTO shared."{t}" SELECT * FROM main."{t}"')
        after = _rows(con, "shared", t) or 0
        if after != main_n:
            con.rollback()
            print(f"  ! {t}: copied {after:,} of {main_n:,} - rolled back, left in main")
            continue
        con.execute(f'DROP TABLE main."{t}"')
        con.commit()
        moved += 1
        print(f"  moved {t:22} {main_n:>10,} rows  ({time.time()-t0:.1f}s)")
    return moved


def main() -> int:
    con = connect()
    if "--status" in sys.argv:
        status(con)
        return 0

    print(f"Migrating shared tables\n  from {DB_PATH}\n  to   {SHARED_DB}\n")
    moved = migrate(con)
    con.execute("VACUUM main")
    con.commit()
    print(f"\nMoved {moved} table(s). Reclaiming space in main...")
    status(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
