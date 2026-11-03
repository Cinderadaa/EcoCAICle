import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const MODEL_BASE_URL = process.env.MODEL_BASE_URL || 'http://127.0.0.1:8001';
const REVIEWER_TOKEN = process.env.ECOCAICLE_REVIEWER_TOKEN || '';

function errorResponse(error, message, status) {
  return NextResponse.json(
    { status: 'api_error', error, message },
    { status },
  );
}

function safePathSegments(rawPath) {
  if (!Array.isArray(rawPath) || rawPath.length === 0 || rawPath.length > 12) return null;
  if (rawPath.some((segment) => typeof segment !== 'string' || !/^[A-Za-z0-9._-]+$/.test(segment))) {
    return null;
  }
  const [resource] = rawPath;
  const allowed = (resource === 'queue' || resource === 'metrics' || resource === 'errors')
    ? rawPath.length === 1
    : resource === 'annotations'
      ? rawPath.length === 2
      : resource === 'images'
        ? rawPath.length >= 2
        : false;
  if (!allowed) return null;
  return rawPath;
}

export async function GET(request, context) {
  return proxyReviewRequest(request, context, 'GET');
}

export async function POST(request, context) {
  return proxyReviewRequest(request, context, 'POST');
}

async function proxyReviewRequest(request, context, method) {
  if (!REVIEWER_TOKEN) {
    return errorResponse('reviewer_not_configured', 'Reviewer access is not configured.', 503);
  }

  const segments = safePathSegments(context?.params?.path);
  if (!segments) {
    return errorResponse('invalid_review_path', 'Invalid reviewer API path.', 400);
  }

  const upstream = new URL(`/review/${segments.map(encodeURIComponent).join('/')}`, MODEL_BASE_URL);
  upstream.search = new URL(request.url).search;

  try {
    const headers = new Headers();
    headers.set('X-Reviewer-Token', REVIEWER_TOKEN);
    if (method === 'POST') headers.set('Content-Type', 'application/json');

    const response = await fetch(upstream, {
      method,
      headers,
      body: method === 'POST' ? await request.text() : undefined,
      cache: 'no-store',
    });

    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const body = await response.json();
      return NextResponse.json(body, {
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
  } catch (error) {
    console.error('review proxy error:', {
      name: error?.name || 'Error',
      message: error?.message || 'Reviewer service unavailable.',
    });
    return errorResponse('reviewer_unavailable', 'Reviewer service is unavailable.', 502);
  }
}
