// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/lib/chat/chat-message-redaction.ts.
 *
 * Run with: `node verify-chat-message-redaction.mjs` from this file's
 * directory (Node 22+ required for built-in TS stripping).
 *
 * Why this script exists: the persist-boundary redaction is the ONLY
 * thing standing between user-typed PII (phone numbers, addresses,
 * SSNs in chat messages) and the durable localStorage at
 * `yourpeer-chat`. localStorage persists indefinitely until cleared.
 * For a population on shared phones, borrowed devices, and household
 * computers with multiple users, "indefinitely" is the wrong default
 * for any kind of PII. If this redaction is wrong, the privacy
 * posture in the design doc (§13: "No PII stored") becomes a lie.
 *
 * The cases below cover the boundary conditions the migrate function
 * and partialize function depend on:
 *   1. User-role messages get text redacted; bot-role do not
 *   2. retryMessage gets redacted on ANY role
 *   3. Empty / missing fields don't crash either helper
 *   4. The "no redaction needed" fast path returns the same object
 *      reference (cheap, important for the hot path)
 *   5. The migrate-time helper tolerates loose schemas
 */

import assert from "node:assert/strict";
import {
  redactMessage,
  redactStoredMessage,
} from "./src/lib/chat/chat-message-redaction.ts";

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
// redactMessage — typed, in-memory ChatMessage path (partialize)
// ---------------------------------------------------------------------------

group("redactMessage — user-role text", () => {
  check("user message with PII gets text redacted", () => {
    const out = redactMessage({
      id: "msg-1",
      role: "user",
      text: "my number is 555-123-4567, I need shelter",
    });
    assert.equal(out.text, "my number is [PHONE], I need shelter");
  });

  check("user message without PII passes through", () => {
    const out = redactMessage({
      id: "msg-2",
      role: "user",
      text: "I need food in Brooklyn",
    });
    assert.equal(out.text, "I need food in Brooklyn");
  });

  check("user message text='' (empty string)", () => {
    // Edge case: a user message with empty text shouldn't crash and
    // shouldn't allocate. Empty text means there was nothing to send,
    // which shouldn't happen in practice but is worth guarding.
    const input = { id: "msg-3", role: "user", text: "" };
    const out = redactMessage(input);
    assert.equal(out.text, "");
  });
});

group("redactMessage — bot-role text NOT redacted", () => {
  check("bot message with PII-shaped text passes through unchanged", () => {
    // The bot doesn't echo user PII back. If a service phone number
    // (for example) appears in bot-rendered text, it's a service
    // contact, not user PII — must NOT be scrubbed.
    const out = redactMessage({
      id: "msg-4",
      role: "bot",
      text: "Call Ali Forney Center at 212-206-0574 for shelter intake.",
    });
    assert.equal(
      out.text,
      "Call Ali Forney Center at 212-206-0574 for shelter intake.",
      "bot text must NOT be scrubbed of phone numbers — those are service contacts",
    );
  });
});

group("redactMessage — retryMessage", () => {
  check("retryMessage on bot-role error gets redacted", () => {
    // Common case: backend errored, error message has a Retry button
    // that holds the original user text. That text needs scrubbing
    // before localStorage persists it.
    const out = redactMessage({
      id: "msg-5",
      role: "bot",
      text: "Sorry, something went wrong. Try again?",
      retryMessage: "my number is 555-123-4567, I need shelter",
    });
    assert.equal(out.text, "Sorry, something went wrong. Try again?");
    assert.equal(
      out.retryMessage,
      "my number is [PHONE], I need shelter",
    );
  });

  check("retryMessage on user-role message gets redacted", () => {
    // Less common but possible — a user-role message could in
    // principle carry retryMessage. Verify both fields handled.
    const out = redactMessage({
      id: "msg-6",
      role: "user",
      text: "my ssn is 123-45-6789",
      retryMessage: "my ssn is 123-45-6789",
    });
    assert.equal(out.text, "my ssn is [SSN]");
    assert.equal(out.retryMessage, "my ssn is [SSN]");
  });
});

group("redactMessage — fast path (no allocation when no redaction)", () => {
  // Important for the hot path: most messages are routine ("yes",
  // "Brooklyn", "near me", quick-reply confirmations) and shouldn't
  // pay an allocation cost for unnecessary copies. The helper
  // returns the input reference unchanged when nothing needs redaction.
  check("bot message without retryMessage returns same reference", () => {
    const input = { id: "m", role: "bot", text: "Welcome." };
    const out = redactMessage(input);
    assert.equal(out, input, "expected same reference (no-op fast path)");
  });

  check("user message with no PII still passes through", () => {
    // The fast path is "needsTextRedaction || needsRetryRedaction" —
    // user messages with text always go through redactPII (which is
    // cheap when there's no PII). They get a NEW object back, but the
    // text is unchanged. That's correct — the call-time check is
    // "does this need scrubbing", not "does this have any text at
    // all".
    const input = { id: "m", role: "user", text: "I need food" };
    const out = redactMessage(input);
    assert.equal(out.text, "I need food");
  });
});

group("redactMessage — non-text fields preserved", () => {
  check("services and quick_replies pass through", () => {
    const input = {
      id: "msg-7",
      role: "bot",
      text: "Found 2 results.",
      services: [
        {
          service_id: "abc",
          service_name: "Ali Forney Center",
          phone: "212-206-0574",
          address: "224 W 35th St",
        },
      ],
      quick_replies: [{ label: "Call", value: "call", href: "tel:+12122060574" }],
    };
    const out = redactMessage(input);
    assert.deepEqual(out.services, input.services);
    assert.deepEqual(out.quick_replies, input.quick_replies);
  });

  check("status, requestId, showFeedback preserved", () => {
    const input = {
      id: "msg-8",
      role: "user",
      text: "shelter please",
      status: "sent",
      requestId: "req-abc",
      showFeedback: false,
    };
    const out = redactMessage(input);
    assert.equal(out.status, "sent");
    assert.equal(out.requestId, "req-abc");
    assert.equal(out.showFeedback, false);
  });
});

// ---------------------------------------------------------------------------
// redactStoredMessage — loose-typed migration path
// ---------------------------------------------------------------------------

group("redactStoredMessage — migration from older schemas", () => {
  check("v2 user message with PII gets redacted on upgrade", () => {
    const out = redactStoredMessage({
      id: "msg-1",
      role: "user",
      text: "my address is 145 East 3rd Street",
    });
    assert.equal(out.text, "my address is [ADDRESS]");
  });

  check("v2 bot message preserved (text not redacted)", () => {
    const out = redactStoredMessage({
      id: "msg-2",
      role: "bot",
      text: "Bronx FoodPantry at 718-555-1234.",
    });
    assert.equal(out.text, "Bronx FoodPantry at 718-555-1234.");
  });

  check("retryMessage redacted regardless of role", () => {
    const out = redactStoredMessage({
      id: "msg-3",
      role: "bot",
      text: "Sorry, error.",
      retryMessage: "call me at 555-123-4567",
    });
    assert.equal(out.retryMessage, "call me at [PHONE]");
  });
});

group("redactStoredMessage — defensive against schema drift", () => {
  check("missing role doesn't crash (text not touched)", () => {
    // Pre-v0 might have had no role field; future versions might add
    // a third role. Either way, "role !== 'user'" should mean
    // "leave text alone."
    const out = redactStoredMessage({
      id: "x",
      text: "any phone 555-123-4567",
    });
    assert.equal(out.text, "any phone 555-123-4567");
  });

  check("non-string text passes through (legacy/corrupt data)", () => {
    // Defensive: if a future schema or corrupt localStorage entry
    // has `text: null` or `text: 123`, the helper must not throw.
    const out = redactStoredMessage({
      id: "x",
      role: "user",
      text: null,
    });
    assert.equal(out.text, null);
  });

  check("non-string retryMessage passes through", () => {
    const out = redactStoredMessage({
      id: "x",
      role: "bot",
      text: "ok",
      retryMessage: undefined,
    });
    assert.equal(out.retryMessage, undefined);
  });

  check("unknown extra fields are preserved verbatim", () => {
    // A user upgrading from a future-version schema (or from a
    // sibling-tab that wrote a newer shape) shouldn't lose fields.
    const out = redactStoredMessage({
      id: "x",
      role: "user",
      text: "hello",
      futureField: { nested: true },
    });
    assert.deepEqual(out.futureField, { nested: true });
  });
});

// ---------------------------------------------------------------------------
// Critical regression case: the original PR comment scenario
// ---------------------------------------------------------------------------

group("regression — the scenario this redaction was added to fix", () => {
  // From the PR description's "follow-ups" section:
  //   "The chat-history persisted in localStorage (zustand persist)
  //    under yourpeer-chat ALSO contains the user's original
  //    PII-bearing text."
  // This test asserts that scenario is now closed: a typical user
  // message with phone + name + address gets all three redacted
  // before reaching the persisted shape.
  check("phone + name + address all redacted in single user message", () => {
    const out = redactMessage({
      id: "msg-9",
      role: "user",
      text: "Hi my name is Sarah, my number is 555-123-4567, I'm at 123 Main Street",
    });
    // No PHONE digits should appear in output
    assert.ok(
      !out.text.includes("555"),
      `phone leaked: ${out.text}`,
    );
    // No "Sarah" string in output
    assert.ok(
      !out.text.includes("Sarah"),
      `name leaked: ${out.text}`,
    );
    // No "Main Street" in output
    assert.ok(
      !out.text.includes("Main Street"),
      `address leaked: ${out.text}`,
    );
    // All three placeholders should be present
    assert.ok(out.text.includes("[PHONE]"));
    assert.ok(out.text.includes("[NAME]"));
    assert.ok(out.text.includes("[ADDRESS]"));
  });
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
