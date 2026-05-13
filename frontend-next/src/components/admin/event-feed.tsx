// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import type { AuditEvent } from "@/lib/chat/types";
import { useSortableTable } from "@/hooks/use-sortable-table";
import { SortableHeader } from "./sortable-header";
import { Tooltip } from "./tooltip";
import { formatRelativeTime, formatAbsoluteTooltip } from "@/lib/admin/format-time";
import {
  FEEDBACK_CRITERIA,
  FEEDBACK_CRITERION_LABELS,
} from "@/lib/admin/locations-types";

function typeBadge(type: string) {
  const label = type.replace(/_/g, " ");
  const cls =
    type === "crisis_detected"
      ? "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300"
      : type === "query_execution"
        ? "bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-300"
        : type === "session_reset"
          ? "bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300"
          : type === "feedback"
            ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300"
            : type === "location_feedback"
              ? "bg-teal-50 text-teal-600 dark:bg-teal-900/30 dark:text-teal-300"
              : "bg-neutral-100 text-neutral-400 dark:bg-neutral-800 dark:text-neutral-500";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${cls}`}>
      {label}
    </span>
  );
}

function feedbackBadge(rating?: string) {
  if (rating === "up") {
    return <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-300">👍 Helpful</span>;
  }
  if (rating === "down") {
    return <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">👎 Not helpful</span>;
  }
  return null;
}

interface EventFeedProps {
  events: AuditEvent[];
}

export function EventFeed({ events }: EventFeedProps) {
  const { sorted, sortKey, sortDir, onSort } = useSortableTable(
    events as unknown as Record<string, unknown>[],
    "timestamp",
    "desc",
  );

  // Note: the empty state is owned by the page-level <DataPanel> wrapper
  // in overview/page.tsx — same pattern as ConversationTable and
  // QueryLogTable. This component assumes it has rows to render.

  return (
    <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableHeader label="Time" field="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
            <SortableHeader label="Type" field="type" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
            <th className="text-left px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 border-b border-neutral-200 dark:border-neutral-800">
              Detail
            </th>
            <SortableHeader label="Session" field="session_id" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((e) => {
            const ev = e as unknown as AuditEvent;
            const time = formatRelativeTime(ev.timestamp);
            const timeTooltip = formatAbsoluteTooltip(ev.timestamp);
            // Composite key — timestamp alone isn't unique because a single
            // session can log multiple events at the same millisecond
            // boundary (e.g. conversation_turn immediately followed by
            // query_execution). Including type and session_id covers that.
            const rowKey = `${ev.timestamp}|${ev.type}|${ev.session_id ?? ""}`;
            let detail: React.ReactNode = "";

            if (ev.type === "conversation_turn") {
              detail = (
                <span className="max-w-[300px] truncate block">
                  {ev.user_message}
                </span>
              );
            } else if (ev.type === "query_execution") {
              detail = (
                <>
                  {ev.template_name} → {ev.result_count} results ({ev.execution_ms}ms)
                  {ev.relaxed && (
                    <span className="ml-1 inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
                      relaxed
                    </span>
                  )}
                </>
              );
            } else if (ev.type === "crisis_detected") {
              detail = (
                <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">
                  {ev.crisis_category}
                </span>
              );
            } else if (ev.type === "session_reset") {
              detail = "Session cleared";
            } else if (ev.type === "feedback") {
              const ctx = ev.context;
              detail = (
                <span className="flex flex-col gap-1">
                  <span className="flex items-center gap-2">
                    {feedbackBadge(ev.rating)}
                    {ev.comment && (
                      <span className="text-neutral-500 dark:text-neutral-400 max-w-[200px] truncate block">
                        &quot;{ev.comment}&quot;
                      </span>
                    )}
                  </span>
                  {ctx?.service_names && ctx.service_names.length > 0 && (
                    <span className="text-xs text-neutral-400 dark:text-neutral-500 leading-snug">
                      {ctx.result_count} results · {ctx.service_names.slice(0, 3).join(", ")}
                      {ctx.service_names.length > 3 ? ` +${ctx.service_names.length - 3} more` : ""}
                      {ctx.organizations?.[0] ? ` @ ${ctx.organizations[0]}` : ""}
                    </span>
                  )}
                  {!ctx?.service_names && ctx?.bot_response && (
                    <span className="text-xs text-neutral-400 dark:text-neutral-500 truncate max-w-[280px] block">
                      {ctx.bot_response.slice(0, 80)}{ctx.bot_response.length > 80 ? "…" : ""}
                    </span>
                  )}
                </span>
              );
            } else if (ev.type === "location_feedback") {
              // location_feedback events carry per-location details
              // (location_id, location_name, ratings dict) that were
              // added to AuditEvent on day 1 of the locations admin
              // build. Render the structured criteria alongside the
              // existing rating + comment so the feed row tells the
              // full story without the user needing to open the
              // transcript drawer.
              const ratings = ev.ratings ?? {};
              // Build a compact criterion summary: count negatives and
              // list them by short name. e.g. "2 flagged: safety,
              // cleanliness". Positive-only events show "all positive".
              // Skipping unrated criteria entirely — a chip per criterion
              // would crowd the feed table.
              const negativeCrits = FEEDBACK_CRITERIA.filter(
                (c) => ratings[c] === false,
              );
              const positiveCrits = FEEDBACK_CRITERIA.filter(
                (c) => ratings[c] === true,
              );
              const totalRated = negativeCrits.length + positiveCrits.length;
              let criteriaLine: React.ReactNode = null;
              if (negativeCrits.length > 0) {
                const labels = negativeCrits
                  .map((c) => FEEDBACK_CRITERION_LABELS[c].toLowerCase())
                  .join(", ");
                criteriaLine = (
                  <span className="text-xs text-red-600 dark:text-red-400">
                    {negativeCrits.length} flagged: {labels}
                  </span>
                );
              } else if (totalRated > 0) {
                criteriaLine = (
                  <span className="text-xs text-emerald-600 dark:text-emerald-400">
                    all positive ({totalRated})
                  </span>
                );
              }
              detail = (
                <span className="flex flex-col gap-1">
                  <span className="flex items-center gap-2 flex-wrap">
                    {feedbackBadge(ev.rating)}
                    {ev.location_name ? (
                      <span className="text-xs text-neutral-600 dark:text-neutral-300 max-w-[180px] truncate">
                        {ev.location_name}
                      </span>
                    ) : (
                      <span className="text-xs text-neutral-400 dark:text-neutral-500 italic">
                        location feedback
                      </span>
                    )}
                    {criteriaLine}
                    {ev.comment && (
                      <span className="text-neutral-500 dark:text-neutral-400 max-w-[200px] truncate block">
                        &quot;{ev.comment}&quot;
                      </span>
                    )}
                  </span>
                  {ev.context?.bot_response && (
                    <span className="text-xs text-neutral-400 dark:text-neutral-500 truncate max-w-[280px] block">
                      {ev.context.bot_response.slice(0, 80)}
                      {ev.context.bot_response.length > 80 ? "…" : ""}
                    </span>
                  )}
                </span>
              );
            }

            return (
              <tr key={rowKey} className="hover:bg-neutral-50/50 dark:hover:bg-neutral-800/40">
                <Tooltip content={timeTooltip}>
                  <td
                    className="px-4 py-2.5 font-mono text-xs border-b border-neutral-100 dark:border-neutral-800"
                  >
                    {time}
                  </td>
                </Tooltip>
                <td className="px-4 py-2.5 border-b border-neutral-100 dark:border-neutral-800">
                  {typeBadge(ev.type)}
                </td>
                <td className="px-4 py-2.5 border-b border-neutral-100 dark:border-neutral-800">
                  {detail}
                </td>
                {/* Truncated session id; full value in tooltip for
                    copy-paste reference. aria-label duplicates the
                    value so screen readers reach it without needing
                    to focus the cell. */}
                <Tooltip content={ev.session_id || ""}>
                  <td
                    className="px-4 py-2.5 font-mono text-xs text-neutral-400 dark:text-neutral-500 border-b border-neutral-100 dark:border-neutral-800"
                    aria-label={ev.session_id ? `Session ${ev.session_id}` : undefined}
                  >
                    {ev.session_id ? `${ev.session_id.slice(0, 12)}…` : ""}
                  </td>
                </Tooltip>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
