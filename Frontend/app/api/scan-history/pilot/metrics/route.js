import { proxyPilotEvaluation } from '@/lib/pilotEvaluationProxy';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  return proxyPilotEvaluation(request, 'metrics');
}
