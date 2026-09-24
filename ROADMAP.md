# Roadmap

Phases 0-8 of the original plan are done: the pipeline runs, homestead and
milestone signals, the buyout estimate, the map (zoning colour, intensity, 3D),
charts and memos, migration flows, roll-over-roll movement, declaration OCR and
review, and coverage. The full plan, with the reasoning behind each phase, is in
git history:

    git show 4414acf:ROADMAP.md

What is left is below. None of it blocks using the app.

---

## Still open

- **Live zoning for Miami-Dade and the City of Miami.** `METRO_ZONING` wires
  Orlando, Tampa, Jacksonville, St. Pete, Clearwater, Sarasota, Tallahassee and
  West Palm Beach, but not the home market -- tri-county zoning comes only from
  the pre-baked layer `fetch_layers.py` downloads. Wiring the live services needs
  their URLs verified against the real endpoints, which the build environment
  could not reach.
- **Labelled terminations for re-weighting (1.4).** DBPR association status is
  already in `dbpr_association` and nothing reads it. Run this against your
  database and the score weights become measurable instead of a judgment call:

  ```sql
  SELECT primary_status, secondary_status, COUNT(*) n
  FROM dbpr_association GROUP BY 1, 2 ORDER BY n DESC;
  ```
- **What the buyout estimate cannot see.** Mortgages and liens (not on the tax
  roll) and the statutory homestead payout floor. Every estimate prints both
  limits.
- **Beneficial owners through Sunbiz.** Owner clustering uses shared mailing
  addresses; resolving LLCs to their officers across buildings was deferred.
- **Richer server-side charts in the memo and DD report.** Optional -- the
  broken sparkline was fixed; more charts were never needed.

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
