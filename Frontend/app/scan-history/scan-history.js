'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Image from 'next/image';
import styles from './scan-history.module.css';

const CATEGORIES = ['general', 'pet_bottle', 'plastic', 'paper', 'glass', 'metal', 'ewaste', 'fuel', 'hazardous', 'infectious', 'unknown'];
const MATERIAL_SUBTYPES = ['pet_bottle', 'stretch_plastic', 'rigid_plastic', 'plastic_straw', 'multilayer_packaging', 'snack_sachet', 'coffee_sachet', 'refill_pouch', 'non_recyclable_foam', 'plastic_coated_paper', 'non_recyclable_dry_plastic', 'paper', 'cardboard', 'glass', 'metal', 'e_waste', 'hazardous', 'infectious', 'general'];
const CATEGORY_NAMES = {
  general: 'ทั่วไป', pet_bottle: 'ขวด PET', plastic: 'พลาสติก', paper: 'กระดาษ', glass: 'แก้ว', metal: 'โลหะ',
  ewaste: 'อิเล็กทรอนิกส์', fuel: 'เชื้อเพลิง', hazardous: 'อันตราย', infectious: 'ติดเชื้อ', unknown: 'ไม่ทราบ',
};

function percent(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
}

function dateLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function imageUrl(requestId) {
  return `/api/scan-history/${encodeURIComponent(requestId)}/image`;
}

function ScanImage({ requestId, alt, className, kind, loading = 'lazy' }) {
  const [startedAt] = useState(() => performance.now());
  const logImage = (eventName) => {
    if (process.env.NODE_ENV !== 'development') return;
    const elapsedMs = Math.round(performance.now() - startedAt);
    console.info(`[ScanHistory] ${kind} image ${eventName}`, { requestId, elapsedMs });
  };
  if (kind === 'thumbnail') {
    return (
      <Image
        className={className}
        src={imageUrl(requestId)}
        alt={alt}
        width={240}
        height={180}
        sizes="124px"
        quality={45}
        loading="lazy"
        onLoad={() => logImage('loaded')}
        onError={() => logImage('failed')}
      />
    );
  }
  return (
    <img
      className={className}
      src={imageUrl(requestId)}
      alt={alt}
      loading={loading}
      decoding="async"
      onLoad={() => logImage('loaded')}
      onError={() => logImage('failed')}
    />
  );
}

function ScanChips({ scan }) {
  const flags = scan.review_flags || {};
  const chips = [];
  if (scan.status) chips.push([scan.status, scan.status === 'success' ? 'ok' : 'warn']);
  if (scan.classification_status && scan.classification_status !== scan.status) {
    chips.push([scan.classification_status, scan.classification_status === 'confirmed' ? 'ok' : 'warn']);
  }
  if (scan.waste_category === 'unknown') chips.push(['unknown', 'danger']);
  if (scan.mapping_fallback) chips.push(['fallback', 'danger']);
  if (scan.mapping_reason === 'infectious_safety_override') {
    chips.push(['infectious safety override', 'review']);
  }
  if (scan.classification_status === 'low_confidence' || (typeof scan.confidence === 'number' && scan.confidence < 0.5)) {
    chips.push(['low confidence', 'danger']);
  }
  if (flags.glass_pet_review_required) chips.push(['glass / PET review', 'review']);
  return <div className={styles.chips}>{chips.map(([label, tone], index) => <span className={`${styles.chip} ${styles[tone]}`} key={`${label}-${index}`}>{label}</span>)}</div>;
}

function CandidateList({ candidates }) {
  const items = Array.isArray(candidates) ? candidates.slice(0, 3) : [];
  if (!items.length) return <span className={styles.muted}>—</span>;
  return <ol className={styles.candidates}>{items.map((candidate, index) => (
    <li key={`${candidate.waste_category}-${index}`}>
      <span>{candidate.object_name_th || '—'} <b>({CATEGORY_NAMES[candidate.waste_category] || candidate.waste_category || '—'})</b></span>
      <span>{percent(candidate.confidence)}</span>
    </li>
  ))}</ol>;
}

function formatAccuracy(metric) {
  return metric?.value == null ? '—' : `${Math.round(metric.value * 100)}% (${metric.correct}/${metric.eligible_samples})`;
}

function PilotMetric({ label, value }) {
  return <div className={styles.pilotMetric}><span>{label}</span><strong>{value}</strong></div>;
}

function PilotEvaluationEditor({ scan, onSaved }) {
  const [evaluation, setEvaluation] = useState(null);
  const [bins, setBins] = useState([]);
  const [validCapture, setValidCapture] = useState('');
  const [category, setCategory] = useState('');
  const [subtype, setSubtype] = useState('');
  const [binNumber, setBinNumber] = useState('');
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    setSaved(false);
    fetch(`/api/scan-history/pilot/annotations/${encodeURIComponent(scan.request_id)}`, { cache: 'no-store' })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.message || payload.detail?.message || `โหลดข้อมูลนำร่องไม่สำเร็จ (${response.status})`);
        if (!active) return;
        const current = payload.evaluation;
        setEvaluation(current || null);
        setBins(Array.isArray(payload.configured_bins) ? payload.configured_bins : []);
        setValidCapture(current ? (current.valid_capture ? 'yes' : 'no') : '');
        setCategory(current?.expected_waste_category || '');
        setSubtype(current?.expected_material_subtype || '');
        setBinNumber(current?.expected_bin_number == null ? '' : String(current.expected_bin_number));
        setNote(current?.note || '');
      })
      .catch((requestError) => { if (active) setError(requestError.message || 'โหลดข้อมูลนำร่องไม่สำเร็จ'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [scan.request_id]);

  async function submit(event) {
    event.preventDefault();
    setError('');
    setSaved(false);
    if (validCapture === '') {
      setError('กรุณาระบุว่าภาพเป็นการจับภาพที่ใช้ได้หรือไม่');
      return;
    }
    if (validCapture === 'yes' && !category) {
      setError('กรุณาเลือกประเภทขยะที่ถูกต้อง');
      return;
    }
    if (validCapture === 'yes' && category !== 'unknown' && !binNumber) {
      setError('กรุณาเลือกหมายเลขถังที่คาดหวัง');
      return;
    }
    setSaving(true);
    try {
      const response = await fetch(`/api/scan-history/pilot/annotations/${encodeURIComponent(scan.request_id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          valid_capture: validCapture === 'yes',
          expected_waste_category: validCapture === 'yes' ? category : null,
          expected_material_subtype: validCapture === 'yes' ? (subtype || null) : null,
          expected_bin_number: validCapture === 'yes' && binNumber ? Number(binNumber) : null,
          note: note.trim() || null,
          expected_revision: evaluation?.revision || 0,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || payload.detail?.message || `บันทึกผลนำร่องไม่สำเร็จ (${response.status})`);
      setEvaluation(payload.evaluation);
      setSaved(true);
      onSaved?.();
    } catch (requestError) {
      setError(requestError.message || 'บันทึกผลนำร่องไม่สำเร็จ');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.pilotEditor} aria-labelledby="pilot-evaluation-title">
      <h3 id="pilot-evaluation-title">ผลทดสอบนำร่อง · บันทึกผลรายการนี้</h3>
      <p>ผลนี้จะบันทึกเป็น ground truth scope นำร่องและไม่แก้ไขผลทำนายเดิม</p>
      {loading ? <div className={styles.pilotState}>กำลังโหลดข้อมูลทดสอบนำร่อง…</div> : (
        <form className={styles.pilotForm} onSubmit={submit}>
          <label>ภาพจับได้ถูกต้อง<select value={validCapture} onChange={(event) => {
            setValidCapture(event.target.value);
            if (event.target.value === 'no') { setCategory(''); setSubtype(''); setBinNumber(''); }
          }}><option value="">เลือก</option><option value="yes">ใช่</option><option value="no">ไม่ใช่</option></select></label>
          <label>ประเภทขยะที่คาดหวัง<select value={category} disabled={validCapture !== 'yes'} onChange={(event) => setCategory(event.target.value)}><option value="">เลือก</option>{CATEGORIES.map((item) => <option key={item} value={item}>{CATEGORY_NAMES[item]} ({item})</option>)}</select></label>
          <label>ชนิดวัสดุที่คาดหวัง · ไม่บังคับ<select value={subtype} disabled={validCapture !== 'yes'} onChange={(event) => setSubtype(event.target.value)}><option value="">ไม่ระบุ</option>{MATERIAL_SUBTYPES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label>หมายเลขถังที่คาดหวัง<select value={binNumber} disabled={validCapture !== 'yes'} onChange={(event) => setBinNumber(event.target.value)}><option value="">เลือกหรือเว้นว่าง</option>{bins.map((bin) => <option key={bin.number} value={bin.number}>ถัง {bin.number} · {bin.name}</option>)}</select></label>
          <label className={styles.pilotNote}>บันทึกเพิ่มเติม · ไม่บังคับ<textarea value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} rows={2} /></label>
          <div className={styles.pilotSubmitRow}><button type="submit" className={styles.pilotSubmit} disabled={saving || loading}>{saving ? 'กำลังบันทึก…' : evaluation ? 'บันทึกฉบับแก้ไข' : 'บันทึกผลทดสอบนำร่อง'}</button>{evaluation && <span>บันทึกแล้ว · ฉบับที่ {evaluation.revision}</span>}{saved && <span role="status">บันทึกผลทดสอบนำร่องแล้ว</span>}</div>
          {error && <div className={styles.error} role="alert">{error}</div>}
        </form>
      )}
    </section>
  );
}

function PilotMetricsPanel({ metrics, error, loading }) {
  return (
    <section className={styles.pilotPanel} aria-labelledby="pilot-metrics-title">
      <div className={styles.pilotPanelHeader}><div><p className={styles.eyebrow}>INTERNAL · PILOT EVALUATION</p><h2 id="pilot-metrics-title">ผลทดสอบนำร่อง</h2></div><span>อิงเฉพาะรายการที่บันทึก scope นำร่อง</span></div>
      <p className={styles.pilotDisclaimer}>ผลทดสอบนำร่องจากการสแกน 20–30 ครั้งใช้ตรวจสอบเบื้องต้นเท่านั้น ไม่ใช่ข้อกล่าวอ้างความแม่นยำสำหรับระบบ production</p>
      {loading && !metrics && <div className={styles.pilotState}>กำลังคำนวณผลทดสอบนำร่อง…</div>}
      {error && <div className={styles.error} role="alert">{error}</div>}
      {metrics && <>
        <div className={styles.pilotMetrics}>
          <PilotMetric label="จำนวนสแกนที่บันทึกผล" value={metrics.attempted_scans} />
          <PilotMetric label="ภาพจับไม่ถูกต้อง" value={metrics.invalid_captures} />
          <PilotMetric label="ภาพจับถูกต้อง" value={metrics.valid_captures} />
          <PilotMetric label="ทำนายเสร็จ" value={metrics.completed_predictions} />
          <PilotMetric label="อัตราทำนายเสร็จในภาพที่ใช้ได้" value={percent(metrics.valid_capture_completion_rate)} />
          <PilotMetric label="ความถูกต้องประเภทขยะ" value={formatAccuracy(metrics.classification_accuracy)} />
          <PilotMetric label="ความถูกต้องหมายเลขถัง" value={formatAccuracy(metrics.bin_accuracy)} />
          <PilotMetric label="ความถูกต้องชนิดวัสดุ" value={formatAccuracy(metrics.subtype_accuracy)} />
          <PilotMetric label="หมดเวลาหรือ API error" value={metrics.api_error_count} />
          <PilotMetric label="หมดเวลา" value={metrics.timeout_count} />
          <PilotMetric label={`ความมั่นใจตั้งแต่ ${percent(metrics.threshold)} ขึ้นไป`} value={metrics.confidence_breakdown.at_or_above_threshold} />
          <PilotMetric label={`ความมั่นใจต่ำกว่า ${percent(metrics.threshold)}`} value={metrics.confidence_breakdown.below_threshold} />
          <PilotMetric label="เวลาอนุมานมัธยฐาน (ms)" value={metrics.completed_inference_latency_ms.median ?? '—'} />
          <PilotMetric label="เวลาอนุมาน P95 (ms)" value={metrics.completed_inference_latency_ms.p95 ?? '—'} />
        </div>
        <p className={styles.pilotDenominator}>ข้อผิดพลาดการจำแนก: {metrics.classification_error_count} · latency นับเฉพาะ prediction ที่เสร็จสมบูรณ์</p>
      </>}
    </section>
  );
}

export default function ScanHistory({ pilotEvaluationEnabled = false }) {
  const [scans, setScans] = useState([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState({ status: '', waste_category: '', bin_number: '', needs_glass_pet_review: false });
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [pilotMetrics, setPilotMetrics] = useState(null);
  const [pilotMetricsError, setPilotMetricsError] = useState('');
  const [pilotMetricsLoading, setPilotMetricsLoading] = useState(false);

  const loadPilotMetrics = useCallback(async () => {
    if (!pilotEvaluationEnabled) return;
    setPilotMetricsLoading(true);
    setPilotMetricsError('');
    try {
      const response = await fetch('/api/scan-history/pilot/metrics', { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || `โหลดผลทดสอบนำร่องไม่สำเร็จ (${response.status})`);
      setPilotMetrics(payload);
    } catch (requestError) {
      setPilotMetricsError(requestError.message || 'โหลดผลทดสอบนำร่องไม่สำเร็จ');
    } finally {
      setPilotMetricsLoading(false);
    }
  }, [pilotEvaluationEnabled]);

  const loadScans = useCallback(async ({ append = false, offset = 0 } = {}) => {
    if (append) setLoadingMore(true);
    else {
      setLoading(true);
      setScans([]);
      setTotal(0);
    }
    setError('');
    const startedAt = performance.now();
    const query = new URLSearchParams({ limit: '20', offset: String(offset) });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== '' && value !== false) query.set(key, String(value));
    });
    try {
      const response = await fetch(`/api/scan-history?${query}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 404 ? 'เปิด Scan History ไม่ได้ ตรวจการตั้งค่า local/internal' : `โหลดรายการไม่สำเร็จ (${response.status})`);
      const payload = await response.json();
      const items = Array.isArray(payload.items) ? payload.items : [];
      setScans((current) => append ? [...current, ...items] : items);
      setTotal(Number(payload.total) || 0);
      if (process.env.NODE_ENV === 'development') {
        console.info('[ScanHistory] metadata request completed', {
          elapsedMs: Math.round(performance.now() - startedAt),
          count: items.length,
          offset,
          total: Number(payload.total) || 0,
        });
      }
    } catch (requestError) {
      setError(requestError.message || 'โหลดรายการไม่สำเร็จ');
      if (!append) {
        setScans([]);
        setTotal(0);
      }
      if (process.env.NODE_ENV === 'development') {
        console.error('[ScanHistory] metadata request failed', {
          elapsedMs: Math.round(performance.now() - startedAt),
          message: requestError.message,
        });
      }
    } finally {
      if (append) setLoadingMore(false);
      else setLoading(false);
    }
  }, [filters]);

  useEffect(() => { loadScans(); }, [loadScans]);
  useEffect(() => { if (pilotEvaluationEnabled) loadPilotMetrics(); }, [pilotEvaluationEnabled, loadPilotMetrics]);

  const binOptions = useMemo(() => [...new Set(scans.map((scan) => scan.bin_number).filter((value) => value != null).map(String))].sort((a, b) => Number(a) - Number(b)), [scans]);

  function changeFilter(name, value) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  return (
    <div className={styles.scrollArea} tabIndex={0} aria-label="Scan History page">
    <section className={styles.page}>
      <header className={styles.header}>
        <div><p className={styles.eyebrow}>INTERNAL · READ ONLY</p><h1>Scan History</h1><p>รายการสแกนล่าสุดจาก persistent storage · โหลดครั้งละ 20 รายการ</p></div>
        <button type="button" className={styles.refresh} onClick={() => loadScans()} disabled={loading || loadingMore}>รีเฟรช</button>
      </header>

      <div className={styles.filters} aria-label="ตัวกรองรายการสแกน">
        <label>สถานะ<select value={filters.status} onChange={(event) => changeFilter('status', event.target.value)}><option value="">ทั้งหมด</option><option value="success">success</option><option value="unknown">unknown</option><option value="missing_mapping">missing_mapping</option><option value="api_error">api_error</option></select></label>
        <label>ประเภทขยะ<select value={filters.waste_category} onChange={(event) => changeFilter('waste_category', event.target.value)}><option value="">ทั้งหมด</option>{CATEGORIES.map((category) => <option key={category} value={category}>{CATEGORY_NAMES[category]} ({category})</option>)}</select></label>
        <label>ถังหมายเลข<select value={filters.bin_number} onChange={(event) => changeFilter('bin_number', event.target.value)}><option value="">ทั้งหมด</option>{binOptions.map((number) => <option key={number} value={number}>ถัง {number}</option>)}</select></label>
        <label className={styles.check}><input type="checkbox" checked={filters.needs_glass_pet_review} onChange={(event) => changeFilter('needs_glass_pet_review', event.target.checked)} /> ต้องตรวจ glass / PET</label>
      </div>

      {pilotEvaluationEnabled && <PilotMetricsPanel metrics={pilotMetrics} error={pilotMetricsError} loading={pilotMetricsLoading} />}

      <div className={styles.count} aria-live="polite">{loading ? 'กำลังโหลดรายการสแกน…' : `${scans.length} รายการ จาก ${total} รายการที่ตรงกับตัวกรอง`}</div>
      {error && <div className={styles.error} role="alert">{error}</div>}
      {loading && scans.length === 0 && <div className={styles.loading} role="status">กำลังอ่านข้อมูลจาก scan storage…</div>}
      {!loading && !error && scans.length === 0 && <div className={styles.empty}>ยังไม่มีรายการสแกนที่ตรงกับตัวกรอง</div>}

      <div className={styles.list}>
        {scans.map((scan) => <button type="button" className={styles.row} key={scan.request_id} onClick={() => setSelected(scan)}>
          <div className={styles.thumb}>{scan.image_available ? <ScanImage requestId={scan.request_id} kind="thumbnail" alt={`Captured ${scan.object_name_th || 'scan'}`} loading="lazy" /> : <span>ภาพไม่มี</span>}</div>
          <div className={styles.summary}>
            <div className={styles.rowTop}><strong>{scan.object_name_th || 'ไม่ระบุวัตถุ'}</strong><ScanChips scan={scan} /></div>
            <div className={styles.meta}><span>{scan.request_id}</span><span>{dateLabel(scan.timestamp)}</span></div>
            <div className={styles.details}>
              <span>ประเภท: <b>{scan.waste_category || scan.normalized_waste_category || '—'}</b></span>
              <span>ความมั่นใจ: <b>{percent(scan.confidence)}</b></span>
              <span>ถัง: <b>{scan.bin_number ?? '—'} {scan.bin_name || ''}</b></span>
              {scan.review_flags?.glass_pet_review_required && <span>ช่องว่าง confidence: <b>{percent(scan.review_flags.glass_pet_confidence_gap)}</b></span>}
            </div>
            <CandidateList candidates={scan.top_candidates} />
          </div>
        </button>)}
      </div>

      {!loading && scans.length < total && <div className={styles.loadMoreWrap}><button type="button" className={styles.loadMore} onClick={() => loadScans({ append: true, offset: scans.length })} disabled={loadingMore}>{loadingMore ? 'กำลังโหลดรายการเก่า…' : 'โหลดรายการเก่าเพิ่ม'}</button></div>}

      {selected && <div className={styles.backdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}>
        <section className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="scan-detail-title">
          <header className={styles.modalHeader}><div><p className={styles.eyebrow}>SCAN DETAIL</p><h2 id="scan-detail-title">{selected.object_name_th || selected.request_id}</h2></div><button type="button" aria-label="ปิดรายละเอียด" onClick={() => setSelected(null)}>ปิด</button></header>
          {selected.image_available ? <ScanImage key={selected.request_id} requestId={selected.request_id} kind="detail" className={styles.largeImage} alt={`Captured ${selected.object_name_th || 'scan'}`} loading="eager" /> : <div className={styles.noImage}>ไม่มีภาพสำหรับรายการนี้</div>}
          <ScanChips scan={selected} />
          <p className={styles.request}>{selected.request_id}</p>
          {pilotEvaluationEnabled && <PilotEvaluationEditor key={selected.request_id} scan={selected} onSaved={loadPilotMetrics} />}
          <h3>ข้อมูลที่บันทึกไว้ครบถ้วน</h3>
          <pre className={styles.record}>{JSON.stringify(selected, null, 2)}</pre>
        </section>
      </div>}
    </section>
    </div>
  );
}
