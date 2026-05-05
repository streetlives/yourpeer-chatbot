// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useDataSlice } from "@/hooks/use-data-slice";
import { DataPanel } from "@/components/admin/data-panel";
import { ConversationTable } from "@/components/admin/conversation-table";
import { TableSkeleton } from "@/components/admin/loading-skeleton";

export default function ConversationsPage() {
  const slice = useDataSlice("conversations");

  return (
    <DataPanel
      slice={slice}
      skeleton={<TableSkeleton rows={6} cols={5} />}
      emptyState={
        <div className="text-center py-16 text-neutral-400">
          <div className="text-3xl mb-3">💬</div>
          <p>No conversations yet. Start chatting to see them here.</p>
        </div>
      }
    >
      {(data) => <ConversationTable conversations={data} />}
    </DataPanel>
  );
}
