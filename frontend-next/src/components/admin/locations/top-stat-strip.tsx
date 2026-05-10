// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { StatCard } from "@/components/admin/stat-card";
import type { LocationsStats } from "@/lib/admin/locations-types";

/**
 * Section 1 of the Locations page — six StatCards in a horizontal row.
 *
 * Each card shows a single number plus a trend indicator where one is
 * available. Trends are 7-day deltas: positive = "more in the last 7d
 * than the 7d before that"; rendered as a small ▲/▼ pill next to the
 * number. Cards without a meaningful trend (total_locations,
 * total_services) just show the number.
 *
 * Color cues follow the convention from existing admin StatCards:
 *   • neutral default for raw counts
 *   • green for "good" indicators (fresh count rising)
 *   • amber for "needs attention" (never-verified, stale)
 *   • amber/red would be redundant here since the SAME count being
 *     high or low determines whether it's good news; we use neutral
 *     and let the trend pill carry the up/down signal.
 */
export function LocationsTopStatStrip({ stats }: { stats: LocationsStats }) {
  const fmt = (n: number) => n.toLocaleString();
  // direction tells us whether a positive delta is good news for THIS
  // metric. For "Total locations", "Verified <90d", and "With
  // feedback", rising = good (green). For "Never verified" or
  // "Stale", rising = bad (red). The footgun this prop catches:
  // copy-pasting a trend onto a bad-metric card and forgetting to
  // flip the polarity — the rising-bad-number quietly shows green.
  type TrendDirection = "up_is_good" | "up_is_bad";
  const trendPill = (delta: number, direction: TrendDirection = "up_is_good") => {
    if (delta === 0) return null;
    const positive = delta > 0;
    const isGood = direction === "up_is_good" ? positive : !positive;
    const cls = isGood
      ? "text-green-700 dark:text-green-400"
      : "text-red-700 dark:text-red-400";
    return (
      <span className={`ml-1.5 text-xs font-medium ${cls}`}>
        {positive ? "▲" : "▼"} {Math.abs(delta)} (7d)
      </span>
    );
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      <StatCardWithTrend
        label="Total locations"
        value={fmt(stats.total_locations)}
        trend={trendPill(stats.trends.total_locations, "up_is_good")}
      />
      <StatCardWithTrend
        label="Total services"
        value={fmt(stats.total_services)}
        trend={null}
      />
      <StatCardWithTrend
        label="Verified <90d"
        value={fmt(stats.fresh_count)}
        trend={trendPill(stats.trends.fresh_count, "up_is_good")}
        valueColor="text-green-700 dark:text-green-400"
      />
      <StatCardWithTrend
        label="Never verified"
        value={fmt(stats.never_verified_count)}
        trend={null}
        valueColor="text-amber-700 dark:text-amber-400"
      />
      <StatCardWithTrend
        label="Stale (>90d)"
        value={fmt(stats.stale_count)}
        trend={null}
        valueColor="text-amber-700 dark:text-amber-400"
      />
      <StatCardWithTrend
        label="With feedback"
        value={fmt(stats.with_feedback_count)}
        trend={trendPill(stats.trends.with_feedback_count, "up_is_good")}
      />
    </div>
  );
}

/**
 * Local extension of StatCard adding a trend pill alongside the value.
 * Inline'd here rather than added to the shared StatCard because
 * trends may be specific to the Locations page; if other admin pages
 * adopt the same pattern, lift it up.
 */
function StatCardWithTrend({
  label,
  value,
  trend,
  valueColor,
}: {
  label: string;
  value: string;
  trend: React.ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
      <div className="text-xs uppercase tracking-wider text-neutral-500 dark:text-neutral-400 mb-1.5">
        {label}
      </div>
      <div className="flex items-baseline">
        <div
          className={`text-2xl font-bold tracking-tight ${
            valueColor || "text-neutral-900 dark:text-neutral-100"
          }`}
        >
          {value}
        </div>
        {trend}
      </div>
    </div>
  );
}

// Re-export StatCard so the page file's import block is uniform —
// the day-1 page imports both this component and the shared StatCard
// from a single neighbor file would be cleaner but isn't critical.
export { StatCard };
