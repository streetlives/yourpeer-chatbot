"""
Tests for per-location feedback (Gap 6).

Covers:
  - log_location_feedback: event stored, ratings dict, partial ratings
  - Stats aggregation: location_feedback_count in get_stats()
  - Admin event filter: location_feedback type accepted

Run with: python -m pytest tests/unit/test_location_feedback.py -v
"""

from app.services.audit_log import (
    log_location_feedback,
    get_recent_events,
    get_stats,
    clear_audit_log,
)


# -----------------------------------------------------------------------
# AUDIT LOG
# -----------------------------------------------------------------------

class TestLocationFeedbackLogging:
    """Test log_location_feedback stores events correctly."""

    def setup_method(self):
        clear_audit_log()

    def test_full_ratings_stored(self):
        log_location_feedback(
            session_id="sess-1",
            location_id="loc-123",
            location_name="Test Pantry",
            safety=True,
            friendliness=True,
            cleanliness=False,
            queer_friendly=True,
            comment="Great place",
        )
        events = get_recent_events(event_type="location_feedback")
        assert len(events) == 1
        evt = events[0]
        assert evt["type"] == "location_feedback"
        assert evt["session_id"] == "sess-1"
        assert evt["location_id"] == "loc-123"
        assert evt["location_name"] == "Test Pantry"
        assert evt["ratings"]["safety"] is True
        assert evt["ratings"]["friendliness"] is True
        assert evt["ratings"]["cleanliness"] is False
        assert evt["ratings"]["queer_friendly"] is True
        assert evt["comment"] == "Great place"

    def test_partial_ratings(self):
        """Only rated dimensions should appear in ratings dict."""
        log_location_feedback(
            session_id="sess-2",
            location_id="loc-456",
            safety=True,
        )
        events = get_recent_events(event_type="location_feedback")
        assert len(events) == 1
        assert events[0]["ratings"] == {"safety": True}

    def test_no_ratings(self):
        """All None ratings → empty ratings dict."""
        log_location_feedback(session_id="sess-3", location_id="loc-789")
        events = get_recent_events(event_type="location_feedback")
        assert len(events) == 1
        assert events[0]["ratings"] == {}

    def test_multiple_feedbacks_for_different_locations(self):
        log_location_feedback(session_id="sess-1", location_id="loc-1", safety=True)
        log_location_feedback(session_id="sess-1", location_id="loc-2", safety=False)
        events = get_recent_events(event_type="location_feedback")
        assert len(events) == 2
        assert events[0]["location_id"] == "loc-1"
        assert events[1]["location_id"] == "loc-2"

    def test_feedback_registered_to_conversation(self):
        """Location feedback should appear in the conversation's event list."""
        log_location_feedback(session_id="sess-4", location_id="loc-1", friendliness=True)
        from app.services.audit_log import get_conversation
        events = get_conversation("sess-4")
        assert len(events) == 1
        assert events[0]["type"] == "location_feedback"


# -----------------------------------------------------------------------
# STATS AGGREGATION
# -----------------------------------------------------------------------

class TestLocationFeedbackStats:
    """Test location_feedback_count in get_stats."""

    def setup_method(self):
        clear_audit_log()

    def test_stats_include_count(self):
        log_location_feedback(session_id="sess-1", location_id="loc-1", safety=True)
        log_location_feedback(session_id="sess-2", location_id="loc-2", friendliness=False)
        stats = get_stats()
        assert stats["location_feedback_count"] == 2

    def test_stats_zero_when_none(self):
        stats = get_stats()
        assert stats["location_feedback_count"] == 0
