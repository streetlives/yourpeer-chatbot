// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { AdminStats } from "@/lib/chat/types";
import { utcHourToET, etHourToUtcHour } from "@/lib/admin/format-time";
import {
  TURN_COUNT_BUCKET_KEYS,
  SESSION_DURATION_BUCKET_KEYS,
} from "@/lib/admin/bucket-keys";

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
            <div className="w-[150px] flex-shrink-0 truncate text-neutral-700 font-medium" title={it.label}>
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
 * Hourly traffic shape. 24 bars, ET-aligned (midnight ET on the left,
 * 11 PM ET on the right). Peak hour highlighted in solid amber so it's
 * a one-glance read for staffing decisions.
 *
 * Why this matters: Section 7's text rendering of the same data ("3 PM:
 * 47 · 2 PM: 41 · 11 AM: 38 · …") shows 6 hours and discards the
 * other 18. The actual shape — when the line goes up, when it tapers,
 * when it spikes — is invisible there. This view shows it.
 *
 * Why the bars are walked in ET order (not UTC order): the backend
 * buckets events by UTC hour. A naive 0..23 walk over the UTC dict
 * produces a chart anchored to UTC midnight, which in NYC means the
 * leftmost bar is 8 PM ET (winter) or 9 PM EDT (summer) — five hours
 * off from what an admin reading "the day's traffic" expects. So we
 * walk ET hours 0..23 and look up each one's UTC bucket via
 * `etHourToUtcHour`. The DST-safe inverse handles the boundary; this
 * widget never has to know the offset.
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

  // Walk ET hours 0..23 explicitly. Each ET hour maps to a UTC hour
  // via the DST-safe inverse; we read the corresponding bucket from
  // the backend dict (which is keyed by UTC hour string). Empty hours
  // render as zero-bars rather than missing columns — the dict is
  // sparse on the backend, but our chart is dense by design.
  const hourly = tod.hourly as Record<string, number>;
  const bars = Array.from({ length: 24 }, (_, etHour) => {
    const utcHour = etHourToUtcHour(etHour);
    const value = hourly[String(utcHour)] ?? 0;
    return {
      label: utcHourToET(utcHour).replace(" ", ""), // "9AM" not "9 AM" for label space
      value,
    };
  });

  // Peak ET index — the bar to highlight visually. Computed from the
  // ET-ordered bars array rather than from peak_hour_utc, so the
  // highlight lands at the right column under this layout. Done as a
  // reduce to satisfy the project's immutability lint rule (no
  // mutable accumulator inside Array.from). Seeded with 0 because
  // bars is always 24 long (the Array.from above) — comparing against
  // -1 would leave best stuck at -1 for the all-zero case.
  const peakEtIndex = bars.reduce(
    (best, bar, i) => (bar.value > bars[best].value ? i : best),
    0,
  );

  // Subtitle peak label: prefer the backend's peak_hour_utc (it's the
  // ground truth from the full event timestamps), fall back to our
  // local peak if absent. Either way the displayed string is in ET.
  const peakLabel =
    tod.peak_hour_utc != null
      ? utcHourToET(tod.peak_hour_utc)
      : peakEtIndex >= 0
        ? utcHourToET(etHourToUtcHour(peakEtIndex))
        : null;

  return (
    <WidgetCard
      title="When (Hourly Traffic)"
      subtitle={`${tod.total_events} events${peakLabel ? ` · peak ${peakLabel}` : ""}`}
    >
      <VerticalBars
        items={bars}
        highlightIndex={peakEtIndex}
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
 * Session duration distribution — 5 buckets (<1m, 1-3m, 3-7m, 7-15m,
 * 15m+). The 3-7m bucket is the peer-navigator handoff target range;
 * the chart shows whether sessions are clustering there or skewing
 * too short (bounce-likely) / too long (stuck users). Bucket
 * boundaries are owned by the backend `_compute_session_duration`
 * function — keep this list in lockstep with that function.
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

  // Canonical bucket order lives in `lib/admin/bucket-keys.ts` so the
  // verify script can cross-check against the backend's
  // `_compute_session_duration` source. Display labels stay here
  // (presentation lives on the frontend). Any unknown keys render at
  // the end of the chart in their iteration order — display rather
  // than drop, so a bucket-name change is visible (degraded labels)
  // not silent (missing data).
  //
  // Widened to readonly string[] for the concat with Object.keys()
  // below (which returns string[], not the literal union).
  const ORDER: readonly string[] = SESSION_DURATION_BUCKET_KEYS;
  const LABEL_BY_KEY: Record<string, string> = {
    "under_1min": "<1m",
    "1_3min": "1-3m",
    "3_7min": "3-7m",
    "7_15min": "7-15m",
    "over_15min": "15m+",
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

  // Turn-count distribution buckets. Canonical order lives in
  // `lib/admin/bucket-keys.ts` so the verify script can cross-check
  // against the backend's `_compute_session_metrics` source. Display
  // labels stay here (presentation lives on the frontend).
  //
  // Widened to readonly string[] for the concat with Object.keys()
  // below (which returns string[], not the literal union).
  const ORDER: readonly string[] = TURN_COUNT_BUCKET_KEYS;
  const LABEL_BY_KEY: Record<string, string> = {
    "1_turn": "1",
    "2-3_turns": "2-3",
    "4-6_turns": "4-6",
    "7-10_turns": "7-10",
    "11+_turns": "11+",
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
// Widget 5 — Crisis Activity (Last 24h + All-time, side-by-side)
// ---------------------------------------------------------------------------

/**
 * Display labels for crisis categories. Lifted to module scope so the
 * two crisis panels (24h and all-time) share one source.
 *
 * The backend emits canonical snake_case names from
 * `crisis_detector._CRISIS_CATEGORIES`; presentation lives here. An
 * unknown key falls back to title-cased snake-case via humanizeKey,
 * so a future backend addition renders acceptably without a frontend
 * change.
 */
const CRISIS_DISPLAY_LABELS: Record<string, string> = {
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

/** snake_case → "Snake Case" — fallback for unknown crisis category keys. */
function humanizeKey(key: string): string {
  return key
    .split("_")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/**
 * One crisis-breakdown panel. Used twice in CrisisActivityBlock — once
 * for the rolling-24h view and once for the all-time view. Renders as
 * a sentinel ("✓ No crises…") when the breakdown is empty, so the
 * panel signals "we're watching, nothing to report" rather than
 * appearing broken.
 *
 * Stacks panel-internal content vertically (title-row, then body). The
 * outer CrisisActivityBlock provides the section header.
 */
function CrisisPanel({
  title,
  subtitle,
  emptyText,
  breakdown,
  total,
  showFooterHint = false,
}: {
  title: string;
  subtitle: string;
  emptyText: string;
  breakdown: Record<string, number>;
  total: number;
  /** Set on the 24h panel only — the link to the activity feed below
   *  is more useful pointing at recent events than at all-time ones. */
  showFooterHint?: boolean;
}) {
  if (total === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4">
        <div className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">
          {title}
        </div>
        <div className="text-[0.7rem] text-neutral-400 mt-0.5 mb-3">
          {subtitle}
        </div>
        <div className="flex items-center gap-2 text-sm text-emerald-700 py-2">
          <span className="text-base">✓</span>
          <span>{emptyText}</span>
        </div>
      </div>
    );
  }

  // Sort by count descending. Display labels are humanized at render time
  // (CRISIS_DISPLAY_LABELS is shared at module scope).
  const items = Object.entries(breakdown)
    .sort(([, a], [, b]) => b - a)
    .map(([key, count]) => ({
      label: CRISIS_DISPLAY_LABELS[key] ?? humanizeKey(key),
      value: count,
    }));

  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">
        {title}
      </div>
      <div className="text-[0.7rem] text-neutral-400 mt-0.5 mb-3">
        {subtitle}
      </div>
      <HorizontalBars items={items} maxRows={8} />
      {showFooterHint && (
        <div className="mt-3 pt-2 border-t border-neutral-100 text-[0.65rem] text-neutral-400">
          See the Recent Activity feed below for individual crisis events.
        </div>
      )}
    </div>
  );
}

/**
 * Crisis Activity block — single section header with two side-by-side
 * panels. Replaces the bare "Crises Detected" count card from the
 * original Overview, and replaces the previous single-panel
 * CrisisCategoriesWidget.
 *
 * The two panels intentionally show:
 *   - Last 24 hours (rolling, anchored to now) — "what's happening
 *     recently". This is the panel that should change shape day to day.
 *   - All-time — "what's the cumulative shape" — useful for recognizing
 *     when today's mix is unusual relative to historical norms.
 *
 * Side-by-side rather than stacked because the comparison itself is the
 * interesting signal (is today's mix unusual? are we seeing more DV
 * than usual?). Both panels under one header rather than two separate
 * widget cards because they're answering the same question at different
 * scopes — visually unifying them makes that legible.
 *
 * Backend dependencies: `crises_by_category_24h` + `total_crises_24h`
 * for the recent panel; `crises_by_category` + `total_crises` for the
 * all-time panel. Both contracts are pinned by the backend test suite.
 */
export function CrisisActivityBlock({ stats }: { stats: AdminStats }) {
  return (
    <div className="bg-neutral-50 border border-neutral-200 rounded-lg p-4">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-neutral-500 font-semibold">
            Crisis Activity
          </div>
          <div className="text-[0.7rem] text-neutral-400 mt-0.5">
            Detection running · breakdown by category
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <CrisisPanel
          title="Last 24 Hours"
          subtitle={
            stats.total_crises_24h > 0
              ? `${stats.total_crises_24h} event${stats.total_crises_24h !== 1 ? "s" : ""} · rolling window`
              : "rolling window"
          }
          emptyText="No crises detected in the last 24 hours."
          breakdown={stats.crises_by_category_24h ?? {}}
          total={stats.total_crises_24h ?? 0}
          showFooterHint
        />
        <CrisisPanel
          title="All-Time"
          subtitle={
            stats.total_crises > 0
              ? `${stats.total_crises} event${stats.total_crises !== 1 ? "s" : ""} · cumulative`
              : "cumulative"
          }
          emptyText="No crises detected to date."
          breakdown={stats.crises_by_category ?? {}}
          total={stats.total_crises ?? 0}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Container — composes the operations widgets into a responsive grid
// ---------------------------------------------------------------------------

/**
 * The Operations widget block, rendered between the headline stat-card
 * row and the System Health panel on the Overview page.
 *
 * Grid layout: on wide screens, two-up for the most-paired widgets
 * (When + Where, How Long + Engagement). Crisis Activity sits below
 * spanning both columns and contains its own internal 2-up layout for
 * the 24h vs all-time panels.
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
            block contains two internal panels (24h + all-time) so it
            needs the full row width to render them side-by-side. */}
        <div className="lg:col-span-2">
          <CrisisActivityBlock stats={stats} />
        </div>
      </div>
    </div>
  );
}
