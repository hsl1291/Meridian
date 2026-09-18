/* The shared building/metro drawer (#t-detail and its sub-panels).
   openDrawer (building) and openMarket (metro) both render into the same
   #t-detail element and then fire off several sub-panel loaders that each
   run their own fetch and write into their own child element. None of them
   checked whether the drawer had already moved on to a different selection
   by the time their fetch resolved -- click building A, then quickly click
   building B, and whichever response happened to arrive LAST would win,
   even if it was A's, leaving B's drawer open with A's data in it (or a mix
   of both, since each sub-panel resolves independently).

   Fixed with one shared counter, detailToken, bumped by whichever of
   openDrawer/openMarket/closeDrawer runs, and checked by every async
   renderer after its own fetch before it touches the DOM -- so a slower
   response for a previous selection can never overwrite a newer one. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WS = fs.readFileSync(path.join(__dirname, '../../frontend/workspace.js'), 'utf8');

// Same brace-counting extractor as overlay-cancellation.test.js -- a plain
// regex can't find the matching close brace when the body has nested `{ }`.
function extractFn(src, name) {
  const m = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`).exec(src);
  assert.ok(m, `function ${name} not found in workspace.js`);
  let depth = 1;
  let i = m.index + m[0].length;
  const start = i;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  assert.ok(depth === 0, `unbalanced braces scanning ${name}`);
  return src.slice(start, i - 1);
}

test('detailToken is declared once at module scope', () => {
  const decls = (WS.match(/\blet detailToken\b/g) || []).length;
  assert.equal(decls, 1, `detailToken declared ${decls} times, expected 1`);
});

test('closeDrawer invalidates whatever is still in flight', () => {
  const body = extractFn(WS, 'closeDrawer');
  assert.ok(/detailToken\+\+/.test(body),
    'closeDrawer must bump detailToken so an in-flight fetch for the closed selection is dropped');
});

// name -> [is the trigger fn that OWNS the token (increments it) or a
// follower that just reads the ambient value, fetch-guarded on both the
// catch and success paths]
const OWNERS = ['openDrawer', 'openMarket'];
const FOLLOWERS = ['loadDeal', 'loadDeclarations', 'loadBeneficial', 'loadEconomics',
  'loadComps', 'drawFlows', 'drawComponents', 'drawTightness'];

for (const fn of OWNERS) {
  test(`${fn}: owns detailToken and bails if a newer selection replaced it`, () => {
    const body = extractFn(WS, fn);
    assert.ok(/myToken\s*=\s*\+\+detailToken/.test(body),
      `${fn} must increment detailToken to claim this selection`);
    assert.ok(/detailToken\s*!==\s*myToken/.test(body),
      `${fn} never checks detailToken after its fetch -- a stale response could still render`);
    assert.ok(/catch[\s\S]{0,120}detailToken\s*===\s*myToken/.test(body),
      `${fn}'s error path must also check detailToken before touching the DOM`);
  });
}

for (const fn of FOLLOWERS) {
  test(`${fn}: reads detailToken and bails if a newer selection replaced it`, () => {
    const body = extractFn(WS, fn);
    assert.ok(/myToken\s*=\s*detailToken\b/.test(body),
      `${fn} must capture detailToken at its own start`);
    assert.ok(/detailToken\s*!==\s*myToken/.test(body),
      `${fn} never checks detailToken after its fetch -- a stale response could still render`);
    assert.ok(/catch[\s\S]{0,160}detailToken\s*===\s*myToken/.test(body),
      `${fn}'s error path must also check detailToken before touching the DOM`);
  });
}

test('wireCapacity guards against both a newer selection and a re-click for the same building', () => {
  const body = extractFn(WS, 'wireCapacity');
  assert.ok(/openToken\s*=\s*detailToken/.test(body),
    'wireCapacity must remember which selection it was wired for');
  assert.ok(/let capSeq\s*=\s*0/.test(body),
    'wireCapacity must have its own per-click sequence guard');
  assert.ok(/mySeq\s*=\s*\+\+capSeq/.test(body));
  assert.ok(/detailToken\s*!==\s*openToken\s*\|\|\s*capSeq\s*!==\s*mySeq/.test(body),
    'a re-click (or a closed/replaced drawer) must stop a stale capacity render');
});

test('ddSearch guards against overlapping searches (the Enter key is not blocked by btn.disabled)', () => {
  const body = extractFn(WS, 'ddSearch');
  assert.ok(/mySeq\s*=\s*\+\+ddSeq/.test(body));
  assert.ok(/ddSeq\s*!==\s*mySeq/.test(body),
    'ddSearch never checks its own sequence after the fetch -- an old query could show stale hits');
});

test('every sub-panel token check happens AFTER the fetch, not before', () => {
  // A guard placed before the await would compile and read plausibly but do
  // nothing -- confirm the check text appears after "await fetchJSON" in
  // each function, not just somewhere in the body.
  for (const fn of [...OWNERS, ...FOLLOWERS]) {
    const body = extractFn(WS, fn);
    const fetchIdx = body.search(/await fetchJSON|await Promise\.all/);
    const checkIdx = body.search(/detailToken\s*!==\s*myToken/);
    assert.ok(fetchIdx >= 0, `${fn} has no awaited fetch to guard`);
    assert.ok(checkIdx > fetchIdx, `${fn}'s staleness check must come after its fetch`);
  }
});
