// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Redact PII from chat messages at the localStorage persist boundary.
 *
 * This module exists as a separate file (rather than living inside
 * store.ts) so the redaction helpers can be unit-tested without
 * pulling in the rest of the store's dependency graph (zustand,
 * idb-keyval, the offline-queue modules). The helpers themselves
 * are pure: they take a message in, return a message out, and have
 * no I/O.
 *
 * Two entry points:
 *
 * - `redactMessage(message)` operates on the in-memory `ChatMessage`
 *   shape and is called from the Zustand `partialize` function on
 *   every persist. Returns a new message with `text` (for user-role
 *   messages) and `retryMessage` (for any role) redacted.
 *
 * - `redactStoredMessage(record)` operates on a loosely-typed
 *   `Record<string, unknown>` and is called from the persist
 *   `migrate` function. Migrate sees historical schemas that may not
 *   match the current ChatMessage exactly, so the helper is
 *   defensive about field types: anything that isn't a string passes
 *   through unchanged.
 *
 * Why redact at the persist boundary, not at the call site:
 * Multiple code paths can put text into the messages array — direct
 * sends, retries, error messages constructed with retryMessage,
 * post-flush state updates from the SW reconcile path. Redacting at
 * one chokepoint guarantees no path can leak unredacted text to disk
 * regardless of how the text got into the in-memory store. Same
 * defense-in-depth pattern the offline queue uses.
 *
 * Bot messages (role === "bot") have their `text` left alone because
 * the server-side logging already operates on redacted user input
 * (see backend/app/services/chatbot/logging.py) — bot replies don't
 * echo user PII back. `retryMessage` IS redacted regardless of role
 * because that field is constructed on the client from the user's
 * original text and bypasses any server-side redaction.
 */

import type { ChatMessage } from "./types";
import { redactPII } from "./pii-redactor.ts";

/** Redact a typed ChatMessage. Returns a new object only when needed. */
export function redactMessage(message: ChatMessage): ChatMessage {
  // Avoid allocating new objects when no redaction is needed —
  // happens for the vast majority of messages (welcome message,
  // "yes please", "near me", short follow-ups, etc.).
  const needsTextRedaction = message.role === "user" && !!message.text;
  const needsRetryRedaction = !!message.retryMessage;
  if (!needsTextRedaction && !needsRetryRedaction) {
    return message;
  }
  const out: ChatMessage = { ...message };
  if (needsTextRedaction) {
    out.text = redactPII(message.text).redacted;
  }
  if (needsRetryRedaction) {
    out.retryMessage = redactPII(message.retryMessage!).redacted;
  }
  return out;
}

/**
 * Loose-typed equivalent of `redactMessage` for the persist migrate
 * function. Migrate() receives `Record<string, unknown>` because it
 * deals with arbitrary historical schemas — we can't assume the
 * incoming object matches the current ChatMessage shape exactly.
 *
 * Defensively narrows each field before calling redactPII. Anything
 * that doesn't look like a string passes through unchanged. This
 * keeps the migration safe against future schema drift: if a future
 * version adds a new role or text-bearing field, the migration won't
 * blow up on data it doesn't understand.
 */
export function redactStoredMessage(
  message: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...message };
  // Only redact text on user-role messages (mirrors redactMessage).
  if (out.role === "user" && typeof out.text === "string") {
    out.text = redactPII(out.text).redacted;
  }
  // retryMessage is redacted regardless of role — it's always a copy
  // of user-typed text, even when attached to a bot-role error
  // message.
  if (typeof out.retryMessage === "string") {
    out.retryMessage = redactPII(out.retryMessage).redacted;
  }
  return out;
}
