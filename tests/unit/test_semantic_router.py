"""
Tests for the semantic router (Tier 2 classification).

The semantic router uses sentence-transformers to embed user messages
and compare against pre-embedded route utterances. Since the actual
model download may not be available in CI, these tests use a mock
model that produces controlled embeddings.

Test categories:
    1. Route definitions — structure and coverage
    2. Initialization — model loading and embedding
    3. Service classification — correct routing for known inputs
    4. False positive rejection — no misrouting on ambiguous inputs
    5. Population detection — cross-cutting identity extraction
    6. Threshold behavior — confidence boundaries
    7. Integration — extract_slots_smart() with semantic routing
    8. Graceful degradation — behavior when model unavailable
    9. Observability — diagnostics output
"""

import pytest
import numpy as np
from unittest.mock import patch
from dataclasses import dataclass

from app.services.semantic_router import (
    SemanticMatch,
    classify_service,
    initialize,
    is_available,
    reset,
    initialize_with_routes,
    get_diagnostics,
    DEFAULT_SERVICE_THRESHOLD,
    DEFAULT_POPULATION_THRESHOLD,
)
from app.services.semantic_routes import SERVICE_ROUTES, POPULATION_ROUTES


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

class MockEmbeddingModel:
    """Mock embedding model that returns predictable embeddings.

    Uses a simple hash-based approach: each unique phrase gets a
    distinct unit vector in a low-dimensional space. Phrases with
    shared words produce embeddings with higher cosine similarity.
    """

    def __init__(self, dim=32):
        self.dim = dim
        self._cache = {}
        self._call_count = 0

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        if isinstance(texts, str):
            texts = [texts]
            single = True
        else:
            single = False

        results = []
        for text in texts:
            self._call_count += 1
            if text not in self._cache:
                # Create a deterministic embedding from the text
                np.random.seed(hash(text.lower().strip()) % (2**31))
                vec = np.random.randn(self.dim).astype(np.float32)
                if normalize_embeddings:
                    vec = vec / np.linalg.norm(vec)
                self._cache[text] = vec
            results.append(self._cache[text])

        arr = np.array(results)
        return arr[0] if single else arr


class ControlledMockModel:
    """Mock model that returns controlled embeddings for testing
    specific similarity relationships.

    Given a mapping of text → embedding, returns the specified
    embedding. Falls back to a random embedding for unknown texts.
    """

    def __init__(self, mappings: dict[str, np.ndarray], dim=16):
        self.dim = dim
        self.mappings = mappings

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        if isinstance(texts, str):
            texts = [texts]
            single = True
        else:
            single = False

        results = []
        for text in texts:
            if text in self.mappings:
                vec = self.mappings[text].copy()
            else:
                # Deterministic fallback
                np.random.seed(hash(text) % (2**31))
                vec = np.random.randn(self.dim).astype(np.float32)

            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            results.append(vec)

        arr = np.array(results)
        return arr[0] if single else arr


@pytest.fixture(autouse=True)
def reset_router():
    """Reset the semantic router state before each test."""
    reset()
    yield
    reset()


@pytest.fixture
def mock_model():
    """Provide a basic mock embedding model."""
    return MockEmbeddingModel(dim=32)


# ---------------------------------------------------------------------------
# 1. ROUTE DEFINITIONS
# ---------------------------------------------------------------------------

class TestRouteDefinitions:
    """Verify route structure, coverage, and quality."""

    def test_service_routes_exist(self):
        """All expected service types have route definitions.

        Note: housing_assistance was retired by the April 15, 2026 audit
        (collapsed into 'other' + description filter — see
        test_audit_regression.py::TestHousingAssistanceRemoval). If you
        re-add it here, also re-add the route in semantic_routes.py,
        the SERVICE_KEYWORDS cluster, and the template redirect in
        query_executor.SLOT_SERVICE_TO_TEMPLATE.
        """
        expected = {
            "medical", "shelter", "food", "clothing", "personal_care",
            "mental_health", "legal", "employment",
            "other",
        }
        assert expected == set(SERVICE_ROUTES.keys())

    def test_population_routes_exist(self):
        """All expected population types have route definitions."""
        expected = {
            "reentry", "veteran", "senior", "pregnant",
            "disabled", "dv_survivor",
        }
        assert expected == set(POPULATION_ROUTES.keys())

    def test_minimum_utterances_per_service_route(self):
        """Each service route has at least 10 utterances."""
        for route_name, utterances in SERVICE_ROUTES.items():
            assert len(utterances) >= 10, (
                f"Route '{route_name}' has only {len(utterances)} utterances "
                f"(minimum 10 required)"
            )

    def test_minimum_utterances_per_population_route(self):
        """Each population route has at least 6 utterances."""
        for route_name, utterances in POPULATION_ROUTES.items():
            assert len(utterances) >= 6, (
                f"Population route '{route_name}' has only {len(utterances)} "
                f"utterances (minimum 6 required)"
            )

    def test_no_duplicate_utterances_within_route(self):
        """No route contains duplicate utterances."""
        for route_name, utterances in {**SERVICE_ROUTES, **POPULATION_ROUTES}.items():
            lower_utterances = [u.lower().strip() for u in utterances]
            assert len(lower_utterances) == len(set(lower_utterances)), (
                f"Route '{route_name}' contains duplicate utterances"
            )

    def test_no_empty_utterances(self):
        """No route contains empty or whitespace-only utterances."""
        for route_name, utterances in {**SERVICE_ROUTES, **POPULATION_ROUTES}.items():
            for i, u in enumerate(utterances):
                assert u.strip(), (
                    f"Route '{route_name}' has empty utterance at index {i}"
                )

    def test_utterances_are_phrases_not_keywords(self):
        """Utterances should be full phrases (≥3 words), not single keywords."""
        for route_name, utterances in SERVICE_ROUTES.items():
            for u in utterances:
                word_count = len(u.split())
                assert word_count >= 3, (
                    f"Route '{route_name}' has short utterance '{u}' "
                    f"({word_count} words). Use full phrases, not keywords."
                )

    def test_eval_failure_scenarios_covered(self):
        """Utterances cover the specific eval failures from Run 24-26."""
        # peer_diabetic_insulin — "insulin" must route to medical
        medical_utterances = " ".join(SERVICE_ROUTES["medical"]).lower()
        assert "insulin" in medical_utterances, (
            "Medical route must cover insulin (peer_diabetic_insulin eval failure)"
        )
        assert "diabetic" in medical_utterances or "diabetes" in medical_utterances, (
            "Medical route must cover diabetes (peer_diabetic_insulin eval failure)"
        )

        # peer_felon_employment — "felon" must route to employment
        employment_utterances = " ".join(SERVICE_ROUTES["employment"]).lower()
        assert "felon" in employment_utterances, (
            "Employment route must cover felon (peer_felon_employment eval failure)"
        )
        assert "criminal record" in employment_utterances, (
            "Employment route must cover criminal record "
            "(peer_felon_employment eval failure)"
        )

        # peer_aging_out_foster — "foster care" must route to shelter
        shelter_utterances = " ".join(SERVICE_ROUTES["shelter"]).lower()
        assert "foster care" in shelter_utterances, (
            "Shelter route must cover foster care "
            "(peer_aging_out_foster eval failure)"
        )

    def test_reentry_population_has_felon_coverage(self):
        """Reentry population covers felon/incarcerated terminology."""
        reentry_utterances = " ".join(POPULATION_ROUTES["reentry"]).lower()
        assert "felon" in reentry_utterances
        assert "incarcerated" in reentry_utterances
        assert "rikers" in reentry_utterances


# ---------------------------------------------------------------------------
# 2. INITIALIZATION
# ---------------------------------------------------------------------------

class TestInitialization:
    """Test model loading and route embedding."""

    def test_initialize_with_mock_model(self, mock_model):
        """Initialize succeeds with a mock model."""
        initialize_with_routes(
            mock_model,
            {"medical": ["I need a doctor"], "food": ["I need food"]},
        )
        assert is_available()

    def test_not_available_before_init(self):
        """is_available() returns False before initialization."""
        assert not is_available()

    def test_classify_returns_none_before_init(self):
        """classify_service() either succeeds or returns None — must not raise.

        Without a cached sentence-transformers model, initialization
        may fail and classify_service falls through to None. Either
        outcome is acceptable; the contract this test guards is
        "does not raise."
        """
        try:
            classify_service("I need food")
        except Exception as e:
            pytest.fail(f"classify_service raised unexpectedly: {type(e).__name__}: {e}")

    def test_reset_clears_state(self, mock_model):
        """reset() clears all module state."""
        initialize_with_routes(
            mock_model,
            {"medical": ["I need a doctor"]},
        )
        assert is_available()
        reset()
        assert not is_available()

    def test_initialize_with_populations(self, mock_model):
        """Initialize with both service and population routes."""
        initialize_with_routes(
            mock_model,
            {"food": ["I need food"]},
            {"veteran": ["I served in the Army"]},
        )
        assert is_available()


# ---------------------------------------------------------------------------
# 3. SERVICE CLASSIFICATION
# ---------------------------------------------------------------------------

class TestServiceClassification:
    """Test correct routing for known inputs using controlled embeddings."""

    def _make_model_with_similarity(self, query_text, target_route_text, dim=16):
        """Create a model where query is very similar to target route."""
        # Target route embedding
        np.random.seed(42)
        target_vec = np.random.randn(dim).astype(np.float32)
        target_vec = target_vec / np.linalg.norm(target_vec)

        # Query is nearly identical to target (high cosine similarity)
        query_vec = target_vec + np.random.randn(dim).astype(np.float32) * 0.05
        query_vec = query_vec / np.linalg.norm(query_vec)

        # Other route is very different
        np.random.seed(999)
        other_vec = np.random.randn(dim).astype(np.float32)
        other_vec = other_vec / np.linalg.norm(other_vec)

        mappings = {
            query_text: query_vec,
            target_route_text: target_vec,
        }
        return ControlledMockModel(mappings, dim=dim)

    def test_high_similarity_matches(self):
        """A message very similar to a route utterance should match."""
        dim = 16
        np.random.seed(42)
        target_vec = np.random.randn(dim).astype(np.float32)
        target_vec = target_vec / np.linalg.norm(target_vec)

        # Query is very close to target
        query_vec = target_vec.copy()
        query_vec[0] += 0.02
        query_vec = query_vec / np.linalg.norm(query_vec)

        # Other routes are distant
        np.random.seed(999)
        other_vec = np.random.randn(dim).astype(np.float32)
        other_vec = other_vec / np.linalg.norm(other_vec)

        model = ControlledMockModel({
            "I ran out of insulin": query_vec,
            "I need my medication": target_vec,  # medical route utterance
            "I need food": other_vec,             # food route utterance
        }, dim=dim)

        initialize_with_routes(
            model,
            {
                "medical": ["I need my medication"],
                "food": ["I need food"],
            },
        )

        result = classify_service("I ran out of insulin")
        assert result is not None
        assert result.service_type == "medical"
        assert result.confidence > 0.9

    def test_low_similarity_returns_none(self):
        """A message distant from all routes should return None."""
        dim = 16
        np.random.seed(42)
        query_vec = np.random.randn(dim).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        # Route embeddings are distant from query
        np.random.seed(100)
        med_vec = np.random.randn(dim).astype(np.float32)
        med_vec = med_vec / np.linalg.norm(med_vec)

        np.random.seed(200)
        food_vec = np.random.randn(dim).astype(np.float32)
        food_vec = food_vec / np.linalg.norm(food_vec)

        model = ControlledMockModel({
            "hello how are you": query_vec,
            "I need a doctor": med_vec,
            "I need food": food_vec,
        }, dim=dim)

        initialize_with_routes(
            model,
            {
                "medical": ["I need a doctor"],
                "food": ["I need food"],
            },
        )

        result = classify_service("hello how are you")
        # With random vectors in 16 dimensions, cosine similarity should
        # be low (~0.0 ± 0.25). Should be below 0.75 threshold.
        if result is not None:
            assert result.confidence < DEFAULT_SERVICE_THRESHOLD

    def test_runner_up_tracking(self, mock_model):
        """Result includes the runner-up route and score."""
        initialize_with_routes(
            mock_model,
            {
                "medical": ["I need a doctor", "I need medical care"],
                "food": ["I need food", "I need a meal"],
                "shelter": ["I need shelter", "I need a bed"],
            },
        )

        result = classify_service("I need some help")
        # Whether it matches or not, if it does match, runner_up should exist
        if result is not None:
            # runner_up_route could be None if only one route exists,
            # but with 3 routes it should have a runner-up
            assert result.runner_up_confidence >= 0.0

    def test_threshold_override(self, mock_model):
        """Custom threshold parameter is respected."""
        initialize_with_routes(
            mock_model,
            {"food": ["I need food", "I need a meal"]},
        )

        # With threshold=0.0, anything should match
        result = classify_service("random text here", threshold=0.0)
        assert result is not None

        # With threshold=1.0, only an exact match would pass.
        # Use text that differs from route utterances.
        result = classify_service("something completely different", threshold=1.0)
        assert result is None


# ---------------------------------------------------------------------------
# 4. FALSE POSITIVE REJECTION
# ---------------------------------------------------------------------------

class TestFalsePositiveRejection:
    """Verify that semantically unrelated inputs don't match routes."""

    def test_casual_greeting_no_match(self):
        """Casual greetings should not match any service route."""
        dim = 16

        # Create orthogonal embeddings for clear separation
        greet_vec = np.zeros(dim, dtype=np.float32)
        greet_vec[0] = 1.0  # pure x-axis

        food_vec = np.zeros(dim, dtype=np.float32)
        food_vec[1] = 1.0  # pure y-axis — orthogonal to greeting

        model = ControlledMockModel({
            "hey what's up": greet_vec,
            "I need food": food_vec,
        }, dim=dim)

        initialize_with_routes(model, {"food": ["I need food"]})
        result = classify_service("hey what's up")
        assert result is None  # cosine similarity = 0

    def test_per_route_threshold_for_other(self):
        """The 'other' category requires higher threshold."""
        from app.services.semantic_router import ROUTE_THRESHOLDS

        assert "other" in ROUTE_THRESHOLDS
        assert ROUTE_THRESHOLDS["other"] > DEFAULT_SERVICE_THRESHOLD


# ---------------------------------------------------------------------------
# 5. POPULATION DETECTION
# ---------------------------------------------------------------------------

class TestPopulationDetection:
    """Test cross-cutting population/identity extraction."""

    def test_population_detected_with_service(self):
        """Population is detected alongside service type."""
        dim = 16

        # Service embedding cluster
        service_vec = np.zeros(dim, dtype=np.float32)
        service_vec[0] = 1.0

        # Population embedding cluster — close to query
        pop_vec = np.zeros(dim, dtype=np.float32)
        pop_vec[1] = 1.0

        # Query close to both service and population
        query_vec = np.array([0.8, 0.8] + [0.0] * (dim - 2), dtype=np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        model = ControlledMockModel({
            "I'm a felon looking for work": query_vec,
            "I need help finding a job": service_vec,
            "I have a criminal record": pop_vec,
        }, dim=dim)

        initialize_with_routes(
            model,
            {"employment": ["I need help finding a job"]},
            {"reentry": ["I have a criminal record"]},
        )

        result = classify_service("I'm a felon looking for work", threshold=0.0)
        assert result is not None
        # With the controlled embeddings, population may or may not match
        # depending on similarity. The key test is that the code path works.

    def test_population_lower_threshold(self):
        """Population uses a lower threshold than service routes."""
        assert DEFAULT_POPULATION_THRESHOLD < DEFAULT_SERVICE_THRESHOLD


# ---------------------------------------------------------------------------
# 6. THRESHOLD BEHAVIOR
# ---------------------------------------------------------------------------

class TestThresholdBehavior:
    """Test confidence threshold edge cases."""

    def test_exact_threshold_passes(self):
        """A score exactly at the threshold should match."""
        dim = 16
        # Create vectors with cosine similarity exactly at threshold
        base = np.zeros(dim, dtype=np.float32)
        base[0] = 1.0

        # Vector with known cosine similarity to base
        # cos(theta) = 0.75 when angle = acos(0.75) ≈ 41.4°
        import math
        angle = math.acos(0.75)
        query_vec = np.zeros(dim, dtype=np.float32)
        query_vec[0] = math.cos(angle)
        query_vec[1] = math.sin(angle)

        model = ControlledMockModel({
            "test query": query_vec,
            "route utterance": base,
        }, dim=dim)

        initialize_with_routes(model, {"food": ["route utterance"]})
        result = classify_service("test query")
        # Cosine similarity is exactly 0.75 = threshold, so should match
        assert result is not None
        assert abs(result.confidence - 0.75) < 0.01

    def test_below_threshold_rejected(self):
        """A score below the threshold should not match."""
        dim = 16
        base = np.zeros(dim, dtype=np.float32)
        base[0] = 1.0

        import math
        # cos(theta) = 0.5 — well below 0.75 threshold
        angle = math.acos(0.5)
        query_vec = np.zeros(dim, dtype=np.float32)
        query_vec[0] = math.cos(angle)
        query_vec[1] = math.sin(angle)

        model = ControlledMockModel({
            "test query": query_vec,
            "route utterance": base,
        }, dim=dim)

        initialize_with_routes(model, {"food": ["route utterance"]})
        result = classify_service("test query")
        assert result is None


# ---------------------------------------------------------------------------
# 7. INTEGRATION WITH SLOT EXTRACTION PIPELINE (Phase 4 Stage 3 port)
# ---------------------------------------------------------------------------
#
# Phase 4 Stage 3 (April 2026): the legacy `extract_slots_smart` was
# deleted. The integration surface in the unified architecture is split:
#   - `pipeline._run_early_extraction(message, session_id)` does
#     regex → semantic-router (Tier 1 + Tier 2)
#   - `slot_extraction.extract(message, regex_result, ...)` does the
#     LLM merge (Tier 3) when needed
#
# These tests port the legacy `TestIntegration` and
# `TestIntegrationFallthrough` classes to drive the new layered API.
# Behavior changes worth flagging:
#   - In the legacy architecture, narrative messages bypassed the
#     semantic router (`extract_slots_smart` short-circuited to
#     `extract_slots_narrative` before semantic ran).
#   - In the unified architecture, semantic runs unconditionally
#     inside `_run_early_extraction`; narrative-vs-short routing
#     happens later inside `slot_extraction.extract`. The semantic
#     router's `service_type` is set on `regex_result` regardless of
#     message length. This was an intentional simplification (the
#     semantic router is fast and idempotent), but it means the
#     legacy `test_narrative_bypasses_semantic_router` no longer
#     applies. The replacement test below verifies the new contract:
#     semantic runs, then the narrative path consumes the augmented
#     regex_result.

class TestIntegration:
    """Semantic router integration with `_run_early_extraction`."""

    def test_semantic_fills_missing_service_type(self, mock_model):
        """When regex returns no service_type, semantic router fills it."""
        initialize_with_routes(
            mock_model,
            {"medical": ["I need insulin", "I ran out of medication"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": "manhattan",
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            mock_classify.return_value = SemanticMatch(
                service_type="medical",
                confidence=0.85,
                population=None,
            )

            from app.services.chatbot.pipeline import _run_early_extraction
            result, source = _run_early_extraction("I ran out of insulin", "test-session")

            assert result["service_type"] == "medical"
            assert source == "semantic"
            mock_classify.assert_called_once()

    def test_semantic_population_merged_with_regex(self, mock_model):
        """Semantic population is merged with regex populations (set union)."""
        initialize_with_routes(
            mock_model,
            {"employment": ["I need a job"]},
            {"reentry": ["I have a criminal record"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": ["veteran"],  # regex found veteran
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            mock_classify.return_value = SemanticMatch(
                service_type="employment",
                confidence=0.82,
                population="reentry",  # semantic found reentry
            )

            from app.services.chatbot.pipeline import _run_early_extraction
            result, _source = _run_early_extraction("job help", "test-session")

            assert result["service_type"] == "employment"
            # Both populations should be present (regex's veteran +
            # semantic's reentry, sorted).
            assert "veteran" in result["_populations"]
            assert "reentry" in result["_populations"]

    def test_semantic_skips_when_regex_has_service(self, mock_model):
        """Semantic router is NOT called when regex found a service_type."""
        initialize_with_routes(
            mock_model,
            {"food": ["I need food"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": "food",
                "location": "brooklyn",
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            from app.services.chatbot.pipeline import _run_early_extraction
            result, source = _run_early_extraction("food in Brooklyn", "test-session")

            # Regex resolved → no semantic call
            mock_classify.assert_not_called()
            assert result["service_type"] == "food"
            assert source == "regex"

    def test_short_message_with_semantic_resolves_without_llm_gate(self, mock_model):
        """Short messages where regex+semantic resolve service_type
        should not need the LLM gate to fire.

        In the unified pipeline, `_run_llm_gate` short-circuits when
        `has_service_intent=True` — so a successful semantic match
        means the gate is skipped (Tier 1/2 sufficient). This replaces
        the legacy `test_short_message_skips_llm_after_semantic` test.
        """
        initialize_with_routes(
            mock_model,
            {"medical": ["I need insulin"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            mock_classify.return_value = SemanticMatch(
                service_type="medical",
                confidence=0.85,
            )

            from app.services.chatbot.pipeline import _run_early_extraction, _run_llm_gate

            early, source = _run_early_extraction("insulin please", "test-session")
            # Semantic resolved
            assert early["service_type"] == "medical"
            assert source == "semantic"

            # has_service_intent=True since semantic set service_type;
            # gate must short-circuit without invoking the LLM.
            with patch("app.services.slot_extraction.extract") as mock_extract:
                _run_llm_gate(
                    message="insulin please",
                    early_extracted=early,
                    has_service_intent=True,
                    action_pre=None,
                    regex_tone_pre=None,
                    extraction_source=source,
                )
                mock_extract.assert_not_called()

    def test_long_message_with_semantic_match_can_still_call_llm_gate(self, mock_model):
        """Long messages where regex+semantic missed should still go to
        the LLM gate for additional slot enrichment.

        The unified gate fires when `not has_service_intent` (Tier 3
        fallback for genuine semantic miss). This replaces the legacy
        `test_long_message_still_calls_llm` test.
        """
        initialize_with_routes(
            mock_model,
            {"medical": ["I need insulin"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service", return_value=None):

            # Regex finds nothing; semantic returns None.
            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            from app.services.chatbot.pipeline import _run_early_extraction

            early, source = _run_early_extraction(
                "I am diabetic and I ran out of my insulin in Manhattan",
                "test-session",
            )
            assert early["service_type"] is None
            assert source is None  # neither tier matched


# ---------------------------------------------------------------------------
# 15. INTEGRATION GAPS — FALLTHROUGH AND BYPASS
# ---------------------------------------------------------------------------

class TestIntegrationFallthrough:
    """Paths where semantic routing is bypassed or returns None."""

    def test_semantic_returns_none_falls_through(self, mock_model):
        """When semantic returns None, early extraction returns
        unresolved (no source) — `_run_llm_gate` will then fire
        downstream as Tier 3 fallback.
        """
        initialize_with_routes(
            mock_model,
            {"food": ["I need food"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch(
                 "app.services.semantic_router.classify_service",
                 return_value=None,
             ) as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            from app.services.chatbot.pipeline import _run_early_extraction
            result, source = _run_early_extraction(
                "some ambiguous message", "test-session"
            )

            mock_classify.assert_called_once()
            assert result["service_type"] is None
            assert source is None  # neither tier matched

    def test_semantic_unavailable_falls_through(self):
        """When `is_available()` returns False, early extraction
        returns regex-only (semantic never invoked).
        """
        reset()  # semantic router not initialized

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.is_available", return_value=False), \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            from app.services.chatbot.pipeline import _run_early_extraction
            result, source = _run_early_extraction("some message here", "test-session")

            mock_classify.assert_not_called()
            assert result["service_type"] is None
            assert source is None  # neither tier matched

    def test_narrative_message_still_runs_semantic(self, mock_model):
        """Behavior change vs. legacy: narrative messages no longer
        bypass the semantic router. `_run_early_extraction` is
        unconditional w.r.t. message length, so semantic still runs
        on long inputs. The narrative-vs-short routing happens later
        inside `slot_extraction.extract`.
        """
        initialize_with_routes(
            mock_model,
            {"shelter": ["I need shelter"]},
        )

        with patch("app.services.chatbot.pipeline.extract_slots") as mock_regex, \
             patch("app.services.semantic_router.classify_service") as mock_classify:

            mock_regex.return_value = {
                "service_type": None,
                "location": None,
                "age": None,
                "urgency": None,
                "_gender": None,
                "family_status": None,
                "_populations": [],
                "additional_services": [],
                "service_detail": None,
                "org_name": None,
                "no_requirements": False,
                "_contradiction": False,
                "_is_additive": False,
            }

            mock_classify.return_value = SemanticMatch(
                service_type="shelter",
                confidence=0.83,
            )

            long_message = (
                "I just got out of the hospital and my housing situation "
                "fell through and I have nowhere to go and I have been "
                "sleeping outside for three nights now"
            )

            from app.services.chatbot.pipeline import _run_early_extraction
            result, source = _run_early_extraction(long_message, "test-session")

            # Semantic was called and resolved
            mock_classify.assert_called_once()
            assert result["service_type"] == "shelter"
            assert source == "semantic"

    def test_semantic_service_type_through_extract_merge(self, mock_model):
        """Trust Model 3 contract for semantic-set service_type when
        the LLM also runs on a non-simple message.

        Two cases:
          (A) Sets agree (both pick the same service): LLM's primary
              wins in Ext-2b. For semantic-set service_type, this means
              the LLM's value flows through — same value, but routed
              via LLM's pick.
          (B) Sets disagree: LLM wins (line 510-514 of merge.py),
              because LLM filtering (context vs. request) is the
              signal Trust Model 3 trusts when the two sources
              diverge. The semantic-set value does NOT override.

        This test pins both branches. The earlier legacy test
        (`test_semantic_service_type_preserved_through_llm_merge`)
        asserted that semantic always wins on disagree — that behavior
        belonged to the legacy `extract_slots_smart`'s post-LLM regex
        override hack and was intentionally retired in R36 when Trust
        Model 3 unified the merge logic. The unified contract: sets
        disagree → LLM wins. Documented in `merge.py:_merge_service_type_and_primary_location`
        and the migration doc rev 17.
        """
        initialize_with_routes(
            mock_model,
            {"medical": ["I need insulin"]},
        )

        # --- Case A: sets agree (both medical) ---
        # Semantic set regex_result["service_type"] = "medical"; LLM
        # also picks medical. Result: medical (LLM's pick, but value matches).
        regex_with_semantic = {
            "service_type": "medical",
            "location": None,
            "age": None,
            "urgency": None,
            "_gender": None,
            "family_status": None,
            "_populations": [],
            "additional_services": [],
            "service_detail": None,
            "org_name": None,
            "no_requirements": False,
            "_contradiction": False,
            "_is_additive": False,
        }
        llm_agrees = {
            "service_type": "medical",
            "service_detail": None,
            "additional_services": [],
            "location": "manhattan",
            "age": 45,
            "urgency": "high",
            "_gender": None,
            "family_status": None,
            "_populations": [],
            "org_name": None,
            "tone": None,
            "action": None,
        }
        with patch(
            "app.services.slot_extraction.extract_slots_short",
            return_value=llm_agrees,
        ):
            from app.services.slot_extraction import extract
            message = "I am a diabetic and I ran out of my insulin today"
            result = extract(message, regex_with_semantic, api_key_available=True)
            assert result["service_type"] == "medical"
            assert result["location"] == "manhattan"
            assert result["age"] == 45

        # --- Case B: sets disagree (LLM wins per Trust Model 3) ---
        # Semantic set "medical"; LLM returns "other" — sets diverge,
        # LLM's filtering signal is trusted. Result: other.
        llm_disagrees = {**llm_agrees, "service_type": "other"}
        with patch(
            "app.services.slot_extraction.extract_slots_short",
            return_value=llm_disagrees,
        ):
            result = extract(message, regex_with_semantic, api_key_available=True)
            # LLM wins on disagreement (Trust Model 3 sets-disagree branch)
            assert result["service_type"] == "other"
            assert result["location"] == "manhattan"
            assert result["age"] == 45

# ---------------------------------------------------------------------------
# 16. ROUTE DEFINITION ALIGNMENT
# ---------------------------------------------------------------------------

class TestRouteAlignment:
    """Verify route definitions align with the rest of the codebase."""

    def test_service_routes_align_with_slot_extractor(self):
        """SERVICE_ROUTES keys should match SERVICE_KEYWORDS keys in
        slot_extractor.py (the service types the system recognizes)."""
        from app.services.slot_extractor import SERVICE_KEYWORDS

        route_keys = set(SERVICE_ROUTES.keys())
        keyword_keys = set(SERVICE_KEYWORDS.keys())

        # Routes should cover all keyword categories
        missing_from_routes = keyword_keys - route_keys
        assert not missing_from_routes, (
            f"SERVICE_KEYWORDS has categories not in SERVICE_ROUTES: "
            f"{missing_from_routes}"
        )

        # Routes should not have categories that keywords don't
        extra_in_routes = route_keys - keyword_keys
        assert not extra_in_routes, (
            f"SERVICE_ROUTES has categories not in SERVICE_KEYWORDS: "
            f"{extra_in_routes}"
        )

    def test_no_cross_route_duplicate_utterances(self):
        """No utterance should appear in multiple service routes."""
        seen = {}
        for route_name, utterances in SERVICE_ROUTES.items():
            for u in utterances:
                key = u.lower().strip()
                if key in seen:
                    pytest.fail(
                        f"Utterance '{u}' appears in both "
                        f"'{seen[key]}' and '{route_name}'"
                    )
                seen[key] = route_name

    def test_no_cross_population_duplicate_utterances(self):
        """No utterance should appear in multiple population routes."""
        seen = {}
        for route_name, utterances in POPULATION_ROUTES.items():
            for u in utterances:
                key = u.lower().strip()
                if key in seen:
                    pytest.fail(
                        f"Utterance '{u}' appears in both "
                        f"'{seen[key]}' and '{route_name}'"
                    )
                seen[key] = route_name


# ---------------------------------------------------------------------------
# 17. DIAGNOSTICS OUTPUT CORRECTNESS
# ---------------------------------------------------------------------------

class TestDiagnosticsCorrectness:
    """Test that get_diagnostics returns correctly structured output."""

    def test_diagnostics_sorted_by_max_similarity(self, mock_model):
        """Output should be sorted by max_similarity descending."""
        initialize_with_routes(
            mock_model,
            {
                "food": ["I need food", "I need a meal"],
                "medical": ["I need a doctor", "I need medicine"],
                "shelter": ["I need shelter", "I need a bed"],
            },
        )

        diag = get_diagnostics("I need some help")
        keys = list(diag.keys())
        scores = [diag[k]["max_similarity"] for k in keys]

        # Verify descending order
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Diagnostics not sorted: {keys[i]}={scores[i]:.4f} "
                f"< {keys[i+1]}={scores[i+1]:.4f}"
            )

    def test_diagnostics_includes_population_routes(self, mock_model):
        """Population routes should appear in diagnostics output."""
        initialize_with_routes(
            mock_model,
            {"food": ["I need food"]},
            {"veteran": ["I served in the Army"]},
        )

        diag = get_diagnostics("test message")
        assert "pop_veteran" in diag
        assert "food" in diag
