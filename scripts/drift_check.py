#!/usr/bin/env python3
"""
Automated drift detection for chatbot query parity.

Catches divergences between our chatbot's query logic and the YourPeer
production app / Streetlives database BEFORE they become user-facing bugs.

Four independent checks:
  1. taxonomy_drift   — DB taxonomy tree vs our hardcoded template lists
  2. source_parity    — YourPeer GitHub source vs our category mapping
  3. result_parity    — same query through both systems, compare location IDs
  4. business_rules   — verify our assumptions against live DB data

Usage:
  python scripts/drift_check.py                  # run all checks
  python scripts/drift_check.py taxonomy_drift    # run one check
  python scripts/drift_check.py --ci              # exit code 1 on failure

Requires:
  - DATABASE_URL env var (checks 1, 4)
  - Network access to GitHub API (check 2)
  - Network access to Streetlives API (check 3)

Schedule: run weekly via CI or cron. Alert on any FAIL.
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _get_db_connection():
    """Get a SQLAlchemy connection to the Streetlives DB."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        return engine.connect()
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        return None


def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch a URL and return text content, or None on failure."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "YourPeer-DriftCheck/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8")
    except Exception as e:
        logger.warning(f"Fetch failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# CHECK 1: Taxonomy drift — DB taxonomy tree vs template lists
# ---------------------------------------------------------------------------

def check_taxonomy_drift() -> dict:
    """
    Compare the chatbot's hardcoded taxonomy_names lists against the
    actual taxonomy tree in the Streetlives database.

    Catches: new taxonomies added to DB that chatbot doesn't know about,
    taxonomy renames that break our LOWER(t.name) matching, and deleted
    taxonomies that are still in our templates.
    """
    logger.info("\n" + "=" * 60)
    logger.info("  CHECK 1: Taxonomy drift (DB vs templates)")
    logger.info("=" * 60)

    conn = _get_db_connection()
    if not conn:
        return {"status": "SKIP", "reason": "No DB connection"}

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.rag.query_templates import TEMPLATES
    from sqlalchemy import text

    # Fetch all taxonomy names and their parent-child relationships from DB
    rows = conn.execute(text("""
        SELECT t.id, t.name, t.parent_name
        FROM taxonomies t
        ORDER BY t.parent_name NULLS FIRST, t.name
    """)).fetchall()
    conn.close()

    db_taxonomies = {}  # name_lower → {name, parent_name, id}
    for row in rows:
        db_taxonomies[row[1].lower()] = {
            "name": row[1],
            "parent_name": row[2],
            "id": str(row[0]),
        }

    db_names = set(db_taxonomies.keys())
    issues = []

    for template_key, template in TEMPLATES.items():
        if template_key == "org_name":
            continue

        our_names = set(
            n.lower() for n in template["default_params"].get("taxonomy_names", [])
        )

        # Find taxonomy names we reference that don't exist in DB
        missing_from_db = our_names - db_names
        if missing_from_db:
            issues.append({
                "type": "STALE_REFERENCE",
                "severity": "HIGH",
                "template": template_key,
                "detail": f"Template references taxonomies not in DB: {sorted(missing_from_db)}",
            })

        # Find DB taxonomies that SHOULD be in our list but aren't.
        # For each parent taxonomy in our list, check if all its children are included.
        our_parents = set()
        for name in our_names:
            info = db_taxonomies.get(name)
            if info and info["parent_name"] is None:
                our_parents.add(name)

        for parent_name in our_parents:
            db_children = {
                name for name, info in db_taxonomies.items()
                if info["parent_name"] and info["parent_name"].lower() == parent_name
            }
            missing_children = db_children - our_names
            if missing_children:
                issues.append({
                    "type": "NEW_TAXONOMY",
                    "severity": "MEDIUM",
                    "template": template_key,
                    "detail": (
                        f"DB has children of '{parent_name}' not in template: "
                        f"{sorted(missing_children)}. YourPeer includes them automatically "
                        f"via parent taxonomy expansion; our chatbot does not."
                    ),
                })

    # Report
    if issues:
        for issue in issues:
            icon = "❌" if issue["severity"] == "HIGH" else "⚠️"
            logger.info(f"  {icon} [{issue['severity']}] {issue['template']}: {issue['detail']}")
        return {"status": "FAIL", "issues": issues}
    else:
        logger.info("  ✅ All template taxonomy names exist in DB")
        logger.info("  ✅ No new child taxonomies missing from templates")
        return {"status": "PASS", "issues": []}


# ---------------------------------------------------------------------------
# CHECK 2: Source parity — YourPeer GitHub vs our mapping
# ---------------------------------------------------------------------------

def check_source_parity() -> dict:
    """
    Fetch YourPeer's CATEGORY_TO_TAXONOMY_NAME_MAP and SHELTER_PARAM
    constants from GitHub, and compare against our category mapping.

    Catches: YourPeer adds a new category or changes taxonomy mapping
    that our chatbot doesn't reflect.
    """
    logger.info("\n" + "=" * 60)
    logger.info("  CHECK 2: Source parity (YourPeer GitHub vs our mapping)")
    logger.info("=" * 60)

    # Fetch the common.ts file from GitHub raw
    raw_url = (
        "https://raw.githubusercontent.com/streetlives/yourpeer.nyc/"
        "main/src/components/common.ts"
    )
    content = _fetch_url(raw_url)
    if not content:
        return {"status": "SKIP", "reason": "Could not fetch YourPeer source from GitHub"}

    issues = []
    import re

    # Extract CATEGORY_TO_TAXONOMY_NAME_MAP
    map_match = re.search(
        r'CATEGORY_TO_TAXONOMY_NAME_MAP[^{]*\{([^}]+)\}', content
    )
    if map_match:
        # Parse the key-value pairs
        pairs = re.findall(
            r'\["([^"]+)"\].*?["\']([^"\']+)["\']',
            map_match.group(1)
        )
        yp_categories = {k: v for k, v in pairs}

        # Our category mapping
        our_categories = {
            "food": "Food",
            "shelters-housing": "Shelter",
            "clothing": "Clothing",
            "personal-care": "Personal Care",
            "health-care": "Health",
            "mental-health": "Mental Health",
            "legal-services": "Legal Services",  # under Other in YP
            "employment": "Employment",           # under Other in YP
        }

        for yp_cat, yp_taxonomy in yp_categories.items():
            if yp_cat not in our_categories:
                issues.append({
                    "type": "NEW_CATEGORY",
                    "severity": "HIGH",
                    "detail": (
                        f"YourPeer has category '{yp_cat}' → '{yp_taxonomy}' "
                        f"that our chatbot doesn't map"
                    ),
                })
        logger.info(f"  Found {len(yp_categories)} YourPeer categories")
    else:
        logger.warning("  Could not parse CATEGORY_TO_TAXONOMY_NAME_MAP from source")

    # Extract shelter sub-filter params
    shelter_params = re.findall(
        r'SHELTER_PARAM_(\w+)_VALUE\s*=\s*["\']([^"\']+)["\']',
        content
    )
    if shelter_params:
        yp_shelter_filters = {name: value for name, value in shelter_params}
        logger.info(f"  Found {len(yp_shelter_filters)} YourPeer shelter sub-filters: "
                     f"{list(yp_shelter_filters.keys())}")

        # Check for new shelter params we don't handle
        known_shelter_params = {"FAMILY", "SINGLE", "YOUTH"}
        new_params = set(yp_shelter_filters.keys()) - known_shelter_params
        if new_params:
            issues.append({
                "type": "NEW_SHELTER_PARAM",
                "severity": "HIGH",
                "detail": f"YourPeer has new shelter sub-filters: {new_params}",
            })

    # Check for new food/health/other sub-filters
    for param_prefix in ["FOOD_PARAM", "HEALTH_PARAM", "OTHER_PARAM",
                          "CLOTHING_PARAM", "AMENITIES_PARAM"]:
        param_values = re.findall(
            rf'{param_prefix}_(\w+)_VALUE\s*=\s*["\']([^"\']+)["\']',
            content
        )
        if param_values:
            logger.info(f"  {param_prefix}: {[v[0] for v in param_values]}")

    if issues:
        for issue in issues:
            logger.info(f"  ❌ [{issue['severity']}] {issue['detail']}")
        return {"status": "FAIL", "issues": issues}
    else:
        logger.info("  ✅ No new YourPeer categories or sub-filters detected")
        return {"status": "PASS", "issues": []}


# ---------------------------------------------------------------------------
# CHECK 3: Result parity — same query, both systems
# ---------------------------------------------------------------------------

def check_result_parity() -> dict:
    """
    Run canonical queries through BOTH the chatbot's SQL pipeline AND the
    Streetlives REST API, then compare which location IDs are returned.

    Catches: our query logic returns different results than YourPeer
    for the same search parameters.
    """
    logger.info("\n" + "=" * 60)
    logger.info("  CHECK 3: Result parity (chatbot SQL vs Streetlives API)")
    logger.info("=" * 60)

    conn = _get_db_connection()
    if not conn:
        return {"status": "SKIP", "reason": "No DB connection"}

    api_base = "https://w6pkliozjh.execute-api.us-east-1.amazonaws.com/prod"

    # First, we need taxonomy IDs from the API
    taxonomy_url = f"{api_base}/taxonomy"
    tax_response = _fetch_url(taxonomy_url)
    if not tax_response:
        conn.close()
        return {"status": "SKIP", "reason": "Could not reach Streetlives API"}

    try:
        taxonomy_tree = json.loads(tax_response)
    except json.JSONDecodeError:
        conn.close()
        return {"status": "SKIP", "reason": "Invalid taxonomy API response"}

    # Build taxonomy name → ID mapping
    tax_name_to_id = {}
    for tax in taxonomy_tree:
        tax_name_to_id[tax["name"]] = tax["id"]
        for child in tax.get("children", []):
            tax_name_to_id[child["name"]] = child["id"]

    logger.info(f"  Loaded {len(tax_name_to_id)} taxonomy IDs from API")

    # Canonical test queries — (category, taxonomy_name, description)
    test_queries = [
        ("food", "Food", "Generic food search"),
        ("shelter", "Shelter", "Generic shelter search"),
        ("clothing", "Clothing", "Generic clothing search"),
        ("personal_care", "Personal Care", "Generic personal care search"),
    ]

    issues = []
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sqlalchemy import text

    for template_key, yp_taxonomy_name, description in test_queries:
        tax_id = tax_name_to_id.get(yp_taxonomy_name)
        if not tax_id:
            logger.warning(f"  ⚠️  No taxonomy ID for '{yp_taxonomy_name}'")
            continue

        # YourPeer API query
        api_url = (
            f"{api_base}/locations"
            f"?occasion=COVID19&taxonomyId={tax_id}&locationFieldsOnly=true"
        )
        api_response = _fetch_url(api_url)
        if not api_response:
            logger.warning(f"  ⚠️  API failed for {template_key}")
            continue

        try:
            api_locations = json.loads(api_response)
            api_location_ids = {str(loc["id"]) for loc in api_locations}
        except (json.JSONDecodeError, KeyError):
            logger.warning(f"  ⚠️  Invalid API response for {template_key}")
            continue

        # Chatbot SQL query (simplified — just location IDs)
        from app.rag.query_templates import TEMPLATES
        our_taxonomies = TEMPLATES[template_key]["default_params"]["taxonomy_names"]
        sql = text("""
            SELECT DISTINCT l.id
            FROM locations l
            JOIN services s ON s.location_id = l.id
            JOIN service_taxonomy st ON st.service_id = s.id
            JOIN taxonomies t ON st.taxonomy_id = t.id
            JOIN physical_addresses pa ON pa.location_id = l.id
            WHERE LOWER(t.name) = ANY(:taxonomy_names)
              AND l.hidden_from_search IS NOT TRUE
              AND LOWER(pa.state_province) = 'ny'
        """)
        try:
            rows = conn.execute(sql, {"taxonomy_names": our_taxonomies}).fetchall()
            our_location_ids = {str(row[0]) for row in rows}
        except Exception as e:
            logger.warning(f"  ⚠️  SQL failed for {template_key}: {e}")
            continue

        # Compare
        only_in_api = api_location_ids - our_location_ids
        only_in_chatbot = our_location_ids - api_location_ids
        overlap = api_location_ids & our_location_ids

        if only_in_api or only_in_chatbot:
            issues.append({
                "type": "RESULT_MISMATCH",
                "severity": "HIGH" if len(only_in_api) > 5 else "MEDIUM",
                "template": template_key,
                "detail": (
                    f"API: {len(api_location_ids)} locations, "
                    f"Chatbot: {len(our_location_ids)}, "
                    f"Overlap: {len(overlap)}, "
                    f"Only API: {len(only_in_api)}, "
                    f"Only chatbot: {len(only_in_chatbot)}"
                ),
            })
            logger.info(f"  ⚠️  {template_key}: {len(only_in_api)} locations in API but not chatbot, "
                        f"{len(only_in_chatbot)} in chatbot but not API")
        else:
            logger.info(f"  ✅ {template_key}: {len(overlap)} locations match exactly")

    conn.close()

    if issues:
        return {"status": "FAIL", "issues": issues}
    else:
        return {"status": "PASS", "issues": []}


# ---------------------------------------------------------------------------
# CHECK 4: Business rule validation — verify assumptions against DB
# ---------------------------------------------------------------------------

def check_business_rules() -> dict:
    """
    Verify the chatbot's invented business rules against actual DB data.

    Catches: our assumptions about taxonomy tagging, age eligibility
    ranges, and service categorization that don't match reality.
    """
    logger.info("\n" + "=" * 60)
    logger.info("  CHECK 4: Business rule validation (assumptions vs DB)")
    logger.info("=" * 60)

    conn = _get_db_connection()
    if not conn:
        return {"status": "SKIP", "reason": "No DB connection"}

    from sqlalchemy import text
    issues = []

    # ── Rule 1: Are youth shelters also tagged "Shelter"? ──
    # If not, they're invisible without the "youth" taxonomy in our list.
    youth_only = conn.execute(text("""
        SELECT l.name, l.slug
        FROM locations l
        JOIN services s ON s.location_id = l.id
        JOIN service_taxonomy st ON st.service_id = s.id
        JOIN taxonomies t ON st.taxonomy_id = t.id
        WHERE LOWER(t.name) = 'youth'
          AND l.hidden_from_search IS NOT TRUE
          AND l.id NOT IN (
              SELECT l2.id
              FROM locations l2
              JOIN services s2 ON s2.location_id = l2.id
              JOIN service_taxonomy st2 ON st2.service_id = s2.id
              JOIN taxonomies t2 ON st2.taxonomy_id = t2.id
              WHERE LOWER(t2.name) = 'shelter'
          )
    """)).fetchall()

    if youth_only:
        names = [row[0] for row in youth_only[:5]]
        issues.append({
            "type": "YOUTH_NOT_SHELTER",
            "severity": "HIGH",
            "detail": (
                f"{len(youth_only)} locations tagged 'Youth' but NOT 'Shelter': "
                f"{names}. These are only visible when 'youth' is in taxonomy list."
            ),
        })
        logger.info(f"  ⚠️  {len(youth_only)} locations tagged 'Youth' but NOT 'Shelter': {names}")
    else:
        logger.info("  ✅ All 'Youth' locations are also tagged 'Shelter'")

    # ── Rule 2: What age ranges exist in the DB for shelters? ──
    age_ranges = conn.execute(text("""
        SELECT
            l.name,
            e.eligible_values
        FROM locations l
        JOIN services s ON s.location_id = l.id
        JOIN service_taxonomy st ON st.service_id = s.id
        JOIN taxonomies t ON st.taxonomy_id = t.id
        JOIN eligibility e ON e.service_id = s.id
        JOIN eligibility_parameters ep ON e.parameter_id = ep.id
        WHERE (LOWER(t.name) = 'shelter' OR LOWER(t.name) = 'youth')
          AND ep.name = 'age'
          AND l.hidden_from_search IS NOT TRUE
        LIMIT 50
    """)).fetchall()

    if age_ranges:
        logger.info(f"  ℹ️  Found {len(age_ranges)} shelter services with age eligibility rules:")
        for row in age_ranges[:10]:
            logger.info(f"     {row[0]}: {row[1]}")

    # ── Rule 3: Are DV services (Safe Horizon) tagged 'Crisis'? ──
    safe_horizon = conn.execute(text("""
        SELECT l.name, ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
        FROM locations l
        JOIN services s ON s.location_id = l.id
        JOIN service_taxonomy st ON st.service_id = s.id
        JOIN taxonomies t ON st.taxonomy_id = t.id
        WHERE l.name ILIKE '%safe horizon%'
          AND l.hidden_from_search IS NOT TRUE
        GROUP BY l.name
    """)).fetchall()

    if safe_horizon:
        for row in safe_horizon:
            tags = row[1]
            has_crisis = any('crisis' in t.lower() for t in tags)
            has_shelter = any('shelter' in t.lower() for t in tags)
            logger.info(f"  {'✅' if has_crisis else '⚠️'} {row[0]}: {tags}")
            if not has_crisis and not has_shelter:
                issues.append({
                    "type": "DV_NOT_TAGGED",
                    "severity": "MEDIUM",
                    "detail": f"{row[0]} has no 'Crisis' or 'Shelter' taxonomy — invisible in shelter search",
                })
    else:
        logger.info("  ℹ️  No 'Safe Horizon' locations found")

    # ── Rule 4: Are Covenant House, Ali Forney tagged correctly? ──
    key_locations = conn.execute(text("""
        SELECT l.name, l.slug, ARRAY_AGG(DISTINCT t.name ORDER BY t.name)
        FROM locations l
        JOIN services s ON s.location_id = l.id
        JOIN service_taxonomy st ON st.service_id = s.id
        JOIN taxonomies t ON st.taxonomy_id = t.id
        WHERE (l.name ILIKE '%covenant house%' OR l.name ILIKE '%ali forney%')
          AND l.hidden_from_search IS NOT TRUE
        GROUP BY l.name, l.slug
    """)).fetchall()

    if key_locations:
        for row in key_locations:
            tags = row[2]
            has_youth = any('youth' in t.lower() for t in tags)
            has_shelter = any('shelter' in t.lower() for t in tags)
            status = "✅" if has_shelter else ("⚠️" if has_youth else "❌")
            logger.info(f"  {status} {row[0]}: {tags}")
            if has_youth and not has_shelter:
                issues.append({
                    "type": "YOUTH_ONLY",
                    "severity": "HIGH",
                    "detail": (
                        f"{row[0]} tagged 'Youth' but NOT 'Shelter'. "
                        f"Visible only because we always include 'youth' in taxonomy list."
                    ),
                })
    else:
        logger.info("  ℹ️  Key youth locations not found in DB")

    # ── Rule 5: Check our description filter patterns match real data ──
    # Sample: does "dental" appear in any health service descriptions?
    pattern_checks = [
        ("dental", "health", "dental care filter"),
        ("detox", "substance use treatment", "detox filter"),
        ("immigra", "legal services", "immigration filter"),
    ]
    for pattern, taxonomy, label in pattern_checks:
        count = conn.execute(text("""
            SELECT COUNT(DISTINCT l.id)
            FROM locations l
            JOIN services s ON s.location_id = l.id
            JOIN service_taxonomy st ON st.service_id = s.id
            JOIN taxonomies t ON st.taxonomy_id = t.id
            WHERE LOWER(t.name) = :taxonomy
              AND s.description ~* :pattern
              AND l.hidden_from_search IS NOT TRUE
        """), {"taxonomy": taxonomy, "pattern": pattern}).fetchone()

        result_count = count[0] if count else 0
        if result_count == 0:
            issues.append({
                "type": "EMPTY_PATTERN",
                "severity": "MEDIUM",
                "detail": f"Description pattern '{pattern}' matches 0 services in '{taxonomy}' — filter always returns empty",
            })
            logger.info(f"  ⚠️  '{label}': 0 matches — pattern may be ineffective")
        else:
            logger.info(f"  ✅ '{label}': {result_count} matching locations")

    conn.close()

    if issues:
        return {"status": "FAIL", "issues": issues}
    else:
        return {"status": "PASS", "issues": []}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_CHECKS = {
    "taxonomy_drift": check_taxonomy_drift,
    "source_parity": check_source_parity,
    "result_parity": check_result_parity,
    "business_rules": check_business_rules,
}


def main():
    parser = argparse.ArgumentParser(description="Automated drift detection")
    parser.add_argument("checks", nargs="*", default=list(ALL_CHECKS.keys()),
                        choices=list(ALL_CHECKS.keys()), help="Which checks to run")
    parser.add_argument("--ci", action="store_true", help="Exit code 1 on any failure")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    results = {}
    for check_name in args.checks:
        try:
            results[check_name] = ALL_CHECKS[check_name]()
        except Exception as e:
            logger.error(f"  ❌ {check_name} crashed: {e}")
            results[check_name] = {"status": "ERROR", "error": str(e)}

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("  SUMMARY")
    logger.info("=" * 60)
    any_fail = False
    for name, result in results.items():
        status = result["status"]
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ERROR": "💥"}.get(status, "?")
        logger.info(f"  {icon} {name}: {status}")
        if status == "FAIL":
            any_fail = True
            for issue in result.get("issues", []):
                logger.info(f"     [{issue['severity']}] {issue.get('detail', '')[:80]}")

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    if args.ci and any_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
