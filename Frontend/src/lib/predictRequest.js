export const PREDICT_TIMEOUT_MS = 15_000;

/** One request per captured image. The caller owns the controller for unmount cleanup. */
export async function requestPrediction({
  imageBlob,
  controller,
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
    onStart();
    const response = await fetchImpl('/api/classify', {
      method: 'POST',
      body: formData,
      signal: controller.signal,
    });
    responseReceived = true;
    onResponse(response.status, Date.now() - startedAt);
    const payload = await response.json();
    return { response, payload };
  } catch (error) {
    if (!responseReceived) onResponse(null, Date.now() - startedAt);
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
