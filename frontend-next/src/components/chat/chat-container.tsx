// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useRef, useState } from "react";
import { useChat } from "@/hooks/use-chat";
import { useOnlineStatus } from "@/hooks/use-online-status";
import { useBackendHealth } from "@/hooks/use-backend-health";
import { useChatStore } from "@/lib/chat/store";
import { ChatMessage } from "./chat-message";
import { ChatMessageBoundary } from "./chat-message-boundary";
import { ChatInput } from "./chat-input";
import { ChatStatus } from "./chat-status";
import { FeedbackRow } from "./feedback-row";

export function ChatContainer() {
  const { messages, isLoading, error, send, retry, submitFeedback } = useChat();
  const isOnline = useOnlineStatus();
  const { backendStatus, statusDetail } = useBackendHealth();
  const chatRef = useRef<HTMLDivElement>(null);

  // Combine browser online status with backend health into a single state.
  //   "connected" — browser online AND backend healthy
  //   "degraded"  — browser online AND backend up but reduced capability
  //   "offline"   — browser offline OR backend unreachable/unhealthy
  const connectionState = !isOnline
    ? "offline"
    : backendStatus === "unreachable"
      ? "offline"
      : backendStatus;

  const dotColor = {
    connected: "bg-green-500 animate-glow-pulse",
    degraded: "bg-amber-400 animate-pulse",
    offline: "bg-red-500 animate-pulse",
  }[connectionState];

  const dotLabel = {
    connected: "Connected",
    degraded: statusDetail,
    offline: !isOnline ? "Offline" : statusDetail,
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
                  onFeedback={submitFeedback}
                  onRetry={retry}
                />
              </ChatMessageBoundary>
            ))
          )}
        </div>

        {/* Floating feedback — bottom-right of the chat area */}
        {messages.some((m) => m.showFeedback) && (
          <div className="absolute bottom-3 right-3 z-10">
            <FeedbackRow onFeedback={submitFeedback} />
          </div>
        )}
      </div>

      {connectionState === "offline" && (
        <div
          role="alert"
          className="mx-1 my-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800"
        >
          {!isOnline
            ? "You appear to be offline. Messages will fail until your connection is restored."
            : "The chat service is temporarily unavailable. Please try again in a moment."}
        </div>
      )}

      {connectionState === "degraded" && (
        <div
          role="status"
          className="mx-1 my-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700"
        >
          Running in basic mode — try simple phrases like &ldquo;food in Brooklyn&rdquo; for best results.
        </div>
      )}

      <ChatStatus isLoading={isLoading} error={error} />

      <ChatInput onSend={send} disabled={isLoading || connectionState === "offline"} />
    </div>
  );
}
