import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PREDICT_TIMEOUT_MS, requestPrediction } from './predictRequest.js';
import { isValidScanPhaseTransition, SCAN_PHASES } from './ttState.js';
import { attachCameraVideo, getLiveCameraStream } from './cameraLifecycle.js';

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

test('analysis timeout is 30 seconds and a 16-second prediction succeeds', async () => {
  assert.equal(PREDICT_TIMEOUT_MS, 30_000);
  let calls = 0;
  const { response } = await requestPrediction({
    imageBlob,
    controller: new AbortController(),
    fetchImpl: async () => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 16_000));
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

test('default timeout aborts an actual request after 30 seconds', async () => {
  const controller = new AbortController();
  const startedAt = Date.now();
  await assert.rejects(requestPrediction({
    imageBlob,
    controller,
    fetchImpl: (_, options) => new Promise((_, reject) => {
      options.signal.addEventListener(
        'abort',
        () => reject(new DOMException('Aborted', 'AbortError')),
        { once: true },
      );
    }),
  }), { name: 'TimeoutError' });
  assert.ok(Date.now() - startedAt >= 30_000);
  assert.equal(controller.signal.aborted, true);
});

test('three consecutive requests can complete on separate scan cycles', async () => {
  const cycleIds = [];
  const resetCycleIds = [];
  for (let cycleId = 1; cycleId <= 3; cycleId += 1) {
    const { response } = await requestPrediction({
      imageBlob,
      controller: new AbortController(),
      cycleId,
      resetCycleId: cycleId,
      fetchImpl: async (_, options) => {
        cycleIds.push(options.headers['x-scan-cycle-id']);
        resetCycleIds.push(options.headers['x-reset-cycle-id']);
        return success();
      },
    });
    assert.equal(response.status, 200);
  }
  assert.deepEqual(cycleIds, ['1', '2', '3']);
  assert.deepEqual(resetCycleIds, ['1', '2', '3']);
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

test('API error auto-resets to a second scan on the same live video and stream', async () => {
  const stream = {
    getVideoTracks: () => [{ readyState: 'live' }],
    getTracks: () => [{ stop() {} }],
  };
  const video = {
    srcObject: null,
    paused: true,
    readyState: 0,
    async play() { this.paused = false; this.readyState = 2; },
  };
  const { stream: initialStream } = await getLiveCameraStream(null, async () => stream);
  await attachCameraVideo(video, initialStream);
  const sameVideo = video;
  let phase = SCAN_PHASES.SCANNING;
  let locked = false;
  let calls = 0;
  const advance = (next) => {
    assert.equal(isValidScanPhaseTransition(phase, next), true);
    phase = next;
  };

  advance(SCAN_PHASES.CAPTURING);
  locked = true;
  advance(SCAN_PHASES.ANALYZING);
  const first = await requestPrediction({
    imageBlob,
    controller: new AbortController(),
    fetchImpl: async () => { calls += 1; return success(502); },
  });
  assert.equal(first.response.ok, false);
  advance(SCAN_PHASES.ERROR);
  locked = false;
  advance(SCAN_PHASES.AUTO_RESET);
  advance(SCAN_PHASES.SCANNING);

  const { stream: resumedStream, reused } = await getLiveCameraStream(
    stream,
    async () => { throw new Error('the active camera stream should be reused'); },
  );
  assert.equal(reused, true);
  await attachCameraVideo(video, resumedStream);
  assert.equal(video, sameVideo);
  assert.equal(video.srcObject, stream);
  assert.equal(video.paused, false);

  assert.equal(locked, false);
  advance(SCAN_PHASES.CAPTURING);
  locked = true;
  advance(SCAN_PHASES.ANALYZING);
  const second = await requestPrediction({
    imageBlob,
    controller: new AbortController(),
    fetchImpl: async () => { calls += 1; return success(200); },
  });
  assert.equal(second.response.ok, true);
  advance(SCAN_PHASES.RESULT);
  assert.equal(calls, 2);
  assert.equal(video.srcObject, stream);
  assert.equal(stream.getVideoTracks()[0].readyState, 'live');
});
