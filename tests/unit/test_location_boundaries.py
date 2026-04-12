"""
Location boundary tests — validates that queries stay within NYC boundaries,
normalization maps correctly, and the relaxed fallback doesn't leak
out-of-area results.

These tests inspect the generated SQL and parameters WITHOUT requiring
a database connection.

Run with: python -m pytest tests/test_location_boundaries.py -v
Or just:  python tests/test_location_boundaries.py
"""



import os
from app.rag.query_templates import build_query, build_relaxed_query, TEMPLATES
from app.rag.query_executor import (
    normalize_location,
    NYC_LOCATION_ALIASES,
    get_borough_city_names,
    is_borough,
)
from app.services.slot_extractor import extract_slots, NEAR_ME_SENTINEL


# -----------------------------------------------------------------------
# STATE FILTER — must ALWAYS be present
# -----------------------------------------------------------------------

def test_state_filter_in_all_templates():
    """Every template's generated SQL must include the NY state filter."""
    for key in TEMPLATES:
        sql, params = build_query(key, {"city": "Brooklyn", "max_results": 5})
        assert "state_province" in sql.lower(), \
            f"Template '{key}' is missing state filter in SQL"
        assert "'ny'" in sql.lower(), \
            f"Template '{key}' state filter doesn't restrict to NY"


def test_state_filter_in_relaxed_queries():
    """Relaxed queries must STILL include the NY state filter."""
    for key in TEMPLATES:
        sql, params = build_relaxed_query(key, {"city": "Brooklyn", "max_results": 5})
        assert "state_province" in sql.lower(), \
            f"Relaxed template '{key}' dropped the state filter"
        assert "'ny'" in sql.lower(), \
            f"Relaxed template '{key}' state filter doesn't restrict to NY"


def test_state_filter_without_city():
    """Even without a city param, the state filter should be present."""
    sql, params = build_query("food", {"max_results": 5})
    assert "state_province" in sql.lower(), \
        "State filter missing when no city provided"


# -----------------------------------------------------------------------
# CITY FILTER — strict queries
# -----------------------------------------------------------------------

def test_city_filter_exact_match():
    """Strict queries should use exact city match (=), not LIKE."""
    sql, params = build_query("food", {"city": "Brooklyn", "max_results": 5})
    sql_lower = sql.lower()
    assert "lower(pa.city) = lower(:city)" in sql_lower, \
        "Strict query should use exact city match"
    assert params["city"] == "Brooklyn"
    assert "city_pattern" not in params, \
        "Strict query should not have city_pattern param"


def test_city_filter_with_normalized_borough():
    """Normalized borough name should be used in the query params."""
    city = normalize_location("manhattan")
    sql, params = build_query("food", {"city": city, "max_results": 5})
    assert params["city"] == "Manhattan"


# -----------------------------------------------------------------------
# RELAXED QUERY — city broadened, not dropped
# -----------------------------------------------------------------------

def test_relaxed_broadens_city_to_like():
    """Relaxed query should use LIKE pattern for city, not drop it."""
    sql, params = build_relaxed_query("food", {"city": "Brooklyn", "max_results": 5})
    assert "city_pattern" in params, "Relaxed query should have city_pattern param"
    assert params["city_pattern"] == "%Brooklyn%", \
        f"Expected '%Brooklyn%', got '{params.get('city_pattern')}'"
    assert "LIKE" in sql, "Relaxed query should use LIKE for city"


def test_relaxed_does_not_drop_city():
    """Relaxed query should NOT allow results from any city."""
    sql, params = build_relaxed_query("food", {"city": "Brooklyn", "max_results": 5})
    # Either city or city_pattern should be in params — city is never fully dropped
    has_city_constraint = "city" in params or "city_pattern" in params
    assert has_city_constraint, "Relaxed query dropped city filter entirely"


def test_relaxed_drops_eligibility_but_keeps_location():
    """Relaxed query drops age/gender but keeps city + state."""
    sql, params = build_relaxed_query("shelter", {
        "city": "Queens",
        "age": 17,
        "gender": "male",
        "max_results": 5,
    })
    # Age and gender should be dropped
    assert "age" not in params, "Relaxed query should drop age"
    assert "gender" not in params, "Relaxed query should drop gender"
    # City should be broadened but present
    assert "city_pattern" in params, "Relaxed query should keep city as LIKE"
    # State should still be there
    assert "state_province" in sql.lower(), "Relaxed query dropped state filter"


def test_relaxed_drops_schedule_but_keeps_location():
    """Relaxed query drops schedule filters but keeps city + state."""
    sql, params = build_relaxed_query("food", {
        "city": "Bronx",
        "weekday": 3,
        "current_time": "14:00",
        "max_results": 5,
    })
    assert "weekday" not in params, "Relaxed query should drop weekday"
    assert "current_time" not in params, "Relaxed query should drop current_time"
    assert "city_pattern" in params, "Relaxed query should keep city"


# -----------------------------------------------------------------------
# NORMALIZATION — comprehensive borough coverage
# -----------------------------------------------------------------------

def test_all_five_boroughs_normalize():
    """All 5 NYC boroughs should normalize to canonical borough names."""
    boroughs = {
        "manhattan": "Manhattan",
        "brooklyn": "Brooklyn",
        "queens": "Queens",
        "bronx": "Bronx",
        "the bronx": "Bronx",
        "staten island": "Staten Island",
    }
    for raw, expected in boroughs.items():
        result = normalize_location(raw)
        assert result == expected, f"'{raw}' → '{result}', expected '{expected}'"


def test_all_neighborhoods_in_map():
    """Every entry in the alias map should normalize to a valid NYC value."""
    valid_values = {"Manhattan", "New York", "Brooklyn", "Queens", "Bronx", "Staten Island"}
    for alias, city in NYC_LOCATION_ALIASES.items():
        assert city in valid_values, \
            f"Alias '{alias}' maps to '{city}' which is not a valid NYC value"


def test_case_insensitive_normalization():
    """Normalization should be case-insensitive."""
    cases = [
        ("BROOKLYN", "Brooklyn"),
        ("Brooklyn", "Brooklyn"),
        ("bRoOkLyN", "Brooklyn"),
        ("QUEENS", "Queens"),
        ("Manhattan", "Manhattan"),
        ("HARLEM", "New York"),
    ]
    for raw, expected in cases:
        result = normalize_location(raw)
        assert result == expected, f"'{raw}' → '{result}', expected '{expected}'"


# -----------------------------------------------------------------------
# LOCATION EXTRACTION EDGE CASES
# -----------------------------------------------------------------------

def test_borough_in_full_sentence():
    """Boroughs should be extracted from natural sentences."""
    cases = [
        ("I need food in Brooklyn please", "brooklyn"),
        ("shelter somewhere in Queens", "queens"),
        ("Is there a clinic in the Bronx", "bronx"),
        ("Looking for clothes in Manhattan", "manhattan"),
        ("Help in Staten Island", "staten island"),
    ]
    for phrase, expected_loc in cases:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location in: {phrase}"
        assert expected_loc in slots["location"].lower(), \
            f"Expected '{expected_loc}' in: {phrase} → {slots['location']}"


def test_neighborhood_in_full_sentence():
    """Neighborhoods should be extracted from natural sentences."""
    cases = [
        ("food in Harlem", "harlem"),
        ("shelter in Bushwick", "bushwick"),
        ("doctor in Astoria", "astoria"),
        ("job near Midtown", "midtown"),
    ]
    for phrase, expected_loc in cases:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location in: {phrase}"
        assert expected_loc in slots["location"].lower(), \
            f"Expected '{expected_loc}' in: {phrase} → {slots['location']}"


def test_non_nyc_location_extracted_but_wont_match():
    """Non-NYC locations should extract (user said it) but normalize as-is."""
    phrases_and_locations = [
        ("food in Springfield", "Springfield"),
        ("shelter in Newark", "Newark"),
        ("I'm in Yonkers", "Yonkers"),
    ]
    for phrase, raw_location in phrases_and_locations:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location in: {phrase}"
        # Normalization should pass through unchanged since it's not in the alias map
        normalized = normalize_location(slots["location"])
        assert normalized not in ["New York", "Brooklyn", "Queens", "Bronx", "Staten Island"], \
            f"Non-NYC location '{raw_location}' incorrectly normalized to an NYC borough"


def test_near_me_with_borough_override():
    """'Food near me in Brooklyn' should extract Brooklyn, not the near-me sentinel."""
    slots = extract_slots("food near me in Brooklyn")
    # The location should be the real borough, not the sentinel
    if slots["location"] == NEAR_ME_SENTINEL:
        # This is the known behavior if near-me check doesn't look ahead.
        # The updated slot extractor should handle this — check if it does.
        print("  INFO: 'food near me in Brooklyn' returned sentinel (known limitation)")
    else:
        assert "brooklyn" in slots["location"].lower()
        print("  PASS: 'near me in Brooklyn' extracts Brooklyn (not sentinel)")


def test_two_boroughs_in_message():
    """When two boroughs are mentioned, the first one via preposition wins."""
    # "I'm in Queens but looking for food in Brooklyn"
    # The regex "in <location>" pattern should catch the first "in Queens"
    slots = extract_slots("I'm in Queens but looking for food in Brooklyn")
    assert slots["location"] is not None
    # First "in X" match wins — this is a known limitation
    print(f"  INFO: two boroughs → extracted '{slots['location']}' (first match)")


def test_borough_with_typos():
    """Common misspellings should still be handled if possible."""
    # These won't match with exact regex — documenting the limitation
    typos = ["brookyln", "quens", "manhatten", "bronks"]
    for typo in typos:
        slots = extract_slots(f"food in {typo}")
        normalized = normalize_location(slots["location"]) if slots["location"] else None
        if normalized and normalized in ["Brooklyn", "Queens", "New York", "Bronx"]:
            print(f"  INFO: typo '{typo}' → normalized correctly")
        else:
            print(f"  INFO: typo '{typo}' → not matched (needs LLM extraction)")


# -----------------------------------------------------------------------
# QUERY BUILDER — filter composition
# -----------------------------------------------------------------------

def test_query_with_city_includes_city_filter():
    """When city is provided, the query SQL should include the city filter."""
    sql, params = build_query("food", {"city": "Brooklyn", "max_results": 5})
    sql_lower = sql.lower()
    assert "lower(pa.city) = lower(:city)" in sql_lower, \
        "City filter not in query SQL"
    assert params["city"] == "Brooklyn"


def test_query_without_city_omits_city_filter():
    """When no city is provided, exact city filter should not be bound."""
    sql, params = build_query("food", {"max_results": 5})
    sql_lower = sql.lower()
    assert "city" not in params, \
        "City param should not be present when no city provided"
    assert "state_province" in sql_lower, \
        "State filter should still be present"


def test_query_with_age_includes_eligibility():
    """Age param should trigger eligibility filter in the query."""
    sql, params = build_query("shelter", {"city": "Brooklyn", "age": 17, "max_results": 5})
    assert "eligibility" in sql.lower(), "Age eligibility filter not in query"
    assert params["age"] == 17


def test_query_without_age_omits_eligibility():
    """Without age param, eligibility filter should not appear."""
    sql, params = build_query("food", {"city": "Brooklyn", "max_results": 5})
    # The eligibility subquery should not be in the WHERE clause
    assert "age_min" not in sql, "Age eligibility in query without age param"


def test_hidden_filter_always_present():
    """The hidden_from_search filter should always be in the query."""
    for key in TEMPLATES:
        sql, params = build_query(key, {"max_results": 5})
        assert "hidden_from_search" in sql.lower(), \
            f"Template '{key}' missing hidden_from_search filter"


def test_taxonomy_filter_always_present():
    """The taxonomy filter should always be in the query."""
    for key in TEMPLATES:
        if key == "org_name":
            continue  # org_name searches by organization, not taxonomy
        sql, params = build_query(key, {"max_results": 5})
        assert "t.name" in sql.lower(), \
            f"Template '{key}' missing taxonomy filter"
        assert "taxonomy_name" in params or "taxonomy_names" in params, \
            f"Template '{key}' missing taxonomy_name/taxonomy_names param"


def test_max_results_default():
    """max_results should default to 10 if not provided."""
    sql, params = build_query("food", {"city": "Brooklyn"})
    assert params["max_results"] == 10


def test_max_results_override():
    """max_results should be overridable."""
    sql, params = build_query("food", {"city": "Brooklyn", "max_results": 3})
    assert params["max_results"] == 3


# -----------------------------------------------------------------------
# RELAXED QUERY — parameter inspection
# -----------------------------------------------------------------------

def test_relaxed_params_compared_to_strict():
    """Compare strict vs relaxed params to verify what gets dropped."""
    strict_sql, strict_params = build_query("shelter", {
        "city": "Queens",
        "age": 17,
        "gender": "male",
        "weekday": 3,
        "max_results": 5,
    })
    relaxed_sql, relaxed_params = build_relaxed_query("shelter", {
        "city": "Queens",
        "age": 17,
        "gender": "male",
        "weekday": 3,
        "max_results": 5,
    })

    # Strict should have all params
    assert "city" in strict_params
    assert "age" in strict_params
    assert "gender" in strict_params

    # Relaxed should drop eligibility and schedule
    assert "age" not in relaxed_params, "Relaxed should drop age"
    assert "gender" not in relaxed_params, "Relaxed should drop gender"
    assert "weekday" not in relaxed_params, "Relaxed should drop weekday"

    # Relaxed should keep location as LIKE
    assert "city_pattern" in relaxed_params, "Relaxed should have city_pattern"
    assert "city" not in relaxed_params, "Relaxed should not have exact city"

    # Both should have state filter in SQL
    assert "state_province" in strict_sql.lower()
    assert "state_province" in relaxed_sql.lower()



def test_relaxed_without_city_still_has_state():
    """Even if there's no city to relax, the state filter remains."""
    sql, params = build_relaxed_query("food", {"max_results": 5})
    assert "state_province" in sql.lower()
    assert "city_pattern" not in params  # no city to broaden


# -----------------------------------------------------------------------
# BOROUGH EXPANSION (Queens fix)
# -----------------------------------------------------------------------

def test_queens_expands_to_neighborhoods():
    """Queens should expand to include all Queens neighborhoods."""
    cities = get_borough_city_names("Queens")
    assert "queens" in cities
    assert "astoria" in cities
    assert "flushing" in cities
    assert "jamaica" in cities
    assert "long island city" in cities
    assert "jackson heights" in cities
    assert "far rockaway" in cities


def test_brooklyn_expands_to_neighborhoods():
    """Brooklyn should expand to include all Brooklyn neighborhoods."""
    cities = get_borough_city_names("Brooklyn")
    assert "brooklyn" in cities
    assert "williamsburg" in cities
    assert "bushwick" in cities
    assert "flatbush" in cities
    assert "crown heights" in cities


def test_manhattan_expands_to_neighborhoods():
    """Manhattan (New York) should expand to include Manhattan neighborhoods."""
    cities = get_borough_city_names("New York")
    assert "new york" in cities
    assert "harlem" in cities
    assert "midtown" in cities
    assert "chelsea" in cities
    assert "soho" in cities


def test_non_borough_does_not_expand():
    """Non-borough locations should return a single-item list."""
    cities = get_borough_city_names("Springfield")
    assert cities == ["springfield"]


def test_borough_expansion_in_query():
    """When city_list is provided, SQL should use ANY(:city_list)."""
    city_list = get_borough_city_names("Queens")
    sql, params = build_query("shelter", {
        "city": "Queens",
        "city_list": city_list,
        "max_results": 5,
    })
    assert "any(:city_list)" in sql.lower(), \
        "SQL should contain ANY(:city_list) for borough expansion"
    assert params["city_list"] == city_list


def test_borough_expansion_in_relaxed_query():
    """Relaxed query with city_list should keep the list, not fall back to LIKE."""
    city_list = get_borough_city_names("Queens")
    sql, params = build_relaxed_query("shelter", {
        "city": "Queens",
        "city_list": city_list,
        "max_results": 5,
    })
    assert "city_list" in params, "Relaxed query should keep city_list"
    assert "city" not in params, "Relaxed query should drop exact city when city_list present"
    assert "city_pattern" not in params, "Relaxed query should not add LIKE when city_list present"


def test_no_expansion_uses_like_in_relaxed():
    """Without city_list, relaxed query should still fall back to LIKE."""
    sql, params = build_relaxed_query("food", {
        "city": "Springfield",
        "max_results": 5,
    })
    assert "city_pattern" in params, "Should fall back to LIKE without city_list"
    assert params["city_pattern"] == "%Springfield%"


# -----------------------------------------------------------------------
# IS_BOROUGH CLASSIFICATION
# -----------------------------------------------------------------------

def test_is_borough_true():
    """Borough names should be identified as boroughs."""
    boroughs = ["manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island"]
    for b in boroughs:
        assert is_borough(b) is True, f"'{b}' should be a borough"


def test_is_borough_false_neighborhoods():
    """Neighborhoods should NOT be identified as boroughs."""
    neighborhoods = ["harlem", "midtown", "bushwick", "astoria", "mott haven"]
    for n in neighborhoods:
        assert is_borough(n) is False, f"'{n}' should not be a borough"


def test_is_borough_false_other():
    """Non-NYC locations should not be boroughs."""
    assert is_borough("Springfield") is False
    assert is_borough("") is False
    assert is_borough(None) is False


def test_is_borough_case_insensitive():
    """is_borough should be case-insensitive."""
    assert is_borough("BROOKLYN") is True
    assert is_borough("Brooklyn") is True
    assert is_borough("HARLEM") is False


# -----------------------------------------------------------------------
# NEIGHBORHOOD-FIRST QUERY LOGIC
# -----------------------------------------------------------------------

def test_neighborhood_strict_uses_borough_city():
    """Neighborhood search should use the normalized borough city value, not the raw name."""
    # Simulate what rag/__init__.py now passes for "food in Chelsea"
    # Chelsea normalizes to "New York" (Manhattan's DB city value)
    from app.rag.query_executor import normalize_location, get_borough_city_names, is_borough

    for neighborhood, expected_city in [
        ("Chelsea", "New York"),
        ("Harlem", "New York"),
        ("Williamsburg", "Brooklyn"),
        ("Astoria", "Queens"),
        ("Mott Haven", "Bronx"),
        ("Flatbush", "Brooklyn"),
        ("Flushing", "Queens"),
        ("Inwood", "New York"),
    ]:
        normalized = normalize_location(neighborhood)
        assert normalized == expected_city, \
            f"{neighborhood} should normalize to {expected_city}, got {normalized}"
        assert not is_borough(neighborhood), \
            f"{neighborhood} should not be classified as a borough"



def test_neighborhood_query_params_match_db():
    """Neighborhood queries should produce params that match what the DB stores."""
    from app.rag.query_executor import normalize_location, get_borough_city_names

    # Simulate what rag/__init__.py builds for neighborhood searches
    for neighborhood, expected_db_city in [
        ("Chelsea", "New York"),
        ("Williamsburg", "Brooklyn"),
        ("Astoria", "Queens"),
        ("South Bronx", "Bronx"),
    ]:
        normalized = normalize_location(neighborhood)
        city_list = get_borough_city_names(normalized)

        # Build params the way __init__.py does for neighborhoods
        params = {
            "city": normalized,
            "city_list": city_list,
            "_borough_city_list": city_list,
            "max_results": 5,
        }

        sql, bound = build_query("food", params)

        # Strict query should use the normalized city (matches DB)
        assert bound["city"] == expected_db_city, \
            f"{neighborhood}: strict city should be {expected_db_city}, got {bound['city']}"

        # Strict query should have city_list for ANY() matching
        assert "city_list" in bound, \
            f"{neighborhood}: strict should have city_list"

        # SQL should have both filters available
        assert "lower(pa.city) = lower(:city)" in sql.lower(), \
            f"{neighborhood}: strict should have exact city filter"



def test_neighborhood_and_borough_produce_same_strict_query():
    """A neighborhood search should produce the same strict query as its parent borough."""
    from app.rag.query_executor import normalize_location, get_borough_city_names

    # Chelsea (neighborhood) vs Manhattan (borough) should produce identical strict params
    chelsea_normalized = normalize_location("Chelsea")
    chelsea_city_list = get_borough_city_names(chelsea_normalized)
    chelsea_params = {
        "city": chelsea_normalized,
        "city_list": chelsea_city_list,
        "_borough_city_list": chelsea_city_list,
        "max_results": 5,
    }

    manhattan_city_list = get_borough_city_names("New York")
    manhattan_params = {
        "city": "New York",
        "city_list": manhattan_city_list,
        "max_results": 5,
    }

    _, chelsea_bound = build_query("food", chelsea_params)
    _, manhattan_bound = build_query("food", manhattan_params)

    # Both should have the same city value
    assert chelsea_bound["city"] == manhattan_bound["city"] == "New York"

    # Both should have city_list with the same neighborhoods
    assert sorted(chelsea_bound["city_list"]) == sorted(manhattan_bound["city_list"])



def test_neighborhood_relaxed_stays_in_borough():
    """Relaxed neighborhood search should stay within the parent borough."""
    from app.rag.query_executor import get_borough_city_names

    for neighborhood_city, excluded_locations in [
        ("New York", ["brooklyn", "queens", "astoria", "flushing"]),
        ("Brooklyn", ["queens", "manhattan", "new york", "harlem"]),
        ("Queens", ["brooklyn", "manhattan", "new york", "bronx"]),
    ]:
        city_list = get_borough_city_names(neighborhood_city)
        params = {
            "city": neighborhood_city,
            "city_list": city_list,
            "_borough_city_list": city_list,
            "max_results": 5,
        }

        _, bound = build_relaxed_query("food", params)
        relaxed_list = bound.get("city_list", [])

        for excluded in excluded_locations:
            assert excluded not in relaxed_list, \
                f"Relaxed {neighborhood_city} should not include {excluded}"



def test_neighborhood_relaxed_drops_eligibility():
    """Relaxed neighborhood query should drop age/gender but keep location."""
    from app.rag.query_executor import get_borough_city_names

    city_list = get_borough_city_names("New York")
    params = {
        "city": "New York",
        "city_list": city_list,
        "_borough_city_list": city_list,
        "age": 17,
        "gender": "female",
        "max_results": 5,
    }

    _, strict_bound = build_query("food", params)
    _, relaxed_bound = build_relaxed_query("food", params)

    # Strict should have age and gender
    assert "age" in strict_bound
    assert "gender" in strict_bound

    # Relaxed should drop them but keep location
    assert "age" not in relaxed_bound
    assert "gender" not in relaxed_bound
    assert "city_list" in relaxed_bound



def test_all_neighborhoods_produce_valid_queries():
    """Every neighborhood in the alias map should produce a valid query."""
    from app.rag.query_executor import NYC_LOCATION_ALIASES, normalize_location, get_borough_city_names

    neighborhoods = {k: v for k, v in NYC_LOCATION_ALIASES.items()
                     if k not in ("manhattan", "brooklyn", "queens",
                                  "bronx", "the bronx", "staten island")}

    for neighborhood, expected_city in neighborhoods.items():
        normalized = normalize_location(neighborhood)
        assert normalized == expected_city, \
            f"{neighborhood}: expected {expected_city}, got {normalized}"

        city_list = get_borough_city_names(normalized)
        assert len(city_list) >= 1, \
            f"{neighborhood}: city_list should not be empty"

        # Build query — should not crash
        params = {
            "city": normalized,
            "city_list": city_list,
            "max_results": 5,
        }
        sql, bound = build_query("food", params)
        assert bound["city"] == expected_city

    print(f"  PASS: all {len(neighborhoods)} neighborhoods produce valid queries")


def test_borough_strict_uses_expansion():
    """Borough search strict query should use ANY() immediately."""
    city_list = get_borough_city_names("Queens")
    params = {
        "city": "Queens",
        "city_list": city_list,
        "max_results": 5,
    }
    sql, bound = build_query("food", params)

    assert "any(:city_list)" in sql.lower(), \
        "Borough strict query should use ANY()"
    assert "city_list" in bound


def test_multiple_neighborhoods_same_borough():
    """Different neighborhoods in the same borough should expand to the same list."""
    harlem_relaxed = get_borough_city_names(normalize_location("harlem"))
    midtown_relaxed = get_borough_city_names(normalize_location("midtown"))
    chelsea_relaxed = get_borough_city_names(normalize_location("chelsea"))

    assert harlem_relaxed == midtown_relaxed == chelsea_relaxed, \
        "All Manhattan neighborhoods should expand to the same borough list"


def test_end_to_end_neighborhood_via_query_services():
    """Test the full query_services path for neighborhood inputs."""
    from app.rag import query_services
    from unittest.mock import patch

    mock_results = {
        "services": [{"service_name": "Test"}],
        "result_count": 1,
        "template_used": "FoodQuery",
        "params_applied": {},
        "relaxed": False,
        "execution_ms": 10,
    }

    for neighborhood in ["Chelsea", "Williamsburg", "Astoria", "Mott Haven"]:
        with patch("app.rag.execute_service_query", return_value=mock_results) as mock_exec:
            query_services(service_type="food", location=neighborhood)
            call_args = mock_exec.call_args
            user_params = call_args[1]["user_params"] if "user_params" in call_args[1] else call_args[0][1]

            # The city param should be the normalized borough value, not the neighborhood name
            normalized = normalize_location(neighborhood)
            assert user_params["city"] == normalized, \
                f"{neighborhood}: expected city={normalized}, got city={user_params.get('city')}"



def test_harlem_full_query_path():
    """Explicit Harlem regression test — strict, relaxed, and cross-borough isolation."""
    # Strict: should use city=New York (not city=Harlem) since DB stores "New York"
    harlem_params = {
        "city": normalize_location("Harlem"),
        "city_list": get_borough_city_names(normalize_location("Harlem")),
        "_borough_city_list": get_borough_city_names(normalize_location("Harlem")),
        "max_results": 5,
    }
    sql_strict, bound_strict = build_query("food", harlem_params)

    assert bound_strict["city"] == "New York", \
        f"Harlem strict should use city=New York, got {bound_strict['city']}"
    assert "city_list" in bound_strict, "Harlem strict should have city_list"
    assert "harlem" in bound_strict["city_list"], \
        "Harlem strict city_list should include harlem"
    assert "new york" in bound_strict["city_list"], \
        "Harlem strict city_list should include new york"

    # Relaxed: should keep within Manhattan, drop eligibility
    harlem_params_with_age = {**harlem_params, "age": 17}
    sql_relaxed, bound_relaxed = build_relaxed_query("food", harlem_params_with_age)

    assert "city_list" in bound_relaxed, "Harlem relaxed should have city_list"
    assert "harlem" in bound_relaxed["city_list"], \
        "Harlem relaxed city_list should include harlem"
    assert "age" not in bound_relaxed, "Harlem relaxed should drop age"
    assert "city" not in bound_relaxed, "Harlem relaxed should drop exact city"
    assert "any(:city_list)" in sql_relaxed.lower(), \
        "Harlem relaxed should use ANY()"

    # Cross-borough isolation: Harlem results should never include other boroughs
    relaxed_cities = bound_relaxed["city_list"]
    assert "brooklyn" not in relaxed_cities, "Harlem should not include Brooklyn"
    assert "queens" not in relaxed_cities, "Harlem should not include Queens"
    assert "astoria" not in relaxed_cities, "Harlem should not include Astoria"
    assert "bronx" not in relaxed_cities, "Harlem should not include Bronx"
    assert "flushing" not in relaxed_cities, "Harlem should not include Flushing"



def test_neighborhood_proximity_params():
    """Neighborhood searches should include lat/lon/radius for PostGIS proximity."""
    from app.rag.query_executor import get_neighborhood_center, DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    for neighborhood in ["Chelsea", "Harlem", "Williamsburg", "Astoria", "Mott Haven"]:
        center = get_neighborhood_center(neighborhood)
        assert center is not None, f"{neighborhood} should have center coordinates"
        lat, lon = center
        assert 40.4 < lat < 41.0, f"{neighborhood} lat {lat} out of NYC range"
        assert -74.3 < lon < -73.6, f"{neighborhood} lon {lon} out of NYC range"

    # Boroughs should NOT have center coordinates
    for borough in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]:
        center = get_neighborhood_center(borough)
        assert center is None, f"{borough} should NOT have center coordinates"



def test_neighborhood_proximity_in_strict_query():
    """Strict query for a neighborhood should include ST_DWithin proximity filter."""
    from app.rag.query_executor import get_neighborhood_center, DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    lat, lon = get_neighborhood_center("chelsea")
    params = {
        "city": "New York",
        "city_list": get_borough_city_names("New York"),
        "lat": lat,
        "lon": lon,
        "radius_meters": DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
        "max_results": 10,
    }

    sql, bound = build_query("food", params)

    assert "st_dwithin" in sql.lower(), "Strict should have ST_DWithin filter"
    assert bound["lat"] == lat
    assert bound["lon"] == lon
    assert bound["radius_meters"] == DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    # Should also order by distance
    assert "st_distance" in sql.lower(), "Strict should order by distance"



def test_neighborhood_relaxed_drops_proximity():
    """Relaxed query should drop proximity filter to broaden from neighborhood to borough."""
    from app.rag.query_executor import get_neighborhood_center, DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    lat, lon = get_neighborhood_center("chelsea")
    params = {
        "city": "New York",
        "city_list": get_borough_city_names("New York"),
        "_borough_city_list": get_borough_city_names("New York"),
        "lat": lat,
        "lon": lon,
        "radius_meters": DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
        "max_results": 10,
    }

    sql, bound = build_relaxed_query("food", params)

    # Relaxed should NOT have proximity params
    assert "lat" not in bound, "Relaxed should drop lat"
    assert "lon" not in bound, "Relaxed should drop lon"
    assert "radius_meters" not in bound, "Relaxed should drop radius_meters"
    assert "st_dwithin" not in sql.lower(), "Relaxed should not have ST_DWithin"

    # Should still have city_list for borough-level filtering
    assert "city_list" in bound, "Relaxed should keep city_list"

    # Should NOT order by distance (no lat/lon)
    assert "st_distance" not in sql.lower(), "Relaxed should not order by distance"



def test_all_neighborhoods_have_coordinates():
    """Every neighborhood in the alias map should have center coordinates."""
    from app.rag.query_executor import NEIGHBORHOOD_CENTERS, NYC_LOCATION_ALIASES

    neighborhoods = {
        k for k in NYC_LOCATION_ALIASES
        if k not in ("manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island")
    }

    missing = []
    for n in neighborhoods:
        if n not in NEIGHBORHOOD_CENTERS:
            missing.append(n)

    assert not missing, f"Missing coordinates for: {missing}"
    print(f"  PASS: all {len(neighborhoods)} neighborhoods have center coordinates")


def test_end_to_end_proximity_via_query_services():
    """query_services should pass proximity params for neighborhood searches."""
    from app.rag import query_services
    from app.rag.query_executor import DEFAULT_NEIGHBORHOOD_RADIUS_METERS
    from unittest.mock import patch

    mock_results = {
        "services": [{"service_name": "Test"}],
        "result_count": 1,
        "template_used": "FoodQuery",
        "params_applied": {},
        "relaxed": False,
        "execution_ms": 10,
    }

    # Chelsea should get proximity params
    with patch("app.rag.execute_service_query", return_value=mock_results) as mock_exec:
        query_services(service_type="food", location="Chelsea")
        call_args = mock_exec.call_args
        user_params = call_args[1]["user_params"] if "user_params" in call_args[1] else call_args[0][1]

        assert "lat" in user_params, "Chelsea should have lat"
        assert "lon" in user_params, "Chelsea should have lon"
        assert "radius_meters" in user_params, "Chelsea should have radius_meters"
        assert user_params["radius_meters"] == DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    # Manhattan (borough) should NOT get proximity params
    with patch("app.rag.execute_service_query", return_value=mock_results) as mock_exec:
        query_services(service_type="food", location="Manhattan")
        call_args = mock_exec.call_args
        user_params = call_args[1]["user_params"] if "user_params" in call_args[1] else call_args[0][1]

        assert "lat" not in user_params, "Manhattan should NOT have lat"
        assert "lon" not in user_params, "Manhattan should NOT have lon"



def test_all_alias_neighborhoods_have_coordinates():
    """Every neighborhood in the alias map must have matching center coordinates."""
    from app.rag.query_executor import NEIGHBORHOOD_CENTERS, NYC_LOCATION_ALIASES

    neighborhoods = {
        k for k in NYC_LOCATION_ALIASES
        if k not in ("manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island")
    }

    missing = [n for n in neighborhoods if n not in NEIGHBORHOOD_CENTERS]
    assert not missing, f"Neighborhoods in alias map but missing coordinates: {missing}"
    print(f"  PASS: all {len(neighborhoods)} alias neighborhoods have coordinates")


def test_all_known_locations_have_aliases():
    """Every location in slot_extractor._KNOWN_LOCATIONS should be in the alias map."""
    from app.services.slot_extractor import _KNOWN_LOCATIONS
    from app.rag.query_executor import NYC_LOCATION_ALIASES

    missing = [loc for loc in _KNOWN_LOCATIONS if loc not in NYC_LOCATION_ALIASES]
    assert not missing, f"Known locations missing from alias map: {missing}"
    print(f"  PASS: all {len(_KNOWN_LOCATIONS)} known locations have aliases")


def test_all_coordinates_within_nyc_bounds():
    """All neighborhood coordinates must be within NYC bounding box."""
    from app.rag.query_executor import NEIGHBORHOOD_CENTERS

    # NYC bounding box (generous)
    NYC_LAT_MIN, NYC_LAT_MAX = 40.49, 40.92
    NYC_LON_MIN, NYC_LON_MAX = -74.26, -73.70

    for name, (lat, lon) in NEIGHBORHOOD_CENTERS.items():
        assert NYC_LAT_MIN < lat < NYC_LAT_MAX, \
            f"{name}: lat {lat} outside NYC bounds ({NYC_LAT_MIN}-{NYC_LAT_MAX})"
        assert NYC_LON_MIN < lon < NYC_LON_MAX, \
            f"{name}: lon {lon} outside NYC bounds ({NYC_LON_MIN}-{NYC_LON_MAX})"

    print(f"  PASS: all {len(NEIGHBORHOOD_CENTERS)} coordinates within NYC bounds")


def test_neighborhood_case_insensitive_lookup():
    """Neighborhood center lookup should be case-insensitive."""
    from app.rag.query_executor import get_neighborhood_center

    for variant in ["Chelsea", "CHELSEA", "chelsea", "HARLEM", "harlem", "Harlem"]:
        center = get_neighborhood_center(variant)
        assert center is not None, f"get_neighborhood_center('{variant}') should not be None"



def test_neighborhood_whitespace_handling():
    """Neighborhood center lookup should handle leading/trailing whitespace."""
    from app.rag.query_executor import get_neighborhood_center

    for padded in [" Chelsea ", " harlem", "astoria ", "  midtown  "]:
        center = get_neighborhood_center(padded)
        assert center is not None, f"get_neighborhood_center('{padded}') should not be None"



def test_duplicate_neighborhood_names_same_coords():
    """bed-stuy and bedford-stuyvesant should have identical coordinates."""
    from app.rag.query_executor import get_neighborhood_center

    c1 = get_neighborhood_center("bed-stuy")
    c2 = get_neighborhood_center("bedford-stuyvesant")
    assert c1 == c2, f"bed-stuy {c1} != bedford-stuyvesant {c2}"

    c3 = get_neighborhood_center("hells kitchen")
    c4 = get_neighborhood_center("hell's kitchen")
    assert c3 == c4, f"hells kitchen {c3} != hell's kitchen {c4}"



def test_new_neighborhoods_extracted_from_messages():
    """Newly added neighborhoods should be extractable from user messages."""
    from app.services.slot_extractor import extract_slots

    test_cases = [
        ("food in chinatown", "chinatown"),
        ("shelter near dumbo", "dumbo"),
        ("food in park slope", "park slope"),
        ("clothing in east harlem", "east harlem"),
        ("help in the financial district", "financial district"),
        ("food near times square", "times square"),
        ("shelter in fort greene", "fort greene"),
        ("food in corona", "corona"),
        ("food in ridgewood", "ridgewood"),
    ]

    for msg, expected_location in test_cases:
        slots = extract_slots(msg)
        location = (slots.get("location") or "").lower()
        assert expected_location in location, \
            f"'{msg}' should extract location containing '{expected_location}', got '{location}'"



def test_unknown_location_falls_back_gracefully():
    """A location not in our map should still work — just without proximity."""
    from app.rag import query_services
    from app.rag.query_executor import get_neighborhood_center
    from unittest.mock import patch

    mock_results = {
        "services": [], "result_count": 0, "template_used": "FoodQuery",
        "params_applied": {}, "relaxed": False, "execution_ms": 10,
    }

    # "Canarsie" is a real Brooklyn neighborhood not in our map
    assert get_neighborhood_center("Canarsie") is None

    with patch("app.rag.execute_service_query", return_value=mock_results) as mock_exec:
        query_services(service_type="food", location="Canarsie")
        call_args = mock_exec.call_args
        user_params = call_args[1]["user_params"] if "user_params" in call_args[1] else call_args[0][1]

        # Should NOT have proximity params (no coordinates)
        assert "lat" not in user_params, "Unknown location should not have lat"
        # Should still have the raw location as city for a LIKE match attempt
        assert "city" in user_params



def test_proximity_radius_is_reasonable():
    """Default radius should be between 800m and 3200m (0.5-2 miles)."""
    from app.rag.query_executor import DEFAULT_NEIGHBORHOOD_RADIUS_METERS

    assert 800 <= DEFAULT_NEIGHBORHOOD_RADIUS_METERS <= 3200, \
        f"Radius {DEFAULT_NEIGHBORHOOD_RADIUS_METERS}m seems unreasonable"
    print(f"  PASS: default radius {DEFAULT_NEIGHBORHOOD_RADIUS_METERS}m is reasonable")


def test_proximity_does_not_break_non_location_queries():
    """Queries without a location should not have proximity params."""
    from app.rag import query_services
    from unittest.mock import patch

    mock_results = {
        "services": [], "result_count": 0, "template_used": "FoodQuery",
        "params_applied": {}, "relaxed": False, "execution_ms": 10,
    }

    with patch("app.rag.execute_service_query", return_value=mock_results) as mock_exec:
        query_services(service_type="food", location=None)
        call_args = mock_exec.call_args
        user_params = call_args[1]["user_params"] if "user_params" in call_args[1] else call_args[0][1]

        assert "lat" not in user_params
        assert "lon" not in user_params
        assert "radius_meters" not in user_params





# -----------------------------------------------------------------------
# DB CONNECTION (test_connection function)
# -----------------------------------------------------------------------

def test_test_connection_without_db():
    """test_connection() should return False when no DATABASE_URL is set,
    not crash the application."""
    from unittest.mock import patch
    from app.rag.query_executor import test_connection
    import app.rag.query_executor as qe

    # Save and clear the engine so it tries to reinitialize
    saved_engine = qe._engine
    qe._engine = None

    try:
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(qe, 'DATABASE_URL', None):
            # Without DATABASE_URL, test_connection should return False
            result = test_connection()
            assert result is False, "test_connection should return False without a DB"
    finally:
        qe._engine = saved_engine
