# Test Suite Organization

## Structure

```
tests/
├── conftest.py              # Shared fixtures, helpers (send, send_multi, assert_classified)
│
├── unit/                    # Fast, isolated tests — no DB, no LLM, no network (42 files)
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
│   ├── test_tone_and_empathy.py        # Tone detection, emotional responses
│   ├── test_safety_identity_and_tone.py # PII warnings, identity, crisis categories
│   ├── test_group_fixes_regression.py  # 117 collision regression tests (Groups A-H)
│   ├── test_medical_urgency.py         # Medical urgency detection (43 tests)
│   └── test_parser_collision_guards.py # Parser collision guards
│
├── integration/             # Multi-component tests — use send(), mock DB/LLM (17 files)
│   ├── test_classification_and_routing.py  # Core generate_reply routing
│   ├── test_multi_turn_and_context.py      # Context-aware yes/no after emotions
│   ├── test_browser_geolocation.py         # Browser geolocation flow
│   ├── test_http_routes_and_models.py      # HTTP /chat/ endpoint, Pydantic models
│   ├── test_admin_api_routes.py            # Admin API routes
│   ├── test_narrative_and_eval_scenarios.py # End-to-end conversation flows
│   ├── test_service_data_llm_firewall.py   # LLM isolation — 57 tests across 7 layers
│   ├── test_db_integration.py              # Live database queries
│   │
│   │  # Regression tests (from bug fixes and coverage audits)
│   ├── test_targeted_bug_regressions.py    # Bugs 8-14 from PR 19
│   ├── test_crisis_and_flow_regressions.py # Run 16 failing scenario fixes
│   ├── test_utility_and_session_edges.py   # Coverage audit fixes
│   ├── test_format_pipeline_and_admin.py   # Gap analysis fixes
│   └── test_schema_and_mock_sync.py        # Boundary condition drift tests
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
| Full conversation flow | `integration/test_classification_and_routing.py` or `integration/test_narrative_and_eval_scenarios.py` |
| A bug fix | `integration/test_targeted_bug_regressions.py` (add a section for the bug number) |
| Gender/LGBTQ filtering | `unit/test_gender_extraction.py` |

## Source Module → Test File Mapping

| Source module | Primary test file(s) |
|---|---|
| `slot_extractor.py` | `unit/test_slot_extractor.py`, `unit/test_gender_extraction.py`, `unit/test_location_boundaries.py` |
| `classifier.py` | `unit/test_contraction_normalization.py`, `unit/test_phrase_audit.py` |
| `responses.py` | `integration/test_service_data_llm_firewall.py` (prompt isolation) |
| `confirmation.py` | `unit/test_confirmation_flow.py` |
| `phrase_lists.py` | `unit/test_phrase_audit.py` |
| `chatbot.py` | `integration/test_classification_and_routing.py`, `integration/test_multi_turn_and_context.py` |
| `pii_redactor.py` | `unit/test_pii_redactor.py` |
| `query_templates.py` | `unit/test_query_templates.py` |
| `crisis_detector.py` | `unit/test_crisis_detector.py` |
| `llm_slot_extractor.py` | `unit/test_llm_slot_extractor.py`, `unit/test_narrative_extraction.py` |
| `llm_classifier.py` | `unit/test_llm_classifier.py` |
| `post_results.py` | `unit/test_post_results.py`, `unit/test_post_results_boundary.py` |
| `routes/chat.py` | `integration/test_http_routes_and_models.py` |
| `routes/admin.py` | `integration/test_admin_api_routes.py` |

## LLM Isolation in Tests

Tests that call `_classify_tone()` or `detect_crisis()` must isolate from the Anthropic API to prevent rate-limit cascading failures. Use these patterns:

```python
# Tone classification — skip the Sonnet crisis detection call
tone = _classify_tone("I'm embarrassed to ask", crisis_result=None)

# Crisis detection — regex only, no LLM fallback
result = detect_crisis("I ran away from home", skip_llm=True)

# Full chatbot flow — prevent LLM classifier from intercepting
@patch("app.services.chatbot._USE_LLM", False)
def test_something(fresh_session):
    result = generate_reply("tell me more", session_id=fresh_session)
```

Three tests intentionally verify LLM integration and are left unprotected:
- `test_llm_called_when_regex_misses` — verifies Sonnet fires for ambiguous messages
- `test_skip_llm_false_default` — verifies `skip_llm` defaults to False
- `test_classify_tone_calls_detect_when_not_provided` — verifies `_classify_tone` calls `detect_crisis` when no pre-computed result

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
