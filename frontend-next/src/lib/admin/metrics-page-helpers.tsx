// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { Children, createContext, isValidElement, useContext } from "react";
import { MetricRow } from "@/components/admin/metric-row";

// Re-declare the status type here rather than importing from metric-row
// (which would create a circular dep) — keep in sync. metric-row.tsx is
// the source of truth for the full list.
export type MetricStatus =
  | "on-target"
  | "warning"
  | "off-target"
  | "no-data"
  | "tracking";

/**
 * Walk a React subtree and pull every MetricRow's `status` prop.
 *
 * Called from MetricsSection during its own render: each section
 * derives its issue count (and visibility under the "Show only
 * issues" filter) by walking its own children. The status array is
 * also reported up to the page via SectionRegistryContext so the
 * sticky-nav chips can show per-section status dots — see the
 * SectionRegistry section below.
 *
 * The walk is recursive because MetricRows are sometimes nested inside
 * fragments / conditionals / `.map()` results inside a section (see
 * the tone-tier metrics in section 4). React.Children automatically
 * flattens fragments and skips falsy values (null, false), so the
 * walk handles those without special-casing.
 *
 * Wrappers that ARE relevant to walk through:
 *   - Fragments (auto-flattened by Children)
 *   - Arrays from `.map(...)` (auto-flattened)
 *   - Conditional `{cond && <MetricRow .../>}` (false is dropped)
 *
 * Wrappers NOT walked into:
 *   - DOM elements like `<div>` — we don't expect MetricRows nested
 *     inside arbitrary wrapper divs in metrics/page.tsx. If that ever
 *     changes, extend this walk.
 *
 * The identity check `child.type === MetricRow` requires MetricRow to
 * be a stable function reference (it is — module-level export). If
 * MetricRow ever moves behind React.memo or React.forwardRef, the
 * identity will change and this check needs to use the wrapped type.
 */
export function collectMetricStatuses(node: React.ReactNode): MetricStatus[] {
  const results: MetricStatus[] = [];
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === MetricRow) {
      // Type-narrow via the known prop shape. We don't import the props
      // interface from metric-row to keep this module free of that
      // dependency cycle; the shape is stable contract.
      const props = child.props as { status?: MetricStatus };
      if (props.status) results.push(props.status);
      return;
    }
    // Recurse into children of non-MetricRow elements. Catches
    // fragments-of-fragments and any elements that themselves hold
    // children (e.g. the `<></>` wrapping a conditional block).
    const childChildren = (child.props as { children?: React.ReactNode }).children;
    if (childChildren !== undefined) {
      results.push(...collectMetricStatuses(childChildren));
    }
  });
  return results;
}

/**
 * Count how many statuses in an array qualify as "issues" — i.e.,
 * something an admin should look at. `off-target` is a confirmed
 * problem; `warning` is the soft band approaching a problem. The
 * other three (`on-target`, `no-data`, `tracking`) are NOT issues:
 *   - on-target: metric is meeting its goal
 *   - no-data: metric isn't measurable yet, not a problem to fix
 *   - tracking: informational, no target
 */
export function countIssues(statuses: MetricStatus[]): number {
  return statuses.filter((s) => s === "off-target" || s === "warning").length;
}

// ---------------------------------------------------------------------
// Issue filter context — for the "Show only issues" toggle.
// MetricRow reads from this; if the filter is `issues` and the row's
// status is not warning/off-target, it renders null. Sections with
// zero visible rows similarly render null (computed by the page via
// collectMetricStatuses + countIssues).
// ---------------------------------------------------------------------

export type IssueFilterMode = "all" | "issues";

export const IssueFilterContext = createContext<IssueFilterMode>("all");

export function useIssueFilter(): IssueFilterMode {
  return useContext(IssueFilterContext);
}

/**
 * True when the given row status should be hidden under the current
 * filter mode. Centralizes the rule so MetricRow, MetricsSection (for
 * its own visibility), and the sticky-nav badge stay aligned.
 */
export function isHiddenByFilter(
  status: MetricStatus,
  mode: IssueFilterMode,
): boolean {
  if (mode === "all") return false;
  return status !== "off-target" && status !== "warning";
}

// ---------------------------------------------------------------------
// Section registry — each MetricsSection reports its summary up to the
// page (during its own render) so the sticky nav at the top can show
// per-section status without the page having to hoist every section's
// JSX or walk the rendered tree.
//
// How it works:
//   - Page creates a Map ref + a stable `set` callback, provides it
//     via SectionRegistryContext.
//   - During each MetricsSection render, the section calls
//     registry.set(id, { ...summary }). The Map is keyed by section
//     id, so a re-render of the same section overwrites — no stale
//     duplicates.
//   - Page runs a useLayoutEffect that snapshots the Map to state if
//     the contents changed. Sticky nav reads from that state.
//   - Synchronous read-after-render works because the page's effect
//     fires AFTER all child renders in the same commit phase.
//
// Stale-entry consideration: sections aren't dynamically added or
// removed in this dashboard (always 9), and MetricsSection registers
// BEFORE its early-return on filter-empty so even hidden sections
// keep the nav data fresh. If sections ever become dynamic, switch
// to a register-on-mount / unregister-on-unmount effect pattern.
// ---------------------------------------------------------------------

export interface SectionSummary {
  id: number;
  shortLabel: string;
  statuses: MetricStatus[];
}

export interface SectionRegistry {
  /** Section calls this during render to report its current summary
   *  up to the page (for the sticky nav). Map-keyed by id so
   *  re-renders overwrite cleanly. */
  set: (id: number, summary: SectionSummary) => void;
  /** Section registers an imperative opener (its own
   *  `() => setOpen(true)`) on mount. Returned function unregisters
   *  on unmount. The page uses this so the sticky-nav chip can
   *  expand a section when clicked even if the user had collapsed
   *  it (or if it defaulted closed — sections 5-9). Imperative is
   *  the right primitive here: the chip click is a one-shot event,
   *  not a reactive state change. */
  registerOpener: (id: number, opener: () => void) => () => void;
  /** Invoke the registered opener for a section. No-op if the
   *  section hasn't registered (e.g. the filter hid it). Returns
   *  whether an opener was actually found and called — useful for
   *  callers that want to know whether the section was reachable. */
  open: (id: number) => boolean;
}

export const SectionRegistryContext = createContext<SectionRegistry | null>(null);

export function useSectionRegistry(): SectionRegistry | null {
  return useContext(SectionRegistryContext);
}

/**
 * Shallow-compare two arrays of section summaries by content.
 * Used by the page's useLayoutEffect to avoid setState when the
 * registry contents haven't changed (which would otherwise loop
 * indefinitely: setState → re-render → useLayoutEffect → setState).
 *
 * Order matters — the page passes summaries sorted by id, and the
 * comparison expects matched order. The inner statuses array is
 * compared element-by-element; with ~5-15 statuses per section and
 * 9 sections, this is < 200 string compares total, trivial cost.
 */
export function summariesEqual(
  a: SectionSummary[],
  b: SectionSummary[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id) return false;
    if (a[i].shortLabel !== b[i].shortLabel) return false;
    if (a[i].statuses.length !== b[i].statuses.length) return false;
    for (let j = 0; j < a[i].statuses.length; j++) {
      if (a[i].statuses[j] !== b[i].statuses[j]) return false;
    }
  }
  return true;
}
