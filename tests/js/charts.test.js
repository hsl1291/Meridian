/* The chart module. Every function returns an SVG string, so the geometry is
   testable with no DOM and no browser. */
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../../frontend/charts.js');

const attr = (svg, name) => (new RegExp(name + '="([^"]*)"').exec(svg) || [])[1];
const nums = (svg, re) => [...svg.matchAll(re)].map((m) => parseFloat(m[1]));

/* ── the bug in the old sparkline ─────────────────────────────────────── */

test('geometry is uniform, so a slope on screen is a slope in the data', () => {
  // _sparkline used preserveAspectRatio="none", which stretches the axes
  // independently and lies about every gradient it draws.
  const svg = C.stripPlot([{ value: 1 }, { value: 2 }]);
  assert.notEqual(attr(svg, 'preserveAspectRatio'), 'none');
});

test('every axis label names a value the chart reaches', () => {
  const { min, max, ticks } = C.niceTicks(441000, 4900000, 5);
  assert.ok(min <= 441000 && max >= 4900000, 'domain must cover the data');
  assert.equal(ticks[0], min);
  assert.equal(ticks[ticks.length - 1], max);
});

test('charts carry no colours of their own', () => {
  // Colour comes from the CSS custom properties the app already defines, or the
  // chart cannot follow the theme. The old sparkline hardcoded #1f3b57.
  const svg = C.stripPlot([{ value: 1 }, { value: 2, bulk: true }])
    + C.stackedBar([{ label: 'a', value: 1 }, { label: 'b', value: 2 }]);
  assert.equal(/#[0-9a-f]{3,6}\b/i.test(svg), false, 'literal colour in chart markup');
  assert.equal(/\b(?:fill|stroke)="(?!none)/.test(svg), false, 'presentational paint attribute');
});

/* ── ticks ────────────────────────────────────────────────────────────── */

test('ticks land on round numbers', () => {
  for (const [lo, hi] of [[0, 97], [3, 11], [0, 4900000], [-40, 40]]) {
    const { ticks } = C.niceTicks(lo, hi, 5);
    const step = ticks[1] - ticks[0];
    const mant = step / Math.pow(10, Math.floor(Math.log10(step)));
    assert.ok([1, 2, 2.5, 5, 10].some((m) => Math.abs(m - mant) < 1e-9),
      `step ${step} is not a round number`);
  }
});

test('ticks are evenly spaced without floating-point drift', () => {
  const { ticks } = C.niceTicks(0, 1, 5);
  const step = ticks[1] - ticks[0];
  for (let i = 1; i < ticks.length; i++) {
    assert.ok(Math.abs((ticks[i] - ticks[i - 1]) - step) < 1e-9, ticks.join(','));
  }
});

test('a flat series still gets a usable axis', () => {
  const { min, max, ticks } = C.niceTicks(5, 5, 5);
  assert.ok(max > min && ticks.length > 1);
});

test('non-finite input does not produce NaN geometry', () => {
  const { ticks } = C.niceTicks(NaN, Infinity, 5);
  assert.ok(ticks.every(Number.isFinite));
  assert.ok(!C.stripPlot([{ value: NaN }, { value: 3 }]).includes('NaN'));
});

/* ── strip plot ───────────────────────────────────────────────────────── */

test('bulk deeds are drawn apart from single-unit sales', () => {
  const svg = C.stripPlot([{ value: 441000 }, { value: 4900000, bulk: true }]);
  assert.ok(svg.includes('chart-mark single'));
  assert.ok(svg.includes('chart-mark bulk'));
});

test('the median line follows single-unit sales only', () => {
  // The whole point: a bulk median is what an assembler already paid, not what
  // the remaining owners will take.
  const pts = [{ value: 440000 }, { value: 442000 },
               { value: 4900000, bulk: true }, { value: 4900000, bulk: true }];
  const svg = C.stripPlot(pts);
  assert.ok(svg.includes('median $441k'), svg.match(/median [^<]*/));
});

test('single-unit marks are painted over bulk ones, never under', () => {
  const svg = C.stripPlot([{ value: 1, bulk: true }, { value: 2 }, { value: 3, bulk: true }]);
  const order = [...svg.matchAll(/chart-mark (single|bulk)/g)].map((m) => m[1]);
  assert.equal(order.lastIndexOf('bulk') < order.indexOf('single'), true, order.join(','));
});

test('an empty series says so instead of drawing an empty box', () => {
  const svg = C.stripPlot([]);
  assert.ok(svg.includes('No recorded price'));
  assert.ok(svg.includes('<desc>'));
});

test('every chart is labelled for a screen reader', () => {
  for (const svg of [C.stripPlot([{ value: 1 }]), C.stripPlot([]),
                     C.stackedBar([{ label: 'a', value: 1 }]), C.stackedBar([])]) {
    assert.ok(svg.includes('role="img"'), svg.slice(0, 60));
    assert.ok(/<title>.+<\/title>/.test(svg));
    assert.ok(/<desc>.*<\/desc>/.test(svg) || svg.includes('aria-label'));
  }
});

test('labels coming from the roll cannot inject markup', () => {
  const svg = C.stripPlot([{ value: 1, label: '<script>x</script>&"' }]);
  assert.equal(svg.includes('<script>'), false);
  assert.ok(svg.includes('&lt;script&gt;'));
});

/* ── stacked bar ──────────────────────────────────────────────────────── */

test('segments fill the width in proportion', () => {
  const svg = C.stackedBar([{ label: 'top', value: 13 }, { label: 'rest', value: 47 }],
                           { width: 600 });
  const widths = nums(svg, /width="([\d.]+)"/g);
  const total = widths.reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(total - (600 - widths.length)) < 1.5, widths.join(','));
});

test('a zero total does not divide by zero', () => {
  const svg = C.stackedBar([{ label: 'a', value: 0 }]);
  assert.equal(svg.includes('NaN'), false);
  assert.ok(svg.includes('Nothing to distribute'));
});

/* ── money ────────────────────────────────────────────────────────────── */

test('money is readable at every scale a condo touches', () => {
  assert.equal(C.money(441000), '$441k');
  assert.equal(C.money(4900000), '$4.9M');
  assert.equal(C.money(19100000), '$19M');
  assert.equal(C.money(950), '$950');
});
