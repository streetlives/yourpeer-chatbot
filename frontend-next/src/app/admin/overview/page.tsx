// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect } from "react";
import { useAdminStore } from "@/lib/admin/store";
import { StatCard } from "@/components/admin/stat-card";
import { EventFeed } from "@/components/admin/event-feed";
import { SystemHealth } from "@/components/admin/system-health";
import { StatCardSkeleton, TableSkeleton } from "@/components/admin/loading-skeleton";

export default function OverviewPage() {
  const { stats, events, fetchStats, fetchEvents } = useAdminStore();

  useEffect(() => {
    fetchStats();
    fetchEvents();
  }, [fetchStats, fetchEvents]);

  if (stats.error || events.error) {
    return (
      <div className="text-center py-16 text-neutral-400">
        <div className="text-3xl mb-3">📊</div>
        <p>No data yet. Start chatting to generate activity.</p>
      </div>
    );
  }

  if (!stats.data) {
    return (
      <>
        <StatCardSkeleton />
        <div className="mt-6">
          <TableSkeleton rows={5} cols={3} />
        </div>
      </>
    );
  }

  const s = stats.data;

  // --- Task Completion Rate ---
  const taskRate = s.task_completion_rate;
  const taskDisplay = taskRate != null ? `${Math.round(taskRate * 100)}%` : "—";
  const taskCls =
    taskRate == null ? ""
      : taskRate >= 0.8 ? "text-green-600"
        : taskRate >= 0.6 ? "text-amber-500"
          : "text-red-600";

  // --- Avg Turns to Result ---
  const avgTurns = s.avg_turns_to_result;
  const avgTurnsDisplay = avgTurns != null ? `${avgTurns}` : "—";
  const avgTurnsCls =
    avgTurns == null ? ""
      : avgTurns <= 4 ? "text-green-600"
        : avgTurns <= 6 ? "text-amber-500"
          : "text-red-600";

  // --- No-Result Rate ---
  const noResultRate = s.no_result_rate;
  const noResultDisplay = noResultRate != null ? `${Math.round(noResultRate * 100)}%` : "—";
  const noResultCls =
    noResultRate == null ? ""
      : noResultRate <= 0.15 ? "text-green-600"
        : noResultRate <= 0.25 ? "text-amber-500"
          : "text-red-600";

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
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 mb-6">
        <StatCard label="Sessions" value={s.unique_sessions} colorClass="text-amber-500" />
        <StatCard
          label="Task Completion"
          value={taskDisplay}
          colorClass={taskCls}
          note="target ≥ 80%"
        />
        <StatCard
          label="Avg Turns to Result"
          value={avgTurnsDisplay}
          colorClass={avgTurnsCls}
          note="target ≤ 4"
        />
        <StatCard
          label="Crises Detected"
          value={s.total_crises}
          colorClass={s.total_crises > 0 ? "text-red-600" : "text-green-600"}
        />
        <StatCard
          label="User Feedback"
          value={feedbackDisplay}
          colorClass={feedbackCls}
          note={totalFeedback > 0 ? `${totalFeedback} responses · target ≥ 70%` : "target ≥ 70%"}
        />
        <StatCard
          label="No-Result Rate"
          value={noResultDisplay}
          colorClass={noResultCls}
          note="target ≤ 15%"
        />
      </div>

      <div className="mb-6">
        <SystemHealth />
      </div>

      <div className="mb-7">
        <h2 className="text-base font-semibold mb-4">Recent Activity</h2>
        <EventFeed events={events.data.slice(0, 20)} />
      </div>
    </>
  );
}
