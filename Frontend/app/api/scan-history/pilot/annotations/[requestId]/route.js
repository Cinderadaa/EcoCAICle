import { proxyPilotEvaluation } from '@/lib/pilotEvaluationProxy';

export const dynamic = 'force-dynamic';

export async function GET(request, context) {
  return proxyPilotEvaluation(request, 'annotations', context?.params?.requestId);
}

export async function POST(request, context) {
  return proxyPilotEvaluation(request, 'annotations', context?.params?.requestId);
}
