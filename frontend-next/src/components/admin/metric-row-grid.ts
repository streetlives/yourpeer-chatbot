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
 * Five columns: name (240px) · subtitle (flex) · target (130px) · value
 * (110px) · status (90px).
 *
 * Tailwind's JIT scanner only picks up class strings that appear literally
 * in source — that's why this is a const-string export (the literal lives
 * here) rather than a runtime computed value.
 */
export const METRIC_GRID_COLS = "grid-cols-[240px_1fr_130px_110px_90px]";
