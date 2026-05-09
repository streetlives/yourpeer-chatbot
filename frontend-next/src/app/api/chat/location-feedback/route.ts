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

const BACKEND_URL = getBackendUrl();

const LOCATION_FEEDBACK_IP_LIMITS: [number, number][] = [
  [60, 10], // 10 location feedback requests/minute per IP
];

export async function POST(req: NextRequest) {
  const clientIp = getClientIp(req.headers);
  const limit = checkRateLimit(`loc-feedback:${clientIp}`, LOCATION_FEEDBACK_IP_LIMITS);
  if (!limit.allowed) {
    return rateLimitResponse(limit.retryAfter);
  }

  try {
    const body = await req.json();
    const res = await fetch(`${BACKEND_URL}/chat/location-feedback`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(clientIp && { "X-Forwarded-For": clientIp }),
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    // Feedback loss is acceptable — don't block the user
    return NextResponse.json({ ok: true });
  }
}
