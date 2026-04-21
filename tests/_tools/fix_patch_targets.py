"""One-shot codemod: rewrite dead patch targets to live ones.

Run from the repo root:
    python3 tests/_tools/fix_patch_targets.py

Transformations (conservative — only hits strings inside patch() calls):

    "app.services.chatbot.claude_reply"   → "app.services.chatbot.handlers.meta.claude_reply"
    "app.services.chatbot.detect_crisis"  → "app.services.chatbot.orchestrator.detect_crisis"
    "app.services.chatbot._USE_LLM"       → "app.services.chatbot.orchestrator._USE_LLM"

Why these targets:
- claude_reply: handlers/meta.py is where the bot-capability handler
  does the LLM call (the primary service-flow invocation). Fallback
  conversational responses go through responses.py — tests that need
  THAT should patch app.services.responses.claude_reply separately;
  this codemod targets the service-flow case which is the majority.
- detect_crisis: orchestrator.py is the primary dispatch call site,
  but classifier.py ALSO has its own import-time binding and calls
  detect_crisis inside _classify_tone. Tests that route through
  assert_classified() hit the classifier path, not the orchestrator
  path, and need a separate patch.
- _USE_LLM: bound in four submodules at import time. Orchestrator
  gates early extraction; pipeline gates classification. Starting
  with orchestrator; pipeline added where tests need it manually.

Dry-run first: prints a diff but doesn't write. Pass --apply to write.

This is not a regex-perfect tool — it does plain string replacement in
string-literal contexts. It WILL miss dynamic patch calls (getattr or
f-strings building the target) but those are rare in this codebase.
After running, re-grep for 'app.services.chatbot.(claude_reply|'
'detect_crisis|_USE_LLM)' to confirm the rewrite was complete.
"""

import argparse
import re
import sys
from pathlib import Path

REWRITES = [
    # Pattern: exact string match inside any quoting context.
    # Key insight: target appears only in patch("...") literals, so we
    # rewrite the literal. No ambiguity with e.g. comments describing
    # the re-export because those are expected to remain.
    (
        r'"app\.services\.chatbot\.claude_reply"',
        '"app.services.chatbot.handlers.meta.claude_reply"',
    ),
    (
        r"'app\.services\.chatbot\.claude_reply'",
        "'app.services.chatbot.handlers.meta.claude_reply'",
    ),
    (
        r'"app\.services\.chatbot\.detect_crisis"',
        '"app.services.chatbot.orchestrator.detect_crisis"',
    ),
    (
        r"'app\.services\.chatbot\.detect_crisis'",
        "'app.services.chatbot.orchestrator.detect_crisis'",
    ),
    (
        r'"app\.services\.chatbot\._USE_LLM"',
        '"app.services.chatbot.orchestrator._USE_LLM"',
    ),
    (
        r"'app\.services\.chatbot\._USE_LLM'",
        "'app.services.chatbot.orchestrator._USE_LLM'",
    ),
]


def apply_to_file(path: Path, apply: bool) -> int:
    """Return number of rewrites in this file."""
    original = path.read_text()
    updated = original
    for pattern, replacement in REWRITES:
        updated = re.sub(pattern, replacement, updated)
    if updated == original:
        return 0
    count = 0
    for pattern, _ in REWRITES:
        count += len(re.findall(pattern, original))
    print(f"{path}: {count} rewrite(s)")
    if apply:
        path.write_text(updated)
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes")
    ap.add_argument("--root", default="tests", help="Root dir to scan")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    total = 0
    files = 0
    for path in sorted(root.rglob("*.py")):
        # Skip caches and our own tool
        parts = set(path.parts)
        if "__pycache__" in parts or "_tools" in parts:
            continue
        n = apply_to_file(path, args.apply)
        if n > 0:
            files += 1
            total += n

    verb = "rewrote" if args.apply else "would rewrite"
    print(f"\n{verb} {total} occurrence(s) across {files} file(s)")
    if not args.apply:
        print("(dry-run — pass --apply to write changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
