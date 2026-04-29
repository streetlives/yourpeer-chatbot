"""
SERVICE DATA / LLM ISOLATION — Comprehensive Test Suite

Architectural guarantee: the LLM handles conversational intake ONLY.
All service data comes from deterministic database queries. The LLM
must NEVER receive, generate, or rephrase service information.

This suite tests every code path where an LLM call could occur and
verifies that service slots, search results, and database content
are never included in prompts sent to Claude.

Layers tested:
  1. Prompt builders — output must not contain service slot values
  2. LLM call sites — captured prompts must be slot-free
  3. Search execution firewall — DB failures use static messages, never LLM
  4. Structured LLM outputs — crisis/classifier/extractor never produce user-facing service text
  5. End-to-end flows — full conversation paths with slot state
  6. System prompt audit — no service data in any system prompt
  7. Regression guards — specific scenarios that previously leaked

Run: pytest tests/integration/test_llm_isolation.py -v
"""

import json
import re
import pytest
from unittest.mock import patch, MagicMock, call

from app.services.chatbot import generate_reply
from app.services.session_store import clear_session, get_session_slots, save_session_slots
from conftest import MOCK_QUERY_RESULTS, MOCK_EMPTY_RESULTS, send, send_multi


# =====================================================================
# CONSTANTS — service slot fields that must NEVER appear in LLM prompts
# =====================================================================

# These are the slot keys that carry service search context.
# If ANY of these values appear in a prompt sent to the LLM,
# the architectural firewall has been breached.
_SERVICE_SLOT_KEYS = {
    "service_type", "service_detail", "location", "age",
    "family_status", "_gender", "org_name", "urgency",
    "_populations", "_queued_services",
}

# Realistic slot state — the worst-case scenario where every
# service field is populated. If ANY of these values leak into
# an LLM prompt, the test fails.
_FULL_SLOTS = {
    "service_type": "shelter",
    "service_detail": "domestic violence",
    "location": "East Harlem",
    "age": 19,
    "family_status": "with_children",
    "_gender": "female",
    "org_name": "Safe Horizon",
    "urgency": "high",
    "_populations": ["youth", "lgbtq"],
    "_queued_services": [("food", None, "Brooklyn")],
    "_latitude": 40.7943,
    "_longitude": -73.9420,
    "no_requirements": False,
    "_contradiction": False,
    "transcript": [
        {"role": "user", "text": "I need shelter"},
        {"role": "bot", "text": "I'll look for shelter..."},
    ],
}

# Values that should NEVER appear in LLM prompts — these are
# USER-SPECIFIC search context from _FULL_SLOTS, not generic category
# names. Category names like "shelter" and "food" legitimately appear
# in capability descriptions and guardrails, but user-specific values
# (locations, org names, ages, demographics) must never leak.
_FORBIDDEN_VALUES = [
    "East Harlem",        # user's location
    "Safe Horizon",       # user's org name
    "with_children",      # user's family status
    "40.7943", "-73.9420",  # user's coordinates
]

# These are slot-context PATTERNS that indicate the prompt builder
# is injecting structured slot data. Category names in prose capability
# descriptions are fine; category names in slot-context format are NOT.
_SLOT_CONTEXT_PATTERNS = [
    r"service_type['\"]?\s*[:=]",
    r"service_detail['\"]?\s*[:=]",
    r"family_status['\"]?\s*[:=]",
    r"org_name['\"]?\s*[:=]",
    r"currently searching for",
    r"location is set to",
    r"Context from our conversation",
    r"their location.*?:",
]


def _assert_no_service_data(prompt: str, context: str = ""):
    """Assert that a prompt string contains no user-specific service slot data.

    This is the core assertion used by every test in this suite.
    It checks for user-specific values (locations, org names, demographics)
    and slot-context formatting patterns that indicate structured slot
    data is being injected into LLM prompts.

    Note: Generic service category names ("shelter", "food") may appear
    in capability descriptions and guardrails — that's intentional.
    What's forbidden is user-specific search context.
    """
    lower = prompt.lower()

    # Check for forbidden values (user-specific service data)
    for val in _FORBIDDEN_VALUES:
        assert val.lower() not in lower, (
            f"SERVICE DATA LEAK in {context}: "
            f"found '{val}' in LLM prompt.\n"
            f"Prompt excerpt: ...{prompt[max(0, lower.index(val.lower())-50):lower.index(val.lower())+50+len(val)]}..."
        )

    # Check for slot context patterns — structured slot data injection
    for pattern in _SLOT_CONTEXT_PATTERNS:
        assert not re.search(pattern, prompt, re.IGNORECASE), (
            f"SERVICE SLOT PATTERN in {context}: "
            f"found pattern '{pattern}' in LLM prompt"
        )


@pytest.fixture
def sid():
    """Unique session ID with cleanup."""
    import uuid
    session_id = f"iso-{uuid.uuid4().hex[:8]}"
    clear_session(session_id)
    yield session_id
    clear_session(session_id)


@pytest.fixture
def sid_with_slots(sid):
    """Session pre-loaded with full service slot state."""
    save_session_slots(sid, dict(_FULL_SLOTS))
    return sid


# =====================================================================
# LAYER 1: PROMPT BUILDER ISOLATION
# =====================================================================

class TestPromptBuilderIsolation:
    """Prompt builders must never include service slot data in output,
    regardless of what slots are passed in."""

    def test_conversational_prompt_ignores_all_slots(self):
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("hey what's up", _FULL_SLOTS)
        _assert_no_service_data(prompt, "_build_conversational_prompt")

    def test_conversational_prompt_with_empty_slots(self):
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("tell me more", {})
        _assert_no_service_data(prompt, "_build_conversational_prompt(empty)")

    def test_conversational_prompt_with_none_slots(self):
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("hello", None)
        _assert_no_service_data(prompt, "_build_conversational_prompt(None)")

    def test_bot_question_prompt_ignores_service_slots(self):
        from app.services.responses import _build_bot_question_prompt
        prompt = _build_bot_question_prompt("how does this work?", _FULL_SLOTS)
        _assert_no_service_data(prompt, "_build_bot_question_prompt")

    def test_bot_question_prompt_allows_geolocation_boolean(self):
        """The only slot-derived data allowed is whether geolocation is active."""
        from app.services.responses import _build_bot_question_prompt
        prompt = _build_bot_question_prompt("can you find things near me?",
                                            {"_latitude": 40.7, "_longitude": -73.9})
        # "geolocation" is allowed; coordinates are NOT
        assert "40.7" not in prompt
        assert "-73.9" not in prompt

    def test_bot_question_prompt_with_no_slots(self):
        from app.services.responses import _build_bot_question_prompt
        prompt = _build_bot_question_prompt("what can you do?", {})
        _assert_no_service_data(prompt, "_build_bot_question_prompt(empty)")

    @pytest.mark.parametrize("service_type", [
        "food", "shelter", "clothing", "personal_care", "medical",
        "mental_health", "legal", "employment", "benefits",
        "other",
    ])
    def test_conversational_prompt_never_leaks_service_type_as_slot(self, service_type):
        """Service type must never appear in slot-context format."""
        from app.services.responses import _build_conversational_prompt
        slots = {"service_type": service_type, "location": "Midtown"}
        prompt = _build_conversational_prompt("what's going on", slots)
        # Category names may appear in guardrails ("do NOT give medical advice")
        # but must NEVER appear in slot-context format
        assert not re.search(
            rf"service.type.*?{re.escape(service_type)}", prompt, re.IGNORECASE
        ), f"service_type '{service_type}' appeared in slot-context format"
        # User-specific location must not appear
        assert "midtown" not in prompt.lower(), "Location 'Midtown' leaked"

    @pytest.mark.parametrize("location", [
        "Brooklyn", "Manhattan", "Queens", "Bronx", "Staten Island",
        "Harlem", "East Village", "Bushwick", "Times Square",
    ])
    def test_conversational_prompt_never_leaks_any_location(self, location):
        """Test with every common NYC location."""
        from app.services.responses import _build_conversational_prompt
        slots = {"service_type": "food", "location": location}
        prompt = _build_conversational_prompt("tell me about yourself", slots)
        assert location.lower() not in prompt.lower(), (
            f"location '{location}' leaked into conversational prompt"
        )


# =====================================================================
# LAYER 2: LLM CALL SITE CAPTURE
# =====================================================================

class TestLLMCallSiteCapture:
    """Mock claude_reply at every call site and capture the prompt.
    Verify no service data is passed regardless of session state."""

    def test_bot_question_prompt_captured(self, sid_with_slots):
        """Bot question handler should not pass service slots to Claude."""
        with patch("app.services.chatbot.handlers.meta.claude_reply",
                   return_value="I help find services.") as mock_llm, \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.bot_knowledge.answer_question", return_value=None):
            generate_reply("how does this thing work?", session_id=sid_with_slots)
            if mock_llm.called:
                prompt = mock_llm.call_args[0][0]
                _assert_no_service_data(prompt, "bot_question call site")

    def test_fallback_response_prompt_captured(self, sid_with_slots):
        """General fallback should not pass service slots to Claude."""
        with patch("app.services.responses.claude_reply",
                   return_value="I'm here to help!") as mock_llm, \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None):
            generate_reply("you know what I mean", session_id=sid_with_slots)
            if mock_llm.called:
                prompt = mock_llm.call_args[0][0]
                _assert_no_service_data(prompt, "_fallback_response call site")

    def test_no_slots_in_any_llm_call_during_full_flow(self, sid):
        """Track ALL claude_reply calls during a multi-turn flow."""
        captured_prompts = []

        def capturing_claude(prompt):
            captured_prompts.append(prompt)
            return "I understand."

        with patch("app.services.responses.claude_reply", side_effect=capturing_claude), \
             patch("app.services.chatbot.handlers.meta.claude_reply", side_effect=capturing_claude), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS):
            # Build up service state
            generate_reply("I need shelter in Brooklyn", session_id=sid)
            generate_reply("Yes, search", session_id=sid)
            # Now send a general message — LLM should fire
            generate_reply("tell me more about that", session_id=sid)

        for i, prompt in enumerate(captured_prompts):
            _assert_no_service_data(prompt, f"claude_reply call #{i+1}")

    def test_emotional_flow_does_not_leak_prior_search(self, sid):
        """After a completed search, emotional message should not
        leak prior search context to the LLM."""
        captured_prompts = []

        def capturing_claude(prompt):
            captured_prompts.append(prompt)
            return "I hear you."

        with patch("app.services.responses.claude_reply", side_effect=capturing_claude), \
             patch("app.services.chatbot.handlers.meta.claude_reply", side_effect=capturing_claude), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS):
            generate_reply("I need food in Manhattan", session_id=sid)
            generate_reply("Yes, search", session_id=sid)
            # Emotional message after search
            generate_reply("I'm feeling really down", session_id=sid)

        for i, prompt in enumerate(captured_prompts):
            _assert_no_service_data(prompt, f"emotional flow call #{i+1}")


# =====================================================================
# LAYER 3: SEARCH EXECUTION FIREWALL
# =====================================================================

class TestSearchExecutionFirewall:
    """The search execution path (_execute_and_respond) must NEVER
    call the LLM. All error paths use static messages."""

    def test_db_exception_uses_static_message(self, sid):
        """DB exception should return static text, not call Claude."""
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services",
                   side_effect=Exception("connection refused")), \
             patch("app.services.responses.claude_reply") as mock_llm:
            generate_reply("I need food in Brooklyn", session_id=sid)
            result = generate_reply("Yes, search", session_id=sid)
            mock_llm.assert_not_called()
            assert "yourpeer.nyc" in result["response"]

    def test_query_error_uses_static_message(self, sid):
        """Query error result should return static text, not call Claude."""
        error_results = {**MOCK_QUERY_RESULTS, "error": "timeout", "result_count": 0, "services": []}
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services", return_value=error_results), \
             patch("app.services.responses.claude_reply") as mock_llm:
            generate_reply("I need shelter in Queens", session_id=sid)
            result = generate_reply("Yes, search", session_id=sid)
            mock_llm.assert_not_called()
            assert "try again" in result["response"].lower() or "yourpeer" in result["response"]

    def test_none_response_uses_static_message(self, sid):
        """When bot_response is somehow None, should use static text."""
        # Force a scenario where result_count=0, no error, no exception
        empty_no_error = {
            "services": [], "result_count": 0,
            "template_used": "T", "params_applied": {},
            "relaxed": False, "execution_ms": 1,
        }
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services", return_value=empty_no_error), \
             patch("app.services.responses.claude_reply") as mock_llm:
            generate_reply("I need food in Brooklyn", session_id=sid)
            _result = generate_reply("Yes, search", session_id=sid)
            # Even if the no-results handler runs, Claude should NOT be called
            mock_llm.assert_not_called()

    def test_consecutive_db_failures_escalate_message(self, sid):
        """Repeated failures should give stronger message, never call LLM."""
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services",
                   side_effect=Exception("DB down")), \
             patch("app.services.responses.claude_reply") as mock_llm:
            generate_reply("I need food in Brooklyn", session_id=sid)
            generate_reply("Yes, search", session_id=sid)
            # Second attempt
            generate_reply("I need food in Brooklyn", session_id=sid)
            r2 = generate_reply("Yes, search", session_id=sid)
            mock_llm.assert_not_called()
            assert "still having trouble" in r2["response"].lower()

    def test_successful_search_never_calls_llm(self, sid):
        """A successful search should return DB results, never call Claude."""
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services",
                   return_value=MOCK_QUERY_RESULTS), \
             patch("app.services.responses.claude_reply") as mock_resp_llm, \
             patch("app.services.chatbot.handlers.meta.claude_reply") as mock_chat_llm:
            generate_reply("I need food in Brooklyn", session_id=sid)
            result = generate_reply("Yes, search", session_id=sid)
            mock_resp_llm.assert_not_called()
            mock_chat_llm.assert_not_called()
            assert result["result_count"] >= 1

    def test_no_results_never_calls_llm(self, sid):
        """Zero results should show a static 'no results' message, not LLM."""
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services",
                   return_value=MOCK_EMPTY_RESULTS), \
             patch("app.services.responses.claude_reply") as mock_llm:
            generate_reply("I need food in Brooklyn", session_id=sid)
            _result = generate_reply("Yes, search", session_id=sid)
            mock_llm.assert_not_called()


# =====================================================================
# LAYER 4: STRUCTURED LLM OUTPUTS
# =====================================================================

class TestStructuredLLMOutputs:
    """LLM calls for classification, crisis detection, and slot extraction
    return structured data only — never user-facing service text."""

    def test_crisis_response_is_hardcoded(self):
        """Crisis response text comes from a dict, not the LLM."""
        from app.services.crisis_detector import (
            _LLM_CATEGORY_RESPONSES, _FAILOPEN_RESPONSE,
        )
        # Every response must be a hardcoded string, not None
        for category, response in _LLM_CATEGORY_RESPONSES.items():
            assert isinstance(response, str), f"Category '{category}' has non-string response"
            assert len(response) > 20, f"Category '{category}' response too short to be useful"
        assert isinstance(_FAILOPEN_RESPONSE, str)

    def test_crisis_llm_returns_json_not_text(self):
        """The crisis LLM call should return structured JSON that gets
        parsed — the raw LLM text is never shown to the user."""
        from app.services.crisis_detector import (
            _detect_crisis_llm, _LLM_CATEGORY_RESPONSES, _FAILOPEN_RESPONSE,
        )
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"crisis": true, "category": "safety_concern"}')]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("app.services.crisis_detector.get_client", return_value=mock_client):
            result = _detect_crisis_llm("I need help right now")
        # Result should be a tuple (category, hardcoded_response), not raw LLM text
        if result:
            category, response_text = result
            assert category in ("safety_concern", "suicide_self_harm",
                                "domestic_violence", "youth_runaway",
                                "assault_victim", "trafficking",
                                "medical_emergency")
            # The response text must come from the hardcoded dict
            assert response_text == _LLM_CATEGORY_RESPONSES.get(category, _FAILOPEN_RESPONSE)

    def test_classifier_returns_structured_data_only(self):
        """Unified extractor returns slot/tone/action — never user-facing text.

        Phase 4 Stage 3 (April 2026): the legacy `classify_unified` was
        deleted. The unified replacement is `slot_extraction.extract`,
        which returns a 15-field dict (12 slot fields + tone + action +
        Trust Model 5 internals). The firewall property is the same:
        no `"response"` key, no user-facing text.
        """
        from app.services.slot_extraction import extract
        from app.services.slot_extractor import extract_slots as regex_extract
        # Mock the inner LLM call; result must be a dict, never text.
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "extract_intake_slots"
        mock_block.input = {
            "service_type": "shelter",
            "location": "Brooklyn",
            "tone": "urgent",
        }
        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("app.llm.claude_client.get_client", return_value=mock_client):
            regex_result = regex_extract("I need shelter in Brooklyn")
            result = extract(
                "I need shelter in Brooklyn",
                regex_result,
                api_key_available=True,
            )
        assert isinstance(result, dict)
        assert "response" not in result  # No user-facing text generated

    def test_slot_extractor_uses_tool_use_not_text(self):
        """LLM slot extractor uses Anthropic's tool_use API — the LLM
        never generates free text, only structured tool call arguments.

        Phase 4 Stage 3 (April 2026): the legacy `extract_slots_llm`
        was deleted. The unified replacement is
        `slot_extraction.dispatch.extract_slots_short` (short path);
        same tool_use contract.
        """
        from app.services.slot_extraction.dispatch import extract_slots_short
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "extract_intake_slots"
        mock_block.input = {
            "service_type": "food",
            "location": "Manhattan",
            "age": None,
            "urgency": None,
        }
        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=mock_client,
        ):
            result = extract_slots_short("I need food in Manhattan")
        # Result is structured slot data, not user-facing text
        assert isinstance(result, dict)
        assert result.get("service_type") == "food"
        assert "response" not in result


# =====================================================================
# LAYER 5: END-TO-END CONVERSATION FLOWS
# =====================================================================

class TestEndToEndIsolation:
    """Full conversation flows where the LLM is invoked. Every prompt
    captured from claude_reply must be free of service data."""

    def _run_flow_and_capture(self, messages, sid):
        """Send messages through generate_reply, capture all LLM prompts."""
        captured = []

        def capture(prompt):
            captured.append(prompt)
            return "I understand, I'm here for you."

        with patch("app.services.responses.claude_reply", side_effect=capture), \
             patch("app.services.chatbot.handlers.meta.claude_reply", side_effect=capture), \
             patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services", return_value=MOCK_QUERY_RESULTS):
            for msg in messages:
                generate_reply(msg, session_id=sid)

        return captured

    def test_general_after_search(self, sid):
        """General message after completed search must not leak search context."""
        prompts = self._run_flow_and_capture([
            "I need food in Brooklyn",
            "Yes, search",
            "tell me something interesting",
        ], sid)
        for i, p in enumerate(prompts):
            _assert_no_service_data(p, f"general_after_search prompt #{i+1}")

    def test_bot_question_during_search(self, sid):
        """Bot question mid-flow must not reveal active search."""
        prompts = self._run_flow_and_capture([
            "I need shelter in Manhattan",
            "how does this work?",
        ], sid)
        for i, p in enumerate(prompts):
            _assert_no_service_data(p, f"bot_question_mid_flow prompt #{i+1}")

    def test_multiple_searches_then_general(self, sid):
        """After multiple completed searches, general message is clean."""
        prompts = self._run_flow_and_capture([
            "I need food in Brooklyn",
            "Yes, search",
            "I also need shelter in Queens",
            "Yes, search",
            "what's going on",
        ], sid)
        for i, p in enumerate(prompts):
            _assert_no_service_data(p, f"multi_search_then_general prompt #{i+1}")

    def test_emotional_with_active_slots(self, sid):
        """Emotional response with populated service slots."""
        prompts = self._run_flow_and_capture([
            "I need shelter in the Bronx",
            "I'm so scared right now",
        ], sid)
        for i, p in enumerate(prompts):
            _assert_no_service_data(p, f"emotional_with_slots prompt #{i+1}")

    def test_crisis_adjacent_with_slots(self, sid):
        """Crisis-adjacent message with full slot state."""
        prompts = self._run_flow_and_capture([
            "I need shelter in East Harlem",
            "I don't know what to do anymore",
        ], sid)
        for i, p in enumerate(prompts):
            _assert_no_service_data(p, f"crisis_adjacent prompt #{i+1}")


# =====================================================================
# LAYER 6: SYSTEM PROMPT AUDIT
# =====================================================================

class TestSystemPromptAudit:
    """All system prompts used in LLM API calls must contain
    appropriate guardrails and never reference specific services."""

    def test_conversational_prompt_has_no_fabrication_rule(self):
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("test", {})
        lower = prompt.lower()
        assert "do not make up" in lower or "do not say whether" in lower

    def test_conversational_prompt_has_no_followup_rule(self):
        """The prompt must explicitly prohibit follow-up questions
        about service types — this was the root cause of the
        confirmation loop bug."""
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("test", {})
        lower = prompt.lower()
        assert "follow-up question" in lower or "follow up question" in lower

    def test_conversational_prompt_has_no_availability_rule(self):
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("test", {})
        lower = prompt.lower()
        assert "no access to the service database" in lower

    def test_crisis_system_prompt_has_no_user_data(self):
        """Crisis system prompt should contain crisis categories (its job)
        but never user-specific data like addresses or phone numbers."""
        from app.services.crisis_detector import _LLM_SYSTEM_PROMPT
        # Crisis categories like "domestic violence" are INTENTIONAL —
        # the classifier needs them. But no user-specific data.
        assert "212-" not in _LLM_SYSTEM_PROMPT  # no phone numbers
        assert "https://yourpeer" not in _LLM_SYSTEM_PROMPT  # no URLs
        assert "service_type" not in _LLM_SYSTEM_PROMPT  # no slot keys

    def test_classifier_system_prompt_has_no_service_data(self):
        """Phase 4 Stage 3: the legacy single `_UNIFIED_SYSTEM_PROMPT`
        was split into `_SHORT_SYSTEM_PROMPT` (used by short-message
        path) and `_NARRATIVE_SYSTEM_PROMPT` (used by long-message
        path). The firewall property is the same — neither prompt may
        contain user-specific data — so we audit both.

        The classifier's job is to map messages to enum values; the
        prompts may legitimately list valid `service_type` ENUM values
        (food, shelter, etc.) as classification targets. They must
        not contain specific service NAMES, addresses, or phone numbers.
        """
        from app.services.slot_extraction.prompts import (
            _NARRATIVE_SYSTEM_PROMPT,
            _SHORT_SYSTEM_PROMPT,
        )
        for prompt_name, prompt_text in (
            ("_SHORT_SYSTEM_PROMPT", _SHORT_SYSTEM_PROMPT),
            ("_NARRATIVE_SYSTEM_PROMPT", _NARRATIVE_SYSTEM_PROMPT),
        ):
            assert "212-" not in prompt_text, (
                f"{prompt_name} contains a phone number"
            )
            assert "https://yourpeer" not in prompt_text, (
                f"{prompt_name} contains a YourPeer URL"
            )

    def test_slot_extractor_system_prompt_has_no_service_data(self):
        """Phase 4 Stage 3: legacy `_SYSTEM_PROMPT` from
        `llm_slot_extractor` is now split into `_SHORT_SYSTEM_PROMPT`
        and `_NARRATIVE_SYSTEM_PROMPT` in `slot_extraction.prompts`.
        Audit both for the same firewall property.
        """
        from app.services.slot_extraction.prompts import (
            _NARRATIVE_SYSTEM_PROMPT,
            _SHORT_SYSTEM_PROMPT,
        )
        for prompt_name, prompt_text in (
            ("_SHORT_SYSTEM_PROMPT", _SHORT_SYSTEM_PROMPT),
            ("_NARRATIVE_SYSTEM_PROMPT", _NARRATIVE_SYSTEM_PROMPT),
        ):
            assert "212-" not in prompt_text, (
                f"{prompt_name} contains a phone number"
            )
            assert "https://yourpeer" not in prompt_text, (
                f"{prompt_name} contains a YourPeer URL"
            )


# =====================================================================
# LAYER 7: REGRESSION GUARDS
# =====================================================================

class TestRegressionGuards:
    """Specific scenarios that previously leaked service data to the LLM
    or could be reintroduced by future changes."""

    def test_db_failure_does_not_generate_service_followup(self, sid):
        """REGRESSION: DB failure previously called _fallback_response which
        sent slots to Claude. Claude generated 'To help narrow things down,
        are you looking for clothes to wear?' — mimicking the intake flow
        and trapping the user in a confirmation loop. (Tester feedback 2025-04-13)"""
        with patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None), \
             patch("app.services.chatbot.execution.query_services",
                   side_effect=Exception("DB down")):
            generate_reply("I need clothing in Brooklyn", session_id=sid)
            result = generate_reply("Yes, search", session_id=sid)
        response = result["response"].lower()
        # Must NOT contain service-specific follow-ups
        assert "narrow" not in response
        assert "clothing care" not in response
        assert "laundry" not in response
        # Must contain clear error message
        assert "yourpeer.nyc" in response or "try again" in response

    def test_slot_context_pattern_removed_from_prompts(self):
        """REGRESSION: Prompts previously included
        'Context from our conversation so far: {service_type: shelter, ...}'"""
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("hello", _FULL_SLOTS)
        assert "context from our conversation" not in prompt.lower()

    def test_steer_instruction_removed(self):
        """REGRESSION: Prompts previously included
        'The user has already expressed a service need. Gently remind them...'
        which steered Claude toward service-related responses."""
        from app.services.responses import _build_conversational_prompt
        prompt = _build_conversational_prompt("hey", _FULL_SLOTS)
        assert "service need" not in prompt.lower()
        assert "continue their search" not in prompt.lower()

    def test_execute_and_respond_has_no_fallback_response_call(self):
        """REGRESSION: _execute_and_respond previously called
        _fallback_response (LLM) on DB failures. Verify via source inspection."""
        import inspect
        from app.services.chatbot import _execute_and_respond
        source = inspect.getsource(_execute_and_respond)
        # Strip comments and docstrings to check only executable code
        code_lines = []
        in_docstring = False
        for line in source.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            code_lines.append(line)
        code = "\n".join(code_lines)
        assert "_fallback_response(" not in code, (
            "_execute_and_respond must not call _fallback_response — "
            "DB failures must use static messages only"
        )

    def test_build_conversational_prompt_signature_accepts_but_ignores_slots(self):
        """The function signature still accepts slots for backward compat,
        but the implementation must not use them."""
        import inspect
        from app.services.responses import _build_conversational_prompt
        source = inspect.getsource(_build_conversational_prompt)
        # Strip the def line, docstrings, and comments — check only executable code
        lines = source.split("\n")
        code_lines = []
        in_docstring = False
        for i, line in enumerate(lines):
            if i == 0:  # skip def line
                continue
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                # Toggle docstring state. If line has opening AND closing, skip it
                count = stripped.count('"""') + stripped.count("'''")
                if count == 2:  # single-line docstring
                    continue
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            code_lines.append(stripped)
        code = "\n".join(code_lines)
        # The executable code should not reference 'slots' at all
        assert "slots" not in code, (
            "_build_conversational_prompt body references 'slots' — "
            "it should ignore the slots parameter entirely"
        )

    def test_build_bot_question_prompt_only_reads_latitude(self):
        """The bot question prompt should only access _latitude from slots."""
        import inspect
        from app.services.responses import _build_bot_question_prompt
        source = inspect.getsource(_build_bot_question_prompt)
        body = "\n".join(source.split("\n")[1:])
        # Find all slots.get(...) calls
        slot_accesses = re.findall(r'slots\.get\(["\']([^"\']+)', body)
        allowed = {"_latitude"}
        for key in slot_accesses:
            assert key in allowed, (
                f"_build_bot_question_prompt accesses slots['{key}'] — "
                f"only {allowed} is permitted"
            )
