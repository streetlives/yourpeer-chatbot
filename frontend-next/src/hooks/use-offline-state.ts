// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Aggregate offline-awareness hook.
 *
 * Exposes a single state object combining:
 * - online status (from useOnlineStatus)
 * - send queue depth (from the IndexedDB queue)
 * - cache age (for the staleness banner)
 *
 * Components should prefer this over useOnlineStatus directly when
 * they need to know about queued messages or cached results.
 */

"use client";

import { useEffect, useState, useCallback } from "react";
import { useOnlineStatus } from "./use-online-status";
import { readQueue } from "@/lib/chat/send-queue";
import { readCachedResults, cacheAgeMs } from "@/lib/chat/offline-cache";

export interface OfflineState {
  /** Browser reports online. */
  isOnline: boolean;
  /** Number of messages waiting in the send queue. 0 when none. */
  queueDepth: number;
  /** Age of cached results in ms, or null if nothing cached. */
  cacheAge: number | null;
  /** True when cached results exist and browser is offline — the
   *  staleness banner should render in this state. */
  showStalenessBanner: boolean;
  /** Force a re-read of queue + cache (e.g., after an enqueue). */
  refresh: () => void;
}

export function useOfflineState(): OfflineState {
  const isOnline = useOnlineStatus();
  const [queueDepth, setQueueDepth] = useState(0);
  const [cacheAge, setCacheAge] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  // Re-read queue and cache on online status change, manual refresh,
  // and on a slow interval (for cache age display).
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const q = await readQueue();
      const cache = await readCachedResults();
      if (cancelled) return;
      setQueueDepth(q.length);
      setCacheAge(cacheAgeMs(cache));
    })();

    return () => {
      cancelled = true;
    };
  }, [isOnline, tick]);

  // Slow tick to keep the "cached N minutes ago" display fresh.
  // 30s granularity is fine — the banner is a hint, not a clock.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  return {
    isOnline,
    queueDepth,
    cacheAge,
    showStalenessBanner: !isOnline && cacheAge !== null,
    refresh,
  };
}
