// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback } from "react";
import { useAdminStore } from "@/lib/admin/store";
import { useDataSlice } from "@/hooks/use-data-slice";
import { EvalRunner } from "@/components/admin/eval-runner";
import { EvalResults } from "@/components/admin/eval-results";
import { EvalSkeleton } from "@/components/admin/loading-skeleton";

export default function EvalsPage() {
  const slice = useDataSlice("evalResults");
  // We still need direct access to the store actions for reset + manual
  // fetcher invocation (the eval-complete flow is unusual — it discards
  // existing data before refetching, which `refresh()` doesn't do).
  const reset = useAdminStore((s) => s.reset);
  const fetchEvalResults = useAdminStore((s) => s.fetchEvalResults);

  const onEvalComplete = useCallback(() => {
    // Force-reset the slice before refetching. Two reasons:
    //  1. If a fetch is in-flight (loading=true), fetchEvalResults would
    //     see the loading guard and skip, silently discarding the new
    //     upload. Resetting clears loading=false so the fetch proceeds.
    //  2. Clearing data shows the loading skeleton instead of the stale
    //     report from the previous run while the new one downloads.
    reset("evalResults");
    fetchEvalResults().catch(() => {
      // Store sets error=true internally on failure. The catch here just
      // suppresses Next.js's "Cannot read properties of undefined (reading
      // 'payload')" complaint about an uncaught promise.
    });
  }, [reset, fetchEvalResults]);

  const report = slice.data;

  // For the eval slice specifically, `data` carries a meaningful three-way
  // distinction:
  //   - undefined → no fetch has resolved yet (initial mount, or just
  //     reset by onEvalComplete and waiting for the next fetch). Show the
  //     skeleton.
  //   - null → fetch resolved with no report on the backend. Show the
  //     "no evaluation results yet" empty state.
  //   - non-null → render the report.
  //
  // Branching on `loading` alone caused a one-tick empty-state flash
  // during the reset+refetch flow because reset() flips data to undefined
  // synchronously while loading=true doesn't catch up until the next
  // microtask.

  return (
    <>
      <EvalRunner onComplete={onEvalComplete} />

      {/* Terminal error: no report ever loaded and the latest fetch
          failed. Show a clear retry affordance — without it, the page
          shows the skeleton or empty state and looks like nothing
          happened, even though the backend rejected the request. */}
      {slice.error && !slice.hasData && (
        <div className="text-center py-16" role="alert">
          <div className="text-3xl mb-3">⚠️</div>
          <p className="text-neutral-500 dark:text-neutral-400 mb-4">
            Could not load eval results. The server may be unavailable.
          </p>
          <button
            onClick={slice.refresh}
            className="px-3.5 py-1.5 rounded-lg text-sm font-medium border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800/40 transition"
          >
            Retry
          </button>
        </div>
      )}

      {/* Stale-data banner: a previous report is still on screen but the
          latest refresh failed. Mirrors DataPanel's StaleDataBanner
          behavior — keeps the report visible but flags that it's stale. */}
      {slice.error && slice.hasData && report && (
        <div
          role="status"
          aria-live="polite"
          className="bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/50 rounded-lg px-3.5 py-2 mb-3 text-sm text-amber-800 dark:text-amber-200 flex items-center justify-between gap-3"
        >
          <span>Latest refresh failed — showing the previous report.</span>
          <button
            onClick={slice.refresh}
            className="flex-shrink-0 px-2.5 py-0.5 rounded-md text-xs font-semibold bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200 hover:bg-amber-200 dark:hover:bg-amber-900/60 transition"
          >
            Retry
          </button>
        </div>
      )}

      {!slice.error && report === undefined && <EvalSkeleton />}

      {!slice.error && report === null && (
        <div className="text-center py-16 text-neutral-400 dark:text-neutral-500">
          <div className="text-3xl mb-3">🧪</div>
          <p>No evaluation results yet.</p>
          <p className="mt-2 font-mono text-xs text-amber-600 dark:text-amber-400 bg-neutral-50 dark:bg-neutral-800/40 inline-block px-4 py-2 rounded-lg">
            Use the Run Evals button above, upload a local eval_report.json, or run: python
            tests/eval/eval_llm_judge.py --output tests/eval_report.json
          </p>
        </div>
      )}

      {report && <EvalResults report={report} />}
    </>
  );
}
