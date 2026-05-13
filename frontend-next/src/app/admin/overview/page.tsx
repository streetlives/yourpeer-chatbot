// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState, useCallback } from "react";
import { useDataSlice } from "@/hooks/use-data-slice";
import { DataPanel } from "@/components/admin/data-panel";
import { StatCard } from "@/components/admin/stat-card";
import { EventFeed } from "@/components/admin/event-feed";
import { SystemHealth } from "@/components/admin/system-health";
import { OperationsBlock } from "@/components/admin/operations-charts";
import { StatCardSkeleton, TableSkeleton } from "@/components/admin/loading-skeleton";
import { StatCardDetailDialog } from "@/components/admin/stat-card-detail-dialog";
import {
  findStatCardDefinition,
  type StatCardDefinition,
} from "@/lib/admin/stat-card-definitions";
import type { AdminStats } from "@/lib/chat/types";

export default function OverviewPage() {
  // Two independent slices so a partial failure (events fetch fails but
  // stats succeeds) keeps the metric cards visible and only renders an
  // error in the events region.
  const statsSlice = useDataSlice("stats");
  const eventsSlice = useDataSlice("events");

  // Open explainer dialog. State lives at the page level (rather than
  // inside StatCardsRow) so the dialog can render OUTSIDE the
  // DataPanel — that way the dialog stays mounted even if the stats
  // slice re-renders and the cards momentarily unmount. Mirrors the
  // pattern used by the Metrics tab's MetricDetailDialog.
  const [openCard, setOpenCard] = useState<StatCardDefinition | null>(null);
  const openCardByLabel = useCallback((label: string) => {
    const def = findStatCardDefinition(label);
    // findStatCardDefinition returns null for unknown labels — in
    // that case do nothing rather than open an empty dialog. This
    // means a future card without a registered definition simply
    // won't be clickable, which is the right failure mode for an
    // explainer UI: missing > wrong.
    if (def) setOpenCard(def);
  }, []);

  return (
    <>
      {/* System Health sits at the top so the dashboard's first
          visible signal is "is everything running?". If a backend is
          unhealthy, every metric below is suspect; surfacing health
          first means admins notice a degraded system before they
          start interpreting numbers it produced. */}
      <div className="mb-6">
        <SystemHealth />
      </div>

      <DataPanel
        slice={statsSlice}
        skeleton={<StatCardSkeleton />}
        isEmpty={(data) => data == null}
        emptyState={
          <div className="text-center py-10 text-neutral-400 dark:text-neutral-500">
            <div className="text-3xl mb-3">📊</div>
            <p>No activity yet. Start chatting to see metrics here.</p>
          </div>
        }
      >
        {(s) => (
          <>
            <StatCardsRow
              stats={s as AdminStats}
              onCardClick={openCardByLabel}
            />
            <OperationsBlock stats={s as AdminStats} />
          </>
        )}
      </DataPanel>

      <div className="mb-7">
        <h2 className="text-base font-semibold mb-4">Recent Activity</h2>
        <DataPanel
          slice={eventsSlice}
          skeleton={
            <TableSkeleton
              rows={5}
              // Matches EventFeed: Time (relative), Type (badge),
              // Detail (wide), Session (id-prefix)
              widths={["w-20", "w-24", "w-44", "w-20"]}
            />
          }
          emptyState={
            <div className="text-center py-10 text-neutral-400 dark:text-neutral-500">
              <p className="text-sm">No recent events to display.</p>
            </div>
          }
        >
          {(events) => <EventFeed events={events.slice(0, 20)} />}
        </DataPanel>
      </div>

      {/* Stat-card explainer dialog. Rendered at page scope (outside
          DataPanel) so it stays mounted across stats re-fetches; the
          DataPanel can swap between skeleton, content, and empty
          state without disturbing an open dialog. Mirrors how the
          Metrics tab places <MetricDetailDialog> at the bottom of
          its render tree. */}
      {openCard && (
        <StatCardDetailDialog
          card={openCard}
          onClose={() => setOpenCard(null)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// StatCardsRow — extracted so the page-level component stays readable and
// the threshold logic lives next to its consumers.
// ---------------------------------------------------------------------------

function StatCardsRow({
  stats: s,
  onCardClick,
}: {
  stats: AdminStats;
  onCardClick: (label: string) => void;
}) {
  // --- Task Completion Rate ---
  // Denominator displayed alongside the percentage so the reader can
  // tell whether a "75%" reflects 3-of-4 or 300-of-400 — small-sample
  // percentages are statistically noisy and shouldn't drive
  // decisions the same way. Same UX as the User Feedback and
  // Confirmation Confirm Rate cards (count · target).
  //
  // `service_intent_sessions` is the right denominator (matches
  // `metrics/page.tsx`'s task-completion calculation, which excludes
  // greeting-only / help-only / crisis-only sessions). Backend
  // contract: AdminStats.service_intent_sessions.
  const taskRate = s.task_completion_rate;
  const taskDisplay = taskRate != null ? `${Math.round(taskRate * 100)}%` : "—";
  const taskCls =
    taskRate == null ? ""
      : taskRate >= 0.8 ? "text-green-600"
        : taskRate >= 0.6 ? "text-amber-500"
          : "text-red-600";
  const serviceIntentSessions = s.service_intent_sessions ?? 0;
  const taskNote =
    serviceIntentSessions > 0
      ? `${serviceIntentSessions} service-intent session${serviceIntentSessions !== 1 ? "s" : ""} · target ≥ 80%`
      : "target ≥ 80%";

  // --- Avg Turns to Result ---
  // Thresholds match `metrics/page.tsx`'s Median Turns to Query row:
  // target ≤ 5 (free-text), warning 5–7, off-target > 7. The note shown
  // to users says "≤ 5" rather than "≤ 4" so the two tabs agree.
  const avgTurns = s.avg_turns_to_result;
  const avgTurnsDisplay = avgTurns != null ? `${avgTurns}` : "—";
  const avgTurnsCls =
    avgTurns == null ? ""
      : avgTurns <= 5 ? "text-green-600"
        : avgTurns <= 7 ? "text-amber-500"
          : "text-red-600";

  // --- No-Result Rate ---
  const noResultRate = s.no_result_rate;
  const noResultDisplay = noResultRate != null ? `${Math.round(noResultRate * 100)}%` : "—";
  const noResultCls =
    noResultRate == null ? ""
      : noResultRate <= 0.15 ? "text-green-600"
        : noResultRate <= 0.25 ? "text-amber-500"
          : "text-red-600";

  // --- Confirmation Confirm Rate ---
  // Replaces the legacy "Crises Detected" count card. A bare crisis
  // count is uninformative on a daily basis (you can't have a "high"
  // or "low" count without context); the per-category breakdown lives
  // in the Operations block's Crisis Activity panels (24h + all-time)
  // instead.
  //
  // Confirm rate fills the "intent-understanding quality" slot in the
  // top row that the other 5 cards don't cover. It's the upstream
  // signal — % of confirmation prompts the user agreed with. Low
  // confirm rate means the bot is mis-hearing user intent, even if
  // downstream metrics like Task Completion still look OK.
  //
  // Thresholds: 75% as the "users agreeing with what we heard"
  // baseline, 50-75% as a warning band, below 50% as a real signal
  // that slot extraction is misaligned with what users are saying.
  // Keep these numbers in sync with the metrics page's confirm-rate
  // row if/when one is added there.
  const confirmRate = s.confirmation_breakdown?.confirm_rate ?? null;
  const confirmActions = s.confirmation_breakdown?.total_actions ?? 0;
  const confirmDisplay =
    confirmRate != null ? `${Math.round(confirmRate * 100)}%` : "—";
  const confirmCls =
    confirmRate == null ? ""
      : confirmRate >= 0.75 ? "text-green-600"
        : confirmRate >= 0.5 ? "text-amber-500"
          : "text-red-600";
  const confirmNote =
    confirmActions > 0
      ? `${confirmActions} confirmation${confirmActions !== 1 ? "s" : ""} · target ≥ 75%`
      : "target ≥ 75%";

  // --- User Feedback ---
  const totalFeedback = (s.feedback_up || 0) + (s.feedback_down || 0);
  const feedbackDisplay =
    totalFeedback > 0 ? `${Math.round((s.feedback_score ?? 0) * 100)}% 👍` : "—";
  const feedbackCls =
    s.feedback_score === null
      ? ""
      : s.feedback_score >= 0.7
        ? "text-green-600"
        : s.feedback_score >= 0.5
          ? "text-amber-500"
          : "text-red-600";

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 mb-6">
      <StatCard
        label="Sessions"
        value={s.unique_sessions}
        colorClass="text-amber-500"
        onClick={onCardClick}
      />
      <StatCard
        label="Task Completion"
        value={taskDisplay}
        colorClass={taskCls}
        note={taskNote}
        onClick={onCardClick}
      />
      <StatCard
        label="Avg Turns to Result"
        value={avgTurnsDisplay}
        colorClass={avgTurnsCls}
        note="target ≤ 5"
        onClick={onCardClick}
      />
      <StatCard
        label="Confirmation Confirm Rate"
        value={confirmDisplay}
        colorClass={confirmCls}
        note={confirmNote}
        onClick={onCardClick}
      />
      <StatCard
        label="User Feedback"
        value={feedbackDisplay}
        colorClass={feedbackCls}
        note={totalFeedback > 0 ? `${totalFeedback} responses · target ≥ 70%` : "target ≥ 70%"}
        onClick={onCardClick}
      />
      <StatCard
        label="No-Result Rate"
        value={noResultDisplay}
        colorClass={noResultCls}
        note="target ≤ 15%"
        onClick={onCardClick}
      />
    </div>
  );
}
