import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const source = await readFile(resolve(root, 'python/qte/gui/static/weekly_reversal_report.js'));
const { renderWeeklyReversal } = await import('data:text/javascript;base64,' + source.toString('base64'));

class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = []; this.listeners = new Map();
    this.value = ''; this.textContent = '';
    for (const [key, value] of Object.entries(attrs)) key === 'text' ? this.textContent = value : this.setAttribute(key, value);
    this.append(...children);
  }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'value') this.value = String(value); }
  append(...nodes) {
    for (const value of nodes) { const node = typeof value === 'string' ? new Node('#text', { text: value }) : value; node.parentElement = this; this.childNodes.push(node); }
    if (this.tagName === 'SELECT' && !this.value && this.childNodes.length) this.value = this.childNodes[0].value;
  }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  addEventListener(type, callback) { const values = this.listeners.get(type) || []; values.push(callback); this.listeners.set(type, values); }
  async emit(type) { for (const callback of this.listeners.get(type) || []) await callback({ target: this }); }
}
const walk = node => [node, ...node.childNodes.flatMap(walk)];
const ui = { element: (tag, attrs, children) => new Node(tag, attrs, children) };
const summary = (net, entries = '1') => ({ sessions: '2', entries, net_pnl: net, risk_deployed: 100,
  unresolved: 0, mean_entries: entries / 2, median_entries: entries / 2 });
const candidate = (id, net) => ({ id, slices: { all: summary(net, 2), evaluation: summary(net / 2) },
  trades: [{ session: 's1', signal_asof_ns: '9007199254740993', decision_available_ns: '9007199254740994',
    entry_ns: '9007199254740995', exit_ns: '9007199254740996', risk_deployed: 50, net_pnl: net }],
  unresolved: [], skipped: [{ session: 's2', reason: 'debit_cap' }, { session: 's2', reason: 'debit_cap' }] });

test('weekly reversal report renders scenarios, selector details, exact timing, and semantic gamma grid', async () => {
  const report = { evidence: 'synthetic_mechanics_only', session_metadata: {
    s1: { case: 'reversal', split: 'design' }, s2: { case: 'wide_spreads', split: 'evaluation' },
  }, runs: {
    base: { candidates: [candidate('alpha', 10), candidate('beta', -2)] },
    stress: { candidates: [candidate('alpha', 5), candidate('beta', -4)] },
  }, gamma: { model: 'american_crr', spots: ['449', '450'], days: ['5', '6'], gamma: [[0.01, 0.02], [0.03, 0.04]] } };
  const nodes = renderWeeklyReversal(ui, report);
  assert.equal(nodes.length, 1);
  let all = walk(nodes[0]);
  assert.ok(all.some(node => node.textContent.includes('Synthetic mechanics fixture only')));
  assert.ok(all.some(node => node.textContent === 'American put gamma scenarios'));
  assert.ok(all.some(node => node.textContent === '449'));
  assert.ok(all.some(node => node.textContent === '9007199254740994'));
  assert.ok(all.some(node => node.textContent === '2')); // compacted duplicate skip count
  const select = all.find(node => node.tagName === 'SELECT');
  assert.equal(select.value, 'alpha');
  select.value = 'beta'; await select.emit('change');
  all = walk(nodes[0]);
  assert.ok(all.some(node => node.textContent === 'Cases · beta'));
  assert.ok(all.some(node => node.textContent.includes('Trade and skip inspection · beta')));
});
