# Copyright (c) 2024 Streetlives, Inc.
# Use of this source code is governed by an MIT-style license.

"""
TTL cache for the locations admin aggregations.

The admin page renders by calling ~11 endpoints on mount. Each endpoint
runs SQL aggregations against the Streetlives DB. Without caching, every
admin page load (or refresh, or back-button) reruns the full set —
~11 round-trips, several involving non-trivial GROUP BY work over
service_taxonomy and service_at_locations.

This module provides a process-local TTL cache to deduplicate those
calls. Admin clusters in time (open page, click around for a minute,
leave); 5-minute TTL gives near-free deduplication without
perceptible staleness.

What this is NOT:
    * A cross-process cache. Each API worker has its own cache. That's
      fine — the cache is a perf optimization, not a correctness
      mechanism, and a 5-minute upper bound on inconsistency between
      workers is acceptable.
    * An invalidation system. There are no write hooks; the cache
      expires entries strictly by TTL. If an admin makes a data change
      elsewhere (e.g. updates a location in YourPeer's own tools) the
      admin page may show stale data for up to TTL seconds.
    * A general-purpose cache. The decorator is hardcoded to log
      under the `locations_admin` logger and use the
      `ADMIN_CACHE_TTL_SECONDS` default. Future admin pages can
      copy the pattern; sharing the same cache instance across
      modules would mix concerns.

What this IS:
    * A small, thread-safe, size-capped TTL cache.
    * A decorator (`@ttl_cached`) that wraps deterministic functions.
    * An observability shim — every HIT/MISS gets a log line at INFO
      so ops can confirm the cache is doing work.
    * An explicit `clear_locations_admin_cache()` entry point for
      tests and for ops force-refresh.

Cache key:
    The decorator keys entries on `(function qualified-name, args,
    sorted kwargs)`. Args containing lists are converted to tuples
    (shallowly) so they're hashable. If any arg remains unhashable
    after conversion, the call bypasses the cache (no exception
    raised).

Capacity:
    Bounded by `MAX_CACHE_ENTRIES`. When the cap is reached, the
    oldest entry is evicted (FIFO, not LRU — LRU adds bookkeeping
    that isn't justified at this scale).
"""

import logging
import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)

# TTL in seconds. 300 = 5 minutes, matching the spec value. Worth
# revisiting if admins start complaining about stale state — easy
# knob to turn down.
ADMIN_CACHE_TTL_SECONDS = 300

# Hard cap on entries. The admin page has ~11 cached endpoints; with
# the small number of filter combinations admins explore in a
# session, we expect total entries to stay well under 100. Setting
# the cap at 1000 leaves substantial headroom for filter-aware
# endpoints if they're ever cached, while bounding memory if
# something goes wrong (e.g. a perm-attacker hitting random URLs).
MAX_CACHE_ENTRIES = 1000


class _TTLCache:
    """Thread-safe TTL cache. Internal — use the `ttl_cached`
    decorator below from application code, and
    `clear_locations_admin_cache` from tests.
    """

    def __init__(self) -> None:
        # OrderedDict so we can do O(1) FIFO eviction when over cap.
        self._data: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        """Returns the cached value or None. Removes the entry if it's
        expired (so the next get returns None, and the next set takes
        the entry's slot in the OrderedDict)."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                # Expired — evict and treat as miss.
                del self._data[key]
                return None
            return value

    def set(self, key: Any, value: Any, ttl: int) -> None:
        with self._lock:
            # If the key already exists, remove it first so the
            # re-insert lands at the end of the OrderedDict (FIFO
            # ordering reflects most-recent-insertion).
            if key in self._data:
                del self._data[key]
            # Evict oldest if at capacity.
            while len(self._data) >= MAX_CACHE_ENTRIES:
                self._data.popitem(last=False)
            self._data[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


# Module-level singleton. Application code never touches this
# directly — go through the decorator or the public clear function.
_cache = _TTLCache()


def _make_hashable(value: Any) -> Any:
    """Convert lists to tuples (shallowly) so the value is hashable.
    Anything else is returned unchanged; if the caller still can't
    hash it, the decorator will detect that and bypass the cache.
    """
    if isinstance(value, list):
        return tuple(_make_hashable(x) for x in value)
    if isinstance(value, dict):
        # Dicts are unhashable; convert to sorted tuple of items.
        return tuple(sorted((k, _make_hashable(v)) for k, v in value.items()))
    return value


def ttl_cached(ttl: int = ADMIN_CACHE_TTL_SECONDS) -> Callable:
    """Decorator factory that caches a function's return value for
    `ttl` seconds keyed by its qualified name + args + kwargs.

    Apply only to read-only functions where stale data within `ttl`
    is acceptable. The admin-locations aggregations satisfy both
    properties.

    Cache hits/misses log at INFO so ops can confirm the cache is
    doing work — without observability, "the cache works" is just
    an assertion.

    Falls through to the underlying function (no caching) if the
    args don't hash cleanly. This is defensive: never let cache
    machinery break a real call.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                key = (
                    fn.__qualname__,
                    tuple(_make_hashable(a) for a in args),
                    tuple(sorted(
                        (k, _make_hashable(v)) for k, v in kwargs.items()
                    )),
                )
                hash(key)  # raises TypeError if any leaf is unhashable
            except TypeError as e:
                logger.warning(
                    "locations_admin cache: bypassing cache for %s — "
                    "args unhashable: %s",
                    fn.__qualname__, e,
                )
                return fn(*args, **kwargs)

            cached = _cache.get(key)
            if cached is not None:
                logger.info(
                    "locations_admin cache HIT for %s (ttl=%ds)",
                    fn.__qualname__, ttl,
                )
                return cached

            result = fn(*args, **kwargs)
            _cache.set(key, result, ttl)
            logger.info(
                "locations_admin cache MISS for %s (stored, ttl=%ds, "
                "cache_size=%d)",
                fn.__qualname__, ttl, _cache.size(),
            )
            return result

        return wrapper

    return decorator


def clear_locations_admin_cache() -> None:
    """Flush the entire cache. Two legitimate callers:

    1. Pytest fixtures, so tests don't share cached state across
       test boundaries (without this, the second test of a given
       endpoint would hit the cache from the first test and skip
       the mocked _execute_sql, breaking assertions).
    2. Ops, when they know upstream data has changed and want the
       admin page to reflect it before the TTL expires. Currently
       no UI button exposes this — call from a Python shell or add
       an admin-only endpoint if the need becomes routine.
    """
    _cache.clear()


def cache_size() -> int:
    """Current number of entries. For diagnostics / tests."""
    return _cache.size()
