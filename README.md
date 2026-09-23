# Meridian

Parcels, zoning, rents and condo takeovers on one map, in one app.

Meridian is the merge of two programs that used to link at each other:
**Sitefolio** (the national parcel/zoning map) and **Prospect** (the condo
termination screen and metro tables). They shared a database and half their
questions, so they are now one process on one port, and the map is the surface
everything else hangs off.

    http://127.0.0.1:8012

## The four modes

| Mode | What it is |
|---|---|
| **Map** | Click any parcel in the country: owner, value, sales, zoning, flood, permits, entity behind the LLC, what can be built |
| **Condo takeovers** | All 6,160 Miami-Dade condominiums ranked as termination / bulk-acquisition plays — table on the left, every matching building pinned on the map to the right |
| **Markets** | 469 US metros scored on the drivers of population growth, a job-announcement demand model, and a one-line market due-diligence report |
| **Reference** | Legends, glossary, how the condo screen works, and every data source — out of the way until you want it |

Everything is stand-alone. Meridian does not read, call, or require any other
application.

---

## Condo takeovers

The Crexi-style split: filters across the top, a paged table on the left, and
the **whole filtered set** — not just the current page — pinned on the map on
the right. Pin size is unit count, colour is screen score. Hovering a row rings
its pin; clicking either one opens the building.

Inside a building you get ownership concentration, shared mailing addresses (one
buyer behind several LLCs shows up there before it shows up in any single owner
name), sales comparables, the redevelopment envelope, and the declaration-review
form.

### Why the screen exists

Under [FS 718.117](https://www.flsenate.gov/laws/statutes/2017/718.117) a condo
can be terminated on **80% approval** with no more than 5% objecting. Many older
declarations set a higher bar, often unanimity. Whether the statutory 80% reaches
a given building turns on whether its declaration adopts the Condominium Act
*as amended from time to time* — "Kaufman language", after *Kaufman v. Shere*,
347 So. 2d 627 (Fla. 3d DCA 1977).

Nobody publishes which buildings have it. Finding out means reading declarations,
which is why the tool is two-stage: screen 6,160 buildings from free bulk data,
then pull documents only for the ones that survive.

> **The distinction that decides deals.** Florida's Third DCA ruled against a
> developer that had acquired 183 of 192 units and then *amended* the declaration
> to insert Kaufman language and drop the threshold from unanimous to 80%.
> Kaufman in the **original** recorded declaration carries weight; Kaufman
> **added later by a bulk owner** is the fact pattern that lost.
> `kaufman_original` and `kaufman_by_amendment` are separate fields and nothing
> collapses them.

### Sales comparables — read this before quoting a number

The FDOR roll's `SALE_PRC1` is the consideration on the **instrument**, and a
deed conveying ten units stamps the same package price on all ten folios. Taken
at face value that inflates a per-unit median by an order of magnitude: one real
building in this data shows 148 folios carrying prices up to $4.9M each, which
are actually a handful of bulk deeds.

So comps group sales into instruments by (year, price), and a package's per-unit
figure is the consideration divided by the folios it covers. Only per-unit
figures are ever medianed, and single-unit sales are reported **separately** from
bulk deeds, because those are the arm's-length signal. For that same building:

| | |
|---|---|
| Naive median of 148 folio prices | ~$967,000 "per unit" — fiction |
| Single-unit sales (37) | **$441,000** median — the real benchmark |
| Bulk deeds (27 instruments, 111 units) | $300,000 median implied per unit |

The roll carries no qualification code, so intra-family and other
non-arm's-length transfers are not filtered out. Confirm the deed before relying
on any single line.

### Offering memoranda

**Offering memo** on any building renders a print-ready acquisition memorandum
from data the app already holds — the scored target row, the unit-level roll and
its recorded sales, the zoning envelope, and the metro screener. Sections: the
opportunity, ownership, sales comparables inside the building, comparable
buildings nearby, the termination path, redevelopment capacity, market context,
sources.

It opens in a new tab with a Print / Save-as-PDF button. **Memo & save** also
writes it to `data\memos\`. Whatever the memo could not confirm is printed at the
top of the document rather than left implicit — an unreviewed declaration, a
bulk-deed distortion, a missing zoning polygon.

Set `memo.firm` in `backend/prospect/config.json` to put a name on the cover;
leave it empty for an unbranded document.

---

## Market trends on the map

Tick **Market trends** under Layers. Every US metro and micropolitan area is
shaded by one indicator, picked from the dropdown; the layer fades out past
zoom 9, where a metro outline is off-screen anyway. Hovering gives the value
and its national percentile; clicking opens that metro's due-diligence report.

Shading is by **national percentile**, not raw value, for two reasons: one ramp
then reads the same way whether the indicator is dollars, a rate or a ratio,
and a quantile scale stops these heavily skewed distributions collapsing into a
single band. The legend carries the real value ranges.

Fourteen raw indicators are available — population growth, net migration,
domestic-only migration, arrivals' income, income premium, wage level, wage
growth, permitting, multifamily share, home price, price growth, affordability,
market rent, rent growth — plus two composites.

### The two composites

Neither is a model. Both are judgment, and both are stated so you can disagree
with a specific number rather than with a black box.

**Demand vs supply** (`tightness`) is migration percentile minus permitting
percentile. No weights, trivially auditable: +40 means the metro ranks 40
points higher on in-migration than on permitting. This is the one that matters
most for rents — a metro pulling people in faster than it permits homes has
pricing power; one permitting into weak migration is building into a vacancy
problem.

**Market strength** blends five drivers, each as a percentile so no single
outlier can drag it:

| Component | Weight | Why |
|---|---|---|
| Net migration | 0.20 | the direct source of absorption |
| Demand vs supply | 0.25 | whether supply is answering that demand |
| Wage growth | 0.20 | whether tenants can pay more next year |
| Income premium | 0.15 | whether arrivals raise or dilute the income base |
| Affordability | 0.20 | room before rent growth hits a ceiling |

Migration is weighted lower than it looks like it deserves because *demand vs
supply already contains it* — together they give migration an effective weight
near 0.33, not 0.45.

### Two things to know before trusting it

The top of **Market strength** is heavily Florida — usually seven or eight of
the top ten among metros above 500k. That is not a weighting artifact: it
survives dropping the migration term, dropping tightness, and doubling
affordability. It is what this data vintage says. It also means the composite
mostly rewards a pattern already visible from a distance.

**Demand vs supply is the better screen for anything non-obvious.** Because it
is demand *relative to* supply, it surfaces constrained markets that never
appear near the top of a migration ranking — Scranton, San Jose, Providence,
Hartford, Detroit — where in-migration is unremarkable but permitting is far
below it.

    /api/metro-trends                         geometry + every indicator
    /api/metro-trends/top?metric=&min_pop=    the leaders on one indicator

Boundaries come from Census TIGERweb via `scripts/fetch_cbsa_geo.py`,
generalised to about 2 km — they are used for shading, never measurement.

---

## Market due diligence

Type a metro into the bar at the top of the **Markets** tab and press **Build
report**. `Columbus Ohio`, `Kansas City Missouri`, `Austin TX` — city plus state
resolves to one metro; a bare `Columbus` returns the five real candidates to
pick from rather than guessing.

The report is deliberately two halves:

**Macro.** Population trajectory with the change decomposed into natural
increase, domestic migration and international migration — which of the three is
carrying growth usually tells you more than the total does. Columbus, OH is the
clean example: +83k people over 2020–2024, but domestic migration is *negative*
and 73% of the gain is international, so the headline depends entirely on that
flow continuing. Then employment and pay, home prices against their own peak,
rents against the national index, affordability on both price-to-income and
rent-to-wage, and a percentile rank on ten measures against all 469 metros.

**Micro.** Industry mix with a location quotient against the national share, so
specialisation is visible rather than inferred (Columbus runs 1.64x national in
transportation and warehousing, 2.23x in management of companies). A
county-by-county table, because a metro average hides that Franklin County is
61% of the population growing at 0.59% while Union County grows at 3.26%. Where
in-movers come from and the AGI each flow carries. And what is being permitted,
split by multifamily share.

Every report ends with what the data could not support — absent price series,
single-year permits, endpoint-only wage years. Nothing is silently omitted.

A location quotient is only reported as a concentration when the sector is both
specialised (LQ >= 1.25) and large enough to matter (>= 1% of metro jobs); a high
ratio on a 4,000-job sector is a statistical curiosity, not an exposure.

    /api/dd/resolve?q=columbus+ohio   candidates, best first
    /api/dd/{cbsa}                    the report as JSON
    /api/dd/{cbsa}/report             print-ready HTML

Built from the shared store the screener already populates — no extra downloads
and no API keys.

---

## Map layers

**Anywhere** — load as you pan, wherever you are:

- **Parcel lines** (zoom 15+)
- **Flood** — FEMA National Flood Hazard Layer, with a high-risk-only filter
- **Zoning** — live municipal polygons coloured by use, every wired city
- **Rents** — two sources, switchable in place:
  - *Zillow ZORI* by ZIP: the typical market asking rent, plus year-over-year
  - *HUD Fair Market Rent* by FMR area: the 40th-percentile ceiling that voucher
    and LIHTC underwriting keys off, by bedroom count

  They are kept separate rather than blended. The gap between them is the
  affordable spread, which is the reason to look at both.
- **Population** — % change by ZIP between any two ACS vintages, 2012–2023, on
  two sliders. Dragging them recolours without refetching.
- **New construction** — permit heat weighted by building size
- **Transit** — BTS routes and stops by mode
- **Modular plants** — offsite-construction factories across 39 states
- **Data coverage** — which markets carry which datasets

**South Florida** — pre-baked, colours in immediately: Miami-Dade and Broward
zoning and future land use, city limits, Opportunity Zones, CRA districts,
brownfields, enterprise zones, historic districts, Rapid Transit Zones,
Metrorail/Metromover, schools, parks.

Rent choropleths break at **quantiles of what is in view**, not a fixed national
ramp — otherwise every ZIP in one metro renders the same colour.

---

## Running it

Download this repository — **Code → Download ZIP**, or clone it — unzip it
wherever you want it to live, and start it:

| | |
|---|---|
| **Windows** | double-click `start.bat` |
| **macOS** | double-click `start.command` |
| **Linux** | `./start.sh` |

The only prerequisite is **Python 3.11 or newer**. On Windows, tick *Add
python.exe to PATH* when you install it; if it is missing the launcher says so
and links the download rather than failing with a traceback.

The first run creates `.venv` inside the folder and installs the dependencies
into it — about a minute, once. If Windows builds that environment without pip
(the Microsoft Store build of Python does this, as do some corporate images) the
launcher repairs it rather than failing. Every run after that starts straight away and
opens <http://127.0.0.1:8012>. Nothing is installed outside the folder, no paths
need editing, and deleting the folder removes the app completely.

If port 8012 is already taken — an older install starting at logon is the usual
reason — the launcher says which folder is holding it and starts this copy on the
next free port instead of opening a browser onto the other one.

```
start.bat --port 8020      serve somewhere else
start.bat --fetch          also download the public map and market data
start.bat --update         update from GitHub first, then start
start.bat --check-update   say whether an update is available, then stop
start.bat --reinstall      rebuild .venv from scratch
```

### Updating

Double-click **`update.bat`** (`update.command` / `update.sh`), or press
**Check for updates** in the app's **Reference** tab. The button takes two
clicks on purpose: the first says what would change, the second does it.

Updates come from GitHub as a zip, so this works whether the folder was cloned
or downloaded. It is plain Python — `urllib` and `zipfile`, no PowerShell and
nothing shelled out — and:

- `data\`, `.venv\` and `logs\` are **never** touched
- `backend\prospect\config.json` is **merged**, not overwritten: anything a new
  version adds appears, and every value you set — score weights, the firm name on
  a memo cover, the county table — stays
- every replaced file is copied to `.backup\<timestamp>\` first
- the download is unpacked in full before anything is swapped, so a dropped
  connection leaves the running copy alone

Set `update.branch` in `backend\prospect\config.json` to whatever you actually
ship from. Pointing it at a branch you no longer merge to would roll an installed
copy **backward**, which is why the repo and branch are printed beside the button
rather than hidden in a constant.

Restart the app afterwards; the running process is still the old code until you
do, and the app says so rather than pretending otherwise. (A copy installed via
`install.bat` restarts itself — see below.)

### Where the data lives

Everything is inside the folder: `data\` for the databases, declarations and
memos, and `data\_shared\` for the large national tables. Point
`APPS_SHARED_DB` at an existing `shared.db` if you already have one elsewhere.

The app runs with no data at all — it reports what is missing rather than
failing. **Reference → Version** and `/api/selftest` list every dataset, whether
it resolved, and the script that builds it.

### Installing it as a standalone app (optional, Windows)

Double-click **`install.bat`**. It sets up `.venv` if `start.bat` has not
already been run, then adds a desktop shortcut that opens Meridian in its own
window, starts the server automatically at logon, and registers a task that
re-invokes the launcher every 15 minutes — after that, the folder behaves like
an installed app rather than something you run from a console.

That 15-minute task does two things, both handled by `launch.py`:

- **self-heal** — if the server isn't answering, it's restarted
- **auto-update** — once every 24 hours (tracked via the `.version` file's own
  timestamp, so this doesn't need a separate setting), it runs the exact same
  update `apply()` the manual button uses. If anything changed, it reinstalls
  dependencies if `requirements.txt` did, then restarts the server so the new
  code actually takes effect — unlike the manual path above, nobody has to
  come back and click restart.

**Your data comes with you.** A fresh download has an empty `data\` folder, so
before it touches anything else `install.py` looks for an earlier install —
through the shortcuts and scheduled task it left behind (Meridian, Groundwork,
Prospect, Sitefolio) — and copies that folder's `data\` in: scored targets,
deal stages, declarations, memos and the shared store. Nothing already in the
new folder is overwritten (except a database with no rows in it at all), the
old folder is only read, and databases are copied through SQLite's backup API,
so it is safe while the old server is still running. If it can't find the old
folder, point it there yourself:
`.venv\Scripts\python.exe install.py --import-from "C:\path\to\old\Meridian"`.

It then takes over port 8012 from the old copy if that is still running —
the launcher checks *which folder* is answering (`/api/instance`), not just that
something is, so the new icon never opens old code.

A **git clone** is never auto-updated (that would overwrite the working tree
from a zip); `git pull` instead, or set `MERIDIAN_AUTO_UPDATE=1` to opt in.

What it checks and how often is in `launch.py` (`UPDATE_CHECK_INTERVAL_HOURS`);
what it did is logged to `logs\update.log`. Without the recurring task
(`--no-task` below), the startup shortcut alone still checks once per sign-in
— a machine left running for days between reboots just goes that many days
between checks.

```bat
install.bat                                      REM one click: sets up .venv, imports old data, then the shortcut
.venv\Scripts\python.exe install.py              REM shortcut + start at logon + auto-update, without the setup step
.venv\Scripts\python.exe install.py --no-task    REM shortcut + start at logon, but skip the recurring task
.venv\Scripts\python.exe install.py --uninstall  REM remove them; data untouched
.venv\Scripts\python.exe install.py --import-from "C:\old\Meridian"  REM bring that folder's data in
```

`install.py` also clears the desktop shortcuts, startup shortcuts and scheduled
tasks belonging to **Sitefolio** and **Prospect** — the two apps this replaces —
and to **Groundwork**, which is what this app was called before it was Meridian.
That last one matters: a stale logon task starting the old folder is how you end
up looking at old code on port 8012 and wondering why nothing changed.
Their folders and data are left alone — only the wiring that would start them is
removed. No PowerShell anywhere: shortcuts go through the shell's `IShellLink`
COM interface via ctypes, and the scheduled task through `schtasks.exe`, so
nothing needs elevation.

### Tests

```bat
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest tests\ -q
```

They run against an **empty** `data\`, which is the state of a fresh clone, and
that is the point: the suite asserts the app boots, that every module and every
script under `scripts\prospect\` imports, and that the data-dependent routes
report an unbuilt store rather than raising a 500. The zoning tests pin the
Miami 21 vocabulary — `CS` is Civic Space here and Commercial Service in half
the other wired cities, so the local dictionary must not leak.

CI runs the same command on every push.

Rebuilding the condo screen from scratch (only needed for a new tax roll):

```bat
venv\Scripts\python.exe scripts\prospect\ingest_dbpr.py         REM   5,456 associations
venv\Scripts\python.exe scripts\prospect\ingest_nal.py          REM 391,210 condo units
venv\Scripts\python.exe scripts\prospect\ingest_pa.py           REM 585,468 land parcels
venv\Scripts\python.exe scripts\prospect\build_targets.py       REM group, match, score
venv\Scripts\python.exe scripts\prospect\ingest_market.py --all REM ~5 min
venv\Scripts\python.exe scripts\prospect\build_markets.py       REM 469 markets scored
```

---

## Layout

```
backend/
  app.py            map, parcels, zoning, flood, permits, rents, everything national
  site_screen.py    federal screening services + saved-address export
  shared_data.py    read-only accessors for the shared store
  florida_lookups.py
  prospect/         acquisitions — was the Prospect app
    routes.py       targets, metros, memos, capacity
    memo.py         offering-memorandum generator
    capacity.py     zoning envelope -> unit yield
    db.py           prospect.db + shared.db attachment
    multiplier.py   job-announcement demand model
    config.json     score weights, shortlist gate, memo settings
frontend/
  index.html        the shell
  app.js            the map
  workspace.js      modes, condo takeovers, markets, drawers
  styles.css        panel + type system
  shell.css         frame, workspace, drawer
  vendor/           MapLibre GL 4.7.1 (BSD-3, licence alongside) -- served
                    locally so a blocked or slow CDN can't blank the map
scripts/
  fetch_layers.py  fetch_zori.py  fetch_zcta_population.py  make_icon.py
  prospect/        the ingest + scoring pipeline
data/
  prospect.db      scored targets + declaration review (the only thing written here)
  zori_rents.json  zcta_pop.json  marks.db  geom_cache.db  memos/
```

### The shared store

National market tables and the Miami-Dade parcel/owner roll live in one database
outside the app, at `C:\Apps\_shared\shared.db` (~330 MB), attached read-only.
Override with `APPS_SHARED_DB`, or `APPS_SHARED` for the whole folder; a `_shared`
directory beside the app folder is found automatically, so a copied install works
wherever it is unzipped.

| Table | Rows |
|---|---:|
| `market` | 469 |
| `county` / `county_pop` | 1,915 / 15,720 |
| `county_permits` / `county_wage` | 3,028 / 127,911 |
| `metro_price` | 41,135 |
| `migration_flow` | 109,044 |
| `nal_condo_unit` | 391,210 |
| `pa_parcel` | 585,577 |

`data/prospect.db` is the only database Meridian writes: the scored `target`
table and the stage-2 declaration findings entered against it.

---

## Endpoints

Map and parcel routes keep the paths they had. The acquisitions side moved a few
names on merge, because the map already owned them:

| Was | Is | Why |
|---|---|---|
| `/api/cities` | `/api/condo/cities` | the map's `/api/cities` is zoning-coverage cities |
| `/api/markets` | `/api/metros` | the map's `/api/markets` is data-coverage map dots |
| `/api/market/{cbsa}` | `/api/metro/{cbsa}` | " |
| `/api/market-stats` | `/api/metro-stats` | " |
| `/api/stats` | `/api/condo/stats` | says which stats |
| `/api/export.xlsx` | `/api/targets.xlsx` | says what it exports |
| `/api/om/{slug}` | `/api/memo/{group_key}` | memos are per building now, not per project |

New:

| Endpoint | Returns |
|---|---|
| `GET /api/targets.geojson` | every target matching the filters, as map points |
| `GET /api/condo-comps?group_key=` | both comp sets as JSON |
| `GET /api/memo/{group_key}` | the rendered memorandum (`&save=true` writes it) |
| `GET /api/memo/{group_key}/inspect` | what the memo will contain, and its gaps |
| `GET /api/rents-overlay?bbox=&source=zori\|fmr&beds=` | rent choropleth for the viewport |
| `GET /api/rents-at?lon=&lat=` | HUD FMR by bedroom at a point |
| `GET /api/dd/resolve?q=` | metro candidates for a free-text name, best first |
| `GET /api/dd/{cbsa}` | the due-diligence report as JSON |
| `GET /api/dd/{cbsa}/report` | the print-ready report |
| `GET /api/metro-trends` | every metro as a shaded polygon, all indicators on each feature |
| `GET /api/metro-trends/top?metric=` | leaders on one indicator |

`/api/docs` is the full generated list.

---

Informational only. Aggregated from public GIS and assessor sources that may be
outdated or inaccurate. Not zoning, legal, valuation, or engineering advice.
Verify with the governing jurisdiction and licensed counsel before acquisition
or design.
