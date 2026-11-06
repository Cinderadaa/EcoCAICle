'use client';

import { useEffect, useRef, useState } from 'react';
import {
  getTTState,
  isValidScanPhaseTransition,
  RESULT_RESET_SECONDS,
  SCAN_PHASES,
  setTTState,
  TT_STATES,
} from '@/lib/ttState';
import { WASTE_TYPE, normalizeClassificationResponse } from '@/lib/wasteClassification';
import {
  getPredictionFailure,
  PREDICTION_ERROR_RESET_MS,
  PREDICT_TIMEOUT_MS,
  requestPrediction,
} from '@/lib/predictRequest';
import {
  attachCameraVideo,
  getLiveCameraStream,
  hasLiveVideoTrack,
  waitForStableCameraFrame,
} from '@/lib/cameraLifecycle';
import { mockWasteResults } from '@/data/mockData';
import WasteResult from '@/components/WasteResult';

// Use the live classification service by default. Mock results are available
// only when explicitly enabled for a local demo.
const USE_MOCK_CLASSIFICATION = process.env.NEXT_PUBLIC_USE_MOCK_CLASSIFICATION === 'true';
const SCAN_RESET_SECONDS = 15;
const CAPTURE_RETRY_DELAY_MS = 250;
const CAMERA_FRAME_STABLE_MS = 800;
const SCREEN_TRANSITION_MS = 540;
const PRESENCE_STABLE_MS = 150;
const PRESENCE_FRAME_WIDTH = 64;
const PRESENCE_FRAME_HEIGHT = 48;
const ERROR_RESET_DELAY_MS = 2_500;

function traceScanEvent(event, cycleId, details = {}) {
  if (process.env.NODE_ENV !== 'development') return;
  console.debug(`[ScanTrace] ${event}`, {
    cycleId,
    timestamp: new Date().toISOString(),
    monotonicMs: typeof performance !== 'undefined' ? Number(performance.now().toFixed(1)) : null,
    ...details,
  });
}

const DEV_LOGGED_SCAN_TRANSITIONS = new Set([
  `${SCAN_PHASES.SCANNING}->${SCAN_PHASES.CAPTURING}`,
  `${SCAN_PHASES.CAPTURING}->${SCAN_PHASES.ANALYZING}`,
  `${SCAN_PHASES.ANALYZING}->${SCAN_PHASES.RESULT}`,
  `${SCAN_PHASES.ANALYZING}->${SCAN_PHASES.ERROR}`,
  `${SCAN_PHASES.RESULT}->${SCAN_PHASES.SCANNING}`,
  `${SCAN_PHASES.ERROR}->${SCAN_PHASES.SCANNING}`,
]);

function drawScaledFromVideo(video, canvas) {
  const vw = video.videoWidth || 640;
  const vh = video.videoHeight || 480;

  // ใช้ทั้งเฟรมเหมือนไฟล์เก่า (no crop)
  const maxDim = 1280; // เพิ่มรายละเอียดสำหรับ webcam (ลด false-negative)
  const scale = Math.min(1, maxDim / Math.max(vw, vh));

  canvas.width = Math.round(vw * scale);
  canvas.height = Math.round(vh * scale);

  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
}

function simpleHash(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h << 5) - h + str.charCodeAt(i);
    h |= 0;
  }
  return h;
}

function samplePresenceReference(video, canvas) {
  if (!video?.videoWidth || !canvas) return null;
  canvas.width = PRESENCE_FRAME_WIDTH;
  canvas.height = PRESENCE_FRAME_HEIGHT;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  const gray = new Uint8Array(PRESENCE_FRAME_WIDTH * PRESENCE_FRAME_HEIGHT);
  for (let pixel = 0; pixel < gray.length; pixel += 1) {
    const offset = pixel * 4;
    gray[pixel] = Math.round(data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114);
  }
  return gray;
}

function isFrameEmpty(ctx, w, h, threshold = 2.5) {
  // Check if camera is just pointing at a blank background
  const data = ctx.getImageData(0, 0, w, h).data;
  let diffSum = 0;
  const step = 4 * 10;
  let samples = 0;
  
  for (let i = 0; i < data.length - step; i += step) {
    const diff = Math.abs(data[i] - data[i+step]) + 
                 Math.abs(data[i+1] - data[i+1+step]) + 
                 Math.abs(data[i+2] - data[i+2+step]);
    diffSum += diff;
    samples++;
  }
  
  const avgDiff = diffSum / samples;
  return avgDiff < threshold;
}

/**
 * Check if a frame is too dark or too overexposed to be useful.
 * Returns { ok: boolean, meanBrightness: number }
 */
function checkFrameBrightness(ctx, w, h) {
  const data = ctx.getImageData(0, 0, w, h).data;
  let total = 0;
  // Sample every 8th pixel for performance
  const step = 8;
  let count = 0;
  for (let i = 0; i < data.length; i += 4 * step) {
    // Luminance approximation: 0.299R + 0.587G + 0.114B
    total += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    count++;
  }
  const mean = count > 0 ? total / count : 128;
  return { ok: mean > 30 && mean < 225, meanBrightness: Math.round(mean) };
}

function waitForVideoFrame(video, timeoutMs = 1_500) {
  if (!video) return Promise.resolve(false);

  return new Promise((resolve) => {
    let settled = false;
    let frameCallbackId = null;
    let animationFrameId = null;
    const initialCurrentTime = video.currentTime;
    const timeoutId = setTimeout(() => finish(false), timeoutMs);

    const finish = (hasFrame) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      if (frameCallbackId !== null && video.cancelVideoFrameCallback) {
        video.cancelVideoFrameCallback(frameCallbackId);
      }
      if (animationFrameId !== null) cancelAnimationFrame(animationFrameId);
      resolve(hasFrame);
    };

    if (typeof video.requestVideoFrameCallback === 'function') {
      frameCallbackId = video.requestVideoFrameCallback(() => finish(true));
      return;
    }

    const checkPlaybackProgress = () => {
      if (video.videoWidth > 0 && video.currentTime > initialCurrentTime) {
        finish(true);
        return;
      }
      animationFrameId = requestAnimationFrame(checkPlaybackProgress);
    };
    animationFrameId = requestAnimationFrame(checkPlaybackProgress);
  });
}

export default function Page() {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const capturedImageRef = useRef('');
  const scanningRef = useRef(false);
  const activeScanRef = useRef(null);
  const scanOperationIdRef = useRef(0);
  const scanPhaseRef = useRef(SCAN_PHASES.SCANNING);
  const mountedRef = useRef(false);
  const autoResetTimerRef = useRef(null);
  const screenTransitionTimerRef = useRef(null);
  const screenTransitionRef = useRef('idle');
  const beginScanTransitionRef = useRef(null);
  const cameraRetryTimerRef = useRef(null);
  const cameraInitRetryTimerRef = useRef(null);
  const cameraRetryAttemptRef = useRef(0);
  const cameraRetryAtRef = useRef(0);
  const cameraInitInFlightRef = useRef(null);
  const cameraWarmedRef = useRef(false);
  const cameraStartRef = useRef(null);
  const cameraInitStartedAtRef = useRef(null);
  const globalStreamRef = useRef(null);
  const emptyFrameCountRef = useRef(0);
  const mockResultIndexRef = useRef(0);
  const presenceSinceRef = useRef(null);
  const scanBackgroundFrameRef = useRef(null);

  const [status, setStatus] = useState('กำลังเปิดกล้อง…');
  const [subStatus, setSubStatus] = useState('');
  const [cameraStatus, setCameraStatus] = useState('initializing');
  const [ttState, setTTStateLocal] = useState(TT_STATES.IDLE);
  const [resultData, setResultData] = useState(null);
  const [capturedImage, setCapturedImage] = useState('');
  const [capturedImageLoaded, setCapturedImageLoaded] = useState(false);
  const [countdown, setCountdown] = useState(15);
  const [currentResultIndex, setCurrentResultIndex] = useState(0);
  const [scanPhase, setScanPhase] = useState(SCAN_PHASES.SCANNING);
  const [scanCycle, setScanCycle] = useState(0);
  const [screenTransition, setScreenTransition] = useState('idle');
  const scanDebugReasonRef = useRef('');
  const currentResultIndexRef = useRef(0);
  
  // Touchless Kiosk Mode Stream
  const [globalStream, setGlobalStream] = useState(null);
  const idleVideoRef = useRef(null);
  const presenceCanvasRef = useRef(null);
  const lastIdleFrameRef = useRef(null);
  const cameraBackgroundFrameRef = useRef(null);

  function logCamera(message, details = undefined) {
    if (process.env.NODE_ENV === 'development') {
      console.debug(`[Camera] ${message}`, details || '');
    }
  }

  function traceCameraTiming(event, details = {}) {
    const stream = globalStreamRef.current || globalStream;
    traceScanEvent(event, scanOperationIdRef.current + 1, {
      phase: scanPhaseRef.current,
      kioskState: typeof window !== 'undefined' ? getTTState() : 'server',
      reactKioskState: ttState,
      liveVideoTrack: hasLiveVideoTrack(stream),
      pageVisible: typeof document !== 'undefined' ? document.visibilityState === 'visible' : null,
      ...details,
    });
  }

  function updateScanDebugReason(reason) {
    if (process.env.NODE_ENV !== 'development') return;
    if (scanDebugReasonRef.current === reason) return;
    scanDebugReasonRef.current = reason;
    console.debug('[ScanDebug]', reason);
  }

  function scheduleCameraRetry() {
    if (!mountedRef.current || cameraInitRetryTimerRef.current !== null) return;
    const delayMs = Math.min(15_000, 1_000 * (2 ** cameraRetryAttemptRef.current));
    cameraRetryAttemptRef.current += 1;
    cameraRetryAtRef.current = Date.now() + delayMs;
      updateScanDebugReason(`waiting for camera: retrying in ${delayMs} ms`);
    cameraInitRetryTimerRef.current = setTimeout(() => {
      cameraInitRetryTimerRef.current = null;
      cameraStartRef.current?.();
    }, delayMs);
  }

  function transitionScanPhase(nextPhase, logFrom = null) {
    const previousPhase = scanPhaseRef.current;
    if (previousPhase === nextPhase) return;
    if (!isValidScanPhaseTransition(previousPhase, nextPhase)) {
      if (process.env.NODE_ENV !== 'production') {
        console.warn(`[ScanState] ignored invalid transition ${previousPhase} → ${nextPhase}`);
      }
      return;
    }

    scanPhaseRef.current = nextPhase;
    if (mountedRef.current) setScanPhase(nextPhase);

    const transitionFrom = logFrom || previousPhase;
    const transitionKey = `${transitionFrom}->${nextPhase}`;
    if (process.env.NODE_ENV !== 'production' && DEV_LOGGED_SCAN_TRANSITIONS.has(transitionKey)) {
      console.debug(`[ScanState] ${transitionFrom} → ${nextPhase}`);
    }
  }

  function resetCaptureGuards({ preserveScanBackgroundFrame = false } = {}) {
    scanningRef.current = false;
    emptyFrameCountRef.current = 0;
    lastIdleFrameRef.current = null;
    presenceSinceRef.current = null;
    if (!preserveScanBackgroundFrame) scanBackgroundFrameRef.current = null;
    cameraBackgroundFrameRef.current = null;
  }

  function cancelActiveScan() {
    const activeScan = activeScanRef.current;
    traceScanEvent('active scan cancelled or reset', activeScan?.id || scanOperationIdRef.current, {
      hadActiveScan: Boolean(activeScan),
      requestStarted: activeScan?.requestStarted || false,
      requestCompleted: activeScan?.requestCompleted || false,
      abortControllerAborted: activeScan?.controller?.signal?.aborted || false,
      kioskState: getTTState(),
      scanPhase: scanPhaseRef.current,
    });
    if (activeScan) {
      activeScan.cancelled = true;
      activeScan.controller.abort();
      activeScanRef.current = null;
    }
    scanningRef.current = false;
  }

  function isActiveScan(activeScan) {
    return mountedRef.current &&
      activeScanRef.current === activeScan &&
      !activeScan.cancelled;
  }

  function getScreenTransitionDuration() {
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      return 160;
    }
    return SCREEN_TRANSITION_MS;
  }

  function resetToWelcome() {
    if (!mountedRef.current) return;
    const cycleId = activeScanRef.current?.id || scanOperationIdRef.current;
    traceScanEvent('scan reset started', cycleId, { phase: scanPhaseRef.current });
    logCamera('camera reset requested', { cycle: scanOperationIdRef.current });
    if (activeScanRef.current?.requestStarted && !activeScanRef.current?.requestCompleted) {
      updateScanDebugReason('capture blocked: request still in flight during reset');
      if (process.env.NODE_ENV !== 'production') {
        console.warn('[Predict] reset before request completed', { cycle: activeScanRef.current.id });
      }
      traceScanEvent('scan reset deferred', cycleId, { reason: 'prediction request still in flight' });
      return;
    }

    const previousPhase = scanPhaseRef.current;
    transitionScanPhase(SCAN_PHASES.AUTO_RESET);
    cancelActiveScan();

    if (autoResetTimerRef.current !== null) {
      clearTimeout(autoResetTimerRef.current);
      autoResetTimerRef.current = null;
    }
    if (cameraRetryTimerRef.current !== null) {
      clearTimeout(cameraRetryTimerRef.current);
      cameraRetryTimerRef.current = null;
    }
    if (cameraInitRetryTimerRef.current !== null) {
      clearTimeout(cameraInitRetryTimerRef.current);
      cameraInitRetryTimerRef.current = null;
    }
    if (screenTransitionTimerRef.current !== null) {
      clearTimeout(screenTransitionTimerRef.current);
      screenTransitionTimerRef.current = null;
    }

    resetCaptureGuards();
    cameraRetryAtRef.current = 0;
    cameraRetryAttemptRef.current = 0;
    cameraWarmedRef.current = false;
    capturedImageRef.current = '';
    setCapturedImage('');
    setCapturedImageLoaded(false);
    setResultData(null);
    currentResultIndexRef.current = 0;
    setCurrentResultIndex(0);
    sessionStorage.removeItem('tt:last');
    setSubStatus('');
    setStatus('วางขยะหน้ากล้องเพื่อเริ่มสแกน');
    updateScanDebugReason('waiting for object: present waste in the scan area');
    setCountdown(SCAN_RESET_SECONDS);

    // A completed result returns straight to Welcome. The reverse scan
    // transition is only for resets that originate from the live scan flow.
    if (previousPhase === SCAN_PHASES.RESULT) {
      screenTransitionRef.current = 'idle';
      setScreenTransition('idle');
      setTTState(TT_STATES.IDLE);
      setTTStateLocal(TT_STATES.IDLE);
      transitionScanPhase(SCAN_PHASES.SCANNING, previousPhase);
      traceScanEvent('welcome screen restored after result', cycleId);
      return;
    }

    screenTransitionRef.current = 'exiting';
    setScreenTransition('exiting');

    const stream = globalStreamRef.current;
    if (hasLiveVideoTrack(stream) && videoRef.current) {
      attachCameraVideo(videoRef.current, stream).catch((playError) => {
        logCamera('camera initialization failed', { phase: 'return transition playback', message: playError?.message });
      });
    }

    screenTransitionTimerRef.current = setTimeout(() => {
      screenTransitionTimerRef.current = null;
      if (!mountedRef.current) return;
      screenTransitionRef.current = 'idle';
      setScreenTransition('idle');
      setTTState(TT_STATES.IDLE);
      setTTStateLocal(TT_STATES.IDLE);
      transitionScanPhase(
        SCAN_PHASES.SCANNING,
        previousPhase === SCAN_PHASES.RESULT || previousPhase === SCAN_PHASES.ERROR
          ? previousPhase
          : null,
      );
      traceScanEvent('welcome screen restored', cycleId);
    }, getScreenTransitionDuration());
  }

  function scheduleAutoReset(delay = ERROR_RESET_DELAY_MS) {
    if (autoResetTimerRef.current !== null) {
      clearTimeout(autoResetTimerRef.current);
    }
    autoResetTimerRef.current = setTimeout(() => {
      autoResetTimerRef.current = null;
      if (scanPhaseRef.current !== SCAN_PHASES.ERROR) {
        traceScanEvent('scan reset skipped', scanOperationIdRef.current, {
          reason: 'error reset timer fired outside error phase',
          phase: scanPhaseRef.current,
        });
        return;
      }
      resetToWelcome();
    }, delay);
  }

  async function beginScanTransition() {
    const transitionBlockedBy = [];
    if (!mountedRef.current) transitionBlockedBy.push('component_unmounted');
    if (getTTState() !== TT_STATES.IDLE) transitionBlockedBy.push(`kiosk_state_${getTTState()}`);
    if (screenTransitionRef.current !== 'idle') transitionBlockedBy.push(`transition_${screenTransitionRef.current}`);
    if (transitionBlockedBy.length) {
      updateScanDebugReason(`capture blocked: ${transitionBlockedBy.join(', ')}`);
      traceCameraTiming('scan transition blocked', { blockedBy: transitionBlockedBy });
      return;
    }

    if (!presenceCanvasRef.current) presenceCanvasRef.current = document.createElement('canvas');
    const idleReference = cameraBackgroundFrameRef.current ||
      samplePresenceReference(idleVideoRef.current, presenceCanvasRef.current);
    scanBackgroundFrameRef.current = idleReference ? new Uint8Array(idleReference) : null;
    updateScanDebugReason(scanBackgroundFrameRef.current
      ? 'waiting for object: scene baseline ready'
      : 'waiting for camera: scene baseline unavailable');
    traceCameraTiming('idle scene reference captured for scan', {
      available: Boolean(scanBackgroundFrameRef.current),
      source: cameraBackgroundFrameRef.current ? 'idle-camera-background' : 'current-idle-frame',
    });

    screenTransitionRef.current = 'preparing';
    setScreenTransition('preparing');
    traceCameraTiming('scan transition preparation started', { transitionMs: getScreenTransitionDuration() });
    await new Promise((resolve) => requestAnimationFrame(resolve));
    if (!mountedRef.current || screenTransitionRef.current !== 'preparing') {
      const reason = !mountedRef.current ? 'component_unmounted' : `transition_${screenTransitionRef.current}`;
      updateScanDebugReason(`capture blocked: scan transition cancelled (${reason})`);
      traceCameraTiming('scan transition cancelled', { stage: 'before_camera_attach', reason });
      return;
    }

    const stream = globalStreamRef.current;
    if (hasLiveVideoTrack(stream) && videoRef.current) {
      try {
        await attachCameraVideo(videoRef.current, stream);
        await waitForVideoFrame(videoRef.current, 800);
      } catch (playError) {
        logCamera('camera initialization failed', { phase: 'scan transition playback', message: playError?.message });
      }
    }
    if (!mountedRef.current || screenTransitionRef.current !== 'preparing') {
      const reason = !mountedRef.current ? 'component_unmounted' : `transition_${screenTransitionRef.current}`;
      updateScanDebugReason(`capture blocked: scan transition cancelled (${reason})`);
      traceCameraTiming('scan transition cancelled', { stage: 'after_camera_attach', reason });
      return;
    }

    screenTransitionRef.current = 'entering';
    presenceSinceRef.current = null;
    setScreenTransition('entering');
    setStatus('กำลังเตรียมภาพ…');
    traceScanEvent('welcome to scan transition started', scanOperationIdRef.current + 1);

    // Start camera qualification as the visual transition begins; the existing
    // scene, exposure, sharpness, and stable-frame gates still decide capture.
    setScanCycle((previousCycle) => previousCycle + 1);
    setTTState(TT_STATES.SCAN);
    setTTStateLocal(TT_STATES.SCAN);
    traceScanEvent('scan state entered during welcome transition', scanOperationIdRef.current + 1);

    screenTransitionTimerRef.current = setTimeout(() => {
      screenTransitionTimerRef.current = null;
      if (!mountedRef.current) return;
      screenTransitionRef.current = 'idle';
      setScreenTransition('idle');
      traceScanEvent('welcome to scan transition completed', scanOperationIdRef.current + 1);
    }, getScreenTransitionDuration());
  }
  beginScanTransitionRef.current = beginScanTransition;

  // RESET/CLEAR state when ttState changes to IDLE or SCAN
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) {
      // reset ทุกอย่างให้พร้อมเริ่มใหม่
      cancelActiveScan();
      resetCaptureGuards();
      setStatus('วางขยะลงหน้ากล้องเพื่อเริ่มสแกน');
      setSubStatus('');
      transitionScanPhase(SCAN_PHASES.SCANNING);
    }

    if (ttState === TT_STATES.SCAN) {
      // เข้าสแกนใหม่ ต้องเป็น live camera เสมอ
      setSubStatus('');
      if (!activeScanRef.current) {
        resetCaptureGuards({ preserveScanBackgroundFrame: true });
        transitionScanPhase(SCAN_PHASES.SCANNING);
      }
    }
  }, [ttState]);

  useEffect(() => {
    mountedRef.current = true;
    traceScanEvent('scan component mounted', scanOperationIdRef.current + 1);
    const persistedState = getTTState();
    // A SCAN state cannot be resumed safely after a page remount: the prior
    // empty-scene reference and active capture refs are memory-only.
    if (persistedState !== TT_STATES.IDLE) {
      traceScanEvent('stale kiosk state reset on mount', scanOperationIdRef.current + 1, {
        persistedState,
        nextState: TT_STATES.IDLE,
        reason: 'camera scene reference is not persisted',
      });
    }
    setTTState(TT_STATES.IDLE);
    setTTStateLocal(TT_STATES.IDLE);
    updateScanDebugReason('waiting for object: present waste in the scan area');
    transitionScanPhase(SCAN_PHASES.SCANNING);

    return () => {
      traceScanEvent('scan component unmounted', activeScanRef.current?.id || scanOperationIdRef.current, {
        activeRequest: Boolean(activeScanRef.current?.requestStarted && !activeScanRef.current?.requestCompleted),
      });
      mountedRef.current = false;
      if (autoResetTimerRef.current !== null) {
        clearTimeout(autoResetTimerRef.current);
        autoResetTimerRef.current = null;
      }
      if (cameraRetryTimerRef.current !== null) {
        clearTimeout(cameraRetryTimerRef.current);
        cameraRetryTimerRef.current = null;
      }
      if (screenTransitionTimerRef.current !== null) {
        clearTimeout(screenTransitionTimerRef.current);
        screenTransitionTimerRef.current = null;
      }
      if (cameraInitRetryTimerRef.current !== null) {
        clearTimeout(cameraInitRetryTimerRef.current);
        cameraInitRetryTimerRef.current = null;
      }
      cancelActiveScan();
      globalStreamRef.current?.getTracks?.().forEach((track) => track.stop());
    };
  }, []);

  function showScanError(message, activeScan = null, resetDelay = ERROR_RESET_DELAY_MS) {
    if (activeScan && !isActiveScan(activeScan)) {
      updateScanDebugReason('capture blocked: error result belongs to an inactive scan');
      traceScanEvent('scan error handling blocked', activeScan.id, { reason: 'inactive_scan' });
      return;
    }
    if (activeScan?.requestStarted && !activeScan.requestCompleted) {
      traceScanEvent('scan error reset deferred', activeScan.id, { reason: 'prediction request still in flight' });
      return;
    }

    transitionScanPhase(SCAN_PHASES.ERROR);
    setStatus('วิเคราะห์ไม่สำเร็จ');
    setSubStatus(message);
    setCountdown(Math.ceil(resetDelay / 1000));
    scheduleAutoReset(resetDelay);
  }

  // Note: Removed 'Enter' key mapping for Kiosk auto-triggering

  // Global Camera Init (Always On)
  useEffect(() => {
    async function initGlobalCamera() {
      if (cameraInitInFlightRef.current) return cameraInitInFlightRef.current;
      if (hasLiveVideoTrack(globalStreamRef.current)) {
        logCamera('camera stream reused or reinitialized', { mode: 'reused' });
        traceCameraTiming('camera stream ready', { source: 'reused', elapsedMs: 0 });
        setCameraStatus('ready');
        return globalStreamRef.current;
      }

      setCameraStatus('initializing');
      setStatus('กำลังเปิดกล้อง…');
      cameraInitStartedAtRef.current = performance.now();
      logCamera('camera initialization started');

      const cameraRequest = (async () => {
        try {
          if (!navigator.mediaDevices?.getUserMedia) {
            throw new Error('Camera API is unavailable');
          }
          const devices = await navigator.mediaDevices.enumerateDevices();
          const videoDevices = devices.filter(d => d.kind === 'videoinput');

          let videoConstraints = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 } };
          const isMobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);

          if (isMobile) {
            videoConstraints.facingMode = { ideal: 'environment' };
          } else {
            const externalCam = videoDevices.find(d => !/facetime|integrated|built[- ]?in|macbook/i.test(d.label));
            if (externalCam?.deviceId) videoConstraints.deviceId = { exact: externalCam.deviceId };
            else {
              const builtInCam = videoDevices.find(d => /facetime|built[- ]?in|macbook|integrated/i.test(d.label)) || videoDevices[0];
              if (builtInCam?.deviceId) videoConstraints.deviceId = { exact: builtInCam.deviceId };
              else videoConstraints.facingMode = { ideal: 'user' };
            }
          }

          const stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints });
          if (mountedRef.current) {
            globalStreamRef.current = stream;
            setGlobalStream(stream);
            setCameraStatus('ready');
            updateScanDebugReason('waiting for object: camera stream is ready');
            cameraRetryAttemptRef.current = 0;
            cameraRetryAtRef.current = 0;
            if (cameraInitRetryTimerRef.current !== null) {
              clearTimeout(cameraInitRetryTimerRef.current);
              cameraInitRetryTimerRef.current = null;
            }
            const streamReadyElapsedMs = cameraInitStartedAtRef.current === null
              ? null
              : Math.round(performance.now() - cameraInitStartedAtRef.current);
            logCamera('camera stream ready', { tracks: stream.getVideoTracks().length, elapsedMs: streamReadyElapsedMs });
            traceCameraTiming('camera stream ready', {
              tracks: stream.getVideoTracks().length,
              liveVideoTrack: hasLiveVideoTrack(stream),
              elapsedMs: streamReadyElapsedMs,
            });
            traceScanEvent('camera ready', scanOperationIdRef.current + 1, {
              trackCount: stream.getVideoTracks().length,
              mockClassification: USE_MOCK_CLASSIFICATION,
            });
            logCamera('camera stream reused or reinitialized', { mode: 'reinitialized' });
          } else {
            stream.getTracks().forEach((track) => track.stop());
          }
          return stream;
        } catch (e) {
          logCamera('camera initialization failed', { name: e?.name || 'Error', message: e?.message });
          if (mountedRef.current) {
            setCameraStatus('error');
            updateScanDebugReason(`waiting for camera: initialization failed (${e?.name || 'Error'})`);
            setStatus('ไม่สามารถเปิดกล้องได้');
            setSubStatus('กำลังลองเปิดกล้องใหม่…');
            scheduleCameraRetry();
          }
          return null;
        }
      })();

      cameraInitInFlightRef.current = cameraRequest;
      try {
        return await cameraRequest;
      } finally {
        if (cameraInitInFlightRef.current === cameraRequest) {
          cameraInitInFlightRef.current = null;
        }
      }
    }

    cameraStartRef.current = initGlobalCamera;
    initGlobalCamera();

    return () => {
      if (cameraStartRef.current === initGlobalCamera) cameraStartRef.current = null;
    };
  }, []);

  // Bind stream to invisible IDLE video for detection
  useEffect(() => {
    if (idleVideoRef.current && globalStream) {
      attachCameraVideo(idleVideoRef.current, globalStream).then((started) => {
        if (started) logCamera('video playback started', { target: 'idle' });
      }).catch((playError) => {
        logCamera('camera initialization failed', { phase: 'idle playback', message: playError?.message });
      });
    }
  }, [globalStream]);

  useEffect(() => {
    const onVisibilityChange = () => {
      const visible = document.visibilityState === 'visible';
      traceCameraTiming('browser visibility changed', { visibilityState: document.visibilityState });
      if (!visible) {
        updateScanDebugReason('waiting for camera: browser tab is hidden');
        return;
      }

      const video = ttState === TT_STATES.SCAN ? videoRef.current : idleVideoRef.current;
      const stream = globalStreamRef.current || globalStream;
      if (!hasLiveVideoTrack(stream)) {
        updateScanDebugReason('waiting for camera: no live video track after tab became visible');
        return;
      }
      attachCameraVideo(video, stream).then(() => {
        traceCameraTiming('camera playback resumed after visibility change', {
          readyState: video?.readyState ?? null,
          videoWidth: video?.videoWidth ?? null,
          videoHeight: video?.videoHeight ?? null,
        });
      }).catch((error) => {
        updateScanDebugReason(`waiting for camera: playback resume failed (${error?.name || 'Error'})`);
        traceCameraTiming('camera playback resume failed', { errorName: error?.name || 'Error' });
      });
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, [ttState, globalStream]);

  // Motion Detection Loop (IDLE -> SCAN)
  useEffect(() => {
    if (ttState !== TT_STATES.IDLE || !globalStream || screenTransitionRef.current !== 'idle') return;

    lastIdleFrameRef.current = null;
    cameraBackgroundFrameRef.current = null;
    presenceSinceRef.current = null;

    const interval = setInterval(() => {
      if (screenTransitionRef.current !== 'idle') return;
      const video = idleVideoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || video.videoWidth === 0) return;

      canvas.width = 64; 
      canvas.height = 64; 
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(video, 0, 0, 64, 64);
      
      const currentData = ctx.getImageData(0,0,64,64).data;
      
      if (!lastIdleFrameRef.current) {
        lastIdleFrameRef.current = new Uint8ClampedArray(currentData);
        if (!presenceCanvasRef.current) presenceCanvasRef.current = document.createElement('canvas');
        cameraBackgroundFrameRef.current = samplePresenceReference(video, presenceCanvasRef.current);
        return;
      }

      let diffSum = 0;
      for (let i = 0; i < currentData.length; i += 4) {
        diffSum += Math.abs(currentData[i] - lastIdleFrameRef.current[i]);
        diffSum += Math.abs(currentData[i + 1] - lastIdleFrameRef.current[i + 1]);
        diffSum += Math.abs(currentData[i + 2] - lastIdleFrameRef.current[i + 2]);
      }

      const avgDiff = diffSum / (64 * 64 * 3);
      if (avgDiff > 8) {
        if (presenceSinceRef.current === null) {
          presenceSinceRef.current = Date.now();
          updateScanDebugReason(`motion detected: confirming for ${PRESENCE_STABLE_MS} ms`);
          traceCameraTiming('idle motion detected', {
            averageFrameDifference: Number(avgDiff.toFixed(2)),
            debounceMs: PRESENCE_STABLE_MS,
            videoReadyState: video.readyState,
            videoWidth: video.videoWidth,
            videoHeight: video.videoHeight,
          });
          traceCameraTiming('idle presence debounce started', { debounceMs: PRESENCE_STABLE_MS });
        }
        if (Date.now() - presenceSinceRef.current >= PRESENCE_STABLE_MS) {
          updateScanDebugReason('object trigger fired: entering scan');
          traceScanEvent('idle object-present detected', scanOperationIdRef.current + 1, {
            averageFrameDifference: Number(avgDiff.toFixed(2)),
            stableForMs: Date.now() - presenceSinceRef.current,
            kioskState: getTTState(),
            scanPhase: scanPhaseRef.current,
            liveVideoTrack: hasLiveVideoTrack(globalStreamRef.current || globalStream),
          });
          beginScanTransitionRef.current?.();
        }
        return;
      }

      presenceSinceRef.current = null;
      lastIdleFrameRef.current = new Uint8ClampedArray(currentData);
      updateScanDebugReason('waiting for object: no sustained scene change');
    }, 150);

    return () => clearInterval(interval);
  }, [ttState, globalStream]);

  // The visible countdown never controls an in-flight request. Its deadline
  // starts only after the request reaches a terminal result or error.
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) return undefined;
    if (ttState === TT_STATES.SCAN && scanPhase === SCAN_PHASES.ERROR) return undefined;

    const isActiveScanPhase = ttState === TT_STATES.SCAN &&
      (scanPhase === SCAN_PHASES.CAPTURING || scanPhase === SCAN_PHASES.ANALYZING);
    const limit = ttState === TT_STATES.RESULT
      ? RESULT_RESET_SECONDS
      : isActiveScanPhase
        ? Math.ceil(PREDICT_TIMEOUT_MS / 1000)
        : SCAN_RESET_SECONDS;
    setCountdown(limit);

    // Reset pagination when entering RESULT state
    if (ttState === TT_STATES.RESULT) {
      setCurrentResultIndex(0);
      currentResultIndexRef.current = 0;
    }

    const deadline = Date.now() + limit * 1000;
    const interval = setInterval(() => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setCountdown(remaining);
      if (remaining === 0) {
        clearInterval(interval);
        if (ttState === TT_STATES.RESULT) {
          resetToWelcome();
        }
      }
    }, 1000);

    // Auto-rotate the result carousel every 4 seconds if there are multiple items
    let carouselInterval;
    if (ttState === TT_STATES.RESULT) {
      carouselInterval = setInterval(() => {
        setCurrentResultIndex(prev => {
          const next = prev + 1;
          currentResultIndexRef.current = next;
          return next;
        });
      }, 4000);
    }

    return () => {
      clearInterval(interval);
      if (carouselInterval) clearInterval(carouselInterval);
    };
  }, [ttState, resultData, scanPhase]);

  async function captureAndScan({ trigger = 'automatic_scene_detection' } = {}) {
    const video = videoRef.current;
    const stream = globalStreamRef.current || globalStream;
    const attemptSnapshot = {
      kioskState: getTTState(),
      reactKioskState: ttState,
      scanPhase: scanPhaseRef.current,
      liveVideoTrack: hasLiveVideoTrack(stream),
      objectTrigger: trigger,
      videoReadyState: video?.readyState ?? null,
      videoWidth: video?.videoWidth ?? null,
      videoHeight: video?.videoHeight ?? null,
      pageVisible: document.visibilityState === 'visible',
      activeScan: Boolean(activeScanRef.current),
      scanningGuard: scanningRef.current,
    };
    traceScanEvent('capture attempt', scanOperationIdRef.current + 1, attemptSnapshot);

    const blockedBy = [];
    if (!mountedRef.current) {
      blockedBy.push('component_unmounted');
    } else if (getTTState() !== TT_STATES.SCAN) {
      blockedBy.push('tt_state_not_scan');
    }
    if (scanPhaseRef.current !== SCAN_PHASES.SCANNING) blockedBy.push(`phase_${scanPhaseRef.current}`);
    if (scanningRef.current) blockedBy.push('already_scanning');
    if (activeScanRef.current) blockedBy.push('active_scan_exists');
    if (blockedBy.length) {
      updateScanDebugReason(`capture blocked: ${blockedBy.join(', ')}`);
      traceScanEvent('capture trigger blocked', scanOperationIdRef.current + 1, {
        ...attemptSnapshot,
        blockedBy,
      });
      return;
    }

    scanningRef.current = true;
    const controller = new AbortController();
    const activeScan = {
      id: scanOperationIdRef.current + 1,
      controller,
      requestStarted: false,
      requestCompleted: false,
      cancelled: false,
    };
    scanOperationIdRef.current = activeScan.id;
    activeScanRef.current = activeScan;
    updateScanDebugReason('capture allowed: drawing camera frame');
    traceScanEvent('capture started', activeScan.id, {
      ...attemptSnapshot,
      phase: scanPhaseRef.current,
      mockClassification: USE_MOCK_CLASSIFICATION,
    });
    transitionScanPhase(SCAN_PHASES.CAPTURING);

    const applyDetectionResult = (detectionResult) => {
      if (!isActiveScan(activeScan)) {
        updateScanDebugReason('capture blocked: result belongs to an inactive scan');
        traceScanEvent('capture result blocked', activeScan.id, { reason: 'inactive_scan' });
        return;
      }
      const normalizedResult = normalizeClassificationResponse(detectionResult);
      if (normalizedResult?.status === 'no_waste_detected' || normalizedResult?.no_waste_detected) {
        showScanError(
          normalizedResult.retry_message || 'ไม่พบวัตถุสำหรับคัดแยกในภาพ',
          activeScan,
        );
        return;
      }
      if (normalizedResult?.status === 'api_error' || normalizedResult?.valid_prediction === false) {
        const apiError = new Error('Prediction failed');
        apiError.responseStatus = 'api_error';
        throw apiError;
      }
      if (!normalizedResult?.prediction?.wasteType) {
        throw new Error('ไม่พบประเภทขยะที่รองรับ');
      }
      console.info(
        normalizedResult.status === 'unknown'
          ? '[Classification] unknown; using configured Bin 1 fallback destination'
          : normalizedResult.status === 'missing_mapping'
            ? '[Classification] confirmed category has no configured destination'
          : '[Classification] confirmed waste category',
        {
          classificationStatus: normalizedResult.classification_status,
          wasteType: normalizedResult.prediction.wasteType,
          destinationSource: normalizedResult.destination_source,
          locationId: normalizedResult.location_id,
          binNumber: normalizedResult.bin_number,
          mappingVersion: normalizedResult.mapping_version,
        },
      );
      const rawCategory = detectionResult?.rawCategory ??
        detectionResult?.mappingDecision?.rawCategory ??
        detectionResult?.prediction?.waste_category ??
        detectionResult?.prediction?.wasteType ?? null;
      console.info('[WasteMapping] scan result', {
        requestId: normalizedResult.request_id || detectionResult?.request_id || null,
        rawCategory,
        normalizedWasteType: normalizedResult.prediction.wasteType,
        binNumber: normalizedResult.bin_number,
        binName: normalizedResult.bin_name,
        mappingFallback: normalizedResult.mapping_fallback === true,
        resetCycleId: scanCycle,
      });
      setSubStatus('');
      setCapturedImage('');
      setCapturedImageLoaded(false);
      sessionStorage.setItem('tt:last', JSON.stringify(normalizedResult));
      setResultData(normalizedResult);
      transitionScanPhase(SCAN_PHASES.RESULT);
      setTTState(TT_STATES.RESULT);
      setTTStateLocal(TT_STATES.RESULT);
    };

    const getNextMockPrediction = () => {
      const next = mockWasteResults[mockResultIndexRef.current % mockWasteResults.length];
      mockResultIndexRef.current += 1;
      if (next?.status) return next;
      const wasteType = next.waste_category || next.wasteType || WASTE_TYPE.UNKNOWN;
      const objectNameTh = next.object_name_th || next.objectNameTh || next.itemNameTh || '';
      const hasFoodScraps = next.has_food_scraps === true || next.hasFoodScraps === true;

      return {
        ...next,
        valid_prediction: true,
        prediction: {
          waste_category: wasteType,
          object_name_th: objectNameTh,
          has_food_scraps: hasFoodScraps,
          wasteType,
          itemNameTh: objectNameTh,
          hasFoodScraps,
          confidence: next.confidence,
        },
        model: { name: 'mock-classifier', version: 'local-demo' },
      };
    };

    try {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || !video.videoWidth) {
        const reason = !video
          ? 'video_element_missing'
          : !canvas
            ? 'canvas_element_missing'
            : 'video_dimensions_unavailable';
        updateScanDebugReason(`capture blocked: ${reason}`);
        traceScanEvent('canvas capture blocked', activeScan.id, {
          reason,
          kioskState: getTTState(),
          scanPhase: scanPhaseRef.current,
          liveVideoTrack: hasLiveVideoTrack(globalStreamRef.current || globalStream),
          videoReadyState: video?.readyState ?? null,
          videoWidth: video?.videoWidth ?? null,
          videoHeight: video?.videoHeight ?? null,
        });
        const captureError = new Error('กล้องยังไม่พร้อม');
        captureError.scanMessage = 'ไม่สามารถอ่านภาพจากกล้องได้';
        throw captureError;
      }

      setSubStatus('กำลังบันทึกภาพ...');
      traceScanEvent('canvas capture started', activeScan.id, {
        width: video.videoWidth,
        height: video.videoHeight,
      });
      const canvasCaptureStartedAt = performance.now();
      drawScaledFromVideo(video, canvas);
      traceScanEvent('canvas frame drawn', activeScan.id, {
        canvasWidth: canvas.width,
        canvasHeight: canvas.height,
        videoWidth: video.videoWidth,
        videoHeight: video.videoHeight,
      });
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      let brightness = checkFrameBrightness(ctx, canvas.width, canvas.height);
      let warmupAttempts = 0;
      while (!brightness.ok && !cameraWarmedRef.current && warmupAttempts < 8) {
        if (!isActiveScan(activeScan)) {
          updateScanDebugReason('capture blocked: scan cancelled before brightness retry');
          traceScanEvent('capture early return', activeScan.id, { reason: 'inactive_scan_before_brightness_retry' });
          return;
        }
        traceScanEvent('brightness retry started', activeScan.id, {
          attempt: warmupAttempts + 1,
          meanBrightness: brightness.meanBrightness,
          retryDelayMs: 250,
        });
        setStatus('กำลังปรับภาพจากกล้อง…');
        setSubStatus('');
        await new Promise((resolve) => setTimeout(resolve, 250));
        if (!isActiveScan(activeScan)) {
          updateScanDebugReason('capture blocked: scan cancelled during brightness retry');
          traceScanEvent('capture early return', activeScan.id, { reason: 'inactive_scan_after_brightness_retry' });
          return;
        }
        drawScaledFromVideo(video, canvas);
        brightness = checkFrameBrightness(ctx, canvas.width, canvas.height);
        warmupAttempts += 1;
      }
      if (!brightness.ok) {
        updateScanDebugReason(`capture blocked: frame brightness ${brightness.meanBrightness} outside 30–225`);
        traceScanEvent('canvas capture blocked', activeScan.id, {
          reason: 'frame_brightness_out_of_range',
          meanBrightness: brightness.meanBrightness,
          retryAttempts: warmupAttempts,
        });
        console.warn(`[SCAN] Frame brightness out of range (${brightness.meanBrightness})`);
        const brightnessError = new Error('ภาพมืดหรือสว่างเกินไป');
        brightnessError.scanMessage = brightnessError.message;
        throw brightnessError;
      }
      cameraWarmedRef.current = true;
      setStatus('วางขยะลงได้เลย');
      setSubStatus('');
      const imageBase64 = canvas.toDataURL('image/jpeg', 0.88);
      traceScanEvent('preview JPEG created', activeScan.id, {
        elapsedMs: Math.round(performance.now() - canvasCaptureStartedAt),
        byteLengthApprox: imageBase64.length,
      });
      capturedImageRef.current = imageBase64;
      setCapturedImage(imageBase64);
      setCapturedImageLoaded(false);
      traceScanEvent('capture completed', activeScan.id, {
        width: canvas.width,
        height: canvas.height,
        base64DataUrl: imageBase64.startsWith('data:image/jpeg;base64,'),
        base64Length: imageBase64.length,
      });

      setSubStatus('จับภาพเรียบร้อย');

      // Keep the existing frame-quality telemetry for the camera flow.
      if (isFrameEmpty(ctx, canvas.width, canvas.height, 2.5)) {
        console.warn('[SCAN] Frame quality is weak; sending it to the backend for validation.');
        sessionStorage.setItem('tt:emptyHash', '1');
      } else {
        const emptyHash = simpleHash(
          ctx.getImageData(0, 0, canvas.width, canvas.height).data.slice(0, 2000).toString()
        );
      }

      transitionScanPhase(SCAN_PHASES.ANALYZING);
      setStatus('กำลังวิเคราะห์ อาจใช้เวลาสักครู่');
      setSubStatus('');

      if (USE_MOCK_CLASSIFICATION) {
        updateScanDebugReason('capture blocked: mock classification enabled; no network request');
        traceScanEvent('prediction request skipped', activeScan.id, { reason: 'mock_classification_enabled' });
        await new Promise(r => setTimeout(r, 500));
        if (!isActiveScan(activeScan)) {
          updateScanDebugReason('capture blocked: mock scan cancelled before result');
          traceScanEvent('capture early return', activeScan.id, { reason: 'inactive_scan_before_mock_result' });
          return;
        }
        applyDetectionResult(getNextMockPrediction());
        return;
      }

      updateScanDebugReason('encoding captured image');
      const blobStartedAt = performance.now();
      traceScanEvent('canvas toBlob started', activeScan.id, {
        canvasWidth: canvas.width,
        canvasHeight: canvas.height,
      });
      const imageBlob = await new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
          if (blob) {
            traceScanEvent('canvas toBlob resolved', activeScan.id, {
              mimeType: blob.type,
              byteLength: blob.size,
              elapsedMs: Math.round(performance.now() - blobStartedAt),
            });
            resolve(blob);
          } else {
            updateScanDebugReason('capture blocked: canvas.toBlob returned no image');
            traceScanEvent('canvas toBlob failed', activeScan.id, {
              canvasWidth: canvas.width,
              canvasHeight: canvas.height,
              reason: 'empty_blob',
            });
            reject(new Error('Image encoding failed'));
          }
        }, 'image/jpeg', 0.88);
      });
      if (!isActiveScan(activeScan)) {
        updateScanDebugReason('capture blocked: scan cancelled after JPEG encoding');
        traceScanEvent('capture early return', activeScan.id, { reason: 'inactive_scan_after_toBlob' });
        return;
      }
      traceScanEvent('JPEG blob created', activeScan.id, {
        mimeType: imageBlob.type,
        byteLength: imageBlob.size,
      });

      let res;
      let parsed;
      activeScan.requestStarted = true;
      const requestStartedAt = Date.now();
      updateScanDebugReason('POST /api/classify sent');
      traceScanEvent('prediction request triggered', activeScan.id, {
        url: '/api/classify',
        method: 'POST',
        timeoutMs: PREDICT_TIMEOUT_MS,
        mockClassification: USE_MOCK_CLASSIFICATION,
      });
      try {
        ({ response: res, payload: parsed } = await requestPrediction({
          imageBlob,
          controller: activeScan.controller,
          cycleId: activeScan.id,
          resetCycleId: scanCycle,
          onStart: () => {
            updateScanDebugReason('POST /api/classify sent');
            traceScanEvent('prediction request sent', activeScan.id, {
              url: '/api/classify',
              kioskState: getTTState(),
              scanPhase: scanPhaseRef.current,
              liveVideoTrack: hasLiveVideoTrack(globalStreamRef.current || globalStream),
            });
          },
          onResponse: (statusCode, elapsedMs) => {
            traceScanEvent('prediction response received', activeScan.id, { status: statusCode, elapsedMs });
          },
        }));
      } finally {
        activeScan.requestCompleted = true;
        traceScanEvent('prediction request settled', activeScan.id, {
          elapsedMs: Date.now() - requestStartedAt,
          cancelled: activeScan.cancelled,
          mounted: mountedRef.current,
        });
      }

      if (!isActiveScan(activeScan)) {
        updateScanDebugReason('capture blocked: scan cancelled after prediction request');
        traceScanEvent('capture early return', activeScan.id, { reason: 'inactive_scan_after_prediction_request' });
        return;
      }
      const predictionFailure = getPredictionFailure(res, parsed);
      if (predictionFailure) throw predictionFailure;

      // The backend validates the complete model JSON and exactly three top
      // candidates before returning any mappable prediction.
      let normalized = parsed;
      try {
        // unwrap common wrappers
        if (normalized?.result) normalized = normalized.result;

        // parse raw JSON string if present
        if (normalized?.raw && typeof normalized.raw === 'string') {
          const parsedRaw = JSON.parse(normalized.raw);
          normalized = parsedRaw;
        }
      } catch (_) {}

      // The UI consumes the backend mapping fields through the shared
      // normalizer; no category-to-bin mapping is performed here.
      applyDetectionResult(normalized);
    } catch (e) {
      if (!isActiveScan(activeScan)) {
        updateScanDebugReason('capture blocked: scan cancelled while handling capture result');
        traceScanEvent('capture early return', activeScan.id, {
          reason: 'inactive_scan_in_error_handler',
          name: e?.name || 'Error',
        });
        return;
      }
      traceScanEvent(activeScan.requestStarted ? 'prediction request failed' : 'capture failed before prediction request', activeScan.id, {
        name: e?.name || 'Error',
        message: e?.message || 'Scan failed',
      });
      updateScanDebugReason(activeScan.requestStarted
        ? `request failed: ${e?.name || 'Error'}`
        : `capture blocked: ${e?.scanMessage || e?.message || e?.name || 'unknown capture error'}`);

      const requestErrorResetDelay = activeScan.requestStarted
        ? PREDICTION_ERROR_RESET_MS
        : ERROR_RESET_DELAY_MS;
      if (e.name === 'TimeoutError') {
        console.warn('[SCAN] Detection timed out.');
        showScanError('ระบบวิเคราะห์ใช้เวลานานเกินไป', activeScan, requestErrorResetDelay);
      } else if (e.name === 'AbortError') {
        showScanError('การวิเคราะห์หยุดก่อนเสร็จ', activeScan, requestErrorResetDelay);
      } else if (e.responseStatus === 'api_error') {
        console.error('backend API error', e);
        showScanError('ระบบวิเคราะห์ขัดข้อง', activeScan, requestErrorResetDelay);
      } else {
        console.error('scan error', e);
        showScanError(
          activeScan.requestStarted ? 'เชื่อมต่อระบบวิเคราะห์ไม่ได้' : (e.scanMessage || 'ไม่สามารถอ่านภาพจากกล้องได้'),
          activeScan,
          requestErrorResetDelay,
        );
      }
    } finally {
      activeScan.requestCompleted = true;
      traceScanEvent('capture attempt finished', activeScan.id, {
        kioskState: getTTState(),
        reactKioskState: ttState,
        scanPhase: scanPhaseRef.current,
        liveVideoTrack: hasLiveVideoTrack(globalStreamRef.current || globalStream),
        requestStarted: activeScan.requestStarted,
        requestCompleted: activeScan.requestCompleted,
        controllerAborted: activeScan.controller.signal.aborted,
      });
      if (activeScanRef.current === activeScan) {
        activeScanRef.current = null;
        scanningRef.current = false;
      }
    }
  }

  // SCAN State: Bind stream to visible UI and Auto-Capture
  useEffect(() => {
    if (ttState !== TT_STATES.SCAN) return undefined;

    let cancelled = false;
    let retryTimer = null;

    const retryUntilVideoReady = () => {
      if (cancelled || !mountedRef.current || getTTState() !== TT_STATES.SCAN) {
        traceCameraTiming('capture retry blocked', {
          reason: cancelled
            ? 'scan_effect_cancelled'
            : !mountedRef.current
              ? 'component_unmounted'
              : `kiosk_state_${getTTState()}`,
        });
        return;
      }
      const untilRetry = Math.max(0, cameraRetryAtRef.current - Date.now());
      const retryDelayMs = Math.max(CAPTURE_RETRY_DELAY_MS, untilRetry);
      traceCameraTiming('capture preparation retry scheduled', {
        retryDelayMs,
        cameraRetryCooldownRemainingMs: untilRetry,
      });
      retryTimer = setTimeout(prepareCapture, retryDelayMs);
      cameraRetryTimerRef.current = retryTimer;
    };

    const prepareCapture = async () => {
      if (cancelled || !mountedRef.current || getTTState() !== TT_STATES.SCAN || scanningRef.current) {
        const blockedReason = cancelled
          ? 'scan_effect_cancelled'
          : !mountedRef.current
            ? 'component_unmounted'
            : getTTState() !== TT_STATES.SCAN
              ? `kiosk_state_${getTTState()}`
              : 'already_scanning';
        updateScanDebugReason(`capture blocked: ${blockedReason}`);
        traceCameraTiming('capture preparation blocked', {
          reason: cancelled
            ? 'scan_effect_cancelled'
            : !mountedRef.current
              ? 'component_unmounted'
              : getTTState() !== TT_STATES.SCAN
                ? `kiosk_state_${getTTState()}`
                : 'already_scanning',
          cancelled,
          mounted: mountedRef.current,
          kioskState: getTTState(),
          scanInProgress: scanningRef.current,
        });
        return;
      }

      const video = videoRef.current;
      setStatus('กำลังตรวจจับขยะ...');
      setSubStatus('ถือขยะให้นิ่งในบริเวณกล้อง');
      updateScanDebugReason('waiting for camera: checking live stream and video element');
      traceCameraTiming('capture preparation started', {
        hasVideo: Boolean(video),
        cameraWarmed: cameraWarmedRef.current,
        hasLiveStream: hasLiveVideoTrack(globalStreamRef.current || globalStream),
      });
      if (!video) {
        updateScanDebugReason('waiting for camera: video element not mounted');
        retryUntilVideoReady();
        return;
      }

      let stream = globalStreamRef.current || globalStream;
      if (!hasLiveVideoTrack(stream)) {
        updateScanDebugReason('waiting for camera: no live video track');
        setCameraStatus('initializing');
        setStatus('กำลังเปิดกล้อง…');
        const waitForRetry = cameraRetryAtRef.current - Date.now();
        if (waitForRetry > 0) {
          updateScanDebugReason(`waiting for camera: retry cooldown ${waitForRetry} ms`);
          retryUntilVideoReady();
          return;
        }
        try {
          const initialized = await getLiveCameraStream(stream, async () => {
            const nextStream = await cameraStartRef.current?.();
            if (!nextStream) throw new Error('Camera initialization returned no stream');
            return nextStream;
          });
          stream = initialized.stream;
          globalStreamRef.current = stream;
          if (!initialized.reused) cameraWarmedRef.current = false;
          if (initialized.reused) {
            logCamera('camera stream reused or reinitialized', { mode: 'reused', cycle: scanCycle });
          }
        } catch (error) {
          updateScanDebugReason(`waiting for camera: stream start failed (${error?.name || 'Error'})`);
          traceCameraTiming('camera stream unavailable for capture', {
            errorName: error?.name || 'Error',
            message: error?.message || 'stream start failed',
          });
          retryUntilVideoReady();
          return;
        }
      }

      if (hasLiveVideoTrack(stream)) {
        logCamera('camera stream reused or reinitialized', { mode: 'reused', cycle: scanCycle });
      }
      try {
        if (await attachCameraVideo(video, stream)) {
          logCamera('video playback started', { target: 'scan' });
        }
      } catch (playError) {
        console.warn('[Camera] video.play() is not ready yet', playError);
      }

      const videoFrameWaitStartedAt = performance.now();
      updateScanDebugReason('waiting for camera: waiting for a fresh video frame');
      if (!(await waitForVideoFrame(video))) {
        updateScanDebugReason('waiting for camera: no fresh video frame');
        traceCameraTiming('video frame wait timed out', {
          elapsedMs: Math.round(performance.now() - videoFrameWaitStartedAt),
          timeoutMs: 1_500,
        });
        retryUntilVideoReady();
        return;
      }
      traceCameraTiming('video frame ready', {
        elapsedMs: Math.round(performance.now() - videoFrameWaitStartedAt),
        width: video.videoWidth,
        height: video.videoHeight,
      });

      if (
        cancelled ||
        !mountedRef.current ||
        getTTState() !== TT_STATES.SCAN ||
        !video.videoWidth
      ) {
        const reason = cancelled
          ? 'scan_effect_cancelled'
          : !mountedRef.current
            ? 'component_unmounted'
            : getTTState() !== TT_STATES.SCAN
              ? `kiosk_state_${getTTState()}`
              : 'video_dimensions_unavailable';
        updateScanDebugReason(`capture blocked: ${reason}`);
        traceCameraTiming('capture preparation blocked after video frame', { reason });
        retryUntilVideoReady();
        return;
      }

      if (!scanBackgroundFrameRef.current) {
        updateScanDebugReason('waiting for object: capturing current scene baseline');
        if (!presenceCanvasRef.current) presenceCanvasRef.current = document.createElement('canvas');
        scanBackgroundFrameRef.current = samplePresenceReference(video, presenceCanvasRef.current);
        if (!scanBackgroundFrameRef.current) {
          updateScanDebugReason('waiting for camera: could not read scene baseline');
          retryUntilVideoReady();
          return;
        }
        traceCameraTiming('scan background reference captured', {
          containsExistingIdleReference: false,
          width: video.videoWidth,
          height: video.videoHeight,
        });
      }

      let lastPresenceState = '';
      updateScanDebugReason('waiting for object: present waste in the scan area');
      const readiness = await waitForStableCameraFrame({
        video,
        referenceFrame: scanBackgroundFrameRef.current,
        stableMs: CAMERA_FRAME_STABLE_MS,
        shouldCancel: () => cancelled || !mountedRef.current || getTTState() !== TT_STATES.SCAN,
        onProgress: ({
          event,
          state,
          changedPixelRatio,
          presenceThreshold,
          presencePassed,
          stabilityTimerStarted,
          stabilityTimerReset,
          stabilityElapsedMs,
          waitElapsedMs,
          elapsedMs,
        }) => {
          if (cancelled || !mountedRef.current) return;
          if (event === 'presence_check' && process.env.NODE_ENV === 'development') {
            const waitingReason = !presencePassed
              ? 'waiting_for_scene_change_threshold'
              : state === 'exposure'
                ? 'waiting_for_exposure'
                : state === 'unclear'
                  ? 'waiting_for_sharp_frame'
                  : state === 'moving'
                    ? 'waiting_for_stable_frame'
                    : 'waiting_for_stability_window';
            console.debug('[CameraPresence] presence check', {
              configuredChangeThreshold: presenceThreshold,
              changedPixelRatio: changedPixelRatio === null ? null : Number(changedPixelRatio.toFixed(4)),
              thresholdPassed: presencePassed,
              state,
              stabilityElapsedMs,
              stableWindowMs: CAMERA_FRAME_STABLE_MS,
              waitElapsedMs,
              waitingReason,
              kioskState: getTTState(),
              scanPhase: scanPhaseRef.current,
            });
          }
          if (event === 'object_present') {
            updateScanDebugReason('object detected: checking frame stability');
            traceCameraTiming('scan object-present detected', {
              elapsedMs,
              changedPixelRatio: changedPixelRatio === null ? null : Number(changedPixelRatio.toFixed(3)),
            });
          }
          if (stabilityTimerStarted) {
            updateScanDebugReason(`waiting for stable frame: ${CAMERA_FRAME_STABLE_MS} ms`);
            traceCameraTiming('camera stability timer started', {
              stableWindowMs: CAMERA_FRAME_STABLE_MS,
              changedPixelRatio: changedPixelRatio === null ? null : Number(changedPixelRatio.toFixed(3)),
              waitElapsedMs,
            });
          }
          if (stabilityTimerReset) {
            updateScanDebugReason(`waiting for stable frame: reset after ${state}`);
            traceCameraTiming('camera stability timer reset', { state, waitElapsedMs });
          }
          if (state && state !== lastPresenceState) {
            traceCameraTiming('camera presence state changed', {
              state,
              waitElapsedMs,
              changedPixelRatio: changedPixelRatio === null ? null : Number(changedPixelRatio.toFixed(3)),
            });
            lastPresenceState = state;
          }
          if (state === 'stable') {
            updateScanDebugReason('waiting for stable frame: object is steady');
            setStatus('กำลังตรวจจับขยะ...');
            setSubStatus('กำลังรอภาพนิ่งครบเวลา…');
            return;
          }
          if (state === 'no_object') {
            updateScanDebugReason('waiting for object: no scene change detected');
            console.info('[CameraPresence] waiting for a scene change', {
              changedPixelRatio: changedPixelRatio === null ? null : Number(changedPixelRatio.toFixed(3)),
            });
            setStatus('กำลังรอวัตถุ');
            setSubStatus('ขยับหรือยื่นขยะเข้ามาในบริเวณกล้อง');
            return;
          }
          if (state === 'exposure') {
            updateScanDebugReason('waiting for camera: frame brightness outside allowed range');
            setStatus('ปรับแสงให้พอดี');
            setSubStatus('ขยับวัตถุให้อยู่ในบริเวณที่มีแสง');
            return;
          }
          if (state === 'unclear') {
            updateScanDebugReason('waiting for object: frame is too blurry');
            setStatus('ภาพยังไม่ชัด');
            setSubStatus('เช็ดเลนส์และขยับวัตถุให้เห็นชัดขึ้น');
            return;
          }
          if (state === 'moving') updateScanDebugReason('waiting for stable frame: object or camera is moving');
          setStatus('กำลังรอภาพนิ่ง');
          setSubStatus('จัดวัตถุให้อยู่กลางภาพและถือให้นิ่ง');
        },
      });

      if (readiness.cancelled || cancelled || !mountedRef.current) {
        updateScanDebugReason('capture blocked: camera readiness wait cancelled');
        traceCameraTiming('capture readiness wait cancelled', {
          readinessCancelled: readiness.cancelled,
          effectCancelled: cancelled,
          mounted: mountedRef.current,
        });
        return;
      }
      if (!readiness.ready) {
        updateScanDebugReason('waiting for camera: stable frame not available');
        retryUntilVideoReady();
        return;
      }

      traceScanEvent('stable camera frame ready', scanOperationIdRef.current + 1, {
        stableForMs: readiness.stableForMs,
        detectionWaitMs: readiness.waitDurationMs,
      });
      traceCameraTiming('capture eligibility reached', {
        stableForMs: readiness.stableForMs,
        detectionWaitMs: readiness.waitDurationMs,
        changedPixelRatio: readiness.changedPixelRatio === null ? null : Number(readiness.changedPixelRatio.toFixed(3)),
      });
      updateScanDebugReason('capture eligible: capturing frame');
      console.log('[Camera] Camera frame is clear and stable; sending it to the classifier');
      setSubStatus('จับภาพวัตถุ…');
      captureAndScan({ trigger: 'object_present_and_stable_frame' });
    };

    setStatus('กำลังตรวจจับขยะ...');
    setSubStatus('ถือขยะให้นิ่งในบริเวณกล้อง');
    traceCameraTiming('capture preparation queued', { delayMs: 0 });
    retryTimer = setTimeout(() => {
      prepareCapture();
    }, 0);
    cameraRetryTimerRef.current = retryTimer;

    return () => {
      cancelled = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      if (cameraRetryTimerRef.current === retryTimer) cameraRetryTimerRef.current = null;
    };
  }, [ttState, globalStream, scanCycle, cameraStatus]);

  return (
    <>
      {/* Live Webcam Background for IDLE (also prevents browser from pausing the video) */}
      <video ref={idleVideoRef} autoPlay playsInline muted 
        style={{ 
          position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', 
          objectFit: 'cover', opacity: 0,
          zIndex: -1, pointerEvents: 'none'
        }} 
      />
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      {/* ── IDLE ── */}
      {(ttState === TT_STATES.IDLE || screenTransition !== 'idle') && (
        <div className={`tt-page tt-welcome-screen${screenTransition !== 'idle' ? ` tt-welcome-screen--${screenTransition}` : ''}`}>
          <div className="tt-idle">
            {/* Blue zone: logo + title */}
            <div className="tt-idle__top">
              <div className="tt-idle__logo-wrap">
                <div className="tt-idle__ring" />
                <div className="tt-idle__ring" />
                <div className="tt-idle__ring" />
                <img src="/Logo.png" alt="EcoCAICle" className="tt-idle__logo" />
              </div>
              <h1 className="tt-idle__title">EcoCAICle</h1>
              <p className="tt-idle__subtitle">
                ระบบคัดแยกขยะอัจฉริยะด้วย AI
              </p>
            </div>

            <button className="tt-idle__cta" type="button" onClick={beginScanTransition}>
              <span className="tt-idle__cta-hint">
                <span className="tt-idle__status-dot" aria-hidden="true" />
                ระบบพร้อมสแกน
              </span>
              <span className="tt-idle__cta-main">วางขยะหน้ากล้อง</span>
            </button>
          </div>
        </div>
      )}

      {/* ── SCAN ── */}
      <div
        className={`tt-page tt-scan-page${screenTransition !== 'idle' ? ` tt-scan-page--${screenTransition}` : ''}`}
        style={{ display: ttState === TT_STATES.SCAN || screenTransition !== 'idle' ? undefined : 'none' }}
      >
          <div className="tt-scan-wrap tt-scan-wrap--reference">
            <header className="tt-scan-heading">
              <h1>EcoCAICle</h1>
              <h2>{capturedImage ? 'ถ่ายภาพแล้ว' : '📷 กำลังสแกนขยะ'}</h2>
              {/* <p>{capturedImage ? '' : 'วางขยะของคุณหน้ากล้อง ระบบจะถ่ายภาพให้อัตโนมัติ'}</p> */}
            </header>

            {/* Keep the preview live until the captured frame is ready, then freeze it during analysis. */}
            <div className="tt-camera-frame tt-camera-frame--reference">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                style={{
                  width: '100%',
                  height: '100%',
                  objectFit: 'cover',
                  display: 'block',
                  visibility: capturedImageLoaded ? 'hidden' : 'visible',
                }}
              />
              {capturedImage && (
                <img
                  className="tt-captured-image"
                  src={capturedImage}
                  alt="ภาพขยะที่ถ่ายแล้ว"
                  onLoad={(event) => {
                    setCapturedImageLoaded(true);
                    traceScanEvent('captured preview displayed', activeScanRef.current?.id || scanOperationIdRef.current, {
                      width: event.currentTarget.naturalWidth,
                      height: event.currentTarget.naturalHeight,
                    });
                  }}
                />
              )}

              {cameraStatus !== 'ready' && !capturedImage && scanPhase === SCAN_PHASES.SCANNING && (
                <div className="tt-camera-overlay-status" role="status" aria-live="polite">
                  <p className="tt-camera-substatus">กำลังเปิดกล้อง…</p>
                </div>
              )}

              {/* Scan line — animated sweep while waiting */}
              {scanPhase === SCAN_PHASES.SCANNING && (
                <div className="tt-scan-line" />
              )}

            </div>

            <div className="tt-scan-status" aria-live="polite">
              <span className="tt-status-dot" />
              <span>{status}</span>
              {subStatus && <p>{subStatus}</p>}
            </div>

            {/* Countdown below camera — only capsule, clean */}
            {scanPhase !== SCAN_PHASES.ANALYZING && (
              <div className="tt-countdown">
                กลับหน้าหลักใน <strong>{countdown}</strong> วินาที
              </div>
            )}
          </div>
        </div>

      {/* ── RESULT ── */}
      {ttState === TT_STATES.RESULT && screenTransition !== 'exiting' && (() => {
        const payload = resultData;

        let items = [];
        if (Array.isArray(payload?.waste_items)) items = payload.waste_items;
        else if (payload?.prediction) items = [payload.prediction];
        else if (
          payload?.name ||
          payload?.object_name_th ||
          payload?.objectNameTh ||
          payload?.itemNameTh
        ) items = [payload];

        if (items.length === 0) return null;

        const actualIndex = currentResultIndex % items.length;
        const it = items[actualIndex];

        const wasteType = it?.waste_category || it?.wasteType || WASTE_TYPE.UNKNOWN;
        const objectNameTh =
          it?.object_name_th ||
          it?.objectNameTh ||
          it?.itemNameTh ||
          payload?.prediction?.object_name_th ||
          payload?.prediction?.objectNameTh ||
          payload?.prediction?.itemNameTh ||
          payload?.object_name_th ||
          payload?.objectNameTh ||
          payload?.itemNameTh ||
          '';
        return (
          <WasteResult
            result={payload}
            wasteType={wasteType}
            objectNameTh={objectNameTh}
            confidence={it?.confidence}
            countdown={countdown}
          />
        );
      })()}

    </>
  );
}
