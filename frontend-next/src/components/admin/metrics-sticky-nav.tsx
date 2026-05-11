// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { SectionSummary } from "@/lib/admin/metrics-page-helpers";
import { countIssues } from "@/lib/admin/metrics-page-helpers";

interface MetricsStickyNavProps {
  sections: SectionSummary[];
  /** Section id currently in view — that chip gets a filled background
   *  to confirm "you are here" as the user scrolls. Passed from the
   *  page's IntersectionObserver. */
  activeId?: number;
  /** Called when a chip is clicked, BEFORE the scroll happens. The
   *  page uses this to expand the target section if it's collapsed
   *  (default-closed sections 5-9, or any section the user has
   *  folded). Returns are ignored — the nav still scrolls regardless. */
  onChipClick?: (id: number) => void;
}

/**
 * Sticky in-page nav for the metrics dashboard.
 *
 * Renders one chip per section (1-9) showing its number + short label
 * + a status dot. The dot's color summarizes the section's worst-case
 * metric status:
 *   - red: at least one off-target metric (something needs fixing)
 *   - amber: warning(s) but no off-target (worth watching)
 *   - gray: section has no measurable issues (on-target / no-data /
 *           tracking) — neutral dot rather than green, to avoid
 *           encouraging admins to ignore them
 *
 * Clicking a chip scrolls the matching section into view. Sections
 * with anchor `metrics-section-N` receive the smooth-scroll behavior;
 * the section component owns its own `scroll-mt` offset to keep its
 * title from sliding under this sticky bar.
 *
 * Sticks with `position: sticky; top: 0` inside the page content. The
 * z-index is high enough to clear the section borders that scroll
 * past underneath. The bar is full-bleed (-mx-7) to align with the
 * admin layout's edges; the inner content recenters with px-7.
 */
export function MetricsStickyNav({
  sections,
  activeId,
  onChipClick,
}: MetricsStickyNavProps) {
  return (
    <nav
      aria-label="Metrics sections"
      // -mx-7 / px-7 is paired with the admin layout's px-7 — the bar
      // visually spans the full page width, with the chips re-aligned
      // to the same gutter as the rest of the dashboard.
      className="sticky top-0 z-30 -mx-7 px-7 mb-5 py-2 bg-neutral-100/95 backdrop-blur-sm border-b border-neutral-200 dark:bg-neutral-950/95 dark:border-neutral-800"
    >
      <div
        className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide"
      >
        {sections.map((s) => {
          const issues = countIssues(s.statuses);
          const offTargets = s.statuses.filter((x) => x === "off-target").length;
          // Dot color priority: off-target > warning > neutral
          const dotClass = offTargets > 0
            ? "bg-red-500 dark:bg-red-400"
            : issues > 0
            ? "bg-amber-500 dark:bg-amber-400"
            : "bg-neutral-300 dark:bg-neutral-700";
          const ariaLabel = issues > 0
            ? `Section ${s.id} ${s.shortLabel}, ${issues} ${
                issues === 1 ? "metric" : "metrics"
              } needing attention`
            : `Section ${s.id} ${s.shortLabel}`;
          const isActive = activeId === s.id;
          // Active chip uses a maximum-contrast neutral inversion —
          // near-black fill + true white text in light mode, true
          // white fill + near-black text in dark mode. Both
          // combinations sit at ~18:1 contrast which is far above
          // any accessibility threshold and unmistakably visible
          // against the surrounding nav bg.
          //
          // Earlier iterations used amber-100 (too washed-out for a
          // selected state) and then neutral-800 (correct in theory
          // but apparently still rendered unclear on some setups —
          // possibly an opacity / blending interaction with the
          // sticky-nav backdrop-blur). Going one step darker (-900
          // / pure white) removes any ambiguity.
          //
          // The number prefix inherits the chip's text color when
          // active (no explicit override) so it stays at full
          // contrast; inactive chips keep their muted gray number.
          return (
            <a
              key={s.id}
              href={`#metrics-section-${s.id}`}
              aria-label={ariaLabel}
              aria-current={isActive ? "true" : undefined}
              onClick={(e) => {
                // Honor the anchor jump but use smooth-scroll explicitly
                // — anchor jumps are instant by default in most browsers,
                // which feels jarring with a sticky header.
                e.preventDefault();
                // Open the target section first if a handler was
                // provided. This is intentionally before scrollIntoView:
                // the section's top doesn't move when it opens (only
                // its bottom extends), so scrolling to the same anchor
                // either way lands at the same place, and pre-opening
                // means the user sees expanded content immediately when
                // they arrive rather than a folded header that needs
                // a second click. If the registered opener is missing
                // (e.g., the section is currently filter-hidden), the
                // call is a no-op and the scroll still proceeds.
                onChipClick?.(s.id);
                const node = document.getElementById(
                  `metrics-section-${s.id}`,
                );
                node?.scrollIntoView({ behavior: "smooth", block: "start" });
                // Update the URL hash so back/forward work and the chip
                // can be deep-linked. `history.replaceState` keeps the
                // back-button history clean.
                window.history.replaceState(
                  null,
                  "",
                  `#metrics-section-${s.id}`,
                );
              }}
              className={`flex-shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium transition-colors ${
                isActive
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-300 dark:hover:bg-neutral-800/80"
              }`}
            >
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full ${dotClass}`}
                aria-hidden="true"
              />
              <span
                className={`tabular-nums ${
                  isActive ? "" : "text-neutral-400 dark:text-neutral-500"
                }`}
              >
                {s.id}
              </span>
              <span className="hidden sm:inline">{s.shortLabel}</span>
            </a>
          );
        })}
      </div>
    </nav>
  );
}
