// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { ChatMessage as ChatMessageType, MessageStatus } from "@/lib/chat/types";
import { ServiceCarousel } from "./service-carousel";
import { ServiceCarouselBoundary } from "./service-carousel-boundary";
import { QuickReplies } from "./quick-replies";

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
        <div className={`${base} text-neutral-400`} aria-label="Sending">
          <Check size={12} aria-hidden="true" />
        </div>
      );
    case "sent":
      return (
        <div className={`${base} text-neutral-500`} aria-label="Sent">
          <CheckCheck size={12} aria-hidden="true" />
        </div>
      );
    case "pending":
      return (
        <div className={`${base} text-amber-700`} aria-label="Waiting to send">
          <Clock size={12} aria-hidden="true" />
          <span>Waiting to send</span>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="ml-1 underline underline-offset-2 hover:text-amber-900 focus:outline-none focus:ring-2 focus:ring-amber-400 rounded"
              aria-label="Cancel this message"
            >
              Cancel
            </button>
          )}
        </div>
      );
    case "failed":
      return (
        <div className={`${base} text-red-700`} aria-label="Not sent">
          <AlertTriangle size={12} aria-hidden="true" />
          <span>Not sent</span>
        </div>
      );
    case "cancelled":
      return (
        <div className={`${base} text-neutral-400`} aria-label="Cancelled">
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
}

export function ChatMessage({ message, onQuickReply, onRetry, onCancel }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isCancelled = message.status === "cancelled";

  // Cancelled messages fade and get a line-through to signal they
  // were pulled back. We deliberately DON'T remove them from the DOM
  // — seeing what you cancelled is part of the audit trail the user
  // needs to trust the queue behavior.
  const cancelledStyle = isCancelled ? "opacity-50 line-through" : "";

  return (
    <>
      <div className={isUser ? "self-end flex flex-col items-end max-w-[82%]" : "self-start max-w-[82%]"}>
        <div
          role={isUser ? "status" : "article"}
          aria-label={isUser ? "You said" : "YourPeer said"}
          className={`px-4 py-3 rounded-2xl text-[0.94rem] leading-relaxed whitespace-pre-wrap animate-in fade-in slide-in-from-bottom-1 ${
            isUser
              ? `bg-amber-300 text-neutral-900 rounded-br-md ${cancelledStyle}`
              : "bg-neutral-100 text-neutral-900 rounded-bl-md"
          }`}
        >
          {isUser ? message.text : stripMarkdown(message.text)}

          {message.retryMessage && onRetry && (
            <button
              onClick={() => onRetry(message.id, message.retryMessage!)}
              aria-label="Retry sending this message"
              className="flex items-center gap-1.5 mt-2 px-3 py-1.5 text-xs font-medium text-amber-800 bg-amber-100 hover:bg-amber-200 rounded-lg transition-colors"
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

      {message.quick_replies && message.quick_replies.length > 0 && (
        <QuickReplies replies={message.quick_replies} onSelect={onQuickReply} />
      )}
    </>
  );
}
