// South Florida Zoning Map — MapLibre frontend.
// Multi-county (Miami-Dade + Broward), with Live Local height tool + full PA record.

const US_CENTER = [-98.5, 39.5]; // CONUS
const FL_CENTER = [-82.4, 28.1]; // statewide Florida view
const TRICOUNTY = [-80.21, 25.95]; // tri-county centroid (Miami-Dade/Broward border)

// General LU type -> color (Miami-Dade)
const ZONING_PALETTE = {
  'Single Family':   '#fff2b3',
  'Duplex':          '#ffe28a',
  'Multi-Family':    '#f7a76c',
  'Estate':          '#fbe7c9',
  'Commercial':      '#e36a6a',
  'Office':          '#c97ad4',
  'Industrial':      '#8a92a8',
  'Mixed Use':       '#b97cb3',
  'Institutional':   '#7da1cf',
  'Agriculture':     '#c8e08a',
  'Recreation':      '#9bd29b',
  'Conservation':    '#5fa07a',
  'Transportation':  '#bdbdbd',
  'Utility':         '#8c8c8c',
  'Other':           '#d6d9df',
};
const ZONING_DEFAULT = '#d6d9df';

// ZoLa-style zoning category colors for the live overlay (colored by family).
const ZONE_CAT_COLORS = {
  residential:  '#F6E58D',
  commercial:   '#E8788A',
  industrial:   '#B79EE8',
  office:       '#7FB3D5',
  mixed:        '#F0A868',
  downtown:     '#C85C8E',
  open:         '#A4D88C',
  agricultural: '#D9C9A3',
  special:      '#AEB6BF',
  other:        '#CFD4DA',
};
const ZONE_CAT_LABELS = {
  residential: 'Residential', commercial: 'Commercial', industrial: 'Industrial',
  office: 'Office', mixed: 'Mixed-use', downtown: 'Downtown / core',
  open: 'Open space / parks', agricultural: 'Agricultural', special: 'Planned / special',
  other: 'Other',
};

const FLOOD_PALETTE = {
  AE:  { color: '#3b82f6', label: 'AE — 1% annual chance, with BFE' },
  AH:  { color: '#60a5fa', label: 'AH — shallow flooding' },
  AO:  { color: '#93c5fd', label: 'AO — sheet flow' },
  A:   { color: '#2563eb', label: 'A — 1% annual chance, no BFE' },
  VE:  { color: '#7c3aed', label: 'VE — coastal high hazard, with BFE' },
  V:   { color: '#6d28d9', label: 'V — coastal high hazard' },
  X:   { color: '#fde68a', label: 'X (shaded) — 0.2% annual chance' },
};
const FLOOD_DEFAULT = '#cbd5e1';
const SFHA_CODES = ['A', 'AE', 'AH', 'AO', 'V', 'VE'];

// Basemaps. Keyless, and deliberately all from one provider.
//
// CARTO's raster endpoints began requiring an API key and started serving tiles
// watermarked "API KEY REQUIRED" -- which is a 200 response, so nothing errors,
// nothing retries, and the map just quietly renders a nag screen. Esri's ArcGIS
// Online tile services need no key and the satellite layer here already used
// them, so that host was the one already proven to work in this app.
//
// `labels` is a separate reference overlay. Esri's Light Gray Base and World
// Imagery carry no place names at all -- they are designed to sit under one --
// so a basemap without this entry renders an unlabelled map, not a subtly
// different one.
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';

// Glyphs, and the one font stack every symbol layer asks for. MapLibre's default
// stack is ["Open Sans Regular","Arial Unicode MS Regular"], and it requests the
// whole comma-joined stack as one path — so a host that does not carry BOTH
// faces 404s and the layer disappears with no error anyone sees. Naming a single
// face the host is known to serve removes that failure mode.
const GLYPHS = 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf';
const FONT = ['Open Sans Regular'];
// Attribution differs by service and is a licence condition, not decoration.
const ESRI_VECTOR_ATTR =
  'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community';
const ESRI_IMAGERY_ATTR =
  'Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics, and the GIS user community';

const BASEMAPS = {
  voyager: {
    type: 'raster',
    tiles: [`${ESRI}/World_Street_Map/MapServer/tile/{z}/{y}/{x}`],
    tileSize: 256,
    attribution: ESRI_VECTOR_ATTR,
  },
  satellite: {
    type: 'raster',
    tiles: [`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`],
    tileSize: 256,
    attribution: ESRI_IMAGERY_ATTR,
    labels: [`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`],
  },
  light: {
    type: 'raster',
    tiles: [`${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
    tileSize: 256,
    attribution: ESRI_VECTOR_ATTR,
    labels: [`${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`],
  },
};

// ---------- hash ----------
function parseHash() {
  const h = window.location.hash.replace(/^#/, '');
  if (!h) return {};
  const p = new URLSearchParams(h);
  const z = parseFloat(p.get('z')), lat = parseFloat(p.get('lat')), lon = parseFloat(p.get('lon'));
  const sel = p.get('sel');
  // Pitch and bearing, so a tilted view is shareable. Without them a link to a
  // massing view arrives flat, which is the one thing it was sent to show.
  const cam = (window.Zoning3D ? window.Zoning3D.parseCamera(p) : { pitch: null, bearing: null });
  return {
    center: Number.isFinite(lat) && Number.isFinite(lon) ? [lon, lat] : null,
    zoom: Number.isFinite(z) ? z : null,
    selection: sel ? sel.split(',').map(Number) : null,
    basemap: p.get('bm'),
    pitch: cam.pitch,
    bearing: cam.bearing,
  };
}
const initial = parseHash();

// ---------- map ----------
const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    // Without a glyphs endpoint every symbol (text) layer is silently rejected —
    // stop names, city labels and target labels all depend on this. It was on
    // CARTO, the same vendor that started gating the raster tiles; this one is
    // the OpenMapTiles font CDN, which is keyless by design and is what most
    // open styles point at. FONT below pins the exact stack it serves.
    glyphs: GLYPHS,
    sources: { basemap: BASEMAPS.light },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  },
  center: initial.center || US_CENTER,
  zoom: initial.zoom || 4.2,
  pitch: initial.pitch ?? 0,
  bearing: initial.bearing ?? 0,
  maxZoom: 19,
});
// showCompass:false left no affordance to rotate or tilt at all.
map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right');

// The canvas is sized once at construction. If the app starts in a container the
// browser has not laid out yet — a hidden pane, a background tab, a collapsed
// split — that size is wrong and nothing ever corrects it, so the map stays
// blank after the container appears. Watching the element covers that, and the
// mode switch that moves the map between the shell and the records pane.
if (window.ResizeObserver) {
  new ResizeObserver(() => map.resize()).observe(document.getElementById('map'));
}

// Basemap and glyph failures used to be invisible. A missing glyph endpoint
// drops every text layer with nothing in the UI to say so, and a gated tile
// server can return a perfectly valid 200 carrying a watermark that says API KEY
// REQUIRED -- which no error handler will ever see, because nothing failed.
//
// This catches the half that does raise, and names which half it was, so the
// next time a provider changes its terms it takes minutes to diagnose instead of
// a squint at the map.
let mapWarned = null;
function warnMap(kind, detail) {
  if (mapWarned === kind) return;
  mapWarned = kind;
  let el = document.getElementById('map-warning');
  if (!el) {
    el = document.createElement('div');
    el.id = 'map-warning';
    el.className = 'map-warning';
    document.getElementById('map').appendChild(el);
  }
  const what = kind === 'glyphs'
    ? 'Map labels are unavailable — the font server did not answer, so every text layer is hidden.'
    : 'Basemap tiles are not loading.';
  el.innerHTML = `<b>${what}</b> <span>${detail}</span>
    <button type="button" aria-label="Dismiss">&times;</button>`;
  el.querySelector('button').addEventListener('click', () => el.remove());
}

map.on('error', (e) => {
  const url = (e && e.error && e.error.url) || '';
  if (!url) return;
  if (url.includes('{fontstack}') || /\/fonts?\//.test(url)) {
    warnMap('glyphs', 'Set GLYPHS in app.js to another keyless font host.');
  } else if (url.includes('/MapServer/tile/')) {
    warnMap('tiles', 'Try another basemap, or change BASEMAPS in app.js.');
  }
});

let activeBasemap = 'light';
function setBasemap(key) {
  if (!BASEMAPS[key] || key === activeBasemap) return;
  // Swap the basemap tiles in place (no setStyle → custom layers untouched, and it
  // can't crash on a not-yet-loaded style the way getStyle()/setStyle() can).
  const src = map.getSource('basemap');
  if (src && src.setTiles) src.setTiles(BASEMAPS[key].tiles);
  // Swap the label overlay to match, and hide it for a basemap that has its own
  // names baked in — two sets of labels on one map is worse than none.
  const labels = BASEMAPS[key].labels;
  if (map.getLayer('basemap-labels')) {
    const lsrc = map.getSource('basemap-labels');
    if (labels && lsrc && lsrc.setTiles) lsrc.setTiles(labels);
    map.setLayoutProperty('basemap-labels', 'visibility', labels ? 'visible' : 'none');
  }
  activeBasemap = key;
  // Scoped to the basemap segment: `.seg-btn` is also the broker type filter,
  // which would otherwise lose its active state every time the basemap changed.
  for (const b of document.querySelectorAll('#basemap-seg .seg-btn')) {
    b.classList.toggle('active', b.dataset.basemap === key);
  }
  updateHash();
}

// ---------- helpers ----------
function colorExpression() {
  const m = ['match', ['get', 'GENRLLUTYPE']];
  for (const [k, v] of Object.entries(ZONING_PALETTE)) m.push(k, v);
  m.push(ZONING_DEFAULT);
  return m;
}
function floodColorExpression(field) {
  const m = ['match', ['get', field]];
  for (const [k, v] of Object.entries(FLOOD_PALETTE)) m.push(k, v.color);
  m.push(FLOOD_DEFAULT);
  return m;
}
function fmtNum(n, opts = {}) {
  if (n == null || n === '') return '—';
  const num = Number(n);
  if (!Number.isFinite(num)) return String(n);
  return num.toLocaleString('en-US', opts);
}
const fmtUSD = (n) => n == null || n === 0 ? '—' : '$' + fmtNum(Math.round(n));
function fmtFeet(ft) {
  if (ft == null) return '—';
  if (ft < 5280) return `${fmtNum(ft)} ft`;
  return `${(ft / 5280).toFixed(2)} mi`;
}
function field(k, v) { return `<div class="field"><div class="k">${k}</div><div class="v">${v ?? '—'}</div></div>`; }

// ---------- tax collector deep links ----------
// Most FL tax collectors run on TaxSys (county-taxes.com) — per-property bill pages
// with actual amounts paid by year. Verified live (403-behind-Cloudflare = exists;
// blocks server scraping but opens fine in a normal browser).
const TAXSYS_SUBDOMAINS = {
  'miami-dade': 'miamidade', 'broward': 'broward', 'lee': 'lee', 'pinellas': 'pinellas',
  'hillsborough': 'hillsborough', 'orange': 'orange', 'duval': 'duval', 'collier': 'collier',
  'alachua': 'alachua', 'leon': 'leon', 'st-lucie': 'stlucie', 'osceola': 'osceola',
  'brevard': 'brevard', 'volusia': 'volusia', 'escambia': 'escambia', 'sarasota': 'sarasota',
  'manatee': 'manatee', 'pasco': 'pasco', 'seminole': 'seminole',
};
const TC_CUSTOM_SITES = {
  'palm-beach': 'https://pbctax.gov',
  'polk': 'https://polktaxes.com',
  'marion': 'https://www.mariontax.com',
};
function taxBillUrl(county, parcelId) {
  const key = (county || '').toLowerCase().replace(/\./g, '').replace(/\s+/g, '-');
  const sub = TAXSYS_SUBDOMAINS[key];
  if (sub) {
    const q = parcelId ? `?search_query=${encodeURIComponent(parcelId)}` : '';
    return `https://${sub}.county-taxes.com/public/search${q}`;
  }
  return TC_CUSTOM_SITES[key] || null;
}
function taxBillLink(county, parcelId) {
  const url = taxBillUrl(county, parcelId);
  if (!url) return '';
  return `<a href="${url}" target="_blank" rel="noopener">View official tax bills (paid by year) →</a>`;
}

// ---------- Sunbiz owner-entity lookup ----------
const ENTITY_RX = /\b(LLC|L\.?L\.?C|INC|CORP|CO|LTD|LP|LLLP|LLP|PLLC|TRUST|HOLDINGS|PARTNERS|GROUP|PROPERTIES|INVESTMENTS|REALTY|VENTURES|CAPITAL|ENTERPRISES|ASSOCIATES)\b/i;

async function loadEntity(owner, state, seq) {
  const panel = document.getElementById('entity-panel');
  const body = document.getElementById('entity-body');
  panel.hidden = false;
  panel.open = true;
  const reg = state === 'FL' ? 'Florida Sunbiz' : (state ? state + ' / OpenCorporates' : 'corporate registries');
  body.innerHTML = `<p style="color:var(--text-muted); font-size:12px">Looking up <b>${esc(owner)}</b> in ${esc(reg)}…</p>`;
  try {
    const qs = `name=${encodeURIComponent(owner)}${state ? '&state=' + state : ''}`;
    const e = await fetchJSON(`/api/entity?${qs}`, { timeoutMs: 20000 });
    if (seq != null && seq !== lookupSeq) return;  // stale — a newer parcel lookup owns the panel
    if (e.found) { body.innerHTML = renderEntity(e); return; }
    // Not found / needs key — still offer the free OpenCorporates deep link.
    let html = `<p style="color:var(--text-muted); font-size:12px">${esc(e.message || 'No structured match.')}</p>`;
    if (e.oc_url && safeUrl(e.oc_url)) html += `<div class="tax-link" style="margin-top:8px"><a href="${safeUrl(e.oc_url)}" target="_blank" rel="noopener">Search ${esc(owner)} on OpenCorporates →</a></div>`;
    body.innerHTML = html;
  } catch (err) {
    if (seq != null && seq !== lookupSeq) return;
    body.innerHTML = `<p style="color:var(--danger); font-size:12px">Entity lookup failed: ${esc(err.message)}</p>`;
  }
}

function renderEntity(e) {
  const p = [];
  const statusColor = /ACTIVE/i.test(e.status || '') ? 'var(--good)' : 'var(--danger)';
  p.push(`<div style="font-weight:600; font-size:13px; margin-bottom:2px">${esc(e.name || e.query)}</div>`);
  p.push(`<div style="font-size:11px; color:var(--text-muted); margin-bottom:8px">
    <span style="color:${statusColor}; font-weight:600">${esc(e.status || '—')}</span>
    ${e.entity_type ? ' · ' + esc(e.entity_type) : ''} ${e.date_filed ? ' · filed ' + esc(e.date_filed) : ''}</div>`);
  if (e.document_number) p.push(field('Doc #', esc(e.document_number)));
  if (e.fei_ein) p.push(field('FEI/EIN', esc(e.fei_ein)));
  if (e.principal_address) p.push(field('Principal', esc(e.principal_address)));
  if (e.mailing_address && e.mailing_address !== e.principal_address) p.push(field('Mailing', esc(e.mailing_address)));
  const isFL = !e.source || /sunbiz/i.test(e.source || '') || e.sunbiz_url;
  const partyLink = (nm) => isFL
    ? `<span class="party-link" data-name="${escAttr(nm)}" title="Find connected entities & properties">${esc(nm)}</span>`
    : esc(nm);
  if (e.registered_agent && e.registered_agent.name) {
    p.push(`<div class="section-title">Registered agent</div>`);
    p.push(`<div style="font-size:12px"><b>${partyLink(e.registered_agent.name)}</b><br><span style="color:var(--text-muted); font-size:11px">${esc(e.registered_agent.address || '')}</span></div>`);
  }
  if (e.officers && e.officers.length) {
    p.push(`<div class="section-title">Managers / officers (${e.officers.length})</div>`);
    for (const o of e.officers) {
      p.push(`<div class="comp"><div class="row1"><span class="addr">${partyLink(o.name)}</span><span class="dist">${esc(o.title || '')}</span></div>${o.address ? `<div class="row2">${esc(o.address)}</div>` : ''}</div>`);
    }
  }
  if (isFL) p.push(`<div class="party-cta-row"><button class="link-btn" data-name="${escAttr(e.name || e.query)}">↳ Find all properties &amp; entities tied to ${esc(e.name || e.query)}</button></div>`);
  if (e.source) p.push(`<div style="font-size:10.5px; color:var(--text-dim); margin-top:6px">Source: ${esc(e.source)}</div>`);
  if (e.sunbiz_url && safeUrl(e.sunbiz_url)) p.push(`<div class="tax-link" style="margin-top:8px"><a href="${safeUrl(e.sunbiz_url)}" target="_blank" rel="noopener">Full Sunbiz record →</a></div>`);
  else if (e.oc_url && safeUrl(e.oc_url)) p.push(`<div class="tax-link" style="margin-top:8px"><a href="${safeUrl(e.oc_url)}" target="_blank" rel="noopener">Full record on OpenCorporates →</a></div>`);
  return p.join('');
}

function escAttr(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// HTML-escape untrusted text before interpolating into innerHTML. Upstream/public-
// record strings (owner names, addresses, permit scopes, deed parties, zoning
// descriptions…) are attacker-influenceable and must never render as markup.
function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Only http(s) URLs may reach an href/window.open — blocks javascript: and
// attribute-breakout payloads from scraped/upstream data. Returns '' otherwise.
function safeUrl(u) {
  const s = String(u == null ? '' : u);
  return /^https?:\/\//i.test(s) ? escAttr(s) : '';
}

// fetch wrapper for all API calls: hard timeout, throws on HTTP error statuses
// (surfacing FastAPI's {"detail": ...} body), parses JSON.
async function fetchJSON(url, opts = {}) {
  const { timeoutMs = 15000, signal, ...init } = opts;
  // Combine the caller's abort signal (e.g. "a newer viewport superseded this")
  // with the timeout so a stale in-flight request is cancelled, not just ignored.
  const timeoutSig = AbortSignal.timeout(timeoutMs);
  const sig = signal && AbortSignal.any ? AbortSignal.any([timeoutSig, signal]) : (signal || timeoutSig);
  const r = await fetch(url, { ...init, signal: sig });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const b = await r.json();
      if (b && b.detail) msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail);
    } catch (e) { /* non-JSON error body */ }
    throw new Error(msg);
  }
  return r.json();
}

// ---------- owner / connected-party search (FL: Sunbiz + parcels) ----------
// Pivot from any name (person or LLC) to: every entity it's an officer/agent of,
// and every parcel it owns. Clicking an entity walks the graph; clicking a
// property flies there and pulls its record.
async function searchParty(name, state) {
  const st = state || currentPlaceState || 'FL';
  const panel = document.getElementById('party-panel');
  const body = document.getElementById('party-body');
  const input = document.getElementById('party-input');
  if (input) input.value = name;
  panel.hidden = false; panel.open = true;
  body.innerHTML = `<p style="color:var(--text-muted);font-size:12px">Searching <b>${esc(name)}</b> across ${st === 'FL' ? 'Sunbiz + FL property records' : esc(st) + ' registries'}…</p>`;
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const qs = `name=${encodeURIComponent(name)}&state=${st}`;
  const [ents, owned] = await Promise.all([
    fetchJSON(`/api/related-entities?${qs}`, { timeoutMs: 25000 }).catch(() => ({})),
    (st === 'FL' ? fetchJSON(`/api/owner-properties?name=${encodeURIComponent(name)}`, { timeoutMs: 40000 }).catch(() => ({})) : Promise.resolve({})),
  ]);
  renderParty(name, st, ents, owned);
}

function renderParty(name, st, ents, owned) {
  const body = document.getElementById('party-body');
  const p = [`<div style="font-weight:600;font-size:13px;margin-bottom:6px">${esc(name)}</div>`];

  // Properties owned (FL)
  const props = (owned && owned.properties) || [];
  if (props.length) {
    p.push(`<div class="section-title">Properties owned (${owned.count})</div>`);
    for (const pr of props) {
      p.push(`<div class="comp party-prop" data-lon="${escAttr(pr.lon)}" data-lat="${escAttr(pr.lat)}">
        <div class="row1"><span class="addr">${esc(pr.address || pr.folio || '—')}</span><span class="dist">${esc(pr.city || '')}</span></div>
        <div class="row2">${esc(pr.owner || '')}${pr.source ? ' · ' + esc(pr.source) : ''}</div></div>`);
    }
  } else if (st === 'FL') {
    p.push(`<div class="section-title">Properties owned</div><p style="color:var(--text-muted);font-size:11.5px">No FL parcels found under this exact name. Try the entity's exact legal name.</p>`);
  }

  // Connected entities (officer / registered agent)
  const list = (ents && ents.entities) || [];
  if (list.length) {
    p.push(`<div class="section-title">Entities — officer / registered agent (${ents.count})</div>`);
    for (const e of list) {
      p.push(`<div class="comp party-entity" data-name="${escAttr(e.entity)}">
        <div class="row1"><span class="addr">${esc(e.entity)}</span>${e.doc ? `<span class="dist">${esc(e.doc)}</span>` : ''}</div>
        <div class="row2">as ${esc(e.officer || 'officer/agent')} · ↳ expand</div></div>`);
    }
    if (ents.search_url && safeUrl(ents.search_url)) p.push(`<div class="tax-link" style="margin-top:8px"><a href="${safeUrl(ents.search_url)}" target="_blank" rel="noopener">Full Sunbiz officer search →</a></div>`);
  } else if (st === 'FL') {
    p.push(`<div class="section-title">Connected entities</div><p style="color:var(--text-muted);font-size:11.5px">No Sunbiz officer/agent matches for this name.</p>`);
  } else if (ents && ents.oc_url && safeUrl(ents.oc_url)) {
    p.push(`<div class="tax-link" style="margin-top:8px"><a href="${safeUrl(ents.oc_url)}" target="_blank" rel="noopener">Officer search for ${esc(name)} on OpenCorporates →</a></div>`);
  }

  body.innerHTML = p.join('');

  // Map the owned parcels as purple party pins
  const feats = props.filter((pr) => pr.lon != null).map((pr) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [pr.lon, pr.lat] },
    properties: { label: pr.address || pr.folio || name },
  }));
  map.getSource('party_props')?.setData({ type: 'FeatureCollection', features: feats });
  if (feats.length) {
    const lons = feats.map((f) => f.geometry.coordinates[0]); const lats = feats.map((f) => f.geometry.coordinates[1]);
    if (feats.length === 1) map.flyTo({ center: feats[0].geometry.coordinates, zoom: 15 });
    else map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 80, maxZoom: 14 });
  }
}

function wirePartySearch() {
  const input = document.getElementById('party-input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && input.value.trim().length >= 3) searchParty(input.value.trim());
    });
  }
  // Event delegation: pivot from any clickable name/entity/property.
  document.addEventListener('click', (e) => {
    const link = e.target.closest('.party-link, .link-btn[data-name]');
    if (link) { searchParty(link.dataset.name); return; }
    const ent = e.target.closest('.party-entity');
    if (ent) { loadEntity(ent.dataset.name, 'FL'); return; }
    const prop = e.target.closest('.party-prop');
    if (prop) {
      const lon = parseFloat(prop.dataset.lon), lat = parseFloat(prop.dataset.lat);
      if (Number.isFinite(lon) && Number.isFinite(lat)) {
        map.flyTo({ center: [lon, lat], zoom: 17, speed: 1.4 });
        doParcelLookup(lon, lat);
      }
    }
  });
  // hover popup for owned-parcel pins
  let pp = null;
  map.on('mouseenter', 'party-props-pts', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    pp = new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(e.features[0].properties.label || '')}</b>`).addTo(map);
  });
  map.on('mouseleave', 'party-props-pts', () => { map.getCanvas().style.cursor = ''; pp && pp.remove(); pp = null; });
  map.on('click', 'party-props-pts', (e) => {
    doParcelLookup(e.lngLat.lng, e.lngLat.lat);
  });
  // hover popup for competing-supply pins
  let sp = null;
  map.on('mouseenter', 'supply-pipeline-pts', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    sp = new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.scope || 'New construction')}</b>${p.value ? '<br>' + fmtUSD(p.value) : ''}${p.address ? '<br><span style="color:#64748b">' + esc(p.address) + '</span>' : ''}`).addTo(map);
  });
  map.on('mouseleave', 'supply-pipeline-pts', () => { map.getCanvas().style.cursor = ''; sp && sp.remove(); sp = null; });
  map.on('click', 'supply-pipeline-pts', (e) => {
    doParcelLookup(e.lngLat.lng, e.lngLat.lat);
  });
}

// Some county/state parcel fabrics store one assessor ID as multiple separate
// polygon rows (an assembled development site keeps each original plat lot's
// boundary as its own map feature while the tax roll treats them as one
// folio) — a click only ever lands in ONE row, so the drawn outline can show a
// fragment of the real property. The check for this is a WHERE-by-ID query
// that's fast on a warm cache but has been observed up to ~14s cold, so it
// runs here, after the parcel panel is already rendered, and silently
// upgrades the outline if a merge is found — never blocks the initial click.
async function upgradeSelectionOutline(parcel, seq) {
  if (!parcel.folio) return;
  const params = new URLSearchParams({ folio: parcel.folio });
  if (parcel.county) params.set('county', parcel.county);
  if (parcel.state) params.set('state', parcel.state);
  if (parcel.source) params.set('source', parcel.source);
  // Cold upstream checks can run many seconds (warm/disk-cached ones are
  // instant) — tell the user something is still happening, but only once the
  // wait is actually noticeable, so cached clicks stay silent.
  const slowNotice = setTimeout(() => {
    if (seq === lookupSeq) toast('Checking for adjacent lots on this folio…');
  }, 1500);
  try {
    const r = await fetchJSON(`/api/parcel-geometry?${params}`, { timeoutMs: 20000 });
    if (seq !== lookupSeq) return;  // a newer click already owns the selection outline
    if (r.merged && r.geometry) {
      map.getSource('selection')?.setData({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: r.geometry, properties: {} }],
      });
      toast('Folio spans multiple lots — outline updated to the full parcel');
    }
  } catch (e) { /* best-effort — the original single-fragment outline stays */ }
  finally { clearTimeout(slowNotice); }
}

// ---------- building permits (Miami-Dade) ----------
async function loadPermits(arg, seq) {
  const panel = document.getElementById('permits-panel');
  const body = document.getElementById('permits-body');
  panel.hidden = false;
  body.innerHTML = `<p style="color:var(--text-muted); font-size:12px">Loading building permits…</p>`;
  const qs = arg.folio ? `folio=${encodeURIComponent(arg.folio)}` : `lon=${arg.lon}&lat=${arg.lat}`;
  try {
    const r = await fetchJSON(`/api/permits?${qs}`, { timeoutMs: 20000 });
    if (seq != null && seq !== lookupSeq) return;  // stale — a newer lookup owns this panel
    if (!r.permits || !r.permits.length) {
      if (!arg.folio && arg.lon != null && (r.available === false)) { panel.hidden = true; return; }  // no service here — hide quietly
      body.innerHTML = `<p style="color:var(--text-muted); font-size:12px">No building permits on record at this location.</p>`;
      return;
    }
    const where = r.city ? ` · ${r.city}` : '';
    document.querySelector('#permits-panel summary').textContent = `Building permits (${r.count})${where}`;
    // Development-activity header (new-build stats for the surrounding ~0.5 mi)
    if (arg.lon != null || arg.folio) {
      const sp = arg.lon != null ? `lon=${arg.lon}&lat=${arg.lat}` : `lon=${lastSelection.lon}&lat=${lastSelection.lat}`;
      fetchJSON(`/api/build-stats?${sp}`).then((s) => {
        if (seq != null && seq !== lookupSeq) return;
        if (!s || !s.available) return;
        const val = s.new_build_value ? ' · ' + fmtUSD(s.new_build_value) + ' declared' : '';
        body.insertAdjacentHTML('afterbegin',
          `<div class="build-stats">📈 <b>${s.all_permits ?? '—'}</b> permits within ½ mi · <b>${s.new_build_permits ?? '—'}</b> new-construction${val}</div>`);
      }).catch(() => {});
    }
    body.innerHTML = r.permits.map((p) => `
      <div class="comp">
        <div class="row1">
          <span class="addr">${esc(p.type || '')}${p.app_type ? ' · ' + esc(p.app_type) : ''}</span>
          <span class="dist">${esc(p.issued || '')}</span>
        </div>
        <div class="row2">${esc(p.scope || '')}</div>
        <div class="row2">
          ${p.permit_number ? '#' + esc(p.permit_number) : ''}
          ${p.est_value ? ' · ' + fmtUSD(p.est_value) : ''}
          ${p.status ? ' · status ' + esc(p.status) : ''}
          ${p.completed ? ' · CO ' + esc(p.completed) : ''}
        </div>
        ${p.contractor ? `<div class="row2"><b>${esc(p.contractor)}</b></div>` : ''}
      </div>`).join('');
  } catch (e) {
    if (seq != null && seq !== lookupSeq) return;
    body.innerHTML = `<p style="color:var(--danger); font-size:12px">Permit lookup failed: ${esc(e.message)}</p>`;
  }
}

// ---------- deed / official-records (national, via NETROnline directory) ----------
function recorderUrl(state, county) {
  if (!state || !county) return null;
  const slug = String(county)
    .toLowerCase()
    .replace(/\s+county.*$/, '')
    .replace(/\(.*?\)/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  if (!slug) return null;
  return `https://publicrecords.netronline.com/state/${state}/county/${slug}`;
}

// Render transactions + deed link into `parts`. `sales` is [{price,date,or_book,or_page,qual,grantor,grantee}].
function renderTransactions(parts, parcel, state) {
  const sales = parcel.sales || [];
  if (sales.length) {
    const label = sales.length === 1 ? 'Last sale' : `Sales history (${sales.length})`;
    parts.push(`<div class="section-title">${label}</div>`);
    for (const s of sales) {
      const or = (s.or_book && s.or_book !== '') ? ` · OR ${esc(s.or_book)}/${esc(s.or_page)}` : '';
      const parties = s.grantee ? `<div class="row2">${esc((s.grantor || '').slice(0, 32))} → ${esc((s.grantee || '').slice(0, 32))}</div>` : '';
      parts.push(`<div class="comp"><div class="row1"><span class="addr">${esc(s.date || '—')}</span><span class="dist">${fmtUSD(s.price)}</span></div>${parties}${or ? `<div class="row2">${or.replace(' · ', '')}${s.qual ? ' · ' + esc(s.qual) : ''}</div>` : ''}</div>`);
    }
  }
  const recUrl = recorderUrl(state, parcel.county_name);
  if (recUrl) {
    parts.push(`<div class="tax-link" style="margin-top:8px">
      <a href="${recUrl}" target="_blank" rel="noopener">County assessor, recorder &amp; tax records →</a></div>`);
    parts.push(`<div class="tax-hint">Opens the county Recorder/Clerk where the recorded deed (and book/page above) can be pulled.</div>`);
  }
}
// Fetch recorded sale(s) from a county recorder/assessor endpoint (free where
// public) and append to the parcel panel. Used outside FL/NYC, where sales
// aren't already inline. No-op when the county isn't registered.
async function loadTransactions(lon, lat, seq) {
  let tx;
  try {
    tx = await fetchJSON(`/api/transactions?lon=${lon}&lat=${lat}`, { timeoutMs: 20000 });
  } catch (e) { return; }
  if (seq != null && seq !== lookupSeq) return;  // stale — don't append into the new parcel's panel
  if (!tx || (!tx.found && !tx.non_disclosure_note)) return;
  const body = document.getElementById('parcel-body');
  if (!body) return;
  const parts = [`<div class="section-title">Recorded sales — ${esc(tx.county_name || '')}${tx.source ? ` <span class="src-badge">${esc(tx.source)}</span>` : ''}</div>`];
  for (const s of (tx.sales || [])) {
    const parties = (s.grantor || s.grantee)
      ? `<div class="row2">${esc((s.grantor || '?').slice(0, 30))} → ${esc((s.grantee || '?').slice(0, 30))}</div>` : '';
    const or = s.or_book ? `<div class="row2">Doc ${esc(s.or_book)}</div>` : '';
    parts.push(`<div class="comp"><div class="row1"><span class="addr">${esc(s.date || '—')}</span><span class="dist">${s.price ? fmtUSD(s.price) : '—'}</span></div>${parties}${or}</div>`);
  }
  if (tx.non_disclosure_note) {
    parts.push(`<div class="tax-hint" style="color:var(--warn,#b45309)">${esc(tx.non_disclosure_note)}</div>`);
  }
  body.insertAdjacentHTML('beforeend', parts.join(''));
}

function toast(msg) {
  let t = document.getElementById('toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 1800);
}

// ---------- layer wiring ----------

// 'style.load', not 'load'. Nothing below needs a painted frame — it adds
// sources and layers and wires DOM handlers — while 'load' additionally waits
// for the first visually complete render. Firing on style.load gets the layers
// registered as early as they can be, and drops a dependency on paint that this
// block never had a reason to carry.
map.on('style.load', () => {
  // ---- Place-name overlay. Added first so it sits above the basemap raster but
  // below every data layer. Which basemaps need it is a property of the basemap
  // (see BASEMAPS.labels), not a special case for satellite: Esri's Light Gray
  // Base carries no labels either.
  map.addSource('basemap-labels', {
    type: 'raster',
    tiles: BASEMAPS[activeBasemap].labels || BASEMAPS.light.labels,
    tileSize: 256,
    attribution: ESRI_VECTOR_ATTR,
  });
  map.addLayer({ id: 'basemap-labels', type: 'raster', source: 'basemap-labels',
    layout: { visibility: BASEMAPS[activeBasemap].labels ? 'visible' : 'none' } });

  // ---- Miami-Dade Zoning
  map.addSource('mdc_zoning', { type: 'geojson', data: '/data/mdc_zoning.geojson' });
  map.addLayer({ id: 'mdc-zoning-fill', type: 'fill', source: 'mdc_zoning',
    paint: { 'fill-color': colorExpression(), 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 10, 0.45, 15, 0.30] } });
  map.addLayer({ id: 'mdc-zoning-line', type: 'line', source: 'mdc_zoning',
    paint: { 'line-color': '#3a4151', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.15, 15, 0.5], 'line-opacity': 0.45 } });

  // ---- Miami-Dade FLU
  map.addSource('mdc_flu', { type: 'geojson', data: '/data/mdc_flu.geojson' });
  map.addLayer({ id: 'mdc-flu-fill', type: 'fill', source: 'mdc_flu', layout: { visibility: 'none' },
    paint: { 'fill-color': '#7dd3fc', 'fill-opacity': 0.35 } });

  // ---- Broward BMSD Zoning (only unincorporated)
  map.addSource('bro_zoning', { type: 'geojson', data: '/data/bro_zoning_bmsd.geojson' });
  map.addLayer({ id: 'bro-zoning-fill', type: 'fill', source: 'bro_zoning', layout: { visibility: 'none' },
    paint: {
      'fill-color': [
        'match', ['coalesce', ['get', 'BCPA_USE'], 'X'],
        '01', '#fff2b3', '03', '#f7a76c', '04', '#b97cb3', '05', '#c97ad4', '08', '#e36a6a',
        '#d6d9df'
      ],
      'fill-opacity': 0.5,
    } });
  map.addLayer({ id: 'bro-zoning-line', type: 'line', source: 'bro_zoning', layout: { visibility: 'none' },
    paint: { 'line-color': '#3a4151', 'line-width': 0.3, 'line-opacity': 0.5 } });

  // ---- Broward FLU
  map.addSource('bro_flu', { type: 'geojson', data: '/data/bro_flu.geojson' });
  map.addLayer({ id: 'bro-flu-fill', type: 'fill', source: 'bro_flu', layout: { visibility: 'none' },
    paint: { 'fill-color': '#a7f3d0', 'fill-opacity': 0.4 } });

  // ---- Flood: combine Miami-Dade + Broward into 2 layers (different field names)
  map.addSource('mdc_flood', { type: 'geojson', data: '/data/mdc_flood.geojson' });
  map.addLayer({ id: 'mdc-flood-fill', type: 'fill', source: 'mdc_flood', layout: { visibility: 'none' },
    paint: { 'fill-color': floodColorExpression('FZONE'), 'fill-opacity': 0.5 } });
  map.addSource('bro_flood', { type: 'geojson', data: '/data/bro_flood.geojson' });
  map.addLayer({ id: 'bro-flood-fill', type: 'fill', source: 'bro_flood', layout: { visibility: 'none' },
    paint: { 'fill-color': floodColorExpression('FLD_ZONE'), 'fill-opacity': 0.5 } });
  // National FEMA flood (FEMA NFHL) — loaded by viewport so flood color works anywhere
  map.addSource('nfhl_flood', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'nfhl-flood-fill', type: 'fill', source: 'nfhl_flood', layout: { visibility: 'none' },
    paint: { 'fill-color': floodColorExpression('FLD_ZONE'), 'fill-opacity': 0.45 } });
  map.addLayer({ id: 'nfhl-flood-line', type: 'line', source: 'nfhl_flood', layout: { visibility: 'none' },
    paint: { 'line-color': '#1e3a8a', 'line-width': 0.3, 'line-opacity': 0.4 } });

  // ---- Incentive overlays (MDC)
  map.addSource('mdc_oz', { type: 'geojson', data: '/data/mdc_opportunity_zones.geojson' });
  map.addLayer({ id: 'oz-fill', type: 'fill', source: 'mdc_oz', layout: { visibility: 'none' },
    paint: { 'fill-color': '#facc15', 'fill-opacity': 0.28 } });
  map.addLayer({ id: 'oz-line', type: 'line', source: 'mdc_oz', layout: { visibility: 'none' },
    paint: { 'line-color': '#a16207', 'line-width': 1.2 } });

  map.addSource('mdc_cra', { type: 'geojson', data: '/data/mdc_cra.geojson' });
  map.addLayer({ id: 'cra-fill', type: 'fill', source: 'mdc_cra', layout: { visibility: 'none' },
    paint: { 'fill-color': '#a78bfa', 'fill-opacity': 0.3 } });
  map.addLayer({ id: 'cra-line', type: 'line', source: 'mdc_cra', layout: { visibility: 'none' },
    paint: { 'line-color': '#5b21b6', 'line-width': 1.2 } });

  map.addSource('mdc_brownfield', { type: 'geojson', data: '/data/mdc_brownfields.geojson' });
  map.addLayer({ id: 'brownfield-fill', type: 'fill', source: 'mdc_brownfield', layout: { visibility: 'none' },
    paint: { 'fill-color': '#f87171', 'fill-opacity': 0.35 } });

  map.addSource('mdc_enterprise', { type: 'geojson', data: '/data/mdc_enterprise_zones.geojson' });
  map.addLayer({ id: 'enterprise-fill', type: 'fill', source: 'mdc_enterprise', layout: { visibility: 'none' },
    paint: { 'fill-color': '#34d399', 'fill-opacity': 0.25 } });

  // ---- Context
  map.addSource('mdc_historic', { type: 'geojson', data: '/data/mdc_historic_districts.geojson' });
  map.addLayer({ id: 'historic-fill', type: 'fill', source: 'mdc_historic', layout: { visibility: 'none' },
    paint: { 'fill-color': '#d97706', 'fill-opacity': 0.25 } });
  map.addLayer({ id: 'historic-line', type: 'line', source: 'mdc_historic', layout: { visibility: 'none' },
    paint: { 'line-color': '#92400e', 'line-width': 1.5 } });

  map.addSource('mdc_rtz', { type: 'geojson', data: '/data/mdc_rapid_transit_zones.geojson' });
  map.addLayer({ id: 'rtz-fill', type: 'fill', source: 'mdc_rtz', layout: { visibility: 'none' },
    paint: { 'fill-color': '#10b981', 'fill-opacity': 0.2 } });

  map.addSource('mdc_metrorail', { type: 'geojson', data: '/data/mdc_metrorail_stations.geojson' });
  map.addSource('mdc_metromover', { type: 'geojson', data: '/data/mdc_metromover_stations.geojson' });
  map.addLayer({ id: 'metrorail-pts', type: 'circle', source: 'mdc_metrorail',
    paint: { 'circle-radius': 6, 'circle-color': '#dc2626', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
  map.addLayer({ id: 'metromover-pts', type: 'circle', source: 'mdc_metromover',
    paint: { 'circle-radius': 5, 'circle-color': '#f59e0b', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });

  map.addSource('mdc_schools', { type: 'geojson', data: '/data/mdc_schools.geojson' });
  map.addLayer({ id: 'schools-pts', type: 'circle', source: 'mdc_schools', layout: { visibility: 'none' },
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2.5, 16, 6],
             'circle-color': '#0ea5e9', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });
  map.addSource('mdc_parks', { type: 'geojson', data: '/data/mdc_parks.geojson' });
  map.addLayer({ id: 'parks-fill', type: 'fill', source: 'mdc_parks', layout: { visibility: 'none' },
    paint: { 'fill-color': '#86efac', 'fill-opacity': 0.45 } });

  // ---- Modular / prefab factories (national, 329 plants), colored by category
  map.addSource('factories', { type: 'geojson', data: '/data/modular_factories.geojson' });
  map.addLayer({
    id: 'factories-pts', type: 'circle', source: 'factories', layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 3, 10, 7],
      'circle-color': ['match', ['get', 'category'],
        'volumetric_multifamily', '#dc2626',
        'residential_modular', '#ea580c',
        'hud_code', '#f59e0b',
        'panelized', '#7c3aed',
        'adu_prefab', '#0891b2',
        '#6b7280'],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1,
      'circle-opacity': 0.9,
    },
  });

  // ---- Live statewide metro zoning overlay (fetched by viewport)
  // Construction-permit heat map (viewport-refreshed, weighted by permit value)
  map.addSource('permit_heat', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'permit-heat', type: 'heatmap', source: 'permit_heat', layout: { visibility: 'none' },
    paint: {
      'heatmap-weight': ['coalesce', ['get', 'w'], 1],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 16, 1.6],
      'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 12, 16, 30],
      'heatmap-opacity': 0.75,
      'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
        0, 'rgba(0,0,0,0)', 0.2, '#2c7fb8', 0.4, '#7fcdbb', 0.6, '#fed976', 0.8, '#fd8d3c', 1, '#bd0026'],
    } });

  map.addSource('metro_zoning', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'metro-zoning-fill', type: 'fill', source: 'metro_zoning', layout: { visibility: 'none' },
    paint: {
      // Darkened within the family by the storey cap the district code
      // declares, so T6-8 and T6-80 stop painting identically. Features with no
      // declared height keep the flat family colour.
      'fill-color': Zoning3D.fillColorExpression(ZONE_CAT_COLORS),
      'fill-opacity': 0.5,
    } });
  // Massing. This is the ZONING ENVELOPE, not the building: it ignores setbacks,
  // lot coverage and site geometry, exactly as capacity.py warns. Off by
  // default, and raised only where the code states a height -- a zero-height
  // extrusion is an honest "unknown"; an invented one is a lie with a shadow.
  map.addLayer({ id: 'metro-zoning-3d', type: 'fill-extrusion', source: 'metro_zoning',
    layout: { visibility: 'none' }, minzoom: 13,
    paint: {
      'fill-extrusion-color': Zoning3D.fillColorExpression(ZONE_CAT_COLORS),
      'fill-extrusion-height': Zoning3D.extrusionHeightExpression(),
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.72,
    } });
  map.addLayer({ id: 'metro-zoning-line', type: 'line', source: 'metro_zoning', layout: { visibility: 'none' },
    paint: { 'line-color': '#475569', 'line-width': 0.5, 'line-opacity': 0.45 } });

  // ---- National transit routes + stops (BTS National Transit Map), colored by mode.
  // GTFS types: 0 tram, 1 subway, 2 rail, 3 bus, 4 ferry, 5 cable, 6 gondola, 7 funicular,
  // 11 trolleybus, 12 monorail. One shared palette so routes and stops read as one system.
  const TRANSIT_MODE_COLOR = (prop) => ['match', ['to-string', ['get', prop]],
    ['0', '5', '7', '12'], '#16a34a',   // tram / streetcar / monorail — green
    '1', '#dc2626',                     // subway / metro — red
    ['2', '6'], '#7c3aed',              // commuter / intercity rail — purple
    ['3', '11'], '#2563eb',             // bus / trolleybus — blue
    '4', '#0891b2',                     // ferry — teal
    '#6b7280'];
  map.addSource('transit_routes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'transit-routes-line', type: 'line', source: 'transit_routes', layout: { visibility: 'none', 'line-cap': 'round' },
    paint: {
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 1, 15, 3.5],
      'line-opacity': 0.8,
      'line-color': TRANSIT_MODE_COLOR('route_type'),
    } });
  map.addSource('transit_stops', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'transit-stops-pts', type: 'circle', source: 'transit_stops', layout: { visibility: 'none' },
    paint: {
      // rail/ferry stations render bigger than bus stops so the hierarchy is obvious
      'circle-radius': ['interpolate', ['linear'], ['zoom'],
        10, ['case', ['==', ['get', 'is_bus'], 1], 1.6, 3.2],
        16, ['case', ['==', ['get', 'is_bus'], 1], 4, 7]],
      'circle-color': TRANSIT_MODE_COLOR('stop_type'),
      'circle-stroke-color': '#fff',
      'circle-stroke-width': ['case', ['==', ['get', 'is_bus'], 1], 0.8, 1.6],
      'circle-opacity': 0.95,
    } });
  map.addLayer({ id: 'transit-stops-label-rail', type: 'symbol', source: 'transit_stops', minzoom: 10,
    filter: ['!=', ['get', 'is_bus'], 1],
    layout: { visibility: 'none', 'text-font': FONT, 'text-field': ['get', 'stop_name'], 'text-size': 11,
      'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-max-width': 12 },
    paint: { 'text-color': '#0f172a', 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });
  map.addLayer({ id: 'transit-stops-label-bus', type: 'symbol', source: 'transit_stops', minzoom: 14.5,
    filter: ['==', ['get', 'is_bus'], 1],
    layout: { visibility: 'none', 'text-font': FONT, 'text-field': ['get', 'stop_name'], 'text-size': 10,
      'text-offset': [0, 0.9], 'text-anchor': 'top', 'text-max-width': 12 },
    paint: { 'text-color': '#1e3a8a', 'text-halo-color': '#fff', 'text-halo-width': 1.3 } });

  // ---- Boundaries (kept invisible by default)
  map.addSource('mdc_munis', { type: 'geojson', data: '/data/mdc_municipalities.geojson' });
  map.addLayer({ id: 'mdc-munis-line', type: 'line', source: 'mdc_munis', layout: { visibility: 'none' },
    paint: { 'line-color': '#111', 'line-width': 1.4, 'line-opacity': 0.6 } });
  map.addSource('bro_munis', { type: 'geojson', data: '/data/bro_municipalities.geojson' });
  map.addLayer({ id: 'bro-munis-line', type: 'line', source: 'bro_munis', layout: { visibility: 'none' },
    paint: { 'line-color': '#111', 'line-width': 1.4, 'line-opacity': 0.6 } });

  // ---- Parcel fabric (boundary lines, on by default, viewport-loaded at zoom 15+)
  map.addSource('parcel_fabric', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'parcel-fabric-line', type: 'line', source: 'parcel_fabric',
    paint: { 'line-color': '#64748b', 'line-width': 0.8, 'line-opacity': 0.7 } });

  // ---- Population growth by ZIP (Census ACS) — a choropleth of ZCTA polygons
  // filled by % change over the selected year window. Diverging purple→green
  // (colorblind-safe): purple = losing people, near-white = flat, green = growing.
  // The 'growth' property is computed client-side per year-window (see recolorPop);
  // Metro trend choropleth. Added before the ZIP layers so that when both are
  // on, the finer-grained ZIP polygons draw over the metro wash rather than
  // under it. maxzoom 9: past that a metro outline is off-screen anyway and
  // the shading would just be a tint over the parcels you came to look at.
  map.addSource('metro_trends', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'mt-fill', type: 'fill', source: 'metro_trends', maxzoom: 9,
    layout: { visibility: 'none' },
    paint: {
      'fill-color': ['step', ['coalesce', ['get', 'strength_p'], -1], '#e5e7eb',
        0, '#f1f6f5', 17, '#cfe6e2', 34, '#9ed0c8', 50, '#66b3a8', 67, '#2f8d80', 84, '#0f766e'],
      'fill-opacity': ['interpolate', ['linear'], ['zoom'], 3, 0.78, 8, 0.5, 9, 0.25],
    } });
  map.addLayer({ id: 'mt-line', type: 'line', source: 'metro_trends', maxzoom: 9,
    layout: { visibility: 'none' },
    paint: { 'line-color': 'rgba(255,255,255,0.8)', 'line-width': 0.7 } });

  // null-growth polygons are hidden via the opacity sentinel (1e9).
  const NO_DATA = 1e9;
  map.addSource('pop_growth', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  const popColor = ['interpolate', ['linear'], ['coalesce', ['get', 'growth'], 0],
    -15, '#762a83', -5, '#af8dc3', -1, '#e7d4e8', 0, '#f7f7f7',
    1, '#d9f0d3', 5, '#7fbf7b', 15, '#1b7837', 30, '#00441b'];
  map.addLayer({ id: 'pop-fill', type: 'fill', source: 'pop_growth', layout: { visibility: 'none' },
    paint: { 'fill-color': popColor,
      'fill-opacity': ['case', ['==', ['coalesce', ['get', 'growth'], NO_DATA], NO_DATA], 0, 0.72] } });
  map.addLayer({ id: 'pop-line', type: 'line', source: 'pop_growth', layout: { visibility: 'none' },
    paint: { 'line-color': 'rgba(255,255,255,0.65)', 'line-width': 0.6 } });
  map.addLayer({ id: 'pop-label', type: 'symbol', source: 'pop_growth', minzoom: 10.5, layout: {
      visibility: 'none',
      'text-font': FONT,
      'text-field': ['case', ['==', ['coalesce', ['get', 'growth'], NO_DATA], NO_DATA], '',
        ['concat', ['case', ['>=', ['coalesce', ['get', 'growth'], 0], 0], '+', ''],
          ['to-string', ['round', ['get', 'growth']]], '%']],
      'text-size': 11 },
    paint: { 'text-color': '#111827', 'text-halo-color': 'rgba(255,255,255,0.9)', 'text-halo-width': 1.4 } });

  // ---- Area rents (Zillow ZORI by ZIP, or HUD Fair Market Rent by FMR area)
  // Sequential teal→amber: the scale is absolute dollars, so unlike population
  // growth there is no meaningful midpoint to diverge around. Break points are
  // set from the data in view (see recolorRents) rather than fixed nationally —
  // a national ramp would render every ZIP in a single metro the same colour.
  map.addSource('rents', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'rents-fill', type: 'fill', source: 'rents', layout: { visibility: 'none' },
    paint: { 'fill-color': ['coalesce', ['get', '_c'], 'rgba(0,0,0,0)'], 'fill-opacity': 0.68 } });
  map.addLayer({ id: 'rents-line', type: 'line', source: 'rents', layout: { visibility: 'none' },
    paint: { 'line-color': 'rgba(255,255,255,0.7)', 'line-width': 0.7 } });
  map.addLayer({ id: 'rents-label', type: 'symbol', source: 'rents', minzoom: 9.5,
    layout: { visibility: 'none', 'text-font': FONT, 'text-field': ['get', '_lbl'], 'text-size': 11 },
    paint: { 'text-color': '#111827', 'text-halo-color': 'rgba(255,255,255,0.92)',
      'text-halo-width': 1.4 } });

  // ---- Condo termination targets (Records mode pins, scored)
  map.addSource('targets', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'targets-pts', type: 'circle', source: 'targets', layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'],
        8, ['interpolate', ['linear'], ['coalesce', ['get', 'units_nal'], 0], 20, 3, 400, 7],
        14, ['interpolate', ['linear'], ['coalesce', ['get', 'units_nal'], 0], 20, 6, 400, 18]],
      'circle-color': ['interpolate', ['linear'], ['coalesce', ['get', 'score'], 0],
        40, '#94a3b8', 60, '#0ea5e9', 75, '#f59e0b', 88, '#dc2626'],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1.2, 'circle-opacity': 0.9,
    },
  });
  map.addLayer({ id: 'targets-sel', type: 'circle', source: 'targets', layout: { visibility: 'none' },
    filter: ['==', ['get', 'group_key'], ''],
    paint: { 'circle-radius': 16, 'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-color': '#0f172a', 'circle-stroke-width': 3 } });
  map.addLayer({ id: 'targets-label', type: 'symbol', source: 'targets', minzoom: 13,
    layout: { visibility: 'none', 'text-font': FONT, 'text-field': ['get', 'condo_name'], 'text-size': 10.5,
      'text-offset': [0, 1.3], 'text-anchor': 'top', 'text-max-width': 12 },
    paint: { 'text-color': '#0f172a', 'text-halo-color': '#fff', 'text-halo-width': 1.4 } });

  // ---- Development-analysis site (assemblage) outline
  map.addSource('dev_site', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'dev-site-fill', type: 'fill', source: 'dev_site',
    paint: { 'fill-color': '#d97706', 'fill-opacity': 0.18 } });
  map.addLayer({ id: 'dev-site-line', type: 'line', source: 'dev_site',
    paint: { 'line-color': '#d97706', 'line-width': 2 } });

  // ---- Selection + comps
  map.addSource('selection', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'selection-fill', type: 'fill', source: 'selection',
    paint: { 'fill-color': '#1f6feb', 'fill-opacity': 0.25 } });
  map.addLayer({ id: 'selection-line', type: 'line', source: 'selection',
    paint: { 'line-color': '#1f6feb', 'line-width': 2.5 } });

  map.addSource('ll-ring', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'll-ring', type: 'line', source: 'll-ring',
    paint: { 'line-color': '#d97706', 'line-width': 2, 'line-dasharray': [3, 3] } });

  // Saved addresses (marks) — gold pins, always visible above everything else
  map.addSource('marks', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'marks-pts', type: 'circle', source: 'marks',
    paint: { 'circle-radius': 7, 'circle-color': '#f59e0b',
      'circle-stroke-color': '#78350f', 'circle-stroke-width': 2 } });

  map.addSource('comps-ring', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'comps-ring', type: 'line', source: 'comps-ring',
    paint: { 'line-color': '#7c3aed', 'line-width': 1.5, 'line-dasharray': [2, 2] } });
  map.addSource('comps', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'comps-pts', type: 'circle', source: 'comps',
    paint: { 'circle-radius': 5,
      'circle-color': ['match', ['get', 'kind'], 'sale', '#7c3aed', 'rent', '#22c55e', '#9ca3af'],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });

  // Owner/party search results — parcels owned by the searched name (purple).
  map.addSource('party_props', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'party-props-pts', type: 'circle', source: 'party_props',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 15, 9],
      'circle-color': '#9333ea', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-opacity': 0.92,
    } });

  // Supply pipeline — nearby competing new-construction permits (amber squares).
  map.addSource('supply_pipeline', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'supply-pipeline-pts', type: 'circle', source: 'supply_pipeline',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 5, 16, 8],
      'circle-color': '#d97706', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-opacity': 0.9,
    } });

  // Market data-coverage markers — every market with wired data, sized/colored by
  // how much data it has (depth). Click to jump; hover lists capabilities.
  map.addSource('zoning_cities', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'zoning-cities-pts', type: 'circle', source: 'zoning_cities', layout: { visibility: 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, ['+', 3, ['*', 0.5, ['coalesce', ['get', 'depth'], 1]]], 11, ['+', 6, ['get', 'depth']]],
      'circle-color': ['step', ['coalesce', ['get', 'depth'], 1], '#5eead4', 3, '#14b8a6', 5, '#0f766e', 7, '#b45309'],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.92,
    } });
  map.addLayer({ id: 'zoning-cities-label', type: 'symbol', source: 'zoning_cities', minzoom: 6,
    layout: { 'text-font': FONT, 'text-field': ['get', 'city'], 'text-size': 10, 'text-offset': [0, 1.1], 'text-anchor': 'top' },
    paint: { 'text-color': '#0f766e', 'text-halo-color': '#fff', 'text-halo-width': 1.4 } });

  // map-dependent wiring (layer events, hovers, viewport handlers, style filters)
  wireToggles();
  wireAutoPlace();
  wireClick();
  wireStationHover();
  wireCompHover();
  wireZoningHover();
  wireHash();
  wireMarkHover();
  wireSfhaToggle();  // sets style filters — must wait for the style
  // DOM wiring too, in case DOMContentLoaded raced us (idempotent)
  wireDomUI();
  // Populate the map-source-backed layers NOW that their sources exist — the
  // DOMContentLoaded path can run wireDomUI before map load, and a setData before
  // the source is created is silently dropped (optional chaining no-op).
  loadMarketMarkers();
  loadMarks();

  if (initial.basemap && BASEMAPS[initial.basemap]) setBasemap(initial.basemap);
  if (initial.selection && initial.selection.length === 2) {
    const [lon, lat] = initial.selection;
    doParcelLookup(lon, lat);
  }
});

// DOM-only UI wiring — must NOT depend on the map having loaded (a slow/blocked
// basemap should never kill tabs, search, pickers or panel logic). Idempotent.
let _domWired = false;
function wireDomUI() {
  if (_domWired) return;
  _domWired = true;
  // Each step guarded: one failure must never take down the rest of the UI.
  for (const fn of [renderLegend, renderZoneCatLegend, renderFloodLegend,
                    wirePlacePicker, wirePartySearch, wireBrokerSearch,
                    wireBasemapButtons, wireSearch, wireShare, wireLiveLocal,
                    wireTabs, wireComparisonTray, loadMarks, loadFactoryCount, populateCounts]) {
    try { fn(); } catch (e) { console.error('wireDomUI:', fn.name, e); }
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireDomUI);
else wireDomUI();

// Factory-layer label count — computed from the geojson at runtime (active plants
// only) so the sidebar never drifts from the data.
async function loadFactoryCount() {
  const el = document.getElementById('factories-count');
  if (!el) return;
  try {
    const gj = await fetchJSON('/data/modular_factories.geojson', { timeoutMs: 30000 });
    const n = (gj.features || []).filter((f) => f.properties && f.properties.status === 'active').length;
    if (n) el.textContent = fmtNum(n);
  } catch (e) {
    // The factory set is curated, not fetched by scripts/fetch_layers.py, so an
    // install that never received the file has no data behind this toggle. A
    // switch that visibly does nothing is worse than no switch — hide it.
    hideFactoryToggle();
  }
}

function hideFactoryToggle() {
  const cb = document.getElementById('lyr-factories');
  if (cb) {
    cb.checked = false;
    if (cb.closest('label')) cb.closest('label').hidden = true;
  }
  const legend = document.getElementById('factory-legend');
  if (legend) legend.hidden = true;
}

function renderLegend() {
  document.getElementById('legend-items').innerHTML = Object.entries(ZONING_PALETTE)
    .map(([k, v]) => `<div class="row"><span class="swatch" style="background:${v}"></span><span>${k}</span></div>`).join('');
}
function renderFloodLegend() {
  document.getElementById('flood-legend').innerHTML = Object.entries(FLOOD_PALETTE)
    .map(([k, v]) => `<div class="row"><span class="swatch" style="background:${v.color}"></span><span>${v.label}</span></div>`).join('');
}
function renderZoneCatLegend() {
  const el = document.getElementById('zone-cat-legend');
  if (!el) return;
  el.innerHTML = Object.keys(ZONE_CAT_LABELS)
    .map((k) => `<div class="row"><span class="swatch" style="background:${ZONE_CAT_COLORS[k]}"></span><span>${ZONE_CAT_LABELS[k]}</span></div>`).join('');
}

// ---------- place picker (State → City) + completed-city coverage ----------
let CITY_INDEX = {};          // { ST: [ {city,lon,lat}, ... ] }
let currentPlaceState = null; // selected state abbrev, drives FL-only UI

async function wirePlacePicker() {
  const stateSel = document.getElementById('pick-state');
  const citySel = document.getElementById('pick-city');
  const countEl = document.getElementById('cities-count');
  if (!stateSel) return;
  let data;
  try { data = await fetchJSON('/api/cities'); }
  catch (e) { return; }
  const cities = data.cities || [];
  if (countEl) countEl.textContent = `${data.count} cities`;
  const inlineCount = document.getElementById('cities-count-inline');
  if (inlineCount) inlineCount.textContent = data.count;
  // Keep every hardcoded "N cities" claim in sync with the live registry count.
  for (const id of ['cities-count-cov', 'cities-count-src']) {
    const el = document.getElementById(id);
    if (el && data.count) el.textContent = data.count;
  }

  // group by state for the picker + auto-place (zoning cities only)
  CITY_INDEX = {};
  for (const c of cities) (CITY_INDEX[c.state] = CITY_INDEX[c.state] || []).push(c);

  // Data-coverage markers come from /api/markets (superset: also permit-only and
  // sales-only markets), each dot carrying its capability list + depth.
  loadMarketMarkers();

  // populate states
  for (const st of Object.keys(CITY_INDEX).sort()) {
    const o = document.createElement('option');
    o.value = st; o.textContent = `${st} (${CITY_INDEX[st].length})`;
    stateSel.appendChild(o);
  }
  stateSel.addEventListener('change', () => {
    citySel.innerHTML = '<option value="">City…</option>';
    const list = CITY_INDEX[stateSel.value] || [];
    for (const c of list) {
      const o = document.createElement('option');
      o.value = c.city; o.textContent = c.city;
      citySel.appendChild(o);
    }
    citySel.disabled = list.length === 0;
  });
  citySel.addEventListener('change', () => {
    const st = stateSel.value;
    const c = (CITY_INDEX[st] || []).find((x) => x.city === citySel.value);
    if (c) recalibrateForPlace(c.city, st, c.lon, c.lat);
  });

  // click a market dot to jump; hover shows what data that market has
  let mpop = null;
  for (const id of ['zoning-cities-pts', 'zoning-cities-label']) {
    map.on('click', id, (e) => {
      const p = e.features[0].properties || {};
      const [clon, clat] = e.features[0].geometry.coordinates;
      if (stateSel && CITY_INDEX[p.state]) { stateSel.value = p.state; stateSel.dispatchEvent(new Event('change')); }
      const c = (CITY_INDEX[p.state] || []).find((x) => x.city === p.city);
      if (citySel && c) citySel.value = p.city;
      recalibrateForPlace(p.city, p.state, clon, clat);
    });
    map.on('mouseenter', id, (e) => {
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0].properties || {};
      let caps = p.caps; try { caps = JSON.parse(p.caps); } catch (_) {}
      const capList = Array.isArray(caps) ? caps : String(caps || '').split(',').filter(Boolean);
      mpop = new maplibregl.Popup({ closeButton: false, offset: 8, maxWidth: '260px' }).setLngLat(e.lngLat)
        .setHTML(`<b>${esc(p.city)}, ${esc(p.state)}</b><br><span style="font-size:11px;color:#475569">${capList.map(esc).join(' · ') || 'flood/demographics/screening (national)'}</span>`).addTo(map);
    });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; mpop && mpop.remove(); mpop = null; });
  }
}

// Load market-coverage dots (capability-tagged) from /api/markets.
async function loadMarketMarkers() {
  let data;
  try { data = await fetchJSON('/api/markets', { timeoutMs: 15000 }); }
  catch (e) { return; }
  const feats = (data.markets || []).filter((mk) => mk.lon != null).map((mk) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [mk.lon, mk.lat] },
    properties: { city: mk.city, state: mk.state, depth: mk.depth, caps: JSON.stringify(mk.caps || []) },
  }));
  map.getSource('zoning_cities')?.setData({ type: 'FeatureCollection', features: feats });
  const badge = document.getElementById('markets-count');
  if (badge && data.count) badge.textContent = data.count;
}

// Recalibrate the whole app for a selected place: fly there, turn on the colored
// zoning overlay, update the badge, and hide Florida-only panels when out of FL.
function recalibrateForPlace(city, state, lon, lat) {
  currentPlaceState = state;
  map.flyTo({ center: [lon, lat], zoom: 14, speed: 1.4 });
  const badge = document.getElementById('place-badge');
  if (badge) badge.textContent = `${city}, ${state}`;
  // auto-enable colored zoning overlay
  const zcb = document.getElementById('lyr-metro-zoning');
  if (zcb && !zcb.checked) { zcb.checked = true; zcb.dispatchEvent(new Event('change')); }
  else if (metroZoningOn) { setTimeout(refreshMetroZoning, 400); }
  applyPlaceScope(state);
}

// Show/hide Florida-only layer groups + panels so the sidebar matches the place.
function applyPlaceScope(state) {
  const isFL = state === 'FL';
  for (const el of document.querySelectorAll('[data-fl-only]')) el.style.display = isFL ? '' : 'none';
}

// ---------- auto place tracking ----------
// Keep the badge, FL-only scope, and pickers in sync with wherever the map IS —
// panning to New York must read "New York, NY", not whatever was last selected.
function wireAutoPlace() {
  let t = null;
  const sync = () => {
    const z = map.getZoom();
    const c = map.getCenter();
    const badge = document.getElementById('place-badge');
    if (z < 5) {  // national view
      if (badge) badge.textContent = 'Nationwide';
      currentPlaceState = null;
      applyPlaceScope(null);
      return;
    }
    // nearest covered city to the viewport center
    let best = null, bestD = 1e9;
    for (const st of Object.keys(CITY_INDEX)) {
      for (const city of CITY_INDEX[st]) {
        const d = (city.lon - c.lng) ** 2 + (city.lat - c.lat) ** 2;
        if (d < bestD) { bestD = d; best = { ...city, state: st }; }
      }
    }
    const dist = Math.sqrt(bestD);  // degrees, ~69mi per degree
    if (best && dist <= 2.5) {
      if (badge) badge.textContent = dist <= 0.35 ? `${best.city}, ${best.state}` : `Near ${best.city}, ${best.state}`;
      currentPlaceState = best.state;
      applyPlaceScope(best.state);
      // sync the pickers to the place (state change only repopulates the city list)
      const stateSel = document.getElementById('pick-state');
      const citySel = document.getElementById('pick-city');
      if (stateSel && stateSel.value !== best.state) {
        stateSel.value = best.state;
        stateSel.dispatchEvent(new Event('change'));
      }
      if (citySel && dist <= 0.35 && citySel.value !== best.city) citySel.value = best.city;
    } else {
      if (badge) badge.textContent = 'Nationwide';
      currentPlaceState = null;
      applyPlaceScope(null);
    }
  };
  map.on('moveend', () => { clearTimeout(t); t = setTimeout(sync, 250); });
  // run once at startup so the initial view is honest too
  setTimeout(sync, 1200);
}

function setVis(layers, on) {
  for (const id of layers) {
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  }
}

function wireToggles() {
  const m = {
    'lyr-mdc-zoning': ['mdc-zoning-fill', 'mdc-zoning-line'],
    'lyr-mdc-flu': ['mdc-flu-fill'],
    'lyr-bro-zoning': ['bro-zoning-fill', 'bro-zoning-line'],
    'lyr-bro-flu': ['bro-flu-fill'],
    'lyr-mdc-munis': ['mdc-munis-line'],
    'lyr-bro-munis': ['bro-munis-line'],
    'lyr-oz': ['oz-fill', 'oz-line'],
    'lyr-cra': ['cra-fill', 'cra-line'],
    'lyr-brownfield': ['brownfield-fill'],
    'lyr-enterprise': ['enterprise-fill'],
    'lyr-historic': ['historic-fill', 'historic-line'],
    'lyr-transit-zones': ['rtz-fill'],
    'lyr-transit': ['metrorail-pts', 'metromover-pts'],
    'lyr-schools': ['schools-pts'],
    'lyr-parks': ['parks-fill'],
    'lyr-factories': ['factories-pts'],  // legend visibility handled below
    'lyr-zoning-cities': ['zoning-cities-pts', 'zoning-cities-label'],
  };
  for (const [cbId, layers] of Object.entries(m)) {
    const cb = document.getElementById(cbId);
    if (!cb) continue;
    cb.addEventListener('change', () => setVis(layers, cb.checked));
    setVis(layers, cb.checked);
  }
  wireMetroZoning();
  wireTransitRoutes();
  wireNationalFlood();
  wirePermitHeat();
  wireParcelFabric();
  wirePopGrowth();
  wireRents();
  wireTrends();
  // Factory legend follows its toggle
  const fcb = document.getElementById('lyr-factories');
  const flegend = document.getElementById('factory-legend');
  if (fcb && flegend) {
    const sync = () => { flegend.hidden = !fcb.checked; };
    fcb.addEventListener('change', sync); sync();
  }
}

// Metro market trends — every CBSA shaded by one indicator. The payload is
// fetched once (geometry is the expensive part and does not change when you
// switch metric), then recolouring is a paint-property swap with no refetch.
//
// Colour is driven by the national percentile, not the raw value, for two
// reasons: one ramp then reads the same way for dollars, rates and ratios, and
// a quantile scale keeps these heavily skewed distributions from collapsing
// into a single band. The legend carries the real values.
const TREND_LAYERS = ['mt-fill', 'mt-line'];
// Colour-blind-safe sequential ramp; six bands to match six quantile cuts.
const TREND_RAMP = ['#f1f6f5', '#cfe6e2', '#9ed0c8', '#66b3a8', '#2f8d80', '#0f766e'];
let trendsData = null;
let trendsOn = false;
let trendsMetric = 'strength';

function trendsColorExpr(metric) {
  const p = ['coalesce', ['get', metric + '_p'], -1];
  return ['step', p, '#e5e7eb',        // -1 → no data
    0, TREND_RAMP[0], 17, TREND_RAMP[1], 34, TREND_RAMP[2],
    50, TREND_RAMP[3], 67, TREND_RAMP[4], 84, TREND_RAMP[5]];
}

// Levels print bare; changes and net figures carry a sign. Showing "+42%" for
// multifamily *share* would read as growth, which it is not.
function fmtTrend(v, unit) {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '';
  switch (unit) {
    case '$':     return '$' + Math.round(v).toLocaleString();
    case 'pct':   return v.toFixed(1) + '%';          // a level
    case 'n/1k':  return v.toFixed(1);                // a level
    case 'x':     return v.toFixed(2) + '×';          // a ratio
    case '%':
    case '%/yr':  return sign + v.toFixed(1) + '%';   // a change
    case 'pts':   return sign + v.toFixed(0);         // a rank difference
    default:      return sign + v.toFixed(1);         // net rate, e.g. /1k
  }
}

function renderTrendsLegend() {
  const box = document.getElementById('trends-legend');
  const about = document.getElementById('trends-about');
  if (!box || !trendsData) return;
  const m = trendsData.metrics[trendsMetric];
  if (!m) return;
  const b = m.breaks || [];
  // Six bands, five cut points: "< b0", "b0–b1", … , "> b4".
  const labels = b.length >= 5
    ? [`< ${fmtTrend(b[0], m.unit)}`, `${fmtTrend(b[0], m.unit)}–${fmtTrend(b[1], m.unit)}`,
       `${fmtTrend(b[1], m.unit)}–${fmtTrend(b[2], m.unit)}`,
       `${fmtTrend(b[2], m.unit)}–${fmtTrend(b[3], m.unit)}`,
       `${fmtTrend(b[3], m.unit)}–${fmtTrend(b[4], m.unit)}`, `> ${fmtTrend(b[4], m.unit)}`]
    : TREND_RAMP.map((_, i) => `band ${i + 1}`);
  box.innerHTML = TREND_RAMP.map((c, i) =>
    `<span><i style="background:${c}"></i>${esc(labels[i])}</span>`).join('')
    + (m.covered < trendsData.count
      ? `<span><i style="background:#e5e7eb;border:1px solid #cbd5e1"></i>no data (${trendsData.count - m.covered})</span>`
      : '');
  if (about) {
    about.textContent = m.description + (m.higher_is_better ? '' : ' Lower is better.');
  }
}

async function loadTrends() {
  if (trendsData) return trendsData;
  const note = document.getElementById('trends-note');
  if (note) { note.textContent = 'Loading metro boundaries…'; note.hidden = false; }
  try {
    trendsData = await fetchJSON('/api/metro-trends', { timeoutMs: 45000 });
  } catch (e) {
    if (note) {
      note.textContent = 'Metro trends unavailable — run scripts/fetch_cbsa_geo.py.';
      note.hidden = false;
    }
    return null;
  }
  if (note) note.hidden = true;
  map.getSource('metro_trends')?.setData(trendsData);

  const sel = document.getElementById('trends-metric');
  if (sel && !sel.options.length) {
    // Composites first — they are the reason to open this layer — then the raw
    // measurements they are built from.
    const order = ['strength', 'tightness', 'migration', 'pop_growth', 'domestic_mig',
      'rent', 'rent_growth', 'wealth', 'agi_premium', 'pay', 'pay_growth',
      'permits', 'mf_share', 'home_price', 'price_growth', 'affordability'];
    const keys = order.filter((k) => trendsData.metrics[k]);
    sel.innerHTML = keys.map((k) =>
      `<option value="${k}"${k === trendsMetric ? ' selected' : ''}>${esc(trendsData.metrics[k].label)}</option>`).join('');
  }
  renderTrendsLegend();
  return trendsData;
}

function wireTrends() {
  const cb = document.getElementById('lyr-trends');
  const controls = document.getElementById('trends-controls');
  const legend = document.getElementById('trends-legend');
  const sel = document.getElementById('trends-metric');
  if (!cb) return;

  cb.addEventListener('change', async () => {
    trendsOn = cb.checked;
    if (controls) controls.hidden = !cb.checked;
    if (legend) legend.hidden = !cb.checked;
    if (!cb.checked) { setVis(TREND_LAYERS, false); return; }
    if (!(await loadTrends())) { cb.checked = trendsOn = false; return; }
    setVis(TREND_LAYERS, true);
  });

  sel?.addEventListener('change', () => {
    trendsMetric = sel.value;
    map.setPaintProperty('mt-fill', 'fill-color', trendsColorExpr(trendsMetric));
    renderTrendsLegend();
  });

  let popup = null;
  map.on('mouseenter', 'mt-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mousemove', 'mt-fill', (e) => {
    const p = e.features[0]?.properties || {};
    const m = trendsData?.metrics?.[trendsMetric];
    if (!m) return;
    const raw = p[trendsMetric] === '' || p[trendsMetric] == null ? null : Number(p[trendsMetric]);
    const pc = p[trendsMetric + '_p'] === '' || p[trendsMetric + '_p'] == null
      ? null : Number(p[trendsMetric + '_p']);
    if (!popup) popup = new maplibregl.Popup({ closeButton: false, offset: 6 });
    popup.setLngLat(e.lngLat).setHTML(
      `<b>${esc(p.name || '')}</b><br/>${esc(m.label)}: <b>${fmtTrend(raw, m.unit)}</b>`
      + (pc == null ? '' : ` <span style="color:#64748b">(${pc}th pct)</span>`)
      + `<br/><span style="color:#64748b">${Number(p.population || 0).toLocaleString()} people`
      + (p.stage ? ` · ${esc(p.stage)}` : '') + '</span>').addTo(map);
  });
  map.on('mouseleave', 'mt-fill', () => {
    map.getCanvas().style.cursor = ''; popup && popup.remove(); popup = null;
  });
  // Click straight through to the full due-diligence report for that metro.
  map.on('click', 'mt-fill', (e) => {
    const cbsa = e.features[0]?.properties?.cbsa;
    if (cbsa) window.open('/api/dd/' + encodeURIComponent(cbsa) + '/report', '_blank', 'noopener');
  });
}

// Population growth by ZIP — a choropleth of Census ZCTA polygons filled by
// % population change over the year window the two sliders pick. Geometry +
// series come per-viewport from /api/zcta-pop (Census TIGERweb boundaries joined
// to zcta_pop.json). Panning refetches geometry; dragging a slider only recolors.
const POP_LAYERS = ['pop-fill', 'pop-line', 'pop-label'];
const POP_NO_DATA = 1e9;
let popYears = null;       // e.g. [2012 … 2023]
let popFC = null;          // last-fetched FeatureCollection (geometry + pop arrays)
let popGrowthOn = false;
let popSeq = 0;            // latest-pan-wins guard

function popYearIdx() {
  const a = document.getElementById('pop-year-a');
  const b = document.getElementById('pop-year-b');
  let i = parseInt(a?.value ?? 0, 10), j = parseInt(b?.value ?? 0, 10);
  if (i > j) [i, j] = [j, i];
  return [i, j];
}

// Recompute each polygon's % growth for the selected window and repaint. Pure
// client-side — no network — so slider drags are instant.
function recolorPop() {
  if (!popFC || !popYears) return;
  const [i, j] = popYearIdx();
  const la = document.getElementById('pop-year-a-lbl');
  const lb = document.getElementById('pop-year-b-lbl');
  if (la) la.textContent = popYears[i];
  if (lb) lb.textContent = popYears[j];
  let shown = 0;
  for (const f of popFC.features) {
    const pop = f.properties.pop || [];
    const p0 = pop[i], p1 = pop[j];
    const ok = p0 != null && p1 != null && p0 > 0;
    f.properties.growth = ok ? ((p1 - p0) / p0 * 100) : null;
    f.properties.p0 = p0 ?? null;
    f.properties.p1 = p1 ?? null;
    if (ok) shown++;
  }
  map.getSource('pop_growth')?.setData(popFC);
  const note = document.getElementById('pop-growth-note');
  if (note) { note.textContent = `${shown.toLocaleString()} ZIPs · % change ${popYears[i]} → ${popYears[j]} (ACS 5-yr)`; note.hidden = false; }
}

async function refreshPopGrowth() {
  const note = document.getElementById('pop-growth-note');
  if (map.getZoom() < 9) {
    popFC = null;
    map.getSource('pop_growth')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in to load population by ZIP…'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  const bbox = `${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`;
  if (note) { note.textContent = 'Loading population by ZIP…'; note.hidden = false; }
  const seq = ++popSeq;
  try {
    const gj = await fetchJSON(`/api/zcta-pop?bbox=${bbox}`, { timeoutMs: 30000 });
    if (seq !== popSeq || !popGrowthOn) return;   // a newer pan won, or layer toggled off
    if (gj.years && gj.years.length) {
      popYears = gj.years;
      const last = popYears.length - 1;
      for (const [id, val] of [['pop-year-a', 0], ['pop-year-b', last]]) {
        const el = document.getElementById(id);
        if (el && el.max !== String(last)) { el.max = String(last); el.value = String(val); }
      }
    }
    popFC = { type: 'FeatureCollection', features: gj.features || [] };
    if (!popFC.features.length && note) { note.textContent = gj.note || 'No ZIP data in view.'; note.hidden = false; return; }
    recolorPop();
  } catch (e) {
    if (note) { note.textContent = 'Population layer unavailable here.'; note.hidden = false; }
  }
}

function wirePopGrowth() {
  const cb = document.getElementById('lyr-pop-growth');
  const controls = document.getElementById('pop-growth-controls');
  const legend = document.getElementById('pop-growth-legend');
  const note = document.getElementById('pop-growth-note');
  if (!cb) return;

  cb.addEventListener('change', () => {
    popGrowthOn = cb.checked;
    setVis(POP_LAYERS, cb.checked);
    if (controls) controls.hidden = !cb.checked;
    if (legend) legend.hidden = !cb.checked;
    if (note) note.hidden = !cb.checked;
    if (cb.checked) refreshPopGrowth();
    else { popSeq++; popFC = null; map.getSource('pop_growth')?.setData({ type: 'FeatureCollection', features: [] }); }
  });

  let t = null;
  for (const id of ['pop-year-a', 'pop-year-b']) {
    document.getElementById(id)?.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(recolorPop, 120);  // recolor only — no refetch
    });
  }
  let mt = null;
  map.on('moveend', () => {
    if (!popGrowthOn) return;
    clearTimeout(mt);
    mt = setTimeout(refreshPopGrowth, 350);
  });

  // hover popup — ZIP, populations at each endpoint, and % change for the window
  let popup = null;
  map.on('mouseenter', 'pop-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mousemove', 'pop-fill', (e) => {
    const p = e.features[0]?.properties || {};
    const [i, j] = popYearIdx();
    const g = (p.growth === '' || p.growth == null) ? null : Number(p.growth);
    const gTxt = g == null ? '<span style="color:#94a3b8">no ACS data</span>'
      : `<b style="color:${g >= 0 ? '#1b7837' : '#762a83'}">${g >= 0 ? '+' : ''}${g.toFixed(1)}%</b>`;
    const body = (p.p0 != null && p.p1 != null && p.p0 !== '' && p.p1 !== '')
      ? `${popYears[i]}: ${Number(p.p0).toLocaleString()} → ${popYears[j]}: ${Number(p.p1).toLocaleString()}<br/>${gTxt}`
      : gTxt;
    if (!popup) popup = new maplibregl.Popup({ closeButton: false, offset: 6 });
    popup.setLngLat(e.lngLat).setHTML(`<b>ZIP ${esc(p.z || '')}</b><br/>${body}`).addTo(map);
  });
  map.on('mouseleave', 'pop-fill', () => { map.getCanvas().style.cursor = ''; popup && popup.remove(); popup = null; });
}

// ---------- area rents (Zillow ZORI by ZIP · HUD FMR by FMR area) ----------
// Two answers to "what does this rent for", kept separate on purpose. ZORI is
// the market asking rent; HUD FMR is the 40th-percentile program ceiling that
// voucher and LIHTC underwriting keys off. The gap between them is the
// affordable spread, which is why you'd want to flip between them in place.
const RENT_LAYERS = ['rents-fill', 'rents-line', 'rents-label'];
const RENT_RAMP = ['#0f766e', '#14b8a6', '#7dd3fc', '#fcd34d', '#f59e0b', '#c2410c'];
let rentsOn = false;
let rentsSeq = 0;
let rentsFC = null;

function rentSource() {
  return document.querySelector('input[name="rent-src"]:checked')?.value || 'zori';
}
function rentBeds() {
  return document.getElementById('rent-beds')?.value || '2';
}

// Quantile breaks over what is actually in view. A fixed national ramp would
// paint every ZIP in one metro the same colour; quantiles keep the spread legible
// whether you're looking at Miami Beach or rural Ohio.
function recolorRents() {
  if (!rentsFC) return;
  const vals = rentsFC.features.map((f) => f.properties.rent).filter((v) => v != null).sort((a, b) => a - b);
  if (!vals.length) return;
  const q = (p) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
  const breaks = [q(0.15), q(0.35), q(0.55), q(0.75), q(0.9)];
  for (const f of rentsFC.features) {
    const v = f.properties.rent;
    let i = 0;
    while (i < breaks.length && v > breaks[i]) i++;
    f.properties._c = RENT_RAMP[i];
    f.properties._lbl = v == null ? '' : '$' + Math.round(v / 10) * 10;
  }
  map.getSource('rents')?.setData(rentsFC);

  const legend = document.getElementById('rents-legend');
  if (legend) {
    // Quantile edges collapse onto each other when few areas are in view — a
    // single ZIP would otherwise print the same "$3010+" swatch six times.
    // Keep only the buckets that actually differ.
    const seen = new Set();
    const items = [];
    for (let i = 0; i < RENT_RAMP.length; i++) {
      const lo = Math.round((i === 0 ? vals[0] : breaks[i - 1]) / 10) * 10;
      if (seen.has(lo)) continue;
      seen.add(lo);
      items.push(`<span><i style="background:${RENT_RAMP[i]}"></i>$${lo.toLocaleString()}${items.length || vals.length > 1 ? '+' : ''}</span>`);
    }
    legend.innerHTML = items.join('');
    legend.hidden = false;
  }
}

async function refreshRents() {
  const note = document.getElementById('rents-note');
  const src = rentSource();
  if (src === 'zori' && map.getZoom() < 8.5) {
    rentsFC = null;
    map.getSource('rents')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in to load rents by ZIP…'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  const bbox = `${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`;
  if (note) { note.textContent = 'Loading rents…'; note.hidden = false; }
  const seq = ++rentsSeq;
  try {
    const url = `/api/rents-overlay?bbox=${bbox}&source=${src}` + (src === 'fmr' ? `&beds=${rentBeds()}` : '');
    const gj = await fetchJSON(url, { timeoutMs: 30000 });
    if (seq !== rentsSeq || !rentsOn) return;      // a newer pan won
    rentsFC = { type: 'FeatureCollection', features: gj.features || [] };
    if (!rentsFC.features.length) {
      map.getSource('rents')?.setData(rentsFC);
      if (note) { note.textContent = gj.note || 'No rent data in view.'; note.hidden = false; }
      return;
    }
    recolorRents();
    if (note) {
      note.textContent = src === 'fmr'
        ? `${rentsFC.features.length} HUD FMR area(s) · ${rentBeds()}-bedroom ceiling`
        : `${rentsFC.features.length} ZIPs · Zillow ZORI${gj.asof ? ' · ' + gj.asof : ''}`;
      note.hidden = false;
    }
  } catch (e) {
    if (note) { note.textContent = 'Rent layer unavailable here.'; note.hidden = false; }
  }
}

function wireRents() {
  const cb = document.getElementById('lyr-rents');
  if (!cb) return;
  const controls = document.getElementById('rents-controls');
  const note = document.getElementById('rents-note');
  const legend = document.getElementById('rents-legend');

  const sync = () => {
    rentsOn = cb.checked;
    setVis(RENT_LAYERS, cb.checked);
    if (controls) controls.hidden = !cb.checked;
    if (note) note.hidden = !cb.checked;
    if (legend) legend.hidden = !cb.checked;
    if (cb.checked) refreshRents();
    else { rentsSeq++; rentsFC = null; map.getSource('rents')?.setData({ type: 'FeatureCollection', features: [] }); }
  };
  cb.addEventListener('change', sync);

  for (const el of document.querySelectorAll('input[name="rent-src"]')) {
    el.addEventListener('change', () => {
      const bedsRow = document.getElementById('rent-beds-row');
      if (bedsRow) bedsRow.hidden = rentSource() !== 'fmr';
      if (rentsOn) refreshRents();
    });
  }
  document.getElementById('rent-beds')?.addEventListener('change', () => { if (rentsOn) refreshRents(); });

  let mt = null;
  map.on('moveend', () => {
    if (!rentsOn) return;
    clearTimeout(mt);
    mt = setTimeout(refreshRents, 350);
  });

  let popup = null;
  map.on('mousemove', 'rents-fill', (e) => {
    const p = e.features[0]?.properties || {};
    map.getCanvas().style.cursor = 'pointer';
    let body;
    if (rentSource() === 'fmr') {
      let all = {};
      try { all = typeof p.all === 'string' ? JSON.parse(p.all) : (p.all || {}); } catch (err) { all = {}; }
      const rows = [['0', 'Studio'], ['1', '1 BR'], ['2', '2 BR'], ['3', '3 BR'], ['4', '4 BR']]
        .filter(([k]) => all[k] != null)
        .map(([k, l]) => `${l} <b>${fmtUSD(all[k])}</b>`).join(' · ');
      body = `${rows || fmtUSD(p.rent)}<br/><span style="color:#64748b;font-size:11px">HUD Fair Market Rent (40th pct)</span>`;
    } else {
      const y = p.yoy === '' || p.yoy == null ? null : Number(p.yoy);
      body = `<b>${fmtUSD(p.rent)}</b>/mo typical asking`
        + (y == null ? '' : ` <span style="color:${y >= 0 ? '#166534' : '#b91c1c'}">(${y >= 0 ? '+' : ''}${y}% yr)</span>`)
        + `<br/><span style="color:#64748b;font-size:11px">${esc(p.city || '')}${p.city && p.metro ? ' · ' : ''}${esc(p.metro || '')}</span>`;
    }
    if (!popup) popup = new maplibregl.Popup({ closeButton: false, offset: 6, maxWidth: '300px' });
    popup.setLngLat(e.lngLat).setHTML(`<b>${esc(p.name || '')}</b><br/>${body}`).addTo(map);
  });
  map.on('mouseleave', 'rents-fill', () => { map.getCanvas().style.cursor = ''; popup && popup.remove(); popup = null; });
}

// Parcel fabric — boundary lines for every parcel in view, on by default (zoom 15+).
let parcelFabricOn = true;
let parcelFabricSeq = 0;   // latest-pan-wins: stale viewport responses must not repaint
function wireParcelFabric() {
  const cb = document.getElementById('lyr-parcels');
  const note = document.getElementById('parcels-note');
  if (cb) cb.addEventListener('change', () => {
    parcelFabricOn = cb.checked;
    setVis(['parcel-fabric-line'], cb.checked);
    if (cb.checked) refreshParcelFabric();
    else {
      parcelFabricSeq++;  // cancel in-flight loads
      map.getSource('parcel_fabric')?.setData({ type: 'FeatureCollection', features: [] });
      if (note) note.hidden = true;
    }
  });
  let t = null;
  map.on('moveend', () => {
    if (!parcelFabricOn) return;
    clearTimeout(t);
    t = setTimeout(refreshParcelFabric, 400);
  });
  refreshParcelFabric();
}

// Cache of the (padded) area currently loaded into the parcel layer, so small
// pans within it don't refetch. Reset on zoom-out or a truncated (dense) load.
let parcelLoadedBox = null;
let parcelAbort = null;

async function refreshParcelFabric() {
  const note = document.getElementById('parcels-note');
  if (map.getZoom() < 15) {
    parcelAbort?.abort();
    parcelLoadedBox = null;
    map.getSource('parcel_fabric')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in (15+) for parcel lines…'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  const w = b.getWest(), s = b.getSouth(), e = b.getEast(), n = b.getNorth();
  // Already covered by the last (padded) fetch → nothing to do.
  if (parcelLoadedBox && w >= parcelLoadedBox.w && s >= parcelLoadedBox.s &&
      e <= parcelLoadedBox.e && n <= parcelLoadedBox.n) return;
  // Fetch a modestly padded box (~25% each side) — small enough to stay fast and
  // complete in dense areas, with a little headroom so tiny pans stay cached.
  const px = (e - w) * 0.25, py = (n - s) * 0.25;
  const fw = w - px, fs = s - py, fe = e + px, fn = n + py;
  const bbox = `${fw.toFixed(5)},${fs.toFixed(5)},${fe.toFixed(5)},${fn.toFixed(5)}`;
  const seq = ++parcelFabricSeq;
  parcelAbort?.abort();               // cancel a superseded in-flight fetch
  parcelAbort = new AbortController();
  try {
    const gj = await fetchJSON(`/api/parcels-overlay?bbox=${bbox}`, { timeoutMs: 25000, signal: parcelAbort.signal });
    if (seq !== parcelFabricSeq) return;  // a newer pan owns the layer
    map.getSource('parcel_fabric')?.setData(gj);
    // Cache the padded box when the result is complete (smooth panning). When
    // the box overflowed the limit (dense wide view), the padded fringe is
    // incomplete — cache only the visible box so a pan refetches the newly
    // exposed lots, keeping the visible view fully lined.
    parcelLoadedBox = gj.truncated ? { w, s, e, n } : { w: fw, s: fs, e: fe, n: fn };
    if (note) {
      const c = (gj.features || []).length;
      note.textContent = !c ? (gj.note || 'No parcels here.')
        : (gj.truncated ? `${c} parcels — zoom in to see all lines` : `${c} parcels in view`);
      note.hidden = false;
    }
  } catch (e) {
    if (e.name === 'AbortError') return;   // superseded — the new fetch owns the UI
    parcelLoadedBox = null;
    if (seq === parcelFabricSeq && note) { note.textContent = 'Parcel lines unavailable.'; note.hidden = false; }
  }
}

// ---------- development-capacity analysis (what can be built) ----------
let devSite = [];        // assembled parcels: {folio, lon, lat, lot_size_sf, address, geometry}
let devCurrent = null;   // last clicked parcel (candidate to add to the site)
let devData = null;      // last /api/dev-analysis response
let devSeq = 0;
const devBonusOn = new Set();

// Mirrors backend _dev_envelope so bonus toggles recompute instantly client-side.
function devEnvelope(lotSf, far, density, covPct, heightRaw, storiesTo, farMult, A) {
  const ftps = (A && A.ft_per_story) || 11;
  const avgUnit = (A && A.avg_unit_sf) || 1000;
  const eff = (A && A.efficiency) || 0.85;
  let stories = null, feet = null;
  if (storiesTo != null) { stories = storiesTo; feet = stories * ftps; }
  else if (heightRaw != null) {
    if (heightRaw <= 100) { stories = heightRaw; feet = stories * ftps; }
    else { feet = heightRaw; stories = Math.round(feet / ftps); }
  }
  const acres = lotSf ? lotSf / 43560 : null;
  const farEff = far ? far * (farMult || 1) : null;
  const footprint = (covPct && lotSf) ? covPct / 100 * lotSf : null;
  const farGsf = (farEff && lotSf) ? farEff * lotSf : null;
  const massGsf = (footprint && stories) ? footprint * stories : null;
  const gsfCaps = {};
  if (farGsf) gsfCaps['FAR'] = farGsf;
  if (massGsf) gsfCaps['height × footprint'] = massGsf;
  const envGsf = Object.keys(gsfCaps).length ? Math.min(...Object.values(gsfCaps)) : null;
  const gsfBind = Object.keys(gsfCaps).find(k => gsfCaps[k] === envGsf) || null;
  const uDen = (density && acres) ? Math.floor(density * acres) : null;
  const uEnv = envGsf ? Math.floor(envGsf * eff / avgUnit) : null;
  const uCaps = {};
  if (uDen) uCaps['density'] = uDen;
  if (uEnv) uCaps['envelope GSF'] = uEnv;
  const maxUnits = Object.keys(uCaps).length ? Math.min(...Object.values(uCaps)) : null;
  const uBind = Object.keys(uCaps).find(k => uCaps[k] === maxUnits) || null;
  return { stories, feet, farEff, farGsf, footprint, massGsf, envGsf, gsfBind, uDen, uEnv, maxUnits, uBind };
}

// The GIS attrs the server needs — passing them along skips its zoning re-lookup.
const DEV_ZFIELDS = ['ZONE', 'ZONEDESC', 'MUNICNAME', 'FAR', 'DENSITY', 'MAXLOTCOV',
  'MAXHEIGHT', 'MINLOTSIZE', 'GENRLLUTYPE', 'MIXUSE', 'county', 'is_use_code'];

function devSameParcel(a, b) {
  if (!a || !b) return false;
  if (a.folio && b.folio) return a.folio === b.folio;
  return Math.abs(a.lon - b.lon) < 1e-6 && Math.abs(a.lat - b.lat) < 1e-6;
}

async function updateDevAnalysis(parcel, lon, lat, seq, zoning) {
  const panel = document.getElementById('dev-panel');
  const body = document.getElementById('dev-body');
  if (!panel || !body) return;
  let zslim = null;
  if (zoning && zoning.found && zoning.ZONE) {
    zslim = {};
    for (const k of DEV_ZFIELDS) if (zoning[k] !== undefined) zslim[k] = zoning[k];
  }
  devCurrent = { folio: parcel.folio || null, lon, lat,
    lot_size_sf: Number(parcel.lot_size_sf) || null,
    address: parcel.address || null, geometry: parcel.geometry || null,
    zoning: zslim };
  panel.hidden = false;
  body.innerHTML = `<p style="color:var(--text-muted);font-size:12px">Analyzing zoning envelope…</p>`;
  // Analyze the whole site only when the clicked parcel belongs to it — a click
  // elsewhere always analyzes what was clicked (the site is kept, not shown).
  const inSite = devSite.some(p => devSameParcel(p, devCurrent));
  const parcels = inSite ? devSite : [devCurrent];
  const mySeq = ++devSeq;
  try {
    const r = await fetchJSON('/api/dev-analysis', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parcels: parcels.map(p => ({ lon: p.lon, lat: p.lat, folio: p.folio, lot_size_sf: p.lot_size_sf, zoning: p.zoning || null })) }),
      timeoutMs: 40000,
    });
    if (mySeq !== devSeq) return;   // a newer lookup owns the panel
    // New zone context invalidates checked bonuses from the previous zone.
    const zkey = `${r?.zoning?.muni}|${r?.zoning?.zone}`;
    if (devData && `${devData?.zoning?.muni}|${devData?.zoning?.zone}` !== zkey) devBonusOn.clear();
    devData = r;
    devData._for = { lon, lat, siteMode: inSite };
    renderDevPanel();
  } catch (e) {
    if (mySeq !== devSeq) return;
    body.innerHTML = `<p style="color:var(--text-muted);font-size:12px">Analysis unavailable: ${esc(String(e.message || e))}</p>`;
  }
}

function devSyncSiteLayer() {
  const feats = devSite.filter(p => p.geometry)
    .map(p => ({ type: 'Feature', geometry: p.geometry, properties: { folio: p.folio } }));
  map.getSource('dev_site')?.setData({ type: 'FeatureCollection', features: feats });
}

function devAddCurrent() {
  if (!devCurrent) return;
  if (!devSite.some(p => devSameParcel(p, devCurrent))) devSite.push(devCurrent);
  devSyncSiteLayer();
  updateDevAnalysis(devCurrent, devCurrent.lon, devCurrent.lat, null,
    devCurrent.zoning ? { found: true, ...devCurrent.zoning } : null);
}

function devClearSite() {
  devSite = [];
  devBonusOn.clear();
  devSyncSiteLayer();
  if (devCurrent) updateDevAnalysis(devCurrent, devCurrent.lon, devCurrent.lat);
}

function devToggleBonus(id) {
  if (devBonusOn.has(id)) devBonusOn.delete(id); else devBonusOn.add(id);
  renderDevPanel();
}

function devFmt(n) { return n == null ? '—' : Math.round(n).toLocaleString(); }

function devTableHtml(env, title, badge) {
  const bind = (k) => (k && (k === env.gsfBind || k === env.uBind)) ? ' class="dev-bind"' : '';
  const st = env.stories == null ? '—'
    : (env.stories === Infinity ? 'unlimited' : `${devFmt(env.stories)} (~${devFmt(env.feet)} ft)`);
  return `
    <div class="section-title">${title}${badge ? ` <span class="dev-badge">${badge}</span>` : ''}</div>
    <div class="dev-table">
      <div class="k">Max stories</div><div class="v">${st}</div>
      <div class="k">Density cap</div><div class="v"${bind('density')}>${env.uDen == null ? '—' : devFmt(env.uDen) + ' units'}</div>
      <div class="k">FAR buildable</div><div class="v"${bind('FAR')}>${env.farGsf == null ? '—' : devFmt(env.farGsf) + ' sf' + (env.farEff ? ` (FAR ${(+env.farEff).toFixed(2)})` : '')}</div>
      <div class="k">Max footprint</div><div class="v">${env.footprint == null ? '—' : devFmt(env.footprint) + ' sf'}</div>
      <div class="k">Height × footprint</div><div class="v"${bind('height × footprint')}>${env.massGsf == null ? '—' : devFmt(env.massGsf) + ' sf'}</div>
      <div class="k"><b>Envelope GSF</b></div><div class="v"><b>${env.envGsf == null ? '—' : devFmt(env.envGsf) + ' sf'}</b>${env.gsfBind ? ` <span class="dev-mini">${env.gsfBind} governs</span>` : ''}</div>
      <div class="k"><b>Achievable units</b></div><div class="v"><b>${env.maxUnits == null ? '—' : devFmt(env.maxUnits)}</b>${env.uBind ? ` <span class="dev-mini">${env.uBind} governs</span>` : ''}</div>
    </div>`;
}

function renderDevPanel() {
  const body = document.getElementById('dev-body');
  const d = devData;
  if (!body || !d) return;
  if (!d.found) {
    body.innerHTML = `<p style="color:var(--text-muted);font-size:12px">${esc(d.message || 'No zoning data here.')}</p>`;
    return;
  }
  const z = d.zoning || {}, A = d.assumptions || {}, site = d.site || {};
  const p = [];

  // header: zone (click-through to the governing code text) + site + controls
  const zoneEl = z.code_url
    ? `<a href="${esc(z.code_url)}" target="_blank" title="Open the governing zoning code for ${esc(z.zone || '')}"><b>${esc(z.zone || '?')}</b></a>`
    : `<b>${esc(z.zone || '?')}</b>`;
  p.push(`<div class="dev-zone">${zoneEl}${z.desc ? ' — ' + esc(z.desc) : ''}<span class="dev-mini"> · ${esc(z.muni || '')}</span></div>`);
  const inSite = devSite.some(pp => devSameParcel(pp, devCurrent));
  const siteMode = !!(d._for && d._for.siteMode);
  const siteBits = [
    `${siteMode ? 'Site: ' : ''}${site.parcels || 1} parcel${(site.parcels || 1) > 1 ? 's' : ''}`,
    site.lot_sf ? `${devFmt(site.lot_sf)} sf (${site.acres} ac)` : 'lot size unknown'];
  p.push(`<div class="dev-siteline">${siteBits.join(' · ')}
    ${inSite ? '' : `<button class="dev-btn" onclick="devAddCurrent()">＋ Add parcel to site</button>`}
    ${devSite.length ? `<button class="dev-btn" onclick="devClearSite()">Clear site (${devSite.length})</button>` : ''}</div>`);
  if (devSite.length && !siteMode) {
    p.push(`<div class="dev-mini" style="margin:-4px 0 6px">Showing the clicked parcel — your ${devSite.length}-parcel site is kept (amber outline). Add this parcel to include it.</div>`);
  }

  // active bonus effects
  let storiesTo = null, farMult = 1, unlimited = false;
  for (const b of (d.bonuses || [])) {
    if (!devBonusOn.has(b.id)) continue;
    if (b.effects && 'stories_to' in b.effects) {
      if (b.effects.stories_to === null) unlimited = true; else storiesTo = b.effects.stories_to;
    }
    if (b.effects && b.effects.flr_mult) farMult *= b.effects.flr_mult;
  }
  const heightRaw = unlimited ? null : (z.max_height_raw ?? null);
  const env = devEnvelope(site.lot_sf, z.far, z.density_du_ac, z.lot_coverage_pct,
    heightRaw, unlimited ? null : storiesTo, farMult, A);
  if (unlimited) env.stories = Infinity;
  p.push(devTableHtml(env, devBonusOn.size ? 'Envelope — with selected bonuses' : 'Base zoning envelope',
    d.rulebook && d.rulebook.verify ? 'verify vs code' : null));

  // bonus toggles
  if ((d.bonuses || []).length) {
    p.push(`<div class="section-title">Bonuses — select to apply</div>`);
    for (const b of d.bonuses) {
      p.push(`<label class="sub-toggle dev-bonus"><input type="checkbox" ${devBonusOn.has(b.id) ? 'checked' : ''}
        onchange="devToggleBonus('${esc(b.id)}')" /> <span><b>${esc(b.label)}</b><br/>${esc(b.desc || '')}</span></label>`);
    }
  }

  // setbacks
  if (d.setbacks_ft) {
    const s = d.setbacks_ft;
    p.push(`<div class="dev-table"><div class="k">Setbacks (principal)</div>
      <div class="v">front ${s.front ?? '—'}′ · side ${s.side ?? '—'}′ · rear ${s.rear ?? '—'}′</div></div>`);
  }

  // Live Local secondary scenario
  const ll = d.live_local;
  if (ll && ll.eligible === false) {
    p.push(`<div class="section-title">Secondary scenario — Live Local Act <span class="dev-badge">not eligible</span></div>
      <div class="dev-mini" style="margin-bottom:8px">${esc(ll.note || '')}</div>`);
  } else if (ll) {
    const hb = ll.height_benchmark;
    const tallest = (hb && (hb.matching_zones_at_max || [])[0]?.zone) || '';
    // Statutory height = benchmark within 1 mi, or 3 stories, whichever higher.
    const llStories = ll.height_stories != null ? ll.height_stories : 3;
    const hbLabel = tallest
      ? `${llStories} stories${ll.height_floored ? ' (3-story statutory floor)' : ` (tallest zone within 1 mi: ${esc(tallest)})`}`
      : `${llStories} stories (3-story statutory floor)`;
    // Pass stories directly (stories_override), so height is the LL benchmark, not base zoning.
    const llEnv = devEnvelope(site.lot_sf, ll.far_150pct, ll.density_max_du_ac,
      z.lot_coverage_pct, null, llStories, 1, A);
    p.push(devTableHtml(llEnv, 'Secondary scenario — Live Local Act', 'FL SB 102/328'));
    p.push(`<div class="dev-table">
      <div class="k">Height benchmark</div><div class="v">${hbLabel}</div>
      <div class="k">Density benchmark</div><div class="v">${ll.density_max_du_ac ? ll.density_max_du_ac + ' du/ac (jurisdiction max)' : '—'}</div>
      <div class="k">FAR benchmark</div><div class="v">${ll.far_150pct ? ll.far_150pct + ' (150% of jurisdiction max ' + ll.far_max_in_jurisdiction + ')' : '—'}</div>
    </div>`);
    if (ll.density_note) p.push(`<div class="dev-mini" style="margin:-4px 0 6px">${esc(ll.density_note)}</div>`);
    p.push(`<details class="dev-more"><summary>Live Local requirements &amp; entitlements</summary><ul>` +
      (ll.requirements || []).map(r => `<li>${esc(r)}</li>`).join('') +
      (ll.entitlements || []).map(r => `<li style="color:var(--text-muted)">${esc(r)}</li>`).join('') +
      `</ul>${ll.source_url ? `<a href="${esc(ll.source_url)}" target="_blank">statute ↗</a>` : ''}</details>`);
  }

  // citations, warnings, assumptions
  if (d.rulebook) {
    p.push(`<div class="dev-mini">Rulebook: ${esc(d.rulebook.code_name || d.rulebook.matched)}
      ${d.rulebook.source_url ? `— <a href="${esc(d.rulebook.source_url)}" target="_blank">code ↗</a>` : ''}</div>`);
  }
  for (const w of (d.warnings || [])) p.push(`<div class="dev-warn">⚠ ${esc(w)}</div>`);
  if (A.note) p.push(`<div class="dev-mini">${esc(A.note)} Screening only — verify against the current code text.</div>`);

  body.innerHTML = p.join('');
}

// New-construction heat map: viewport-refreshed master building permits, weighted
// by building floor area. Single-family houses are excluded unless the sub-toggle
// is on (see /api/permit-heat).
let permitHeatOn = false;
function wirePermitHeat() {
  const cb = document.getElementById('lyr-permit-heat');
  const controls = document.getElementById('permit-heat-controls');
  const sfr = document.getElementById('permit-heat-sfr');
  const note = document.getElementById('permit-heat-note');
  if (!cb) return;
  const apply = () => {
    permitHeatOn = cb.checked;
    setVis(['permit-heat'], cb.checked);
    if (controls) controls.hidden = !cb.checked;
    if (note) note.hidden = !cb.checked;
    if (cb.checked) refreshPermitHeat();
    else map.getSource('permit_heat')?.setData({ type: 'FeatureCollection', features: [] });
  };
  cb.addEventListener('change', apply);
  sfr?.addEventListener('change', () => { if (permitHeatOn) refreshPermitHeat(); });
  if (cb.checked) apply();  // honor a click made before the map finished loading
  let t = null;
  map.on('moveend', () => {
    if (!permitHeatOn) return;
    clearTimeout(t);
    t = setTimeout(refreshPermitHeat, 350);
  });
}

async function refreshPermitHeat() {
  const note = document.getElementById('permit-heat-note');
  if (map.getZoom() < 10) {
    map.getSource('permit_heat')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in to load permit density…'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  const bbox = `${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`;
  const inclSfr = document.getElementById('permit-heat-sfr')?.checked ? '&include_sfr=true' : '';
  if (note) { note.textContent = 'Loading new-construction permits…'; note.hidden = false; }
  try {
    const gj = await fetchJSON(`/api/permit-heat?bbox=${bbox}${inclSfr}`, { timeoutMs: 20000 });
    map.getSource('permit_heat')?.setData(gj);
    if (note) {
      const n = (gj.features || []).length;
      note.textContent = n
        ? `${n} new-construction building${n === 1 ? '' : 's'} in view${gj.source ? ' · ' + gj.source : ''}`
        : (gj.note || 'No permit source covers this view.');
      note.hidden = false;
    }
  } catch (e) {
    if (note) { note.textContent = 'Permit heat unavailable here.'; note.hidden = false; }
  }
}

// Live metro zoning overlay: fetch polygons for the viewport when toggled on + zoomed in.
let metroZoningOn = false;
function wireMetroZoning() {
  const cb = document.getElementById('lyr-metro-zoning');
  const massing = () => !!document.getElementById('lyr-zoning-3d')?.checked;
  const note = document.getElementById('metro-zoning-note');
  if (!cb) return;
  cb.addEventListener('change', () => {
    metroZoningOn = cb.checked;
    const vis = cb.checked ? 'visible' : 'none';
    setVis(['metro-zoning-fill', 'metro-zoning-line'], cb.checked && !massing());
    setVis(['metro-zoning-3d'], cb.checked && massing());
    if (cb.checked) refreshMetroZoning(); else map.getSource('metro_zoning')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) note.hidden = !cb.checked;
  });
  // Massing replaces the flat fill rather than stacking on it: two coats of the
  // same polygon at 0.5 and 0.72 opacity just muddies both.
  const m3d = document.getElementById('lyr-zoning-3d');
  if (m3d) {
    m3d.addEventListener('change', () => {
      setVis(['metro-zoning-fill', 'metro-zoning-line'], cb.checked && !m3d.checked);
      setVis(['metro-zoning-3d'], cb.checked && m3d.checked);
      // Tilt on the way in, because a massing layer viewed from directly
      // overhead is indistinguishable from the flat one it replaced.
      if (m3d.checked && cb.checked && map.getPitch() < 20) {
        map.easeTo({ pitch: 55, duration: 600 });
      }
    });
  }

  let t = null;
  map.on('moveend', () => {
    if (!metroZoningOn) return;
    clearTimeout(t);
    t = setTimeout(refreshMetroZoning, 350);
  });
  // hover popup
  let pop = null;
  map.on('mouseenter', 'metro-zoning-fill', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    pop = new maplibregl.Popup({ closeButton: false, offset: 6 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.zone || '')}</b>${p.desc && p.desc !== 'null' ? ' — ' + esc(p.desc) : ''}<br/><span style="color:#64748b">${esc(p.muni || '')}</span>`).addTo(map);
  });
  map.on('mousemove', 'metro-zoning-fill', (e) => { if (pop) pop.setLngLat(e.lngLat); });
  map.on('mouseleave', 'metro-zoning-fill', () => { map.getCanvas().style.cursor = ''; pop && pop.remove(); pop = null; });
}

async function refreshMetroZoning() {
  const note = document.getElementById('metro-zoning-note');
  if (map.getZoom() < 12.5) {
    map.getSource('metro_zoning')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in to load metro zoning…'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  const bbox = `${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`;
  if (note) { note.textContent = 'Loading zoning…'; note.hidden = false; }
  try {
    const gj = await fetchJSON(`/api/zoning-overlay?bbox=${bbox}`, { timeoutMs: 30000 });
    map.getSource('metro_zoning')?.setData(gj);
    if (note) {
      const n = (gj.features || []).length;
      // Two different reasons for an empty result, and they need different
      // actions. METRO_ZONING wires eight Florida cities and not Miami-Dade, so
      // tri-county zoning comes only from the pre-baked GeoJSON that
      // fetch_layers.py downloads -- which is absent on a fresh install. Saying
      // "use the layers above" when those layers were never fetched sends the
      // user to an empty checkbox.
      note.textContent = n
        ? `${n} zoning polygons in view`
        : (map.querySourceFeatures('mdc_zoning').length
            ? 'No live zoning service here — the tri-county layers above cover this area.'
            : 'No zoning for this view. Tri-county zoning is a local layer: run scripts/fetch_layers.py to build it.');
      note.hidden = false;
    }
  } catch (e) {
    if (note) { note.textContent = 'Zoning load failed.'; note.hidden = false; }
  }
}

// National transit routes + stops — fetched by viewport from the BTS National Transit Map.
const NTM_ROUTES = 'https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_National_Transit_Map_Routes/FeatureServer/0';
const NTM_STOPS = 'https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_National_Transit_Map_Stops/FeatureServer/0';
const TRANSIT_LAYERS = ['transit-routes-line', 'transit-stops-pts', 'transit-stops-label-rail', 'transit-stops-label-bus'];
let transitOn = false;
let transitSeq = 0;          // latest-pan-wins: stale viewport responses must not repaint
let transitCounts = { routes: null, stops: null, stopsTruncated: false };

function transitAllowedTypes() {
  // Mode chips gate both routes and stops. '' keeps unknown-mode features visible.
  const on = (id) => { const el = document.getElementById(id); return !el || el.checked; };
  const allowed = [];
  if (on('tmode-rail')) allowed.push('0', '1', '2', '5', '6', '7', '12', '');
  if (on('tmode-bus')) allowed.push('3', '11');
  if (on('tmode-ferry')) allowed.push('4');
  return allowed;
}
function applyTransitModeFilter() {
  const allowed = transitAllowedTypes();
  const routesF = ['in', ['to-string', ['coalesce', ['get', 'route_type'], '']], ['literal', allowed]];
  const stopsF = ['in', ['to-string', ['coalesce', ['get', 'stop_type'], '']], ['literal', allowed]];
  if (map.getLayer('transit-routes-line')) map.setFilter('transit-routes-line', routesF);
  if (map.getLayer('transit-stops-pts')) map.setFilter('transit-stops-pts', stopsF);
  if (map.getLayer('transit-stops-label-rail')) map.setFilter('transit-stops-label-rail', ['all', ['!=', ['get', 'is_bus'], 1], stopsF]);
  if (map.getLayer('transit-stops-label-bus')) map.setFilter('transit-stops-label-bus', ['all', ['==', ['get', 'is_bus'], 1], stopsF]);
}
function updateTransitNote(msg) {
  const note = document.getElementById('transit-note');
  if (!note) return;
  if (msg) { note.textContent = msg; note.hidden = false; return; }
  const bits = [];
  if (transitCounts.routes != null) bits.push(`${transitCounts.routes} route segments`);
  if (transitCounts.stops != null) {
    bits.push(`${transitCounts.stops}${transitCounts.stopsTruncated ? '+' : ''} stops`);
  }
  if (map.getZoom() < 13) bits.push('zoom in for bus stops + names');
  else if (transitCounts.stopsTruncated) bits.push('zoom in to see all');
  note.textContent = bits.length ? bits.join(' · ') : 'No transit here.';
  note.hidden = false;
}
function wireTransitRoutes() {
  const cb = document.getElementById('lyr-transit-routes');
  if (!cb) return;
  const apply = () => {
    transitOn = cb.checked;
    setVis(TRANSIT_LAYERS, cb.checked);
    const legend = document.getElementById('transit-legend');
    const modes = document.getElementById('transit-modes');
    if (legend) legend.hidden = !cb.checked;
    if (modes) modes.hidden = !cb.checked;
    if (cb.checked) { applyTransitModeFilter(); refreshTransit(); }
    else {
      transitSeq++;  // cancel in-flight loads
      map.getSource('transit_routes')?.setData({ type: 'FeatureCollection', features: [] });
      map.getSource('transit_stops')?.setData({ type: 'FeatureCollection', features: [] });
      const note = document.getElementById('transit-note');
      if (note) note.hidden = true;
    }
  };
  cb.addEventListener('change', apply);
  // Wiring runs at map load, which can lag DOM readiness by many seconds on a slow
  // tile fetch — honor any click the user made in that window instead of losing it.
  if (cb.checked) apply();
  for (const id of ['tmode-rail', 'tmode-bus', 'tmode-ferry']) {
    document.getElementById(id)?.addEventListener('change', applyTransitModeFilter);
  }
  let t = null;
  map.on('moveend', () => { if (!transitOn) return; clearTimeout(t); t = setTimeout(refreshTransit, 350); });
  let pop = null;
  map.on('mouseenter', 'transit-routes-line', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    const rtt = String(p.route_type_text || '').replace(/^"+|"+$/g, '');  // BTS quotes the mode text
    pop = new maplibregl.Popup({ closeButton: false, offset: 6 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.route_short_name || '')} ${esc(p.route_long_name || '')}</b><br/><span style="color:#64748b">${esc(rtt)}${p.agency_id ? ' · ' + esc(p.agency_id) : ''}</span>`).addTo(map);
  });
  map.on('mouseleave', 'transit-routes-line', () => { map.getCanvas().style.cursor = ''; pop && pop.remove(); pop = null; });
  map.on('click', 'transit-stops-pts', (e) => {
    const p = (e.features[0] || {}).properties || {};
    const wc = String(p.wheelchair_boarding) === '1' ? ' · wheelchair accessible' : '';
    new maplibregl.Popup({ offset: 8 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.stop_name || 'Transit stop')}</b><br/><span style="color:#64748b">${esc(p.stop_type_text || 'Transit stop')}${wc}</span>`)
      .addTo(map);
  });
  map.on('mouseenter', 'transit-stops-pts', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'transit-stops-pts', () => { map.getCanvas().style.cursor = ''; });
}
function refreshTransit() {
  if (!transitOn) return;  // a moveend debounce can fire after the user toggled off
  const seq = ++transitSeq;
  if (map.getZoom() < 9.5) {
    map.getSource('transit_routes')?.setData({ type: 'FeatureCollection', features: [] });
    map.getSource('transit_stops')?.setData({ type: 'FeatureCollection', features: [] });
    transitCounts = { routes: null, stops: null, stopsTruncated: false };
    updateTransitNote('Zoom in to load transit…');
    return;
  }
  updateTransitNote('Loading transit…');
  refreshTransitRoutes(seq);
  refreshTransitStops(seq);
}
function transitEnvelope() {
  const b = map.getBounds();
  return encodeURIComponent(JSON.stringify({ xmin: b.getWest(), ymin: b.getSouth(), xmax: b.getEast(), ymax: b.getNorth(), spatialReference: { wkid: 4326 } }));
}
async function refreshTransitRoutes(seq) {
  // Dense metros (Manhattan) return ~17 MB of full-resolution route vertices — past any
  // sane timeout. Generalize geometry by zoom; 0.0002° ≈ 20 m, invisible at city scale.
  const z = map.getZoom();
  const offset = z < 11 ? 0.0008 : z < 13 ? 0.0004 : z < 15 ? 0.0002 : 0.00005;
  const url = `${NTM_ROUTES}/query?geometry=${transitEnvelope()}&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=route_short_name,route_long_name,route_type,route_type_text,agency_id&returnGeometry=true&outSR=4326&geometryPrecision=5&maxAllowableOffset=${offset}&resultRecordCount=2000&f=geojson`;
  try {
    const gj = await fetchJSON(url, { timeoutMs: 20000 });
    if (seq !== transitSeq) return;  // user panned again — a newer load owns the layer
    map.getSource('transit_routes')?.setData(gj);
    transitCounts.routes = (gj.features || []).length;
    updateTransitNote();
  } catch (e) { if (seq === transitSeq) updateTransitNote('Transit route load failed.'); }
}
async function refreshTransitStops(seq) {
  // Below z13 only rail/ferry stations load (bus stops would blow the 2000-record cap
  // and bury the map); zoomed in, everything loads and bus names label at z14.5+.
  const includeBus = map.getZoom() >= 13;
  const where = encodeURIComponent(includeBus ? '1=1' : "stop_type NOT IN ('3','11')");
  const url = `${NTM_STOPS}/query?where=${where}&geometry=${transitEnvelope()}&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=stop_name,stop_type,stop_type_text,wheelchair_boarding&returnGeometry=true&outSR=4326&geometryPrecision=6&resultRecordCount=2000&f=geojson`;
  try {
    const gj = await fetchJSON(url, { timeoutMs: 20000 });
    if (seq !== transitSeq) return;
    const feats = gj.features || [];
    for (const f of feats) {
      const p = f.properties || (f.properties = {});
      const st = String(p.stop_type ?? '');
      p.is_bus = (st === '3' || st === '11') ? 1 : 0;
      // BTS wraps mode text in literal quotes ('"Bus"') — strip for display
      if (p.stop_type_text) p.stop_type_text = String(p.stop_type_text).replace(/^"+|"+$/g, '');
    }
    map.getSource('transit_stops')?.setData(gj);
    transitCounts.stops = feats.length;
    transitCounts.stopsTruncated = feats.length >= 2000;
    updateTransitNote();
  } catch (e) { if (seq === transitSeq) updateTransitNote('Transit stop load failed.'); }
}

function wireSfhaToggle() {
  const cb = document.getElementById('lyr-flood-sfha');
  const apply = () => {
    const f = cb.checked ? ['literal', SFHA_CODES] : null;
    map.setFilter('mdc-flood-fill', cb.checked ? ['in', ['get', 'FZONE'], f] : null);
    map.setFilter('bro-flood-fill', cb.checked ? ['in', ['get', 'FLD_ZONE'], f] : null);
    map.setFilter('nfhl-flood-fill', cb.checked ? ['in', ['get', 'FLD_ZONE'], f] : null);
  };
  cb.addEventListener('change', apply);
  apply();
}

// National FEMA flood — pre-baked FL layers + FEMA NFHL polygons loaded by viewport.
const NFHL = 'https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28';
let floodOn = false;
function wireNationalFlood() {
  const cb = document.getElementById('lyr-flood');
  if (!cb) return;
  cb.addEventListener('change', () => {
    floodOn = cb.checked;
    setVis(['mdc-flood-fill', 'bro-flood-fill', 'nfhl-flood-fill', 'nfhl-flood-line'], cb.checked);
    if (cb.checked) refreshNationalFlood();
    else map.getSource('nfhl_flood')?.setData({ type: 'FeatureCollection', features: [] });
  });
  let t = null;
  map.on('moveend', () => { if (!floodOn) return; clearTimeout(t); t = setTimeout(refreshNationalFlood, 350); });
}
async function refreshNationalFlood() {
  const note = document.getElementById('flood-note');
  if (map.getZoom() < 12) {  // FEMA chokes on wide-area requests — keep the bbox small
    map.getSource('nfhl_flood')?.setData({ type: 'FeatureCollection', features: [] });
    if (note) { note.textContent = 'Zoom in closer to load FEMA flood zones.'; note.hidden = false; }
    return;
  }
  const b = map.getBounds();
  // Proxy via our backend — FEMA's server has no CORS + resets connections.
  const bbox = `${b.getWest().toFixed(5)},${b.getSouth().toFixed(5)},${b.getEast().toFixed(5)},${b.getNorth().toFixed(5)}`;
  if (note) { note.textContent = 'Loading flood zones…'; note.hidden = false; }
  try {
    const gj = await fetchJSON(`/api/flood-overlay?bbox=${bbox}&limit=1200`, { timeoutMs: 25000 });
    map.getSource('nfhl_flood')?.setData(gj && gj.features ? gj : { type: 'FeatureCollection', features: [] });
    if (note) { const n = (gj.features || []).length; note.textContent = n ? `${n} FEMA flood polygons in view` : 'No mapped flood zones here.'; note.hidden = false; }
  } catch (e) { if (note) { note.textContent = 'Flood load failed.'; note.hidden = false; } }
}

function wireBasemapButtons() {
  for (const b of document.querySelectorAll('.seg-btn')) {
    b.addEventListener('click', () => setBasemap(b.dataset.basemap));
  }
}

// ---------- hover tag ----------

function wireZoningHover() {
  const tag = document.getElementById('hover-tag');
  let lastKey = null;
  map.on('mousemove', (e) => {
    // Defer to the population choropleth's own ZIP popup when that layer is on.
    if (popGrowthOn) { tag.hidden = true; lastKey = null; return; }
    const overMarker = map.queryRenderedFeatures(e.point, { layers: ['metrorail-pts', 'metromover-pts', 'comps-pts'] }).length;
    if (overMarker) { tag.hidden = true; lastKey = null; return; }
    const z = map.queryRenderedFeatures(e.point, { layers: ['mdc-zoning-fill'] })[0]?.properties
            || map.queryRenderedFeatures(e.point, { layers: ['bro-zoning-fill'] })[0]?.properties;
    const f = map.queryRenderedFeatures(e.point, { layers: ['mdc-flood-fill'] })[0]?.properties
            || map.queryRenderedFeatures(e.point, { layers: ['bro-flood-fill'] })[0]?.properties;
    if (!z && !f) { tag.hidden = true; lastKey = null; return; }
    const key = (z?.ZONE || z?.ZONING || '') + '|' + (f?.FZONE || f?.FLD_ZONE || '');
    if (key !== lastKey) {
      lastKey = key;
      const parts = [];
      if (z) {
        const zone = z.ZONE || z.ZONING || z.SLUC1 || '';
        const desc = z.ZONEDESC || z.DESCRIPTION || z.BPC_BCFUTU || '';
        const muni = z.MUNICNAME || z.AREANAME || '';
        parts.push(`<span class="ht-zone">${esc(zone)}</span><span class="ht-sub">${esc(muni)}${desc ? ' · ' + esc(desc) : ''}</span>`);
      }
      const fz = f?.FZONE || f?.FLD_ZONE;
      if (fz) parts.push(`<span class="ht-flood">Flood ${esc(fz)}</span>`);
      tag.innerHTML = parts.join('');
    }
    tag.hidden = false;
    tag.style.left = e.point.x + 'px';
    tag.style.top = e.point.y + 'px';
  });
  map.on('mouseout', () => { tag.hidden = true; });
}

// ---------- search ----------

function wireSearch() {
  const input = document.getElementById('search-input');
  const clear = document.getElementById('search-clear');
  const list = document.getElementById('search-results');
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    clear.hidden = q.length === 0;
    if (q.length < 3) { list.hidden = true; list.innerHTML = ''; return; }
    timer = setTimeout(() => doSearch(q), 250);
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { list.hidden = true; input.blur(); } });
  clear.addEventListener('click', () => {
    input.value = ''; clear.hidden = true; list.hidden = true; list.innerHTML = ''; input.focus();
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('#search-section')) list.hidden = true; });
}

async function doSearch(q) {
  const list = document.getElementById('search-results');
  list.innerHTML = '<div class="item"><span class="m">Searching…</span></div>';
  list.hidden = false;
  try {
    const r = await fetchJSON(`/api/search?q=${encodeURIComponent(q)}`, { timeoutMs: 20000 });
    if (!r.results || !r.results.length) {
      list.innerHTML = '<div class="item"><span class="m">No matches</span></div>'; return;
    }
    list.innerHTML = r.results.map((res, i) => `
      <div class="item" data-idx="${i}">
        <div class="a">${esc(res.address || '(no address)')}</div>
        <div class="m">${esc([[res.city, res.zip].filter(Boolean).join(' '), res.folio].filter(Boolean).join(' · '))}</div>
      </div>`).join('');
    list.querySelectorAll('.item').forEach((el) => {
      el.addEventListener('click', () => {
        const hit = r.results[+el.dataset.idx];
        list.hidden = true;
        document.getElementById('search-input').value = hit.address || '';
        document.getElementById('search-clear').hidden = false;
        map.flyTo({ center: [hit.lon, hit.lat], zoom: 17, speed: 1.4 });
        doParcelLookup(hit.lon, hit.lat);
      });
    });
  } catch (err) {
    list.innerHTML = `<div class="item"><span class="m">Search failed: ${esc(err.message)}</span></div>`;
  }
}

// ---------- click → parcel lookup ----------

function wireClick() {
  // Layers with their own click behavior — a click on any of these must not ALSO
  // fire the generic parcel lookup (double lookup / spurious flyTo).
  const CLICK_EXCLUDED_LAYERS = [
    'metrorail-pts', 'metromover-pts', 'comps-pts',
    'marks-pts', 'party-props-pts', 'supply-pipeline-pts',
    'zoning-cities-pts', 'zoning-cities-label',
    'targets-pts', 'targets-label',
    'transit-stops-pts', 'transit-stops-label-rail', 'transit-stops-label-bus',
  ];
  map.on('click', (e) => {
    const features = map.queryRenderedFeatures(e.point, { layers: CLICK_EXCLUDED_LAYERS });
    if (features.length) return;
    doParcelLookup(e.lngLat.lng, e.lngLat.lat);
  });
}

let lastSelection = null;
let lastLookup = null;   // snapshot of the current selection for ★ Save
let marksCache = [];     // current saved marks
let lookupSeq = 0;       // latest-click-wins token: stale responses must not touch the DOM

async function doParcelLookup(lon, lat) {
  const seq = ++lookupSeq;
  lastSelection = { lon, lat };
  map.getCanvas().style.cursor = 'wait';
  renderParcelLoading(lon, lat);
  document.getElementById('ll-run').disabled = false;
  // clear PA detail panel from previous click
  document.getElementById('pa-detail-panel').hidden = true;
  document.getElementById('pa-detail-body').innerHTML = '';
  document.getElementById('ll-result').hidden = true;
  document.getElementById('condo-panel').hidden = true;
  document.getElementById('condo-body').innerHTML = '';
  // clear Live Local ring + entity panel (null-safe: map sources may not be
  // ready if the user clicks before the style finishes loading)
  map.getSource('ll-ring')?.setData({ type: 'FeatureCollection', features: [] });
  document.getElementById('entity-panel').hidden = true;
  document.getElementById('entity-body').innerHTML = '';
  document.getElementById('permits-panel').hidden = true;
  document.getElementById('permits-body').innerHTML = '';
  // reset the permits header — it otherwise keeps the previous parcel's count/city
  const permitsSummary = document.querySelector('#permits-panel summary');
  if (permitsSummary) permitsSummary.textContent = 'Building permits';

  try {
    // Per-endpoint degradation: one failed sub-endpoint marks its own section
    // unavailable instead of blanking the whole panel.
    const settled = await Promise.allSettled([
      fetchJSON(`/api/parcel?lon=${lon}&lat=${lat}`, { timeoutMs: 30000 }),
      fetchJSON(`/api/zoning?lon=${lon}&lat=${lat}`, { timeoutMs: 30000 }),
      fetchJSON(`/api/nearest-transit?lon=${lon}&lat=${lat}`),
      fetchJSON(`/api/comps?lon=${lon}&lat=${lat}&radius_ft=2000&listing_type=sale&limit=6`),
      queryOverlayMembership(lon, lat),
    ]);
    if (seq !== lookupSeq) return;  // stale — a newer click owns the panel
    const FALLBACKS = [
      { found: false, _error: true },              // parcel
      { found: false, _error: true },              // zoning
      { _error: true },                            // transit
      { available: false, _error: true },          // comps
      { flood: null, _error: true },               // overlays
    ];
    const [parcel, zoning, transit, comps, overlays] =
      settled.map((res, i) => (res.status === 'fulfilled' ? res.value : FALLBACKS[i]));
    // For counties outside tri-county pre-baked flood, query FEMA NFHL by point.
    if (!overlays.flood) {
      try {
        const fema = await fetchJSON(`/api/flood?lon=${lon}&lat=${lat}`);
        if (fema && fema.found) {
          overlays.flood = { FZONE: fema.zone, ZONESUBTY: fema.subtype, ELEV: fema.bfe, SFHA: fema.sfha };
        }
      } catch (e) { /* non-fatal */ }
      if (seq !== lookupSeq) return;
    }
    // Snapshot for the ★ Save button (marks tab)
    lastLookup = {
      lon, lat,
      folio: parcel.found ? (parcel.folio || null) : null,
      address: parcel.found
        ? [parcel.address, parcel.city].filter(Boolean).join(', ')
        : `${lat.toFixed(5)}, ${lon.toFixed(5)}`,
      zone: zoning.found ? (zoning.ZONE || null) : null,
      county: parcel.county_name || zoning.MUNICNAME || parcel.county || null,
    };
    renderQuickInfo({ parcel, zoning, overlays });
    renderParcel({ parcel, zoning, transit, comps, overlays, lon, lat, seq });
    if (parcel.found && parcel.geometry) {
      map.getSource('selection')?.setData({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: parcel.geometry, properties: {} }],
      });
      upgradeSelectionOutline(parcel, seq);
    } else {
      map.getSource('selection')?.setData({ type: 'FeatureCollection', features: [] });
    }
    drawCompsLayer(comps, lon, lat);
    updateHash();

    // Outside FL/NYC, pull recorded sale(s) from the county recorder where a
    // free endpoint exists (FL & NYC already show sales inline).
    const isFL = (parcel.state === 'FL') || ['miami-dade', 'broward'].includes(parcel.county) || parcel.source === 'statewide';
    const isNYC = parcel.county === 'nyc' || /PLUTO/i.test(parcel.source || '') || /,\s*NY$/.test(parcel.city || '');
    if (!isFL && !isNYC) loadTransactions(lon, lat, seq);

    // Licensed-broker directory for the parcel's market (FL roster; links elsewhere).
    loadBrokerDirectory(parcel, seq);

    // Development-capacity analysis — what could be built on this parcel/site.
    // Reuse the zoning already fetched above so the server skips a re-lookup.
    if (parcel.found) updateDevAnalysis(parcel, lon, lat, seq, zoning);
    else { const dp = document.getElementById('dev-panel'); if (dp && !devSite.length) dp.hidden = true; }

    // Live Local Act only applies in Florida — only show the tool there.
    const inFL = (parcel.state === 'FL') ||
      ['miami-dade', 'broward'].includes(parcel.county) || parcel.source === 'statewide' ||
      (zoning.found && /,\s*FL$/.test(zoning.MUNICNAME || ''));
    const llPanel = document.getElementById('ll-panel');
    llPanel.hidden = !inFL;
    document.getElementById('ll-run').disabled = !inFL || !parcel.found;

    // If Miami-Dade parcel, asynchronously fetch full PA record + building permits
    if (parcel.found && parcel.county === 'miami-dade' && parcel.folio) {
      loadPropertyDetails(parcel.folio, seq);
      loadPermits({ folio: parcel.folio, lon, lat }, seq);
      // TRUE_SITE_UNIT populated ⇒ this parcel is one unit of a multi-unit
      // building — worth checking for a condo roster (cheap; the endpoint itself
      // answers is_condo:false with no cost if it's a false-positive signal).
      if (parcel.unit) loadCondoUnits(parcel.folio, parcel.unit, seq);
    } else {
      // National: building permits by point (nearest harvested city permit service)
      loadPermits({ lon, lat }, seq);
    }
    // If the owner is a business entity, look up who's behind it (Sunbiz for FL,
    // OpenCorporates elsewhere).
    if (parcel.found && parcel.owner && ENTITY_RX.test(parcel.owner)) {
      const ownerState = parcel.state ||
        (['miami-dade', 'broward'].includes(parcel.county) || parcel.source === 'statewide' ? 'FL' : null);
      loadEntity(parcel.owner, ownerState, seq);
    }
  } catch (err) {
    if (seq !== lookupSeq) return;
    document.getElementById('parcel-body').innerHTML = `<p style="color:var(--danger)">Lookup failed: ${esc(err.message)}</p>`;
  } finally {
    if (seq === lookupSeq) map.getCanvas().style.cursor = '';
  }
}

// ---------- overlay membership (in-browser PIP against cached GeoJSON) ----------

const OVERLAY_URLS = {
  oz: '/data/mdc_opportunity_zones.geojson',
  cra: '/data/mdc_cra.geojson',
  brownfield: '/data/mdc_brownfields.geojson',
  enterprise: '/data/mdc_enterprise_zones.geojson',
  rtz: '/data/mdc_rapid_transit_zones.geojson',
  historic: '/data/mdc_historic_districts.geojson',
  flood_mdc: '/data/mdc_flood.geojson',
  flood_bro: '/data/bro_flood.geojson',
  parks: '/data/mdc_parks.geojson',
};
const OVERLAY_CACHE = {};
const OVERLAY_LOADERS = {};
function loadOverlay(key) {
  if (OVERLAY_CACHE[key]) return Promise.resolve(OVERLAY_CACHE[key]);
  if (OVERLAY_LOADERS[key]) return OVERLAY_LOADERS[key];
  OVERLAY_LOADERS[key] = fetchJSON(OVERLAY_URLS[key], { timeoutMs: 45000 })
    .then((gj) => { OVERLAY_CACHE[key] = gj.features || []; return OVERLAY_CACHE[key]; })
    .catch(() => {
      // Transient failure: return [] for THIS query but leave the cache/loader
      // unset so the next parcel click retries instead of reporting "no overlay"
      // for the rest of the session.
      delete OVERLAY_LOADERS[key];
      return [];
    });
  return OVERLAY_LOADERS[key];
}

async function queryOverlayMembership(lon, lat) {
  const point = [lon, lat];
  await Promise.all(Object.keys(OVERLAY_URLS).map(loadOverlay));
  const out = {};
  for (const key of Object.keys(OVERLAY_URLS)) {
    const hit = (OVERLAY_CACHE[key] || []).find((f) => pointInGeom(point, f.geometry));
    out[key] = hit ? (hit.properties || {}) : null;
  }
  // Normalize: choose whichever flood layer matched
  out.flood = out.flood_mdc || out.flood_bro || null;
  if (out.flood_bro) {
    // Translate FLD_ZONE -> FZONE for unified UI
    out.flood = { FZONE: out.flood_bro.FLD_ZONE, ZONESUBTY: out.flood_bro.ZONE_SUBTY, ELEV: out.flood_bro.STATIC_BFE };
  }
  return out;
}

function pointInGeom(pt, geom) {
  if (!geom) return false;
  if (geom.type === 'Polygon') return pointInRings(pt, geom.coordinates);
  if (geom.type === 'MultiPolygon') return geom.coordinates.some((rings) => pointInRings(pt, rings));
  return false;
}
function pointInRings(pt, rings) {
  if (!rings.length) return false;
  if (!pointInRing(pt, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) if (pointInRing(pt, rings[i])) return false;
  return true;
}
function pointInRing(pt, ring) {
  const [x, y] = pt;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (((yi > y) !== (yj > y)) && (x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-12) + xi)) inside = !inside;
  }
  return inside;
}

// ---------- sidebar render ----------

function renderParcelLoading(lon, lat) {
  document.getElementById('quick-info').hidden = true;
  const body = document.getElementById('parcel-body');
  body.classList.remove('empty');
  body.innerHTML = `<p style="color:var(--text-muted)">Loading parcel…</p>`;
}

function countyChip(c, name) {
  if (c === 'miami-dade') return '<span class="county-chip mdc">Miami-Dade</span>';
  if (c === 'broward') return '<span class="county-chip bro">Broward</span>';
  if (c === 'palm-beach') return '<span class="county-chip pb">Palm Beach</span>';
  // Any other Florida county (statewide cadastral)
  const label = name || (c ? c.replace(/-/g, ' ').replace(/\b\w/g, m => m.toUpperCase()) : '');
  return label ? `<span class="county-chip fl">${esc(label)}</span>` : '';
}

function renderQuickInfo({ parcel, zoning, overlays }) {
  const qi = document.getElementById('quick-info');
  if (!zoning.found && !parcel.found) { qi.hidden = true; return; }

  const flags = [];
  if (overlays.oz) flags.push(`<span class="badge oz">OZ</span>`);
  if (overlays.cra) flags.push(`<span class="badge cra">CRA</span>`);
  if (overlays.brownfield) flags.push(`<span class="badge bf">Brownfield</span>`);
  if (overlays.rtz) flags.push(`<span class="badge rtz">RTZ</span>`);
  if (overlays.historic) flags.push(`<span class="badge hist">Historic</span>`);
  if (overlays.flood && overlays.flood.FZONE) {
    const fz = overlays.flood.FZONE.toLowerCase().replace(/[^a-z]/g, '');
    flags.push(`<span class="badge flood-${fz}">Flood ${esc(overlays.flood.FZONE)}</span>`);
  }

  const zoneCode = esc(zoning.ZONE || '—');
  const desc = esc(zoning.ZONEDESC || zoning.SHORTDESC || '');
  const muni = esc(zoning.MUNICNAME || '');
  const cnty = parcel.county || zoning.county;
  const lot = parcel.lot_size_sf ? `${fmtNum(parcel.lot_size_sf)} sf` : '—';

  // Statewide parcels (FDOR) carry values + use code, not FAR/height. Show the
  // most useful 3 stats depending on what we have.
  let statsHtml;
  if (zoning.is_use_code || parcel.source === 'statewide') {
    const jv = parcel.just_value ? fmtUSD(parcel.just_value) : '—';
    const yr = esc(parcel.year_built || '—');
    statsHtml = `
      <div class="qi-stat"><div class="v">${jv}</div><div class="k">Just value</div></div>
      <div class="qi-stat"><div class="v">${yr}</div><div class="k">Year built</div></div>
      <div class="qi-stat"><div class="v">${lot}</div><div class="k">Lot size</div></div>`;
  } else {
    const far = esc(zoning.FAR || '—');
    const ht = zoning.MAXHEIGHT ? `${esc(zoning.MAXHEIGHT)} ft` : '—';
    statsHtml = `
      <div class="qi-stat"><div class="v">${far}</div><div class="k">Max FAR</div></div>
      <div class="qi-stat"><div class="v">${ht}</div><div class="k">Max height</div></div>
      <div class="qi-stat"><div class="v">${lot}</div><div class="k">Lot size</div></div>`;
  }

  const zoneLabel = zoning.is_use_code
    ? `${zoneCode} <span style="font-size:11px;font-weight:500;color:var(--text-dim)">use code</span>`
    : zoneCode;

  const alreadySaved = lastLookup && marksCache.some((m) =>
    (lastLookup.folio && m.folio === lastLookup.folio) ||
    (Math.abs(m.lon - lastLookup.lon) < 1e-6 && Math.abs(m.lat - lastLookup.lat) < 1e-6));

  qi.innerHTML = `
    <div class="qi-head">
      <div class="qi-zone">${zoneLabel}${countyChip(cnty, parcel.county_name || zoning.MUNICNAME)}</div>
      <div class="qi-muni">${muni}</div>
    </div>
    <div class="qi-desc">${desc}</div>
    <div class="qi-stats">${statsHtml}</div>
    ${flags.length ? `<div class="qi-flags">${flags.join('')}</div>` : ''}
    <button id="qi-save" class="save-btn${alreadySaved ? ' saved' : ''}" ${alreadySaved ? 'disabled' : ''}>
      ${alreadySaved ? '★ Saved' : '★ Save address'}
    </button>
  `;
  qi.hidden = false;
  const btn = document.getElementById('qi-save');
  if (btn && !alreadySaved) btn.addEventListener('click', saveMark);
}

function renderParcel({ parcel, zoning, transit, comps, overlays, lon, lat, seq }) {
  const body = document.getElementById('parcel-body');
  body.classList.remove('empty');
  const parts = [];

  parts.push(`<div class="section-title">Parcel</div>`);
  if (parcel.found) {
    if (parcel.county === 'miami-dade') {
      const addr = [parcel.address, parcel.unit].filter(Boolean).join(' #');
      const cityzip = [parcel.city, parcel.zip].filter(Boolean).join(' ');
      parts.push(field('Folio', safeUrl(parcel.pa_url) ? `<a href="${safeUrl(parcel.pa_url)}" target="_blank" rel="noopener">${esc(parcel.folio)}</a>` : (esc(parcel.folio) || '—')));
      parts.push(field('Address', esc([addr, cityzip].filter(Boolean).join(', ')) || '—'));
      parts.push(field('Owner', esc(parcel.owner) || '—'));
      parts.push(field('Lot', parcel.lot_size_sf ? `${fmtNum(parcel.lot_size_sf)} sf (${(parcel.lot_size_sf / 43560).toFixed(2)} ac)` : '—'));
      parts.push(field('Building', parcel.building_area_sf ? `${fmtNum(parcel.building_area_sf)} sf` : '—'));
      parts.push(field('Year built', parcel.year_built ? fmtNum(parcel.year_built, { maximumFractionDigits: 0 }) : '—'));
      parts.push(`<div class="tax-link" style="margin-top:8px"><a href="${recorderUrl('FL', 'Miami-Dade')}" target="_blank" rel="noopener">County assessor, recorder &amp; tax records →</a></div>`);
      parts.push(`<p style="font-size:11px; color:var(--text-muted); margin-top:6px;">Full sales history, owner trail, and 3-year assessment loading below ↓</p>`);
    } else if (parcel.county === 'broward') {
      parts.push(field('Folio', safeUrl(parcel.pa_url) ? `<a href="${safeUrl(parcel.pa_url)}" target="_blank" rel="noopener">${esc(parcel.folio)}</a>` : (esc(parcel.folio) || '—')));
      parts.push(field('Address', esc(parcel.address) || '—'));
      parts.push(field('City', esc(parcel.city) || '—'));
      parts.push(field('Use code', esc(parcel.use_code) || '—'));
      parts.push(`<div class="tax-link">${taxBillLink('broward', parcel.folio)}</div>`);
      parts.push(`<div class="tax-link" style="margin-top:6px"><a href="${recorderUrl('FL', 'Broward')}" target="_blank" rel="noopener">County assessor, recorder &amp; tax records →</a></div>`);
      parts.push(`<p style="font-size:11px; color:var(--text-muted); margin-top:6px;">Broward — BCPA full record at the link above.</p>`);
    } else {
      // Generic card: FL statewide (FDOR), free-state adapters (MA/WI/…), or Regrid.
      const isFL = parcel.source === 'statewide';
      const st = parcel.state || (isFL ? 'FL' : null);
      parts.push(field('Parcel ID', esc(parcel.folio) || '—'));
      parts.push(field('County', esc([parcel.county_name, st].filter(Boolean).join(', ')) || '—'));
      parts.push(field('Address', esc([parcel.address, parcel.city, parcel.zip].filter(Boolean).join(', ')) || '—'));
      parts.push(field('Owner', esc(parcel.owner) || '—'));
      if (parcel.owner_mailing) parts.push(field('Owner mail', esc(parcel.owner_mailing)));
      if (parcel.use_description || parcel.use_code)
        parts.push(field('Use', esc([parcel.use_code, parcel.use_description].filter(Boolean).join(' · '))));
      if (parcel.lot_size_sf) parts.push(field('Lot', `${fmtNum(parcel.lot_size_sf)} sf (${(parcel.lot_size_sf / 43560).toFixed(2)} ac)`));
      if (parcel.building_area_sf) parts.push(field('Building', `${fmtNum(parcel.building_area_sf)} sf`));
      if (parcel.lot_frontage_ft || parcel.lot_depth_ft) parts.push(field('Lot dims', `${esc(parcel.lot_frontage_ft || '?')} ft × ${esc(parcel.lot_depth_ft || '?')} ft`));
      if (parcel.units_total) parts.push(field('Units', `${fmtNum(parcel.units_total)}${parcel.res_units != null ? ' (' + fmtNum(parcel.res_units) + ' resid)' : ''}${parcel.floors ? ' · ' + esc(parcel.floors) + ' floors' : ''}`));
      if (parcel.building_class) parts.push(field('Bldg class', esc(parcel.building_class) + (parcel.building_class_desc ? ` <span style="color:var(--text-muted)">— ${esc(parcel.building_class_desc)}</span>` : '')));
      if (parcel.year_built) parts.push(field('Year built', `${esc(parcel.year_built)}${parcel.effective_year ? ' (eff. ' + esc(parcel.effective_year) + ')' : ''}`));
      if (parcel.zoning_hint) parts.push(field('Zoning', `${esc(parcel.zoning_hint)}${parcel.far_note ? ' · FAR ' + esc(parcel.far_note) : ''}`));
      if (parcel.historic) parts.push(field('Historic', `<span style="color:var(--warn)">${esc(parcel.historic)}</span>`));
      // NYC admin districts (ZoLa parity)
      if (parcel.community_district || parcel.council_district) {
        const admin = [parcel.community_district ? 'CD ' + parcel.community_district : null,
                       parcel.council_district ? 'Council ' + parcel.council_district : null,
                       parcel.school_district ? 'School ' + parcel.school_district : null,
                       parcel.police_precinct ? 'Pct ' + parcel.police_precinct : null].filter(Boolean).join(' · ');
        if (admin) parts.push(field('Districts', esc(admin)));
      }
      // NYC official record links (ZoLa parity: ACRIS deeds, DOB permits, ZoLa, tax map)
      if (parcel.nyc_links && Object.keys(parcel.nyc_links).length) {
        const L = parcel.nyc_links;
        const lk = [];
        if (safeUrl(L.acris)) lk.push(`<a href="${safeUrl(L.acris)}" target="_blank" rel="noopener">Deeds (ACRIS)</a>`);
        if (safeUrl(L.dob)) lk.push(`<a href="${safeUrl(L.dob)}" target="_blank" rel="noopener">DOB / permits</a>`);
        if (safeUrl(L.zola)) lk.push(`<a href="${safeUrl(L.zola)}" target="_blank" rel="noopener">ZoLa</a>`);
        if (safeUrl(L.dtm)) lk.push(`<a href="${safeUrl(L.dtm)}" target="_blank" rel="noopener">Tax map</a>`);
        if (lk.length) parts.push(`<div class="tax-link" style="margin-top:6px">${lk.join(' · ')}</div>`);
      }

      // Values
      const valLabel = isFL ? 'Assessment (FDOR 2025)' : `Assessment (${esc(parcel.source || 'roll')})`;
      if (parcel.just_value || parcel.land_value || parcel.assessed_value) {
        parts.push(`<div class="section-title">${valLabel}</div>`);
        if (parcel.just_value) parts.push(field(isFL ? 'Just value' : 'Total value', fmtUSD(parcel.just_value)));
        if (parcel.land_value) parts.push(field('Land value', fmtUSD(parcel.land_value)));
        if (parcel.improvement_value) parts.push(field('Improvement', fmtUSD(parcel.improvement_value)));
        if (parcel.assessed_value) parts.push(field('Assessed', fmtUSD(parcel.assessed_value)));
        if (parcel.taxable_value) parts.push(field('Taxable', fmtUSD(parcel.taxable_value)));
      }
      const tl = taxBillLink(parcel.county_name, parcel.folio);
      if (tl) parts.push(`<div class="tax-link">${tl}</div>`);

      // Transactions / deeds
      renderTransactions(parts, parcel, st);

      if (parcel.legal) {
        parts.push(`<div class="section-title">Legal</div>`);
        parts.push(`<div style="font-size:11px; color:var(--text-muted); font-family:monospace; white-space:pre-wrap; line-height:1.4">${esc(parcel.legal)}</div>`);
      }
      const note = isFL
        ? `Source: FDOR statewide cadastral. Land-use code shown instead of zoning where no zoning service exists. Flood from FEMA NFHL.`
        : `Source: ${esc(parcel.source)}. Flood from FEMA NFHL.`;
      parts.push(`<p style="font-size:11px; color:var(--text-muted); margin-top:8px;">${note}</p>`);
    }
  } else if (parcel._error) {
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Parcel record unavailable — the parcel service didn't respond. Try clicking again.</p>`);
  } else if (parcel.needs_regrid_key) {
    parts.push(`<p style="color:var(--text-muted)">Parcel details aren't free in <b>${esc(parcel.state || 'this state')}</b>${parcel.county_name ? ' (' + esc(parcel.county_name) + ')' : ''}. Flood, deeds &amp; demographics below still work here.</p>
      <p style="font-size:11.5px; color:var(--text-muted); margin-top:6px;">To add owner / value / sales, connect a <a href="https://regrid.com/api" target="_blank" rel="noopener">Regrid</a> key <span style="color:var(--text-dim)">(set <code>REGRID_TOKEN</code>, then restart)</span>.</p>`);
    const recUrl = recorderUrl(parcel.state, parcel.county_name);
    if (recUrl) parts.push(`<div class="tax-link" style="margin-top:8px"><a href="${recUrl}" target="_blank" rel="noopener">County assessor, recorder &amp; tax records →</a></div>`);
  } else {
    parts.push(`<p style="color:var(--text-muted)">No parcel at this point (likely water or right-of-way).</p>`);
  }

  parts.push(`<div class="section-title">${zoning.is_use_code ? 'Land use (assessor)' : 'Zoning'}</div>`);
  if (zoning.found) {
    parts.push(field(zoning.is_use_code ? 'Use code' : 'Code', `<b>${esc(zoning.ZONE) || '—'}</b>`));
    if (zoning.is_use_code) parts.push(`<div style="font-size:11px;color:var(--warn);margin:2px 0 4px">Assessor tax classification — <b>not</b> a zoning district. No published zoning service here.</div>`);
    parts.push(field('Description', esc(zoning.ZONEDESC || zoning.SHORTDESC) || '—'));
    if (zoning.GENRLLUTYPE) parts.push(field('Category', esc(zoning.GENRLLUTYPE)));
    if (zoning.FAR) parts.push(field('Max FAR', esc(zoning.FAR)));
    if (zoning.MAXHEIGHT) parts.push(field('Max height', `${esc(zoning.MAXHEIGHT)} ft`));
    if (zoning.MINLOTSIZE) parts.push(field('Min lot', esc(zoning.MINLOTSIZE)));
    if (zoning.DENSITY) parts.push(field('Density', `${esc(zoning.DENSITY)} du/ac`));
    if (zoning.MIXUSE) parts.push(field('Mixed use', esc(zoning.MIXUSE)));
    if (zoning.MAXLOTCOV) parts.push(field('Max lot cov', `${esc(zoning.MAXLOTCOV)}%`));
    if (zoning.source) parts.push(field('Source', `<span style="color:var(--text-dim)">${esc(zoning.source)}</span>`));

    // Definition + possible bonuses + code link (national)
    if (zoning.code_link || zoning.bonuses) {
      parts.push(`<div class="section-title">Definition &amp; bonuses</div>`);
      if (zoning.bonuses) parts.push(`<div style="font-size:11.5px; color:var(--text-muted); line-height:1.5">${esc(zoning.bonuses)}</div>`);
      if (safeUrl(zoning.code_link)) parts.push(`<div class="tax-link" style="margin-top:6px"><a href="${safeUrl(zoning.code_link)}" target="_blank" rel="noopener">Full ${esc(zoning.ZONE || '')} definition &amp; bonus provisions →</a></div>`);
    }

    if (parcel.found && parcel.lot_size_sf) {
      const lot = Number(parcel.lot_size_sf);
      const far = parseFloat(zoning.FAR);
      const dens = parseFloat(zoning.DENSITY);
      if ((Number.isFinite(far) && far > 0) || (Number.isFinite(dens) && dens > 0)) {
        parts.push(`<div class="section-title">Theoretical envelope</div>`);
        if (Number.isFinite(far) && far > 0) {
          parts.push(field('Max GSF', `${fmtNum(Math.round(lot * far))} sf <span style="color:var(--text-dim)">(${far} FAR × ${fmtNum(lot)})</span>`));
        }
        if (Number.isFinite(dens) && dens > 0) {
          const units = Math.floor((lot / 43560) * dens);
          parts.push(field('Max units', `${fmtNum(units)} <span style="color:var(--text-dim)">(${dens}/ac × ${(lot / 43560).toFixed(2)} ac)</span>`));
        }
        parts.push(`<div style="font-size:10.5px;color:var(--text-dim);margin-top:4px;line-height:1.45">Gross theoretical maximum before setbacks, lot coverage, parking, height/bulk, overlays, and platting. Actual yield is typically lower — not a unit count.</div>`);
      }
    }
  } else if (zoning._error) {
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Zoning unavailable — the zoning service didn't respond.</p>`);
  } else {
    parts.push(`<p style="color:var(--text-muted)">No zoning polygon at this point.</p>`);
  }

  // Flood (always shown)
  parts.push(`<div class="section-title">Flood plain</div>`);
  if (overlays.flood && overlays.flood.FZONE) {
    const fz = overlays.flood.FZONE;
    const meta = FLOOD_PALETTE[fz] || { label: '' };
    const isSfha = SFHA_CODES.includes(fz);
    parts.push(field('FEMA zone', `<span class="badge flood-${esc(String(fz).toLowerCase())}">${esc(fz)}</span> <span style="color:var(--text-dim)">${meta.label.split('—').slice(1).join('—').trim()}</span>`));
    if (overlays.flood.ZONESUBTY) parts.push(field('Subtype', esc(overlays.flood.ZONESUBTY)));
    if (overlays.flood.ELEV != null && overlays.flood.ELEV !== -9999 && overlays.flood.ELEV !== 0) {
      parts.push(field('Base elev.', `${esc(overlays.flood.ELEV)} ft NAVD88`));
    }
    parts.push(field('Risk', isSfha ? '<b style="color:var(--danger)">High (SFHA)</b>' : '<span style="color:var(--good)">Moderate / low</span>'));
  } else if (overlays._error) {
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Flood check unavailable — overlay data didn't load.</p>`);
  } else {
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Outside any FEMA flood polygon.</p>`);
  }

  if (overlays.oz || overlays.cra || overlays.brownfield) {
    parts.push(`<div class="section-title">Incentives</div>`);
    if (overlays.oz) parts.push(field('Opportunity Zone', `Census Tract ${esc(overlays.oz.GEOID10 || '')} <span style="color:var(--text-dim)">— federal QOZ</span>`));
    if (overlays.cra) parts.push(field('CRA', `${esc(overlays.cra.LOCATION || '')} — ${fmtNum(overlays.cra.ACRE, { maximumFractionDigits: 0 })} ac`));
    if (overlays.brownfield) parts.push(field('Brownfield', esc(overlays.brownfield.SITENAME || overlays.brownfield.NAME || 'site')));
  }

  // Transit — nearest stop per mode (BTS National Transit Map, national) plus the
  // richer Miami-Dade Metrorail/Metromover detail where it applies. Each row names
  // the mode so a bus stop never masquerades as a train station.
  const MAX_TRANSIT_FT = 25 * 5280;
  const tRows = [];
  if (transit && transit.available !== false && !transit._error) {
    if (transit.metrorail && transit.metrorail.feet <= MAX_TRANSIT_FT) tRows.push(field('Metrorail', `${esc(transit.metrorail.name)} — ${fmtFeet(transit.metrorail.feet)}`));
    if (transit.metromover && transit.metromover.feet <= MAX_TRANSIT_FT) tRows.push(field('Metromover', `${esc(transit.metromover.name)} — ${fmtFeet(transit.metromover.feet)}`));
    // National nearest rail: skip in Miami when Metrorail already covers it
    if (transit.rail && !transit.metrorail) tRows.push(field(esc(transit.rail.mode || 'Rail'), `${esc(transit.rail.name)} — ${fmtFeet(transit.rail.feet)}`));
    if (transit.bus) tRows.push(field('Bus stop', `${esc(transit.bus.name)} — ${fmtFeet(transit.bus.feet)}`));
    if (transit.ferry) tRows.push(field('Ferry', `${esc(transit.ferry.name)} — ${fmtFeet(transit.ferry.feet)}`));
  }
  if (tRows.length) {
    parts.push(`<div class="section-title">Transit — nearest by mode</div>`);
    parts.push(...tRows);
  } else if (transit && transit._error) {
    parts.push(`<div class="section-title">Transit</div>`);
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Transit distances unavailable.</p>`);
  }

  if (comps && comps._error) {
    parts.push(`<div class="section-title">Comps</div>`);
    parts.push(`<p style="color:var(--text-muted); font-size:11.5px;">Comps unavailable — the comps service didn't respond.</p>`);
  }
  if (comps && comps.available) {
    parts.push(`<div class="section-title">Comps (within ${fmtFeet(comps.radius_ft)})</div>`);
    const stats = comps.stats || {};
    if (stats.sale && stats.sale.count) parts.push(field('For sale', `${stats.sale.count} — median ${stats.sale.median_psf ? '$' + stats.sale.median_psf + '/sf' : '—'}`));
    if (stats.rent && stats.rent.count) parts.push(field('For rent', `${stats.rent.count} — median ${stats.rent.median_psf ? '$' + stats.rent.median_psf + '/sf' : '—'}`));
    // Rental rates by bedroom
    const rbb = comps.rent_by_bed || {};
    const rentRows = Object.entries(rbb);
    if (rentRows.length) {
      parts.push(`<div class="section-title">Rental rates (within ${fmtFeet(comps.radius_ft)})</div>`);
      parts.push(`<table class="pa-table"><thead><tr><th>Type</th><th class="r">Median rent</th><th class="r">$/sf</th><th class="r">n</th></tr></thead><tbody>${rentRows.map(([bed, s]) => `<tr><td>${esc(bed)}</td><td class="r num">${s.median_rent ? fmtUSD(s.median_rent) + '/mo' : '—'}</td><td class="r num">${s.median_psf ? '$' + esc(s.median_psf) : '—'}</td><td class="r num">${esc(s.count)}</td></tr>`).join('')}</tbody></table>`);
    }
    for (const c of (comps.listings || []).slice(0, 5)) {
      const psf = c.price_per_sqft ? `${fmtUSD(c.price_per_sqft)}/sf` : '';
      const compLabel = esc(c.address || c.id);
      const compLink = safeUrl(c.url)
        ? `<a class="addr" href="${safeUrl(c.url)}" target="_blank" rel="noopener">${compLabel}</a>`
        : `<span class="addr">${compLabel}</span>`;
      parts.push(`<div class="comp"><div class="row1">${compLink}<span class="dist">${fmtFeet(c.distance_ft)}</span></div><div class="row2">${esc(c.beds || '?')}bd / ${esc(c.baths || '?')}ba · ${c.sqft ? fmtNum(c.sqft) + ' sf' : '—'} · ${fmtUSD(c.price)} ${psf ? '(' + psf + ')' : ''}</div></div>`);
    }
  }

  // Nearby new developments (curated buildings with developer + website)
  if (comps && comps.buildings && comps.buildings.length) {
    parts.push(`<div class="section-title">Nearby developments (${comps.buildings.length})</div>`);
    for (const b of comps.buildings.slice(0, 6)) {
      const meta = [b.status, b.unit_count ? b.unit_count + ' units' : null,
                    b.height_stories ? b.height_stories + ' stories' : null,
                    b.pct_sold != null ? Math.round(b.pct_sold) + '% sold' : null].filter(Boolean).join(' · ');
      const nameHtml = safeUrl(b.website)
        ? `<a class="addr" href="${safeUrl(b.website)}" target="_blank" rel="noopener">${esc(b.name)}</a>`
        : `<span class="addr">${esc(b.name)}</span>`;
      parts.push(`<div class="comp">
        <div class="row1">${nameHtml}<span class="dist">${b.distance_ft != null ? fmtFeet(b.distance_ft) : ''}</span></div>
        ${b.developer ? `<div class="row2">Developer: <b>${esc(b.developer)}</b>${b.architect ? ' · Arch: ' + esc(b.architect) : ''}</div>` : ''}
        ${meta ? `<div class="row2">${esc(meta)}</div>` : ''}
      </div>`);
    }
  }

  parts.push(`<div class="section-title">Area context</div>`);
  parts.push(`<div id="area-context"><span style="color:var(--text-dim); font-size:11.5px">Loading demographics, wetlands…</span></div>`);

  parts.push(`<div class="section-title">Site screening <span style="font-weight:400;color:var(--text-dim)">— national</span></div>`);
  parts.push(`<div id="site-screen"><span style="color:var(--text-dim); font-size:11.5px">Loading elevation, environmental, hazards…</span></div>`);

  const isIL = parcel.state === 'IL';
  if (parcel.county === 'nyc' && parcel.folio) {
    parts.push(`<div class="section-title">ACRIS — deeds &amp; mortgages</div>`);
    parts.push(`<div id="acris"><span style="color:var(--text-dim); font-size:11.5px">Pulling recorded documents…</span></div>`);
  }
  if ((parcel.county === 'nyc' && parcel.folio) || isIL) {
    parts.push(`<div class="section-title">Investment sales <span style="font-weight:400;color:var(--text-dim)">— ZIP, rolling window</span></div>`);
    parts.push(`<div id="inv-sales"><span style="color:var(--text-dim); font-size:11.5px">Loading recorded sales…</span></div>`);
  }

  parts.push(`<div class="section-title">Supply pipeline <span style="font-weight:400;color:var(--text-dim)">— nearby competing permits</span></div>`);
  parts.push(`<div id="supply-pipeline"><span style="color:var(--text-dim); font-size:11.5px">Scanning nearby new-construction permits…</span></div>`);

  if (parcel.zip) {
    parts.push(`<div class="section-title">Financing activity <span style="font-weight:400;color:var(--text-dim)">— SBA RE loans, ZIP</span></div>`);
    parts.push(`<div id="financing"><span style="color:var(--text-dim); font-size:11.5px">Loading SBA loans…</span></div>`);
  }

  parts.push(`<div class="section-title">Market rent <span style="font-weight:400;color:var(--text-dim)">— Zillow Research</span></div>`);
  parts.push(`<div id="market-rent"><span style="color:var(--text-dim); font-size:11.5px">Loading…</span></div>`);

  parts.push(`<div class="section-title">Walkable amenities <span style="font-weight:400;color:var(--text-dim)">— OSM, ½ mi</span></div>`);
  parts.push(`<div id="amenities"><span style="color:var(--text-dim); font-size:11.5px">Counting restaurants, groceries, parks…</span></div>`);

  const lotSf = parcel.lot_size_sf ? Number(parcel.lot_size_sf) : null;
  const far = zoning.found && zoning.FAR ? parseFloat(zoning.FAR) : null;
  const density = zoning.found && zoning.DENSITY ? parseFloat(zoning.DENSITY) : null;
  if (lotSf && (far || density)) {
    parts.push(`<div class="section-title">Quick pro-forma <span style="font-weight:400;color:var(--text-dim)">— screening only</span></div>`);
    parts.push(`<div id="proforma"></div>`);
  }

  parts.push(`<div class="section-title">Point</div>`);
  parts.push(field('Lat / Lon', `${lat.toFixed(6)}, ${lon.toFixed(6)}`));
  parts.push(`<div style="margin-top:10px;display:flex;gap:6px"><button class="primary-btn" id="report-btn" type="button" style="flex:1">⎙ Diligence report</button><button class="link-btn" id="compare-btn" type="button" style="flex:0 0 auto;width:auto">+ Compare</button></div>`);

  body.innerHTML = parts.join('');
  loadAreaContext(lon, lat, seq);
  loadSiteScreen(lon, lat, seq);
  loadMarketRent(parcel, seq, lotSf, far, density);
  loadAmenities(lon, lat, seq);
  loadSupplyPipeline(lon, lat, seq);
  if (parcel.zip) loadFinancing(parcel.zip, seq);
  if (parcel.county === 'nyc' && parcel.folio) {
    loadAcris(parcel.folio, seq);
    if (parcel.zip) loadInvestmentSales(parcel.zip, seq, null);
  } else if (parcel.state === 'IL' && parcel.zip) {
    loadInvestmentSales(parcel.zip, seq, 'IL');
  }
  const rb = document.getElementById('report-btn');
  if (rb) rb.addEventListener('click', () => openDiligenceReport(lon, lat));
  const cb = document.getElementById('compare-btn');
  if (cb) cb.addEventListener('click', () => addToComparison(parcel, zoning, lon, lat));
}

// ---------- side-by-side property comparison ----------
const COMPARE_LIST = [];  // [{address, owner, zone, far, height, lotSf, floodZone, lon, lat}]
const COMPARE_MAX = 4;

function addToComparison(parcel, zoning, lon, lat) {
  if (COMPARE_LIST.length >= COMPARE_MAX) { toast(`Compare up to ${COMPARE_MAX} at once — remove one first`); return; }
  const address = parcel.found ? [parcel.address, parcel.city].filter(Boolean).join(', ') : `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  if (COMPARE_LIST.some((c) => c.address === address)) { toast('Already comparing this property'); return; }
  COMPARE_LIST.push({
    address, lon, lat,
    owner: parcel.owner || null,
    zone: zoning.found ? zoning.ZONE : null,
    far: zoning.found ? zoning.FAR : null,
    height: zoning.found ? zoning.MAXHEIGHT : null,
    density: zoning.found ? zoning.DENSITY : null,
    lotSf: parcel.lot_size_sf || null,
    value: parcel.just_value || parcel.market_value || null,
  });
  renderComparisonTray();
}

function renderComparisonTray() {
  const tray = document.getElementById('compare-tray');
  const wrap = document.getElementById('compare-table-wrap');
  if (!tray || !wrap) return;
  if (!COMPARE_LIST.length) { tray.hidden = true; return; }
  tray.hidden = false;
  const rows = [
    ['Address', (c) => esc(c.address)],
    ['Owner', (c) => esc(c.owner || '—')],
    ['Zone', (c) => esc(c.zone || '—')],
    ['Max FAR', (c) => esc(c.far || '—')],
    ['Max height', (c) => c.height ? esc(c.height) + ' ft' : '—'],
    ['Density', (c) => c.density ? esc(c.density) + ' du/ac' : '—'],
    ['Lot size', (c) => c.lotSf ? fmtNum(c.lotSf) + ' sf' : '—'],
    ['Value', (c) => c.value ? fmtUSD(c.value) : '—'],
  ];
  let html = '<table class="compare-table"><thead><tr><th class="metric"></th>';
  COMPARE_LIST.forEach((c, i) => {
    html += `<th>#${i + 1} <span class="compare-remove" data-idx="${i}" title="Remove">&times;</span></th>`;
  });
  html += '</tr></thead><tbody>';
  for (const [label, get] of rows) {
    html += `<tr><th class="metric">${label}</th>` + COMPARE_LIST.map((c) => `<td>${get(c)}</td>`).join('') + '</tr>';
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function wireComparisonTray() {
  const tray = document.getElementById('compare-tray');
  if (!tray) return;
  document.getElementById('compare-close')?.addEventListener('click', () => { tray.hidden = true; });
  tray.addEventListener('click', (e) => {
    const btn = e.target.closest('.compare-remove');
    if (!btn) return;
    const idx = parseInt(btn.dataset.idx, 10);
    COMPARE_LIST.splice(idx, 1);
    renderComparisonTray();
  });
}

// Screening-only pro-forma: Lot SF x FAR (or zoned density) -> est. units ->
// x market rent (ZORI) -> gross revenue. Not underwriting — no efficiency loss,
// parking, setbacks, vacancy, or opex. Rendered once market rent resolves.
function renderProforma(lotSf, far, density, rentMonthly) {
  const el = document.getElementById('proforma');
  if (!el) return;
  const buildableSf = far ? Math.round(lotSf * far) : null;
  const acres = lotSf / 43560;
  const AVG_UNIT_SF = 850;
  let units = null, unitBasis = '';
  if (buildableSf) { units = Math.max(1, Math.round(buildableSf / AVG_UNIT_SF)); unitBasis = `@ ~${AVG_UNIT_SF} sf/unit avg`; }
  else if (density) { units = Math.max(1, Math.round(density * acres)); unitBasis = '@ zoned density'; }
  const annualRevenue = (units && rentMonthly) ? units * rentMonthly * 12 : null;
  const p = [];
  if (buildableSf) p.push(field('Buildable SF (Lot × FAR)', fmtNum(buildableSf) + ' sf'));
  if (units) p.push(field('Est. units', fmtNum(units) + ` <span style="color:var(--text-dim);font-size:10.5px">${unitBasis}</span>`));
  if (annualRevenue) p.push(field('Est. gross rent revenue', fmtUSD(annualRevenue) + '/yr'));
  p.push(`<div class="tax-hint">Screening math only — ignores efficiency loss, parking/setbacks, vacancy, opex. Not underwriting.</div>`);
  el.innerHTML = p.join('');
}

// Market asking-rent benchmark (Zillow Research ZORI, public data) by parcel ZIP,
// falling back to the city name as a metro match.
async function loadMarketRent(parcel, seq, lotSf, far, density) {
  const el0 = document.getElementById('market-rent');
  const zip5 = String(parcel.zip || '').slice(0, 5);
  const metro = parcel.city || '';
  if (!zip5 && !metro) {
    if (el0) el0.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">No ZIP on record here.</span>';
    if (lotSf && (far || density)) renderProforma(lotSf, far, density, null);
    return;
  }
  let r;
  try {
    r = await fetchJSON(`/api/market-rents?${zip5 ? 'zip=' + zip5 : ''}${metro ? '&metro=' + encodeURIComponent(metro) : ''}`);
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('market-rent');
  if (!el) return;
  if (!r || r.available === false || (!r.zip && !r.metro)) {
    el.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">No ZORI rent benchmark for this area.</span>';
    if (lotSf && (far || density)) renderProforma(lotSf, far, density, null);
    return;
  }
  const p = [];
  const fmtYoy = (y) => y == null ? '' : ` <span style="color:${y >= 0 ? 'var(--good)' : 'var(--danger)'}">(${y >= 0 ? '+' : ''}${esc(y)}% yr)</span>`;
  if (r.zip) p.push(field(`ZIP ${esc(r.zip.code)} typical rent`, `${fmtUSD(r.zip.rent)}/mo${fmtYoy(r.zip.yoy)}`));
  if (r.metro) p.push(field(`${esc(r.metro.name)} metro`, `${fmtUSD(r.metro.rent)}/mo${fmtYoy(r.metro.yoy)}`));
  p.push(`<div style="font-size:10px;color:var(--text-dim)">${esc(r.attribution || 'Data: Zillow Research (ZORI)')} · as of ${esc(r.asof || '')}</div>`);
  el.innerHTML = p.join('');
  if (lotSf && (far || density)) {
    const rentMonthly = (r.zip && r.zip.rent) || (r.metro && r.metro.rent) || null;
    renderProforma(lotSf, far, density, rentMonthly);
  }
}

// NYC ACRIS — recorded deeds (who owns it, what they paid) + mortgages with
// satisfaction status. Renders into #acris on NYC parcel clicks.
async function loadAcris(bbl, seq) {
  let r;
  try {
    r = await fetchJSON(`/api/acris?bbl=${encodeURIComponent(bbl)}`, { timeoutMs: 30000 });
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('acris');
  if (!el) return;
  if (!r || !r.found) {
    const link = r && r.acris_url && safeUrl(r.acris_url) ? ` <a href="${safeUrl(r.acris_url)}" target="_blank" rel="noopener">Open ACRIS →</a>` : '';
    el.innerHTML = `<span style="color:var(--text-dim);font-size:11.5px">${esc((r && r.message) || 'ACRIS unavailable.')}${link}</span>`;
    return;
  }
  const p = [];
  for (const d of (r.deeds || [])) {
    p.push(`<div class="comp"><div class="row1"><span class="addr">${esc(d.type)} — ${esc((d.buyer || []).join(', ') || '?')}</span><span class="dist">${d.amount ? fmtUSD(d.amount) : '—'}</span></div><div class="row2">${esc(d.date)}${(d.seller || []).length ? ' · from ' + esc(d.seller.join(', ').slice(0, 48)) : ''}</div></div>`);
  }
  if ((r.mortgages || []).length) {
    p.push(`<div class="section-title" style="margin-top:8px">Mortgages ${r.open_mortgages ? `<span class="badge" style="background:#fee2e2;color:#991b1b">${r.open_mortgages} open</span>` : '<span class="badge" style="background:#dcfce7;color:#166534">none open</span>'}</div>`);
    for (const m of r.mortgages) {
      p.push(`<div class="comp"><div class="row1"><span class="addr">${esc((m.lender || []).join(', ').slice(0, 44) || m.type)}</span><span class="dist">${m.amount ? fmtUSD(m.amount) : '—'}</span></div><div class="row2">${esc(m.date)} · ${m.satisfied ? '<span style="color:var(--good)">satisfied</span>' : '<span style="color:var(--warn)">not marked satisfied</span>'}</div></div>`);
    }
  }
  if (r.note) p.push(`<div class="tax-hint">${esc(r.note)}</div>`);
  if (r.acris_url && safeUrl(r.acris_url)) p.push(`<div class="tax-link" style="margin-top:6px"><a href="${safeUrl(r.acris_url)}" target="_blank" rel="noopener">All ${esc(r.documents_on_lot)} recorded documents (ACRIS) →</a></div>`);
  el.innerHTML = p.join('') || '<span style="color:var(--text-dim);font-size:11.5px">No deed/mortgage documents found.</span>';
}

// Investment sales for the parcel's ZIP + statistics. NYC: DOF Rolling Sales.
// Illinois (Cook County): Assessor Parcel Sales.
async function loadInvestmentSales(zip, seq, state) {
  let r;
  try {
    r = await fetchJSON(`/api/investment-sales?zip=${encodeURIComponent(String(zip).slice(0, 5))}${state ? '&state=' + state : ''}`, { timeoutMs: 25000 });
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('inv-sales');
  if (!el) return;
  if (!r || !r.found) {
    el.innerHTML = `<span style="color:var(--text-dim);font-size:11.5px">${esc((r && r.message) || 'Sales data unavailable.')}</span>`;
    return;
  }
  const st = r.stats || {};
  const p = [];
  p.push(`<div class="amen-chips">
    <span class="amen-chip"><b>${esc(st.count)}</b> sales</span>
    <span class="amen-chip"><b>${fmtUSD(st.volume)}</b> volume</span>
    <span class="amen-chip"><b>${fmtUSD(st.median_price)}</b> median</span>
    ${st.median_ppsf ? `<span class="amen-chip"><b>$${esc(st.median_ppsf)}</b>/SF median</span>` : ''}
    ${st.max_price ? `<span class="amen-chip"><b>${fmtUSD(st.max_price)}</b> top sale</span>` : ''}
  </div>`);
  if ((st.top_categories || []).length) {
    p.push(`<div style="font-size:10.5px;color:var(--text-muted);margin:5px 0 3px">${st.top_categories.slice(0, 3).map((c) => esc(c.count + '× ' + c.category.replace(/^\d+\s*/, '').toLowerCase())).join(' · ')}</div>`);
  }
  for (const s of (r.sales || []).slice(0, 6)) {
    p.push(`<div class="comp"><div class="row1"><span class="addr">${esc(s.address)}</span><span class="dist">${fmtUSD(s.price)}</span></div><div class="row2">${esc(s.date)} · ${esc((s.category || '').replace(/^\d+\s*/, '').toLowerCase())}${s.gsf ? ' · ' + esc(Math.round(s.price / s.gsf)) + '/SF' : ''}</div></div>`);
  }
  p.push(`<div style="font-size:10px;color:var(--text-dim);margin-top:4px">${esc(r.source)} · ZIP ${esc(r.zip)} · ${esc((r.window || {}).from || '')} → ${esc((r.window || {}).to || '')}</div>`);
  el.innerHTML = p.join('');
}

// Licensed-broker market directory tab (FL: full DBPR extract; elsewhere: state lookup link).
let _brokerCtx = null;   // {county, state} of the current market scope
let _brokerType = 'all'; // all | broker | brokerage

// Scope the Brokers tab to the parcel's market when a parcel is selected.
function loadBrokerDirectory(parcel, seq) {
  const isFL = (parcel.state === 'FL') || ['miami-dade', 'broward'].includes(parcel.county) || parcel.source === 'statewide';
  const st = isFL ? 'FL' : (parcel.state || null);
  const county = (parcel.county_name || '').replace(/ county.*$/i, '');
  if (!st) return;
  if (seq != null && seq !== lookupSeq) return;  // a newer click already owns the scope
  _brokerCtx = { county, state: st };
  refreshBrokerTab(seq);
}

// Fetch + render the Brokers tab for the current scope, type filter and search text.
async function refreshBrokerTab(seq) {
  const body = document.getElementById('brokers-body');
  const summary = document.getElementById('brokers-summary');
  if (!body) return;
  // default scope: wherever the map is (auto-place), else Florida statewide
  const ctx = _brokerCtx || { county: null, state: currentPlaceState || 'FL' };
  const st = ctx.state || 'FL';
  const q = (document.getElementById('brokers-q') || { value: '' }).value.trim();
  body.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">Loading directory…</span>';
  let r;
  try {
    r = await fetchJSON(`/api/broker-directory?state=${st}`
      + (st === 'FL' && ctx.county ? '&county=' + encodeURIComponent(ctx.county) : '')
      + (_brokerType !== 'all' ? '&btype=' + _brokerType : '')
      + (q.length >= 2 ? '&q=' + encodeURIComponent(q) : ''));
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;  // stale — a newer parcel owns the tab
  if (!r) { body.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">Directory unavailable.</span>'; return; }
  if (!r.available) {
    if (summary) summary.textContent = `Licensed brokers — ${st}`;
    body.innerHTML = r.lookup_url && safeUrl(r.lookup_url)
      ? `<p style="font-size:11.5px;color:var(--text-muted)">${esc(r.message || '')}</p><div class="tax-link"><a href="${safeUrl(r.lookup_url)}" target="_blank" rel="noopener">Official ${esc(st)} license lookup →</a></div>`
      : `<p style="font-size:11.5px;color:var(--text-muted)">${esc(r.message || 'No roster for this state.')}</p>`;
    return;
  }
  if (summary) summary.textContent = `Licensed brokers — ${ctx.county || 'Florida'} (${Number(r.count).toLocaleString()})`;
  renderBrokerRows(body, r, ctx.county);
}

function renderBrokerRows(body, r, county) {
  const rows = (r.brokers || []).map((b) => `<div class="comp"><div class="row1"><span class="addr">${esc(b.name)}${b.dba ? ` <span style="color:var(--text-dim)">dba ${esc(b.dba)}</span>` : ''}</span><span class="dist">${esc(b.license || '')}</span></div><div class="row2">${esc(b.type || '')}${b.city ? ' · ' + esc(b.city) : ''}${b.zip ? ' ' + esc(b.zip) : ''}</div></div>`).join('');
  body.innerHTML = (rows || '<p style="font-size:11.5px;color:var(--text-muted)">No matches.</p>')
    + `<div style="font-size:10px;color:var(--text-dim);margin-top:5px">${esc(r.source || '')} · ${Number(r.count).toLocaleString()} licensed in ${esc(county || 'FL')} · showing ${(r.brokers || []).length}</div>`;
}

function wireBrokerSearch() {
  const input = document.getElementById('brokers-q');
  if (input) {
    let t = null;
    input.addEventListener('input', () => {
      clearTimeout(t);
      const q = input.value.trim();
      if (q.length === 1) return;  // wait for 2+ chars (0 chars = clear → refresh)
      t = setTimeout(refreshBrokerTab, 300);
    });
  }
  // type filter: All / Brokers / Brokerages
  const seg = document.getElementById('brokers-type');
  if (seg) {
    seg.addEventListener('click', (e) => {
      const btn = e.target.closest('.seg-btn');
      if (!btn) return;
      for (const b of seg.querySelectorAll('.seg-btn')) b.classList.toggle('active', b === btn);
      _brokerType = btn.dataset.btype || 'all';
      refreshBrokerTab();
    });
  }
}

// Competing supply nearby — large-value / multifamily-signal new-construction
// permits within radius, from the same permit sources /api/permits already uses.
// SBA real-estate loan activity in the parcel's ZIP (public FOIA data).
async function loadFinancing(zip, seq) {
  let r;
  try { r = await fetchJSON(`/api/financing?zip=${encodeURIComponent(String(zip).slice(0, 5))}`, { timeoutMs: 15000 }); }
  catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('financing');
  if (!el) return;
  if (!r || !r.found) {
    el.innerHTML = `<span style="color:var(--text-dim);font-size:11.5px">${esc((r && r.message) || 'No SBA loan activity.')}</span>`;
    return;
  }
  const p = [`<div style="font-size:10.5px;color:var(--text-muted);margin-bottom:3px">${esc(r.count)} SBA RE loans · ${fmtUSD(r.total_amount)} total</div>`];
  for (const l of r.loans.slice(0, 6)) {
    p.push(`<div class="comp"><div class="row1"><span class="addr">${esc(l.name)}</span><span class="dist">${fmtUSD(l.amount)}</span></div><div class="row2">${esc(l.date)} · ${esc(l.program)}${l.use ? ' · ' + esc(l.use) : ''}</div></div>`);
  }
  p.push(`<div style="font-size:10px;color:var(--text-dim);margin-top:3px">${esc(r.attribution || 'SBA FOIA')}</div>`);
  el.innerHTML = p.join('');
}

async function loadSupplyPipeline(lon, lat, seq) {
  let r;
  try {
    r = await fetchJSON(`/api/supply-pipeline?lon=${lon}&lat=${lat}&radius_ft=15840&min_value=1000000`, { timeoutMs: 20000 });
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('supply-pipeline');
  if (!el) return;
  map.getSource('supply_pipeline')?.setData({ type: 'FeatureCollection', features: [] });
  if (!r || !r.available) {
    el.innerHTML = `<span style="color:var(--text-dim);font-size:11.5px">${esc((r && r.message) || 'No permit signal for this area.')}</span>`;
    return;
  }
  if (!r.count) {
    el.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">No large new-construction permits found within 3 mi.</span>';
    return;
  }
  const p = [`<div style="font-size:10.5px;color:var(--text-muted);margin-bottom:3px">${esc(r.count)} within 3 mi · ${esc(r.source)}</div>`];
  for (const proj of r.projects.slice(0, 8)) {
    p.push(`<div class="comp"><div class="row1"><span class="addr">${esc((proj.scope || 'New construction').slice(0, 42))}</span><span class="dist">${proj.value ? fmtUSD(proj.value) : '—'}</span></div><div class="row2">${esc(proj.address || '')}${proj.date ? ' · ' + esc(proj.date) : ''}</div></div>`);
  }
  p.push(r.note ? `<div class="tax-hint">${esc(r.note)}</div>` : '');
  el.innerHTML = p.join('');
  const feats = r.projects.filter((x) => x.lon != null && x.lat != null).map((x) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [x.lon, x.lat] },
    properties: { scope: x.scope || 'New construction', value: x.value, address: x.address },
  }));
  map.getSource('supply_pipeline')?.setData({ type: 'FeatureCollection', features: feats });
}

// Walking-distance amenities (OpenStreetMap) — counts + notable names.
const AMENITY_LABELS = { grocery: 'Grocery', restaurant: 'Restaurants', cafe: 'Cafés', bar: 'Bars', school: 'Schools', pharmacy: 'Pharmacies', bank: 'Banks', gym: 'Gyms', park: 'Parks', health: 'Health', transit: 'Transit' };
async function loadAmenities(lon, lat, seq) {
  let r;
  try {
    r = await fetchJSON(`/api/amenities?lon=${lon}&lat=${lat}`, { timeoutMs: 25000 });
  } catch (e) { r = null; }
  if (seq != null && seq !== lookupSeq) return;
  const el = document.getElementById('amenities');
  if (!el) return;
  if (!r || !r.available) {
    el.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">Amenity data unavailable right now.</span>';
    return;
  }
  const counts = r.counts || {};
  const keys = Object.keys(AMENITY_LABELS).filter((k) => counts[k]);
  if (!keys.length) {
    el.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">No mapped amenities within a half-mile.</span>';
    return;
  }
  const chips = keys.map((k) => `<span class="amen-chip"><b>${counts[k]}</b> ${AMENITY_LABELS[k]}</span>`).join('');
  const notable = [];
  for (const k of ['grocery', 'gym', 'transit', 'park']) {
    for (const nm of (r.names && r.names[k]) || []) {
      if (notable.length < 6 && !notable.includes(nm)) notable.push(nm);
    }
  }
  el.innerHTML = `<div class="amen-chips">${chips}</div>`
    + (notable.length ? `<div style="font-size:11px;color:var(--text-muted);margin-top:5px">Incl. ${esc(notable.join(', '))}</div>` : '')
    + `<div style="font-size:10px;color:var(--text-dim);margin-top:3px">© OpenStreetMap contributors (ODbL)</div>`;
}

// Coverage counts — runs independently of the map so the sidebar shows honest
// numbers even if the basemap style is slow or blocked. Idempotent.
let _countsDone = false;
async function populateCounts() {
  if (_countsDone) return;
  _countsDone = true;
  try {
    const data = await fetchJSON('/api/cities', { timeoutMs: 15000 });
    const n = data && data.count;
    if (n) {
      for (const id of ['cities-count-cov', 'cities-count-src', 'cities-count-inline']) {
        const el = document.getElementById(id); if (el) el.textContent = n;
      }
      const badge = document.getElementById('cities-count'); if (badge) badge.textContent = `${n} cities`;
    }
  } catch (e) { _countsDone = false; }  // allow a retry if it failed
  try {
    const gj = await fetchJSON('/data/modular_factories.geojson', { timeoutMs: 30000 });
    const active = (gj.features || []).filter((f) => (f.properties || {}).status === 'active').length;
    const el = document.getElementById('factories-count'); if (el && active) el.textContent = active;
  } catch (e) { hideFactoryToggle(); }
}
// Belt-and-suspenders: also fire once the DOM is ready, before/without the map.
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', populateCounts);
else populateCounts();

// National site screening — elevation, environmental, historic, hazards, schools.
async function loadSiteScreen(lon, lat, seq) {
  let s;
  try {
    s = await fetchJSON(`/api/site-screen?lon=${lon}&lat=${lat}`, { timeoutMs: 20000 });
  } catch (e) {
    const el = document.getElementById('site-screen');
    if (el && (seq == null || seq === lookupSeq)) el.innerHTML = '<span style="color:var(--text-dim);font-size:11.5px">Site screening unavailable.</span>';
    return;
  }
  if (seq != null && seq !== lookupSeq) return;  // stale
  const el = document.getElementById('site-screen');
  if (!el) return;
  const p = [];
  if (s.elevation && s.elevation.elevation_ft != null) p.push(field('Ground elevation', esc(s.elevation.elevation_ft) + ' ft'));
  // Environmental: Superfund
  const sf = s.superfund;
  if (sf) {
    if ((sf.within_1mi || []).length) {
      p.push(field('Superfund (EPA NPL)', `<span style="color:var(--danger)">${sf.within_1mi.length} within 1 mi</span>${sf.count_3mi ? ` · ${sf.count_3mi} within 3 mi` : ''}`));
      for (const site of sf.within_1mi.slice(0, 3)) {
        p.push(`<div class="comp"><div class="row1"><span class="addr">${esc(site.name)}</span><span class="dist">${site.miles != null ? site.miles + ' mi' : ''}</span></div><div class="row2">${esc(site.status || '')}${site.city ? ' · ' + esc(site.city) : ''}</div></div>`);
      }
    } else if (sf.count_3mi != null) {
      p.push(field('Superfund (EPA NPL)', sf.count_3mi ? `${sf.count_3mi} within 3 mi` : '<span style="color:var(--good)">None within 3 mi</span>'));
    }
  }
  // Historic
  const h = s.historic;
  if (h) {
    if (h.in_district) p.push(field('Historic district', `<span class="badge" style="background:#fef3c7;color:#92400e">${h.is_nhl ? 'National Historic Landmark' : 'On National Register'}</span> ${esc(h.district_name || '')}`));
    else if ((h.listed_nearby || []).length) p.push(field('Historic nearby', `${h.listed_nearby.length} listed within 500 m`));
  }
  // Critical habitat
  const ch = s.critical_habitat;
  if (ch && ch.in_critical_habitat) {
    p.push(field('Critical habitat', `<span style="color:var(--warn)">USFWS-designated</span> — ${esc((ch.species || []).map((x) => x.name).filter(Boolean).slice(0, 2).join(', '))}`));
  }
  // Airport
  if (s.nearest_airport) {
    const a = s.nearest_airport;
    p.push(field('Nearest airport', `${esc(a.ident || a.name || '')} — ${esc(a.miles)} mi${a.city ? ' (' + esc(a.city) + ')' : ''}`));
  }
  // Schools — zoned (assigned) first; the ≤1 mi list is proximity only, not assignment
  const sz = s.school_zones;
  if (sz && (sz.elementary || sz.middle || sz.high)) {
    const zoned = ['elementary', 'middle', 'high'].map((k) => sz[k] && sz[k].name).filter(Boolean);
    p.push(field('Zoned schools (MDCPS)', esc(zoned.join(' · '))));
  }
  if ((s.schools_1mi || []).length) {
    p.push(field('Public schools ≤1 mi', esc(s.schools_1mi.length) + ` (${esc(s.schools_1mi.slice(0, 2).map((x) => x.name).filter(Boolean).join(', '))}${s.schools_1mi.length > 2 ? '…' : ''})`));
  }
  // Wildfire hazard (USFS)
  const wf = s.wildfire_hazard;
  if (wf && wf.class && !/non-burnable|water/i.test(wf.class)) {
    const hot = /high/i.test(wf.class);
    p.push(field('Wildfire hazard', `<span style="color:${hot ? 'var(--danger)' : 'var(--warn)'}">${esc(wf.class)}</span>`));
  }
  // Seismic design values (USGS)
  if (s.seismic && s.seismic.pga_g != null) {
    p.push(field('Seismic (PGA)', `${esc(s.seismic.pga_g)}g${s.seismic.sdc ? ' · SDC ' + esc(s.seismic.sdc) : ''}`));
  }
  // Sea level rise (NOAA)
  const slr = s.sea_level_rise;
  if (slr && slr.floods_at_ft != null) {
    p.push(field('Floods at sea level +', `<span style="color:${slr.floods_at_ft <= 2 ? 'var(--danger)' : 'var(--warn)'}">${esc(slr.floods_at_ft)} ft</span>`));
  }
  // Coastal Barrier Resources System
  if (s.coastal_barrier && s.coastal_barrier.in_cbrs) {
    p.push(field('Coastal Barrier (CBRS)', `<span style="color:var(--warn)">${esc(s.coastal_barrier.name || s.coastal_barrier.unit)}</span> — federal flood insurance restricted`));
  }
  // Environmental extras: brownfields, LUST, RCRA
  const env = s.environmental;
  if (env) {
    if ((env.brownfields_5mi || []).length) p.push(field('Brownfields (5 mi)', esc(env.brownfields_5mi.length) + ': ' + esc(env.brownfields_5mi.slice(0, 2).map((x) => x.name).join(', '))));
    if (env.lust_3mi_count) p.push(field('Leaking tanks (3 mi)', esc(env.lust_3mi_count)));
    if ((env.rcra_3mi || []).length) p.push(field('Hazardous waste handlers (3 mi)', esc(env.rcra_3mi.length)));
  }
  // Affordable housing incentives: QCT/DDA/LIHTC
  const af = s.affordable_housing;
  if (af) {
    const badges = [];
    if (af.is_qct) badges.push('<span class="badge" style="background:#dbeafe;color:#1e40af">QCT</span>');
    if (af.is_dda) badges.push('<span class="badge" style="background:#ede9fe;color:#5b21b6">DDA</span>');
    if (badges.length) p.push(field('LIHTC basis boost', badges.join(' ') + ' <span style="color:var(--text-dim);font-size:10.5px">130% eligible basis</span>'));
    if ((af.nearby_lihtc || []).length) p.push(field('LIHTC properties (2 mi)', esc(af.nearby_lihtc.length) + ' — ' + esc(af.nearby_lihtc.slice(0, 2).map((x) => x.project).filter(Boolean).join(', '))));
  }
  // Severe-weather history (10-yr NOAA county counts)
  const sh = s.disaster_history && s.disaster_history.storm_history;
  if (sh && sh.total) {
    const parts2 = [['hurricane', 'hurricanes'], ['flood', 'floods'], ['tornado', 'tornadoes'], ['hail', 'hail'], ['wind', 'wind']]
      .filter(([k]) => sh[k]).map(([k, lbl]) => `${sh[k]} ${lbl}`);
    p.push(field('Severe weather (10 yr)', `${esc(sh.total)} events${parts2.length ? ' — ' + esc(parts2.join(', ')) : ''}${sh.prop_damage ? ' · ' + fmtUSD(sh.prop_damage) + ' damage' : ''}`));
  }
  // Disaster history
  const dh = s.disaster_history;
  if (dh && (dh.declarations || []).length) {
    p.push(`<div class="section-title" style="margin-top:10px">FEMA disaster history — ${esc(dh.county || '')}</div>`);
    for (const dd of dh.declarations.slice(0, 5)) {
      p.push(`<div class="comp"><div class="row1"><span class="addr">${esc(dd.type || '')} — ${esc(dd.title || '')}</span><span class="dist">${esc(dd.date || '')}</span></div></div>`);
    }
  }
  el.innerHTML = p.length ? p.join('') : '<span style="color:var(--text-dim);font-size:11.5px">No screening flags at this point.</span>';
}

// ---------- printable Site Diligence Report ----------
async function openDiligenceReport(lon, lat) {
  const w = window.open('', '_blank');
  if (w) w.document.write('<!doctype html><meta charset="utf-8"><title>Site Diligence Report</title><body style="font:14px system-ui;padding:40px">Building report…</body>');
  // Development analysis rides along automatically — reuse the panel's result
  // when it's for this point (it almost always is), else fetch fresh.
  const devReady = devData && devData.found && devData._for &&
    Math.abs(devData._for.lon - lon) < 1e-6 && Math.abs(devData._for.lat - lat) < 1e-6;
  const [parcel, zoning, flood, area, screen, amen, dev] = await Promise.all([
    fetchJSON(`/api/parcel?lon=${lon}&lat=${lat}`).catch(() => ({})),
    fetchJSON(`/api/zoning?lon=${lon}&lat=${lat}`).catch(() => ({})),
    fetchJSON(`/api/flood?lon=${lon}&lat=${lat}`).catch(() => ({})),
    fetchJSON(`/api/area-context?lon=${lon}&lat=${lat}`).catch(() => ({})),
    fetchJSON(`/api/site-screen?lon=${lon}&lat=${lat}`).catch(() => ({})),
    fetchJSON(`/api/amenities?lon=${lon}&lat=${lat}`, { timeoutMs: 20000 }).catch(() => ({})),
    devReady ? Promise.resolve(devData)
      : fetchJSON('/api/dev-analysis', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ parcels: [{ lon, lat, folio: devCurrent?.folio || null, lot_size_sf: devCurrent?.lot_size_sf || null, zoning: devCurrent?.zoning || null }] }),
          timeoutMs: 40000 }).catch(() => ({})),
  ]);
  let zori = {}, zipGrowth = {};
  try {
    const zip5 = String(parcel.zip || '').slice(0, 5);
    const [zr, zg] = await Promise.all([
      (zip5 || parcel.city) ? fetchJSON(`/api/market-rents?${zip5 ? 'zip=' + zip5 : ''}${parcel.city ? '&metro=' + encodeURIComponent(parcel.city) : ''}`).catch(() => ({})) : {},
      zip5 ? fetchJSON(`/api/zip-growth?zip=${zip5}`).catch(() => ({})) : {},
    ]);
    zori = zr; zipGrowth = zg;
  } catch (e) { /* section omitted */ }
  const E = (s) => esc(s == null ? '' : s);
  const row = (k, v) => v || v === 0 ? `<tr><th>${E(k)}</th><td>${v}</td></tr>` : '';
  const money = (n) => (n || n === 0) ? '$' + Number(n).toLocaleString() : '';

  const addr = parcel.found ? [parcel.address, parcel.city].filter(Boolean).join(', ') : `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  const secParcel = [
    row('Owner', E(parcel.owner)), row('Folio / parcel', E(parcel.folio || parcel.parcel_id)),
    row('Address', E(addr)), row('County', E(parcel.county_name)),
    row('Land use', E(parcel.use_description || parcel.use_code)),
    row('Just / market value', money(parcel.just_value || parcel.market_value)),
    row('Lot size', parcel.lot_size_sf ? Number(parcel.lot_size_sf).toLocaleString() + ' sf' : (parcel.lot_acres ? parcel.lot_acres + ' ac' : '')),
    row('Year built', E(parcel.year_built)),
  ].join('');
  const secZoning = [
    row('Zone', E(zoning.ZONE)), row('Description', E(zoning.ZONEDESC)),
    row('Jurisdiction', E(zoning.MUNICNAME)),
    row('Max FAR', E(zoning.FAR)), row('Max height', E(zoning.MAXHEIGHT)),
    row('Density', zoning.DENSITY ? E(zoning.DENSITY) + ' du/ac' : ''),
    zoning.is_use_code ? row('Note', 'Assessor land-use code (no published zoning service here)') : '',
  ].join('');
  const secFlood = flood.found ? [
    row('FEMA zone', E(flood.zone)), row('Subtype', E(flood.subtype)),
    row('SFHA (high-risk)', flood.sfha ? 'Yes' : 'No'), row('Base flood elev', E(flood.bfe)),
  ].join('') : '<tr><td colspan="2">No FEMA flood data at this point.</td></tr>';

  const d = area.demographics || {};
  const secArea = [
    row('Census area', E(area.geography ? [area.geography.place, area.geography.tract].filter(Boolean).join(' · ') : '')),
    row('Tract population', d.population != null ? Number(d.population).toLocaleString() : ''),
    row('Median HH income', money(d.median_household_income)),
    row('Median rent', d.median_gross_rent ? money(d.median_gross_rent) + '/mo' : ''),
    row('County jobs (QCEW)', area.employment && area.employment.jobs ? Number(area.employment.jobs).toLocaleString() : ''),
    row('Population growth (10 yr)', area.population_growth && area.population_growth.pct != null ? (area.population_growth.pct >= 0 ? '+' : '') + area.population_growth.pct + '%' : ''),
    row('Opportunity Zone', area.opportunity_zone ? 'Yes — tract ' + E(area.opportunity_zone) : ''),
    row('Wetland (NWI)', E(area.wetland)),
    row('Building code', E(area.building_code && area.building_code.adopted)),
  ].join('');

  const sc = screen || {};
  const sf = sc.superfund || {};
  const dh = sc.disaster_history || {};
  const secScreen = [
    row('Ground elevation', sc.elevation && sc.elevation.elevation_ft != null ? E(sc.elevation.elevation_ft) + ' ft' : ''),
    row('Superfund (NPL)', (sf.within_1mi || []).length ? E(sf.within_1mi.length) + ' within 1 mi' : (sf.count_3mi != null ? (sf.count_3mi ? E(sf.count_3mi) + ' within 3 mi' : 'None within 3 mi') : '')),
    row('Historic', sc.historic && sc.historic.in_district ? (sc.historic.is_nhl ? 'National Historic Landmark' : 'On National Register') + ' — ' + E(sc.historic.district_name) : ''),
    row('Critical habitat', sc.critical_habitat && sc.critical_habitat.in_critical_habitat ? 'USFWS-designated' : ''),
    row('Nearest airport', sc.nearest_airport ? E(sc.nearest_airport.ident || sc.nearest_airport.name) + ' — ' + E(sc.nearest_airport.miles) + ' mi' : ''),
    row('Zoned schools', sc.school_zones ? E(['elementary', 'middle', 'high'].map((k) => sc.school_zones[k] && sc.school_zones[k].name).filter(Boolean).join(' · ')) : ''),
    row('Public schools ≤1 mi', (sc.schools_1mi || []).length ? E(sc.schools_1mi.length) : ''),
    row('Wildfire hazard', sc.wildfire_hazard && !/non-burnable|water/i.test(sc.wildfire_hazard.class || '') ? E(sc.wildfire_hazard.class) : ''),
    row('Seismic (PGA)', sc.seismic && sc.seismic.pga_g != null ? E(sc.seismic.pga_g) + 'g' + (sc.seismic.sdc ? ' (SDC ' + E(sc.seismic.sdc) + ')' : '') : ''),
    row('Floods at sea level +', sc.sea_level_rise && sc.sea_level_rise.floods_at_ft != null ? E(sc.sea_level_rise.floods_at_ft) + ' ft' : ''),
    row('Coastal Barrier (CBRS)', sc.coastal_barrier && sc.coastal_barrier.in_cbrs ? 'Yes — ' + E(sc.coastal_barrier.name || sc.coastal_barrier.unit) : ''),
    row('Brownfields (5 mi)', sc.environmental && (sc.environmental.brownfields_5mi || []).length ? E(sc.environmental.brownfields_5mi.length) : ''),
    row('Leaking tanks (3 mi)', sc.environmental && sc.environmental.lust_3mi_count ? E(sc.environmental.lust_3mi_count) : ''),
    row('LIHTC basis boost (QCT/DDA)', sc.affordable_housing && (sc.affordable_housing.is_qct || sc.affordable_housing.is_dda) ? [sc.affordable_housing.is_qct && 'QCT', sc.affordable_housing.is_dda && 'DDA'].filter(Boolean).join(' + ') : ''),
    row('LIHTC properties nearby', sc.affordable_housing && (sc.affordable_housing.nearby_lihtc || []).length ? E(sc.affordable_housing.nearby_lihtc.length) : ''),
    row('Severe weather (10 yr)', dh.storm_history && dh.storm_history.total ? E(dh.storm_history.total) + ' events' + (dh.storm_history.prop_damage ? ', ' + money(dh.storm_history.prop_damage) + ' damage' : '') : ''),
  ].join('');
  const disasterRows = (dh.declarations || []).slice(0, 8)
    .map((x) => `<tr><td>${E(x.date)}</td><td>${E(x.type)}</td><td>${E(x.title)}</td></tr>`).join('');

  const secMarket = [
    row('ZIP market rent', zori && zori.zip ? money(zori.zip.rent) + '/mo' + (zori.zip.yoy != null ? ` (${zori.zip.yoy >= 0 ? '+' : ''}${zori.zip.yoy}% yr)` : '') : ''),
    row('Metro market rent', zori && zori.metro ? money(zori.metro.rent) + '/mo — ' + E(zori.metro.name) : ''),
    row('ZIP population growth', zipGrowth && zipGrowth.found
      ? `${zipGrowth.pct >= 0 ? '+' : ''}${zipGrowth.pct}% (${E(zipGrowth.from_year)}→${E(zipGrowth.to_year)}, ${Number(zipGrowth.pop_from).toLocaleString()}→${Number(zipGrowth.pop_to).toLocaleString()}; ACS 5-yr ZCTA ${E(zipGrowth.zip)})` : ''),
    row('Walkable amenities (½ mi)', amen && amen.available && amen.counts
      ? E(Object.entries(amen.counts).map(([k, v]) => `${v} ${k}`).join(' · ')) : ''),
  ].join('');

  // ---- Development capacity (what can be built) ----
  const num = (n) => (n == null ? '' : Math.round(n).toLocaleString());
  let secDev = '';
  if (dev && dev.found) {
    const dz = dev.zoning || {}, db = dev.base || {}, ds = dev.site || {}, da = dev.assumptions || {};
    const stStr = db.stories == null ? '' : (db.stories === null ? 'unlimited'
      : `${num(db.stories)} stories${db.height_ft_est ? ` (~${num(db.height_ft_est)} ft)` : ''}`);
    const baseRows = [
      row('Zone', E(dz.zone) + (dz.desc ? ' — ' + E(dz.desc) : '')),
      row('Site area', ds.lot_sf ? `${num(ds.lot_sf)} sf (${ds.acres} ac)` + (ds.parcels > 1 ? ` · ${ds.parcels} parcels assembled` : '') : ''),
      row('Max stories', stStr),
      row('Density cap', db.units_by_density != null ? num(db.units_by_density) + ' units' : ''),
      row('FAR buildable', db.max_far_gsf != null ? num(db.max_far_gsf) + ' sf' + (db.far_effective ? ` (FAR ${(+db.far_effective).toFixed(2)})` : '') : ''),
      row('Max footprint', db.footprint_sf != null ? num(db.footprint_sf) + ' sf' : ''),
      row('Envelope GSF', db.envelope_gsf != null ? `<b>${num(db.envelope_gsf)} sf</b>${db.gsf_binding ? ` (${E(db.gsf_binding)} governs)` : ''}` : ''),
      row('Achievable units', db.max_units != null ? `<b>${num(db.max_units)}</b>${db.units_binding ? ` (${E(db.units_binding)} governs)` : ''}` : ''),
    ].join('');
    const sb = dev.setbacks_ft;
    const sbRow = sb ? row('Setbacks (principal)', `front ${E(sb.front ?? '—')}′ · side ${E(sb.side ?? '—')}′ · rear ${E(sb.rear ?? '—')}′`) : '';
    const bonusRows = (dev.bonuses || []).length
      ? row('Available bonuses', (dev.bonuses || []).map((b) => E(b.label)).join('<br>')) : '';

    // Live Local secondary scenario
    let llBlock = '';
    const ll = dev.live_local;
    if (ll && ll.eligible === false) {
      llBlock = `<p class="note">Live Local Act: ${E(ll.note)}</p>`;
    } else if (ll) {
      const hb = ll.height_benchmark;
      const tallest = (hb && (hb.matching_zones_at_max || [])[0]?.zone) || '';
      const llStories = ll.height_stories != null ? ll.height_stories : 3;
      const hbLabel = tallest
        ? `${llStories} stories${ll.height_floored ? ' (3-story statutory floor)' : ` (tallest zone within 1 mi: ${E(tallest)})`}`
        : `${llStories} stories (3-story statutory floor)`;
      // Same envelope engine as the panel — LL height = benchmark stories.
      const llEnv = devEnvelope(ds.lot_sf, ll.far_150pct, ll.density_max_du_ac,
        dz.lot_coverage_pct, null, llStories, 1, da);
      llBlock = `<table>${[
        row('Max stories (LL)', llStories === null ? 'unlimited' : num(llStories)),
        row('Height benchmark', hbLabel),
        row('Density benchmark', ll.density_max_du_ac ? ll.density_max_du_ac + ' du/ac (jurisdiction max)' : ''),
        row('FAR benchmark', ll.far_150pct ? ll.far_150pct + ' (150% of jurisdiction max ' + E(ll.far_max_in_jurisdiction) + ')' : ''),
        row('Envelope GSF (LL)', llEnv.envGsf != null ? `<b>${num(llEnv.envGsf)} sf</b>${llEnv.gsfBind ? ` (${E(llEnv.gsfBind)} governs)` : ''}` : ''),
        row('Achievable units (LL)', llEnv.maxUnits != null ? `<b>${num(llEnv.maxUnits)}</b>${llEnv.uBind ? ` (${E(llEnv.uBind)} governs)` : ''}` : ''),
      ].join('')}</table>${ll.density_note ? `<p class="note">${E(ll.density_note)}</p>` : ''}`;
    }
    const rb = dev.rulebook;
    const cite = rb ? `<p class="note">Rulebook: ${E(rb.code_name || rb.matched)}${rb.verify ? ' — figures marked for verification against current code text' : ''}${dz.code_url ? ` · <a href="${E(dz.code_url)}" target="_blank">code ↗</a>` : ''}</p>`
      : (dz.code_url ? `<p class="note"><a href="${E(dz.code_url)}" target="_blank">Governing code ↗</a></p>` : '');
    const warns = (dev.warnings || []).map((wn) => `<p class="note">⚠ ${E(wn)}</p>`).join('');
    secDev = `<table>${baseRows}${sbRow}${bonusRows}</table>${cite}` +
      (llBlock ? `<h3 class="sub-h">Secondary scenario — Live Local Act (SB 102/328)</h3>${llBlock}` : '') +
      warns +
      (da.note ? `<p class="note">${E(da.note)} Screening only.</p>` : '');
  }

  const now = new Date();
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Site Diligence Report — ${E(addr)}</title>
  <style>
    body{font:13px/1.5 -apple-system,Segoe UI,system-ui,sans-serif;color:#0f172a;max-width:760px;margin:0 auto;padding:36px 40px}
    h1{font-size:20px;margin:0 0 2px} .sub{color:#64748b;margin:0 0 20px;font-size:12px}
    h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#475569;border-bottom:1px solid #e2e8f0;padding-bottom:4px;margin:22px 0 8px}
    h3.sub-h{font-size:12px;color:#b45309;margin:12px 0 4px;font-weight:700}
    .note{font-size:11px;color:#64748b;margin:4px 0}
    table{width:100%;border-collapse:collapse;margin-bottom:4px} th{text-align:left;color:#64748b;font-weight:600;width:180px;vertical-align:top;padding:3px 8px 3px 0}
    td{padding:3px 0;vertical-align:top} .dis th{width:110px}
    .foot{margin-top:28px;color:#94a3b8;font-size:11px;border-top:1px solid #e2e8f0;padding-top:10px}
    .brand{font-size:11px;font-weight:700;color:#1f6feb;letter-spacing:.02em;margin:0 0 10px}
    @media print{body{padding:0}} button{margin-bottom:16px;padding:8px 14px;font:inherit;cursor:pointer;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc}
  </style></head><body>
  <button onclick="window.print()">⎙ Print / Save as PDF</button>
  <p class="brand">SITEFOLIO</p>
  <h1>Site Diligence Report</h1>
  <p class="sub">${E(addr)} · ${lat.toFixed(6)}, ${lon.toFixed(6)} · generated ${E(now.toLocaleString())}</p>
  <h2>Parcel &amp; ownership</h2><table>${secParcel || '<tr><td>No free parcel record at this point.</td></tr>'}</table>
  <h2>Zoning</h2><table>${secZoning || '<tr><td>No zoning found.</td></tr>'}</table>
  ${secDev ? `<h2>Development capacity — what can be built</h2>${secDev}` : ''}
  <h2>Flood (FEMA NFHL)</h2><table>${secFlood}</table>
  <h2>Area context</h2><table>${secArea}</table>
  <h2>Site screening</h2><table>${secScreen}</table>
  ${secMarket ? `<h2>Market &amp; walkability</h2><table>${secMarket}</table>` : ''}
  ${disasterRows ? `<h2>FEMA disaster history — ${E(dh.county)}</h2><table class="dis"><thead><tr><th>Date</th><th>Type</th><th>Declaration</th></tr></thead><tbody>${disasterRows}</tbody></table>` : ''}
  <p class="foot">Sources: county property appraiser / FDOR / NYC PLUTO / Regrid · municipal zoning · FEMA NFHL · US Census ACS (tract + ZCTA) · USGS · EPA · NPS · USFWS · FAA · NCES · OpenFEMA · Zillow Research (ZORI) · © OpenStreetMap contributors. Development-capacity figures are screening estimates from GIS zoning dimensions plus a curated rulebook — verify against the current code text and with counsel before relying on any figure.</p>
  </body></html>`;
  if (w) { w.document.open(); w.document.write(html); w.document.close(); }
  else { toast('Allow pop-ups to open the report'); }
}

async function loadAreaContext(lon, lat, seq) {
  try {
    const a = await fetchJSON(`/api/area-context?lon=${lon}&lat=${lat}`, { timeoutMs: 25000 });
    if (seq != null && seq !== lookupSeq) return;  // stale — #area-context now belongs to a newer parcel
    const el = document.getElementById('area-context');
    if (!el) return;
    const p = [];
    const g = a.geography;
    if (g && (g.place || g.tract)) p.push(field('Census area', esc([g.place, g.tract].filter(Boolean).join(' · '))));
    const d = a.demographics;
    if (d) {
      if (d.population != null) p.push(field('Population', fmtNum(d.population) + ' (tract)'));
      if (d.median_household_income != null) p.push(field('Median HH inc', fmtUSD(d.median_household_income)));
      if (d.median_gross_rent != null) p.push(field('Median rent', fmtUSD(d.median_gross_rent) + '/mo <span style="color:var(--text-dim);font-size:10px">(contract)</span>'));
      if (d.median_home_value != null) p.push(field('Median home', fmtUSD(d.median_home_value)));
      if (d.median_age != null) p.push(field('Median age', esc(d.median_age)));
      if (d.renter_pct != null) p.push(field('Renter %', esc(d.renter_pct) + '%'));
      if (d.vintage) p.push(`<div style="font-size:10px;color:var(--text-dim)">${esc(d.vintage)} · US Census via Esri Living Atlas</div>`);
    }
    // County economy — BLS employment + population growth
    const emp = a.employment;
    if (emp && emp.jobs) p.push(field('County jobs', fmtNum(emp.jobs) + (emp.establishments ? ` · ${fmtNum(emp.establishments)} biz` : '') + (emp.avg_annual_pay ? ` · ${fmtUSD(emp.avg_annual_pay)} avg pay` : '') + ` <span style="color:var(--text-dim);font-size:10px">(QCEW ${esc(emp.year || '')})</span>`));
    const pg = a.population_growth;
    if (pg && pg.pct != null) p.push(field('Population growth', `<span style="color:${pg.pct >= 0 ? 'var(--good)' : 'var(--danger)'}">${pg.pct >= 0 ? '+' : ''}${esc(pg.pct)}%</span> over 10 yr <span style="color:var(--text-dim);font-size:10px">(${fmtNum(pg.decade_ago)}→${fmtNum(pg.now)})</span>`));
    if (a.opportunity_zone) p.push(field('Opportunity Zone', `<span class="badge oz">QOZ</span> Census tract ${esc(a.opportunity_zone)} <span style="color:var(--text-dim)">— deferred capital gains</span>`));
    if (a.wetland) p.push(field('Wetland (NWI)', `<span style="color:var(--warn)">${esc(a.wetland)}</span>`));
    // HUD Fair Market Rents (national benchmark, by bedroom)
    const fmr = a.fair_market_rent;
    if (fmr && (fmr.br1 || fmr.br2)) {
      p.push(`<div class="section-title" style="margin-top:10px">HUD Fair Market Rent — ${esc(fmr.area || 'area')}</div>`);
      p.push(`<table class="pa-table"><thead><tr><th>Studio</th><th>1BR</th><th>2BR</th><th>3BR</th><th>4BR</th></tr></thead><tbody><tr>
        <td>${fmr.studio ? fmtUSD(fmr.studio) : '—'}</td><td>${fmr.br1 ? fmtUSD(fmr.br1) : '—'}</td><td>${fmr.br2 ? fmtUSD(fmr.br2) : '—'}</td><td>${fmr.br3 ? fmtUSD(fmr.br3) : '—'}</td><td>${fmr.br4 ? fmtUSD(fmr.br4) : '—'}</td></tr></tbody></table>`);
    }
    // Building code (adopted edition + link)
    if (a.building_code) {
      const codeLink = safeUrl(a.building_code.code_url) ? ` <a href="${safeUrl(a.building_code.code_url)}" target="_blank" rel="noopener">(code →)</a>` : '';
      p.push(field('Building code', `${esc(a.building_code.adopted)}${codeLink}`));
    }
    el.innerHTML = p.length ? p.join('') : '<span style="color:var(--text-dim); font-size:11.5px">No area context available.</span>';
  } catch (e) { /* non-fatal */ }
}

// ---------- Property Appraiser full record ----------

async function loadPropertyDetails(folio, seq) {
  const panel = document.getElementById('pa-detail-panel');
  const body = document.getElementById('pa-detail-body');
  panel.hidden = false;
  panel.removeAttribute('open');  // available but collapsed by default (reduce scroll dump)
  body.innerHTML = '<p class="help">Loading full Property Appraiser record…</p>';

  try {
    const d = await fetchJSON(`/api/property-details?folio=${encodeURIComponent(folio)}`, { timeoutMs: 25000 });
    if (seq != null && seq !== lookupSeq) return;  // stale — a newer lookup owns this panel
    body.innerHTML = renderPADetails(d);
  } catch (err) {
    if (seq != null && seq !== lookupSeq) return;
    body.innerHTML = `<p style="color:var(--danger); font-size:12px;">PA fetch failed: ${esc(err.message)}</p>`;
  }
}

function renderPADetails(d) {
  const p = d.property || {};
  const parts = [];

  // Building details
  parts.push(`<div class="pa-section">
    <div class="pa-h">Building &amp; lot</div>
    <div class="pa-grid">
      <div class="k">Bed / Bath</div><div class="v">${esc(p.bedrooms || '—')} bd · ${esc(p.bathrooms || '—')}ba ${p.half_baths ? '· ' + esc(p.half_baths) + ' half' : ''}</div>
      <div class="k">Floors</div><div class="v">${esc(p.floors || '—')}</div>
      <div class="k">Units</div><div class="v">${esc(p.units || '—')}</div>
      <div class="k">Year built</div><div class="v">${esc(p.year_built || '—')} ${p.effective_year ? '(effective ' + esc(p.effective_year) + ')' : ''}</div>
      <div class="k">Actual area</div><div class="v">${p.actual_area_sf ? fmtNum(p.actual_area_sf) + ' sf' : '—'}</div>
      <div class="k">Heated area</div><div class="v">${p.heated_area_sf ? fmtNum(p.heated_area_sf) + ' sf' : '—'}</div>
      <div class="k">Effective area</div><div class="v">${p.effective_area_sf ? fmtNum(p.effective_area_sf) + ' sf' : '—'}</div>
      <div class="k">Lot size</div><div class="v">${p.lot_size_sf ? fmtNum(p.lot_size_sf) + ' sf' : '—'}</div>
      <div class="k">DOR use</div><div class="v">${esc(p.dor_code || '')} · ${esc(p.dor_description || '—')}</div>
      <div class="k">PA zone</div><div class="v">${esc(p.primary_zone || '')} · ${esc(p.primary_zone_desc || '—')}</div>
      <div class="k">Neighborhood</div><div class="v">${esc(p.neighborhood || '—')}</div>
      <div class="k">Subdivision</div><div class="v">${esc(p.subdivision || '—')}</div>
      <div class="k">Plat</div><div class="v">${esc(p.plat_book || '')}${p.plat_page ? '/' + esc(p.plat_page) : ''}</div>
      <div class="k">Parent folio</div><div class="v">${esc(p.parent_folio || '—')}</div>
      <div class="k">Status</div><div class="v">${esc(p.status || '—')}</div>
    </div>
  </div>`);

  // Owners
  if (d.owners && d.owners.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Current owners</div>
      ${d.owners.map(o => `<div style="font-size:11.5px"><b>${esc(o.name || '')}</b>${o.pct_owned ? ' (' + esc(o.pct_owned) + '%)' : ''}</div>`).join('')}
    </div>`);
  }

  // Mailing address
  const m = d.mailing_address || {};
  if (m.line1) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Mailing address</div>
      <div style="font-size:11.5px">${[m.line1, m.line2].filter(Boolean).map(esc).join('<br>')}<br>${esc([m.city, m.state, m.zip].filter(Boolean).join(' '))}${m.country && m.country !== 'USA' ? ' · ' + esc(m.country) : ''}</div>
    </div>`);
  }

  // Sales history
  if (d.sales && d.sales.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Sales history (${d.sales.length})</div>
      <table class="pa-table">
        <thead><tr><th>Date</th><th>Price</th><th>Grantee</th><th>OR</th><th>Qual</th></tr></thead>
        <tbody>
          ${d.sales.map(s => `<tr>
            <td>${esc(s.date || '—')}</td>
            <td class="r num">${fmtUSD(s.price)}</td>
            <td>${esc((s.grantee || '').slice(0, 40)) || '—'}</td>
            <td>${s.or_book ? esc(s.or_book) + '/' + esc(s.or_page) : '—'}</td>
            <td title="${escAttr(s.qualification || '')}">${esc(s.qualified_flag || '—')}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`);
  }

  // Assessment trend
  if (d.assessment_history && d.assessment_history.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Assessment trend</div>
      <table class="pa-table">
        <thead><tr><th>Year</th><th class="r">Land</th><th class="r">Building</th><th class="r">Total</th><th class="r">Assessed</th></tr></thead>
        <tbody>
          ${d.assessment_history.map(a => `<tr>
            <td><b>${esc(a.year)}</b></td>
            <td class="r num">${fmtUSD(a.land_value)}</td>
            <td class="r num">${fmtUSD(a.building_value)}</td>
            <td class="r num">${fmtUSD(a.total_value)}</td>
            <td class="r num">${fmtUSD(a.assessed_value)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`);
  }

  // Taxable values by year + official bills link
  if (d.taxable_history && d.taxable_history.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Taxable values by year</div>
      <table class="pa-table">
        <thead><tr><th>Year</th><th class="r">County</th><th class="r">School</th><th class="r">City</th><th class="r">Regional</th></tr></thead>
        <tbody>
          ${d.taxable_history.map(t => `<tr>
            <td><b>${esc(t.year)}</b></td>
            <td class="r num">${fmtUSD(t.county_taxable)}</td>
            <td class="r num">${fmtUSD(t.school_taxable)}</td>
            <td class="r num">${fmtUSD(t.city_taxable)}</td>
            <td class="r num">${fmtUSD(t.regional_taxable)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      <div class="tax-link">${taxBillLink('miami-dade', d.folio)}</div>
      <div class="tax-hint">Ad valorem tax = taxable value × adopted millage. The link above has the actual bills — amounts paid each year, incl. non-ad valorem and discounts.</div>
    </div>`);
  }

  // Exemptions
  if (d.exemptions && d.exemptions.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Exemptions / benefits</div>
      ${d.exemptions.map(e => `<div style="font-size:11.5px">${esc(e.name || '')}${e.amount ? ' · ' + fmtUSD(e.amount) : ''}</div>`).join('')}
    </div>`);
  }

  // Legal description
  if (d.legal_description && d.legal_description.length) {
    parts.push(`<div class="pa-section">
      <div class="pa-h">Legal description</div>
      <div style="font-size:11px; color:var(--text-muted); font-family:monospace; white-space:pre-wrap; line-height:1.4">${esc((Array.isArray(d.legal_description) ? d.legal_description : [d.legal_description]).join('\n'))}</div>
    </div>`);
  }

  return parts.join('');
}

// ---------- Condo / building unit roster ----------

let _condoData = null;      // last-loaded roster, kept for re-sort/CSV without a refetch
let _condoSelfFolio = null; // the clicked unit's own folio — highlighted in the table
let _condoSort = { key: 'unit', dir: 1 };

async function loadCondoUnits(folio, unit, seq) {
  const panel = document.getElementById('condo-panel');
  const body = document.getElementById('condo-body');
  try {
    const d = await fetchJSON(`/api/condo-units?folio=${encodeURIComponent(folio)}`, { timeoutMs: 20000 });
    if (seq != null && seq !== lookupSeq) return;  // stale — a newer lookup owns this panel
    if (!d.is_condo) { panel.hidden = true; body.innerHTML = ''; return; }
    _condoData = d;
    _condoSelfFolio = folio;
    _condoSort = { key: 'unit', dir: 1 };
    panel.hidden = false;
    document.getElementById('condo-unit-count').textContent = d.unit_count;
    body.innerHTML = renderCondoUnits(d);
    wireCondoTable(seq);
  } catch (err) {
    if (seq != null && seq !== lookupSeq) return;
    panel.hidden = true;
    body.innerHTML = '';
  }
}

function _condoSortedUnits() {
  if (!_condoData) return [];
  const { key, dir } = _condoSort;
  const units = _condoData.units.slice();
  units.sort((a, b) => {
    if (key === 'unit') return dir * (_natUnitCmp(a.unit, b.unit));
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;   // nulls sort last regardless of direction
    if (bv == null) return -1;
    if (typeof av === 'string') return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
  return units;
}
function _natUnitCmp(a, b) {
  const pa = /^(\d+)/.exec(a || ''), pb = /^(\d+)/.exec(b || '');
  if (pa && pb) return (+pa[1] - +pb[1]) || String(a).localeCompare(String(b));
  if (pa) return -1;
  if (pb) return 1;
  return String(a || '').localeCompare(String(b || ''));
}

function renderCondoUnits(d) {
  const s = d.summary || {};
  const units = _condoSortedUnits();
  const hasValues = units.some((u) => u.assessed_value != null);

  const stats = [
    { n: d.unit_count, l: 'Units' },
    { n: s.total_sf ? fmtNum(s.total_sf) : '—', l: 'Total SF' },
    { n: s.avg_sf ? fmtNum(s.avg_sf) : '—', l: 'Avg SF/unit' },
    { n: s.distinct_owners ?? '—', l: 'Distinct owners' },
    { n: s.llc_owned != null ? `${s.llc_owned} (${Math.round(100 * s.llc_owned / d.unit_count)}%)` : '—', l: 'LLC-owned' },
    { n: s.out_of_state_owned != null ? `${s.out_of_state_owned} (${Math.round(100 * s.out_of_state_owned / d.unit_count)}%)` : '—', l: 'Out-of-state owner' },
  ];
  if (hasValues) {
    const vals = units.map((u) => u.assessed_value).filter((v) => v != null);
    const total = vals.reduce((a, b) => a + b, 0);
    stats.push({ n: fmtUSD(total), l: 'Total assessed value' });
    stats.push({ n: vals.length ? fmtUSD(Math.round(total / vals.length)) : '—', l: 'Avg value/unit' });
  }

  const cols = [
    ['unit', 'Unit'], ['owner', 'Owner'], ['sqft', 'SF'],
    ...(hasValues ? [['assessed_value', 'Value'], ['last_sale_price', 'Last sale']] : []),
  ];
  const rows = units.map((u) => {
    const badges = (u.is_llc ? '<span class="condo-owner-badge llc" title="Entity-owned">LLC</span>' : '') +
      (u.out_of_state ? '<span class="condo-owner-badge oos" title="Out-of-state mailing address">OOS</span>' : '');
    const saleCell = u.last_sale_price
      ? `${fmtUSD(u.last_sale_price)}${u.last_sale_date ? `<div style="color:var(--text-dim);font-size:9.5px">${esc(u.last_sale_date)}</div>` : ''}`
      : '—';
    return `<tr class="unit-row${u.folio === _condoSelfFolio ? ' self-row' : ''}" data-folio="${escAttr(u.folio)}">
      <td>${esc(u.unit)}</td>
      <td>${esc((u.owner || '—').slice(0, 34))}${badges}</td>
      <td class="r">${u.sqft ? fmtNum(u.sqft) : '—'}</td>
      ${hasValues ? `<td class="r">${u.assessed_value ? fmtUSD(u.assessed_value) : '—'}</td><td class="r">${saleCell}</td>` : ''}
    </tr>`;
  }).join('');

  const multiOwners = (s.multi_unit_owners || []).length
    ? `<div class="pa-section"><div class="pa-h">Owns multiple units here</div>
        ${s.multi_unit_owners.map((o) => `<div class="condo-multi-owner"><b>${esc(o.count)}×</b> ${esc(o.owner)}</div>`).join('')}
      </div>` : '';

  return `
    <p class="help" style="margin:0 0 8px">${esc(d.building_address || 'This building')} — click a column header to sort, a row to pull that unit's full record, ★ to export.</p>
    <div class="condo-summary">${stats.map((x) => `<div class="stat"><span class="n">${x.n}</span><span class="l">${x.l}</span></div>`).join('')}</div>
    ${!hasValues ? `<button class="secondary-btn" id="condo-load-values" style="margin-bottom:8px">Load values &amp; sales (${d.unit_count} units)</button>
      <div id="condo-values-note" class="mini-note" hidden></div>` : ''}
    <div class="condo-table-wrap">
      <table class="condo-table">
        <thead><tr>${cols.map(([k, label]) => `<th data-sort="${k}">${esc(label)}${_condoSort.key === k ? (_condoSort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${multiOwners}
    <button class="secondary-btn" id="condo-export-csv" style="margin-top:8px">Export roster (CSV)</button>
  `;
}

function wireCondoTable(seq) {
  const body = document.getElementById('condo-body');
  body.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      _condoSort = _condoSort.key === key ? { key, dir: -_condoSort.dir } : { key, dir: 1 };
      body.innerHTML = renderCondoUnits(_condoData);
      wireCondoTable(seq);
    });
  });
  body.querySelectorAll('tr.unit-row').forEach((tr) => {
    tr.addEventListener('click', () => loadPropertyDetails(tr.dataset.folio, lookupSeq));
  });
  document.getElementById('condo-load-values')?.addEventListener('click', (e) => loadCondoValues(e.target, seq));
  document.getElementById('condo-export-csv')?.addEventListener('click', exportCondoCsv);
}

async function loadCondoValues(btn, seq) {
  if (!_condoData) return;
  const note = document.getElementById('condo-values-note');
  btn.disabled = true;
  btn.textContent = 'Loading…';
  if (note) { note.hidden = false; note.textContent = `Fetching ${_condoData.unit_count} unit records — can take up to a minute for large buildings…`; }
  try {
    const r = await fetchJSON(`/api/condo-unit-values?building_folio=${encodeURIComponent(_condoData.building_folio)}`, { timeoutMs: 60000 });
    if (seq != null && seq !== lookupSeq) return;  // a newer lookup owns this panel now
    for (const u of _condoData.units) {
      const v = r.values[u.folio];
      if (v) Object.assign(u, v);
    }
    if (r.truncated && note) note.textContent = `Values loaded for the first ${r.enriched_count} units (capped — this building exceeds the per-click limit).`;
    document.getElementById('condo-body').innerHTML = renderCondoUnits(_condoData);
    wireCondoTable(seq);
  } catch (err) {
    if (seq != null && seq !== lookupSeq) return;
    btn.disabled = false;
    btn.textContent = 'Retry loading values';
    if (note) note.textContent = 'Failed to load values — try again.';
  }
}

function exportCondoCsv() {
  if (!_condoData) return;
  const hasValues = _condoData.units.some((u) => u.assessed_value != null);
  const head = ['Unit', 'Folio', 'Owner', 'SF', 'Year Built', 'LLC', 'Out-of-state',
    ...(hasValues ? ['Assessed Value', 'Last Sale Price', 'Last Sale Date', 'Beds', 'Baths'] : [])];
  const csvCell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const lines = [head.map(csvCell).join(',')];
  for (const u of _condoSortedUnits()) {
    lines.push([u.unit, u.folio, u.owner, u.sqft, u.year_built, u.is_llc ? 'Y' : '', u.out_of_state ? 'Y' : '',
      ...(hasValues ? [u.assessed_value, u.last_sale_price, u.last_sale_date, u.beds, u.baths] : [])]
      .map(csvCell).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `condo-units-${_condoData.building_folio}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------- Live Local ----------

function wireLiveLocal() {
  document.getElementById('ll-run').addEventListener('click', runLiveLocal);
}

async function runLiveLocal() {
  if (!lastSelection) return;
  const { lon, lat } = lastSelection;
  const out = document.getElementById('ll-result');
  out.hidden = false;
  out.innerHTML = '<p class="help" style="color:#92400e">Scanning 1-mile radius…</p>';

  try {
    const r = await fetchJSON(`/api/live-local?lon=${lon}&lat=${lat}`, { timeoutMs: 25000 });
    if (!r.found) {
      out.innerHTML = `<p class="help" style="color:#92400e">${esc(r.note || 'No height data in radius')}</p>`;
      return;
    }
    const top = r.matching_zones_at_max?.[0];
    const zones = r.all_zones_in_radius || [];
    out.innerHTML = `
      <div class="big">${fmtNum(r.max_height_ft)}<span class="unit">ft</span></div>
      <div class="src">Set by <b>${esc(top?.zone || '')}</b> (${esc(top?.desc || '')}) in <b>${esc(top?.muni || '')}</b></div>
      ${r.matching_zones_at_max.length > 1 ? `<div class="src">Also matched by ${r.matching_zones_at_max.length - 1} other zone(s) at the same height.</div>` : ''}
      <div class="zone-list">
        ${zones.slice(0, 20).map(z => `<div class="row">
          <span class="zone">${esc(z.zone)}</span>
          <span class="ht">${esc(z.muni)}</span>
          <span class="ft">${esc(z.max_height_ft)} ft</span>
          <span class="ht">${z.far ? 'FAR ' + esc(z.far) : ''}</span>
        </div>`).join('')}
      </div>`;

    // Draw 1-mile ring
    const milePadLat = 5280 / 364320;
    const milePadLon = milePadLat / Math.cos(lat * Math.PI / 180);
    const ring = [];
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      ring.push([lon + Math.cos(a) * milePadLon, lat + Math.sin(a) * milePadLat]);
    }
    map.getSource('ll-ring')?.setData({
      type: 'FeatureCollection',
      features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: ring }, properties: {} }],
    });
  } catch (err) {
    out.innerHTML = `<p class="help" style="color:var(--danger)">Failed: ${esc(err.message)}</p>`;
  }
}

// ---------- comps map layer ----------

function drawCompsLayer(comps, lon, lat) {
  if (!comps || !comps.available) {
    map.getSource('comps')?.setData({ type: 'FeatureCollection', features: [] });
    map.getSource('comps-ring')?.setData({ type: 'FeatureCollection', features: [] });
    return;
  }
  const r = comps.radius_ft / 364320.0;
  const ring = [];
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * Math.PI * 2;
    const dx = (r / Math.cos((lat * Math.PI) / 180)) * Math.cos(a);
    const dy = r * Math.sin(a);
    ring.push([lon + dx, lat + dy]);
  }
  map.getSource('comps-ring')?.setData({
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: ring }, properties: {} }],
  });
  const features = (comps.listings || []).map((c) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [c.lng, c.lat] },
    properties: { kind: c.listing_type, address: c.address, price: c.price, psf: c.price_per_sqft, beds: c.beds, baths: c.baths, sqft: c.sqft, url: c.url, dist: c.distance_ft },
  }));
  map.getSource('comps')?.setData({ type: 'FeatureCollection', features });
}

function wireCompHover() {
  let popup = null;
  map.on('mouseenter', 'comps-pts', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    popup = new maplibregl.Popup({ closeButton: false, offset: 10 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.address || '')}</b><br/>${fmtUSD(p.price)} ${p.psf ? '($' + esc(p.psf) + '/sf)' : ''} · ${fmtFeet(+p.dist)}`).addTo(map);
  });
  map.on('mouseleave', 'comps-pts', () => { map.getCanvas().style.cursor = ''; popup && popup.remove(); popup = null; });
  map.on('click', 'comps-pts', (e) => { const url = e.features[0].properties.url; if (url && /^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener'); });
}

function wireStationHover() {
  for (const [id, label] of [['metrorail-pts', 'Metrorail'], ['metromover-pts', 'Metromover']]) {
    let popup = null;
    map.on('mouseenter', id, (e) => {
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0].properties || {};
      const name = p.STATION || p.NAME || p.STATIONNAME || 'Station';
      popup = new maplibregl.Popup({ closeButton: false, offset: 10 }).setLngLat(e.lngLat).setHTML(`<b>${esc(name)}</b><br/>${label}`).addTo(map);
    });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; popup && popup.remove(); popup = null; });
  }
  // Modular/prefab factories
  let fpop = null;
  const CAT_LABEL = { volumetric_multifamily: 'Volumetric multifamily', residential_modular: 'Residential modular', hud_code: 'HUD-code', panelized: 'Panelized', adu_prefab: 'ADU / prefab' };
  map.on('mouseenter', 'factories-pts', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    const site = p.website && p.website !== 'null' && safeUrl(p.website) ? `<br/><a href="${safeUrl(p.website)}" target="_blank" rel="noopener">${esc(p.website.replace(/^https?:\/\//, '').slice(0, 36))}</a>` : '';
    fpop = new maplibregl.Popup({ closeButton: false, offset: 10 }).setLngLat(e.lngLat)
      .setHTML(`<b>${esc(p.name)}</b><br/>${esc(CAT_LABEL[p.category] || p.category)} · ${esc(p.status || '')}<br/>${esc([p.city, p.state].filter(Boolean).join(', '))}${site}`).addTo(map);
  });
  map.on('mouseleave', 'factories-pts', () => { map.getCanvas().style.cursor = ''; fpop && fpop.remove(); fpop = null; });

}

// ---------- share ----------
function updateHash() {
  const c = map.getCenter();
  const params = new URLSearchParams();
  params.set('z', map.getZoom().toFixed(2));
  params.set('lat', c.lat.toFixed(5));
  params.set('lon', c.lng.toFixed(5));
  params.set('bm', activeBasemap);
  const pitch = map.getPitch(), bearing = map.getBearing();
  if (pitch > 0.5) params.set('pitch', pitch.toFixed(1));
  if (Math.abs(bearing) > 0.5) params.set('bearing', bearing.toFixed(1));
  if (lastSelection) params.set('sel', `${lastSelection.lon.toFixed(6)},${lastSelection.lat.toFixed(6)}`);
  history.replaceState(null, '', '#' + params.toString());
}
function wireHash() {
  map.on('moveend', updateHash);
  map.on('zoomend', updateHash);
  map.on('pitchend', updateHash);
  map.on('rotateend', updateHash);
}
function wireShare() {
  document.getElementById('share-link').addEventListener('click', (e) => {
    e.preventDefault();
    updateHash();
    navigator.clipboard.writeText(window.location.href).then(() => toast('Link copied'), () => toast(window.location.href));
  });
}

// ---------- saved addresses (marks) ----------

function wireTabs() {
  for (const btn of document.querySelectorAll('.tab-btn')) {
    btn.addEventListener('click', () => {
      for (const b of document.querySelectorAll('.tab-btn')) b.classList.toggle('active', b === btn);
      document.getElementById('tab-explore').hidden = btn.dataset.tab !== 'explore';
      document.getElementById('tab-saved').hidden = btn.dataset.tab !== 'saved';
      const tb = document.getElementById('tab-brokers');
      if (tb) tb.hidden = btn.dataset.tab !== 'brokers';
      // opening the Brokers tab with no market scoped yet → default to the map's state
      if (btn.dataset.tab === 'brokers' && !_brokerCtx) refreshBrokerTab();
    });
  }
}

async function loadMarks() {
  try {
    const r = await fetchJSON('/api/marks');
    marksCache = r.marks || [];
  } catch (e) {
    marksCache = [];
  }
  renderMarks();
}

function renderMarks() {
  // map pins (guard: source may not exist yet if the style is still loading)
  const src = map.getSource('marks');
  if (src) src.setData({
    type: 'FeatureCollection',
    features: marksCache.map((m) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [m.lon, m.lat] },
      properties: { id: m.id, address: m.address, note: m.note, zone: m.zone },
    })),
  });

  // tab count pill
  const pill = document.getElementById('saved-count');
  pill.textContent = marksCache.length;
  pill.hidden = marksCache.length === 0;

  // list
  const list = document.getElementById('marks-list');
  if (!marksCache.length) {
    list.classList.add('empty');
    list.innerHTML = 'Nothing saved yet. Select a parcel in Explore and hit ★ Save.';
    return;
  }
  list.classList.remove('empty');
  list.innerHTML = marksCache.map((m) => `
    <div class="mark-item" data-id="${escAttr(m.id)}">
      <div class="mark-head">
        <a class="mark-addr" href="#" data-act="go">${esc(m.address || m.folio || (m.lat.toFixed(5) + ', ' + m.lon.toFixed(5)))}</a>
        <button class="mark-del" data-act="del" title="Remove">&times;</button>
      </div>
      <div class="mark-meta">
        ${m.zone ? `<span class="badge mark-zone">${esc(m.zone)}</span>` : ''}
        ${m.county ? `<span class="mark-county">${esc(m.county)}</span>` : ''}
        <span class="mark-date">${esc((m.created_at || '').slice(0, 10))}</span>
      </div>
      <input class="mark-note" data-act="note" placeholder="Add a note…" value="${escAttr(m.note || '')}" />
    </div>`).join('');

  list.querySelectorAll('.mark-item').forEach((el) => {
    const id = +el.dataset.id;
    const m = marksCache.find((x) => x.id === id);
    el.querySelector('[data-act=go]').addEventListener('click', (e) => {
      e.preventDefault();
      map.flyTo({ center: [m.lon, m.lat], zoom: 17, speed: 1.4 });
      doParcelLookup(m.lon, m.lat);
      document.querySelector('.tab-btn[data-tab=explore]').click();
    });
    el.querySelector('[data-act=del]').addEventListener('click', async () => {
      try {
        const r = await fetch(`/api/marks/${id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        marksCache = marksCache.filter((x) => x.id !== id);
        renderMarks();
        toast('Removed');
      } catch (err) {
        toast("Couldn't delete — is the server up?");
        loadMarks();  // re-sync with what the server actually has
      }
    });
    const noteEl = el.querySelector('[data-act=note]');
    noteEl.addEventListener('change', async () => {
      try {
        const r = await fetch(`/api/marks/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: noteEl.value }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        m.note = noteEl.value;
        toast('Note saved');
      } catch (err) {
        toast("Couldn't save — is the server up?");
        loadMarks();  // re-sync (also restores the input to the stored note)
      }
    });
  });
}

async function saveMark() {
  if (!lastLookup) return;
  let r;
  try {
    r = await fetchJSON('/api/marks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(lastLookup),
    });
  } catch (err) {
    toast("Couldn't save — is the server up?");
    loadMarks();  // re-sync with the server instead of trusting the local cache
    return;
  }
  if (r.created) {
    marksCache.unshift(r.mark);
    renderMarks();
    toast('Address saved');
    const btn = document.getElementById('qi-save');
    if (btn) { btn.textContent = '★ Saved'; btn.classList.add('saved'); btn.disabled = true; }
  } else {
    toast('Already saved');
  }
}

function wireMarkHover() {
  let popup = null;
  map.on('mouseenter', 'marks-pts', (e) => {
    map.getCanvas().style.cursor = 'pointer';
    const p = e.features[0].properties || {};
    popup = new maplibregl.Popup({ closeButton: false, offset: 10 })
      .setLngLat(e.lngLat)
      .setHTML(`<b>★ ${esc(p.address || '')}</b>${p.zone ? '<br/>' + esc(p.zone) : ''}${p.note ? '<br/><i>' + esc(p.note) + '</i>' : ''}`)
      .addTo(map);
  });
  map.on('mouseleave', 'marks-pts', () => {
    map.getCanvas().style.cursor = '';
    popup && popup.remove(); popup = null;
  });
  map.on('click', 'marks-pts', (e) => {
    const [lon, lat] = e.features[0].geometry.coordinates;
    doParcelLookup(lon, lat);
  });
}
