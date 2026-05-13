// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { MetricDefinition } from "@/lib/admin/metric-definitions";

interface MetricDetailDialogProps {
  metric: MetricDefinition;
  onClose: () => void;
}

export function MetricDetailDialog({ metric, onClose }: MetricDetailDialogProps) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-2xl max-w-[560px] w-[90%] max-h-[80vh] overflow-y-auto p-7 z-50 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex justify-between items-center mb-5">
            <Dialog.Title className="text-base font-semibold text-neutral-900 dark:text-neutral-100">
              {metric.name}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label="Close metric detail"
                className="w-8 h-8 rounded-lg border border-neutral-200 bg-neutral-50 text-neutral-400 flex items-center justify-center transition hover:border-red-300 hover:text-red-500 dark:border-neutral-800 dark:bg-neutral-800 dark:text-neutral-500 dark:hover:border-red-800 dark:hover:text-red-400"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-4">
            <div className="inline-block px-2.5 py-1 rounded-lg text-xs font-semibold bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
              METRICS.md § {metric.section} · {metric.phase}
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Definition
              </div>
              <Dialog.Description className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {metric.definition}
              </Dialog.Description>
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Formula
              </div>
              <p className="text-sm text-neutral-600 dark:text-neutral-300 font-mono bg-neutral-50 dark:bg-neutral-800/40 border border-neutral-200 dark:border-neutral-800 rounded-lg px-3.5 py-2.5 leading-relaxed">
                {metric.formula}
              </p>
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Target
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200">
                {metric.target}
              </p>
            </div>

            <div>
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                Why It Matters
              </div>
              <p className="text-sm text-neutral-700 dark:text-neutral-200 leading-relaxed">
                {metric.rationale}
              </p>
            </div>

            {metric.statusNote && (
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3.5 py-2.5">
                <div className="text-xs font-semibold text-amber-600 dark:text-amber-400 mb-1">
                  Status Note
                </div>
                <p className="text-sm text-amber-800 dark:text-amber-200 leading-relaxed">
                  {metric.statusNote}
                </p>
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
