# Test Suite Organization

## Structure

```
tests/
├── conftest.py              # Shared fixtures + helpers (send, send_multi,
│                            # assert_classified, make_ctx for MessageContext)
│
├── unit/                    # Fast, isolated tests — no DB, no LLM, no network (66 files)
│   │
│   │  # Slot extraction, classification, and routing
│   ├── test_slot_extraction.py            # Unified slot extractor (Phase 4 consolidated)
│   ├── test_slot_extraction_regex.py      # Regex layer — service, location, age, family
│   ├── test_slot_extraction_keywords.py   # Keyword-specific slot extraction
│   ├── test_service_keywords.py           # Service keyword coverage and alignment
│   ├── test_semantic_router.py            # Semantic routing — classification, thresholds, populations
│   ├── test_gender_extraction.py          # Gender / LGBTQ identity extraction
│   ├── test_populations.py                # Population context extraction and query boosts
│   ├── test_location_boundaries.py        # NYC location parsing edge cases
│   ├── test_geographic_borough_validator.py  # query_executor borough validator
│   ├── test_boundaries.py                 # app.rag.boundaries — coord-to-borough lookups
│   ├── test_org_name_search.py            # Organization name search
│   ├── test_phrase_audit.py               # Keyword coverage audits
│   ├── test_parser_collision_guards.py    # Parser collision guards (substring false-positive prevention)
│   ├── test_medical_urgency.py            # Medical urgency detection
│   ├── test_hybrid_multi_intent.py        # classify_all_services, regex+semantic merge
│   ├── test_multi_intent_queue.py         # Multi-service queue handling
│   ├── test_service_change_merge.py       # merge_slots clears queue state on service change (A.4)
│   ├── test_narrowing.py                  # Query narrowing — taxonomies, descriptions, exclusion
│   ├── test_contraction_normalization.py  # Contraction expansion, intensifier stripping
│   ├── test_text_normalize.py             # app.utils.text_normalize helpers
│   │
│   │  # Crisis, tone, and emotional handling
│   ├── test_crisis_detector.py            # Crisis phrase detection (regex + LLM)
│   ├── test_tone_and_empathy.py           # Tone detection, emotional responses (AVR)
│   ├── test_safety_identity_and_tone.py   # PII warnings, identity, crisis categories
│   ├── test_response_escalation.py        # Frustration escalation (3-tier counter)
│   ├── test_frustration_and_crisis.py     # Frustration restate, crisis step-down geolocation
│   │
│   │  # Orchestrator / dispatch / session helpers
│   ├── test_chatbot_extracted_helpers.py  # Helpers extracted from generate_reply (D-1, accessibility handlers)
│   ├── test_session_helpers.py            # chatbot.session_helpers (snapshot consume, transcript append)
│   ├── test_session_store.py              # In-memory session state
│   ├── test_session_token.py              # Session token signing
│   ├── test_helpers.py                    # Shared test helpers (_fresh, _send, _build_shelter_results)
│   ├── test_contextual_acknowledgments.py # chatbot.contextual_acknowledgments
│   ├── test_routing_category_order.py     # Routing-category cascade regression guards
│   ├── test_result_builder.py             # chatbot.result_builder._build_follow_up_response
│   │
│   │  # Conversation flows / confirmation / post-results
│   ├── test_core_conversation_flows.py    # Core conversation scenarios
│   ├── test_change_mind_and_decline.py    # Mid-conversation service changes, decline/contradiction
│   ├── test_confirmation_flow.py          # Confirmation/denial flow
│   ├── test_post_results.py               # Post-results question classification
│   ├── test_post_results_boundary.py      # Post-results → new-request transitions
│   ├── test_post_results_state.py         # Post-results state management
│   ├── test_results_enhancements.py       # Sort, auto-execute, day detection
│   ├── test_filter_classification.py      # Post-results filter regex + compound detection
│   ├── test_filter_pipeline.py            # Filter pipeline, helpers, dispatch, monitoring
│   ├── test_display_and_pagination.py     # loc_label, pagination, taxonomies, open-right-now regex
│   ├── test_service_card_display.py       # Service card rendering, pagination
│   ├── test_walk_in_and_card_extras.py    # Walk-in filter, card extras
│   ├── test_population_fallback.py        # Population-critical citywide fallback
│   ├── test_browser_geolocation.py        # Geolocation coord handling, failure fallback to borough
│   │
│   │  # Privacy / data / queries / knowledge base
│   ├── test_pii_redactor.py               # PII detection and redaction
│   ├── test_query_templates.py            # SQL template building, formatting, dynamic ORDER BY
│   ├── test_bot_knowledge.py              # Bot knowledge base answers
│   ├── test_bot_knowledge_freshness.py    # TestBotKnowledgeFreshness — prose-vs-collection drift guard
│   │
│   │  # Edge cases and regression shields
│   ├── test_edge_cases.py                 # Slot extractor edge cases
│   ├── test_diabetic_insulin.py           # Sprint 1 fixes for peer_diabetic_insulin (R37)
│   ├── test_audit_regression.py           # Regression shield for April 2026 parity-audit decisions
│   │
│   │  # API / infrastructure / persistence
│   ├── test_main.py                       # FastAPI app initialization, health check, CORS
│   ├── test_claude_client.py              # Claude API client mocking
│   ├── test_rate_limiter.py               # Rate limiting logic
│   ├── test_audit_log.py                  # Audit log writing, stats, eval results
│   ├── test_persistence.py                # SQLite pilot persistence (write-through, hydration)
│   ├── test_location_feedback.py          # Location-specific feedback endpoint
│   ├── test_health_and_upload.py          # ping_llm, get_status, eval upload, userFacingError
│   ├── test_idempotency.py                # Idempotency cache (PWA design §3.10–3.11)
│   ├── test_time_format.py                # app.utils.time_format helpers
│   │
│   │  # Tooling and meta
│   ├── test_eval_subset_filter.py         # eval_llm_judge --subset filter
│   ├── test_mutation_workflow_pairings.py # Mutation-workflow pairing guard
│   └── test_check_docs.py                 # Drift checker (scripts/check_docs.py) regression tests
│
├── integration/             # Multi-component tests — use send(), mock DB/LLM (16 files)
│   │
│   │  # Core integration (orchestrator + handlers + DB mocks)
│   ├── test_classification_and_routing.py # Core generate_reply routing
│   ├── test_multi_turn_and_context.py     # Context-aware yes/no after emotions
│   ├── test_browser_geolocation.py        # End-to-end geolocation flow + cross-cutting paths
│   ├── test_http_routes_and_models.py     # HTTP /chat/ endpoint, Pydantic models
│   ├── test_admin_api_routes.py           # Admin API routes
│   ├── test_narrative_and_eval_scenarios.py  # End-to-end conversation flows
│   ├── test_service_data_llm_firewall.py  # LLM isolation — prompts never see DB data
│   ├── test_orchestrator_guards.py        # Orchestrator mutation-score gap closures
│   ├── test_apostrophe_fuzz.py            # Apostrophe fuzz harness (ENG-1)
│   ├── test_llm_call_redundancy.py        # Cost regression for LLM-1, LLM-2, LLM-3 dedupes
│   ├── test_slot_extraction_live.py       # Live API tests (gated; skip without real key)
│   │
│   │  # Regression tests (from bug fixes and coverage audits)
│   ├── test_targeted_bug_regressions.py   # Bugs 8-14 from PR 19
│   ├── test_crisis_and_flow_regressions.py  # Run 16 failing-scenario fixes
│   ├── test_utility_and_session_edges.py  # Coverage audit fixes
│   ├── test_format_pipeline_and_admin.py  # Gap analysis fixes
│   └── test_schema_and_mock_sync.py       # Boundary condition / mock-Pydantic-SQL drift
│
└── eval/                    # LLM evaluation (run separately, not under pytest)
    └── eval_llm_judge.py                  # Scenario-based LLM-as-judge evaluator
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
pytest tests/unit/test_slot_extraction_regex.py

# With coverage
pytest tests/unit/ --cov=app.services.slot_extraction_regex
```

## Where to Add New Tests

| You're testing... | Add to... |
|---|---|
| A new slot extractor function | `unit/test_slot_extraction_regex.py` (or `unit/test_slot_extraction.py` for the unified extractor) |
| A new phrase list or keyword | `unit/test_phrase_audit.py` |
| Action / tone classification | `unit/test_contraction_normalization.py` (input normalization) or `unit/test_safety_identity_and_tone.py` (tone/identity) |
| Confirmation message formatting | `unit/test_confirmation_flow.py` |
| A new response string | `unit/test_safety_identity_and_tone.py` (PII / crisis category responses) or `unit/test_tone_and_empathy.py` (emotional / AVR responses) |
| PII redaction patterns | `unit/test_pii_redactor.py` |
| Crisis detection phrases | `unit/test_crisis_detector.py` |
| Filter classification or pipeline | `unit/test_filter_classification.py` or `unit/test_filter_pipeline.py` |
| Post-results question handling | `unit/test_post_results.py`, `unit/test_post_results_boundary.py`, or `unit/test_post_results_state.py` |
| Display/pagination behavior | `unit/test_display_and_pagination.py` |
| MessageContext-using helpers | `unit/test_chatbot_extracted_helpers.py` (use the `make_ctx` fixture from `conftest.py`) |
| Full conversation flow | `integration/test_classification_and_routing.py` or `integration/test_narrative_and_eval_scenarios.py` |
| A bug fix | `integration/test_targeted_bug_regressions.py` (add a section for the bug number) |
| A regression after an audit decision | `unit/test_audit_regression.py` (parity-audit decisions) or `integration/test_crisis_and_flow_regressions.py` |
| Gender / LGBTQ filtering | `unit/test_gender_extraction.py` |
| Drift-checker behavior | `unit/test_check_docs.py` |

## Source Module → Test File Mapping

| Source module | Primary test file(s) |
|---|---|
| `slot_extraction_regex.py` | `unit/test_slot_extraction_regex.py`, `unit/test_gender_extraction.py`, `unit/test_location_boundaries.py` |
| `slot_extraction/` (package, unified extractor) | `unit/test_slot_extraction.py`, `integration/test_slot_extraction_live.py` |
| `classifier.py` | `unit/test_contraction_normalization.py`, `unit/test_phrase_audit.py` |
| `responses.py` | `unit/test_safety_identity_and_tone.py`, `unit/test_tone_and_empathy.py`, `integration/test_service_data_llm_firewall.py` (prompt isolation) |
| `confirmation.py` (top-level message builder) | `unit/test_confirmation_flow.py` |
| `chatbot/handlers/confirmation.py` (orchestrator dispatcher) | `unit/test_confirmation_flow.py`, `integration/test_classification_and_routing.py` |
| `chatbot/orchestrator.py` (`generate_reply`) | `integration/test_classification_and_routing.py`, `integration/test_orchestrator_guards.py` |
| `chatbot/context.py` (MessageContext) | `unit/test_chatbot_extracted_helpers.py` (use `make_ctx` fixture) |
| `chatbot/session_helpers.py` | `unit/test_session_helpers.py` |
| `chatbot/result_builder.py` | `unit/test_result_builder.py` |
| `chatbot/contextual_acknowledgments.py` | `unit/test_contextual_acknowledgments.py` |
| `phrase_lists.py` | `unit/test_phrase_audit.py` |
| `pii_redactor.py` | `unit/test_pii_redactor.py` |
| `query_templates.py` | `unit/test_query_templates.py` |
| `query_executor.py` | `unit/test_geographic_borough_validator.py`, `unit/test_narrowing.py` |
| `crisis_detector.py` | `unit/test_crisis_detector.py` |
| `semantic_router.py` | `unit/test_semantic_router.py`, `unit/test_hybrid_multi_intent.py` |
| `bot_knowledge.py` | `unit/test_bot_knowledge.py`, `unit/test_bot_knowledge_freshness.py` |
| `app/utils/text_normalize.py` | `unit/test_text_normalize.py` |
| `app/utils/time_format.py` | `unit/test_time_format.py` |
| `app/rag/boundaries.py` | `unit/test_boundaries.py` |
| `services/post_results.py` (post-results question handler) | `unit/test_post_results.py`, `unit/test_post_results_boundary.py`, `unit/test_post_results_state.py` |
| `routes/chat.py` | `integration/test_http_routes_and_models.py` |
| `routes/admin.py` | `integration/test_admin_api_routes.py` |
| `scripts/check_docs.py` | `unit/test_check_docs.py` |

## LLM Isolation in Tests

Tests that call `_classify_tone()` or `detect_crisis()` must isolate from the Anthropic API to prevent rate-limit cascading failures. Use these patterns:

```python
# Tone classification — skip the Sonnet crisis detection call
tone = _classify_tone("I'm embarrassed to ask", crisis_result=None)

# Crisis detection — regex only, no LLM fallback
result = detect_crisis("I ran away from home", skip_llm=True)

# Full chatbot flow — prevent LLM classifier from intercepting.
# Patch on the orchestrator module (where the name is used at call
# time), NOT on the chatbot package — see "Patch where imported, not
# where defined" below.
@patch("app.services.chatbot.orchestrator._USE_LLM", False)
def test_something(fresh_session):
    result = generate_reply("tell me more", session_id=fresh_session)
```

Three tests intentionally verify LLM integration and are left unprotected:
- `test_llm_called_when_regex_misses` — verifies Sonnet fires for ambiguous messages
- `test_skip_llm_false_default` — verifies `skip_llm` defaults to False
- `test_classify_tone_calls_detect_when_not_provided` — verifies `_classify_tone` calls `detect_crisis` when no pre-computed result

### Patch where imported, not where defined

A test-quality audit in April 2026 found 187 patches across 23 files that were silently no-ops because they patched the wrong target. The pattern looked correct but the patches never took effect — tests "passed" because the real (unpatched) functions returned benign defaults.

The rule: **patch the name in the module that *uses* it, not the module that *defines* it.** When `module_a.py` does `from module_b import foo`, patching `module_b.foo` does not affect `module_a.foo` — Python pre-binds names at import time. You must patch `module_a.foo`.

The three function names whose package-level patch is wrong — and the bind sites to patch instead:

| ❌ Wrong (silent no-op)              | ✅ Right                                                                 |
|--------------------------------------|--------------------------------------------------------------------------|
| `app.services.chatbot.claude_reply`  | `app.services.chatbot.handlers.meta.claude_reply`                        |
| `app.services.chatbot.detect_crisis` | `app.services.chatbot.orchestrator.detect_crisis` **AND** `app.services.classifier.detect_crisis` (two separate bind sites — see below) |
| `app.services.chatbot._USE_LLM`      | `app.services.chatbot.orchestrator._USE_LLM`                             |

**`detect_crisis` has two bind sites.** Both `orchestrator.py` and `classifier.py` do `from app.services.crisis_detector import detect_crisis` at module load, creating two independent local bindings. The orchestrator calls it in the dispatch flow; the classifier calls it inside `_classify_tone`. If you patch only one binding, the other path runs the real function — and if `ANTHROPIC_API_KEY` is set but invalid, the real function's LLM fallback fires, 401s, fail-opens to a crisis result, and hijacks classification. This is the specific reason `send()`, `send_multi()`, and `assert_classified()` patch both.

The `send()`, `send_multi()`, and `assert_classified()` helpers in `conftest.py` patch all of these correctly — prefer them over hand-rolled `@patch` decorators when possible.

### Determinism across environments

The suite is designed to produce identical results whether `ANTHROPIC_API_KEY` is unset, set to a placeholder (e.g., `sk-ant-test-xxx`), or set to a real working key. This property is enforced by:

1. **`conftest.py` patches both bind sites of `detect_crisis`** (orchestrator + classifier) so a nonworking key can't cause fail-open-to-crisis from the LLM fallback.
2. **Live LLM tests are gated** behind `@_skip_no_api_key`, which uses `_api_key_looks_real()` to detect placeholder keys and skip cleanly rather than 401-and-report-fail.
3. **The `assert_classified()` helper** wraps `_classify_message` in a local `patch("app.services.classifier.detect_crisis", return_value=None)` for the same reason.

If a test fails only when an env var is set (or only when it isn't), that's a D5 finding — the audit tool will catch it and `check_audit_baseline.py` will fail CI.

### Test-quality tooling

Three tools live in `tests/_tools/`:

- **`audit_tests.py`** — static scanner for known anti-patterns (dead mocks, assertionless tests, env-leaky tests, unauthenticated admin calls). Run with `make audit` or `python3 tests/_tools/audit_tests.py`. Current baseline: **29 findings total** (D3=19 advisory, D5=8 mostly deliberate env tests, D8=1, D9=1; D2/D6/D7 all at 0 and gated).
- **`check_audit_baseline.py`** — CI gate that compares current findings against `tests/_tools/audit_baseline.txt`. The build fails if any category's count rises above the baseline.
- **`fix_patch_targets.py`** — codemod that rewrites the dead patch targets above to their live equivalents. Already applied (130 rewrites across 21 files; 0 D7 findings remain). Safe to re-run with `--apply` at any time — it's idempotent and exits cleanly on a clean tree.

See `TEST_INFRASTRUCTURE.md` at the repo root for the full operator's guide, including mutation testing on safety-critical modules.

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
