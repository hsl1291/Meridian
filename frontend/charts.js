/* ============================================================
   Charts.

   The application had exactly one: _sparkline() in market_dd.py, a bare
   polyline with a hardcoded stroke, no axis, no labels, and
   preserveAspectRatio="none" -- which stretches non-uniformly, so the slopes it
   drew were not the slopes in the data.

   Hand-rolled SVG rather than a library. There is no build step here, every
   script is a plain tag, and line/bar/strip geometry is a few hundred lines; a
   charting library would cost more in theming fights than it saves.

   Two rules the old sparkline broke, and neither is negotiable:

     * colour comes from the CSS custom properties the app already defines, so
       charts follow the theme instead of carrying their own palette
     * every axis label names a value the chart actually reaches, and the
       geometry is uniform, so a slope on screen is a slope in the data

   Every function returns an SVG STRING. That keeps rendering a pure function of
   its inputs -- testable under `node --test` with no DOM, no browser -- and
   matches how the rest of this frontend builds markup.
   ============================================================ */
(function (root) {
  'use strict';

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ── scales ───────────────────────────────────────────────────────────── */

  /** Round a span to human numbers: 1, 2, 2.5, 5, 10 x a power of ten. */
  function niceNumber(range, round) {
    if (!(range > 0)) return 1;
    const exp = Math.floor(Math.log10(range));
    const f = range / Math.pow(10, exp);
    let nf;
    if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
    else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nf * Math.pow(10, exp);
  }

  /**
   * Axis ticks that land on round numbers and cover the data.
   *
   * The domain is widened to the ticks, never the reverse: a label must name a
   * value the chart reaches, and clipping the data to pretty bounds would hide
   * the outlier that is usually the reason to look.
   */
  function niceTicks(min, max, count = 5) {
    if (!isFinite(min) || !isFinite(max)) return { min: 0, max: 1, ticks: [0, 1] };
    if (min === max) { const p = Math.abs(min) || 1; min -= p * 0.5; max += p * 0.5; }
    const step = niceNumber(niceNumber(max - min, false) / (count - 1), true);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    // Accumulate by index, not by repeated addition: 0.1 + 0.1 + 0.1 drifts.
    for (let i = 0; lo + i * step <= hi + step * 1e-9; i++) {
      ticks.push(Number((lo + i * step).toPrecision(12)));
    }
    return { min: lo, max: hi, ticks };
  }

  function scale(d0, d1, r0, r1) {
    const span = d1 - d0;
    return (v) => (span === 0 ? (r0 + r1) / 2 : r0 + ((v - d0) / span) * (r1 - r0));
  }

  /* ── formatting ───────────────────────────────────────────────────────── */

  function money(v) {
    const n = Math.abs(v);
    if (n >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return '$' + (v / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
    if (n >= 1e3) return '$' + Math.round(v / 1e3) + 'k';
    return '$' + Math.round(v);
  }

  /* ── strip plot ───────────────────────────────────────────────────────── */

  /**
   * Every recorded per-unit price as one mark, bulk deeds marked apart, the
   * single-unit median drawn as a line.
   *
   * This is the chart the README's biggest trap wants: one real building shows
   * 148 folios carrying prices up to $4.9M each that are really a handful of
   * bulk deeds. As a paragraph of warning it is easy to skim. As a picture the
   * two populations do not look like one.
   *
   * points: [{value, bulk, label}]
   */
  function stripPlot(points, opts = {}) {
    const w = opts.width || 520;
    const h = opts.height || 96;
    const pad = { l: 8, r: 8, t: 10, b: 24 };
    const title = opts.title || 'Recorded price per unit';
    const fmt = opts.format || money;
    const pts = (points || []).filter((p) => isFinite(p.value));

    if (!pts.length) {
      return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}: no data">
        <title>${esc(title)}</title><desc>No recorded price to plot.</desc>
        <text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="chart-empty">No recorded price</text></svg>`;
    }

    const vals = pts.map((p) => p.value);
    const ax = niceTicks(Math.min(...vals), Math.max(...vals), 5);
    const x = scale(ax.min, ax.max, pad.l, w - pad.r);
    const yMid = pad.t + (h - pad.t - pad.b) / 2;

    const singles = pts.filter((p) => !p.bulk).map((p) => p.value).sort((a, b) => a - b);
    const median = singles.length
      ? (singles.length % 2 ? singles[(singles.length - 1) / 2]
        : (singles[singles.length / 2 - 1] + singles[singles.length / 2]) / 2)
      : null;

    const gridline = ax.ticks.map((t) =>
      `<line class="chart-grid" x1="${x(t).toFixed(1)}" y1="${pad.t}" x2="${x(t).toFixed(1)}" y2="${h - pad.b}"/>`).join('');
    const labels = ax.ticks.map((t) =>
      `<text class="chart-tick" x="${x(t).toFixed(1)}" y="${h - pad.b + 14}" text-anchor="middle">${esc(fmt(t))}</text>`).join('');

    // Bulk marks drawn first so a single-unit sale is never hidden behind one:
    // the single-unit sales are the arm's-length signal and must stay readable.
    const order = [...pts].sort((a, b) => (a.bulk === b.bulk ? 0 : a.bulk ? -1 : 1));
    const marks = order.map((p) => {
      const cx = x(p.value).toFixed(1);
      const cls = p.bulk ? 'chart-mark bulk' : 'chart-mark single';
      const lab = `${p.label ? p.label + ': ' : ''}${fmt(p.value)}${p.bulk ? ' (bulk deed)' : ''}`;
      return `<circle class="${cls}" cx="${cx}" cy="${yMid}" r="${p.bulk ? 3.5 : 4.5}"><title>${esc(lab)}</title></circle>`;
    }).join('');

    const medLine = median == null ? '' :
      `<line class="chart-median" x1="${x(median).toFixed(1)}" y1="${pad.t - 2}" x2="${x(median).toFixed(1)}" y2="${h - pad.b + 2}"/>` +
      `<text class="chart-median-label" x="${x(median).toFixed(1)}" y="${pad.t + 8}" text-anchor="middle">median ${esc(fmt(median))}</text>`;

    const nBulk = pts.filter((p) => p.bulk).length;
    const desc = `${pts.length - nBulk} single-unit sales${nBulk ? ` and ${nBulk} folios on bulk deeds` : ''}`
      + (median == null ? '' : `, single-unit median ${fmt(median)}`);

    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(title)}">
      <title>${esc(title)}</title><desc>${esc(desc)}</desc>
      ${gridline}${marks}${medLine}${labels}</svg>`;
  }

  /* ── horizontal stacked bar ───────────────────────────────────────────── */

  /** segments: [{label, value, cls}] -- ownership concentration at a glance. */
  function stackedBar(segments, opts = {}) {
    const w = opts.width || 520;
    const h = opts.height || 26;
    const segs = (segments || []).filter((s) => s.value > 0);
    const total = segs.reduce((a, s) => a + s.value, 0);
    const title = opts.title || 'Distribution';
    if (!total) {
      return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}: no data">
        <title>${esc(title)}</title><desc>Nothing to distribute.</desc></svg>`;
    }
    let x = 0;
    const rects = segs.map((s) => {
      const sw = (s.value / total) * w;
      const r = `<rect class="chart-seg ${s.cls || ''}" x="${x.toFixed(1)}" y="0" width="${Math.max(0, sw - 1).toFixed(1)}" height="${h}">`
        + `<title>${esc(s.label)}: ${s.value} (${(100 * s.value / total).toFixed(1)}%)</title></rect>`;
      x += sw;
      return r;
    }).join('');
    const desc = segs.map((s) => `${s.label} ${(100 * s.value / total).toFixed(1)}%`).join(', ');
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}">
      <title>${esc(title)}</title><desc>${esc(desc)}</desc>${rects}</svg>`;
  }

  const api = { niceNumber, niceTicks, scale, money, stripPlot, stackedBar, esc };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Charts = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
