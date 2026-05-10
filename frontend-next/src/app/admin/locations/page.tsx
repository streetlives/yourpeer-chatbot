// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useState } from "react";
import { LocationsTopStatStrip } from "@/components/admin/locations/top-stat-strip";
import { LocationsTable } from "@/components/admin/locations/locations-table";
import { StatCardSkeleton } from "@/components/admin/loading-skeleton";
import type { LocationsStats } from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";
import { AlertCircle } from "lucide-react";

/**
 * Locations admin page (v1, day 1 scope).
 *
 * Day 1 surfaces the top stat strip (section 1) and the triage
 * table (section 2b). Subsequent days add the freshness histogram,
 * geographic distribution, service-type coverage, location feedback,
 * data integrity callouts, and time series — all defined in the
 * spec at /mnt/user-data/outputs/locations_page_spec/SPEC.md.
 *
 * Page state design:
 *   * Stats are fetched once on mount (small payload, slow to change).
 *   * Table state lives entirely inside <LocationsTable/> so it can
 *     re-fetch independently on filter/sort/page changes without
 *     triggering a stats refetch.
 *
 * Both fetches go through the catch-all admin proxy at
 * /api/admin/[...slug] which forwards to /admin/api/locations/<endpoint>
 * with the server-side admin key. No special wiring needed — every
 * /api/admin/locations/<x> route Just Works.
 */
export default function LocationsPage() {
  const [stats, setStats] = useState<LocationsStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time fetch needs to mark loading state; standard pattern in this codebase
    setStatsLoading(true);
    fetch("/api/admin/locations/stats")
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        if (isAdminApiError(body)) {
          setStatsError(body.detail);
        } else {
          setStats(body as LocationsStats);
        }
        setStatsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setStatsError(String(err));
        setStatsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <div className="mb-2">
        <h1 className="text-xl font-semibold text-neutral-900 dark:text-neutral-100">
          Locations
        </h1>
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Operational overview of the Streetlives location catalog — what data we have,
          how fresh it is, and what users are flagging.
        </p>
      </div>

      <div className="mt-6">
        {statsError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
            <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
            Couldn&apos;t load location stats: {statsError}
          </div>
        )}
        {statsLoading && !stats && <StatCardSkeleton count={6} />}
        {stats && <LocationsTopStatStrip stats={stats} />}
      </div>

      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Locations needing review
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Default view shows least-recently-verified locations first
          (never-verified at the top). Use filters to narrow by borough,
          age bucket, or data-quality issues.
        </p>
        <LocationsTable />
      </div>
    </>
  );
}
