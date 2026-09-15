# Roadmap

Where the condo takeover module goes next, and why in that order.

Written against commit `5d63c0f`, the Sitefolio + Prospect merge. Effort figures
are working days for one person who already knows this codebase.

---

## The honest starting position

Three things are true at once, and the sequencing below follows from them.

**The screen is good.** Grouping bulk deeds into instruments before taking a
per-unit median, keeping `kaufman_original` and `kaufman_by_amendment` apart,
excluding association-owned units from the concentration signal, refusing to
pass off plat-wide `lot_size` as building site area — these are the judgment
calls that separate a tool somebody trusts from a spreadsheet with a colour
ramp. Most of what follows builds on that instinct rather than replacing it.

**The pipeline does not run.** Every script under `scripts/prospect/` fails at
import. They were moved into a subdirectory during the merge and their path
wiring was not moved with them:

```
$ python3 scripts/prospect/stage2.py --status
ModuleNotFoundError: No module named 'backend'
```

`ROOT = Path(__file__).resolve().parent.parent` resolves to `scripts/`, not the
repo root, and `backend.db` / `backend.norm` / `backend.declaration` now live at
`backend.prospect.*`. All seven scripts have it. The README's "First run on a new
machine" section instructs a new user to run six commands that all fail on line
one. Nothing else on this list matters until that is fixed.

**Most of what is missing is synthesis, not collection.** The drawer already
shows age, every owner and their share, shared mailing addresses, and both comp
sets — but nothing multiplies them into a buyout number. `migration_flow` holds
109,044 IRS county-to-county rows with income attached, and is read in exactly
one place to build a summary string. `tot_lvg_area` sits on every unit and a
median $/SF is computed from the comps, and the two are never combined. The data
is largely already here. The arithmetic on top of it is not.

---

## Phase 0 — make it run again  ✅ done

**1 day, as estimated.** `stage2.py --status` runs end to end again; 44 tests
pass on three consecutive runs leaving no artifacts behind.

| # | Work | Status |
|---|---|---|
| 0.1 | Path wiring in all 7 `scripts/prospect/*.py` | done |
| 0.2 | Undeclared deps → `requirements.txt` + a new `requirements-dev.txt` | done |
| 0.3 | `tests/` (44) + GitHub Actions CI on every push | done |
| 0.4 | Dockerfile scope decided: it ships the map, not the condo screen | done |

The wrong `ROOT` was doing more damage than blocking imports — it was silently
redirecting data paths. `ingest_nal.py` looked for its tax roll in
`scripts/data/raw/`, `stage2.py` for declarations in `scripts/data/declarations/`,
and all three `config.json` reads pointed at a file that does not exist. Fixed
explicitly per file rather than through a shared bootstrap module, which would
have broken under `python -m` and under the test harness.

`Pillow` went to `requirements-dev.txt` rather than the runtime install: it is
only `make_icon.py`, whose output is already committed.

**Three things the tests found that the plan did not.** Each one is the kind
that only appears when something actually runs:

1. **The first-run failure is not a missing table.** `ATTACH` fails before any
   query, because the shared store is absent. Two different states with two
   different fixes, so `unbuilt_detail()` reports them separately — a missing
   table names the script that builds it, a missing store names the environment
   variables that point at one. Every other `OperationalError` still raises,
   pinned by a test, because a handler that swallows *database is locked* behind
   a friendly message is worse than the 500 it replaced.
2. **`_shared_root()`'s Windows fallback is a *relative* path on POSIX.**
   `Path(r"C:\Apps\_shared")` is absolute only on Windows; everywhere else an
   unconfigured run creates a directory named `C:\Apps\_shared` **in the working
   directory** and writes a store into it, rather than saying it is not
   configured. Folded into Portability below, now as a bug rather than a
   style note.
3. **Built-but-empty is a third first-run state.** `connect()` creates the schema
   as a side effect, so running any pipeline script once makes every table exist
   with zero rows. The UI then shows "0 condos" and cannot distinguish *the
   screen was built and found nothing* from *the screen was never built* — the
   same ambiguity 0.4 set out to remove. See `/api/selftest` below.

A test asserts that every `scripts\...py` path printed in an error message
actually exists; it immediately found nine more stale ones in docstrings that
the merge had left behind.

---

## Phase 1 — the signals that decide real deals

**7 days.** The stage-1 score is age, scale, concentration, absentee — the four
things easiest to compute from the roll, not the four that predict a termination.

### 1.1 Homestead status  ✅ done

FS 718.117 needs 80% approval **and no more than 5% objecting**, and the screen
had nothing on the objection side. `absentee_pct` was standing in, but absentee
is a mailing-address comparison — it puts a Brickell landlord who owns three
units and a retiree in Ohio in the same bucket. Homestead is the legal fact: it
marks the owner-occupant who objects, and whose payout floor is protected.

`homestead` and `homestead_val` on the unit, `homestead_pct` on the group and the
target, `score_resistance` alongside.

**The column name is resolved from the header, not hardcoded.** The roll cannot
be downloaded from this environment, and the NAL layout is revised between roll
years — a name that silently stopped matching would leave every unit reading
*not homesteaded*, which is worse than not having the field at all, because
nothing downstream could tell that apart from a building where nobody is. So
candidates are tried in order, the run prints which one matched, and unknown is
stored as `NULL` rather than `0`. The share is then computed over units the roll
can answer for: two homesteaded of two knowable is 100%, not 50%.

> **Confirm the candidate list against a real roll** —
> `python scripts/prospect/ingest_nal.py --show-header` prints the roll's columns
> and says which candidates are present. This is the one part of 1.1 that could
> not be verified here.

`score_resistance` is stored and sortable but **deliberately not folded into
`score`** — a fifth term without re-derived weights would silently move every
building in the app. That is 1.4. A test asserts the weights are still the
original four.

### 1.2 Milestone inspection and structural distress *(3-4 days)*

Post-Surfside, the building that terminates in 2026 is not the one that is
merely old — it is the one facing a milestone inspection under FS 553.899 plus a
structural integrity reserve study under 718.112(2)(g), staring at a five- or
six-figure per-unit special assessment the owners cannot fund. That building has
motivated sellers. An identically-aged building that already passed
recertification does not.

`milestone_due` is currently `age_years >= 30` — a guess at a fact Miami-Dade
publishes. RER maintains 40-year recertification status and unsafe-structure
cases by folio. Ingesting it splits the 30-plus cohort, which is most of the
shortlist, into two groups with completely different motivation profiles.

Add `recert_status`, `recert_due_date`, `unsafe_case_open` and a
`score_distress` term, and let the table filter on the open-case flag — that is a
search somebody runs every morning.

### 1.3 Sale qualification codes  ✅ done (resolves at ingest)

The README states the roll "carries no qualification code, so intra-family and
other non-arm's-length transfers are not filtered out." The published FDOR NAL
layout documents `QUAL_CD1` / `VI_CD1`, and the same pair for the second sale.
`ingest_nal.py` never asks for them, so this is untested rather than untrue.

**Built so the roll answers the question rather than me.** `QUAL_CD1`, `VI_CD1`,
`SALE_MO1` and the entire second sale are now resolved from the header the same
way homestead is: captured where present, `NULL` where absent, and reported
either way. `--show-header` states outright whether the README's note holds for
your roll.

The comps engine filters to an arm's-length set (`comps.arms_length_codes` in
config.json, defaulting to `01`/`02`) **only where the roll carries a code** — a
`NULL` means the column was absent, and dropping those would silently empty the
comp set on every roll predating the field. *We cannot tell* is not
*disqualified*. Excluded sales are counted and reported as a gap.

### 1.4 Re-weight and document *(1 day)*

Once 1.1-1.3 land, `score_weights` has four terms for a six-term problem.
Re-derive against whatever ground truth exists — Miami-Dade buildings that have
actually terminated since 2018 — and write the reasoning into the config the way
the existing `_weights_note` does. The score should stay auditable judgment
rather than become a model; the README is right about that.

---

## Phase 2 — what the buyout costs  ✅ done

**Scoped to comps only.** No redevelopment residual, no margin, no breakeven —
those need a revenue per new unit that is nowhere in this data, and an invented
one would be the least reliable figure in the app sitting exactly where the eye
lands. The question is only what the units cost.

`backend/prospect/economics.py`, and `GET /api/economics/{group_key}` with the
assumptions as query parameters so a sensitivity pass is three URL changes.

Each unit is priced at a $/SF against **its own living area** — the difference
between a defensible per-unit figure and a building-wide average that would
price a 500sf studio and a 2,000sf penthouse identically. Where that is
unavailable it steps down through the building median, nearby comps, then
assessed value, and every unit carries the step it landed on, so the estimate
reports its own quality: *"68% in-building comps, 22% nearby, 10% assessed."*

The price-to-assessment ratio is **measured from the building's own sales**
rather than assumed. A statewide constant would be wrong per building and
unfalsifiable; this one is checkable against the rows it came from.

### Two defects the tests found

**The comp engine was collapsing separate sales into phantom bulk deeds.**
`building_sales` grouped folios on `(year, price)`, so three units that each
genuinely sold for $440,000 in one year read as *one* $440,000 deed conveying
three units — and priced at $146,667 each. A threefold understatement, in the
direction that makes a building look cheap. Identical round prices are not
exotic: a tower of identical floorplans, or the nominal $10 considerations on
quitclaim transfers, collide exactly this way.

OR book/page **is** the recorded instrument, is already on the roll and already
ingested, and is now the grouping key. `(year, price)` stays as the fallback and
its cost is pinned by a test. The engine also moved out of `memo.gather()` so the
memo and the buyout estimate cannot drift into quoting different prices for the
same building.

**Shared mailing counted as control too eagerly.** With both concentration
percentages at zero, an arbitrary single mailing address marked a unit as already
owned. It now requires the address to cover more than one unit and to be the
stronger of the two signals.

### What it still cannot see

Mortgages and liens, which the tax roll does not carry (Phase 7.2), and the
statutory payout floor for homestead owners, which needs 1.1's data on a real
roll. Every estimate prints both limits.

---

## Phase 3 — the map  ✅ done

**6 days.** The reference point is [Gridics](https://map.gridics.com/us/fl/miami-beach),
which is Miami-built on Miami 21 and whose whole argument is in its URL fragment:
`#12.85/25.79458/-80.12569/0/45` — zoom, centre, bearing, and **45° of pitch**. It
reads zoning as massing. Groundwork draws the same polygons flat.

What is there today: a MapLibre map on a CARTO raster basemap, a ten-family
ZoLa-style zoning palette, live municipal polygons for the wired cities, and
pre-baked tri-county layers. What is not: any pitch, any bearing, any
`fill-extrusion`, and — until this phase — correct colours in the home market.

### 3.1 Zoning colour accuracy *(done)*

`_zone_category` applied one set of generic letter rules nationally, so Miami,
the market this app exists for, had the worst colour accuracy in it:

| Code | Was | Is | Miami 21 meaning |
|---|---|---|---|
| `CS` | commercial | open | Civic Space — parks, drawn as retail |
| `CI`, `CI-HD` | commercial | special | Civic Institutional |
| `D1` | other | industrial | Work Place |
| `D2` | other | industrial | Industrial |
| `D3` | other | industrial | Marine |
| `EU-*` | other | residential | Miami-Dade Estate Use |

These are real ambiguities rather than oversights — `CS` is Civic Space under
Miami 21 and Commercial Service in half the other wired cities — so the local
vocabulary is now applied only where it is in force. `_zone_category` takes an
optional municipality, both overlay call sites pass it, and a test pins the
generic behaviour so Miami's dictionary cannot leak into Tampa's.

Also added: `_zone_stories`, which recovers the storey cap a form-based code
*declares* — `T6-8` is eight storeys and `T6-80` is eighty — published as
`max_stories` on every overlay feature. Codes that declare no height report
`None` rather than a guess. One property, two consumers: 3.2 and 3.3.

### 3.2 Intensity inside the family  ✅ done

Every T6 tier currently paints one colour, so `T6-8-O` and `T6-80-O` are
indistinguishable on the map. For a termination screen that is the wrong thing to
hide: the intensity tier *is* the redevelopment capacity, and it is the reason to
look at a building whose by-right envelope is smaller than what is standing.

Ramp lightness within each family by `max_stories` rather than adding colours —
the ten-family legend stays readable and the tiers separate. Legend gains a
lightness scale, not ten more swatches.

### 3.3 Three dimensions  ✅ done

- `NavigationControl({ showCompass: false })` → `true`. There is currently no
  affordance for rotate or tilt at all.
- A `zoning-3d` `fill-extrusion` layer keyed to `max_stories × 10ft`, toggled in
  Layers, so the envelope reads as volume. Extrude only where the code declares a
  height; flat where it does not, and say which in the legend.
- Pitch and bearing into the URL hash, which currently carries only `z/lat/lon/sel/bm`.
  A pitched view is not shareable today — the whole point of the Gridics link.

The honest limit: this draws the *zoning envelope*, not the building. Real massing
needs setbacks, lot coverage and site geometry, which `capacity.py` already warns
it ignores. Label it as the envelope and it is useful; label it as a building and
it is a lie with a shadow on it.

### 3.4 The home market has no live zoning service  ✅ partly

`METRO_ZONING` wires Orlando, Tampa, Jacksonville, St. Pete, Clearwater,
Sarasota, Tallahassee, West Palm Beach — and **not Miami-Dade**. Tri-county
zoning comes only from the pre-baked GeoJSON that `fetch_layers.py` downloads
into `data/`, which is gitignored and absent from a fresh clone. So a new install
that has not run the fetch script shows an empty zoning layer over Miami and a
populated one over Tampa, with nothing in the UI explaining the difference.

**Correction: the layer was not silent.** It already reported an empty result —
it reported the wrong *cause*, saying tri-county zoning is handled by "the layers
above", which is true only once `fetch_layers.py` has been run. Those layers are
gitignored and absent from a fresh clone, so the message sent the user to an
empty checkbox. It now distinguishes *no live service here* from *the local layer
was never fetched* and names the script.

Still open: wiring the Miami-Dade and City of Miami services into
`METRO_ZONING` so the home market has live coverage at all. That needs the
service URLs verified against the live endpoints, which this environment cannot
reach.

---

## Phase 4 — charts and documents  ✅ done

**8 days.** The application contains one chart, and the document it prints
carries four print-geometry bugs.

`market_dd.py:532`, `_sparkline()` — a bare polyline with a hardcoded
`#1f3b57` stroke, no axis, no labels, no theme awareness, and
`preserveAspectRatio="none"`, which stretches the line non-uniformly so the
slopes it draws are not the slopes in the data. Four uses in the DD report. Zero
charts in `app.js` or `workspace.js`.

### 4.1 The chart module  ✅ done

`frontend/charts.js`, hand-rolled SVG. The app has no build step — everything is
plain script tags — and line, bar, area, scatter and strip plots are a few
hundred lines of geometry. A charting library would cost more in theming fights
than it saves. Non-negotiables:

- colours read from the CSS custom properties already in `styles.css`, so charts
  follow the app's theme instead of carrying their own
- real axes with labelled ticks at values the chart actually reaches
- correct `preserveAspectRatio`, `viewBox` sizing, tabular figures
- `<title>` and `<desc>` on every chart, and keyboard-reachable hit targets

Fix the server-side `_sparkline` against the same geometry while in there. It is
a twenty-line correction to something that currently draws misleading slopes, and
that is a bug regardless of where the new charts live.

### 4.2 The charts that earn their place  ✅ done

Five, in build order. Each one answers a question the app currently answers in
prose or not at all.

1. **Unit price distribution in a building.** ✅ Built, and live in the Records
   drawer above the comps table. A strip plot of every recorded per-unit price,
   bulk-deed sales marked separately, the single-unit median drawn as a line. The README's biggest data trap — 148 folios showing up to
   $4.9M each that are really a handful of bulk deeds — is currently a paragraph
   of warning. It should be a picture, because the picture is unmistakable.
2. **The buyout waterfall** from 2.2. ✅ In the Records drawer, fed by `/api/economics`.
3. **Ownership concentration.** ✅ A stacked bar: top owner, next four, long
   tail — one hue getting lighter, since the segments are ordered by size rather
   than competing identities.
4. **Population components.** ✅ **Not** a stacked area, which is what this
   roadmap called for and is the wrong form: domestic migration goes negative —
   Columbus added 83k people on *negative* domestic migration — and a stacked
   area cannot show a negative component without lying about the total. Built as
   a diverging bar instead, positives above zero and negatives below, in the
   market drawer.
5. **Permits against migration, all metros.** ✅ An *emphasis* scatter: the
   selected metro carries the accent and the rest are context gray. Colouring 469
   points by identity would bury the one the reader came for. It makes the
   `tightness` composite auditable at a glance, which is what the codebase says
   it wants from it.

### 4.3 The memo prints badly  ✅ done

The memo's typography is considered — the problems are geometry, and they are the
kind that only appear on paper.

1. **Double margins.** `@page` insets `0.7in` on each side, then `.pad` adds
   another `0.7in` inside it. On Letter that leaves about `5.7in` of live width
   for tables built for more, so columns cramp and wrap.
2. **The cover spills a blank page.** `.cover` is `min-height: 9.6in` against
   `11in − 0.75in − 0.85in = 9.4in` of available height. It overflows by two
   tenths of an inch, and `page-break-after: always` then adds a second break —
   so every memo has an empty page two.
3. **Multi-page tables lose their headers.** No `thead { display: table-header-group }`
   anywhere, so a forty-row sales table continues onto page three with no column
   labels.
4. **`section { page-break-inside: avoid }` applies to sections taller than a
   page**, where it either does nothing or pushes a large blank gap ahead of the
   section. Avoidance belongs on rows and on small blocks, not on containers that
   can exceed a sheet.

Also: `.kv` uses `repeat(auto-fill, minmax(1.6in, 1fr))`, so a count that does not
divide evenly leaves one cell stretched across the final row, and there is no
`orphans` / `widows` control anywhere.

None of that is subjective, so fix it first and look again — "not clean" may be
entirely these four things, or it may turn out to be the Georgia-and-Helvetica
pairing underneath them, which is a different job.

---

## Phase 5 — where the population is going  ✅ done

**7 days.** National scan first, then metro-level flows.

`migration_flow` holds 109,044 IRS SOI county-to-county rows with a `direction`
column and **AGI attached to every flow** — the only free source that says what
movers earn. It is read once, in `build_markets.py:143`, to compute a
pipe-joined `top_origins` string and an AGI premium. The `out` direction is
ingested and never surfaced anywhere in the application.

### 5.1 The flow query layer  ✅ done

Aggregate county-to-county to CBSA-to-CBSA in both directions and net them.

**Correction: no index was needed.** This roadmap said outbound queries would
table-scan without an `origin_fips` index. They do not, because the column names
mean something other than what they say:

| column | what it holds |
|---|---|
| `dest_fips` | the county the row is **about** — arrivals for `in`, departures for `out` |
| `origin_fips` | the counterparty |

So `origin_fips` holds the *destination* on an outbound row, both directions key
on the same subject, and `ix_flow_dest (dest_fips, year)` already covers every
query here. `build_markets.py` documents this; reading the names literally gives
numbers that look plausible and are wrong, which is what most of the flow tests
are about.

    GET /api/flows/national?year=&metric=returns|agi
    GET /api/flows/{cbsa}?direction=in|out|net

Three limits belong in the response payload, not in a footnote, because each one
changes how a number should be read:

- **SOI lags about two years.** This is not a current-conditions indicator.
- **Flows under ten returns are suppressed.** Small corridors are missing
  entirely, so visible flows do not sum to the county total.
- **It counts tax filers, not people.** Non-filers — low income, many elderly —
  are invisible, which biases exactly the populations a relocation plan has to
  deal with.

### 5.2 National scan  ✅ done

Every metro ranked by net household gain and by net AGI gain. The interesting
part is the gap between the two: a metro gaining households while losing AGI is
gaining poor and losing rich, and no single-number ranking shows that.

Feed both as new metrics into the existing `/api/metro-trends` dropdown — the
choropleth layer already exists and already shades by national percentile, so
this is two new columns rather than new map work. Pair it with the scatter from
4.2: net households on one axis, net AGI on the other, quadrants labelled.

### 5.3 Metro flow view  ✅ done

For one metro: top inbound and outbound corridors, netted, with AGI per
household on each. Paired horizontal bars from a shared centre — a chord diagram
is the photogenic choice and unreadable at fifteen corridors. Both gross flows
stay visible rather than only the net: a corridor moving 900 households each way
nets to nothing and is not the same as a corridor nobody uses.

Miami-Dade is the demonstration case and it is already in the README: −67,418
domestic against +123,835 international in 2024. The app can state those two
totals today. It cannot say where the 67,418 went or what they earned, and that
is the question worth answering.

---

## Phase 6 — movement, not level  ✅ done

**9 days.** This slipped down the list and it is worth saying why it should not
slip off it.

### 6.1 Roll vintage history and assembly velocity  ✅ done

`build_targets.py` opens with `DELETE FROM condo_group` and `DELETE FROM target`.
Every rebuild destroys the prior state, so the app can only answer *who owns a
lot of this building today* and never *who is buying it*.

Add a `target_snapshot` table keyed `(group_key, roll_year)` holding the
ownership columns, written on every rebuild, and derive `conc_delta_1yr`,
`new_entity_buyers`, and an `assembly_flag` for concentration rising alongside
entity share.

A building at 45% single-owner has probably already been bought by somebody who
knows what they are doing. A building that went 6% to 19% since the last roll is
the one to be early on. The current screen ranks the first one higher.

**Paired with Phase 2, this is the whole question in two numbers: what would it
cost me, and is somebody already doing it.** Neither half is worth as much alone.
This needs no new data source — only the discipline to stop deleting history.

### 6.2 Beneficial-owner clustering  ✅ done (Sunbiz deferred)

Shared mailing address is a clever proxy for one buyer behind several LLCs and
the weakest link in the concentration signal — it misses anyone using a
registered-agent address or separate mailboxes, and false-positives where a
management company receives mail for many owners.

**Built without Sunbiz, because the registry cannot be downloaded from here and
a parser written blind against a file format nobody has seen is not worth
shipping.** The clustering itself does not need it: owners are unioned
transitively over two auditable rules already computable from the roll — a
mailing address shared by two or more owner names, and a name series between
entities (`FLAGLER HOLDINGS I/II LLC`). Transitivity is what makes it worth
doing: if one Flagler entity shares an address with `BRICKELL 27 LLC`, all three
are one buyer, which neither rule finds alone.

Every rule is conservative past the point of leaving real links on the table,
because the constraint `normalize_owner` already states applies double here: a
false merge fabricates the number the whole tool is ranked on. A stem below two
tokens is not used (`BRICKELL 900` and `BRICKELL 1200` are two addresses, not one
sponsor); individuals are never merged on a name, only entities; and an address
shared by more than a dozen distinct names is a mail drop, not a buyer. Each edge
carries its evidence so a cluster can be disbelieved on its specifics.

Sunbiz — officers and registered agents — remains the obvious next edge type and
plugs into `EDGE_RULES` without touching anything else.

### 6.3 What moved since the last roll  ✅ done

Scoped down for a personal tool: a saved-search subscription system is
multi-user machinery, and the useful half is the digest. `GET /api/movement`
compares the two most recent vintages — how many buildings are concentrating,
dispersing, changing hands, and the biggest movers — with a panel above the
Records table, plus `sort=assembling` and a *Being assembled* filter on the table
itself. With one vintage it says so rather than rendering zeros.

---

## Phase 7 — declaration review at scale  ✅ done

**9 days.** Stage 2 is the strategic asset and the most manual thing here.

### 7.1 OCR  ✅ done

`cmd_extract` bails with "this is likely a SCANNED image PDF" below 200
characters of extracted text. The target set is buildings recorded roughly
1965-1990, and essentially all of those declarations are scanned images — so the
extractor gives up on precisely the documents it exists to read. Wire Tesseract
or a hosted OCR behind `pdf_text`, cache the text beside the PDF, and record
which path produced it: OCR output is noisier and `find_threshold` needs to know
that before reporting high confidence.

### 7.2 Bring stage 2 into the app  ✅ done

Retrieval being manual is a sound decision. The *review* being CLI-only is not.
Today an analyst runs `--worklist`, gets an xlsx, saves PDFs into a folder named
by folio prefix, and runs `--extract` from a terminal. Make it: upload in the
building drawer, see findings beside their verbatim snippets, accept or correct,
save. The form and `POST /api/target/{key}/stage2` already exist.

Same document pipeline, same drawer: **mortgage and lien entry**, which is the
one input Phase 2 cannot derive. Miami-Dade Clerk Official Records has it and
shares this retrieval problem, so do them together. Until then let an analyst
enter aggregate debt by hand and have the waterfall flag every figure computed
without it.

### 7.3 Extractor hardening  ✅ done

`declaration.py` is validated by four hand-written samples it passes by
construction. Before anyone relies on `termination_threshold` at volume it needs
50-100 real declarations, hand-labelled, with precision and recall reported per
field. Regex over legal prose fails in specific, learnable ways; the tool should
know its own error rate and print it.

Four more fields are worth extracting, because the three captured today are not
the only ones that kill a deal: the **amendment chain** (the operative threshold
is the original as amended, and today an amendment PDF overwrites the original's
findings in the same columns), **right of first refusal**, **recreation leases**,
and **55+ covenants**.

### 7.4 Re-test Clerk retrieval  ✂ dropped

Worth thirty minutes with the network tab before accepting "no documented query
API" permanently. The SPA is talking to something. If a stable JSON endpoint
exists, 7.1 and most of 7.2 collapse into an overnight job. If not, write down
what was tried and when, and keep the manual path.

---

## Phase 8 — coverage

**5 days.** Deliberately last: three counties of a mediocre screen is worse than
one county of a good one.

`nal_condo_unit.county` already exists, and the FDOR NAL and DBPR registry are
both statewide files with identical structure, so Broward and Palm Beach are the
same ingest against a different URL with a county-scoped `group_key`. The work is
not the ingest — it is that `capacity.py` reads Miami-Dade zoning only and
`site_coords` hardcodes EPSG:2236 plus a Miami-Dade bounding box, both of which
need to become per-county configuration. Broward, then Palm Beach, then the rest
of the coastal counties.

---

## Also worth doing

- **Deal pipeline state.** ✅ Done. `deal_state` and `deal_note`, deliberately
  OUTSIDE `target`: a rebuild rewrites that table every time a new roll lands,
  and losing where a deal had got to because the data refreshed would be the
  worst bug in this app.
- **Portability.** ✅ Done, and it was **five** copies, not three. One
  `backend/shared_paths.py`, and the fallback is platform-aware — , and its
  `C:\Apps\_shared` fallback is a *relative* path off Windows — so an
  unconfigured POSIX run creates that name as a directory in the working
  directory instead of reporting that the store is not configured (found in
  Phase 0; `tests/conftest.py` now cleans it up). Make it one import`launch.py` and `install.py` stay Windows-only, which is
  fine for one box. A test asserts the resolver exists in exactly one place.
- **Roll provenance in the UI.** ✅ Done — county, roll year, roll type and the
  last build date above the Records table.
- **Failures are silent in the map UI.** `loadTargetPins` and several sibling
  fetches are `catch (e) { return; }` — an endpoint that 500s and an empty result
  set look identical to the user, which is exactly the ambiguity that makes "is
  the map working" hard to answer. Surface a one-line failure state per layer. ~1 day.
- **`/api/selftest`.** ✅ Done. Reports every dataset, its path, whether it
  resolved, its row count and the script that builds it — so *built and found
  nothing* is distinguishable from *never built*, which a 503 cannot say.
  Previously: the app needs ~53MB of fetched layers plus a ~330MB shared store,
  and had no way to report which are present. A route listing each
  dataset, its path, and whether it resolved would make the question answerable
  by the app instead of by reading logs. It also closes the built-but-empty gap
  Phase 0 left open: row counts distinguish a screen that found nothing from one
  that was never built, which a 503 cannot. ~0.5 day.
- **Basemap provider risk.** ✅ Happened. CARTO began requiring an API key and
  started serving tiles watermarked *API KEY REQUIRED* — a 200 response, so
  nothing errored, nothing retried, and the map quietly rendered a nag screen.
  All four CARTO dependencies (two basemaps, the satellite label overlay, and the
  **glyph endpoint**, which every text layer depends on) moved to keyless Esri
  and the OpenMapTiles font CDN, with per-service attribution and a visible
  banner for the half of this failure mode that does raise an error. Pinned by
  tests. The lesson stands: a gated tile is not a failed one.
- **FastAPI `regex=` is deprecated** in favour of `pattern=` at
  `site_screen.py:917` and `app.py:2615`. Two lines, and a warning that becomes a
  break. ~10m.
- **Memo and DD report charts.** ✅ The broken one is fixed: `_sparkline` now
  shares `charts.js`'s geometry, labels its endpoints, carries no literal colour
  and is described for a screen reader. Richer server-side charts remain
  available but unbuilt — the fix was the part that was wrong.

---

## Sequencing

```
Phase 0  ██                              1d   DONE
Phase 1  ██████████████                  7d   1.1 DONE
Phase 2  ████████                        4d   DONE (comps only)
Phase 3  ████████████                    6d   DONE
Phase 4  ████████████████                8d   DONE
Phase 5  ██████████████                  7d   DONE
Phase 6  ██████████████████              9d   DONE
Phase 7  ██████████████████              9d   DONE
Phase 8  ██████████                      5d   coverage
                                        ───
                                        58d
```

Three dependencies are real and cheap to honour: **1.1 before 2.2** (the
homestead floor), **4.1 before 2.3 and 5.2** (the chart module), and **3.1 before
3.2 and 3.3** — already met, since `max_stories` is what the intensity ramp and
the extrusion height both read.

Pull `charts.js` forward into the Phase 2 window rather than treating Phase 4 as
a block: the waterfall wants it, and building the module against a first real
consumer produces a better module than building it speculatively.

If only one week: **Phase 0 + 1.1 + 2.1** — homestead status and per-unit
valuation, which together turn the drawer from a fact sheet into an estimate.

---

## Answered

**Personal tool, not a product.** Phase 8 (coverage) and the multi-user items —
pipeline state, watchlist alerts — drop down accordingly. Portability stays only
for the `_shared_root()` bug, because that one bites a single user on a single
machine the moment the path is not configured.

**Sellout assumption: show what we have.** Phase 2 is comps only, as built. No
residual.

**Declaration retrieval cost: dropped.** 7.4's timeboxed Clerk re-test comes off
the list. The manual path stays, and 7.1 (OCR) still matters because the target
set is 1965–1990 declarations, which are scans.

**Labelled terminations — you already have them.** DBPR tracks association
status, and `ingest_dbpr.py` already writes `Primary Status` and
`Secondary Status` into `dbpr_association` for all 5,456 Dade associations.
Nothing in the app has ever read those two columns. One query against your
existing database:

```sql
SELECT primary_status, secondary_status, COUNT(*) n
FROM dbpr_association GROUP BY 1, 2 ORDER BY n DESC;
```

Whatever that returns for terminated or dissolved associations, joined back to
`target` on `project_number`, is the labelled set 1.4 needs — at zero collection
cost. I could not run it from here: this environment reaches GitHub and nothing
else, so `floridarevenue.com`, DBPR and the Miami-Dade open data portal are all
blocked. Send me the output and 1.4 becomes measurable rather than a judgment
call.

---

*Informational only. Nothing in this document, or in the tool it describes, is
legal, valuation or engineering advice. The statutory citations here are
starting points for counsel, not conclusions.*
