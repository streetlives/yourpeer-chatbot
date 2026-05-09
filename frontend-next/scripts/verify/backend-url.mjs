// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for `src/lib/backend-url.ts`.
 *
 * Run with: `npm run verify:backend-url` from frontend-next/, or
 * `node scripts/verify/backend-url.mjs` directly (Node 22+ required
 * for built-in TS stripping).
 *
 * Why this exists: the helper's contract has three branches and a
 * couple of normalization rules. Two of the three branches (production
 * + missing env, production + present env) are not exercised by
 * `npm run dev` — by definition, dev runs with NODE_ENV=development.
 * Without this script, the only place those production branches get
 * tested is in production itself, which is the wrong place to learn
 * that "we throw on missing env" doesn't actually fire because of a
 * subtle bug.
 *
 * What's covered (5 cases):
 *   1. Env set in any mode → returns the value verbatim.
 *   2. Env unset + NODE_ENV != "production" → localhost fallback.
 *   3. Env unset + NODE_ENV = "production" → throws with a message
 *      that names CHAT_BACKEND_URL specifically. The error message
 *      is part of the contract — anyone debugging a deploy needs to
 *      grep the deploy log for "CHAT_BACKEND_URL" and find this.
 *   4. Env set to whitespace-only → treated as unset (`.trim()`
 *      empties it; no live system stores blanks intentionally).
 *   5. Env set with surrounding whitespace → returned trimmed.
 *      Common .env-file mistake (trailing space, copy-paste from
 *      docs); silently mishandling would create a baffling
 *      URL-parse error far from the source.
 */

import assert from "node:assert/strict";

import { getBackendUrl } from "../../src/lib/backend-url.ts";

let passed = 0;
let failed = 0;

function check(label, fn) {
  try {
    fn();
    console.log(`  ✓ ${label}`);
    passed += 1;
  } catch (err) {
    console.log(`  ✗ ${label}`);
    console.log(`      ${err.message}`);
    failed += 1;
  }
}

// Save originals once so each test can restore independently. The
// verify script runs in its own Node process, so leaking changes
// across tests would just be sloppy hygiene rather than a real
// concern — but the explicit save/restore makes the test code easier
// to reason about and survives any future move into a runner that
// reuses processes (vitest, jest).
const ORIGINAL_BACKEND = process.env.CHAT_BACKEND_URL;
const ORIGINAL_NODE_ENV = process.env.NODE_ENV;

function restore() {
  if (ORIGINAL_BACKEND === undefined) delete process.env.CHAT_BACKEND_URL;
  else process.env.CHAT_BACKEND_URL = ORIGINAL_BACKEND;
  if (ORIGINAL_NODE_ENV === undefined) delete process.env.NODE_ENV;
  else process.env.NODE_ENV = ORIGINAL_NODE_ENV;
}

console.log("Backend URL helper:");

// ---------------------------------------------------------------------
// Case 1: env set in development → returns the value
// ---------------------------------------------------------------------
check("env set + dev → returns value", () => {
  process.env.CHAT_BACKEND_URL = "https://staging.example.com";
  process.env.NODE_ENV = "development";
  try {
    assert.equal(getBackendUrl(), "https://staging.example.com");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 2: env set in production → returns the value (no spurious throw)
// ---------------------------------------------------------------------
check("env set + production → returns value", () => {
  process.env.CHAT_BACKEND_URL = "https://api.example.com";
  process.env.NODE_ENV = "production";
  try {
    assert.equal(getBackendUrl(), "https://api.example.com");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 3: env unset in development → localhost fallback
// ---------------------------------------------------------------------
check("env unset + dev → localhost fallback", () => {
  delete process.env.CHAT_BACKEND_URL;
  process.env.NODE_ENV = "development";
  try {
    assert.equal(getBackendUrl(), "http://localhost:8000");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 4: env unset in production → throws with named env var
// ---------------------------------------------------------------------
check("env unset + production → throws", () => {
  delete process.env.CHAT_BACKEND_URL;
  process.env.NODE_ENV = "production";
  try {
    let caught = null;
    try {
      getBackendUrl();
    } catch (err) {
      caught = err;
    }
    assert.ok(caught, "expected getBackendUrl() to throw");
    assert.match(
      caught.message,
      /CHAT_BACKEND_URL/,
      "error message must name the missing env var",
    );
    assert.match(
      caught.message,
      /production/,
      "error message must mention production context",
    );
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 5: env set but blank → treated as unset (dev branch)
// ---------------------------------------------------------------------
check("env set to whitespace + dev → localhost fallback", () => {
  process.env.CHAT_BACKEND_URL = "   ";
  process.env.NODE_ENV = "development";
  try {
    assert.equal(getBackendUrl(), "http://localhost:8000");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 6: env set to whitespace + prod → throws (whitespace not bypass)
// ---------------------------------------------------------------------
check("env set to whitespace + production → throws", () => {
  process.env.CHAT_BACKEND_URL = "  ";
  process.env.NODE_ENV = "production";
  try {
    let caught = null;
    try {
      getBackendUrl();
    } catch (err) {
      caught = err;
    }
    assert.ok(caught, "whitespace must not bypass the production guard");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
// Case 7: env set with leading/trailing whitespace → trimmed
// ---------------------------------------------------------------------
check("env set with whitespace padding → trimmed", () => {
  process.env.CHAT_BACKEND_URL = "  https://api.example.com  ";
  process.env.NODE_ENV = "production";
  try {
    assert.equal(getBackendUrl(), "https://api.example.com");
  } finally {
    restore();
  }
});

// ---------------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
