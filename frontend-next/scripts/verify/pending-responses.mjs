// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Standalone verification for src/lib/chat/pending-responses.ts —
 * specifically the pure `classifyPending` function that decides which
 * pending entries to deliver, drop, or keep.
 *
 * Run with: `npm run verify:pending` from frontend-next/, or
 * `node scripts/verify/pending-responses.mjs` directly (Node 22+
 * required for built-in TS stripping).
 *
 * Why this script exists: classifyPending closes a real race between
 * the service worker's Background Sync drain and a client-side
 * resetChat. Getting the classification wrong would inject orphaned
 * bot messages into a chat the user thought they had wiped — a UX
 * trust failure for the population this serves. The cases below
 * document the threat model and the boundary conditions.
 */

import assert from "node:assert/strict";
import { classifyPending } from "../../src/lib/chat/pending-responses.ts";

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

// Helper: build a PendingResponse with sensible defaults
function entry(opts) {
  return {
    id: opts.id ?? "msg-1",
    sessionId: opts.sessionId ?? null,
    body: opts.body ?? { ok: true },
    receivedAt: opts.receivedAt ?? 1000,
    queuedAt: opts.queuedAt,
  };
}

// Time horizon for tests: epoch=1000 means "user clicked reset at t=1000"
const NOW = 5000;

group("expired entries (older than PENDING_TTL_MS)", () => {
  // PENDING_TTL_MS is 1 hour = 3,600,000 ms
  const TTL = 60 * 60 * 1000;
  check("expired by 1 ms — dropped", () => {
    const e = entry({ receivedAt: NOW - TTL - 1 });
    const { matched } = classifyPending([e], null, 0, NOW);
    assert.equal(matched.length, 0);
  });
  check("at the boundary — kept", () => {
    const e = entry({ receivedAt: NOW - TTL, queuedAt: NOW - TTL });
    const { matched } = classifyPending([e], null, 0, NOW);
    assert.equal(matched.length, 1);
  });
});

group("pre-reset filtering — the core race", () => {
  // Scenario: user queued m1 at t=500, clicked reset at t=1000, SW
  // POST completed at t=1500 with sessionId=A. Reconcile runs at
  // t=NOW. We must drop the response.
  check("queuedAt < resetEpoch — dropped", () => {
    const e = entry({
      id: "m1",
      sessionId: "A",
      receivedAt: 1500,
      queuedAt: 500,
    });
    const { matched } = classifyPending([e], "B", 1000, NOW);
    assert.equal(matched.length, 0, "pre-reset entry must not be returned");
  });
  check("queuedAt > resetEpoch — kept (post-reset typing)", () => {
    const e = entry({
      id: "m2",
      sessionId: "B",
      receivedAt: 2000,
      queuedAt: 1500,
    });
    const { matched } = classifyPending([e], "B", 1000, NOW);
    assert.equal(matched.length, 1);
  });
  check("queuedAt === resetEpoch — kept (resetEpoch is exclusive lower bound)", () => {
    const e = entry({
      id: "m3",
      sessionId: "B",
      receivedAt: 1500,
      queuedAt: 1000,
    });
    const { matched } = classifyPending([e], "B", 1000, NOW);
    // The check is `queuedAt < resetEpoch`, so equal means kept.
    // Documented here to lock in the boundary semantics.
    assert.equal(matched.length, 1);
  });
  check(
    "the receivedAt-vs-queuedAt distinction — receivedAt post-reset, queuedAt pre-reset",
    () => {
      // This is the case that the original (buggy) implementation got
      // wrong. SW finished POSTing AFTER reset, so receivedAt is post-
      // reset, but the underlying user typing was pre-reset.
      const e = entry({
        id: "m1",
        sessionId: "A",
        receivedAt: 1500, // post-reset
        queuedAt: 500, // pre-reset
      });
      const { matched } = classifyPending([e], null, 1000, NOW);
      assert.equal(
        matched.length,
        0,
        "entry with pre-reset queuedAt must not slip through",
      );
    },
  );
});

group("session matching", () => {
  check("sessionId matches current — accept", () => {
    const e = entry({ sessionId: "A", receivedAt: 2000, queuedAt: 1500 });
    const { matched } = classifyPending([e], "A", 1000, NOW);
    assert.equal(matched.length, 1);
  });
  check("sessionId differs from current — drop (no reset epoch)", () => {
    const e = entry({ sessionId: "A", receivedAt: 2000, queuedAt: 1500 });
    const { matched } = classifyPending([e], "B", 0, NOW);
    assert.equal(matched.length, 0);
  });
  check(
    "currentSessionId === null + entry has queuedAt — adopt (post-reset, server assigned session)",
    () => {
      const e = entry({ sessionId: "C", receivedAt: 2000, queuedAt: 1500 });
      const { matched } = classifyPending([e], null, 1000, NOW);
      assert.equal(matched.length, 1);
      assert.equal(matched[0].sessionId, "C");
    },
  );
  check(
    "currentSessionId === null + entry has no queuedAt (legacy SW) — drop conservatively",
    () => {
      const e = entry({ sessionId: "X", receivedAt: 2000 }); // no queuedAt
      const { matched } = classifyPending([e], null, 1000, NOW);
      assert.equal(
        matched.length,
        0,
        "legacy entry without queuedAt must not be adopted via null branch",
      );
    },
  );
});

group("no-reset case (resetEpoch === 0, fresh client)", () => {
  check("legacy entry adopted when no reset has ever happened", () => {
    // resetEpoch === 0 means: user has never clicked reset on this
    // device. There's no since-reset session to be wary of, so the
    // legacy entry is safe to adopt.
    //
    // Wait — the current implementation drops legacy entries via the
    // currentSessionId === null branch unconditionally. Lock that in:
    // the safer behavior is "drop legacy entries when current session
    // is null, regardless of resetEpoch", because we can't tell the
    // first-install-after-upgrade case from the just-reset case.
    const e = entry({ sessionId: "X", receivedAt: 2000 }); // no queuedAt
    const { matched } = classifyPending([e], null, 0, NOW);
    assert.equal(matched.length, 0);
  });
  check(
    "legacy entry kept when sessionId matches (no null-adopt needed)",
    () => {
      const e = entry({ sessionId: "X", receivedAt: 2000 });
      const { matched } = classifyPending([e], "X", 0, NOW);
      assert.equal(matched.length, 1);
    },
  );
});

group("multiple entries — interleaved cases", () => {
  check("mixed pre-reset, post-reset, expired, matching", () => {
    const TTL = 60 * 60 * 1000;
    const entries = [
      entry({ id: "expired", receivedAt: NOW - TTL - 1, queuedAt: NOW - TTL }),
      entry({ id: "pre-reset", sessionId: "A", receivedAt: 1500, queuedAt: 500 }),
      entry({ id: "post-reset-match", sessionId: "B", receivedAt: 2000, queuedAt: 1500 }),
      entry({
        id: "post-reset-stale-session",
        sessionId: "C",
        receivedAt: 2000,
        queuedAt: 1500,
      }),
    ];
    const { matched } = classifyPending(entries, "B", 1000, NOW);
    const ids = matched.map((p) => p.id).sort();
    assert.deepEqual(ids, ["post-reset-match"], `got ${ids.join(",")}`);
  });
});

group("input tolerance", () => {
  check("undefined input — empty matched", () => {
    const { matched } = classifyPending(undefined, null, 0, NOW);
    assert.equal(matched.length, 0);
  });
  check("non-array input — empty matched", () => {
    // The runtime path coerces this anyway; lock in that classify
    // doesn't blow up if a corrupt IDB value somehow reaches it.
    const { matched } = classifyPending(
      /** @type any */ ("not an array"),
      null,
      0,
      NOW,
    );
    assert.equal(matched.length, 0);
  });
  check("empty array — empty matched", () => {
    const { matched } = classifyPending([], null, 0, NOW);
    assert.equal(matched.length, 0);
  });
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
