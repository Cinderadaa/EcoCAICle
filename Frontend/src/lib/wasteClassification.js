/** The material/waste types accepted by the model contract. */
export const WASTE_TYPE = Object.freeze({
  GENERAL: 'general',
  PET_BOTTLE: 'pet_bottle',
  PLASTIC: 'plastic',
  PAPER: 'paper',
  GLASS: 'glass',
  METAL: 'metal',
  EWASTE: 'ewaste',
  FUEL: 'fuel',
  HAZARDOUS: 'hazardous',
  INFECTIOUS: 'infectious',
  UNKNOWN: 'unknown',
});

export const WASTE_TYPES = Object.freeze(Object.values(WASTE_TYPE));

/**
 * Historical compatibility data only. Authoritative Phase 2 responses carry
 * their own location-specific destination and never consult this master.
 */
export const BIN_MASTER = Object.freeze({
  [WASTE_TYPE.GENERAL]: {
    number: 1,
    category: 'ขยะทั่วไป (ขยะฝังกลบ)',
    icon: '/general_waste.png',
  },
  [WASTE_TYPE.PET_BOTTLE]: {
    number: 2,
    category: 'ขวดพลาสติก PET',
    icon: '/pet_bottle.png',
  },
  [WASTE_TYPE.PLASTIC]: {
    number: 3,
    category: 'พลาสติก',
    icon: '/plastic.png',
  },
  [WASTE_TYPE.PAPER]: {
    number: 4,
    category: 'กระดาษ',
    icon: '/paper.png',
  },
  [WASTE_TYPE.GLASS]: {
    number: 5,
    category: 'ขวดแก้ว',
    icon: '/glass.png',
  },
  [WASTE_TYPE.METAL]: {
    number: 6,
    category: 'กระป๋องอะลูมิเนียมและโลหะอื่น ๆ',
    icon: '/metal.png',
  },
  [WASTE_TYPE.EWASTE]: {
    number: 7,
    category: 'ขยะอิเล็กทรอนิกส์',
    icon: '/electronic.png',
  },
  [WASTE_TYPE.FUEL]: {
    number: 8,
    category: 'ขยะเชื้อเพลิง',
    icon: '/fuel.png',
  },
  [WASTE_TYPE.HAZARDOUS]: {
    number: 9,
    category: 'ขยะอันตราย',
    icon: '/hazardous_waste.png',
  },
  [WASTE_TYPE.INFECTIOUS]: {
    number: 10,
    category: 'ขยะติดเชื้อ',
    icon: '/infectious.png',
  },
});

/**
 * A prediction below this score has no confirmed destination in the legacy
 * adapter. Authoritative backend responses provide their resolved status.
 */
const configuredLowConfidenceThreshold = Number(
  process.env.NEXT_PUBLIC_LOW_CONFIDENCE_THRESHOLD ?? '0.85',
);
export const LOW_CONFIDENCE_THRESHOLD = Number.isFinite(configuredLowConfidenceThreshold)
  ? Math.min(1, Math.max(0, configuredLowConfidenceThreshold))
  : 0.85;

export const UNKNOWN_PRESENTATION = Object.freeze({
  title: 'ไม่สามารถระบุประเภทขยะได้',
  image: BIN_MASTER[WASTE_TYPE.GENERAL].icon,
  alt: 'ไม่สามารถระบุประเภทขยะได้',
});

/**
 * Historical presentation data only. The live preparation flow must use the
 * backend `preparation.container_disposal` destination and never this value.
 */
export const FOOD_SCRAP_CONTAINER_BIN = Object.freeze({
  ...BIN_MASTER[WASTE_TYPE.PLASTIC],
});

/** Historical compatibility labels; live steps come from backend config. */
export const FOOD_SCRAP_STEPS = Object.freeze([
  Object.freeze({
    number: 1,
    action: 'เทเศษอาหาร',
    icon: '/เทไอคอน.png',
    iconAlt: 'เทเศษอาหารลงถังเศษอาหาร',
    destination: 'ลงถังเศษอาหาร',
  }),
  Object.freeze({
    number: 2,
    action: 'ทิ้งภาชนะ',
    iconAlt: 'ทิ้งภาชนะลงถังพลาสติก',
    destinationPrefix: 'ลงถัง',
  }),
]);

/** Explicit disposal scenario identifiers supplied by configuration/payload. */
export const DISPOSAL_SCENARIO = Object.freeze({
  LIQUID_CONTAINER: 'liquid-container',
});

/** Optional, organization-approved liquid guidance remains opt-in. */
const approvedLiquidDestinationInstruction =
  process.env.NEXT_PUBLIC_LIQUID_DISPOSAL_GUIDANCE?.trim() || null;

export const LIQUID_DISPOSAL_GUIDANCE = Object.freeze({
  destinationInstruction: approvedLiquidDestinationInstruction,
  reviewRequired: !approvedLiquidDestinationInstruction,
});

export const LIQUID_DISPOSAL_SCENARIOS = Object.freeze({
  [DISPOSAL_SCENARIO.LIQUID_CONTAINER]: Object.freeze({
    instructions: Object.freeze(['หากมีน้ำเหลืออยู่ ให้เทน้ำทิ้งก่อน']),
    additionalInstructions: Object.freeze([]),
  }),
});

/** Thai presentation labels are only fallbacks when the model has no object name. */
export const WASTE_TYPE_PRESENTATION = Object.freeze({
  [WASTE_TYPE.GENERAL]: { title: 'ขยะทั่วไป', alt: 'ขยะทั่วไป' },
  [WASTE_TYPE.PET_BOTTLE]: { title: 'ขวดพลาสติก', alt: 'ขวดพลาสติก' },
  [WASTE_TYPE.PLASTIC]: { title: 'พลาสติก', alt: 'พลาสติก' },
  [WASTE_TYPE.PAPER]: { title: 'กระดาษ', alt: 'กระดาษ' },
  [WASTE_TYPE.GLASS]: { title: 'ขวดแก้ว', alt: 'ขวดแก้ว' },
  [WASTE_TYPE.METAL]: { title: 'โลหะ', alt: 'โลหะ' },
  [WASTE_TYPE.EWASTE]: { title: 'ขยะอิเล็กทรอนิกส์', alt: 'ขยะอิเล็กทรอนิกส์' },
  [WASTE_TYPE.FUEL]: { title: 'ขยะเชื้อเพลิง', alt: 'ขยะเชื้อเพลิง' },
  [WASTE_TYPE.HAZARDOUS]: { title: 'ขยะอันตราย', alt: 'ขยะอันตราย' },
  [WASTE_TYPE.INFECTIOUS]: { title: 'ขยะติดเชื้อ', alt: 'ขยะติดเชื้อ' },
  [WASTE_TYPE.UNKNOWN]: UNKNOWN_PRESENTATION,
});

function normalizeWasteType(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return WASTE_TYPES.includes(normalized) ? normalized : WASTE_TYPE.UNKNOWN;
}

function readConfidence(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}

export function getSafeThaiItemName(value, wasteType) {
  const candidate = typeof value === 'string' ? value.trim().replace(/\s+/gu, ' ') : '';
  const validThaiOnly = candidate.length > 0 &&
    /^[\u0E01-\u0E3A\u0E40-\u0E4E\s]+$/u.test(candidate) &&
    /[\u0E01-\u0E2E]/u.test(candidate);
  if (validThaiOnly) return candidate.slice(0, 120);
  return WASTE_TYPE_PRESENTATION[normalizeWasteType(wasteType)]?.title || UNKNOWN_PRESENTATION.title;
}

function readItemName(value, wasteType) {
  return getSafeThaiItemName(value, wasteType);
}

export function getClassificationPresentation(wasteType) {
  const type = normalizeWasteType(wasteType);
  const presentation = WASTE_TYPE_PRESENTATION[type] || UNKNOWN_PRESENTATION;
  const bin = BIN_MASTER[type] || BIN_MASTER[WASTE_TYPE.GENERAL];
  return {
    ...presentation,
    image: bin.icon,
  };
}

/** Returns liquid guidance only for an explicitly configured scenario key. */
export function getLiquidPreparationInstructions(scenarioKey) {
  const normalizedKey = String(scenarioKey || '').toLowerCase();
  const scenario = Object.prototype.hasOwnProperty.call(LIQUID_DISPOSAL_SCENARIOS, normalizedKey)
    ? LIQUID_DISPOSAL_SCENARIOS[normalizedKey]
    : null;
  if (!scenario) return [];

  return [
    ...scenario.instructions,
    ...scenario.additionalInstructions,
    ...(LIQUID_DISPOSAL_GUIDANCE.destinationInstruction
      ? [LIQUID_DISPOSAL_GUIDANCE.destinationInstruction]
      : []),
  ];
}

function unwrapResponse(response) {
  let source = response?.result || response?.data || response || {};
  if (source?.result && typeof source.result === 'object') source = source.result;
  if (source?.data && typeof source.data === 'object') source = source.data;
  return source;
}

const AUTHORITATIVE_STATUSES = Object.freeze([
  'success',
  'unknown',
  'missing_mapping',
  'api_error',
  'no_waste_detected',
]);

function hasOwn(source, key) {
  return Object.prototype.hasOwnProperty.call(source || {}, key);
}

/**
 * Phase 2 responses carry an explicit status and mapping metadata. Once this
 * shape is present, the frontend must render it and must not infer a bin from
 * the waste category. Responses without this contract use the isolated
 * legacy adapter below for compatibility with older deployments.
 */
function isAuthoritativeBackendResponse(source) {
  return Boolean(
    source &&
    typeof source === 'object' &&
    hasOwn(source, 'status') &&
    hasOwn(source, 'classification_status') &&
    (hasOwn(source, 'request_id') || hasOwn(source, 'location_id') || hasOwn(source, 'mapping_version')),
  );
}

function readBinNumber(value) {
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function readBackendMapping(source) {
  const canonical = source?.mappedBin;
  const binIdValue = canonical?.id ?? source?.bin_id ?? source?.binId;
  const binNumberValue = canonical?.number ?? source?.binNumber ?? source?.bin_number;
  const binNameValue = canonical?.nameTh ?? source?.binName ?? source?.bin_name;
  const fallbackValue = typeof canonical?.fallback === 'boolean'
    ? canonical.fallback
    : source?.mappingFallback === true || source?.mapping_fallback === true;
  const binId = typeof binIdValue === 'string' && binIdValue.trim()
    ? binIdValue.trim()
    : null;
  const binNumber = readBinNumber(binNumberValue);
  const binName = typeof binNameValue === 'string' && binNameValue.trim()
    ? binNameValue.trim()
    : null;
  const available = Boolean(binId && binNumber !== null && binName);

  return {
    available,
    binId: available ? binId : null,
    binNumber: available ? binNumber : null,
    binName: available ? binName : null,
    binWasteType: typeof canonical?.wasteType === 'string'
      ? normalizeWasteType(canonical.wasteType)
      : null,
    mappingVersion: typeof canonical?.mappingVersion === 'string'
      ? canonical.mappingVersion
      : typeof source?.mapping_version === 'string'
        ? source.mapping_version
        : typeof source?.mappingVersion === 'string' ? source.mappingVersion : null,
    isFallback: fallbackValue,
  };
}

function traceFrontendMapping(source, wasteType, confidence, backendMapping, mapping) {
  if (process.env.NODE_ENV === 'production') return;
  const classificationStatus = source.classificationStatus || source.classification_status;
  const fallbackReason = !backendMapping.isFallback
    ? null
    : classificationStatus === 'unknown'
      ? 'unknown-classification'
      : classificationStatus === 'low_confidence'
        ? 'low-confidence'
        : classificationStatus === 'unavailable'
          ? 'no-verified-mapping'
          : 'backend-fallback';
  console.debug('[WasteMapping] frontend rendering backend destination', {
    rawModelCategory: source.prediction?.waste_category ?? source.prediction?.wasteType ?? source.waste_category ?? source.wasteType ?? null,
    normalizedWasteType: wasteType,
    confidence,
    fallbackReason,
    mappingKeyUsed: backendMapping.binWasteType || (backendMapping.isFallback ? 'general' : wasteType),
    matchedBinId: mapping?.binId || null,
    matchedBinNumber: mapping?.binNumber ?? null,
    matchedBinName: mapping?.binName || null,
    locationId: source.location_id || null,
    mappingVersion: backendMapping.mappingVersion || source.mapping_version || null,
  });
}

function readFoodScraps(source, predictionObject) {
  return predictionObject?.has_food_scraps === true ||
    predictionObject?.hasFoodScraps === true ||
    source?.hasFoodScraps === true ||
    source?.has_food_scraps === true ||
    source?.disposal?.hasFoodScraps === true;
}

function readHasLiquid(source, predictionObject) {
  return predictionObject?.has_liquid === true || predictionObject?.hasLiquid === true ||
    source?.hasLiquid === true || source?.has_liquid === true ||
    source?.disposal?.hasLiquid === true || source?.disposal?.has_liquid === true;
}

function readModelDetails(source) {
  return {
    name: typeof source?.model === 'string' ? source.model : source?.model?.name || null,
    version: typeof source?.model === 'object' ? source.model.version || null : null,
  };
}

function readPreparation(source, mapping, status, classificationStatus) {
  const raw = source?.preparation || source?.disposal?.preparation;
  if (!raw || typeof raw !== 'object') return null;

  const base = {
    required: false,
    reason: typeof raw.reason === 'string' ? raw.reason : 'unavailable',
    version: typeof raw.version === 'string' ? raw.version : null,
    signal: typeof raw.signal === 'string' ? raw.signal : 'has_food_scraps',
    signal_value: raw.signal_value === true ? true : raw.signal_value === false ? false : null,
    guidance_mode: typeof raw.guidance_mode === 'string'
      ? raw.guidance_mode
      : 'combined_food_liquid_contamination',
    shared_disposal_destination: null,
    container_disposal: null,
    steps: [],
    limitation_th: typeof raw.limitation_th === 'string' ? raw.limitation_th : null,
  };

  // Preparation is valid only for a confirmed backend destination. In
  // particular, an unknown/fallback or missing mapping must never be turned
  // into a second, apparently confirmed disposal instruction.
  if (raw.required !== true || status !== 'success' || classificationStatus !== 'confirmed') {
    return base;
  }

  const shared = raw.shared_disposal_destination;
  const sharedValid = shared && typeof shared === 'object' &&
    typeof shared.destination_id === 'string' && shared.destination_id.trim() &&
    shared.type === 'designated_disposal_point' &&
    typeof shared.label_th === 'string' && shared.label_th.trim() &&
    (shared.bin_id === null || shared.bin_id === undefined) &&
    (shared.bin_number === null || shared.bin_number === undefined);
  const rawSteps = Array.isArray(raw.steps) ? raw.steps : [];
  if (!sharedValid || rawSteps.length !== 2 || !mapping?.binId ||
      !Number.isInteger(mapping.binNumber) || mapping.binNumber <= 0 ||
      !mapping.binName) {
    return { ...base, reason: 'invalid_preparation' };
  }

  const normalizedSteps = rawSteps.map((step, index) => {
    if (!step || typeof step !== 'object' || step.step !== index + 1 ||
        typeof step.action_th !== 'string' || !step.action_th.trim() ||
        typeof step.instruction_th !== 'string' || !step.instruction_th.trim()) {
      return null;
    }
    return {
      step: step.step,
      action_th: step.action_th.trim(),
      instruction_th: step.instruction_th.trim(),
      icon: typeof step.icon === 'string' && step.icon.trim() ? step.icon.trim() : null,
    };
  });
  if (normalizedSteps.some((step) => !step)) {
    return { ...base, reason: 'invalid_preparation' };
  }

  const sharedDestination = {
    destination_id: shared.destination_id.trim(),
    type: 'designated_disposal_point',
    label_th: shared.label_th.trim(),
    bin_id: null,
    bin_number: null,
    bin_name: null,
  };
  const containerDestination = {
    type: 'mapped_bin',
    bin_id: mapping.binId,
    bin_number: mapping.binNumber,
    bin_name: mapping.binName,
    mapping_version: mapping.mappingVersion || null,
  };

  return {
    ...base,
    required: true,
    reason: typeof raw.reason === 'string' ? raw.reason : 'combined_food_liquid_signal',
    shared_disposal_destination: sharedDestination,
    container_disposal: containerDestination,
    steps: [
      { ...normalizedSteps[0], destination: sharedDestination },
      { ...normalizedSteps[1], destination: containerDestination },
    ],
  };
}

function readClassificationFields(source) {
  const prediction = source?.prediction !== undefined ? source.prediction : source;
  const predictionObject = prediction && typeof prediction === 'object' ? prediction : {};
  const rawWasteType = typeof prediction === 'string'
    ? prediction
    : predictionObject?.waste_category ??
      predictionObject?.wasteType ??
      source?.waste_category ??
      source?.wasteType ??
      predictionObject?.class ??
      source?.class;
  const resolvedWasteType = normalizeWasteType(rawWasteType);
  const confidence = readConfidence(
    predictionObject?.confidence ?? source?.confidence,
  );
  const objectName = readItemName(
    predictionObject?.object_name_th ??
    predictionObject?.objectNameTh ??
    predictionObject?.itemNameTh ??
    source?.object_name_th ??
    source?.objectNameTh ??
    source?.itemNameTh,
    resolvedWasteType,
  );

  return {
    predictionObject,
    resolvedWasteType,
    confidence,
    objectName,
    hasFoodScraps: readFoodScraps(source, predictionObject),
    hasLiquid: readHasLiquid(source, predictionObject),
  };
}

function buildNormalizedResult({
  source,
  wasteType,
  objectNameTh,
  confidence,
  hasFoodScraps,
  hasLiquid,
  classificationStatus,
  status,
  mapping,
  mappingVersion,
  requestId = null,
  locationId = null,
  validPrediction = true,
}) {
  const isFallbackDestination = Boolean(mapping?.isFallback);
  const hasDestination = Boolean(mapping && (mapping.verified || mapping.binNumber !== null));
  const liquidPreparationScenario = String(
    source?.liquidPreparationScenario || source?.disposal?.liquidPreparationScenario || '',
  ).toLowerCase();
  const model = readModelDetails(source);
  const displayObjectName = getSafeThaiItemName(objectNameTh, wasteType);
  const mappedBin = mapping && mapping.binNumber !== null && mapping.binName ? {
    id: mapping.binId || null,
    number: mapping.binNumber,
    nameTh: mapping.binName,
    wasteType: mapping.binWasteType || (mapping.isFallback ? WASTE_TYPE.GENERAL : wasteType),
    mappingVersion: mappingVersion || null,
    fallback: isFallbackDestination,
  } : null;

  return {
    prediction: {
      wasteType,
      waste_category: wasteType,
      classification_status: classificationStatus,
      object_name_th: displayObjectName,
      objectNameTh: displayObjectName,
      itemNameTh: displayObjectName,
      hasFoodScraps,
      has_food_scraps: hasFoodScraps,
      hasLiquid,
      has_liquid: hasLiquid,
      confidence,
    },
    valid_prediction: validPrediction,
    retry_message: typeof source?.retry_message === 'string' ? source.retry_message : null,
    no_waste_detected: source?.no_waste_detected === true || status === 'no_waste_detected',
    topK: Array.isArray(source?.topK) ? source.topK : [],
    model,
    classification: getClassificationPresentation(wasteType),
    has_verified_bin: Boolean(mapping?.verified),
    classification_status: classificationStatus,
    destination_source: isFallbackDestination
      ? 'unknown-classification-fallback'
      : status === 'success'
        ? 'confirmed-mapping'
        : status === 'missing_mapping'
          ? 'unavailable'
          : status === 'api_error'
            ? 'api-error'
            : hasDestination
              ? 'unknown-classification-fallback'
              : 'unavailable',
    mapping: {
      verified: Boolean(mapping?.verified),
      binKey: mapping?.binKey || mapping?.binId || null,
      binNumber: mapping?.binNumber ?? null,
      binCategory: mapping?.binName || null,
      // The backend contract does not include an icon. Presentation may add
      // one later, but it must never derive a destination from this field.
      binIcon: null,
      isFallback: isFallbackDestination,
      fallbackReason: mapping?.fallbackReason || null,
      version: mappingVersion || null,
    },
    mappedBin,
    preparation: readPreparation(
      source,
      {
        binId: mapping?.binId || null,
        binNumber: mapping?.binNumber ?? null,
        binName: mapping?.binName || null,
        mappingVersion: mappingVersion || null,
      },
      status,
      classificationStatus,
    ),
    request_id: requestId,
    location_id: locationId,
    bin_id: mapping?.binId || null,
    bin_number: mapping?.binNumber ?? null,
    bin_name: mapping?.binName || null,
    mapping_version: mappingVersion || null,
    mapping_fallback: isFallbackDestination,
    status,
    inference: { latencyMs: typeof source?.inference?.latencyMs === 'number' ? source.inference.latencyMs : null },
    verification: { status: source?.verification?.status || null },
    disposal: { hasFoodScraps, has_food_scraps: hasFoodScraps, hasLiquid, has_liquid: hasLiquid, liquidPreparationScenario },
    waste_items: [{
      wasteType,
      waste_category: wasteType,
      classification_status: classificationStatus,
      object_name_th: displayObjectName,
      objectNameTh: displayObjectName,
      itemNameTh: displayObjectName,
      confidence,
      has_food_scraps: hasFoodScraps,
      binKey: mapping?.binKey || mapping?.binId || null,
      binNumber: mapping?.binNumber ?? null,
      binCategory: mapping?.binName || null,
      isFallback: isFallbackDestination,
      fallbackReason: mapping?.fallbackReason || null,
      hasFoodScraps,
      hasLiquid,
      has_liquid: hasLiquid,
      liquidPreparationScenario,
    }],
  };
}

/**
 * Compatibility adapter for genuinely old backend responses. This is the
 * only path allowed to use the historical frontend bin master.
 */
function normalizeLegacyClassificationResponse(source) {
  const {
    resolvedWasteType,
    confidence,
    objectName,
    hasFoodScraps,
    hasLiquid,
  } = readClassificationFields(source);
  const isLowConfidence = confidence === null || confidence < LOW_CONFIDENCE_THRESHOLD;
  const wasteType = resolvedWasteType === WASTE_TYPE.UNKNOWN || isLowConfidence
    ? WASTE_TYPE.UNKNOWN
    : resolvedWasteType;
  const objectNameTh = wasteType === WASTE_TYPE.UNKNOWN ? '' : objectName;
  const backendMapping = readBackendMapping(source);
  const isFallbackDestination = backendMapping.isFallback;
  const mappingCanRender = backendMapping.available && (
    (wasteType !== WASTE_TYPE.UNKNOWN && !isFallbackDestination) ||
    (backendMapping.binNumber === 1 && isFallbackDestination)
  );
  const classificationStatus = wasteType === WASTE_TYPE.UNKNOWN
    ? (resolvedWasteType === WASTE_TYPE.UNKNOWN ? 'unknown' : 'low_confidence')
    : 'confirmed';
  const status = mappingCanRender
    ? (classificationStatus === 'confirmed' ? 'success' : 'unknown')
    : (wasteType === WASTE_TYPE.UNKNOWN ? 'unknown' : 'missing_mapping');
  const mapping = mappingCanRender ? {
    verified: !isFallbackDestination,
    binKey: backendMapping.binWasteType || wasteType,
    binId: backendMapping.binId,
    binNumber: backendMapping.binNumber,
    binName: backendMapping.binName,
    binWasteType: backendMapping.binWasteType,
    mappingVersion: backendMapping.mappingVersion,
    isFallback: isFallbackDestination,
    fallbackReason: isFallbackDestination ? 'backend-fallback' : null,
  } : null;
  traceFrontendMapping(source, resolvedWasteType, confidence, backendMapping, mapping);

  return buildNormalizedResult({
    source,
    wasteType,
    objectNameTh,
    confidence,
    hasFoodScraps,
    hasLiquid,
    classificationStatus,
    status,
    mapping,
    mappingVersion: backendMapping.mappingVersion || source?.mapping?.version || null,
  });
}

/**
 * Authoritative Phase 2 response adapter. It copies backend destination data
 * and status values; it never calls getBinRecommendation or applies the
 * frontend confidence threshold.
 */
function normalizeAuthoritativeBackendResponse(source) {
  const {
    resolvedWasteType,
    confidence,
    objectName,
    hasFoodScraps,
    hasLiquid,
  } = readClassificationFields(source);
  const backendClassificationStatus = typeof (source.classificationStatus || source.classification_status) === 'string'
    ? (source.classificationStatus || source.classification_status)
    : 'unknown';
  const backendStatus = AUTHORITATIVE_STATUSES.includes(source.status)
    ? source.status
    : 'api_error';
  const backendMapping = readBackendMapping(source);
  if (backendStatus === 'no_waste_detected') {
    return buildNormalizedResult({
      source,
      wasteType: WASTE_TYPE.UNKNOWN,
      objectNameTh: '',
      confidence,
      hasFoodScraps: false,
      hasLiquid: false,
      classificationStatus: 'invalid_capture',
      status: 'no_waste_detected',
      mapping: null,
      mappingVersion: typeof source.mapping_version === 'string' ? source.mapping_version : null,
      requestId: typeof source.request_id === 'string' ? source.request_id : null,
      locationId: typeof source.location_id === 'string' ? source.location_id : null,
      validPrediction: false,
    });
  }
  const mappingCanRender = backendMapping.available && (
    backendStatus === 'success' || backendStatus === 'unknown' ||
    (backendStatus === 'missing_mapping' && backendMapping.isFallback)
  );
  const status = backendStatus === 'success' && !mappingCanRender
    ? 'missing_mapping'
    : backendStatus;
  const wasteType = backendStatus === 'api_error' ||
    backendClassificationStatus === 'unknown' ||
    backendClassificationStatus === 'low_confidence'
    ? WASTE_TYPE.UNKNOWN
    : resolvedWasteType;
  const objectNameTh = backendStatus === 'api_error'
    ? ''
    : objectName;
  const mapping = mappingCanRender ? {
    verified: backendStatus === 'success' && source.mapping_fallback !== true,
    binId: backendMapping.binId,
    binNumber: backendMapping.binNumber,
    binName: backendMapping.binName,
    binWasteType: backendMapping.binWasteType,
    mappingVersion: backendMapping.mappingVersion,
    isFallback: backendMapping.isFallback || source.mapping_fallback === true,
    fallbackReason: source.mapping_fallback === true ? 'unknown-classification' : null,
  } : null;

  traceFrontendMapping(source, resolvedWasteType, confidence, backendMapping, mapping);

  return buildNormalizedResult({
    source,
    wasteType,
    objectNameTh,
    confidence,
    hasFoodScraps,
    hasLiquid,
    classificationStatus: backendClassificationStatus,
    status,
    mapping,
    mappingVersion: typeof source.mapping_version === 'string' ? source.mapping_version : null,
    requestId: typeof source.request_id === 'string' ? source.request_id : null,
    locationId: typeof source.location_id === 'string' ? source.location_id : null,
    validPrediction: backendStatus !== 'api_error',
  });
}

/**
 * Converts authoritative and legacy API responses into one UI shape.
 */
export function normalizeClassificationResponse(response = {}) {
  const source = unwrapResponse(response);
  return isAuthoritativeBackendResponse(source)
    ? normalizeAuthoritativeBackendResponse(source)
    : normalizeLegacyClassificationResponse(source);
}
