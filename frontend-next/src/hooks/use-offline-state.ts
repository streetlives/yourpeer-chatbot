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
 * State refreshes happen via event subscription, not polling — the
 * underlying send-queue and offline-cache modules dispatch custom
 * events on every mutation, and this hook listens. A slow interval
 * (30s) also ticks the cache age so "5 minutes ago" stays accurate.
 *
 * Components should prefer this over useOnlineStatus directly when
 * they need to know about queued messages or cached results.
 */

"use client";

import { useEffect, useState, useCallback } from "react";
import { useOnlineStatus } from "./use-online-status";
import { readQueue, QUEUE_CHANGE_EVENT } from "@/lib/chat/send-queue";
import {
  readCachedResults,
  cacheAgeMs,
  CACHE_CHANGE_EVENT,
} from "@/lib/chat/offline-cache";

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
  /** Force a re-read of queue + cache. Normally unnecessary — events
   *  handle this automatically — but exposed for edge cases. */
  refresh: () => void;
}

export function useOfflineState(): OfflineState {
  const isOnline = useOnlineStatus();
  const [queueDepth, setQueueDepth] = useState(0);
  const [cacheAge, setCacheAge] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  // Re-read queue and cache on:
  // - online status change (we want a fresh read when connection returns)
  // - manual refresh() call (escape hatch)
  // - queue/cache change events (primary driver — fires immediately
  //   after enqueue, dequeue, cache write, etc.)
  // - slow interval (keeps "N minutes ago" display accurate)
  useEffect(() => {
    let cancelled = false;

    const readState = async () => {
      const q = await readQueue();
      const cache = await readCachedResults();
      if (cancelled) return;
      setQueueDepth(q.length);
      setCacheAge(cacheAgeMs(cache));
    };

    void readState();

    // Subscribe to change events so UI reflects mutations immediately.
    const onChange = () => {
      void readState();
    };
    window.addEventListener(QUEUE_CHANGE_EVENT, onChange);
    window.addEventListener(CACHE_CHANGE_EVENT, onChange);

    return () => {
      cancelled = true;
      window.removeEventListener(QUEUE_CHANGE_EVENT, onChange);
      window.removeEventListener(CACHE_CHANGE_EVENT, onChange);
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
