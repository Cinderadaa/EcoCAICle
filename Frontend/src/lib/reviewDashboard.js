export const REVIEW_CATEGORIES = Object.freeze([
  'general',
  'pet_bottle',
  'plastic',
  'paper',
  'glass',
  'metal',
  'ewaste',
  'fuel',
  'hazardous',
  'infectious',
  'unknown',
]);

export function buildReviewImageUrl(imageId) {
  if (typeof imageId !== 'string' || !imageId.trim()) return null;
  const segments = imageId.split('/');
  if (segments.some((segment) => !segment || segment === '.' || segment === '..')) return null;
  return `/api/review/images/${segments.map(encodeURIComponent).join('/')}`;
}

export function formatCategory(category) {
  if (category === 'pet_bottle') return 'pet bottle';
  return typeof category === 'string' ? category : '—';
}

export function formatMetric(value, digits = 2) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return Number(value).toFixed(digits);
  return String(value);
}

export function reviewActionFor(category, predictedCategory) {
  return category === predictedCategory ? 'confirm' : 'correct';
}
