/* Failed panels used to say only "Could not load targets." / "Comps
   unavailable." while the server's response carried the actual reason --
   e.g. /api/targets' 503 says "The shared store could not be opened at
   ...; set APPS_SHARED_DB" -- which nobody saw unless they opened the
   network tab. Every catch path now shows errReason(e), which is the
   server's `detail` when there is one and a plain-English cause when there
   isn't (timeout, nothing answering, a bare 500). */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP = fs.readFileSync(path.join(__dirname, '../../frontend/app.js'), 'utf8');
const WS = fs.readFileSync(path.join(__dirname, '../../frontend/workspace.js'), 'utf8');

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

// Run the real helpers from app.js, with fetch stubbed per test.
function sandbox(fetchImpl) {
  const ctx = vm.createContext({ fetch: fetchImpl, AbortSignal, Error, JSON, String, Array });
  vm.runInContext(['esc', 'fetchJSON', 'errReason', 'failMsg'].map((n) => extractFn(APP, n)).join('\n'), ctx);
  return ctx;
}
const reply = (status, body) => async () => ({
  ok: status < 400, status,
  json: async () => { if (body === undefined) throw new SyntaxError('not json'); return body; },
});

test("the server's own detail reaches the user", async () => {
  const detail = 'The shared store could not be opened at C:\\x\\shared.db; set APPS_SHARED_DB';
  const ctx = sandbox(reply(503, { state: 'shared_store_missing', detail }));
  const e = await ctx.fetchJSON('/api/targets').then(() => null, (x) => x);
  assert.equal(e.status, 503);
  assert.equal(ctx.errReason(e), detail);
  assert.ok(ctx.failMsg('Could not load targets', e).includes('APPS_SHARED_DB'));
});

test('a FastAPI 422 list becomes "field: problem", not a JSON blob', async () => {
  const ctx = sandbox(reply(422, { detail: [
    { loc: ['body', 'termination_threshold'], msg: 'Input should be a valid number' },
  ] }));
  const e = await ctx.fetchJSON('/x').then(() => null, (x) => x);
  assert.equal(ctx.errReason(e), 'termination_threshold: Input should be a valid number');
});

test('a bare 500 points at the log instead of just saying "HTTP 500"', async () => {
  const ctx = sandbox(reply(500, undefined));
  const e = await ctx.fetchJSON('/x').then(() => null, (x) => x);
  assert.match(ctx.errReason(e), /HTTP 500.*server\.err\.log/);
});

test('timeouts and dead servers get a cause a user can act on', () => {
  const ctx = sandbox(async () => {});
  assert.match(ctx.errReason(Object.assign(new Error('x'), { name: 'TimeoutError' })), /too long/);
  assert.match(ctx.errReason(new TypeError('Failed to fetch')), /still running/);
});

test('failMsg escapes the reason -- server text goes into innerHTML', () => {
  const ctx = sandbox(async () => {});
  const out = ctx.failMsg('X', new Error('<img src=x onerror=alert(1)>'));
  assert.ok(!out.includes('<img'), out);
});

test('no workspace.js catch path falls back to a reason-less message', () => {
  // Every catch block that writes a message must pass the error along.
  // Brace-matched, so a catch spanning several lines is checked too.
  const bad = [];
  const re = /catch \((\w+)\) \{/g;
  let m;
  while ((m = re.exec(WS))) {
    let depth = 1, i = re.lastIndex;
    while (depth > 0 && i < WS.length) {
      if (WS[i] === '{') depth++;
      else if (WS[i] === '}') depth--;
      i++;
    }
    const body = WS.slice(re.lastIndex, i - 1);
    const writes = /innerHTML\s*=|textContent\s*=|showHits\(/.test(body);
    if (writes && !new RegExp(`\\b${m[1]}\\b`).test(body)) bad.push(body.trim().slice(0, 120));
  }
  assert.deepEqual(bad, [], 'these catch blocks show a message without the reason');
});

test('the save buttons report why a save failed (and notice a dead server)', () => {
  assert.ok(!/'Save failed\.'/.test(WS), 'a bare "Save failed." hides the 422 that says which field is wrong');
  assert.equal((WS.match(/Not saved — \$\{errReason\(e\)\}/g) || []).length, 2);
});
