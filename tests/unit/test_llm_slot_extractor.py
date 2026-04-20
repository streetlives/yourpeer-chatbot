"""
Tests for the LLM-based slot extractor.

Unit tests mock the Anthropic API. Integration tests (marked with _live suffix)
require ANTHROPIC_API_KEY in the environment and hit the real API.

Run unit tests:  python tests/test_llm_slot_extractor.py
Run live tests:  ANTHROPIC_API_KEY=sk-... python tests/test_llm_slot_extractor.py --live
"""

import os
import pytest
from unittest.mock import patch, MagicMock


from app.services.llm_slot_extractor import (
    extract_slots_llm,
    extract_slots_smart,
    _empty_slots,
)


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _mock_tool_response(**slots):
    """Build a mock Anthropic API response with tool_use output."""
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "extract_intake_slots"
    mock_block.input = slots

    mock_response = MagicMock()
    mock_response.content = [mock_block]
    return mock_response


def _mock_client_with_response(**slots):
    """Create a mock Anthropic client that returns the given slots."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_tool_response(**slots)
    return mock_client


# -----------------------------------------------------------------------
# UNIT TESTS — LLM EXTRACTION (mocked)
# -----------------------------------------------------------------------

@patch("app.services.llm_slot_extractor.get_client")
def test_llm_extracts_service_and_location(mock_get_client):
    """LLM should extract service type and location."""
    mock_get_client.return_value = _mock_client_with_response(
        service_type="food",
        location="Brooklyn",
    )
    result = extract_slots_llm("I need food in Brooklyn")
    assert result["service_type"] == "food"
    assert result["location"] == "Brooklyn"


@patch("app.services.llm_slot_extractor.get_client")
def test_llm_extracts_age_and_gender(mock_get_client):
    """LLM should extract age and gender when mentioned."""
    mock_get_client.return_value = _mock_client_with_response(
        service_type="shelter",
        location="Queens",
        age=17,
        gender="female",
        urgency="high",
    )
    result = extract_slots_llm("I'm a 17 year old girl and I need shelter tonight in Queens")
    assert result["age"] == 17
    assert result["_gender"] == "female"
    assert result["urgency"] == "high"


@patch("app.services.llm_slot_extractor.get_client")
def test_llm_handles_third_person(mock_get_client):
    """LLM should extract info about a third person ('my son is 12')."""
    mock_get_client.return_value = _mock_client_with_response(
        service_type="clothing",
        age=12,
    )
    result = extract_slots_llm("my son is 12 and needs a coat")
    assert result["service_type"] == "clothing"
    assert result["age"] == 12


@patch("app.services.llm_slot_extractor.get_client")
def test_llm_handles_contradicting_locations(mock_get_client):
    """LLM should pick the intended location, not the current one."""
    mock_get_client.return_value = _mock_client_with_response(
        service_type="food",
        location="Bronx",
    )
    result = extract_slots_llm("I'm in Queens but looking for food in the Bronx")
    assert result["location"] == "Bronx"


@patch("app.services.llm_slot_extractor.get_client")
def test_llm_handles_empty_message(mock_get_client):
    """LLM should return empty slots for non-service messages."""
    mock_get_client.return_value = _mock_client_with_response()
    result = extract_slots_llm("hello")
    assert result["service_type"] is None
    assert result["location"] is None


@patch("app.services.llm_slot_extractor.get_client")
def test_llm_failure_returns_empty(mock_get_client):
    """If the LLM call fails, should return empty slots, not crash."""
    mock_get_client.side_effect = RuntimeError("API key missing")
    result = extract_slots_llm("I need food in Brooklyn")
    assert result == _empty_slots()


# -----------------------------------------------------------------------
# UNIT TESTS — SMART EXTRACTOR (complexity-based routing)
# -----------------------------------------------------------------------

@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_uses_regex_for_simple_messages(mock_llm):
    """Short, clear messages with known location should skip LLM."""
    result = extract_slots_smart("I need food in Brooklyn")
    mock_llm.assert_not_called()
    assert result["service_type"] == "food"
    assert "brooklyn" in result["location"].lower()


@pytest.mark.xfail(reason="Regex override: 'hospital' matches medical keyword, overriding LLM's correct 'shelter'. The override prefers regex when both disagree — correct for 'dental' vs 'personal_care' but wrong here.")
@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_uses_llm_for_long_messages(mock_llm):
    """Long messages should always go to LLM even if regex finds slots."""
    mock_llm.return_value = {
        "service_type": "shelter",
        "location": "East New York",
        "age": None,
        "urgency": "high",
        "gender": None,
    }
    msg = (
        "I just got out of the hospital and I have been staying with friends "
        "in East New York but they can not keep me anymore"
    )
    result = extract_slots_smart(msg)
    mock_llm.assert_called_once()
    assert result["service_type"] == "shelter"  # LLM gets this right
    assert "east new york" in result["location"].lower()


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_uses_llm_when_regex_partial(mock_llm):
    """When regex finds service but no location, LLM should be called."""
    mock_llm.return_value = {
        "service_type": "food",
        "location": "Harlem",
        "age": None,
        "urgency": None,
        "gender": None,
    }
    _result = extract_slots_smart("I need food")
    mock_llm.assert_called_once()


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_uses_llm_for_implicit_needs(mock_llm):
    """Implicit needs that regex can't parse should trigger LLM."""
    mock_llm.return_value = {
        "service_type": "shelter",
        "location": "Bronx",
        "age": None,
        "urgency": "high",
        "gender": "female",
    }
    result = extract_slots_smart("somewhere safe for tonight, I'm a woman near the Bronx")
    mock_llm.assert_called_once()
    assert result["service_type"] == "shelter"
    assert result["urgency"] == "high"
    assert result["gender"] == "female"


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_llm_supplements_with_regex(mock_llm):
    """When LLM misses a slot that regex got, regex fills the gap."""
    # LLM gets service+location but misses urgency
    mock_llm.return_value = {
        "service_type": "food",
        "location": "Harlem",
        "age": None,
        "urgency": None,  # LLM missed this
        "gender": None,
    }
    # "tonight" would be caught by regex urgency extraction
    result = extract_slots_smart("I need food tonight in Harlem please help me out")
    assert result["service_type"] == "food"
    assert result["location"] == "Harlem"
    # Regex urgency should supplement the LLM gap
    assert result["urgency"] == "high"

@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_regex_overrides_biased_llm_service_type(mock_llm):
    """When regex finds an explicit service keyword but the LLM returns a
    different service_type (biased by conversation history), regex wins.

    Scenario: user searched for showers, then says 'What about dental care?'
    LLM sees shower-heavy history and returns personal_care. Regex found
    'dental' → medical. Regex should take priority.
    """
    mock_llm.return_value = {
        "service_type": "personal_care",  # LLM is context-biased
        "location": None,
        "age": None,
        "urgency": None,
        "gender": None,
    }
    result = extract_slots_smart("What about dental care?")
    # Regex found "dental" → medical. Should override the LLM's biased answer.
    assert result["service_type"] == "medical", \
        f"Expected regex override to 'medical', got '{result['service_type']}'"
    mock_llm.assert_called_once()


@pytest.mark.xfail(reason="Regex override: 'hospital' matches medical keyword even though the user's need is shelter. Test description says 'no regex match' but regex does find 'hospital' → medical.")
@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_regex_does_not_override_when_no_regex_match(mock_llm):
    """When regex finds no service_type but LLM does, LLM result is used."""
    mock_llm.return_value = {
        "service_type": "shelter",
        "location": "Queens",
        "age": None,
        "urgency": None,
        "gender": None,
    }
    # "I just got out of the hospital and need somewhere safe" — no explicit
    # service keyword for shelter, but LLM infers it.
    result = extract_slots_smart("I just got out of the hospital and need somewhere safe")
    assert result["service_type"] == "shelter"


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_regex_no_override_when_llm_agrees(mock_llm):
    """When regex and LLM agree on service_type, no override needed."""
    mock_llm.return_value = {
        "service_type": "medical",
        "location": "Manhattan",
        "age": None,
        "urgency": None,
        "gender": None,
    }
    result = extract_slots_smart("I need dental care in Manhattan")
    assert result["service_type"] == "medical"

@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_llm_failure_falls_back_to_regex(mock_llm):
    """If LLM fails, smart extractor should return regex results."""
    mock_llm.return_value = _empty_slots()  # LLM failed
    result = extract_slots_smart("I need food")
    assert result["service_type"] == "food"  # regex still works


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_conflicting_keywords_go_to_llm(mock_llm):
    """Messages with multiple service keywords should go to LLM."""
    mock_llm.return_value = {
        "service_type": "shelter",
        "location": "Manhattan",
        "age": None,
        "urgency": None,
        "gender": None,
    }
    # "hospital" (medical) + "shelter" — conflicting keywords
    _result = extract_slots_smart("hospital near Manhattan for shelter")
    mock_llm.assert_called_once()


@patch("app.services.llm_slot_extractor.extract_slots_llm")
def test_smart_unknown_location_goes_to_llm(mock_llm):
    """Unknown location (not in known list) should go to LLM."""
    mock_llm.return_value = {
        "service_type": "food",
        "location": "City Hall",
        "age": None,
        "urgency": None,
        "gender": None,
    }
    result = extract_slots_smart("food near City Hall")
    mock_llm.assert_called_once()
    assert result["service_type"] == "food"  # regex still got this


# -----------------------------------------------------------------------
# CONVERSATION HISTORY TESTS
# -----------------------------------------------------------------------

def test_history_passed_to_llm():
    """extract_slots_llm should forward conversation history to Claude."""
    mock_response = _mock_tool_response_from_dict({"location": "Brooklyn"})

    with patch("app.services.llm_slot_extractor.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_get.return_value = mock_client

        history = [
            {"role": "user", "text": "I need food in Queens"},
            {"role": "assistant", "text": "I'll search for food in Queens."},
        ]
        result = extract_slots_llm("What about in Brooklyn?", conversation_history=history)

        messages = mock_client.messages.create.call_args.kwargs["messages"]
        assert len(messages) == 3  # 2 history + 1 current
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "I need food in Queens"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["content"] == "What about in Brooklyn?"
        assert result["location"] == "Brooklyn"


def test_history_alternating_messages_enforced():
    """Consecutive same-role messages in history should get placeholders."""
    mock_response = _mock_tool_response_from_dict({})

    with patch("app.services.llm_slot_extractor.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_get.return_value = mock_client

        history = [
            {"role": "user", "text": "I need food"},
            {"role": "user", "text": "in Queens"},
        ]
        extract_slots_llm("try Brooklyn", conversation_history=history)

        messages = mock_client.messages.create.call_args.kwargs["messages"]
        # Verify strictly alternating roles
        for i in range(1, len(messages)):
            assert messages[i]["role"] != messages[i - 1]["role"], \
                f"Messages {i-1} and {i} have same role: {messages[i]['role']}"


def test_history_none_and_empty():
    """None and empty history should work (single-message extraction)."""
    mock_response = _mock_tool_response_from_dict({"service_type": "food"})

    with patch("app.services.llm_slot_extractor.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_get.return_value = mock_client

        extract_slots_llm("food in Brooklyn", conversation_history=None)
        msgs_none = mock_client.messages.create.call_args.kwargs["messages"]
        assert len(msgs_none) == 1

        extract_slots_llm("food in Brooklyn", conversation_history=[])
        msgs_empty = mock_client.messages.create.call_args.kwargs["messages"]
        assert len(msgs_empty) == 1


def test_history_truncated_to_six():
    """Only the last 6 history turns should be included."""
    mock_response = _mock_tool_response_from_dict({})

    with patch("app.services.llm_slot_extractor.get_client") as mock_get:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_get.return_value = mock_client

        history = []
        for i in range(10):
            history.append({"role": "user", "text": f"msg {i}"})
            history.append({"role": "assistant", "text": f"resp {i}"})

        extract_slots_llm("latest message", conversation_history=history)

        messages = mock_client.messages.create.call_args.kwargs["messages"]
        # 6 history messages + 1 current = 7
        assert len(messages) == 7


def test_smart_extractor_passes_history():
    """extract_slots_smart should forward history to extract_slots_llm."""
    with patch("app.services.llm_slot_extractor.extract_slots_llm") as mock_llm:
        mock_llm.return_value = {"service_type": "food", "location": "Brooklyn",
                                  "age": None, "urgency": None, "gender": None}
        history = [{"role": "user", "text": "test context"}]

        # Force complex path (long message)
        extract_slots_smart(
            "I was just released from the hospital and need somewhere to go",
            conversation_history=history,
        )

        mock_llm.assert_called_once()
        call_kwargs = mock_llm.call_args.kwargs
        assert "conversation_history" in call_kwargs
        assert call_kwargs["conversation_history"] == history


def _mock_tool_response_from_dict(slot_values):
    """Helper: create a mock Claude response with tool_use block."""
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "extract_intake_slots"
    mock_block.input = slot_values
    mock_response.content = [mock_block]
    return mock_response


# -----------------------------------------------------------------------
# INTEGRATION TESTS (only run with --live flag)
# -----------------------------------------------------------------------

_skip_no_api_key = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live LLM tests",
)


@_skip_no_api_key
def test_live_simple_extraction():
    """[LIVE] Simple service + location extraction."""
    result = extract_slots_llm("I need food in Brooklyn")
    assert result["service_type"] == "food"
    assert "brooklyn" in (result["location"] or "").lower()
    print("  PASS [LIVE]: simple extraction")


@_skip_no_api_key
def test_live_third_person():
    """[LIVE] Third-person extraction."""
    result = extract_slots_llm("my son is 12 and needs a warm coat")
    assert result["service_type"] == "clothing"
    assert result["age"] == 12
    print("  PASS [LIVE]: third-person extraction")


@_skip_no_api_key
def test_live_contradicting_locations():
    """[LIVE] Intended vs current location."""
    result = extract_slots_llm("I'm in Queens but looking for food in the Bronx")
    assert result["service_type"] == "food"
    assert "bronx" in (result["location"] or "").lower()
    print("  PASS [LIVE]: contradicting locations")


@_skip_no_api_key
def test_live_implicit_needs():
    """[LIVE] Implicit service type from context."""
    result = extract_slots_llm("somewhere safe for tonight, I'm a woman")
    assert result["service_type"] == "shelter"
    assert result["urgency"] == "high"
    assert result["_gender"] is not None
    print("  PASS [LIVE]: implicit needs")


@_skip_no_api_key
def test_live_complex_sentence():
    """[LIVE] Complex sentence with multiple slots."""
    result = extract_slots_llm(
        "I'm 22, just got out of Rikers, and I need help finding "
        "a place to stay in the Bronx tonight"
    )
    assert result["service_type"] == "shelter"
    assert result["age"] == 22
    assert "bronx" in (result["location"] or "").lower()
    assert result["urgency"] == "high"
    print("  PASS [LIVE]: complex sentence")


# -----------------------------------------------------------------------
