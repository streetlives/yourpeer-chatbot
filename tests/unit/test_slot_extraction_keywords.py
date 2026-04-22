"""
Tests for slot extraction keyword additions and negation-aware extraction.

Covers changes to slot_extractor.py:
  - Medical keywords: insulin, diabetic, diabetes, inhaler, asthma, etc.
  - Reentry population phrases: felon, felony, criminal record, etc.
  - Foster care shelter keywords: aging out, foster care
  - Negation-aware extraction: _is_negated(), negated keywords skipped

Run with: python -m pytest tests/unit/test_slot_extraction_keywords.py -v
"""

import pytest
from app.services.slot_extractor import (
    extract_slots,
    _extract_populations,
    _is_negated,
    _extract_all_service_types,
)


# -----------------------------------------------------------------------
# MEDICAL KEYWORDS
# -----------------------------------------------------------------------

class TestMedicalKeywordAdditions:
    """New chronic-condition and medication keywords → service_type=medical."""

    @pytest.mark.parametrize("keyword", [
        "insulin", "diabetic", "diabetes", "inhaler", "asthma",
        "dialysis", "blood sugar", "epipen",
    ])
    def test_new_keyword_extracts_medical(self, keyword):
        assert extract_slots(keyword)["service_type"] == "medical"

    @pytest.mark.parametrize("keyword", [
        "medication", "prescription", "doctor", "clinic", "hospital",
        "dentist", "methadone", "urgent care", "hiv testing",
    ])
    def test_existing_keywords_unchanged(self, keyword):
        assert extract_slots(keyword)["service_type"] == "medical"

    def test_full_diabetic_scenario(self):
        s = extract_slots("I'm diabetic and I ran out of insulin")
        assert s["service_type"] == "medical"

    def test_insulin_with_location(self):
        s = extract_slots("I need insulin in Manhattan")
        assert s["service_type"] == "medical"
        assert s["location"] == "manhattan"

    def test_asthma_with_context(self):
        s = extract_slots("my kid has asthma and needs an inhaler")
        assert s["service_type"] == "medical"

    def test_blood_sugar_not_just_blood(self):
        """'blood sugar' should match as compound, not 'blood' alone."""
        s = extract_slots("my blood sugar is dangerously high")
        assert s["service_type"] == "medical"


# -----------------------------------------------------------------------
# REENTRY POPULATION PHRASES
# -----------------------------------------------------------------------

class TestReentryPopulationAdditions:
    """New criminal-justice phrases → population=reentry."""

    @pytest.mark.parametrize("phrase", [
        "felon", "felony", "ex-felon", "criminal record",
        # "have a record" retired — false positive: "I have a record of my meetings" (REGEX_AUDIT)
        "been to prison", "was in prison",
        "got out of prison",
        # "did time" retired — false positive: "I did time management training" (REGEX_AUDIT)
    ])
    def test_new_phrase_extracts_reentry(self, phrase):
        assert "reentry" in _extract_populations(phrase)

    @pytest.mark.parametrize("phrase", [
        "just got out of jail", "on parole", "on probation",
        "formerly incarcerated", "released from rikers",
    ])
    def test_existing_phrases_unchanged(self, phrase):
        assert "reentry" in _extract_populations(phrase)

    def test_felon_with_employment_intent(self):
        """'felon looking for work' → employment + reentry."""
        # Uses "looking for work" (multi-word phrase still in regex)
        # instead of "looking for a job" ("job" retired from regex — REGEX_AUDIT)
        s = extract_slots("looking for work that hires felons near East New York")
        assert s["service_type"] == "employment"
        p = _extract_populations("looking for work that hires felons")
        assert "reentry" in p

    def test_felon_no_false_positive_on_unrelated(self):
        """Ensure 'felon' doesn't appear in unrelated text."""
        p = _extract_populations("I need food in Brooklyn")
        assert "reentry" not in p

    def test_criminal_record_as_compound(self):
        s = extract_slots("I have a criminal record and need employment help")
        assert s["service_type"] == "employment"
        p = _extract_populations("I have a criminal record")
        assert "reentry" in p


# -----------------------------------------------------------------------
# FOSTER CARE KEYWORDS
# -----------------------------------------------------------------------

class TestFosterCareKeywords:
    """Foster care / aging out phrases.

    Pre-R34-Sprint: 'aging out', 'foster care' etc. were shelter
    keywords — any mention auto-assigned service_type=shelter.

    Post-R34-Sprint (peer_aging_out_foster investigation): these
    phrases describe a SITUATION, not a service. They set
    _populations=['foster_youth'] but leave service_type=None unless
    an explicit service keyword is also present. This lets the bot
    route open-question flows ('aging out of foster care, what do I
    do') to a service-category menu rather than silently assuming
    shelter.
    """

    @pytest.mark.parametrize("phrase", [
        "aging out",
        "aged out",
        "foster care",
        "aging out of foster",
    ])
    def test_phrase_sets_foster_youth_population_only(self, phrase):
        """Pre-fix these forced service_type=shelter. Post-fix they set
        the population flag but leave service_type open — the user
        hasn't said what they need yet."""
        s = extract_slots(phrase)
        assert s["service_type"] is None, (
            f"'{phrase}' should not auto-assign service_type — it "
            f"describes a situation, not a service. Got {s['service_type']!r}"
        )
        assert "foster_youth" in (s.get("_populations") or []), (
            f"'{phrase}' should set _populations=['foster_youth']. "
            f"Got {s.get('_populations')}"
        )

    def test_open_question_no_auto_shelter(self):
        """R34 peer_aging_out_foster target — the failing eval case."""
        s = extract_slots("aging out of foster care next month, what do I do")
        assert s["service_type"] is None, (
            "Open-ended question after foster-care context should not "
            "auto-assign shelter. The bot should offer service "
            "categories via the fallback menu."
        )
        assert "foster_youth" in (s.get("_populations") or [])

    def test_explicit_shelter_with_foster_context_still_extracts(self):
        """Safety check — when shelter IS explicitly requested alongside
        foster context, service_type=shelter is still correctly set."""
        s = extract_slots("aging out of foster care and need a place to stay")
        assert s["service_type"] == "shelter"
        assert "foster_youth" in (s.get("_populations") or [])

    def test_former_foster_with_explicit_shelter(self):
        s = extract_slots("former foster youth, need shelter tonight")
        assert s["service_type"] == "shelter"
        assert "foster_youth" in (s.get("_populations") or [])

    def test_aging_out_with_age_and_location_no_auto_shelter(self):
        """Updated from 'I'm aging out of foster care, 21, in the Bronx'
        which used to auto-pick shelter. Post-fix: age + location are
        captured, population is set, service_type remains open for the
        user to clarify."""
        s = extract_slots("I'm aging out of foster care, 21, in the Bronx")
        assert s["service_type"] is None
        assert s["age"] == 21
        assert "bronx" in s["location"]
        assert "foster_youth" in (s.get("_populations") or [])

    def test_existing_shelter_keywords_unchanged(self):
        """Regression guard — removing aging-out-as-shelter must not
        affect other shelter keyword extraction."""
        for kw in ["shelter", "homeless", "place to stay", "evicted"]:
            assert extract_slots(kw)["service_type"] == "shelter"


# -----------------------------------------------------------------------
# NEGATION-AWARE EXTRACTION
# -----------------------------------------------------------------------

class TestNegationDetection:
    """_is_negated() correctly identifies negated keyword positions."""

    @pytest.mark.parametrize("text,pos,expected", [
        ("not food, shelter", 4, True),
        ("forget food, shelter", 7, True),
        ("dont want food, need shelter", 10, True),
        ("don't want food, need shelter", 11, True),
        ("instead of food, shelter", 11, True),
        ("skip food, give me clothing", 5, True),
        ("no more food, shelter", 8, True),
        ("i need food in brooklyn", 7, False),
        ("where can i find food", 18, False),
        ("food is what I need", 0, False),
    ])
    def test_is_negated(self, text, pos, expected):
        assert _is_negated(text.lower(), pos) is expected


class TestNegationAwareExtraction:
    """Negated service keywords are excluded from extraction."""

    def test_not_food_shelter(self):
        assert extract_slots("not food, shelter")["service_type"] == "shelter"

    def test_forget_food_shelter(self):
        assert extract_slots("forget food, I need shelter")["service_type"] == "shelter"

    def test_instead_of_food(self):
        assert extract_slots("instead of food, shelter in Brooklyn")["service_type"] == "shelter"

    def test_dont_want_food(self):
        assert extract_slots("dont want food, need shelter")["service_type"] == "shelter"

    def test_dont_want_apostrophe(self):
        assert extract_slots("don't want food, need shelter")["service_type"] == "shelter"

    def test_skip_food(self):
        assert extract_slots("skip food, give me clothing")["service_type"] == "clothing"

    def test_both_negated_returns_none(self):
        assert extract_slots("not food, not shelter")["service_type"] is None

    def test_normal_extraction_unaffected(self):
        # Negation check doesn't block legitimate compound extraction.
        # Primary service is decided by _SERVICE_NEED_PRIORITY:
        # shelter (tier 1) wins over food (tier 2).
        assert extract_slots("I need food and shelter")["service_type"] == "shelter"

    def test_shelter_not_food(self):
        assert extract_slots("shelter not food")["service_type"] == "shelter"

    def test_negation_with_location(self):
        s = extract_slots("not food, shelter in Queens")
        assert s["service_type"] == "shelter"
        assert s["location"] == "queens"

    def test_no_more_food(self):
        assert extract_slots("no more food, I need clothing")["service_type"] == "clothing"

    def test_not_looking_for_food(self):
        assert extract_slots("not looking for food, need shelter")["service_type"] == "shelter"
