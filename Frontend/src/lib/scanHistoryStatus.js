export function getScanStatusChips(scan = {}) {
  const flags = scan.review_flags || {};
  const chips = [];
  if (scan.status) chips.push([scan.status, scan.status === 'success' ? 'ok' : 'warn']);
  if (scan.classification_status) {
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

  const unique = new Map();
  const tonePriority = { ok: 0, warn: 1, review: 2, danger: 3 };
  for (const [label, tone] of chips) {
    const current = unique.get(label);
    if (!current || tonePriority[tone] > tonePriority[current]) unique.set(label, tone);
  }
  return [...unique].map(([label, tone]) => [label, tone]);
}
