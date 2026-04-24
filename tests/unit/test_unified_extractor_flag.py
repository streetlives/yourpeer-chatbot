"""Phase 2 tests for the `USE_UNIFIED_EXTRACTOR` feature flag.

Verifies that when the flag is on, the orchestrator and confirmation
handler route slot extraction through the new
`slot_extraction.extract()` rather than the legacy
`extract_slots_smart`. When the flag is off, the legacy path runs.

These are routing tests, not eval tests — they mock the LLM so no API
credits are spent and the parallel-run eval can be run separately.

See UNIFIED_EXTRACTOR_MIGRATION.md Phase 2 for scope and acceptance
criteria.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_session_state():
    """Every test in this module starts with a fresh session store.
    Without this, module-level `_SESSION_STATE` in session_store.py
    can leak across tests — especially insidious because Python
    interns short string literals, so `id(message)` collides across
    tests using the same message text."""
    from app.services.session_store import _SESSION_STATE
    _SESSION_STATE.clear()
    yield
    _SESSION_STATE.clear()


# ---------------------------------------------------------------------------
# Flag definition — smoke tests
# ---------------------------------------------------------------------------

class TestFlagDefinition:
    """The flag itself — is it exposed where callers expect it?"""

    def test_flag_is_defined_in_context_module(self):
        from app.services.chatbot.context import _USE_UNIFIED_EXTRACTOR
        # It's a bool
        assert isinstance(_USE_UNIFIED_EXTRACTOR, bool)

    def test_flag_is_re_exported_from_chatbot_package(self):
        from app.services.chatbot import _USE_UNIFIED_EXTRACTOR
        assert isinstance(_USE_UNIFIED_EXTRACTOR, bool)

    def test_flag_is_false_when_env_unset(self, monkeypatch):
        """With USE_UNIFIED_EXTRACTOR unset, flag is False at import
        time. Re-importing after modifying env vars is the mechanism
        for exercising the flag logic."""
        monkeypatch.delenv("USE_UNIFIED_EXTRACTOR", raising=False)
        # Re-import the module under a fresh env
        import importlib
        import app.services.chatbot.context as ctx_module
        importlib.reload(ctx_module)
        assert ctx_module._USE_UNIFIED_EXTRACTOR is False

    @pytest.mark.parametrize("truthy_value", ["1", "true", "TRUE", "yes", "YES", "on", "On"])
    def test_flag_is_true_for_truthy_values(self, monkeypatch, truthy_value):
        monkeypatch.setenv("USE_UNIFIED_EXTRACTOR", truthy_value)
        import importlib
        import app.services.chatbot.context as ctx_module
        importlib.reload(ctx_module)
        assert ctx_module._USE_UNIFIED_EXTRACTOR is True

    @pytest.mark.parametrize("falsy_value", ["0", "false", "no", "off", "", "random_string"])
    def test_flag_is_false_for_falsy_or_unrecognized(self, monkeypatch, falsy_value):
        monkeypatch.setenv("USE_UNIFIED_EXTRACTOR", falsy_value)
        import importlib
        import app.services.chatbot.context as ctx_module
        importlib.reload(ctx_module)
        assert ctx_module._USE_UNIFIED_EXTRACTOR is False


# ---------------------------------------------------------------------------
# Orchestrator routing
# ---------------------------------------------------------------------------

class TestOrchestratorFlagRouting:
    """The orchestrator picks the new extractor when the flag is on."""

    def test_flag_off_calls_legacy_extract_slots_smart(self):
        """With flag off, orchestrator uses legacy
        `extract_slots_smart`. This is the current prod behavior."""
        # Patch the flag to False + _USE_LLM True
        with patch("app.services.chatbot.orchestrator._USE_UNIFIED_EXTRACTOR", False), \
             patch("app.services.chatbot.orchestrator._USE_LLM", True), \
             patch(
                 "app.services.llm_slot_extractor.extract_slots_smart"
             ) as mock_legacy, \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_legacy.return_value = _mock_extracted_slots()
            # Fire the orchestrator branch by calling the function
            # directly — use a simple service message.
            _run_orchestrator_service_message("i need food in brooklyn")

            # Legacy path was called; new path was not.
            mock_legacy.assert_called()
            mock_new.assert_not_called()

    def test_flag_on_calls_unified_extract(self):
        """With flag on, orchestrator uses the new `extract()`."""
        with patch("app.services.chatbot.orchestrator._USE_UNIFIED_EXTRACTOR", True), \
             patch("app.services.chatbot.orchestrator._USE_LLM", True), \
             patch(
                 "app.services.llm_slot_extractor.extract_slots_smart"
             ) as mock_legacy, \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_new.return_value = _mock_extracted_slots()
            _run_orchestrator_service_message("i need food in brooklyn")

            # New path called; legacy not.
            mock_new.assert_called()
            mock_legacy.assert_not_called()

    def test_flag_on_passes_regex_result_as_second_arg(self):
        """The new `extract()` signature requires a regex_result
        parameter. Verify the orchestrator passes `early_extracted`
        (which is the regex output) as the second arg."""
        with patch("app.services.chatbot.orchestrator._USE_UNIFIED_EXTRACTOR", True), \
             patch("app.services.chatbot.orchestrator._USE_LLM", True), \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_new.return_value = _mock_extracted_slots()
            _run_orchestrator_service_message("i need food in brooklyn")

            # Inspect call args
            assert mock_new.called
            args, kwargs = mock_new.call_args
            # First positional: the raw message
            assert args[0] == "i need food in brooklyn"
            # Second positional: regex_result (dict shape)
            assert isinstance(args[1], dict)
            # Should have the 13 fields from extract_slots
            assert "service_type" in args[1]
            assert "additional_services" in args[1]


# ---------------------------------------------------------------------------
# Confirmation handler routing
# ---------------------------------------------------------------------------

class TestConfirmationHandlerFlagRouting:
    """The confirmation handler picks the new extractor when the flag
    is on. Unlike the orchestrator, it doesn't have `early_extracted`
    in scope, so it has to run regex itself inline."""

    def test_flag_off_calls_legacy_extract_slots_smart(self):
        with patch("app.services.chatbot.handlers.confirmation._USE_UNIFIED_EXTRACTOR", False), \
             patch("app.services.chatbot.handlers.confirmation._USE_LLM", True), \
             patch(
                 "app.services.llm_slot_extractor.extract_slots_smart"
             ) as mock_legacy, \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_legacy.return_value = _mock_extracted_slots()
            _run_confirmation_handler("actually, shelter")
            mock_legacy.assert_called()
            mock_new.assert_not_called()

    def test_flag_on_calls_unified_extract(self):
        with patch("app.services.chatbot.handlers.confirmation._USE_UNIFIED_EXTRACTOR", True), \
             patch("app.services.chatbot.handlers.confirmation._USE_LLM", True), \
             patch(
                 "app.services.llm_slot_extractor.extract_slots_smart"
             ) as mock_legacy, \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_new.return_value = _mock_extracted_slots()
            _run_confirmation_handler("actually, shelter")
            mock_new.assert_called()
            mock_legacy.assert_not_called()

    def test_flag_on_runs_regex_inline_before_new_extract(self):
        """The confirmation handler doesn't have early_extracted in
        scope, so it must call extract_slots inline. Verify the
        regex_result passed to extract() was computed from the
        message."""
        with patch("app.services.chatbot.handlers.confirmation._USE_UNIFIED_EXTRACTOR", True), \
             patch("app.services.chatbot.handlers.confirmation._USE_LLM", True), \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            mock_new.return_value = _mock_extracted_slots()
            _run_confirmation_handler("i need food in brooklyn now")

            assert mock_new.called
            args, _ = mock_new.call_args
            # Second positional: regex_result. Regex should have caught
            # 'food' and 'brooklyn' from this message.
            regex_result = args[1]
            assert regex_result["service_type"] == "food"
            assert regex_result["location"] == "brooklyn"


# ---------------------------------------------------------------------------
# No-API-key path (flag + no key)
# ---------------------------------------------------------------------------

class TestNoAPIKeyPath:
    """When _USE_LLM is False, neither flag should matter — both
    paths bypass LLM entirely."""

    def test_no_api_key_orchestrator_uses_early_extracted(self):
        """Flag on but no API key → orchestrator takes the
        `extracted = early_extracted` branch and never calls either
        extractor."""
        with patch("app.services.chatbot.orchestrator._USE_UNIFIED_EXTRACTOR", True), \
             patch("app.services.chatbot.orchestrator._USE_LLM", False), \
             patch(
                 "app.services.llm_slot_extractor.extract_slots_smart"
             ) as mock_legacy, \
             patch(
                 "app.services.slot_extraction.extract"
             ) as mock_new:
            _run_orchestrator_service_message("i need food in brooklyn")
            mock_legacy.assert_not_called()
            mock_new.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_extracted_slots() -> dict:
    """Return a well-formed 13-field extracted-slots dict."""
    return {
        "service_type": "food",
        "service_detail": None,
        "additional_services": [],
        "location": "brooklyn",
        "age": None,
        "urgency": None,
        "_gender": None,
        "family_status": None,
        "_populations": [],
        "org_name": None,
        "no_requirements": False,
        "_contradiction": False,
        "_is_additive": False,
    }


def _run_orchestrator_service_message(message: str):
    """Drive the orchestrator far enough to trigger the service-branch
    extraction. Uses generate_reply with a fresh session."""
    import uuid
    from app.services.chatbot import generate_reply
    # Unique session id so no cross-test leakage via session store.
    # (Python interns string literals; `id(message)` is not sufficient
    # when the same message string appears in multiple tests — they
    # share the same integer id and would collide in _SESSION_STATE.)
    session_id = f"test-session-{uuid.uuid4()}"
    try:
        generate_reply(
            session_id=session_id,
            message=message,
        )
    except Exception:
        # The mocks may break downstream behavior (e.g., the query
        # pipeline), but the extraction call site is what we're
        # asserting — the assertion on the mock is the load-bearing
        # check, not the end-to-end reply.
        pass


def _run_confirmation_handler(message: str):
    """Drive the confirmation handler's post-pending path.

    Sets up a session with a `_pending_confirmation` marker, then
    sends `message` through `generate_reply`. The orchestrator will
    route it into `_handle_post_pending_confirmation`, which hits
    the extraction call site we're testing.
    """
    import uuid
    from app.services.chatbot import generate_reply
    from app.services.session_store import save_session_slots

    session_id = f"test-pending-{uuid.uuid4()}"
    # Prime the session with a pending confirmation
    save_session_slots(session_id, {
        "service_type": "food",
        "location": "brooklyn",
        "_pending_confirmation": "search",
        "transcript": [],
    })
    try:
        generate_reply(
            session_id=session_id,
            message=message,
        )
    except Exception:
        pass
