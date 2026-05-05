// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useState } from "react";
import type { HealthCheckResponse } from "@/lib/chat/types";

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const parts = [`${d}d`];
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  return parts.join(" ");
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "up"
      ? "bg-green-500"
      : status === "down"
        ? "bg-red-500"
        : "bg-amber-400"; // degraded, unavailable, not_loaded
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} aria-label={status} />;
}

export function SystemHealth() {
  const [health, setHealth] = useState<HealthCheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function check() {
      try {
        const res = await fetch("/api/health", {
          signal: AbortSignal.timeout(5_000),
          cache: "no-store",
        });
        const data: HealthCheckResponse = await res.json();
        if (active) {
          setHealth(data);
          setError(null);
        }
      } catch {
        if (active) setError("Backend unreachable");
      }
    }

    check();
    const id = setInterval(check, 60_000);
    return () => { active = false; clearInterval(id); };
  }, []);

  const overallColor =
    health?.status === "healthy"
      ? "text-green-700 bg-green-50"
      : health?.status === "degraded"
        ? "text-amber-700 bg-amber-50"
        : "text-red-700 bg-red-50";

  const overallLabel = health?.status
    ? health.status.charAt(0).toUpperCase() + health.status.slice(1)
    : error || "Checking…";

  const rows = health
    ? [
        {
          name: "Backend",
          status: health.status === "unhealthy" ? "down" : "up",
          detail: health.uptime_seconds != null ? `Uptime: ${formatUptime(health.uptime_seconds)}` : "Running",
        },
        {
          name: "Database",
          status: health.checks.database.status,
          detail: health.checks.database.status === "up"
            ? (health.checks.database.latency_ms != null ? `${health.checks.database.latency_ms}ms latency` : "Connected")
            : health.checks.database.error || "Unreachable",
        },
        {
          name: "LLM (Anthropic)",
          status: health.checks.llm.status,
          detail: health.checks.llm.status === "up"
            ? (health.checks.llm.latency_ms != null
              ? `Responding (${health.checks.llm.latency_ms}ms)${health.checks.llm.cached ? " · cached" : ""}`
              : "Connected")
            : health.checks.llm.status === "unavailable"
              ? (health.checks.llm.detail || "Not configured — regex-only mode")
              : health.checks.llm.status === "degraded"
                ? (health.checks.llm.detail || "API issue")
                : "Unknown",
        },
        {
          name: "Semantic router",
          status: health.checks.semantic_router?.status ?? "not_loaded",
          detail: health.checks.semantic_router?.status === "up"
            ? (health.checks.semantic_router.total_utterances != null
              ? `${health.checks.semantic_router.total_utterances} utterances · ${health.checks.semantic_router.service_routes ?? "?"}+${health.checks.semantic_router.population_routes ?? "?"} routes · ${health.checks.semantic_router.model}`
              : health.checks.semantic_router.route_count != null
                ? `${health.checks.semantic_router.route_count} routes · ${health.checks.semantic_router.model}`
                : "Loaded")
            : health.checks.semantic_router?.status === "degraded"
              ? "Model loaded but functional check failed"
              : "Model not loaded",
        },
      ]
    : [];

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50 border-b border-slate-200">
        <span className="text-sm font-medium text-slate-700">System health</span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${overallColor}`}>
          {overallLabel}
        </span>
      </div>
      <div className="divide-y divide-slate-100">
        {rows.length === 0 && (
          <div className="px-4 py-3 text-sm text-slate-400">
            {error || "Loading…"}
          </div>
        )}
        {rows.map((row) => (
          <div key={row.name} className="flex items-center gap-3 px-4 py-2.5">
            <StatusDot status={row.status} />
            <span className="text-sm font-medium text-slate-700 w-32">{row.name}</span>
            <span className="text-sm text-slate-500">{row.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
