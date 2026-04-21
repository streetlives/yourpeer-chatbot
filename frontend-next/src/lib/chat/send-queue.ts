// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Send queue for messages the user types while offline.
 *
 * When the user hits send offline (or the request fails with a network
 * error), the message goes in this queue. When `online` fires, the
 * chat hook flushes the queue one-at-a-time, replaying each message
 * against the live API.
 *
 * Design notes:
 * - Queue is stored as a single IndexedDB value (array) under one key.
 *   We always read/write the whole thing. Queue depth is small
 *   (typically 0-3 entries), so this is fine and avoids IDB cursor
 *   complexity. Mutations go through idb-keyval's `update()` which
 *   runs the read-modify-write inside one IDB transaction — so a
 *   concurrent enqueue() and reapExpired() can't clobber each other
 *   (previously last-write-wins; now properly serialized).
 * - 1-hour TTL on queued entries. After an hour, a queued "what food
 *   is near me?" is probably no longer the question the user wanted
 *   answered — they've moved, changed context, or given up. Rather
 *   than silently send a stale question, we expire it.
 * - Messages include coords captured AT QUEUE TIME, not at flush time.
 *   If the user typed "food near me" in Brooklyn at 10am, queued, and
 *   is flushed while walking in Queens at 11am, "near me" should mean
 *   where they were when they asked. Otherwise results look wrong.
 * - Queue is capped at MAX_QUEUE_DEPTH entries to protect against
 *   pathological cases (user spams send while offline for 59 minutes).
 *   New sends past the cap are silently rejected and the caller is
 *   told via the enqueue return value.
 * - Subscribe to QUEUE_CHANGE_EVENT to know when the queue mutates,
 *   so UI state can refresh without polling.
 */

"use client";

import { get, update } from "idb-keyval";

const QUEUE_KEY = "yourpeer:send-queue:v1";

/** Queued messages expire after this long and are dropped on flush. */
export const QUEUE_TTL_MS = 60 * 60 * 1000; // 1 hour

/** Maximum number of messages the queue will accept. Past this, new
 *  sends are rejected so the queue can't grow without bound during
 *  a long offline spell with rapid typing. */
export const MAX_QUEUE_DEPTH = 50;

/** Event dispatched on window when the queue is mutated. UI hooks
 *  listen to this to refresh their state immediately rather than
 *  waiting on a polling interval. */
export const QUEUE_CHANGE_EVENT = "yourpeer:queue-change";

function notifyChange(): void {
  if (typeof window === "undefined") return;
  // Use a microtask to batch multiple mutations in the same tick.
  queueMicrotask(() => {
    window.dispatchEvent(new CustomEvent(QUEUE_CHANGE_EVENT));
  });
}

export interface QueuedMessage {
  /** Unique ID — matches the chat message ID in the UI so we can
   *  update it (e.g. pending → sent checkmark) when flush completes. */
  id: string;
  /** The text the user typed (or the quick-reply value). */
  text: string;
  /** Coords captured at send time, if geolocation was available. */
  coords: { latitude: number; longitude: number } | null;
  /** Session ID at send time. May be expired when we flush —
   *  the chat hook handles that case by probing the session first. */
  sessionId: string | null;
  /** Unix ms when the user hit send (not when flushed). */
  queuedAt: number;
  /**
   * Stable idempotency key sent to the server as X-Request-ID. Reused
   * on every retry/flush of this message so the server can dedupe
   * successful responses that the client never saw (e.g. network flap
   * during response). Optional for backward compatibility with
   * already-queued entries from older builds — flush falls back to a
   * fresh UUID when missing.
   */
  requestId?: string;
}

/** Read the full queue. Returns [] on error or when nothing queued. */
export async function readQueue(): Promise<QueuedMessage[]> {
  if (typeof window === "undefined") return [];

  try {
    const q = (await get(QUEUE_KEY)) as QueuedMessage[] | undefined;
    return Array.isArray(q) ? q : [];
  } catch (err) {
    console.warn("[send-queue] read failed:", err);
    return [];
  }
}

/**
 * Append a message to the queue. Idempotent on id collisions.
 *
 * Returns { accepted: true } normally, or { accepted: false, reason }
 * when the queue is full. Callers should surface the rejection to
 * the user rather than silently swallowing it.
 *
 * Atomicity: the read-and-write happens inside a single idb-keyval
 * `update()` transaction, so a concurrent reapExpired() can't drop
 * this message after we've written it. The cap check and dedupe
 * happen inside the same transaction too — no window for a stale
 * read of queue length.
 *
 * The result is read out via closure (a mutable `result` variable
 * set inside the updater) because `update()` returns `Promise<void>`
 * by design. Safe because the updater runs synchronously within the
 * IDB transaction.
 */
export async function enqueue(
  msg: QueuedMessage,
): Promise<{ accepted: true } | { accepted: false; reason: "queue_full" }> {
  if (typeof window === "undefined") return { accepted: true };

  let result: { accepted: true } | { accepted: false; reason: "queue_full" } = {
    accepted: true,
  };
  let mutated = false;

  try {
    await update<QueuedMessage[] | undefined>(QUEUE_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      const existing = current.find((m) => m.id === msg.id);
      if (!existing && current.length >= MAX_QUEUE_DEPTH) {
        result = { accepted: false, reason: "queue_full" };
        // Return unchanged queue — no write actually happens at the
        // logical level, but the transaction still commits the
        // (identical) value. Cheap.
        return current;
      }
      // Dedupe by ID — prevents double-queueing if send() is called
      // twice for the same user action (e.g. retry while still queued).
      const deduped = current.filter((m) => m.id !== msg.id);
      deduped.push(msg);
      mutated = true;
      return deduped;
    });
    if (mutated) notifyChange();
    return result;
  } catch (err) {
    console.warn("[send-queue] enqueue failed:", err);
    // IDB write failure is itself a form of "can't accept" — treat
    // as full so the caller shows an error to the user rather than
    // silently losing the message.
    return { accepted: false, reason: "queue_full" };
  }
}

/** Remove a message from the queue by id. Used after successful flush.
 *  Atomic — reads and writes inside a single IDB transaction. */
export async function dequeue(id: string): Promise<void> {
  if (typeof window === "undefined") return;

  let mutated = false;
  try {
    await update<QueuedMessage[] | undefined>(QUEUE_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      const filtered = current.filter((m) => m.id !== id);
      if (filtered.length !== current.length) mutated = true;
      return filtered;
    });
    if (mutated) notifyChange();
  } catch (err) {
    console.warn("[send-queue] dequeue failed:", err);
  }
}

/** Drop all queued messages. Used by "Start over" / session reset.
 *  Atomic write; safe under concurrent enqueue (queue becomes empty
 *  regardless of what raced in). */
export async function clearQueue(): Promise<void> {
  if (typeof window === "undefined") return;

  let mutated = false;
  try {
    await update<QueuedMessage[] | undefined>(QUEUE_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      if (current.length === 0) return current;
      mutated = true;
      return [];
    });
    if (mutated) notifyChange();
  } catch (err) {
    console.warn("[send-queue] clear failed:", err);
  }
}

/**
 * Remove expired entries. Returns the list of entries that were
 * dropped (so the caller can notify the user which messages timed
 * out). Call this before flushing.
 *
 * Atomic: the split happens inside a single `update()` transaction,
 * so a concurrent enqueue()'s new message can't be dropped as a
 * side-effect of this reap. The expired list is captured via closure
 * because `update()` returns Promise<void> — same pattern as enqueue().
 */
export async function reapExpired(): Promise<QueuedMessage[]> {
  if (typeof window === "undefined") return [];

  const expired: QueuedMessage[] = [];
  let mutated = false;

  try {
    await update<QueuedMessage[] | undefined>(QUEUE_KEY, (oldValue) => {
      const current = Array.isArray(oldValue) ? oldValue : [];
      const now = Date.now();
      const fresh: QueuedMessage[] = [];
      for (const msg of current) {
        if (now - msg.queuedAt > QUEUE_TTL_MS) {
          expired.push(msg);
        } else {
          fresh.push(msg);
        }
      }
      if (expired.length > 0) {
        mutated = true;
        return fresh;
      }
      // Nothing expired — return the input unchanged so the transaction
      // commits a no-op write. Simpler than conditionally skipping the
      // write, and cheap.
      return current;
    });
    if (mutated) notifyChange();
    return expired;
  } catch (err) {
    console.warn("[send-queue] reap failed:", err);
    return [];
  }
}
