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
from app.services.slot_extractor import (
    NEAR_ME_SENTINEL,
    is_enough_to_answer,
    merge_slots,
    next_follow_up_question,
)
from app.services import slot_extraction

from .context import MessageContext, _USE_LLM, _empty_reply
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
    _apply_session_geo,
    _compute_routing_category,
    _redact_with_safety_warning,
    _run_early_extraction,
    _run_llm_gate,
)
from .contextual_acknowledgments import _combined_contextual_acknowledgments
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
) -> dict:
    if not session_id:
        session_id = str(uuid.uuid4())
    if not request_id:
        request_id = str(uuid.uuid4())

    logger.info(f"[req:{request_id}] Session {session_id}: processing message")

    # --- Empty message guard ---
    if not message or not message.strip():
        return _empty_reply(
            session_id,
            "What are you looking for today? I can help with food, "
            "shelter, clothing, health care, and more.",
            get_session_slots(session_id),
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )

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

    # --- EARLY SLOT EXTRACTION (regex + semantic, before LLM gate) ---
    early_extracted, _extraction_source = _run_early_extraction(message, session_id)

    has_service_intent = (
        early_extracted.get("service_type") is not None
        or early_extracted.get("org_name") is not None
    )

    # --- CLASSIFY ACTION (regex, instant) ---
    _action_pre = _classify_action(message)

    # --- UNIFIED LLM CLASSIFICATION GATE ---
    _regex_tone_pre = _classify_tone(message, crisis_result=_CRISIS_NOT_CHECKED)
    has_service_intent, _action_pre, _extraction_source, _llm_tone, _llm_action = _run_llm_gate(
        message=message,
        early_extracted=early_extracted,
        has_service_intent=has_service_intent,
        action_pre=_action_pre,
        regex_tone_pre=_regex_tone_pre,
        extraction_source=_extraction_source,
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
    _crisis_result = detect_crisis(message, skip_llm=_is_safe_short)

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
    _response_tone = tone
    category, _confidence = _compute_routing_category(
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
    )

    if tone == "crisis":
        pass  # handled below in routing
    else:
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
        # (`slot_extractor.py:1804-1809`) and also wipes queue state,
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
            return _promote_queued_offer(
                ctx, offer,
                location_override=early_extracted.get("location"),
            )

        # --- POST-RESULTS QUESTION CHECK ---
        _post_result = _handle_post_results_interaction(ctx)
        if _post_result:
            return _post_result

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
    _spanish_result, _spanish_acknowledgment = _handle_spanish_detection(
        session_id, message, redacted_message, existing,
        has_service_intent, tone, request_id,
    )
    if _spanish_result:
        return _spanish_result
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
        response_tone=_response_tone,
        is_service_flow=_is_service_flow,
        prior_emotional_context=existing.get("_emotional_context"),
    )
    # Late-set: meta + service handlers read this.
    ctx.tone_prefix = _tone_prefix

    # Persist emotional context for subsequent turns (needed here, not
    # just at the service-flow site below, because help/confused
    # handlers can now set sensitive context on the first turn).
    _persist_emotional_context_early(session_id, existing, _emotional_context_update)

    # --- Reset ---
    if category == "reset":
        return _handle_reset(ctx)

    # --- Correction ---
    if category == "correction":
        return _handle_correction(ctx)

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
            return _handle_negative_preference(ctx)

    # --- Greeting ---
    if category == "greeting":
        return _handle_greeting(ctx)

    # --- Thanks ---
    if category == "thanks":
        return _handle_thanks(ctx)

    # --- Help ---
    if category == "help":
        return _handle_help(ctx)

    # --- Bot Identity ---
    if category == "bot_identity":
        return _handle_bot_identity(ctx)

    # --- Bot capability questions ---
    if category == "bot_question":
        return _handle_bot_capability_question(ctx)

    # --- Demographic skip ("I'd rather not say" / "skip") ---
    # SAMHSA Empowerment principle: users control what they share.
    _demo_skip_result = _handle_demographic_skip(ctx)
    if _demo_skip_result:
        return _demo_skip_result

    # --- Location unknown ---
    _loc_unknown_result = _handle_location_unknown(ctx)
    if _loc_unknown_result:
        return _loc_unknown_result

    # --- Confused / Overwhelmed ---
    if category == "confused":
        return _handle_confused(ctx)

    # --- Emotional expression ---
    if category == "emotional":
        return _handle_emotional(ctx)

    # --- Frustration ---
    if category == "frustration":
        return _handle_frustration(ctx)

    # --- Escalation ---
    if category == "escalation":
        return _handle_escalation(ctx)

    # --- Context-aware "yes" / "no" handling ---
    last_action = existing.get("_last_action")
    context_result = _handle_context_aware_confirm(ctx, last_action)
    if context_result:
        return context_result

    # Clear the last_action tracker now that we've checked it
    _consume_last_action(session_id, existing, last_action)

    # --- Handle "change location" / "change service" outside pending ---
    if not existing.get("_pending_confirmation"):
        if category == "confirm_change_location":
            return _handle_change_location_request(ctx)
        if category == "confirm_change_service":
            return _handle_change_service_request(ctx)

    # --- Handle confirmation responses ---
    pending = existing.get("_pending_confirmation")
    confirm_result = _handle_pending_confirmation(ctx, pending)
    if confirm_result:
        return confirm_result

    # If pending confirmation but user typed something new
    if pending:
        return _handle_post_pending_confirmation(ctx, _response_tone)

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
            extracted = slot_extraction.extract(
                message,
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
        response_tone=_response_tone,
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

    # If enough detail → CONFIRMATION step
    if (is_enough_to_answer(merged) or _geolocation_ready) and has_new_slots:
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
        follow_up = next_follow_up_question(merged)
        return _build_follow_up_response(
            session_id=session_id,
            redacted_message=redacted_message,
            response_text=follow_up,
            merged=merged,
            quick_replies=_follow_up_quick_replies(merged),
            log_category="service",
            request_id=request_id,
            tone=tone,
        )

    # --- General conversation / unrecognized service ---
    return _handle_general_conversation(ctx)
