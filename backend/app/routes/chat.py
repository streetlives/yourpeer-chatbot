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
from app.services import idempotency

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
    # Idempotency: clients send X-Request-ID for each user action. If the
    # same ID arrives twice within IDEMPOTENCY_TTL_SECONDS (e.g. because
    # the response to the first request was lost to a network flap and
    # the client retried from its offline queue), return the cached
    # response instead of running the full LLM pipeline again.
    #
    # Note: we only use client-supplied IDs for dedupe. Server-generated
    # fallback IDs (when the header is absent) are unique per call and
    # can't collide.
    client_request_id = raw.headers.get("x-request-id")
    request_id = client_request_id or str(uuid.uuid4())

    if client_request_id:
        cached_body = idempotency.get(client_request_id)
        if cached_body is not None:
            logger.info(
                f"[req:{request_id}] idempotency cache hit — returning cached response"
            )
            return JSONResponse(
                content=cached_body,
                headers={
                    "X-Request-ID": request_id,
                    "X-Idempotency-Replay": "true",
                },
            )

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
            source=request.source,
        )
        # Make sure the (possibly new) session_id is returned to the client
        result["session_id"] = session_id
        response_body = ChatResponse(**result).model_dump()

        # Cache for idempotent retry. Only cache successful responses —
        # if something transient went wrong upstream (a 500-level error
        # that somehow escaped here), don't let the client be stuck
        # with a cached bad response for the next minute.
        if client_request_id:
            idempotency.put(client_request_id, response_body)

        return JSONResponse(
            content=response_body,
            headers={"X-Request-ID": request_id},
        )
    except Exception:
        logger.exception(
            f"[req:{request_id}] Unhandled error in chat route"
        )
        # Return a usable response, not a 500. For someone in crisis
        # searching for shelter, a dead end with no guidance is dangerous.
        # Don't cache this — we want a retry to actually retry.
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
