import { NextResponse } from 'next/server.js';

const BASE_URL = process.env.MODEL_BASE_URL || 'http://127.0.0.1:8001';

export function scanHistoryError(error, message, status) {
  return NextResponse.json({ error, message }, { status, headers: { 'Cache-Control': 'no-store' } });
}

function isLoopback(value) {
  const host = String(value || '').replace(/^\[|\]$/g, '').toLowerCase();
  return host === 'localhost' || host === '::1' || /^::ffff:127\./.test(host) || /^127\./.test(host);
}

function isLoopbackHost(value) {
  const host = String(value || '').replace(/^\[|\]$/g, '').toLowerCase();
  return host === 'localhost' || host === '::1' || /^::ffff:127\./.test(host) || /^127\./.test(host);
}

function isDevelopmentLoopbackRequest(request) {
  try {
    const requestUrl = new URL(request.url);
    const rawHost = request.headers.get('host');
    const hostUrl = rawHost ? new URL(`http://${rawHost}`) : requestUrl;
    if (!isLoopbackHost(hostUrl.hostname)) return false;

    const origin = request.headers.get('origin');
    if (origin) {
      const originUrl = new URL(origin);
      if (!['http:', 'https:'].includes(originUrl.protocol)) return false;
      if (!isLoopbackHost(originUrl.hostname) || originUrl.host !== hostUrl.host) return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function scanHistoryRequestAllowed(request) {
  if (process.env.ECOAICLE_SCAN_HISTORY_ENABLED !== '1') return false;
  const clientIp = request.ip || '';
  if (isLoopback(clientIp)) return true;
  // Next 14 development requests can omit request.ip or report a bridge IP.
  // Permit only a matching loopback Host/Origin there; production still requires
  // a loopback socket IP or the explicit host allowlist.
  if (process.env.NODE_ENV === 'development') {
    const allowed = isDevelopmentLoopbackRequest(request);
    if (allowed) {
      console.info('[ScanHistoryProxy] development loopback request accepted', {
        host: request.headers.get('host'),
        requestIpPresent: Boolean(clientIp),
      });
      return true;
    }
  }
  const allowed = (process.env.ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS || '')
    .split(',').map((host) => host.trim()).filter(Boolean);
  return allowed.includes(clientIp);
}

export async function proxyScanHistory(request, suffix = '') {
  if (!scanHistoryRequestAllowed(request)) {
    if (process.env.NODE_ENV === 'development') {
      console.warn('[ScanHistoryProxy] request returned 404 before upstream fetch', {
        stage: 'proxy_guard',
        requestIpPresent: Boolean(request.ip),
        host: request.headers.get('host'),
        origin: request.headers.get('origin'),
      });
    }
    return scanHistoryError('scan_history_unavailable', 'Scan history is unavailable.', 404);
  }
  const upstream = new URL(`/scan-history${suffix}`, BASE_URL);
  upstream.search = new URL(request.url).search;
  try {
    const response = await fetch(upstream, { cache: 'no-store' });
    if (process.env.NODE_ENV === 'development' && response.status === 404) {
      console.warn('[ScanHistoryProxy] upstream returned 404', { path: upstream.pathname });
    }
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
  } catch (error) {
    return scanHistoryError('scan_history_unavailable', 'Scan history service is unavailable.', 502);
  }
}
