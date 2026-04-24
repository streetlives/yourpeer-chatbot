"""Unified slot extractor for the YourPeer chatbot.

Public API:

    extract(message, regex_result, conversation_history=None,
            api_key_available=True) → dict

Written during the `llm_slot_extractor` → `slot_extraction` migration
(Phase 1, April 2026). Replaces both `extract_slots_smart` and
`classify_unified`. See UNIFIED_EXTRACTOR_MIGRATION.md for the full
design.

The module consolidates four concerns that were previously scattered:

    1. Dispatch: decide whether to call the LLM at all (simple
       messages skip), and if so, which prompt (short vs narrative).
       See `dispatch.py`.
    2. LLM call: a single function per prompt, returning the normalized
       10-field dict shape (the schema fields; Trust Model 5's
       three regex-only fields aren't asked of the LLM). See
       `dispatch.py`.
    3. Merge: apply per-field trust-model rules to combine regex and
       LLM output into the 13-field result. See `merge.py`.
    4. Prompts: tool schema + short + narrative system prompts kept
       close to the merge logic they pair with. See `prompts.py`.

Callers pass in `regex_result` instead of having it re-computed inside
the extractor. This eliminates the footgun where regex ran once in
`_run_early_extraction` and again inside `extract_slots_smart` with
independent semantic-router invocations — see Behavior #4 in the
migration doc.
"""

import logging
from typing import Optional

from .dispatch import (
    _is_narrative,
    _is_simple_message,
    _narrative_regex_fallback,
    extract_slots_narrative,
    extract_slots_short,
)
from .merge import _filter_valid_service_types, merge

logger = logging.getLogger(__name__)


__all__ = ["extract"]


def extract(
    message: str,
    regex_result: dict,
    conversation_history: Optional[list] = None,
    api_key_available: bool = True,
) -> dict:
    """Extract slots from a user message.

    Args:
        message: The current user message. Must be the same text
            regex_result was computed from.
        regex_result: The output of `slot_extractor.extract_slots` on
            `message`, possibly augmented with semantic-router
            populations (as `pipeline._run_early_extraction` does).
            Must have the 13-field shape documented in the migration
            doc.
        conversation_history: Optional list of prior turns, each a
            dict with keys 'role' ('user' or 'assistant') and 'text'.
            Used by the LLM paths to resolve references like 'there'
            or 'what about Brooklyn'.
        api_key_available: If False, skip all LLM calls and return
            regex_result unchanged (minus the narrative regex-fallback
            urgency augmentation for narrative-length messages).

    Returns:
        A dict with the same 13 fields as regex_result.

    This function never mutates its inputs.
    """
    # Fast path: no API key means we can't invoke the LLM. Long
    # narratives still get the urgency-clue augmentation from the
    # regex fallback, matching legacy `extract_slots_smart` behavior.
    if not api_key_available:
        if _is_narrative(message):
            return _narrative_regex_fallback(regex_result, message)
        return dict(regex_result)

    # Fast path: simple messages trust regex entirely. Four-criteria
    # check: ≤ 8 words, has service + location, location is a known
    # NYC location, only one service-keyword category matched.
    if _is_simple_message(message, regex_result):
        return dict(regex_result)

    # Narrative path: LLM is fully authoritative on service_type.
    # Regex remains authoritative for location, _gender, and the
    # other Trust Model 1 fields. LLM failure falls back to the
    # regex urgency-hierarchy re-prioritizer.
    if _is_narrative(message):
        llm_result = extract_slots_narrative(message, conversation_history)
        if _is_empty_llm_result(llm_result):
            logger.warning(
                "Narrative LLM returned empty — falling back to regex"
            )
            return _narrative_regex_fallback(regex_result, message)
        return merge(regex_result, _filter_valid_service_types(llm_result))

    # Short path, non-simple: call the short LLM and merge its output
    # with the caller's regex_result using per-field trust models.
    #
    # Note: the legacy `extract_slots_smart` had an optimization where a
    # message of ≤ 8 words with a semantic-router match would skip the
    # LLM entirely. That optimization now lives in the CALLER
    # (`pipeline._run_early_extraction`), which folds the router's
    # service_type and populations into `regex_result` before
    # invoking this function. From this layer's perspective, a
    # caller-supplied `regex_result.service_type` is already trusted;
    # we still run the LLM to enrich fields the regex/router path
    # doesn't catch (age, urgency, family_status, additional services,
    # etc.). If latency becomes a concern on these short messages,
    # revisit; the parallel-run eval in Phase 2 will reveal whether
    # skipping the LLM here costs score points.
    llm_result = extract_slots_short(message, conversation_history)
    if _is_empty_llm_result(llm_result):
        logger.warning(
            "Short-path LLM returned empty — falling back to regex"
        )
        return dict(regex_result)

    return merge(regex_result, _filter_valid_service_types(llm_result))


# ---------------------------------------------------------------------------
# DISPATCH HELPERS
# ---------------------------------------------------------------------------


def _is_empty_llm_result(result: dict) -> bool:
    """Check whether an LLM result is empty enough to warrant
    wholesale regex fallback.

    The LLM returns the empty-slots dict on failure (see
    `dispatch._empty_slots`). It may also return a tool_use block
    with all fields missing — e.g., a confused message that didn't
    trigger extraction. Either way, merging an empty LLM result into
    regex_result is a no-op that adds latency and logging noise. The
    caller returns regex_result directly instead.

    Criterion: no service_type, no service_detail, no location, no
    age, no urgency, no gender, no family_status, no org_name, empty
    additional_services, empty populations. All ten must be empty
    for this to fire.
    """
    if result.get("service_type"):
        return False
    if result.get("location"):
        return False
    if result.get("age") is not None:
        return False
    if result.get("urgency"):
        return False
    if result.get("_gender"):
        return False
    if result.get("family_status"):
        return False
    if result.get("org_name"):
        return False
    if result.get("service_detail"):
        return False
    if result.get("additional_services"):
        return False
    if result.get("_populations"):
        return False
    return True
