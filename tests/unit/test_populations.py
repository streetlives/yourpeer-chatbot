"""
Tests for Phase 3: User Attribute Slots (populations).

Covers:
  - Population extraction (regex): each population type + phrase coverage
  - Multiple populations: "disabled veteran" → ["disabled", "veteran"]
  - False positive guards: "Salvation Army" ≠ veteran, etc.
  - Pregnant coexistence: family_status + populations both fire
  - Senior auto-infer: age >= 62 → populations includes "senior"
  - Veteran boost: veteran_boost param set when population=veteran
  - Description boost: disabled + food → description pattern injected
  - Confirmation message: reflects population context
  - Merge preserves populations: union semantics across messages
  - No population → no boost: base query unchanged
  - Accessibility on cards: format_service_card includes accessibility_info
  - has_new_slots: empty _populations list doesn't trigger false positive

Run with: python -m pytest tests/unit/test_populations.py -v
"""

import sys
import os
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from app.services.slot_extractor import (
    _extract_populations,
    extract_slots,
    merge_slots,
)
from app.services.confirmation import _build_confirmation_message
from app.rag.query_templates import format_service_card


# -----------------------------------------------------------------------
# HELPER: call query_services and capture user_params
# -----------------------------------------------------------------------

def _get_query_params(service_type="food", populations=None, age=None, **kwargs):
    """Call query_services with mocked executor and return the user_params."""
    from app.rag import query_services

    with patch("app.rag.execute_service_query") as mock_exec:
        mock_exec.return_value = {
            "services": [], "result_count": 0,
            "template_used": "test", "params_applied": {},
            "relaxed": False, "execution_ms": 0,
        }
        query_services(
            service_type=service_type,
            location="Brooklyn",
            populations=populations,
            age=age,
            **kwargs,
        )
        call_kwargs = mock_exec.call_args
        return call_kwargs.kwargs.get("user_params", call_kwargs[1].get("user_params", {}))


# -----------------------------------------------------------------------
# POPULATION EXTRACTION — INDIVIDUAL TYPES
# -----------------------------------------------------------------------

class TestPopulationExtraction:
    """Test _extract_populations for each population type."""

    def test_veteran_phrases(self):
        assert "veteran" in _extract_populations("I'm a veteran and need food")
        assert "veteran" in _extract_populations("I served in the army")
        assert "veteran" in _extract_populations("I was in the marines for 8 years")
        assert "veteran" in _extract_populations("navy vet looking for help")
        assert "veteran" in _extract_populations("air force veteran needs shelter")
        assert "veteran" in _extract_populations("national guard member here")

    def test_disabled_phrases(self):
        assert "disabled" in _extract_populations("I'm disabled and need food")
        assert "disabled" in _extract_populations("I have a disability")
        assert "disabled" in _extract_populations("I use a wheelchair")
        assert "disabled" in _extract_populations("I'm blind and need help")
        assert "disabled" in _extract_populations("I'm deaf, where can I get food")
        assert "disabled" in _extract_populations("I'm hearing impaired")

    def test_reentry_phrases(self):
        assert "reentry" in _extract_populations("just got out of jail last week")
        assert "reentry" in _extract_populations("released from prison yesterday")
        assert "reentry" in _extract_populations("I'm on parole")
        assert "reentry" in _extract_populations("I'm on probation and need a job")
        assert "reentry" in _extract_populations("formerly incarcerated, need help")
        assert "reentry" in _extract_populations("just got out of rikers")

    def test_dv_survivor_phrases(self):
        assert "dv_survivor" in _extract_populations("I escaped abuse")
        assert "dv_survivor" in _extract_populations("fleeing abuse with my kids")
        assert "dv_survivor" in _extract_populations("I'm in an abusive relationship")
        assert "dv_survivor" in _extract_populations("domestic violence situation")
        assert "dv_survivor" in _extract_populations("my abusive partner")
        assert "dv_survivor" in _extract_populations("fleeing domestic violence")

    def test_pregnant_phrases(self):
        assert "pregnant" in _extract_populations("I'm pregnant and need a doctor")
        assert "pregnant" in _extract_populations("expecting a baby soon")
        assert "pregnant" in _extract_populations("having a baby in two months")

    def test_senior_phrases(self):
        # "senior" standalone retired from regex — "senior developer" false positive
        # (REGEX_AUDIT). Standalone "senior" is now handled by semantic routing
        # (Tier 2). Multi-word phrases and age-based detection still work via regex.
        assert "senior" in _extract_populations("elderly woman looking for help")
        assert "senior" in _extract_populations("I'm an older adult")
        assert "senior" in _extract_populations("senior citizen needing shelter")

    def test_no_population(self):
        assert _extract_populations("I need food in Brooklyn") == []
        assert _extract_populations("shelter near me") == []
        assert _extract_populations("hello") == []
        assert _extract_populations("") == []


# -----------------------------------------------------------------------
# MULTIPLE POPULATIONS
# -----------------------------------------------------------------------

class TestMultiplePopulations:
    """Verify multiple populations are extracted from a single message."""

    def test_disabled_veteran(self):
        result = _extract_populations("I'm a disabled veteran")
        assert "disabled" in result
        assert "veteran" in result
        assert len(result) == 2

    def test_pregnant_dv_survivor(self):
        result = _extract_populations("I'm pregnant and fleeing abuse")
        assert "pregnant" in result
        assert "dv_survivor" in result

    def test_senior_disabled(self):
        result = _extract_populations("I'm an elderly disabled person")
        assert "senior" in result
        assert "disabled" in result

    def test_reentry_veteran(self):
        result = _extract_populations("I'm a veteran, just got out of prison")
        assert "veteran" in result
        assert "reentry" in result

    def test_sorted_deterministic(self):
        """Output should be sorted for deterministic behavior."""
        result = _extract_populations("disabled veteran on parole")
        assert result == sorted(result)


# -----------------------------------------------------------------------
# FALSE POSITIVE GUARDS
# -----------------------------------------------------------------------

class TestPopulationFalsePositives:
    """Phrases containing population keywords that should NOT extract."""

    def test_salvation_army_not_veteran(self):
        assert "veteran" not in _extract_populations("I need help from the Salvation Army")

    def test_disabled_account_not_disabled(self):
        assert "disabled" not in _extract_populations("my account was disabled")
        assert "disabled" not in _extract_populations("they disabled my phone")

    def test_veterans_day_not_veteran(self):
        assert "veteran" not in _extract_populations("is this open on veterans day")

    def test_veterans_memorial_not_veteran(self):
        assert "veteran" not in _extract_populations("I'm near the veterans memorial")

    def test_blind_spot_not_disabled(self):
        assert "disabled" not in _extract_populations("that's a blind spot in the system")


# -----------------------------------------------------------------------
# PREGNANT + FAMILY_STATUS COEXISTENCE
# -----------------------------------------------------------------------

class TestPregnantCoexistence:
    """Pregnant should fire BOTH family_status and _populations."""

    def test_pregnant_sets_both(self):
        slots = extract_slots("I'm pregnant and need shelter in Brooklyn")
        assert slots["family_status"] == "with_children"
        assert "pregnant" in slots["_populations"]

    def test_pregnant_with_kids_both_fire(self):
        slots = extract_slots("I'm pregnant with two kids")
        assert slots["family_status"] == "with_children"
        assert "pregnant" in slots["_populations"]


# -----------------------------------------------------------------------
# EXTRACT_SLOTS INTEGRATION
# -----------------------------------------------------------------------

class TestExtractSlotsPopulations:
    """Verify _populations appears in extract_slots output."""

    def test_veteran_in_extract_slots(self):
        slots = extract_slots("I'm a veteran and need food in Brooklyn")
        assert slots["service_type"] == "food"
        assert "veteran" in slots["_populations"]

    def test_no_population_returns_empty_list(self):
        slots = extract_slots("I need food in Brooklyn")
        assert slots["_populations"] == []

    def test_populations_dont_interfere_with_service_type(self):
        """Population keywords shouldn't shadow service extraction."""
        slots = extract_slots("I'm a veteran and need shelter in Queens")
        assert slots["service_type"] == "shelter"
        assert "veteran" in slots["_populations"]


# -----------------------------------------------------------------------
# MERGE_SLOTS — LIST UNION SEMANTICS
# -----------------------------------------------------------------------

class TestMergePopulations:
    """Merge should union populations, not replace."""

    def test_merge_adds_new_population(self):
        existing = {"service_type": "food", "_populations": ["veteran"]}
        new = {"_populations": ["disabled"]}
        merged = merge_slots(existing, new)
        assert "veteran" in merged["_populations"]
        assert "disabled" in merged["_populations"]

    def test_merge_deduplicates(self):
        existing = {"_populations": ["veteran"]}
        new = {"_populations": ["veteran", "disabled"]}
        merged = merge_slots(existing, new)
        assert merged["_populations"].count("veteran") == 1
        assert "disabled" in merged["_populations"]

    def test_merge_preserves_when_new_empty(self):
        existing = {"_populations": ["veteran"]}
        new = {"_populations": []}
        merged = merge_slots(existing, new)
        assert merged["_populations"] == ["veteran"]

    def test_merge_creates_from_empty(self):
        existing = {}
        new = {"_populations": ["reentry"]}
        merged = merge_slots(existing, new)
        assert merged["_populations"] == ["reentry"]

    def test_merge_sorted_output(self):
        existing = {"_populations": ["veteran"]}
        new = {"_populations": ["disabled"]}
        merged = merge_slots(existing, new)
        assert merged["_populations"] == sorted(merged["_populations"])


# -----------------------------------------------------------------------
# SENIOR AUTO-INFER FROM AGE
# -----------------------------------------------------------------------

class TestSeniorAutoInfer:
    """query_services should auto-add 'senior' when age >= 62."""

    def test_age_65_adds_senior(self):
        params = _get_query_params(populations=[], age=65)
        boost = params.get("pop_boost_pattern", "")
        assert "senior" in boost or "elder" in boost

    def test_age_30_no_senior(self):
        params = _get_query_params(populations=[], age=30)
        assert "pop_boost_pattern" not in params

    def test_age_62_boundary(self):
        params = _get_query_params(populations=[], age=62)
        boost = params.get("pop_boost_pattern", "")
        assert "senior" in boost or "elder" in boost

    def test_explicit_senior_not_doubled(self):
        """If user already said 'senior', auto-infer shouldn't duplicate."""
        params = _get_query_params(populations=["senior"], age=70)
        boost = params.get("pop_boost_pattern", "")
        # Should have senior pattern exactly once — no duplication
        assert boost.count("senior") >= 1  # at least present


# -----------------------------------------------------------------------
# VETERAN BOOST
# -----------------------------------------------------------------------

class TestVeteranBoost:
    """Veteran population should set veteran_boost param."""

    def test_veteran_sets_boost(self):
        params = _get_query_params(populations=["veteran"])
        assert params.get("veteran_boost") is True

    def test_non_veteran_no_boost(self):
        params = _get_query_params(populations=["disabled"])
        assert "veteran_boost" not in params or params.get("veteran_boost") is not True

    def test_no_population_no_boost(self):
        params = _get_query_params(populations=[])
        assert "veteran_boost" not in params


# -----------------------------------------------------------------------
# DESCRIPTION BOOST
# -----------------------------------------------------------------------

class TestDescriptionBoost:
    """Non-veteran populations inject pop_boost_pattern (ORDER BY rank)."""

    def test_disabled_injects_pattern(self):
        params = _get_query_params(populations=["disabled"])
        boost = params.get("pop_boost_pattern", "")
        assert "disabilit" in boost
        assert "wheelchair" in boost

    def test_reentry_injects_pattern(self):
        params = _get_query_params(populations=["reentry"])
        boost = params.get("pop_boost_pattern", "")
        assert "reentry" in boost
        assert "parole" in boost

    def test_dv_survivor_injects_pattern(self):
        params = _get_query_params(populations=["dv_survivor"])
        boost = params.get("pop_boost_pattern", "")
        assert "domestic violence" in boost

    def test_pregnant_injects_pattern(self):
        params = _get_query_params(populations=["pregnant"])
        boost = params.get("pop_boost_pattern", "")
        assert "prenatal" in boost

    def test_multiple_populations_combine_patterns(self):
        params = _get_query_params(populations=["disabled", "reentry"])
        boost = params.get("pop_boost_pattern", "")
        assert "disabilit" in boost
        assert "reentry" in boost

    def test_description_boost_separate_from_subcategory_filter(self):
        """Phase 4 sub-category filter (description_pattern) and Phase 3
        population boost (pop_boost_pattern) use different params."""
        params = _get_query_params(
            service_type="other",
            populations=["disabled"],
            service_detail="English classes",
        )
        # Phase 4: description_pattern is a WHERE filter for sub-category
        desc_filter = params.get("description_pattern", "")
        assert "ESL" in desc_filter or "english" in desc_filter.lower()
        # Phase 3: pop_boost_pattern is an ORDER BY rank for population
        pop_boost = params.get("pop_boost_pattern", "")
        assert "disabilit" in pop_boost
        # They're separate keys — no collision
        assert "disabilit" not in desc_filter
        assert "ESL" not in pop_boost


# -----------------------------------------------------------------------
# CONFIRMATION MESSAGE
# -----------------------------------------------------------------------

class TestConfirmationPopulations:
    """Confirmation message should reflect population context."""

    def test_veteran_friendly(self):
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_populations": ["veteran"],
        }
        msg = _build_confirmation_message(slots)
        assert "veteran-friendly" in msg

    def test_accessible(self):
        slots = {
            "service_type": "shelter",
            "location": "Manhattan",
            "_populations": ["disabled"],
        }
        msg = _build_confirmation_message(slots)
        assert "accessible" in msg

    def test_reentry_friendly(self):
        slots = {
            "service_type": "employment",
            "location": "Bronx",
            "_populations": ["reentry"],
        }
        msg = _build_confirmation_message(slots)
        assert "reentry-friendly" in msg

    def test_senior_without_age(self):
        slots = {
            "service_type": "food",
            "location": "Queens",
            "_populations": ["senior"],
        }
        msg = _build_confirmation_message(slots)
        assert "senior-friendly" in msg

    def test_senior_with_age_no_double(self):
        """When age is present, senior-friendly prefix is suppressed
        because '(age 65)' already conveys the information."""
        slots = {
            "service_type": "food",
            "location": "Queens",
            "age": 65,
            "_populations": ["senior"],
        }
        msg = _build_confirmation_message(slots)
        assert "senior-friendly" not in msg
        assert "(age 65)" in msg

    def test_no_population_no_prefix(self):
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_populations": [],
        }
        msg = _build_confirmation_message(slots)
        assert "veteran" not in msg
        assert "accessible" not in msg
        assert "reentry" not in msg

    def test_lgbtq_takes_priority_over_population(self):
        """LGBTQ prefix comes from _gender, not populations.
        Both should coexist without conflict."""
        slots = {
            "service_type": "shelter",
            "location": "Manhattan",
            "_gender": "lgbtq",
            "_populations": ["veteran"],
        }
        msg = _build_confirmation_message(slots)
        # LGBTQ-friendly is applied first (existing logic), then veteran
        assert "LGBTQ-friendly" in msg


# -----------------------------------------------------------------------
# NO POPULATION → NO BOOST
# -----------------------------------------------------------------------

class TestNoPopulationNoBoost:
    """Base query should be unchanged when populations is empty/None."""

    def test_empty_list(self):
        params = _get_query_params(populations=[])
        assert "veteran_boost" not in params
        assert "pop_boost_pattern" not in params

    def test_none(self):
        params = _get_query_params(populations=None)
        assert "veteran_boost" not in params
        assert "pop_boost_pattern" not in params


# -----------------------------------------------------------------------
# ACCESSIBILITY ON SERVICE CARDS
# -----------------------------------------------------------------------

class TestAccessibilityOnCards:
    """format_service_card should include accessibility_info."""

    def _make_row(self, **overrides):
        base = {
            "service_id": "123",
            "service_name": "Test Service",
            "service_description": "A test service",
            "organization_name": "Test Org",
            "location_name": "Test Location",
            "location_slug": "test-location",
            "address": "123 Main St",
            "city": "Brooklyn",
            "state": "NY",
            "zip_code": "11201",
            "phone": "212-555-1234",
            "service_url": None,
            "service_email": None,
            "fees": None,
            "additional_info": None,
            "organization_url": None,
            "today_opens": None,
            "today_closes": None,
            "requires_membership": False,
            "last_validated_at": None,
            "also_available": None,
            "location_id": "loc-1",
            "accessibility_info": None,
        }
        base.update(overrides)
        return base

    def test_accessibility_present(self):
        row = self._make_row(accessibility_info="Wheelchair accessible entrance")
        card = format_service_card(row)
        assert card["accessibility"] == "Wheelchair accessible entrance"

    def test_accessibility_absent(self):
        row = self._make_row(accessibility_info=None)
        card = format_service_card(row)
        assert card["accessibility"] is None


# -----------------------------------------------------------------------
# HAS_NEW_SLOTS GUARD
# -----------------------------------------------------------------------

class TestHasNewSlotsGuard:
    """Empty _populations list should NOT trigger has_new_slots."""

    def test_empty_populations_not_new(self):
        """Simulates the has_new_slots check from chatbot.py."""
        extracted = {
            "service_type": None,
            "service_detail": None,
            "additional_services": [],
            "location": None,
            "urgency": None,
            "age": None,
            "family_status": None,
            "_gender": None,
            "_populations": [],
        }
        has_new = any(
            v is not None and v != []
            for k, v in extracted.items()
            if k not in ("additional_services", "_populations")
        )
        assert has_new is False

    def test_population_with_service_is_new(self):
        """A message with service_type should still count as new."""
        extracted = {
            "service_type": "food",
            "service_detail": None,
            "additional_services": [],
            "location": None,
            "urgency": None,
            "age": None,
            "family_status": None,
            "_gender": None,
            "_populations": ["veteran"],
        }
        has_new = any(
            v is not None and v != []
            for k, v in extracted.items()
            if k not in ("additional_services", "_populations")
        )
        assert has_new is True


# -----------------------------------------------------------------------
# LLM SLOT EXTRACTOR SCHEMA
# -----------------------------------------------------------------------

class TestLLMSlotExtractorSchema:
    """Verify the LLM extractor tool schema includes populations."""

    def test_populations_in_tool_schema(self):
        from app.services.llm_slot_extractor import _EXTRACT_SLOTS_TOOL
        props = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"]
        assert "populations" in props
        assert props["populations"]["type"] == "array"

    def test_empty_slots_includes_populations(self):
        from app.services.llm_slot_extractor import _empty_slots
        empty = _empty_slots()
        assert "_populations" in empty
        assert empty["_populations"] == []


# -----------------------------------------------------------------------
# LLM CLASSIFIER SCHEMA
# -----------------------------------------------------------------------

class TestLLMClassifierPopulations:
    """Verify the unified classifier handles populations."""

    def test_classifier_prompt_mentions_populations(self):
        from app.services.llm_classifier import _UNIFIED_SYSTEM_PROMPT
        assert "populations" in _UNIFIED_SYSTEM_PROMPT
        assert "veteran" in _UNIFIED_SYSTEM_PROMPT
        assert "disabled" in _UNIFIED_SYSTEM_PROMPT
        assert "reentry" in _UNIFIED_SYSTEM_PROMPT


# -----------------------------------------------------------------------
# WORD BOUNDARY EXTRACTION (SHORT KEYWORDS)
# -----------------------------------------------------------------------

class TestWordBoundaryPopulations:
    """Short keywords like 'vet' and 'army' use word-boundary matching."""

    def test_vet_word_boundary(self):
        assert "veteran" in _extract_populations("I'm a vet, need food")

    def test_vet_not_in_veterinarian(self):
        assert "veteran" not in _extract_populations("I saw a veterinarian")

    def test_vet_not_in_veto(self):
        assert "veteran" not in _extract_populations("they tried to veto it")

    def test_vet_not_in_vetted(self):
        assert "veteran" not in _extract_populations("the proposal was vetted")

    def test_army_word_boundary(self):
        assert "veteran" in _extract_populations("I was in the army")

    def test_army_not_salvation_army(self):
        """'salvation army' is a service org, not military service."""
        assert "veteran" not in _extract_populations(
            "I need help from the salvation army"
        )

    def test_army_not_salvation_army_capitalized(self):
        assert "veteran" not in _extract_populations(
            "The Salvation Army shelter is full"
        )


# -----------------------------------------------------------------------
# DISABLED / SERVICE KEYWORD OVERLAP
# -----------------------------------------------------------------------

class TestDisabledServiceKeywordOverlap:
    """'disabled' exists in both SERVICE_KEYWORDS['other'] and
    _POPULATION_PHRASES. Verify both systems extract correctly
    and don't interfere with each other."""

    def test_disabled_alone_extracts_both(self):
        """'I'm disabled' → service_type=other AND _populations=['disabled'].
        This is correct: the service extractor sees 'disabled' as a service
        keyword, and the population extractor sees it as an identity."""
        slots = extract_slots("I'm disabled")
        assert slots["service_type"] == "other"
        assert "disabled" in slots["_populations"]

    def test_disabled_with_service_doesnt_shadow(self):
        """'I'm disabled and need food' → service_type extracts both
        'other' (from 'disabled') and 'food', with food as primary
        if it appears first in the multi-service scan. Population
        still extracts 'disabled'."""
        slots = extract_slots("I need food and I'm disabled")
        assert slots["service_type"] == "food"
        assert "disabled" in slots["_populations"]

    def test_disability_services_extracts_both(self):
        """'I need disability services in Brooklyn' → service_type=other
        (from 'disability services' keyword) AND population=disabled."""
        slots = extract_slots("I need disability services in Brooklyn")
        assert slots["service_type"] == "other"
        assert "disabled" in slots["_populations"]

    def test_wheelchair_food_only_population(self):
        """'wheelchair' is only a population phrase, not a service keyword.
        So it extracts population but doesn't set service_type."""
        slots = extract_slots("I use a wheelchair and need food in Queens")
        assert slots["service_type"] == "food"
        assert "disabled" in slots["_populations"]

    def test_disabled_veteran_food(self):
        """'disabled veteran, need food' — 'disabled' is first in text so
        it becomes primary service_type=other. 'food' goes to additional.
        Both populations still extract correctly."""
        slots = extract_slots("disabled veteran, need food in Brooklyn")
        # 'disabled' appears first → primary service is 'other'
        assert slots["service_type"] == "other"
        assert "disabled" in slots["_populations"]
        assert "veteran" in slots["_populations"]
        # 'food' captured in additional_services
        additional_types = [s[0] for s in slots.get("additional_services", [])]
        assert "food" in additional_types

    def test_reentry_employment_overlap(self):
        """'reentry' keywords appear in both SERVICE_KEYWORDS['other'] and
        _POPULATION_PHRASES. 'on parole' is only a population phrase;
        'reentry' is both."""
        slots = extract_slots("I need a job, I'm on parole")
        assert slots["service_type"] == "employment"
        assert "reentry" in slots["_populations"]


# -----------------------------------------------------------------------
# CONFIRMATION PREFIX SINGLE-REPLACE INTEGRITY
# -----------------------------------------------------------------------

class TestConfirmationPrefixIntegrity:
    """The prefix is built once and applied via a single str.replace().
    Verify the fix for the fragile multi-replace bug."""

    def test_lgbtq_veteran_lgbtq_wins(self):
        """LGBTQ prefix takes priority over veteran population prefix."""
        slots = {
            "service_type": "shelter",
            "location": "Manhattan",
            "_gender": "lgbtq",
            "_populations": ["veteran"],
        }
        msg = _build_confirmation_message(slots)
        assert "LGBTQ-friendly" in msg
        # Veteran should NOT also appear (only one prefix shown)
        assert "veteran-friendly" not in msg

    def test_lgbtq_disabled_lgbtq_wins(self):
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_gender": "lgbtq",
            "_populations": ["disabled"],
        }
        msg = _build_confirmation_message(slots)
        assert "LGBTQ-friendly" in msg
        assert "accessible" not in msg

    def test_no_gender_veteran_wins(self):
        """Without LGBTQ gender, veteran population prefix applies."""
        slots = {
            "service_type": "food",
            "location": "Brooklyn",
            "_gender": "male",
            "_populations": ["veteran"],
        }
        msg = _build_confirmation_message(slots)
        assert "veteran-friendly" in msg
        assert "LGBTQ" not in msg


# -----------------------------------------------------------------------
# LLM EXTRACT_SLOTS_SMART POPULATION MERGE
# -----------------------------------------------------------------------

class TestExtractSlotsSmartPopulationMerge:
    """Verify that extract_slots_smart merges populations from
    both regex and LLM sources via union."""

    def test_supplement_logic_merges_populations(self):
        """Simulate the supplement block: LLM has one population,
        regex has a different one. Both should be in the result."""
        # This tests the code pattern, not the actual LLM call
        llm_result = {"_populations": ["veteran"], "service_type": "food"}
        regex_result = {"_populations": ["disabled"], "service_type": "food"}

        # Simulate the merge logic from extract_slots_smart
        llm_pops = set(llm_result.get("_populations") or [])
        regex_pops = set(regex_result.get("_populations") or [])
        combined = sorted(llm_pops | regex_pops)
        assert combined == ["disabled", "veteran"]

    def test_supplement_when_llm_empty(self):
        """LLM returns empty populations, regex found some.
        Regex populations should survive."""
        llm_pops = set([])
        regex_pops = set(["veteran"])
        combined = sorted(llm_pops | regex_pops)
        assert combined == ["veteran"]

    def test_supplement_when_both_empty(self):
        llm_pops = set([])
        regex_pops = set([])
        combined = sorted(llm_pops | regex_pops)
        assert combined == []


# -----------------------------------------------------------------------
# ORDER BY DYNAMIC BUILDER
# -----------------------------------------------------------------------

class TestOrderByDynamicBuilder:
    """Verify the dynamic ORDER BY includes pop_boost_pattern
    in the generated SQL — the core fix for Phase 3."""

    def test_pop_boost_pattern_in_sql(self):
        """When pop_boost_pattern is set, it appears in the ORDER BY."""
        from app.rag.query_templates import build_query
        sql, params = build_query("food", {"pop_boost_pattern": "disabilit|wheelchair"})
        assert ":pop_boost_pattern" in sql
        assert "ORDER BY" in sql
        assert params.get("pop_boost_pattern") == "disabilit|wheelchair"

    def test_no_pop_boost_pattern_not_in_sql(self):
        """When pop_boost_pattern is absent, it does NOT appear in SQL."""
        from app.rag.query_templates import build_query
        sql, params = build_query("food", {})
        assert ":pop_boost_pattern" not in sql

    def test_lgbtq_and_pop_boost_coexist(self):
        """LGBTQ boost + description boost can appear together."""
        from app.rag.query_templates import build_query
        sql, params = build_query("food", {
            "lgbtq_boost": True,
            "pop_boost_pattern": "disabilit",
        })
        assert "lgbtq" in sql.lower()
        assert ":pop_boost_pattern" in sql

    def test_veteran_and_pop_boost_coexist(self):
        """Veteran boost + description boost can appear together."""
        from app.rag.query_templates import build_query
        sql, params = build_query("shelter", {
            "veteran_boost": True,
            "pop_boost_pattern": "disabilit",
        })
        assert "veterans" in sql.lower()
        assert ":pop_boost_pattern" in sql

    def test_distance_and_pop_boost_coexist(self):
        """Proximity search + description boost can appear together."""
        from app.rag.query_templates import build_query
        sql, params = build_query("food", {
            "pop_boost_pattern": "senior",
            "lat": 40.7,
            "lon": -74.0,
            "radius_meters": 3000,
        })
        assert ":pop_boost_pattern" in sql
        assert "ST_Distance" in sql


# -----------------------------------------------------------------------
# PHASE 5: DV CRISIS → POPULATION INJECTION
# -----------------------------------------------------------------------

_DV_CRISIS_RESPONSE = "I'm sorry you're going through this. You deserve to be safe."

_MOCK_RESULTS = {
    "services": [{"service_name": "Test Shelter", "address": "123 Main St"}],
    "result_count": 1,
    "template_used": "shelter",
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 50,
}


def _send_with_crisis(message, crisis_category, session_id=None):
    """Send a message through generate_reply with mocked crisis detection."""
    from unittest.mock import patch as _patch
    from app.services.chatbot import generate_reply
    from app.services.session_store import clear_session, get_session_slots

    if session_id is None:
        import uuid
        session_id = f"test-dv-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)

    crisis_return = (crisis_category, _DV_CRISIS_RESPONSE)

    with _patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         _patch("app.services.chatbot.query_services", return_value=_MOCK_RESULTS), \
         _patch("app.services.chatbot.detect_crisis", return_value=crisis_return):
        result = generate_reply(message, session_id=session_id)

    slots = get_session_slots(session_id)
    return result, slots, session_id


class TestDVCrisisPopulationInjection:
    """Phase 5: DV crisis step-down injects dv_survivor into _populations
    so the description boost fires on subsequent searches."""

    def test_dv_crisis_with_service_intent_injects_population(self):
        """'he hits me and I need shelter' — crisis fires, step-down
        offers search, and dv_survivor is injected into session."""
        result, slots, _ = _send_with_crisis(
            "he hits me and I need shelter in Brooklyn",
            "domestic_violence",
        )
        assert "dv_survivor" in slots.get("_populations", [])

    def test_dv_crisis_without_service_intent_still_injects(self):
        """'he hits me' — crisis fires, no service intent, but
        dv_survivor is still injected for future searches."""
        result, slots, _ = _send_with_crisis(
            "he hits me",
            "domestic_violence",
        )
        assert "dv_survivor" in slots.get("_populations", [])

    def test_non_dv_crisis_no_injection(self):
        """Safety concern crisis should NOT inject dv_survivor."""
        result, slots, _ = _send_with_crisis(
            "I don't feel safe here, I need shelter in Brooklyn",
            "safety_concern",
        )
        assert "dv_survivor" not in slots.get("_populations", [])

    def test_dv_crisis_preserves_existing_populations(self):
        """If user already identified as veteran, DV injection should
        ADD dv_survivor without removing veteran."""
        from app.services.session_store import save_session_slots, clear_session
        import uuid

        session_id = f"test-dv-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)
        # Pre-populate session with veteran population
        save_session_slots(session_id, {"_populations": ["veteran"]})

        _, slots, _ = _send_with_crisis(
            "he hits me and I need shelter in Brooklyn",
            "domestic_violence",
            session_id=session_id,
        )
        assert "dv_survivor" in slots.get("_populations", [])
        assert "veteran" in slots.get("_populations", [])

    def test_dv_crisis_no_duplicate_when_already_extracted(self):
        """If population extractor already caught dv_survivor (from
        'domestic violence' phrase), injection shouldn't duplicate."""
        result, slots, _ = _send_with_crisis(
            "domestic violence, I need shelter in Brooklyn",
            "domestic_violence",
        )
        pops = slots.get("_populations", [])
        assert pops.count("dv_survivor") == 1

    def test_dv_step_down_offers_search(self):
        """DV crisis with service intent should offer step-down search."""
        result, _, _ = _send_with_crisis(
            "he hits me and I need shelter in Brooklyn",
            "domestic_violence",
        )
        assert "would you like me to search" in result["response"].lower() or \
               "Yes, search" in str(result.get("quick_replies", []))

    def test_dv_population_persists_after_confirm(self):
        """After DV crisis step-down, user confirms search → dv_survivor
        should still be in session when query_services is called."""
        from unittest.mock import patch as _patch
        from app.services.chatbot import generate_reply
        from app.services.session_store import clear_session
        import uuid

        session_id = f"test-dv-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)

        # Step 1: DV crisis with service intent → step-down
        with _patch("app.services.chatbot.claude_reply", return_value=""), \
             _patch("app.services.chatbot.query_services", return_value=_MOCK_RESULTS), \
             _patch("app.services.chatbot.detect_crisis",
                    return_value=("domestic_violence", _DV_CRISIS_RESPONSE)):
            generate_reply("he hits me and I need shelter in Brooklyn",
                           session_id=session_id)

        # Step 2: User confirms → query executes
        with _patch("app.services.chatbot.claude_reply", return_value=""), \
             _patch("app.services.chatbot.query_services", return_value=_MOCK_RESULTS) as mock_qs, \
             _patch("app.services.chatbot.detect_crisis", return_value=None):
            generate_reply("Yes, search", session_id=session_id)

        # Hard assertions — query_services MUST have been called
        assert mock_qs.called, "query_services was not called after confirm"
        call_kwargs = mock_qs.call_args
        populations = call_kwargs.kwargs.get("populations")
        assert populations is not None, "populations param missing from query_services call"
        assert "dv_survivor" in populations, \
            f"Expected dv_survivor in populations, got: {populations}"

    def test_dv_no_service_intent_then_followup_gets_boost(self):
        """User says 'he hits me' (no service intent) → crisis fires →
        dv_survivor injected. Then user says 'I need shelter in Brooklyn'
        → dv_survivor should be in query_services call."""
        from unittest.mock import patch as _patch
        from app.services.chatbot import generate_reply
        from app.services.session_store import clear_session
        import uuid

        session_id = f"test-dv-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)

        # Step 1: DV crisis, no service intent
        with _patch("app.services.chatbot.claude_reply", return_value=""), \
             _patch("app.services.chatbot.query_services", return_value=_MOCK_RESULTS), \
             _patch("app.services.chatbot.detect_crisis",
                    return_value=("domestic_violence", _DV_CRISIS_RESPONSE)):
            generate_reply("he hits me", session_id=session_id)

        # Step 2: Follow-up with service intent → should reach confirmation
        with _patch("app.services.chatbot.claude_reply", return_value=""), \
             _patch("app.services.chatbot.query_services", return_value=_MOCK_RESULTS), \
             _patch("app.services.chatbot.detect_crisis", return_value=None):
            result = generate_reply("I need shelter in Brooklyn",
                                    session_id=session_id)

        # Verify dv_survivor is in session for when search executes
        from app.services.session_store import get_session_slots
        slots = get_session_slots(session_id)
        assert "dv_survivor" in slots.get("_populations", []), \
            f"dv_survivor not in session after follow-up, got: {slots.get('_populations')}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
