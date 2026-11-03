import ScanHistory from './scan-history';
import { notFound } from 'next/navigation';

export const dynamic = 'force-dynamic';

export default function ScanHistoryPage() {
  if (process.env.ECOAICLE_SCAN_HISTORY_ENABLED !== '1') {
    notFound();
  }
  return <ScanHistory />;
}
