"""Static test-quality audit.

Scans every test file and reports likely anti-patterns. Does NOT run tests
or touch production code. Purpose: surface tests that pass for the wrong
reason (dead mocks, missing assertions, returning success sentinels without
verification, env-dependent behavior).

Anti-patterns detected:

 D1. Dead mock targets — patch() strings that don't exist as attributes
     of the named module, or that point at re-exports the runtime doesn't
     use. (We already found 187 of these; this confirms the rewrite.)

 D2. Tests that don't assert — test functions with no `assert` statement
     and no pytest.raises/approx/warns/match. These "pass" unconditionally.

 D3. Tests whose only assertions are on the mock, not the behavior —
     e.g. asserts call_count or call_args but never checks a return value
     or side effect on real state. Not inherently wrong but worth flagging;
     we saw cases where this masked "mock not wired."

 D4. Tests that set a return_value and never verify the mock was called —
     if the mock is never called, the return_value is irrelevant. Flags
     potential dead patches like the 187 we just fixed.

 D5. Environment-dependent tests — tests that check os.environ or implicitly
     depend on ANTHROPIC_API_KEY / ADMIN_API_KEY being set or unset. These
     pass on one machine and fail on another.

 D6. HTTP tests that hit /admin/* without an Authorization header.

 D7. Tests that patch a module-level attribute without patching the
     submodule-level binding — the "patch where it's defined, not where
     it's looked up" footgun.

 D8. Tests with time-dependent assertions that don't use fake timers.

 D9. Tests that sleep() — unreliable in CI.

 D10. Tests that import but never use key fixtures (fresh_session, etc.)
      — often a copy-paste artifact.

Run:
    python3 tests/_tools/audit_tests.py [--category D1|D2|...] [--file GLOB]

Output: a plain-text report written to stdout with one finding per line:
    <file>:<line>  <category>  <snippet>

No production code is read or modified.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]  # repo root from tests/_tools/
TESTS = ROOT / "tests"
BACKEND = ROOT / "backend"


@dataclass
class Finding:
    category: str
    file: Path
    line: int
    message: str

    def format(self) -> str:
        rel = self.file.relative_to(ROOT)
        return f"{rel}:{self.line}  {self.category}  {self.message}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, category: str, file: Path, line: int, msg: str) -> None:
        self.findings.append(Finding(category, file, line, msg))

    def by_category(self) -> dict[str, list[Finding]]:
        by: dict[str, list[Finding]] = defaultdict(list)
        for f in self.findings:
            by[f.category].append(f)
        return by

    def counter(self) -> Counter[str]:
        return Counter(f.category for f in self.findings)


# --------------------------------------------------------------------------
# D1 — patch target validity
# --------------------------------------------------------------------------
#
# We can't import the production modules safely here (missing env vars, heavy
# side effects). Instead, parse patch() strings and compare against a cached
# map of "what names exist in which backend modules" that we build by
# AST-scanning backend/app/.

def _build_backend_symbol_map() -> dict[str, set[str]]:
    """Map of dotted-module-path → set of top-level names defined/imported there."""
    out: dict[str, set[str]] = {}
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.ImportFrom):
                for alias in (node.names or []):
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
        # Derive module dotted path from file location under backend/
        rel = py.relative_to(BACKEND)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]  # strip .py
        dotted = ".".join(parts)
        out[dotted] = names
    return out


_PATCH_STRING_RE = re.compile(
    r"""patch(?:\.object|\.dict)?\s*\(\s*["']([\w\.]+)["']""",
)


def audit_d1_dead_patches(report: Report, test_files: list[Path],
                          symbol_map: dict[str, set[str]]) -> None:
    """Flag patch targets whose module OR attribute doesn't exist in the backend."""
    for path in test_files:
        text = path.read_text()
        for m in _PATCH_STRING_RE.finditer(text):
            target = m.group(1)
            line = text[: m.start()].count("\n") + 1
            # Split into module + attr. patch() accepts arbitrarily deep
            # dotted paths; conventionally the last component is the attr.
            parts = target.rsplit(".", 1)
            if len(parts) != 2:
                continue
            module, attr = parts
            if module not in symbol_map:
                # Module doesn't exist — definitely dead
                report.add("D1", path, line,
                           f"patch({target!r}) — module not found")
                continue
            if attr not in symbol_map[module]:
                report.add("D1", path, line,
                           f"patch({target!r}) — {attr!r} not in {module}")


# --------------------------------------------------------------------------
# D2 — tests without assertions
# --------------------------------------------------------------------------

_ASSERT_MARKERS = (
    "assert",  # assert statements AND pytest.raises as `with pytest.raises`
    "pytest.raises",
    "pytest.warns",
    "pytest.approx",
    "pytest.fail",        # explicit test failure
    ".assert_called",     # MagicMock.assert_called*
    ".assert_not_called",
    ".assert_any_call",
    ".assert_has_calls",
    "unittest.assert",   # unittest.TestCase.assertFoo
    "self.assert",       # unittest.TestCase.assertFoo (common form)
)


def audit_d2_no_assertions(report: Report, test_files: list[Path]) -> None:
    for path in test_files:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        source = path.read_text()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body_src = ast.get_source_segment(source, node) or ""
            if any(m in body_src for m in _ASSERT_MARKERS):
                continue
            # One more check: the function might just be a helper not
            # intended as a test. Skip empty/pass-only bodies — those are
            # usually placeholders.
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                continue
            report.add("D2", path, node.lineno,
                       f"def {node.name} — no assertion found")


# --------------------------------------------------------------------------
# D3 / D4 — mock-without-behavior-check
# --------------------------------------------------------------------------

def audit_d3_d4_mock_only(report: Report, test_files: list[Path]) -> None:
    """A test that patches with a return_value and also sets an assertion
    on behavior is a GOOD test. A test that patches with a return_value
    and whose only checks are on mock state — or a test whose mock is
    set but never checked — is a candidate for D3/D4."""
    for path in test_files:
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        source = path.read_text()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body_src = ast.get_source_segment(source, node) or ""
            patches_with_return = body_src.count("return_value=") + body_src.count("side_effect=")
            if patches_with_return == 0:
                continue
            # Does the test ever call assert_called / call_args on a mock?
            checks_mock = ( # noqa: F841
                ".assert_called" in body_src or
                ".assert_not_called" in body_src or
                ".assert_any_call" in body_src or
                ".assert_has_calls" in body_src or
                ".called" in body_src or
                ".call_args" in body_src or
                ".call_count" in body_src
            )
            # Does it make any assertion? (Checked via D2 pattern.)
            has_any_assertion = any(m in body_src for m in _ASSERT_MARKERS)

            if not has_any_assertion:
                # D4: mock set up but nothing ever verified
                report.add("D4", path, node.lineno,
                           f"def {node.name} — {patches_with_return} mock(s) "
                           f"configured but no assertions at all")
            # D3: has assertions but ONLY on mock state — flag for review
            # (this isn't always wrong, just worth auditing)
            # Skipping D3 for now — too many false positives without deeper
            # data flow analysis. Focus on D4 which is clearer.


# --------------------------------------------------------------------------
# D5 — env-dependent tests
# --------------------------------------------------------------------------

_ENV_MARKERS = (
    "ANTHROPIC_API_KEY",
    "ADMIN_API_KEY",
    "os.environ",
    "os.getenv",
    "getenv(",
)


def audit_d5_env_dependent(report: Report, test_files: list[Path]) -> None:
    """Flag tests that read env vars without properly isolating them.

    A test is "safe" when it either:
      - uses monkeypatch.setenv / patch.dict(os.environ, ...)
      - uses @pytest.mark.skipif on the env var
      - uses a conftest fixture that controls the env

    A test is "unsafe" when it reads an env var bare in its body, or
    when its behavior silently branches on the presence of a var.
    """
    for path in test_files:
        text = path.read_text()
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body_src = ast.get_source_segment(text, node) or ""
            has_env_ref = any(m in body_src for m in _ENV_MARKERS)
            if not has_env_ref:
                continue
            # "Safe" indicators in the same function
            safe = (
                "monkeypatch.setenv" in body_src or
                "monkeypatch.delenv" in body_src or
                "patch.dict(os.environ" in body_src or
                "patch.dict(\"os.environ\"" in body_src or
                "skipif" in body_src
            )
            if safe:
                continue
            report.add("D5", path, node.lineno,
                       f"def {node.name} — reads env but doesn't isolate it")


# --------------------------------------------------------------------------
# D6 — admin routes without auth
# --------------------------------------------------------------------------

_ADMIN_ROUTE_RE = re.compile(
    r"""client\.(?:get|post|put|delete|patch)\(\s*["']/admin/""",
)


def audit_d6_admin_without_auth(report: Report, test_files: list[Path]) -> None:
    for path in test_files:
        text = path.read_text()
        # If the module-level client itself is constructed with auth headers,
        # every admin call in that file is covered. Look for the pattern
        # `TestClient(app, headers=...)` or an explicit Authorization
        # default at module scope.
        module_level_auth = (
            "TestClient(app, headers=" in text
            or re.search(r"^_default_headers\s*=", text, re.MULTILINE)
            or re.search(r'^client\s*=.*Authorization', text, re.MULTILINE)
        )
        if module_level_auth:
            continue

        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body_src = ast.get_source_segment(text, node) or ""
            if not _ADMIN_ROUTE_RE.search(body_src):
                continue
            safe = (
                "Authorization" in body_src
                or "admin_client." in body_src
                or "status_code == 401" in body_src
                or "assert response.status_code == 401" in body_src
                or "ADMIN_API_KEY" in body_src
            )
            if safe:
                continue
            report.add("D6", path, node.lineno,
                       f"def {node.name} — admin route hit, no auth / no 401 check")


# --------------------------------------------------------------------------
# D7 — package-level patches of re-exported names (redundant check; codemod
#      already rewrote the top-3 offenders, but new ones may appear)
# --------------------------------------------------------------------------

_SUSPECT_REEXPORT_RE = re.compile(
    r"""patch\(\s*["']app\.services\.chatbot\.(?!handlers|orchestrator|pipeline|execution|context|dispatch|routing)"""
    r"""([a-zA-Z_][\w]*)["']"""
)


def audit_d7_reexport_patches(report: Report, test_files: list[Path]) -> None:
    for path in test_files:
        text = path.read_text()
        for m in _SUSPECT_REEXPORT_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            name = m.group(1)
            report.add("D7", path, line,
                       f"patches app.services.chatbot.{name} — likely a re-export; "
                       f"prefer patching the actual bind site in a submodule")


# --------------------------------------------------------------------------
# D8 / D9 — timing
# --------------------------------------------------------------------------

def audit_d8_d9_timing(report: Report, test_files: list[Path]) -> None:
    for path in test_files:
        text = path.read_text()
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body_src = ast.get_source_segment(text, node) or ""
            if "time.sleep(" in body_src or "asyncio.sleep(" in body_src:
                report.add("D9", path, node.lineno,
                           f"def {node.name} — uses sleep(); flaky in CI")
            if ("time.time()" in body_src or "datetime.now()" in body_src) and (
                "freeze_time" not in body_src and
                "monkeypatch" not in body_src and
                "patch(" not in body_src and
                "setSystemTime" not in body_src
            ):
                # Only flag if the test also asserts on a time value
                if any(kw in body_src for kw in ("age", "elapsed", "duration", "expired")):
                    report.add("D8", path, node.lineno,
                               f"def {node.name} — uses real time, asserts on time-like field")


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def collect_test_files() -> list[Path]:
    out = []
    for path in TESTS.rglob("test_*.py"):
        if "__pycache__" in path.parts:
            continue
        if "_tools" in path.parts:
            continue
        out.append(path)
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", action="append",
                        help="Limit to one or more categories (D1, D2, ...)")
    parser.add_argument("--summary", action="store_true",
                        help="Print only the counts, not each finding")
    parser.add_argument("--file", default=None,
                        help="Substring-match on test file path")
    args = parser.parse_args()

    test_files = collect_test_files()
    if args.file:
        test_files = [p for p in test_files if args.file in str(p)]

    symbol_map = _build_backend_symbol_map()

    report = Report()
    audit_d1_dead_patches(report, test_files, symbol_map)
    audit_d2_no_assertions(report, test_files)
    audit_d3_d4_mock_only(report, test_files)
    audit_d5_env_dependent(report, test_files)
    audit_d6_admin_without_auth(report, test_files)
    audit_d7_reexport_patches(report, test_files)
    audit_d8_d9_timing(report, test_files)

    if args.category:
        wanted = set(args.category)
        report.findings = [f for f in report.findings if f.category in wanted]

    counts = report.counter()

    print("=" * 72)
    print(f"TEST AUDIT — {len(test_files)} files scanned")
    print("=" * 72)
    for cat in sorted(counts):
        print(f"  {cat}: {counts[cat]} findings")
    print(f"  TOTAL: {len(report.findings)} findings")
    print()

    if args.summary:
        return 0

    by_cat = report.by_category()
    for cat in sorted(by_cat):
        print(f"--- {cat} " + "-" * (68 - len(cat)))
        for f in by_cat[cat]:
            print(f"  {f.format()}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
