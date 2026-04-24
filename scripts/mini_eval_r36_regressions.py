#!/usr/bin/env python3
"""Mini-eval for the R36 unified-path regressions and watch-list scenarios.

Runs a curated subset of scenarios against the unified extractor path:
    - All 12 scenarios that were failing in R36 Unified (avg < 4.0)
    - 3 borderline/headline scenarios worth monitoring:
        * multi_shower_and_food_drop_in (was 4.00 — Option 4 target)
        * multi_clothing_and_food_harlem (was 4.64 — verify no regression)
        * multi_cross_borough_food_brooklyn_shelter_manhattan (was 4.73 —
          the migration's headline win, must stay high)

The script reuses `simulate_conversation`, `judge_conversation`, and
`generate_report` from the main eval runner so results are directly
comparable to R36's output shape. The output includes both the current
run's scores and the R36 Legacy/Unified baselines, so you can see at a
glance which scenarios recovered, held, or regressed.

USAGE:
    USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... \
        python scripts/mini_eval_r36_regressions.py

    # Save JSON alongside the printed summary
    USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... \
        python scripts/mini_eval_r36_regressions.py \
        --output mini_eval_r36_regressions.json

    # Smaller subset (only Option 4 watch-list + the big-win)
    USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... \
        python scripts/mini_eval_r36_regressions.py \
        --subset option-4

COST / TIME:
    Full set (15 scenarios): ~3-6 minutes, ~$2-4 in Opus judge costs.
    --subset option-4 (4 scenarios): ~60-90s, ~$0.50.
    Full eval for comparison: 30-60 min, ~$15-25.

EXIT CODES:
    0 — all scenarios pass (avg ≥ 4.0)
    1 — one or more scenarios failing
    2 — usage / environment error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure the repo's `backend/` and `tests/` are on the path before importing
# the eval runner. The runner expects to be invoked from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Target scenario catalog
# ---------------------------------------------------------------------------
# R36 Legacy and R36 Unified baselines recorded inline so the output can
# show deltas without requiring the JSON reports to be colocated.

# (scenario_id, category, r36_legacy, r36_unified, notes)
# Categories: A=watch-list (Option 4), B=narrative (Option 2b), C=port-needed,
#             D=borderline, H=held-from-legacy, L=long-standing, W=big-win
_TARGETS = [
    # --- Failing in R36 unified (avg < 4.0) ---
    ("natural_long_story",                               "B", 4.45, 3.91, "Option 2b targets"),
    ("multiturn_change_mind",                            "D", 4.00, 3.91, "borderline"),
    ("accessibility_low_literacy",                       "C", 4.73, 3.73, "C.2 port needed"),
    ("confirm_multi_change",                             "C", 4.73, 3.55, "C.1 port needed"),
    ("wa_substance_use_shelter",                         "D", 4.09, 3.91, "borderline"),
    ("wa_negative_preference",                           "H", 3.82, 3.82, "held from legacy"),
    ("multi_food_and_shelter_brooklyn",                  "A", 4.36, 3.91, "Option 4 targets"),
    ("multi_accept_queued_shelter",                      "C", 4.36, 3.82, "C.3 — may recover from Option 4"),
    ("multi_cross_neighborhood_shower_les_food_chinatown","A", 4.00, 3.55, "Option 4 targets"),
    ("peer_young_mom_multiple_needs",                    "D", 4.18, 3.91, "borderline"),
    ("peer_diabetic_insulin",                            "L", 2.64, 3.00, "long-standing"),
    ("peer_aging_out_foster",                            "H", 3.73, 3.55, "held, worsened in unified"),
    # --- Borderline passing / watch-list (worth monitoring) ---
    ("multi_shower_and_food_drop_in",                    "A", 4.45, 4.00, "Option 4 targets"),
    ("multi_clothing_and_food_harlem",                   "A", 4.55, 4.64, "non-regressing watch-list — verify no regression"),
    ("multi_cross_borough_food_brooklyn_shelter_manhattan","W", 2.82, 4.73, "migration headline win — must stay ≥4.5"),
]

# Subset: Option 4 only (4 watch-list scenarios)
_SUBSET_OPTION_4 = {
    "multi_food_and_shelter_brooklyn",
    "multi_shower_and_food_drop_in",
    "multi_clothing_and_food_harlem",
    "multi_cross_neighborhood_shower_les_food_chinatown",
    "multi_cross_borough_food_brooklyn_shelter_manhattan",  # plus the big-win
}


# ---------------------------------------------------------------------------
# Environment guards
# ---------------------------------------------------------------------------

def _require_env():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        print("Usage: USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... "
              "python scripts/mini_eval_r36_regressions.py",
              file=sys.stderr)
        sys.exit(2)

    flag = os.getenv("USE_UNIFIED_EXTRACTOR")
    if flag not in ("1", "true", "True", "yes"):
        print("ERROR: USE_UNIFIED_EXTRACTOR is not set to '1'.", file=sys.stderr)
        print("This mini-eval measures the unified extractor path. Set "
              "USE_UNIFIED_EXTRACTOR=1 to enable it.",
              file=sys.stderr)
        sys.exit(2)

    return api_key


# ---------------------------------------------------------------------------
# Scenario filter
# ---------------------------------------------------------------------------

def _select_scenarios(all_scenarios, subset: Optional[str]):
    """Return the list of scenario dicts matching our target IDs.

    all_scenarios — the SCENARIOS list from tests/eval/eval_llm_judge.py
    subset — optional keyword to narrow further ('option-4', 'failing')
    """
    wanted_ids = {t[0] for t in _TARGETS}
    if subset == "option-4":
        wanted_ids &= _SUBSET_OPTION_4
    elif subset == "failing":
        # Only scenarios that were < 4.0 in R36 unified
        wanted_ids = {t[0] for t in _TARGETS if t[3] < 4.0}
    elif subset is not None:
        print(f"ERROR: unknown --subset value: {subset!r}", file=sys.stderr)
        print("Valid values: option-4, failing", file=sys.stderr)
        sys.exit(2)

    matched = [s for s in all_scenarios if s["id"] in wanted_ids]
    got_ids = {s["id"] for s in matched}
    missing = wanted_ids - got_ids
    if missing:
        print(f"ERROR: scenario IDs not found in SCENARIOS list: "
              f"{sorted(missing)}", file=sys.stderr)
        print("The scenario definitions may have changed. Update _TARGETS.",
              file=sys.stderr)
        sys.exit(2)

    # Preserve the order declared in _TARGETS
    order = {t[0]: i for i, t in enumerate(_TARGETS)}
    matched.sort(key=lambda s: order[s["id"]])
    return matched


def _target_meta(scenario_id: str) -> tuple[str, float, float, str]:
    """Return (category, r36_legacy, r36_unified, note) for a scenario ID."""
    for t in _TARGETS:
        if t[0] == scenario_id:
            return t[1], t[2], t[3], t[4]
    return ("?", 0.0, 0.0, "")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_CAT_NAMES = {
    "A": "watch-list (Option 4)",
    "B": "narrative (Option 2b)",
    "C": "port needed",
    "D": "borderline (near threshold)",
    "H": "held from legacy",
    "L": "long-standing failure",
    "W": "migration headline win",
}


def _delta_arrow(delta: float) -> str:
    if delta >= 0.30:
        return "▲▲"
    if delta >= 0.05:
        return "▲"
    if delta <= -0.30:
        return "▼▼"
    if delta <= -0.05:
        return "▼"
    return "·"


def _print_summary(results, elapsed_total: float):
    """Print a focused per-scenario comparison table + aggregate stats."""
    print()
    print("=" * 100)
    print("MINI-EVAL R36 REGRESSIONS — summary")
    print("=" * 100)
    print()

    # Group by category for readability
    by_cat: dict[str, list] = {}
    for r in results:
        sid = r["scenario_id"]
        cat, _, _, _ = _target_meta(sid)
        by_cat.setdefault(cat, []).append(r)

    # Print each category's scenarios
    header = f"{'scenario':56s}  {'R36 L':>6}  {'R36 U':>6}  {'this':>6}  {'vs U':>7}  {'pass?':>6}"
    separator = "-" * len(header)

    for cat in ["A", "B", "C", "D", "H", "L", "W"]:
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        print(f"[{cat}] {_CAT_NAMES[cat]}")
        print(separator)
        print(header)
        print(separator)
        for r in rows:
            sid = r["scenario_id"]
            _, leg, uni, _ = _target_meta(sid)
            this = r["avg"]
            delta = this - uni
            arrow = _delta_arrow(delta)
            pass_mark = "✓" if this >= 4.0 else "✗"
            print(f"{sid:56s}  {leg:>6.2f}  {uni:>6.2f}  {this:>6.2f}  "
                  f"{delta:>+6.2f}{arrow:>2}  {pass_mark:>6}")
        print()

    # Aggregate stats
    total = len(results)
    passing = sum(1 for r in results if r["avg"] >= 4.0)
    failing = total - passing
    critical = sum(r["critical_count"] for r in results)

    recovered = sum(1 for r in results
                    if _target_meta(r["scenario_id"])[2] < 4.0 and r["avg"] >= 4.0)
    new_fails = sum(1 for r in results
                    if _target_meta(r["scenario_id"])[2] >= 4.0 and r["avg"] < 4.0)

    print(separator)
    print(f"Total:     {total}   Passing: {passing}   Failing: {failing}   "
          f"Critical failures: {critical}")
    print(f"Recovered (were failing, now passing): {recovered}")
    print(f"New regressions (were passing, now failing): {new_fails}")
    print(f"Wall time: {elapsed_total:.1f}s")
    print(separator)

    if failing == 0:
        print("\n✓ All target scenarios pass. Ready for full unified eval.")
    elif new_fails > 0:
        print(f"\n⚠ {new_fails} new regressions vs R36 Unified. Investigate before full eval.")
    else:
        print(f"\n◦ {failing} scenarios still failing (no new regressions). "
              f"Decide whether to proceed to full eval or address remaining gaps first.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Mini-eval targeting R36 unified-path regressions + watch-list. "
            "Runs ~15 scenarios instead of 171. See --subset to narrow further."
        ),
    )
    parser.add_argument(
        "--subset", type=str, default=None, choices=["option-4", "failing"],
        help=(
            "option-4: only the 4 Option 4 watch-list + big-win (~5 scenarios). "
            "failing: only scenarios that were <4.0 in R36 unified (~12 scenarios). "
            "Default: full target set (~15 scenarios)."
        ),
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save JSON report to this file (same shape as full eval).",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show the judge's justification for each scenario.",
    )
    args = parser.parse_args()

    _require_env()

    # Import the eval runner and its scenarios list lazily so --help is fast.
    import anthropic
    from tests.eval.eval_llm_judge import (
        SCENARIOS,
        simulate_conversation,
        judge_conversation,
        generate_report,
    )

    scenarios = _select_scenarios(SCENARIOS, args.subset)

    print("=" * 100)
    print("OPTION 4 + 2b MINI-EVAL — R36 regressions + watch-list")
    print("=" * 100)
    print(f"USE_UNIFIED_EXTRACTOR = {os.getenv('USE_UNIFIED_EXTRACTOR')}")
    print(f"Running {len(scenarios)} scenario(s)")
    if args.subset:
        print(f"Subset: {args.subset}")
    print()

    # Pre-warm semantic router (same as main eval)
    try:
        from app.services.semantic_router import initialize as _sr_init
        print("  Pre-warming semantic router...", end="", flush=True)
        ok = _sr_init()
        print(" ✓ ready" if ok else " ⚠ not available")
    except Exception as e:
        print(f" ⚠ failed: {e}")
    print()

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    results = []
    run_start = time.time()

    for i, scenario in enumerate(scenarios):
        sid = scenario["id"]
        cat, leg, uni, note = _target_meta(sid)
        label = f"[{i+1}/{len(scenarios)}] [{cat}] {sid} (R36 uni={uni:.2f})"
        print(f"  ▶ {label} ...", end="", flush=True)

        start = time.time()
        conversation = simulate_conversation(scenario, client)
        judgment = judge_conversation(client, conversation)
        elapsed = time.time() - start

        if "error" in judgment:
            print(f" ❌ error ({elapsed:.1f}s)")
            results.append({
                "scenario_id": sid,
                "avg": 0.0,
                "critical_count": 0,
                "error": judgment["error"],
                "conversation": conversation,
                "judgment": judgment,
            })
            continue

        scores = judgment.get("scores", {})
        avg = sum(s["score"] for s in scores.values()) / len(scores) if scores else 0
        cf_count = len(judgment.get("critical_failures", []) or [])
        delta = avg - uni
        arrow = _delta_arrow(delta)
        emoji = "✅" if avg >= 4.0 else "⚠️" if avg >= 3.0 else "❌"
        print(f" {emoji} {avg:.2f}/5.0  Δ={delta:+.2f}{arrow}  ({elapsed:.1f}s)")

        if args.verbose:
            notes = judgment.get("overall_notes", "")
            if notes:
                print(f"      └─ {notes[:200]}{'…' if len(notes) > 200 else ''}")

        results.append({
            "scenario_id": sid,
            "avg": avg,
            "critical_count": cf_count,
            "conversation": conversation,
            "judgment": judgment,
        })

    elapsed_total = time.time() - run_start

    _print_summary(results, elapsed_total)

    if args.output:
        # Build a full report using the same generator as the main eval.
        # This means the JSON shape matches eval_report_*.json and can be
        # fed to compare_eval_reports.py.
        report_input = [
            {"conversation": r["conversation"], "judgment": r["judgment"]}
            for r in results
        ]
        full_report = generate_report(report_input)
        with open(args.output, "w") as f:
            json.dump(full_report, f, indent=2)
        print(f"\nJSON report saved to {args.output}")

    # Exit code: 0 if all passed, 1 otherwise
    failing = sum(1 for r in results if r["avg"] < 4.0)
    sys.exit(0 if failing == 0 else 1)


if __name__ == "__main__":
    main()
