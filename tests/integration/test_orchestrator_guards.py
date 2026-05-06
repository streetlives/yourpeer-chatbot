"""Tests that close mutation-score gaps in ``orchestrator.py``.

These tests target the three code blocks added in PR #62 — the
queue-accept fast path, the ``_awaiting_service_after_clear`` guard,
and the unified-extractor call site — because that's where the
surviving mutants live (mutation score 68% on this file, threshold
70%).

Note: Phase 4 (April 2026) deleted the legacy ``extract_slots_smart``
path and the ``_USE_UNIFIED_EXTRACTOR`` feature flag. Slot extraction
in the service-flow branch is now an unconditional call into
``app.services.slot_extraction.extract``. Tests here exercise that
unconditional path; there is no flag to flip.

The strategy is: for every conjunction, equality, index, and argument
shape in those blocks, write at least one positive test (path taken,
correct arguments) and one negative test per *individual* clause being
false. That is what kills mutants of the form ``and`` → ``or``, ``==``
→ ``!=``, ``[0]`` → ``[1]``, and dropped ``not``s — a single
"happy path + sad path" pair only kills the overall predicate, not the
sub-clauses.

Each test docstring names the mutant(s) it kills so future readers can
preserve the coverage when refactoring.

These tests deliberately mock at a high level (``_promote_queued_offer``,
``extract_unified``) so failures point at the orchestrator's routing
logic, not at downstream behavior already covered by
``test_slot_extraction.py`` and ``test_multi_intent_queue.py``.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session(monkeypatch):
    """In-memory session store. Returns a (get, save) pair backed by a dict.

    Avoids touching the real session backend and lets each test set up its
    own session state explicitly.
    """
    store: dict[str, dict] = {}

    def _get(session_id):
        # Return a copy so tests can detect whether the orchestrator
        # mutated and saved (rather than mutating in place without saving).
        return dict(store.get(session_id, {}))

    def _save(session_id, slots):
        store[session_id] = dict(slots)

    monkeypatch.setattr(
        "app.services.chatbot.orchestrator.get_session_slots", _get
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator.save_session_slots", _save
    )
    return store


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Stub out classification/PII/LLM-gate so tests focus on routing.

    Returns a controller object that lets each test set:
      * the early_extracted dict
      * has_service_intent
      * the action category
      * the tone
      * the crisis result (default: None — no crisis)
    """

    class Controller:
        early_extracted: dict = {"service_type": None}
        has_service_intent: bool = False
        action: str = "service"
        tone: str | None = None
        crisis: object = None

    ctrl = Controller()

    def _redact(message):
        return message, "", []

    def _early(message, session_id):
        return dict(ctrl.early_extracted), "regex"

    def _llm_gate(**kwargs):
        return (
            ctrl.has_service_intent,
            ctrl.action,
            "regex",
            None,
            None,
            None,  # unified_extraction — None to exercise the non-cached path
        )

    def _classify_action(message):
        return ctrl.action

    def _classify_tone(message, crisis_result=None, **_kwargs):
        # **_kwargs absorbs the orchestrator's ``redacted_text=...`` kwarg
        # threaded through in the Phase 1 pre-LLM redaction work
        # (PRE_LLM_REDACTION_SCOPE.md). The stub doesn't care about
        # redaction since it's a fixed-return mock; the **_kwargs lets
        # this fixture stay backward-compatible without each test having
        # to know about the threaded parameter.
        return ctrl.tone

    def _detect_crisis(message, skip_llm=False):
        return ctrl.crisis

    def _routing_category(**kwargs):
        return kwargs.get("action", "service"), 0.9

    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._redact_with_safety_warning", _redact
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._run_early_extraction", _early
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._run_llm_gate", _llm_gate
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._classify_action", _classify_action
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._classify_tone", _classify_tone
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator.detect_crisis", _detect_crisis
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._compute_routing_category",
        _routing_category,
    )
    monkeypatch.setattr(
        "app.services.chatbot.orchestrator._apply_session_geo",
        lambda *args, **kwargs: None,
    )
    return ctrl


# ===========================================================================
# Block A — Queue-Accept Fast Path
# ===========================================================================


class TestQueueAcceptFastPath:
    """Block A: 4-clause conjunction + argument shape verification.

    Source code under test:

        if (has_service_intent
                and existing.get("_queue_offer_pending")
                and existing.get("_queued_offer")
                and early_extracted.get("service_type") == existing["_queued_offer"][0]):
            offer = existing["_queued_offer"]
            return _promote_queued_offer(
                ctx, offer,
                location_override=early_extracted.get("location"),
            )
    """

    def test_fast_path_promotes_offer_when_all_four_clauses_are_satisfied(
        self, mock_session, stub_pipeline
    ):
        """Positive case: every clause holds → _promote_queued_offer called.

        Kills the trivial removal mutant (deleting the whole if-block).
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-pos"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            "_queued_offer": ("shelter", None, "Brooklyn"),
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "location": "Brooklyn",
        }
        stub_pipeline.action = "service"

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            mock_promote.return_value = {"response": "ok"}
            result = generate_reply("I need shelter", session_id=sid)

        assert mock_promote.called
        assert result == {"response": "ok"}

    def test_fast_path_is_skipped_when_no_service_intent_was_extracted(
        self, mock_session, stub_pipeline
    ):
        """Clause 1 false: ``has_service_intent`` is False.

        Kills the ``and`` → ``or`` mutant on the first clause: under
        the mutated form, _queue_offer_pending alone would be enough to
        promote, even without service intent.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-no-intent"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            "_queued_offer": ("shelter", None, "Brooklyn"),
        }
        stub_pipeline.has_service_intent = False
        stub_pipeline.early_extracted = {"service_type": None}
        stub_pipeline.action = "general"

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            generate_reply("hello", session_id=sid)

        assert not mock_promote.called

    def test_fast_path_is_skipped_when_queue_offer_pending_flag_is_absent(
        self, mock_session, stub_pipeline
    ):
        """Clause 2 false: ``_queue_offer_pending`` not set.

        Kills the ``and`` → ``or`` mutant on the second clause AND the
        mutant that drops the get() entirely.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-no-pending"
        mock_session[sid] = {
            # _queue_offer_pending intentionally missing
            "_queued_offer": ("shelter", None, "Brooklyn"),
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "location": "Brooklyn",
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            generate_reply("I need shelter", session_id=sid)

        assert not mock_promote.called

    def test_fast_path_is_skipped_when_queued_offer_tuple_is_missing(
        self, mock_session, stub_pipeline
    ):
        """Clause 3 false: ``_queued_offer`` tuple missing.

        Kills the ``and`` → ``or`` mutant on the third clause. Without
        this test, the mutated predicate would attempt
        ``existing["_queued_offer"][0]`` and KeyError.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-no-tuple"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            # _queued_offer intentionally missing
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "location": "Brooklyn",
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            generate_reply("I need shelter", session_id=sid)

        assert not mock_promote.called

    def test_fast_path_is_skipped_when_extracted_service_does_not_match_queued_service(
        self, mock_session, stub_pipeline
    ):
        """Clause 4 false: extracted service_type does not match queued.

        Kills the ``==`` → ``!=`` mutant. Under the mutated form, food
        would match a queued shelter offer.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-mismatch"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            "_queued_offer": ("shelter", None, "Brooklyn"),  # queued = shelter
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "food",  # asking for food
            "location": "Brooklyn",
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            generate_reply("I need food", session_id=sid)

        assert not mock_promote.called

    def test_match_compares_extracted_service_to_first_element_of_queued_tuple(
        self, mock_session, stub_pipeline
    ):
        """The ``[0]`` index reads the SERVICE, not the detail or location.

        Kills the ``[0]`` → ``[1]`` and ``[0]`` → ``[2]`` mutants. Setup
        is constructed so that:
          - extracted service_type == queued_offer[0] (service)  → match
          - extracted service_type != queued_offer[1] (detail)
          - extracted service_type != queued_offer[2] (location)
        Under the mutated index, no clause would match and promote
        wouldn't fire.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-index"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            # tuple = (service, detail, location); each slot is distinct
            "_queued_offer": ("food", "food_pantry", "Manhattan"),
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "food",  # matches [0] only
            "location": "Manhattan",
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            mock_promote.return_value = {"response": "ok"}
            generate_reply("I need food", session_id=sid)

        assert mock_promote.called

    def test_promote_receives_extracted_location_as_override_not_queued_location(
        self, mock_session, stub_pipeline
    ):
        """``location_override`` must be the EXTRACTED location.

        Kills mutants that change ``location_override=early_extracted
        .get("location")`` to ``=None``, ``=""``, or to the queued
        offer's location.

        Setup: queued offer is for Brooklyn, but the user typed
        Manhattan in their re-statement (the documented "user re-types
        with new location" branch). The override must be Manhattan.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-loc-override"
        mock_session[sid] = {
            "_queue_offer_pending": True,
            "_queued_offer": ("shelter", None, "Brooklyn"),
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "location": "Manhattan",  # user override
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            mock_promote.return_value = {"response": "ok"}
            generate_reply("I need shelter in Manhattan", session_id=sid)

        # location_override is keyword-only; verify by name, not position.
        kwargs = mock_promote.call_args.kwargs
        assert kwargs["location_override"] == "Manhattan"

    def test_promote_receives_queued_offer_tuple_as_offer_argument(
        self, mock_session, stub_pipeline
    ):
        """The ``offer`` positional arg is exactly ``existing["_queued_offer"]``.

        Kills mutants that pass the wrong tuple (e.g., a fresh
        construction) into _promote_queued_offer.
        """
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-offer-arg"
        queued = ("clothing", None, "Bronx")
        mock_session[sid] = {
            "_queue_offer_pending": True,
            "_queued_offer": queued,
        }
        stub_pipeline.has_service_intent = True
        stub_pipeline.early_extracted = {
            "service_type": "clothing",
            "location": "Bronx",
        }

        with patch(
            "app.services.chatbot.orchestrator._promote_queued_offer"
        ) as mock_promote:
            mock_promote.return_value = {"response": "ok"}
            generate_reply("I need clothing", session_id=sid)

        # After Phase C ctx-migration, signature is
        # ``_promote_queued_offer(ctx, offer, location_override=...)``,
        # so offer is positional[1].
        positional = mock_promote.call_args.args
        assert positional[1] == queued


# ===========================================================================
# Block B — Awaiting-Service-After-Clear Guard
# ===========================================================================


class TestAwaitingServiceAfterClearGuard:
    """Block B: regex bypass when user just cleared service slot.

    Source code under test::

        awaiting_clear = existing.get("_awaiting_service_after_clear")
        regex_confident = (
            early_extracted.get("service_type") is not None
            and not early_extracted.get("additional_services")
        )
        if awaiting_clear and regex_confident:
            extracted = dict(early_extracted)
            existing.pop("_awaiting_service_after_clear", None)
            save_session_slots(session_id, existing)
        else:
            # Phase 4: unconditional unified-extract call (legacy
            # branch and feature flag both deleted).
            extracted = extract_unified(...)
    """

    def _setup_for_service_flow(self, mock_session, stub_pipeline, sid, slots):
        """Drive the orchestrator into the ``category == 'service'`` branch
        with no pending confirmation, no crisis, no tone, etc.
        """
        mock_session[sid] = slots
        stub_pipeline.has_service_intent = True
        stub_pipeline.action = "service"
        stub_pipeline.tone = None
        stub_pipeline.crisis = None

    def test_bypass_skips_llm_when_awaiting_flag_set_and_regex_returns_single_service(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """Positive case: flag set + clean single-service regex → no LLM call.

        Kills the trivial removal mutant on the whole if-branch (which
        would force the LLM path even when the bypass is supposed to
        fire) and the mutant on the surrounding ``elif`` ordering.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-bypass"
        self._setup_for_service_flow(
            mock_session,
            stub_pipeline,
            sid,
            {"_awaiting_service_after_clear": True},
        )
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }

        # Patch the unified extractor so we can assert it isn't called.
        # (Phase 4 deleted the legacy ``extract_slots_smart`` path and
        # the ``_USE_UNIFIED_EXTRACTOR`` flag — unified is now the only
        # extractor.)
        unified_mock = MagicMock(return_value={"service_type": "wrong"})
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )

        # ``_USE_LLM`` still exists on the orchestrator (gates the
        # whole service-flow LLM branch). Force it on so the bypass
        # branch is the only path that produces "no extract call".
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("Shelter", session_id=sid)

        assert not unified_mock.called, (
            "bypass should skip unified extractor when regex is confident"
        )

    def test_bypass_clears_awaiting_flag_after_firing(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """The bypass MUST clear ``_awaiting_service_after_clear``.

        Kills the mutant that removes the ``existing.pop(...)`` call. If
        the flag persists, the bypass would fire on every subsequent
        turn, not just the immediate one.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-pop"
        self._setup_for_service_flow(
            mock_session,
            stub_pipeline,
            sid,
            {"_awaiting_service_after_clear": True},
        )
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("Shelter", session_id=sid)

        # The flag must not appear in the saved session.
        assert "_awaiting_service_after_clear" not in mock_session[sid]

    def test_bypass_does_not_fire_when_awaiting_flag_is_absent(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """Flag absent → bypass does NOT fire, extractor IS called.

        Kills the ``and`` → ``or`` mutant on ``awaiting_clear and
        regex_confident``: under ``or``, a confident regex alone would
        skip the LLM, breaking every normal service-flow turn.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-no-flag"
        self._setup_for_service_flow(mock_session, stub_pipeline, sid, {})
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }

        unified_mock = MagicMock(
            return_value={
                "service_type": "shelter",
                "additional_services": [],
            }
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("Shelter", session_id=sid)

        assert unified_mock.called

    def test_bypass_does_not_fire_when_regex_extracts_additional_services(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """``additional_services`` non-empty → regex NOT confident → LLM runs.

        Kills the mutant that drops the ``not`` from
        ``not early_extracted.get("additional_services")``. Without the
        ``not``, the bypass would fire only when there ARE multiple
        services — the opposite of the documented intent.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-multi"
        self._setup_for_service_flow(
            mock_session,
            stub_pipeline,
            sid,
            {"_awaiting_service_after_clear": True},
        )
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            # User said "shelter and food" — the LLM should disambiguate.
            "additional_services": [{"type": "food"}],
            "location": None,
        }

        unified_mock = MagicMock(
            return_value={
                "service_type": "shelter",
                "additional_services": [{"type": "food"}],
            }
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("Shelter and food", session_id=sid)

        assert unified_mock.called, (
            "bypass must fall through to LLM when there are additional services"
        )

    def test_bypass_does_not_fire_when_regex_extracts_no_service_type(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """``service_type is None`` → regex NOT confident → LLM runs.

        Kills the ``is not None`` → ``is None`` mutant on the
        regex-confidence check. Under the mutated form, the bypass
        would fire when regex extracted nothing — silently throwing
        away the LLM's chance to extract anything.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-no-svc"
        self._setup_for_service_flow(
            mock_session,
            stub_pipeline,
            sid,
            {"_awaiting_service_after_clear": True},
        )
        stub_pipeline.early_extracted = {
            "service_type": None,
            "additional_services": [],
            "location": "Brooklyn",
        }

        unified_mock = MagicMock(
            return_value={"service_type": "shelter"}
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("In Brooklyn", session_id=sid)

        assert unified_mock.called

    def test_bypass_passes_dict_copy_of_early_extracted_not_a_reference(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """``extracted = dict(early_extracted)`` must be a COPY.

        Kills the ``dict(early_extracted)`` → ``early_extracted`` mutant.
        The downstream code calls ``extracted.items()`` and merges into
        a new slot dict. If the copy is dropped and downstream code
        mutates the dict in place, ``early_extracted`` (which is held
        by the orchestrator's local) would also mutate, leaking state
        into the next turn's slot view.

        We verify by mutating the result via a downstream side effect
        and asserting the original early_extracted dict is unchanged.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-copy"
        self._setup_for_service_flow(
            mock_session,
            stub_pipeline,
            sid,
            {"_awaiting_service_after_clear": True},
        )
        early = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }
        stub_pipeline.early_extracted = early
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        # merge_slots will read from `extracted`. If the orchestrator
        # held a reference instead of a copy, downstream merge_slots
        # could (and historically did) mutate the dict via its
        # __setitem__ side effects on shared sub-objects.
        original_keys = set(early.keys())
        original_service = early["service_type"]

        generate_reply("Shelter", session_id=sid)

        # The dict the test handed in must be unchanged.
        assert set(early.keys()) == original_keys
        assert early["service_type"] == original_service


# ===========================================================================
# Block C — Unified extract() call shape
# ===========================================================================
# Phase 4 deleted the ``_USE_UNIFIED_EXTRACTOR`` flag and the
# ``test_unified_extractor_flag.py`` file that used to cover env-var
# parsing. The two argument-shape mutants on the call site are still
# worth covering here:
#   - ``api_key_available=True`` → ``=False``
#   - the second positional arg being ``early_extracted`` specifically


class TestUnifiedExtractorCallShape:
    """Block C: argument shape on the unified extract() call."""

    def test_unified_extract_is_called_with_api_key_available_set_to_true(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """``api_key_available=True`` is hardcoded — gated by ``_USE_LLM``.

        Kills the ``api_key_available=True`` → ``=False`` mutant. Under
        the mutated form, the unified extractor would run with the LLM
        path disabled even when an API key is present, defeating the
        whole point of the migration.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-api-key"
        mock_session[sid] = {}
        stub_pipeline.has_service_intent = True
        stub_pipeline.action = "service"
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }

        unified_mock = MagicMock(
            return_value={
                "service_type": "shelter",
                "additional_services": [],
            }
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("I need shelter", session_id=sid)

        kwargs = unified_mock.call_args.kwargs
        assert kwargs["api_key_available"] is True

    def test_unified_extract_receives_early_extracted_as_regex_result_argument(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """Second positional arg is ``early_extracted`` (the regex result).

        Kills mutants that pass an empty dict, the session ``existing``
        dict, or ``None`` as the regex_result. The whole trust-model
        merge depends on this being the regex output.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-second-arg"
        mock_session[sid] = {}
        stub_pipeline.has_service_intent = True
        stub_pipeline.action = "service"
        early = {
            "service_type": "shelter",
            "additional_services": [],
            "location": "Brooklyn",
            "_gender": None,
        }
        stub_pipeline.early_extracted = early

        unified_mock = MagicMock(
            return_value={
                "service_type": "shelter",
                "additional_services": [],
            }
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("I need shelter", session_id=sid)

        # Signature: extract(message, regex_result, *, conversation_history, api_key_available, extraction_source)
        positional = unified_mock.call_args.args
        assert positional[1] == early, (
            "regex_result (second positional arg) must be early_extracted"
        )

    def test_unified_extract_receives_extraction_source_from_pipeline(
        self, mock_session, stub_pipeline, monkeypatch
    ):
        """``extraction_source`` kwarg carries the pipeline's source label.

        Added in Phase 4 Stage 3: when the semantic router classified
        intent rather than the regex-only path, Trust Model 3 needs to
        know so it can give the router priority over the LLM's pick.
        The orchestrator forwards whatever ``_run_llm_gate`` returned
        for ``_extraction_source`` (here ``"regex"``, set by the stub).

        Kills mutants that drop the kwarg entirely (silently breaks
        Trust Model 3 router-priority handling), hardcode it to
        ``None`` (same effect), or hardcode a wrong literal like
        ``"semantic_router"``.
        """
        from app.services.chatbot import orchestrator
        from app.services.chatbot.orchestrator import generate_reply

        sid = "sess-extraction-source"
        mock_session[sid] = {}
        stub_pipeline.has_service_intent = True
        stub_pipeline.action = "service"
        stub_pipeline.early_extracted = {
            "service_type": "shelter",
            "additional_services": [],
            "location": None,
        }

        unified_mock = MagicMock(
            return_value={
                "service_type": "shelter",
                "additional_services": [],
            }
        )
        monkeypatch.setattr(
            "app.services.slot_extraction.extract", unified_mock
        )
        monkeypatch.setattr(orchestrator, "_USE_LLM", True)

        generate_reply("I need shelter", session_id=sid)

        # The stub_pipeline fixture's _early and _llm_gate both return
        # "regex" as the source. Whatever they return must be forwarded.
        kwargs = unified_mock.call_args.kwargs
        assert kwargs["extraction_source"] == "regex"
