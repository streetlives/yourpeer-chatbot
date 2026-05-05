// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useMemo, useState } from "react";
import type { ConversationSummary, AuditEvent } from "@/lib/chat/types";
import { fetchConversationDetail } from "@/lib/chat/api";
import { useSortableTable } from "@/hooks/use-sortable-table";
import { SortableHeader } from "./sortable-header";
import { TranscriptDrawer } from "./transcript-drawer";

type OutcomeFilter = "all" | "results" | "no_results" | "crisis";

interface ConversationTableProps {
  conversations: ConversationSummary[];
}

export function ConversationTable({ conversations }: ConversationTableProps) {
  const [transcript, setTranscript] = useState<AuditEvent[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Filter state
  const [search, setSearch] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>("all");
  const [minTurns, setMinTurns] = useState<number>(0);

  // Apply filters before sorting so the result count reflects what's visible.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return conversations.filter((c) => {
      // Search: matches session_id prefix or any slot key/value containing the query
      if (q) {
        const inId = c.session_id.toLowerCase().includes(q);
        const inSlots = Object.entries(c.final_slots || {}).some(
          ([k, v]) =>
            k.toLowerCase().includes(q) ||
            (v != null && String(v).toLowerCase().includes(q)),
        );
        if (!inId && !inSlots) return false;
      }
      // Outcome filter
      if (outcomeFilter === "results" && c.services_delivered <= 0) return false;
      if (outcomeFilter === "no_results" && c.services_delivered > 0) return false;
      if (outcomeFilter === "crisis" && !c.crisis_detected) return false;
      // Min turns
      if (minTurns > 0 && c.turn_count < minTurns) return false;
      return true;
    });
  }, [conversations, search, outcomeFilter, minTurns]);

  const { sorted, sortKey, sortDir, onSort } = useSortableTable(
    filtered as unknown as Record<string, unknown>[],
    "last_seen",
    "desc",
  );

  async function openTranscript(sessionId: string) {
    setSelectedId(sessionId);
    setLoading(true);
    try {
      const events = await fetchConversationDetail(sessionId);
      setTranscript(events);
    } catch {
      setTranscript([]);
    } finally {
      setLoading(false);
    }
  }

  function closeTranscript() {
    setSelectedId(null);
    setTranscript(null);
  }

  function clearFilters() {
    setSearch("");
    setOutcomeFilter("all");
    setMinTurns(0);
  }

  const filtersActive =
    search.trim().length > 0 || outcomeFilter !== "all" || minTurns > 0;

  // Note: the "no conversations at all" empty state is handled by the
  // page-level <DataPanel> wrapper; this component renders the filter UI
  // with a "no matches" state when filters apply but no rows match.
  const outcomeButtons: Array<{ key: OutcomeFilter; label: string }> = [
    { key: "all", label: "All" },
    { key: "results", label: "Has results" },
    { key: "no_results", label: "No results" },
    { key: "crisis", label: "Crisis" },
  ];

  return (
    <>
      {/* Filter bar */}
      <div className="bg-white border border-neutral-200 rounded-lg p-3 mb-3 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search session ID or slot value (e.g. food, brooklyn, urgent)…"
          aria-label="Search conversations"
          className="flex-1 min-w-[240px] px-3 py-1.5 text-sm border border-neutral-200 rounded-lg bg-white text-neutral-700 placeholder:text-neutral-400 focus:outline-none focus:border-amber-400 focus:ring-1 focus:ring-amber-400"
        />

        <div className="flex items-center gap-1 border border-neutral-200 rounded-lg p-0.5 bg-neutral-50">
          {outcomeButtons.map((b) => (
            <button
              key={b.key}
              onClick={() => setOutcomeFilter(b.key)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition ${
                outcomeFilter === b.key
                  ? "bg-white text-neutral-900 shadow-sm"
                  : "text-neutral-500 hover:text-neutral-700"
              }`}
            >
              {b.label}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-1.5 text-xs text-neutral-500">
          Min turns
          <input
            type="number"
            min={0}
            value={minTurns || ""}
            onChange={(e) =>
              setMinTurns(Math.max(0, parseInt(e.target.value, 10) || 0))
            }
            placeholder="0"
            aria-label="Minimum turns filter"
            className="w-14 px-2 py-1 text-sm border border-neutral-200 rounded-md bg-white text-neutral-700 placeholder:text-neutral-300 focus:outline-none focus:border-amber-400"
          />
        </label>

        <div className="text-xs text-neutral-400 ml-auto">
          {filtered.length} of {conversations.length}
          {filtersActive && (
            <button
              onClick={clearFilters}
              className="ml-2 px-2 py-0.5 rounded text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700 transition"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="bg-white border border-neutral-200 rounded-lg py-10 text-center text-sm text-neutral-400">
          No conversations match these filters.
        </div>
      ) : (
        <div className="bg-white border border-neutral-200 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <SortableHeader label="Session" field="session_id" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader label="Turns" field="turn_count" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <SortableHeader label="Outcome" field="services_delivered" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                <th className="text-left px-4 py-3 text-xs uppercase tracking-wider text-neutral-400 font-semibold border-b border-neutral-200">
                  Slots
                </th>
                <SortableHeader label="Last Active" field="last_seen" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {(sorted as unknown as ConversationSummary[]).map((c) => {
                const slots =
                  Object.entries(c.final_slots || {})
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ") || "—";
                return (
                  <tr
                    key={c.session_id}
                    onClick={() => openTranscript(c.session_id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        openTranscript(c.session_id);
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`View transcript for session ${c.session_id.slice(0, 12)}`}
                    className="cursor-pointer hover:bg-amber-50/50 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-1"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs text-neutral-500 border-b border-neutral-100" title={c.session_id}>
                      {c.session_id.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-2.5 border-b border-neutral-100">
                      {c.turn_count}
                    </td>
                    <td className="px-4 py-2.5 border-b border-neutral-100 space-x-1">
                      {c.services_delivered > 0 ? (
                        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-600">
                          {c.services_delivered} results
                        </span>
                      ) : (
                        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-neutral-100 text-neutral-400">
                          no results
                        </span>
                      )}
                      {c.crisis_detected && (
                        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-red-50 text-red-600">
                          crisis
                        </span>
                      )}
                    </td>
                    <td
                      className="px-4 py-2.5 text-sm border-b border-neutral-100 max-w-[300px] truncate"
                      title={slots}
                    >
                      {slots}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-neutral-400 border-b border-neutral-100">
                      {new Date(c.last_seen).toLocaleString("en-US", { timeZone: "America/New_York" })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedId && (
        <TranscriptDrawer
          sessionId={selectedId}
          events={transcript}
          loading={loading}
          onClose={closeTranscript}
        />
      )}
    </>
  );
}
