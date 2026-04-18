"""
Tests for PR 4: LLM multi-service extraction (Option B).

Append these to tests/test_llm_slot_extractor.py, or run standalone.
Uses the same mock pattern as existing LLM extractor tests.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Ensure backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ---------------------------------------------------------------------------
# MOCK HELPERS
# ---------------------------------------------------------------------------

def _mock_tool_response(tool_input: dict):
    """Create a mock Anthropic response with a tool_use block."""
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "extract_intake_slots"
    mock_block.input = tool_input

    mock_response = MagicMock()
    mock_response.content = [mock_block]
    return mock_response


def _reset_client():
    """Reset the cached Anthropic client so each test is independent."""
    from app.llm import claude_client as cc
    cc._client = None
    cc._init_error = None


# ---------------------------------------------------------------------------
# extract_slots_llm — ADDITIONAL SERVICE TYPES
# ---------------------------------------------------------------------------

@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_llm_extracts_additional_service_types(mock_anthropic):
    """LLM returns additional_service_types for multi-service requests."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "food",
        "location": "Brooklyn",
        "additional_service_types": ["shelter"],
    })

    from app.services.llm_slot_extractor import extract_slots_llm
    result = extract_slots_llm(
        "I need somewhere to eat and a place to crash in Brooklyn"
    )
    assert result["service_type"] == "food"
    assert result["additional_service_types"] == ["shelter"]


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_llm_additional_empty_for_single_service(mock_anthropic):
    """Single-service request returns empty additional list."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "food",
        "location": "Brooklyn",
    })

    from app.services.llm_slot_extractor import extract_slots_llm
    result = extract_slots_llm("I need food in Brooklyn")
    assert result["additional_service_types"] == []


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_llm_additional_multiple(mock_anthropic):
    """LLM can return multiple additional services."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "shelter",
        "location": "Manhattan",
        "additional_service_types": ["food", "mental_health"],
    })

    from app.services.llm_slot_extractor import extract_slots_llm
    result = extract_slots_llm(
        "I need a bed, something to eat, and someone to talk to in Manhattan"
    )
    assert result["service_type"] == "shelter"
    assert result["additional_service_types"] == ["food", "mental_health"]


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_llm_additional_null_becomes_empty_list(mock_anthropic):
    """If LLM returns null for additional_service_types, it becomes []."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "food",
        "location": "Brooklyn",
        "additional_service_types": None,
    })

    from app.services.llm_slot_extractor import extract_slots_llm
    result = extract_slots_llm("I need food in Brooklyn")
    assert result["additional_service_types"] == []


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_llm_failure_returns_empty_additional(mock_anthropic):
    """If LLM call fails, additional_service_types is [] in fallback."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API timeout")

    from app.services.llm_slot_extractor import extract_slots_llm
    result = extract_slots_llm("I need food and shelter")
    assert result["additional_service_types"] == []
    assert result["service_type"] is None


# ---------------------------------------------------------------------------
# extract_slots_smart — MULTI-SERVICE MERGE
# ---------------------------------------------------------------------------

@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_llm_adds_service_regex_missed(mock_anthropic):
    """LLM detects a service that regex missed (indirect phrasing).

    "a place to crash" doesn't match any shelter keyword in regex,
    but the LLM understands it means shelter. Under Housing First
    (Feature B, April 2026), shelter (tier 1) is the expected
    primary; the LLM prompt encodes the same urgency hierarchy.
    """
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "shelter",
        "location": "Brooklyn",
        "additional_service_types": ["food"],
    })

    from app.services.llm_slot_extractor import extract_slots_smart
    result = extract_slots_smart(
        "I need food and a place to crash in Brooklyn"
    )
    assert result["service_type"] == "shelter"
    additional_types = [svc for svc, *_ in result.get("additional_services", [])]
    assert "food" in additional_types


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_merges_llm_and_regex_additional(mock_anthropic):
    """Both regex and LLM find additional services — combined without dupes.

    Under Housing First: regex detects shelter primary + food additional;
    LLM detects shelter primary + food + mental_health additional.
    Result: shelter primary, additional = [food, mental_health].
    """
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "shelter",
        "location": "Brooklyn",
        "additional_service_types": ["food", "mental_health"],
    })

    from app.services.llm_slot_extractor import extract_slots_smart
    result = extract_slots_smart(
        "I need food and shelter and someone to talk to in Brooklyn"
    )
    assert result["service_type"] == "shelter"
    additional_types = [svc for svc, *_ in result.get("additional_services", [])]
    assert "food" in additional_types
    assert "mental_health" in additional_types
    # No duplicates
    assert len(additional_types) == len(set(additional_types))


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_no_duplicate_primary_in_additional(mock_anthropic):
    """Primary service_type should not appear in additional_services.

    Housing First: shelter is primary, food additional. Even if the LLM
    includes its own primary (shelter) in additional_service_types
    redundantly, the merge should exclude it.
    """
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "shelter",
        "location": "Brooklyn",
        "additional_service_types": ["shelter", "food"],  # LLM includes primary
    })

    from app.services.llm_slot_extractor import extract_slots_smart
    result = extract_slots_smart("I need food and shelter in Brooklyn")
    additional_types = [svc for svc, *_ in result.get("additional_services", [])]
    assert "shelter" not in additional_types  # primary excluded
    assert "food" in additional_types


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_additional_service_types_key_removed(mock_anthropic):
    """The raw LLM key 'additional_service_types' should not leak into
    the final result — downstream code uses 'additional_services'."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_tool_response({
        "service_type": "food",
        "location": "Brooklyn",
        "additional_service_types": ["shelter"],
    })

    from app.services.llm_slot_extractor import extract_slots_smart
    result = extract_slots_smart("I need food and shelter in Brooklyn")
    assert "additional_service_types" not in result
    assert "additional_services" in result


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_simple_message_skips_llm(mock_anthropic):
    """Simple messages should skip LLM entirely — no additional_service_types
    processing needed, regex additional_services pass through as-is."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    from app.services.llm_slot_extractor import extract_slots_smart
    # "I need food in Brooklyn" is short + has service + has known location
    # → classified as simple → regex only, no LLM call
    result = extract_slots_smart("I need food in Brooklyn")
    assert result["service_type"] == "food"
    # LLM was never called
    mock_client.messages.create.assert_not_called()


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_smart_llm_failure_preserves_regex_additional(mock_anthropic):
    """If the LLM fails, regex additional_services should still be present."""
    _reset_client()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API timeout")

    from app.services.llm_slot_extractor import extract_slots_smart
    # Long enough to trigger LLM path, and has two services for regex
    result = extract_slots_smart(
        "I just got released and I need food and shelter in East New York "
        "because my friend can't keep me anymore"
    )
    # LLM failed → regex fallback. Regex should still have additional_services
    assert result["service_type"] in ("food", "shelter")
    # The other service should be in additional_services from regex
    additional_types = [svc for svc, *_ in result.get("additional_services", [])]
    assert len(additional_types) >= 1
