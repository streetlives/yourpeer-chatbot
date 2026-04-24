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

from app.privacy.pii_redactor import redact_pii  # noqa: F401  (re-exported below)
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

from .context import _USE_LLM, _USE_UNIFIED_EXTRACTOR, _empty_reply
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
)
from .logging import _log_turn
from .pipeline import (
    _apply_session_geo,
    _compute_routing_category,
    _redact_with_safety_warning,
    _run_early_extraction,
    _run_llm_gate,
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
    has_coords = latitude is not None and longitude is not None  # noqa: F841
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

    if tone == "crisis":
        pass  # handled below in routing
    else:
        # --- POST-RESULTS QUESTION CHECK ---
        _post_result = _handle_post_results_interaction(
            session_id, message, redacted_message, existing,
            early_extracted, has_service_intent, _action_pre, tone, request_id,
        )
        if _post_result:
            return _post_result

    # --- COMBINE INTO ROUTING CATEGORY ---
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

    # === ROUTE TO HANDLERS ===

    # Clear stale _last_action when the user shifts context.
    # _last_action is set by emotional/escalation/crisis/confused/frustration
    # handlers and consumed by _handle_context_aware_confirm for the NEXT
    # confirm_yes or confirm_deny. If the user sends anything else (greeting,
    # help, thanks, a new service request), the context has shifted and
    # _last_action should not persist — otherwise it would incorrectly
    # affect a confirm_yes/confirm_deny many turns later.
    _CONSUMES_LAST_ACTION = {"confirm_yes", "confirm_deny"}
    if existing.get("_last_action") and category not in _CONSUMES_LAST_ACTION:
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)

    # --- Crisis ---
    if category == "crisis":
        result = _handle_crisis(
            session_id, message, redacted_message, existing,
            early_extracted, has_service_intent, _crisis_result,
            tone, request_id,
        )
        if result:
            return result
        # If _crisis_result was None (classification disagreed), fall through
        category = "general"

    # --- Spanish / non-English detection ---
    _spanish_result, _spanish_acknowledgment = _handle_spanish_detection(
        session_id, message, redacted_message, existing,
        has_service_intent, tone, request_id,
    )
    if _spanish_result:
        return _spanish_result

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

    # Persist emotional context for subsequent turns (needed here, not
    # just at the service-flow site below, because help/confused
    # handlers can now set sensitive context on the first turn).
    if _emotional_context_update is not None:
        existing["_emotional_context"] = _emotional_context_update
        save_session_slots(session_id, existing)

    # --- Reset ---
    if category == "reset":
        return _handle_reset(session_id, redacted_message, category, tone, request_id)

    # --- Correction ---
    if category == "correction":
        return _handle_correction(session_id, redacted_message, existing, tone, request_id)

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
            # Fall through to normal service routing below.
        else:
            return _handle_negative_preference(
                session_id, redacted_message, existing, tone, request_id,
            )

    # --- Greeting ---
    if category == "greeting":
        return _handle_greeting(session_id, redacted_message, existing, category, tone, request_id)

    # --- Thanks ---
    if category == "thanks":
        return _handle_thanks(session_id, redacted_message, existing, category, tone, request_id)

    # --- Help ---
    if category == "help":
        return _handle_help(session_id, message, redacted_message, existing,
                            _response_tone, category, tone, _tone_prefix, request_id)

    # --- Bot Identity ---
    if category == "bot_identity":
        return _handle_bot_identity(session_id, redacted_message, existing,
                                    category, tone, request_id)

    # --- Bot capability questions ---
    if category == "bot_question":
        return _handle_bot_capability_question(session_id, message, redacted_message,
                                               existing, category, tone, request_id)

    # --- Demographic skip ("I'd rather not say" / "skip") ---
    # SAMHSA Empowerment principle: users control what they share.
    _demo_skip_result = _handle_demographic_skip(session_id, message, redacted_message,
                                                  existing, tone, request_id)
    if _demo_skip_result:
        return _demo_skip_result

    # --- Location unknown ---
    _loc_unknown_result = _handle_location_unknown(session_id, message, redacted_message,
                                                    existing, tone, request_id)
    if _loc_unknown_result:
        return _loc_unknown_result

    # --- Confused / Overwhelmed ---
    if category == "confused":
        return _handle_confused(session_id, redacted_message, existing,
                                category, tone, _tone_prefix, request_id)

    # --- Emotional expression ---
    if category == "emotional":
        return _handle_emotional(session_id, message, redacted_message, existing,
                                 category, tone, request_id)

    # --- Frustration ---
    if category == "frustration":
        return _handle_frustration(session_id, redacted_message, existing, tone, request_id)

    # --- Escalation ---
    if category == "escalation":
        return _handle_escalation(session_id, redacted_message, existing,
                                  category, tone, request_id)

    # --- Context-aware "yes" / "no" handling ---
    last_action = existing.get("_last_action")
    context_result = _handle_context_aware_confirm(
        session_id, message, redacted_message, existing,
        category, last_action, tone, request_id,
    )
    if context_result:
        return context_result

    # Clear the last_action tracker now that we've checked it
    if last_action:
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)

    # --- Handle "change location" / "change service" outside pending ---
    if not existing.get("_pending_confirmation"):
        if category == "confirm_change_location":
            return _handle_change_location_request(
                session_id, redacted_message, existing, early_extracted,
                category, tone, request_id,
            )
        if category == "confirm_change_service":
            return _handle_change_service_request(
                session_id, redacted_message, existing, category, tone, request_id,
            )

    # --- Handle confirmation responses ---
    pending = existing.get("_pending_confirmation")
    confirm_result = _handle_pending_confirmation(
        session_id, message, redacted_message, existing, pending,
        category, tone, request_id, early_extracted=early_extracted,
    )
    if confirm_result:
        return confirm_result

    # If pending confirmation but user typed something new
    if pending:
        return _handle_post_pending_confirmation(
            session_id, message, redacted_message, existing,
            _response_tone, tone, request_id,
        )

    # --- Service request or general conversation ---
    if _USE_LLM and category == "service":
        # Feature flag for Phase 2 of the llm_slot_extractor migration.
        # When USE_UNIFIED_EXTRACTOR=1, route through the new
        # `slot_extraction.extract()`; otherwise use the legacy
        # `extract_slots_smart`. See UNIFIED_EXTRACTOR_MIGRATION.md.
        if _USE_UNIFIED_EXTRACTOR:
            from app.services.slot_extraction import extract as extract_unified
            extracted = extract_unified(
                message,
                early_extracted,
                conversation_history=existing.get("transcript", []),
                api_key_available=True,  # gated by _USE_LLM above
            )
        else:
            from app.services.llm_slot_extractor import extract_slots_smart
            extracted = extract_slots_smart(
                message,
                conversation_history=existing.get("transcript", []),
            )
    else:
        extracted = early_extracted

    has_new_slots = any(v is not None and v != [] for k, v in extracted.items()
                        if k not in ("additional_services", "_populations", "_contradiction", "_is_additive"))

    merged = merge_slots(existing, extracted)

    # Store redacted transcript
    if "transcript" not in merged:
        merged["transcript"] = []
    merged["transcript"].append({"role": "user", "text": redacted_message})
    _MAX_TRANSCRIPT = 20
    if len(merged["transcript"]) > _MAX_TRANSCRIPT:
        merged["transcript"] = merged["transcript"][-_MAX_TRANSCRIPT:]

    # Queue additional services
    additional = extracted.get("additional_services", [])
    _is_additive = extracted.get("_is_additive", False)
    if additional and "_queued_services" not in merged:
        merged["_queued_services"] = additional
    if (extracted.get("service_type")
            and existing.get("service_type")
            and extracted["service_type"] != existing.get("service_type")
            and not additional
            and not _is_additive):
        merged.pop("_queued_services", None)

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
    # "shame" here).
    if _emotional_context_update is not None:
        merged["_emotional_context"] = _emotional_context_update

    # Re-save if emotional context was set after the initial save.
    # Without this, emotional context is lost on the follow-up path where
    # save_session_slots isn't called again before returning.
    if merged.get("_emotional_context") and not existing.get("_emotional_context"):
        save_session_slots(session_id, merged)

    # Prepend PII safety warning, Spanish acknowledgment, and/or
    # immigration-context acknowledgment before the tone prefix so they
    # appear first in confirmations and follow-ups.
    _prefix_prepend = (
        _pii_warning
        + _spanish_acknowledgment
        + _immigration_acknowledgment(merged)
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
        result = {
            "session_id": session_id,
            "response": confirm_msg,
            "follow_up_needed": True,
            "slots": merged,
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": _confirmation_quick_replies(merged),
        }
        _log_turn(session_id, redacted_message, result, "confirmation",
                  request_id=request_id, tone=tone)
        return result

    # Need more slots — service request
    if category == "service":
        follow_up = _tone_prefix + next_follow_up_question(merged)
        result = {
            "session_id": session_id,
            "response": follow_up,
            "follow_up_needed": True,
            "slots": merged,
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": _follow_up_quick_replies(merged),
        }
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    # Service flow continuation
    if has_new_slots and existing.get("service_type") and not existing.get("_pending_confirmation"):
        follow_up = next_follow_up_question(merged)
        result = {
            "session_id": session_id,
            "response": follow_up,
            "follow_up_needed": True,
            "slots": merged,
            "services": [],
            "result_count": 0,
            "relaxed_search": False,
            "quick_replies": _follow_up_quick_replies(merged),
        }
        _log_turn(session_id, redacted_message, result, "service", request_id=request_id, tone=tone)
        return result

    # --- General conversation / unrecognized service ---
    return _handle_general_conversation(
        session_id, message, redacted_message, merged,
        _confidence, tone, request_id,
    )
