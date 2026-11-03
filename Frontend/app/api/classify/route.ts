import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { createProxyApiErrorEnvelope } from "@/lib/proxyError";

export const dynamic = "force-dynamic";

const MODEL_BASE_URL =
  process.env.MODEL_BASE_URL || "http://127.0.0.1:8001";
const LOCATION_ID = process.env.ECOCAICLE_LOCATION_ID || "ICT-001";
const PROXY_TIMEOUT_MS = 40_000;

function tracePrediction(event: string, cycleId: string, details: object = {}) {
  if (process.env.NODE_ENV !== "development") return;
  console.info(`[ScanTrace] ${event}`, {
    cycleId,
    timestamp: new Date().toISOString(),
    ...details,
  });
}

function proxyApiError(requestId: string, detail: object, status = 502) {
  return NextResponse.json(
    createProxyApiErrorEnvelope({
      requestId,
      locationId: LOCATION_ID,
      detail,
    }),
    { status },
  );
}

export async function POST(req: Request) {
  const requestId = randomUUID();
  const cycleId = req.headers.get("x-scan-cycle-id") || "unknown";
  const resetCycleId = req.headers.get("x-reset-cycle-id") || cycleId;
  const upstreamUrl = `${MODEL_BASE_URL}/predict`;
  const startedAt = Date.now();
  let upstreamTimedOut = false;
  let upstreamTimeout: ReturnType<typeof setTimeout> | undefined;
  const upstreamController = new AbortController();
  tracePrediction("prediction request URL", cycleId, {
    url: upstreamUrl,
    requestId,
    resetCycleId,
    timeoutMs: PROXY_TIMEOUT_MS,
    mockClassification: process.env.NEXT_PUBLIC_USE_MOCK_CLASSIFICATION === "true",
  });

  try {
    const formData = await req.formData();
    const file = formData.get("file");

    if (!file || typeof file === "string" || typeof file.arrayBuffer !== "function") {
      tracePrediction("prediction request failed", cycleId, {
        requestId,
        resetCycleId,
        reason: "missing_file",
        elapsedMs: Date.now() - startedAt,
      });
      return NextResponse.json(
        { error: "missing_file", detail: "Multipart field 'file' is required." },
        { status: 400 }
      );
    }

    const upstreamFormData = new FormData();
    upstreamFormData.append("file", file, file.name || "capture.jpg");

    tracePrediction("image payload created", cycleId, {
      requestId,
      mimeType: file.type || "unknown",
      byteLength: file.size,
    });
    // The browser may hit its 30-second UI deadline first. Let this upstream
    // request finish or reach its own bounded deadline so an automatic reset
    // cannot overlap an inference that is still running.
    upstreamTimeout = setTimeout(() => {
      upstreamTimedOut = true;
      upstreamController.abort();
    }, PROXY_TIMEOUT_MS);
    const res = await fetch(upstreamUrl, {
      method: "POST",
      body: upstreamFormData,
      headers: {
        "x-scan-cycle-id": cycleId,
        "x-reset-cycle-id": resetCycleId,
      },
      signal: upstreamController.signal,
    });

    const raw = await res.text();

    try {
      const payload = JSON.parse(raw);
      tracePrediction("prediction request completed", cycleId, {
        requestId,
        status: res.status,
        elapsedMs: Date.now() - startedAt,
      });
      return NextResponse.json(payload, { status: res.status });
    } catch {
      tracePrediction("prediction request failed", cycleId, {
        requestId,
        reason: "invalid_backend_response",
        status: res.status,
        elapsedMs: Date.now() - startedAt,
      });
      return proxyApiError(
        requestId,
        {
          error: "invalid_backend_response",
          type: "InvalidBackendResponse",
          message: "Backend returned a non-JSON response.",
        },
        res.ok ? 502 : (res.status || 502),
      );
    }
  } catch (err: any) {
    if (upstreamTimedOut) {
      const elapsedMs = Date.now() - startedAt;
      tracePrediction("prediction request failed", cycleId, {
        requestId,
        reason: "upstream_timeout",
        timeoutMs: PROXY_TIMEOUT_MS,
        elapsedMs,
      });
      return proxyApiError(
        requestId,
        {
          error: "prediction_timeout",
          type: "TimeoutError",
          message: `Prediction service exceeded the ${PROXY_TIMEOUT_MS / 1000}-second upstream timeout.`,
        },
        504,
      );
    }
    tracePrediction("prediction request failed", cycleId, {
      requestId,
      name: err?.name || "Error",
      message: err?.message || "Prediction request failed.",
      elapsedMs: Date.now() - startedAt,
    });
    console.error("classify proxy error:", {
      name: err?.name || "Error",
      message: err?.message || "Prediction request failed.",
    });

    return proxyApiError(
      requestId,
      {
        error: "model_unavailable",
        type: err?.name || "ProxyError",
        message: "Prediction service is unavailable.",
      },
      502,
    );
  } finally {
    if (upstreamTimeout !== undefined) clearTimeout(upstreamTimeout);
  }
}
