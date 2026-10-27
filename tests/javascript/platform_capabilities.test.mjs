import test from 'node:test';
import assert from 'node:assert/strict';
import { capabilityAvailable, routeCapability } from '../../python/qte/gui/static/platform_capabilities.js';

test('host capabilities default to blocked and duplicate/malformed entries never enable controls', () => {
  for (const value of [null, {}, { capabilities: [] }, { capabilities: [{ id: 'research', state: 'blocked' }] },
    { capabilities: [{ id: 'research', state: 'available' }, { id: 'research', state: 'available' }] }]) {
    assert.equal(capabilityAvailable(value, 'research'), false);
  }
  assert.equal(capabilityAvailable({ capabilities: [{ id: 'research', state: 'available' }] }, 'research'), true);
});

test('deep links require host capabilities before any workflow module is loaded', () => {
  assert.equal(routeCapability('/research/new/run'), 'research');
  assert.equal(routeCapability('/research/job-id'), 'research');
  assert.equal(routeCapability('/data/new'), 'data');
  assert.equal(routeCapability('/system'), 'test_execution');
  assert.equal(routeCapability('/system/benchmarks'), 'benchmarks');
  assert.equal(routeCapability('/system/connections'), 'connection_inspection');
  assert.equal(routeCapability('/library'), null);
});
