import { proxyScanHistory } from '@/lib/scanHistoryProxy';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  return proxyScanHistory(request);
}
