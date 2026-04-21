# Copyright (c) 2024 Streetlives, Inc.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""
Claude LLM Client — shared Anthropic client for all LLM tasks.

Provides a single lazy-initialized Anthropic client and task-specific
helper functions. Replaces the previous Gemini client for conversational
responses while consolidating with the existing Claude usage for slot
extraction and crisis detection.

Model selection (from model analysis, April 2026):
    - Conversational responses: Haiku 4.5 (speed > reasoning depth)
    - Slot extraction: Haiku 4.5 (simple schema, bounded tool calling)
    - Crisis detection: Sonnet 4.6 (safety-critical, needs nuance)

See /admin/models in the staff console for the full cost/capability analysis.
"""

import os
import logging
import time
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MODELS — centralized so changes propagate everywhere
# ---------------------------------------------------------------------------

# Conversational fallback (warm, short replies, no tool calling)
CONVERSATIONAL_MODEL = "claude-haiku-4-5-20251001"

# Slot extraction (structured tool calling with simple schema)
SLOT_EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

# Crisis detection (safety-critical classification, needs nuance)
CRISIS_DETECTION_MODEL = "claude-sonnet-4-6"

# Message classification (routing ambiguous messages to the right handler)
CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# CLIENT
# ---------------------------------------------------------------------------

try:
    import anthropic
    _anthropic_available = True
except ImportError:
    _anthropic_available = False
    logger.warning("anthropic SDK not installed — LLM features disabled")

_client = None
_init_error = None


def get_client():
    """Lazy-initialize a shared Anthropic client.

    Used by this module and can be imported by llm_slot_extractor.py and
    crisis_detector.py to avoid creating multiple client instances.
    """
    global _client, _init_error

    if _client is not None:
        return _client
    if _init_error is not None:
        raise _init_error
    if not _anthropic_available:
        _init_error = RuntimeError("anthropic SDK not installed")
        raise _init_error

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        _init_error = RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to your .env file.\n"
            "Get a key at https://console.anthropic.com/"
        )
        raise _init_error

    try:
        _client = anthropic.Anthropic(
            api_key=api_key,
            timeout=10.0,  # D2: prevent stalled LLM calls from blocking requests
        )
        logger.info(
            f"Anthropic client initialized. "
            f"Conversational: {CONVERSATIONAL_MODEL}, "
            f"Slots: {SLOT_EXTRACTION_MODEL}, "
            f"Crisis: {CRISIS_DETECTION_MODEL}"
        )
        return _client
    except Exception as e:
        _init_error = RuntimeError(f"Failed to initialize Anthropic client: {e}")
        raise _init_error


# ---------------------------------------------------------------------------
# LLM CALL TRACKING — daily counter with alerting
# ---------------------------------------------------------------------------

_daily_call_count = 0
_daily_call_date = None
DAILY_CALL_LIMIT = int(os.getenv("LLM_DAILY_CALL_LIMIT", "5000"))


def _track_llm_call(task: str = "unknown") -> None:
    """Track LLM API calls and alert when daily threshold is exceeded.

    Called at the start of every LLM function (claude_reply,
    classify_message_llm, etc.). Logs CRITICAL when the daily limit
    is reached — this should trigger alerting in production.
    """
    global _daily_call_count, _daily_call_date
    from datetime import datetime
    today = datetime.now().date()
    if _daily_call_date != today:
        if _daily_call_count > 0:
            logger.info(f"Daily LLM call count reset (yesterday: {_daily_call_count})")
        _daily_call_count = 0
        _daily_call_date = today
    _daily_call_count += 1
    if _daily_call_count == DAILY_CALL_LIMIT:
        logger.critical(
            f"Daily LLM call limit reached: {DAILY_CALL_LIMIT} calls. "
            f"Task: {task}. Check for abuse or bot traffic."
        )
    elif _daily_call_count == DAILY_CALL_LIMIT // 2:
        logger.warning(
            f"Daily LLM calls at 50%: {_daily_call_count}/{DAILY_CALL_LIMIT}. "
            f"Task: {task}."
        )


# ---------------------------------------------------------------------------
# CONVERSATIONAL REPLY (replaces gemini_reply)
# ---------------------------------------------------------------------------

def claude_reply(prompt: str) -> str:
    """Generate a short conversational reply using Claude Haiku.

    This replaces the previous gemini_reply() function. Used for general
    conversation when the user's message doesn't match service keywords
    or other classified intents.

    Returns empty string on failure so the caller can fall back to a
    safe static message.
    """
    from app.services.audit_log import record_llm_call
    try:
        _track_llm_call("conversational")
        client = get_client()
        t0 = time.perf_counter()
        response = client.messages.create(
            model=CONVERSATIONAL_MODEL,
            max_tokens=150,  # 1-3 sentences, never needs more
            messages=[{"role": "user", "content": prompt}],
        )
        latency = round((time.perf_counter() - t0) * 1000)
        record_llm_call(
            task="conversational", model=CONVERSATIONAL_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency, success=True,
        )
        return response.content[0].text or ""
    except Exception as e:
        logger.error(f"Claude conversational reply failed: {e}")
        return "I'm having trouble connecting right now. Please try again."


# ---------------------------------------------------------------------------
# MESSAGE CLASSIFICATION
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM_PROMPT = """\
You are a message classifier for a social services chatbot serving \
people experiencing homelessness in New York City.

Classify the user's message into exactly one category. Respond with \
ONLY the category name, nothing else.

Categories:
- greeting: casual hello, hi, hey, good morning (only if that's ALL the message is)
- thanks: thank you, appreciate it (only if that's ALL the message is)
- reset: user wants to start over, clear, new search, begin again
- help: user asking what the bot can do, how it works, list services
- bot_identity: user asking if they're talking to AI, a robot, or a person
- bot_question: user asking about the bot's capabilities, how it works, why it did \
something, or what it can search for — NOT "help" (which is just "what can you do")
- escalation: user wants to talk to a real person, peer navigator, case manager
- frustration: user is upset with results or the bot (not helpful, waste of time)
- confused: user doesn't know what they need (I don't know, I'm lost, overwhelmed)
- emotional: user expressing feelings or emotional distress below crisis level \
(feeling down, rough day, scared, lonely, struggling) — NOT a service request
- confirm_yes: user confirming a pending action (yes, sure, ok, go ahead, search)
- confirm_deny: user declining a pending action (no, nah, not yet, wait)
- confirm_change_service: user wants to change the service type in a pending search
- confirm_change_location: user wants to change the location in a pending search
- service: user is describing a need for a social service (food, shelter, clothing, \
shower, health, mental health, legal, job, benefits) even if phrased indirectly
- general: everything else that doesn't fit above

Important rules:
- A message about needing help with a life situation (housing, food, safety) is \
"service", not "help". "help" is ONLY about how the bot works.
- "I don't know what to do" or "I'm overwhelmed" is "confused", NOT "service" \
or "mental_health". The user hasn't stated a need yet.
- "I'm feeling really down" or "having a rough day" is "emotional", not "confused" \
or "service". The user is sharing feelings, not asking for help yet.
- If the message contains BOTH a greeting and a service need ("hi I need food"), \
classify as "service" — the service need takes priority.
- Short affirmative words (yes, ok, sure) are "confirm_yes".
- Short negative words (no, nah, nope) are "confirm_deny".
- "Why weren't you able to get my location?" or "how does this work?" is \
"bot_question", not "frustration". The user is asking a question, not complaining.\
"""


def classify_message_llm(text: str) -> str | None:
    """Classify a message using Claude when regex is uncertain.

    Returns one of the category strings, or None if the LLM call fails
    (so the caller can fall back to regex classification).
    """
    from app.services.audit_log import record_llm_call
    try:
        _track_llm_call("classification")
        client = get_client()
        t0 = time.perf_counter()
        response = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=20,  # single word category name
            system=_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        latency = round((time.perf_counter() - t0) * 1000)
        record_llm_call(
            task="classification", model=CLASSIFICATION_MODEL,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency, success=True,
        )
        raw = response.content[0].text.strip().lower()

        # Validate that the response is an expected category
        valid = {
            "greeting", "thanks", "reset", "help", "bot_identity",
            "bot_question", "escalation", "frustration", "confused",
            "emotional",
            "confirm_yes", "confirm_deny",
            "confirm_change_service", "confirm_change_location",
            "service", "general",
        }
        if raw in valid:
            return raw

        logger.warning(f"LLM classifier returned unexpected category: '{raw}'")
        return None

    except Exception as e:
        logger.error(f"LLM message classification failed: {e}")
        return None


# ---------------------------------------------------------------------------
# HEALTH PING — lightweight live check for the /api/health endpoint
# ---------------------------------------------------------------------------
# Makes a minimal Haiku call (max_tokens=1, ~10 input tokens) to verify:
#   1. API key is valid (not revoked)
#   2. Anthropic API is reachable (not in outage)
#   3. Rate limits are not exceeded
#   4. Network path is clear
#
# Cost: ~$0.000012 per ping (Haiku input: 10 tokens × $0.80/M + 1 output × $4/M)
# At 90s TTL: ~960 pings/day ≈ $0.01/day
#
# Results are cached with a TTL so the frontend's 30s polling doesn't
# trigger a new API call every time. The cache stores the full result
# including latency and error details.

_LLM_HEALTH_CACHE_TTL = 90  # seconds — ping at most once per 90s
_llm_health_cache: dict | None = None
_llm_health_cache_time: float = 0


def ping_llm() -> dict:
    """Lightweight live health check against the Anthropic API.

    Returns a dict with:
        status: "up" | "auth_error" | "rate_limited" | "timeout" |
                "api_error" | "unavailable"
        latency_ms: round-trip time (only when status is "up")
        detail: human-readable description
        cached: True if this result was served from cache

    The result is cached for _LLM_HEALTH_CACHE_TTL seconds.
    """
    global _llm_health_cache, _llm_health_cache_time

    now = time.time()
    if _llm_health_cache and (now - _llm_health_cache_time) < _LLM_HEALTH_CACHE_TTL:
        return {**_llm_health_cache, "cached": True}

    # No API key → not configured (not an error)
    if not os.getenv("ANTHROPIC_API_KEY"):
        result = {"status": "unavailable", "detail": "API key not configured"}
        _llm_health_cache = result
        _llm_health_cache_time = now
        return {**result, "cached": False}

    if not _anthropic_available:
        result = {"status": "unavailable", "detail": "anthropic SDK not installed"}
        _llm_health_cache = result
        _llm_health_cache_time = now
        return {**result, "cached": False}

    try:
        client = get_client()
        t0 = time.perf_counter()
        response = client.messages.create(
            model=CONVERSATIONAL_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        latency_ms = round((time.perf_counter() - t0) * 1000)
        # Touch the response so linters / dead-code analyses don't flag it;
        # also a tiny sanity check that we got a parseable object back.
        _ = response

        result = {
            "status": "up",
            "latency_ms": latency_ms,
            "detail": f"Responding ({latency_ms}ms)",
        }

    except Exception as e:
        error_name = type(e).__name__
        error_msg = str(e)[:120]

        if _anthropic_available:
            if isinstance(e, anthropic.AuthenticationError):
                result = {
                    "status": "auth_error",
                    "detail": "API key invalid or revoked",
                }
            elif isinstance(e, anthropic.RateLimitError):
                result = {
                    "status": "rate_limited",
                    "detail": "Rate limit exceeded",
                }
            elif isinstance(e, anthropic.APITimeoutError):
                result = {
                    "status": "timeout",
                    "detail": "API request timed out",
                }
            elif isinstance(e, anthropic.APIConnectionError):
                result = {
                    "status": "api_error",
                    "detail": "Cannot reach Anthropic API",
                }
            elif isinstance(e, anthropic.APIStatusError) and e.status_code >= 500:
                result = {
                    "status": "api_error",
                    "detail": f"Anthropic API error ({e.status_code})",
                }
            else:
                result = {
                    "status": "api_error",
                    "detail": f"{error_name}: {error_msg}",
                }
        else:
            result = {
                "status": "api_error",
                "detail": f"{error_name}: {error_msg}",
            }

        logger.warning(f"LLM health ping failed: {result['status']} — {result['detail']}")

    _llm_health_cache = result
    _llm_health_cache_time = now
    return {**result, "cached": False}
