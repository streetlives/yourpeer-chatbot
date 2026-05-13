// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useMemo, useState } from "react";
import { AlertCircle, AlertTriangle, Info } from "lucide-react";
import type {
  CategoryCoverageResponse,
  CategoryCoverageRow,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";
import { Tooltip } from "@/components/admin/tooltip";

// Categories to show by default. Anything beyond surfaces under a
// "Show all" toggle so admins can drill into the long tail when
// they need to.
const DEFAULT_VISIBLE_CATEGORIES = 12;

/**
 * Section 4a — service-type coverage with demand:supply ratio.
 *
 * The most operationally useful metric on this page: which categories
 * have demand outpacing supply? Default sort puts those first.
 *
 * Demand attribution caveat shown via info icon next to the column
 * header. Best-effort split: a chat template covering N taxonomies
 * (e.g. FoodQuery covers 11 food-related taxonomies) divides its
 * demand evenly across them. Not precise — a "Food" search probably
 * hits "Food Pantry" more than "Farmer's Markets" — but a uniform
 * split is honest about the uncertainty rather than fabricating a
 * weighting we don't have data for.
 *
 * Empty demand columns (showing "—") on rows without a template
 * mapping are explicitly distinct from "0% demand"  — the
 * distinction matters for ops triage.
 */
export function CategoryCoverageTable() {
  const { data, loading, error } = useAdminFetch<CategoryCoverageResponse>(
    "/api/admin/locations/category-coverage",
  );
  const [expanded, setExpanded] = useState(false);


  const visibleRows = useMemo(() => {
    if (!data) return [];
    return expanded
      ? data.categories
      : data.categories.slice(0, DEFAULT_VISIBLE_CATEGORIES);
  }, [data, expanded]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load category coverage: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[300px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  if (data.categories.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <p className="text-sm text-neutral-500 italic dark:text-neutral-400">
          No taxonomy data available yet — the table populates as services
          are imported and tagged.
        </p>
      </div>
    );
  }

  const hasUncategorized = data.uncategorized_demand.query_count > 0;
  const hiddenCount = Math.max(
    0,
    data.categories.length - DEFAULT_VISIBLE_CATEGORIES,
  );

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      {/* Uncategorized-demand banner — only renders when there's
       *  attribution-orphaned demand to surface. The number tells
       *  admins how much demand signal is being lost to catch-all
       *  templates that can't be mapped to specific taxonomies. */}
      {hasUncategorized && (
        <div className="px-4 py-2.5 bg-neutral-50 border-b border-neutral-200 dark:bg-neutral-800/40 dark:border-neutral-800">
          <div className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
            <Info size={12} aria-hidden="true" />
            <span>
              <span className="font-medium text-neutral-700 dark:text-neutral-300">
                {data.uncategorized_demand.query_count.toLocaleString()}
              </span>{" "}
              queries went to catch-all templates and aren&apos;t attributed below
              {data.uncategorized_demand.no_result_count > 0 && (
                <>
                  {" "}({data.uncategorized_demand.no_result_count.toLocaleString()} returned no results)
                </>
              )}.
            </span>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-neutral-50 dark:bg-neutral-800/50">
            <tr>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400"
              >
                Category
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400"
              >
                Services
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400"
              >
                Locations
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400 whitespace-nowrap"
              >
                Verified &lt;90d
              </th>
              <Tooltip content="Approximate — chat-template demand divided evenly across each template's covered taxonomies.">
                <th
                  scope="col"
                  className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 whitespace-nowrap"
                >
                  Demand
                  <Info size={11} className="inline ml-1 -mt-0.5 text-neutral-300 dark:text-neutral-600" aria-hidden="true" />
                </th>
              </Tooltip>
              <th
                scope="col"
                className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 whitespace-nowrap"
              >
                No-result %
              </th>
              <Tooltip content="Demand queries per available location. High = users keep asking, supply is thin.">
                <th
                  scope="col"
                  className="px-4 py-3 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 whitespace-nowrap"
                >
                  Demand : Supply
                </th>
              </Tooltip>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((cat) => (
              <CoverageRow key={cat.taxonomy_name} category={cat} />
            ))}
          </tbody>
        </table>
      </div>

      {hiddenCount > 0 && (
        <div className="px-4 py-3 border-t border-neutral-200 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-800/30">
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-xs font-medium text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300"
            aria-expanded={expanded}
          >
            {expanded
              ? "Show fewer"
              : `Show ${hiddenCount} more ${hiddenCount === 1 ? "category" : "categories"}`}
          </button>
        </div>
      )}
    </div>
  );
}

function CoverageRow({ category: cat }: { category: CategoryCoverageRow }) {
  // Color cue on the demand:supply ratio — high values are the
  // operational alarm, so we tint amber/red. Threshold values are
  // judgement calls; tunable here.
  let ratioClass = "text-neutral-600 dark:text-neutral-400";
  if (cat.demand_supply_ratio !== null) {
    if (cat.demand_supply_ratio >= 1.0) {
      ratioClass = "text-red-700 dark:text-red-400 font-semibold";
    } else if (cat.demand_supply_ratio >= 0.25) {
      ratioClass = "text-amber-700 dark:text-amber-400 font-medium";
    } else {
      ratioClass = "text-neutral-700 dark:text-neutral-300";
    }
  }

  // No-result rate gets its own color cue — danger above 50%, warn
  // above 25%. Matches the operations-charts color thresholds.
  let nrClass = "text-neutral-500 dark:text-neutral-400";
  if (cat.no_result_rate !== null) {
    if (cat.no_result_rate >= 0.5) {
      nrClass = "text-red-700 dark:text-red-400";
    } else if (cat.no_result_rate >= 0.25) {
      nrClass = "text-amber-700 dark:text-amber-400";
    } else {
      nrClass = "text-neutral-700 dark:text-neutral-300";
    }
  }

  return (
    <tr className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30">
      <td className="px-4 py-3 text-sm font-medium text-neutral-900 dark:text-neutral-100">
        {cat.taxonomy_name}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 text-right tabular-nums">
        {cat.service_count.toLocaleString()}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 text-right tabular-nums">
        {cat.location_count.toLocaleString()}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-700 dark:text-neutral-300 tabular-nums whitespace-nowrap">
        {cat.verified_lt90d_pct !== null ? (
          <>
            {cat.fresh_location_count.toLocaleString()} ({cat.verified_lt90d_pct.toFixed(1)}%)
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 text-right tabular-nums">
        {cat.demand_query_count > 0 ? cat.demand_query_count.toFixed(1) : "—"}
      </td>
      <td className={`px-4 py-3 text-sm text-right tabular-nums ${nrClass}`}>
        {cat.no_result_rate !== null ? `${Math.round(cat.no_result_rate * 100)}%` : "—"}
      </td>
      <td className={`px-4 py-3 text-sm text-right tabular-nums ${ratioClass}`}>
        {cat.demand_supply_ratio !== null ? (
          <span className="inline-flex items-center gap-1">
            {cat.demand_supply_ratio >= 1.0 && (
              <AlertTriangle size={11} className="inline" aria-hidden="true" />
            )}
            {cat.demand_supply_ratio.toFixed(2)}
          </span>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}
