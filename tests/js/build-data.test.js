/* A fresh install has none of the big tables, and the screens that need them
   used to say only "the shared store could not be opened". They now point at
   Reference > Build data, which runs the build scripts and shows progress. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
const WS = fs.readFileSync(path.join(__dirname, '../../frontend/workspace.js'), 'utf8');
const HTML = fs.readFileSync(path.join(__dirname, '../../frontend/index.html'), 'utf8');

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

function appCtx(fetchImpl) {
  const ctx = vm.createContext({ fetch: fetchImpl, AbortSignal, Error, JSON, String, Array });
  vm.runInContext(['esc', 'fetchJSON', 'errReason', 'failMsg'].map((n) => extractFn(APP, n)).join('\n'), ctx);
  return ctx;
}
const reply = (status, body) => async () => ({ ok: status < 400, status, json: async () => body });

test('fetchJSON keeps the server\'s `state` on the error', async () => {
  const c = appCtx(reply(503, { state: 'table_not_built', detail: 'x' }));
  const e = await c.fetchJSON('/api/metros').then(() => null, (x) => x);
  assert.equal(e.state, 'table_not_built');
});

test('only an unbuilt-store error gets the "Build the data" link', async () => {
  for (const state of ['shared_store_missing', 'table_not_built']) {
    const c = appCtx(reply(503, { state, detail: 'not built' }));
    const e = await c.fetchJSON('/x').then(() => null, (x) => x);
    assert.match(c.failMsg('Could not load markets', e), /data-goto-build/);
  }
  const c = appCtx(reply(500, { detail: 'boom' }));
  const e = await c.fetchJSON('/x').then(() => null, (x) => x);
  assert.doesNotMatch(c.failMsg('Could not load markets', e), /data-goto-build/);
});

function renderCtx() {
  const box = { innerHTML: '' };
  const ctx = vm.createContext({
    el: (id) => (id === 'build-body' ? box : null),
    fmt: (n) => String(n), Array, Math,
  });
  vm.runInContext(extractFn(APP, 'esc') + '\n' + extractFn(WS, 'renderBuild'), ctx);
  return { ctx, box };
}
const groups = [
  { id: 'markets', title: 'National market data', detail: 'd', size: '5 min', built: false, rows: null, steps: ['a', 'b'] },
  { id: 'condo', title: 'Condo screen', detail: 'd', size: '20 min', built: true, rows: 5456, steps: ['a'] },
];

test('the card offers Build for what is missing and Rebuild for what is there', () => {
  const { ctx, box } = renderCtx();
  ctx.renderBuild({ groups, job: null });
  assert.match(box.innerHTML, /data-build="markets"[^>]*>\s*Build\s*</);
  assert.match(box.innerHTML, /data-build="condo"[^>]*>\s*Rebuild\s*</);
  assert.match(box.innerHTML, /5456 rows/);
});

test('a running build shows its step, offers Cancel, and locks the other buttons', () => {
  const { ctx, box } = renderCtx();
  ctx.renderBuild({ groups, job: { group: 'markets', state: 'running', step: 1, steps: ['a', 'b'], tail: ['row 100'], log: 'l' } });
  assert.match(box.innerHTML, /step 2 of 2: b/);
  assert.match(box.innerHTML, /data-build-cancel/);
  assert.equal((box.innerHTML.match(/ disabled/g) || []).length, 2, 'both Build buttons disabled while running');
  assert.match(box.innerHTML, /row 100/);
});

test('a failed build shows why, with the log tail and its path', () => {
  const { ctx, box } = renderCtx();
  ctx.renderBuild({ groups, job: { group: 'markets', state: 'failed', step: 0, steps: ['a', 'b'],
    error: 'a failed (exit 1).', tail: ['503 from census.gov <b>'], log: 'C:\\app\\logs\\build-markets.log' } });
  assert.match(box.innerHTML, /a failed \(exit 1\)/);
  assert.match(box.innerHTML, /503 from census\.gov &lt;b&gt;/, 'log text must be escaped');
  assert.match(box.innerHTML, /build-markets\.log/);
});

test('the card exists, loads on first visit to Reference, and the link opens it', () => {
  assert.match(HTML, /id="build-card"/);
  assert.match(HTML, /id="build-body"/);
  assert.match(WS, /refLoaded = true; wireDataBuild\(\)/);
  assert.match(WS, /closest\('\[data-goto-build\]'\)/);
});

test('polling only continues while a job is running', () => {
  const b = extractFn(WS, 'refreshBuild');
  assert.match(b, /st\.job && st\.job\.state === 'running'\) buildTimer = setTimeout\(refreshBuild/);
});
