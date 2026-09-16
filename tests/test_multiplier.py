"""Job-announcement impact sizing (multiplier.estimate), which /api/jobs-impact
exposes directly. avg_household_income double-counted WORKERS_PER_HOUSEHOLD:
households is already total_jobs divided BY that factor, so total_payroll /
households already lands on income per household -- multiplying by the same
factor again overstated every reported figure by ~40% (at the current 1.4
constant).
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.prospect.multiplier import WORKERS_PER_HOUSEHOLD, estimate  # noqa: E402

DDL = """
CREATE TABLE county (fips TEXT PRIMARY KEY, name TEXT, state TEXT, state_fips TEXT,
  cbsa TEXT, cbsa_name TEXT, cbsa_type TEXT);
CREATE TABLE county_wage (fips TEXT, year INTEGER, naics TEXT, industry TEXT,
  employment INTEGER, avg_annual_pay REAL, PRIMARY KEY (fips, year, naics));
CREATE TABLE market (cbsa TEXT PRIMARY KEY, name TEXT);
"""


def build():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    con.execute("INSERT INTO market (cbsa, name) VALUES ('99999', 'Test Metro')")
    con.execute("INSERT INTO county VALUES ('12345', 'Test County', 'TS', '12', '99999', "
               "'Test Metro', 'Metropolitan')")
    # NAICS 55 (corporate HQ) at exactly $140,000/worker in this market.
    con.execute("INSERT INTO county_wage VALUES ('12345', 2024, '55', 'Management', "
               "1000, 140000)")
    # All-industry average for induced (non-tradable) jobs: $70,000/worker.
    con.execute("INSERT INTO county_wage VALUES ('12345', 2024, 'TOTAL', 'All industries', "
               "5000, 70000)")
    con.commit()
    return con


def test_average_household_income_is_not_inflated_by_workers_per_household():
    """100 direct jobs at NAICS 55 (high_skill_tradable, x2.5 multiplier) ->
    250 induced jobs, 350 total. Pick pay so the math is checkable by hand:
    direct payroll = 100 * 140,000 = 14,000,000; induced payroll =
    250 * 70,000 = 17,500,000; total_payroll = 31,500,000.
    households = 350 / 1.4 = 250. The correct average household income is
    total_payroll / households = 31,500,000 / 250 = 126,000 -- NOT that
    times 1.4 again (176,400), which is what the bug produced."""
    con = build()
    out = estimate(con, "99999", "55", 100)

    assert out.direct_avg_pay == pytest.approx(140_000)
    assert out.induced_avg_pay == pytest.approx(70_000)
    assert out.induced_jobs == 250
    assert out.total_jobs == 350
    assert out.total_payroll == pytest.approx(31_500_000)
    assert out.households == 250

    assert out.avg_household_income == pytest.approx(126_000)
    # The bug's signature: this only equals the correct figure when
    # WORKERS_PER_HOUSEHOLD is 1, so pin the constant this test assumes.
    assert WORKERS_PER_HOUSEHOLD == pytest.approx(1.4)


def test_household_income_equals_total_payroll_over_households_exactly():
    """Restated without hand arithmetic, so a future change to the pay
    fixtures or the multiplier table can't accidentally keep this passing
    for the wrong reason."""
    con = build()
    out = estimate(con, "99999", "55", 100)
    assert out.avg_household_income == round(out.total_payroll / out.households)
