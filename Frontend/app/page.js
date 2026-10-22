'use client';

import { useEffect, useRef, useState } from 'react';
import { getTTState, RESULT_RESET_SECONDS, setTTState, TT_STATES } from '@/lib/ttState';
import { WASTE_TYPE, normalizeClassificationResponse } from '@/lib/wasteClassification';
import { mockWasteResults } from '@/data/mockData';
import WasteResult from '@/components/WasteResult';

// Use the live classification service by default. Mock results are available
// only when explicitly enabled for a local demo.
const USE_MOCK_CLASSIFICATION = process.env.NEXT_PUBLIC_USE_MOCK_CLASSIFICATION === 'true';

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
  const abortRef = useRef(null);
  const lastFrameHashRef = useRef(null);
  const lastScanAtRef = useRef(0);
  const emptyFrameCountRef = useRef(0);
  const retryCountRef = useRef(0);
  const mockResultIndexRef = useRef(0);

  const [status, setStatus] = useState('กำลังเปิดกล้อง…');
  const [previewImage, setPreviewImage] = useState(null);
  const [subStatus, setSubStatus] = useState('');
  const [error, setError] = useState(null);
  const [ttState, setTTStateLocal] = useState(TT_STATES.IDLE);
  const [resultData, setResultData] = useState(null);
  const [countdown, setCountdown] = useState(15);
  const [currentResultIndex, setCurrentResultIndex] = useState(0);
  const [errorOverlay, setErrorOverlay] = useState(null);
  const currentResultIndexRef = useRef(0);
  
  // Touchless Kiosk Mode Stream
  const [globalStream, setGlobalStream] = useState(null);
  const idleVideoRef = useRef(null);
  const lastIdleFrameRef = useRef(null);

  // RESET/CLEAR state when ttState changes to IDLE or SCAN
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) {
      // reset ทุกอย่างให้พร้อมเริ่มใหม่
      setPreviewImage(null);
      setStatus('วางขยะลงหน้ากล้องเพื่อเริ่มสแกน');
      setSubStatus('');
      setError(null);
      setErrorOverlay(null);
      scanningRef.current = false;
      lastFrameHashRef.current = null;
      retryCountRef.current = 0;
    }

    if (ttState === TT_STATES.SCAN) {
      // เข้าสแกนใหม่ ต้องเป็น live camera เสมอ
      setPreviewImage(null);
      setSubStatus('');
      setError(null);
      setErrorOverlay(null);
      scanningRef.current = false;
    }
  }, [ttState]);

  useEffect(() => {
    setTTState(TT_STATES.IDLE);
    setTTStateLocal(TT_STATES.IDLE);
  }, []);

  function showScanError(message) {
    setErrorOverlay(message);
    setStatus('วิเคราะห์ไม่สำเร็จ');
    setSubStatus('');
    scanningRef.current = false;

    setTimeout(() => {
      if (getTTState() === TT_STATES.SCAN) {
        setErrorOverlay(null);
        setTTState(TT_STATES.IDLE);
        setTTStateLocal(TT_STATES.IDLE);
      }
    }, 2500);
  }

  // Note: Removed 'Enter' key mapping for Kiosk auto-triggering

  // Global Camera Init (Always On)
  useEffect(() => {
    async function initGlobalCamera() {
      try {
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
        setGlobalStream(stream);
      } catch (e) {
        console.error('Camera init error', e);
        setError('ไม่สามารถเปิดกล้องได้');
      }
    }
    initGlobalCamera();
  }, []);

  // Bind stream to invisible IDLE video for detection
  useEffect(() => {
    if (idleVideoRef.current && globalStream) {
      idleVideoRef.current.srcObject = globalStream;
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

  // Manage universal kiosk timeouts
  useEffect(() => {
    if (ttState === TT_STATES.IDLE) return;

    // Default timeouts: 15s for results, 20s for scan inactivity (e.g. error scan/abandon)
    const limit = (ttState === TT_STATES.RESULT) ? RESULT_RESET_SECONDS : 20;
    setCountdown(limit);

    // Reset pagination when entering RESULT state
    if (ttState === TT_STATES.RESULT) {
      setCurrentResultIndex(0);
      currentResultIndexRef.current = 0;
    }

    const interval = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          clearInterval(interval);
          setResultData(null);
          setTTState(TT_STATES.IDLE);
          setTTStateLocal(TT_STATES.IDLE);
          return 0;
        }
        return prev - 1;
      });
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
  }, [ttState, resultData]);

  async function captureAndScan() {
    const now = Date.now();
    if (now - lastScanAtRef.current < 5000) {
      scanningRef.current = false;
      return;
    }

    if (scanningRef.current) return;
    scanningRef.current = true;
    abortRef.current?.abort();
    abortRef.current = new AbortController();

    const video = videoRef.current;
    const canvas = canvasRef.current;

    if (!video || !video.videoWidth) {
      scanningRef.current = false;
      return;
    }

    const applyDetectionResult = (detectionResult) => {
      const normalizedResult = normalizeClassificationResponse(detectionResult);
      if (normalizedResult?.status === 'api_error' || normalizedResult?.valid_prediction === false) {
        showScanError('ระบบวิเคราะห์ขัดข้อง กรุณาลองใหม่อีกครั้ง');
        return;
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
      retryCountRef.current = 0;
      setSubStatus('');
      abortRef.current?.abort();
      sessionStorage.setItem('tt:last', JSON.stringify(normalizedResult));
      setResultData(normalizedResult);
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

    setStatus('วางขยะลงได้เลย');
    setSubStatus('');

    // Reduced wait for MX Brio / external webcams
    await new Promise(r => setTimeout(r, 1000));
    
    setSubStatus('กำลังบันทึกภาพ...');

    // Capture multiple frames — pick sharpest using proper 3-channel Laplacian scoring
    function frameSharpnessScore(ctx, w, h) {
      const img = ctx.getImageData(0, 0, w, h).data;
      let score = 0;
      // Use a 2-pixel horizontal step, sample all 3 RGB channels
      for (let i = 0; i < img.length - 4 * 2; i += 4) {
        const rDiff = Math.abs(img[i]     - img[i + 8]); // R channel
        const gDiff = Math.abs(img[i + 1] - img[i + 9]); // G channel
        const bDiff = Math.abs(img[i + 2] - img[i + 10]);// B channel
        score += rDiff + gDiff + bDiff;
      }
      return score;
    }

    let bestImage = null;
    let bestScore = -1;
    let brightnessFail = false;

    // 5 frames with 200ms spacing for better sharpest-frame selection
    for (let i = 0; i < 5; i++) {
      drawScaledFromVideo(video, canvas);
      const ctx = canvas.getContext('2d', { willReadFrequently: true });

      // Brightness gate — skip frames that are too dark or overexposed
      const { ok: brightOk, meanBrightness } = checkFrameBrightness(ctx, canvas.width, canvas.height);
      if (!brightOk) {
        console.warn(`[SCAN] Frame ${i} brightness out of range (${meanBrightness}), skipping`);
        brightnessFail = true;
        await new Promise(r => setTimeout(r, 200));
        continue;
      }
      brightnessFail = false;

      const score = frameSharpnessScore(ctx, canvas.width, canvas.height);
      if (score > bestScore) {
        bestScore = score;
        bestImage = canvas.toDataURL('image/jpeg', 0.88);
      }
      await new Promise(r => setTimeout(r, 200));
    }

    // If all frames were too dark/bright, show an error instead of sending garbage.
    if (!bestImage || brightnessFail) {
      showScanError('ภาพมืดหรือสว่างเกินไป');
      return;
    }

    const imageBase64 = bestImage;

    setPreviewImage(imageBase64);
    setSubStatus('จับภาพเรียบร้อย');

    // Keep the existing frame-quality telemetry for the camera flow.
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (isFrameEmpty(ctx, canvas.width, canvas.height, 2.5)) {
      console.warn('[SCAN] Frame quality is weak; sending it to the backend for validation.');
      sessionStorage.setItem('tt:emptyHash', '1');
    } else {
      const emptyHash = simpleHash(
        ctx.getImageData(0, 0, canvas.width, canvas.height).data.slice(0, 2000).toString()
      );
      sessionStorage.setItem('tt:emptyHash', String(emptyHash));
    }

    const frameHash = simpleHash(imageBase64.slice(0, 2000));

    // allow up to 2 retries even if frame looks identical
    if (lastFrameHashRef.current === frameHash && retryCountRef.current >= 2) {
      scanningRef.current = false;
      return;
    }

    if (lastFrameHashRef.current === frameHash) {
      retryCountRef.current += 1;
    } else {
      retryCountRef.current = 0;
    }

    lastFrameHashRef.current = frameHash;
    lastScanAtRef.current = Date.now();

    try {
      setStatus('กำลังวิเคราะห์ภาพ…');

      if (USE_MOCK_CLASSIFICATION) {
        setSubStatus('กำลังใช้ข้อมูลตัวอย่าง…');
        await new Promise(r => setTimeout(r, 500));
        applyDetectionResult(getNextMockPrediction());
        return;
      }

      setSubStatus('กำลังเชื่อมต่อกับ FastAPI…');

      const imageResponse = await fetch(imageBase64);
      const imageBlob = await imageResponse.blob();
      const formData = new FormData();
      formData.append('file', imageBlob, 'capture.jpg');

      const timeoutId = setTimeout(() => abortRef.current?.abort(), 120000);
      const res = await fetch('/api/classify', {
        method: 'POST',
        body: formData,
        signal: abortRef.current.signal,
      });
      clearTimeout(timeoutId);

      const parsed = await res.json();
      console.log('[CLASSIFY RAW]', parsed);

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
      if (e.name === 'AbortError') {
        console.warn('[SCAN] Detection timed out.');
        showScanError('การวิเคราะห์ใช้เวลานานเกินไป');
      } else if (e.responseStatus === 'api_error') {
        console.error('backend API error', e);
        showScanError('ระบบวิเคราะห์ขัดข้อง กรุณาลองใหม่อีกครั้ง');
      } else {
        console.error('scan error', e);
        showScanError('ไม่สามารถเชื่อมต่อระบบวิเคราะห์ได้');
      }
    }
  }

  // SCAN State: Bind stream to visible UI and Auto-Capture
  useEffect(() => {
    if (ttState !== TT_STATES.SCAN) return;

    if (videoRef.current && globalStream) {
      videoRef.current.srcObject = globalStream;
    }

    // Delay briefly to allow auto-exposure adjustments before capture
    console.log('[Camera] Using auto focus/exposure with stabilization delay');
    setStatus('กำลังเตรียมภาพ…');
    const timer = setTimeout(() => {
      if (!scanningRef.current && emptyFrameCountRef.current === 0) {
        captureAndScan();
      }
    }, 1000); 

    return () => clearTimeout(timer);
  }, [ttState, globalStream]);

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
      {ttState === TT_STATES.SCAN && (
        <div className="tt-page tt-scan-page">
          <div className="tt-scan-wrap tt-scan-wrap--reference">
            <header className="tt-scan-heading">
              <h1>EcoAICle</h1>
              <h2>📷 กำลังสแกนขยะ</h2>
              <p>วางขยะของคุณหน้ากล้อง ระบบจะถ่ายภาพให้อัตโนมัติ</p>
            </header>

            {/* The camera remains fully automatic; this frame is visual only. */}
            <div className={`tt-camera-frame tt-camera-frame--reference ${previewImage ? 'tt-camera-frame--captured' : ''}`}>
              {!previewImage ? (
                <video ref={videoRef} autoPlay playsInline muted
                  style={{ width:'100%', height:'100%', objectFit:'cover', display:'block' }} />
              ) : (
                <img src={previewImage} alt="Captured"
                  style={{ width:'100%', height:'100%', objectFit:'cover', display:'block' }} />
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
      )}

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

      {/* Hidden canvas for image capture and analysis */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </>
  );
}
