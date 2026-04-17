"""
YourPeer Chatbot — Main routing module.

This is the thin orchestrator that:
  1. Extracts slots from the user's message
  2. Classifies intent and tone
  3. Routes to the appropriate handler
  4. Executes DB queries when confirmed

All data constants live in phrase_lists.py.
All classification logic lives in classifier.py.
All response strings and prompts live in responses.py.
All confirmation/follow-up logic lives in confirmation.py.
"""

import uuid
import re
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.llm.claude_client import claude_reply
from app.services.session_store import (
    get_session_slots,
    save_session_slots,
    clear_session,
)
from app.services.slot_extractor import (
    extract_slots,
    is_enough_to_answer,
    merge_slots,
    next_follow_up_question,
    NEAR_ME_SENTINEL,
)
from app.rag import query_services
from app.rag.query_executor import fetch_schedule_for_day
from app.rag.query_templates import _format_time
from app.privacy.pii_redactor import redact_pii
from app.services.crisis_detector import detect_crisis
from app.services.post_results import classify_post_results_question, answer_from_results

# Extracted modules
from app.services.phrase_lists import (
    _WELCOME_QUICK_REPLIES,
    _SERVICE_LABELS,
)
from app.services.classifier import (
    _classify_action,
    _classify_tone,
    _normalize_contractions,
    _strip_intensifiers,
    _CRISIS_NOT_CHECKED,
)
from app.services.responses import (
    _GREETING_RESPONSE,
    _RESET_RESPONSE,
    _THANKS_RESPONSE,
    _HELP_RESPONSE,
    _ESCALATION_RESPONSE,
    _FRUSTRATION_RESPONSE,
    _BOT_IDENTITY_RESPONSE,
    _CONFUSED_RESPONSE,
    _pick_emotional_response,
    _build_bot_question_prompt,
    _build_conversational_prompt,
    _static_bot_answer,
    _fallback_response,
    random_warmth_prefix,
)
from app.services.confirmation import (
    _build_confirmation_message,
    _confirmation_quick_replies,
    _follow_up_quick_replies,
    _get_nearby_boroughs,
    _no_results_message,
)

from app.services.audit_log import (
    log_conversation_turn,
    log_query_execution,
    log_crisis_detected,
    log_session_reset,
)

logger = logging.getLogger(__name__)

# Use LLM-based features when ANTHROPIC_API_KEY is available.
# Falls back to regex-only if the key is not set.
_USE_LLM = bool(os.getenv("ANTHROPIC_API_KEY"))

if _USE_LLM:
    from app.services.llm_slot_extractor import extract_slots_smart
    from app.services.llm_classifier import classify_unified
    from app.llm.claude_client import classify_message_llm
    logger.info("LLM features enabled (ANTHROPIC_API_KEY found)")
else:
    logger.info("LLM features disabled — using regex only")


# ---------------------------------------------------------------------------
# DISPLAY CONFIGURATION
# ---------------------------------------------------------------------------
# How many service cards to show per page. The DB query fetches more
# (up to _FETCH_LIMIT in _execute_and_respond) to support client-side
# filtering, but we paginate the display to avoid overwhelming users —
# especially in crisis situations where cognitive load is high.
_DISPLAY_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# MESSAGE CONTEXT — shared state between classification and handlers
# ---------------------------------------------------------------------------
# Built by _build_context() from the classification pipeline. Passed to
# every handler function so they don't need 15+ positional arguments.
# Mutable: handlers may modify `existing` (session slots) and should
# call save_session_slots() when they do.

@dataclass
class MessageContext:
    """All state produced by the classification pipeline for a single message.

    Built once by _build_context(), consumed by handler functions.
    Replaces the 15+ local variables that were previously shared via
    closure inside the monolithic generate_reply().
    """
    # --- Identifiers ---
    session_id: str
    request_id: str
    # --- Message variants ---
    message: str              # original user message
    redacted_message: str     # PII-scrubbed version (for logging)
    pii_warning: str          # prepend to response if user shared SSN/phone
    # --- Session state (mutable) ---
    existing: dict            # session slots — handlers may modify + save
    # --- Classification results ---
    category: str             # routing key ("crisis", "service", "greeting", etc.)
    action: str               # classified action ("confirm_yes", "reset", etc.)
    tone: Optional[str]       # emotional tone ("crisis", "emotional", "frustrated", etc.)
    confidence: str           # "high" | "semantic" | "medium" | "low"
    extraction_source: Optional[str]  # "regex" | "semantic" | "llm_gate" | None
    # --- Extracted slots from this message ---
    early_extracted: dict     # raw extraction result (service_type, location, age, etc.)
    has_service_intent: bool  # True if service_type or org_name was extracted
    # --- Crisis ---
    crisis_result: Optional[dict]  # from detect_crisis(), None if no crisis
    # --- Post-results state ---
    last_results: Optional[list]   # cached query results from session, or None
    is_confirmation_action: bool   # True if action is confirm_yes/deny/change/reset/greeting
    # --- Geolocation ---
    has_coords: bool          # True if lat/lon were provided by browser
    latitude: Optional[float]
    longitude: Optional[float]
    # --- Language ---
    spanish_detected: bool    # True if Spanish phrases found in message
    spanish_acknowledgment: str  # bilingual prefix if Spanish + service intent


def _count_unique_locations(services: list[dict]) -> int:
    """Count unique locations by org+address — mirrors the frontend's
    ``groupByLocation()`` which renders co-located services as one card.
    Without this, text says "5 results" while the carousel shows 4 cards
    because two services share the same location."""
    seen: set[str] = set()
    for svc in services:
        key = f"{(svc.get('organization') or '').lower().strip()}||{(svc.get('address') or '').lower().strip()}"
        seen.add(key)
    return len(seen)


# ---------------------------------------------------------------------------
# EMPTY RESPONSE HELPER
# ---------------------------------------------------------------------------

def _empty_reply(
    session_id: str,
    response: str,
    slots: dict,
    quick_replies: list | None = None,
) -> dict:
    """Build a reply dict with no service results."""
    return {
        "session_id": session_id,
        "response": response,
        "follow_up_needed": False,
        "slots": slots,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": quick_replies or [],
    }


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

# PII types that warrant a user-facing warning when shared. Other types
# (names, emails) are quietly redacted but don't trigger a warning.
_PII_WARN_TYPES = {"ssn", "phone"}


def _redact_with_safety_warning(message: str) -> tuple[str, str, list]:
    """Redact PII and compute a user-facing safety warning if applicable.

    Returns (redacted_message, warning_prefix, pii_detections). The warning
    prefix is "" when no sensitive PII was shared; otherwise it's a
    category-appropriate reminder that the bot has scrubbed the PII.
    The caller prepends it to whatever response the handler returns.
    """
    redacted_message, pii_detections = redact_pii(message)
    warning_prefix = ""
    if pii_detections:
        detected = {d.pii_type for d in pii_detections}
        sensitive = detected & _PII_WARN_TYPES
        if sensitive:
            if "ssn" in sensitive:
                warning_prefix = (
                    "For your safety, please don't share your Social Security "
                    "number or other sensitive personal information in this "
                    "chat. I've removed it from the conversation.\n\n"
                )
            else:
                warning_prefix = (
                    "Just a heads up — I've removed your phone number from "
                    "the conversation to protect your privacy. You don't need "
                    "to share personal info to search for services.\n\n"
                )
    return redacted_message, warning_prefix, pii_detections


def _run_early_extraction(message: str, session_id: str) -> tuple[dict, str | None]:
    """Regex slot extraction + semantic-router fallback.

    Runs BEFORE the LLM gate so it fires even in regex-only mode
    (no ANTHROPIC_API_KEY), saves an LLM call when Tier 1 or Tier 2
    resolves, and prevents "general" fallthrough on messages that
    semantic routing can classify.

    Returns (extracted_slots, extraction_source) where source is one of
    "regex" / "semantic" / None.
    """
    early_extracted = extract_slots(message)
    extraction_source = "regex" if early_extracted.get("service_type") else None

    if early_extracted.get("service_type") is None:
        # Lazy import — avoids loading the sentence-transformer model
        # in processes that don't need routing (e.g. test collection).
        from app.services.semantic_router import classify_service as _semantic_classify
        from app.services.semantic_router import is_available as _semantic_available

        if _semantic_available():
            semantic_match = _semantic_classify(message)
            if semantic_match is not None:
                logger.info(
                    f"Session {session_id}: semantic router matched "
                    f"'{semantic_match.service_type}' "
                    f"(confidence={semantic_match.confidence:.3f})"
                )
                early_extracted["service_type"] = semantic_match.service_type
                extraction_source = "semantic"

                # Merge population from semantic router
                if semantic_match.population:
                    existing_pops = set(early_extracted.get("_populations") or [])
                    existing_pops.add(semantic_match.population)
                    early_extracted["_populations"] = sorted(existing_pops)

    return early_extracted, extraction_source


# Actions that should NOT trigger the unified LLM classification gate —
# these have high-confidence regex classifiers, so an additional LLM call
# would only waste tokens without improving routing.
_SKIP_UNIFIED_ACTIONS = frozenset({
    "reset", "greeting", "thanks", "bot_identity", "bot_question",
    "confirm_yes", "confirm_deny", "confirm_change_service",
    "confirm_change_location", "correction", "negative_preference",
    "escalation",
})


def _run_llm_gate(
    message: str,
    early_extracted: dict,
    has_service_intent: bool,
    action_pre: str | None,
    regex_tone_pre: str | None,
    extraction_source: str | None,
) -> tuple[bool, str | None, str | None, str | None, str | None]:
    """Unified LLM classification gate.

    Runs only when regex + semantic routing didn't resolve AND the message
    looks substantive enough (≥4 words, not a known simple action) to
    justify the API call. Mutates `early_extracted` in place when the LLM
    finds a service_type / demographic slot that regex/semantic missed.

    Returns a tuple of updated state:
        (has_service_intent, action_pre, extraction_source, llm_tone, llm_action)
    """
    needs_unified = (
        _USE_LLM
        and not has_service_intent
        and action_pre not in _SKIP_UNIFIED_ACTIONS
        and regex_tone_pre is None
        and len(message.split()) >= 4
    )
    if not needs_unified:
        return has_service_intent, action_pre, extraction_source, None, None

    llm_tone = None
    llm_action = None
    try:
        unified = classify_unified(message)
        if unified:
            if unified.get("service_type"):
                logger.info(
                    f"Unified gate found service_type="
                    f"'{unified['service_type']}' that regex missed"
                )
                early_extracted["service_type"] = unified["service_type"]
                extraction_source = "llm_gate"
                if unified.get("service_detail"):
                    early_extracted["service_detail"] = unified["service_detail"]
                if unified.get("location"):
                    early_extracted["location"] = unified["location"]
                if unified.get("additional_services"):
                    early_extracted["additional_services"] = unified["additional_services"]
                if unified.get("urgency"):
                    early_extracted["urgency"] = unified["urgency"]
                if unified.get("age"):
                    early_extracted["age"] = unified["age"]
                if unified.get("family_status"):
                    early_extracted["family_status"] = unified["family_status"]
                if unified.get("gender"):
                    early_extracted["_gender"] = unified["gender"]
                has_service_intent = True

            if unified.get("tone"):
                llm_tone = unified["tone"]
                logger.info(f"Unified gate detected tone='{llm_tone}'")
            if unified.get("action"):
                llm_action = unified["action"]
                logger.info(f"Unified gate detected action='{llm_action}'")
                if action_pre is None:
                    action_pre = llm_action
    except Exception as e:
        logger.error(f"Unified LLM classification failed: {e}")

    return has_service_intent, action_pre, extraction_source, llm_tone, llm_action


def _compute_routing_category(
    *,
    tone: str | None,
    action: str | None,
    has_service_intent: bool,
    early_extracted: dict,
    extraction_source: str | None,
    message: str,
) -> tuple[str, str]:
    """Combine tone + action + intent signals into a routing category + confidence.

    **The branch ORDER encodes precedence rules** and is guarded by
    ``tests/unit/test_routing_category_order.py``. Do not reorder without
    updating that test. Crisis wins over reset, reset wins over correction,
    etc.; the sequence is load-bearing for safety (crisis) and for
    disambiguation (confirm_* before has_service_intent so pending
    confirmations aren't bypassed by a trailing service keyword).

    Returns (category, confidence) where confidence is one of
    "high" / "semantic" / "medium" / "low".
    """
    # Confidence reflects how the service_type was determined:
    #   "high"     — regex keyword match (deterministic)
    #   "semantic" — semantic embedding match (Tier 2, high but not deterministic)
    #   "medium"   — LLM classification (unified gate or fallback)
    #   "low"      — no classification succeeded, using fallback
    if extraction_source == "regex":
        confidence = "high"
    elif extraction_source == "semantic":
        confidence = "semantic"
    elif extraction_source == "llm_gate":
        confidence = "medium"
    else:
        confidence = "high"  # default for non-service routes (greeting, reset, etc.)

    if tone == "crisis":
        category = "crisis"
    elif action == "reset":
        category = "reset"
    elif action == "correction":
        category = "correction"
    elif action == "negative_preference":
        category = "negative_preference"
    elif action in ("confirm_change_service", "confirm_change_location",
                     "confirm_yes", "confirm_deny"):
        category = action
    elif action in ("bot_identity", "bot_question", "greeting", "thanks"):
        category = action
    elif has_service_intent:
        if action == "bot_question":
            category = "bot_question"
        elif action == "escalation" and not early_extracted.get("location"):
            category = "escalation"
        else:
            category = "service"
    elif action == "help":
        category = "help"
    elif action == "escalation":
        category = "escalation"
    elif tone == "frustrated":
        category = "frustration"
    elif tone == "emotional":
        category = "emotional"
    elif tone == "confused":
        category = "confused"
    elif _USE_LLM and len(message.strip().split()) > 3:
        llm_category = classify_message_llm(message)
        if llm_category is not None:
            logger.info(
                f"LLM classifier override: regex='general' → llm='{llm_category}'"
            )
            category = llm_category
            confidence = "medium"
        else:
            category = "general"
            confidence = "low"
    else:
        category = "general"
        confidence = "low"

    return category, confidence


def _apply_session_geo(
    session_id: str,
    existing: dict,
    latitude: float | None,
    longitude: float | None,
) -> None:
    """Store browser geolocation coords in session slots if provided.

    Also marks location as "answered" with the NEAR_ME_SENTINEL when
    coords arrive without a prior location — without this, the bot
    would still ask "What neighborhood?" even though it has GPS.

    Mutates `existing` in place and persists via save_session_slots.
    """
    if latitude is None or longitude is None:
        return
    existing["_latitude"] = latitude
    existing["_longitude"] = longitude
    if not existing.get("location"):
        existing["location"] = NEAR_ME_SENTINEL
    save_session_slots(session_id, existing)


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
    has_coords = latitude is not None and longitude is not None
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

    # --- Reset ---
    if category == "reset":
        return _handle_reset(session_id, redacted_message, category, tone, request_id)

    # --- Correction ---
    if category == "correction":
        return _handle_correction(session_id, redacted_message, existing, tone, request_id)

    # --- Negative preference ---
    if category == "negative_preference":
        return _handle_negative_preference(session_id, redacted_message, existing, tone, request_id)

    # --- Greeting ---
    if category == "greeting":
        return _handle_greeting(session_id, redacted_message, existing, category, tone, request_id)

    # --- Thanks ---
    if category == "thanks":
        return _handle_thanks(session_id, redacted_message, existing, category, tone, request_id)

    # --- Help ---
    if category == "help":
        return _handle_help(session_id, message, redacted_message, existing,
                            _response_tone, category, tone, request_id)

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
                                category, tone, request_id)

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
        extracted = extract_slots_smart(
            message,
            conversation_history=existing.get("transcript", []),
        )
    else:
        extracted = early_extracted

    has_new_slots = any(v is not None and v != [] for k, v in extracted.items()
                        if k not in ("additional_services", "_populations", "_contradiction"))

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
    if additional and "_queued_services" not in merged:
        merged["_queued_services"] = additional
    if (extracted.get("service_type")
            and existing.get("service_type")
            and extracted["service_type"] != existing.get("service_type")
            and not additional):
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

    # --- Tone prefix for service-flow responses ---
    _is_service_flow = category == "service"
    _tone_prefix, _emotional_context_update = _compute_tone_prefix(
        message=message,
        response_tone=_response_tone,
        is_service_flow=_is_service_flow,
        prior_emotional_context=existing.get("_emotional_context"),
    )

    # Persist emotional context for subsequent turns
    if _emotional_context_update is not None:
        merged["_emotional_context"] = _emotional_context_update

    # Re-save if emotional context was set after the initial save.
    # Without this, emotional context is lost on the follow-up path where
    # save_session_slots isn't called again before returning.
    if merged.get("_emotional_context") and not existing.get("_emotional_context"):
        save_session_slots(session_id, merged)

    # Prepend PII safety warning and/or Spanish acknowledgment before
    # the tone prefix so they appear first in confirmations and follow-ups.
    _prefix_prepend = _pii_warning + _spanish_acknowledgment
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
        _log_turn(session_id, redacted_message, result, "confirmation", request_id=request_id, tone=tone)
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


# ---------------------------------------------------------------------------
# HANDLER HELPERS (extracted from generate_reply for readability)
# ---------------------------------------------------------------------------

# Phrases that count as a user declining to share demographic info.
_DEMOGRAPHIC_SKIP_PHRASES = (
    "i'd rather not say", "id rather not say", "i would rather not say",
    "rather not say", "prefer not to say", "prefer not to",
    "skip", "skip this", "don't want to say", "dont want to say",
    "none of your business", "that's personal", "thats personal",
    "pass",
)

# Phrases indicating the user doesn't know their location (triggers
# the location-picker fallback).
_LOCATION_UNKNOWN_PHRASES = (
    "i don't know", "i dont know", "idk", "not sure", "i'm not sure",
    "im not sure", "no idea", "don't know", "dont know",
    "i don't know where i am", "i dont know where i am",
    "not sure where i am", "don't know where i am",
    "dont know where i am", "no clue",
    "anywhere", "wherever", "doesn't matter", "doesnt matter",
    "it doesn't matter", "it doesnt matter",
    "where i am",
)
_LOCATION_UNKNOWN_EXACT = ("here", "right here")

# Words that signal the help-intent message is actually a shame/vulnerability
# disclosure rather than a "what can you do?" question.
_SHAME_HELP_SIGNALS = (
    "embarrassed", "ashamed", "pathetic", "humiliating",
    "hard to ask", "hard for me", "hate asking", "hate to ask",
    "difficult to ask", "burden", "swallow my pride",
)


def _handle_help(session_id, message, redacted_message, existing,
                 response_tone, category, tone, request_id):
    """Show the service-menu help response.

    Two variants: if the user is expressing shame around asking ("I'm
    embarrassed to ask for help"), this routes to the emotional handler
    instead. Confused or emotional callers get a lead-in that acknowledges
    the overwhelm before the menu.
    """
    # Shame + help: vulnerability disclosure masquerading as a help request.
    # Route to emotional handler with the shame-specific response.
    if response_tone == "emotional":
        help_lower = message.lower()
        if any(s in help_lower for s in _SHAME_HELP_SIGNALS):
            response = _pick_emotional_response(message)
            existing["_last_action"] = "emotional"
            existing["_emotional_context"] = "shame"
            save_session_slots(session_id, existing)
            result = _empty_reply(
                session_id, response, existing,
                quick_replies=[
                    {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                ],
            )
            _log_turn(session_id, redacted_message, result, "emotional",
                      request_id=request_id, tone=tone)
            return result

    # When the user is confused or emotional AND asking for help,
    # lead with empathy before showing the service menu.
    if response_tone in ("confused", "emotional"):
        help_msg = (
            "I hear you — it can feel overwhelming when you don't know "
            "where to start. Let's take it one step at a time. "
            "Here's what I can help you find:"
        )
    else:
        help_msg = _HELP_RESPONSE
    result = _empty_reply(
        session_id, help_msg, existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_bot_identity(session_id, redacted_message, existing, category, tone, request_id):
    """Answer "are you a bot?" / "who are you?" with the standard identity line."""
    result = _empty_reply(
        session_id, _BOT_IDENTITY_RESPONSE, existing,
        quick_replies=[
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_bot_capability_question(session_id, message, redacted_message, existing,
                                    category, tone, request_id):
    """Answer "what can you do?" / "can you find X?" — tries bot_knowledge first,
    then LLM, then a static fallback."""
    from app.services.bot_knowledge import answer_question
    static_answer = answer_question(message)
    if static_answer:
        response = static_answer
    elif _USE_LLM:
        try:
            prompt = _build_bot_question_prompt(message, slots=existing)
            response = claude_reply(prompt)
        except Exception as e:
            logger.error(f"Bot question LLM response failed: {e}")
            response = _static_bot_answer(message)
    else:
        response = _static_bot_answer(message)
    result = _empty_reply(session_id, response, existing)
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_demographic_skip(session_id, message, redacted_message, existing,
                             tone, request_id):
    """If a demographic question is pending and the user declined, mark the
    slots "skipped" and proceed to confirmation.

    Returns a result dict if the skip pattern fired, None otherwise.
    """
    skip_lower = message.lower().strip()
    is_skip = (
        any(p in skip_lower for p in _DEMOGRAPHIC_SKIP_PHRASES)
        or skip_lower in ("skip", "pass")
    )
    is_demographic_pending = (
        existing.get("service_type")
        and existing.get("location")
        and not existing.get("_pending_confirmation")
        and (not existing.get("age") or not existing.get("family_status"))
    )
    if not (is_skip and is_demographic_pending):
        return None

    # Mark skipped demographics so we don't re-ask
    if not existing.get("age"):
        existing["age"] = "skipped"
    if not existing.get("family_status"):
        existing["family_status"] = "skipped"
    save_session_slots(session_id, existing)

    # Proceed to confirmation with what we have
    existing["_pending_confirmation"] = True
    save_session_slots(session_id, existing)
    confirm_msg = "No problem at all. " + _build_confirmation_message(existing)
    result = {
        "session_id": session_id,
        "response": confirm_msg,
        "follow_up_needed": True,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": _confirmation_quick_replies(existing),
    }
    _log_turn(session_id, redacted_message, result, "demographic_skip",
              request_id=request_id, tone=tone)
    return result


def _handle_location_unknown(session_id, message, redacted_message, existing,
                             tone, request_id):
    """If the user has a service_type but no location, and replied with an
    "I don't know" phrase, offer the geolocation+borough picker.

    Returns a result dict if the pattern fired, None otherwise.
    """
    msg_lower = message.lower().strip()
    is_location_unknown = (
        any(p in msg_lower for p in _LOCATION_UNKNOWN_PHRASES)
        or msg_lower in _LOCATION_UNKNOWN_EXACT
    )
    needs_location_picker = (
        existing.get("service_type")
        and not existing.get("location")
        and not existing.get("_pending_confirmation")
        and is_location_unknown
    )
    if not needs_location_picker:
        return None

    result = _empty_reply(
        session_id,
        "No problem! You can share your location and I'll find what's "
        "nearby, or pick a borough:",
        existing,
        quick_replies=[
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "location_unknown",
              request_id=request_id, tone=tone)
    return result


# Common Spanish phrases that should trigger a bilingual acknowledgment.
# SAMHSA Cultural Humility: acknowledge the language gap rather than
# returning silence or an English-only response.
_SPANISH_RE = re.compile(
    r"\b(necesito|ayuda|comida|refugio|albergue|por favor|"
    r"no hablo ingles|no hablo inglés|hola|buenos dias|"
    r"buenas tardes|buenas noches|tengo hambre|"
    r"necesito ayuda|donde puedo|dónde puedo)\b", re.I,
)


def _handle_spanish_detection(session_id, message, redacted_message, existing,
                              has_service_intent, tone, request_id):
    """Detect Spanish phrases and either return a bilingual response outright
    (Spanish-only message) or return an acknowledgment prefix to prepend to
    the downstream response (Spanish + service request).

    Returns (result, acknowledgment_prefix) where exactly one is non-empty:
      - result is non-None when we've fully answered (no fallthrough needed)
      - acknowledgment_prefix is non-empty when the caller should continue
        processing and prepend the prefix to its eventual response
    """
    if not _SPANISH_RE.search(message):
        return None, ""

    if not has_service_intent:
        # Spanish only, no service request — return bilingual message
        result = _empty_reply(
            session_id,
            "I'm sorry — right now I can only help in English. "
            "A peer navigator may be able to help in Spanish.\n\n"
            "Lo siento — por ahora solo puedo ayudar en inglés. "
            "Un navegador comunitario puede ayudarte en español.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "spanish_detected",
                  request_id=request_id, tone=tone)
        return result, ""

    # Spanish + service intent — process normally with a bilingual prefix
    acknowledgment = (
        "I can see you may prefer Spanish — lo siento, por ahora "
        "solo puedo ayudar en inglés. I'll do my best to help.\n\n"
    )
    return None, acknowledgment


def _handle_confused(session_id, redacted_message, existing, category, tone, request_id):
    """Acknowledge overwhelm with the standard confused response and mark
    _last_action so a follow-up 'yes' / 'no' is interpreted in this context."""
    existing["_last_action"] = "confused"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id, _CONFUSED_RESPONSE, existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_emotional(session_id, message, redacted_message, existing,
                      category, tone, request_id):
    """Empathic response picked to match the detected emotional signal.
    Marks _last_action so a follow-up 'yes' is interpreted as asking for
    the peer-navigator handoff."""
    response = _pick_emotional_response(message)
    existing["_last_action"] = "emotional"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id, response, existing,
        quick_replies=[
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_escalation(session_id, redacted_message, existing, category, tone, request_id):
    """User explicitly asked for human help. Clear any pending confirmation
    (so a trailing 'yes' doesn't fire a search the user abandoned) and offer
    peer navigator + start-over."""
    if existing.get("_pending_confirmation"):
        existing.pop("_pending_confirmation", None)
    existing["_last_action"] = "escalation"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id, _ESCALATION_RESPONSE, existing,
        quick_replies=[
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "👤 Talk to a person", "value": "Connect with person"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


# Phrases indicating shame/vulnerability disclosure. When these co-occur with
# a service request, a normalizing prefix ("It takes real strength…") is
# prepended instead of a generic empathy line.
_SHAME_SIGNALS = (
    "embarrassed", "ashamed", "pathetic", "failure",
    "never thought i'd need", "never thought id need",
    "hard for me to say", "hard to say", "hard for me to ask",
    "hard to ask", "hard to admit",
    "difficult to ask", "difficult to say",
    "hate asking", "hate to ask", "hate having to ask",
    "humiliating", "degrading",
    "burden", "swallow my pride", "swallowed my pride",
    "first time asking", "never done this before",
    "never had to ask", "never asked for help",
    "can't believe i'm", "cant believe im",
    "can't afford to eat", "cant afford to eat",
    "can't even feed", "cant even feed",
    "don't want anyone to know", "dont want anyone to know",
)

# Medical urgency requires BOTH a depletion signal AND a medical keyword
# to fire, avoiding false positives on generic "ran out of" phrases.
_MEDICATION_DEPLETION = (
    "ran out of", "run out of", "running out of", "out of my",
    "don't have my", "dont have my", "lost my medication",
    "lost my medicine",
    "no more", "can't get my", "cant get my", "ran out of my",
)
_MEDICATION_WORDS = (
    "insulin", "medication", "medicine", "prescription",
    "inhaler", "epipen", "pills", "meds",
)

# Sensitive life-situation phrases that override casual tone (e.g. a user
# writing "just got out of jail, need a shower" should not get a cheerful
# baseline-warmth opener).
_SENSITIVE_CONTEXT_RE = re.compile(
    r"\b(foster care|aging out|aged out|fleeing|escaped|"
    r"just got out of jail|just got out of prison|domestic violence)\b", re.I,
)


def _compute_tone_prefix(
    message: str,
    response_tone: str | None,
    is_service_flow: bool,
    prior_emotional_context: str | None,
) -> tuple[str, str | None]:
    """Compute the opening phrase for a service-flow response based on tone.

    **Precedence (load-bearing):**

    1. Sensitive context (foster care, fleeing, just got out of jail) —
       overrides everything else with a specific acknowledgment. Must come
       last in the function so it can override an already-set prefix.
    2. Shame disclosure + service flow → normalizing prefix.
    3. Medical urgency (depletion + meds keyword) + service flow →
       "That sounds urgent — let me help you find care right away."
    4. Emotional / frustrated / confused / urgent tone + service flow →
       matching empathic prefix.
    5. Prior emotional context (set on a previous turn this session) →
       continuity prefix ("Still here with you.").
    6. Baseline warmth (routine service flow with no emotional signal) →
       randomly selected warm opener.

    Returns (prefix, emotional_context) where ``prefix`` is the string to
    prepend (empty string if none) and ``emotional_context`` is what the
    caller should save to ``merged["_emotional_context"]`` for use on the
    next turn (None = don't change).
    """
    msg_lower = message.lower()
    is_shame = any(s in msg_lower for s in _SHAME_SIGNALS)
    is_medical_urgent = (
        is_service_flow
        and any(s in msg_lower for s in _MEDICATION_DEPLETION)
        and any(s in msg_lower for s in _MEDICATION_WORDS)
    )

    prefix = ""
    emotional_context: str | None = None

    if is_shame and is_service_flow:
        prefix = (
            "It takes real strength to reach out — a lot of people use "
            "these services, and there's no shame in it. "
        )
        emotional_context = "shame"
    elif is_medical_urgent:
        prefix = "That sounds urgent — let me help you find care right away. "
        emotional_context = "medical_urgent"
    elif response_tone == "emotional" and is_service_flow:
        prefix = "I hear you, and I want to help. "
        emotional_context = "emotional"
    elif response_tone == "frustrated" and is_service_flow:
        prefix = "I understand this has been frustrating. Let me try something different. "
    elif response_tone == "confused" and is_service_flow:
        prefix = "No worries — let me help you with that. "
    elif response_tone == "urgent" and is_service_flow:
        prefix = "I can see this is urgent — let me find something right away. "

    # Continuity: prior emotional context from earlier in the session
    if not prefix and is_service_flow and prior_emotional_context:
        if prior_emotional_context == "shame":
            prefix = "Still here with you. "
        elif prior_emotional_context == "medical_urgent":
            prefix = "Let's get you to the right place. "
        else:
            prefix = "I'm still here with you. "

    # Baseline warmth: prevent "functional but flat" tone on routine turns
    if not prefix and is_service_flow:
        prefix = random_warmth_prefix()

    # Sensitive context OVERRIDES everything — applied last
    if _SENSITIVE_CONTEXT_RE.search(message):
        prefix = "I understand this is a difficult situation. Let me help. "
        emotional_context = "sensitive"

    return prefix, emotional_context


# Patterns that mark a message as casual small talk rather than a service query.
_CASUAL_CHAT_RE = re.compile(
    r"\b(how are you|how's it going|hows it going|what's up|whats up|"
    r"hey there|just (wanted to|wanna) (chat|talk)|having a good day|"
    r"good morning|good afternoon|good evening|how you doing|"
    r"what are you up to|how do you do)\b", re.I,
)

# Service-need markers used to distinguish "I need help" (user asking for
# something specific we might not offer) from general conversation.
_SERVICE_NEED_RE = re.compile(
    r"\b(?:i need|i want|can you find|looking for|help me find|"
    r"get me|find me|i'm looking for|im looking for)\b",
    re.I,
)

_CASUAL_RESPONSES = (
    "I'm doing well, thanks for asking! I'm here whenever you need me.",
    "Hey! Just here and ready to help whenever you are.",
    "Doing good! Let me know if there's anything I can help you find.",
)


def _handle_post_results_interaction(
    session_id: str,
    message: str,
    redacted_message: str,
    existing: dict,
    early_extracted: dict,
    has_service_intent: bool,
    action_pre: str | None,
    tone: str | None,
    request_id: str | None,
) -> dict | None:
    """Handle follow-up messages after results have been shown.

    This runs BEFORE the routing cascade — it catches "show more," sort
    requests, questions about specific results, and confirm_yes/confirm_deny
    that would otherwise fall through to extraction and re-trigger the same
    search. When the user has instead started a new search (new service
    intent, or a confirmation action), it clears the stale result state so
    the routing cascade can handle the new intent cleanly.

    Returns a result dict if a post-results interaction fired, ``None`` if
    the caller should continue to the normal routing cascade. May mutate
    ``existing`` (pops ``_last_results`` / ``_last_action`` and saves).
    """
    last_results = existing.get("_last_results")
    is_confirmation_action = action_pre in (
        "confirm_change_service", "confirm_change_location",
        "confirm_yes", "confirm_deny", "reset", "greeting",
    )

    # confirm_yes / confirm_deny after results when no pending confirmation.
    # Without this, those messages fall through to extraction and re-trigger
    # the same search.
    # Guard: when the message ALSO contains a new service intent (e.g.
    # "Search for employment in Manhattan"), the new intent should override
    # the confirm action. Without this guard, "search for" matches
    # confirm_yes and the user's new request gets swallowed.
    if (last_results
            and action_pre in ("confirm_yes", "confirm_deny")
            and not existing.get("_pending_confirmation")
            and not existing.get("_queue_offer_pending")
            and not existing.get("_queued_services")
            and not existing.get("_last_action")
            and not has_service_intent):

        if action_pre == "confirm_yes":
            existing.pop("_last_results", None)
            save_session_slots(session_id, existing)
            result = _empty_reply(
                session_id,
                "I've already shown the results above — you can tap on any "
                "service card for more details. Would you like to search for "
                "something else?",
                existing,
                quick_replies=[
                    {"label": "🔍 New search", "value": "Start over"},
                    {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                ],
            )
            _log_turn(session_id, redacted_message, result, "post_results_confirm",
                      request_id=request_id, tone=tone)
            return result

        # confirm_deny
        existing.pop("_last_results", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! Let me know if you need anything else.",
            existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, "post_results_decline",
                  request_id=request_id, tone=tone)
        return result

    if last_results and not has_service_intent and not is_confirmation_action:
        # Clear stale _last_action: if the user is interacting with
        # results (asking questions, sorting, paginating), any prior
        # emotional/escalation/crisis context is no longer relevant.
        if existing.get("_last_action"):
            existing.pop("_last_action", None)
            save_session_slots(session_id, existing)

        is_frustration_or_rejection = (
            tone == "frustrated"
            or action_pre == "negative_preference"
            or action_pre == "correction"
        )
        if is_frustration_or_rejection:
            existing.pop("_last_results", None)
            save_session_slots(session_id, existing)
        elif early_extracted.get("location"):
            existing.pop("_last_results", None)
            save_session_slots(session_id, existing)
        else:
            # "Show all results" / "Show more results"
            show_result = _handle_show_more(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if show_result:
                return show_result

            # Sort options
            sort_result = _handle_sort_results(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if sort_result:
                return sort_result

            # Questions about specific results
            post_intent_result = _handle_post_results_question(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if post_intent_result:
                return post_intent_result

    if last_results and (has_service_intent or is_confirmation_action):
        existing.pop("_last_results", None)
        # When results were already shown and the user asks for something
        # new, treat it as a fresh search — clear the old service slots so
        # they don't compound with the new request. Multi-service should
        # only happen within a single message or before results.
        if has_service_intent:
            existing.pop("service_type", None)
            existing.pop("service_detail", None)
            existing.pop("_queued_services", None)
            existing.pop("_queued_services_original", None)
            existing.pop("_queue_offer_pending", None)
            existing.pop("_pending_confirmation", None)
            existing.pop("_displayed_count", None)
        save_session_slots(session_id, existing)

    return None


# Phrases that request pagination of prior results.
_SHOW_MORE_PATTERNS = (
    "show all results", "show results", "show all",
    "show more results", "show more", "more results",
    "any others", "what else", "next results",
    "any more", "see more",
)


def _handle_show_more(session_id, message, redacted_message, existing,
                      last_results, request_id):
    """Handle 'show more results' / 'show all' after results were displayed."""
    if message.lower().strip() not in _SHOW_MORE_PATTERNS:
        return None

    displayed = existing.get("_displayed_count", 0)
    if displayed and displayed < len(last_results):
        # Show the next page of results (not all remaining)
        next_page = last_results[displayed:displayed + _DISPLAY_PAGE_SIZE]
        new_displayed = displayed + len(next_page)
        existing["_displayed_count"] = new_displayed
        save_session_slots(session_id, existing)

        still_remaining = len(last_results) - new_displayed
        qr = [
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ]
        if still_remaining > 0:
            remaining_locs = _count_unique_locations(
                last_results[new_displayed:new_displayed + _DISPLAY_PAGE_SIZE]
            )
            qr.insert(0, {
                "label": f"📋 Show {remaining_locs} more result{'s' if remaining_locs != 1 else ''}",
                "value": "Show more results",
            })

        page_loc_count = _count_unique_locations(next_page)
        result = {
            "session_id": session_id,
            "response": f"Here are {page_loc_count} more result{'s' if page_loc_count != 1 else ''}:",
            "follow_up_needed": False,
            "slots": existing,
            "services": next_page,
            "result_count": page_loc_count,
            "relaxed_search": False,
            "quick_replies": qr,
        }
    else:
        # No more to show — re-display all
        result = {
            "session_id": session_id,
            "response": "Here are all the results again:",
            "follow_up_needed": False,
            "slots": existing,
            "services": last_results,
            "result_count": len(last_results),
            "relaxed_search": False,
            "quick_replies": [
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


# Sort-mode phrases → internal sort key.
_SORT_PATTERNS = {
    "sort by recently verified": "verified",
    "sort by recently updated": "verified",
    "sort by newest": "verified",
    "sort by most services": "services",
    "most services": "services",
}


def _handle_sort_results(session_id, message, redacted_message, existing,
                         last_results, request_id):
    """Handle 'sort by recently verified' / 'most services' requests."""
    sort_key = _SORT_PATTERNS.get(message.lower().strip())
    if not (sort_key and last_results):
        return None

    if sort_key == "verified":
        sorted_results = sorted(
            last_results,
            key=lambda s: s.get("last_validated_at") or "",
            reverse=True,
        )
    else:  # "services"
        sorted_results = sorted(
            last_results,
            key=lambda s: len(s.get("also_available") or []),
            reverse=True,
        )
    existing["_last_results"] = sorted_results
    sort_page = sorted_results[:_DISPLAY_PAGE_SIZE]
    existing["_displayed_count"] = len(sort_page)
    save_session_slots(session_id, existing)

    sort_remaining = len(sorted_results) - len(sort_page)
    sort_qr = [
        {"label": "🔍 New search", "value": "Start over"},
        {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
    ]
    if sort_remaining > 0:
        next_sort_page = sorted_results[len(sort_page):len(sort_page) + _DISPLAY_PAGE_SIZE]
        show_next = _count_unique_locations(next_sort_page)
        sort_qr.insert(0, {
            "label": f"📋 Show {show_next} more result{'s' if show_next != 1 else ''}",
            "value": "Show more results",
        })

    sort_label = "most recently verified" if sort_key == "verified" else "most services at location"
    result = {
        "session_id": session_id,
        "response": f"Here are the results sorted by {sort_label}:",
        "follow_up_needed": False,
        "slots": existing,
        "services": sort_page,
        "result_count": len(sort_page),
        "relaxed_search": False,
        "quick_replies": sort_qr,
    }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


def _handle_post_results_question(session_id, message, redacted_message, existing,
                                  last_results, request_id):
    """Handle questions about specific results ('what are the hours', 'tell me
    about the second one', etc.) after results were displayed."""
    post_intent = classify_post_results_question(message)
    if post_intent is None:
        return None

    # Day-specific hours — requires DB lookup
    if post_intent.get("type") == "ask_hours_day":
        result = _handle_hours_for_day(
            session_id, existing, last_results, post_intent, redacted_message, request_id
        )
        if result:
            return result

    pr = answer_from_results(
        post_intent,
        last_results,
        existing.get("_displayed_count", len(last_results)),
    )
    if pr is not None:
        result = {
            "session_id": session_id,
            "response": pr["response"],
            "follow_up_needed": False,
            "slots": existing,
            "services": pr.get("services", []),
            "result_count": len(pr.get("services", [])),
            "relaxed_search": False,
            "quick_replies": pr.get("quick_replies", []),
        }
        _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
        return result

    if post_intent.get("type") == "specific_name":
        query = post_intent.get("query", "that")
        result = _empty_reply(
            session_id,
            "I'm not sure if you're asking about the results "
            "I showed, or if you'd like to search for "
            "something new. Which would you prefer?",
            existing,
            quick_replies=[
                {"label": f"🔍 Search for {query}", "value": f"I need {query}"},
                {"label": "📋 More about results", "value": "Tell me about the first one"},
                {"label": "🔍 New search", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "disambiguation",
                  request_id=request_id, confidence="disambiguated")
        return result

    return None


def _handle_general_conversation(session_id, message, redacted_message, merged,
                                 confidence, tone, request_id):
    """Handle general chat / unrecognized service requests with tiered
    escalation.

    Three cases:

    1. User asks for something specific we can't help with ("I need a job")
       → tiered redirect: tier 1 says what we DO cover, tier 2 adds peer
       navigator, tier 3 goes straight to navigator.
    2. User sends casual chat ("how are you?") → rotating friendly reply.
    3. Anything else → fallback response, optionally with a low-confidence
       peer-navigator offer.
    """
    is_casual_chat = bool(_CASUAL_CHAT_RE.search(message))
    is_service_request_pattern = bool(_SERVICE_NEED_RE.search(message))
    has_unrecognized_need = (
        is_service_request_pattern
        and not merged.get("service_type")
        and not is_casual_chat
    )

    if (has_unrecognized_need
            or (merged.get("location")
                and not merged.get("service_type")
                and len(merged.get("transcript", [])) >= 2)):
        # Track repeated unrecognized requests for response variation
        unrec_count = merged.get("_unrecognized_count", 0) + 1
        merged["_unrecognized_count"] = unrec_count
        save_session_slots(session_id, merged)

        location_label = merged.get("location") or "your area"
        if location_label == NEAR_ME_SENTINEL:
            location_label = "your area"
        if unrec_count >= 3:
            # Tier 3: direct to navigator
            response = (
                "I'm limited to social services and can't help with that. "
                "A peer navigator might be able to point you in the right direction."
            )
            qr = [{"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}]
        elif unrec_count >= 2:
            # Tier 2: shorter, stronger navigator recommendation
            response = (
                "I understand that's what you're looking for, but I'm limited "
                "to social services like food, shelter, and health care. "
                "Would you like to try one of those, or connect with a person "
                "who might know other resources?"
            )
            qr = list(_WELCOME_QUICK_REPLIES) + [
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ]
        else:
            # Tier 1: standard redirect
            response = (
                "I'm not sure I can help with that specifically, but I can "
                f"search for services in {location_label} — things like food, "
                "shelter, clothing, showers, health care, legal help, and more. "
                "What would be most helpful?"
            )
            qr = list(_WELCOME_QUICK_REPLIES) + [
                {"label": "❌ Not what I meant", "value": "not what I meant"},
            ]
        result = _empty_reply(session_id, response, merged, quick_replies=qr)
        _log_turn(session_id, redacted_message, result, "unrecognized_service",
                  request_id=request_id, tone=tone, confidence="low")
        return result

    if is_casual_chat:
        idx = len(merged.get("transcript", [])) % len(_CASUAL_RESPONSES)
        response = _CASUAL_RESPONSES[idx]
    else:
        response = _fallback_response(message, merged)
        # Cultural humility: when the bot can't understand what the user
        # needs (low confidence), acknowledge the limitation rather than
        # pretending the generic response is adequate.
        if confidence == "low" and not merged.get("service_type"):
            response += (
                "\n\nIf I'm missing something important about what you need, "
                "a peer navigator can help — they're real people who know "
                "the system well."
            )

    has_service_intent = bool(merged.get("service_type") or merged.get("location"))
    general_qr = []
    if not has_service_intent and len(merged.get("transcript", [])) <= 1 and not is_casual_chat:
        general_qr = list(_WELCOME_QUICK_REPLIES)
    if confidence in ("medium", "low"):
        general_qr.append({"label": "❌ Not what I meant", "value": "not what I meant"})
    result = _empty_reply(
        session_id, response, merged,
        quick_replies=general_qr,
    )
    _log_turn(session_id, redacted_message, result, "general",
              request_id=request_id, tone=tone, confidence=confidence)
    return result


def _handle_post_pending_confirmation(session_id, message, redacted_message, existing,
                                      response_tone, tone, request_id):
    """Handle messages that arrive while a confirmation was pending, after
    ``_handle_pending_confirmation`` has already had a chance to match a
    direct yes/no/change.

    Three paths:

    1. **Contradiction** — a slot CHANGED (old → new value). The user is
       correcting their search; auto-execute with an acknowledgment prefix.
    2. **Slot fill** — a slot FILLED (None → new value). The user already
       confirmed and is just providing the missing piece; auto-execute
       without re-confirmation.
    3. **No new info** — re-nudge with a tone-matched confirmation prompt.

    Returns a result dict in all three cases.
    """
    existing.pop("_pending_confirmation", None)
    if _USE_LLM:
        pending_extracted = extract_slots_smart(
            message,
            conversation_history=existing.get("transcript", []),
        )
    else:
        pending_extracted = extract_slots(message)
    pending_has_new = any(v is not None and v != [] for k, v in pending_extracted.items()
                          if k not in ("additional_services", "_populations", "_contradiction"))

    # Path 1+2: something changed or filled
    if pending_has_new:
        _SLOT_KEYS = ("service_type", "location", "age", "_gender", "family_status")
        changed = {
            k: pending_extracted[k]
            for k in _SLOT_KEYS
            if pending_extracted.get(k) is not None
            and existing.get(k) is not None
            and pending_extracted[k] != existing[k]
        }
        if changed:
            # Path 1: contradiction detected
            merged_pending = merge_slots(existing, pending_extracted)
            if is_enough_to_answer(merged_pending):
                logger.info(
                    f"[{session_id}] Contradiction during confirmation: "
                    f"{changed} — auto-executing"
                )
                save_session_slots(session_id, merged_pending)
                result = _execute_and_respond(
                    session_id, message, merged_pending, request_id=request_id
                )
                # Prepend acknowledgment of the change
                changes = []
                if "service_type" in changed:
                    changes.append(
                        _SERVICE_LABELS.get(changed["service_type"], changed["service_type"])
                    )
                if "location" in changed:
                    changes.append(changed["location"])
                prefix = f"Got it — switching to {', '.join(changes)}. " if changes else ""
                result["response"] = prefix + result["response"]
                _log_turn(
                    session_id, redacted_message, result,
                    "contradiction_auto_execute", request_id=request_id, tone=tone,
                )
                return result

        # Path 2: slot fill after user already confirmed. The contradiction
        # check above fires only on VALUE CHANGES (old → new). This handles
        # FILLS (None → new) — e.g. crisis step-down "Yes, search" → location
        # follow-up → user provides location. They already said yes; they're
        # just providing the missing piece, not requesting a new search.
        if not changed:
            merged_pending = merge_slots(existing, pending_extracted)
            geolocation_fill = (
                merged_pending.get("location") == NEAR_ME_SENTINEL
                and merged_pending.get("_latitude") is not None
                and merged_pending.get("_longitude") is not None
            )
            if is_enough_to_answer(merged_pending) or geolocation_fill:
                logger.info(
                    f"[{session_id}] Slot fill after confirmation — auto-executing"
                )
                save_session_slots(session_id, merged_pending)
                result = _execute_and_respond(
                    session_id, message, merged_pending, request_id=request_id
                )
                _log_turn(
                    session_id, redacted_message, result,
                    "fill_auto_execute", request_id=request_id, tone=tone,
                )
                return result

    # Path 3: nothing new — re-nudge. Restore the pending flag so the
    # next message is interpreted as a confirmation response.
    existing["_pending_confirmation"] = True
    save_session_slots(session_id, existing)

    nudge_prefix = "Just to make sure — "
    if response_tone == "emotional":
        nudge_prefix = "I hear you. Just to make sure — "
    elif response_tone == "frustrated":
        nudge_prefix = "I understand. Let me just confirm — "
    elif response_tone == "confused":
        nudge_prefix = "No worries — let me just confirm: "
    elif response_tone == "urgent":
        nudge_prefix = "Got it — just to confirm: "

    confirm_msg = (
        nudge_prefix + _build_confirmation_message(existing)
        + ' Tap "Yes, search" to go, or you can change the details.'
    )
    result = {
        "session_id": session_id,
        "response": confirm_msg,
        "follow_up_needed": True,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": _confirmation_quick_replies(existing),
    }
    _log_turn(session_id, redacted_message, result, "confirmation_nudge",
              request_id=request_id, tone=tone)
    return result


def _handle_change_location_request(session_id, redacted_message, existing,
                                    early_extracted, category, tone, request_id):
    """User asked to change search location ("search somewhere else").

    If the message already contains a new location (e.g. "actually Manhattan"),
    apply it directly — prevents a frustration loop where the bot wipes the
    location and re-asks what the user just said. Otherwise, wipe and ask.
    """
    new_loc = early_extracted.get("location")
    if new_loc:
        existing["location"] = new_loc
        save_session_slots(session_id, existing)
        if is_enough_to_answer(existing):
            existing["_pending_confirmation"] = True
            save_session_slots(session_id, existing)
            confirm_msg = _build_confirmation_message(existing)
            result = {
                "session_id": session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "confirmation",
                      request_id=request_id, tone=tone)
            return result
        else:
            follow_up = next_follow_up_question(existing)
            result = {
                "session_id": session_id,
                "response": follow_up,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _follow_up_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "service",
                      request_id=request_id, tone=tone)
            return result

    # No location in message — clear and ask for one
    existing["location"] = None
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "Sure! What neighborhood or borough should I search in?",
        existing,
        quick_replies=[
            {"label": "📍 Use my location", "value": "__use_geolocation__"},
            {"label": "Manhattan", "value": "Manhattan"},
            {"label": "Brooklyn", "value": "Brooklyn"},
            {"label": "Queens", "value": "Queens"},
            {"label": "Bronx", "value": "Bronx"},
            {"label": "Staten Island", "value": "Staten Island"},
        ],
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_change_service_request(session_id, redacted_message, existing,
                                   category, tone, request_id):
    """User asked to change the service type — wipe service_type + service_detail
    and show the service menu."""
    existing["service_type"] = None
    existing.pop("service_detail", None)
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "No problem! What kind of help do you need?",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_reset(session_id, redacted_message, category, tone, request_id):
    """Clear all session state and return the reset response."""
    clear_session(session_id)
    log_session_reset(session_id)
    result = _empty_reply(
        session_id, _RESET_RESPONSE, {},
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_correction(session_id, redacted_message, existing, tone, request_id):
    """Acknowledge a user correction ("that's not what I meant") by clearing
    pending state and echoing what we WERE searching for so they can redirect."""
    existing.pop("_pending_confirmation", None)
    existing.pop("_last_action", None)
    existing.pop("_last_results", None)
    save_session_slots(session_id, existing)
    service_type = existing.get("service_type")
    location = existing.get("location")
    if location == NEAR_ME_SENTINEL:
        location = None
    context = ""
    if service_type and location:
        context = f" I was searching for {service_type} in {location}."
    elif service_type:
        context = f" I was searching for {service_type}."
    result = _empty_reply(
        session_id,
        f"Sorry about that!{context} Let me know what you need — you can "
        f"pick a service below, tell me in your own words, or connect "
        f"with a peer navigator.",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "correction",
              request_id=request_id, tone=tone, confidence="low")
    return result


def _handle_negative_preference(session_id, redacted_message, existing, tone, request_id):
    """Handle "I don't like those" / "none of these" with tiered escalation.

    After 3+ consecutive frustration-counted turns, routes to peer navigator.
    After 2, adds 311 as a live-help option. Otherwise offers to search
    something else.
    """
    # Also count as frustration for escalation tiers (Run 24 eval fix)
    frust_count = existing.get("_frustration_count", 0) + 1
    existing["_frustration_count"] = frust_count

    # When frustration has accumulated, use tiered escalation
    if frust_count >= 3:
        existing["_last_action"] = "frustration"
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "frustration_tier3",
                  request_id=request_id, tone=tone)
        return result
    elif frust_count >= 2:
        existing["_last_action"] = "frustration"
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "A peer navigator would be more helpful — they're real people "
            "who know the system. You can also call 311 for live help.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "frustration_tier2",
                  request_id=request_id, tone=tone)
        return result

    existing["_last_action"] = "negative_preference"
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "I understand — those options aren't what you need. "
        "I can search for a different type of service, or connect "
        "you with a peer navigator who might know of other resources. "
        "What would be most helpful?",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(session_id, redacted_message, result, "negative_preference",
              request_id=request_id, tone=tone)
    return result


def _handle_greeting(session_id, redacted_message, existing, category, tone, request_id):
    """Welcome message. If we have prior session state, offer to resume or reset."""
    if existing and any(v is not None for v in existing.values()):
        response = (
            "Hey again! I still have your earlier search info. "
            "Want to keep going, or would you like to start over?"
        )
        result = _empty_reply(session_id, response, existing)
    else:
        result = _empty_reply(
            session_id, _GREETING_RESPONSE, existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_thanks(session_id, redacted_message, existing, category, tone, request_id):
    """Acknowledge thanks and offer the welcome actions for whatever comes next."""
    result = _empty_reply(
        session_id, _THANKS_RESPONSE, existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
    return result


def _handle_crisis(
    session_id, message, redacted_message, existing,
    early_extracted, has_service_intent, _crisis_result,
    tone, request_id,
):
    """Handle crisis detection. Returns a result dict, or None to fall through."""
    if _crisis_result is None:
        return None

    crisis_category, crisis_response = _crisis_result
    logger.warning(
        f"Session {session_id}: crisis detected, "
        f"category='{crisis_category}'"
    )
    log_crisis_detected(session_id, crisis_category, redacted_message, request_id=request_id)

    if existing.get("_pending_confirmation"):
        existing.pop("_pending_confirmation", None)

    _step_down_categories = (
        "safety_concern", "domestic_violence", "youth_runaway", "assault_victim",
    )
    if has_service_intent and crisis_category in _step_down_categories:
        merged_crisis = merge_slots(existing, early_extracted)

        # Phase 5: When crisis category is domestic_violence, ensure
        # dv_survivor is in _populations so the description boost fires
        # on the subsequent search. The crisis detector catches 54 DV
        # phrases (e.g. "he hits me", "afraid to go home") that the
        # population extractor doesn't cover. This bridges the gap.
        if crisis_category == "domestic_violence":
            pops = set(merged_crisis.get("_populations", []))
            pops.add("dv_survivor")
            merged_crisis["_populations"] = sorted(pops)

        additional = early_extracted.get("additional_services", [])
        if additional and "_queued_services" not in merged_crisis:
            merged_crisis["_queued_services"] = additional
        merged_crisis["_last_action"] = "crisis"
        # Set pending confirmation so tapping "Yes, search" routes through
        # _handle_pending_confirmation → _execute_and_respond.  Without this,
        # the "already shown results" guard (which checks _last_results +
        # confirm_yes + NOT _pending_confirmation) swallows the button press
        # and tells the user "I've already shown the results above" — even
        # though no service cards were shown, only crisis hotline numbers.
        merged_crisis["_pending_confirmation"] = True
        save_session_slots(session_id, merged_crisis)

        svc_label = _SERVICE_LABELS.get(
            early_extracted.get("service_type", ""),
            early_extracted.get("service_type", "services"),
        )
        loc_label = early_extracted.get("location") or "your area"
        step_down_msg = (
            f"\n\nI can also help you find {svc_label} in "
            f"{loc_label} — would you like me to search?"
        )
        result = _empty_reply(
            session_id,
            crisis_response + step_down_msg,
            merged_crisis,
            quick_replies=[
                {"label": f"✅ Yes, search for {svc_label}",
                 "value": "__crisis_geo_search__"},
                {"label": "🤝 Peer navigator",
                 "value": "Connect with peer navigator"},
            ],
        )
    else:
        # Phase 5: Inject dv_survivor even without service intent, so
        # if the user later asks for a service, the boost is in session.
        if crisis_category == "domestic_violence":
            pops = set(existing.get("_populations", []))
            pops.add("dv_survivor")
            existing["_populations"] = sorted(pops)

        existing["_last_action"] = "crisis"
        save_session_slots(session_id, existing)
        result = _empty_reply(session_id, crisis_response, existing)

    _log_turn(session_id, redacted_message, result, "crisis", request_id=request_id, tone=tone)
    return result


def _handle_frustration(session_id, redacted_message, existing, tone, request_id):
    """Handle frustration with escalating responses.

    When the user is frustrated because the bot re-asked for info they
    already provided, and the session already has enough to search,
    offer to proceed instead of just apologizing.
    """
    frust_count = existing.get("_frustration_count", 0) + 1
    existing["_frustration_count"] = frust_count
    existing["_last_action"] = "frustration"
    save_session_slots(session_id, existing)

    # Context recovery: if we already have enough info to search,
    # acknowledge the frustration AND offer to proceed immediately.
    _has_enough = is_enough_to_answer(existing)
    _svc = existing.get("service_type")
    _loc = existing.get("location")

    if frust_count >= 3:
        result = _empty_reply(
            session_id,
            "I'm sorry I haven't been able to help. Let me connect you "
            "with a peer navigator — they can work with you directly.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
    elif frust_count >= 2:
        result = _empty_reply(
            session_id,
            "I hear you — I'm clearly not finding what you need right now. "
            "I think a peer navigator would be more helpful. They're real "
            "people who know the system and can work with you directly. "
            "You can also call 311 for live help anytime.",
            existing,
            quick_replies=[
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                {"label": "🔄 Start over", "value": "Start over"},
            ],
        )
    elif _has_enough and _svc and _loc:
        # First frustration AND we have enough info — acknowledge the
        # mistake and offer to proceed with what we already know.
        svc_label = _SERVICE_LABELS.get(_svc, _svc)
        loc_label = _loc if _loc != NEAR_ME_SENTINEL else "your area"
        result = _empty_reply(
            session_id,
            f"You're right, I apologize for the confusion. "
            f"I already have what I need — I'll look for "
            f"{svc_label} in {loc_label}. Sound good?",
            existing,
            quick_replies=_confirmation_quick_replies(existing),
        )
        existing["_pending_confirmation"] = True
        # Clear _last_action: the frustration context has been resolved —
        # the bot is now asking a confirmation question ("Sound good?").
        # Without this, _handle_context_aware_confirm sees last_action=
        # "frustration" + confirm_yes and routes to escalation instead of
        # _handle_pending_confirmation which executes the search.
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
    else:
        result = _empty_reply(
            session_id, _FRUSTRATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
    _log_turn(session_id, redacted_message, result, "frustration", request_id=request_id, tone=tone)
    return result


def _handle_context_aware_confirm(
    session_id, message, redacted_message, existing,
    category, last_action, tone, request_id,
):
    """Handle yes/no after escalation, emotional, crisis, confused, frustration.

    Returns a result dict, or None if no context-aware handling applies.
    """
    if last_action == "escalation" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "I've shared the contact info above — reach out when you're "
            "ready. Is there anything else I can help with in the meantime?",
            existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
                {"label": "👤 Show contact info again", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "emotional" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            _ESCALATION_RESPONSE,
            existing,
            quick_replies=[
                {"label": "🔍 Search for services", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "crisis" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        _crisis_geo_ready = (
            existing.get("location") == NEAR_ME_SENTINEL
            and existing.get("_latitude") is not None
            and existing.get("_longitude") is not None
        )
        if is_enough_to_answer(existing) or _crisis_geo_ready:
            result = _execute_and_respond(session_id, message, existing, request_id=request_id)
        else:
            follow_up = next_follow_up_question(existing)
            result = _empty_reply(
                session_id, follow_up, existing,
                quick_replies=_follow_up_quick_replies(existing),
            )
            result["follow_up_needed"] = True
        _log_turn(session_id, redacted_message, result, "service", request_id=request_id, tone=tone)
        return result

    if last_action == "confused" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id, _ESCALATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    if last_action == "frustration" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id, _ESCALATION_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "👤 Talk to a person", "value": "Connect with person"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "escalation", request_id=request_id, tone=tone)
        return result

    # Deny handlers for each context
    _deny_contexts = {
        "escalation": (
            "No problem — I'm here if you change your mind. "
            "Is there anything else I can help you with?"
        ),
        "emotional": (
            "That's okay. I'm here whenever you're ready. "
            "If there's anything practical I can help you find, just let me know."
        ),
        "frustrated": (
            "No worries. If you'd like to try something else or talk to a "
            "real person, just let me know."
        ),
        "frustration": (
            "No worries. If you'd like to try something else or talk to a "
            "real person, just let me know."
        ),
        "confused": (
            "That's okay — no rush. I'm here when you're ready. "
            "You can also talk to a real person if that would help."
        ),
        "crisis": (
            "That's okay. The resources above are available anytime. "
            "If you'd like to search for services later, I'm here."
        ),
    }
    if category == "confirm_deny" and last_action in _deny_contexts:
        existing.pop("_last_action", None)
        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        qr = [{"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}]
        if last_action == "crisis":
            qr.append({"label": "🔍 Search for services", "value": "I need help"})
        result = _empty_reply(
            session_id, _deny_contexts[last_action], existing,
            quick_replies=qr,
        )
        _log_turn(session_id, redacted_message, result, "general", request_id=request_id, tone=tone)
        return result

    return None


def _handle_pending_confirmation(
    session_id, message, redacted_message, existing, pending,
    category, tone, request_id, early_extracted=None,
):
    """Handle confirm_yes, confirm_change_*, confirm_deny during pending confirmation.

    Returns a result dict, or None if no pending handling applies.
    """
    if not pending:
        # Check queue offer decline
        queue_offer_active = existing.get("_queued_services") or existing.get("_queue_offer_pending")
        if category == "confirm_deny" and queue_offer_active:
            existing.pop("_queued_services", None)
            existing.pop("_queue_offer_pending", None)
            existing.pop("_queued_services_original", None)
            save_session_slots(session_id, existing)
            result = _empty_reply(
                session_id,
                "No problem! Let me know if you need anything else.",
                existing,
                quick_replies=list(_WELCOME_QUICK_REPLIES),
            )
            _log_turn(session_id, redacted_message, result, "queue_decline", request_id=request_id, tone=tone)
            return result
        return None

    if category == "confirm_yes":
        confirm_extracted = extract_slots(message)
        if (confirm_extracted.get("service_type") is not None
                and confirm_extracted["service_type"] != existing.get("service_type")):
            logger.info(
                f"Service type changed in confirmation: "
                f"'{existing.get('service_type')}' → '{confirm_extracted['service_type']}'"
            )
            existing = merge_slots(existing, confirm_extracted)
        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        result = _execute_and_respond(session_id, message, existing, request_id=request_id)
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_change_service":
        existing.pop("_pending_confirmation", None)
        existing["service_type"] = None
        existing.pop("service_detail", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! What kind of help do you need?",
            existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_change_location":
        existing.pop("_pending_confirmation", None)
        # If the user's message contains a new location (e.g., "change
        # to Brooklyn" or "I already said Manhattan"), use it directly
        # instead of wiping and re-asking.
        new_loc = (early_extracted or {}).get("location")
        if new_loc:
            existing["location"] = new_loc
            save_session_slots(session_id, existing)
            if is_enough_to_answer(existing):
                existing["_pending_confirmation"] = True
                save_session_slots(session_id, existing)
                confirm_msg = _build_confirmation_message(existing)
                result = {
                    "session_id": session_id,
                    "response": confirm_msg,
                    "follow_up_needed": True,
                    "slots": existing,
                    "services": [],
                    "result_count": 0,
                    "relaxed_search": False,
                    "quick_replies": _confirmation_quick_replies(existing),
                }
                _log_turn(session_id, redacted_message, result, "confirmation",
                          request_id=request_id, tone=tone)
                return result
        # No location in message — ask for one
        existing["location"] = None
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "Sure! What neighborhood or borough should I search in?",
            existing,
            quick_replies=[
                {"label": "📍 Use my location", "value": "__use_geolocation__"},
                {"label": "Manhattan", "value": "Manhattan"},
                {"label": "Brooklyn", "value": "Brooklyn"},
                {"label": "Queens", "value": "Queens"},
                {"label": "Bronx", "value": "Bronx"},
                {"label": "Staten Island", "value": "Staten Island"},
            ],
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    if category == "confirm_deny":
        # Fix 1: Check if the denial also contains a new service intent.
        # "I changed my mind, shelter" should switch to shelter, not deny.
        deny_extracted = extract_slots(message)
        new_service = deny_extracted.get("service_type")
        if new_service and new_service != existing.get("service_type"):
            logger.info(
                f"[{session_id}] confirm_deny + new service: "
                f"'{existing.get('service_type')}' → '{new_service}'"
            )
            existing = merge_slots(existing, deny_extracted)
            existing.pop("_pending_confirmation", None)
            save_session_slots(session_id, existing)
            new_label = _SERVICE_LABELS.get(new_service, new_service)
            confirm_msg = f"Got it — switching to {new_label}. " + _build_confirmation_message(existing)
            result = {
                "session_id": session_id,
                "response": confirm_msg,
                "follow_up_needed": True,
                "slots": existing,
                "services": [],
                "result_count": 0,
                "relaxed_search": False,
                "quick_replies": _confirmation_quick_replies(existing),
            }
            _log_turn(session_id, redacted_message, result, "service_switch", request_id=request_id, tone=tone)
            return result

        existing.pop("_pending_confirmation", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! I'll hold onto your info in case you want to "
            "come back to it. What would you like to do?",
            existing,
            quick_replies=[
                {"label": "🔄 Change service", "value": "Change service"},
                {"label": "📍 Change location", "value": "Change location"},
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    return None


# ---------------------------------------------------------------------------
# DAY-SPECIFIC HOURS (Gap 15)
# ---------------------------------------------------------------------------

_ISODOW_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
                 5: "Friday", 6: "Saturday", 7: "Sunday"}


def _handle_hours_for_day(
    session_id, existing, last_results, post_intent, redacted_message, request_id
):
    """Look up schedule for a specific day of the week for displayed services."""
    weekday = post_intent.get("weekday")
    is_weekend = post_intent.get("weekend", False)
    if weekday is None or not last_results:
        return None

    # Collect service IDs
    service_ids = [s["service_id"] for s in last_results if s.get("service_id")]
    if not service_ids:
        return None

    # Fetch from DB
    schedule = fetch_schedule_for_day(service_ids, weekday)

    # For weekend requests, also fetch Sunday
    if is_weekend:
        sunday_sched = fetch_schedule_for_day(service_ids, 7)
        for sid, slots in sunday_sched.items():
            schedule.setdefault(sid, []).extend(slots)

    day_name = _ISODOW_NAMES.get(weekday, f"day {weekday}")
    if is_weekend:
        day_name = "the weekend"

    # Build response lines
    lines = []
    for svc in last_results:
        sid = svc.get("service_id")
        name = svc.get("service_name", "Unknown")
        sched_entries = schedule.get(sid)
        if sched_entries:
            time_strs = []
            for entry in sched_entries:
                opens = _format_time(entry.get("opens_at"))
                closes = _format_time(entry.get("closes_at"))
                if opens and closes:
                    time_strs.append(f"{opens} – {closes}")
            if time_strs:
                lines.append(f"• **{name}**: {', '.join(time_strs)}")
            else:
                lines.append(f"• **{name}**: Hours not confirmed")
        else:
            lines.append(f"• **{name}**: No schedule data for {day_name}")

    response = f"Here are the hours for {day_name}:\n\n" + "\n".join(lines)
    response += "\n\nHours can change — I'd recommend calling ahead to confirm."

    result = {
        "session_id": session_id,
        "response": response,
        "follow_up_needed": False,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": [
            {"label": "📋 Show results again", "value": "Show all results"},
            {"label": "🔍 New search", "value": "Start over"},
        ],
    }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


# ---------------------------------------------------------------------------
# QUERY EXECUTION (after confirmation)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Population-Critical Fallback
# ---------------------------------------------------------------------------
# When a user belongs to a rare population (LGBTQ, youth, senior, veteran)
# and the main shelter query returns results that do NOT match that
# population's rare taxonomy — e.g., 5 generic Soho shelters but no
# "LGBTQ Young Adult" tag — the proximity filter has excluded relevant
# services. Only 2 services are tagged "LGBTQ Young Adult" in the whole
# DB; Ali Forney Center is in Midtown, outside Soho's 1600m radius.
#
# This fallback detects that case and runs a second, targeted query that
# drops proximity in favor of borough-wide, restricted to ONLY the rare
# taxonomies. Results are appended with a contextual note so the user
# understands why they're further away.
#
# See docs/design/POPULATION_FALLBACK_SPEC.md for the full design.

# Rare, population-specific shelter taxonomies. Intentionally excludes
# "drop-in center" and "crisis" — those are in the base default list
# and nearly always have proximity-local results.
_POPULATION_RARE_TAXONOMIES = {
    "youth": ["youth"],
    "lgbtq": ["lgbtq young adult"],
    "senior": ["senior"],
    "veteran": ["veterans", "veterans short-term housing"],
}

# Population-appropriate note prefix for the fallback section. Keys are
# the same labels used in _POPULATION_RARE_TAXONOMIES. When multiple
# populations are active, the composed note joins the relevant labels.
_POPULATION_FALLBACK_LABEL = {
    "youth": "youth-specific",
    "lgbtq": "LGBTQ-friendly",
    "senior": "senior-specific",
    "veteran": "veteran",
}

# How many fallback cards to append. Kept small so the main (proximity-
# local) results remain the headline answer.
_POPULATION_FALLBACK_MAX = 3

# Reverse lookup: city value (from normalize_location) → canonical borough
# name (used for borough-level queries in the fallback). Soho normalizes
# to "New York" which maps back to Manhattan here.
_CITY_TO_BOROUGH = {
    "New York": "Manhattan",
    "Brooklyn": "Brooklyn",
    "Queens": "Queens",
    "Bronx": "Bronx",
    "Staten Island": "Staten Island",
}

# Reverse-geocode GPS coordinates to an NYC borough by finding the
# nearest known neighborhood and using its borough. This is more
# accurate than a per-borough centroid because Queens's geometric
# center is in sparsely-populated east Queens (causing Astoria/LIC
# to mismatch), and Manhattan is a long thin island where midtown's
# centroid is far from Washington Heights or Battery Park.
#
# The underlying data is NEIGHBORHOOD_CENTERS (59 NYC neighborhoods
# with coordinates) and NYC_LOCATION_ALIASES (neighborhood → city
# value). The built table maps each neighborhood's (lat, lon) to a
# canonical borough name that feeds into the population fallback's
# city_list-based borough query. Built lazily and cached.
_NEIGHBORHOOD_TO_BOROUGH_TABLE: list[tuple[float, float, str]] = []


def _build_neighborhood_borough_table() -> list[tuple[float, float, str]]:
    """Materialize [(lat, lon, borough), ...] for every NYC neighborhood
    that has both coordinates AND a known city→borough mapping.

    Neighborhoods whose city value doesn't map to a borough (shouldn't
    happen with the current data, but defensive) are silently skipped.

    Staten Island is supplemented with hardcoded anchor points because
    NEIGHBORHOOD_CENTERS doesn't currently have any Staten Island
    entries — without these, a GPS user on Staten Island would never
    reverse-geocode to "Staten Island" and the fallback would route
    them to Brooklyn instead.
    """
    from app.rag.query_executor import NEIGHBORHOOD_CENTERS, NYC_LOCATION_ALIASES

    rows: list[tuple[float, float, str]] = []
    for name, (lat, lon) in NEIGHBORHOOD_CENTERS.items():
        city = NYC_LOCATION_ALIASES.get(name.lower())
        borough = _CITY_TO_BOROUGH.get(city) if city else None
        if borough:
            rows.append((lat, lon, borough))

    # Staten Island supplement (no SI entries in NEIGHBORHOOD_CENTERS).
    # A handful of well-spread anchors is enough for nearest-point
    # reverse geocoding to work across the borough.
    rows.extend([
        (40.644, -74.074, "Staten Island"),  # St. George (north shore)
        (40.585, -74.145, "Staten Island"),  # New Dorp / mid-island
        (40.510, -74.230, "Staten Island"),  # Tottenville (south)
    ])
    return rows


def _nearest_borough_by_centroid(lat: float, lon: float) -> Optional[str]:
    """Resolve (lat, lon) to a canonical NYC borough name.

    Two-tier strategy:

    1. **Polygon containment** via NYC DCP boundary polygons (authoritative
       within NYC). If the point is inside any of the five boroughs, that
       borough wins. This is the accurate path — no false attribution near
       borough edges.

    2. **Centroid fallback** if the point is outside NYC (e.g., NJ GPS drift,
       Yonkers, Long Island). Returns the borough of the closest NYC
       neighborhood by squared Euclidean distance in lat/lon space. This
       preserves the original useful property of always returning *some*
       borough for callers like the population-critical fallback, which
       needs a borough to query.

    Function name preserved for back-compat with existing callers.

    Returns None if inputs aren't numeric or the neighborhood fallback
    table is also empty (pathological case — shouldn't happen in prod).
    """
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None

    # Tier 1: polygon containment
    try:
        from app.rag.boundaries import borough_from_coords
        poly_borough = borough_from_coords(lat, lon)
        if poly_borough is not None:
            return poly_borough
    except Exception as e:
        # Boundaries module should never fail, but if the vendored GeoJSON
        # is corrupt or shapely blows up, fall through to the centroid
        # path rather than taking down the whole population fallback.
        logger.warning(
            "borough_from_coords failed (lat=%s lon=%s): %s — "
            "falling back to centroid-based resolution", lat, lon, e,
        )

    # Tier 2: centroid fallback (also handles out-of-NYC GPS)
    global _NEIGHBORHOOD_TO_BOROUGH_TABLE
    if not _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        _NEIGHBORHOOD_TO_BOROUGH_TABLE = _build_neighborhood_borough_table()
    if not _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        return None

    best_borough: Optional[str] = None
    best_d2 = float("inf")
    for nlat, nlon, borough in _NEIGHBORHOOD_TO_BOROUGH_TABLE:
        d2 = (lat - nlat) ** 2 + (lon - nlon) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_borough = borough
    return best_borough


def _compute_rare_population_taxonomies(slots: dict) -> tuple[list[str], list[str]]:
    """Determine which rare population-specific shelter taxonomies the
    user's context makes relevant.

    Derives from population context (age/gender/_populations) directly,
    NOT from a diff against the default taxonomy list. The rare taxonomies
    (lgbtq young adult, youth, senior, veterans, veterans short-term
    housing) are ALREADY in the base default list — so a diff would be
    empty and the fallback would never fire for the Cornell Q1 case.

    Returns:
        (taxonomies, labels) — both lists, empty when no rare population
        applies. taxonomies is the flat list of DB taxonomy names to query
        against; labels is the list of population labels for the note.
    """
    taxonomies: list[str] = []
    labels: list[str] = []

    age = slots.get("age")
    age_valid = isinstance(age, int) and age != "skipped"
    populations = slots.get("_populations") or []
    gender = slots.get("_gender")
    family_status = slots.get("family_status")

    # Youth: 16-24, unless the user is explicitly searching for family
    # shelter (family_status set to with_children/with_family) — family
    # shelter has its own taxonomy track.
    if age_valid and 16 <= age <= 24 and family_status not in ("with_children", "with_family"):
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["youth"])
        labels.append("youth")

    # LGBTQ: any of gender=lgbtq/transgender/nonbinary, OR lgbtq in populations
    is_lgbtq = (
        gender in ("lgbtq", "transgender", "nonbinary")
        or "lgbtq" in populations
    )
    if is_lgbtq:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["lgbtq"])
        labels.append("lgbtq")

    # Senior: age >= 62
    if age_valid and age >= 62:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["senior"])
        labels.append("senior")

    # Veteran
    if "veteran" in populations:
        taxonomies.extend(_POPULATION_RARE_TAXONOMIES["veteran"])
        labels.append("veteran")

    # Dedupe taxonomies while preserving order. Labels are always distinct
    # by construction, no dedupe needed.
    seen = set()
    deduped = [t for t in taxonomies if not (t in seen or seen.add(t))]
    return deduped, labels


def _resolve_borough_from_location(location: Optional[str], slots: Optional[dict] = None) -> Optional[str]:
    """Resolve a user-facing location (borough name OR neighborhood) to a
    canonical borough name.

    If `slots` is provided and `location` is the NEAR_ME_SENTINEL (browser
    geolocation active — no text location), falls back to reverse-geocoding
    the user's lat/lon against borough centroids.

    Returns None if the location can't be resolved.

    Note: as of the Option B change to _run_population_fallback, this
    function is no longer called by the population-critical fallback
    (which now runs citywide regardless of user borough). It's retained
    for potential future "browse by borough" UI and for its test coverage
    of the text→borough normalization logic.
    """
    from app.rag.query_executor import is_borough, normalize_location

    # GPS path — user has no text location, just lat/lon. Reverse-geocode
    # against borough centroids. Matches on the sentinel string OR on a
    # falsy location when coords are present.
    if slots is not None:
        _lat = slots.get("_latitude")
        _lon = slots.get("_longitude")
        if (
            (location == NEAR_ME_SENTINEL or not location)
            and _lat is not None
            and _lon is not None
        ):
            return _nearest_borough_by_centroid(_lat, _lon)

    if not location:
        return None

    if is_borough(location):
        # Already a borough — return the canonical Title-cased name so it
        # keys correctly into _CITY_TO_BOROUGH and drives the fallback's
        # city_list-based borough query. "The Bronx" → "Bronx".
        cleaned = location.strip().title()
        if cleaned.lower() == "the bronx":
            return "Bronx"
        return cleaned

    # Neighborhood — normalize to city, then reverse-map to borough.
    city = normalize_location(location)
    return _CITY_TO_BOROUGH.get(city)


def _taxonomies_overlap(card_taxonomies, rare_set_lower: set) -> bool:
    """Check whether a service card's taxonomy tags intersect the rare set.

    DB values are stored in Title Case (e.g. "LGBTQ Young Adult") while
    the rare set is lowercase — compare case-insensitively.
    """
    if not card_taxonomies:
        return False
    card_lower = {str(t).lower() for t in card_taxonomies if t}
    return bool(card_lower & rare_set_lower)


def _run_population_fallback(
    slots: dict,
    rare_taxonomies: list[str],
    labels: list[str],
    existing_service_ids: set,
) -> tuple[list[dict], str]:
    """Execute the fallback query citywide and return (fallback_cards, note_text).

    Returns ([], "") when the fallback query errors out or when all
    fallback cards are duplicates of the main results. Never raises — any
    exception is caught and logged so a fallback failure can't break the
    main response path.

    **Scope: citywide.** We intentionally do NOT filter by the user's
    borough here. Rare-population services are sparse — Ali Forney Center
    (the only LGBTQ Young Adult shelter) is in Manhattan, so a borough-
    scoped query from a Far Rockaway GPS user would return nothing.
    For these rare taxonomies, cross-borough results are strictly
    better than no results. See docs/design/POPULATION_FALLBACK_SPEC.md §Scope.

    Dedupe still applies, so services from the main query don't double-
    up. The "further away" note phrasing is accurate for citywide scope
    too — these cards ARE further from the user, often in a different
    borough, which is exactly why they need the contextual framing.
    """
    _age = slots.get("age")
    age_valid = isinstance(_age, int) and _age != "skipped"

    try:
        # Citywide, no proximity, no gender, no borough. Age is preserved
        # (a 17-year-old still shouldn't see adult-only shelters), but
        # family_status, service_detail, AND location are all dropped —
        # the point of the fallback is to find the rare taxonomies at
        # all, not to satisfy every filter the main query applied.
        fallback_result = query_services(
            service_type="shelter",
            location=None,
            age=_age if age_valid else None,
            gender=None,
            latitude=None,
            longitude=None,
            family_status=None,
            service_detail=None,
            populations=None,
            taxonomy_override=rare_taxonomies,
            max_results=_POPULATION_FALLBACK_MAX,
        )
    except Exception as e:
        logger.warning(f"Population fallback query failed: {e}")
        return [], ""

    cards = fallback_result.get("services", []) or []

    # Dedupe against main results — same service shouldn't appear twice.
    deduped = [c for c in cards if c.get("service_id") not in existing_service_ids]
    if not deduped:
        return [], ""

    # Mark each card so the frontend can visually distinguish fallback
    # cards from main results (future-proofing — current UI renders them
    # in the same carousel). Per-card `fallback_population` reflects
    # which specific rare population this particular card matched, not
    # just the first label — Ali Forney Center shown to a trans
    # 20-year-old should mark as "lgbtq" (its distinguishing tag), not
    # "youth" just because youth came first alphabetically.
    for card in deduped:
        card["is_population_fallback"] = True
        card_tx_lower = {
            str(t).lower() for t in (card.get("service_taxonomies") or []) if t
        }
        matched_label = None
        for label in labels:
            label_tx_lower = {
                t.lower() for t in _POPULATION_RARE_TAXONOMIES.get(label, [])
            }
            if card_tx_lower & label_tx_lower:
                matched_label = label
                break
        # Fall through to the first label only if NOTHING matched — this
        # shouldn't happen (we ran the fallback BECAUSE of these labels)
        # but is a safe default.
        card["fallback_population"] = matched_label or labels[0]

    # Compose the note. Cap at the first two labels to keep prose readable
    # when a user matches multiple populations (e.g., trans veteran youth).
    note_parts = [_POPULATION_FALLBACK_LABEL.get(lb, lb) for lb in labels[:2]]
    if len(note_parts) == 1:
        note_phrase = note_parts[0]
    else:
        note_phrase = " and ".join(note_parts)
    note = (
        f"\n\nI also found {note_phrase} services further away "
        f"that may be helpful:"
    )
    return deduped, note


def _apply_queue_offer(
    session_id: str,
    slots: dict,
    services_list: list,
    bot_response: str,
) -> tuple[str, list]:
    """Append a "You also mentioned X — search for that too?" offer when
    the user queued multiple services in one message.

    Fires only when there's at least one queued service AND the current
    search returned something (``services_list`` non-empty). Pops one
    item from the queue, persists ``_queue_offer_pending`` on the
    session, and replaces the default after-results quick replies with
    yes/no buttons specific to the next queued service.

    Returns (augmented_bot_response, after_results_qr).
    """
    default_qr = [
        {"label": "🔍 New search", "value": "Start over"},
        {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
    ]

    queued = slots.get("_queued_services", [])
    if not (queued and services_list):
        return bot_response, default_qr

    q_item = queued[0]
    next_service = q_item[0]
    next_detail = q_item[1] if len(q_item) > 1 else None
    next_location = q_item[2] if len(q_item) > 2 else None
    remaining = queued[1:]

    if remaining:
        slots["_queued_services"] = remaining
    else:
        slots.pop("_queued_services", None)
    slots["_queue_offer_pending"] = True

    if next_location and next_location != slots.get("location"):
        slots["_queued_location"] = next_location
    save_session_slots(session_id, slots)

    label = next_detail or _SERVICE_LABELS.get(next_service, next_service)
    loc_suffix = ""
    if next_location and next_location != slots.get("location"):
        loc_suffix = f" in {next_location}"
    augmented = bot_response + (
        f"\n\nYou also mentioned {label}{loc_suffix} — would you like me to "
        f"search for that too?"
    )
    qr_value = f"I need {next_service}"
    if next_location:
        qr_value += f" in {next_location}"
    after_results_qr = [
        {"label": f"✅ Yes, search for {label}", "value": qr_value},
        {"label": "❌ No thanks", "value": "No thanks"},
    ]
    return augmented, after_results_qr


def _build_success_response(
    slots: dict,
    results: dict,
    colocated_success: bool,
    colocated_types: list | None,
) -> tuple[str, list, list, int, int, bool]:
    """Translate a successful query result into the user-facing response
    message + card list + pagination metadata.

    Handles:
    - Standard success ("I found N option(s)")
    - Co-located multi-service ("I found N location(s) that offer both food and clothing")
    - "Relaxed" search qualifier when the strict query returned 0 and we
      broadened via the relaxed path
    - Population-critical fallback for shelter queries where no local
      result matches a rare-population taxonomy (LGBTQ YA, youth, senior,
      veteran) — appends citywide fallback cards with their own note.

    Returns (bot_response, services_list, all_services, main_displayed_count,
    result_count, relaxed). The distinction between ``services_list``
    (displayed cards, including fallback) and ``all_services`` (main-query
    cards only, the pagination source) is load-bearing — pagination must
    NOT re-show fallback cards on "Show more."
    """
    all_services = results["services"]
    services_list = all_services[:_DISPLAY_PAGE_SIZE]
    main_displayed_count = len(services_list)
    result_count = len(services_list)
    total_count = _count_unique_locations(all_services)
    relaxed = results.get("relaxed", False)

    qualifier = " (I broadened the search a bit)" if relaxed else ""

    if colocated_success and colocated_types:
        primary = _SERVICE_LABELS.get(
            slots.get("service_type", ""), slots.get("service_type", "")
        )
        queued_original = slots.get("_queued_services_original", [])
        co_labels = []
        for i, t in enumerate(colocated_types):
            detail = queued_original[i][1] if i < len(queued_original) else None
            co_labels.append(detail or _SERVICE_LABELS.get(t, t))
        all_labels = [primary] + co_labels
        combined = " and ".join(all_labels) if len(all_labels) <= 2 else (
            ", ".join(all_labels[:-1]) + ", and " + all_labels[-1]
        )
        bot_response = (
            f"I found {total_count} location(s) that offer both "
            f"{combined.lower()}{qualifier}:"
        )
    else:
        bot_response = f"I found {total_count} option(s) for you{qualifier}:"

    # Population-critical fallback (shelter only). When the user belongs
    # to a rare population (LGBTQ, youth, senior, veteran) and the
    # proximity-local results contain no services tagged with that
    # population's rare taxonomy, run a CITYWIDE targeted query for
    # those taxonomies so Ali Forney / Covenant House / VA etc. can
    # still surface regardless of which borough the user is searching
    # from. See _run_population_fallback and
    # docs/design/POPULATION_FALLBACK_SPEC.md §Scope.
    #
    # Fallback cards are appended to services_list for display but
    # INTENTIONALLY NOT to all_services. all_services drives pagination
    # via slots["_last_results"]; fallback cards are supplementary
    # (shown once with a contextual note) and must not reappear on
    # subsequent "Show more" pages.
    if (
        slots.get("service_type") == "shelter"
        and not results.get("relaxed")
        and not colocated_success
    ):
        rare_tx, rare_labels = _compute_rare_population_taxonomies(slots)
        if rare_tx:
            rare_set_lower = {t.lower() for t in rare_tx}
            has_match = any(
                _taxonomies_overlap(card.get("service_taxonomies"), rare_set_lower)
                for card in all_services
            )
            if not has_match:
                existing_ids = {c.get("service_id") for c in all_services if c.get("service_id")}
                fb_cards, fb_note = _run_population_fallback(
                    slots, rare_tx, rare_labels, existing_ids
                )
                if fb_cards:
                    services_list = services_list + fb_cards
                    result_count = len(services_list)
                    bot_response = bot_response + fb_note
                    # Expose fallback cards separately for potential
                    # post-results queries / admin logging. Not part of
                    # pagination.
                    slots["_fallback_results"] = fb_cards

    return bot_response, services_list, all_services, main_displayed_count, result_count, relaxed


def _build_db_failure_message(session_id: str, slots: dict) -> str:
    """Generate the user-facing message when the DB query throws.

    **Critical invariant:** never call the LLM on this path. When the DB is
    down, the LLM produces helpful-sounding follow-up questions ("To help
    narrow things down…") that look like the intake flow and trap the user
    in an infinite confirmation loop where they keep confirming but never
    get results. The static messages below are intentional.

    Escalates to a "try yourpeer.nyc directly" message after 2+ failures
    in the same session. Mutates ``slots["_search_fail_count"]``.
    """
    fail_count = slots.get("_search_fail_count", 0) + 1
    slots["_search_fail_count"] = fail_count
    save_session_slots(session_id, slots)
    if fail_count >= 2:
        return (
            "I'm still having trouble searching. "
            "Please visit yourpeer.nyc to search directly, "
            "or try again later."
        )
    return (
        "I'm having trouble connecting to the service database "
        "right now. You can try again in a moment, or visit "
        "yourpeer.nyc to search for services directly."
    )


def _execute_and_respond(session_id: str, message: str, slots: dict, request_id: str | None = None) -> dict:
    """Execute the DB query and return results. Called after user confirms."""
    bot_response = None
    services_list = []
    all_services = []
    # Count of main-query cards in `services_list`. Stays in sync with
    # `len(services_list)` EXCEPT when the population-critical fallback
    # appends extra cards — those are supplementary and must NOT count
    # toward pagination (they're shown once with their own note and don't
    # reappear on "Show more"). See the fallback block below.
    _main_displayed_count = 0
    result_count = 0
    relaxed = False
    _FETCH_LIMIT = 25

    try:
        location = slots.get("location")
        use_coords = (
            location == NEAR_ME_SENTINEL
            and slots.get("_latitude") is not None
            and slots.get("_longitude") is not None
        )

        queued = slots.get("_queued_services", [])
        # Only co-locate queued services that share the same location.
        # Cross-borough requests (e.g., shelter in Manhattan while searching
        # food in Brooklyn) should remain queued, not co-located.
        primary_location = slots.get("location")
        colocated_types = []
        for q in queued:
            q_location = q[2] if len(q) > 2 else None
            if q_location is None or q_location == primary_location:
                colocated_types.append(q[0])
        colocated_types = colocated_types or None
        if queued:
            slots["_queued_services_original"] = list(queued)

        _age = slots.get("age")
        _family = slots.get("family_status")
        results = query_services(
            service_type=slots.get("service_type"),
            location=location,
            age=_age if _age != "skipped" else None,
            gender=slots.get("_gender"),
            latitude=slots.get("_latitude") if use_coords else None,
            longitude=slots.get("_longitude") if use_coords else None,
            family_status=_family if _family != "skipped" else None,
            colocated_service_types=colocated_types,
            service_detail=slots.get("service_detail"),
            populations=slots.get("_populations"),
            org_name=slots.get("org_name"),
            no_requirements=bool(slots.get("no_requirements")),
            max_results=_FETCH_LIMIT,
        )

        colocated_success = (
            colocated_types
            and results.get("result_count", 0) > 0
            and not results.get("colocated_fallback")
        )
        if colocated_success:
            slots.pop("_queued_services", None)
            slots.pop("_queued_services_original", None)
            save_session_slots(session_id, slots)

        log_query_execution(
            session_id=session_id,
            template_name=results.get("template_used", "unknown"),
            params=results.get("params_applied", {}),
            result_count=results.get("result_count", 0),
            relaxed=results.get("relaxed", False),
            execution_ms=results.get("execution_ms", 0),
            freshness=results.get("freshness"),
            request_id=request_id,
        )

        if results.get("error"):
            logger.warning(f"Query error: {results['error']}")
            bot_response = (
                "I ran into an issue with that search. "
                "You can try again, or visit yourpeer.nyc directly."
            )
        elif results["result_count"] > 0:
            (bot_response, services_list, all_services,
             _main_displayed_count, result_count, relaxed) = _build_success_response(
                slots, results, colocated_success, colocated_types,
            )
        else:
            bot_response = _no_results_message(slots)

    except Exception as e:
        logger.error(f"Database query failed: {e}")
        # CRITICAL: Do NOT call _fallback_response (LLM) for DB failures.
        # See _build_db_failure_message for the full rationale.
        bot_response = _build_db_failure_message(session_id, slots)

    if bot_response is None:
        # Same principle: don't call LLM for search-path failures.
        bot_response = (
            "I wasn't able to complete the search. "
            "You can try again, or visit yourpeer.nyc directly."
        )

    # Queue offer for multi-intent: if the user asked for multiple services,
    # offer to search the next one. Snapshot the queue BEFORE the helper
    # mutates it — the "Show more" suppression below checks whether a
    # queue offer was MADE, not whether more items remain after.
    queued_before_offer = slots.get("_queued_services", [])
    bot_response, after_results_qr = _apply_queue_offer(
        session_id, slots, services_list, bot_response,
    )

    if services_list:
        slots.pop("_search_fail_count", None)  # Clear on success
        slots["_last_results"] = all_services  # main-query results only — pagination source
        # _displayed_count counts the MAIN cards the user has seen (not
        # fallback cards, which are supplementary and shown once with a
        # note). Using len(services_list) here would double-count fallback
        # cards and cause "Show more" to re-show them on page 2.
        _displayed = _main_displayed_count if _main_displayed_count else len(services_list)
        slots["_displayed_count"] = _displayed
        save_session_slots(session_id, slots)

        # If there are undisplayed main results, add "show more" quick reply.
        # Button label shows the next page's *location* count (not raw
        # service count) so the number matches the carousel card count.
        # Suppressed when a queue offer was made — the queue offer buttons
        # are the user's next action, adding "Show more" alongside would
        # clutter the UI.
        _undisplayed = len(all_services) - _displayed
        if _undisplayed > 0 and not queued_before_offer:
            _next_page_services = all_services[_displayed:_displayed + _DISPLAY_PAGE_SIZE]
            _show_next = _count_unique_locations(_next_page_services)
            after_results_qr.insert(0, {
                "label": f"📋 Show {_show_next} more result{'s' if _show_next != 1 else ''}",
                "value": "Show more results",
            })

    return {
        "session_id": session_id,
        "response": bot_response,
        "follow_up_needed": False,
        "slots": slots,
        "services": services_list,
        "result_count": result_count,
        "relaxed_search": relaxed,
        "quick_replies": after_results_qr if services_list else list(_WELCOME_QUICK_REPLIES),
    }


# ---------------------------------------------------------------------------
# AUDIT LOG HELPER
# ---------------------------------------------------------------------------

def _log_turn(session_id: str, user_msg: str, result: dict, category: str,
              request_id: str | None = None, tone=None, confidence: str = "high"):
    """Log a conversation turn to the audit log."""
    try:
        bot_response_redacted, _ = redact_pii(result.get("response", ""))
        log_conversation_turn(
            session_id=session_id,
            user_message_redacted=user_msg,
            bot_response=bot_response_redacted,
            slots=result.get("slots", {}),
            category=category,
            services_count=result.get("result_count", 0),
            quick_replies=result.get("quick_replies", []),
            follow_up_needed=result.get("follow_up_needed", False),
            request_id=request_id,
            tone=tone,
            confidence=confidence,
        )
    except Exception as e:
        logger.error(f"Failed to log conversation turn: {e}")
