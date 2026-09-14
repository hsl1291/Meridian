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

## Phase 0 — make it run again

Blocking. **1 day.** Nothing below can be verified until this lands.

| # | Work | Effort |
|---|---|---|
| 0.1 | Fix path wiring in all 7 `scripts/prospect/*.py`: `ROOT` to `parents[2]`, imports to `backend.prospect.*`, `CFG` to `backend/prospect/config.json` | 2h |
| 0.2 | Add the four undeclared third-party deps to `requirements.txt`: `pyproj` (build_targets), `pypdf` (stage2), `requests` (every ingest), `Pillow` (make_icon) | 15m |
| 0.3 | `tests/` + CI: import every module, run `declaration._selftest()`, boot the app with an empty DB and assert every route in `/api/docs` answers without a 500 | 4h |
| 0.4 | Decide what the Dockerfile is for — it fetches map layers but never builds `prospect.db`, so the image has no condo data. Either run the prospect pipeline in the build or say in the file that the image is map-only | 1h |

There are currently **zero tests** in the repo. The 4-sample `_selftest` inside
`declaration.py` is the only executable check of anything, and it is not wired to
run anywhere. A single CI job that catches "the scripts don't import" would have
caught the merge regression on the day it happened.

---

## Phase 1 — the signals that decide real deals

**7 days.** The stage-1 score is age, scale, concentration, absentee — the four
things easiest to compute from the roll, not the four that predict a termination.

### 1.1 Homestead status *(2 days, and a dependency for Phase 2)*

FS 718.117 needs 80% approval **and no more than 5% objecting**. The screen
models nothing on the objection side. `absentee_pct` is standing in for it, but
absentee is a mailing-address comparison — it puts a Brickell landlord who owns
three units and a retiree in Ohio in the same bucket, when the legal fact that
matters is homestead exemption.

This field does double duty and that is why it is first. A homesteaded
owner-occupant is the one who objects **and** the one whose payout floor is
protected — so the same column drives the resistance term in the score and the
floor in the buyout model. The FDOR NAL carries homestead exemption fields per
parcel; `ingest_nal.py` reads twenty columns and drops them.

Add `homestead` per unit, `homestead_pct` per group, a `score_resistance` term,
and expect the top hundred to reorder substantially. That is the point.

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

### 1.3 Sale qualification codes — check the header first *(half a day if present)*

The README states the roll "carries no qualification code, so intra-family and
other non-arm's-length transfers are not filtered out." The published FDOR NAL
layout documents `QUAL_CD1` / `VI_CD1`, and the same pair for the second sale.
`ingest_nal.py` never asks for them, so this is untested rather than untrue.

Print the header of the cached zip. If the columns are there, the comps caveat
that currently qualifies every price in every memo — and every figure Phase 2
derives from those comps — goes away for one afternoon's work. While in there,
capture `SALE_MO1` (month, not just year) and the **second** sale: a unit that
traded twice in three years is a signal the current schema cannot see at all.

### 1.4 Re-weight and document *(1 day)*

Once 1.1-1.3 land, `score_weights` has four terms for a six-term problem.
Re-derive against whatever ground truth exists — Miami-Dade buildings that have
actually terminated since 2018 — and write the reasoning into the config the way
the existing `_weights_note` does. The score should stay auditable judgment
rather than become a model; the README is right about that.

---

## Phase 2 — what the buyout costs

**6 days.** The drawer shows every input to this number and never computes it.

What is already on screen at `workspace.js:245-380`: units, year built, age,
declaration year, assessed and land value per unit, top-owner share, every owner
with their unit count and percentage, shared mailing addresses, in-building
single-unit median, bulk-deed median per unit, median $/SF, and nearby
buildings within two miles. The gap is arithmetic, not data.

### 2.1 Per-unit valuation *(2 days)*

`memo.gather()` already pulls `tot_lvg_area` alongside every recorded sale and
computes a median $/SF. Nothing multiplies them back out. Per-unit fair market
value is that $/SF against each unit's own living area — which is the difference
between a building-wide average and a number you can defend unit by unit.

New `backend/prospect/economics.py`, with an explicit basis on every unit so the
estimate can report its own quality:

| Basis | When it applies |
|---|---|
| `comp_psf` | unit has living area, building has recent single-unit sales |
| `building_median` | unit has no living area — fall back to the building's median per unit |
| `nearby_psf` | building has no recent single-unit sales — use the comp set within radius |
| `assessed_ratio` | neither — assessed value times a stated ratio, flagged as weakest |

**Assessed value is not market value and must never be used as though it were.**
Florida just value runs below market, and Save Our Homes caps compress
homesteaded units further — which biases exactly the units whose payout floor
matters most. JV is a floor indicator here, nothing more.

### 2.2 The waterfall *(2 days)*

```
  units already controlled          no buyout
+ units to acquire  x  FMV          from 2.1
+ homestead floor where it binds    needs 1.1
+ holdout premium on the tail       the last 10% is where deals die
= total acquisition basis
- redevelopment residual            capacity.py best case x sellout/unit
= margin, and the unit count it breaks even at
```

Expose it as `/api/economics/{group_key}` with the assumptions as query
parameters, so a sensitivity pass is three URL changes rather than a rebuild.
Mortgage balances are the one input not in the repo — see Phase 6.2.

### 2.3 Surface it, and sort on it *(2 days)*

A new drawer section above Sales comparables: the number, the waterfall, and the
assumption inputs inline. Then the part that changes how the tool is used —
**make estimated basis per unit and estimated margin sortable columns in the main
table.** Ranking 6,160 buildings by deal economics rather than by screen score is
a different tool, and it falls out of 2.1 almost for free.

Print the basis mix next to every estimate: *"68% of units valued from
in-building comps, 22% nearby, 10% assessed ratio."* An estimate that will not
say how it was built is not a screening figure, it is a guess with a dollar sign.

> Screening estimate, not an appraisal. Have counsel review the 718.117 logic
> before it drives a decision, and have `economics.py` print the subsection it is
> applying next to each figure — the way `capacity.py` already carries a `basis`
> string — refusing to produce a number it cannot cite.

---

## Phase 3 — the chart layer

**5 days.** The whole application contains one chart.

`market_dd.py:532`, `_sparkline()` — a bare polyline with a hardcoded
`#1f3b57` stroke, no axis, no labels, no theme awareness, and
`preserveAspectRatio="none"`, which stretches the line non-uniformly so the
slopes it draws are not the slopes in the data. Four uses in the DD report. Zero
charts in `app.js` or `workspace.js`.

### 3.1 The module *(2 days)*

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

### 3.2 The charts that earn their place *(3 days)*

Five, in build order. Each one answers a question the app currently answers in
prose or not at all.

1. **Unit price distribution in a building.** A strip plot of every recorded
   per-unit price, bulk-deed sales marked separately, the single-unit median
   drawn as a line. The README's biggest data trap — 148 folios showing up to
   $4.9M each that are really a handful of bulk deeds — is currently a paragraph
   of warning. It should be a picture, because the picture is unmistakable.
2. **The buyout waterfall** from 2.2.
3. **Ownership concentration.** A stacked bar: top owner, next four, long tail.
   Whether a building is already being assembled should read at a glance rather
   than by scanning a table of percentages.
4. **Population components, stacked area.** Natural increase, domestic migration,
   international migration. The DD report explains Columbus entirely in prose —
   +83k people but negative domestic migration and 73% international — and draws
   nothing. This is the chart that sentence is describing.
5. **Permits against migration, all 469 metros.** A scatter with the selected
   metro highlighted and the diagonal marked. It makes the `tightness` composite
   auditable at a glance, which is what the codebase says it wants from it.

---

## Phase 4 — where the population is going

**7 days.** National scan first, then metro-level flows.

`migration_flow` holds 109,044 IRS SOI county-to-county rows with a `direction`
column and **AGI attached to every flow** — the only free source that says what
movers earn. It is read once, in `build_markets.py:143`, to compute a
pipe-joined `top_origins` string and an AGI premium. The `out` direction is
ingested and never surfaced anywhere in the application.

### 4.1 The flow query layer *(2 days)*

Aggregate county-to-county to CBSA-to-CBSA in both directions and net them.
`ix_flow_dest` covers `(dest_fips, year)` only, so every outbound query is a
table scan — add the matching `origin_fips` index first.

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

### 4.2 National scan *(2 days)*

Every metro ranked by net household gain and by net AGI gain. The interesting
part is the gap between the two: a metro gaining households while losing AGI is
gaining poor and losing rich, and no single-number ranking shows that.

Feed both as new metrics into the existing `/api/metro-trends` dropdown — the
choropleth layer already exists and already shades by national percentile, so
this is two new columns rather than new map work. Pair it with the scatter from
3.2: net households on one axis, net AGI on the other, quadrants labelled.

### 4.3 Metro flow view *(3 days)*

For one metro: top inbound and outbound corridors, netted, with AGI per
household on each. Paired horizontal bars — inbound left, outbound right, net in
the middle — rather than a chord diagram, which looks better in a screenshot and
reads worse at fifteen corridors.

Miami-Dade is the demonstration case and it is already in the README: −67,418
domestic against +123,835 international in 2024. The app can state those two
totals today. It cannot say where the 67,418 went or what they earned, and that
is the question worth answering.

---

## Phase 5 — movement, not level

**9 days.** This slipped down the list and it is worth saying why it should not
slip off it.

### 5.1 Roll vintage history and assembly velocity *(3 days)*

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

### 5.2 Sunbiz entity resolution *(4 days)*

Shared mailing address is a clever proxy for one buyer behind several LLCs and
the weakest link in the concentration signal — it misses anyone using a
registered-agent address or separate mailboxes, and false-positives where a
management company receives mail for many owners.

Florida's Division of Corporations publishes the full registry as a free bulk
quarterly download: entity name, registered agent, officers, principal address.
Joining `owner_norm` to it builds a real graph and collapses beneficial
ownership properly. *"These four LLCs holding 31% share a manager"* is a
sentence the tool cannot produce today.

### 5.3 Watchlists and change alerts *(2 days)*

Once 5.1 exists, a saved filter that re-evaluates each roll and reports what
entered, exited or moved is small work on top of it. This is what makes the app
something opened weekly rather than quarterly.

---

## Phase 6 — declaration review at scale

**9 days.** Stage 2 is the strategic asset and the most manual thing here.

### 6.1 OCR *(2 days)*

`cmd_extract` bails with "this is likely a SCANNED image PDF" below 200
characters of extracted text. The target set is buildings recorded roughly
1965-1990, and essentially all of those declarations are scanned images — so the
extractor gives up on precisely the documents it exists to read. Wire Tesseract
or a hosted OCR behind `pdf_text`, cache the text beside the PDF, and record
which path produced it: OCR output is noisier and `find_threshold` needs to know
that before reporting high confidence.

### 6.2 Bring stage 2 into the app *(3 days)*

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

### 6.3 Extractor hardening *(3 days)*

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

### 6.4 Re-test Clerk retrieval *(1 day, timeboxed)*

Worth thirty minutes with the network tab before accepting "no documented query
API" permanently. The SPA is talking to something. If a stable JSON endpoint
exists, 6.1 and most of 6.2 collapse into an overnight job. If not, write down
what was tried and when, and keep the manual path.

---

## Phase 7 — coverage

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

- **Deal pipeline state.** `stage2_verified` is the only workflow field on a
  target. A status enum (screened / researching / contacted / LOI / dead) with a
  timestamp and a note log turns the table into something a team works from. ~2 days.
- **Portability.** `C:\Apps\_shared` is the fallback in three separate copies of
  `_shared_root()`; `launch.py` and `install.py` are Windows-only. If this only
  ever runs on one Windows box that is fine — but the Dockerfile suggests
  otherwise, and the three duplicated functions should be one import regardless. ~1 day.
- **Roll provenance in the UI.** `ingest_log` is populated and exposed through
  `/api/condo/stats`, but a user reading a score cannot see it came from a
  *preliminary* 2026 roll. Put the vintage in the Records header. ~2h.
- **Memo and DD report charts.** Out of scope for Phase 3, which is in-app only.
  Worth revisiting once `charts.js` exists, since the geometry ports directly and
  the memo is what a recipient actually sees. ~2 days.

---

## Sequencing

```
Phase 0  ██                              1d   blocking
Phase 1  ██████████████                  7d   1.1 gates Phase 2
Phase 2  ████████████                    6d   what the buyout costs
Phase 3  ██████████                      5d   3.1 gates 2.3 and 4.2
Phase 4  ██████████████                  7d   where the population is going
Phase 5  ██████████████████              9d   who is already assembling
Phase 6  ██████████████████              9d   declarations at scale
Phase 7  ██████████                      5d   coverage
                                        ───
                                        49d
```

Two dependencies are real and cheap to honour: **1.1 before 2.2** (the homestead
floor), and **3.1 before 2.3 and 4.2** (the chart module). Pull `charts.js`
forward into the Phase 2 window rather than treating Phase 3 as a block — the
waterfall wants it, and building the module against a first real consumer
produces a better module than building it speculatively.

If only one week: **Phase 0 + 1.1 + 2.1** — homestead status and per-unit
valuation, which together turn the drawer from a fact sheet into an estimate.

---

## Open questions

1. **Is this a personal tool or a product?** Everything in Phase 7, and most of
   Portability, is only worth doing for the second. The install story — desktop
   shortcut, start at logon, self-heal task — reads personal; the Dockerfile
   reads product.
2. **Is there a labelled set of actual terminations?** Miami-Dade terminations
   since 2018 are a matter of record. Twenty of them would turn the score from
   defensible judgment into something measurable, and would settle 1.4. They
   would also give Phase 2 something to check its estimates against.
3. **What does a sellout assumption come from?** Phase 2's residual needs a
   revenue per new unit. The nearby comps give resale; new construction in the
   same submarket does not appear in this data at all. Either it is an input the
   user supplies, or it needs a source.
4. **How much does declaration retrieval cost in practice?** If a title company
   or a Clerk bulk order can produce declarations for the whole shortlist for a
   few hundred dollars, 6.1 and 6.4 get deprioritised and the money is the
   better tool.

---

*Informational only. Nothing in this document, or in the tool it describes, is
legal, valuation or engineering advice. The statutory citations here are
starting points for counsel, not conclusions.*
