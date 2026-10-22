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
import { PREDICT_TIMEOUT_MS, requestPrediction } from '@/lib/predictRequest';
import { attachCameraVideo, getLiveCameraStream, hasLiveVideoTrack } from '@/lib/cameraLifecycle';
import { mockWasteResults } from '@/data/mockData';
import WasteResult from '@/components/WasteResult';

// Use the live classification service by default. Mock results are available
// only when explicitly enabled for a local demo.
const USE_MOCK_CLASSIFICATION = process.env.NEXT_PUBLIC_USE_MOCK_CLASSIFICATION === 'true';
const SCAN_RESET_SECONDS = 15;
const CAPTURE_START_DELAY_MS = 1_000;
const CAPTURE_RETRY_DELAY_MS = 250;
const ERROR_RESET_DELAY_MS = 2_500;

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

export default function Page() {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const scanningRef = useRef(false);
  const activeScanRef = useRef(null);
  const scanOperationIdRef = useRef(0);
  const scanPhaseRef = useRef(SCAN_PHASES.SCANNING);
  const mountedRef = useRef(false);
  const autoResetTimerRef = useRef(null);
  const cameraRetryTimerRef = useRef(null);
  const cameraInitRetryTimerRef = useRef(null);
  const cameraRetryAttemptRef = useRef(0);
  const cameraRetryAtRef = useRef(0);
  const cameraErrorShownRef = useRef(false);
  const cameraInitInFlightRef = useRef(null);
  const cameraStartRef = useRef(null);
  const globalStreamRef = useRef(null);
  const emptyFrameCountRef = useRef(0);
  const mockResultIndexRef = useRef(0);

  const [status, setStatus] = useState('กำลังเปิดกล้อง…');
  const [previewImage, setPreviewImage] = useState(null);
  const [subStatus, setSubStatus] = useState('');
  const [cameraStatus, setCameraStatus] = useState('initializing');
  const [ttState, setTTStateLocal] = useState(TT_STATES.IDLE);
  const [resultData, setResultData] = useState(null);
  const [countdown, setCountdown] = useState(15);
  const [currentResultIndex, setCurrentResultIndex] = useState(0);
  const [errorOverlay, setErrorOverlay] = useState(null);
  const [scanPhase, setScanPhase] = useState(SCAN_PHASES.SCANNING);
  const [scanCycle, setScanCycle] = useState(0);
  const currentResultIndexRef = useRef(0);
  
  // Touchless Kiosk Mode Stream
  const [globalStream, setGlobalStream] = useState(null);
  const idleVideoRef = useRef(null);
  const lastIdleFrameRef = useRef(null);

  function logCamera(message, details = undefined) {
    if (process.env.NODE_ENV === 'development') {
      console.debug(`[Camera] ${message}`, details || '');
    }
  }

  function scheduleCameraRetry() {
    if (!mountedRef.current || cameraInitRetryTimerRef.current !== null) return;
    const delayMs = Math.min(15_000, 1_000 * (2 ** cameraRetryAttemptRef.current));
    cameraRetryAttemptRef.current += 1;
    cameraRetryAtRef.current = Date.now() + delayMs;
    cameraInitRetryTimerRef.current = setTimeout(() => {
      cameraInitRetryTimerRef.current = null;
      cameraStartRef.current?.();
    }, delayMs);
  }

  function clearCameraError() {
    if (cameraErrorShownRef.current) setErrorOverlay(null);
    cameraErrorShownRef.current = false;
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

  function resetCaptureGuards() {
    scanningRef.current = false;
    emptyFrameCountRef.current = 0;
    lastIdleFrameRef.current = null;
  }

  function cancelActiveScan() {
    const activeScan = activeScanRef.current;
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

  function resetToScanning() {
    if (!mountedRef.current) return;
    logCamera('camera reset requested', { cycle: scanOperationIdRef.current });
    if (activeScanRef.current?.requestStarted && !activeScanRef.current?.requestCompleted) {
      if (process.env.NODE_ENV !== 'production') {
        console.warn('[Predict] reset before request completed', { cycle: activeScanRef.current.id });
      }
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

    resetCaptureGuards();
    setPreviewImage(null);
    setResultData(null);
    setErrorOverlay(null);
    setSubStatus('');
    setStatus('กำลังเตรียมภาพ…');
    setCountdown(SCAN_RESET_SECONDS);
    setScanCycle((previousCycle) => previousCycle + 1);
    setTTState(TT_STATES.SCAN);
    setTTStateLocal(TT_STATES.SCAN);
    transitionScanPhase(
      SCAN_PHASES.SCANNING,
      previousPhase === SCAN_PHASES.RESULT || previousPhase === SCAN_PHASES.ERROR
        ? previousPhase
        : null,
    );
  }

  function scheduleAutoReset(delay = ERROR_RESET_DELAY_MS) {
    if (autoResetTimerRef.current !== null) {
      clearTimeout(autoResetTimerRef.current);
    }
    autoResetTimerRef.current = setTimeout(() => {
      autoResetTimerRef.current = null;
      resetToScanning();
    }, delay);
  }

  // RESET/CLEAR state when ttState changes to IDLE or SCAN
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) {
      // reset ทุกอย่างให้พร้อมเริ่มใหม่
      cancelActiveScan();
      resetCaptureGuards();
      setPreviewImage(null);
      setStatus('วางขยะลงหน้ากล้องเพื่อเริ่มสแกน');
      setSubStatus('');
      setErrorOverlay(null);
      transitionScanPhase(SCAN_PHASES.SCANNING);
    }

    if (ttState === TT_STATES.SCAN) {
      // เข้าสแกนใหม่ ต้องเป็น live camera เสมอ
      setPreviewImage(null);
      setSubStatus('');
      setErrorOverlay(null);
      if (!activeScanRef.current) {
        resetCaptureGuards();
        transitionScanPhase(SCAN_PHASES.SCANNING);
      }
    }
  }, [ttState]);

  useEffect(() => {
    mountedRef.current = true;
    const persistedState = getTTState();
    const initialState = persistedState === TT_STATES.SCAN
      ? TT_STATES.SCAN
      : TT_STATES.IDLE;
    setTTState(initialState);
    setTTStateLocal(initialState);
    transitionScanPhase(SCAN_PHASES.SCANNING);

    return () => {
      mountedRef.current = false;
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
      cancelActiveScan();
      globalStreamRef.current?.getTracks?.().forEach((track) => track.stop());
    };
  }, []);

  function showScanError(message, activeScan = null) {
    if (activeScan && !isActiveScan(activeScan)) return;

    transitionScanPhase(SCAN_PHASES.ERROR);
    setErrorOverlay(message);
    setStatus('วิเคราะห์ไม่สำเร็จ');
    setSubStatus('');
    scheduleAutoReset(ERROR_RESET_DELAY_MS);
  }

  // Note: Removed 'Enter' key mapping for Kiosk auto-triggering

  // Global Camera Init (Always On)
  useEffect(() => {
    async function initGlobalCamera() {
      if (cameraInitInFlightRef.current) return cameraInitInFlightRef.current;
      if (hasLiveVideoTrack(globalStreamRef.current)) {
        logCamera('camera stream reused or reinitialized', { mode: 'reused' });
        setCameraStatus('ready');
        clearCameraError();
        return globalStreamRef.current;
      }

      setCameraStatus('initializing');
      setStatus('กำลังเปิดกล้อง…');
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
            cameraRetryAttemptRef.current = 0;
            cameraRetryAtRef.current = 0;
            if (cameraInitRetryTimerRef.current !== null) {
              clearTimeout(cameraInitRetryTimerRef.current);
              cameraInitRetryTimerRef.current = null;
            }
            clearCameraError();
            logCamera('camera stream ready', { tracks: stream.getVideoTracks().length });
            logCamera('camera stream reused or reinitialized', { mode: 'reinitialized' });
          } else {
            stream.getTracks().forEach((track) => track.stop());
          }
          return stream;
        } catch (e) {
          logCamera('camera initialization failed', { name: e?.name || 'Error', message: e?.message });
          if (mountedRef.current) {
            setCameraStatus('error');
            setStatus('ไม่สามารถเปิดกล้องได้');
            setErrorOverlay('ไม่สามารถเปิดกล้องได้');
            cameraErrorShownRef.current = true;
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

  // Motion Detection Loop (IDLE -> SCAN)
  useEffect(() => {
    if (ttState !== TT_STATES.IDLE || !globalStream) return;

    lastIdleFrameRef.current = null;

    const interval = setInterval(() => {
      const video = idleVideoRef.current;
      const canvas = canvasRef.current;
      if (!video || !canvas || video.videoWidth === 0) return;

      canvas.width = 64; 
      canvas.height = 64; 
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(video, 0, 0, 64, 64);
      
      const currentData = ctx.getImageData(0,0,64,64).data;
      
      if (lastIdleFrameRef.current) {
        let diffSum = 0;
        for (let i = 0; i < currentData.length; i += 4) {
          const rDiff = Math.abs(currentData[i] - lastIdleFrameRef.current[i]);
          const gDiff = Math.abs(currentData[i+1] - lastIdleFrameRef.current[i+1]);
          const bDiff = Math.abs(currentData[i+2] - lastIdleFrameRef.current[i+2]);
          diffSum += (rDiff + gDiff + bDiff);
        }
        
        const avgDiff = diffSum / (64 * 64 * 3);
        // Threshold tuning: if > 8 per px/channel, trigger scan
        if (avgDiff > 8) { 
          setTTState(TT_STATES.SCAN);
          setTTStateLocal(TT_STATES.SCAN);
          setStatus('กำลังเตรียมภาพ…');
          return;
        }
      }
      
      lastIdleFrameRef.current = new Uint8ClampedArray(currentData);
    }, 400);

    return () => clearInterval(interval);
  }, [ttState, globalStream]);

  // The visible countdown never controls an in-flight request. Its deadline
  // starts only after the request reaches a terminal result or error.
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) return undefined;

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
          resetToScanning();
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

  async function captureAndScan() {
    if (!mountedRef.current || getTTState() !== TT_STATES.SCAN ||
        scanPhaseRef.current !== SCAN_PHASES.SCANNING || scanningRef.current || activeScanRef.current) return;

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
    transitionScanPhase(SCAN_PHASES.CAPTURING);

    const applyDetectionResult = (detectionResult) => {
      if (!isActiveScan(activeScan)) return;
      const normalizedResult = normalizeClassificationResponse(detectionResult);
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
      setSubStatus('');
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
        const captureError = new Error('กล้องยังไม่พร้อม');
        captureError.scanMessage = 'ไม่สามารถอ่านภาพจากกล้องได้';
        throw captureError;
      }

      setStatus('วางขยะลงได้เลย');
      setSubStatus('');

      setSubStatus('กำลังบันทึกภาพ...');
      drawScaledFromVideo(video, canvas);
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      const { ok: brightOk, meanBrightness } = checkFrameBrightness(ctx, canvas.width, canvas.height);
      if (!brightOk) {
        console.warn(`[SCAN] Frame brightness out of range (${meanBrightness})`);
        const brightnessError = new Error('ภาพมืดหรือสว่างเกินไป');
        brightnessError.scanMessage = brightnessError.message;
        throw brightnessError;
      }
      const imageBase64 = canvas.toDataURL('image/jpeg', 0.88);

      setPreviewImage(imageBase64);
      setSubStatus('จับภาพเรียบร้อย');

      // Keep the existing frame-quality telemetry for the camera flow.
      if (isFrameEmpty(ctx, canvas.width, canvas.height, 2.5)) {
        console.warn('[SCAN] Frame quality is weak; sending it to the backend for validation.');
        sessionStorage.setItem('tt:emptyHash', '1');
      } else {
        const emptyHash = simpleHash(
          ctx.getImageData(0, 0, canvas.width, canvas.height).data.slice(0, 2000).toString()
        );
        sessionStorage.setItem('tt:emptyHash', String(emptyHash));
      }

      transitionScanPhase(SCAN_PHASES.ANALYZING);
      setStatus('กำลังวิเคราะห์ภาพ…');

      if (USE_MOCK_CLASSIFICATION) {
        setSubStatus('กำลังใช้ข้อมูลตัวอย่าง…');
        await new Promise(r => setTimeout(r, 500));
        if (!isActiveScan(activeScan)) return;
        applyDetectionResult(getNextMockPrediction());
        return;
      }

      setSubStatus('กำลังเชื่อมต่อกับ FastAPI…');

      const imageBlob = await new Promise((resolve, reject) => {
        canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Image encoding failed')), 'image/jpeg', 0.88);
      });
      if (!isActiveScan(activeScan)) return;

      let res;
      let parsed;
      activeScan.requestStarted = true;
      const requestStartedAt = Date.now();
      try {
        ({ response: res, payload: parsed } = await requestPrediction({
          imageBlob,
          controller: activeScan.controller,
          onStart: () => {
            if (process.env.NODE_ENV !== 'production') console.debug('[Predict] POST /predict start', { cycle: activeScan.id });
          },
          onResponse: (statusCode, elapsedMs) => {
            if (process.env.NODE_ENV !== 'production') {
              console.debug('[Predict] response', { cycle: activeScan.id, status: statusCode, elapsedMs });
            }
          },
        }));
      } finally {
        activeScan.requestCompleted = true;
        if (process.env.NODE_ENV !== 'production') {
          console.debug('[Predict] settled', {
            cycle: activeScan.id,
            elapsedMs: Date.now() - requestStartedAt,
            resetBeforeCompletion: activeScan.cancelled && mountedRef.current,
          });
        }
      }

      if (!isActiveScan(activeScan)) return;
      if (process.env.NODE_ENV !== 'production') console.debug('[CLASSIFY RAW]', parsed);

      if (!res.ok || parsed?.status === 'api_error') {
        const apiError = new Error(parsed?.detail || parsed?.error || `Prediction failed (${res.status})`);
        apiError.responseStatus = parsed?.status || 'api_error';
        throw apiError;
      }

      // --- Normalize model response (robust against partial / variant schemas) ---
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
      if (!isActiveScan(activeScan)) return;

      if (e.name === 'TimeoutError') {
        console.warn('[SCAN] Detection timed out.');
        showScanError('การวิเคราะห์ใช้เวลานานเกินไป', activeScan);
      } else if (e.name === 'AbortError') {
        showScanError('การวิเคราะห์ถูกยกเลิก กรุณาลองใหม่อีกครั้ง', activeScan);
      } else if (e.responseStatus === 'api_error') {
        console.error('backend API error', e);
        showScanError('ระบบวิเคราะห์ขัดข้อง กรุณาลองใหม่อีกครั้ง', activeScan);
      } else {
        console.error('scan error', e);
        showScanError(e.scanMessage || 'ไม่สามารถเชื่อมต่อระบบวิเคราะห์ได้', activeScan);
      }
    } finally {
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
      if (cancelled || !mountedRef.current || getTTState() !== TT_STATES.SCAN) return;
      const untilRetry = Math.max(0, cameraRetryAtRef.current - Date.now());
      retryTimer = setTimeout(prepareCapture, Math.max(CAPTURE_RETRY_DELAY_MS, untilRetry));
      cameraRetryTimerRef.current = retryTimer;
    };

    const prepareCapture = async () => {
      if (cancelled || !mountedRef.current || getTTState() !== TT_STATES.SCAN || scanningRef.current) return;

      const video = videoRef.current;
      if (!video) {
        retryUntilVideoReady();
        return;
      }

      let stream = globalStreamRef.current || globalStream;
      if (!hasLiveVideoTrack(stream)) {
        setCameraStatus('initializing');
        setStatus('กำลังเปิดกล้อง…');
        const waitForRetry = cameraRetryAtRef.current - Date.now();
        if (waitForRetry > 0) {
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
          if (initialized.reused) {
            logCamera('camera stream reused or reinitialized', { mode: 'reused', cycle: scanCycle });
          }
        } catch {
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

      if (
        cancelled ||
        !mountedRef.current ||
        getTTState() !== TT_STATES.SCAN ||
        !video.videoWidth ||
        video.readyState < 2
      ) {
        retryUntilVideoReady();
        return;
      }

      console.log('[Camera] Using auto focus/exposure with stabilization delay');
      captureAndScan();
    };

    if (cameraStatus === 'ready') setStatus('กำลังเตรียมภาพ…');
    retryTimer = setTimeout(prepareCapture, CAPTURE_START_DELAY_MS);
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
          objectFit: 'cover', opacity: ttState === TT_STATES.IDLE ? 0.35 : 0, 
          zIndex: -1, transition: 'opacity 0.5s ease', pointerEvents: 'none'
        }} 
      />
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      {/* ── IDLE ── */}
      {ttState === TT_STATES.IDLE && (
        <div className="tt-page">
          <div className="tt-idle">
            {/* Blue zone: logo + title */}
            <div className="tt-idle__top">
              <div className="tt-idle__logo-wrap">
                <div className="tt-idle__ring" />
                <div className="tt-idle__ring" />
                <div className="tt-idle__ring" />
                <img src="/Logo.png" alt="EcoAICle" className="tt-idle__logo" />
              </div>
              <h1 className="tt-idle__title">EcoAICle</h1>
              <p className="tt-idle__subtitle">
                ให้เราบอกถังที่ใช่<br />
                ทิ้งถูกถังได้ในไม่กี่วินาที
              </p>
            </div>

            {/* White CTA card */}
            <div className="tt-idle__cta">
              <span className="tt-idle__cta-hint">วิธีใช้งาน</span>
              <p className="tt-idle__cta-main">
                วางขยะหน้ากล้อง
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── SCAN ── */}
      <div className="tt-page tt-scan-page" style={{ display: ttState === TT_STATES.SCAN ? undefined : 'none' }}>
          <div className="tt-scan-wrap tt-scan-wrap--reference">
            <header className="tt-scan-heading">
              <h1>EcoAICle</h1>
              <h2>📷 กำลังสแกนขยะ</h2>
              <p>วางขยะของคุณหน้ากล้อง ระบบจะถ่ายภาพให้อัตโนมัติ</p>
            </header>

            {/* The camera remains fully automatic; this frame is visual only. */}
            <div className={`tt-camera-frame tt-camera-frame--reference ${previewImage ? 'tt-camera-frame--captured' : ''}`}>
              <video ref={videoRef} autoPlay playsInline muted
                style={{ width:'100%', height:'100%', objectFit:'cover', display:'block' }} />
              {previewImage && (
                <img src={previewImage} alt="Captured"
                  style={{ position:'absolute', inset:0, width:'100%', height:'100%', objectFit:'cover', display:'block' }} />
              )}

              {!previewImage && cameraStatus !== 'ready' && (
                <div className="tt-camera-overlay-status" role="status" aria-live="polite">
                  <p className="tt-camera-substatus">กำลังเปิดกล้อง…</p>
                </div>
              )}

              {/* Scan line — animated sweep while waiting */}
              {!previewImage && (
                <div className="tt-scan-line" />
              )}

              {/* Freeze flash — shows briefly when capture happens */}
              {previewImage && (
                <div className="tt-capture-flash" />
              )}

            </div>

            <div className="tt-scan-status" aria-live="polite">
              <span className="tt-status-dot" />
              <span>{status}</span>
              {subStatus && <p>{subStatus}</p>}
            </div>

            {/* Countdown below camera — only capsule, clean */}
            <div className="tt-countdown">
              กลับหน้าหลักใน <strong>{countdown}</strong> วินาที
            </div>
          </div>
        </div>

      {/* ── RESULT ── */}
      {ttState === TT_STATES.RESULT && (() => {
        let payload = resultData;
        if (!payload) {
          try { const c = sessionStorage.getItem('tt:last'); if (c) payload = JSON.parse(c); } catch (_) {}
        }

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

      {/* ── ERROR OVERLAY (Full-screen Alert) ── */}
      {errorOverlay && (
        <div style={{
          position: 'fixed',
          top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.6)',
          zIndex: 9999,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center'
        }}>
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            padding: '50px 70px',
            gap: '24px',
            background: 'rgba(255, 255, 255, 0.95)',
            backdropFilter: 'blur(24px)',
            WebkitBackdropFilter: 'blur(24px)',
            border: '1.5px solid rgba(255, 255, 255, 0.9)',
            boxShadow: '0 12px 40px rgba(0,0,0,0.15)',
            borderRadius: '24px' // Modern card with 4 rounded corners instead of capsule
          }}>
            <img src="/wrong.png" alt="Error" style={{ width: '140px', height: '140px', objectFit: 'contain' }} />
            <h2 style={{ fontFamily: 'Thonburi', color: '#1f2937', fontSize: '2rem', fontWeight: 600, margin: 0, textAlign: 'center' }}>
              {errorOverlay}
            </h2>
            <p style={{ fontFamily: 'Thonburi', color: '#6b7280', fontSize: '1rem', margin: 0 }}>
              กำลังกลับสู่หน้าหลัก...
            </p>
          </div>
        </div>
      )}

    </>
  );
}
