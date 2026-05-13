// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { StatCardDefinition } from "@/lib/admin/stat-card-definitions";

interface StatCardDetailDialogProps {
  card: StatCardDefinition;
  onClose: () => void;
}

/**
 * Explainer dialog for the six Overview stat cards.
 *
 * Visually a near-twin of `MetricDetailDialog` — same Radix Dialog
 * scaffolding, same overlay class, same close-button treatment — so
 * the two surfaces feel like one popup system. The CONTENT is
 * deliberately different: this dialog speaks in plain English (what
 * the number means, how it's computed in one sentence, why it
 * matters), where `MetricDetailDialog` speaks in formal METRICS.md
 * terms (formula, section number, pilot/post-pilot phase).
 *
 * The split is by audience, not by feature:
 *   - Overview is the first surface a new staff member sees. Cards
 *     need to read without prior context.
 *   - Metrics is the deep-dive surface. Rows already assume the
 *     reader knows what "service-intent sessions" means.
 *
 * For users who want the technical version after the plain one, the
 * dialog renders a soft pointer at the bottom — "More on the Metrics
 * tab → Section X" — but it's an offer, not a redirect.
 *
 * The footer hint is text-only by design (no actual link). Clicking
 * the Metrics tab is one nav-strip click away; we don't try to be
 * cleverer than that. A real link out of the dialog would close the
 * dialog and navigate away mid-explanation, which is the wrong
 * affordance — the user came here for the explanation, not to leave.
 */
export function StatCardDetailDialog({
  card,
  onClose,
}: StatCardDetailDialogProps) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-2xl max-w-[560px] w-[90%] max-h-[80vh] overflow-y-auto p-7 z-50 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex justify-between items-center mb-5">
            <Dialog.Title className="text-base font-semibold">
              {card.label}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label="Close stat card detail"
                className="w-8 h-8 rounded-lg border border-neutral-200 bg-neutral-50 text-neutral-400 flex items-center justify-center transition hover:border-red-300 hover:text-red-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-500 dark:hover:border-red-800 dark:hover:text-red-400"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-4">
            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                What it shows
              </div>
              <Dialog.Description className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {card.definition}
              </Dialog.Description>
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                How it&apos;s computed
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {card.howComputed}
              </p>
            </div>

            {/* Technical-detail callout. Rendered in a neutral info
             *  box (not amber) because this is *definitional*
             *  context, not a warning. Placed between "How it's
             *  computed" and "Target" so the structural definition
             *  sits next to the formula rather than as an afterthought
             *  at the end of the dialog. Cards without this field
             *  skip the block entirely. */}
            {card.technicalNote && (
              <div className="bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200 dark:border-neutral-800 rounded-lg px-3.5 py-2.5">
                <div className="text-xs font-semibold text-neutral-600 dark:text-neutral-300 mb-1">
                  {card.technicalNote.label}
                </div>
                <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                  {card.technicalNote.body}
                </p>
              </div>
            )}

            {/* Target row is rendered only when the card has one.
             *  Volume cards like Sessions intentionally have no
             *  target (a session count isn't "good" or "bad" out of
             *  context), so omitting the row is correct — adding a
             *  "—" or "No target" line would be noise. */}
            {card.target && (
              <div>
                <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                  Target
                </div>
                <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                  {card.target}
                </p>
              </div>
            )}

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Why it matters
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {card.whyItMatters}
              </p>
            </div>

            {card.metricsTabReference && (
              <div className="pt-2 mt-2 border-t border-neutral-100 dark:border-neutral-800 text-xs text-neutral-500 dark:text-neutral-400">
                <span className="font-semibold">
                  More on the Metrics tab:
                </span>{" "}
                {card.metricsTabReference}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
