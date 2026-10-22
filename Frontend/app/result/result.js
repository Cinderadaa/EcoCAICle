'use client';
import React, { useEffect, useState } from 'react';
import { WASTE_TYPE, normalizeClassificationResponse } from '@/lib/wasteClassification';
import { RESULT_RESET_SECONDS, setTTState, TT_STATES } from '@/lib/ttState';
import { getRemainingResultSeconds, shouldResetResult } from '@/lib/resultFlow';
import WasteResult from '@/components/WasteResult';

function tryParse(str) {
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

function normalizePayload(input) {
  if (!input) return null;

  // 1) If there is raw model output, parse it first and trust it
  if (typeof input.raw === 'string') {
    const parsed = tryParse(input.raw);
    if (
      parsed &&
      (
        parsed.name ||
        parsed.object_name_th ||
        parsed.objectNameTh ||
        parsed.itemNameTh ||
        parsed.waste_category ||
        parsed.wasteType ||
        parsed.prediction?.object_name_th ||
        parsed.prediction?.waste_category ||
        parsed.prediction?.wasteType ||
        parsed.prediction?.class ||
        parsed.bin_color ||
        parsed.bin_key ||
        parsed.status ||
        parsed.request_id ||
        parsed.bin_id ||
        Array.isArray(parsed.items) ||
        Array.isArray(parsed.wasteItems) ||
        Array.isArray(parsed.waste_items)
      )
    ) {
      return parsed;
    }
  }

  // 2) unwrap common API shapes
  if (input.result && typeof input.result === 'object') input = input.result;
  if (input.data && typeof input.data === 'object') input = input.data;

  // 3) accept direct object
  if (
    typeof input === 'object' &&
    (
      input.object_name_th ||
      input.objectNameTh ||
      input.itemNameTh ||
      input.waste_category ||
      input.name ||
      input.wasteType ||
      input.prediction?.object_name_th ||
      input.prediction?.waste_category ||
      input.prediction?.wasteType ||
      input.prediction?.class ||
      input.bin_color ||
      input.bin_key ||
      input.status ||
      input.request_id ||
      input.bin_id ||
      Array.isArray(input.items) ||
      Array.isArray(input.wasteItems) ||
      Array.isArray(input.waste_items)
    )
  ) {
    return input;
  }

  return null;
}

export default function ResultPage() {
  const [payload, setPayload] = useState(null);
  const [showModelErrorPopup, setShowModelErrorPopup] = useState(false);
  // --- UX countdown state ---
  const [countdown, setCountdown] = useState(RESULT_RESET_SECONDS);
  const [phase, setPhase] = useState('show'); // 'show' | 'resetting'

  // Load payload from sessionStorage
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const raw = sessionStorage.getItem('tt:last');
      if (!raw) {
        console.warn('[Result] tt:last not found');
        setPayload(null);
        return;
      }
      const parsed = tryParse(raw);
      console.log('[Result] loaded raw', parsed);
      const usablePayload = normalizePayload(parsed);
      const normalizedPayload = usablePayload ? normalizeClassificationResponse(usablePayload) : null;
      if (normalizedPayload) {
        console.info(
          normalizedPayload.status === 'unknown'
            ? '[Result] unknown classification; using configured Bin 1 fallback destination'
            : normalizedPayload.status === 'missing_mapping'
              ? '[Result] no configured destination for this location'
              : normalizedPayload.status === 'api_error'
                ? '[Result] backend API error'
            : '[Result] confirmed waste category',
          {
            classificationStatus: normalizedPayload.classification_status,
            status: normalizedPayload.status,
            wasteType: normalizedPayload.prediction?.wasteType,
            destinationSource: normalizedPayload.destination_source,
            locationId: normalizedPayload.location_id,
            binNumber: normalizedPayload.bin_number,
            mappingVersion: normalizedPayload.mapping_version,
          },
        );
      }
      setPayload(normalizedPayload);
      if (parsed && parsed.raw) {
        console.log('[Result] raw model output detected');
      }
    } catch (e) {
      console.error('[Result] load error', e);
      setPayload(null);
    }
  }, []);

  useEffect(() => {
    if (!payload) {
      setShowModelErrorPopup(true);
      return;
    }

    setShowModelErrorPopup(false);

    // One interval drives both the visible countdown and the automatic reset.
    const deadline = Date.now() + RESULT_RESET_SECONDS * 1000;
    let stopped = false;
    setCountdown(RESULT_RESET_SECONDS);
    setPhase('show');

    const resetToIdle = () => {
      if (stopped) return;
      stopped = true;
      setCountdown(0);
      setPhase('resetting');
      sessionStorage.removeItem('tt:last');
      // Return to the kiosk scanner so the next capture starts automatically.
      setTTState(TT_STATES.SCAN);
      window.location.href = '/';
    };
    const interval = setInterval(() => {
      const now = Date.now();
      const remaining = getRemainingResultSeconds(deadline, now);
      setCountdown(remaining);
      if (shouldResetResult(deadline, now)) resetToIdle();
    }, 1000);
    const handleObjectGone = () => resetToIdle();
    window.addEventListener('tt:object-gone', handleObjectGone);

    return () => {
      stopped = true;
      clearInterval(interval);
      window.removeEventListener('tt:object-gone', handleObjectGone);
    };
  }, [payload]);

  // Build items: support multiple items if present, including waste_items
  let items = [];

  if (payload) {
    const rawItems =
      payload.waste_items ||
      payload.wasteItems ||
      payload.items ||
      (payload.name || payload.bin_color || payload.bin_key ? [payload] : []);

    if (Array.isArray(rawItems)) {
      items = rawItems.map((it) => ({
        wasteType:
          it.waste_category ||
          it.wasteType ||
          payload.prediction?.waste_category ||
          payload.prediction?.wasteType ||
          payload.waste_category ||
          payload.wasteType ||
          WASTE_TYPE.UNKNOWN,
        objectNameTh:
          it.object_name_th ||
          it.objectNameTh ||
          it.itemNameTh ||
          payload.prediction?.object_name_th ||
          payload.prediction?.objectNameTh ||
          payload.prediction?.itemNameTh ||
          payload.object_name_th ||
          payload.objectNameTh ||
          payload.itemNameTh ||
          '',
        confidence: typeof it.confidence === 'number' ? it.confidence : payload.prediction?.confidence,
        hasFoodScraps: it.hasFoodScraps === true || it.has_food_scraps === true || payload?.disposal?.hasFoodScraps === true,
        liquidPreparationScenario: it.liquidPreparationScenario || payload?.disposal?.liquidPreparationScenario || '',
      }));
    }
  }
  const wasteType =
    items[0]?.wasteType ||
    payload?.prediction?.waste_category ||
    payload?.prediction?.wasteType ||
    payload?.waste_category ||
    payload?.wasteType ||
    WASTE_TYPE.UNKNOWN;

  return (
   <>

      {showModelErrorPopup && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10002,
            background: 'rgba(17,24,39,0.45)',
            display: 'grid', placeItems: 'center', padding: 16
          }}
          aria-modal="true" role="dialog"
          onClick={() => { /* block closing; do nothing */ }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="tt-dialog"
            style={{
              width: 'min(92vw, 420px)', background: '#F5F7FB',
              borderRadius: 20, boxShadow: '0 20px 50px rgba(0,0,0,0.15)',
              padding: 24, textAlign: 'center'
            }}
          >
            <div style={{display:'grid', placeItems:'center', marginTop:-60, marginBottom:8}}>
              <img src="/wrong.png" alt="สแกนไม่ได้" width={120} height={120} style={{ display:'block' }} />
            </div>
            <div className="tt-dialog-title" style={{fontWeight:800, fontSize:23, color:'#111827', marginBottom:8}}>สแกนไม่สำเร็จ</div>
            <div className="tt-dialog-text" style={{fontSize:17, color:'#374151', marginBottom:20, lineHeight:1.5}}>
              ไม่พบข้อมูลจาก AI<br/>
              กรุณาถ่ายภาพใหม่อีกครั้ง
            </div>
            {/* No button, auto reset only */}
          </div>
        </div>
      )}
      <WasteResult
        result={payload}
        wasteType={wasteType}
        objectNameTh={items[0]?.objectNameTh || payload?.prediction?.object_name_th || payload?.prediction?.itemNameTh || ''}
        confidence={items[0]?.confidence ?? payload?.prediction?.confidence}
        countdown={phase === 'show' ? countdown : 0}
      />
    </> 
  );

}
