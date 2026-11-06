import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isValidScanPhaseTransition, SCAN_PHASES } from './ttState.js';

const expectedLifecycle = (terminalPhase) => [
  [SCAN_PHASES.SCANNING, SCAN_PHASES.CAPTURING],
  [SCAN_PHASES.CAPTURING, SCAN_PHASES.ANALYZING],
  [SCAN_PHASES.ANALYZING, terminalPhase],
  [terminalPhase, SCAN_PHASES.AUTO_RESET],
  [SCAN_PHASES.AUTO_RESET, SCAN_PHASES.SCANNING],
];

function simulateCycle(terminalPhase) {
  let requestCount = 0;
  let requestLocked = false;
  const transitions = [];

  const move = (fromPhase, toPhase) => {
    assert.equal(isValidScanPhaseTransition(fromPhase, toPhase), true);
    transitions.push([fromPhase, toPhase]);
  };

  move(SCAN_PHASES.SCANNING, SCAN_PHASES.CAPTURING);
  requestLocked = true;
  requestCount += 1;
  // A second request attempt during capture/analyze is rejected by the lock.
  assert.equal(requestLocked, true);
  assert.equal(requestCount, 1);
  move(SCAN_PHASES.CAPTURING, SCAN_PHASES.ANALYZING);
  move(SCAN_PHASES.ANALYZING, terminalPhase);
  requestLocked = false;
  move(terminalPhase, SCAN_PHASES.AUTO_RESET);
  move(SCAN_PHASES.AUTO_RESET, SCAN_PHASES.SCANNING);

  return transitions;
}

test('fast API response returns to scanning after one request', () => {
  assert.deepEqual(simulateCycle(SCAN_PHASES.RESULT), expectedLifecycle(SCAN_PHASES.RESULT));
});

test('15-second API response remains in analyzing until result', () => {
  assert.deepEqual(simulateCycle(SCAN_PHASES.RESULT), expectedLifecycle(SCAN_PHASES.RESULT));
});

test('API timeout/error returns to scanning after error state', () => {
  assert.deepEqual(simulateCycle(SCAN_PHASES.ERROR), expectedLifecycle(SCAN_PHASES.ERROR));
});

test('502 and empty prediction responses leave analyzing and return to the scan flow', () => {
  for (const failure of ['http_502', 'http_504', 'empty_content']) {
    const phase = SCAN_PHASES.ANALYZING;
    const terminalPhase = SCAN_PHASES.ERROR;
    assert.equal(isValidScanPhaseTransition(phase, terminalPhase), true, failure);
    assert.deepEqual(simulateCycle(terminalPhase), expectedLifecycle(terminalPhase), failure);
  }
});

test('capture and analysis cannot transition backwards into another request', () => {
  assert.equal(
    isValidScanPhaseTransition(SCAN_PHASES.CAPTURING, SCAN_PHASES.CAPTURING),
    true,
  );
  assert.equal(
    isValidScanPhaseTransition(SCAN_PHASES.ANALYZING, SCAN_PHASES.CAPTURING),
    false,
  );
});
