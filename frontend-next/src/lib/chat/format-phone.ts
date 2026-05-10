// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Normalize a phone number string to `(NNN) NNN-NNNN` US-style
 * formatting.
 *
 * The Streetlives DB has phone numbers in inconsistent shapes —
 * raw 10-digit `5162165196`, dashed `516-216-5196`, dotted
 * `516.216.5196`, parenthesized `(516) 216-5196`, with-country-
 * code `1-516-216-5196`, and a few with extensions
 * `516-216-5196 ext 102`. Showing them inconsistently in the UI
 * is a credibility hit (some look like business numbers, others
 * look like data leaks). This helper unifies them.
 *
 * Rules:
 *   1. 10 digits → `(NNN) NNN-NNNN`.
 *   2. 11 digits starting with 1 → drop the 1, format as 10-digit.
 *   3. Trailing extension (`ext`, `ext.`, `x`, or `extension`)
 *      preserved as ` ext. NNN` after the formatted number.
 *   4. Anything else passes through unchanged. This catches:
 *      - 3-digit short codes (911, 311, 211) — emergency / info lines.
 *      - 7-digit relay codes (711, 7-1-1) — accessibility services.
 *      - Multi-number strings (`(212) 555-1234, (212) 555-1235`).
 *      - Annotated strings (`Call 311 for info`).
 *      - International formats (`+44 20 7946 0958`).
 *      - Garbage / unparseable input.
 *      - Empty strings.
 *
 *      Better to render the original than to fake-format something
 *      we don't fully understand.
 */
export function formatPhone(raw: string | null | undefined): string {
  if (!raw) return raw ?? "";
  const trimmed = raw.trim();
  if (!trimmed) return "";

  // Extract extension if present. Match common forms: "ext 102",
  // "ext. 102", "x 102", "x102", "extension 102". Case-insensitive,
  // anchored at the end of the string so we don't grab a midstring
  // "extension" mention.
  let extension: string | null = null;
  let body = trimmed;
  const extMatch = body.match(
    /\s*(?:ext\.?|extension|x)\s*([0-9]+)\s*$/i,
  );
  if (extMatch) {
    extension = extMatch[1];
    body = body.slice(0, extMatch.index).trim();
  }

  // Detect whether the body is "just a phone number" vs. "annotated
  // text containing digits". Pass-through anything that has letters
  // (other than the extension we already stripped), commas (multi-
  // number), or other unexpected punctuation.
  //
  // The expected separators inside a single phone number are:
  // digits, spaces, hyphens, dots, parentheses, plus, slashes inside
  // a few NYC numbers like "718-555-1234/5". A character class
  // covering exactly those tokens is the boundary between
  // "format me" and "leave me alone".
  if (!/^[0-9\s+().\-/]+$/.test(body)) {
    return raw;
  }

  // Strip every non-digit to count.
  const digits = body.replace(/\D/g, "");

  // 11 digits with leading 1 (US country code) → drop the 1.
  let core = digits;
  if (core.length === 11 && core.startsWith("1")) {
    core = core.slice(1);
  }

  if (core.length !== 10) {
    // Not a standard US 10-digit number. Pass through — this catches
    // 3/7-digit short codes (911, 711), multi-number strings whose
    // digit sum happens to be != 10, and international numbers.
    return raw;
  }

  const formatted = `(${core.slice(0, 3)}) ${core.slice(3, 6)}-${core.slice(6)}`;
  return extension ? `${formatted} ext. ${extension}` : formatted;
}

/**
 * Strip a phone number to bare digits suitable for a `tel:` href.
 *
 * The dialer URI scheme accepts digits, `+`, and a few control
 * characters. Existing `tel:` href construction in this codebase
 * uses `phone.split(/\s*ext/i)[0].replace(/\D/g, "")` ad-hoc;
 * centralizing the logic here.
 *
 * Returns just the digits (no plus sign, no extension), which is
 * what most US dialers expect.
 */
export function phoneToTelHref(raw: string | null | undefined): string {
  if (!raw) return "";
  // Drop extension before stripping non-digits — extensions don't
  // belong in tel: URIs (the user dials the main number, then
  // navigates the menu manually).
  const beforeExt = raw.split(/\s*(?:ext\.?|extension|x)\s+/i)[0];
  return beforeExt.replace(/\D/g, "");
}
