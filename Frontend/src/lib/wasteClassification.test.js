import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mockWasteResults } from '../data/mockData.js';
import { createProxyApiErrorEnvelope } from './proxyError.js';
import { getAuthoritativeBin } from './resultPresentation.js';
import { getRemainingResultSeconds, shouldResetResult } from './resultFlow.js';
import { getPreparationStepText } from './preparationGuidance.js';
import { getSafeThaiItemName, normalizeClassificationResponse } from './wasteClassification.js';

const expectedBins = new Map([
  ['general', 1],
  ['pet_bottle', 2],
  ['plastic', 3],
  ['paper', 4],
  ['glass', 5],
  ['metal', 6],
  ['ewaste', 7],
  ['fuel', 8],
  ['hazardous', 9],
  ['infectious', 10],
]);

test('authoritative ICT fixtures render all ten backend bin recommendations', () => {
  const confirmedFixtures = mockWasteResults.filter((fixture) => fixture.status === 'success');
  assert.equal(confirmedFixtures.length, 10);

  for (const fixture of confirmedFixtures) {
    const normalized = normalizeClassificationResponse(fixture);
    const wasteCategory = fixture.prediction.waste_category;

    assert.equal(normalized.status, 'success');
    assert.equal(normalized.location_id, 'ICT-001');
    assert.equal(normalized.mapping_version, 'ICT-10-bin-v2');
    assert.equal(normalized.bin_number, expectedBins.get(wasteCategory));
    assert.equal(normalized.mappedBin.number, expectedBins.get(wasteCategory));
    assert.equal(normalized.mappedBin.id, fixture.bin_id);
    assert.equal(normalized.mappedBin.nameTh, fixture.bin_name);
    assert.equal(normalized.mappedBin.wasteType, wasteCategory);
    assert.equal(normalized.mapping.binNumber, expectedBins.get(wasteCategory));
    assert.equal(normalized.mapping.binCategory, fixture.bin_name);
    assert.equal(normalized.prediction.has_food_scraps, fixture.prediction.has_food_scraps);
    assert.equal(normalized.prediction.hasFoodScraps, fixture.prediction.hasFoodScraps);
    assert.equal(normalized.disposal.hasFoodScraps, fixture.disposal.hasFoodScraps);
  }
});

test('confirmed preparation uses an unnumbered shared point then the backend bin', () => {
  const fixture = mockWasteResults.find((item) => item.request_id === 'mock-ict-b03');
  const normalized = normalizeClassificationResponse(fixture);

  assert.equal(normalized.status, 'success');
  assert.equal(normalized.preparation.required, true);
  assert.equal(
    normalized.preparation.shared_disposal_destination.type,
    'designated_disposal_point',
  );
  assert.equal(normalized.preparation.shared_disposal_destination.bin_id, null);
  assert.equal(normalized.preparation.shared_disposal_destination.bin_number, null);
  assert.equal(
    getPreparationStepText(normalized.preparation.steps[0], 'เทเศษอาหาร'),
    'เทเศษอาหารที่เหลือที่จุดแยกที่กำหนด',
  );
  assert.equal(normalized.preparation.steps[1].destination.bin_number, 3);
  assert.equal(normalized.preparation.steps[1].destination.bin_name, 'พลาสติก');
  assert.match(normalized.preparation.limitation_th, /เทเศษอาหาร/);
});

test('valid Thai title and confirmed canonical mappedBin are preserved', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'canonical-paper', location_id: 'ICT-001',
    prediction: { wasteType: 'paper', itemNameTh: 'กระดาษทิชชู่ใช้แล้ว', confidence: 0.9 },
    mappedBin: { id: 'ICT-001-B04', number: 4, nameTh: 'กระดาษ', wasteType: 'paper', mappingVersion: 'ICT-10-bin-v2', fallback: false },
    classificationStatus: 'confirmed', classification_status: 'confirmed', status: 'success',
  });
  assert.equal(normalized.prediction.itemNameTh, 'กระดาษทิชชู่ใช้แล้ว');
  assert.deepEqual(getAuthoritativeBin(normalized), { number: 4, category: 'กระดาษ', fallback: false });
});

test('face-only no-waste capture is an invalid retry with no bin fallback', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'd1e35391-399f-4edc-9130-bbe3c3108938',
    location_id: 'ICT-001', mapping_version: 'ICT-10-bin-v3',
    status: 'no_waste_detected', classification_status: 'invalid_capture',
    no_waste_detected: true, valid_prediction: false,
    retry_message: 'ไม่พบขยะในภาพ กรุณาวางขยะในกรอบแล้วสแกนใหม่',
    prediction: { waste_category: 'unknown', object_name_th: 'ไม่พบขยะในภาพ', confidence: 0.1 },
    bin_id: null, bin_number: null, bin_name: null, mappedBin: null,
    mapping_fallback: false,
  });

  assert.equal(normalized.status, 'no_waste_detected');
  assert.equal(normalized.classification_status, 'invalid_capture');
  assert.equal(normalized.valid_prediction, false);
  assert.equal(normalized.no_waste_detected, true);
  assert.equal(normalized.retry_message, 'ไม่พบขยะในภาพ กรุณาวางขยะในกรอบแล้วสแกนใหม่');
  assert.equal(normalized.bin_number, null);
  assert.equal(normalized.mappedBin, null);
  assert.equal(getAuthoritativeBin(normalized), null);
});

test('consecutive current API mappings survive clearing result display state at Welcome', () => {
  const scans = [
    {
      request_id: 'scan-ewaste', location_id: 'ICT-001', mapping_version: 'ICT-10-bin-v2',
      prediction: { waste_category: 'ewaste', object_name_th: 'โทรศัพท์มือถือพร้อมเคส', confidence: 0.98 },
      status: 'success', classification_status: 'confirmed',
      binId: 'ICT-001-B07', binNumber: 7, binName: 'ขยะอิเล็กทรอนิกส์', mappingFallback: false,
    },
    {
      request_id: 'scan-paper', location_id: 'ICT-001', mapping_version: 'ICT-10-bin-v2',
      prediction: { waste_category: 'paper', object_name_th: 'กล่องกระดาษ', confidence: 0.98 },
      status: 'success', classification_status: 'confirmed',
      binId: 'ICT-001-B04', binNumber: 4, binName: 'กระดาษ', mappingFallback: false,
    },
    {
      request_id: 'scan-glass', location_id: 'ICT-001', mapping_version: 'ICT-10-bin-v2',
      prediction: { waste_category: 'glass', object_name_th: 'ขวดแก้ว', confidence: 0.98 },
      status: 'success', classification_status: 'confirmed',
      binId: 'ICT-001-B05', binNumber: 5, binName: 'ขวดแก้ว', mappingFallback: false,
    },
  ];
  const expected = [
    { wasteType: 'ewaste', number: 7, name: 'ขยะอิเล็กทรอนิกส์' },
    { wasteType: 'paper', number: 4, name: 'กระดาษ' },
    { wasteType: 'glass', number: 5, name: 'ขวดแก้ว' },
  ];
  let displayedResult = null;

  scans.forEach((response, index) => {
    displayedResult = normalizeClassificationResponse(response);
    assert.equal(displayedResult.request_id, response.request_id);
    assert.equal(displayedResult.prediction.wasteType, expected[index].wasteType);
    assert.equal(displayedResult.mapping_fallback, false);
    assert.deepEqual(getAuthoritativeBin(displayedResult), {
      number: expected[index].number,
      category: expected[index].name,
      fallback: false,
    });

    // Welcome clears presentation state, then the next API response is
    // normalized independently without reading a previous result.
    displayedResult = null;
    assert.equal(displayedResult, null);
  });
});

test('mixed-language item title falls back to a safe Thai waste label', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'bad-title', location_id: 'ICT-001',
    prediction: { wasteType: 'glass', itemNameTh: 'ขวดแก้ว中文 bottle', confidence: 0.95 },
    mappedBin: { id: 'ICT-001-B05', number: 5, nameTh: 'ขวดแก้ว', wasteType: 'glass', mappingVersion: 'ICT-10-bin-v2', fallback: false },
    classificationStatus: 'confirmed', status: 'success',
  });
  assert.equal(normalized.prediction.itemNameTh, 'ขวดแก้ว');
  assert.equal(getSafeThaiItemName('ABC中文', 'paper'), 'กระดาษ');
});

test('unknown prediction uses the explicit bin one fallback', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'unknown-canonical', location_id: 'ICT-001',
    prediction: { wasteType: 'unknown', itemNameTh: '混合', confidence: 0.2 },
    mappedBin: { id: 'ICT-001-B01', number: 1, nameTh: 'ขยะทั่วไป (ขยะฝังกลบ)', wasteType: 'general', mappingVersion: 'ICT-10-bin-v2', fallback: true },
    classificationStatus: 'unknown', status: 'unknown', mapping_fallback: true,
  });
  assert.equal(normalized.mappedBin.number, 1);
  assert.equal(normalized.mappedBin.nameTh, 'ขยะทั่วไป (ขยะฝังกลบ)');
  assert.equal(normalized.mappedBin.fallback, true);
});

test('food and liquid preparation destinations use the mapped bin', () => {
  const fixture = mockWasteResults.find((item) => item.request_id === 'mock-ict-b03');
  const liquidResponse = {
    ...fixture,
    prediction: { ...fixture.prediction, has_liquid: true, hasLiquid: true },
    disposal: { ...fixture.disposal, has_liquid: true, hasLiquid: true },
  };
  const normalized = normalizeClassificationResponse(liquidResponse);
  assert.equal(normalized.prediction.hasLiquid, true);
  assert.equal(normalized.preparation.steps[1].destination.bin_number, normalized.mappedBin.number);
  assert.equal(normalized.preparation.steps[1].destination.bin_name, normalized.mappedBin.nameTh);
});

test('liquid-only preparation displays the backend liquid instruction', () => {
  const fixture = mockWasteResults.find((item) => item.request_id === 'mock-ict-b03');
  const liquidInstruction = 'เทของเหลวที่เหลือที่จุดแยกที่กำหนด';
  const liquidResponse = {
    ...fixture,
    prediction: {
      ...fixture.prediction,
      has_food_scraps: false,
      hasFoodScraps: false,
      has_liquid: true,
      hasLiquid: true,
    },
    disposal: {
      ...fixture.disposal,
      has_food_scraps: false,
      hasFoodScraps: false,
      has_liquid: true,
      hasLiquid: true,
    },
    preparation: {
      ...fixture.preparation,
      steps: [
        { ...fixture.preparation.steps[0], action_th: 'แยกของเหลว', instruction_th: liquidInstruction },
        {
          ...fixture.preparation.steps[1],
          action_th: 'ทิ้งภาชนะ',
          instruction_th: 'นำภาชนะไปทิ้งตามถังที่แนะนำ',
        },
      ],
    },
  };
  const normalized = normalizeClassificationResponse(liquidResponse);

  assert.equal(normalized.prediction.has_food_scraps, false);
  assert.equal(normalized.prediction.has_liquid, true);
  assert.equal(
    getPreparationStepText(normalized.preparation.steps[0], 'เทเศษอาหาร'),
    liquidInstruction,
  );
  assert.equal(
    getPreparationStepText(normalized.preparation.steps[1], 'นำภาชนะไปทิ้งที่ถังนี้'),
    'นำภาชนะไปทิ้งตามถังที่แนะนำ',
  );
});

test('glass preparation keeps bin five from the authoritative response', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'prep-glass-1',
    location_id: 'ICT-001',
    prediction: {
      waste_category: 'glass',
      object_name_th: 'ขวดแก้ว',
      has_food_scraps: true,
      confidence: 0.9,
    },
    bin_id: 'ICT-001-B05',
    bin_number: 5,
    bin_name: 'ขวดแก้ว',
    mapping_version: 'ICT-10-bin-v2',
    classification_status: 'confirmed',
    mapping_fallback: false,
    status: 'success',
    preparation: {
      required: true,
      reason: 'combined_food_liquid_signal',
      version: 'ICT-preparation-v1',
      signal: 'has_food_scraps',
      signal_value: true,
      guidance_mode: 'combined_food_liquid_contamination',
      shared_disposal_destination: {
        destination_id: 'ICT-001-FOOD-LIQUID-SEPARATION',
        type: 'designated_disposal_point',
        label_th: 'จุดแยกเศษอาหาร/ของเหลว',
        bin_id: null,
        bin_number: null,
      },
      steps: [
        { step: 1, action_th: 'แยก', instruction_th: 'แยกก่อน', icon: '/เทไอคอน.png' },
        { step: 2, action_th: 'ทิ้งภาชนะ', instruction_th: 'ทิ้ง', icon: '/Tang.png' },
      ],
      limitation_th: 'สัญญาณรวม',
    },
  });

  assert.equal(normalized.preparation.required, true);
  assert.equal(normalized.preparation.steps[1].destination.bin_number, 5);
  assert.equal(normalized.preparation.steps[1].destination.bin_name, 'ขวดแก้ว');
});

test('unknown, low-confidence, missing mapping, and API error have no preparation flow', () => {
  const unknown = normalizeClassificationResponse(mockWasteResults.find((item) => item.request_id === 'mock-ict-unknown'));
  const low = normalizeClassificationResponse(mockWasteResults.find((item) => item.request_id === 'mock-ict-low-confidence'));
  const missing = normalizeClassificationResponse({
    request_id: 'prep-missing',
    location_id: 'ALT-001',
    prediction: { waste_category: 'glass', confidence: 0.9, has_food_scraps: true },
    bin_id: null,
    bin_number: null,
    bin_name: null,
    mapping_version: 'ALT-v1',
    classification_status: 'unavailable',
    mapping_fallback: false,
    status: 'missing_mapping',
    preparation: { required: true, steps: [] },
  });
  const apiError = normalizeClassificationResponse({
    request_id: 'prep-error',
    location_id: 'ICT-001',
    classification_status: 'api_error',
    mapping_fallback: false,
    status: 'api_error',
    preparation: { required: true, steps: [] },
  });

  assert.equal(unknown.preparation.required, false);
  assert.equal(low.preparation.required, false);
  assert.equal(missing.preparation.required, false);
  assert.equal(apiError.preparation.required, false);
});

test('invalid preparation destination is suppressed instead of displaying a bin', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'prep-invalid',
    location_id: 'ICT-001',
    prediction: { waste_category: 'plastic', confidence: 0.9, has_food_scraps: true },
    bin_id: 'ICT-001-B03',
    bin_number: 3,
    bin_name: 'พลาสติก',
    mapping_version: 'ICT-10-bin-v2',
    classification_status: 'confirmed',
    mapping_fallback: false,
    status: 'success',
    preparation: {
      required: true,
      shared_disposal_destination: {
        destination_id: 'bad',
        type: 'designated_disposal_point',
        label_th: 'ผิด',
        bin_id: 'FAKE-BIN',
        bin_number: 99,
      },
      steps: [],
    },
  });

  assert.equal(normalized.preparation.required, false);
  assert.equal(normalized.preparation.reason, 'invalid_preparation');
});

test('preparation metadata survives session-style normalization', () => {
  const fixture = mockWasteResults.find((item) => item.request_id === 'mock-ict-b03');
  const normalized = normalizeClassificationResponse(fixture);
  const restored = normalizeClassificationResponse(normalized);

  assert.equal(restored.preparation.required, true);
  assert.equal(restored.preparation.steps[0].destination.bin_number, null);
  assert.equal(restored.preparation.steps[1].destination.bin_number, 3);
  assert.equal(restored.preparation.version, 'ICT-preparation-v1');
});

test('unknown response displays the backend-provided fallback', () => {
  const normalized = normalizeClassificationResponse(mockWasteResults.find((fixture) => fixture.status === 'unknown' && fixture.classification_status === 'unknown'));

  assert.equal(normalized.status, 'unknown');
  assert.equal(normalized.classification_status, 'unknown');
  assert.equal(normalized.bin_id, 'ICT-001-B01');
  assert.equal(normalized.bin_number, 1);
  assert.equal(normalized.mapping_fallback, true);
  assert.equal(normalized.mapping.binCategory, 'ขยะทั่วไป (ขยะฝังกลบ)');
  assert.deepEqual(getAuthoritativeBin(normalized), {
    number: 1,
    category: 'ขยะทั่วไป (ขยะฝังกลบ)',
    fallback: true,
  });
});

test('low-confidence response remains unknown and uses only the backend fallback', () => {
  const normalized = normalizeClassificationResponse(mockWasteResults.find((fixture) => fixture.classification_status === 'low_confidence'));

  assert.equal(normalized.status, 'unknown');
  assert.equal(normalized.classification_status, 'low_confidence');
  assert.equal(normalized.prediction.wasteType, 'unknown');
  assert.equal(normalized.bin_number, 1);
  assert.equal(normalized.mapping_fallback, true);
});

test('missing mapping never invents a frontend destination', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'missing-1',
    location_id: 'ALT-001',
    prediction: {
      waste_category: 'organic',
      object_name_th: 'เศษวัสดุ',
      has_food_scraps: false,
      confidence: 0.99,
    },
    bin_id: null,
    bin_number: null,
    bin_name: null,
    mapping_version: 'ALT-v1',
    classification_status: 'unavailable',
    mapping_fallback: false,
    status: 'missing_mapping',
  });

  assert.equal(normalized.status, 'missing_mapping');
  assert.equal(normalized.classification_status, 'unavailable');
  assert.equal(normalized.bin_id, null);
  assert.equal(normalized.bin_number, null);
  assert.equal(normalized.mapping.binNumber, null);
  assert.equal(normalized.mapping.binCategory, null);
});

test('API error remains an API error and has no destination', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'error-1',
    location_id: 'ICT-001',
    mapping_version: 'ICT-10-bin-v2',
    classification_status: 'api_error',
    mapping_fallback: false,
    status: 'api_error',
    detail: { error: 'Model unavailable' },
  });

  assert.equal(normalized.status, 'api_error');
  assert.equal(normalized.valid_prediction, false);
  assert.equal(normalized.bin_number, null);
  assert.equal(normalized.mapping.binNumber, null);
});

test('Next.js proxy errors use the structured api_error contract', () => {
  const envelope = createProxyApiErrorEnvelope({
    requestId: 'proxy-error-1',
    locationId: 'ICT-001',
    detail: {
      error: 'model_unavailable',
      type: 'FetchError',
      message: 'Prediction service is unavailable.',
    },
  });

  assert.deepEqual(envelope, {
    request_id: 'proxy-error-1',
    location_id: 'ICT-001',
    object_name_th: null,
    waste_category: null,
    confidence: null,
    bin_id: null,
    bin_number: null,
    bin_name: null,
    mappedBin: null,
    mapping_version: null,
    classification_status: 'api_error',
    classificationStatus: 'api_error',
    mapping_fallback: false,
    status: 'api_error',
    detail: {
      error: 'model_unavailable',
      type: 'FetchError',
      message: 'Prediction service is unavailable.',
    },
  });
});

test('authoritative location-specific mapping overrides no frontend master data', () => {
  const backendResponse = {
    request_id: 'alt-1',
    location_id: 'ALT-001',
    prediction: {
      waste_category: 'general',
      object_name_th: 'ขยะทั่วไป',
      has_food_scraps: false,
      confidence: 0.99,
    },
    bin_id: 'ALT-001-B42',
    bin_number: 42,
    bin_name: 'ถังเฉพาะจุด ALT',
    mapping_version: 'ALT-v2',
    classification_status: 'confirmed',
    mapping_fallback: false,
    status: 'success',
  };

  const normalized = normalizeClassificationResponse(backendResponse);
  assert.equal(normalized.location_id, 'ALT-001');
  assert.equal(normalized.bin_id, 'ALT-001-B42');
  assert.equal(normalized.bin_number, 42);
  assert.equal(normalized.bin_name, 'ถังเฉพาะจุด ALT');
  assert.equal(normalized.mapping.binNumber, 42);
  assert.equal(normalized.mapping.binCategory, 'ถังเฉพาะจุด ALT');

  // Session restore re-normalizes the same contract and must preserve it.
  const restored = normalizeClassificationResponse(normalized);
  assert.equal(restored.location_id, 'ALT-001');
  assert.equal(restored.bin_number, 42);
  assert.equal(restored.mapping.binCategory, 'ถังเฉพาะจุด ALT');
});

test('canonical mappedBin wins over contradictory legacy bin fields', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'mapped-bin-precedence', location_id: 'ICT-001',
    prediction: { waste_category: 'paper', confidence: 0.99 },
    mappedBin: {
      id: 'ICT-001-B04', number: 4, nameTh: 'กระดาษ', wasteType: 'paper',
      mappingVersion: 'ICT-10-bin-v2', fallback: false,
    },
    bin_id: 'ICT-001-B03', bin_number: 3, bin_name: 'พลาสติก',
    mapping_version: 'ICT-10-bin-v2', classification_status: 'confirmed',
    mapping_fallback: false, status: 'success',
  });
  assert.deepEqual(normalized.mappedBin, {
    id: 'ICT-001-B04', number: 4, nameTh: 'กระดาษ', wasteType: 'paper',
    mappingVersion: 'ICT-10-bin-v2', fallback: false,
  });
  assert.deepEqual(getAuthoritativeBin(normalized), { number: 4, category: 'กระดาษ', fallback: false });
});

test('phone classification renders the backend e-waste bin seven unchanged', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'phone-ewaste', location_id: 'ICT-001',
    prediction: {
      wasteType: 'ewaste', itemNameTh: 'โทรศัพท์มือถือพร้อมเคส', confidence: 0.99,
    },
    mappedBin: {
      id: 'ICT-001-B07', number: 7, nameTh: 'ขยะอิเล็กทรอนิกส์', wasteType: 'ewaste',
      mappingVersion: 'ICT-10-bin-v2', fallback: false,
    },
    classificationStatus: 'confirmed', status: 'success',
  });
  assert.equal(normalized.prediction.wasteType, 'ewaste');
  assert.deepEqual(getAuthoritativeBin(normalized), {
    number: 7,
    category: 'ขยะอิเล็กทรอนิกส์',
    fallback: false,
  });
});

test('bin presentation uses backend number and name without category recalculation', () => {
  const normalized = normalizeClassificationResponse({
    request_id: 'render-1',
    location_id: 'ALT-001',
    prediction: { waste_category: 'general', object_name_th: 'ขยะ', confidence: 0.99 },
    bin_id: 'ALT-001-B42',
    bin_number: 42,
    bin_name: 'ถังเฉพาะจุด ALT',
    mapping_version: 'ALT-v1',
    classification_status: 'confirmed',
    mapping_fallback: false,
    status: 'success',
  });

  assert.deepEqual(getAuthoritativeBin(normalized), {
    number: 42,
    category: 'ถังเฉพาะจุด ALT',
    fallback: false,
  });
});

test('result countdown resets exactly at expiry', () => {
  assert.equal(getRemainingResultSeconds(10_000, 9_001), 1);
  assert.equal(getRemainingResultSeconds(10_000, 10_000), 0);
  assert.equal(shouldResetResult(10_000, 9_999), false);
  assert.equal(shouldResetResult(10_000, 10_000), true);
});

test('legacy responses without a backend bin do not infer a destination', () => {
  const normalized = normalizeClassificationResponse({
    prediction: {
      waste_category: 'cardboard',
      object_name_th: 'กล่องกระดาษ',
      has_food_scraps: false,
      confidence: 0.9,
    },
  });

  assert.equal(normalized.status, 'unknown');
  assert.equal(normalized.prediction.wasteType, 'unknown');
  assert.equal(normalized.bin_id, null);
  assert.equal(normalized.bin_number, null);
  assert.equal(normalized.mappedBin, null);
  assert.equal(normalized.location_id, null);
});

test('unsupported legacy categories never choose plastic, paper, glass, or metal bins', () => {
  for (const waste_category of ['other', 'trash', 'cardboard', 'organic', 'compost']) {
    const normalized = normalizeClassificationResponse({
      prediction: { waste_category, confidence: 0.99 },
    });
    assert.equal(normalized.prediction.wasteType, 'unknown', waste_category);
    assert.equal(normalized.mappedBin, null, waste_category);
  }
});
