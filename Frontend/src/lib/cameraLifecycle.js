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
