"""Launcher — health-check, self-heal, log-rotate, open the app window.
Plain Python: no PowerShell, no wscript/.vbs. Shared across the apps; the
CONFIG block below is the only per-app difference.

Invoked directly by the desktop shortcut via venv\\Scripts\\pythonw.exe, so this
process has no console attached — guard prints accordingly (handled below).

--server-only: ensure the server is running but don't open a browser window.
"""
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
WINDOW_TITLE = "Groundwork"
HEALTH_PATH = "api/shared/status"
# --------------------------------

ROOT = Path(__file__).resolve().parent
URL = f"http://127.0.0.1:{PORT}/"
HEALTH_URL = f"{URL}{HEALTH_PATH}"
# pythonw.exe, not python.exe: it is a GUI-subsystem binary, so it never
# allocates a console at all. stdout/stderr are redirected to the log files
# below, so nothing is lost.
PYTHON = ROOT / "venv" / "Scripts" / "pythonw.exe"
if not PYTHON.exists():
    PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "logs"

CREATE_NO_WINDOW = 0x08000000
# NOT DETACHED_PROCESS: Windows *ignores* CREATE_NO_WINDOW when DETACHED_PROCESS
# is also set, so the server ended up with no inherited console, allocated its
# own, and Windows Terminal hosted it as a visible window. CREATE_NO_WINDOW
# alone gives a console that is never shown; NEW_PROCESS_GROUP keeps a stray
# Ctrl+C in a parent console from reaching the server.
CREATE_NEW_PROCESS_GROUP = 0x00000200


def is_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


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
    if not is_healthy():
        start_server()
    if not server_only:
        open_window()
    return 0


if __name__ == "__main__":
    sys.exit(main())
