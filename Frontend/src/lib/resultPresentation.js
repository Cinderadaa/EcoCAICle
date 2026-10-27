/**
 * Read a destination already resolved by the backend normalizer. This helper
 * intentionally has no category-to-bin fallback or legacy lookup.
 */
export function getAuthoritativeBin(result) {
  const mappedBin = result?.mappedBin;
  const hasValidStatus = result?.status === 'success' || result?.status === 'unknown' ||
    (result?.status === 'missing_mapping' && mappedBin?.fallback === true);
  const hasValidNumber = Number.isInteger(mappedBin?.number) && mappedBin.number > 0;
  const hasValidName = typeof mappedBin?.nameTh === 'string' && mappedBin.nameTh.trim().length > 0;

  if (!hasValidStatus || !hasValidNumber || !hasValidName) return null;

  return {
    number: mappedBin.number,
    category: mappedBin.nameTh,
    fallback: mappedBin.fallback === true,
  };
}
