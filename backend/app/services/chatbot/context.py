"""Shared primitives used across every chatbot submodule.

Kept deliberately small and dependency-free so every other module in the
``chatbot`` package can import from here without creating cycles.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional


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
    crisis_result: Optional[dict]  # from detect_crisis(), None if no crisis
    # --- Post-results state ---
    last_results: Optional[list]   # cached query results from session, or None
    is_confirmation_action: bool   # True if action is confirm_yes/deny/change/reset/greeting
    # --- Geolocation ---
    has_coords: bool          # True if lat/lon were provided by browser
    latitude: Optional[float]
    longitude: Optional[float]
    # --- Language ---
    spanish_detected: bool    # True if Spanish phrases found in message
    spanish_acknowledgment: str  # bilingual prefix if Spanish + service intent


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
