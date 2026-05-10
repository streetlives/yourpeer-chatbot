// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the Locations admin section's
 * Python ↔ TypeScript contracts.
 *
 * Run with: `npm run verify:locations-contract` from frontend-next/, or
 * `node scripts/verify/locations-contract.mjs` directly (Node 22+
 * required for built-in TS stripping).
 *
 * Why this script exists: the Locations admin page has 12 components
 * across 11 sections, all reading typed responses from 12 backend
 * endpoints. The TypeScript types pin many shapes at compile time
 * (literal unions, etc.) but several string sets are only enforced as
 * conventions:
 *
 *   1. Age-bucket keys ("lt30", "30to90", ...) — the freshness
 *      histogram emits these AND the locations table accepts them as
 *      a query param. A backend rename would silently break the
 *      histogram → table click-through.
 *
 *   2. Borough labels ("Manhattan", "Brooklyn", ...) — the borough
 *      breakdown table's rows AND the heatmap's columns rely on
 *      these. A backend typo or rename would silently render an
 *      "Other" bucket where data should be in a named borough.
 *
 *   3. Feedback criteria ("safety", "friendliness", "cleanliness",
 *      "queer_friendly") — the criterion-summary panel renders
 *      cards keyed by these strings; the criterion_counts micro-bars
 *      in the most-flagged table read by these names. A backend
 *      criteria-list change would silently produce empty cards.
 *
 *   4. Integrity callout IDs — the 5 `id` strings emitted by the
 *      data-integrity-callouts endpoint. Frontend types these as a
 *      literal union (compile-protected) but a runtime mismatch
 *      could still send a callout to the default branch.
 *
 * Each section follows the same shape as bucket-keys.mjs and
 * contract.mjs: read backend source, regex-extract the canonical
 * set, compare to the frontend's exported constant. Order-sensitive
 * for histogram bucket keys + borough labels (frontend renders by
 * walking the array); set-equality for criteria + callout IDs
 * (frontend looks each up by name).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  LOCATIONS_AGE_BUCKET_KEYS,
  LOCATIONS_BOROUGH_LABELS,
  FEEDBACK_CRITERIA_KEYS,
  INTEGRITY_CALLOUT_IDS,
} from "../../src/lib/admin/locations-keys.ts";

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

function readBackend(relPath) {
  const path = resolve(REPO_ROOT, relPath);
  try {
    return readFileSync(path, "utf8");
  } catch (err) {
    console.error(`Couldn't read ${relPath}: ${err.message}`);
    console.error(`Tried: ${path}`);
    process.exit(2);
  }
}

/**
 * Compare two arrays in order. Reports each position separately so a
 * single drift surfaces with location and value, not just "the lists
 * differ." Used where order is semantic.
 */
function checkOrdered(sectionLabel, frontend, backend) {
  check(
    `${sectionLabel}: count matches (${frontend.length} vs ${backend.length})`,
    frontend.length === backend.length,
    `frontend: [${frontend.join(", ")}]\n      backend:  [${backend.join(", ")}]`,
  );
  const max = Math.max(frontend.length, backend.length);
  for (let i = 0; i < max; i += 1) {
    const fe = frontend[i] ?? "(missing)";
    const be = backend[i] ?? "(missing)";
    check(
      `${sectionLabel}: position ${i} — frontend "${fe}" === backend "${be}"`,
      fe === be,
    );
  }
}

/**
 * Compare two arrays as sets. Used where the frontend doesn't iterate
 * in any particular order (looking each up by name).
 */
function checkSetEquality(sectionLabel, frontend, backend) {
  const feSet = new Set(frontend);
  const beSet = new Set(backend);
  for (const fe of feSet) {
    check(
      `${sectionLabel}: frontend "${fe}" present in backend`,
      beSet.has(fe),
    );
  }
  for (const be of beSet) {
    check(
      `${sectionLabel}: backend "${be}" present in frontend`,
      feSet.has(be),
    );
  }
}

/**
 * Extract string literals from a Python dict literal. Uses a regex
 * that tolerates whitespace and trailing commas. The marker is the
 * line containing the dict's opening `{`; the function reads until
 * the matching `}` at the start of a line.
 *
 * Returns an array of the keys in source order.
 */
function extractDictKeys(source, markerRegex) {
  const start = source.search(markerRegex);
  if (start < 0) {
    console.error(`Couldn't find marker ${markerRegex} in source`);
    process.exit(2);
  }
  // Walk forward until we find the closing brace at the start of a
  // line (with optional indentation). The age_clause dict in
  // aggregations.py uses braces this way.
  const tail = source.slice(start);
  const end = tail.search(/\n\s*\}/);
  if (end < 0) {
    console.error(`Couldn't find closing brace for marker ${markerRegex}`);
    process.exit(2);
  }
  const block = tail.slice(0, end);
  // Match key strings: "key" or 'key' followed by a colon. Handles
  // simple alphanumeric / underscore keys (which all our cases are).
  const keyRegex = /["']([\w-]+)["']\s*:/g;
  const keys = [];
  let match;
  while ((match = keyRegex.exec(block)) !== null) {
    keys.push(match[1]);
  }
  return keys;
}

/**
 * Extract a Python tuple/list of string literals after a marker. Used
 * for module-level constants like `_FEEDBACK_CRITERIA = (...)`.
 */
function extractTupleStrings(source, markerRegex) {
  const start = source.search(markerRegex);
  if (start < 0) {
    console.error(`Couldn't find marker ${markerRegex} in source`);
    process.exit(2);
  }
  const tail = source.slice(start);
  // Find first opening paren or bracket
  const openMatch = tail.match(/[\(\[]/);
  if (!openMatch) {
    console.error(`Couldn't find opening tuple/list after marker ${markerRegex}`);
    process.exit(2);
  }
  const openIdx = openMatch.index;
  // Find matching closing paren / bracket
  const closingChar = tail[openIdx] === "(" ? ")" : "]";
  let depth = 0;
  let endIdx = -1;
  for (let i = openIdx; i < tail.length; i += 1) {
    if (tail[i] === tail[openIdx]) depth += 1;
    else if (tail[i] === closingChar) {
      depth -= 1;
      if (depth === 0) {
        endIdx = i;
        break;
      }
    }
  }
  if (endIdx < 0) {
    console.error(`Unterminated tuple/list at marker ${markerRegex}`);
    process.exit(2);
  }
  const block = tail.slice(openIdx + 1, endIdx);
  // Match content inside quotes — \w plus spaces and hyphens to
  // accommodate multi-word strings like "Staten Island". Excludes
  // the quote characters themselves so the regex terminates at the
  // closing quote without nesting.
  const strRegex = /["']([\w\s-]+)["']/g;
  const strs = [];
  let match;
  while ((match = strRegex.exec(block)) !== null) {
    strs.push(match[1]);
  }
  return strs;
}

/**
 * Extract the callout id strings from the body of the
 * `get_data_integrity_callouts` function in aggregations.py.
 *
 * Strategy: structural parse, not pattern scan.
 *
 *   1. Locate the function start.
 *   2. Locate every `callouts.append({` within its body.
 *   3. For each, scan forward with a balanced-brace counter to find
 *      the closing `})`.
 *   4. Extract the `"id": "value"` from inside that bounded block.
 *
 * Why not a single regex: the function body contains docstrings
 * documenting the response shape (with `"id": str` lines that look
 * superficially like callouts), helper SQL strings with curly braces,
 * and potentially nested helper closures. A flat regex either
 * picks up false positives (docstring lines) or has to fight Python
 * structure with lookbehinds that don't generalize. The structural
 * walk is verbose but predictable — and crucially, it surfaces an
 * intent error (unbalanced brace, no id found in an append block)
 * with a clear message rather than silently dropping the id.
 *
 * Accepts ids with any non-quote character (previously \w_ only),
 * so future callouts can use hyphens, dots, etc. without the verify
 * script silently missing them.
 */
function extractIntegrityCalloutIds(source) {
  const fnStart = source.search(/def get_data_integrity_callouts/);
  if (fnStart < 0) {
    console.error(`Couldn't find get_data_integrity_callouts in source`);
    process.exit(2);
  }
  // Walk to the next top-level `def` (or end of file) for the
  // function body.
  const tail = source.slice(fnStart);
  const nextDef = tail.slice(1).search(/\ndef /);
  const body = nextDef >= 0 ? tail.slice(0, nextDef + 1) : tail;

  const ids = [];
  const appendMarker = "callouts.append({";
  let cursor = 0;
  while (true) {
    const blockStart = body.indexOf(appendMarker, cursor);
    if (blockStart < 0) break;
    // Scan from the opening brace of the dict, balancing { and }.
    const dictStart = blockStart + appendMarker.length - 1; // points at `{`
    let depth = 0;
    let dictEnd = -1;
    for (let i = dictStart; i < body.length; i += 1) {
      const ch = body[i];
      if (ch === "{") depth += 1;
      else if (ch === "}") {
        depth -= 1;
        if (depth === 0) {
          dictEnd = i;
          break;
        }
      }
    }
    if (dictEnd < 0) {
      console.error(
        "extractIntegrityCalloutIds: unbalanced braces in callouts.append " +
        `block starting at offset ${blockStart} in function body. ` +
        "The source file is malformed or this regex is missing context.",
      );
      process.exit(2);
    }
    // Extract the id from inside this bounded block. The id pattern
    // accepts any chars except the surrounding quote — supports
    // hyphen, dot, underscore, digits, etc.
    const dictBody = body.slice(dictStart, dictEnd + 1);
    const idMatch = dictBody.match(/["']id["']\s*:\s*"([^"]+)"|["']id["']\s*:\s*'([^']+)'/);
    if (!idMatch) {
      console.error(
        "extractIntegrityCalloutIds: found a callouts.append({...}) " +
        `block at offset ${blockStart} that doesn't contain an "id" key. ` +
        "Every callout dict must have an id; the contract verifier " +
        "relies on it.",
      );
      process.exit(2);
    }
    ids.push(idMatch[1] || idMatch[2]);
    cursor = dictEnd + 1;
  }
  return ids;
}

// ---------------------------------------------------------------------
// Run the checks
// ---------------------------------------------------------------------

const aggregationsSrc = readBackend(
  "backend/app/services/locations_admin/aggregations.py",
);

console.log("\nLocations admin contract verification\n");

// 1. Age-bucket keys (order-sensitive)
console.log("Age-bucket keys (histogram + table filter param):");
const backendAgeKeys = extractDictKeys(
  aggregationsSrc,
  /age_clause\s*=\s*\{/,
);
checkOrdered(
  "age_bucket",
  [...LOCATIONS_AGE_BUCKET_KEYS],
  backendAgeKeys,
);

// 2. Borough labels (order-sensitive)
console.log("\nBorough labels (table rows + heatmap columns):");
const backendBoroughs = extractTupleStrings(
  aggregationsSrc,
  /^NYC_BOROUGHS\s*=/m,
);
// Backend NYC_BOROUGHS is the 5-tuple; "Other" is appended by the
// aggregations that emit it. Build the canonical labels list for
// the comparison.
const backendBoroughLabels = [...backendBoroughs, "Other"];
checkOrdered(
  "borough",
  [...LOCATIONS_BOROUGH_LABELS],
  backendBoroughLabels,
);

// 3. Feedback criteria (order-sensitive — frontend renders cards in this order)
console.log("\nFeedback criteria (criterion-summary cards + badge order):");
const backendCriteria = extractTupleStrings(
  aggregationsSrc,
  /^_FEEDBACK_CRITERIA\s*=/m,
);
checkOrdered(
  "feedback_criteria",
  [...FEEDBACK_CRITERIA_KEYS],
  backendCriteria,
);

// 4. Integrity callout IDs (set-equality — frontend looks up by name)
console.log("\nIntegrity callout IDs (data-integrity panel):");
const backendCalloutIds = extractIntegrityCalloutIds(aggregationsSrc);
checkSetEquality(
  "callout_id",
  [...INTEGRITY_CALLOUT_IDS],
  backendCalloutIds,
);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
