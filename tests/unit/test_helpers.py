"""
Shared helpers for results/refinement/crisis test suites.

Provides:
  _fresh()                — create an isolated session
  _send()                 — send a message with mocked DB/LLM/crisis
  _build_shelter_results() — generate N mock shelter service cards
"""

import uuid
from unittest.mock import patch

from app.services.chatbot import generate_reply, _DISPLAY_PAGE_SIZE
from app.services.slot_extractor import NEAR_ME_SENTINEL
from app.services.session_store import (
    clear_session, get_session_slots, save_session_slots,
)
from conftest import MOCK_QUERY_RESULTS


def _fresh():
    sid = f"test-dvfix-{uuid.uuid4().hex[:8]}"
    clear_session(sid)
    return sid


def _send(msg, sid, mock_crisis=None, mock_query=None, latitude=None, longitude=None):
    with (
        patch("app.services.chatbot.execution.query_services",
              return_value=mock_query or MOCK_QUERY_RESULTS),
        patch("app.services.chatbot.handlers.meta.claude_reply",
              return_value="How can I help?"),
        patch("app.services.chatbot.orchestrator.detect_crisis",
              return_value=mock_crisis),
    ):
        return generate_reply(msg, session_id=sid,
                              latitude=latitude, longitude=longitude)


def _build_shelter_results(n):
    """Build mock shelter results with taxonomy tags for filtering tests."""
    base = [
        {
            "service_id": "1", "service_name": "Adult Families Intake",
            "organization": "Department of Homeless Services (DHS)",
            "description": "Intake Center for Adult Families",
            "address": "400 E 30th St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0001",
            "fees": "Free", "is_open": "open",
            "hours_today": "8:00 AM – 6:00 PM",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-01",
            "also_available": ["Case Management"],
            "eligibility_summary": "Families",
            "review_highlight": "Staff was helpful",
            "required_documents": None,
            "languages": ["English", "Spanish"],
            "service_taxonomies": ["Shelter", "Families", "Intake"],
        },
        {
            "service_id": "2", "service_name": "Family Intake",
            "organization": "Department of Homeless Services (DHS)",
            "description": "DHS shelter intake for Families with children",
            "address": "151 E 151st St, Bronx, NY",
            "city": "Bronx", "phone": "212-555-0002",
            "fees": "Free", "is_open": "closed",
            "hours_today": "9:00 AM – 5:00 PM",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-03-15",
            "also_available": ["Food"],
            "eligibility_summary": "Families",
            "review_highlight": None,
            "required_documents": ["State ID"],
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Families"],
        },
        {
            "service_id": "3", "service_name": "Single Adult Shelter",
            "organization": "CAMBA",
            "description": "Emergency shelter for single adults",
            "address": "1000 Blake Ave, Brooklyn, NY",
            "city": "Brooklyn", "phone": "212-555-0003",
            "fees": "Free", "is_open": "open",
            "hours_today": "24 Hours",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-10",
            "also_available": ["Shower", "Food"],
            "eligibility_summary": None,
            "review_highlight": None,
            "required_documents": None,
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Single Adult"],
        },
        {
            "service_id": "4", "service_name": "Veterans Shelter",
            "organization": "Veterans Affairs",
            "description": "Transitional housing for veterans",
            "address": "245 W Houston St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0004",
            "fees": "Free", "is_open": None,
            "hours_today": None,
            "requires_membership": True, "yourpeer_url": None,
            "last_validated_at": "2026-02-20",
            "also_available": ["Health"],
            "eligibility_summary": None,
            "review_highlight": "Really supportive environment",
            "required_documents": ["DD-214"],
            "languages": ["English"],
            "service_taxonomies": ["Shelter", "Veterans", "Veterans Short-Term Housing"],
        },
        {
            "service_id": "5", "service_name": "Youth Safe Haven",
            "organization": "Covenant House",
            "description": "Emergency shelter for youth ages 16-24",
            "address": "460 W 41st St, New York, NY",
            "city": "Manhattan", "phone": "212-555-0005",
            "fees": "Free", "is_open": "open",
            "hours_today": "24 Hours",
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": "2026-04-12",
            "also_available": ["Food", "Health", "Employment"],
            "eligibility_summary": "Ages 16–24",
            "review_highlight": None,
            "required_documents": None,
            "languages": ["English", "Spanish"],
            "service_taxonomies": ["Shelter", "Youth", "Safe Haven"],
        },
    ]
    while len(base) < n:
        i = len(base)
        base.append({
            "service_id": str(i + 1),
            "service_name": f"Shelter Program {i + 1}",
            "organization": f"Org {i + 1}",
            "description": "Emergency shelter services",
            "address": f"{i+1} Main St, Manhattan, NY",
            "city": "Manhattan", "phone": f"212-555-{i:04d}",
            "fees": "Free", "is_open": None,
            "hours_today": None,
            "requires_membership": False, "yourpeer_url": None,
            "last_validated_at": None,
            "also_available": None,
            "eligibility_summary": None,
            "review_highlight": None,
            "required_documents": None,
            "languages": None,
            "service_taxonomies": ["Shelter"],
        })
    return {
        "services": base[:n],
        "result_count": n,
        "template_used": "HousingEligibilityQuery",
        "params_applied": {},
        "relaxed": False,
        "execution_ms": 50,
        "freshness": {"fresh": n, "total": n, "total_with_date": n},
    }
