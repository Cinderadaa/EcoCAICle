// Helpers for the retrospective "POC historical verified" view of Scan History.
// Only human_verified rows feed accuracy figures; historical_prediction_only rows
// are demonstration data and must always carry the demo-only label.

export const POC_DEMO_LABEL = 'ใช้สาธิตเท่านั้น ไม่ใช้คำนวณ accuracy';
export const POC_VERIFIED_LABEL = 'ตรวจโดยคนแล้ว';
export const POC_GROUND_TRUTH_OPTIONS = [
  { value: 'human_verified', label: 'ตรวจโดยคนแล้ว' },
  { value: 'historical_prediction_only', label: 'ใช้สาธิตเท่านั้น' },
];

export function isDemoOnly(item) {
  return item?.ground_truth_source !== 'human_verified';
}

export function pocImageUrl(requestId) {
  return `/api/scan-history/poc/${encodeURIComponent(requestId)}/image`;
}

// Flatten a POC item into the shape the existing Scan History rows render, and keep
// the POC-specific fields under `poc` so they cannot be mistaken for live scan data.
export function normalizePocItem(item = {}) {
  const prediction = item.historical_prediction || {};
  const mapping = item.historical_mapping || {};
  return {
    request_id: item.request_id,
    timestamp: item.timestamp,
    object_name_th: prediction.object_name_th,
    waste_category: prediction.waste_category,
    confidence: prediction.confidence,
    top_candidates: Array.isArray(prediction.top_candidates) ? prediction.top_candidates : [],
    bin_number: mapping.bin_number,
    bin_name: mapping.bin_name,
    status: 'success',
    image_available: true,
    poc: {
      source_label: item.source_label ?? null,
      ground_truth_source: item.ground_truth_source,
      demo_only: isDemoOnly(item),
      ground_truth_waste_category: item.ground_truth_waste_category ?? null,
      expected_bin_number: item.expected_bin_number ?? null,
      bin_ground_truth_status: item.bin_ground_truth_status ?? null,
      classification_correct: item.classification_correct ?? null,
      bin_correct: item.bin_correct ?? null,
      material_subtype: prediction.material_subtype ?? null,
      mapping_version: mapping.mapping_version ?? null,
      mapping_reason: mapping.mapping_reason ?? null,
      review_action: item.review?.review_action ?? null,
      reviewer_id: item.review?.reviewer_id ?? null,
      reviewed_at: item.review?.reviewed_at ?? null,
    },
  };
}

export function formatPercent(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—';
}

export function formatScore(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : '—';
}

export function correctnessLabel(value) {
  if (value === true) return 'ถูกต้อง';
  if (value === false) return 'ไม่ถูกต้อง';
  return '—';
}

// Metric cards for the POC banner. Every value is `—` until human_verified labels exist.
export function pocMetricCards(summary) {
  const metrics = summary?.metrics || {};
  const pool = summary?.pool || {};
  const outcomes = summary?.outcome_totals || {};
  const classificationN = metrics.classification_sample_size ?? 0;
  return [
    { label: 'Accuracy', value: formatPercent(metrics.accuracy), hint: `n=${classificationN} (human_verified)` },
    { label: 'Macro F1', value: formatScore(metrics.macro_f1), hint: 'human_verified เท่านั้น' },
    { label: 'Bin accuracy', value: formatPercent(metrics.bin_accuracy), hint: `n=${metrics.bin_sample_size ?? 0} (plastic ไม่มี bin ที่ยืนยันได้)` },
    { label: 'รอ reviewer ยืนยัน', value: String(summary?.review_needed ?? '—'), hint: 'demo-only ยังไม่มี label จากคน' },
    { label: 'human_verified', value: String(pool.human_verified ?? '—'), hint: 'ใช้คำนวณ metric' },
    { label: 'ข้อมูลเก่า success / timeout / error', value: `${outcomes.success ?? '—'} / ${outcomes.timeout ?? '—'} / ${outcomes.error ?? '—'}`, hint: 'แยกจาก accuracy' },
  ];
}

export function buildPocQuery(filters = {}, { limit = 20, offset = 0 } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  for (const key of ['ground_truth_source', 'waste_category', 'bin_number']) {
    const value = filters[key];
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  return query;
}
