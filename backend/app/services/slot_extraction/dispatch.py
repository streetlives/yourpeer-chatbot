"""Dispatch logic and LLM call wrappers for the unified slot extractor.

Four public-within-package functions:

    _is_narrative(message) → bool
    _is_simple_message(message, regex_result) → bool
    extract_slots_short(message, conversation_history) → dict
    extract_slots_narrative(message, conversation_history) → dict

Plus `_narrative_regex_fallback(regex_result, message)` used when the
narrative LLM call fails.

The legacy `llm_slot_extractor` module had these functions at top level;
moving them here consolidates the "one LLM call per path" pattern into
a single module that only `__init__.py` depends on.

Key differences from the legacy versions:

    1. The LLM returns `additional_services` as a list of objects
       `{type, detail?, location?}` instead of `additional_service_types`
       as a list of strings. We normalize to 3-tuples `(type, detail,
       location)` so the regex and LLM sides speak the same shape.

    2. `service_detail` is now captured from the LLM output and placed
       in the returned dict. The merge layer applies the canonical-form
       validator later.

    3. All returned dicts include `org_name` and are 10-field in shape
       (legacy narrative path dropped `org_name` and used a 7-field
       return — see Behavior #13 in the migration doc).
"""

import logging
import time
from typing import Optional

from app.llm.claude_client import (
    get_client,
    SLOT_EXTRACTION_MODEL,
    _track_llm_call,
)

from .prompts import (
    _EXTRACT_SLOTS_TOOL,
    _NARRATIVE_SYSTEM_PROMPT,
    _NARRATIVE_THRESHOLD,
    _SHORT_SYSTEM_PROMPT,
    _URGENCY_HIERARCHY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# COMPLEXITY CHECK — is this message simple enough to skip the LLM?
# ---------------------------------------------------------------------------

def _is_narrative(message: str) -> bool:
    """Detect if a message is a narrative (long enough to need
    urgency-aware extraction rather than keyword matching)."""
    return len(message.split()) >= _NARRATIVE_THRESHOLD


def _is_simple_message(message: str, regex_result: dict) -> bool:
    """Determine if a message is simple enough to trust regex extraction.

    Simple = short, clear keyword match, known location, no ambiguity.
    Complex = long sentences, implicit needs, conflicting signals, slang.

    When in doubt, return False (use LLM) — accuracy > speed.

    Ported verbatim from `llm_slot_extractor._is_simple_message` — the
    four criteria (≤8 words, has service + location, known NYC location,
    single service-type category matched) are load-bearing behavior
    documented as Behavior #5 in the migration doc.
    """
    words = message.split()

    # Long messages are always complex
    if len(words) > 8:
        return False

    has_service = regex_result.get("service_type") is not None
    has_location = regex_result.get("location") is not None

    # If regex didn't get both, it's not simple
    if not (has_service and has_location):
        return False

    # Check if the extracted location is a known NYC location
    # (not a greedy-captured sentence fragment).
    from app.services.slot_extractor import _KNOWN_LOCATIONS, NEAR_ME_SENTINEL
    location = regex_result.get("location", "")
    if location == NEAR_ME_SENTINEL:
        return True  # "near me" is simple
    location_is_known = any(
        loc == location.lower().strip()
        for loc in _KNOWN_LOCATIONS
    )
    if not location_is_known:
        return False  # Unknown location may be garbled — let LLM handle

    # Check for multiple service-type keywords (conflicting signals)
    from app.services.slot_extractor import SERVICE_KEYWORDS
    lower = message.lower()
    matched_categories = set()
    for cat, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            if len(kw) > 3 and kw in lower:  # only check non-short keywords
                matched_categories.add(cat)
    if len(matched_categories) > 1:
        return False  # Conflicting service signals — let LLM disambiguate

    return True


# ---------------------------------------------------------------------------
# SHORT-PATH LLM CALL
# ---------------------------------------------------------------------------

def extract_slots_short(
    message: str,
    conversation_history: Optional[list] = None,
) -> dict:
    """Call Claude's short-path slot extractor.

    Used for messages under the narrative threshold (< 20 words). Returns
    the 10-field dict shape (the schema fields; Trust Model 5's three
    regex-only fields aren't asked of the LLM). The merge layer
    combines it with the 13-field regex result.

    On any LLM failure (API error, no tool_use block, token budget) the
    function returns the empty-slots dict. The caller is responsible for
    falling back to regex_result wholesale — see Behavior #7 in the
    migration doc.
    """
    try:
        _track_llm_call("slot_extraction")
        client = get_client()

        messages = _build_messages_with_history(message, conversation_history)

        t0 = time.perf_counter()
        response = client.messages.create(
            model=SLOT_EXTRACTION_MODEL,
            max_tokens=256,
            system=_SHORT_SYSTEM_PROMPT,
            tools=[_EXTRACT_SLOTS_TOOL],
            tool_choice={"type": "tool", "name": "extract_intake_slots"},
            messages=messages,
        )
        latency = round((time.perf_counter() - t0) * 1000)

        _record_success("slot_extraction", response, latency)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_intake_slots":
                return _normalize_tool_output(block.input)

        logger.warning("Short-path LLM: no tool_use block in response")
        _record_failure("slot_extraction", reason="no_tool_use")
        return _empty_slots()

    except Exception as e:
        logger.error(f"Short-path LLM extraction failed: {e}")
        _record_failure("slot_extraction", reason=str(e))
        return _empty_slots()


# ---------------------------------------------------------------------------
# NARRATIVE-PATH LLM CALL
# ---------------------------------------------------------------------------

def extract_slots_narrative(
    message: str,
    conversation_history: Optional[list] = None,
) -> dict:
    """Call Claude's narrative-path slot extractor.

    For narratives, the LLM is FULLY AUTHORITATIVE — the merge layer
    does not let regex override the LLM's service_type. This prevents
    "I just got out of the hospital and my housing fell through" from
    extracting "medical" (regex keyword on "hospital") when the user's
    primary need is shelter.

    On LLM failure the function returns the empty-slots dict. The
    caller is responsible for falling back to
    `_narrative_regex_fallback(regex_result, message)` — keeping the
    fallback decision at the dispatch layer rather than inside the call
    means the fallback also runs on API-key-absent paths, matching the
    legacy behavior.
    """
    try:
        _track_llm_call("narrative_extraction")
        client = get_client()

        messages = _build_messages_with_history(message, conversation_history)

        t0 = time.perf_counter()
        response = client.messages.create(
            model=SLOT_EXTRACTION_MODEL,
            max_tokens=256,
            system=_NARRATIVE_SYSTEM_PROMPT,
            tools=[_EXTRACT_SLOTS_TOOL],
            tool_choice={"type": "tool", "name": "extract_intake_slots"},
            messages=messages,
        )
        latency = round((time.perf_counter() - t0) * 1000)

        _record_success("narrative_extraction", response, latency)

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_intake_slots":
                result = _normalize_tool_output(block.input)
                logger.info(
                    f"Narrative extraction: primary={result['service_type']}, "
                    f"additional={result['additional_services']}, "
                    f"urgency={result['urgency']}"
                )
                return result

        logger.warning("Narrative-path LLM: no tool_use block in response")
        _record_failure("narrative_extraction", reason="no_tool_use")
        return _empty_slots()

    except Exception as e:
        logger.error(f"Narrative LLM extraction failed: {e}")
        _record_failure("narrative_extraction", reason=str(e))
        return _empty_slots()


# ---------------------------------------------------------------------------
# NARRATIVE REGEX FALLBACK
# ---------------------------------------------------------------------------

def _narrative_regex_fallback(regex_result: dict, message: str) -> dict:
    """Fallback for narrative extraction when the LLM is unavailable.

    Uses the caller-supplied regex_result, re-prioritizes the primary
    service_type by _URGENCY_HIERARCHY, and infers urgency from
    context clues.

    Example: "I just got out of the hospital and my housing fell through"
    Regex extracts: service_type=medical (via "hospital"), additional=shelter
    Fallback selects: service_type=shelter (higher urgency than medical)

    Note: operates on a copy of regex_result. Does NOT mutate the input.
    """
    result = dict(regex_result)

    # Collect all detected service types (primary + additional) with details
    all_services: list[tuple] = []
    primary = result.get("service_type")
    if primary:
        detail = result.get("service_detail")
        all_services.append((primary, detail))

    for item in result.get("additional_services", []):
        svc = item[0]
        detail = item[1] if len(item) > 1 else None
        all_services.append((svc, detail))

    if len(all_services) <= 1:
        # Single or no service — re-prioritization is a no-op
        return _augment_urgency_from_clues(result, message)

    # Re-rank by urgency hierarchy (stable sort preserves text-position
    # ordering when services tie, e.g. food vs mental_health).
    all_services.sort(
        key=lambda x: _URGENCY_HIERARCHY.get(x[0], 0),
        reverse=True,
    )

    new_primary, new_detail = all_services[0]
    remaining_deduped = []
    seen = {new_primary}
    for svc, det in all_services[1:]:
        if svc in seen:
            continue
        remaining_deduped.append((svc, det, None))  # no per-service location
        seen.add(svc)

    if new_primary != primary:
        logger.info(
            f"Narrative regex fallback: re-prioritized "
            f"'{primary}' → '{new_primary}' (urgency hierarchy)"
        )

    result["service_type"] = new_primary
    result["service_detail"] = new_detail
    result["additional_services"] = remaining_deduped

    return _augment_urgency_from_clues(result, message)


def _augment_urgency_from_clues(result: dict, message: str) -> dict:
    """Set urgency='high' if the narrative contains an urgency clue
    word and no urgency is already set. See Behavior #20 in the
    migration doc for the clue list (expanded from the legacy 14-word
    list to include DV/safety signals like 'not safe', 'fleeing',
    'threatened').
    """
    if result.get("urgency"):
        return result

    lower = message.lower()
    urgency_clues = (
        # Legacy list (14 words)
        "tonight", "right now", "nowhere to go", "kicked out",
        "evicted", "just released", "just got out", "ran away",
        "runaway", "on the street", "sleeping outside", "emergency",
        "fleeing", "escaped",
        # Phase-0 / Behavior #20 additions
        "not safe", "don't feel safe", "unsafe",
        "threatening", "threatened",
        "can't stay", "can't keep",
        "ran out of",
    )
    if any(c in lower for c in urgency_clues):
        result["urgency"] = "high"

    return result


# ---------------------------------------------------------------------------
# SHARED HELPERS
# ---------------------------------------------------------------------------

def _build_messages_with_history(
    message: str,
    conversation_history: Optional[list],
) -> list:
    """Build the messages array for the Claude API, injecting prior
    conversation turns with role-alternation padding as required.

    The Claude API rejects two consecutive messages with the same role.
    History turns are preserved verbatim where possible; when two
    consecutive turns would share a role, a placeholder with the other
    role is inserted. Matches legacy `llm_slot_extractor` Behavior #6.
    """
    messages: list = []
    if conversation_history:
        recent = conversation_history[-6:]
        for turn in recent:
            role = turn.get("role", "user")
            text = turn.get("text", "")
            api_role = "user" if role == "user" else "assistant"

            if messages and messages[-1]["role"] == api_role:
                placeholder_role = "assistant" if api_role == "user" else "user"
                messages.append({
                    "role": placeholder_role,
                    "content": "(continuing)",
                })

            messages.append({"role": api_role, "content": text})

        if messages and messages[-1]["role"] == "user":
            messages.append({"role": "assistant", "content": "(listening)"})

    messages.append({"role": "user", "content": message})
    return messages


def _normalize_tool_output(raw: dict) -> dict:
    """Translate the tool_use input dict into the 10-field result shape.

    The tool schema now returns `additional_services` as a list of
    objects `{type, detail?, location?}`. We convert those to 3-tuples
    `(type, detail, location)` for consistency with the regex side's
    `additional_services` representation.

    `service_detail` is captured here (new field in this migration's
    schema — see Behavior #24 in the migration doc). The merge layer
    snaps it to a canonical value or drops it.

    Note on field count: the regex side produces a 13-field dict
    (adding `no_requirements`, `_contradiction`, `_is_additive` per
    Trust Model 5 — those aren't asked of the LLM). The merge layer
    stitches LLM's 10 + regex's 3 → 13 fields.
    """
    additional_raw = raw.get("additional_services") or []
    normalized_additional: list[tuple] = []
    for item in additional_raw:
        if not isinstance(item, dict):
            continue
        svc = item.get("type")
        if not svc:
            continue
        detail = item.get("detail") or None
        loc = item.get("location") or None
        normalized_additional.append((svc, detail, loc))

    return {
        "service_type": raw.get("service_type"),
        "service_detail": raw.get("service_detail"),
        "additional_services": normalized_additional,
        "location": raw.get("location"),
        "age": raw.get("age"),
        "urgency": raw.get("urgency"),
        "_gender": raw.get("gender"),
        "family_status": raw.get("family_status"),
        "_populations": raw.get("populations") or [],
        "org_name": raw.get("org_name"),
    }


def _empty_slots() -> dict:
    """The ten-field all-None/empty dict. Returned when an LLM call
    fails and the caller needs a well-shaped dict before deciding
    whether to fall back to regex wholesale."""
    return {
        "service_type": None,
        "service_detail": None,
        "additional_services": [],
        "location": None,
        "age": None,
        "urgency": None,
        "_gender": None,
        "family_status": None,
        "_populations": [],
        "org_name": None,
    }


def _record_success(task: str, response, latency_ms: int) -> None:
    """Log a successful LLM call to the audit trail. Guarded so audit
    failures never bubble up (audit is observability, not correctness).
    """
    try:
        from app.services.audit_log import record_llm_call
        usage = getattr(response, "usage", None)
        record_llm_call(
            task=task,
            model=SLOT_EXTRACTION_MODEL,
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            latency_ms=latency_ms,
            success=True,
        )
    except Exception as e:
        logger.debug(f"Audit log (success) failed: {e}")


def _record_failure(task: str, reason: str) -> None:
    """Log a failed LLM call to the audit trail. Fixes Behavior #16:
    the legacy code never recorded success=False, so the audit trail
    only had half the picture.

    The audit log's `record_llm_call` doesn't take an error message
    today (see audit_log.py:989). We log the reason separately through
    the standard logger so it's still discoverable in logs; the audit
    record itself just captures success=False. The warning log is
    outside the try/except so an audit-log failure doesn't also
    swallow the reason-logging.
    """
    logger.warning(f"LLM call failed — task={task}, reason={reason[:200]}")
    try:
        from app.services.audit_log import record_llm_call
        record_llm_call(
            task=task,
            model=SLOT_EXTRACTION_MODEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            success=False,
        )
    except Exception as e:
        logger.debug(f"Audit log (failure) failed: {e}")
