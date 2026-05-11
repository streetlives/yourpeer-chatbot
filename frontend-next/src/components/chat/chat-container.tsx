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

  // Auto-scroll on new messages.
  //
  // Old behavior: always set scrollTop = scrollHeight on every messages
  // change. That worked for short replies but broke two cases:
  //   (1) A bot reply taller than the visible region landed the user at
  //       the END of the reply, missing the beginning. Users had to
  //       scroll up to read what was said — bad UX.
  //   (2) A status update on an existing message (e.g. pending → sent)
  //       triggered an unwanted scroll-to-bottom even though no new
  //       message arrived, jumping the user away from whatever they
  //       were reading mid-scroll.
  //
  // Previous "new behavior" tried to be clever: short bot messages
  // scrolled to bottom, long ones aligned the top. That broke for the
  // most common case — a bot reply with services. The bubble's
  // `data-message-id` wraps only the text bubble (see
  // chat-message.tsx); the carousel is a SIBLING fragment, so
  // node.offsetHeight measured ~60px and the height check failed even
  // though the visible content (bubble + carousel + feedback row) was
  // far taller than the viewport. Scroll-to-bottom then put the user
  // at the bottom of the carousel, hiding the text entirely. Users
  // typed "food in Brooklyn", got a result, and had to scroll up to
  // read what the bot said.
  //
  // Current behavior: track the last message id we've already scrolled
  // for; on a NEW id, anchor the top of that message's bubble to the
  // top of the visible region. Works for both roles:
  //   • Bot: bubble lands at the top; carousel + feedback row flow
  //     down below, naturally inviting downward swipe through cards.
  //   • User: the bubble is the last element in the scroll region, so
  //     "top of bubble at top of viewport" clamps against the
  //     scrollTop ceiling and resolves to the same position as the
  //     previous scroll-to-bottom — message lands just above the input.
  // Status flips on existing messages don't trigger a scroll (the id
  // hasn't changed).
  const lastScrolledIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!chatRef.current || messages.length === 0) return;

    const lastMsg = messages[messages.length - 1];
    if (lastScrolledIdRef.current === lastMsg.id) return; // status flip; no new message
    lastScrolledIdRef.current = lastMsg.id;

    requestAnimationFrame(() => {
      const region = chatRef.current;
      if (!region) return;

      const node = region.querySelector<HTMLElement>(
        `[data-message-id="${lastMsg.id}"]`,
      );
      if (node) {
        // Compute the y-offset of the message within the scroll region
        // using bounding rects rather than offsetTop. offsetTop walks
        // the offsetParent chain, which for these messages may NOT
        // terminate at the scroll region (depends on which ancestors
        // establish a containing block); using rects sidesteps that
        // subtlety entirely.
        //
        // Math: messageRect.top is relative to the viewport.
        // regionRect.top is also relative to the viewport. The
        // message's y-position within the region is therefore
        // (messageRect.top - regionRect.top) + region.scrollTop.
        //
        // The browser clamps `scrollTop` to [0, scrollHeight -
        // clientHeight], so for short last-messages this naturally
        // resolves to scroll-to-bottom (target > max → clamp). For
        // tall messages, the target lands below max and we get true
        // top-anchoring — the desired behavior.
        const regionRect = region.getBoundingClientRect();
        const nodeRect = node.getBoundingClientRect();
        region.scrollTop = (nodeRect.top - regionRect.top) + region.scrollTop;
        return;
      }

      // DOM-node-missing fallback: scroll to bottom. Shouldn't fire
      // in practice (every chat message renders with a
      // data-message-id), but defensive against future regressions.
      region.scrollTop = region.scrollHeight;
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
      //
      // max-w: 1024px on all sizes. Below ~820px viewport the container
      // is naturally capped by viewport width and the value is moot;
      // at 1024+ desktop viewports the wider cap gives the chat panel
      // and the input meaningful breathing room — previously the
      // 820px cap left ~200px of dead whitespace on either side of a
      // standard 1280×720 desktop monitor and clipped the rightmost
      // service card in the results carousel.
      className="flex flex-col max-w-[1024px] mx-auto pl-[max(env(safe-area-inset-left,0px),0.75rem)] pr-[max(env(safe-area-inset-right,0px),0.75rem)] sm:pl-[max(env(safe-area-inset-left,0px),1rem)] sm:pr-[max(env(safe-area-inset-right,0px),1rem)] min-h-dvh"
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
      {/* Header layout — responsive:
       *   • Mobile (< sm, ~640px): two rows.
       *       Row 1: [title] [dot]    [theme] [Leave site]   (right cluster pinned via ml-auto)
       *       Row 2: [subtitle]
       *   • Desktop (sm+): single row.
       *       [title] [dot] [subtitle]    [theme] [Leave site]
       *
       * Mobile vertical spacing is intentionally tight:
       *   • pt-3 pb-2 (was pt-5 pb-3.5): the original padding was
       *     sized for a one-row header. With two rows on mobile,
       *     the same padding compounds with the extra row height
       *     and pushes chat content too far down — wasted real
       *     estate on a screen that's already small. sm: bumps
       *     the padding back up to the original values for desktop.
       *   • gap-0 between rows (was gap-1): the subtitle sits
       *     directly under the title with only the natural line
       *     height as separation. Reads as a sub-header rather
       *     than a separate paragraph.
       *   • text-xs on the subtitle (was text-sm): 12px tag-line
       *     weight rather than 14px sub-header weight, since the
       *     subtitle is supplementary on mobile, not equal-weight
       *     to the title. sm:text-sm restores the original size
       *     on desktop where it sits inline with the title.
       *
       * Why not just flex-wrap the original single row: at narrow
       * widths flex-wrap split the five children arbitrarily, so
       * the right cluster could land on a wrap line BELOW the
       * subtitle. Two parallel renderings of the right cluster
       * (one inside the title row for mobile, one as a sibling
       * for desktop) give deterministic placement at every width.
       * sm:contents on the mobile row dissolves it into the outer
       * flex on desktop so children become direct descendants.
       */}
      <div className="flex flex-col sm:flex-row sm:items-baseline gap-0 sm:gap-2.5 px-1 pt-3 pb-2 sm:pt-5 sm:pb-3.5">
        <div className="flex items-center gap-2.5 sm:contents">
          {/* Title size:
           *   • Base (>= 380px viewport): text-xl, the brand weight
           *     used everywhere else.
           *   • max-[379px] (very small phones, e.g. iPhone 5/SE 1st
           *     gen at 320px, certain budget Android at 360px): drop
           *     to text-base. Combined with the QuickExit padding
           *     reduction at the same breakpoint (see quick-exit.tsx),
           *     this keeps "YourPeer AI Chat" and "Leave site" on a
           *     single row at 320–379px. Above 380px the layout
           *     comfortably fits the full-weight title.
           */}
          <h1 className="text-xl max-[379px]:text-base font-bold tracking-tight text-neutral-900 dark:text-neutral-100">
            YourPeer AI Chat
          </h1>
          <span
            title={dotLabel}
            aria-label={dotLabel}
            className={`inline-block w-2 h-2 rounded-full shrink-0 ${dotColor}`}
          />
          {/* Right cluster on MOBILE only — pinned to the right edge
           * of row 1 via ml-auto. Hidden on desktop; the desktop
           * instance below sits at the trailing edge of the single
           * header row. */}
          <div className="ml-auto flex items-center gap-2 sm:hidden">
            <ThemeToggle />
            <QuickExit />
          </div>
        </div>
        <span className="text-xs sm:text-sm text-neutral-400 dark:text-neutral-500 leading-tight">
          Find services near you
        </span>
        {/* Right cluster on DESKTOP only — sits at the trailing edge
         * of the single header row via sm:ml-auto. Hidden on mobile. */}
        <div className="hidden sm:ml-auto sm:flex items-center gap-2">
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

      {/* Backend degraded — desktop only. On mobile this renders below
          the chat log (closer to the input). See the sm:hidden copy. */}
      {connectionState === "degraded" && (
        <div
          role="status"
          className="hidden sm:block mx-1 mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300"
        >
          {statusDetail.includes("Rate limit")
            ? "Temporarily rate-limited — try again in a moment, or use simple phrases like \u201cfood in Brooklyn\u201d."
            : statusDetail.includes("Anthropic") || statusDetail.includes("API")
              ? "AI features temporarily limited — service search still works."
              : "Running in basic mode — try simple phrases like \u201cfood in Brooklyn\u201d for best results."}
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
      <div className="relative flex-1 min-h-0">
        <div
          ref={chatRef}
          role="log"
          aria-label="Chat messages"
          aria-live="polite"
          aria-relevant="additions"
          tabIndex={0}
          // Height policy:
          //   • Mobile (< sm): no min-height. The chat region uses
          //     `flex-1` from the parent + h-full here to fill the
          //     space the header and ChatInput leave behind. Quick
          //     replies (rendered inside this scroll region) used
          //     to push the region's content past the 400px minimum
          //     and add ~80px of pill height on top, which combined
          //     with the always-on `min-h-[400px]` made the *total*
          //     of header + chat region + input + safe-area exceed
          //     100dvh — the small phantom mobile scroll the user
          //     was reporting on iPhone 12 (844px viewport).
          //   • Desktop (sm+): keep min-h-[400px] so the region
          //     doesn't visually collapse on empty state. Plenty of
          //     viewport on desktop, no overflow risk.
          //
          //
          // max-h-[80dvh]: caps the panel at 80% of viewport height
          // on tall monitors. This is a deliberate point on a
          // tradeoff: the wrapper's `flex-1` reserves all remaining
          // column space (= viewport − header − ChatInput − padding),
          // and the inner panel grows via `h-full` to fill that
          // wrapper. If we remove the cap, the panel fills the full
          // reserved space — which on a 1200px-tall viewport reads as
          // visually overwhelming. If we set the cap too low (e.g.
          // 60dvh), the panel is short but a visible empty gap opens
          // between the panel bottom and the input top, because the
          // wrapper is still `flex-1` and absorbs the slack as dead
          // space inside itself. 80dvh tunes the gap small on
          // typical laptop viewports while keeping the panel from
          // dominating the screen on tall ones. Dial down (75, 70)
          // for a shorter panel + larger gap; dial up (85, 90) for
          // a taller panel + smaller gap.
          className="bg-white border border-neutral-200 rounded-2xl h-full sm:min-h-[400px] max-h-[80dvh] overflow-y-auto p-3 sm:p-5 flex flex-col gap-2.5 shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-300/30 dark:bg-neutral-900 dark:border-neutral-800"
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

      {/* Backend degraded — mobile only. On desktop this renders above
          the chat log. See the hidden sm:block copy above. */}
      {connectionState === "degraded" && (
        <div
          role="status"
          className="sm:hidden mx-1 my-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-700 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300"
        >
          {statusDetail.includes("Rate limit")
            ? "Temporarily rate-limited — try again in a moment, or use simple phrases like \u201cfood in Brooklyn\u201d."
            : statusDetail.includes("Anthropic") || statusDetail.includes("API")
              ? "AI features temporarily limited — service search still works."
              : "Running in basic mode — try simple phrases like \u201cfood in Brooklyn\u201d for best results."}
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
