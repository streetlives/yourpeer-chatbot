// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useRef, useEffect } from "react";
import { ThumbsUp, ThumbsDown } from "lucide-react";
import type { FeedbackRating } from "@/lib/chat/types";

interface FeedbackRowProps {
  onFeedback: (rating: FeedbackRating) => void;
}

export function FeedbackRow({ onFeedback }: FeedbackRowProps) {
  const [submitted, setSubmitted] = useState<FeedbackRating | null>(null);
  const [hidden, setHidden] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  function submit(rating: FeedbackRating) {
    if (submitted) return;
    setSubmitted(rating);
    onFeedback(rating);
    // Auto-hide after a short delay so the thank-you is visible
    timerRef.current = setTimeout(() => setHidden(true), 1500);
  }

  if (hidden) return null;

  const label =
    submitted === "up"
      ? "Thanks! 👍"
      : submitted === "down"
        ? "Thanks 👎"
        : "Helpful?";

  return (
    <div
      role="group"
      aria-label="Rate these results"
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 bg-white border border-neutral-200 rounded-full animate-in fade-in slide-in-from-bottom-1 dark:bg-neutral-900 dark:border-neutral-700"
    >
      <span className="text-[0.7rem] text-neutral-400 dark:text-neutral-500 font-medium">{label}</span>
      {!submitted && (
        <>
          <button
            type="button"
            onClick={() => submit("up")}
            aria-label="Thumbs up"
            className="w-7 h-7 rounded-full border border-neutral-200 bg-white text-neutral-400 flex items-center justify-center transition-all hover:bg-green-50 hover:border-green-300 hover:text-green-600 dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-500 dark:hover:bg-green-950/40 dark:hover:border-green-800 dark:hover:text-green-400"
          >
            <ThumbsUp size={13} />
          </button>
          <button
            type="button"
            onClick={() => submit("down")}
            aria-label="Thumbs down"
            className="w-7 h-7 rounded-full border border-neutral-200 bg-white text-neutral-400 flex items-center justify-center transition-all hover:bg-red-50 hover:border-red-200 hover:text-red-500 dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-500 dark:hover:bg-red-950/40 dark:hover:border-red-900 dark:hover:text-red-400"
          >
            <ThumbsDown size={13} />
          </button>
        </>
      )}
    </div>
  );
}
