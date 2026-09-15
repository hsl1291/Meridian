/* Map overlay fetches (permits, zoning, flood, population, rents, transit) are
   triggered on every 'moveend', debounced 250-400ms. Panning repeatedly fires a
   new fetch before the previous one resolves.

   Three of the six refresh functions (permit-heat, metro-zoning, national-flood)
   had no staleness guard at all: whichever response arrived LAST won, even if it
   was for an older, now-scrolled-away viewport -- a fast pan could show zoning or
   flood data for the wrong location. The other three (population, rents, transit)
   ignored a stale response correctly but never cancelled the underlying request,
   so a superseded fetch stayed in flight for up to its full 20-30s timeout,
   occupying one of the browser's ~6 concurrent connections per host and delaying
   the CURRENT viewport's request behind stale ones nobody would ever look at.

   The fix: every overlay now takes an AbortController, aborts the previous
   in-flight request before starting a new one, and still guards on a sequence
   number in case an abort races a response that was already in flight. This
   suite checks the source for both halves of that fix, function by function --
   not just that the words "AbortController" and "seq" appear somewhere in the
   file, which passed even when only the parcel-fabric layer (the original
   pattern this fix generalized from) had them. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const APP = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');

// Pulls one top-level `function name(...) { ... }` or `async function name(...) { ... }`
// body out of a flat (non-module) script by counting braces -- a plain regex can't
// find the matching close brace when the body itself contains nested `{ }`.
function extractFn(src, name) {
  const m = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`).exec(src);
  assert.ok(m, `function ${name} not found in app.js`);
  let depth = 1;
  let i = m.index + m[0].length;
  const start = i;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  assert.ok(depth === 0, `unbalanced braces scanning ${name}`);
  return src.slice(start, i - 1);
}

// name -> [trigger fn (owns abort()+seq++), fetch fn(s) (check seq, receive the
// signal, special-case AbortError), abort variable, seq variable]
const OVERLAYS = {
  'permit heat': ['refreshPermitHeat', ['refreshPermitHeat'], 'permitHeatAbort', 'permitHeatSeq'],
  'metro zoning': ['refreshMetroZoning', ['refreshMetroZoning'], 'metroZoningAbort', 'metroZoningSeq'],
  'national flood': ['refreshNationalFlood', ['refreshNationalFlood'], 'floodAbort', 'floodSeq'],
  'population by ZIP': ['refreshPopGrowth', ['refreshPopGrowth'], 'popAbort', 'popSeq'],
  rents: ['refreshRents', ['refreshRents'], 'rentsAbort', 'rentsSeq'],
  // Transit's trigger function fires both fetches, so it (not the fetch
  // functions themselves) owns the single shared AbortController.
  transit: ['refreshTransit', ['refreshTransitRoutes', 'refreshTransitStops'], 'transitAbort', 'transitSeq'],
};

for (const [label, [triggerFn, fetchFns, abortVar, seqVar]] of Object.entries(OVERLAYS)) {
  test(`${label}: a superseded fetch is aborted, not just outrun`, () => {
    const triggerBody = extractFn(APP, triggerFn);
    assert.ok(
      new RegExp(`${abortVar}\\?\\.abort\\(\\)`).test(triggerBody),
      `${triggerFn} never calls ${abortVar}?.abort() to cancel a superseded request`
    );
    assert.ok(
      new RegExp(`${abortVar}\\s*=\\s*new AbortController\\(\\)`).test(triggerBody),
      `${triggerFn} never creates a fresh ${abortVar} for the new request`
    );
    for (const fnName of fetchFns) {
      const body = extractFn(APP, fnName);
      assert.ok(
        /signal/.test(body),
        `${fnName} never threads the abort signal into fetchJSON -- ${abortVar}.abort() would be a no-op`
      );
    }
  });

  test(`${label}: an AbortError from the cancelled fetch is swallowed silently`, () => {
    for (const fnName of fetchFns) {
      const body = extractFn(APP, fnName);
      assert.ok(
        /AbortError/.test(body),
        `${fnName} does not special-case AbortError -- a cancelled fetch would show an error note`
      );
    }
  });

  test(`${label}: a stale response cannot overwrite a newer one`, () => {
    for (const fnName of fetchFns) {
      const body = extractFn(APP, fnName);
      assert.ok(
        new RegExp(`seq\\s*!==\\s*${seqVar}`).test(body),
        `${fnName} has no "seq !== ${seqVar}" guard -- an out-of-order response would repaint the map with stale data`
      );
    }
  });
}

test('every overlay abort/seq pair is declared once at module scope', () => {
  // A duplicate declaration would be a copy-paste error across overlays (each
  // must own its own controller -- sharing one would cross-cancel unrelated
  // layers, e.g. panning with rents on aborting an in-flight zoning fetch).
  for (const [, [, , abortVar, seqVar]] of Object.entries(OVERLAYS)) {
    const abortDecls = (APP.match(new RegExp(`let ${abortVar}\\b`, 'g')) || []).length;
    const seqDecls = (APP.match(new RegExp(`let ${seqVar}\\b`, 'g')) || []).length;
    assert.equal(abortDecls, 1, `${abortVar} declared ${abortDecls} times, expected 1`);
    assert.equal(seqDecls, 1, `${seqVar} declared ${seqDecls} times, expected 1`);
  }
});
