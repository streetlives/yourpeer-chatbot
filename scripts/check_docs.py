#!/usr/bin/env python3
"""
Docs drift checker — flags stale facts in markdown files.

Compares hardcoded numbers, model IDs, env vars, and category counts in
documentation against the actual codebase. Run after any change to tests,
models, configuration, or key source-code constants to catch drift before
it reaches main.

Usage:
    python scripts/check_docs.py                    # report all drift
    python scripts/check_docs.py --fix              # auto-fix what's safe
    python scripts/check_docs.py --json             # machine-readable output
    python scripts/check_docs.py --github           # GitHub Actions annotations
    python scripts/check_docs.py --severity=error   # only fail on errors
    python scripts/check_docs.py --skip=test-counts # disable one check
    python scripts/check_docs.py --skip=audit-baseline  # skip the live audit run

Designed to run in CI (exits 1 if drift at or above `--severity` threshold,
default=warning) or locally. No external dependencies beyond the Python
standard library.

To suppress a known false positive, add an HTML comment in the markdown:

    <!-- drift:ignore -->            applies to the next non-blank line
    <!-- drift:ignore-next-line -->  alias for the above
    <!-- drift:ignore-file -->       applies to the entire file (anywhere)
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# -----------------------------------------------------------------------
# PATHS
# -----------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
TESTS_DIR = ROOT / "tests"
BACKEND_DIR = ROOT / "backend"
SCRIPTS_DIR = ROOT / "scripts"


# -----------------------------------------------------------------------
# ISSUE MODEL (with severity, line numbers, suggested fixes)
# -----------------------------------------------------------------------

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


@dataclass
class Issue:
    file: str
    severity: str                        # "error", "warning", "info"
    category: str                        # e.g. "test-count", "bare-filename"
    message: str
    line: Optional[int] = None
    suggested_fix: Optional[str] = None
    fixed: bool = False                  # True if --fix auto-resolved it


_issues: list[Issue] = []
_scanned_files = 0
_scanned_refs = 0


def add(issue: Issue) -> None:
    _issues.append(issue)


def warn(file: str, msg: str, *, severity: str = "warning",
         category: str = "generic", line: Optional[int] = None,
         suggested_fix: Optional[str] = None) -> None:
    """Back-compat shim: existing calls to warn(file, msg) still work."""
    add(Issue(file=file, severity=severity, category=category, message=msg,
              line=line, suggested_fix=suggested_fix))


# -----------------------------------------------------------------------
# DRIFT-IGNORE COMMENT SUPPORT
# -----------------------------------------------------------------------
# Industry convention — see lychee's `lychee-ignore`, markdownlint's
# `markdownlint-disable`. Lets us suppress false positives in
# self-referential files (like the audit doc cataloging fixed drift).

_IGNORE_FILE_RE = re.compile(
    r'<!--\s*drift:ignore-file(?::[^>]*)?\s*-->', re.IGNORECASE)
_IGNORE_LINE_RE = re.compile(
    r'<!--\s*drift:ignore(?:-next-line)?(?::[^>]*)?\s*-->', re.IGNORECASE)


def file_is_ignored(content: str) -> bool:
    """True if the file has a `<!-- drift:ignore-file -->` marker anywhere."""
    return bool(_IGNORE_FILE_RE.search(content))


def ignored_lines(content: str) -> set[int]:
    """Return 1-indexed line numbers where drift reports should be suppressed.

    `<!-- drift:ignore -->` on its own line suppresses the next non-blank line.
    Same comment at the end of a content line suppresses that line.
    """
    ignored: set[int] = set()
    lines = content.split("\n")
    for i, line in enumerate(lines, start=1):
        if not _IGNORE_LINE_RE.search(line):
            continue
        stripped = _IGNORE_LINE_RE.sub('', line).strip()
        if stripped:
            ignored.add(i)  # inline — suppress this line
        else:
            for j in range(i + 1, len(lines) + 1):
                if lines[j - 1].strip():
                    ignored.add(j)
                    break
    return ignored


# -----------------------------------------------------------------------
# FILE DISCOVERY
# -----------------------------------------------------------------------

def all_md_files() -> list[Path]:
    """Return all markdown files from repo root, docs/, scripts/ (recursive)."""
    files: list[Path] = list(ROOT.glob("*.md"))
    files.extend(DOCS_DIR.rglob("*.md"))
    if SCRIPTS_DIR.exists():
        files.extend(SCRIPTS_DIR.rglob("*.md"))
    return sorted(set(files))


def rel(path: Path) -> str:
    """Path relative to ROOT, with forward slashes for display."""
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


# -----------------------------------------------------------------------
# AST-BASED SOURCE-OF-TRUTH EXTRACTORS
# -----------------------------------------------------------------------
# Parsing Python with AST instead of regex: reformatting doesn't break
# detection, and we can trust the extracted values.

def _parse_py(path: Path) -> Optional[ast.Module]:
    if not path.exists():
        return None
    try:
        return ast.parse(path.read_text())
    except SyntaxError:
        return None


def extract_int_constant(path: Path, name: str) -> Optional[int]:
    """Find `NAME = <int>` in a Python file, return the int value."""
    tree = _parse_py(path)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    if isinstance(node.value, ast.Constant) and \
                       isinstance(node.value.value, int):
                        return node.value.value
    return None


def extract_collection_len(path: Path, name: str) -> Optional[int]:
    """Find `NAME = [...] | (...) | {...}` and return len()."""
    tree = _parse_py(path)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    v = node.value
                    if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                        return len(v.elts)
                    if isinstance(v, ast.Dict):
                        return len(v.keys)
    return None


def extract_string_constants(path: Path, suffix: str = "_MODEL") -> dict[str, str]:
    """Find all `<name><suffix> = "string"` assignments."""
    result: dict[str, str] = {}
    tree = _parse_py(path)
    if tree is None:
        return result
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith(suffix):
                    if isinstance(node.value, ast.Constant) and \
                       isinstance(node.value.value, str):
                        result[target.id] = node.value.value
    return result


# -----------------------------------------------------------------------
# CHECK CONFIG — what we watch for drift
# -----------------------------------------------------------------------
# Adding a tracked constant is a single-entry edit. The checker reads the
# source-of-truth at runtime via AST and cross-references each regex capture
# in every markdown file.

# Gap 3: numeric constants in prose.
NUMERIC_CONSTANTS = [
    {
        "name": "_DISPLAY_PAGE_SIZE",
        "source": BACKEND_DIR / "app/services/chatbot/context.py",
        "patterns": [
            r"displays? the first (\d+)",
            r"first (\d+) (?:results?|option)",
            r"displays? first (\d+)",
        ],
    },
]

# Gap 4: category / feature counts.
CATEGORY_COUNTS = [
    {
        "name": "_CRISIS_CATEGORIES",
        "source": BACKEND_DIR / "app/services/crisis_detector.py",
        "desc": "crisis categories",
        "patterns": [
            r"(\d+) crisis categor(?:y|ies)",
            r"(Seven|Eight|Nine|seven|eight|nine) crisis categor(?:y|ies)",
        ],
        "word_numbers": {"Seven": 7, "Eight": 8, "Nine": 9,
                         "seven": 7, "eight": 8, "nine": 9},
    },
    {
        "name": "_EMOTIONAL_RESPONSES",
        "source": BACKEND_DIR / "app/services/responses.py",
        "desc": "emotion-specific responses",
        "patterns": [
            r"(\d+) emotion-specific (?:static )?responses?",
        ],
    },
    {
        "name": "SERVICE_ROUTES",
        "source": BACKEND_DIR / "app/services/semantic_routes.py",
        "desc": "semantic service routes",
        "patterns": [
            r"all (\d+) service routes",
            r"(\d+) service routes(?:[^s]|$)",  # not "service route scores"
        ],
    },
]


# -----------------------------------------------------------------------
# AUTO-FIX HELPER
# -----------------------------------------------------------------------

def _apply_replace(path: Path, old: str, new: str, description: str) -> None:
    content = path.read_text()
    if old in content:
        path.write_text(content.replace(old, new))
        if _issues:
            _issues[-1].fixed = True
        print(f"  FIXED: {rel(path)} — {description}", file=sys.stderr)


# -----------------------------------------------------------------------
# 1. TEST COUNTS — with Gap 2 fix (recursive glob)
# -----------------------------------------------------------------------

def _count_tests_in_file(path: Path) -> int:
    """Count test functions in a file — both module-level and class methods,
    sync and async. AST-based to handle any indentation correctly.

    Note: parametrized tests count once at the `def` level. Actual pytest
    collection may expand them further, so the reported total here is a
    "raw def count" rather than "collected count".
    """
    tree = _parse_py(path)
    if tree is None:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                count += 1
    return count


def check_test_counts(args):
    testing_md_path = DOCS_DIR / "TESTING.md"
    if not testing_md_path.exists():
        return
    testing_md = testing_md_path.read_text()

    # Gap 2 fix: rglob finds tests/unit/ and tests/integration/.
    # AST-based counting (fixed from `^def test_` regex which missed
    # class-method tests like those in test_semantic_router.py's
    # TestServiceRoutes class).
    actual_counts: dict[str, int] = {}
    total_actual = 0
    for f in sorted(TESTS_DIR.rglob("test_*.py")):
        if "__pycache__" in f.parts:
            continue
        count = _count_tests_in_file(f)
        actual_counts[f.name] = count
        total_actual += count

    # Total-count claim
    total_match = re.search(r"(\d+) tests across", testing_md)
    if total_match:
        doc_total = int(total_match.group(1))
        # Allow ±5% tolerance for "approximately N" claims
        tolerance = max(5, total_actual * 0.05)
        if abs(doc_total - total_actual) > tolerance:
            warn("docs/TESTING.md",
                 f"says {doc_total} total tests, actual {total_actual}",
                 category="test-count")
            if args.fix:
                _apply_replace(testing_md_path,
                               f"{doc_total} tests across",
                               f"{total_actual} tests across",
                               f"total count {doc_total} → {total_actual}")

    # Per-file counts like "### `test_chatbot.py` — 47 tests"
    for match in re.finditer(r"### `(test_\w+\.py)` — (\d+) tests", testing_md):
        name, doc_count = match.group(1), int(match.group(2))
        if name in actual_counts and actual_counts[name] != doc_count:
            warn("docs/TESTING.md",
                 f"{name}: says {doc_count}, actual {actual_counts[name]}",
                 category="test-count")
            if args.fix:
                _apply_replace(testing_md_path,
                               f"### `{name}` — {doc_count} tests",
                               f"### `{name}` — {actual_counts[name]} tests",
                               f"{name}: {doc_count} → {actual_counts[name]}")

    # README.md cross-reference
    readme_path = ROOT / "README.md"
    if readme_path.exists():
        readme = readme_path.read_text()
        rm = re.search(r"(\d+) unit tests", readme)
        if rm:
            rc = int(rm.group(1))
            if rc != total_actual and abs(rc - total_actual) > 5:
                warn("README.md",
                     f"says {rc} unit tests, actual {total_actual}",
                     category="test-count")


# -----------------------------------------------------------------------
# 2. MODEL IDs
# -----------------------------------------------------------------------

def check_model_ids(args):
    client_path = BACKEND_DIR / "app/llm/claude_client.py"
    models = extract_string_constants(client_path, suffix="_MODEL")
    if not models:
        return

    for md_file in ["README.md", "docs/DEPLOY.md",
                    "docs/design/CRISIS_DETECTION.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()
        if file_is_ignored(content):
            continue

        for model_id in models.values():
            family = model_id.rsplit("-", 1)[0]
            for found in re.findall(rf"{re.escape(family)}[\w-]*", content):
                if found != model_id and found + ")" not in content:
                    warn(md_file,
                         f"has model ID '{found}', code uses '{model_id}'",
                         category="model-id", severity="error",
                         suggested_fix=f"Replace with `{model_id}`")


# -----------------------------------------------------------------------
# 3. ENV VARS
# -----------------------------------------------------------------------

def check_env_vars(args):
    render_path = ROOT / "render.yaml"
    if not render_path.exists():
        return
    render_src = render_path.read_text()
    render_vars = set(re.findall(r"key:\s+(\w+)", render_src))

    deploy_path = DOCS_DIR / "DEPLOY.md"
    if deploy_path.exists():
        deploy_src = deploy_path.read_text()
        if not file_is_ignored(deploy_src):
            doc_vars = set(re.findall(r"\| `(\w+(?:_\w+)+)` \|", deploy_src))
            for var in doc_vars:
                if var in render_vars or var == "CHAT_BACKEND_URL":
                    continue
                try:
                    idx = deploy_src.index(var)
                except ValueError:
                    continue
                context = deploy_src[max(0, idx - 200):idx].lower()
                if "frontend" not in context:
                    warn("docs/DEPLOY.md",
                         f"documents `{var}` but it's not in render.yaml",
                         category="env-var")

    for md_file in ["docs/SETUP.md", "docs/DEPLOY.md", "README.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()
        if file_is_ignored(content):
            continue
        if "GEMINI_API_KEY" in content or "GEMINI_MODEL" in content:
            warn(md_file, "still references GEMINI env vars (removed)",
                 category="env-var", severity="error")


# -----------------------------------------------------------------------
# 4. FULL-PATH FILE REFERENCES
# -----------------------------------------------------------------------

def check_file_references(args):
    """Check that files mentioned in docs actually exist, with line numbers
    and did-you-mean suggestions for handler-directory refactors."""
    global _scanned_refs

    full_path_re = re.compile(
        r"`((?:backend|tests|frontend-next|frontend|scripts|docs)"
        r"[\w/.-]+\.(?:py|tsx?|md|json|geojson|yaml|yml))`")

    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        for i, line in enumerate(content.split("\n"), start=1):
            if i in ignored:
                continue
            for match in full_path_re.finditer(line):
                _scanned_refs += 1
                ref = match.group(1)
                if (ROOT / ref).exists():
                    continue
                suggested = _suggest_directory_for(ref)
                warn(rel(md_file),
                     f"references `{ref}` which doesn't exist",
                     category="file-ref", line=i,
                     suggested_fix=suggested)


def _suggest_directory_for(ref: str) -> Optional[str]:
    """Handler-directory awareness — suggest the `foo/` package when
    `foo.py` doesn't exist but `foo/` does."""
    if not ref.endswith(".py"):
        return None
    without_ext = ROOT / ref[:-3]
    if without_ext.is_dir():
        return f"Did you mean the package `{ref[:-3]}/`? (Phase 3 decomposition)"
    return None


# -----------------------------------------------------------------------
# 5. BARE FILENAMES — Gap 1
# -----------------------------------------------------------------------

def check_bare_filenames(args):
    """Catch backticked bare filenames like `chatbot.py` (no path prefix)
    that don't exist anywhere in backend/, tests/, or scripts/."""
    global _scanned_refs

    # Build inventory of actual Python file basenames
    inventory: set[str] = set()
    for base in (BACKEND_DIR, TESTS_DIR, SCRIPTS_DIR):
        if base.exists():
            for p in base.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                inventory.add(p.name)

    # Also build inventory of same-stem directories (for handler-dir awareness)
    package_stems: set[str] = set()
    if BACKEND_DIR.exists():
        for p in BACKEND_DIR.rglob("*"):
            if p.is_dir() and "__pycache__" not in p.parts:
                package_stems.add(p.name)

    bare_re = re.compile(r"`([A-Za-z_][\w-]*\.py)`")

    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        for i, line in enumerate(content.split("\n"), start=1):
            if i in ignored:
                continue
            for match in bare_re.finditer(line):
                _scanned_refs += 1
                name = match.group(1)
                stem = name[:-3]
                if name in inventory:
                    continue  # file exists somewhere — good

                if stem in package_stems:
                    warn(rel(md_file),
                         f"bare ref `{name}` — file doesn't exist but "
                         f"`{stem}/` package does",
                         category="bare-filename", line=i,
                         suggested_fix=f"Clarify as `{stem}/` package")
                else:
                    warn(rel(md_file),
                         f"bare ref `{name}` — no such file in "
                         f"backend/, tests/, or scripts/",
                         category="bare-filename", line=i)


# -----------------------------------------------------------------------
# 6. INTERNAL LINKS
# -----------------------------------------------------------------------

def check_internal_links(args):
    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        for i, line in enumerate(content.split("\n"), start=1):
            if i in ignored:
                continue
            for match in re.finditer(r'\[([^\]]*)\]\((?!http)([^)]+)\)', line):
                link_text, target = match.group(1), match.group(2)
                file_part = target.split("#")[0]
                if not file_part:
                    continue
                resolved = (md_file.parent / file_part).resolve()
                if not resolved.exists():
                    warn(rel(md_file),
                         f"broken link [{link_text}]({target})",
                         category="broken-link", severity="error", line=i)


# -----------------------------------------------------------------------
# 7. ANCHOR LINE-NUMBER REFS
# -----------------------------------------------------------------------

def check_line_anchor_refs(args):
    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)
        for i, line in enumerate(content.split("\n"), start=1):
            if i in ignored:
                continue
            if re.search(r'\[([^\]]*)\]\(([^)]*#L\d+[^)]*)\)', line):
                warn(rel(md_file),
                     "link with #L anchor — breaks on any code edit; "
                     "use function names or section headers instead",
                     category="line-anchor", line=i)


# -----------------------------------------------------------------------
# 8. PROSE LINE-NUMBER REFS — Gap 5
# -----------------------------------------------------------------------

def check_prose_line_numbers(args):
    """Flag plain-text line number references like '~line 940' or 'L1153'
    in prose — drifts every time the target file is edited."""
    patterns = [
        re.compile(r'\b(?:at|around|~|approximately|near)\s+line\s+\d+\b',
                   re.IGNORECASE),
        re.compile(r'\bline\s+\d{3,}\b(?!\s*number)', re.IGNORECASE),
        re.compile(r'\bL\d{3,}\b(?!\-)'),  # L940 but not L940-L950 range
    ]

    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        in_code_block = False
        for i, line in enumerate(content.split("\n"), start=1):
            # Track code-block state so we don't flag "line 940" inside
            # a code example
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or i in ignored:
                continue
            # Skip lines that are already anchor-link refs (caught elsewhere)
            if re.search(r'#L\d+', line):
                continue
            # Skip lines that look like table separators or headers
            if line.strip().startswith(("|", "#")):
                continue
            for pat in patterns:
                if pat.search(line):
                    warn(rel(md_file),
                         "prose line-number reference — drifts every time "
                         "the target file is edited",
                         category="prose-line-num", line=i,
                         suggested_fix="Name the function/constant instead")
                    break


# -----------------------------------------------------------------------
# 9. CODE-BLOCK COMMAND REFS
# -----------------------------------------------------------------------

def check_code_block_commands(args):
    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        for m in re.finditer(r'```(?:bash|sh|shell)?\n(.*?)```',
                             content, re.DOTALL):
            block_start_line = content[:m.start()].count("\n") + 1
            block = m.group(1)
            for cmd in re.finditer(r'(?:python|pytest)\s+([\w/.-]+\.py)',
                                   block):
                script = cmd.group(1)
                offset = block[:cmd.start()].count("\n")
                abs_line = block_start_line + offset + 1

                if (ROOT / script).exists():
                    continue
                found = any((ROOT / p / script).exists()
                            for p in ("tests/", "scripts/", "backend/",
                                      "tests/unit/", "tests/integration/"))
                if not found:
                    warn(rel(md_file),
                         f"code block references `{script}` which "
                         f"doesn't exist",
                         category="code-block", line=abs_line)


# -----------------------------------------------------------------------
# 10. API ROUTES (deferred — prefix-mount ambiguity)
# -----------------------------------------------------------------------

def check_api_routes(args):
    """Placeholder — this project's prefix-mount routing makes exact
    matching brittle. Re-enable once routes are canonicalized."""
    pass


# -----------------------------------------------------------------------
# 11. DEPENDENCY VERSIONS
# -----------------------------------------------------------------------

def check_dependency_refs(args):
    render_path = ROOT / "render.yaml"
    if not render_path.exists():
        return
    render_src = render_path.read_text()

    versions: dict[str, str] = {}
    for m in re.finditer(
            r'key:\s+(\w+VERSION\w*)\s+value:\s+"?([^"\n]+)"?', render_src):
        versions[m.group(1)] = m.group(2).strip()

    for md_file in ["docs/SETUP.md", "docs/DEPLOY.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()
        if file_is_ignored(content):
            continue
        for var, version in versions.items():
            for m in re.finditer(rf'{var}.*?(\d+\.\d+[\.\d]*)', content):
                doc_version = m.group(1)
                if doc_version != version and doc_version not in version:
                    warn(md_file,
                         f"`{var}` is {version} in render.yaml but "
                         f"{doc_version} in docs",
                         category="dep-version")


# -----------------------------------------------------------------------
# 12. NUMERIC CONSTANTS IN PROSE — Gap 3
# -----------------------------------------------------------------------

def check_numeric_constants(args):
    """Cross-reference doc claims like "displays the first 10" against the
    actual int constants in Python source."""
    for entry in NUMERIC_CONSTANTS:
        actual = extract_int_constant(entry["source"], entry["name"])
        if actual is None:
            continue

        for md_file in all_md_files():
            content = md_file.read_text()
            if file_is_ignored(content):
                continue
            ignored = ignored_lines(content)

            for i, line in enumerate(content.split("\n"), start=1):
                if i in ignored:
                    continue
                for pat in entry["patterns"]:
                    m = re.search(pat, line, re.IGNORECASE)
                    if not m:
                        continue
                    claimed = int(m.group(1))
                    if claimed != actual:
                        warn(rel(md_file),
                             f"claims \"{m.group(0)}\" but {entry['name']}"
                             f" = {actual} in {rel(entry['source'])}",
                             category="numeric-const", line=i,
                             suggested_fix=f"Update number to {actual}")


# -----------------------------------------------------------------------
# 13. CATEGORY / FEATURE COUNTS — Gap 4
# -----------------------------------------------------------------------

def check_category_counts(args):
    """Cross-reference doc claims like "7 crisis categories" against
    len(_CRISIS_CATEGORIES) in the source."""
    for entry in CATEGORY_COUNTS:
        actual = extract_collection_len(entry["source"], entry["name"])
        if actual is None:
            continue
        word_nums = entry.get("word_numbers", {})

        for md_file in all_md_files():
            content = md_file.read_text()
            if file_is_ignored(content):
                continue
            ignored = ignored_lines(content)

            for i, line in enumerate(content.split("\n"), start=1):
                if i in ignored:
                    continue
                for pat in entry["patterns"]:
                    m = re.search(pat, line)
                    if not m:
                        continue
                    captured = m.group(1)
                    if captured.isdigit():
                        claimed = int(captured)
                    elif captured in word_nums:
                        claimed = word_nums[captured]
                    else:
                        continue
                    if claimed != actual:
                        warn(rel(md_file),
                             f"claims \"{m.group(0)}\" but "
                             f"len({entry['name']}) = {actual} in "
                             f"{rel(entry['source'])}",
                             category="category-count", line=i,
                             suggested_fix=f"Update to {actual} "
                                           f"{entry['desc']}")


# -----------------------------------------------------------------------
# 14. CROSS-DOC CONTRADICTIONS (new)
# -----------------------------------------------------------------------

def check_cross_doc_contradictions(args):
    """Flag when the same named thing has different counts in different docs.

    Catches drift even when source-of-truth is deleted or unavailable — any
    disagreement between docs about e.g. crisis-category count is suspicious.
    """
    for entry in CATEGORY_COUNTS:
        topic = entry["desc"]
        word_nums = entry.get("word_numbers", {})
        occurrences: list[tuple[str, int, int, str]] = []
        # (file, line, value, matched_text)

        for md_file in all_md_files():
            content = md_file.read_text()
            if file_is_ignored(content):
                continue
            ignored = ignored_lines(content)
            for i, line in enumerate(content.split("\n"), start=1):
                if i in ignored:
                    continue  # respect line-level drift:ignore markers
                for pat in entry["patterns"]:
                    m = re.search(pat, line)
                    if not m:
                        continue
                    captured = m.group(1)
                    if captured.isdigit():
                        value = int(captured)
                    elif captured in word_nums:
                        value = word_nums[captured]
                    else:
                        continue
                    occurrences.append((rel(md_file), i, value, m.group(0)))

        if not occurrences:
            continue
        distinct_values = {v for _, _, v, _ in occurrences}
        if len(distinct_values) <= 1:
            continue

        detail = ", ".join(f"{f}:{line} says {v}"
                           for f, line, v, _ in sorted(occurrences)[:5])
        warn("(multiple)",
             f"contradictory counts for {topic}: values "
             f"{sorted(distinct_values)} — {detail}",
             category="contradiction")


# -----------------------------------------------------------------------
# 15. DEPRECATED-PATTERN DETECTION (new)
# -----------------------------------------------------------------------

_DEPRECATED_PATTERNS = [
    (re.compile(r'\bLINK-TO-OUR-REPO\b'),
     "placeholder `LINK-TO-OUR-REPO` was never filled in"),
    (re.compile(r'\bTODO(?::|\s+\w)', re.IGNORECASE),
     "unresolved TODO in docs"),
    (re.compile(r'\bFIXME(?::|\s+\w)', re.IGNORECASE),
     "unresolved FIXME in docs"),
    (re.compile(r'\bXXX:'),
     "XXX marker in docs"),
]


def check_deprecated_patterns(args):
    """Flag placeholders and unresolved markers."""
    for md_file in all_md_files():
        content = md_file.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        in_code_block = False
        for i, line in enumerate(content.split("\n"), start=1):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or i in ignored:
                continue
            for pat, msg in _DEPRECATED_PATTERNS:
                if pat.search(line):
                    warn(rel(md_file), msg,
                         category="deprecated-pattern", severity="info",
                         line=i)
                    break


# -----------------------------------------------------------------------
# 16. AUDIT-BASELINE DRIFT (new)
# -----------------------------------------------------------------------
# Cross-checks three sources that must stay in sync around the test-
# quality audit tool:
#
#   1. tests/_tools/audit_tests.py     — the code (what we actually
#                                        emit today)
#   2. tests/_tools/audit_baseline.txt — accepted floor (what the CI
#                                        gate compares against)
#   3. TEST_INFRASTRUCTURE.md + tests/README.md — what the docs claim
#
# Failure modes this catches:
#
#   a) Baseline counts disagree with a fresh audit run. The CI gate
#      (check_audit_baseline.py) would also catch this, but surfacing
#      it in the drift checker means developers see it locally before
#      pushing — and the error message points at the specific category
#      that moved.
#
#   b) A category ID (D1, D2, ...) is documented in the audit_tests.py
#      docstring but never emitted by report.add(). We hit this once
#      with D3 and D10 — described in the module docstring but the
#      code path was removed without updating the text.
#
#   c) Docs mention a category ID that no longer exists. If someone
#      deletes the D9 check in the source, TEST_INFRASTRUCTURE.md
#      shouldn't still list it.

_AUDIT_TOOL_PATH = TESTS_DIR / "_tools" / "audit_tests.py"
_AUDIT_BASELINE_PATH = TESTS_DIR / "_tools" / "audit_baseline.txt"

# Markdown docs that describe the audit categories. Only flag mismatches
# against files that are expected to reference them.
_AUDIT_DOC_FILES = [
    ROOT / "TEST_INFRASTRUCTURE.md",
    TESTS_DIR / "README.md",
]


def _parse_audit_baseline(path: Path) -> dict[str, int]:
    """Parse 'D5: 8' / 'TOTAL: 10' lines out of audit_baseline.txt.

    Ignores blank lines and comments. Returns {category: count}.
    The file format is documented in the baseline file's own header.
    """
    result: dict[str, int] = {}
    line_re = re.compile(r"^\s*(D\d+|TOTAL)\s*:\s*(\d+)\s*$",
                         re.IGNORECASE)
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = line_re.match(raw)
        if m:
            result[m.group(1).upper()] = int(m.group(2))
    return result


def _parse_docstring_audit_categories(audit_src: str) -> set[str]:
    """Find category IDs (D1, D2, ...) named in the module docstring.

    Matches lines like ' D1. Dead mock targets — ...' so docstring prose
    that happens to contain 'D10k' or 'D2D' doesn't trigger a match.
    """
    mod = ast.parse(audit_src)
    doc = ast.get_docstring(mod) or ""
    return set(re.findall(r"\bD\d+(?=\.|\s)", doc))


def _parse_emitted_audit_categories(audit_src: str) -> set[str]:
    """Find category IDs actually passed to report.add() in the source.

    Conservative literal-string match — we're not evaluating Python;
    just parsing the AST and collecting the first argument when it's
    a string constant matching 'D\\d+'.
    """
    emitted: set[str] = set()
    tree = ast.parse(audit_src)
    cat_re = re.compile(r"^D\d+$")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Look for  report.add("D5", ...)  — attribute call with a
        # string literal first arg.
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if cat_re.match(first.value):
                emitted.add(first.value)
    return emitted


def _run_audit_in_process() -> Optional[Counter[str]]:
    """Run the audit tool without shelling out. Returns category counts.

    This keeps check_docs.py's "no subprocess" style — we import the
    audit module, build a Report in-memory, and call each audit_d*
    function directly.

    Returns None if the audit tool isn't importable for any reason
    (moved, renamed, missing deps) — in that case we skip the runtime
    comparison but still do the static checks.
    """
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location(
        "audit_tests_for_drift_check", _AUDIT_TOOL_PATH)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module — @dataclass looks up
    # cls.__module__ in sys.modules during class construction, and
    # silently fails with an opaque AttributeError if the module
    # isn't registered. This is a standard importlib idiom that trips
    # people up the first time.
    _sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        _sys.modules.pop(spec.name, None)
        # Surface the specific failure — swallowing it silently hid a
        # sys.modules registration bug for hours during development.
        warn(rel(_AUDIT_TOOL_PATH),
             f"could not import for in-process run: "
             f"{type(e).__name__}: {e}",
             category="audit-baseline", severity="warning")
        return None

    try:
        # The audit tool exposes collect_test_files(),
        # _build_backend_symbol_map(), and a Report class. We call the
        # same functions main() calls so the comparison is against
        # real audit output, not a re-implementation that can diverge.
        test_files = mod.collect_test_files()
        symbol_map = mod._build_backend_symbol_map()
        report = mod.Report()
        mod.audit_d1_dead_patches(report, test_files, symbol_map)
        mod.audit_d2_no_assertions(report, test_files)
        mod.audit_d3_mock_only(report, test_files)
        mod.audit_d4_mock_only(report, test_files)
        mod.audit_d5_env_dependent(report, test_files)
        mod.audit_d6_admin_without_auth(report, test_files)
        mod.audit_d7_reexport_patches(report, test_files)
        mod.audit_d8_d9_timing(report, test_files)
        return report.counter()
    except Exception as e:  # noqa: BLE001
        warn(rel(_AUDIT_TOOL_PATH),
             f"in-process run failed: {type(e).__name__}: {e}",
             category="audit-baseline", severity="warning")
        return None
    finally:
        _sys.modules.pop(spec.name, None)


# Matches "D5 findings", "D5: 7", "D5=7", "(D5=8, D8=1, D9=1, TOTAL=10)".
# Captures the category (group 1) and optionally the count (group 2).
_DOC_CATEGORY_REF_RE = re.compile(
    r"\b(D\d+)\b\s*[:=]?\s*(\d+)?",
)


def check_audit_baseline(args):
    """Validate audit_baseline.txt, audit_tests.py, and their docs agree.

    Three sub-checks (see section header for rationale):
      (a) baseline counts vs live audit counts
      (b) docstring mentions vs actually-emitted categories
      (c) doc mentions (TEST_INFRASTRUCTURE.md, tests/README.md) vs
          emitted categories
    """

    # All three sub-checks need the audit source. If it's missing
    # outright, that's its own error — someone moved or deleted the
    # file without updating docs.
    if not _AUDIT_TOOL_PATH.exists():
        warn(rel(_AUDIT_TOOL_PATH.parent),
             f"{_AUDIT_TOOL_PATH.name} not found — audit-baseline check "
             "cannot run",
             category="audit-baseline", severity="warning")
        return

    audit_src = _AUDIT_TOOL_PATH.read_text()
    emitted = _parse_emitted_audit_categories(audit_src)

    # ---------- (b) docstring drift inside audit_tests.py itself -----
    documented = _parse_docstring_audit_categories(audit_src)
    phantom = documented - emitted
    if phantom:
        warn(rel(_AUDIT_TOOL_PATH),
             f"docstring mentions {sorted(phantom)} but report.add() "
             f"never emits those categories — remove from docstring or "
             f"implement the check",
             category="audit-baseline", severity="warning",
             suggested_fix="Update the docstring at the top of "
                           "audit_tests.py to match the emitted "
                           "categories, or add the missing audit "
                           "function.")

    undocumented = emitted - documented
    if undocumented:
        warn(rel(_AUDIT_TOOL_PATH),
             f"report.add() emits {sorted(undocumented)} but they're "
             f"not listed in the module docstring",
             category="audit-baseline", severity="info")

    # ---------- (a) baseline counts vs live audit counts -------------
    if _AUDIT_BASELINE_PATH.exists():
        try:
            baseline = _parse_audit_baseline(_AUDIT_BASELINE_PATH)
        except Exception as e:  # noqa: BLE001
            warn(rel(_AUDIT_BASELINE_PATH),
                 f"could not parse baseline counts: {e}",
                 category="audit-baseline", severity="error")
            baseline = {}

        live = _run_audit_in_process()
        if live is not None and baseline:
            # Compare per-category, not just TOTAL — a silent swap
            # (D5 drops by 1, D9 rises by 1) would net zero on TOTAL.
            tracked = {k for k in baseline if k != "TOTAL"} | set(live)
            for cat in sorted(tracked):
                baseline_n = baseline.get(cat, 0)
                live_n = live.get(cat, 0)
                if baseline_n == live_n:
                    continue
                severity = "warning" if live_n > baseline_n else "info"
                direction = "above" if live_n > baseline_n else "below"
                note = ("run `make audit-baseline` after confirming the "
                        "new findings are deliberate"
                        if live_n > baseline_n
                        else "run `make audit-baseline` to ratchet the "
                             "floor down and lock in the win")
                warn(rel(_AUDIT_BASELINE_PATH),
                     f"{cat}: baseline says {baseline_n}, live audit "
                     f"reports {live_n} ({direction} baseline)",
                     category="audit-baseline", severity=severity,
                     suggested_fix=note)

            # And check TOTAL separately — useful signal even if
            # categories agree (they shouldn't if TOTAL doesn't).
            if "TOTAL" in baseline:
                live_total = sum(live.values())
                if baseline["TOTAL"] != live_total:
                    warn(rel(_AUDIT_BASELINE_PATH),
                         f"TOTAL: baseline says {baseline['TOTAL']}, "
                         f"live audit reports {live_total}",
                         category="audit-baseline", severity="warning")
    else:
        # No baseline file yet — recommend creating one, don't fail.
        warn(rel(TESTS_DIR / "_tools"),
             f"{_AUDIT_BASELINE_PATH.name} not found — generate with "
             "`make audit-baseline`",
             category="audit-baseline", severity="info")

    # ---------- (c) markdown docs vs emitted categories --------------
    # Scan the docs that explicitly describe audit categories. We only
    # flag IDs that clearly mean an audit category: uppercase D followed
    # by digits, appearing near words like "category" / "findings" or
    # inside a known table. We do NOT scan every markdown file — many
    # random docs use D1/D2 for unrelated reasons (diagrams, grades).
    for md_path in _AUDIT_DOC_FILES:
        if not md_path.exists():
            continue
        content = md_path.read_text()
        if file_is_ignored(content):
            continue
        ignored = ignored_lines(content)

        seen_phantom_in_file: set[str] = set()
        for i, line in enumerate(content.split("\n"), start=1):
            if i in ignored:
                continue
            # Gate on context words to avoid grading scales, diagram
            # labels, etc. being mistaken for audit categories.
            if not re.search(
                r"audit|categor|finding|anti-pattern|baseline|patch where",
                line, re.IGNORECASE,
            ):
                continue
            for m in _DOC_CATEGORY_REF_RE.finditer(line):
                cat = m.group(1)
                if cat in emitted:
                    continue
                # Deduplicate — if D99 appears on 6 lines of a file,
                # one warning is enough.
                key = f"{md_path}:{cat}"
                if key in seen_phantom_in_file:
                    continue
                seen_phantom_in_file.add(key)
                warn(rel(md_path),
                     f"mentions `{cat}` but audit_tests.py does not "
                     f"emit that category",
                     category="audit-baseline", severity="warning",
                     line=i,
                     suggested_fix=f"Remove the reference to {cat}, "
                                   f"or add {cat} to audit_tests.py "
                                   f"if the check was intended.")


# -----------------------------------------------------------------------
# OUTPUT FORMATTERS
# -----------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty()


def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _severity_label(sev: str) -> str:
    color = {"error": "31", "warning": "33", "info": "36"}.get(sev, "")
    return _color(sev.upper(), color)


def render_text(issues: list[Issue]) -> None:
    if not issues:
        print("\nNo drift detected.")
        return
    by_category: dict[str, list[Issue]] = {}
    for issue in issues:
        by_category.setdefault(issue.category, []).append(issue)
    for category, items in sorted(by_category.items()):
        print(f"\n--- {category} ({len(items)}) ---")
        for issue in items:
            loc = f"{issue.file}:{issue.line}" if issue.line else issue.file
            marker = _color("FIXED", "32") if issue.fixed \
                else _severity_label(issue.severity)
            print(f"  {marker}  {loc}  {issue.message}")
            if issue.suggested_fix:
                print(f"         → {issue.suggested_fix}")


def render_json(issues: list[Issue]) -> None:
    print(json.dumps({
        "scanned_files": _scanned_files,
        "scanned_refs": _scanned_refs,
        "issues": [asdict(i) for i in issues],
    }, indent=2))


def render_github(issues: list[Issue]) -> None:
    """GitHub Actions annotation format — surfaces inline in PRs."""
    for issue in issues:
        level = {"error": "error", "warning": "warning",
                 "info": "notice"}.get(issue.severity, "warning")
        loc = f"file={issue.file}"
        if issue.line:
            loc += f",line={issue.line}"
        msg = issue.message
        if issue.suggested_fix:
            msg += f" (suggestion: {issue.suggested_fix})"
        print(f"::{level} {loc}::[{issue.category}] {msg}")


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------

CHECKS = [
    ("test-counts",         check_test_counts),
    ("model-ids",           check_model_ids),
    ("env-vars",            check_env_vars),
    ("file-refs",           check_file_references),
    ("bare-filenames",      check_bare_filenames),
    ("internal-links",      check_internal_links),
    ("line-anchors",        check_line_anchor_refs),
    ("prose-line-nums",     check_prose_line_numbers),
    ("code-blocks",         check_code_block_commands),
    ("api-routes",          check_api_routes),
    ("dep-versions",        check_dependency_refs),
    ("numeric-constants",   check_numeric_constants),
    ("category-counts",     check_category_counts),
    ("cross-doc",           check_cross_doc_contradictions),
    ("deprecated-patterns", check_deprecated_patterns),
    ("audit-baseline",      check_audit_baseline),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Docs drift checker — validate docs against code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--fix", action="store_true",
                   help="Auto-fix safe issues (test counts only)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON for CI integration")
    p.add_argument("--github", action="store_true",
                   help="Emit GitHub Actions annotations")
    p.add_argument("--severity", choices=["error", "warning", "info"],
                   default="warning",
                   help="Exit nonzero on issues AT OR ABOVE this level "
                        "(default: warning)")
    p.add_argument("--skip", default="",
                   help=f"Comma-separated check names to skip. "
                        f"Available: {', '.join(n for n, _ in CHECKS)}")
    return p.parse_args()


def main() -> int:
    global _scanned_files
    args = parse_args()

    skipped = {s.strip() for s in args.skip.split(",") if s.strip()}
    to_run = [(n, fn) for n, fn in CHECKS if n not in skipped]

    if not args.json and not args.github:
        print(f"Checking docs against codebase ({len(to_run)} checks)...",
              file=sys.stderr)

    _scanned_files = len(all_md_files())

    for name, fn in to_run:
        try:
            fn(args)
        except Exception as e:  # noqa: BLE001
            warn("(checker)", f"check '{name}' crashed: {e}",
                 category="checker-bug", severity="error")

    if args.json:
        render_json(_issues)
    elif args.github:
        render_github(_issues)
    else:
        render_text(_issues)
        # Summary for humans
        n_err = sum(1 for i in _issues if i.severity == "error" and not i.fixed)
        n_warn = sum(1 for i in _issues if i.severity == "warning" and not i.fixed)
        n_info = sum(1 for i in _issues if i.severity == "info" and not i.fixed)
        n_fixed = sum(1 for i in _issues if i.fixed)
        print(f"\n{'=' * 60}")
        print(f"Scanned {_scanned_files} markdown files, {_scanned_refs} "
              f"file refs, {len(to_run)} checks")
        parts = []
        if n_err:
            parts.append(f"{_color(str(n_err), '31')} error(s)")
        if n_warn:
            parts.append(f"{_color(str(n_warn), '33')} warning(s)")
        if n_info:
            parts.append(f"{_color(str(n_info), '36')} info")
        if n_fixed:
            parts.append(f"{_color(str(n_fixed), '32')} auto-fixed")
        print("  " + (", ".join(parts) if parts else "no drift detected"))
        print("=" * 60)

    # Exit code: nonzero when issues at or above `--severity` threshold
    threshold = SEVERITY_ORDER[args.severity]
    has_blocker = any(
        SEVERITY_ORDER.get(i.severity, 0) >= threshold and not i.fixed
        for i in _issues)
    return 1 if has_blocker else 0


if __name__ == "__main__":
    sys.exit(main())
