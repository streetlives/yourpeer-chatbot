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
 *   complexity.
 * - 1-hour TTL on queued entries. After an hour, a queued "what food
 *   is near me?" is probably no longer the question the user wanted
 *   answered — they've moved, changed context, or given up. Rather
 *   than silently send a stale question, we expire it.
 * - Messages include coords captured AT QUEUE TIME, not at flush time.
 *   If the user typed "food near me" in Brooklyn at 10am, queued, and
 *   is flushed while walking in Queens at 11am, "near me" should mean
 *   where they were when they asked. Otherwise results look wrong.
 */

"use client";

import { get, set } from "idb-keyval";

const QUEUE_KEY = "yourpeer:send-queue:v1";

/** Queued messages expire after this long and are dropped on flush. */
export const QUEUE_TTL_MS = 60 * 60 * 1000; // 1 hour

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

/** Append a message to the queue. Idempotent on id collisions. */
export async function enqueue(msg: QueuedMessage): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    const current = await readQueue();
    // Dedupe by ID — prevents double-queueing if send() is called
    // twice for the same user action (e.g. retry while still queued).
    const deduped = current.filter((m) => m.id !== msg.id);
    deduped.push(msg);
    await set(QUEUE_KEY, deduped);
  } catch (err) {
    console.warn("[send-queue] enqueue failed:", err);
  }
}

/** Remove a message from the queue by id. Used after successful flush. */
export async function dequeue(id: string): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    const current = await readQueue();
    const filtered = current.filter((m) => m.id !== id);
    await set(QUEUE_KEY, filtered);
  } catch (err) {
    console.warn("[send-queue] dequeue failed:", err);
  }
}

/** Drop all queued messages. Used by "Start over" / session reset. */
export async function clearQueue(): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    await set(QUEUE_KEY, []);
  } catch (err) {
    console.warn("[send-queue] clear failed:", err);
  }
}

/**
 * Remove expired entries. Returns the list of entries that were
 * dropped (so the caller can notify the user which messages timed
 * out). Call this before flushing.
 */
export async function reapExpired(): Promise<QueuedMessage[]> {
  if (typeof window === "undefined") return [];

  try {
    const current = await readQueue();
    const now = Date.now();
    const fresh: QueuedMessage[] = [];
    const expired: QueuedMessage[] = [];
    for (const msg of current) {
      if (now - msg.queuedAt > QUEUE_TTL_MS) {
        expired.push(msg);
      } else {
        fresh.push(msg);
      }
    }
    if (expired.length > 0) {
      await set(QUEUE_KEY, fresh);
    }
    return expired;
  } catch (err) {
    console.warn("[send-queue] reap failed:", err);
    return [];
  }
}
