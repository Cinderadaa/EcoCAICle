'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Image from 'next/image';
import styles from './scan-history.module.css';
import { getScanStatusChips } from '../../src/lib/scanHistoryStatus.js';
import {
  POC_DEMO_LABEL,
  POC_GROUND_TRUTH_OPTIONS,
  POC_VERIFIED_LABEL,
  buildPocQuery,
  correctnessLabel,
  normalizePocItem,
  pocImageUrl,
  pocMetricCards,
} from '../../src/lib/pocScanHistory.js';

const CATEGORIES = ['general', 'pet_bottle', 'plastic', 'paper', 'glass', 'metal', 'ewaste', 'fuel', 'hazardous', 'infectious', 'unknown'];
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

function classificationMessage(scan) {
  if (scan.classification_reason === 'no_object_visible') return 'ไม่พบวัตถุสำหรับคัดแยกในภาพ';
  if (scan.classification_reason === 'visible_object_unclassified') return 'พบวัตถุ แต่ยังระบุประเภทขยะไม่ได้';
  return null;
}

function ScanImage({ requestId, alt, className, kind, loading = 'lazy', src = imageUrl(requestId) }) {
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
        src={src}
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
      src={src}
      alt={alt}
      loading={loading}
      decoding="async"
      onLoad={() => logImage('loaded')}
      onError={() => logImage('failed')}
    />
  );
}

function ScanChips({ scan }) {
  const chips = getScanStatusChips(scan);
  return <div className={styles.chips}>{chips.map(([label, tone], index) => <span className={`${styles.chip} ${styles[tone]}`} key={`${label}-${index}`}>{label}</span>)}</div>;
}

function PocPanel({ summary }) {
  const cards = pocMetricCards(summary);
  return (
    <section className={styles.pocPanel} aria-label="POC historical verified metrics">
      <p className={styles.pocNotice}>
        POC historical verified · ข้อมูลเก่าแบบ derived และอ่านอย่างเดียว · Accuracy, Macro F1 และ Bin accuracy คำนวณจาก human_verified เท่านั้น · {POC_DEMO_LABEL}
      </p>
      <dl className={styles.pocMetrics}>
        {cards.map((card) => (
          <div key={card.label}>
            <dt>{card.label}</dt>
            <dd>{card.value}</dd>
            <small>{card.hint}</small>
          </div>
        ))}
      </dl>
      {summary && <p className={styles.muted}>คัดได้ {summary.pool?.eligible ?? 0} รายการ · ไม่รวม reviewer unreviewable {summary.excluded?.reviewer_unreviewable ?? 0} รายการ</p>}
    </section>
  );
}

function PocDetails({ poc }) {
  return (
    <div className={styles.pocDetails}>
      <span className={poc.demo_only ? styles.demoBadge : styles.verifiedBadge}>{poc.demo_only ? POC_DEMO_LABEL : POC_VERIFIED_LABEL}</span>
      <span>ประเภทที่ยืนยัน: <b>{poc.ground_truth_waste_category || '—'}</b> ({correctnessLabel(poc.classification_correct)})</span>
      <span>ถังที่คาดหวัง: <b>{poc.expected_bin_number ?? '—'}</b> ({correctnessLabel(poc.bin_correct)})</span>
      <span>เหตุผล mapping: <b>{poc.mapping_reason || '—'}</b></span>
      <span>ชนิดย่อย: <b>{poc.material_subtype || '—'}</b></span>
    </div>
  );
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

export default function ScanHistory() {
  const [scans, setScans] = useState([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState({ dataset: '', ground_truth_source: '', status: '', waste_category: '', bin_number: '', needs_glass_pet_review: false });
  const [pocSummary, setPocSummary] = useState(null);
  const pocMode = filters.dataset === 'poc';
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const loadScans = useCallback(async ({ append = false, offset = 0 } = {}) => {
    if (append) setLoadingMore(true);
    else {
      setLoading(true);
      setScans([]);
      setTotal(0);
    }
    setError('');
    const startedAt = performance.now();
    const query = pocMode
      ? buildPocQuery(filters, { limit: 20, offset })
      : new URLSearchParams({ limit: '20', offset: String(offset) });
    if (!pocMode) {
      Object.entries(filters).forEach(([key, value]) => {
        if (key === 'dataset' || key === 'ground_truth_source') return;
        if (value !== '' && value !== false) query.set(key, String(value));
      });
    }
    try {
      const response = await fetch(pocMode ? `/api/scan-history/poc?${query}` : `/api/scan-history?${query}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 404 ? 'เปิด Scan History ไม่ได้ ตรวจการตั้งค่า local/internal' : `โหลดรายการไม่สำเร็จ (${response.status})`);
      const payload = await response.json();
      const items = Array.isArray(payload.items) ? payload.items.map((item) => (pocMode ? normalizePocItem(item) : item)) : [];
      setScans((current) => append ? [...current, ...items] : items);
      setTotal(Number(payload.total) || 0);
      setPocSummary(pocMode ? payload.summary || null : null);
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
      setPocSummary(null);
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
  }, [filters, pocMode]);

  useEffect(() => { loadScans(); }, [loadScans]);

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
        <label>ชุดข้อมูล<select value={filters.dataset} onChange={(event) => changeFilter('dataset', event.target.value)}><option value="">Scan ปัจจุบัน</option><option value="poc">POC historical verified</option></select></label>
        {pocMode && <label>ประเภทข้อมูล POC<select value={filters.ground_truth_source} onChange={(event) => changeFilter('ground_truth_source', event.target.value)}><option value="">ทั้งหมด</option>{POC_GROUND_TRUTH_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>}
        {!pocMode && <label>สถานะ<select value={filters.status} onChange={(event) => changeFilter('status', event.target.value)}><option value="">ทั้งหมด</option><option value="success">success</option><option value="unknown">unknown</option><option value="missing_mapping">missing_mapping</option><option value="api_error">api_error</option></select></label>}
        <label>ประเภทขยะ<select value={filters.waste_category} onChange={(event) => changeFilter('waste_category', event.target.value)}><option value="">ทั้งหมด</option>{CATEGORIES.map((category) => <option key={category} value={category}>{CATEGORY_NAMES[category]} ({category})</option>)}</select></label>
        <label>ถังหมายเลข<select value={filters.bin_number} onChange={(event) => changeFilter('bin_number', event.target.value)}><option value="">ทั้งหมด</option>{binOptions.map((number) => <option key={number} value={number}>ถัง {number}</option>)}</select></label>
        {!pocMode && <label className={styles.check}><input type="checkbox" checked={filters.needs_glass_pet_review} onChange={(event) => changeFilter('needs_glass_pet_review', event.target.checked)} /> ต้องตรวจ glass / PET</label>}
      </div>

      {pocMode && <PocPanel summary={pocSummary} />}

      <div className={styles.count} aria-live="polite">{loading ? 'กำลังโหลดรายการสแกน…' : `${scans.length} รายการ จาก ${total} รายการที่ตรงกับตัวกรอง`}</div>
      {error && <div className={styles.error} role="alert">{error}</div>}
      {loading && scans.length === 0 && <div className={styles.loading} role="status">กำลังอ่านข้อมูลจาก scan storage…</div>}
      {!loading && !error && scans.length === 0 && <div className={styles.empty}>ยังไม่มีรายการสแกนที่ตรงกับตัวกรอง</div>}

      <div className={styles.list}>
        {scans.map((scan) => <button type="button" className={styles.row} key={scan.request_id} onClick={() => setSelected(scan)}>
          <div className={styles.thumb}>{scan.image_available ? <ScanImage requestId={scan.request_id} kind="thumbnail" alt={`Captured ${scan.object_name_th || 'scan'}`} loading="lazy" src={scan.poc ? pocImageUrl(scan.request_id) : undefined} /> : <span>ภาพไม่มี</span>}</div>
          <div className={styles.summary}>
            <div className={styles.rowTop}><strong>{scan.object_name_th || 'ไม่ระบุวัตถุ'}</strong><ScanChips scan={scan} /></div>
            {classificationMessage(scan) && <p className={styles.classificationMessage}>{classificationMessage(scan)}</p>}
            <div className={styles.meta}><span>{scan.request_id}</span><span>{dateLabel(scan.timestamp)}</span></div>
            <div className={styles.details}>
              <span>ประเภท: <b>{scan.waste_category || scan.normalized_waste_category || '—'}</b></span>
              <span>ความมั่นใจ: <b>{percent(scan.confidence)}</b></span>
              <span>ถัง: <b>{scan.bin_number ?? '—'} {scan.bin_name || ''}</b></span>
              {scan.review_flags?.glass_pet_review_required && <span>ช่องว่าง confidence: <b>{percent(scan.review_flags.glass_pet_confidence_gap)}</b></span>}
            </div>
            <CandidateList candidates={scan.top_candidates} />
            {scan.poc && <PocDetails poc={scan.poc} />}
          </div>
        </button>)}
      </div>

      {!loading && scans.length < total && <div className={styles.loadMoreWrap}><button type="button" className={styles.loadMore} onClick={() => loadScans({ append: true, offset: scans.length })} disabled={loadingMore}>{loadingMore ? 'กำลังโหลดรายการเก่า…' : 'โหลดรายการเก่าเพิ่ม'}</button></div>}

      {selected && <div className={styles.backdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}>
        <section className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="scan-detail-title">
          <header className={styles.modalHeader}><div><p className={styles.eyebrow}>SCAN DETAIL</p><h2 id="scan-detail-title">{selected.object_name_th || selected.request_id}</h2></div><button type="button" aria-label="ปิดรายละเอียด" onClick={() => setSelected(null)}>ปิด</button></header>
          {selected.image_available ? <ScanImage key={selected.request_id} requestId={selected.request_id} kind="detail" className={styles.largeImage} alt={`Captured ${selected.object_name_th || 'scan'}`} loading="eager" src={selected.poc ? pocImageUrl(selected.request_id) : undefined} /> : <div className={styles.noImage}>ไม่มีภาพสำหรับรายการนี้</div>}
          <ScanChips scan={selected} />
          {selected.poc && <PocDetails poc={selected.poc} />}
          {classificationMessage(selected) && <p className={styles.classificationMessage}>{classificationMessage(selected)}</p>}
          <p className={styles.request}>{selected.request_id}</p>
          <dl className={styles.detailGrid}>
            <dt>ตรวจพบวัตถุ</dt><dd>{selected.visible_object_detected === true ? 'ใช่' : selected.visible_object_detected === false ? 'ไม่' : '—'}</dd>
            <dt>เหตุผลการจัดประเภท</dt><dd>{selected.classification_reason || '—'}</dd>
          </dl>
          <h3>ข้อมูลที่บันทึกไว้ครบถ้วน</h3>
          <pre className={styles.record}>{JSON.stringify(selected, null, 2)}</pre>
        </section>
      </div>}
    </section>
    </div>
  );
}
