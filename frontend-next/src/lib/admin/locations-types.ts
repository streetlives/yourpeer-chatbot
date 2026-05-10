// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

/**
 * TypeScript types for the locations admin page endpoints.
 *
 * Mirrors the response shapes from the Python aggregation functions
 * in `backend/app/services/locations_admin/aggregations.py`. The
 * verify script `scripts/verify/locations-contract.mjs` locks the
 * surface so backend changes that alter these shapes can't ship
 * without an explicit frontend update.
 */

// -----------------------------------------------------------------
// Shared
// -----------------------------------------------------------------

export type BoroughLabel =
  | "Manhattan"
  | "Brooklyn"
  | "Queens"
  | "Bronx"
  | "Staten Island"
  | "Other";

export const NYC_BOROUGHS: ReadonlyArray<BoroughLabel> = [
  "Manhattan",
  "Brooklyn",
  "Queens",
  "Bronx",
  "Staten Island",
] as const;

// -----------------------------------------------------------------
// GET /api/admin/locations/stats — section 1 (top stat strip)
// -----------------------------------------------------------------

export interface LocationsStats {
  total_locations: number;
  total_services: number;
  /** Locations with last_validated_at within FRESHNESS_THRESHOLD_DAYS (90d). */
  fresh_count: number;
  /** Locations whose last_validated_at IS NULL — never verified. */
  never_verified_count: number;
  /** Locations whose last_validated_at is older than the freshness threshold. */
  stale_count: number;
  /** Distinct location_ids that have at least one location_feedback event ever. */
  with_feedback_count: number;
  /** 7-day-trend deltas. Positive = growth in the last 7d vs the prior 7d.
   *  total_locations is currently 0 — see backend comment. */
  trends: {
    total_locations: number;
    fresh_count: number;
    with_feedback_count: number;
  };
}

// -----------------------------------------------------------------
// GET /api/admin/locations/list — section 2b (triage table)
// -----------------------------------------------------------------

export type LocationsSortKey =
  | "name"
  | "organization"
  | "city"
  | "service_count"
  | "last_validated_at"
  | "recent_flags";

export type LocationsSortDir =
  | "asc"
  | "desc"
  | "asc_nulls_first"
  | "desc_nulls_first";

export type LocationsAgeBucket =
  | "lt30"
  | "30to90"
  | "90to180"
  | "180to365"
  | "gt365"
  | "never";

export interface LocationsListParams {
  page?: number;
  page_size?: number;
  sort_key?: LocationsSortKey;
  sort_dir?: LocationsSortDir;
  borough?: BoroughLabel[];
  age_bucket?: LocationsAgeBucket;
  has_issues?: boolean;
  category?: string[];
  search?: string;
}

export interface LocationsListRow {
  location_id: string;
  location_name: string;
  organization: string | null;
  city: string | null;
  borough: BoroughLabel;
  service_count: number;
  /** Top 3 distinct taxonomy names by service count at this location. */
  service_categories: string[];
  /** Number of additional distinct taxonomies beyond the top 3 shown. */
  service_categories_more: number;
  /** ISO8601 timestamp, or null if never validated. */
  last_validated_at: string | null;
  has_phone: boolean;
  has_address: boolean;
  has_hours: boolean;
  /** Any location_feedback event ever, regardless of recency. */
  has_reviews: boolean;
  /** Count of location_feedback events with at least one negative criterion
   *  within the last RECENT_FLAGS_LOOKBACK_DAYS (45d). */
  recent_flags: number;
  yourpeer_url: string;
}

export interface LocationsListResponse {
  locations: LocationsListRow[];
  total: number;
  page: number;
  page_size: number;
}

// -----------------------------------------------------------------
// GET /api/admin/locations/freshness-histogram — section 2a
// -----------------------------------------------------------------

/** Bucket key — canonical 6-bucket histogram, matching the
 *  age_bucket values accepted by /list so a click on a bar can
 *  drive the table filter without translation. */
export type FreshnessBucketKey =
  | "lt30"
  | "30to90"
  | "90to180"
  | "180to365"
  | "gt365"
  | "never";

export interface FreshnessHistogramBucket {
  key: FreshnessBucketKey;
  /** Display label, e.g. "<30d", "Never". Localizable in v2 if needed. */
  label: string;
  count: number;
}

export interface FreshnessHistogramResponse {
  /** Always 6 buckets in display order, regardless of which have data. */
  buckets: FreshnessHistogramBucket[];
  total: number;
}

// -----------------------------------------------------------------
// GET /api/admin/locations/by-borough — section 3a
// -----------------------------------------------------------------

export interface BoroughBreakdownRow {
  borough: BoroughLabel;
  location_count: number;
  service_count: number;
  /** Rounded to 1 decimal. 0 when location_count is 0. */
  avg_services_per_location: number;
  /** % of locations in this borough verified within 90 days.
   *  null when location_count is 0 (avoids 0/0 = NaN). */
  verified_lt90d_pct: number | null;
  /** Most-common taxonomy category in this borough; null when
   *  no services exist. */
  top_category: string | null;
}

export interface BoroughBreakdownResponse {
  /** Always 6 rows in canonical order (5 NYC boroughs + Other),
   *  regardless of which have data. Empty boroughs render as zeros. */
  rows: BoroughBreakdownRow[];
  totals: {
    location_count: number;
    service_count: number;
  };
}

// -----------------------------------------------------------------
// GET /api/admin/locations/heatmap — section 3b
// -----------------------------------------------------------------

export interface HeatmapCategory {
  name: string;
  /** Sum across all boroughs — drives default sort. */
  total_locations: number;
  /** Distinct location count per borough column. Always includes
   *  every borough label (5 NYC + Other) even when zero. */
  by_borough: Record<BoroughLabel, number>;
}

export interface HeatmapResponse {
  /** Sorted by total_locations DESC; equal totals tie-break by name. */
  categories: HeatmapCategory[];
  /** Column order for the heatmap UI — fixed 6-borough sequence. */
  boroughs: BoroughLabel[];
}

// -----------------------------------------------------------------
// GET /api/admin/locations/coordinate-issues — section 3c
// -----------------------------------------------------------------

export interface CoordinateIssue {
  location_id: string;
  location_name: string;
  organization: string | null;
  /** Raw pa.city as stored in the DB. */
  stated_city: string | null;
  /** stated_city mapped to a canonical NYC borough. null when the
   *  city doesn't map (e.g. an out-of-state city in pa.city). */
  stated_borough: string | null;
  /** Borough computed from coordinates via NYC DCP polygons. null
   *  when the coords fall outside NYC entirely (the "outside NYC"
   *  issue case — distinguishable from the borough-mismatch case
   *  by this null check). */
  computed_borough: string | null;
  latitude: number;
  longitude: number;
  yourpeer_url: string;
}

export interface CoordinateIssuesResponse {
  /** Outside-NYC issues sort first (most concerning), then
   *  borough-mismatch issues alphabetically. */
  issues: CoordinateIssue[];
  /** Total locations with non-null position — denominator for an
   *  "X of Y locations have issues" framing. */
  total_with_coords: number;
  /** Subset of `issues` where computed_borough === null. The frontend
   *  can compute this by filtering, but exposing it as an aggregate
   *  saves a re-walk and reads cleanly in copy. */
  outside_nyc_count: number;
}

// -----------------------------------------------------------------
// GET /api/admin/locations/category-coverage — section 4a
// -----------------------------------------------------------------

export interface CategoryCoverageRow {
  taxonomy_name: string;
  service_count: number;
  /** Distinct locations offering at least one service in this taxonomy. */
  location_count: number;
  /** Of those locations, how many are verified <90 days ago. */
  fresh_location_count: number;
  /** % of location_count that's fresh. null when location_count == 0. */
  verified_lt90d_pct: number | null;
  /** Best-effort attribution from chat-template demand via
   *  even-split across each template's covered taxonomies.
   *  May be a fractional float (e.g. 2/11 = 0.18). */
  demand_query_count: number;
  no_result_count: number;
  /** demand_no_result / demand_query when both nonzero, else null. */
  no_result_rate: number | null;
  /** demand_query_count / location_count, when both nonzero, else null.
   *  High = "users keep asking, supply is thin". */
  demand_supply_ratio: number | null;
}

export interface CategoryCoverageResponse {
  /** Sorted by demand_supply_ratio DESC NULLS LAST, then
   *  -location_count for stable tie-break in the null group. */
  categories: CategoryCoverageRow[];
  /** Demand from chat templates that don't map to a fixed taxonomy
   *  set (e.g. OtherServicesQuery, org-name searches). Surfaced
   *  separately so admins can see how much demand isn't attributable. */
  uncategorized_demand: {
    query_count: number;
    no_result_count: number;
  };
}

// -----------------------------------------------------------------
// GET /api/admin/locations/stale-categories — section 4b
// -----------------------------------------------------------------

export interface StaleCategoryRow {
  taxonomy_name: string;
  /** Number of locations offering this taxonomy. */
  location_count: number;
  /** Most recent last_validated_at across all locations offering this.
   *  null when every offering location has last_validated_at IS NULL. */
  max_verified_at: string | null;
  /** Days since `max_verified_at`. null when max_verified_at is null. */
  days_since_max_verified: number | null;
}

export interface StaleCategoriesResponse {
  /** Top-N categories where every offering location is stale.
   *  Sorted oldest-first so the most-needing-attention surfaces first. */
  categories: StaleCategoryRow[];
  /** Total count of stale categories across the catalog — may exceed
   *  categories.length when truncated. */
  total_stale: number;
  lookback_days: number;
}

// -----------------------------------------------------------------
// GET /api/admin/locations/feedback-aggregates — sections 5a + 5b
// -----------------------------------------------------------------

export type FeedbackCriterion =
  | "safety"
  | "friendliness"
  | "cleanliness"
  | "queer_friendly";

export const FEEDBACK_CRITERIA: ReadonlyArray<FeedbackCriterion> = [
  "safety",
  "friendliness",
  "cleanliness",
  "queer_friendly",
] as const;

/** Display label for each criterion — kept on the frontend so the
 *  presentation can change without backend coordination. */
export const FEEDBACK_CRITERION_LABELS: Record<FeedbackCriterion, string> = {
  safety: "Safety",
  friendliness: "Friendliness",
  cleanliness: "Cleanliness",
  queer_friendly: "Queer-friendly",
};

export interface CriterionCounts {
  positive: number;
  negative: number;
  /** total ratings on this criterion (positive + negative). */
  rated: number;
}

export interface MostFlaggedRow {
  location_id: string;
  /** May be null if every event on this location lacked a name
   *  (rare — the chat captures this from the service card title). */
  location_name: string | null;
  total_events: number;
  criterion_counts: Record<FeedbackCriterion, CriterionCounts>;
  /** Sum of False ratings across all four criteria. */
  negative_ratings_count: number;
  /** Sum of True+False ratings across all four criteria. */
  total_ratings_count: number;
  /** (neg + 1) / (total + 2) — Laplace smoothing. Used for the
   *  default sort order. */
  negative_ratio_smoothed: number;
  /** neg / total — surfaced for transparency alongside the smoothed
   *  ratio so admins can see what the "raw" signal looks like. */
  raw_negative_ratio: number;
  /** ISO8601 of the most-recent feedback event for this location. */
  last_event_at: string;
  /** Number of events with non-empty (non-whitespace) comments. */
  comments_count: number;
}

export interface CriterionSummaryRow {
  /** How many events rated this criterion (the denominator for any
   *  per-criterion analysis). */
  events_rated: number;
  positive: number;
  negative: number;
  /** negative / events_rated as a percentage. null when events_rated
   *  is 0 — distinguishes "this criterion isn't being rated" from
   *  "this criterion is consistently positive (0% negative)". */
  negative_pct: number | null;
}

export interface FeedbackAggregatesResponse {
  /** Top-N locations sorted by smoothed negative ratio. May be empty
   *  even when total_events_overall > 0 (if no location has reached
   *  the min_sample cutoff). */
  most_flagged: MostFlaggedRow[];
  /** Locations that meet the min_sample cutoff. May exceed
   *  most_flagged.length when there are more than MOST_FLAGGED_TOP_N
   *  qualifying locations. */
  total_eligible: number;
  /** Eligibility threshold (FEEDBACK_MIN_SAMPLE on the backend). */
  min_sample: number;
  /** Per-criterion population %: how often does each criterion get
   *  flagged across ALL feedback events? Always renders all four
   *  criteria, even when none have data. */
  criterion_summary: Record<FeedbackCriterion, CriterionSummaryRow>;
  /** Total location_feedback events seen — useful for "X events from
   *  Y locations" framing. */
  total_events_overall: number;
}

// -----------------------------------------------------------------
// API error shape — shared with other admin endpoints
// -----------------------------------------------------------------

export interface AdminApiError {
  error: true;
  endpoint: string;
  detail: string;
}

export function isAdminApiError(value: unknown): value is AdminApiError {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { error?: unknown }).error === true
  );
}
