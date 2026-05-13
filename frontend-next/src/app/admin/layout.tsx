// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import type { Metadata } from "next";
import { AdminShell } from "./admin-shell";

export const metadata: Metadata = {
  title: "YourPeer — Staff Review Console",
};

/**
 * Server component. Its only job is to export the route's metadata
 * (which client components can't do) and hand off to `<AdminShell>`
 * for the actual layout markup.
 *
 * Splitting the file this way lets `AdminShell` be a client
 * component — needed because it mounts Radix's `Tooltip.Provider`
 * (a React context provider, client-only) so any admin page can use
 * tooltips. The previous one-file layout couldn't host the provider
 * without giving up the `metadata` export.
 */
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AdminShell>{children}</AdminShell>;
}
