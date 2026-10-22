import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { createProxyApiErrorEnvelope } from "@/lib/proxyError";

export const dynamic = "force-dynamic";

const MODEL_BASE_URL =
  process.env.MODEL_BASE_URL || "http://127.0.0.1:8001";
const LOCATION_ID = process.env.ECOAICLE_LOCATION_ID || "ICT-001";

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

  try {
    const formData = await req.formData();
    const file = formData.get("file");

    if (!file || typeof file === "string" || typeof file.arrayBuffer !== "function") {
      return NextResponse.json(
        { error: "missing_file", detail: "Multipart field 'file' is required." },
        { status: 400 }
      );
    }

    const upstreamFormData = new FormData();
    upstreamFormData.append("file", file, file.name || "capture.jpg");

    const res = await fetch(`${MODEL_BASE_URL}/predict`, {
      method: "POST",
      body: upstreamFormData,
    });

    const raw = await res.text();

    try {
      return NextResponse.json(JSON.parse(raw), { status: res.status });
    } catch {
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
  }
}
