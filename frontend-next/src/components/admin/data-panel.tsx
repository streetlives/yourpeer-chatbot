// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import type { ReactNode } from "react";
import type { UseDataSliceResult } from "@/hooks/use-data-slice";

/**
 * DataPanel — renders the right UI for each of the four slice states:
 *   - error: a recoverable error message with retry affordance
 *   - initial loading: caller-supplied skeleton
 *   - empty: caller-supplied empty state, or a default
 *   - loaded: caller-supplied children (render prop, gets data)
 *
 * The "loaded" state passes the typed `data` to a render-prop function so
 * downstream code can rely on data being non-null/non-empty without extra
 * narrowing.
 *
 * Why a render prop rather than children directly? Because TypeScript
 * can't narrow the type of `slice.data` based on a sibling check —
 * having DataPanel pass narrowed data into the children function lets
 * the consumer skip its own null-check.
 */

interface DataPanelProps<T> {
  slice: UseDataSliceResult<T>;

  /** Rendered while the slice has never loaded (no data, no error). */
  skeleton: ReactNode;

  /**
   * Predicate to decide whether the loaded data is "empty" and should
   * render the empty state. Defaults to checking falsy + zero-length array.
   * Override when "empty" has domain-specific meaning.
   */
  isEmpty?: (data: T) => boolean;

  /** Rendered when isEmpty() returns true. */
  emptyState?: ReactNode;

  /** Rendered on fetch error. Falls back to a default error UI. */
  errorState?: ReactNode;

  /** Render prop for the loaded state. Receives the slice's data. */
  children: (data: T) => ReactNode;
}

export function DataPanel<T>({
  slice,
  skeleton,
  isEmpty,
  emptyState,
  errorState,
  children,
}: DataPanelProps<T>) {
  if (slice.error) {
    return errorState ?? <DefaultErrorState onRetry={slice.refresh} />;
  }

  // Initial-load state: show the caller's skeleton. We deliberately don't
  // show the skeleton on every reload — once we have data, we keep showing
  // it (the loaded UI may show its own subtle "refreshing" indicator if
  // it cares).
  if (slice.isInitial) {
    return <>{skeleton}</>;
  }

  // Loaded state — but possibly empty.
  const checkEmpty = isEmpty ?? defaultIsEmpty;
  if (checkEmpty(slice.data)) {
    return emptyState ? <>{emptyState}</> : <DefaultEmptyState />;
  }

  return <>{children(slice.data)}</>;
}

// ---------------------------------------------------------------------------
// Default state UIs
// ---------------------------------------------------------------------------

function DefaultErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="text-center py-16">
      <div className="text-3xl mb-3">⚠️</div>
      <p className="text-neutral-500 mb-4">
        Could not load this data. The server may be unavailable.
      </p>
      <button
        onClick={onRetry}
        className="px-3.5 py-1.5 rounded-lg text-sm font-medium border border-neutral-200 bg-white text-neutral-700 hover:bg-neutral-50 transition"
      >
        Retry
      </button>
    </div>
  );
}

function DefaultEmptyState() {
  return (
    <div className="text-center py-16 text-neutral-400">
      <div className="text-3xl mb-3">📊</div>
      <p>No data yet.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Default emptiness check
// ---------------------------------------------------------------------------

function defaultIsEmpty<T>(data: T): boolean {
  if (data == null) return true;
  if (Array.isArray(data) && data.length === 0) return true;
  return false;
}
