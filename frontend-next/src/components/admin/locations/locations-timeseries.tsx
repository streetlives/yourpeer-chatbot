// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";
import type {
  TimeseriesResponse,
  TimeseriesWeek,
} from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";

/**
 * Section 7 — weekly time series charts.
 *
 * Three side-by-side small line charts showing the last 26 weeks of:
 *   - Locations added per week
 *   - Locations verified per week
 *   - Location feedback events per week
 *
 * Inline SVG rather than a chart library — same reasoning as the
 * heatmap and operations-charts: bundle hygiene + visual consistency
 * with the other custom charts on the page. Three small line charts
 * is well within the "do it ourselves" zone; the implementation
 * fits in one file and stays under 200 LOC.
 *
 * Empty state surfaces a soft message rather than rendering three
 * flat-line charts at zero — flat-line zeros read as "broken" when
 * the system is genuinely just new / sparse.
 */
export function LocationsTimeseries() {
  const [data, setData] = useState<TimeseriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time fetch needs to mark loading state; standard pattern in this codebase
    setLoading(true);
    fetch("/api/admin/locations/timeseries")
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        if (isAdminApiError(body)) {
          setError(body.detail);
        } else {
          setData(body as TimeseriesResponse);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Pre-compute totals across the window — surfaced as a row total
  // beneath each chart so admins see the "what's the absolute scale
  // here" answer without hovering.
  const totals = useMemo(() => {
    if (!data) return { added: 0, verified: 0, feedback: 0 };
    return data.weeks.reduce(
      (acc, w) => ({
        added: acc.added + w.locations_added,
        verified: acc.verified + w.locations_verified,
        feedback: acc.feedback + w.feedback_events,
      }),
      { added: 0, verified: 0, feedback: 0 },
    );
  }, [data]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load time series: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[200px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  // Soft empty-state — when no series has any activity, three
  // flat-zero charts read as "broken" rather than "new system."
  const allZero = totals.added === 0 && totals.verified === 0 && totals.feedback === 0;
  if (allZero) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <p className="text-sm text-neutral-500 italic dark:text-neutral-400">
          No activity in the last {data.total_weeks} weeks. The series will populate
          as locations are added, verified, or receive feedback.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <SeriesChart
          title="Locations added"
          subtitle={`${totals.added.toLocaleString()} over ${data.total_weeks} weeks`}
          weeks={data.weeks}
          accessor={(w) => w.locations_added}
          colorClass="stroke-emerald-500 dark:stroke-emerald-400"
          fillClass="fill-emerald-100 dark:fill-emerald-900/30"
        />
        <SeriesChart
          title="Locations verified"
          subtitle={`${totals.verified.toLocaleString()} over ${data.total_weeks} weeks`}
          weeks={data.weeks}
          accessor={(w) => w.locations_verified}
          colorClass="stroke-blue-500 dark:stroke-blue-400"
          fillClass="fill-blue-100 dark:fill-blue-900/30"
        />
        <SeriesChart
          title="Feedback events"
          subtitle={`${totals.feedback.toLocaleString()} over ${data.total_weeks} weeks`}
          weeks={data.weeks}
          accessor={(w) => w.feedback_events}
          colorClass="stroke-amber-500 dark:stroke-amber-400"
          fillClass="fill-amber-100 dark:fill-amber-900/30"
        />
      </div>
    </div>
  );
}

/**
 * One time-series chart. SVG-based area chart with a stroked line
 * on top. ViewBox-based sizing means the chart scales fluidly into
 * its parent column width — no resize observer needed.
 *
 * 26-week window with a single line is a "just enough info" chart:
 * the shape is the signal (trending up? recent spike? steady
 * cadence?), exact weekly counts are available on hover via the
 * <title> per data point.
 */
function SeriesChart({
  title,
  subtitle,
  weeks,
  accessor,
  colorClass,
  fillClass,
}: {
  title: string;
  subtitle: string;
  weeks: TimeseriesWeek[];
  accessor: (w: TimeseriesWeek) => number;
  colorClass: string;
  fillClass: string;
}) {
  // Chart geometry. The viewBox aspect ratio matches the typical
  // rendered aspect ratio (≈4:1 at desktop 3-column layout × h-24)
  // so `preserveAspectRatio` can preserve the shape without leaving
  // large whitespace in most viewports.
  //
  // Previously this used preserveAspectRatio="none" which made the
  // chart stretch to fill its container — same path, different
  // shape at different widths. That's wrong for a time-series whose
  // signal IS the shape (steady cadence vs trending up vs recent
  // spike). At wide containers a real peak would visually flatten,
  // and on narrow containers a flat series would visually steepen.
  // Now the shape stays true regardless of container width.
  const W = 100;
  const H = 25;
  const PAD_T = 2;
  const PAD_B = 2;
  const usableH = H - PAD_T - PAD_B;

  const values = weeks.map(accessor);
  const maxVal = Math.max(1, ...values);
  const stepX = weeks.length > 1 ? W / (weeks.length - 1) : 0;

  // Build the line path AND a mirrored fill path for the soft area
  // beneath the line. The fill closes at y=H so it bottoms out at
  // the baseline.
  const linePoints = values.map((v, i) => {
    const x = i * stepX;
    const y = PAD_T + usableH - (v / maxVal) * usableH;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const linePath = `M ${linePoints.join(" L ")}`;
  // Fill path: same line, then bottom-right corner, bottom-left
  // corner, close.
  const fillPath = `${linePath} L ${(W).toFixed(2)},${H} L 0,${H} Z`;

  // Hover hit areas — invisible rects covering each data point.
  // Title attribute carries the precise count + week.
  const hoverRects = weeks.map((wk, i) => ({
    x: Math.max(0, i * stepX - stepX / 2),
    width: stepX || W,
    label: `${formatWeekLabel(wk.week_start)}: ${accessor(wk).toLocaleString()}`,
  }));

  // X-axis tick labels — first, last, and middle. With 26 weeks at
  // narrow widths, more ticks would crowd; three ticks span the
  // window without overlap.
  const firstWeek = weeks[0]?.week_start;
  const middleWeek = weeks[Math.floor(weeks.length / 2)]?.week_start;
  const lastWeek = weeks[weeks.length - 1]?.week_start;

  return (
    <div>
      <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
        {title}
      </div>
      <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5 mb-3">
        {subtitle}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="w-full h-24 overflow-visible"
        role="img"
        aria-label={`${title} weekly time series`}
      >
        {/* Area fill */}
        <path d={fillPath} className={fillClass} />
        {/* Line */}
        <path
          d={linePath}
          fill="none"
          strokeWidth={1.2}
          vectorEffect="non-scaling-stroke"
          className={colorClass}
        />
        {/* Hover hit areas — invisible but title-bearing */}
        {hoverRects.map((rect, i) => (
          <rect
            key={i}
            x={rect.x}
            y={0}
            width={rect.width}
            height={H}
            fill="transparent"
          >
            <title>{rect.label}</title>
          </rect>
        ))}
      </svg>
      {/* X-axis labels — three ticks (start / middle / end) sized
       *  to match the chart's visual extent. */}
      <div className="flex justify-between mt-1 text-[0.65rem] text-neutral-400 dark:text-neutral-500 tabular-nums">
        <span>{firstWeek ? formatWeekLabel(firstWeek) : ""}</span>
        <span>{middleWeek ? formatWeekLabel(middleWeek) : ""}</span>
        <span>{lastWeek ? formatWeekLabel(lastWeek) : ""}</span>
      </div>
    </div>
  );
}

/**
 * Format an ISO date as "MMM D" (e.g. "Jan 8"). Inline rather than
 * lifted to a util because date formatting in this codebase is
 * scattered already (some places use Intl.DateTimeFormat, some use
 * native Date methods); a single callsite doesn't earn shared util
 * status.
 */
function formatWeekLabel(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
