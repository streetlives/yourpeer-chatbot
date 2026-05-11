// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Pure helpers for building the "restored earlier results" message —
 * the single bot turn injected into the chat stream when the user
 * taps "See earlier results" after a session reset.
 *
 * Kept in its own module (not inlined in store.ts) so the verify
 * script can import the helper without dragging zustand, IndexedDB-
 * backed send queue, and service-worker pending-response plumbing
 * through Node's ESM resolver. The store action remains a thin
 * caller of `buildRestoredResultsMessage`.
 *
 * Related:
 *   - src/lib/chat/store.ts          (caller — `restoreEarlierResults`)
 *   - src/components/chat/earlier-results-link.tsx (UI affordance)
 *   - scripts/verify/restore-earlier-results.mjs   (verification)
 *   - backend/app/services/chatbot/handlers/post_results.py
 *       ("Here are all the results again" — analogous backend path
 *        whose standard QR set this module mirrors)
 */

import type { ChatMessage, QuickReply } from "./types";

/**
 * The fixed orienting line that frames recalled cards. Lifted to a
 * constant so the verify script can pin it without re-typing the
 * literal — drift here would silently break the user experience the
 * `EarlierResultsLink` doc comment promises.
 */
export const RESTORED_RESULTS_PREFIX =
  "Here are the services you were looking at before:";

/**
 * Standard quick replies attached to a restored-results message.
 * Mirrors the post-results QR set the backend uses in
 * `backend/app/services/chatbot/handlers/post_results.py` for its
 * "Here are all the results again" path — the user is in the same
 * "I have results, what next" state, so the two affordances are the
 * same: start over, or talk to a peer navigator. The snapshot's own
 * quick replies (which may include pagination cursors or filter
 * affordances tied to server state that no longer exists) are
 * intentionally dropped.
 */
export const RESTORED_RESULTS_QUICK_REPLIES: QuickReply[] = [
  { label: "🔍 New search", value: "Start over" },
  { label: "🤝 Peer navigator", value: "Connect with peer navigator" },
];

/**
 * Build the single bot message that the chat stream should append
 * when the user taps "See earlier results". Pure function — verified
 * in `scripts/verify/restore-earlier-results.mjs`.
 *
 * The snapshot's original `text`, `quick_replies`, and `showFeedback`
 * are all overridden:
 *
 *   - `text` is replaced with `RESTORED_RESULTS_PREFIX`. The snapshot
 *     was last shown alongside a "I found N option(s) for you:" line
 *     (count and broadened-search qualifier specific to the wiped
 *     session); re-rendering that reads like a fresh search, which
 *     it isn't.
 *
 *   - `quick_replies` are replaced with `RESTORED_RESULTS_QUICK_REPLIES`.
 *     Pagination QRs ("Show more results") reference server state
 *     that was wiped on reset; filter QRs ("Free only") reference
 *     `_last_results` that no longer exists server-side. The fresh
 *     pair routes the user to either a new search or human help.
 *
 *   - `showFeedback` is forced to `false`. The thumbs were associated
 *     with the original delivery; asking again on the same cards
 *     in a new session would be confusing.
 *
 * The cards themselves (`services`) and all other snapshot fields
 * (e.g. requestId for backend audit) are preserved via the spread.
 */
export function buildRestoredResultsMessage(
  snapshot: ChatMessage,
  newId: string,
): ChatMessage {
  return {
    ...snapshot,
    id: newId,
    text: RESTORED_RESULTS_PREFIX,
    quick_replies: RESTORED_RESULTS_QUICK_REPLIES,
    showFeedback: false,
  };
}
