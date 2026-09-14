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

/* ── waterfall ────────────────────────────────────────────────────────── */

test('the waterfall accumulates and the total restarts at zero', () => {
  const svg = C.waterfall([
    { label: 'Units to acquire', value: 18e6, kind: 'add' },
    { label: 'Holdout premium', value: 2e6, kind: 'risk' },
    { label: 'Total', value: 20e6, kind: 'total' },
  ], { width: 520, labelWidth: 150 });
  const paths = [...svg.matchAll(/<path class="chart-step (\w+)" d="M([\d.]+) /g)]
    .map((m) => ({ kind: m[1], x: parseFloat(m[2]) }));
  assert.equal(paths[0].kind, 'add');
  assert.ok(paths[1].x > paths[0].x, 'the premium must start where the cost ended');
  assert.equal(paths[2].x, paths[0].x, 'the total bar restarts at the baseline');
});

test('the holdout premium is the warning token, not a fourth series hue', () => {
  const svg = C.waterfall([{ label: 'Premium', value: 1, kind: 'risk' }]);
  assert.ok(svg.includes('chart-step risk'));
  assert.equal(/series-[abc]/.test(svg), false);
});

test('a waterfall with no steps says so', () => {
  assert.ok(C.waterfall([]).includes('Nothing to total'));
});

/* ── diverging bars ───────────────────────────────────────────────────── */

const SERIES = [
  { key: 'nat', label: 'Natural', cls: 'series-a' },
  { key: 'dom', label: 'Domestic', cls: 'series-b' },
  { key: 'intl', label: 'International', cls: 'series-c' },
];

test('a negative component is drawn below the zero line, not folded into a stack', () => {
  // Columbus: +83k overall on NEGATIVE domestic migration. A stacked area cannot
  // show that without lying about the total, which is why this is not one.
  const rows = [{ label: '2024', values: { nat: 5000, dom: -12000, intl: 30000 } }];
  const svg = C.divergingBars(rows, SERIES, { height: 190 });
  const y = (cls) => parseFloat(new RegExp(
    'class="chart-seg ' + cls + '" d="M[\\d.]+ ([\\d.]+)').exec(svg)[1]);
  const zero = parseFloat(/class="chart-grid zero" x1="[\d.]+" y1="([\d.]+)"/.exec(svg)[1]);
  assert.ok(y('series-b') >= zero - 1, 'domestic is negative and must sit below zero');
  assert.ok(y('series-c') < zero, 'international is positive and must sit above zero');
});

test('the zero line is drawn and is the only emphasised gridline', () => {
  const svg = C.divergingBars([{ label: 'a', values: { nat: 1, dom: -1, intl: 2 } }], SERIES);
  assert.equal((svg.match(/chart-grid zero/g) || []).length, 1);
});

test('two or more series always carry a legend', () => {
  const out = C.divergingBars([{ label: 'a', values: { nat: 1, dom: 2, intl: 3 } }], SERIES);
  assert.ok(out.includes('chart-legend'));
  for (const s of SERIES) assert.ok(out.includes(s.label));
});

test('a zero value draws no segment rather than a zero-height sliver', () => {
  const svg = C.divergingBars([{ label: 'a', values: { nat: 0, dom: 5, intl: 0 } }], SERIES);
  assert.equal(/chart-seg series-a/.test(svg), false);
  assert.ok(svg.includes('chart-seg series-b'));
});

/* ── emphasis scatter ─────────────────────────────────────────────────── */

test('the subject is drawn after the context so it is never buried', () => {
  const pts = [{ x: 1, y: 1, label: 'a' }, { x: 2, y: 2, label: 'Miami', highlight: true },
               { x: 3, y: 3, label: 'c' }];
  const svg = C.scatter(pts);
  const order = [...svg.matchAll(/class="chart-dot( hot)?"/g)].map((m) => !!m[1]);
  assert.equal(order.lastIndexOf(false) < order.indexOf(true), true, 'context must come first');
});

test('the subject is directly labelled; the other points are not', () => {
  const pts = [{ x: 1, y: 1, label: 'Ordinary' }, { x: 2, y: 2, label: 'Miami', highlight: true }];
  const svg = C.scatter(pts);
  const labels = [...svg.matchAll(/<text class="chart-value hot"[^>]*>([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(labels, ['Miami']);
});

test('469 context points stay one colour rather than becoming a palette', () => {
  const pts = Array.from({ length: 469 }, (_, i) => ({ x: i, y: i % 7, label: 'm' + i }));
  const svg = C.scatter(pts);
  assert.equal(/series-[abc]/.test(svg), false, 'context must not be coloured by identity');
  assert.equal((svg.match(/class="chart-dot"/g) || []).length, 469);
});

test('both axes are titled, because neither is self-evident', () => {
  const svg = C.scatter([{ x: 1, y: 2, label: 'a' }],
    { xLabel: 'Permits per 1,000', yLabel: 'Net migration per 1,000' });
  assert.ok(svg.includes('Permits per 1,000'));
  assert.ok(svg.includes('Net migration per 1,000'));
});

/* ── anatomy, across every form ───────────────────────────────────────── */

test('marks are separated by a surface gap, not by a stroke around them', () => {
  const svg = C.divergingBars([{ label: 'a', values: { nat: 5, dom: 5, intl: 5 } }], SERIES)
    + C.waterfall([{ label: 'x', value: 5, kind: 'add' }, { label: 'y', value: 5, kind: 'risk' }]);
  assert.equal(/class="chart-seg[^"]*"[^>]*stroke=/.test(svg), false);
  assert.equal(/class="chart-step[^"]*"[^>]*stroke=/.test(svg), false);
});

test('no new form smuggles in a literal colour', () => {
  const svg = C.waterfall([{ label: 'a', value: 1, kind: 'add' }])
    + C.divergingBars([{ label: 'a', values: { nat: 1, dom: -1, intl: 1 } }], SERIES)
    + C.scatter([{ x: 1, y: 1, label: 'a', highlight: true }]);
  assert.equal(/#[0-9a-f]{3,6}\b/i.test(svg), false);
});

test('every new form is labelled for a screen reader', () => {
  for (const svg of [C.waterfall([{ label: 'a', value: 1, kind: 'add' }]),
                     C.divergingBars([{ label: 'a', values: { nat: 1 } }], SERIES),
                     C.scatter([{ x: 1, y: 1 }]),
                     C.waterfall([]), C.scatter([])]) {
    assert.ok(svg.includes('role="img"'));
    assert.ok(/<desc>.*<\/desc>/s.test(svg));
  }
});

test('labels from the data cannot inject markup in any form', () => {
  const bad = '<img src=x onerror=1>';
  const svg = C.waterfall([{ label: bad, value: 1, kind: 'add' }])
    + C.divergingBars([{ label: bad, values: { nat: 1 } }], SERIES)
    + C.scatter([{ x: 1, y: 1, label: bad, highlight: true }]);
  assert.equal(svg.includes('<img'), false);
});

/* ── butterfly (corridors) ────────────────────────────────────────────── */

test('arrivals and departures share one scale, so the balance is visible', () => {
  const svg = C.butterfly([{ label: 'Broward', inValue: 1000, outValue: 2000 }], { width: 520 });
  const paths = [...svg.matchAll(/class="chart-seg (in|out)" d="M([\d.]+) /g)]
    .map((m) => ({ side: m[1], x: parseFloat(m[2]) }));
  const inBar = paths.find((p) => p.side === 'in');
  const outBar = paths.find((p) => p.side === 'out');
  assert.ok(outBar.x < inBar.x, 'departures sit left of the centre, arrivals right');
});

test('a bar twice the size is twice the length', () => {
  const wide = (v) => {
    const svg = C.butterfly([{ label: 'x', inValue: v, outValue: 0 },
                             { label: 'y', inValue: 100, outValue: 0 }], { width: 520 });
    const d = /class="chart-seg in" d="M([\d.]+) [\d.]+ H([\d.]+)/.exec(svg);
    return parseFloat(d[2]) - parseFloat(d[1]);
  };
  const a = wide(50), b = wide(100);
  assert.ok(Math.abs(b - 2 * a - 4) < 6, `${a} vs ${b}`);
});

test('a corridor that moves people both ways is not the same as an unused one', () => {
  // Netting to zero and never being used look identical on a net-only chart.
  const busy = C.butterfly([{ label: 'a', inValue: 900, outValue: 900 }]);
  const idle = C.butterfly([{ label: 'a', inValue: 0, outValue: 0 }]);
  assert.ok(busy.includes('chart-seg in') && busy.includes('chart-seg out'));
  assert.equal(/chart-seg (in|out)/.test(idle), false);
});

test('the centre line is drawn as the zero reference', () => {
  const svg = C.butterfly([{ label: 'a', inValue: 5, outValue: 5 }]);
  assert.equal((svg.match(/chart-grid zero/g) || []).length, 1);
});

test('corridors carry a legend and escape their labels', () => {
  const svg = C.butterfly([{ label: '<b>x</b>', inValue: 1, outValue: 1, note: '<i>y</i>' }]);
  assert.ok(svg.includes('chart-legend'));
  assert.ok(svg.includes('Arrivals') && svg.includes('Departures'));
  assert.equal(svg.includes('<b>'), false);
  assert.equal(svg.includes('<i>'), false);
});

test('an empty corridor set says so', () => {
  assert.ok(C.butterfly([]).includes('No corridors'));
});
