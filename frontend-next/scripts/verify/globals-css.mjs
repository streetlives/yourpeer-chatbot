// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/app/globals.css.
 *
 * Currently checks one invariant — the overflow-x policy on
 * html/body — but this script exists as a hook for any future
 * "global CSS we don't want silently regressed" guards.
 *
 * Run with: `npm run verify:globals-css` (or via `npm run verify`).
 * Exit non-zero on any failure.
 *
 * Why this exists: the html/body overflow setting is in the
 * "easy to revert without realizing" category. The natural-looking
 * change `overflow-x: hidden` reads correct to anyone who didn't
 * read the spec gotcha — that "overflow-x: hidden + overflow-y:
 * visible" silently makes overflow-y compute to `auto`, turning
 * html/body into separate vertical scroll containers. We landed
 * on `clip` (clips without making the element a scroll container)
 * to fix a 1-2px phantom vertical scroll on mobile that the
 * `hidden` value caused. This guard prevents a future contributor
 * from "simplifying" it back to `hidden` without understanding
 * the gotcha.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CSS_PATH = join(__dirname, "..", "..", "src", "app", "globals.css");

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

const css = readFileSync(CSS_PATH, "utf8");

// ---------------------------------------------------------------------
// Case 1: html/body block exists and uses overflow-x: clip, not hidden.
// ---------------------------------------------------------------------

check("html/body block uses overflow-x: clip (not hidden)", () => {
  // Find the html, body block. Normalize whitespace so the regex
  // tolerates either compact or expanded formatting.
  const blockMatch = css.match(/html\s*,\s*body\s*\{([^}]*)\}/);
  assert.ok(
    blockMatch,
    "Could not find `html, body { ... }` block in globals.css. " +
    "If the block was renamed or split, update this check accordingly."
  );

  const body = blockMatch[1];
  assert.match(
    body,
    /overflow-x:\s*clip\b/,
    "html/body should use `overflow-x: clip`, not `overflow-x: hidden`. " +
    "Reason: the CSS overflow spec silently computes overflow-y from " +
    "`visible` to `auto` whenever overflow-x is hidden/auto/scroll/clip " +
    "and the other axis is visible. With `hidden`, that turns html/body " +
    "into vertical scroll containers and produces a 1-2px phantom scroll " +
    "on mobile. With `clip`, overflow-y stays visible and the document " +
    "uses its natural single scroll context. " +
    "See https://drafts.csswg.org/css-overflow/#overflow-properties §5.1."
  );
  assert.doesNotMatch(
    body,
    /overflow-x:\s*hidden\b/,
    "html/body must NOT use `overflow-x: hidden` — see the rationale " +
    "in the assertion above. Use `clip` instead. The Safari minimum " +
    "(15.8+) is comfortably below this project's targets."
  );
});

// ---------------------------------------------------------------------
// Case 2: overscroll-behavior: none is preserved alongside the clip.
// ---------------------------------------------------------------------

check("html/body preserves overscroll-behavior: none", () => {
  const blockMatch = css.match(/html\s*,\s*body\s*\{([^}]*)\}/);
  assert.ok(blockMatch, "html/body block missing");
  assert.match(
    blockMatch[1],
    /overscroll-behavior:\s*none\b/,
    "html/body should declare `overscroll-behavior: none` to disable " +
    "the iOS / Android rubber-band overscroll bounce. Without this, " +
    "scrolling past the top or bottom of the document on mobile shows " +
    "the host browser's chrome behind the page, which feels like a " +
    "website rather than an app."
  );
});

// ---------------------------------------------------------------------

console.log("");
console.log(`${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
