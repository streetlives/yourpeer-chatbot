"""
Tests for query_templates.py — validates SQL generation, taxonomy name
correctness, service card formatting, schedule status computation,
time formatting, and result deduplication.

All tests run without a database connection by inspecting generated SQL
strings, parameters, and calling pure functions with mock data.

Run with: python -m pytest tests/test_query_templates.py -v
Or just:  python tests/test_query_templates.py
"""

from datetime import time, datetime
from unittest.mock import patch

import pytest


from app.rag.query_templates import (
    build_query,
    build_relaxed_query,
    format_service_card,
    deduplicate_results,
    _compute_schedule_status,
    _format_time,
    TEMPLATES,
    _BASE_QUERY,
)


# -----------------------------------------------------------------------
# TAXONOMY NAME CORRECTNESS
# -----------------------------------------------------------------------
# Ground truth: all taxonomy names confirmed from the Streetlives DB
# via: SELECT DISTINCT t.name FROM taxonomies t JOIN service_taxonomy st ...
#
# Every name in a template's taxonomy_names list MUST appear here.
# If you add a new taxonomy alias, add it to this set too.

VALID_DB_TAXONOMY_NAMES = {
    # Food
    "Food", "Food Pantry", "Food Benefits", "Mobile Pantry",
    "Mobile Food Truck", "Mobile Market", "Food Delivery / Meals on Wheels",
    "Soup Kitchen", "Mobile Soup Kitchen", "Brown Bag", "Farmer's Markets",
    # Shelter
    "Shelter", "Transitional Independent Living (TIL)", "Supportive Housing",
    "Housing Lottery", "Veterans Short-Term Housing", "Warming Center", "Safe Haven",
    # Clothing
    "Clothing", "Clothing Pantry", "Interview-Ready Clothing",
    "Professional Clothing", "Coat Drive", "Thrift Shop",
    # Health
    "Health", "General Health", "Crisis",
    # Mental health
    "Mental Health", "Substance Use Treatment", "Residential Recovery", "Support Groups",
    # Legal
    "Legal Services", "Immigration Services",
    # Employment
    "Employment", "Internship",
    # Personal care
    "Personal Care", "Shower", "Laundry", "Toiletries", "Hygiene", "Haircut", "Restrooms",
    # Other
    "Other service", "Benefits", "Drop-in Center", "Case Workers", "Referral",
    "Education", "Mail", "Free Wifi", "Taxes", "Baby Supplies", "Baby",
    "Assessment", "Community Services", "Activities", "Appliances", "Gym",
    "Pets", "Single Adult", "Families", "Youth", "Senior", "Veterans",
    "LGBTQ Young Adult", "Intake",
}

# Expected taxonomy_names lists per template — ground truth from DB audit.
# Update this dict whenever the DB taxonomy schema changes.
EXPECTED_TAXONOMY_NAMES = {
    "food": {
        "food", "food pantry", "food benefits", "mobile pantry",
        "mobile food truck", "mobile market", "food delivery / meals on wheels",
        "soup kitchen", "mobile soup kitchen", "brown bag", "farmer's markets",
    },
    "shelter": {
        # Parent + generic housing types
        "shelter", "transitional independent living (til)", "supportive housing",
        "housing lottery", "veterans short-term housing", "warming center", "safe haven",
        # Population-specific shelter children (Apr 2026 YourPeer parity update):
        "youth", "families", "single adult", "senior", "lgbtq young adult", "veterans",
        # Service-type shelter children (Apr 16 DB verification — Covenant House /
        # Safe Horizon discoverability fix):
        "crisis", "drop-in center", "referral", "assessment", "residential recovery",
    },
    "clothing": {
        "clothing", "clothing pantry", "interview-ready clothing",
        "professional clothing", "coat drive", "thrift shop",
    },
    "medical": {
        # DB verified April 2026: Health parent + children (General Health,
        # Mental Health, Substance Use Treatment, Support Groups).
        # Mental Health excluded intentionally (handled by mental_health template).
        # "crisis" removed — was a BUG; Crisis is a Shelter child, not Health.
        "health", "general health", "substance use treatment", "support groups",
    },
    "legal": {
        "legal services", "immigration services",
    },
    "employment": {
        "employment", "internship",
    },
    "personal_care": {
        "personal care", "shower", "laundry", "toiletries",
        "hygiene", "haircut", "restrooms",
    },
    "mental_health": {
        "mental health", "substance use treatment",
        "residential recovery", "support groups",
    },
    "other": {
        "other service", "benefits", "drop-in center", "case workers", "referral",
        "education", "mail", "free wifi", "taxes", "baby supplies", "baby",
        "assessment", "community services", "activities", "appliances", "gym",
        "pets", "single adult", "families", "youth", "senior", "veterans",
        "lgbtq young adult", "intake",
    },
}


def test_all_templates_use_taxonomy_names_list():
    """Every template must use taxonomy_names (list), not the old taxonomy_name (string)."""
    for key, template in TEMPLATES.items():
        if key == "org_name":
            continue  # org_name searches by organization, not taxonomy
        params = template["default_params"]
        assert "taxonomy_names" in params, \
            f"Template '{key}' still uses old taxonomy_name (singular). " \
            f"Migrate to taxonomy_names list."
        assert "taxonomy_name" not in params, \
            f"Template '{key}' has both taxonomy_name and taxonomy_names — remove the old one."
        assert isinstance(params["taxonomy_names"], list), \
            f"Template '{key}' taxonomy_names must be a list, got {type(params['taxonomy_names'])}"
        assert len(params["taxonomy_names"]) > 0, \
            f"Template '{key}' taxonomy_names list is empty."


def test_all_taxonomy_names_are_lowercase():
    """All entries in taxonomy_names must be lowercase (for ANY() case-insensitive matching)."""
    for key, template in TEMPLATES.items():
        if key == "org_name":
            continue
        for name in template["default_params"]["taxonomy_names"]:
            assert name == name.lower(), \
                f"Template '{key}' has non-lowercase taxonomy name: '{name}'. " \
                f"All entries must be lowercase for ANY() matching."


def test_all_taxonomy_names_exist_in_db():
    """Every taxonomy name in every template must exist in the actual Streetlives DB."""
    valid_lower = {n.lower() for n in VALID_DB_TAXONOMY_NAMES}
    for key, template in TEMPLATES.items():
        if key == "org_name":
            continue
        for name in template["default_params"]["taxonomy_names"]:
            assert name in valid_lower, \
                f"Template '{key}' has taxonomy_name '{name}' not found in DB. " \
                f"Run the taxonomy audit query to verify it exists."


def test_no_taxonomy_name_duplicates_within_template():
    """No template should list the same taxonomy name twice."""
    for key, template in TEMPLATES.items():
        if key == "org_name":
            continue
        names = template["default_params"]["taxonomy_names"]
        assert len(names) == len(set(names)), \
            f"Template '{key}' has duplicate taxonomy names: {[n for n in names if names.count(n) > 1]}"


def test_no_taxonomy_name_in_wrong_template():
    """Mental Health itself must not appear in the medical template.

    Matches YourPeer's client-side exclusion: YourPeer's API returns Mental
    Health services for health-care queries, then filter_services_by_name
    strips them from the health-care view. The chatbot achieves the same
    by excluding 'mental health' from the medical template's default list.

    Substance Use Treatment and Support Groups ARE shared between medical
    and mental_health templates — DB verification (April 2026) confirmed
    they are parented under Health and legitimately belong to both
    categories. A user asking 'where can I see a doctor about my addiction'
    routes to medical and finds them; a user asking 'I need a support
    group' routes to mental_health and finds them.
    """
    health_names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
    mental_names = set(TEMPLATES["mental_health"]["default_params"]["taxonomy_names"])

    # Hard exclusion: Mental Health itself (128 services) must not appear in medical
    assert "mental health" not in health_names, \
        "'mental health' must only appear in mental_health template, not medical"

    # Documented intentional overlap
    expected_overlap = {"substance use treatment", "support groups"}
    actual_overlap = health_names & mental_names
    unexpected = actual_overlap - expected_overlap
    assert not unexpected, \
        f"Unexpected overlap between medical and mental_health: {unexpected}. " \
        f"Only {expected_overlap} should be shared."


def test_food_includes_soup_kitchen():
    """Soup Kitchen (180 services) must be in food template — biggest fix from DB audit."""
    names = TEMPLATES["food"]["default_params"]["taxonomy_names"]
    assert "soup kitchen" in names, "soup kitchen missing from food template"
    assert "mobile soup kitchen" in names, "mobile soup kitchen missing from food template"


def test_food_includes_food_pantry():
    """Food Pantry (732 services, largest category) must be in food template."""
    names = TEMPLATES["food"]["default_params"]["taxonomy_names"]
    assert "food pantry" in names, \
        "food pantry missing from food template — this is the largest food taxonomy (732 services)"


def test_shelter_includes_warming_center_and_safe_haven():
    """Warming Center and Safe Haven must be in shelter template."""
    names = TEMPLATES["shelter"]["default_params"]["taxonomy_names"]
    assert "warming center" in names, "warming center missing from shelter template"
    assert "safe haven" in names, "safe haven missing from shelter template"


def test_clothing_includes_clothing_pantry():
    """Clothing Pantry (84 services) must be in clothing template."""
    names = TEMPLATES["clothing"]["default_params"]["taxonomy_names"]
    assert "clothing pantry" in names, \
        "clothing pantry missing from clothing template — this caused 0 results for clothing in Queens"


def test_mental_health_includes_substance_use():
    """Substance Use Treatment must be in mental_health template."""
    names = TEMPLATES["mental_health"]["default_params"]["taxonomy_names"]
    assert "substance use treatment" in names, \
        "substance use treatment missing from mental_health template"


def test_legal_includes_immigration():
    """Immigration Services must be in legal template."""
    names = TEMPLATES["legal"]["default_params"]["taxonomy_names"]
    assert "immigration services" in names, \
        "immigration services missing from legal template"


def test_personal_care_includes_hygiene_and_haircut():
    """Hygiene and Haircut must be in personal_care template."""
    names = TEMPLATES["personal_care"]["default_params"]["taxonomy_names"]
    assert "hygiene" in names, "hygiene missing from personal_care template"
    assert "haircut" in names, "haircut missing from personal_care template"


def test_other_includes_benefits_and_drop_in():
    """Benefits and Drop-in Center must be in other template."""
    names = TEMPLATES["other"]["default_params"]["taxonomy_names"]
    assert "benefits" in names, "benefits missing from other template"
    assert "drop-in center" in names, "drop-in center missing from other template"


def test_exact_taxonomy_names_match_expected():
    """Each template's taxonomy_names must exactly match the DB-audited expected set.

    This is the regression guard — if someone adds or removes a taxonomy name,
    this test will fail and require an explicit update to EXPECTED_TAXONOMY_NAMES.
    """
    for key, expected in EXPECTED_TAXONOMY_NAMES.items():
        actual = set(TEMPLATES[key]["default_params"]["taxonomy_names"])
        missing = expected - actual
        extra = actual - expected
        assert not missing, \
            f"Template '{key}' is MISSING taxonomy names (add them): {missing}"
        assert not extra, \
            f"Template '{key}' has EXTRA taxonomy names not in DB audit (verify & update EXPECTED_TAXONOMY_NAMES): {extra}"


def test_taxonomy_aliases_match_taxonomy_names():
    """taxonomy_aliases must cover all taxonomy_names (case-insensitively).

    aliases are used in slot extraction; if a name is in the query but not
    the alias list, the chatbot may not route to the right template.
    """
    for key, template in TEMPLATES.items():
        if key == "org_name":
            continue
        names_lower = set(template["default_params"]["taxonomy_names"])
        aliases_lower = {a.lower() for a in template.get("taxonomy_aliases", [])}
        missing_from_aliases = names_lower - aliases_lower
        assert not missing_from_aliases, \
            f"Template '{key}' has taxonomy names not reflected in taxonomy_aliases: " \
            f"{missing_from_aliases}. Add them so slot extraction can route correctly."


# -----------------------------------------------------------------------
# BASE QUERY STRUCTURE
# -----------------------------------------------------------------------

def test_base_query_joins():
    """Base query must include all required table joins."""
    sql_lower = _BASE_QUERY.lower()
    # Core joins
    assert "join service_at_locations" in sql_lower
    assert "join locations" in sql_lower
    assert "left join organizations" in sql_lower
    assert "left join physical_addresses" in sql_lower
    # Taxonomy is now an EXISTS subquery in filters, not a base JOIN.
    # The also_available correlated subquery uses service_taxonomy but
    # is inside a SELECT subquery, not a FROM-level JOIN.
    # Check that the main FROM clause doesn't join service_taxonomy directly.
    from_clause = sql_lower.split("from services")[1].split("where")[0] if "where" in sql_lower else sql_lower.split("from services")[1]
    # Remove parenthesized subqueries from the from clause check
    import re
    from_no_subqueries = re.sub(r'\(select.*?\)', '', from_clause, flags=re.DOTALL)
    assert "join service_taxonomy" not in from_no_subqueries, \
        "Taxonomy should use EXISTS filter, not base JOIN (avoids row duplication)"
    # Schedule and membership use regular LEFT JOINs (not LATERAL)
    assert "left join holiday_schedules" in sql_lower
    assert "left join eligibility" in sql_lower


def test_base_query_phone_is_lateral():
    """Phone join should be LATERAL to prevent row multiplication."""
    sql_lower = _BASE_QUERY.lower()
    assert "lateral" in sql_lower, "Phone should use LATERAL join"
    assert "best_phone" in sql_lower, "Phone subquery should be aliased as best_phone"
    assert "limit 1" in sql_lower, "Phone subquery should LIMIT 1"


def test_base_query_phone_priority_order():
    """Phone LATERAL should prefer location > service > organization."""
    sql_lower = _BASE_QUERY.lower()
    # The CASE statement should order location first
    assert "when ph.location_id" in sql_lower
    assert "when ph.service_id" in sql_lower
    assert "when ph.organization_id" in sql_lower


def test_base_query_schedule_join():
    """Schedule should use a regular LEFT JOIN for today's hours."""
    sql_lower = _BASE_QUERY.lower()
    assert "today_sched" in sql_lower
    assert "left join holiday_schedules" in sql_lower
    assert "isodow" in sql_lower


def test_base_query_selects_slug():
    """Base query must select location slug for YourPeer URL."""
    sql_lower = _BASE_QUERY.lower()
    assert "l.slug" in sql_lower
    assert "location_slug" in sql_lower


# -----------------------------------------------------------------------
# SERVICE CARD FORMATTING
# -----------------------------------------------------------------------

def _mock_row(**overrides):
    """Build a mock DB result row."""
    base = {
        "service_id": "test-uuid-123",
        "service_name": "Test Food Pantry",
        "service_description": "Free food distribution",
        "fees": "Free",
        "service_url": "https://example.com",
        "service_email": "info@example.com",
        "organization_name": "Test Org",
        "organization_url": "https://testorg.com",
        "location_id": "loc-uuid-456",
        "location_name": "Main Office",
        "location_slug": "test-food-pantry-brooklyn",
        "address": "123 Main Street",
        "city": "Brooklyn",
        "state": "NY",
        "zip_code": "11201",
        "latitude": 40.6826,
        "longitude": -73.9754,
        "phone": "212-555-0001",
        "today_opens": None,
        "today_closes": None,
        "requires_membership": None,
    }
    base.update(overrides)
    return base


def test_format_card_all_fields():
    """Service card should include all fields from a complete row."""
    card = format_service_card(_mock_row())
    assert card["service_name"] == "Test Food Pantry"
    assert card["organization"] == "Test Org"
    assert card["phone"] == "212-555-0001"
    assert "123 Main Street" in card["address"]
    assert "Brooklyn" in card["address"]
    assert "NY" in card["address"]
    assert card["fees"] == "Free"
    assert card["email"] == "info@example.com"


def test_format_card_yourpeer_url():
    """Card should build YourPeer URL from location slug."""
    card = format_service_card(_mock_row(location_slug="my-location"))
    assert card["yourpeer_url"] == "https://yourpeer.nyc/locations/my-location"


def test_format_card_no_slug():
    """Card should have None yourpeer_url if no slug."""
    card = format_service_card(_mock_row(location_slug=None))
    assert card["yourpeer_url"] is None


def test_format_card_missing_optional_fields():
    """Card should handle missing optional fields gracefully."""
    card = format_service_card(_mock_row(
        organization_name=None,
        phone=None,
        service_email=None,
        fees=None,
        service_description=None,
    ))
    assert card["service_name"] == "Test Food Pantry"
    assert card["organization"] is None
    assert card["phone"] is None
    assert card["email"] is None
    assert card["fees"] is None


def test_format_card_website_fallback():
    """Card should fall back to org URL if service URL is missing."""
    card = format_service_card(_mock_row(service_url=None, organization_url="https://org.com"))
    assert card["website"] == "https://org.com"


def test_format_card_website_prefers_service():
    """Card should prefer service URL over org URL."""
    card = format_service_card(_mock_row(
        service_url="https://service.com",
        organization_url="https://org.com",
    ))
    assert card["website"] == "https://service.com"


def test_format_card_website_normalizes_missing_protocol():
    """URLs without a protocol should get https:// prepended."""
    # Bare domain
    card = format_service_card(_mock_row(service_url="www.example.com", organization_url=None))
    assert card["website"] == "https://www.example.com"

    # Domain with path
    card = format_service_card(_mock_row(service_url="example.org/services", organization_url=None))
    assert card["website"] == "https://example.org/services"

    # Org URL fallback also normalized
    card = format_service_card(_mock_row(service_url=None, organization_url="org.example.com"))
    assert card["website"] == "https://org.example.com"

    # Already has https — no change
    card = format_service_card(_mock_row(service_url="https://already-good.com"))
    assert card["website"] == "https://already-good.com"

    # Already has http — no change
    card = format_service_card(_mock_row(service_url="http://legacy.com"))
    assert card["website"] == "http://legacy.com"

    # Protocol-relative — no change
    card = format_service_card(_mock_row(service_url="//cdn.example.com"))
    assert card["website"] == "//cdn.example.com"

    # None/empty → None
    card = format_service_card(_mock_row(service_url=None, organization_url=None))
    assert card["website"] is None

    card = format_service_card(_mock_row(service_url="", organization_url=""))
    assert card["website"] is None

    # Whitespace-only → None
    card = format_service_card(_mock_row(service_url="  ", organization_url=None))
    assert card["website"] is None



def test_format_card_no_address():
    """Card address should be None if all address parts are missing."""
    card = format_service_card(_mock_row(
        address=None, city=None, state=None, zip_code=None,
    ))
    assert card["address"] is None


def test_format_card_partial_address():
    """Card should build address from whatever parts are available."""
    card = format_service_card(_mock_row(address=None, state=None, zip_code=None))
    assert card["address"] == "Brooklyn"


def test_format_card_default_service_name():
    """Card should show 'Unknown Service' if service_name is missing."""
    card = format_service_card(_mock_row(service_name=None))
    assert card["service_name"] == "Unknown Service"


# -----------------------------------------------------------------------
# COORDINATES — lat/lon from PostGIS l.position projection
# -----------------------------------------------------------------------
# Added when the geographic borough validator shipped (see
# docs/audits/BOUNDARY_AUDIT.md). The base SELECT projects ST_Y/ST_X on
# l.position so the validator in query_executor can read coordinates
# off each card without a second query.

def test_format_card_includes_lat_lon():
    """Card should carry latitude and longitude from the row."""
    card = format_service_card(_mock_row(latitude=40.7484, longitude=-73.9857))
    assert card["latitude"] == 40.7484
    assert card["longitude"] == -73.9857


def test_format_card_handles_missing_coordinates():
    """Services without l.position (legacy / manual entries) get None."""
    card = format_service_card(_mock_row(latitude=None, longitude=None))
    assert card["latitude"] is None
    assert card["longitude"] is None


def test_format_card_coerces_decimal_coordinates():
    """psycopg2 may surface PostGIS doubles as Decimal; card stores float."""
    from decimal import Decimal
    card = format_service_card(_mock_row(
        latitude=Decimal("40.7484"),
        longitude=Decimal("-73.9857"),
    ))
    assert isinstance(card["latitude"], float)
    assert isinstance(card["longitude"], float)
    assert card["latitude"] == 40.7484
    assert card["longitude"] == -73.9857


def test_format_card_handles_invalid_coord_types():
    """Unexpected types (e.g., strings from a miswritten query) fall back to None."""
    card = format_service_card(_mock_row(
        latitude="not a number",
        longitude="also not",
    ))
    assert card["latitude"] is None
    assert card["longitude"] is None


def test_base_query_projects_lat_lon():
    """The generated SQL must include ST_Y/ST_X projections so the
    executor's validator can read coords off each row. If this stops,
    the validator silently degrades (all cards look like they're missing
    coords — no mismatches flagged)."""
    sql, _ = build_query("food", {})
    assert "ST_Y(l.position::geometry) AS latitude" in sql, (
        "Base query missing latitude projection — geographic borough "
        "validator won't see any coordinates"
    )
    assert "ST_X(l.position::geometry) AS longitude" in sql, (
        "Base query missing longitude projection"
    )


# -----------------------------------------------------------------------
# SCHEDULE STATUS
# -----------------------------------------------------------------------

def test_schedule_none_values():
    """None opens/closes should return no schedule data."""
    result = _compute_schedule_status(None, None)
    assert result["hours_today"] is None
    assert result["is_open"] is None


def test_schedule_one_none():
    """One None value should return no schedule data."""
    assert _compute_schedule_status("09:00:00", None)["is_open"] is None
    assert _compute_schedule_status(None, "17:00:00")["is_open"] is None


def test_schedule_string_times():
    """String time values (from DB) should parse correctly."""
    result = _compute_schedule_status("09:00:00", "17:00:00")
    assert result["hours_today"] == "9:00 AM – 5:00 PM"
    assert result["is_open"] in ("open", "closed")  # depends on current time


def test_schedule_time_objects():
    """Python time objects should work."""
    result = _compute_schedule_status(time(9, 0), time(17, 0))
    assert result["hours_today"] == "9:00 AM – 5:00 PM"


def test_schedule_midnight_wrap():
    """Overnight schedules (e.g. 8PM-6AM) should format correctly."""
    result = _compute_schedule_status(time(20, 0), time(6, 0))
    assert result["hours_today"] == "8:00 PM – 6:00 AM"
    assert result["is_open"] in ("open", "closed")


def test_schedule_invalid_string():
    """Invalid time strings should return no data, not crash."""
    result = _compute_schedule_status("not-a-time", "also-bad")
    assert result["hours_today"] is None
    assert result["is_open"] is None


def test_schedule_mixed_types():
    """Mixed string + time object should work."""
    result = _compute_schedule_status("09:00:00", time(17, 0))
    assert result["hours_today"] == "9:00 AM – 5:00 PM"


def test_schedule_with_card():
    """Schedule data should flow through to the service card."""
    card = format_service_card(_mock_row(
        today_opens=time(9, 0),
        today_closes=time(17, 0),
    ))
    assert card["hours_today"] == "9:00 AM – 5:00 PM"
    assert card["is_open"] in ("open", "closed")


def test_schedule_no_data_in_card():
    """Card with no schedule data should show None."""
    card = format_service_card(_mock_row())
    assert card["hours_today"] is None
    assert card["is_open"] is None


# -----------------------------------------------------------------------
# TIME FORMATTING
# -----------------------------------------------------------------------

def test_format_time_morning():
    assert _format_time(time(9, 0)) == "9:00 AM"
    assert _format_time(time(9, 30)) == "9:30 AM"


def test_format_time_afternoon():
    assert _format_time(time(14, 0)) == "2:00 PM"
    assert _format_time(time(17, 45)) == "5:45 PM"


def test_format_time_noon():
    assert _format_time(time(12, 0)) == "12:00 PM"


def test_format_time_midnight():
    assert _format_time(time(0, 0)) == "12:00 AM"


def test_format_time_just_after_midnight():
    assert _format_time(time(0, 30)) == "12:30 AM"


def test_format_time_no_leading_zero():
    """Single-digit hours should NOT have a leading zero."""
    result = _format_time(time(9, 0))
    assert not result.startswith("0"), f"Leading zero in: {result}"
    result2 = _format_time(time(1, 0))
    assert not result2.startswith("0"), f"Leading zero in: {result2}"


# -----------------------------------------------------------------------
# DEDUPLICATION
# -----------------------------------------------------------------------

def test_deduplicate_removes_dupes():
    """Rows with the same service_id should be collapsed to one."""
    rows = [
        {"service_id": "aaa", "phone": "111"},
        {"service_id": "aaa", "phone": "222"},
        {"service_id": "bbb", "phone": "333"},
    ]
    result = deduplicate_results(rows)
    assert len(result) == 2
    assert result[0]["service_id"] == "aaa"
    assert result[1]["service_id"] == "bbb"


def test_deduplicate_keeps_first():
    """Should keep the first occurrence of each service_id."""
    rows = [
        {"service_id": "aaa", "phone": "first"},
        {"service_id": "aaa", "phone": "second"},
    ]
    result = deduplicate_results(rows)
    assert len(result) == 1
    assert result[0]["phone"] == "first"


def test_deduplicate_empty():
    """Empty list should return empty list."""
    assert deduplicate_results([]) == []


def test_deduplicate_no_service_id():
    """Rows without service_id should be skipped."""
    rows = [
        {"service_id": None, "phone": "111"},
        {"service_id": "aaa", "phone": "222"},
    ]
    result = deduplicate_results(rows)
    assert len(result) == 1
    assert result[0]["service_id"] == "aaa"


def test_deduplicate_all_unique():
    """All-unique rows should pass through unchanged."""
    rows = [
        {"service_id": "aaa", "phone": "111"},
        {"service_id": "bbb", "phone": "222"},
        {"service_id": "ccc", "phone": "333"},
    ]
    result = deduplicate_results(rows)
    assert len(result) == 3


# -----------------------------------------------------------------------
# GENERATED SQL VALIDATION
# -----------------------------------------------------------------------

def test_generated_sql_is_parameterized():
    """Generated SQL should use :param placeholders, never string interpolation."""
    for key in TEMPLATES:
        if key == "org_name":
            continue  # org_name uses ILIKE, not taxonomy_names
        sql, params = build_query(key, {"city": "Brooklyn", "age": 17, "max_results": 5})
        # All templates now use taxonomy_names list with ANY()
        assert ":taxonomy_names" in sql, \
            f"Template '{key}' SQL missing :taxonomy_names placeholder"
        assert ":max_results" in sql
        # Should NOT have raw values injected
        assert "'Brooklyn'" not in sql, f"Template '{key}' has raw value in SQL"
        assert "17" not in sql.split("ISODOW")[0], \
            f"Template '{key}' may have raw age in SQL (check carefully)"


def test_both_city_and_city_like_dont_conflict():
    """When strict query has exact city, LIKE pattern should NOT also be present."""
    sql, params = build_query("food", {"city": "Brooklyn", "max_results": 5})
    # Should have exact city but NOT city_pattern (that's for relaxed only)
    assert "city" in params
    assert "city_pattern" not in params


def test_relaxed_has_city_pattern_not_city():
    """Relaxed query should swap city for city_pattern."""
    sql, params = build_relaxed_query("food", {"city": "Brooklyn", "max_results": 5})
    assert "city_pattern" in params
    assert "city" not in params
    assert params["city_pattern"] == "%Brooklyn%"


def test_unknown_template_raises():
    """build_query with unknown template key should raise ValueError."""
    try:
        build_query("nonexistent", {"max_results": 5})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "nonexistent" in str(e)


# -----------------------------------------------------------------------
# BOROUGH FILTERING — via pa.city (pa.borough does NOT exist in prod)
# -----------------------------------------------------------------------
# HISTORY: Previously this file had tests asserting that every template
# MUST include FILTER_BY_BOROUGH (matching pa.borough). Those tests
# passed because they only inspected the Python SQL string, never
# executing it. In prod the query raised
#   psycopg2.errors.UndefinedColumn: column pa.borough does not exist
# on every borough-level search, the exception was swallowed by
# _execute_sql's generic handler, and the user got the relaxed-query
# fallback (adding an unnecessary "I broadened the search a bit"
# message to every direct borough lookup).
#
# Removed Apr 17, 2026. All borough filtering now uses
# FILTER_BY_CITY_IN_BOROUGH (pa.city = ANY(:city_list)) — the only
# filter that has ever actually worked. See docs/audits/BOUNDARY_AUDIT.md.
#
# The tests below are guards against regression: they assert the
# broken filter stays GONE, and that borough searches use city_list.

def test_filter_by_borough_not_exported():
    """FILTER_BY_BOROUGH must not exist — pa.borough is not a real column.

    Re-introducing it would resurface the UndefinedColumn error storm.
    """
    import app.rag.query_templates as qt
    assert not hasattr(qt, "FILTER_BY_BOROUGH"), (
        "FILTER_BY_BOROUGH was re-added. pa.borough does not exist in the "
        "Streetlives DB; see docs/audits/BOUNDARY_AUDIT.md. Use "
        "FILTER_BY_CITY_IN_BOROUGH (pa.city = ANY(:city_list)) instead."
    )


def test_no_template_references_pa_borough():
    """No template's generated SQL may reference pa.borough."""
    for key in TEMPLATES:
        sql, _ = build_query(key, {})
        assert "pa.borough" not in sql, (
            f"Template '{key}' references pa.borough in generated SQL — "
            f"that column does not exist in prod. See BOUNDARY_AUDIT.md."
        )


def test_borough_search_uses_city_list():
    """A borough-level search must emit pa.city = ANY(...) — no pa.borough."""
    sql, params = build_query("food", {
        "city_list": ["queens", "jamaica", "flushing", "astoria"],
        "max_results": 5,
    })
    assert "LOWER(pa.city) = ANY(:city_list)" in sql
    assert "pa.borough" not in sql
    assert params["city_list"] == ["queens", "jamaica", "flushing", "astoria"]


def test_relaxed_query_does_not_reintroduce_borough():
    """Relaxed queries must not emit pa.borough either."""
    sql, _ = build_relaxed_query("food", {
        "city_list": ["queens", "jamaica", "flushing"],
        "max_results": 5,
    })
    assert "pa.borough" not in sql


# -----------------------------------------------------------------------
# BOROUGH NORMALIZATION (query_executor)
# -----------------------------------------------------------------------

def test_normalize_borough_names():
    """Borough name input passes through normalize_location title-cased.

    These values are then consumed by get_borough_city_names which maps
    them to primary city values via _BOROUGH_TO_PRIMARY_CITY.
    """
    from app.rag.query_executor import normalize_location
    assert normalize_location("manhattan") == "Manhattan"
    assert normalize_location("Brooklyn") == "Brooklyn"
    assert normalize_location("queens") == "Queens"
    assert normalize_location("bronx") == "Bronx"
    assert normalize_location("the bronx") == "Bronx"
    assert normalize_location("staten island") == "Staten Island"


def test_normalize_then_expand_manhattan_pipeline():
    """Borough search pipeline: "manhattan" → "Manhattan" → city_list
    containing "new york".

    The full flow: user types a borough name, normalize_location returns
    the title-cased borough, get_borough_city_names walks it through
    _BOROUGH_TO_PRIMARY_CITY ("Manhattan" → "New York") and returns the
    full list of Manhattan city values seen in pa.city. This is how
    borough-level filtering actually works in prod — via pa.city, not
    pa.borough (which doesn't exist).
    """
    from app.rag.query_executor import normalize_location, get_borough_city_names
    normalized = normalize_location("manhattan")
    assert normalized == "Manhattan"
    cities = get_borough_city_names(normalized)
    assert "new york" in cities, (
        "Manhattan must expand to include 'new york' — the primary DB "
        "city value for Manhattan addresses"
    )


def test_get_borough_city_names_manhattan():
    """Manhattan borough must expand to New York city values for city-field fallback."""
    from app.rag.query_executor import get_borough_city_names
    cities = get_borough_city_names("Manhattan")
    assert "new york" in cities, \
        "Manhattan city expansion must include 'new york' for pa.city fallback queries"


def test_get_borough_city_names_queens():
    """Queens borough must expand to all Queens neighborhood city values."""
    from app.rag.query_executor import get_borough_city_names
    cities = get_borough_city_names("Queens")
    for expected in ["queens", "jamaica", "flushing", "astoria", "long island city"]:
        assert expected in cities, \
            f"Queens city expansion missing '{expected}'"


def test_is_borough_all_five():
    """is_borough must return True for all five NYC boroughs."""
    from app.rag.query_executor import is_borough
    for b in ["manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island",
              "Manhattan", "QUEENS", "The Bronx"]:
        assert is_borough(b), f"is_borough('{b}') returned False"


def test_is_borough_false_for_neighborhoods():
    """is_borough must return False for neighborhoods."""
    from app.rag.query_executor import is_borough
    for n in ["harlem", "williamsburg", "astoria", "jamaica", "chelsea", ""]:
        assert not is_borough(n), f"is_borough('{n}') returned True — should be False"


# -----------------------------------------------------------------------
# MEMBERSHIP / REFERRAL BADGE
# -----------------------------------------------------------------------
# DB audit (Apr 2026): 624 services have membership = ["true"] (referral
# required). 635 have ["true","false"] (open to all). We surface a badge
# on the card rather than filtering — silently excluding 624 services would
# significantly reduce results for vulnerable users.

def test_requires_membership_true_when_true_only():
    """Card must set requires_membership=True when eligible_values is ["true"] only."""
    card = format_service_card(_mock_row(requires_membership=True))
    assert card["requires_membership"] is True, \
        "requires_membership should be True when DB returns True"


def test_requires_membership_false_when_null():
    """Card must set requires_membership=False when no membership rule exists (NULL from LATERAL)."""
    card = format_service_card(_mock_row(requires_membership=None))
    assert card["requires_membership"] is False, \
        "requires_membership should be False when DB returns NULL (no rule)"


def test_requires_membership_false_when_false():
    """Card must set requires_membership=False when membership allows non-members."""
    card = format_service_card(_mock_row(requires_membership=False))
    assert card["requires_membership"] is False, \
        "requires_membership should be False when DB returns False (['true','false'])"


def test_requires_membership_always_present_in_card():
    """requires_membership key must always be present in the card dict."""
    card = format_service_card(_mock_row())
    assert "requires_membership" in card, \
        "requires_membership field missing from service card — frontend badge logic will break"


def test_base_query_selects_requires_membership():
    """Base query must select requires_membership from the membership LEFT JOIN."""
    assert "requires_membership" in _BASE_QUERY.lower(), \
        "Base query missing requires_membership field"
    assert "membership_elig" in _BASE_QUERY.lower(), \
        "Base query missing membership_elig join alias"
    assert "eligibility_parameters" in _BASE_QUERY.lower(), \
        "Base query missing eligibility_parameters join in membership LATERAL"


# -----------------------------------------------------------------------
# SCHEDULE FILTERS — open-now safety
# -----------------------------------------------------------------------
# DB audit (Apr 2026): schedule data is only populated for walk-in services.
# Most categories have 0% coverage. Applying schedule filters broadly would
# silently exclude the majority of the DB.
#
# Rules enforced here:
#   - FILTER_BY_OPEN_NOW only fires when BOTH weekday AND current_time present
#   - FILTER_BY_WEEKDAY fires with weekday alone (distinct, documented intent)
#   - Relaxed query always drops both schedule params
#   - No template has schedule filters as required (always optional)

def test_open_now_requires_both_weekday_and_current_time():
    """FILTER_BY_OPEN_NOW must only fire when both weekday AND current_time are present.

    Passing just one must not trigger it — that would silently exclude services
    with no schedule rows, which is the majority of the DB for most categories.

    We check bound params rather than SQL text because opens_at/closes_at appear
    in the base query's display LATERAL and in FILTER_BY_OPEN_NOW's subquery,
    making SQL-text scanning ambiguous.
    """
    _, params_weekday_only = build_query("food", {"weekday": 1, "max_results": 5})
    _, params_time_only = build_query("food", {"current_time": "14:00", "max_results": 5})
    _, params_neither = build_query("food", {"max_results": 5})
    _, params_both = build_query("food", {"weekday": 1, "current_time": "14:00", "max_results": 5})

    assert "current_time" not in params_weekday_only, \
        "current_time appeared in params with weekday only — open-now filter should not fire"
    assert "weekday" not in params_time_only, \
        "weekday appeared in params with current_time only — open-now filter should not fire"
    assert "weekday" not in params_neither and "current_time" not in params_neither, \
        "schedule params appeared with no schedule input"
    assert "weekday" in params_both and "current_time" in params_both, \
        "open-now filter did not bind both params when both provided"


def test_weekday_filter_fires_without_current_time():
    """FILTER_BY_WEEKDAY fires with weekday alone — distinct from open-now.

    This is intentional: weekday-only filters to services operating on a given
    day, without requiring a specific time. Documents the distinction explicitly.
    """
    _, params = build_query("shelter", {"weekday": 0, "max_results": 5})
    assert "weekday" in params, \
        "weekday param missing from bound params — FILTER_BY_WEEKDAY did not fire"
    assert "current_time" not in params, \
        "current_time appeared without being passed — open-now filter should not have fired"


def test_relaxed_query_drops_schedule_params():
    """Relaxed query must always drop weekday and current_time.

    Schedule filters would silently exclude services with no schedule rows.
    The relaxed path must broaden — never further restrict.
    """
    _, params = build_relaxed_query("food", {
        "borough": "Queens",
        "weekday": 2,
        "current_time": "10:00",
        "max_results": 5,
    })
    assert "weekday" not in params, \
        "Relaxed query kept weekday param — must drop all schedule filters"
    assert "current_time" not in params, \
        "Relaxed query kept current_time param — must drop all schedule filters"


def test_schedule_filters_are_optional_not_required():
    """No template should have FILTER_BY_OPEN_NOW or FILTER_BY_WEEKDAY in required_filters.

    Making schedule filters required would break every query for the ~80% of
    services with no schedule data.
    """
    from app.rag.query_templates import FILTER_BY_OPEN_NOW, FILTER_BY_WEEKDAY
    for key, template in TEMPLATES.items():
        required = template["required_filters"]
        assert FILTER_BY_OPEN_NOW not in required, \
            f"Template '{key}' has FILTER_BY_OPEN_NOW in required_filters — must be optional only"
        assert FILTER_BY_WEEKDAY not in required, \
            f"Template '{key}' has FILTER_BY_WEEKDAY in required_filters — must be optional only"


def test_no_schedule_data_card_is_none():
    """Service card with no schedule rows must have is_open=None and hours_today=None.

    The frontend uses is_open=None to show 'Call for hours' badge.
    Ensure the card never fabricates an open/closed status from missing data.
    """
    card = format_service_card(_mock_row(today_opens=None, today_closes=None))
    assert card["is_open"] is None, \
        f"is_open should be None when no schedule data, got '{card['is_open']}'"
    assert card["hours_today"] is None, \
        f"hours_today should be None when no schedule data, got '{card['hours_today']}'"


# -----------------------------------------------------------------------
# RESULT SORTING
# -----------------------------------------------------------------------
# Results are sorted with three tiers:
#   1. Open now (services currently open appear first)
#   2. Recently verified (l.last_validated_at DESC NULLS LAST)
#   3. Service name (stable tiebreaker)
# When proximity (lat/lon) is active, distance is the primary sort.

# Results sorting — as of Apr 16, 2026:
#   Python (query_executor._sort_open_first) — open-now sort (single source of truth)
#   SQL _BASE_ORDER_PARTS:
#     1. Recently verified (l.last_validated_at DESC NULLS LAST)
#     2. Service name (stable tiebreaker)
# When proximity (lat/lon) is active, distance is the primary SQL sort.
#
# Prior to Apr 16, 2026, SQL also ranked open services first. The SQL rank
# was redundant with the Python stable sort that always ran afterward and
# differed in its treatment of unknown-schedule services (SQL: unknown=closed;
# Python: unknown<closed). Removed for single-source-of-truth.

def test_default_order_uses_freshness_first():
    """Default ORDER BY (no proximity) should sort by freshness then name.

    Open-now sorting is handled in Python post-query — SQL does not
    contribute to open-status ordering. See _sort_open_first in
    query_executor.py.
    """
    sql, _ = build_query("food", {"borough": "Brooklyn", "max_results": 5})
    # Should NOT contain the SQL open-now CASE (moved to Python)
    assert "CURRENT_TIME" not in sql, (
        "ORDER BY should not include SQL open-now rank — Python is the single "
        "source of truth for open-status sorting (Apr 16, 2026)."
    )
    assert "last_validated_at" in sql, "ORDER BY should include freshness sort"
    # No proximity → no distance
    assert "ST_Distance" not in sql


def test_proximity_order_uses_distance_first():
    """When lat/lon present, distance should be the primary SQL sort.

    Open-now is still applied post-query in Python.
    """
    sql, _ = build_query("food", {
        "lat": 40.69, "lon": -73.99, "radius_meters": 1600, "max_results": 5,
    })
    assert "ST_Distance" in sql, "Proximity query should sort by distance"
    assert "last_validated_at" in sql, "Proximity query should also sort by freshness"
    # SQL should not rank open-now
    assert "CURRENT_TIME" not in sql, (
        "Proximity query should not include SQL open-now rank — Python handles it."
    )


def test_freshness_is_primary_non_proximity_sort():
    """last_validated_at should be the first non-distance sort key."""
    sql, _ = build_query("food", {"borough": "Manhattan", "max_results": 5})
    # Only inspect the ORDER BY section (after the last WHERE clause)
    order_section = sql[sql.rindex("ORDER BY"):]
    fresh_pos = order_section.index("last_validated_at")
    name_pos = order_section.index("s.name")
    assert fresh_pos < name_pos, "Freshness should sort before name in ORDER BY"


def test_base_query_selects_last_validated_at():
    """The base query SELECT should include last_validated_at for sorting."""
    assert "last_validated_at" in _BASE_QUERY, \
        "last_validated_at must be in the base SELECT for ORDER BY to reference it"


# -----------------------------------------------------------------------
# Bucketed distance sort (see docs/design/BUCKETED_DISTANCE_SORT_SPEC.md)
# -----------------------------------------------------------------------
# When proximity search is active, the SQL ORDER BY splits distance into:
#   (1) a 4-band rank (0/1/2/3 for <500m / 500m-1km / 1km-2km / 2km+)
#       used BEFORE freshness — so services in closer bands always win
#   (2) continuous distance as a tiebreaker AFTER freshness — so within
#       the same band and freshness tier, closer still beats farther
# The result: freshness can re-order "equally walkable" services without
# a 1-meter distance difference dominating the sort.

def test_distance_band_rank_has_four_buckets():
    """The band CASE expression should define exactly 4 bands (0/1/2/3)."""
    from app.rag.query_templates import _DISTANCE_BAND_RANK
    assert "THEN 0" in _DISTANCE_BAND_RANK, "Band 0 (<500m) missing"
    assert "THEN 1" in _DISTANCE_BAND_RANK, "Band 1 (500m-1km) missing"
    assert "THEN 2" in _DISTANCE_BAND_RANK, "Band 2 (1km-2km) missing"
    assert "ELSE 3" in _DISTANCE_BAND_RANK, "Band 3 (2km+) missing"


def test_distance_band_thresholds_are_500_1000_2000():
    """Band thresholds match the spec: 500m, 1000m, 2000m."""
    from app.rag.query_templates import _DISTANCE_BAND_RANK
    assert "< 500" in _DISTANCE_BAND_RANK, "First threshold should be 500m"
    assert "< 1000" in _DISTANCE_BAND_RANK, "Second threshold should be 1000m (1km)"
    assert "< 2000" in _DISTANCE_BAND_RANK, "Third threshold should be 2000m (2km)"


def test_proximity_order_uses_distance_band_before_freshness():
    """When lat/lon present, the distance BAND should sort BEFORE freshness.

    This is the key behavior of bucketed distance: closer bands always win,
    but within a band, freshness takes over.
    """
    sql, _ = build_query("food", {
        "lat": 40.69, "lon": -73.99, "radius_meters": 1600, "max_results": 5,
    })
    order_section = sql[sql.rindex("ORDER BY"):]
    # Band CASE has the characteristic "< 500 THEN 0" marker
    assert "< 500 THEN 0" in order_section, \
        "Proximity ORDER BY should include distance band CASE"
    band_pos = order_section.index("< 500 THEN 0")
    fresh_pos = order_section.index("last_validated_at")
    assert band_pos < fresh_pos, \
        "Distance band should sort BEFORE freshness — closer bands always win"


def test_proximity_order_uses_continuous_distance_after_freshness():
    """Continuous distance should be a tiebreaker AFTER freshness, BEFORE name.

    Within the same distance band and freshness tier, the physically
    closer service should still sort first — but freshness has already
    broken ties within the band.
    """
    sql, _ = build_query("food", {
        "lat": 40.69, "lon": -73.99, "radius_meters": 1600, "max_results": 5,
    })
    order_section = sql[sql.rindex("ORDER BY"):]
    fresh_pos = order_section.index("last_validated_at")
    # Find the continuous ST_Distance (not the ones inside the CASE).
    # Count occurrences: the band CASE has 3, then 1 standalone tiebreaker.
    # So the last occurrence is the tiebreaker.
    last_dist_pos = order_section.rindex("ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)")
    name_pos = order_section.index("s.name")
    assert fresh_pos < last_dist_pos, \
        "Continuous distance tiebreaker should come AFTER freshness"
    assert last_dist_pos < name_pos, \
        "Continuous distance tiebreaker should come BEFORE name"


def test_proximity_order_full_key_sequence():
    """Full key sequence for proximity + lgbtq boost:
    boost → band → freshness → continuous distance → name.
    """
    sql, _ = build_query("shelter", {
        "lat": 40.72, "lon": -74.00, "radius_meters": 1600, "max_results": 5,
        "lgbtq_boost": True,
    })
    order_section = sql[sql.rindex("ORDER BY"):]
    boost_pos = order_section.index("lgbtq young adult")
    band_pos = order_section.index("< 500 THEN 0")
    fresh_pos = order_section.index("last_validated_at")
    last_dist_pos = order_section.rindex("ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)")
    name_pos = order_section.index("s.name")
    positions = [boost_pos, band_pos, fresh_pos, last_dist_pos, name_pos]
    assert positions == sorted(positions), (
        "Order must be: boost → band → freshness → continuous distance → name. "
        f"Got positions: {positions}"
    )


def test_non_proximity_order_unaffected_by_band_change():
    """Non-proximity queries should not contain any distance rank at all.

    Regression guard: we must not accidentally emit band or continuous
    distance ORDER BY keys when lat/lon are absent.
    """
    sql, _ = build_query("food", {"borough": "Brooklyn", "max_results": 5})
    order_section = sql[sql.rindex("ORDER BY"):]
    assert "ST_Distance" not in order_section, \
        "Non-proximity query should not include ST_Distance in ORDER BY"
    assert "< 500 THEN 0" not in order_section, \
        "Non-proximity query should not include distance band CASE"


def test_relaxed_query_drops_distance_band():
    """Relaxed queries drop lat/lon — so no band rank, no continuous distance."""
    sql, _ = build_relaxed_query("food", {
        "lat": 40.72, "lon": -74.00, "radius_meters": 1600,
        "_borough_city_list": ["new york", "manhattan"],
        "max_results": 5,
    })
    order_section = sql[sql.rindex("ORDER BY"):]
    assert "ST_Distance" not in order_section, \
        "Relaxed query strips proximity, so no ST_Distance in ORDER BY"
    assert "last_validated_at" in order_section, \
        "Relaxed query should still sort by freshness"


def test_relaxed_query_keeps_sort_order():
    """Relaxed queries should maintain the same sort priority (no SQL open-now)."""
    sql, _ = build_relaxed_query("food", {
        "borough": "Brooklyn", "max_results": 5,
    })
    assert "last_validated_at" in sql, "Relaxed query should still sort by freshness"
    assert "CURRENT_TIME" not in sql, (
        "Relaxed query should not include SQL open-now rank (Python handles it)."
    )


def test_no_template_has_sql_open_now_sort():
    """No template's generated SQL should include the SQL open-now CASE.

    Python's _sort_open_first is the single source of truth for open-status
    ordering as of Apr 16, 2026. If any template introduces a SQL-level rank,
    it risks drift from the Python sort and re-creates the dual-source
    inconsistency that was just cleaned up.
    """
    for key in TEMPLATES:
        sql, _ = build_query(key, {"borough": "Brooklyn", "max_results": 5})
        assert "CURRENT_TIME" not in sql, (
            f"Template '{key}' includes CURRENT_TIME in SQL — this is the "
            f"signature of the SQL open-now rank, which was removed for "
            f"single-source-of-truth. Python handles open-status sort."
        )


# -----------------------------------------------------------------------



# -----------------------------------------------------------------------
# SHELTER TAXONOMY ENRICHMENT (query_services in rag/__init__.py)
# -----------------------------------------------------------------------

def _get_taxonomy_names(service_type, **kwargs):
    """Helper: call query_services with mock and return the taxonomy_names passed."""
    from unittest.mock import patch
    from app.rag import query_services

    with patch("app.rag.execute_service_query") as mock_exec:
        mock_exec.return_value = {
            "services": [], "result_count": 0,
            "template_used": "test", "params_applied": {},
            "relaxed": False, "execution_ms": 0,
        }
        query_services(service_type=service_type, location="Brooklyn", **kwargs)
        return mock_exec.call_args.kwargs["user_params"].get("taxonomy_names", [])


def test_shelter_enrichment_youth():
    """Shelter query should always include 'youth' taxonomy (NYC DYCD/HUD
    define youth as 16-24; age eligibility filter handles exclusion)."""
    names_16 = _get_taxonomy_names("shelter", age=16)
    assert "youth" in names_16
    names_19 = _get_taxonomy_names("shelter", age=19)
    assert "youth" in names_19, "19yo must see youth shelters (Covenant House serves 16-24)"
    names_none = _get_taxonomy_names("shelter")
    assert "youth" in names_none, "Youth should be included even without age"


def test_shelter_enrichment_senior():
    """Shelter query for age >= 62 should add 'senior' to taxonomy_names."""
    names = _get_taxonomy_names("shelter", age=65)
    assert "senior" in names
    assert "youth" in names, "Youth is always included (age eligibility handles exclusion)"


def test_shelter_enrichment_families():
    """Shelter query with family_status=with_children narrows to families + parent shelter.

    DB verification (April 2026) showed the Families child has only 3 services;
    strict YourPeer-style narrowing would often return 0 results. The chatbot
    preserves the parent 'shelter' taxonomy in narrowed queries for better recall
    — documented divergence from YourPeer in QUERY_PARITY_AUDIT.md.
    """
    names = _get_taxonomy_names("shelter", family_status="with_children")
    assert names == ["families", "shelter"], f"Expected ['families', 'shelter'], got {names}"


def test_shelter_enrichment_single_adult():
    """Shelter query with family_status=alone narrows to single adult + parent shelter.

    Single Adult child has 38 services (DB verified). Parent preservation
    ensures the 18 generic-Shelter-tagged services remain visible.
    """
    names = _get_taxonomy_names("shelter", family_status="alone")
    assert names == ["single adult", "shelter"], f"Expected ['single adult', 'shelter'], got {names}"


def test_shelter_default_includes_lgbtq_young_adult():
    """lgbtq young adult is in the default shelter taxonomy list.

    Previously it was added as an unconditional enrichment; now it's part of the
    default list (matching YourPeer's parent-taxonomy + API expansion behavior).
    """
    names = _get_taxonomy_names("shelter")
    assert "lgbtq young adult" in names


def test_shelter_default_includes_all_children():
    """Default shelter query (no family_status) returns the full taxonomy list —
    equivalent to YourPeer sending the parent 'Shelter' ID and API expanding to
    all children."""
    names = _get_taxonomy_names("shelter")
    for child in ["shelter", "youth", "families", "single adult", "senior",
                  "lgbtq young adult", "veterans", "safe haven"]:
        assert child in names, f"Default shelter list missing '{child}'"


def test_shelter_narrowing_excludes_generic_siblings():
    """When family_status narrows, generic sibling housing taxonomies
    (safe haven, warming center, TIL) are NOT in the final list.

    Parent 'shelter' IS preserved (see test_shelter_enrichment_families).
    Uses age=30 to avoid triggering the youth safety enrichment.
    """
    names = _get_taxonomy_names("shelter", age=30, family_status="with_children")
    assert "safe haven" not in names, "Generic 'safe haven' should be excluded under narrow"
    assert "warming center" not in names, "Generic 'warming center' should be excluded under narrow"
    assert "single adult" not in names, "Wrong child included under narrow"
    assert "youth" not in names, "Youth not expected for age 30"
    assert names == ["families", "shelter"], f"Expected ['families', 'shelter'], got {names}"


def test_shelter_narrowing_youth_safety_add():
    """When family_status narrows BUT user is in the youth age range (16-24),
    'youth' is added back as a safety enrichment so Covenant House / Ali Forney
    remain discoverable (Cornell sample outcome)."""
    names = _get_taxonomy_names("shelter", age=19, family_status="with_children")
    assert names == ["families", "shelter", "youth"], \
        f"Expected ['families', 'shelter', 'youth'], got {names}"


def test_food_no_enrichment():
    """Non-shelter queries should NOT get taxonomy enrichment."""
    names = _get_taxonomy_names("food", age=16, family_status="with_children")
    assert "youth" not in names
    assert "families" not in names
    assert "lgbtq young adult" not in names


def test_shelter_enrichment_no_mutation():
    """Enrichment should not mutate the TEMPLATES default_params."""
    from app.rag.query_templates import TEMPLATES
    original = list(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
    _get_taxonomy_names("shelter", age=16, family_status="with_children")
    after = TEMPLATES["shelter"]["default_params"]["taxonomy_names"]
    assert original == after, "TEMPLATES default_params was mutated by enrichment"


# -----------------------------------------------------------------------
# OPEN-NOW SORT (post-query)
# -----------------------------------------------------------------------

def test_sort_open_first_basic():
    """Open services should appear before closed and unknown."""
    from app.rag.query_executor import _sort_open_first
    cards = [
        {"service_name": "A", "is_open": None},
        {"service_name": "B", "is_open": "closed"},
        {"service_name": "C", "is_open": "open"},
    ]
    result = _sort_open_first(cards)
    assert result[0]["service_name"] == "C"
    assert result[1]["service_name"] == "B"
    assert result[2]["service_name"] == "A"


def test_sort_open_first_stable_order():
    """Within each group, original order should be preserved (stable sort)."""
    from app.rag.query_executor import _sort_open_first
    cards = [
        {"service_name": "A", "is_open": None},
        {"service_name": "B", "is_open": "closed"},
        {"service_name": "C", "is_open": "open"},
        {"service_name": "D", "is_open": None},
        {"service_name": "E", "is_open": "open"},
        {"service_name": "F", "is_open": "closed"},
    ]
    result = _sort_open_first(cards)
    names = [c["service_name"] for c in result]
    assert names == ["C", "E", "B", "F", "A", "D"]


def test_sort_open_first_all_open():
    """All open services — order should not change."""
    from app.rag.query_executor import _sort_open_first
    cards = [
        {"service_name": "A", "is_open": "open"},
        {"service_name": "B", "is_open": "open"},
    ]
    result = _sort_open_first(cards)
    assert [c["service_name"] for c in result] == ["A", "B"]


def test_sort_open_first_all_unknown():
    """All unknown services — order should not change."""
    from app.rag.query_executor import _sort_open_first
    cards = [
        {"service_name": "A", "is_open": None},
        {"service_name": "B", "is_open": None},
    ]
    result = _sort_open_first(cards)
    assert [c["service_name"] for c in result] == ["A", "B"]


def test_sort_open_first_empty():
    """Empty list should return empty list."""
    from app.rag.query_executor import _sort_open_first
    assert _sort_open_first([]) == []


def test_sort_open_first_single():
    """Single card should return unchanged."""
    from app.rag.query_executor import _sort_open_first
    cards = [{"service_name": "A", "is_open": "closed"}]
    result = _sort_open_first(cards)
    assert len(result) == 1
    assert result[0]["service_name"] == "A"


# -----------------------------------------------------------------------
# FRESHNESS TIER RANKING
# -----------------------------------------------------------------------
# Tiered freshness CASE (fresh ≤90d = 0, stale >90d = 1, unverified = 2)
# sorts before the continuous timestamp so recently verified services
# beat older ones within a distance band, and unverified services sink.
# Paired with the bucketed-distance sort — see
# docs/design/FRESHNESS_TIER_SPEC.md and docs/design/BUCKETED_DISTANCE_SORT_SPEC.md.
#
# The single source of truth for _FRESHNESS_DAYS lives in
# query_templates.py and is re-exported by query_executor.py.
#
# HISTORICAL NOTE: this feature was spec'd but unshipped through Apr 17,
# 2026. These tests sat xfail'd against the spec until the implementation
# landed. Two sibling tests (asserting 4-element _BASE_ORDER_PARTS with
# SQL open-now at index 0) were deleted because they encoded a direction
# the codebase explicitly rejected — open-now sorting is done in Python
# (`_sort_open_first`) with three buckets (open/closed/unknown), not in
# SQL with two (open/not-open). See query_templates.py:430-448.


def test_freshness_tier_constant_is_90_days():
    """Freshness threshold must be 90 days and match between modules.

    The SQL sort threshold (_FRESHNESS_DAYS in query_templates) must
    match the Python stats threshold used by _compute_freshness — a
    drift would mean the displayed "X of Y verified in last 90 days"
    stat disagrees with the sort order the user sees.
    """
    from app.rag.query_templates import _FRESHNESS_DAYS
    from app.rag.query_executor import _FRESHNESS_DAYS as EXECUTOR_DAYS
    assert _FRESHNESS_DAYS == 90
    assert _FRESHNESS_DAYS == EXECUTOR_DAYS, \
        "query_templates and query_executor must use the same freshness threshold"


def test_freshness_tier_in_generated_sql():
    """Generated SQL must include the freshness tier CASE expression in ORDER BY."""
    sql, _ = build_query("food", {})
    assert "CURRENT_DATE - INTERVAL" in sql, \
        "Freshness tier CASE expression missing from generated SQL"
    assert "90 days" in sql, \
        "Freshness tier should use 90-day interval"


def test_freshness_tier_three_tiers():
    """Freshness CASE must produce 3 distinct tiers: 0 (fresh), 1 (stale), 2 (null)."""
    from app.rag.query_templates import _FRESHNESS_TIER_RANK
    assert "THEN 0" in _FRESHNESS_TIER_RANK, "Tier 0 (fresh) missing"
    assert "THEN 1" in _FRESHNESS_TIER_RANK, "Tier 1 (stale) missing"
    assert "ELSE 2" in _FRESHNESS_TIER_RANK, "Tier 2 (never verified) missing"


def test_freshness_tier_in_all_templates():
    """Every template's generated SQL should include the freshness tier."""
    for key in TEMPLATES:
        sql, _ = build_query(key, {})
        assert "CURRENT_DATE - INTERVAL" in sql, \
            f"Template '{key}' missing freshness tier in ORDER BY"


def test_freshness_tier_survives_with_boosts():
    """Freshness tier should remain in ORDER BY even with population/distance boosts."""
    sql, _ = build_query("shelter", {
        "lgbtq_boost": True,
        "lat": 40.7128,
        "lon": -74.0060,
        "radius_meters": 2000,
    })
    assert "CURRENT_DATE - INTERVAL" in sql, \
        "Freshness tier dropped when boosts are active"
    # The final ORDER BY is at the end; the base SELECT's subqueries may
    # contain their own ORDER BY clauses (e.g., on t_co.name). Use rfind.
    order_start = sql.rfind("ORDER BY")
    lgbtq_pos = sql.index("lgbtq", order_start)
    freshness_pos = sql.index("CURRENT_DATE", order_start)
    assert lgbtq_pos < freshness_pos, \
        "Population boost should sort before freshness tier"


def test_freshness_tier_before_continuous_timestamp():
    """Freshness tier CASE must come BEFORE the continuous timestamp.

    This is the whole point of the tier: within a tier, the continuous
    timestamp is a tiebreaker. If the order flipped, the tier would
    become dead weight (continuous timestamp would already fully
    resolve ordering).
    """
    sql, _ = build_query("food", {})
    order_start = sql.rfind("ORDER BY")
    tier_pos = sql.index("CURRENT_DATE", order_start)
    # "l.last_validated_at DESC" is the continuous timestamp tiebreaker.
    timestamp_pos = sql.index("l.last_validated_at DESC", order_start)
    assert tier_pos < timestamp_pos, (
        "Freshness tier CASE (CURRENT_DATE...) must sort BEFORE the "
        "continuous timestamp (l.last_validated_at DESC) — otherwise "
        "the tier has no effect"
    )


def test_base_order_parts_current_shape():
    """_BASE_ORDER_PARTS has 3 elements: freshness tier, continuous timestamp, name.

    If this test fails after a deliberate change to the sort design,
    update the five feature tests above to match the new shape.
    """
    from app.rag.query_templates import _BASE_ORDER_PARTS, _FRESHNESS_TIER_RANK
    assert len(_BASE_ORDER_PARTS) == 3, (
        f"_BASE_ORDER_PARTS has {len(_BASE_ORDER_PARTS)} elements; "
        f"expected 3 (freshness tier CASE, continuous timestamp, name). "
        f"See docs/design/FRESHNESS_TIER_SPEC.md."
    )
    assert _BASE_ORDER_PARTS[0] == _FRESHNESS_TIER_RANK, \
        "Index 0 should be the freshness tier CASE"
    assert "last_validated_at DESC" in _BASE_ORDER_PARTS[1], \
        "Index 1 should be the continuous-timestamp tiebreaker"
    assert _BASE_ORDER_PARTS[2] == "s.name", \
        "Index 2 should be the name tiebreaker"


# -----------------------------------------------------------------------
# format_service_card — also_available and last_validated_at
# -----------------------------------------------------------------------

def test_format_card_also_available_filters():
    """also_available should filter to display categories only."""
    from app.rag.query_templates import format_service_card
    card = format_service_card({
        "service_id": "1", "service_name": "Test",
        "also_available": ["Shower", "Other service", "Clothing Pantry", "Unknown Category"],
    })
    assert card["also_available"] == ["Clothing", "Shower"]


def test_format_card_also_available_none_when_empty():
    """also_available should be None when no co-located services."""
    from app.rag.query_templates import format_service_card
    card = format_service_card({"service_id": "1", "service_name": "Test", "also_available": []})
    assert card["also_available"] is None


def test_format_card_also_available_none_when_only_other():
    """also_available should be None when only 'Other service' is co-located."""
    from app.rag.query_templates import format_service_card
    card = format_service_card({
        "service_id": "1", "service_name": "Test",
        "also_available": ["Other service"],
    })
    # "Other service" is already filtered by the SQL, but if it slips through
    # the display filter should catch it
    assert card["also_available"] is None


def test_format_card_last_validated_at_serialized():
    """last_validated_at should be ISO string, not datetime object."""
    from datetime import datetime
    from app.rag.query_templates import format_service_card
    dt = datetime(2026, 4, 8, 14, 30, 0)
    card = format_service_card({
        "service_id": "1", "service_name": "Test",
        "last_validated_at": dt,
    })
    assert card["last_validated_at"] == "2026-04-08T14:30:00"


def test_format_card_last_validated_at_none():
    """last_validated_at should be None when not provided."""
    from app.rag.query_templates import format_service_card
    card = format_service_card({"service_id": "1", "service_name": "Test"})
    assert card["last_validated_at"] is None


def test_format_card_also_available_sorted():
    """also_available should be sorted alphabetically."""
    from app.rag.query_templates import format_service_card
    card = format_service_card({
        "service_id": "1", "service_name": "Test",
        "also_available": ["Shelter", "Benefits", "Health", "Laundry"],
    })
    assert card["also_available"] == ["Benefits", "Health", "Laundry", "Shelter"]


# -----------------------------------------------------------------------
# CO-LOCATED FILTER
# -----------------------------------------------------------------------

def test_colocated_filter_fragment_structure():
    """FILTER_BY_COLOCATED_TAXONOMY should have correct SQL and params."""
    from app.rag.query_templates import FILTER_BY_COLOCATED_TAXONOMY
    sql, params = FILTER_BY_COLOCATED_TAXONOMY
    assert "colocated_taxonomy_names" in sql
    assert "sal_co.location_id = l.id" in sql
    assert "s_co.id != s.id" in sql
    assert params == ["colocated_taxonomy_names"]


def test_build_query_includes_colocated_filter():
    """When colocated_taxonomy_names is provided, the filter should be in the SQL."""
    from app.rag.query_templates import build_query
    sql, params = build_query("food", {"colocated_taxonomy_names": ["clothing", "clothing pantry"]})
    assert "colocated_taxonomy_names" in sql
    assert params["colocated_taxonomy_names"] == ["clothing", "clothing pantry"]


def test_build_query_excludes_colocated_without_param():
    """Without colocated_taxonomy_names, the filter should not appear."""
    from app.rag.query_templates import build_query
    sql, _ = build_query("food", {})
    assert "colocated_taxonomy_names" not in sql
