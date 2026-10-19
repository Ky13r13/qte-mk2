// Dependency-free state-machine tests, not a substitute for browser/layout QA.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../../python/qte/gui/static/comparisons.js', import.meta.url));
const { createComparisonViews } = await import('data:text/javascript;base64,' + source.toString('base64'));
class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag; this.attrs = {}; this.children = []; this.handlers = {};
    this.textContent = attrs.text || ''; this.value = attrs.value || ''; this.disabled = false;
    for (const [key, value] of Object.entries(attrs)) if (key !== 'text') this.setAttribute(key, value);
    this.append(...children);
  }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(event, action) { this.handlers[event] = action; }
  async emit(event) { await this.handlers[event]?.({ preventDefault() {} }); }
}
const walk = node => [node, ...node.children.flatMap(walk)];
const detail = { name: '<img src=x onerror=alert(1)>', workflow: 'historical_experiment',
  windows: [{ name: 'train', role: 'train', regime: 'low' }, { name: 'secret', role: 'test', regime: 'secret' }],
  warnings: ['Promotion eligibility withheld.'], status: {} };
const page = (offset = 0, total = 26) => ({ columns: ['candidate', 'validation_score', 'exclusions', 'configured_eligible', 'parameters'],
  rows: [{ candidate: 'alpha', validation_score: [0.1, 0.2], exclusions: [], configured_eligible: true,
    parameters: { quantity: '9223372036854775807' } }], offset, limit: 25, total, warnings: [] });
function harness(reply) {
  let generation = 0; let view = new Node('main');
  const calls = [], fallback = [];
  const ui = {
    element: (...args) => new Node(...args), textLink: (href, text) => new Node('a', { href, text }),
    api: async url => { calls.push(url); return reply(url); },
    beginView: () => ++generation, isCurrentView: id => id === generation,
    replaceView: (...nodes) => { view = new Node('main', {}, nodes); },
    setStatus() {}, setNavigation() {}, humanize: value => value,
    displayValue: value => value === null || value === undefined ? 'Not recorded' :
      Array.isArray(value) ? value.join('; ') : typeof value === 'object' ? 'Not recorded' : String(value),
    loadArtifact: async (...args) => { fallback.push(args); },
  };
  return { ui, calls, fallback, nodes: () => walk(view),
    button: text => walk(view).find(n => n.tag === 'button' && n.textContent === text) };
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('comparison metrics require an explicit safe window', async () => {
  const h = harness(url => url.includes('/tables/') ? page() : { comparison: detail });
  const views = createComparisonViews(h.ui);
  await views.load('id', 'comparison');
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.nodes().filter(n => n.tag === 'option').map(n => n.attrs.value), ['', 'train']);
  await views.load('id', 'comparison', 'secret');
  assert.equal(h.calls.length, 2);
  const select = h.nodes().find(n => n.tag === 'select');
  select.value = 'train';
  await h.nodes().find(n => n.tag === 'form').emit('submit');
  assert.match(h.calls.at(-1), /tables\/comparison\?offset=0&limit=25&window=train$/);
});

test('four columns, exact-value row details, paging, and text-only markup', async () => {
  const h = harness(url => url.includes('/tables/') ? page(Number(new URL(url, 'http://local').searchParams.get('offset'))) : { comparison: detail });
  await createComparisonViews(h.ui).load('id');
  assert.equal(h.nodes().filter(n => n.tag === 'th').length, 4);
  assert.ok(h.nodes().some(n => n.tag === 'h1' && n.textContent === detail.name));
  assert.ok(h.nodes().some(n => n.tag === 'p' && n.textContent.includes('Evidence: unknown')));
  assert.ok(!h.nodes().some(n => n.tag === 'img'));
  await h.button('alpha').emit('click');
  assert.ok(h.nodes().some(n => n.textContent.includes('9223372036854775807')));
  await h.button('Back to records').emit('click');
  assert.equal(h.button('Previous').disabled, true);
  await h.button('Next').emit('click');
  assert.match(h.calls.at(-1), /offset=25&limit=25$/);
  assert.equal(h.button('Next').disabled, true);
  await h.button('Previous').emit('click');
  assert.match(h.calls.at(-1), /offset=0&limit=25$/);
});

test('a stale detail response cannot request or replace the current table', async () => {
  const slow = deferred();
  const h = harness(url => url === '/api/v1/comparisons/old' ? slow.promise :
    url.includes('/tables/') ? page() : { comparison: { ...detail, name: 'current' } });
  const views = createComparisonViews(h.ui);
  const old = views.load('old');
  await views.load('new');
  slow.resolve({ comparison: detail }); await old;
  assert.ok(!h.calls.some(url => url.includes('/old/tables/')));
  assert.equal(h.nodes().find(n => n.tag === 'h1').textContent, 'current');
});

test('stale table errors and detached row handlers cannot restore old views', async () => {
  const slow = deferred();
  const h = harness(url => url.includes('/old/tables/') ? slow.promise :
    url.includes('/tables/') ? page() : { comparison: { ...detail, name: url.endsWith('/old') ? 'old' : 'new' } });
  const views = createComparisonViews(h.ui);
  const pending = views.load('old');
  await new Promise(resolve => setImmediate(resolve));
  await views.load('new');
  const detached = h.button('alpha');
  h.ui.beginView(); h.ui.replaceView(new Node('h1', { text: 'different page' }));
  slow.resolve({ bad: true }); await pending;
  await detached.emit('click');
  assert.equal(h.nodes().find(n => n.tag === 'h1').textContent, 'different page');
  assert.equal(h.fallback.length, 0);
});

test('current API failure falls back to guarded catalog, never a raw download', async () => {
  const h = harness(() => { throw new Error('Protected input'); });
  await createComparisonViews(h.ui).load('id');
  assert.deepEqual(h.fallback, [['id', 0, true, 'overview', 'Protected input']]);
  assert.equal(h.calls.length, 1);
});
