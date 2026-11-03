export function hasLiveVideoTrack(stream) {
  return Boolean(stream?.getVideoTracks?.().some((track) => track.readyState === 'live'));
}

export async function getLiveCameraStream(stream, startStream) {
  if (hasLiveVideoTrack(stream)) return { stream, reused: true };
  const nextStream = await startStream();
  if (!hasLiveVideoTrack(nextStream)) throw new Error('Camera stream has no live video track');
  return { stream: nextStream, reused: false };
}

export async function attachCameraVideo(video, stream) {
  if (!video || !stream) return false;
  if (video.srcObject !== stream) video.srcObject = stream;
  if (video.paused || video.readyState < 2) await video.play();
  return true;
}

const FRAME_SAMPLE_WIDTH = 64;
const FRAME_SAMPLE_HEIGHT = 48;
const FRAME_CHECK_INTERVAL_MS = 200;
const MAX_STABLE_FRAME_DELTA = 7;
const PRESENCE_PIXEL_DELTA = 16;
const MIN_PRESENCE_CHANGED_RATIO = 0.005;

function sampleVideoFrame(video) {
  const canvas = document.createElement('canvas');
  canvas.width = FRAME_SAMPLE_WIDTH;
  canvas.height = FRAME_SAMPLE_HEIGHT;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  const gray = new Uint8Array(FRAME_SAMPLE_WIDTH * FRAME_SAMPLE_HEIGHT);
  let brightness = 0;
  let sharpness = 0;
  for (let y = 0; y < FRAME_SAMPLE_HEIGHT; y += 1) {
    for (let x = 0; x < FRAME_SAMPLE_WIDTH; x += 1) {
      const pixel = y * FRAME_SAMPLE_WIDTH + x;
      const offset = pixel * 4;
      const value = Math.round(data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114);
      gray[pixel] = value;
      brightness += value;
      if (x > 0) sharpness += Math.abs(value - gray[pixel - 1]);
      if (y > 0) sharpness += Math.abs(value - gray[pixel - FRAME_SAMPLE_WIDTH]);
    }
  }
  const pixels = gray.length;
  return {
    gray,
    brightness: brightness / pixels,
    sharpness: sharpness / ((FRAME_SAMPLE_WIDTH - 1) * FRAME_SAMPLE_HEIGHT + FRAME_SAMPLE_WIDTH * (FRAME_SAMPLE_HEIGHT - 1)),
  };
}

function frameDelta(previous, next) {
  if (!previous || !next || previous.length !== next.length) return Infinity;
  let difference = 0;
  for (let index = 0; index < previous.length; index += 1) {
    difference += Math.abs(previous[index] - next[index]);
  }
  return difference / previous.length;
}

function sceneChangedPixelRatio(current, reference) {
  if (!current || !reference || current.length !== reference.length) return null;
  let changedPixels = 0;
  for (let index = 0; index < current.length; index += 1) {
    if (Math.abs(current[index] - reference[index]) >= PRESENCE_PIXEL_DELTA) changedPixels += 1;
  }
  return changedPixels / current.length;
}

/** Wait for a changed foreground and a well-lit, reasonably sharp stable frame. */
export async function waitForStableCameraFrame({
  video,
  referenceFrame,
  stableMs = 900,
  shouldCancel = () => false,
  onProgress = () => {},
}) {
  const waitStartedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
  let previousFrame = null;
  let stableSince = null;
  let lastVideoTime = -1;
  let lastReportedState = '';
  let presenceActive = false;

  while (!shouldCancel()) {
    if (video?.readyState >= 2 && video.videoWidth > 0 && video.currentTime !== lastVideoTime) {
      const timestamp = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const frame = sampleVideoFrame(video);
      lastVideoTime = video.currentTime;
      if (!frame) {
        await new Promise((resolve) => setTimeout(resolve, FRAME_CHECK_INTERVAL_MS));
        continue;
      }

      const delta = frameDelta(previousFrame, frame.gray);
      const changedPixelRatio = sceneChangedPixelRatio(frame.gray, referenceFrame);
      const hasPresence = changedPixelRatio !== null &&
        changedPixelRatio >= MIN_PRESENCE_CHANGED_RATIO;
      if (hasPresence && !presenceActive) {
        onProgress({
          event: 'object_present',
          state: 'detected',
          changedPixelRatio,
          elapsedMs: Math.round(timestamp - waitStartedAt),
        });
      }
      presenceActive = hasPresence;
      const state = !hasPresence
        ? 'no_object'
        : frame.brightness < 25 || frame.brightness > 238
          ? 'exposure'
        : frame.sharpness < 3
          ? 'unclear'
          : delta <= MAX_STABLE_FRAME_DELTA
            ? 'stable'
            : 'moving';

      if (state === 'stable') {
        const stabilityTimerStarted = stableSince === null;
        if (stabilityTimerStarted) stableSince = timestamp;
        const stabilityElapsedMs = Math.round(timestamp - stableSince);
        onProgress({
          event: 'presence_check',
          state,
          changedPixelRatio,
          presenceThreshold: { min: MIN_PRESENCE_CHANGED_RATIO, max: 1 },
          presencePassed: hasPresence,
          stabilityElapsedMs,
          waitElapsedMs: Math.round(timestamp - waitStartedAt),
        });
        if (timestamp - stableSince >= stableMs) {
          return {
            ready: true,
            stableForMs: stabilityElapsedMs,
            changedPixelRatio,
            waitDurationMs: Math.round(timestamp - waitStartedAt),
          };
        }
        if (lastReportedState !== 'stable') {
          onProgress({
            state: 'stable',
            changedPixelRatio,
            stabilityTimerStarted,
            stabilityElapsedMs: Math.round(timestamp - stableSince),
            waitElapsedMs: Math.round(timestamp - waitStartedAt),
          });
          lastReportedState = 'stable';
        }
      } else {
        const stabilityTimerReset = stableSince !== null;
        stableSince = null;
        onProgress({
          event: 'presence_check',
          state,
          changedPixelRatio,
          presenceThreshold: { min: MIN_PRESENCE_CHANGED_RATIO, max: 1 },
          presencePassed: hasPresence,
          stabilityElapsedMs: 0,
          waitElapsedMs: Math.round(timestamp - waitStartedAt),
        });
        if (lastReportedState !== state) {
          onProgress({
            state,
            changedPixelRatio,
            stabilityTimerReset,
            waitElapsedMs: Math.round(timestamp - waitStartedAt),
          });
          lastReportedState = state;
        }
      }
      previousFrame = frame.gray;
    }

    await new Promise((resolve) => setTimeout(resolve, FRAME_CHECK_INTERVAL_MS));
  }

  return { ready: false, cancelled: true };
}
