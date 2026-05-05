// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Default per-table row limits for admin list endpoints.
 *
 * These are consumed by both the API layer (`lib/chat/api.ts`, where they
 * supply the `limit` query parameter when callers don't override it) and
 * the admin store (`lib/admin/store.ts`, where they're passed explicitly
 * to the fetchers driving the dashboard pages).
 *
 * Centralizing prevents the historical drift where api.ts defaulted to
 * smaller limits (100/100/200) than what the dashboard actually wanted
 * (200/50/500). The numbers are tuned for the dashboard:
 *   - Conversations: 200 covers ~24 hours of pilot traffic with margin.
 *   - Queries: 500 because each conversation can fire 1–5 queries, so
 *     500 ≈ same time window as 200 conversations.
 *   - Events: 50 because the overview's event feed is a "recent activity"
 *     widget — older events are visible via the conversation detail
 *     drawer, not the feed itself.
 *
 * If a caller needs a different value (a backfill script, a one-off
 * report), pass it explicitly. These are the dashboard's defaults, not
 * a hard ceiling.
 */
export const CONVERSATIONS_LIMIT = 200;
export const QUERIES_LIMIT = 500;
export const EVENTS_LIMIT = 50;
