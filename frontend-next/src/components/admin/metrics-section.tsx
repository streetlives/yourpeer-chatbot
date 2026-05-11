// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { METRIC_GRID_COLS } from "./metric-row-grid";
import {
  collectMetricStatuses,
  countIssues,
  isHiddenByFilter,
  useIssueFilter,
  useSectionRegistry,
} from "@/lib/admin/metrics-page-helpers";

interface MetricsSectionProps {
  /** Section number (1-9). Used for the in-page anchor `metrics-section-N`
   *  the sticky nav scrolls to, and as the registry key when the section
   *  reports up to the page. Required for sections that should appear
   *  in the sticky nav; optional for ad-hoc one-offs. */
  sectionId?: number;
  /** Short label for the sticky nav chip (e.g. "Results"). The full
   *  `title` stays in the section header; this is the abbreviated
   *  version that fits in a chip on narrow viewports. */
  shortLabel?: string;
  title: string;
  description?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

export function MetricsSection({
  sectionId,
  shortLabel,
  title,
  description,
  children,
  defaultOpen = true,
}: MetricsSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const filterMode = useIssueFilter();
  const registry = useSectionRegistry();

  // Statuses + counts are derived from the section's children via a
  // recursive walk. useMemo keys on `children` identity — typical render
  // cycle in the metrics page rebuilds the JSX tree on every state
  // update, so the memo is effectively re-computed each render, but
  // the explicit memo keeps the dependency obvious and protects
  // against expensive re-walks if children identity stays stable.
  const statuses = useMemo(() => collectMetricStatuses(children), [children]);
  const issueCount = countIssues(statuses);
  const visibleRowCount = statuses.filter(
    (s) => !isHiddenByFilter(s, filterMode),
  ).length;

  // Report this section's summary up to the page registry BEFORE any
  // early return — the sticky nav must show this section's status
  // even when "Show only issues" filter has hidden the section itself.
  // Without this ordering, a section that the filter empties out
  // would disappear from the nav too, losing the visible signal that
  // those metrics exist.
  if (registry && sectionId !== undefined && shortLabel !== undefined) {
    registry.set(sectionId, { id: sectionId, shortLabel, statuses });
  }

  // Register an imperative "open me" callback with the page registry
  // so the sticky-nav chip can expand a collapsed section on click.
  // Without this, clicking a chip for a default-closed section
  // (sections 5-9) would scroll the page to the section header but
  // leave it folded, requiring a second click on the header itself.
  //
  // The opener is a fresh closure each render (captures the current
  // `setOpen`), but `setOpen` itself is stable, so the closure is
  // semantically identical across renders. We re-register on every
  // run anyway because that's what useEffect does with this dep list —
  // it's cheap (one Map.set per section per render-with-dep-change).
  useEffect(() => {
    if (!registry || sectionId === undefined) return;
    return registry.registerOpener(sectionId, () => setOpen(true));
  }, [registry, sectionId]);

  // Filter-aware section hide: when the user has toggled "issues only"
  // and this section's contents are now empty, drop the whole section
  // rather than render a header above a blank list. The section still
  // registered (above), so the nav chip continues to render with its
  // current status dot — just gray, since there are no issues here.
  if (filterMode === "issues" && visibleRowCount === 0) {
    return null;
  }

  return (
    <div
      id={sectionId !== undefined ? `metrics-section-${sectionId}` : undefined}
      // scroll-mt offset so the sticky nav (~44px) doesn't cover the
      // section title when jumped-to via the nav anchor link. Without
      // this, scrollIntoView lands the title flush with viewport top
      // and the sticky bar covers it.
      className="mb-9 scroll-mt-14"
    >
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between text-sm font-semibold text-neutral-700 dark:text-neutral-200 mb-3 pb-2 border-b border-neutral-200 dark:border-neutral-800 cursor-pointer hover:text-neutral-900 dark:hover:text-neutral-50 transition-colors"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <span>{title}</span>
          {issueCount > 0 && (
            <span
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-amber-100 text-amber-800 text-xs font-semibold dark:bg-amber-900/40 dark:text-amber-200"
              aria-label={`${issueCount} ${
                issueCount === 1 ? "metric needs" : "metrics need"
              } attention`}
            >
              <span aria-hidden="true">⚠</span>
              {issueCount} {issueCount === 1 ? "issue" : "issues"}
            </span>
          )}
        </span>
        <ChevronDown
          size={16}
          className={`transition-transform duration-200 ${open ? "" : "-rotate-90"}`}
          aria-hidden="true"
        />
      </button>
      {open && (
        <>
          {description && (
            <p className="text-sm text-neutral-500 dark:text-neutral-400 mb-4">
              {description}
            </p>
          )}
          {/* Header row — uses the same grid template as MetricRow so the
              header columns align with the data rows below. */}
          <div
            className={`grid ${METRIC_GRID_COLS} gap-3.5 pb-2 text-xs uppercase tracking-wider text-neutral-500 dark:text-neutral-400 font-semibold`}
          >
            <span>Metric</span>
            <span>Target</span>
            <span className="text-right">Current</span>
            <span className="text-right">Status</span>
            <span className="text-right">Phase</span>
          </div>
          {children}
        </>
      )}
    </div>
  );
}
