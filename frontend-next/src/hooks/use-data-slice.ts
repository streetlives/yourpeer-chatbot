// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect } from "react";
import { useAdminStore } from "@/lib/admin/store";

/**
 * useDataSlice — convenience hook that wraps the standard pattern:
 *   1. Read the named slice from the admin store
 *   2. Trigger its fetcher on mount (the fetcher itself handles staleness
 *      and dedup, so calling on every mount is correct)
 *   3. Return the slice plus boolean convenience flags
 *
 * Replaces the per-page `useEffect(() => fetchX(), [fetchX])` boilerplate.
 *
 * Usage:
 *   const { data, loading, error, isInitial } = useDataSlice("conversations");
 *   if (error) return <ErrorState />;
 *   if (isInitial) return <Skeleton />;
 *   return <Table data={data} />;
 *
 * Or, more concisely with <DataPanel>:
 *   <DataPanel slice={useDataSlice("conversations")} skeleton={...}>
 *     {(data) => <Table data={data} />}
 *   </DataPanel>
 */

// Map of slice key -> fetcher action name. Keeping this explicit avoids
// magic-string indexing into the store and lets TypeScript narrow.
const FETCHER_BY_KEY = {
  stats: "fetchStats",
  conversations: "fetchConversations",
  queries: "fetchQueries",
  events: "fetchEvents",
  evalResults: "fetchEvalResults",
} as const;

export type SliceKey = keyof typeof FETCHER_BY_KEY;

// Return type for useDataSlice — narrowed by the slice key. We can't easily
// preserve the exact data type per key without conditional types, so we
// return the underlying store type and let consumers type-narrow at the
// call site (or via DataPanel's children render prop).

export interface UseDataSliceResult<T> {
  data: T;
  loading: boolean;
  error: boolean;
  /** True when the slice has never successfully loaded (no data yet, no error yet). */
  isInitial: boolean;
  /** True when the slice has data — useful for "show stale data while reloading" patterns. */
  hasData: boolean;
  /**
   * Epoch milliseconds of the last *successful* fetch. Zero before any
   * successful load. Exposed (rather than only summarized into `hasData`)
   * so consumers can render "data from 5m ago" affordances when a refresh
   * has failed but cached data is still on screen.
   */
  lastFetchedAt: number;
  /** Force a re-fetch. */
  refresh: () => void;
}

export function useDataSlice<K extends SliceKey>(
  key: K,
): UseDataSliceResult<ReturnType<typeof useAdminStore.getState>[K]["data"]> {
  // Subscribe to just this slice and the actions we need. Selecting narrowly
  // means re-renders only fire when this slice changes, not on every other
  // store update.
  const slice = useAdminStore((s) => s[key]);
  const fetcher = useAdminStore((s) => s[FETCHER_BY_KEY[key]]);
  const invalidate = useAdminStore((s) => s.invalidate);

  useEffect(() => {
    fetcher();
  }, [fetcher]);

  // hasData semantics: the slice has loaded successfully at least once.
  // We use lastFetchedAt > 0 as the signal — it's set on every successful
  // load and only on success.
  const hasData = slice.lastFetchedAt > 0;
  const isInitial = !hasData && !slice.error;

  return {
    data: slice.data,
    loading: slice.loading,
    error: slice.error,
    isInitial,
    hasData,
    lastFetchedAt: slice.lastFetchedAt,
    refresh: () => {
      invalidate(key);
      fetcher();
    },
  };
}
