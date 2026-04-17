// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, useState, useCallback } from "react";
import { useAdminStore } from "@/lib/admin/store";
import { MetricsSection } from "@/components/admin/metrics-section";
import { MetricRow, statusClass, fmtMetric } from "@/components/admin/metric-row";
import { MetricsSkeleton } from "@/components/admin/loading-skeleton";
import { MetricDetailDialog } from "@/components/admin/metric-detail-dialog";
import { findMetricDefinition } from "@/lib/admin/metric-definitions";
import type { MetricDefinition } from "@/lib/admin/metric-definitions";

// ---------------------------------------------------------------------------
// TIMEZONE — All times render in Eastern Time (NYC-based service)
// ---------------------------------------------------------------------------
const NYC_TZ = "America/New_York";

/** Convert a UTC hour (0–23) to an Eastern Time label like "9 AM" */
function utcHourToET(utcHour: number): string {
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), utcHour, 0, 0));
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    timeZone: NYC_TZ,
  });
}

export default function MetricsPage() {
  const {
    stats: statsSlice,
    conversations: convosSlice,
    queries: queriesSlice,
    fetchStats,
    fetchConversations,
    fetchQueries,
  } = useAdminStore();

  useEffect(() => {
    fetchStats();
    fetchConversations();
    fetchQueries();
  }, [fetchStats, fetchConversations, fetchQueries]);

  const [selectedMetric, setSelectedMetric] = useState<MetricDefinition | null>(null);

  const onMetricClick = useCallback((name: string) => {
    const def = findMetricDefinition(name);
    if (def) setSelectedMetric(def);
  }, []);

  const loading = (!statsSlice.data && statsSlice.loading)
    || (convosSlice.data.length === 0 && convosSlice.loading)
    || (queriesSlice.data.length === 0 && queriesSlice.loading);

  if (loading || !statsSlice.data) {
    return <MetricsSkeleton />;
  }

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
  const avgTurns =
    completedConvos.length > 0
      ? completedConvos.reduce((s, c) => s + c.turn_count, 0) / completedConvos.length
      : null;

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

  return (
    <>
      <div className="bg-neutral-50 border border-neutral-200 rounded-lg px-3.5 py-2.5 text-sm text-neutral-500 mb-6">
        Metrics are computed from the audit log. When PILOT_DB_PATH is set, data
        persists across server restarts. <strong>n/a</strong> = not yet measurable.{" "}
        <strong>Click any metric name</strong> for a detailed explanation with
        formula and target rationale from{" "}
        <a
          href="https://github.com/ianlau20/yourpeer-chatbot/blob/main/docs/ops/METRICS.md"
          target="_blank"
          className="text-amber-600 hover:underline"
        >
          METRICS.md
        </a>
        .
      </div>

      {/* ================================================================= */}
      {/* 1 · IS IT WORKING?  — Leadership dashboard                       */}
      {/* ================================================================= */}
      <MetricsSection
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
          status={statusClass(stats.feedback_score, 0.7, "gte", 0.5)}
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
          name="Avg Turns to Query"
          subtitle="Average turns from session start to first query (completed sessions)"
          target="≤ 5 turns (free-text)"
          value={fmtMetric(avgTurns, false, 1)}
          status={statusClass(avgTurns, 5, "lte", 7)}
        />
      </MetricsSection>

      {/* ================================================================= */}
      {/* 2 · ARE THE RESULTS GOOD?  — Data steward focus                   */}
      {/* ================================================================= */}
      <MetricsSection
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
        <MetricRow onClick={onMetricClick} name="Eligibility Fit Rate" subtitle="% of results matching all stated user criteria" target="≥ 95%" value="By design (canary)" status="no-data" />
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
        title="3 · Is It Safe?"
        description="Crisis detection, privacy, and hallucination metrics. Crisis detection must be 100% — any miss could leave a vulnerable person without resources."
      >
        <MetricRow onClick={onMetricClick}
          name="Crisis Detection Count"
          subtitle="Total sessions where crisis language was detected and resources shown"
          target="Target: 100% of crisis messages"
          value={String(stats.total_crises)}
          status={stats.total_crises > 0 ? "on-target" : "no-data"}
        />
        <MetricRow onClick={onMetricClick} name="Crisis False Positive Rate" subtitle="% of crisis-flagged sessions that were not genuine crises" target="≤ 5%" value={null} status="no-data" />
        <MetricRow onClick={onMetricClick} name="PII Leakage Rate" subtitle="% of stored transcripts with detectable PII after redaction" target="0%" value={null} status="no-data" />
        <MetricRow onClick={onMetricClick} name="Hallucination Rate" subtitle="% of bot responses containing fabricated service data" target="< 1% (structural guarantee)" value="~0% by design" status="on-target" />
      </MetricsSection>

      {/* ================================================================= */}
      {/* 4 · HOW DOES IT FEEL?  — Conversation experience                 */}
      {/* ================================================================= */}
      <MetricsSection
        title="4 · How Does It Feel?"
        description="Emotional awareness, tone, frustration handling, and repetition. For this population, even routine interactions carry emotional weight — purely transactional tone is a gap."
      >
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
          status="tracking"
        />
        <MetricRow onClick={onMetricClick}
          name="Emotional → Service Rate"
          subtitle="% of emotional sessions where user eventually reached a service search"
          target="Baseline tracking"
          value={fmtMetric(stats.conversation_quality?.emotional_to_service ?? null, true)}
          status="tracking"
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
          toneEntries.map(([tone, count]) => {
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
          })
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
        title="6 · System Internals"
        description="Routing distribution, classifier confidence, recovery rates, and LLM cost/performance. For engineering — not day-to-day monitoring."
        defaultOpen={false}
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
        <MetricRow onClick={onMetricClick} name="Total LLM Calls" subtitle={llmMetrics?.avg_calls_per_session != null ? `Avg ${llmMetrics.avg_calls_per_session} calls/session` : "No calls logged yet"} target="Baseline tracking" value={llmMetrics?.total_calls != null ? String(llmMetrics.total_calls) : null} status={llmMetrics?.total_calls ? "tracking" : "no-data"} />
        <MetricRow onClick={onMetricClick} name="Estimated LLM Cost" subtitle={`${llmMetrics?.total_input_tokens || 0} input + ${llmMetrics?.total_output_tokens || 0} output tokens`} target="Track for capacity model" value={llmMetrics?.estimated_cost != null ? `$${llmMetrics.estimated_cost.toFixed(4)}` : null} status={llmMetrics?.estimated_cost ? "tracking" : "no-data"} />
        <MetricRow onClick={onMetricClick} name="Latency p50 / p95" subtitle="Median and 95th percentile LLM response time" target="p50 ≤ 600ms" value={llmMetrics?.latency_p50_ms != null ? `${llmMetrics.latency_p50_ms}ms / ${llmMetrics.latency_p95_ms ?? "n/a"}ms` : null} status={llmMetrics?.latency_p50_ms != null ? (llmMetrics.latency_p50_ms <= 600 ? "on-target" : "warning") : "no-data"} />
        <MetricRow onClick={onMetricClick} name="LLM Failure Rate" subtitle="% of LLM calls that failed (timeout, error, invalid response)" target="≤ 2%" value={fmtMetric(llmMetrics?.failure_rate ?? null, true)} status={statusClass(llmMetrics?.failure_rate ?? null, 0.02, "lte", 0.05)} />
        {llmMetrics?.by_task && Object.keys(llmMetrics.by_task).length > 0 && (
          <MetricRow onClick={onMetricClick} name="Calls by Task" subtitle={Object.entries(llmMetrics.by_task as Record<string, { calls: number; avg_latency_ms: number }>).sort(([, a], [, b]) => b.calls - a.calls).map(([task, info]) => `${task}: ${info.calls} (${info.avg_latency_ms}ms avg)`).join(" · ")} target="—" value={`${Object.keys(llmMetrics.by_task).length} tasks`} status="tracking" />
        )}
        {llmMetrics?.by_model && Object.keys(llmMetrics.by_model).length > 0 && (
          <MetricRow onClick={onMetricClick} name="Calls by Model" subtitle={Object.entries(llmMetrics.by_model as Record<string, number>).map(([model, count]) => `${model.replace("claude-", "").replace("-20251001", "")}: ${count}`).join(" · ")} target="—" value={`${Object.values(llmMetrics.by_model as Record<string, number>).reduce((a, b) => a + b, 0)} total`} status="tracking" />
        )}
      </MetricsSection>

      {/* ================================================================= */}
      {/* 7 · OPERATIONS  — Staffing & planning                            */}
      {/* ================================================================= */}
      <MetricsSection
        title="7 · Operations"
        description="When and where users need help, session engagement patterns, and post-results behavior. Informs peer navigator staffing and database coverage priorities."
        defaultOpen={false}
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
        <MetricRow onClick={onMetricClick} name="Avg Turns per Session" subtitle={`Median: ${sessionMetrics?.median_turns_per_session ?? "n/a"} · ${sessionMetrics?.total_sessions || 0} sessions`} target="3-6 for triage" value={sessionMetrics?.avg_turns_per_session != null ? String(sessionMetrics.avg_turns_per_session) : null} status={sessionMetrics?.avg_turns_per_session != null ? "tracking" : "no-data"} />
        {sessionMetrics?.distribution && (
          <MetricRow onClick={onMetricClick} name="Turn Distribution" subtitle={Object.entries(sessionMetrics.distribution as Record<string, number>).map(([bucket, count]) => `${bucket.replace(/_/g, " ")}: ${count}`).join(" · ")} target="—" value={`${sessionMetrics.total_sessions} sessions`} status="tracking" />
        )}
        <MetricRow onClick={onMetricClick} name="Avg Session Duration" subtitle={sessionDur?.median_duration_sec != null ? `Median: ${Math.round(sessionDur.median_duration_sec)}s · p95: ${sessionDur.p95_duration_sec ?? "n/a"}s` : "Needs multi-turn sessions"} target="3-7 min for navigator" value={sessionDur?.avg_duration_sec != null ? `${Math.round(sessionDur.avg_duration_sec)}s` : null} status={sessionDur?.avg_duration_sec != null ? "tracking" : "no-data"} />
        {sessionDur?.buckets && sessionDur.total_multi_turn_sessions > 0 && (
          <MetricRow onClick={onMetricClick} name="Duration Buckets" subtitle={Object.entries(sessionDur.buckets as Record<string, number>).map(([bucket, count]) => `${bucket.replace(/_/g, " ")}: ${count}`).join(" · ")} target="—" value={`${sessionDur.total_multi_turn_sessions} sessions`} status="tracking" />
        )}
        <MetricRow onClick={onMetricClick} name="Post-Results Engagement" subtitle={`Of ${postResultsEng?.sessions_with_results || 0} sessions with results, ${postResultsEng?.sessions_engaged || 0} asked follow-up questions`} target="Baseline tracking" value={fmtMetric(postResultsEng?.engagement_rate ?? null, true)} status={postResultsEng?.engagement_rate != null ? "tracking" : "no-data"} />
      </MetricsSection>

      {/* ================================================================= */}
      {/* 8 · EVAL TARGETS                                                 */}
      {/* ================================================================= */}
      <MetricsSection
        title="8 · Eval Targets — LLM-as-Judge"
        description="Run the eval suite from the Eval Results tab to populate scores. Critical failures on Safety or Hallucination Resistance are deploy blockers."
      >
        {[
          { key: "slot_extraction", label: "Slot Extraction Accuracy", target: "≥ 4.0 / 5.0" },
          { key: "dialog_efficiency", label: "Dialog Efficiency", target: "≥ 3.5 / 5.0" },
          { key: "response_tone", label: "Response Tone", target: "≥ 4.0 / 5.0" },
          { key: "safety_crisis", label: "Safety & Crisis Handling", target: "≥ 4.5 / 5.0 ⚠ blocker" },
          { key: "confirmation_ux", label: "Confirmation UX", target: "≥ 3.5 / 5.0" },
          { key: "privacy", label: "Privacy", target: "≥ 4.5 / 5.0" },
          { key: "hallucination_resistance", label: "Hallucination Resistance", target: "≥ 4.5 / 5.0 ⚠ blocker" },
          { key: "error_recovery", label: "Error Recovery", target: "≥ 3.5 / 5.0" },
          { key: "dignity_anti_stigma", label: "Dignity & Anti-Stigma", target: "≥ 4.0 / 5.0" },
          { key: "cultural_responsiveness", label: "Cultural Responsiveness", target: "≥ 4.0 / 5.0" },
          { key: "equity_of_access", label: "Equity of Access", target: "≥ 4.0 / 5.0" },
        ].map((d) => (
          <MetricRow onClick={onMetricClick} key={d.key} name={d.label} subtitle="LLM-as-judge score (1–5)" target={d.target} value={null} status="no-data" />
        ))}
      </MetricsSection>

      {/* ================================================================= */}
      {/* 9 · POST-PILOT OUTCOMES                                          */}
      {/* ================================================================= */}
      <MetricsSection
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
  );
}
