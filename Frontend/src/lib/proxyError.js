/**
 * Build the shared API-error envelope used when the Next.js proxy cannot
 * obtain a valid response from FastAPI. Backend responses are passed through
 * unchanged; this helper is only for proxy/network failures.
 */
export function createProxyApiErrorEnvelope({
  requestId,
  locationId = 'ICT-001',
  detail,
}) {
  return {
    request_id: requestId,
    location_id: locationId,
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
    detail,
  };
}
