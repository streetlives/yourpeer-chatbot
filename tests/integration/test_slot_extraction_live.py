"""Live API tests for the unified slot extractor.

These tests hit the real Anthropic API and SKIP when no real API key
is set (CI without secrets, dev machines without a configured key).
When a developer runs them locally with a real key, they catch:
  - API contract changes (response shape drift)
  - Tool-use schema mismatches between our prompt and Anthropic's
    validation
  - Regressions in the prompts that produce semantically wrong
    extractions (e.g., LLM stops handling third-person correctly)

Ported from legacy `tests/unit/test_llm_slot_extractor.py` `test_live_*`
suite (Phase 4 Stage 4, April 2026). The legacy file's target
(`extract_slots_llm` from `app.services.llm_slot_extractor`) is
deleted; the equivalent in the unified architecture is
`extract_slots_short` from `app.services.slot_extraction.dispatch`.

Run locally with:

    ANTHROPIC_API_KEY=sk-ant-... pytest tests/integration/test_slot_extraction_live.py -v

These tests do NOT run in CI (the secret isn't set) and they do NOT
fail when the key is absent — they skip cleanly. Cost per full run:
< $0.01 (5 short LLM calls).
"""

from __future__ import annotations

import os

import pytest


def _api_key_looks_real() -> bool:
    """Return True if ANTHROPIC_API_KEY looks like a real working key.

    A "real-looking" key starts with `sk-ant-` and has a realistic body
    length (~100 chars). Placeholder keys (`sk-ant-test...`, `fake`,
    empty, etc.) produce a skip rather than a noisy 401 fail that
    doesn't tell us anything useful.
    """
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key.startswith("sk-ant-"):
        return False
    if len(key) < 50:
        return False
    lower = key.lower()
    if any(marker in lower for marker in ("test", "fake", "dummy", "placeholder", "nonworking")):
        return False
    return True


_skip_no_api_key = pytest.mark.skipif(
    not _api_key_looks_real(),
    reason=(
        "ANTHROPIC_API_KEY not set or clearly a placeholder — "
        "skipping live LLM tests"
    ),
)


@_skip_no_api_key
def test_live_simple_extraction():
    """[LIVE] Simple service + location extraction.

    Verifies the short-path tool_use schema works end-to-end against
    the real Claude Haiku model and returns the canonical lowercase
    forms post-normalization.
    """
    from app.services.slot_extraction.dispatch import extract_slots_short

    result = extract_slots_short("I need food in Brooklyn")
    assert result["service_type"] == "food"
    # location now lowercased by `_normalize_string_field` (Bug #2 fix);
    # `(result["location"] or "")` defends against rare empty-result paths.
    assert "brooklyn" in (result["location"] or "")


@_skip_no_api_key
def test_live_third_person():
    """[LIVE] Third-person extraction.

    Regression guard: 'my son is 12' must extract age=12, not None.
    The prompt explicitly handles third-person attribution (see
    schema description for `age` in `prompts.py`).
    """
    from app.services.slot_extraction.dispatch import extract_slots_short

    result = extract_slots_short("my son is 12 and needs a warm coat")
    assert result["service_type"] == "clothing"
    assert result["age"] == 12


@_skip_no_api_key
def test_live_contradicting_locations():
    """[LIVE] Intended vs current location.

    Tests that the LLM picks the SERVICE-RELEVANT location ('Bronx')
    rather than the user's current location ('Queens'). Edge case
    that regex can't disambiguate without context.
    """
    from app.services.slot_extraction.dispatch import extract_slots_short

    result = extract_slots_short(
        "I'm in Queens but looking for food in the Bronx"
    )
    assert result["service_type"] == "food"
    assert "bronx" in (result["location"] or "")


@_skip_no_api_key
def test_live_implicit_needs():
    """[LIVE] Implicit service type from context.

    'somewhere safe for tonight, I'm a woman' should yield
    service_type=shelter (no explicit shelter keyword), urgency=high
    (from 'tonight'), and a populated _gender. Tests the LLM's
    ability to infer the service category from semantic context.
    """
    from app.services.slot_extraction.dispatch import extract_slots_short

    result = extract_slots_short("somewhere safe for tonight, I'm a woman")
    assert result["service_type"] == "shelter"
    assert result["urgency"] == "high"
    assert result["_gender"] is not None


@_skip_no_api_key
def test_live_complex_sentence():
    """[LIVE] Complex sentence with multiple slots.

    Stresses simultaneous extraction of age, service_type, location,
    urgency, and (implicitly) the reentry population tag.
    """
    from app.services.slot_extraction.dispatch import extract_slots_short

    result = extract_slots_short(
        "I'm 22, just got out of Rikers, and I need help finding "
        "a place to stay in the Bronx tonight"
    )
    assert result["service_type"] == "shelter"
    assert result["age"] == 22
    assert "bronx" in (result["location"] or "")
    assert result["urgency"] == "high"
