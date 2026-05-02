"""
Tests for backend/app/utils/time_format.py.

Pin the cross-platform behavior of format_time. The previous in-rag
private helper (`_format_time` in query_templates.py) had no dedicated
tests; its behavior was exercised indirectly via integration tests
that asserted on rendered hours strings. Now that the function lives
in utils/, it has its own test file.
"""

from datetime import time

from app.utils.time_format import format_time


def test_morning_single_digit_hour_strips_leading_zero():
    """9:00 AM should not render as '09:00 AM'.

    Mutant kill: dropping the lstrip('0') would render the leading zero.
    This is the docstring's explicit reason for not using %-I directly.
    """
    assert format_time(time(9, 0)) == "9:00 AM"
    assert format_time(time(8, 30)) == "8:30 AM"


def test_morning_double_digit_hour_keeps_full_format():
    """10:00 AM through 11:59 AM render with both digits."""
    assert format_time(time(10, 0)) == "10:00 AM"
    assert format_time(time(11, 45)) == "11:45 AM"


def test_afternoon_pm_marker():
    """1:00 PM through 11:59 PM render with PM marker, leading zero stripped."""
    assert format_time(time(13, 0)) == "1:00 PM"
    assert format_time(time(13, 30)) == "1:30 PM"
    assert format_time(time(14, 15)) == "2:15 PM"


def test_afternoon_double_digit_hour_pm():
    """10:00 PM and 11:00 PM keep both digits."""
    assert format_time(time(22, 0)) == "10:00 PM"
    assert format_time(time(23, 59)) == "11:59 PM"


def test_midnight():
    """12:00 AM is the documented edge case for the strip-leading-zero logic.

    %I produces '12' for midnight, so lstrip('0') has no effect — the
    output stays as '12:00 AM' rather than getting wrongly stripped to
    '2:00 AM'. Mutant kill: dropping the startswith('0') guard would
    incorrectly strip nothing here either, but keeping the guard makes
    the intent explicit.
    """
    assert format_time(time(0, 0)) == "12:00 AM"


def test_noon():
    """12:00 PM mirrors the midnight case for the PM half of the clock."""
    assert format_time(time(12, 0)) == "12:00 PM"
    assert format_time(time(12, 30)) == "12:30 PM"


def test_minutes_zero_padded():
    """Minutes always render two digits — '9:05 AM', not '9:5 AM'."""
    assert format_time(time(9, 5)) == "9:05 AM"
    assert format_time(time(14, 7)) == "2:07 PM"


def test_returns_string():
    """Return type is str (callers concat with other strings)."""
    result = format_time(time(9, 0))
    assert isinstance(result, str)


def test_round_trip_through_all_hours():
    """Smoke test: every hour of the day produces a valid '<n>:00 <AM|PM>' string."""
    for hour in range(24):
        result = format_time(time(hour, 0))
        # Must end with ' AM' or ' PM'
        assert result.endswith(" AM") or result.endswith(" PM"), result
        # Must contain a colon
        assert ":" in result
        # Hour part must not start with 0 (the whole point of the helper)
        # — except for 12-something which legitimately starts with '12'.
        hour_part = result.split(":")[0]
        assert not hour_part.startswith("0"), f"leading zero in {result!r}"
