# Test Suite Organization

## Structure

```
tests/
├── conftest.py              # Shared fixtures, helpers (send, send_multi, assert_classified)
│
├── unit/                    # Fast, isolated tests — no DB, no LLM, no network (39 files)
│   ├── test_slot_extractor.py          # Slot extraction (service, location, age, family)
│   ├── test_slot_extraction_keywords.py # Keyword-specific slot extraction
│   ├── test_service_keywords.py        # Service keyword coverage and alignment
│   ├── test_semantic_router.py         # Semantic routing (classification, thresholds, populations)
│   ├── test_gender_extraction.py       # Gender/LGBTQ identity extraction
│   ├── test_populations.py            # Population context extraction and query boosts
│   ├── test_location_boundaries.py     # NYC location parsing edge cases
│   ├── test_contraction_normalization.py # Contraction expansion, intensifier stripping
│   ├── test_phrase_audit.py            # Keyword coverage audits
│   ├── test_pii_redactor.py            # PII detection and redaction
│   ├── test_query_templates.py         # SQL template building and formatting
│   ├── test_crisis_detector.py         # Crisis phrase detection
│   ├── test_post_results.py            # Post-results question classification
│   ├── test_post_results_boundary.py   # Post-results → new-request transitions
│   ├── test_post_results_state.py      # Post-results state management
│   ├── test_results_enhancements.py    # Sort, auto-execute, day detection
│   ├── test_bot_knowledge.py           # Bot knowledge base answers
│   ├── test_audit_log.py               # Audit log writing and stats
│   ├── test_session_store.py           # In-memory session state
│   ├── test_session_token.py           # Session token signing
│   ├── test_rate_limiter.py            # Rate limiting logic
│   ├── test_claude_client.py           # Claude API client mocking
│   ├── test_llm_slot_extractor.py      # LLM-based slot extraction
│   ├── test_llm_classifier.py          # Unified LLM classifier
│   ├── test_llm_multi_service.py       # Multi-service LLM extraction
│   ├── test_narrative_extraction.py    # Long-message narrative handling
│   ├── test_edge_cases.py              # Slot extractor edge cases
│   ├── test_persistence.py             # SQLite pilot persistence
│   ├── test_main.py                    # FastAPI app initialization, health check, CORS
│   ├── test_org_name_search.py         # Organization name search
│   ├── test_service_card_display.py    # Service card rendering, pagination
│   ├── test_walk_in_and_card_extras.py # Walk-in filter, card extras
│   ├── test_location_feedback.py       # Location-specific feedback
│   ├── test_confirmation_flow.py       # Confirmation/denial flow
│   ├── test_change_mind_and_decline.py # Mid-conversation service changes
│   ├── test_core_conversation_flows.py # Core conversation scenarios
│   ├── test_multi_intent_queue.py      # Multi-service queue handling
│   ├── test_response_escalation.py     # Frustration escalation
│   └── test_tone_and_empathy.py        # Tone detection, emotional responses
│
├── integration/             # Multi-component tests — use send(), mock DB/LLM
│   ├── test_chatbot.py                 # Core generate_reply routing (193 tests)
│   ├── test_context_routing.py         # Context-aware yes/no after emotions
│   ├── test_ambiguity_handling.py      # Ambiguous message handling
│   ├── test_crisis_safety_edges.py     # Crisis → service flow transitions
│   ├── test_geolocation.py             # Browser geolocation flow
│   ├── test_chat_route.py              # HTTP /api/chat endpoint
│   ├── test_admin.py                   # Admin API routes
│   ├── test_integration_scenarios.py   # End-to-end conversation flows
│   ├── test_rate_limit_integration.py  # Rate limiting through HTTP
│   ├── test_db_integration.py          # Live database queries
│   │
│   │  # Regression tests (from bug fixes and coverage audits)
│   ├── test_bug_fixes.py               # Bugs 8-14 from PR 19
│   ├── test_structural_fixes.py        # Run 16 failing scenario fixes
│   ├── test_coverage_gaps.py           # Coverage audit fixes
│   ├── test_gap_coverage.py            # Gap analysis fixes
│   └── test_boundary_drift.py          # Boundary condition drift tests
│
└── eval/                    # LLM evaluation (not pytest — run separately)
    └── eval_llm_judge.py               # Scenario-based LLM judge evaluator
```

## Running Tests

```bash
# All tests
pytest

# Unit tests only (fast, no external deps)
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific module
pytest tests/unit/test_slot_extractor.py

# With coverage
pytest tests/unit/ --cov=app.services.slot_extractor
```

## Where to Add New Tests

| You're testing... | Add to... |
|---|---|
| A new slot extractor function | `unit/test_slot_extractor.py` |
| A new phrase list or keyword | `unit/test_phrase_audit.py` |
| Classification (action/tone) | `unit/test_contraction_normalization.py` or add `unit/test_classifier.py` |
| Confirmation message formatting | Add `unit/test_confirmation.py` |
| A new response string | Add `unit/test_responses.py` |
| PII redaction patterns | `unit/test_pii_redactor.py` |
| Crisis detection phrases | `unit/test_crisis_detector.py` |
| Full conversation flow | `integration/test_chatbot.py` or `integration/test_integration_scenarios.py` |
| A bug fix | `integration/test_bug_fixes.py` (add a section for the bug number) |
| Gender/LGBTQ filtering | `unit/test_gender_extraction.py` |

## Source Module → Test File Mapping

| Source module | Primary test file(s) |
|---|---|
| `slot_extractor.py` | `unit/test_slot_extractor.py`, `unit/test_gender_extraction.py`, `unit/test_location_boundaries.py` |
| `classifier.py` | `unit/test_contraction_normalization.py`, `unit/test_phrase_audit.py` |
| `responses.py` | (create `unit/test_responses.py`) |
| `confirmation.py` | (create `unit/test_confirmation.py`) |
| `phrase_lists.py` | `unit/test_phrase_audit.py` |
| `chatbot.py` | `integration/test_chatbot.py`, `integration/test_context_routing.py` |
| `pii_redactor.py` | `unit/test_pii_redactor.py` |
| `query_templates.py` | `unit/test_query_templates.py` |
| `crisis_detector.py` | `unit/test_crisis_detector.py`, `integration/test_crisis_safety_edges.py` |
| `llm_slot_extractor.py` | `unit/test_llm_slot_extractor.py`, `unit/test_narrative_extraction.py` |
| `llm_classifier.py` | `unit/test_llm_classifier.py` |
| `post_results.py` | `unit/test_post_results.py`, `unit/test_post_results_boundary.py` |

## Import Changes (Chatbot Refactor)

The chatbot was split into 5 modules. Test imports were updated:

```python
# Old
from app.services.chatbot import _classify_action, _ESCALATION_RESPONSE

# New
from app.services.classifier import _classify_action
from app.services.responses import _ESCALATION_RESPONSE
```

See `app/services/` for the full module breakdown:
- `phrase_lists.py` — data constants
- `classifier.py` — action/tone classification
- `responses.py` — response strings and prompts
- `confirmation.py` — confirmation messages and quick replies
- `chatbot.py` — routing only
