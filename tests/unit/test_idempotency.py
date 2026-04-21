"""
Tests for the idempotency cache.

The idempotency cache dedupes client retries at the chat endpoint: when
a client loses its network on the response side of a request, it may
retry with the same X-Request-ID — and we don't want to re-run the
whole LLM pipeline a second time. See docs/design/PWA_OFFLINE_DESIGN.md
§3.10–3.11 for the design rationale.

Covered here:
    - Basic get/put
    - TTL expiry
    - Size-cap overflow (oldest-first eviction)
    - Empty / None request_id handling (routing-layer edge cases)
    - Overwrite semantics
    - Thread safety under concurrent writers
    - Diagnostic helpers (size, clear)

Time is controlled via ``patch("app.services.idempotency.time")`` —
same pattern as ``test_rate_limiter.py``. No freezegun dependency.

Run: pytest tests/unit/test_idempotency.py -v
"""

import threading
from unittest.mock import patch

import pytest

from app.services import idempotency
from app.services.idempotency import (
    IDEMPOTENCY_TTL_SECONDS,
    MAX_CACHE_SIZE,
    clear,
    get,
    put,
    size,
)


# -----------------------------------------------------------------------
# FIXTURES
# -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Wipe the module-level cache between tests. Autouse because the
    cache is global state — one leaky test would pollute the next."""
    clear()
    yield
    clear()


# -----------------------------------------------------------------------
# BASIC GET / PUT
# -----------------------------------------------------------------------


def test_get_returns_none_on_miss():
    """An unknown request_id should return None, not raise."""
    assert get("never-seen-this-id") is None


def test_put_then_get_returns_stored_body():
    """Round-trip: a body written via put() comes back via get()."""
    body = {"response": "I can help with that.", "services": []}
    put("req-123", body)
    result = get("req-123")
    assert result == body


def test_put_then_get_returns_same_object_identity():
    """No-copy cache: the returned dict is the same object (not a
    defensive copy). Documents the current contract — callers must not
    mutate the returned dict. If that becomes a footgun we switch to
    copy.deepcopy at the boundary; for now this pins the behavior."""
    body = {"response": "hi"}
    put("req-identity", body)
    result = get("req-identity")
    assert result is body


def test_put_overwrites_existing_entry():
    """Same request_id twice should overwrite. The second response is
    the canonical one — if a retry somehow slipped through with a
    fresh handler invocation, we want the freshest version."""
    put("req-overwrite", {"response": "first"})
    put("req-overwrite", {"response": "second"})
    assert get("req-overwrite") == {"response": "second"}


def test_size_tracks_entry_count():
    """size() should reflect current cache occupancy."""
    assert size() == 0
    put("a", {"x": 1})
    assert size() == 1
    put("b", {"x": 2})
    assert size() == 2
    put("a", {"x": 3})  # overwrite, not an add
    assert size() == 2


def test_clear_empties_cache():
    """clear() wipes everything. Used in tests; not production."""
    put("a", {"x": 1})
    put("b", {"x": 2})
    assert size() == 2
    clear()
    assert size() == 0
    assert get("a") is None
    assert get("b") is None


# -----------------------------------------------------------------------
# EMPTY / FALSY REQUEST IDS
# -----------------------------------------------------------------------


def test_get_with_empty_string_returns_none():
    """Empty string is a no-op on get() — matches the route's
    pattern where client_request_id falsiness guards the call."""
    assert get("") is None


def test_get_with_none_returns_none():
    """None is a no-op on get(). The signature is typed as str, but
    defensive guards exist for the case where a caller forgets to
    check headers.get(..., '')."""
    assert get(None) is None


def test_put_with_empty_string_is_noop():
    """put('') should NOT store anything — with no key there's
    nothing meaningful to dedupe against."""
    put("", {"response": "should not be cached"})
    assert size() == 0


def test_put_with_none_is_noop():
    """put(None, ...) should NOT store anything. Never raises."""
    put(None, {"response": "should not be cached"})
    assert size() == 0


# -----------------------------------------------------------------------
# TTL EXPIRY
# -----------------------------------------------------------------------


def test_entry_valid_just_before_ttl():
    """An entry read 1 second before the TTL should still be there."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        mock_time.time.return_value = base
        put("req-ttl-ok", {"response": "still here"})

        # Advance to just before expiry
        mock_time.time.return_value = base + IDEMPOTENCY_TTL_SECONDS - 1
        assert get("req-ttl-ok") == {"response": "still here"}


def test_entry_expired_after_ttl():
    """Reading an entry past its TTL returns None."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        mock_time.time.return_value = base
        put("req-ttl-expired", {"response": "gone soon"})

        # Advance past expiry
        mock_time.time.return_value = base + IDEMPOTENCY_TTL_SECONDS + 1
        assert get("req-ttl-expired") is None


def test_expired_entry_removed_from_cache_on_access():
    """Expired entries are pruned lazily on read. An expired get()
    should leave size() smaller afterward — we don't want the cache
    holding onto dead entries until the next write triggers eviction."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        mock_time.time.return_value = base
        put("req-prune", {"response": "x"})
        assert size() == 1

        mock_time.time.return_value = base + IDEMPOTENCY_TTL_SECONDS + 1
        _ = get("req-prune")  # triggers lazy eviction
        assert size() == 0


def test_expiry_is_exactly_at_boundary():
    """At exactly the TTL boundary, the entry is considered expired.
    The comparison is `expires_at <= now`, so equality is the
    expired side. Documents the choice — a 1-second-resolution retry
    arriving at the exact millisecond of expiry is safer to re-run
    than to return a stale response."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        mock_time.time.return_value = base
        put("req-boundary", {"response": "x"})

        mock_time.time.return_value = base + IDEMPOTENCY_TTL_SECONDS
        assert get("req-boundary") is None


def test_put_evicts_other_expired_entries():
    """A write also cleans up unrelated expired entries — so a
    long-running process that never retries doesn't accumulate
    zombies. The eviction runs inside put()."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        mock_time.time.return_value = base
        put("old-1", {"x": 1})
        put("old-2", {"x": 2})
        assert size() == 2

        # Advance past TTL; old entries are now stale.
        mock_time.time.return_value = base + IDEMPOTENCY_TTL_SECONDS + 1
        put("fresh", {"x": 3})
        # Writing "fresh" evicted "old-1" and "old-2".
        assert size() == 1
        assert get("fresh") == {"x": 3}
        assert get("old-1") is None
        assert get("old-2") is None


# -----------------------------------------------------------------------
# SIZE-CAP OVERFLOW
# -----------------------------------------------------------------------


def test_cache_stays_at_or_below_max():
    """Writing past MAX_CACHE_SIZE should never grow unbounded —
    overflow eviction keeps us at the cap. Uses a monotonically-
    increasing clock so each entry has a distinct expiration
    (otherwise the ``sorted(..., key=expiration)`` eviction
    tie-breaks could be flaky)."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        for i in range(MAX_CACHE_SIZE + 100):
            # Advance clock by a microsecond each iteration to keep
            # expirations distinct. All entries stay WITHIN TTL.
            mock_time.time.return_value = base + i * 1e-6
            put(f"req-{i}", {"i": i})
        assert size() <= MAX_CACHE_SIZE


def test_overflow_drops_oldest_first():
    """When the cap is exceeded, the entry with the EARLIEST
    expiration (= the one written earliest) is the first to go."""
    base = 1000.0
    with patch("app.services.idempotency.time") as mock_time:
        # Fill to the cap with a gap between each write.
        for i in range(MAX_CACHE_SIZE):
            mock_time.time.return_value = base + i * 1e-3
            put(f"req-{i}", {"i": i})
        assert size() == MAX_CACHE_SIZE

        # One more write tips us over — req-0 (oldest) should be evicted.
        mock_time.time.return_value = base + MAX_CACHE_SIZE * 1e-3
        put("req-new", {"i": "new"})

        assert size() == MAX_CACHE_SIZE
        assert get("req-0") is None, "oldest entry should have been evicted"
        assert get("req-new") == {"i": "new"}
        # Entry just above the evicted oldest should still be there.
        assert get("req-1") == {"i": 1}


# -----------------------------------------------------------------------
# THREAD SAFETY
# -----------------------------------------------------------------------


def test_concurrent_writers_all_succeed():
    """Many threads writing distinct keys should not deadlock, lose
    writes, or corrupt the dict. Run enough writes from enough threads
    that any unlocked access would almost certainly trigger a
    RuntimeError (dict size changed during iteration) in the eviction
    pass."""
    thread_count = 10
    writes_per_thread = 200
    errors: list = []

    def writer(thread_id: int):
        try:
            for i in range(writes_per_thread):
                put(f"t{thread_id}-{i}", {"thread": thread_id, "i": i})
        except Exception as e:  # pragma: no cover — only on a real bug
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"writer threads saw exceptions: {errors}"
    # All writes fit well under MAX_CACHE_SIZE (10 × 200 = 2000 > 1000,
    # so we expect overflow eviction to have kicked in — but the
    # invariant is that the cache is intact, not a specific count).
    assert size() <= MAX_CACHE_SIZE


def test_concurrent_read_write_no_corruption():
    """Readers and writers should coexist without a reader ever
    seeing a half-written entry (which would show up as a KeyError
    or TypeError on the tuple unpack in get()).

    Uses a bounded operation count (not a time window) so the test
    is deterministic and doesn't depend on wall-clock delays —
    keeps this out of the D9 sleep-based-flake audit category.
    """
    ops_per_writer = 500
    ops_per_reader = 500
    errors: list = []

    def writer():
        try:
            for i in range(ops_per_writer):
                put(f"shared-{i % 50}", {"i": i})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def reader():
        try:
            for _ in range(ops_per_reader):
                # Read both hits and misses.
                get("shared-0")
                get("nonexistent")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    writers = [threading.Thread(target=writer) for _ in range(4)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    for t in writers + readers:
        t.join()

    assert errors == [], f"concurrent operations saw exceptions: {errors}"


# -----------------------------------------------------------------------
# DEFENSIVE BEHAVIOR
# -----------------------------------------------------------------------


def test_get_never_raises_on_malformed_entry():
    """If an entry somehow has a non-float expiration (test-only
    manipulation or a corrupted state), get() should still not
    raise unhandled exceptions that would break the request.

    We test this by writing directly into the internal cache with
    a malformed tuple and confirming get() handles it gracefully.
    """
    from app.services.idempotency import _get_internal_state, _LOCK

    with _LOCK:
        _get_internal_state()["malformed"] = ("not-a-tuple-the-right-shape",)  # wrong arity

    # Current implementation will raise ValueError on the tuple unpack
    # — this is a latent robustness gap. The test documents the
    # current brittle behavior so a future hardening PR flips it.
    # When we tighten get() to try/except the unpack, change this
    # assertion to `assert get("malformed") is None`.
    with pytest.raises(ValueError):
        get("malformed")


def test_body_can_contain_nested_structures():
    """Real response bodies are nested dicts with lists of service
    cards. Cache should handle them transparently — no serialization."""
    body = {
        "response": "I found 2 shelters for you.",
        "services": [
            {"id": "svc-1", "name": "Ali Forney Center", "taxonomies": ["LGBTQ", "Youth"]},
            {"id": "svc-2", "name": "Covenant House", "taxonomies": ["Youth"]},
        ],
        "quick_replies": [{"label": "Change location", "value": "__change_loc__"}],
        "slots": {"service_type": "shelter", "location": "Manhattan"},
    }
    put("req-nested", body)
    result = get("req-nested")
    assert result == body
    # The nested list should be the same reference, not a copy.
    assert result["services"] is body["services"]


# -----------------------------------------------------------------------
# TTL / SIZE CONSTANT SANITY
# -----------------------------------------------------------------------


def test_ttl_matches_design_doc():
    """Pin the 60-second TTL. If someone changes it, the design
    doc (PWA_OFFLINE_DESIGN.md §3.10) should be updated too — this
    test exists to make the coupling visible."""
    assert IDEMPOTENCY_TTL_SECONDS == 60


def test_max_cache_size_matches_design_doc():
    """Pin the 1000-entry cap. Same motivation as above — the cap
    is a policy choice, not a tunable. If it changes, the rationale
    in PWA_OFFLINE_DESIGN.md should be revisited."""
    assert MAX_CACHE_SIZE == 1000
