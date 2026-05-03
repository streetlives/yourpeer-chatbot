"""Regression tests for the LLM call-redundancy cleanup landed against
``LLM-1``, ``LLM-2``, and ``LLM-3`` from ``docs/audits/PHASE_AC_AFTERMATH.md``.

These tests assert call counts on the underlying Claude client for paths
that were previously firing the same LLM call twice on the same message.
They are written as **call-count** tests (not behavior tests) because the
audit findings were specifically about cost and latency — duplicate calls
that produced near-identical output. Behavior tests for these paths
already exist elsewhere in the suite (e.g.
``test_classification_and_routing.py``, ``test_service_data_llm_firewall``).

What "call count" means here: the number of invocations of
``client.messages.create()`` on the mocked Claude client. Every
slot-extraction path (gate + orchestrator service branch + post-pending-
confirmation handler) ultimately calls into
``app.llm.claude_client.get_client()``, so patching that single seam
catches every slot-extraction LLM call. We patch ``detect_crisis`` to
``None`` and ``claude_reply`` / ``query_services`` to fixed values to
avoid noise from other LLM-using subsystems.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


def _make_mock_client(slot_extraction_input: dict) -> MagicMock:
    """Build a mock Claude client whose ``messages.create()`` returns a
    tool-use response with the given slot-extraction input.

    Every call to ``messages.create()`` gets the same response, so the
    test can drive multiple LLM invocations and still get a deterministic
    output — the assertion is on call count, not response content.
    """
    block = MagicMock()
    block.type = "tool_use"
    block.name = "extract_intake_slots"
    block.input = slot_extraction_input
    response = MagicMock()
    response.content = [block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


@pytest.fixture
def mock_slot_extraction_client():
    """Mock client returning a service-shaped slot extraction (shelter +
    Queens). Suitable for messages where the LLM should "find" a
    service_type that regex missed.
    """
    return _make_mock_client({
        "service_type": "shelter",
        "location": "queens",
        "urgency": "high",
        "tone": None,
        "action": None,
    })


class TestLLM3NoClassifierFallbackCall:
    """LLM-3: ``_compute_routing_category`` previously fell through to a
    second LLM classifier (``classify_message_llm``) when no other branch
    matched. That call was redundant — by the time the fallback fires,
    every signal the LLM could detect has already been checked by
    ``_run_llm_gate`` (slot extraction + tone + action), and the
    fallback's category outputs either overlap with already-handled
    branches or are the same ``general`` default.

    The fix removes the fallback. This test pins ``classify_message_llm``
    is never invoked from the orchestrator pipeline.
    """

    def test_no_signal_message_does_not_call_classify_message_llm(
        self, mock_slot_extraction_client
    ):
        """A non-service message with no tone/action signal previously
        triggered ``classify_message_llm`` from the routing-category
        fallback. Post-LLM-3, that branch is gone and the routing
        defaults to ``general`` directly.
        """
        from app.services.chatbot import generate_reply

        sid = f"test-llm3-{uuid4().hex[:8]}"

        # Different mock client: returns an empty extraction so the gate
        # finds no service (matches the scenario where pre-fix the
        # classify_message_llm fallback would fire).
        empty_client = _make_mock_client({
            "service_type": None,
            "location": None,
            "tone": None,
            "action": None,
        })

        with patch(
            "app.services.chatbot.pipeline._USE_LLM", True,
        ), patch(
            "app.services.chatbot.orchestrator._USE_LLM", True,
        ), patch(
            "app.services.chatbot.handlers.confirmation._USE_LLM", True,
        ), patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=empty_client,
        ), patch(
            "app.services.chatbot.handlers.meta.claude_reply",
            return_value="ok",
        ), patch(
            "app.services.responses.claude_reply",
            return_value="ok",
        ), patch(
            # Counter on the routing-category LLM fallback. Post-LLM-3,
            # this should NEVER be called from the orchestrator path.
            "app.llm.claude_client.classify_message_llm",
            return_value=None,
        ) as mock_classify, patch(
            "app.services.chatbot.execution.query_services",
            return_value=[],
        ), patch(
            "app.services.chatbot.orchestrator.detect_crisis",
            return_value=None,
        ), patch(
            "app.services.classifier.detect_crisis",
            return_value=None,
        ):
            # 8 words — long enough to trigger the LLM fallback's
            # ``len(message) > 3`` precondition pre-fix. No service
            # keyword, no urgency word, no emotional trigger, no
            # explicit action — designed to fall through everything.
            generate_reply(
                "wondering about the various options that exist for me",
                session_id=sid,
            )

        assert mock_classify.call_count == 0, (
            f"Expected 0 calls to classify_message_llm (LLM-3 fallback "
            f"removal); got {mock_classify.call_count}. The "
            f"_compute_routing_category should default to 'general' "
            f"directly rather than firing a second LLM classifier."
        )


class TestLLM2PostPendingReusesContextValues:
    """LLM-2: ``_handle_post_pending_confirmation`` previously did a
    fresh ``extract_slots(ctx.message)`` followed by
    ``slot_extraction.extract(...)`` on every post-pending turn,
    duplicating work the orchestrator's ``_run_early_extraction`` and
    ``_run_llm_gate`` had already done. The fix reuses
    ``ctx.unified_extraction`` (cache hit from the gate) or
    ``ctx.early_extracted`` (regex+semantic) instead.
    """

    def test_post_pending_with_gate_hit_invokes_llm_only_once(
        self, mock_slot_extraction_client
    ):
        """When the gate already ran on the user's follow-up message
        (e.g. they typed a long response while a confirmation was
        pending), the post-pending handler should reuse the gate's
        cached unified result instead of firing the LLM again.

        Pre-fix call count for this scenario: 2 (gate + handler).
        Post-fix: 1 (gate only).
        """
        from app.services.chatbot import generate_reply
        from app.services.session_store import save_session_slots

        sid = f"test-llm2-pending-{uuid4().hex[:8]}"

        # Set up a pending confirmation in the session, mimicking the
        # state after a prior turn where the bot offered a confirmation.
        save_session_slots(sid, {
            "_pending_confirmation": True,
            "service_type": "shelter",
            "location": "manhattan",
        })

        with patch(
            "app.services.chatbot.pipeline._USE_LLM", True,
        ), patch(
            "app.services.chatbot.orchestrator._USE_LLM", True,
        ), patch(
            "app.services.chatbot.handlers.confirmation._USE_LLM", True,
        ), patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=mock_slot_extraction_client,
        ), patch(
            "app.services.chatbot.handlers.meta.claude_reply",
            return_value="ok",
        ), patch(
            "app.services.responses.claude_reply",
            return_value="ok",
        ), patch(
            "app.llm.claude_client.classify_message_llm",
            return_value=None,
        ), patch(
            "app.services.chatbot.execution.query_services",
            return_value=[],
        ), patch(
            "app.services.chatbot.orchestrator.detect_crisis",
            return_value=None,
        ), patch(
            "app.services.classifier.detect_crisis",
            return_value=None,
        ):
            # 11 words, regex misses service_type, no urgency tone.
            # Same conditions as the LLM-1 test message — gate fires.
            generate_reply(
                "could really use some assistance with finding somewhere indoors in queens",
                session_id=sid,
            )

        assert mock_slot_extraction_client.messages.create.call_count == 1, (
            f"Expected 1 LLM call (LLM-2 cache hit on the post-pending "
            f"path); got {mock_slot_extraction_client.messages.create.call_count}. "
            f"_handle_post_pending_confirmation should reuse "
            f"ctx.unified_extraction from the gate instead of firing "
            f"slot_extraction.extract() a second time."
        )


class TestLLM1NoDuplicateExtractionCall:
    """LLM-1: when ``_run_llm_gate`` fires ``slot_extraction.extract()``
    on a long message that regex missed, the orchestrator's service
    branch must reuse the cached result via ``ctx.unified_extraction``
    instead of firing a second identical LLM call.
    """

    def test_long_service_message_invokes_llm_once(self, mock_slot_extraction_client):
        """Pre-fix: 2 LLM calls (gate + service branch). Post-fix: 1.

        The message is >8 words so ``_is_simple_message`` returns False
        in the second call's path, which is what allowed the duplicate
        call to fire pre-fix. The message also has no regex-matchable
        service keyword (so the gate's ``not has_service_intent``
        precondition holds) and no regex-matchable tone (so the gate's
        ``regex_tone_pre is None`` precondition holds).
        """
        from app.services.chatbot import generate_reply

        sid = f"test-llm1-{uuid4().hex[:8]}"

        with patch(
            "app.services.chatbot.pipeline._USE_LLM", True,
        ), patch(
            "app.services.chatbot.orchestrator._USE_LLM", True,
        ), patch(
            "app.services.chatbot.handlers.confirmation._USE_LLM", True,
        ), patch(
            # dispatch.py imports get_client at module top, freezing the
            # local binding. Patch the bind site (not the source module).
            "app.services.slot_extraction.dispatch.get_client",
            return_value=mock_slot_extraction_client,
        ), patch(
            # Two ``claude_reply`` bind sites (one in handlers.meta for
            # service-flow responses, one in services.responses for the
            # general fallback). Both must be patched out so they don't
            # 401 when the test environment has no real API key.
            "app.services.chatbot.handlers.meta.claude_reply",
            return_value="ok",
        ), patch(
            "app.services.responses.claude_reply",
            return_value="ok",
        ), patch(
            # ``classify_message_llm`` is the routing-category LLM
            # fallback (LLM-3 territory). Patched to None so we don't
            # spuriously count its LLM calls.
            "app.llm.claude_client.classify_message_llm",
            return_value=None,
        ), patch(
            "app.services.chatbot.execution.query_services",
            return_value=[],
        ), patch(
            "app.services.chatbot.orchestrator.detect_crisis",
            return_value=None,
        ), patch(
            "app.services.classifier.detect_crisis",
            return_value=None,
        ):
            generate_reply(
                # 11 words. Regex catches location ("queens") but NOT
                # service_type — the phrase "somewhere indoors" doesn't
                # match any shelter keyword in the regex extractor's
                # vocabulary. Also tone-neutral: no "tonight"/"asap"
                # would tip the regex tone classifier into "urgent",
                # which would block the gate's precondition
                # ``regex_tone_pre is None``. So has_service_intent is
                # False after regex AND the gate's tone gate doesn't
                # short-circuit; the gate fires (LLM call #1), the LLM
                # finds service_type="shelter", mutates early_extracted,
                # and then has_service_intent is True for the rest of
                # the turn. Pre-fix the orchestrator's service branch
                # would then call slot_extraction.extract a SECOND time
                # on the same message (LLM call #2). Post-fix it reads
                # from ctx.unified_extraction.
                "could really use some assistance with finding somewhere indoors in queens",
                session_id=sid,
            )

        assert mock_slot_extraction_client.messages.create.call_count == 1, (
            f"Expected 1 LLM call (LLM-1 cache hit); got "
            f"{mock_slot_extraction_client.messages.create.call_count}. "
            f"The orchestrator's service branch should reuse "
            f"ctx.unified_extraction from the gate instead of firing "
            f"slot_extraction.extract() a second time."
        )

    def test_short_simple_service_message_invokes_llm_zero_times(
        self, mock_slot_extraction_client
    ):
        """Sanity check on the existing fast paths. A short message with
        regex-matchable service+location ("I need food in Brooklyn", 5
        words) should not invoke the LLM at all — ``_is_simple_message``
        returns True in ``slot_extraction.extract()``, AND the gate
        doesn't fire because ``has_service_intent`` is True from regex.

        This test exists to confirm the LLM-1 cache change didn't
        accidentally introduce a regression on the short path. If this
        fails, the cache fix is calling the LLM for a message it
        previously didn't.
        """
        from app.services.chatbot import generate_reply

        sid = f"test-llm1-short-{uuid4().hex[:8]}"

        with patch(
            "app.services.chatbot.pipeline._USE_LLM", True,
        ), patch(
            "app.services.chatbot.orchestrator._USE_LLM", True,
        ), patch(
            "app.services.chatbot.handlers.confirmation._USE_LLM", True,
        ), patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=mock_slot_extraction_client,
        ), patch(
            "app.services.chatbot.handlers.meta.claude_reply",
            return_value="ok",
        ), patch(
            "app.services.responses.claude_reply",
            return_value="ok",
        ), patch(
            "app.llm.claude_client.classify_message_llm",
            return_value=None,
        ), patch(
            "app.services.chatbot.execution.query_services",
            return_value=[],
        ), patch(
            "app.services.chatbot.orchestrator.detect_crisis",
            return_value=None,
        ), patch(
            "app.services.classifier.detect_crisis",
            return_value=None,
        ):
            generate_reply("I need food in brooklyn", session_id=sid)

        assert mock_slot_extraction_client.messages.create.call_count == 0, (
            f"Expected 0 LLM calls for a short regex-resolved message; "
            f"got {mock_slot_extraction_client.messages.create.call_count}. "
            f"The simple-message fast path should skip LLM extraction "
            f"entirely."
        )
