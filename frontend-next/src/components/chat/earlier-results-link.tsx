// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * "Your earlier results" restore prompt.
 *
 * Shown at the top of the chat log after a session reset (TTL expiry
 * or explicit user reset) when there was a previous results message
 * in the wiped conversation. Gives the user two clear choices:
 *
 *   - See earlier results: injects a single bot message that pairs
 *     the saved service cards with the orienting prefix "Here are
 *     the services you were looking at before:" and offers
 *     fresh-conversation quick replies ("New search" / "Peer
 *     navigator"). The snapshot's original text — "I found N
 *     option(s) for you:" with counts specific to the wiped
 *     session — is intentionally NOT re-rendered: the recall isn't
 *     a new search and shouldn't read like one. See
 *     `store.ts::restoreEarlierResults` for the override details.
 *   - Dismiss: silently discards the snapshot.
 *
 * Rationale: in the target use case ("I just found a shelter, session
 * expired, where is it?") losing the results silently is a real
 * failure. The restore link respects the user's memory of what they
 * were doing without forcing them to re-ask.
 */

"use client";

import type { ChatMessage } from "@/lib/chat/types";
import { History } from "lucide-react";

interface EarlierResultsLinkProps {
  snapshot: ChatMessage;
  onRestore: () => void;
  onDismiss: () => void;
}

export function EarlierResultsLink({
  snapshot,
  onRestore,
  onDismiss,
}: EarlierResultsLinkProps) {
  const count = snapshot.services?.length ?? 0;
  // Fallback labels — snapshot may not carry a query string
  const label =
    count > 0
      ? `${count} ${count === 1 ? "service" : "services"} from your last search`
      : "services from your last search";

  return (
    <div
      role="status"
      className="mx-1 mb-2 px-3 py-2.5 rounded-lg bg-neutral-50 border border-neutral-200 text-sm text-neutral-700 flex items-center gap-2 dark:bg-neutral-900 dark:border-neutral-800 dark:text-neutral-300"
    >
      <History size={16} className="shrink-0 text-neutral-500 dark:text-neutral-400" aria-hidden="true" />
      <div className="flex-1">
        <button
          type="button"
          onClick={onRestore}
          className="underline underline-offset-2 font-medium text-neutral-900 hover:text-amber-700 focus:outline-none focus:ring-2 focus:ring-amber-400 rounded dark:text-neutral-100 dark:hover:text-amber-300"
        >
          See earlier results
        </button>{" "}
        <span className="text-neutral-500 dark:text-neutral-400">— {label}</span>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 text-xs text-neutral-500 hover:text-neutral-700 underline underline-offset-2 focus:outline-none focus:ring-2 focus:ring-neutral-400 rounded px-1 dark:text-neutral-400 dark:hover:text-neutral-200"
        aria-label="Dismiss earlier results"
      >
        Dismiss
      </button>
    </div>
  );
}
