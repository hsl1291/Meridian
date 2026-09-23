/* Regression tests from the line-by-line review of frontend/app.js.

   Pure helpers run for real in a vm sandbox; DOM-bound functions are checked
   structurally (guard present, and placed after the await it protects). */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');

function extractFn(src, name) {
  const m = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`).exec(src);
  assert.ok(m, `function ${name} not found`);
  let depth = 1;
  let i = m.index + m[0].length;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(m.index, i);
}
const body = (name) => { const f = extractFn(APP, name); return f.slice(f.indexOf('{') + 1, -1); };
function run(names, ctx = {}, extra = '') {
  const c = vm.createContext(ctx);
  vm.runInContext(names.map((n) => extractFn(APP, n)).join('\n') + '\n' + extra, c);
  return c;
}
const constLine = (name) => {
  const m = new RegExp(`const ${name} = [^;]+;`).exec(APP);
  assert.ok(m, `const ${name} not found`);
  return m[0];
};

// ── loading: the map fetched data nobody asked for ─────────────────────────

test('overlay membership is skipped outside Miami-Dade/Broward', () => {
  const c = run(['inOverlayRegion'], {}, constLine('OVERLAY_BBOX'));
  assert.equal(c.inOverlayRegion(-80.19, 25.77), true, 'Miami');
  assert.equal(c.inOverlayRegion(-80.14, 26.12), true, 'Fort Lauderdale');
  assert.equal(c.inOverlayRegion(-104.99, 39.74), false, 'Denver');
  assert.equal(c.inOverlayRegion(-82.46, 27.95), false, 'Tampa');
  const b = body('queryOverlayMembership');
  assert.ok(b.indexOf('inOverlayRegion') < b.indexOf('loadOverlay'),
    'the region check must come BEFORE the nine downloads, not after');
});

test('hidden static layers are not downloaded at startup', () => {
  // The only /data URLs handed straight to addSource are the ones visible
  // on first paint; everything else goes through addLazySource.
  const eager = [...APP.matchAll(/map\.addSource\('([\w-]+)', \{ type: 'geojson', data: '\/data\//g)].map((m) => m[1]);
  assert.deepEqual(eager.sort(), ['mdc_metromover', 'mdc_metrorail', 'mdc_zoning']);
  assert.ok((APP.match(/addLazySource\('/g) || []).length >= 16);
});

test('a lazy source loads exactly once, the first time a layer using it is shown', () => {
  const calls = [];
  const sources = {};
  const map = {
    addSource: (id) => { sources[id] = { setData: (d) => calls.push([id, d]) }; },
    getSource: (id) => sources[id],
    getLayer: (id) => ({ 'flood-fill': { source: 'flood' }, 'flood-line': { source: 'flood' } }[id]),
    setLayoutProperty: () => {},
  };
  const c = run(['addLazySource', 'ensureLazySource', 'setVis'], { map }, 'const LAZY_SOURCES = {};');
  c.addLazySource('flood', '/data/flood.geojson');
  assert.ok(sources.flood, 'the source exists (empty) so layers can attach to it');
  c.setVis(['flood-fill', 'flood-line'], false);
  assert.equal(calls.length, 0, 'hiding must not load it');
  c.setVis(['flood-fill', 'flood-line'], true);
  c.setVis(['flood-fill'], true);
  assert.deepEqual(calls, [['flood', '/data/flood.geojson']], 'loaded once, on first show');
});

// ── latest-wins guards on fetches that overlap ─────────────────────────────

const GUARDS = {
  searchParty: /mine !== partySeq/,
  loadEntity: /stale\(\)/,
  doSearch: /mine !== searchSeq/,
  refreshBrokerTab: /mine !== brokerSeq/,
  runLiveLocal: /seq !== lookupSeq/,
};
for (const [fn, guard] of Object.entries(GUARDS)) {
  test(`${fn}: a slower, older response cannot overwrite a newer one`, () => {
    const b = body(fn);
    const awaitAt = b.search(/await (fetchJSON|Promise\.all)/);
    const guardAt = b.search(guard);
    assert.ok(awaitAt >= 0 && guardAt > awaitAt, `${fn} must check ${guard} after its fetch`);
  });
}

test('the typeahead also cancels the superseded request', () => {
  assert.match(body('doSearch'), /searchAbort\?\.abort\(\)/);
  assert.match(body('doSearch'), /signal: searchAbort\.signal/);
});

test('switching metro trends off during its first load keeps it off', () => {
  const b = body('wireTrends');
  const after = b.slice(b.indexOf('await loadTrends()'));
  assert.ok(after.indexOf('if (!cb.checked) return') >= 0
    && after.indexOf('if (!cb.checked) return') < after.indexOf('setVis(TREND_LAYERS, true)'));
});

// ── a failed check must not read as a clean one ────────────────────────────

test('a failed flood lookup is "unknown", never "outside the flood zone"', () => {
  const d = body('doParcelLookup');
  assert.match(d, /catch \(e\) \{ overlays\.floodUnknown = true; \}/);
  assert.match(d, /fema\.message\)\s*\{\s*overlays\.floodUnknown = true/);
  const r = body('renderParcel');
  assert.match(r, /overlays\._error \|\| overlays\.floodUnknown/);
  assert.match(body('openDiligenceReport'), /Flood zone unknown/);
});

test('a condo roster outage is shown, not hidden as "not a condo"', () => {
  const b = body('loadCondoUnits');
  const c = b.slice(b.indexOf('catch'));
  assert.match(c, /panel\.hidden = false/);
  assert.match(c, /failMsg\('Unit roster unavailable', err\)/);
});

test('a ZIP with no rent is left uncoloured, not painted cheapest', () => {
  let fc = null;
  const map = { getSource: () => ({ setData: (d) => { fc = d; } }) };
  const document = { getElementById: () => null };
  const c = run(['recolorRents'], { map, document, Math },
    "const RENT_RAMP = ['a','b','c','d','e','f']; var rentsFC;");
  vm.runInContext(`rentsFC = { type: 'FeatureCollection', features: [
    { properties: { rent: 1500 } }, { properties: { rent: 2500 } },
    { properties: { rent: 3500 } }, { properties: { rent: null } } ] };
    recolorRents();`, c);
  const colours = fc.features.map((f) => f.properties._c);
  assert.equal(colours[3], null);
  assert.ok(colours.slice(0, 3).every(Boolean));
});

// ── links built from upstream data ─────────────────────────────────────────

test('recorderUrl cannot be broken out of by a hostile state value', () => {
  const c = run(['recorderUrl']);
  assert.equal(c.recorderUrl('FL', 'Miami-Dade County'),
    'https://publicrecords.netronline.com/state/FL/county/miami-dade');
  const u = c.recorderUrl('FL"><img src=x onerror=alert(1)>', 'Dade');
  assert.ok(!/["<>]/.test(u), u);
});

test('no href is built with plain HTML-escaping (it lets javascript: through)', () => {
  const bad = APP.match(/href="\$\{(esc|E)\(/g) || [];
  assert.deepEqual(bad, [], 'use safeUrl() for any href built from data');
});

test('the report is branded Meridian, not a retired app name', () => {
  assert.ok(!/sitefolio/i.test(APP), 'Sitefolio is one of the apps Meridian replaced');
});

test('basemap buttons only bind the basemap segment', () => {
  assert.match(body('wireBasemapButtons'), /#basemap-seg \.seg-btn/);
});
