// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useRef, useState } from "react";
import { LocationsTopStatStrip } from "@/components/admin/locations/top-stat-strip";
import { FreshnessHistogram } from "@/components/admin/locations/freshness-histogram";
import { BoroughBreakdownTable } from "@/components/admin/locations/borough-breakdown";
import { ServiceBoroughHeatmap } from "@/components/admin/locations/service-borough-heatmap";
import { CoordinateIssuesTable } from "@/components/admin/locations/coordinate-issues-table";
import { CategoryCoverageTable } from "@/components/admin/locations/category-coverage-table";
import { StaleCategoriesList } from "@/components/admin/locations/stale-categories-list";
import { LocationsTable } from "@/components/admin/locations/locations-table";
import { StatCardSkeleton } from "@/components/admin/loading-skeleton";
import type {
  LocationsStats,
  LocationsAgeBucket,
} from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";
import { AlertCircle } from "lucide-react";

/**
 * Locations admin page (v1, days 1-2 scope).
 *
 * Layout:
 *   1. Stat strip (section 1)
 *   2. Freshness histogram (section 2a) — clicking a bar drives
 *      the table's age_bucket filter via lifted state
 *   3. Borough breakdown table (section 3a)
 *   4. Triage table (section 2b)
 *
 * The histogram → table click-through works by lifting the table's
 * age_bucket filter up to the page. The table accepts ageBucket as
 * an optional controlled prop; when the page passes it (along with
 * a setter), the histogram's onBucketClick callback can update the
 * page state and the table re-renders with the new filter applied.
 *
 * Other section 2b filters stay table-internal — only age_bucket
 * needs cross-component coordination today. If section 4 (category
 * coverage) ever wants to drive the table's category filter via
 * click-through, the same lifting pattern applies.
 */
export default function LocationsPage() {
  const [stats, setStats] = useState<LocationsStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Lifted age_bucket — driven by the histogram's bar click.
  const [ageBucket, setAgeBucket] = useState<LocationsAgeBucket | "">("");

  // Ref to the triage table heading so we can scroll to it on
  // histogram click. Without this the user clicks a bar and nothing
  // visibly happens until they scroll down.
  const tableHeadingRef = useRef<HTMLHeadingElement>(null);

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

  function handleHistogramClick(bucket: LocationsAgeBucket) {
    // Toggle: clicking the same bucket again clears the filter.
    // Standard pattern across data-viz click-through filters; users
    // expect "click on, click off" symmetry.
    setAgeBucket((current) => (current === bucket ? "" : bucket));
    // Smooth-scroll the triage section into view so the user sees
    // the filter took effect.
    tableHeadingRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

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

      {/* Section 1: top stat strip */}
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

      {/* Section 2a: freshness histogram. Clicking a bar drives the
       *  triage table's age_bucket filter (see handleHistogramClick). */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Data freshness
        </h2>
        <FreshnessHistogram onBucketClick={handleHistogramClick} />
      </div>

      {/* Section 3a: borough breakdown */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Locations by borough
        </h2>
        <BoroughBreakdownTable />
      </div>

      {/* Section 3b: service-category × borough heatmap. Top 10 by
       *  default; expand toggle in the component reveals the rest. */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Service categories by borough
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Cell color shows location count, anchored to the brightest cell across the matrix.
          Use this to spot equity gaps — categories where a borough is empty (—) or near-empty
          relative to its peers.
        </p>
        <ServiceBoroughHeatmap />
      </div>

      {/* Section 3c: coordinate validation. Surfaces data-quality bugs:
       *  coords outside NYC, or coords that disagree with the stated city. */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Coordinate validation
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Locations whose lat/lon doesn&apos;t match their declared city.
          Outside-NYC issues are likely typo&apos;d coordinates;
          borough-mismatch issues need manual verification — either side could be wrong.
        </p>
        <CoordinateIssuesTable />
      </div>

      {/* Section 4a: per-taxonomy coverage with demand:supply ratio.
       *  Default sort surfaces categories where users keep asking and
       *  supply is thin — the most operationally useful triage prompt. */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Service-type coverage
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Per-taxonomy supply (services + locations) paired with chat demand.
          Default sort: highest demand-to-supply ratio first — the operational
          prompt for &ldquo;where should we focus partner outreach this quarter?&rdquo;
        </p>
        <CategoryCoverageTable />
      </div>

      {/* Section 4b: stale categories — taxonomies where no offering
       *  location has been verified in 180+ days. */}
      <div className="mt-6">
        <h2 className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100">
          Stale categories
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Taxonomies where every offering location is &gt;180 days old. These are
          categories at risk of going stale system-wide — a single fresh location
          would clear the alert.
        </p>
        <StaleCategoriesList />
      </div>

      {/* Section 2b: triage table. ageBucket is controlled by the page
       *  so the histogram can drive it. Other filters stay internal. */}
      <div className="mt-6">
        <h2
          ref={tableHeadingRef}
          className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100 scroll-mt-6"
        >
          Locations needing review
        </h2>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
          Default view shows least-recently-verified locations first
          (never-verified at the top). Use filters to narrow by borough,
          age bucket, or data-quality issues. The histogram above also
          drives the age filter — click a bar to apply, click again to clear.
        </p>
        <LocationsTable ageBucket={ageBucket} onAgeBucketChange={setAgeBucket} />
      </div>
    </>
  );
}
