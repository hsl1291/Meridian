/* The buyout waterfall. The FS 718.117 homestead floor is a legal minimum, so
   it must be its own step -- if the holdout bar were still computed as
   total minus FMV, the floor would be drawn as negotiating risk. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const WS = fs.readFileSync(path.join(__dirname, '../../frontend/workspace.js'), 'utf8');

function body(name) {
  const m = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`).exec(WS);
  assert.ok(m);
  let depth = 1, i = m.index + m[0].length;
  while (depth > 0) { if (WS[i] === '{') depth++; else if (WS[i] === '}') depth--; i++; }
  return WS.slice(m.index, i);
}

test('the homestead floor is its own waterfall step, not part of the holdout premium', () => {
  const b = body('loadEconomics');
  assert.match(b, /Homestead floor, FS 718\.117/);
  assert.match(b, /const premium = \(e\.cost_with_holdout \|\| statutory\) - statutory/,
    'the holdout premium must be measured from the statutory total, not from FMV');
});
