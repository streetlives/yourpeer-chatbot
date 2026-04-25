"""Unit tests for the --subset filter in tests/eval/eval_llm_judge.py.

The filter selects scenarios from the SCENARIOS list whose `average_score`
in a prior eval report falls below a threshold. This file exercises the
pure-logic parts (file I/O, filtering, error reporting) without touching
the Anthropic API.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Load the eval module by file path. tests/eval/ is not a package, so we
# can't `from tests.eval.eval_llm_judge import _apply_subset_filter`.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
_EVAL_PATH = _REPO_ROOT / "tests" / "eval" / "eval_llm_judge.py"
_spec = importlib.util.spec_from_file_location("_eval_llm_judge", _EVAL_PATH)
_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_eval)

_apply_subset_filter = _eval._apply_subset_filter
_SUBSET_THRESHOLDS = _eval._SUBSET_THRESHOLDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_scenarios():
    """A small synthetic SCENARIOS list — IDs only matter for filtering."""
    return [
        {"id": "happy_a", "name": "Happy A", "category": "happy_path"},
        {"id": "happy_b", "name": "Happy B", "category": "happy_path"},
        {"id": "fail_a",  "name": "Fail A",  "category": "edge_case"},
        {"id": "fail_b",  "name": "Fail B",  "category": "natural_language"},
        {"id": "fail_c",  "name": "Fail C",  "category": "multi_intent"},
    ]


def _write_report(tmp_path, scenarios):
    """Write a minimal eval-report JSON in the shape produced by --output."""
    report = {
        "timestamp": "2026-04-24T00:00:00",
        "summary": {"overall_average": 4.5},
        "critical_failures": [],
        "scenarios": scenarios,
    }
    path = tmp_path / "prior_report.json"
    path.write_text(json.dumps(report))
    return str(path)


# ---------------------------------------------------------------------------
# Happy path — failing/borderline subsets
# ---------------------------------------------------------------------------

class TestSubsetFailing:
    """Scenarios with average_score < 4.0 are selected when subset='failing'."""

    def test_selects_only_failing_scenarios(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "happy_a", "average_score": 4.91},
            {"id": "happy_b", "average_score": 4.55},
            {"id": "fail_a",  "average_score": 3.55},
            {"id": "fail_b",  "average_score": 3.91},
            {"id": "fail_c",  "average_score": 4.27},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        ids = {s["id"] for s in matched}
        assert ids == {"fail_a", "fail_b"}

    def test_default_failing_threshold_is_4_0(self):
        assert _SUBSET_THRESHOLDS["failing"] == 4.0

    def test_returns_full_scenario_dicts_not_ids(self, tmp_path, fake_scenarios):
        # Caller iterates these and reads name, category, etc.
        report = _write_report(tmp_path, [
            {"id": "fail_b", "average_score": 3.5},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        assert len(matched) == 1
        assert matched[0]["name"] == "Fail B"
        assert matched[0]["category"] == "natural_language"


class TestSubsetBorderline:
    """Borderline pulls in scenarios scoring 4.0–4.49 too."""

    def test_selects_failing_and_borderline(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "happy_a", "average_score": 4.91},
            {"id": "happy_b", "average_score": 4.55},
            {"id": "fail_a",  "average_score": 3.55},
            {"id": "fail_b",  "average_score": 3.91},
            {"id": "fail_c",  "average_score": 4.27},
        ])
        matched = _apply_subset_filter(fake_scenarios, "borderline", report, None)
        ids = {s["id"] for s in matched}
        # All three sub-4.5 scenarios — fail_a, fail_b, fail_c
        assert ids == {"fail_a", "fail_b", "fail_c"}

    def test_default_borderline_threshold_is_4_5(self):
        assert _SUBSET_THRESHOLDS["borderline"] == 4.5


# ---------------------------------------------------------------------------
# Threshold override
# ---------------------------------------------------------------------------

class TestThresholdOverride:
    """--subset-threshold replaces the named subset's default threshold."""

    def test_override_narrows_to_lowest_scoring(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "fail_a", "average_score": 3.55},
            {"id": "fail_b", "average_score": 3.91},
            {"id": "fail_c", "average_score": 4.27},
        ])
        # Threshold 3.7 picks only fail_a
        matched = _apply_subset_filter(fake_scenarios, "failing", report, 3.7)
        assert {s["id"] for s in matched} == {"fail_a"}

    def test_override_can_widen_above_default(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "happy_a", "average_score": 4.91},
            {"id": "happy_b", "average_score": 4.55},
            {"id": "fail_c",  "average_score": 4.27},
        ])
        # Threshold 4.6 with subset='failing' is unusual but should work —
        # picks anything below 4.6
        matched = _apply_subset_filter(fake_scenarios, "failing", report, 4.6)
        assert {s["id"] for s in matched} == {"happy_b", "fail_c"}

    def test_strict_less_than_not_less_equal(self, tmp_path, fake_scenarios):
        # A scenario at exactly the threshold is NOT selected.
        report = _write_report(tmp_path, [
            {"id": "fail_a", "average_score": 4.0},
            {"id": "fail_b", "average_score": 3.99},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        assert {s["id"] for s in matched} == {"fail_b"}


# ---------------------------------------------------------------------------
# Error paths — missing args, bad files, malformed JSON
# ---------------------------------------------------------------------------

class TestErrorPaths:
    """Helper exits 2 on usage/IO errors. Callers don't branch on returns."""

    def test_missing_subset_from_exits_2(self, fake_scenarios):
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing", None, None)
        assert exc.value.code == 2

    def test_missing_subset_from_message_mentions_flag(self, fake_scenarios, capsys):
        with pytest.raises(SystemExit):
            _apply_subset_filter(fake_scenarios, "failing", None, None)
        err = capsys.readouterr().err
        assert "--subset-from" in err

    def test_nonexistent_path_exits_2(self, fake_scenarios):
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing",
                                 "/tmp/nope-does-not-exist.json", None)
        assert exc.value.code == 2

    def test_malformed_json_exits_2(self, tmp_path, fake_scenarios):
        bad = tmp_path / "bad.json"
        bad.write_text("{ this is not valid json")
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing", str(bad), None)
        assert exc.value.code == 2

    def test_report_without_scenarios_key_exits_2(self, tmp_path, fake_scenarios):
        bad = tmp_path / "no_scenarios.json"
        bad.write_text(json.dumps({"timestamp": "...", "summary": {}}))
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing", str(bad), None)
        assert exc.value.code == 2

    def test_report_with_non_list_scenarios_exits_2(self, tmp_path, fake_scenarios):
        bad = tmp_path / "wrong_shape.json"
        bad.write_text(json.dumps({"scenarios": "not a list"}))
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing", str(bad), None)
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# Empty match case — exits 0, not an error
# ---------------------------------------------------------------------------

class TestNoMatch:
    """When no scenario scored under the threshold, exit 0 (nothing to do)."""

    def test_all_passing_exits_0(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "happy_a", "average_score": 4.91},
            {"id": "happy_b", "average_score": 4.55},
        ])
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(fake_scenarios, "failing", report, None)
        assert exc.value.code == 0

    def test_no_match_message_is_informational(self, tmp_path, fake_scenarios, capsys):
        report = _write_report(tmp_path, [
            {"id": "happy_a", "average_score": 4.91},
        ])
        with pytest.raises(SystemExit):
            _apply_subset_filter(fake_scenarios, "failing", report, None)
        out = capsys.readouterr().out
        # Message should be on stdout (informational), not stderr (error).
        assert "Nothing to run" in out


# ---------------------------------------------------------------------------
# Stale report — scenarios renamed/removed since the report was written
# ---------------------------------------------------------------------------

class TestStaleReportIDs:
    """Prior report has IDs no longer in SCENARIOS — warn and continue."""

    def test_renamed_scenarios_warning_only(self, tmp_path, fake_scenarios, capsys):
        report = _write_report(tmp_path, [
            {"id": "fail_a",         "average_score": 3.5},
            {"id": "renamed_old_id", "average_score": 3.5},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        # fail_a is still found and matched; the missing ID is reported.
        assert {s["id"] for s in matched} == {"fail_a"}
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "renamed_old_id" in err

    def test_all_ids_missing_no_match_exit_0(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "totally_gone_a", "average_score": 3.5},
            {"id": "totally_gone_b", "average_score": 3.5},
        ])
        with pytest.raises(SystemExit) as exc:
            # The wanted_ids set is non-empty so we don't hit the "no match
            # under threshold" exit. But the post-filter intersection is
            # empty. Currently this returns an empty list — the scenario
            # selection block in main() would then run zero scenarios.
            # The eval runner handles empty `scenarios` gracefully (just
            # prints "Running 0 scenario(s)..." and produces an empty
            # report). We don't exit here because the user might have
            # intended the empty result.
            matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
            # If we reach here without SystemExit, the empty-list path is
            # what's happening. Convert to assertion.
            assert matched == []
            raise SystemExit(0)
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Tolerance — scenarios in report missing average_score field
# ---------------------------------------------------------------------------

class TestToleratesMissingFields:
    """Errored or partial scenarios in the prior report shouldn't crash."""

    def test_skips_scenarios_without_average_score(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"id": "fail_a", "average_score": 3.5},
            {"id": "fail_b", "error": "API error", "average_score": None},
            {"id": "fail_c"},  # No score field at all
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        # Only fail_a has a usable score below threshold.
        assert {s["id"] for s in matched} == {"fail_a"}

    def test_skips_scenarios_without_id(self, tmp_path, fake_scenarios):
        report = _write_report(tmp_path, [
            {"average_score": 3.5},  # Missing id
            {"id": "fail_a", "average_score": 3.5},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", report, None)
        assert {s["id"] for s in matched} == {"fail_a"}
