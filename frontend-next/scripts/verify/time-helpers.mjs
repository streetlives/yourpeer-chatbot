// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the ET↔UTC hour helpers in
 * `src/lib/admin/format-time.ts`.
 *
 * Run with: `npm run verify:time` from frontend-next/, or
 * `node scripts/verify/time-helpers.mjs` directly.
 *
 * The Operations chart on the admin Overview reads UTC-bucketed
 * hourly counts and renders them in ET order. A miscompute here
 * silently rotates the chart by 5 hours (winter) or 4 (summer) —
 * exactly the bug we hit on the first version of the When widget,
 * and exactly the bug a unit test would have caught.
 *
 * Asserts:
 *   1. utcHourToET → etHourToUtcHour round-trip is identity for all
 *      24 hours (every ET hour maps back to exactly one UTC hour).
 *   2. The mapping is one-to-one (no two ET hours collide on the same
 *      UTC hour).
 *   3. The mapping is monotonic-with-wrap (ET hour 0 maps to a
 *      specific UTC offset; consecutive ET hours map to consecutive
 *      UTC hours modulo 24).
 *   4. Out-of-range etHour input returns 0 rather than NaN/throw.
 *
 * No actual unit framework — this is a small standalone script
 * matching the project's existing verify pattern. If/when a frontend
 * test runner is added, these can become real tests.
 */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// Inline the helpers rather than import — the source is TypeScript and
// this script runs as plain ESM JS. Keep this in lockstep with
// `src/lib/admin/format-time.ts` (utcHourToET + etHourToUtcHour). If
// the source diverges from this, a fix in one file should be mirrored
// here so the verification continues to mean something.
const NYC_TZ = "America/New_York";

function utcHourToET(utcHour) {
  const now = new Date();
  const d = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), utcHour, 0, 0),
  );
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    timeZone: NYC_TZ,
  });
}

function etHourToUtcHour(etHour) {
  if (etHour < 0 || etHour > 23 || !Number.isInteger(etHour)) return 0;
  const now = new Date();
  for (let utc = 0; utc < 24; utc += 1) {
    const d = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), utc, 0, 0),
    );
    const etLabel = d.toLocaleTimeString("en-US", {
      timeZone: NYC_TZ,
      hour: "numeric",
      hour12: false,
    });
    const etHourFromLabel = parseInt(etLabel, 10) % 24;
    if (etHourFromLabel === etHour) return utc;
  }
  return 0;
}

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

// ---------------------------------------------------------------------
// 1. Round-trip identity
// ---------------------------------------------------------------------
console.log("Round-trip identity:");
for (let etH = 0; etH < 24; etH += 1) {
  const utc = etHourToUtcHour(etH);
  // Read the UTC hour back as an ET label, parse the hour out.
  const label = utcHourToET(utc);
  // utcHourToET returns "9 AM" / "12 PM" / "11 PM" — convert back to
  // 24h via Date parsing.
  const parsed = new Date(`${label} GMT`);
  const recoveredHour =
    label.includes("12 AM") ? 0 :
      label.includes("12 PM") ? 12 :
        label.includes("AM") ? parseInt(label, 10) :
          parseInt(label, 10) + 12;
  check(
    `ET hour ${etH} round-trips through UTC ${utc} ("${label}")`,
    recoveredHour === etH,
    `expected ${etH}, got ${recoveredHour} from label "${label}"`,
  );
}

// ---------------------------------------------------------------------
// 2. Mapping is one-to-one (24 distinct UTC hours)
// ---------------------------------------------------------------------
console.log("\nMapping is one-to-one:");
const utcHours = new Set();
for (let etH = 0; etH < 24; etH += 1) {
  utcHours.add(etHourToUtcHour(etH));
}
check(
  `24 ET hours map to 24 distinct UTC hours`,
  utcHours.size === 24,
  `got ${utcHours.size} distinct UTC hours`,
);

// ---------------------------------------------------------------------
// 3. Monotonic-with-wrap: consecutive ET hours map to consecutive UTC
//    hours (modulo 24). This catches bugs where the inverse function
//    drifts mid-day.
// ---------------------------------------------------------------------
console.log("\nMonotonic-with-wrap:");
for (let etH = 0; etH < 24; etH += 1) {
  const utcThis = etHourToUtcHour(etH);
  const utcNext = etHourToUtcHour((etH + 1) % 24);
  const expectedNext = (utcThis + 1) % 24;
  check(
    `ET ${etH} → UTC ${utcThis}, ET ${(etH + 1) % 24} → UTC ${utcNext} (consecutive)`,
    utcNext === expectedNext,
    `expected ${expectedNext}, got ${utcNext}`,
  );
}

// ---------------------------------------------------------------------
// 4. Out-of-range handling
// ---------------------------------------------------------------------
console.log("\nOut-of-range handling:");
check("etHourToUtcHour(-1) returns 0", etHourToUtcHour(-1) === 0);
check("etHourToUtcHour(24) returns 0", etHourToUtcHour(24) === 0);
check("etHourToUtcHour(2.5) returns 0", etHourToUtcHour(2.5) === 0);
check("etHourToUtcHour(NaN) returns 0", etHourToUtcHour(NaN) === 0);

// ---------------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
