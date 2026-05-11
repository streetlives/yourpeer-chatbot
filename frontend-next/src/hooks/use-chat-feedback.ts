// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback } from "react";
import { useChatStore } from "@/lib/chat/store";
import { sendFeedback } from "@/lib/chat/api";
import type { FeedbackRating } from "@/lib/chat/types";

/**
 * Hook that exposes the `submitFeedback` callback.
 *
 * Extracted from `use-chat.ts` per `FRONTEND_AUDIT.md` 2026-05 P1 #4
 * (split the 1243-line hook into smaller pieces). This piece is
 * self-contained — it only reads `sessionId` and `messages` from the
 * chat store and calls the `sendFeedback` API. No interaction with
 * the send pipeline, offline queue, or service-worker reconcile.
 *
 * Behavior preserved bit-for-bit from the inline version: gather
 * context from the most recent bot message with results (or the most
 * recent bot text), truncate to keep payload small, fire-and-forget
 * the POST. The dependency array matches the original (`sessionId`,
 * `messages`).
 *
 * Why a hook (returns a callback) rather than a plain function:
 *   `submitFeedback` reads live store state, which means it has to
 *   close over the reactive `messages` array. A plain function would
 *   need to receive `messages` on every call from the caller —
 *   pushing the same hook subscription up to the parent. Wrapping it
 *   here keeps the consumer's call site simple (`submitFeedback(rating)`)
 *   and the store subscription co-located with what depends on it.
 */
export function useChatFeedback(): (rating: FeedbackRating) => void {
  const { sessionId, messages } = useChatStore();

  return useCallback(
    (rating: FeedbackRating) => {
      if (!sessionId) return;

      // Gather context from the most recent bot message with results,
      // or the most recent bot response text if no results were shown.
      const botMessages = messages.filter((m) => m.role === "bot");
      const lastWithResults = [...botMessages]
        .reverse()
        .find((m) => m.services && m.services.length > 0);
      const lastBot = botMessages[botMessages.length - 1];

      const context: Record<string, unknown> = {};
      if (lastWithResults?.services) {
        context.result_count = lastWithResults.services.length;
        context.service_names = lastWithResults.services
          .slice(0, 10)
          .map((s) => s.service_name)
          .filter(Boolean);
        context.organizations = [
          ...new Set(
            lastWithResults.services
              .map((s) => s.organization)
              .filter(Boolean),
          ),
        ].slice(0, 5);
      }
      if (lastBot?.text) {
        // Truncate to avoid sending huge payloads
        context.bot_response = lastBot.text.slice(0, 200);
      }

      sendFeedback(sessionId, rating, context);
    },
    [sessionId, messages],
  );
}
