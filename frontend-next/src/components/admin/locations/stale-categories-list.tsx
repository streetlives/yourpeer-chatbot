// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { AlertCircle, Clock } from "lucide-react";
import type { StaleCategoriesResponse } from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";

/**
 * Section 4b — categories with no recently-verified offering location.
 *
 * Surfaces the long-tail of forgotten taxonomies: a category counts
 * as stale when its single most-recently-verified offering location
 * is older than 180 days. Even one fresh location pulls a category
 * out of this list, so showing up here means literally no recent
 * verification touched anything in this category.
 *
 * Empty state is a positive callout: "✓ No category-wide stale
 * data — every taxonomy has at least one location verified in the
 * last 180 days." This is the outcome the team WANTS to be true,
 * so surfacing it positively gives them a visible win.
 */
export function StaleCategoriesList() {
  const { data, loading, error } = useAdminFetch<StaleCategoriesResponse>(
    "/api/admin/locations/stale-categories",
  );


  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load stale categories: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[160px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  if (data.categories.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
          <span className="text-base">✓</span>
          <span>
            No category-wide stale data — every taxonomy has at least one location
            verified in the last {data.lookback_days} days.
          </span>
        </div>
      </div>
    );
  }

  const hasMore = data.total_stale > data.categories.length;

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      {/* Summary banner */}
      <div className="px-4 py-2.5 bg-amber-50 border-b border-amber-200 dark:bg-amber-950/20 dark:border-amber-900/50">
        <div className="flex items-center gap-2 text-sm text-amber-900 dark:text-amber-200">
          <Clock size={12} aria-hidden="true" />
          <span>
            {hasMore ? (
              <>
                Showing top <span className="font-semibold">{data.categories.length}</span> of{" "}
                <span className="font-semibold">{data.total_stale}</span> categories where every
                offering location is &gt;{data.lookback_days} days old.
              </>
            ) : (
              <>
                <span className="font-semibold">{data.total_stale}</span>{" "}
                {data.total_stale === 1 ? "category has" : "categories have"} no recent verification
                across any offering location ({data.lookback_days}+ days).
              </>
            )}
          </span>
        </div>
      </div>

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
                Locations
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400 whitespace-nowrap"
              >
                Most recent verification
              </th>
            </tr>
          </thead>
          <tbody>
            {data.categories.map((cat) => (
              <tr
                key={cat.taxonomy_name}
                className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30"
              >
                <td className="px-4 py-3 text-sm font-medium text-neutral-900 dark:text-neutral-100">
                  {cat.taxonomy_name}
                </td>
                <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 text-right tabular-nums">
                  {cat.location_count.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-sm">
                  {cat.days_since_max_verified === null ? (
                    <span className="inline-flex items-center gap-1.5 text-red-700 dark:text-red-400">
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 dark:bg-red-400 inline-block" aria-hidden="true" />
                      Never verified
                    </span>
                  ) : cat.days_since_max_verified >= 365 ? (
                    <span className="text-red-700 dark:text-red-400">
                      {Math.floor(cat.days_since_max_verified / 365)}y{" "}
                      {Math.floor((cat.days_since_max_verified % 365) / 30)}mo ago
                    </span>
                  ) : (
                    <span className="text-amber-700 dark:text-amber-400">
                      {cat.days_since_max_verified} days ago
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
