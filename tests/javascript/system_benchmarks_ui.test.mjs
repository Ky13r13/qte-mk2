import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../python/qte/gui/static/system_benchmarks.js', import.meta.url));
const { createSystemBenchmarks } = await import('data:text/javascript;base64,' + source.toString('base64'));
let focused = null;
class Node {
  constructor(tag, attrs = {}, children = []) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = []; this.listeners = new Map(); this.disabled = this.hidden = false; this.open = false; this.textContent = ''; this.dataset = {}; for (const [key, value] of Object.entries(attrs)) key === 'text' ? this.textContent = String(value) : this.setAttribute(key, value); this.append(...children); }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'class') this.className = String(value); if (['id', 'type', 'href', 'value', 'min', 'max'].includes(key)) this[key] = String(value); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.childNodes.push(node); } }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  addEventListener(type, handler) { const items = this.listeners.get(type) || []; items.push(handler); this.listeners.set(type, items); }
  async emit(type) { for (const handler of this.listeners.get(type) || []) await handler({ currentTarget: this, target: this, preventDefault() {} }); }
  focus() { focused = this; }
}
const walk = node => [node, ...node.childNodes.flatMap(walk)];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const job = (id, state = 'succeeded') => ({ id, kind: 'benchmark', state, created_ns: '1', start_ns: '2', end_ns: '3', timeout_seconds: 300 });
const list = (items = [], offset = 0) => ({ schema_version: 1, items, total: items.length === 10 ? 11 : items.length, offset, limit: 10 });
const unavailable = item => ({ schema_version: 1, job: item, evidence: { state: 'unavailable' } });
function harness(reply) {
  globalThis.document = new Node('document'); globalThis.document.createTextNode = text => new Node('#text', { text }); Object.defineProperty(globalThis.document, 'activeElement', { get: () => focused });
  let view = new Node('main'), generation = 0, dispose = () => {}; const calls = [], statuses = [], navigation = [];
  const ui = { element: (tag, attrs, children) => new Node(tag, attrs, children), textLink: (href, text) => new Node('a', { href, text }), api: async (url, options) => { calls.push({ url, options }); return reply(url, options); }, beginView: () => { dispose(); return ++generation; }, isCurrentView: value => value === generation, replaceView: (...nodes) => view.replaceChildren(...nodes), setStatus: text => statuses.push(text), setNavigation: section => navigation.push(section), humanize: value => String(value || 'unknown').replaceAll('_', ' '), displayValue: (value, fallback = 'Not recorded') => value === undefined || value === null || value === '' ? fallback : String(value) };
  return { ui, calls, statuses, navigation, setDispose: fn => { dispose = fn; }, get view() { return view; }, nodes: () => walk(view), button(text) { const found = walk(view).find(node => node.tagName === 'BUTTON' && node.textContent === text); assert.ok(found, `button ${text}`); return found; }, input() { return walk(view).find(node => node.tagName === 'INPUT'); }, select() { return walk(view).find(node => node.tagName === 'SELECT'); } };
}
afterEach(() => { delete globalThis.document; focused = null; });

test('opening is passive and the form has the exact bounded default request surface', async () => {
  const h = harness((url, options) => { assert.equal(options, undefined); assert.equal(url, '/api/v1/system/benchmarks?offset=0&limit=10'); return list(); }); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load();
  assert.equal(h.calls.length, 1); assert.equal(h.input().value, '10000'); assert.equal(h.select().value, 'hold'); assert.equal(h.button('Run benchmark').disabled, false); assert.ok(h.nodes().some(node => node.textContent.includes('never runs a benchmark'))); assert.deepEqual(h.navigation, ['system']);
});

test('submit validates input and sends only an explicit exact benchmark DTO', async () => {
  const id = 'a'.repeat(32); const h = harness((url, options) => { if (!options) return url.includes(id) ? unavailable(job(id, 'queued')) : list(); if (url === '/api/v1/system/benchmarks') return { job: job(id, 'queued') }; assert.fail(url); }); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load();
  h.input().value = '100001'; await h.button('Run benchmark').emit('click'); assert.equal(h.calls.length, 1);
  h.input().value = '42'; h.select().value = 'sma'; await h.button('Run benchmark').emit('click'); const body = JSON.parse(h.calls.find(call => call.options)?.options.body); assert.deepEqual(Object.keys(body).sort(), ['bars', 'idempotency_key', 'schema_version', 'strategy', 'timeout_seconds']); assert.deepEqual({ ...body, idempotency_key: 'uuid' }, { schema_version: 1, bars: 42, strategy: 'sma', timeout_seconds: 300, idempotency_key: 'uuid' }); assert.match(body.idempotency_key, /^[0-9a-f]{8}-[0-9a-f]{4}-4/i);
});

test('ambiguous submission freezes retry bytes, while a 4xx after retry remains uncertain', async () => {
  let posts = 0; const h = harness((url, options) => { if (!options) return list(); if (url === '/api/v1/system/benchmarks') { posts += 1; throw Object.assign(new Error(posts === 1 ? 'timeout' : 'bad request'), { status: posts === 1 ? 503 : 409 }); } }); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load();
  await h.button('Run benchmark').emit('click'); const first = h.calls.at(-1).options.body; await h.button('Retry same request').emit('click'); assert.equal(h.calls.at(-1).options.body, first); assert.ok(h.button('Retry same request')); assert.equal(h.button('Run benchmark').disabled, true);
});

test('list paging and stale detached page controls are guarded', async () => {
  const jobs = Array.from({ length: 11 }, (_, i) => job(String(i))); const h = harness(url => { const offset = Number(new URL(url, 'http://x').searchParams.get('offset')); return { schema_version: 1, items: jobs.slice(offset, offset + 10), total: 11, offset, limit: 10 }; }); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load(); const detached = h.button('Next benchmark jobs'); await detached.emit('click'); assert.equal(h.calls.at(-1).url, '/api/v1/system/benchmarks?offset=10&limit=10'); const count = h.calls.length; await detached.emit('click'); assert.equal(h.calls.length, count);
});

test('verified projection limits prominent output to workload and timing, with metadata collapsed', async () => {
  const id = 'b'.repeat(32); const detail = { schema_version: 1, job: job(id), evidence: { state: 'verified', started_ns: '10', finished_ns: '20', source_to_binary: 'unknown', identity_stability: { source: 'same', tests: 'same', binaries: 'unknown', build: 'different' }, measurement: { bars: 100, strategy: 'hold', workload_label: 'synthetic no-op', load_seconds: 1, replay_seconds: 2, bars_per_second: 50, fills: 0, final_equity: 123, machine: { system: 'x', machine: 'y', python: 'z', cpu_count: 4 } } } }; const h = harness(url => url.includes(id) ? detail : list([job(id)])); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load(); await h.button(id).emit('click'); assert.ok(h.nodes().some(node => node.textContent.includes('Synthetic single-sample timing, not strategy performance'))); assert.ok(h.nodes().some(node => node.textContent.includes('no-op workload makes no trades; it is not buy-and-hold'))); const details = h.nodes().filter(node => node.tagName === 'DETAILS'); assert.equal(details.length, 2); assert.equal(details.at(-1).open, false); assert.equal(h.nodes().some(node => node.textContent === 'Fills'), true);
});

test('stale selection and cancellation completion cannot replace a new navigation', async () => {
  const old = 'c'.repeat(32), newer = 'd'.repeat(32), pending = deferred(); const h = harness((url, options) => { if (url.endsWith('/cancel')) return pending.promise; if (url.includes(old)) return unavailable(job(old, 'running')); if (url.includes(newer)) return unavailable(job(newer)); return list([job(old, 'running'), job(newer)]); }); const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load(); await h.button(old).emit('click'); const cancelling = h.button('Cancel benchmark job').emit('click'); await flush(); h.ui.beginView(); h.ui.replaceView(new Node('h1', { text: 'Elsewhere' })); pending.resolve({ job: job(old, 'cancelled') }); await cancelling; assert.equal(h.view.childNodes[0].textContent, 'Elsewhere');
});

test('detached run buttons cannot repeat a completed launch and edited workload stays visible', async () => {
  const id = 'e'.repeat(32), pending = deferred();
  const h = harness((url, options) => options ? pending.promise : url.includes(id) ? unavailable(job(id)) : list());
  const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load();
  h.input().value = '37'; h.select().value = 'sma';
  const detached = h.button('Run benchmark');
  const submitting = detached.emit('click'); await flush();
  assert.equal(h.input().value, '37'); assert.equal(h.select().value, 'sma');
  await detached.emit('click');
  assert.equal(h.calls.filter(call => call.options).length, 1);
  pending.resolve({ job: job(id) }); await submitting;
  await detached.emit('click');
  assert.equal(h.calls.filter(call => call.options).length, 1);
  assert.equal(h.input().value, '37'); assert.equal(h.select().value, 'sma');
});

test('cancel completion after A to B to A restores controls without stale content', async () => {
  const a = 'a'.repeat(32), b = 'b'.repeat(32), pending = deferred();
  const h = harness((url, options) => options ? pending.promise :
    url.includes(a) ? unavailable(job(a, 'running')) : url.includes(b) ? unavailable(job(b)) :
    list([job(a), job(b)]));
  const view = createSystemBenchmarks(h.ui); h.setDispose(view.dispose); await view.load();
  await h.button(a).emit('click');
  const oldCancel = h.button('Cancel benchmark job');
  const canceling = oldCancel.emit('click'); await flush();
  await h.button(b).emit('click'); await h.button(a).emit('click');
  assert.equal(h.button('Cancel benchmark job').disabled, true);
  pending.resolve({ job: job(a, 'canceled') }); await canceling;
  assert.equal(h.button('Cancel benchmark job').disabled, false);
  const before = h.calls.length; await oldCancel.emit('click');
  assert.equal(h.calls.length, before);
  assert.ok(h.nodes().some(node => node.textContent === 'State: running.'));
});
