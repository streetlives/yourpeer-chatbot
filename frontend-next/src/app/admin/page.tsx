// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { redirect } from "next/navigation";

/**
 * The bare /admin route redirects to /admin/overview, which is the
 * canonical landing page for the staff console. Without this redirect,
 * /admin would render an empty page (or worse, stale duplicate content)
 * and the error boundary's "Back to overview" link would take users
 * somewhere broken.
 */
export default function AdminPage() {
  redirect("/admin/overview");
}
