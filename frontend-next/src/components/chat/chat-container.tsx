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
import { OfflineBanner } from "./offline-banner";
import { EarlierResultsLink } from "./earlier-results-link";
import { ThemeToggle } from "@/components/theme-toggle";

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
  //
  // Hardened against two failure modes that previously caused a stuck
  // "Loading…" state on back-navigation:
  //
  //  1. Race between hasHydrated() and onFinishHydration: the storage
  //     read can complete in a microtask between the synchronous check
  //     and the subscription. onFinishHydration is one-shot — registering
  //     after hydration completes means the callback never fires.
  //     Mitigation: re-check hasHydrated() once *after* subscribing, and
  //     unsubscribe immediately if it's already done.
  //
  //  2. bfcache restore: some browsers restore the page from bfcache
  //     with React state intact, others with a fresh mount but stale
  //     listeners. The pageshow handler with event.persiseted catches
  //     the restore case and re-checks hydration.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    // Fast path: already hydrated.
    if (useChatStore.persist.hasHydrated()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- Zustand persist hydration check; idempotent and runs once
      setHydrated(true);
      return;
    }
    // Subscribe before re-checking, so we can't miss the event.
    const unsub = useChatStore.persist.onFinishHydration(() => setHydrated(true));
    // Re-check: hydration might have completed between the fast-path
    // check above and the subscribe call. If so, the subscribed
    // callback won't fire, so flip the flag here and unsubscribe.
    if (useChatStore.persist.hasHydrated()) {
      setHydrated(true);
      unsub();
      return;
    }
    // bfcache restore re-check: when the page is restored from the
    // back/forward cache, React state may or may not be preserved. If
    // we end up remounted with hydrated=false but the store is in fact
    // hydrated, this catches it.
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted && useChatStore.persist.hasHydrated()) {
        setHydrated(true);
      }
    };
    window.addEventListener("pageshow", onPageShow);
    return () => {
      unsub();
      window.removeEventListener("pageshow", onPageShow);
    };
  }, []);

  // PWA shortcut / deep-link prefill. Home-screen shortcuts (manifest
  // "shortcuts") land on /chat?prefill=<URL-encoded message> and expect
  // that message to auto-send. Also used by share-target / external
  // links. The ref guards against double-send across re-renders. We
  // wait for hydration so that the send dedup logic and store can see
  // the message arrive in the correct order; if we send before hydration
  // the message appears above any restored session state, which looks
  // wrong.
  //
  // After sending, clear the query param via history.replaceState so a
  // browser refresh doesn't re-trigger the prefill. We use raw History
  // API rather than `next/navigation` useRouter because useSearchParams
  // would require wrapping this client component in Suspense (statically-
  // rendered parent) and we only need a one-shot read on mount. The
  // replaceState approach leaves the tab history untouched.
  const hasPrefilledRef = useRef(false);
  useEffect(() => {
    if (!hydrated) return;
    if (hasPrefilledRef.current) return;
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const prefill = params.get("prefill");
    if (!prefill) return;
    // Length guard — prefill param is user-controllable via URL; cap
    // to the same length the chat input enforces to prevent abuse.
    if (prefill.length > 1000) return;
    hasPrefilledRef.current = true;
    send(prefill);
    // Strip the query param so a reload doesn't re-send. Preserves
    // the pathname so users stay on /chat.
    window.history.replaceState(null, "", window.location.pathname);
  }, [hydrated, send]);

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
      {/* pr-28 reserves horizontal space for the fixed QuickExit
          button (top-right, ~100px wide). Without this, the
          ThemeToggle on the right side of the header sits underneath
          the floating safety button on narrow viewports. */}
      <div className="flex items-baseline gap-2.5 px-1 pt-5 pb-3.5 pr-28">
        <h1 className="text-xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100">
          YourPeer AI Chat
        </h1>
        <span
          title={dotLabel}
          aria-label={dotLabel}
          className={`inline-block w-2 h-2 rounded-full shrink-0 ${dotColor}`}
        />
        <span className="text-sm text-neutral-400 dark:text-neutral-500">
          Find services near you
        </span>
        {/* Push the toggle to the right end of the header row. items-
            baseline on the parent keeps the h1 + status aligned to
            text baseline; the toggle's ml-auto shoves it to the far
            right without changing that baseline. self-center keeps
            the button vertically centered in the header rather than
            inheriting the text baseline (which would half-cut it). */}
        <div className="ml-auto self-center">
          <ThemeToggle />
        </div>
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
          className="mx-1 mb-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700 dark:bg-red-950/50 dark:border-red-900 dark:text-red-300"
        >
          Can&apos;t reach the server right now. Service search is unavailable;
          your messages will send when we&apos;re back up.
        </div>
      )}

      {/* Backend degraded — AI features limited but service search works */}
      {connectionState === "degraded" && (
        <div role="status" className="mx-1 mb-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-700 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300">
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

      {/* Chat area wrapper. The relative positioning here was
          previously needed to anchor a floating bottom-right
          feedback row; the feedback row now renders inline inside
          the latest bot message instead, but the wrapper stays for
          layout consistency. */}
      <div className="relative flex-1">
        <div
          ref={chatRef}
          role="log"
          aria-label="Chat messages"
          aria-live="polite"
          aria-relevant="additions"
          tabIndex={0}
          className="bg-white border border-neutral-200 rounded-2xl min-h-[400px] max-h-[75vh] overflow-y-auto p-5 flex flex-col gap-2.5 shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-300/30 dark:bg-neutral-900 dark:border-neutral-800"
        >
          {!hydrated ? (
            <p className="text-neutral-400 text-sm">Loading…</p>
          ) : (() => {
            // Find the id of the last bot message in the log. Used to
            // mark stateful quick replies (pagination) as live only on
            // that message. Computed once per render rather than per
            // mapped message.
            let latestBotId: string | undefined;
            for (let i = messages.length - 1; i >= 0; i--) {
              if (messages[i].role === "bot") {
                latestBotId = messages[i].id;
                break;
              }
            }
            return messages.map((msg) => (
              <ChatMessageBoundary key={msg.id}>
                <ChatMessage
                  message={msg}
                  onQuickReply={send}
                  onRetry={retry}
                  onCancel={cancelQueued}
                  isLatestBot={msg.id === latestBotId}
                  onFeedback={submitFeedback}
                />
              </ChatMessageBoundary>
            ));
          })()}
        </div>
      </div>

      {connectionState === "degraded" && (
        <div
          role="status"
          className="mx-1 my-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300"
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
