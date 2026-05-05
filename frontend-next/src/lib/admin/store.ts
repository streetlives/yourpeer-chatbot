// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { create } from "zustand";
import {
  fetchAdminStats,
  fetchConversations as apiConversations,
  fetchQueries as apiQueries,
  fetchEvents as apiEvents,
  fetchEvalResults as apiEvalResults,
} from "@/lib/chat/api";
import type {
  AdminStats,
  ConversationSummary,
  QueryLogEntry,
  AuditEvent,
  EvalReport,
} from "@/lib/chat/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DataSlice<T> {
  data: T;
  loading: boolean;
  error: boolean;
  lastFetchedAt: number;
}

function emptySlice<T>(initial: T): DataSlice<T> {
  return { data: initial, loading: false, error: false, lastFetchedAt: 0 };
}

interface AdminStore {
  stats: DataSlice<AdminStats | null>;
  conversations: DataSlice<ConversationSummary[]>;
  queries: DataSlice<QueryLogEntry[]>;
  events: DataSlice<AuditEvent[]>;
  evalResults: DataSlice<EvalReport | null | undefined>;

  fetchStats: () => Promise<void>;
  fetchConversations: () => Promise<void>;
  fetchQueries: () => Promise<void>;
  fetchEvents: () => Promise<void>;
  fetchEvalResults: () => Promise<void>;

  /** Force all slices to re-fetch on next access. */
  invalidateAll: () => void;
  /** Force a single slice to re-fetch on next access. */
  invalidate: (key: SliceName) => void;
  /**
   * Reset a single slice back to its initial state (clears data + error,
   * marks stale). Used when data needs to be discarded entirely — e.g.
   * after starting a new eval run, the previous report should not be
   * shown alongside the in-progress status.
   */
  reset: (key: SliceName) => void;
}

type SliceName = "stats" | "conversations" | "queries" | "events" | "evalResults";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Data older than this is considered stale and will be re-fetched. */
const STALE_AFTER_MS = 30_000; // 30 seconds

/** Fixed limits — use the largest value any consumer needs. */
const CONVERSATIONS_LIMIT = 200;
const QUERIES_LIMIT = 500;
const EVENTS_LIMIT = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isStale(slice: DataSlice<unknown>): boolean {
  return Date.now() - slice.lastFetchedAt > STALE_AFTER_MS;
}

/**
 * Build a fetcher action for a given slice. Centralizes the staleness +
 * dedup + loading + error pattern so each slice's action is a one-liner.
 *
 * The fetcher:
 *   - returns early if a fetch is already in flight or the slice is fresh
 *   - sets loading=true, error=false at the start
 *   - on success, replaces data and resets loading/error/timestamp
 *   - on failure, leaves data intact (stale-while-error UX), sets error=true
 *
 * Leaving data intact on failure is deliberate: if we have a 5-minute-old
 * snapshot, that's more useful than blanking the dashboard. The error
 * flag drives a banner or retry UI; the data flag stays available to the
 * page that wants to render it anyway.
 */
function createFetcher<K extends SliceName>(
  key: K,
  apiFn: () => Promise<AdminStore[K]["data"]>,
  set: (partial: Partial<AdminStore>) => void,
  get: () => AdminStore,
): () => Promise<void> {
  return async () => {
    const slice = get()[key] as DataSlice<unknown>;
    if (slice.loading || !isStale(slice)) return;

    // We need to spread the current slice values when we update so we don't
    // overwrite siblings; the cast lets TS know the result is still a valid
    // DataSlice for this key.
    set({
      [key]: { ...slice, loading: true, error: false },
    } as Partial<AdminStore>);

    try {
      const data = await apiFn();
      set({
        [key]: { data, loading: false, error: false, lastFetchedAt: Date.now() },
      } as Partial<AdminStore>);
    } catch {
      const current = get()[key] as DataSlice<unknown>;
      set({
        [key]: { ...current, loading: false, error: true },
      } as Partial<AdminStore>);
    }
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAdminStore = create<AdminStore>((set, get) => ({
  stats: emptySlice<AdminStats | null>(null),
  conversations: emptySlice<ConversationSummary[]>([]),
  queries: emptySlice<QueryLogEntry[]>([]),
  events: emptySlice<AuditEvent[]>([]),
  evalResults: emptySlice<EvalReport | null | undefined>(undefined),

  fetchStats: createFetcher("stats", fetchAdminStats, set, get),
  fetchConversations: createFetcher(
    "conversations",
    () => apiConversations(CONVERSATIONS_LIMIT),
    set,
    get,
  ),
  fetchQueries: createFetcher(
    "queries",
    () => apiQueries(QUERIES_LIMIT),
    set,
    get,
  ),
  fetchEvents: createFetcher("events", () => apiEvents(EVENTS_LIMIT), set, get),
  fetchEvalResults: createFetcher("evalResults", apiEvalResults, set, get),

  invalidateAll: () => {
    // Iterate the canonical slice list so adding a new slice only
    // requires updating SLICE_NAMES (not this body too). Calling
    // invalidate(key) per slice would re-enter set() five times — the
    // single set() with a spread keeps the update atomic.
    set((s) => {
      const next: Partial<AdminStore> = {};
      for (const key of SLICE_NAMES) {
        // The cast is needed because TS doesn't narrow `next[key]` to
        // the matching DataSlice type via `[key in SliceName]`. The
        // runtime structure is sound: each key gets a DataSlice with
        // lastFetchedAt zeroed.
        (next as Record<SliceName, unknown>)[key] = {
          ...s[key],
          lastFetchedAt: 0,
        };
      }
      return next;
    });
  },

  invalidate: (key) => {
    set(
      (s) =>
        ({
          [key]: { ...s[key], lastFetchedAt: 0 },
        }) as Partial<AdminStore>,
    );
  },

  reset: (key) => {
    // Map of empty initial values per slice. Has to mirror the create()
    // call above; if a new slice is added with non-default initial state,
    // both places need updating. Encoded explicitly rather than reading
    // from a shared constant because the type per slice differs and a
    // single `INITIAL` map would lose type narrowing.
    const initial: { [K in SliceName]: AdminStore[K] } = {
      stats: emptySlice<AdminStats | null>(null),
      conversations: emptySlice<ConversationSummary[]>([]),
      queries: emptySlice<QueryLogEntry[]>([]),
      events: emptySlice<AuditEvent[]>([]),
      evalResults: emptySlice<EvalReport | null | undefined>(undefined),
    };
    set({ [key]: initial[key] } as Partial<AdminStore>);
  },
}));

// Canonical list of slice names. Used by invalidateAll. Adding a new slice
// means appending here; the type system catches missed updates because
// SliceName is a literal-union that has to stay in sync with the keys above.
const SLICE_NAMES: readonly SliceName[] = [
  "stats",
  "conversations",
  "queries",
  "events",
  "evalResults",
] as const;
