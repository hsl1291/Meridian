"""Read-only access to the shared store at C:\\Apps\\_shared\\shared.db.

The store holds the datasets too large to ship inside the app folder:

  national market intelligence   market, county, county_pop, county_permits,
                                 county_wage, metro_price, migration_flow
  Miami-Dade parcel + owner roll nal_condo_unit (391k units), pa_parcel (585k)

The ingest scripts under scripts/prospect/ are the producer. This module is the
map side's reader and opens the file with mode=ro, so a map request can never
modify ingested data even by accident.

Everything here degrades to empty results when the store is absent, so the app
still runs on a machine where the store has never been built.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

from .shared_paths import shared_db




SHARED_DB = shared_db()

_local = threading.local()


def available() -> bool:
    return SHARED_DB.exists()


def _con() -> sqlite3.Connection | None:
    """One read-only connection per thread. FastAPI runs sync endpoints in a
    worker pool, and a sqlite3 connection may not cross threads."""
    if not SHARED_DB.exists():
        return None
    con = getattr(_local, "con", None)
    if con is None:
        try:
            con = sqlite3.connect(
                f"file:{SHARED_DB.as_posix()}?mode=ro", uri=True, timeout=5.0)
            con.row_factory = sqlite3.Row
        except sqlite3.Error:
            return None
        _local.con = con
    return con


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    con = _con()
    if con is None:
        return []
    try:
        return [dict(r) for r in con.execute(sql, params)]
    except sqlite3.Error:
        return []


# ── national market intelligence ────────────────────────────────────────────

def markets(limit: int = 100, min_pop: int = 0, stage: str | None = None,
            state: str | None = None) -> list[dict]:
    sql = ("SELECT cbsa, name, states, population, net_mig_total_rate, "
           "inflow_agi_per_return, permits_per_1k, zhvi, zhvi_3yr, "
           "price_to_income, avg_annual_pay, score, stage "
           "FROM market WHERE population >= ?")
    p: list = [min_pop]
    if stage:
        sql += " AND stage = ?"
        p.append(stage)
    if state:
        sql += " AND states LIKE ?"
        p.append(f"%{state}%")
    sql += " ORDER BY score DESC LIMIT ?"
    p.append(limit)
    return _rows(sql, tuple(p))


def market(cbsa: str) -> dict | None:
    r = _rows("SELECT * FROM market WHERE cbsa = ?", (cbsa,))
    return r[0] if r else None


def market_for_county(fips: str) -> dict | None:
    """The market a county rolls up to -- lets a parcel click show the metro
    score for wherever it happens to be, anywhere in the US."""
    r = _rows(
        "SELECT m.* FROM county c JOIN market m ON m.cbsa = c.cbsa "
        "WHERE c.fips = ?", (fips,))
    return r[0] if r else None


# ── Miami-Dade parcel + owner roll ──────────────────────────────────────────

def owner_units(name: str, limit: int = 60) -> list[dict]:
    """Condo units whose owner matches `name`. Indexed on owner_norm, so this
    answers in milliseconds where the live ArcGIS owner query takes seconds --
    and it covers condo UNITS, which the parcel services do not carry at all."""
    nm = " ".join((name or "").upper().split())
    if len(nm) < 3:
        return []
    return _rows(
        "SELECT folio, owner_name, phy_addr1, phy_city, phy_zip, act_yr_blt, "
        "jv, tot_lvg_area, sale_prc1, sale_yr1, is_entity, is_absentee "
        "FROM nal_condo_unit WHERE owner_norm LIKE ? "
        "ORDER BY jv DESC LIMIT ?", (f"%{nm}%", limit))


def owner_rollup(name: str) -> dict | None:
    """Portfolio summary for an owner: how many units, where, what they're
    worth. The question a broker actually asks after seeing one match."""
    nm = " ".join((name or "").upper().split())
    if len(nm) < 3:
        return None
    r = _rows(
        "SELECT count(*) units, sum(jv) total_jv, "
        "count(DISTINCT group_key) buildings, "
        "sum(is_absentee) absentee, max(is_entity) entity "
        "FROM nal_condo_unit WHERE owner_norm LIKE ?", (f"%{nm}%",))
    if not r or not r[0]["units"]:
        return None
    out = r[0]
    out["name"] = nm
    out["top_buildings"] = _rows(
        "SELECT group_key, count(*) units, min(phy_addr1) addr, min(phy_city) city "
        "FROM nal_condo_unit WHERE owner_norm LIKE ? "
        "GROUP BY group_key ORDER BY units DESC LIMIT 8", (f"%{nm}%",))
    return out


def parcel_by_folio(folio: str) -> dict | None:
    r = _rows("SELECT * FROM pa_parcel WHERE folio = ?", (str(folio).strip(),))
    return r[0] if r else None


def units_in_building(group_key: str, limit: int = 500) -> list[dict]:
    """Every unit sharing a building (first 9 folio digits)."""
    return _rows(
        "SELECT folio, owner_name, phy_addr1, jv, tot_lvg_area, sale_prc1, "
        "sale_yr1, is_absentee FROM nal_condo_unit WHERE group_key = ? "
        "ORDER BY folio LIMIT ?", (group_key, limit))


def stats() -> dict:
    """What the shared store actually contains -- surfaced so the UI can say
    'not available' rather than silently showing nothing."""
    if not available():
        return {"available": False, "path": str(SHARED_DB)}
    out = {"available": True, "path": str(SHARED_DB)}
    for t in ("market", "county", "nal_condo_unit", "pa_parcel"):
        r = _rows(f"SELECT count(*) n FROM {t}")
        out[t] = r[0]["n"] if r else 0
    return out
