// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useState } from "react";
import { AlertCircle, AlertTriangle, ExternalLink, MapPin } from "lucide-react";
import type {
  CoordinateIssuesResponse,
  CoordinateIssue,
} from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";

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
 * can see the scale at a glance.
 *
 * Empty state ("No coordinate issues — all locations match their
 * declared city") gets a subtle ✓ green callout. This is the
 * outcome we WANT to be true after a cleanup pass; surfacing it
 * positively gives the team a visible win when the queue clears.
 */
export function CoordinateIssuesTable() {
  const [data, setData] = useState<CoordinateIssuesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time fetch needs to mark loading state; standard pattern in this codebase
    setLoading(true);
    fetch("/api/admin/locations/coordinate-issues")
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        if (isAdminApiError(body)) {
          setError(body.detail);
        } else {
          setData(body as CoordinateIssuesResponse);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
      {/* Summary strip — gives the "X of Y" framing before the table */}
      <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 dark:bg-amber-950/20 dark:border-amber-900/50">
        <div className="flex items-center gap-2 text-xs text-amber-900 dark:text-amber-200">
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
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-neutral-50 dark:bg-neutral-800/50">
            <tr>
              <th scope="col" className="px-4 py-3 text-xs uppercase tracking-wider font-semibold text-neutral-400 text-left">
                Location
              </th>
              <th scope="col" className="px-4 py-3 text-xs uppercase tracking-wider font-semibold text-neutral-400 text-left">
                Stated city
              </th>
              <th scope="col" className="px-4 py-3 text-xs uppercase tracking-wider font-semibold text-neutral-400 text-left">
                Issue
              </th>
              <th scope="col" className="px-4 py-3 text-xs uppercase tracking-wider font-semibold text-neutral-400 text-left">
                Coords
              </th>
              <th scope="col" className="px-4 py-3 text-xs uppercase tracking-wider font-semibold text-neutral-400 text-right">
                Open
              </th>
            </tr>
          </thead>
          <tbody>
            {data.issues.map((issue) => (
              <IssueRow key={issue.location_id} issue={issue} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
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
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block" aria-hidden="true" />
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
