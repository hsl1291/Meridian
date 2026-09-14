/* Zoning intensity and massing. Pure functions, run under `node --test`:
   no browser, no DOM, no map object. */
const test = require('node:test');
const assert = require('node:assert/strict');
const Z = require('../../frontend/zoning3d.js');

const PALETTE = {
  residential: '#F6E58D', commercial: '#E8788A', industrial: '#B79EE8',
  office: '#7FB3D5', mixed: '#F0A868', downtown: '#C85C8E', open: '#A4D88C',
  agricultural: '#D9C9A3', special: '#AEB6BF', other: '#CFD4DA',
};

const lum = (hex) => { const c = Z.parseHex(hex); return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; };

test('a taller district paints darker within the same family', () => {
  const t6_8 = Z.shade(PALETTE.downtown, 8);
  const t6_80 = Z.shade(PALETTE.downtown, 80);
  assert.ok(lum(t6_80) < lum(t6_8), 'T6-80 must read darker than T6-8');
  assert.notEqual(t6_8, t6_80, 'the whole point: they used to be identical');
});

test('the ramp is monotonic across the Miami 21 tiers', () => {
  const tiers = [2, 3, 5, 8, 12, 24, 36, 48, 60, 80];
  const l = tiers.map((s) => lum(Z.shade(PALETTE.downtown, s)));
  for (let i = 1; i < l.length; i++) {
    assert.ok(l[i] <= l[i - 1], `tier ${tiers[i]} is lighter than ${tiers[i - 1]}`);
  }
});

test('an unknown height is not darkened at all', () => {
  // "We could not tell" must not render as the lightest tier of a real ramp.
  assert.equal(Z.shade(PALETTE.downtown, null), PALETTE.downtown);
  assert.equal(Z.shade(PALETTE.downtown, undefined), PALETTE.downtown);
  assert.equal(Z.shade(PALETTE.downtown, NaN), PALETTE.downtown);
});

test('shading never leaves the family it started in', () => {
  for (const [, hex] of Object.entries(PALETTE)) {
    const c0 = Z.parseHex(hex);
    for (const s of [2, 24, 80]) {
      const c = Z.parseHex(Z.shade(hex, s));
      // Darkening scales all three channels equally, so the hue order holds.
      const order = (x) => [x.r >= x.g, x.g >= x.b, x.r >= x.b].join();
      assert.equal(order(c), order(c0), `${hex} at ${s} storeys drifted hue`);
    }
  }
});

test('a bad colour is returned untouched rather than becoming black', () => {
  assert.equal(Z.shade('not-a-colour', 40), 'not-a-colour');
  assert.equal(Z.parseHex('#fff').r, 255);
});

test('extrusion height is ten feet a storey, in metres', () => {
  assert.ok(Math.abs(Z.extrusionMetres(10) - 30.48) < 0.01);
  assert.ok(Math.abs(Z.extrusionMetres(80) - 243.84) < 0.01);
});

test('no declared height means no extrusion, not a one-storey slab', () => {
  for (const v of [null, undefined, NaN, 0, -3]) assert.equal(Z.extrusionMetres(v), 0);
});

test('the fill expression covers every family in the palette', () => {
  const expr = Z.fillColorExpression(PALETTE);
  assert.equal(expr[0], 'match');
  const named = expr.filter((x) => typeof x === 'string' && x in PALETTE);
  for (const cat of Object.keys(PALETTE)) {
    if (cat === 'other') continue;
    assert.ok(named.includes(cat), `${cat} missing from the paint expression`);
  }
  assert.equal(expr[expr.length - 1], PALETTE.other, 'fallback must be the other colour');
});

test('features with no storey count keep the flat family colour', () => {
  const expr = Z.fillColorExpression(PALETTE);
  const i = expr.indexOf('downtown');
  const interp = expr[i + 1];
  assert.equal(interp[0], 'interpolate');
  // coalesce(max_stories, 0) -> the 0 stop is the undarkened colour.
  const zeroStop = interp.indexOf(0, 3);
  assert.equal(interp[zeroStop + 1], PALETTE.downtown);
});

test('the height expression reads max_stories and tolerates its absence', () => {
  const e = Z.extrusionHeightExpression();
  assert.equal(e[0], '*');
  assert.deepEqual(e[1], ['coalesce', ['get', 'max_stories'], 0]);
});

test('pitch and bearing round-trip through the hash', () => {
  const p = new URLSearchParams('z=14&lat=25.79&lon=-80.13&pitch=45&bearing=32.5');
  assert.deepEqual(Z.parseCamera(p), { pitch: 45, bearing: 32.5 });
});

test('an absent camera returns null so the caller keeps its default', () => {
  // Returning 0 here would snap a tilted view flat on every hash read.
  assert.deepEqual(Z.parseCamera(new URLSearchParams('z=14')), { pitch: null, bearing: null });
  assert.deepEqual(Z.parseCamera(new URLSearchParams('pitch=abc')), { pitch: null, bearing: null });
});

test('camera values are clamped to what MapLibre accepts', () => {
  assert.equal(Z.parseCamera(new URLSearchParams('pitch=120')).pitch, 85);
  assert.equal(Z.parseCamera(new URLSearchParams('pitch=-30')).pitch, 0);
  assert.equal(Z.parseCamera(new URLSearchParams('bearing=999')).bearing, 360);
});
