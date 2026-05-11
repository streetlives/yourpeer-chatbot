// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Non-React utilities for the `useChat` hook.
 *
 * Why this file exists:
 *   `src/hooks/use-chat.ts` was 1243 lines. The audit
 *   (`FRONTEND_AUDIT.md` 2026-05, P1 #4) recommended splitting the
 *   hook into smaller pieces. This module is the first extraction:
 *   the pure module-level helpers — error inspectors, retry/delay,
 *   network-failure detection, background-sync registration, cache
 *   write — that the hook used but that have no React surface and
 *   are independently testable.
 *
 *   Pulling them here removed ~190 lines from the hook file without
 *   any behavior change. The hook still imports and uses each
 *   function exactly as before.
 *
 * What is intentionally NOT here:
 *   - `flushInFlight` (the module-level flush coordinator) stays in
 *     `use-chat.ts` because its primary consumer is `flushQueue`,
 *     which currently lives there. If/when `flushQueue` is later
 *     extracted to its own file, the coordinator should move with
 *     it.
 *   - Anything that calls React hooks (`useCallback`, `useEffect`)
 *     or reads hook-scoped state. Those live in `src/hooks/`.
 */

import type { ChatMessage } from "@/lib/chat/types";
import { cacheLastResults } from "@/lib/chat/offline-cache";

/**
 * Sentinel values the frontend uses to trigger backend special
 * paths. Kept here (not as backend imports) because the strings are
 * a wire-protocol contract — they're matched by exact string on the
 * backend (`slot_extraction_regex._LOCATION_TRIGGERS`).
 */
export const GEOLOCATION_TRIGGER = "__use_geolocation__";
export const CRISIS_GEO_TRIGGER = "__crisis_geo_search__";

/** Wait ms milliseconds. */
export const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Try an async operation with one automatic retry after a delay.
 * Does NOT retry 429 (rate limit) or 403 (auth) errors.
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  retryDelayMs = 1500,
): Promise<T> {
  try {
    return await fn();
  } catch (err: unknown) {
    const msg = errMessage(err);
    // Don't retry rate limits or auth errors
    if (msg.includes("429") || msg.includes("403")) throw err;
    await delay(retryDelayMs);
    return fn();
  }
}

/** Safely extract a message string from an unknown thrown value. */
export function errMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && "message" in err) {
    const m = (err as { message?: unknown }).message;
    return typeof m === "string" ? m : "";
  }
  return "";
}

/** Safely extract an error name (e.g. "TypeError") from an unknown thrown value. */
export function errName(err: unknown): string {
  if (err instanceof Error) return err.name;
  if (err && typeof err === "object" && "name" in err) {
    const n = (err as { name?: unknown }).name;
    return typeof n === "string" ? n : "";
  }
  return "";
}

/** Convert a caught error into a user-friendly message. */
export function userFacingError(err: unknown): string {
  const msg = errMessage(err);
  const name = errName(err);
  // Rate-limit messages include "wait" — pass through verbatim
  if (msg.includes("wait")) return msg;
  // API layer errors (503, 500) already have good messages — pass through
  if (
    msg.includes("temporarily unavailable") ||
    msg.includes("on our end") ||
    msg.includes("Try again")
  )
    return msg;
  // Network error — fetch itself failed (no response)
  if (name === "TypeError" || msg.includes("fetch"))
    return "Can't reach the server right now. Check your connection and try again.";
  // Timeout — AbortSignal.timeout fired
  if (name === "TimeoutError" || name === "AbortError")
    return "The search is taking longer than expected. Try again in a moment.";
  // Fallback
  return "Sorry, something went wrong. Try again in a moment.";
}

/**
 * True when an error looks like a network failure (offline, DNS, etc.),
 * as opposed to a server-returned error (4xx/5xx) or a request timeout.
 * Used to decide whether to enqueue the message for later flush or
 * surface the error to the user immediately.
 *
 * Rate limits (429), auth (403), bad request (400), server errors
 * (5xx) are NOT network errors — they mean we reached the server and
 * it answered.
 *
 * Timeouts (AbortSignal.timeout firing) are also NOT treated as
 * network errors, even though they produce no response: a timeout
 * means the server was slow, not that the user was offline. Queuing
 * on timeout would show the user "I'll send this when you're back
 * online" while they're clearly online, which is confusing. Let those
 * flow to the normal error path where userFacingError() says "taking
 * longer than expected — try again."
 *
 * As a final guard we also check navigator.onLine. Even if fetch
 * raised TypeError, if the browser thinks it's online the user
 * probably sees a degraded state (e.g. captive portal, VPN issue)
 * better served by an error message than silent queuing.
 */
export function isNetworkError(err: unknown): boolean {
  // Browser is sure we're online → not a queue-worthy network failure
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    // Offline for sure — anything that failed is a network error
    return true;
  }
  const msg = errMessage(err);
  const name = errName(err);
  // Timeouts are slow-server, not no-network — don't queue
  if (name === "TimeoutError") return false;
  if (name === "AbortError") return false;
  if (name === "TypeError") return true; // fetch itself failed
  if (msg.includes("Failed to fetch")) return true;
  if (msg.includes("NetworkError")) return true;
  // HTTP status codes in the message mean we got a response
  if (/\b[45]\d\d\b/.test(msg)) return false;
  return false;
}

/**
 * Ask the browser to fire a Background Sync event when connectivity is
 * available. The sync tag here MUST match `SYNC_TAG` in public/sw.js —
 * the SW's sync handler only runs for the exact tag it's checking for.
 *
 * This is a strict enhancement on top of the existing client-side
 * flush. When it works (Chromium browsers with the API enabled), the
 * browser will drain the queue even if the tab is closed or JS is
 * frozen — useful for iOS Android Chrome in background or users who
 * close the tab between when they hit send and when connectivity
 * returns. When it doesn't work (Safari, Firefox, older Edge, or any
 * browser where the registration fails), the existing client-side
 * `online` handler still fires on tab focus and drains the queue.
 *
 * Failures are swallowed: this is a best-effort enhancement, never
 * the primary send path. We also check `navigator.onLine` before
 * registering — an immediate sync on an already-online browser would
 * fire right away, which is fine but racy with the client-side
 * flush. Skip it; the client-side path will handle it.
 */
export async function tryRegisterBackgroundSync(): Promise<void> {
  if (typeof window === "undefined") return;
  if (!("serviceWorker" in navigator)) return;
  // Feature detection — SyncManager is the global API for Background
  // Sync. Safari and Firefox omit this; registering on those browsers
  // throws, which is why we gate even though the call below is in a
  // try/catch.
  if (!("SyncManager" in window)) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    // The `sync` property is typed as optional in lib.dom — check
    // before using to satisfy strict mode. Present whenever
    // SyncManager is defined.
    const sync = (reg as ServiceWorkerRegistration & {
      sync?: { register(tag: string): Promise<void> };
    }).sync;
    if (!sync) return;
    await sync.register("yourpeer-send-queue");
  } catch (err) {
    // Common failure modes: user-denied storage permission, no SW
    // registered yet (would be odd this deep in the send path), or
    // the browser happens not to implement Background Sync despite
    // exposing SyncManager. None of these should break the user's
    // flow — the client-side online handler is the fallback.
    console.debug("[sync] registration failed (non-fatal):", err);
  }
}

/**
 * Write a bot response that includes service cards to the offline
 * cache. Fire-and-forget — caching failures never block the UI.
 *
 * Called from every success path (geo flow, crisis flow, normal
 * send, retries, queue flush). Kept as a standalone helper so if
 * any success path is added later, we don't forget to cache there
 * too.
 *
 * Guards:
 * - Requires at least one service in the response. Without services
 *   there's nothing useful to show offline.
 * - Requires no retryMessage. If the response also carries a retry
 *   prompt it's an error message, not results — don't cache.
 */
export function cacheIfResults(botMessage: ChatMessage, userQuery: string): void {
  if (!botMessage.services || botMessage.services.length === 0) return;
  if (botMessage.retryMessage) return;
  void cacheLastResults(botMessage, userQuery);
}
