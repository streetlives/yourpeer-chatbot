"""Mobile-input apostrophe fuzz harness — ENG-1.

Mobile keyboards autocorrect straight apostrophes to curly ones (U+2019).
The codebase has ``normalize_apostrophes`` in ``app.utils.text_normalize``
that handles this at known call sites — but a new feature could route
user input through a path that forgets to normalize, and the regression
would be silent (the message just doesn't match the phrase list).

This harness runs a curated list of representative scenarios — each one
exercises a specific phrase-list dispatch — through the full
``generate_reply`` pipeline with the straight-apostrophe input and with
each of the four non-standard apostrophe variants. The expected
dispatch behavior is asserted to be identical across all variants.

If a future code path is added that handles user input without
normalization, this harness will surface the regression as a divergence
between the straight and curly variants.

Adding new scenarios:
  * Add a row to ``APOSTROPHE_SCENARIOS`` with the straight-apostrophe
    message and a callable that asserts the expected dispatch.
  * The assertion callable takes the result dict and the variant label
    (for clearer test names on failure).
  * Aim for messages where the apostrophe is load-bearing — i.e., where
    removing or substituting it would route the message differently.

See ``app.utils.text_normalize.NON_STANDARD_APOSTROPHES`` for the
canonical list of variants we test against.
"""
from __future__ import annotations

import re
from typing import Callable

import pytest

from conftest import send

from app.utils.text_normalize import NON_STANDARD_APOSTROPHES


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------
#
# Each scenario is a tuple of:
#   (label, straight_message, expected_assertion)
#
# The label is a short identifier shown in pytest test IDs. The straight
# message is the canonical apostrophe-bearing input. The assertion is a
# callable taking (result_dict, variant_label) — failure messages should
# include `variant_label` so a regression points clearly at which
# substitution broke.

# A scenario where "I don't know" / "I'm not sure" should trigger the
# location-unknown picker. The detector is in
# ``_handle_location_unknown`` which calls ``normalize_apostrophes`` on
# the raw message before phrase-matching.
def _assert_location_unknown_offers_picker(result, variant):
    """The location-unknown path offers the geolocation/borough picker."""
    response = (result.get("response") or "").lower()
    assert "location" in response, (
        f"[variant={variant}] location-unknown path didn't fire — "
        f"response was {result.get('response')!r}"
    )
    # Picker shows a quick-replies row including "Use my location"
    quick = result.get("quick_replies") or []
    labels = [qr.get("label", "") for qr in quick]
    assert any("location" in lbl.lower() for lbl in labels), (
        f"[variant={variant}] location picker quick-replies missing — "
        f"got {labels}"
    )


# Each row: (label, straight_message, assertion_callable, setup_callable_or_None).
# setup_callable runs before the apostrophe-bearing message is sent and
# is responsible for putting the session into the right precondition
# state (e.g. service_type set so location-unknown can fire).
#
# === SCOPE NOTE ===
# As of this harness's introduction, the only integration-reachable
# dispatch path that calls ``normalize_apostrophes`` on raw user text is
# ``_handle_location_unknown``. Other handlers that DO normalize:
#
# - ``_handle_demographic_skip``: preconditions can't be reached via
#   natural conversation (service+location auto-sets
#   ``_pending_confirmation``, gating demographic-skip off). Direct unit
#   tests in ``test_chatbot_extracted_helpers.py::
#   TestHandleDemographicSkip`` cover the curly-apostrophe path.
# - ``contextual_acknowledgments._is_personal_story`` and friends: the
#   ``_combined_contextual_acknowledgments`` entry point isn't currently
#   wired into the orchestrator's response chain (the docstring claims
#   it is, but no callers exist outside the module — track as a
#   separate finding).
#
# When new handlers that normalize apostrophes are added, this list is
# the place to add a fuzz scenario for them. Rule of thumb: if your
# handler does ``normalize_apostrophes(message)`` and is reachable via
# ``send()``, add a row here.
APOSTROPHE_SCENARIOS = [
    (
        "location_unknown_dont_know",
        "I don't know",
        _assert_location_unknown_offers_picker,
        # Precondition: service_type set, location NOT set.
        lambda sid: send("I need food", session_id=sid),
    ),
    (
        "location_unknown_im_not_sure",
        "I'm not sure",
        _assert_location_unknown_offers_picker,
        lambda sid: send("I need food", session_id=sid),
    ),
    (
        "location_unknown_dont_know_shelter_path",
        # Same dispatch, different service_type — guards against a
        # service-type-specific regression in the location-unknown path.
        "I don't know",
        _assert_location_unknown_offers_picker,
        lambda sid: send("I need shelter", session_id=sid),
    ),
]


# ---------------------------------------------------------------------------
# Substitution helpers
# ---------------------------------------------------------------------------

def _substitute_apostrophe(message: str, variant: str) -> str:
    """Replace every ASCII apostrophe in ``message`` with ``variant``.

    Using ``str.replace`` rather than regex so any character substitution
    works — even non-printable ones we might add to the variants tuple
    in the future.
    """
    return message.replace("'", variant)


def _variant_label(variant: str) -> str:
    """Return a human-readable label for the variant used in pytest IDs.

    Without this, parametrize IDs would show the literal Unicode char
    which is hard to copy-paste in error messages.
    """
    code = f"U+{ord(variant):04X}"
    return code


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label,straight_message,assertion,setup",
    APOSTROPHE_SCENARIOS,
    ids=[s[0] for s in APOSTROPHE_SCENARIOS],
)
@pytest.mark.parametrize(
    "variant",
    NON_STANDARD_APOSTROPHES,
    ids=[_variant_label(v) for v in NON_STANDARD_APOSTROPHES],
)
def test_apostrophe_variant_routes_identically(
    label, straight_message, assertion, setup, variant, fresh_session,
):
    """For each scenario, every non-standard apostrophe variant must
    route to the same dispatch path as the straight-apostrophe input.

    A failure here means the dispatch path that handled the straight
    input forgot to call ``normalize_apostrophes`` on its raw message
    input. Find that path and add the call (see ``_handle_demographic_skip``
    for the pattern).
    """
    # 1. Set up session preconditions
    if setup is not None:
        setup(fresh_session)

    # 2. Send the variant message
    fuzzed = _substitute_apostrophe(straight_message, variant)
    # Sanity: confirm we actually injected the variant
    assert variant in fuzzed, (
        f"Test setup error: variant {_variant_label(variant)} not in "
        f"fuzzed message {fuzzed!r}"
    )
    assert "'" not in fuzzed, (
        f"Test setup error: ASCII apostrophe still present in "
        f"fuzzed message {fuzzed!r}"
    )

    result = send(fuzzed, session_id=fresh_session)

    # 3. Assert dispatch behavior matches expected
    assertion(result, _variant_label(variant))


def test_straight_apostrophe_baseline(fresh_session):
    """Sanity test: the straight-apostrophe versions of each scenario
    pass their assertions. If this fails, the assertion callables
    themselves are wrong — the variant tests above would all be
    broken too, and this gives a clearer signal."""
    sid = fresh_session
    for label, straight_message, assertion, setup in APOSTROPHE_SCENARIOS:
        from app.services.session_store import clear_session
        clear_session(sid)
        if setup is not None:
            setup(sid)
        result = send(straight_message, session_id=sid)
        try:
            assertion(result, "straight (baseline)")
        except AssertionError as e:
            raise AssertionError(
                f"Baseline scenario {label!r} failed its own assertion. "
                f"Fix the assertion before fuzzing variants. {e}"
            ) from e
