/* ============================================================
   Groundwork — workspace
   Mode switching plus the three non-map surfaces: condo takeovers
   (table beside the map), the metro screener, and reference.

   Loaded after app.js, so `map`, `esc`, `fetchJSON` and friends are
   already bound at global scope. The map element itself is moved
   between the shell and the records pane rather than duplicated —
   one WebGL context, one set of layers, one selection.
   ============================================================ */
(function () {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const el = (id) => document.getElementById(id);

  const fmt = (v, d = 0) => v == null || v === '' ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const usd = (v) => v == null || v === '' ? '—' : '$' + Math.round(Number(v)).toLocaleString();
  const pc = (v, d = 0) => v == null || v === '' ? '—' : Number(v).toFixed(d) + '%';
  const signed = (v, d = 1) => v == null || v === '' ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(d);

  function bar(v) {
    const p = Math.max(0, Math.min(100, v || 0));
    return `<span class="bar"><i style="width:${p}%"></i><span>${v == null ? '—' : Number(v).toFixed(0)}</span></span>`;
  }

  // ── mode switching ────────────────────────────────────────────────────
  const TARGET_LAYERS = ['targets-pts', 'targets-label'];
  let mode = 'map';
  let recordsLoaded = false;
  let marketsLoaded = false;

  function setMode(next) {
    if (next === mode) return;
    mode = next;
    for (const b of document.querySelectorAll('.mode-btn')) {
      b.classList.toggle('active', b.dataset.mode === next);
    }
    const showMap = next === 'map' || next === 'records';
    el('sidebar').hidden = next !== 'map';
    el('workspace').hidden = next === 'map';
    for (const [id, m] of [['ws-records', 'records'], ['ws-markets', 'markets'], ['ws-reference', 'reference']]) {
      el(id).hidden = next !== m;
    }

    // Reparent the live map rather than making a second one.
    const mapEl = el('map');
    const home = el('shell');
    const slot = el('records-map-slot');
    if (next === 'records') {
      if (mapEl.parentElement !== slot) slot.appendChild(mapEl);
    } else if (mapEl.parentElement !== home) {
      home.appendChild(mapEl);
    }
    mapEl.hidden = !showMap;

    // Target pins belong to Records only; the map view is about parcels.
    for (const id of TARGET_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', next === 'records' ? 'visible' : 'none');
    }
    if (map.getLayer('targets-sel')) {
      map.setLayoutProperty('targets-sel', 'visibility', next === 'records' ? 'visible' : 'none');
    }

    if (showMap) requestAnimationFrame(() => map.resize());
    if (next === 'records' && !recordsLoaded) { recordsLoaded = true; initRecords(); }
    if (next === 'markets' && !marketsLoaded) { marketsLoaded = true; initMarkets(); wireNationalFlows(); }
    history.replaceState(null, '', next === 'map' ? location.pathname + location.hash : '#mode=' + next);
  }

  for (const b of document.querySelectorAll('.mode-btn')) {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  }
  for (const a of document.querySelectorAll('.go-reference')) {
    a.addEventListener('click', (e) => { e.preventDefault(); setMode('reference'); });
  }

  // ══ condo takeovers ═══════════════════════════════════════════════════

  const PAGE = 100;
  let tOffset = 0, tTotal = 0, tSelected = null;

  function tFilters() {
    return {
      q: el('t-q').value.trim(),
      city: el('t-city').value,
      min_units: el('t-units').value || 0,
      min_age: el('t-age').value || 0,
      min_conc: el('t-conc').value || 0,
      milestone_only: el('t-milestone').checked ? 'true' : '',
    };
  }
  function tParams(extra) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...tFilters(), ...extra })) {
      if (v !== '' && v != null) p.set(k, v);
    }
    return p;
  }

  async function loadTargets() {
    const body = el('t-rows');
    body.innerHTML = '<tr><td colspan="8" class="ws-empty">Loading…</td></tr>';
    let r;
    try {
      r = await fetchJSON('/api/targets?' + tParams({ limit: PAGE, offset: tOffset, sort: el('t-sort').value }));
    } catch (e) {
      body.innerHTML = '<tr><td colspan="8" class="ws-empty">Could not load targets.</td></tr>';
      return;
    }
    tTotal = r.total;
    body.innerHTML = r.rows.map((t) => {
      const flags = [];
      if (t.milestone_due) flags.push('<span class="tag warm">milestone</span>');
      if ((t.top_owner_pct || 0) >= 50) flags.push('<span class="tag hot">bulk owner</span>');
      if (t.stage2_verified) flags.push('<span class="tag cool">verified</span>');
      return `<tr data-k="${esc(t.group_key)}" data-lon="${t.lon ?? ''}" data-lat="${t.lat ?? ''}">
        <td><div class="nm">${esc(t.condo_name || t.addr_primary || '—')}</div>
            <div class="sub">${esc([t.addr_primary, t.city].filter(Boolean).join(' · '))} ${flags.join(' ')}</div></td>
        <td class="n">${fmt(t.units_nal)}</td>
        <td class="n">${t.age_years ?? '—'}</td>
        <td class="n">${pc(t.top_owner_pct)}</td>
        <td class="n">${pc(t.top_mail_pct)}</td>
        <td class="n dim">${pc(t.absentee_pct)}</td>
        <td class="n">${usd(t.jv_per_unit)}</td>
        <td class="n">${bar(t.score)}</td></tr>`;
    }).join('') || '<tr><td colspan="8" class="ws-empty">No condos match these filters.</td></tr>';

    el('t-count').textContent = tTotal
      ? `${(tOffset + 1).toLocaleString()}–${Math.min(tOffset + PAGE, tTotal).toLocaleString()} of ${tTotal.toLocaleString()}`
      : 'No matches';
    el('t-page').textContent = tTotal ? `${Math.floor(tOffset / PAGE) + 1} / ${Math.ceil(tTotal / PAGE)}` : '';
    el('t-prev').disabled = tOffset === 0;
    el('t-next').disabled = tOffset + PAGE >= tTotal;

    for (const tr of body.querySelectorAll('tr[data-k]')) {
      tr.addEventListener('click', () => selectTarget(tr.dataset.k, true));
      tr.addEventListener('mouseenter', () => highlightPin(tr.dataset.k));
      tr.addEventListener('mouseleave', () => highlightPin(tSelected));
    }
    if (tSelected) markActiveRow(tSelected);
  }

  // The table pages; the map shows the entire filtered set. That split is the
  // whole point of putting them side by side.
  async function loadTargetPins(fit) {
    let gj;
    try {
      gj = await fetchJSON('/api/targets.geojson?' + tParams({}), { timeoutMs: 30000 });
    } catch (e) { return; }
    map.getSource('targets')?.setData(gj);
    if (fit && gj.features && gj.features.length) {
      let w = 180, s = 90, e2 = -180, n = -90;
      for (const f of gj.features) {
        const [x, y] = f.geometry.coordinates;
        if (x < w) w = x; if (x > e2) e2 = x;
        if (y < s) s = y; if (y > n) n = y;
      }
      if (w <= e2) map.fitBounds([[w, s], [e2, n]], { padding: 48, maxZoom: 13, duration: 600 });
    }
  }

  function highlightPin(key) {
    if (map.getLayer('targets-sel')) {
      map.setFilter('targets-sel', ['==', ['get', 'group_key'], key || '']);
    }
  }
  function markActiveRow(key) {
    for (const tr of document.querySelectorAll('#t-rows tr[data-k]')) {
      tr.classList.toggle('active', tr.dataset.k === key);
    }
  }

  function selectTarget(key, fly) {
    tSelected = key;
    highlightPin(key);
    markActiveRow(key);
    const tr = document.querySelector(`#t-rows tr[data-k="${CSS.escape(key)}"]`);
    if (fly && tr && tr.dataset.lon) {
      map.flyTo({ center: [Number(tr.dataset.lon), Number(tr.dataset.lat)], zoom: Math.max(map.getZoom(), 15.5), duration: 700 });
    }
    openDrawer(key);
  }

  let tDebounce = null;
  function tRefresh(resetPage) {
    if (resetPage) tOffset = 0;
    clearTimeout(tDebounce);
    tDebounce = setTimeout(() => { loadTargets(); loadTargetPins(false); }, 220);
  }

  async function initRecords() {
    try {
      const cities = await fetchJSON('/api/condo/cities');
      el('t-city').innerHTML = '<option value="">All cities</option>' +
        cities.map((c) => `<option value="${esc(c.city)}">${esc(c.city)} (${c.n})</option>`).join('');
    } catch (e) { /* filter still works without the city list */ }

    for (const id of ['t-q', 't-units', 't-age', 't-conc']) {
      el(id).addEventListener('input', () => tRefresh(true));
    }
    for (const id of ['t-city', 't-sort', 't-milestone']) {
      el(id).addEventListener('change', () => tRefresh(true));
    }
    for (const th of document.querySelectorAll('th[data-tsort]')) {
      th.addEventListener('click', () => { el('t-sort').value = th.dataset.tsort; tRefresh(true); });
    }
    el('t-prev').addEventListener('click', () => { tOffset = Math.max(0, tOffset - PAGE); loadTargets(); });
    el('t-next').addEventListener('click', () => { if (tOffset + PAGE < tTotal) { tOffset += PAGE; loadTargets(); } });
    el('t-export').addEventListener('click', () => {
      location.href = '/api/targets.xlsx?' + new URLSearchParams({ limit: 1000, min_units: el('t-units').value || 0 });
    });

    map.on('click', 'targets-pts', (e) => {
      const p = e.features[0]?.properties;
      if (p) selectTarget(p.group_key, false);
    });
    map.on('mouseenter', 'targets-pts', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'targets-pts', () => { map.getCanvas().style.cursor = ''; });

    let tpop = null;
    map.on('mousemove', 'targets-pts', (e) => {
      const p = e.features[0]?.properties || {};
      if (!tpop) tpop = new maplibregl.Popup({ closeButton: false, offset: 10, maxWidth: '260px' });
      tpop.setLngLat(e.lngLat).setHTML(
        `<b>${esc(p.condo_name || '')}</b><br/>${fmt(p.units_nal)} units · built ${p.act_yr_blt || '—'}<br/>` +
        `Top owner ${pc(p.top_owner_pct)} · score ${p.score == null ? '—' : Number(p.score).toFixed(0)}`).addTo(map);
    });
    map.on('mouseleave', 'targets-pts', () => { tpop && tpop.remove(); tpop = null; });

    await loadTargets();
    await loadTargetPins(true);
  }

  // ── target drawer ─────────────────────────────────────────────────────

  function closeDrawer() {
    el('t-drawer').hidden = true;
    el('t-scrim').hidden = true;
  }
  el('t-close').addEventListener('click', closeDrawer);
  el('t-scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

  async function openDrawer(key) {
    const box = el('t-detail');
    el('t-drawer').hidden = false;
    el('t-scrim').hidden = false;
    el('t-drawer').scrollTop = 0;
    box.innerHTML = '<p class="msg">Loading…</p>';

    let d;
    try { d = await fetchJSON('/api/target/' + encodeURIComponent(key)); }
    catch (e) { box.innerHTML = '<p class="msg">Could not load this building.</p>'; return; }
    const t = d.target;
    const g = d.group;

    const owners = d.owners.map((o) => `<tr>
      <td>${esc(o.owner_name)}${o.is_entity ? ' <span class="tag">entity</span>' : ''}</td>
      <td class="dim">${esc([o.owner_city, o.owner_state].filter(Boolean).join(', '))}</td>
      <td class="n">${o.units}</td>
      <td class="n">${t.units_nal ? (100 * o.units / t.units_nal).toFixed(1) + '%' : '—'}</td></tr>`).join('');

    const mail = d.shared_mailing.length
      ? d.shared_mailing.map((m) => `<tr><td>${esc(m.owner_addr_norm)}</td>
          <td class="n">${m.names}</td><td class="n">${m.units}</td></tr>`).join('')
      : '<tr><td colspan="3" class="dim">No mailing address is shared by more than one unit.</td></tr>';

    box.innerHTML = `
      <h2>${esc(t.condo_name || t.addr_primary || key)}</h2>
      <p class="sub">${esc([t.addr_primary, t.city].filter(Boolean).join(' · '))}
        &nbsp;<span class="mono">${esc(t.group_key)}</span></p>

      <div class="cells">
        <div><b>${fmt(t.units_nal)}</b><span>Units</span></div>
        <div><b>${t.act_yr_blt || '—'}</b><span>Built</span></div>
        <div><b>${t.age_years ?? '—'}</b><span>Age</span></div>
        <div><b>${t.recorded_year || '—'}</b><span>Decl. recorded</span></div>
        <div><b>${usd(t.jv_per_unit)}</b><span>Assessed / unit</span></div>
        <div><b>${usd(t.lnd_val_per_unit)}</b><span>Land / unit</span></div>
        <div><b>${pc(t.top_owner_pct, 1)}</b><span>Top owner</span></div>
        <div><b>${t.score == null ? '—' : t.score.toFixed(1)}</b><span>Score</span></div>
      </div>

      <div class="actions">
        <button class="primary-btn solid" id="t-memo">Offering memo</button>
        <button class="ghost-btn" id="t-memo-save">Memo &amp; save</button>
        <button class="ghost-btn" id="t-onmap">Show on map</button>
        <span class="msg" id="t-memo-msg"></span>
      </div>

      <div class="note">Score = ${Math.round((t.score_age ?? 0))} age · ${Math.round((t.score_scale ?? 0))} scale
        · ${Math.round((t.score_concentration ?? 0))} concentration · ${Math.round((t.score_absentee ?? 0))} absentee.
        DBPR match: <b>${esc(t.match_method)}</b>${t.match_confidence ? ` (confidence ${t.match_confidence})` : ''}.
        ${t.milestone_due ? '<br><b>Milestone inspection window is open</b> under FS 553.899 — verify against the current statute.' : ''}</div>

      <h3>Sales comparables</h3>
      <div id="t-comps"><p class="msg">Loading comps…</p></div>

      <h3>Redevelopment capacity</h3>
      <div class="capbar">
        <input id="t-lot" type="number" min="0" step="100" placeholder="Site area in SF" />
        <button class="ghost-btn" id="t-cap-go">Analyze</button>
        <span class="msg" id="t-cap-hint"></span>
      </div>
      <div id="t-cap"><p class="msg">Loading zoning envelope…</p></div>

      <h3>What the buyout costs</h3>
      <div id="t-econ"><p class="msg">Estimating…</p></div>

      <h3>Ownership concentration</h3>
      <div id="t-conc"></div>
      <table class="mini"><thead><tr><th>Owner</th><th>Mailing</th><th class="n">Units</th><th class="n">Share</th></tr></thead>
        <tbody>${owners}</tbody></table>
      <div class="note">${g ? `${fmt(g.distinct_owners)} distinct owners across ${fmt(t.units_nal)} units.
        Association-owned units (${fmt(g.assoc_owned_units)}, ${pc(g.assoc_owned_pct)}) are excluded from the
        largest-owner figure — the HOA is not an acquirer.` : ''}</div>

      <h3>Shared mailing addresses</h3>
      <table class="mini"><thead><tr><th>Mailing address</th><th class="n">Names</th><th class="n">Units</th></tr></thead>
        <tbody>${mail}</tbody></table>
      <div class="note">One buyer behind several LLCs shows up here before it shows up in any single owner name.</div>

      <h3>Declaration review</h3>
      <div class="form">
        <label>Termination threshold
          <select id="f-thr"><option value="">Not reviewed</option>
            <option>100% (unanimous)</option><option>95%</option><option>90%</option>
            <option>80%</option><option>75%</option><option>other — see notes</option></select></label>
        <label>Kaufman language
          <select id="f-kauf"><option value="">Not reviewed</option>
            <option value="orig">Present — ORIGINAL declaration</option>
            <option value="amend">Present — added BY AMENDMENT</option>
            <option value="none">Not present</option></select></label>
        <label>Declaration OR book <input id="f-book" value="${esc(t.declaration_or_book || '')}" /></label>
        <label>Declaration OR page <input id="f-page" value="${esc(t.declaration_or_page || '')}" /></label>
        <label class="wide">Source URL <input id="f-url" value="${esc(t.declaration_source_url || '')}" placeholder="link to the recorded instrument" /></label>
        <label class="wide">Notes <textarea id="f-notes">${esc(t.stage2_notes || '')}</textarea></label>
        <label class="wide rowchk"><input type="checkbox" id="f-ver" ${t.stage2_verified ? 'checked' : ''} />
          I have read the recorded declaration and verified the above</label>
      </div>
      <div class="note warn"><b>Why the Kaufman distinction matters.</b> Florida's Third DCA ruled against a developer
        that acquired 183 of 192 units and then amended the declaration to add Kaufman language and drop the threshold
        from unanimous to 80%. Kaufman in the <i>original</i> recorded declaration is what carries weight; Kaufman added
        later by a bulk owner is the fact pattern that lost.</div>
      <div class="actions">
        <button class="primary-btn solid" id="t-save">Save review</button>
        <span class="msg" id="t-save-msg"></span>
      </div>`;

    // memo
    const memoUrl = (save) => `/api/memo/${encodeURIComponent(key)}${save ? '?save=true' : ''}`;
    el('t-memo').addEventListener('click', () => window.open(memoUrl(false), '_blank', 'noopener'));
    el('t-memo-save').addEventListener('click', () => {
      window.open(memoUrl(true), '_blank', 'noopener');
      el('t-memo-msg').textContent = 'saved to data\\memos\\';
    });
    el('t-onmap').addEventListener('click', () => {
      if (t.lon != null) map.flyTo({ center: [t.lon, t.lat], zoom: 16.5, duration: 700 });
      closeDrawer();
    });

    // stage 2
    const k = t.kaufman_original ? 'orig' : t.kaufman_by_amendment ? 'amend'
      : (t.kaufman_original === 0 && t.kaufman_by_amendment === 0) ? 'none' : '';
    el('f-kauf').value = k;
    if (t.termination_threshold) el('f-thr').value = t.termination_threshold;
    el('t-save').addEventListener('click', async () => {
      const kv = el('f-kauf').value;
      const res = await fetch(`/api/target/${encodeURIComponent(key)}/stage2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          termination_threshold: el('f-thr').value || null,
          kaufman_original: kv ? kv === 'orig' : null,
          kaufman_by_amendment: kv ? kv === 'amend' : null,
          declaration_or_book: el('f-book').value || null,
          declaration_or_page: el('f-page').value || null,
          declaration_source_url: el('f-url').value || null,
          stage2_verified: el('f-ver').checked,
          stage2_notes: el('f-notes').value || null,
        }),
      });
      el('t-save-msg').textContent = res.ok ? 'Saved.' : 'Save failed.';
      if (res.ok) loadTargets();
    });

    loadComps(key);
    wireCapacity(key);
    loadEconomics(key);
    drawConcentration(d);
  }

  // Ownership as one bar: whether a building is already being assembled should
  // read at a glance rather than by scanning a column of percentages. Ordered
  // by size, so it is one hue getting lighter, not four competing identities.
  function drawConcentration(d) {
    const box = el('t-conc');
    if (!box || !window.Charts) return;
    const total = d.target.units_nal || 0;
    const owners = (d.owners || []).slice();
    if (!total || !owners.length) { box.innerHTML = ''; return; }
    const top = owners[0], next = owners.slice(1, 5);
    const nextUnits = next.reduce((a, o) => a + o.units, 0);
    const tail = Math.max(0, total - top.units - nextUnits);
    box.innerHTML = Charts.stackedBar([
      { label: top.owner_name, value: top.units, cls: 'top' },
      { label: `next ${next.length} owners`, value: nextUnits, cls: 'next' },
      { label: `${d.group.distinct_owners - 1 - next.length} remaining owners`, value: tail, cls: 'tail' },
    ], { title: 'Ownership concentration' }) + `<div class="chart-legend">
      <span><i class="seg-top"></i>${esc((top.owner_name || '').slice(0, 26))} — ${pc(100 * top.units / total, 1)}</span>
      <span><i class="seg-next"></i>Next ${next.length}</span>
      <span><i class="seg-tail"></i>The rest</span></div>`;
  }

  async function loadEconomics(key) {
    const box = el('t-econ');
    if (!box) return;
    let e;
    try { e = await fetchJSON(`/api/economics/${encodeURIComponent(key)}`, { timeoutMs: 30000 }); }
    catch (err) { box.innerHTML = '<p class="msg">Buyout estimate unavailable.</p>'; return; }
    if (e.cost_at_fmv == null) {
      box.innerHTML = `<div class="note warn">${esc(e.caveats[0] || 'Nothing here could be priced.')}</div>`;
      return;
    }
    const premium = (e.cost_with_holdout || e.cost_at_fmv) - e.cost_at_fmv;
    const chart = window.Charts ? Charts.waterfall([
      { label: `${fmt(e.units_to_acquire)} units to acquire`, value: e.cost_at_fmv, kind: 'add' },
      { label: `Holdout premium (${Math.round(e.holdout_share * 100)}% at +${Math.round(e.holdout_premium * 100)}%)`,
        value: premium, kind: 'risk' },
      { label: 'Total', value: e.cost_with_holdout, kind: 'total' },
    ], { title: 'Buyout cost' }) : '';
    box.innerHTML = `
      <div class="cells">
        <div><b>${usd(e.cost_with_holdout)}</b><span>Est. buyout</span></div>
        <div><b>${usd(e.median_unit_value)}</b><span>Median unit</span></div>
        <div><b>${fmt(e.controlled_units)}</b><span>Already held</span></div>
        <div><b>${fmt(e.units_to_acquire)}</b><span>To acquire</span></div>
      </div>
      ${chart}
      <div class="note">Priced from ${esc(e.basis_summary || 'recorded sales')}.
        ${e.psf ? `$/SF ${usd(e.psf)} from ${esc((e.psf_source || '').replace(/_/g, ' '))}.` : ''}</div>
      ${e.caveats.map((c) => `<div class="note warn">${esc(c)}</div>`).join('')}`;
  }

  async function loadComps(key) {
    const box = el('t-comps');
    let c;
    try { c = await fetchJSON(`/api/condo-comps?group_key=${encodeURIComponent(key)}`, { timeoutMs: 30000 }); }
    catch (e) { box.innerHTML = '<p class="msg">Comps unavailable.</p>'; return; }
    const s = c.summary;

    const inRows = c.in_building.slice(0, 12).map((r) => `<tr>
      <td>${r.sale_yr1 || '—'}</td>
      <td class="dim">${esc((r.buyer || '').slice(0, 30))}${r.bulk ? ' <span class="tag warm">bulk</span>' : ''}</td>
      <td class="n">${r.units}</td>
      <td class="n">${usd(r.sale_prc1)}</td>
      <td class="n">${usd(r.per_unit)}</td>
      <td class="n">${r.psf ? usd(r.psf) : '—'}</td></tr>`).join('')
      || '<tr><td colspan="6" class="dim">No recorded sale carries a price here.</td></tr>';

    const nearRows = c.nearby.map((n) => `<tr data-k="${esc(n.group_key)}">
      <td>${esc((n.condo_name || n.addr_primary || '—').slice(0, 30))}</td>
      <td class="n">${n.distance_mi.toFixed(2)}</td>
      <td class="n">${fmt(n.units_nal)}</td>
      <td class="n">${n.act_yr_blt || '—'}</td>
      <td class="n">${usd(n.sale_median)}</td>
      <td class="n">${n.sale_psf ? usd(n.sale_psf) : '—'}</td></tr>`).join('')
      || '<tr><td colspan="6" class="dim">No comparable buildings within the radius.</td></tr>';

    // The distribution, before the numbers. The bulk-deed trap is a paragraph of
    // warning in the table below and a paragraph is easy to skim; as a picture
    // the two populations plainly are not one.
    const strip = window.Charts ? Charts.stripPlot(
      c.in_building.map((r) => ({ value: r.per_unit, bulk: !!r.bulk,
        label: `${r.sale_yr1 || ''} ${(r.buyer || '').slice(0, 24)}`.trim() })),
      { title: 'Recorded price per unit' }) : '';
    const legend = strip && c.in_building.length ? `<div class="chart-legend">
      <span><i class="single"></i>Single-unit sale</span>
      <span><i class="bulk"></i>Folio on a bulk deed</span>
      <span>Dashed line: single-unit median</span></div>` : '';

    box.innerHTML = `
      ${strip}${legend}
      <div class="cells">
        <div><b>${fmt(s.units_with_price)}</b><span>Units w/ price</span></div>
        <div><b>${fmt(s.single_unit_count)}</b><span>Single-unit sales</span></div>
        <div><b>${usd(s.single_unit_median)}</b><span>Median, single unit</span></div>
        <div><b>${usd(s.last5_median_psf)}</b><span>Median $/SF</span></div>
        <div><b>${fmt(s.bulk_instruments)}</b><span>Bulk deeds</span></div>
        <div><b>${usd(s.bulk_median_per_unit)}</b><span>Bulk, per unit</span></div>
      </div>
      ${s.bulk_instruments ? `<div class="note warn"><b>Read the single-unit line.</b>
        ${fmt(s.bulk_instruments)} instrument(s) convey ${fmt(s.bulk_units)} units together — the tax roll stamps the
        package price on every folio, so those rows are one negotiation, not many. Per-unit figures on them are the
        consideration divided by units conveyed.</div>` : ''}
      <table class="mini"><thead><tr><th>Year</th><th>Buyer</th><th class="n">Units</th>
        <th class="n">Consideration</th><th class="n">Per unit</th><th class="n">$/SF</th></tr></thead>
        <tbody>${inRows}</tbody></table>

      <h3 style="margin-top:18px">Nearby buildings <span class="msg">within ${c.radius_mi} mi</span></h3>
      <table class="mini"><thead><tr><th>Building</th><th class="n">Miles</th><th class="n">Units</th>
        <th class="n">Built</th><th class="n">Median/unit</th><th class="n">$/SF</th></tr></thead>
        <tbody>${nearRows}</tbody></table>`;

    for (const tr of box.querySelectorAll('tbody tr[data-k]')) {
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', () => selectTarget(tr.dataset.k, true));
    }
  }

  function wireCapacity(key) {
    const box = el('t-cap');
    const run = async () => {
      const sf = parseFloat(el('t-lot').value) || null;
      box.innerHTML = '<p class="msg">Analyzing…</p>';
      let d;
      try { d = await fetchJSON(`/api/capacity?group_key=${encodeURIComponent(key)}${sf ? '&lot_sf=' + sf : ''}`, { timeoutMs: 30000 }); }
      catch (e) { box.innerHTML = '<p class="msg">Capacity lookup failed.</p>'; return; }
      if (!d.resolved) {
        box.innerHTML = `<div class="note">No zoning polygon covers this point — ${esc(d.reason || 'unresolved')}.</div>`;
        return;
      }
      const z = d.zoning, r = d.rebuild;
      const rows = (d.scenarios || []).map((s) => `<tr>
        <td>${esc(s.path)}${s.administrative ? ' <span class="tag">administrative</span>' : ''}</td>
        <td class="n">${s.units != null ? fmt(s.units) : '—'}</td>
        <td class="dim">${esc(s.requirement || '')}</td></tr>`).join('');
      box.innerHTML = `
        <div class="cells">
          <div><b>${esc(z.zone || '—')}</b><span>${esc(z.municipality || 'Zoning')}</span></div>
          <div><b>${z.density_per_acre ? fmt(z.density_per_acre) : '—'}</b><span>Density u/ac</span></div>
          <div><b>${z.far ? z.far + '×' : '—'}</b><span>Max FAR</span></div>
          <div><b>${z.max_height_stories ? fmt(z.max_height_stories) : '—'}</b><span>Max stories</span></div>
        </div>
        ${r ? `<div class="note${r.non_conforming ? ' warn' : ''}"><b>${esc(r.verdict)}</b>${r.non_conforming
          ? ` — the ${fmt(r.existing_units)} standing units exceed today's by-right ${fmt(r.by_right_units)}, so the site
             is legally non-conforming and a base-density rebuild loses units.` : ''}</div>` : ''}
        <table class="mini"><thead><tr><th>Development path</th><th class="n">Units</th><th>Key requirement</th></tr></thead>
          <tbody>${rows}</tbody></table>
        <div class="note">${esc(d.caveat || '')}</div>`;
      el('t-cap-hint').textContent = sf ? '' : 'Zoning shown; enter site area for unit counts.';
    };
    el('t-cap-go').addEventListener('click', run);
    el('t-lot').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    run();
  }

  // ══ markets ═══════════════════════════════════════════════════════════

  let mOffset = 0, mTotal = 0, HOME = null;

  function mParams() {
    const p = new URLSearchParams({
      limit: PAGE, offset: mOffset, q: el('m-q').value.trim(),
      stage: el('m-stage').value, state: el('m-state').value,
      min_pop: el('m-pop').value || 0, sort: el('m-sort').value,
    });
    p.set('metro_only', el('m-metro').checked ? 'true' : 'false');
    return p;
  }

  async function loadMarkets() {
    const body = el('m-rows');
    body.innerHTML = '<tr><td colspan="10" class="ws-empty">Loading…</td></tr>';
    let r;
    try { r = await fetchJSON('/api/metros?' + mParams()); }
    catch (e) { body.innerHTML = '<tr><td colspan="10" class="ws-empty">Could not load markets.</td></tr>'; return; }
    mTotal = r.total;
    HOME = HOME || r.home;
    body.innerHTML = r.rows.map((m) => `<tr data-c="${esc(m.cbsa)}">
      <td><div class="nm">${esc(m.name)}${m.cbsa === HOME ? ' <span class="tag info">home</span>' : ''}</div>
          <div class="sub">${esc(m.states || '')}</div></td>
      <td><span class="stage ${esc(m.stage)}">${esc(m.stage)}</span></td>
      <td class="n">${fmt(m.population)}</td>
      <td class="n">${signed(m.net_mig_total_rate)}</td>
      <td class="n">${usd(m.inflow_agi_per_return)}</td>
      <td class="n">${m.permits_per_1k == null ? '—' : m.permits_per_1k.toFixed(1)}</td>
      <td class="n">${usd(m.zhvi)}</td>
      <td class="n">${signed(m.zhvi_3yr, 0)}%</td>
      <td class="n">${bar(m.score_headroom)}</td>
      <td class="n">${bar(m.score)}</td></tr>`).join('')
      || '<tr><td colspan="10" class="ws-empty">No markets match.</td></tr>';

    el('m-count').textContent = mTotal
      ? `${(mOffset + 1).toLocaleString()}–${Math.min(mOffset + PAGE, mTotal).toLocaleString()} of ${mTotal.toLocaleString()}`
      : 'No matches';
    el('m-page').textContent = mTotal ? `${Math.floor(mOffset / PAGE) + 1} / ${Math.ceil(mTotal / PAGE)}` : '';
    el('m-prev').disabled = mOffset === 0;
    el('m-next').disabled = mOffset + PAGE >= mTotal;

    for (const tr of body.querySelectorAll('tr[data-c]')) {
      tr.addEventListener('click', () => openMarket(tr.dataset.c));
    }
  }

  // ── market due diligence ──────────────────────────────────────────────
  // Type a market, get the report. An ambiguous name is a question, not a
  // guess — "columbus" is five real metros and picking one silently would
  // produce a confident report about the wrong city.

  function ddReport(cbsa) {
    hideHits();
    window.open('/api/dd/' + encodeURIComponent(cbsa) + '/report', '_blank', 'noopener');
  }

  function hideHits() {
    const h = el('dd-hits');
    h.hidden = true;
    h.innerHTML = '';
  }

  function showHits(html) {
    const h = el('dd-hits');
    h.innerHTML = html;
    h.hidden = false;
  }

  async function ddSearch() {
    const q = el('dd-q').value.trim();
    if (q.length < 2) { hideHits(); return; }
    const btn = el('dd-go');
    btn.disabled = true;
    btn.textContent = 'Finding…';
    let r;
    try {
      r = await fetchJSON('/api/dd/resolve?q=' + encodeURIComponent(q));
    } catch (e) {
      showHits('<p class="msg">Could not reach the market index.</p>');
      return;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Build report';
    }

    if (!r.count) {
      showHits(`<p class="msg">No metro matches “${esc(q)}”. Try a city and state —
        “Columbus Ohio”, “Kansas City Missouri”.</p>`);
      return;
    }
    if (r.unambiguous) { ddReport(r.candidates[0].cbsa); return; }

    showHits(r.candidates.map((c) => `<button type="button" data-c="${esc(c.cbsa)}">
      <span>${esc(c.name)}</span>
      <span class="pop">${fmt(c.population)} · score ${(c.score ?? 0).toFixed(0)}</span>
    </button>`).join(''));
    for (const b of el('dd-hits').querySelectorAll('button[data-c]')) {
      b.addEventListener('click', () => ddReport(b.dataset.c));
    }
    el('dd-hits').querySelector('button')?.focus();
  }

  function initDD() {
    el('dd-go').addEventListener('click', ddSearch);
    el('dd-q').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); ddSearch(); }
      if (e.key === 'Escape') hideHits();
    });
    el('dd-q').addEventListener('input', () => { if (!el('dd-q').value.trim()) hideHits(); });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.dd-bar')) hideHits();
    });
  }

  async function initMarkets() {
    initDD();
    try {
      const s = await fetchJSON('/api/metro-stats');
      HOME = s.home;
      el('m-stage').innerHTML = '<option value="">All stages</option>' +
        ['Early', 'Emerging', 'Established', 'Steady', 'Cooling'].filter((k) => s.stages[k])
          .map((k) => `<option value="${k}">${k} (${s.stages[k]})</option>`).join('');
      el('m-state').innerHTML = '<option value="">All states</option>' +
        s.states.map((x) => `<option>${esc(x)}</option>`).join('');
    } catch (e) { /* filters degrade to free text */ }

    let d = null;
    for (const id of ['m-q', 'm-pop']) {
      el(id).addEventListener('input', () => { clearTimeout(d); d = setTimeout(() => { mOffset = 0; loadMarkets(); }, 220); });
    }
    for (const id of ['m-stage', 'm-state', 'm-sort', 'm-metro']) {
      el(id).addEventListener('change', () => { mOffset = 0; loadMarkets(); });
    }
    for (const th of document.querySelectorAll('th[data-msort]')) {
      th.addEventListener('click', () => { el('m-sort').value = th.dataset.msort; mOffset = 0; loadMarkets(); });
    }
    el('m-prev').addEventListener('click', () => { mOffset = Math.max(0, mOffset - PAGE); loadMarkets(); });
    el('m-next').addEventListener('click', () => { if (mOffset + PAGE < mTotal) { mOffset += PAGE; loadMarkets(); } });
    el('m-home').addEventListener('click', () => { if (HOME) openMarket(HOME); });

    await loadMarkets();
  }

  // 5.2 — the national scan. Loaded on first open rather than with the tab: it
  // reads every flow row, and most visits to Markets are not about migration.
  let flowsLoaded = false;
  function wireNationalFlows() {
    const panel = el('flows-panel');
    if (!panel) return;
    panel.addEventListener('toggle', async () => {
      if (!panel.open || flowsLoaded) return;
      flowsLoaded = true;
      const box = el('flows-national');
      box.innerHTML = '<p class="msg">Reading 109,000 flows…</p>';
      let f;
      try { f = await fetchJSON('/api/flows/national?limit=12&min_pop=250000', { timeoutMs: 60000 }); }
      catch (e) { flowsLoaded = false; box.innerHTML = '<p class="msg">National flows unavailable.</p>'; return; }

      const table = (rows, valueLabel, value) => `<table class="mini">
        <thead><tr><th>Metro</th><th class="n">${valueLabel}</th><th class="n">Arrivals' AGI</th></tr></thead>
        <tbody>${rows.map((r) => `<tr data-c="${esc(r.cbsa)}"><td>${esc(r.name)}</td>
          <td class="n">${value(r)}</td>
          <td class="n">${r.in_agi_per_return ? usd(r.in_agi_per_return) : '—'}</td></tr>`).join('')}</tbody></table>`;

      box.innerHTML = `
        <div class="flows-cols">
          <div><h4>Gaining households</h4>
            ${table(f.by_households, 'Net households', (r) => signed(r.net_returns, 0))}</div>
          <div><h4>Gaining income</h4>
            ${table(f.by_income, 'Net AGI', (r) => usd(r.net_agi))}</div>
        </div>
        ${f.divergent.length ? `<h4>The two disagree here</h4>
          <div class="note">A metro gaining households while losing AGI is gaining poor and losing
            rich — and the reverse happens too. No single ranking shows either.</div>
          ${table(f.divergent, 'Net households', (r) => signed(r.net_returns, 0))}` : ''}
        ${f.limits.map((l) => `<div class="note warn">${esc(l)}</div>`).join('')}`;

      for (const tr of box.querySelectorAll('tr[data-c]')) {
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', () => openMarket(tr.dataset.c));
      }
    });
  }

  // Corridors. The `out` direction has been in the database since the first
  // ingest and has never been shown anywhere, so until now the app could say a
  // metro lost 67,418 people domestically and not where a single one went.
  async function drawFlows(cbsa) {
    const box = el('m-flows');
    if (!box) return;
    let f;
    try { f = await fetchJSON(`/api/flows/${encodeURIComponent(cbsa)}?limit=8`, { timeoutMs: 30000 }); }
    catch (e) { box.innerHTML = '<p class="msg">Corridor data unavailable.</p>'; return; }
    const t = f.totals;
    const rows = [...f.gaining_from, ...f.losing_to]
      .sort((a, b) => b.net_returns - a.net_returns)
      .map((c) => ({
        label: (c.name || '').split(',')[0].slice(0, 22),
        inValue: c.in_returns, outValue: c.out_returns,
        // AGI per arriving household -- the one thing only this source knows.
        note: c.in_agi_per_return ? usd(c.in_agi_per_return) : '',
      }));
    box.innerHTML = `
      <div class="cells">
        <div><b>${fmt(t.in_returns)}</b><span>Households in</span></div>
        <div><b>${fmt(t.out_returns)}</b><span>Households out</span></div>
        <div><b>${signed(t.net_returns, 0)}</b><span>Net households</span></div>
        <div><b>${usd(t.net_agi)}</b><span>Net AGI</span></div>
      </div>
      ${window.Charts ? Charts.butterfly(rows, { title: 'Migration corridors' }) : ''}
      <div class="note">Right-hand figures are the average AGI of a household arriving on that
        corridor — IRS SOI is the only free source that attaches income to movers.
        ${t.net_returns > 0 && t.net_agi < 0 ? '<b>This metro is gaining households and losing income.</b>' : ''}</div>
      ${f.limits.map((l) => `<div class="note warn">${esc(l)}</div>`).join('')}`;
  }

  // Components of population change, by year. NOT a stacked area: domestic
  // migration goes negative -- Columbus added 83k people on NEGATIVE domestic
  // migration -- and a stacked area cannot show a negative component without
  // lying about the total. Positives above zero, negatives below it.
  async function drawComponents(cbsa) {
    const box = el('m-components');
    if (!box) return;
    let dd;
    try { dd = await fetchJSON('/api/dd/' + encodeURIComponent(cbsa), { timeoutMs: 30000 }); }
    catch (e) { box.innerHTML = '<p class="msg">Component series unavailable.</p>'; return; }
    const years = (dd.pop_years || []).slice(1);   // first year has no change
    if (!years.length || !window.Charts) { box.innerHTML = '<p class="msg">No component series for this metro.</p>'; return; }
    box.innerHTML = Charts.divergingBars(
      years.map((r) => ({ label: String(r.year), values: r })),
      [{ key: 'nat', label: 'Natural increase', cls: 'series-a' },
       { key: 'dom', label: 'Domestic migration', cls: 'series-b' },
       { key: 'intl', label: 'International migration', cls: 'series-c' }],
      { title: 'Components of population change' })
      + `<div class="note">Which of the three carries the growth usually tells you more than the
         total does — a headline resting on international migration depends entirely on that flow
         continuing.</div>`;
  }

  // Emphasis, not a palette: this metro against all the others. Colouring 469
  // points by anything would bury the one the reader came for.
  let ALL_METROS = null;
  async function drawTightness(cbsa) {
    const box = el('m-scatter');
    if (!box) return;
    try {
      if (!ALL_METROS) ALL_METROS = (await fetchJSON('/api/metros?limit=1000&min_pop=0&metro_only=true')).rows;
    } catch (e) { box.innerHTML = '<p class="msg">Metro set unavailable.</p>'; return; }
    const pts = ALL_METROS
      .filter((r) => r.permits_per_1k != null && r.net_mig_total_rate != null)
      .map((r) => ({ x: r.permits_per_1k, y: r.net_mig_total_rate,
                     label: r.name, highlight: r.cbsa === cbsa }));
    if (!pts.length || !window.Charts) { box.innerHTML = '<p class="msg">No metros to plot.</p>'; return; }
    box.innerHTML = Charts.scatter(pts, {
      title: 'Migration against permitting, all metros',
      xLabel: 'Permits per 1,000 residents', yLabel: 'Net migration per 1,000',
      formatX: (v) => v.toFixed(0), formatY: (v) => v.toFixed(0) })
      + `<div class="note">Up and to the left is demand arriving faster than supply answers it.
         This is the composite the screener calls <i>demand vs supply</i>, drawn rather than
         scored — so you can disagree with a position instead of a number.</div>`;
  }

  async function openMarket(cbsa) {
    const box = el('t-detail');
    el('t-drawer').hidden = false;
    el('t-scrim').hidden = false;
    el('t-drawer').scrollTop = 0;
    box.innerHTML = '<p class="msg">Loading…</p>';

    let d, naics;
    try {
      [d, naics] = await Promise.all([
        fetchJSON('/api/metro/' + encodeURIComponent(cbsa)),
        fetchJSON('/api/naics'),
      ]);
    } catch (e) { box.innerHTML = '<p class="msg">Could not load this market.</p>'; return; }
    const m = d.market;

    const cells = [
      [fmt(m.population), 'Population'],
      [signed(m.net_mig_total_rate), 'Net migration /1k'],
      [usd(m.inflow_agi_per_return), "Arrivals' AGI"],
      [usd(m.outflow_agi_per_return), "Departures' AGI"],
      [signed(m.agi_premium_pct, 0) + '%', 'AGI premium'],
      [(m.permits_per_1k ?? 0).toFixed(1), 'Permits /1k'],
      [usd(m.zhvi), 'Median home value'],
      [signed(m.zhvi_3yr, 0) + '%', '3-yr price'],
      [usd(m.avg_annual_pay), 'Avg annual pay'],
      [(m.price_to_income ?? 0).toFixed(1) + 'x', 'Price to pay'],
      [esc(m.stage), 'Stage'],
      [(m.score ?? 0).toFixed(1), 'Score'],
    ];
    const drivers = [['Migration pull', m.score_demand], ["In-migrant income", m.score_income],
      ['Affordability', m.score_affordability], ['Jobs', m.score_jobs], ['Headroom', m.score_headroom]];
    const origins = d.origins.length
      ? d.origins.map((o) => `<tr><td>${esc(o.name)}</td><td class="n">${fmt(o.returns)}</td>
          <td class="n">${usd(o.avg_agi)}</td></tr>`).join('')
      : '<tr><td colspan="3" class="dim">No origin data.</td></tr>';
    const inds = d.industries.slice(0, 10).map((i) => `<tr><td>${esc(i.industry)}</td>
      <td class="n">${fmt(i.employment)}</td><td class="n">${usd(i.avg_pay)}</td></tr>`).join('');
    const counties = d.counties.map((c) => `<tr><td>${esc(c.name)}</td><td class="dim">${esc(c.state)}</td>
      <td class="n">${fmt(c.population)}</td><td class="n">${fmt(c.domestic_mig)}</td>
      <td class="n">${fmt(c.international_mig)}</td></tr>`).join('');

    box.innerHTML = `
      <h2>${esc(m.name)}</h2>
      <p class="sub">${esc(m.states || '')} · ${m.counties} counties ·
        <span class="mono">CBSA ${esc(m.cbsa)}</span> · <span class="stage ${esc(m.stage)}">${esc(m.stage)}</span></p>
      <p><button type="button" class="primary-btn" id="dd-open">Full due diligence report</button></p>
      <div class="cells">${cells.map(([v, l]) => `<div><b>${v}</b><span>${l}</span></div>`).join('')}</div>
      <div class="note"><b>Why this stage.</b> A market is <i>Early</i> when the demand drivers are firing but supply
        and price have not responded yet. Headroom is the inverse of permits-per-capita and 3-year price growth — an
        Established market scores just as well on drivers, it has simply already been bid up.</div>

      <h3>What is driving the population</h3>
      <div id="m-components"><p class="msg">Loading components…</p></div>

      <h3>Demand against supply</h3>
      <div id="m-scatter"><p class="msg">Loading metros…</p></div>

      <h3>Driver scores</h3>
      <table class="mini"><tbody>${drivers.map(([l, v]) =>
        `<tr><td style="width:44%">${l}</td><td>${bar(v)}</td></tr>`).join('')}</tbody></table>

      <h3>Where people come from, and where they go</h3>
      <div id="m-flows"><p class="msg">Loading corridors…</p></div>

      <h3>Where arrivals come from</h3>
      <table class="mini"><thead><tr><th>Origin market</th><th class="n">Households/yr</th>
        <th class="n">Avg AGI</th></tr></thead><tbody>${origins}</tbody></table>

      <h3>Job announcement → housing demand</h3>
      <div class="form">
        <label>Announced jobs <input type="number" id="j-jobs" value="1000" min="1" step="50" /></label>
        <label>Industry <select id="j-naics">${naics.map((n) =>
          `<option value="${esc(n.naics)}">${esc(n.label)} (×${n.multiplier})</option>`).join('')}</select></label>
        <label>Stated avg pay <input type="number" id="j-pay" placeholder="from BLS if blank" /></label>
      </div>
      <div class="actions"><button class="primary-btn solid" id="j-run">Estimate</button></div>
      <div id="j-out"></div>

      <h3>Largest industries</h3>
      <table class="mini"><thead><tr><th>Industry</th><th class="n">Employment</th>
        <th class="n">Avg pay</th></tr></thead><tbody>${inds}</tbody></table>

      <h3>Counties</h3>
      <table class="mini"><thead><tr><th>County</th><th>State</th><th class="n">Population</th>
        <th class="n">Domestic mig</th><th class="n">Intl mig</th></tr></thead><tbody>${counties}</tbody></table>

      <div class="note">Sources: Census population estimates (components of change); IRS SOI county-to-county migration
        with AGI; Census Building Permits Survey; Zillow ZHVI; BLS QCEW. Multiplier model per Moretti (2010).</div>`;

    el('dd-open').addEventListener('click', () => ddReport(cbsa));
    drawComponents(cbsa);
    drawTightness(cbsa);
    drawFlows(cbsa);

    el('j-run').addEventListener('click', async () => {
      const r = await fetch('/api/jobs-impact', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cbsa, naics: el('j-naics').value, jobs: +el('j-jobs').value,
          avg_pay: el('j-pay').value ? +el('j-pay').value : null,
        }),
      });
      if (!r.ok) { el('j-out').innerHTML = '<div class="note warn">Could not estimate.</div>'; return; }
      const j = await r.json();
      el('j-out').innerHTML = `<div class="cells">
        <div><b>${fmt(j.direct_jobs)}</b><span>Direct jobs</span></div>
        <div><b>${fmt(j.induced_jobs)}</b><span>Induced (×${j.multiplier})</span></div>
        <div><b>${fmt(j.total_jobs)}</b><span>Total jobs</span></div>
        <div><b>${usd(j.direct_avg_pay)}</b><span>Direct avg pay</span></div>
        <div><b>${usd(j.total_payroll)}</b><span>New payroll</span></div>
        <div><b>${fmt(j.households)}</b><span>Households</span></div>
        <div><b>${fmt(j.housing_units)}</b><span>Housing units</span></div>
        <div><b>${usd(j.avg_household_income)}</b><span>Avg hh income</span></div>
      </div><div class="note"><b>Pay basis:</b> ${esc(j.direct_pay_basis)}.<br>${j.notes.map(esc).join('<br>')}</div>`;
    });
  }

  // ── deep link: #mode=records ──────────────────────────────────────────
  const m = /(?:^|[#&])mode=(records|markets|reference)/.exec(location.hash);
  if (m) setMode(m[1]);
})();
