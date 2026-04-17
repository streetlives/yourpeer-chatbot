"""Meta-conversation handlers: greeting, thanks, help, bot-identity, reset.

These all return canned or near-canned responses and generally touch the
session only to clear state (reset) or to mark ``_last_action`` so a
follow-up yes/no can be interpreted in context (confused).
"""

import logging

from app.llm.claude_client import claude_reply
from app.services.phrase_lists import _WELCOME_QUICK_REPLIES
from app.services.responses import (
    _BOT_IDENTITY_RESPONSE,
    _CONFUSED_RESPONSE,
    _GREETING_RESPONSE,
    _HELP_RESPONSE,
    _RESET_RESPONSE,
    _THANKS_RESPONSE,
    _build_bot_question_prompt,
    _pick_emotional_response,
    _static_bot_answer,
)
from app.services.session_store import clear_session, save_session_slots
from app.services.audit_log import log_session_reset

from ..context import _USE_LLM, _empty_reply
from ..logging import _log_turn


logger = logging.getLogger(__name__)


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
