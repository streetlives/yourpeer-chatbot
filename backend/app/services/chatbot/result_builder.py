"""Result-dict builder for follow-up turns.

The orchestrator's three follow-up code paths (confirmation, service
slot-filling, continuation) all build an identically-shaped result
dict and call ``_log_turn``. This helper consolidates that
boilerplate so the shape lives in one place.

A note on ``response_text``:
    The orchestrator deliberately does NOT prepend the tone prefix on
    one of the three call sites (the continuation path at
    ``orchestrator.py``'s ``has_new_slots and existing.service_type``
    branch). That asymmetry is preserved here — callers compose the
    response_text themselves and pass it in, so whether the prefix
    appears is visible at the call site rather than hidden in the
    helper. See ``ORCHESTRATOR_AUDIT.md`` "Suspect 1" for the open
    question on whether that asymmetry is intentional.
"""

from .logging import _log_turn


def _build_follow_up_response(
    *,
    session_id: str,
    redacted_message: str,
    response_text: str,
    merged: dict,
    quick_replies: list,
    log_category: str,
    request_id: str | None,
    tone: str | None,
) -> dict:
    """Build a follow-up-needed result dict, log the turn, and return it.

    Keyword-only by design: with seven parameters of similar types
    (several strings, two dicts/lists), positional calls would be
    error-prone and would also produce a forest of argument-order
    mutants on every caller.

    The returned dict has the exact shape that the API layer expects:

    * ``session_id`` — echoed back so the client can keep the same
      thread alive
    * ``response`` — the bot's user-facing string (already composed by
      the caller, including any tone prefix)
    * ``follow_up_needed`` — always True for this helper; the
      orchestrator's "results returned" path uses a different builder
    * ``slots`` — the merged session dict (so the client can inspect
      what the bot understood)
    * ``services`` — empty by definition: a follow-up turn never
      contains service cards
    * ``result_count`` — zero by the same reasoning
    * ``relaxed_search`` — False by the same reasoning
    * ``quick_replies`` — caller-supplied (confirmation flow uses
      different buttons than the slot-filling flow)

    After building, ``_log_turn`` is invoked with the same shape as
    the original three call sites: positional ``(session_id,
    redacted_message, result, log_category)`` followed by keyword
    ``request_id`` and ``tone``.
    """
    result = {
        "session_id": session_id,
        "response": response_text,
        "follow_up_needed": True,
        "slots": merged,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": quick_replies,
    }
    _log_turn(
        session_id,
        redacted_message,
        result,
        log_category,
        request_id=request_id,
        tone=tone,
    )
    return result
