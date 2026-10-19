// Dependency-free comparison-form state-machine tests. This DOM double does
// not claim browser layout or native constraint-validation coverage.
import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const metadata = JSON.parse(execFileSync(resolve(root, '.venv/bin/python'), ['-c',
  'import json; from qte.gui.comparison_forms import comparison_form_options; print(json.dumps(comparison_form_options()))',
], { cwd: root, encoding: 'utf8' }));
const source = await readFile(resolve(root, 'python/qte/gui/static/comparison_forms.js'));
const { createComparisonForms } = await import('data:text/javascript;base64,' + source.toString('base64'));

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
  addEventListener(type, fn) {
    const handlers = this.listeners.get(type) || [];
    handlers.push(fn); this.listeners.set(type, handlers);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(handler => handler !== fn));
  }
  focus() { focused = this; }
  async emit(type) {
    const event = { currentTarget: this, target: this, preventDefault() {} };
    for (const handler of this.listeners.get(type) || []) await handler(event);
    if (['input', 'change'].includes(type) && this.parentElement) await this.parentElement.emit(type);
  }
}
function walk(node) { return [node, ...node.childNodes.flatMap(walk)]; }
function available(node) { for (let current = node; current; current = current.parentElement) if (current.disabled || current.hidden) return false; return true; }
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; let reject; const promise = new Promise((ok, no) => { resolve = ok; reject = no; }); return { promise, resolve, reject }; }

function harness(api) {
  globalThis.document = new Node('document');
  Object.defineProperty(globalThis.document, 'activeElement', { get: () => focused });
  globalThis.document.createTextNode = text => new Node('#text', { text });
  let view = new Node('main'); let generation = 0;
  const calls = []; const navigations = []; const statuses = [];
  const ui = {
    element: (tag, attrs, children) => new Node(tag, attrs, children),
    textLink: (href, text) => new Node('a', { href, text }),
    api: async (url, options) => { calls.push({ url, options }); return api(url, options); },
    beginView: () => ++generation, isCurrentView: value => value === generation,
    replaceView: (...nodes) => { view.replaceChildren(...nodes); },
    setStatus: text => statuses.push(text), setNavigation() {}, navigate: href => navigations.push(href),
  };
  return {
    ui, calls, navigations, statuses, get view() { return view; }, nodes: () => walk(view),
    field: name => {
      const node = walk(view).find(candidate => candidate.name === name && available(candidate));
      assert.ok(node, `missing available field ${name}`); return node;
    },
    button: text => {
      const node = walk(view).find(candidate => candidate.tagName === 'BUTTON' && candidate.textContent === text && !candidate.hidden);
      assert.ok(node, `missing button ${text}`); return node;
    },
    buttons: text => walk(view).filter(candidate => candidate.tagName === 'BUTTON' && candidate.textContent === text && !candidate.hidden),
  };
}

const previewResult = {
  schema_version: 1, kind: 'comparison_run', preview_fingerprint: 'a'.repeat(64),
  holdout_history: 'unknown', assumptions: ['Validation checks consistency.'],
};
const validationIds = ['1'.repeat(32), '2'.repeat(32), '3'.repeat(32), '4'.repeat(32)];
const sma = metadata.strategies.find(strategy => strategy.type === 'moving_average_regime');
const historical = metadata.workflows.find(workflow => workflow.type === 'historical_experiment');

function requestBodies(h, suffix) {
  return h.calls.filter(call => call.url.endsWith(suffix)).map(call => JSON.parse(call.options.body));
}
function nestedDefaults(fields) {
  const result = {};
  for (const [path, field] of Object.entries(fields)) {
    const parts = path.split('.'); let current = result;
    for (const part of parts.slice(0, -1)) current = current[part] ||= {};
    current[parts.at(-1)] = field.default;
  }
  return result;
}
function onlyControlPlaneCalls(h) {
  assert.ok(h.calls.every(call => /\/api\/v1\/(comparisons\/(form-options|preview|jobs)|artifacts\?)/.test(call.url)),
    h.calls.map(call => call.url).join(', '));
  assert.equal(h.calls.some(call => /datasets\/|\/files|reveal|download|raw/i.test(call.url)), false);
}
function fillWindow(h, index, id = validationIds[index]) {
  const prefix = `configuration.windows[${index}]`;
  h.field(`${prefix}.name`).value = ['train', 'validation', 'test', 'extra'][index];
  h.field(`${prefix}.regime`).value = `fixture_${index + 1}`;
  h.field(`${prefix}.validation_id`).value = id;
  h.field(`${prefix}.evidence.kind`).value = 'synthetic';
  h.field(`${prefix}.evidence.source`).value = 'invented_fixture';
  h.field(`${prefix}.evidence.corporate_action_policy`).value = 'fabricated_action_free';
}
function fillSma(h, index = 0) {
  const prefix = `configuration.candidates[${index}]`;
  h.field(`${prefix}.name`).value = `sma_${index + 1}`;
  h.field(`${prefix}.symbol`).value = 'SPY';
  h.field(`${prefix}.parameters.fast_period`).value = '2';
  h.field(`${prefix}.parameters.slow_period`).value = '3';
  h.field(`${prefix}.parameters.quantity`).value = '9223372036854775807';
}
async function submitPreview(h) { await h.nodes().find(node => node.tagName === 'FORM').emit('submit'); await flush(); }
async function fillSmaWorkflow(h) {
  fillWindow(h, 0); fillWindow(h, 1); fillSma(h);
}
async function switchHistorical(h) {
  const workflow = h.field('configuration.workflow');
  workflow.value = 'historical_experiment'; await workflow.emit('change');
  fillWindow(h, 0); fillWindow(h, 1); fillWindow(h, 2); fillSma(h);
}

afterEach(() => { delete globalThis.document; focused = null; });

test('comparison form loading requests only metadata and never previews, executes, or reveals', async () => {
  const h = harness(url => {
    assert.equal(url, '/api/v1/comparisons/form-options');
    return metadata;
  });
  await createComparisonForms(h.ui).load();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/v1/comparisons/form-options']);
  assert.ok(h.nodes().some(node => node.textContent === 'New comparison'));
  onlyControlPlaneCalls(h);
});

test('manual validation IDs produce the fixed SMA preview and launch envelope, then navigate to existing jobs', async () => {
  const jobId = 'f'.repeat(32);
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/preview')) return previewResult;
    if (url.endsWith('/jobs')) return { job: { id: jobId } };
    throw new Error(`unexpected request ${url}`);
  });
  await createComparisonForms(h.ui).load(); await fillSmaWorkflow(h); await submitPreview(h);
  const [preview] = requestBodies(h, '/preview');
  assert.deepEqual(preview, {
    schema_version: 1, kind: 'comparison_run', configuration: {
      schema_version: 1, workflow: 'moving_average_comparison',
      windows: [0, 1].map(index => ({ name: ['train', 'validation'][index], role: ['train', 'test'][index], regime: `fixture_${index + 1}`,
        validation_id: validationIds[index], evidence: { kind: 'synthetic', source: 'invented_fixture', corporate_action_policy: 'fabricated_action_free' } })),
      candidates: [{ name: 'sma_1', type: 'moving_average_regime', symbol: 'SPY', eligible: true,
        parameters: { fast_period: '2', slow_period: '3', quantity: '9223372036854775807' } }],
      experiment: nestedDefaults(metadata.workflows[0].experiment_fields),
    },
  });
  await h.button('Launch fixed comparison').emit('click');
  const [launch] = requestBodies(h, '/jobs');
  assert.equal(launch.timeout_seconds, 1800);
  assert.equal(launch.preview_fingerprint, previewResult.preview_fingerprint);
  assert.match(launch.idempotency_key, /^qte_[0-9a-f]{32}$/);
  assert.deepEqual(launch.configuration, preview.configuration);
  assert.deepEqual(h.navigations, [`/research/jobs/${jobId}`]);
  onlyControlPlaneCalls(h);
});

test('historical experiment compiles metadata-driven candidate and dotted experiment fields with exact wire integers', async () => {
  const h = harness(url => url.endsWith('form-options') ? metadata : previewResult);
  await createComparisonForms(h.ui).load(); await switchHistorical(h);
  h.field('configuration.experiment.random_seed').value = '18446744073709551615';
  await h.button('Add candidate').emit('click');
  const type = h.field('configuration.candidates[1].type'); type.value = 'trend'; await type.emit('change');
  h.field('configuration.candidates[1].name').value = 'trend_1';
  h.field('configuration.candidates[1].symbol').value = 'QQQ';
  await submitPreview(h);
  const [preview] = requestBodies(h, '/preview');
  const configuration = preview.configuration;
  assert.equal(configuration.workflow, 'historical_experiment');
  assert.deepEqual(configuration.windows.map(window => window.role), ['train', 'validation', 'test']);
  assert.equal(configuration.candidates[0].parameters.quantity, '9223372036854775807');
  assert.equal(configuration.experiment.random_seed, '18446744073709551615');
  assert.equal(typeof configuration.experiment.random_seed, 'string');
  assert.deepEqual(Object.keys(configuration.experiment).sort(), Object.keys(historical.experiment_fields).map(path => path.split('.')[0]).filter((name, index, all) => all.indexOf(name) === index).sort());
  const trend = metadata.strategies.find(strategy => strategy.type === 'trend');
  assert.deepEqual(configuration.candidates[1].parameters, Object.fromEntries(Object.entries(trend.fields).map(([name, field]) => [name, field.default])));
  onlyControlPlaneCalls(h);
});

test('catalog chooser pages only registered validation summaries and never fetches holdout content', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    if (url === '/api/v1/artifacts?offset=0&limit=25') return {
      items: [{ id: 'a'.repeat(32), name: 'ordinary research', kind: 'research_run_v1', integrity: 'verified' }], offset: 0, limit: 25, total: 26,
    };
    if (url === '/api/v1/artifacts?offset=25&limit=25') return {
      items: [{ id: validationIds[0], name: 'verified validation', kind: 'dataset_validation_v1', status: { integrity: 'verified' } }], offset: 25, limit: 25, total: 26,
    };
    throw new Error(`unexpected request ${url}`);
  });
  await createComparisonForms(h.ui).load();
  await h.buttons('Choose validated dataset')[0].emit('click');
  assert.equal(h.nodes().some(node => node.textContent === 'Choose ordinary research'), false);
  await h.button('Next validations').emit('click');
  await h.button('Choose verified validation').emit('click');
  assert.equal(h.field('configuration.windows[0].validation_id').value, validationIds[0]);
  assert.deepEqual(h.calls.map(call => call.url), [
    '/api/v1/comparisons/form-options', '/api/v1/artifacts?offset=0&limit=25', '/api/v1/artifacts?offset=25&limit=25',
  ]);
  onlyControlPlaneCalls(h);
});

test('detached chooser handlers cannot start a catalog request after navigation', async () => {
  const h = harness(url => {
    if (url.endsWith('form-options')) return metadata;
    throw new Error(`detached form requested ${url}`);
  });
  await createComparisonForms(h.ui).load();
  const choose = h.buttons('Choose validated dataset')[0];
  h.ui.beginView(); h.ui.replaceView(new Node('h1', { text: 'New destination' }));
  await choose.emit('click');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/v1/comparisons/form-options']);
  assert.equal(h.view.childNodes[0].textContent, 'New destination');
});

test('edited or navigated-away previews stay stale and cannot enable launch', async () => {
  const editPreview = deferred();
  const edited = harness(url => url.endsWith('form-options') ? metadata : editPreview.promise);
  await createComparisonForms(edited.ui).load(); await fillSmaWorkflow(edited); await submitPreview(edited);
  const quantity = edited.field('configuration.candidates[0].parameters.quantity');
  quantity.value = '15'; await quantity.emit('input');
  editPreview.resolve(previewResult); await flush();
  assert.equal(edited.button('Launch fixed comparison').disabled, true);
  assert.equal(requestBodies(edited, '/jobs').length, 0);

  const navigationPreview = deferred();
  const navigated = harness(url => url.endsWith('form-options') ? metadata : navigationPreview.promise);
  await createComparisonForms(navigated.ui).load(); await fillSmaWorkflow(navigated); await submitPreview(navigated);
  navigated.ui.beginView(); navigated.ui.replaceView(new Node('h1', { text: 'New destination' }));
  navigationPreview.resolve(previewResult); await flush();
  assert.equal(navigated.view.childNodes[0].textContent, 'New destination');
  assert.equal(requestBodies(navigated, '/jobs').length, 0);
});

test('double launch submits once and uncertain retry reuses the same frozen request and key', async () => {
  const pending = deferred(); let submissions = 0;
  const h = harness((url) => {
    if (url.endsWith('form-options')) return metadata;
    if (url.endsWith('/preview')) return previewResult;
    if (url.endsWith('/jobs')) { submissions += 1; return submissions === 1 ? pending.promise : { job: { id: 'b'.repeat(32) } }; }
    throw new Error(url);
  });
  await createComparisonForms(h.ui).load(); await fillSmaWorkflow(h); await submitPreview(h);
  const launch = h.button('Launch fixed comparison');
  const first = launch.emit('click'); const duplicate = launch.emit('click'); await flush();
  assert.equal(submissions, 1);
  pending.reject(Object.assign(new Error('temporary failure'), { status: 503 }));
  await Promise.all([first, duplicate]);
  await h.button('Retry same request').emit('click');
  const sent = requestBodies(h, '/jobs');
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(sent[0].configuration.candidates[0].parameters.quantity, '9223372036854775807');
  assert.deepEqual(h.navigations, ['/research/jobs/' + 'b'.repeat(32)]);
});

test('invalid collapsed advanced fields open and focus without issuing a preview', async () => {
  const h = harness(url => {
    assert.ok(url.endsWith('form-options'), 'invalid draft must not issue a request');
    return metadata;
  });
  await createComparisonForms(h.ui).load(); await switchHistorical(h);
  const form = h.nodes().find(node => node.tagName === 'FORM');
  assert.equal(form.getAttribute('novalidate'), '');
  const seed = h.field('configuration.experiment.random_seed');
  seed.value = '';
  await submitPreview(h);
  assert.equal(focused, seed);
  assert.equal(seed.getAttribute('aria-invalid'), 'true');
  let parent = seed.parentElement;
  while (parent && parent.tagName !== 'DETAILS') parent = parent.parentElement;
  assert.equal(parent.open, true);
  seed.value = '42';
  const regimes = h.field('configuration.experiment.required_regimes');
  regimes.value = '';
  await submitPreview(h);
  assert.equal(focused, regimes);
  assert.equal(requestBodies(h, '/preview').length, 0);
});

test('base and stressed costs are labeled separately and empty SMA advanced controls are absent', async () => {
  const h = harness(() => metadata);
  await createComparisonForms(h.ui).load();
  assert.equal(h.nodes().some(node => node.textContent === 'Advanced parameter defaults'), false);
  await switchHistorical(h);
  for (const [path, title] of [['base_costs', 'Base costs'], ['stressed_costs', 'Stressed costs']]) {
    const group = h.nodes().find(node => node.tagName === 'FIELDSET'
      && node.getAttribute('data-field-path') === `configuration.experiment.${path}`);
    assert.ok(group);
    assert.ok(group.childNodes.some(node => node.tagName === 'LEGEND' && node.textContent === title));
    for (const field of ['commission_bps', 'spread_bps', 'slippage_bps']) {
      assert.ok(walk(group).includes(h.field(`configuration.experiment.${path}.${field}`)));
    }
  }
});
