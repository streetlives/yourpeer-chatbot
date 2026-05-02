"""Session-state mutations extracted from the orchestrator.

The orchestrator was previously interleaving routing logic with
session-state housekeeping: transcript appends, queue updates,
flag clearing, emotional-context persistence. Each helper here
takes the relevant dicts (and a ``session_id`` where persistence
is needed), applies the documented mutation, and saves via
``save_session_slots`` when the original code did. The orchestrator
calls these as one-liners so the routing flow reads cleanly.

Helpers come in two shapes:

* **Pure mutators** (``_append_to_transcript``, ``_update_queued_services``)
  modify the passed-in dict and do NOT call ``save_session_slots``.
  The caller already saves at a known point downstream — preserving
  the original code's save semantics.

* **Persisting clearers/setters** take ``session_id`` and call
  ``save_session_slots`` themselves. These wrap the original "pop and
  save" or "set and save" patterns one-for-one.

A note on test coverage: each helper has an isolated unit test in
``tests/unit/test_session_helpers.py``. The orchestrator-level
integration tests in ``tests/integration/test_orchestrator_guards.py``
and the broader integration suite continue to exercise these
helpers end-to-end via ``generate_reply``.
"""

from app.services.session_store import save_session_slots


# --- Module-level constants ---

# Categories that read and consume ``_last_action`` on the next turn.
# When the user's next category is NOT one of these, any pending
# ``_last_action`` was set in a prior emotional/escalation/crisis
# context that no longer applies and should be cleared.
# (Originally inline in orchestrator.py.)
_CONSUMES_LAST_ACTION = frozenset({"confirm_yes", "confirm_deny"})


# Maximum number of user-message turns kept in the session transcript.
# Older entries are dropped first. The transcript is included in the
# context window of LLM calls (``slot_extraction.extract`` reads it),
# so the upper bound also caps token cost.
# (Originally inline in orchestrator.py.)
_MAX_TRANSCRIPT = 20


# Slot keys that represent user-provided content. Distinguished from
# Trust Model 5 control flags (``no_requirements``, ``_contradiction``,
# ``_is_additive``) which are always-populated metadata that the unified
# extractor returns on every turn — including turns where the user
# shared nothing searchable.
#
# Background — why this list exists at all:
# After a turn 1 of pure casual chat ("how's it going?"), the session
# contains ``{"no_requirements": False, "_populations": [],
# "_contradiction": False, "_is_additive": False, "transcript": [...]}``.
# Every value is non-None even though the user shared nothing. A naive
# ``any(v is not None for v in existing.values())`` check therefore
# misreads casual-chat sessions as having an active search.
#
# Whitelisting the user-content slots fixes this: empty ``_populations``
# evaluates falsy via ``bool([])``, the False default of
# ``no_requirements`` doesn't trigger, and the resume/has-state branch
# only fires when the user actually shared something searchable.
#
# Originally a private constant in ``handlers/meta.py`` for
# ``_handle_greeting``. Promoted here so Phase C handlers
# (``_handle_pending_confirmation``, ``_handle_post_results_interaction``,
# etc.) can use the same predicate without re-importing across handler
# modules or re-defining the list.
_USER_PROVIDED_SLOTS = (
    "service_type", "service_detail", "additional_services",
    "location", "urgency", "age", "family_status",
    "_gender", "_populations", "org_name",
)


def has_user_content(session: dict | None) -> bool:
    """Return True if the session contains any user-provided slot value.

    Distinguishes "the user has shared something searchable" from "the
    extractor has run and populated control flags" — see the
    ``_USER_PROVIDED_SLOTS`` docstring for the bug this predicate fixes.

    Examples:
        ``{}``                                    → False
        ``None``                                  → False
        ``{"no_requirements": False}``            → False (control flag)
        ``{"_populations": []}``                  → False (empty list)
        ``{"service_type": "shelter"}``           → True
        ``{"location": "Brooklyn"}``              → True
        ``{"_gender": "female"}``                 → True

    The check uses ``.get(k)`` (not ``k in session``) so a key with a
    falsy value — empty string, empty list, None — counts as "no
    content" rather than "content present but falsy."
    """
    if not session:
        return False
    return any(session.get(k) for k in _USER_PROVIDED_SLOTS)


# --- Pure mutators (no save) ---

def _append_to_transcript(merged: dict, redacted_message: str) -> None:
    """Append a user-message turn to the session transcript and truncate.

    Mutates ``merged["transcript"]`` in place. Initializes the list
    if absent. Truncates from the front when the list exceeds
    ``_MAX_TRANSCRIPT`` entries.

    Does NOT call ``save_session_slots`` — the orchestrator's
    surrounding code saves ``merged`` on its own at a known point
    downstream. Splitting the save out of this helper preserves the
    original ordering (mutate, then maybe more mutations, then one
    save) rather than introducing a redundant save.
    """
    if "transcript" not in merged:
        merged["transcript"] = []
    merged["transcript"].append({"role": "user", "text": redacted_message})
    if len(merged["transcript"]) > _MAX_TRANSCRIPT:
        merged["transcript"] = merged["transcript"][-_MAX_TRANSCRIPT:]


def _update_queued_services(
    merged: dict, extracted: dict, existing: dict
) -> None:
    """Reconcile the ``_queued_services`` slot against the latest extraction.

    Two effects, applied in order:

    1. **Seed** ``merged["_queued_services"]`` from
       ``extracted["additional_services"]`` if the queue is currently
       empty and the extraction surfaced additional services.

    2. **Clear** ``merged["_queued_services"]`` when the user has
       changed primary service from a prior turn AND the new message
       does not itself add services AND is not flagged as an additive
       turn. This handles the "I want shelter instead" case where the
       previously queued add-ons are no longer relevant.

    Mutates ``merged`` in place. Does NOT save — same rationale as
    ``_append_to_transcript``.
    """
    additional = extracted.get("additional_services", [])
    is_additive = extracted.get("_is_additive", False)
    if additional and "_queued_services" not in merged:
        merged["_queued_services"] = additional
    if (
        extracted.get("service_type")
        and existing.get("service_type")
        and extracted["service_type"] != existing.get("service_type")
        and not additional
        and not is_additive
    ):
        merged.pop("_queued_services", None)


# --- Persisting clearers/setters ---

def _clear_awaiting_service_after_clear(
    session_id: str, existing: dict
) -> None:
    """Pop the ``_awaiting_service_after_clear`` flag and save.

    Called from the regex-bypass branch in ``orchestrator.generate_reply``
    after the bypass fires. Without the pop, the flag would persist
    into the next turn and incorrectly trigger the bypass again.
    """
    existing.pop("_awaiting_service_after_clear", None)
    save_session_slots(session_id, existing)


def _clear_stale_last_action(
    session_id: str, existing: dict, category: str
) -> None:
    """Clear ``_last_action`` when the category indicates context shift.

    ``_last_action`` is set by emotional/escalation/crisis/confused/
    frustration handlers and consumed by ``_handle_context_aware_confirm``
    for the NEXT confirm_yes / confirm_deny. If the user sends anything
    else (greeting, help, thanks, a new service request), the context
    has shifted and the flag should not persist — otherwise it would
    incorrectly affect a confirm_yes / confirm_deny many turns later.

    No-op when ``_last_action`` was not set, or when ``category`` is
    one of the consuming actions.
    """
    if (
        existing.get("_last_action")
        and category not in _CONSUMES_LAST_ACTION
    ):
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)


def _consume_last_action(
    session_id: str, existing: dict, last_action: object
) -> None:
    """Clear ``_last_action`` after ``_handle_context_aware_confirm`` checked it.

    Takes the captured ``last_action`` value (read before the handler
    ran) as a parameter rather than reading ``existing["_last_action"]``
    again, in case the handler modified the dict.
    """
    if last_action:
        existing.pop("_last_action", None)
        save_session_slots(session_id, existing)


def _persist_emotional_context_early(
    session_id: str, existing: dict, update: object
) -> None:
    """Save the emotional-context update detected during early tone-prefix.

    The early ``_compute_tone_prefix`` call (before routing to
    help/confused/emotional handlers) may set ``_emotional_context``
    based on first-turn sensitive language. This helper mirrors the
    original orchestrator code: when ``update`` is non-None, write
    it to ``existing`` and persist immediately.
    """
    if update is not None:
        existing["_emotional_context"] = update
        save_session_slots(session_id, existing)


def _persist_emotional_context_late(
    session_id: str, merged: dict, existing: dict, update: object
) -> None:
    """Save the emotional-context update detected during late tone-prefix.

    The late ``_compute_tone_prefix`` call (after potential B.2
    promotion of negative_preference → service) may produce a
    different result than the early one. This helper preserves the
    original orchestrator's two-stage logic exactly:

    1. If ``update`` is non-None, write it to ``merged`` (in-memory).
    2. Save IFF the merged dict now has emotional context AND
       ``existing`` (pre-merge) did not.

    NOTE (ORCHESTRATOR_AUDIT.md Bug 1): Step 2's condition only fires
    on a None → non-None transition. If the early site already set
    ``_emotional_context = "shame"`` and the late computation wants
    ``"frustrated"``, the in-memory dict updates but the save
    condition is False (both are truthy), so the change is lost on
    follow-up paths that don't save again before returning. This
    bug is preserved here bit-for-bit and is queued for fix in PR-γ
    (TEST_QUALITY_PLAN.md). When that fix lands, change the
    condition to ``!=`` and add a regression test in
    ``test_session_helpers.py``.
    """
    if update is not None:
        merged["_emotional_context"] = update
    if (
        merged.get("_emotional_context")
        and not existing.get("_emotional_context")
    ):
        save_session_slots(session_id, merged)
