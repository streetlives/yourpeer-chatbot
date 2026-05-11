// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { QueryLogEntry } from "@/lib/chat/types";

interface QueryDetailDrawerProps {
  query: QueryLogEntry;
  onClose: () => void;
}

export function QueryDetailDrawer({ query, onClose }: QueryDetailDrawerProps) {
  const params = Object.entries(query.params || {});

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-2xl max-w-[600px] w-[90%] max-h-[80vh] overflow-y-auto p-7 z-50 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex justify-between items-center mb-5">
            <Dialog.Title className="text-base font-semibold">
              Query: {query.template_name}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label="Close query detail"
                className="w-8 h-8 rounded-lg border border-neutral-200 bg-neutral-50 text-neutral-400 flex items-center justify-center transition hover:border-red-300 hover:text-red-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-500 dark:hover:border-red-800 dark:hover:text-red-400"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Visually-hidden description — Radix Dialog requires either
              aria-describedby or a Dialog.Description child. The dialog
              body is a status-pill row plus a parameter table; neither
              reads cleanly as prose, so an sr-only summary covers
              screen readers without changing the visible layout. */}
          <Dialog.Description className="sr-only">
            Query template {query.template_name} returned {query.result_count} result
            {query.result_count !== 1 ? "s" : ""} in {query.execution_ms} milliseconds
            {query.relaxed ? ", with relaxed proximity matching" : ""}
            {query.proximity_timeout ? ", with a proximity timeout" : ""}.
          </Dialog.Description>

          <div className="space-y-4">
            {/* Summary row */}
            <div className="flex gap-3 flex-wrap">
              <span className={`inline-block px-2.5 py-1 rounded-lg text-xs font-semibold ${
                query.result_count > 0
                  ? "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                  : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300"
              }`}>
                {query.result_count} result{query.result_count !== 1 ? "s" : ""}
              </span>
              {query.relaxed && (
                <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300">
                  Relaxed
                </span>
              )}
              {query.proximity_timeout && (
                <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">
                  Proximity timeout
                </span>
              )}
              <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
                {query.execution_ms}ms
              </span>
            </div>

            {/* Timestamp */}
            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Executed at
              </div>
              <div className="text-sm text-neutral-700 dark:text-neutral-200">
                {new Date(query.timestamp).toLocaleString("en-US", { timeZone: "America/New_York" })}
              </div>
            </div>

            {/* Session */}
            {query.session_id && (
              <div>
                <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                  Session
                </div>
                <div className="text-sm font-mono text-neutral-500 dark:text-neutral-400" title={query.session_id}>
                  {query.session_id.slice(0, 12)}…
                </div>
              </div>
            )}

            {/* Parameters */}
            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-2">
                Query Parameters
              </div>
              {params.length === 0 ? (
                <p className="text-sm text-neutral-400 dark:text-neutral-500 italic">No parameters</p>
              ) : (
                <div className="bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden">
                  {params.map(([key, value]) => (
                    <div
                      key={key}
                      className="flex justify-between px-3.5 py-2.5 border-b border-neutral-100 dark:border-neutral-800 last:border-0"
                    >
                      <span className="text-sm font-mono text-neutral-500 dark:text-neutral-400">{key}</span>
                      <span className="text-sm text-neutral-700 dark:text-neutral-200 text-right max-w-[300px] break-all">
                        {typeof value === "object" ? JSON.stringify(value) : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
