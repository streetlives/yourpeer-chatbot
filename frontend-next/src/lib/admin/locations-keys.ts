// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

/**
 * Canonical key arrays for the Locations admin page.
 *
 * Mirrors the same pattern as `bucket-keys.ts` and `contract-keys.ts`:
 * single source of truth for the strings the frontend reads from the
 * backend's response shapes, paired with a verify script
 * (`scripts/verify/locations-contract.mjs`) that cross-checks them
 * against the backend source.
 *
 * If a key set here drifts from the backend, the verify script fails
 * with a clear message pointing at both files. That's a more useful
 * diagnostic than a silently-empty heatmap or a missing badge.
 *
 * What's pinned here (and why):
 *   - LOCATIONS_AGE_BUCKET_KEYS — keys the freshness histogram emits
 *     and that the locations table accepts as the `age_bucket` query
 *     param. Cross-table contract: histogram clicks set the table's
 *     filter, so a backend rename would break the click-through.
 *
 *   - LOCATIONS_BOROUGH_LABELS — the 6 column headers of the borough
 *     table and heatmap. Backend emits these as both the keys of
 *     `by_borough` and the values of `boroughs[]`; frontend reads them
 *     to render columns.
 *
 *   - FEEDBACK_CRITERIA_KEYS — the 4 strings used as keys in the
 *     feedback `criterion_summary` and `criterion_counts` dicts.
 *     Frontend has its own runtime constant FEEDBACK_CRITERIA in
 *     `locations-types.ts`; this duplicate exists for the verify
 *     script to read statically.
 *
 *   - INTEGRITY_CALLOUT_IDS — the 5 stable ids the integrity-callouts
 *     endpoint emits. Frontend types them as a literal union; the
 *     verify check is belt-and-suspenders for runtime payloads.
 *
 * What's NOT pinned (and why):
 *   - Service-borough heatmap category counts: dynamic from DB, not a
 *     fixed string set.
 *   - Coordinate-issue row shapes: the test suite covers these
 *     end-to-end; type drift would surface as a TS compile error.
 *   - Feedback comment criterion arrays: derived from
 *     FEEDBACK_CRITERIA_KEYS — pinning them separately would
 *     duplicate the same check.
 */

/**
 * Age-bucket keys that drive the freshness histogram and the locations
 * table's age_bucket filter param. ORDER IS SIGNIFICANT — the
 * histogram renders bars in this order. Mirrors AGE_BUCKETS in
 * backend/app/services/locations_admin/aggregations.py.
 */
export const LOCATIONS_AGE_BUCKET_KEYS = [
  "lt30",
  "30to90",
  "90to180",
  "180to365",
  "gt365",
  "never",
] as const;

export type LocationsAgeBucketKey = (typeof LOCATIONS_AGE_BUCKET_KEYS)[number];

/**
 * The 6 borough labels emitted by the borough-breakdown and heatmap
 * endpoints. ORDER IS SIGNIFICANT — both tables render columns in
 * this order. Mirrors NYC_BOROUGHS + "Other" in
 * backend/app/services/locations_admin/aggregations.py.
 */
export const LOCATIONS_BOROUGH_LABELS = [
  "Manhattan",
  "Brooklyn",
  "Queens",
  "Bronx",
  "Staten Island",
  "Other",
] as const;

export type LocationsBoroughLabel = (typeof LOCATIONS_BOROUGH_LABELS)[number];

/**
 * The 4 feedback criteria. ORDER IS SIGNIFICANT — frontend renders
 * negative_criteria / positive_criteria badge rows in this order.
 * Mirrors _FEEDBACK_CRITERIA in
 * backend/app/services/locations_admin/aggregations.py.
 *
 * NB: also exported as FEEDBACK_CRITERIA from locations-types.ts
 * for runtime use. Duplicating here lets the verify script read
 * a static array without reaching into types.ts (which has many
 * other concerns).
 */
export const FEEDBACK_CRITERIA_KEYS = [
  "safety",
  "friendliness",
  "cleanliness",
  "queer_friendly",
] as const;

export type FeedbackCriterionKey = (typeof FEEDBACK_CRITERIA_KEYS)[number];

/**
 * Stable ids for the data-integrity callouts. Frontend types these
 * as a literal union on `IntegrityCallout.id`; verify ensures the
 * runtime payload also matches.
 *
 * Order doesn't matter here (frontend doesn't iterate the array;
 * each id is a switch-case key). Set-equality check is enough.
 */
export const INTEGRITY_CALLOUT_IDS = [
  "orphaned_locations",
  "orphaned_services",
  "malformed_phones",
  "entity_encoded_html",
  "coordinate_issues_ref",
] as const;

export type IntegrityCalloutId = (typeof INTEGRITY_CALLOUT_IDS)[number];
