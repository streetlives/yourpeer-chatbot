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

const FEEDBACK_IP_LIMITS: [number, number][] = [
  [60, 20], // 20 feedback requests/minute per IP
];

export async function POST(req: NextRequest) {
  const clientIp = getClientIp(req.headers);
  const limit = checkRateLimit(`feedback:${clientIp}`, FEEDBACK_IP_LIMITS);
  if (!limit.allowed) {
    return rateLimitResponse(limit.retryAfter);
  }

  // Resolve backend URL before the try/catch — see chat/route.ts for
  // the rationale. Same pattern across all five proxy routes.
  const backendUrl = getBackendUrl();

  try {
    const body = await req.json();
    const res = await fetch(`${backendUrl}/chat/feedback`, {
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
