// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/lib/chat/format-phone.ts.
 *
 * Phone formatting is the kind of pure function whose correctness
 * is dominated by edge cases — short codes (911, 311, 211),
 * relay codes (711, 7-1-1), country-code variations, extension
 * forms, multi-number annotations, international, garbage. The
 * helper's documented contract is "format US 10-digit numbers
 * consistently, leave everything else alone." This verify script
 * exercises that contract case-by-case so a future refactor can't
 * silently regress.
 *
 * No jest/vitest dependency — minimal-deps philosophy.
 *
 * Run with: `npm run verify:format-phone` or via `npm run verify`.
 * Exit non-zero on any failure.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC_PATH = join(
  __dirname, "..", "..", "src", "lib", "chat", "format-phone.ts",
);

// ---------------------------------------------------------------------
// Strip TS type syntax and load as plain JS.
// ---------------------------------------------------------------------

const tsSource = readFileSync(SRC_PATH, "utf8");
const jsSource = tsSource
  .replace(/:\s*string\s*\|\s*null\s*\|\s*undefined/g, "")
  .replace(/:\s*string(\s*[={,)])/g, "$1")
  .replace(/:\s*string\s*$/gm, "")
  .replace(/:\s*string\s*\|\s*null/g, "")
  // strip the optional return type annotation
  .replace(/\)\s*:\s*string\s*\{/g, ") {");

const { formatPhone, phoneToTelHref } = await import(
  `data:text/javascript;base64,${Buffer.from(jsSource).toString("base64")}`
);

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

// ---------------------------------------------------------------------
// Case 1: 10-digit raw input — the most common DB shape.
// ---------------------------------------------------------------------

check("raw 10 digits → (NNN) NNN-NNNN", () => {
  assert.equal(formatPhone("5162165196"), "(516) 216-5196");
});

check("raw 10 digits with leading zero borough", () => {
  // Edge case: NYC numbers often start with the same 2 in the
  // original area code. Check that nothing about the "starts with
  // a low digit" case trips up the slice math.
  assert.equal(formatPhone("2125551234"), "(212) 555-1234");
});

// ---------------------------------------------------------------------
// Case 2: already-formatted variations.
// ---------------------------------------------------------------------

check("already (NNN) NNN-NNNN passes through formatted", () => {
  assert.equal(formatPhone("(516) 216-5196"), "(516) 216-5196");
});

check("dashed 516-216-5196", () => {
  assert.equal(formatPhone("516-216-5196"), "(516) 216-5196");
});

check("dotted 516.216.5196", () => {
  assert.equal(formatPhone("516.216.5196"), "(516) 216-5196");
});

check("space-separated 516 216 5196", () => {
  assert.equal(formatPhone("516 216 5196"), "(516) 216-5196");
});

// ---------------------------------------------------------------------
// Case 3: country-code variations.
// ---------------------------------------------------------------------

check("11 digits with leading 1 → drop the 1", () => {
  assert.equal(formatPhone("15162165196"), "(516) 216-5196");
});

check("1-516-216-5196", () => {
  assert.equal(formatPhone("1-516-216-5196"), "(516) 216-5196");
});

check("+1 516 216 5196", () => {
  assert.equal(formatPhone("+1 516 216 5196"), "(516) 216-5196");
});

check("+1-516-216-5196", () => {
  assert.equal(formatPhone("+1-516-216-5196"), "(516) 216-5196");
});

// ---------------------------------------------------------------------
// Case 4: extensions.
// ---------------------------------------------------------------------

check("extension with 'ext'", () => {
  assert.equal(formatPhone("516-216-5196 ext 102"), "(516) 216-5196 ext. 102");
});

check("extension with 'ext.'", () => {
  assert.equal(formatPhone("516-216-5196 ext. 102"), "(516) 216-5196 ext. 102");
});

check("extension with 'x'", () => {
  assert.equal(formatPhone("5162165196 x 102"), "(516) 216-5196 ext. 102");
});

check("extension with 'extension'", () => {
  assert.equal(formatPhone("5162165196 extension 102"), "(516) 216-5196 ext. 102");
});

check("extension case-insensitive (Ext.)", () => {
  assert.equal(formatPhone("5162165196 Ext. 102"), "(516) 216-5196 ext. 102");
});

// ---------------------------------------------------------------------
// Case 5: pass-through cases — short codes and special numbers.
// ---------------------------------------------------------------------

check("3-digit emergency code 911 passes through", () => {
  assert.equal(formatPhone("911"), "911");
});

check("3-digit info code 311 passes through", () => {
  assert.equal(formatPhone("311"), "311");
});

check("3-digit helpline 211 passes through", () => {
  assert.equal(formatPhone("211"), "211");
});

check("relay code 711 passes through", () => {
  assert.equal(formatPhone("711"), "711");
});

check("relay code 7-1-1 passes through", () => {
  // Has digits + hyphens (allowed punctuation), 3 digits total —
  // not 10, not 11-with-leading-1, so passes through.
  assert.equal(formatPhone("7-1-1"), "7-1-1");
});

// ---------------------------------------------------------------------
// Case 6: pass-through — annotated / multi-number / international.
// ---------------------------------------------------------------------

check("annotated 'Call 311 for info' passes through unchanged", () => {
  // Has letters → preserved. The "Call" word would be lost if we
  // were to digit-strip + format, which would be a bug.
  assert.equal(formatPhone("Call 311 for info"), "Call 311 for info");
});

check("multi-number with comma passes through", () => {
  // Comma signals annotation. We don't try to format the first
  // number and re-attach the rest — too lossy (ambiguous what
  // the second value refers to). Pass through as-is.
  assert.equal(
    formatPhone("(212) 555-1234, (212) 555-5678"),
    "(212) 555-1234, (212) 555-5678",
  );
});

check("international format with country name passes through", () => {
  assert.equal(
    formatPhone("UK: +44 20 7946 0958"),
    "UK: +44 20 7946 0958",
  );
});

check("non-US 11+ digits without leading 1 passes through", () => {
  // A real number from another country shouldn't be mangled.
  // 12 digits with no leading 1 → not a US format we recognize.
  assert.equal(formatPhone("442079460958"), "442079460958");
});

// ---------------------------------------------------------------------
// Case 7: degenerate inputs — empty, undefined, garbage.
// ---------------------------------------------------------------------

check("empty string returns empty", () => {
  assert.equal(formatPhone(""), "");
});

check("undefined returns empty", () => {
  assert.equal(formatPhone(undefined), "");
});

check("null returns empty", () => {
  assert.equal(formatPhone(null), "");
});

check("whitespace-only returns empty", () => {
  assert.equal(formatPhone("   "), "");
});

check("garbage text passes through", () => {
  assert.equal(formatPhone("not a phone"), "not a phone");
});

check("9 digits (too short) passes through", () => {
  // Short by one — could be a typo, could be a deliberate code.
  // We don't know, so don't fake-format.
  assert.equal(formatPhone("516216519"), "516216519");
});

check("12 digits without leading 1 passes through", () => {
  assert.equal(formatPhone("251621651960"), "251621651960");
});

// ---------------------------------------------------------------------
// Case 8: phoneToTelHref companion — bare digits, no extension.
// ---------------------------------------------------------------------

check("phoneToTelHref strips formatting to bare digits", () => {
  assert.equal(phoneToTelHref("(516) 216-5196"), "5162165196");
});

check("phoneToTelHref drops extension before stripping", () => {
  assert.equal(phoneToTelHref("(516) 216-5196 ext. 102"), "5162165196");
});

check("phoneToTelHref handles 'x' extension form", () => {
  assert.equal(phoneToTelHref("516-216-5196 x 102"), "5162165196");
});

check("phoneToTelHref empty input returns empty", () => {
  assert.equal(phoneToTelHref(""), "");
  assert.equal(phoneToTelHref(undefined), "");
  assert.equal(phoneToTelHref(null), "");
});

check("phoneToTelHref preserves digits in unrecognized formats", () => {
  // Even when format isn't a recognized US shape, the tel: target
  // gets all the digits — the dialer can do its best.
  assert.equal(phoneToTelHref("Call 311 for info"), "311");
});

// ---------------------------------------------------------------------

console.log("");
console.log(`${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
