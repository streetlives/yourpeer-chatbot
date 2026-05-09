// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Cross-stack enum constants that the frontend depends on the backend
 * emitting verbatim. Companion to `bucket-keys.ts` (which covers
 * distribution-dict keys); this module covers the broader category of
 * "string the frontend hardcodes against the backend's output."
 *
 * Each constant here is the source of truth for the frontend; the
 * verify script `scripts/verify/contract.mjs` cross-checks each list
 * against the backend's literal source and fails CI if they diverge.
 *
 * Why this matters: TypeScript's structural typing can't catch a
 * branch like `if (status === "healthy") ...` against a backend that
 * emits "OK" instead of "healthy" — the equality just returns false
 * silently, the branch never fires, and the dashboard shows wrong
 * colors with no warning. Lifting the canonical set into a typed
 * literal union AND a runtime-comparable array gives us a single
 * place to update plus a place the verify script can read.
 */

// ---------------------------------------------------------------------
// Health status — emitted by /api/health and consumed by SystemHealth
// and use-backend-health
// ---------------------------------------------------------------------

/**
 * Top-level health status. Backend source:
 * `backend/app/main.py :: /api/health :: overall = "healthy"|"degraded"|"unhealthy"`
 *
 * Used by `use-backend-health.ts` (drives the chat-status dot color)
 * and `system-health.tsx` (drives the badge in the System Health card).
 */
export const HEALTH_OVERALL_STATUSES = [
  "healthy",
  "degraded",
  "unhealthy",
] as const;
export type HealthOverallStatus = (typeof HEALTH_OVERALL_STATUSES)[number];

/**
 * Per-check status. Each subsystem (database, llm, semantic_router)
 * reports one of these. The frontend's renderer in `system-health.tsx`
 * has explicit branches for `"up"`, `"down"`, `"degraded"`,
 * `"unavailable"`, and `"not_loaded"`. A backend-emitted status outside
 * this set falls through the renderer's amber fallback color, which
 * is a degraded UX (no specific detail string).
 *
 * Backend source: `backend/app/main.py` — the `/api/health` handler.
 * This is the wire-format contract.
 *
 * Note: the LLM probe in `backend/app/llm/claude_client.py` emits
 * fine-grained internal statuses (`auth_error`, `rate_limited`,
 * `timeout`, `api_error`) that the `/api/health` handler maps into
 * `"degraded"` before sending. The frontend never sees those internal
 * statuses on the wire. If that mapping layer ever gets removed, the
 * verify script (which currently scans main.py only) needs updating
 * — and so does this list.
 */
export const HEALTH_CHECK_STATUSES = [
  "up",
  "down",
  "degraded",
  "unavailable",
  "not_loaded",
] as const;
export type HealthCheckStatus = (typeof HEALTH_CHECK_STATUSES)[number];

// ---------------------------------------------------------------------
// Audit event type — emitted by audit_log.py event recorders, consumed
// by event-feed and transcript-drawer
// ---------------------------------------------------------------------

/**
 * Canonical event types the audit log emits. Each `log_*` function in
 * `backend/app/services/audit_log.py` writes a dict with a `"type"`
 * field. The frontend's `transcript-drawer.tsx` switches on this type
 * to pick an icon + label per event; the `event-feed.tsx` colors
 * crisis events specially.
 *
 * If the backend ever adds a new event type, the frontend's switch
 * cases default to a neutral icon — degraded but not broken. If the
 * backend renames an existing type, every event of that type silently
 * falls to default. The verify script catches both.
 *
 * Backend source: `backend/app/services/audit_log.py`, all `log_*`
 * functions. The verify script regex-extracts every `"type": "..."`
 * literal in that file.
 */
export const AUDIT_EVENT_TYPES = [
  "conversation_turn",
  "query_execution",
  "crisis_detected",
  "session_reset",
  "feedback",
  "location_feedback",
] as const;
export type AuditEventType = (typeof AUDIT_EVENT_TYPES)[number];

// ---------------------------------------------------------------------
// Routing buckets — emitted by audit_log._compute_routing, consumed by
// the Metrics tab Section 5
// ---------------------------------------------------------------------

/**
 * Routing buckets, in the order the metrics page renders them.
 * Backend source: `backend/app/services/audit_log.py ::
 * _compute_routing :: buckets = {...}`.
 *
 * The metrics page reads each key by name (`routing.buckets.service_flow`,
 * etc.). A renamed key silently shows 0 in the corresponding row of
 * Section 5. There are 6 keys total (the 6th, `general`, is surfaced
 * via `general_rate` rather than read directly from the dict, but the
 * verify script still asserts it's present in the backend dict so a
 * future renaming gets caught).
 */
export const ROUTING_BUCKET_KEYS = [
  "service_flow",
  "conversational",
  "emotional",
  "safety",
  "recovery",
  "general",
] as const;
export type RoutingBucketKey = (typeof ROUTING_BUCKET_KEYS)[number];
