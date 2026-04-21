// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Offline cache for the most-recent chat results.
 *
 * This module is the *authoritative* store for data the user needs
 * when offline — specifically: the last bot response that returned
 * service cards. When a new results message arrives, write it here;
 * when the app boots offline (or connection drops), read it back and
 * display with a staleness banner.
 *
 * Design notes:
 * - Only the LAST results message is cached. Older ones fall off.
 *   This matches the target scenario: "I just found a shelter, lost
 *   WiFi, need the address." Full history offline is out of scope.
 * - IndexedDB not localStorage so the SW can also read it later if
 *   we add SW-side responses. localStorage is also sync and not
 *   accessible from workers.
 * - All reads are safe to call during SSR (they return null on the
 *   server because idb-keyval throws without IndexedDB).
 */

"use client";

import { get, set, del } from "idb-keyval";
import type { ChatMessage } from "./types";

const CACHE_KEY = "yourpeer:last-results:v1";

/** Results older than this are not served — user sees empty cached state. */
export const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

interface CachedResults {
  /** The bot message that contained the service cards. */
  message: ChatMessage;
  /** Unix ms when this was cached. Used for TTL + staleness display. */
  cachedAt: number;
  /** The user query text that produced these results. Shown to the
   *  user ("Results for: food in Brooklyn") so they know what they're
   *  looking at when returning offline. May be empty for quick-reply
   *  or geo flows — caller is responsible for picking a sensible value. */
  queryText: string;
}

/**
 * Store the most-recent results message.
 *
 * Writes are fire-and-forget: any IndexedDB failure is logged and
 * swallowed. The online experience must never break because caching
 * failed.
 */
export async function cacheLastResults(
  message: ChatMessage,
  queryText: string,
): Promise<void> {
  if (typeof window === "undefined") return;
  if (!message.services || message.services.length === 0) return;

  try {
    const entry: CachedResults = {
      message,
      cachedAt: Date.now(),
      queryText,
    };
    await set(CACHE_KEY, entry);
  } catch (err) {
    // IndexedDB can fail in private browsing, low disk, etc.
    // Log and continue — don't break the online flow.
    console.warn("[offline-cache] write failed:", err);
  }
}

/**
 * Read the cached results if still within TTL.
 *
 * Returns null if: no cache exists, cache is stale, or IndexedDB fails.
 * Never throws.
 */
export async function readCachedResults(): Promise<CachedResults | null> {
  if (typeof window === "undefined") return null;

  try {
    const entry = (await get(CACHE_KEY)) as CachedResults | undefined;
    if (!entry) return null;

    const age = Date.now() - entry.cachedAt;
    if (age > CACHE_TTL_MS) {
      // Stale — drop it so we don't keep returning dead data.
      await del(CACHE_KEY).catch(() => {});
      return null;
    }

    return entry;
  } catch (err) {
    console.warn("[offline-cache] read failed:", err);
    return null;
  }
}

/** Clear the cache — used by "Start over" / session reset flows. */
export async function clearCachedResults(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    await del(CACHE_KEY);
  } catch (err) {
    console.warn("[offline-cache] clear failed:", err);
  }
}

/**
 * How old the cached results are, in milliseconds.
 * Returns null if nothing cached. Used for the staleness banner.
 */
export function cacheAgeMs(entry: CachedResults | null): number | null {
  if (!entry) return null;
  return Date.now() - entry.cachedAt;
}

/**
 * Human-readable age for the banner — "just now", "5 minutes ago", etc.
 * Intentionally coarse; this isn't a precise timestamp, it's a hint
 * to the user that the data might be outdated.
 */
export function formatCacheAge(ms: number | null): string {
  if (ms === null) return "";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins === 1) return "1 minute ago";
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.floor(mins / 60);
  if (hours === 1) return "1 hour ago";
  return `${hours} hours ago`;
}
