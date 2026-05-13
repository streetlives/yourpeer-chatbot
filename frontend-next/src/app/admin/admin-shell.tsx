// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import { AdminNav } from "@/components/admin/admin-nav";
import { AdminAuthGuard } from "@/components/admin/admin-auth-guard";
import { LogoutButton } from "@/components/admin/logout-button";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * The interactive shell for every admin page — header, nav strip,
 * auth guard, and any tooltip/dialog providers that descendants
 * rely on. Split out of `layout.tsx` because `layout.tsx` is a
 * server component (for the `metadata` export) and a
 * Radix `Tooltip.Provider` is a React context provider, which means
 * it has to live in a client component.
 *
 * Tooltip.Provider configuration:
 *
 *   - delayDuration={150}: shorten the default 700ms hover-delay.
 *     On small targets like the locations-table data badges (phone
 *     / addr / hrs / revs, each ~24px wide), the default delay is
 *     long enough that users move on before the tooltip ever
 *     appears. 150ms feels reactive but slow enough that the
 *     tooltip doesn't fire from incidental cursor passes.
 *
 *   - skipDelayDuration={300}: once any tooltip has appeared, the
 *     next one within 300ms shows instantly. Matters when scanning
 *     a row of badges side-by-side — after seeing the first,
 *     moving the cursor one badge over should surface the next
 *     tooltip immediately, not re-impose the 150ms delay.
 */
export function AdminShell({ children }: { children: React.ReactNode }) {
  return (
    <Tooltip.Provider delayDuration={150} skipDelayDuration={300}>
      <AdminAuthGuard>
        <div className="min-h-dvh bg-neutral-100 dark:bg-neutral-950 text-neutral-900 dark:text-neutral-100">
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
    </Tooltip.Provider>
  );
}
