#!/usr/bin/env python3
r"""Start Groundwork from wherever this folder is.

Download the repository, open the folder, run this. Nothing is installed outside
it, no paths need editing, and the only prerequisite is Python 3.11 or newer.

    Windows   double-click start.bat   (or: py start.py)
    macOS     double-click start.command
    Linux     ./start.sh

What it does, in order, skipping anything already done:

    1. checks the Python version
    2. creates .venv inside this folder
    3. installs requirements.txt into it
    4. starts the server and opens a browser

STDLIB ONLY, deliberately. This is the script that runs BEFORE anything is
installed, so importing a dependency here would be a chicken-and-egg failure
with a confusing traceback.

The app works with no data at all -- the map needs its layers fetched and the
condo screen needs its pipeline run, and it will tell you which are missing
rather than failing. `--fetch` does the downloads that need no credentials.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PORT = 8012
URL = f"http://127.0.0.1:{PORT}"
MIN_PYTHON = (3, 11)


def say(msg, kind="·"):
    print(f" {kind}  {msg}", flush=True)


def venv_python() -> Path:
    """The interpreter inside .venv, whatever this platform calls it."""
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def check_python():
    if sys.version_info < MIN_PYTHON:
        need = ".".join(str(n) for n in MIN_PYTHON)
        have = platform.python_version()
        raise SystemExit(
            f"\n Groundwork needs Python {need} or newer; this is {have}.\n"
            f" Install a current Python from https://www.python.org/downloads/ and\n"
            f" run this again. On Windows, tick 'Add python.exe to PATH' in the\n"
            f" installer.\n")


def ensure_venv(force=False):
    """A virtual environment inside the app folder.

    Inside, not shared: a download that installs packages into the system Python
    is a download that breaks something else on the machine.
    """
    py = venv_python()
    if force and VENV.exists():
        say("removing the old environment")
        shutil.rmtree(VENV)
        py = venv_python()
    if py.exists():
        return py
    say("creating a Python environment in .venv (once, about a minute)")
    venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    if not py.exists():
        raise SystemExit(
            f"\n Could not create a virtual environment at {VENV}.\n"
            f" On Debian or Ubuntu this usually means python3-venv is missing:\n"
            f"   sudo apt install python3-venv\n")
    return py


def stamp() -> Path:
    return VENV / ".requirements-stamp"


def ensure_deps(py: Path, force=False):
    """Install requirements when they have changed. The stamp is the file's own
    text, so editing requirements.txt reinstalls and nothing else does."""
    req = ROOT / "requirements.txt"
    want = req.read_text(encoding="utf-8")
    if not force and stamp().exists() and stamp().read_text(encoding="utf-8") == want:
        return
    say("installing dependencies")
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "-r", str(req)])
    if r.returncode != 0:
        raise SystemExit(
            "\n Dependencies failed to install. The usual cause is no internet\n"
            " connection, or a proxy that pip cannot see through. Re-run with\n"
            " --reinstall once that is sorted.\n")
    stamp().write_text(want, encoding="utf-8")


def already_running() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=1.5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def fetch_data(py: Path):
    """The downloads that need no credentials. Each is skipped if its output is
    already there, and a failure in one does not stop the others."""
    jobs = [("map layers (~53MB)", "scripts/fetch_layers.py"),
            ("Zillow rents", "scripts/fetch_zori.py"),
            ("ACS population", "scripts/fetch_zcta_population.py"),
            ("metro boundaries", "scripts/fetch_cbsa_geo.py")]
    for label, script in jobs:
        say(f"fetching {label}")
        r = subprocess.run([str(py), str(ROOT / script)])
        if r.returncode != 0:
            say(f"{label} failed — the app still runs without it", "!")
    say("done. The condo screen needs its own pipeline; see the README.")


def do_update(check_only=False) -> bool:
    """Update from GitHub using the app's own updater, which is stdlib-only and
    therefore importable before anything is installed."""
    sys.path.insert(0, str(ROOT))
    try:
        from backend.updater import apply, check
    except ImportError as e:
        say(f"the updater is missing from this copy ({e})", "!")
        return False

    st = check()
    if not st.get("ok"):
        say(st.get("error", "could not reach GitHub"), "!")
        return check_only          # a failed check should not block a start
    if check_only:
        if st["behind"]:
            say(f"an update is available: {st['latest_message']}")
        elif st["unknown_local"]:
            say("this copy has no version stamp; --update will record one")
        else:
            say("up to date")
        return True

    if st["local_sha"] and not st["behind"]:
        say("already up to date")
        return True

    say("downloading the latest version")
    r = apply()
    if not r.get("ok"):
        say(r.get("error", "update failed"), "!")
        return False
    if not r["updated"]:
        say("already up to date")
        return True
    say(f"{r['changed_count']} file(s) updated"
        + (", your config was merged" if r["merged"] else ""))
    if r.get("backup"):
        say(f"the previous files are in {Path(r['backup']).name}")
    return True


def serve(py: Path, open_browser=True):
    if already_running():
        say(f"already running at {URL}")
        if open_browser:
            import webbrowser
            webbrowser.open(URL)
        return 0

    say(f"starting on {URL}")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.app:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env)

    if open_browser:
        # Wait for it to answer rather than guessing at a delay, so a slow
        # machine does not open a browser onto a connection error.
        for _ in range(60):
            if proc.poll() is not None:
                break
            if already_running():
                import webbrowser
                webbrowser.open(URL)
                break
            time.sleep(0.5)

    print(f"\n    Groundwork is running:  {URL}")
    print( "    Press Ctrl-C here to stop it.\n")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        say("stopping")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true",
                    help="download the latest version from GitHub, then start")
    ap.add_argument("--check-update", action="store_true",
                    help="say whether an update is available, and exit")
    ap.add_argument("--fetch", action="store_true",
                    help="download the public map and market data, then start")
    ap.add_argument("--reinstall", action="store_true",
                    help="rebuild .venv from scratch")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--setup-only", action="store_true",
                    help="install everything and exit without starting")
    args = ap.parse_args()

    print("\n  Groundwork\n  " + "-" * 40)
    check_python()

    # Before the venv, deliberately: an update can change requirements.txt, and
    # installing the old list then the new one is a wasted minute every time.
    if args.update or args.check_update:
        if not do_update(check_only=args.check_update):
            return 1
        if args.check_update:
            return 0

    py = ensure_venv(force=args.reinstall)
    ensure_deps(py, force=args.reinstall)
    if args.fetch:
        fetch_data(py)
    if args.setup_only:
        say("ready. Run this again without --setup-only to start.")
        return 0
    return serve(py, open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main() or 0)
