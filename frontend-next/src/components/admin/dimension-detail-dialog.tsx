// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { EvalDimension } from "@/lib/admin/eval-dimensions";

/**
 * DimensionDetailDialog — explainer popup for an eval dimension.
 *
 * Mirrors the MetricDetailDialog structure (same Radix patterns, same
 * spacing, same close-button affordance) so the two dialogs feel
 * consistent. Different content shape: dimensions have anchors and
 * a weight, metrics have a formula and a target string.
 *
 * Source-of-truth for the displayed text is `EVAL_DIMENSIONS` in
 * `lib/admin/eval-dimensions.ts`, which mirrors the LLM-judge rubric
 * in `tests/eval/eval_llm_judge.py`. If a definition reads stale,
 * the rubric is the authoritative copy.
 */

interface DimensionDetailDialogProps {
  dimension: EvalDimension;
  onClose: () => void;
}

export function DimensionDetailDialog({ dimension, onClose }: DimensionDetailDialogProps) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-2xl max-w-[600px] w-[90%] max-h-[85vh] overflow-y-auto p-7 z-50 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex justify-between items-start gap-4 mb-5">
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold">
                {dimension.label}
              </Dialog.Title>
              <div className="flex flex-wrap items-center gap-2 mt-2">
                <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
                  Weight ×{dimension.weight.toFixed(1)}
                </span>
                <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
                  Target ≥ {dimension.target}/5.0
                </span>
                {dimension.blocker && (
                  <span className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">
                    Deploy blocker
                  </span>
                )}
              </div>
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="Close dimension detail"
                className="w-8 h-8 rounded-lg border border-neutral-200 bg-neutral-50 text-neutral-400 flex items-center justify-center transition hover:border-red-300 hover:text-red-500 flex-shrink-0 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-500 dark:hover:border-red-800 dark:hover:text-red-400"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-4">
            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Definition
              </div>
              <Dialog.Description className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {dimension.definition}
              </Dialog.Description>
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                What the judge looks at
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {dimension.whatItMeasures}
              </p>
            </div>

            {dimension.scoreAnchors && dimension.scoreAnchors.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-2">
                  Score anchors (1–5)
                </div>
                <div className="space-y-1.5">
                  {dimension.scoreAnchors.map((anchor) => {
                    const onTarget = anchor.score >= dimension.target;
                    return (
                      <div
                        key={anchor.score}
                        className="flex items-start gap-3 text-sm border border-neutral-200 dark:border-neutral-800 rounded-lg px-3 py-2"
                      >
                        <span
                          className={`font-mono font-bold flex-shrink-0 ${
                            onTarget ? "text-green-600 dark:text-green-400" : "text-neutral-400 dark:text-neutral-500"
                          }`}
                        >
                          {anchor.score}
                        </span>
                        <span className="text-neutral-700 dark:text-neutral-200 leading-relaxed">
                          {anchor.description}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {!dimension.scoreAnchors && (
              <div>
                <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                  Scoring scale
                </div>
                <p className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed">
                  The judge scores 1–5, with 5 being best. The rubric does not specify per-score anchors for this dimension; scoring uses the judge&apos;s holistic interpretation of the definition above.
                </p>
              </div>
            )}

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Why this weight
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {dimension.weightRationale}
              </p>
            </div>

            <div className="bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200 dark:border-neutral-800 rounded-lg px-3.5 py-2.5">
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Source
              </div>
              <p className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed">
                Definitions and anchors are mirrored from the LLM-judge rubric in <code className="font-mono text-xs bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded px-1 py-0.5">tests/eval/eval_llm_judge.py</code>. Targets are owned by this admin UI and may differ slightly from R28+ historical convention.
              </p>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
