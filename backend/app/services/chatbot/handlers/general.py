"""General-conversation handler — the catch-all for messages that didn't
fit any more specific category.

Three distinct paths inside:

1. **Unrecognized service requests** — the user asked for something
   specific we can't help with ("I need a job"). Tiered escalation: tier 1
   says what we DO cover, tier 2 adds peer navigator, tier 3 goes straight
   to navigator.
2. **Casual chat** — "how are you?" / "what's up?" → rotating friendly
   reply.
3. **Anything else** — fallback response from the LLM, with a low-
   confidence peer-navigator offer when appropriate.
"""

import re

from app.services.phrase_lists import _WELCOME_QUICK_REPLIES
from app.services.responses import _fallback_response
from app.services.session_store import save_session_slots
from app.services.slot_extractor import NEAR_ME_SENTINEL

from ..context import MessageContext, _empty_reply
from ..logging import _log_turn


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


def _handle_general_conversation(ctx: MessageContext):
    """Handle general chat / unrecognized service requests with tiered
    escalation.

    Three cases:

    1. User asks for something specific we can't help with ("I need a job")
       → tiered redirect: tier 1 says what we DO cover, tier 2 adds peer
       navigator, tier 3 goes straight to navigator.
    2. User sends casual chat ("how are you?") → rotating friendly reply.
    3. Anything else → fallback response, optionally with a low-confidence
       peer-navigator offer.

    Reads ``ctx.merged`` (post-merge_slots dict) — this handler runs at
    the end of ``generate_reply`` so merged is always populated by then.
    """
    merged = ctx.merged
    is_casual_chat = bool(_CASUAL_CHAT_RE.search(ctx.message))
    is_service_request_pattern = bool(_SERVICE_NEED_RE.search(ctx.message))
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
        save_session_slots(ctx.session_id, merged)

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
        result = _empty_reply(ctx.session_id, response, merged, quick_replies=qr)
        _log_turn(ctx.session_id, ctx.redacted_message, result, "unrecognized_service",
                  request_id=ctx.request_id, tone=ctx.tone, confidence="low")
        return result

    if is_casual_chat:
        idx = len(merged.get("transcript", [])) % len(_CASUAL_RESPONSES)
        response = _CASUAL_RESPONSES[idx]
    else:
        response = _fallback_response(ctx.message, merged)
        # Cultural humility: when the bot can't understand what the user
        # needs (low confidence), acknowledge the limitation rather than
        # pretending the generic response is adequate.
        if ctx.confidence == "low" and not merged.get("service_type"):
            response += (
                "\n\nIf I'm missing something important about what you need, "
                "a peer navigator can help — they're real people who know "
                "the system well."
            )

    has_service_intent = bool(merged.get("service_type") or merged.get("location"))
    general_qr = []
    if not has_service_intent and len(merged.get("transcript", [])) <= 1 and not is_casual_chat:
        general_qr = list(_WELCOME_QUICK_REPLIES)
    if ctx.confidence in ("medium", "low"):
        general_qr.append({"label": "❌ Not what I meant", "value": "not what I meant"})
    result = _empty_reply(
        ctx.session_id, response, merged,
        quick_replies=general_qr,
    )
    _log_turn(ctx.session_id, ctx.redacted_message, result, "general",
              request_id=ctx.request_id, tone=ctx.tone, confidence=ctx.confidence)
    return result
