// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { AlertCircle } from "lucide-react";
import type { BoroughBreakdownResponse } from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";

/**
 * Section 3a — borough breakdown table.
 *
 * Six fixed rows (5 NYC boroughs + Other), always in canonical
 * display order regardless of which the data contains. Empty
 * boroughs render as zeros — never missing — so admins build a
 * mental map of "I always see all 6, the question is just what
 * the numbers are."
 *
 * Columns:
 *   * Locations — total count
 *   * Service entries — count of service-at-location pairs at the
 *     borough's locations. NOT the count of distinct services —
 *     a multi-service location contributes one entry per service.
 *     Sum across boroughs equals total service-at-location rows,
 *     which is a different (typically larger) number than the
 *     stat strip's "Total services" (= distinct services in the
 *     catalog). The two answer different questions and that's
 *     intentional, but the column name needs to communicate it.
 *   * Avg entries / location — efficiency proxy; high values
 *     signal one location offering many services (e.g. a multi-
 *     service center), low values signal single-purpose locations
 *   * % verified <90d — freshness ratio for the borough
 *   * Top category — most-common taxonomy in this borough
 *
 * The bar in the "% verified" column is a small inline rendering —
 * easier to scan than raw numbers when comparing 6 rows.
 */
export function BoroughBreakdownTable() {
  const { data, loading, error } = useAdminFetch<BoroughBreakdownResponse>(
    "/api/admin/locations/by-borough",
  );


  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load borough breakdown: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[260px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-neutral-50 dark:bg-neutral-800/50">
            <tr>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Borough
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-right">
                Locations
              </th>
              <th
                scope="col"
                className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-right"
                title="Count of service-at-location pairs in this borough. A multi-service location contributes one entry per service — sum across boroughs is larger than the 'Total services' stat (which counts distinct services in the catalog)."
              >
                Service entries
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-right">
                Avg entries/loc
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Verified &lt;90d
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Top category
              </th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr
                key={row.borough}
                className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30"
              >
                <td className="px-4 py-3 text-sm font-medium text-neutral-900 dark:text-neutral-100">
                  {row.borough}
                </td>
                <td className="px-4 py-3 text-sm text-neutral-700 dark:text-neutral-300 text-right tabular-nums">
                  {row.location_count.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-sm text-neutral-700 dark:text-neutral-300 text-right tabular-nums">
                  {row.service_count.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-sm text-neutral-700 dark:text-neutral-300 text-right tabular-nums">
                  {row.location_count > 0 ? row.avg_services_per_location.toFixed(1) : "—"}
                </td>
                <td className="px-4 py-3">
                  <VerifiedBar pct={row.verified_lt90d_pct} />
                </td>
                <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-400">
                  {row.top_category || "—"}
                </td>
              </tr>
            ))}
            {/* Totals row — separated by a heavier border so it reads
             *  as summary, not as a 7th borough. */}
            <tr className="border-t-2 border-neutral-200 bg-neutral-50/40 dark:border-neutral-700 dark:bg-neutral-800/30">
              <td className="px-4 py-3 text-sm font-semibold text-neutral-700 dark:text-neutral-200">
                Total
              </td>
              <td className="px-4 py-3 text-sm font-semibold text-neutral-900 dark:text-neutral-100 text-right tabular-nums">
                {data.totals.location_count.toLocaleString()}
              </td>
              <td className="px-4 py-3 text-sm font-semibold text-neutral-900 dark:text-neutral-100 text-right tabular-nums">
                {data.totals.service_count.toLocaleString()}
              </td>
              <td className="px-4 py-3 text-sm text-neutral-500 dark:text-neutral-400 text-right">—</td>
              <td className="px-4 py-3 text-sm text-neutral-500 dark:text-neutral-400">—</td>
              <td className="px-4 py-3 text-sm text-neutral-500 dark:text-neutral-400">—</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Inline visual + numeric for the verified-lt90d percentage.
 * The bar gives a fast "ranked across boroughs" read; the number
 * gives the precise value when the comparison matters.
 *
 * Color: green at high freshness, amber mid, red low. Matches the
 * verification-age color cues elsewhere in the admin.
 */
function VerifiedBar({ pct }: { pct: number | null }) {
  if (pct === null) {
    return <span className="text-neutral-400 dark:text-neutral-500">—</span>;
  }
  const colorClass =
    pct >= 50 ? "bg-green-500"
    : pct >= 25 ? "bg-amber-500"
    : "bg-red-500";
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-2 rounded-full bg-neutral-100 dark:bg-neutral-800 overflow-hidden">
        <div
          className={`h-full ${colorClass} rounded-full transition-all`}
          style={{ width: `${Math.min(100, Math.max(2, pct))}%` }}
          title={`${pct.toFixed(1)}%`}
        />
      </div>
      <span className="text-xs text-neutral-700 dark:text-neutral-300 tabular-nums w-12 text-right">
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}
