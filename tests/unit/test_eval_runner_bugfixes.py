"""Tests for the eval-runner bug fixes (May 2026 audit).

Companion to test_eval_runner_durability.py. These tests cover the
14 bugs identified in the audit after the May 2026 lost-run incident.
The big ones:

- Bug 4: overall_average is mean of scenario averages, not pool of
  individual dimension scores.
- Bug 5/12: baselines are selectable (R28 + R38), with each baseline
  shown against ITS OWN denominator.
- Bug 16: --strict gates CF-based exit-1 behavior.
- Bug 17: early --redact-before-llm peek uses argparse, not
  substring match.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest


# Make the eval-runner module importable without running main().
_EVAL_DIR = os.path.join(os.path.dirname(__file__), "..", "eval")
_EVAL_DIR = os.path.abspath(_EVAL_DIR)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

import eval_llm_judge as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Bug 4: overall_average uses scenario means, not dimension-pool means
# ---------------------------------------------------------------------------

def _make_results_for(scenario_dim_scores):
    """Helper: build a `results` list shaped like the runner produces.

    `scenario_dim_scores` is [(scenario_id, {dim: score})] — for each
    scenario, the dimension scores the judge returned. Missing dimensions
    are simply omitted from the dict.
    """
    results = []
    for sid, dims in scenario_dim_scores:
        scenario = {
            "id": sid,
            "name": sid,
            "category": "happy_path",
            "description": "test",
            "user_turns": ["hi"],
            "expected": {},
        }
        results.append({
            "conversation": {
                "scenario": scenario,
                "transcript": [],
                "turn_count": 1,
                "llm_simulator_turns": [],
            },
            "judgment": {
                "scores": {d: {"score": s, "justification": ""} for d, s in dims.items()},
                "overall_notes": "",
                "critical_failures": [],
            },
        })
    return results


def test_overall_average_uses_scenario_means_not_dimension_pool():
    """Bug 4: when the judge omits dimensions from some scenarios,
    the overall_average should NOT be biased toward complete scenarios.

    Scenario A scored 5 on all 11 dimensions (mean = 5.0).
    Scenario B scored 3 on a single dimension (others omitted, mean = 3.0).
    Old behavior: pool of 12 scores = (11×5 + 1×3) / 12 = 4.83 — biased high.
    New behavior: mean of scenario means = (5.0 + 3.0) / 2 = 4.0.
    """
    dims = ["slot_extraction", "dialog_efficiency", "response_tone",
            "safety_crisis", "confirmation_ux", "privacy",
            "hallucination_resistance", "error_recovery",
            "dignity_anti_stigma", "cultural_responsiveness", "equity_of_access"]

    results = _make_results_for([
        ("scenario_a_complete", {d: 5 for d in dims}),
        ("scenario_b_partial", {"slot_extraction": 3}),  # judge omitted others
    ])

    report = runner.generate_report(results)

    overall = report["summary"]["overall_average"]
    # Mean of scenario means: (5.0 + 3.0) / 2 = 4.0
    assert overall == 4.0, (
        f"Expected overall=4.0 (mean of scenario means). Got {overall}. "
        f"If this is ~4.83, the old dimension-pool calculation regressed."
    )


def test_overall_average_skips_zero_scenarios():
    """Bug 3 corollary: scenarios where the judge returned no
    recognized dimension scores get average_score=0 and should NOT
    drag the overall down. They're errors, not real zeros."""
    dims = ["slot_extraction", "dialog_efficiency", "response_tone",
            "safety_crisis", "confirmation_ux", "privacy",
            "hallucination_resistance", "error_recovery",
            "dignity_anti_stigma", "cultural_responsiveness", "equity_of_access"]
    results = _make_results_for([
        ("real_scenario", {d: 5 for d in dims}),
        ("judge_glitched", {}),  # judge returned no recognized dimensions
    ])

    report = runner.generate_report(results)
    # Should be 5.0 (only the real scenario counts), not 2.5 (5+0)/2.
    assert report["summary"]["overall_average"] == 5.0


# ---------------------------------------------------------------------------
# Bug 5/12: Baselines are selectable; denominator alignment
# ---------------------------------------------------------------------------

def test_baselines_dict_has_r28_and_r38():
    """The two known baselines are exposed for --baseline."""
    assert "R28" in runner.BASELINES
    assert "R38" in runner.BASELINES


def test_r38_baseline_has_required_fields():
    """R38 is the default baseline; it needs every field print_report uses."""
    r38 = runner.BASELINES["R38"]
    required = {
        "overall_average", "weighted_average",
        "passing_count", "failing_count", "critical_failure_count",
        "perfect_count", "total_scenarios",
        "dimensions", "categories", "key_scenarios",
    }
    missing = required - r38.keys()
    assert not missing, f"R38 baseline missing fields: {missing}"


def test_r38_baseline_total_scenarios_matches_passing_plus_failing():
    """Sanity check that R38's denominator math is internally consistent."""
    r38 = runner.BASELINES["R38"]
    # 173 passing + 2 failing = 175 total. Numbers from EVAL_RESULTS.md.
    assert r38["total_scenarios"] == r38["passing_count"] + r38["failing_count"]


def test_baseline_id_threads_through_to_summary():
    """generate_report(baseline_id=...) should land in summary['baseline']."""
    results = _make_results_for([("s1", {"slot_extraction": 5})])

    report_r28 = runner.generate_report(results, baseline_id="R28")
    assert report_r28["summary"]["baseline"] == "R28"

    report_r38 = runner.generate_report(results, baseline_id="R38")
    assert report_r38["summary"]["baseline"] == "R38"


def test_print_report_falls_back_on_unknown_baseline(capsys):
    """Bug 12 robustness: an unknown baseline ID shouldn't crash the
    report; it should warn and fall back to R38."""
    results = _make_results_for([("s1", {"slot_extraction": 5})])
    report = runner.generate_report(results)
    report["summary"]["baseline"] = "RNonexistent"

    runner.print_report(report)  # must not raise

    out = capsys.readouterr().out
    # The header should reflect the fallback
    assert "Baseline: R38" in out


def test_print_report_shows_baseline_native_denominator(capsys):
    """Bug 12 fix: when comparing 'Passing' counts, R38's 173/175 must
    be shown against R38's own denominator, not the current run's. The
    deltas should be in percentage points, not raw count differences."""
    results = _make_results_for([("s1", {d: 5 for d in [
        "slot_extraction", "dialog_efficiency", "response_tone",
        "safety_crisis", "confirmation_ux", "privacy",
        "hallucination_resistance", "error_recovery",
        "dignity_anti_stigma", "cultural_responsiveness",
        "equity_of_access",
    ]})])
    report = runner.generate_report(results, baseline_id="R38")

    runner.print_report(report)
    out = capsys.readouterr().out

    # Critical: R38's row should show 173/175, not 173/<current_total>.
    assert "173/175" in out, (
        "R38 baseline must display its own denominator (175). "
        "If this fails, Bug 12 has regressed."
    )
    # And the delta should be in percentage points (pp), not a raw diff.
    assert "pp" in out


# ---------------------------------------------------------------------------
# Bug 9: services_count drives simulator stop-condition, not string match
# ---------------------------------------------------------------------------

def test_user_response_returns_none_when_services_count_is_positive():
    """Bug 9 fix: structured signal, not string match."""
    transcript = [
        {"role": "user", "text": "I need food"},
        {
            "role": "bot",
            "text": "Some random reply that doesn't contain 'found' or 'option'",
            "services_count": 3,  # the structured signal
            "quick_replies": [],
        },
    ]
    scenario = {"id": "test", "description": "", "expected": {}}
    # Pass None for client because we don't expect any LLM call when
    # services_count > 0 — the function should short-circuit.
    result = runner._generate_user_response(None, scenario, transcript)
    assert result is None
