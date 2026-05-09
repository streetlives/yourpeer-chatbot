// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { NextRequest, NextResponse } from "next/server";
import {
  checkRateLimit,
  getClientIp,
  rateLimitResponse,
} from "@/lib/rate-limit";
import { getBackendUrl } from "@/lib/backend-url";

// Frontend-layer rate limits (per IP).
// First line of defense — the backend has its own, stricter per-session +
// per-IP limits. These prevent request volume from overwhelming the Node.js
// proxy before reaching FastAPI.
const CHAT_IP_LIMITS: [number, number][] = [
  [60, 30],   // 30 requests/minute per IP
  [3600, 150], // 150 requests/hour per IP
];

export async function POST(req: NextRequest) {
  // --- Rate limit check (before parsing body or proxying) ---
  const clientIp = getClientIp(req.headers);
  const limit = checkRateLimit(`chat:${clientIp}`, CHAT_IP_LIMITS);
  if (!limit.allowed) {
    return rateLimitResponse(limit.retryAfter);
  }

  // Resolve backend URL before the try/catch so a missing
  // CHAT_BACKEND_URL in production escapes the proxy-failure handler
  // and surfaces the helper's clear error in logs.
  const backendUrl = getBackendUrl();

  try {
    const body = await req.json();
    const requestId = req.headers.get("x-request-id") || crypto.randomUUID();
    const res = await fetch(`${backendUrl}/chat/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        ...(clientIp && { "X-Forwarded-For": clientIp }),
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, {
      status: res.status,
      headers: { "X-Request-ID": requestId },
    });
  } catch (err) {
    // Proxy-level failure: the backend is unreachable or returned
    // non-JSON. Log so ops can see recurring upstream problems, but
    // return a 502 with a neutral message — the client-side
    // sendChatMessage() handles 5xx by showing "something went
    // wrong on our end."
    console.error("[api/chat] backend proxy failed:", err);
    return NextResponse.json(
      { detail: "Failed to reach chat backend" },
      { status: 502 },
    );
  }
}
