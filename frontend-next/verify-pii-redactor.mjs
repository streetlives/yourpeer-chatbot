// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/lib/chat/pii-redactor.ts.
 *
 * Run with: `node verify-pii-redactor.mjs` from this file's directory
 * (Node 22+ required — uses built-in TypeScript stripping).
 *
 * Same philosophy as verify-theme.mjs — no test framework dependency.
 * The redactor is pure, dependency-free, and the verification cases
 * here document the patterns alongside the implementation.
 *
 * Cases come from three sources:
 *   1. Pattern parity with backend/app/privacy/pii_redactor.py
 *   2. Population-specific cases drawn from Cornell sample queries
 *      and YourPeer eval scenarios (lgbtq youth, peer_pregnant,
 *      pii_phone_shared, pii_ssn_shared, etc.)
 *   3. False-positive guards demonstrating why the client redactor
 *      uses NARROWER patterns than the server (single-message context
 *      can't disambiguate "I'm scared" from "I'm Sarah" reliably).
 *
 * Exit code is non-zero on any failure so this can run as a CI
 * preflight without test-framework integration.
 */

import assert from "node:assert/strict";
import { detectPII, redactPII, hasPII } from "./src/lib/chat/pii-redactor.ts";

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok ${name}`);
  } catch (err) {
    failed++;
    console.log(`  NOT ok ${name}`);
    console.log(`    ${err.message}`);
  }
}

function group(label, body) {
  console.log(`\n# ${label}`);
  body();
}

// ---------------------------------------------------------------------------
// Helpers — make assertions read like English
// ---------------------------------------------------------------------------

function expectRedacted(input, expected) {
  const { redacted } = redactPII(input);
  assert.equal(
    redacted,
    expected,
    `redactPII(${JSON.stringify(input)})\n      got: ${JSON.stringify(redacted)}\n      want: ${JSON.stringify(expected)}`,
  );
}

function expectUnchanged(input) {
  const { redacted, detections } = redactPII(input);
  assert.equal(redacted, input, `expected no change, got ${JSON.stringify(redacted)}`);
  assert.equal(
    detections.length,
    0,
    `expected no detections, got ${JSON.stringify(detections)}`,
  );
}

function expectDetected(input, types) {
  const { detections } = redactPII(input);
  const got = detections.map((d) => d.type).sort();
  const want = [...types].sort();
  assert.deepEqual(got, want, `detections: got ${got.join(",")}, want ${want.join(",")}`);
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

group("phone numbers", () => {
  check("basic 10-digit with dashes", () =>
    expectRedacted("call me at 555-123-4567", "call me at [PHONE]"),
  );
  check("parenthesized area code", () =>
    expectRedacted(
      "my number is (212) 555-1234 thanks",
      "my number is [PHONE] thanks",
    ),
  );
  check("country code +1", () =>
    expectRedacted("text +1 555-555-1234", "text [PHONE]"),
  );
  check("dotted format", () =>
    expectRedacted("ph 555.123.4567", "ph [PHONE]"),
  );
  check("not a phone — 7 digits", () => expectUnchanged("call 1234567"));
  check("not a phone — 8 digits as ID", () => expectUnchanged("ref 12345678"));
});

group("ssn", () => {
  check("dashed SSN", () =>
    expectRedacted("my ssn is 123-45-6789", "my ssn is [SSN]"),
  );
  check("space-separated SSN", () =>
    expectRedacted("123 45 6789", "[SSN]"),
  );
  check("unseparated 9-digit run is also matched as SSN", () =>
    // The Python source uses `[-\s]?` (optional separator) which
    // allows a contiguous 9-digit run to match. This client mirrors
    // that — a 9-digit number in a chat message is more likely to be
    // an SSN than something else, and we err toward redaction. Same
    // behavior as backend/app/privacy/pii_redactor.py.
    expectRedacted("123456789 random", "[SSN] random"),
  );
});

group("email", () => {
  check("basic email", () =>
    expectRedacted("contact me at jane@example.com", "contact me at [EMAIL]"),
  );
  check("plus-tag email", () =>
    expectRedacted("user+tag@gmail.com", "[EMAIL]"),
  );
  check("not an email", () => expectUnchanged("ratio 1@2 isn't email"));
});

group("credit cards (Luhn-validated)", () => {
  check("valid Visa with spaces", () =>
    expectRedacted("4111 1111 1111 1111", "[CREDIT_CARD]"),
  );
  check("valid Visa unseparated", () =>
    expectRedacted("4111111111111111 yes", "[CREDIT_CARD] yes"),
  );
  check("invalid Luhn — not redacted", () =>
    // 16 digits but bad checksum; should stay because it's not a real CC
    expectUnchanged("ref 1234567812345678"),
  );
});

group("urls and social handles", () => {
  check("https url", () =>
    expectRedacted(
      "see https://example.com/path?x=1 for info",
      "see [URL] for info",
    ),
  );
  check("bare facebook handle", () =>
    expectRedacted("find me on facebook.com/janedoe", "find me on [URL]"),
  );
});

group("dob", () => {
  check("numeric DOB", () =>
    expectRedacted("dob 12/25/1990", "dob [DOB]"),
  );
  check("written DOB", () =>
    expectRedacted("born March 5, 1985", "born [DOB]"),
  );
  check("invalid month", () =>
    expectUnchanged("count 13/45/2020"),
  );
});

group("addresses", () => {
  check("standard street + suffix + apt", () =>
    expectRedacted(
      "I live at 123 Main Street Apt 4B",
      "I live at [ADDRESS]",
    ),
  );
  check("ordinal address", () =>
    expectRedacted("789 5th Avenue", "[ADDRESS]"),
  );
  check("Broadway special case", () =>
    expectRedacted("meet me at 1234 Broadway", "meet me at [ADDRESS]"),
  );
  check("not an address — no suffix", () =>
    expectUnchanged("I'm 21 years old in Manhattan"),
  );
});

group("names — high-confidence introductions only", () => {
  check("'my name is X'", () =>
    expectRedacted("hi my name is Jane Smith", "hi my name is [NAME]"),
  );
  check("'call me X'", () =>
    expectRedacted("just call me Bryan", "just call me [NAME]"),
  );
  check("'this is X'", () =>
    expectRedacted("hi this is Maria", "hi this is [NAME]"),
  );
  check("greeting + name", () =>
    expectRedacted("Hi Bryan how are you", "Hi [NAME] how are you"),
  );
  check("blocklist guard — 'my name is there'", () =>
    expectUnchanged("my name is there"),
  );

  // Critical false-positive guards. The bare "I'm X" pattern that the
  // server runs is INTENTIONALLY NOT in the client redactor — these
  // assertions document what would otherwise be misclassified.
  check("FP guard — 'I'm scared'", () =>
    expectUnchanged("I'm scared and I need help"),
  );
  check("FP guard — 'I'm 21'", () =>
    expectUnchanged("I'm 21 and looking for shelter"),
  );
  check("FP guard — 'I'm hungry'", () =>
    expectUnchanged("I'm hungry, where can I get food"),
  );
});

group("gender identity (PII-adjacent for this population)", () => {
  // From eval scenario natural_lgbtq_youth and Cornell sample queries
  check("'I'm trans'", () =>
    expectRedacted(
      "I'm a trans woman looking for safe shelter",
      "[GENDER] looking for safe shelter",
    ),
  );
  check("'as a queer person'", () =>
    expectRedacted("as a queer person I need a doctor", "[GENDER] person I need a doctor"),
  );
  check("'I am non-binary'", () =>
    expectRedacted("I am non-binary, looking for housing", "[GENDER], looking for housing"),
  );
  check("not redacted — incidental third-person", () =>
    expectUnchanged("the man at the counter was rude"),
  );
});

group("multi-pattern messages — Cornell sample queries", () => {
  // From the Cornell doc: "21, LGBTQ, in Soho, need a bed tonight" —
  // age stays, identity gets redacted, location stays.
  check("Cornell #1 — LGBTQ + Soho + bed", () => {
    const { redacted } = redactPII("21, LGBTQ, in Soho, need a bed tonight.");
    // LGBTQ alone (no lead-in) is NOT a match — we only match
    // self-identifying declarations. Document this explicitly so
    // future readers know it's intentional.
    assert.equal(redacted, "21, LGBTQ, in Soho, need a bed tonight.");
  });

  check("from a longer crisis message", () =>
    expectDetected(
      "my name is Sarah, my number is 555-123-4567, I'm at 123 Main Street",
      ["name", "phone", "address"],
    ),
  );

  check("eval pii_ssn_shared scenario", () =>
    expectRedacted(
      "here's my ssn 123-45-6789 to verify",
      "here's my ssn [SSN] to verify",
    ),
  );
});

group("edge cases", () => {
  check("empty string", () => expectUnchanged(""));
  check("idempotent — redacting twice is a no-op", () => {
    const once = redactPII("call me at 555-123-4567 thanks").redacted;
    const twice = redactPII(once).redacted;
    assert.equal(twice, once, "second pass changed the output");
  });
  check("hasPII matches detectPII", () => {
    assert.equal(hasPII("nothing here"), false);
    assert.equal(hasPII("ssn 123-45-6789"), true);
  });
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
