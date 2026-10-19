// Dependency-free state-machine tests, not browser/layout coverage.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../python/qte/gui/static/system_tests.js', import.meta.url));
const { createSystemTests } = await import('data:text/javascript;base64,' + source.toString('base64'));

let focused = null;
class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = [];
    this.listeners = new Map(); this.disabled = false; this.hidden = false;
    this.textContent = ''; this.dataset = {}; this.open = false;
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'text') this.textContent = String(value);
      else this.setAttribute(key, value);
    }
    this.append(...children);
  }
  setAttribute(key, value) {
    this.attrs[key] = String(value);
    if (key === 'class') this.className = String(value);
    if (['id', 'type', 'href'].includes(key)) this[key] = String(value);
  }
  getAttribute(key) { return this.attrs[key] ?? null; }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.childNodes.push(node); } }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || []; handlers.push(handler); this.listeners.set(type, handlers);
  }
  focus() { focused = this; }
  async emit(type) {
    const event = { currentTarget: this, target: this, preventDefault() {} };
    for (const handler of this.listeners.get(type) || []) await handler(event);
  }
}
const walk = node => [node, ...node.childNodes.flatMap(walk)];
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const job = (id, state = 'succeeded', runner = 'pytest') => ({
  id, kind: 'test_run', state, runner, created_ns: '100', start_ns: '110', end_ns: '120', timeout_seconds: 300,
});
const list = (items = []) => ({ schema_version: 1, items, total: items.length, offset: 0, limit: 10 });
const protectedDetail = item => ({ schema_version: 1, job: item, evidence: { state: 'protected' } });
const verifiedEvidence = {
  state: 'verified', runner: 'pytest', outcome: 'failed',
  counts: { tests: 4, passed: 1, failed: 1, errors: 0, skipped: 1, xfailed: 1 },
  identity_stability: { source: 'same', tests: 'same', binaries: 'unknown', build: 'same' },
  current_match: { source: 'unknown', tests: 'unknown', binaries: 'unknown', build: 'unknown' },
  source_to_binary: 'unknown', log_truncated: false,
  details_truncated: true,
  versions: { python: '<img src=x onerror=alert(1)>', pytest: '9.0' },
  termination: 'exit', exit_code: 1,
  cases: [{ ordinal: 0, name: '<script>alert(1)</script>', classname: 'suite.<bad>', outcome: 'failed', duration: 0.25,
    issues: [{ kind: 'failure', message: '<b>broken</b>', text: 'plain <img> text' }] }],
  total: 1, offset: 0, limit: 25, disclosure: 'revealed_outside_protocol',
};

function harness(reply) {
  globalThis.document = new Node('document');
  globalThis.document.createTextNode = text => new Node('#text', { text });
  Object.defineProperty(globalThis.document, 'activeElement', { get: () => focused });
  let view = new Node('main'); let generation = 0; let dispose = () => {};
  const calls = []; const statuses = []; const navigation = [];
  const ui = {
    element: (tag, attrs, children) => new Node(tag, attrs, children),
    textLink: (href, text) => new Node('a', { href, text }),
    api: async (url, options) => { calls.push({ url, options }); return reply(url, options); },
    beginView: () => { dispose(); return ++generation; },
    isCurrentView: request => request === generation,
    replaceView: (...nodes) => { view.replaceChildren(...nodes); },
    setStatus: text => statuses.push(text),
    setNavigation: section => navigation.push(section),
    humanize: item => String(item || 'unknown').replaceAll('_', ' '),
    displayValue: (item, fallback = 'Not recorded') => item === undefined || item === null || item === '' ? fallback : String(item),
  };
  return {
    ui, calls, statuses, navigation,
    setDispose: callback => { dispose = callback; },
    get view() { return view; },
    nodes: () => walk(view),
    button(text) {
      const node = walk(view).find(item => item.tagName === 'BUTTON' && item.textContent === text && !item.hidden);
      assert.ok(node, `button ${text}`); return node;
    },
  };
}

afterEach(() => { delete globalThis.document; focused = null; });

test('opening System is passive and performs only the bounded test-job listing GET', async () => {
  const h = harness((url, options) => {
    assert.equal(options, undefined);
    assert.equal(url, '/api/v1/system/tests?offset=0&limit=10');
    return list([]);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose);
  await system.load();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls.some(call => call.options?.method === 'POST'), false);
  assert.equal(h.button('Run Python tests').disabled, false);
  assert.equal(h.button('Run C++ tests').disabled, false);
  assert.deepEqual(h.navigation, ['system']);
  assert.ok(h.nodes().some(node => node.textContent.includes('never starts a test')));
});

test('test-job list pagination is explicit, bounded, and rejects detached page handlers', async () => {
  const jobs = Array.from({ length: 11 }, (_, index) => job(index.toString(16).padStart(32, '0')));
  const h = harness(url => {
    const offset = Number(new URL(url, 'http://local').searchParams.get('offset'));
    return { schema_version: 1, items: jobs.slice(offset, offset + 10), total: 11, offset, limit: 10 };
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  const detachedNext = h.button('Next test jobs');
  assert.equal(h.button('Previous test jobs').disabled, true);
  await detachedNext.emit('click');
  assert.equal(h.calls.at(-1).url, '/api/v1/system/tests?offset=10&limit=10');
  assert.equal(h.button('Next test jobs').disabled, true);
  assert.equal(h.button('Previous test jobs').disabled, false);
  const count = h.calls.length;
  await detachedNext.emit('click');
  assert.equal(h.calls.length, count);
  await h.button('Previous test jobs').emit('click');
  assert.equal(h.calls.at(-1).url, '/api/v1/system/tests?offset=0&limit=10');
});

test('both runners launch only on explicit clicks with fixed timeout and distinct UUID intents', async () => {
  const ids = ['a'.repeat(32), 'b'.repeat(32)]; let accepted = 0;
  const h = harness((url, options) => {
    if (!options && url.includes(ids[0])) return protectedDetail(job(ids[0], 'queued', 'pytest'));
    if (!options && url.includes(ids[1])) return protectedDetail(job(ids[1], 'queued', 'ctest'));
    if (!options) return list([]);
    if (url === '/api/v1/system/tests') return { job: job(ids[accepted++], 'queued', JSON.parse(options.body).runner) };
    assert.fail('unexpected request ' + url);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button('Run Python tests').emit('click');
  await h.button('Run C++ tests').emit('click');
  const sent = h.calls.filter(call => call.url === '/api/v1/system/tests' && call.options).map(call => JSON.parse(call.options.body));
  assert.deepEqual(sent.map(body => [body.schema_version, body.runner, body.timeout_seconds]), [[1, 'pytest', 300], [1, 'ctest', 300]]);
  assert.match(sent[0].idempotency_key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.notEqual(sent[0].idempotency_key, sent[1].idempotency_key);
});

test('double click is guarded and uncertain retry reuses the byte-identical frozen request', async () => {
  const pending = deferred(); let posts = 0;
  const id = 'c'.repeat(32);
  const h = harness((url, options) => {
    if (!options) {
      if (url.includes(id)) return protectedDetail(job(id));
      return list([]);
    }
    if (url === '/api/v1/system/tests') {
      posts += 1;
      return posts === 1 ? pending.promise : { job: job(id) };
    }
    assert.fail('unexpected request ' + url);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  const run = h.button('Run Python tests');
  const first = run.emit('click'); const duplicate = run.emit('click'); await flush();
  assert.equal(posts, 1);
  pending.reject(Object.assign(new Error('temporary failure'), { status: 503 }));
  await Promise.all([first, duplicate]);
  assert.equal(h.button('Run Python tests').disabled, true);
  assert.equal(h.button('Run C++ tests').disabled, true);
  await run.emit('click');
  assert.equal(posts, 1); // A detached pre-uncertainty launch cannot replace the retained key.
  await h.button('Retry same request').emit('click');
  const bodies = h.calls.filter(call => call.url === '/api/v1/system/tests' && call.options).map(call => call.options.body);
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], bodies[1]);
  assert.equal(h.button('Run Python tests').disabled, false);
  assert.ok(h.nodes().some(node => node.textContent.includes('protected')));
});

test('a definitive error retrying an uncertain request retains only that pending key', async () => {
  let posts = 0; const id = '9'.repeat(32);
  const h = harness((url, options) => {
    if (!options) return url.includes(id) ? protectedDetail(job(id)) : list([]);
    if (url === '/api/v1/system/tests') {
      posts += 1;
      if (posts === 1) throw Object.assign(new Error('gateway timeout'), { status: 503 });
      if (posts === 2) throw Object.assign(new Error('source changed'), { status: 409 });
      return { job: job(id) };
    }
    assert.fail('unexpected request ' + url);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button('Run Python tests').emit('click');
  const firstBody = h.calls.at(-1).options.body;
  await h.button('Retry same request').emit('click');
  assert.equal(h.button('Run Python tests').disabled, true);
  assert.ok(h.button('Retry same request'));
  await h.button('Retry same request').emit('click');
  const bodies = h.calls.filter(call => call.url === '/api/v1/system/tests' && call.options).map(call => call.options.body);
  assert.deepEqual(bodies, [firstBody, firstBody, firstBody]);
});

test('protected evidence is never fetched or disclosed until explicit reveal, then current check is explicit', async () => {
  const id = 'd'.repeat(32); let revealed = false;
  const h = harness((url, options) => {
    if (url === '/api/v1/system/tests?offset=0&limit=10') return list([job(id)]);
    if (url.endsWith('/reveal')) {
      assert.deepEqual(JSON.parse(options.body), { schema_version: 1, confirmation: 'reveal outside protocol' });
      revealed = true; return { evidence: { state: 'revealed' } };
    }
    if (url.endsWith('/check-current')) {
      assert.deepEqual(JSON.parse(options.body), { schema_version: 1 });
      return { current_match: { source: 'different', tests: 'same', binaries: 'unknown', build: 'unknown' },
        versions_match: 'different', checked_ns: '999' };
    }
    if (url.includes(id)) return revealed ? { schema_version: 1, job: job(id), evidence: verifiedEvidence } : protectedDetail(job(id));
    assert.fail('unexpected request ' + url);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button(id).emit('click');
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls.some(call => call.url.endsWith('/reveal')), false);
  assert.ok(h.nodes().some(node => node.textContent.includes('protected because')));
  await h.button('Reveal test evidence').emit('click');
  assert.equal(h.calls.filter(call => call.url.includes(id) && !call.url.endsWith('/reveal')).length, 2);
  assert.ok(h.nodes().some(node => node.textContent.includes('Tests 4; passed 1; failed 1; errors 0; skipped 1; xfailed 1')));
  assert.ok(h.nodes().some(node => node.textContent.includes('revealed outside protocol')));
  assert.ok(h.nodes().some(node => node.textContent.includes('explicitly listed files only')));
  assert.ok(h.nodes().some(node => node.textContent.includes('details are truncated')));
  assert.ok(h.nodes().some(node => node.textContent.includes('not a continuous freshness guarantee')));
  await h.button('Observe current identity now').emit('click');
  assert.ok(h.nodes().some(node => node.textContent === 'Identity observed at: 999.'));
  assert.ok(h.nodes().some(node => node.textContent === 'source'));
  assert.ok(h.nodes().some(node => node.textContent === 'different'));
  assert.ok(h.nodes().some(node => node.textContent === 'Current tool version match'));
});

test('navigation invalidates cancellation response and detached handlers', async () => {
  const id = 'e'.repeat(32); const cancel = deferred();
  const h = harness((url, options) => {
    if (url.endsWith('/cancel')) return cancel.promise;
    if (url.includes(id)) return protectedDetail(job(id, 'running'));
    return list([job(id, 'running')]);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  const detachedOpen = h.button(id);
  await detachedOpen.emit('click');
  const cancelling = h.button('Cancel test job').emit('click'); await flush();
  const callsBeforeNavigation = h.calls.length;
  h.ui.beginView();
  h.ui.replaceView(new Node('h1', { text: 'New destination' }));
  cancel.resolve({ job: job(id, 'cancelled') });
  await cancelling;
  await detachedOpen.emit('click');
  assert.equal(h.calls.length, callsBeforeNavigation);
  assert.equal(h.view.childNodes[0].textContent, 'New destination');
});

test('stale selected-job response cannot replace a newer selection', async () => {
  const oldId = 'f'.repeat(32); const newId = '1'.repeat(32); const slow = deferred();
  const h = harness(url => {
    if (url.includes(oldId)) return slow.promise;
    if (url.includes(newId)) return protectedDetail(job(newId));
    return list([job(oldId), job(newId)]);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  const old = h.button(oldId).emit('click'); await flush();
  await h.button(newId).emit('click');
  slow.resolve(protectedDetail(job(oldId))); await old;
  assert.ok(h.nodes().some(node => node.textContent === `Test job ${newId}`));
  assert.equal(h.nodes().some(node => node.textContent === `Test job ${oldId}`), false);
});

test('detached detail actions cannot mutate a newly selected job', async () => {
  const oldId = '3'.repeat(32); const newId = '4'.repeat(32);
  const h = harness((url, options) => {
    if (options) assert.fail('a detached action submitted ' + url);
    if (url.includes(oldId)) return protectedDetail(job(oldId, 'running'));
    if (url.includes(newId)) return protectedDetail(job(newId));
    return list([job(oldId, 'running'), job(newId)]);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button(oldId).emit('click');
  const detachedCancel = h.button('Cancel test job');
  await h.button(newId).emit('click');
  const count = h.calls.length;
  await detachedCancel.emit('click');
  assert.equal(h.calls.length, count);
  assert.ok(h.nodes().some(node => node.textContent === `Test job ${newId}`));
});

for (const returnToOriginal of [false, true]) test(`settling a mutation does not strand controls (return to original: ${returnToOriginal})`, async () => {
  const oldId = '5'.repeat(32); const newId = '6'.repeat(32); const pending = deferred();
  const h = harness((url, options) => {
    if (url.endsWith(`/jobs/${oldId}/cancel`)) return pending.promise;
    if (options) assert.fail('unexpected mutation ' + url);
    if (url.includes(oldId)) return protectedDetail(job(oldId, 'running'));
    if (url.includes(newId)) return protectedDetail(job(newId, 'running'));
    return list([job(oldId, 'running'), job(newId, 'running')]);
  });
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button(oldId).emit('click');
  const oldMutation = h.button('Cancel test job').emit('click'); await flush();
  await h.button(newId).emit('click');
  assert.equal(h.button('Cancel test job').disabled, false);
  assert.equal(h.button('Refresh selected test').disabled, false);
  if (returnToOriginal) {
    await h.button(oldId).emit('click');
    assert.equal(h.button('Cancel test job').disabled, true);
  }
  pending.resolve({ job: job(oldId, 'canceled') }); await oldMutation;
  assert.equal(h.button('Cancel test job').disabled, false);
  assert.equal(h.button('Refresh selected test').disabled, false);
  assert.ok(h.nodes().some(node => node.textContent === `Test job ${returnToOriginal ? oldId : newId}`));
});

test('verified cases, versions, hash states, and issue text stay collapsed and text-only', async () => {
  const id = '2'.repeat(32);
  const h = harness(url => url.includes(id) ? { schema_version: 1, job: job(id), evidence: verifiedEvidence } : list([job(id)]));
  const system = createSystemTests(h.ui); h.setDispose(system.dispose); await system.load();
  await h.button(id).emit('click');
  const details = h.nodes().filter(node => node.tagName === 'DETAILS');
  assert.equal(details.length, 2);
  assert.ok(details.every(node => node.open === false));
  assert.equal(h.nodes().some(node => ['IMG', 'SCRIPT', 'B'].includes(node.tagName)), false);
  assert.ok(h.nodes().some(node => node.textContent === '<script>alert(1)</script> — failed'));
  assert.ok(h.nodes().some(node => node.textContent.includes('plain <img> text')));
  assert.ok(h.nodes().some(node => node.textContent === 'unknown'));
  assert.equal(h.nodes().some(node => /raw log|download|register/i.test(node.textContent)), false);
  assert.equal(h.calls.some(call => /log|download|register/.test(call.url)), false);
});
