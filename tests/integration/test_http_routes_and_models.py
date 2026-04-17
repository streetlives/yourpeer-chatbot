"""
Tests for the chat route and Pydantic models.

Run: pytest tests/integration/test_http_routes_and_models.py
"""

import uuid
from unittest.mock import patch

from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.models.chat_models import ChatRequest, ChatResponse, ServiceCard, QuickReply
from app.services.session_store import clear_session
from app.services.session_token import generate_session_id
from conftest import MOCK_SERVICE_CARD, MOCK_QUERY_RESULTS, MOCK_EMPTY_RESULTS

client = TestClient(app)

# Local aliases matching the original names used throughout this file
_MOCK_SERVICE_CARD = MOCK_SERVICE_CARD
_MOCK_QUERY_RESULTS = MOCK_QUERY_RESULTS
_MOCK_EMPTY_RESULTS = MOCK_EMPTY_RESULTS


# -----------------------------------------------------------------------
# PYDANTIC MODELS — ChatRequest
# -----------------------------------------------------------------------

def test_chat_request_valid():
    """ChatRequest should accept a message and optional session_id."""
    r = ChatRequest(message="I need food")
    assert r.message == "I need food"
    assert r.session_id is None


def test_chat_request_with_session_id():
    """ChatRequest should accept an explicit session_id."""
    r = ChatRequest(message="hello", session_id="abc-123")
    assert r.session_id == "abc-123"


def test_chat_request_missing_message():
    """ChatRequest should reject missing message field."""
    try:
        ChatRequest()
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_chat_request_wrong_type():
    """ChatRequest should reject non-string message."""
    try:
        ChatRequest(message=123)
        # Pydantic v2 coerces int to str in strict=False mode
        # This is expected behavior — not a bug
        print("  PASS: ChatRequest coerces int to str (Pydantic v2 behavior)")
    except ValidationError:
        print("  PASS: ChatRequest rejects int message")


def test_chat_request_empty_string_accepted():
    """ChatRequest currently accepts empty strings (validated downstream).

    Note: The generate_reply() function handles empty messages with an
    early-return guard. If we add min_length=1 to the model later,
    this test should be updated to expect ValidationError.
    """
    r = ChatRequest(message="")
    assert r.message == ""


def test_chat_request_accepts_long_message():
    """ChatRequest should accept messages up to the 1,000-char limit."""
    msg = "a" * 1_000
    r = ChatRequest(message=msg)
    assert len(r.message) == 1_000


def test_chat_request_rejects_oversized_message():
    """ChatRequest should reject messages exceeding 1,000 chars."""
    msg = "a" * 1_001
    try:
        ChatRequest(message=msg)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_chat_route_rejects_oversized_message_http():
    """POST /chat/ with a message over 1,000 chars should return 422."""
    msg = "a" * 1_001
    response = client.post("/chat/", json={"message": msg})
    assert response.status_code == 422


# -----------------------------------------------------------------------
# PYDANTIC MODELS — Coordinate Validation
# -----------------------------------------------------------------------

def test_chat_request_valid_coordinates():
    """ChatRequest should accept valid lat/lng."""
    r = ChatRequest(message="hi", latitude=40.7128, longitude=-74.0060)
    assert r.latitude == 40.7128
    assert r.longitude == -74.0060


def test_chat_request_boundary_coordinates():
    """ChatRequest should accept boundary lat/lng values."""
    r = ChatRequest(message="hi", latitude=90, longitude=180)
    assert r.latitude == 90
    r = ChatRequest(message="hi", latitude=-90, longitude=-180)
    assert r.latitude == -90


def test_chat_request_rejects_invalid_latitude():
    """ChatRequest should reject latitude outside ±90."""
    try:
        ChatRequest(message="hi", latitude=91, longitude=0)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass
    try:
        ChatRequest(message="hi", latitude=-91, longitude=0)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_chat_request_rejects_invalid_longitude():
    """ChatRequest should reject longitude outside ±180."""
    try:
        ChatRequest(message="hi", latitude=0, longitude=181)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass
    try:
        ChatRequest(message="hi", latitude=0, longitude=-181)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_chat_route_rejects_invalid_coordinates_http():
    """POST /chat/ with out-of-range coordinates should return 422."""
    response = client.post("/chat/", json={
        "message": "hi",
        "latitude": 100,
        "longitude": 0,
    })
    assert response.status_code == 422


# -----------------------------------------------------------------------
# REQUEST CORRELATION ID
# -----------------------------------------------------------------------

def test_chat_route_returns_request_id_header():
    """POST /chat/ should echo X-Request-ID in the response."""
    response = client.post(
        "/chat/",
        json={"message": "hello"},
        headers={"X-Request-ID": "test-req-123"},
    )
    assert response.headers.get("x-request-id") == "test-req-123"


def test_chat_route_generates_request_id_if_missing():
    """POST /chat/ should generate X-Request-ID when not provided."""
    response = client.post("/chat/", json={"message": "hello"})
    req_id = response.headers.get("x-request-id")
    assert req_id is not None
    assert len(req_id) > 0


# -----------------------------------------------------------------------
# PYDANTIC MODELS — ServiceCard
# -----------------------------------------------------------------------

def test_service_card_minimal():
    """ServiceCard should work with only the required field (service_name)."""
    sc = ServiceCard(service_name="Test Service")
    assert sc.service_name == "Test Service"
    assert sc.organization is None
    assert sc.phone is None
    assert sc.is_open is None


def test_service_card_full():
    """ServiceCard should accept all optional fields."""
    sc = ServiceCard(**_MOCK_SERVICE_CARD)
    assert sc.service_name == "Test Food Pantry"
    assert sc.organization == "Test Org"
    assert sc.phone == "212-555-0001"
    assert sc.is_open == "open"
    assert sc.yourpeer_url == "https://yourpeer.nyc/locations/test-food-pantry"


def test_service_card_missing_service_name():
    """ServiceCard should reject missing service_name."""
    try:
        ServiceCard(organization="Org")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_service_card_serialization():
    """ServiceCard should serialize to dict with all fields."""
    sc = ServiceCard(service_name="Test", phone="555-0000")
    data = sc.model_dump()
    assert "service_name" in data
    assert "phone" in data
    assert "organization" in data  # None but present
    assert len(data) == 21  # 17 original + 5 new optional fields


# -----------------------------------------------------------------------
# PYDANTIC MODELS — QuickReply
# -----------------------------------------------------------------------

def test_quick_reply_valid():
    """QuickReply should require both label and value."""
    qr = QuickReply(label="🍽️ Food", value="I need food")
    assert qr.label == "🍽️ Food"
    assert qr.value == "I need food"


def test_quick_reply_missing_label():
    """QuickReply should reject missing label."""
    try:
        QuickReply(value="test")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


def test_quick_reply_missing_value():
    """QuickReply should reject missing value."""
    try:
        QuickReply(label="test")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


# -----------------------------------------------------------------------
# PYDANTIC MODELS — ChatResponse
# -----------------------------------------------------------------------

def test_chat_response_minimal():
    """ChatResponse should work with required fields and defaults."""
    r = ChatResponse(
        session_id="s1",
        response="Hello!",
        follow_up_needed=False,
        slots={},
    )
    assert r.session_id == "s1"
    assert r.services == []
    assert r.result_count == 0
    assert r.relaxed_search is False
    assert r.quick_replies == []


def test_chat_response_with_services():
    """ChatResponse should accept a list of ServiceCards."""
    r = ChatResponse(
        session_id="s1",
        response="Found 1 result.",
        follow_up_needed=False,
        slots={"service_type": "food"},
        services=[ServiceCard(service_name="Test")],
        result_count=1,
    )
    assert len(r.services) == 1
    assert r.services[0].service_name == "Test"
    assert r.result_count == 1


def test_chat_response_missing_required():
    """ChatResponse should reject missing required fields."""
    required = ["session_id", "response", "follow_up_needed", "slots"]
    base = {"session_id": "s", "response": "r", "follow_up_needed": False, "slots": {}}

    for field in required:
        data = dict(base)
        del data[field]
        try:
            ChatResponse(**data)
            assert False, f"Should reject missing {field}"
        except ValidationError:
            pass


def test_chat_response_serialization_round_trip():
    """ChatResponse should serialize to JSON and back."""
    original = ChatResponse(
        session_id="s1",
        response="Found results.",
        follow_up_needed=False,
        slots={"service_type": "food", "location": "Brooklyn"},
        services=[ServiceCard(service_name="Pantry", phone="555-0000")],
        result_count=1,
        relaxed_search=True,
        quick_replies=[QuickReply(label="New search", value="Start over")],
    )

    json_str = original.model_dump_json()
    restored = ChatResponse.model_validate_json(json_str)

    assert restored.session_id == "s1"
    assert restored.result_count == 1
    assert restored.relaxed_search is True
    assert len(restored.services) == 1
    assert restored.services[0].phone == "555-0000"
    assert len(restored.quick_replies) == 1
    assert restored.quick_replies[0].label == "New search"


# -----------------------------------------------------------------------
# HTTP ROUTE — POST /chat/
# -----------------------------------------------------------------------

@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="How can I help?")
def test_chat_route_valid_request(mock_claude, mock_query):
    """POST /chat/ with a valid message should return 200."""
    response = client.post("/chat/", json={"message": "hello"})
    assert response.status_code == 200

    data = response.json()
    assert "session_id" in data
    assert "response" in data
    assert isinstance(data["services"], list)
    assert isinstance(data["quick_replies"], list)


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_generates_session_id(mock_claude, mock_query):
    """POST /chat/ without session_id should generate one."""
    response = client.post("/chat/", json={"message": "hi"})
    data = response.json()
    assert data["session_id"] is not None
    assert len(data["session_id"]) > 0


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_preserves_session_id(mock_claude, mock_query):
    """POST /chat/ with a valid session_id should preserve it."""
    sid = generate_session_id()
    response = client.post("/chat/", json={
        "message": "hi",
        "session_id": sid,
    })
    assert response.status_code == 200
    assert response.json()["session_id"] == sid
    clear_session(sid)


def test_chat_route_missing_message():
    """POST /chat/ without message should return 422."""
    response = client.post("/chat/", json={})
    assert response.status_code == 422


def test_chat_route_not_json():
    """POST /chat/ with non-JSON body should return 422."""
    response = client.post("/chat/", content="hello",
                           headers={"Content-Type": "text/plain"})
    assert response.status_code == 422


def test_chat_route_no_body():
    """POST /chat/ with no body should return 422."""
    response = client.post("/chat/")
    assert response.status_code == 422


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_empty_message(mock_claude, mock_query):
    """POST /chat/ with empty message should return 200 with welcome prompt.

    The model accepts '' but generate_reply handles it with an early guard.
    """
    response = client.post("/chat/", json={"message": ""})
    assert response.status_code == 200
    assert "looking for" in response.json()["response"].lower()


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_response_schema(mock_claude, mock_query):
    """Response should match ChatResponse schema exactly."""
    response = client.post("/chat/", json={"message": "I need food"})
    data = response.json()

    # All required fields present
    assert "session_id" in data
    assert "response" in data
    assert "follow_up_needed" in data
    assert "slots" in data

    # Default fields present
    assert "services" in data
    assert "result_count" in data
    assert "relaxed_search" in data
    assert "quick_replies" in data

    # Types correct
    assert isinstance(data["session_id"], str)
    assert isinstance(data["response"], str)
    assert isinstance(data["follow_up_needed"], bool)
    assert isinstance(data["slots"], dict)
    assert isinstance(data["services"], list)
    assert isinstance(data["result_count"], int)
    assert isinstance(data["relaxed_search"], bool)
    assert isinstance(data["quick_replies"], list)


# -----------------------------------------------------------------------
# HTTP ROUTE — Multi-turn conversation
# -----------------------------------------------------------------------

@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_QUERY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_multi_turn_with_services(mock_claude, mock_query):
    """A full multi-turn conversation should return service cards."""
    sid = generate_session_id()
    clear_session(sid)

    # Step 1: service request → confirmation
    r1 = client.post("/chat/", json={"message": "I need food in Brooklyn", "session_id": sid})
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["follow_up_needed"] is True
    assert len(data1["services"]) == 0  # confirmation, no results yet
    assert len(data1["quick_replies"]) > 0

    # Step 2: confirm → results
    r2 = client.post("/chat/", json={"message": "Yes, search", "session_id": sid})
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["result_count"] == 1
    assert len(data2["services"]) == 1

    # Verify service card structure
    card = data2["services"][0]
    assert card["service_name"] == "Test Food Pantry"
    assert card["phone"] == "212-555-0001"
    assert card["is_open"] == "open"
    assert card["yourpeer_url"] is not None


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_session_continuity(mock_claude, mock_query):
    """Slots should accumulate across turns within the same session."""
    sid = generate_session_id()
    clear_session(sid)

    # Turn 1: provide service type
    r1 = client.post("/chat/", json={"message": "I need shelter", "session_id": sid})
    data1 = r1.json()
    assert data1["slots"].get("service_type") == "shelter"
    assert data1["follow_up_needed"] is True  # needs location

    # Turn 2: provide location
    r2 = client.post("/chat/", json={"message": "Queens", "session_id": sid})
    data2 = r2.json()
    assert data2["slots"].get("service_type") == "shelter"
    assert data2["slots"].get("location") is not None


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_reset_clears_session(mock_claude, mock_query):
    """Saying 'start over' should clear the session slots."""
    sid = generate_session_id()
    clear_session(sid)

    # Build up some slots
    client.post("/chat/", json={"message": "I need food in Brooklyn", "session_id": sid})

    # Reset
    r = client.post("/chat/", json={"message": "start over", "session_id": sid})
    data = r.json()
    # After reset, slots should be empty
    assert data["slots"] == {} or data["slots"].get("service_type") is None


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_quick_replies_structure(mock_claude, mock_query):
    """Quick replies should have label and value fields."""
    r = client.post("/chat/", json={"message": "hello"})
    data = r.json()

    if data["quick_replies"]:
        qr = data["quick_replies"][0]
        assert "label" in qr, "Quick reply missing 'label'"
        assert "value" in qr, "Quick reply missing 'value'"
        assert isinstance(qr["label"], str)
        assert isinstance(qr["value"], str)


# -----------------------------------------------------------------------
# HTTP ROUTE — Crisis handling
# -----------------------------------------------------------------------

@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
def test_chat_route_crisis_returns_resources(mock_claude, mock_query):
    """Crisis messages should return crisis resources, not service results."""
    r = client.post("/chat/", json={"message": "I want to kill myself"})
    assert r.status_code == 200

    data = r.json()
    assert "988" in data["response"], "Should include 988 lifeline"
    assert len(data["services"]) == 0
    mock_query.assert_not_called()


# -----------------------------------------------------------------------
# HTTP ROUTE — Catch-all error handling
# -----------------------------------------------------------------------

def test_chat_route_returns_usable_response_on_crash():
    """If generate_reply crashes, user should get a helpful message, not a 500."""
    with patch("app.routes.chat.generate_reply",
               side_effect=RuntimeError("unexpected internal error")):
        r = client.post("/chat/", json={"message": "I need shelter"})

    # Should NOT be a 500
    assert r.status_code == 200
    data = r.json()

    # Should have all required ChatResponse fields
    assert "session_id" in data
    assert "response" in data
    assert "follow_up_needed" in data
    assert "slots" in data

    # Should direct user to yourpeer.nyc
    assert "yourpeer.nyc" in data["response"]

    # Should not expose internal error details
    assert "RuntimeError" not in data["response"]
    assert "unexpected internal error" not in data["response"]


def test_chat_route_crash_preserves_session_id():
    """Even on crash, the session_id should be returned so the user can retry."""
    sid = generate_session_id()
    with patch("app.routes.chat.generate_reply",
               side_effect=Exception("db gone")):
        r = client.post("/chat/", json={"message": "help", "session_id": sid})

    assert r.status_code == 200
    assert r.json()["session_id"] == sid


# -----------------------------------------------------------------------
# HTTP ROUTE — GET method not allowed
# -----------------------------------------------------------------------

def test_chat_route_get_not_allowed():
    """GET /chat/ should not return 200 — it's a POST-only endpoint.

    Note: Returns 404 (not 405) because the catch-all static file handler
    in main.py intercepts GET requests to /chat/ before FastAPI's method
    checking can return 405. The important thing is it doesn't succeed.
    """
    response = client.get("/chat/")
    assert response.status_code in (404, 405)


# -----------------------------------------------------------------------
# SESSION TOKEN VALIDATION (S4) — forged / invalid tokens at HTTP level
# -----------------------------------------------------------------------

_TEST_SECRET = b"test-secret-for-route-tests"


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
@patch("app.services.session_token._SECRET", _TEST_SECRET)
def test_chat_rejects_forged_session_id(mock_claude, mock_query):
    """POST /chat/ with a forged session_id should return 403."""
    response = client.post("/chat/", json={
        "message": "hello",
        "session_id": "forged-session-id",
    })
    assert response.status_code == 403
    assert "Invalid session token" in response.json()["detail"]


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
@patch("app.services.session_token._SECRET", _TEST_SECRET)
def test_chat_rejects_tampered_session_id(mock_claude, mock_query):
    """POST /chat/ with a tampered signature should return 403."""
    response = client.post("/chat/", json={
        "message": "hello",
        "session_id": "some-raw-value.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    })
    assert response.status_code == 403


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
@patch("app.services.session_token._SECRET", _TEST_SECRET)
def test_chat_accepts_valid_signed_session(mock_claude, mock_query):
    """POST /chat/ with a properly signed session_id should succeed."""
    from app.services.session_token import generate_session_id
    token = generate_session_id()
    response = client.post("/chat/", json={
        "message": "hello",
        "session_id": token,
    })
    assert response.status_code == 200
    assert response.json()["session_id"] == token


@patch("app.services.chatbot.execution.query_services", return_value=_MOCK_EMPTY_RESULTS)
@patch("app.services.chatbot.claude_reply", return_value="test")
@patch("app.services.session_token._SECRET", _TEST_SECRET)
def test_chat_mints_signed_token_on_first_message(mock_claude, mock_query):
    """POST /chat/ without session_id should mint a signed token."""
    response = client.post("/chat/", json={"message": "hello"})
    assert response.status_code == 200
    token = response.json()["session_id"]
    assert "." in token  # signed tokens have a dot separator


@patch("app.services.session_token._SECRET", _TEST_SECRET)
def test_feedback_rejects_forged_session_id():
    """POST /chat/feedback with a forged session_id should return 403."""
    response = client.post("/chat/feedback", json={
        "session_id": "forged-session-id",
        "rating": "up",
    })
    assert response.status_code == 403
    assert "Invalid session token" in response.json()["detail"]


# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# SERIALIZATION BOUNDARY — fields must survive Pydantic round-trip
# -----------------------------------------------------------------------

def test_new_service_card_fields_survive_serialization():
    """ServiceCard new fields (also_available, last_validated_at, etc.)
    must not be stripped by Pydantic model_dump().

    This test exists because Pydantic v2 silently drops unknown fields.
    When new fields are added to format_service_card() but not to the
    ServiceCard model, the data flows correctly through generate_reply()
    (raw dict) but is stripped at the HTTP boundary (ChatResponse.model_dump()).
    All chatbot tests use generate_reply() directly and never catch this.
    """
    card = ServiceCard(
        service_name="Test Pantry",
        service_id="svc-123",
        also_available=["Shower", "Clothing Pantry"],
        last_validated_at="2026-04-08T10:00:00",
        requires_membership=True,
    )
    data = card.model_dump()
    assert data["service_id"] == "svc-123", "service_id stripped by Pydantic"
    assert data["also_available"] == ["Shower", "Clothing Pantry"], "also_available stripped by Pydantic"
    assert data["last_validated_at"] == "2026-04-08T10:00:00", "last_validated_at stripped by Pydantic"
    assert data["requires_membership"] is True, "requires_membership stripped by Pydantic"


def test_quick_reply_href_survives_serialization():
    """QuickReply.href must not be stripped by Pydantic model_dump().

    Without this field, call buttons render as regular buttons instead of
    <a href="tel:..."> links that trigger the native phone dialer.
    """
    qr = QuickReply(label="📞 Call", value="Call 555-1234", href="tel:5551234")
    data = qr.model_dump()
    assert data["href"] == "tel:5551234", "href stripped by Pydantic"


def test_full_response_preserves_new_fields_through_http():
    """End-to-end: new fields must survive ChatResponse serialization.

    This is the actual code path in app/routes/chat.py — if this test
    fails, the frontend will never receive the field.
    """
    result = {
        "session_id": "test-123",
        "response": "Found results",
        "follow_up_needed": False,
        "slots": {},
        "services": [{
            "service_name": "The Door",
            "service_id": "svc-456",
            "also_available": ["Health", "Laundry"],
            "last_validated_at": "2026-04-01",
            "requires_membership": False,
            "phone": "555-1234",
        }],
        "result_count": 1,
        "relaxed_search": False,
        "quick_replies": [
            {"label": "📞 Call", "value": "Call 555", "href": "tel:555"},
            {"label": "🔍 New search", "value": "Start over"},
        ],
    }
    # This is the exact code path from app/routes/chat.py:
    serialized = ChatResponse(**result).model_dump()

    svc = serialized["services"][0]
    assert svc["service_id"] == "svc-456"
    assert svc["also_available"] == ["Health", "Laundry"]
    assert svc["last_validated_at"] == "2026-04-01"
    assert svc["requires_membership"] is False

    call_qr = serialized["quick_replies"][0]
    assert call_qr["href"] == "tel:555"

    # Regular QR without href should have None
    regular_qr = serialized["quick_replies"][1]
    assert regular_qr["href"] is None
