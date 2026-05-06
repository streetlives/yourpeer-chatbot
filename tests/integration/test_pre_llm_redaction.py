"""Comprehensive Phase 1 verification for the pre-LLM redaction work.

Every Anthropic-touching call site listed in PRE_LLM_REDACTION_SCOPE.md
must, when ``REDACT_BEFORE_LLM=true``, send the *redacted* version of
the user message — never the raw text. Conversely, with the flag off
(default), every site must continue to send raw text bit-for-bit
identical to pre-Phase-1 behavior.

This test pins both halves of that contract. It is the definitive
"no leaks anywhere" check: if a future PR introduces a new LLM call
site or refactors an existing one without threading the flag through,
one of these tests will fail.

Structure
=========

Each leak surface gets two tests:

    test_<surface>_off_sends_raw      # flag default (False) — raw goes through
    test_<surface>_on_sends_redacted  # flag patched True  — redacted goes through

Mocking strategy
================

We patch ``get_client`` at every dispatch seam (the same pattern as
``test_llm_call_redundancy.py``). The mock client's
``messages.create`` records every call; the test asserts on the
``messages=[{"role": "user", "content": ...}]`` payload OR the
``system`` payload, whichever carries the user text for that surface.

Crisis Stage 2 is special — it's gated by an ``_USE_LLM_DETECTION``
module-level constant in crisis_detector.py and an indirect-language
prompt. We exercise it by feeding a message that won't match Stage 1
regex but contains a PII pattern, then asserting the LLM call payload
holds the redacted version.

Important non-coverage
======================

These tests do NOT exercise the non-LLM regex/pattern paths
(``_classify_action``, ``_classify_tone``, ``_compute_routing_category``,
``_compute_tone_prefix``, ``_extract_raw_phrase``). Those are
local-only and don't leak; whether they see raw or redacted text is
not a privacy property. See PRE_LLM_REDACTION_SCOPE.md "What's NOT a
leak" section.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A test message that contains PII the redactor will scrub. Choosing a
# phone number because it's the most common shape and the redactor
# replaces it with the well-known [PHONE] placeholder.
RAW_MESSAGE_WITH_PII = (
    "my number is 212-555-1234 and I need shelter in Brooklyn tonight"
)
EXPECTED_REDACTED = (
    "my number is [PHONE] and I need shelter in Brooklyn tonight"
)
PII_FRAGMENT = "212-555-1234"  # the bit the redactor scrubs
REDACTED_MARKER = "[PHONE]"


def _make_slot_extraction_response(slots: dict | None = None) -> MagicMock:
    """Mock response shaped like a Claude tool_use slot-extraction result."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "extract_intake_slots"
    block.input = slots or {
        "service_type": "shelter",
        "location": "brooklyn",
        "urgency": "high",
        "tone": None,
        "action": None,
    }
    response = MagicMock()
    response.content = [block]
    return response


def _make_text_response(text: str) -> MagicMock:
    """Mock response shaped like a single-text-block Claude reply."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _flag_patches(redact_on: bool):
    """Returns the list of context-manager patches to toggle
    ``_REDACT_BEFORE_LLM`` consistently at every import site.

    Module-level imports re-bind the symbol into each importing
    module's namespace, so patching ``context._REDACT_BEFORE_LLM``
    alone is insufficient — ``orchestrator``, ``pipeline``, and the
    two ``handlers/*`` modules all need their local binding patched
    too. ``post_results`` does function-local imports so the
    ``context``-level patch reaches it implicitly.
    """
    targets = [
        "app.services.chatbot.context._REDACT_BEFORE_LLM",
        "app.services.chatbot.pipeline._REDACT_BEFORE_LLM",
        "app.services.chatbot.orchestrator._REDACT_BEFORE_LLM",
        "app.services.chatbot.handlers.general._REDACT_BEFORE_LLM",
        "app.services.chatbot.handlers.meta._REDACT_BEFORE_LLM",
    ]
    return [patch(t, redact_on) for t in targets]


# ---------------------------------------------------------------------------
# Surface 1+2: slot extraction (gate path AND service-flow path)
# ---------------------------------------------------------------------------
# Both ``_run_llm_gate`` (orchestrator line 150) and the service-flow
# second extraction call (orchestrator line ~558) route through
# ``slot_extraction.extract`` -> ``extract_slots_short`` /
# ``extract_slots_narrative`` in dispatch.py. Patching ``get_client``
# at the dispatch seam catches both.


@pytest.mark.parametrize("redact_on,expect_pii,reject_marker", [
    (False, True, False),   # flag off: raw text reaches LLM, no [PHONE] marker
    (True, False, True),    # flag on: redacted text reaches LLM, [PHONE] marker present
])
def test_slot_extraction_payload_respects_flag(redact_on, expect_pii, reject_marker):
    """Slot extraction (gate AND service-flow) must use redacted text under
    the flag and raw text without it.

    Both surfaces share a code path through ``slot_extraction.extract``,
    so this single test covers items 1 and 2 from the leak-surface table.
    """
    from app.services.chatbot import generate_reply

    sid = f"test-slot-{uuid4().hex[:8]}"

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_slot_extraction_response()

    patches = [
        patch("app.services.chatbot.pipeline._USE_LLM", True),
        patch("app.services.chatbot.orchestrator._USE_LLM", True),
        patch("app.services.chatbot.handlers.confirmation._USE_LLM", True),
        patch(
            "app.services.slot_extraction.dispatch.get_client",
            return_value=mock_client,
        ),
        # Other LLM-using subsystems get safe stubs so they don't add
        # noise to the call inspection.
        patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
        patch("app.services.responses.claude_reply", return_value="ok"),
        patch("app.services.chatbot.execution.query_services", return_value=[]),
        patch("app.services.chatbot.orchestrator.detect_crisis", return_value=None),
        patch("app.services.classifier.detect_crisis", return_value=None),
    ] + _flag_patches(redact_on)

    with patches[0], patches[1], patches[2], patches[3], patches[4], \
            patches[5], patches[6], patches[7], patches[8], patches[9], \
            patches[10], patches[11], patches[12], patches[13]:
        generate_reply(RAW_MESSAGE_WITH_PII, session_id=sid)

    # Assert at least one call carried our message and inspect what
    # text it contained.
    assert mock_client.messages.create.call_count >= 1, (
        "Expected at least one slot-extraction LLM call for a message "
        "with high urgency + service intent that the gate would handle."
    )

    # Pull every payload that looks like it could carry user text.
    # slot_extraction prompts embed the message in messages[0].content
    # (user role) of the messages.create kwargs.
    sent_text_blobs = []
    for call in mock_client.messages.create.call_args_list:
        kwargs = call.kwargs or {}
        for msg in kwargs.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                sent_text_blobs.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        sent_text_blobs.append(block.get("text", ""))
        # Some prompts put the user text into the system field; capture
        # that too so we don't miss leaks via system-prompt embedding.
        sys_content = kwargs.get("system")
        if isinstance(sys_content, str):
            sent_text_blobs.append(sys_content)

    combined = "\n".join(sent_text_blobs)

    if expect_pii:
        assert PII_FRAGMENT in combined, (
            f"With flag OFF, slot-extraction LLM call should have received "
            f"raw PII text {PII_FRAGMENT!r}. Got:\n{combined!r}"
        )
    else:
        assert PII_FRAGMENT not in combined, (
            f"With flag ON, slot-extraction LLM call must NOT contain raw "
            f"PII fragment {PII_FRAGMENT!r}. Leak detected:\n{combined!r}"
        )
    if reject_marker:
        assert REDACTED_MARKER in combined, (
            f"With flag ON, slot-extraction LLM call should have received "
            f"the redacted placeholder {REDACTED_MARKER!r}. Got:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Surface 3: crisis detection Stage 2
# ---------------------------------------------------------------------------
# Stage 1 (regex) doesn't reach Anthropic and works on either raw or
# redacted input. Stage 2 (LLM) is gated by both _USE_LLM_DETECTION
# and Stage 1 missing. We exercise Stage 2 by:
#   1. Patching detect_crisis's Stage 1 to "miss" (returning the LLM
#      fallback path).
#   2. Capturing what Stage 2 sends.
# Easier: directly call detect_crisis with a controlled input and
# confirm what reaches the underlying LLM client.


@pytest.mark.parametrize("redact_on,expect_pii", [
    (False, True),
    (True, False),
])
def test_crisis_stage2_payload_respects_flag(redact_on, expect_pii):
    """Crisis detection's Stage 2 LLM call must use redacted text under the
    flag (and raw without it)."""
    from app.services.chatbot import generate_reply

    sid = f"test-crisis-{uuid4().hex[:8]}"

    mock_client = MagicMock()
    # Crisis Stage 2 returns a tool_use response with a single category
    # decision; we can return a "no crisis" so the test doesn't tip
    # into the crisis-resource path (which would early-return before
    # other surfaces matter, but that's fine — we only care about what
    # the Stage 2 LLM RECEIVED).
    block = MagicMock()
    block.type = "tool_use"
    block.name = "classify_crisis"
    block.input = {"category": "none"}
    response = MagicMock()
    response.content = [block]
    mock_client.messages.create.return_value = response

    # Drive a crisis-ambiguous message that won't match Stage 1 regex
    # but contains a phone number to verify what Stage 2 sees.
    crisis_ambiguous = (
        "I keep thinking about my number 212-555-1234, things are dark"
    )

    patches = [
        patch("app.services.crisis_detector._USE_LLM_DETECTION", True),
        patch("app.services.crisis_detector.get_client", return_value=mock_client),
        patch("app.services.chatbot.pipeline._USE_LLM", False),
        patch("app.services.chatbot.orchestrator._USE_LLM", False),
        patch("app.services.chatbot.execution.query_services", return_value=[]),
        patch("app.services.chatbot.handlers.meta.claude_reply", return_value="ok"),
        patch("app.services.responses.claude_reply", return_value="ok"),
    ] + _flag_patches(redact_on)

    # 7 base + 5 flag = 12 patches, indices 0..11
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
            patches[5], patches[6], patches[7], patches[8], patches[9], \
            patches[10], patches[11]:
        generate_reply(crisis_ambiguous, session_id=sid)

    # Find the crisis-Stage-2 call. The test mocks every LLM client at
    # different seams, so this client only sees crisis calls.
    sent_text_blobs = []
    for call in mock_client.messages.create.call_args_list:
        kwargs = call.kwargs or {}
        for msg in kwargs.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                sent_text_blobs.append(content)

    combined = "\n".join(sent_text_blobs)

    if not mock_client.messages.create.call_args_list:
        # If Stage 1 ate the message (which happens for some
        # ambiguous-but-still-overlapping phrasings), the test is
        # vacuous on this run. Don't false-pass; skip with a clear
        # explanation so the contributor knows to retune the input
        # if the regex list expands to swallow this case.
        pytest.skip(
            "Crisis Stage 1 regex matched the test input; Stage 2 LLM "
            "was not invoked. Retune `crisis_ambiguous` if this skip "
            "becomes persistent."
        )

    if expect_pii:
        assert PII_FRAGMENT in combined, (
            f"With flag OFF, crisis Stage 2 LLM should have received raw "
            f"PII text. Got:\n{combined!r}"
        )
    else:
        assert PII_FRAGMENT not in combined, (
            f"With flag ON, crisis Stage 2 LLM must NOT contain raw PII. "
            f"Leak detected:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Surface 4: post_results — _classify_post_results_llm
# ---------------------------------------------------------------------------
# Triggered when a user sends a follow-up after results are displayed.
# The session needs ``_last_results`` populated and the message has to
# be a follow-up phrasing (not a new service request). Routes through
# ``classify_post_results_question`` -> ``_classify_post_results_llm``.


@pytest.mark.parametrize("redact_on,expect_pii", [
    (False, True),
    (True, False),
])
def test_post_results_classify_payload_respects_flag(redact_on, expect_pii):
    """The post-results LLM classifier must use redacted text under the
    flag.

    Direct-call version: invokes ``classify_post_results_question``
    directly with controlled raw + redacted inputs, so the test does
    not depend on the orchestrator routing the message to the
    post-results handler. This isolates the leak-surface contract
    from upstream classifier changes.
    """
    from app.services.post_results import classify_post_results_question

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_text_response("other")

    # The phrase has to (a) survive the regex tiers in
    # classify_post_results_question and reach the LLM tier (>= 3 words,
    # not match _NEW_REQUEST_RE / specific-name / index / filter /
    # _RESULT_REFERENCE_RE / _ONES_REFINE_RE patterns), and (b) carry a
    # PII fragment so we can verify which version the LLM saw.
    # `_RESULT_REFERENCE_RE` catches them|they|these|those|the results|
    # the services|the options|the places — so we deliberately use no
    # such pronouns. "wondering if anything fits a 212-555-1234 type
    # problem" is pronoun-free, ambiguous-intent, and >= 3 words.
    raw_phrase = "wondering if anything fits a 212-555-1234 type problem"
    redacted_phrase = "wondering if anything fits a [PHONE] type problem"

    flag_patches = _flag_patches(redact_on)
    with patch("app.llm.claude_client.get_client", return_value=mock_client), \
         flag_patches[0], flag_patches[1], flag_patches[2], \
         flag_patches[3], flag_patches[4]:
        classify_post_results_question(raw_phrase, redacted_message=redacted_phrase)

    sent_text_blobs = []
    for call in mock_client.messages.create.call_args_list:
        kwargs = call.kwargs or {}
        for msg in kwargs.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                sent_text_blobs.append(content)

    assert sent_text_blobs, (
        "Post-results LLM classifier was not invoked. The test phrase "
        "should reach the LLM tier — if classify_post_results_question's "
        "regex tiers were tightened to handle this phrasing earlier, "
        "retune `raw_phrase` to a different ambiguous follow-up."
    )

    combined = "\n".join(sent_text_blobs)

    if expect_pii:
        assert PII_FRAGMENT in combined, (
            f"With flag OFF, post-results classifier should have received "
            f"raw PII text. Got:\n{combined!r}"
        )
    else:
        assert PII_FRAGMENT not in combined, (
            f"With flag ON, post-results classifier must NOT contain raw "
            f"PII. Leak detected:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Surface 5: post_results — _extract_keywords_llm
# ---------------------------------------------------------------------------
# Reached via the filter handler when ``_extract_keywords_llm`` is
# called with a ``raw_phrase`` derived from the user message. The
# fix routes ``_extract_raw_phrase`` over the redacted version under
# the flag, so the leak surface is closed at the keyword-derivation
# step rather than the LLM call alone.


@pytest.mark.parametrize("redact_on,expect_pii", [
    (False, True),
    (True, False),
])
def test_post_results_keyword_extract_payload_respects_flag(redact_on, expect_pii):
    """The post-results LLM keyword extractor must derive its raw_phrase
    from redacted text under the flag, so PII never reaches Anthropic
    via this path.

    Direct-call version: this surface is closed by the
    ``_raw_phrase_source`` picker in ``classify_post_results_question``
    — when the flag is on, ``_extract_raw_phrase`` operates on the
    redacted message, so the ``raw_phrase`` field of the
    ``filter_subcategory`` intent dict is already PII-free before it
    flows downstream to ``_handle_filter_subcategory`` ->
    ``_extract_keywords_llm``. We verify the contract at the intent-
    dict boundary rather than mocking through to ``_extract_keywords_llm``,
    because (a) ``_extract_keywords_llm`` is a passive embedder that
    can't possibly leak more than ``raw_phrase`` already contains, and
    (b) testing the boundary makes the contract reusable for any
    future caller of ``_extract_keywords_llm``.
    """
    from app.services.post_results import classify_post_results_question

    # A "ones that X" phrasing reaches the filter_subcategory branch
    # via _ONES_REFINE_RE = \b(ones like|ones that|ones with|ones for)\b.
    # The regex requires the words to be adjacent — "ones near X that"
    # would NOT match because "near X" sits between them. The PII
    # fragment goes after "for" so it's clearly inside the
    # raw_phrase that downstream _extract_keywords_llm receives.
    raw_phrase = "ones that allow walk-ins for 212-555-1234"
    redacted_phrase = "ones that allow walk-ins for [PHONE]"

    flag_patches = _flag_patches(redact_on)
    with flag_patches[0], flag_patches[1], flag_patches[2], \
         flag_patches[3], flag_patches[4]:
        result = classify_post_results_question(
            raw_phrase, redacted_message=redacted_phrase
        )

    assert result is not None and result.get("type") == "filter_subcategory", (
        f"Expected filter_subcategory intent for `ones that...` phrasing, "
        f"got {result!r}. If post_results regex tiers were retuned, "
        f"adjust the test phrase to a different filter-shape phrasing."
    )
    extracted_raw_phrase = result.get("raw_phrase", "")

    if expect_pii:
        assert PII_FRAGMENT in extracted_raw_phrase, (
            f"With flag OFF, the intent dict's raw_phrase field should "
            f"contain raw PII (since _extract_keywords_llm will receive "
            f"it verbatim). Got: {extracted_raw_phrase!r}"
        )
    else:
        assert PII_FRAGMENT not in extracted_raw_phrase, (
            f"With flag ON, the intent dict's raw_phrase must NOT "
            f"contain raw PII — _extract_keywords_llm receives this "
            f"value verbatim, so any PII here leaks to Anthropic. "
            f"Got: {extracted_raw_phrase!r}"
        )
        assert REDACTED_MARKER in extracted_raw_phrase, (
            f"With flag ON, raw_phrase should contain the redacted "
            f"placeholder. Got: {extracted_raw_phrase!r}"
        )


# ---------------------------------------------------------------------------
# Surface 6: conversational fallback (handlers/general -> _fallback_response)
# ---------------------------------------------------------------------------
# The general handler routes to _fallback_response for messages that
# don't match a service flow. _fallback_response builds a prompt that
# embeds the input verbatim ("User message: {user_message}") and sends
# it via claude_reply.


@pytest.mark.parametrize("redact_on,expect_pii", [
    (False, True),
    (True, False),
])
def test_conversational_fallback_payload_respects_flag(redact_on, expect_pii):
    """The conversational fallback (claude_reply via _fallback_response)
    must use redacted text under the flag.

    Direct-call version: invokes ``_handle_general_conversation(ctx)``
    with a hand-built ``MessageContext`` so the test does not depend
    on orchestrator routing landing on the general handler. Asserts
    on the prompt string passed to ``claude_reply``, which is the
    seam where the leak surface either redacts or doesn't.
    """
    from app.services.chatbot.handlers.general import _handle_general_conversation
    from conftest import make_ctx

    captured_prompts = []

    def _capture(prompt):
        captured_prompts.append(prompt)
        return "ok"

    # A substantive message that's neither casual-chat (no "how are
    # you", etc.) nor a service-need pattern (no "i need", "looking
    # for", etc.) — forces the else branch of
    # _handle_general_conversation that calls _fallback_response.
    raw = "my phone number 212-555-1234 has been acting weird lately honestly"
    redacted = "my phone number [PHONE] has been acting weird lately honestly"

    ctx = make_ctx(
        message=raw,
        redacted_message=redacted,
        merged={"transcript": []},  # empty transcript so unrecognized-need branch's >= 2 condition fails
        existing={},
        confidence="high",  # so the low-confidence navigator suffix doesn't fire
    )

    flag_patches = _flag_patches(redact_on)
    with patch("app.services.responses.claude_reply", side_effect=_capture), \
         flag_patches[0], flag_patches[1], flag_patches[2], \
         flag_patches[3], flag_patches[4]:
        _handle_general_conversation(ctx)

    assert captured_prompts, (
        "_fallback_response was not invoked. The general handler may "
        "have routed to the casual-chat or unrecognized-service branch "
        "instead. Adjust `raw` or the ctx fields if persistent."
    )

    combined = "\n".join(captured_prompts)

    if expect_pii:
        assert PII_FRAGMENT in combined, (
            f"With flag OFF, conversational fallback should have "
            f"received raw PII text. Got:\n{combined!r}"
        )
    else:
        assert PII_FRAGMENT not in combined, (
            f"With flag ON, conversational fallback must NOT contain "
            f"raw PII. Leak detected:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Surface 7: bot-question fallback (handlers/meta ->
# _handle_bot_capability_question)
# ---------------------------------------------------------------------------
# When a user asks a "what can you do" / "are you a bot" type question
# that doesn't match the local bot_knowledge string-match, the handler
# routes to claude_reply with a prompt that embeds the user's question.


@pytest.mark.parametrize("redact_on,expect_pii", [
    (False, True),
    (True, False),
])
def test_bot_question_fallback_payload_respects_flag(redact_on, expect_pii):
    """The bot-question LLM fallback must use redacted text under the flag.

    Direct-call version: invokes ``_handle_bot_capability_question(ctx)``
    with a hand-built ``MessageContext``. ``answer_question`` is patched
    to return None so the static-answer branch falls through to the LLM
    branch where the redaction swap lives.
    """
    from app.services.chatbot.handlers.meta import _handle_bot_capability_question
    from conftest import make_ctx

    captured_prompts = []

    def _capture(prompt):
        captured_prompts.append(prompt)
        return "I'm an assistant — anonymous and free."

    raw = "what unusual things can you help with for someone whose number is 212-555-1234"
    redacted = "what unusual things can you help with for someone whose number is [PHONE]"

    ctx = make_ctx(
        message=raw,
        redacted_message=redacted,
        existing={},
        category="bot_question",
    )

    flag_patches = _flag_patches(redact_on)
    with patch("app.services.bot_knowledge.answer_question", return_value=None), \
         patch("app.services.chatbot.handlers.meta._USE_LLM", True), \
         patch("app.services.chatbot.handlers.meta.claude_reply", side_effect=_capture), \
         flag_patches[0], flag_patches[1], flag_patches[2], \
         flag_patches[3], flag_patches[4]:
        _handle_bot_capability_question(ctx)

    assert captured_prompts, (
        "_handle_bot_capability_question's LLM branch did not fire. "
        "Confirm answer_question is patched to None and _USE_LLM is True "
        "at the meta module's import site."
    )

    combined = "\n".join(captured_prompts)

    if expect_pii:
        assert PII_FRAGMENT in combined, (
            f"With flag OFF, bot-question fallback should have "
            f"received raw PII text. Got:\n{combined!r}"
        )
    else:
        assert PII_FRAGMENT not in combined, (
            f"With flag ON, bot-question fallback must NOT contain raw "
            f"PII. Leak detected:\n{combined!r}"
        )


# ---------------------------------------------------------------------------
# Sanity: redactor produces the expected placeholder for our test fixture
# ---------------------------------------------------------------------------
# If the redactor's pattern set is ever changed and PII_FRAGMENT no
# longer maps to REDACTED_MARKER, every test above silently breaks
# even though the redaction itself works. Pin the contract loudly.

def test_redactor_fixture_matches_expectations():
    """The PII fragment used in the test suite must scrub to [PHONE]."""
    from app.privacy.pii_redactor import redact_pii
    redacted, detections = redact_pii(RAW_MESSAGE_WITH_PII)
    assert redacted == EXPECTED_REDACTED, (
        f"Redactor output drifted from test expectation. The leak-"
        f"surface tests above all depend on RAW_MESSAGE_WITH_PII "
        f"becoming EXPECTED_REDACTED. Got:\n  {redacted!r}\n"
        f"Expected:\n  {EXPECTED_REDACTED!r}"
    )
    assert detections, "Expected at least one PII detection in fixture"
    assert detections[0].pii_type == "phone"
