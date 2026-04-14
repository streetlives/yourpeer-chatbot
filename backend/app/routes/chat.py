from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import logging
import uuid

from app.models.chat_models import ChatRequest, ChatResponse
from app.services.chatbot import generate_reply
from app.services.audit_log import log_feedback, log_location_feedback
from app.services.session_token import generate_session_id, validate_session_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_ERROR_RESPONSE = (
    "I\u2019m having trouble right now. You can try again, "
    "or visit yourpeer.nyc to search directly."
)


class FeedbackRequest(BaseModel):
    session_id: str
    rating: str = Field(..., pattern="^(up|down)$")
    comment: Optional[str] = Field(None, max_length=500)
    context: Optional[dict] = None


class LocationFeedbackRequest(BaseModel):
    """Per-location feedback with binary ratings per dimension."""
    session_id: str
    location_id: str
    location_name: Optional[str] = None
    safety: Optional[bool] = None
    friendliness: Optional[bool] = None
    cleanliness: Optional[bool] = None
    queer_friendly: Optional[bool] = None
    comment: Optional[str] = Field(None, max_length=500)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, raw: Request):
    request_id = raw.headers.get("x-request-id") or str(uuid.uuid4())
    session_id = request.session_id

    if session_id:
        if not validate_session_id(session_id):
            raise HTTPException(status_code=403, detail="Invalid session token")
    else:
        session_id = generate_session_id()

    try:
        result = generate_reply(
            message=request.message,
            session_id=session_id,
            latitude=request.latitude,
            longitude=request.longitude,
            request_id=request_id,
        )
        # Make sure the (possibly new) session_id is returned to the client
        result["session_id"] = session_id
        return JSONResponse(
            content=ChatResponse(**result).model_dump(),
            headers={"X-Request-ID": request_id},
        )
    except Exception:
        logger.exception(
            f"[req:{request_id}] Unhandled error in chat route"
        )
        # Return a usable response, not a 500. For someone in crisis
        # searching for shelter, a dead end with no guidance is dangerous.
        return JSONResponse(
            content=ChatResponse(
                session_id=session_id,
                response=_ERROR_RESPONSE,
                follow_up_needed=False,
                slots={},
            ).model_dump(),
            headers={"X-Request-ID": request_id},
        )


@router.post("/feedback")
async def feedback(request: FeedbackRequest):
    if not validate_session_id(request.session_id):
        raise HTTPException(status_code=403, detail="Invalid session token")
    try:
        log_feedback(
            session_id=request.session_id,
            rating=request.rating,
            comment=request.comment,
            context=request.context,
        )
    except Exception:
        logger.exception("Failed to log feedback")
    return {"ok": True}


@router.post("/location-feedback")
async def location_feedback(request: LocationFeedbackRequest):
    if not validate_session_id(request.session_id):
        raise HTTPException(status_code=403, detail="Invalid session token")
    try:
        log_location_feedback(
            session_id=request.session_id,
            location_id=request.location_id,
            location_name=request.location_name,
            safety=request.safety,
            friendliness=request.friendliness,
            cleanliness=request.cleanliness,
            queer_friendly=request.queer_friendly,
            comment=request.comment,
        )
    except Exception:
        logger.exception("Failed to log location feedback")
    return {"ok": True}
