"""Build the app's data from inside the app.

A fresh install has no national market tables and no Miami-Dade condo roll --
they are built from public files, by the scripts under scripts/prospect/. Until
now that meant a list of command-line steps in the README, and every screen that
needed the data said only that "the shared store could not be opened". Someone
who downloaded a zip and double-clicked install.bat has no reason to know what a
shared store is, let alone how to build one.

This runs the same scripts, in the same order the README documents, as one
background job the Reference tab can start, watch and cancel. Nothing here
changes what the scripts do; it only removes the need to open a terminal.

One job at a time. The scripts all write the same two SQLite files, and two
writers racing each other is how a half-built store gets made.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .shared_paths import APP_ROOT, shared_db

ROOT = APP_ROOT
LOG_DIR = ROOT / "logs"
SCRIPTS = ROOT / "scripts" / "prospect"

# What each group builds, in the order that works (build_targets reads what the
# three ingests wrote; build_markets reads what ingest_market wrote). `check` is
# the table whose rows say "this is built", and `store` which database holds it.
GROUPS = {
    "markets": {
        "title": "National market data",
        "detail": ("Population, migration, building permits, wages and home values for every "
                   "US metro, from Census, IRS, BLS and Zillow public files. Feeds the Markets "
                   "tab."),
        "size": "about 5-10 minutes; downloads a few hundred MB",
        "store": "shared", "check": "market",
        "steps": [("Download the public files", "ingest_market.py", ["--all"]),
                  ("Score the markets", "build_markets.py", [])],
    },
    "condo": {
        "title": "Miami-Dade condo screen",
        "detail": ("Every condo association (state registry) and every unit owner (tax roll), "
                   "matched, grouped by building and scored. Feeds the Records tab."),
        "size": "about 15-25 minutes; downloads about 150 MB",
        "store": "prospect", "check": "target",
        "steps": [("State condo registry", "ingest_dbpr.py", []),
                  ("Condo unit owners (tax roll)", "ingest_nal.py", []),
                  ("Land parcels", "ingest_pa.py", []),
                  ("Group, match and score buildings", "build_targets.py", [])],
    },
}
TAIL_LINES = 14

_lock = threading.Lock()
_job: dict | None = None        # the current or most recent job; None before the first
_proc: subprocess.Popen | None = None
_thread: threading.Thread | None = None
router = APIRouter()


def _python() -> str:
    """The interpreter to run the scripts with. The server is usually started
    with pythonw.exe (no console window); a child of that would try to open a
    console of its own."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def _popen_flags() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _rows(group: dict) -> int | None:
    """Row count of the group's marker table, or None if it cannot be read."""
    if group["store"] == "shared":
        path = shared_db()
    else:
        from .prospect.db import DB_PATH as path
    if not Path(path).exists():
        return None
    try:
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=3.0)
        try:
            return con.execute(f"SELECT COUNT(*) FROM {group['check']}").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _tail(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.rstrip() for ln in deque(f, maxlen=TAIL_LINES) if ln.strip()]
    except OSError:
        return []


def _run(job: dict) -> None:
    """Run a job's steps in order; record where it stopped and why."""
    global _proc
    group = GROUPS[job["group"]]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(job["log"], "w", encoding="utf-8") as log:
            for i, (label, script, args) in enumerate(group["steps"]):
                if job["cancel"]:
                    break
                job["step"] = i
                log.write(f"\n=== {label} ({script}) ===\n")
                log.flush()
                with _lock:
                    if job["cancel"]:
                        break
                    _proc = subprocess.Popen(
                        [_python(), "-u", str(SCRIPTS / script), *args], cwd=str(ROOT),
                        stdout=log, stderr=subprocess.STDOUT, **_popen_flags())
                    proc = _proc
                code = proc.wait()
                if job["cancel"]:
                    break
                if code != 0:
                    job["state"] = "failed"
                    last = (_tail(Path(job["log"])) or [""])[-1][:300]
                    job["error"] = f"{label} failed (exit {code})" + (f": {last}" if last else ".")
                    break
            else:
                job["state"] = "done"
        if job["cancel"]:
            job["state"] = "cancelled"
    except Exception as e:                       # noqa: BLE001 -- must never leave a job "running"
        job["state"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished"] = datetime.now().isoformat(timespec="seconds")
        with _lock:
            _proc = None


def start(group_id: str) -> dict:
    global _job, _thread
    if group_id not in GROUPS:
        raise KeyError(group_id)
    with _lock:
        if _job and _job["state"] == "running":
            raise RuntimeError(f"{GROUPS[_job['group']]['title']} is still building.")
        _job = {"group": group_id, "state": "running", "step": 0, "cancel": False,
                "started": datetime.now().isoformat(timespec="seconds"), "finished": None,
                "error": None, "log": str(LOG_DIR / f"build-{group_id}.log")}
        _thread = threading.Thread(target=_run, args=(_job,), daemon=True)
        _thread.start()
        return _public(_job)


def cancel() -> bool:
    with _lock:
        if not _job or _job["state"] != "running":
            return False
        _job["cancel"] = True
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()
        return True


def _public(job: dict) -> dict:
    g = GROUPS[job["group"]]
    return {"group": job["group"], "title": g["title"], "state": job["state"],
            "step": job["step"], "steps": [s[0] for s in g["steps"]],
            "started": job["started"], "finished": job["finished"], "error": job["error"],
            "tail": _tail(Path(job["log"])), "log": job["log"]}


def status() -> dict:
    out = []
    for gid, g in GROUPS.items():
        n = _rows(g)
        out.append({"id": gid, "title": g["title"], "detail": g["detail"], "size": g["size"],
                    "built": bool(n), "rows": n,
                    "steps": [s[0] for s in g["steps"]]})
    return {"groups": out, "job": _public(_job) if _job else None}


@router.get("/api/build")
def build_status():
    return status()


@router.post("/api/build/{group_id}")
def build_start(group_id: str):
    try:
        return start(group_id)
    except KeyError:
        raise HTTPException(404, f"Unknown dataset '{group_id}'.")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/api/build-cancel")
def build_cancel():
    return {"cancelled": cancel()}
