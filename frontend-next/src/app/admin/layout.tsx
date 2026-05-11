// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import type { Metadata } from "next";
import { AdminNav } from "@/components/admin/admin-nav";
import { AdminAuthGuard } from "@/components/admin/admin-auth-guard";
import { LogoutButton } from "@/components/admin/logout-button";
import { ThemeToggle } from "@/components/theme-toggle";

export const metadata: Metadata = {
  title: "YourPeer — Staff Review Console",
};

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AdminAuthGuard>
      <div className="min-h-dvh bg-neutral-100 dark:bg-neutral-950">
        <div className="max-w-[1280px] mx-auto px-7 py-6">
          <header className="flex items-center justify-between pb-5 border-b border-neutral-300 dark:border-neutral-800 mb-6">
            <div className="flex items-baseline gap-3">
              <h1 className="text-xl font-bold tracking-tight text-amber-500 dark:text-amber-400">
                YourPeer
              </h1>
              <span className="text-sm text-neutral-400 dark:text-neutral-500">
                Staff Review Console
              </span>
            </div>
            {/* ThemeToggle sits to the LEFT of Sign out so staff can flip
                between light and dark while reviewing the admin console.
                The toggle is shared with the chat — same button, same
                cycle (system → light → dark → system), same hydration
                guard. */}
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <LogoutButton />
            </div>
          </header>

          <AdminNav />

          {children}
        </div>
      </div>
    </AdminAuthGuard>
  );
}
