import assert from 'node:assert/strict';
import test from 'node:test';
import {
  POC_DEMO_LABEL,
  buildPocQuery,
  correctnessLabel,
  formatPercent,
  isDemoOnly,
  normalizePocItem,
  pocImageUrl,
  pocMetricCards,
} from './pocScanHistory.js';

const demoItem = {
  request_id: 'req-demo',
  timestamp: '2026-10-21T07:00:00Z',
  ground_truth_source: 'historical_prediction_only',
  ground_truth_waste_category: null,
  expected_bin_number: 4,
  bin_ground_truth_status: null,
  classification_correct: null,
  bin_correct: null,
  demo_only: true,
  historical_prediction: { object_name_th: 'กระดาษ', waste_category: 'paper', confidence: 0.9, top_candidates: [{ object_name_th: 'กล่อง' }], material_subtype: null },
  historical_mapping: { bin_number: 4, bin_name: 'paper', mapping_version: 'ICT-10-bin-v2', mapping_reason: null },
  review: null,
};

test('historical-only rows are demo-only and carry the non-accuracy label', () => {
  assert.equal(isDemoOnly(demoItem), true);
  assert.equal(POC_DEMO_LABEL, 'ใช้สาธิตเท่านั้น ไม่ใช้คำนวณ accuracy');
  const normalized = normalizePocItem(demoItem);
  assert.equal(normalized.poc.demo_only, true);
  assert.equal(normalized.poc.classification_correct, null);
  assert.equal(normalized.waste_category, 'paper');
  assert.equal(normalized.bin_number, 4);
});

test('human_verified rows keep reviewer correctness and ground truth', () => {
  const verified = {
    ...demoItem,
    ground_truth_source: 'human_verified',
    ground_truth_waste_category: 'glass',
    classification_correct: false,
    bin_correct: false,
    demo_only: false,
    review: { reviewer_id: 'reviewer-1', reviewed_at: '2026-10-28T06:00:00Z', review_action: 'correct' },
  };
  const normalized = normalizePocItem(verified);
  assert.equal(isDemoOnly(verified), false);
  assert.equal(normalized.poc.ground_truth_waste_category, 'glass');
  assert.equal(normalized.poc.review_action, 'correct');
  assert.equal(correctnessLabel(normalized.poc.classification_correct), 'ไม่ถูกต้อง');
});

test('metric cards show dashes when there are no human-verified labels', () => {
  const cards = pocMetricCards({
    metrics: { accuracy: null, macro_f1: null, bin_accuracy: null, classification_sample_size: 0, bin_sample_size: 0 },
    pool: { eligible: 69, human_verified: 0, historical_prediction_only: 69 },
    review_needed: 69,
    outcome_totals: { success: 233, timeout: 60, error: 32 },
  });
  const byLabel = Object.fromEntries(cards.map((card) => [card.label, card.value]));
  assert.equal(byLabel.Accuracy, '—');
  assert.equal(byLabel['Macro F1'], '—');
  assert.equal(byLabel['Bin accuracy'], '—');
  assert.equal(byLabel['รอ reviewer ยืนยัน'], '69');
  assert.equal(byLabel['ข้อมูลเก่า success / timeout / error'], '233 / 60 / 32');
});

test('metric cards show percentages once labels exist', () => {
  const cards = pocMetricCards({
    metrics: { accuracy: 0.9539, macro_f1: 0.7212, bin_accuracy: 0.9231, classification_sample_size: 152, bin_sample_size: 91 },
    pool: { human_verified: 152 },
    review_needed: 69,
    outcome_totals: { success: 233, timeout: 60, error: 32 },
  });
  assert.equal(cards[0].value, '95.4%');
  assert.equal(cards[1].value, '0.721');
  assert.equal(formatPercent(null), '—');
});

test('query only forwards POC filters and paging', () => {
  const query = buildPocQuery({ ground_truth_source: 'human_verified', waste_category: '', bin_number: '4', status: 'api_error' }, { limit: 20, offset: 40 });
  assert.equal(query.toString(), 'limit=20&offset=40&ground_truth_source=human_verified&bin_number=4');
});

test('POC image URL uses the POC route, not the live scan route', () => {
  assert.equal(pocImageUrl('a-b_1'), '/api/scan-history/poc/a-b_1/image');
});
