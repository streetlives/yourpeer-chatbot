// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback } from "react";
import { useAdminStore } from "@/lib/admin/store";
import { useDataSlice } from "@/hooks/use-data-slice";
import { EvalRunner, EvalResults } from "@/components/admin/eval-results";
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

  return (
    <>
      <EvalRunner onComplete={onEvalComplete} />

      {slice.loading && report === undefined && <EvalSkeleton />}

      {!slice.loading && (report === null || report === undefined) && (
        <div className="text-center py-16 text-neutral-400">
          <div className="text-3xl mb-3">🧪</div>
          <p>No evaluation results yet.</p>
          <p className="mt-2 font-mono text-xs text-amber-600 bg-neutral-50 inline-block px-4 py-2 rounded-lg">
            Use the Run Evals button above, upload a local eval_report.json, or run: python
            tests/eval/eval_llm_judge.py --output tests/eval_report.json
          </p>
        </div>
      )}

      {report && <EvalResults report={report} />}
    </>
  );
}
