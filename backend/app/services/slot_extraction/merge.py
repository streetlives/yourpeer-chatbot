"""Per-field merge rules for the unified slot extractor.

The five trust models from the migration doc are each implemented as a
named function:

    _merge_regex_literal(...)  — Trust Model 1 (location, _gender,
                                 service_detail, org_name)
    _merge_llm_semantic(...)   — Trust Model 2 (age, urgency,
                                 family_status)
    _merge_service_type_and_primary_location(...)
                               — Trust Model 3 (service_type + its
                                 primary location + winner's
                                 additional_services)
    _merge_union(...)          — Trust Model 4 (_populations)
    _merge_regex_only(...)     — Trust Model 5 (no_requirements,
                                 _contradiction, _is_additive)

Plus two validator-gated sub-cases of Trust Model 1 with their own
named functions:

    _validate_service_detail(llm_value)     — snap to _NOTABLE_SUB_TYPES
                                              canonical value or drop
    _validate_org_name(llm_value)           — fuzzy-match against
                                              _KNOWN_ORGS or drop

And the hybrid rule:

    _merge_additional_services(...)         — dedup-union with regex-
                                              tuple preservation

The top-level `merge(regex_result, llm_result)` function composes these
into the 13-field result dict the orchestrator expects.

If you are adding a new field to the extractor, pick the trust model
that already fits, not a new one — the five models are load-bearing by
design. See "Per-field reference table" in the migration doc.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Optional

from .prompts import _SERVICE_TYPE_ENUM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TRUST MODEL 1 — regex literal authority
# ---------------------------------------------------------------------------

def _merge_regex_literal(
    regex_value: Any,
    llm_value: Any,
) -> Any:
    """Trust Model 1: regex's explicit-phrase match wins; LLM fills gaps.

    Used for `location` and `_gender`. The regex's vocabulary is
    intentionally curated — "Manhattan" vs "lower manhattan" is a
    meaningful distinction downstream, and LLM paraphrases can drift.
    """
    if regex_value:
        return regex_value
    return llm_value


# ---------------------------------------------------------------------------
# TRUST MODEL 1 — sub-case: service_detail with canonical-form validator
# ---------------------------------------------------------------------------

def _validate_service_detail(llm_value: Optional[str]) -> Optional[str]:
    """Snap LLM's service_detail to a canonical `_NOTABLE_SUB_TYPES`
    value, or drop it.

    Rationale: the tool schema doesn't enum-constrain service_detail
    (98 values would bloat the prompt), so the LLM may output
    near-canonical strings like "diabetes care" instead of the
    canonical "diabetes / insulin care". Downstream DB filters do
    substring matching, so non-canonical values cause false-negatives.
    The validator snaps to a canonical or drops.

    Matching logic (in order of preference):
      1. Exact canonical value match (case-insensitive) → keep as-is
         in canonical case.
      2. LLM value is a substring of a canonical value, OR a canonical
         value is a substring of the LLM value → snap to the canonical.
      3. SequenceMatcher ratio ≥ 0.80 on a normalized (lowercased,
         whitespace-collapsed) comparison → snap to the canonical.
      4. Otherwise → drop.

    Ratio 0.80 is empirical, not sacred; tune if eval shows too-
    aggressive or too-conservative snapping.
    """
    if not llm_value or not isinstance(llm_value, str):
        return None

    # Local import so this module doesn't force a slot_extractor
    # import at package load time. The _NOTABLE_SUB_TYPES dict is
    # built from several hundred lines of keyword mappings, so we
    # pull only the canonical-values set.
    from app.services.slot_extractor import _NOTABLE_SUB_TYPES
    # Sorted for deterministic iteration — a Python set's iteration
    # order depends on hash seeding, which means "care" could map
    # to "urgent care" in one process and "diabetes / insulin care"
    # in another. Sorting eliminates that non-determinism.
    canonical_values = sorted(set(_NOTABLE_SUB_TYPES.values()))

    llm_norm = _normalize_for_match(llm_value)
    if not llm_norm:
        return None

    # Exact case-insensitive
    for canonical in canonical_values:
        if _normalize_for_match(canonical) == llm_norm:
            return canonical

    # Substring (both directions). When multiple canonicals match,
    # prefer the LONGEST canonical — longer matches are more specific
    # and typically the intended meaning (e.g. "care" → "urgent care"
    # rather than matching an arbitrary first-found canonical).
    substring_matches = [
        c for c in canonical_values
        if llm_norm in _normalize_for_match(c)
        or _normalize_for_match(c) in llm_norm
    ]
    if substring_matches:
        # Sort by length DESC, then alphabetically for final determinism
        substring_matches.sort(key=lambda c: (-len(c), c))
        return substring_matches[0]

    # Fuzzy ratio. Ties broken by the sorted iteration order above
    # (first-wins among equal ratios) — deterministic because the
    # list is already sorted.
    best_canonical: Optional[str] = None
    best_ratio = 0.0
    for canonical in canonical_values:
        canonical_norm = _normalize_for_match(canonical)
        ratio = difflib.SequenceMatcher(None, llm_norm, canonical_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_canonical = canonical

    if best_ratio >= 0.80 and best_canonical:
        logger.debug(
            f"service_detail fuzzy-snap: {llm_value!r} → {best_canonical!r} "
            f"(ratio={best_ratio:.2f})"
        )
        return best_canonical

    logger.info(
        f"service_detail dropped (no canonical match for {llm_value!r}, "
        f"best ratio={best_ratio:.2f})"
    )
    return None


def _merge_service_detail(
    regex_value: Optional[str],
    llm_value: Optional[str],
) -> Optional[str]:
    """Trust Model 1 sub-case: regex wins, validator-gated LLM fallback.

    Regex's `_NOTABLE_SUB_TYPES` lookup always produces a canonical
    value, so we trust it outright. LLM output is run through the
    canonical-form validator before use.
    """
    if regex_value:
        return regex_value
    return _validate_service_detail(llm_value)


# ---------------------------------------------------------------------------
# TRUST MODEL 1 — sub-case: org_name with fuzzy-match validator
# ---------------------------------------------------------------------------

def _validate_org_name(llm_value: Optional[str]) -> Optional[str]:
    """Accept LLM's org_name only if it fuzzy-matches a known canonical
    org. Otherwise drop.

    Matches approximately `token-sort-ratio ≥ 85` from the rapidfuzz
    library, implemented with stdlib difflib.SequenceMatcher over a
    token-sorted lowercased comparison.

    The goal is to allow LLM coverage for minor misspellings and word-
    order variations ("Center for Ali Forney" → "Ali Forney Center")
    while blocking hallucinated orgs the regex table doesn't know.
    """
    if not llm_value or not isinstance(llm_value, str):
        return None

    from app.services.slot_extractor import (
        _KNOWN_ORGS,
        _KNOWN_ORG_ABBREVIATIONS,
    )
    # Sorted for deterministic iteration (set order depends on hash
    # seeding; we need identical results across Python processes).
    canonicals = sorted(
        set(_KNOWN_ORGS.values()) | set(_KNOWN_ORG_ABBREVIATIONS.values())
    )

    llm_tokens = _token_sort(llm_value)
    if not llm_tokens:
        return None

    best_canonical: Optional[str] = None
    best_ratio = 0.0
    for canonical in canonicals:
        canonical_tokens = _token_sort(canonical)
        ratio = difflib.SequenceMatcher(None, llm_tokens, canonical_tokens).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_canonical = canonical

    if best_ratio >= 0.85 and best_canonical:
        if best_canonical != llm_value:
            logger.debug(
                f"org_name fuzzy-snap: {llm_value!r} → {best_canonical!r} "
                f"(ratio={best_ratio:.2f})"
            )
        return best_canonical

    logger.info(
        f"org_name dropped (no canonical match for {llm_value!r}, "
        f"best ratio={best_ratio:.2f})"
    )
    return None


def _merge_org_name(
    regex_value: Optional[str],
    llm_value: Optional[str],
) -> Optional[str]:
    """Trust Model 1 sub-case: regex wins, validator-gated LLM fallback.

    Regex's `_extract_org_name` only returns strings that are already
    canonical (it looks up `_KNOWN_ORGS[...]` or
    `_KNOWN_ORG_ABBREVIATIONS[...]`), so regex values pass through
    unchanged.
    """
    if regex_value:
        return regex_value
    return _validate_org_name(llm_value)


# ---------------------------------------------------------------------------
# TRUST MODEL 2 — LLM semantic authority
# ---------------------------------------------------------------------------

def _merge_llm_semantic(
    regex_value: Any,
    llm_value: Any,
) -> Any:
    """Trust Model 2: LLM's context-awareness wins; regex fills gaps.

    Used for `age`, `urgency`, `family_status`. These fields are
    expressed implicitly ('tonight' → urgency=high) or with attribution
    ('my son is 12' is NOT the user's age in some contexts but IS per
    the schema). The LLM resolves those ambiguities better than regex.
    """
    if llm_value is not None:
        return llm_value
    return regex_value


# ---------------------------------------------------------------------------
# TRUST MODEL 3 — set-agreement decides ordering for service_type
# ---------------------------------------------------------------------------

def _merge_service_type_and_primary_location(
    regex_result: dict,
    llm_result: dict,
) -> tuple[Optional[str], Optional[str], list]:
    """Trust Model 3: the set-agreement rule for `service_type` and
    its primary `location` plus `additional_services`.

    Returns `(service_type, location, additional_services)` — a
    triple, because the primary location and additional_services are
    both bound to the choice of winner.

    The rule:
        R = {regex_primary} ∪ regex_additional_service_types
        L = {llm_primary}   ∪ llm_additional_service_types

        if R is empty:              LLM wins
        elif L is empty:            regex wins
        elif R == L:                regex priority wins
        else:                       LLM wins

    When regex wins, we return `regex_result`'s triple verbatim.
    When LLM wins, we return the LLM-extracted triple.

    `additional_services` here is the WINNER's list — the hybrid
    dedup-union with the loser is done afterward by
    `_merge_additional_services`.
    """
    regex_primary = regex_result.get("service_type")
    regex_additional = regex_result.get("additional_services") or []
    llm_primary = llm_result.get("service_type")
    llm_additional = llm_result.get("additional_services") or []

    # Build the two sets of service types
    regex_set = set()
    if regex_primary:
        regex_set.add(regex_primary)
    for item in regex_additional:
        if item and item[0]:
            regex_set.add(item[0])

    llm_set = set()
    if llm_primary:
        llm_set.add(llm_primary)
    for item in llm_additional:
        if item and item[0]:
            llm_set.add(item[0])

    if not regex_set:
        # Regex found nothing; LLM's output (including None) is authoritative.
        return llm_primary, llm_result.get("location"), llm_additional

    if not llm_set:
        # LLM found nothing; trust regex.
        return regex_primary, regex_result.get("location"), regex_additional

    if regex_set == llm_set:
        # Both parsers agree on WHAT was requested. Regex's priority
        # table decides WHICH is primary. This is where the set-
        # equality blind spot lives — see the four watch-list scenarios
        # in the Phase 2 acceptance criteria.
        #
        # Note: we return `regex_primary` directly rather than re-applying
        # _SERVICE_NEED_PRIORITY here because regex's own extract_slots
        # ALREADY sorts its output by priority (see
        # slot_extractor._extract_all_service_types:933). So
        # regex_primary is, by construction, the priority-table winner
        # of its own set. The rule's "regex priority wins" is satisfied
        # trivially.
        return regex_primary, regex_result.get("location"), regex_additional

    # Sets disagree. LLM's filtering (context vs request) or coverage
    # (missing regex keyword) is the signal we trust.
    logger.info(
        f"Set-agreement: regex set={sorted(regex_set)}, llm set={sorted(llm_set)}, "
        f"LLM wins"
    )
    return llm_primary, llm_result.get("location"), llm_additional


# ---------------------------------------------------------------------------
# TRUST MODEL 4 — union with false-positive tolerance
# ---------------------------------------------------------------------------

def _merge_union(
    regex_value: Optional[list],
    llm_value: Optional[list],
) -> list:
    """Trust Model 4: union of both sources, sorted for determinism.

    Used for `_populations`. Regex catches explicit keywords; the
    semantic router contributes novel-phrase matches (folded into
    `regex_result["_populations"]` upstream at
    `pipeline._run_early_extraction`); LLM catches implicit membership.

    Accepted cost: "my brother's in jail" → regex-FP `reentry`. Net
    recall gain from union beats the FP rate.
    """
    result_set: set = set()
    for source in (regex_value or [], llm_value or []):
        for item in source:
            if isinstance(item, str) and item.strip():
                result_set.add(item.strip())
    return sorted(result_set)


# ---------------------------------------------------------------------------
# TRUST MODEL 5 — regex-only (LLM not asked)
# ---------------------------------------------------------------------------

def _merge_regex_only(regex_value: Any) -> Any:
    """Trust Model 5: regex-only. LLM doesn't contribute; we just
    pass regex's value through.

    Used for `no_requirements`, `_contradiction`, `_is_additive`.
    These boolean signals come from tuned phrase lists; LLM could
    over-trigger on mild phrases. Future work may move them to Trust
    Model 2 if Haiku proves reliable, but out of scope here.
    """
    return regex_value


# ---------------------------------------------------------------------------
# HYBRID — additional_services (dedup-union with regex-tuple preservation)
# ---------------------------------------------------------------------------

def _merge_additional_services(
    primary: Optional[str],
    winner_additional: list,
    regex_additional: list,
    llm_additional: list,
) -> list:
    """Dedup-union of regex and LLM additional services.

    Called AFTER `_merge_service_type_and_primary_location` has chosen
    the winner and emitted `winner_additional` (the winner's own
    additionals list). The caller passes in both sides' additionals
    because even when the LLM wins primary, some regex additionals
    (with their richer `(type, detail, location)` tuples) may still be
    worth keeping.

    Rule:
        For each unique service type in
        winner_additional ∪ regex_additional ∪ llm_additional:
            if regex has it:  use regex's tuple (type, detail, location)
            else:             use LLM's / winner's tuple

    Primary service_type is excluded from the result (to avoid the
    primary appearing in its own additionals).

    Each returned item is a 3-tuple `(type, detail, location)` where
    `detail` and `location` may be None.
    """
    seen: set = set()
    if primary:
        seen.add(primary)

    result: list = []

    # 1) Preserve regex tuples first — they have detail/location info
    #    that LLM's may not carry.
    for item in regex_additional or []:
        svc, detail, loc = _unpack_additional_item(item)
        if not svc or svc in seen:
            continue
        result.append((svc, detail, loc))
        seen.add(svc)

    # 2) For each item in winner_additional not yet seen, add it.
    #    Step 1 already added all regex items with their richer tuples,
    #    so winner_additional items reaching here are those regex
    #    didn't have. We use winner_additional's tuple directly.
    for item in winner_additional or []:
        svc, detail, loc = _unpack_additional_item(item)
        if not svc or svc in seen:
            continue
        result.append((svc, detail, loc))
        seen.add(svc)

    # 3) Finally, LLM additionals not picked up yet.
    for item in llm_additional or []:
        svc, detail, loc = _unpack_additional_item(item)
        if not svc or svc in seen:
            continue
        result.append((svc, detail, loc))
        seen.add(svc)
        logger.info(
            f"LLM detected additional service {svc!r} "
            f"that regex missed"
        )

    return result


def _unpack_additional_item(item: Any) -> tuple:
    """Coerce an additional-services entry into a (type, detail,
    location) triple.

    Accepts both legacy 2-tuples and new 3-tuples. Returns `(None, None,
    None)` for anything unparseable so callers can skip-filter.
    """
    if not item:
        return (None, None, None)
    if isinstance(item, tuple) or isinstance(item, list):
        if len(item) >= 3:
            return (item[0], item[1], item[2])
        if len(item) == 2:
            return (item[0], item[1], None)
        if len(item) == 1:  # pragma: no branch
            # len can't be 0 here (caught by `if not item` above) and
            # can't be >3 (caught above). Remaining case is always 1.
            return (item[0], None, None)
    if isinstance(item, str):
        return (item, None, None)
    return (None, None, None)


# ---------------------------------------------------------------------------
# TOP-LEVEL COMPOSITION
# ---------------------------------------------------------------------------

def merge(regex_result: dict, llm_result: dict) -> dict:
    """Merge regex and LLM extraction results into the 13-field dict
    the orchestrator expects.

    Preconditions:
      - `regex_result` has all 13 fields (regex-side produces the
        canonical shape via slot_extractor.extract_slots).
      - `llm_result` has the 10 LLM-contributed fields populated where
        possible (the 3 regex-only Trust Model 5 fields are not
        present).

    Postconditions:
      - returned dict has the same 13 keys as regex_result
      - all Trust Model 5 fields (no_requirements, _contradiction,
        _is_additive) come directly from regex_result
      - all other fields follow their trust model's rule
    """
    # TRUST MODEL 3: service_type, primary location, winner's additionals
    service_type, location_from_primary, winner_additional = \
        _merge_service_type_and_primary_location(regex_result, llm_result)

    # TRUST MODEL 1: location (regex-literal)
    # Note: location is Trust Model 1 by design — the regex's explicit
    # location vocabulary (known NYC neighborhoods/boroughs) is preferred
    # over LLM paraphrases. This has a known tradeoff: messages like
    # "I'm in Queens but need food in Brooklyn" will regex-extract
    # "queens" and stay there, overriding the LLM's correct "brooklyn".
    # The Trust Model 1 rationale accepts this because paraphrase drift
    # in location literals ("lower manhattan", "manhattan island", etc.)
    # has caused more downstream harm than the occasional "intended
    # location" override. If Phase 2 eval surfaces scenarios where this
    # costs score points, revisit by moving location to Trust Model 3
    # (follow the set-agreement winner).
    location = _merge_regex_literal(regex_result.get("location"), location_from_primary)

    # TRUST MODEL 1: _gender
    gender = _merge_regex_literal(
        regex_result.get("_gender"),
        llm_result.get("_gender"),
    )

    # TRUST MODEL 1 sub-case: service_detail (validator-gated)
    service_detail = _merge_service_detail(
        regex_result.get("service_detail"),
        llm_result.get("service_detail"),
    )

    # TRUST MODEL 1 sub-case: org_name (validator-gated)
    org_name = _merge_org_name(
        regex_result.get("org_name"),
        llm_result.get("org_name"),
    )

    # TRUST MODEL 2: age, urgency, family_status
    age = _merge_llm_semantic(
        regex_result.get("age"),
        llm_result.get("age"),
    )
    urgency = _merge_llm_semantic(
        regex_result.get("urgency"),
        llm_result.get("urgency"),
    )
    family_status = _merge_llm_semantic(
        regex_result.get("family_status"),
        llm_result.get("family_status"),
    )

    # TRUST MODEL 4: _populations (union)
    populations = _merge_union(
        regex_result.get("_populations"),
        llm_result.get("_populations"),
    )

    # HYBRID: additional_services (dedup-union with regex-tuple preservation)
    additional_services = _merge_additional_services(
        primary=service_type,
        winner_additional=winner_additional,
        regex_additional=regex_result.get("additional_services") or [],
        llm_additional=llm_result.get("additional_services") or [],
    )

    # TRUST MODEL 5: regex-only phrase signals
    no_requirements = _merge_regex_only(regex_result.get("no_requirements"))
    contradiction = _merge_regex_only(regex_result.get("_contradiction"))
    is_additive = _merge_regex_only(regex_result.get("_is_additive"))

    return {
        # Trust Model 3
        "service_type": service_type,
        # Trust Model 1 (and sub-cases)
        "location": location,
        "service_detail": service_detail,
        "org_name": org_name,
        "_gender": gender,
        # Trust Model 2
        "age": age,
        "urgency": urgency,
        "family_status": family_status,
        # Trust Model 4
        "_populations": populations,
        # Hybrid
        "additional_services": additional_services,
        # Trust Model 5
        "no_requirements": no_requirements,
        "_contradiction": contradiction,
        "_is_additive": is_additive,
    }


# ---------------------------------------------------------------------------
# STRING HELPERS
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_match(value: str) -> str:
    """Lowercase + collapse whitespace. Used for substring/fuzzy
    comparison in the service_detail validator."""
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value.lower()).strip()


def _token_sort(value: str) -> str:
    """Lowercase, split into word tokens, sort alphabetically, rejoin
    with spaces. Approximates rapidfuzz's token_sort normalization for
    the org_name validator.

    'Ali Forney Center' → 'ali center forney'
    'the Ali Forney Center' → 'ali center forney the'
    """
    if not value:
        return ""
    tokens = sorted(value.lower().split())
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# VALIDATION — ENUM GUARDS
# ---------------------------------------------------------------------------

_VALID_SERVICE_TYPES = frozenset(_SERVICE_TYPE_ENUM)


def _filter_valid_service_types(llm_result: dict) -> dict:
    """Strip out any service_type or additional_services entries whose
    type isn't in the canonical enum.

    Belt-and-suspenders — the tool schema enum-constrains these fields,
    so the LLM shouldn't produce invalid values, but defensive
    filtering avoids silent downstream breakage if the schema ever
    drifts.

    Expects `additional_services` in the normalized 3-tuple format
    produced by `dispatch._normalize_tool_output`. Dict-format entries
    (raw tool_use shape) will be silently dropped — they shouldn't
    reach this function in production, but if they do,
    `_unpack_additional_item` returns `(None, None, None)` and the
    enum check filters them out.

    Returns a new dict (does not mutate input).
    """
    out = dict(llm_result)
    st = out.get("service_type")
    if st is not None and st not in _VALID_SERVICE_TYPES:
        logger.warning(f"Dropping LLM service_type {st!r}: not in enum")
        out["service_type"] = None

    out["additional_services"] = [
        item for item in out.get("additional_services", [])
        if item and _unpack_additional_item(item)[0] in _VALID_SERVICE_TYPES
    ]
    return out
