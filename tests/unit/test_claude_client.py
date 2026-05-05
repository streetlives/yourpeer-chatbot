"""
Tests for the Claude LLM client — shared Anthropic client initialization,
error caching, model constants, claude_reply success/failure paths,
and get_client reuse.

All external calls (anthropic SDK) are mocked so tests run without
an API key or network access.

Run with: python -m pytest tests/test_claude_client.py -v
Or just:  python tests/test_claude_client.py
"""

import os
from unittest.mock import patch, MagicMock

import pytest

import app.llm.claude_client as cc


def _reset_globals():
    """Reset the module's lazy-init state between tests."""
    cc._client = None
    cc._init_error = None


# -----------------------------------------------------------------------
# MODEL CONSTANTS
# -----------------------------------------------------------------------

def test_model_constants_are_set():
    """Model constants should be defined and non-empty."""
    assert cc.CONVERSATIONAL_MODEL, "CONVERSATIONAL_MODEL should be set"
    assert cc.SLOT_EXTRACTION_MODEL, "SLOT_EXTRACTION_MODEL should be set"
    assert cc.CRISIS_DETECTION_MODEL, "CRISIS_DETECTION_MODEL should be set"


def test_recommended_model_assignments():
    """Model assignments should match the recommended config from model analysis.

    Conversational + slots → Haiku (speed > reasoning depth)
    Crisis detection → Sonnet (safety-critical, needs nuance)
    """
    assert "haiku" in cc.CONVERSATIONAL_MODEL.lower(), \
        f"Conversational should use Haiku, got {cc.CONVERSATIONAL_MODEL}"
    assert "haiku" in cc.SLOT_EXTRACTION_MODEL.lower(), \
        f"Slot extraction should use Haiku, got {cc.SLOT_EXTRACTION_MODEL}"
    assert "sonnet" in cc.CRISIS_DETECTION_MODEL.lower(), \
        f"Crisis detection should use Sonnet, got {cc.CRISIS_DETECTION_MODEL}"


# -----------------------------------------------------------------------
# LAZY INITIALIZATION
# -----------------------------------------------------------------------

@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_lazy_init_creates_client(mock_anthropic):
    """First call to get_client should create the Anthropic client."""
    _reset_globals()
    mock_anthropic.Anthropic.return_value = MagicMock()

    client = cc.get_client()
    assert client is not None
    mock_anthropic.Anthropic.assert_called_once_with(api_key="fake-key", timeout=10.0)


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_lazy_init_reuses_client(mock_anthropic):
    """Subsequent calls should reuse the cached client, not create a new one."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    client1 = cc.get_client()
    client2 = cc.get_client()

    assert client1 is client2
    mock_anthropic.Anthropic.assert_called_once()  # only called once


# -----------------------------------------------------------------------
# MISSING ENV VARS
# -----------------------------------------------------------------------

@patch.dict(os.environ, {}, clear=True)
def test_missing_api_key_raises():
    """Missing ANTHROPIC_API_KEY should raise RuntimeError."""
    _reset_globals()
    try:
        cc.get_client()
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


# -----------------------------------------------------------------------
# ERROR CACHING
# -----------------------------------------------------------------------

@patch.dict(os.environ, {}, clear=True)
def test_init_error_cached():
    """Once init fails, subsequent calls should raise immediately
    without retrying the initialization."""
    _reset_globals()

    # First call — fails and caches the error
    try:
        cc.get_client()
    except RuntimeError:
        pass

    assert cc._init_error is not None

    # Second call — should raise the SAME cached error immediately
    try:
        cc.get_client()
        assert False, "Should have raised cached error"
    except RuntimeError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_client_creation_failure_cached(mock_anthropic):
    """If anthropic.Anthropic() throws, the error should be cached."""
    _reset_globals()
    mock_anthropic.Anthropic.side_effect = Exception("Network error")

    try:
        cc.get_client()
    except RuntimeError as e:
        assert "Failed to initialize" in str(e)

    # Second call should raise cached error without calling Anthropic again
    mock_anthropic.Anthropic.reset_mock()
    try:
        cc.get_client()
    except RuntimeError:
        pass
    mock_anthropic.Anthropic.assert_not_called()


# -----------------------------------------------------------------------
# CLAUDE_REPLY — SUCCESS
# -----------------------------------------------------------------------

@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_claude_reply_success(mock_anthropic):
    """Successful API call should return the response text."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    mock_text_block = MagicMock()
    mock_text_block.text = "Here are some food options."
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    result = cc.claude_reply("Find food in Brooklyn")

    assert result == "Here are some food options."
    mock_client.messages.create.assert_called_once()

    # Verify model used is CONVERSATIONAL_MODEL (Haiku)
    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs[1]["model"] == cc.CONVERSATIONAL_MODEL


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_claude_reply_uses_haiku(mock_anthropic):
    """claude_reply should use CONVERSATIONAL_MODEL (Haiku), not Sonnet."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    mock_text_block = MagicMock()
    mock_text_block.text = "response"
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    cc.claude_reply("test")

    call_kwargs = mock_client.messages.create.call_args
    model_used = call_kwargs[1]["model"]
    assert "haiku" in model_used.lower(), \
        f"claude_reply should use Haiku for speed, got {model_used}"


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_claude_reply_max_tokens_bounded(mock_anthropic):
    """claude_reply max_tokens should be small (conversational = 1-3 sentences)."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    mock_text_block = MagicMock()
    mock_text_block.text = "response"
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    cc.claude_reply("test")

    call_kwargs = mock_client.messages.create.call_args
    max_tokens = call_kwargs[1]["max_tokens"]
    assert max_tokens <= 200, \
        f"Conversational max_tokens should be small, got {max_tokens}"


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_claude_reply_empty_text(mock_anthropic):
    """If response text is None, should return empty string."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    mock_text_block = MagicMock()
    mock_text_block.text = None
    mock_response = MagicMock()
    mock_response.content = [mock_text_block]
    mock_client.messages.create.return_value = mock_response

    result = cc.claude_reply("test")
    assert result == ""


# -----------------------------------------------------------------------
# CLAUDE_REPLY — FAILURE
# -----------------------------------------------------------------------

@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
@patch("app.llm.claude_client.anthropic")
def test_claude_reply_api_failure(mock_anthropic):
    """API call failure should return the fallback string, not crash."""
    _reset_globals()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.side_effect = Exception("API timeout")

    result = cc.claude_reply("test prompt")

    assert "trouble connecting" in result.lower()
    assert "try again" in result.lower()


def test_claude_reply_init_failure():
    """If client init fails, claude_reply should return fallback, not raise."""
    _reset_globals()
    with patch.dict(os.environ, {}, clear=True):
        result = cc.claude_reply("test")
        assert "trouble connecting" in result.lower()


# -----------------------------------------------------------------------
# CLASSIFY_MESSAGE_LLM — DEPRECATED
# -----------------------------------------------------------------------
# Phase 1 of the pre-LLM redaction work (PRE_LLM_REDACTION_SCOPE.md)
# converted classify_message_llm() into a fail-fast deprecation stub.
# The function previously sent raw user text to Anthropic and was
# already removed from production code paths in the LLM-3 cleanup
# (see test_llm_call_redundancy.py for the regression test asserting
# it is not called from the orchestrator). The stub is preserved as a
# patch target for those regression tests but raises if invoked, so
# any future code that reaches for it gets a loud failure instead of
# silently bypassing the pre-LLM redactor.
#
# The previous SUCCESS / FAILURE test sections (returns valid
# category, uses Haiku, rejects invalid category, handles whitespace,
# API failure returns None, init failure returns None) tested the
# behavior of the now-deleted body and have been removed. The single
# test below pins the new contract.

def test_classify_message_llm_raises_runtime_error():
    """Calling classify_message_llm() must raise RuntimeError."""
    with pytest.raises(RuntimeError, match="deprecated"):
        cc.classify_message_llm("any message")


# -----------------------------------------------------------------------
# MODULE CLEANUP — prevent stale mock client from polluting later tests
# -----------------------------------------------------------------------

def teardown_module():
    _reset_globals()


# -----------------------------------------------------------------------
