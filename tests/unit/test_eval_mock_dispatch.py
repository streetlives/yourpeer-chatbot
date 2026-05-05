"""Tests for the eval-runner's fixture-based mock dispatcher.

History:
- Bug 8 (May 2026): static MOCK_QUERY_RESULTS returned Brooklyn food
  pantries for every search. Replaced with a service-type-aware
  dispatcher.
- Bug 8 layer 2 (May 2026): dispatcher's borough-only resolver fell
  back to Brooklyn for any neighborhood search, producing ~32
  false-positive location-mismatch CFs in R39. Added neighborhood
  resolution table.
- Path C (May 2026): replaced the hand-coded service cards (8 builder
  functions) with a fixture loaded from a real Streetlives DB
  snapshot. Replaced the hand-coded neighborhood table with imports
  from production's NEIGHBORHOOD_CENTERS / NYC_LOCATION_ALIASES /
  _CITY_TO_BOROUGH. The dispatcher now knows nothing about NYC and
  nothing about what services exist — it's a pure filter on
  production data.

These tests verify the dispatcher's contract:
- service_type filter actually filters
- borough filter actually filters (using production's lookup chain)
- response shape matches production's query_services
- sentinels return empty results
- backward-compat constants still work
"""

from __future__ import annotations

import os
import sys

import pytest


_EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "eval")
)
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

import eval_llm_judge as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture sanity — does the fixture exist and have data?
# ---------------------------------------------------------------------------


class TestFixtureLoaded:
    """Ensures the fixture file loaded at import. Without it, every
    other test would silently pass with empty results."""

    def test_fixture_is_nonempty(self):
        """The fixture should have at least 100 rows. Expected size is
        ~218 (rn<=5 cap, ~9 service types × 5 boroughs)."""
        assert len(runner._FIXTURE) > 100, (
            f"Fixture only has {len(runner._FIXTURE)} rows. "
            f"Expected ~200. Did the JSON file load correctly?"
        )

    def test_fixture_rows_have_required_fields(self):
        """Every row needs the fields the dispatcher reads."""
        required = [
            "service_id", "service_name", "organization_name",
            "phone", "address", "city", "borough",
            "bot_service_type",
        ]
        sample = runner._FIXTURE[0]
        for field in required:
            assert field in sample, (
                f"Fixture row missing required field {field!r}: {sample}"
            )


# ---------------------------------------------------------------------------
# Service type filter
# ---------------------------------------------------------------------------

ALL_SERVICE_TYPES = [
    "food", "shelter", "clothing", "personal_care",
    "medical", "mental_health", "legal", "employment", "other",
]


class TestServiceTypeFilter:
    """The dispatcher must filter the fixture by service_type."""

    @pytest.mark.parametrize("service_type", ALL_SERVICE_TYPES)
    def test_service_type_returns_only_matching_rows(self, service_type):
        """Whatever cards come back must belong to fixture rows tagged
        with the requested service_type. The dispatcher should NEVER
        return shelter cards in response to a food search."""
        # Get the matching fixture rows directly (ground truth)
        expected_service_names = {
            r["service_name"]
            for r in runner._FIXTURE
            if r["bot_service_type"] == service_type
        }
        result = runner._mock_query_services(service_type=service_type)
        for card in result["services"]:
            assert card["service_name"] in expected_service_names, (
                f"Card {card['service_name']!r} from {service_type!r} search "
                f"isn't in any fixture row tagged {service_type!r}"
            )

    @pytest.mark.parametrize("service_type", ALL_SERVICE_TYPES)
    def test_service_type_returns_nonempty(self, service_type):
        """Every bot-recognized service_type should have at least
        one row in the fixture. If a future fixture refresh produces
        zero rows for some service_type, this test catches it before
        the eval runs against an unknowingly-empty bucket."""
        result = runner._mock_query_services(service_type=service_type)
        assert result["result_count"] > 0, (
            f"{service_type!r} returned no cards. The fixture may be "
            f"stale or the bucketing CASE in scripts/fixture/ may need "
            f"updating."
        )

    def test_unknown_service_type_returns_empty(self):
        """Unknown service types return empty rather than crashing."""
        result = runner._mock_query_services(service_type="rocketship")
        assert result["result_count"] == 0


# ---------------------------------------------------------------------------
# Borough filter
# ---------------------------------------------------------------------------


class TestBoroughFilter:
    """The dispatcher must filter by borough using production's
    NYC_LOCATION_ALIASES + _CITY_TO_BOROUGH chain."""

    @pytest.mark.parametrize("borough", [
        "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island",
    ])
    def test_borough_filter_narrows_results(self, borough):
        """A borough-named search returns only cards from fixture rows
        tagged with that borough."""
        result = runner._mock_query_services(
            service_type="food", location=borough,
        )
        # Every returned card should belong to a fixture row whose
        # borough field is the requested borough.
        expected_addrs = {
            f"{r['address']}, {r['city']}, {r['state']} {r['zip_code']}".strip()
            for r in runner._FIXTURE
            if r["bot_service_type"] == "food" and r["borough"] == borough
        }
        for card in result["services"]:
            assert card["address"] in expected_addrs, (
                f"Card {card['service_name']!r} address {card['address']!r} "
                f"isn't in {borough!r} food rows. Borough filter failed."
            )

    @pytest.mark.parametrize("neighborhood,expected_borough", [
        ("Harlem", "Manhattan"),
        ("East Harlem", "Manhattan"),
        ("Soho", "Manhattan"),
        ("Lower East Side", "Manhattan"),
        ("Times Square", "Manhattan"),
        ("Penn Station", "Manhattan"),
        ("Jackson Heights", "Queens"),
        ("Flushing", "Queens"),
        ("Williamsburg", "Brooklyn"),
        ("Mott Haven", "Bronx"),
    ])
    def test_neighborhood_resolves_to_correct_borough(
        self, neighborhood, expected_borough,
    ):
        """A neighborhood search returns cards in that neighborhood's
        borough — using production's lookup chain, not a hand-coded
        eval table."""
        result = runner._mock_query_services(
            service_type="food", location=neighborhood,
        )
        # Build a set of (service_name, formatted_address) tuples for
        # rows in the expected borough. The card's address field is
        # composed from the same fixture fields, so an exact match
        # tells us the dispatcher actually returned a borough-matching
        # row (not just a row with a colliding service_name).
        expected_addrs = {
            f"{r['address']}, {r['city']}, {r['state']} {r['zip_code']}".strip()
            for r in runner._FIXTURE
            if r["bot_service_type"] == "food"
            and r["borough"] == expected_borough
        }
        assert result["services"], (
            f"{neighborhood} returned no cards. Resolver may have "
            f"failed silently."
        )
        for card in result["services"]:
            assert card["address"] in expected_addrs, (
                f"{neighborhood!r} should resolve to {expected_borough!r}, "
                f"but returned a card at {card['address']!r} which is not "
                f"in any {expected_borough!r} food row."
            )

    def test_unrecognized_location_does_not_crash(self):
        """Unrecognized locations don't crash — the dispatcher returns
        any matching service-type rows. May be flagged as relaxed
        (broader than requested) but always succeeds."""
        result = runner._mock_query_services(
            service_type="food", location="Atlantis",
        )
        # Either: returns rows because resolver couldn't narrow, or
        # returns empty. Either way, must not crash.
        assert isinstance(result["services"], list)


class TestResolveBorough:
    """Direct tests for _resolve_borough — the function that powers
    the borough filter via production's lookup chain."""

    @pytest.mark.parametrize("location,expected", [
        # The five boroughs (case variants)
        ("Manhattan", "Manhattan"),
        ("manhattan", "Manhattan"),
        ("MANHATTAN", "Manhattan"),
        ("Brooklyn", "Brooklyn"),
        ("Queens", "Queens"),
        ("Bronx", "Bronx"),
        ("Staten Island", "Staten Island"),
        # Manhattan neighborhoods
        ("Harlem", "Manhattan"),
        ("East Harlem", "Manhattan"),
        ("Soho", "Manhattan"),
        ("Lower East Side", "Manhattan"),
        ("Times Square", "Manhattan"),
        ("Midtown", "Manhattan"),
        ("Penn Station", "Manhattan"),
        # Queens neighborhoods
        ("Jackson Heights", "Queens"),
        ("Flushing", "Queens"),
        ("Astoria", "Queens"),
        # Brooklyn neighborhoods
        ("Williamsburg", "Brooklyn"),
        # Bronx neighborhoods
        ("Mott Haven", "Bronx"),
        # Compound inputs (substring match)
        ("midtown Manhattan", "Manhattan"),
        ("shelter in Harlem", "Manhattan"),
        ("near Penn Station", "Manhattan"),
        # Unresolvable
        (None, None),
        ("", None),
        ("Atlantis", None),
        ("Mars", None),
    ])
    def test_resolves_correctly(self, location, expected):
        assert runner._resolve_borough(location) == expected


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------


class TestSentinels:
    """Special argument values that test scenarios use to force
    particular dispatcher behavior."""

    def test_nowhere_location_returns_empty(self):
        result = runner._mock_query_services(
            service_type="food", location="__nowhere__",
        )
        assert result["result_count"] == 0
        assert result["services"] == []

    def test_error_service_type_returns_empty(self):
        result = runner._mock_query_services(
            service_type="__error__", location="Brooklyn",
        )
        assert result["result_count"] == 0
        assert result["services"] == []


# ---------------------------------------------------------------------------
# Argument shape — production calls query_services positionally and by keyword
# ---------------------------------------------------------------------------


class TestArgumentShape:
    """The dispatcher accepts both positional and keyword forms,
    because production's query_services uses both."""

    def test_positional_service_type(self):
        """Production sometimes calls query_services('food', ...).
        Dispatcher must accept positional service_type."""
        result = runner._mock_query_services("food")
        assert result["result_count"] > 0

    def test_keyword_service_type(self):
        result = runner._mock_query_services(service_type="food")
        assert result["result_count"] > 0

    def test_extra_kwargs_silently_accepted(self):
        """Production passes many other kwargs (age, gender, urgency,
        radius_meters, etc.). The dispatcher ignores them but must
        not crash."""
        result = runner._mock_query_services(
            service_type="food", location="Brooklyn",
            age=25, gender="women", urgency="high",
            radius_meters=2000, time_of_day="evening",
            family_status="with_children", language="es",
            requires_referral=False, accessibility_needed=True,
        )
        assert result["result_count"] > 0


# ---------------------------------------------------------------------------
# Population-fallback parity — taxonomy_override + max_results
# ---------------------------------------------------------------------------


class TestTaxonomyOverride:
    """Production's population-fallback flow calls query_services with
    `taxonomy_override=["youth"]` (or similar) to find population-
    specific services. The dispatcher must filter the fixture to rows
    whose service_taxonomies overlap (case-insensitive) with the
    override list. Without this filter, population fallback returns
    all-borough rows and the bot displays inappropriate cards
    (veterans housing, formerly-incarcerated shelters, etc.) labeled
    as "youth-friendly" — a major eval-fidelity issue surfaced by
    shelter_queens_17 in the May 2026 subset run.
    """

    def test_taxonomy_override_filters_to_matching_rows(self):
        """Override matches a row's service_taxonomies array
        case-insensitively."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["veterans short-term housing"],
        )
        # The fixture has Charles B. Wang's Veterans Short-Term
        # Housing service in Queens.
        assert result["result_count"] >= 1
        for card in result["services"]:
            taxes = {t.lower() for t in card.get("service_taxonomies", [])}
            assert "veterans short-term housing" in taxes

    def test_taxonomy_override_returns_zero_when_no_match(self):
        """If no fixture row matches the override taxonomies, the
        result is empty. This is production-faithful — production's
        population-fallback dedup-to-empty path triggers a logged
        warning rather than appending fallback cards."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["nonexistent-taxonomy"],
        )
        assert result["result_count"] == 0

    def test_taxonomy_override_records_in_params_applied(self):
        """The eval transcript and judge prompt include params_applied
        so the judge can verify which template ran. taxonomy_override
        must appear there."""
        result = runner._mock_query_services(
            service_type="shelter",
            taxonomy_override=["youth", "lgbtq young adult"],
        )
        assert result["params_applied"].get("taxonomy_override") == [
            "youth", "lgbtq young adult",
        ]


class TestMaxResults:
    """Production's population-fallback caps results at
    _POPULATION_FALLBACK_MAX (= 3). Some main queries also use
    max_results for pagination. The dispatcher must honor it."""

    def test_max_results_caps_count(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=2,
        )
        assert result["result_count"] == 2

    def test_max_results_zero_returns_empty(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=0,
        )
        assert result["result_count"] == 0

    def test_max_results_higher_than_data_returns_all(self):
        result = runner._mock_query_services(
            service_type="shelter", location=None, max_results=999,
        )
        # Should return all 25 shelter rows; max acts as a cap, not a target
        assert result["result_count"] >= 5
        assert result["result_count"] <= 999

    def test_max_results_combines_with_taxonomy_override(self):
        """Production's population fallback uses both together."""
        result = runner._mock_query_services(
            service_type="shelter", location=None,
            taxonomy_override=["single adult"],
            max_results=3,
        )
        assert result["result_count"] <= 3


class TestServiceIdPassthrough:
    """Production's population-fallback dedup uses service_id to
    avoid showing the same row in main + fallback. Cards must
    include service_id."""

    def test_card_has_service_id(self):
        result = runner._mock_query_services(service_type="food")
        for card in result["services"]:
            assert "service_id" in card
            assert card["service_id"], (
                f"Card {card.get('service_name')!r} has no service_id"
            )


# ---------------------------------------------------------------------------
# Response shape — must match production
# ---------------------------------------------------------------------------


class TestResponseShape:
    """The dispatcher's response shape must match what production's
    query_services returns. The bot's downstream pipeline expects
    specific fields; missing one can cause subtle bugs."""

    def test_response_has_expected_top_level_keys(self):
        result = runner._mock_query_services(service_type="food")
        for key in ["services", "result_count", "template_used",
                    "params_applied", "relaxed", "execution_ms"]:
            assert key in result, f"Missing top-level key: {key!r}"

    def test_each_card_has_expected_fields(self):
        """Every card needs name, org, address, phone, etc. for the
        bot's formatter."""
        result = runner._mock_query_services(service_type="food")
        for card in result["services"]:
            for field in ["service_name", "organization", "address",
                          "phone", "fees", "description", "hours_today",
                          "is_open", "yourpeer_url"]:
                assert field in card, (
                    f"Card {card.get('service_name')!r} missing {field!r}"
                )

    def test_template_used_reflects_service_type(self):
        result = runner._mock_query_services(service_type="shelter")
        assert "Shelter" in result["template_used"]


# ---------------------------------------------------------------------------
# Backward compatibility constants
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Other test modules import MOCK_QUERY_RESULTS / MOCK_EMPTY_RESULTS
    by name. They must keep working."""

    def test_mock_query_results_still_exists(self):
        assert hasattr(runner, "MOCK_QUERY_RESULTS")

    def test_mock_query_results_is_food_brooklyn(self):
        """Convention: MOCK_QUERY_RESULTS is a frozen 'food in
        Brooklyn' response. Other tests pin behavior against it."""
        mqr = runner.MOCK_QUERY_RESULTS
        assert mqr["result_count"] > 0
        assert "food" in mqr["template_used"].lower()

    def test_mock_empty_results_still_exists(self):
        assert hasattr(runner, "MOCK_EMPTY_RESULTS")

    def test_mock_empty_results_has_zero_count(self):
        assert runner.MOCK_EMPTY_RESULTS["result_count"] == 0
        assert runner.MOCK_EMPTY_RESULTS["services"] == []


# ---------------------------------------------------------------------------
# Colocated service filter
# ---------------------------------------------------------------------------


class TestColocatedServiceTypes:
    """Production's FILTER_BY_COLOCATED_TAXONOMY restricts results to
    locations where some other service is tagged with one of the
    colocated types' taxonomies. The mock approximates this using each
    row's `also_available` field (built by the same SQL pattern in
    scripts/fixture/_q3_clean.sql).

    Production retries the query without the colocated filter when the
    strict filter returns 0, and sets `colocated_fallback=True` on the
    response (rag/__init__.py:530-545). The mock should mirror this.
    """

    def test_no_colocated_means_no_fallback_flag(self):
        """Sanity: when no colocated_service_types is passed, the
        response should not carry colocated_fallback at all."""
        r = runner._mock_query_services(service_type="food", location="brooklyn")
        assert "colocated_fallback" not in r

    def test_colocated_with_strict_match_returns_intersection(self):
        """When at least one row's also_available overlaps the
        colocated taxonomy set, return that filtered subset and do
        NOT set colocated_fallback."""
        # Brooklyn food + clothing colocation: fixture has 1 strict
        # match (Food Pantry whose location also offers Clothing Pantry).
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["clothing"],
        )
        assert r["result_count"] >= 1
        assert not r.get("colocated_fallback")

    def test_colocated_with_zero_strict_matches_falls_back(self):
        """When no row's also_available overlaps the colocated
        taxonomy set, return the unfiltered primary results AND set
        colocated_fallback=True (matches production retry-and-flag)."""
        # Brooklyn food + shelter colocation: fixture has 0 strict
        # matches. Should fall back to all 5 Brooklyn food rows.
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["shelter"],
        )
        assert r["result_count"] == 5
        assert r.get("colocated_fallback") is True

    def test_colocated_unresolvable_type_flags_fallback(self):
        """Production marks colocated_fallback=True when no colocated
        type can be resolved to a taxonomy set, regardless of whether
        the unfiltered query would have returned results
        (rag/__init__.py:544-545)."""
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            colocated_service_types=["this_is_not_a_real_service_type"],
        )
        assert r["result_count"] == 5  # unfiltered primary still served
        assert r.get("colocated_fallback") is True

    def test_colocated_recorded_in_params_applied(self):
        """The response's params_applied should reflect the colocated
        types so log_query_execution can serialize them."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            colocated_service_types=["food"],
        )
        assert r["params_applied"].get("colocated_service_types") == ["food"]

    def test_colocated_real_data_smoke(self):
        """Sanity check against fixture data: shelter in Manhattan
        with food colocation should find the cluster of homeless-
        services centers that offer multiple services. This is
        regression-protection against accidentally filtering all rows."""
        r = runner._mock_query_services(
            service_type="shelter", location="manhattan",
            colocated_service_types=["food"],
        )
        # Fixture has 4 such rows (Day Sleeping Room, Shelter
        # Placement, Runaway Youth, Overnight Men Sign-Up). Don't pin
        # exact count - fixture refresh may shift it.
        assert r["result_count"] >= 1
        assert not r.get("colocated_fallback")


# ---------------------------------------------------------------------------
# service_detail narrowing
# ---------------------------------------------------------------------------


class TestServiceDetailNarrowing:
    """Production's service_detail narrowing
    (rag/__init__.py::_DETAIL_TO_TAXONOMY_NARROWING) is hand-curated.
    The mock approximates with a substring match against
    service_name / taxonomies / description, with permissive fallback
    when nothing matches.
    """

    def test_no_service_detail_returns_unfiltered(self):
        """Sanity: when service_detail is None, behavior is unchanged."""
        r = runner._mock_query_services(service_type="food", location="brooklyn")
        assert r["result_count"] == 5

    def test_service_detail_narrows_when_substring_matches(self):
        """When at least one row's service_name/taxonomies/description
        contains the detail substring, narrow to those rows."""
        # 'soup kitchen' should match the Brooklyn Community Breakfast
        # row whose taxonomy is "Soup Kitchen".
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            service_detail="soup kitchen",
        )
        assert r["result_count"] >= 1
        assert all(
            "soup kitchen" in (c.get("service_name") or "").lower()
            or any(
                "soup kitchen" in str(t).lower()
                for t in (c.get("service_taxonomies") or [])
            )
            for c in r["services"]
        )

    def test_service_detail_falls_back_when_no_substring_match(self):
        """Permissive: when nothing matches, return the unfiltered
        rows. Production's narrowing is stricter (taxonomy swap), but
        a strict eval would invalidate scenarios where the fixture
        happens to lack a perfect match. Better to score the bot on
        what it CAN return than to penalize the fixture's gap."""
        r = runner._mock_query_services(
            service_type="food", location="brooklyn",
            service_detail="quantumcryogenicfeasts",
        )
        assert r["result_count"] == 5  # unfiltered primary rows

    def test_service_detail_recorded_in_params_applied(self):
        r = runner._mock_query_services(
            service_type="other", location="manhattan",
            service_detail="financial services",
        )
        assert r["params_applied"].get("service_detail") == "financial services"


# ---------------------------------------------------------------------------
# Service-type taxonomy lookup (built once at module load)
# ---------------------------------------------------------------------------


class TestServiceTypeTaxonomyLookup:
    """The lookup is built at module load by reading production's
    TEMPLATES dict. Ensures it's populated for the canonical service
    types."""

    def test_lookup_is_populated(self):
        # Empty when production isn't on the path (spot-check env).
        # In the normal test environment, production should be
        # importable.
        assert runner._SERVICE_TYPE_TAXONOMY_LOOKUP

    def test_lookup_has_all_canonical_service_types(self):
        canonical = set(runner._SERVICE_TYPE_TO_TEMPLATE.keys())
        assert set(runner._SERVICE_TYPE_TAXONOMY_LOOKUP.keys()) == canonical

    def test_lookup_food_includes_food_pantry(self):
        food = runner._SERVICE_TYPE_TAXONOMY_LOOKUP.get("food", set())
        assert "food pantry" in food
        assert "soup kitchen" in food

    def test_lookup_shelter_includes_youth_and_families(self):
        shelter = runner._SERVICE_TYPE_TAXONOMY_LOOKUP.get("shelter", set())
        # Production's shelter template enumerates child taxonomies
        # explicitly (see query_templates.py:659-685). The eval mock
        # depends on these being present for colocated filtering to
        # work right.
        assert "youth" in shelter
        assert "families" in shelter
        assert "single adult" in shelter

    def test_lookup_values_are_lowercased(self):
        """All taxonomy names must be lowercased for case-insensitive
        comparison against fixture rows' service_taxonomies and
        also_available."""
        for service_type, taxonomies in runner._SERVICE_TYPE_TAXONOMY_LOOKUP.items():
            for tax in taxonomies:
                assert tax == tax.lower(), (
                    f"{service_type} taxonomy {tax!r} is not lowercased"
                )
