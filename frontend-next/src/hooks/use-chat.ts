// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect } from "react";
import { useChatStore, nextMsgId } from "@/lib/chat/store";
import { sendChatMessage, sendFeedback } from "@/lib/chat/api";
import { useGeolocation } from "./use-geolocation";
import type { FeedbackRating, ChatMessage } from "@/lib/chat/types";
import {
  enqueue as enqueueMessage,
  dequeue as dequeueMessage,
  readQueue,
  reapExpired,
} from "@/lib/chat/send-queue";
import { cacheLastResults } from "@/lib/chat/offline-cache";
import { generateRequestId } from "@/lib/chat/request-id";

const GEOLOCATION_TRIGGER = "__use_geolocation__";
const CRISIS_GEO_TRIGGER = "__crisis_geo_search__";

/**
 * Module-level coordinator for the offline-queue flush.
 *
 * Holds the Promise for the currently-draining flush, or null when
 * none is running. Used by:
 *   1. `flushQueue()` — re-entry guard (second call returns the
 *      in-flight Promise so it's awaitable, not a no-op).
 *   2. `send()` and `retry()` — serialize new outbound POSTs after
 *      any ongoing flush. Without this, two concurrent POSTs to the
 *      stateful chatbot interleave and bot responses come back out
 *      of order.
 *
 * Module-scoped rather than a `useRef` because:
 *   a) The flush coordinator is singleton per-tab — multiple mounts
 *      of the hook (e.g. during fast refresh or suspense retries)
 *      should share the same gate, not each get their own.
 *   b) React 19's compiler-aware lint rules flag in-callback
 *      mutations of ref.current as "modifying a hook argument."
 *      Module scope sidesteps that analysis cleanly.
 *
 * NOT a useRef even with the "Ref" suffix — see comment above.
 */
let flushInFlight: Promise<void> | null = null;

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
 * as opposed to a server-returned error (4xx/5xx) or a request timeout.
 * Used to decide whether to enqueue the message for later flush or
 * surface the error to the user immediately.
 *
 * Rate limits (429), auth (403), bad request (400), server errors
 * (5xx) are NOT network errors — they mean we reached the server and
 * it answered.
 *
 * Timeouts (AbortSignal.timeout firing) are also NOT treated as
 * network errors, even though they produce no response: a timeout
 * means the server was slow, not that the user was offline. Queuing
 * on timeout would show the user "I'll send this when you're back
 * online" while they're clearly online, which is confusing. Let those
 * flow to the normal error path where userFacingError() says "taking
 * longer than expected — try again."
 *
 * As a final guard we also check navigator.onLine. Even if fetch
 * raised TypeError, if the browser thinks it's online the user
 * probably sees a degraded state (e.g. captive portal, VPN issue)
 * better served by an error message than silent queuing.
 */
function isNetworkError(err: unknown): boolean {
  // Browser is sure we're online → not a queue-worthy network failure
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    // Offline for sure — anything that failed is a network error
    return true;
  }
  const msg = errMessage(err);
  const name = errName(err);
  // Timeouts are slow-server, not no-network — don't queue
  if (name === "TimeoutError") return false;
  if (name === "AbortError") return false;
  if (name === "TypeError") return true;          // fetch itself failed
  if (msg.includes("Failed to fetch")) return true;
  if (msg.includes("NetworkError")) return true;
  // HTTP status codes in the message mean we got a response
  if (/\b[45]\d\d\b/.test(msg)) return false;
  return false;
}

/**
 * Write a bot response that includes service cards to the offline
 * cache. Fire-and-forget — caching failures never block the UI.
 *
 * Called from every success path (geo flow, crisis flow, normal
 * send, retries, queue flush). Kept as a standalone helper so if
 * any success path is added later, we don't forget to cache there
 * too.
 *
 * Guards:
 * - Requires at least one service in the response. Without services
 *   there's nothing useful to show offline.
 * - Requires no retryMessage. If the response also carries a retry
 *   prompt it's an error message, not results — don't cache.
 */
function cacheIfResults(botMessage: ChatMessage, userQuery: string): void {
  if (!botMessage.services || botMessage.services.length === 0) return;
  if (botMessage.retryMessage) return;
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
    updateMessage,
    removeMessage,
    setLoading,
    setError,
    markQuickRepliesUsed,
    dismissEarlierResults,
  } = useChatStore();

  const { latitude, longitude, hasCoords, requestLocation } = useGeolocation();

  /**
   * Shared helper used by every catch block that might see a network
   * error. If the error is a genuine network failure (see
   * isNetworkError for the definition), enqueue the user's message
   * for flush-on-reconnect and return true — the caller should bail
   * out of its error-handling flow. Otherwise return false and let
   * the caller handle it as a regular error (show message, retry
   * button, etc.).
   *
   * Parameters:
   * - err: the caught error
   * - userMsgId: ID of the user message already in the chat. If the
   *   message is in the chat (normal send path), its status will be
   *   updated to "pending" (or "failed" on queue rejection). For
   *   paths where no user message was added (retry, geo, crisis),
   *   callers should pass a fresh ID — updateMessage no-ops gracefully.
   * - text: the raw text being sent (what the queue will replay)
   * - requestId: the stable idempotency key for this message. Must
   *   match what was sent as X-Request-ID on the original attempt
   *   so the server can dedupe.
   */
  const handleNetworkError = useCallback(
    async (
      err: unknown,
      userMsgId: string,
      text: string,
      requestId: string,
    ): Promise<boolean> => {
      if (!isNetworkError(err)) return false;

      const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
      const result = await enqueueMessage({
        id: userMsgId,
        text,
        coords,
        sessionId,
        queuedAt: Date.now(),
        requestId,
      });

      if (result.accepted) {
        // Mark the user's own message as pending. Avoids the awkward
        // "bot reply saying your message will send later" pattern —
        // the status lives on the message itself (WhatsApp-style).
        updateMessage(userMsgId, { status: "pending" });
      } else {
        // Queue is full or IDB write failed. Soften the message and
        // mark it transient so it doesn't persist to localStorage
        // and confuse the user after the queue eventually empties.
        updateMessage(userMsgId, { status: "failed" });
        addMessage({
          id: nextMsgId(),
          role: "bot",
          text: "Something went wrong saving your message. Check your connection and try again.",
          transient: true,
          retryMessage: text,
        });
      }
      return true;
    },
    [sessionId, latitude, longitude, hasCoords, addMessage, updateMessage],
  );

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if (!message) return;

      // If a flush is currently draining the offline queue, wait for
      // it to complete before firing a new request. The chatbot is
      // stateful per-session — parallel requests interleave on the
      // backend and the two bot responses can come back out of order
      // (new message's response arriving before the queued replay's).
      // The await is a no-op on the hot path (no flush in flight);
      // it only blocks during the narrow window where a user types
      // a new message while their offline queue is replaying. The
      // input itself stays enabled — we just serialize the POSTs.
      if (flushInFlight) {
        await flushInFlight;
      }

      // The "See earlier results" link (driven by lastResultsBeforeReset)
      // represents a snapshot from a previous session that was offered
      // but not acted on. As soon as the user sends in the new session,
      // they've moved on — dismiss the snapshot so the link doesn't
      // linger next to a fresh conversation. If they actually wanted
      // their old results, they would have tapped "See earlier results"
      // before typing.
      dismissEarlierResults();

      // Mark any existing quick replies as used
      markQuickRepliesUsed();

      // Handle "Use my location" quick reply
      if (message === GEOLOCATION_TRIGGER) {
        // Capture the user message ID and an idempotency key up front
        // so status transitions (sending/pending/sent/failed) can
        // target the actual chat message, and so a retry from the
        // queue reuses the original key for backend dedupe.
        const userMsgId = nextMsgId();
        const requestId = generateRequestId();
        addMessage({
          id: userMsgId,
          role: "user",
          text: "Use my location",
          status: "sending",
          requestId,
        });
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
          // Permission denied, timeout, or unavailable — show specific reason.
          // Geolocation failure isn't a "delivery failed" state for the
          // user message (the message wasn't even sent) — drop the
          // status so it doesn't show a misleading tick.
          updateMessage(userMsgId, { status: undefined });
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
          const data = await withRetry(() =>
            sendChatMessage("near me", sessionId, coords, requestId),
          );
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);
          updateMessage(userMsgId, { status: "sent" });

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

          // Stale session token — clear and retry (reuses requestId so
          // idempotency cache can dedupe a successful-but-lost response)
          if (errMessage(err).includes("403") && sessionId) {
            try {
              useChatStore.getState().setSessionId(null);
              const data = await sendChatMessage("near me", null, coords, requestId);
              if (data.session_id) setSessionId(data.session_id);
              updateMessage(userMsgId, { status: "sent" });
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

          // Network error — queue "near me" for flush-on-reconnect
          // against the ACTUAL user message ID so its status (and the
          // Cancel affordance) shows up correctly. Reuses requestId so
          // a successful-but-lost response on the first attempt gets
          // deduped by the backend idempotency cache on flush.
          // This path only runs if geolocation itself succeeded
          // (offline coords from cache) but the backend call failed.
          if (await handleNetworkError(err, userMsgId, "near me", requestId)) {
            setLoading(false);
            return;
          }

          // Non-network error — mark user message failed
          updateMessage(userMsgId, { status: "failed" });

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
        // Capture user message ID + stable idempotency key so status
        // transitions and the backend dedupe both work correctly.
        const userMsgId = nextMsgId();
        const requestId = generateRequestId();
        addMessage({
          id: userMsgId,
          role: "user",
          text: "Yes, search nearby",
          status: "sending",
          requestId,
        });
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
          const data = await withRetry(() =>
            sendChatMessage("Yes, search", sessionId, coordsToSend, requestId),
          );
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);
          updateMessage(userMsgId, { status: "sent" });

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
              const data = await sendChatMessage("Yes, search", null, coordsToSend, requestId);
              if (data.session_id) setSessionId(data.session_id);
              updateMessage(userMsgId, { status: "sent" });
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

          // Crisis step-down intentionally does NOT queue on network
          // error. "Yes, search" is a bare API trigger that relies on
          // the server-side crisis context from earlier in the
          // conversation. If the session has been reset by the time
          // the flush runs, the server sees a meaningless "Yes,
          // search" with no context. Safer to surface the error and
          // let the user decide whether to retry. Still mark the
          // user message "failed" so the visual indicator is consistent.
          updateMessage(userMsgId, { status: "failed" });
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
      // Stable idempotency key for this logical user action. Reused on
      // every retry/flush so the server can dedupe. Persisted on the
      // message itself so queue flush can find it.
      const requestId = generateRequestId();
      addMessage({
        id: userMsgId,
        role: "user",
        text: message,
        status: "sending",
        requestId,
      });

      setLoading(true);
      try {
        // Attach coords if we have them
        const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
        // Auto-retry once with 1.5s backoff for transient failures (not 429/403)
        const data = await withRetry(() =>
          sendChatMessage(message, sessionId, coords, requestId),
        );
        if (data.session_id) setSessionId(data.session_id);

        // Mark the user's message as delivered. Drives the "two ticks"
        // visual state.
        updateMessage(userMsgId, { status: "sent" });

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
        // backend mints a fresh token. Reuses requestId for idempotency.
        if (errMessage(err).includes("403") && sessionId) {
          try {
            useChatStore.getState().setSessionId(null);
            const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
            const data = await sendChatMessage(message, null, coords, requestId);
            if (data.session_id) setSessionId(data.session_id);
            updateMessage(userMsgId, { status: "sent" });
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

        // Network error (offline, DNS) — enqueue via shared helper.
        if (await handleNetworkError(err, userMsgId, message, requestId)) {
          setLoading(false);
          return;
        }

        // Any non-network error — mark the user's message as failed so
        // it visually stands out. The error bubble below explains why.
        updateMessage(userMsgId, { status: "failed" });

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
    [sessionId, latitude, longitude, hasCoords, addMessage, updateMessage, removeMessage, setSessionId, setLoading, setError, markQuickRepliesUsed, dismissEarlierResults, requestLocation, handleNetworkError],
  );

  /** Retry a failed message — removes the error and re-sends without
   *  adding a duplicate user message (the original is still in the chat). */
  const retry = useCallback(
    async (errorMsgId: string, originalText: string) => {
      // Same serialization concern as send() — a retry fires a fresh
      // POST, and if an offline-queue flush is currently draining,
      // we wait for it before retrying. Prevents the retry's bot
      // response from arriving ahead of still-queued earlier ones.
      if (flushInFlight) {
        await flushInFlight;
      }

      // Find the user message that corresponds to this error bubble
      // BEFORE we remove the bubble. The user message that failed is
      // the most recent user-role message chronologically preceding
      // the error bubble. This is exact — not a text-match heuristic —
      // so it handles every case correctly: duplicate messages, geo
      // triggers with non-literal text, multiple stacked errors, etc.
      //
      // We need the pre-removal message list so the index lookup is
      // meaningful; after removeMessage fires, the error bubble is
      // gone and we'd have to guess the insertion point.
      const originalFailedMsg = (() => {
        const liveMessages = useChatStore.getState().messages;
        const errorIdx = liveMessages.findIndex((m) => m.id === errorMsgId);
        if (errorIdx < 0) return null;
        for (let i = errorIdx - 1; i >= 0; i--) {
          const m = liveMessages[i];
          if (m.role === "user") return m;
        }
        return null;
      })();

      removeMessage(errorMsgId);

      // Mark the original message as "sending" again. On success we
      // bump it to "sent"; on failure we roll back to "failed". This
      // keeps the visual indicator next to the user's bubble
      // consistent with the actual delivery outcome, avoiding the
      // "Not sent" label lingering on a message that eventually did
      // go through. Note updateMessage itself guards against
      // cancelled-state overwrites, so if the message is somehow in
      // a terminal state the patch no-ops safely.
      if (originalFailedMsg) {
        updateMessage(originalFailedMsg.id, { status: "sending" });
      }

      // Idempotency key for the retry. Reuse the original message's
      // requestId when present: if the first attempt actually
      // succeeded server-side but the response never reached us, the
      // backend's cache will return that same response without
      // running the pipeline again. Fall back to a fresh UUID for
      // legacy messages (persisted from before requestId was added).
      const retryRequestId = originalFailedMsg?.requestId ?? generateRequestId();

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
          const data = await withRetry(() =>
            sendChatMessage("near me", sessionId, geoResult, retryRequestId),
          );
          removeMessage(searchProgressId);
          if (data.session_id) setSessionId(data.session_id);
          // Retry succeeded — mark the original failed message sent.
          if (originalFailedMsg) {
            updateMessage(originalFailedMsg.id, { status: "sent" });
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
          cacheIfResults(botMsg, "near me");
        } catch (err: unknown) {
          removeMessage(searchProgressId);
          // Network error during retry — queue against the original
          // failed message (if we have one) so its status transitions
          // to "pending" rather than orphaning the old failed state.
          const queueId = originalFailedMsg?.id ?? nextMsgId();
          if (await handleNetworkError(err, queueId, "near me", retryRequestId)) {
            setLoading(false);
            return;
          }
          // Retry failed again — roll status back to "failed" so the
          // warning indicator returns.
          if (originalFailedMsg) {
            updateMessage(originalFailedMsg.id, { status: "failed" });
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

      // Normal retry — API call only, user message is already in chat.
      // retryRequestId was set at the top of retry(); reusing it
      // means the server can dedupe if the original actually
      // succeeded but the response was lost on the way back.
      setLoading(true);
      try {
        const coords = hasCoords ? { latitude: latitude!, longitude: longitude! } : null;
        const data = await withRetry(() =>
          sendChatMessage(originalText, sessionId, coords, retryRequestId),
        );
        if (data.session_id) setSessionId(data.session_id);

        // Retry succeeded — mark the original failed message sent.
        if (originalFailedMsg) {
          updateMessage(originalFailedMsg.id, { status: "sent" });
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
        cacheIfResults(botMsg, originalText);
      } catch (err: unknown) {
        // If retrying while offline, queue instead of showing an error.
        // handleNetworkError will enqueue and transition the status —
        // but it expects the user-message-ID to patch. Pass the
        // original failed message's ID so its status goes to
        // "pending" rather than orphaning the old failed state.
        if (originalFailedMsg) {
          if (await handleNetworkError(err, originalFailedMsg.id, originalText, retryRequestId)) {
            setLoading(false);
            return;
          }
        } else {
          if (await handleNetworkError(err, nextMsgId(), originalText, retryRequestId)) {
            setLoading(false);
            return;
          }
        }

        // Retry failed (non-network) — roll status back to "failed".
        if (originalFailedMsg) {
          updateMessage(originalFailedMsg.id, { status: "failed" });
        }

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
    [sessionId, latitude, longitude, hasCoords, addMessage, updateMessage, removeMessage, setSessionId, setLoading, setError, requestLocation, handleNetworkError],
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
  //
  // Flush coordination uses the module-level `flushInFlight` (see
  // top of file for the rationale). No useRef here.

  const flushQueue = useCallback(async () => {
    if (flushInFlight) return flushInFlight;
    if (!navigator.onLine) return;

    const work = (async () => {
      // Drop expired entries first. If any were reaped, tell the user
      // which ones so they know why nothing happened for them.
      const expired = await reapExpired();
      if (expired.length > 0) {
        // Mark each expired user message as failed so it shows the
        // warning state visually. The bot summary below gives context.
        for (const m of expired) {
          updateMessage(m.id, { status: "failed" });
        }
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

        // Check whether the message is still a valid flush target.
        // Two ways it can drop out between iterations:
        //   1. User cancelled it (message still in chat, status=cancelled).
        //   2. Session reset fired (message removed from chat entirely).
        // In either case, the flush shouldn't proceed — sending would
        // either overwrite a cancellation the user explicitly made, or
        // drop a bot response into the welcome screen with no matching
        // user message. Dequeue defensively (cancel/reset already did
        // this, but the queue IDB write is async — belt + braces).
        {
          const liveMessages = useChatStore.getState().messages;
          const liveMsg = liveMessages.find((m) => m.id === queued.id);
          if (!liveMsg || liveMsg.status === "cancelled") {
            await dequeueMessage(queued.id);
            continue;
          }
        }

        // Transition the message from "pending" (queued, waiting) to
        // "sending" (actively being delivered). Gives the user
        // feedback that their backlog is draining.
        updateMessage(queued.id, { status: "sending" });

        try {
          // Reuse the stable requestId for idempotent retry. If this
          // exact request already succeeded on the server (response
          // lost on the way back), the server returns the cached
          // response instead of running the chatbot again.
          const data = await sendChatMessage(
            queued.text,
            queued.sessionId,
            queued.coords,
            queued.requestId,
          );

          // A second check, now that the response has landed but
          // before we commit the bot reply to the chat. If the user
          // cancelled or reset the chat while the request was in
          // flight, honor that — don't inject a bot response into a
          // conversation that no longer has the originating user
          // message. The server-side work is already done (and
          // cached in idempotency for 60s), so cost is sunk; the
          // UX preserves the user's explicit intent.
          {
            const liveMessages = useChatStore.getState().messages;
            const liveMsg = liveMessages.find((m) => m.id === queued.id);
            if (!liveMsg || liveMsg.status === "cancelled") {
              await dequeueMessage(queued.id);
              continue;
            }
          }

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

          // Success — mark the user message as delivered and remove
          // from the IDB queue so we don't replay.
          updateMessage(queued.id, { status: "sent" });
          await dequeueMessage(queued.id);
        } catch (err: unknown) {
          // Network error again (e.g., connection dropped mid-flush) —
          // roll status back to "pending" so the user sees the message
          // is still waiting, and stop flushing. Will retry on next
          // online event.
          if (isNetworkError(err)) {
            updateMessage(queued.id, { status: "pending" });
            break;
          }

          // Server-side error (auth, rate limit, 5xx) — surface to
          // user and dequeue the message so we don't retry forever.
          // The user sees what failed and can try again manually.
          updateMessage(queued.id, { status: "failed" });
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
    })();

    flushInFlight = work;
    try {
      await work;
    } finally {
      // Clear only if this is still our flush — a new flush could
      // theoretically start after the iteration body completes but
      // before this line runs. Defensive: don't null-out someone
      // else's promise.
      if (flushInFlight === work) {
        flushInFlight = null;
      }
    }
  }, [addMessage, updateMessage, setSessionId]);

  /**
   * Cancel a pending queued message before it's flushed. Removes it
   * from the IDB queue and marks the visible chat message as
   * "cancelled" so the user sees a clear indication their input was
   * discarded. Safe to call on a message that was already sent or
   * already cancelled — the queue dequeue is idempotent and the status
   * transition is a no-op if the message doesn't exist.
   */
  const cancelQueued = useCallback(
    async (msgId: string) => {
      await dequeueMessage(msgId);
      updateMessage(msgId, { status: "cancelled" });
    },
    [updateMessage],
  );

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

  return { messages, isLoading, error, send, retry, submitFeedback, cancelQueued };
}
