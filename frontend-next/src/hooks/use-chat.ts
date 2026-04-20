// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback } from "react";
import { useChatStore, nextMsgId } from "@/lib/chat/store";
import { sendChatMessage, sendFeedback } from "@/lib/chat/api";
import { useGeolocation } from "./use-geolocation";
import type { FeedbackRating } from "@/lib/chat/types";

const GEOLOCATION_TRIGGER = "__use_geolocation__";
const CRISIS_GEO_TRIGGER = "__crisis_geo_search__";

/** Wait ms milliseconds. */
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Try an async operation with one automatic retry after a delay.
 * Does NOT retry 429 (rate limit) or 403 (auth) errors.
 */
async function withRetry<T>(fn: () => Promise<T>, retryDelayMs = 1500): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    // Don't retry rate limits or auth errors
    if (msg.includes("429") || msg.includes("403")) throw err;
    await delay(retryDelayMs);
    return fn();
  }
}

/** Convert a caught error into a user-friendly message. */
function userFacingError(err: unknown): string {
  const msg = err instanceof Error ? err.message : "Unknown error";
  const name = err instanceof Error ? err.name : "Unknown error";
  // Rate-limit messages include "wait" — pass through verbatim
  if (msg.includes("wait")) return msg;
  // API layer errors (503, 500) already have good messages — pass through
  if (msg.includes("temporarily unavailable") || msg.includes("on our end") || msg.includes("Try again")) return msg;
  // Network error — fetch itself failed (no response)
  if (name === "TypeError" || msg.includes("fetch")) return "Can't reach the server right now. Check your connection and try again.";
  // Timeout — AbortSignal.timeout fired
  if (name === "TimeoutError" || name === "AbortError") return "The search is taking longer than expected. Try again in a moment.";
  // Fallback
  return "Sorry, something went wrong. Try again in a moment.";
}

export function useChat() {
  const {
    sessionId,
    messages,
    isLoading,
    error,
    setSessionId,
    addMessage,
    removeMessage,
    setLoading,
    setError,
    markQuickRepliesUsed,
  } = useChatStore();

  const { latitude, longitude, hasCoords, requestLocation } = useGeolocation();

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message) return;

      // Mark any existing quick replies as used
      markQuickRepliesUsed();

      // Handle "Use my location" quick reply
      if (message === GEOLOCATION_TRIGGER) {
        addMessage({ id: nextMsgId(), role: "user", text: "Use my location" });
        setLoading(true);

        // Show immediate feedback — the browser permission dialog + GPS
        // lookup can take several seconds with no visible progress.
        const geoProgressId = nextMsgId();
        if (!hasCoords) {
          addMessage({ id: geoProgressId, role: "bot", text: "Getting your location…", transient: true });
        }

        const coords = hasCoords
          ? { latitude: latitude!, longitude: longitude! }
          : await requestLocation();

        // Remove the "Getting your location" progress message
        if (!hasCoords) removeMessage(geoProgressId);

        if ("error" in coords) {
          // Permission denied, timeout, or unavailable — show specific reason
          setLoading(false);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: coords.error,
            quick_replies: [
              { label: "Manhattan", value: "Manhattan" },
              { label: "Brooklyn", value: "Brooklyn" },
              { label: "Queens", value: "Queens" },
              { label: "Bronx", value: "Bronx" },
              { label: "Staten Island", value: "Staten Island" },
            ],
          });
          return;
        }

        // Got coords — show searching progress, then send API call
        const searchProgressId = nextMsgId();
        addMessage({ id: searchProgressId, role: "bot", text: "Finding where you are…", transient: true });

        try {
          const data = await withRetry(() => sendChatMessage("near me", sessionId, coords));
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);

          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          });
        } catch (err) {
          removeMessage(searchProgressId);
          const msg = err instanceof Error ? err.message : "Unknown error"
          // Stale session token — clear and retry
          if (msg.includes("403") && sessionId) {
            try {
              useChatStore.getState().setSessionId(null);
              const data = await sendChatMessage("near me", null, coords);
              if (data.session_id) setSessionId(data.session_id);
              addMessage({
                id: nextMsgId(),
                role: "bot",
                text: data.response || "(No response text)",
                services: data.services,
                quick_replies: data.quick_replies,
                showFeedback: (data.services?.length ?? 0) > 0,
              });
              setLoading(false);
              return;
            } catch {
              // Fall through
            }
          }

          const friendlyMsg = userFacingError(err);
          setError(friendlyMsg);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: friendlyMsg,
            retryMessage: friendlyMsg.includes("wait") ? undefined : GEOLOCATION_TRIGGER,
          });
        } finally {
          setLoading(false);
        }
        return;
      }

      // Handle "Yes, search" from crisis step-down.
      // Request geolocation FIRST, then send "Yes, search" with coords
      // so the backend crisis handler can execute immediately.
      if (message === CRISIS_GEO_TRIGGER) {
        addMessage({ id: nextMsgId(), role: "user", text: "Yes, search nearby" });
        setLoading(true);

        // Request geolocation — show progress while browser dialog is open
        const geoProgressId = nextMsgId();
        if (!hasCoords) {
          addMessage({ id: geoProgressId, role: "bot", text: "Getting your location…", transient: true });
        }

        const geoResult = hasCoords
          ? { latitude: latitude!, longitude: longitude! }
          : await requestLocation();

        if (!hasCoords) removeMessage(geoProgressId);

        // Send "Yes, search" to backend — with or without coords.
        // If coords available, backend executes search immediately.
        // If denied, backend falls back to asking for borough.
        const coordsToSend = "error" in geoResult ? null : geoResult;

        const searchProgressId = nextMsgId();
        addMessage({ id: searchProgressId, role: "bot", text: "Searching nearby…", transient: true });

        try {
          const data = await withRetry(() => sendChatMessage("Yes, search", sessionId, coordsToSend));
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);

          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          });
        } catch (err) {
          removeMessage(searchProgressId);
          const msg = err instanceof Error ? err.message : "Unknown error";
          if (msg.includes("403") && sessionId) {
            try {
              useChatStore.getState().setSessionId(null);
              const data = await sendChatMessage("Yes, search", null, coordsToSend);
              if (data.session_id) setSessionId(data.session_id);
              addMessage({
                id: nextMsgId(),
                role: "bot",
                text: data.response || "(No response text)",
                services: data.services,
                quick_replies: data.quick_replies,
                showFeedback: (data.services?.length ?? 0) > 0,
              });
              setLoading(false);
              return;
            } catch {
              // Fall through
            }
          }

          const friendlyMsg = userFacingError(err);
          setError(friendlyMsg);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: friendlyMsg,
            retryMessage: friendlyMsg.includes("wait") ? undefined : CRISIS_GEO_TRIGGER,
          });
        } finally {
          setLoading(false);
        }
        return;
      }

      // Normal message flow
      addMessage({ id: nextMsgId(), role: "user", text: message });

      setLoading(true);
      try {
        // Attach coords if we have them
        const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
        // Auto-retry once with 1.5s backoff for transient failures (not 429/403)
        const data = await withRetry(() => sendChatMessage(message, sessionId, coords));
        if (data.session_id) setSessionId(data.session_id);

        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: data.response || "(No response text)",
          services: data.services,
          quick_replies: data.quick_replies,
          showFeedback: (data.services?.length ?? 0) > 0,
        });
      } catch (err) {
        // If the backend rejected our session token (e.g. SECRET changed),
        // clear the stale sessionId and retry once with no session so the
        // backend mints a fresh token.
        const msg = err instanceof Error ? err.message : "Unknown error";
        if (msg.includes("403") && sessionId) {
          try {
            useChatStore.getState().setSessionId(null);
            const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
            const data = await sendChatMessage(message, null, coords);
            if (data.session_id) setSessionId(data.session_id);
            addMessage({
              id: nextMsgId(),
              role: "bot",
              text: data.response || "(No response text)",
              services: data.services,
              quick_replies: data.quick_replies,
              showFeedback: (data.services?.length ?? 0) > 0,
            });
            setLoading(false);
            return;
          } catch {
            // Retry also failed — fall through to normal error handling
          }
        }

        const friendlyMsg = userFacingError(err);
        setError(friendlyMsg);
        // Rate-limit errors include timing info — show as-is, no retry.
        // Other errors get a Retry button via retryMessage.
        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: friendlyMsg,
          retryMessage: friendlyMsg.includes("wait") ? undefined : message,
        });
      } finally {
        setLoading(false);
      }
    },
    [sessionId, latitude, longitude, hasCoords, addMessage, removeMessage, setSessionId, setLoading, setError, markQuickRepliesUsed, requestLocation],
  );

  /** Retry a failed message — removes the error and re-sends without
   *  adding a duplicate user message (the original is still in the chat). */
  const retry = useCallback(
    async (errorMsgId: string, originalText: string) => {
      removeMessage(errorMsgId);

      // Geolocation retry — re-run location request + API call
      if (originalText === GEOLOCATION_TRIGGER) {
        setLoading(true);

        const geoProgressId = nextMsgId();
        if (!hasCoords) {
          addMessage({ id: geoProgressId, role: "bot", text: "Getting your location…", transient: true });
        }

        const geoResult = hasCoords
          ? { latitude: latitude!, longitude: longitude! }
          : await requestLocation();

        if (!hasCoords) removeMessage(geoProgressId);

        if ("error" in geoResult) {
          setLoading(false);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: geoResult.error,
            quick_replies: [
              { label: "Manhattan", value: "Manhattan" },
              { label: "Brooklyn", value: "Brooklyn" },
              { label: "Queens", value: "Queens" },
              { label: "Bronx", value: "Bronx" },
              { label: "Staten Island", value: "Staten Island" },
            ],
          });
          return;
        }

        const searchProgressId = nextMsgId();
        addMessage({ id: searchProgressId, role: "bot", text: "Finding where you are…", transient: true });

        try {
          const data = await withRetry(() => sendChatMessage("near me", sessionId, geoResult));
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          });
        } catch (err) {
          removeMessage(searchProgressId);
          const friendlyMsg = userFacingError(err);
          setError(friendlyMsg);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: friendlyMsg,
            retryMessage: friendlyMsg.includes("wait") ? undefined : GEOLOCATION_TRIGGER,
          });
        } finally {
          setLoading(false);
        }
        return;
      }

      // Normal retry — API call only, user message is already in chat
      setLoading(true);
      try {
        const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
        const data = await withRetry(() => sendChatMessage(originalText, sessionId, coords));
        if (data.session_id) setSessionId(data.session_id);

        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: data.response || "(No response text)",
          services: data.services,
          quick_replies: data.quick_replies,
          showFeedback: (data.services?.length ?? 0) > 0,
        });
      } catch (err) {
        const friendlyMsg = userFacingError(err);
        setError(friendlyMsg);
        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: friendlyMsg,
          retryMessage: friendlyMsg.includes("wait") ? undefined : originalText,
        });
      } finally {
        setLoading(false);
      }
    },
    [sessionId, latitude, longitude, hasCoords, addMessage, removeMessage, setSessionId, setLoading, setError, requestLocation],
  );

  const submitFeedback = useCallback(
    (rating: FeedbackRating) => {
      if (!sessionId) return;

      // Gather context from the most recent bot message with results,
      // or the most recent bot response text if no results were shown.
      const botMessages = messages.filter((m) => m.role === "bot");
      const lastWithResults = [...botMessages].reverse().find((m) => m.services && m.services.length > 0);
      const lastBot = botMessages[botMessages.length - 1];

      const context: Record<string, unknown> = {};
      if (lastWithResults?.services) {
        context.result_count = lastWithResults.services.length;
        context.service_names = lastWithResults.services
          .slice(0, 10)
          .map((s) => s.service_name)
          .filter(Boolean);
        context.organizations = [...new Set(
          lastWithResults.services.map((s) => s.organization).filter(Boolean),
        )].slice(0, 5);
      }
      if (lastBot?.text) {
        // Truncate to avoid sending huge payloads
        context.bot_response = lastBot.text.slice(0, 200);
      }

      sendFeedback(sessionId, rating, context);
    },
    [sessionId, messages],
  );

  return { messages, isLoading, error, send, retry, submitFeedback };
}
