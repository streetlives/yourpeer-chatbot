// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useRef, useState } from "react";
import { useChat } from "@/hooks/use-chat";
import { useOnlineStatus } from "@/hooks/use-online-status";
import { useOfflineState } from "@/hooks/use-offline-state";
import { useBackendHealth } from "@/hooks/use-backend-health";
import { useChatStore } from "@/lib/chat/store";
import { ChatMessage } from "./chat-message";
import { ChatMessageBoundary } from "./chat-message-boundary";
import { ChatInput } from "./chat-input";
import { ChatStatus } from "./chat-status";
import { FeedbackRow } from "./feedback-row";
import { OfflineBanner } from "./offline-banner";
import { EarlierResultsLink } from "./earlier-results-link";

export function ChatContainer() {
  const { messages, isLoading, error, send, retry, submitFeedback, cancelQueued } = useChat();
  const isOnline = useOnlineStatus();
  const { cacheAge, queueDepth } = useOfflineState();
  const { backendStatus, statusDetail } = useBackendHealth();
  const chatRef = useRef<HTMLDivElement>(null);

  // Session-reset snapshot: if the previous conversation had results
  // and was wiped (by TTL or explicit reset), offer a link to restore
  // them. Subscribed individually so we don't re-render the whole
  // chat log on every store change.
  const lastResultsBeforeReset = useChatStore((s) => s.lastResultsBeforeReset);
  const restoreEarlierResults = useChatStore((s) => s.restoreEarlierResults);
  const dismissEarlierResults = useChatStore((s) => s.dismissEarlierResults);

  // Combine browser online status with backend health into a single state.
  //   "connected" — browser online AND backend healthy
  //   "degraded"  — browser online AND backend up but reduced capability
  //   "offline"   — browser offline OR backend unreachable/unhealthy
  const connectionState = !isOnline
    ? "offline"
    : backendStatus === "unreachable"
      ? "offline"
      : backendStatus;

  // When the user is offline (browser-level), we show the OfflineBanner
  // which handles both the cached-results and no-cache cases. The old
  // red banner was subtractive — it told users "you're offline, nothing
  // works" — but with queued sends + cached results, that messaging is
  // wrong. See PWA PR for design rationale.
  //
  // The amber "backend unreachable" banner (below chat) is still shown
  // when the browser is online but the server can't be reached, since
  // that's a different condition and the existing wording is accurate.
  const showOfflineBanner = !isOnline;
  const showBackendUnreachableBanner =
    isOnline && backendStatus === "unreachable";

  const dotColor = {
    connected: "bg-green-500 animate-glow-pulse",
    degraded: "bg-amber-400 animate-pulse",
    offline: "bg-red-500 animate-pulse",
  }[connectionState];

  const dotLabel = {
    connected: "Connected",
    degraded: statusDetail,
    offline: !isOnline
      ? queueDepth > 0
        ? `Offline — ${queueDepth} message${queueDepth === 1 ? "" : "s"} pending`
        : "Offline"
      : statusDetail,
  }[connectionState];

  // Wait for Zustand persist to finish rehydrating from localStorage.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    if (useChatStore.persist.hasHydrated()) {
      setHydrated(true);
      return;
    }
    const unsub = useChatStore.persist.onFinishHydration(() => setHydrated(true));
    return unsub;
  }, []);

  // Auto-scroll on new messages
  useEffect(() => {
    requestAnimationFrame(() => {
      if (chatRef.current) {
        chatRef.current.scrollTop = chatRef.current.scrollHeight;
      }
    });
  }, [messages]);

  return (
    <div className="flex flex-col max-w-[820px] mx-auto px-4 pb-7 min-h-dvh">
      <div className="flex items-baseline gap-2.5 px-1 pt-5 pb-3.5">
        <h1 className="text-xl font-bold tracking-tight text-neutral-900">
          YourPeer AI Chat
        </h1>
        <span
          title={dotLabel}
          aria-label={dotLabel}
          className={`inline-block w-2 h-2 rounded-full shrink-0 ${dotColor}`}
        />
        <span className="text-sm text-neutral-400">
          Find services near you
        </span>
      </div>

      {/* Offline state — amber banner covers both cached-results and
          no-cache cases. Replaces the old red "nothing works" banner. */}
      {showOfflineBanner && (
        <OfflineBanner
          hasCachedResults={cacheAge !== null}
          queueDepth={queueDepth}
        />
      )}

      {/* Backend unreachable while online — distinct from offline.
          Users can see cached data but can't send new messages;
          queue will hold them until the backend is back. */}
      {showBackendUnreachableBanner && (
        <div
          role="alert"
          className="mx-1 mb-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700"
        >
          Can&apos;t reach the server right now. Service search is unavailable;
          your messages will send when we&apos;re back up.
        </div>
      )}

      {/* Backend degraded — AI features limited but service search works */}
      {connectionState === "degraded" && (
        <div role="status" className="mx-1 mb-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-700">
          {statusDetail.includes("API key")
            ? "Running in basic mode — service search still works."
            : statusDetail.includes("Rate limit")
              ? "Temporarily limited — service search still works."
              : statusDetail.includes("Anthropic") || statusDetail.includes("API")
                ? "AI features temporarily limited — service search still works."
                : "Some features may be limited — service search still works."}
        </div>
      )}

      {/* Session-reset restore link — present when a prior session had
          results and was wiped by TTL or an explicit reset. Lets the
          user pull those results back into view without re-searching. */}
      {lastResultsBeforeReset && (
        <EarlierResultsLink
          snapshot={lastResultsBeforeReset}
          onRestore={restoreEarlierResults}
          onDismiss={dismissEarlierResults}
        />
      )}

      {/* Chat area wrapper — relative for floating feedback positioning */}
      <div className="relative flex-1">
        <div
          ref={chatRef}
          role="log"
          aria-label="Chat messages"
          aria-live="polite"
          aria-relevant="additions"
          tabIndex={0}
          className="bg-white border border-neutral-200 rounded-2xl min-h-[400px] max-h-[75vh] overflow-y-auto p-5 flex flex-col gap-2.5 shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-300/30"
        >
          {!hydrated ? (
            <p className="text-neutral-400 text-sm">Loading…</p>
          ) : (
            messages.map((msg) => (
              <ChatMessageBoundary key={msg.id}>
                <ChatMessage
                  message={msg}
                  onQuickReply={send}
                  onRetry={retry}
                  onCancel={cancelQueued}
                />
              </ChatMessageBoundary>
            ))
          )}
        </div>

        {/* Floating feedback — bottom-right of the chat area.
            Keyed to the latest results message ID so it remounts
            (resetting hidden/submitted state) when new results arrive. */}
        {(() => {
          const lastFeedbackMsg = [...messages].reverse().find((m) => m.showFeedback);
          if (!lastFeedbackMsg) return null;
          // Only show if the last results message is also the last bot message
          const lastBot = [...messages].reverse().find((m) => m.role === "bot");
          if (lastBot?.id !== lastFeedbackMsg.id) return null;
          return (
            <div className="absolute bottom-3 right-3 z-10">
              <FeedbackRow key={lastFeedbackMsg.id} onFeedback={submitFeedback} />
            </div>
          );
        })()}
      </div>

      {connectionState === "degraded" && (
        <div
          role="status"
          className="mx-1 my-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700"
        >
          Running in basic mode — try simple phrases like &ldquo;food in Brooklyn&rdquo; for best results.
        </div>
      )}

      <ChatStatus isLoading={isLoading} error={error} />

      {/* ChatInput: stay enabled when offline so messages can queue.
          Only disable during active send (isLoading) to prevent
          double-submit. Queue flushing happens in use-chat.ts. */}
      <ChatInput onSend={send} disabled={isLoading} />
    </div>
  );
}
