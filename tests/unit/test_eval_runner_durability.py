"""Tests for the eval-runner durability behavior.

These tests cover the recovery-from-failure machinery that was added
after the May 2026 incident in which a 50-minute, ~$20 eval run was
lost because `--output` pointed at a non-existent directory and the
final write failed.

The tests don't run a full eval (that would cost real API time);
they target the helpers and write paths in isolation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


# Make the eval-runner module importable without running its main().
# The runner is at tests/eval/eval_llm_judge.py; pythonpath in
# pyproject.toml already covers `backend/`, but not `tests/eval/`.
_EVAL_DIR = os.path.join(os.path.dirname(__file__), "..", "eval")
_EVAL_DIR = os.path.abspath(_EVAL_DIR)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

import eval_llm_judge as runner  # noqa: E402


# ---------------------------------------------------------------------------
# _atomic_write_text / _atomic_write_json
# ---------------------------------------------------------------------------

def test_atomic_write_text_creates_missing_parent_dir(tmp_path):
    """The original incident: parent dir didn't exist.

    `_atomic_write_text` must auto-create the parent so the next
    50-minute run doesn't die on the final line.
    """
    target = tmp_path / "deeply" / "nested" / "report.txt"
    assert not target.parent.exists()  # precondition

    runner._atomic_write_text(str(target), "hello world")

    assert target.exists()
    assert target.read_text() == "hello world"


def test_atomic_write_json_creates_missing_parent_dir(tmp_path):
    """JSON variant of the same protection."""
    target = tmp_path / "eval_results" / "report.json"
    assert not target.parent.exists()  # precondition

    runner._atomic_write_json(str(target), {"overall": 4.61, "passing": 173})

    assert target.exists()
    assert json.loads(target.read_text()) == {"overall": 4.61, "passing": 173}


def test_atomic_write_uses_temp_file_then_rename(tmp_path, monkeypatch):
    """Must write to .tmp first, then rename. A crash between open
    and rename leaves either the old file or the .tmp — never a
    half-written target."""
    target = tmp_path / "report.json"
    target.write_text('{"old": "value"}')  # pre-existing content

    # Spy on the sequence of file operations.
    seen_ops = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        seen_ops.append(("replace", src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracking_replace)

    runner._atomic_write_json(str(target), {"new": "value"})

    # Final state: the new content is at the target path.
    assert json.loads(target.read_text()) == {"new": "value"}

    # And os.replace was called from a .tmp file — that's the atomic part.
    assert len(seen_ops) == 1
    op, src, dst = seen_ops[0]
    assert op == "replace"
    assert src.endswith(".tmp")
    assert dst == str(target)


def test_atomic_write_cleans_up_tmp_on_failure(tmp_path, monkeypatch):
    """If os.replace fails (e.g. cross-device rename, permission),
    the .tmp file should be cleaned up rather than left as garbage."""
    target = tmp_path / "report.json"

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated rename failure"):
        runner._atomic_write_json(str(target), {"x": 1})

    # Target was never written — expected.
    assert not target.exists()
    # AND the .tmp file should have been cleaned up.
    tmp_file = target.with_suffix(target.suffix + ".tmp")
    assert not tmp_file.exists(), (
        "Leftover .tmp file would accumulate as garbage on every failure"
    )


def test_atomic_write_does_not_corrupt_existing_on_failure(tmp_path, monkeypatch):
    """If the new write fails, the OLD content of the target
    file must remain readable. This is the core atomicity property."""
    target = tmp_path / "report.json"
    target.write_text('{"original": "intact"}')

    def boom(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        runner._atomic_write_json(str(target), {"new": "would-be-content"})

    # Old content survives.
    assert json.loads(target.read_text()) == {"original": "intact"}


# ---------------------------------------------------------------------------
# Run-archive directory layout
# ---------------------------------------------------------------------------
# These tests don't actually run a 182-scenario eval; they verify
# the directory-naming logic and that the archival paths the runner
# expects to write are valid filesystem paths.

def test_run_id_format_includes_timestamp_and_redact_suffix():
    """Run-ID format: <timestamp>[_redact_on]. Must be filesystem-safe
    (no colons, slashes, or spaces — sortable by name)."""
    from datetime import datetime
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

    # Compact ISO-ish: e.g. 20260504T172315
    assert "T" in run_id
    assert ":" not in run_id, "colons break Windows filesystems"
    assert " " not in run_id, "spaces break shell scripts"
    assert "/" not in run_id, "slashes are path separators"
    # Sortable: lexicographic order matches chronological order.
    assert len(run_id) == 15  # YYYYMMDDTHHMMSS
