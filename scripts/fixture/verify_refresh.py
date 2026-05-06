"""Post-refresh verification for tests/eval/fixtures/services.json.

Run after the data team produces a new fixture from
04_extract_fixture_hybrid.sql to confirm the cohort coverage and pinned
providers landed correctly.

Usage:
    python3 scripts/fixture/verify_refresh.py

Exit code 0 = pass; 1 = at least one expected coverage gap remains.
The script prints a per-cohort and per-pin matrix to make it easy to
see which expectations are met and which still fail.

Designed to be cheap (~50ms) and runnable in CI as a fixture
sanity-check before any full eval run.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

FIXTURE_PATH = Path("tests/eval/fixtures/services.json")


# ---------------------------------------------------------------------------
# COHORT EXPECTATIONS
# ---------------------------------------------------------------------------
# Each cohort maps to a taxonomy name (case-insensitive). Per-cohort
# minimum borough coverage:
#
#   - drop_in_center, substance_use_treatment: 3/5 boroughs.
#     These cohorts have substantial coverage in production data.
#
#   - families, youth, lgbtq_young_adult: 1/5 boroughs.
#     These are rare populations in the source DB. The May 5 refresh
#     surfaced 1 row each in Manhattan; better than 0/5, but unlikely
#     to ever reach 3+ boroughs without upstream data ingestion.
#     Keeping the threshold at ≥1 means the cohort filter has at
#     least *something* to filter against.
#
# If a cohort below its threshold, that's a fail and the verifier
# exits non-zero. The runbook documents which gaps are upstream-data
# constraints vs. fixture-script bugs.
COHORT_TAXONOMIES = {
    "families":                "families",
    "youth":                   "youth",
    "lgbtq_young_adult":       "lgbtq young adult",
    "drop_in_center":          "drop-in center",
    "substance_use_treatment": "substance use treatment",
}

MIN_BOROUGHS_PER_COHORT = {
    "drop_in_center":          3,
    "substance_use_treatment": 3,
    # Rare populations — accept ≥1 because production data is thin.
    "families":                1,
    "youth":                   1,
    "lgbtq_young_adult":       1,
}

BOROUGHS = ("Manhattan", "Brooklyn", "Bronx", "Queens", "Staten Island")


# ---------------------------------------------------------------------------
# PINNED-PROVIDER EXPECTATIONS
# ---------------------------------------------------------------------------
# Each pin: (label, lambda(row)->bool, required: bool).
# - required=True: the fixture MUST contain this provider after refresh.
#   These are the ones the Cornell sample queries explicitly name AND
#   that the eval scenarios reference by name.
# - required=False: nice-to-have but not blocking. Logged but no fail.
def _s(r: dict, k: str) -> str:
    return (r.get(k) or "").lower()


PIN_PATTERNS = [
    # (label, predicate, required)
    ("covenant_house",
     lambda r: "covenant house" in _s(r, "organization_name"),
     True),
    ("ali_forney",
     lambda r: "ali forney" in _s(r, "organization_name") or "ali forney" in _s(r, "location_name"),
     True),
    ("make_the_road",
     lambda r: "make the road" in _s(r, "organization_name"),
     True),
    ("safe_horizon_streetwork",
     lambda r: "safe horizon" in _s(r, "organization_name") and (
         "streetwork" in _s(r, "organization_name")
         or "streetwork" in _s(r, "location_name")
         or "streetwork" in _s(r, "service_name")
     ),
     True),
    # DHS Family Intake — production has AFIC (Adult Family Intake Center)
    # and PATH (Prevention Assistance and Temporary Housing). Both are
    # DHS family intake centers and operationally equivalent for the
    # eval scenarios — accept either.
    ("dhs_family_intake",
     lambda r: ("dhs" in _s(r, "organization_name") or "department of homeless" in _s(r, "organization_name"))
        and ("path" in _s(r, "location_name") or "path" in _s(r, "service_name")
             or "afic" in _s(r, "location_name") or "afic" in _s(r, "service_name")
             or "intake" in _s(r, "location_name") or "intake" in _s(r, "service_name")
             or "prevention assistance" in _s(r, "service_description")),
     True),
    ("msbi_addiction",
     lambda r: "mount sinai beth israel" in _s(r, "organization_name") and (
         "addiction" in _s(r, "location_name") or "addiction" in _s(r, "service_name")
     ),
     True),
    # Important but not always available
    ("realization_center",
     lambda r: "realization center" in _s(r, "organization_name"),
     False),
    ("project_renewal_3rd_st",
     lambda r: "project renewal" in _s(r, "organization_name") and (
         "3rd street" in _s(r, "location_name") or "third street" in _s(r, "location_name")
         or "820" in _s(r, "service_name")
     ),
     False),
    ("cabrini_immigrant",
     lambda r: "cabrini" in _s(r, "organization_name")
        and "immigrant" in _s(r, "organization_name"),
     False),
    ("unlocal",
     lambda r: "unlocal" in _s(r, "organization_name"),
     False),
    ("catholic_worker",
     lambda r: "catholic worker" in _s(r, "organization_name"),
     False),
    ("dycd_youth_drop_in",
     lambda r: "dycd" in _s(r, "organization_name") and (
         "drop-in" in _s(r, "service_name") or "drop in" in _s(r, "service_name")
         or "drop-in" in _s(r, "location_name") or "drop in" in _s(r, "location_name")
     ),
     False),
    ("idnyc",
     lambda r: "idnyc" in _s(r, "service_name") or "idnyc" in _s(r, "location_name")
        or "idnyc" in _s(r, "service_description")
        or "free id" in _s(r, "service_name")
        or "municipal id" in _s(r, "service_description"),
     False),
    ("family_justice_center",
     lambda r: "family justice center" in _s(r, "organization_name"),
     False),
    ("riseboro",
     lambda r: "riseboro" in _s(r, "organization_name"),
     False),
    ("doobneek",
     lambda r: "doobneek" in _s(r, "organization_name"),
     False),
    ("bowery_mission",
     lambda r: "bowery mission" in _s(r, "organization_name"),
     False),
]


def main() -> int:
    if not FIXTURE_PATH.exists():
        print(f"❌ Fixture not found: {FIXTURE_PATH}")
        return 1
    fixture = json.loads(FIXTURE_PATH.read_text())
    print(f"Fixture: {len(fixture)} rows\n")

    failed = False

    # ------------------------- Cohort coverage -------------------------
    print("=" * 70)
    print("COHORT COVERAGE")
    print("=" * 70)
    cohort_hits: dict[tuple[str, str], int] = defaultdict(int)
    for r in fixture:
        tax = {t.lower() for t in (r.get("service_taxonomies") or [])}
        for cohort, taxonomy_name in COHORT_TAXONOMIES.items():
            if taxonomy_name in tax:
                cohort_hits[(cohort, r.get("borough", "?"))] += 1

    print(f"\n  {'cohort':<26}" + "".join(f"  {b:<14}" for b in BOROUGHS))
    print(f"  {'-'*26}" + "".join(f"  {'-'*14}" for _ in BOROUGHS))
    for cohort in COHORT_TAXONOMIES:
        row_counts = [cohort_hits.get((cohort, b), 0) for b in BOROUGHS]
        boroughs_with_coverage = sum(1 for c in row_counts if c > 0)
        threshold = MIN_BOROUGHS_PER_COHORT[cohort]
        marker = "✅" if boroughs_with_coverage >= threshold else "❌"
        cells = []
        for c in row_counts:
            cells.append(f"  {('-' if c == 0 else str(c)):<14}")
        print(f"  {cohort:<26}" + "".join(cells) + f"  {marker} ({boroughs_with_coverage}/5, need ≥{threshold})")
        if boroughs_with_coverage < threshold:
            failed = True

    # ------------------------- Pinned providers -------------------------
    print("\n" + "=" * 70)
    print("PINNED PROVIDERS")
    print("=" * 70)
    print(f"\n  {'pin':<32} {'count':>5} {'required':>9} {'status':>10}")
    print(f"  {'-'*32} {'-'*5} {'-'*9} {'-'*10}")
    for label, pred, required in PIN_PATTERNS:
        matches = [r for r in fixture if pred(r)]
        n = len(matches)
        if required:
            status = "✅ pass" if n > 0 else "❌ MISSING"
            if n == 0:
                failed = True
        else:
            status = "✓" if n > 0 else "·"
        print(f"  {label:<32} {n:>5} {('yes' if required else 'no'):>9} {status:>10}")

    # ------------------------- Bucket distribution -------------------------
    print("\n" + "=" * 70)
    print("BUCKET DISTRIBUTION (sanity)")
    print("=" * 70)
    buckets: Counter[tuple[str, str]] = Counter()
    for r in fixture:
        st = r.get("bot_service_type")
        b = r.get("borough")
        if st and b:
            buckets[(st, b)] += 1
    print(f"\n  {'service_type':<15}" + "".join(f"  {b:<14}" for b in BOROUGHS))
    print(f"  {'-'*15}" + "".join(f"  {'-'*14}" for _ in BOROUGHS))
    service_types = sorted({k[0] for k in buckets})
    for st in service_types:
        cells = []
        for b in BOROUGHS:
            n = buckets.get((st, b), 0)
            cells.append(f"  {n:<14}")
        print(f"  {st:<15}" + "".join(cells))

    # ------------------------- Summary -------------------------
    print()
    if failed:
        print("❌ FIXTURE VERIFICATION FAILED")
        print("   Refresh did not produce expected coverage. See ❌ markers above.")
        return 1
    print(f"✅ FIXTURE VERIFICATION PASSED ({len(fixture)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
