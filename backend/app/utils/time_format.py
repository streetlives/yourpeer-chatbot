"""
Time formatting utilities.

Small, side-effect-free helpers for rendering ``datetime.time`` values
into user-facing strings. Lives in ``app/utils`` so any layer (rag,
chatbot, handlers) can import without a layering smell — previously
``format_time`` lived in ``app/rag/query_templates.py`` as a private
helper but was imported by ``app/services/chatbot/handlers/post_results.py``,
which crossed the rag → chatbot boundary.
"""

from datetime import datetime


def format_time(t) -> str:
    """Format a ``datetime.time`` as a 12-hour clock string ('9:00 AM').

    Cross-platform note: Python's strftime offers ``%-I`` (no leading zero)
    on macOS/Linux glibc but raises on Windows and on some musl libc
    distributions. To stay portable we use the zero-padded ``%I`` and
    strip the leading zero manually. This trades two extra lines for
    "works everywhere our backend might run".

    Example:
        >>> from datetime import time
        >>> format_time(time(9, 0))
        '9:00 AM'
        >>> format_time(time(13, 30))
        '1:30 PM'
        >>> format_time(time(0, 0))
        '12:00 AM'
    """
    formatted = datetime.combine(datetime.min, t).strftime("%I:%M %p")
    return formatted.lstrip("0") if formatted.startswith("0") else formatted
