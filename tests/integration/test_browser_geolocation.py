"""
Tests for browser geolocation support in the chatbot.

Verifies that lat/lng coordinates from the browser Geolocation API
are correctly accepted, stored in session, and used for proximity
search when the user says "near me".

Run: pytest tests/test_geolocation.py
"""

from unittest.mock import patch

from app.models.chat_models import ChatRequest
from app.services.chatbot import generate_reply
from app.services.session_store import clear_session, get_session_slots
from app.services.slot_extractor import NEAR_ME_SENTINEL
from app.rag import query_services
from conftest import MOCK_QUERY_RESULTS, send, send_multi


# Coordinates for downtown Brooklyn (used across tests)
BK_LAT = 40.6892
BK_LNG = -73.9857


# -----------------------------------------------------------------------
# PYDANTIC MODEL
# -----------------------------------------------------------------------

def test_chat_request_accepts_coordinates():
    """ChatRequest should accept optional latitude/longitude."""
    r = ChatRequest(message="food near me", latitude=BK_LAT, longitude=BK_LNG)
    assert r.latitude == BK_LAT
    assert r.longitude == BK_LNG


def test_chat_request_coordinates_optional():
    """ChatRequest should work without coordinates."""
    r = ChatRequest(message="food near me")
    assert r.latitude is None
    assert r.longitude is None


# -----------------------------------------------------------------------
# SESSION STORAGE
# -----------------------------------------------------------------------

def test_coords_stored_in_session(fresh_session):
    """Coordinates should be stored in session slots when provided."""
    send("I need food", session_id=fresh_session, latitude=BK_LAT, longitude=BK_LNG)
    slots = get_session_slots(fresh_session)
    assert slots["_latitude"] == BK_LAT
    assert slots["_longitude"] == BK_LNG


def test_coords_not_stored_when_absent(fresh_session):
    """Session should not have coords when none provided."""
    send("I need food", session_id=fresh_session)
    slots = get_session_slots(fresh_session)
    assert "_latitude" not in slots
    assert "_longitude" not in slots


# -----------------------------------------------------------------------
# "NEAR ME" + COORDS → CONFIRMATION
# -----------------------------------------------------------------------

def test_near_me_with_coords_triggers_confirmation(fresh_session):
    """'food near me' + coords should go to confirmation, not ask for borough."""
    result = send(
        "I need food near me",
        session_id=fresh_session,
        latitude=BK_LAT,
        longitude=BK_LNG,
    )
    # Should show confirmation, not a follow-up asking for location
    assert "search" in result["response"].lower() or "near your location" in result["response"].lower()
    # Should have confirmation quick replies, not borough buttons
    qr_values = [q["value"] for q in result["quick_replies"]]
    assert "Yes, search" in qr_values


def test_near_me_without_coords_asks_for_location(fresh_session):
    """'food near me' without coords should ask for a borough."""
    result = send("I need food near me", session_id=fresh_session)
    assert result["slots"].get("location") == NEAR_ME_SENTINEL
    # Should show borough buttons + geolocation option
    qr_values = [q["value"] for q in result["quick_replies"]]
    assert "Manhattan" in qr_values
    assert "__use_geolocation__" in qr_values


def test_confirmation_message_says_near_your_location(fresh_session):
    """Confirmation should say 'near your location' not '__near_me__'."""
    result = send(
        "I need food near me",
        session_id=fresh_session,
        latitude=BK_LAT,
        longitude=BK_LNG,
    )
    assert "near your location" in result["response"]
    assert NEAR_ME_SENTINEL not in result["response"]


# -----------------------------------------------------------------------
# FULL FLOW: NEAR ME → CONFIRM → RESULTS
# -----------------------------------------------------------------------

def test_near_me_with_coords_full_flow(fresh_session):
    """Full flow: food near me + coords → confirmation → search → results."""
    with patch("app.services.chatbot.claude_reply", return_value="How can I help?"), \
         patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS) as mock_qs, \
         patch("app.services.chatbot.detect_crisis", return_value=None):

        # Step 1: "food near me" with coords → should go to confirmation
        r1 = generate_reply(
            "I need food near me",
            session_id=fresh_session,
            latitude=BK_LAT,
            longitude=BK_LNG,
        )
        assert "near your location" in r1["response"]

        # Step 2: Confirm → should execute query with coords
        r2 = generate_reply(
            "Yes, search",
            session_id=fresh_session,
            latitude=BK_LAT,
            longitude=BK_LNG,
        )
        assert r2["result_count"] >= 1

        # Verify query_services was called with lat/lng
        mock_qs.assert_called_once()
        call_kwargs = mock_qs.call_args
        assert call_kwargs.kwargs.get("latitude") == BK_LAT or call_kwargs[1].get("latitude") == BK_LAT


# -----------------------------------------------------------------------
# RAG: DIRECT COORDS
# -----------------------------------------------------------------------

def test_query_services_uses_direct_coords():
    """query_services should build proximity params from direct lat/lng."""
    with patch("app.rag.execute_service_query", return_value=MOCK_QUERY_RESULTS) as mock_exec:
        query_services(
            service_type="food",
            latitude=BK_LAT,
            longitude=BK_LNG,
        )
        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        user_params = call_kwargs.kwargs.get("user_params") or call_kwargs[1]["user_params"]
        assert user_params["lat"] == BK_LAT
        assert user_params["lon"] == BK_LNG
        assert "radius_meters" in user_params


def test_query_services_coords_override_location():
    """When both coords and location are provided, coords should take precedence."""
    with patch("app.rag.execute_service_query", return_value=MOCK_QUERY_RESULTS) as mock_exec:
        query_services(
            service_type="food",
            location="Manhattan",
            latitude=BK_LAT,
            longitude=BK_LNG,
        )
        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        user_params = call_kwargs.kwargs.get("user_params") or call_kwargs[1]["user_params"]
        # Should use coords, not borough filter
        assert user_params["lat"] == BK_LAT
        assert "borough" not in user_params


# -----------------------------------------------------------------------
# COORDS PERSIST ACROSS TURNS
# -----------------------------------------------------------------------

def test_coords_persist_across_turns(fresh_session):
    """Coords stored in session should persist for subsequent messages."""
    # First message stores coords
    send("I need food", session_id=fresh_session, latitude=BK_LAT, longitude=BK_LNG)

    # Second message says "near me" — should use stored coords
    result = send("near me", session_id=fresh_session, latitude=BK_LAT, longitude=BK_LNG)
    slots = get_session_slots(fresh_session)
    assert slots.get("_latitude") == BK_LAT
    assert slots.get("_longitude") == BK_LNG
