from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=1_000)
    session_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    # How the user submitted this message:
    #   • "typed"        — keyboard input via the chat input field
    #   • "quick_reply"  — tap on a bot-emitted button pill
    # None is allowed for legacy clients that pre-date this field; the
    # backend treats unknown source the same as "typed" (conservative
    # default — the confirmation-skip optimization only fires when we're
    # certain every message in the session came from a tap). Pydantic
    # enforces the literal values when the field IS provided.
    source: Optional[Literal["typed", "quick_reply"]] = None


class ServiceCard(BaseModel):
    """Structured service result for frontend rendering."""
    service_id: Optional[str] = None
    service_name: str
    organization: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    # Geographic coordinates from the DB's ST_Y/ST_X projection on
    # location.position. Used by the frontend for map markers and
    # distance display; also consumed by the geographic-borough validator
    # in query_executor. NULL-safe — services without position data pass
    # through as None.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    fees: Optional[str] = None
    yourpeer_url: Optional[str] = None
    hours_today: Optional[str] = None
    is_open: Optional[str] = None
    requires_membership: Optional[bool] = None
    last_validated_at: Optional[str] = None
    also_available: Optional[List[str]] = None
    accessibility: Optional[str] = None
    eligibility_summary: Optional[str] = None
    review_highlight: Optional[str] = None
    required_documents: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    # Raw DB taxonomy tags for this service (e.g. ["Shelter", "Families",
    # "Intake"]). Kept as canonical DB names (not display-label-mapped)
    # so post-results filters can match against them. Enforced as a
    # ServiceCard field by tests/integration/test_schema_and_mock_sync.py
    # so format_service_card output round-trips cleanly through Pydantic.
    service_taxonomies: Optional[List[str]] = None


class QuickReply(BaseModel):
    """A tappable button option shown below a bot message."""
    label: str
    value: str  # the text sent as a user message when tapped
    href: Optional[str] = None  # when present, renders as <a> (e.g. tel: links)


class ChatResponse(BaseModel):
    session_id: str
    response: str
    follow_up_needed: bool
    slots: dict
    services: List[ServiceCard] = []
    result_count: int = 0
    relaxed_search: bool = False
    quick_replies: List[QuickReply] = []
