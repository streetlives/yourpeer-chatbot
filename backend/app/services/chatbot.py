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

    # --- PII Redaction ---
    redacted_message, pii_detections = redact_pii(message)
    if pii_detections:
        logger.info(
            f"Session {session_id}: redacted {len(pii_detections)} PII item(s) "
            f"from message: {[d.pii_type for d in pii_detections]}"
        )

    # --- PII Safety Warning ---
    # When a user shares highly sensitive PII (SSN, phone number), warn
    # them immediately. The message is still processed (with PII redacted),
    # but the warning is prepended to whatever response follows.
    _PII_WARN_TYPES = {"ssn", "phone"}
    _pii_warning = ""
    if pii_detections:
        _detected = {d.pii_type for d in pii_detections}
        _sensitive = _detected & _PII_WARN_TYPES
        if _sensitive:
            if "ssn" in _sensitive:
                _pii_warning = (
                    "For your safety, please don't share your Social Security "
                    "number or other sensitive personal information in this "
                    "chat. I've removed it from the conversation.\n\n"
                )
            else:
                _pii_warning = (
                    "Just a heads up — I've removed your phone number from "
                    "the conversation to protect your privacy. You don't need "
                    "to share personal info to search for services.\n\n"
                )

    existing = get_session_slots(session_id)

    # Store browser geolocation coords in session if provided
    has_coords = latitude is not None and longitude is not None
    if has_coords:
        existing["_latitude"] = latitude
        existing["_longitude"] = longitude
        save_session_slots(session_id, existing)

    # --- EXTRACT SLOTS FIRST (before classification) ---
    early_extracted = extract_slots(message)
    _extraction_source = "regex" if early_extracted.get("service_type") else None

    # --- TIER 2: SEMANTIC ROUTING (before LLM gate) ---
    # The semantic router is a LOCAL model — no API key needed.
    # It runs here (not just inside extract_slots_smart) so that:
    #   1. It fires in regex-only mode (no ANTHROPIC_API_KEY)
    #   2. It fires before the unified LLM gate (saving an LLM call)
    #   3. It fires before routing, preventing "general" fallthrough
    if early_extracted.get("service_type") is None:
        from app.services.semantic_router import classify_service as _semantic_classify
        from app.services.semantic_router import is_available as _semantic_available

        if _semantic_available():
            _semantic_match = _semantic_classify(message)
            if _semantic_match is not None:
                logger.info(
                    f"Session {session_id}: semantic router matched "
                    f"'{_semantic_match.service_type}' "
                    f"(confidence={_semantic_match.confidence:.3f})"
                )
                early_extracted["service_type"] = _semantic_match.service_type
                _extraction_source = "semantic"

                # Merge population from semantic router
                if _semantic_match.population:
                    existing_pops = set(early_extracted.get("_populations") or [])
                    existing_pops.add(_semantic_match.population)
                    early_extracted["_populations"] = sorted(existing_pops)

    has_service_intent = (
        early_extracted.get("service_type") is not None
        or early_extracted.get("org_name") is not None
    )

    # --- CLASSIFY ACTION (regex, instant) ---
    _action_pre = _classify_action(message)

    # --- UNIFIED LLM CLASSIFICATION GATE ---
    _llm_tone = None
    _llm_action = None
    _SKIP_UNIFIED_ACTIONS = {
        "reset", "greeting", "thanks", "bot_identity", "bot_question",
        "confirm_yes", "confirm_deny", "confirm_change_service",
        "confirm_change_location", "correction", "negative_preference",
        "escalation",
    }
    _regex_tone_pre = _classify_tone(message, crisis_result=_CRISIS_NOT_CHECKED)
    _needs_unified = (
        _USE_LLM
        and not has_service_intent
        and _action_pre not in _SKIP_UNIFIED_ACTIONS
        and _regex_tone_pre is None
        and len(message.split()) >= 4
    )
    if _needs_unified:
        try:
            _unified = classify_unified(message)
            if _unified:
                if _unified.get("service_type"):
                    logger.info(
                        f"Unified gate found service_type="
                        f"'{_unified['service_type']}' that regex missed"
                    )
                    early_extracted["service_type"] = _unified["service_type"]
                    _extraction_source = "llm_gate"
                    if _unified.get("service_detail"):
                        early_extracted["service_detail"] = _unified["service_detail"]
                    if _unified.get("location"):
                        early_extracted["location"] = _unified["location"]
                    if _unified.get("additional_services"):
                        early_extracted["additional_services"] = _unified["additional_services"]
                    if _unified.get("urgency"):
                        early_extracted["urgency"] = _unified["urgency"]
                    if _unified.get("age"):
                        early_extracted["age"] = _unified["age"]
                    if _unified.get("family_status"):
                        early_extracted["family_status"] = _unified["family_status"]
                    if _unified.get("gender"):
                        early_extracted["_gender"] = _unified["gender"]
                    has_service_intent = True

                if _unified.get("tone"):
                    _llm_tone = _unified["tone"]
                    logger.info(f"Unified gate detected tone='{_llm_tone}'")
                if _unified.get("action"):
                    _llm_action = _unified["action"]
                    logger.info(f"Unified gate detected action='{_llm_action}'")
                    if _action_pre is None:
                        _action_pre = _llm_action
        except Exception as e:
            logger.error(f"Unified LLM classification failed: {e}")

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
        _last_results = existing.get("_last_results")
        _is_confirmation_action = _action_pre in (
            "confirm_change_service", "confirm_change_location",
            "confirm_yes", "confirm_deny", "reset", "greeting",
        )
        # Handle confirm_yes / confirm_deny after results when no pending
        # confirmation exists. Without this, these messages fall through to
        # extraction and re-trigger the same search.
        # Guard: when the message ALSO contains a new service intent
        # (e.g., "Search for employment in Manhattan"), the new intent
        # should override the confirm action. Without this guard,
        # "search for" matches confirm_yes and the user's new request
        # is swallowed.
        if (_last_results
                and _action_pre in ("confirm_yes", "confirm_deny")
                and not existing.get("_pending_confirmation")
                and not existing.get("_queue_offer_pending")
                and not existing.get("_queued_services")
                and not has_service_intent):

            if _action_pre == "confirm_yes":
                # "Yes, search" after results already shown
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

            else:  # confirm_deny
                # "nah I'm good" / "no thanks" after results
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

        if _last_results and not has_service_intent and not _is_confirmation_action:
            _is_frustration_or_rejection = (
                tone == "frustrated"
                or _action_pre == "negative_preference"
                or _action_pre == "correction"
            )
            if _is_frustration_or_rejection:
                existing.pop("_last_results", None)
                save_session_slots(session_id, existing)
            elif early_extracted.get("location"):
                existing.pop("_last_results", None)
                save_session_slots(session_id, existing)
            else:
                # "Show all results" / "Show more results"
                _show_patterns = (
                    "show all results", "show results", "show all",
                    "show more results", "show more", "more results",
                    "any others", "what else", "next results",
                    "any more", "see more",
                )
                if message.lower().strip() in _show_patterns:
                    displayed = existing.get("_displayed_count", 0)
                    if displayed and displayed < len(_last_results):
                        # Show the undisplayed remainder
                        remaining = _last_results[displayed:]
                        existing["_displayed_count"] = len(_last_results)
                        save_session_slots(session_id, existing)
                        result = {
                            "session_id": session_id,
                            "response": f"Here are {len(remaining)} more result{'s' if len(remaining) != 1 else ''}:",
                            "follow_up_needed": False,
                            "slots": existing,
                            "services": remaining,
                            "result_count": len(remaining),
                            "relaxed_search": False,
                            "quick_replies": [
                                {"label": "🔍 New search", "value": "Start over"},
                                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                            ],
                        }
                    else:
                        # No more to show — re-display all
                        result = {
                            "session_id": session_id,
                            "response": "Here are all the results again:",
                            "follow_up_needed": False,
                            "slots": existing,
                            "services": _last_results,
                            "result_count": len(_last_results),
                            "relaxed_search": False,
                            "quick_replies": [
                                {"label": "🔍 New search", "value": "Start over"},
                                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                            ],
                        }
                    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
                    return result

                # Gap 8: Sort options
                _sort_patterns = {
                    "sort by recently verified": "verified",
                    "sort by recently updated": "verified",
                    "sort by newest": "verified",
                    "sort by most services": "services",
                    "most services": "services",
                }
                _sort_key = _sort_patterns.get(message.lower().strip())
                if _sort_key and _last_results:
                    if _sort_key == "verified":
                        sorted_results = sorted(
                            _last_results,
                            key=lambda s: s.get("last_validated_at") or "",
                            reverse=True,
                        )
                    else:  # "services"
                        sorted_results = sorted(
                            _last_results,
                            key=lambda s: len(s.get("also_available") or []),
                            reverse=True,
                        )
                    existing["_last_results"] = sorted_results
                    existing["_displayed_count"] = len(sorted_results)
                    save_session_slots(session_id, existing)
                    result = {
                        "session_id": session_id,
                        "response": f"Here are the results sorted by {'most recently verified' if _sort_key == 'verified' else 'most services at location'}:",
                        "follow_up_needed": False,
                        "slots": existing,
                        "services": sorted_results[:10],
                        "result_count": len(sorted_results[:10]),
                        "relaxed_search": False,
                        "quick_replies": [
                            {"label": "🔍 New search", "value": "Start over"},
                            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                        ],
                    }
                    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
                    return result

                post_intent = classify_post_results_question(message)
                if post_intent is not None:
                    # Gap 15: day-specific hours — requires DB lookup
                    if post_intent.get("type") == "ask_hours_day":
                        result = _handle_hours_for_day(
                            session_id, existing, _last_results, post_intent, redacted_message, request_id
                        )
                        if result:
                            return result

                    pr = answer_from_results(post_intent, _last_results)
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
                            f"I'm not sure if you're asking about the results "
                            f"I showed, or if you'd like to search for "
                            f"something new. Which would you prefer?",
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

        if _last_results and (has_service_intent or _is_confirmation_action):
            existing.pop("_last_results", None)
            # When results were already shown and the user asks for something
            # new, treat it as a fresh search — clear the old service slots
            # so they don't compound with the new request. Multi-service
            # should only happen within a single message or before results.
            if has_service_intent:
                existing.pop("service_type", None)
                existing.pop("service_detail", None)
                existing.pop("_queued_services", None)
                existing.pop("_queued_services_original", None)
                existing.pop("_queue_offer_pending", None)
                existing.pop("_pending_confirmation", None)
                existing.pop("_displayed_count", None)
            save_session_slots(session_id, existing)

    # --- COMBINE INTO ROUTING CATEGORY ---
    action = _action_pre
    _response_tone = tone
    # Confidence reflects how the service_type was determined:
    #   "high"     — regex keyword match (deterministic)
    #   "semantic" — semantic embedding match (Tier 2, high but not deterministic)
    #   "medium"   — LLM classification (unified gate or fallback)
    #   "low"      — no classification succeeded, using fallback
    if _extraction_source == "regex":
        _confidence = "high"
    elif _extraction_source == "semantic":
        _confidence = "semantic"
    elif _extraction_source == "llm_gate":
        _confidence = "medium"
    else:
        _confidence = "high"  # default for non-service routes (greeting, reset, etc.)

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
            _confidence = "medium"
        else:
            category = "general"
            _confidence = "low"
    else:
        category = "general"
        _confidence = "low"

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
    # SAMHSA Cultural Humility: acknowledge the language gap rather than
    # returning silence or an English-only response. Even before full
    # Spanish support ships, detecting common Spanish phrases and
    # responding with an acknowledgment shows awareness.
    _SPANISH_RE = re.compile(
        r"\b(necesito|ayuda|comida|refugio|albergue|por favor|"
        r"no hablo ingles|no hablo inglés|hola|buenos dias|"
        r"buenas tardes|buenas noches|tengo hambre|"
        r"necesito ayuda|donde puedo|dónde puedo)\b", re.I,
    )
    _spanish_detected = _SPANISH_RE.search(message)
    _spanish_acknowledgment = ""

    if _spanish_detected and not has_service_intent:
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
        return result
    elif _spanish_detected and has_service_intent:
        # Spanish mixed with a service request — acknowledge the language
        # but still process the search. The acknowledgment is prepended
        # to whatever response follows (confirmation or follow-up).
        _spanish_acknowledgment = (
            "I can see you may prefer Spanish — lo siento, por ahora "
            "solo puedo ayudar en inglés. I'll do my best to help.\n\n"
        )

    # --- Reset ---
    if category == "reset":
        clear_session(session_id)
        log_session_reset(session_id)
        result = _empty_reply(
            session_id, _RESET_RESPONSE, {},
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    # --- Correction ---
    if category == "correction":
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

    # --- Negative preference ---
    if category == "negative_preference":
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

    # --- Greeting ---
    if category == "greeting":
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

    # --- Thanks ---
    if category == "thanks":
        result = _empty_reply(
            session_id, _THANKS_RESPONSE, existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    # --- Help ---
    if category == "help":
        # Shame + help: "I'm embarrassed to ask for help" is expressing
        # vulnerability, not asking what the bot can do. Route to the
        # emotional handler with the shame-specific response.
        if _response_tone == "emotional":
            _help_lower = message.lower()
            _is_shame_help = any(
                s in _help_lower for s in [
                    "embarrassed", "ashamed", "pathetic", "humiliating",
                    "hard to ask", "hard for me", "hate asking", "hate to ask",
                    "difficult to ask", "burden", "swallow my pride",
                ]
            )
            if _is_shame_help:
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
        if _response_tone in ("confused", "emotional"):
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

    # --- Bot Identity ---
    if category == "bot_identity":
        result = _empty_reply(
            session_id, _BOT_IDENTITY_RESPONSE, existing,
            quick_replies=[
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        )
        _log_turn(session_id, redacted_message, result, category, request_id=request_id, tone=tone)
        return result

    # --- Bot capability questions ---
    if category == "bot_question":
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

    # --- Demographic skip ("I'd rather not say" / "skip") ---
    # SAMHSA Empowerment principle: users control what they share.
    # When a demographic question is pending and the user declines,
    # proceed to confirmation with what we have.
    _SKIP_PHRASES = [
        "i'd rather not say", "id rather not say", "i would rather not say",
        "rather not say", "prefer not to say", "prefer not to",
        "skip", "skip this", "don't want to say", "dont want to say",
        "none of your business", "that's personal", "thats personal",
        "pass",
    ]
    _skip_lower = message.lower().strip()
    _is_skip = any(p in _skip_lower for p in _SKIP_PHRASES) or _skip_lower in ("skip", "pass")
    _is_demographic_pending = (
        existing.get("service_type")
        and existing.get("location")
        and not existing.get("_pending_confirmation")
        and (not existing.get("age") or not existing.get("family_status"))
    )
    if _is_skip and _is_demographic_pending:
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

    # --- Location unknown ---
    _LOCATION_UNKNOWN_PHRASES = [
        "i don't know", "i dont know", "idk", "not sure", "i'm not sure",
        "im not sure", "no idea", "don't know", "dont know",
        "i don't know where i am", "i dont know where i am",
        "not sure where i am", "don't know where i am",
        "dont know where i am", "no clue",
        "anywhere", "wherever", "doesn't matter", "doesnt matter",
        "it doesn't matter", "it doesnt matter",
        "where i am",
    ]
    _LOCATION_UNKNOWN_EXACT = ["here", "right here"]
    _msg_lower = message.lower().strip()
    _is_location_unknown = (
        any(p in _msg_lower for p in _LOCATION_UNKNOWN_PHRASES)
        or _msg_lower in _LOCATION_UNKNOWN_EXACT
    )
    if (existing.get("service_type")
            and not existing.get("location")
            and not existing.get("_pending_confirmation")
            and _is_location_unknown):
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
        _log_turn(session_id, redacted_message, result, "location_unknown", request_id=request_id, tone=tone)
        return result

    # --- Confused / Overwhelmed ---
    if category == "confused":
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

    # --- Emotional expression ---
    if category == "emotional":
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

    # --- Frustration ---
    if category == "frustration":
        result = _handle_frustration(session_id, redacted_message, existing, tone, request_id)
        return result

    # --- Escalation ---
    if category == "escalation":
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

        if category == "confirm_change_service":
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

    # --- Handle confirmation responses ---
    pending = existing.get("_pending_confirmation")
    confirm_result = _handle_pending_confirmation(
        session_id, message, redacted_message, existing, pending,
        category, tone, request_id,
    )
    if confirm_result:
        return confirm_result

    # If pending confirmation but user typed something new
    if pending:
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

        # Fix 3: Contradiction detection — if a slot CHANGED (not just
        # filled), the user is correcting their search. Auto-execute
        # instead of re-confirming to reduce friction.
        if pending_has_new:
            _SLOT_KEYS = ("service_type", "location", "age", "_gender", "family_status")
            _changed = {
                k: pending_extracted[k]
                for k in _SLOT_KEYS
                if pending_extracted.get(k) is not None
                and existing.get(k) is not None
                and pending_extracted[k] != existing[k]
            }
            if _changed:
                merged_pending = merge_slots(existing, pending_extracted)
                if is_enough_to_answer(merged_pending):
                    logger.info(
                        f"[{session_id}] Contradiction during confirmation: "
                        f"{_changed} — auto-executing"
                    )
                    save_session_slots(session_id, merged_pending)
                    result = _execute_and_respond(
                        session_id, message, merged_pending, request_id=request_id
                    )
                    # Prepend acknowledgment of the change
                    changes = []
                    if "service_type" in _changed:
                        changes.append(
                            _SERVICE_LABELS.get(_changed["service_type"], _changed["service_type"])
                        )
                    if "location" in _changed:
                        changes.append(_changed["location"])
                    prefix = f"Got it — switching to {', '.join(changes)}. " if changes else ""
                    result["response"] = prefix + result["response"]
                    _log_turn(
                        session_id, redacted_message, result,
                        "contradiction_auto_execute", request_id=request_id, tone=tone,
                    )
                    return result

        if not pending_has_new:
            existing["_pending_confirmation"] = True
            save_session_slots(session_id, existing)

            nudge_prefix = "Just to make sure — "
            if _response_tone == "emotional":
                nudge_prefix = "I hear you. Just to make sure — "
            elif _response_tone == "frustrated":
                nudge_prefix = "I understand. Let me just confirm — "
            elif _response_tone == "confused":
                nudge_prefix = "No worries — let me just confirm: "
            elif _response_tone == "urgent":
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
            _log_turn(session_id, redacted_message, result, "confirmation_nudge", request_id=request_id, tone=tone)
            return result

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

    # Shame detection — sub-category of emotional tone that needs a
    # specific normalizing prefix rather than generic empathy.
    _SHAME_SIGNALS = [
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
    ]
    _msg_lower_tone = message.lower()
    _is_shame = any(s in _msg_lower_tone for s in _SHAME_SIGNALS)

    # Tone-based prefix
    _is_service_flow = category == "service"
    _tone_prefix = ""

    # Medical urgency — running out of essential medication is medically
    # dangerous. This fires when the message contains BOTH a medication
    # depletion signal AND a medical keyword, preventing false positives
    # on generic "ran out of" phrases.
    _MEDICATION_DEPLETION = [
        "ran out of", "run out of", "running out of", "out of my",
        "don't have my", "dont have my", "lost my medication",
        "lost my medicine",
        "no more", "can't get my", "cant get my", "ran out of my",
    ]
    _MEDICATION_WORDS = [
        "insulin", "medication", "medicine", "prescription",
        "inhaler", "epipen", "pills", "meds",
    ]
    _is_medical_urgent = (
        _is_service_flow
        and any(s in _msg_lower_tone for s in _MEDICATION_DEPLETION)
        and any(s in _msg_lower_tone for s in _MEDICATION_WORDS)
    )

    if _is_shame and _is_service_flow:
        # Shame-specific normalizing prefix — NOT generic "I hear you"
        _tone_prefix = "It takes real strength to reach out — a lot of people use these services, and there's no shame in it. "
    elif _is_medical_urgent:
        # Medical urgency — medication depletion needs a specific
        # acknowledgment that the bot understands the medical seriousness,
        # not just generic urgency ("I can see this is urgent").
        _tone_prefix = "That sounds urgent — let me help you find care right away. "
    elif _response_tone == "emotional" and _is_service_flow:
        _tone_prefix = "I hear you, and I want to help. "
    elif _response_tone == "frustrated" and _is_service_flow:
        _tone_prefix = "I understand this has been frustrating. Let me try something different. "
    elif _response_tone == "confused" and _is_service_flow:
        _tone_prefix = "No worries — let me help you with that. "
    elif _response_tone == "urgent" and _is_service_flow:
        _tone_prefix = "I can see this is urgent — let me find something right away. "

    # Check for emotional context from a previous turn in this session.
    # When the user expressed emotion in an earlier message (e.g., "I'm really
    # struggling and need food and shelter"), subsequent confirmations should
    # stay warm instead of resetting to a cold default.
    if not _tone_prefix and _is_service_flow:
        _prior_emotion = existing.get("_emotional_context")
        if _prior_emotion == "shame":
            _tone_prefix = "Still here with you. "
        elif _prior_emotion == "medical_urgent":
            _tone_prefix = "Let's get you to the right place. "
        elif _prior_emotion:
            _tone_prefix = "I'm still here with you. "

    # Baseline warmth: when no emotional/shame/urgent context was detected
    # and it's a routine service flow, add a small warmth prefix to prevent
    # the bot from feeling "functional but flat" (the tone=3 gap).
    if not _tone_prefix and _is_service_flow:
        _tone_prefix = random_warmth_prefix()

    # Persist emotional context for subsequent turns
    if _is_shame:
        merged["_emotional_context"] = "shame"
    elif _is_medical_urgent:
        merged["_emotional_context"] = "medical_urgent"
    elif _response_tone == "emotional" and _is_service_flow:
        merged["_emotional_context"] = "emotional"

    # Override casual tone for sensitive life situations (Run 24 eval fix)
    # Also SET a prefix when tone=None but the message contains sensitive context.
    _SENSITIVE_CONTEXT_RE = re.compile(
        r"\b(foster care|aging out|aged out|fleeing|escaped|"
        r"just got out of jail|just got out of prison|domestic violence)\b", re.I,
    )
    if _SENSITIVE_CONTEXT_RE.search(message):
        _tone_prefix = "I understand this is a difficult situation. Let me help. "
        merged["_emotional_context"] = "sensitive"

    # Re-save if emotional context was set after the initial save (line 1113).
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

    # --- General conversation ---
    _CASUAL_CHAT_RE = re.compile(
        r"\b(how are you|how's it going|hows it going|what's up|whats up|"
        r"hey there|just (wanted to|wanna) (chat|talk)|having a good day|"
        r"good morning|good afternoon|good evening|how you doing|"
        r"what are you up to|how do you do)\b", re.I
    )
    _is_casual_chat = bool(_CASUAL_CHAT_RE.search(message))

    _need_re = re.compile(
        r"\b(?:i need|i want|can you find|looking for|help me find|"
        r"get me|find me|i'm looking for|im looking for)\b",
        re.I,
    )
    _is_service_request_pattern = bool(_need_re.search(message))
    _has_unrecognized_need = (
        _is_service_request_pattern
        and not merged.get("service_type")
        and not _is_casual_chat
    )
    if (_has_unrecognized_need
            or (merged.get("location")
                and not merged.get("service_type")
                and len(merged.get("transcript", [])) >= 2)):
        # Track repeated unrecognized requests for response variation
        _unrec_count = merged.get("_unrecognized_count", 0) + 1
        merged["_unrecognized_count"] = _unrec_count
        save_session_slots(session_id, merged)

        location_label = merged.get("location", "your area")
        if location_label == NEAR_ME_SENTINEL:
            location_label = "your area"
        if _unrec_count >= 3:
            # Tier 3: direct to navigator
            response = (
                "I'm limited to social services and can't help with that. "
                "A peer navigator might be able to point you in the right direction."
            )
            qr = [{"label": "🤝 Peer navigator", "value": "Connect with peer navigator"}]
        elif _unrec_count >= 2:
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

    if _is_casual_chat:
        _CASUAL_RESPONSES = [
            "I'm doing well, thanks for asking! I'm here whenever you need me.",
            "Hey! Just here and ready to help whenever you are.",
            "Doing good! Let me know if there's anything I can help you find.",
        ]
        _idx = len(merged.get("transcript", [])) % len(_CASUAL_RESPONSES)
        response = _CASUAL_RESPONSES[_idx]
    else:
        response = _fallback_response(message, merged)
        # Cultural humility: when the bot can't understand what the user
        # needs (low confidence), acknowledge the limitation rather than
        # pretending the generic response is adequate.
        if _confidence == "low" and not merged.get("service_type"):
            response += (
                "\n\nIf I'm missing something important about what you need, "
                "a peer navigator can help — they're real people who know "
                "the system well."
            )

    has_service_intent = bool(merged.get("service_type") or merged.get("location"))
    _general_qr = []
    if not has_service_intent and len(merged.get("transcript", [])) <= 1 and not _is_casual_chat:
        _general_qr = list(_WELCOME_QUICK_REPLIES)
    if _confidence in ("medium", "low"):
        _general_qr.append({"label": "❌ Not what I meant", "value": "not what I meant"})
    result = _empty_reply(
        session_id, response, merged,
        quick_replies=_general_qr,
    )
    _log_turn(session_id, redacted_message, result, "general",
              request_id=request_id, tone=tone, confidence=_confidence)
    return result


# ---------------------------------------------------------------------------
# HANDLER HELPERS (extracted from generate_reply for readability)
# ---------------------------------------------------------------------------

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
        save_session_slots(session_id, merged_crisis)

        svc_label = _SERVICE_LABELS.get(
            early_extracted.get("service_type", ""),
            early_extracted.get("service_type", "services"),
        )
        loc_label = early_extracted.get("location", "your area")
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
                 "value": "Yes, search"},
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
    """Handle frustration with escalating responses."""
    frust_count = existing.get("_frustration_count", 0) + 1
    existing["_frustration_count"] = frust_count
    existing["_last_action"] = "frustration"
    save_session_slots(session_id, existing)

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
    if last_action in ("escalation", "emotional") and category == "confirm_yes":
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

    if last_action == "crisis" and category == "confirm_yes":
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)
        if is_enough_to_answer(existing):
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
    category, tone, request_id,
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

def _execute_and_respond(session_id: str, message: str, slots: dict, request_id: str | None = None) -> dict:
    """Execute the DB query and return results. Called after user confirms."""
    bot_response = None
    services_list = []
    all_services = []
    result_count = 0
    relaxed = False
    _DISPLAY_LIMIT = 10
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
            all_services = results["services"]
            services_list = all_services[:_DISPLAY_LIMIT]
            result_count = len(services_list)
            relaxed = results.get("relaxed", False)

            qualifier = ""
            if relaxed:
                qualifier = " (I broadened the search a bit)"

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
                    f"I found {result_count} location(s) that offer both "
                    f"{combined.lower()}{qualifier} \u2014 here\u2019s what\u2019s available:"
                )
            else:
                bot_response = (
                    f"I found {result_count} option(s) for you{qualifier}:"
                )
        else:
            bot_response = _no_results_message(slots)

    except Exception as e:
        logger.error(f"Database query failed: {e}")
        # CRITICAL: Do NOT call _fallback_response (LLM) for DB failures.
        # When the DB is down, Claude generates helpful-sounding follow-up
        # questions ("To help narrow things down...") that look like the
        # intake flow, trapping the user in an infinite confirmation loop
        # where they keep confirming but never get results.
        _fail_count = slots.get("_search_fail_count", 0) + 1
        slots["_search_fail_count"] = _fail_count
        save_session_slots(session_id, slots)
        if _fail_count >= 2:
            bot_response = (
                "I'm still having trouble searching. "
                "Please visit yourpeer.nyc to search directly, "
                "or try again later."
            )
        else:
            bot_response = (
                "I'm having trouble connecting to the service database "
                "right now. You can try again in a moment, or visit "
                "yourpeer.nyc to search for services directly."
            )

    if bot_response is None:
        # Same principle: don't call LLM for search-path failures.
        bot_response = (
            "I wasn't able to complete the search. "
            "You can try again, or visit yourpeer.nyc directly."
        )

    after_results_qr = [
        {"label": "🔍 New search", "value": "Start over"},
        {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
    ]

    # Queue offer for multi-intent
    queued = slots.get("_queued_services", [])
    if queued and services_list:
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
        bot_response += (
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

    if services_list:
        slots.pop("_search_fail_count", None)  # Clear on success
        slots["_last_results"] = all_services  # Store ALL fetched (up to 25)
        slots["_displayed_count"] = len(services_list)  # Track what user has seen
        save_session_slots(session_id, slots)

        # If there are undisplayed results, add "show more" quick reply
        _undisplayed = len(all_services) - len(services_list)
        if _undisplayed > 0 and not queued:
            after_results_qr.insert(0, {
                "label": f"📋 Show {_undisplayed} more result{'s' if _undisplayed != 1 else ''}",
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
