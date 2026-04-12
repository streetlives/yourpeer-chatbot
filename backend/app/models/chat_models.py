from typing import Optional, List
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=1_000)
    session_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)


class ServiceCard(BaseModel):
    """Structured service result for frontend rendering."""
    service_id: Optional[str] = None
    service_name: str
    organization: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    fees: Optional[str] = None
    additional_info: Optional[str] = None
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
