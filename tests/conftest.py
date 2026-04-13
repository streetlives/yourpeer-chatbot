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
To run one file:     pytest tests/test_chatbot.py
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


# ---------------------------------------------------------------------------
# MOCK SERVICE DATA
# ---------------------------------------------------------------------------
# Shared across test_chatbot.py, test_chat_route.py, and any new test
# files that need a realistic query_services() return value.
# Update this in ONE place when the service card schema changes.


MOCK_SERVICE_CARD = {
    "service_name": "Test Food Pantry",
    "organization": "Test Org",
    "description": "Free meals every weekday",
    "address": "123 Test St, Brooklyn, NY 11201",
    "city": "Brooklyn",
    "phone": "212-555-0001",
    "email": "info@testpantry.org",
    "website": "https://testpantry.org",
    "fees": "Free",
    "yourpeer_url": "https://yourpeer.nyc/locations/test-food-pantry",
    "hours_today": "9:00 AM – 5:00 PM",
    "is_open": "open",
    "service_id": "svc-001",
    "requires_membership": False,
    "last_validated_at": "2026-04-01T10:00:00",
    "also_available": ["Shower", "Clothing Pantry"],
    # Optional fields added to ServiceCard Pydantic model
    "accessibility": None,
    "eligibility_summary": None,
    "review_highlight": None,
    "required_documents": None,
    "languages": None,
}

MOCK_QUERY_RESULTS = {
    "services": [MOCK_SERVICE_CARD],
    "result_count": 1,
    "template_used": "FoodQuery",
    "params_applied": {"taxonomy_name": "Food", "city": "Brooklyn"},
    "relaxed": False,
    "execution_ms": 50,
    "freshness": {"fresh": 1, "total": 1, "total_with_date": 1},
}

MOCK_EMPTY_RESULTS = {
    "services": [],
    "result_count": 0,
    "template_used": "FoodQuery",
    "params_applied": {},
    "relaxed": False,
    "execution_ms": 10,
}

MOCK_RELAXED_RESULTS = {
    "services": [MOCK_SERVICE_CARD],
    "result_count": 1,
    "template_used": "FoodQuery",
    "params_applied": {"taxonomy_name": "Food"},
    "relaxed": True,
    "execution_ms": 75,
}


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

    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=mock_query_return), \
         patch("app.services.chatbot.detect_crisis", return_value=mock_crisis_return):
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
    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=mock_query_return), \
         patch("app.services.chatbot.detect_crisis", return_value=mock_crisis_return):
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
    from app.services.classifier import _classify_message
    actual = _classify_message(message)
    assert actual == expected_category, \
        f"Expected '{message}' → '{expected_category}', got '{actual}'"

