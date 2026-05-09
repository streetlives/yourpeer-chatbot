// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Canonical bucket-key constants shared between the admin frontend and
 * its verify scripts. Every distribution dict the backend emits in
 * `AdminStats` has a fixed set of keys defined in `audit_log.py`; this
 * module mirrors those sets so the frontend can reference them in one
 * place and the verify script (`scripts/verify/bucket-keys.mjs`) can
 * cross-check them against the backend source.
 *
 * If the backend bucket keys change:
 *   1. Update the corresponding constant here.
 *   2. Run `npm run verify:bucket-keys` — it'll fail if the backend
 *      file's literal dict doesn't match what's exported here.
 *
 * Why this is its own module (and not inline in operations-charts.tsx):
 *   - Single source of truth — multiple widgets and tests can import
 *     the same arrays; nobody has to remember to update two places.
 *   - The verify script can statically extract these constants and
 *     compare them to the backend, which would be awkward against an
 *     anonymous local in a render function.
 *   - Constants live in `lib/admin/` because that's where shared
 *     non-rendering admin utilities go (eval-dimensions.ts,
 *     metric-definitions.ts).
 *
 * The accompanying display labels (e.g. "1-3m" for the "1_3min" key)
 * stay in operations-charts.tsx — they're presentation, and the
 * frontend owning them is a deliberate decision (backend stays
 * presentation-agnostic). Only the canonical KEY ORDER lives here.
 */

/**
 * Turn-count distribution bucket keys, in canonical chronological order.
 * Backend source: `audit_log.py :: _compute_session_metrics` line ~727.
 */
export const TURN_COUNT_BUCKET_KEYS = [
  "1_turn",
  "2-3_turns",
  "4-6_turns",
  "7-10_turns",
  "11+_turns",
] as const;

export type TurnCountBucketKey = (typeof TURN_COUNT_BUCKET_KEYS)[number];

/**
 * Session-duration distribution bucket keys, in canonical chronological
 * order. Backend source: `audit_log.py :: _compute_session_duration`
 * line ~967.
 */
export const SESSION_DURATION_BUCKET_KEYS = [
  "under_1min",
  "1_3min",
  "3_7min",
  "7_15min",
  "over_15min",
] as const;

export type SessionDurationBucketKey = (typeof SESSION_DURATION_BUCKET_KEYS)[number];

/**
 * Crisis category canonical names. Backend source:
 * `crisis_detector.py :: _CRISIS_CATEGORIES`. Used in
 * `crises_by_category` and `crises_by_category_24h`. Note: the dict is
 * sparse (only categories that fired appear), and an "uncategorized"
 * fallback can also be present — these 8 are the canonical names the
 * detector emits, not an exhaustive list of what might appear in the
 * dict at runtime.
 */
export const CRISIS_CATEGORY_KEYS = [
  "suicide_self_harm",
  "medical_emergency",
  "domestic_violence",
  "youth_runaway",
  "assault_victim",
  "safety_concern",
  "trafficking",
  "violence",
] as const;

export type CrisisCategoryKey = (typeof CRISIS_CATEGORY_KEYS)[number];
