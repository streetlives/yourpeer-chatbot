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

import { get, update } from "idb-keyval";

const PENDING_KEY = "yourpeer:pending-responses:v1";

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
 * Drain and return all entries whose session matches `currentSessionId`
 * (or that have no session), while dropping any entries for a different
 * session or older than PENDING_TTL_MS.
 *
 * Session matching rationale: if the user reset their session while
 * offline, the SW might still complete a POST for the OLD session.
 * Returning that response and injecting it into the new session's
 * chat log would be wrong. Entries with no session (server assigned
 * one fresh) are always returned to the caller, who's expected to
 * adopt the new session ID.
 *
 * Atomicity: read-filter-write happens inside one idb-keyval update()
 * transaction, so a concurrent SW write can't leak an entry past
 * reconcile. Same pattern as send-queue's reapExpired().
 */
export async function reconcilePending(
  currentSessionId: string | null,
): Promise<PendingResponse[]> {
  if (typeof window === "undefined") return [];

  const matched: PendingResponse[] = [];
  let mutated = false;

  try {
    await update<PendingResponse[] | undefined>(PENDING_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      const now = Date.now();
      const remaining: PendingResponse[] = [];
      for (const p of current) {
        // Expired — drop (client won't get value from a stale response)
        if (now - p.receivedAt > PENDING_TTL_MS) continue;
        // Matches current session, or the client has no session yet
        // and this entry has one (which it's expected to adopt).
        if (
          p.sessionId === currentSessionId ||
          currentSessionId === null ||
          p.sessionId === null
        ) {
          matched.push(p);
          continue;
        }
        // Stale session — drop. User has moved on.
        mutated = true;
      }
      // Always remove matched entries; they've been handed to caller.
      if (matched.length > 0) mutated = true;
      // Return only the entries that were neither matched nor stale.
      // In practice `remaining` is always empty because everything is
      // either matched, stale, or expired — kept as a defensive bucket
      // in case future logic adds an "unsure, skip for now" case.
      return remaining;
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
