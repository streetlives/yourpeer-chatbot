"""Regression guards for the routing-category cascade in generate_reply.

Added during the chatbot.py decomposition (April 2026) to ensure that the
if/elif ordering at L651-694 stays byte-identical as code moves into
extracted helper functions. The order encodes precedence rules —
crisis wins over reset, reset wins over correction, etc. — and a
subtle reorder would change how the bot routes messages without
any test (before this file) catching it.

These tests inspect chatbot.py's source as text, not runtime behavior,
because the cascade has ~20 branches and exhaustive runtime testing
would be fragile and slow.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CHATBOT_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "backend" / "app" / "services" / "chatbot" / "pipeline.py"
)


@pytest.fixture(scope="module")
def chatbot_source() -> str:
    return CHATBOT_FILE.read_text()


def _extract_category_assignments(source: str) -> list[str]:
    """Return the sequence of category-string literals assigned in the cascade.

    Each category assignment is one of:
      category = "crisis"      — literal string
      category = action        — variable (kept as-is)
      category = llm_category  — variable
    """
    cascade = _get_cascade_source(source)

    # Extract every `category = <value>` — the RHS is either a string
    # literal or a bare identifier (action, llm_category)
    assignments: list[str] = []
    for m in re.finditer(r"^\s+category = (.+?)$", cascade, re.MULTILINE):
        rhs = m.group(1).strip()
        assignments.append(rhs)
    return assignments


def _get_cascade_source(source: str) -> str:
    """Slice out the body of _compute_routing_category.

    The cascade used to live directly inside generate_reply under the
    `# --- COMBINE INTO ROUTING CATEGORY ---` comment, but was extracted
    during the April 2026 decomposition. The routing precedence is now
    encoded inside the helper.
    """
    start = source.index("def _compute_routing_category(")
    # The helper ends at the next top-level `def`
    end_match = re.search(r"^def ", source[start + 1:], re.MULTILINE)
    assert end_match is not None, "Could not locate end of _compute_routing_category"
    end = start + 1 + end_match.start()
    return source[start:end]


def test_routing_category_sequence_is_preserved(chatbot_source: str) -> None:
    """The cascade must emit these category values in this order.

    If you reorder branches, you're changing precedence. Update this
    test ONLY if the reorder is intentional and reviewed.
    """
    expected = [
        '"crisis"',             # tone == crisis (highest)
        '"reset"',              # action == reset
        '"correction"',         # action == correction
        '"negative_preference"',
        "action",               # confirm_change_service / confirm_change_location / confirm_yes / confirm_deny
        "action",               # bot_identity / bot_question / greeting / thanks
        '"bot_question"',       # has_service_intent + action == bot_question
        '"escalation"',         # has_service_intent + action == escalation (no location)
        '"general"',            # has_service_intent + low-confidence "other" with no detail (LLM was reaching)
        '"service"',            # has_service_intent default
        '"help"',               # action == help
        '"escalation"',         # action == escalation
        '"frustration"',        # tone == frustrated
        '"emotional"',          # tone == emotional
        '"confused"',           # tone == confused
        '"general"',            # final else: no signal — default fallthrough
    ]
    actual = _extract_category_assignments(chatbot_source)
    assert actual == expected, (
        f"Routing-category cascade changed. Review the re-ordering carefully "
        f"before updating this test.\nExpected:\n{expected}\nActual:\n{actual}"
    )


def test_cascade_branch_conditions_unchanged(chatbot_source: str) -> None:
    """Spot-check specific conditions that encode load-bearing precedence.

    Crisis must come first; 'general' must be the final fallback.
    """
    cascade = _get_cascade_source(chatbot_source)

    # The FIRST cascade branch must test 'tone == crisis'. Note that
    # the cascade is preceded by a _confidence if/elif chain — find the
    # specific cascade by locating the first `if` followed by a
    # `category =` assignment.
    cascade_if_match = re.search(
        r'if (.+?):\s*\n\s+category = ', cascade, re.MULTILINE
    )
    assert cascade_if_match is not None, "Could not locate cascade's first branch"
    first_if = cascade_if_match.group(1).strip()
    assert first_if == 'tone == "crisis"', (
        f"First cascade branch must be 'tone == \"crisis\"', got: {first_if!r}. "
        f"Crisis detection must NEVER be displaced from the top of the cascade."
    )

    # The last reachable assignment before the closing else must be 'general'
    # with confidence = 'low'
    assert 'category = "general"' in cascade
    assert 'confidence = "low"' in cascade, (
        "The 'general' fallback must explicitly set confidence='low' — "
        "this signals to downstream handlers that the classification was "
        "a fallback, not a confident match."
    )


def test_has_service_intent_branch_position(chatbot_source: str) -> None:
    """The has_service_intent branch must be after 'confirm_*' but before 'help'.

    Rationale: a user typing "food" (service) while a confirmation is
    pending should route to confirm_yes handling (so the bot can
    disambiguate), not directly to service search. But a user typing
    "food" WITHOUT a pending confirmation should beat the generic
    "help" fallback.
    """
    cascade = _get_cascade_source(chatbot_source)

    confirm_pos = cascade.index('"confirm_change_service"')
    service_intent_pos = cascade.index("elif has_service_intent:")
    help_pos = cascade.index('action == "help"')
    assert confirm_pos < service_intent_pos < help_pos, (
        "Order broken: confirm_* must precede has_service_intent must "
        "precede help. See routing precedence comment above the cascade."
    )
