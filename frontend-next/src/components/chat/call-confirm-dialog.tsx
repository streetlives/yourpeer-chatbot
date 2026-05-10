// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useRef } from "react";
import { Phone } from "lucide-react";
import { formatPhone, phoneToTelHref } from "@/lib/chat/format-phone";

interface CallConfirmDialogProps {
  phone: string;
  name: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Confirmation dialog shown before placing a phone call.
 *
 * Prevents accidental calls when tapping "Call" buttons on mobile —
 * the native dialer opens immediately on tel: links, which can be
 * jarring. This adds a single confirmation step showing the number
 * and who the user is calling.
 */
export function CallConfirmDialog({ phone, name, onConfirm, onCancel }: CallConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const callRef = useRef<HTMLAnchorElement>(null);

  // Focus the cancel button on mount (safer default — don't auto-call)
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Lock body scroll while dialog is open.
  // Using position:fixed preserves scroll position on iOS Safari
  // (overflow:hidden alone causes the page to jump to top).
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

  // Close on Escape + trap focus between Cancel and Call
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCancel();
        return;
      }
      if (e.key === "Tab") {
        const focusable = [cancelRef.current, callRef.current].filter(Boolean) as HTMLElement[];
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  // Close on backdrop click
  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onCancel();
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Call ${name}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 animate-in fade-in dark:bg-black/70"
      onClick={handleBackdropClick}
    >
      <div
        className="bg-white rounded-2xl shadow-xl w-[min(340px,90vw)] p-5 animate-in zoom-in-95 fade-in dark:bg-neutral-900 dark:shadow-2xl dark:ring-1 dark:ring-neutral-800"
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-full bg-neutral-900 flex items-center justify-center shrink-0 dark:bg-neutral-100">
            <Phone className="w-5 h-5 text-white dark:text-neutral-900" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-neutral-900 truncate dark:text-neutral-100">{name}</p>
            <p className="text-sm text-neutral-500 font-mono dark:text-neutral-400">{formatPhone(phone)}</p>
          </div>
        </div>

        <p className="text-sm text-neutral-500 mb-5 dark:text-neutral-400">
          This will start a call to this number. Hours and availability may have changed — if no one answers, try again later.
        </p>

        <div className="flex gap-2">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="flex-1 py-3 rounded-lg border border-neutral-200 bg-white text-sm font-semibold text-neutral-700 transition hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200 dark:hover:bg-neutral-800"
          >
            Cancel
          </button>
          <a
            ref={callRef}
            href={`tel:${phoneToTelHref(phone)}`}
            onClick={onConfirm}
            className="flex-1 py-3 rounded-lg border border-neutral-900 bg-neutral-900 text-center text-sm font-semibold text-white transition hover:bg-neutral-700 dark:border-neutral-400 dark:bg-neutral-400 dark:text-neutral-900 dark:hover:bg-neutral-300 dark:hover:border-neutral-300"
          >
            Call
          </a>
        </div>
      </div>
    </div>
  );
}
