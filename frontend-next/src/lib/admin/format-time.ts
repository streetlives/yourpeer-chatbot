// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Format a timestamp as a relative time string ("12m ago", "2h ago",
 * "yesterday 3:14 PM", "Apr 28 9:21 AM").
 *
 * Used in the event feed and query log where rows can span multiple days
 * but most are recent. Showing absolute time-of-day alone (the previous
 * pattern) made yesterday's events visually indistinguishable from
 * today's. Showing full date+time on every row is bulky.
 *
 * Relative time, with the absolute timestamp on hover via the consumer's
 * `title` attribute, is the standard observability-tool idiom.
 *
 * Returns the input verbatim if it can't be parsed as a Date.
 */

const NYC_TZ = "America/New_York";

export function formatRelativeTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;

  const now = new Date();
  const deltaMs = now.getTime() - date.getTime();
  const deltaSec = Math.floor(deltaMs / 1000);

  if (deltaSec < 0) {
    // Future timestamp — fall through to absolute formatting.
    return formatAbsolute(date, /* includeYear */ now.getFullYear() !== date.getFullYear());
  }

  if (deltaSec < 45) return "just now";
  if (deltaSec < 90) return "1m ago";

  const deltaMin = Math.floor(deltaSec / 60);
  if (deltaMin < 60) return `${deltaMin}m ago`;

  const deltaHr = Math.floor(deltaMin / 60);
  if (deltaHr < 24 && isSameLocalDay(now, date)) return `${deltaHr}h ago`;

  // Yesterday → "yesterday 3:14 PM"
  if (isYesterdayLocal(now, date)) {
    return `yesterday ${formatTimeOfDay(date)}`;
  }

  // Within current year → "Apr 28 9:21 AM"
  // Older than current year → "Apr 28 2024"
  const sameYear = now.getFullYear() === date.getFullYear();
  return formatAbsolute(date, /* includeYear */ !sameYear);
}

/**
 * Absolute-format helper for use in the consumer's `title` attribute.
 * Always full date + time + zone, suitable for tooltips.
 */
export function formatAbsoluteTooltip(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString("en-US", {
    timeZone: NYC_TZ,
    dateStyle: "medium",
    timeStyle: "long",
  });
}

// ---------------------------------------------------------------------------
// Public helpers
// ---------------------------------------------------------------------------

/**
 * Format a timestamp as just the time-of-day in NYC-local time, with
 * second precision (e.g. "9:21:34 AM"). Used by views like the
 * transcript drawer where multiple turns can fire within the same
 * minute and the order matters. For minute-precision (event feed,
 * query log), use `formatRelativeTime` for the visible cell and rely
 * on `formatAbsoluteTooltip` for hover detail.
 */
export function formatTimeOfDayWithSeconds(timestamp: string): string {
  try {
    return new Date(timestamp).toLocaleTimeString("en-US", {
      timeZone: NYC_TZ,
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    // toLocaleTimeString rarely throws on modern engines, but a malformed
    // timestamp returning an Invalid Date would render "Invalid Date"
    // through it. Falling back to the raw input keeps the UI from showing
    // that nonsense — the drawer can render the original ISO string
    // instead, which at least lets a viewer reason about the issue.
    return timestamp;
  }
}

// ---------------------------------------------------------------------------
// Hour-of-day formatting
// ---------------------------------------------------------------------------

/**
 * Convert a UTC hour (0–23) to an Eastern-Time hour label like "9 AM".
 *
 * Time-of-day stats arrive from the backend as UTC hour buckets (the
 * `time_of_day.hourly` field on AdminStats). The dashboard renders in
 * NYC time so admins can read the chart against the city they're
 * staffing. This function does the conversion, anchored to "today" so
 * DST shifts at the right moment of the year.
 *
 * Anchoring matters: UTC hour 13 lands at 9 AM ET in winter (UTC−5)
 * and 9 AM EDT in summer (UTC−4). Using `now` as the anchor means the
 * label is correct for the current viewing context. If we instead used
 * a fixed reference date, half the year would be off by an hour.
 *
 * Used by:
 *   - admin/metrics — Section 7 Hourly Distribution row
 *   - admin/overview — When-bar widget
 */
export function utcHourToET(utcHour: number): string {
  const now = new Date();
  const d = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), utcHour, 0, 0),
  );
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    timeZone: NYC_TZ,
  });
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function formatTimeOfDay(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    timeZone: NYC_TZ,
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatAbsolute(date: Date, includeYear: boolean): string {
  const datePart = date.toLocaleDateString("en-US", {
    timeZone: NYC_TZ,
    month: "short",
    day: "numeric",
    ...(includeYear ? { year: "numeric" } : {}),
  });
  return `${datePart} ${formatTimeOfDay(date)}`;
}

/**
 * Compare two Date objects in NYC-local terms. We can't naively use
 * `getDate()` because that returns the user's local-machine day, not
 * NYC's. Format both as a YYYY-MM-DD NYC string and compare.
 */
function localDayKey(d: Date): string {
  return d.toLocaleDateString("en-CA", { timeZone: NYC_TZ });
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return localDayKey(a) === localDayKey(b);
}

function isYesterdayLocal(now: Date, date: Date): boolean {
  // 24 hours ago in local-NYC terms. Subtract a day, then check same-day.
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  return isSameLocalDay(yesterday, date);
}
