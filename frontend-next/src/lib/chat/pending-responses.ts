// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Pending responses — server replies received by the service worker
 * (via Background Sync) while the client tab wasn't running or was
 * frozen. Stored in IndexedDB until the client next mounts, at which
 * point they're reconciled into the chat log and cleared.
 *
 * Why this exists: the client-side send queue drains in response to
 * the `online` event handler in use-chat. That handler only runs when
 * the tab is alive with JS unfrozen. On mobile especially (iOS Safari,
 * Android Chrome in the background), tabs get frozen or discarded
 * after a few minutes — the online event fires but JS never runs to
 * handle it.
 *
 * Background Sync fixes this by waking the SW when connectivity
 * returns, even if no tab is open. The SW drains the queue and stores
 * responses here. Next time the user opens the app, the client picks
 * them up.
 *
 * Shape mirrors send-queue.ts on purpose — same IDB backend, same
 * atomicity via idb-keyval's update(), same event-based change
 * notifications. Keep those patterns aligned so anyone familiar with
 * one understands the other.
 *
 * TTL: same 1 hour as the send queue. A response to a request the
 * user asked 2 hours ago is no longer relevant — show the current
 * reality instead. Reap on mount before reconcile.
 *
 * Cap: 50 entries, same reasoning as send-queue. Realistically this
 * store has at most a handful of entries; the cap is a guard against
 * pathological loops (buggy SW storing to a dead session repeatedly).
 */

"use client";

import { get, set, update } from "idb-keyval";

const PENDING_KEY = "yourpeer:pending-responses:v1";

/**
 * Timestamp of the most recent client-side resetChat. Stored in the
 * same IndexedDB keyval database as the queue + pending stores so the
 * service worker can read it during a Background Sync drain.
 *
 * Reset epoch protects against a narrow but real race: the SW reads
 * the send queue, starts draining, the user taps "Start over" which
 * fires clearPending() and clearQueue(), the SW finishes its in-flight
 * POST and writes a response into pending-responses for what is now a
 * stale session. On next mount the client would read that response,
 * see currentSessionId === null (because reset cleared it), match the
 * "fresh client adopts any session" branch, and inject the orphaned
 * bot reply into a chat the user thought they had wiped.
 *
 * The fix: stamp Date.now() into RESET_EPOCH_KEY whenever resetChat
 * runs. reconcilePending drops any pending entry whose receivedAt is
 * older than the stored epoch — those entries belong to a session the
 * user has since walked away from.
 */
export const RESET_EPOCH_KEY = "yourpeer:reset-epoch:v1";

/**
 * Persist the timestamp of a client-side session reset so that any
 * SW-delivered response received before this moment is treated as
 * stale on next mount. Caller is expected to invoke this BEFORE
 * (or in the same microtask as) clearPending() — see store.ts.
 */
export async function markResetEpoch(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    await set(RESET_EPOCH_KEY, Date.now());
  } catch (err) {
    // Non-fatal — worst case, a stale SW response could surface on
    // next mount. The clearPending() that follows handles the
    // common case (SW already finished writing); reset epoch only
    // covers the narrow window where the SW writes AFTER reset.
    console.warn("[pending-responses] markResetEpoch failed:", err);
  }
}

async function readResetEpoch(): Promise<number> {
  if (typeof window === "undefined") return 0;
  try {
    const v = await get(RESET_EPOCH_KEY);
    return typeof v === "number" ? v : 0;
  } catch (err) {
    console.warn("[pending-responses] readResetEpoch failed:", err);
    return 0;
  }
}

/** Entries older than this are dropped on reap. Matches QUEUE_TTL_MS
 *  so a send-queue entry and its eventual response share the same
 *  expiry horizon. */
export const PENDING_TTL_MS = 60 * 60 * 1000; // 1 hour

/** Hard cap on stored entries. Prevents buggy-SW loops from filling
 *  IDB. */
export const MAX_PENDING_DEPTH = 50;

/** Dispatched on window when the pending-responses store mutates.
 *  Mirrors send-queue's QUEUE_CHANGE_EVENT. UI hooks listen to refresh
 *  immediately without polling. */
export const PENDING_CHANGE_EVENT = "yourpeer:pending-responses-change";

function notifyChange(): void {
  if (typeof window === "undefined") return;
  queueMicrotask(() => {
    window.dispatchEvent(new CustomEvent(PENDING_CHANGE_EVENT));
  });
}

/**
 * A response the SW successfully fetched while the client was offline
 * or backgrounded. The SW writes these; the client reads and clears
 * them on next mount.
 *
 * Shape is intentionally narrow — just the fields the client needs
 * to update its UI. Full server response JSON is kept under `body`
 * for feature flexibility (e.g. quick_replies, service cards).
 */
export interface PendingResponse {
  /** Message ID — matches the original QueuedMessage.id. Used to
   *  flip the user message from "sending" to "sent" and anchor the
   *  assistant reply in the right place in the transcript. */
  id: string;
  /** Session ID returned by the server. May be new (if the original
   *  queued message had no session) or the same as what the client
   *  last saw. Client reconciles: if this differs from current
   *  session, the response was for a stale session and is discarded. */
  sessionId: string | null;
  /** Raw server response JSON. Shape is whatever /api/chat returns:
   *  { response, slots, services, quick_replies, follow_up_needed,
   *    relaxed_search, ... }. Typed as unknown to keep this module
   *  framework-agnostic; callers (use-chat) cast after validation. */
  body: unknown;
  /** Unix ms when the SW received the response. Used for TTL. */
  receivedAt: number;
  /** Unix ms when the user originally queued the underlying message
   *  (i.e. when they typed and hit send). Compared against the reset
   *  epoch to detect entries whose USER-INTENT is from before a since-
   *  reset session — receivedAt alone isn't enough because the SW can
   *  legitimately complete a POST AFTER the user resets, and the
   *  resulting response would carry a post-reset receivedAt while
   *  belonging to pre-reset typing. Optional only for backward compat
   *  with entries written by SWs that predate this field. */
  queuedAt?: number;
}

/** Read all pending responses. Returns [] on error or empty store. */
export async function readPending(): Promise<PendingResponse[]> {
  if (typeof window === "undefined") return [];
  try {
    const p = (await get(PENDING_KEY)) as PendingResponse[] | undefined;
    return Array.isArray(p) ? p : [];
  } catch (err) {
    console.warn("[pending-responses] read failed:", err);
    return [];
  }
}

/**
 * Pure classification of pending-response entries against the current
 * session and reset epoch. Extracted from `reconcilePending` so it can
 * be exercised by verify-pending-responses.mjs without an IDB stub.
 *
 * Returns the entries to hand back to the caller (`matched`) and the
 * entries that should remain in IDB (`remaining`, which is always
 * empty in the current logic — every entry is classified into matched,
 * dropped, or expired). The split is kept so future logic can add an
 * "unsure, defer" case without changing the signature.
 *
 * Inputs that aren't objects are tolerated as `[]` to match the
 * runtime guard that wraps this in the IDB update().
 */
export function classifyPending(
  entries: PendingResponse[] | undefined,
  currentSessionId: string | null,
  resetEpoch: number,
  now: number,
): { matched: PendingResponse[]; remaining: PendingResponse[] } {
  const current = Array.isArray(entries) ? entries : [];
  const matched: PendingResponse[] = [];
  const remaining: PendingResponse[] = [];

  for (const p of current) {
    // Expired — drop. Client won't get value from a stale response.
    if (now - p.receivedAt > PENDING_TTL_MS) continue;

    // Pre-reset — this entry's underlying message was QUEUED by the
    // user before their most recent "Start over". Drop it.
    //
    // Compare against queuedAt (user-typing time), NOT receivedAt
    // (SW-response time). The SW can legitimately complete a POST
    // after the user resets — that response would carry a post-reset
    // receivedAt but belongs to pre-reset typing. Filtering on
    // queuedAt is the only way to catch this case, which is the
    // central race that motivated the reset-epoch mechanism.
    //
    // Backward compat: entries written by SWs that predate the
    // queuedAt field have it as undefined. They fall through to the
    // session-match below where the currentSessionId === null branch
    // is conservative about adopting them (see below).
    if (
      resetEpoch > 0 &&
      typeof p.queuedAt === "number" &&
      p.queuedAt < resetEpoch
    ) {
      continue;
    }

    // Matches current session — accept.
    if (p.sessionId === currentSessionId) {
      matched.push(p);
      continue;
    }

    // Client has no session yet (fresh start, or after reset before
    // the next user send). The pre-reset filter above has vetted
    // entries with a queuedAt field as post-reset. Legacy entries
    // with no queuedAt can't be vetted that way — they were written
    // by an older SW that predates the field, and could be from
    // before a since-reset session. Drop those conservatively rather
    // than adopt them. Within an hour of upgrade all legacy entries
    // TTL out and this branch becomes a no-op.
    if (currentSessionId === null) {
      if (typeof p.queuedAt !== "number") continue;
      matched.push(p);
      continue;
    }

    // Stale session — drop. User has moved on.
  }

  return { matched, remaining };
}

/**
 * Drain and return all entries whose session matches `currentSessionId`
 * (or that have no session AND were queued after the last reset),
 * while dropping any entries for a different session, older than
 * PENDING_TTL_MS, or queued before the most recent client reset.
 *
 * See `classifyPending` for the per-entry classification logic. This
 * function is just the IDB I/O wrapper around it.
 *
 * Atomicity: read-filter-write happens inside one idb-keyval update()
 * transaction, so a concurrent SW write can't leak an entry past
 * reconcile. Same pattern as send-queue's reapExpired().
 */
export async function reconcilePending(
  currentSessionId: string | null,
): Promise<PendingResponse[]> {
  if (typeof window === "undefined") return [];

  // Read the reset epoch outside the update transaction. There's a
  // theoretical race here — if the user fires resetChat between this
  // read and the update transaction starting, an entry whose
  // queuedAt falls between the two epoch values would slip through
  // the pre-reset filter. In practice the race is self-healing:
  // resetChat synchronously wipes `messages` to a fresh welcome state,
  // so any entry we leak through reconcile is immediately overwritten
  // when the reset's setState lands. The race window is also small
  // (microseconds) and requires the user to tap "Start over" exactly
  // during the mount-time reconcile pass. Trading a tighter
  // single-transaction read for the complexity of a multi-key IDB
  // transaction isn't worth it.
  const resetEpoch = await readResetEpoch();

  let matched: PendingResponse[] = [];
  let mutated = false;

  try {
    await update<PendingResponse[] | undefined>(PENDING_KEY, (oldValue) => {
      const result = classifyPending(oldValue, currentSessionId, resetEpoch, Date.now());
      matched = result.matched;
      // Anything not in `remaining` was either matched (handed to
      // caller) or dropped (stale/expired/pre-reset). Either way the
      // store mutates if the input had any of those.
      const inputLen = Array.isArray(oldValue) ? oldValue.length : 0;
      if (inputLen !== result.remaining.length) mutated = true;
      return result.remaining;
    });
    if (mutated) notifyChange();
    return matched;
  } catch (err) {
    console.warn("[pending-responses] reconcile failed:", err);
    return [];
  }
}

/** Drop all pending responses. Called on session reset alongside
 *  clearQueue(). */
export async function clearPending(): Promise<void> {
  if (typeof window === "undefined") return;

  let mutated = false;
  try {
    await update<PendingResponse[] | undefined>(PENDING_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      if (current.length === 0) return current;
      mutated = true;
      return [];
    });
    if (mutated) notifyChange();
  } catch (err) {
    console.warn("[pending-responses] clear failed:", err);
  }
}
