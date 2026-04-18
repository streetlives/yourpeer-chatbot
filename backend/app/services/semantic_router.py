"""
Semantic Router — Tier 2 classification for YourPeer chatbot.

Sits between the regex keyword matcher (Tier 1) and the LLM fallback
(Tier 3). Converts user messages into 384-dimensional vectors and
compares against pre-embedded example utterances for each service
category.

Two classification modes:
    classify_service()       — Single best match (original, used for
                               fallback when regex found nothing).
    classify_all_services()  — Multi-label: returns ALL routes above
                               threshold. Used for multi-intent extraction
                               alongside regex results (hybrid approach).

This eliminates the "missing keyword" class of failures. When a user
says "I ran out of insulin," the embedding is semantically close to
"I need my medication" and "where can I get a prescription filled" —
even though these phrases share zero keywords.

Performance:
    - Model load + pre-embed routes: ~1-2 seconds (once at startup)
    - Embed user message: ~2-5ms per call
    - Cosine similarity across all routes: <0.1ms per call
    - Memory footprint: ~100 MB

Usage:
    from app.services.semantic_router import (
        classify_service, classify_all_services, is_available,
    )

    if is_available():
        # Single-intent (backward compat)
        match = classify_service("I ran out of insulin")

        # Multi-intent (hybrid with regex)
        regex_found = {"food"}  # regex already found food
        matches = classify_all_services(
            "I need somewhere to sleep and something to eat",
            exclude=regex_found,
        )
        # → [SemanticMatch(service_type="shelter", confidence=0.87)]

Graceful degradation:
    If sentence-transformers is not installed, the module logs a warning
    and all calls to classify_service() return None. The system continues
    to work via Tiers 1 and 3.
"""

import logging
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LAZY IMPORTS — sentence-transformers is optional
# ---------------------------------------------------------------------------

_SENTENCE_TRANSFORMERS_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    _SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    logger.warning(
        "sentence-transformers not installed — semantic routing (Tier 2) "
        "disabled. Install with: pip install sentence-transformers"
    )

# ---------------------------------------------------------------------------
# MODULE STATE
# ---------------------------------------------------------------------------

_model = None
_route_embeddings: dict[str, np.ndarray] = {}  # {"medical": array, "pop_reentry": array, ...}
_initialized = False

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Default confidence thresholds.
# Service routes require higher confidence than population routes because
# misrouting a service type is more costly than missing a population tag.
DEFAULT_SERVICE_THRESHOLD = 0.75
DEFAULT_POPULATION_THRESHOLD = 0.70

# Per-route threshold overrides for categories that are semantically
# close to each other. These can be tuned using eval data.
ROUTE_THRESHOLDS: dict[str, float] = {
    # shelter and housing_assistance are close — require higher confidence
    # to avoid routing "I need help with rent" to shelter
    "housing_assistance": 0.78,
    # "other" is a broad catch-all — require higher confidence to prevent
    # false positives from nonsense/adversarial inputs
    "other": 0.78,
}


@dataclass
class SemanticMatch:
    """Result of semantic classification."""
    service_type: str
    confidence: float
    population: str | None = None
    runner_up_route: str | None = None
    runner_up_confidence: float = 0.0


# ---------------------------------------------------------------------------
# INITIALIZATION
# ---------------------------------------------------------------------------

def initialize() -> bool:
    """Load the embedding model and pre-embed all route utterances.

    Call once at startup. Safe to call multiple times (idempotent).

    Returns True if initialization succeeded, False otherwise.
    """
    global _model, _route_embeddings, _initialized

    if _initialized:
        return True

    if not _SENTENCE_TRANSFORMERS_AVAILABLE:
        logger.warning("Cannot initialize semantic router — sentence-transformers not installed")
        return False

    try:
        start = time.time()

        # Import route definitions
        from app.services.semantic_routes import SERVICE_ROUTES, POPULATION_ROUTES

        # Load model
        _model = SentenceTransformer(MODEL_NAME)
        load_time = time.time() - start
        logger.info(f"Semantic router: model loaded in {load_time:.2f}s")

        # Pre-embed service routes
        embed_start = time.time()
        for route_name, utterances in SERVICE_ROUTES.items():
            embeddings = _model.encode(utterances, normalize_embeddings=True)
            _route_embeddings[route_name] = np.array(embeddings)

        # Pre-embed population routes (prefixed with "pop_")
        for pop_name, utterances in POPULATION_ROUTES.items():
            embeddings = _model.encode(utterances, normalize_embeddings=True)
            _route_embeddings[f"pop_{pop_name}"] = np.array(embeddings)

        embed_time = time.time() - embed_start
        total_time = time.time() - start

        total_utterances = sum(
            len(emb) for emb in _route_embeddings.values()
        )
        logger.info(
            f"Semantic router: {len(_route_embeddings)} routes, "
            f"{total_utterances} utterances embedded in {embed_time:.2f}s "
            f"(total init: {total_time:.2f}s)"
        )

        _initialized = True
        return True

    except Exception as e:
        logger.error(f"Semantic router initialization failed: {e}")
        _model = None
        _route_embeddings = {}
        return False


def is_available() -> bool:
    """Check if the semantic router is ready to classify messages."""
    return _initialized and _model is not None


def get_status() -> dict:
    """Return health-check-friendly status of the semantic router.

    Used by the /api/health endpoint to report component status
    without accessing private module variables.

    Returns:
        available: bool — ready to classify
        model: str | None — model name (e.g., "all-MiniLM-L6-v2")
        route_count: int — total routes (service + population)
        service_routes: int — number of service routes
        population_routes: int — number of population routes
        total_utterances: int — total pre-embedded utterances
        embedding_dim: int | None — embedding dimension (384 for MiniLM)
        functional: bool — True if a test embedding succeeds
    """
    if not is_available():
        return {
            "available": False,
            "model": None,
            "route_count": 0,
        }

    service_count = sum(
        1 for k in _route_embeddings if not k.startswith("pop_")
    )
    pop_count = sum(
        1 for k in _route_embeddings if k.startswith("pop_")
    )
    total_utterances = sum(len(emb) for emb in _route_embeddings.values())

    # Embedding dimension from the first route's embeddings
    embedding_dim = None
    if _route_embeddings:
        first_emb = next(iter(_route_embeddings.values()))
        if len(first_emb) > 0:
            embedding_dim = int(first_emb[0].shape[0])

    # Lightweight functional check — embed a short string and verify shape.
    # Adds ~2ms. Catches model corruption without external dependencies.
    functional = False
    try:
        test_vec = _model.encode("test", normalize_embeddings=True)
        functional = (
            test_vec is not None
            and hasattr(test_vec, "shape")
            and len(test_vec.shape) == 1
            and test_vec.shape[0] == embedding_dim
        )
    except Exception:
        functional = False

    return {
        "available": True,
        "model": MODEL_NAME.split("/")[-1] if _model else None,
        "route_count": len(_route_embeddings),
        "service_routes": service_count,
        "population_routes": pop_count,
        "total_utterances": total_utterances,
        "embedding_dim": embedding_dim,
        "functional": functional,
    }


# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------

def classify_service(
    message: str,
    threshold: float | None = None,
) -> SemanticMatch | None:
    """Classify a message by semantic similarity to route utterances.

    Args:
        message: The user's message text.
        threshold: Override the default service threshold. If None, uses
            DEFAULT_SERVICE_THRESHOLD (with per-route overrides).

    Returns:
        SemanticMatch if a route exceeds the threshold, None otherwise.
        The match includes the best service_type, confidence score,
        and optionally a detected population.
    """
    # Lazy initialization — if not already initialized, try now
    if not _initialized:
        if not initialize():
            return None

    if _model is None:
        return None

    try:
        # Embed the user's message
        query_embedding = _model.encode(message, normalize_embeddings=True)

        # --- Service route matching ---
        best_route = None
        best_score = 0.0
        runner_up_route = None
        runner_up_score = 0.0

        for route_name, route_embeddings in _route_embeddings.items():
            if route_name.startswith("pop_"):
                continue

            # Cosine similarity (embeddings are normalized, so dot product = cosine)
            similarities = np.dot(route_embeddings, query_embedding)
            max_sim = float(np.max(similarities))

            if max_sim > best_score:
                # Current best becomes runner-up
                runner_up_route = best_route
                runner_up_score = best_score
                # New best
                best_route = route_name
                best_score = max_sim
            elif max_sim > runner_up_score:
                runner_up_route = route_name
                runner_up_score = max_sim

        # Apply threshold (per-route or default)
        effective_threshold = threshold
        if effective_threshold is None:
            effective_threshold = ROUTE_THRESHOLDS.get(
                best_route, DEFAULT_SERVICE_THRESHOLD
            )

        if best_score < effective_threshold:
            logger.debug(
                f"Semantic router: best match '{best_route}' "
                f"({best_score:.3f}) below threshold ({effective_threshold})"
            )
            return None

        # --- Population route matching ---
        best_pop = None
        for route_name, route_embeddings in _route_embeddings.items():
            if not route_name.startswith("pop_"):
                continue

            similarities = np.dot(route_embeddings, query_embedding)
            max_sim = float(np.max(similarities))

            if max_sim >= DEFAULT_POPULATION_THRESHOLD:
                pop_name = route_name.replace("pop_", "")
                # Take the highest-scoring population
                if best_pop is None:
                    best_pop = pop_name
                    best_pop_score = max_sim
                elif max_sim > best_pop_score:
                    best_pop = pop_name
                    best_pop_score = max_sim

        match = SemanticMatch(
            service_type=best_route,
            confidence=best_score,
            population=best_pop,
            runner_up_route=runner_up_route,
            runner_up_confidence=runner_up_score,
        )

        logger.info(
            f"Semantic router: '{best_route}' ({best_score:.3f})"
            f"{f', pop={best_pop}' if best_pop else ''}"
            f"{f', runner_up={runner_up_route} ({runner_up_score:.3f})' if runner_up_route else ''}"
        )

        return match

    except Exception as e:
        logger.error(f"Semantic router classification failed: {e}")
        return None


def classify_all_services(
    message: str,
    threshold: float | None = None,
    exclude: set[str] | None = None,
) -> list[SemanticMatch]:
    """Score ALL service routes and return every match above threshold.

    Unlike classify_service() which returns only the single best match,
    this function returns multiple matches — enabling multi-intent
    extraction from compound messages like "I need somewhere to sleep
    and something to eat."

    The embedding is computed once; scoring is cosine similarity against
    each route's pre-embedded utterances (~0.1ms per route).

    Args:
        message: The user's message text.
        threshold: Override the default service threshold. If None, uses
            DEFAULT_SERVICE_THRESHOLD (with per-route overrides).
        exclude: Set of service_type names to skip (e.g., types already
            found by regex). Avoids duplicate work.

    Returns:
        List of SemanticMatch objects, sorted by confidence (highest first).
        Empty list if no routes exceed the threshold.
    """
    if not _initialized:
        if not initialize():
            return []

    if _model is None:
        return []

    exclude = exclude or set()

    try:
        query_embedding = _model.encode(message, normalize_embeddings=True)

        matches: list[SemanticMatch] = []

        for route_name, route_embeddings in _route_embeddings.items():
            if route_name.startswith("pop_"):
                continue
            if route_name in exclude:
                continue

            similarities = np.dot(route_embeddings, query_embedding)
            max_sim = float(np.max(similarities))

            effective_threshold = threshold
            if effective_threshold is None:
                effective_threshold = ROUTE_THRESHOLDS.get(
                    route_name, DEFAULT_SERVICE_THRESHOLD
                )

            if max_sim >= effective_threshold:
                matches.append(SemanticMatch(
                    service_type=route_name,
                    confidence=max_sim,
                ))

        matches.sort(key=lambda m: m.confidence, reverse=True)

        # --- Population matching (same as classify_service) ---
        # When the caller overrides the service threshold (e.g. threshold=0.3
        # in a loose-match context), apply the same override floor to the
        # population threshold so population detection stays in sync with
        # service sensitivity. Without this, a call that widens services
        # still uses the strict 0.70 population default — asymmetry that
        # surprises callers (see test_population_attached_to_all_matches).
        effective_pop_threshold = DEFAULT_POPULATION_THRESHOLD
        if threshold is not None and threshold < DEFAULT_POPULATION_THRESHOLD:
            effective_pop_threshold = threshold

        best_pop = None
        best_pop_score = 0.0
        for route_name, route_embeddings in _route_embeddings.items():
            if not route_name.startswith("pop_"):
                continue
            similarities = np.dot(route_embeddings, query_embedding)
            max_sim = float(np.max(similarities))
            if max_sim >= effective_pop_threshold and max_sim > best_pop_score:
                best_pop = route_name.replace("pop_", "")
                best_pop_score = max_sim

        # Attach population to all matches
        if best_pop:
            for m in matches:
                m.population = best_pop

        if matches:
            route_str = ", ".join(
                f"{m.service_type}({m.confidence:.3f})" for m in matches
            )
            logger.info(
                f"Semantic router (multi): [{route_str}]"
                f"{f', pop={best_pop}' if best_pop else ''}"
            )

        return matches

    except Exception as e:
        logger.error(f"Semantic router multi-classification failed: {e}")
        return []


# ---------------------------------------------------------------------------
# OBSERVABILITY
# ---------------------------------------------------------------------------

def get_diagnostics(message: str) -> dict:
    """Return detailed similarity scores for all routes.

    Useful for debugging and calibrating thresholds. Not called in
    the production path.
    """
    if not is_available():
        return {"error": "Semantic router not available"}

    query_embedding = _model.encode(message, normalize_embeddings=True)

    results = {}
    for route_name, route_embeddings in _route_embeddings.items():
        similarities = np.dot(route_embeddings, query_embedding)
        results[route_name] = {
            "max_similarity": float(np.max(similarities)),
            "mean_similarity": float(np.mean(similarities)),
            "top_3_indices": [int(i) for i in np.argsort(similarities)[-3:][::-1]],
        }

    return dict(sorted(results.items(), key=lambda x: x[1]["max_similarity"], reverse=True))


# ---------------------------------------------------------------------------
# TESTING SUPPORT
# ---------------------------------------------------------------------------

def reset():
    """Reset module state. For testing only."""
    global _model, _route_embeddings, _initialized
    _model = None
    _route_embeddings = {}
    _initialized = False


def initialize_with_routes(
    model,
    service_routes: dict[str, list[str]],
    population_routes: dict[str, list[str]] | None = None,
) -> None:
    """Initialize with a provided model and routes. For testing only.

    Args:
        model: A SentenceTransformer instance (or mock with .encode()).
        service_routes: Dict mapping route names to utterance lists.
        population_routes: Optional dict mapping population names to
            utterance lists.
    """
    global _model, _route_embeddings, _initialized

    _model = model
    _route_embeddings = {}

    for route_name, utterances in service_routes.items():
        embeddings = model.encode(utterances, normalize_embeddings=True)
        _route_embeddings[route_name] = np.array(embeddings)

    if population_routes:
        for pop_name, utterances in population_routes.items():
            embeddings = model.encode(utterances, normalize_embeddings=True)
            _route_embeddings[f"pop_{pop_name}"] = np.array(embeddings)

    _initialized = True
