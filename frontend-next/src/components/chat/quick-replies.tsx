// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { QuickReply } from "@/lib/chat/types";
import { CallConfirmDialog } from "./call-confirm-dialog";

interface QuickRepliesProps {
  replies: QuickReply[];
  onSelect: (value: string) => void;
  /**
   * Optional element rendered inside the same flex-wrap container
   * as the reply pills, before them. Used to inline the "Helpful?"
   * feedback row so it shares horizontal space with the quick
   * replies and only takes its own row when wrapping is needed.
   * Reduces vertical chrome on mobile.
   */
  leadingSlot?: ReactNode;
}

// Quick reply pill class shared by all three button shapes (regular,
// tel: link, generic href). Padding is responsive:
//   • Mobile: px-3 py-2 (12px / 8px) — tightens horizontal width
//     so 3-4 pills fit on a 360px row without wrapping, and reduces
//     vertical height ~4-5px per row. Combined with the row's gap-2
//     (8px), each pill's effective tap area is still ~44px square
//     (28px content + 8px row gap on the bottom + visual margin
//     above), comfortably at the WCAG 2.5.5 / Apple HIG floor.
//   • Desktop (sm+): px-4 py-2.5 (16px / 10px) — original sizing
//     where viewport space is plentiful and the larger pills feel
//     more substantial.
//
// Border weight is also responsive: 1px on mobile (cleaner at high-
// DPI mobile pixel density), 1.5px on desktop (visual weight).
// 1.5px borders can render unevenly at some zoom levels on lower-
// DPI displays, but desktop displays are largely past that issue
// in 2026.
const btnClass =
  "px-3 py-2 sm:px-4 sm:py-2.5 rounded-full border border-neutral-200 sm:border-[1.5px] bg-white text-neutral-900 text-sm font-medium whitespace-normal sm:whitespace-nowrap text-left transition-all hover:bg-amber-300 hover:border-amber-300 hover:shadow-md hover:-translate-y-px active:translate-y-0 active:scale-[0.97] dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-[rgba(255,213,79,0.75)] dark:hover:border-[rgba(255,213,79,0.75)] dark:hover:text-neutral-900";

export function QuickReplies({ replies, onSelect, leadingSlot }: QuickRepliesProps) {
  const [callConfirm, setCallConfirm] = useState<{ phone: string; label: string } | null>(null);

  // Globally suppress the "Other" welcome quick reply. The label
  // doesn't communicate where it leads, so users avoided it; free-text
  // entry already covers the same ground. Filtering here (instead of
  // only in the frontend's welcome list) catches the case where the
  // backend re-sends the welcome replies from a "Start over" or reset
  // flow via phrase_lists.py.
  const visibleReplies = replies.filter((qr) => qr.value !== "I need other services");
  if (visibleReplies.length === 0 && !leadingSlot) return null;

  return (
    <>
      <div
        role="group"
        aria-label="Quick reply options"
        className="flex flex-wrap items-center gap-2 self-start max-w-full sm:max-w-[92%] animate-in fade-in slide-in-from-bottom-1"
      >
        {leadingSlot}
        {visibleReplies.map((qr, idx) =>
          qr.href?.startsWith("tel:") ? (
            <button
              key={`${idx}-${qr.value}`}
              type="button"
              onClick={() => setCallConfirm({
                phone: qr.href!.replace("tel:", ""),
                label: qr.label,
              })}
              aria-label={qr.label}
              className={btnClass}
            >
              {qr.label}
            </button>
          ) : qr.href ? (
            <a
              key={`${idx}-${qr.value}`}
              href={qr.href}
              aria-label={qr.label}
              className={btnClass + " inline-block text-center no-underline"}
            >
              {qr.label}
            </a>
          ) : (
            <button
              key={`${idx}-${qr.value}`}
              type="button"
              onClick={() => onSelect(qr.value)}
              aria-label={qr.value}
              className={btnClass}
            >
              {qr.label}
            </button>
          ),
        )}
      </div>

      {callConfirm && (
        <CallConfirmDialog
          phone={callConfirm.phone}
          name={callConfirm.label.replace(/^📞\s*/, "").replace(/^Call\s*/i, "").trim() || "this number"}
          onConfirm={() => setCallConfirm(null)}
          onCancel={() => setCallConfirm(null)}
        />
      )}
    </>
  );
}
