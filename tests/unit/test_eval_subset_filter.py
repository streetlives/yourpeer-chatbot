"""Unit tests for the --subset and --scenario-id flags in tests/eval/eval_llm_judge.py.

The subset filter selects scenarios from the SCENARIOS list whose
``average_score`` in a prior eval report falls below a threshold. The
scenario-id parser turns the (possibly repeated, possibly
comma-separated) ``--scenario-id`` argparse output into a deduped list
of IDs. This file exercises both pure-logic surfaces without touching
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
_parse_scenario_id_arg = _eval._parse_scenario_id_arg


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


# ---------------------------------------------------------------------------
# scenarios.jsonl input path (per-scenario streaming format from runs/<ts>/)
# ---------------------------------------------------------------------------


def _write_jsonl(tmp_path, records, name="scenarios.jsonl"):
    """Write one-record-per-line JSONL in the shape the streaming writer
    emits during a live eval run."""
    path = tmp_path / name
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return str(path)


def _judgment_with_score(avg_score):
    """Build a judgment dict whose 11 dimension scores average to ~avg_score.

    The scoring function takes the arithmetic mean of all 11 dimension
    scores. To produce a target average we set every dimension to the
    target — fine for tests since the mean is what's checked.
    """
    dims = (
        "slot_extraction", "dialog_efficiency", "response_tone",
        "safety_crisis", "confirmation_ux", "privacy",
        "hallucination_resistance", "error_recovery",
        "dignity_anti_stigma", "cultural_responsiveness",
        "equity_of_access",
    )
    return {
        "scores": {
            d: {"score": avg_score, "justification": f"placeholder for {d}"}
            for d in dims
        },
        "critical_failures": [],
        "overall_notes": "test fixture",
    }


class TestSubsetFromJsonl:
    """The new eval_results/runs/<timestamp>/scenarios.jsonl format must
    work as a --subset-from path. It uses different field names
    (`scenario_id` not `id`) and lacks pre-computed average_score."""

    def test_jsonl_failing_subset_works(self, tmp_path, fake_scenarios):
        path = _write_jsonl(tmp_path, [
            {"scenario_id": "happy_a", "judgment": _judgment_with_score(5)},
            {"scenario_id": "happy_b", "judgment": _judgment_with_score(4.5)},
            {"scenario_id": "fail_a",  "judgment": _judgment_with_score(3.0)},
            {"scenario_id": "fail_b",  "judgment": _judgment_with_score(3.5)},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", path, None)
        assert {s["id"] for s in matched} == {"fail_a", "fail_b"}

    def test_jsonl_borderline_subset_includes_failing(self, tmp_path, fake_scenarios):
        path = _write_jsonl(tmp_path, [
            {"scenario_id": "happy_a", "judgment": _judgment_with_score(5)},
            {"scenario_id": "happy_b", "judgment": _judgment_with_score(4.2)},
            {"scenario_id": "fail_a",  "judgment": _judgment_with_score(3.5)},
        ])
        matched = _apply_subset_filter(fake_scenarios, "borderline", path, None)
        # borderline default = 4.5; both happy_b and fail_a fall below.
        assert {s["id"] for s in matched} == {"happy_b", "fail_a"}

    def test_jsonl_skips_errored_scenarios(self, tmp_path, fake_scenarios):
        """Errored scenarios (no judgment block) must be skipped, not crash."""
        path = _write_jsonl(tmp_path, [
            {"scenario_id": "happy_a", "judgment": _judgment_with_score(5)},
            {"scenario_id": "fail_a",  "elapsed_seconds": 0.1},  # errored, no judgment
            {"scenario_id": "fail_b",  "judgment": _judgment_with_score(3.0)},
        ])
        matched = _apply_subset_filter(fake_scenarios, "failing", path, None)
        assert {s["id"] for s in matched} == {"fail_b"}

    def test_jsonl_tolerates_blank_lines(self, tmp_path, fake_scenarios):
        """Trailing newlines or blank lines mid-file must not crash."""
        path = tmp_path / "scenarios.jsonl"
        path.write_text(
            json.dumps({"scenario_id": "fail_a",
                        "judgment": _judgment_with_score(3.0)}) +
            "\n\n" +  # blank line in middle
            json.dumps({"scenario_id": "fail_b",
                        "judgment": _judgment_with_score(3.5)}) +
            "\n"
        )
        matched = _apply_subset_filter(fake_scenarios, "failing", str(path), None)
        assert {s["id"] for s in matched} == {"fail_a", "fail_b"}

    def test_jsonl_warns_on_malformed_line_but_continues(
        self, tmp_path, fake_scenarios, capsys
    ):
        """A garbled JSONL line should warn but not abort the filter."""
        path = tmp_path / "scenarios.jsonl"
        path.write_text(
            json.dumps({"scenario_id": "fail_a",
                        "judgment": _judgment_with_score(3.0)}) +
            "\n{ this is not json\n" +
            json.dumps({"scenario_id": "fail_b",
                        "judgment": _judgment_with_score(3.5)}) +
            "\n"
        )
        matched = _apply_subset_filter(fake_scenarios, "failing", str(path), None)
        assert {s["id"] for s in matched} == {"fail_a", "fail_b"}
        captured = capsys.readouterr()
        assert "malformed JSONL line" in captured.err


# ---------------------------------------------------------------------------
# Directory input path (eval_results/runs/<timestamp>/)
# ---------------------------------------------------------------------------


class TestSubsetFromDirectory:
    """Pointing --subset-from at a runs/<timestamp>/ directory should
    auto-resolve to report.json (preferred) or scenarios.jsonl (fallback
    if the run was killed mid-eval)."""

    def test_directory_with_report_json_uses_it(self, tmp_path, fake_scenarios):
        # Build a runs/<ts>/ directory with both files. Should prefer
        # report.json since it's pre-computed.
        run_dir = tmp_path / "20260505T120000"
        run_dir.mkdir()
        (run_dir / "report.json").write_text(json.dumps({
            "scenarios": [
                {"id": "fail_a", "average_score": 3.0},
                {"id": "fail_b", "average_score": 3.5},
                {"id": "happy_a", "average_score": 5.0},
            ],
        }))
        # Also have a stale scenarios.jsonl with different IDs to prove
        # report.json takes precedence.
        (run_dir / "scenarios.jsonl").write_text(json.dumps({
            "scenario_id": "different_id",
            "judgment": _judgment_with_score(3.0),
        }) + "\n")

        matched = _apply_subset_filter(
            fake_scenarios, "failing", str(run_dir), None,
        )
        assert {s["id"] for s in matched} == {"fail_a", "fail_b"}

    def test_directory_falls_back_to_jsonl_when_no_report(
        self, tmp_path, fake_scenarios,
    ):
        """A killed-mid-run directory has only scenarios.jsonl. The
        filter should still work — that's the whole point of streaming
        per-scenario."""
        run_dir = tmp_path / "20260505T120000"
        run_dir.mkdir()
        (run_dir / "scenarios.jsonl").write_text(
            json.dumps({"scenario_id": "fail_a",
                        "judgment": _judgment_with_score(3.0)}) +
            "\n" +
            json.dumps({"scenario_id": "happy_a",
                        "judgment": _judgment_with_score(5.0)}) +
            "\n"
        )
        matched = _apply_subset_filter(
            fake_scenarios, "failing", str(run_dir), None,
        )
        assert {s["id"] for s in matched} == {"fail_a"}

    def test_empty_directory_exits_2(self, tmp_path, fake_scenarios):
        """Pointing at a directory that has neither file is a usage
        error and should exit 2 with a clear message."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()
        with pytest.raises(SystemExit) as exc:
            _apply_subset_filter(
                fake_scenarios, "failing", str(run_dir), None,
            )
        assert exc.value.code == 2

    def test_directory_works_with_threshold_override(
        self, tmp_path, fake_scenarios,
    ):
        """Directory resolution composes correctly with --subset-threshold."""
        run_dir = tmp_path / "20260505T120000"
        run_dir.mkdir()
        (run_dir / "report.json").write_text(json.dumps({
            "scenarios": [
                {"id": "fail_a",  "average_score": 3.0},
                {"id": "happy_a", "average_score": 4.2},
                {"id": "happy_b", "average_score": 4.7},
            ],
        }))
        # Override default to 4.5 — happy_a should now be included.
        matched = _apply_subset_filter(
            fake_scenarios, "failing", str(run_dir), 4.5,
        )
        assert {s["id"] for s in matched} == {"fail_a", "happy_a"}


# ---------------------------------------------------------------------------
# _parse_scenario_id_arg — multi-ID flag parser
# ---------------------------------------------------------------------------

class TestParseScenarioIdArg:
    """Pure-function tests for the multi-ID parser.

    The argparse layer hands us either ``None`` (flag not passed) or a
    list of strings (one per ``--scenario-id`` invocation). Each string
    may itself contain comma-separated IDs. The parser flattens the
    structure, strips whitespace, drops empty tokens, dedupes while
    preserving first-seen order, and returns a plain list[str].
    """

    def test_none_returns_empty(self):
        """Flag not passed at all — argparse hands us None."""
        assert _parse_scenario_id_arg(None) == []

    def test_empty_list_returns_empty(self):
        """Defensive: empty list is treated like None."""
        assert _parse_scenario_id_arg([]) == []

    def test_single_id(self):
        """The original v1 invocation must keep working."""
        assert _parse_scenario_id_arg(["foo"]) == ["foo"]

    def test_comma_separated_in_one_invocation(self):
        """Most ergonomic multi-ID form: one flag, comma-separated."""
        assert _parse_scenario_id_arg(["foo,bar,baz"]) == ["foo", "bar", "baz"]

    def test_repeated_flag_invocations(self):
        """Argparse with action='append' delivers a list element per flag."""
        assert _parse_scenario_id_arg(["foo", "bar", "baz"]) == ["foo", "bar", "baz"]

    def test_mixed_repeated_and_comma(self):
        """Both forms in the same command line — most flexible."""
        assert _parse_scenario_id_arg(["foo,bar", "baz"]) == ["foo", "bar", "baz"]
        assert _parse_scenario_id_arg(["foo", "bar,baz"]) == ["foo", "bar", "baz"]

    def test_whitespace_tolerated(self):
        """Quoted flag values may contain whitespace around commas."""
        assert _parse_scenario_id_arg(["foo, bar, baz"]) == ["foo", "bar", "baz"]
        assert _parse_scenario_id_arg([" foo ", " bar "]) == ["foo", "bar"]

    def test_duplicates_deduped_preserving_order(self):
        """Order on first occurrence wins — ``foo,bar`` and ``bar,foo``
        each return their own ordering, but a later duplicate doesn't
        re-position the earlier one."""
        assert _parse_scenario_id_arg(["foo,bar,foo"]) == ["foo", "bar"]
        assert _parse_scenario_id_arg(["foo", "bar", "foo"]) == ["foo", "bar"]
        assert _parse_scenario_id_arg(["foo,bar", "bar,baz"]) == ["foo", "bar", "baz"]

    def test_order_differs_by_input_order(self):
        """Sanity: the parser does NOT canonicalize — input order is the output."""
        assert _parse_scenario_id_arg(["bar,foo"]) == ["bar", "foo"]
        assert _parse_scenario_id_arg(["foo,bar"]) == ["foo", "bar"]

    def test_empty_tokens_dropped(self):
        """Trailing comma, double comma, all-whitespace token — none of these
        should produce a zero-length ID that downstream lookup would fail on.
        """
        assert _parse_scenario_id_arg(["foo,"]) == ["foo"]
        assert _parse_scenario_id_arg(["foo,,bar"]) == ["foo", "bar"]
        assert _parse_scenario_id_arg([",foo"]) == ["foo"]
        assert _parse_scenario_id_arg(["  ,  ,foo"]) == ["foo"]

    def test_only_empty_tokens_returns_empty(self):
        """Defensive: a command line of nothing-but-commas should not crash;
        it should produce an empty list and let the caller fall through to
        the no-filter branch."""
        assert _parse_scenario_id_arg([","]) == []
        assert _parse_scenario_id_arg([",,,"]) == []
        assert _parse_scenario_id_arg(["  "]) == []
        assert _parse_scenario_id_arg(["", ""]) == []

    def test_realistic_probe_batch(self):
        """The use case that motivated this change — a 7-scenario probe
        batch in a single command. Order preserved, no duplicates, no
        surprises."""
        ids = _parse_scenario_id_arg([
            "pre_llm_redact_phone_in_followup,"
            "shelter_queens_17,"
            "peer_escaped_abuse_child_next_steps,"
            "peer_young_mom_multiple_needs,"
            "natural_lgbtq_youth,"
            "wa_family_with_children,"
            "wa_negative_preference"
        ])
        assert ids == [
            "pre_llm_redact_phone_in_followup",
            "shelter_queens_17",
            "peer_escaped_abuse_child_next_steps",
            "peer_young_mom_multiple_needs",
            "natural_lgbtq_youth",
            "wa_family_with_children",
            "wa_negative_preference",
        ]
        assert len(ids) == 7
        assert len(set(ids)) == 7  # no dupes
