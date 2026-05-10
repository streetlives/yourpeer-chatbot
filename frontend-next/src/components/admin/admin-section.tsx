// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { forwardRef, type ReactNode } from "react";

/**
 * Section wrapper used by the Locations admin page (and any other
 * admin page with the same `<h2>title</h2><p>description</p><body>`
 * pattern).
 *
 * Replaces ~80 LOC of repeated `<div className="mt-6"><h2 ...>Title
 * </h2><p ...>Description</p><Component /></div>` blocks. Future
 * contributors should reach for this rather than copy-pasting another
 * div sandwich.
 *
 * Forwards a ref to the heading element so the page can wire up
 * smooth-scroll targets (e.g. the freshness-histogram → triage-table
 * click-through scrolls to the heading rather than the table itself,
 * for better visual landing). Use of `forwardRef` is intentional
 * here; this is the one place external code needs DOM-level access.
 */
export interface AdminSectionProps {
  title: string;
  /** Optional explanatory paragraph below the heading. Most sections
   *  benefit from this; a couple don't (the stat strip's purpose is
   *  obvious). When omitted, the heading sits directly above the
   *  body with the same `mb-3` spacing the description would have
   *  provided. */
  description?: ReactNode;
  children: ReactNode;
  /** When set, applied to the outer wrapper. Defaults to "mt-6"
   *  which matches every section on the Locations page. Override
   *  for sections that need different spacing (e.g. the first section
   *  with no top margin). */
  className?: string;
  /** Optional id for deep-linking (e.g. ?section=feedback). The
   *  Locations page doesn't use this yet but it's a low-cost addition
   *  for v1.5. */
  id?: string;
}

export const AdminSection = forwardRef<HTMLHeadingElement, AdminSectionProps>(
  function AdminSection({ title, description, children, className, id }, ref) {
    return (
      <div className={className ?? "mt-6"} id={id}>
        <h2
          ref={ref}
          className="text-base font-semibold mb-3 text-neutral-900 dark:text-neutral-100"
        >
          {title}
        </h2>
        {description && (
          <p className="text-xs text-neutral-500 dark:text-neutral-400 mb-3">
            {description}
          </p>
        )}
        {children}
      </div>
    );
  },
);
