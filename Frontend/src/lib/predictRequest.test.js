import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  getPredictionFailure,
  PREDICTION_ERROR_RESET_MS,
  PREDICT_TIMEOUT_MS,
  requestPrediction,
} from './predictRequest.js';

test('browser prediction deadline is 180 seconds', () => {
  assert.equal(PREDICT_TIMEOUT_MS, 180_000);
});

test('request failures reset the kiosk after eight seconds', () => {
  assert.equal(PREDICTION_ERROR_RESET_MS, 8_000);
});

test('502, 504 and invalid prediction responses become terminal errors', () => {
  const candidates = [{}, {}, {}];
  const upstreamFailures = [502, 504].map((status) => getPredictionFailure(
    { ok: false, status },
    { status: 'api_error', detail: 'upstream failed' },
  ));
  const emptyResponse = getPredictionFailure({ ok: true, status: 200 }, null);
  const incomplete = getPredictionFailure({ ok: true, status: 200 }, { top_candidates: candidates.slice(0, 2) });

  assert.ok(upstreamFailures.every((failure) => failure instanceof Error));
  assert.ok(upstreamFailures.every((failure) => failure.responseStatus === 'api_error'));
  assert.ok(emptyResponse instanceof Error);
  assert.ok(incomplete instanceof Error);
  assert.equal(getPredictionFailure({ ok: true, status: 200 }, { top_candidates: candidates }), null);
});

test('network failures reject immediately for the kiosk error handler', async () => {
  const networkError = new TypeError('network unavailable');
  await assert.rejects(requestPrediction({
    imageBlob: new Blob(['capture']),
    controller: new AbortController(),
    fetchImpl: async () => { throw networkError; },
    timeoutMs: 1_000,
  }), (error) => error === networkError);
});

test('invalid JSON responses reject immediately for the kiosk error handler', async () => {
  await assert.rejects(requestPrediction({
    imageBlob: new Blob(['capture']),
    controller: new AbortController(),
    fetchImpl: async () => ({
      status: 502,
      json: async () => { throw new SyntaxError('invalid JSON'); },
    }),
    timeoutMs: 1_000,
  }), SyntaxError);
});
