// Dependency-free module state-machine tests. This small DOM double is NOT a
// browser and deliberately does not claim native layout/validation coverage.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const metadata = JSON.parse(execFileSync(resolve(root, '.venv/bin/python'), ['-c',
  'import json; from qte.gui.research_forms import form_options; from qte.gui.dto import artifact_value; print(json.dumps(artifact_value(form_options())))'
], { cwd: root, encoding: 'utf8' }));
// Match the route's control-plane version restoration after exact-value DTOs.
metadata.schema_version = 1;
metadata.config_origin.form_schema_version = 1;
const loadModule = async name => import('data:text/javascript;base64,' + Buffer.from(
  await readFile(resolve(root, 'python/qte/gui/static', name))).toString('base64'));
const { createResearchForms } = await loadModule('research_forms.js');
const { createResearchJobs } = await loadModule('research_jobs.js');
let focused = null;
class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = [];
    this.listeners = new Map(); this.disabled = false; this.hidden = false;
    this.value = ''; this.checked = false; this.textContent = ''; this.dataset = {};
    this.classList = { add: name => { this.className = `${this.className || ''} ${name}`; } };
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'text') this.textContent = value;
      else this.setAttribute(key, value);
    }
    this.append(...children);
  }
  setAttribute(key, value) {
    this.attrs[key] = String(value);
    if (key === 'class') this.className = String(value);
    if (['name', 'type', 'value', 'id'].includes(key)) this[key] = String(value);
  }
  getAttribute(key) { return this.attrs[key] ?? null; }
  removeAttribute(key) { delete this.attrs[key]; }
  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this; this.childNodes.push(node);
      if (this.tagName === 'SELECT' && this.childNodes.length === 1) this.value = node.value;
    }
  }
  prepend(node) { node.parentElement = this; this.childNodes.unshift(node); }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  addEventListener(type, fn) { const list = this.listeners.get(type) || []; list.push(fn); this.listeners.set(type, list); }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x !== fn)); }
  focus() { focused = this; }
  matches(selector) {
    if (selector.startsWith('[data-job-action=')) return this.dataset.jobAction === selector.split('"')[1];
    if (selector.startsWith('.')) return (this.className || '').split(' ').includes(selector.slice(1));
    return this.tagName.toLowerCase() === selector;
  }
  closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; }
  querySelector(selector) { return walk(this).find(node => node !== this && node.matches(selector)) || null; }
  querySelectorAll(selector) { return walk(this).filter(node => node !== this && node.matches(selector)); }
  async emit(type) {
    const event = { currentTarget: this, target: this, preventDefault() {} };
    for (const fn of this.listeners.get(type) || []) await fn(event);
    if (['input', 'change'].includes(type) && this.parentElement) await this.parentElement.emit(type);
  }
}
function walk(node) { return [node, ...node.childNodes.flatMap(walk)]; }
function available(node) { for (let n = node; n; n = n.parentElement) if (n.disabled || n.hidden) return false; return true; }
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; }
function harness(api) {
  globalThis.document = new Node('document');
  Object.defineProperty(globalThis.document, 'activeElement', { get: () => focused });
  globalThis.document.createTextNode = text => new Node('#text', { text });
  let view = new Node('main'); let generation = 0; let dispose = () => {};
  const calls = []; const navigations = []; const statuses = [];
  const ui = {
    element: (tag, attrs, children) => new Node(tag, attrs, children),
    textLink: (href, text) => new Node('a', { href, text }),
    api: async (url, options) => { calls.push({ url, options }); return api(url, options); },
    beginView: () => { dispose(); return ++generation; }, isCurrentView: value => value === generation,
    replaceView: (...nodes) => { view.replaceChildren(...nodes); },
    setStatus: text => statuses.push(text), setNavigation() {}, navigate: href => navigations.push(href),
  };
  return {
    ui, calls, navigations, statuses, get view() { return view; },
    setDispose: fn => { dispose = fn; },
    nodes: () => walk(view),
    field: name => { const node = walk(view).find(n => n.name === name && available(n)); assert.ok(node, name); return node; },
    button: text => { const node = walk(view).find(n => n.tagName === 'BUTTON' && n.textContent === text && !n.hidden); assert.ok(node, text); return node; },
  };
}
const previewResult = { preview_fingerprint: 'a'.repeat(64), assumptions: ['Synthetic mechanics only'], effective_configuration: { engine: { random_seed: '0' } } };
async function runForm(h) {
  await createResearchForms(h.ui).loadRun();
  for (const [name, value] of Object.entries({
    'data.path': 'data/bars.csv', 'data.interval_ns': '3600000000000', 'strategy.symbol': 'SPY',
    'strategy.fast_period': '2', 'strategy.slow_period': '3', 'strategy.quantity': '9223372036854775807',
    'engine.initial_cash': '100000',
  })) h.field('configuration.' + name).value = value;
  h.field('configuration.data.action_free').checked = true;
}
async function preview(h) { await h.nodes().find(n => n.tagName === 'FORM').emit('submit'); await flush(); }
afterEach(() => { delete globalThis.document; focused = null; });

test('download form stays offline on load and blocks launch without server permission', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/prerequisites')) return { enabled: false, credentials_present: true };
    if (url.endsWith('/validate')) return previewResult;
    assert.fail('unexpected request: ' + url);
  });
  await createResearchForms(h.ui).loadDownload();
  assert.equal(h.calls.length, 2);
  for (const [name, value] of Object.entries({ symbols: 'SPY, QQQ', timeframe: '1Hour',
    start: '2024-01-02T14:00:00Z', end: '2024-01-02T17:00:00Z', feed: 'iex' })) {
    h.field('configuration.' + name).value = value;
  }
  h.field('configuration.action_free').checked = true;
  await preview(h);
  const body = JSON.parse(h.calls.find(c => c.url.endsWith('/validate')).options.body);
  assert.equal(body.kind, 'download_alpaca');
  assert.deepEqual(body.configuration.symbols, ['SPY', 'QQQ']);
  assert.equal(h.button('Download historical data').disabled, true);
  await h.button('Download historical data').emit('click');
  assert.equal(h.calls.length, 3);
  assert.deepEqual(h.navigations, []);
});

test('download requires an explicit feed and action declaration before preview then explicit launch', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/prerequisites')) return { enabled: true, credentials_present: true };
    if (url.endsWith('/validate')) return previewResult;
    if (url.endsWith('/jobs')) return { job: { id: 'e'.repeat(32) } };
    assert.fail('unexpected request: ' + url);
  });
  await createResearchForms(h.ui).loadDownload();
  for (const [name, value] of Object.entries({ symbols: 'SPY', timeframe: '1Day',
    start: '2024-01-02T00:00:00Z', end: '2024-01-03T00:00:00Z' })) {
    h.field('configuration.' + name).value = value;
  }
  await preview(h);
  assert.equal(h.calls.length, 2);
  h.field('configuration.feed').value = 'sip';
  await preview(h);
  assert.equal(h.calls.length, 2);
  h.field('configuration.action_free').checked = true;
  await preview(h);
  assert.equal(h.calls.length, 3);
  await h.button('Download historical data').emit('click');
  assert.equal(h.calls.length, 4);
  const sent = JSON.parse(h.calls.at(-1).options.body);
  assert.equal(sent.kind, 'download_alpaca');
  assert.equal(sent.configuration.feed, 'sip');
  assert.deepEqual(h.navigations, ['/research/jobs/' + 'e'.repeat(32)]);
});

test('dataset form submits only data and launches the fixed validation kind', async () => {
  const h = harness((url) => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/validate')) return previewResult;
    if (url.endsWith('/jobs')) return { job: { id: 'd'.repeat(32) } };
    throw new Error(url);
  });
  await createResearchForms(h.ui).loadDataset();
  h.field('configuration.data.path').value = 'data/bars.csv';
  h.field('configuration.data.interval_ns').value = '3600000000000';
  h.field('configuration.data.action_free').checked = true;
  assert.equal(h.calls.length, 1); // Opening the form does not preview or launch.
  await preview(h);
  await h.button('Launch fixed job').emit('click');
  const sent = JSON.parse(h.calls.find(call => call.url.endsWith('/jobs')).options.body);
  assert.equal(sent.kind, 'dataset_validate');
  assert.deepEqual(Object.keys(sent.configuration), ['data']);
  assert.equal(sent.configuration.data.interval_ns, '3600000000000');
  assert.deepEqual(h.navigations, ['/research/jobs/' + 'd'.repeat(32)]);
});

test('selected dataset preserves mapping and checksum through run preview and launch', async () => {
  const data = { adapter: 'csv', path: 'data/bars.csv', interval_ns: '3600000000000',
    schema: { symbol: 'ticker' }, action_free: true, expected_sha256: 'f'.repeat(64) };
  const h = harness((url) => {
    if (url.endsWith('form-options')) return metadata;
    if (url.includes('/datasets/')) return { dataset: { source: { state: 'available', data } } };
    if (url.endsWith('/validate')) return previewResult;
    if (url.endsWith('/jobs')) return { job: { id: 'e'.repeat(32) } };
    throw new Error(url);
  });
  await createResearchForms(h.ui).loadRun('a'.repeat(32));
  for (const [name, value] of Object.entries({ 'strategy.symbol': 'SPY', 'strategy.fast_period': '2',
    'strategy.slow_period': '3', 'strategy.quantity': '1', 'engine.initial_cash': '100000' })) {
    h.field('configuration.' + name).value = value;
  }
  const locked = h.nodes().find(node => node.name === 'configuration.data.path');
  assert.equal(available(locked), false);
  await preview(h);
  await h.button('Launch fixed job').emit('click');
  const calls = h.calls.filter(call => call.url.endsWith('/validate') || call.url.endsWith('/jobs'));
  assert.equal(calls.length, 2);
  for (const call of calls) assert.deepEqual(JSON.parse(call.options.body).configuration.data, data);
});

test('unavailable selected dataset does not silently fall back to an unpinned run', async () => {
  const h = harness(url => url.endsWith('form-options') ? metadata : { dataset: { source: { state: 'unavailable' } } });
  await createResearchForms(h.ui).loadRun('a'.repeat(32));
  assert.equal(h.nodes().some(node => node.tagName === 'FORM'), false);
  assert.ok(h.nodes().some(node => node.textContent.includes('source is unavailable')));
  assert.equal(h.calls.length, 2);
});

test('form submits exact original integer wire text; double launch and uncertain retry share one key', async () => {
  let submits = 0;
  const pending = deferred();
  const h = harness((url, options) => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/validate')) return previewResult;
    if (url.endsWith('/jobs')) { submits++; return submits === 1 ? pending.promise : { job: { id: 'b'.repeat(32) } }; }
    throw new Error(url);
  });
  await runForm(h); await preview(h);
  const launch = h.button('Launch fixed job');
  assert.equal(launch.disabled, false);
  const first = launch.emit('click'); const duplicate = launch.emit('click'); await flush();
  assert.equal(submits, 1);
  pending.reject(Object.assign(new Error('temporary failure'), { status: 503 }));
  await Promise.all([first, duplicate]);
  await h.button('Retry same request').emit('click');
  const sent = h.calls.filter(x => x.url.endsWith('/jobs')).map(x => JSON.parse(x.options.body));
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(sent[0].configuration.strategy.quantity, '9223372036854775807');
  assert.equal(sent[0].configuration.strategy.fast_period, '2');
  assert.equal(typeof sent[0].configuration.engine.initial_cash, 'number');
  assert.equal(sent[0].timeout_seconds, 1800);
  assert.deepEqual(h.navigations, ['/research/jobs/' + 'b'.repeat(32)]);
});

test('editing during preview discards its response and never enables launch', async () => {
  const pending = deferred();
  const h = harness(url => url.endsWith('form-options') ? metadata : pending.promise);
  await runForm(h); await preview(h);
  const input = h.field('configuration.strategy.quantity'); input.value = '15'; await input.emit('input');
  pending.resolve(previewResult); await flush();
  assert.equal(h.button('Launch fixed job').disabled, true);
  assert.equal(h.calls.filter(x => x.url.endsWith('/jobs')).length, 0);
});

test('router profile stays exact raw text through preview and explicit launch', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/validate')) return previewResult;
    if (url.endsWith('/jobs')) return { job: { id: 'c'.repeat(32) } };
    assert.fail(url);
  });
  await runForm(h);
  const type = h.field('configuration.strategy.type');
  type.value = 'macro_router'; await type.emit('change');
  const profile = h.field('configuration.strategy.profile');
  assert.equal(profile.tagName, 'TEXTAREA');
  const original = '{"evidence":"synthetic","candidates":[{"kind":"trend","fast_period":"2"}],"macro_observations":[{"reference_ns":"9007199254740993","available_ns":"9007199254740995"}]}';
  profile.value = original;
  await preview(h);
  assert.equal(h.calls.filter(call => call.url.endsWith('/jobs')).length, 0);
  await h.button('Launch fixed job').emit('click');
  for (const call of h.calls.filter(call => call.url.endsWith('/validate') || call.url.endsWith('/jobs'))) {
    const strategy = JSON.parse(call.options.body).configuration.strategy;
    assert.deepEqual(strategy, { type: 'macro_router', symbol: 'SPY', profile: original });
  }
  assert.deepEqual(h.navigations, ['/research/jobs/' + 'c'.repeat(32)]);
});

test('router profile edits invalidate preview and duplicate keys reach strict server validation intact', async () => {
  let previews = 0;
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/validate') && ++previews === 1) return previewResult;
    if (url.endsWith('/validate')) throw Object.assign(new Error('Invalid router profile'),
      { status: 400, field: 'configuration.strategy.profile' });
    assert.fail(url);
  });
  await runForm(h);
  const type = h.field('configuration.strategy.type');
  type.value = 'macro_router'; await type.emit('change');
  await preview(h);
  assert.equal(h.button('Launch fixed job').disabled, false);
  const profile = h.field('configuration.strategy.profile');
  profile.value = '{"evidence":"synthetic","evidence":"user_declared"}';
  await profile.emit('input');
  assert.equal(h.button('Launch fixed job').disabled, true);
  await preview(h);
  assert.equal(JSON.parse(h.calls.at(-1).options.body).configuration.strategy.profile, profile.value);
  assert.ok(h.nodes().some(node => node.textContent.includes('Invalid router profile')));
  assert.equal(h.calls.filter(call => call.url.endsWith('/jobs')).length, 0);
});

test('router route is allowed and table details preserve exact nested provenance with stale handlers inert', async () => {
  const script = await readFile(resolve(root, 'python/qte/gui/static/app.js'), 'utf8');
  const allowed = Function(script.match(/const runViews = new Set\([^\n]+/)[0] + '\nreturn runViews;')();
  assert.ok(allowed.has('route_events'));
  const source = script.slice(script.indexOf('function runTable('), script.indexOf('\nfunction runSeries('));
  let current = true; let view = new Node('main'); const loads = [];
  const runTable = Function('element', 'displayValue', 'humanize', 'activeView', 'isCurrentView', 'runHeader', 'replaceView', 'loadRun', source + '\nreturn runTable;')(
    (tag, attrs, children) => new Node(tag, attrs, children), (value, fallback) => value == null ? fallback : String(value),
    value => value, 1, () => current, () => [], (...nodes) => view.replaceChildren(...nodes), (...args) => loads.push(args));
  const payload = { columns: ['timestamp_ns', 'target_family', 'active_family', 'phase', 'macro_inputs'], total: 3, limit: 1,
    rows: [{ timestamp_ns: '9007199254740993', target_family: 'trend', active_family: 'trend', phase: 'active',
      macro_inputs: [{ available_ns: '9007199254740991', source_id: '<script>not executable</script>' }] }] };
  runTable('id', {}, payload, 'route_events', 'route_events', 0);
  const open = walk(view).find(node => node.className === 'record-open');
  const next = walk(view).find(node => node.textContent === 'Next');
  assert.equal(open.textContent, '9007199254740993');
  await open.emit('click');
  const nested = walk(view).find(node => node.tagName === 'PRE');
  assert.deepEqual(JSON.parse(nested.textContent), payload.rows[0].macro_inputs);
  assert.equal(nested.childNodes.length, 0);
  const back = walk(view).find(node => node.textContent === 'Back to records');
  current = false; view.replaceChildren(new Node('p', { text: 'new page' }));
  await back.emit('click'); await next.emit('click'); await open.emit('click');
  assert.equal(view.childNodes[0].textContent, 'new page');
  assert.deepEqual(loads, []);
});

test('root validation error is bounded and does not strand the form', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    throw Object.assign(new Error('Invalid configuration'), { status: 400, field: 'configuration' });
  });
  await runForm(h); await preview(h);
  assert.ok(h.nodes().some(n => n.textContent === 'configuration: Invalid configuration'));
  assert.equal(h.button('Preview assumptions').disabled, false);
});

test('disabled optional sampling cannot block native required-field validation', async () => {
  const h = harness(() => metadata); await runForm(h);
  for (const name of ['max_staleness_ns', 'periods_per_year', 'interval_ns']) {
    const field = h.nodes().find(n => n.name === 'configuration.sampling.' + name);
    assert.ok(field);
    assert.equal(available(field), false);
    let disabled = false; for (let n = field; n; n = n.parentElement) disabled ||= n.disabled;
    assert.equal(disabled, true, `${name} must be disabled, not just hidden`);
  }
});

test('job envelope renders, completion wins cancellation, registration remains explicit', async () => {
  const id = 'a'.repeat(32); let state = 'running';
  const h = harness((url, options) => {
    if (url.endsWith('/cancel')) { assert.equal(options.method, 'POST'); assert.deepEqual(JSON.parse(options.body), { schema_version: 1 }); state = 'succeeded'; return { job: { id, state, output_path: 'build/out' } }; }
    if (url.endsWith('/register')) return { artifact: { id: 'artifact-id' } };
    if (url.includes('/log?')) return { text: 'sanitized phase' };
    return { job: { id, state, output_path: 'build/out', created_ns: '9223372036854775807' } };
  });
  const jobs = createResearchJobs(h.ui); h.setDispose(jobs.dispose);
  try {
    await jobs.load(id);
    assert.ok(h.nodes().some(n => n.textContent === 'State: running.'));
    assert.equal(h.calls.filter(x => x.url.endsWith('/register')).length, 0);
    await h.button('Cancel job').emit('click'); await flush();
    assert.ok(h.nodes().some(n => n.textContent === 'State: succeeded.'));
    await h.button('Register output').emit('click');
    assert.deepEqual(h.navigations, ['/research/artifact-id']);
    assert.equal(h.calls.some(x => /reveal|files|experiments/.test(x.url)), false);
  } finally { jobs.dispose(); }
});

test('stale job response after navigation cannot replace the current view', async () => {
  const pending = deferred(); const h = harness(() => pending.promise);
  const jobs = createResearchJobs(h.ui); h.setDispose(jobs.dispose);
  const loading = jobs.load('a'.repeat(32)); h.ui.beginView();
  h.ui.replaceView(new Node('h1', { text: 'New destination' }));
  pending.resolve({ job: { id: 'a'.repeat(32), state: 'running' } }); await loading;
  assert.equal(h.view.childNodes[0].textContent, 'New destination');
  assert.equal(h.calls.length, 1); jobs.dispose();
});
