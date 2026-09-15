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


def run(cmd, **kw):
    """Run a command and capture everything, so a failure can be REPORTED rather
    than guessed at. The first version of this script told people their pip
    failure was a network problem when it was a missing ensurepip."""
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def has_pip(py: Path) -> bool:
    return run([str(py), "-m", "pip", "--version"]).returncode == 0


def ensure_pip(py: Path):
    """Get pip into the environment, whatever it takes.

    `python -m venv` is supposed to do this, and on a lot of Windows installs it
    quietly does not -- the Microsoft Store build and several corporate images
    ship without a working `ensurepip`, so the venv is created, python.exe is
    there, and pip simply is not. Three escalating attempts, and the real error
    if all three fail.
    """
    if has_pip(py):
        return

    say("the new environment has no pip — repairing")
    r = run([str(py), "-m", "ensurepip", "--upgrade", "--default-pip"])
    if has_pip(py):
        return

    # Last resort: the official bootstrap. urllib, not PowerShell, not curl --
    # this has to work on a bare Windows box.
    say("fetching get-pip.py")
    try:
        with urllib.request.urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=60) as resp:
            script = resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(
            f"\n Could not install pip into {VENV}.\n"
            f"\n ensurepip said:\n   {(r.stderr or r.stdout or 'nothing').strip()[:400]}\n"
            f"\n and downloading get-pip.py failed: {e}\n"
            f"\n This machine's Python cannot build a working environment. The usual\n"
            f" cause is the Microsoft Store version of Python. Install it from\n"
            f" https://www.python.org/downloads/ instead, tick 'Add python.exe to\n"
            f" PATH', then delete the .venv folder and run this again.\n")

    tmp = VENV / "get-pip.py"
    tmp.write_bytes(script)
    g = run([str(py), str(tmp)])
    tmp.unlink(missing_ok=True)
    if has_pip(py):
        return

    raise SystemExit(
        f"\n pip could not be installed into {VENV}.\n"
        f"\n ensurepip said:\n   {(r.stderr or r.stdout or 'nothing').strip()[:300]}\n"
        f"\n get-pip said:\n   {(g.stderr or g.stdout or 'nothing').strip()[:300]}\n"
        f"\n Delete the .venv folder and try again. If it keeps happening, the\n"
        f" Python running this is probably the Microsoft Store build — install\n"
        f" from https://www.python.org/downloads/ instead.\n")


def ensure_venv(force=False):
    """A virtual environment inside the app folder.

    Inside, not shared: a download that installs packages into the system Python
    is a download that breaks something else on the machine.
    """
    py = venv_python()
    if force and VENV.exists():
        say("removing the old environment")
        shutil.rmtree(VENV, ignore_errors=True)
        py = venv_python()

    if not py.exists():
        say("creating a Python environment in .venv (once, about a minute)")
        # Through the interpreter rather than venv.EnvBuilder: a failure here
        # returns a message worth printing instead of a traceback.
        r = run([sys.executable, "-m", "venv", str(VENV)])
        if not py.exists():
            raise SystemExit(
                f"\n Could not create a virtual environment at {VENV}.\n"
                f"\n python -m venv said:\n   {(r.stderr or r.stdout or 'nothing').strip()[:400]}\n"
                f"\n On Debian or Ubuntu this usually means python3-venv is missing:\n"
                f"   sudo apt install python3-venv\n")

    ensure_pip(py)
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
    r = run([str(py), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(req)])
    if r.returncode != 0:
        # Print what pip actually said. Guessing at the cause is how the first
        # version of this reported a missing ensurepip as a network outage.
        raise SystemExit(
            "\n Dependencies failed to install. pip said:\n\n"
            + "\n".join("   " + ln for ln in
                        (r.stderr or r.stdout or "nothing").strip().splitlines()[-12:])
            + "\n\n Then re-run. --reinstall rebuilds .venv from scratch.\n")
    stamp().write_text(want, encoding="utf-8")


def whoever_is_on_the_port(port: int) -> dict | None:
    """Identify what is answering, not just that something is.

    An older install auto-starting at logon holds 8012. Treating that as "we are
    already running" and opening a browser onto it means the folder you just
    downloaded looks broken while a different copy shows you stale code -- which
    is exactly what happens with a stale basemap and no way to tell.
    """
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/api/instance", timeout=2) as r:
            import json
            d = json.loads(r.read())
            return {"root": d.get("root"), "pid": d.get("pid"), "known": True}
    except urllib.error.HTTPError:
        # Something is there and serving, but too old to have /api/instance.
        return {"root": None, "pid": None, "known": False}
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        with urllib.request.urlopen(base, timeout=2):
            return {"root": None, "pid": None, "known": False}
    except (urllib.error.URLError, OSError):
        return None


def free_port(start_at: int, tries: int = 12) -> int | None:
    import socket
    for port in range(start_at, start_at + tries):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


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


def serve(py: Path, open_browser=True, port=PORT):
    import webbrowser

    holder = whoever_is_on_the_port(port)
    if holder is not None:
        same = holder["root"] and Path(holder["root"]).resolve() == ROOT
        if same:
            say(f"this copy is already running at http://127.0.0.1:{port}")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{port}")
            return 0

        # Something else has the port. Say so plainly and move aside rather than
        # opening a browser onto another install.
        who = holder["root"] or "an older version, which has no way to identify itself"
        say(f"port {port} is taken by {who}", "!")
        alt = free_port(port + 1)
        if alt is None:
            raise SystemExit(
                f"\n Port {port} is in use by another copy of Groundwork and no nearby\n"
                f" port is free. Close the other one -- if it starts at logon, run\n"
                f"   .venv\\Scripts\\python.exe install.py --uninstall\n"
                f" in that folder -- then run this again.\n")
        say(f"starting this copy on {alt} instead")
        port = alt

    url = f"http://127.0.0.1:{port}"
    say(f"starting on {url}")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "backend.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env)

    if open_browser:
        # Wait for it to answer rather than guessing at a delay, so a slow
        # machine does not open a browser onto a connection error.
        for _ in range(60):
            if proc.poll() is not None:
                break
            h = whoever_is_on_the_port(port)
            if h and (not h["root"] or Path(h["root"]).resolve() == ROOT):
                webbrowser.open(url)
                break
            time.sleep(0.5)

    print(f"\n    Groundwork is running:  {url}")
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
    ap.add_argument("--port", type=int, default=PORT,
                    help=f"serve on this port instead of {PORT}")
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
    return serve(py, open_browser=not args.no_browser, port=args.port)


if __name__ == "__main__":
    sys.exit(main() or 0)
