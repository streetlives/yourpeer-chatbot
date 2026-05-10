// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the locations-table sort cycle helpers.
 *
 * Run with: `npm run verify:locations-sort-cycle` from frontend-next/,
 * or `node scripts/verify/locations-sort-cycle.mjs` directly (Node 22+
 * required for built-in TS stripping).
 *
 * Why this script exists: v1 shipped with a sort-cycle bug in the
 * locations triage table — clicking the `last_validated_at` column
 * header cycled `asc_nulls_first → desc → asc → desc → asc → …` with
 * no path back to `asc_nulls_first`. The never-verified rows could
 * only be re-clustered by switching columns and switching back.
 *
 * The fix landed in `locations-sort-cycle.ts`. This script exercises
 * the cycle from every state on every column type and asserts the
 * full round-trip behavior. If anyone simplifies the cycle logic in
 * a way that breaks the round-trip, this fails before merge.
 *
 * What's checked:
 *   1. last_validated_at: full round-trip
 *      asc_nulls_first → desc → asc_nulls_first
 *   2. Other columns: full round-trip asc → desc → asc
 *   3. Defaults: last_validated_at defaults to asc_nulls_first;
 *      everything else defaults to asc
 *   4. Defensive fallback: cycling from a foreign state (e.g. asc
 *      while on last_validated_at) returns the column default
 */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  defaultSortDirForField,
  cycleSortDirForField,
} from "../../src/lib/admin/locations-sort-cycle.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Path resolution kept consistent with other verify scripts even
// though we don't read any files here — only ESM imports.
void __dirname;

let passed = 0;
let failed = 0;

function check(label, predicate, detail) {
  if (predicate) {
    console.log(`  ✓ ${label}`);
    passed += 1;
  } else {
    console.log(`  ✗ ${label}${detail ? `\n      ${detail}` : ""}`);
    failed += 1;
  }
}

function assertEqual(label, actual, expected) {
  check(
    `${label}: ${JSON.stringify(actual)} === ${JSON.stringify(expected)}`,
    actual === expected,
    actual !== expected ? `got ${JSON.stringify(actual)}` : "",
  );
}

console.log("\nLocations sort-cycle verification\n");

// -----------------------------------------------------------------------
// 1. last_validated_at — the column where the v1 bug lived
// -----------------------------------------------------------------------

console.log("last_validated_at cycle (the column where v1 bug lived):");
{
  // Start from the documented default state.
  const start = defaultSortDirForField("last_validated_at");
  assertEqual("default", start, "asc_nulls_first");

  const click1 = cycleSortDirForField("last_validated_at", start);
  assertEqual("first click (asc_nulls_first → ?)", click1, "desc");

  const click2 = cycleSortDirForField("last_validated_at", click1);
  // This is the critical assertion — v1's bug meant this returned
  // "asc" instead of "asc_nulls_first" and the cycle never recovered.
  assertEqual(
    "second click (desc → ? — THE CRITICAL ROUND-TRIP)",
    click2,
    "asc_nulls_first",
  );

  const click3 = cycleSortDirForField("last_validated_at", click2);
  assertEqual("third click (asc_nulls_first → ?)", click3, "desc");
}

// -----------------------------------------------------------------------
// 2. Other columns — plain asc/desc cycle
// -----------------------------------------------------------------------

console.log("\nOther columns cycle (asc ↔ desc):");
for (const field of ["name", "organization", "city", "service_count", "recent_flags"]) {
  const def = defaultSortDirForField(field);
  assertEqual(`${field}: default`, def, "asc");

  const click1 = cycleSortDirForField(field, def);
  assertEqual(`${field}: asc → ?`, click1, "desc");

  const click2 = cycleSortDirForField(field, click1);
  assertEqual(`${field}: desc → ? (round-trip back to asc)`, click2, "asc");
}

// -----------------------------------------------------------------------
// 3. Defensive fallback — foreign state lands on column default
// -----------------------------------------------------------------------

console.log("\nDefensive fallback (foreign state → column default):");
{
  // If we somehow arrived at last_validated_at with an `asc` state
  // (a plain-asc value that doesn't belong to its nulls-first cycle),
  // the next click should re-establish the canonical default rather
  // than spinning in a half-broken state.
  const recover = cycleSortDirForField("last_validated_at", "asc");
  assertEqual(
    "last_validated_at recovers from foreign 'asc'",
    recover,
    "asc_nulls_first",
  );

  // Same for the other direction — landing on asc_nulls_first while
  // sorted by `name` should recover to that column's default.
  const recover2 = cycleSortDirForField("name", "asc_nulls_first");
  assertEqual("name recovers from foreign 'asc_nulls_first'", recover2, "asc");
}

// -----------------------------------------------------------------------
// 4. Round-trip property — every column eventually returns to default
// -----------------------------------------------------------------------

console.log("\nRound-trip property (default eventually re-reached):");
for (const field of [
  "name",
  "organization",
  "city",
  "service_count",
  "last_validated_at",
  "recent_flags",
]) {
  const def = defaultSortDirForField(field);
  let dir = def;
  let reachedAgain = false;
  // Up to 5 clicks. For both cycle shapes (2-state and 2-state), the
  // default should be re-reached within 2-4 clicks. 5 is generous.
  for (let i = 0; i < 5; i += 1) {
    dir = cycleSortDirForField(field, dir);
    if (dir === def) {
      reachedAgain = true;
      break;
    }
  }
  check(
    `${field}: default '${def}' re-reached within 5 clicks`,
    reachedAgain,
    `current dir after 5 clicks: ${dir}`,
  );
}

// -----------------------------------------------------------------------
// 5. Cycle-length cap — pin the EXACT click count to round-trip
// -----------------------------------------------------------------------
//
// The "5 clicks" check above is generous and catches catastrophic
// regressions (infinite cycles, broken paths back). This block is
// stricter: it pins the exact cycle length for each field shape. If
// someone introduces a 3-state cycle on a column expected to be
// 2-state, the round-trip check still passes (3 ≤ 5) but the cycle
// is now twice as many clicks as users learned. That'd be a usability
// regression; this assertion catches it.
//
// Expected cycle lengths:
//   last_validated_at: 2 (asc_nulls_first ↔ desc)
//   all others:        2 (asc ↔ desc)
// All columns currently use 2-state cycles. If we ever introduce a
// 3-state cycle (e.g. asc → desc → unsorted → asc), update both
// the cycle helper AND this assertion together.

function clicksToRoundTrip(field) {
  const def = defaultSortDirForField(field);
  let dir = def;
  for (let i = 1; i <= 10; i += 1) {
    dir = cycleSortDirForField(field, dir);
    if (dir === def) return i;
  }
  return -1; // infinite cycle or broken — should never happen
}

console.log("\nCycle length cap (exact click count, not just 'eventually'):");
{
  const lastValClicks = clicksToRoundTrip("last_validated_at");
  assertEqual("last_validated_at cycle length", lastValClicks, 2);

  for (const field of ["name", "organization", "city", "service_count", "recent_flags"]) {
    assertEqual(`${field} cycle length`, clicksToRoundTrip(field), 2);
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
