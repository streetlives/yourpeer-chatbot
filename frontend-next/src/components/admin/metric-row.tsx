// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { METRIC_GRID_COLS } from "./metric-row-grid";
import { useIssueFilter, isHiddenByFilter } from "@/lib/admin/metrics-page-helpers";

type MetricStatus = "on-target" | "warning" | "off-target" | "no-data" | "tracking";

interface MetricRowProps {
  name: string;
  subtitle: string;
  target: string;
  value: string | null;
  status: MetricStatus;
  phase?: "Pilot" | "Post-pilot";
  onClick?: (name: string) => void;
  statusOverride?: string;
}

const STATUS_LABELS: Record<MetricStatus, string> = {
  "on-target": "✓ On target",
  warning: "⚠ Watch",
  "off-target": "✗ Off target",
  "no-data": "— No data",
  "tracking": "📊 Tracking",
};

const STATUS_COLORS: Record<MetricStatus, string> = {
  "on-target": "text-green-600",
  warning: "text-amber-500",
  "off-target": "text-red-600",
  "no-data": "text-neutral-400",
  "tracking": "text-blue-500",
};

const PILL_BG: Record<MetricStatus, string> = {
  "on-target": "bg-green-50 text-green-600",
  warning: "bg-amber-50 text-amber-600",
  "off-target": "bg-red-50 text-red-600",
  "no-data": "bg-neutral-100 text-neutral-400",
  "tracking": "bg-blue-50 text-blue-500",
};

export function statusClass(
  val: number | null,
  target: number,
  direction: "gte" | "lte" = "gte",
  warn?: number,
): MetricStatus {
  if (val === null) return "no-data";
  if (direction === "gte") {
    if (val >= target) return "on-target";
    if (warn !== undefined && val >= warn) return "warning";
    return "off-target";
  }
  if (val <= target) return "on-target";
  if (warn !== undefined && val <= warn) return "warning";
  return "off-target";
}

export function fmtMetric(
  val: number | null | undefined,
  isPercent = false,
  decimals = 0,
): string | null {
  if (val === null || val === undefined) return null;
  if (isPercent) return (val * 100).toFixed(decimals) + "%";
  return val.toFixed(decimals);
}

export function MetricRow({
  name,
  subtitle,
  target,
  value,
  status,
  phase = "Pilot",
  onClick,
  statusOverride,
}: MetricRowProps) {
  // Honor the page-level "Show only issues" toggle. Reading the filter
  // via context (rather than a prop) keeps every existing MetricRow
  // call site unchanged — the page wraps its content in
  // <IssueFilterContext.Provider> and rows opt-in automatically.
  // Default-context value is "all", so rows render normally when no
  // provider is mounted (e.g. unit tests, the locations page if it
  // ever consumes MetricRow).
  const filterMode = useIssueFilter();
  if (isHiddenByFilter(status, filterMode)) return null;

  return (
    <div className={`grid ${METRIC_GRID_COLS} items-center gap-3.5 py-2.5 border-b border-neutral-100 text-sm last:border-b-0`}>
      <div>
        <div
          className={`font-semibold text-sm ${onClick ? "cursor-pointer hover:text-amber-600 transition-colors" : ""}`}
          onClick={onClick ? () => onClick(name) : undefined}
          role={onClick ? "button" : undefined}
          tabIndex={onClick ? 0 : undefined}
          onKeyDown={
            onClick
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onClick(name);
                  }
                }
              : undefined
          }
        >
          {name}
        </div>
        <div className="text-sm text-neutral-500 dark:text-neutral-400 mt-1">{subtitle}</div>
      </div>
      <div className="font-mono text-xs text-neutral-400 dark:text-neutral-500">{target}</div>
      <div className={`font-mono font-bold text-right ${STATUS_COLORS[status]}`}>
        {value ?? "—"}
      </div>
      <div className="text-right">
        <span
          className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${PILL_BG[status]}`}
        >
          {statusOverride ?? STATUS_LABELS[status]}
        </span>
      </div>
      <div className="text-right">
        <span
          className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
            phase === "Post-pilot"
              ? "bg-neutral-100 text-neutral-400 dark:bg-neutral-800 dark:text-neutral-500"
              : "bg-blue-50 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300"
          }`}
        >
          {phase}
        </span>
      </div>
    </div>
  );
}
