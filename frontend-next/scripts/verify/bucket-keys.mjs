// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for `src/lib/admin/bucket-keys.ts`.
 *
 * Run with: `npm run verify:bucket-keys` from frontend-next/, or
 * `node scripts/verify/bucket-keys.mjs` directly (Node 22+ required
 * for built-in TS stripping).
 *
 * Why this exists: the operations-charts.tsx widgets read
 * `Record<string, number>` distribution dicts from the backend's
 * AdminStats payload and look up specific keys (e.g. "1_turn",
 * "under_1min"). TypeScript's structural typing can't catch a wrong
 * key — `Record<string, number>` accepts any string. We saw this fail
 * twice in the same week (the bucket-key bugs from the May 2026
 * audit), where the frontend was looking up keys the backend never
 * emits, and the chart silently degraded to a fallback path that
 * rendered raw snake-case bucket names instead of nice canonical labels.
 *
 * This script enforces the contract by parsing the backend's literal
 * dict definitions and comparing them to the canonical KEYS exported
 * from `src/lib/admin/bucket-keys.ts`. If they drift, this fails with
 * a clear message pointing at both files.
 *
 * Backend source files are parsed with regex rather than executed via
 * Python — the verify scripts run under Node, and shelling out to
 * Python in CI is more brittle than just reading the file. Python
 * dict literals like `{"1_turn": 0, "2-3_turns": 0, ...}` are stable
 * enough to extract reliably; the regex tolerates whitespace and
 * trailing commas. If the backend ever moves to non-literal bucket
 * construction (a function call, a constant from elsewhere), we'd
 * need to update the regex — but that's a deliberate refactor, not a
 * silent drift.
 *
 * Cases:
 *   1. Turn-count bucket keys match `_compute_session_metrics` in
 *      audit_log.py.
 *   2. Session-duration bucket keys match `_compute_session_duration`
 *      in audit_log.py.
 *   3. Crisis category names match `_CRISIS_CATEGORIES` in
 *      crisis_detector.py.
 *   4. Order matches — not just set equality. The frontend renders
 *      canonical-order bars by walking ORDER; if the backend reorders
 *      its dict literal, the chart silently follows. Order test
 *      catches that.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  TURN_COUNT_BUCKET_KEYS,
  SESSION_DURATION_BUCKET_KEYS,
  CRISIS_CATEGORY_KEYS,
} from "../../src/lib/admin/bucket-keys.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "../../..");

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

/**
 * Extract the keys from a Python dict literal that follows a marker
 * pattern. The marker is the `buckets = {` line; we read until the
 * matching closing brace, then pull each key string.
 *
 * Returns the keys in source order. If the marker doesn't match,
 * returns null — the caller should treat that as a parse failure
 * and fail the test loudly (rather than silently returning [] and
 * letting an empty-set comparison pass).
 */
function extractDictKeys(source, markerPattern) {
  const startMatch = source.match(markerPattern);
  if (!startMatch) return null;
  const startIdx = startMatch.index + startMatch[0].length;
  // Find the matching closing brace. The dict literals here don't
  // contain nested braces — if the backend ever introduces nested
  // dicts in these specific buckets, this needs revisiting.
  const endIdx = source.indexOf("}", startIdx);
  if (endIdx === -1) return null;
  const body = source.slice(startIdx, endIdx);
  // Pull each "key": value pair. Keys can contain digits, underscores,
  // hyphens, and pluses (e.g. "11+_turns").
  const keyMatches = [...body.matchAll(/"([^"]+)":/g)];
  return keyMatches.map((m) => m[1]);
}

/**
 * Extract the canonical category names from the
 * `_CRISIS_CATEGORIES` list of tuples in crisis_detector.py. Each
 * tuple is `("category_name", phrases, response)`; we just want the
 * first string.
 */
function extractCrisisCategories(source) {
  const startMatch = source.match(/_CRISIS_CATEGORIES\s*=\s*\[/);
  if (!startMatch) return null;
  const startIdx = startMatch.index + startMatch[0].length;
  // Find the matching closing bracket. The list contains tuples; no
  // nested square brackets inside.
  const endIdx = source.indexOf("]", startIdx);
  if (endIdx === -1) return null;
  const body = source.slice(startIdx, endIdx);
  // Each tuple opens with `("name",`. Pull the first string from
  // each tuple. The phrase lists and response constants in slot 2/3
  // are identifiers, not strings, so this is unambiguous.
  const tupleMatches = [...body.matchAll(/\(\s*"([^"]+)"\s*,/g)];
  return tupleMatches.map((m) => m[1]);
}

// ---------------------------------------------------------------------
// Read backend source files
// ---------------------------------------------------------------------

const auditLogPath = resolve(REPO_ROOT, "backend/app/services/audit_log.py");
const crisisDetectorPath = resolve(REPO_ROOT, "backend/app/services/crisis_detector.py");

let auditLogSource;
let crisisDetectorSource;
try {
  auditLogSource = readFileSync(auditLogPath, "utf8");
  crisisDetectorSource = readFileSync(crisisDetectorPath, "utf8");
} catch (err) {
  console.error(`Couldn't read backend source: ${err.message}`);
  console.error(`Tried: ${auditLogPath}`);
  console.error(`       ${crisisDetectorPath}`);
  console.error("This script must run from the frontend-next directory and");
  console.error("expects the standard repo layout (../../backend/...).");
  process.exit(2);
}

// ---------------------------------------------------------------------
// 1. Turn-count buckets
// ---------------------------------------------------------------------
console.log("Turn-count bucket keys:");

// Match the line `buckets = {"1_turn": 0,` — anchor on the first
// turn-count key to avoid matching the duration-buckets dict, which
// uses a similar pattern but appears later in the file.
const turnBucketsBackend = extractDictKeys(
  auditLogSource,
  /buckets\s*=\s*\{(?=\s*"1_turn")/,
);

check(
  "Turn-count dict literal found in audit_log.py",
  turnBucketsBackend !== null,
  `Looked for /buckets\\s*=\\s*\\{(?=\\s*"1_turn")/ — not found. ` +
    `Either the dict literal style changed, or this regex needs updating.`,
);

if (turnBucketsBackend) {
  const frontend = [...TURN_COUNT_BUCKET_KEYS];
  check(
    `frontend (${frontend.length} keys) matches backend (${turnBucketsBackend.length} keys)`,
    frontend.length === turnBucketsBackend.length,
    `frontend: [${frontend.join(", ")}]\n      backend:  [${turnBucketsBackend.join(", ")}]`,
  );

  // Order-sensitive comparison. The frontend renders bars in this
  // order via the ORDER constant; if the backend ever changes the
  // dict-literal order without updating the frontend, the chart's
  // visual order would drift.
  for (let i = 0; i < Math.max(frontend.length, turnBucketsBackend.length); i += 1) {
    const fe = frontend[i] ?? "(missing)";
    const be = turnBucketsBackend[i] ?? "(missing)";
    check(
      `position ${i}: frontend "${fe}" === backend "${be}"`,
      fe === be,
    );
  }
}

// ---------------------------------------------------------------------
// 2. Session-duration buckets
// ---------------------------------------------------------------------
console.log("\nSession-duration bucket keys:");

const durationBucketsBackend = extractDictKeys(
  auditLogSource,
  /buckets\s*=\s*\{(?=\s*"under_1min")/,
);

check(
  "Duration dict literal found in audit_log.py",
  durationBucketsBackend !== null,
  `Looked for /buckets\\s*=\\s*\\{(?=\\s*"under_1min")/ — not found.`,
);

if (durationBucketsBackend) {
  const frontend = [...SESSION_DURATION_BUCKET_KEYS];
  check(
    `frontend (${frontend.length} keys) matches backend (${durationBucketsBackend.length} keys)`,
    frontend.length === durationBucketsBackend.length,
    `frontend: [${frontend.join(", ")}]\n      backend:  [${durationBucketsBackend.join(", ")}]`,
  );
  for (let i = 0; i < Math.max(frontend.length, durationBucketsBackend.length); i += 1) {
    const fe = frontend[i] ?? "(missing)";
    const be = durationBucketsBackend[i] ?? "(missing)";
    check(
      `position ${i}: frontend "${fe}" === backend "${be}"`,
      fe === be,
    );
  }
}

// ---------------------------------------------------------------------
// 3. Crisis category names
// ---------------------------------------------------------------------
console.log("\nCrisis category names:");

const crisisCategoriesBackend = extractCrisisCategories(crisisDetectorSource);

check(
  "_CRISIS_CATEGORIES list found in crisis_detector.py",
  crisisCategoriesBackend !== null,
);

if (crisisCategoriesBackend) {
  const frontend = [...CRISIS_CATEGORY_KEYS];
  // Order-sensitive — `_CRISIS_CATEGORIES` is documented as
  // "more specific categories checked first," so the order is
  // semantic. If the backend reorders, the frontend should too,
  // even though the frontend doesn't currently use the order
  // (display sorts by count). Catching reorders preserves the
  // invariant for any future code that does care about order.
  check(
    `frontend (${frontend.length} keys) matches backend (${crisisCategoriesBackend.length} keys)`,
    frontend.length === crisisCategoriesBackend.length,
    `frontend: [${frontend.join(", ")}]\n      backend:  [${crisisCategoriesBackend.join(", ")}]`,
  );
  for (let i = 0; i < Math.max(frontend.length, crisisCategoriesBackend.length); i += 1) {
    const fe = frontend[i] ?? "(missing)";
    const be = crisisCategoriesBackend[i] ?? "(missing)";
    check(
      `position ${i}: frontend "${fe}" === backend "${be}"`,
      fe === be,
    );
  }
}

// ---------------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
