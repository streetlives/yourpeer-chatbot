"""CI gate — compare current audit findings against an accepted baseline.

Fails the build if:
  1. Any category's count in the current audit exceeds its baseline count
     (new anti-patterns introduced).
  2. The total count regressed upward.

Does NOT fail if counts DECREASE below the baseline. Improvements are fine;
we don't want a "regression" for cleaning up old findings. But the
baseline file should be regenerated periodically so the floor ratchets
downward over time.

Usage:
    python3 tests/_tools/check_audit_baseline.py \
        --baseline tests/_tools/audit_baseline.txt \
        --current /tmp/audit-current.txt

The "baseline" file is expected to be the shortform produced by
`audit_tests.py --summary`, which looks like:

    D5: 7 findings
    D8: 1 findings
    D9: 1 findings
    TOTAL: 9 findings

or the equivalent `D5: 7` shortform. Lines starting with # are comments.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


_CAT_LINE_RE = re.compile(
    r"^\s*(?P<cat>D\d+|TOTAL):\s*(?P<count>\d+)",
    re.IGNORECASE,
)


def parse(path: Path) -> dict[str, int]:
    """Parse a baseline / current audit summary into {category: count}."""
    out: dict[str, int] = {}
    for line in path.read_text().splitlines():
        # Skip comments and blank lines.
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _CAT_LINE_RE.match(line)
        if m:
            out[m.group("cat").upper()] = int(m.group("count"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--current", required=True, type=Path)
    args = ap.parse_args()

    if not args.baseline.exists():
        print(f"ERROR: baseline file not found: {args.baseline}", file=sys.stderr)
        print("Regenerate with: python3 tests/_tools/audit_tests.py --summary > "
              f"{args.baseline}", file=sys.stderr)
        return 2
    if not args.current.exists():
        print(f"ERROR: current file not found: {args.current}", file=sys.stderr)
        return 2

    baseline = parse(args.baseline)
    current = parse(args.current)
    current = parse(args.current)

    # D3 is advisory — detection produces ~50% false positives (tests
    # that deliberately verify a mock contract look identical to tests
    # that forgot to verify behavior). We run the check to surface the
    # pattern for human review but never gate the build on it.
    baseline.pop("D3", None)
    current.pop("D3", None)

    # TOTAL is a raw `len(findings)` in the audit output, so it still
    # includes the D3 count we just popped. Recompute it here so the
    # TOTAL gate reflects only the categories we actually gate on —
    # otherwise a D3 spike silently fails the build through the TOTAL
    # comparison, defeating the "advisory" intent.
    def _recompute_total(d: dict[str, int]) -> None:
        real = {k: v for k, v in d.items() if k != "TOTAL"}
        if "TOTAL" in d and real:
            d["TOTAL"] = sum(real.values())

    _recompute_total(baseline)
    _recompute_total(current)

    if not baseline:
        print(f"ERROR: no entries parsed from {args.baseline}. Malformed?",
              file=sys.stderr)
        return 2

    # Check each category (and TOTAL) — current count must be <= baseline.
    failures = []
    improvements = []
    all_cats = set(baseline) | set(current)
    for cat in sorted(all_cats):
        b = baseline.get(cat, 0)
        c = current.get(cat, 0)
        if c > b:
            failures.append((cat, b, c))
        elif c < b:
            improvements.append((cat, b, c))

    # Print report
    print("Audit-baseline check")
    print("=" * 50)
    print(f"{'Category':<10} {'Baseline':>10} {'Current':>10} {'Δ':>6}")
    print("-" * 50)
    for cat in sorted(all_cats):
        b = baseline.get(cat, 0)
        c = current.get(cat, 0)
        delta = c - b
        marker = ""
        if delta > 0:
            marker = "  ❌ NEW"
        elif delta < 0:
            marker = "  ✅ better"
        print(f"{cat:<10} {b:>10} {c:>10} {delta:>+6}{marker}")
    print()

    if failures:
        print("❌ FAIL — new audit findings beyond the baseline:", file=sys.stderr)
        for cat, b, c in failures:
            print(f"    {cat}: {b} → {c} (+{c - b})", file=sys.stderr)
        print(file=sys.stderr)
        print("To investigate: python3 tests/_tools/audit_tests.py --category "
              + " --category ".join(cat for cat, _, _ in failures),
              file=sys.stderr)
        print(file=sys.stderr)
        print("If the new findings are intentional and accepted, update the "
              "baseline:", file=sys.stderr)
        print(f"    python3 tests/_tools/audit_tests.py --summary > {args.baseline}",
              file=sys.stderr)
        return 1

    if improvements:
        print("✅ Improvements over baseline:")
        for cat, b, c in improvements:
            print(f"    {cat}: {b} → {c}")
        print()
        print("Consider regenerating the baseline to ratchet the floor:")
        print(f"    python3 tests/_tools/audit_tests.py --summary > {args.baseline}")
        print()

    print("✅ PASS — no new audit findings beyond the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
