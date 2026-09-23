"""backend.data_import -- carrying an old install's data\\ into a fresh one.

A fresh download starts with an empty data\\ folder, so without this every
install looked like it had lost the user's scored targets, deal stages and
declarations (they were still in the old folder, which nothing pointed at
any more). The COM half -- reading the old shortcuts -- only runs on
Windows; everything it feeds into is tested here.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import data_import as di  # noqa: E402


def _db(path: Path, rows: int = 1, wal: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE deal_state (group_key TEXT PRIMARY KEY, stage TEXT)")
    con.executemany("INSERT INTO deal_state VALUES (?, ?)",
                    [(f"k{i}", "LOI") for i in range(rows)])
    con.commit()
    con.close()


def _rows(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM deal_state").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def old_new(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "data").mkdir(parents=True)
    (new / "data").mkdir(parents=True)
    return old, new


def test_copies_databases_files_and_subfolders(old_new):
    old, new = old_new
    _db(old / "data" / "prospect.db", rows=3)
    _db(old / "data" / "_shared" / "shared.db", rows=2)
    (old / "data" / "zori_rents.json").write_text('{"a": 1}')
    (old / "data" / "memos").mkdir()
    (old / "data" / "memos" / "m1.pdf").write_bytes(b"%PDF-1.4")

    r = di.import_data(old, new, log=lambda *_: None)

    assert sorted(r["copied"]) == ["_shared/shared.db", "memos/m1.pdf",
                                   "prospect.db", "zori_rents.json"]
    assert _rows(new / "data" / "prospect.db") == 3
    assert _rows(new / "data" / "_shared" / "shared.db") == 2
    assert (new / "data" / "memos" / "m1.pdf").read_bytes() == b"%PDF-1.4"


def test_never_overwrites_a_file_that_has_data(old_new):
    old, new = old_new
    _db(old / "data" / "prospect.db", rows=5)
    _db(new / "data" / "prospect.db", rows=1)      # user already did work here
    (old / "data" / "zcta_pop.json").write_text("old")
    (new / "data" / "zcta_pop.json").write_text("new")

    r = di.import_data(old, new, log=lambda *_: None)

    assert sorted(r["kept"]) == ["prospect.db", "zcta_pop.json"]
    assert _rows(new / "data" / "prospect.db") == 1
    assert (new / "data" / "zcta_pop.json").read_text() == "new"


def test_replaces_an_empty_database_the_first_start_created(old_new):
    old, new = old_new
    _db(old / "data" / "prospect.db", rows=4)
    _db(new / "data" / "prospect.db", rows=0)      # schema only, no rows

    r = di.import_data(old, new, log=lambda *_: None)

    assert r["replaced"] == ["prospect.db"]
    assert _rows(new / "data" / "prospect.db") == 4


def test_a_live_wal_database_comes_across_whole_and_sidecars_are_not_copied(old_new):
    """The old server is usually still running with the DB open in WAL mode:
    recent writes sit in prospect.db-wal, not the main file. A byte copy of
    prospect.db alone would lose them; copying the -wal beside it would
    risk corruption. The backup API gets both right."""
    old, new = old_new
    p = old / "data" / "prospect.db"
    _db(p, rows=1, wal=True)
    held = sqlite3.connect(p)                       # the "old server"
    held.execute("PRAGMA wal_autocheckpoint=0")
    held.execute("INSERT INTO deal_state VALUES ('late', 'Closed')")
    held.commit()
    try:
        assert (old / "data" / "prospect.db-wal").stat().st_size > 0
        r = di.import_data(old, new, log=lambda *_: None)
    finally:
        held.close()

    assert r["copied"] == ["prospect.db"]
    assert _rows(new / "data" / "prospect.db") == 2
    assert not (new / "data" / "prospect.db-wal").exists()
    assert not (new / "data" / "prospect.db-shm").exists()


def test_the_old_folder_is_left_exactly_as_it_was(old_new):
    old, new = old_new
    _db(old / "data" / "prospect.db", rows=2)
    (old / "data" / "x.json").write_text("{}")
    before = {p: p.read_bytes() for p in (old / "data").rglob("*") if p.is_file()}
    di.import_data(old, new, log=lambda *_: None)
    after = {p: p.read_bytes() for p in (old / "data").rglob("*") if p.is_file()}
    assert before == after


def test_skips_a_file_the_disk_cannot_hold(old_new, monkeypatch):
    old, new = old_new
    (old / "data" / "big.json").write_text("x" * 100)

    class Usage:
        free = 50
    monkeypatch.setattr(di.shutil, "disk_usage", lambda _p: Usage)
    logs = []
    r = di.import_data(old, new, log=logs.append)

    assert r["failed"] == ["big.json"]
    assert not (new / "data" / "big.json").exists()
    assert any("free disk" in m for m in logs)


def test_a_failed_copy_leaves_no_half_written_file(old_new, monkeypatch):
    old, new = old_new
    (old / "data" / "a.json").write_text("{}")

    def boom(src, dst):
        Path(dst).write_text("partial")
        raise OSError("disk yanked")
    monkeypatch.setattr(di.shutil, "copy2", boom)
    r = di.import_data(old, new, log=lambda *_: None)

    assert r["failed"] == ["a.json"]
    assert list((new / "data").iterdir()) == []


def test_root_from_launch_ref_reads_every_shortcut_shape():
    got = di.root_from_launch_ref(
        working_dir=r"C:\Apps\Groundwork",
        target=r"C:\Apps\Old\.venv\Scripts\pythonw.exe",
        arguments=r'"C:\Apps\Other Place\launch.py" --server-only')
    assert [str(p).replace("\\", "/") for p in got] == [
        "C:/Apps/Groundwork", "C:/Apps/Other Place", "C:/Apps/Old"]


def test_previous_installs_drops_this_folder_duplicates_and_folders_without_data(tmp_path):
    here, a, b = tmp_path / "here", tmp_path / "a", tmp_path / "b"
    for d in (here, a):
        (d / "data").mkdir(parents=True)
    b.mkdir()                                       # no data\ -- not an install
    got = di.previous_installs([here, a, b, a, tmp_path / "missing"], here)
    assert got == [a]
