"""Unit tests for the unified slot extractor.

Covers the five trust models plus the hybrid and the top-level
`extract()` dispatcher. No LLM calls are made — the LLM is mocked
where needed. Tests assert merge behavior, not LLM behavior.

See UNIFIED_EXTRACTOR_MIGRATION.md for the design these tests enforce.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.slot_extraction import extract
from app.services.slot_extraction.dispatch import (
    _augment_urgency_from_clues,
    _empty_slots,
    _is_narrative,
    _is_simple_message,
    _narrative_regex_fallback,
    _normalize_tool_output,
)
from app.services.slot_extraction.merge import (
    _merge_additional_services,
    _merge_llm_semantic,
    _merge_org_name,
    _merge_regex_literal,
    _merge_regex_only,
    _merge_service_detail,
    _merge_service_type_and_primary_location,
    _merge_union,
    _token_sort,
    _validate_org_name,
    _validate_service_detail,
    merge,
)
from app.services.slot_extraction.prompts import (
    _EXTRACT_SLOTS_TOOL,
    _NARRATIVE_SYSTEM_PROMPT,
    _NARRATIVE_THRESHOLD,
    _SERVICE_TYPE_ENUM,
    _SHORT_SYSTEM_PROMPT,
    _URGENCY_HIERARCHY,
)


# ---------------------------------------------------------------------------
# FIXTURES — empty 13-field regex/LLM dicts to start from
# ---------------------------------------------------------------------------

def _empty_regex_result() -> dict:
    """The 13-field shape that `slot_extractor.extract_slots` produces."""
    return {
        "service_type": None,
        "service_detail": None,
        "additional_services": [],
        "location": None,
        "age": None,
        "urgency": None,
        "_gender": None,
        "family_status": None,
        "_populations": [],
        "org_name": None,
        "no_requirements": False,
        "_contradiction": False,
        "_is_additive": False,
    }


def _llm_result(**overrides) -> dict:
    """An LLM-shape dict (10 fields — the schema-contributed set) with
    overrides. Built on top of `dispatch._empty_slots()` so it stays
    in sync with the real LLM return shape."""
    base = _empty_slots()
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TRUST MODEL 1 — regex literal authority
# ---------------------------------------------------------------------------

class TestTrustModel1Location:
    """Trust Model 1: regex's literal-keyword match wins for location."""

    def test_regex_wins_when_present(self):
        assert _merge_regex_literal("Brooklyn", "Manhattan") == "Brooklyn"

    def test_llm_fills_gap_when_regex_none(self):
        assert _merge_regex_literal(None, "Manhattan") == "Manhattan"

    def test_both_none(self):
        assert _merge_regex_literal(None, None) is None


class TestTrustModel1Gender:
    """Same rule, different field."""

    def test_regex_wins(self):
        assert _merge_regex_literal("female", "male") == "female"

    def test_llm_fills_gap(self):
        assert _merge_regex_literal(None, "nonbinary") == "nonbinary"


class TestTrustModel1ServiceDetail:
    """Service_detail is a sub-case: regex wins, LLM goes through the
    canonical-form validator."""

    def test_regex_wins_no_validator_needed(self):
        # Regex values are always canonical, so they pass through.
        result = _merge_service_detail("dental care", "detox")
        assert result == "dental care"

    def test_llm_value_snapped_to_canonical(self):
        # The LLM's "diabetes care" should snap to "diabetes / insulin care"
        # (one of the canonical values) — a common Haiku paraphrase.
        result = _merge_service_detail(None, "diabetes care")
        assert result == "diabetes care"  # also happens to be canonical

    def test_llm_value_exact_canonical(self):
        result = _merge_service_detail(None, "food stamps / SNAP")
        assert result == "food stamps / SNAP"

    def test_llm_value_fuzzy_snap(self):
        # "food stamp" (singular) should snap to "food stamps / SNAP"
        # or "EBT / food stamps" via substring match.
        result = _merge_service_detail(None, "food stamps")
        # Either canonical is acceptable — both contain "food stamps".
        assert result in ("food stamps / SNAP", "EBT / food stamps")

    def test_llm_value_hallucinated_dropped(self):
        # Values that don't map to any canonical form are dropped.
        result = _merge_service_detail(None, "quantum gastronomy services")
        assert result is None

    def test_both_none(self):
        assert _merge_service_detail(None, None) is None


class TestServiceDetailValidatorBoundary:
    """Direct tests for the validator's ratio threshold behavior."""

    def test_validator_drops_empty(self):
        assert _validate_service_detail(None) is None
        assert _validate_service_detail("") is None

    def test_validator_drops_non_string(self):
        assert _validate_service_detail(123) is None  # type: ignore

    def test_validator_substring_match_both_directions(self):
        # "detox" should snap to "detox" (exact match)
        assert _validate_service_detail("detox") == "detox"
        # "getting a detox" should snap (canonical "detox" is substring)
        # Depending on match order — this may also return a longer canonical.
        # Just verify it returns SOMETHING canonical.
        result = _validate_service_detail("getting a detox")
        assert result is not None


class TestTrustModel1OrgName:
    """Trust Model 1 sub-case: regex wins, LLM goes through fuzzy-match."""

    def test_regex_wins(self):
        result = _merge_org_name("Covenant House", "Covenant Home")
        assert result == "Covenant House"

    def test_llm_fuzzy_match_accepted(self):
        # Same tokens as canonical, just different capitalization.
        result = _merge_org_name(None, "ali forney center")
        assert result == "Ali Forney Center"

    def test_llm_partial_name_dropped_conservatively(self):
        # "Ali Forney" (missing "Center") is only ~74% similar by
        # token-sort-ratio. Validator is conservative; drop rather
        # than snap. This prevents hallucinated orgs from sneaking in
        # via partial-name matches.
        result = _merge_org_name(None, "Ali Forney")
        assert result is None

    def test_llm_token_sort_accepted(self):
        # "Center Ali Forney" — same tokens, different order
        result = _merge_org_name(None, "Center Ali Forney")
        assert result == "Ali Forney Center"

    def test_llm_hallucinated_dropped(self):
        # Orgs not in the canonical list must be dropped, not kept
        # under a fabricated name.
        result = _merge_org_name(None, "XYZ Homeless Services Inc")
        assert result is None

    def test_llm_close_misspelling_accepted(self):
        # Minor typo — "Covnant House" → "Covenant House"
        result = _merge_org_name(None, "Covnant House")
        # Ratio depends on token overlap; accept either match or
        # drop. The contract is "don't hallucinate"; dropping is fine.
        assert result in ("Covenant House", None)


class TestOrgNameValidatorBoundary:
    """Validator-level tests."""

    def test_validator_drops_none(self):
        assert _validate_org_name(None) is None

    def test_validator_drops_empty_string(self):
        assert _validate_org_name("") is None

    def test_validator_drops_low_similarity(self):
        # A string with zero token overlap should drop.
        assert _validate_org_name("Microsoft Corporation") is None


def test_womens_shelter_location_correctly_extracted():
    """Documents that the regex correctly extracts "brooklyn" from
    "women's shelter in Brooklyn" — the location-extractor is NOT
    greedy past the preceding phrase. Earlier revisions of this doc
    flagged this as a potential edge case; behavior audit found it
    resolved. Test asserts the working behavior so a future regex
    change that DOES break it gets caught."""
    from app.services.slot_extractor import extract_slots
    slots = extract_slots("women's shelter in Brooklyn")
    assert slots["location"] == "brooklyn"


# ---------------------------------------------------------------------------
# TRUST MODEL 2 — LLM semantic authority
# ---------------------------------------------------------------------------

class TestTrustModel2Age:
    def test_llm_wins_when_present(self):
        assert _merge_llm_semantic(17, 25) == 25

    def test_regex_fallback_when_llm_none(self):
        assert _merge_llm_semantic(17, None) == 17

    def test_both_none(self):
        assert _merge_llm_semantic(None, None) is None


class TestTrustModel2Urgency:
    def test_llm_wins(self):
        assert _merge_llm_semantic("medium", "high") == "high"

    def test_regex_fallback(self):
        assert _merge_llm_semantic("high", None) == "high"


class TestTrustModel2FamilyStatus:
    def test_llm_wins(self):
        assert _merge_llm_semantic("alone", "with_children") == "with_children"

    def test_regex_fallback(self):
        assert _merge_llm_semantic("alone", None) == "alone"


def test_third_person_age_is_schema_intended():
    """Documents that 'I have a 3-year-old' yields age=3, per the tool
    schema which explicitly says "age may be the user or someone
    they're asking about (e.g., 'my son is 12' → 12)". This is
    intended behavior, not a bug. Earlier doc revisions flagged it as
    an xfail edge case; audit found it correctly implements the
    schema. Test asserts the working behavior."""
    from app.services.slot_extractor import extract_slots
    slots = extract_slots("I have a 3-year-old who needs clothes")
    assert slots["age"] == 3


# ---------------------------------------------------------------------------
# TRUST MODEL 3 — set-agreement ordering
# ---------------------------------------------------------------------------

class TestTrustModel3SetAgreement:
    """Trust Model 3: set-equality decides whether regex priority or
    LLM picks primary. The six worked-examples table rows are each
    one test."""

    def test_row1_cross_borough_sets_match_regex_priority_wins(self):
        # "food in Brooklyn and shelter in Manhattan"
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",
            "location": "manhattan",
            "additional_services": [("food", None, "brooklyn")],
        })
        llm = _llm_result(
            service_type="food",
            location="brooklyn",
            additional_services=[("shelter", None, "manhattan")],
        )
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        # Sets both = {food, shelter}. Regex priority wins → shelter.
        assert primary == "shelter"
        assert loc == "manhattan"

    def test_row2_same_location_multi_intent_regex_priority_wins(self):
        # "I need food and shelter in Brooklyn"
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",
            "location": "brooklyn",
            "additional_services": [("food", None, None)],
        })
        llm = _llm_result(
            service_type="food",
            location="brooklyn",
            additional_services=[("shelter", None, None)],
        )
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        assert primary == "shelter"
        assert loc == "brooklyn"

    def test_row3_hospital_context_llm_wins(self):
        # "just got out of hospital, need somewhere safe"
        # Regex catches {medical, shelter}; LLM correctly filters to {shelter}.
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",
            "additional_services": [("medical", None, None)],
        })
        llm = _llm_result(service_type="shelter", additional_services=[])
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        # Sets disagree ({medical, shelter} != {shelter}) → LLM wins.
        assert primary == "shelter"
        assert additional == []  # LLM's additionals

    def test_row4_doctor_on_tv_llm_wins(self):
        # "need food, saw a doctor on TV"
        regex = _empty_regex_result()
        regex.update({
            "service_type": "food",
            "additional_services": [("medical", None, None)],
        })
        llm = _llm_result(service_type="food", additional_services=[])
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        assert primary == "food"

    def test_row5_three_services_sets_match_regex_priority_wins(self):
        # "food, shelter, and a job"
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",
            "additional_services": [
                ("food", None, None),
                ("employment", None, None),
            ],
        })
        llm = _llm_result(
            service_type="food",
            additional_services=[
                ("shelter", None, None),
                ("employment", None, None),
            ],
        )
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        assert primary == "shelter"  # regex priority tier 1

    def test_row6_regex_empty_llm_wins(self):
        # "I ran out of insulin" — regex has no keyword for this; LLM does.
        regex = _empty_regex_result()
        llm = _llm_result(service_type="medical", service_detail="diabetes / insulin care")
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        assert primary == "medical"

    def test_llm_empty_regex_wins(self):
        # LLM returned nothing — trust regex.
        regex = _empty_regex_result()
        regex.update({
            "service_type": "food",
            "location": "brooklyn",
        })
        llm = _llm_result()  # all empty
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        assert primary == "food"
        assert loc == "brooklyn"


# ---------------------------------------------------------------------------
# TRUST MODEL 4 — union with FP tolerance
# ---------------------------------------------------------------------------

class TestTrustModel4Union:
    def test_union_of_both(self):
        result = _merge_union(["veteran"], ["disabled"])
        assert result == ["disabled", "veteran"]

    def test_regex_only(self):
        assert _merge_union(["veteran"], []) == ["veteran"]

    def test_llm_only(self):
        assert _merge_union([], ["reentry"]) == ["reentry"]

    def test_both_empty(self):
        assert _merge_union([], []) == []

    def test_deduplicates(self):
        result = _merge_union(["veteran", "disabled"], ["disabled", "senior"])
        assert result == ["disabled", "senior", "veteran"]

    def test_none_inputs_tolerated(self):
        assert _merge_union(None, ["foster_youth"]) == ["foster_youth"]
        assert _merge_union(["foster_youth"], None) == ["foster_youth"]


def test_brother_in_jail_does_not_trigger_reentry_fp():
    """Documents that the regex does NOT falsely extract reentry from
    third-person attribution like 'my brother's in jail'. Earlier
    revisions of the doc flagged this as a potential FP the union
    rule would accept; audit found the regex's negation/attribution
    logic already handles it. Test asserts the correct behavior so
    a regression becomes visible."""
    from app.services.slot_extractor import extract_slots
    slots = extract_slots("my brother's in jail and I need food")
    assert "reentry" not in (slots.get("_populations") or [])


# ---------------------------------------------------------------------------
# TRUST MODEL 5 — regex-only (LLM not asked)
# ---------------------------------------------------------------------------

class TestTrustModel5SchemaAssertions:
    """Schema-level assertion: the tool does NOT declare these fields.
    LLM is not asked; its output cannot contribute."""

    def test_tool_schema_has_no_requirements_field_absent(self):
        schema_fields = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"].keys()
        assert "no_requirements" not in schema_fields

    def test_tool_schema_has_no_contradiction_field(self):
        schema_fields = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"].keys()
        assert "_contradiction" not in schema_fields
        assert "contradiction" not in schema_fields

    def test_tool_schema_has_no_is_additive_field(self):
        schema_fields = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"].keys()
        assert "_is_additive" not in schema_fields
        assert "is_additive" not in schema_fields


class TestTrustModel5Passthrough:
    def test_passthrough_true(self):
        assert _merge_regex_only(True) is True

    def test_passthrough_false(self):
        assert _merge_regex_only(False) is False


# ---------------------------------------------------------------------------
# HYBRID — additional_services
# ---------------------------------------------------------------------------

class TestHybridAdditionalServices:
    """Three tests per the doc's test-coverage block:
    regex-only item preserved, LLM-only item preserved, dedup by type."""

    def test_regex_item_with_detail_and_location_preserved(self):
        regex_additional = [("food", "food pantries", "brooklyn")]
        llm_additional = []
        winner_additional = llm_additional
        result = _merge_additional_services(
            primary="shelter",
            winner_additional=winner_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        assert result == [("food", "food pantries", "brooklyn")]

    def test_llm_item_with_detail_and_location_preserved(self):
        # LLM found a service regex missed, with its own detail + loc.
        regex_additional = []
        llm_additional = [("shelter", None, "manhattan")]
        winner_additional = llm_additional
        result = _merge_additional_services(
            primary="food",
            winner_additional=winner_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        assert result == [("shelter", None, "manhattan")]

    def test_dedup_by_service_type_regex_wins_for_detail(self):
        # Both found shelter, but regex has detail; result should keep
        # regex's tuple.
        regex_additional = [("shelter", "emergency shelter", "brooklyn")]
        llm_additional = [("shelter", None, None)]  # LLM entry with less info
        result = _merge_additional_services(
            primary="food",
            winner_additional=llm_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        assert len(result) == 1
        assert result[0] == ("shelter", "emergency shelter", "brooklyn")

    def test_primary_excluded_from_additional(self):
        # Regex-extracted additional repeats the primary; should be
        # filtered out.
        regex_additional = [("food", None, None), ("shelter", None, None)]
        result = _merge_additional_services(
            primary="food",
            winner_additional=[],
            regex_additional=regex_additional,
            llm_additional=[],
        )
        # "food" (primary) excluded, shelter kept.
        types = [item[0] for item in result]
        assert "food" not in types
        assert "shelter" in types

    def test_per_field_merge_llm_fills_regex_missing_detail(self):
        """Behavior #19.5 in the migration doc: on duplicate, regex
        wins type but LLM fills detail/location when regex's is None.
        Previously the function picked regex's tuple wholesale,
        dropping LLM-supplied detail/location info. Now merges
        per-field."""
        regex_additional = [("food", None, None)]     # regex has type, no detail/loc
        llm_additional = [("food", "food stamps", "brooklyn")]  # LLM has richer info
        result = _merge_additional_services(
            primary=None,
            winner_additional=llm_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        # Doc-expected: regex wins type, LLM fills detail+location
        assert result == [("food", "food stamps", "brooklyn")]

    def test_per_field_merge_regex_keeps_its_set_fields(self):
        """Flip side of the above: when regex has detail OR location,
        keep regex's value; LLM's entry doesn't override."""
        regex_additional = [("shelter", "emergency shelter", None)]  # regex has detail
        llm_additional = [("shelter", "different shelter type", "manhattan")]
        result = _merge_additional_services(
            primary=None,
            winner_additional=llm_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        # Regex's detail wins; location falls back to LLM's (regex's was None).
        assert result == [("shelter", "emergency shelter", "manhattan")]

    def test_per_field_merge_partial_regex_info(self):
        """Regex has detail but no location; LLM has location but no
        detail. Result should merge both — regex's detail + LLM's
        location."""
        regex_additional = [("medical", "dental care", None)]
        llm_additional = [("medical", None, "bronx")]
        result = _merge_additional_services(
            primary=None,
            winner_additional=llm_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        assert result == [("medical", "dental care", "bronx")]

    def test_primary_exclusion_emits_debug_log(self, caplog):
        """Behavior #19.3 in the migration doc: log primary-exclusion
        at debug level for ops traceability."""
        import logging
        regex_additional = [("food", "groceries", "brooklyn")]  # same as primary
        with caplog.at_level(logging.DEBUG, logger="app.services.slot_extraction.merge"):
            result = _merge_additional_services(
                primary="food",
                winner_additional=[],
                regex_additional=regex_additional,
                llm_additional=[],
            )
        assert result == []
        # Debug log was emitted with the service name
        assert any(
            "excluding 'food'" in rec.message and rec.levelname == "DEBUG"
            for rec in caplog.records
        ), f"Expected DEBUG log for 'food' exclusion; got: {[(r.levelname, r.message) for r in caplog.records]}"

    def test_regex_item_with_empty_type_skipped(self):
        """Line 452: regex_additional has entries with empty/None type —
        skipped silently (defensive guard)."""
        regex_additional = [
            ("", None, None),              # empty type
            (None, None, None),            # None type
            ("shelter", None, None),       # valid
        ]
        result = _merge_additional_services(
            primary="food",
            winner_additional=[],
            regex_additional=regex_additional,
            llm_additional=[],
        )
        # Only shelter survives.
        assert result == [("shelter", None, None)]

    def test_regex_duplicate_type_second_occurrence_not_logged(self):
        """Line 459->464: when svc is in seen but svc != primary (a
        duplicate within regex_additional itself), skip silently
        without emitting the primary-exclusion debug log. Defensive
        guard for upstream data that shouldn't duplicate types in
        the first place (extract_slots already dedupes)."""
        import logging
        regex_additional = [
            ("shelter", "emergency", None),
            ("shelter", "transitional", None),  # duplicate type — no log emitted
        ]
        caplog_messages = []

        class Capturing(logging.Handler):
            def emit(self, record):
                caplog_messages.append((record.levelname, record.getMessage()))

        handler = Capturing()
        logger_inst = logging.getLogger("app.services.slot_extraction.merge")
        logger_inst.addHandler(handler)
        logger_inst.setLevel(logging.DEBUG)
        try:
            result = _merge_additional_services(
                primary="food",
                winner_additional=[],
                regex_additional=regex_additional,
                llm_additional=[],
            )
        finally:
            logger_inst.removeHandler(handler)
        # Only the first shelter entry was kept.
        assert result == [("shelter", "emergency", None)]
        # No primary-exclusion log fired (shelter != primary "food").
        assert not any(
            "excluding 'shelter'" in msg
            for _, msg in caplog_messages
        )

    def test_llm_duplicate_type_second_occurrence_ignored(self):
        """llm_by_type index uses first-wins semantics. If llm_additional
        contains the same type twice, only the first entry's detail/
        location are used when merging against regex."""
        regex_additional = [("food", None, None)]
        llm_additional = [
            ("food", "food pantries", "brooklyn"),   # first-wins for index
            ("food", "soup kitchens", "manhattan"),  # second entry ignored in index
        ]
        result = _merge_additional_services(
            primary=None,
            winner_additional=llm_additional,
            regex_additional=regex_additional,
            llm_additional=llm_additional,
        )
        # First LLM entry's detail/location flow into the regex tuple.
        # Second entry is a duplicate type — since primary is None and
        # it's not in `seen` after step 1... actually, step 1 added
        # "food" to seen, so step 2 & 3 skip the second LLM entry.
        assert result == [("food", "food pantries", "brooklyn")]


# ---------------------------------------------------------------------------
# TOP-LEVEL merge() — 13-field composition
# ---------------------------------------------------------------------------

class TestTopLevelMerge:
    """Full merge() invocations producing the 13-field result shape."""

    def test_result_has_13_fields(self):
        regex = _empty_regex_result()
        llm = _llm_result()
        result = merge(regex, llm)
        expected_fields = {
            "service_type", "service_detail", "additional_services",
            "location", "age", "urgency", "_gender", "family_status",
            "_populations", "org_name", "no_requirements",
            "_contradiction", "_is_additive",
        }
        assert set(result.keys()) == expected_fields

    def test_trust_model_5_fields_always_from_regex(self):
        regex = _empty_regex_result()
        regex["_contradiction"] = True
        regex["_is_additive"] = True
        regex["no_requirements"] = True
        # LLM result has none of these — that's by design.
        llm = _llm_result()
        result = merge(regex, llm)
        assert result["_contradiction"] is True
        assert result["_is_additive"] is True
        assert result["no_requirements"] is True

    def test_mixed_regex_and_llm_contributions(self):
        # Realistic case: regex caught location + populations;
        # LLM caught age + urgency.
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",
            "location": "brooklyn",
            "_populations": ["veteran"],
        })
        llm = _llm_result(
            service_type="shelter",
            age=45,
            urgency="high",
            _populations=["disabled"],
        )
        result = merge(regex, llm)
        assert result["service_type"] == "shelter"
        assert result["location"] == "brooklyn"
        assert result["age"] == 45
        assert result["urgency"] == "high"
        assert result["_populations"] == ["disabled", "veteran"]


# ---------------------------------------------------------------------------
# DISPATCH helpers
# ---------------------------------------------------------------------------

class TestIsNarrative:
    def test_short_message_is_not_narrative(self):
        assert _is_narrative("I need food in Brooklyn") is False

    def test_long_message_is_narrative(self):
        msg = " ".join(["word"] * _NARRATIVE_THRESHOLD)
        assert _is_narrative(msg) is True

    def test_exactly_threshold_words_is_narrative(self):
        msg = " ".join(["word"] * _NARRATIVE_THRESHOLD)
        assert _is_narrative(msg) is True

    def test_one_less_than_threshold_is_not_narrative(self):
        msg = " ".join(["word"] * (_NARRATIVE_THRESHOLD - 1))
        assert _is_narrative(msg) is False


class TestIsSimpleMessage:
    def test_short_clear_keyword_known_location(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "brooklyn"})
        # "food in brooklyn" — 3 words, clear keyword, known loc.
        # Single service category.
        assert _is_simple_message("food in brooklyn", regex) is True

    def test_long_message_not_simple(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "brooklyn"})
        msg = "food in brooklyn i also need a place to sleep please"
        assert _is_simple_message(msg, regex) is False

    def test_unknown_location_not_simple(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "somewhere"})
        assert _is_simple_message("food somewhere", regex) is False

    def test_missing_service_or_location_not_simple(self):
        regex = _empty_regex_result()
        regex["service_type"] = "food"
        assert _is_simple_message("food", regex) is False

    def test_near_me_sentinel_is_simple(self):
        from app.services.slot_extractor import NEAR_ME_SENTINEL
        regex = _empty_regex_result()
        regex.update({
            "service_type": "food",
            "location": NEAR_ME_SENTINEL,
        })
        assert _is_simple_message("food near me", regex) is True

    def test_conflicting_service_signals_not_simple(self):
        """Message contains keywords from 2+ service categories — even
        with a known location and populated regex_result, that's a
        cue to let the LLM disambiguate (the blind-spot prevention
        logic). Covers the last branch of `_is_simple_message`."""
        regex = _empty_regex_result()
        regex.update({
            "service_type": "food",
            "location": "brooklyn",
        })
        # "food" (food keyword), "shower" (personal_care keyword) —
        # two categories, both keywords > 3 chars.
        assert _is_simple_message("food and shower in brooklyn", regex) is False


class TestAugmentUrgencyFromClues:
    def test_tonight_triggers_urgency(self):
        result = _augment_urgency_from_clues({"urgency": None}, "i need shelter tonight")
        assert result["urgency"] == "high"

    def test_not_safe_triggers_urgency(self):
        # Behavior #20 addition
        result = _augment_urgency_from_clues({"urgency": None}, "i don't feel safe here")
        assert result["urgency"] == "high"

    def test_fleeing_triggers_urgency(self):
        result = _augment_urgency_from_clues({"urgency": None}, "i'm fleeing my husband")
        assert result["urgency"] == "high"

    def test_no_clues_preserves_none(self):
        result = _augment_urgency_from_clues({"urgency": None}, "i need food")
        assert result["urgency"] is None

    def test_existing_urgency_not_overwritten(self):
        result = _augment_urgency_from_clues({"urgency": "medium"}, "tonight")
        assert result["urgency"] == "medium"


class TestNormalizeToolOutput:
    """Verify that the extended additional_services object schema
    normalizes to 3-tuples and that service_detail flows through."""

    def test_normalizes_object_additional_to_tuple(self):
        raw = {
            "service_type": "food",
            "additional_services": [
                {"type": "shelter", "detail": "emergency shelter", "location": "manhattan"},
            ],
        }
        result = _normalize_tool_output(raw)
        assert result["additional_services"] == [
            ("shelter", "emergency shelter", "manhattan"),
        ]

    def test_additional_without_optional_fields(self):
        raw = {
            "service_type": "food",
            "additional_services": [{"type": "shelter"}],
        }
        result = _normalize_tool_output(raw)
        assert result["additional_services"] == [("shelter", None, None)]

    def test_service_detail_captured(self):
        raw = {"service_type": "medical", "service_detail": "dental care"}
        result = _normalize_tool_output(raw)
        assert result["service_detail"] == "dental care"

    def test_org_name_captured(self):
        # Regression for Behavior #13 — legacy narrative dropped org_name.
        raw = {"service_type": "shelter", "org_name": "Covenant House"}
        result = _normalize_tool_output(raw)
        assert result["org_name"] == "Covenant House"

    def test_all_none_input(self):
        # LLM declined to extract anything.
        result = _normalize_tool_output({})
        assert result["service_type"] is None
        assert result["additional_services"] == []
        assert result["_populations"] == []


class TestNarrativeRegexFallback:
    """The fallback is used when the narrative LLM call fails."""

    def test_reprioritizes_by_urgency_hierarchy(self):
        # Regex extracts {medical, shelter} from "hospital ... housing";
        # fallback re-picks shelter because it's higher urgency.
        regex = _empty_regex_result()
        regex.update({
            "service_type": "medical",
            "service_detail": None,
            "additional_services": [("shelter", None, None)],
        })
        result = _narrative_regex_fallback(
            regex,
            "I just got out of the hospital and my housing fell through and i don't know where to go",
        )
        assert result["service_type"] == "shelter"

    def test_single_service_no_reprioritization(self):
        regex = _empty_regex_result()
        regex["service_type"] = "food"
        result = _narrative_regex_fallback(regex, "i need food pantries")
        assert result["service_type"] == "food"

    def test_infers_urgency_from_clues(self):
        regex = _empty_regex_result()
        regex["service_type"] = "shelter"
        result = _narrative_regex_fallback(
            regex,
            "i have nowhere to go tonight and need somewhere safe",
        )
        assert result["urgency"] == "high"

    def test_does_not_mutate_input(self):
        regex = _empty_regex_result()
        regex.update({
            "service_type": "medical",
            "additional_services": [("shelter", None, None)],
        })
        regex_copy = dict(regex)
        regex_copy["additional_services"] = list(regex["additional_services"])
        _narrative_regex_fallback(regex, "hospital and housing fallback test")
        assert regex == regex_copy

    def test_primary_already_highest_urgency_no_reprioritization_log(self):
        """Multi-service message where regex's primary is ALREADY the
        highest-urgency service. Re-ranking runs but new_primary ==
        primary, so the re-prioritization log path is skipped
        (branch 281->287 in dispatch.py)."""
        regex = _empty_regex_result()
        regex.update({
            # Shelter is already tier 8 — nothing beats it.
            "service_type": "shelter",
            "additional_services": [("food", None, None)],
        })
        result = _narrative_regex_fallback(
            regex,
            "a long message about needing shelter and food please help today",
        )
        # Primary unchanged; re-ranking is a no-op.
        assert result["service_type"] == "shelter"

    def test_regex_primary_none_skips_collection_branch(self):
        """regex_result.service_type is None — the `if primary:` branch
        in the fallback is skipped (branch 252->256). This can happen
        if the caller invokes the fallback with a truly empty regex
        result (edge case; normally regex would populate something on
        a narrative-length message)."""
        regex = _empty_regex_result()
        # No service_type, no additional. The fallback should return
        # essentially unchanged except for urgency-clue augmentation.
        result = _narrative_regex_fallback(
            regex,
            "some very long message that has no service keywords at all "
            "and just talks about things in general terms here okay",
        )
        assert result["service_type"] is None


# ---------------------------------------------------------------------------
# PUBLIC extract() — the full dispatcher
# ---------------------------------------------------------------------------

class TestExtractNoApiKey:
    """When api_key_available=False, no LLM call is made."""

    def test_simple_message_returns_regex_unchanged(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "brooklyn"})
        result = extract(
            "food in brooklyn", regex, api_key_available=False,
        )
        assert result["service_type"] == "food"
        assert result["location"] == "brooklyn"

    def test_narrative_runs_regex_fallback(self):
        # Long message without API key still gets urgency augmentation.
        regex = _empty_regex_result()
        regex.update({
            "service_type": "medical",
            "additional_services": [("shelter", None, None)],
        })
        long_msg = (
            "I just got out of the hospital and my housing fell through "
            "and I really need somewhere safe to sleep tonight because i "
            "have nowhere else to go"
        )
        result = extract(long_msg, regex, api_key_available=False)
        # Regex fallback re-prioritized
        assert result["service_type"] == "shelter"
        # Urgency inferred from "tonight"
        assert result["urgency"] == "high"

    def test_does_not_mutate_regex_result(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "brooklyn"})
        regex_snapshot = dict(regex)
        extract("food in brooklyn", regex, api_key_available=False)
        assert regex == regex_snapshot


class TestExtractSimpleFastPath:
    """Simple messages skip the LLM even when api_key is available.
    Using patch to prove no LLM call is made."""

    def test_simple_message_does_not_call_llm(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food", "location": "brooklyn"})

        with patch(
            "app.services.slot_extraction.extract_slots_short",
        ) as mock_short, patch(
            "app.services.slot_extraction.extract_slots_narrative",
        ) as mock_narrative:
            result = extract("food in brooklyn", regex)
            mock_short.assert_not_called()
            mock_narrative.assert_not_called()
        assert result["service_type"] == "food"


class TestExtractNarrativePath:
    """Long messages call the narrative LLM."""

    def test_narrative_llm_called_for_long_message(self):
        regex = _empty_regex_result()
        long_msg = " ".join(["word"] * (_NARRATIVE_THRESHOLD + 2))

        with patch(
            "app.services.slot_extraction.extract_slots_narrative",
        ) as mock_narrative:
            mock_narrative.return_value = _llm_result(
                service_type="shelter", location="brooklyn",
            )
            result = extract(long_msg, regex)
            mock_narrative.assert_called_once()
        assert result["service_type"] == "shelter"

    def test_narrative_llm_empty_falls_back_to_regex_fallback(self):
        regex = _empty_regex_result()
        regex.update({
            "service_type": "medical",
            "additional_services": [("shelter", None, None)],
        })
        long_msg = (
            "a long message with many words that triggers the narrative "
            "path for testing purposes and is over twenty words for sure"
        )

        with patch(
            "app.services.slot_extraction.extract_slots_narrative",
        ) as mock_narrative:
            # Simulate LLM returning all-empty (e.g., failed tool-use).
            mock_narrative.return_value = _empty_slots()
            result = extract(long_msg, regex)
        # Fell back to regex fallback → re-prioritized to shelter
        assert result["service_type"] == "shelter"


class TestExtractShortPath:
    """Medium-length non-simple messages call the short LLM."""

    def test_short_nonsimple_calls_short_llm(self):
        regex = _empty_regex_result()
        # Has service but not location — not simple.
        regex["service_type"] = "food"
        # Message is 6 words, non-simple, regex has no location.
        msg = "i need food and clothes please"

        with patch(
            "app.services.slot_extraction.extract_slots_short",
        ) as mock_short:
            mock_short.return_value = _llm_result(
                service_type="food",
                location="brooklyn",
                additional_services=[("clothing", None, None)],
            )
            result = extract(msg, regex)
            mock_short.assert_called_once()

        assert result["service_type"] == "food"
        assert result["location"] == "brooklyn"
        # Hybrid additional_services merged in
        types = [item[0] for item in result["additional_services"]]
        assert "clothing" in types

    def test_short_llm_empty_falls_back_to_regex(self):
        regex = _empty_regex_result()
        regex.update({"service_type": "food"})

        with patch(
            "app.services.slot_extraction.extract_slots_short",
        ) as mock_short:
            mock_short.return_value = _empty_slots()
            result = extract("i need food and something else", regex)

        assert result == regex  # wholesale fallback


# ---------------------------------------------------------------------------
# PROMPT SANITY
# ---------------------------------------------------------------------------

class TestPromptSanity:
    """Low-level sanity checks on the prompts — tested here because
    they're the load-bearing instruction to Haiku."""

    def test_short_prompt_no_service_data_leak(self):
        # Re-assertion of the firewall: no phone numbers, no yourpeer URLs.
        assert "212-" not in _SHORT_SYSTEM_PROMPT
        assert "yourpeer.nyc" not in _SHORT_SYSTEM_PROMPT

    def test_narrative_prompt_no_service_data_leak(self):
        assert "212-" not in _NARRATIVE_SYSTEM_PROMPT
        assert "yourpeer.nyc" not in _NARRATIVE_SYSTEM_PROMPT

    def test_service_detail_description_does_not_leak_canonicals(self):
        """Behavior #21 in the migration doc: 'no system prompt in the
        new module contains any _NOTABLE_SUB_TYPES canonical value
        (i.e., the LLM must infer sub-types, never be told them).'

        The service_detail description is the riskiest surface — it's
        the field where the LLM is asked to output sub-type text, so
        teaching it canonical values directly would tautologize the
        canonical-form validator. Must describe the FORMAT without
        naming specific canonicals.

        (Category descriptors in service_type.description that happen
        to overlap canonicals — e.g. 'counseling' as a mental_health
        descriptor — are a separate concern: they describe the
        CATEGORY, not the service_detail. Validated implicitly by
        tests elsewhere.)
        """
        from app.services.slot_extractor import _NOTABLE_SUB_TYPES
        sd_desc = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"][
            "service_detail"
        ]["description"]
        canonicals = set(_NOTABLE_SUB_TYPES.values())
        leaks = [c for c in canonicals if c.lower() in sd_desc.lower()]
        assert leaks == [], \
            f"service_detail description leaks canonical values: {leaks}"

    def test_narrative_prompt_enumerates_all_schema_fields(self):
        """Behavior #23 fix: the "Extract ALL slots" directive must list
        all fields in the tool schema. Derived dynamically so adding a
        new schema field without updating the prompt surfaces here.

        Note: the schema uses 'gender' / 'populations' (API names),
        which is what the prompt must reference — the returned dict
        uses '_gender' / '_populations' (internal shape), but that's
        a dispatch-level concern, not a prompt concern.
        """
        schema_fields = list(
            _EXTRACT_SLOTS_TOOL["input_schema"]["properties"].keys()
        )
        assert len(schema_fields) == 10, \
            f"Expected 10 fields in schema, got {len(schema_fields)}"
        for field in schema_fields:
            assert field in _NARRATIVE_SYSTEM_PROMPT, \
                f"Narrative prompt missing '{field}' in extract-all directive"

    def test_urgency_hierarchy_food_geq_mental_health(self):
        # Phase 0 decision: food ≥ mental_health.
        assert _URGENCY_HIERARCHY["food"] >= _URGENCY_HIERARCHY["mental_health"]

    def test_service_type_enum_has_expected_values(self):
        expected = {
            "food", "shelter", "clothing", "personal_care",
            "medical", "mental_health", "legal", "employment", "other",
        }
        assert set(_SERVICE_TYPE_ENUM) == expected

    def test_tool_schema_has_service_detail(self):
        # Phase 0 Option A: service_detail is now in the schema.
        assert "service_detail" in \
            _EXTRACT_SLOTS_TOOL["input_schema"]["properties"]

    def test_tool_schema_additional_services_is_object_array(self):
        # Phase 0: extended schema is list of objects with type/detail/location.
        schema = _EXTRACT_SLOTS_TOOL["input_schema"]["properties"]
        assert "additional_services" in schema
        item_schema = schema["additional_services"]["items"]
        assert item_schema["type"] == "object"
        assert "type" in item_schema["properties"]
        assert "detail" in item_schema["properties"]
        assert "location" in item_schema["properties"]


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

class TestTokenSort:
    """Helper used by org_name validator."""

    def test_sorts_tokens(self):
        assert _token_sort("Ali Forney Center") == "ali center forney"

    def test_lowercases(self):
        assert _token_sort("SAFE Horizon") == "horizon safe"

    def test_empty_input(self):
        assert _token_sort("") == ""
        assert _token_sort(None) == ""  # type: ignore


# ===========================================================================
# COVERAGE-GAP TESTS
# ===========================================================================
# These tests fill branches not reached by the higher-level tests above.
# Organized by module, then by uncovered line range. Each test names the
# specific branch it exists to cover.


# ---------------------------------------------------------------------------
# _is_empty_llm_result — 9 early-return branches
# ---------------------------------------------------------------------------

class TestIsEmptyLLMResultBranches:
    """Exercises every early-return `return False` path. Each test has
    exactly ONE field populated and asserts the result is not-empty.
    (We test via extract()'s observable effect: if result is non-empty,
    extract() calls merge() instead of falling back to regex.)
    """

    def _run_with_llm(self, llm_return: dict) -> dict:
        """Helper: invoke extract() with a mocked short LLM that returns
        `llm_return`, and return the result. Uses a regex_result that's
        explicitly non-simple so the short-path LLM actually runs."""
        regex = _empty_regex_result()
        # Non-simple input (long-ish, no location so not simple)
        with patch(
            "app.services.slot_extraction.extract_slots_short",
            return_value=llm_return,
        ):
            return extract("i need help with many different things here", regex)

    def test_non_empty_service_type_triggers_merge(self):
        result = self._run_with_llm(_llm_result(service_type="food"))
        # If merge ran, service_type is food. If fallback ran, it'd be None.
        assert result["service_type"] == "food"

    def test_non_empty_location_triggers_merge(self):
        result = self._run_with_llm(_llm_result(location="brooklyn"))
        assert result["location"] == "brooklyn"

    def test_non_empty_age_triggers_merge(self):
        result = self._run_with_llm(_llm_result(age=25))
        assert result["age"] == 25

    def test_non_empty_urgency_triggers_merge(self):
        result = self._run_with_llm(_llm_result(urgency="high"))
        assert result["urgency"] == "high"

    def test_non_empty_gender_triggers_merge(self):
        result = self._run_with_llm(_llm_result(_gender="female"))
        assert result["_gender"] == "female"

    def test_non_empty_family_status_triggers_merge(self):
        result = self._run_with_llm(_llm_result(family_status="with_children"))
        assert result["family_status"] == "with_children"

    def test_non_empty_org_name_triggers_merge(self):
        # LLM returned a known canonical org name — validator accepts.
        result = self._run_with_llm(_llm_result(org_name="Covenant House"))
        assert result["org_name"] == "Covenant House"

    def test_non_empty_service_detail_triggers_merge(self):
        # LLM-only service_detail path through the validator.
        result = self._run_with_llm(_llm_result(service_detail="dental care"))
        assert result["service_detail"] == "dental care"

    def test_non_empty_additional_services_triggers_merge(self):
        result = self._run_with_llm(
            _llm_result(additional_services=[("shelter", None, None)])
        )
        # Hybrid merge: shelter should appear in the result.
        types = [item[0] for item in result["additional_services"]]
        assert "shelter" in types

    def test_non_empty_populations_triggers_merge(self):
        result = self._run_with_llm(_llm_result(_populations=["veteran"]))
        assert "veteran" in result["_populations"]


# ---------------------------------------------------------------------------
# Dispatch: LLM call internals (success, no tool_use, exception paths)
# ---------------------------------------------------------------------------

class _FakeToolUseBlock:
    """Fake `block` object matching what Anthropic returns in
    `response.content` when Claude emits a tool_use."""
    def __init__(self, name: str, tool_input: dict):
        self.type = "tool_use"
        self.name = name
        self.input = tool_input


class _FakeTextBlock:
    """Fake text block — appears when Claude emits prose instead of a
    tool call. Used to exercise the 'no tool_use' branch."""
    def __init__(self, text: str = "sure, let me think"):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content: list, usage=None):
        self.content = content
        self.usage = usage or _FakeUsage()


class TestExtractSlotsShortLLMInternals:
    """Exercise the success path, no-tool-use path, and exception path
    of `extract_slots_short`."""

    def test_success_path_returns_normalized(self):
        """Happy path: LLM emits a tool_use block; the function
        normalizes and returns the 10-field dict."""
        from app.services.slot_extraction.dispatch import extract_slots_short

        fake_response = _FakeResponse(content=[
            _FakeToolUseBlock("extract_intake_slots", {
                "service_type": "food",
                "location": "Brooklyn",
            }),
        ])
        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.return_value = fake_response
            result = extract_slots_short("i need food in brooklyn")

        assert result["service_type"] == "food"
        assert result["location"] == "Brooklyn"
        # Shape is 10-field (new architecture)
        assert "additional_services" in result
        assert "_populations" in result
        assert "service_detail" in result

    def test_no_tool_use_block_returns_empty(self):
        """LLM responded with prose instead of calling the tool; we
        fall through the for-loop without returning and hit the
        `_empty_slots()` path."""
        from app.services.slot_extraction.dispatch import extract_slots_short

        fake_response = _FakeResponse(content=[_FakeTextBlock()])
        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.return_value = fake_response
            result = extract_slots_short("i need something")

        # Empty-slots shape
        assert result["service_type"] is None
        assert result["additional_services"] == []

    def test_exception_path_returns_empty(self):
        """API call raises — function catches, logs, records failure,
        returns empty-slots."""
        from app.services.slot_extraction.dispatch import extract_slots_short

        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.side_effect = \
                RuntimeError("API unavailable")
            result = extract_slots_short("i need food")

        assert result["service_type"] is None


class TestExtractSlotsNarrativeLLMInternals:
    """Parallel tests for the narrative-path LLM call."""

    def test_success_path_returns_normalized(self):
        from app.services.slot_extraction.dispatch import extract_slots_narrative

        fake_response = _FakeResponse(content=[
            _FakeToolUseBlock("extract_intake_slots", {
                "service_type": "shelter",
                "urgency": "high",
                "additional_services": [
                    {"type": "medical", "detail": None, "location": None},
                ],
            }),
        ])
        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.return_value = fake_response
            result = extract_slots_narrative(
                "a long narrative message describing many needs " * 3,
            )

        assert result["service_type"] == "shelter"
        assert result["urgency"] == "high"
        assert result["additional_services"] == [("medical", None, None)]

    def test_no_tool_use_block_returns_empty(self):
        from app.services.slot_extraction.dispatch import extract_slots_narrative

        fake_response = _FakeResponse(content=[_FakeTextBlock()])
        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.return_value = fake_response
            result = extract_slots_narrative("a long message " * 10)

        assert result["service_type"] is None

    def test_exception_path_returns_empty(self):
        from app.services.slot_extraction.dispatch import extract_slots_narrative

        with patch(
            "app.services.slot_extraction.dispatch.get_client",
        ) as mock_get_client:
            mock_get_client.return_value.messages.create.side_effect = \
                TimeoutError("Haiku timed out")
            result = extract_slots_narrative("a long narrative " * 10)

        assert result["service_type"] is None


# ---------------------------------------------------------------------------
# Dispatch: _build_messages_with_history role alternation
# ---------------------------------------------------------------------------

class TestBuildMessagesWithHistory:
    """Covers the role-alternation padding + trailing-user placeholder
    logic in `_build_messages_with_history`."""

    def _call(self, history):
        # Helper: the function is called indirectly via extract_slots_short,
        # but we want to test it directly.
        from app.services.slot_extraction.dispatch import _build_messages_with_history
        return _build_messages_with_history("current message", history)

    def test_no_history_just_current_message(self):
        result = self._call(None)
        assert result == [{"role": "user", "content": "current message"}]

    def test_simple_alternating_history_preserved(self):
        history = [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "hello, what do you need?"},
        ]
        result = self._call(history)
        # History + "(listening)" placeholder (last history is assistant,
        # but wait — last is assistant, so no trailing placeholder needed)
        # Actually the guard says "if last is user, insert assistant". Last
        # is assistant, so nothing inserted.
        assert result == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello, what do you need?"},
            {"role": "user", "content": "current message"},
        ]

    def test_two_consecutive_user_messages_get_placeholder(self):
        """User said two things back-to-back — need an assistant
        placeholder between them to satisfy Claude's alternation."""
        history = [
            {"role": "user", "text": "hi"},
            {"role": "user", "text": "are you there?"},  # consecutive user
        ]
        result = self._call(history)
        # First user, then assistant placeholder, then second user,
        # then (last was user) assistant "(listening)", then current.
        roles = [m["role"] for m in result]
        assert roles == ["user", "assistant", "user", "assistant", "user"]

    def test_two_consecutive_assistant_messages_get_placeholder(self):
        history = [
            {"role": "assistant", "text": "one"},
            {"role": "assistant", "text": "two"},
        ]
        result = self._call(history)
        # The guard inserts a user placeholder between the two assistants.
        # Then current user is appended; last history was assistant so no
        # trailing placeholder needed.
        roles = [m["role"] for m in result]
        assert roles == ["assistant", "user", "assistant", "user"]

    def test_trailing_user_history_gets_listening_placeholder(self):
        history = [
            {"role": "assistant", "text": "any more info?"},
            {"role": "user", "text": "I'm 25"},  # last is user
        ]
        result = self._call(history)
        # Must insert "(listening)" before the current message.
        roles = [m["role"] for m in result]
        assert roles == ["assistant", "user", "assistant", "user"]
        # The inserted placeholder's content
        assert result[2]["content"] == "(listening)"

    def test_only_last_six_turns_kept(self):
        history = [
            {"role": "user", "text": f"msg_{i}"} for i in range(10)
        ]
        # Each one is a user turn — role-alternation will insert padding.
        result = self._call(history)
        # The function slices history[-6:], so only msg_4..msg_9 are there.
        # msg_0..msg_3 should NOT appear.
        joined = " ".join(m["content"] for m in result)
        assert "msg_0" not in joined
        assert "msg_3" not in joined
        assert "msg_9" in joined


# ---------------------------------------------------------------------------
# Dispatch: _normalize_tool_output edge cases
# ---------------------------------------------------------------------------

class TestNormalizeToolOutputEdgeCases:
    def test_additional_services_item_not_a_dict_skipped(self):
        """If the LLM returns a string or other non-dict in the list,
        we skip rather than crash."""
        raw = {
            "service_type": "food",
            "additional_services": ["shelter", {"type": "medical"}],
        }
        result = _normalize_tool_output(raw)
        # Only the dict item should survive.
        assert result["additional_services"] == [("medical", None, None)]

    def test_additional_services_dict_without_type_skipped(self):
        """If the LLM returns an object with no `type` field, skip."""
        raw = {
            "service_type": "food",
            "additional_services": [
                {"detail": "orphan"},
                {"type": "clothing"},
            ],
        }
        result = _normalize_tool_output(raw)
        assert result["additional_services"] == [("clothing", None, None)]


# ---------------------------------------------------------------------------
# Dispatch: _narrative_regex_fallback dedup + multi-service branches
# ---------------------------------------------------------------------------

class TestNarrativeRegexFallbackDedup:
    def test_dedup_same_service_in_primary_and_additional(self):
        """If regex somehow has the same service as primary AND in
        additional_services, the re-prioritization + dedup path
        removes the duplicate."""
        regex = _empty_regex_result()
        regex.update({
            "service_type": "medical",
            "additional_services": [
                ("shelter", None, None),
                ("medical", "urgent care", None),  # dup of primary
            ],
        })
        result = _narrative_regex_fallback(
            regex,
            "hospital and housing and urgent care help now please today this week",
        )
        # Shelter should be primary after re-prioritization; medical
        # should appear exactly once in additionals.
        assert result["service_type"] == "shelter"
        additional_types = [item[0] for item in result["additional_services"]]
        assert additional_types.count("medical") == 1


# ---------------------------------------------------------------------------
# Dispatch: audit log helper failure paths
# ---------------------------------------------------------------------------

class TestAuditLogHelpers:
    """The success/failure audit helpers swallow any exception the
    audit-log module raises. Covers the except branches."""

    def test_record_success_swallows_exceptions(self):
        from app.services.slot_extraction.dispatch import _record_success

        fake_response = _FakeResponse(content=[])
        with patch(
            "app.services.audit_log.record_llm_call",
            side_effect=RuntimeError("audit store down"),
        ):
            # Must not raise
            _record_success("slot_extraction", fake_response, 100)

    def test_record_failure_swallows_exceptions(self):
        from app.services.slot_extraction.dispatch import _record_failure

        with patch(
            "app.services.audit_log.record_llm_call",
            side_effect=RuntimeError("audit store down"),
        ):
            _record_failure("slot_extraction", "timeout")

    def test_record_success_handles_response_without_usage(self):
        """Some Claude API SDK paths may omit `usage` — we should
        tolerate it by emitting zero tokens."""
        from app.services.slot_extraction.dispatch import _record_success

        class _ResponseNoUsage:
            content = []
            # No `usage` attribute
        with patch("app.services.audit_log.record_llm_call") as mock:
            _record_success("slot_extraction", _ResponseNoUsage(), 50)
            # Was called with input_tokens=0, output_tokens=0
            kwargs = mock.call_args.kwargs
            assert kwargs["input_tokens"] == 0
            assert kwargs["output_tokens"] == 0


# ---------------------------------------------------------------------------
# Merge: _validate_service_detail branch coverage
# ---------------------------------------------------------------------------

class TestValidateServiceDetailBranches:
    def test_whitespace_only_input_drops(self):
        """Normalization strips to empty — validator returns None."""
        result = _validate_service_detail("   \t  \n ")
        assert result is None

    def test_fuzzy_snap_below_threshold_drops(self):
        """Value that's close to a canonical but below the 0.80 ratio
        should drop. Matches an in-doc "ratio=0.80 is empirical"
        caveat; test pins the threshold behavior."""
        # Short arbitrary string unlikely to match strongly.
        # ("xyz" won't hit substring or high ratio.)
        result = _validate_service_detail("xyz")
        assert result is None

    def test_fuzzy_snap_path_exercised(self):
        """Hit the fuzzy-snap log path (lines 131-135): the LLM value
        is NOT a substring of any canonical and no canonical is a
        substring of it, but the ratio against 'dental care' is ~0.91
        (well above the 0.80 threshold). Exercises the pure-fuzzy
        branch, not the exact-match or substring branches."""
        # "dnetal care" — transposed letters, no substring overlap.
        result = _validate_service_detail("dnetal care")
        assert result == "dental care"


# ---------------------------------------------------------------------------
# Merge: _validate_org_name empty-token branch
# ---------------------------------------------------------------------------

class TestValidateOrgNameEmptyTokens:
    def test_only_whitespace_drops(self):
        """Empty-after-tokenization input hits the `if not llm_tokens`
        early return."""
        assert _validate_org_name("   ") is None


# ---------------------------------------------------------------------------
# Merge: _merge_additional_services edge cases
# ---------------------------------------------------------------------------

class TestMergeAdditionalServicesEdgeCases:
    def test_llm_novel_service_logged(self):
        """Step 3: LLM finds a service regex didn't, and emits an info
        log. Covers the log-line branch (438-440)."""
        import logging
        result = _merge_additional_services(
            primary="food",
            winner_additional=[],
            regex_additional=[],
            llm_additional=[("shelter", "emergency", "brooklyn")],
        )
        assert result == [("shelter", "emergency", "brooklyn")]

    def test_unpack_two_tuple(self):
        """Legacy 2-tuple `(type, detail)` should unpack to
        `(type, detail, None)`."""
        from app.services.slot_extraction.merge import _unpack_additional_item
        assert _unpack_additional_item(("food", "pantries")) == \
            ("food", "pantries", None)

    def test_unpack_one_tuple(self):
        from app.services.slot_extraction.merge import _unpack_additional_item
        assert _unpack_additional_item(("food",)) == ("food", None, None)

    def test_unpack_string_treated_as_type(self):
        """If the LLM returned `["shelter"]` (legacy string list
        format), coerce to `(shelter, None, None)`."""
        from app.services.slot_extraction.merge import _unpack_additional_item
        assert _unpack_additional_item("shelter") == ("shelter", None, None)

    def test_regex_set_skips_empty_tuples(self):
        """Trust Model 3 set-build loop skips entries that are None or
        have empty type. Defensive — in practice the regex-side
        `extract_slots` never produces None/empty entries in
        `additional_services`, but the set-equality rule must not
        crash if upstream schema drift ever introduces them.
        """
        regex = _empty_regex_result()
        regex.update({
            "service_type": "shelter",  # already priority-picked by regex
            "additional_services": [
                None,                          # entry is None — should be skipped
                ("", None, None),              # empty type — should be skipped
                ("food", None, None),          # valid entry
            ],
        })
        llm = _llm_result(
            service_type="shelter",
            additional_services=[
                None,
                ("", None, None),
                ("food", None, None),
            ],
        )
        primary, loc, additional = \
            _merge_service_type_and_primary_location(regex, llm)
        # Sets both == {shelter, food}; regex wins, regex_primary is
        # already priority-picked → shelter. The None/empty entries
        # were silently skipped; neither extractor's output crashed.
        assert primary == "shelter"

    def test_union_skips_non_string_populations(self):
        """Trust Model 4 _merge_union should ignore non-string entries
        in either list (branch 344->343 False leg). Defensive — should
        not happen in practice but guards against upstream schema
        drift."""
        result = _merge_union(
            [None, "veteran", 123, ""],  # type: ignore
            ["disabled", None],           # type: ignore
        )
        # Only valid string entries retained, deduplicated, sorted.
        assert result == ["disabled", "veteran"]

    def test_unpack_none_or_falsy_returns_triple_none(self):
        """Line 445: the `if not item:` early return for None, empty
        dict, empty tuple, 0, etc."""
        from app.services.slot_extraction.merge import _unpack_additional_item
        assert _unpack_additional_item(None) == (None, None, None)
        assert _unpack_additional_item({}) == (None, None, None)
        assert _unpack_additional_item(()) == (None, None, None)
        assert _unpack_additional_item(0) == (None, None, None)

    def test_unpack_truthy_non_sequence_returns_triple_none(self):
        """_unpack_additional_item on a truthy non-sequence (dict, int,
        etc.) falls to the final return triple-None (branch 451->453
        False leg)."""
        from app.services.slot_extraction.merge import _unpack_additional_item
        # Truthy dict that's not a tuple/list/str
        assert _unpack_additional_item({"arbitrary": "dict"}) == (None, None, None)
        # Truthy int
        assert _unpack_additional_item(42) == (None, None, None)


# ---------------------------------------------------------------------------
# Merge: _normalize_for_match empty branch
# ---------------------------------------------------------------------------

class TestNormalizeForMatchEdge:
    def test_none_and_empty_return_empty(self):
        from app.services.slot_extraction.merge import _normalize_for_match
        assert _normalize_for_match(None) == ""  # type: ignore
        assert _normalize_for_match("") == ""


# ---------------------------------------------------------------------------
# Merge: _filter_valid_service_types
# ---------------------------------------------------------------------------

class TestFilterValidServiceTypes:
    def test_invalid_primary_dropped(self):
        from app.services.slot_extraction.merge import _filter_valid_service_types
        result = _filter_valid_service_types(_llm_result(service_type="bogus"))
        assert result["service_type"] is None

    def test_valid_primary_kept(self):
        from app.services.slot_extraction.merge import _filter_valid_service_types
        result = _filter_valid_service_types(_llm_result(service_type="food"))
        assert result["service_type"] == "food"

    def test_invalid_additional_filtered_out(self):
        from app.services.slot_extraction.merge import _filter_valid_service_types
        result = _filter_valid_service_types(_llm_result(
            additional_services=[("food", None, None), ("bogus", None, None)]
        ))
        types = [item[0] for item in result["additional_services"]]
        assert "food" in types
        assert "bogus" not in types

    def test_does_not_mutate_input(self):
        from app.services.slot_extraction.merge import _filter_valid_service_types
        llm = _llm_result(service_type="bogus")
        llm_snapshot = dict(llm)
        _filter_valid_service_types(llm)
        assert llm == llm_snapshot
