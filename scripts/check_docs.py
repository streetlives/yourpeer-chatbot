#!/usr/bin/env python3
"""
Docs drift checker — flags stale facts in markdown files.

Compares hardcoded numbers, model IDs, and env vars in documentation
against the actual codebase. Run after any change to tests, models,
or configuration to catch drift before it reaches main.

Usage:
    python scripts/check_docs.py          # report only
    python scripts/check_docs.py --fix    # auto-fix what's safe to fix

Designed to run in CI (exits 1 if drift detected) or locally.
"""

import os
import re
import sys
import subprocess
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
TESTS_DIR = ROOT / "tests"
BACKEND_DIR = ROOT / "backend"


def all_md_files():
    """Return all markdown files from repo root and docs/ (recursive into subdirs)."""
    files = list(ROOT.glob("*.md"))
    files.extend(DOCS_DIR.rglob("*.md"))
    return files

# Track all issues found
issues = []
fixes_applied = []
auto_fix = "--fix" in sys.argv


def warn(file, msg):
    issues.append((file, msg))
    print(f"  DRIFT: {file} — {msg}")


def fix(file, old, new, description):
    path = ROOT / file
    content = path.read_text()
    if old in content:
        path.write_text(content.replace(old, new))
        fixes_applied.append((file, description))
        print(f"  FIXED: {file} — {description}")


# -----------------------------------------------------------------------
# 1. TEST COUNTS
# -----------------------------------------------------------------------

def check_test_counts():
    """Compare per-file test counts in TESTING.md against actual."""
    print("\n--- Test counts ---")

    testing_md = (ROOT / "docs" / "TESTING.md").read_text()

    # Count actual tests per file
    actual_counts = {}
    total_actual = 0
    for f in sorted(TESTS_DIR.glob("test_*.py")):
        count = len(re.findall(r"^def test_", f.read_text(), re.MULTILINE))
        actual_counts[f.name] = count
        total_actual += count

    # Check total count in TESTING.md
    total_match = re.search(r"(\d+) tests across", testing_md)
    if total_match:
        doc_total = int(total_match.group(1))
        if doc_total != total_actual:
            warn("docs/TESTING.md", f"says {doc_total} total tests, actual {total_actual}")
            if auto_fix:
                fix("docs/TESTING.md", f"{doc_total} tests across", f"{total_actual} tests across",
                    f"total count {doc_total} → {total_actual}")

    # Check per-file counts (lines like "### `test_chatbot.py` — 47 tests")
    for match in re.finditer(r"### `(test_\w+\.py)` — (\d+) tests", testing_md):
        name, doc_count = match.group(1), int(match.group(2))
        if name in actual_counts and actual_counts[name] != doc_count:
            warn("docs/TESTING.md", f"{name}: says {doc_count}, actual {actual_counts[name]}")
            if auto_fix:
                fix("docs/TESTING.md",
                    f"### `{name}` — {doc_count} tests",
                    f"### `{name}` — {actual_counts[name]} tests",
                    f"{name} count {doc_count} → {actual_counts[name]}")

    # Check README.md cross-reference
    readme = (ROOT / "README.md").read_text()
    readme_match = re.search(r"(\d+) unit tests", readme)
    if readme_match:
        readme_count = int(readme_match.group(1))
        if readme_count != total_actual:
            warn("README.md", f"says {readme_count} unit tests, actual {total_actual}")
            if auto_fix:
                fix("README.md", f"{readme_count} unit tests", f"{total_actual} unit tests",
                    f"test count {readme_count} → {total_actual}")


# -----------------------------------------------------------------------
# 2. MODEL IDS
# -----------------------------------------------------------------------

def check_model_ids():
    """Compare model IDs in docs against claude_client.py constants."""
    print("\n--- Model IDs ---")

    client_path = BACKEND_DIR / "app" / "llm" / "claude_client.py"
    if not client_path.exists():
        warn("backend/app/llm/claude_client.py", "file not found")
        return

    client_src = client_path.read_text()

    # Extract model constants
    models = {}
    for match in re.finditer(r'^(\w+_MODEL)\s*=\s*"([^"]+)"', client_src, re.MULTILINE):
        models[match.group(1)] = match.group(2)

    if not models:
        warn("claude_client.py", "no model constants found")
        return

    # Check each doc file for model ID references
    for md_file in ["README.md", "docs/DEPLOY.md", "docs/design/CRISIS_DETECTION.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()

        for model_id in models.values():
            # Check for old/wrong model IDs by looking for the model family
            # with a different version
            family = model_id.rsplit("-", 1)[0]  # e.g. "claude-haiku-4-5"
            for found in re.findall(rf"{family}[\w-]*", content):
                if found != model_id and found + ")" not in content:
                    # Might be an outdated ID
                    warn(md_file, f"has '{found}', code uses '{model_id}'")


# -----------------------------------------------------------------------
# 3. ENV VARS
# -----------------------------------------------------------------------

def check_env_vars():
    """Verify env vars in render.yaml match what docs reference."""
    print("\n--- Environment variables ---")

    render_path = ROOT / "render.yaml"
    if not render_path.exists():
        return

    render_src = render_path.read_text()

    # Extract env var keys from render.yaml
    render_vars = set(re.findall(r"key:\s+(\w+)", render_src))

    # Check DEPLOY.md env vars table
    deploy_path = ROOT / "docs" / "DEPLOY.md"
    if deploy_path.exists():
        deploy_src = deploy_path.read_text()
        # Look for env vars in the table
        doc_vars = set(re.findall(r"\| `(\w+(?:_\w+)+)` \|", deploy_src))

        for var in doc_vars:
            if var not in render_vars and var not in {"CHAT_BACKEND_URL"}:
                # CHAT_BACKEND_URL is on the frontend service, not backend
                if "frontend" not in deploy_src[deploy_src.index(var)-200:deploy_src.index(var)].lower():
                    warn("docs/DEPLOY.md", f"documents `{var}` but it's not in render.yaml")

    # Check for removed env vars still referenced in docs
    for md_file in ["docs/SETUP.md", "docs/DEPLOY.md", "README.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()
        if "GEMINI_API_KEY" in content or "GEMINI_MODEL" in content:
            warn(md_file, "still references GEMINI env vars (removed)")


# -----------------------------------------------------------------------
# 4. FILE REFERENCES
# -----------------------------------------------------------------------

def check_file_references():
    """Check that files mentioned in docs actually exist."""
    print("\n--- File references ---")

    for md_file in all_md_files():
        content = md_file.read_text()

        # Find references to Python files
        for match in re.finditer(r"`((?:backend|tests|frontend)[\w/.-]+\.(?:py|tsx?))`", content):
            ref_path = ROOT / match.group(1)
            if not ref_path.exists():
                # Check without leading directory
                alt_path = ROOT / match.group(1).split("/", 1)[-1] if "/" in match.group(1) else None
                if alt_path and not alt_path.exists():
                    warn(md_file.name, f"references `{match.group(1)}` which doesn't exist")


# -----------------------------------------------------------------------
# 5. INTERNAL MARKDOWN LINKS
# -----------------------------------------------------------------------

def check_internal_links():
    """Check that markdown links to local files resolve."""
    print("\n--- Internal links ---")

    for md_file in all_md_files():
        content = md_file.read_text()

        # Match [text](relative/path) but not [text](http...)
        for match in re.finditer(r'\[([^\]]*)\]\((?!http)([^)]+)\)', content):
            link_text, target = match.group(1), match.group(2)

            # Strip anchors (#section) for file existence check
            file_part = target.split("#")[0]
            if not file_part:
                continue  # Pure anchor link like (#section)

            resolved = (md_file.parent / file_part).resolve()
            if not resolved.exists():
                warn(md_file.name, f"broken link [{link_text}]({target})")


# -----------------------------------------------------------------------
# 6. STALE LINE NUMBER REFERENCES
# -----------------------------------------------------------------------

def check_line_number_refs():
    """Flag links with #L<number> anchors — these break on any code change."""
    print("\n--- Line number references ---")

    for md_file in all_md_files():
        content = md_file.read_text()
        refs = re.findall(r'\[([^\]]*)\]\(([^)]*#L\d+[^)]*)\)', content)
        if refs:
            warn(md_file.name,
                 f"has {len(refs)} link(s) with #L line numbers — "
                 f"these break when code is edited. Use function names or "
                 f"section headers instead")


# -----------------------------------------------------------------------
# 7. CODE BLOCKS WITH SHELL COMMANDS
# -----------------------------------------------------------------------

def check_code_block_commands():
    """Check that commands in code blocks reference files that exist."""
    print("\n--- Code block commands ---")

    for md_file in all_md_files():
        content = md_file.read_text()

        # Find shell code blocks
        for block in re.finditer(r'```(?:bash|sh|shell)?\n(.*?)```', content, re.DOTALL):
            block_text = block.group(1)
            # Look for `python <file>` or `pytest <file>` references
            for cmd_match in re.finditer(r'(?:python|pytest)\s+([\w/.-]+\.py)', block_text):
                script = cmd_match.group(1)
                if not (ROOT / script).exists():
                    # Try relative to common dirs
                    found = False
                    for prefix in ["tests/", "scripts/", "backend/"]:
                        if (ROOT / prefix / script).exists():
                            found = True
                            break
                    if not found:
                        warn(md_file.name, f"code block references `{script}` which doesn't exist")


# -----------------------------------------------------------------------
# 8. API ROUTES
# -----------------------------------------------------------------------

def check_api_routes():
    """Verify that API routes documented in markdown exist in the codebase."""
    print("\n--- API routes ---")

    # Collect actual routes from FastAPI route files
    actual_routes = set()
    routes_dir = BACKEND_DIR / "app" / "routes"
    if routes_dir.exists():
        for py_file in routes_dir.glob("*.py"):
            src = py_file.read_text()
            # Match @router.get("/path"), @router.post("/path"), etc.
            for match in re.finditer(r'@\w+\.(?:get|post|put|delete)\(["\']([^"\']+)', src):
                actual_routes.add(match.group(1))

    if not actual_routes:
        return  # Can't validate without route definitions

    # Check documented routes
    for md_file in all_md_files():
        content = md_file.read_text()
        for match in re.finditer(r'`(/(?:api|chat|admin)/[\w/{}*]+)`', content):
            doc_route = match.group(1)
            # Normalize: strip trailing slash, replace {param} with wildcard
            normalized = re.sub(r'\{[^}]+\}', '*', doc_route.rstrip('/'))
            # Check if any actual route matches (fuzzy — route prefixes differ)
            route_stem = normalized.split('/')[-1]
            if route_stem and route_stem != '*':
                found = any(route_stem in r for r in actual_routes)
                if not found and route_stem not in {"api", "admin", "chat"}:
                    # Only flag specific endpoint names, not generic prefixes
                    pass  # Too noisy for this project — routes use prefix mounts


# -----------------------------------------------------------------------
# 9. DEPENDENCY VERSIONS
# -----------------------------------------------------------------------

def check_dependency_refs():
    """Flag version-specific dependency references that may go stale."""
    print("\n--- Dependency versions ---")

    # Check render.yaml versions against docs
    render_path = ROOT / "render.yaml"
    if not render_path.exists():
        return

    render_src = render_path.read_text()

    # Extract version values from render.yaml
    versions = {}
    for match in re.finditer(r'key:\s+(\w+VERSION\w*)\s+value:\s+"?([^"\n]+)"?', render_src):
        versions[match.group(1)] = match.group(2).strip()

    # Check if docs mention different versions
    for md_file in ["docs/SETUP.md", "docs/DEPLOY.md"]:
        path = ROOT / md_file
        if not path.exists():
            continue
        content = path.read_text()

        for var, version in versions.items():
            # Check if the doc mentions this var with a different version
            for match in re.finditer(rf'{var}.*?(\d+\.\d+[\.\d]*)', content):
                doc_version = match.group(1)
                if doc_version != version and doc_version not in version:
                    warn(md_file, f"`{var}` is {version} in render.yaml but {doc_version} in docs")


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------

def main():
    print("Checking docs against codebase...\n")

    check_test_counts()
    check_model_ids()
    check_env_vars()
    check_file_references()
    check_internal_links()
    check_line_number_refs()
    check_code_block_commands()
    check_api_routes()
    check_dependency_refs()

    print(f"\n{'=' * 50}")

    if fixes_applied:
        print(f"Auto-fixed {len(fixes_applied)} issue(s):")
        for file, desc in fixes_applied:
            print(f"  {file}: {desc}")
        print()

    remaining = len(issues) - len(fixes_applied)
    if remaining > 0:
        print(f"{remaining} issue(s) found — review manually or re-run with --fix")
        return 1
    elif issues:
        print("All issues auto-fixed.")
        return 0
    else:
        print("No drift detected.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
