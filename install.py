r"""Groundwork — install / re-install the launcher wiring on this machine.

OPTIONAL. Groundwork runs from its own folder via start.bat; this only adds a
desktop shortcut and starts it at logon for people who want that.

    .venv\Scripts\python.exe install.py             desktop shortcut + auto-start at logon
    .venv\Scripts\python.exe install.py --task      also register a task that re-checks every 15 min
    .venv\Scripts\python.exe install.py --uninstall remove the shortcuts and the task

Groundwork replaces two earlier apps — Sitefolio (the map) and Prospect (the
tables) — so install also clears their desktop and startup shortcuts and their
scheduled task. Their folders and data are left alone; only the wiring that
would start them, or put a second icon on the desktop, is removed.

The app is self-contained: launch.py health-checks over HTTP and only restarts
when the server is actually down, so running it repeatedly is safe.

No PowerShell anywhere. Shortcuts are created through the shell's IShellLink COM
interface via ctypes (stdlib only — this app does not ship pywin32), and the
scheduled task goes through schtasks.exe as the current user, so no elevation is
needed.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import urllib.request
from ctypes import POINTER, byref, c_int, c_void_p
from ctypes.wintypes import BOOL, DWORD, HANDLE, HWND, LPCWSTR, LPWSTR
from pathlib import Path

ROOT = Path(__file__).resolve().parent
def _pythonw() -> Path:
    """Prefer .venv (what start.py builds), then the older hand-made venv. Both
    live inside the app folder, so a shortcut points at the downloaded copy
    rather than a fixed path somebody has to recreate."""
    for env in (".venv", "venv"):
        for exe in ("pythonw.exe", "python.exe"):
            p = ROOT / env / "Scripts" / exe
            if p.exists():
                return p
    return ROOT / ".venv" / "Scripts" / "pythonw.exe"


PYTHONW = _pythonw()
LAUNCH = ROOT / "launch.py"
ICON = ROOT / "frontend" / "static" / "favicon.ico"
APP_NAME = "Groundwork"
TASK_NAME = "Groundwork Server"
PORT = 8012
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/shared/status"

# Apps this one replaces: (desktop shortcut, startup shortcut, scheduled task).
SUPERSEDED = [
    ("Sitefolio.lnk", "Sitefolio (server).lnk", "Sitefolio Server"),
    ("Prospect.lnk", "Prospect (server).lnk", "Prospect Server"),
]

# ── minimal COM plumbing for creating .lnk files ────────────────────────────
ole32 = ctypes.OleDLL("ole32")
shell32 = ctypes.OleDLL("shell32")

CLSCTX_INPROC_SERVER = 1
CSIDL_DESKTOPDIRECTORY = 0x0010
CSIDL_STARTUP = 0x0007


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ole32.CLSIDFromString(text, byref(self))


CLSID_ShellLink = GUID("{00021401-0000-0000-C000-000000000046}")
IID_IShellLinkW = GUID("{000214F9-0000-0000-C000-000000000046}")
IID_IPersistFile = GUID("{0000010B-0000-0000-C000-000000000046}")

# vtable slots (IUnknown occupies 0-2)
QUERY_INTERFACE, RELEASE = 0, 2
SL_SET_DESCRIPTION, SL_SET_WORKING_DIR = 7, 9
SL_SET_ARGUMENTS, SL_SET_ICON, SL_SET_PATH = 11, 17, 20
PF_SAVE = 6


def _invoke(ptr: c_void_p, slot: int, *arg_pairs):
    """Call vtable[slot] on a COM pointer. arg_pairs is (ctype, value) pairs."""
    argtypes = [t for t, _ in arg_pairs]
    args = [v for _, v in arg_pairs]
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    return proto(vtbl[slot])(ptr, *args)


def known_folder(csidl: int) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW.argtypes = [HWND, c_int, HANDLE, DWORD, LPWSTR]
    shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def create_shortcut(path: Path, target: Path, arguments: str = "",
                    description: str = "", icon: Path | None = None,
                    working_dir: Path | None = None) -> None:
    ole32.CoInitialize(None)
    link = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_ShellLink), None, CLSCTX_INPROC_SERVER,
                           byref(IID_IShellLinkW), byref(link))
    try:
        _invoke(link, SL_SET_PATH, (LPCWSTR, str(target)))
        _invoke(link, SL_SET_WORKING_DIR, (LPCWSTR, str(working_dir or ROOT)))
        if arguments:
            _invoke(link, SL_SET_ARGUMENTS, (LPCWSTR, arguments))
        if description:
            _invoke(link, SL_SET_DESCRIPTION, (LPCWSTR, description))
        if icon and icon.exists():
            _invoke(link, SL_SET_ICON, (LPCWSTR, str(icon)), (c_int, 0))

        persist = c_void_p()
        _invoke(link, QUERY_INTERFACE,
                (POINTER(GUID), byref(IID_IPersistFile)),
                (POINTER(c_void_p), byref(persist)))
        try:
            _invoke(persist, PF_SAVE, (LPCWSTR, str(path)), (BOOL, True))
        finally:
            _invoke(persist, RELEASE)
    finally:
        _invoke(link, RELEASE)

    # Verify rather than assume. A shortcut can be silently dropped (sync
    # clients and Controlled Folder Access both do it), which would leave the
    # installer claiming success for a file that is gone.
    if path.exists():
        print(f"  + {path}")
    else:
        print(f"  ! shortcut did not persist: {path}")


# ── install steps ───────────────────────────────────────────────────────────

def _desktop() -> Path:
    """The real desktop. When OneDrive has taken it over — the usual setup on a
    managed machine — CSIDL_DESKTOPDIRECTORY can still point at the local
    profile folder nobody looks at, so prefer a redirected OneDrive Desktop when
    one exists."""
    for d in sorted(Path.home().glob("OneDrive*/Desktop")):
        if d.is_dir():
            return d
    return known_folder(CSIDL_DESKTOPDIRECTORY)


def _paths() -> tuple[Path, Path]:
    return (_desktop() / f"{APP_NAME}.lnk",
            known_folder(CSIDL_STARTUP) / f"{APP_NAME} (server).lnk")


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _drop_task(name: str) -> bool:
    if _schtasks("/Query", "/TN", name).returncode != 0:
        return False
    _schtasks("/Delete", "/TN", name, "/F")
    print(f"  - scheduled task '{name}'")
    return True


def clear_superseded() -> None:
    """Remove the wiring for the apps Groundwork replaces."""
    desktop, startup = _desktop(), known_folder(CSIDL_STARTUP)
    found = False
    for desk_lnk, start_lnk, task in SUPERSEDED:
        for p in (desktop / desk_lnk, startup / start_lnk):
            if p.exists():
                p.unlink()
                print(f"  - {p}")
                found = True
        found = _drop_task(task) or found
    if not found:
        print("  (nothing left over from Sitefolio or Prospect)")


def uninstall() -> int:
    for p in _paths():
        if p.exists():
            p.unlink()
            print(f"  - {p}")
    _drop_task(TASK_NAME)
    print("\nUninstalled. The app folder and its data are untouched.")
    return 0


def install(with_task: bool) -> int:
    if not PYTHONW.exists():
        print(f"ERROR: {PYTHONW} not found.")
        print("Run start.py once first — it creates .venv inside this folder.")
        return 1

    print(f"Clearing the wiring for the apps {APP_NAME} replaces...")
    clear_superseded()

    print(f"\nInstalling {APP_NAME} launcher wiring...")
    desktop_lnk, startup_lnk = _paths()

    # 1. Desktop shortcut — opens the app in an Edge app window.
    create_shortcut(desktop_lnk, PYTHONW, f'"{LAUNCH}"',
                    "Groundwork - parcels, zoning, rents and condo takeovers", ICON)

    # 2. Start the server at logon, no window, so the app answers immediately.
    #    pythonw.exe is console-less on its own.
    create_shortcut(startup_lnk, PYTHONW, f'"{LAUNCH}" --server-only',
                    "Groundwork server (background)", ICON)

    # 3. Optional: a task that re-checks every 15 minutes. Runs as the current
    #    user, so it needs no elevation.
    if with_task:
        r = _schtasks("/Create", "/TN", TASK_NAME,
                      "/TR", f'"{PYTHONW}" "{LAUNCH}" --server-only',
                      "/SC", "MINUTE", "/MO", "15", "/F")
        if r.returncode == 0:
            print(f"  + scheduled task '{TASK_NAME}' (re-checks every 15 min)")
        else:
            print(f"  ! could not register the task: {(r.stderr or r.stdout).strip()}")
            print("    The logon shortcut is installed, so the app still starts at sign-in.")

    print("\nStarting the server...")
    subprocess.run([str(PYTHONW), str(LAUNCH), "--server-only"], cwd=str(ROOT))

    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as r:
            up = r.status == 200
    except Exception:
        up = False
    if up:
        print(f"Running at http://127.0.0.1:{PORT}")
    else:
        print(rf"WARNING: server did not come up -- check {ROOT}\logs\server.err.log")
    return 0


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        sys.exit(uninstall())
    sys.exit(install(with_task="--task" in sys.argv))
