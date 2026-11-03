import { proxyScanHistory, scanHistoryError } from '@/lib/scanHistoryProxy';

export const dynamic = 'force-dynamic';

export async function GET(request, context) {
  const requestId = context?.params?.requestId;
  if (typeof requestId !== 'string' || !/^[A-Za-z0-9-]{1,80}$/.test(requestId)) {
    return scanHistoryError('invalid_request_id', 'Invalid scan request ID.', 400);
  }
  return proxyScanHistory(request, `/images/${encodeURIComponent(requestId)}`);
}
