// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useRef } from "react";

interface ReviewDetailDialogProps {
  review: string;
  locationName: string;
  onClose: () => void;
}

/**
 * Modal that shows the full review text when the truncated highlight on
 * a service card is tapped. The card preview only shows ~117 chars; the
 * full text can be much longer, and users were losing trust signal when
 * the most useful part of the review fell past the ellipsis.
 *
 * Mirrors the dialog UX from CallConfirmDialog (modal, focus management,
 * Escape to close, scroll lock, backdrop click) so users have one mental
 * model for chat dialogs across the app.
 */
export function ReviewDetailDialog({ review, locationName, onClose }: ReviewDetailDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  // Focus the close button on mount — keyboard users can dismiss with
  // Enter immediately. (Auto-focusing the dialog body would be hostile
  // to screen reader users who want to hear the heading first.)
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Lock body scroll using the same fixed-position pattern as
  // CallConfirmDialog — preserves iOS Safari scroll position.
  useEffect(() => {
    const scrollY = window.scrollY;
    document.body.style.position = "fixed";
    document.body.style.top = `-${scrollY}px`;
    document.body.style.left = "0";
    document.body.style.right = "0";
    return () => {
      document.body.style.position = "";
      document.body.style.top = "";
      document.body.style.left = "";
      document.body.style.right = "";
      window.scrollTo(0, scrollY);
    };
  }, []);

  // Escape closes; Tab is naturally trapped because there's only one
  // focusable element (the close button).
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-detail-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 animate-in fade-in dark:bg-black/70 px-4"
      onClick={handleBackdropClick}
    >
      <div
        className="bg-white rounded-2xl shadow-xl w-[min(420px,90vw)] max-h-[80vh] flex flex-col p-5 animate-in zoom-in-95 fade-in dark:bg-neutral-900 dark:shadow-2xl dark:ring-1 dark:ring-neutral-800"
      >
        <div className="flex items-start gap-3 mb-3">
          <div className="text-2xl leading-none mt-0.5" aria-hidden="true">💬</div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-neutral-500 uppercase tracking-wide dark:text-neutral-400">
              Review
            </p>
            <p
              id="review-detail-title"
              className="text-sm font-semibold text-neutral-900 truncate dark:text-neutral-100"
            >
              {locationName}
            </p>
          </div>
        </div>

        <div className="overflow-y-auto -mx-1 px-1 mb-4">
          <p className="text-sm text-neutral-700 leading-relaxed italic dark:text-neutral-300 whitespace-pre-wrap">
            {review}
          </p>
        </div>

        <button
          ref={closeRef}
          onClick={onClose}
          className="w-full py-3 rounded-lg border border-neutral-900 bg-neutral-900 text-sm font-semibold text-white transition hover:bg-neutral-700 dark:border-neutral-400 dark:bg-neutral-400 dark:text-neutral-900 dark:hover:bg-neutral-300 dark:hover:border-neutral-300"
        >
          Close
        </button>
      </div>
    </div>
  );
}
