/**
 * Read a destination already resolved by the backend normalizer. This helper
 * intentionally has no category-to-bin fallback or legacy lookup.
 */
export function getAuthoritativeBin(result) {
  const mapping = result?.mapping;
  const hasValidStatus = result?.status === 'success' || result?.status === 'unknown';
  const hasValidNumber = Number.isInteger(mapping?.binNumber) && mapping.binNumber > 0;
  const hasValidName = typeof mapping?.binCategory === 'string' && mapping.binCategory.trim().length > 0;

  if (!hasValidStatus || !hasValidNumber || !hasValidName) return null;

  return {
    number: mapping.binNumber,
    category: mapping.binCategory,
  };
}
