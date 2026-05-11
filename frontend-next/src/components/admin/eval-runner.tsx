// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { triggerEvalRun, fetchEvalStatus, uploadEvalReport } from "@/lib/chat/api";
import { SCENARIO_COUNT_APPROX } from "@/lib/admin/eval-dimensions";

/**
 * EvalRunner — controller for the Run Evals + Upload Report flow.
 *
 * Owns: confirmation dialog, scenario count selector, status polling,
 * upload-from-disk path, "Stop watching" escape hatch.
 *
 * Does NOT own: rendering eval results. After a run completes, calls
 * `onComplete()` so the parent can refresh the report from the store.
 *
 * Lives in its own file separate from EvalResults because the two share
 * zero state and have completely different concerns.
 */

interface EvalRunnerProps {
  onComplete: () => void;
}

// Polling safety thresholds. The interval is 2.5s, so:
//   1800 attempts ≈ 75 minutes (full eval is 30–60 min, plenty of headroom)
//   5 consecutive failures ≈ 12.5 seconds of network silence
// The hard cap protects against a backend that hangs in `running:true`
// forever; the consecutive-failure cap protects against the dashboard
// losing the backend (e.g. server restart mid-run) without the spinner
// spinning indefinitely on a zombie status check.
const MAX_POLL_ATTEMPTS = 1800;
const MAX_CONSECUTIVE_FAILURES = 5;
const POLL_INTERVAL_MS = 2500;

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

  // Stop polling and put the runner back into a non-running state, with
  // a final status message. The eval may or may not still be running on
  // the backend — we just stop watching from this client.
  const stopWatching = useCallback(
    (message: string) => {
      stopPolling();
      setRunning(false);
      setStatus(message);
    },
    [stopPolling],
  );

  async function handleRun() {
    setConfirmOpen(false);
    setRunning(true);
    setStatus("Starting eval run…");
    let attempts = 0;
    let consecutiveFailures = 0;
    try {
      const count = scenarioCount ? parseInt(scenarioCount, 10) : undefined;
      const validCount = count != null && Number.isFinite(count) && count > 0 ? count : undefined;
      await triggerEvalRun(validCount);
      pollRef.current = setInterval(async () => {
        attempts += 1;
        if (attempts > MAX_POLL_ATTEMPTS) {
          stopWatching(
            "⚠️ Status check timed out after 75 minutes — eval may still be running on the server. Refresh to check results.",
          );
          return;
        }
        try {
          const s = await fetchEvalStatus();
          consecutiveFailures = 0;
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
          consecutiveFailures += 1;
          if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
            stopWatching(
              "⚠️ Lost connection to server. The eval may still be running — refresh to check.",
            );
          } else {
            setStatus(`Lost connection to server (retrying… ${consecutiveFailures}/${MAX_CONSECUTIVE_FAILURES}).`);
          }
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Unknown error");
      setRunning(false);
    }
  }

  const scenarioLabel = scenarioCount
    ? `${scenarioCount} scenarios`
    : `all scenarios (${SCENARIO_COUNT_APPROX})`;

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
            disabled={running || uploading}
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
              A full run (currently {SCENARIO_COUNT_APPROX} scenarios) typically costs <strong>$15–25</strong> in
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
        <option value="25">25 scenarios</option>
        <option value="50">50 scenarios</option>
        <option value="100">100 scenarios</option>
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

      {running && (
        <button
          onClick={() =>
            stopWatching(
              "Stopped watching status. The eval may still be running on the server.",
            )
          }
          aria-label="Stop polling for eval status"
          className="ml-2 px-2.5 py-1 rounded-md text-xs font-medium text-neutral-500 hover:text-neutral-700 hover:bg-neutral-100 transition"
        >
          Stop watching
        </button>
      )}
    </div>
  );
}
