"""Meta-conversation handlers: greeting, thanks, help, bot-identity, reset.

These all return canned or near-canned responses and generally touch the
session only to clear state (reset) or to mark ``_last_action`` so a
follow-up yes/no can be interpreted in context (confused).

All handlers in this module take a single ``MessageContext`` parameter
(see ``chatbot/context.py``). Field reads use ``ctx.X`` rather than
positional arguments — this is the migration referenced in
``ORCHESTRATOR_AUDIT.md`` Phase A.
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

from ..context import MessageContext, _USE_LLM, _empty_reply
from ..logging import _log_turn


logger = logging.getLogger(__name__)


# Words that signal the help-intent message is actually a shame/vulnerability
# disclosure rather than a "what can you do?" question.
_SHAME_HELP_SIGNALS = (
    "embarrassed", "ashamed", "pathetic", "humiliating",
    "hard to ask", "hard for me", "hate asking", "hate to ask",
    "difficult to ask", "burden", "swallow my pride",
)


def _handle_help(ctx: MessageContext):
    """Show the service-menu help response.

    Two variants: if the user is expressing shame around asking ("I'm
    embarrassed to ask for help"), this routes to the emotional handler
    instead. Confused or emotional callers get a lead-in that acknowledges
    the overwhelm before the menu.

    ``ctx.tone_prefix`` is the sensitive-context / tonal prefix computed by
    the orchestrator (e.g., "I understand this is a difficult situation.
    Let me help. " for foster-care / fleeing / just-got-out-of-jail
    messages). When non-empty, it PRE-empts the generic confused/emotional
    lead-in below — the sensitive prefix is more specific, and stacking
    both reads as doubled empathy ("I understand... I hear you...").
    """
    # Shame + help: vulnerability disclosure masquerading as a help request.
    # Route to emotional handler with the shame-specific response.
    if ctx.tone == "emotional":
        help_lower = ctx.message.lower()
        if any(s in help_lower for s in _SHAME_HELP_SIGNALS):
            response = _pick_emotional_response(ctx.message)
            ctx.existing["_last_action"] = "emotional"
            ctx.existing["_emotional_context"] = "shame"
            save_session_slots(ctx.session_id, ctx.existing)
            result = _empty_reply(
                ctx.session_id, response, ctx.existing,
                quick_replies=[
                    {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                ],
            )
            _log_turn(ctx.session_id, ctx.redacted_message, result, "emotional",
                      request_id=ctx.request_id, tone=ctx.tone)
            return result

    # When sensitive context is present, the orchestrator's tone_prefix
    # already provides an empathic acknowledgment — use standard menu
    # body to avoid doubling up. Otherwise, confused / emotional callers
    # still get the overwhelm lead-in.
    if ctx.tone_prefix:
        help_msg = ctx.tone_prefix + _HELP_RESPONSE
    elif ctx.tone in ("confused", "emotional"):
        help_msg = (
            "I hear you — it can feel overwhelming when you don't know "
            "where to start. Let's take it one step at a time. "
            "Here's what I can help you find:"
        )
    else:
        help_msg = _HELP_RESPONSE
    result = _empty_reply(
        ctx.session_id, help_msg, ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_bot_identity(ctx: MessageContext):
    """Answer "are you a bot?" / "who are you?" with the standard identity line."""
    result = _empty_reply(
        ctx.session_id, _BOT_IDENTITY_RESPONSE, ctx.existing,
        quick_replies=[
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_bot_capability_question(ctx: MessageContext):
    """Answer "what can you do?" / "can you find X?" — tries bot_knowledge first,
    then LLM, then a static fallback."""
    from app.services.bot_knowledge import answer_question
    static_answer = answer_question(ctx.message)
    if static_answer:
        response = static_answer
    elif _USE_LLM:
        try:
            prompt = _build_bot_question_prompt(ctx.message, slots=ctx.existing)
            response = claude_reply(prompt)
        except Exception as e:
            logger.error(f"Bot question LLM response failed: {e}")
            response = _static_bot_answer(ctx.message)
    else:
        response = _static_bot_answer(ctx.message)
    result = _empty_reply(ctx.session_id, response, ctx.existing)
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_confused(ctx: MessageContext):
    """Acknowledge overwhelm with the standard confused response and mark
    _last_action so a follow-up 'yes' / 'no' is interpreted in this context.

    ``ctx.tone_prefix`` is prepended when set (e.g., sensitive-context empathy
    for foster-care / fleeing scenarios). The standard confused response
    ("That's okay — you don't have to know exactly...") flows naturally
    after any tone prefix without doubling empathy.
    """
    ctx.existing["_last_action"] = "confused"
    save_session_slots(ctx.session_id, ctx.existing)
    result = _empty_reply(
        ctx.session_id, ctx.tone_prefix + _CONFUSED_RESPONSE, ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES) + [
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ],
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_reset(ctx: MessageContext):
    """Clear all session state and return the reset response."""
    clear_session(ctx.session_id)
    log_session_reset(ctx.session_id)
    result = _empty_reply(
        ctx.session_id, _RESET_RESPONSE, {},
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_greeting(ctx: MessageContext):
    """Welcome message. If we have prior session state, offer to resume or reset."""
    if ctx.existing and any(v is not None for v in ctx.existing.values()):
        response = (
            "Hey again! I still have your earlier search info. "
            "Want to keep going, or would you like to start over?"
        )
        result = _empty_reply(ctx.session_id, response, ctx.existing)
    else:
        result = _empty_reply(
            ctx.session_id, _GREETING_RESPONSE, ctx.existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result


def _handle_thanks(ctx: MessageContext):
    """Acknowledge thanks and offer the welcome actions for whatever comes next."""
    result = _empty_reply(
        ctx.session_id, _THANKS_RESPONSE, ctx.existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, ctx.category,
              request_id=ctx.request_id, tone=ctx.tone)
    return result
