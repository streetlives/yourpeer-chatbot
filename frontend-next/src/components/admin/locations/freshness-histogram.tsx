// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { AlertCircle } from "lucide-react";
import type {
  FreshnessHistogramResponse,
  FreshnessBucketKey,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/lib/admin/use-admin-fetch";

/**
 * Section 2a — freshness distribution histogram.
 *
 * Six fixed buckets (lt30, 30to90, 90to180, 180to365, gt365, never)
 * rendered as a horizontal bar chart. Each bucket key matches the
 * `age_bucket` filter value on /list, so clicking a bar drives the
 * table filter via the `onBucketClick` callback — no key translation.
 *
 * Color scale: gradient from green (fresh) to amber (stale) to red
 * (very stale or never). Conveys the desirable→bad direction visually
 * for non-color-blind users; sufficient color-vs-color contrast for
 * accessibility (greens and reds chosen from the existing admin
 * palette which has been audited).
 *
 * Bar widget styled to match the `VerticalBars` pattern in
 * operations-charts.tsx — including the `items-end`-removed-with-
 * justify-end fix for percentage-height resolution. Matches the
 * earlier ops bar regression fix; documented inline.
 */
export function FreshnessHistogram({
  onBucketClick,
  activeBucket,
}: {
  onBucketClick?: (key: FreshnessBucketKey) => void;
  /** When set (and non-empty), the matching bar renders with an
   *  active-state outline so users can see which bucket is currently
   *  filtering the table below. Toggle-off via clicking the same bar
   *  is handled by the caller — this prop is presentation only. */
  activeBucket?: FreshnessBucketKey | "";
}) {
  const { data, loading, error } = useAdminFetch<FreshnessHistogramResponse>(
    "/api/admin/locations/freshness-histogram",
  );


  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load histogram: {error}
      </div>
    );
  }

  if (loading || !data) {
    // Minimal placeholder — same height as the loaded chart (~140px)
    // so the page doesn't reflow when data arrives.
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[140px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  const maxValue = Math.max(1, ...data.buckets.map((b) => b.count));

  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          Verification age distribution
        </h3>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {data.total.toLocaleString()} locations
        </span>
      </div>

      {/* Bar row.
       *
       * Mirror the operations-charts VerticalBars layout: do NOT add
       * `items-end` — the wrappers are content-sized that way and
       * percentage-height bars collapse to their min floor. Default
       * `align-items: stretch` makes wrappers fill the row height;
       * `justify-end` on each wrapper anchors the bar to the bottom
       * of its column. Same trap, same workaround.
       */}
      <div
        className="flex gap-1 items-stretch"
        style={{ height: "96px" }}
      >
        {data.buckets.map((bucket) => {
          const pct = (bucket.count / maxValue) * 100;
          const heightStyle = bucket.count === 0 ? "1px" : `max(2px, ${pct}%)`;
          const colorClass = colorClassForBucket(bucket.key, bucket.count);
          const isActive = activeBucket === bucket.key;

          const inner = (
            <div
              title={`${bucket.label}: ${bucket.count.toLocaleString()}`}
              className={`w-full rounded-t-[2px] transition-all ${colorClass}`}
              style={{ height: heightStyle }}
            />
          );

          // The wrapper is a button when onBucketClick is provided so
          // keyboard users can activate the filter the same way mouse
          // users can click. Otherwise it's a plain div (still
          // hoverable for the title tooltip).
          //
          // Active state: when this bucket matches the table's current
          // filter, mark it with an amber ring + tinted background so
          // it's visually unmistakable which bar is "in effect." The
          // toggle behavior (click again to clear) is much more
          // discoverable when the active state is visible.
          if (onBucketClick) {
            const activeRing = isActive
              ? "ring-2 ring-amber-500 ring-offset-1 ring-offset-white dark:ring-offset-neutral-900 bg-amber-50/50 dark:bg-amber-900/10"
              : "hover:opacity-90";
            return (
              <button
                key={bucket.key}
                type="button"
                onClick={() => onBucketClick(bucket.key)}
                className={`flex-1 min-w-0 flex flex-col items-center justify-end gap-0.5 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 rounded ${activeRing}`}
                aria-label={`${isActive ? "Currently filtering by " : "Filter table to "}${bucket.label} bucket (${bucket.count} locations)${isActive ? " — click to clear" : ""}`}
                aria-pressed={isActive}
              >
                {inner}
              </button>
            );
          }
          return (
            <div
              key={bucket.key}
              className="flex-1 min-w-0 flex flex-col items-center justify-end gap-0.5"
            >
              {inner}
            </div>
          );
        })}
      </div>

      {/* Bucket labels under the bars. Two lines per label: the date
       *  range, then the count below in smaller text. Aligned to
       *  match the bar columns above. The active bucket's label is
       *  rendered in amber/bold to reinforce the active-state cue from
       *  the bar's ring. */}
      <div className="flex gap-1 mt-2">
        {data.buckets.map((bucket) => {
          const isActive = activeBucket === bucket.key;
          return (
            <div key={bucket.key} className="flex-1 min-w-0 text-center">
              <div
                className={`text-[0.65rem] uppercase tracking-wide ${
                  isActive
                    ? "text-amber-700 dark:text-amber-400 font-semibold"
                    : "text-neutral-500 dark:text-neutral-400"
                }`}
              >
                {bucket.label}
              </div>
              <div
                className={`text-xs tabular-nums ${
                  isActive
                    ? "text-amber-700 dark:text-amber-400 font-semibold"
                    : "text-neutral-700 dark:text-neutral-300"
                }`}
              >
                {bucket.count.toLocaleString()}
              </div>
            </div>
          );
        })}
      </div>

      {onBucketClick && (
        <p className="mt-3 text-[0.7rem] text-neutral-500 dark:text-neutral-400 italic">
          {activeBucket
            ? "Click the highlighted bucket again to clear the filter."
            : "Click a bucket to filter the table below."}
        </p>
      )}
    </div>
  );
}

/**
 * Color scale by bucket. Fresh = green; stale = amber; never =
 * red. Matches the data-quality color cues used elsewhere in the
 * admin (StatCard valueColor) so admins build the same color-meaning
 * association across pages.
 *
 * Empty buckets get a desaturated tone so the column is still
 * visually claimed (the user can see "yes there's a bucket here, it
 * has zero data") rather than missing entirely.
 */
function colorClassForBucket(key: FreshnessBucketKey, count: number): string {
  if (count === 0) return "bg-neutral-100 dark:bg-neutral-800";
  switch (key) {
    case "lt30":     return "bg-green-500 hover:bg-green-600";
    case "30to90":   return "bg-green-400 hover:bg-green-500";
    case "90to180":  return "bg-amber-400 hover:bg-amber-500";
    case "180to365": return "bg-amber-500 hover:bg-amber-600";
    case "gt365":    return "bg-red-400 hover:bg-red-500";
    case "never":    return "bg-red-500 hover:bg-red-600";
  }
}
