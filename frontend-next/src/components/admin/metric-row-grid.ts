// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Grid template shared by `metric-row.tsx` and `metrics-section.tsx`.
 * The header row in MetricsSection and the data rows in MetricRow must
 * align column-for-column, so they need to use the same template.
 *
 * Five columns:
 *   1. Metric — name + subtitle stacked, takes the flex space so long
 *      subtitles ("% of post-result feedback that is positive (N
 *      responses so far)") fit on one line on wide viewports.
 *      `minmax(240px, 1fr)` keeps a sane floor on narrower screens.
 *   2. Target — `140px`, sized for the longest target string seen in
 *      practice ("Baseline tracking only"). Was previously the flex
 *      column, which left a large empty gap to its right on wide
 *      desktops.
 *   3. Current — `130px`, font-mono numeric value.
 *   4. Status — `170px`, pill. Was 110px and sized for the canonical
 *      labels ("✓ On target", "⚠ Watch", etc., all under ~14 chars),
 *      but `MetricRow.statusOverride` lets callers pass arbitrary
 *      content — and the longest current overrides ("n=12345 (low
 *      confidence)", "34% of sessions") push 16-24 chars. At 110px
 *      those wrapped to two lines, which made `rounded-full` render
 *      with oversized rounded ends (the radius is half the
 *      now-doubled height) and read as visually broken. 170px fits
 *      the longest seen override on a single line at text-xs with
 *      the pill's px-2 padding, so the rounded-full pill silhouette
 *      stays intact.
 *   5. Phase — `90px`, pill.
 *
 * Tailwind's JIT scanner only picks up class strings that appear literally
 * in source — that's why this is a const-string export (the literal lives
 * here) rather than a runtime computed value.
 */
export const METRIC_GRID_COLS =
  "grid-cols-[minmax(240px,1fr)_140px_130px_170px_90px]";
