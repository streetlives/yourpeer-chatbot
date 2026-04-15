// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useCallback } from "react";
import { useAdminStore } from "@/lib/admin/store";
import { EvalRunner, EvalResults } from "@/components/admin/eval-results";
import { EvalSkeleton } from "@/components/admin/loading-skeleton";

export default function EvalsPage() {
  const { evalResults, fetchEvalResults, invalidate } = useAdminStore();

  useEffect(() => {
    fetchEvalResults();
  }, [fetchEvalResults]);

  const onEvalComplete = useCallback(() => {
    invalidate("evalResults");
    fetchEvalResults();
  }, [invalidate, fetchEvalResults]);

  const report = evalResults.data;

  return (
    <>
      <EvalRunner onComplete={onEvalComplete} />

      {evalResults.loading && report === undefined && (
        <EvalSkeleton />
      )}

      {!evalResults.loading && (report === null || report === undefined) && (
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
