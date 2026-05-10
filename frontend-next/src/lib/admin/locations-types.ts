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
