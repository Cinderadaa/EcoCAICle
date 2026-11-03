import { NextResponse } from 'next/server.js';
import { scanHistoryRequestAllowed } from './scanHistoryProxy.js';

const BASE_URL = process.env.MODEL_BASE_URL || 'http://127.0.0.1:8001';

function errorResponse(error, message, status) {
  return NextResponse.json({ error, message }, { status, headers: { 'Cache-Control': 'no-store' } });
}

export async function proxyPilotEvaluation(request, resource, requestId = null) {
  if (process.env.ECOAICLE_PILOT_EVALUATION_ENABLED !== '1') {
    return errorResponse('pilot_evaluation_unavailable', 'Pilot evaluation is unavailable.', 404);
  }
  if (!scanHistoryRequestAllowed(request)) {
    return errorResponse('scan_history_unavailable', 'Scan history is unavailable.', 404);
  }
  const reviewerToken = process.env.ECOCAICLE_REVIEWER_TOKEN || '';
  if (!reviewerToken) {
    return errorResponse('reviewer_not_configured', 'Reviewer access is not configured.', 503);
  }
  if (resource !== 'metrics' && resource !== 'annotations') {
    return errorResponse('invalid_pilot_path', 'Invalid pilot evaluation path.', 400);
  }
  if (resource === 'annotations' && (typeof requestId !== 'string' || !/^[A-Za-z0-9-]{1,80}$/.test(requestId))) {
    return errorResponse('invalid_request_id', 'Invalid scan request ID.', 400);
  }
  const path = resource === 'metrics'
    ? '/review/pilot/metrics'
    : `/review/pilot/annotations/${encodeURIComponent(requestId)}`;
  try {
    const response = await fetch(new URL(path, BASE_URL), {
      method: request.method,
      headers: {
        'X-Reviewer-Token': reviewerToken,
        ...(request.method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
      },
      body: request.method === 'POST' ? await request.text() : undefined,
      cache: 'no-store',
    });
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return NextResponse.json(await response.json(), {
        status: response.status,
        headers: { 'Cache-Control': 'no-store' },
      });
    }
    return new NextResponse(await response.arrayBuffer(), {
      status: response.status,
      headers: {
        'Content-Type': contentType || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch {
    return errorResponse('pilot_evaluation_unavailable', 'Pilot evaluation service is unavailable.', 502);
  }
}
