"""Keep the suite idempotent.

Merely querying a condo route opens `data/prospect.db`, and sqlite3.connect
CREATES the file. Left behind, that flips the install into the built-but-empty
state and the next run silently skips the fresh-clone assertions. So anything
the session creates, the session removes -- and anything that was already there
is left strictly alone, because it is somebody's real screen.
"""
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = [ROOT / "data" / "prospect.db",
             ROOT / "data" / "prospect.db-shm",
             ROOT / "data" / "prospect.db-wal",
             # On POSIX the Windows fallback in _shared_root() is a RELATIVE
             # path, so an unconfigured run creates a directory named
             # 'C:\\Apps\\_shared' in the working directory instead of failing.
             ROOT / r"C:\Apps\_shared"]


@pytest.fixture(autouse=True, scope="session")
def _leave_no_trace():
    pre = {p for p in ARTIFACTS if p.exists()}
    yield
    for p in ARTIFACTS:
        if p in pre or not p.exists():
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
