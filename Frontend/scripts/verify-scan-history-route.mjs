import assert from 'node:assert/strict';
import { once } from 'node:events';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { scanHistoryRequestAllowed } from '../src/lib/scanHistoryProxy.js';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const nextCli = path.join(frontendRoot, 'node_modules', 'next', 'dist', 'bin', 'next');

const pageClient = await readFile(path.join(frontendRoot, 'app', 'scan-history', 'scan-history.js'), 'utf8');
const metadataProxy = await readFile(path.join(frontendRoot, 'src', 'lib', 'scanHistoryProxy.js'), 'utf8');
assert.match(pageClient, /fetch\(`\/api\/scan-history\?/);
assert.doesNotMatch(`${pageClient}\n${metadataProxy}`, /\/predict|\/api\/classify|litellm|QWEN_API|chat\/completions/i);

const previousNodeEnv = process.env.NODE_ENV;
const previousHistoryFlag = process.env.ECOAICLE_SCAN_HISTORY_ENABLED;
const previousAllowedHosts = process.env.ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS;
try {
  delete process.env.ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS;
  process.env.NODE_ENV = 'development';
  process.env.ECOAICLE_SCAN_HISTORY_ENABLED = '1';
  for (const hostname of ['127.0.0.1', 'localhost']) {
    const host = `${hostname}:3002`;
    const request = {
      ip: undefined,
      url: `http://${host}/api/scan-history?limit=20&offset=0`,
      headers: new Headers({ host, origin: `http://${host}` }),
    };
    assert.equal(scanHistoryRequestAllowed(request), true, `missing request.ip should allow ${host} in development`);
  }
  const remoteRequest = {
    ip: undefined,
    url: 'http://example.com/api/scan-history',
    headers: new Headers({ host: 'example.com', origin: 'http://example.com' }),
  };
  assert.equal(scanHistoryRequestAllowed(remoteRequest), false, 'missing request.ip must not allow remote development hosts');

  process.env.NODE_ENV = 'production';
  const productionLocalHostWithoutIp = {
    ip: undefined,
    url: 'http://127.0.0.1:3002/api/scan-history',
    headers: new Headers({ host: '127.0.0.1:3002', origin: 'http://127.0.0.1:3002' }),
  };
  assert.equal(scanHistoryRequestAllowed(productionLocalHostWithoutIp), false, 'production must not use the development Host/Origin fallback');
} finally {
  if (previousNodeEnv === undefined) delete process.env.NODE_ENV;
  else process.env.NODE_ENV = previousNodeEnv;
  if (previousHistoryFlag === undefined) delete process.env.ECOAICLE_SCAN_HISTORY_ENABLED;
  else process.env.ECOAICLE_SCAN_HISTORY_ENABLED = previousHistoryFlag;
  if (previousAllowedHosts === undefined) delete process.env.ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS;
  else process.env.ECOAICLE_SCAN_HISTORY_ALLOWED_HOSTS = previousAllowedHosts;
}

const upstreamRequests = [];
const upstream = createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  upstreamRequests.push(`${url.pathname}${url.search}`);
  if (url.pathname === '/scan-history/images/safe-image-1') {
    response.writeHead(200, { 'Content-Type': 'image/png' });
    response.end(Buffer.from([137, 80, 78, 71]));
    return;
  }
  if (url.pathname === '/scan-history') {
    if (url.searchParams.get('simulate_not_found') === '1') {
      response.writeHead(404, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ detail: { error: 'stored_scan_history_not_found' } }));
      return;
    }
    if (url.searchParams.get('simulate_error') === '1') {
      response.writeHead(503, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ detail: { error: 'temporary_storage_error' } }));
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ items: [], total: 0, limit: 20, offset: 0, has_more: false }));
    return;
  }
  response.writeHead(404, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify({ detail: { error: 'not_found' } }));
});
upstream.listen(0, '127.0.0.1');
await once(upstream, 'listening');
const upstreamPort = upstream.address().port;

async function availablePort(preferredPort = 0) {
  const server = net.createServer();
  server.listen(preferredPort, '127.0.0.1');
  try {
    await once(server, 'listening');
  } catch (error) {
    if (preferredPort && error.code === 'EADDRINUSE') return availablePort();
    throw error;
  }
  const { port } = server.address();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function checkRoute(flagValue, expectedStatus, requirePageContent = false, preferredPort = 0) {
  const port = await availablePort(preferredPort);
  const env = { ...process.env };
  env.MODEL_BASE_URL = `http://127.0.0.1:${upstreamPort}`;
  if (flagValue === undefined) delete env.ECOAICLE_SCAN_HISTORY_ENABLED;
  else env.ECOAICLE_SCAN_HISTORY_ENABLED = flagValue;

  const child = spawn(process.execPath, [nextCli, 'dev', '--hostname', '127.0.0.1', '--port', String(port)], {
    cwd: frontendRoot,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  child.stdout.setEncoding('utf8').on('data', (chunk) => { output += chunk; });
  child.stderr.setEncoding('utf8').on('data', (chunk) => { output += chunk; });
  const waitForLog = async (text) => {
    const deadline = Date.now() + 2_000;
    while (!output.includes(text) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 25));
    assert.ok(output.includes(text), `Expected Next terminal log ${text}:\n${output}`);
  };

  try {
    const startupDeadline = Date.now() + 90_000;
    while (!/Ready in/.test(output)) {
      if (child.exitCode !== null) throw new Error(`Next.js exited before startup:\n${output}`);
      if (Date.now() > startupDeadline) throw new Error(`Timed out waiting for Next.js:\n${output}`);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }

    let response;
    let lastError;
    const requestDeadline = Date.now() + 90_000;
    while (Date.now() < requestDeadline) {
      try {
        response = await fetch(`http://127.0.0.1:${port}/scan-history`);
        if (response.status === expectedStatus) break;
      } catch (error) {
        lastError = error;
      }
      if (child.exitCode !== null) throw new Error(`Next.js exited while serving the route:\n${output}`);
      await new Promise((resolve) => setTimeout(resolve, 250));
    }

    assert.ok(response, `No HTTP response received${lastError ? `: ${lastError}` : ''}`);
    assert.equal(response.status, expectedStatus, output);
    if (requirePageContent) {
      const html = await response.text();
      assert.match(html, /Scan History/, 'enabled response should render the actual page content');

      const browserHeaders = {
        Origin: `http://127.0.0.1:${port}`,
        Host: `127.0.0.1:${port}`,
      };
      const defaultMetadataResponse = await fetch(`http://127.0.0.1:${port}/api/scan-history?limit=20&offset=0`, { headers: browserHeaders });
      assert.equal(defaultMetadataResponse.status, 200, `${await defaultMetadataResponse.clone().text()}\n${output}`);
      assert.deepEqual(await defaultMetadataResponse.json(), { items: [], total: 0, limit: 20, offset: 0, has_more: false });
      assert.ok(upstreamRequests.includes('/scan-history?limit=20&offset=0'));
      await waitForLog('requestIpPresent: false');

      const localhostHeaders = { Origin: `http://localhost:${port}`, Host: `localhost:${port}` };
      const localhostResponse = await fetch(`http://localhost:${port}/api/scan-history?limit=20&offset=0`, { headers: localhostHeaders });
      assert.equal(localhostResponse.status, 200, 'localhost Origin/Host should also pass the development loopback check');

      const query = 'limit=20&offset=0&status=success&waste_category=glass&bin_number=5&needs_glass_pet_review=true';
      const metadataResponse = await fetch(`http://127.0.0.1:${port}/api/scan-history?${query}`, { headers: browserHeaders });
      assert.equal(metadataResponse.status, 200);
      assert.deepEqual(await metadataResponse.json(), { items: [], total: 0, limit: 20, offset: 0, has_more: false });
      assert.ok(upstreamRequests.includes(`/scan-history?${query}`), 'metadata proxy should preserve all filters');

      const upstreamError = await fetch(`http://127.0.0.1:${port}/api/scan-history?simulate_error=1`);
      assert.equal(upstreamError.status, 503, 'proxy should preserve upstream errors instead of returning 404');
      assert.deepEqual(await upstreamError.json(), { detail: { error: 'temporary_storage_error' } });

      const upstreamNotFound = await fetch(`http://127.0.0.1:${port}/api/scan-history?simulate_not_found=1`);
      assert.equal(upstreamNotFound.status, 404);
      assert.deepEqual(await upstreamNotFound.json(), { detail: { error: 'stored_scan_history_not_found' } });
      await waitForLog('[ScanHistoryProxy] upstream returned 404');

      const imageResponse = await fetch(`http://127.0.0.1:${port}/api/scan-history/safe-image-1/image`);
      assert.equal(imageResponse.status, 200);
      assert.equal(imageResponse.headers.get('content-type'), 'image/png');
      assert.deepEqual([...new Uint8Array(await imageResponse.arrayBuffer())], [137, 80, 78, 71]);
    }
    console.log(`ECOAICLE_SCAN_HISTORY_ENABLED=${flagValue ?? '(unset)'} -> HTTP ${response.status}`);
    if (requirePageContent && preferredPort && port !== preferredPort) {
      const exactResponse = await fetch('http://127.0.0.1:3002/api/scan-history?limit=20&offset=0', {
        headers: { Origin: 'http://127.0.0.1:3002', Host: '127.0.0.1:3002' },
      });
      assert.equal(exactResponse.status, 200, 'the already-running 127.0.0.1:3002 route should return 200');
      console.log(`GET /api/scan-history?limit=20&offset=0 on 127.0.0.1:3002 -> HTTP ${exactResponse.status}`);
    }
    if (!requirePageContent) {
      const guardResponse = await fetch(`http://127.0.0.1:${port}/api/scan-history?limit=20&offset=0`);
      assert.equal(guardResponse.status, 404);
      assert.deepEqual(await guardResponse.json(), {
        error: 'scan_history_unavailable',
        message: 'Scan history is unavailable.',
      });
      await waitForLog('[ScanHistoryProxy] request returned 404 before upstream fetch');
    }
    const routeLogs = output.split(/\r?\n/).filter((line) => line.includes('GET /api/scan-history') || line.includes('[ScanHistoryProxy]'));
    if (routeLogs.length) console.log(routeLogs.join('\n'));
  } finally {
    child.kill('SIGTERM');
    await Promise.race([once(child, 'exit'), new Promise((resolve) => setTimeout(resolve, 5_000))]);
  }
}

try {
  await checkRoute(undefined, 404);
  await checkRoute('1', 200, true, 3002);
} finally {
  await new Promise((resolve, reject) => upstream.close((error) => error ? reject(error) : resolve()));
}
