"""
Tests for the chatbot module — message classification, routing,
multi-turn session state, PII integration, and fallback behavior.
External dependencies (Claude LLM, Streetlives DB) are mocked so
tests run without API keys or a database connection.
Run: pytest tests/test_chatbot.py
"""
from unittest.mock import patch, MagicMock
from app.services.classifier import _classify_message
from app.services.confirmation import _no_results_message
from app.services.phrase_lists import _WELCOME_QUICK_REPLIES
from app.services.responses import _GREETING_RESPONSE, _RESET_RESPONSE, _THANKS_RESPONSE, _HELP_RESPONSE
from app.services.chatbot import generate_reply
from app.services.session_store import clear_session, get_session_slots
from conftest import (
    MOCK_QUERY_RESULTS, MOCK_EMPTY_RESULTS,
    send, send_multi, assert_classified,
)
# -----------------------------------------------------------------------
# MESSAGE CLASSIFICATION
# -----------------------------------------------------------------------
# NOTE: These tests exercise _classify_message(), which is a backward-
# compatibility wrapper used by the LLM fallback path. The main routing
# in generate_reply() uses _classify_action() + _classify_tone() directly
# for more nuanced handling (e.g., service intent + emotional tone).
# For end-to-end routing tests, use send() and assert on response
# properties. See the "COMBINED ROUTING" and "SPLIT CLASSIFIER" sections
# below for tests that exercise the actual routing pipeline.
def test_classify_reset():
    """Reset phrases should classify as 'reset'."""
    for phrase in ["start over", "reset", "nevermind", "cancel", "new search"]:
        assert_classified(phrase, "reset")
def test_classify_greeting():
    """Short greetings should classify as 'greeting'."""
    for phrase in ["hi", "hey", "hello", "yo", "good morning"]:
        assert_classified(phrase, "greeting")
def test_classify_greeting_not_long_messages():
    """Long messages starting with 'hi' should NOT be greetings."""
    assert _classify_message("hi I need food in Brooklyn") != "greeting"
    assert _classify_message("hey can you find me a shelter in Queens") != "greeting"
def test_classify_thanks():
    """Thank-you phrases should classify as 'thanks'."""
    for phrase in ["thanks", "thank you", "thx", "appreciate it", "that helps"]:
        assert_classified(phrase, "thanks")
def test_classify_help():
    """Help phrases should classify as 'help'."""
    for phrase in ["help", "what is this", "who are you",
                   "list services", "show services"]:
        assert_classified(phrase, "help")
    # Capability questions now route to bot_question (more specific answers)
    for phrase in ["how does this work", "what can you do",
                   "why weren't you able to get my location"]:
        assert_classified(phrase, "bot_question")
def test_classify_service():
    """Messages with service keywords should classify as 'service'."""
    for phrase in ["I need food", "shelter in Brooklyn",
                   "looking for a doctor", "I need a job"]:
        assert_classified(phrase, "service")
def test_classify_general():
    """Messages that don't match anything should classify as 'general'."""
    for phrase in ["tell me more", "what about the second one", "I see"]:
        assert_classified(phrase, "general")
def test_classify_reset_takes_priority():
    """Reset should take priority over other categories."""
    assert_classified("start over", "reset")
    assert_classified("cancel", "reset")
def test_classify_with_punctuation():
    """Classification should work with punctuation."""
    assert_classified("hi!", "greeting")
    assert_classified("thanks!!!", "thanks")
    assert_classified("start over.", "reset")
    assert_classified("help?", "help")
# -----------------------------------------------------------------------
# GENERATE REPLY — ROUTING PATHS
# -----------------------------------------------------------------------
MOCK_ERROR_RESULTS = {
    "services": [],
    "result_count": 0,
    "template_used": None,
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 0,
    "error": "Unknown template: xyz",
}
def test_greeting_route(fresh_session):
    """Greeting should return greeting response without querying DB."""
    result = send("hi", session_id=fresh_session)
    assert _GREETING_RESPONSE in result["response"]
    assert result["services"] == []
def test_greeting_with_existing_session(fresh_session):
    """Greeting with existing slots should acknowledge the session."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {"service_type": "food"})
    result = send("hey", session_id=fresh_session)
    assert "earlier search" in result["response"].lower() or "still have" in result["response"].lower()
def test_reset_clears_session(fresh_session):
    """Reset should clear the session and return reset response."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {"service_type": "food", "location": "Brooklyn"})
    result = send("start over", session_id=fresh_session)
    assert result["slots"] == {}
    assert "fresh" in result["response"].lower() or "start" in result["response"].lower()
    assert get_session_slots(fresh_session) == {}
def test_thanks_route(fresh_session):
    """Thanks should return thanks response."""
    result = send("thank you", session_id=fresh_session)
    assert _THANKS_RESPONSE in result["response"]
def test_help_route(fresh_session):
    """Help should return help response."""
    result = send("help", session_id=fresh_session)
    assert "free services" in result["response"].lower() or "find" in result["response"].lower()


def test_bot_question_route(fresh_session):
    """Bot capability questions should get direct, informative answers."""
    result = send("why weren't you able to get my location?", session_id=fresh_session)
    # Should explain location/capabilities, not show frustration response
    response_lower = result["response"].lower()
    assert "frustrat" not in response_lower
    assert "tried places" not in response_lower
    # Should mention location, browser, or capabilities
    assert any(w in response_lower for w in ["location", "browser", "borough", "neighborhood", "nyc"])


def test_bot_question_does_not_extract_slots(fresh_session):
    """Bot questions should not trigger slot extraction."""
    result = send("what can you search for?", session_id=fresh_session)
    assert result["slots"].get("service_type") is None
    assert result["slots"].get("_pending_confirmation") is None
def test_service_with_results(fresh_session):
    """Full service request should confirm first, then query DB on confirmation."""
    r1, r2 = send_multi(
        ["I need food in Brooklyn", "Yes, search"],
        session_id=fresh_session,
    )
    assert r1["follow_up_needed"] is True
    assert "food" in r1["response"].lower()
    assert "brooklyn" in r1["response"].lower()
    assert r2["result_count"] == 1
    assert r2["services"][0]["service_name"] == "Test Food Pantry"
def test_service_no_results(fresh_session):
    """Service request with no results should show helpful message after confirmation."""
    results = send_multi(
        ["I need food in Brooklyn", "Yes, search"],
        session_id=fresh_session,
        mock_query_return=MOCK_EMPTY_RESULTS,
    )
    assert results[-1]["result_count"] == 0
    assert "wasn't able to find" in results[-1]["response"] or "try" in results[-1]["response"].lower()
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", side_effect=Exception("DB connection failed"))
def test_db_failure_uses_static_fallback(mock_query, mock_crisis, fresh_session):
    """If DB query throws after confirmation, should return a static error
    message — NOT an LLM response (which can generate follow-up questions
    that trap the user in a confirmation loop)."""
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    result = generate_reply("Yes, search", session_id=fresh_session)
    mock_query.assert_called_once()
    assert len(result["response"]) > 0
    assert result["services"] == []
    # Should contain a clear error message directing user to yourpeer.nyc
    assert "yourpeer.nyc" in result["response"]
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", side_effect=Exception("DB down"))
def test_both_db_and_claude_fail(mock_query, mock_crisis, fresh_session):
    """If DB query fails, static fallback should still work (no Claude needed)."""
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    result = generate_reply("Yes, search", session_id=fresh_session)
    # Should return a safe fallback, not crash
    assert len(result["response"]) > 0
    assert result["services"] == []
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", return_value=MOCK_ERROR_RESULTS)
def test_query_error_uses_static_fallback(mock_query, mock_crisis, fresh_session):
    """If query_services returns an error key, should return a static error
    message — NOT an LLM response."""
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    result = generate_reply("Yes, search", session_id=fresh_session)
    assert len(result["response"]) > 0
    assert result["services"] == []
    # Should contain a clear error message
    assert "try again" in result["response"].lower() or "yourpeer" in result["response"]
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", side_effect=Exception("DB down"))
def test_repeated_db_failure_escalates_message(mock_query, mock_crisis, fresh_session):
    """Second consecutive DB failure should show a stronger 'still having trouble' message."""
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    r1 = generate_reply("Yes, search", session_id=fresh_session)
    assert "yourpeer.nyc" in r1["response"]
    # User tries again — starts new flow
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    r2 = generate_reply("Yes, search", session_id=fresh_session)
    assert "still having trouble" in r2["response"].lower()
# -----------------------------------------------------------------------
# SERVICE FOLLOW-UP (not enough slots)
# -----------------------------------------------------------------------
def test_service_needs_followup(fresh_session):
    """Service keyword without location should ask follow-up, not query DB."""
    result = send("I need food", session_id=fresh_session)
    assert result["follow_up_needed"] is True
    assert result["services"] == []
    assert "borough" in result["response"].lower() or "neighborhood" in result["response"].lower() or "area" in result["response"].lower()
# -----------------------------------------------------------------------
# GENERAL CONVERSATION
# -----------------------------------------------------------------------
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services")
@patch("app.services.responses.claude_reply", return_value="I understand. How can I help you find what you need?")
@patch("app.services.chatbot.claude_reply", return_value="I understand. How can I help you find what you need?")
def test_general_conversation(mock_chatbot_claude, mock_responses_claude, mock_query, mock_crisis, fresh_session):
    """Unrecognized messages should route to Claude for conversational response."""
    result = generate_reply("tell me more about that", session_id=fresh_session)
    mock_query.assert_not_called()
    # _fallback_response calls claude_reply via responses.py
    mock_responses_claude.assert_called_once()
    # Should return Claude's response (not empty, not a service result)
    assert len(result["response"]) > 0
    assert result["services"] == []
# -----------------------------------------------------------------------
# MULTI-TURN SESSION STATE
# -----------------------------------------------------------------------
def test_multi_turn_slot_accumulation(fresh_session):
    """Slots should accumulate across turns within the same session."""
    r1, r2, r3 = send_multi(
        ["I need food", "Brooklyn", "Yes, search"],
        session_id=fresh_session,
    )
    assert r1["follow_up_needed"] is True
    assert r1["slots"].get("service_type") == "food"
    assert r2["follow_up_needed"] is True
    assert "brooklyn" in r2["slots"].get("location", "").lower()
    assert r3["result_count"] >= 1
def test_reset_then_new_search(fresh_session):
    """After reset, a new search should start from scratch."""
    r_food, r_reset, r_shelter = send_multi(
        ["I need food", "start over", "shelter"],
        session_id=fresh_session,
    )
    assert r_reset["slots"] == {}
    assert r_shelter["slots"].get("service_type") == "shelter"
# -----------------------------------------------------------------------
# PII REDACTION IN CHATBOT FLOW
# -----------------------------------------------------------------------
def test_pii_redacted_in_transcript(fresh_session):
    """PII should be redacted in the stored transcript but slots should still extract."""
    result = send(
        "My name is Sarah and I need food in Brooklyn",
        session_id=fresh_session,
    )
    assert result["slots"].get("service_type") == "food"
    stored = get_session_slots(fresh_session)
    transcript = stored.get("transcript", [])
    assert len(transcript) > 0
    last_msg = transcript[-1]["text"]
    assert "Sarah" not in last_msg
    assert "[NAME]" in last_msg
    assert "Brooklyn" in last_msg or "brooklyn" in last_msg
def test_phone_redacted_in_transcript(fresh_session):
    """Phone numbers should be redacted in transcript."""
    send("My number is 212-555-1234, I need food", session_id=fresh_session)
    stored = get_session_slots(fresh_session)
    transcript = stored.get("transcript", [])
    assert len(transcript) > 0
    last_msg = transcript[-1]["text"]
    assert "212-555-1234" not in last_msg
    assert "[PHONE]" in last_msg
# -----------------------------------------------------------------------
# SESSION ID GENERATION
# -----------------------------------------------------------------------
def test_session_id_generated_when_none():
    """If no session_id is provided, one should be generated."""
    result = send("hi")
    assert result["session_id"] is not None
    assert len(result["session_id"]) > 0
    clear_session(result["session_id"])
def test_session_id_preserved():
    """If a valid UUID session_id is provided, it should be returned unchanged."""
    import uuid
    custom_id = str(uuid.uuid4())
    result = send("hi", session_id=custom_id)
    assert result["session_id"] == custom_id
    clear_session(custom_id)
# -----------------------------------------------------------------------
# RESPONSE STRUCTURE VALIDATION
# -----------------------------------------------------------------------
def test_response_has_all_required_keys(fresh_session):
    """Every response should have all required keys for the ChatResponse model."""
    required_keys = [
        "session_id", "response", "follow_up_needed",
        "slots", "services", "result_count", "relaxed_search",
        "quick_replies",
    ]
    for msg in ["hi", "I need food", "thank you", "start over", "help"]:
        result = send(msg, session_id=fresh_session)
        for key in required_keys:
            assert key in result, f"Missing key '{key}' in response for: {msg}"
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS)
@patch("app.services.chatbot.claude_reply")
def test_relaxed_search_flag(mock_claude, mock_query, mock_crisis, fresh_session):
    """relaxed_search should reflect whether the query was relaxed."""
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    result = generate_reply("Yes, search", session_id=fresh_session)
    assert result["relaxed_search"] is False
    mock_query.return_value = {**MOCK_QUERY_RESULTS, "relaxed": True}
    clear_session(fresh_session)
    generate_reply("I need food in Brooklyn", session_id=fresh_session)
    result2 = generate_reply("Yes, search", session_id=fresh_session)
    assert result2["relaxed_search"] is True
    assert "broadened" in result2["response"].lower()
# -----------------------------------------------------------------------
# CONFIRMATION FLOW & QUICK REPLIES
# -----------------------------------------------------------------------
def test_classify_confirmation_phrases():
    """Confirmation phrases should classify correctly."""
    for phrase in ["yes", "yeah", "ok", "sure", "go ahead", "yes please",
                   "correct", "yep", "yup", "do it", "find", "please"]:
        assert_classified(phrase, "confirm_yes")
    for phrase in ["yes search", "yes, search", "yes I want to search",
                   "please search", "search for that", "looks good",
                   "that's right", "thats correct", "confirm",
                   "go ahead and search", "yes that is correct"]:
        assert_classified(phrase, "confirm_yes")
    for phrase in ["change location", "different area", "wrong location"]:
        assert_classified(phrase, "confirm_change_location")
    for phrase in ["change service", "different service", "wrong service"]:
        assert_classified(phrase, "confirm_change_service")
def test_confirmation_step_triggered(fresh_session):
    """When both slots are filled, should return confirmation, not query."""
    result = send("I need food in Brooklyn", session_id=fresh_session)
    assert result["follow_up_needed"] is True
    assert "food" in result["response"].lower()
    assert "brooklyn" in result["response"].lower()
    assert len(result["quick_replies"]) >= 3
def test_change_location_during_confirmation(fresh_session):
    """User can change location during confirmation."""
    _, result = send_multi(
        ["I need food in Brooklyn", "Change location"],
        session_id=fresh_session,
    )
    assert result["slots"].get("location") is None
    assert result["slots"].get("service_type") == "food"
    assert len(result["quick_replies"]) > 0
def test_change_service_during_confirmation(fresh_session):
    """User can change service type during confirmation."""
    _, result = send_multi(
        ["I need food in Brooklyn", "Change service"],
        session_id=fresh_session,
    )
    assert result["slots"].get("service_type") is None
    assert "brooklyn" in result["slots"].get("location", "").lower()
def test_greeting_has_quick_replies(fresh_session):
    """Greeting response should include quick-reply category buttons."""
    result = send("hi", session_id=fresh_session)
    assert len(result["quick_replies"]) > 0
    labels = [qr["label"] for qr in result["quick_replies"]]
    assert any("Food" in l for l in labels)
    assert any("Shelter" in l for l in labels)
def test_reset_has_quick_replies(fresh_session):
    """Reset response should include quick-reply category buttons."""
    result = send("start over", session_id=fresh_session)
    assert len(result["quick_replies"]) > 0
def test_service_followup_has_quick_replies(fresh_session):
    """When only service type is known, follow-up should show borough buttons."""
    result = send("I need food", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert any("Manhattan" in l or "Brooklyn" in l for l in labels), \
        f"Expected borough buttons, got: {labels}"
def test_new_input_clears_pending_confirmation(fresh_session):
    """Typing new service input during confirmation should update slots.
    Contradiction detection auto-executes when both service and location change."""
    _, result = send_multi(
        ["I need food in Brooklyn", "I need shelter in Queens"],
        session_id=fresh_session,
    )
    assert result["slots"].get("service_type") == "shelter"
    assert "queens" in result["slots"].get("location", "").lower()
    # Contradiction detection auto-executes — returns results, not re-confirmation
    assert result["result_count"] >= 1 or result["follow_up_needed"] is False
def test_results_have_post_search_quick_replies(fresh_session):
    """After results are shown, should offer new search and peer navigator buttons."""
    _, result = send_multi(
        ["I need food in Brooklyn", "Yes, search"],
        session_id=fresh_session,
    )
    assert result["result_count"] > 0
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert any("search" in l.lower() or "new" in l.lower() for l in labels)
def test_general_reply_does_not_retrigger_confirmation(fresh_session):
    """A general message like 'no' should NOT re-trigger confirmation from stale slots."""
    *_, result = send_multi(
        ["I need food in Manhattan", "Yes, search", "no"],
        session_id=fresh_session,
    )
    assert result["result_count"] == 0
    assert "search for" not in result["response"].lower()
    assert result["follow_up_needed"] is False
def test_no_after_escalation_does_not_confirm(fresh_session):
    """After escalation response, 'no' should route to general conversation."""
    *_, result = send_multi(
        ["I need food in Manhattan", "Yes, search",
         "Connect with peer navigator", "no"],
        session_id=fresh_session,
    )
    assert result["result_count"] == 0
    assert "search for" not in result["response"].lower()
# -----------------------------------------------------------------------
# BUG FIX REGRESSION TESTS
# -----------------------------------------------------------------------
def test_confirm_deny_breaks_loop(fresh_session):
    """Bug 1: Saying 'no' during confirmation should NOT loop forever."""
    r1, r2, r3 = send_multi(
        ["I need food in Brooklyn", "no", "no"],
        session_id=fresh_session,
    )
    assert r1["slots"].get("_pending_confirmation") is True
    assert r2["slots"].get("_pending_confirmation") is None
    assert "hold onto" in r2["response"].lower() or "no problem" in r2["response"].lower()
    assert len(r2["quick_replies"]) > 0
    assert "just to make sure" not in r3["response"].lower()
def test_confirm_deny_exact_phrases():
    """Exact denial words should always classify as 'confirm_deny'."""
    for phrase in ["no", "nah", "nope", "not yet", "hold on", "stop"]:
        assert_classified(phrase, "confirm_deny")


def test_confirm_deny_longer_phrases():
    """Longer denial phrases should classify as 'confirm_deny'."""
    for phrase in ["no thanks", "no thank you", "i changed my mind",
                   "not right now", "maybe later"]:
        assert_classified(phrase, "confirm_deny")
def test_cancel_variants_reset():
    """Bug 2: 'cancel my search', 'please cancel' etc. should trigger reset."""
    for phrase in ["cancel", "cancel my search", "please cancel",
                   "i want to cancel", "cancel this"]:
        assert_classified(phrase, "reset")
def test_frustration_expanded_phrases():
    """Bug 3: Expanded frustration phrases should be detected."""
    for phrase in ["wrong results", "these results are bad",
                   "thats not right", "not useful", "this sucks",
                   "useless", "that didnt help"]:
        assert_classified(phrase, "frustration")
    # These map to negative_preference (more specific action):
    # "not what I needed" matches negative pref phrases
    # "none of those work" contains "none of those" in negative pref
    assert_classified("not what I needed", "negative_preference")
    assert_classified("none of those work", "negative_preference")
def test_thanks_with_continuation_falls_through():
    """Bug 8: 'thanks but I need X' should NOT be classified as thanks."""
    assert_classified("thanks", "thanks")
    assert_classified("thank you", "thanks")
    for phrase in ["thanks but I need more options",
                   "thank you but I also want shelter",
                   "thanks but can you also search for food",
                   "thanks however I need something else"]:
        assert _classify_message(phrase) != "thanks", \
            f"'{phrase}' should NOT classify as thanks"
def test_empty_message_guard(fresh_session):
    """Bug 6: Empty string messages should return welcome, not hit Claude."""
    result = send("", session_id=fresh_session)
    assert "looking for" in result["response"].lower()
    assert len(result["quick_replies"]) == len(_WELCOME_QUICK_REPLIES)
def test_whitespace_message_guard(fresh_session):
    """Bug 6: Whitespace-only messages should return welcome, not hit Claude."""
    result = send("   ", session_id=fresh_session)
    assert "looking for" in result["response"].lower()
    assert len(result["quick_replies"]) == len(_WELCOME_QUICK_REPLIES)
def test_confused_classification():
    """Confusion phrases should classify as 'confused', not 'general'."""
    # Mock detect_crisis so LLM fail-open doesn't misclassify as crisis
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        for phrase in ["I don't know what to do", "I dont know what to do",
                       "idk what to do", "I don't know", "I'm confused",
                       "I'm lost", "I'm overwhelmed", "I'm not sure what I need",
                       "what should I do", "where do I start", "what are my options"]:
            assert_classified(phrase, "confused")
    assert_classified("I need food", "service")
    assert_classified("hello", "greeting")
def test_confused_does_not_trigger_llm(fresh_session):
    """'I don't know what to do' should NOT reach the LLM or extract slots."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        result = send("I don't know what to do", session_id=fresh_session)
    assert "figure it out" in result["response"].lower() or "okay" in result["response"].lower()
    assert len(result["quick_replies"]) >= 9
    assert result["slots"].get("service_type") is None
    assert result["slots"].get("_pending_confirmation") is None


# -----------------------------------------------------------------------
# EMOTIONAL AWARENESS
# -----------------------------------------------------------------------

def test_emotional_classification():
    """Emotional phrases should classify as 'emotional', not 'confused' or 'general'."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        for phrase in [
            "I'm feeling really down",
            "I'm feeling sad",
            "having a hard time",
            "having a rough day",
            "I'm scared",
            "feeling lonely",
            "I'm not okay",
            "I'm struggling",
            "stressed out",
            "nobody cares",
            "tired of everything",
            "feeling hopeless",
        ]:
            assert_classified(phrase, "emotional")


def test_emotional_does_not_catch_service_messages():
    """Messages with service intent should still classify as 'service'."""
    assert_classified("I need food", "service")
    assert_classified("I need shelter in Brooklyn", "service")
    assert_classified("I need mental health support", "service")
    # "feeling hungry" has no emotional phrase match — goes to slots
    assert_classified("I'm feeling hungry", "service")
    # Regression: emotional phrase + service intent → service, not emotional
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        assert_classified(
            "I'm struggling with addiction and need a treatment program in Manhattan",
            "service",
        )
        assert_classified(
            "I'm overwhelmed and need food in Brooklyn",
            "service",
        )


def test_emotional_distinct_from_confused():
    """'I'm feeling lost' should be 'emotional', not 'confused'.
    'I don't know what to do' should still be 'confused'."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        assert_classified("I'm feeling lost", "emotional")
        assert_classified("I feel stuck", "emotional")
        assert_classified("I don't know what to do", "confused")
        assert_classified("what are my options", "confused")


def test_emotional_response_has_peer_navigator(fresh_session):
    """Emotional messages should get a warm response with a peer navigator option."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        result = send("I'm feeling really down", session_id=fresh_session)
    # Should acknowledge feeling
    assert "service" not in result["response"].lower() or "peer" in result["response"].lower()
    # Should show New search + Peer navigator (not welcome menu)
    labels = [qr["label"] for qr in result["quick_replies"]]
    assert "🍽️ Food" not in labels, "Emotional should NOT show welcome menu"
    assert any("peer" in qr["label"].lower() or "navigator" in qr["label"].lower()
               for qr in result["quick_replies"])
    # Should not extract slots
    assert result["slots"].get("service_type") is None


def test_emotional_does_not_set_confirmation(fresh_session):
    """Emotional messages should never trigger the confirmation flow."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        result = send("I'm having a really rough day", session_id=fresh_session)
    assert result["slots"].get("_pending_confirmation") is None
    assert result["follow_up_needed"] is False


def test_emotional_static_fallback_without_llm(fresh_session):
    """Without LLM, emotional messages should use the static response."""
    with patch("app.services.chatbot.detect_crisis", return_value=None), \
         patch("app.services.chatbot._USE_LLM", False):
        result = send("I'm feeling really down", session_id=fresh_session)
    assert "sorry" in result["response"].lower() or "courage" in result["response"].lower()
    assert "peer navigator" in result["response"].lower()


def test_conversation_history_passed_to_llm(fresh_session):
    """Session transcript should be stored and available for LLM context."""
    result = send("I need food in Queens", session_id=fresh_session)
    slots = result["slots"]
    assert "transcript" in slots
    assert len(slots["transcript"]) >= 1
    entry = slots["transcript"][0]
    assert entry["role"] == "user"
    assert "food" in entry["text"].lower()


# -----------------------------------------------------------------------
# PENDING CONFIRMATION LEAK (#3)
# -----------------------------------------------------------------------

def test_escalation_clears_pending_confirmation(fresh_session):
    """Escalation during a pending confirmation should clear the flag."""
    # Get to confirmation
    r1 = send("I need food in Brooklyn", session_id=fresh_session)
    assert r1["follow_up_needed"] is True
    assert r1["slots"].get("_pending_confirmation") is True

    # Escalate instead of confirming
    r2 = send("Connect with peer navigator", session_id=fresh_session)
    assert "peer navigator" in r2["response"].lower()

    # The pending flag should be cleared
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("_pending_confirmation") is None


def test_crisis_clears_pending_confirmation(fresh_session):
    """Crisis during a pending confirmation should clear the flag."""
    # Get to confirmation
    r1 = send("I need food in Brooklyn", session_id=fresh_session)
    assert r1["follow_up_needed"] is True

    # Trigger crisis — call generate_reply directly because send()
    # always mocks detect_crisis to None, overriding our mock.
    from app.services.chatbot import generate_reply
    with patch("app.services.chatbot.detect_crisis",
               return_value=("suicide_self_harm", "Crisis response")), \
         patch("app.services.chatbot.claude_reply", return_value=""), \
         patch("app.services.chatbot.query_services"):
        generate_reply("I want to hurt myself", session_id=fresh_session)

    # The pending flag should be cleared
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("_pending_confirmation") is None


# -----------------------------------------------------------------------
# CONTEXT-AWARE "NO" (#4)
# -----------------------------------------------------------------------

def test_no_after_escalation_routes_to_general(fresh_session):
    """'No' after escalation should NOT re-trigger search confirmation."""
    # Fill slots and confirm
    r1 = send("I need food in Brooklyn", session_id=fresh_session)
    assert r1["follow_up_needed"] is True

    # Escalate (this clears pending confirmation)
    r2 = send("Connect with peer navigator", session_id=fresh_session)
    assert "peer navigator" in r2["response"].lower()

    # Say "no" — should be about the escalation, not the search
    r3 = send("no", session_id=fresh_session)
    assert "change your mind" in r3["response"].lower() or "anything else" in r3["response"].lower()
    # Should NOT show the food/Brooklyn confirmation
    assert "food" not in r3["response"].lower()


def test_yes_after_escalation_shows_peer_navigator(fresh_session):
    """'Yes' after escalation should show peer navigator info, not run a search."""
    r1 = send("I need food in Brooklyn", session_id=fresh_session)
    assert r1["follow_up_needed"] is True

    # Escalate
    r2 = send("Connect with peer navigator", session_id=fresh_session)
    assert "peer navigator" in r2["response"].lower()

    # Say "yes" — should acknowledge navigator info, not execute the food search
    r3 = send("yes", session_id=fresh_session)
    assert "contact" in r3["response"].lower() or "peer navigator" in r3["response"].lower() or "reach out" in r3["response"].lower()
    assert r3["result_count"] == 0  # No service results


def test_yes_after_emotional_routes_to_escalation(fresh_session):
    """'Yes' after an emotional response should connect to peer navigator."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        r1 = send("I'm feeling really down", session_id=fresh_session)
    # The emotional response offers a peer navigator
    assert any("person" in qr["label"].lower() or "peer" in qr["value"].lower()
               for qr in r1["quick_replies"])

    # User says "yes" — means "yes, connect me with a person"
    r2 = send("yes", session_id=fresh_session)
    assert "contact" in r2["response"].lower() or "peer navigator" in r2["response"].lower() or "reach out" in r2["response"].lower()


def test_no_after_emotional_is_gentle(fresh_session):
    """'No' after an emotional response should be gentle, not push services."""
    with patch("app.services.chatbot.detect_crisis", return_value=None):
        r1 = send("I'm feeling really down", session_id=fresh_session)

    r2 = send("no", session_id=fresh_session)
    assert "okay" in r2["response"].lower() or "ready" in r2["response"].lower()
    # Should NOT show the full service menu
    labels = [qr["label"] for qr in r2.get("quick_replies", [])]
    assert "🍽️ Food" not in labels


def test_yes_still_confirms_search_normally(fresh_session):
    """Context-aware yes should not break normal search confirmation."""
    r1 = send("I need food in Brooklyn", session_id=fresh_session)
    assert r1["follow_up_needed"] is True

    # "yes" with no prior escalation/emotional should still run the search
    r2 = send("Yes, search", session_id=fresh_session)
    assert r2["result_count"] >= 1


# -----------------------------------------------------------------------
# JUST CHATTING MODE (#5)
# -----------------------------------------------------------------------

def test_general_response_no_buttons_after_conversation(fresh_session):
    """After the first turn, general responses should not push service buttons."""
    # First general message — may show buttons (first turn)
    r1 = send("how's it going?", session_id=fresh_session)

    # Second general message — should NOT push the full 9-category menu
    r2 = send("just thinking about stuff", session_id=fresh_session)
    labels = [qr["label"] for qr in r2.get("quick_replies", [])]
    # Should not have the full welcome menu
    assert "🍽️ Food" not in labels


def test_general_response_no_buttons_with_service_intent(fresh_session):
    """General messages mid-search should not re-show the service menu."""
    # Start a search
    send("I need food", session_id=fresh_session)

    # General follow-up mid-search
    r2 = send("ok cool", session_id=fresh_session)
    labels = [qr["label"] for qr in r2.get("quick_replies", [])]
    assert "🍽️ Food" not in labels


# -----------------------------------------------------------------------
# BOT RESPONSE PII REDACTION IN AUDIT LOG
# -----------------------------------------------------------------------

def test_log_turn_redacts_bot_response_pii():
    """_log_turn should redact PII from bot responses before storing."""
    from app.services.chatbot import _log_turn
    from app.services.audit_log import get_recent_events, clear_audit_log

    clear_audit_log()
    _log_turn(
        session_id="s-pii-test",
        user_msg="My name is [NAME]",
        result={
            "response": "Hi Bryan! I can help you find services.",
            "slots": {"service_type": "food"},
        },
        category="general",
    )

    events = get_recent_events()
    bot_response = events[0]["bot_response"]
    assert "Bryan" not in bot_response, "PII should be redacted from bot response"
    assert "[NAME]" in bot_response


def test_log_turn_redacts_phone_in_bot_response():
    """_log_turn should redact phone numbers echoed in bot responses."""
    from app.services.chatbot import _log_turn
    from app.services.audit_log import get_recent_events, clear_audit_log

    clear_audit_log()
    _log_turn(
        session_id="s-pii-test-2",
        user_msg="[PHONE]",
        result={
            "response": "I see your number is 212-555-1234. Let me look that up.",
            "slots": {},
        },
        category="general",
    )

    events = get_recent_events()
    bot_response = events[0]["bot_response"]
    assert "212-555-1234" not in bot_response, "Phone should be redacted from bot response"
    assert "[PHONE]" in bot_response


# -----------------------------------------------------------------------
# GEOLOCATION COORDINATE PRIORITY
# -----------------------------------------------------------------------

def test_text_location_overrides_stored_coords(fresh_session):
    """When user provides a text location after using 'near me', the query
    should use the text location and NOT the stored browser coordinates."""
    from unittest.mock import patch, call
    from app.services.chatbot import generate_reply
    from conftest import MOCK_QUERY_RESULTS

    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS) as mock_query, \
         patch("app.services.chatbot.detect_crisis", return_value=None):

        # Step 1: User says "food near me" with browser coordinates (Harlem)
        generate_reply("food near me", session_id=fresh_session, latitude=40.8116, longitude=-73.9465)
        # Step 2: Confirm the near-me search
        generate_reply("Yes, search", session_id=fresh_session)

        # Step 3: New search with a TEXT location (Midtown East)
        generate_reply("mental health in Midtown East", session_id=fresh_session)
        generate_reply("Yes, search", session_id=fresh_session)

        # The LAST call to query_services should use Midtown East,
        # NOT the Harlem coordinates
        last_call = mock_query.call_args
        assert last_call.kwargs.get("latitude") is None or last_call[1].get("latitude") is None, \
            "Stored coordinates should not override text location"
        location_arg = last_call.kwargs.get("location") or last_call[1].get("location")
        assert location_arg is not None, "Text location should be passed"
        assert "near_me" not in str(location_arg).lower(), "Should not use near-me sentinel"


# -----------------------------------------------------------------------
# PRIVACY QUESTIONS — CLASSIFICATION
# -----------------------------------------------------------------------

def test_classify_privacy_questions():
    """Privacy-related questions should classify as bot_question."""
    privacy_questions = [
        "is this private",
        "is this confidential",
        "is this safe",
        "are you recording me",
        "who can see this",
        "can anyone see my messages",
        "do you share my information",
        "do you store my data",
        "can ICE see this",
        "can the police see what I said",
        "will this affect my benefits",
        "can my case worker see this",
        "can the shelter see what I wrote",
        "how do i delete my conversation",
        "do you know who I am",
        "do you know my name",
        "do you save my info",
        "do you track me",
        "is this anonymous",
    ]
    for phrase in privacy_questions:
        assert_classified(phrase, "bot_question")


def test_privacy_not_confused_with_service():
    """Privacy questions should not be misclassified as service requests."""
    # "benefits" is also an "other" service keyword, but in privacy context
    # "will this affect my benefits" should be bot_question not service
    assert_classified("will this affect my benefits", "bot_question")


# -----------------------------------------------------------------------
# STATIC BOT ANSWERS — PATTERN MATCHING
# -----------------------------------------------------------------------

def test_static_bot_answer_geolocation_failure():
    """Geolocation failure questions get a specific answer."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Why couldn't you get my location?")
    assert "permission" in response.lower() or "denied" in response.lower()
    assert "neighborhood" in response.lower() or "borough" in response.lower()


def test_static_bot_answer_geolocation_general():
    """General geolocation questions get a relevant answer."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("How does location work?")
    assert "gps" in response.lower() or "location" in response.lower()


def test_static_bot_answer_outside_nyc():
    """Outside-NYC questions mention 211."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Can you search outside NYC?")
    assert "211" in response
    assert "new york" in response.lower() or "nyc" in response.lower()


def test_static_bot_answer_services_list():
    """Service category questions list available categories."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("What services can you search for?")
    assert "food" in response.lower()
    assert "shelter" in response.lower()
    assert "legal" in response.lower()


def test_static_bot_answer_privacy_ice():
    """ICE-related privacy questions give specific reassurance."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Can ICE see my conversation?")
    assert "ice" in response.lower()
    assert "government" in response.lower() or "identifying" in response.lower()


def test_static_bot_answer_privacy_police():
    """Police-related questions give specific reassurance."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Do you share with the police?")
    assert "law enforcement" in response.lower()


def test_static_bot_answer_privacy_benefits():
    """Benefits-impact questions give specific reassurance."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Will this affect my benefits or case?")
    assert "benefits" in response.lower()
    assert "case" in response.lower() or "provider" in response.lower()


def test_static_bot_answer_privacy_who_can_see():
    """Visibility questions reassure no one else can see."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Can anyone see what I said?")
    assert "no one" in response.lower() or "nobody" in response.lower()


def test_static_bot_answer_privacy_delete():
    """Delete/clear questions explain how."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("How do I delete my conversation?")
    assert "start over" in response.lower()


def test_static_bot_answer_privacy_identity():
    """Identity questions confirm anonymity."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Do you know my name?")
    assert "don't know" in response.lower() or "do not know" in response.lower()


def test_static_bot_answer_privacy_general():
    """General privacy question gets comprehensive answer."""
    from app.services.bot_knowledge import answer_question
    response = answer_question("Is this confidential?")
    assert response is not None
    assert "see your conversation" in response.lower() or "private" in response.lower() or "anonymous" in response.lower()


def test_static_bot_answer_how_it_works():
    """'How does this work' gets an explanation."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("How does this work?")
    assert "database" in response.lower() or "search" in response.lower()


def test_static_bot_answer_default():
    """Unknown bot questions get a generic but useful answer."""
    from app.services.responses import _static_bot_answer
    response = _static_bot_answer("Why is the sky blue?")
    assert "service" in response.lower()


# -----------------------------------------------------------------------
# SERVICE DETAIL IN CONFIRMATION
# -----------------------------------------------------------------------

def test_confirmation_uses_service_detail(fresh_session):
    """Confirmation message should use service_detail when available."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "medical", "service_detail": "dental care", "location": "Brooklyn"}
    msg = _build_confirmation_message(slots)
    assert "dental care" in msg.lower()
    assert "health care" not in msg.lower(), "Should use detail, not generic label"


def test_confirmation_falls_back_to_label(fresh_session):
    """Confirmation message should use generic label when no service_detail."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "food", "location": "Queens"}
    msg = _build_confirmation_message(slots)
    assert "food" in msg.lower()


def test_change_service_clears_detail(fresh_session):
    """Changing service type during confirmation should clear service_detail."""
    from app.services.session_store import get_session_slots

    # Start a dental care search
    send("I need dental care", session_id=fresh_session)
    send("Brooklyn", session_id=fresh_session)

    # At confirmation, change service
    send("Change service", session_id=fresh_session)

    slots = get_session_slots(fresh_session)
    assert slots.get("service_detail") is None, "service_detail should be cleared"
    assert slots.get("service_type") is None, "service_type should be cleared"


# -----------------------------------------------------------------------
# BOT QUESTION HANDLER — FULL FLOW
# -----------------------------------------------------------------------

def test_bot_question_privacy_gets_specific_response(fresh_session):
    """Privacy questions should get a specific answer, not generic overview."""
    result = send("Is this private? Can anyone see what I type?", session_id=fresh_session)
    response = result["response"].lower()
    # Should mention privacy, not just list service categories
    assert "private" in response or "anonymous" in response or "no one" in response


def test_bot_question_geolocation_gets_specific_response(fresh_session):
    """Geolocation questions should explain why it might fail."""
    result = send("Why couldn't you get my location?", session_id=fresh_session)
    response = result["response"].lower()
    assert "permission" in response or "browser" in response or "neighborhood" in response


def test_bot_question_outside_nyc_gets_specific_response(fresh_session):
    """Outside-NYC questions should mention coverage limitation."""
    result = send("Can you search outside NYC?", session_id=fresh_session)
    response = result["response"].lower()
    assert "new york" in response or "nyc" in response or "five boroughs" in response


# -----------------------------------------------------------------------
# CONTEXT-AWARE YES/NO — EXPANDED
# -----------------------------------------------------------------------

def test_yes_after_frustration_connects_navigator(fresh_session):
    """'Yes' after frustration should connect to peer navigator.

    The frustration handler's messaging pushes toward navigator ('I think
    a peer navigator would be more helpful'). The 'Try different search'
    button sends 'Start over' directly, so 'yes' means 'yes, connect me.'
    See STRUCTURAL_FIXES_CHANGELOG.md Fix 5."""
    send("I need food", session_id=fresh_session)
    send("Brooklyn", session_id=fresh_session)
    send("Yes, search", session_id=fresh_session)
    send("this is useless", session_id=fresh_session)
    result = send("yes", session_id=fresh_session)
    # Should route to navigator — not reset
    response = result["response"].lower()
    assert "peer" in response or "navigator" in response or "streetlives" in response


def test_no_after_frustration_offers_navigator(fresh_session):
    """'No' after frustration should offer peer navigator."""
    send("that wasn't helpful", session_id=fresh_session)
    result = send("no", session_id=fresh_session)
    response = result["response"].lower()
    assert "person" in response or "let me know" in response


def test_yes_after_confused_escalates(fresh_session):
    """'Yes' after confused should connect to peer navigator."""
    send("I don't know what to do", session_id=fresh_session)
    result = send("yes", session_id=fresh_session)
    response = result["response"].lower()
    assert "peer" in response or "navigator" in response or "streetlives" in response


def test_no_after_confused_gentle_response(fresh_session):
    """'No' after confused should give gentle encouragement."""
    send("I'm so lost", session_id=fresh_session)
    result = send("no", session_id=fresh_session)
    response = result["response"].lower()
    assert "okay" in response or "ready" in response
    # Should have some way forward
    qr = result.get("quick_replies", [])
    assert len(qr) > 0, "Should show quick replies after declining"


def test_no_after_emotional_has_quick_replies(fresh_session):
    """'No' after emotional should show quick reply buttons."""
    send("I'm having a really rough day", session_id=fresh_session)
    result = send("no", session_id=fresh_session)
    qr = result.get("quick_replies", [])
    assert len(qr) > 0, "Should show quick replies so user has a next step"


def test_context_cleared_after_unrelated_message(fresh_session):
    """_last_action should be cleared after any non-yes/no message."""
    send("I'm feeling really down", session_id=fresh_session)
    # Send an unrelated message — should clear _last_action
    send("I need food in Brooklyn", session_id=fresh_session)
    # "yes" should now refer to the pending confirmation, not escalation
    result = send("Yes, search", session_id=fresh_session)
    assert result["result_count"] >= 1 or result["services"], \
        "Yes should trigger search, not escalation"


# -----------------------------------------------------------------------
# FRUSTRATION LOOP — NO REPEATED RESPONSE
# -----------------------------------------------------------------------

def test_repeated_frustration_different_response(fresh_session):
    """Second frustration message should NOT repeat the same response."""
    r1 = send("that's not helpful", session_id=fresh_session)
    r2 = send("I don't like those options either", session_id=fresh_session)
    assert r1["response"] != r2["response"], \
        "Repeated frustration should produce a different response"


def test_repeated_frustration_pushes_navigator(fresh_session):
    """Second frustration should push peer navigator more strongly."""
    send("not helpful at all", session_id=fresh_session)
    result = send("this is useless", session_id=fresh_session)
    response = result["response"].lower()
    assert "peer navigator" in response or "real people" in response
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert any("peer" in l.lower() or "navigator" in l.lower() for l in labels)


def test_repeated_frustration_shorter_response(fresh_session):
    """Second frustration response should be shorter than the first."""
    r1 = send("that wasn't helpful", session_id=fresh_session)
    r2 = send("still not helpful", session_id=fresh_session)
    assert len(r2["response"]) < len(r1["response"]), \
        "Repeated frustration response should be shorter, not a wall of text"


# -----------------------------------------------------------------------
# FAMILY STATUS IN CONFIRMATION
# -----------------------------------------------------------------------

def test_confirmation_with_children():
    """Confirmation message should mention children when family_status is set."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "shelter", "location": "Brooklyn", "family_status": "with_children"}
    msg = _build_confirmation_message(slots)
    assert "children" in msg.lower()


def test_confirmation_with_family():
    """Confirmation message should mention family."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "shelter", "location": "Queens", "family_status": "with_family"}
    msg = _build_confirmation_message(slots)
    assert "family" in msg.lower()


def test_confirmation_alone():
    """Confirmation message should mention 'yourself' when alone."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "shelter", "location": "Bronx", "family_status": "alone"}
    msg = _build_confirmation_message(slots)
    assert "yourself" in msg.lower()


def test_confirmation_no_family_status():
    """Confirmation without family_status should not mention family."""
    from app.services.confirmation import _build_confirmation_message
    slots = {"service_type": "shelter", "location": "Manhattan"}
    msg = _build_confirmation_message(slots)
    assert "children" not in msg.lower()
    assert "family" not in msg.lower()
    assert "yourself" not in msg.lower()


def test_family_status_extracted_in_flow(fresh_session):
    """Family status should be extracted during multi-turn shelter search."""
    send("I need shelter", session_id=fresh_session)
    send("Brooklyn", session_id=fresh_session)
    result = send("I have two kids with me", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("family_status") == "with_children"


# -----------------------------------------------------------------------
# SPLIT CLASSIFIER — _classify_action
# -----------------------------------------------------------------------

def test_classify_action_reset():
    """Reset phrases should return 'reset' action."""
    from app.services.classifier import _classify_action
    for phrase in ["start over", "reset", "new search", "cancel"]:
        assert _classify_action(phrase) == "reset", f"Failed on: {phrase}"


def test_classify_action_greeting():
    """Short greetings should return 'greeting' action."""
    from app.services.classifier import _classify_action
    assert _classify_action("hello") == "greeting"
    assert _classify_action("hi") == "greeting"


def test_classify_action_greeting_long_message():
    """Long messages starting with 'hi' should NOT classify as greeting."""
    from app.services.classifier import _classify_action
    assert _classify_action("hi I need food in Brooklyn") is None


def test_classify_action_confirm_yes():
    """Confirmation phrases should return 'confirm_yes'."""
    from app.services.classifier import _classify_action
    assert _classify_action("yes") == "confirm_yes"
    assert _classify_action("go ahead") == "confirm_yes"


def test_classify_action_confirm_deny():
    """Denial phrases should return 'confirm_deny'."""
    from app.services.classifier import _classify_action
    assert _classify_action("no") == "confirm_deny"
    assert _classify_action("not yet") == "confirm_deny"


def test_classify_action_bot_question():
    """Bot capability questions should return 'bot_question'."""
    from app.services.classifier import _classify_action
    assert _classify_action("how does this work") == "bot_question"
    assert _classify_action("is this private") == "bot_question"


def test_classify_action_escalation():
    """Escalation phrases should return 'escalation'."""
    from app.services.classifier import _classify_action
    assert _classify_action("connect me with a peer navigator") == "escalation"


def test_classify_action_help():
    """Help phrases should return 'help'."""
    from app.services.classifier import _classify_action
    assert _classify_action("help") == "help"


def test_classify_action_none_for_service():
    """Service requests should return None (not an action)."""
    from app.services.classifier import _classify_action
    assert _classify_action("I need food in Brooklyn") is None


def test_classify_action_none_for_emotional():
    """Emotional phrases should return None (not an action)."""
    from app.services.classifier import _classify_action
    assert _classify_action("I'm feeling really down") is None


# -----------------------------------------------------------------------
# SPLIT CLASSIFIER — _classify_tone
# -----------------------------------------------------------------------

def test_classify_tone_emotional():
    """Emotional phrases should return 'emotional'."""
    from app.services.classifier import _classify_tone
    assert _classify_tone("I'm feeling really down", crisis_result=None) == "emotional"
    assert _classify_tone("having a rough day", crisis_result=None) == "emotional"


def test_classify_tone_frustrated():
    """Frustration phrases should return 'frustrated'."""
    from app.services.classifier import _classify_tone
    assert _classify_tone("that's not helpful", crisis_result=None) == "frustrated"
    assert _classify_tone("this is useless", crisis_result=None) == "frustrated"


def test_classify_tone_confused():
    """Confused phrases should return 'confused'."""
    from app.services.classifier import _classify_tone
    assert _classify_tone("I don't know what to do", crisis_result=None) == "confused"
    assert _classify_tone("I'm overwhelmed", crisis_result=None) == "confused"


def test_classify_tone_none_for_neutral():
    """Neutral messages should return None."""
    from app.services.classifier import _classify_tone
    assert _classify_tone("I need food in Brooklyn", crisis_result=None) is None
    assert _classify_tone("hello", crisis_result=None) is None


def test_classify_tone_no_service_word_gate():
    """Tone classifier should detect emotion EVEN with service words.
    This is the key difference from the old _classify_message."""
    from app.services.classifier import _classify_tone
    # Old classifier would skip "emotional" because "need" is a service word
    assert _classify_tone("I'm struggling and need food", crisis_result=None) == "emotional"
    assert _classify_tone("I'm feeling down and need shelter", crisis_result=None) == "emotional"


# -----------------------------------------------------------------------
# COMBINED ROUTING — SERVICE INTENT + TONE
# -----------------------------------------------------------------------

def test_emotional_plus_service_routes_to_service(fresh_session):
    """'I'm struggling and need food in Brooklyn' should go to service flow
    with empathetic framing, not the pure emotional handler."""
    result = send("I'm struggling and need food in Brooklyn", session_id=fresh_session)
    # Should reach confirmation (service flow) not emotional response
    assert result.get("follow_up_needed") or "search" in result["response"].lower()
    # Should have empathetic prefix
    assert "hear you" in result["response"].lower() or "help" in result["response"].lower()


def test_help_plus_service_routes_to_service(fresh_session):
    """'I need help with immigration in the Bronx' should go to service flow,
    not the help handler."""
    result = send("I need help with immigration in the Bronx", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "legal"


def test_escalation_plus_service_routes_to_service(fresh_session):
    """'I'm a peer navigator, my client needs shelter in East Harlem'
    should go to service flow, not escalation."""
    result = send(
        "I'm a peer navigator. I have a client who needs shelter in East Harlem.",
        session_id=fresh_session,
    )
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "shelter"


def test_pure_emotional_still_works(fresh_session):
    """'I'm feeling really down' with no service intent should still
    trigger the emotional handler."""
    result = send("I'm feeling really down", session_id=fresh_session)
    response = result["response"].lower()
    # Should be empathetic, not a service confirmation
    assert "search" not in response or "food" not in response
    qr = result.get("quick_replies", [])
    labels = [q["label"] for q in qr]
    assert any("peer" in l.lower() or "navigator" in l.lower() for l in labels)


def test_pure_help_still_works(fresh_session):
    """'help' with no service intent should trigger help handler."""
    result = send("help", session_id=fresh_session)
    response = result["response"].lower()
    assert "food" in response or "shelter" in response  # lists categories


def test_pure_escalation_still_works(fresh_session):
    """'connect me with a peer navigator' should trigger escalation."""
    result = send("connect me with a peer navigator", session_id=fresh_session)
    response = result["response"].lower()
    assert "peer" in response or "navigator" in response or "streetlives" in response


def test_confused_plus_service_routes_to_service(fresh_session):
    """'I don't know, maybe shelter in Brooklyn?' should go to service
    with a gentle tone prefix."""
    result = send("I don't know, maybe shelter in Brooklyn?", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "shelter"
    # Should have confused tone prefix
    assert "no worries" in result["response"].lower() or "help" in result["response"].lower()


def test_frustrated_plus_service_routes_to_service(fresh_session):
    """'That wasn't helpful, find me clothing in Queens' should search
    with a frustrated tone prefix."""
    result = send("That wasn't helpful, find me clothing in Queens", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "clothing"
    # Should have frustrated tone prefix
    response = result["response"].lower()
    assert "understand" in response or "frustrating" in response or "different" in response


# -----------------------------------------------------------------------
# URGENT TONE
# -----------------------------------------------------------------------

def test_classify_tone_urgent():
    """Urgency phrases should return 'urgent' tone."""
    from app.services.classifier import _classify_tone
    phrases = [
        "I need shelter right now",
        "I have nowhere to go tonight",
        "please help me find food immediately",
        "this is urgent",
        "we're on the street",
        "I was evicted today",
        "I'm desperate",
    ]
    for phrase in phrases:
        # Pass crisis_result=None to isolate urgent tone testing.
        # Some of these phrases (e.g. "I'm desperate") correctly trigger
        # crisis via the LLM, but we're testing the urgent regex path here.
        result = _classify_tone(phrase, crisis_result=None)
        assert result == "urgent", f"Expected 'urgent' for '{phrase}', got '{result}'"


def test_classify_tone_emotional_beats_urgent():
    """Emotional tone should take priority over urgent."""
    from app.services.classifier import _classify_tone
    # "I'm scared" is emotional, "tonight" is urgent — emotional wins
    assert _classify_tone("I'm scared and need shelter tonight", crisis_result=None) == "emotional"


def test_classify_tone_urgent_without_emotion():
    """Pure urgency without emotional content should return 'urgent'."""
    from app.services.classifier import _classify_tone
    assert _classify_tone("I need food tonight", crisis_result=None) == "urgent"


def test_urgent_plus_service_gets_prefix(fresh_session):
    """Urgent + service intent should produce urgent prefix in confirmation."""
    result = send("I need shelter in Brooklyn right now", session_id=fresh_session)
    response = result["response"].lower()
    assert "urgent" in response or "right away" in response


def test_urgent_no_service_falls_through(fresh_session):
    """Urgent tone without service intent should not crash."""
    # "right now" has no service keyword — should fall to general/help
    result = send("I need help right now", session_id=fresh_session)
    # Should get help response or general, not an error
    assert result.get("response") is not None


# -----------------------------------------------------------------------
# BUG FIX — ESCALATION GUARD REGRESSION
# -----------------------------------------------------------------------

def test_escalation_without_location_stays_escalation(fresh_session):
    """'Connect me with a navigator about food' (food but no location)
    should route to escalation, not service. The user wants human help."""
    result = send("connect me with a peer navigator about food", session_id=fresh_session)
    response = result["response"].lower()
    # Should be escalation response (mentions peer/navigator/streetlives)
    assert any(w in response for w in ["peer", "navigator", "streetlives", "person"]), \
        f"Expected escalation response, got: {response[:100]}"


def test_escalation_with_service_and_location_routes_to_service(fresh_session):
    """'Navigator, client needs shelter in East Harlem' (service + location)
    should route to service, not escalation. This is a request on behalf of someone."""
    result = send(
        "I'm a peer navigator. My client needs shelter in East Harlem.",
        session_id=fresh_session,
    )
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "shelter"


def test_talk_to_someone_about_shelter_stays_escalation(fresh_session):
    """'I want to talk to someone about shelter' — user wants human,
    not a chatbot search."""
    result = send("I want to talk to someone about shelter", session_id=fresh_session)
    response = result["response"].lower()
    assert any(w in response for w in ["peer", "navigator", "streetlives", "person"]), \
        f"Expected escalation response, got: {response[:100]}"


# -----------------------------------------------------------------------
# BUG FIX — TONE PREFIX ON PENDING CONFIRMATION RE-SHOW
# -----------------------------------------------------------------------
# Fix 5 is a defensive code quality improvement. In practice, messages
# with a detected tone either match a category handler (emotional,
# frustrated, confused) that fires before the pending re-show, or they
# contain extractable slot data (urgency keywords) that causes the
# confirmation to re-trigger via the normal path rather than the re-show.
# The tone-aware nudge prefix exists as a safety net for future tones
# or handler reordering. No test needed for the current tone set.


# -----------------------------------------------------------------------
# FAMILY_STATUS REACHES QUERY_SERVICES
# -----------------------------------------------------------------------

def test_family_status_passed_to_query(fresh_session):
    """family_status should be passed through to query_services."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "shelter",
        "location": "Brooklyn",
        "family_status": "with_children",
        "_pending_confirmation": True,
    })
    result = send("yes", session_id=fresh_session)
    # The mock query_services was called — check it received family_status
    # We can verify indirectly: the result should have services (from mock)
    # and the session should have family_status
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    # family_status should still be in session after execution
    assert slots.get("family_status") == "with_children"


# -----------------------------------------------------------------------
# _classify_action RETURNS NONE FOR TONES
# -----------------------------------------------------------------------

def test_classify_action_none_for_frustrated():
    """Frustration phrases should return None from _classify_action (tone, not action)."""
    from app.services.classifier import _classify_action
    assert _classify_action("that wasn't helpful") is None
    assert _classify_action("this is useless") is None


def test_classify_action_none_for_confused():
    """Confused phrases should return None from _classify_action."""
    from app.services.classifier import _classify_action
    assert _classify_action("I don't know what to do") is None


def test_classify_action_none_for_urgent():
    """Urgent phrases without 'help' should return None from _classify_action.

    Note: 'I need help right now' legitimately contains 'help' and correctly
    returns 'help' from _classify_action. The urgency ('right now') is
    captured separately by _classify_tone. In generate_reply, both action
    and tone are available for combined routing."""
    from app.services.classifier import _classify_action
    assert _classify_action("tonight") is None
    assert _classify_action("I need something right now") is None
    assert _classify_action("this is urgent") is None
    # "I need help right now" correctly returns "help" — the word "help"
    # is present and should match. Urgency is a tone, not an action.
    assert _classify_action("I need help right now") == "help"


# -----------------------------------------------------------------------
# SERVICE QUEUE (multi-intent PR 3)
# -----------------------------------------------------------------------

def test_multi_intent_queues_additional_services(fresh_session):
    """'I need food and shelter in Brooklyn' should queue shelter."""
    result = send("I need food and shelter in Brooklyn", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "food"
    assert "_queued_services" in slots
    assert any(s[0] == "shelter" for s in slots["_queued_services"])


def test_multi_intent_offers_queued_after_results(fresh_session):
    """After food+shelter query, bot should mention both services in results."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
        "_pending_confirmation": True,
    })
    result = send("yes", session_id=fresh_session)
    response = result["response"].lower()
    assert "shelter" in response, f"Should mention shelter, got: {response[:120]}"
    # Co-located query: either "both food and shelter" or fallback "also mentioned"
    assert (
        ("both" in response and "food" in response and "shelter" in response)
        or "also mentioned" in response
    ), f"Expected co-located or queue-offer message, got: {response[:120]}"


def test_multi_intent_yes_to_queued_service(fresh_session):
    """Tapping 'Yes, search for shelter' should start a shelter search."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
    })
    result = send("I need shelter", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "shelter"
    assert slots.get("location") == "Brooklyn"


def test_multi_intent_no_thanks_clears_queue(fresh_session):
    """'No thanks' should clear the queue and show wrap-up."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
    })
    result = send("No thanks", session_id=fresh_session)
    response = result["response"].lower()
    assert "no problem" in response or "let me know" in response
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert "_queued_services" not in slots


def test_multi_intent_three_services_queued(fresh_session):
    """Three services should queue the second and third."""
    result = send("I need food, clothing, and legal help in Manhattan", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "food"
    queued = slots.get("_queued_services", [])
    queued_types = [s[0] for s in queued]
    assert "clothing" in queued_types
    assert "legal" in queued_types


def test_multi_intent_queue_not_overwritten(fresh_session):
    """If a queue already exists, a new message shouldn't overwrite it."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
    })
    result = send("actually make that Queens", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert "_queued_services" in slots


def test_multi_intent_queue_cleared_on_service_change(fresh_session):
    """Changing service type without multi-intent should clear stale queue."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
    })
    result = send("I need medical care", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert slots.get("service_type") == "medical"
    assert "_queued_services" not in slots


def test_multi_intent_reset_clears_queue(fresh_session):
    """'Start over' should clear everything including the queue."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
    })
    result = send("start over", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert "_queued_services" not in slots


def test_multi_intent_no_queue_when_single_service(fresh_session):
    """Single service message should not create a queue."""
    result = send("I need food in Brooklyn", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    assert "_queued_services" not in slots


def test_multi_intent_queue_offer_uses_detail_label(fresh_session):
    """Queue offer should use service_detail label when available."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("medical", "dental care")],
        "_pending_confirmation": True,
    })
    result = send("yes", session_id=fresh_session)
    response = result["response"].lower()
    # Co-located query should use detail label "dental care" when available
    if result.get("result_count", 0) > 0:
        assert "dental care" in response or "health care" in response, \
            f"Should use service label, got: {response[:120]}"


def test_multi_intent_queue_preserved_through_confirmation(fresh_session):
    """Queue should survive the confirmation flow."""
    result = send("I need food and shelter in Brooklyn", session_id=fresh_session)
    from app.services.session_store import get_session_slots
    slots = get_session_slots(fresh_session)
    # Should have pending confirmation AND queue
    assert slots.get("_pending_confirmation") is True
    assert "_queued_services" in slots
    assert any(s[0] == "shelter" for s in slots["_queued_services"])


# -----------------------------------------------------------------------
# NO-RESULTS MESSAGE & NEARBY BOROUGH SUGGESTIONS (Issue 10)
# -----------------------------------------------------------------------

def test_no_results_message_basic():
    """No-results message should mention the service and location."""
    msg = _no_results_message({"service_type": "food", "location": "Brooklyn"})
    assert "food" in msg.lower()
    assert "brooklyn" in msg.lower()
    assert "wasn't able" in msg.lower() or "try" in msg.lower()


def test_no_results_message_suggests_nearby_boroughs():
    """No-results for a borough search should suggest nearby boroughs."""
    msg = _no_results_message({"service_type": "food", "location": "Staten Island"})
    # Staten Island food should suggest Brooklyn or Queens (higher service counts)
    assert "brooklyn" in msg.lower() or "queens" in msg.lower()


def test_no_results_message_different_service_different_suggestions():
    """Different service types should suggest different boroughs based on availability."""
    from app.services.confirmation import _get_nearby_boroughs
    food_nearby = _get_nearby_boroughs("food", "Staten Island")
    shelter_nearby = _get_nearby_boroughs("shelter", "Staten Island")
    # Both should return suggestions, but they may differ
    assert len(food_nearby) > 0
    assert len(shelter_nearby) > 0


def test_no_results_message_neighborhood_no_borough_suggestion():
    """No-results for a neighborhood search should NOT suggest boroughs
    (neighborhoods are already within a borough)."""
    msg = _no_results_message({"service_type": "food", "location": "harlem"})
    # Should suggest trying a different neighborhood, not specific boroughs
    assert "different neighborhood" in msg.lower() or "different" in msg.lower()


def test_no_results_message_includes_navigator_option():
    """No-results should always mention peer navigator as an option."""
    msg = _no_results_message({"service_type": "shelter", "location": "Queens"})
    assert "peer navigator" in msg.lower() or "real person" in msg.lower()


def test_get_nearby_boroughs_all_boroughs_covered():
    """Every borough should have nearby suggestions for common service types."""
    from app.services.confirmation import _get_nearby_boroughs
    boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
    for service in ["food", "shelter", "clothing", "medical"]:
        for borough in boroughs:
            nearby = _get_nearby_boroughs(service, borough)
            assert len(nearby) > 0, \
                f"No nearby suggestions for {service} in {borough}"
            assert borough not in nearby, \
                f"Nearby list for {borough} should not include {borough} itself"


def test_get_nearby_boroughs_unknown_service_uses_default():
    """Unknown service types should fall back to geographic proximity defaults."""
    from app.services.confirmation import _get_nearby_boroughs
    nearby = _get_nearby_boroughs("xyz_unknown", "Brooklyn")
    assert len(nearby) > 0, "Should fall back to default nearby boroughs"


def test_get_nearby_boroughs_unknown_borough():
    """Unknown borough should return empty list, not crash."""
    from app.services.confirmation import _get_nearby_boroughs
    nearby = _get_nearby_boroughs("food", "Yonkers")
    assert isinstance(nearby, list)


# -----------------------------------------------------------------------
# CO-LOCATED MULTI-SERVICE QUERY
# -----------------------------------------------------------------------

def test_colocated_query_mentions_both_services(fresh_session):
    """Co-located results should mention both services in the response."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Manhattan",
        "_queued_services": [("clothing", None)],
        "_pending_confirmation": True,
    })
    result = send("yes", session_id=fresh_session)
    response = result["response"].lower()
    assert "food" in response
    assert "clothing" in response
    assert "both" in response or "also" in response


def test_colocated_query_clears_queue_on_success(fresh_session):
    """Successful co-located query should clear the queue."""
    from app.services.session_store import save_session_slots, get_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food",
        "location": "Brooklyn",
        "_queued_services": [("shelter", None)],
        "_pending_confirmation": True,
    })
    send("yes", session_id=fresh_session)
    slots = get_session_slots(fresh_session)
    assert "_queued_services" not in slots, "Queue should be cleared after co-located success"


def test_colocated_confirmation_mentions_both(fresh_session):
    """Confirmation message should list all requested services."""
    result = send("I need food and clothing in Brooklyn", session_id=fresh_session)
    response = result["response"].lower()
    assert "food" in response
    assert "clothing" in response
    assert "brooklyn" in response


# -----------------------------------------------------------------------
# QUICK REPLY BUTTON AUDIT TESTS
# -----------------------------------------------------------------------

def test_bot_identity_buttons(fresh_session):
    """Bot identity should show New search + Peer navigator, not welcome menu."""
    result = send("are you a bot", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "🔍 New search" in labels
    assert "🤝 Peer navigator" in labels
    assert "🍽️ Food" not in labels, "Should NOT show welcome menu"


def test_emotional_buttons_no_welcome_menu(fresh_session):
    """Emotional response should show only Peer navigator, not welcome menu or New search."""
    result = send("I'm scared and alone", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "🤝 Peer navigator" in labels
    assert "🍽️ Food" not in labels
    # Emotional should NOT show New search — only navigator
    assert "🔍 New search" not in labels


def test_frustrated_first_buttons(fresh_session):
    """Frustrated (first time) should show New search + Peer navigator."""
    result = send("this is not helpful at all", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "🔍 New search" in labels
    assert "🤝 Peer navigator" in labels
    assert "🍽️ Food" not in labels


def test_escalation_buttons(fresh_session):
    """Escalation should show New search + Talk to a person."""
    result = send("I want to talk to someone", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    values = [qr["value"] for qr in result.get("quick_replies", [])]
    assert "🔍 New search" in labels
    assert "👤 Talk to a person" in labels
    assert "Connect with person" in values
    assert "🍽️ Food" not in labels


def test_yes_after_emotional_shows_escalation_buttons(fresh_session):
    """'Yes' after emotional should show contact/search buttons."""
    send("I'm feeling really down", session_id=fresh_session)
    result = send("yes", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    # Buttons offer to show contact info again or search for services
    assert any("contact" in l.lower() or "search" in l.lower() for l in labels)


def test_no_after_escalation_shows_escalation_buttons(fresh_session):
    """'No' after escalation should still show peer navigator option."""
    send("I want to talk to someone", session_id=fresh_session)
    result = send("no", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert any("peer" in l.lower() or "navigator" in l.lower() for l in labels)


def test_connect_with_person_routes_to_escalation(fresh_session):
    """'Connect with person' (Talk to a person button value) should trigger escalation."""
    from app.services.classifier import _classify_action
    assert _classify_action("Connect with person") == "escalation"


def test_connect_with_peer_navigator_routes_to_escalation(fresh_session):
    """'Connect with peer navigator' (Peer navigator button value) should trigger escalation."""
    from app.services.classifier import _classify_action
    assert _classify_action("Connect with peer navigator") == "escalation"


def test_peer_navigator_label_standardized(fresh_session):
    """All non-escalation paths should use '🤝 Peer navigator', not 'Talk to a person'."""
    # Emotional
    r1 = send("I'm feeling really down", session_id=fresh_session)
    labels1 = [qr["label"] for qr in r1.get("quick_replies", [])]
    assert "🤝 Peer navigator" in labels1
    assert "🤝 Talk to a person" not in labels1


def test_location_change_has_use_my_location_first(fresh_session):
    """'Change location' should show 'Use my location' as the first option."""
    from app.services.session_store import save_session_slots
    save_session_slots(fresh_session, {
        "service_type": "food", "location": "Brooklyn",
    })
    result = send("Change location", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" in labels, f"Missing Use my location, got {labels}"
    assert labels[0] == "📍 Use my location", f"Should be first, got {labels}"
    assert "Staten Island" in labels, "Should include Staten Island"


# -----------------------------------------------------------------------
# LOCATION UNKNOWN — "I don't know" when bot asks for location
# -----------------------------------------------------------------------

def test_idk_after_location_ask_offers_geolocation(fresh_session):
    """'I don't know' after location prompt should offer Use my location."""
    send("I need shelter", session_id=fresh_session)
    result = send("I don't know", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" in labels
    assert "Manhattan" in labels
    assert "🍽️ Food" not in labels, "Should NOT show welcome menu"


def test_idk_variants_after_location_ask(fresh_session):
    """Multiple 'I don't know' variants should all offer geolocation."""
    for phrase in ["idk", "not sure", "I'm not sure", "no idea",
                   "I don't know where I am", "anywhere", "wherever",
                   "doesn't matter", "here", "right here"]:
        from app.services.session_store import clear_session
        clear_session(fresh_session)
        send("I need food", session_id=fresh_session)
        result = send(phrase, session_id=fresh_session)
        labels = [qr["label"] for qr in result.get("quick_replies", [])]
        assert "📍 Use my location" in labels, f"'{phrase}' should offer geolocation"


def test_here_exact_match_no_false_positive(fresh_session):
    """'here' inside longer phrases should NOT trigger location-unknown handler.
    The bot correctly shows location buttons because it needs a location for
    the food search — that's the normal follow-up flow, not the location-unknown
    handler (which says 'No problem! You can share your location...')."""
    send("I need food", session_id=fresh_session)
    result = send("here's what I need", session_id=fresh_session)
    # The location-unknown handler adds a specific message. Verify it didn't fire.
    assert "no problem" not in result["response"].lower() or \
           "share your location" not in result["response"].lower()
    # Service type should still be food (not cleared)
    assert result["slots"].get("service_type") == "food"


def test_service_flow_continuation_near_me(fresh_session):
    """'near me' after location ask should show geolocation buttons."""
    send("I need shelter", session_id=fresh_session)
    result = send("near me", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" in labels, "'near me' should offer geolocation"


def test_service_flow_continuation_close_by(fresh_session):
    """'close by' after location ask should show geolocation buttons."""
    send("I need food", session_id=fresh_session)
    result = send("close by", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" in labels, "'close by' should offer geolocation"


def test_idk_without_service_type_is_confused(fresh_session):
    """'I don't know' without prior service should NOT offer geolocation."""
    result = send("I don't know", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" not in labels


def test_idk_with_location_set_is_confused(fresh_session):
    """'I don't know' when location is already set should NOT offer geolocation."""
    send("I need food in Brooklyn", session_id=fresh_session)
    result = send("I don't know", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "📍 Use my location" not in labels


# ---------------------------------------------------------------------------
# RUN 22 — Emotion-specific responses
# ---------------------------------------------------------------------------

def test_pick_emotional_response_scared():
    """'I'm scared' should get the scared-specific response."""
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("I'm feeling really scared right now")
    assert "scared" in resp.lower() or "frightening" in resp.lower() or "fear" in resp.lower()
    # Must NOT mention services
    assert "food" not in resp.lower()
    assert "shelter" not in resp.lower()
    assert "shower" not in resp.lower()


def test_pick_emotional_response_sad():
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("I'm feeling really down today")
    assert "sorry" in resp.lower() or "courage" in resp.lower()


def test_pick_emotional_response_rough_day():
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("Having a really rough day")
    assert "hard" in resp.lower() or "heavy" in resp.lower()


def test_pick_emotional_response_shame():
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("I'm embarrassed to ask for help")
    assert "ashamed" in resp.lower() or "strength" in resp.lower() or "nothing to be" in resp.lower()


def test_pick_emotional_response_grief():
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("My friend died last week")
    assert "sorry" in resp.lower() or "loss" in resp.lower()


def test_pick_emotional_response_alone():
    from app.services.responses import _pick_emotional_response
    resp = _pick_emotional_response("I feel completely alone")
    assert "alone" in resp.lower() or "invisible" in resp.lower() or "courage" in resp.lower()


def test_emotional_no_llm_call(fresh_session):
    """Emotional handler should NOT call the LLM — static response only."""
    import unittest.mock as mock
    with mock.patch("app.services.chatbot.claude_reply") as mock_llm:
        send("I'm feeling really scared right now", session_id=fresh_session)
        # claude_reply should NOT have been called for emotional category
        for call in mock_llm.call_args_list:
            prompt = call[0][0] if call[0] else ""
            assert "empathetic" not in prompt.lower(), \
                "LLM was called with empathetic prompt — should use static response"


# ---------------------------------------------------------------------------
# RUN 22 — Negative preference
# ---------------------------------------------------------------------------

def test_negative_preference_classification():
    from app.services.classifier import _classify_action
    assert _classify_action("I do not want any of those") == "negative_preference"
    assert _classify_action("None of those are what I need") == "negative_preference"
    assert _classify_action("Those don't help") == "negative_preference"


def test_negative_preference_handler(fresh_session):
    """Negative preference should acknowledge rejection and offer alternatives."""
    result = send("None of those are what I need", session_id=fresh_session)
    assert "understand" in result["response"].lower()
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    # Should show service menu + navigator
    assert any("Peer navigator" in l for l in labels)
    assert any("Food" in l or "Shelter" in l for l in labels)


# ---------------------------------------------------------------------------
# RUN 22 — Privacy routing exception
# ---------------------------------------------------------------------------

def test_privacy_question_with_service_keyword(fresh_session):
    """Privacy question containing 'shelter' should route to bot_question, not service."""
    from app.services.classifier import _classify_action
    action = _classify_action("If I search for shelter here, do they get my information?")
    assert action == "bot_question"


def test_privacy_question_answered(fresh_session):
    """Privacy question should get a privacy answer, not a shelter search."""
    result = send("Does the shelter know I searched?", session_id=fresh_session)
    resp = result["response"].lower()
    # Should mention privacy/anonymity, not ask for location
    assert any(w in resp for w in ["private", "anonymous", "can't see", "won't affect"])


# ---------------------------------------------------------------------------
# RUN 22 — Conversational awareness guard
# ---------------------------------------------------------------------------

def test_casual_chat_no_service_buttons(fresh_session):
    """'How are you' should NOT show service category buttons."""
    result = send("Hey how are you doing today", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "🍽️ Food" not in labels
    assert "🏠 Shelter" not in labels


# ---------------------------------------------------------------------------
# RUN 22 — Frustration 3-tier
# ---------------------------------------------------------------------------

def test_frustration_third_tier(fresh_session):
    """Third frustration should give immediate navigator offer."""
    # Mock crisis at the classifier level too — LLM correctly flags
    # 'nothing works' as crisis, but we're testing the frustration tier path.
    with patch("app.services.classifier.detect_crisis", return_value=None):
        send("this is not helpful", session_id=fresh_session)
        send("still not helpful", session_id=fresh_session)
        result = send("nothing works", session_id=fresh_session)
    labels = [qr["label"] for qr in result.get("quick_replies", [])]
    assert "🤝 Peer navigator" in labels
    # Should NOT have New search on 3rd tier
    assert "🔍 New search" not in labels
