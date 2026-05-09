// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the cross-stack contracts the frontend
 * has with the backend, beyond the distribution-dict keys covered by
 * `bucket-keys.mjs`.
 *
 * Run with: `npm run verify:contract` from frontend-next/, or
 * `node scripts/verify/contract.mjs` directly (Node 22+ required for
 * built-in TS stripping).
 *
 * Why this script exists: the May 2026 audit found three Python ↔
 * TypeScript contract-drift bugs in shipped frontend code. Two of
 * them (Bugs 2 and 3 — turn-count and duration bucket keys) were
 * caught by `bucket-keys.mjs` once it landed. This script extends the
 * same pattern to the four next-most-likely drift surfaces:
 *
 *   1. Routing buckets — keys read by the Metrics tab Section 5.
 *      Frontend reads `routing.buckets.service_flow` etc. by name; a
 *      backend rename silently shows 0.
 *
 *   2. Health statuses — string-equality branches in
 *      `system-health.tsx` and `use-backend-health.ts` that decide
 *      badge color and the chat status dot. Backend emits these
 *      strings from `/api/health`; a rename silently colors things
 *      wrong.
 *
 *   3. Audit event types — the 6 `"type"` strings emitted by the
 *      `log_*` functions in `audit_log.py`. The frontend's
 *      transcript-drawer and event-feed switch on these. A rename
 *      sends every event of that type to the default branch.
 *
 *   4. Eval dimension keys — the 11 dimensions in
 *      `tests/eval/eval_llm_judge.py :: DIMENSION_WEIGHTS`. The Evals
 *      tab looks each up in `summary.dimension_averages[key]`; if
 *      the backend renames a dimension, the row silently disappears.
 *
 * Surfaces NOT covered (and why):
 *   - Confidence buckets (high/semantic/medium/low/disambiguated).
 *     The values are passed dynamically through call chains rather
 *     than enumerated in a literal dict, so they can't be statically
 *     extracted from the backend without parsing the docstring of
 *     `_compute_confidence`. Docstring-as-contract would be fragile.
 *     Filed as a follow-up — possibly via a runtime check on a sample
 *     `/admin/api/stats` response.
 *   - `is_open` service status (open/closed). Only 2 values, very
 *     stable. Low-cost to add later; not worth the noise today.
 *   - `geographic_borough` enum and `fallback_population` enum. Both
 *     are TypeScript literal unions on `ServiceResult` —
 *     type-protected at compile time (TS catches a wrong string
 *     before it reaches any branch).
 *
 * Each section follows the same shape:
 *   - Read the backend source file.
 *   - Extract the canonical set with regex.
 *   - Compare to the frontend's exported constant.
 *   - Order-sensitive where the frontend renders by walking the
 *     array (routing buckets, eval dimensions); set-only where
 *     position doesn't matter (health statuses, event types).
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  HEALTH_OVERALL_STATUSES,
  HEALTH_CHECK_STATUSES,
  AUDIT_EVENT_TYPES,
  ROUTING_BUCKET_KEYS,
} from "../../src/lib/admin/contract-keys.ts";
import { EVAL_DIMENSIONS } from "../../src/lib/admin/eval-dimensions.ts";

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

/** Read a backend source file or fail with a clear path message. */
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
 * differ." Used for sections where the order is semantic.
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
 * Compare two arrays as sets. Used for sections where the frontend
 * doesn't iterate in any particular order — only set membership
 * matters (every frontend-expected value is a backend-emitted value,
 * and vice versa).
 */
function checkSetsEqual(sectionLabel, frontend, backend) {
  const feSet = new Set(frontend);
  const beSet = new Set(backend);
  const onlyFrontend = [...feSet].filter((x) => !beSet.has(x));
  const onlyBackend = [...beSet].filter((x) => !feSet.has(x));

  check(
    `${sectionLabel}: every frontend value is emitted by the backend`,
    onlyFrontend.length === 0,
    onlyFrontend.length > 0
      ? `Frontend expects but backend doesn't emit: [${onlyFrontend.join(", ")}]`
      : null,
  );
  check(
    `${sectionLabel}: every backend value is recognized by the frontend`,
    onlyBackend.length === 0,
    onlyBackend.length > 0
      ? `Backend emits but frontend doesn't handle: [${onlyBackend.join(", ")}]`
      : null,
  );
}

// ---------------------------------------------------------------------
// 1. Routing buckets
// ---------------------------------------------------------------------
console.log("Routing buckets (audit_log.py :: _compute_routing):");

{
  const auditLog = readBackend("backend/app/services/audit_log.py");
  // Anchor on the unique `_compute_routing` definition, then find the
  // `buckets = {` literal that follows.
  const fnIdx = auditLog.indexOf("def _compute_routing");
  if (fnIdx === -1) {
    check("_compute_routing function found", false,
      "Looked for `def _compute_routing` — not found. Function may have been renamed.");
  } else {
    const region = auditLog.slice(fnIdx, fnIdx + 2000);
    const dictMatch = region.match(/buckets\s*=\s*\{([\s\S]*?)\}/);
    if (!dictMatch) {
      check("buckets dict literal found", false,
        "_compute_routing exists but no `buckets = {...}` literal inside the first 2000 chars.");
    } else {
      const keys = [...dictMatch[1].matchAll(/"([^"]+)":/g)].map((m) => m[1]);
      checkOrdered("Routing buckets", [...ROUTING_BUCKET_KEYS], keys);
    }
  }
}

// ---------------------------------------------------------------------
// 2. Health overall + check statuses
// ---------------------------------------------------------------------
console.log("\nHealth overall status (main.py :: /api/health):");

{
  const main = readBackend("backend/app/main.py");
  // Extract every string assigned to `overall = "..."`. Restrict to the
  // /api/health handler region — look from `def health` (or similar)
  // forward, but the file is small enough that scanning the whole
  // thing is fine. We constrain to the `overall = "..."` pattern,
  // which only appears in the health endpoint.
  const overallMatches = [...main.matchAll(/overall\s*=\s*"([^"]+)"/g)];
  const overallSet = new Set(overallMatches.map((m) => m[1]));

  if (overallMatches.length === 0) {
    check("overall = '...' assignments found in main.py", false,
      "No `overall = '...'` patterns found. The /api/health handler may have changed shape.");
  } else {
    checkSetsEqual(
      "Health overall",
      [...HEALTH_OVERALL_STATUSES],
      [...overallSet],
    );
  }
}

console.log("\nHealth check statuses (main.py — wire-format contract):");

{
  const main = readBackend("backend/app/main.py");
  // Extract every status string emitted by the /api/health response.
  // We deliberately scan ONLY main.py, not claude_client.py: the LLM
  // probe in claude_client emits fine-grained internal statuses
  // (auth_error, rate_limited, timeout, api_error) which main.py's
  // health handler maps into the 5 wire-format statuses the frontend
  // actually sees ("up" / "down" / "degraded" / "unavailable" /
  // "not_loaded"). The contract this script is enforcing is the wire
  // format — the boundary between backend and frontend — not internal
  // backend representations. If a future refactor removes that
  // mapping layer and exposes the fine-grained statuses on the wire,
  // this script needs to be updated to scan claude_client too. The
  // first version of this script flagged that as drift, which was a
  // useful false positive — it surfaced the layer boundary explicitly.
  //
  // The pattern we match is permissive within main.py: any
  // `"status": "..."` literal. The /api/health handler is the only
  // place in main.py that builds these objects.
  const statusMatches = [...main.matchAll(/"status":\s*"([^"]+)"/g)];
  const statusSet = new Set(statusMatches.map((m) => m[1]));

  // Filter out "alive" — that's the /healthz endpoint, separate from
  // /api/health which the SystemHealth component reads.
  statusSet.delete("alive");

  if (statusMatches.length === 0) {
    check("\"status\": \"...\" patterns found in main.py", false,
      "No status literals found. /api/health handler shape may have changed.");
  } else {
    checkSetsEqual(
      "Health check status",
      [...HEALTH_CHECK_STATUSES],
      [...statusSet],
    );
  }
}

// ---------------------------------------------------------------------
// 3. Audit event types
// ---------------------------------------------------------------------
console.log("\nAudit event types (audit_log.py :: log_* functions):");

{
  const auditLog = readBackend("backend/app/services/audit_log.py");
  // Each log_* function constructs an event dict with `"type": "..."`.
  // Pull every literal in the file. The audit_log.py file does not
  // contain any other `"type":` patterns at module scope (verified
  // by inspection — all matches are event-type assignments).
  const typeMatches = [...auditLog.matchAll(/"type":\s*"([^"]+)"/g)];
  const typeSet = new Set(typeMatches.map((m) => m[1]));

  if (typeMatches.length === 0) {
    check("event type literals found", false,
      'No `"type": "..."` patterns found in audit_log.py.');
  } else {
    checkSetsEqual(
      "Audit event type",
      [...AUDIT_EVENT_TYPES],
      [...typeSet],
    );
  }
}

// ---------------------------------------------------------------------
// 4. Eval dimension keys
// ---------------------------------------------------------------------
console.log("\nEval dimension keys (eval_llm_judge.py :: DIMENSION_WEIGHTS):");

{
  const judge = readBackend("tests/eval/eval_llm_judge.py");
  const dictStart = judge.indexOf("DIMENSION_WEIGHTS = {");
  if (dictStart === -1) {
    check("DIMENSION_WEIGHTS dict found", false,
      "Looked for `DIMENSION_WEIGHTS = {` — not found.");
  } else {
    // Find matching closing brace by counting depth. The dict has no
    // nested braces (values are floats with comments), so a naive
    // forward scan to the next "}" works, but counting depth is the
    // safer pattern.
    let depth = 0;
    let endIdx = -1;
    for (let i = dictStart; i < judge.length; i += 1) {
      const ch = judge[i];
      if (ch === "{") depth += 1;
      else if (ch === "}") {
        depth -= 1;
        if (depth === 0) { endIdx = i; break; }
      }
    }
    if (endIdx === -1) {
      check("DIMENSION_WEIGHTS dict closing brace found", false,
        "DIMENSION_WEIGHTS opens but never closes? File may be truncated.");
    } else {
      const body = judge.slice(dictStart, endIdx);
      const keys = [...body.matchAll(/"([a-z_]+)":/g)].map((m) => m[1]);

      // EVAL_DIMENSIONS is an array of objects with a `key` field.
      const frontendKeys = EVAL_DIMENSIONS.map((d) => d.key);

      // For dimensions, set-equality is the contract: the frontend
      // must render every dimension the backend scores, and shouldn't
      // expect dimensions the backend doesn't score. Order in
      // EVAL_DIMENSIONS reflects rendering order (which is independent
      // of DIMENSION_WEIGHTS' declaration order — weights are sorted
      // by importance, the dashboard sorts by category).
      checkSetsEqual(
        "Eval dimension keys",
        frontendKeys,
        keys,
      );
    }
  }
}

// ---------------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
