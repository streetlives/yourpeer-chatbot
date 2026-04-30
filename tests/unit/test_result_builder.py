"""Tests for ``chatbot.result_builder._build_follow_up_response``.

Each output-dict field is asserted independently (kills "wrong field
source" mutants), the ``_log_turn`` call is asserted positional-by-
positional (kills argument-shuffle mutants), and the keyword-only
constraint is enforced via a ``TypeError`` test (kills the mutant
that drops the ``*`` from the signature).

All tests use a stub for ``_log_turn`` so the audit-log dependency
isn't exercised — that's covered by ``test_logging.py``.
"""

import pytest

from app.services.chatbot.result_builder import _build_follow_up_response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log_recorder(monkeypatch):
    """Replace ``_log_turn`` and record every call.

    Returns a list of call records; each record is a tuple of
    ``(args_tuple, kwargs_dict)`` so positional and keyword arguments
    are distinguishable.
    """
    calls = []

    def _stub(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "app.services.chatbot.result_builder._log_turn", _stub
    )
    return calls


def _call(**overrides):
    """Build a ``_build_follow_up_response`` call with defaulted args.

    Lets each test override one or two parameters at a time. The
    defaults are valid representative values (no None unless the test
    is testing None-handling).
    """
    kwargs = {
        "session_id": "sess-1",
        "redacted_message": "redacted user msg",
        "response_text": "bot response",
        "merged": {"service_type": "shelter"},
        "quick_replies": ["yes", "no"],
        "log_category": "confirmation",
        "request_id": "req-1",
        "tone": "warm",
    }
    kwargs.update(overrides)
    return _build_follow_up_response(**kwargs)


# ===========================================================================
# Output dict — one test per field
# ===========================================================================


class TestOutputDictFields:
    def test_session_id_field_is_input_value(self, log_recorder):
        result = _call(session_id="sess-XYZ")
        assert result["session_id"] == "sess-XYZ"

    def test_response_field_is_input_response_text(self, log_recorder):
        """Mutant kill: ``"response": session_id`` (wrong source)."""
        result = _call(response_text="hello there")
        assert result["response"] == "hello there"

    def test_follow_up_needed_is_true(self, log_recorder):
        """Mutant kill: ``True`` → ``False``."""
        result = _call()
        assert result["follow_up_needed"] is True

    def test_slots_field_is_merged_input(self, log_recorder):
        """Mutant kill: ``"slots": existing`` (wrong source — but
        helper has no ``existing`` arg, so a more plausible mutant is
        ``"slots": {}``).
        """
        merged = {"service_type": "food", "location": "Brooklyn"}
        result = _call(merged=merged)
        assert result["slots"] is merged  # identity, not just equality

    def test_services_field_is_empty_list(self, log_recorder):
        """Mutant kill: ``[]`` → ``[None]`` or ``[merged]``."""
        result = _call()
        assert result["services"] == []

    def test_result_count_field_is_zero(self, log_recorder):
        """Mutant kill: ``0`` → ``1`` or ``len(quick_replies)``."""
        result = _call(quick_replies=["a", "b", "c"])
        assert result["result_count"] == 0

    def test_relaxed_search_field_is_false(self, log_recorder):
        """Mutant kill: ``False`` → ``True``."""
        result = _call()
        assert result["relaxed_search"] is False

    def test_quick_replies_field_is_input_value(self, log_recorder):
        """Mutant kill: ``"quick_replies": []`` (dropping the input)."""
        result = _call(quick_replies=["yes", "no", "maybe"])
        assert result["quick_replies"] == ["yes", "no", "maybe"]

    def test_returned_dict_has_exactly_eight_keys(self, log_recorder):
        """Mutant kill: dropping a key, or adding a stray key.

        If ``cosmic-ray`` mutates the dict literal to add or remove a
        line, this assertion catches it.
        """
        result = _call()
        assert set(result.keys()) == {
            "session_id",
            "response",
            "follow_up_needed",
            "slots",
            "services",
            "result_count",
            "relaxed_search",
            "quick_replies",
        }


# ===========================================================================
# _log_turn invocation
# ===========================================================================


class TestLogTurnInvocation:
    def test_log_turn_called_exactly_once(self, log_recorder):
        _call()
        assert len(log_recorder) == 1

    def test_log_turn_receives_session_id_first_positional(self, log_recorder):
        _call(session_id="sess-LOG")
        args, kwargs = log_recorder[0]
        assert args[0] == "sess-LOG"

    def test_log_turn_receives_redacted_message_second_positional(
        self, log_recorder
    ):
        """Mutant kill: positional swap of session_id and redacted_message."""
        _call(redacted_message="REDACTED-CONTENT")
        args, kwargs = log_recorder[0]
        assert args[1] == "REDACTED-CONTENT"

    def test_log_turn_receives_result_dict_third_positional(self, log_recorder):
        """The result dict passed to _log_turn should be the SAME dict
        the helper returns (identity, not just equality), so the
        audit-log layer can read response/slots from it.

        Mutant kill: ``_log_turn(session_id, redacted_message, {}, ...)``.
        """
        result = _call()
        args, kwargs = log_recorder[0]
        assert args[2] is result

    def test_log_turn_receives_log_category_fourth_positional(
        self, log_recorder
    ):
        """Mutant kill: log_category arg dropped or replaced with literal."""
        _call(log_category="my-special-category")
        args, kwargs = log_recorder[0]
        assert args[3] == "my-special-category"

    def test_log_turn_receives_request_id_kwarg(self, log_recorder):
        _call(request_id="req-XYZ")
        args, kwargs = log_recorder[0]
        assert kwargs.get("request_id") == "req-XYZ"

    def test_log_turn_receives_tone_kwarg(self, log_recorder):
        _call(tone="frustrated")
        args, kwargs = log_recorder[0]
        assert kwargs.get("tone") == "frustrated"

    def test_log_turn_receives_only_request_id_and_tone_as_kwargs(
        self, log_recorder
    ):
        """Mutant kill: extra kwargs added that the original code didn't pass."""
        _call()
        args, kwargs = log_recorder[0]
        assert set(kwargs.keys()) == {"request_id", "tone"}


# ===========================================================================
# None-passthrough cases
# ===========================================================================


class TestNonePassthrough:
    def test_none_request_id_is_forwarded_as_none(self, log_recorder):
        _call(request_id=None)
        _, kwargs = log_recorder[0]
        assert kwargs["request_id"] is None

    def test_none_tone_is_forwarded_as_none(self, log_recorder):
        _call(tone=None)
        _, kwargs = log_recorder[0]
        assert kwargs["tone"] is None

    def test_empty_quick_replies_list_is_forwarded(self, log_recorder):
        result = _call(quick_replies=[])
        assert result["quick_replies"] == []


# ===========================================================================
# Keyword-only enforcement
# ===========================================================================


class TestKeywordOnlySignature:
    def test_helper_is_keyword_only(self, log_recorder):
        """Mutant kill: dropping the ``*`` from the signature.

        With keyword-only args, every caller must spell out names,
        which prevents argument-order mutants from compiling at all.
        Removing the ``*`` allows positional calls — this test
        defends the constraint.
        """
        with pytest.raises(TypeError):
            _build_follow_up_response(  # noqa  — intentional positional call
                "sess-1",
                "redacted",
                "text",
                {},
                [],
                "category",
                None,
                None,
            )
