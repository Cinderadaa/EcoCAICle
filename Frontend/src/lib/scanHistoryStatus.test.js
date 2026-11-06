import assert from 'node:assert/strict';
import test from 'node:test';
import { getScanStatusChips } from './scanHistoryStatus.js';

test('duplicate unknown and fallback status values render once', () => {
  const chips = getScanStatusChips({
    status: 'unknown',
    classification_status: 'unknown',
    waste_category: 'unknown',
    mapping_fallback: true,
  });
  assert.deepEqual(chips, [['unknown', 'danger'], ['fallback', 'danger']]);
});
