#!/usr/bin/env python3
"""Mini-eval for Option 4 hardening of the short-path slot prompt.

Purpose:
    Fast validation that the Option 4 prompt changes actually fix the
    4 watch-list blind-spot scenarios WITHOUT breaking safety-signal
    behavior in the other direction. Runs 6 test cases against a live
    Haiku call: 4 watch-list scenarios + 2 safety-signal sanity checks.

Context:
    Phase 2 of the llm_slot_extractor migration uses set-equality
    (Trust Model 3) to decide primary service_type when regex and LLM
    agree on the full set but disagree on which is primary. The 4
    watch-list scenarios are cases where regex priority-orders the
    set wrong (e.g., 'food and shelter in Brooklyn' → regex picks
    shelter because shelter has higher priority, but the scenario
    author expects food because it's first-mentioned).

    Option 4 tightens the short system prompt to explicitly teach
    first-mentioned-by-default with a safety-signal override. If the
    Phase 2 parallel-run eval shows any watch-list scenario regressed
    below the acceptance line (was >=4.5, dropped below 4.2), this
    mini-eval is the fast check that Option 4 recovers them.

Usage:
    # Requires ANTHROPIC_API_KEY and USE_UNIFIED_EXTRACTOR=1
    USE_UNIFIED_EXTRACTOR=1 python scripts/mini_eval_option_4.py

    # Run with verbose output (shows every LLM response)
    USE_UNIFIED_EXTRACTOR=1 python scripts/mini_eval_option_4.py --verbose

Cost:
    6 scenarios × 1 Haiku call each = ~$0.01. Runs in under 30 seconds.
    Cheap enough to re-run freely while iterating on prompt wording.

Exit codes:
    0 — all 6 test cases produced the expected primary service_type
    1 — at least one mismatch (Option 4 has not recovered that case)
    2 — environment error (no API key, module import fails, etc.)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Optional


# --- Test cases ------------------------------------------------------------
# Each case is a single-turn message + expected primary service_type.
# These mirror the 4 watch-list scenarios from eval_llm_judge.py exactly,
# plus 2 sanity checks on the safety-signal override direction to catch
# any regression from over-fitting to first-mentioned.

@dataclass
class MiniEvalCase:
    id: str
    message: str
    expected_primary: str
    expected_additional: set        # what types should appear in additional_services
    rationale: str


# The 4 watch-list scenarios. These all expect FIRST-MENTIONED to win
# because no safety signal is present in any of them.
WATCH_LIST_CASES = [
    MiniEvalCase(
        id="multi_food_and_shelter_brooklyn",
        message="I need food and a place to sleep in Brooklyn",
        expected_primary="food",
        expected_additional={"shelter"},
        rationale=(
            "No safety signal (no 'tonight' / 'right now' / 'nowhere'). "
            "First-mentioned is food. Regex priority would over-promote shelter."
        ),
    ),
    MiniEvalCase(
        id="multi_shower_and_food_drop_in",
        message="Where can I get a shower and something to eat in Manhattan?",
        expected_primary="personal_care",
        expected_additional={"food"},
        rationale=(
            "No safety signal. First-mentioned is shower (personal_care). "
            "Regex priority would over-promote food over personal_care."
        ),
    ),
    MiniEvalCase(
        id="multi_clothing_and_food_harlem",
        message="I need some clean clothes and a meal in Harlem",
        expected_primary="clothing",
        expected_additional={"food"},
        rationale=(
            "No safety signal. First-mentioned is clothing. Regex priority "
            "would over-promote food."
        ),
    ),
    MiniEvalCase(
        id="multi_cross_neighborhood_shower_les_food_chinatown",
        message="I want to shower in the Lower East Side and grab food in Chinatown",
        expected_primary="personal_care",
        expected_additional={"food"},
        rationale=(
            "No safety signal. First-mentioned is shower. Cross-neighborhood "
            "variant of the shower+food pattern."
        ),
    ),
]


# 2 sanity checks: scenarios that SHOULD trigger the safety-signal override
# (shelter/medical wins even when mentioned after another service). These
# guard against over-fitting to first-mentioned and losing the safety
# override.
SAFETY_OVERRIDE_CASES = [
    MiniEvalCase(
        id="sanity_food_shelter_tonight",
        message="I need food and somewhere to sleep tonight",
        expected_primary="shelter",
        expected_additional={"food"},
        rationale=(
            "SAFETY SIGNAL 'tonight' → shelter wins despite food being "
            "first-mentioned. This is the opposite direction from the "
            "watch-list: make sure Option 4 didn't kill the safety "
            "override."
        ),
    ),
    MiniEvalCase(
        id="sanity_job_shelter_nowhere",
        message="I need a job but I have nowhere to go right now",
        expected_primary="shelter",
        expected_additional={"employment"},
        rationale=(
            "SAFETY SIGNAL 'nowhere to go' + 'right now' → shelter wins "
            "over first-mentioned employment. Guards the same override "
            "direction with different wording."
        ),
    ),
]


ALL_CASES = WATCH_LIST_CASES + SAFETY_OVERRIDE_CASES


# --- Runner ----------------------------------------------------------------

def _bail(code: int, msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _run_one(case: MiniEvalCase, verbose: bool) -> tuple[bool, dict]:
    """Run one case through the short-path LLM extractor.

    Returns (passed, details) where details includes the actual
    extracted primary and additional services so the caller can
    print them in the report.
    """
    # Import lazily so the --help path works without the full backend.
    from app.services.slot_extractor import extract_slots
    from app.services.slot_extraction.dispatch import extract_slots_short

    # The short path needs conversation_history=[] and extracts from the
    # raw message only. This mirrors the real pipeline's call shape.
    regex_result = extract_slots(case.message)

    # We want to exercise the SHORT LLM prompt directly. The full
    # extract() would route through _is_simple_message first and
    # short-circuit (these messages are long enough to not be "simple"
    # but short enough to not be narrative). Call the short extractor
    # directly to ensure the LLM is actually invoked with the new
    # prompt.
    llm_result = extract_slots_short(case.message, conversation_history=[])

    if llm_result is None or not llm_result.get("service_type"):
        return False, {
            "actual_primary": None,
            "actual_additional": set(),
            "error": "LLM returned empty or None",
            "regex_primary": regex_result.get("service_type"),
        }

    actual_primary = llm_result["service_type"]
    actual_additional = {
        item[0] if isinstance(item, tuple) else item.get("type")
        for item in (llm_result.get("additional_services") or [])
    }
    actual_additional.discard(None)

    primary_ok = actual_primary == case.expected_primary
    # Additional services: we want the EXPECTED set to be a subset of
    # what the LLM returned. Extra additionals (LLM noticing something
    # we didn't list) are not failures.
    additional_ok = case.expected_additional.issubset(actual_additional)

    return primary_ok and additional_ok, {
        "actual_primary": actual_primary,
        "actual_additional": actual_additional,
        "regex_primary": regex_result.get("service_type"),
        "primary_ok": primary_ok,
        "additional_ok": additional_ok,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mini-eval for Option 4 short-prompt hardening.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print every LLM response, not just failures.",
    )
    parser.add_argument(
        "--only-watch-list", action="store_true",
        help="Skip the 2 safety-override sanity checks. Faster iteration.",
    )
    args = parser.parse_args(argv)

    # Environment guards — fail fast with clear diagnostics.
    if not os.getenv("ANTHROPIC_API_KEY"):
        _bail(2, "ANTHROPIC_API_KEY is not set.")
    if os.getenv("USE_UNIFIED_EXTRACTOR", "").lower() not in ("1", "true", "yes", "on"):
        _bail(
            2,
            "USE_UNIFIED_EXTRACTOR is not set. Option 4 only affects the "
            "new extractor's short prompt; without the flag the legacy "
            "path runs and this mini-eval has no signal.\n\n"
            "Run as: USE_UNIFIED_EXTRACTOR=1 python scripts/mini_eval_option_4.py",
        )

    # Ensure we can import the backend. The eval harness does this with
    # a sys.path hack; do the same for consistency.
    here = os.path.dirname(os.path.abspath(__file__))
    backend_path = os.path.join(here, "..", "backend")
    sys.path.insert(0, backend_path)

    try:
        from app.services.slot_extractor import extract_slots  # noqa: F401
    except ImportError as e:
        _bail(2, f"Cannot import backend: {e}")

    cases = WATCH_LIST_CASES if args.only_watch_list else ALL_CASES

    print("=" * 78)
    print("OPTION 4 MINI-EVAL — short-prompt multi-intent hardening")
    print("=" * 78)
    print(f"Running {len(cases)} cases "
          f"({len(WATCH_LIST_CASES)} watch-list + "
          f"{0 if args.only_watch_list else len(SAFETY_OVERRIDE_CASES)} sanity)")
    print()

    results: list = []
    for case in cases:
        passed, details = _run_one(case, verbose=args.verbose)
        results.append((case, passed, details))
        marker = "✓" if passed else "✗"
        print(f"  {marker} {case.id}")
        print(f"      message: {case.message!r}")
        print(
            f"      expected primary={case.expected_primary!r}  "
            f"additional⊇{case.expected_additional}"
        )
        print(
            f"      actual   primary={details.get('actual_primary')!r}  "
            f"additional={details.get('actual_additional')}"
        )
        if "error" in details:
            print(f"      ERROR: {details['error']}")
        if not passed:
            print(f"      rationale: {case.rationale}")
        if args.verbose:
            print(f"      regex primary (would-be fallback): "
                  f"{details.get('regex_primary')!r}")
        print()

    # Summary
    passed_count = sum(1 for _, p, _ in results if p)
    total = len(results)
    print("-" * 78)
    print(f"Result: {passed_count}/{total} passed")

    # Split-out the two sub-categories for clarity
    watch_results = [r for r in results if r[0] in WATCH_LIST_CASES]
    sanity_results = [r for r in results if r[0] in SAFETY_OVERRIDE_CASES]
    watch_pass = sum(1 for _, p, _ in watch_results if p)
    sanity_pass = sum(1 for _, p, _ in sanity_results if p)
    print(f"  Watch-list (first-mentioned recovery): "
          f"{watch_pass}/{len(watch_results)}")
    if sanity_results:
        print(f"  Sanity (safety-signal override):        "
              f"{sanity_pass}/{len(sanity_results)}")

    print("-" * 78)
    if passed_count == total:
        print("✓ All cases pass. Option 4 hardening recovers the blind-spot "
              "scenarios and preserves the safety-signal override.")
        return 0
    else:
        print("✗ Failures present. Review prompt wording and re-run.")
        print("  (Haiku is non-deterministic on edge phrasings; try "
              "re-running 2-3 times to rule out flakes before iterating.)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
