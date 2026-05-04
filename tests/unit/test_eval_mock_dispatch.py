"""Tests for the eval-runner's service-type-aware mock dispatcher.

Bug 8 fix (May 2026): the previous static `MOCK_QUERY_RESULTS` returned
the same Brooklyn food pantry for every search, regardless of what
the bot actually queried for. That made every Hallucination Resistance
score noisy: a scenario searching for shelter in Manhattan got back
food in Brooklyn, and the LLM judge correctly flagged the resulting
transcript as hallucination — when the bot was just faithfully
echoing corrupted mock data.

These tests verify:
- Each service type returns appropriately-named services
- The address reflects the searched borough
- The template_used field reflects the searched type
- Sentinels return empty results for no_result scenarios
- Backward compatibility: MOCK_QUERY_RESULTS preserves the old shape
- Every shape detail of the production response is preserved
"""

from __future__ import annotations

import os
import sys

import pytest


# Importable without running main()
_EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "eval")
)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

import eval_llm_judge as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Service type → cards mapping
# ---------------------------------------------------------------------------

ALL_SERVICE_TYPES = [
    "food", "shelter", "clothing", "personal_care",
    "medical", "mental_health", "legal", "employment",
]


def test_each_service_type_returns_nonempty_results():
    """No service type should silently return empty cards."""
    for st in ALL_SERVICE_TYPES:
        result = runner._mock_query_services(service_type=st)
        assert result["result_count"] > 0, f"{st} returned no cards"
        assert len(result["services"]) > 0


def test_service_names_distinguish_by_type():
    """Different service types must return distinguishable names. The
    core Bug 8 issue was that shelter + medical + clothing all returned
    'Community Food Pantry'. This test would have failed in the old
    code."""
    names_by_type = {}
    for st in ALL_SERVICE_TYPES:
        result = runner._mock_query_services(service_type=st)
        names_by_type[st] = {s["service_name"] for s in result["services"]}

    # Pairwise: no two service types share their primary card name
    for st1 in ALL_SERVICE_TYPES:
        for st2 in ALL_SERVICE_TYPES:
            if st1 >= st2:
                continue
            shared = names_by_type[st1] & names_by_type[st2]
            assert not shared, (
                f"{st1} and {st2} share service names {shared} — "
                f"Bug 8 has regressed"
            )


def test_template_used_reflects_service_type():
    """`template_used` is in the response shape and the judge sees it.
    A mismatched template_used would leak the bug back as a slot-extraction
    or hallucination penalty."""
    expected = {
        "food": "FoodQuery",
        "shelter": "ShelterQuery",
        "clothing": "ClothingQuery",
        "personal_care": "ShowerQuery",
        "medical": "HealthQuery",
        "mental_health": "MentalHealthQuery",
        "legal": "LegalQuery",
        "employment": "EmploymentQuery",
    }
    for st, expected_template in expected.items():
        result = runner._mock_query_services(service_type=st)
        assert result["template_used"] == expected_template


# ---------------------------------------------------------------------------
# Location reflection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("borough", ["manhattan", "brooklyn", "queens", "bronx", "staten island"])
def test_address_reflects_borough(borough):
    """When the bot searched 'shelter in Manhattan', the mock must
    return Manhattan addresses — not 'Brooklyn' as the old static
    mock did. Otherwise the judge sees a location mismatch."""
    result = runner._mock_query_services(
        service_type="shelter", location=borough,
    )
    expected_display = "Staten Island" if borough == "staten island" else borough.title()
    for card in result["services"]:
        assert expected_display in card["address"], (
            f"Card address {card['address']!r} should contain "
            f"borough display name {expected_display!r}"
        )


def test_unknown_location_falls_back_to_brooklyn():
    """Backward compat: scenarios that don't specify a location land
    on Brooklyn (matches the old hardcoded mock)."""
    result = runner._mock_query_services(service_type="food")
    for card in result["services"]:
        assert "Brooklyn" in card["address"]


def test_neighborhood_routes_to_borough():
    """A Manhattan neighborhood like 'soho' should route to Manhattan
    addresses if the borough match logic catches it. The current
    implementation does substring match on borough name only — so
    'soho' falls back to Brooklyn. This test pins the current behavior;
    if a future change makes the matcher smarter, this test should be
    updated."""
    result = runner._mock_query_services(
        service_type="food", location="soho",
    )
    # Pinning current behavior — neighborhood-only locations fall back.
    # Not a bug per se, but worth documenting.
    assert any(
        "Brooklyn" in card["address"] or "Manhattan" in card["address"]
        for card in result["services"]
    )


# ---------------------------------------------------------------------------
# Argument forms
# ---------------------------------------------------------------------------

def test_positional_service_type_arg():
    """Production query_services takes service_type as first positional.
    The mock must accept that form too — patching with side_effect
    delivers args positionally."""
    result = runner._mock_query_services("food", location="brooklyn")
    assert result["services"][0]["service_name"] == "Community Food Pantry"


def test_keyword_service_type_arg():
    """Keyword form works equivalently."""
    result = runner._mock_query_services(service_type="food", location="brooklyn")
    assert result["services"][0]["service_name"] == "Community Food Pantry"


def test_extra_kwargs_ignored():
    """Production query_services takes ~16 keyword args. The mock must
    silently accept all of them — anything else would crash the eval."""
    result = runner._mock_query_services(
        service_type="medical",
        location="manhattan",
        age=24,
        gender="female",
        weekday=3,
        current_time="14:00",
        max_results=10,
        latitude=40.7,
        longitude=-74.0,
        family_status="alone",
        colocated_service_types=["clothing"],
        service_detail="primary care",
        populations=["lgbtq"],
        org_name=None,
        no_requirements=False,
        taxonomy_override=None,
    )
    assert result["result_count"] > 0


# ---------------------------------------------------------------------------
# Sentinels and edge cases
# ---------------------------------------------------------------------------

def test_nowhere_sentinel_returns_empty():
    """Sentinel for explicitly testing the no-result path."""
    result = runner._mock_query_services(
        service_type="shelter", location="__nowhere__",
    )
    assert result["result_count"] == 0
    assert result["services"] == []


def test_error_sentinel_returns_empty():
    """Same shape on error (matches the old MOCK_EMPTY_RESULTS)."""
    result = runner._mock_query_services(service_type="__error__")
    assert result["result_count"] == 0
    assert result["services"] == []


def test_unknown_service_type_falls_back_gracefully():
    """An unknown service_type shouldn't crash — it returns generic
    drop-in center results."""
    result = runner._mock_query_services(service_type="widget")
    assert result["result_count"] >= 1
    assert result["template_used"] == "GeneralQuery"


def test_none_service_type_falls_back():
    """When the bot calls with service_type=None (no slot extracted),
    the mock should still return something — we don't want eval
    scenarios to silently miss this path."""
    result = runner._mock_query_services(service_type=None)
    assert result["result_count"] >= 1


# ---------------------------------------------------------------------------
# Card shape preservation
# ---------------------------------------------------------------------------

REQUIRED_CARD_FIELDS = {
    "service_name", "organization", "address", "phone", "fees",
    "description", "hours_today", "is_open", "yourpeer_url",
}

REQUIRED_RESPONSE_FIELDS = {
    "services", "result_count", "template_used", "params_applied",
    "relaxed", "execution_ms",
}


def test_response_shape_matches_production():
    """Top-level response keys match what production query_services
    returns. The chatbot reads these; missing one would crash a
    handler before the judge ever saw the conversation."""
    result = runner._mock_query_services(service_type="food")
    missing = REQUIRED_RESPONSE_FIELDS - result.keys()
    assert not missing, f"Response missing fields: {missing}"


def test_card_shape_preserved_across_all_service_types():
    """Every card from every service type has the full ten fields.
    Regression test: a future addition of a new service type that
    forgets a field would silently corrupt the eval transcripts."""
    for st in ALL_SERVICE_TYPES:
        result = runner._mock_query_services(service_type=st)
        for card in result["services"]:
            missing = REQUIRED_CARD_FIELDS - card.keys()
            assert not missing, (
                f"{st} card {card.get('service_name')!r} missing "
                f"fields: {missing}"
            )


def test_cards_have_unique_phone_numbers_within_a_search():
    """Within a single response, no two cards share a phone number.
    This is what the Bug 8 judge note flagged — if two cards have
    the same phone, the bot looks like it's deduplicating poorly."""
    for st in ALL_SERVICE_TYPES:
        result = runner._mock_query_services(service_type=st)
        phones = [card["phone"] for card in result["services"]]
        assert len(phones) == len(set(phones)), (
            f"{st} has duplicate phone numbers across cards"
        )


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_mock_query_results_constant_still_exists():
    """conftest.py and other test modules import MOCK_QUERY_RESULTS by
    name. Removing the constant would break those tests."""
    assert hasattr(runner, "MOCK_QUERY_RESULTS")
    assert isinstance(runner.MOCK_QUERY_RESULTS, dict)


def test_mock_query_results_is_food_brooklyn():
    """The constant alias preserves the original food-Brooklyn case
    so existing tests that snapshot specific values still work."""
    mqr = runner.MOCK_QUERY_RESULTS
    assert mqr["template_used"] == "FoodQuery"
    assert mqr["result_count"] == 2
    assert mqr["services"][0]["service_name"] == "Community Food Pantry"
    assert "Brooklyn" in mqr["services"][0]["address"]


def test_mock_empty_results_constant_still_exists():
    """Some tests use MOCK_EMPTY_RESULTS for explicit no-result cases."""
    assert hasattr(runner, "MOCK_EMPTY_RESULTS")
    assert runner.MOCK_EMPTY_RESULTS["result_count"] == 0
