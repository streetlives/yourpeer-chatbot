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
import { FeedbackAggregatesPanel } from "@/components/admin/locations/feedback-aggregates";
import { FeedbackCommentsStream } from "@/components/admin/locations/feedback-comments-stream";
import { DataIntegrityCallouts } from "@/components/admin/locations/data-integrity-callouts";
import { LocationsTimeseries } from "@/components/admin/locations/locations-timeseries";
import { LocationsTable } from "@/components/admin/locations/locations-table";
import { StatCardSkeleton } from "@/components/admin/loading-skeleton";
import { AdminSection } from "@/components/admin/admin-section";
import type {
  LocationsStats,
  LocationsAgeBucket,
} from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";
import { AlertCircle } from "lucide-react";

/**
 * Locations admin page — v1 feature-complete.
 *
 * Layout (in display order; section IDs match the spec):
 *   1.  Stat strip                                       (section 1)
 *   2.  Freshness histogram                              (section 2a)
 *   3.  Borough breakdown                                (section 3a)
 *   4.  Service-category × borough heatmap               (section 3b)
 *   5.  Stale categories                                 (section 4b)
 *   6.  Service-type coverage with demand:supply         (section 4a)
 *   7.  Coordinate validation                            (section 3c)
 *   8.  Location feedback (per-criterion + most-flagged) (sections 5a+5b)
 *   9.  Recent feedback comments                         (section 5c)
 *   10. Data integrity callouts                          (section 6)
 *   11. Activity over time                               (section 7)
 *   12. Locations needing review (triage table)          (section 2b — placed last)
 *
 * Section numbers come from the original spec and no longer match the
 * display order — the spec's 3c/4a/4b cluster was reshuffled
 * (heatmap → stale → coverage → coords) so admins read the by-category
 * narrative as a continuous block before pivoting to coordinate
 * issues. Don't rely on section numbers to read the page top-down.
 *
 * Cross-component state coordination is intentionally minimal: only
 * the freshness-histogram → triage-table click-through needs lifting.
 * The page owns an `ageBucket` state; the histogram drives it via
 * onBucketClick and visualizes its current value via the activeBucket
 * prop; the triage table consumes it via its controlled ageBucket prop.
 *
 * Everything else fetches independently. 12 components, 12 endpoints,
 * each component owning its own loading / error / empty states. See
 * src/components/admin/locations/README.md for the section map.
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
      <AdminSection title="Data freshness">
        <FreshnessHistogram
          onBucketClick={handleHistogramClick}
          activeBucket={ageBucket}
        />
      </AdminSection>

      {/* Section 3a: borough breakdown */}
      <AdminSection title="Locations by borough">
        <BoroughBreakdownTable />
      </AdminSection>

      {/* Section 3b: service-category × borough heatmap. Top 10 by
       *  default; expand toggle in the component reveals the rest. */}
      <AdminSection
        title="Service categories by borough"
        description="Cell color shows location count, anchored to the brightest cell across the matrix. Use this to spot equity gaps — categories where a borough is empty (—) or near-empty relative to its peers."
      >
        <ServiceBoroughHeatmap />
      </AdminSection>

      {/* Section 4b: stale categories — taxonomies where no offering
       *  location has been verified in 180+ days. Sits between the
       *  service-category heatmap and the per-taxonomy coverage table
       *  so admins read "which categories exist, where" → "which are
       *  going stale" → "where the supply/demand gaps are" as a single
       *  thematic block. */}
      <AdminSection
        title="Stale categories"
        description={
          <>
            Taxonomies where every offering location is &gt;180 days old. These are
            categories at risk of going stale system-wide — a single fresh location
            would clear the alert.
          </>
        }
      >
        <StaleCategoriesList />
      </AdminSection>

      {/* Section 4a: per-taxonomy coverage with demand:supply ratio.
       *  Default sort surfaces categories where users keep asking and
       *  supply is thin — the most operationally useful triage prompt. */}
      <AdminSection
        title="Service-type coverage"
        description={
          <>
            Per-taxonomy supply (services + locations) paired with chat demand.
            Default sort: highest demand-to-supply ratio first — the operational
            prompt for &ldquo;where should we focus partner outreach this quarter?&rdquo;
          </>
        }
      >
        <CategoryCoverageTable />
      </AdminSection>

      {/* Section 3c: coordinate validation. Surfaces data-quality bugs:
       *  coords outside NYC, or coords that disagree with the stated city.
       *  Placed AFTER the category-coverage block because coordinate
       *  issues are a data-quality concern that affects every category
       *  uniformly — putting it here lets the reader finish the
       *  by-category narrative before switching to "and on top of that,
       *  some locations have busted coords." */}
      <AdminSection
        title="Coordinate validation"
        description={
          <>
            Locations whose lat/lon doesn&apos;t match their declared city.
            Outside-NYC issues are likely typo&apos;d coordinates;
            borough-mismatch issues need manual verification — either side could be wrong.
          </>
        }
      >
        <CoordinateIssuesTable />
      </AdminSection>

      {/* Sections 5a + 5b: location feedback aggregates. The component
       *  fetches once and renders the per-criterion baseline above the
       *  most-flagged ranking — admins build a population baseline
       *  before reading individual rows. Section 5c (comments stream)
       *  comes in a separate day. */}
      <AdminSection
        title="Location feedback"
        description="Per-criterion baseline across all feedback events, plus the most-flagged locations by smoothed negative ratio. Locations need at least 2 feedback events to qualify for the ranking."
      >
        <FeedbackAggregatesPanel />
      </AdminSection>

      {/* Section 5c: recent comments stream — qualitative companion
       *  to the quantitative feedback aggregates above. Each row is
       *  clickable and opens the originating session's transcript in
       *  the standard admin TranscriptDrawer. */}
      <AdminSection
        title="Recent feedback comments"
        description={
          <>
            The qualitative companion — comments often surface things that don&apos;t
            fit any criterion checkbox. Click a row to view the full chat session
            for context.
          </>
        }
      >
        <FeedbackCommentsStream />
      </AdminSection>

      {/* Section 6: data integrity callouts — query-driven panel that
       *  fires only when count > 0 of any check. Empty state is a
       *  positive ✓ "no issues detected" state. */}
      <AdminSection
        title="Data integrity"
        description={
          <>
            Catalog-wide health checks: orphaned records, malformed phone formats,
            encoded HTML in descriptions, and a rollup of the coordinate validation
            above. Each fires only when there&apos;s something to fix.
          </>
        }
      >
        <DataIntegrityCallouts />
      </AdminSection>

      {/* Section 7: time series — locations added / verified /
       *  feedback events by week, last 26 weeks. */}
      <AdminSection
        title="Activity over time"
        description="Last 26 weeks. The shape of these curves is the signal: steady cadence versus trending up versus recent spike each tell a different story about where the catalog is headed."
      >
        <LocationsTimeseries />
      </AdminSection>

      {/* Section 2b: triage table. ageBucket is controlled by the page
       *  so the histogram can drive it. Other filters stay internal.
       *  The section heading is the smooth-scroll target for histogram
       *  bar clicks — ref forwarded through AdminSection. */}
      <AdminSection
        ref={tableHeadingRef}
        title="Locations needing review"
        description="Default view shows least-recently-verified locations first (never-verified at the top). Use filters to narrow by borough, age bucket, or data-quality issues. The histogram above also drives the age filter — click a bar to apply, click again to clear."
      >
        <LocationsTable ageBucket={ageBucket} onAgeBucketChange={setAgeBucket} />
      </AdminSection>
    </>
  );
}
