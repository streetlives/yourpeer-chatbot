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
import { QuickExit } from "./quick-exit";

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
  // Defense-in-depth, because we've seen real user-reproducible
  // "stuck on Loading…" reports after browser back-then-forward
  // navigation that the more theoretically-clean event-based logic
  // didn't fix. We register every recovery path that's cheap, since
  // any one of them might be the one that fires:
  //
  //  (a) Fast-path check on mount — store may already be hydrated
  //      when this component mounts (e.g. remount after navigation
  //      where the store module stayed in memory).
  //  (b) onFinishHydration callback — Zustand's canonical signal,
  //      one-shot. We re-check hasHydrated() once *after* subscribing
  //      to close the race where hydration completes between the
  //      fast-path check and the subscribe.
  //  (c) pageshow listener with event.persisted — bfcache restore.
  //      Browsers vary on whether they preserve React state through
  //      bfcache and whether they re-run effects on restore. Keeping
  //      this listener registered regardless of the fast-path outcome
  //      means we catch the restore even if the original mount hit
  //      the fast path and bailed.
  //  (d) 100ms poll — guarantees recovery from any case where (a)–(c)
  //      missed: a browser quirk, an event that fired before our
  //      listener registered, a storage backend that completes
  //      between checks. Auto-cancels once hydrated. Cost is
  //      negligible; benefit is the "stuck forever" failure mode
  //      can no longer happen.
  //  (e) 2-second hard ceiling — if hydration genuinely failed
  //      (localStorage disabled, quota exceeded, deserialize threw),
  //      render anyway. The store's initial state is the welcome
  //      message; falling through to it is much better UX than
  //      indefinite Loading…
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const flip = () => {
      if (!cancelled) setHydrated(true);
    };
    const checkAndFlip = () => {
      if (useChatStore.persist.hasHydrated()) flip();
    };

    // (a) Fast path
    if (useChatStore.persist.hasHydrated()) {
      flip();
      return;
    }

    // (b) Subscribe before re-checking, so we don't miss the event
    // if it fires between the fast-path check and subscribe.
    const unsub = useChatStore.persist.onFinishHydration(flip);
    if (useChatStore.persist.hasHydrated()) {
      flip();
      unsub();
      return;
    }

    // (c) bfcache restore
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) checkAndFlip();
    };
    window.addEventListener("pageshow", onPageShow);

    // (d) Polling fallback
    const poll = setInterval(() => {
      if (useChatStore.persist.hasHydrated()) {
        flip();
        clearInterval(poll);
      }
    }, 100);

    // (e) Hard ceiling
    const timeout = setTimeout(flip, 2000);

    return () => {
      cancelled = true;
      unsub();
      window.removeEventListener("pageshow", onPageShow);
      clearInterval(poll);
      clearTimeout(timeout);
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
    <div
      // Horizontal padding: max() of the safe-area-inset and the
      // responsive 12px/16px Tailwind would have given us. Doing this
      // via Tailwind arbitrary values (rather than a `style` prop)
      // means the `sm:` cascade still wins above 640px — the previous
      // version used a `style` prop for these and silently overrode
      // sm:px-4, losing 4px of desktop side padding.
      className="flex flex-col max-w-[820px] mx-auto pl-[max(env(safe-area-inset-left,0px),0.75rem)] pr-[max(env(safe-area-inset-right,0px),0.75rem)] sm:pl-[max(env(safe-area-inset-left,0px),1rem)] sm:pr-[max(env(safe-area-inset-right,0px),1rem)] min-h-dvh"
      style={{
        // Vertical safe-area handling: keep these as inline style
        // because there's no responsive breakpoint to compose with —
        // top inset is just the inset, bottom inset is inset + the
        // existing pb-7 (1.75rem) spacing. env() values evaluate to 0
        // on devices without insets, so this is a no-op on a desktop
        // browser or a non-notched phone.
        paddingTop: "env(safe-area-inset-top, 0px)",
        paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 1.75rem)",
      }}
    >
      <div className="flex items-baseline gap-2.5 px-1 pt-5 pb-3.5 flex-wrap">
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
        {/* Header right cluster: ThemeToggle + QuickExit, pushed to
            the right edge by ml-auto. items-baseline on the parent
            keeps the h1 + status aligned; self-center keeps the
            buttons vertically centered to the header row rather
            than inheriting the text baseline. The gap matches the
            inter-element spacing of the rest of the header. */}
        <div className="ml-auto self-center flex items-center gap-2">
          <ThemeToggle />
          <QuickExit />
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
          className="bg-white border border-neutral-200 rounded-2xl min-h-[400px] max-h-[75dvh] overflow-y-auto p-3 sm:p-5 flex flex-col gap-2.5 shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-300/30 dark:bg-neutral-900 dark:border-neutral-800"
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

      <ChatStatus error={error} />

      {/* ChatInput: stay enabled when offline so messages can queue.
          Only disable during active send (isLoading) to prevent
          double-submit. Queue flushing happens in use-chat.ts. */}
      <ChatInput onSend={send} disabled={isLoading} />
    </div>
  );
}
