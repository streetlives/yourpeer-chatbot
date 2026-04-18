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
) -> None:
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
