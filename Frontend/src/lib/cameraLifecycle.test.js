import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attachCameraVideo, getLiveCameraStream } from './cameraLifecycle.js';

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
