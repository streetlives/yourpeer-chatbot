// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState, useMemo } from "react";
import type { QueryLogEntry } from "@/lib/chat/types";
import { useSortableTable } from "@/hooks/use-sortable-table";
import { SortableHeader } from "./sortable-header";
import { QueryDetailDrawer } from "./query-detail-drawer";
import { formatRelativeTime, formatAbsoluteTooltip } from "@/lib/admin/format-time";
import { TablePagination, DEFAULT_PAGE_SIZE, type PageSize } from "./table-pagination";

interface QueryLogTableProps {
  queries: QueryLogEntry[];
}

export function QueryLogTable({ queries }: QueryLogTableProps) {
  const [selected, setSelected] = useState<QueryLogEntry | null>(null);
  // Page state lives here, not in TablePagination — see the
  // TablePagination component header for why.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);

  // Decorate each row with a synthetic `has_issue` boolean for sorting.
  // The Issues column shows two badges (proximity timeout + relaxed
  // fallback), and previously the sortable header sorted only on
  // `relaxed`, missing rows that had a proximity timeout but no relax.
  // Adding a combined field lets the column sort on "any issue."
  // The original entries are preserved on each row so the JSX below
  // can still render the per-flag badges separately.
  const queriesWithDerived = useMemo(
    () =>
      queries.map((q) => ({
        ...q,
        has_issue: q.proximity_timeout || q.relaxed ? 1 : 0,
      })),
    [queries],
  );

  const { sorted, sortKey, sortDir, onSort } = useSortableTable(
    queriesWithDerived as unknown as Record<string, unknown>[],
    "timestamp",
    "desc",
  );

  // Render-time clamp: if the underlying data shrinks below the
  // current page's start (refresh returns fewer rows, page size
  // bumped up), compute the safe page during render rather than via
  // useEffect. Avoids the cascading-render warning from the
  // react-hooks/set-state-in-effect rule.
  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);

  // Slice for the current page. Sort happens upstream in the hook;
  // pagination is the last step before render.
  const pageRows = useMemo(() => {
    const start = (safePage - 1) * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, safePage, pageSize]);

  // Note: the empty state is handled by the page-level <DataPanel>
  // wrapper; this component assumes it has rows to render.

  return (
    <>
      <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
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
                <SortableHeader label="Issues" field="has_issue" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {(pageRows as unknown as QueryLogEntry[]).map((q) => {
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
                    <td
                      className="px-4 py-2.5 font-mono text-xs border-b border-neutral-100"
                      title={formatAbsoluteTooltip(q.timestamp)}
                    >
                      {formatRelativeTime(q.timestamp)}
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

        {/* Pagination footer — inside the same bordered container so the
            full table reads as one visual unit. The container's
            `overflow-hidden` keeps the rounded bottom corners clean
            against the footer's bg-neutral-50. */}
        <TablePagination
          totalItems={sorted.length}
          page={page}
          pageSize={pageSize}
          onPageChange={setPage}
          onPageSizeChange={(newSize) => {
            setPageSize(newSize);
            // Page size change resets to page 1: keeping the user on
            // page 6 when they bumped from 25 → 100 per page would
            // strand them well past the end of the now-shorter list.
            setPage(1);
          }}
          ariaLabel="Query log pagination"
        />
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
