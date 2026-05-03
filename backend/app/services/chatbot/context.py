"""Shared primitives used across every chatbot submodule.

Kept deliberately small and dependency-free so every other module in the
``chatbot`` package can import from here without creating cycles.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DISPLAY CONFIGURATION
# ---------------------------------------------------------------------------
# How many service cards to show per page. The DB query fetches more
# (up to _FETCH_LIMIT in _execute_and_respond) to support client-side
# filtering, but we paginate the display to avoid overwhelming users —
# especially in crisis situations where cognitive load is high.
_DISPLAY_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# LLM FEATURE GATE
# ---------------------------------------------------------------------------
# Use LLM-based features when ANTHROPIC_API_KEY is available.
# Falls back to regex-only if the key is not set. Every module that needs
# to branch on LLM availability imports this single flag.
_USE_LLM = bool(os.getenv("ANTHROPIC_API_KEY"))

if _USE_LLM:
    logger.info("LLM features enabled (ANTHROPIC_API_KEY found)")
else:
    logger.info("LLM features disabled — using regex only")


# ---------------------------------------------------------------------------
# MESSAGE CONTEXT — shared state between classification and handlers
# ---------------------------------------------------------------------------
# Built by the pipeline module from the classification stage. Passed to
# handler functions so they don't need 15+ positional arguments.
# Mutable: handlers may modify ``existing`` (session slots) and should
# call save_session_slots() when they do.

@dataclass
class MessageContext:
    """All state produced by the classification pipeline for a single message.

    Built once at the start of ``generate_reply``, consumed by handler
    functions. Replaces the 15+ local variables that were previously shared
    via closure inside the monolithic generate_reply().

    Most fields are populated when the dataclass is instantiated. A few
    are set after computation later in ``generate_reply``:

    * ``tone_prefix`` — built by ``_compute_tone_prefix`` and set onto
      the existing ctx instance via direct attribute assignment. Some
      handlers fire before the prefix is computed (e.g. crisis), in
      which case the field stays at its default ``""`` and they don't
      use it. Handlers that DO use it run after the assignment.
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
    # Tuple of (crisis_category, response_text) when detected, else None.
    # Shape comes from ``detect_crisis()``; the dispatcher checks against
    # ``None`` to gate crisis routing.
    crisis_result: Optional[Tuple[str, str]]
    # --- Post-results state ---
    last_results: Optional[list]   # cached query results from session, or None
    is_confirmation_action: bool   # True if action is confirm_yes/deny/change/reset/greeting
    # --- Geolocation (browser-provided coords for this request) ---
    # Stashed on ctx for handlers that need them in concert with
    # ``existing["_latitude"]`` / ``existing["_longitude"]``. Pre-merge
    # coords only — handlers that need post-merge readiness should read
    # from ``ctx.merged`` (or ``existing`` after ``_apply_session_geo``).
    latitude: Optional[float]
    longitude: Optional[float]
    # --- Language (set late, after _handle_spanish_detection) ---
    # Bilingual prefix prepended to the service-flow response. Empty
    # string when no Spanish was detected. Read in the orchestrator's
    # service-flow prefix-injection block.
    spanish_acknowledgment: str = ""
    # --- Tone prefix (set late — see class docstring) ---
    tone_prefix: str = ""     # sensitive-context / warmth prefix from _compute_tone_prefix
    # --- Merged slot state (set late, after merge_slots in service flow) ---
    # Stays Optional because there's a real "not yet computed" state: the
    # orchestrator only sets ``ctx.merged`` after ``merge_slots`` runs in
    # the service-flow branch (orchestrator.py:519). Handlers that route
    # to the service flow's tail (currently only ``_handle_general_
    # conversation``) read this — they're guaranteed to see a populated
    # value by then. Handlers that fire EARLIER (crisis, emotional,
    # confirmation, etc.) must NOT read ``merged`` directly — use
    # ``require_merged()`` if you must, or read from ``existing`` /
    # ``early_extracted`` instead.
    #
    # Tightening this to a non-Optional type would force a synthetic
    # initial value, masking the "not yet set" condition. The
    # ``require_merged()`` accessor below makes the read-time contract
    # explicit instead.
    merged: Optional[dict] = None  # post-merge dict; same identity as orchestrator's `merged`
    # --- Cached LLM extraction (set when ``_run_llm_gate`` fires) ---
    # Full 15-field merged dict from ``slot_extraction.extract()``, captured
    # by the gate so the orchestrator's service branch and
    # ``_handle_post_pending_confirmation`` can reuse it instead of re-
    # invoking the LLM on the same message. ``None`` when the gate
    # condition didn't fire (short message, prior service intent, etc.) or
    # when the call raised — consumers must handle that case by either
    # falling through to a fresh ``slot_extraction.extract()`` call or
    # using ``early_extracted`` directly.
    unified_extraction: Optional[dict] = None
    # --- Snapshot fields (set by orchestrator immediately before handler dispatch) ---
    # These exist because three confirmation handlers mutate session state
    # (popping ``_last_action`` / ``_pending_confirmation`` from
    # ``existing``) as part of their dispatch logic. The orchestrator
    # captures the pre-mutation value into these snapshot fields so the
    # handler — and any orchestrator code that reads the same value
    # *after* the handler returns (like ``_consume_last_action``) — can
    # see the value the handler dispatched on.
    #
    # Tracks audit item D-5 from PHASE_AC_AFTERMATH.md. The previous
    # design passed these as positional args alongside ctx; promoting
    # them to ctx fields makes every handler signature uniform
    # (``_handle_X(ctx)``) and documents the snapshot semantics here.
    #
    # Set to ``None`` at construction time. The orchestrator MUST populate
    # them before calling the corresponding handler. Tests can either
    # set these directly via ``make_ctx(snapshot_last_action=...)`` or
    # via attribute assignment after ``make_ctx()``.
    snapshot_last_action: Optional[str] = None
    snapshot_pending: Optional[bool] = None
    snapshot_response_tone: Optional[str] = None

    def require_merged(self) -> dict:
        """Read ``self.merged`` with a fail-fast guard.

        Use this when a handler genuinely needs the post-merge slot state
        and would crash on ``merged.get(...)`` if ``merged`` were ``None``.
        Raises a descriptive RuntimeError naming the contract instead of
        a bare AttributeError on the missing ``.get`` method.

        For handlers that have a sensible fallback (read from
        ``ctx.existing`` instead) — don't use this; just check ``ctx.merged``
        directly.
        """
        if self.merged is None:
            raise RuntimeError(
                "ctx.merged accessed before it was populated. This handler "
                "is running before the orchestrator's merge_slots call (see "
                "orchestrator.py around line 519). Handler should either "
                "read from ctx.existing / ctx.early_extracted instead, or "
                "be moved to the post-merge dispatch path."
            )
        return self.merged


# ---------------------------------------------------------------------------
# REPLY / COUNTING HELPERS
# ---------------------------------------------------------------------------

def _count_unique_locations(services: list[dict]) -> int:
    """Count unique locations by org+address — mirrors the frontend's
    ``groupByLocation()`` which renders co-located services as one card.
    Without this, text says "5 results" while the carousel shows 4 cards
    because two services share the same location."""
    seen: set[str] = set()
    for svc in services:
        key = (
            f"{(svc.get('organization') or '').lower().strip()}"
            f"||{(svc.get('address') or '').lower().strip()}"
        )
        seen.add(key)
    return len(seen)


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
