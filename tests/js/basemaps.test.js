/* Basemaps and glyphs. These are the settings that took the map down when a
   provider changed its terms, so they are pinned rather than trusted. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const APP = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
const HTML = fs.readFileSync(path.join(__dirname, '../../frontend/index.html'), 'utf8');

test('nothing points at CARTO any more', () => {
  // Their raster endpoints began requiring a key and serving tiles watermarked
  // "API KEY REQUIRED" -- a 200 response, so nothing errored and nothing retried.
  assert.equal(/cartocdn|carto\.com/i.test(APP), false);
  assert.equal(/cartocdn|carto\.com/i.test(HTML), false);
});

test('every basemap and the glyph host is keyless', () => {
  // Tile URLs are template literals against the ESRI constant, so resolve that
  // first -- a regex for a bare https:// misses all of them and would pass an
  // empty set.
  const base = /const ESRI = '([^']+)'/.exec(APP);
  assert.ok(base, 'ESRI base URL constant is missing');
  const templates = [...APP.matchAll(/`\$\{ESRI\}([^`]+)`/g)].map((m) => base[1] + m[1])
    .concat([...APP.matchAll(/'(https:\/\/[^']*\{(?:z|fontstack)\}[^']*)'/g)].map((m) => m[1]));

  assert.ok(templates.length >= 5, `expected tile and glyph URLs, found ${templates.length}`);
  for (const u of templates) {
    assert.equal(/[?&](api_?key|access_token|key)=/i.test(u), false, u);
    assert.ok(u.startsWith('https://'), u);
  }
});

test('the glyph endpoint is declared once and used by name', () => {
  assert.ok(/const GLYPHS = 'https:\/\/[^']+\{fontstack\}[^']*'/.test(APP));
  assert.ok(/glyphs: GLYPHS,/.test(APP));
});

test('every text layer pins a font stack rather than inheriting the default', () => {
  // MapLibre's default is ["Open Sans Regular","Arial Unicode MS Regular"] and it
  // requests the whole comma-joined stack as one path, so a host missing EITHER
  // face 404s and the layer vanishes with no error anyone sees.
  const fields = (APP.match(/'text-field':/g) || []).length;
  const fonts = (APP.match(/'text-font': FONT,/g) || []).length;
  assert.ok(fields > 0);
  assert.equal(fonts, fields, `${fonts} text-font for ${fields} text-field`);
  assert.ok(/const FONT = \['[^']+'\];/.test(APP), 'FONT must name exactly one face');
});

test('a basemap without its own labels declares a reference overlay', () => {
  // Esri's Light Gray Base and World Imagery carry no place names at all, so
  // omitting this renders an unlabelled map rather than a subtly different one.
  const block = APP.slice(APP.indexOf('const BASEMAPS = {'), APP.indexOf('// ---------- hash'));
  for (const key of ['satellite', 'light']) {
    const seg = block.slice(block.indexOf(`${key}: {`));
    assert.ok(seg.slice(0, seg.indexOf('},')).includes('labels:'), `${key} needs a labels overlay`);
  }
});

test('attribution is per service, not one blanket string', () => {
  // It is a licence condition, and imagery and vector credit different sources.
  assert.ok(APP.includes('ESRI_IMAGERY_ATTR'));
  assert.ok(APP.includes('ESRI_VECTOR_ATTR'));
  assert.ok(/Maxar/.test(APP), 'imagery attribution must credit Maxar');
  assert.ok(/OpenStreetMap/.test(APP), 'vector attribution must credit OSM');
});

test('a tile or glyph failure surfaces something the user can see', () => {
  assert.ok(APP.includes("map.on('error'"));
  assert.ok(/function warnMap\(/.test(APP));
  assert.ok(APP.includes('map-warning'));
});
