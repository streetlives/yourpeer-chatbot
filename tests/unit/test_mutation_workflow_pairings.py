"""Mutation-workflow pairing guard.

The mutation-testing workflows (``.github/workflows/mutation-testing.yml`` and
``.github/workflows/mutation-testing-pr.yml``) pair each critical module with
a focused test subset. If a listed test file doesn't actually import or
patch the module it's paired with, cosmic-ray produces a misleading 0%
score — every mutation survives because no test exercises the mutated code.

Weekly run #1 (2026-04-21) surfaced exactly this failure mode on five pairings:

    session_token.py      ← test_session_store.py (0 imports)
    session_token.py      ← test_main.py (0 imports)
    crisis_detector.py    ← test_frustration_and_crisis.py (0 imports)
    pipeline.py           ← test_targeted_bug_regressions.py (0 imports)
    execution.py          ← test_multi_turn_and_context.py (0 imports)

``session_token.py`` scored 0/12 and ``crisis_detector.py`` scored 3/44 in
that run — nearly the entire result explained by wrong test pairings.

This guard asserts at PR-edit time that every workflow-listed pairing is
valid, turning silent 0% scores a month later into a loud pytest failure
today. Same pattern as ``TestBotKnowledgeFreshness`` — hand-maintained
mapping checked against ground truth.

**When this test fails, the fix is to update the workflow YAML (or fix
whichever test file should have imported the module) — don't loosen the
assertion.**
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WEEKLY_YML = REPO_ROOT / ".github/workflows/mutation-testing.yml"
PR_YML = REPO_ROOT / ".github/workflows/mutation-testing-pr.yml"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_weekly_pairings() -> list[tuple[str, list[str]]]:
    """Extract (module_path, [test_paths]) pairs from the weekly YAML matrix.

    The weekly workflow uses GitHub Actions matrix syntax:

        strategy:
          matrix:
            include:
              - module: backend/app/services/X.py
                tests: >-
                  tests/unit/test_X.py
                  tests/unit/test_Y.py

    We use ``yaml.safe_load`` rather than regex so that edits to surrounding
    YAML structure don't silently break parsing.
    """
    data = yaml.safe_load(WEEKLY_YML.read_text())
    includes = data["jobs"]["mutation-test"]["strategy"]["matrix"]["include"]
    pairings = []
    for entry in includes:
        module = entry["module"]
        # ``tests`` comes out as a single whitespace-separated string
        # because of the YAML folded-scalar ">-" prefix.
        tests = entry["tests"].split()
        pairings.append((module, tests))
    return pairings


def _parse_pr_pairings() -> list[tuple[str, list[str]]]:
    """Extract (module_path, [test_paths]) pairs from the PR workflow.

    Unlike the weekly workflow, the PR workflow builds its matrix
    dynamically in bash. We parse the bash associative-array assignments
    to recover the same (module, tests) pairing:

        TESTS_FOR_MODULE["backend/app/services/X.py"]="tests/... tests/..."

    Regex-based because the values aren't valid YAML — they live inside
    a ``run:`` block as bash commands.
    """
    src = PR_YML.read_text()
    pattern = re.compile(
        r'TESTS_FOR_MODULE\["([^"]+)"\]="([^"]+)"'
    )
    pairings = []
    for m in pattern.finditer(src):
        module, tests_str = m.group(1), m.group(2)
        pairings.append((module, tests_str.split()))
    return pairings


def _test_file_exercises_module(test_path: Path, module_path: str) -> bool:
    """Return True if ``test_path`` imports, patches, or exercises the module.

    Recognizes three forms of exercise:

    1. **Direct usage** — ``from app.services.X import ...``,
       ``patch("app.services.X....")``, attribute access on the module.
    2. **Harness-based exercise** — the test file uses one of the known
       conftest harnesses (``send``, ``send_multi``, ``assert_classified``)
       that routes through the full chatbot pipeline. In that case,
       mutations to any pipeline-resident module (orchestrator, pipeline,
       execution, classifier, slot_extractor, crisis_detector, and the
       handlers under ``chatbot/handlers/``) can be killed by assertions
       on end-to-end behavior. Confirmation.py is the canonical example:
       no direct imports in its paired tests, but scored 232/398 = 58.3%
       in the weekly run — clear evidence the harness exercises it.
    3. **Same-module name references** — comments, parametrize IDs,
       docstrings that mention the module. Weakest signal; only used to
       tolerate lightly-paired files.

    Non-pipeline utility modules (``session_token``, ``pii_redactor``)
    are NOT exercised by the harness, so they still require a direct
    import or patch — which is exactly what caught the session_token
    0/12 pairing bug: its paired files had no imports AND no harness
    use, producing no exercise of any kind.
    """
    if not test_path.exists():
        return False
    src = test_path.read_text()
    mod_basename = Path(module_path).stem
    dotted = module_path.replace("backend/", "").replace(".py", "").replace("/", ".")

    # Form 1: direct import / patch / attribute access.
    # Also handles the `from parent_package import submodule [as alias]` form:
    #   from app.services.chatbot import pipeline as pipeline_module
    # Parent is everything except the final dotted component; basename
    # is the module's file name.
    parent = dotted.rsplit(".", 1)[0] if "." in dotted else ""
    direct_patterns = [
        rf"from\s+{re.escape(dotted)}",
        rf"import\s+{re.escape(dotted)}",
        rf'patch\(\s*["\']?{re.escape(dotted)}',
        rf'\b{re.escape(mod_basename)}\.\w',
    ]
    if parent:
        # e.g. `from app.services.chatbot import pipeline` —
        # must include the basename as an imported name after the
        # `import` keyword. The `(?:[^\n]*,\s*)?` allows other imports
        # on the same line before the basename; the look-ahead ensures
        # we match at a word boundary (so `pipeline` doesn't also match
        # `pipeline_helpers`).
        direct_patterns.append(
            rf"from\s+{re.escape(parent)}\s+import\s+"
            rf"(?:[^\n]*,\s*)?{re.escape(mod_basename)}\b"
        )
    if any(re.search(p, src) for p in direct_patterns):
        return True

    # Form 2: harness-based exercise — only valid for modules that live
    # inside the chat pipeline (i.e., are reachable from send() / etc.).
    # session_token.py and pii_redactor.py are reachable via FastAPI
    # dependencies, but in tests the SESSION_SECRET env is typically
    # unset so the HMAC path never runs — harness use doesn't help
    # there. Restrict this to the pipeline-resident modules.
    PIPELINE_MODULES = {
        "backend/app/services/classifier.py",
        "backend/app/services/crisis_detector.py",
        "backend/app/services/slot_extractor.py",
        "backend/app/services/chatbot/orchestrator.py",
        "backend/app/services/chatbot/pipeline.py",
        "backend/app/services/chatbot/execution.py",
        "backend/app/services/chatbot/handlers/confirmation.py",
        "backend/app/services/chatbot/handlers/meta.py",
        "backend/app/services/chatbot/handlers/emotional.py",
        "backend/app/services/chatbot/handlers/accessibility.py",
        "backend/app/rag/query_templates.py",
    }
    if module_path in PIPELINE_MODULES:
        # Multi-line imports are common:
        #   from conftest import (
        #       MOCK_QUERY_RESULTS,
        #       send, send_multi, assert_classified,
        #   )
        # The DOTALL flag makes . match newlines, and we constrain the
        # span with ``[^(]*`` inside the parens to avoid matching across
        # unrelated imports elsewhere in the file.
        harness_patterns = [
            r"from\s+conftest\s+import\s+\([^)]*\b(?:send|send_multi|assert_classified)\b[^)]*\)",
            r"from\s+conftest\s+import\s+[^\n(]*\b(?:send|send_multi|assert_classified)\b",
            r"conftest\.(?:send|send_multi|assert_classified)\b",
        ]
        if any(re.search(p, src, re.DOTALL) for p in harness_patterns):
            return True

    return False


def _has_dedicated_test_file(module_path: str) -> Path | None:
    """If ``tests/unit/test_<basename>.py`` exists, return its path."""
    basename = Path(module_path).stem
    candidate = REPO_ROOT / "tests" / "unit" / f"test_{basename}.py"
    return candidate if candidate.exists() else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWeeklyMutationWorkflowPairings:
    """Every (module, tests) pairing in the weekly workflow must be valid."""

    @pytest.fixture(scope="class")
    def pairings(self) -> list[tuple[str, list[str]]]:
        return _parse_weekly_pairings()

    def test_workflow_yaml_is_parseable(self, pairings):
        """Basic sanity: the YAML parses and contains ≥1 pairing."""
        assert pairings, (
            "No matrix entries parsed from mutation-testing.yml. The "
            "workflow YAML structure may have changed — update the "
            "parser in this test file."
        )

    def test_every_listed_test_file_exists(self, pairings):
        """Workflow edits sometimes rename tests without updating the map."""
        missing = []
        for module, tests in pairings:
            for test_path_str in tests:
                test_path = REPO_ROOT / test_path_str
                if not test_path.exists():
                    missing.append((module, test_path_str))
        assert not missing, (
            f"Listed test files don't exist in the repo:\n  " +
            "\n  ".join(f"{module} ← {tf}" for module, tf in missing)
        )

    def test_every_pairing_has_an_exercising_test_file(self, pairings):
        """The CORE guard. At least one listed test file must import or
        patch the module under test. If NONE do, cosmic-ray mutations
        survive silently and the mutation score is meaningless.
        """
        broken = []
        for module, tests in pairings:
            any_exercises = any(
                _test_file_exercises_module(REPO_ROOT / tf, module)
                for tf in tests
            )
            if not any_exercises:
                broken.append((module, tests))
        assert not broken, (
            "Mutation workflow pairings where NO listed test file "
            "imports/patches the module — cosmic-ray will report 0% "
            "mutation scores on these regardless of actual test quality:"
            "\n\n  " +
            "\n  ".join(
                f"{module}\n    listed: {tests}"
                for module, tests in broken
            ) +
            "\n\nFix: update .github/workflows/mutation-testing.yml to "
            "pair each module with test file(s) that actually exercise "
            "it. Consider the dedicated test_<basename>.py if one exists."
        )

    def test_dedicated_test_files_are_in_the_map(self, pairings):
        """If ``tests/unit/test_<basename>.py`` exists for a module but
        isn't listed in the workflow map, that's the error pattern from
        the 2026-04-21 weekly run. Flag it explicitly.
        """
        omitted = []
        for module, tests in pairings:
            dedicated = _has_dedicated_test_file(module)
            if dedicated is None:
                continue
            rel_path = str(dedicated.relative_to(REPO_ROOT))
            if rel_path not in tests:
                omitted.append((module, rel_path, tests))
        assert not omitted, (
            "Mutation workflow has a dedicated test_<basename>.py for "
            "these modules but the workflow doesn't include it. This "
            "was the weekly-run-#1 failure mode:\n\n  " +
            "\n  ".join(
                f"{module}\n    omits: {dedicated}\n    currently lists: {listed}"
                for module, dedicated, listed in omitted
            )
        )

    def test_every_listed_file_actually_exercises_its_module(self, pairings):
        """Even if SOME listed file exercises the module (passing
        test_every_pairing_has_an_exercising_test_file), any INDIVIDUAL
        listed file that doesn't exercise the module is dead weight —
        slows mutation runs, dilutes the focused subset, and suggests
        a copy-paste error. Flag these so the map stays tight.
        """
        dead_entries = []
        for module, tests in pairings:
            for tf in tests:
                if not _test_file_exercises_module(REPO_ROOT / tf, module):
                    dead_entries.append((module, tf))
        assert not dead_entries, (
            "Mutation workflow lists these test files against modules "
            "they don't exercise. Remove them from the pairing or "
            "replace with files that do import/patch the module:\n\n  " +
            "\n  ".join(
                f"{module} ← {tf}" for module, tf in dead_entries
            )
        )


class TestPRMutationWorkflowPairings:
    """Same guards applied to the PR workflow's bash-embedded matrix."""

    @pytest.fixture(scope="class")
    def pairings(self) -> list[tuple[str, list[str]]]:
        return _parse_pr_pairings()

    def test_pr_workflow_has_pairings(self, pairings):
        assert pairings, (
            "No TESTS_FOR_MODULE entries parsed from mutation-testing-pr.yml. "
            "The bash syntax may have changed — update the regex in "
            "_parse_pr_pairings."
        )

    def test_pr_pairings_match_weekly_pairings(self, pairings):
        """The PR workflow's pairings must stay in sync with the weekly
        workflow's. If they drift, a PR could pass its mutation gate
        while the weekly run continues to fail on the same module.
        """
        weekly = {m: set(tests) for m, tests in _parse_weekly_pairings()}
        pr = {m: set(tests) for m, tests in pairings}

        diffs = []
        for module, pr_tests in pr.items():
            if module not in weekly:
                diffs.append(
                    f"{module} is in PR workflow but not weekly workflow"
                )
                continue
            weekly_tests = weekly[module]
            if pr_tests != weekly_tests:
                only_pr = pr_tests - weekly_tests
                only_weekly = weekly_tests - pr_tests
                parts = []
                if only_pr:
                    parts.append(f"only in PR: {sorted(only_pr)}")
                if only_weekly:
                    parts.append(f"only in weekly: {sorted(only_weekly)}")
                diffs.append(f"{module} — {'; '.join(parts)}")
        assert not diffs, (
            "PR and weekly mutation workflows have drifted:\n\n  " +
            "\n  ".join(diffs) +
            "\n\nBoth workflows must use the same (module, tests) "
            "pairings so the PR gate and the weekly run give consistent "
            "signals."
        )

    def test_every_pr_pairing_has_an_exercising_test_file(self, pairings):
        """Same core guard as the weekly version."""
        broken = []
        for module, tests in pairings:
            any_exercises = any(
                _test_file_exercises_module(REPO_ROOT / tf, module)
                for tf in tests
            )
            if not any_exercises:
                broken.append((module, tests))
        assert not broken, (
            f"PR workflow pairings where NO listed file exercises the "
            f"module: {broken}"
        )
