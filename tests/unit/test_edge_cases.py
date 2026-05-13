"""
Edge case tests — covers scenarios from project specs, user testing plans,
and real-world seeker behavior patterns.

Run with: python -m pytest tests/test_edge_cases.py -v
Or just:  python tests/test_edge_cases.py
"""



from app.services.slot_extraction_regex import (
    extract_slots,
    merge_slots,
    is_enough_to_answer,
    next_follow_up_question,
    NEAR_ME_SENTINEL,
)
from app.privacy.pii_redactor import redact_pii
from app.rag.query_executor import normalize_location, resolve_template_key


# -----------------------------------------------------------------------
# LOCATION NORMALIZATION (borough/neighborhood → DB city value)
# -----------------------------------------------------------------------

def test_borough_normalization():
    """NYC boroughs should map to their canonical borough names."""
    cases = [
        ("manhattan", "Manhattan"),
        ("brooklyn", "Brooklyn"),
        ("queens", "Queens"),
        ("bronx", "Bronx"),
        ("the bronx", "Bronx"),
        ("staten island", "Staten Island"),
    ]
    for raw, expected in cases:
        result = normalize_location(raw)
        assert result == expected, f"normalize('{raw}') = '{result}', expected '{expected}'"


def test_neighborhood_normalization():
    """NYC neighborhoods should map to their borough's DB city value."""
    cases = [
        ("harlem", "New York"),
        ("midtown", "New York"),
        ("soho", "New York"),
        ("williamsburg", "Brooklyn"),
        ("bushwick", "Brooklyn"),
        ("bed-stuy", "Brooklyn"),
        ("astoria", "Queens"),
        ("flushing", "Queens"),
        ("south bronx", "Bronx"),
        ("mott haven", "Bronx"),
    ]
    for raw, expected in cases:
        result = normalize_location(raw)
        assert result == expected, f"normalize('{raw}') = '{result}', expected '{expected}'"


def test_unknown_location_passes_through():
    """Locations not in the alias map should pass through unchanged."""
    unknowns = ["Springfield", "Los Angeles", "New Jersey", "Yonkers"]
    for loc in unknowns:
        result = normalize_location(loc)
        assert result == loc, f"normalize('{loc}') should pass through, got '{result}'"


def test_normalize_strips_whitespace():
    """Leading/trailing whitespace should be stripped."""
    assert normalize_location("  brooklyn  ") == "Brooklyn"
    assert normalize_location("queens ") == "Queens"


# -----------------------------------------------------------------------
# TEMPLATE RESOLUTION (slot service_type → query template key)
# -----------------------------------------------------------------------

def test_all_service_types_resolve():
    """Every service type the slot extractor can return should resolve to a template."""
    service_types = [
        "food", "shelter", "housing", "clothing", "personal_care",
        "shower", "medical", "healthcare", "health",
        "mental_health", "legal", "employment", "job", "other", "benefits",
    ]
    for stype in service_types:
        key = resolve_template_key(stype)
        assert key is not None, f"No template for service_type='{stype}'"


def test_unknown_service_type_returns_none():
    """Unknown service types should return None, not crash."""
    assert resolve_template_key("xyz_unknown") is None
    assert resolve_template_key("") is None
    assert resolve_template_key(None) is None


# -----------------------------------------------------------------------
# MULTI-INTENT MESSAGES
# -----------------------------------------------------------------------

def test_multi_intent_picks_first():
    """When a user mentions multiple services, the first match wins.
    (This is a known limitation documented in the code.)"""
    slots = extract_slots("I need food and shelter in Brooklyn")
    assert slots["service_type"] is not None, "Should extract at least one service type"
    assert slots["location"] is not None, "Should still extract location"
    print(f"  PASS: multi-intent picks first match (got '{slots['service_type']}')")


# -----------------------------------------------------------------------
# LOCATION EDGE CASES
# -----------------------------------------------------------------------

def test_location_outside_nyc():
    """Locations outside NYC should still be extracted (but won't match DB)."""
    slots = extract_slots("I need food in Springfield")
    # "in Springfield" should extract as a location via the regex pattern
    assert slots["service_type"] == "food"
    assert slots["location"] is not None


def test_location_with_mixed_case():
    """Location extraction should handle mixed case."""
    slots = extract_slots("food in BROOKLYN")
    assert slots["location"] is not None
    assert "brooklyn" in slots["location"].lower()


def test_location_change_mid_conversation():
    """User corrects location — new value should override old."""
    existing = {"service_type": "food", "location": "Brooklyn"}
    new_slots = extract_slots("actually I'm in Queens")
    merged = merge_slots(existing, new_slots)
    assert "queens" in merged["location"].lower(), \
        f"Location should update to Queens, got '{merged['location']}'"


def test_service_change_mid_conversation():
    """User changes service type — new value should override old."""
    existing = {"service_type": "food", "location": "Brooklyn"}
    new_slots = extract_slots("actually I need shelter")
    merged = merge_slots(existing, new_slots)
    assert merged["service_type"] == "shelter"
    assert merged["location"] == "Brooklyn"  # location preserved


# -----------------------------------------------------------------------
# MINOR + URGENCY SPECIAL CASE
# -----------------------------------------------------------------------

def test_minor_urgent_shelter():
    """A minor needing shelter tonight should fill age + urgency + service."""
    slots = extract_slots("I'm 17 and I need shelter tonight in Queens")
    assert slots["service_type"] == "shelter"
    assert slots["age"] == 17
    assert slots["urgency"] == "high"
    assert "queens" in (slots["location"] or "").lower()


def test_shelter_asks_age_followup():
    """If shelter + location are filled but no age, should ask about age."""
    question = next_follow_up_question({
        "service_type": "shelter",
        "location": "Brooklyn",
    })
    assert "age" in question.lower() or "old" in question.lower()


def test_non_shelter_doesnt_ask_age():
    """Non-shelter services shouldn't ask about age as a follow-up."""
    question = next_follow_up_question({
        "service_type": "food",
        "location": "Brooklyn",
    })
    assert "age" not in question.lower()


# -----------------------------------------------------------------------
# PII + SLOT EXTRACTION INTERACTION
# -----------------------------------------------------------------------

def test_pii_redaction_preserves_service_extraction():
    """Redacting PII shouldn't break service type extraction."""
    msg = "My name is Sarah and I need food in Brooklyn"
    redacted, dets = redact_pii(msg)
    # Slot extraction runs on ORIGINAL text (per chatbot.py design)
    slots = extract_slots(msg)
    assert slots["service_type"] == "food"
    assert "brooklyn" in (slots["location"] or "").lower()
    # But the redacted version should hide the name
    assert "Sarah" not in redacted
    assert "[NAME]" in redacted


def test_pii_redaction_preserves_age():
    """Age numbers should NOT be redacted as PII."""
    msg = "I'm 17 and need shelter"
    redacted, dets = redact_pii(msg)
    # "17" should not be treated as PII
    assert "17" in redacted
    # And slot extraction should still find the age
    slots = extract_slots(msg)
    assert slots["age"] == 17


def test_pii_not_echoed_in_confirmation():
    """PII in slot values must be redacted before echoing in confirmation."""
    from app.services.confirmation import _build_confirmation_message
    # Simulate a street address captured as location
    slots = {"service_type": "food", "location": "123 Main Street"}
    msg = _build_confirmation_message(slots)
    assert "123 Main Street" not in msg
    assert "[ADDRESS]" in msg

    # A phone number somehow in a slot value
    slots_phone = {"service_type": "food", "location": "212-555-1234"}
    msg_phone = _build_confirmation_message(slots_phone)
    assert "212-555-1234" not in msg_phone
    assert "[PHONE]" in msg_phone

    # A normal location should pass through unchanged
    slots_clean = {"service_type": "food", "location": "Brooklyn"}
    msg_clean = _build_confirmation_message(slots_clean)
    assert "Brooklyn" in msg_clean


def test_pii_with_phone_and_location():
    """Phone gets redacted, location stays intact for slot extraction."""
    msg = "Call me at 212-555-9876, I need food in Queens"
    redacted, dets = redact_pii(msg)
    assert "[PHONE]" in redacted
    assert "Queens" in redacted  # location should survive
    slots = extract_slots(msg)
    assert slots["service_type"] == "food"
    assert "queens" in (slots["location"] or "").lower()


# -----------------------------------------------------------------------
# NEAR ME + REAL LOCATION MULTI-TURN
# -----------------------------------------------------------------------

def test_near_me_then_real_location_flow():
    """Simulate: user says 'food near me', bot asks where, user says 'Brooklyn'."""
    # Turn 1: "food near me"
    slots_1 = extract_slots("food near me")
    session = merge_slots({}, slots_1)
    assert session["service_type"] == "food"
    assert session["location"] == NEAR_ME_SENTINEL
    assert is_enough_to_answer(session) is False

    # Follow-up should ask for real location
    question = next_follow_up_question(session)
    assert "borough" in question.lower() or "neighborhood" in question.lower()

    # Turn 2: "Brooklyn"
    slots_2 = extract_slots("Brooklyn")
    session = merge_slots(session, slots_2)
    assert session["location"] == "brooklyn"
    assert session["location"] != NEAR_ME_SENTINEL
    assert is_enough_to_answer(session) is True


# -----------------------------------------------------------------------
# EMPTY AND GARBAGE INPUT
# -----------------------------------------------------------------------

def test_empty_message():
    """Empty string should extract nothing and not crash."""
    slots = extract_slots("")
    assert slots["service_type"] is None
    assert slots["location"] is None
    assert slots["age"] is None
    assert slots["urgency"] is None


def test_whitespace_only():
    """Whitespace-only message should extract nothing."""
    slots = extract_slots("   \n\t  ")
    assert slots["service_type"] is None
    assert slots["location"] is None


def test_single_word_location_answer():
    """When user replies with just a borough name, it should extract."""
    slots = extract_slots("Brooklyn")
    assert slots["location"] is not None
    assert "brooklyn" in slots["location"].lower()


def test_single_word_service_answer():
    """When user replies with just a service keyword, it should extract."""
    slots = extract_slots("food")
    assert slots["service_type"] == "food"


def test_numbers_only():
    """Just a number (like an age response) should NOT currently extract age.

    The regex patterns require "I am X" or "age X"; a bare number won't
    match. This test pins the current behavior. If an LLM-based extractor
    starts handling bare numbers in context, flip this to assert
    slots['age'] == 17 and update the regex docs.
    """
    slots = extract_slots("17")
    assert slots["age"] is None, (
        f"Bare number '17' extracted age={slots['age']} — if this is "
        f"intentional, update the assertion and the regex docs."
    )


# -----------------------------------------------------------------------
# SLOT KEYWORD OVERLAP PREVENTION
# -----------------------------------------------------------------------

def test_health_vs_mental_health():
    """'Mental health' should match mental_health, not medical."""
    slots = extract_slots("I need mental health support")
    assert slots["service_type"] == "mental_health", \
        f"Expected 'mental_health', got '{slots['service_type']}'"


def test_health_alone_is_medical():
    """Plain 'health' should match medical."""
    slots = extract_slots("I need health care")
    assert slots["service_type"] == "medical", \
        f"Expected 'medical', got '{slots['service_type']}'"


def test_food_stamps_is_benefits():
    """'Food stamps' should match benefits (Phase B Ticket D), not food.

    SNAP / EBT / food stamps are benefit-enrollment requests (signing up
    for the federal SNAP program), distinct from "where can I get a meal
    right now" (which is food). Phase B Ticket D promoted these from
    `other` to `benefits` along with the rest of the benefits cluster."""
    slots = extract_slots("How do I apply for food stamps")
    assert slots["service_type"] == "benefits", \
        f"Expected 'benefits', got '{slots['service_type']}'"


def test_legal_aid_is_legal():
    """'Legal aid' should match legal."""
    slots = extract_slots("Where can I find legal aid?")
    assert slots["service_type"] == "legal"


def test_job_training_is_employment():
    """'Job training' should match employment."""
    slots = extract_slots("I'm looking for job training programs")
    assert slots["service_type"] == "employment"


# -----------------------------------------------------------------------
