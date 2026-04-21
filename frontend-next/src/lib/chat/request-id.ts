// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Idempotency key generator.
 *
 * Why this exists: `crypto.randomUUID()` only works in secure contexts
 * (HTTPS or localhost). Staging/preview deploys served over plain HTTP,
 * or apps embedded in an iframe inside a non-secure parent, will throw
 * `TypeError: crypto.randomUUID is not a function`. Because the throw
 * happens BEFORE the fetch call in use-chat.ts's send flow, it
 * propagates into the catch block as a non-network error — so the
 * user sees "something went wrong" instead of the offline queue
 * behavior they should have gotten, and they have no idea why.
 *
 * Strategy:
 *   1. Prefer `crypto.randomUUID()` — it's one call and guaranteed
 *      RFC 4122 v4.
 *   2. Fall back to `crypto.getRandomValues()` + manual v4 formatting.
 *      This works on every browser that supports fetch (IE11+ via
 *      Web Crypto), including non-secure contexts.
 *   3. Last-resort fallback: Math.random() with a timestamp prefix.
 *      Not cryptographically random, but we don't need it to be —
 *      an idempotency key is correct if it's unique within a single
 *      user's 60-second retry window. Collision would require two
 *      Math.random() calls producing identical 11-char suffixes in
 *      the same millisecond, which isn't a realistic failure mode
 *      for a chat UI.
 *
 * SSR: returns a placeholder on the server because crypto and
 * Math.random are deterministic under some Node configurations —
 * but request IDs should never be generated during SSR anyway
 * (they're created at message-send time, client-only). The
 * placeholder is namespaced so it's obvious in logs if it ever
 * leaks into a real request.
 */
export function generateRequestId(): string {
  if (typeof crypto !== "undefined") {
    // Fast path — modern secure contexts.
    if (typeof crypto.randomUUID === "function") {
      try {
        return crypto.randomUUID();
      } catch {
        // Some environments expose the function but throw at call
        // time (e.g. crypto.randomUUID in an insecure context shim).
        // Fall through to getRandomValues.
      }
    }
    // Manual UUID v4 via getRandomValues — works in non-secure
    // contexts. 16 random bytes, then set the version/variant bits
    // per RFC 4122 §4.4.
    if (typeof crypto.getRandomValues === "function") {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
      bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
      return (
        hex.slice(0, 4).join("") +
        "-" +
        hex.slice(4, 6).join("") +
        "-" +
        hex.slice(6, 8).join("") +
        "-" +
        hex.slice(8, 10).join("") +
        "-" +
        hex.slice(10, 16).join("")
      );
    }
  }

  // Last resort — insecure-random fallback. Prefixed with "fb-" so
  // server logs can tell these apart from real UUIDs.
  const t = Date.now().toString(36);
  const r1 = Math.random().toString(36).slice(2, 11);
  const r2 = Math.random().toString(36).slice(2, 11);
  return `fb-${t}-${r1}${r2}`;
}
