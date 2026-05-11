"""Regression tests for the D1 audit check (`audit_d1_dead_patches`).

The D1 check finds patch-call argument strings whose target doesn't
exist on the named module — the original silent-no-op bug class that
motivated the entire audit framework.

Pre-May-2026, the check split the patch target on the LAST dot only:

    target = "app.services.X.datetime.datetime"
    module, attr = target.rsplit(".", 1)
    # module = "app.services.X.datetime"  ← treated as a module path
    # attr   = "datetime"

That works for `module.function`, but breaks for the legitimate chained-
attribute pattern `module.imported_module.class`. Specifically, the new
`tests/unit/test_cold_context_and_prevention_regex.py` patches
`app.services.slot_extraction_regex.datetime.datetime` to swap the
`datetime` class on the `datetime` module that `slot_extraction_regex`
imports — a standard mock-the-clock idiom — and got flagged as 8 dead
patches even though the tests run and pass.

The fix walks the dotted path from longest-prefix to shortest, finds
the longest prefix that names a real backend module, and verifies the
next component is a name on that module. Deeper components are
sub-attribute accesses that `patch()` resolves at runtime, which the
static audit can't (and shouldn't try to) verify.

These tests cover:
  - The chained-attribute pattern is accepted (the bug we fixed).
  - Genuine bugs are still caught (module/attr genuinely missing).
  - The single legitimate baseline finding still surfaces.

A note on style: the test sources below construct synthetic
`@<decorator>("target")` decorators piece by piece via string
concatenation. That's because `tests/_tools/audit_tests.py` D1 (and
D7) regex-grep the FILE SOURCE for the patch-call substring — they
don't care about syntactic context, so any literal patch-call
substring in this file (even inside a textwrap.dedent of a synthetic
test source) gets parsed as a live patch and flagged. Building the
substrings dynamically avoids the false positive while keeping the
synthetic test source readable when materialized into the temp file.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_TOOL_PATH = REPO_ROOT / "tests" / "_tools" / "audit_tests.py"

# Constructed dynamically — see the module docstring note. The audit's
# patch-regex looks for the literal substring `patch(` in the file
# source; defining the decorator name here as the concatenation of two
# string fragments keeps it from appearing as a contiguous literal in
# this file's source while still producing the right token in synthetic
# test sources at runtime.
_PATCH_DECORATOR = "@" + "patch"


def _load_audit_tool():
    """Load tests/_tools/audit_tests.py as a module.

    tests/_tools/ has no `__init__.py` (intentionally — the tools are
    runnable scripts, not a package), so a normal `import` won't find
    them. Use importlib's spec mechanism instead.
    """
    spec = importlib.util.spec_from_file_location(
        "audit_tests_module_under_test", AUDIT_TOOL_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_tests_module_under_test"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _build_synthetic_test(target: str) -> str:
    """Construct a one-decorator test-file source with `target` as the
    `patch()` argument. Built via string concatenation so this module's
    own source doesn't trip the audit's patch-call regex."""
    decorator_line = f'{_PATCH_DECORATOR}("{target}")'
    return textwrap.dedent(f"""\
        from unittest.mock import patch
        {decorator_line}
        def test_x(_): pass
    """)


def _run_d1(audit_mod, source: str, tmp_path: Path) -> list:
    """Write `source` to a temp file and run D1 against it.

    Returns the list of Finding objects. The function builds the real
    backend symbol map once (slow first call, fast thereafter due to
    Python's cached module imports) so the assertions can reference
    actual backend modules like `app.services.slot_extraction_regex`.
    """
    test_file = tmp_path / "synthetic_test.py"
    test_file.write_text(source)
    report = audit_mod.Report()
    symbol_map = audit_mod._build_backend_symbol_map()
    audit_mod.audit_d1_dead_patches(report, [test_file], symbol_map)
    return [f for f in report.findings if f.category == "D1"]


class TestD1ChainedAttributePattern:
    """The chained-attribute pattern must NOT be flagged."""

    def test_chained_datetime_module_attribute_accepted(self, tmp_path):
        """A patch target of `app.services.X.datetime.datetime` where X
        did `import datetime` is legitimate — datetime is a real
        attribute of X (imported), datetime.datetime is the class to
        patch. This is the exact bug we fixed.
        """
        audit_mod = _load_audit_tool()
        # slot_extraction_regex DOES `import datetime` at module top.
        source = _build_synthetic_test(
            "app.services.slot_extraction_regex.datetime.datetime",
        )
        findings = _run_d1(audit_mod, source, tmp_path)
        assert findings == [], (
            f"Chained-attribute patch should not be flagged but got: "
            f"{[f.message for f in findings]}"
        )


class TestD1StillCatchesGenuineBugs:
    """The fix must not make D1 too permissive — real bugs should still surface."""

    def test_module_does_not_exist(self, tmp_path):
        audit_mod = _load_audit_tool()
        source = _build_synthetic_test(
            "app.services.totally_made_up_module.thing",
        )
        findings = _run_d1(audit_mod, source, tmp_path)
        assert len(findings) == 1
        assert "totally_made_up_module" in findings[0].message

    def test_attribute_not_defined_on_real_module(self, tmp_path):
        audit_mod = _load_audit_tool()
        # slot_extraction_regex is real but doesn't define this name.
        source = _build_synthetic_test(
            "app.services.slot_extraction_regex.function_that_does_not_exist",
        )
        findings = _run_d1(audit_mod, source, tmp_path)
        assert len(findings) == 1
        assert "function_that_does_not_exist" in findings[0].message
        assert "app.services.slot_extraction_regex" in findings[0].message

    def test_attribute_not_on_package_init(self, tmp_path):
        # app.services.chatbot is a package with an __init__.py; we
        # name an attribute that the package definitely doesn't expose.
        audit_mod = _load_audit_tool()
        source = _build_synthetic_test(
            "app.services.chatbot.totally_invented_name",
        )
        findings = _run_d1(audit_mod, source, tmp_path)
        assert len(findings) == 1
        assert "totally_invented_name" in findings[0].message


class TestD1ValidSimplePatches:
    """Standard last-dot patches against real backend symbols pass cleanly."""

    def test_simple_function_patch_accepted(self, tmp_path):
        """A direct patch of a function on a backend module."""
        audit_mod = _load_audit_tool()
        # app.services.classifier imports detect_crisis from crisis_detector.
        # Patching it there is the correct "where-imported" pattern.
        source = _build_synthetic_test(
            "app.services.classifier.detect_crisis",
        )
        findings = _run_d1(audit_mod, source, tmp_path)
        assert findings == [], (
            f"Simple patch of a real imported name should not be "
            f"flagged: {[f.message for f in findings]}"
        )


class TestD1Robustness:
    """Edge cases that shouldn't crash the audit."""

    def test_too_short_target_ignored(self, tmp_path):
        """A single-component target (no dot) is malformed for the
        patch call but should not crash D1 — the call itself will fail
        at runtime, so the audit can stay silent."""
        audit_mod = _load_audit_tool()
        source = _build_synthetic_test("just_a_name")
        # Should not raise; may or may not report (currently doesn't,
        # since `parts < 2` is skipped at the top of the loop).
        findings = _run_d1(audit_mod, source, tmp_path)
        # Pin the current behavior: no finding. If a future change adds
        # one, that's an intentional improvement — update this test.
        assert findings == []
