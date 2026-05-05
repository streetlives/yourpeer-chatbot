// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { triggerEvalRun, fetchEvalStatus, uploadEvalReport } from "@/lib/chat/api";
import type { EvalReport } from "@/lib/chat/types";
import { StatCard } from "./stat-card";
import * as Dialog from "@radix-ui/react-dialog";
import { EVAL_DIMENSIONS, BLOCKER_KEYS, DIM_SHORT_LABELS } from "@/lib/admin/eval-dimensions";

// -- Eval Runner Controls --

interface EvalRunnerProps {
  onComplete: () => void;
}

export function EvalRunner({ onComplete }: EvalRunnerProps) {
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [scenarioCount, setScenarioCount] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  async function handleRun() {
    setConfirmOpen(false);
    setRunning(true);
    setStatus("Starting eval run…");
    try {
      const count = scenarioCount ? parseInt(scenarioCount) : undefined;
      await triggerEvalRun(count);
      pollRef.current = setInterval(async () => {
        try {
          const s = await fetchEvalStatus();
          const progress = s.total ? ` (${s.completed || 0}/${s.total})` : "";
          setStatus((s.message || "") + progress);
          if (!s.running) {
            stopPolling();
            setRunning(false);
            if (s.finished_at) {
              setStatus(`✅ ${s.message}`);
              onComplete();
            } else if (s.message?.startsWith("Error")) {
              setStatus(`❌ ${s.message}`);
            }
          }
        } catch {
          setStatus("Lost connection to server.");
        }
      }, 2500);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Unknown error");
      setRunning(false);
    }
  }

  const scenarioLabel = scenarioCount
    ? `${scenarioCount} scenarios`
    : "all scenarios (~150+)";

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    // Reset input so the same file can be re-selected
    e.target.value = "";

    setUploading(true);
    setStatus("Uploading eval report…");
    try {
      const result = await uploadEvalReport(file);
      setStatus(result.detail || "Upload complete.");
      onComplete();
    } catch (err) {
      setStatus(`Upload failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="flex items-center gap-3 mb-5 flex-wrap">
      <Dialog.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
        <Dialog.Trigger asChild>
          <button
            disabled={running}
            aria-label={running ? "Evaluation running" : "Run evaluation suite"}
            className="px-4 py-2 rounded-lg bg-amber-300 text-neutral-900 font-semibold text-sm transition hover:bg-amber-400 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? "⏳ Running…" : "▶ Run Evals"}
          </button>
        </Dialog.Trigger>

        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/40 z-50" />
          <Dialog.Content
            className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-white rounded-xl shadow-xl w-full max-w-md p-6"
            aria-describedby="eval-confirm-desc"
          >
            <Dialog.Title className="text-lg font-bold text-neutral-900 mb-1">
              Run evaluation suite?
            </Dialog.Title>
            <p id="eval-confirm-desc" className="text-sm text-neutral-500 mb-4">
              This will run <strong>{scenarioLabel}</strong> through the full
              chatbot pipeline and score each one using Claude Opus as a judge.
            </p>

            <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 mb-5 text-sm text-amber-800">
              <strong>Cost warning:</strong> Each scenario makes multiple
              Anthropic API calls (Haiku for conversation, Sonnet for user
              simulation, Opus for judging across 11 dimensions).
              A full run of 167 scenarios typically costs <strong>$15–25</strong> in
              API credits and takes 30–60 minutes. The backend will be under
              heavier load during the run.
            </div>

            <div className="flex justify-end gap-3">
              <Dialog.Close asChild>
                <button className="px-4 py-2 rounded-lg bg-amber-400 text-sm font-semibold text-neutral-900 hover:bg-amber-500 transition">
                  Cancel
                </button>
              </Dialog.Close>
              <button
                onClick={handleRun}
                className="px-4 py-2 rounded-lg text-sm font-medium text-neutral-600 hover:bg-neutral-100 transition"
              >
                Yes, run {scenarioLabel}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <select
        value={scenarioCount}
        onChange={(e) => setScenarioCount(e.target.value)}
        className="bg-white border border-neutral-200 rounded-lg px-2.5 py-1.5 text-sm"
      >
        <option value="">All scenarios</option>
        <option value="5">5 scenarios (quick)</option>
        <option value="10">10 scenarios</option>
        <option value="20">20 scenarios</option>
      </select>

      <button
        onClick={() => fileInputRef.current?.click()}
        disabled={running || uploading}
        aria-label="Upload eval report from local file"
        className="px-4 py-2 rounded-lg border border-neutral-200 bg-white text-neutral-700 font-medium text-sm transition hover:bg-neutral-50 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {uploading ? "⏳ Uploading…" : "📄 Upload Report"}
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json"
        onChange={handleUpload}
        className="hidden"
        aria-hidden="true"
      />

      {status && (
        <span className="text-sm text-neutral-500">{status}</span>
      )}
    </div>
  );
}

// -- Eval Results Display --

interface EvalResultsProps {
  report: EvalReport;
}

export function EvalResults({ report }: EvalResultsProps) {
  const { summary } = report;

  return (
    <>
      {/* Summary cards */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 mb-7">
        <StatCard
          label="Overall Score"
          value={`${summary.overall_average.toFixed(2)} / 5.00`}
          colorClass={
            summary.overall_average >= 4
              ? "text-green-600"
              : summary.overall_average >= 3
                ? "text-amber-500"
                : "text-red-600"
          }
        />
        <StatCard label="Scenarios" value={summary.scenarios_evaluated} colorClass="text-amber-500" />
        <StatCard
          label="Critical Failures"
          value={summary.critical_failure_count}
          colorClass={summary.critical_failure_count > 0 ? "text-red-600" : "text-green-600"}
        />
        <StatCard
          label="Eval Errors"
          value={summary.scenarios_with_errors}
          colorClass={summary.scenarios_with_errors > 0 ? "text-amber-500" : "text-green-600"}
        />
      </div>

      {/* Dimension scores */}
      <div className="mb-7">
        <h3 className="text-base font-semibold mb-4">Dimension Scores</h3>
        {EVAL_DIMENSIONS.map(({ key, shortLabel, target, blocker }) => {
          const d = summary.dimension_averages[key];
          if (!d) return null;
          const pct = (d.average / 5) * 100;
          const targetPct = (target / 5) * 100;
          const meetsTarget = d.average >= target;
          const barColor = meetsTarget
            ? "bg-green-500"
            : d.average >= target - 0.5
              ? "bg-amber-400"
              : "bg-red-500";
          const scoreColor = meetsTarget
            ? "text-green-600"
            : d.average >= target - 0.5
              ? "text-amber-500"
              : "text-red-600";

          return (
            <div
              key={key}
              className="flex items-center gap-3.5 py-2.5 border-b border-neutral-100 last:border-b-0"
            >
              <div className="w-[220px] flex-shrink-0 text-sm font-medium">
                {shortLabel}
                {blocker && (
                  <span className="ml-1.5 text-[0.65rem] text-red-600 font-semibold">
                    BLOCKER
                  </span>
                )}
              </div>
              <div className="flex-1 relative">
                <div className="w-full h-2 bg-neutral-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${barColor}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div
                  className="absolute -top-0.5 h-3 w-0.5 bg-neutral-400 rounded-full"
                  style={{ left: `${targetPct}%` }}
                  title={`Target: ${target}/5.0`}
                />
              </div>
              <div className={`w-[60px] text-right font-mono font-bold text-sm ${scoreColor}`}>
                {d.average.toFixed(2)}
              </div>
              <div className={`w-[80px] text-right text-xs font-semibold ${scoreColor}`}>
                {meetsTarget ? "✓" : "✗"} ≥{target}
              </div>
            </div>
          );
        })}
      </div>

      {/* Category averages */}
      {summary.category_averages && Object.keys(summary.category_averages).length > 0 && (
        <div className="mb-7">
          <h3 className="text-base font-semibold mb-3">Category Averages</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.category_averages)
              .sort()
              .map(([cat, avg]) => {
                const cls =
                  avg >= 4
                    ? "bg-green-50 text-green-600"
                    : avg >= 3
                      ? "bg-amber-50 text-amber-600"
                      : "bg-red-50 text-red-600";
                return (
                  <span
                    key={cat}
                    className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold ${cls}`}
                  >
                    {cat}: {avg.toFixed(1)}
                  </span>
                );
              })}
          </div>
        </div>
      )}

      {/* Critical failures */}
      {report.critical_failures && report.critical_failures.length > 0 && (
        <div className="mb-7">
          <h3 className="text-base font-semibold text-red-600 mb-3">
            ⚠ Critical Failures
          </h3>
          {report.critical_failures.map((cf, i) => (
            <div
              key={i}
              className="bg-red-50 rounded-lg px-3.5 py-2.5 mb-1.5 text-sm"
            >
              <strong>{cf.scenario}</strong>: {cf.failure}
            </div>
          ))}
        </div>
      )}

      {/* Scenario details */}
      <h3 className="text-base font-semibold mb-3">Scenario Details</h3>
      {(report.scenarios || []).map((s, i) => {
        if (s.error) {
          return (
            <div
              key={i}
              className="bg-white border border-red-200 rounded-lg px-5 py-4 mb-2.5"
            >
              <div className="font-semibold text-sm">❌ {s.name}</div>
              <div className="text-sm text-red-600 mt-1">Error: {s.error}</div>
            </div>
          );
        }

        const emoji = s.average_score >= 4 ? "✅" : s.average_score >= 3 ? "⚠️" : "❌";
        const scoreColor =
          s.average_score >= 4
            ? "text-green-600"
            : s.average_score >= 3
              ? "text-amber-500"
              : "text-red-600";

        return (
          <div
            key={i}
            className="bg-white border border-neutral-200 rounded-lg px-5 py-4 mb-2.5"
          >
            <div className="flex justify-between items-center mb-2">
              <span className="font-semibold text-sm">
                {emoji} {s.name}
              </span>
              <span className={`font-mono font-bold ${scoreColor}`}>
                {s.average_score.toFixed(1)}/5.0
              </span>
            </div>
            {s.overall_notes && (
              <div className="text-sm text-neutral-500 mt-1">{s.overall_notes}</div>
            )}
            {Object.entries(s.scores || {}).map(([dim, d]) => {
              if (d.score > 3) return null;
              return (
                <div
                  key={dim}
                  className="text-xs text-amber-600 mt-1.5 pl-3 border-l-2 border-amber-400"
                >
                  {DIM_SHORT_LABELS[dim] || dim}: {d.score}/5 — {d.justification}
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}
