// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Single source of truth for LLM-as-Judge eval dimensions.
 *
 * Both the Metrics tab (`metrics/page.tsx`, Section 8) and the Evals tab
 * (`eval-results.tsx`) consume from this module. Without it, the two tabs
 * had drifted: Confirmation UX target was 4.5 in Metrics but 3.5 in Evals,
 * Error Recovery had the same split, and dimension labels were near-but-
 * not-quite-identical. Anyone reading both tabs side by side saw two
 * different stories about what passing meant on the same dimension.
 *
 * If a target value or label changes, change it here. Both pages will
 * pick up the update automatically.
 *
 * NOTE: This file owns the *display* targets — what the UI uses to color
 * pills and decide pass/fail. The eval engineering plan v2 calls for the
 * eval report itself to begin recording per-dimension empirical variance
 * (Foundation 1) and human-correlation calibration (Foundation 2), at
 * which point the "is this dimension passing?" question becomes more
 * nuanced than a single threshold. When that lands, this module will
 * grow to surface those signals; for now, simple thresholds are correct.
 */

export interface EvalDimension {
  /** Backend / report key — must match the keys in EvalReport.summary.dimension_averages */
  key: string;
  /** Long label used on the Metrics tab (where dimensions sit in a wide "Metric" column) */
  label: string;
  /** Short label used on the Evals tab (where dimensions sit in a tighter dimension list) */
  shortLabel: string;
  /** Score threshold for the dimension to be considered "on target" */
  target: number;
  /** True if this dimension is a deploy-blocker — failures should surface prominently */
  blocker?: boolean;
}

/**
 * Ordered list of all 11 LLM-as-Judge dimensions, as of the R28+ rubric.
 *
 * Order matters: this is the order both tabs render dimensions in. Top-of-list
 * dimensions are surfaced first, so safety-adjacent dimensions sit higher than
 * dialog polish.
 */
export const EVAL_DIMENSIONS: EvalDimension[] = [
  { key: "slot_extraction", label: "Slot Extraction Accuracy", shortLabel: "Slot Extraction", target: 4.0 },
  { key: "dialog_efficiency", label: "Dialog Efficiency", shortLabel: "Dialog Efficiency", target: 3.5 },
  { key: "response_tone", label: "Response Tone", shortLabel: "Response Tone", target: 4.0 },
  { key: "safety_crisis", label: "Safety & Crisis Handling", shortLabel: "Safety & Crisis", target: 4.5, blocker: true },
  { key: "confirmation_ux", label: "Confirmation UX", shortLabel: "Confirmation UX", target: 4.5 },
  { key: "privacy", label: "Privacy", shortLabel: "Privacy", target: 4.5 },
  { key: "hallucination_resistance", label: "Hallucination Resistance", shortLabel: "Hallucination Resistance", target: 4.5, blocker: true },
  { key: "error_recovery", label: "Error Recovery", shortLabel: "Error Recovery", target: 4.5 },
  { key: "dignity_anti_stigma", label: "Dignity & Anti-Stigma", shortLabel: "Dignity & Anti-Stigma", target: 4.0 },
  { key: "cultural_responsiveness", label: "Cultural Responsiveness", shortLabel: "Cultural Responsiveness", target: 4.0 },
  { key: "equity_of_access", label: "Equity of Access", shortLabel: "Equity of Access", target: 4.0 },
];

// ---------------------------------------------------------------------------
// Convenience lookups
// ---------------------------------------------------------------------------

/** Map of dimension key → short label, for quick lookup in scenario detail rendering. */
export const DIM_SHORT_LABELS: Record<string, string> = Object.fromEntries(
  EVAL_DIMENSIONS.map((d) => [d.key, d.shortLabel]),
);

/** Set of deploy-blocker dimension keys, for quick membership testing. */
export const BLOCKER_KEYS: ReadonlySet<string> = new Set(
  EVAL_DIMENSIONS.filter((d) => d.blocker).map((d) => d.key),
);

/**
 * Return the dimension definition for a given key, or undefined if unknown.
 * Useful for scenario-detail rendering where the report may include legacy
 * keys that no longer exist in the current dimension list.
 */
export function getDimension(key: string): EvalDimension | undefined {
  return EVAL_DIMENSIONS.find((d) => d.key === key);
}
