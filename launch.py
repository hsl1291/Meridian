"""Launcher — health-check, self-heal, auto-update, log-rotate, open the app
window. Plain Python: no PowerShell, no wscript/.vbs. Shared across the apps;
the CONFIG block below is the only per-app difference.

Invoked directly by the desktop shortcut via venv\\Scripts\\pythonw.exe, so this
process has no console attached — guard prints accordingly (handled below).

--server-only: ensure the server is running but don't open a browser window.

Every invocation checks whether an update from GitHub is due -- at most once
per UPDATE_CHECK_INTERVAL_HOURS, tracked via .version's own mtime, so this is
a no-op network-wise on all but roughly one call a day even though the
"Meridian Server" scheduled task (registered by install.py by default) invokes
this every 15 minutes. When an update actually applies, the running server is
restarted so the new code takes effect -- updating the files on disk does
nothing to a process that already loaded the old ones into memory.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ---- CONFIG (edit per app) ----
PORT = 8012
UVICORN_MODULE = "backend.app:app"
APP_DIR_ARG = None
WINDOW_TITLE = "Meridian"
HEALTH_PATH = "api/instance"
# --------------------------------

ROOT = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{PORT}/"
HEALTH_URL = f"{URL}{HEALTH_PATH}"
# pythonw.exe, not python.exe: it is a GUI-subsystem binary, so it never
# allocates a console at all. stdout/stderr are redirected to the log files
# below, so nothing is lost.
# .venv is what start.py creates; `venv` is the older hand-made layout. Both are
# inside the app folder, so a downloaded copy is self-contained either way.
def _find_python() -> Path:
    for env in (".venv", "venv"):
        base = ROOT / env / ("Scripts" if os.name == "nt" else "bin")
        for exe in ("pythonw.exe", "python.exe", "python3", "python"):
            p = base / exe
            if p.exists():
                return p
    return Path(sys.executable)


PYTHON = _find_python()
LOG_DIR = ROOT / "logs"
UPDATE_LOG = LOG_DIR / "update.log"
# Written by backend.updater.apply() on every run that reaches GitHub
# successfully (whether or not anything actually changed), so its mtime is
# already the right signal for "when did we last check" -- no separate stamp
# file to keep in sync with it.
VERSION_STAMP = ROOT / ".version"
UPDATE_CHECK_INTERVAL_HOURS = 24

CREATE_NO_WINDOW = 0x08000000
# NOT DETACHED_PROCESS: Windows *ignores* CREATE_NO_WINDOW when DETACHED_PROCESS
# is also set, so the server ended up with no inherited console, allocated its
# own, and Windows Terminal hosted it as a visible window. CREATE_NO_WINDOW
# alone gives a console that is never shown; NEW_PROCESS_GROUP keeps a stray
# Ctrl+C in a parent console from reaching the server.
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _same_folder(a, b) -> bool:
    return (os.path.normcase(os.path.realpath(str(a)))
            == os.path.normcase(os.path.realpath(str(b))))


def is_healthy() -> bool:
    """True only when THIS folder's server is the one answering on PORT.

    "Something answers on 8012" is not enough. Installing a new copy while an
    older one is still running (it starts at logon, so it usually is) left
    the old server holding the port: this check passed, nothing restarted,
    and the new desktop icon opened the OLD folder's code until the next
    reboot. /api/instance names the folder it is serving from; anything
    else on the port -- another copy, or one too old to have the route --
    counts as down, and start_server() clears the port before starting.
    """
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as r:
            if r.status != 200:
                return False
            info = json.loads(r.read().decode("utf-8"))
    except Exception:
        return False
    root = info.get("root") if isinstance(info, dict) else None
    return bool(root) and _same_folder(root, ROOT)


def rotate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > 1_000_000:
            old = path.with_suffix(path.suffix + ".old")
            path.replace(old)
    except OSError:
        pass


def kill_stale_port_owner(port: int) -> None:
    try:
        # creationflags matters: without CREATE_NO_WINDOW this console-subsystem
        # helper pops a visible command-prompt window every time the launcher
        # runs under pythonw.exe.
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return
    pids = set()
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                pids.add(m.group(1))
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", pid, "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )


def start_server() -> None:
    kill_stale_port_owner(PORT)
    LOG_DIR.mkdir(exist_ok=True)
    log_out = LOG_DIR / "server.log"
    log_err = LOG_DIR / "server.err.log"
    rotate_log(log_out)
    rotate_log(log_err)

    args = [str(PYTHON), "-m", "uvicorn", UVICORN_MODULE,
            "--host", "127.0.0.1", "--port", str(PORT)]
    if APP_DIR_ARG:
        args += ["--app-dir", APP_DIR_ARG]

    with open(log_out, "ab") as out_f, open(log_err, "ab") as err_f:
        subprocess.Popen(
            args,
            cwd=str(ROOT),
            stdout=out_f,
            stderr=err_f,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )

    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        if is_healthy():
            break


def _log_update(msg: str) -> None:
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(UPDATE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def _is_git_checkout() -> bool:
    """A clone is updated with git, not by overwriting its working tree from a
    zip -- that would leave every updated file showing as a local change and
    silently discard uncommitted edits' context. MERIDIAN_AUTO_UPDATE=1
    opts a clone back in (the Windows CI job does, to exercise the path)."""
    return (ROOT / ".git").exists() and os.environ.get("MERIDIAN_AUTO_UPDATE") != "1"


def _update_due() -> bool:
    if _is_git_checkout():
        return False
    try:
        age = time.time() - VERSION_STAMP.stat().st_mtime
        return age >= UPDATE_CHECK_INTERVAL_HOURS * 3600
    except OSError:
        return True  # no stamp yet -- this copy has never checked


def check_for_update() -> bool:
    """Pull an update from GitHub if one is due, applying it in place via the
    same backend.updater.apply() the in-app "Check for updates" button and
    update.bat use. Returns True when files actually changed, which means the
    running server (if any) is stale and must be restarted for the new code
    to take effect -- writing new files does nothing to a process that
    already has the old ones loaded into memory."""
    if not _update_due():
        return False
    sys.path.insert(0, str(ROOT))
    try:
        from backend.updater import apply
    except ImportError as e:
        _log_update(f"updater unavailable: {e}")
        return False

    r = apply()
    if not r.get("ok"):
        _log_update(f"check failed: {r.get('error')}")
        return False
    if not r.get("updated"):
        return False  # apply() still wrote .version, so this is rate-limited again either way

    _log_update(f"applied {r['changed_count']} file(s) from {r.get('from_sha')} "
               f"to {r.get('to_sha')}" + (", config merged" if r.get("merged") else ""))

    # requirements.txt may have changed; reuse start.py's own idempotent
    # installer (it stamps requirements.txt and skips the reinstall when it
    # hasn't changed) rather than duplicating pip logic here. creationflags is
    # Windows-only -- this app only ever runs launch.py there, but a bare
    # ValueError from a kwarg irrelevant to what's being fixed is not the
    # failure anyone auto-updating should see, so it's conditional rather
    # than assumed like the rest of this Windows-only file.
    extra = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    setup = subprocess.run(
        [str(PYTHON), str(ROOT / "start.py"), "--setup-only"],
        cwd=str(ROOT), capture_output=True, text=True, **extra,
    )
    if setup.returncode != 0:
        _log_update("dependency setup after update failed: "
                   + (setup.stderr or setup.stdout or "").strip()[:400])
        # Still restart below -- new code visibly failing to start beats old
        # code silently keeping running with nobody the wiser.
    return True


def open_window() -> None:
    edge_paths = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    edge = next((p for p in edge_paths if p.exists()), None)
    if edge:
        subprocess.Popen([str(edge), f"--app={URL}"], creationflags=CREATE_NO_WINDOW)
    else:
        os.startfile(URL)


def main() -> int:
    server_only = "--server-only" in sys.argv
    updated = check_for_update()
    if updated or not is_healthy():
        start_server()
    if not server_only:
        open_window()
    return 0


if __name__ == "__main__":
    sys.exit(main())
