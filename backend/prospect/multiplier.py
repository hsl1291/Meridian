"""Job announcement -> jobs, pay, and housing demand.

Answers the question behind an HQ relocation or plant announcement: a press
release says "1,200 jobs" — so how many households actually arrive, and what do
they earn?

THE MULTIPLIER. Moretti (2010) found that one new job in the TRADABLE sector
creates about 1.6 additional jobs in the local non-tradable sector (~2.6 total).
The effect is not uniform:

    skilled / high-tech tradable   ~2.5 additional non-tradable jobs
    average tradable (mfg)         ~1.6
    unskilled tradable             ~1.0
    non-tradable                   small — it mostly redistributes local demand

So the industry of the announced jobs matters more than the headline count. An
HQ relocation (NAICS 55, Management of Companies) carries a high-skill
multiplier; a distribution centre does not.

THE PAY. Direct pay comes from BLS QCEW for that NAICS *in that market* — the
same job pays differently in Austin and Des Moines. Induced non-tradable jobs are
paid at the market's all-industry average, because that is what they are.

Everything returned is an estimate with its basis attached. It is a sizing tool,
not a forecast.
"""
from dataclasses import dataclass, field, asdict

# NAICS 2-digit sector -> (label, tradability tier)
# Tiers: high_skill_tradable | tradable | unskilled_tradable | non_tradable
SECTORS = {
    "11": ("Agriculture, forestry, fishing", "tradable"),
    "21": ("Mining, quarrying, oil & gas", "tradable"),
    "22": ("Utilities", "non_tradable"),
    "23": ("Construction", "non_tradable"),
    "31": ("Manufacturing", "tradable"),
    "32": ("Manufacturing", "tradable"),
    "33": ("Manufacturing", "tradable"),
    "42": ("Wholesale trade", "tradable"),
    "44": ("Retail trade", "non_tradable"),
    "45": ("Retail trade", "non_tradable"),
    "48": ("Transportation & warehousing", "unskilled_tradable"),
    "49": ("Transportation & warehousing", "unskilled_tradable"),
    "51": ("Information / tech", "high_skill_tradable"),
    "52": ("Finance & insurance", "high_skill_tradable"),
    "53": ("Real estate & leasing", "non_tradable"),
    "54": ("Professional, scientific & technical", "high_skill_tradable"),
    "55": ("Corporate HQ / management of companies", "high_skill_tradable"),
    "56": ("Administrative & support", "non_tradable"),
    "61": ("Educational services", "non_tradable"),
    "62": ("Health care & social assistance", "non_tradable"),
    "71": ("Arts, entertainment & recreation", "non_tradable"),
    "72": ("Accommodation & food services", "non_tradable"),
    "81": ("Other services", "non_tradable"),
    "92": ("Public administration", "non_tradable"),
}

MULTIPLIER = {
    "high_skill_tradable": 2.5,
    "tradable": 1.6,
    "unskilled_tradable": 1.0,
    "non_tradable": 0.3,
}
MULTIPLIER_BASIS = {
    "high_skill_tradable": "Moretti (2010): skilled/high-tech tradable jobs create ~2.5 "
                           "additional non-tradable jobs",
    "tradable": "Moretti (2010): one manufacturing job creates ~1.6 additional "
                "non-tradable jobs (~2.6 total)",
    "unskilled_tradable": "Moretti (2010): unskilled tradable jobs create ~1.0 additional job",
    "non_tradable": "Non-tradable employment largely redistributes existing local "
                    "demand; only a small net multiplier is applied",
}

# ACS: workers per household nationally runs ~1.35-1.45.
WORKERS_PER_HOUSEHOLD = 1.4
VACANCY_FACTOR = 1.05          # units needed per household at a healthy vacancy rate


@dataclass
class JobImpact:
    market: str = ""
    cbsa: str = ""
    industry: str = ""
    naics: str = ""
    tier: str = ""
    direct_jobs: int = 0
    induced_jobs: int = 0
    total_jobs: int = 0
    multiplier: float = 0.0
    direct_avg_pay: float | None = None
    direct_pay_basis: str = ""
    induced_avg_pay: float | None = None
    direct_payroll: float | None = None
    induced_payroll: float | None = None
    total_payroll: float | None = None
    households: int = 0
    housing_units: int = 0
    avg_household_income: float | None = None
    notes: list[str] = field(default_factory=list)

    def dict(self):
        return asdict(self)


def sector_of(naics: str):
    n = (naics or "").strip()
    key = n[:2]
    return SECTORS.get(key, ("Unspecified industry", "tradable"))


def _lookup_pay(con, cbsa, naics):
    """Average annual pay for a NAICS sector in a market, employment-weighted
    across its counties. Falls back to the national figure for that sector, then
    to the market's all-industry average — the basis is always reported."""
    year = con.execute("SELECT MAX(year) FROM county_wage").fetchone()[0]

    def weighted(where, params):
        r = con.execute(
            f"SELECT SUM(w.employment) e, SUM(w.employment*w.avg_annual_pay) p "
            f"FROM county_wage w {where}", params).fetchone()
        if r and r["e"] and r["p"]:
            return r["p"] / r["e"]
        return None

    if cbsa and naics:
        v = weighted("JOIN county c ON c.fips=w.fips WHERE c.cbsa=? AND w.naics=? AND w.year=?",
                     (cbsa, naics, year))
        if v:
            return v, f"BLS QCEW {year}, NAICS {naics} in this market"
    if naics:
        v = weighted("WHERE w.naics=? AND w.year=?", (naics, year))
        if v:
            return v, f"BLS QCEW {year}, NAICS {naics} national average (market-level " \
                      f"figure unavailable)"
    if cbsa:
        v = weighted("JOIN county c ON c.fips=w.fips WHERE c.cbsa=? AND w.naics='TOTAL' "
                     "AND w.year=?", (cbsa, year))
        if v:
            return v, f"BLS QCEW {year}, all-industry average for this market " \
                      f"(no figure for NAICS {naics})"
    return None, "no wage data available"


def estimate(con, cbsa: str, naics: str, jobs: int,
             stated_avg_pay: float | None = None) -> JobImpact:
    """Size an announcement. `stated_avg_pay` overrides QCEW when the press
    release gives a number — that is better evidence than an industry average."""
    label, tier = sector_of(naics)
    m = con.execute("SELECT name FROM market WHERE cbsa=?", (cbsa,)).fetchone()
    out = JobImpact(market=m["name"] if m else cbsa, cbsa=cbsa, industry=label,
                    naics=naics, tier=tier, direct_jobs=int(jobs))

    mult = MULTIPLIER[tier]
    out.multiplier = mult
    out.induced_jobs = round(jobs * mult)
    out.total_jobs = out.direct_jobs + out.induced_jobs
    out.notes.append(MULTIPLIER_BASIS[tier])

    if stated_avg_pay:
        out.direct_avg_pay = float(stated_avg_pay)
        out.direct_pay_basis = "stated in the announcement"
    else:
        pay, basis = _lookup_pay(con, cbsa, naics)
        out.direct_avg_pay, out.direct_pay_basis = pay, basis

    induced_pay, _ = _lookup_pay(con, cbsa, "TOTAL")
    out.induced_avg_pay = induced_pay
    if induced_pay:
        out.notes.append("Induced jobs are paid at the market's all-industry average, "
                         "since they are local non-tradable roles")

    if out.direct_avg_pay:
        out.direct_payroll = round(out.direct_avg_pay * out.direct_jobs)
    if induced_pay:
        out.induced_payroll = round(induced_pay * out.induced_jobs)
    if out.direct_payroll is not None or out.induced_payroll is not None:
        out.total_payroll = round((out.direct_payroll or 0) + (out.induced_payroll or 0))

    out.households = round(out.total_jobs / WORKERS_PER_HOUSEHOLD)
    out.housing_units = round(out.households * VACANCY_FACTOR)
    if out.total_payroll and out.households:
        out.avg_household_income = round(out.total_payroll / out.households
                                         * WORKERS_PER_HOUSEHOLD)
        out.notes.append(f"Household income assumes {WORKERS_PER_HOUSEHOLD} workers per "
                         f"household (ACS national average)")
    out.notes.append(f"Housing units include a {int((VACANCY_FACTOR-1)*100)}% vacancy allowance")
    if not out.direct_avg_pay:
        out.notes.append("No wage figure found — supply an average pay to size the payroll")
    return out
