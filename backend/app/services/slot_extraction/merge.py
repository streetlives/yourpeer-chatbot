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
# TRUST MODEL 1 — sub-case: location with canonical-form validator
# ---------------------------------------------------------------------------

def _validate_location(llm_value: Optional[str]) -> Optional[str]:
    """Snap LLM's location to a canonical `_KNOWN_LOCATIONS` value, or
    drop it.

    Rationale: regex's location extraction uses the curated
    `_KNOWN_LOCATIONS` vocabulary, but only recognizes exact-match
    tokens — it misses typos ("broklyn"), stylistic variants ("LES"),
    and regional shorthand. When regex finds nothing, the LLM's
    interpretation is usually correct (Haiku handles typo tolerance
    and well-known abbreviations reliably). We want to USE that
    interpretation downstream rather than drop the entire location
    slot — but only if it matches our canonical vocabulary, so
    downstream DB filters don't need to know every possible spelling.

    Matching logic (intentionally strict — exact match only):
      1. Case-insensitive, whitespace-normalized exact match against
         the `_KNOWN_LOCATIONS` list → return the canonical form.
      2. Otherwise → None.

    No substring or fuzzy matching. If the LLM returns "lower
    manhattan" but our canonical is "manhattan", the validator drops
    it. Rationale: substring matches risk false positives ("I'm in
    New Brooklyn" matching "brooklyn"), and fuzzy matches risk
    accepting genuine junk. Keep it strict; if R37 shows the LLM
    returning non-canonical variants we want to accept, revisit with
    a specific list of aliases.

    Motivating case: `accessibility_low_literacy` ("were food broklyn
    free") — regex misses "broklyn", LLM returns "brooklyn"
    (canonical), validator accepts → final location = "brooklyn".
    """
    if not llm_value or not isinstance(llm_value, str):
        return None

    # Local import: same pattern as _validate_service_detail — avoid
    # forcing a slot_extractor import at package load time.
    from app.services.slot_extractor import _KNOWN_LOCATIONS

    llm_norm = _normalize_for_match(llm_value)
    if not llm_norm:
        return None

    for canonical in _KNOWN_LOCATIONS:
        if _normalize_for_match(canonical) == llm_norm:
            return canonical

    logger.info(
        f"location dropped (not in _KNOWN_LOCATIONS: {llm_value!r})"
    )
    return None


def _merge_location(
    regex_value: Optional[str],
    llm_value: Optional[str],
) -> Optional[str]:
    """Trust Model 1 sub-case: regex wins, validator-gated LLM fallback.

    Mirrors `_merge_service_detail` and `_merge_org_name`. The regex's
    `_KNOWN_LOCATIONS` match always produces a canonical value, so we
    trust it outright. LLM output is run through the canonical-form
    validator before use — the LLM is good at typo/variant recognition
    but we only accept values that match our downstream-filter
    vocabulary.
    """
    if regex_value:
        return regex_value
    return _validate_location(llm_value)


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
    message: Optional[str] = None,
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
        elif R == L:                LLM wins            ← Ext-2b
        else:                       LLM wins

    When regex wins, we return `regex_result`'s triple verbatim.
    When LLM wins, we return the LLM-extracted triple.

    `additional_services` here is the WINNER's list — the hybrid
    dedup-union with the loser is done afterward by
    `_merge_additional_services`.

    Ext-2b (applied after R36 Phase 2 parallel-run, replacing the
    earlier narrative-path-only exception):

        When sets match, the LLM's primary pick wins regardless of
        message length. Both LLM paths teach primary selection:

          - Narrative prompt: full urgency hierarchy with worked
            examples (hospital = context, shelter = request).
          - Short prompt (Option 4): first-mentioned by default,
            shelter/medical when a safety signal is present
            ("tonight", "right now", "nowhere to sleep", etc.).

        Regex's static priority table can't distinguish request from
        context or first-mentioned from priority-ordered; the prompts
        can. This rule change unifies the treatment across paths and
        lets the prompt — not the merge — be the locus of primary-
        selection logic going forward.

        `message` is preserved in the signature for backward
        compatibility with direct-caller unit tests and for possible
        future use; it is no longer consulted in the sets-match
        branch.
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
        # Both parsers agree on WHAT was requested. Trust the LLM's
        # primary pick regardless of message length ("Ext-2b":
        # extension of the original narrative-path exception to the
        # short path).
        #
        # Why LLM wins on sets-match:
        #   - The narrative prompt teaches urgency hierarchy with
        #     worked examples (e.g., "I just got out of the hospital
        #     and my housing fell through → service_type: shelter,
        #     NOT medical").
        #   - The short prompt (Option 4 hardening, Phase 2) teaches
        #     first-mentioned-wins UNLESS a safety signal is present
        #     ("tonight", "right now", "nowhere to sleep", etc.),
        #     in which case shelter/medical wins.
        #   - Regex's priority table is static — it can't distinguish
        #     "I need food and shelter in Brooklyn" (first-mentioned
        #     food is what the user said first) from "I need a bed
        #     tonight and some food" (safety signal promotes shelter).
        #     The prompts can.
        #
        # This collapses the earlier short/narrative distinction. The
        # `message` parameter is no longer consulted in this branch;
        # it's preserved in the signature for backward compatibility
        # with direct-caller unit tests and for future use.
        #
        # Scenarios this rule serves (R36 Category A + B, same fix):
        #   - multi_food_and_shelter_brooklyn (short, food-first)
        #   - multi_shower_and_food_drop_in (short, personal_care-first)
        #   - multi_cross_neighborhood_shower_les_food_chinatown (short)
        #   - natural_long_story (narrative, hospital-context)
        #
        # Ext-2b cross-borough carve-out:
        #
        # When regex detected distinct per-service locations ("food in
        # Brooklyn and shelter in Manhattan") but the LLM collapsed both
        # services to one location, regex's positional binding is more
        # reliable than the LLM's semantic inference. Regex binds
        # locations by adjacency to each service name — deterministic
        # and correct when the phrasing provides it. The LLM's tool
        # output often drops one of the locations.
        #
        # Without this carve-out, Ext-2b regressed
        # `multi_cross_borough_food_brooklyn_shelter_manhattan` from
        # R36 baseline 4.73 to 3.36 — the migration's headline scenario.
        #
        # The carve-out is narrow: it fires only when regex has distinct
        # per-service locations AND the LLM does NOT. When both extractors
        # preserve cross-location structure (as in
        # `multi_cross_neighborhood_shower_les_food_chinatown` where the
        # LLM correctly binds shower→LES and food→Chinatown), the rule
        # does not fire and Ext-2b applies as before. This keeps the
        # first-mentioned / priority-hierarchy benefits of Ext-2b for
        # cases where LLM's output is trustworthy.
        regex_primary_loc = regex_result.get("location")

        def _loc_norm(x):
            return x.lower() if isinstance(x, str) else x

        regex_has_cross = any(
            item and len(item) >= 3 and item[2] is not None
            and _loc_norm(item[2]) != _loc_norm(regex_primary_loc)
            for item in regex_additional
        )
        llm_primary_loc = llm_result.get("location")
        llm_has_cross = llm_primary_loc is not None and any(
            item and len(item) >= 3 and item[2] is not None
            and _loc_norm(item[2]) != _loc_norm(llm_primary_loc)
            for item in llm_additional
        )
        if regex_has_cross and not llm_has_cross:
            logger.info(
                f"Set-agreement: regex has cross-location, LLM collapsed — "
                f"regex wins on primary+location+additional (set={sorted(regex_set)})"
            )
            return regex_primary, regex_result.get("location"), regex_additional

        logger.info(
            f"Set-agreement: regex set={sorted(regex_set)}, "
            f"llm set={sorted(llm_set)}, LLM wins on primary"
        )
        return llm_primary, llm_result.get("location"), llm_additional

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
            - `type`: regex wins
            - `detail`: regex's value if non-None, else LLM's value
            - `location`: regex's value if non-None, else LLM's value

    In other words: regex is authoritative for TYPE (matches Trust
    Model 1 for additional services), but for DETAIL and LOCATION
    we prefer whichever source populated them — LLM often carries
    detail/location info that regex missed, especially in
    multi-borough scenarios or for implicit sub-types.

    Primary service_type is excluded from the result (to avoid the
    primary appearing in its own additionals).

    Each returned item is a 3-tuple `(type, detail, location)` where
    `detail` and `location` may be None.
    """
    seen: set = set()
    if primary:
        seen.add(primary)

    # Index LLM additionals by type so we can merge per-field when a
    # regex item has the same type. "first match wins" for duplicates
    # within llm_additional, which is consistent with how the rest of
    # the function deduplicates.
    llm_by_type: dict = {}
    for item in llm_additional or []:
        svc, detail, loc = _unpack_additional_item(item)
        if svc and svc not in llm_by_type:
            llm_by_type[svc] = (svc, detail, loc)

    result: list = []

    # 1) Preserve regex tuples first, merging per-field with LLM's
    #    tuple for the same type when LLM has info regex missed.
    for item in regex_additional or []:
        svc, regex_detail, regex_loc = _unpack_additional_item(item)
        if not svc:
            continue
        if svc in seen:
            # Behavior #19.3: log primary-exclusion at debug level for
            # traceability. Regex's priority-sort can produce an
            # `additional_services` entry that duplicates the promoted
            # primary — silently dropping it is correct, but the
            # dedup happens enough that logging helps ops diagnosis.
            if svc == primary:
                logger.debug(
                    f"_merge_additional_services: excluding {svc!r} from "
                    f"regex_additional (matches primary)"
                )
            continue

        # Per-field merge: regex wins if non-None, else LLM fills.
        # Behavior #19.5 in the migration doc.
        merged_detail = regex_detail
        merged_loc = regex_loc
        if svc in llm_by_type:
            _, llm_detail, llm_loc = llm_by_type[svc]
            if merged_detail is None and llm_detail is not None:
                merged_detail = llm_detail
            if merged_loc is None and llm_loc is not None:
                merged_loc = llm_loc

        result.append((svc, merged_detail, merged_loc))
        seen.add(svc)

    # 2) For each item in winner_additional not yet seen, add it.
    #    Step 1 already added all regex items (with LLM's fields merged
    #    in where applicable), so winner_additional items reaching here
    #    are those regex didn't have. We use winner_additional's tuple
    #    directly.
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

def merge(
    regex_result: dict,
    llm_result: dict,
    message: Optional[str] = None,
) -> dict:
    """Merge regex and LLM extraction results into the 13-field dict
    the orchestrator expects.

    Preconditions:
      - `regex_result` has all 13 fields (regex-side produces the
        canonical shape via slot_extractor.extract_slots).
      - `llm_result` has the 10 LLM-contributed fields populated where
        possible (the 3 regex-only Trust Model 5 fields are not
        present).
      - `message` (optional) is the original user message. When
        supplied, Trust Model 3 uses it to distinguish narrative-path
        messages (≥ _NARRATIVE_THRESHOLD words) from short-path ones
        and applies the narrative-path exception to the sets-match
        rule. When None, the original sets-match rule (regex priority
        wins) applies — this preserves backward compatibility for
        unit tests that call `merge()` without the path distinction.

    Postconditions:
      - returned dict has the same 13 keys as regex_result
      - all Trust Model 5 fields (no_requirements, _contradiction,
        _is_additive) come directly from regex_result
      - all other fields follow their trust model's rule
    """
    # TRUST MODEL 3: service_type, primary-winner's location, winner's additionals.
    # The primary-winner's location is used AS-IS when it has a value —
    # whether that's LLM (Ext-2b wins on sets-match) or regex (carve-out
    # fires). This keeps primary-service-to-location binding consistent:
    # when LLM wins with `(food, Brooklyn, [shelter+Manhattan])`, food
    # stays bound to Brooklyn and does not get silently rebound to
    # regex's Manhattan (which was regex's primary-for-shelter value).
    service_type, location_from_primary, winner_additional = \
        _merge_service_type_and_primary_location(regex_result, llm_result, message)

    # TRUST MODEL 1: location (regex-literal, validator-gated LLM fallback)
    # When the primary winner has a location, it's the source of truth —
    # the location is tied to THAT service's binding in THAT extractor's
    # output. Only when the primary winner has no location (e.g., regex
    # won primary but regex's `_KNOWN_LOCATIONS` couldn't parse the
    # user's phrasing — the `accessibility_low_literacy` case) do we
    # fall back to `_merge_location`, which consults both sides with
    # validator-gated LLM fallback.
    #
    # LLM-sourced locations MUST go through `_validate_location` even
    # when LLM won primary — the LLM can hallucinate non-NYC locations
    # ("Chicago") and the validator drops anything outside our
    # `_KNOWN_LOCATIONS` vocabulary. Regex-sourced locations are already
    # canonical (by construction — regex only emits values from that
    # vocabulary), so they're used directly.
    #
    # Prior version (rev pre-carve-out) always called `_merge_location`
    # with both sides' raw locations, which decoupled location from
    # primary-service choice. That caused `multi_cross_borough` to
    # score 3.36 (R36 baseline 4.73): when Ext-2b gave LLM's food the
    # primary win, regex's "manhattan" (which was regex's primary-for-
    # shelter location) overrode LLM's "Brooklyn" for food — binding
    # food to Manhattan when the user said Brooklyn.
    llm_won_primary = (
        service_type is not None
        and service_type == llm_result.get("service_type")
    )
    if location_from_primary is None:
        # Primary winner had no location — existing fallback via
        # validator-gated Trust Model 1 (protects accessibility_low_literacy
        # when regex couldn't parse the user's location phrasing).
        location = _merge_location(
            regex_result.get("location"),
            llm_result.get("location"),
        )
    elif llm_won_primary:
        # LLM's location needs validator-gating — it can hallucinate.
        # If validator drops it (not in _KNOWN_LOCATIONS), fall back to
        # regex's location (which may also be None, and that's fine).
        location = _validate_location(location_from_primary) or regex_result.get("location")
    else:
        # Regex won primary; its location is already canonical.
        location = location_from_primary

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
