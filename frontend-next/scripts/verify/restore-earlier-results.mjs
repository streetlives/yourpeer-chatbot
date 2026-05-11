// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for the restored-results message shape
 * built by `buildRestoredResultsMessage` in src/lib/chat/store.ts.
 *
 * Run with: `npm run verify:restore` from frontend-next/, or
 * `node scripts/verify/restore-earlier-results.mjs` directly (Node 22+
 * required for built-in TS stripping).
 *
 * Why this script exists: when a session expires (30-min TTL) or the
 * user explicitly resets the chat, the previous results message is
 * preserved as `lastResultsBeforeReset` and offered via a "See
 * earlier results" link. Tapping the link calls
 * `restoreEarlierResults`, which assembles a single bot message
 * pairing the saved cards with an orienting prefix. The shape of
 * that assembled message matters for three reasons:
 *
 *   1. The snapshot's ORIGINAL text reads "I found N option(s) for
 *      you:" with a count and a potential "I broadened the search a
 *      bit" qualifier — facts specific to the wiped session. If that
 *      text re-renders unchanged, the user sees what looks like a
 *      stale search result with no explanation of where it came from.
 *      The fix is to override `text` with a recall-framing prefix.
 *
 *   2. The snapshot's ORIGINAL quick_replies may include "Show more
 *      results" (pagination cursor tied to wiped server-side
 *      _last_results) or filter buttons ("Free only", "Open now")
 *      that operate on server state that's gone. Tapping them would
 *      return an error or empty state. The fix is to override with
 *      the standard post-results QR pair the backend uses for a
 *      similar in-state case.
 *
 *   3. The snapshot's `showFeedback` may be true — but the thumbs
 *      were associated with the original delivery moment. Asking
 *      again on the same cards in a different session reads as
 *      duplicate. The fix is to force false.
 *
 * Each case below pins one of these contracts so a future refactor
 * (e.g. extracting the QR set, renaming the prefix constant, removing
 * the override) is caught at verify time instead of in the wild.
 */

import assert from "node:assert/strict";
import {
  buildRestoredResultsMessage,
  RESTORED_RESULTS_PREFIX,
  RESTORED_RESULTS_QUICK_REPLIES,
} from "../../src/lib/chat/restored-message.ts";

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

// Helper: a representative snapshot the backend might have produced
// after a successful 6-result shelter search with "broadened" qualifier
// and pagination-aware quick replies. The shape mirrors what would land
// in localStorage at `yourpeer-chat::messages[i]` for a results message.
function buildSnapshot(overrides = {}) {
  return {
    id: "msg-old-12-1747000000000",
    role: "bot",
    text: "I found 6 option(s) for you (I broadened the search a bit) — showing the first 5:",
    services: [
      {
        service_id: "svc-1",
        service_name: "Overnight Chair",
        organization: "CAMBA",
        is_open: "open",
      },
      {
        service_id: "svc-2",
        service_name: "Overnight Sign-Up",
        organization: "CAMBA",
        is_open: "open",
      },
    ],
    quick_replies: [
      { label: "📋 Show 1 more result", value: "Show more results" },
      { label: "💲 Free only", value: "Filter by free" },
      { label: "🕒 Open now", value: "Filter by open now" },
    ],
    showFeedback: true,
    requestId: "req-old-abc",
    ...overrides,
  };
}

const NEW_ID = "msg-restored-1-1747001000000";

group("prefix override — drops the stale 'I found N option(s)' text", () => {
  check("snapshot 'I found 6...' is replaced with the prefix constant", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.text, RESTORED_RESULTS_PREFIX);
    // Pin the literal too — a regression that silently changes the
    // prefix text would be a UX bug; the doc on EarlierResultsLink
    // promises this exact phrasing.
    assert.equal(restored.text, "Here are the services you were looking at before:");
  });
  check("co-located qualifier text doesn't leak through either", () => {
    const snap = buildSnapshot({
      text: "I found 3 location(s) that offer both shelter and food (I broadened the search a bit):",
    });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.text, RESTORED_RESULTS_PREFIX);
  });
  check("empty / undefined snapshot text still produces the prefix", () => {
    // Defensive: even if upstream changes how snapshots get serialized
    // and text comes through empty, the restored message must still
    // explain itself rather than rendering as an empty bubble.
    const snap = buildSnapshot({ text: "" });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.text, RESTORED_RESULTS_PREFIX);
  });
});

group("quick replies — replaced with the standard restored-state pair", () => {
  check("pagination 'Show more results' is not preserved", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    const values = (restored.quick_replies ?? []).map((q) => q.value);
    assert.ok(
      !values.includes("Show more results"),
      `Stale pagination QR leaked through: ${values.join(", ")}`,
    );
  });
  check("filter QRs ('Free only', 'Open now') are not preserved", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    const values = (restored.quick_replies ?? []).map((q) => q.value);
    assert.ok(!values.includes("Filter by free"));
    assert.ok(!values.includes("Filter by open now"));
  });
  check("standard 'Start over' + 'Peer navigator' pair is present", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    const values = (restored.quick_replies ?? []).map((q) => q.value);
    assert.deepEqual(values, ["Start over", "Connect with peer navigator"]);
  });
  check("QR shape matches the exported constant exactly", () => {
    // If someone refactors restoreEarlierResults to inline a literal
    // instead of importing the constant, drift between this module's
    // QR set and the backend's post_results.py default would be silent.
    // Pin the constant identity.
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.deepEqual(restored.quick_replies, RESTORED_RESULTS_QUICK_REPLIES);
  });
  check("snapshot with no quick_replies still gets the standard pair", () => {
    const snap = buildSnapshot({ quick_replies: undefined });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.deepEqual(
      (restored.quick_replies ?? []).map((q) => q.value),
      ["Start over", "Connect with peer navigator"],
    );
  });
});

group("showFeedback — forced off regardless of snapshot state", () => {
  check("snapshot showFeedback=true → restored showFeedback=false", () => {
    const snap = buildSnapshot({ showFeedback: true });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.showFeedback, false);
  });
  check("snapshot showFeedback=false → restored showFeedback=false", () => {
    const snap = buildSnapshot({ showFeedback: false });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.showFeedback, false);
  });
  check("snapshot showFeedback undefined → restored showFeedback=false", () => {
    const snap = buildSnapshot({ showFeedback: undefined });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.showFeedback, false);
  });
});

group("preserved fields — cards and request metadata pass through", () => {
  check("services array is preserved byref (same length, same ids)", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.services?.length, 2);
    assert.equal(restored.services?.[0].service_id, "svc-1");
    assert.equal(restored.services?.[1].service_id, "svc-2");
  });
  check("role is preserved as 'bot'", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.role, "bot");
  });
  check("new id is applied (no collision with the snapshot's old id)", () => {
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.id, NEW_ID);
    assert.notEqual(restored.id, snap.id);
  });
  check("requestId from the original turn is preserved (audit trail)", () => {
    // requestId is on the snapshot for backend dedup correlation; it's
    // historically attached to user messages but the spread preserves
    // whatever the snapshot had. Either way, restored shouldn't strip
    // it silently.
    const snap = buildSnapshot();
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.requestId, "req-old-abc");
  });
});

group("zero-services snapshot — degraded gracefully", () => {
  check("empty services array still produces a valid message", () => {
    // Edge case: a snapshot stored from a "0 results" turn shouldn't
    // crash the restore path. The text override still fires; the QRs
    // still attach; services renders as an empty list.
    const snap = buildSnapshot({ services: [] });
    const restored = buildRestoredResultsMessage(snap, NEW_ID);
    assert.equal(restored.text, RESTORED_RESULTS_PREFIX);
    assert.deepEqual(restored.services, []);
    assert.deepEqual(
      (restored.quick_replies ?? []).map((q) => q.value),
      ["Start over", "Connect with peer navigator"],
    );
  });
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
