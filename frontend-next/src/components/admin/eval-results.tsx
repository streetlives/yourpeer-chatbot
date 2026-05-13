// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { EvalReport, EvalScenarioResult } from "@/lib/chat/types";
import { StatCard } from "./stat-card";
import { Tooltip } from "./tooltip";
import { DimensionDetailDialog } from "./dimension-detail-dialog";
import { EVAL_DIMENSIONS, DIM_SHORT_LABELS, getDimension, warningBoundFor } from "@/lib/admin/eval-dimensions";
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

/**
 * Discriminated union for the scenario-list filter. Three modes:
 *   - `all`: every scenario in the report
 *   - `failures`: scenarios scoring < 4.0 or erroring (the inverse
 *     of the "passing" definition used in the dashboard summary)
 *   - `category`: scenarios whose `category` matches `name`
 *
 * Kept local to this module — only `EvalResults` consumes it.
 */
type ScenarioFilter =
  | { kind: "all" }
  | { kind: "failures" }
  | { kind: "category"; name: string };

export function EvalResults({ report }: EvalResultsProps) {
  const { summary } = report;

  // Open-dimension state: tracks which dimension's explainer dialog
  // is currently shown, or null when closed. Mirrors the
  // selectedMetric pattern in metrics/page.tsx so the two pages have
  // the same shape of in-component dialog state.
  const [selectedDimension, setSelectedDimension] = useState<EvalDimension | null>(null);

  // Scenario-list filter. Three modes: show all, show only failing
  // scenarios (across all categories), or filter to a specific
  // category. Using a discriminated union — rather than a single
  // `string | null` with sentinels — keeps each mode's payload
  // explicit and makes adding future filter kinds straightforward.
  const [filter, setFilter] = useState<ScenarioFilter>({ kind: "all" });

  // Pre-compute the category histogram so the filter pills can be
  // sorted by frequency (most-populated first) and show counts. We
  // only include categories that actually appear in this report's
  // scenarios — the report's `summary.category_averages` map may
  // include categories with all-errored scenarios that wouldn't be
  // useful as filters. Stable-sorted: equal counts preserve order
  // of first appearance, which keeps the pill row from reshuffling
  // when re-rendering with the same data.
  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of report.scenarios ?? []) {
      if (!s.category) continue;
      counts.set(s.category, (counts.get(s.category) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort(([, a], [, b]) => b - a);
  }, [report.scenarios]);

  // Apply the current filter to the scenario list. The "failures"
  // mode matches the dashboard's pass definition: a scenario is
  // passing iff its average_score >= 4.0 AND it didn't error.
  // Everything else (low scores OR an evaluation error) counts as
  // a failure for triage purposes. This matches the `passingScenarios`
  // computation below so the count on the "All Failures" pill and
  // the count in the summary cards stay aligned.
  const filteredScenarios = useMemo(() => {
    const all = report.scenarios ?? [];
    switch (filter.kind) {
      case "all":
        return all;
      case "failures":
        return all.filter((s) => s.error || s.average_score < 4.0);
      case "category":
        return all.filter((s) => s.category === filter.name);
    }
  }, [report.scenarios, filter]);

  // Group critical failures by scenario name. The raw `critical_failures`
  // list is flat — one entry per failure — and the same scenario can
  // appear multiple times. Grouping lets us collapse the repetition
  // ("shelter_queens_17 · 2 failures") so a long flat list becomes a
  // shorter index of affected scenarios. Map preserves insertion order,
  // so groups appear in the order their first failure was logged
  // (rather than alphabetical or count-sorted) — matches "list order"
  // intuition without rearranging anything the user might be tracking.
  const groupedCriticalFailures = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const cf of report.critical_failures ?? []) {
      const existing = groups.get(cf.scenario);
      if (existing) {
        existing.push(cf.failure);
      } else {
        groups.set(cf.scenario, [cf.failure]);
      }
    }
    return Array.from(groups.entries());
  }, [report.critical_failures]);

  const totalScenarioCount = report.scenarios?.length ?? 0;

  // Auto-expand all scenario cards when a non-"All" filter narrows
  // the list to ≤5 scenarios. At that size the user wants a quick
  // overview rather than having to click each card individually; at
  // larger sizes the expanded view would be unwieldy. Applies to both
  // the per-category filter and the "All Failures" filter — both are
  // "narrow slice" views where the user is reviewing a specific
  // subset and benefits from seeing scores and justifications at a
  // glance. Excluded: the "All" filter, where the list is the full
  // report and the collapsed view is the right default regardless of
  // count.
  const autoExpand =
    filter.kind !== "all" && filteredScenarios.length <= 5;

  // Passing rate: scenarios scoring >= 4.0 average that didn't error.
  // Matches the "scenario passes if its average score across all 11
  // dimensions is ≥4.0" convention used in the eval reports themselves
  // and mirrored in metrics/page.tsx Section 8. Note this is an aggregate
  // threshold, not a per-dimension pass — see audit finding #23 — but
  // staying consistent with the rest of the dashboard is the right call
  // here over diverging on definition.
  //
  // Both numerator and denominator derive from `report.scenarios` (not
  // a mix of that array and `summary.scenarios_evaluated`). The eval
  // script generates both from the same internal `results` list, so
  // they're invariantly equal — but reading them from one source makes
  // the arithmetic obviously consistent and prevents a future drift if
  // either field's definition shifts.
  const scenarioList = report.scenarios ?? [];
  const passingScenarios = scenarioList.filter(
    (s) => s.average_score >= 4.0 && !s.error,
  ).length;
  const totalScenarios = scenarioList.length;
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
        <p className="text-xs text-neutral-400 dark:text-neutral-500 mb-3">
          Click a dimension name to see what the LLM judge measures and how it scores 1–5.
        </p>
        {EVAL_DIMENSIONS.map((dim) => {
          const { key, shortLabel, target, blocker } = dim;
          const d = summary.dimension_averages[key];

          // Missing-dimension placeholder. Rather than returning null
          // (which silently dropped the row and left reviewers unsure
          // whether the dim was excluded, scored zero, or the dashboard
          // was broken), render a muted stub with "no data in this
          // report". Common cause: older report file predates a new
          // dimension being added to the rubric.
          if (!d) {
            return (
              <div
                key={key}
                className="flex items-center gap-3.5 py-2.5 border-b border-neutral-100 last:border-b-0 dark:border-neutral-800"
              >
                <div className="w-[220px] flex-shrink-0 text-sm font-medium text-neutral-400 dark:text-neutral-500">
                  {shortLabel}
                  {blocker && (
                    <span className="ml-1.5 text-[0.65rem] text-red-500 dark:text-red-400 font-semibold opacity-60">
                      BLOCKER
                    </span>
                  )}
                </div>
                <div className="flex-1 text-xs italic text-neutral-400 dark:text-neutral-500">
                  No data in this report
                </div>
                <div className="w-[60px] text-right font-mono font-bold text-sm text-neutral-300 dark:text-neutral-600">
                  —
                </div>
                <div className="w-[80px] text-right text-xs font-semibold text-neutral-400 dark:text-neutral-500">
                  ≥{target}
                </div>
              </div>
            );
          }

          const pct = (d.average / 5) * 100;
          const targetPct = (target / 5) * 100;
          const meetsTarget = d.average >= target;
          // warningBoundFor uses the dimension's explicit warningThreshold
          // when set, otherwise target - 0.3. This replaces the previous
          // hard-coded `target - 0.5` formula, which was calibrated for
          // targets in the 4.0–4.5 range and became too wide once
          // safety-critical targets were tightened to 4.9 — scores in
          // 4.4–4.9 were rendering amber when they represented major
          // regressions from the historical 4.96–4.99 band.
          const warnBound = warningBoundFor(dim);
          const barColor = meetsTarget
            ? "bg-green-500"
            : d.average >= warnBound
              ? "bg-amber-400"
              : "bg-red-500";
          const scoreColor = meetsTarget
            ? "text-green-600"
            : d.average >= warnBound
              ? "text-amber-500"
              : "text-red-600";

          return (
            <div
              key={key}
              className="flex items-center gap-3.5 py-2.5 border-b border-neutral-100 dark:border-neutral-800 last:border-b-0"
            >
              <div className="w-[220px] flex-shrink-0 text-sm font-medium">
                <button
                  type="button"
                  onClick={() => setSelectedDimension(dim)}
                  className="text-left hover:text-amber-600 dark:hover:text-amber-400 focus:text-amber-600 dark:focus:text-amber-400 focus:outline-none focus:underline transition-colors cursor-pointer"
                  aria-label={`Show details for ${shortLabel}`}
                >
                  {shortLabel}
                </button>
                {blocker && (
                  <span className="ml-1.5 text-[0.65rem] text-red-600 dark:text-red-400 font-semibold">
                    BLOCKER
                  </span>
                )}
              </div>
              <div className="flex-1 relative">
                <div className="w-full h-2 bg-neutral-100 dark:bg-neutral-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${barColor}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <Tooltip content={`Target: ${target}/5.0`}>
                  <div
                    className="absolute -top-0.5 h-3 w-0.5 bg-neutral-400 dark:bg-neutral-500 rounded-full"
                    style={{ left: `${targetPct}%` }}
                  />
                </Tooltip>
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
              // Sort alphabetically by category key. Explicit compareFn
              // because the default `.sort()` does string-coercion on
              // [string, number] tuples and accidentally works for keys
              // but would silently sort wrong if anyone refactors to
              // "sort by value" using the default.
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([cat, avg]) => {
                const cls =
                  avg >= 4
                    ? "bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-300"
                    : avg >= 3
                      ? "bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300"
                      : "bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300";
                return (
                  <span
                    key={cat}
                    className={`inline-block px-2.5 py-1 rounded-full text-sm font-semibold ${cls}`}
                  >
                    {formatCategoryLabel(cat)}: {avg.toFixed(1)}
                  </span>
                );
              })}
          </div>
        </div>
      )}

      {/* Critical failures — grouped by scenario. Each group shows
          the scenario name; if it has more than one failure, the
          group is collapsible and the failures are revealed on
          click. With one failure, the failure renders inline (no
          click required). */}
      {groupedCriticalFailures.length > 0 && (
        <div className="mb-7" id="eval-critical-failures">
          <h3 className="text-base font-semibold text-red-600 dark:text-red-400 mb-3">
            ⚠ Critical Failures
          </h3>
          {groupedCriticalFailures.map(([scenario, failures]) => (
            <CriticalFailureGroup
              key={scenario}
              scenario={scenario}
              failures={failures}
            />
          ))}
        </div>
      )}

      {/* Scenario details */}
      <div id="eval-scenario-details">
        <div className="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
          <h3 className="text-base font-semibold">Scenario Details</h3>
          <span
            className="text-sm text-neutral-500 dark:text-neutral-400"
            aria-live="polite"
          >
            {filter.kind === "all"
              ? `${totalScenarioCount} ${totalScenarioCount === 1 ? "scenario" : "scenarios"}`
              : `${filteredScenarios.length} of ${totalScenarioCount} ${totalScenarioCount === 1 ? "scenario" : "scenarios"}`}
          </span>
        </div>

        {/* Filter chips. "All" + "All Failures" are cross-category
            shortcuts; the rest are per-category. We render the row
            whenever there's at least one categorized scenario — that's
            also when the cross-category options are meaningful (a
            report with no categories at all probably means an older
            run, and the filter is then less useful). Buttons with
            aria-pressed since each is a single-select toggle modifying
            the shared scenario list below — not tabs into separate
            panels (which would need role="tablist" + aria-controls +
            a tabpanel for each). */}
        {categoryCounts.length > 0 && (
          <div
            className="flex flex-wrap gap-1.5 mb-4"
            role="group"
            aria-label="Filter scenarios"
          >
            <FilterPill
              active={filter.kind === "all"}
              onClick={() => setFilter({ kind: "all" })}
              label="All"
              count={totalScenarioCount}
            />
            <FilterPill
              active={filter.kind === "failures"}
              onClick={() => setFilter({ kind: "failures" })}
              label="All Failures"
              count={totalScenarioCount - passingScenarios}
            />
            {categoryCounts.map(([cat, count]) => (
              <FilterPill
                key={cat}
                active={filter.kind === "category" && filter.name === cat}
                onClick={() =>
                  setFilter({ kind: "category", name: cat })
                }
                label={formatCategoryLabel(cat)}
                count={count}
              />
            ))}
          </div>
        )}

        {filteredScenarios.length === 0 ? (
          <div className="text-center py-8 text-sm text-neutral-400 dark:text-neutral-500">
            {filter.kind === "failures"
              ? "No failing scenarios — everything is passing."
              : "No scenarios in this category."}
          </div>
        ) : (
          filteredScenarios.map((s) => (
            // Key includes autoExpand so that when the user switches
            // between a small category (defaultOpen=true) and a
            // larger view, the card remounts with the new initial
            // state. React's `useState(defaultOpen)` only honors the
            // initial value at mount — keying on the prop is the
            // idiomatic way to "reset on prop change" without an
            // in-effect setState anti-pattern.
            <ScenarioCard
              key={`${s.name}__${autoExpand ? "open" : "shut"}`}
              scenario={s}
              defaultOpen={autoExpand}
            />
          ))
        )}
      </div>

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

/**
 * One row in the Scenario Details list. Collapsed by default — header
 * shows only emoji + name + score (and a chevron). Click to expand and
 * see the overall notes plus any sub-target dimension justifications.
 *
 * Error scenarios bypass the collapse entirely: the error message IS
 * the content, and there's no useful "expand for more" target. They
 * render as a flat red-bordered card matching the previous behavior.
 *
 * `defaultOpen` only takes effect on mount — used by the parent to
 * request that the card start expanded (e.g., "narrow category,
 * expand the overview" UX). For the value to re-apply when the
 * parent's auto-expand decision flips, the parent must change the
 * card's `key` so React remounts it with fresh `useState`. Pure
 * `useState(defaultOpen)` without a key strategy would only honor
 * the value on the very first mount.
 *
 * Per-card local state (rather than a shared map in the parent) keeps
 * the implementation simple at the cost of losing manual toggles
 * when the parent triggers a remount. That's actually the right UX
 * here — when the filter changes meaningfully, re-syncing to the
 * default is more useful than remembering scattered per-card states.
 */
function ScenarioCard({
  scenario,
  defaultOpen = false,
}: {
  scenario: EvalScenarioResult;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  if (scenario.error) {
    return (
      <div className="bg-white border border-red-200 rounded-lg px-5 py-4 mb-2.5 dark:bg-neutral-900 dark:border-red-900/50">
        <div className="font-semibold text-sm">❌ {scenario.name}</div>
        <div className="text-sm text-red-600 dark:text-red-400 mt-1">
          Error: {scenario.error}
        </div>
      </div>
    );
  }

  const emoji =
    scenario.average_score >= 4 ? "✅" : scenario.average_score >= 3 ? "⚠️" : "❌";
  const scoreColor =
    scenario.average_score >= 4
      ? "text-green-600 dark:text-green-400"
      : scenario.average_score >= 3
        ? "text-amber-500 dark:text-amber-400"
        : "text-red-600 dark:text-red-400";

  return (
    <div className="bg-white border border-neutral-200 rounded-lg mb-2.5 dark:bg-neutral-900 dark:border-neutral-800">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex justify-between items-center px-5 py-3.5 text-left hover:bg-neutral-50 transition-colors rounded-lg dark:hover:bg-neutral-800/60"
      >
        <span className="flex items-center gap-2 min-w-0">
          <ChevronDown
            size={16}
            className={`flex-shrink-0 text-neutral-400 transition-transform duration-200 ${
              open ? "" : "-rotate-90"
            }`}
            aria-hidden="true"
          />
          <span className="font-semibold text-sm truncate">
            {emoji} {scenario.name}
          </span>
        </span>
        <span className={`font-mono font-bold flex-shrink-0 ml-3 ${scoreColor}`}>
          {scenario.average_score.toFixed(1)}/5.0
        </span>
      </button>

      {open && (
        <div className="px-5 pb-4 pt-1 border-t border-neutral-100 dark:border-neutral-800">
          {scenario.overall_notes && (
            <div className="text-sm text-neutral-500 dark:text-neutral-400 mt-2.5">
              {scenario.overall_notes}
            </div>
          )}
          {Object.entries(scenario.scores || {}).map(([dim, d]) => {
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
                className="text-xs text-amber-600 dark:text-amber-400 mt-1.5 pl-3 border-l-2 border-amber-400 dark:border-amber-600"
              >
                {DIM_SHORT_LABELS[dim] || dim}: {d.score}/5 — {d.justification}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * One scenario's worth of critical failures. Two render modes:
 *   - Single failure: flat row, same look as the original flat list —
 *     scenario name in bold, failure text inline. No click needed.
 *   - Multiple failures: collapsible. Header shows scenario name +
 *     failure count, body lists the failures when expanded.
 *
 * Default-collapsed in the multi case to keep the section short by
 * default; clicking the header reveals all failures for that
 * scenario. Per-card local state (same pattern as ScenarioCard above)
 * — no shared map needed.
 *
 * Red tinting reflects the "critical" severity context, consistent
 * with the section header.
 */
function CriticalFailureGroup({
  scenario,
  failures,
}: {
  scenario: string;
  failures: string[];
}) {
  const [open, setOpen] = useState(false);

  if (failures.length === 1) {
    return (
      <div className="bg-red-50 rounded-lg px-3.5 py-2.5 mb-1.5 text-sm dark:bg-red-950/30 dark:text-red-100">
        <strong>{scenario}</strong>: {failures[0]}
      </div>
    );
  }

  return (
    <div className="bg-red-50 rounded-lg mb-1.5 dark:bg-red-950/30">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3.5 py-2.5 text-sm text-left rounded-lg hover:bg-red-100/60 transition-colors dark:text-red-100 dark:hover:bg-red-900/30"
      >
        <ChevronDown
          size={14}
          className={`flex-shrink-0 text-red-600 dark:text-red-400 transition-transform duration-200 ${
            open ? "" : "-rotate-90"
          }`}
          aria-hidden="true"
        />
        <strong className="flex-1 min-w-0 truncate">{scenario}</strong>
        <span className="flex-shrink-0 text-xs text-red-700 dark:text-red-300 tabular-nums">
          {failures.length} failures
        </span>
      </button>
      {open && (
        // pl-9 (36px) aligns the bullets with the start of the scenario
        // name in the header: button px-3.5 (14) + chevron (14) + gap-2
        // (8) = 36px. Without this, bullets would hang to the left of
        // the header text and look unanchored.
        <ul className="text-sm list-disc pl-9 pr-3.5 pb-2.5 pt-0.5 space-y-1 dark:text-red-100">
          {failures.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One pill in the scenario filter row. Single-select semantics: the
 * caller is responsible for clearing other pills when this one is
 * activated. `aria-pressed` reflects the active state.
 *
 * Active styling matches the metrics-sticky-nav active chip: a near-
 * black fill with white text in light mode, true white fill with
 * near-black text in dark mode. Both sit at ~18:1 contrast — far above
 * any threshold and unmistakably "selected". The count number inherits
 * the chip's text color when active so it stays at full contrast;
 * inactive pills get a muted neutral count to keep the label primary.
 */
function FilterPill({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium transition-colors ${
        active
          ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
          : "bg-neutral-100 text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
      }`}
    >
      <span>{label}</span>
      <span
        className={`tabular-nums ${
          active ? "" : "text-neutral-400 dark:text-neutral-500"
        }`}
      >
        {count}
      </span>
    </button>
  );
}

/**
 * Turn a raw category key like `natural_language` into a display label
 * like `Natural language`. Sentence-case (only the first letter
 * capitalized) reads better than Title Case for tag-style labels and
 * matches the project's general label conventions. If the report
 * starts including categories with multi-word names already
 * capitalized, this will lower-case the trailing words — acceptable
 * because every category seen in practice is snake_case.
 */
function formatCategoryLabel(cat: string): string {
  const spaced = cat.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}
