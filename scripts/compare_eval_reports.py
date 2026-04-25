#!/usr/bin/env python3
"""Compare two eval reports for Phase 2 of the unified-extractor migration.

Takes two JSON reports produced by `tests/eval/eval_llm_judge.py` — one
with `USE_UNIFIED_EXTRACTOR` off (legacy extract_slots_smart), one with
it on (new slot_extraction.extract). Produces a scenario-by-scenario
diff highlighting:

  1. Scenarios that moved by at least 0.3 on average_score in either
     direction (Phase 2 doc's threshold for "moved" scenarios).
  2. Scenarios that were >= 4.5 on the legacy path and dropped below
     4.2 on the unified path (Phase 2 acceptance criterion).
  3. The 4 set-equality blind-spot watch-list scenarios, always
     reported regardless of whether they crossed a threshold.
  4. `multi_cross_borough` status on both paths (the specific scenario
     the migration exists to fix).
  5. Critical-failure count delta (unified must be <= legacy).
  6. Scenario-count and category mismatches that would invalidate a
     naive comparison.

Exit codes:
  0  — acceptance criteria met OR warnings only
  1  — acceptance criteria violated (hard fail — do NOT flip Phase 3)
  2  — inputs malformed / unable to compare

Usage:
    python compare_eval_reports.py eval_report_legacy.json eval_report_unified.json

Optional flags:
    --markdown FILE   also write a markdown summary to FILE
    --json FILE       also write the full diff as JSON to FILE
    --verbose         print every scenario's delta, not just notable ones

Acceptance criteria (from UNIFIED_EXTRACTOR_MIGRATION.md, Phase 2):
  - multi_cross_borough must pass on the unified path
  - No scenario currently >= 4.5 on legacy drops below 4.2 on unified
  - Critical-failure count on unified path <= count on legacy path

The watch list is reported separately. If any watch-list scenario
crosses the acceptance line, that's the Option 4 trigger described in
the migration doc — not a hard migration failure, but a follow-up PR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# --- Constants pulled from the migration doc (rev 14) ----------------------

WATCH_LIST_SCENARIOS = [
    "multi_food_and_shelter_brooklyn",
    "multi_shower_and_food_drop_in",
    "multi_clothing_and_food_harlem",
    "multi_cross_neighborhood_shower_les_food_chinatown",
]

# The scenario the migration exists to fix. Must pass on the unified
# path — passing = average_score >= 4.0 per the eval framework.
TARGET_FIX_SCENARIO = "multi_cross_borough"

# Thresholds
MOVE_DELTA_THRESHOLD = 0.3        # "moved by >= 0.3"
REGRESSION_FLOOR = 4.2             # acceptance: no >= 4.5 drops below this
REGRESSION_CEILING = 4.5           # acceptance: scenarios at or above this
PASSING_THRESHOLD = 4.0            # a scenario "passes" at >= 4.0

# --- Data loading ----------------------------------------------------------

def load_report(path: Path) -> dict:
    """Load and sanity-check a single report. Exits with code 2 on
    input errors (malformed JSON, missing file, missing keys)."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"ERROR: {path} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)

    for key in ("summary", "scenarios", "critical_failures"):
        if key not in data:
            print(
                f"ERROR: {path} is missing top-level key '{key}'",
                file=sys.stderr,
            )
            sys.exit(2)

    if not isinstance(data["scenarios"], list):
        print(f"ERROR: {path}: 'scenarios' is not a list", file=sys.stderr)
        sys.exit(2)

    return data


def index_by_id(scenarios: list) -> dict:
    """Build {scenario_id: scenario_result} map. Warns on duplicates."""
    out: dict = {}
    duplicates: list = []
    for s in scenarios:
        sid = s.get("id")
        if sid is None:
            continue
        if sid in out:
            duplicates.append(sid)
            continue
        out[sid] = s
    if duplicates:
        print(
            f"  WARNING: duplicate scenario IDs in report: {duplicates}",
            file=sys.stderr,
        )
    return out


# --- Core diff logic -------------------------------------------------------

def diff_scenarios(legacy_idx: dict, unified_idx: dict) -> dict:
    """Compute per-scenario deltas and classify each scenario.

    Classifications:
      - 'only_in_legacy': scenario present in legacy report, absent in unified
      - 'only_in_unified': vice versa
      - 'moved_up': unified - legacy >= MOVE_DELTA_THRESHOLD
      - 'moved_down': legacy - unified >= MOVE_DELTA_THRESHOLD
      - 'stable': |delta| < MOVE_DELTA_THRESHOLD
      - 'regressed_acceptance': was >= REGRESSION_CEILING on legacy,
                                 dropped below REGRESSION_FLOOR on unified
      - 'newly_failing': passed on legacy (>= 4.0), failed on unified (< 4.0)
      - 'newly_passing': failed on legacy, passed on unified
    """
    results: dict = {
        "only_in_legacy": [],
        "only_in_unified": [],
        "moved_up": [],
        "moved_down": [],
        "stable": [],
        "regressed_acceptance": [],
        "newly_failing": [],
        "newly_passing": [],
    }

    legacy_ids = set(legacy_idx.keys())
    unified_ids = set(unified_idx.keys())

    for sid in sorted(legacy_ids - unified_ids):
        results["only_in_legacy"].append({
            "id": sid,
            "score": legacy_idx[sid].get("average_score"),
        })
    for sid in sorted(unified_ids - legacy_ids):
        results["only_in_unified"].append({
            "id": sid,
            "score": unified_idx[sid].get("average_score"),
        })

    for sid in sorted(legacy_ids & unified_ids):
        legacy = legacy_idx[sid]
        u = unified_idx[sid]
        l_score = legacy.get("average_score", 0)
        u_score = u.get("average_score", 0)
        delta = round(u_score - l_score, 2)

        entry = {
            "id": sid,
            "category": legacy.get("category", ""),
            "legacy_score": l_score,
            "unified_score": u_score,
            "delta": delta,
            "legacy_weighted": legacy.get("weighted_score"),
            "unified_weighted": u.get("weighted_score"),
        }

        # Acceptance regression: a scenario that was >= 4.5 dropped
        # below 4.2. These are the migration-blocking regressions per
        # Phase 2 acceptance criterion #2.
        if l_score >= REGRESSION_CEILING and u_score < REGRESSION_FLOOR:
            results["regressed_acceptance"].append(entry)

        # Passing-to-failing transitions (soft signal — below the hard
        # acceptance line but still worth surfacing).
        if l_score >= PASSING_THRESHOLD and u_score < PASSING_THRESHOLD:
            results["newly_failing"].append(entry)
        elif l_score < PASSING_THRESHOLD and u_score >= PASSING_THRESHOLD:
            results["newly_passing"].append(entry)

        # Move classification
        if delta >= MOVE_DELTA_THRESHOLD:
            results["moved_up"].append(entry)
        elif delta <= -MOVE_DELTA_THRESHOLD:
            results["moved_down"].append(entry)
        else:
            results["stable"].append(entry)

    return results


def check_target_fix(legacy_idx: dict, unified_idx: dict) -> dict:
    """The specific scenario this migration exists to fix."""
    legacy = legacy_idx.get(TARGET_FIX_SCENARIO)
    u = unified_idx.get(TARGET_FIX_SCENARIO)
    if u is None:
        return {
            "present": False,
            "reason": f"{TARGET_FIX_SCENARIO} absent from unified report",
        }
    u_score = u.get("average_score", 0)
    return {
        "present": True,
        "legacy_score": legacy.get("average_score") if legacy else None,
        "unified_score": u_score,
        "passes_acceptance": u_score >= PASSING_THRESHOLD,
    }


def check_watch_list(legacy_idx: dict, unified_idx: dict) -> list:
    """Report each of the 4 watch-list scenarios explicitly.

    The migration doc scoped these as expected to potentially mispick
    (set-equality blind spot). The Option 4 trigger is "any of them
    crossed the acceptance line" (was >= 4.5 and dropped below 4.2).
    """
    out: list = []
    for sid in WATCH_LIST_SCENARIOS:
        legacy = legacy_idx.get(sid)
        u = unified_idx.get(sid)
        if legacy is None or u is None:
            out.append({
                "id": sid,
                "present": False,
                "reason": (
                    "absent from legacy report" if legacy is None
                    else "absent from unified report"
                ),
            })
            continue
        l_score = legacy.get("average_score", 0)
        u_score = u.get("average_score", 0)
        triggers_option_4 = (
            l_score >= REGRESSION_CEILING and u_score < REGRESSION_FLOOR
        )
        out.append({
            "id": sid,
            "present": True,
            "legacy_score": l_score,
            "unified_score": u_score,
            "delta": round(u_score - l_score, 2),
            "triggers_option_4": triggers_option_4,
        })
    return out


def count_critical_failures(report: dict) -> int:
    """CF count from the report's top-level critical_failures list."""
    cfs = report.get("critical_failures", [])
    return len(cfs) if isinstance(cfs, list) else 0


# --- Output formatting -----------------------------------------------------

def fmt_delta(delta: float) -> str:
    """Arrow + signed delta for quick scanning."""
    if delta >= MOVE_DELTA_THRESHOLD:
        return f"▲ +{delta}"
    if delta <= -MOVE_DELTA_THRESHOLD:
        return f"▼ {delta}"
    return f"·  {delta:+.2f}"


def print_report(
    legacy_report: dict,
    unified_report: dict,
    diff: dict,
    target: dict,
    watch_list: list,
    verbose: bool,
) -> bool:
    """Print the human-readable diff. Returns True if all acceptance
    criteria are met, False otherwise."""
    legacy_summary = legacy_report["summary"]
    unified_summary = unified_report["summary"]
    legacy_cf = count_critical_failures(legacy_report)
    unified_cf = count_critical_failures(unified_report)

    # ---- Headline numbers ------------------------------------------------
    print("=" * 78)
    print("PHASE 2 EVAL COMPARISON")
    print("=" * 78)
    print(f"Legacy report:  timestamp={legacy_report.get('timestamp', '?')}")
    print(f"Unified report: timestamp={unified_report.get('timestamp', '?')}")
    print()
    print("Overall:")
    print(f"  Unweighted avg: {legacy_summary.get('overall_average', '?'):.2f}"
          f"  →  {unified_summary.get('overall_average', '?'):.2f}")
    print(f"  Weighted avg:   {legacy_summary.get('weighted_average', '?'):.2f}"
          f"  →  {unified_summary.get('weighted_average', '?'):.2f}")
    print(f"  Scenarios:      legacy={len(legacy_report['scenarios'])}"
          f"  unified={len(unified_report['scenarios'])}")
    print(f"  Critical fails: legacy={legacy_cf}  unified={unified_cf}")
    print()

    # ---- Acceptance criteria --------------------------------------------
    acceptance_pass = True
    print("-" * 78)
    print("ACCEPTANCE CRITERIA (from UNIFIED_EXTRACTOR_MIGRATION.md Phase 2)")
    print("-" * 78)

    # Criterion 1: multi_cross_borough passes
    if not target["present"]:
        print(f"  ✗ {TARGET_FIX_SCENARIO}: NOT FOUND in unified report")
        acceptance_pass = False
    else:
        status = "PASSES" if target["passes_acceptance"] else "FAILS"
        mark = "✓" if target["passes_acceptance"] else "✗"
        legacy_s = target["legacy_score"]
        unified_s = target["unified_score"]
        print(
            f"  {mark} {TARGET_FIX_SCENARIO} {status} on unified path: "
            f"legacy={legacy_s}, unified={unified_s}"
        )
        if not target["passes_acceptance"]:
            acceptance_pass = False

    # Criterion 2: no >= 4.5 drops below 4.2
    regressions = diff["regressed_acceptance"]
    if regressions:
        print(
            f"  ✗ {len(regressions)} scenario(s) were >= {REGRESSION_CEILING} "
            f"on legacy and dropped below {REGRESSION_FLOOR} on unified:"
        )
        for r in regressions:
            print(
                f"      {r['id']} ({r['category']}): "
                f"{r['legacy_score']} → {r['unified_score']} "
                f"({fmt_delta(r['delta'])})"
            )
        acceptance_pass = False
    else:
        print(
            f"  ✓ no scenarios >= {REGRESSION_CEILING} on legacy dropped "
            f"below {REGRESSION_FLOOR} on unified"
        )

    # Criterion 3: CF count
    if unified_cf <= legacy_cf:
        print(f"  ✓ critical failures: unified ({unified_cf}) <= legacy ({legacy_cf})")
    else:
        print(
            f"  ✗ critical failures: unified ({unified_cf}) > legacy ({legacy_cf}) "
            f"by {unified_cf - legacy_cf}"
        )
        acceptance_pass = False

    # ---- Watch list ------------------------------------------------------
    print()
    print("-" * 78)
    print("WATCH LIST (set-equality blind spots — Option 4 trigger check)")
    print("-" * 78)
    any_trigger = False
    for w in watch_list:
        if not w["present"]:
            print(f"  ⚠  {w['id']}: {w['reason']}")
            continue
        marker = "⚠ OPTION 4 TRIGGER" if w["triggers_option_4"] else "✓"
        print(
            f"  {marker:20s} {w['id']}: "
            f"{w['legacy_score']} → {w['unified_score']} "
            f"({fmt_delta(w['delta'])})"
        )
        if w["triggers_option_4"]:
            any_trigger = True

    if any_trigger:
        print()
        print("  → Watch-list regression detected. Per the migration doc,")
        print("    ship the Option 4 hardening (urgency hierarchy appended")
        print("    to _SHORT_SYSTEM_PROMPT) as a fast follow-up PR. This is")
        print("    a follow-up action, NOT a migration blocker.")

    # ---- Movers ----------------------------------------------------------
    print()
    print("-" * 78)
    print(f"SCENARIOS THAT MOVED BY >= {MOVE_DELTA_THRESHOLD}")
    print("-" * 78)
    print(f"  Moved up:   {len(diff['moved_up'])}")
    print(f"  Moved down: {len(diff['moved_down'])}")
    print(f"  Stable:     {len(diff['stable'])}")
    print()

    if diff["moved_up"]:
        print("  Improvements (largest first):")
        sorted_up = sorted(
            diff["moved_up"], key=lambda x: x["delta"], reverse=True
        )
        for s in sorted_up:
            print(
                f"    {fmt_delta(s['delta'])}  {s['id']} ({s['category']}): "
                f"{s['legacy_score']} → {s['unified_score']}"
            )
    if diff["moved_down"]:
        print()
        print("  Regressions (largest first):")
        sorted_down = sorted(
            diff["moved_down"], key=lambda x: x["delta"]
        )
        for s in sorted_down:
            print(
                f"    {fmt_delta(s['delta'])}  {s['id']} ({s['category']}): "
                f"{s['legacy_score']} → {s['unified_score']}"
            )

    # ---- Passing-status transitions --------------------------------------
    print()
    print("-" * 78)
    print(f"PASSING-STATUS TRANSITIONS (threshold = {PASSING_THRESHOLD})")
    print("-" * 78)
    if diff["newly_passing"]:
        print(f"  Newly passing ({len(diff['newly_passing'])}):")
        for s in diff["newly_passing"]:
            print(
                f"    {s['id']} ({s['category']}): "
                f"{s['legacy_score']} → {s['unified_score']}"
            )
    if diff["newly_failing"]:
        print(f"  Newly failing ({len(diff['newly_failing'])}):")
        for s in diff["newly_failing"]:
            print(
                f"    {s['id']} ({s['category']}): "
                f"{s['legacy_score']} → {s['unified_score']}"
            )
    if not diff["newly_passing"] and not diff["newly_failing"]:
        print("  No scenarios crossed the passing threshold in either direction.")

    # ---- Coverage mismatches ---------------------------------------------
    only_legacy = diff["only_in_legacy"]
    only_unified = diff["only_in_unified"]
    if only_legacy or only_unified:
        print()
        print("-" * 78)
        print("COVERAGE MISMATCH (scenario ID set differs between reports)")
        print("-" * 78)
        if only_legacy:
            print(f"  Only in legacy ({len(only_legacy)}):")
            for s in only_legacy:
                print(f"    {s['id']} (score: {s['score']})")
        if only_unified:
            print(f"  Only in unified ({len(only_unified)}):")
            for s in only_unified:
                print(f"    {s['id']} (score: {s['score']})")
        print()
        print(
            "  NOTE: Comparison applies only to the intersection of scenario "
            "IDs. Mismatches may indicate a skipped scenario, a non-deterministic "
            "simulation that threw before judging, or a scenario-bank change "
            "between runs."
        )

    # ---- Verbose dump ----------------------------------------------------
    if verbose:
        print()
        print("-" * 78)
        print("ALL SCENARIOS (ordered by delta)")
        print("-" * 78)
        all_entries = (
            diff["moved_up"] + diff["moved_down"] + diff["stable"]
        )
        for s in sorted(all_entries, key=lambda x: x["delta"]):
            print(
                f"  {fmt_delta(s['delta'])}  {s['id']} ({s['category']}): "
                f"{s['legacy_score']} → {s['unified_score']}"
            )

    # ---- Verdict ---------------------------------------------------------
    print()
    print("=" * 78)
    if acceptance_pass:
        print("✓ ACCEPTANCE CRITERIA MET — migration clear to proceed to Phase 3")
        if any_trigger:
            print("  (Ship Option 4 hardening as a follow-up PR first.)")
    else:
        print("✗ ACCEPTANCE CRITERIA VIOLATED — do NOT flip Phase 3")
        print("  Return to Phase 1 and tune merge rules per the doc.")
    print("=" * 78)

    return acceptance_pass


def write_markdown(
    path: Path,
    diff: dict,
    target: dict,
    watch_list: list,
    legacy_report: dict,
    unified_report: dict,
    acceptance_pass: bool,
) -> None:
    """Write a markdown summary suitable for pasting into a PR description."""
    legacy_s = legacy_report["summary"]
    unified_s = unified_report["summary"]
    legacy_cf = count_critical_failures(legacy_report)
    unified_cf = count_critical_failures(unified_report)

    lines: list = []
    lines.append("# Phase 2 Eval Comparison")
    lines.append("")
    lines.append(
        f"**Legacy run:** {legacy_report.get('timestamp', '?')}  "
        f"**Unified run:** {unified_report.get('timestamp', '?')}"
    )
    lines.append("")
    lines.append(
        f"**Overall (unweighted):** {legacy_s.get('overall_average', '?')}"
        f" → {unified_s.get('overall_average', '?')}"
    )
    lines.append(
        f"**Overall (weighted):** {legacy_s.get('weighted_average', '?')}"
        f" → {unified_s.get('weighted_average', '?')}"
    )
    lines.append(f"**Critical failures:** {legacy_cf} → {unified_cf}")
    lines.append("")

    # Acceptance criteria table
    lines.append("## Acceptance Criteria")
    lines.append("")
    lines.append("| Criterion | Status | Detail |")
    lines.append("|---|---|---|")

    target_status = (
        "✅ PASS" if target.get("passes_acceptance")
        else ("❌ FAIL" if target.get("present") else "⚠️ NOT FOUND")
    )
    target_detail = (
        f"legacy {target['legacy_score']} → unified {target['unified_score']}"
        if target.get("present") else target.get("reason", "")
    )
    lines.append(
        f"| `{TARGET_FIX_SCENARIO}` passes on unified | {target_status} "
        f"| {target_detail} |"
    )

    reg_count = len(diff["regressed_acceptance"])
    reg_status = "✅ PASS" if reg_count == 0 else "❌ FAIL"
    lines.append(
        f"| No scenario >={REGRESSION_CEILING} drops below "
        f"{REGRESSION_FLOOR} | {reg_status} "
        f"| {reg_count} regression(s) |"
    )

    cf_status = "✅ PASS" if unified_cf <= legacy_cf else "❌ FAIL"
    lines.append(
        f"| CF count: unified <= legacy | {cf_status} "
        f"| {unified_cf} vs {legacy_cf} |"
    )
    lines.append("")

    # Watch list
    lines.append("## Watch List (Set-Equality Blind Spots)")
    lines.append("")
    lines.append("| Scenario | Legacy | Unified | Delta | Option 4 Trigger |")
    lines.append("|---|---|---|---|---|")
    for w in watch_list:
        if not w.get("present"):
            lines.append(
                f"| `{w['id']}` | — | — | — | ⚠️ {w.get('reason', 'absent')} |"
            )
        else:
            trig = "⚠️ YES" if w["triggers_option_4"] else "no"
            lines.append(
                f"| `{w['id']}` | {w['legacy_score']} | "
                f"{w['unified_score']} | {w['delta']:+.2f} | {trig} |"
            )
    lines.append("")

    # Significant movers
    if diff["moved_down"]:
        lines.append("## Regressed Scenarios (≥0.3 drop)")
        lines.append("")
        lines.append("| Scenario | Category | Legacy | Unified | Delta |")
        lines.append("|---|---|---|---|---|")
        for s in sorted(diff["moved_down"], key=lambda x: x["delta"]):
            lines.append(
                f"| `{s['id']}` | {s['category']} | {s['legacy_score']} "
                f"| {s['unified_score']} | {s['delta']:+.2f} |"
            )
        lines.append("")

    if diff["moved_up"]:
        lines.append("## Improved Scenarios (≥0.3 gain)")
        lines.append("")
        lines.append("| Scenario | Category | Legacy | Unified | Delta |")
        lines.append("|---|---|---|---|---|")
        for s in sorted(
            diff["moved_up"], key=lambda x: x["delta"], reverse=True
        ):
            lines.append(
                f"| `{s['id']}` | {s['category']} | {s['legacy_score']} "
                f"| {s['unified_score']} | {s['delta']:+.2f} |"
            )
        lines.append("")

    # Passing transitions
    if diff["newly_failing"] or diff["newly_passing"]:
        lines.append(f"## Passing-Status Transitions (threshold {PASSING_THRESHOLD})")
        lines.append("")
        if diff["newly_passing"]:
            lines.append(f"**Newly passing ({len(diff['newly_passing'])}):**")
            for s in diff["newly_passing"]:
                lines.append(
                    f"- `{s['id']}` ({s['category']}): "
                    f"{s['legacy_score']} → {s['unified_score']}"
                )
            lines.append("")
        if diff["newly_failing"]:
            lines.append(f"**Newly failing ({len(diff['newly_failing'])}):**")
            for s in diff["newly_failing"]:
                lines.append(
                    f"- `{s['id']}` ({s['category']}): "
                    f"{s['legacy_score']} → {s['unified_score']}"
                )
            lines.append("")

    # Verdict
    lines.append("## Verdict")
    lines.append("")
    if acceptance_pass:
        lines.append("✅ **Acceptance criteria met — clear to proceed to Phase 3.**")
    else:
        lines.append("❌ **Acceptance criteria violated — do not flip Phase 3.**")
        lines.append("Return to Phase 1 and tune merge rules.")

    path.write_text("\n".join(lines) + "\n")
    print(f"  Markdown summary written to: {path}")


# --- CLI -------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "legacy", type=Path,
        help="Path to the eval report from the legacy (flag-off) run",
    )
    parser.add_argument(
        "unified", type=Path,
        help="Path to the eval report from the unified (flag-on) run",
    )
    parser.add_argument(
        "--markdown", type=Path, default=None,
        help="Also write a markdown summary to this path",
    )
    parser.add_argument(
        "--json", type=Path, default=None, dest="json_out",
        help="Also write the full diff as JSON to this path",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print every scenario's delta, not just notable ones",
    )
    args = parser.parse_args(argv)

    legacy = load_report(args.legacy)
    unified = load_report(args.unified)

    legacy_idx = index_by_id(legacy["scenarios"])
    unified_idx = index_by_id(unified["scenarios"])

    diff = diff_scenarios(legacy_idx, unified_idx)
    target = check_target_fix(legacy_idx, unified_idx)
    watch_list = check_watch_list(legacy_idx, unified_idx)

    acceptance_pass = print_report(
        legacy, unified, diff, target, watch_list, args.verbose,
    )

    if args.markdown:
        write_markdown(
            args.markdown, diff, target, watch_list,
            legacy, unified, acceptance_pass,
        )

    if args.json_out:
        payload = {
            "legacy_timestamp": legacy.get("timestamp"),
            "unified_timestamp": unified.get("timestamp"),
            "legacy_summary": legacy["summary"],
            "unified_summary": unified["summary"],
            "legacy_critical_failure_count": count_critical_failures(legacy),
            "unified_critical_failure_count": count_critical_failures(unified),
            "target_fix_scenario": target,
            "watch_list": watch_list,
            "diff": diff,
            "acceptance_pass": acceptance_pass,
        }
        args.json_out.write_text(json.dumps(payload, indent=2))
        print(f"  Diff JSON written to: {args.json_out}")

    return 0 if acceptance_pass else 1


if __name__ == "__main__":
    sys.exit(main())
