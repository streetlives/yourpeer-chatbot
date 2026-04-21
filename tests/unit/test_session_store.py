"""
Tests for the session store — basic operations and thread safety.

Bug 7 regression: The session store previously had no threading lock,
meaning concurrent FastAPI requests could corrupt the session dict
during iteration/deletion in _evict_expired().

Run with: python -m pytest tests/test_session_store.py -v
Or just:  python tests/test_session_store.py
"""

import time
import pytest
import threading


from app.services.session_store import (
    get_session_slots,
    save_session_slots,
    clear_session,
    _SESSION_STATE,
    _lock,
    SESSION_TTL_SECONDS,
)


def _clear_all():
    """Helper: clear all sessions for test isolation."""
    with _lock:
        _SESSION_STATE.clear()


# -----------------------------------------------------------------------
# BASIC OPERATIONS
# -----------------------------------------------------------------------

def test_save_and_get():
    """Saving slots and retrieving them should return a copy."""
    _clear_all()
    save_session_slots("s1", {"service_type": "food", "location": "Brooklyn"})
    slots = get_session_slots("s1")
    assert slots["service_type"] == "food"
    assert slots["location"] == "Brooklyn"

    # Modifying the returned dict should NOT affect the store
    slots["service_type"] = "shelter"
    original = get_session_slots("s1")
    assert original["service_type"] == "food", \
        "Returned slots should be a deep copy — mutation should not propagate"


def test_get_nonexistent():
    """Getting a nonexistent session should return empty dict."""
    _clear_all()
    slots = get_session_slots("nonexistent")
    assert slots == {}


def test_clear_session():
    """Clearing a session should remove it entirely."""
    _clear_all()
    save_session_slots("s1", {"service_type": "food"})
    assert get_session_slots("s1") != {}

    clear_session("s1")
    assert get_session_slots("s1") == {}


def test_clear_nonexistent():
    """Clearing a nonexistent session should not raise."""
    _clear_all()
    try:
        clear_session("nonexistent")
    except Exception as e:
        pytest.fail(f"clear_session on nonexistent id raised: {type(e).__name__}: {e}")


def test_overwrite():
    """Saving to the same session ID should overwrite."""
    _clear_all()
    save_session_slots("s1", {"service_type": "food"})
    save_session_slots("s1", {"service_type": "shelter", "location": "Queens"})
    slots = get_session_slots("s1")
    assert slots["service_type"] == "shelter"
    assert slots["location"] == "Queens"


# -----------------------------------------------------------------------
# THREAD SAFETY (Bug 7 regression)
# -----------------------------------------------------------------------

def test_concurrent_read_write():
    """Bug 7: Concurrent reads and writes should not raise or corrupt data.

    Previously, _evict_expired() could delete keys while another thread
    was iterating the dict, causing RuntimeError: dictionary changed
    size during iteration.
    """
    _clear_all()
    errors = []
    num_threads = 10
    ops_per_thread = 50

    def writer(thread_id):
        try:
            for i in range(ops_per_thread):
                sid = f"thread-{thread_id}-{i}"
                save_session_slots(sid, {"n": i, "t": thread_id})
        except Exception as e:
            errors.append(f"Writer {thread_id}: {e}")

    def reader(thread_id):
        try:
            for i in range(ops_per_thread):
                sid = f"thread-{thread_id}-{i}"
                get_session_slots(sid)
        except Exception as e:
            errors.append(f"Reader {thread_id}: {e}")

    def clearer(thread_id):
        try:
            for i in range(ops_per_thread):
                sid = f"thread-{thread_id}-{i}"
                clear_session(sid)
        except Exception as e:
            errors.append(f"Clearer {thread_id}: {e}")

    threads = []
    for t in range(num_threads):
        threads.append(threading.Thread(target=writer, args=(t,)))
        threads.append(threading.Thread(target=reader, args=(t,)))
        threads.append(threading.Thread(target=clearer, args=(t,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Thread safety errors: {errors}"
    print(f"  PASS: {num_threads * 3} concurrent threads, {num_threads * ops_per_thread * 3} operations, no errors")


def test_lock_exists():
    """Bug 7: Session store should have a threading lock."""
    assert hasattr(threading, 'Lock')
    assert _lock is not None
    assert isinstance(_lock, type(threading.Lock()))


# -----------------------------------------------------------------------
