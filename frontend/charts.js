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


  /* ── shared anatomy ──────────────────────────────────────────────────── */

  // A 2px gap in the surface colour is what separates touching marks -- never a
  // stroke, which adds ink that is not data.
  const GAP = 2;
  const BAR_MAX = 24;          // cap the bar; the band's leftover is air
  const R = 4;                 // rounded data-end, square at the baseline

  /** Bar path with the far end rounded and the baseline end square. */
  function barPath(x, y, w, h, dir) {
    const r = Math.min(R, w / 2, Math.abs(h) / 2);
    if (!(h > 0)) return '';
    if (dir === 'up')
      return `M${x} ${y + h} V${y + r} Q${x} ${y} ${x + r} ${y} H${x + w - r} Q${x + w} ${y} ${x + w} ${y + r} V${y + h} Z`;
    if (dir === 'down')
      return `M${x} ${y} V${y + h - r} Q${x} ${y + h} ${x + r} ${y + h} H${x + w - r} Q${x + w} ${y + h} ${x + w} ${y + h - r} V${y} Z`;
    // right
    return `M${x} ${y} H${x + w - r} Q${x + w} ${y} ${x + w} ${y + r} V${y + h - r} Q${x + w} ${y + h} ${x + w - r} ${y + h} H${x} Z`;
  }

  function legend(items) {
    return `<div class="chart-legend">` + items.map((i) =>
      `<span><i class="${i.cls}"></i>${esc(i.label)}</span>`).join('') + `</div>`;
  }

  /* ── waterfall ───────────────────────────────────────────────────────── */

  /**
   * How a buyout total is arrived at, step by step.
   *
   * Not a categorical palette: the steps are not competing identities, they are
   * one accumulating quantity plus a risk component. So it is one hue for cost,
   * the warning token for the holdout premium, and a de-emphasised step for
   * units already controlled -- which cost nothing and would otherwise read as
   * spend.
   *
   * steps: [{label, value, kind: 'base'|'add'|'risk'|'total'}]
   */
  function waterfall(steps, opts = {}) {
    const w = opts.width || 520;
    const rowH = 30;
    const labelW = opts.labelWidth || 150;
    const fmt = opts.format || money;
    const title = opts.title || 'Buyout cost';
    const rows = (steps || []).filter((s) => isFinite(s.value));
    if (!rows.length) return empty(w, 80, title, 'Nothing to total.');

    const h = rows.length * rowH + 26;
    let run = 0;
    const laid = rows.map((s) => {
      const start = s.kind === 'total' ? 0 : run;
      if (s.kind !== 'total') run += s.value;
      return { ...s, start, end: s.kind === 'total' ? s.value : run };
    });
    const max = Math.max(...laid.map((l) => Math.max(l.start, l.end)), 0);
    const ax = niceTicks(0, max, 4);
    const x = scale(0, ax.max, labelW, w - 8);

    const grid = ax.ticks.map((t) =>
      `<line class="chart-grid" x1="${x(t).toFixed(1)}" y1="14" x2="${x(t).toFixed(1)}" y2="${h - 22}"/>`
      + `<text class="chart-tick" x="${x(t).toFixed(1)}" y="${h - 8}" text-anchor="middle">${esc(fmt(t))}</text>`).join('');

    const bars = laid.map((l, i) => {
      const y = 18 + i * rowH + (rowH - BAR_MAX) / 2;
      const x0 = x(Math.min(l.start, l.end)), x1 = x(Math.max(l.start, l.end));
      const bw = Math.max(0, x1 - x0 - (l.kind === 'total' ? 0 : GAP));
      const cls = `chart-step ${l.kind}`;
      return `<path class="${cls}" d="${barPath(x0, y, bw, BAR_MAX, 'right')}">`
        + `<title>${esc(l.label)}: ${esc(fmt(l.value))}</title></path>`
        + `<text class="chart-rowlabel" x="${labelW - 8}" y="${y + BAR_MAX / 2 + 3}" text-anchor="end">${esc(l.label)}</text>`
        // The value rides the bar: four steps, so every one is worth labelling.
        + `<text class="chart-value" x="${x1 + 5}" y="${y + BAR_MAX / 2 + 3}">${esc(fmt(l.value))}</text>`;
    }).join('');

    const desc = laid.map((l) => `${l.label} ${fmt(l.value)}`).join('; ');
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(title)}">
      <title>${esc(title)}</title><desc>${esc(desc)}</desc>${grid}${bars}</svg>`;
  }

  /* ── diverging bars ──────────────────────────────────────────────────── */

  /**
   * Components of population change, by year.
   *
   * NOT a stacked area, which is the obvious choice and the wrong one: domestic
   * migration goes negative -- Columbus ran +83k overall on NEGATIVE domestic
   * migration -- and a stacked area cannot show a negative component without
   * lying about the total. Positives stack up from zero, negatives stack down,
   * and the zero line is the reference the reader actually needs.
   *
   * rows: [{label, values: {key: number}}]; series: [{key, label, cls}]
   */
  function divergingBars(rows, series, opts = {}) {
    const w = opts.width || 520;
    const h = opts.height || 190;
    const pad = { l: 46, r: 8, t: 10, b: 26 };
    const fmt = opts.format || ((v) => (v >= 1000 || v <= -1000 ? (v / 1000).toFixed(0) + 'k' : String(Math.round(v))));
    const title = opts.title || 'Components of change';
    const data = rows || [];
    if (!data.length || !series.length) return empty(w, h, title, 'No series to plot.');

    let lo = 0, hi = 0;
    for (const r of data) {
      let up = 0, dn = 0;
      for (const s of series) {
        const v = r.values[s.key] || 0;
        if (v >= 0) up += v; else dn += v;
      }
      hi = Math.max(hi, up); lo = Math.min(lo, dn);
    }
    const ax = niceTicks(lo, hi, 5);
    const y = scale(ax.min, ax.max, h - pad.b, pad.t);
    const band = (w - pad.l - pad.r) / data.length;
    const bw = Math.min(BAR_MAX, band * 0.62);

    const grid = ax.ticks.map((t) => {
      const yy = y(t).toFixed(1);
      return `<line class="chart-grid${t === 0 ? ' zero' : ''}" x1="${pad.l}" y1="${yy}" x2="${w - pad.r}" y2="${yy}"/>`
        + `<text class="chart-tick" x="${pad.l - 6}" y="${(+yy + 3).toFixed(1)}" text-anchor="end">${esc(fmt(t))}</text>`;
    }).join('');

    const bars = data.map((r, i) => {
      const cx = pad.l + band * (i + 0.5) - bw / 2;
      let up = 0, dn = 0;
      const segs = series.map((s) => {
        const v = r.values[s.key] || 0;
        if (!v) return '';
        let y0, y1;
        if (v >= 0) { y0 = y(up + v); y1 = y(up); up += v; }
        else { y0 = y(dn); y1 = y(dn + v); dn += v; }
        const hh = Math.max(0, Math.abs(y1 - y0) - GAP);
        return `<path class="chart-seg ${s.cls}" d="${barPath(cx, Math.min(y0, y1), bw, hh, v >= 0 ? 'up' : 'down')}">`
          + `<title>${esc(r.label)} — ${esc(s.label)}: ${esc(fmt(v))}</title></path>`;
      }).join('');
      return segs + `<text class="chart-tick" x="${(cx + bw / 2).toFixed(1)}" y="${h - 8}" text-anchor="middle">${esc(r.label)}</text>`;
    }).join('');

    const desc = series.map((s) => s.label).join(', ') + ` by ${data.length} periods`;
    return legend(series.map((s) => ({ cls: s.cls, label: s.label })))
      + `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(title)}">
      <title>${esc(title)}</title><desc>${esc(desc)}</desc>${grid}${bars}</svg>`;
  }

  /* ── emphasis scatter ────────────────────────────────────────────────── */

  /**
   * One metro against all 469, on two axes it is worth reading together.
   *
   * Emphasis, not categorical: the subject is one point and the other 468 are
   * context, so they are the de-emphasis gray and the subject carries the
   * accent. Colouring 469 points by anything would bury the one that matters.
   *
   * points: [{x, y, label, highlight}]
   */
  function scatter(points, opts = {}) {
    const w = opts.width || 520;
    const h = opts.height || 300;
    const pad = { l: 48, r: 12, t: 12, b: 34 };
    const pts = (points || []).filter((p) => isFinite(p.x) && isFinite(p.y));
    const title = opts.title || 'Scatter';
    if (!pts.length) return empty(w, h, title, 'No metros to plot.');

    const ax = niceTicks(Math.min(...pts.map((p) => p.x)), Math.max(...pts.map((p) => p.x)), 5);
    const ay = niceTicks(Math.min(...pts.map((p) => p.y)), Math.max(...pts.map((p) => p.y)), 5);
    const X = scale(ax.min, ax.max, pad.l, w - pad.r);
    const Y = scale(ay.min, ay.max, h - pad.b, pad.t);
    const fx = opts.formatX || String, fy = opts.formatY || String;

    const grid = ax.ticks.map((t) =>
      `<line class="chart-grid" x1="${X(t).toFixed(1)}" y1="${pad.t}" x2="${X(t).toFixed(1)}" y2="${h - pad.b}"/>`
      + `<text class="chart-tick" x="${X(t).toFixed(1)}" y="${h - pad.b + 14}" text-anchor="middle">${esc(fx(t))}</text>`).join('')
      + ay.ticks.map((t) =>
      `<line class="chart-grid" x1="${pad.l}" y1="${Y(t).toFixed(1)}" x2="${w - pad.r}" y2="${Y(t).toFixed(1)}"/>`
      + `<text class="chart-tick" x="${pad.l - 6}" y="${(Y(t) + 3).toFixed(1)}" text-anchor="end">${esc(fy(t))}</text>`).join('');

    // Context first, subject last, so the point that matters is never underneath.
    const ctx = pts.filter((p) => !p.highlight).map((p) =>
      `<circle class="chart-dot" cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="2.5"><title>${esc(p.label || '')}</title></circle>`).join('');
    const hot = pts.filter((p) => p.highlight).map((p) =>
      `<circle class="chart-dot hot" cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="5"><title>${esc(p.label || '')}</title></circle>`
      + `<text class="chart-value hot" x="${(X(p.x) + 9).toFixed(1)}" y="${(Y(p.y) + 4).toFixed(1)}">${esc(p.label || '')}</text>`).join('');

    const axisLabels =
      `<text class="chart-axis-title" x="${(pad.l + w - pad.r) / 2}" y="${h - 4}" text-anchor="middle">${esc(opts.xLabel || '')}</text>`
      + `<text class="chart-axis-title" transform="translate(11 ${(pad.t + h - pad.b) / 2}) rotate(-90)" text-anchor="middle">${esc(opts.yLabel || '')}</text>`;

    const named = pts.filter((p) => p.highlight).map((p) => p.label).join(', ');
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${esc(title)}">
      <title>${esc(title)}</title><desc>${esc(pts.length)} metros${named ? `, highlighting ${esc(named)}` : ''}.</desc>
      ${grid}${ctx}${hot}${axisLabels}</svg>`;
  }

  function empty(w, h, title, msg) {
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}: no data">
      <title>${esc(title)}</title><desc>${esc(msg)}</desc>
      <text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="chart-empty">${esc(msg)}</text></svg>`;
  }

  const api = { niceNumber, niceTicks, scale, money, stripPlot, stackedBar,
                waterfall, divergingBars, scatter, barPath, legend, esc };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Charts = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
