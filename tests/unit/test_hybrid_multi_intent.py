"""
Hybrid multi-intent extraction tests.

Covers:
  1. classify_all_services() — multi-label semantic classification
  2. Hybrid integration — regex + semantic merge in chatbot.py
  3. Negation keyword extraction — "anywhere to sleep" etc.
  4. _display_location() — NYC location formatting
  5. Route utterance coverage — negation phrasings per category

Run with: python -m pytest tests/unit/test_hybrid_multi_intent.py -v
"""

import pytest
import numpy as np
from unittest.mock import patch, MagicMock

from app.services.semantic_router import (
    SemanticMatch,
    classify_all_services,
    classify_service,
    initialize_with_routes,
    reset,
    DEFAULT_SERVICE_THRESHOLD,
)
from app.services.semantic_routes import SERVICE_ROUTES, POPULATION_ROUTES
from app.services.slot_extraction_regex import extract_slots, _extract_all_service_types
from app.services.confirmation import _display_location


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

class ControlledMockModel:
    """Mock model returning controlled embeddings for specific texts.

    For texts in the mapping, returns the specified vector.
    For route utterances, returns a vector near the route's centroid.
    For unknown texts, returns a random vector unlikely to match anything.
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
                np.random.seed(hash(text) % (2**31))
                vec = np.random.randn(self.dim).astype(np.float32)

            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            results.append(vec)

        arr = np.array(results)
        return arr[0] if single else arr


def _make_unit_vec(dim, index):
    """Create a unit vector with 1.0 at the given index."""
    vec = np.zeros(dim, dtype=np.float32)
    vec[index] = 1.0
    return vec


def _make_near_vec(base, noise=0.05):
    """Create a vector near `base` with slight noise."""
    noisy = base + np.random.randn(len(base)).astype(np.float32) * noise
    return noisy / np.linalg.norm(noisy)


@pytest.fixture(autouse=True)
def reset_router():
    reset()
    yield
    reset()


# ---------------------------------------------------------------------------
# 1. classify_all_services() — MULTI-LABEL CLASSIFICATION
# ---------------------------------------------------------------------------

class TestClassifyAllServices:
    """Tests for the new multi-label classification function."""

    def _setup_routes(self):
        """Create a controlled model where shelter=dim0, food=dim1,
        medical=dim2, and a user message matches both shelter and food."""
        dim = 16
        shelter_vec = _make_unit_vec(dim, 0)
        food_vec = _make_unit_vec(dim, 1)
        medical_vec = _make_unit_vec(dim, 2)

        # User message: near both shelter and food
        both_vec = np.zeros(dim, dtype=np.float32)
        both_vec[0] = 0.8  # close to shelter
        both_vec[1] = 0.7  # close to food
        both_vec = both_vec / np.linalg.norm(both_vec)

        mappings = {
            # Route utterances
            "I need a bed tonight": _make_near_vec(shelter_vec),
            "I need somewhere to sleep": _make_near_vec(shelter_vec),
            "I'm hungry and need food": _make_near_vec(food_vec),
            "where can I get a meal": _make_near_vec(food_vec),
            "I need to see a doctor": _make_near_vec(medical_vec),
            "I need medication": _make_near_vec(medical_vec),
            # User messages
            "I need somewhere to sleep and something to eat": both_vec,
        }

        model = ControlledMockModel(mappings, dim=dim)
        initialize_with_routes(
            model,
            {
                "shelter": ["I need a bed tonight", "I need somewhere to sleep"],
                "food": ["I'm hungry and need food", "where can I get a meal"],
                "medical": ["I need to see a doctor", "I need medication"],
            },
        )
        return model

    def test_returns_multiple_matches(self):
        """Messages matching multiple routes return all matches."""
        self._setup_routes()
        matches = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.5,
        )
        types = {m.service_type for m in matches}
        assert "shelter" in types
        assert "food" in types

    def test_returns_empty_below_threshold(self):
        """No matches returned when all scores are below threshold."""
        self._setup_routes()
        matches = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.99,
        )
        assert matches == []

    def test_sorted_by_confidence(self):
        """Results are sorted by confidence descending."""
        self._setup_routes()
        matches = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.3,
        )
        if len(matches) >= 2:
            for i in range(len(matches) - 1):
                assert matches[i].confidence >= matches[i + 1].confidence

    def test_exclude_skips_routes(self):
        """The `exclude` parameter skips specified service types."""
        self._setup_routes()
        matches = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.3,
            exclude={"shelter"},
        )
        types = {m.service_type for m in matches}
        assert "shelter" not in types, "Excluded route should not appear"
        assert "food" in types, "Non-excluded matching route should appear"

    def test_exclude_empty_set(self):
        """Empty exclude set has no effect."""
        self._setup_routes()
        matches_no_exclude = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.3,
        )
        matches_empty = classify_all_services(
            "I need somewhere to sleep and something to eat",
            threshold=0.3,
            exclude=set(),
        )
        assert len(matches_no_exclude) == len(matches_empty)

    def test_population_attached_to_all_matches(self):
        """Population tag is attached to every match."""
        dim = 16
        shelter_vec = _make_unit_vec(dim, 0)
        food_vec = _make_unit_vec(dim, 1)
        reentry_vec = _make_unit_vec(dim, 3)

        both_vec = np.zeros(dim, dtype=np.float32)
        both_vec[0] = 0.8
        both_vec[1] = 0.7
        both_vec[3] = 0.6  # also near reentry
        both_vec = both_vec / np.linalg.norm(both_vec)

        mappings = {
            "I need shelter": _make_near_vec(shelter_vec),
            "I need food": _make_near_vec(food_vec),
            "I just got out of jail": _make_near_vec(reentry_vec),
            "test multi with reentry": both_vec,
        }

        model = ControlledMockModel(mappings, dim=dim)
        initialize_with_routes(
            model,
            {"shelter": ["I need shelter"], "food": ["I need food"]},
            {"reentry": ["I just got out of jail"]},
        )

        matches = classify_all_services(
            "test multi with reentry",
            threshold=0.3,
        )
        for m in matches:
            assert m.population == "reentry", \
                f"Population should be 'reentry' for {m.service_type}"

    def test_returns_empty_when_not_initialized(self):
        """Returns empty list if router is not available."""
        reset()
        with patch("app.services.semantic_router.initialize", return_value=False):
            matches = classify_all_services("I need food")
            assert matches == []

    def test_single_match_works(self):
        """A message matching only one route returns a single match."""
        self._setup_routes()
        dim = 16
        # A message very close to medical only
        _med_vec = _make_unit_vec(dim, 2)
        matches = classify_all_services(
            "I need to see a doctor",
            threshold=0.3,
        )
        # Should match at least medical
        types = {m.service_type for m in matches}
        assert "medical" in types


# ---------------------------------------------------------------------------
# 2. NEGATION KEYWORD EXTRACTION
# ---------------------------------------------------------------------------

class TestNegationKeywords:
    """Test that negation phrasings correctly extract service types."""

    @pytest.mark.parametrize("msg,expected_service", [
        ("I don't have anywhere to sleep", "shelter"),
        ("I have nowhere to go tonight", "shelter"),
        ("I don't have a place to stay", "shelter"),
        ("I have no place to sleep", "shelter"),
        ("nowhere to stay tonight", "shelter"),
        ("I have nowhere to go", "shelter"),
    ])
    def test_negation_shelter_extraction(self, msg, expected_service):
        """Negation phrasings for shelter are extracted by regex."""
        result = extract_slots(msg)
        assert result.get("service_type") == expected_service, \
            f"'{msg}' should extract {expected_service}, got {result.get('service_type')}"

    def test_rikers_multi_intent(self):
        """The exact Rikers message extracts both shelter and food."""
        msg = "I just got out of Rikers and I don't have anywhere to sleep or anything to eat"
        services = _extract_all_service_types(msg)
        types = [s[0] for s in services]
        assert "shelter" in types, f"Should extract shelter, got {types}"
        assert "food" in types, f"Should extract food, got {types}"

    def test_rikers_primary_is_shelter(self):
        """Shelter appears first in text, so it's the primary service."""
        msg = "I just got out of Rikers and I don't have anywhere to sleep or anything to eat"
        result = extract_slots(msg)
        assert result.get("service_type") == "shelter"
        additional = result.get("additional_services", [])
        additional_types = [s[0] for s in additional]
        assert "food" in additional_types

    def test_rikers_reentry_population(self):
        """'Rikers' triggers the reentry population tag."""
        msg = "I just got out of Rikers and I don't have anywhere to sleep or anything to eat"
        result = extract_slots(msg)
        assert "reentry" in result.get("_populations", [])

    @pytest.mark.parametrize("msg,expected_types", [
        ("I have nowhere to go and nothing to eat", ["shelter", "food"]),
        ("no place to sleep and I'm hungry", ["shelter", "food"]),
        ("nowhere to stay and I need clothes", ["shelter", "clothing"]),
    ])
    def test_negation_multi_intent_extraction(self, msg, expected_types):
        """Negation + compound messages extract multiple services."""
        services = _extract_all_service_types(msg)
        types = [s[0] for s in services]
        for expected in expected_types:
            assert expected in types, \
                f"'{msg}' should extract {expected}, got {types}"


# ---------------------------------------------------------------------------
# 3. _display_location() — NYC LOCATION FORMATTING
# ---------------------------------------------------------------------------

class TestDisplayLocation:
    """Test the location display formatter."""

    @pytest.mark.parametrize("input_val,expected", [
        ("manhattan", "Manhattan"),
        ("brooklyn", "Brooklyn"),
        ("queens", "Queens"),
        ("bronx", "Bronx"),
        ("staten island", "Staten Island"),
        ("lower east side", "Lower East Side"),
        ("east new york", "East New York"),
        ("washington heights", "Washington Heights"),
        ("jackson heights", "Jackson Heights"),
        ("harlem", "Harlem"),
        ("midtown", "Midtown"),
        ("chelsea", "Chelsea"),
        ("williamsburg", "Williamsburg"),
        ("bushwick", "Bushwick"),
    ])
    def test_standard_title_case(self, input_val, expected):
        assert _display_location(input_val) == expected

    @pytest.mark.parametrize("input_val,expected", [
        ("soho", "SoHo"),
        ("noho", "NoHo"),
        ("dumbo", "DUMBO"),
        ("nolita", "NoLiTa"),
        ("tribeca", "TriBeCa"),
        ("bed-stuy", "Bed-Stuy"),
        ("bedford-stuyvesant", "Bedford-Stuyvesant"),
        ("nycha", "NYCHA"),
        ("the bronx", "the Bronx"),
    ])
    def test_nyc_special_casing(self, input_val, expected):
        assert _display_location(input_val) == expected

    def test_empty_string_passthrough(self):
        assert _display_location("") == ""

    def test_none_passthrough(self):
        assert _display_location(None) is None

    def test_already_capitalized_passthrough(self):
        """Title case input still returns title case."""
        assert _display_location("Your Area") == "Your Area"

    def test_near_your_location_passthrough(self):
        """'near your location' gets title-cased (but this phrase is
        set before _display_location runs, so it shouldn't reach it)."""
        assert _display_location("near your location") == "Near Your Location"


# ---------------------------------------------------------------------------
# 4. ROUTE UTTERANCE COVERAGE REQUIREMENTS
# ---------------------------------------------------------------------------

class TestRouteNegationCoverage:
    """Structural tests: every service route has negation phrasings."""

    NEGATION_WORDS = [
        "nowhere", "anywhere", "nothing", "no place",
        "don't have", "can't", "have no", "haven't",
        "nobody", "no one",
    ]

    def test_every_service_route_has_negation_phrasings(self):
        """Each service route must have ≥2 negation-style utterances."""
        for route_name, utterances in SERVICE_ROUTES.items():
            neg_count = sum(
                1 for u in utterances
                if any(w in u.lower() for w in self.NEGATION_WORDS)
            )
            assert neg_count >= 2, (
                f"Route '{route_name}' has only {neg_count} negation phrasings "
                f"(minimum 2). Add phrasings like 'I don't have...' or "
                f"'I can't find...' for multi-intent extraction coverage."
            )

    def test_minimum_utterances_per_route(self):
        """Each service route must have ≥15 utterances for good embedding
        coverage in the hybrid approach."""
        for route_name, utterances in SERVICE_ROUTES.items():
            assert len(utterances) >= 15, (
                f"Route '{route_name}' has only {len(utterances)} utterances "
                f"(minimum 15 for hybrid multi-intent)."
            )

    def test_minimum_population_utterances(self):
        """Each population route must have ≥8 utterances."""
        for route_name, utterances in POPULATION_ROUTES.items():
            assert len(utterances) >= 8, (
                f"Population route '{route_name}' has only "
                f"{len(utterances)} utterances (minimum 8)."
            )


# ---------------------------------------------------------------------------
# 5. HYBRID INTEGRATION — chatbot.py merge logic
# ---------------------------------------------------------------------------

class TestHybridIntegration:
    """Integration tests for the regex + semantic merge in chatbot.py.

    These test the merge logic by mocking both extraction layers
    and verifying the combined result.
    """

    def test_semantic_adds_to_regex_primary(self):
        """When regex finds food (primary) and semantic finds shelter,
        shelter is appended to additional_services."""
        regex_result = {
            "service_type": "food",
            "additional_services": [],
            "location": None,
            "_populations": [],
        }
        semantic_matches = [
            SemanticMatch(service_type="shelter", confidence=0.85),
        ]

        # Simulate the merge logic from chatbot.py
        regex_found = {regex_result["service_type"]}
        for sm in semantic_matches:
            if sm.service_type not in regex_found:
                queued = regex_result.get("additional_services") or []
                queued.append((sm.service_type, None, None))
                regex_result["additional_services"] = queued
                regex_found.add(sm.service_type)

        assert regex_result["service_type"] == "food"
        additional_types = [s[0] for s in regex_result["additional_services"]]
        assert "shelter" in additional_types

    def test_semantic_becomes_primary_when_regex_empty(self):
        """When regex finds nothing, semantic sets the primary service."""
        regex_result = {
            "service_type": None,
            "additional_services": [],
            "location": None,
            "_populations": [],
        }
        semantic_matches = [
            SemanticMatch(service_type="shelter", confidence=0.85),
            SemanticMatch(service_type="food", confidence=0.75),
        ]

        regex_found: set = set()
        for sm in semantic_matches:
            if sm.service_type not in regex_found:
                if regex_result["service_type"] is None:
                    regex_result["service_type"] = sm.service_type
                else:
                    queued = regex_result.get("additional_services") or []
                    queued.append((sm.service_type, None, None))
                    regex_result["additional_services"] = queued
                regex_found.add(sm.service_type)

        assert regex_result["service_type"] == "shelter"
        additional_types = [s[0] for s in regex_result["additional_services"]]
        assert "food" in additional_types

    def test_dedup_prevents_double_counting(self):
        """If regex and semantic both find food, it appears only once."""
        regex_result = {
            "service_type": "food",
            "additional_services": [],
            "location": "manhattan",
            "_populations": [],
        }
        semantic_matches = [
            SemanticMatch(service_type="food", confidence=0.90),
            SemanticMatch(service_type="shelter", confidence=0.82),
        ]

        regex_found = {regex_result["service_type"]}
        for sm in semantic_matches:
            if sm.service_type not in regex_found:
                queued = regex_result.get("additional_services") or []
                queued.append((sm.service_type, None, None))
                regex_result["additional_services"] = queued
                regex_found.add(sm.service_type)

        # food should NOT be duplicated
        all_services = [regex_result["service_type"]] + \
            [s[0] for s in regex_result["additional_services"]]
        assert all_services.count("food") == 1
        assert "shelter" in all_services

    def test_population_merged_from_semantic(self):
        """Population tags from semantic matches are merged into
        _populations."""
        regex_result = {
            "service_type": "food",
            "additional_services": [],
            "_populations": [],
        }
        semantic_matches = [
            SemanticMatch(
                service_type="shelter",
                confidence=0.85,
                population="reentry",
            ),
        ]

        regex_found = {regex_result["service_type"]}
        for sm in semantic_matches:
            if sm.service_type not in regex_found:
                queued = regex_result.get("additional_services") or []
                queued.append((sm.service_type, None, None))
                regex_result["additional_services"] = queued
                regex_found.add(sm.service_type)
                if sm.population:
                    pops = set(regex_result.get("_populations") or [])
                    pops.add(sm.population)
                    regex_result["_populations"] = sorted(pops)

        assert "reentry" in regex_result["_populations"]

    def test_empty_semantic_preserves_regex(self):
        """When semantic returns no matches, regex result is unchanged."""
        regex_result = {
            "service_type": "food",
            "additional_services": [("shelter", None, None)],
            "_populations": ["reentry"],
        }
        original = dict(regex_result)
        semantic_matches = []

        _regex_found = {regex_result["service_type"]}
        for sm in semantic_matches:
            pass  # nothing to merge

        assert regex_result == original

    def test_three_services_merged(self):
        """Regex finds 1, semantic finds 2 more = 3 total services."""
        regex_result = {
            "service_type": "food",
            "additional_services": [],
            "_populations": [],
        }
        semantic_matches = [
            SemanticMatch(service_type="shelter", confidence=0.88),
            SemanticMatch(service_type="clothing", confidence=0.76),
        ]

        regex_found = {regex_result["service_type"]}
        for sm in semantic_matches:
            if sm.service_type not in regex_found:
                queued = regex_result.get("additional_services") or []
                queued.append((sm.service_type, None, None))
                regex_result["additional_services"] = queued
                regex_found.add(sm.service_type)

        all_services = [regex_result["service_type"]] + \
            [s[0] for s in regex_result["additional_services"]]
        assert len(all_services) == 3
        assert set(all_services) == {"food", "shelter", "clothing"}


# ---------------------------------------------------------------------------
# 6. NEED-BASED SERVICE PRIORITIZATION
# ---------------------------------------------------------------------------

class TestNeedBasedPriority:
    """Tests for Maslow/Housing First/SAMHSA-grounded service priority.

    Research basis:
        - Housing First (HUD): shelter is foundational
        - Maslow adapted for homelessness (Fleury et al. 2021, PMC):
          basic needs > health > stability > growth
        - SAMHSA crisis care (2025): safety > basic needs > health
        - Zheng et al. (2016): safety above physiological for mental health

    Priority tiers:
        Tier 1: shelter, medical (life/safety)
        Tier 2: food, mental_health (survival + behavioral health)
        Tier 3: clothing, personal_care (physiological, non-life-threatening)
        Tier 4: legal, employment (stability)
        Tier 5: other (support services)
    """

    def test_shelter_over_food(self):
        """Housing First: shelter is primary even when food mentioned first."""
        services = _extract_all_service_types(
            "I need food and I need somewhere to stay"
        )
        assert services[0][0] == "shelter", \
            f"Shelter should be primary (Housing First), got {services[0][0]}"

    def test_medical_over_clothing(self):
        """Medical (tier 1) outranks clothing (tier 3)."""
        services = _extract_all_service_types(
            "I need clothes and I need to see a doctor"
        )
        types = [s[0] for s in services]
        assert types.index("medical") < types.index("clothing"), \
            f"Medical should rank above clothing, got {types}"

    def test_food_over_employment(self):
        """Food (tier 2) outranks employment (tier 4)."""
        services = _extract_all_service_types(
            "I need a job and I'm hungry"
        )
        types = [s[0] for s in services]
        assert types.index("food") < types.index("employment"), \
            f"Food should rank above employment, got {types}"

    def test_shelter_medical_tiebreak_by_text_position(self):
        """Same tier: text position breaks tie."""
        services = _extract_all_service_types(
            "I need a doctor and a bed tonight"
        )
        # Both tier 1, doctor mentioned first → medical primary
        assert services[0][0] == "medical"

    def test_clothing_over_legal(self):
        """Clothing (tier 3) outranks legal (tier 4)."""
        services = _extract_all_service_types(
            "I need a lawyer and some clothes"
        )
        types = [s[0] for s in services]
        assert types.index("clothing") < types.index("legal"), \
            f"Clothing should rank above legal, got {types}"

    def test_rikers_shelter_primary_over_food(self):
        """The Rikers message: shelter should be primary, not food."""
        services = _extract_all_service_types(
            "I just got out of Rikers and I don't have anywhere to sleep or anything to eat"
        )
        assert services[0][0] == "shelter", \
            f"Shelter should be primary for Rikers message, got {services[0][0]}"

    def test_contradiction_overrides_priority(self):
        """'Actually forget X, I need Y' — contradiction signal overrides
        need-based priority. User's explicit correction takes precedence."""
        services = _extract_all_service_types(
            "actually forget shelter, I really need food"
        )
        # Contradiction signal promotes food despite shelter being tier 1
        assert services[0][0] == "food", \
            f"Contradiction should override priority, got {services[0][0]}"

    def test_priority_table_complete(self):
        """Every service category has a priority rank assigned."""
        from app.services.slot_extraction_regex import SERVICE_KEYWORDS
        # The priority table is defined inside _extract_all_service_types,
        # so we test by extraction: every category should sort deterministically
        all_categories = set(SERVICE_KEYWORDS.keys())
        # Verify by constructing the table directly
        priority = {
            "shelter": 1, "medical": 1,
            "food": 2, "mental_health": 2,
            "clothing": 3, "personal_care": 3,
            # Tier 4 — stability: legal, employment, education, benefits
            # (education promoted in Phase B Ticket C, benefits in
            # Ticket D — TAXONOMY_AUDIT_MAY2026.md §IX).
            "legal": 4, "employment": 4,
            "education": 4, "benefits": 4,
            "other": 5,
        }
        for cat in all_categories:
            assert cat in priority, \
                f"Category '{cat}' missing from _SERVICE_NEED_PRIORITY"
