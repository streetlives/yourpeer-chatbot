// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useDataSlice } from "@/hooks/use-data-slice";
import { DataPanel } from "@/components/admin/data-panel";
import { QueryLogTable } from "@/components/admin/query-log-table";
import { TableSkeleton } from "@/components/admin/loading-skeleton";

export default function QueriesPage() {
  const slice = useDataSlice("queries");

  return (
    <DataPanel
      slice={slice}
      skeleton={
        <TableSkeleton
          rows={6}
          // Matches QueryLogTable: Time, Template, Params (wide truncated),
          // Results (small), Duration (small), Relaxed (small badge)
          widths={["w-20", "w-28", "w-44", "w-12", "w-14", "w-12"]}
        />
      }
      emptyState={
        <div className="text-center py-16 text-neutral-400">
          <div className="text-3xl mb-3">🔍</div>
          <p>No queries logged yet. Service searches will appear here.</p>
        </div>
      }
    >
      {(data) => <QueryLogTable queries={data} />}
    </DataPanel>
  );
}
