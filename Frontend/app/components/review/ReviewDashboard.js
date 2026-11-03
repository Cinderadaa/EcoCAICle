'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  buildReviewImageUrl,
  formatCategory,
  formatMetric,
  REVIEW_CATEGORIES,
} from '@/lib/reviewDashboard';
import styles from '../../review/review.module.css';

const EMPTY_METRICS = {
  total_scans: 0,
  pending_reviews: 0,
  reviewed_samples: 0,
  unreviewable_samples: 0,
  classification_accuracy: { value: null },
  bin_recommendation_accuracy: { value: null },
  latency_ms: { total: { mean: null, p50: null, p95: null } },
  error_counts: { api_errors: 0 },
  categories: REVIEW_CATEGORIES,
  confusion_matrix: { categories: REVIEW_CATEGORIES, matrix: {} },
  per_category: {},
};

function responseError(body, fallback) {
  return body?.detail?.message || body?.message || body?.detail || fallback;
}

async function readJson(url, options) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  let body = null;
  try { body = await response.json(); } catch { /* handled below */ }
  if (!response.ok) throw new Error(responseError(body, `Request failed (${response.status})`));
  return body;
}

function Field({ label, children }) {
  return <label className={styles.field}><span>{label}</span>{children}</label>;
}

function Metric({ label, value, warning }) {
  return <div className={styles.metric}>
    <span className={styles.metricLabel}>{label}</span>
    <strong className={styles.metricValue}>{value}</strong>
    {warning && <div className={styles.warning}>ตัวอย่างยังมีจำนวนน้อย</div>}
  </div>;
}

function Fact({ label, value }) {
  return <div className={styles.fact}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>;
}

function Status({ value, error }) {
  const className = error ? styles.error : `${styles.status} ${value || ''}`;
  return <span className={className}>{value || '—'}</span>;
}

export default function ReviewDashboard({ mode = 'queue' }) {
  const [metrics, setMetrics] = useState(EMPTY_METRICS);
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [filters, setFilters] = useState({
    review_status: 'pending',
    predicted_category: '',
    location_id: '',
    date_from: '',
    date_to: '',
    error_type: 'all',
  });
  const [reviewerId, setReviewerId] = useState('');
  const [category, setCategory] = useState('paper');
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const stored = window.localStorage.getItem('ecocaicle.review.reviewer_id');
    if (stored) setReviewerId(stored);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (mode === 'queue') {
        Object.entries(filters).forEach(([key, value]) => {
          if (key !== 'error_type' && value) query.set(key, value);
        });
        query.set('limit', '100');
      } else if (filters.error_type && filters.error_type !== 'all') {
        query.set('error_type', filters.error_type);
      }
      const [metricBody, listBody] = await Promise.all([
        readJson('/api/review/metrics'),
        readJson(`/api/review/${mode === 'queue' ? 'queue' : 'errors'}?${query.toString()}`),
      ]);
      setMetrics(metricBody || EMPTY_METRICS);
      setItems(listBody?.items || []);
      setSelectedId((current) => (listBody?.items || []).some((item) => item.request_id === current)
        ? current
        : listBody?.items?.[0]?.request_id || null);
    } catch (loadError) {
      setError(loadError.message || 'ไม่สามารถโหลดข้อมูลผู้ตรวจสอบได้');
    } finally {
      setLoading(false);
    }
  }, [filters, mode]);

  useEffect(() => { load(); }, [load]);

  const selected = useMemo(
    () => items.find((item) => item.request_id === selectedId) || null,
    [items, selectedId],
  );

  useEffect(() => {
    if (selected?.review?.ground_truth_waste_category) {
      setCategory(selected.review.ground_truth_waste_category);
    } else if (selected?.waste_category && selected.waste_category !== 'unknown') {
      setCategory(selected.waste_category);
    }
    setNote(selected?.review?.note || '');
  }, [selected]);

  function updateFilter(name, value) {
    setFilters((current) => ({ ...current, [name]: value }));
  }

  async function submit(action) {
    if (!selected) return;
    const cleanReviewerId = reviewerId.trim();
    if (!cleanReviewerId) {
      setError('กรุณาระบุรหัสผู้ตรวจสอบก่อนบันทึก');
      return;
    }
    window.localStorage.setItem('ecocaicle.review.reviewer_id', cleanReviewerId);
    setSubmitting(true);
    setError('');
    try {
      await readJson(`/api/review/annotations/${encodeURIComponent(selected.request_id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reviewer_id: cleanReviewerId,
          review_action: action,
          category: action === 'label' ? category : null,
          note: note.trim() || null,
          expected_revision: selected.review?.revision || 0,
        }),
      });
      setNote('');
      await load();
    } catch (submitError) {
      setError(submitError.message || 'บันทึกการตรวจสอบไม่สำเร็จ กรุณาโหลดข้อมูลใหม่');
    } finally {
      setSubmitting(false);
    }
  }

  const sampleWarning = Boolean(metrics.sample_size_warning);
  const displayCategories = metrics.categories?.length ? metrics.categories : REVIEW_CATEGORIES;
  const matrix = metrics.confusion_matrix?.matrix || {};
  const labelAction = selected && category === selected.waste_category ? 'confirm' : 'correct';

  return <div className={styles.reviewShell}>
    <header className={styles.header}>
      <div>
        <h1>EcoCAICle Review & Evaluation</h1>
        <p>Local-only ground truth review · location {metrics.location_id || '—'} · mapping {metrics.mapping_version || '—'}</p>
      </div>
      <nav className={styles.nav} aria-label="Review navigation">
        <Link href="/review">Review queue</Link>
        <Link href="/review/errors">Error explorer</Link>
        <Link href="/">Kiosk</Link>
      </nav>
    </header>

    <main className={styles.content}>
      <section className={styles.metrics} aria-label="Evaluation metrics">
        <Metric label="Total scans" value={metrics.total_scans ?? 0} />
        <Metric label="Pending reviews" value={metrics.pending_reviews ?? 0} />
        <Metric label="Reviewed samples" value={metrics.reviewed_samples ?? 0} warning={sampleWarning} />
        <Metric label="Classification accuracy" value={formatMetric(metrics.classification_accuracy?.value)} warning={sampleWarning} />
        <Metric label="Bin accuracy" value={formatMetric(metrics.bin_recommendation_accuracy?.value)} warning={sampleWarning} />
        <Metric label="API failures" value={metrics.error_counts?.api_errors ?? 0} />
      </section>

      <section className={styles.panel} style={{ marginBottom: 18 }}>
        <div className={styles.toolbar} style={{ margin: 0, padding: 0, border: 0, boxShadow: 'none' }}>
          {mode === 'queue' && <Field label="Review status"><select value={filters.review_status} onChange={(event) => updateFilter('review_status', event.target.value)}>
            <option value="all">All</option><option value="pending">Pending</option><option value="reviewed">Reviewed</option><option value="unreviewable">Unreviewable</option>
          </select></Field>}
          {mode === 'errors' && <Field label="Error type"><select value={filters.error_type} onChange={(event) => updateFilter('error_type', event.target.value)}>
            <option value="all">All errors</option><option value="classification_error">Classification</option><option value="mapping_error">Mapping</option><option value="classification_and_mapping_error">Classification + mapping</option><option value="api_error">API failures</option>
          </select></Field>}
          <Field label="Predicted category"><select value={filters.predicted_category} onChange={(event) => updateFilter('predicted_category', event.target.value)}>
            <option value="">All categories</option>{displayCategories.map((item) => <option key={item} value={item}>{formatCategory(item)}</option>)}
          </select></Field>
          <Field label="Location"><input value={filters.location_id} onChange={(event) => updateFilter('location_id', event.target.value)} placeholder="ICT-001" /></Field>
          <Field label="From"><input type="date" value={filters.date_from} onChange={(event) => updateFilter('date_from', event.target.value)} /></Field>
          <Field label="To"><input type="date" value={filters.date_to} onChange={(event) => updateFilter('date_to', event.target.value)} /></Field>
          <Field label="Reviewer ID"><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="reviewer.name" /></Field>
          <button className={`${styles.button} ${styles.secondary}`} type="button" onClick={load} disabled={loading}>Refresh</button>
        </div>
      </section>

      {error && <div className={styles.error} role="alert">{error}</div>}

      <div className={styles.grid}>
        <section className={styles.panel}>
          <h2>{mode === 'errors' ? 'Samples requiring analysis' : 'Review queue'} <span className={styles.subtle}>({items.length})</span></h2>
          {loading ? <div className={styles.empty}>Loading review data…</div> : items.length === 0 ? <div className={styles.empty}>No samples match the current filters.</div> : <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th>Time</th><th>Prediction</th><th>Status</th><th>Confidence</th><th>Bin</th></tr></thead>
              <tbody>{items.map((item) => <tr key={item.request_id} className={`${styles.clickable} ${item.request_id === selectedId ? styles.selected : ''}`} onClick={() => setSelectedId(item.request_id)}>
                <td>{String(item.timestamp || '—').replace('T', ' ').slice(0, 16)}</td>
                <td>{formatCategory(item.waste_category)}</td>
                <td><Status value={mode === 'errors' ? item.error?.error_type : item.review?.status} error={mode === 'errors' && item.error?.error_type === 'api_error'} /></td>
                <td>{formatMetric(item.confidence)}</td>
                <td>{item.bin_number ?? '—'} {item.bin_name ? `· ${item.bin_name}` : ''}</td>
              </tr>)}</tbody>
            </table>
          </div>}
        </section>

        <section className={styles.panel}>
          <h2>Sample detail</h2>
          {!selected ? <div className={styles.empty}>Select a scan to inspect its image and review fields.</div> : <div className={styles.detail}>
            <div>{selected.image_available && buildReviewImageUrl(selected.image_id) ? <img className={styles.image} src={buildReviewImageUrl(selected.image_id)} alt="Stored scan for review" /> : <div className={styles.imageMissing}>Image unavailable. This record can be marked unreviewable.</div>}</div>
            <div>
              <dl className={styles.facts}>
                <Fact label="Request ID" value={selected.request_id} />
                <Fact label="Timestamp" value={selected.timestamp} />
                <Fact label="Location" value={selected.location_id} />
                <Fact label="Object" value={selected.object_name_th} />
                <Fact label="Model category" value={formatCategory(selected.waste_category)} />
                <Fact label="Confidence" value={formatMetric(selected.confidence)} />
                <Fact label="Inference status" value={selected.status} />
                <Fact label="Classification status" value={selected.classification_status} />
                <Fact label="Recommended bin" value={selected.bin_number ? `${selected.bin_number} · ${selected.bin_name || ''}` : 'Unavailable'} />
                <Fact label="Expected bin" value={selected.expected_bin ? `${selected.expected_bin.bin_number} · ${selected.expected_bin.bin_name}` : '—'} />
                <Fact label="Food-scrap metadata" value={selected.has_food_scraps === true ? 'true' : selected.has_food_scraps === false ? 'false' : '—'} />
                <Fact label="Inference latency" value={selected.inference_latency_ms != null ? `${selected.inference_latency_ms} ms` : '—'} />
                <Fact label="Total latency" value={selected.total_latency_ms != null ? `${selected.total_latency_ms} ms` : '—'} />
                <Fact label="Review status" value={selected.review?.status} />
                <Fact label="Mapping version" value={selected.mapping_version} />
              </dl>
              {mode === 'errors' && <div className={styles.subtle}>Human truth: {formatCategory(selected.error?.actual_category)} · {selected.error?.error_type}</div>}
              <div className={styles.reviewForm}>
                <Field label="Canonical ground-truth category"><select value={category} onChange={(event) => setCategory(event.target.value)}>
                  {displayCategories.map((item) => <option key={item} value={item}>{formatCategory(item)}</option>)}
                </select></Field>
                <label className={styles.field} style={{ marginTop: 10 }}><span>Review note (optional)</span><textarea className={styles.note} rows="3" value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} /></label>
                <div className={styles.actions}>
                  <button className={styles.button} type="button" disabled={submitting || selected.review?.status === 'unreviewable'} onClick={() => submit('label')}>{labelAction === 'confirm' ? 'Confirm prediction' : 'Save correction'}</button>
                  <button className={`${styles.button} ${styles.danger}`} type="button" disabled={submitting} onClick={() => submit('unreviewable')}>Mark unreviewable</button>
                  <button className={`${styles.button} ${styles.secondary}`} type="button" disabled={submitting} onClick={() => submit('skip')}>Skip</button>
                </div>
              </div>
            </div>
          </div>}
        </section>
      </div>

      <section className={styles.panel} style={{ marginTop: 18 }}>
        <h2>Confusion matrix</h2>
        {metrics.reviewed_samples === 0 ? <div className={styles.empty}>No reviewed samples yet.</div> : <div className={styles.matrix}><table className={styles.table}><thead><tr><th>Actual \ Predicted</th>{displayCategories.map((categoryName) => <th key={categoryName}>{formatCategory(categoryName)}</th>)}</tr></thead><tbody>{displayCategories.map((actual) => <tr key={actual}><th>{formatCategory(actual)}</th>{displayCategories.map((predicted) => <td key={predicted}>{matrix?.[actual]?.[predicted] || 0}</td>)}</tr>)}</tbody></table></div>}
        <p className={styles.subtle}>Latency total: mean {formatMetric(metrics.latency_ms?.total?.mean)} ms · P50 {formatMetric(metrics.latency_ms?.total?.p50)} ms · P95 {formatMetric(metrics.latency_ms?.total?.p95)} ms. API failures are reported separately.</p>
      </section>
    </main>
  </div>;
}
