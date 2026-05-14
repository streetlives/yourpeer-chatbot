"""Orchestrator — generate_reply() dispatcher.

This module owns only the top-level routing logic: run the pipeline,
build context, dispatch to the right handler, then fall through to the
slot-filling / confirmation / execution path for service requests.

All the heavy lifting is delegated:

  * pipeline.py       — classification, PII redaction, LLM gate
  * tone.py           — tone-prefix computation
  * execution.py      — DB query + population fallback + response build
  * handlers/         — one handler per routing category
  * logging.py        — audit-log wrapper
  * context.py        — MessageContext, _empty_reply, _DISPLAY_PAGE_SIZE, _USE_LLM
"""

import logging
import uuid

from app.services.classifier import (
    _CRISIS_NOT_CHECKED,
    _classify_action,
    _classify_tone,
)
from app.services.confirmation import (
    _build_confirmation_message,
    _confirmation_quick_replies,
    _follow_up_quick_replies,
)
from app.services.crisis_detector import detect_crisis  # noqa: F401  (re-exported)
from app.services.phrase_lists import _WELCOME_QUICK_REPLIES
from app.services.session_store import (
    get_session_slots,
    save_session_slots,
)
from app.services.slot_extraction_regex import (
    NEAR_ME_SENTINEL,
    is_enough_to_answer,
    merge_slots,
    next_follow_up_question,
)
from app.services import slot_extraction

from .context import MessageContext, _USE_LLM, _empty_reply
from .execution import _execute_and_respond
from .handlers import (
    _handle_bot_capability_question,
    _handle_bot_identity,
    _handle_change_location_request,
    _handle_change_service_request,
    _handle_confused,
    _handle_context_aware_confirm,
    _handle_correction,
    _handle_crisis,
    _handle_demographic_skip,
    _handle_emotional,
    _handle_escalation,
    _handle_frustration,
    _handle_general_conversation,
    _handle_greeting,
    _handle_help,
    _handle_location_unknown,
    _handle_negative_preference,
    _handle_pending_confirmation,
    _handle_post_pending_confirmation,
    _handle_post_results_interaction,
    _handle_reset,
    _handle_spanish_detection,
    _handle_thanks,
    _immigration_acknowledgment,
    _promote_queued_offer,
)
from .pipeline import (
    _apply_pii_warning,
    _apply_session_geo,
    _compute_routing_category,
    _redact_with_safety_warning,
    _run_early_extraction,
    _run_llm_gate,
)
from .contextual_acknowledgments import _combined_contextual_acknowledgments
from .logging import _log_turn
from .result_builder import _build_follow_up_response
from .session_helpers import (
    _append_to_transcript,
    _clear_awaiting_service_after_clear,
    _clear_stale_last_action,
    _consume_last_action,
    _persist_emotional_context_early,
    _persist_emotional_context_late,
    _update_queued_services,
)
from .tone import _compute_tone_prefix


logger = logging.getLogger(__name__)


def generate_reply(
    message: str,
    session_id: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    request_id: str | None = None,
    source: str | None = None,
) -> dict:
    """Top-level message dispatch.

    ``source`` indicates how the user submitted this message:
      • ``"quick_reply"`` — tap on a bot-emitted button pill
      • ``"typed"``       — keyboard input via the chat input field
      • ``None``          — legacy clients pre-dating the field; treated
                            as "typed" for the confirmation-skip
                            optimization (conservative default), but
                            preserved as None in audit logs so analytics
                            can distinguish "user typed" from
                            "client predates field."
    Drives two downstream behaviors:
      1. Audit log per-turn ``source`` field (admin analytics:
         tap-vs-type ratio, navigation patterns).
      2. The session-level ``_pure_tap_session`` slot, which is True iff
         every message in this session was a quick-reply tap. When True,
         the canonical confirmation gate below (search-detail confirmation
         "Yes, search") is skipped because the user's selections are all
         canonical button values — the prompt is friction without value.
         Triggered by user feedback that tapping "Yes, search" after
         already tapping service-type and location pills felt repetitive.
    """
    if not session_id:
        session_id = str(uuid.uuid4())
    if not request_id:
        request_id = str(uuid.uuid4())

    # Stamp this request's session_id onto the audit-log contextvar.
    # `record_llm_call` (in audit_log.py) reads it as a fallback when
    # the caller doesn't pass session_id explicitly — which is every
    # current call site. Without this, `avg_calls_per_session` in the
    # admin metrics is always None because no LLM call has ever had a
    # non-empty session_id. ContextVar propagates through async
    # coroutines and FastAPI's threadpool bridge automatically, so
    # the six call sites deep in the call tree (slot extraction,
    # crisis Stage 2, post-results classify, filter-keyword extract,
    # conversational reply) all pick it up without signature changes.
    # See May 2026 admin-metrics investigation.
    from app.services.audit_log import (
        set_session_id_context,
        set_message_source_context,
    )
    set_session_id_context(session_id)
    # Same pattern for the message-origin field: every `_log_turn(...)`
    # exit point across ~55 handler branches picks up `source` from this
    # contextvar without each handler having to plumb it explicitly.
    # See audit_log.set_message_source_context and the
    # `log_conversation_turn` resolution logic for the read side.
    set_message_source_context(source)

    logger.info(f"[req:{request_id}] Session {session_id}: processing message")

    # --- Empty message guard ---
    if not message or not message.strip():
        # SMELL-9 resolution: every other return path in `generate_reply`
        # eventually flows through `_log_turn` (directly or via a handler).
        # Without this call, the audit feed has no record that the bot
        # ever saw an empty message — admin dashboards undercount turns
        # and the "user sent nothing → bot replied with welcome prompt"
        # case is invisible. `_log_turn` is wrapped in try/except, so an
        # audit-log failure can't break the user-facing path.
        empty_reply = _empty_reply(
            session_id,
            "What are you looking for today? I can help with food, "
            "shelter, clothing, health care, and more.",
            get_session_slots(session_id),
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(
            session_id, "", empty_reply, "empty_message",
            request_id=request_id, tone=None,
        )
        return empty_reply

    # --- Local-naming convention (SMELL-2 resolution) ---
    # Several locals in this function carry a leading underscore — e.g.,
    # `_pii_warning`, `_extraction_source`, `_action_pre`, `_post_result`,
    # `_spanish_acknowledgment`. Python's standard convention reserves
    # the leading underscore for module-private *names*, not function
    # locals; the orchestrator uses it as a visual marker for transient
    # pipeline state that is consumed within `generate_reply` and is
    # NOT promoted onto `MessageContext`. (Values that DO promote use
    # ctx fields directly — e.g. `ctx.tone`, `ctx.snapshot_*`.) The
    # convention is intentionally preserved here; see PHASE_AC_AFTERMATH
    # SMELL-2 for the design discussion and the option-(b) decision to
    # keep it as a semantic marker rather than sweep-rename.

    # --- PII Redaction + Safety Warning ---
    # When a user shares highly sensitive PII (SSN, phone), the warning
    # is prepended to the eventual response. The message is still processed
    # with PII redacted.
    redacted_message, _pii_warning, pii_detections = _redact_with_safety_warning(message)
    if pii_detections:
        logger.info(
            f"Session {session_id}: redacted {len(pii_detections)} PII item(s) "
            f"from message: {[d.pii_type for d in pii_detections]}"
        )

    existing = get_session_slots(session_id)

    # Store browser geolocation coords in session if provided.
    _apply_session_geo(session_id, existing, latitude, longitude)

    # --- PURE-TAP SESSION TRACKING ---
    # `_pure_tap_session` is True iff every user message in this session
    # was a quick-reply tap. Defaults to True (via .get(..., True)) so
    # fresh sessions start optimistic; flips permanently to False the
    # first time we see a typed message (or a message with unknown
    # source — legacy clients are treated as typed for the skip
    # decision). Once False, stays False until session reset. Read by
    # the confirmation gate below to decide whether the "Yes, search"
    # prompt is friction worth skipping. Persisted only on the
    # transition to False to keep the audit-log slot history clean
    # (the optimistic default doesn't need a write).
    if source != "quick_reply":
        if existing.get("_pure_tap_session", True):
            existing["_pure_tap_session"] = False
            save_session_slots(session_id, existing)

    # --- EARLY SLOT EXTRACTION (regex + semantic, before LLM gate) ---
    early_extracted, _extraction_source = _run_early_extraction(message, session_id)

    has_service_intent = (
        early_extracted.get("service_type") is not None
        or early_extracted.get("org_name") is not None
    )

    # --- CLASSIFY ACTION (regex, instant) ---
    _action_pre = _classify_action(message)

    # --- UNIFIED LLM CLASSIFICATION GATE ---
    # Pass redacted_message so the classifier's internal detect_crisis()
    # (sentinel-triggered) uses redacted text under the flag. Without
    # this, the leak would route through _classify_tone -> detect_crisis
    # -> Anthropic, bypassing the orchestrator's own detect_crisis fix
    # below. The second _classify_tone call further down passes a
    # pre-computed crisis_result so it doesn't hit detect_crisis at
    # all and doesn't need redacted_text.
    _regex_tone_pre = _classify_tone(
        message,
        crisis_result=_CRISIS_NOT_CHECKED,
        redacted_text=redacted_message,
    )
    has_service_intent, _action_pre, _extraction_source, _llm_tone, _llm_action, _unified = _run_llm_gate(
        message=message,
        early_extracted=early_extracted,
        has_service_intent=has_service_intent,
        action_pre=_action_pre,
        regex_tone_pre=_regex_tone_pre,
        extraction_source=_extraction_source,
        # Pre-LLM redaction (Phase 1 of PRE_LLM_REDACTION_SCOPE.md): pass
        # both raw and redacted text to the gate. Local decisions (length
        # check, gate condition) keep using raw to avoid masking behavior;
        # only the LLM payload swaps to redacted when the flag is on. With
        # the flag off (default), the gate sends raw — bit-for-bit
        # identical to pre-Phase-1 behavior.
        redacted_message=redacted_message,
    )

    # --- CRISIS DETECTION ---
    _is_safe_short = (
        _action_pre in (
            "confirm_yes", "confirm_deny", "confirm_change_service",
            "confirm_change_location", "reset", "greeting", "thanks",
            "bot_identity",
        )
        and len(message.split()) <= 4
    )
    # Pre-LLM redaction (Phase 1): when the flag is on, send the redacted
    # version to detect_crisis. Stage 1 (regex) is unaffected — crisis
    # phrases like "want to die" / "he hits me" don't overlap with the
    # redactor's structural patterns ([PHONE], [ADDRESS], [NAME], etc.).
    # Stage 2 (Anthropic Sonnet on ambiguous language) sees the redacted
    # input. Phase 2 eval explicitly verifies that crisis-detection
    # scenarios still pass with redacted input — see
    # ``pre_llm_redact_crisis_indirect`` in eval_llm_judge.py.
    # Phase 4: pre-LLM redaction is mandatory — the flag has been removed.
    # `redacted_message` is the only path; the `is not None` guard remains
    # because the kwarg signature still permits None for callers who don't
    # need the redacted form (no current callers rely on this, but the
    # defensive guard preserves call-site flexibility for future ones).
    _crisis_input = redacted_message if redacted_message is not None else message
    _crisis_result = detect_crisis(_crisis_input, skip_llm=_is_safe_short)

    if _crisis_result is not None:
        tone = "crisis"
    else:
        tone = _classify_tone(message, crisis_result=_crisis_result)
        if tone is None and _llm_tone:
            tone = _llm_tone

    # --- COMBINE INTO ROUTING CATEGORY ---
    # Computed here (instead of after the fast paths below) so that
    # MessageContext can be constructed before the queue-accept and
    # post-results fast paths run. Those fast paths now take ``ctx``,
    # consistent with all other handlers. ``_compute_routing_category``
    # depends only on tone/action/has_service_intent/early_extracted/
    # extraction_source/message, all finalized by this point.
    action = _action_pre
    category, _confidence, _confidence_reason = _compute_routing_category(
        tone=tone,
        action=action,
        has_service_intent=has_service_intent,
        early_extracted=early_extracted,
        extraction_source=_extraction_source,
        message=message,
    )

    # Clear stale _last_action when the user shifts context.
    # _last_action is set by emotional/escalation/crisis/confused/frustration
    # handlers and consumed by _handle_context_aware_confirm for the NEXT
    # confirm_yes or confirm_deny. If the user sends anything else (greeting,
    # help, thanks, a new service request), the context has shifted and
    # _last_action should not persist — otherwise it would incorrectly
    # affect a confirm_yes/confirm_deny many turns later.
    #
    # Safe to run before the post-results fast path: the fast path's
    # ``not ctx.existing.get("_last_action")`` guard inside
    # ``_handle_post_results_interaction`` only fires when
    # ``ctx.action in ("confirm_yes", "confirm_deny")``, and for those
    # actions ``category`` is also confirm_yes/confirm_deny (set above).
    # Since both are in ``_CONSUMES_LAST_ACTION``,
    # ``_clear_stale_last_action`` is a no-op for the case the fast
    # path cares about. See ``test_routing_category_order.py`` for the
    # precedence guarantee.
    _clear_stale_last_action(session_id, existing, category)

    # --- Build MessageContext for handler dispatch ---
    # ORCHESTRATOR_AUDIT.md PR-α: handlers progressively migrating from
    # positional args to ctx-only signatures. Constructed here (earliest
    # point all not-late-set fields are available) so it's visible to
    # the queue-accept/post-results fast paths and the crisis dispatch
    # below. Two fields are LATE-SET via direct attribute assignment
    # as their values are computed downstream:
    #   * spanish_acknowledgment — set after _handle_spanish_detection
    #   * tone_prefix            — set after _compute_tone_prefix
    #   * merged                 — set after merge_slots in service flow
    # Handlers that fire before each setter runs see the default ("" / None)
    # — that's safe because no handler reads a field before it's populated.
    ctx = MessageContext(
        session_id=session_id,
        request_id=request_id,
        message=message,
        redacted_message=redacted_message,
        pii_warning=_pii_warning,
        existing=existing,
        category=category,
        action=action,
        tone=tone,
        confidence=_confidence,
        confidence_reason=_confidence_reason,
        extraction_source=_extraction_source,
        early_extracted=early_extracted,
        has_service_intent=has_service_intent,
        crisis_result=_crisis_result,
        last_results=existing.get("_last_results"),
        is_confirmation_action=action in (
            "confirm_yes", "confirm_deny", "confirm_change_service",
            "confirm_change_location", "reset", "greeting",
        ),
        latitude=latitude,
        longitude=longitude,
        spanish_acknowledgment="",         # late-set after spanish detection
        tone_prefix="",                    # late-set after _compute_tone_prefix
        merged=None,                       # late-set after merge_slots (service flow)
        unified_extraction=_unified,       # cached gate output (None if gate didn't fire)
    )

    # Capture ``tone`` BEFORE the negative_preference B.2 promotion block
    # below (which may reassign ``tone`` from None to "frustrated").
    # Several downstream sites need the PRE-promotion tone:
    #   * the late ``_compute_tone_prefix`` recomputation in the service
    #     branch, which deliberately recomputes with the original tone
    #     after correcting only ``is_service_flow``.
    #   * ``_handle_post_pending_confirmation``'s nudge-prefix selection,
    #     which should reflect the user's original emotional disclosure.
    # See ``MessageContext.snapshot_response_tone`` for the contract.
    ctx.snapshot_response_tone = tone

    # Crisis tone skips the queue-accept and post-results fast paths —
    # those checks are non-meaningful for a user in active crisis and the
    # message should fall through to crisis routing below. Flipped from
    # `if tone == "crisis": pass else: …` per SMELL-5 resolution.
    if tone != "crisis":
        # --- QUEUE-ACCEPT FAST PATH ---
        # When the user has a pending queue offer (from a prior multi-
        # intent search) AND the current message's extracted service
        # matches the offered service, treat this as queue-accept: promote
        # the offer and search immediately. Covers both the button-click
        # path (quick reply sends "I need <queued_service> in <loc>") and
        # the typed re-statement path ("I need food").
        #
        # Must run BEFORE `_handle_post_results_interaction` because that
        # handler wipes `_queue_offer_pending` and other queue state when
        # it sees `has_service_intent` (it treats any new service intent
        # as a fresh search). It must also run BEFORE the normal service
        # flow, whose `merge_slots` hits its "service change" guard
        # (`slot_extraction_regex.py:1804-1809`) and also wipes queue state,
        # resulting in a redundant re-confirmation of the queued service.
        #
        # Without this fast path, `multi_accept_queued_shelter`-style
        # flows score a critical failure because the scenario expects an
        # immediate search on turn 3 ("I need food" accepts the queued
        # offer), not another "I'll look for food in Brooklyn — sound
        # right?" turn.
        #
        # Delegates to `_promote_queued_offer`, the same helper the
        # `confirm_yes`-on-queue path uses — both are "user accepts the
        # queued offer," expressed either as a bare `yes` or as a
        # service-phrased sentence.
        if (has_service_intent
                and existing.get("_queue_offer_pending")
                and existing.get("_queued_offer")
                and early_extracted.get("service_type") == existing["_queued_offer"][0]):
            offer = existing["_queued_offer"]
            return _apply_pii_warning(
                _pii_warning,
                _promote_queued_offer(
                    ctx, offer,
                    location_override=early_extracted.get("location"),
                ),
            )

        # --- POST-RESULTS QUESTION CHECK ---
        # Walrus form (SMELL-8): the assignment-and-truth-check stay on
        # one line; handler returns dict|None so the truthy check is
        # equivalent to `is not None`.
        if _post_result := _handle_post_results_interaction(ctx):
            return _apply_pii_warning(_pii_warning, _post_result)

    # === ROUTE TO HANDLERS ===

    # --- Crisis ---
    if category == "crisis":
        result = _handle_crisis(ctx)
        if result:
            return result
        # If _crisis_result was None (classification disagreed), fall through
        category = "general"
        ctx.category = "general"

    # --- Spanish / non-English detection ---
    _spanish_result, _spanish_acknowledgment = _handle_spanish_detection(ctx)
    if _spanish_result:
        return _apply_pii_warning(_pii_warning, _spanish_result)
    # Late-set: read in the service-flow prefix-injection block below.
    ctx.spanish_acknowledgment = _spanish_acknowledgment

    # --- Tone prefix (computed early so help/confused/emotional handlers
    # can use it too, not just service-flow responses). The sensitive-
    # context override in `_compute_tone_prefix` fires unconditionally
    # when the message matches _SENSITIVE_CONTEXT_RE — so even when the
    # user's question routes to help/confused (e.g. "aging out of foster
    # care, what do I do"), the empathic acknowledgment still applies.
    #
    # Computed with is_service_flow=(category == "service") as it
    # currently stands. If B.2 below promotes negative_preference →
    # service, the baseline-warmth / response_tone branches of the
    # prefix would differ — but sensitive context (the reason we
    # moved this up) is unconditional, so the promoted case still
    # works correctly with the early computation.
    _is_service_flow = category == "service"
    _tone_prefix, _emotional_context_update = _compute_tone_prefix(
        message=message,
        # Read from the snapshot for uniformity with the late
        # ``_compute_tone_prefix`` call below — at this point in
        # ``generate_reply`` ``tone`` and ``ctx.snapshot_response_tone``
        # are equal (the negative_preference promotion hasn't run yet),
        # so this is functionally identical, but using the snapshot
        # makes both call sites read from the same documented contract.
        response_tone=ctx.snapshot_response_tone,
        is_service_flow=_is_service_flow,
        prior_emotional_context=existing.get("_emotional_context"),
    )
    # Late-set: meta + service handlers read this.
    ctx.tone_prefix = _tone_prefix

    # Persist emotional context for subsequent turns (needed here, not
    # just at the service-flow site below, because help/confused
    # handlers can now set sensitive context on the first turn).
    _persist_emotional_context_early(session_id, existing, _emotional_context_update)

    # NOTE: PII safety warnings.
    # Every category-specific handler return below is wrapped with
    # `_apply_pii_warning(_pii_warning, ...)`. The wrap is a no-op when
    # `_pii_warning` is "" (the common case — most messages don't carry
    # SSN or phone), so the wrap is essentially free. When the user did
    # share warning-worthy PII (turn 3 of `pre_llm_redact_phone_in_followup`
    # is the canonical example), the warning is prepended regardless of
    # which handler the message routes through.
    #
    # Crisis is the one path that does NOT get wrapped — `_handle_crisis`
    # already prepends safety resources (988, etc.), and prepending a
    # privacy warning ahead of those would invert the priority order.
    # User safety > privacy reminder.
    #
    # The late service-flow path (line ~640 below) prepends `_pii_warning`
    # via `_tone_prefix`. The `_apply_pii_warning` helper is idempotent —
    # if a response already starts with `_pii_warning`, the helper
    # returns it unchanged. Safe to wrap every site.

    # --- Reset ---
    if category == "reset":
        return _apply_pii_warning(_pii_warning, _handle_reset(ctx))

    # --- Correction ---
    if category == "correction":
        return _apply_pii_warning(_pii_warning, _handle_correction(ctx))

    # --- Negative preference ---
    if category == "negative_preference":
        # B.2 compound-intent override: when the rejection message also
        # carries a concrete NEW service intent (different from the
        # existing primary), the user is telling us which direction to
        # pivot, not asking for an open menu. Example:
        #     "I already tried those, I need shelter instead"
        # Pre-B.2, B.1's phrase match would drop the 'shelter' intent
        # and show a menu. Here we downgrade the action to the service
        # flow and promote frustration tone so the resulting
        # confirmation acknowledges that the prior search didn't help.
        # When the rejection stands alone (no new service intent, or
        # user is refining the same service_type), the negative_preference
        # handler still fires as before.
        _new_service = early_extracted.get("service_type")
        if _new_service and _new_service != existing.get("service_type"):
            category = "service"
            if tone is None:
                tone = "frustrated"
            # Keep ctx in sync with local promotion so downstream ctx-using
            # handlers see the correct category/tone (the local var and
            # ctx.* must agree — see ORCHESTRATOR_AUDIT.md PR-α invariants).
            ctx.category = category
            ctx.tone = tone
            # Fall through to normal service routing below.
        else:
            return _apply_pii_warning(_pii_warning, _handle_negative_preference(ctx))

    # --- Greeting ---
    if category == "greeting":
        return _apply_pii_warning(_pii_warning, _handle_greeting(ctx))

    # --- Thanks ---
    if category == "thanks":
        return _apply_pii_warning(_pii_warning, _handle_thanks(ctx))

    # --- Help ---
    if category == "help":
        return _apply_pii_warning(_pii_warning, _handle_help(ctx))

    # --- Bot Identity ---
    if category == "bot_identity":
        return _apply_pii_warning(_pii_warning, _handle_bot_identity(ctx))

    # --- Bot capability questions ---
    if category == "bot_question":
        return _apply_pii_warning(_pii_warning, _handle_bot_capability_question(ctx))

    # --- Demographic skip ("I'd rather not say" / "skip") ---
    # SAMHSA Empowerment principle: users control what they share.
    if _demo_skip_result := _handle_demographic_skip(ctx):
        return _apply_pii_warning(_pii_warning, _demo_skip_result)

    # --- Location unknown ---
    if _loc_unknown_result := _handle_location_unknown(ctx):
        return _apply_pii_warning(_pii_warning, _loc_unknown_result)

    # --- Confused / Overwhelmed ---
    if category == "confused":
        return _apply_pii_warning(_pii_warning, _handle_confused(ctx))

    # --- Emotional expression ---
    if category == "emotional":
        return _apply_pii_warning(_pii_warning, _handle_emotional(ctx))

    # --- Frustration ---
    if category == "frustration":
        return _apply_pii_warning(_pii_warning, _handle_frustration(ctx))

    # --- Escalation ---
    if category == "escalation":
        return _apply_pii_warning(_pii_warning, _handle_escalation(ctx))

    # --- Context-aware "yes" / "no" handling ---
    # Snapshot _last_action onto ctx so the handler can dispatch on the
    # pre-mutation value (the handler pops _last_action on confirm_yes
    # paths) and so the post-handler ``_consume_last_action`` call below
    # sees the same value the handler dispatched on. See
    # ``MessageContext.snapshot_last_action`` for the contract.
    ctx.snapshot_last_action = existing.get("_last_action")
    if context_result := _handle_context_aware_confirm(ctx):
        return _apply_pii_warning(_pii_warning, context_result)

    # Clear the last_action tracker now that we've checked it
    _consume_last_action(session_id, existing, ctx.snapshot_last_action)

    # --- Handle "change location" / "change service" outside pending ---
    if not existing.get("_pending_confirmation"):
        if category == "confirm_change_location":
            return _apply_pii_warning(_pii_warning, _handle_change_location_request(ctx))
        if category == "confirm_change_service":
            return _apply_pii_warning(_pii_warning, _handle_change_service_request(ctx))

    # --- Handle confirmation responses ---
    # Snapshot _pending_confirmation: handler pops it on confirm paths;
    # the ``if pending:`` guard below must see the pre-mutation value.
    ctx.snapshot_pending = existing.get("_pending_confirmation")
    if confirm_result := _handle_pending_confirmation(ctx):
        return _apply_pii_warning(_pii_warning, confirm_result)

    # If pending confirmation but user typed something new
    if ctx.snapshot_pending:
        # ``ctx.snapshot_response_tone`` was captured EARLY (before the
        # negative_preference promotion block) so it reflects the
        # original tone classification, not any post-promotion override.
        # Don't overwrite it here.
        return _apply_pii_warning(_pii_warning, _handle_post_pending_confirmation(ctx))

    # --- Service request or general conversation ---
    if _USE_LLM and category == "service":
        # Post-change-service bypass: if the user just said "change
        # service" on the prior turn and regex confidently extracted a
        # single service on this turn (no ambiguity — no additional
        # services, service_type set), trust regex and skip the LLM.
        #
        # Rationale: the transcript stores only user messages, so the
        # bot's "What kind of help do you need?" prompt is missing from
        # the history the LLM sees. A bare "Shelter" reply then gets
        # interpreted with the stale prior request still in view, and
        # the LLM can bundle the cleared-out service back in as either
        # primary (with the new one as additional) or as additional
        # (with the new one as primary) — both break the expected
        # "service_type replaced" state. Covers confirm_multi_change.
        #
        # The guard on `early_extracted.additional_services` keeps this
        # from firing when the user names multiple services on this
        # turn ("shelter and food") — those cases still need the LLM.
        awaiting_clear = existing.get("_awaiting_service_after_clear")
        regex_confident = (
            early_extracted.get("service_type") is not None
            and not early_extracted.get("additional_services")
        )
        if awaiting_clear and regex_confident:
            extracted = dict(early_extracted)
            _clear_awaiting_service_after_clear(session_id, existing)
        elif ctx.unified_extraction is not None:
            # LLM-1: Reuse the gate's already-computed result. The gate
            # fired ``slot_extraction.extract()`` on this same message
            # (the only way ``unified_extraction`` gets populated), and
            # the second call here would invoke the same LLM with the
            # same regex_result for a near-identical (modulo non-
            # determinism) output. Skip it.
            #
            # See PHASE_AC_AFTERMATH.md `LLM-1` and the comment at
            # ``slot_extraction/__init__.py:162-163`` (the deferred
            # latency note that anticipated this fix).
            extracted = ctx.unified_extraction
        else:
            # Phase 4 (April 2026): the legacy `extract_slots_smart`
            # path was removed and the feature flag deleted; slot
            # extraction now always routes through the unified
            # `app.services.slot_extraction.extract()`. See
            # UNIFIED_EXTRACTOR_MIGRATION.md.
            #
            # Pass `extraction_source` so Trust Model 3 can give the
            # semantic router priority when its classification
            # disagrees with the LLM's pick (Phase 4 Stage 3
            # follow-up). When the source is "regex" or None, merge
            # behaves as before.
            #
            # Phase 4 close-out (May 2026): pre-LLM redaction is mandatory,
            # so this no longer branches on a flag — it sends redacted text
            # whenever it's available. The conversation_history kwarg below
            # already passes server-stored redacted text. See
            # docs/design/PRE_LLM_REDACTION_SCOPE.md.
            extracted = slot_extraction.extract(
                redacted_message if redacted_message is not None else message,
                early_extracted,
                conversation_history=existing.get("transcript", []),
                api_key_available=True,  # gated by _USE_LLM above
                extraction_source=_extraction_source,
            )
    else:
        extracted = early_extracted

    has_new_slots = any(v is not None and v != [] for k, v in extracted.items()
                        if k not in ("additional_services", "_populations", "_contradiction", "_is_additive", "tone", "action"))

    merged = merge_slots(existing, extracted)
    # Late-set: handlers that fire after merge_slots (notably
    # _handle_general_conversation at the function tail) read this.
    ctx.merged = merged

    _append_to_transcript(merged, redacted_message)
    _update_queued_services(merged, extracted, existing)

    save_session_slots(session_id, merged)

    # Geolocation readiness
    _has_session_coords = (
        merged.get("_latitude") is not None
        and merged.get("_longitude") is not None
    )
    _geolocation_ready = (
        bool(merged.get("service_type"))
        and merged.get("location") == NEAR_ME_SENTINEL
        and _has_session_coords
    )

    # --- Tone prefix refresh (B.2 promotion case) ---
    # We already computed `_tone_prefix` early for the help/confused
    # handlers. If B.2 promoted negative_preference → service, the
    # early prefix was computed with is_service_flow=False. Recompute
    # here with the corrected flag so the service-flow baseline-warmth
    # / response-tone prefix fires. Sensitive-context override still
    # wins last, so foster-care-type messages stay empathic either way.
    _is_service_flow = category == "service"
    _tone_prefix, _emotional_context_update = _compute_tone_prefix(
        message=message,
        # Use the early-captured snapshot — ``tone`` may have been
        # promoted to "frustrated" by the negative_preference B.2 block
        # above, but this recomputation should preserve the original
        # tone classification (only ``is_service_flow`` is being
        # corrected here).
        response_tone=ctx.snapshot_response_tone,
        is_service_flow=_is_service_flow,
        prior_emotional_context=existing.get("_emotional_context"),
    )

    # Persist emotional context update (may differ from the early
    # computation if B.2 promoted — e.g., shame prefix only fires for
    # service flow, so the context could transition from None early to
    # "shame" here, or "shame" early to "frustrated" here on the
    # negative_preference path).
    _persist_emotional_context_late(
        session_id, merged, existing, _emotional_context_update
    )

    # Prepend PII safety warning, Spanish acknowledgment, immigration-
    # context acknowledgment, and the four contextual acknowledgments
    # (personal-story warmth, PATH intake, rough sleeper outreach,
    # substance-use shelter framing) before the tone prefix so they
    # appear first in confirmations and follow-ups.
    _prefix_prepend = (
        _pii_warning
        + ctx.spanish_acknowledgment
        + _immigration_acknowledgment(merged)
        + _combined_contextual_acknowledgments(merged, redacted_message)
    )
    if _prefix_prepend:
        _tone_prefix = _prefix_prepend + _tone_prefix

    # Block confirmation when the LLM gate snapped to "other" with no
    # detail because regex AND semantic both missed — in that case the
    # routing layer has already classified this as a low-confidence
    # unrecognized request (category="general") and we're headed to
    # `_handle_general_conversation`'s tiered redirect. Without this
    # guard, the confirmation message ("I'll look for other services
    # in Staten Island — sound good?") fires for the helicopter-ride
    # case before the redirect ever runs. See
    # `TestUnrecognizedServiceLLMGateGuard` in
    # tests/integration/test_multi_turn_and_context.py.
    #
    # Pre-refactor (May 2026): this used to check the trio
    # ``(extraction_source == "llm_gate", service_type == "other",
    # not service_detail)`` inline at three call sites. The reason
    # field collapses that into one named state. See
    # ``ARCH_NOTES.md`` "Confidence reason refactor".
    _is_low_confidence_other_routing = _confidence_reason == "llm_reaching_other"

    # If enough detail → CONFIRMATION step
    if (
        (is_enough_to_answer(merged) or _geolocation_ready)
        and has_new_slots
        and not _is_low_confidence_other_routing
    ):
        # Pure-tap optimization: skip the "Yes, search" confirmation when
        # service AND location were both supplied via quick-reply taps in
        # the same session and the current turn is also a tap. User
        # research found the confirmation prompt repetitive once both
        # pills had already been tapped; the slot values came from
        # canonical bot-emitted buttons so there's no parsing
        # uncertainty to verify.
        #
        # Three invariants this code upholds (each is a separate guard
        # below, with code comments naming the rule it enforces):
        #
        # INVARIANT 1 — "service AND location both came from taps."
        #   Enforced by `_pure_tap_session=True` (no typed/unknown-source
        #   message has occurred in this session, so any slot fill must
        #   have come from a tap) AND explicit slot-presence checks below
        #   (service_type set, and either a concrete location or the
        #   geolocation-ready combo of NEAR_ME + coords). The explicit
        #   slot-presence check is what makes this skip narrower than
        #   `is_enough_to_answer`: org-name-only searches (which also
        #   satisfy `is_enough_to_answer`) deliberately do NOT skip,
        #   because the user spec named service+location as the trigger.
        #
        # INVARIANT 2 — "Start over should completely reset this."
        #   `_handle_reset` in handlers/meta.py calls clear_session(),
        #   which wipes _SESSION_STATE[session_id] and the persisted row.
        #   After reset, the next message's get_session_slots returns {},
        #   `_pure_tap_session` defaults back to True via .get(default).
        #   Nothing to do here — the guarantee comes from clear_session
        #   being thorough. Test: tap service → type "start over" →
        #   tap service → tap location → SKIPS (fresh streak).
        #
        # INVARIANT 3 — "Text inputs should never skip confirmation."
        #   Enforced by both: (a) any typed message flips
        #   `_pure_tap_session` to False permanently (~line 221), AND
        #   (b) the current turn must have source=="quick_reply". Belt
        #   and suspenders: layer (a) covers "user typed earlier in the
        #   session, then taps", layer (b) covers "user types the final
        #   message that happens to fully fill slots."
        #
        # Other unaffected paths:
        #   • Low-confidence routing — the existing
        #     `_is_low_confidence_other_routing` guard above already
        #     excludes the "snapped to 'other'" branch.
        #   • Crisis flows route through _handle_crisis / the
        #     confirm_yes branch of _handle_pending_confirmation before
        #     reaching this gate; their own auto-execute logic is
        #     unaffected.
        #   • Mid-flow change-location, additive-intent, topic-shift,
        #     and re-nudge confirmations live in handlers/confirmation.py
        #     and are NOT skipped — each has its own reason to confirm
        #     that the tap signal doesn't address.
        _is_pure_tap_streak = merged.get("_pure_tap_session", True)
        _current_is_tap = source == "quick_reply"
        _has_service = bool(merged.get("service_type"))
        # Location is "real" iff a concrete borough/neighborhood was set
        # OR the geolocation-ready combination (NEAR_ME sentinel +
        # browser coords) is present. NEAR_ME alone (no coords) is the
        # mid-flow state where the bot is still resolving location; it
        # does NOT qualify because the user hasn't actually picked a
        # location yet.
        _has_location = bool(
            (merged.get("location") and merged.get("location") != NEAR_ME_SENTINEL)
            or _geolocation_ready
        )
        _service_and_location_both_tapped = (
            _is_pure_tap_streak and _has_service and _has_location
        )
        if _service_and_location_both_tapped and _current_is_tap:
            merged.pop("_pending_confirmation", None)
            merged.pop("_queue_offer_pending", None)
            merged.pop("_queued_services_original", None)
            save_session_slots(session_id, merged)
            result = _execute_and_respond(
                session_id, message, merged, request_id=request_id,
            )
            _log_turn(
                session_id, redacted_message, result,
                "confirmation_skipped_pure_tap",
                request_id=request_id, tone=tone, source=source,
            )
            return result

        merged["_pending_confirmation"] = True
        merged.pop("_queue_offer_pending", None)
        merged.pop("_queued_services_original", None)
        save_session_slots(session_id, merged)

        confirm_msg = _tone_prefix + _build_confirmation_message(merged)
        return _build_follow_up_response(
            session_id=session_id,
            redacted_message=redacted_message,
            response_text=confirm_msg,
            merged=merged,
            quick_replies=_confirmation_quick_replies(merged),
            log_category="confirmation",
            request_id=request_id,
            tone=tone,
        )

    # Need more slots — service request
    if category == "service":
        follow_up = _tone_prefix + next_follow_up_question(merged)
        return _build_follow_up_response(
            session_id=session_id,
            redacted_message=redacted_message,
            response_text=follow_up,
            merged=merged,
            quick_replies=_follow_up_quick_replies(merged),
            log_category=category,
            request_id=request_id,
            tone=tone,
        )

    # Service flow continuation
    if has_new_slots and existing.get("service_type") and not existing.get("_pending_confirmation"):
        # NOTE: ``response_text`` does NOT include ``_tone_prefix`` here,
        # unlike the two follow-up paths above. The asymmetry is
        # pre-existing — see ``ORCHESTRATOR_AUDIT.md`` "Suspect 1" —
        # and is preserved by this refactor; whether to add the prefix
        # is an open product/UX question, not a code-shape question.
        #
        # PII warning, however, IS applied here (May 2026): the asymmetry
        # in the comment above is about whether the warmth-tone prefix
        # fires; the PII warning is a separate, unconditional safety
        # signal. A user sharing a phone number mid-service-flow should
        # be warned regardless of whether the warmth prefix is suppressed.
        follow_up = next_follow_up_question(merged)
        return _apply_pii_warning(
            _pii_warning,
            _build_follow_up_response(
                session_id=session_id,
                redacted_message=redacted_message,
                response_text=follow_up,
                merged=merged,
                quick_replies=_follow_up_quick_replies(merged),
                log_category="service",
                request_id=request_id,
                tone=tone,
            ),
        )

    # --- General conversation / unrecognized service ---
    return _apply_pii_warning(_pii_warning, _handle_general_conversation(ctx))
