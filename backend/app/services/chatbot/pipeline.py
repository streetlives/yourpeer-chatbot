"""Classification pipeline — runs before the handler dispatch.

Each helper produces one slice of the state that eventually flows into
``MessageContext``. The helpers are kept independent so each can be
unit-tested in isolation (see ``tests/unit/test_chatbot_extracted_helpers.py``).
"""

import logging

from app.privacy.pii_redactor import redact_pii
from app.services.session_store import save_session_slots
from app.services.slot_extractor import NEAR_ME_SENTINEL, extract_slots

from .context import _USE_LLM


# Phase 4 (April 2026): the gap-filler at `_run_llm_gate` now routes
# through `app.services.slot_extraction.extract()` for both slot
# enrichment (service_type, location, demographics) and advisory
# classification (tone, action). The legacy `classify_unified` is no
# longer imported here. See UNIFIED_EXTRACTOR_MIGRATION.md.


logger = logging.getLogger(__name__)


# PII types that warrant a user-facing warning when shared. Other types
# (names, emails) are quietly redacted but don't trigger a warning.
_PII_WARN_TYPES = {"ssn", "phone"}


# Actions that should NOT trigger the unified LLM classification gate —
# these have high-confidence regex classifiers, so an additional LLM call
# would only waste tokens without improving routing.
_SKIP_UNIFIED_ACTIONS = frozenset({
    "reset", "greeting", "thanks", "bot_identity", "bot_question",
    "confirm_yes", "confirm_deny", "confirm_change_service",
    "confirm_change_location", "correction", "negative_preference",
    "escalation",
})


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
        # Route through the unified extractor — Phase 4 replacement for
        # the legacy `classify_unified` call. `slot_extraction.extract()`
        # accepts the regex-side result and returns the merged 15-field
        # dict (13 slots + tone, action). The gate condition guarantees
        # `early_extracted.service_type is None`, so when the result has
        # a service_type, the LLM contributed it.
        from app.services import slot_extraction
        unified = slot_extraction.extract(
            message,
            regex_result=early_extracted,
            api_key_available=True,
        )
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
                # Note: extract() returns merged shape with `_gender` (the
                # internal key); legacy `classify_unified` returned `gender`
                # (API key). Read from the merged-shape key here.
                if unified.get("_gender"):
                    early_extracted["_gender"] = unified["_gender"]
                # Note on _populations: the legacy `classify_unified` gate
                # ignored populations from the LLM (the prompt declared the
                # field but the gate didn't read it). Phase 4 Stage 1 keeps
                # that behavior bit-for-bit — populations enrichment in the
                # gate is deferred to a future intentional change with its
                # own eval validation.
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
        from app.llm.claude_client import classify_message_llm
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
