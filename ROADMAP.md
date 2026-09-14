# Roadmap

Where the condo takeover module goes next, and why in that order.

Written against commit `5d63c0f`, the Sitefolio + Prospect merge. Effort figures
are working days for one person who already knows this codebase.

---

## The honest starting position

Three things are true at once, and the sequencing below follows from them.

**The screen is good.** The bulk-deed handling in comps, keeping
`kaufman_original` and `kaufman_by_amendment` apart, excluding association-owned
units from the concentration signal, refusing to pass off plat-wide `lot_size`
as site area — these are the judgment calls that separate a tool somebody trusts
from a spreadsheet with a colour ramp. Most of what follows builds on that
instinct rather than replacing it.

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

**The moat is not the screen.** Every developer in Miami can rank condos by age
and unit count. Two-stage declaration review is a genuine edge, but a finite one
— the buildings with original Kaufman language and 200 units are a set that gets
enumerated once and then stays enumerated. What does not get enumerated is
*movement*: which building went from 8% to 22% single-owner in one roll year, and
which three LLCs buying into it share a registered agent. The roll is a snapshot
and the pipeline throws away last year's. Recovering that is the highest-value
thing on this document (Phase 2.1) and it is cheap.

---

## Phase 0 — make it run again

Blocking. Roughly **1 day**. Nothing below can be verified until this lands.

| # | Work | Effort |
|---|---|---|
| 0.1 | Fix path wiring in all 7 `scripts/prospect/*.py`: `ROOT` to `parents[2]`, imports to `backend.prospect.*`, `CFG` to `backend/prospect/config.json` | 2h |
| 0.2 | Add the four undeclared third-party deps to `requirements.txt`: `pyproj` (build_targets), `pypdf` (stage2), `requests` (every ingest), `Pillow` (make_icon) | 15m |
| 0.3 | `tests/` + CI: import every module, run `declaration._selftest()`, boot the app with an empty DB and assert every route in `/api/docs` answers without a 500 | 4h |
| 0.4 | Decide what the Dockerfile is for — it fetches map layers but never builds `prospect.db`, so the image has no condo data. Either run the prospect pipeline in the build or say in the file that the image is map-only | 1h |

On 0.3: there are currently **zero tests** in the repo. The 4-sample `_selftest`
inside `declaration.py` is the only executable check of anything, and it is not
wired to run anywhere. A single CI job that catches "the scripts don't import"
would have caught the merge regression on the day it happened.

---

## Phase 1 — the signals that decide real deals

The stage-1 score is age, scale, concentration, absentee. Those are the four
things that are easy to compute from the roll, not the four things that predict a
termination. Three of the strongest predictors are absent, and two of them are
already sitting in files the pipeline reads.

### 1.1 Homestead / owner-occupancy — the 5% objector rule *(2 days)*

FS 718.117 needs 80% approval **and no more than 5% objecting**. The screen has
nothing that models the objection side. `absentee_pct` is standing in for it, but
absentee is a mailing-address comparison — it puts a Brickell landlord who owns
three units and a retiree in Ohio in the same bucket, while the actual legal fact
is homestead exemption.

A homesteaded owner-occupant is the one who objects, the one whose payout floor
is protected, and the one a judge listens to. The FDOR NAL carries homestead
exemption fields per parcel. `ingest_nal.py` reads 20 columns and drops them.

Add `homestead` per unit, `homestead_pct` per group, and invert it into a
`score_resistance` term. Expect this to reorder the top 100 substantially —
which is the point.

### 1.2 Milestone inspection and structural distress *(3-4 days)*

This is the single biggest omission and the reason is timing. Post-Surfside, the
building that terminates in 2026 is not the one that is merely old — it is the
one facing a milestone inspection under FS 553.899 plus a structural integrity
reserve study under 718.112(2)(g), staring at a five- or six-figure per-unit
special assessment the owners cannot fund. That building has motivated sellers.
An identically-aged building that already passed recertification does not.

`milestone_due` is currently `age_years >= 30` — a proxy for a fact that
Miami-Dade actually publishes. RER maintains 40-year recertification status and
unsafe-structure cases by folio. Ingesting it turns a guess into a fact and
splits the "30+ years old" cohort, which is most of the shortlist, into two
groups with completely different motivation profiles.

Add: `recert_status`, `recert_due_date`, `unsafe_case_open`, and a
`score_distress` term. Then let the table filter on it, because "open unsafe
structure case" is a search somebody will run every morning.

### 1.3 Sale qualification codes — check the header first *(half a day if present)*

The README states the roll "carries no qualification code, so intra-family and
other non-arm's-length transfers are not filtered out." The published FDOR NAL
layout documents `QUAL_CD1` / `VI_CD1` (and the same for the second sale).
`ingest_nal.py` never asks for them, so this is untested rather than untrue.

Verify by printing the header of the cached zip. If the columns are there, the
comps caveat that currently limits every price in every memo goes away for one
afternoon's work, and the arm's-length filter the comps engine deserves becomes
possible. While in there, also capture `SALE_MO1` (month, not just year) and the
**second** sale — a unit that traded twice in three years is a signal the current
schema cannot see at all.

### 1.4 Re-weight and document *(1 day)*

Once 1.1-1.3 land, `score_weights` in `config.json` has four terms for a
six-term problem. Re-derive the weights against whatever ground truth exists
(buildings that have actually terminated in Miami-Dade since 2018 — a small but
real labelled set) and write the reasoning into the config the way the existing
`_weights_note` does. The score should stay auditable judgment, not become a
model; the README is right about that and it should not change.

---

## Phase 2 — movement, not level

This is the differentiated part. **~1.5 weeks.**

### 2.1 Roll vintage history and assembly velocity *(3 days)*

`build_targets.py` opens with `DELETE FROM condo_group` and `DELETE FROM target`.
Every rebuild destroys the prior state, so the app can only ever answer "who owns
a lot of this building today" and never "who is *buying* it."

Add a `target_snapshot` table keyed `(group_key, roll_year)` holding the
concentration and ownership columns, written on every rebuild. Then derive:

- `conc_delta_1yr` — change in top-owner or shared-mailing share
- `new_entity_buyers` — LLCs that appear in this roll and not the last
- `assembly_flag` — concentration rising while entity share rises

A building at 45% single-owner has probably already been bought by somebody who
knows what they are doing. A building that went 6% → 19% since last year is the
one you want to be early on. The current screen ranks the first one higher.

This is worth doing *before* Phase 3, because it needs no new data source at all
— only the discipline to stop deleting history.

### 2.2 Sunbiz entity resolution *(4 days)*

Shared mailing address is a clever proxy for one buyer behind several LLCs, and
it is the weakest link in the concentration signal — it misses anyone using a
registered-agent address or separate mailboxes, and it false-positives on
buildings where a management company receives mail for many owners.

Florida's Division of Corporations publishes the full corporate registry as a
free bulk quarterly download: entity name, registered agent, officers and
directors, principal address. Joining `owner_norm` to that file builds a real
graph — LLC → officers → other LLCs — and collapses beneficial ownership
properly.

Add `owner_entity` and `entity_principal` tables, a `beneficial_owner_pct` on
`condo_group`, and surface the graph in the building drawer. "These four LLCs
holding 31% share a manager" is a sentence the current tool cannot produce and
is the reason somebody opens the app.

### 2.3 Watchlist and change alerts *(2 days)*

Once 2.1 exists, a saved filter that re-evaluates on each roll and reports what
entered, exited or moved is a small feature on top of it. Persist saved
searches, diff them across snapshots, render the delta. This is what makes the
app something opened weekly instead of quarterly.

---

## Phase 3 — the buyout actually penciling

The screen ranks buildings. It does not tell you whether a deal clears. **~2 weeks.**

### 3.1 Termination waterfall model *(5 days)*

FS 718.117 sets out who gets paid and in what order when a condominium
terminates, and it carries protections that change the arithmetic materially —
including a floor for homestead owners who occupied the unit, and the rule that a
plan cannot be approved if it leaves those owners owing money on a mortgage after
the payout. A screen that ignores the payout floor will rank buildings full of
long-held homesteads as cheap when they are the most expensive kind.

Build a `backend/prospect/economics.py` that takes a target and produces:

- buyout cost at fair market value, per unit, from the comps engine already built
- the homestead floor where it binds, and how many units it binds on
- units already controlled (no buyout needed) vs units to acquire
- a holdout premium curve — the last 10% of units is where deals die
- total basis vs the redevelopment residual from `capacity.py`
- the margin, and the unit-count breakeven

Everything it needs except the mortgage balances already exists in this repo.
Route it as `/api/economics/{group_key}` and put it in the memo as a section
above the termination path.

**Have counsel review the statutory logic before this drives a decision.** The
module should print the subsection it is applying next to every number, the same
way `capacity.py` carries a `basis` string, and it should refuse to produce a
figure it cannot cite.

### 3.2 Debt and encumbrances *(4 days, gated on retrieval)*

The gap in 3.1 is that the tax roll knows nothing about mortgages, and you cannot
underwrite a buyout without knowing what debt has to be cleared. Miami-Dade Clerk
Official Records has it. So does the declaration pipeline's retrieval problem —
see Phase 4, and do them together.

Interim: let an analyst enter aggregate debt on the shortlist by hand, the same
way stage-2 findings are entered now, and have the waterfall flag every figure it
computed without it.

### 3.3 Sensitivity *(1 day)*

One screen, three sliders: buyout premium over FMV, holdout share, construction
cost per unit. A deal that only works at a 10% premium and 0% holdouts is a deal
that does not work, and the tool should say so out loud.

---

## Phase 4 — declaration review at scale

Stage 2 is the strategic asset and it is the most manual thing here. **~1.5 weeks.**

### 4.1 OCR *(2 days)*

`cmd_extract` bails with "this is likely a SCANNED image PDF" when text
extraction returns under 200 characters. The target set is buildings recorded
between roughly 1965 and 1990. Essentially all of those declarations are scanned
images. The extractor gives up on precisely the documents it exists to read.

Wire Tesseract (or a hosted OCR) behind the existing `pdf_text`, cache the
extracted text next to the PDF so a re-run is free, and record which path
produced the text — OCR output is noisier and `find_threshold` needs to know that
before it reports high confidence.

### 4.2 Bring stage 2 into the app *(3 days)*

Retrieval is manual on purpose and that reasoning is sound. The *review* being
CLI-only is not. Today an analyst runs `--worklist`, gets an xlsx, saves PDFs to
a folder by folio prefix, and runs `--extract` from a terminal. Make it: upload
the PDF in the building drawer, see the extracted findings with their verbatim
snippets side by side with the source page, accept or correct, save. The
stage-2 form and the `POST /api/target/{key}/stage2` route already exist — this
is a document pane and an upload endpoint, not new domain logic.

### 4.3 Extractor hardening *(3 days)*

`declaration.py` is validated by four hand-written samples that it passes by
construction. Before anybody relies on `termination_threshold` at volume it needs
a real corpus — 50-100 actual declarations, hand-labelled, with reported
precision and recall per field and a documented failure mode. Regex over legal
prose fails in specific, learnable ways; the tool should know its own error rate
and print it.

Extend the extraction while in there. The three fields captured now are not the
only ones that kill a deal:

- the **amendment chain** — the operative threshold is the original as amended, and today an amendment PDF overwrites the original's findings in the same columns
- **right of first refusal** on unit transfers, which throttles quiet assembly
- **recreation leases / leasehold land**, which can make the fee unbuildable
- **age-restricted (55+)** covenants, which change both the buyer pool and the relocation politics

### 4.4 Re-test Clerk retrieval *(1 day, timeboxed)*

Worth thirty minutes with the network tab before accepting "no documented query
API" permanently. The SPA is talking to something. If a stable JSON endpoint
exists, half of Phase 3.2 and Phase 4 collapses into an overnight job. If it does
not, write down what was tried and when, and keep the manual path.

---

## Phase 5 — coverage

**~1 week.** Deliberately last: three counties of a mediocre screen is worse
than one county of a good one.

The schema is already most of the way there — `nal_condo_unit.county` exists and
the FDOR NAL and DBPR registry are both statewide files with identical structure.
Broward and Palm Beach are the same ingest with a different URL and a
county-scoped `group_key`. The work is not the ingest; it is that
`capacity.py` is Miami-Dade zoning only and `site_coords` hardcodes EPSG:2236
plus a Miami-Dade bounding box, so both need to become per-county configuration.

Sequence: Broward → Palm Beach → the rest of the coastal counties. The metro
screener is already national, so this is the module that is out of step, not the
other way round.

---

## Also worth doing

Small things that are not part of the arc above.

- **Deal pipeline state.** `stage2_verified` is the only workflow field on a
  target. A status enum (screened / researching / contacted / LOI / dead) with a
  timestamp and a note log turns the table into something a team works from. ~2 days.
- **Portability.** `C:\Apps\_shared` is the fallback path in three separate
  `_shared_root()` copies, `launch.py` and `install.py` are Windows-only, and the
  README's commands are all `venv\Scripts\`. If this only ever runs on one
  Windows box that is fine — but the Dockerfile suggests otherwise, and the
  three duplicated `_shared_root()` functions should be one import regardless. ~1 day.
- **Roll provenance in the UI.** `ingest_log` is populated and exposed through
  `/api/condo/stats`, but a user reading a score cannot see that it came from a
  *preliminary* 2026 roll. Put the vintage in the header of the Records mode.  ~2h.
- **Memo economics section.** Once Phase 3 lands, the memo is the natural place
  for it, and the memo's existing habit of printing what it could not confirm at
  the top is exactly right for a financial model. ~1 day.

---

## Sequencing

```
Phase 0  ██                                    1d      blocking
Phase 1  ████████████                          7d      biggest score improvement
Phase 2  ███████████████                       9d      the differentiated part
Phase 3  ████████████████████                 10d      screen -> underwriting tool
Phase 4  ███████████████                       9d      unlocks the strategic asset
Phase 5  ████████                              5d      breadth, last
```

If only one phase gets built: **Phase 2**. If only one week: **Phase 0 + 1.1 +
1.2**, because homestead and recertification status reorder the shortlist more
than anything else on this list and both are afternoon-scale ingests.

---

## Open questions

These change the priorities enough to be worth answering first.

1. **Is this a personal tool or a product?** Everything in Phase 5 and most of
   Portability is only worth doing for the second. The install story — desktop
   shortcut, start at logon, self-heal task — reads personal; the Dockerfile
   reads product.
2. **Is there a labelled set of actual terminations?** Miami-Dade terminations
   since 2018 are a matter of record. Twenty of them would turn the score from
   defensible judgment into something measurable, and would settle Phase 1.4.
3. **How much does declaration retrieval cost in practice?** If a title company
   or a Clerk bulk-order can produce declarations for the whole shortlist for a
   few hundred dollars, Phase 4.1 and 4.4 get deprioritised and the money is the
   better tool.
4. **Who else sees this?** Phase 5.1's pipeline state and Phase 2.3's alerts are
   multi-user features. Single-user, they are much less valuable.

---

*Informational only. Nothing in this document, or in the tool it describes, is
legal, valuation or engineering advice. The statutory citations here are
starting points for counsel, not conclusions.*
