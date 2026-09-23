r"""Bring an earlier install's data\ into this one.

A fresh download has an empty data\ folder -- no scored targets, no deal
stages, no declarations read, no shared store -- and the old install that
had all of that is still sitting in another folder. install.py finds that
folder (from the shortcuts and scheduled task the old install left behind)
and calls import_data() before starting the new server.

Rules, in order of how much they matter:

  * Nothing in this install is ever overwritten -- with one exception: a
    SQLite file that exists here but holds no rows at all (the app creates
    empty databases on first start) is replaced, since there is nothing in
    it to lose.
  * The old folder is only read, never changed.
  * SQLite files are copied with the backup API rather than as bytes, so a
    database the old server still has open (it usually does -- it starts at
    logon) comes across consistent, WAL contents included. Its -wal/-shm
    sidecars are skipped: the backup already folded them in, and a stray
    -wal beside a freshly copied file is how you corrupt it.
  * Every copy goes to a temporary name first and is renamed into place, so
    an interrupted import leaves no half-written file behind.
  * If the disk can't hold a file, it is skipped and said so rather than
    filling the drive.

Stdlib only: this runs from install.py before anything else is guaranteed.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path, PurePath, PureWindowsPath
from typing import Callable, Iterable

SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
SKIP_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini"}
# Leave this much free after a copy, so an import never takes the last of the disk.
DISK_HEADROOM = 1 << 30


def same_folder(a: Path | str, b: Path | str) -> bool:
    return (os.path.normcase(os.path.realpath(str(a)))
            == os.path.normcase(os.path.realpath(str(b))))


def root_from_launch_ref(working_dir: str = "", target: str = "",
                         arguments: str = "") -> list[Path]:
    r"""Every app folder a shortcut or task command line could be pointing at.

    Tried three ways because shortcuts made by different versions of the
    installer set different fields: the working directory (current), the
    "...\launch.py" in the arguments, and the interpreter's own path
    (<root>\.venv\Scripts\pythonw.exe)."""
    out: list[Path] = []
    if working_dir:
        out.append(Path(str(_pure(working_dir))))
    for token in _quoted_or_bare(arguments):
        if token.lower().endswith(".py"):
            out.append(Path(str(_pure(token).parent)))
    if target:
        t = _pure(target)
        if t.parent.name.lower() in ("scripts", "bin") and len(t.parents) >= 3:
            out.append(Path(str(t.parents[2])))
    return out


def _pure(s: str) -> PurePath:
    # These strings come out of Windows shortcuts. Parsing them as Windows
    # paths explicitly keeps .parent right when the tests run elsewhere.
    return PureWindowsPath(s) if "\\" in s else PurePath(s)


def _quoted_or_bare(s: str) -> list[str]:
    toks, cur, quoted = [], "", False
    for ch in s or "":
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if cur:
                toks.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        toks.append(cur)
    return toks


def previous_installs(candidates: Iterable[Path], this_root: Path) -> list[Path]:
    """De-duplicated candidates that really are another install with data,
    in the order given (callers list the likeliest first)."""
    seen: list[Path] = []
    for c in candidates:
        try:
            if not (c / "data").is_dir() or same_folder(c, this_root):
                continue
        except OSError:
            continue
        if not any(same_folder(c, s) for s in seen):
            seen.append(c)
    return seen


def _is_sqlite(p: Path) -> bool:
    if p.suffix.lower() not in SQLITE_SUFFIXES:
        return False
    try:
        with open(p, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _row_count(p: Path) -> int | None:
    """Total rows across every table, or None if it can't be read (treated as
    "has data", so an unreadable file is never replaced)."""
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return sum(con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names)
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _copy_sqlite(src: Path, tmp: Path) -> None:
    # Opened read-write on purpose: a read-only open of a WAL database whose
    # -shm is gone fails outright. Nothing is written to it by a backup.
    s = sqlite3.connect(str(src), timeout=30)
    try:
        d = sqlite3.connect(str(tmp))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _drop_sidecars(p: Path) -> None:
    for suf in SIDECAR_SUFFIXES:
        side = p.with_name(p.name + suf)
        if side.exists():
            side.unlink()   # raises if the file is open -- the caller skips it


def import_data(src_root: Path, dest_root: Path,
                log: Callable[[str], None] = print) -> dict:
    """Copy src_root/data into dest_root/data under the rules above."""
    src, dest = Path(src_root) / "data", Path(dest_root) / "data"
    copied: list[str] = []
    replaced: list[str] = []
    kept: list[str] = []
    failed: list[str] = []
    if not src.is_dir():
        return {"copied": copied, "replaced": replaced, "kept": kept, "failed": failed}

    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        if any(part in SKIP_NAMES for part in rel.parts) or not f.is_file():
            continue
        if f.name.endswith(SIDECAR_SUFFIXES) or f.name.endswith(".importing"):
            continue
        name = rel.as_posix()
        target = dest / rel
        sqlite_file = _is_sqlite(f)

        if target.exists():
            if not (sqlite_file and _row_count(target) == 0):
                kept.append(name)
                continue
            replacing = True
        else:
            replacing = False

        tmp = target.with_name(target.name + ".importing")
        try:
            size = f.stat().st_size
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(target.parent).free < size + DISK_HEADROOM:
                failed.append(name)
                log(f"  ! {name}: not enough free disk for {size / 1e9:.1f} GB -- skipped")
                continue
            if tmp.exists():
                tmp.unlink()
            if size > 200_000_000:
                log(f"  … {name} ({size / 1e9:.1f} GB) -- this one takes a while")
            if sqlite_file:
                _copy_sqlite(f, tmp)
            else:
                shutil.copy2(f, tmp)
            if replacing:
                _drop_sidecars(target)
            os.replace(tmp, target)
        except (OSError, sqlite3.Error) as e:
            failed.append(name)
            log(f"  ! {name}: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass
            continue
        (replaced if replacing else copied).append(name)

    return {"copied": copied, "replaced": replaced, "kept": kept, "failed": failed}
