// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { HealthCheckResponse } from "@/lib/chat/types";

const POLL_INTERVAL_MS = 60_000;
const FETCH_TIMEOUT_MS = 5_000;

export type BackendStatus = "connected" | "degraded" | "unreachable";

export interface BackendHealth {
  /** Overall backend status derived from the health check. */
  backendStatus: BackendStatus;
  /** Full health check response (null until first successful poll). */
  health: HealthCheckResponse | null;
  /** Human-readable status detail for tooltips / screen readers. */
  statusDetail: string;
}

/**
 * Poll the backend health endpoint periodically.
 *
 * Returns a three-state status:
 *   "connected"   — backend healthy, all checks pass
 *   "degraded"    — backend up but LLM or semantic router unavailable
 *   "unreachable" — backend not responding or database down
 */
export function useBackendHealth(): BackendHealth {
  const [health, setHealth] = useState<HealthCheckResponse | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("connected");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const check = useCallback(async () => {
    try {
      const res = await fetch("/api/health", {
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        cache: "no-store",
      });
      const data: HealthCheckResponse = await res.json();
      setHealth(data);

      if (data.status === "healthy") {
        setBackendStatus("connected");
      } else if (data.status === "degraded") {
        setBackendStatus("degraded");
      } else {
        setBackendStatus("unreachable");
      }
    } catch {
      setHealth(null);
      setBackendStatus("unreachable");
    }
  }, []);

  useEffect(() => {
    // Initial check on mount
    check();

    intervalRef.current = setInterval(check, POLL_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [check]);

  // Derive detail string for tooltips
  let statusDetail = "Connected";
  if (backendStatus === "degraded") {
    const parts: string[] = [];
    const llmStatus = health?.checks.llm.status;
    const llmDetail = health?.checks.llm.detail;
    if (llmStatus === "degraded") {
      // Live ping detected a specific issue — use it
      parts.push(llmDetail || "LLM issue detected");
      parts.push("Search still works");
    } else if (llmStatus === "unavailable") {
      parts.push(llmDetail || "LLM not configured");
      parts.push("Using keyword matching only");
    } else if (llmStatus !== "up") {
      parts.push("LLM unavailable");
      parts.push("Search still works");
    }
    if (health?.checks.semantic_router?.status !== "up"
        && health?.checks.semantic_router?.status !== undefined) {
      parts.push("Semantic routing not loaded");
    }
    statusDetail = parts.length > 0
      ? parts.join(" · ")
      : "Degraded";
  } else if (backendStatus === "unreachable") {
    const dbError = health?.checks?.database?.error;
    statusDetail = dbError
      ? `Backend unreachable: ${dbError}`
      : "Backend unreachable";
  }

  return { backendStatus, health, statusDetail };
}
