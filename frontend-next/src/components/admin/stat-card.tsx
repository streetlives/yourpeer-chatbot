// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

interface StatCardProps {
  label: string;
  value: string | number;
  note?: string | null;
  colorClass?: string;
  /**
   * Optional click handler. When provided, the card behaves as an
   * accessible button: cursor changes, hover/focus ring appears,
   * Enter and Space activate it, the whole card becomes the click
   * target. When omitted, the card renders as a plain div with no
   * interactive treatment — matching the original API so existing
   * call sites (eval-results, locations top-stat-strip) don't need
   * to change.
   *
   * Receives the card's label string. The Overview page uses this
   * to look up the matching `StatCardDefinition` and open the
   * explainer dialog. Keeping the contract label-based (rather than
   * passing a definition object straight into StatCard) preserves
   * the separation between this UI primitive and the dialog data —
   * StatCard stays a dumb visual; the consumer owns the meaning.
   */
  onClick?: (label: string) => void;
}

export function StatCard({
  label,
  value,
  note,
  colorClass,
  onClick,
}: StatCardProps) {
  // Visual is identical whether onClick is set or not. The only
  // differences when interactive: cursor, hover/focus ring, and the
  // accessibility attributes that turn a div into a button. Keeping
  // the resting appearance unchanged means non-clickable cards
  // (the eval-results and locations call sites) look exactly as
  // they did before.
  const interactive = onClick !== undefined;

  // Hover/focus ring color follows the precedent set by
  // EvalSummaryCard in eval-results.tsx — amber-300 on hover,
  // amber-400 on keyboard focus — so the admin's interactive-card
  // affordance reads as a single pattern across surfaces.
  const interactiveClasses = interactive
    ? "cursor-pointer transition hover:ring-2 hover:ring-amber-300 hover:ring-offset-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-1 dark:hover:ring-amber-500/70 dark:focus-visible:ring-amber-400"
    : "";

  return (
    <div
      className={`bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 ${interactiveClasses}`}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={
        interactive
          ? `${label}${note ? ` (${note})` : ""} — open details`
          : undefined
      }
      onClick={interactive ? () => onClick(label) : undefined}
      onKeyDown={
        interactive
          ? (e) => {
              // Enter and Space both activate buttons by convention;
              // preventDefault on Space stops the page from
              // scrolling when the card has focus.
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick(label);
              }
            }
          : undefined
      }
    >
      <div className="text-sm font-semibold text-neutral-500 dark:text-neutral-400 mb-1.5">
        {label}
        {note && (
          <span className="ml-1.5 text-xs font-normal text-neutral-400 dark:text-neutral-500">
            {note}
          </span>
        )}
      </div>
      <div
        className={`text-2xl font-bold tracking-tight ${
          colorClass || "text-neutral-900 dark:text-neutral-100"
        }`}
      >
        {value ?? "—"}
      </div>
    </div>
  );
}
