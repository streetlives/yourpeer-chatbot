// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { redirect } from "next/navigation";

/**
 * Bare `/admin` route — server-side redirect to `/admin/overview`,
 * the canonical landing page for the Staff Review Console.
 *
 * Why this file is a redirect and not a render:
 *
 * `AdminNav` highlights the active tab by exact pathname match
 * (`pathname === item.href`). If `/admin` rendered any tab's content
 * directly, no chip in the nav strip would be highlighted as
 * active — the user lands on a page with no orienting signal about
 * which view they're on. Redirecting to `/admin/overview` keeps the
 * nav and the rendered content in sync.
 *
 * Why server-side (`redirect()`) and not a client-side router push:
 *
 * `redirect()` from `next/navigation` short-circuits the response
 * before any HTML is sent — the browser receives a 307 to
 * `/admin/overview` and never sees the bare `/admin` document. No
 * flash of unstyled content, no double-render, no client-side
 * router roundtrip. The redirect happens before
 * `AdminAuthGuard` mounts, so an unauthenticated visitor still ends
 * up on `/admin/overview` and sees the login form there, exactly as
 * if they had typed `/admin/overview` directly.
 *
 * Why not `next.config.js` redirects:
 *
 * A page-level redirect keeps the routing decision discoverable next
 * to the route itself — anyone reading `src/app/admin/` sees the
 * intent without cross-referencing the global config. There's only
 * one such redirect in this app; the convenience of a single
 * routes-config entry wouldn't outweigh that locality.
 *
 * History: this file was previously a near-duplicate of
 * `src/app/admin/conversations/page.tsx` (rendered the same
 * `ConversationTable` with the same `useDataSlice("conversations")`
 * call). That left `/admin` showing the conversations table with no
 * active tab, which was a bug, not the intended landing experience.
 */
export default function AdminPage() {
  redirect("/admin/overview");
}
