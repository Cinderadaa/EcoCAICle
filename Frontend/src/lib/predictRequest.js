export const PREDICT_TIMEOUT_MS = 180_000;
export const PREDICTION_ERROR_RESET_MS = 8_000;

export function getPredictionFailure(response, payload) {
  if (!response || !payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return new Error('Prediction service returned an empty or invalid response');
  }
  if (!response.ok || payload.status === 'api_error') {
    const error = new Error(payload.detail || payload.error || `Prediction failed (${response.status})`);
    error.responseStatus = payload.status || 'api_error';
    return error;
  }
  const prediction = payload.prediction && typeof payload.prediction === 'object'
    ? payload.prediction
    : payload;
  if (!Array.isArray(prediction.top_candidates) || prediction.top_candidates.length !== 3) {
    return new Error('Prediction service returned an incomplete classification');
  }
  return null;
}

function tracePrediction(event, cycleId, details = {}) {
  if (process.env.NODE_ENV !== 'development') return;
  console.debug(`[ScanTrace] ${event}`, {
    cycleId,
    timestamp: new Date().toISOString(),
    ...details,
  });
}

/** One request per captured image. The caller owns the controller for unmount cleanup. */
export async function requestPrediction({
  imageBlob,
  controller,
  cycleId = 'unknown',
  resetCycleId = cycleId,
  timeoutMs = PREDICT_TIMEOUT_MS,
  fetchImpl = fetch,
  onStart = () => {},
  onResponse = () => {},
}) {
  const formData = new FormData();
  formData.append('file', imageBlob, 'capture.jpg');
  const startedAt = Date.now();
  let timedOut = false;
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  let responseReceived = false;

  try {
    tracePrediction('prediction request URL', cycleId, {
      url: '/api/classify',
      method: 'POST',
      timeoutMs,
    });
    onStart();
    const response = await fetchImpl('/api/classify', {
      method: 'POST',
      body: formData,
      headers: {
        'x-scan-cycle-id': String(cycleId),
        'x-reset-cycle-id': String(resetCycleId),
      },
      signal: controller.signal,
    });
    responseReceived = true;
    onResponse(response.status, Date.now() - startedAt);
    const payload = await response.json();
    tracePrediction('prediction request completed', cycleId, {
      status: response.status,
      elapsedMs: Date.now() - startedAt,
    });
    return { response, payload };
  } catch (error) {
    if (!responseReceived) onResponse(null, Date.now() - startedAt);
    tracePrediction('prediction request failed', cycleId, {
      name: error?.name || 'Error',
      message: error?.message || 'Prediction request failed',
      elapsedMs: Date.now() - startedAt,
      aborted: controller.signal.aborted,
    });
    if (timedOut) {
      const timeoutError = new Error('Prediction timed out');
      timeoutError.name = 'TimeoutError';
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}
