// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { AdminStats } from "@/lib/chat/types";
import { utcHourToET } from "@/lib/admin/format-time";

// ===========================================================================
// Operations widgets
//
// Five inline charts that surface Section-7-style operational signal on the
// Overview page. Built without a charting library (deliberate decision —
// all five charts here are categorical bar shapes, none need axes or
// tooltips, and a recharts dependency would add ~80 KB gzipped to the
// admin bundle for no real gain). The visual vocabulary mirrors what's
// already in `eval-results.tsx`: small Tailwind primitives, neutral-100
// track, theme-amber fill, color-coded thresholds where relevant.
//
// Each widget is self-contained — accepts the AdminStats slice it needs
// and renders empty / no-data states gracefully. The Overview page
// composes them; the Metrics page still owns Section 7 as the deeper
// drill-down view.
// ===========================================================================

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

/** Section heading shared by all widgets — keeps spacing/treatment uniform. */
function WidgetCard({
  title,
  subtitle,
  emptyHint,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Show this in place of children when there's no data yet. */
  emptyHint?: string | null;
  children?: React.ReactNode;
}) {
  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">
            {title}
          </div>
          {subtitle && (
            <div className="text-[0.7rem] text-neutral-400 mt-0.5">
              {subtitle}
            </div>
          )}
        </div>
      </div>
      {emptyHint ? (
        <div className="text-sm text-neutral-400 italic py-3">
          {emptyHint}
        </div>
      ) : (
        children
      )}
    </div>
  );
}

/**
 * Vertical bar chart primitive. One bar per item. Used for hourly
 * histograms and bucketed distributions where the x-axis is intrinsic
 * (hours of day, fixed bucket order).
 *
 * Bars are sized as a fraction of `maxValue` so the tallest bar reaches
 * the configured chart height. A value of 0 still renders a 1-pixel
 * stub so the column is visually claimed (vs. just gone), which keeps
 * a 24-bar chart from appearing to have gaps.
 */
function VerticalBars({
  items,
  highlightIndex,
  height = 96,
}: {
  items: Array<{ label: string; sublabel?: string; value: number; }>;
  /** Index of the item to highlight (e.g. peak hour). -1 to skip. */
  highlightIndex?: number;
  /** Pixel height of the bar area. Default 96px (h-24). */
  height?: number;
}) {
  const maxValue = Math.max(1, ...items.map((it) => it.value));

  return (
    <div className="w-full">
      <div
        className="flex items-end gap-[2px]"
        style={{ height: `${height}px` }}
      >
        {items.map((it, i) => {
          // Floor at 1px so empty buckets are visually claimed. Without
          // this, a 24-bar chart with a single zero in the middle looks
          // like a missing column, not an explicit zero.
          const pct = (it.value / maxValue) * 100;
          const minHeightPx = it.value === 0 ? 1 : 2;
          const heightStyle =
            it.value === 0
              ? `${minHeightPx}px`
              : `max(${minHeightPx}px, ${pct}%)`;
          const isHighlight = highlightIndex !== undefined && highlightIndex === i;
          return (
            <div
              key={i}
              className="flex-1 min-w-0 flex flex-col items-center justify-end gap-0.5"
            >
              <div
                title={`${it.label}: ${it.value}`}
                className={`w-full rounded-t-[2px] transition-all ${
                  isHighlight
                    ? "bg-amber-400"
                    : it.value === 0
                      ? "bg-neutral-100"
                      : "bg-amber-300/70 hover:bg-amber-400"
                }`}
                style={{ height: heightStyle }}
              />
            </div>
          );
        })}
      </div>
      {/* Labels row — only render labels at sparse intervals on long
          rows so they don't crowd. The chart is still informative as a
          shape; the title attribute on each bar carries the precise
          value/label. */}
      <div className="flex items-start gap-[2px] mt-1.5">
        {items.map((it, i) => {
          // For 24-bucket charts, label every 6th bar (midnight, 6am,
          // noon, 6pm). For shorter charts (≤8 items), label all.
          const showLabel =
            items.length <= 8 ||
            i === 0 ||
            i === items.length - 1 ||
            i % 6 === 0;
          return (
            <div
              key={i}
              className="flex-1 min-w-0 text-center"
              style={{ visibility: showLabel ? "visible" : "hidden" }}
            >
              <div className="text-[0.6rem] text-neutral-400 leading-tight truncate">
                {it.label}
              </div>
              {it.sublabel && showLabel && (
                <div className="text-[0.55rem] text-neutral-300 leading-tight truncate">
                  {it.sublabel}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Horizontal bar list. Used for ranked categorical data — top locations,
 * crisis categories — where the labels are the meaningful axis and bar
 * length encodes count.
 *
 * `secondary` lets each row carry an additional signal as a small
 * trailing label (e.g. no-result rate). Color-coded if `secondaryColor`
 * is provided.
 */
function HorizontalBars({
  items,
  maxRows = 6,
}: {
  items: Array<{
    label: string;
    value: number;
    /** Optional trailing detail (e.g. "12% no-result"). */
    secondary?: string;
    /** Color treatment for the secondary label. Default neutral. */
    secondaryColor?: "neutral" | "warn" | "danger";
  }>;
  maxRows?: number;
}) {
  const visible = items.slice(0, maxRows);
  const maxValue = Math.max(1, ...visible.map((it) => it.value));

  return (
    <div className="space-y-1.5">
      {visible.map((it, i) => {
        const pct = (it.value / maxValue) * 100;
        const secondaryClass =
          it.secondaryColor === "danger"
            ? "text-red-600"
            : it.secondaryColor === "warn"
              ? "text-amber-600"
              : "text-neutral-400";
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <div className="w-[100px] flex-shrink-0 truncate text-neutral-700 font-medium" title={it.label}>
              {it.label}
            </div>
            <div className="flex-1 h-[18px] bg-neutral-100 rounded overflow-hidden relative">
              <div
                className="h-full bg-amber-300/70 rounded"
                style={{ width: `${pct}%` }}
              />
              <div className="absolute inset-0 flex items-center pl-2 text-[0.7rem] font-mono text-neutral-700 font-semibold">
                {it.value}
              </div>
            </div>
            {it.secondary && (
              <div className={`w-[80px] text-right text-[0.7rem] flex-shrink-0 ${secondaryClass}`}>
                {it.secondary}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Widget 1 — When (24-hour histogram)
// ---------------------------------------------------------------------------

/**
 * Hourly traffic shape. 24 bars, ET-labeled. Peak hour highlighted in
 * solid amber so it's a one-glance read for staffing decisions.
 *
 * Why this matters: Section 7's text rendering of the same data ("3 PM:
 * 47 · 2 PM: 41 · 11 AM: 38 · …") shows 6 hours and discards the
 * other 18. The actual shape — when the line goes up, when it tapers,
 * when it spikes — is invisible there. This view shows it.
 */
export function WhenWidget({ stats }: { stats: AdminStats }) {
  const tod = stats.time_of_day;
  if (!tod || !tod.hourly || Object.keys(tod.hourly).length === 0) {
    return (
      <WidgetCard
        title="When (Hourly Traffic)"
        subtitle="Conversation turns by hour of day, ET"
        emptyHint="No traffic data yet — needs at least one conversation turn to populate."
      />
    );
  }

  // Walk 0..23 explicitly so empty hours render as zero-bars, not as
  // missing columns. The backend's hourly dict is sparse.
  const bars = Array.from({ length: 24 }, (_, hour) => {
    const value = (tod.hourly as Record<string, number>)[String(hour)] ?? 0;
    return {
      label: utcHourToET(hour).replace(" ", ""), // "9AM" not "9 AM" for label space
      value,
    };
  });

  const peakHour = tod.peak_hour_utc;
  const peakLabel =
    peakHour != null
      ? utcHourToET(peakHour)
      : null;

  return (
    <WidgetCard
      title="When (Hourly Traffic)"
      subtitle={`${tod.total_events} turns${peakLabel ? ` · peak ${peakLabel}` : ""}`}
    >
      <VerticalBars
        items={bars}
        highlightIndex={peakHour != null ? peakHour : -1}
        height={96}
      />
    </WidgetCard>
  );
}

// ---------------------------------------------------------------------------
// Widget 2 — Where (top locations)
// ---------------------------------------------------------------------------

/**
 * Top user locations by query volume, with no-result rate as a secondary
 * signal. Locations with high no-result rates are color-coded (amber
 * 25-50%, red 50%+) — these are the "underserved areas" Section 7's
 * description calls out, and visualizing them as warning-tinted bars
 * makes them the most-clickable thing in the widget when it matters.
 */
export function WhereWidget({ stats }: { stats: AdminStats }) {
  const geo = stats.geographic_demand;
  if (!geo || Object.keys(geo).length === 0) {
    return (
      <WidgetCard
        title="Where (Top Locations)"
        subtitle="User-stated locations by query volume"
        emptyHint="No location data yet — populates as users mention boroughs / neighborhoods."
      />
    );
  }

  const entries = Object.entries(geo)
    .map(([loc, info]) => ({
      label: loc,
      value: info.total_queries,
      noResultRate: info.no_result_rate,
    }))
    .sort((a, b) => b.value - a.value);

  const items = entries.map((e) => {
    const pctNoResult = Math.round(e.noResultRate * 100);
    const secondary =
      e.noResultRate > 0
        ? `${pctNoResult}% no-result`
        : "all matched";
    const secondaryColor: "danger" | "warn" | "neutral" =
      e.noResultRate >= 0.5 ? "danger"
        : e.noResultRate >= 0.25 ? "warn"
          : "neutral";
    return {
      label: e.label,
      value: e.value,
      secondary,
      secondaryColor,
    };
  });

  return (
    <WidgetCard
      title="Where (Top Locations)"
      subtitle={`${entries.length} location${entries.length !== 1 ? "s" : ""} active · top 5 shown`}
    >
      <HorizontalBars items={items} maxRows={5} />
    </WidgetCard>
  );
}

// ---------------------------------------------------------------------------
// Widget 3 — How long (session duration buckets)
// ---------------------------------------------------------------------------

/**
 * Session duration distribution — 5 buckets (<1m, 1-5m, 5-15m, 15-30m,
 * 30m+). The 1-5min and 5-15min buckets are the navigator-handoff target
 * range; the chart shows whether sessions are clustering there or
 * skewing too short (bounce-likely) / too long (stuck users).
 */
export function HowLongWidget({ stats }: { stats: AdminStats }) {
  const sd = stats.session_duration;
  if (!sd || !sd.buckets || sd.total_multi_turn_sessions === 0) {
    return (
      <WidgetCard
        title="How Long (Session Duration)"
        subtitle="Multi-turn session length"
        emptyHint="No multi-turn sessions yet — populates with at least one conversation."
      />
    );
  }

  // Canonical bucket order. Backend returns these as keys; we want them
  // displayed left-to-right in chronological order regardless of the
  // dict's iteration order. If the backend ever introduces a new bucket
  // it'd be missing from this list and we'd silently drop it — we render
  // any unknown keys at the end as a fallback.
  const ORDER = ["<1min", "1_to_5min", "5_to_15min", "15_to_30min", "30min_plus"];
  const LABEL_BY_KEY: Record<string, string> = {
    "<1min": "<1m",
    "1_to_5min": "1-5m",
    "5_to_15min": "5-15m",
    "15_to_30min": "15-30m",
    "30min_plus": "30m+",
  };

  const buckets = sd.buckets as Record<string, number>;
  const ordered = ORDER.filter((k) => k in buckets).concat(
    Object.keys(buckets).filter((k) => !ORDER.includes(k)),
  );

  const items = ordered.map((key) => ({
    label: LABEL_BY_KEY[key] ?? key.replace(/_/g, " "),
    value: buckets[key] ?? 0,
  }));

  const median = sd.median_duration_sec;
  const medianStr = median != null ? `${Math.round(median)}s` : null;

  return (
    <WidgetCard
      title="How Long (Session Duration)"
      subtitle={`${sd.total_multi_turn_sessions} multi-turn session${sd.total_multi_turn_sessions !== 1 ? "s" : ""}${medianStr ? ` · median ${medianStr}` : ""}`}
    >
      <VerticalBars items={items} height={88} />
    </WidgetCard>
  );
}

// ---------------------------------------------------------------------------
// Widget 4 — Engagement (turn distribution + post-results)
// ---------------------------------------------------------------------------

/**
 * Two paired signals about how deeply users engage:
 *   1. Turn-count distribution — the conversation depth shape
 *   2. Post-results engagement rate — % of result-receiving sessions
 *      that asked a follow-up question
 *
 * The pairing is intentional. Turn distribution alone tells you HOW
 * conversations spread out. Post-results rate alone tells you whether
 * users find results valuable enough to ask more. Together they
 * separate "users got an answer and left" (low post-results, low
 * average turns) from "users couldn't get what they wanted" (low
 * post-results, lots of turns) from "users dug in deeply" (high
 * post-results, longer sessions).
 */
export function EngagementWidget({ stats }: { stats: AdminStats }) {
  const sm = stats.session_metrics;
  const pre = stats.post_results_engagement;

  if (!sm || !sm.distribution) {
    return (
      <WidgetCard
        title="Engagement"
        subtitle="Turn-count distribution and post-results follow-up"
        emptyHint="Engagement data populates after at least one full session."
      />
    );
  }

  // Turn-count distribution buckets. Keep canonical order (1, 2, 3-5,
  // 6-10, 11+). Same robustness as duration: render unknown keys after
  // canonical, don't drop.
  const ORDER = ["1", "2", "3_to_5", "6_to_10", "11_plus"];
  const LABEL_BY_KEY: Record<string, string> = {
    "1": "1",
    "2": "2",
    "3_to_5": "3-5",
    "6_to_10": "6-10",
    "11_plus": "11+",
  };

  const dist = sm.distribution as Record<string, number>;
  const ordered = ORDER.filter((k) => k in dist).concat(
    Object.keys(dist).filter((k) => !ORDER.includes(k)),
  );

  const items = ordered.map((key) => ({
    label: LABEL_BY_KEY[key] ?? key.replace(/_/g, " "),
    value: dist[key] ?? 0,
  }));

  const postPct = pre?.engagement_rate != null
    ? `${Math.round(pre.engagement_rate * 100)}%`
    : "—";
  const postSubtitle = pre?.sessions_with_results
    ? `${pre.sessions_engaged ?? 0} of ${pre.sessions_with_results} session${pre.sessions_with_results !== 1 ? "s" : ""} with results`
    : "Awaiting result-bearing sessions";

  return (
    <WidgetCard
      title="Engagement"
      subtitle={`${sm.total_sessions} session${sm.total_sessions !== 1 ? "s" : ""} · turn-count distribution`}
    >
      <VerticalBars items={items} height={80} />

      {/* Post-results engagement — rendered as a paired stat below the
          distribution bars. Same widget, two related signals; users
          read this as a unit. */}
      <div className="mt-4 pt-3 border-t border-neutral-100">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <div className="text-[0.7rem] uppercase tracking-wider text-neutral-400">
              Post-Results Follow-up Rate
            </div>
            <div className="text-[0.65rem] text-neutral-400 mt-0.5">
              {postSubtitle}
            </div>
          </div>
          <div className="text-xl font-bold tracking-tight text-neutral-900">
            {postPct}
          </div>
        </div>
      </div>
    </WidgetCard>
  );
}

// ---------------------------------------------------------------------------
// Widget 5 — Crisis Categories Today
// ---------------------------------------------------------------------------

/**
 * Crisis events broken down by category. Replaces the bare "Crises
 * Detected" count card from the original Overview. The breakdown is
 * the actually-actionable signal — "3 medical, 1 DV today" tells a
 * story; "4 crises" doesn't.
 *
 * When zero crises occurred, renders as a quiet sentinel — neutral
 * styling, "No crises in this window" — rather than as an empty chart.
 * The point of this widget is to surface activity when it happens, not
 * to demand attention when nothing did.
 *
 * Relies on `stats.crises_by_category` (added to the backend
 * aggregation on May 2026). Sums to `stats.total_crises`; that
 * invariant is enforced by the backend test suite.
 */
export function CrisisCategoriesWidget({ stats }: { stats: AdminStats }) {
  const breakdown = stats.crises_by_category ?? {};
  const total = stats.total_crises ?? 0;
  const entries = Object.entries(breakdown).sort(([, a], [, b]) => b - a);

  if (total === 0) {
    // Sentinel state — not a chart, just a quiet status.
    return (
      <WidgetCard
        title="Crisis Activity"
        subtitle="Detection running"
      >
        <div className="flex items-center gap-2 text-sm text-emerald-700 py-2">
          <span className="text-base">✓</span>
          <span>No crises detected in this window.</span>
        </div>
      </WidgetCard>
    );
  }

  // Active state — show the breakdown. Display labels are humanized
  // here (not on the backend) because the backend stays presentation-
  // agnostic and we want sensible casing/spacing for admin display.
  // Unknown keys fall back to title-cased version of the snake_case name.
  const DISPLAY_LABELS: Record<string, string> = {
    suicide_self_harm: "Suicide / Self-Harm",
    medical_emergency: "Medical Emergency",
    domestic_violence: "Domestic Violence",
    youth_runaway: "Youth Runaway",
    assault_victim: "Assault Victim",
    safety_concern: "Safety Concern",
    trafficking: "Trafficking",
    violence: "Violence",
    uncategorized: "Uncategorized",
  };

  const items = entries.map(([key, count]) => ({
    label: DISPLAY_LABELS[key] ?? humanizeKey(key),
    value: count,
  }));

  return (
    <WidgetCard
      title="Crisis Activity"
      subtitle={`${total} crisis event${total !== 1 ? "s" : ""} · by category`}
    >
      <HorizontalBars items={items} maxRows={8} />
      <div className="mt-3 pt-2 border-t border-neutral-100 text-[0.65rem] text-neutral-400">
        See the Recent Activity feed below for individual crisis events.
      </div>
    </WidgetCard>
  );
}

/** snake_case → "Snake Case" — fallback for unknown crisis category keys. */
function humanizeKey(key: string): string {
  return key
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Container — composes the five widgets into a responsive grid
// ---------------------------------------------------------------------------

/**
 * The Operations widget block, rendered between the headline stat-card
 * row and the System Health panel on the Overview page.
 *
 * Grid layout: on wide screens, two-up for the most-paired widgets
 * (When + Where, How Long + Engagement) with Crisis Activity below.
 * On narrow screens it stacks. The grid uses `auto-fit` rather than
 * fixed column counts so a single-widget mobile view falls out
 * naturally.
 */
export function OperationsBlock({ stats }: { stats: AdminStats }) {
  return (
    <div className="mb-7">
      <h2 className="text-base font-semibold mb-1">Operations</h2>
      <p className="text-xs text-neutral-400 mb-4">
        When and where users need help, session engagement patterns, and
        post-results behavior. Informs peer navigator staffing and database
        coverage priorities.
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <WhenWidget stats={stats} />
        <WhereWidget stats={stats} />
        <HowLongWidget stats={stats} />
        <EngagementWidget stats={stats} />
        {/* Crisis Activity spans both columns on wide screens — the
            label deserves the width and the chart often only has 2-3
            rows so it'd look stranded in a half-width column. */}
        <div className="lg:col-span-2">
          <CrisisCategoriesWidget stats={stats} />
        </div>
      </div>
    </div>
  );
}
