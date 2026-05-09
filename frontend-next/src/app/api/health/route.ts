// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/backend-url";

const BACKEND_URL = getBackendUrl();

export async function GET() {
  try {
    const headers: Record<string, string> = {};

    // Forward admin API key so the backend returns full diagnostics
    // (latency, model info, timestamps) for the admin panel.
    const adminKey = process.env.ADMIN_API_KEY;
    if (adminKey) {
      headers["Authorization"] = `Bearer ${adminKey}`;
    }

    const res = await fetch(`${BACKEND_URL}/api/health`, {
      signal: AbortSignal.timeout(5_000),
      headers,
      // Prevent caching so every poll gets fresh status
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      {
        status: "unhealthy",
        timestamp: new Date().toISOString(),
        uptime_seconds: 0,
        checks: {
          database: { status: "down", error: "Backend unreachable" },
          llm: { status: "unavailable" },
          semantic_router: { status: "not_loaded" },
        },
      },
      { status: 503 },
    );
  }
}
