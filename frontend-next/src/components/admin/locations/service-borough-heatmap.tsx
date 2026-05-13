// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useMemo, useState } from "react";
import { AlertCircle, ChevronDown, ChevronUp } from "lucide-react";
import type { HeatmapResponse, HeatmapCategory, BoroughLabel } from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";
import { Tooltip } from "@/components/admin/tooltip";

// Default top-N matching the backend constant. Surfaced here as a
// const so future tuning lives in one place. Backend's
// HEATMAP_TOP_N_CATEGORIES drives sort order; this constant drives
// the default-collapsed display.
const DEFAULT_VISIBLE_CATEGORIES = 10;

/**
 * Section 3b — service-category × borough coverage heatmap.
 *
 * The single most useful visualization for ops decisions on this
 * page: instantly shows whether categories like "Showers" or "Mental
 * Health" are evenly distributed across the 5 boroughs or clustered
 * in 1–2. Equity gaps surface as visually empty cells.
 *
 * Layout: a CSS grid table with category names on the left and
 * borough columns across the top. Cell background opacity scales
 * with the cell value (linearly, anchored to the largest cell across
 * the WHOLE matrix — not per-row, since cross-row comparison is what
 * makes the heatmap useful).
 *
 * Default shows top 10 categories by total location count; click
 * "Show 29 more" to expand. Collapse toggle reverses.
 *
 * Cell numbers are always shown (not just on hover) — losing the
 * exact count to color encoding alone would be hostile to
 * accessibility and to anyone needing to act on the data. The color
 * is decoration; the number is the truth.
 */
export function ServiceBoroughHeatmap() {
  const { data, loading, error } = useAdminFetch<HeatmapResponse>(
    "/api/admin/locations/heatmap",
  );
  const [expanded, setExpanded] = useState(false);


  // expand/collapse needs to recompute the maximum because the
  // basis is the visible cells, not the full matrix. See the
  // useMemo for matrixMax below for the rationale.
  const visible = useMemo(
    () => (
      !data ? []
        : expanded
          ? data.categories
          : data.categories.slice(0, DEFAULT_VISIBLE_CATEGORIES)
    ),
    [data, expanded],
  );

  // Per-row minimums: visible categories vs. total. We compute the
  // matrix maximum cell value over the VISIBLE subset only — color
  // intensity should reflect relative strength within what the user
  // is actually looking at.
  //
  // Alternative we considered: anchor matrixMax to the full data set
  // so colors stayed stable across expand/collapse. We chose against
  // it because the typical case is that hidden categories include
  // unusually-bright cells (rare taxonomies clustered in one
  // borough), and anchoring to those made the visible cells look
  // uniformly washed out. The default top-10 view especially needs
  // the strongest signal within its 10 rows; users can expand to
  // see the long tail.
  const matrixMax = useMemo(() => {
    let m = 1;
    for (const c of visible) {
      for (const v of Object.values(c.by_borough)) {
        if (v > m) m = v;
      }
    }
    return m;
  }, [visible]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load heatmap: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[400px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  if (data.categories.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <p className="text-sm text-neutral-500 italic dark:text-neutral-400">
          No taxonomy categories with location data yet — the heatmap populates
          as services are imported and tagged.
        </p>
      </div>
    );
  }

  // `visible` is computed above (via useMemo) so it's available to
  // matrixMax. Use it directly here.
  const hidden = data.categories.length - visible.length;

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      <div className="overflow-x-auto">
        <table className="w-full" role="grid" aria-label="Service category by borough coverage heatmap">
          <thead>
            <tr>
              {/* Category name column header */}
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400 border-b border-neutral-200 dark:border-neutral-800 sticky left-0 bg-white dark:bg-neutral-900 z-10"
              >
                Category
              </th>
              {/* Borough column headers */}
              {data.boroughs.map((b) => (
                <th
                  key={b}
                  scope="col"
                  className="px-3 py-3 text-center text-xs font-semibold text-neutral-500 dark:text-neutral-400 border-b border-neutral-200 dark:border-neutral-800 whitespace-nowrap"
                >
                  {b}
                </th>
              ))}
              {/* Totals column */}
              <th
                scope="col"
                className="px-3 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 border-b border-neutral-200 dark:border-neutral-800"
              >
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((cat) => (
              <CategoryRow
                key={cat.name}
                category={cat}
                boroughs={data.boroughs}
                matrixMax={matrixMax}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Expand/collapse control. Only renders when there's a meaningful
       *  hidden count to reveal. */}
      {data.categories.length > DEFAULT_VISIBLE_CATEGORIES && (
        <div className="px-4 py-3 border-t border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-800/30">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-xs font-medium text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300 inline-flex items-center gap-1"
            aria-expanded={expanded}
          >
            {expanded ? (
              <>
                <ChevronUp size={14} aria-hidden="true" />
                Show fewer
              </>
            ) : (
              <>
                <ChevronDown size={14} aria-hidden="true" />
                Show {hidden} more {hidden === 1 ? "category" : "categories"}
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * One category row. Each cell's background opacity scales with its
 * value relative to matrixMax (anchor to the global max so cross-
 * category comparison is meaningful — the whole point of a heatmap).
 *
 * Color: amber, matching the operations-charts palette. Empty cells
 * use a slightly different cool gray so "this category isn't
 * available in this borough" reads as visually distinct from "this
 * cell is just sparse" (which uses the same amber at low opacity).
 */
function CategoryRow({
  category,
  boroughs,
  matrixMax,
}: {
  category: HeatmapCategory;
  /** Boroughs come from the response — already typed as BoroughLabel
   *  on the backend. The function-arg typing widens to string only
   *  for the JSX iteration; we cast back inside the lookup. */
  boroughs: BoroughLabel[];
  matrixMax: number;
}) {
  return (
    <tr className="border-t border-neutral-100 dark:border-neutral-800">
      <th
        scope="row"
        className="px-4 py-2.5 text-left text-sm font-medium text-neutral-700 dark:text-neutral-300 sticky left-0 bg-white dark:bg-neutral-900 z-[1] whitespace-nowrap"
      >
        {category.name}
      </th>
      {boroughs.map((b) => {
        const value = category.by_borough[b] ?? 0;
        const intensity = matrixMax > 0 ? value / matrixMax : 0;
        // Discrete opacity buckets for cleaner visual stepping than
        // continuous interpolation. Cells with value=0 get an
        // explicit empty-state class so they're visually distinct
        // from "low-but-not-zero" cells.
        let cellClass: string;
        if (value === 0) {
          cellClass = "bg-neutral-50 dark:bg-neutral-800/30 text-neutral-300 dark:text-neutral-600";
        } else if (intensity < 0.1) {
          cellClass = "bg-amber-50 text-neutral-700 dark:bg-amber-950/40 dark:text-neutral-300";
        } else if (intensity < 0.25) {
          cellClass = "bg-amber-100 text-neutral-700 dark:bg-amber-900/40 dark:text-neutral-200";
        } else if (intensity < 0.5) {
          cellClass = "bg-amber-200 text-neutral-800 dark:bg-amber-800/50 dark:text-neutral-100";
        } else if (intensity < 0.75) {
          cellClass = "bg-amber-300 text-neutral-900 dark:bg-amber-700/60 dark:text-neutral-100";
        } else {
          cellClass = "bg-amber-400 text-neutral-900 dark:bg-amber-600/70 dark:text-neutral-50 font-semibold";
        }
        return (
          <Tooltip
            key={b}
            content={`${b} · ${category.name}: ${value.toLocaleString()} location${value === 1 ? "" : "s"}`}
          >
            <td
              className={`px-3 py-2.5 text-center text-sm tabular-nums transition-colors ${cellClass}`}
            >
              {value === 0 ? "—" : value.toLocaleString()}
            </td>
          </Tooltip>
        );
      })}
      {/* Total column — always neutral, not heat-mapped (a heat scale
       *  on the total would dominate the eye and obscure the per-cell
       *  pattern). */}
      <td className="px-3 py-2.5 text-right text-sm font-semibold text-neutral-900 dark:text-neutral-100 tabular-nums">
        {category.total_locations.toLocaleString()}
      </td>
    </tr>
  );
}
