// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { EvalReport } from "@/lib/chat/types";
import { StatCard } from "./stat-card";
import { EVAL_DIMENSIONS, BLOCKER_KEYS, DIM_SHORT_LABELS } from "@/lib/admin/eval-dimensions";

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
      <h3 className="text-base font-semibold mb-3">Scenario Details</h3>
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
