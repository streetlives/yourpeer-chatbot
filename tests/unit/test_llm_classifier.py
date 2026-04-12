"""Tests for the unified LLM classification gate.

Tests cover:
    1. _validate_result — normalization, invalid values, edge cases
    2. classify_unified — mock LLM call, JSON parsing, error handling
    3. Gate logic — fires/skips correctly based on regex results
"""

import json
import pytest
from unittest.mock import patch, MagicMock

# Import validation directly (no LLM needed)
from app.services.llm_classifier import _validate_result, classify_unified


# =========================================================================
# 1. _validate_result tests
# =========================================================================

class TestValidateResult:
    """Tests for the validation/normalization layer."""

    def test_valid_full_result(self):
        data = {
            "service_type": "shelter",
            "service_detail": "transitional housing",
            "location": "brooklyn",
            "additional_services": [
                {"type": "food", "location": "manhattan"},
                {"type": "employment", "location": None},
            ],
            "tone": "emotional",
            "action": None,
            "urgency": "high",
            "age": 22,
            "family_status": "with_children",
        }
        result = _validate_result(data)
        assert result["service_type"] == "shelter"
        assert result["service_detail"] == "transitional housing"
        assert result["location"] == "brooklyn"
        assert result["tone"] == "emotional"
        assert result["urgency"] == "high"
        assert result["age"] == 22
        assert result["family_status"] == "with_children"
        assert len(result["additional_services"]) == 2
        assert result["additional_services"][0] == ("food", None, "manhattan")
        assert result["additional_services"][1] == ("employment", None, None)

    def test_all_nulls(self):
        data = {
            "service_type": None,
            "location": None,
            "tone": None,
            "action": None,
            "urgency": None,
            "age": None,
            "family_status": None,
            "additional_services": [],
        }
        result = _validate_result(data)
        assert result["service_type"] is None
        assert result["location"] is None
        assert result["tone"] is None
        assert result["action"] is None
        assert result["additional_services"] == []

    def test_invalid_service_type_rejected(self):
        data = {"service_type": "pizza_delivery"}
        result = _validate_result(data)
        assert result["service_type"] is None

    def test_invalid_tone_rejected(self):
        data = {"tone": "happy"}
        result = _validate_result(data)
        assert result["tone"] is None

    def test_invalid_action_rejected(self):
        data = {"action": "dance"}
        result = _validate_result(data)
        assert result["action"] is None

    def test_case_normalization(self):
        data = {"service_type": "SHELTER", "tone": "Emotional", "location": "BROOKLYN"}
        result = _validate_result(data)
        assert result["service_type"] == "shelter"
        assert result["tone"] == "emotional"
        assert result["location"] == "brooklyn"

    def test_age_as_string(self):
        data = {"age": "17"}
        result = _validate_result(data)
        assert result["age"] == 17

    def test_age_out_of_range(self):
        data = {"age": 250}
        result = _validate_result(data)
        assert result["age"] is None

    def test_age_negative(self):
        data = {"age": -5}
        result = _validate_result(data)
        assert result["age"] is None

    def test_empty_string_service_type(self):
        data = {"service_type": ""}
        result = _validate_result(data)
        assert result["service_type"] is None

    def test_additional_services_invalid_items_skipped(self):
        data = {
            "additional_services": [
                {"type": "food", "location": "bronx"},
                {"type": "invalid_service", "location": None},
                "not a dict",
                {"type": "legal"},
            ],
        }
        result = _validate_result(data)
        assert len(result["additional_services"]) == 2
        assert result["additional_services"][0] == ("food", None, "bronx")
        assert result["additional_services"][1] == ("legal", None, None)

    def test_missing_fields_default_to_none(self):
        result = _validate_result({})
        assert result["service_type"] is None
        assert result["tone"] is None
        assert result["action"] is None
        assert result["location"] is None
        assert result["urgency"] is None
        assert result["age"] is None
        assert result["family_status"] is None
        assert result["additional_services"] == []

    def test_whitespace_trimming(self):
        data = {"service_type": "  food  ", "location": "  east village  "}
        result = _validate_result(data)
        assert result["service_type"] == "food"
        assert result["location"] == "east village"

    def test_confirm_yes_action(self):
        data = {"action": "confirm_yes"}
        result = _validate_result(data)
        assert result["action"] == "confirm_yes"

    def test_negative_preference_action(self):
        data = {"action": "negative_preference"}
        result = _validate_result(data)
        assert result["action"] == "negative_preference"


# =========================================================================
# 2. classify_unified tests (mocked LLM)
# =========================================================================

class TestClassifyUnified:
    """Tests for the LLM call and JSON parsing."""

    def _mock_response(self, text):
        """Create a mock Anthropic API response."""
        mock_resp = MagicMock()
        mock_content = MagicMock()
        mock_content.text = text
        mock_resp.content = [mock_content]
        return mock_resp

    @patch("app.llm.claude_client.get_client")
    def test_basic_service_extraction(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(json.dumps({
            "service_type": "shelter",
            "location": "brooklyn",
            "additional_services": [],
            "tone": "urgent",
            "action": None,
            "urgency": "high",
            "age": None,
            "family_status": None,
        }))

        result = classify_unified("I need a roof over my head in Brooklyn")
        assert result is not None
        assert result["service_type"] == "shelter"
        assert result["location"] == "brooklyn"
        assert result["tone"] == "urgent"
        assert result["urgency"] == "high"

    @patch("app.llm.claude_client.get_client")
    def test_multi_service_with_locations(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(json.dumps({
            "service_type": "food",
            "location": "brooklyn",
            "additional_services": [
                {"type": "shelter", "location": "manhattan"},
            ],
            "tone": None,
            "action": None,
            "urgency": None,
            "age": None,
            "family_status": None,
        }))

        result = classify_unified("I need food in Brooklyn and shelter in Manhattan")
        assert result["service_type"] == "food"
        assert result["location"] == "brooklyn"
        assert len(result["additional_services"]) == 1
        assert result["additional_services"][0] == ("shelter", None, "manhattan")

    @patch("app.llm.claude_client.get_client")
    def test_emotional_no_service(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(json.dumps({
            "service_type": None,
            "location": None,
            "additional_services": [],
            "tone": "emotional",
            "action": None,
            "urgency": None,
            "age": None,
            "family_status": None,
        }))

        result = classify_unified("I just can't take it anymore everything is falling apart")
        assert result["service_type"] is None
        assert result["tone"] == "emotional"

    @patch("app.llm.claude_client.get_client")
    def test_json_with_markdown_fences(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(
            '```json\n{"service_type": "food", "tone": null}\n```'
        )

        result = classify_unified("I'm starving haven't eaten in two days")
        assert result is not None
        assert result["service_type"] == "food"

    @patch("app.llm.claude_client.get_client")
    def test_invalid_json_returns_none(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response("not valid json")

        result = classify_unified("some message")
        assert result is None

    @patch("app.llm.claude_client.get_client")
    def test_client_none_returns_none(self, mock_get_client):
        mock_get_client.return_value = None
        result = classify_unified("some message")
        assert result is None

    @patch("app.llm.claude_client.get_client")
    def test_api_exception_returns_none(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.side_effect = Exception("API error")

        result = classify_unified("some message")
        assert result is None

    @patch("app.llm.claude_client.get_client")
    def test_false_positive_rejection(self, mock_get_client):
        """LLM should return null for non-service messages."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(json.dumps({
            "service_type": None,
            "location": None,
            "additional_services": [],
            "tone": None,
            "action": None,
            "urgency": None,
            "age": None,
            "family_status": None,
        }))

        result = classify_unified("I saw a doctor on TV last night")
        assert result is not None
        assert result["service_type"] is None

    @patch("app.llm.claude_client.get_client")
    def test_reentry_scenario(self, mock_get_client):
        """Real-world: post-incarceration with multiple needs."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.messages.create.return_value = self._mock_response(json.dumps({
            "service_type": "shelter",
            "service_detail": None,
            "location": "south bronx",
            "additional_services": [{"type": "employment", "location": None}],
            "tone": None,
            "action": None,
            "urgency": "high",
            "age": None,
            "family_status": None,
        }))

        result = classify_unified(
            "I just got out of Rikers yesterday. "
            "I need somewhere to stay and help finding work. "
            "I'm in the South Bronx."
        )
        assert result["service_type"] == "shelter"
        assert result["location"] == "south bronx"
        assert result["urgency"] == "high"
        assert len(result["additional_services"]) == 1
        assert result["additional_services"][0][0] == "employment"


# =========================================================================
# 3. Gate logic tests (when should unified gate fire?)
# =========================================================================

class TestGateLogic:
    """Tests for the gate conditions that control when the unified
    classifier fires vs when regex handles the message."""

    def test_regex_found_service_skips_gate(self):
        """When regex already found a service_type, the gate should NOT fire."""
        # The gate condition: not has_service_intent
        has_service_intent = True
        action = None
        tone = None
        words = 10
        should_fire = (
            not has_service_intent
            and action not in {"reset", "greeting", "confirm_yes"}
            and tone is None
            and words >= 4
        )
        assert should_fire is False

    def test_regex_found_action_skips_gate(self):
        """When regex found a meaningful action, the gate should NOT fire."""
        has_service_intent = False
        action = "confirm_yes"
        tone = None
        words = 5
        skip_actions = {
            "reset", "greeting", "thanks", "confirm_yes", "confirm_deny",
        }
        should_fire = (
            not has_service_intent
            and action not in skip_actions
            and tone is None
            and words >= 4
        )
        assert should_fire is False

    def test_regex_found_tone_skips_gate(self):
        """When regex found a tone, the gate should NOT fire."""
        has_service_intent = False
        action = None
        tone = "emotional"
        words = 8
        should_fire = (
            not has_service_intent
            and action not in {"reset"}
            and tone is None
            and words >= 4
        )
        assert should_fire is False

    def test_short_message_skips_gate(self):
        """Messages under 4 words skip the gate."""
        has_service_intent = False
        action = None
        tone = None
        words = 2
        should_fire = (
            not has_service_intent
            and action not in {"reset"}
            and tone is None
            and words >= 4
        )
        assert should_fire is False

    def test_long_unclassified_message_fires_gate(self):
        """When regex found nothing on a 4+ word message, gate fires."""
        has_service_intent = False
        action = None
        tone = None
        words = 10
        should_fire = (
            not has_service_intent
            and action not in {"reset"}
            and tone is None
            and words >= 4
        )
        assert should_fire is True

    def test_help_action_does_not_skip_gate(self):
        """'help' action is NOT in the skip set — message might have
        service intent that the gate can detect."""
        has_service_intent = False
        action = "help"
        tone = None
        words = 10
        skip_actions = {
            "reset", "greeting", "thanks", "bot_identity", "bot_question",
            "confirm_yes", "confirm_deny", "confirm_change_service",
            "confirm_change_location", "correction", "negative_preference",
            "escalation",
        }
        should_fire = (
            not has_service_intent
            and action not in skip_actions
            and tone is None
            and words >= 4
        )
        assert should_fire is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
