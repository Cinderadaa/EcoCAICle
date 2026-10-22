import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PREDICT_TIMEOUT_MS, requestPrediction } from './predictRequest.js';
import { isValidScanPhaseTransition, SCAN_PHASES } from './ttState.js';

const imageBlob = new Blob(['jpeg-bytes'], { type: 'image/jpeg' });
const success = (status = 200) => ({ status, ok: status < 400, json: async () => ({ status: 'success' }) });

test('fast prediction posts one captured image and returns the response', async () => {
  let calls = 0;
  const { response, payload } = await requestPrediction({
    imageBlob,
    controller: new AbortController(),
    fetchImpl: async (url, options) => {
      calls += 1;
      assert.equal(url, '/api/classify');
      assert.equal(options.method, 'POST');
      assert.equal(options.body.get('file').size, imageBlob.size);
      return success();
    },
  });
  assert.equal(calls, 1);
  assert.equal(response.status, 200);
  assert.equal(payload.status, 'success');
});

test('analysis timeout is 15 seconds and a slow response within the limit succeeds', async () => {
  assert.equal(PREDICT_TIMEOUT_MS, 15_000);
  let calls = 0;
  const { response } = await requestPrediction({
    imageBlob,
    controller: new AbortController(),
    fetchImpl: async () => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 14_000));
      return success();
    },
  });
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
});

test('timeout aborts the one active request', async () => {
  let calls = 0;
  const controller = new AbortController();
  await assert.rejects(requestPrediction({
    imageBlob,
    controller,
    timeoutMs: 20,
    fetchImpl: (_, options) => {
      calls += 1;
      return new Promise((_, reject) => {
        options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    },
  }), { name: 'TimeoutError' });
  assert.equal(calls, 1);
  assert.equal(controller.signal.aborted, true);
});

test('4xx and 5xx responses are delivered to the caller for the existing error screen', async () => {
  for (const status of [400, 502]) {
    let calls = 0;
    const { response } = await requestPrediction({
      imageBlob,
      controller: new AbortController(),
      fetchImpl: async () => { calls += 1; return success(status); },
    });
    assert.equal(response.status, status);
    assert.equal(response.ok, false);
    assert.equal(calls, 1);
  }
});

test('three sequential cycles each create one request with a fresh controller', async () => {
  let calls = 0;
  let phase = SCAN_PHASES.SCANNING;
  const cameraStream = { active: true };
  const advance = (next) => {
    assert.equal(isValidScanPhaseTransition(phase, next), true);
    phase = next;
  };

  for (let cycle = 0; cycle < 3; cycle += 1) {
    const controller = new AbortController();
    advance(SCAN_PHASES.CAPTURING);
    advance(SCAN_PHASES.ANALYZING);
    const { response } = await requestPrediction({
      imageBlob,
      controller,
      fetchImpl: async () => { calls += 1; return success(); },
    });
    assert.equal(response.status, 200);
    assert.equal(controller.signal.aborted, false);
    advance(SCAN_PHASES.RESULT);
    advance(SCAN_PHASES.AUTO_RESET);
    advance(SCAN_PHASES.SCANNING);
    assert.equal(cameraStream.active, true);
  }
  assert.equal(calls, 3);
  assert.equal(phase, SCAN_PHASES.SCANNING);
});
