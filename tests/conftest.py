# Copyright (c) 2024 Streetlives, Inc.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""
Shared test fixtures for the YourPeer chatbot test suite.

This file is automatically loaded by pytest before any test module.
It provides:
    - sys.path setup (so `from app.services.chatbot import ...` works)
    - Shared mock data fixtures (MOCK_QUERY_RESULTS, etc.)
    - Session management helpers

To run all tests:    pytest
To run one file:     pytest tests/integration/test_classification_and_routing.py
To run with print:   pytest -s
"""

import sys
import os
import pytest

# ---------------------------------------------------------------------------
# COLLECTION CONFIGURATION
# ---------------------------------------------------------------------------
# eval_llm_judge.py is not a unit test — it's an evaluation runner invoked
# separately via the admin panel or CLI. Exclude from pytest collection.

collect_ignore = ["eval_llm_judge.py"]


# ---------------------------------------------------------------------------
# PATH SETUP
# ---------------------------------------------------------------------------
# pyproject.toml sets pythonpath = ["backend"], but this ensures
# compatibility when running individual test files directly too.

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Make conftest itself importable as a regular module.
# Many test files use `from conftest import send, send_multi` instead of
# pytest fixtures. This works only when tests/ is on sys.path, which is
# not guaranteed when running `pytest` from the project root.
sys.path.insert(0, os.path.dirname(__file__))

# Make intra-directory test helpers importable. Several unit-test files
# use `from test_helpers import _fresh, _send, _build_shelter_results`
# (referring to tests/unit/test_helpers.py) and similar patterns in
# tests/integration/. These imports only resolve when the directory
# containing the helper module is on sys.path directly.
for _sub in ("unit", "integration"):
    _path = os.path.join(os.path.dirname(__file__), _sub)
    if os.path.isdir(_path):
        sys.path.insert(0, _path)


# ---------------------------------------------------------------------------
# MOCK SERVICE DATA
# ---------------------------------------------------------------------------
# Shared across integration tests (test_classification_and_routing.py,
# test_http_routes_and_models.py, test_browser_geolocation.py,
# test_service_data_llm_firewall.py, test_crisis_and_flow_regressions.py)
# and the eval harness (tests/eval/eval_llm_judge.py — which has its own
# duplicate of MOCK_QUERY_RESULTS that should use build_mock_query_results
# via side_effect after this change lands).
#
# Prior to 2026-04, this file exposed a single hardcoded MOCK_QUERY_RESULTS
# dict (one Brooklyn food pantry) that was used regardless of what the
# caller queried. That caused two problems:
#   1. An eval scenario where the user asks for shelter in Manhattan would
#      get a food pantry in Brooklyn back, contaminating the Opus judge's
#      scoring of search-quality dimensions.
#   2. Multi-service scenarios couldn't distinguish "searched for shelter"
#      from "searched for food" because both returned the same cards.
#
# The new API keeps the old constant (MOCK_QUERY_RESULTS) for
# backward compatibility in integration tests that don't care about
# service-type fidelity, and adds build_mock_query_results(...) for tests
# that DO care — especially the eval harness which should use
# `side_effect=lambda **kwargs: build_mock_query_results(**kwargs)` so
# the mock responds to the actual query_services() arguments at each call.
#
# Update SERVICE_CARD_TEMPLATES in ONE place when the service card schema
# changes. MOCK_SERVICE_CARD is kept as an alias for backward-compat with
# tests that import it directly; it points at the Brooklyn food pantry.


# Field defaults shared across all card templates — override per-template only where they differ.
_DEFAULT_CARD_FIELDS = {
    "organization": "Test Org",
    "fees": "Free",
    "is_open": "open",
    "requires_membership": False,
    "last_validated_at": "2026-04-01T10:00:00",
    # Optional fields added to the ServiceCard Pydantic model
    "accessibility": None,
    "eligibility_summary": None,
    "review_highlight": None,
    "required_documents": None,
    "languages": None,
}

# Per-service-type card templates. Fields here override _DEFAULT_CARD_FIELDS.
# Each template is applied against the user-supplied location (when present)
# so the returned card plausibly matches the query.
_SERVICE_CARD_TEMPLATES = {
    "food": {
        "service_name": "Test Food Pantry",
        "description": "Free meals every weekday",
        "phone": "212-555-0001",
        "email": "info@testpantry.org",
        "website": "https://testpantry.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-food-pantry",
        "hours_today": "9:00 AM – 5:00 PM",
        "service_id": "svc-food-001",
        "also_available": ["Shower", "Clothing Pantry"],
        "service_taxonomies": ["Food", "Soup Kitchen", "Pantry"],
    },
    "shelter": {
        "service_name": "Test Shelter",
        "description": "Emergency shelter with 24/7 intake",
        "phone": "212-555-0002",
        "email": "intake@testshelter.org",
        "website": "https://testshelter.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-shelter",
        "hours_today": "24 hours",
        "service_id": "svc-shelter-001",
        "also_available": ["Case Management", "Meals"],
        "service_taxonomies": ["Shelter", "Emergency Housing"],
    },
    "medical": {
        "service_name": "Test Community Clinic",
        "description": "Walk-in medical care, no insurance required",
        "phone": "212-555-0003",
        "email": "info@testclinic.org",
        "website": "https://testclinic.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-clinic",
        "hours_today": "9:00 AM – 6:00 PM",
        "service_id": "svc-medical-001",
        "also_available": ["Dental", "Vision"],
        "service_taxonomies": ["Medical", "Clinic", "Primary Care"],
    },
    "mental_health": {
        "service_name": "Test Counseling Center",
        "description": "Counseling, therapy, and substance use support",
        "phone": "212-555-0004",
        "email": "help@testcounseling.org",
        "website": "https://testcounseling.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-counseling",
        "hours_today": "9:00 AM – 8:00 PM",
        "service_id": "svc-mh-001",
        "also_available": ["Support Groups"],
        "service_taxonomies": ["Mental Health", "Counseling", "Substance Use"],
    },
    "clothing": {
        "service_name": "Test Clothing Pantry",
        "description": "Free clothing, coats, and seasonal gear",
        "phone": "212-555-0005",
        "email": "info@testclothing.org",
        "website": "https://testclothing.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-clothing",
        "hours_today": "10:00 AM – 4:00 PM",
        "service_id": "svc-clothing-001",
        "also_available": ["Hygiene Kits"],
        "service_taxonomies": ["Clothing", "Clothing Pantry"],
    },
    "personal_care": {
        "service_name": "Test Drop-in Center",
        "description": "Showers, laundry, and personal care supplies",
        "phone": "212-555-0006",
        "email": "info@testdropin.org",
        "website": "https://testdropin.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-dropin",
        "hours_today": "8:00 AM – 5:00 PM",
        "service_id": "svc-pc-001",
        "also_available": ["Meals", "Mail Service"],
        "service_taxonomies": ["Personal Care", "Drop-in Center", "Shower"],
    },
    "legal": {
        "service_name": "Test Legal Aid",
        "description": "Free legal help for housing, immigration, and benefits",
        "phone": "212-555-0007",
        "email": "help@testlegal.org",
        "website": "https://testlegal.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-legal",
        "hours_today": "9:00 AM – 5:00 PM",
        "service_id": "svc-legal-001",
        "also_available": ["Translation"],
        "service_taxonomies": ["Legal", "Legal Aid"],
    },
    "employment": {
        "service_name": "Test Jobs Center",
        "description": "Job placement, resume help, and workforce training",
        "phone": "212-555-0008",
        "email": "jobs@testemployment.org",
        "website": "https://testemployment.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-employment",
        "hours_today": "9:00 AM – 5:00 PM",
        "service_id": "svc-emp-001",
        "also_available": ["Computer Access"],
        "service_taxonomies": ["Employment", "Workforce Development"],
    },
    "other": {
        "service_name": "Test Benefits Office",
        "description": "Help enrolling in SNAP, Medicaid, and public benefits",
        "phone": "212-555-0009",
        "email": "info@testbenefits.org",
        "website": "https://testbenefits.org",
        "yourpeer_url": "https://yourpeer.nyc/locations/test-benefits",
        "hours_today": "9:00 AM – 4:00 PM",
        "service_id": "svc-other-001",
        "also_available": ["IDs", "Phone Access"],
        "service_taxonomies": ["Other", "Benefits Enrollment"],
    },
}

# Per-borough coordinate centroids (approximate) — used so a Manhattan card
# gets Manhattan coordinates, a Bronx card gets Bronx coordinates, etc.
# Makes distance-based downstream logic behave plausibly in tests.
_BOROUGH_COORDS = {
    "brooklyn":        (40.6937, -73.9946),
    "manhattan":       (40.7831, -73.9712),
    "bronx":           (40.8448, -73.8648),
    "queens":          (40.7282, -73.7949),
    "staten island":   (40.5795, -74.1502),
}

# Rough ZIP mapping for the `address` field. Unknown locations fall
# back to Brooklyn's ZIP to preserve historical test assertions.
_BOROUGH_ZIPS = {
    "brooklyn": "11201",
    "manhattan": "10001",
    "bronx": "10451",
    "queens": "11101",
    "staten island": "10301",
}


def build_mock_query_results(
    service_type: str = None,
    location: str = None,
    *,
    result_count: int = 1,
    relaxed: bool = False,
    **_unused_kwargs,
) -> dict:
    """Build a query_services() return value that plausibly matches the query.

    Args:
        service_type: Canonical service type (food, shelter, medical, ...).
            If unknown or None, falls back to the food template so tests
            that don't care about service-type fidelity keep working.
        location:     NYC borough or neighborhood string. Used to build
            plausible coordinates and addresses for the returned cards.
            If None, defaults to Brooklyn (legacy behavior).
        result_count: How many copies of the template card to include.
            Defaults to 1. Tests that need the "multiple results" path
            (e.g. pagination, relaxed mode) can pass a higher number.
        relaxed:      Whether to mark the result as relaxed-query. Used
            by tests that exercise the relaxed-query fallback path.
        **_unused_kwargs: Silently accept and ignore any other kwargs
            that query_services accepts (age, gender, weekday, etc.).
            Mocks shouldn't fail on future signature additions.

    Returns: a dict matching query_services()'s return contract.
    """
    # Pick template — unknown service types fall back to food
    template_key = service_type.lower() if isinstance(service_type, str) else "food"
    if template_key not in _SERVICE_CARD_TEMPLATES:
        template_key = "food"
    template = _SERVICE_CARD_TEMPLATES[template_key]

    # Resolve location → coords + address
    loc_lower = location.lower().strip() if isinstance(location, str) else "brooklyn"
    lat, lng = _BOROUGH_COORDS.get(loc_lower, _BOROUGH_COORDS["brooklyn"])
    zip_code = _BOROUGH_ZIPS.get(loc_lower, _BOROUGH_ZIPS["brooklyn"])
    display_loc = location.title() if isinstance(location, str) else "Brooklyn"

    card = {
        **_DEFAULT_CARD_FIELDS,
        **template,
        "address": f"123 Test St, {display_loc}, NY {zip_code}",
        "city": display_loc,
        "latitude": lat,
        "longitude": lng,
    }

    services = [dict(card) for _ in range(max(1, result_count))]

    return {
        "services": services,
        "result_count": len(services),
        "template_used": f"{template_key.capitalize()}Query",
        "params_applied": {
            "taxonomy_name": template["service_taxonomies"][0],
            "city": display_loc,
        },
        "relaxed": relaxed,
        "execution_ms": 50,
        "freshness": {
            "fresh": len(services),
            "total": len(services),
            "total_with_date": len(services),
        },
    }


# Backward-compat constants — preserved for the many existing tests that
# import these directly. Both resolve to the "food in Brooklyn" default
# which matches the pre-2026-04 fixture behavior.
MOCK_SERVICE_CARD = build_mock_query_results(
    service_type="food", location="Brooklyn"
)["services"][0]

MOCK_QUERY_RESULTS = build_mock_query_results(
    service_type="food", location="Brooklyn"
)

MOCK_EMPTY_RESULTS = {
    "services": [],
    "result_count": 0,
    "template_used": "FoodQuery",
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 10,
}

MOCK_RELAXED_RESULTS = build_mock_query_results(
    service_type="food", location="Brooklyn", relaxed=True,
)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset rate limiter state before each test so tests are isolated."""
    from app.services.rate_limiter import clear
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _reset_claude_client_cache():
    """Reset the lazy-initialized Anthropic client cache between tests.

    ``app.llm.claude_client`` lazily caches the SDK client in a module-
    level ``_client`` global so production calls don't reinitialize on
    every request. Tests that use ``@patch("app.llm.claude_client.anthropic")``
    (e.g. ``tests/unit/test_claude_client.py``) install a MagicMock as
    ``cc.anthropic.Anthropic`` and then call ``get_client()``, which
    populates ``cc._client`` with a MagicMock instance.

    Without this fixture, that MagicMock-shaped client lingers in the
    module global after the test completes. Subsequent tests in the
    same pytest process — even ones that don't patch anthropic at all —
    then get the stale MagicMock back from ``get_client()`` because the
    early-return at the top of the function short-circuits the real
    initialization. The leaked client returns MagicMock-shaped responses
    from ``client.messages.create()``, and downstream string operations
    (e.g. ``response += "..."`` in ``handlers/general.py``) produce a
    MagicMock-typed ``result["response"]`` that fails string-only
    consumers (``redact_pii``, ``len(result["response"])``, etc.).

    This was observed in CI on PR #74 where
    ``test_llm_returns_none`` failed with a MagicMock-typed response;
    a prior test in the same process had patched anthropic and left the
    cached client behind. Local runs without ``ANTHROPIC_API_KEY`` set
    didn't reproduce because ``get_client()`` raised on the missing key
    before reaching the cache-check, so the leak was invisible.

    Same shape as ``_clear_rate_limits`` — clear before AND after each
    test to be robust against tests that are themselves the leak source.
    """
    import app.llm.claude_client as cc
    cc._client = None
    cc._init_error = None
    yield
    cc._client = None
    cc._init_error = None


@pytest.fixture
def mock_service_card():
    """A single realistic service card dict."""
    return dict(MOCK_SERVICE_CARD)


@pytest.fixture
def mock_query_results():
    """A query_services() return value with one result."""
    return dict(MOCK_QUERY_RESULTS)


@pytest.fixture
def mock_empty_results():
    """A query_services() return value with zero results."""
    return dict(MOCK_EMPTY_RESULTS)


@pytest.fixture
def mock_query_builder():
    """The service-type-aware mock builder, for tests that want
    query-sensitive mocks.

    Usage:
        def test_shelter_in_manhattan(mock_query_builder):
            with patch(
                "app.services.chatbot.execution.query_services",
                side_effect=lambda **kwargs: mock_query_builder(**kwargs),
            ):
                result = generate_reply(
                    "I need shelter in Manhattan", session_id=sid,
                )
                # The bot's query_services call receives
                # service_type="shelter", location="Manhattan" and the mock
                # returns a shelter card with Manhattan address + coords.

    The eval harness (tests/eval/eval_llm_judge.py) should use the same
    pattern: replace `return_value=MOCK_QUERY_RESULTS` with
    `side_effect=lambda **kwargs: build_mock_query_results(**kwargs)`
    so scoring isn't contaminated by service-type-mismatched mock data.
    """
    return build_mock_query_results


@pytest.fixture
def fresh_session():
    """Provide a unique session ID and clean it up after the test.

    Usage:
        def test_something(fresh_session):
            result = generate_reply("hi", session_id=fresh_session)
            assert result["response"]
    """
    import uuid
    from app.services.session_store import clear_session

    sid = str(uuid.uuid4())
    clear_session(sid)
    yield sid
    clear_session(sid)


# ---------------------------------------------------------------------------
# SHARED HELPERS — importable by test files
# ---------------------------------------------------------------------------
# These are plain functions, not fixtures. Import them directly:
#
#   from conftest import send, send_multi, assert_classified
#

def send(message, session_id=None, mock_query_return=None, latitude=None, longitude=None, mock_crisis_return=None):
    """Send a single message through generate_reply with mocked externals.

    Patches claude_reply and query_services so tests don't need real
    API keys or a database. Returns the full response dict.

    Args:
        message: The user message to send.
        session_id: Session ID (auto-generated if None).
        mock_query_return: What query_services should return.
            Defaults to MOCK_QUERY_RESULTS.
        latitude: Optional browser geolocation latitude.
        longitude: Optional browser geolocation longitude.
        mock_crisis_return: What detect_crisis should return.
            Defaults to None (no crisis). Pass a (category, response)
            tuple to simulate crisis detection.

    Usage:
        from conftest import send, MOCK_QUERY_RESULTS
        result = send("I need food in Brooklyn")
        assert result["slots"]["service_type"] == "food"

        # Simulate crisis:
        result = send("I want to hurt myself",
                      mock_crisis_return=("suicide_self_harm", "Call 988."))
    """
    from unittest.mock import patch
    from app.services.chatbot import generate_reply
    from app.services.session_store import clear_session

    if mock_query_return is None:
        mock_query_return = MOCK_QUERY_RESULTS

    if session_id is None:
        import uuid
        session_id = f"test-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)

    # Phase 3: patch paths target the specific submodule that holds the
    # binding (not the top-level package). Each submodule that did
    # `from X import Y` at load time gets its own binding, and
    # patch.object on the source module doesn't propagate. So we patch
    # every bind site that's reachable from the dispatch path:
    #   * handlers.meta.claude_reply — primary LLM call in service flow
    #   * execution.query_services   — primary DB call in _execute_and_respond
    #   * orchestrator.detect_crisis — the dispatcher's crisis check
    #   * classifier.detect_crisis   — the classifier's crisis check
    #                                  (reachable via _classify_tone when
    #                                  orchestrator passes crisis_result=
    #                                  _CRISIS_NOT_CHECKED, or when tests
    #                                  call _classify_message directly)
    # Without the classifier patch, tests with a non-working
    # ANTHROPIC_API_KEY get the real LLM crisis detector, which fails-
    # open to a crisis result and hijacks the classification outcome.
    with patch("app.services.chatbot.handlers.meta.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.execution.query_services", return_value=mock_query_return), \
         patch("app.services.chatbot.orchestrator.detect_crisis", return_value=mock_crisis_return), \
         patch("app.services.classifier.detect_crisis", return_value=mock_crisis_return):
        return generate_reply(message, session_id=session_id, latitude=latitude, longitude=longitude)


def send_multi(messages, session_id=None, mock_query_return=None, latitude=None, longitude=None, mock_crisis_return=None):
    """Send multiple messages in sequence within the same session.

    Returns a list of response dicts, one per message.

    Args:
        messages: List of user message strings or tuples of (message, kwargs).
        session_id: Session ID (auto-generated if None).
        mock_query_return: What query_services should return.
        latitude: Optional browser geolocation latitude (applied to all messages).
        longitude: Optional browser geolocation longitude (applied to all messages).
        mock_crisis_return: What detect_crisis should return.
            Defaults to None (no crisis).

    Usage:
        from conftest import send_multi
        results = send_multi(["I need food", "Brooklyn", "Yes, search"])
        assert results[-1]["result_count"] >= 1
    """
    from unittest.mock import patch
    from app.services.chatbot import generate_reply
    from app.services.session_store import clear_session

    if mock_query_return is None:
        mock_query_return = MOCK_QUERY_RESULTS

    if session_id is None:
        import uuid
        session_id = f"test-{uuid.uuid4().hex[:8]}"
        clear_session(session_id)

    results = []
    # Same bind-site strategy as send(). See comment there for rationale.
    with patch("app.services.chatbot.handlers.meta.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.execution.query_services", return_value=mock_query_return), \
         patch("app.services.chatbot.orchestrator.detect_crisis", return_value=mock_crisis_return), \
         patch("app.services.classifier.detect_crisis", return_value=mock_crisis_return):
        for msg in messages:
            results.append(generate_reply(msg, session_id=session_id, latitude=latitude, longitude=longitude))
    return results


def assert_classified(message, expected_category):
    """Assert that a message is classified into the expected category.

    Usage:
        from conftest import assert_classified
        assert_classified("start over", "reset")
        assert_classified("hi", "greeting")
    """
    from unittest.mock import patch
    from app.services.classifier import _classify_message
    # _classify_message → _classify_tone → classifier.detect_crisis.
    # If a non-working ANTHROPIC_API_KEY is set, the real detector's
    # LLM fallback fires, 401s, and fails open to a crisis result —
    # which hijacks classification and makes "I need food" classify
    # as "crisis". Patch the binding so tests assert the REGEX
    # classifier's behavior, not the LLM detector's fallback.
    with patch("app.services.classifier.detect_crisis", return_value=None):
        actual = _classify_message(message)
    assert actual == expected_category, \
        f"Expected '{message}' → '{expected_category}', got '{actual}'"


# ---------------------------------------------------------------------------
# MessageContext builder for handler unit tests
# ---------------------------------------------------------------------------
# Background: Phase A-C migrated handlers to take a single ``ctx`` arg
# instead of 6+ positional args. Constructing a MessageContext for a
# handler unit test by hand is 18 lines of dataclass kwargs, most with
# safe-default values. This helper centralizes the defaults so tests
# only specify what they care about.
#
# Tracks audit item D-2 from PHASE_AC_AFTERMATH.md.
#
# Example usage:
#   ctx = make_ctx(message="I'd rather not say",
#                  existing={"service_type": "food", "location": "brooklyn"})
#   result = _handle_demographic_skip(ctx)
#
# Snapshot args (last_action, pending, response_tone) are NOT on ctx in
# the current architecture — they're positional snapshots passed by the
# orchestrator after capturing them from existing. Tests that need to
# pin snapshot-arg semantics should construct ctx with whatever existing
# state is convenient and pass the snapshot value separately. (Audit
# item D-5 will eventually move snapshots onto ctx as accessor methods.)

def make_ctx(
    *,
    message: str = "",
    redacted_message: str | None = None,
    session_id: str = "test-session",
    request_id: str = "test-request",
    pii_warning: str = "",
    existing: dict | None = None,
    category: str = "general",
    action: str = "",
    tone: str | None = None,
    confidence: str = "high",
    confidence_reason: str = "regex_match",
    extraction_source: str | None = None,
    early_extracted: dict | None = None,
    has_service_intent: bool = False,
    crisis_result: tuple | None = None,
    last_results: list | None = None,
    is_confirmation_action: bool | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    spanish_acknowledgment: str = "",
    tone_prefix: str = "",
    merged: dict | None = None,
    unified_extraction: dict | None = None,
    snapshot_last_action: str | None = None,
    snapshot_pending: bool | None = None,
    snapshot_response_tone: str | None = None,
):
    """Construct a MessageContext with sensible defaults for unit tests.

    Every field has a default. Pass keyword overrides for the fields
    your test cares about. ``redacted_message`` defaults to ``message``
    (matching the most common case where there's nothing to redact).

    ``is_confirmation_action`` is auto-derived from ``action`` using the
    same membership check the orchestrator uses, so a test that sets
    ``action="confirm_yes"`` automatically gets the consistent
    ``is_confirmation_action=True``. Pass an explicit bool to override
    the derivation (rare — useful only for tests that want to exercise
    inconsistent ctx states).

    ``existing`` and ``early_extracted`` default to fresh dicts to avoid
    accidental sharing between tests. Don't change this default to a
    module-level constant.
    """
    from app.services.chatbot.context import MessageContext

    if is_confirmation_action is None:
        is_confirmation_action = action in (
            "confirm_yes", "confirm_deny", "confirm_change_service",
            "confirm_change_location", "reset", "greeting",
        )

    return MessageContext(
        session_id=session_id,
        request_id=request_id,
        message=message,
        redacted_message=message if redacted_message is None else redacted_message,
        pii_warning=pii_warning,
        existing=existing if existing is not None else {},
        category=category,
        action=action,
        tone=tone,
        confidence=confidence,
        confidence_reason=confidence_reason,
        extraction_source=extraction_source,
        early_extracted=early_extracted if early_extracted is not None else {},
        has_service_intent=has_service_intent,
        crisis_result=crisis_result,
        last_results=last_results,
        is_confirmation_action=is_confirmation_action,
        latitude=latitude,
        longitude=longitude,
        spanish_acknowledgment=spanish_acknowledgment,
        tone_prefix=tone_prefix,
        merged=merged,
        unified_extraction=unified_extraction,
        snapshot_last_action=snapshot_last_action,
        snapshot_pending=snapshot_pending,
        snapshot_response_tone=snapshot_response_tone,
    )

