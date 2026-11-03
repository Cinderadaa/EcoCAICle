import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attachCameraVideo, getLiveCameraStream, waitForStableCameraFrame } from './cameraLifecycle.js';

function fakeStream() {
  return {
    getVideoTracks: () => [{ readyState: 'live' }],
    stopCalls: 0,
    getTracks() { return [{ stop: () => { this.stopCalls += 1; } }]; },
  };
}

function fakeVideo() {
  return {
    srcObject: null,
    paused: true,
    readyState: 0,
    playCalls: 0,
    async play() {
      this.playCalls += 1;
      this.paused = false;
      this.readyState = 2;
    },
  };
}

test('initial camera startup attaches a newly acquired live stream and plays it', async () => {
  const stream = fakeStream();
  let starts = 0;
  const { stream: readyStream, reused } = await getLiveCameraStream(null, async () => {
    starts += 1;
    return stream;
  });
  const video = fakeVideo();
  await attachCameraVideo(video, readyStream);

  assert.equal(reused, false);
  assert.equal(starts, 1);
  assert.equal(video.srcObject, stream);
  assert.equal(video.playCalls, 1);
});

test('three result/error reset cycles reuse and resume the mounted camera video', async () => {
  const stream = fakeStream();
  const video = fakeVideo();
  let starts = 0;
  const startStream = async () => { starts += 1; return stream; };

  for (let cycle = 0; cycle < 3; cycle += 1) {
    const { stream: reusedStream, reused } = await getLiveCameraStream(stream, startStream);
    assert.equal(reused, true);
    await attachCameraVideo(video, reusedStream);
    assert.equal(video.srcObject, stream);
    assert.equal(video.paused, false);

    // A result/error view can pause playback while retaining the same element.
    video.paused = true;
    video.readyState = 0;
  }

  assert.equal(starts, 0);
  assert.equal(video.playCalls, 3);
  assert.equal(stream.stopCalls, 0);
});

test('a stopped stream is replaced, and camera permission errors propagate to the UI handler', async () => {
  const stoppedStream = { getVideoTracks: () => [{ readyState: 'ended' }] };
  const replacement = fakeStream();
  const { stream, reused } = await getLiveCameraStream(stoppedStream, async () => replacement);
  assert.equal(stream, replacement);
  assert.equal(reused, false);

  await assert.rejects(
    getLiveCameraStream(null, async () => { throw new DOMException('Permission denied', 'NotAllowedError'); }),
    { name: 'NotAllowedError' },
  );
});

test('a 0.96 changed-pixel ratio passes presence detection and reaches capture readiness', async () => {
  const width = 64;
  const height = 48;
  const pixelCount = width * height;
  const rgba = new Uint8ClampedArray(pixelCount * 4);
  const reference = new Uint8Array(pixelCount);

  for (let pixel = 0; pixel < pixelCount; pixel += 1) {
    const x = pixel % width;
    const y = Math.floor(pixel / width);
    const gray = (x + y) % 2 === 0 ? 80 : 180;
    // Keep 4% of pixels identical and change the remaining 96% substantially.
    reference[pixel] = pixel < Math.round(pixelCount * 0.04) ? gray : 255 - gray;
    rgba[pixel * 4] = gray;
    rgba[pixel * 4 + 1] = gray;
    rgba[pixel * 4 + 2] = gray;
    rgba[pixel * 4 + 3] = 255;
  }

  const priorDocument = globalThis.document;
  globalThis.document = {
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({
        drawImage() {},
        getImageData: () => ({ data: rgba }),
      }),
    }),
  };

  try {
    const progress = [];
    let frameTime = 0;
    const readiness = await waitForStableCameraFrame({
      video: {
        readyState: 2,
        videoWidth: 640,
        get currentTime() { frameTime += 1; return frameTime; },
      },
      referenceFrame: reference,
      stableMs: 0,
      onProgress: (event) => progress.push(event),
    });

    assert.equal(readiness.ready, true);
    assert.ok(readiness.changedPixelRatio >= 0.95);
    assert.ok(progress.some((event) =>
      event.event === 'presence_check' &&
      event.presencePassed === true &&
      event.state === 'stable' &&
      event.changedPixelRatio >= 0.95,
    ));
  } finally {
    if (priorDocument === undefined) delete globalThis.document;
    else globalThis.document = priorDocument;
  }
});
