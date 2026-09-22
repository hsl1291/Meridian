/* The map library used to load from unpkg.com. When that CDN was slow,
   blocked by a firewall, or the machine was offline, `maplibregl` was
   undefined, app.js threw on its first line that touches it, and the whole
   UI came up blank with no explanation. It is now served from
   frontend/vendor/ by the app's own static mount, so nothing the map needs
   to boot comes from a third party. (Google Fonts is still remote on
   purpose: a failed font load falls back to the system font and breaks
   nothing.) */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const FRONTEND = path.join(__dirname, '../../frontend');
const HTML = fs.readFileSync(path.join(FRONTEND, 'index.html'), 'utf8');

test('index.html loads no script from another origin', () => {
  const srcs = [...HTML.matchAll(/<script[^>]*\bsrc="([^"]+)"/g)].map(m => m[1]);
  assert.ok(srcs.length > 0, 'expected at least one <script src>');
  const remote = srcs.filter(s => /^(https?:)?\/\//i.test(s));
  assert.deepEqual(remote, [], `scripts must be served locally: ${remote.join(', ')}`);
});

test('index.html loads no stylesheet from a CDN (fonts excepted)', () => {
  const hrefs = [...HTML.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map(m => m[1]);
  const remote = hrefs.filter(h => /^(https?:)?\/\//i.test(h) && !h.startsWith('https://fonts.googleapis.com/'));
  assert.deepEqual(remote, [], `stylesheets must be served locally: ${remote.join(', ')}`);
});

test('every local script and stylesheet index.html references exists on disk', () => {
  const refs = [
    ...[...HTML.matchAll(/<script[^>]*\bsrc="([^"]+)"/g)].map(m => m[1]),
    ...[...HTML.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map(m => m[1]),
  ].filter(r => !/^(https?:)?\/\//i.test(r));
  for (const r of refs) {
    const file = path.join(FRONTEND, r.split('?')[0]);
    assert.ok(fs.existsSync(file), `index.html references ${r} but ${file} is missing`);
  }
});

test('the vendored MapLibre ships with its license', () => {
  const m = /vendor\/(maplibre-gl-[\d.]+)\/maplibre-gl\.js/.exec(HTML);
  assert.ok(m, 'index.html must load MapLibre from frontend/vendor/');
  const dir = path.join(FRONTEND, 'vendor', m[1]);
  assert.ok(fs.existsSync(path.join(dir, 'LICENSE.txt')), 'BSD-3 requires the license to travel with the code');
  const js = fs.readFileSync(path.join(dir, 'maplibre-gl.js'), 'utf8').slice(0, 400);
  assert.ok(js.includes(m[1].replace('maplibre-gl-', 'v')),
    'the version in the folder name must match the bundle header');
});
