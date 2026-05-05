// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import type { QueryLogEntry } from "@/lib/chat/types";
import { useSortableTable } from "@/hooks/use-sortable-table";
import { SortableHeader } from "./sortable-header";
import { QueryDetailDrawer } from "./query-detail-drawer";

interface QueryLogTableProps {
  queries: QueryLogEntry[];
}

export function QueryLogTable({ queries }: QueryLogTableProps) {
  const [selected, setSelected] = useState<QueryLogEntry | null>(null);
  const { sorted, sortKey, sortDir, onSort } = useSortableTable(
    queries as unknown as Record<string, unknown>[],
    "timestamp",
    "desc",
  );

  if (queries.length === 0) {
    return (
      <div className="text-center py-16 text-neutral-400">
        <div className="text-3xl mb-3">🔍</div>
        <p>No queries executed yet.</p>
      </div>
    );
  }

  return (
    <>
      <div className="bg-white border border-neutral-200 rounded-lg overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <SortableHeader label="Time" field="timestamp" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              <SortableHeader label="Template" field="template_name" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              <th className="text-left px-4 py-3 text-xs uppercase tracking-wider text-neutral-400 font-semibold border-b border-neutral-200">
                Params
              </th>
              <SortableHeader label="Results" field="result_count" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              <SortableHeader label="Duration" field="execution_ms" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              <SortableHeader label="Relaxed" field="relaxed" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {(sorted as unknown as QueryLogEntry[]).map((q) => {
              const params = Object.entries(q.params || {})
                .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                .join(", ");
              // Composite key — same reasoning as event-feed: a single
              // session can fire multiple queries at the same timestamp.
              const rowKey = `${q.timestamp}|${q.template_name}|${q.session_id ?? ""}`;
              return (
                <tr
                  key={rowKey}
                  onClick={() => setSelected(q)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(q);
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`View details for ${q.template_name} query`}
                  className="cursor-pointer hover:bg-amber-50/50 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-1"
                >
                  <td className="px-4 py-2.5 font-mono text-xs border-b border-neutral-100">
                    {new Date(q.timestamp).toLocaleTimeString("en-US", { timeZone: "America/New_York" })}
                  </td>
                  <td className="px-4 py-2.5 font-semibold border-b border-neutral-100">
                    {q.template_name}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-neutral-500 max-w-[250px] truncate border-b border-neutral-100" title={params}>
                    {params || "—"}
                  </td>
                  <td className="px-4 py-2.5 border-b border-neutral-100">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
                      q.result_count > 0 ? "bg-green-50 text-green-600" : "bg-red-50 text-red-600"
                    }`}>
                      {q.result_count}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-neutral-400 border-b border-neutral-100">
                    {q.execution_ms}ms
                  </td>
                  <td className="px-4 py-2.5 border-b border-neutral-100">
                    {q.proximity_timeout && (
                      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600 mr-1">
                        timeout
                      </span>
                    )}
                    {q.relaxed && (
                      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-600">
                        yes
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && (
        <QueryDetailDrawer
          query={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
