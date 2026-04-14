// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { ChatMessage as ChatMessageType, FeedbackRating } from "@/lib/chat/types";
import { ServiceCarousel } from "./service-carousel";
import { ServiceCarouselBoundary } from "./service-carousel-boundary";
import { QuickReplies } from "./quick-replies";

import { RotateCcw } from "lucide-react";

function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/\*/g, "");
}

interface ChatMessageProps {
  message: ChatMessageType;
  onQuickReply: (value: string) => void;
  onFeedback: (rating: FeedbackRating) => void;
  onRetry?: (errorMsgId: string, originalText: string) => void;
}

export function ChatMessage({ message, onQuickReply, onFeedback, onRetry }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <>
      <div
        role={isUser ? "status" : "article"}
        aria-label={isUser ? "You said" : "YourPeer said"}
        className={`max-w-[82%] px-4 py-3 rounded-2xl text-[0.94rem] leading-relaxed whitespace-pre-wrap animate-in fade-in slide-in-from-bottom-1 ${
          isUser
            ? "self-end bg-amber-300 text-neutral-900 rounded-br-md"
            : "self-start bg-neutral-100 text-neutral-900 rounded-bl-md"
        }`}
      >
        {isUser ? message.text : stripMarkdown(message.text)}

        {message.retryMessage && onRetry && (
          <button
            onClick={() => onRetry(message.id, message.retryMessage!)}
            className="flex items-center gap-1.5 mt-2 px-3 py-1.5 text-xs font-medium text-amber-800 bg-amber-100 hover:bg-amber-200 rounded-lg transition-colors"
          >
            <RotateCcw size={12} />
            Retry
          </button>
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
