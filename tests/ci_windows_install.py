r"""End-to-end check of install.bat on a real Windows machine (the CI job).

Not collected by pytest (no test_ prefix): it changes the machine -- a
desktop shortcut, a logon shortcut, a scheduled task -- which is fine on a
throwaway CI runner and not fine on a developer's laptop.

    python tests\ci_windows_install.py setup    fake an older install to migrate from
    install.bat < nul                           the real thing, as a double-click runs it
    python tests\ci_windows_install.py verify   check it did what it says
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = Path.home() / "OldGroundworkInstall"
PORT = 8012


def _install_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("install_mod", ROOT / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setup() -> None:
    (OLD / "data" / "memos").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OLD / "data" / "prospect.db")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS deal_state (group_key TEXT PRIMARY KEY, stage TEXT)")
    con.execute("INSERT OR REPLACE INTO deal_state VALUES ('ci-building', 'LOI')")
    con.commit()
    con.close()
    (OLD / "data" / "memos" / "ci-memo.pdf").write_bytes(b"%PDF-1.4 ci")

    inst = _install_mod()
    inst.create_shortcut(inst._desktop() / "Groundwork.lnk", Path(sys.executable),
                         f'"{OLD / "launch.py"}"', "old install", None, working_dir=OLD)
    print(f"fake old install at {OLD}, shortcut on the desktop")


def verify() -> int:
    failures = []
    inst = _install_mod()

    db = ROOT / "data" / "prospect.db"
    try:
        con = sqlite3.connect(db)
        row = con.execute("SELECT stage FROM deal_state WHERE group_key='ci-building'").fetchone()
        con.close()
        if row != ("LOI",):
            failures.append(f"old deal stage not imported into {db}: {row}")
    except sqlite3.Error as e:
        failures.append(f"{db}: {e}")
    if not (ROOT / "data" / "memos" / "ci-memo.pdf").exists():
        failures.append("old memo not imported")

    if (inst._desktop() / "Groundwork.lnk").exists():
        failures.append("old Groundwork shortcut was not removed")
    desk, start = inst._paths()
    for p in (desk, start):
        if not p.exists():
            failures.append(f"missing shortcut {p}")
        else:
            got = inst.read_shortcut(p)
            if not inst.data_import.same_folder(got.get("working_dir", ""), ROOT):
                failures.append(f"{p.name} starts in {got.get('working_dir')!r}, not {ROOT}")
    if inst._schtasks("/Query", "/TN", inst.TASK_NAME).returncode != 0:
        failures.append(f"scheduled task '{inst.TASK_NAME}' not registered")

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/instance", timeout=10) as r:
            root = json.loads(r.read().decode("utf-8")).get("root")
        if not inst.data_import.same_folder(root, ROOT):
            failures.append(f"port {PORT} is served from {root}, not {ROOT}")
    except Exception as e:
        failures.append(f"server not answering on {PORT}: {e}")

    for f in failures:
        print("FAIL:", f)
    print("install verified" if not failures else f"{len(failures)} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
        sys.exit(0)
    if cmd == "verify":
        sys.exit(verify())
    sys.exit(__doc__)
