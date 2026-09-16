"""/api/selftest and /api/acquisitions/health both had the same shape of
connection leak: db() was opened inside a `try`, and `con.close()` sat on its
own line rather than in a `finally` -- so any exception that was not the one
specific type the surrounding `except` caught (a missing table for selftest,
literally anything for health) skipped the close and leaked a sqlite3
connection. Neither route is hit often, but health-check endpoints tend to be
polled, and a fresh or partially-built install is exactly the state where the
"table missing" exception each route was written to expect could instead
surface as some other error and slip past the close.

Fixed by moving the close into a `finally`, so the connection is released
whether the route returns normally, hits the exception it expects, or hits
one it does not.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.prospect.routes as routes_mod  # noqa: E402


class _SpyConnection:
    """Wraps a real sqlite3 connection and records whether close() ran, so a
    test can prove cleanup happened without depending on GC timing."""

    def __init__(self, con):
        self._con = con
        self.closed = False

    def execute(self, *a, **kw):
        return self._con.execute(*a, **kw)

    def close(self):
        self.closed = True
        self._con.close()


def _empty_db():
    """No tables at all -- every SELECT COUNT(*) FROM <table> raises
    sqlite3.OperationalError, the exact state of a fresh, unbuilt install."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con


def test_selftest_closes_its_connection_on_the_happy_path():
    spy = _SpyConnection(_empty_db())
    orig_db = routes_mod.db
    routes_mod.db = lambda: spy
    try:
        out = routes_mod.selftest()
    finally:
        routes_mod.db = orig_db
    assert out["ok"] is False  # every table missing on a bare db
    assert spy.closed, "connection was not closed after selftest() ran normally"


def test_selftest_still_closes_its_connection_when_a_table_check_misbehaves():
    """The actual regression: something other than the missing-table
    OperationalError the per-table loop expects (here, a bare RuntimeError
    from execute() on the second table) must still leave the connection
    closed, not leaked, even though it propagates out of the route."""
    real = _empty_db()
    spy = _SpyConnection(real)
    calls = {"n": 0}
    orig_execute = spy.execute

    def flaky_execute(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated unexpected failure")
        return orig_execute(*a, **kw)

    spy.execute = flaky_execute
    orig_db = routes_mod.db
    routes_mod.db = lambda: spy
    try:
        try:
            routes_mod.selftest()
        except RuntimeError:
            pass  # expected: this is the exception that used to skip the close
        else:
            raise AssertionError("expected the simulated RuntimeError to propagate")
    finally:
        routes_mod.db = orig_db
    assert spy.closed, "connection leaked when a table check raised something other than OperationalError"


def test_health_closes_its_connection_on_the_happy_path():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE target (group_key TEXT)")
    con.execute("INSERT INTO target VALUES ('a'), ('b')")
    spy = _SpyConnection(con)
    orig_db = routes_mod.db
    routes_mod.db = lambda: spy
    try:
        out = routes_mod.health()
    finally:
        routes_mod.db = orig_db
    assert out == {"ok": True, "targets": 2}
    assert spy.closed, "connection was not closed after health() ran normally"


def test_health_still_closes_its_connection_when_the_query_fails():
    """The actual regression: db() succeeding but the query failing (a fresh
    install with no target table yet, or any other error) used to skip
    con.close() entirely, because it sat on its own line before the except."""
    spy = _SpyConnection(_empty_db())  # no target table -> execute() raises
    orig_db = routes_mod.db
    routes_mod.db = lambda: spy
    try:
        result = routes_mod.health()
    finally:
        routes_mod.db = orig_db
    assert result.status_code == 500
    assert spy.closed, "connection leaked when the health query failed"
