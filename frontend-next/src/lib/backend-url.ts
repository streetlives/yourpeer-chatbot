// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Resolve the FastAPI backend URL the Next.js API routes proxy to.
 *
 * Behavior:
 *   - If `CHAT_BACKEND_URL` is set (and non-empty after trimming), return it.
 *   - Otherwise in development (`NODE_ENV !== "production"`), return the
 *     localhost fallback `http://localhost:8000` for `npm run dev`.
 *   - Otherwise in production, throw a clear error naming the missing
 *     env var. This is the whole point of the helper: silent localhost
 *     fallback in production looks like "the backend is slow" or "the
 *     request hangs" rather than a clear configuration error. By the
 *     time anyone has narrowed it down to env config, time has been
 *     spent on the wrong hypothesis. Throwing makes the failure mode
 *     unambiguous: the deploy log shows the message, the route 500s
 *     on first hit, and the fix is one env var change.
 *
 * Why a helper, not inline at each call site:
 *   The pattern was duplicated across five API routes
 *   (api/chat, api/chat/feedback, api/chat/location-feedback,
 *   api/health, api/admin/[...slug]). Inline copies drift — one of
 *   them already had a slightly different fallback style than the
 *   others before this consolidation. Single source of truth for the
 *   "what URL should I proxy to?" question, validated in one place.
 *
 * Why no URL-format validation:
 *   `new URL(url)` later in the call chain will throw on malformed
 *   input, with a clear native error message. Adding a separate
 *   validation here would either duplicate that or mask it. The one
 *   normalization we do is `.trim()` because whitespace-padded env
 *   vars are a common ops mistake (e.g. .env file with trailing
 *   space) that would otherwise produce baffling URL parse errors.
 *
 * Note for callers: this function is intentionally called at module
 * top level by each route. That means if production env is missing,
 * the route's first request will 500 with the helper's error
 * message visible in deploy logs — preferable to lazy per-request
 * checks that could let some requests succeed (against the wrong
 * backend) before the misconfiguration is noticed.
 */
export function getBackendUrl(): string {
  const raw = process.env.CHAT_BACKEND_URL?.trim();
  if (raw) return raw;
  if (process.env.NODE_ENV === "production") {
    throw new Error(
      "CHAT_BACKEND_URL is required in production. " +
        "Set it in your deployment environment (e.g. Render dashboard) " +
        "to point at the FastAPI backend (e.g. https://yourpeer-backend.onrender.com).",
    );
  }
  return "http://localhost:8000";
}
