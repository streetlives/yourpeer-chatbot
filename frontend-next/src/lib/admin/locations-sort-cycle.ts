// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

import type {
  LocationsSortKey,
  LocationsSortDir,
} from "./locations-types";

/**
 * Sort-cycle helpers for the Locations admin triage table.
 *
 * Extracted into its own module so:
 *   1. The locations-table component can import them cleanly.
 *   2. The verify script (`scripts/verify/locations-sort-cycle.mjs`)
 *      can exercise the cycle without React rendering — pure
 *      functions, plain Node import.
 *
 * The cycle is column-specific:
 *   • last_validated_at: asc_nulls_first ↔ desc. In both directions
 *     never-verified rows stay clustered at one end (the backend's
 *     `desc` maps to "DESC NULLS LAST"). This column has a meaningful
 *     "missing" category that admins want grouped, not interleaved.
 *   • All other columns: asc ↔ desc. No nulls in practice; nulls
 *     handling would be visual noise.
 *
 * Background — the bug this module exists to prevent:
 * v1 shipped with a sort cycle that went
 * `asc_nulls_first → desc → asc → desc → asc → …` with no path back
 * to `asc_nulls_first`. The never-verified rows could only be
 * re-clustered by switching columns and switching back. The fix and
 * the verify check landed together; future "simplifications" of the
 * cycle logic should run the verify before merging.
 */

/**
 * The default sort direction for `field` when the user first switches
 * to that column. Splits last_validated_at (nulls-first default) from
 * every other column (plain asc).
 */
export function defaultSortDirForField(
  field: LocationsSortKey,
): LocationsSortDir {
  return field === "last_validated_at" ? "asc_nulls_first" : "asc";
}

/**
 * The next direction in the cycle for a clicked column-header. See
 * the module docstring for the column-specific cycle definitions.
 *
 * If the current `d` doesn't belong to the column's cycle (e.g. we
 * just switched columns and inherited a foreign direction), fall back
 * to the column's default direction. Defensive — shouldn't happen
 * given the table's handleSortClick sets a column-appropriate default
 * on switch.
 */
export function cycleSortDirForField(
  field: LocationsSortKey,
  d: LocationsSortDir,
): LocationsSortDir {
  if (field === "last_validated_at") {
    if (d === "asc_nulls_first") return "desc";
    if (d === "desc") return "asc_nulls_first";
    return "asc_nulls_first";
  }
  if (d === "asc") return "desc";
  if (d === "desc") return "asc";
  return "asc";
}
