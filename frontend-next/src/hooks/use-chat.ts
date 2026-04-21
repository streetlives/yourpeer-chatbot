// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect, useRef } from "react";
import { useChatStore, nextMsgId } from "@/lib/chat/store";
import { sendChatMessage, sendFeedback } from "@/lib/chat/api";
import { useGeolocation } from "./use-geolocation";
import type { FeedbackRating, ChatMessage } from "@/lib/chat/types";
import {
  enqueue as enqueueMessage,
  dequeue as dequeueMessage,
  readQueue,
  reapExpired,
  type QueuedMessage,
} from "@/lib/chat/send-queue";
import { cacheLastResults } from "@/lib/chat/offline-cache";

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
  } catch (err: unknown) {
    const msg = errMessage(err);
    // Don't retry rate limits or auth errors
    if (msg.includes("429") || msg.includes("403")) throw err;
    await delay(retryDelayMs);
    return fn();
  }
}

/** Safely extract a message string from an unknown thrown value. */
function errMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && "message" in err) {
    const m = (err as { message?: unknown }).message;
    return typeof m === "string" ? m : "";
  }
  return "";
}

/** Safely extract an error name (e.g. "TypeError") from an unknown thrown value. */
function errName(err: unknown): string {
  if (err instanceof Error) return err.name;
  if (err && typeof err === "object" && "name" in err) {
    const n = (err as { name?: unknown }).name;
    return typeof n === "string" ? n : "";
  }
  return "";
}

/** Convert a caught error into a user-friendly message. */
function userFacingError(err: unknown): string {
  const msg = errMessage(err);
  const name = errName(err);
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

/**
 * True when an error looks like a network failure (offline, DNS, etc.),
 * as opposed to a server-returned error (4xx/5xx). Used to decide
 * whether to enqueue the message for later flush or surface the error
 * to the user immediately.
 *
 * Rate limits (429), auth (403), bad request (400), server errors
 * (5xx) are NOT network errors — they mean we reached the server and
 * it answered. Only retry via queue when there's genuinely no connection.
 */
function isNetworkError(err: unknown): boolean {
  const msg = errMessage(err);
  const name = errName(err);
  if (name === "TypeError") return true;          // fetch itself failed
  if (name === "TimeoutError") return true;
  if (name === "AbortError") return true;
  if (msg.includes("fetch")) return true;
  if (msg.includes("NetworkError")) return true;
  // HTTP status codes in the message mean we got a response
  if (/\b[45]\d\d\b/.test(msg)) return false;
  return false;
}

/**
 * Write a bot response that includes service cards to the offline
 * cache. Fire-and-forget — caching failures never block the UI.
 *
 * Called from all three success paths (geo flow, crisis flow, normal
 * send). Kept as a standalone helper so if any success path is added
 * later, we don't forget to cache there too.
 */
function cacheIfResults(botMessage: ChatMessage, userQuery: string): void {
  if (!botMessage.services || botMessage.services.length === 0) return;
  void cacheLastResults(botMessage, userQuery);
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

          const botMsg: ChatMessage = {
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          };
          addMessage(botMsg);
          cacheIfResults(botMsg, "near me");
        } catch (err: unknown) {
          removeMessage(searchProgressId);

          // Stale session token — clear and retry
          if (errMessage(err).includes("403") && sessionId) {
            try {
              useChatStore.getState().setSessionId(null);
              const data = await sendChatMessage("near me", null, coords);
              if (data.session_id) setSessionId(data.session_id);
              const botMsg: ChatMessage = {
                id: nextMsgId(),
                role: "bot",
                text: data.response || "(No response text)",
                services: data.services,
                quick_replies: data.quick_replies,
                showFeedback: (data.services?.length ?? 0) > 0,
              };
              addMessage(botMsg);
              cacheIfResults(botMsg, "near me");
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

          const botMsg: ChatMessage = {
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          };
          addMessage(botMsg);
          cacheIfResults(botMsg, "Yes, search");
        } catch (err: unknown) {
          removeMessage(searchProgressId);

          if (errMessage(err).includes("403") && sessionId) {
            try {
              useChatStore.getState().setSessionId(null);
              const data = await sendChatMessage("Yes, search", null, coordsToSend);
              if (data.session_id) setSessionId(data.session_id);
              const botMsg: ChatMessage = {
                id: nextMsgId(),
                role: "bot",
                text: data.response || "(No response text)",
                services: data.services,
                quick_replies: data.quick_replies,
                showFeedback: (data.services?.length ?? 0) > 0,
              };
              addMessage(botMsg);
              cacheIfResults(botMsg, "Yes, search");
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
      const userMsgId = nextMsgId();
      addMessage({ id: userMsgId, role: "user", text: message });

      setLoading(true);
      try {
        // Attach coords if we have them
        const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
        // Auto-retry once with 1.5s backoff for transient failures (not 429/403)
        const data = await withRetry(() => sendChatMessage(message, sessionId, coords));
        if (data.session_id) setSessionId(data.session_id);

        const botMsg: ChatMessage = {
          id: nextMsgId(),
          role: "bot",
          text: data.response || "(No response text)",
          services: data.services,
          quick_replies: data.quick_replies,
          showFeedback: (data.services?.length ?? 0) > 0,
        };
        addMessage(botMsg);
        cacheIfResults(botMsg, message);
      } catch (err: unknown) {
        // If the backend rejected our session token (e.g. SECRET changed),
        // clear the stale sessionId and retry once with no session so the
        // backend mints a fresh token.
        if (errMessage(err).includes("403") && sessionId) {
          try {
            useChatStore.getState().setSessionId(null);
            const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
            const data = await sendChatMessage(message, null, coords);
            if (data.session_id) setSessionId(data.session_id);
            const botMsg: ChatMessage = {
              id: nextMsgId(),
              role: "bot",
              text: data.response || "(No response text)",
              services: data.services,
              quick_replies: data.quick_replies,
              showFeedback: (data.services?.length ?? 0) > 0,
            };
            addMessage(botMsg);
            cacheIfResults(botMsg, message);
            setLoading(false);
            return;
          } catch {
            // Retry also failed — fall through to normal error handling
          }
        }

        // Network error (offline, DNS, timeout) — enqueue the message
        // for later flush instead of surfacing an error. The user's
        // message stays in the chat so they can see what they sent.
        // A subtle "waiting to send" bot message signals queue state.
        if (isNetworkError(err)) {
          const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
          const queuedMsg: QueuedMessage = {
            id: userMsgId,
            text: message,
            coords,
            sessionId,
            queuedAt: Date.now(),
          };
          await enqueueMessage(queuedMsg);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: "Saved — I'll send this when you're back online.",
            transient: true,
          });
          setLoading(false);
          return;
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
          const botMsg: ChatMessage = {
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          };
          addMessage(botMsg);
          cacheIfResults(botMsg, "near me");
        } catch (err: unknown) {
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

        const botMsg: ChatMessage = {
          id: nextMsgId(),
          role: "bot",
          text: data.response || "(No response text)",
          services: data.services,
          quick_replies: data.quick_replies,
          showFeedback: (data.services?.length ?? 0) > 0,
        };
        addMessage(botMsg);
        cacheIfResults(botMsg, originalText);
      } catch (err: unknown) {
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

  // Flush queued messages when connection returns.
  //
  // Uses a ref-based lock so a rapid offline/online/offline flapping
  // pattern doesn't fire concurrent flushes. Each flushed message is
  // sent in serial order — the chatbot is stateful, so parallel sends
  // would scramble conversational context.
  //
  // We deliberately DO NOT use the `send` callback here: that would
  // push another "user" message into the chat for each queued item,
  // but the user's original message is already in the chat (added
  // when they typed it while offline). We use sendChatMessage
  // directly and addMessage for just the bot response.
  const flushingRef = useRef(false);

  const flushQueue = useCallback(async () => {
    if (flushingRef.current) return;
    if (!navigator.onLine) return;
    flushingRef.current = true;

    try {
      // Drop expired entries first. If any were reaped, tell the user
      // which ones so they know why nothing happened for them.
      const expired = await reapExpired();
      if (expired.length > 0) {
        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: `Some messages were waiting too long and weren't sent. Feel free to ask again: ${expired
            .map((m) => `"${m.text.slice(0, 40)}${m.text.length > 40 ? "…" : ""}"`)
            .join(", ")}`,
          transient: true,
        });
      }

      const queue = await readQueue();
      if (queue.length === 0) return;

      // Session probe: if the session expired server-side (30min TTL),
      // warn the user before sending queued messages with a stale
      // session token. We detect this by attempting the first message;
      // if it returns 403, the backend mints a new session, but the
      // conversational context (slots, last query) is gone.
      //
      // Rather than pre-probe (another round-trip), we optimistically
      // send and tell the user conversationally if the session reset.
      let sessionResetWarned = false;

      for (const queued of queue) {
        // Re-check online in case we went offline mid-flush
        if (!navigator.onLine) break;

        try {
          const data = await sendChatMessage(
            queued.text,
            queued.sessionId,
            queued.coords,
          );
          if (data.session_id) setSessionId(data.session_id);

          // Session probe heuristic: if the queued message had a
          // session ID but the response came back with a *different*
          // session ID, the backend rejected our token and minted a
          // fresh one. Context was lost — warn the user once.
          if (
            !sessionResetWarned &&
            queued.sessionId &&
            data.session_id &&
            data.session_id !== queued.sessionId
          ) {
            addMessage({
              id: nextMsgId(),
              role: "bot",
              text: "You were offline for a while — starting a fresh conversation.",
              transient: true,
            });
            sessionResetWarned = true;
          }

          const botMsg: ChatMessage = {
            id: nextMsgId(),
            role: "bot",
            text: data.response || "(No response text)",
            services: data.services,
            quick_replies: data.quick_replies,
            showFeedback: (data.services?.length ?? 0) > 0,
          };
          addMessage(botMsg);
          cacheIfResults(botMsg, queued.text);

          // Success — remove from queue
          await dequeueMessage(queued.id);
        } catch (err: unknown) {
          // Network error again (e.g., connection dropped mid-flush) —
          // leave in queue and stop trying. Will retry on next online.
          if (isNetworkError(err)) break;

          // Server-side error (auth, rate limit, 5xx) — surface to
          // user and dequeue the message so we don't retry forever.
          // The user sees what failed and can try again manually.
          const friendlyMsg = userFacingError(err);
          addMessage({
            id: nextMsgId(),
            role: "bot",
            text: `Couldn't send "${queued.text.slice(0, 40)}${queued.text.length > 40 ? "…" : ""}": ${friendlyMsg}`,
            retryMessage: friendlyMsg.includes("wait") ? undefined : queued.text,
          });
          await dequeueMessage(queued.id);
          // Rate-limit: stop flushing (don't burn through the rate
          // limit for every queued message). Let the user manually
          // retry from the button.
          if (errMessage(err).includes("429")) break;
        }
      }
    } finally {
      flushingRef.current = false;
    }
  }, [addMessage, setSessionId]);

  // Register the online handler once and also probe on mount (in case
  // we came back online while the component was unmounted).
  useEffect(() => {
    const handler = () => {
      void flushQueue();
    };
    window.addEventListener("online", handler);
    // Probe on mount — queue might have entries from a previous session
    if (navigator.onLine) {
      void flushQueue();
    }
    return () => window.removeEventListener("online", handler);
  }, [flushQueue]);

  return { messages, isLoading, error, send, retry, submitFeedback };
}
