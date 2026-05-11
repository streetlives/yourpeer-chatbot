// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Skeleton loading placeholders for admin pages.
 *
 * These provide visual structure while data loads, replacing plain
 * "Loading…" text with animated shapes that match the final layout.
 */

function Bone({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded bg-neutral-200 ${className}`}
      aria-hidden="true"
    />
  );
}

/** Skeleton for StatCard grid (overview page). */
export function StatCardSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div
      className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3"
      role="status"
      aria-label="Loading statistics"
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-4 py-4"
        >
          <Bone className="h-3 w-20 mb-3" />
          <Bone className="h-7 w-14 mb-2" />
          <Bone className="h-2.5 w-24" />
        </div>
      ))}
    </div>
  );
}

/**
 * Skeleton for table views (conversations, queries, events).
 *
 * `widths` controls the per-column placeholder widths and should match
 * the rendered table's actual column proportions to minimize visual jump
 * on the skeleton-to-data transition. Pass an array of Tailwind width
 * classes; the array length determines `cols` if cols is not given.
 *
 * Without a widths array, falls back to a generic "first column wider,
 * rest narrow" pattern. That's fine for one-off use but produces visible
 * flicker against tables with non-uniform real layouts.
 */
export function TableSkeleton({
  rows = 5,
  cols,
  widths,
}: {
  rows?: number;
  cols?: number;
  widths?: string[];
}) {
  // Resolve effective widths and column count. If widths is given it takes
  // precedence; otherwise we fall back to the legacy "wide first column,
  // narrow rest" pattern.
  const effectiveCols = widths?.length ?? cols ?? 4;
  const colWidths =
    widths ??
    Array.from({ length: effectiveCols }).map((_, i) => (i === 0 ? "w-24" : "w-16"));

  return (
    <div
      className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden"
      role="status"
      aria-label="Loading table"
    >
      {/* Header */}
      <div className="flex gap-4 px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
        {colWidths.map((w, i) => (
          <Bone key={i} className={`h-3 ${w}`} />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
          {colWidths.map((w, c) => (
            <Bone key={c} className={`h-4 ${w}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Skeleton for metrics page (sections with metric rows). */
export function MetricsSkeleton() {
  return (
    <div role="status" aria-label="Loading metrics">
      {[1, 2, 3].map((section) => (
        <div key={section} className="mb-6">
          <Bone className="h-5 w-40 mb-3" />
          <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex justify-between px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
                <Bone className="h-4 w-36" />
                <Bone className="h-4 w-16" />
                <Bone className="h-4 w-20" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Skeleton for eval results page. */
export function EvalSkeleton() {
  return (
    <div role="status" aria-label="Loading evaluation results">
      <Bone className="h-5 w-48 mb-4" />
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 mb-4">
        <Bone className="h-4 w-64 mb-3" />
        <Bone className="h-4 w-52 mb-3" />
        <Bone className="h-4 w-40" />
      </div>
      <TableSkeleton rows={6} cols={5} />
    </div>
  );
}
