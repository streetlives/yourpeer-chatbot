"""Audit-log wrapper for conversation turns.

Kept in its own module so every handler can import it without pulling in
the rest of the chatbot package. Any exception here is caught and logged
— an audit failure must never break the user-facing response.
"""

import logging

from app.privacy.pii_redactor import redact_pii
from app.services.audit_log import log_conversation_turn


logger = logging.getLogger(__name__)


def _log_turn(
    session_id: str,
    user_msg: str,
    result: dict,
    category: str,
    request_id: str | None = None,
    tone=None,
    confidence: str = "high",
    confidence_reason: str | None = None,
    source: str | None = None,
) -> None:
    """Log a conversation turn to the audit log.

    ``confidence`` is the ordinal signal-strength indicator
    (``"high" | "semantic" | "medium" | "low"``); ``confidence_reason``
    is the categorical discriminator (``"regex_match" |
    "semantic_match" | "llm_match" | "llm_reaching_other" | "no_signal"
    | "non_service_route"``). See ``_compute_routing_category`` in
    ``pipeline.py``. Both flow into the audit event so downstream
    analytics can graph them independently.

    ``source`` is the user-message origin (``"typed" | "quick_reply"``
    or None for legacy clients). Persisted on every turn so admin
    analytics can compute tap-vs-type ratios and study navigation
    patterns. ``log_conversation_turn`` accepts any non-None kwarg, so
    None values simply don't appear on the event — keeps the audit
    feed clean for old data while the field rolls out.
    """
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
            confidence_reason=confidence_reason,
            source=source,
        )
    except Exception as e:
        logger.error(f"Failed to log conversation turn: {e}")
