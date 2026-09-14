"""A condominium with enough recorded history to fill a memo.

Built large on purpose: 60 units and 40 sales, so the sales table runs past one
page and the repeating-header and page-break rules are actually exercised rather
than asserted against a document that happens to fit.
"""
import sqlite3
from datetime import date

YEAR = date.today().year

DDL = """
CREATE TABLE target (group_key TEXT PRIMARY KEY, project_number TEXT, match_method TEXT,
  match_confidence REAL, condo_name TEXT, addr_primary TEXT, city TEXT, units_dbpr INTEGER,
  units_nal INTEGER, recorded_year INTEGER, act_yr_blt INTEGER, age_years INTEGER,
  milestone_due INTEGER, jv_per_unit REAL, lnd_val_per_unit REAL, top_owner TEXT,
  top_owner_pct REAL, top_mail_pct REAL, absentee_pct REAL, out_of_state_pct REAL,
  corporate_pct REAL, entity_sales_last_3yr INTEGER, score REAL, score_age REAL,
  score_scale REAL, score_concentration REAL, score_absentee REAL,
  termination_threshold TEXT, kaufman_original INTEGER, kaufman_by_amendment INTEGER,
  declaration_or_book TEXT, declaration_or_page TEXT, declaration_source_url TEXT,
  stage2_verified INTEGER, stage2_notes TEXT, lon REAL, lat REAL,
  homestead_pct REAL, score_resistance REAL);
CREATE TABLE condo_group (group_key TEXT PRIMARY KEY, unit_folios INTEGER,
  condo_name_legal TEXT, name_norm TEXT, addr_primary TEXT, addr_all TEXT,
  addr_count INTEGER, city TEXT, zip TEXT, act_yr_blt INTEGER, jv_total REAL,
  jv_per_unit REAL, lnd_val_total REAL, lnd_val_per_unit REAL, avg_living_area REAL,
  top_owner TEXT, top_owner_units INTEGER, top_owner_pct REAL, top_mail_addr TEXT,
  top_mail_units INTEGER, top_mail_pct REAL, distinct_owners INTEGER,
  assoc_owned_units INTEGER, assoc_owned_pct REAL, absentee_units INTEGER,
  absentee_pct REAL, out_of_state_units INTEGER, out_of_state_pct REAL,
  corporate_units INTEGER, corporate_pct REAL, sales_last_3yr INTEGER,
  entity_sales_last_3yr INTEGER, lon REAL, lat REAL,
  homestead_units INTEGER, homestead_pct REAL);
CREATE TABLE nal_condo_unit (folio TEXT PRIMARY KEY, group_key TEXT, owner_name TEXT,
  owner_norm TEXT, owner_addr1 TEXT, owner_addr_norm TEXT, owner_city TEXT,
  owner_state TEXT, owner_zip TEXT, phy_addr1 TEXT, phy_addr_norm TEXT, phy_city TEXT,
  phy_zip TEXT, act_yr_blt INTEGER, eff_yr_blt INTEGER, jv REAL, lnd_val REAL,
  tot_lvg_area REAL, sale_prc1 REAL, sale_yr1 INTEGER, or_book1 TEXT, or_page1 TEXT,
  s_legal TEXT, is_entity INTEGER, is_absentee INTEGER, county TEXT,
  homestead INTEGER, homestead_val REAL);
CREATE TABLE dbpr_association (project_number TEXT PRIMARY KEY, file_number TEXT,
  name TEXT, name_norm TEXT, county TEXT, address TEXT, addr_norm TEXT,
  address_city TEXT, address_zip TEXT, units INTEGER, recorded_date TEXT,
  recorded_year INTEGER, primary_status TEXT, secondary_status TEXT, me_number TEXT,
  me_name TEXT, me_addr TEXT, me_city TEXT, me_state TEXT, me_zip TEXT);
"""

KEY = "0132070"
N_UNITS = 60


def memo_db(units=N_UNITS):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)

    con.execute(
        "INSERT INTO target (group_key, project_number, match_method, match_confidence, "
        "condo_name, addr_primary, city, units_dbpr, units_nal, recorded_year, act_yr_blt, "
        "age_years, milestone_due, jv_per_unit, lnd_val_per_unit, top_owner, top_owner_pct, "
        "top_mail_pct, absentee_pct, out_of_state_pct, corporate_pct, entity_sales_last_3yr, "
        "score, score_age, score_scale, score_concentration, score_absentee, lon, lat) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (KEY, "PRJ-1", "name+units", 0.96, "Seaside Towers Condominium",
         "1200 OCEAN DR", "MIAMI BEACH", units, units, 1974, 1975, YEAR - 1975, 1,
         318_000, 74_000, "CORAL HOLDINGS LLC", 21.7, 24.9, 58.3, 19.1, 27.5, 9,
         71.4, 82.0, 63.0, 43.4, 55.1, -80.1301, 25.7889))

    con.execute(
        "INSERT INTO condo_group (group_key, unit_folios, condo_name_legal, addr_primary, "
        "addr_all, addr_count, city, zip, act_yr_blt, jv_total, jv_per_unit, lnd_val_total, "
        "lnd_val_per_unit, avg_living_area, top_owner, top_owner_units, top_owner_pct, "
        "top_mail_addr, top_mail_units, top_mail_pct, distinct_owners, assoc_owned_units, "
        "assoc_owned_pct, absentee_units, absentee_pct, out_of_state_units, out_of_state_pct, "
        "corporate_units, corporate_pct, sales_last_3yr, entity_sales_last_3yr, lon, lat, "
        "homestead_units, homestead_pct) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (KEY, units, "SEASIDE TOWERS CONDO", "1200 OCEAN DR", "1200 OCEAN DR", 1,
         "MIAMI BEACH", "33139", 1975, 318_000 * units, 318_000, 74_000 * units, 74_000,
         1_040, "CORAL HOLDINGS LLC", 13, 21.7, "PO BOX 4410", 15, 24.9, 41, 2, 3.3,
         35, 58.3, 11, 19.1, 17, 27.5, 12, 9, -80.1301, 25.7889, 22, 36.7))

    con.execute(
        "INSERT INTO dbpr_association (project_number, name, county, address, units, "
        "recorded_date, recorded_year, primary_status, me_name) VALUES (?,?,?,?,?,?,?,?,?)",
        ("PRJ-1", "SEASIDE TOWERS CONDOMINIUM ASSOCIATION INC", "DADE",
         "1200 OCEAN DR MIAMI BEACH FL 33139", units, "03/14/1974", 1974,
         "Active", "COASTAL MANAGEMENT GROUP"))

    # 40 priced units: distinct instruments, a spread of years and sizes, plus one
    # real bulk deed so the memo's bulk caveat renders.
    for i in range(units):
        priced = i < 40
        bulk = 34 <= i < 40           # six folios on one instrument
        sf = 780 + (i % 7) * 190
        con.execute(
            "INSERT INTO nal_condo_unit (folio, group_key, owner_name, owner_norm, "
            "owner_addr1, owner_addr_norm, owner_city, owner_state, phy_addr1, "
            "phy_addr_norm, phy_city, phy_zip, act_yr_blt, jv, lnd_val, tot_lvg_area, "
            "sale_prc1, sale_yr1, or_book1, or_page1, is_entity, is_absentee, county, "
            "homestead) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{KEY}{i:06d}", KEY,
             "CORAL HOLDINGS LLC" if i < 13 else f"OWNER {i} FAMILY TRUST",
             "CORAL HOLDINGS LLC" if i < 13 else f"OWNER {i} FAMILY TRUST",
             "PO BOX 4410" if i < 15 else f"{100 + i} MAPLE ST",
             "PO BOX 4410" if i < 15 else f"{100 + i} MAPLE ST",
             "MIAMI", "FL" if i % 3 else "NY", "1200 OCEAN DR", "1200 OCEAN DR",
             "MIAMI BEACH", "33139", 1975, 318_000 + (i % 5) * 21_000, 74_000, sf,
             (2_940_000 if bulk else 402_000 + (i % 11) * 17_500) if priced else None,
             (YEAR - 1 - (i % 4)) if priced else None,
             "31500" if bulk else (f"{30000 + i}" if priced else None),
             "119" if bulk else (f"{100 + i}" if priced else None),
             1 if i < 13 else 0, 1 if i % 3 else 0, "DADE", 1 if 20 <= i < 42 else 0))
    con.commit()
    return con
