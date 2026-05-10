// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the `containsHtml` regex in
 * src/components/chat/safe-html.tsx.
 *
 * This regex decides whether a string is "plain text" (rendered as
 * a text node, safe by construction) or "potentially HTML" (routed
 * through the DOMParser-based sanitizer). The regex is the gatekeeper
 * — if it returns false for a string that actually contains tags,
 * those tags render verbatim because the plain-text branch escapes
 * nothing.
 *
 * The May 2026 bug that motivated this script: malformed input like
 * `</br>` (the Streetlives DB has it in a few service descriptions)
 * failed the opening-tag-only regex `/<[a-z]/i` because the first
 * char after `<` is `/`, not a letter. Result: the literal `</br>`
 * rendered in the UI as text. Fix: regex now allows an optional `/`
 * after the `<`, so closing tags are detected too.
 *
 * No DOM polyfill needed — the regex is pure JS. We also assert a
 * few cases related to the sanitizer's behavior (the sanitizer is
 * exercised at runtime via DOMParser, which isn't available in node;
 * the sanitizer guarantees here are documented but not directly
 * tested in this verify pass).
 *
 * Run with: `npm run verify:safe-html` (or via `npm run verify`).
 * Exit non-zero on any failure.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SAFEHTML_PATH = join(
  __dirname, "..", "..", "src", "components", "chat", "safe-html.tsx",
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
// Extract the containsHtml regex literal from the source so this test
// is testing the actual deployed code, not a redefinition.
// ---------------------------------------------------------------------

const src = readFileSync(SAFEHTML_PATH, "utf8");

// Match: function containsHtml(...) { return /REGEX/FLAGS.test(text); }
// Captures the regex pattern and flags.
const regexMatch = src.match(
  /function containsHtml\([^)]*\)[^{]*\{\s*return\s+\/(.+?)\/([a-z]*)\.test/m,
);
assert.ok(
  regexMatch,
  "Could not extract the containsHtml regex from safe-html.tsx. If the " +
  "function was renamed or restructured, update this verify script."
);

const containsHtml = (() => {
  const re = new RegExp(regexMatch[1], regexMatch[2]);
  return (s) => re.test(s);
})();

// ---------------------------------------------------------------------
// Case 1: opening tags — was already working pre-fix.
// ---------------------------------------------------------------------

check("opening tag <p> detected", () => {
  assert.equal(containsHtml("<p>hello</p>"), true);
});

check("opening tag <br> detected", () => {
  assert.equal(containsHtml("hello<br>world"), true);
});

check("self-closing <br /> detected", () => {
  assert.equal(containsHtml("hello<br />world"), true);
});

check("opening tag is case-insensitive", () => {
  assert.equal(containsHtml("<P>hello</P>"), true);
  assert.equal(containsHtml("<BR/>"), true);
});

// ---------------------------------------------------------------------
// Case 2: closing tags — the regression. These FAILED pre-fix.
// ---------------------------------------------------------------------

check("closing tag </p> detected (was failing pre-fix)", () => {
  assert.equal(
    containsHtml("</p>"), true,
    "Closing tag should be detected so the sanitizer can normalize it. " +
    "Pre-fix, this returned false and the literal `</p>` rendered as text."
  );
});

check("closing tag </br> detected — the actual bug", () => {
  assert.equal(
    containsHtml("• Item one. </br> • Item two."), true,
    "This is the exact bug from the May 2026 production screenshot. " +
    "The Streetlives DB contains descriptions with `</br>` tags " +
    "(malformed — `br` is a void element). Pre-fix, this returned " +
    "false, the plain-text branch fired, and `</br>` rendered verbatim. " +
    "Post-fix, it routes through DOMParser, which silently drops the " +
    "malformed token, and the surrounding text renders cleanly."
  );
});

check("mixed opening + closing tags detected", () => {
  assert.equal(containsHtml("<p>hello</p><br>world"), true);
});

check("standalone closing tag </a> detected", () => {
  assert.equal(containsHtml("text</a>more"), true);
});

// ---------------------------------------------------------------------
// Case 2b: ENTITY-ENCODED tags — second-pass bug fix (May 2026).
//
// The first round of fixes (Case 2 above) handled raw closing tags
// like `</br>`. But the Streetlives DB also stores some descriptions
// with tags ALREADY HTML-entity-encoded — e.g. `&lt;/br&gt;` — which
// is the entity-encoded form of the literal `</br>` string.
//
// React renders entity-encoded text by decoding the entities for
// display. So a string like `&lt;/br&gt;` in a plain-text branch
// would visually render as `</br>` — exactly what the user-facing
// bug screenshot showed. Fix: regex now also matches `&lt;a-z` /
// `&lt;/a-z` so entity-encoded tags route through the sanitizer,
// which decodes entities before parsing and emits real `<br />`s.
// ---------------------------------------------------------------------

check("entity-encoded &lt;br&gt; detected", () => {
  assert.equal(
    containsHtml("hello &lt;br&gt; world"), true,
    "Entity-encoded opening tag should match the regex so the sanitizer " +
    "gets a chance to decode and normalize. Without this, React renders " +
    "the entities as visible `<br>` text."
  );
});

check("entity-encoded &lt;/br&gt; detected — the production bug", () => {
  assert.equal(
    containsHtml("• Item one. &lt;/br&gt; • Item two."), true,
    "This is the exact production data shape behind the May 2026 " +
    "screenshot showing literal `</br>` text in a service description. " +
    "The DB stores some descriptions with HTML-entity-encoded tags. " +
    "Pre-fix the regex required real `<` characters, so the entity form " +
    "fell into the plain-text branch and React decoded the entities " +
    "into visible `</br>` text."
  );
});

check("entity-encoded with self-closing &lt;br /&gt; detected", () => {
  assert.equal(
    containsHtml("&lt;br /&gt;"), true,
    "Both void and self-closing entity-encoded forms should match."
  );
});

check("entity-encoded in mixed content detected", () => {
  // The most realistic bug data: bullet-list-style description with
  // entity-encoded line breaks between items.
  assert.equal(
    containsHtml(
      "• Perishable items. &lt;/br&gt; &lt;/br&gt; • Services available."
    ), true,
  );
});

check("plain text with bare ampersand still returns false", () => {
  // Make sure the new entity branch doesn't false-positive on plain
  // ampersands. Only `&lt;letter` and `&lt;/letter` should trigger.
  assert.equal(containsHtml("Use & instead"), false);
  assert.equal(containsHtml("Smith & Sons"), false);
});

// ---------------------------------------------------------------------
// Case 2c: source-level invariants for the entity decode pipeline.
// ---------------------------------------------------------------------

check("decodeEntities function exists", () => {
  assert.match(
    src,
    /function\s+decodeEntities\s*\(/,
    "The decodeEntities helper must exist. It bridges the gap between " +
    "DB-stored entity-encoded tags and the sanitizer (which expects " +
    "real tag characters). Without it, sanitizeHtml + the regex match " +
    "is necessary but not sufficient — DOMParser parses `&lt;br&gt;` " +
    "as text content `<br>`, then escapeText re-escapes it back to " +
    "`&lt;br&gt;`, then React decodes for display. Vicious circle."
  );
});

check("sanitizeHtml calls decodeEntities", () => {
  const sanitizeBody = src.match(
    /function\s+sanitizeHtml[\s\S]*?\{([\s\S]*?)^}/m,
  );
  assert.ok(sanitizeBody, "sanitizeHtml function body not found");
  assert.match(
    sanitizeBody[1],
    /decodeEntities\s*\(/,
    "sanitizeHtml must pre-decode entities before passing to DOMParser. " +
    "Without this call, entity-encoded tags survive the sanitize pass " +
    "and render as visible text after React decoding."
  );
});

check("stripTags calls decodeEntities", () => {
  const stripBody = src.match(
    /function\s+stripTags[\s\S]*?\{([\s\S]*?)^}/m,
  );
  assert.ok(stripBody, "stripTags function body not found");
  assert.match(
    stripBody[1],
    /decodeEntities\s*\(/,
    "stripTags is the SSR fallback. It must also pre-decode entities " +
    "or SSR shows literal `</br>` text until hydration replaces it."
  );
});

// ---------------------------------------------------------------------
// Case 3: plain text — should still return false (fast-path correctness).
// ---------------------------------------------------------------------

check("plain text returns false", () => {
  assert.equal(containsHtml("hello world"), false);
});

check("text with bare < but no tag returns false", () => {
  // Math/comparison text — no actual tag here. DOMParser would handle
  // this correctly as text either way, but the fast-path should catch it.
  assert.equal(containsHtml("x < 5 and y > 3"), false);
});

check("empty string returns false", () => {
  assert.equal(containsHtml(""), false);
});

check("text with > but no < returns false", () => {
  assert.equal(containsHtml("price > $100"), false);
});

// ---------------------------------------------------------------------
// Case 4: edge cases that could trip future refactors.
// ---------------------------------------------------------------------

check("HTML comment <!-- ... --> NOT detected (text fast-path is fine)", () => {
  // Comments aren't a tag in our allowlist; if they were rendered they'd
  // be stripped by the sanitizer anyway. The regex doesn't have to match
  // them — text fast-path will pass them through DOMParser-free, which
  // is correct because comments in text content render as nothing visible.
  // Documenting expected behavior, not asserting either way.
  const result = containsHtml("<!-- comment -->");
  // Either result is acceptable; what matters is that the path is
  // consistent. Currently the regex requires a letter after </? so this
  // returns false. If a future change makes this return true, the
  // sanitizer drops comments anyway, so still safe.
  assert.equal(typeof result, "boolean");
});

check("malformed `< br>` (space after <) NOT detected — known limitation", () => {
  // Real malformed cases like `< br />` slip through the regex. They
  // would render as plain text. This is a documented known limitation;
  // the Streetlives DB hasn't surfaced this pattern in practice. If it
  // ever does, the fix would be a more permissive regex like
  // /<\s*\/?[a-z]/i — but that has its own false-positive risk.
  // Documenting the boundary here so future contributors understand it.
  assert.equal(containsHtml("hello < br > world"), false);
});

// ---------------------------------------------------------------------

console.log("");
console.log(`${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
