// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState } from "react";
import type { EvalReport } from "@/lib/chat/types";
import { StatCard } from "./stat-card";
import { DimensionDetailDialog } from "./dimension-detail-dialog";
import { EVAL_DIMENSIONS, DIM_SHORT_LABELS, getDimension } from "@/lib/admin/eval-dimensions";
import type { EvalDimension } from "@/lib/admin/eval-dimensions";

/**
 * EvalResults — pure presentation component for an EvalReport.
 *
 * Renders summary cards, per-dimension scores, critical-failure list,
 * category averages, and per-scenario detail. Stateless aside from the
 * report prop.
 *
 * The companion `eval-runner.tsx` owns the interactive run/upload/poll
 * flow. The two share zero state.
 */

interface EvalResultsProps {
  report: EvalReport;
}

export function EvalResults({ report }: EvalResultsProps) {
  const { summary } = report;

  // Open-dimension state: tracks which dimension's explainer dialog
  // is currently shown, or null when closed. Mirrors the
  // selectedMetric pattern in metrics/page.tsx so the two pages have
  // the same shape of in-component dialog state.
  const [selectedDimension, setSelectedDimension] = useState<EvalDimension | null>(null);

  // Passing rate: scenarios scoring >= 4.0 average that didn't error.
  // Matches the "scenario passes if its average score across all 11
  // dimensions is ≥4.0" convention used in the eval reports themselves
  // and mirrored in metrics/page.tsx Section 8. Note this is an aggregate
  // threshold, not a per-dimension pass — see audit finding #23 — but
  // staying consistent with the rest of the dashboard is the right call
  // here over diverging on definition.
  const passingScenarios = (report.scenarios ?? []).filter(
    (s) => s.average_score >= 4.0 && !s.error,
  ).length;
  const totalScenarios = summary.scenarios_evaluated;
  const passingRate = totalScenarios > 0 ? passingScenarios / totalScenarios : null;
  const passingDisplay =
    passingRate != null ? `${(passingRate * 100).toFixed(1)}%` : "—";
  const passingColor =
    passingRate == null
      ? "text-neutral-400"
      : passingRate >= 0.95
        ? "text-green-600"
        : passingRate >= 0.85
          ? "text-amber-500"
          : "text-red-600";

  return (
    <>
      {/* Summary cards. Each card links to its corresponding section
          below — the cards are essentially a table of contents with a
          headline value. The Eval Errors card targets Scenario Details
          because errored scenarios are interleaved there (rendered with
          a red border) rather than in their own section. */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 mb-7">
        <EvalSummaryCard href="#eval-dimension-scores" label="Overall Score, jump to Dimension Scores">
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
        </EvalSummaryCard>
        <EvalSummaryCard href="#eval-scenario-details" label="Scenarios, jump to Scenario Details">
          <StatCard label="Scenarios" value={summary.scenarios_evaluated} colorClass="text-amber-500" />
        </EvalSummaryCard>
        <EvalSummaryCard
          href="#eval-scenario-details"
          label="Passing rate, jump to Scenario Details"
          // Spans 2 columns because this card's note ("N/M ≥ 4.0") is
          // longer than the others' (which have none) and otherwise wraps
          // to a second line, making the card taller than its peers.
          className="sm:col-span-2"
        >
          <StatCard
            label="Passing Rate"
            value={passingDisplay}
            note={
              passingRate != null
                ? `${passingScenarios}/${totalScenarios} ≥ 4.0`
                : null
            }
            colorClass={passingColor}
          />
        </EvalSummaryCard>
        <EvalSummaryCard href="#eval-critical-failures" label="Critical failures, jump to Critical Failures">
          <StatCard
            label="Critical Failures"
            value={summary.critical_failure_count}
            colorClass={summary.critical_failure_count > 0 ? "text-red-600" : "text-green-600"}
          />
        </EvalSummaryCard>
        <EvalSummaryCard href="#eval-scenario-details" label="Eval errors, jump to Scenario Details">
          <StatCard
            label="Eval Errors"
            value={summary.scenarios_with_errors}
            colorClass={summary.scenarios_with_errors > 0 ? "text-amber-500" : "text-green-600"}
          />
        </EvalSummaryCard>
      </div>

      {/* Dimension scores */}
      <div className="mb-7" id="eval-dimension-scores">
        <h3 className="text-base font-semibold mb-4">Dimension Scores</h3>
        <p className="text-xs text-neutral-400 mb-3">
          Click a dimension name to see what the LLM judge measures and how it scores 1–5.
        </p>
        {EVAL_DIMENSIONS.map((dim) => {
          const { key, shortLabel, target, blocker } = dim;
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
                <button
                  type="button"
                  onClick={() => setSelectedDimension(dim)}
                  className="text-left hover:text-amber-600 focus:text-amber-600 focus:outline-none focus:underline transition-colors cursor-pointer"
                  aria-label={`Show details for ${shortLabel}`}
                >
                  {shortLabel}
                </button>
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
        <div className="mb-7" id="eval-critical-failures">
          <h3 className="text-base font-semibold text-red-600 mb-3">
            ⚠ Critical Failures
          </h3>
          {report.critical_failures.map((cf) => (
            <div
              key={`${cf.scenario}|${cf.failure}`}
              className="bg-red-50 rounded-lg px-3.5 py-2.5 mb-1.5 text-sm"
            >
              <strong>{cf.scenario}</strong>: {cf.failure}
            </div>
          ))}
        </div>
      )}

      {/* Scenario details */}
      <div id="eval-scenario-details">
        <h3 className="text-base font-semibold mb-3">Scenario Details</h3>
      </div>
      {(report.scenarios || []).map((s) => {
        if (s.error) {
          return (
            <div
              key={s.name}
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
            key={s.name}
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
              // Show the justification only when this dimension scored
              // *below* its rubric target — those are the ones reviewers
              // want to read. The previous hardcoded `> 3` cutoff hid
              // failures on dimensions whose target is 4.0 or 4.5: a
              // safety_crisis score of 3.5 is failing the rubric (target
              // 4.5) but the old check skipped it. Falling back to 4.0
              // for unknown keys covers any future report dim not yet in
              // EVAL_DIMENSIONS — better to show those than hide them.
              const target = getDimension(dim)?.target ?? 4.0;
              if (d.score >= target) return null;
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

      {selectedDimension && (
        <DimensionDetailDialog
          dimension={selectedDimension}
          onClose={() => setSelectedDimension(null)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Wraps a StatCard in an in-page anchor link so the summary cards act as
 * a clickable table of contents for the sections below. Renders as an
 * `<a href="#anchor-id">`, which uses the browser's native scroll-to-id
 * behavior — no JS scroll handling needed, and it works with the back
 * button to return to the previous position.
 *
 * The `label` is used as the link's accessible name (since the StatCard
 * itself doesn't have one suitable for assistive tech announcing "links
 * to ..."). Hover/focus styles signal interactivity without changing the
 * card's resting visual.
 */
function EvalSummaryCard({
  href,
  label,
  className = "",
  children,
}: {
  href: string;
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      aria-label={label}
      className={`block rounded-lg transition hover:ring-2 hover:ring-amber-300 hover:ring-offset-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-1 ${className}`}
    >
      {children}
    </a>
  );
}
