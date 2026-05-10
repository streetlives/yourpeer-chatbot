// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { AlertCircle, AlertTriangle, MessageSquare } from "lucide-react";
import type {
  FeedbackAggregatesResponse,
  MostFlaggedRow,
  CriterionSummaryRow,
  FeedbackCriterion,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";
import {
  FEEDBACK_CRITERIA,
  FEEDBACK_CRITERION_LABELS,
} from "@/lib/admin/locations-types";

/**
 * Sections 5a + 5b — location feedback aggregates.
 *
 * Renders both panels from a single fetch: 5b "Per-criterion baseline"
 * appears first as a 4-bar summary so admins build a population
 * baseline before reading individual rows; 5a "Most-flagged locations"
 * follows with the per-location ranking.
 *
 * Empty state: when no feedback exists yet, the panel collapses to a
 * single positive callout so the page doesn't have a hollow gap.
 *
 * Why one component, not two: the two sections share both source data
 * (location_feedback events) and conceptual framing (one is the
 * baseline, one is the deviation from baseline). Splitting them into
 * separate components would mean two redundant fetches and a layout
 * decision about how to visually pair them. Single component, single
 * fetch, paired layout out of the box.
 */
export function FeedbackAggregatesPanel() {
  const { data, loading, error } = useAdminFetch<FeedbackAggregatesResponse>(
    "/api/admin/locations/feedback-aggregates",
  );


  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load feedback aggregates: {error}
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

  // Whole-section empty state — feedback is sparse in pilot, this
  // is more likely to be true than not for a while. Single positive
  // callout vs two empty panels.
  if (data.total_events_overall === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-400">
          <MessageSquare size={14} aria-hidden="true" />
          <span>
            No feedback events yet — populates as users rate locations from service cards.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <CriterionBaseline data={data} />
      <MostFlaggedTable data={data} />
    </div>
  );
}

/**
 * Section 5b — per-criterion population baseline.
 *
 * Four small bar widgets, one per criterion, showing the negative-flag
 * rate across ALL feedback events. Reads as a baseline: "of every
 * safety rating we've gotten, X% were negative." Pairs with the
 * most-flagged ranking below — a location's 50% safety-negative
 * rate reads differently when the baseline is 5% vs 30%.
 *
 * Bars show negative_pct on a 0-100 scale, color-cued: red >=30%,
 * amber >=10%, green below. Thresholds are judgement; tunable here.
 * The denominator (events_rated) is shown as small text below the
 * bar so admins can see how much data is behind each %.
 *
 * Criteria with events_rated=0 (no one has rated this criterion yet)
 * render with a "—" placeholder rather than a 0%-tall bar — the
 * absent-data state is meaningfully distinct from "0% negative."
 */
function CriterionBaseline({ data }: { data: FeedbackAggregatesResponse }) {
  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <div>
          <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
            Per-criterion baseline
          </div>
          <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
            How often each criterion gets flagged across {data.total_events_overall.toLocaleString()}{" "}
            event{data.total_events_overall === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {FEEDBACK_CRITERIA.map((crit) => (
          <CriterionBar
            key={crit}
            criterion={crit}
            summary={data.criterion_summary[crit]}
          />
        ))}
      </div>
    </div>
  );
}

function CriterionBar({
  criterion,
  summary,
}: {
  criterion: FeedbackCriterion;
  summary: CriterionSummaryRow;
}) {
  const label = FEEDBACK_CRITERION_LABELS[criterion];
  const pct = summary.negative_pct;
  const noData = summary.events_rated === 0 || pct === null;

  // Color cue thresholds — same scale as the no-result-rate cues in
  // operations-charts and the demand:supply ratio in section 4a so
  // admins build the same color-meaning association across pages.
  let barColor = "bg-emerald-400 dark:bg-emerald-500";
  let textColor = "text-emerald-700 dark:text-emerald-400";
  if (!noData && pct! >= 30) {
    barColor = "bg-red-500 dark:bg-red-600";
    textColor = "text-red-700 dark:text-red-400";
  } else if (!noData && pct! >= 10) {
    barColor = "bg-amber-400 dark:bg-amber-500";
    textColor = "text-amber-700 dark:text-amber-400";
  }

  return (
    <div className="rounded border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="text-xs font-medium text-neutral-700 dark:text-neutral-300 mb-2">
        {label}
      </div>
      {noData ? (
        <>
          <div className="h-2 rounded-full bg-neutral-100 dark:bg-neutral-800 mb-2" />
          <div className="text-lg font-semibold text-neutral-300 dark:text-neutral-600">—</div>
          <div className="text-[0.7rem] text-neutral-400 dark:text-neutral-500">Not rated</div>
        </>
      ) : (
        <>
          {/* Bar showing pct on a 0-100 scale. Min 4% width so a 1%
           *  bar is still visible (keeps the visual a "bar", not a
           *  vanishing line). */}
          <div className="h-2 rounded-full bg-neutral-100 dark:bg-neutral-800 mb-2 overflow-hidden">
            <div
              className={`h-full ${barColor} rounded-full transition-all`}
              style={{ width: `${Math.max(4, Math.min(100, pct!))}%` }}
            />
          </div>
          <div className={`text-lg font-semibold ${textColor}`}>
            {pct!.toFixed(1)}%
          </div>
          <div className="text-[0.7rem] text-neutral-500 dark:text-neutral-400">
            {summary.negative.toLocaleString()} of {summary.events_rated.toLocaleString()} negative
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Section 5a — most-flagged locations.
 *
 * Table of top-N locations ranked by smoothed negative ratio.
 * Columns:
 *   - Location name (+ org? not surfaced today; backend snapshot
 *     is name-only — defer)
 *   - Total events (the sample size)
 *   - Per-criterion counts (4 mini-bars, neg out of rated)
 *   - Smoothed ratio (the sort key) + raw ratio for transparency
 *   - Last event timestamp
 *   - Comments count
 *
 * Empty state: when no locations meet the min_sample cutoff, surface
 * that explicitly so admins know it's not a bug. "0 locations have
 * accumulated 2+ events yet" reads as "system is working, just
 * sparse" vs a hollow empty table.
 */
function MostFlaggedTable({ data }: { data: FeedbackAggregatesResponse }) {
  if (data.most_flagged.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 mb-1">
          Most-flagged locations
        </div>
        <p className="text-xs text-neutral-500 dark:text-neutral-400 italic">
          No locations have accumulated {data.min_sample}+ feedback events yet. Once feedback
          volume picks up, the most-flagged ranking will appear here.
        </p>
      </div>
    );
  }

  const truncated = data.total_eligible > data.most_flagged.length;

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
        <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          Most-flagged locations
        </div>
        <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
          {truncated ? (
            <>
              Top <span className="font-medium">{data.most_flagged.length}</span> of{" "}
              <span className="font-medium">{data.total_eligible}</span> qualifying location
              {data.total_eligible === 1 ? "" : "s"} (≥{data.min_sample} feedback events).
              Sorted by smoothed negative ratio.
            </>
          ) : (
            <>
              <span className="font-medium">{data.total_eligible}</span> qualifying location
              {data.total_eligible === 1 ? "" : "s"} (≥{data.min_sample} feedback events).
              Sorted by smoothed negative ratio.
            </>
          )}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-neutral-50 dark:bg-neutral-800/50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs uppercase tracking-wider font-semibold text-neutral-400">
                Location
              </th>
              <th scope="col" className="px-3 py-3 text-right text-xs uppercase tracking-wider font-semibold text-neutral-400">
                Events
              </th>
              {FEEDBACK_CRITERIA.map((crit) => (
                <th
                  key={crit}
                  scope="col"
                  className="px-3 py-3 text-center text-xs uppercase tracking-wider font-semibold text-neutral-400 whitespace-nowrap"
                >
                  {FEEDBACK_CRITERION_LABELS[crit]}
                </th>
              ))}
              <th
                scope="col"
                className="px-3 py-3 text-right text-xs uppercase tracking-wider font-semibold text-neutral-400 whitespace-nowrap"
                title="Smoothed via Laplace add-one ((neg+1)/(total+2)) to avoid 100% cliffs at low samples. Raw ratio shown alongside."
              >
                Negative ratio
              </th>
              <th scope="col" className="px-3 py-3 text-right text-xs uppercase tracking-wider font-semibold text-neutral-400">
                Comments
              </th>
              <th scope="col" className="px-3 py-3 text-right text-xs uppercase tracking-wider font-semibold text-neutral-400 whitespace-nowrap">
                Last event
              </th>
            </tr>
          </thead>
          <tbody>
            {data.most_flagged.map((row) => (
              <FlaggedRow key={row.location_id} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FlaggedRow({ row }: { row: MostFlaggedRow }) {
  // Color cue on the smoothed ratio — same thresholds as the
  // criterion-baseline bars so the row's "alarm-ness" reads
  // consistently with the population baseline above.
  const smoothedPct = row.negative_ratio_smoothed * 100;
  let ratioClass = "text-emerald-700 dark:text-emerald-400";
  if (smoothedPct >= 50) {
    ratioClass = "text-red-700 dark:text-red-400 font-semibold";
  } else if (smoothedPct >= 25) {
    ratioClass = "text-amber-700 dark:text-amber-400 font-medium";
  }

  return (
    <tr className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30">
      <td className="px-4 py-3 text-sm font-medium text-neutral-900 dark:text-neutral-100">
        {row.location_name || (
          <span className="text-neutral-400 italic font-normal">
            (no name captured)
          </span>
        )}
        <div className="text-[0.7rem] text-neutral-400 font-mono">
          {row.location_id.slice(0, 8)}
          {row.location_id.length > 8 && "…"}
        </div>
      </td>
      <td className="px-3 py-3 text-sm text-neutral-700 dark:text-neutral-300 text-right tabular-nums">
        {row.total_events.toLocaleString()}
      </td>
      {FEEDBACK_CRITERIA.map((crit) => {
        const counts = row.criterion_counts[crit];
        return (
          <td key={crit} className="px-3 py-3 text-center text-xs">
            {counts.rated === 0 ? (
              <span className="text-neutral-300 dark:text-neutral-600">—</span>
            ) : (
              <CriterionMicroBar counts={counts} />
            )}
          </td>
        );
      })}
      <td className={`px-3 py-3 text-sm text-right tabular-nums ${ratioClass}`}>
        <span className="inline-flex items-center gap-1">
          {smoothedPct >= 50 && (
            <AlertTriangle size={11} aria-hidden="true" />
          )}
          <span>{smoothedPct.toFixed(0)}%</span>
        </span>
        <div className="text-[0.7rem] text-neutral-400 dark:text-neutral-500 font-normal mt-0.5">
          raw {(row.raw_negative_ratio * 100).toFixed(0)}%
        </div>
      </td>
      <td className="px-3 py-3 text-sm text-right tabular-nums">
        {row.comments_count > 0 ? (
          <span className="inline-flex items-center gap-1 text-neutral-700 dark:text-neutral-300">
            <MessageSquare size={11} aria-hidden="true" />
            {row.comments_count}
          </span>
        ) : (
          <span className="text-neutral-300 dark:text-neutral-600">—</span>
        )}
      </td>
      <td className="px-3 py-3 text-xs text-neutral-500 dark:text-neutral-400 text-right whitespace-nowrap tabular-nums">
        {formatRelativeTime(row.last_event_at)}
      </td>
    </tr>
  );
}

/**
 * Inline mini-bar for the per-criterion cell in the most-flagged
 * table. Shows neg/rated as a small visual hint plus the count
 * underneath. Compact enough to fit in a narrow column.
 */
function CriterionMicroBar({ counts }: { counts: { positive: number; negative: number; rated: number } }) {
  const negPct = (counts.negative / counts.rated) * 100;
  let cls = "bg-emerald-400 dark:bg-emerald-500";
  if (negPct >= 50) cls = "bg-red-500 dark:bg-red-600";
  else if (negPct >= 25) cls = "bg-amber-400 dark:bg-amber-500";

  return (
    <div className="inline-flex flex-col items-center gap-0.5">
      <div className="w-12 h-1 rounded-full bg-neutral-100 dark:bg-neutral-800 overflow-hidden">
        <div
          className={`h-full ${cls} rounded-full`}
          style={{ width: `${Math.max(4, negPct)}%` }}
        />
      </div>
      <span className="text-[0.65rem] text-neutral-500 dark:text-neutral-400 tabular-nums">
        {counts.negative}/{counts.rated}
      </span>
    </div>
  );
}

/**
 * Lightweight relative-time formatter for the last-event column.
 * Matches the style used in locations-table.tsx (today / N days ago /
 * N months ago / N years ago) so the page reads consistently.
 *
 * Inline rather than lifted to a shared util because the format is
 * the only thing that varies between admin tables and the rest of
 * the app uses native Intl.RelativeTimeFormat — the existing util
 * doesn't fit our "today" / "X months ago" preferred form.
 */
function formatRelativeTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (isNaN(date.getTime())) return "—";
  const now = new Date();
  const days = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
  if (days < 1) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  if (days < 60) return "1 month ago";
  if (days < 365) return `${Math.floor(days / 30)} months ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}
