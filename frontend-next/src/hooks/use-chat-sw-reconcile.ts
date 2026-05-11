// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect } from "react";
import { useChatStore, nextMsgId } from "@/lib/chat/store";
import { reconcilePending } from "@/lib/chat/pending-responses";
import { cacheIfResults } from "@/lib/chat/use-chat-helpers";
import type { ChatMessage } from "@/lib/chat/types";

/**
 * Side-effect hook that reconciles service-worker-delivered pending
 * responses into the chat log.
 *
 * Extracted from `use-chat.ts` per `FRONTEND_AUDIT.md` 2026-05 P1 #4
 * (split the 1243-line hook into smaller pieces). This piece is
 * fully independent of the send pipeline and the offline flush
 * queue — its only interaction with them is "both fire on the
 * `online` event," which is harmless because each one's payload is
 * idempotent (the SW reconcile drains its own pending-responses
 * store; the flush drains its own queue).
 *
 * What it does:
 *   When the service worker handles a Background Sync event for a
 *   queued message while the tab is closed or frozen, it stores the
 *   server's response under `yourpeer:pending-responses:v1`. On the
 *   next tab focus (mount) and on every `online` event, this hook
 *   drains that store and injects the responses into the chat log.
 *
 * Triggers:
 *   - On mount: covers the case where the user closed the tab
 *     before the queue drained, then reopens later. Any
 *     SW-completed sends surface now.
 *   - On `online`: covers the case where the tab stayed open during
 *     an offline spell, the SW drained on reconnect, and the
 *     reconcile happens right after the online handler fires the
 *     client-side flush (harmless duplication — server dedupes via
 *     X-Request-ID, client dedupes by message id).
 *
 * The reconcile itself is session-aware: `reconcilePending` drops
 * pending responses for sessions the user has since reset. Responses
 * for the current session (or from a request that started with no
 * session) are applied here.
 *
 * Returns nothing. The hook is for its side effects only.
 */
export function useChatSwReconcile(): void {
  const { sessionId, addMessage, updateMessage, setSessionId } = useChatStore();

  const reconcileSwResponses = useCallback(async () => {
    if (typeof window === "undefined") return;
    const matched = await reconcilePending(sessionId);
    if (matched.length === 0) return;

    let sessionResetWarned = false;
    for (const entry of matched) {
      // Narrow the unknown response body to a shape we can use. The
      // SW writes whatever /api/chat returned; if the shape is off
      // (e.g. old server, partial response), skip rather than crash.
      const body = entry.body;
      if (!body || typeof body !== "object") continue;
      const data = body as {
        response?: string;
        session_id?: string;
        services?: ChatMessage["services"];
        quick_replies?: ChatMessage["quick_replies"];
      };

      if (data.session_id) setSessionId(data.session_id);

      // Same session-reset heuristic as flushQueue: if the pending
      // response was for a token the server rejected and minted a
      // new one, let the user know. Guarded to fire at most once
      // per reconcile batch so a backlog doesn't spam the user.
      if (
        !sessionResetWarned &&
        entry.sessionId &&
        data.session_id &&
        data.session_id !== entry.sessionId
      ) {
        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: "You were offline for a while — starting a fresh conversation.",
          transient: true,
        });
        sessionResetWarned = true;
      }

      // Flip the user's own message (which should still be present
      // with status="pending") to "sent". updateMessage is a no-op
      // if the message isn't found — safe if the user wiped history
      // in between.
      updateMessage(entry.id, { status: "sent" });

      const botMsg: ChatMessage = {
        id: nextMsgId(),
        role: "bot",
        text: data.response || "(No response text)",
        services: data.services,
        quick_replies: data.quick_replies,
        showFeedback: (data.services?.length ?? 0) > 0,
      };
      addMessage(botMsg);
      // cacheIfResults needs the user's original query; we don't have
      // it here (pending-responses stores only the response, not the
      // request). Look it up from the live message log — if it's
      // still there, we cache; if not (history was wiped), skip.
      const userMsg = useChatStore
        .getState()
        .messages.find((m) => m.id === entry.id);
      if (userMsg) {
        cacheIfResults(botMsg, userMsg.text);
      }
    }
  }, [sessionId, addMessage, updateMessage, setSessionId]);

  useEffect(() => {
    // Fire on mount and whenever the tab comes back online. The
    // reconcile itself is idempotent (drain-and-clear pattern) so
    // double-fires are harmless.
    void reconcileSwResponses();
    const handler = () => {
      void reconcileSwResponses();
    };
    window.addEventListener("online", handler);
    return () => window.removeEventListener("online", handler);
  }, [reconcileSwResponses]);
}
