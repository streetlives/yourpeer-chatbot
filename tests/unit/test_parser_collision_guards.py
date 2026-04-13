"""
Regression tests for pre-existing test failure fixes (Groups A–H).

Each class covers one fix group and locks in both the fix behavior AND
the collision edge cases discovered during testing. If a future change
reintroduces any of these issues, these tests will catch it.

Groups:
  A — Location non_locations filter (slot_extractor.py)
  B — Confirm deny startswith (phrase_lists.py, classifier.py)
  C — Shelter "need a place" keywords (slot_extractor.py)
  D — Diapers categorization (test expectation only)
  E — Age extraction comma pattern (slot_extractor.py)
  F — Auto-execute xfail (test markers only)
  G — Day detection priority (post_results.py)
  H — Post-results has_service_intent guard (chatbot.py)

Run: pytest tests/unit/test_group_fixes_regression.py -v
"""

import uuid
import pytest

from app.services.slot_extractor import extract_slots
from app.services.classifier import _classify_action
from app.services.post_results import classify_post_results_question
from app.services.session_store import clear_session, get_session_slots

from conftest import send, send_multi


# ===================================================================
# GROUP A: LOCATION non_locations FILTER
# ===================================================================

class TestGroupA_LocationFilter:
    """The fallback location regex captures text after prepositions like
    'by' and 'in'. The non_locations filter blocks common non-location
    words that would otherwise be extracted as locations."""

    # --- A1: Sort/filter UI commands must NOT extract locations ---

    @pytest.mark.parametrize("msg", [
        "Sort by recently verified",
        "Sort by most services",
        "Sort by name",
        "Sort by distance",
        "Sort by rating",
        "filter by open now",
        "organized by category",
        "connected by phone",
    ])
    def test_sort_commands_no_location(self, msg):
        """UI sort/filter commands must not extract a false location."""
        s = extract_slots(msg)
        assert s["location"] is None, \
            f"False location from sort command: '{msg}' → loc={s['location']!r}"

    # --- A2: Temporal words after 'by' must NOT extract locations ---

    @pytest.mark.parametrize("msg", [
        "I need shelter by tomorrow",
        "can you find something by next week",
        "food by today",
    ])
    def test_temporal_after_by_no_location(self, msg):
        s = extract_slots(msg)
        assert s["location"] is None or s["location"] in (
            # May extract a known location from elsewhere in the message
            "shelter", "food",
        ) or any(loc in (s["location"] or "").lower() for loc in [
            "manhattan", "brooklyn", "queens", "bronx", "harlem",
        ]), f"False temporal location: '{msg}' → loc={s['location']!r}"

    # --- A3: Real NYC locations after 'by' must STILL work ---

    @pytest.mark.parametrize("msg,expected_loc", [
        ("shelter by Grand Central", "grand central"),
        ("food by Penn Station", "penn station"),
        ("drop me by Harlem", "harlem"),
        ("food in Bushwick", "bushwick"),
        ("shelter near Times Square", "times square"),
    ])
    def test_real_locations_after_prepositions_preserved(self, msg, expected_loc):
        s = extract_slots(msg)
        assert s["location"] is not None, \
            f"Real location lost: '{msg}' should extract '{expected_loc}'"
        assert expected_loc in s["location"].lower(), \
            f"Wrong location: '{msg}' → {s['location']!r}, expected '{expected_loc}'"

    # --- A4: Known collision words that we deliberately do NOT block ---

    def test_bus_not_blocked_because_bushwick(self):
        """'bus' cannot be in non_locations because it would block 'Bushwick'."""
        s = extract_slots("food in Bushwick")
        assert "bushwick" in s["location"].lower()

    def test_time_not_blocked_because_times_square(self):
        """'time' cannot be in non_locations because it would block 'Times Square'."""
        s = extract_slots("shelter near Times Square")
        assert "times square" in s["location"].lower()


# ===================================================================
# GROUP B: CONFIRM_DENY_STARTSWITH
# ===================================================================

class TestGroupB_ConfirmDenyStartswith:
    """'nah', 'nope' followed by other words should classify as
    confirm_deny. Previously only exact matches worked."""

    # --- B1: Deny words + trailing text = confirm_deny ---

    @pytest.mark.parametrize("msg", [
        "nah food",
        "nah shelter in Brooklyn",
        "nah let me think",
        "nope not that",
        "nope I changed my mind",
        "no way",
        "no way jose",
    ])
    def test_deny_with_trailing_words(self, msg):
        assert _classify_action(msg) == "confirm_deny", \
            f"'{msg}' should be confirm_deny"

    def test_nah_something_else_is_negative_preference(self):
        """'nah I need something else' — 'something else' matches
        negative_preference which fires before confirm_deny. This is
        correct: the user wants a different service, not just declining."""
        assert _classify_action("nah I need something else") == "negative_preference"

    # --- B2: Words starting with 'nah' that are NOT denials ---

    @pytest.mark.parametrize("msg", [
        "nahhhh",       # no trailing space
        "nahua language",
        "nahuatl",
    ])
    def test_nah_prefix_without_space_not_deny(self, msg):
        assert _classify_action(msg) != "confirm_deny", \
            f"'{msg}' should NOT be confirm_deny"

    # --- B3: Exact deny matches still work ---

    @pytest.mark.parametrize("msg", [
        "nah",
        "no",
        "nope",
        "stop",
    ])
    def test_exact_deny_still_works(self, msg):
        assert _classify_action(msg) == "confirm_deny"

    # --- B4: 'no way' edge cases ---

    def test_no_way_at_start_is_deny(self):
        assert _classify_action("no way I'm going back") == "confirm_deny"

    def test_no_way_mid_sentence_not_deny(self):
        """'no way' must be at the start to match startswith."""
        assert _classify_action("there's no way to get there") != "confirm_deny"

    # --- B5: Full conversation flows ---

    def test_nah_denies_confirmation(self):
        r = send_multi(["I need food in Brooklyn", "nah"])
        assert "hold onto" in r[1]["response"].lower() or \
               "what would you like" in r[1]["response"].lower()

    def test_nah_food_denies_same_service_confirmation(self):
        """'nah, food' when food is already pending = denial, not re-request."""
        r = send_multi(["I need food in Brooklyn", "nah, food"])
        # Should NOT re-confirm food — should deny
        assert r[1]["result_count"] == 0

    def test_nah_different_service_switches(self):
        """'nah, shelter instead' during food confirmation = service switch."""
        r = send_multi(["I need food in Brooklyn", "nah, shelter instead"])
        assert r[1]["slots"].get("service_type") == "shelter"

    def test_nah_after_results_declines(self):
        r = send_multi(["I need food in Brooklyn", "Yes, search", "nah I'm good"])
        assert "no problem" in r[2]["response"].lower() or \
               "let me know" in r[2]["response"].lower()


# ===================================================================
# GROUP C: SHELTER "need a place" KEYWORDS
# ===================================================================

class TestGroupC_ShelterPlaceKeywords:
    """'need a place tonight' and related phrases should extract as
    shelter, but 'need a place to eat/shower' should NOT."""

    # --- C1: Phrases that ARE shelter ---

    @pytest.mark.parametrize("msg", [
        "I need a place to stay",
        "I need a place tonight",
        "need a place to go",
        "a place tonight in Queens",
        "I need a place to stay in Brooklyn",
    ])
    def test_shelter_phrases(self, msg):
        s = extract_slots(msg)
        assert s["service_type"] == "shelter", \
            f"'{msg}' should extract shelter, got {s['service_type']!r}"

    # --- C2: 'place' + purpose clause = other service, NOT shelter ---

    @pytest.mark.parametrize("msg,expected", [
        ("I need a place to eat", "food"),
        ("I need a place to shower", "personal_care"),
        ("I need a place to get clothes", "clothing"),
        ("I need a place to see a doctor", "medical"),
        ("I need a place to get legal help", "legal"),
    ])
    def test_place_with_purpose_not_shelter(self, msg, expected):
        s = extract_slots(msg)
        assert s["service_type"] == expected, \
            f"'{msg}' should extract {expected}, got {s['service_type']!r}"

    # --- C3: Ambiguous 'place' without purpose or temporal = None ---

    @pytest.mark.parametrize("msg", [
        "I need a place",
        "this place sucks",
        "there's no place for me",
        "in the first place I went",
    ])
    def test_ambiguous_place_not_shelter(self, msg):
        s = extract_slots(msg)
        assert s["service_type"] != "shelter" or s["service_type"] is None, \
            f"'{msg}' should NOT extract shelter"

    # --- C4: 'workplace' substring must not match ---

    def test_workplace_not_shelter(self):
        s = extract_slots("my workplace is nearby")
        assert s["service_type"] is None

    # --- C5: The original pregnant couple scenario ---

    def test_pregnant_couple_tonight(self):
        """The scenario that motivated this fix."""
        s = extract_slots(
            "I'm pregnant and my partner and I need a place tonight in Brooklyn"
        )
        assert s["service_type"] == "shelter"
        assert "brooklyn" in (s["location"] or "").lower()


# ===================================================================
# GROUP E: AGE EXTRACTION — COMMA PATTERN
# ===================================================================

class TestGroupE_AgeCommaPattern:
    """Ages preceded by a comma (e.g., 'foster care, 21, in the Bronx')
    should be extracted. Non-age numbers after commas should not."""

    # --- E1: Valid ages after commas ---

    @pytest.mark.parametrize("msg,expected_age", [
        ("foster care, 21, in the Bronx", 21),
        ("I need help, 19, homeless", 19),
        ("unemployed, 25, in Queens", 25),
        ("pregnant, 17, fleeing", 17),
        ("single mom, 30, need shelter", 30),
    ])
    def test_age_after_comma(self, msg, expected_age):
        s = extract_slots(msg)
        assert s["age"] == expected_age, \
            f"'{msg}' should extract age={expected_age}, got {s['age']}"

    # --- E2: Non-age numbers after commas must NOT extract ---

    @pytest.mark.parametrize("msg", [
        "I need food, clothing, and shelter",
        "room 5, building 3, main street",
        "open 9, close 5",
        "I have 3, maybe 4 kids",
        "floor 2, room 7, wing B",
        "call 211, they might help",
        "I called 311, no luck",
        "bus route 7, stop 3, Queens",
    ])
    def test_non_age_numbers_after_comma(self, msg):
        s = extract_slots(msg)
        assert s["age"] is None, \
            f"False age from '{msg}' → age={s['age']}"

    # --- E3: Address numbers must not extract as age ---

    def test_street_number_not_age(self):
        s = extract_slots("I was at 42, broadway")
        assert s["age"] is None

    # --- E4: Existing age patterns still work ---

    @pytest.mark.parametrize("msg,expected", [
        ("I'm 21", 21),
        ("I am 17", 17),
        ("age 22", 22),
        ("22 years old", 22),
        ("19-year-old", 19),
        ("21, LGBTQ, in Soho", 21),
    ])
    def test_existing_patterns_preserved(self, msg, expected):
        s = extract_slots(msg)
        assert s["age"] == expected

    # --- E5: Boundary values ---

    def test_age_zero_rejected(self):
        s = extract_slots(", 0, test")
        assert s["age"] is None

    def test_age_120_plus_rejected(self):
        s = extract_slots(", 150, test")
        assert s["age"] is None

    def test_age_99_accepted(self):
        s = extract_slots(", 99, test")
        assert s["age"] == 99


# ===================================================================
# GROUP G: DAY DETECTION PRIORITY
# ===================================================================

class TestGroupG_DayDetectionPriority:
    """Day-of-week detection must fire BEFORE the generic filter_open
    handler. 'are you open Saturday' is a day-specific question, not
    a 'what's open right now' filter."""

    # --- G1: Day + open/hours = ask_hours_day ---

    @pytest.mark.parametrize("msg,expected_day", [
        ("are you open Saturday", 6),
        ("are they open on Monday", 1),
        ("open Monday?", 1),
        ("open on Tuesday?", 2),
        ("is it open Wednesday", 3),
        ("open Thursday afternoon?", 4),
        ("will they be open Friday", 5),
        ("open Sunday morning?", 7),
        ("what are the hours for Saturday", 6),
        ("what time do they open Monday", 1),
        ("schedule for Wednesday?", 3),
    ])
    def test_day_with_open_is_hours_day(self, msg, expected_day):
        intent = classify_post_results_question(msg)
        assert intent is not None, f"'{msg}' should not return None"
        assert intent["type"] == "ask_hours_day", \
            f"'{msg}' should be ask_hours_day, got {intent['type']!r}"
        assert intent["weekday"] == expected_day, \
            f"'{msg}' should have weekday={expected_day}, got {intent['weekday']}"

    # --- G2: Weekend detection ---

    @pytest.mark.parametrize("msg", [
        "are you open on the weekend",
        "weekend hours?",
        "open on weekends?",
    ])
    def test_weekend_detection(self, msg):
        intent = classify_post_results_question(msg)
        assert intent is not None
        assert intent["type"] == "ask_hours_day"
        assert intent["weekday"] == 6
        assert intent.get("weekend") is True

    # --- G3: Generic 'open' WITHOUT day = filter_open ---

    @pytest.mark.parametrize("msg", [
        "are you open now",
        "which ones are open",
        "any open right now",
        "is it still open",
        "are they open today",
        "currently open?",
    ])
    def test_open_without_day_is_filter_open(self, msg):
        intent = classify_post_results_question(msg)
        assert intent is not None
        assert intent["type"] == "filter_open", \
            f"'{msg}' should be filter_open, got {intent['type']!r}"

    # --- G4: Bare 'open' without day context must NOT trigger day detection ---

    @pytest.mark.parametrize("msg", [
        "I'm open to suggestions",
        "keep an open mind",
        "the door was open",
    ])
    def test_bare_open_no_day_no_trigger(self, msg):
        intent = classify_post_results_question(msg)
        if intent is not None:
            assert intent["type"] != "ask_hours_day", \
                f"'{msg}' should not trigger day detection"

    # --- G5: Day name in non-schedule context ---

    def test_lost_phone_friday_not_day_detection(self):
        """'I lost my phone on Friday' — 'phone' triggers ask_field,
        not day detection (no open/hours signal)."""
        intent = classify_post_results_question("I lost my phone on Friday")
        assert intent is not None
        assert intent["type"] == "ask_field"
        assert intent["field"] == "phone"


# ===================================================================
# GROUP H: POST-RESULTS has_service_intent GUARD
# ===================================================================

class TestGroupH_PostResultsServiceGuard:
    """After results are displayed, messages with 'search for' that ALSO
    contain a new service intent should start a new search, not be
    swallowed by the 'already shown results' handler."""

    # --- H1: New service after results starts new flow ---

    @pytest.mark.parametrize("msg,expected_svc", [
        ("Search for employment programs in Manhattan", "employment"),
        ("Search for shelter in Brooklyn", "shelter"),
        ("Please search for food in Harlem", "food"),
    ])
    def test_search_for_new_service_after_results(self, msg, expected_svc):
        """'Search for X' with a new service type should escape post-results."""
        r = send_multi(["I need food in Brooklyn", "Yes, search", msg])
        assert r[2]["slots"].get("service_type") == expected_svc, \
            f"'{msg}' after results should extract {expected_svc}"

    # --- H2: Bare confirm_yes after results = 'already shown' ---

    @pytest.mark.parametrize("msg", [
        "Yes, search",
        "Do the search",
        "Go ahead",
    ])
    def test_bare_confirm_after_results_already_shown(self, msg):
        r = send_multi(["I need food in Brooklyn", "Yes, search", msg])
        assert "already shown" in r[2]["response"].lower() or \
               "search for something else" in r[2]["response"].lower(), \
            f"Bare '{msg}' after results should say already shown"

    # --- H3: Deny + new service after results = new search ---

    def test_deny_with_new_service_after_results(self):
        r = send_multi([
            "I need food in Brooklyn",
            "Yes, search",
            "No, I need shelter in Manhattan",
        ])
        assert r[2]["slots"].get("service_type") == "shelter"

    def test_nah_with_new_service_after_results(self):
        r = send_multi([
            "I need food in Brooklyn",
            "Yes, search",
            "nah shelter in Manhattan",
        ])
        assert r[2]["slots"].get("service_type") == "shelter"

    # --- H4: Confirm + same service = not a new search ---

    def test_yes_search_for_same_service_after_results(self):
        """'Yes, search for food in Queens' after food results — the service
        intent (food) prevents the 'already shown' handler, but it should
        start a new food search in Queens."""
        r = send_multi([
            "I need food in Brooklyn",
            "Yes, search",
            "Yes, search for food in Queens",
        ])
        # Should either show a new confirmation for Queens or results
        assert "queens" in r[2]["response"].lower() or \
               r[2]["result_count"] >= 1


# ===================================================================
# CROSS-GROUP INTERACTIONS
# ===================================================================

class TestCrossGroupInteractions:
    """Tests for interactions between multiple fix groups."""

    # --- Medical urgency + shelter place keyword ---

    def test_insulin_and_place_tonight_multi_intent(self):
        """'I ran out of insulin and need a place tonight' should extract
        medical as primary with shelter queued."""
        s = extract_slots("I ran out of insulin and need a place tonight")
        assert s["service_type"] == "medical"
        assert s["urgency"] == "high"
        addl = s.get("additional_services", [])
        addl_types = [a[0] for a in addl]
        assert "shelter" in addl_types, \
            f"Shelter should be queued as additional, got {addl_types}"

    # --- Age comma + location + service compound ---

    def test_age_comma_with_location_and_service(self):
        s = extract_slots("foster care, 17, need shelter, Bronx")
        assert s["service_type"] == "shelter"
        assert s["age"] == 17
        assert "bronx" in (s["location"] or "").lower()

    def test_age_comma_with_shelter_place_keyword(self):
        s = extract_slots("pregnant, 19, need a place tonight, Manhattan")
        assert s["service_type"] == "shelter"
        assert s["age"] == 19
        assert "manhattan" in (s["location"] or "").lower()

    # --- Deny startswith + day detection in post-results ---

    def test_nah_not_saturday_is_deny_not_day(self):
        """'nah not Saturday' — classify_action should return confirm_deny.
        The day detection in post_results is separate and only fires
        when no service routing is active."""
        assert _classify_action("nah not Saturday") == "confirm_deny"

    # --- Location filter + shelter keywords ---

    def test_place_tonight_does_not_extract_false_location(self):
        """'need a place tonight' — 'tonight' should NOT be extracted
        as a location via the fallback regex."""
        s = extract_slots("need a place tonight")
        assert s["service_type"] == "shelter"
        assert s["location"] is None  # no real location provided

    # --- Confirm deny + post-results guard ---

    def test_nah_after_results_no_service_intent(self):
        """'nah' after results with no new service = post-results decline,
        not the 'already shown' handler."""
        r = send_multi(["I need food in Brooklyn", "Yes, search", "nah"])
        # Should decline, not say "already shown"
        assert "already shown" not in r[2]["response"].lower()
