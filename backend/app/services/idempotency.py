"""
Idempotency cache for the chat endpoint.

A client that loses its network on the response side of a request (the
server processed it, but the response never arrived) may retry with the
same X-Request-ID from its queue. Without dedupe, the server executes
the full LLM pipeline twice — wasted cost, wasted tokens, and a second
conversational turn the user didn't intend.

This module provides a short-lived response cache keyed by request_id.
On a cache hit, the cached JSON response is returned without re-running
the chatbot. On a miss, the request proceeds normally and the response
is written through to the cache.

Design choices:

- In-memory dict with a threading.Lock. Matches the existing pattern in
  session_store.py and rate_limiter.py. Single-process deployments only.
  If we ever scale out, Redis is the drop-in replacement.
- 60-second TTL. Long enough to cover realistic client-side retry
  windows (network flap, reconnect delay) without introducing surprising
  "why did the bot just repeat itself?" behavior if the user resends a
  similar message minutes later with a fresh X-Request-ID.
- Capped at 1000 entries. A background eviction pass runs on every
  write; no separate timer needed.
- Only successful responses (2xx-equivalent) are cached. Errors are
  rerun so transient server problems don't stick.
"""

import time
import threading
from typing import Any, Dict, Optional, Tuple

# Cache entry: (response_body_dict, expires_at_unix_timestamp)
_CACHE: Dict[str, Tuple[dict, float]] = {}
_LOCK = threading.Lock()

IDEMPOTENCY_TTL_SECONDS = 60
MAX_CACHE_SIZE = 1000


def _evict_expired_locked(now: float) -> None:
    """Drop expired entries. Caller must hold the lock."""
    stale = [k for k, (_, exp) in _CACHE.items() if exp <= now]
    for k in stale:
        _CACHE.pop(k, None)


def _evict_overflow_locked() -> None:
    """
    Drop oldest entries if we've exceeded the size cap.
    Caller must hold the lock.

    This is a safety valve for pathological request-ID patterns (a
    misbehaving client generating thousands of unique IDs per minute).
    Normal traffic should never hit this cap.
    """
    if len(_CACHE) <= MAX_CACHE_SIZE:
        return
    # Sort by expiration ascending and drop the oldest until under cap
    ordered = sorted(_CACHE.items(), key=lambda kv: kv[1][1])
    drop_count = len(_CACHE) - MAX_CACHE_SIZE
    for k, _ in ordered[:drop_count]:
        _CACHE.pop(k, None)


def get(request_id: str) -> Optional[dict]:
    """
    Return a cached response body for request_id, or None if not cached
    or expired. Never raises.
    """
    if not request_id:
        return None
    now = time.time()
    with _LOCK:
        entry = _CACHE.get(request_id)
        if entry is None:
            return None
        body, expires_at = entry
        if expires_at <= now:
            _CACHE.pop(request_id, None)
            return None
        return body


def put(request_id: str, body: dict) -> None:
    """
    Store a response body for request_id. Overwrites any existing entry
    with the same ID. Never raises.
    """
    if not request_id:
        return
    now = time.time()
    expires_at = now + IDEMPOTENCY_TTL_SECONDS
    with _LOCK:
        _evict_expired_locked(now)
        _CACHE[request_id] = (body, expires_at)
        _evict_overflow_locked()


def clear() -> None:
    """Wipe the cache. Intended for tests; not used in production code."""
    with _LOCK:
        _CACHE.clear()


def size() -> int:
    """Return the current number of cached entries. Intended for diagnostics."""
    with _LOCK:
        return len(_CACHE)


# Expose these for tests that want to advance time
def _get_internal_state() -> Any:
    """Testing helper — returns the raw cache dict. Do not use outside tests."""
    return _CACHE
