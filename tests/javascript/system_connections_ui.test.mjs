import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../python/qte/gui/static/system_connections.js', import.meta.url));
const { createSystemConnections } = await import('data:text/javascript;base64,' + source.toString('base64'));

class Node {
  constructor(tag, attrs = {}, children = []) { this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = []; this.listeners = new Map(); this.textContent = ''; this.hidden = this.disabled = false; this.open = false; for (const [key, value] of Object.entries(attrs)) key === 'text' ? this.textContent = String(value) : this.setAttribute(key, value); this.append(...children); }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'class') this.className = String(value); if (['id', 'href', 'type'].includes(key)) this[key] = String(value); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.childNodes.push(node); } }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) || []), handler]); }
  async emit(type) { for (const handler of this.listeners.get(type) || []) await handler({ target: this, currentTarget: this, preventDefault() {} }); }
}
const walk = node => [node, ...node.childNodes.flatMap(walk)];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
const dto = (schwabPresent = true, robinhoodPresent = false) => ({ schema_version: 1, mode: 'offline_prerequisites_only', read_enabled: false, private_response_retention: false, connections: [
  { id: 'schwab', label: 'Schwab', scope: 'Contract-defined GET only', credential_environment_key: 'SCHWAB_ACCESS_TOKEN', credential_key_present: schwabPresent, credential_validity: 'not_checked', authentication: 'not_verified', read_enabled: false, contract_state: 'not_configured', signer_state: 'not_applicable', operations: [], blockers: ['broker_reads_disabled', 'contract_not_configured', ...(schwabPresent ? [] : ['credential_key_absent'])] },
  { id: 'robinhood_crypto', label: 'Robinhood Crypto', scope: 'Crypto GET only; no equities or trading', credential_environment_key: 'ROBINHOOD_CRYPTO_API_KEY', credential_key_present: robinhoodPresent, credential_validity: 'not_checked', authentication: 'not_verified', read_enabled: false, contract_state: 'not_applicable', signer_state: 'not_configured', operations: ['accounts', 'trading_pairs', 'holdings', 'orders'], blockers: ['broker_reads_disabled', 'signer_not_configured', ...(robinhoodPresent ? [] : ['credential_key_absent'])] },
] });
function harness(reply) {
  let view = new Node('main'), generation = 0, dispose = () => {}; const calls = [], statuses = [], navigation = [];
  const ui = { element: (tag, attrs, children) => new Node(tag, attrs, children), textLink: (href, text) => new Node('a', { href, text }), api: async (url, options) => { calls.push({ url, options }); return reply(url, options); }, beginView: () => { dispose(); return ++generation; }, isCurrentView: request => request === generation, replaceView: (...nodes) => view.replaceChildren(...nodes), setStatus: text => statuses.push(text), setNavigation: item => navigation.push(item), humanize: item => String(item).replaceAll('_', ' ') };
  return { ui, calls, statuses, navigation, setDispose: fn => { dispose = fn; }, get view() { return view; }, nodes: () => walk(view), button(text) { const item = walk(view).find(node => node.tagName === 'BUTTON' && node.textContent === text); assert.ok(item, text); return item; } };
}
afterEach(() => { delete globalThis.document; });

test('opening makes exactly one GET and displays only offline prerequisite controls', async () => {
  const h = harness((url, options) => { assert.equal(url, '/api/v1/connections'); assert.equal(options, undefined); return dto(); }); const view = createSystemConnections(h.ui); h.setDispose(view.dispose); await view.load();
  assert.equal(h.calls.length, 1); assert.equal(h.calls.some(call => call.options?.method === 'POST'), false); assert.deepEqual(h.navigation, ['system']);
  for (const text of ['System', 'Connections', 'Offline prerequisites only. No broker requests are made.', 'Schwab', 'Robinhood Crypto', 'Blocked — not connected', 'Refresh connection status']) assert.ok(h.nodes().some(node => node.textContent === text), text);
  assert.ok(h.nodes().some(node => node.textContent === 'Environment key present (value not inspected)')); assert.ok(h.nodes().some(node => node.textContent === 'Environment key absent'));
  assert.ok(h.nodes().some(node => node.href === '/library/broker-connections'));
  assert.equal(h.nodes().some(node => ['INPUT', 'TEXTAREA', 'SELECT'].includes(node.tagName)), false);
});

test('unknown payload fields and malformed responses are not rendered or treated as connected', async () => {
  const secret = 'secret-token-should-never-render'; const bad = dto(); bad.connections[0].authentication = 'authenticated'; bad.untrusted = secret;
  const h = harness(() => bad); const view = createSystemConnections(h.ui); h.setDispose(view.dispose); await view.load();
  const text = h.nodes().map(node => node.textContent).join(' ');
  assert.equal(text.includes(secret), false); assert.equal(text.includes('authenticated'), false); assert.ok(text.includes('could not be verified'));
  assert.equal(h.statuses.at(-1), 'Offline connection prerequisites unavailable.');
  assert.equal(text.includes('Blocked — not connected'), true); assert.equal(/ready|connected/i.test(text.replaceAll('Blocked — not connected', '')), false);
});

test('refresh is explicit and a stale failed request cannot replace a new destination', async () => {
  const pending = deferred(); let calls = 0; const h = harness(() => ++calls === 1 ? dto() : pending.promise); const view = createSystemConnections(h.ui); h.setDispose(view.dispose); await view.load();
  const refresh = h.button('Refresh connection status'); const refreshWork = refresh.emit('click'); await flush(); assert.equal(h.calls.length, 2);
  h.ui.beginView(); h.ui.replaceView(new Node('h1', { text: 'Elsewhere' })); pending.reject(new Error('network detail must not render')); await refreshWork;
  assert.equal(h.view.childNodes[0].textContent, 'Elsewhere'); await refresh.emit('click'); assert.equal(h.calls.length, 2);
});
