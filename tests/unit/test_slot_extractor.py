"""
Tests for the slot extractor.

Run with: python -m pytest tests/test_slot_extractor.py -v
Or just:  python tests/test_slot_extractor.py
"""

import pytest



from app.services.slot_extractor import (
    extract_slots,
    merge_slots,
    is_enough_to_answer,
    next_follow_up_question,
    NEAR_ME_SENTINEL,
)


# -----------------------------------------------------------------------
# SERVICE TYPE EXTRACTION
# -----------------------------------------------------------------------

def test_food_keywords():
    """All food-related phrases should extract service_type=food."""
    phrases = [
        "I need food",
        "Where can I get a meal?",
        "I'm hungry",
        "Is there a food bank nearby?",
        "Looking for a soup kitchen",
        "Free food in Brooklyn",
        "I need groceries",
        "Any food pantry open today?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "food", f"Failed on: {phrase} → {slots['service_type']}"


def test_shelter_keywords():
    """All shelter-related phrases should extract service_type=shelter."""
    phrases = [
        "I need shelter",
        "I need a place to stay",
        "Where can I sleep tonight?",
        "I'm homeless and need a bed",
        "Looking for housing",
        "Is there a drop-in center?",
        "I need somewhere to sleep",
        "Warming center in Manhattan",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "shelter", f"Failed on: {phrase} → {slots['service_type']}"


def test_clothing_keywords():
    """Clothing-related phrases should extract service_type=clothing."""
    phrases = [
        "I need clothes",
        "Where can I get a jacket?",
        "Free clothing near me",
        "I need a coat",
        "Can I get shoes somewhere?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "clothing", f"Failed on: {phrase} → {slots['service_type']}"


def test_personal_care_keywords():
    """Shower/hygiene phrases should extract service_type=personal_care."""
    phrases = [
        "I need a shower",
        "Where can I do laundry?",
        "I need toiletries",
        "Is there a place to clean up?",
        "I need a haircut",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "personal_care", f"Failed on: {phrase} → {slots['service_type']}"


def test_medical_keywords():
    """Medical phrases should extract service_type=medical."""
    phrases = [
        "I need to see a doctor",
        "Is there a clinic nearby?",
        "I need medical help",
        "Where's the nearest hospital?",
        "I need a dentist",
        "I need health care",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "medical", f"Failed on: {phrase} → {slots['service_type']}"


def test_mental_health_keywords():
    """Mental health phrases should extract service_type=mental_health."""
    phrases = [
        "I need mental health help",
        "I'm looking for counseling",
        "I need a therapist",
        "Where can I find a support group?",
        "I'm dealing with addiction",
        "I need help with substance abuse",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "mental_health", f"Failed on: {phrase} → {slots['service_type']}"


def test_legal_keywords():
    """Legal phrases should extract service_type=legal."""
    phrases = [
        "I need legal help",
        "I'm facing eviction",
        "I need an immigration lawyer",
        "Can I get legal aid?",
        "I need help with my green card",
        "I need a public defender",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "legal", f"Failed on: {phrase} → {slots['service_type']}"


def test_employment_keywords():
    """Employment phrases should extract service_type=employment."""
    phrases = [
        # "I need a job" retired from regex — "good job" false positive (REGEX_AUDIT).
        # Now handled by semantic routing (Tier 2).
        "Where can I find work?",
        "Job training programs",
        "Help with my resume",
        "I need job placement",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "employment", f"Failed on: {phrase} → {slots['service_type']}"


def test_other_keywords():
    """Benefits/ID/misc phrases should extract service_type=other."""
    phrases = [
        "How do I get SNAP benefits?",
        "I need help with food stamps",
        "I need an ID",
        "I need a birth certificate",
        "Is there free wifi anywhere?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "other", f"Failed on: {phrase} → {slots['service_type']}"


def test_no_service_type():
    """Messages without service keywords should return None."""
    phrases = [
        "Hello",
        "Thank you",
        "What time is it?",
        "Tell me more",
        "Yes",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] is None, f"False positive on: {phrase} → {slots['service_type']}"

def test_service_detail_specific_keywords():
    """Notable sub-type keywords should populate service_detail."""
    cases = [
        ("I need dental care", "medical", "dental care"),
        ("I need to see a dentist", "medical", "dental care"),
        ("I need an eye doctor", "medical", "vision care"),
        ("Where can I get an HIV testing", "medical", "HIV testing"),
        ("I need help with immigration", "legal", "immigration services"),
        ("I'm facing eviction", "legal", "eviction help"),
        ("I need a shower", "personal_care", "showers"),
        ("Where can I do laundry", "personal_care", "laundry"),
        ("I need a haircut", "personal_care", "haircuts"),
        ("I need counseling", "mental_health", "counseling"),
        ("I need rehab", "mental_health", "rehab services"),
        ("I'm looking for a soup kitchen", "food", "soup kitchens"),
        ("Where's the nearest food pantry", "food", "food pantries"),
    ]
    for phrase, expected_type, expected_detail in cases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == expected_type, \
            f"Wrong service_type for: {phrase} → {slots['service_type']}"
        assert slots["service_detail"] == expected_detail, \
            f"Wrong service_detail for: {phrase} → {slots['service_detail']}"


def test_service_detail_none_for_generic():
    """Generic category keywords should NOT populate service_detail."""
    cases = [
        ("I need food", "food"),
        ("I need shelter", "shelter"),
        ("I need clothing", "clothing"),
        ("I need medical help", "medical"),
        ("I need legal help", "legal"),
        ("I need employment help", "employment"),
    ]
    for phrase, expected_type in cases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == expected_type, \
            f"Wrong service_type for: {phrase}"
        assert slots["service_detail"] is None, \
            f"Unexpected service_detail for generic keyword: {phrase} → {slots['service_detail']}"


# =========================================================================
# tests/test_slot_extractor.py — add after test_age_out_of_range()
# =========================================================================

@pytest.mark.xfail(reason="Not yet implemented: word-to-number conversion for voice-transcribed ages")
def test_spoken_number_age_extraction():
    """Voice-transcribed word-form numbers should extract as ages."""
    cases = [
        ("I'm seventeen", 17),
        ("I am twenty two", 22),
        ("age forty five", 45),
        ("thirteen years old", 13),
        ("im eighteen", 18),
        ("I'm sixty five", 65),
        ("I am thirty", 30),
        ("im twelve", 12),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["age"] == expected, \
            f"Expected age {expected} for: {phrase} → {slots['age']}"


def test_spoken_number_digit_priority():
    """Digit patterns should still work and take priority over word forms."""
    cases = [
        ("I'm 17", 17),
        ("I am 22", 22),
        ("age 30", 30),
        ("45 years old", 45),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["age"] == expected, \
            f"Expected age {expected} for: {phrase} → {slots['age']}"


def test_spoken_number_no_false_positives():
    """Spoken numbers without age-context phrases should NOT extract as age."""
    phrases = [
        "I need food in Brooklyn",
        "There are five of us",
        "I've been here for twelve days",
        "Give me twenty options",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["age"] is None, \
            f"False positive age in: {phrase} → {slots['age']}"

# -----------------------------------------------------------------------
# LOCATION EXTRACTION
# -----------------------------------------------------------------------

def test_location_in_pattern():
    """'in <location>' pattern should extract the location."""
    cases = [
        ("food in Brooklyn", "brooklyn"),
        ("shelter in Queens", "queens"),
        ("I'm in Manhattan", "manhattan"),
        ("services in the Bronx", "bronx"),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location found in: {phrase}"
        assert expected.lower() in slots["location"].lower(), \
            f"Expected '{expected}' in location for: {phrase} → {slots['location']}"


def test_location_preposition_variants():
    """Prepositions like 'near', 'around', 'by', 'from' should extract location."""
    cases = [
        ("actually near Queens", "queens"),
        ("food around Harlem", "harlem"),
        ("shelter by Midtown", "midtown"),
        ("I'm from Brooklyn", "brooklyn"),
        ("services near the Bronx", "bronx"),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location found in: {phrase}"
        assert expected.lower() in slots["location"].lower(), \
            f"Expected '{expected}' in location for: {phrase} → {slots['location']}"


def test_location_known_names():
    """Known NYC borough/neighborhood names should be extracted."""
    cases = [
        ("food brooklyn", "brooklyn"),
        ("shelter queens tonight", "queens"),
        ("midtown clinic", "midtown"),
        ("harlem food pantry", "harlem"),
        ("long island city shelter", "long island city"),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["location"] is not None, f"No location found in: {phrase}"
        assert expected in slots["location"].lower(), \
            f"Expected '{expected}' in location for: {phrase} → {slots['location']}"


def test_location_false_positives():
    """Phrases like 'in need' or 'in trouble' should NOT extract a location."""
    phrases = [
        "I'm in need of help",
        "I'm in trouble",
        "I'm in a bad situation",
        "I'm in danger",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["location"] is None, \
            f"False positive location in: {phrase} → {slots['location']}"


def test_near_me_detection():
    """'Near me' phrases should return the sentinel, not a real location."""
    phrases = [
        "food near me",
        "shelters nearby",
        "closest food bank",
        "services close to me",
        "food around here",
        "what's in my area",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["location"] == NEAR_ME_SENTINEL, \
            f"Expected NEAR_ME_SENTINEL for: {phrase} → {slots['location']}"


def test_no_location():
    """Messages without location info should return None."""
    phrases = [
        "I need food",
        "Help me find a shelter",
        "Where can I get clothes?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["location"] is None, \
            f"False positive location in: {phrase} → {slots['location']}"


# -----------------------------------------------------------------------
# AGE EXTRACTION
# -----------------------------------------------------------------------

def test_age_extraction():
    """Various age patterns should be extracted correctly."""
    cases = [
        ("I am 17", 17),
        ("I'm 22", 22),
        ("age 30", 30),
        ("I'm 65 years old", 65),
        ("im 19", 19),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["age"] == expected, \
            f"Expected age {expected} for: {phrase} → {slots['age']}"


def test_no_age():
    """Messages without age info should return None."""
    phrases = [
        "I need food in Brooklyn",
        "Help me find shelter",
        "Looking for a job",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["age"] is None, \
            f"False positive age in: {phrase} → {slots['age']}"


def test_age_out_of_range():
    """Ages outside 1-119 should be rejected."""
    phrases = [
        "I am 0",
        "I am 150",
        "age 999",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["age"] is None, \
            f"Should reject out-of-range age in: {phrase} → {slots['age']}"


def test_age_does_not_match_for_duration_phrases():
    """Preventive hardening (April 2026): 'for NN <time-unit>' is always
    a duration, never an age. The preprocessing step in _extract_age
    strips these substrings before pattern matching so future regex
    additions can't accidentally match them."""
    duration_phrases = [
        "for 3 years",
        "I've been homeless for 3 years",
        "waiting for 6 months",
        "homeless for 12 days now",
        "waited for 2 weeks",
        "for 10 days",
    ]
    for phrase in duration_phrases:
        slots = extract_slots(phrase)
        assert slots["age"] is None, \
            f"Duration phrase should not yield an age: {phrase} → {slots['age']}"


def test_age_preserved_when_duration_and_age_coexist():
    """When a message contains BOTH a duration phrase AND an age
    statement, the duration is stripped first and the real age is
    still extracted."""
    cases = [
        ("I'm 25 and I've been homeless for 3 years", 25),
        ("I've lived here for 3 years and I'm 45", 45),
        ("for 6 months now, I'm 19", 19),
        ("I am 30, homeless for 2 years", 30),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["age"] == expected, \
            f"Expected age={expected} for: {phrase} → {slots['age']}"


def test_age_year_old_without_hyphen():
    """Users commonly type 'NN year old' without the hyphens that the
    original regex required. All natural-language variants of the
    'year old' phrase should extract the age correctly."""
    cases = [
        # Core hyphen variants — must all produce age=17
        ("17 year old", 17),
        ("17 years old", 17),
        ("17-year-old", 17),
        ("17-years-old", 17),
        ("17 year-old", 17),
        ("17-year old", 17),
        ("17year old", 17),  # no space before "year" either
        # Embedded in sentences (what users actually type)
        ("17 year old needs shelter", 17),
        ("i am a 17 year old", 17),
        ("my son is a 12 year old", 12),
        ("a 19 year old from the bronx", 19),
        ("I'm a 20 year old veteran", 20),
        # "yr" abbreviation variants
        ("17 yr old", 17),
        ("17-yr-old", 17),
        ("17yr old", 17),
        ("17 yrs old", 17),
        # Centenarians (3-digit ages)
        ("105 years old", 105),
        ("i am a 105 year old", 105),
    ]
    for phrase, expected in cases:
        slots = extract_slots(phrase)
        assert slots["age"] == expected, \
            f"Expected age={expected} for: {phrase} → {slots['age']}"


def test_age_year_old_edge_negatives():
    """The 'NN year old' pattern should NOT match these nearby phrasings
    that look similar but don't state an age."""
    cases = [
        "17 years ago",          # past — not age
        "17 years experience",   # duration — not age
        "17 yrs ago",
        "for 17 years",          # stripped by duration filter
        "300 year old tree",     # age out of range
        "999 years old",
        "a17 year old",          # no word boundary before 17
        "17year",                # incomplete — no "old"
    ]
    for phrase in cases:
        slots = extract_slots(phrase)
        assert slots["age"] is None, \
            f"Should not extract age from: {phrase!r} → {slots['age']}"


# -----------------------------------------------------------------------
# URGENCY EXTRACTION
# -----------------------------------------------------------------------

def test_urgency_extraction():
    """Urgency keywords should be classified correctly."""
    high_phrases = [
        ("I need shelter tonight", "high"),
        ("This is urgent", "high"),
        ("I need food right now", "high"),
        ("Help me asap", "high"),
    ]
    for phrase, expected in high_phrases:
        slots = extract_slots(phrase)
        assert slots["urgency"] == expected, \
            f"Expected urgency '{expected}' for: {phrase} → {slots['urgency']}"

    medium_phrases = [
        ("I need help soon", "medium"),
        ("Sometime this week", "medium"),
    ]
    for phrase, expected in medium_phrases:
        slots = extract_slots(phrase)
        assert slots["urgency"] == expected, \
            f"Expected urgency '{expected}' for: {phrase} → {slots['urgency']}"


def test_no_urgency():
    """Messages without urgency keywords should return None."""
    slots = extract_slots("I need food in Brooklyn")
    assert slots["urgency"] is None


# -----------------------------------------------------------------------
# MULTI-SLOT EXTRACTION
# -----------------------------------------------------------------------

def test_multi_slot_single_message():
    """A single message can fill multiple slots at once."""
    slots = extract_slots("I need shelter in Queens tonight, I'm 17")
    assert slots["service_type"] == "shelter"
    assert "queens" in (slots["location"] or "").lower()
    assert slots["urgency"] == "high"
    assert slots["age"] == 17


def test_full_sentence():
    """Realistic full sentences should extract correctly."""
    slots = extract_slots("I'm 22 and I need food in Brooklyn")
    assert slots["service_type"] == "food"
    assert "brooklyn" in (slots["location"] or "").lower()
    assert slots["age"] == 22


# -----------------------------------------------------------------------
# MERGE SLOTS
# -----------------------------------------------------------------------

def test_merge_new_over_empty():
    """New slots should merge into an empty session."""
    existing = {}
    new = {"service_type": "food", "location": None, "urgency": None, "age": None}
    merged = merge_slots(existing, new)
    assert merged["service_type"] == "food"
    assert "location" not in merged or merged.get("location") is None


def test_merge_preserves_existing():
    """Existing slots should be preserved when new values are None."""
    existing = {"service_type": "food", "location": "Brooklyn"}
    new = {"service_type": None, "location": None, "urgency": "high", "age": None}
    merged = merge_slots(existing, new)
    assert merged["service_type"] == "food"
    assert merged["location"] == "Brooklyn"
    assert merged["urgency"] == "high"


def test_merge_overrides_with_new():
    """New non-None values should override existing ones."""
    existing = {"service_type": "food", "location": "Brooklyn"}
    new = {"service_type": "shelter", "location": None, "urgency": None, "age": None}
    merged = merge_slots(existing, new)
    assert merged["service_type"] == "shelter"
    assert merged["location"] == "Brooklyn"


def test_merge_near_me_overrides_stale_location():
    """A 'near me' sentinel SHOULD replace an existing location.

    When the user explicitly says 'close by' or 'near me', they want
    proximity search — not the location from a previous search. The
    sentinel must override to prevent silently ignoring the user's
    explicit request.
    """
    existing = {"location": "Brooklyn"}
    new = {"location": NEAR_ME_SENTINEL}
    merged = merge_slots(existing, new)
    assert merged["location"] == NEAR_ME_SENTINEL


def test_merge_real_location_replaces_near_me():
    """A real location should replace a previous 'near me' sentinel."""
    existing = {"location": NEAR_ME_SENTINEL}
    new = {"location": "Queens"}
    merged = merge_slots(existing, new)
    assert merged["location"] == "Queens"


# -----------------------------------------------------------------------
# IS ENOUGH TO ANSWER
# -----------------------------------------------------------------------

def test_enough_with_service_and_location():
    """Should be enough when both service_type and location are present."""
    assert is_enough_to_answer({"service_type": "food", "location": "Brooklyn"}) is True


def test_not_enough_missing_service():
    """Should NOT be enough when service_type is missing."""
    assert is_enough_to_answer({"location": "Brooklyn"}) is False


def test_not_enough_missing_location():
    """Should NOT be enough when location is missing."""
    assert is_enough_to_answer({"service_type": "food"}) is False


def test_not_enough_near_me_sentinel():
    """The 'near me' sentinel should NOT count as a real location."""
    assert is_enough_to_answer({
        "service_type": "food",
        "location": NEAR_ME_SENTINEL,
    }) is False


def test_not_enough_empty():
    """Empty slots should not be enough."""
    assert is_enough_to_answer({}) is False


# -----------------------------------------------------------------------
# FOLLOW-UP QUESTIONS
# -----------------------------------------------------------------------

def test_followup_asks_service_type_first():
    """With no slots, should ask about service type."""
    question = next_follow_up_question({})
    assert "help" in question.lower() or "need" in question.lower()


def test_followup_asks_location_second():
    """With service type but no location, should ask about location."""
    question = next_follow_up_question({"service_type": "food"})
    assert "borough" in question.lower() or "neighborhood" in question.lower() or "area" in question.lower()


def test_followup_asks_location_for_near_me():
    """With 'near me' as location, should still ask for real location."""
    question = next_follow_up_question({
        "service_type": "food",
        "location": NEAR_ME_SENTINEL,
    })
    assert "borough" in question.lower() or "neighborhood" in question.lower()


def test_followup_asks_age_for_shelter():
    """For shelter with location but no age, should ask about age."""
    question = next_follow_up_question({
        "service_type": "shelter",
        "location": "Brooklyn",
    })
    assert "age" in question.lower() or "old" in question.lower()


# -----------------------------------------------------------------------
# WORD-BOUNDARY KEYWORDS (restored collision-prone keywords)
# -----------------------------------------------------------------------

def test_word_boundary_bed_matches_shelter():
    """'bed' with word boundaries should match shelter."""
    phrases = [
        "I need a bed",
        "Is there a bed available?",
        "Where can I get a bed tonight?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "shelter", f"Failed on: {phrase} → {slots['service_type']}"


def test_word_boundary_bed_no_collision_with_locations():
    """'bed' must NOT trigger shelter when part of a location name."""
    phrases = [
        ("food in bed-stuy", "food"),
        ("shelter near bedford-stuyvesant", "shelter"),
        ("food in bedford", "food"),
    ]
    for phrase, expected in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == expected, \
            f"Collision: '{phrase}' → {slots['service_type']} (expected {expected})"


def test_word_boundary_wash_matches_personal_care():
    """'wash' with word boundaries should match personal_care."""
    phrases = [
        "I need to wash up",
        "Where can I wash my face?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "personal_care", f"Failed on: {phrase} → {slots['service_type']}"


def test_word_boundary_wash_no_collision_with_washington():
    """'wash' must NOT trigger personal_care in 'washington heights'."""
    slots = extract_slots("food near washington heights")
    assert slots["service_type"] == "food", \
        f"Collision: 'washington heights' triggered {slots['service_type']}"


def test_word_boundary_id_matches_other():
    """'id' with word boundaries should match other."""
    phrases = [
        "I need an id",
        "How do I get an ID?",
        "I lost my ID",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "other", f"Failed on: {phrase} → {slots['service_type']}"


def test_word_boundary_id_no_collision_with_locations():
    """'id' must NOT trigger other when part of 'side', 'midtown', etc."""
    phrases = [
        ("shelter in midtown", "shelter"),
        ("food on the east side", "food"),
        ("food near bay ridge", "food"),
    ]
    for phrase, expected in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == expected, \
            f"Collision: '{phrase}' → {slots['service_type']} (expected {expected})"


def test_word_boundary_eat_matches_food():
    """'eat' with word boundaries should match food."""
    phrases = [
        "I need to eat",
        "Where can I eat?",
        "I just want to eat something",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "food", f"Failed on: {phrase} → {slots['service_type']}"


def test_word_boundary_eat_no_collision():
    """'eat' must NOT trigger food in 'beat', 'seat', 'theater'."""
    phrases = [
        "I beat the odds",
        "I had a good seat",
        "I went to the theater",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] is None, \
            f"Collision: '{phrase}' → {slots['service_type']}"


def test_word_boundary_hat_matches_clothing():
    """'hat' with word boundaries should match clothing."""
    slots = extract_slots("I need a hat")
    assert slots["service_type"] == "clothing"


def test_word_boundary_hat_no_collision():
    """'hat' must NOT trigger clothing in 'what', 'that', 'chat'."""
    phrases = [
        "What time is it?",
        "That is a good idea",
        "Let's chat about it",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] is None, \
            f"Collision: '{phrase}' → {slots['service_type']}"


# -----------------------------------------------------------------------
# NEW KEYWORDS (expanded coverage for target population)
# -----------------------------------------------------------------------

def test_new_food_keywords():
    """Newly added food keywords should match."""
    phrases = [
        "I need something to eat",
        "Can I grab a bite somewhere?",
        "Any canned food available?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "food", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_shelter_keywords():
    """Newly added shelter keywords should match."""
    phrases = [
        "I got evicted yesterday",
        "My parents kicked me out",
        "I've been sleeping outside",
        "I'm on the street and need help",
        "I need somewhere safe",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "shelter", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_clothing_keywords():
    """Newly added clothing keywords should match."""
    phrases = [
        "I need a sweater",
        "Do you have any hoodies?",
        "I need gloves for the winter",
        "Where can I get sneakers?",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "clothing", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_personal_care_keywords():
    """Newly added personal care keywords should match."""
    phrases = [
        "I need feminine products",
        "Where can I get pads?",
        "I need a hygiene kit",
        "I just want to freshen up",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "personal_care", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_medical_keywords():
    """Newly added medical keywords should match."""
    phrases = [
        # "I'm sick and need help" retired from regex — "sick of this" false positive
        # (REGEX_AUDIT). Now handled by semantic routing (Tier 2).
        "I have a wound that won't heal",
        "Can I see a nurse?",
        "I need medication",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "medical", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_mental_health_keywords():
    """Mental health keywords should match. Note: 'struggling', 'having a
    hard time', and 'someone to talk to' were intentionally removed — they
    are emotional expressions / escalation signals, not mental health service
    requests. See STRUCTURAL_FIXES_CHANGELOG.md Fix 1."""
    phrases = [
        "I'm dealing with grief",
        "I need counseling",
        "I need therapy",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "mental_health", f"Failed on: {phrase} → {slots['service_type']}"


def test_emotional_phrases_not_mental_health():
    """Emotional expressions should NOT extract as mental_health service.
    These are handled by the emotional tone handler in chatbot.py, not
    the service slot extractor."""
    phrases = [
        "I've been struggling lately",
        "I'm having a hard time",
        "I just need someone to talk to",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] != "mental_health", \
            f"'{phrase}' should not extract as mental_health — got {slots['service_type']}"


def test_new_legal_keywords():
    """Newly added legal keywords should match."""
    phrases = [
        "My landlord is threatening me",
        "I need help with custody",
        # "I need bail money" retired from regex — "bail out" collision (REGEX_AUDIT).
        # Now handled by semantic routing (Tier 2).
        "I'm facing discrimination",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "legal", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_other_keywords():
    """Newly added other-services keywords should match."""
    phrases = [
        "How do I get welfare?",
        "I need cash assistance",
        "Where do I get a state ID?",
        "I need a metro card",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["service_type"] == "other", f"Failed on: {phrase} → {slots['service_type']}"


def test_new_urgency_keywords():
    """Newly added urgency terms should extract high urgency."""
    phrases = [
        "I need shelter today",
        "This is an emergency",
        "I'm freezing out here",
        "I need help before dark",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["urgency"] == "high", f"Failed on: {phrase} → urgency={slots['urgency']}"


# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# "OTHER SERVICES" KEYWORD
# -----------------------------------------------------------------------

def test_other_services_keyword():
    """'I need other services' (quick reply value) should extract service_type=other."""
    slots = extract_slots("I need other services")
    assert slots["service_type"] == "other", f"Got: {slots['service_type']}"


def test_other_service_singular():
    """'other service' should also extract service_type=other."""
    slots = extract_slots("I need other service help")
    assert slots["service_type"] == "other"


# -----------------------------------------------------------------------
# SERVICE DETAIL EXTRACTION
# -----------------------------------------------------------------------

def test_service_detail_dental():
    """'dental' should extract service_type=medical with service_detail='dental care'."""
    slots = extract_slots("I need dental care")
    assert slots["service_type"] == "medical"
    assert slots["service_detail"] == "dental care"


def test_service_detail_therapy():
    """'therapy' should extract service_type=mental_health with service_detail='therapy'."""
    slots = extract_slots("I need therapy")
    assert slots["service_type"] == "mental_health"
    assert slots["service_detail"] == "therapy"


def test_service_detail_immigration():
    """'immigration' should extract service_type=legal with service_detail='immigration services'."""
    slots = extract_slots("I need immigration help")
    assert slots["service_type"] == "legal"
    assert slots["service_detail"] == "immigration services"


def test_service_detail_shower():
    """'shower' should extract service_type=personal_care with service_detail='showers'."""
    slots = extract_slots("I need a shower")
    assert slots["service_type"] == "personal_care"
    assert slots["service_detail"] == "showers"


def test_service_detail_food_pantry():
    """'food pantry' should extract service_type=food with service_detail='food pantries'."""
    slots = extract_slots("where is the nearest food pantry")
    assert slots["service_type"] == "food"
    assert slots["service_detail"] == "food pantries"


def test_service_detail_none_for_generic():
    """Generic keywords like 'food' should have no service_detail."""
    slots = extract_slots("I need food")
    assert slots["service_type"] == "food"
    assert slots["service_detail"] is None


def test_service_detail_aa_meeting():
    """'AA meeting' should extract mental_health with detail='AA meetings'."""
    slots = extract_slots("where can I find an AA meeting")
    assert slots["service_type"] == "mental_health"
    assert slots["service_detail"] == "AA meetings"


# -----------------------------------------------------------------------
# MERGE SLOTS — SERVICE DETAIL CLEARING
# -----------------------------------------------------------------------

def test_merge_slots_clears_stale_detail():
    """When service_type changes and new extraction has no detail, old detail is cleared."""
    from app.services.slot_extractor import merge_slots
    existing = {"service_type": "medical", "service_detail": "dental care", "location": "Brooklyn"}
    new = {"service_type": "food", "service_detail": None, "location": None}
    merged = merge_slots(existing, new)
    assert merged["service_type"] == "food"
    assert "service_detail" not in merged or merged.get("service_detail") is None


def test_merge_slots_keeps_detail_when_same_service():
    """When service_type doesn't change, service_detail should persist."""
    from app.services.slot_extractor import merge_slots
    existing = {"service_type": "medical", "service_detail": "dental care"}
    new = {"service_type": None, "location": "Queens"}
    merged = merge_slots(existing, new)
    assert merged["service_detail"] == "dental care"


def test_merge_slots_updates_detail_with_new_subtype():
    """When service_type changes and new extraction has a detail, use the new one."""
    from app.services.slot_extractor import merge_slots
    existing = {"service_type": "food", "service_detail": None, "location": "Brooklyn"}
    new = {"service_type": "medical", "service_detail": "dental care"}
    merged = merge_slots(existing, new)
    assert merged["service_type"] == "medical"
    assert merged["service_detail"] == "dental care"


# -----------------------------------------------------------------------
# FAMILY STATUS EXTRACTION
# -----------------------------------------------------------------------

def test_family_status_with_children():
    """Mentions of children should extract family_status=with_children."""
    phrases = [
        "I have two kids with me",
        "my daughter is 6",
        "I'm here with my son",
        # "pregnant" removed — pregnancy ≠ with_children (uses pregnant population tag)
        "with my children ages 4 and 7",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] == "with_children", f"Failed on: {phrase}"


def test_pregnant_is_population_not_family_status():
    """Pregnancy should be a population tag, not family_status=with_children."""
    slots = extract_slots("I'm pregnant and need shelter")
    assert slots.get("family_status") != "with_children", \
        "Pregnancy should not set family_status to with_children"
    assert "pregnant" in slots.get("_populations", []), \
        "Pregnancy should set pregnant population tag"


def test_family_status_with_children_prepositional():
    """Prepositional and possessive 'for me and my kids' / 'have a baby' forms.

    Added to `child_phrases` in `_extract_family_status` — was previously
    xfailed as a regex gap.
    """
    phrases = [
        "I need shelter for me and my kids",
        "I have a baby",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] == "with_children", f"Failed on: {phrase}"


def test_family_status_single_parent():
    """'Single mother/father/parent' should be with_children, not alone."""
    phrases = [
        "I'm a single mother",
        "single mom with two kids",
        "I'm a single dad",
        "single parent needing shelter",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] == "with_children", \
            f"'{phrase}' should be with_children, got: {slots['family_status']}"


def test_family_status_with_family():
    """Mentions of partner/spouse should extract family_status=with_family."""
    phrases = [
        "I'm with my partner",
        "me and my wife need shelter",
        "with my husband",
        "with my family",
        "with my girlfriend",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] == "with_family", f"Failed on: {phrase}"


def test_family_status_alone():
    """Explicit alone statements should extract family_status=alone."""
    phrases = [
        "I'm alone",
        "I'm by myself",
        "it's just me",
        "I'm on my own",
        "no one with me",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] == "alone", f"Failed on: {phrase}"


def test_family_status_none_when_not_mentioned():
    """Messages without family info should return None."""
    phrases = [
        "I need food in Brooklyn",
        "shelter in Queens",
        "where can I find clothing",
        "I need help",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] is None, f"False positive on: {phrase}"


def test_family_status_false_positive_i_have_a():
    """'I have a question' should NOT extract family_status."""
    phrases = [
        "I have a question about shelters",
        "I have a problem",
        "I have a feeling this won't work",
    ]
    for phrase in phrases:
        slots = extract_slots(phrase)
        assert slots["family_status"] is None, \
            f"False positive on: {phrase} → {slots['family_status']}"


def test_family_status_false_positive_me_and_my_friend():
    """'me and my friend' should NOT extract with_family."""
    slots = extract_slots("me and my friend need food in Brooklyn")
    assert slots["family_status"] is None, \
        f"'me and my friend' should not be with_family, got: {slots['family_status']}"


def test_family_status_false_positive_feeling_alone():
    """Emotional 'feeling alone' should NOT extract family_status."""
    slots = extract_slots("I'm feeling so alone right now")
    assert slots["family_status"] is None, \
        f"Emotional phrase should not extract family_status, got: {slots['family_status']}"


def test_family_status_children_with_service():
    """Family status should extract alongside service type."""
    slots = extract_slots("I have 2 kids and need shelter in the Bronx")
    assert slots["service_type"] == "shelter"
    assert slots["family_status"] == "with_children"
    assert "bronx" in slots["location"].lower()


# -----------------------------------------------------------------------
# FOLLOW-UP QUESTION — FAMILY STATUS
# -----------------------------------------------------------------------

def test_followup_asks_family_for_shelter():
    """Shelter search with age but no family_status should ask about family."""
    from app.services.slot_extractor import next_follow_up_question
    slots = {"service_type": "shelter", "location": "Brooklyn", "age": 30}
    question = next_follow_up_question(slots)
    assert "family" in question.lower() or "children" in question.lower()


def test_followup_skips_family_for_food():
    """Food search should NOT ask about family status."""
    from app.services.slot_extractor import next_follow_up_question
    slots = {"service_type": "food", "location": "Brooklyn"}
    question = next_follow_up_question(slots)
    assert "family" not in question.lower()


def test_followup_skips_family_when_already_set():
    """Shelter with family_status already set should not ask again."""
    from app.services.slot_extractor import next_follow_up_question
    slots = {"service_type": "shelter", "location": "Brooklyn", "age": 25,
             "family_status": "with_children"}
    question = next_follow_up_question(slots)
    assert "family" not in question.lower() and "children" not in question.lower()


# -----------------------------------------------------------------------
# MULTI-SERVICE EXTRACTION
# -----------------------------------------------------------------------

def test_extract_all_two_services():
    """'food and shelter' should extract both service types."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food and shelter in Brooklyn")
    types = [r[0] for r in results]
    assert "food" in types
    assert "shelter" in types
    assert len(types) == 2


def test_extract_all_three_services():
    """Three services should all be extracted."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food, shelter, and clothing")
    types = [r[0] for r in results]
    assert len(types) == 3
    assert "food" in types
    assert "shelter" in types
    assert "clothing" in types


def test_extract_all_no_duplicates():
    """Same category mentioned twice should only appear once."""
    from app.services.slot_extractor import _extract_all_service_types
    # "food" and "food pantry" are both category "food"
    results = _extract_all_service_types("I need food from a food pantry")
    types = [r[0] for r in results]
    assert types.count("food") == 1


def test_extract_all_mental_health_not_double_match():
    """'mental health' should match mental_health, not also 'health' as medical."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need mental health support")
    types = [r[0] for r in results]
    assert "mental_health" in types
    assert "medical" not in types, "'mental health' should not also match 'health'"


def test_extract_all_preserves_detail():
    """Sub-type details should be preserved for each service."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need dental care and therapy")
    details = {r[0]: r[1] for r in results}
    assert details.get("medical") == "dental care"
    assert details.get("mental_health") == "therapy"


def test_extract_all_single_service():
    """Single service should return a list of one."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food")
    assert len(results) == 1
    assert results[0][0] == "food"


def test_extract_all_no_service():
    """No service keywords should return empty list."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("hello how are you")
    assert results == []


def test_extract_slots_additional_services():
    """extract_slots should return primary + additional_services.

    Housing First priority: shelter (tier 1) wins primary over food
    (tier 2) even though food is mentioned first.
    """
    slots = extract_slots("I need food and shelter in Brooklyn")
    assert slots["service_type"] == "shelter"
    assert len(slots["additional_services"]) == 1
    assert slots["additional_services"][0][0] == "food"
    assert slots["location"] is not None


def test_extract_slots_no_additional():
    """Single service should have empty additional_services."""
    slots = extract_slots("I need food in Brooklyn")
    assert slots["service_type"] == "food"
    assert slots["additional_services"] == []


def test_extract_slots_additional_none_message():
    """No service should have empty additional_services and None primary."""
    slots = extract_slots("hello")
    assert slots["service_type"] is None
    assert slots["additional_services"] == []


def test_merge_slots_skips_additional_services():
    """merge_slots should not persist additional_services in session."""
    from app.services.slot_extractor import merge_slots
    existing = {"service_type": "food", "location": "Brooklyn"}
    new = {"service_type": "food", "additional_services": [("shelter", None)]}
    merged = merge_slots(existing, new)
    assert "additional_services" not in merged


def test_extract_all_food_and_legal():
    """'food and legal help' should extract both."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food and legal help in Manhattan")
    types = [r[0] for r in results]
    assert "food" in types
    assert "legal" in types


def test_extract_all_complex_multi_intent():
    """Complex multi-intent with details should work."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types(
        "I need a shower, some food, and help with my immigration case"
    )
    types = [r[0] for r in results]
    assert "personal_care" in types
    assert "food" in types
    assert "legal" in types


# -----------------------------------------------------------------------
# BUG FIX TESTS — _extract_all_service_types
# -----------------------------------------------------------------------

def test_extract_all_find_scans_forward():
    """find() should scan past overlapping spans to find later occurrences.
    Bug: 'food stamps and food' — first 'food' at pos 7 is inside 'food stamps',
    but 'food' at pos 23 is independent and should be found."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food stamps and food")
    types = [r[0] for r in results]
    assert "other" in types, "'food stamps' should match 'other' category"
    assert "food" in types, "'food' after 'food stamps' should also be found"


def test_extract_all_text_position_order():
    """Within a priority tier, results are ordered by text position.

    After _SERVICE_NEED_PRIORITY was introduced (Housing First, April 2026),
    the primary sort key is the priority tier; text position is the
    tiebreaker WITHIN a tier. Food (tier 2) and shelter (tier 1) are in
    different tiers, so order depends on priority — see
    test_extract_all_cross_tier_priority below. Use medical+shelter
    (both tier 1) here to test the position tiebreak.
    """
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need a doctor and a bed tonight")
    # Both tier 1. Medical mentioned first → medical primary.
    assert results[0][0] == "medical", f"Expected medical first, got {results[0][0]}"
    assert results[1][0] == "shelter", f"Expected shelter second, got {results[1][0]}"


def test_extract_all_cross_tier_priority():
    """Across tiers, priority wins regardless of text position.

    Housing First: shelter (tier 1) beats food (tier 2) even if food is
    mentioned first. See _SERVICE_NEED_PRIORITY in slot_extractor.py.
    """
    from app.services.slot_extractor import _extract_all_service_types
    # Food first in text, but shelter (tier 1) wins the priority.
    results = _extract_all_service_types("I need food and shelter")
    assert results[0][0] == "shelter", f"Expected shelter first (priority), got {results[0][0]}"
    assert results[1][0] == "food", f"Expected food second, got {results[1][0]}"


def test_extract_all_text_position_order_reversed():
    """Reversed mention order still respects priority (shelter wins)."""
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need shelter and food")
    assert results[0][0] == "shelter", f"Expected shelter first, got {results[0][0]}"
    assert results[1][0] == "food", f"Expected food second, got {results[1][0]}"


def test_extract_all_word_boundary_ordered():
    """Word-boundary fallback matches are picked up and sorted correctly.

    "bed" uses the word-boundary pattern (collision-prone keyword). This
    test verifies two things: (a) "bed" is detected as shelter, and
    (b) the result is sorted by _SERVICE_NEED_PRIORITY — shelter (tier 1)
    wins over food (tier 2) regardless of mention order.
    """
    from app.services.slot_extractor import _extract_all_service_types
    results = _extract_all_service_types("I need food and a bed")
    types = [r[0] for r in results]
    assert "shelter" in types, f"'bed' should match shelter, got {types}"
    assert "food" in types, f"'food' should match, got {types}"
    # Housing First: shelter (tier 1) primary over food (tier 2)
    assert types.index("shelter") < types.index("food"), \
        f"shelter (tier 1) should rank above food (tier 2), got {results}"


# -----------------------------------------------------------------------
# NYC ZIP CODE EXTRACTION
# -----------------------------------------------------------------------

def test_zip_to_neighborhood_specific():
    """Common NYC zip codes should map to specific neighborhoods."""
    cases = [
        ("10035", "east harlem"),
        ("10029", "east harlem"),
        ("10027", "harlem"),
        ("10001", "chelsea"),
        ("10451", "mott haven"),
        ("11201", "brooklyn"),
        ("11354", "flushing"),
        ("11372", "jackson heights"),
        ("10301", "staten island"),
    ]
    for zip_code, expected in cases:
        slots = extract_slots(zip_code)
        assert slots["location"] == expected, \
            f"Zip {zip_code}: expected '{expected}', got '{slots['location']}'"


def test_zip_to_borough_fallback():
    """NYC zips not in the specific lookup should map to a borough."""
    # 10128 is Upper East Side but not in the specific table
    # It's in the Manhattan range (10001-10282)
    slots = extract_slots("10128")
    assert slots["location"] == "manhattan", \
        f"Zip 10128: expected 'manhattan', got '{slots['location']}'"


def test_zip_non_nyc_returns_none():
    """Non-NYC zip codes should not extract a location."""
    for zip_code in ["90210", "60601", "02101", "99999"]:
        slots = extract_slots(zip_code)
        assert slots["location"] is None, \
            f"Non-NYC zip {zip_code} should not extract, got '{slots['location']}'"


def test_zip_in_sentence():
    """Zip code embedded in a sentence should still extract."""
    slots = extract_slots("I'm in the 10035 area")
    assert slots["location"] == "east harlem"


def test_zip_with_service():
    """Zip code + service should extract both."""
    slots = extract_slots("food in 10035")
    assert slots["service_type"] == "food"
    assert slots["location"] == "east harlem"


def test_zip_does_not_conflict_with_age():
    """A 5-digit number should be treated as a zip, not an age."""
    slots = extract_slots("10035")
    assert slots["location"] == "east harlem"
    assert slots["age"] is None  # Not 10035 years old


def test_zip_overridden_by_known_location():
    """If a known location name is present, it should take priority over zip."""
    # "in Brooklyn" should match the preposition+location pattern before zip
    slots = extract_slots("I live at 11201 but need food in Queens")
    assert slots["location"] == "queens"
