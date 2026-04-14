// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import type { AuditEvent } from "@/lib/chat/types";
import { useSortableTable } from "@/hooks/use-sortable-table";
import { SortableHeader } from "./sortable-header";

function typeBadge(type: string) {
  const label = type.replace(/_/g, " ");
  const cls =
    type === "crisis_detected"
      ? "bg-red-50 text-red-600"
      : type === "query_execution"
        ? "bg-blue-50 text-blue-600"
        : type === "session_reset"
          ? "bg-amber-50 text-amber-600"
          : type === "feedback"
            ? "bg-emerald-50 text-emerald-600"
            : "bg-neutral-100 text-neutral-400";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${cls}`}>
      {label}
    </span>
  );
}

function feedbackBadge(rating?: string) {
  if (rating === "up") {
    return <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-600">👍 Helpful</span>;
  }
  if (rating === "down") {
    return <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600">👎 Not helpful</span>;
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

  if (events.length === 0) {
    return (
      <div className="text-center py-16 text-neutral-400">
        <div className="text-3xl mb-3">💬</div>
        <p>No events yet.</p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-neutral-200 rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableHeader label="Time" field="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
            <SortableHeader label="Type" field="type" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
            <th className="text-left px-4 py-3 text-xs uppercase tracking-wider text-neutral-400 font-semibold border-b border-neutral-200">
              Detail
            </th>
            <SortableHeader label="Session" field="session_id" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((e, i) => {
            const ev = e as unknown as AuditEvent;
            const time = new Date(ev.timestamp).toLocaleTimeString("en-US", { timeZone: "America/New_York" });
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
                    <span className="ml-1 inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-600">
                      relaxed
                    </span>
                  )}
                </>
              );
            } else if (ev.type === "crisis_detected") {
              detail = (
                <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600">
                  {ev.crisis_category}
                </span>
              );
            } else if (ev.type === "session_reset") {
              detail = "Session cleared";
            } else if (ev.type === "feedback") {
              detail = (
                <span className="flex items-center gap-2">
                  {feedbackBadge(ev.rating)}
                  {ev.comment && (
                    <span className="text-neutral-500 max-w-[200px] truncate block">
                      &quot;{ev.comment}&quot;
                    </span>
                  )}
                </span>
              );
            }

            return (
              <tr key={i} className="hover:bg-neutral-50/50">
                <td className="px-4 py-2.5 font-mono text-xs border-b border-neutral-100">
                  {time}
                </td>
                <td className="px-4 py-2.5 border-b border-neutral-100">
                  {typeBadge(ev.type)}
                </td>
                <td className="px-4 py-2.5 border-b border-neutral-100">
                  {detail}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-neutral-400 border-b border-neutral-100" title={ev.session_id || ""}>
                  {ev.session_id ? `${ev.session_id.slice(0, 12)}…` : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
