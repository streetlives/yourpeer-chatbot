// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { NextRequest, NextResponse } from "next/server";
import { getBackendUrl } from "@/lib/backend-url";

/**
 * Catch-all proxy: /api/admin/stats → /admin/api/stats
 *                  /api/admin/eval/run → /admin/api/eval/run
 *                  /api/admin/eval/upload → /admin/api/eval/upload
 *                  etc.
 *
 * Body handling: the request body is streamed through without buffering.
 * This avoids Next.js's internal body size limits and supports large
 * eval report uploads (projected to 3-4 MB at 1000 scenarios).
 */
async function proxyToBackend(req: NextRequest, slug: string[]) {
  const path = slug.join("/");

  // Resolve backend URL before the try/catch so a missing
  // CHAT_BACKEND_URL in production surfaces the helper's clear
  // error in deploy logs rather than the friendly "Failed to reach
  // admin backend" 502 (which would mask the real misconfiguration).
  const backendUrl = getBackendUrl();
  const url = new URL(`${backendUrl}/admin/api/${path}`);

  // Forward query params
  req.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value);
  });

  try {
    const headers: Record<string, string> = {};

    // Preserve content-type from the original request
    const contentType = req.headers.get("content-type");
    if (contentType) {
      headers["Content-Type"] = contentType;
    } else {
      headers["Content-Type"] = "application/json";
    }

    // Forward admin API key — prefer server-side env var (not exposed to browser)
    const adminKey = process.env.ADMIN_API_KEY;
    if (adminKey) {
      headers["Authorization"] = `Bearer ${adminKey}`;
    }

    const fetchOpts: RequestInit = {
      method: req.method,
      headers,
    };

    // Stream the body through without parsing. Using req.text() instead
    // of req.json() + JSON.stringify() avoids the internal body size limit
    // that causes "Request body too large" on eval report uploads.
    // req.text() reads the raw bytes as a string — no JSON round-trip.
    if (req.method === "POST" || req.method === "PUT" || req.method === "PATCH") {
      try {
        fetchOpts.body = await req.text();
      } catch {
        // No body — that's fine for some POSTs (e.g., eval/run)
      }
    }

    const res = await fetch(url.toString(), fetchOpts);
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { detail: "Failed to reach admin backend" },
      { status: 502 },
    );
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params;
  return proxyToBackend(req, slug);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params;
  return proxyToBackend(req, slug);
}
