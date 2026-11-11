// A minimal DOM double exercises chart facts and keyboard state, not browser layout.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../python/qte/gui/static/market_chart.js', import.meta.url), 'utf8');
const { timestampX, regimeAt, renderMarketChart } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag; this.attrs = {}; this.children = []; this.events = {};
    for (const [key, value] of Object.entries(attrs)) key === 'text' ? this.textContent = value : this.setAttribute(key, value);
    this.append(...children);
  }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'value') this.value = String(value); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(key, callback) { this.events[key] = callback; }
  querySelector(selector) { return walk(this).find(node => selector.startsWith('.') ? (node.attrs.class || '').split(' ').includes(selector.slice(1)) : node.tagName === selector); }
  focus() {}
}
const walk = node => [node, ...node.children.flatMap(walk)];
const ui = { element: (tag, attrs, children) => new Node(tag, attrs, children) };
globalThis.document = { createElementNS: (_, tag) => new Node(tag) };
const origin = 1700000000000000000n;
const ts = delta => String(origin + BigInt(delta));
const candle = index => ({ start_ns: ts(index * 10), end_ns: ts((index + 1) * 10), open: 100, high: 105, low: 99, close: 104, volume: 1000 });
const fixture = () => ({ status: 'recorded', symbol: 'SPY', candles: [candle(0), candle(1)],
  regimes: [{ timestamp_ns: ts(10), macro_regime: 'risk_on', volatility_regime: 'low' }],
  runs: [{ candidate: 'trend', status: 'ok', fills: [{ timestamp_ns: ts(10), signal_ns: ts(10), side: 'BUY', quantity: '1', price: 100, commission: 1, order_id: '1' }],
    equity: [{ timestamp_ns: ts(0), equity: 1000 }, { timestamp_ns: ts(10), equity: 1001 }, { timestamp_ns: ts(10), equity: 1000 }, { timestamp_ns: ts(20), equity: 1004 }] }],
});

test('relative axis preserves one-nanosecond distinctions above JS safe integer range', () => {
  assert.equal(timestampX(ts(1), ts(0), ts(10), 0, 100), 10);
  assert.notEqual(timestampX(ts(1), ts(0), ts(10)), timestampX(ts(2), ts(0), ts(10)));
});

test('as-of regime is unavailable before publication, not backfilled to candle open', () => {
  const rows = fixture().regimes;
  assert.equal(regimeAt(rows, ts(9)), null);
  assert.equal(regimeAt(rows, ts(10)).macro_regime, 'risk_on');
  const section = renderMarketChart(ui, fixture());
  const exact = section.querySelector('pre');
  const facts = JSON.parse(exact.textContent);
  assert.equal(facts.regime_at_open, null);
  assert.equal(facts.regime_at_close.macro_regime, 'risk_on');
  assert.equal(facts.executions[0].fills.length, 0); // Fill at next open, not prior candle.
  const band = walk(section).find(node => (node.attrs.class || '').includes('market-regime-0'));
  assert.equal(Number(band.attrs.x), timestampX(ts(10), ts(0), ts(20)));
});

test('actual fill and submission markers remain distinct; duplicate-time equity rows survive', () => {
  const section = renderMarketChart(ui, fixture());
  assert.equal(walk(section).filter(node => (node.attrs.class || '').startsWith('market-fill ')).length, 1);
  assert.equal(walk(section).filter(node => (node.attrs.class || '').startsWith('market-submission ')).length, 1);
  const equity = section.querySelector('.market-equity');
  assert.equal((equity.attrs.d.match(/L/g) || []).length, 3);
  section.querySelector('svg').events.keydown({ key: 'ArrowRight', preventDefault() {} });
  const facts = JSON.parse(section.querySelector('pre').textContent);
  assert.equal(facts.candle.start_ns, ts(10));
  assert.equal(facts.executions[0].fills[0].timestamp_ns, ts(10));
});

test('legacy unavailable state draws no fabricated candles', () => {
  const section = renderMarketChart(ui, { status: 'not_recorded', reason: 'Not recorded' });
  assert.equal(section.querySelector('svg'), undefined);
  assert.equal(section.children[0].textContent, 'Not recorded');
});

test('large windows are paged as raw candles, not silently aggregated', () => {
  const data = fixture(); data.candles = Array.from({ length: 140 }, (_, index) => candle(index));
  const section = renderMarketChart(ui, data);
  assert.equal(walk(section).filter(node => node.attrs.class === 'market-candle').length, 128);
  section.querySelector('svg').events.keydown({ key: 'End', preventDefault() {} });
  assert.equal(walk(section).filter(node => node.attrs.class === 'market-candle').length, 12);
  assert.equal(JSON.parse(section.querySelector('pre').textContent).candle.start_ns, ts(1390));
});
