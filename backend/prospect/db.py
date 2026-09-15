"""SQLite schema + connection for the acquisitions side of Groundwork.

Three layers:
  raw      nal_condo_unit, dbpr_association, pa_parcel   (ingested; never hand-edited)
  derived  condo_group                                   (rebuilt by build_targets.py)
  scored   target                                         (the ranked list the UI reads)

Everything derived is dropped and rebuilt on each run, so a bad heuristic is one
script re-run away from being fixed. Raw tables are only replaced by their own
ingest script.

Source-of-truth note: the condo pipeline runs on the FDOR NAL tax roll
(nal_condo_unit), NOT on the Miami-Dade PaParcelView service. PaParcelView
contains only platted land parcels -- it has no condo unit folios at all, and
its CONDO_FLAG is populated on 91 of 595,849 rows. pa_parcel is kept because the
land/parent parcels are useful for site-level enrichment later, but nothing in
the target ranking depends on it.
"""
from pathlib import Path
import os
import re
import sqlite3

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "prospect.db"

# ── shared store ────────────────────────────────────────────────────────────
# The national county/metro tables and the Miami-Dade parcel + owner roll are
# far larger than everything else the app holds, so they live in ONE database
# outside the app folder, attached here as `shared`. The ingest scripts write
# them through this connection; the map side reads the same file read-only via
# backend/shared_data.py.
#
# SQLite resolves an unqualified table name against `main` first, then attached
# databases -- so every existing query keeps working unchanged, provided these
# tables are NOT also created in main (see _split_schema).

def _shared_root() -> Path:
    r"""Locate the shared store. Checked in order so a copied install works
    wherever it is unzipped, without anyone editing a path:
      1. APPS_SHARED env var (explicit wins)
      2. a `_shared` folder beside this app's folder  <- the shareable layout
      3. C:\Apps\_shared                              <- the original install
    """
    env = os.environ.get("APPS_SHARED")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parent.parent.parent.parent / "_shared"
    if sibling.is_dir():
        return sibling
    return Path(r"C:\Apps\_shared")


SHARED_DB = Path(os.environ.get("APPS_SHARED_DB")
                 or _shared_root() / "shared.db")

SHARED_TABLES = {
    # national market intelligence (Module C)
    "county", "county_pop", "migration_flow", "county_permits",
    "county_wage", "metro_price", "market",
    # Miami-Dade parcel + owner roll
    "nal_condo_unit", "pa_parcel",
}

SCHEMA = """
-- ── raw ────────────────────────────────────────────────────────────────────
-- FDOR NAL 2026 preliminary tax roll, Dade county, DOR_UC='004' (condominium).
-- 391,210 unit-level rows, 99.9% carrying an owner name.
CREATE TABLE IF NOT EXISTS nal_condo_unit (
    folio        TEXT PRIMARY KEY,   -- PARCEL_ID, 13 digits
    group_key    TEXT,               -- first 9 folio digits; units of one condo share it
    owner_name   TEXT,
    owner_norm   TEXT,
    owner_addr1  TEXT,
    owner_addr_norm TEXT,
    owner_city   TEXT,
    owner_state  TEXT,
    owner_zip    TEXT,
    phy_addr1    TEXT,
    phy_addr_norm TEXT,              -- unit designator stripped -> building address
    phy_city     TEXT,
    phy_zip      TEXT,
    act_yr_blt   INTEGER,
    eff_yr_blt   INTEGER,
    jv           REAL,               -- just (market) value
    lnd_val      REAL,
    tot_lvg_area REAL,
    sale_prc1    REAL,
    sale_yr1     INTEGER,
    or_book1     TEXT,
    or_page1     TEXT,
    s_legal      TEXT,
    is_entity    INTEGER,            -- owner reads as a company/trust
    is_absentee  INTEGER,            -- mailing address != building address
    county       TEXT,               -- FDOR county name; the roll is per-county
    -- Homestead exemption. FS 718.117 needs 80% approval AND no more than 5%
    -- objecting, and absentee share was standing in for the objection side --
    -- but absentee is a mailing-address comparison, which puts a Brickell
    -- landlord and a retiree in Ohio in the same bucket. Homestead is the legal
    -- fact: it marks the owner-occupant who objects, and whose payout floor is
    -- protected. NULL means the roll's exemption column could not be resolved,
    -- which is not the same as zero (see ingest_nal.py).
    homestead    INTEGER,
    homestead_val REAL
);
CREATE INDEX IF NOT EXISTS ix_nal_group ON nal_condo_unit(group_key);
CREATE INDEX IF NOT EXISTS ix_nal_owner ON nal_condo_unit(owner_norm);
CREATE INDEX IF NOT EXISTS ix_nal_addr  ON nal_condo_unit(phy_addr_norm);

-- DBPR Division of Condominiums registry (Condo_MD.csv), Dade rows only.
-- Carries the one thing the tax roll does not: the declaration's recording date.
CREATE TABLE IF NOT EXISTS dbpr_association (
    project_number   TEXT PRIMARY KEY,
    file_number      TEXT,
    name             TEXT,
    name_norm        TEXT,
    county           TEXT,
    address          TEXT,
    addr_norm        TEXT,
    address_city     TEXT,
    address_zip      TEXT,
    units            INTEGER,
    recorded_date    TEXT,
    recorded_year    INTEGER,
    primary_status   TEXT,
    secondary_status TEXT,
    me_number        TEXT,
    me_name          TEXT,
    me_addr          TEXT,
    me_city          TEXT,
    me_state         TEXT,
    me_zip           TEXT
);
CREATE INDEX IF NOT EXISTS ix_dbpr_namenorm ON dbpr_association(name_norm);
CREATE INDEX IF NOT EXISTS ix_dbpr_addrnorm ON dbpr_association(addr_norm);

-- Miami-Dade PaParcelView: platted LAND parcels only (no condo units).
CREATE TABLE IF NOT EXISTS pa_parcel (
    folio TEXT PRIMARY KEY, subdivision TEXT, owner1 TEXT, mail_addr1 TEXT,
    mail_city TEXT, mail_state TEXT, mail_zip TEXT, year_built INTEGER,
    assessed_val REAL, lot_size REAL, site_addr TEXT, site_unit TEXT,
    dor_code TEXT, legal TEXT, sale_date TEXT, sale_price REAL,
    -- State Plane Florida East (EPSG:2236), feet -- as published by the county
    x_coord REAL, y_coord REAL
);
CREATE INDEX IF NOT EXISTS ix_parcel_subdiv ON pa_parcel(subdivision);

-- ── derived ────────────────────────────────────────────────────────────────
-- One row per condo, grouped from nal_condo_unit by the 9-digit folio prefix.
CREATE TABLE IF NOT EXISTS condo_group (
    group_key        TEXT PRIMARY KEY,
    unit_folios      INTEGER,
    -- Condo name recovered from the LAND parcel legal in pa_parcel (99.8% of
    -- groups have one). The NAL's own S_LEGAL is abbreviated and nameless, so
    -- this is what makes name-based matching to the DBPR registry possible.
    condo_name_legal TEXT,
    name_norm        TEXT,
    addr_primary     TEXT,      -- modal building address
    addr_all         TEXT,      -- '|'-joined distinct addresses (garden complexes span many)
    addr_count       INTEGER,
    city             TEXT,
    zip              TEXT,
    act_yr_blt       INTEGER,   -- modal non-zero actual year built
    jv_total         REAL,
    jv_per_unit      REAL,
    lnd_val_total    REAL,
    lnd_val_per_unit REAL,
    avg_living_area  REAL,
    -- ownership concentration
    top_owner        TEXT,
    top_owner_units  INTEGER,
    top_owner_pct    REAL,
    top_mail_addr    TEXT,
    top_mail_units   INTEGER,
    top_mail_pct     REAL,
    distinct_owners  INTEGER,
    -- units held by the association itself (foreclosures, common elements).
    -- Excluded from top_owner: the HOA is not an acquirer, and counting it
    -- would fake a bulk-owner signal on ordinary buildings.
    assoc_owned_units INTEGER,
    assoc_owned_pct   REAL,
    -- investor / absentee signals
    absentee_units     INTEGER,
    absentee_pct       REAL,
    out_of_state_units INTEGER,
    out_of_state_pct   REAL,
    corporate_units    INTEGER,
    corporate_pct      REAL,
    -- recent transaction velocity (are units trading into one hand right now?)
    sales_last_3yr     INTEGER,
    entity_sales_last_3yr INTEGER,
    -- WGS84, reprojected from the site's land parcel; puts the building on the map
    lon REAL,
    lat REAL,
    -- Appended after lon/lat on purpose: build_targets.py inserts positionally,
    -- so new columns go at the end and the row tuple grows at the end too. A
    -- test asserts the two stay the same length.
    homestead_units INTEGER,
    homestead_pct   REAL
);
CREATE INDEX IF NOT EXISTS ix_group_addr ON condo_group(addr_primary);

-- ── scored ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS target (
    group_key        TEXT PRIMARY KEY,
    project_number   TEXT,
    match_method     TEXT,      -- address | address+units | unmatched
    match_confidence REAL,
    condo_name       TEXT,      -- from DBPR when matched, else the address
    addr_primary     TEXT,
    city             TEXT,
    units_dbpr       INTEGER,
    units_nal        INTEGER,
    recorded_year    INTEGER,   -- declaration recorded (DBPR)
    act_yr_blt       INTEGER,
    age_years        INTEGER,
    milestone_due    INTEGER,   -- 1 if 30+ years old (FS 553.899, verify)
    jv_per_unit      REAL,
    lnd_val_per_unit REAL,
    top_owner        TEXT,
    top_owner_pct    REAL,
    top_mail_pct     REAL,
    absentee_pct     REAL,
    out_of_state_pct REAL,
    corporate_pct    REAL,
    entity_sales_last_3yr INTEGER,
    -- stage 1 scoring
    score               REAL,
    score_age           REAL,
    score_scale         REAL,
    score_concentration REAL,
    score_absentee      REAL,
    -- stage 2 (declaration review) -- null until a declaration is pulled
    termination_threshold  TEXT,
    kaufman_original       INTEGER,
    kaufman_by_amendment   INTEGER,
    declaration_or_book    TEXT,
    declaration_or_page    TEXT,
    declaration_source_url TEXT,
    stage2_verified        INTEGER DEFAULT 0,
    stage2_notes           TEXT,
    lon REAL,
    lat REAL,
    -- Resistance to termination. Scored and stored, but deliberately NOT folded
    -- into `score` yet: adding a fifth term without re-deriving the weights would
    -- silently move every building in the app. That is Phase 1.4, and it wants
    -- the labelled set of actual terminations first.
    homestead_pct    REAL,
    score_resistance REAL,
    -- ── movement, against the previous roll vintage ────────────────────────
    -- NULL on the first build, and on any building absent from the prior roll.
    -- That is "no comparison available", which is not the same as "no change".
    prior_roll_year      INTEGER,
    conc_delta           REAL,   -- change in max(top_owner_pct, top_mail_pct)
    owners_delta         INTEGER,-- change in distinct_owners; negative = consolidating
    corporate_pct_delta  REAL,
    top_owner_changed    INTEGER,
    -- Concentration rising while the owner count falls. Either alone is noise;
    -- together they are somebody buying the building.
    assembly_flag        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_target_score ON target(score DESC);
CREATE INDEX IF NOT EXISTS ix_target_assembly ON target(conc_delta DESC);

-- ── history ───────────────────────────────────────────────────────────────
-- build_targets.py opens with DELETE FROM condo_group and DELETE FROM target,
-- so every rebuild destroyed the prior state and the app could only ever answer
-- "who owns a lot of this building today" -- never "who is BUYING it".
--
-- A building at 45% single-owner has probably already been found by somebody who
-- knows what they are doing. One that went 6% to 19% since the last roll is the
-- one to be early on, and the flat screen ranks the first one higher.
--
-- One row per building per roll vintage, written on every rebuild and never
-- deleted. Group-level metrics only: the per-unit roster would be another
-- 391,210 rows a vintage, and concentration, owner count and corporate share
-- are enough to see a building being assembled.
CREATE TABLE IF NOT EXISTS target_snapshot (
    group_key        TEXT,
    roll_year        INTEGER,
    roll_type        TEXT,
    captured         TEXT,      -- ISO date of the rebuild that wrote this
    unit_folios      INTEGER,
    top_owner        TEXT,
    top_owner_units  INTEGER,
    top_owner_pct    REAL,
    top_mail_addr    TEXT,
    top_mail_pct     REAL,
    distinct_owners  INTEGER,
    corporate_pct    REAL,
    absentee_pct     REAL,
    entity_sales_last_3yr INTEGER,
    homestead_pct    REAL,
    PRIMARY KEY (group_key, roll_year)
);
CREATE INDEX IF NOT EXISTS ix_snapshot_year ON target_snapshot(roll_year);

-- Provenance: keeps "is this stale?" answerable.
CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, started TEXT, finished TEXT, rows INTEGER, note TEXT
);

-- ═══ MODULE C — market screener ════════════════════════════════════════════
-- National, county-level, aggregated to CBSA (metro). South Florida is the
-- default view, not a limit: every table is nationwide so any market can be
-- selected. Sources are all free federal bulk files plus Zillow's public CSV.

-- County -> CBSA crosswalk (Census delineation file).
CREATE TABLE IF NOT EXISTS county (
    fips       TEXT PRIMARY KEY,   -- 5-digit state+county
    name       TEXT,
    state      TEXT,
    state_fips TEXT,
    cbsa       TEXT,
    cbsa_name  TEXT,
    cbsa_type  TEXT                -- Metropolitan | Micropolitan
);
CREATE INDEX IF NOT EXISTS ix_county_cbsa ON county(cbsa);

-- Census population estimates: the components of change. domestic_mig is the
-- outcome variable the whole screen is trying to predict early.
CREATE TABLE IF NOT EXISTS county_pop (
    fips TEXT, year INTEGER, population INTEGER,
    births INTEGER, deaths INTEGER,
    domestic_mig INTEGER, international_mig INTEGER, natural_change INTEGER,
    PRIMARY KEY (fips, year)
);

-- IRS SOI county-to-county migration. The only source that attaches INCOME to
-- movers: agi is thousands of dollars, so agi/returns = average AGI per
-- household actually moving. This is measured, not inferred.
CREATE TABLE IF NOT EXISTS migration_flow (
    dest_fips TEXT, origin_fips TEXT, year INTEGER,
    direction TEXT,                -- in | out
    returns INTEGER,               -- ~households
    exemptions INTEGER,            -- ~people
    agi REAL,                      -- $000s
    PRIMARY KEY (dest_fips, origin_fips, year, direction)
);
CREATE INDEX IF NOT EXISTS ix_flow_dest ON migration_flow(dest_fips, year);

-- Census Building Permits Survey. Permits per capita is the supply-response
-- proxy: a market with strong demand drivers and LOW permitting is early.
CREATE TABLE IF NOT EXISTS county_permits (
    fips TEXT, year INTEGER,
    units_total INTEGER, units_1 INTEGER, units_2_4 INTEGER, units_5plus INTEGER,
    PRIMARY KEY (fips, year)
);

-- BLS QCEW annual averages by county and NAICS sector. Drives the pay side of
-- the job-announcement model.
CREATE TABLE IF NOT EXISTS county_wage (
    fips TEXT, year INTEGER, naics TEXT, industry TEXT,
    employment INTEGER, avg_annual_pay REAL,
    PRIMARY KEY (fips, year, naics)
);
CREATE INDEX IF NOT EXISTS ix_wage_naics ON county_wage(naics);

-- Zillow ZHVI by metro, monthly. Price level and price growth.
CREATE TABLE IF NOT EXISTS metro_price (
    cbsa TEXT, month TEXT, zhvi REAL,
    PRIMARY KEY (cbsa, month)
);

-- Derived: one scored row per market. Rebuilt by build_markets.py.
CREATE TABLE IF NOT EXISTS market (
    cbsa            TEXT PRIMARY KEY,
    name            TEXT,
    cbsa_type       TEXT,
    states          TEXT,
    counties        INTEGER,
    population      INTEGER,
    pop_cagr_3yr    REAL,
    -- migration. Domestic and international are kept apart deliberately:
    -- Miami-Dade ran -67,418 domestic and +123,835 international in 2024, so a
    -- screen scored on domestic migration alone would rank it near-worthless
    -- while it was in fact absorbing record demand.
    net_domestic_mig      INTEGER,   -- latest year
    net_domestic_mig_rate REAL,      -- per 1,000 residents
    net_dom_mig_3yr       INTEGER,
    net_intl_mig          INTEGER,
    net_intl_mig_rate     REAL,
    net_mig_total         INTEGER,
    net_mig_total_rate    REAL,
    inflow_returns        INTEGER,
    inflow_agi_per_return REAL,      -- avg AGI of arriving households ($)
    outflow_agi_per_return REAL,
    agi_premium_pct       REAL,      -- arrivals vs departures
    top_origins           TEXT,      -- '|' joined "CBSA name:returns:avgAGI"
    -- housing supply + price
    permits_total         INTEGER,
    permits_per_1k        REAL,
    permits_5plus_share   REAL,
    zhvi                  REAL,
    zhvi_yoy              REAL,
    zhvi_3yr              REAL,
    price_to_income       REAL,
    -- jobs
    employment            INTEGER,
    avg_annual_pay        REAL,
    pay_growth_3yr        REAL,
    -- scoring
    score_demand      REAL,   -- migration pull actually happening
    score_income      REAL,   -- quality of the income arriving
    score_affordability REAL, -- cost advantage vs the metros feeding it
    score_jobs        REAL,
    score_headroom    REAL,   -- inverse of supply response + price run-up
    score             REAL,
    stage             TEXT    -- Early | Emerging | Established | Cooling
);
CREATE INDEX IF NOT EXISTS ix_market_score ON market(score DESC);
"""


_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)
_CREATE_INDEX = re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)", re.I)


def _statements(sql: str) -> list[str]:
    """Split DDL into statements. Boundary detection ignores `--` comments,
    two of which contain a semicolon and would otherwise split mid-statement."""
    out, cur = [], []
    for line in sql.splitlines():
        cur.append(line)
        if ";" in re.sub(r"--.*$", "", line):
            out.append("\n".join(cur))
            cur = []
    if any(s.strip() for s in cur):
        out.append("\n".join(cur))
    return [s for s in out if s.strip()]


def _split_schema(sql: str) -> tuple[str, str]:
    """Route each statement to main or the shared DB by the table it touches.
    Keeping one SCHEMA definition and routing by name means the two databases
    can never drift out of sync the way two hand-maintained copies would."""
    main, shared = [], []
    for stmt in _statements(sql):
        t = _CREATE_TABLE.search(stmt)
        i = _CREATE_INDEX.search(stmt)
        if t and t.group(1) in SHARED_TABLES:
            # the schema prefix goes on the table name
            shared.append(_CREATE_TABLE.sub(
                lambda m: m.group(0).replace(m.group(1), f"shared.{m.group(1)}"), stmt, count=1))
        elif i and i.group(2) in SHARED_TABLES:
            # ...and for an index, on the index name, not the table
            shared.append(_CREATE_INDEX.sub(
                lambda m: m.group(0).replace(m.group(1), f"shared.{m.group(1)}", 1), stmt, count=1))
        else:
            main.append(stmt)
    return "\n".join(main), "\n".join(shared)


SCHEMA_MAIN, SCHEMA_SHARED = _split_schema(SCHEMA)


def connect(path: Path = DB_PATH, shared_db: Path = SHARED_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    shared_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("ATTACH DATABASE ? AS shared", (str(shared_db),))
    con.executescript(SCHEMA_MAIN)
    if SCHEMA_SHARED:
        con.executescript(SCHEMA_SHARED)
    _ensure_columns(con)
    return con


# Columns added after the table already existed somewhere. CREATE TABLE IF NOT
# EXISTS will not alter a live table, so they are applied explicitly.
_ADDED_COLUMNS = [
    ("shared", "nal_condo_unit", "county", "TEXT"),
    ("shared", "nal_condo_unit", "homestead", "INTEGER"),
    ("shared", "nal_condo_unit", "homestead_val", "REAL"),
    ("main", "condo_group", "homestead_units", "INTEGER"),
    ("main", "condo_group", "homestead_pct", "REAL"),
    ("main", "target", "homestead_pct", "REAL"),
    ("main", "target", "score_resistance", "REAL"),
    ("main", "target", "prior_roll_year", "INTEGER"),
    ("main", "target", "conc_delta", "REAL"),
    ("main", "target", "owners_delta", "INTEGER"),
    ("main", "target", "corporate_pct_delta", "REAL"),
    ("main", "target", "top_owner_changed", "INTEGER"),
    ("main", "target", "assembly_flag", "INTEGER"),
]


def _ensure_columns(con: sqlite3.Connection) -> None:
    for schema, table, col, decl in _ADDED_COLUMNS:
        try:
            cols = {r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")}
            if cols and col not in cols:
                con.execute(f'ALTER TABLE {schema}."{table}" ADD COLUMN {col} {decl}')
                con.commit()
        except sqlite3.Error:
            pass


def connect_query(path: Path = DB_PATH, shared_db: Path = SHARED_DB) -> sqlite3.Connection:
    """Read path for the API: attaches the shared store but skips schema
    creation, which the ingest scripts own. connect() runs executescript on
    every call, which is far too much work for a per-request dependency."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("ATTACH DATABASE ? AS shared", (str(shared_db),))
    return con


def has_table(con: sqlite3.Connection, table: str) -> bool:
    """True if `table` exists in main OR any attached database. A plain
    `SELECT ... FROM sqlite_master` only sees main and would report the
    shared tables as missing."""
    for schema in ("main", "shared", "temp"):
        try:
            row = con.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if row:
                return True
        except sqlite3.Error:
            continue
    return False


def log_ingest(con, source, started, finished, rows, note=""):
    con.execute(
        "INSERT INTO ingest_log (source, started, finished, rows, note) VALUES (?,?,?,?,?)",
        (source, started, finished, rows, note),
    )
    con.commit()
