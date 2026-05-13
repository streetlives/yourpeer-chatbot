// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import { AlertCircle, AlertTriangle, ExternalLink, MapPin } from "lucide-react";
import type {
  CoordinateIssuesResponse,
  CoordinateIssue,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";

/**
 * Section 3c — coordinate validation table.
 *
 * Surfaces locations whose lat/lon doesn't match their declared city.
 * Two distinct issue kinds, both worth fixing:
 *
 *   * "Outside NYC" — coords fall outside all 5 borough polygons.
 *     Almost certainly a typo or a wrongly-imported out-of-state row.
 *     User-facing impact: invisible to proximity searches; can't be
 *     filtered to in geographic queries. Sorts FIRST since these are
 *     the most concerning data bugs.
 *
 *   * "Borough mismatch" — coords are in NYC but a different borough
 *     than the address record claims. Could be either side wrong;
 *     manual verification needed. User-facing impact: location may
 *     show up in the "wrong" borough's search and be missed by users
 *     filtering to the correct one.
 *
 * Header includes an "X of Y locations have issues" framing so admins
 * can see the scale at a glance, plus two toggle pills (one per issue
 * kind) for narrowing the view. Outside-NYC tends to dominate the row
 * count in production data; toggling it off lets admins focus on the
 * smaller borough-mismatch backlog without scrolling past dozens of
 * obvious typo rows. Toggles affect only the rendered table — the
 * count strip continues to show the full totals so admins see what's
 * been hidden.
 *
 * Empty state ("No coordinate issues — all locations match their
 * declared city") gets a subtle ✓ green callout. This is the
 * outcome we WANT to be true after a cleanup pass; surfacing it
 * positively gives the team a visible win when the queue clears.
 */
export function CoordinateIssuesTable() {
  const { data, loading, error } = useAdminFetch<CoordinateIssuesResponse>(
    "/api/admin/locations/coordinate-issues",
  );

  // Issue-type filters.
  //
  // Borough-mismatch defaults to ON — those rows are the ones a
  // staffer can actually triage (each one is a yes/no decision
  // between "address is wrong" and "coords are wrong"), and the
  // backlog is small enough to scan end-to-end.
  //
  // Outside-NYC defaults to OFF. In production data, out-of-state
  // typos dominate the issue count — the rationale comment at the
  // top of the file already calls this out as "toggling it off lets
  // admins focus on the smaller borough-mismatch backlog without
  // scrolling past dozens of obvious typo rows." Defaulting it OFF
  // makes the recommended triage view the page's starting state
  // instead of requiring every staffer to click the toggle before
  // they can work. The summary strip still shows the full
  // outside-NYC count, so it's visible-but-collapsed, not hidden.
  //
  // Local state — no URL sync (the table is a single panel;
  // deep-linking a filtered view isn't a user need yet) and no
  // persistence (workflow is "open the page, triage, leave" —
  // sticky filters would surprise the next visit). Toggling back
  // ON is one click, so power-users who want the full view can
  // still get there easily.
  const [showOutsideNYC, setShowOutsideNYC] = useState(false);
  const [showBoroughMismatch, setShowBoroughMismatch] = useState(true);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load coordinate validation: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[200px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  if (data.issues.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
          <span className="text-base">✓</span>
          <span>
            No coordinate issues — all {data.total_with_coords.toLocaleString()} located locations match their declared city.
          </span>
        </div>
      </div>
    );
  }

  const mismatchCount = data.issues.length - data.outside_nyc_count;

  // Apply the toggle filters. A row is "outside NYC" when
  // `computed_borough === null` (the same predicate `IssueRow` uses
  // for its rendering branch); the inverse is the borough-mismatch
  // case. Hidden rows still count in the summary strip totals so
  // admins can see what's been filtered out.
  const visibleIssues = data.issues.filter((issue) => {
    const isOutsideNYC = issue.computed_borough === null;
    if (isOutsideNYC) return showOutsideNYC;
    return showBoroughMismatch;
  });
  const hiddenCount = data.issues.length - visibleIssues.length;

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      {/* Summary strip — gives the "X of Y" framing before the table */}
      <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 dark:bg-amber-950/20 dark:border-amber-900/50">
        <div className="flex items-center gap-2 text-sm text-amber-900 dark:text-amber-200">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>
            <span className="font-semibold">{data.issues.length.toLocaleString()}</span>
            {" of "}
            <span className="font-semibold">{data.total_with_coords.toLocaleString()}</span>
            {" located locations have issues — "}
            {data.outside_nyc_count > 0 && (
              <>
                <span className="font-semibold">{data.outside_nyc_count.toLocaleString()}</span> outside NYC
                {mismatchCount > 0 && ", "}
              </>
            )}
            {mismatchCount > 0 && (
              <>
                <span className="font-semibold">{mismatchCount.toLocaleString()}</span> with borough mismatch
              </>
            )}
          </span>
        </div>

        {/* Issue-type toggles. Only render a toggle for issue kinds
            that have at least one row — no point offering a "Hide
            outside NYC" pill when there are zero outside-NYC rows. */}
        {(data.outside_nyc_count > 0 || mismatchCount > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-amber-800/80 dark:text-amber-300/80">
              Show
            </span>
            {data.outside_nyc_count > 0 && (
              <FilterPill
                active={showOutsideNYC}
                label="Outside NYC"
                count={data.outside_nyc_count}
                onToggle={() => setShowOutsideNYC((v) => !v)}
              />
            )}
            {mismatchCount > 0 && (
              <FilterPill
                active={showBoroughMismatch}
                label="Borough mismatch"
                count={mismatchCount}
                onToggle={() => setShowBoroughMismatch((v) => !v)}
              />
            )}
            {hiddenCount > 0 && (
              <span
                className="text-xs text-amber-800/70 dark:text-amber-300/70"
                aria-live="polite"
              >
                {hiddenCount.toLocaleString()} hidden
              </span>
            )}
          </div>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-neutral-50 dark:bg-neutral-800/50">
            <tr>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Location
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Stated city
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Issue
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">
                Coords
              </th>
              <th scope="col" className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-right">
                Open
              </th>
            </tr>
          </thead>
          <tbody>
            {visibleIssues.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-8 text-center text-sm text-neutral-400 dark:text-neutral-500"
                >
                  All issues are hidden by the filters above. Toggle a
                  pill back on to see them.
                </td>
              </tr>
            ) : (
              visibleIssues.map((issue) => (
                <IssueRow key={issue.location_id} issue={issue} />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Two-state toggle pill matching the amber summary-strip palette.
 * Active = solid amber background, content visible in the table.
 * Inactive = outlined, content hidden. Includes the row count so
 * admins know the size of what they're toggling.
 *
 * The `aria-pressed` state is the canonical signal for assistive
 * technology — visual treatment is layered on top. Hit area is the
 * full pill (button); the count is part of the label, not a separate
 * focusable element.
 */
function FilterPill({
  active,
  label,
  count,
  onToggle,
}: {
  active: boolean;
  label: string;
  count: number;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={[
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full",
        "text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-amber-500 focus-visible:ring-offset-1",
        "focus-visible:ring-offset-amber-50 dark:focus-visible:ring-offset-amber-950/30",
        active
          ? "bg-amber-200 text-amber-900 hover:bg-amber-300 dark:bg-amber-900/60 dark:text-amber-100 dark:hover:bg-amber-900/80"
          : "bg-transparent text-amber-700/70 ring-1 ring-inset ring-amber-300 hover:bg-amber-100 dark:text-amber-300/70 dark:ring-amber-800 dark:hover:bg-amber-900/30",
      ].join(" ")}
    >
      <span
        className={[
          "w-1.5 h-1.5 rounded-full",
          active ? "bg-amber-700 dark:bg-amber-300" : "bg-amber-400/50 dark:bg-amber-700/60",
        ].join(" ")}
        aria-hidden="true"
      />
      {label}
      <span className="tabular-nums opacity-80">
        ({count.toLocaleString()})
      </span>
    </button>
  );
}

function IssueRow({ issue }: { issue: CoordinateIssue }) {
  const isOutsideNYC = issue.computed_borough === null;

  return (
    <tr className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30">
      <td className="px-4 py-3 text-sm">
        <div className="font-medium text-neutral-900 dark:text-neutral-100">
          {issue.location_name}
        </div>
        {issue.organization && (
          <div className="text-xs text-neutral-500 dark:text-neutral-400">
            {issue.organization}
          </div>
        )}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300">
        {issue.stated_city || "—"}
      </td>
      <td className="px-4 py-3 text-sm">
        {isOutsideNYC ? (
          <span className="inline-flex items-center gap-1.5 text-red-700 dark:text-red-400">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 dark:bg-red-400 inline-block" aria-hidden="true" />
            Outside NYC
          </span>
        ) : (
          <span className="text-amber-700 dark:text-amber-400">
            Coords say{" "}
            <span className="font-semibold">{issue.computed_borough}</span>
            {issue.stated_borough && (
              <>
                {" "}
                <span className="text-neutral-400 dark:text-neutral-500">·</span>{" "}
                stated as{" "}
                <span className="font-semibold">{issue.stated_borough}</span>
              </>
            )}
          </span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-neutral-500 dark:text-neutral-400 font-mono tabular-nums whitespace-nowrap">
        <MapPin size={11} className="inline -mt-0.5 mr-1 text-neutral-400 dark:text-neutral-500" aria-hidden="true" />
        {issue.latitude.toFixed(4)}, {issue.longitude.toFixed(4)}
      </td>
      <td className="px-4 py-3 text-right">
        <a
          href={issue.yourpeer_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300"
          aria-label={`Open ${issue.location_name} on YourPeer`}
        >
          YourPeer <ExternalLink size={12} aria-hidden="true" />
        </a>
      </td>
    </tr>
  );
}
