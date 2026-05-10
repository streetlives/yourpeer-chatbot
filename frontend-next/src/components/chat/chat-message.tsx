// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { ChatMessage as ChatMessageType, MessageStatus, FeedbackRating } from "@/lib/chat/types";
import { ServiceCarousel } from "./service-carousel";
import { ServiceCarouselBoundary } from "./service-carousel-boundary";
import { QuickReplies } from "./quick-replies";
import { FeedbackRow } from "./feedback-row";

import { RotateCcw, Clock, Check, CheckCheck, AlertTriangle, X } from "lucide-react";

function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/\*/g, "");
}

/**
 * Renders the per-message delivery indicator (WhatsApp-style ticks).
 *
 * - sending: single faint check, rendered slightly muted
 * - sent: two checks, solid
 * - pending: clock icon with an inline Cancel button (user wants to
 *   abort before the queue flushes)
 * - failed: warning triangle — the error bubble nearby explains why
 * - cancelled: "Cancelled" label; the bubble itself is faded upstream
 *
 * Returns null for statuses that shouldn't render anything
 * (undefined = legacy message without status).
 */
function MessageStatusIndicator({
  status,
  onCancel,
}: {
  status: MessageStatus | undefined;
  onCancel?: () => void;
}) {
  if (!status) return null;

  const base = "flex items-center gap-1 text-[0.7rem] mt-0.5";

  switch (status) {
    case "sending":
      return (
        <div className={`${base} text-neutral-400 dark:text-neutral-500`} aria-label="Sending">
          <Check size={12} aria-hidden="true" />
        </div>
      );
    case "sent":
      return (
        <div className={`${base} text-neutral-500 dark:text-neutral-400`} aria-label="Sent">
          <CheckCheck size={12} aria-hidden="true" />
        </div>
      );
    case "pending":
      return (
        <div className={`${base} text-amber-700 dark:text-amber-400`} aria-label="Waiting to send">
          <Clock size={12} aria-hidden="true" />
          <span>Waiting to send</span>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="ml-1 underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-200 focus:outline-none focus:ring-2 focus:ring-amber-400 rounded"
              aria-label="Cancel this message"
            >
              Cancel
            </button>
          )}
        </div>
      );
    case "failed":
      return (
        <div className={`${base} text-red-700 dark:text-red-400`} aria-label="Not sent">
          <AlertTriangle size={12} aria-hidden="true" />
          <span>Not sent</span>
        </div>
      );
    case "cancelled":
      return (
        <div className={`${base} text-neutral-400 dark:text-neutral-500`} aria-label="Cancelled">
          <X size={12} aria-hidden="true" />
          <span>Cancelled</span>
        </div>
      );
    default:
      return null;
  }
}

interface ChatMessageProps {
  message: ChatMessageType;
  onQuickReply: (value: string) => void;
  onRetry?: (errorMsgId: string, originalText: string) => void;
  /** Cancel a queued message before flush. Only called for messages
   *  with status="pending" — the cancel affordance is hidden otherwise. */
  onCancel?: (msgId: string) => void;
  /** True when this is the most recent bot message. Used to suppress
   *  stateful quick replies (specifically "Show more results") on
   *  older messages — clicking them would be ambiguous because the
   *  pagination cursor has moved past their context. Also gates
   *  showing the inline feedback row, which only makes sense for
   *  the latest results message. */
  isLatestBot?: boolean;
  /** Submit feedback (thumbs up/down) for the latest bot results.
   *  Plumbed down so FeedbackRow can render inline at the end of
   *  the message rather than floating over the chat surface. */
  onFeedback?: (rating: FeedbackRating) => void;
}

export function ChatMessage({ message, onQuickReply, onRetry, onCancel, isLatestBot, onFeedback }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isCancelled = message.status === "cancelled";

  // Cancelled messages fade and get a line-through to signal they
  // were pulled back. We deliberately DON'T remove them from the DOM
  // — seeing what you cancelled is part of the audit trail the user
  // needs to trust the queue behavior.
  const cancelledStyle = isCancelled ? "opacity-50 line-through" : "";

  return (
    <>
      <div
        data-message-id={message.id}
        data-message-role={message.role}
        className={isUser ? "self-end flex flex-col items-end max-w-[82%]" : "self-start max-w-[82%]"}
      >
        <div
          role={isUser ? "status" : "article"}
          aria-label={isUser ? "You said" : "YourPeer said"}
          className={`px-4 py-3 rounded-2xl text-base leading-relaxed whitespace-pre-wrap animate-in fade-in slide-in-from-bottom-1 ${
            isUser
              // User bubble: brand amber. In LIGHT mode, full
              // amber-300 (#FCD34D) — the page is bright white and the
              // bubble needs the saturation to read as a distinct
              // surface. In DARK mode, amber-300 at 75% alpha — the
              // page is near-black (neutral-950 #0a0a0a) and full-
              // saturation yellow becomes a near-spotlight against it,
              // disproportionately drawing the eye and creating a
              // photosensitivity concern for a population with high
              // trauma exposure (where bright high-contrast stimuli
              // can be triggering). The 75% alpha softens the
              // brightness without changing the hue — the bubble
              // still reads as "the YourPeer yellow", just dimmer.
              // Identity-as-color is preserved; brightness shock is
              // not.
              //
              // We can't use Tailwind's `dark:bg-amber-300/75`
              // shorthand because the BASE class needs to be a
              // bg-amber-300 with full opacity for light mode. The
              // dark-mode override uses inline rgba via an arbitrary
              // value to be explicit about what's happening. Note
              // the rgba components match the BRAND amber from
              // tailwind.config.ts (#FFD54F = 255, 213, 79), not
              // Tailwind's default amber-300 (#FCD34D) — Streetlives
              // overrides the amber palette to a slightly warmer
              // yellow. Text color stays neutral-900 in both modes
              // because the 75%-alpha amber is still light enough
              // that dark text hits AAA contrast over the dark page.
              ? `bg-amber-300 dark:bg-[rgba(255,213,79,0.75)] text-neutral-900 rounded-br-md ${cancelledStyle}`
              // Bot bubble: neutral-100 on light page, neutral-700 on
              // dark. neutral-700 (#404040) is a deliberate two-step
              // lift from neutral-950 page background — neutral-800
              // (#262626) was previously used but blends into the
              // dark page on lower-end displays / OLED in low light,
              // making the bubble boundary hard to perceive.
              // dark:text-neutral-100 reverses the near-black default.
              : "bg-neutral-100 text-neutral-900 dark:bg-neutral-700 dark:text-neutral-100 rounded-bl-md"
          }`}
        >
          {isUser ? message.text : stripMarkdown(message.text)}

          {message.retryMessage && onRetry && (
            <button
              onClick={() => onRetry(message.id, message.retryMessage!)}
              aria-label="Retry sending this message"
              className="flex items-center gap-1.5 mt-2 px-3 py-1.5 text-xs font-medium text-amber-800 bg-amber-100 hover:bg-amber-200 dark:text-amber-200 dark:bg-amber-900/40 dark:hover:bg-amber-900/60 rounded-lg transition-colors"
            >
              <RotateCcw size={12} />
              Retry
            </button>
          )}
        </div>

        {/* Status indicator — only for user messages. Renders beneath
            the bubble so it doesn't visually overload short messages. */}
        {isUser && (
          <MessageStatusIndicator
            status={message.status}
            onCancel={
              message.status === "pending" && onCancel
                ? () => onCancel(message.id)
                : undefined
            }
          />
        )}
      </div>

      {message.services && message.services.length > 0 && (
        <ServiceCarouselBoundary>
          <ServiceCarousel services={message.services} />
        </ServiceCarouselBoundary>
      )}

      {/* Combined feedback + quick-replies row.
          The Helpful? thumbs and quick-reply pills share a single
          flex-wrap container so they fit on one line on wide
          screens and only break to multi-line when they don't.
          Cuts the vertical chrome roughly in half on mobile, where
          the cards already take up most of the screen.
          Feedback is gated on isLatestBot + showFeedback so older
          results don't accumulate stale Helpful? prompts. */}
      {(() => {
        const showFeedback =
          isLatestBot &&
          message.role === "bot" &&
          message.showFeedback &&
          !!onFeedback;

        // Drop "Show more results" on stale (non-latest) bot messages.
        // The backend attaches it correctly at the time of response, but
        // it lingers on every prior turn. Once a newer turn arrives the
        // pagination cursor has moved past this message, so clicking it
        // would either re-show already-shown results or do nothing
        // useful — confusing either way.
        const filtered = message.quick_replies
          ? isLatestBot
            ? message.quick_replies
            : message.quick_replies.filter((qr) => qr.value !== "Show more results")
          : [];

        if (filtered.length === 0 && !showFeedback) return null;

        const feedbackEl = showFeedback ? (
          <FeedbackRow key={message.id} onFeedback={onFeedback!} />
        ) : null;

        return (
          <QuickReplies
            replies={filtered}
            onSelect={onQuickReply}
            leadingSlot={feedbackEl}
          />
        );
      })()}
    </>
  );
}
