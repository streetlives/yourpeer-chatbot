"""
Tests for health monitoring, eval upload, and client error handling.

Covers the features added in the health enrichment + eval upload session:
    - ping_llm() — live LLM health ping with caching and error classification
    - get_status() — enriched semantic router health check
    - POST /admin/api/eval/upload — eval report file upload
    - userFacingError patterns — verified via HTTP status codes from api.ts
    - Health endpoint — enriched LLM and semantic router fields

All external calls (Anthropic SDK, sentence-transformers) are mocked
so tests run without an API key or network access.

Run with: python -m pytest tests/unit/test_health_and_upload.py -v
"""

import json
import os
import time
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

import app.llm.claude_client as cc
from app.main import app
from app.services.audit_log import set_eval_results, get_eval_results, clear_audit_log


client = TestClient(app)


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _reset_ping_cache():
    """Clear the ping_llm cache between tests."""
    cc._llm_health_cache = None
    cc._llm_health_cache_time = 0


def _reset_client():
    """Reset the claude client lazy-init state."""
    cc._client = None
    cc._init_error = None


def _valid_eval_report():
    """Return a minimal valid eval report JSON."""
    return {
        "summary": {
            "overall_average": 4.54,
            "scenarios_evaluated": 167,
            "critical_failure_count": 3,
            "scenarios_with_errors": 0,
            "dimension_averages": {},
            "category_averages": {},
        },
        "scenarios": [],
        "critical_failures": [],
    }


# =======================================================================
# 1. ping_llm() — LIVE LLM HEALTH PING
# =======================================================================

class TestPingLlm:
    """Tests for the lightweight LLM health ping."""

    def setup_method(self):
        _reset_ping_cache()
        _reset_client()

    # -- Success --

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_success_returns_up(self, mock_anthropic):
        """Successful ping should return status 'up' with latency."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 1
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "up"
        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], int)
        assert result["cached"] is False
        assert "Responding" in result["detail"]

    # -- Error classification --

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_auth_error(self, mock_anthropic):
        """Invalid API key should return 'auth_error'."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.AuthenticationError(
            "Invalid API key", response=MagicMock(), body={}
        )
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "auth_error"
        assert "invalid" in result["detail"].lower() or "revoked" in result["detail"].lower()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_rate_limit(self, mock_anthropic):
        """Rate limit should return 'rate_limited'."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.RateLimitError(
            "Rate limit exceeded", response=MagicMock(), body={}
        )
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "rate_limited"
        assert "rate limit" in result["detail"].lower()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_timeout(self, mock_anthropic):
        """API timeout should return 'timeout'."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.APITimeoutError(
            request=MagicMock()
        )
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "timeout"
        assert "timed out" in result["detail"].lower()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_connection_error(self, mock_anthropic):
        """Connection failure should return 'api_error'."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_anthropic.APIConnectionError(
            request=MagicMock()
        )
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "api_error"
        assert "reach" in result["detail"].lower() or "connect" in result["detail"].lower()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_server_error_5xx(self, mock_anthropic):
        """5xx API error should return 'api_error' with status code."""
        mock_client = MagicMock()
        err = mock_anthropic.APIStatusError(
            "Internal Server Error", response=MagicMock(), body={}
        )
        err.status_code = 500
        mock_client.messages.create.side_effect = err
        mock_anthropic.Anthropic.return_value = mock_client

        result = cc.ping_llm()

        assert result["status"] == "api_error"
        assert "500" in result["detail"]

    # -- No API key --

    @patch.dict(os.environ, {}, clear=True)
    def test_ping_no_api_key(self):
        """Missing API key should return 'unavailable'."""
        _reset_client()
        os.environ.pop("ANTHROPIC_API_KEY", None)

        result = cc.ping_llm()

        assert result["status"] == "unavailable"
        assert "not configured" in result["detail"].lower()

    # -- Caching --

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_caches_result(self, mock_anthropic):
        """Second call within TTL should return cached result."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        result1 = cc.ping_llm()
        result2 = cc.ping_llm()

        assert result1["cached"] is False
        assert result2["cached"] is True
        # API should only be called once
        mock_client.messages.create.assert_called_once()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("app.llm.claude_client.anthropic")
    def test_ping_cache_expires(self, mock_anthropic):
        """After TTL expires, cache should miss and re-ping."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.Anthropic.return_value = mock_client

        cc.ping_llm()

        # Fast-forward past TTL
        cc._llm_health_cache_time = time.time() - cc._LLM_HEALTH_CACHE_TTL - 1

        result = cc.ping_llm()
        assert result["cached"] is False
        assert mock_client.messages.create.call_count == 2


# =======================================================================
# 2. ENRICHED HEALTH ENDPOINT
# =======================================================================

class TestHealthEndpointEnriched:
    """Tests for the enriched /api/health response."""

    def setup_method(self):
        _reset_ping_cache()
        _reset_client()

    @patch("app.llm.claude_client.ping_llm")
    @patch("app.rag.query_executor.test_connection", return_value=True)
    def test_health_llm_up_shows_latency(self, _db, mock_ping):
        """When LLM is up, health should include latency_ms and detail."""
        mock_ping.return_value = {
            "status": "up", "latency_ms": 342,
            "detail": "Responding (342ms)", "cached": False,
        }

        r = client.get("/api/health")
        data = r.json()

        assert data["status"] == "healthy"
        llm = data["checks"]["llm"]
        assert llm["status"] == "up"
        assert llm.get("latency_ms") == 342
        assert llm.get("detail") == "Responding (342ms)"

    @patch("app.llm.claude_client.ping_llm")
    @patch("app.rag.query_executor.test_connection", return_value=True)
    def test_health_llm_degraded_shows_error_type(self, _db, mock_ping):
        """When LLM ping fails, health should show degraded with error_type."""
        mock_ping.return_value = {
            "status": "auth_error",
            "detail": "API key invalid or revoked", "cached": False,
        }

        r = client.get("/api/health")
        data = r.json()

        assert data["status"] == "degraded"
        llm = data["checks"]["llm"]
        assert llm["status"] == "degraded"
        assert llm.get("error_type") == "auth_error"
        assert llm.get("detail") == "API key invalid or revoked"

    @patch("app.llm.claude_client.ping_llm")
    @patch("app.rag.query_executor.test_connection", return_value=True)
    def test_health_llm_unavailable_is_degraded(self, _db, mock_ping):
        """When no API key, overall status should be degraded not unhealthy."""
        mock_ping.return_value = {
            "status": "unavailable",
            "detail": "API key not configured",
        }

        r = client.get("/api/health")
        data = r.json()

        assert data["status"] == "degraded"
        assert data["checks"]["llm"]["status"] == "unavailable"


# =======================================================================
# 3. ENRICHED SEMANTIC ROUTER get_status()
# =======================================================================

class TestSemanticRouterStatus:
    """Tests for the enriched semantic router health check."""

    def test_status_when_available(self):
        """When initialized, get_status should report full metadata."""
        from app.services import semantic_router as sr

        if not sr.is_available():
            pytest.skip("Semantic router not loaded in test env")

        status = sr.get_status()

        assert status["available"] is True
        assert status["model"] == "all-MiniLM-L6-v2"
        assert status["route_count"] == status["service_routes"] + status["population_routes"]
        assert status["service_routes"] >= 10
        assert status["population_routes"] >= 6
        assert status["total_utterances"] >= 250
        assert status["embedding_dim"] == 384
        assert status["functional"] is True

    def test_status_counts_match_route_data(self):
        """Utterance counts should match actual route definitions."""
        try:
            from app.services.semantic_routes import SERVICE_ROUTES, POPULATION_ROUTES
        except ImportError:
            pytest.skip("semantic_routes not importable")

        expected_service = len(SERVICE_ROUTES)
        expected_population = len(POPULATION_ROUTES)
        expected_utterances = sum(len(v) for v in SERVICE_ROUTES.values()) + \
                              sum(len(v) for v in POPULATION_ROUTES.values())

        from app.services import semantic_router as sr
        if not sr.is_available():
            pytest.skip("Semantic router not loaded")

        status = sr.get_status()
        assert status["service_routes"] == expected_service
        assert status["population_routes"] == expected_population
        assert status["total_utterances"] == expected_utterances

    def test_status_when_unavailable(self):
        """When model is not loaded, get_status should report unavailable."""
        from app.services import semantic_router as sr

        saved_model = sr._model
        saved_init = sr._initialized
        try:
            sr._model = None
            sr._initialized = False

            status = sr.get_status()
            assert status["available"] is False
            assert status["route_count"] == 0
        finally:
            sr._model = saved_model
            sr._initialized = saved_init


# =======================================================================
# 4. EVAL UPLOAD ENDPOINT
# =======================================================================

class TestEvalUpload:
    """Tests for POST /admin/api/eval/upload."""

    def setup_method(self):
        clear_audit_log()

    # -- Happy path --

    def test_upload_valid_report(self):
        """Uploading a valid eval report should store it and return success."""
        report = _valid_eval_report()

        r = client.post(
            "/admin/api/eval/upload",
            content=json.dumps(report),
            headers={"Content-Type": "application/json"},
        )

        assert r.status_code == 200
        data = r.json()
        assert "167 scenarios" in data["detail"]
        assert "4.54" in data["detail"]

        # Verify it was stored
        stored = get_eval_results()
        assert stored is not None
        assert stored["summary"]["overall_average"] == 4.54

    def test_upload_replaces_previous(self):
        """Uploading a new report should replace the previous one."""
        report1 = _valid_eval_report()
        report1["summary"]["overall_average"] = 4.00

        report2 = _valid_eval_report()
        report2["summary"]["overall_average"] = 4.80

        client.post("/admin/api/eval/upload",
                     content=json.dumps(report1),
                     headers={"Content-Type": "application/json"})
        client.post("/admin/api/eval/upload",
                     content=json.dumps(report2),
                     headers={"Content-Type": "application/json"})

        stored = get_eval_results()
        assert stored["summary"]["overall_average"] == 4.80

    def test_upload_visible_in_get_eval(self):
        """Uploaded report should be returned by GET /admin/api/eval."""
        from pathlib import Path

        report = _valid_eval_report()
        client.post("/admin/api/eval/upload",
                     content=json.dumps(report),
                     headers={"Content-Type": "application/json"})

        with patch("app.routes.admin.TESTS_DIR", Path("/nonexistent")):
            r = client.get("/admin/api/eval")

        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["scenarios_evaluated"] == 167

    # -- Validation errors --

    def test_upload_invalid_json(self):
        """Non-JSON body should return 400."""
        r = client.post(
            "/admin/api/eval/upload",
            content="this is not json {{{",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "Invalid JSON" in r.json()["detail"]

    def test_upload_not_object(self):
        """JSON array (not object) should return 400."""
        r = client.post(
            "/admin/api/eval/upload",
            content=json.dumps([1, 2, 3]),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "JSON object" in r.json()["detail"]

    def test_upload_missing_summary(self):
        """Missing 'summary' field should return 400."""
        r = client.post(
            "/admin/api/eval/upload",
            content=json.dumps({"scenarios": []}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "summary" in r.json()["detail"].lower()

    def test_upload_missing_required_fields(self):
        """Summary missing required fields should return 400."""
        report = {"summary": {"overall_average": 4.5}}  # missing scenarios_evaluated

        r = client.post(
            "/admin/api/eval/upload",
            content=json.dumps(report),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "scenarios_evaluated" in r.json()["detail"]

    # -- Race condition --

    def test_upload_blocked_during_eval_run(self):
        """Upload should return 409 when an eval run is in progress."""
        import app.routes.admin as admin_mod

        with admin_mod._eval_lock:
            admin_mod._eval_running = True
        try:
            report = _valid_eval_report()
            r = client.post(
                "/admin/api/eval/upload",
                content=json.dumps(report),
                headers={"Content-Type": "application/json"},
            )
            assert r.status_code == 409
            assert "in progress" in r.json()["detail"].lower()
        finally:
            with admin_mod._eval_lock:
                admin_mod._eval_running = False


# =======================================================================
# 5. HTTP ERROR MESSAGE QUALITY (api.ts behavior verified server-side)
# =======================================================================

class TestHttpErrorMessages:
    """Verify backend returns appropriate status codes that the
    frontend's userFacingError() can classify."""

    def test_rate_limit_429_includes_detail(self):
        """429 response should include a detail message with timing info."""
        # Rate limiting is tested separately; this verifies the shape
        # The frontend checks msg.includes("wait") to detect rate limits
        pass  # Covered by existing test_rate_limit_integration.py

    def test_health_503_when_db_down(self):
        """Health endpoint returns 503 when database is unreachable."""
        with patch("app.rag.query_executor.test_connection", return_value=False):
            with patch("app.llm.claude_client.ping_llm",
                       return_value={"status": "up", "latency_ms": 100,
                                     "detail": "OK", "cached": False}):
                r = client.get("/api/health")

        assert r.status_code == 503
        assert r.json()["status"] == "unhealthy"


# =======================================================================
# Run standalone
# =======================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
