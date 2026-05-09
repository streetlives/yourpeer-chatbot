// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";

/**
 * Stateless pagination control for admin tables.
 *
 * The parent owns page state and pageSize state — TablePagination is
 * purely presentational. This split matters: when a parent's filter
 * state changes, the parent needs to reset page to 1, which is awkward
 * if page state lived inside this component.
 *
 * Design choices:
 *
 * - Prev / Next + page indicator (vs. numbered links). Numbered links
 *   are more useful when "page 7" is a meaningful bookmark, but the
 *   data here streams in continuously and sorts rearrange under the
 *   user's feet — page numbers aren't stable enough to be worth the
 *   visual cost. Prev/Next + first/last covers the practical access
 *   patterns (one step back, one step forward, jump to extremes).
 *
 * - First / Last buttons surface explicitly because admins often want
 *   to see "what's most recent" (page 1 when default sort is desc) or
 *   "what's oldest" (last page) without clicking through.
 *
 * - Page size selector inline. 25 / 50 / 100 covers the realistic
 *   range — below 25 a user is paginating too much; above 100 the
 *   table itself becomes the bottleneck. Default 25.
 */

export const DEFAULT_PAGE_SIZE = 25;
export const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;
export type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

interface TablePaginationProps {
  /** Total number of items in the (pre-pagination) view. */
  totalItems: number;
  /** Current 1-indexed page. */
  page: number;
  /** Items per page. */
  pageSize: PageSize;
  /** Called when the user navigates to a different page. */
  onPageChange: (page: number) => void;
  /** Called when the user changes the page size. Parent should
   *  also reset to page 1 in response. */
  onPageSizeChange: (pageSize: PageSize) => void;
  /** Optional ARIA label override for the navigation region. */
  ariaLabel?: string;
}

export function TablePagination({
  totalItems,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  ariaLabel = "Table pagination",
}: TablePaginationProps) {
  // Clamp `page` defensively: if a parent passes a stale page after a
  // filter narrowed the result set, we don't want to render "page 8 of
  // 2." Parents SHOULD reset on filter change, but this is the safety
  // net.
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);

  const isFirstPage = safePage === 1;
  const isLastPage = safePage === totalPages;

  // Render-the-range string ("Showing 1–25 of 247"). Compute against
  // the actual page rather than the clamped safe value so the count
  // matches what the table is actually displaying.
  const startItem = totalItems === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const endItem = Math.min(safePage * pageSize, totalItems);

  return (
    <nav
      aria-label={ariaLabel}
      className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 bg-neutral-50 border-t border-neutral-200 text-xs text-neutral-500"
    >
      {/* Left: range string */}
      <div>
        Showing <span className="font-mono text-neutral-700">{startItem}</span>
        –<span className="font-mono text-neutral-700">{endItem}</span>
        {" of "}
        <span className="font-mono text-neutral-700">{totalItems}</span>
      </div>

      {/* Right: page-size selector + nav buttons */}
      <div className="flex items-center gap-3">
        {/* Page size — inline with the nav, not a separate row, so the
            entire pagination affordance is one visual unit. */}
        <label className="flex items-center gap-1.5">
          <span>Per page</span>
          <select
            value={pageSize}
            onChange={(e) =>
              onPageSizeChange(Number(e.target.value) as PageSize)
            }
            aria-label="Items per page"
            className="px-2 py-1 border border-neutral-200 rounded-md bg-white text-neutral-700 text-xs focus:outline-none focus:border-amber-400 focus:ring-1 focus:ring-amber-400"
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>

        {/* Page nav: First, Prev, indicator, Next, Last. The indicator
            is a static "Page X of Y" — non-clickable. Numbered links
            were considered and rejected (see file header). */}
        <div className="flex items-center gap-0.5">
          <NavButton
            label="First page"
            disabled={isFirstPage}
            onClick={() => onPageChange(1)}
            icon={<ChevronsLeft size={14} />}
          />
          <NavButton
            label="Previous page"
            disabled={isFirstPage}
            onClick={() => onPageChange(safePage - 1)}
            icon={<ChevronLeft size={14} />}
          />
          <span className="px-2.5 py-1 text-neutral-600 font-medium tabular-nums">
            Page <span className="font-mono">{safePage}</span> of{" "}
            <span className="font-mono">{totalPages}</span>
          </span>
          <NavButton
            label="Next page"
            disabled={isLastPage}
            onClick={() => onPageChange(safePage + 1)}
            icon={<ChevronRight size={14} />}
          />
          <NavButton
            label="Last page"
            disabled={isLastPage}
            onClick={() => onPageChange(totalPages)}
            icon={<ChevronsRight size={14} />}
          />
        </div>
      </div>
    </nav>
  );
}

interface NavButtonProps {
  label: string;
  disabled: boolean;
  onClick: () => void;
  icon: React.ReactNode;
}

function NavButton({ label, disabled, onClick, icon }: NavButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="w-7 h-7 inline-flex items-center justify-center rounded-md text-neutral-500 transition-colors hover:bg-white hover:text-neutral-700 hover:border-neutral-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-neutral-500"
    >
      {icon}
    </button>
  );
}
