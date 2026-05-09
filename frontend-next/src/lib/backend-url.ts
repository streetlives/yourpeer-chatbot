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
 * Why callers invoke this per-request, not at module top level:
 *   The earlier version of this helper was called once at module
 *   top in each route (`const BACKEND_URL = getBackendUrl()`). That
 *   broke `next build`: Next.js's "Collecting page data" phase
 *   evaluates each route module to determine static-vs-dynamic, and
 *   does so with `NODE_ENV=production` set but without runtime env
 *   vars. The helper saw "production with missing env" and threw,
 *   failing the build on every deploy.
 *
 *   Per-request invocation defers the env check to actual request
 *   handling, where the Render server has the runtime env vars set.
 *   The "module-top is earlier failure than per-request" argument
 *   that drove the earlier design doesn't actually hold: the
 *   function is deterministic in env vars, so the very first
 *   incoming request fails identically to module load. The friendly
 *   try/catch handlers in each route would also swallow the env
 *   error if the call were inside the try block — so callers must
 *   place this call BEFORE their try/catch, so missing-env errors
 *   escape to the default Next.js error handler (visible in deploy
 *   logs) rather than getting masked as "Backend unreachable".
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
