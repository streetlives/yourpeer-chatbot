"""Tests for query_executor's DB-unreachable detection.

Covers the May 2026 fix: prior to this change, a TCP-level connect
failure (RDS in maintenance, network partition, host unreachable) was
caught as a generic SQLAlchemy exception and absorbed via `return []`,
which the chatbot then surfaced as "no services found near you" — a
misleading message that suggested the user should adjust their query
instead of retry.

After the fix, those failures raise `DatabaseUnreachableError` so the
orchestrator's existing `_build_db_failure_message` path can produce
an honest "having trouble connecting" message with retry guidance.
"""
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.rag.query_executor import (
    QueryTimeoutError,
    DatabaseUnreachableError,
    _execute_sql,
)


# ---------------------------------------------------------------------------
# Helper — fabricate an OperationalError with a chosen message body
# ---------------------------------------------------------------------------

def _operational_error(message: str) -> OperationalError:
    """Build a SQLAlchemy OperationalError that string-matches `message`.

    SQLAlchemy's OperationalError wraps the underlying psycopg2 error;
    str(err) yields the wrapped message. We don't need a real psycopg2
    error, just an exception whose string representation contains the
    marker tokens our detector looks for.
    """
    return OperationalError(statement="SELECT 1", params={}, orig=Exception(message))


# ---------------------------------------------------------------------------
# Detection — the right exception type for each error class
# ---------------------------------------------------------------------------

class TestDatabaseUnreachableDetection:
    """_execute_sql should raise DatabaseUnreachableError when the
    failure indicates a transport-layer connection problem, distinct
    from QueryTimeoutError (DB reachable, query too slow) and from
    silent return [] (programming bug we don't want to surface)."""

    def _patch_engine_to_fail_with(self, message: str):
        """Returns a context manager that makes engine.connect()/execute()
        raise an OperationalError carrying `message`."""
        err = _operational_error(message)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value.execute.side_effect = err
        return patch("app.rag.query_executor._get_engine", return_value=mock_engine)

    @pytest.mark.parametrize("error_message", [
        # Each of these has been observed in this app's production logs.
        'connection to server at "host" port 5432 failed: Operation timed out',
        "could not connect to server: Connection refused",
        "connection refused",
        "no route to host",
        "could not translate host name",
        "name or service not known",
    ])
    def test_connect_failure_raises_unreachable(self, error_message):
        with self._patch_engine_to_fail_with(error_message):
            with pytest.raises(DatabaseUnreachableError):
                _execute_sql("SELECT 1", {})

    def test_statement_timeout_still_raises_query_timeout(self):
        """Sanity: the QueryTimeoutError detection is unchanged. A query
        that connects but exceeds statement_timeout should still raise
        QueryTimeoutError, NOT DatabaseUnreachableError, because the
        recovery is different (relaxed fallback, not user-facing retry)."""
        with self._patch_engine_to_fail_with(
            "canceling statement due to statement timeout"
        ):
            with pytest.raises(QueryTimeoutError):
                _execute_sql("SELECT 1", {})

    def test_unrelated_query_error_returns_empty(self):
        """Other unexpected errors (programming bugs, schema drift, etc.)
        should still return [] — surfacing them as DatabaseUnreachable
        would mislead the user into retrying when retry won't help."""
        with self._patch_engine_to_fail_with(
            "column 'nonexistent' does not exist"
        ):
            result = _execute_sql("SELECT nonexistent FROM x", {})
            assert result == []

    def test_unreachable_marker_inside_longer_message(self):
        """The detector matches markers anywhere in the error string,
        not just at the start. Real psycopg2 messages include host/port
        details, traceback fragments, and links to docs."""
        with self._patch_engine_to_fail_with(
            'connection to server at "streetlives-prod.cd1mqmjnwg1v.'
            'us-east-1.rds.amazonaws.com" (44.197.82.78), port 5432 failed: '
            "Operation timed out\n\tIs the server running on that host "
            "and accepting TCP/IP connections?"
        ):
            with pytest.raises(DatabaseUnreachableError):
                _execute_sql("SELECT 1", {})


# ---------------------------------------------------------------------------
# Propagation — execute_service_query passes DatabaseUnreachableError up
# ---------------------------------------------------------------------------

class TestUnreachablePropagation:
    """execute_service_query should NOT swallow DatabaseUnreachableError
    by retrying the relaxed fallback (which would just hit the same
    dead host and waste another connect_timeout window). Propagate up
    so the chatbot orchestrator surfaces it."""

    def test_strict_query_unreachable_propagates(self):
        from app.rag.query_executor import execute_service_query
        with patch(
            "app.rag.query_executor._execute_sql",
            side_effect=DatabaseUnreachableError("boom"),
        ):
            with pytest.raises(DatabaseUnreachableError):
                execute_service_query(
                    template_key="food",
                    user_params={"location": "Brooklyn"},
                    max_results=10,
                )

    def test_strict_timeout_then_relaxed_unreachable_propagates(self):
        """Strict times out (relaxed fallback engaged), relaxed hits an
        unreachable error. Should propagate, not absorb to []."""
        from app.rag.query_executor import execute_service_query

        call_count = {"n": 0}

        def alternating(sql, params):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise QueryTimeoutError("statement_timeout")
            raise DatabaseUnreachableError("connection lost")

        with patch(
            "app.rag.query_executor._execute_sql",
            side_effect=alternating,
        ):
            with pytest.raises(DatabaseUnreachableError):
                execute_service_query(
                    template_key="food",
                    user_params={"location": "Brooklyn"},
                    max_results=10,
                )
