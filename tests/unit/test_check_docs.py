"""Regression tests for `scripts/check_docs.py`.

The drift checker is the project's first line of defense against stale
documentation. It silently failed for a long time because the test-count
regex (`\\d+ tests across`) didn't match the actual TESTING.md format
(`~3,845 collected tests across`) — the word "collected" was inserted
between the count and "tests across", making the pattern un-matchable.
No warning fired in CI for months while drift accumulated.

These tests pin the regex to several plausible formatting variants so the
same kind of silent break can't recur.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# scripts/ is not a package, so add it to path explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_docs  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_issues():
    """Each test gets a fresh issue list."""
    check_docs._issues.clear()
    yield
    check_docs._issues.clear()


def _make_args(**overrides) -> argparse.Namespace:
    """Build the args namespace that check functions expect."""
    defaults = {"fix": False, "skip": "", "severity": "warning",
                "json": False, "github": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _setup_fake_repo(tmp_path: Path, *, testing_md: str,
                     unit_files: int = 1, integration_files: int = 1,
                     tests_per_file: int = 1) -> Path:
    """Build a minimal docs/ + tests/ tree under tmp_path and return the root.

    `testing_md` is the literal content for `docs/TESTING.md`.
    Test files are bare `def test_X(): pass` — counted by AST walk.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "TESTING.md").write_text(testing_md)

    tests = tmp_path / "tests"
    (tests / "unit").mkdir(parents=True)
    (tests / "integration").mkdir()

    test_body = "\n".join(f"def test_t{i}(): pass"
                          for i in range(tests_per_file))
    for i in range(unit_files):
        (tests / "unit" / f"test_unit_{i}.py").write_text(test_body)
    for i in range(integration_files):
        (tests / "integration" / f"test_int_{i}.py").write_text(test_body)

    return tmp_path


def _run_check_with_paths(tmp_path: Path, *, fix: bool = False) -> list:
    """Run check_test_counts with module path constants pointed at tmp_path."""
    with patch.object(check_docs, "ROOT", tmp_path), \
         patch.object(check_docs, "DOCS_DIR", tmp_path / "docs"), \
         patch.object(check_docs, "TESTS_DIR", tmp_path / "tests"):
        check_docs.check_test_counts(_make_args(fix=fix))
    return list(check_docs._issues)


# ---------------------------------------------------------------------------
# Total-count regex coverage — the silent-break failure mode
# ---------------------------------------------------------------------------


class TestTotalCountRegex:
    """Pin every formatting variant that has appeared (or could plausibly
    appear) in TESTING.md's headline count sentence. If someone adds a new
    qualifier word between the count and 'tests across', this test pins the
    expectation that we either match it or update the regex."""

    @pytest.mark.parametrize("phrase", [
        # Original (pre-comma, pre-tilde, pre-"collected") format
        "200 tests across 5 files",
        # Tilde prefix
        "~200 tests across 5 files",
        # Comma in number (1,000+)
        "1,000 tests across 5 test files",
        # "collected" infix (the actual silent-break case)
        "200 collected tests across 5 test files",
        # Combination: tilde + comma + collected + "test files"
        "~1,234 collected tests across 5 test files",
    ])
    def test_drifts_caught_for_all_formats(self, tmp_path, phrase):
        """Every variant must produce a warning when both numbers are wrong."""
        # Construct a doc with `phrase`; actual will be 1 unit file with 1
        # test, so the doc's claims (200/5 etc.) are heavily wrong.
        md = f"# Testing\n\n{phrase}\n"
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=1,
                         integration_files=0, tests_per_file=1)

        issues = _run_check_with_paths(tmp_path)

        # Should warn at least about the file count (1 actual vs 5 doc)
        file_warns = [i for i in issues
                      if "test files" in i.message
                      and i.category == "test-count"]
        assert file_warns, (
            f"No file-count warning for phrase {phrase!r}. "
            f"All issues: {[i.message for i in issues]}"
        )


class TestFileCountAutoFix:
    """The auto-fix path must update the file count without touching the
    test-count number, because the AST counter does not match the doc's
    'collected' wording (post-parametrize-expansion). Auto-writing the AST
    count into a 'collected tests' phrase would replace one wrong number
    with another wrong number."""

    def test_fix_updates_file_count_only(self, tmp_path):
        md = (
            "# Testing\n\n"
            "The suite has ~3,845 collected tests across 69 test files "
            "(plus other stuff).\n"
        )
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=2,
                         integration_files=1, tests_per_file=1)
        # Actual: 3 files, 3 tests.

        _run_check_with_paths(tmp_path, fix=True)

        result = (tmp_path / "docs" / "TESTING.md").read_text()
        # File count fixed
        assert "across 3 test files" in result
        # Test count NOT fixed (3845 stays — auto-fix is conservative)
        assert "~3,845 collected tests" in result


class TestPerDirectoryFileCounts:
    """Drift in per-directory counts like '`tests/unit/` (57 files — ...)'
    is a separate sentence from the headline count, hand-written, and
    drifted independently in production."""

    def test_unit_count_drift_caught(self, tmp_path):
        md = (
            "# Testing\n\n"
            "Tests are in `tests/unit/` (57 files — no DB) and "
            "`tests/integration/` (12 files — mocked).\n"
        )
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=3,
                         integration_files=2, tests_per_file=1)

        issues = _run_check_with_paths(tmp_path)
        msgs = [i.message for i in issues]
        assert any("tests/unit/ has 57" in m and "actual 3" in m
                   for m in msgs), msgs
        assert any("tests/integration/ has 12" in m and "actual 2" in m
                   for m in msgs), msgs

    def test_correct_counts_no_warn(self, tmp_path):
        md = (
            "# Testing\n\n"
            "Tests are in `tests/unit/` (3 files — no DB) and "
            "`tests/integration/` (2 files — mocked).\n"
        )
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=3,
                         integration_files=2, tests_per_file=1)

        issues = _run_check_with_paths(tmp_path)
        dir_issues = [i for i in issues
                      if "tests/unit/" in i.message
                      or "tests/integration/" in i.message]
        assert not dir_issues, (
            f"Unexpected dir-count warnings: "
            f"{[i.message for i in dir_issues]}"
        )


class TestFilenameCollisionDoesNotUndercount:
    """Latent bug pinned: when both `tests/unit/test_X.py` and
    `tests/integration/test_X.py` exist (same basename), the file count
    should still be 2, not 1. Earlier versions used a dict keyed by
    filename and silently collapsed collisions."""

    def test_same_basename_in_two_dirs(self, tmp_path):
        md = (
            "# Testing\n\n"
            "100 tests across 2 test files (...)\n"
        )
        # Both subdirs get a file with the same name.
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "TESTING.md").write_text(md)

        tests = tmp_path / "tests"
        for sub in ("unit", "integration"):
            (tests / sub).mkdir(parents=True)
            (tests / sub / "test_collision.py").write_text(
                "def test_one(): pass\n"
            )

        issues = _run_check_with_paths(tmp_path)
        # Doc says "2 test files" — that's now accurate. No file-count
        # warning should fire (would have fired with the old dict-dedup
        # logic claiming "actual 1").
        file_warns = [i for i in issues
                      if "test files" in i.message]
        assert not file_warns, (
            f"File count under-counted due to filename collision: "
            f"{[i.message for i in file_warns]}"
        )


class TestDriftIgnoreHonored:
    """Test counts can be intentionally hard to validate (e.g., the
    AST-vs-pytest-collection convention gap on the 'collected tests'
    number). The `drift:ignore` comment must suppress warnings on the
    next non-blank line so the team can opt out of specific lines
    without disabling the whole check."""

    def test_ignore_comment_suppresses_total_count_warning(self, tmp_path):
        md = (
            "# Testing\n\n"
            "<!-- drift:ignore: known convention gap -->\n"
            "10000 tests across 50 test files\n"
        )
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=2,
                         integration_files=1, tests_per_file=1)
        # Actual: 3 files, 3 tests; doc claims 10000/50 — both wrong, both
        # on the ignored line, so neither should fire.

        issues = _run_check_with_paths(tmp_path)
        for i in issues:
            assert "10000" not in i.message and "50 test files" not in i.message, (
                f"drift:ignore should have suppressed warning: {i.message}"
            )

    def test_file_level_ignore_suppresses_all(self, tmp_path):
        md = (
            "<!-- drift:ignore-file -->\n"
            "# Testing\n\n"
            "99 tests across 50 test files\n"
            "Tests in `tests/unit/` (40 files — ...).\n"
        )
        _setup_fake_repo(tmp_path, testing_md=md, unit_files=2,
                         integration_files=1, tests_per_file=1)

        issues = _run_check_with_paths(tmp_path)
        assert not issues, (
            f"drift:ignore-file should suppress everything, got: "
            f"{[i.message for i in issues]}"
        )


class TestNonUtf8FilesSkipped:
    """macOS AppleDouble files (`._*`) appear when a tarball made on
    macOS is extracted on another platform. They're binary metadata,
    not markdown — but `Path.rglob('*.md')` picks them up by extension
    and crashes UTF-8 readers. The checker must skip them."""

    def test_dot_underscore_files_filtered_out(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "real.md").write_text("# Real markdown\n")
        # AppleDouble companion — non-UTF-8 binary
        (docs / "._real.md").write_bytes(b"\x00\x05\x16\x07\xa2\xff\x00")

        with patch.object(check_docs, "ROOT", tmp_path), \
             patch.object(check_docs, "DOCS_DIR", docs), \
             patch.object(check_docs, "SCRIPTS_DIR", tmp_path / "scripts"):
            files = check_docs.all_md_files()

        names = {f.name for f in files}
        assert "real.md" in names
        assert "._real.md" not in names
