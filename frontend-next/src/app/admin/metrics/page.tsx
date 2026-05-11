// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, useCallback } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useAdminStore } from "@/lib/admin/store";
import { MetricsSection } from "@/components/admin/metrics-section";
import { MetricRow, statusClass, fmtMetric } from "@/components/admin/metric-row";
import { MetricsSkeleton } from "@/components/admin/loading-skeleton";
import { MetricDetailDialog } from "@/components/admin/metric-detail-dialog";
import { MetricsStickyNav } from "@/components/admin/metrics-sticky-nav";
import { findMetricDefinition } from "@/lib/admin/metric-definitions";
import type { MetricDefinition } from "@/lib/admin/metric-definitions";
import {
  IssueFilterContext,
  SectionRegistryContext,
  summariesEqual,
  type IssueFilterMode,
  type SectionRegistry,
  type SectionSummary,
} from "@/lib/admin/metrics-page-helpers";
import { EVAL_DIMENSIONS, warningBoundFor } from "@/lib/admin/eval-dimensions";
import { utcHourToET } from "@/lib/admin/format-time";

// ---------------------------------------------------------------------------
// STATISTICAL HELPERS
// Bimodal distributions are the norm in this dataset (short triage sessions vs.
// long advisory sessions). Mean is misleading; median + low-n awareness are
// the right primitives for the dashboard.
// ---------------------------------------------------------------------------

/** Compute median of a number array. Returns null for empty input. */
function median(nums: number[]): number | null {
  if (nums.length === 0) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

/** Sample size below which percentages and ratios should be treated as
 *  unreliable (n=1 sessions producing 100% rates, etc.) */
const LOW_N_THRESHOLD = 5;
function isLowN(n: number | null | undefined): boolean {
  return n != null && n < LOW_N_THRESHOLD;
}

export default function MetricsPage() {
  const {
    stats: statsSlice,
    conversations: convosSlice,
    queries: queriesSlice,
    evalResults: evalSlice,
    fetchStats,
    fetchConversations,
    fetchQueries,
    fetchEvalResults,
  } = useAdminStore();

  useEffect(() => {
    fetchStats();
    fetchConversations();
    fetchQueries();
    fetchEvalResults();
  }, [fetchStats, fetchConversations, fetchQueries, fetchEvalResults]);

  const [selectedMetric, setSelectedMetric] = useState<MetricDefinition | null>(null);

  // "Show only issues" toggle. When `issues`, every MetricRow with a
  // non-warning/off-target status returns null, and any MetricsSection
  // that empties out drops too — see metrics-page-helpers.tsx.
  const [filterMode, setFilterMode] = useState<IssueFilterMode>("all");

  // Collapsible intro paragraph. Closed by default after first visit
  // would be nicer, but persistence across reloads is overkill for a
  // single paragraph — keep it open initially and let the user fold
  // it once they know the dashboard.
  const [introOpen, setIntroOpen] = useState(true);

  // Section registry: each MetricsSection calls registry.set(id, summary)
  // during its render; useLayoutEffect snapshots the Map to state if
  // the contents changed, and the sticky nav reads from state. See
  // the comment block above SectionRegistry in metrics-page-helpers.tsx
  // for the timing rationale.
  //
  // The registry ALSO holds an imperative "opener" callback per
  // section so the sticky-nav chip can expand a collapsed section
  // when clicked. Each MetricsSection mounts → calls registerOpener
  // → unmounts → cleanup unregisters. Chip click → registry.open(id)
  // → that section's setOpen(true) fires.
  const registryMapRef = useRef<Map<number, SectionSummary>>(new Map());
  const openersRef = useRef<Map<number, () => void>>(new Map());
  const registry = useMemo<SectionRegistry>(
    () => ({
      set: (id, summary) => {
        registryMapRef.current.set(id, summary);
      },
      registerOpener: (id, opener) => {
        openersRef.current.set(id, opener);
        // Cleanup only deletes if the registered opener is still
        // ours — protects against StrictMode's double-invocation,
        // where the cleanup of the first effect call could otherwise
        // wipe out the opener registered by the second call.
        return () => {
          if (openersRef.current.get(id) === opener) {
            openersRef.current.delete(id);
          }
        };
      },
      open: (id) => {
        const opener = openersRef.current.get(id);
        if (opener) {
          opener();
          return true;
        }
        return false;
      },
    }),
    [],
  );
  const [navSummaries, setNavSummaries] = useState<SectionSummary[]>([]);
  // Intentionally NO dependency array: this effect must run after every
  // render because children push to `registryMapRef.current` during
  // their own renders, and we need to snapshot the latest contents
  // into state. The infinite-loop hazard the lint rule warns about
  // is handled by the `summariesEqual` guard: setState only fires
  // when the snapshot actually differs from current state, so the
  // post-setState re-render's effect run finds no change and stops.
  // Adding `[navSummaries]` here would cause the effect to skip
  // subsequent registry updates (e.g. when stats data refreshes and
  // section statuses change but navSummaries hasn't yet been updated).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    const next = Array.from(registryMapRef.current.values()).sort(
      (a, b) => a.id - b.id,
    );
    if (!summariesEqual(navSummaries, next)) {
      setNavSummaries(next);
    }
  });

  // Active-section tracking for the sticky nav. As the user scrolls,
  // the chip matching the section currently under the sticky bar gets
  // a highlighted background.
  //
  // Approach: scroll listener + getBoundingClientRect. For each
  // section in DOM order, we ask "has its top scrolled past the
  // sticky-bar bottom (~44px) plus a small buffer?" The LAST section
  // for which the answer is yes is the one the user is currently
  // reading — its header has scrolled up into / above the sticky bar
  // but the next section's header hasn't yet. This is the classic
  // "scrollspy" pattern and matches user mental model exactly.
  //
  // (Earlier this used IntersectionObserver, but the rootMargin
  // approach had timing issues with the initial state: when section 1
  // sits below the active band on first paint, no observer events fire
  // and the chip remains stuck at the default. Scrollspy avoids that —
  // we compute synchronously from rect positions, no event needed.)
  //
  // Throttled with requestAnimationFrame so a fast scroll fires at
  // most one update per frame. Re-runs when the filter mode changes
  // because hidden sections come and go from the DOM.
  const [activeSectionId, setActiveSectionId] = useState(1);
  useEffect(() => {
    if (!statsSlice.data) return;

    let rafId: number | null = null;
    let lastActive = -1;

    const update = () => {
      rafId = null;
      // Threshold: sticky bar (~44px) plus a small buffer so the chip
      // transitions a hair BEFORE the next section's header has fully
      // tucked under the bar. Feels more responsive than waiting until
      // the moment of crossover.
      const THRESHOLD = 80;
      let active = 1;
      for (let i = 1; i <= 9; i++) {
        const el = document.getElementById(`metrics-section-${i}`);
        if (!el) continue;
        const top = el.getBoundingClientRect().top;
        if (top <= THRESHOLD) {
          active = i;
        }
        // Don't break on first miss: a hidden section earlier in the
        // list (filter "issues only" dropped it) could leave a gap
        // where later sections still need to be checked.
      }

      // End-of-page adjustment. A short trailing section (e.g.
      // section 9, "Closed-loop") can never satisfy `top <= 80`
      // because at maximum scroll, its top is still pinned hundreds
      // of pixels below that threshold — by then the page can't
      // scroll any further. Without this, that section is never
      // marked active even when it's the only content visible.
      // When the document is scrolled to within a pixel of its end
      // (the `-2` tolerates browser sub-pixel rounding), we instead
      // pick the highest-numbered section whose body extends into
      // the viewport at all — i.e. the "last" section the user can
      // actually see right now.
      const scrolled = window.scrollY + window.innerHeight;
      const docHeight = document.documentElement.scrollHeight;
      if (scrolled >= docHeight - 2) {
        for (let i = 9; i >= 1; i--) {
          const el = document.getElementById(`metrics-section-${i}`);
          if (!el) continue;
          if (el.getBoundingClientRect().top < window.innerHeight) {
            active = i;
            break;
          }
        }
      }

      if (active !== lastActive) {
        lastActive = active;
        setActiveSectionId(active);
      }
    };

    const onScroll = () => {
      if (rafId !== null) return;
      rafId = requestAnimationFrame(update);
    };

    update(); // initial pass — also handles deep-linked hash navigation
    window.addEventListener("scroll", onScroll, { passive: true });
    // Resize triggers the same update path: the at-bottom check depends
    // on `window.innerHeight` and `document.scrollHeight`, both of
    // which can change when the window is resized (e.g. dock to half-
    // screen). Without this, a user at page bottom who resizes might
    // see the active chip stuck on the wrong section until they scroll.
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [statsSlice.data, filterMode]);

  const onMetricClick = useCallback((name: string) => {
    const def = findMetricDefinition(name);
    if (def) setSelectedMetric(def);
  }, []);

  const loading = (!statsSlice.data && statsSlice.loading)
    || (convosSlice.data.length === 0 && convosSlice.loading)
    || (queriesSlice.data.length === 0 && queriesSlice.loading);

  // Terminal error: stats never loaded successfully and the latest fetch
  // failed. The page can't render anything useful without stats, so show
  // a full error UI with retry. (Other slices have safer defaults — empty
  // arrays render fine — so they don't need to gate rendering this way.)
  if (!statsSlice.data && statsSlice.error) {
    return (
      <div className="text-center py-16" role="alert">
        <div className="text-3xl mb-3">⚠️</div>
        <p className="text-neutral-500 dark:text-neutral-400 mb-4">
          Could not load metrics. The server may be unavailable.
        </p>
        <button
          onClick={() => {
            fetchStats();
            fetchConversations();
            fetchQueries();
            fetchEvalResults();
          }}
          className="px-3.5 py-1.5 rounded-lg text-sm font-medium border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800/40 transition"
        >
          Retry
        </button>
      </div>
    );
  }

  if (loading || !statsSlice.data) {
    return <MetricsSkeleton />;
  }

  // Non-terminal error: at least stats loaded successfully, but a later
  // refresh failed on one or more slices. Surface a banner so users know
  // some sections may show stale data, but render the page so they can
  // see what's available. Uses the same soft-amber styling as
  // DataPanel's StaleDataBanner.
  const erroredSlices: string[] = [];
  if (statsSlice.error) erroredSlices.push("stats");
  if (convosSlice.error) erroredSlices.push("conversations");
  if (queriesSlice.error) erroredSlices.push("queries");
  if (evalSlice.error) erroredSlices.push("eval results");
  const hasError = erroredSlices.length > 0;

  const stats = statsSlice.data;
  const convos = convosSlice.data;
  const queries = queriesSlice.data;

  // ---------------------------------------------------------------------------
  // DERIVED METRICS
  // ---------------------------------------------------------------------------

  const totalSessions = stats.unique_sessions || 0;
  const totalQueries = stats.total_queries || 0;
  const serviceIntentSessions = stats.service_intent_sessions || 0;

  const sessionsWithQueries = convos.filter((c) => c.services_delivered > 0 || c.queries_executed > 0).length;
  const taskCompletionRate =
    serviceIntentSessions > 0 && sessionsWithQueries > 0
      ? Math.min(sessionsWithQueries / serviceIntentSessions, 1)
      : null;

  const zeroResultQueries = queries.filter((q) => q.result_count === 0).length;
  const noResultRate = queries.length > 0 ? zeroResultQueries / queries.length : null;

  const relaxedRateVal = totalQueries > 0 ? stats.relaxed_query_rate : null;

  const abandonedSessions = convos.filter(
    (c) => c.services_delivered === 0 && c.turn_count > 0,
  ).length;
  const abandonRate = totalSessions > 0 ? abandonedSessions / totalSessions : null;

  const completedConvos = convos.filter((c) => c.services_delivered > 0);
  const completedTurnCounts = completedConvos.map((c) => c.turn_count);
  const avgTurns =
    completedTurnCounts.length > 0
      ? completedTurnCounts.reduce((s, n) => s + n, 0) / completedTurnCounts.length
      : null;
  const medianTurns = median(completedTurnCounts);

  const fbTotal = (stats.feedback_up || 0) + (stats.feedback_down || 0);
  const fbDisplay = fbTotal > 0 ? fmtMetric(stats.feedback_score, true) : null;

  const cb = stats.confirmation_breakdown;
  const cbTotal = cb?.total_actions || 0;

  const routing = stats.routing;
  const totalCategorized = routing?.total_categorized || 0;

  const toneDist = stats.tone_distribution;
  const tones: Record<string, number> = toneDist?.tones || {};
  const toneEntries = Object.entries(tones).sort(([, a], [, b]) => b - a);
  const totalTurnsForToneRate =
    (toneDist?.total_with_tone || 0) + (toneDist?.turns_without_tone || 0);

  const mi = stats.multi_intent;
  const queueOffers = mi?.queue_offers || 0;
  const queueDeclines = mi?.queue_declines || 0;
  const queueAcceptRate =
    queueOffers > 0 ? (queueOffers - queueDeclines) / queueOffers : null;

  const confidence = stats.confidence;
  const recovery = stats.recovery_rates;
  const sessionMetrics = stats.session_metrics;
  const noResultBySvc = stats.no_result_by_service;
  const timeOfDay = stats.time_of_day;
  const postResultsEng = stats.post_results_engagement;
  const geoDemand = stats.geographic_demand;
  const frustTiers = stats.frustration_tiers;
  const sessionDur = stats.session_duration;
  const repRate = stats.repetition_rate;
  const llmMetrics = stats.llm_metrics;

  // Sample size for the Emotional → cascading metrics. With n < 5, the
  // 100%/0% rates that fall out are statistically meaningless.
  const emotionalN = stats.conversation_quality?.emotional_sessions || 0;

  // Tone classifier coverage. When most turns have no tone detected, the
  // derived tone metrics in Section 4 are unreliable — surface that.
  const totalToneClassified = (toneDist?.total_with_tone || 0) + (toneDist?.turns_without_tone || 0);
  const toneClassifierCoverage = totalToneClassified > 0
    ? (toneDist?.total_with_tone || 0) / totalToneClassified
    : null;
  const toneClassifierDegenerate = totalToneClassified >= 50 && toneClassifierCoverage != null && toneClassifierCoverage < 0.05;

  // Crisis-detection callout in Section 3 — pulls forward what's already in
  // Section 6's by_task breakdown so the safety story includes the cost
  // and latency story.
  const crisisTask = llmMetrics?.by_task?.crisis_detection;
  const crisisShareOfCalls = crisisTask && llmMetrics?.total_calls
    ? crisisTask.calls / llmMetrics.total_calls
    : null;

  // Total LLM Calls subtitle — kept coherent with the value cell.
  // Prior bug (May 2026): subtitle gated on avg_calls_per_session,
  // value gated on total_calls. The two could disagree, producing
  // "13" with subtitle "No calls logged yet". Now: if total_calls is
  // 0, say so; otherwise prefer the avg-per-session phrasing (which
  // requires the backend's session_id contextvar fix to be deployed),
  // and fall back to a defensive "X calls (per-session unavailable)"
  // for the rare case where total_calls > 0 but avg is still null
  // (e.g. legacy untagged calls still in the in-memory deque from
  // before the contextvar fix landed).
  const llmTotalCalls = llmMetrics?.total_calls ?? 0;
  const llmAvgPerSession = llmMetrics?.avg_calls_per_session;
  const llmCallsSubtitle =
    llmTotalCalls === 0
      ? "No calls logged yet"
      : llmAvgPerSession != null
        ? `Avg ${llmAvgPerSession} calls/session`
        : `${llmTotalCalls} calls (per-session unavailable)`;

  // Latency p50 / p95 display — format both percentiles separately so
  // the "ms" suffix doesn't get concatenated onto the "n/a" fallback.
  // Prior bug: `${p95 ?? "n/a"}ms` produced the "n/ams" display when
  // the backend correctly returned null for p95 (it does so when
  // len(latencies) < 20 — see audit_log.py:_compute_llm_metrics —
  // because below that sample size the 95th percentile isn't
  // statistically meaningful).
  const llmLatencyValue = (() => {
    if (llmMetrics?.latency_p50_ms == null) return null;
    const p50 = `${llmMetrics.latency_p50_ms}ms`;
    const p95 =
      llmMetrics.latency_p95_ms != null
        ? `${llmMetrics.latency_p95_ms}ms`
        : "n/a";
    return `${p50} / ${p95}`;
  })();

  return (
    <SectionRegistryContext.Provider value={registry}>
      <IssueFilterContext.Provider value={filterMode}>
        <>
          {hasError && (
            <div
              role="status"
              aria-live="polite"
              className="bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/50 rounded-lg px-3.5 py-2 mb-3 text-sm text-amber-800 dark:text-amber-200 flex items-center justify-between gap-3"
            >
              <span>
                Latest refresh failed for {erroredSlices.join(", ")} — some
                sections may show stale data.
              </span>
              <button
                onClick={() => {
                  if (statsSlice.error) fetchStats();
                  if (convosSlice.error) fetchConversations();
                  if (queriesSlice.error) fetchQueries();
                  if (evalSlice.error) fetchEvalResults();
                }}
                className="flex-shrink-0 px-2.5 py-0.5 rounded-md text-xs font-semibold bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200 hover:bg-amber-200 dark:hover:bg-amber-900/60 transition"
              >
                Retry
              </button>
            </div>
          )}

          {/* Sticky in-page section nav. Pinned to the top of the
              scroll region, shows one chip per section with a
              status dot. Always rendered (no loading state) — chips
              fill in their dots after the first render via the
              registry pipeline. */}
          <MetricsStickyNav
            sections={navSummaries}
            activeId={activeSectionId}
            onChipClick={(id) => registry.open(id)}
          />

          {/* Filter toggle row + collapsible intro.
              The toggle is the primary "I'm here to triage" affordance —
              it sits next to the intro disclosure so both controls live
              together at the top of the dashboard. */}
          <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
            <button
              type="button"
              onClick={() => setIntroOpen((v) => !v)}
              aria-expanded={introOpen}
              className="inline-flex items-center gap-1 text-sm font-medium text-neutral-500 hover:text-neutral-700 transition-colors dark:text-neutral-400 dark:hover:text-neutral-200"
            >
              {introOpen ? (
                <ChevronDown size={16} aria-hidden="true" />
              ) : (
                <ChevronRight size={16} aria-hidden="true" />
              )}
              About this dashboard
            </button>

            <label className="inline-flex items-center gap-2 text-sm font-medium text-neutral-600 dark:text-neutral-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={filterMode === "issues"}
                onChange={(e) =>
                  setFilterMode(e.target.checked ? "issues" : "all")
                }
                className="h-4 w-4 rounded border-neutral-300 text-amber-500 dark:text-amber-500 focus:ring-amber-400 dark:border-neutral-600 dark:bg-neutral-800"
              />
              Show only issues
            </label>
          </div>

          {introOpen && (
            <div className="bg-neutral-50 border border-neutral-200 rounded-lg px-3.5 py-2.5 text-sm text-neutral-500 mb-6 dark:bg-neutral-900 dark:border-neutral-800 dark:text-neutral-400">
              Metrics are computed from the audit log. When PILOT_DB_PATH is set, data
              persists across server restarts. <strong>n/a</strong> = not yet measurable.{" "}
              <strong>Click any metric name</strong> for a detailed explanation with
              formula and target rationale from{" "}
              <a
                href="https://github.com/ianlau20/yourpeer-chatbot/blob/main/docs/ops/METRICS.md"
                target="_blank"
                rel="noopener noreferrer"
                className="text-amber-600 dark:text-amber-400 hover:underline"
              >
                METRICS.md
              </a>
              .
            </div>
          )}

          {/* ================================================================= */}
          {/* 1 · IS IT WORKING?  — Leadership dashboard                       */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={1}
            shortLabel="Is it working?"
            title="1 · Is It Working?"
            description="The headline metrics. Is the chatbot completing its core job — helping people find services?"
          >
            <MetricRow onClick={onMetricClick}
              name="Task Completion Rate"
              subtitle="% of service-intent sessions reaching a confirmed query"
              target="≥ 70% (pilot launch)"
              value={fmtMetric(taskCompletionRate, true)}
              status={statusClass(taskCompletionRate, 0.7, "gte", 0.55)}
            />
            <MetricRow onClick={onMetricClick}
              name="Session Abandonment Rate"
              subtitle="% of sessions with no query executed"
              target="≤ 30%"
              value={fmtMetric(abandonRate, true)}
              status={statusClass(abandonRate, 0.3, "lte", 0.45)}
            />
            <MetricRow onClick={onMetricClick}
              name="No-Result Rate"
              subtitle="% of queries returning zero services after relaxed fallback"
              target="≤ 15% overall"
              value={fmtMetric(noResultRate, true)}
              status={statusClass(noResultRate, 0.15, "lte", 0.25)}
            />
            <MetricRow onClick={onMetricClick}
              name="User Feedback Score"
              subtitle={`% of post-result feedback that is positive (${fbTotal} response${fbTotal !== 1 ? "s" : ""} so far)`}
              target="≥ 70% positive"
              value={fbDisplay}
              status={isLowN(fbTotal) ? "no-data" : statusClass(stats.feedback_score, 0.7, "gte", 0.5)}
              statusOverride={isLowN(fbTotal) && fbTotal > 0 ? `n=${fbTotal} (low confidence)` : undefined}
            />
            <MetricRow onClick={onMetricClick}
              name="Escalation Rate"
              subtitle="% of sessions where user requests a human peer navigator"
              target="Baseline tracking only"
              value={fmtMetric(
                totalSessions > 0 ? (stats.total_escalations || 0) / totalSessions : null,
                true,
              )}
              status={totalSessions > 0 ? "tracking" : "no-data"}
            />
            <MetricRow onClick={onMetricClick}
              name="Median Turns (Completed Sessions)"
              subtitle={
                avgTurns != null
                  ? `Median across ${completedTurnCounts.length} completed session${completedTurnCounts.length !== 1 ? "s" : ""} · mean ${avgTurns.toFixed(1)}`
                  : "Needs at least one completed session"
              }
              target="≤ 7 turns end-to-end"
              value={fmtMetric(medianTurns, false, 1)}
              status={statusClass(medianTurns, 7, "lte", 10)}
            />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 2 · ARE THE RESULTS GOOD?  — Data steward focus                   */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={2}
            shortLabel="Results"
            title="2 · Are the Results Good?"
            description="Quality of search results and service data. Informs query template tuning, database coverage, and multi-intent handling."
          >
            {noResultBySvc && Object.keys(noResultBySvc).length > 0 && (
              <MetricRow onClick={onMetricClick}
                name="No-Result by Service"
                subtitle={Object.entries(noResultBySvc as Record<string, { total_queries: number; no_result_rate: number }>)
                  .sort(([, a], [, b]) => b.no_result_rate - a.no_result_rate)
                  .map(([svc, info]) => `${svc}: ${Math.round(info.no_result_rate * 100)}% (${info.total_queries}q)`)
                  .join(" · ")}
                target="≤ 10% for food/shelter"
                value={`${Object.keys(noResultBySvc).length} categories`}
                status="tracking"
              />
            )}
            <MetricRow onClick={onMetricClick}
              name="Relaxed Query Rate"
              subtitle="% of queries that only returned results after relaxing strict filters"
              target="≤ 25%"
              value={fmtMetric(relaxedRateVal, true)}
              status={statusClass(relaxedRateVal, 0.25, "lte", 0.35)}
            />
            <MetricRow onClick={onMetricClick}
              name="Data Freshness Rate"
              subtitle={`% of returned service cards verified within last 90 days (${stats.data_freshness_detail?.cards_served || 0} cards served)`}
              target="≥ 80%"
              value={fmtMetric(stats.data_freshness_rate, true)}
              status={statusClass(stats.data_freshness_rate, 0.8, "gte", 0.6)}
            />
            <MetricRow onClick={onMetricClick} name="Eligibility Fit Rate" subtitle="% of results matching all stated user criteria" target="≥ 95%" value="By design (canary)" status="no-data" phase="Post-pilot" />
            <MetricRow onClick={onMetricClick}
              name="Queue Offers"
              subtitle="Times the bot offered a second service after delivering results"
              target="Baseline tracking"
              value={String(queueOffers)}
              status={queueOffers > 0 ? "tracking" : "no-data"}
              statusOverride={totalSessions > 0 && queueOffers > 0 ? `${Math.round((queueOffers / totalSessions) * 100)}% of sessions` : undefined}
            />
            <MetricRow onClick={onMetricClick}
              name="Queue Accept Rate"
              subtitle="% of queue offers that were accepted (user searched the next service)"
              target="Baseline tracking"
              value={fmtMetric(queueAcceptRate, true)}
              status={queueAcceptRate !== null ? "tracking" : "no-data"}
              statusOverride={queueAcceptRate !== null ? `${Math.round(queueAcceptRate * 100)}% accepted` : undefined}
            />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 3 · IS IT SAFE?  — Safety & privacy                              */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={3}
            shortLabel="Safety"
            title="3 · Is It Safe?"
            description="Crisis detection, privacy, and hallucination metrics. Crisis detection must be 100% — any miss could leave a vulnerable person without resources."
          >
            <MetricRow onClick={onMetricClick}
              name="Crisis Detection Count"
              subtitle={
                stats.total_crises > 0
                  ? `${stats.total_crises} session${stats.total_crises !== 1 ? "s" : ""} where crisis language was detected and resources shown`
                  : "No crises detected this period — confirm via test message that detection is firing"
              }
              target="Target: 100% recall (no missed crises)"
              value={String(stats.total_crises)}
              // Always "tracking" rather than "on-target" / "no-data". A
              // non-zero count doesn't validate 100% recall (it just means
              // detection fired at least once), and zero is ambiguous —
              // could mean no users in crisis (good) or detection broken
              // (bad). The dashboard can't distinguish these without an
              // out-of-band health signal, so calling either branch a "pass"
              // would be misleading. See audit finding #22.
              status="tracking"
            />
            {crisisTask && (
              <MetricRow onClick={onMetricClick}
                name="Crisis Detection Workload"
                subtitle={`${crisisTask.calls.toLocaleString()} LLM call${crisisTask.calls !== 1 ? "s" : ""} on Sonnet · ${crisisTask.avg_latency_ms}ms avg latency · drives most LLM cost and tail latency`}
                target="Watch for cost / latency impact"
                value={crisisShareOfCalls != null ? `${Math.round(crisisShareOfCalls * 100)}% of calls` : null}
                status={crisisShareOfCalls != null && crisisShareOfCalls > 0.5 ? "warning" : "tracking"}
              />
            )}
            <MetricRow onClick={onMetricClick} name="Crisis False Positive Rate" subtitle="% of crisis-flagged sessions that were not genuine crises" target="≤ 5%" value={null} status="no-data" phase="Post-pilot" />
            <MetricRow onClick={onMetricClick} name="PII Leakage Rate" subtitle="% of stored transcripts with detectable PII after redaction" target="0%" value={null} status="no-data" phase="Post-pilot" />
            <MetricRow onClick={onMetricClick} name="Hallucination Rate" subtitle="% of bot responses containing fabricated service data" target="< 1% (structural guarantee)" value="~0% by design" status="on-target" />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 4 · HOW DOES IT FEEL?  — Conversation experience                 */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={4}
            shortLabel="Feel"
            title="4 · How Does It Feel?"
            description="Emotional awareness, tone, frustration handling, and repetition. For this population, even routine interactions carry emotional weight — purely transactional tone is a gap."
          >
            {toneClassifierDegenerate && (
              <div className="bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/50 rounded-lg px-3.5 py-2.5 text-sm text-amber-800 dark:text-amber-200 mb-3">
                <strong>Tone classifier rarely firing</strong> — only {Math.round((toneClassifierCoverage ?? 0) * 100)}% of {totalToneClassified} (total) classified turns have a tone detected. The derived emotional and tone metrics below may be unreliable until classifier coverage improves. Investigate the split classifier&apos;s tone detection logic before drawing conclusions from this section.
              </div>
            )}
            <MetricRow onClick={onMetricClick}
              name="Emotional Detection Rate"
              subtitle={`% of sessions with an emotional turn (${stats.conversation_quality?.emotional_sessions || 0} sessions)`}
              target="Baseline tracking"
              value={fmtMetric(stats.conversation_quality?.emotional_rate ?? null, true)}
              status="tracking"
            />
            <MetricRow onClick={onMetricClick}
              name="Emotional → Escalation Rate"
              subtitle="% of emotional sessions where user subsequently asked for a peer navigator"
              target="Baseline tracking"
              value={fmtMetric(stats.conversation_quality?.emotional_to_escalation ?? null, true)}
              status={isLowN(emotionalN) ? "no-data" : "tracking"}
              statusOverride={isLowN(emotionalN) && emotionalN > 0 ? `n=${emotionalN} (low confidence)` : undefined}
            />
            <MetricRow onClick={onMetricClick}
              name="Emotional → Service Rate"
              subtitle="% of emotional sessions where user eventually reached a service search"
              target="Baseline tracking"
              value={fmtMetric(stats.conversation_quality?.emotional_to_service ?? null, true)}
              status={isLowN(emotionalN) ? "no-data" : "tracking"}
              statusOverride={isLowN(emotionalN) && emotionalN > 0 ? `n=${emotionalN} (low confidence)` : undefined}
            />
            <MetricRow onClick={onMetricClick}
              name="Frustration Tier Distribution"
              subtitle={frustTiers ? `${frustTiers.total_frustrated_sessions} frustrated sessions: T1=${frustTiers.tiers?.tier_1 || 0}, T2=${frustTiers.tiers?.tier_2 || 0}, T3+=${frustTiers.tiers?.tier_3_plus || 0}` : "No frustrated sessions yet"}
              target="Most at T1 (defused)"
              value={fmtMetric(frustTiers?.tier_1_rate ?? null, true)}
              status={frustTiers?.tier_3_plus_rate != null && frustTiers.tier_3_plus_rate > 0.3 ? "warning" : frustTiers?.total_frustrated_sessions ? "tracking" : "no-data"}
              statusOverride={frustTiers?.tier_3_plus_rate != null ? `T3+: ${Math.round(frustTiers.tier_3_plus_rate * 100)}%` : undefined}
            />
            <MetricRow onClick={onMetricClick}
              name="Bot Repetition Rate"
              subtitle={`Sessions where bot gave identical consecutive responses (${repRate?.sessions_with_repetition || 0} sessions)`}
              target="≤ 5%"
              value={fmtMetric(repRate?.repetition_rate ?? null, true)}
              status={statusClass(repRate?.repetition_rate ?? null, 0.05, "lte", 0.15)}
            />
            {toneEntries.length > 0 ? (
              <>
                {toneEntries.slice(0, 6).map(([tone, count]) => {
                  const pct = totalTurnsForToneRate > 0 ? Math.round((count / totalTurnsForToneRate) * 100) : null;
                  return (
                    <MetricRow onClick={onMetricClick}
                      key={tone}
                      name={`Tone: ${tone.charAt(0).toUpperCase() + tone.slice(1)}`}
                      subtitle={`${count} turn${count !== 1 ? "s" : ""} detected`}
                      target="Baseline tracking"
                      value={fmtMetric(
                        totalTurnsForToneRate > 0 ? count / totalTurnsForToneRate : null,
                        true,
                      )}
                      status={pct !== null ? "tracking" : "no-data"}
                      statusOverride={pct !== null ? `${pct}%` : undefined}
                    />
                  );
                })}
                {toneEntries.length > 6 && (
                  // No onClick here: the dynamic name "+ N more tones" doesn't
                  // match any METRIC_DEFINITIONS entry, so a click would be a
                  // silent no-op. The row is informational; tone definitions
                  // live on the individual "Tone: X" rows above which DO have
                  // a dynamic-fallback definition in findMetricDefinition().
                  <MetricRow
                    name={`+ ${toneEntries.length - 6} more tone${toneEntries.length - 6 !== 1 ? "s" : ""}`}
                    subtitle={toneEntries.slice(6).map(([t, c]) => `${t}: ${c}`).join(" · ")}
                    target="—"
                    value={`${toneEntries.slice(6).reduce((s, [, c]) => s + c, 0)} turns`}
                    status="tracking"
                  />
                )}
              </>
            ) : (
              <MetricRow
                name="No tones detected yet"
                subtitle="Tone data populates after the split classifier processes messages"
                target="—"
                value={null}
                status="no-data"
              />
            )}
            <MetricRow onClick={onMetricClick}
              name="Turns Without Tone"
              subtitle="Neutral turns — no emotional tone detected"
              target="—"
              value={`${toneDist?.turns_without_tone || 0} turns`}
              status={totalTurnsForToneRate > 0 ? "tracking" : "no-data"}
              statusOverride={totalTurnsForToneRate > 0 ? `${Math.round(((toneDist?.turns_without_tone || 0) / totalTurnsForToneRate) * 100)}%` : undefined}
            />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 5 · INTAKE & CONFIRMATION FLOW                                   */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={5}
            shortLabel="Intake"
            defaultOpen={false}
            title="5 · Intake & Confirmation Flow"
            description="How well the chatbot collects structured fields and guides users through confirmation. High confirmation rates and low correction rates indicate the slot extractor is working."
          >
            <MetricRow onClick={onMetricClick}
              name="Slot Confirmation Rate"
              subtitle="% of queries that went through the explicit confirmation step"
              target="≥ 90%"
              value={fmtMetric(stats.slot_confirmation_rate, true)}
              status={statusClass(stats.slot_confirmation_rate, 0.9, "gte", 0.8)}
            />
            <MetricRow onClick={onMetricClick}
              name="Slot Correction Rate"
              subtitle="% of sessions where user corrects a slot after confirmation"
              target="≤ 15%"
              value={fmtMetric(stats.slot_correction_rate, true)}
              status={statusClass(stats.slot_correction_rate, 0.15, "lte", 0.25)}
            />
            <MetricRow onClick={onMetricClick}
              name="Confirmation: Confirm Rate"
              subtitle={`% of confirmation actions that are "Yes, search" (${cbTotal} actions)`}
              target="≥ 65% confirm"
              value={fmtMetric(cb?.confirm_rate ?? null, true)}
              status={statusClass(cb?.confirm_rate ?? null, 0.65, "gte", 0.5)}
            />
            <MetricRow onClick={onMetricClick}
              name="Confirmation: Abandon Rate"
              subtitle="% of sessions that reach confirmation but never confirm"
              target="≤ 10%"
              value={fmtMetric(cb?.abandon_rate ?? null, true)}
              status={statusClass(cb?.abandon_rate ?? null, 0.1, "lte", 0.2)}
            />
            <MetricRow onClick={onMetricClick}
              name="Bot Question Rate"
              subtitle={`% of turns asking about bot capabilities (${stats.conversation_quality?.bot_question_turns || 0} turns)`}
              target="Baseline tracking"
              value={fmtMetric(stats.conversation_quality?.bot_question_rate ?? null, true)}
              status="tracking"
            />
            <MetricRow onClick={onMetricClick}
              name="Bot Question → Frustration Rate"
              subtitle="% of bot-question sessions followed by frustration"
              target="≤ 10%"
              value={fmtMetric(stats.conversation_quality?.bot_question_to_frustration ?? null, true)}
              status={statusClass(stats.conversation_quality?.bot_question_to_frustration ?? null, 0.1, "lte", 0.2)}
            />
            <MetricRow onClick={onMetricClick}
              name="Conversational Discovery Rate"
              subtitle={`% of query sessions that included a conversational turn (${stats.conversation_quality?.conversational_discovery || 0} sessions)`}
              target="Baseline tracking"
              value={fmtMetric(stats.conversation_quality?.conversational_discovery_rate ?? null, true)}
              status="tracking"
            />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 6 · SYSTEM INTERNALS  — Engineering (collapsed by default)        */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={6}
            shortLabel="Internals"
            defaultOpen={false}
            title="6 · System Internals"
            description="Routing distribution, classifier confidence, recovery rates, and LLM cost/performance. For engineering — not day-to-day monitoring."
          >
            {/* --- Routing --- */}
            <MetricRow onClick={onMetricClick} name="Service Flow" subtitle="Turns routed to service search, confirmation, or slot-filling" target="Largest bucket" value={`${routing?.buckets?.service_flow || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.buckets?.service_flow || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="Conversational (Safe)" subtitle="Greetings, thanks, help, bot identity, reset — deterministic handlers" target="—" value={`${routing?.buckets?.conversational || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.buckets?.conversational || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="Post-Results Questions" subtitle="Follow-up questions about displayed services — answered from card data, no LLM" target="Baseline tracking" value={`${routing?.category_distribution?.post_results || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.category_distribution?.post_results || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="Emotional / Frustrated / Confused" subtitle="Tone-aware responses with empathetic framing" target="—" value={`${routing?.buckets?.emotional || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.buckets?.emotional || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="Safety (Crisis + Escalation)" subtitle="Crisis resources shown or peer navigator offered" target="—" value={`${routing?.buckets?.safety || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.buckets?.safety || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="Recovery (Correction / Disambiguation)" subtitle="User corrected a misunderstanding, clarified ambiguity, or rejected results" target="—" value={`${routing?.buckets?.recovery || 0} turns`} status={totalCategorized > 0 ? "tracking" : "no-data"} statusOverride={totalCategorized > 0 ? `${Math.round(((routing?.buckets?.recovery || 0) / totalCategorized) * 100)}%` : undefined} />
            <MetricRow onClick={onMetricClick} name="⚠ General (LLM-Generated)" subtitle="Turns where the LLM fully generates the response — no template grounding" target="≤ 15% of turns" value={fmtMetric(routing?.general_rate ?? null, true)} status={statusClass(routing?.general_rate ?? null, 0.15, "lte", 0.25)} />
            {routing?.category_distribution && Object.keys(routing.category_distribution).length > 0 && (
              <MetricRow onClick={onMetricClick} name="Full Category Breakdown" subtitle={Object.entries(routing.category_distribution as Record<string, number>).sort(([, a], [, b]) => (b as number) - (a as number)).map(([cat, count]) => `${cat}: ${count}`).join(" · ")} target="—" value={`${Object.keys(routing.category_distribution).length} categories`} status="tracking" />
            )}

            {/* --- Classifier confidence --- */}
            <MetricRow onClick={onMetricClick} name="High Confidence Rate" subtitle={`Regex matched clearly — ${confidence?.distribution?.high || 0} of ${confidence?.total_with_confidence || 0} turns`} target="≥ 60%" value={fmtMetric(confidence?.high_rate ?? null, true)} status={statusClass(confidence?.high_rate ?? null, 0.6, "gte", 0.4)} />
            <MetricRow onClick={onMetricClick} name="Low Confidence Rate" subtitle={`LLM fallback — ${confidence?.distribution?.low || 0} turns. High = regex needs expansion`} target="≤ 15%" value={fmtMetric(confidence?.low_rate ?? null, true)} status={statusClass(confidence?.low_rate ?? null, 0.15, "lte", 0.25)} />
            {confidence?.distribution && Object.keys(confidence.distribution).length > 0 && (
              <MetricRow onClick={onMetricClick} name="Full Confidence Breakdown" subtitle={Object.entries(confidence.distribution as Record<string, number>).sort(([, a], [, b]) => b - a).map(([level, count]) => `${level}: ${count}`).join(" · ")} target="—" value={`${confidence.total_with_confidence} turns`} status="tracking" />
            )}

            {/* --- Recovery rates --- */}
            <MetricRow onClick={onMetricClick} name="Correction Rate" subtitle={`% of sessions where user said "not what I meant" (${recovery?.correction_turns || 0} turns)`} target="≤ 5%" value={fmtMetric(recovery?.correction_session_rate ?? null, true)} status={statusClass(recovery?.correction_session_rate ?? null, 0.05, "lte", 0.1)} />
            <MetricRow onClick={onMetricClick} name="Disambiguation Rate" subtitle={`% of sessions with clarifying prompts (${recovery?.disambiguation_turns || 0} turns)`} target="Baseline tracking" value={fmtMetric(recovery?.disambiguation_session_rate ?? null, true)} status={recovery?.disambiguation_session_rate != null ? "tracking" : "no-data"} />
            <MetricRow onClick={onMetricClick} name="Negative Preference Rate" subtitle={`% of sessions where user rejected all results (${recovery?.negative_preference_turns || 0} turns)`} target="≤ 10%" value={fmtMetric(recovery?.negative_preference_session_rate ?? null, true)} status={statusClass(recovery?.negative_preference_session_rate ?? null, 0.1, "lte", 0.2)} />

            {/* --- LLM cost & performance --- */}
            <MetricRow onClick={onMetricClick} name="Total LLM Calls" subtitle={llmCallsSubtitle} target="Baseline tracking" value={llmMetrics?.total_calls != null ? String(llmMetrics.total_calls) : null} status={llmMetrics?.total_calls ? "tracking" : "no-data"} />
            <MetricRow onClick={onMetricClick} name="Estimated LLM Cost" subtitle={`${llmMetrics?.total_input_tokens || 0} input + ${llmMetrics?.total_output_tokens || 0} output tokens`} target="Track for capacity model" value={llmMetrics?.estimated_cost != null ? `$${llmMetrics.estimated_cost.toFixed(4)}` : null} status={llmMetrics?.estimated_cost ? "tracking" : "no-data"} />
            <MetricRow onClick={onMetricClick} name="Latency p50 / p95" subtitle="Median and 95th percentile LLM response time" target="p50 ≤ 600ms" value={llmLatencyValue} status={llmMetrics?.latency_p50_ms != null ? (llmMetrics.latency_p50_ms <= 600 ? "on-target" : "warning") : "no-data"} />
            <MetricRow onClick={onMetricClick} name="LLM Failure Rate" subtitle="% of LLM calls that failed (timeout, error, invalid response)" target="≤ 2%" value={fmtMetric(llmMetrics?.failure_rate ?? null, true)} status={statusClass(llmMetrics?.failure_rate ?? null, 0.02, "lte", 0.05)} />
            {llmMetrics?.by_task && Object.keys(llmMetrics.by_task).length > 0 && (
              <MetricRow onClick={onMetricClick} name="Calls by Task" subtitle={Object.entries(llmMetrics.by_task as Record<string, { calls: number; avg_latency_ms: number }>).sort(([, a], [, b]) => b.calls - a.calls).map(([task, info]) => `${task}: ${info.calls} (${info.avg_latency_ms}ms avg)`).join(" · ")} target="—" value={`${Object.keys(llmMetrics.by_task).length} tasks`} status="tracking" />
            )}
            {llmMetrics?.by_model && Object.keys(llmMetrics.by_model).length > 0 && (
              // Build a compact "haiku-4-5: 234 · sonnet-4-6: 12" subtitle.
              // Strip the "claude-" prefix and any 8-digit date suffix
              // (e.g. "-20251001"). Same pattern as model-card.tsx —
              // hardcoding a specific date string used to drop suffixes
              // for some models but not others.
              <MetricRow onClick={onMetricClick} name="Calls by Model" subtitle={Object.entries(llmMetrics.by_model as Record<string, number>).map(([model, count]) => `${model.replace("claude-", "").replace(/-\d{8}$/, "")}: ${count}`).join(" · ")} target="—" value={`${Object.values(llmMetrics.by_model as Record<string, number>).reduce((a, b) => a + b, 0)} total`} status="tracking" />
            )}
          </MetricsSection>

          {/* ================================================================= */}
          {/* 7 · OPERATIONS  — Staffing & planning                            */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={7}
            shortLabel="Ops"
            defaultOpen={false}
            title="7 · Operations"
            description="When and where users need help, session engagement patterns, and post-results behavior. Informs peer navigator staffing and database coverage priorities."
          >
            <MetricRow onClick={onMetricClick} name="Peak Hour (ET)" subtitle={timeOfDay?.total_events ? `${timeOfDay.total_events} events across ${Object.keys(timeOfDay.daily || {}).length} days` : "No data yet"} target="Staffing alignment" value={timeOfDay?.peak_hour_utc != null ? utcHourToET(timeOfDay.peak_hour_utc) : null} status={timeOfDay?.peak_hour_utc != null ? "tracking" : "no-data"} />
            {timeOfDay?.hourly && Object.keys(timeOfDay.hourly).length > 0 && (
              <MetricRow onClick={onMetricClick} name="Hourly Distribution" subtitle={Object.entries(timeOfDay.hourly as Record<string, number>).filter(([, count]) => count > 0).sort(([, a], [, b]) => b - a).slice(0, 6).map(([hour, count]) => `${utcHourToET(Number(hour))}: ${count}`).join(" · ") + " (top 6, ET)"} target="—" value={`${timeOfDay.total_events} events`} status="tracking" />
            )}
            {geoDemand && Object.keys(geoDemand).length > 0 && (
              <>
                <MetricRow onClick={onMetricClick} name="Top Locations" subtitle={Object.entries(geoDemand as Record<string, { total_queries: number; share: number; no_result_rate: number }>).slice(0, 5).map(([loc, info]) => `${loc}: ${info.total_queries}q (${Math.round(info.share * 100)}%)`).join(" · ")} target="—" value={`${Object.keys(geoDemand).length} locations`} status="tracking" />
                <MetricRow onClick={onMetricClick} name="Location No-Result Rates" subtitle={Object.entries(geoDemand as Record<string, { total_queries: number; no_result_rate: number }>).filter(([, info]) => info.no_result_rate > 0).sort(([, a], [, b]) => b.no_result_rate - a.no_result_rate).slice(0, 5).map(([loc, info]) => `${loc}: ${Math.round(info.no_result_rate * 100)}%`).join(" · ") || "All locations returning results"} target="Identify underserved areas" value={null} status="tracking" />
              </>
            )}
            <MetricRow onClick={onMetricClick} name="Bounce Rate" subtitle={`% of sessions with exactly 1 turn (${sessionMetrics?.bounce_count || 0} bounces)`} target="≤ 25%" value={fmtMetric(sessionMetrics?.bounce_rate ?? null, true)} status={statusClass(sessionMetrics?.bounce_rate ?? null, 0.25, "lte", 0.4)} />
            <MetricRow onClick={onMetricClick} name="Median Turns per Session" subtitle={sessionMetrics?.median_turns_per_session != null ? `Mean: ${sessionMetrics.avg_turns_per_session ?? "n/a"} · ${sessionMetrics.total_sessions || 0} sessions` : `${sessionMetrics?.total_sessions || 0} sessions`} target="3-6 for triage" value={sessionMetrics?.median_turns_per_session != null ? String(sessionMetrics.median_turns_per_session) : null} status={sessionMetrics?.median_turns_per_session != null ? "tracking" : "no-data"} />
            {sessionMetrics?.distribution && (
              <MetricRow onClick={onMetricClick} name="Turn Distribution" subtitle={Object.entries(sessionMetrics.distribution as Record<string, number>).map(([bucket, count]) => `${bucket.replace(/_/g, " ")}: ${count}`).join(" · ")} target="—" value={`${sessionMetrics.total_sessions} sessions`} status="tracking" />
            )}
            <MetricRow onClick={onMetricClick} name="Median Session Duration" subtitle={sessionDur?.median_duration_sec != null ? `Mean: ${sessionDur.avg_duration_sec != null ? `${Math.round(sessionDur.avg_duration_sec)}s` : "n/a"} · p95: ${sessionDur.p95_duration_sec ?? "n/a"}s` : "Needs multi-turn sessions"} target="3-7 min for navigator" value={sessionDur?.median_duration_sec != null ? `${Math.round(sessionDur.median_duration_sec)}s` : null} status={sessionDur?.median_duration_sec != null ? "tracking" : "no-data"} />
            {sessionDur?.buckets && sessionDur.total_multi_turn_sessions > 0 && (
              <MetricRow onClick={onMetricClick} name="Duration Buckets" subtitle={Object.entries(sessionDur.buckets as Record<string, number>).map(([bucket, count]) => `${bucket.replace(/_/g, " ")}: ${count}`).join(" · ")} target="—" value={`${sessionDur.total_multi_turn_sessions} sessions`} status="tracking" />
            )}
            <MetricRow onClick={onMetricClick} name="Post-Results Engagement" subtitle={`Of ${postResultsEng?.sessions_with_results || 0} sessions with results, ${postResultsEng?.sessions_engaged || 0} asked follow-up questions`} target="Baseline tracking" value={fmtMetric(postResultsEng?.engagement_rate ?? null, true)} status={postResultsEng?.engagement_rate != null ? "tracking" : "no-data"} />
          </MetricsSection>

          {/* ================================================================= */}
          {/* 8 · EVAL TARGETS                                                 */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={8}
            shortLabel="Evals"
            defaultOpen={false}
            title="8 · Eval Targets — LLM-as-Judge"
            description="Run or upload an eval report from the Evals tab to populate these scores. Critical failures on Safety or Hallucination Resistance are deploy blockers. Note: Response Tone and Dignity & Anti-Stigma are intentionally scored strictly per the SAMHSA rubric — for the population this serves, transactional tone is a gap, so sub-4.0 scores reflect real warmth gaps, not measurement noise."
          >
            {(() => {
              const evalReport = evalSlice.data;
              const dims = evalReport?.summary?.dimension_averages ?? null;
              const scenarios = evalReport?.scenarios ?? null;

              // Headline summary rows — passing rate + critical failures from
              // whichever report is currently loaded.
              let summaryRows: React.ReactNode = null;
              if (evalReport?.summary) {
                const total = evalReport.summary.scenarios_evaluated;
                const cfs = evalReport.summary.critical_failure_count;
                const passing = scenarios
                  ? scenarios.filter((s) => s.average_score >= 4.0 && !s.error).length
                  : null;
                const passingRate = passing !== null && total > 0 ? passing / total : null;
                const overall = evalReport.summary.overall_average;

                summaryRows = (
                  <>
                    <MetricRow
                      onClick={onMetricClick}
                      name="Overall Average"
                      subtitle={`${total} scenarios evaluated${evalReport.summary.scenarios_with_errors ? ` · ${evalReport.summary.scenarios_with_errors} eval errors` : ""}`}
                      target="≥ 4.0 / 5.0"
                      value={fmtMetric(overall, false, 2)}
                      status={statusClass(overall, 4.0, "gte", 3.5)}
                    />
                    {passingRate !== null && (
                      <MetricRow
                        onClick={onMetricClick}
                        name="Mean Passing Rate"
                        subtitle={`Aggregate signal — scenarios with mean score ≥ 4.0 across all dimensions (${passing}/${total}). Per-dimension targets tracked below.`}
                        target="≥ 95% (aspirational)"
                        value={fmtMetric(passingRate, true, 1)}
                        status={statusClass(passingRate, 0.95, "gte", 0.85)}
                      />
                    )}
                    <MetricRow
                      onClick={onMetricClick}
                      name="Critical Failures"
                      subtitle="Concrete failures flagged by the judge — deploy-blocking when on Safety or Hallucination dims"
                      target="0"
                      value={String(cfs)}
                      status={cfs === 0 ? "on-target" : cfs <= 5 ? "warning" : "off-target"}
                    />
                  </>
                );
              }

              // Per-dimension rows — populated from dimension_averages when present,
              // fall back to no-data when no report has been loaded. The dimension
              // list is owned by `lib/admin/eval-dimensions.ts` so the Metrics tab
              // and the Evals tab cannot disagree on targets.

              return (
                <>
                  {summaryRows}
                  {EVAL_DIMENSIONS.map((d) => {
                    const score = dims?.[d.key]?.average ?? null;
                    const targetLabel = `≥ ${d.target.toFixed(1)} / 5.0${d.blocker ? " ⚠ blocker" : ""}`;
                    // warningBoundFor honors per-dimension explicit
                    // warningThreshold overrides (privacy, hallucination,
                    // equity). The previous `d.target - 0.5` formula
                    // was too wide once safety-critical targets moved
                    // to 4.9 — same fix as in eval-results.tsx.
                    return (
                      <MetricRow
                        onClick={onMetricClick}
                        key={d.key}
                        name={d.label}
                        subtitle="LLM-as-judge score (1–5)"
                        target={targetLabel}
                        value={fmtMetric(score, false, 2)}
                        status={statusClass(score, d.target, "gte", warningBoundFor(d))}
                      />
                    );
                  })}
                </>
              );
            })()}
          </MetricsSection>

          {/* ================================================================= */}
          {/* 9 · POST-PILOT OUTCOMES                                          */}
          {/* ================================================================= */}
          <MetricsSection
            sectionId={9}
            shortLabel="Closed-loop"
            defaultOpen={false}
            title="9 · Closed-Loop Outcomes — Post-Pilot"
            description="These metrics require SMS follow-up infrastructure and privacy review. Not implemented in the pilot."
          >
            <MetricRow onClick={onMetricClick} name="Referral Success Rate" subtitle="% of users who confirm visiting the referred service" target="≥ 75% of opt-in users" value={null} status="no-data" phase="Post-pilot" />
            <MetricRow onClick={onMetricClick} name="Service Accuracy Rate" subtitle="% of post-visit feedback where details matched reality" target="≥ 85%" value={null} status="no-data" phase="Post-pilot" />
            <MetricRow onClick={onMetricClick} name="Outcome Linkage" subtitle="Correlate success rates with user profile and location" target="Baseline established" value={null} status="no-data" phase="Post-pilot" />
          </MetricsSection>

          {/* Metric detail dialog */}
          {selectedMetric && (
            <MetricDetailDialog
              metric={selectedMetric}
              onClose={() => setSelectedMetric(null)}
            />
          )}
        </>
      </IssueFilterContext.Provider>
    </SectionRegistryContext.Provider>
  );
}
