# Testing Guide

## Overview

<!-- drift:ignore: "collected" count means post-parametrize pytest collection (~4,405). The drift checker uses an AST-walked def count (~3,229) which is structurally lower; the gap is measurement convention, not stale data. Re-verify with `pytest tests/ --collect-only -q | tail -1` if updating. -->
The test suite covers ~4,405 collected tests across 82 test files (896 raw `def test_*` functions that expand via parametrization), plus an LLM-as-judge evaluation framework with 171 scenarios — see [`EVALUATION_TESTING.md`](EVALUATION_TESTING.md) for the eval operator's manual. Tests are organized into `tests/unit/` (66 files — no DB or LLM needed) and `tests/integration/` (16 files — use mocked DB/LLM via `send()`/`send_multi()` helpers), with a separate `tests/eval/` directory for the LLM judge. Tests validate every backend module: slot extraction (regex, semantic embedding, and LLM-based), gender/LGBTQ identity extraction, population context extraction (veteran, disabled, reentry, foster_youth, dv_survivor, pregnant, senior — with false-positive guards, multi-population support, query boost verification, DV crisis injection), PII redaction (including gender identity terms) and PII safety warnings, conversational routing, crisis detection (8 categories: suicide_self_harm, medical_emergency, domestic_violence, youth_runaway, assault_victim, safety_concern, trafficking, violence), crisis step-down (including DV population injection and slot preservation for youth_runaway/assault_victim), emotional handling (AVR pattern with 9 emotion-specific static responses), frustration routing (3-tier counter-based escalation), negative preference handling, conversational awareness guard, privacy routing exception, phrase list audit coverage (C-SSRS, Joiner IPT, DV control, shame/stigma, grief, NYC service terms), contraction normalization, intensifier stripping, post-normalization emotional phrase variants, location boundary enforcement, query template correctness (including dynamic ORDER BY generation with population boosts), confirmation flow (including population-aware prefixes, warm reframe, baseline warmth), quick replies, audit logging, admin API routes, chat HTTP endpoint, Pydantic model validation, Claude client initialization, API configuration, session management, geolocation, rate limiting, request correlation IDs, privacy question handling, family composition, multi-service extraction, split classifier (action + tone), shelter taxonomy enrichment, word-boundary keyword collision prevention, nearby borough suggestions, bug fix regressions (7 targeted fixes with 30 tests), post-results question handling, crisis safety edge cases (research-sourced C-SSRS, HITS/SAFE, Polaris, SAMHSA), co-located multi-service queries, gap coverage (freshness, admin stats shape, skip_llm pipeline, prompt builders), quick reply button audit, SQLite pilot persistence (write-through, hydration, disabled mode), database schema/query integration, bot self-knowledge (live capability sourcing, topic matching), boundary drift detection (mock/Pydantic/SQL/format sync), context-aware routing (state transitions, frustration counting, implicit service changes), integration scenarios (narrative flows, cross-feature interactions, eval approximations), narrative extraction (urgency-aware slot extraction for long messages), ambiguity handling (confidence scoring, disambiguation prompts, correction recovery, "Not what I meant" button), post-results boundary routing (new-request escape hatch, location-based result clearing, name-match fallthrough), semantic routing (route definitions, initialization, service classification, false positive rejection, population detection, threshold behavior, integration fallthrough, graceful degradation, observability, route alignment), filter-persistence lifecycle (`_filtered_results`/`_last_results` preservation through emotional transitions, "show all" escape, new-search reset), and the April 15 `housing_assistance` retirement regression guards (`test_audit_regression.py::TestHousingAssistanceRemoval`). Unit tests run without external services (database and Claude API are mocked). DB integration tests require DATABASE_URL and are automatically skipped without it.

### April 2026 — Multi-intent & cultural-responsiveness additions

A seven-fix pass in April 2026 landed multi-intent/frustration improvements with the following test additions:

- **A.4** (queue-clearing on service change): new file `tests/unit/test_service_change_merge.py` with 9 tests organized into 3 classes (clears-queue-state, doesn't-clear-unrelated, contradiction-promotion) + an integration regression guard (`test_service_change_does_not_leak_old_service_into_response`).
- **B.1** (negative-preference phrase expansion for `edge_frustration`): new `TestExpandedNegativePreferencePhrases` class in `tests/unit/test_frustration_and_crisis.py` (21 tests) + integration guard `test_edge_frustration_scenario_does_not_repeat_response`.
- **B.2** (compound-intent override — rejection + new service): 4 integration tests under a `B.2` section header in `tests/integration/test_classification_and_routing.py` — INT-3 regression replay, frustration-tone assertion, menu-preservation guard, location-persistence guard.
- **A.1.b** (immigration acknowledgment prefix): 21 unit tests organized into 3 classes in `tests/unit/test_chatbot_extracted_helpers.py` (`TestDetectImmigrationContext`, `TestImmigrationContextDetail`, `TestImmigrationAcknowledgment`) + 4 integration tests in `tests/integration/test_narrative_and_eval_scenarios.py`. Includes a dual-key contract lock (`test_asylum_in_queued_services_key`) ensuring the helper reads both `additional_services` and `_queued_services` so a future refactor that drops either key fails the suite.
- **C.1** (bot_identity / bot_question phrase expansion — PR 7, follow-on to PR 6): 4 classifier tests + 1 integration regression guard in `tests/integration/test_classification_and_routing.py`. The regression guard (`test_c1_regression_guard_whats_your_name_during_pending_confirmation`) replays the user-reported surreal-non-sequitur bug: pending confirmation set → identity question → expect bot_identity response, NOT a silent re-nudge.
- **C.2** (topic-shift disambiguation in pending-confirmation Path 3 — PR 7): new `TestLooksLikeTopicShiftQuestion` class in `tests/unit/test_frustration_and_crisis.py` (9 unit tests for the heuristic) + 2 integration tests in `tests/integration/test_classification_and_routing.py` — one asserting novel off-topic questions produce disambiguation, one confirming short confirmation-shaped messages still re-nudge (sanity check that the heuristic is conservative).

Each production change (A.4, B.1, B.2, A.1.b, C.1, C.2) has a proven regression guard: the fix was temporarily stripped and the integration test was confirmed to fail with the expected evidence before being restored. The C.2 strip evidence is particularly informative — stripping the disambiguation branch produces literally the surreal non-sequitur the original bug report described: `"Just to make sure — I'll look for food in Brooklyn — sound good? Tap 'Yes, search' to go, or you can change the details."`

## Running Tests

From the repo root with the virtual environment activated:

```
source backend/venv/bin/activate
```

**Run all tests with pytest (recommended):**

```
pip install pytest httpx
pytest tests/ -q --ignore=tests/integration/test_admin_api_routes.py --ignore=tests/ --tb=short
```

**Run unit tests only (fast, no external deps):**

```
pytest tests/unit/ -v
```

**Run integration tests only:**

```
pytest tests/integration/ -v
```

**Run a single test file:**

```
pytest tests/unit/test_slot_extraction_regex.py -v
```

**Run a single test:**

```
pytest tests/integration/test_classification_and_routing.py::test_confirm_deny_breaks_loop -v
```

**Run LLM-as-judge evaluation (requires API key):**

```
ANTHROPIC_API_KEY=sk-ant-... python tests/eval/eval_llm_judge.py
```

See [`EVALUATION_TESTING.md`](EVALUATION_TESTING.md) for full CLI flags (including `--subset failing` for fast inner-loop runs), workflow patterns, and the scoring rubric.

**Run LLM integration tests (requires API key):**

```
ANTHROPIC_API_KEY=sk-ant-... pytest tests/integration/test_slot_extraction_live.py -v
```

Without `ANTHROPIC_API_KEY`, the 5 live LLM tests are automatically skipped.

## Test Quality Infrastructure

The test suite is gated by three CI workflows that enforce quality beyond simple pass/fail. See `TEST_INFRASTRUCTURE.md` at the repo root for the full operator's guide.

### Coverage gate

`.github/workflows/test-quality.yml` runs the full suite with line and branch coverage on every PR. The build fails if line coverage drops below 85% (currently 88%).

```
make coverage          # line coverage with terminal report
make coverage-branch   # + branch coverage, HTML report at htmlcov/
```

### Static audit gate

`tests/_tools/audit_tests.py` scans every test file for known anti-patterns. Categories include dead mocks (D7), assertionless tests (D2), unauthenticated admin calls (D6), env-leaky tests (D5), and `time.sleep()` in CI (D9). The full category list is in `TEST_INFRASTRUCTURE.md`.

```
make audit                                          # full report
python3 tests/_tools/audit_tests.py --summary       # counts only
python3 tests/_tools/audit_tests.py --category D2   # one category
```

The CI gate (`tests/_tools/check_audit_baseline.py`) compares the current findings against `tests/_tools/audit_baseline.txt`. The build fails if any category's count rises above the baseline. To deliberately accept new findings, regenerate the baseline:

```
make audit-baseline   # writes tests/_tools/audit_baseline.txt
```

**Current baseline:** 29 findings total — D3=19 (advisory, not gated), D5=8 (deliberate env-handling tests), D8=1 and D9=1 (both legitimate and accepted). D1, D2, D4, D6, and D7 are all at 0 and gated — any new occurrence fails CI. Down from 203 pre-cleanup.

This audit was prompted by an April 2026 cleanup that found 187 patches across 23 test files were silent no-ops — they patched `app.services.chatbot.X` at the package level when the runtime binding lives on a submodule. See "Patch where imported, not where defined" in `tests/README.md`.

### Mutation testing on safety-critical modules

Coverage measures execution; mutation testing measures whether tests would catch a regression. Five safety-critical modules are mutation-tested:

| Module | Why | Threshold |
|---|---|---|
| `backend/app/services/crisis_detector.py` | Safety-critical | 50% (raw); has untestable LLM-API path |
| `backend/app/services/classifier.py` | Routes everything; misroute is silent | 70% |
| `backend/app/privacy/pii_redactor.py` | Privacy-critical | 85% |
| `backend/app/services/chatbot/orchestrator.py` | Main dispatch | 70% |
| `backend/app/services/session_token.py` | Security-adjacent | 85% |

Two workflows use cosmic-ray for mutation testing (mutmut v3 fights the `backend/` layout + conftest's `sys.path` manipulation; cosmic-ray patches in-place which works without modification):

- `.github/workflows/mutation-testing.yml` — runs weekly Sunday 03:00 UTC across all 5 modules in a parallel matrix. Files a tracking issue with label `test-quality` if any module drops below threshold.
- `.github/workflows/mutation-testing-pr.yml` — runs per-PR but only when the PR touches a critical module. Mutates only the changed file (the Google model from Petrović & Ivanković, TSE 2021). Skipped entirely on PRs that don't touch any critical module.

Locally:

```
make mutation-module MODULE=backend/app/services/classifier.py   # one module
make mutation-report                                              # summarize
```

A single module typically takes 15-60 minutes. Cosmic-ray stores state in `cr-<module>.sqlite`; interrupted runs resume from where they left off.

### Production fix surfaced by the audit

The April 2026 audit also revealed that `ping_llm()` in `backend/app/llm/claude_client.py` had its real API call commented out and was returning a fabricated `status="up"`. The `/api/health` endpoint reported the LLM as healthy whether or not the API key was valid. Fix: uncommented the real call. Now properly tested by `test_health_and_upload.py::TestPingLlm`.

### Drift guards — hand-maintained mappings vs live code

A recurring bug class surfaced four times in a single week in April 2026: a hand-maintained mapping (a count in prose, an enumeration in a topic answer, a workflow-YAML pair of module-to-test-files) drifted out of sync with the live code it was describing. Each time, no automated check existed to catch the drift, and the staleness persisted for weeks or months until someone noticed.

Cases fixed so far:

| Case | What was stale | Guard that now prevents recurrence |
|---|---|---|
| `bot_knowledge.py` topic answers | "8 PII types" vs 9 live; "English only" vs partial Spanish shipped; 10 source refs to a file that no longer exists | pytest (`tests/unit/test_bot_knowledge.py::TestBotKnowledgeFreshness`) |
| `CHATBOT_BEHAVIOR.md` crisis section | "six crisis categories" vs 8 live; acute/step-down lists using stale category names | `scripts/check_docs.py::check_crisis_category_name_refs` | <!-- drift:ignore: documenting the historical stale claim as a worked example; the real CHATBOT_BEHAVIOR.md was fixed in the same PR -->
| `FEATURES.md` count claims | "18 negative-preference phrases" vs 35 live; "Six crisis categories" vs 8 | `scripts/check_docs.py::check_category_counts` (widened `word_numbers` vocabulary) + `check_service_category_enumeration` | <!-- drift:ignore: documenting the historical stale claim as a worked example; the real FEATURES.md was fixed in the same PR -->
| Mutation workflow pairings | test files paired with modules they don't exercise (produces 0% mutation scores); unpaired files that do exercise the module (depresses scores below what existing tests could achieve) | pytest (`tests/unit/test_mutation_workflow_pairings.py`) |

Same shape every time: a mapping between names/counts/references in artifact A and ground truth in artifact B, with no automated check enforcing consistency. Same fix pattern every time: **live-source the ground truth, parse the hand-maintained claim, fail loudly with a targeted message when they disagree.**

#### Which guard to reach for when adding a new mapping

| Signal | Guard location | Example |
|---|---|---|
| Prose in one Python module describes live data in the same module | pytest test in `tests/unit/test_<module>.py` | `TestBotKnowledgeFreshness` |
| Prose in a markdown doc describes live code elsewhere | check function in `scripts/check_docs.py` | `check_service_category_enumeration` |
| Numbers-as-words in docs about code-owned counts | extend the relevant `CATEGORY_COUNTS` entry's `word_numbers` dict in `scripts/check_docs.py` | widened "Seven/Eight/Nine" → full 1-15 |
| Prose enumerates code-owned list members by name | new check function in `scripts/check_docs.py` following the enumeration-guard pattern | `check_crisis_category_name_refs` |
| Prose makes structured claims (tier N contains X) | new check function following the tier-structure pattern | `check_service_need_priority_tiers` |
| Workflow YAML pairs named entities with files/modules | pytest test that parses the YAML + validates the pairing | `test_mutation_workflow_pairings.py` |
| A specific past regression you want to lock down | single-purpose `test_no_<X>_in_module`-style assertion | `test_no_english_only_claim_in_module` |

Every guard's failure message should name three things: **what** is wrong, **where** to find it (file + line), and **how** to fix it (with both "update the prose to match code" and "update the code if the prose is the intended new state" stated as options). The message IS the contract documentation — a future engineer who breaks the mapping should get enough information from the failure to fix it without reading the guard source.

#### Adding a new guard — checklist

1. Identify the hand-maintained claim and its ground-truth source.
2. Live-source the ground truth (AST parsing for Python collections, `yaml.safe_load` for workflows, regex for specific patterns).
3. Parse the claim (regex on prose, grep on imports, etc.).
4. Write the assertion with a failure message that names the offending text, the live value, and the remediation path.
5. **Strip-discipline proof**: revert the fix that motivated the guard, confirm the guard fires with the targeted message, restore. This is the evidence the guard will catch the class of bug it was built for.
6. Add the guard to the appropriate registry (`CHECKS` list in `check_docs.py`, new test class in a pytest file, etc.).

The split between linter checks and pytest guards is intentional: **`check_docs.py` handles cross-file drift** (test counts in TESTING.md vs actual test functions, YAML workflows vs code), **pytest guards handle module-internal prose-vs-live-code drift** (answer strings vs their underlying data, friendly-name maps vs live enum keys). If a drift case could reasonably go in either, prefer pytest — it runs faster and fails with richer error context.

## Test Coverage Map

All backend modules and all public functions are covered. Tests are in `tests/unit/` (no external deps) and `tests/integration/` (mocked DB/LLM):

| Module | Test file(s) | Tests | Status |
|---|---|---|---|
| `chatbot/` package (orchestrator + handlers) | `integration/test_classification_and_routing.py`, `integration/test_targeted_bug_regressions.py`, `integration/test_multi_turn_and_context.py`, `integration/test_narrative_and_eval_scenarios.py`, `unit/test_service_card_display.py`, `unit/test_results_enhancements.py`, `unit/test_audit_regression.py` (housing_assistance retirement guards), `unit/test_frustration_and_crisis.py` (filter-aware cleanup in `handlers/emotional.py`), `unit/test_post_results_extras.py` (filter persistence in `handlers/post_results.py`) | 310+ | Full — tests exercise the whole package via `send()`/`send_multi()` and don't distinguish handler boundaries. Individual handlers (`handlers/emotional.py`, `handlers/confirmation.py`, `handlers/post_results.py`, etc.) inherit their coverage from the integration flow tests |
| `classifier.py` | `unit/test_contraction_normalization.py`, `unit/test_phrase_audit.py`, `integration/test_classification_and_routing.py` | 60+ | Full |
| `phrase_lists.py` | `unit/test_phrase_audit.py` | 41 | Full |
| `responses.py` | `integration/test_classification_and_routing.py`, `integration/test_format_pipeline_and_admin.py` | (inline) | Full |
| `confirmation.py` | `unit/test_edge_cases.py`, `unit/test_gender_extraction.py`, `integration/test_classification_and_routing.py` | (inline) | Full |
| `slot_extraction_regex.py` | `unit/test_slot_extraction_regex.py`, `unit/test_gender_extraction.py`, `unit/test_edge_cases.py`, `unit/test_location_boundaries.py`, `unit/test_populations.py`, `unit/test_org_name_search.py`, `unit/test_walk_in_and_card_extras.py` | 330+ | Full |
| `rag/__init__.py` | `unit/test_query_templates.py`, `unit/test_populations.py`, `unit/test_org_name_search.py`, `unit/test_walk_in_and_card_extras.py`, `integration/test_browser_geolocation.py` | 125+ | Full |
| `query_templates.py` | `unit/test_query_templates.py`, `unit/test_location_boundaries.py`, `unit/test_service_card_display.py`, `unit/test_walk_in_and_card_extras.py` | 120+ | Full |
| `query_executor.py` | `unit/test_location_boundaries.py`, `unit/test_edge_cases.py` | 65 | Full |
| `audit_log.py` | `unit/test_audit_log.py`, `unit/test_location_feedback.py`, `integration/test_targeted_bug_regressions.py`, `integration/test_admin_api_routes.py`, `unit/test_audit_regression.py` | 77+ | Full |
| `crisis_detector.py` | `unit/test_crisis_detector.py`, `integration/test_targeted_bug_regressions.py`, `unit/test_phrase_audit.py` | 60+ | Full |
| `slot_extraction/` (package) | `unit/test_slot_extraction.py`, `integration/test_slot_extraction_live.py`, `unit/test_semantic_router.py` | 215+ | Full |
| `semantic_router.py` | `unit/test_semantic_router.py` | 54 | Full |
| `semantic_routes.py` | `unit/test_semantic_router.py` | 54 | Full |
| `bot_knowledge.py` | `unit/test_bot_knowledge.py` | 44 | Full |
| `post_results.py` | `unit/test_post_results.py`, `unit/test_post_results_boundary.py`, `unit/test_results_enhancements.py` | 125 | Full |
| `pii_redactor.py` | `unit/test_pii_redactor.py`, `unit/test_gender_extraction.py`, `unit/test_edge_cases.py` | 38+ | Full |
| `session_store.py` | `unit/test_session_store.py`, `integration/test_classification_and_routing.py`, `integration/test_http_routes_and_models.py` | 7+ | Full |
| `session_token.py` | `unit/test_session_token.py`, `integration/test_http_routes_and_models.py` | 17 | Full |
| `rate_limiter.py` | `unit/test_rate_limiter.py` | 24 | Full |
| `chat_models.py` | `integration/test_http_routes_and_models.py`, `integration/test_schema_and_mock_sync.py` | 27+ | Full |
| `admin.py` (routes) | `integration/test_admin_api_routes.py` | 28 | Full |
| `chat.py` (route) | `integration/test_http_routes_and_models.py` | 48 | Full |
| `claude_client.py` | `unit/test_claude_client.py` | 19 | Full |
| `main.py` | `unit/test_main.py` | 14 | Full |
| LLM isolation (cross-cutting) | `integration/test_service_data_llm_firewall.py` | 57 | Full |

**Not covered:** Frontend TypeScript/React components (`frontend-next/`). There is no frontend test infrastructure in the project yet. See "Known Limitations" section below.

## Test Suites

> **Note on slot-extraction tests (Phase 4, April 2026).** The `USE_UNIFIED_EXTRACTOR` flag was deleted in Stage 2; `app.services.slot_extraction.extract()` is now the only path. The legacy `extract_slots_smart` in `app.services.llm_slot_extractor` still exists on disk but has no production callers — Stage 3 deletes that module. Test references to `extract_slots_smart` below are scheduled for migration in Stage 4. See `docs/design/UNIFIED_EXTRACTOR_MIGRATION.md`.

### `integration/test_classification_and_routing.py` — 193 tests

Validates the main chatbot routing — message classification (split classifier in `classifier.py`), slot extraction routing, PII redaction integration, confirmation flow (in `confirmation.py`), quick replies, emotional awareness (responses in `responses.py`), bot questions, privacy question handling, static fallbacks, context-aware yes/no, frustration loop detection, family composition, gender/LGBTQ identity handling, combined action+tone routing, tone prefix assertions, escalation guard, nearby borough suggestions, location-unknown interceptor, service flow continuation, and LLM fallback. External dependencies are mocked.

| Category | Tests | What's covered |
|---|---|---|
| `_classify_action` | 13 | Reset, greeting (short/long), confirm_yes, confirm_deny, bot_question, escalation, help, returns None for service, returns None for emotional, returns None for frustrated, returns None for confused, returns None for urgent |
| `_classify_tone` | 10 | Emotional, frustrated, confused, None for neutral, no service-word gate (detects emotion even with "need"/"food" present), urgent phrases (7 variants), emotional beats urgent, pure urgency |
| Combined routing | 10 | Emotional+service → service with prefix, help+service → service, escalation+service → service, confused+service → service with prefix, frustrated+service → service with prefix, pure emotional/help/escalation still work, urgent+service gets prefix |
| Escalation guard | 3 | Escalation+service without location → escalation, escalation+service+location → service, "talk to someone about shelter" → escalation |
| Message classification | 13 | All 16 routing categories including emotional, bot_question. Long messages not misclassified as greetings. Punctuation handling. Emotional distinct from confused. Bot question distinct from frustration and help. NOTE: these test `_classify_message()` (backward-compat wrapper for LLM fallback path); end-to-end routing uses `_classify_action()` + `_classify_tone()` directly |
| Privacy classification | 2 | 19 privacy phrases (ICE, police, benefits, recording, anonymity) all route to bot_question. Privacy phrasing not misclassified as service request |
| Routing paths | 12 | Greeting (with and without existing session), reset, thanks, help, bot question (direct answer, no slot extraction), service with results, no results, partial slots trigger follow-up, general conversation |
| Emotional awareness | 6 | Emotional classification (12 phrases), false negatives (service messages not caught), distinct from confused, peer navigator offered, no confirmation set, static fallback without LLM |
| Context-aware yes/no | 12 | "Yes"/"no" after escalation, emotional, frustration, confused — each with appropriate response. "Yes" after frustration resets. "Yes" after confused escalates. "No" after emotional has quick replies. Context cleared after unrelated message |
| Pending confirmation | 2 | Escalation clears pending confirmation, crisis clears pending confirmation |
| No pushy buttons | 2 | General responses don't push service menu after first turn, no menu mid-search |
| Frustration loop | 3 | Repeated frustration produces different response, pushes navigator harder, shorter than first response |
| Static bot answers | 14 | Pattern-matched fallbacks for: geolocation failure, geolocation general, outside NYC (211), service categories, ICE privacy, police privacy, benefits privacy, who-can-see, delete/clear, identity/anonymity, general privacy, how-it-works, unknown default |
| Bot question full flow | 3 | Privacy question gets specific answer, geolocation question explains failure, outside-NYC mentions coverage |
| Fallback behavior | 3 | DB failure → Claude fallback. Both fail → safe static message. Query error → Claude fallback |
| Multi-turn sessions | 2 | Slot accumulation across turns with confirmation. Reset then new search |
| Geolocation priority | 1 | Text location overrides stored browser coordinates from prior near-me search |
| PII in chatbot flow | 4 | Name/phone redacted in transcript but slots still extract, bot response PII (name) redacted before audit log, bot response PII (phone) redacted before audit log |
| Session ID | 2 | Auto-generated when none provided. Preserved when provided |
| Response structure | 2 | All 8 required keys present. Relaxed search flag |
| Service detail in confirmation | 3 | Confirmation uses service_detail ("dental care" not "health care"), falls back to generic label, change-service clears detail |
| Family status in confirmation | 6 | Confirmation mentions "children", "family", "yourself" per status. No mention when not set. Family status extracted during multi-turn shelter flow. family_status reaches query_services via _execute_and_respond |
| Confirmation & quick replies | 11 | Confirmation triggered, change location/service, greeting/reset/follow-up quick replies, new input re-extracts, results show post-search buttons, exact deny phrases, longer deny phrases |
| Bug fix regressions | 6 | "No" breaks confirmation loop, cancel variants trigger reset, expanded frustration phrases, thanks-with-continuation falls through, empty/whitespace message guard |
| Nearby borough suggestions | 8 | Basic no-results message, borough suggestions by service type, different services get different suggestions, neighborhood doesn't suggest boroughs, navigator always offered, all borough+service combos covered, unknown service falls back to default, unknown borough doesn't crash |
| Location-unknown interceptor | 4 | "I don't know" after location ask offers geolocation + boroughs, 10 phrase variants ("anywhere", "here", "idk"), exact-match "here" doesn't false-positive on "here's what I need", guards (no service_type → confused, location already set → confused) |
| Service flow continuation | 2 | "near me" continues service flow instead of falling to LLM, "close by" continues service flow |
| Escalation buttons | 4 | Frustration shows correct buttons, escalation shows buttons, "yes" after emotional shows escalation buttons, "no" after escalation shows buttons |
| Escalation phrase variants | 3 | "connect with a person" routes to escalation, "connect with peer navigator" routes to escalation, peer navigator label standardized |
| Location change UX | 1 | Location change shows "Use my location" as first option |

### `test_slot_extraction_regex.py` — 117 tests

Validates the regex-based slot extraction pipeline.

| Category | Tests | What's covered |
|---|---|---|
| Service type | 10 | All 9 categories plus false positive prevention |
| "Other services" keyword | 2 | Quick reply value "I need other services" and singular form both extract service_type=other |
| Service detail extraction | 7 | Sub-type labels: dental→dental care, therapy→therapy, immigration→immigration services, shower→showers, food pantry→food pantries, AA meeting→AA meetings. Generic "food" has no detail |
| Multi-service extraction | 17 | Two services, three services, no duplicate categories, "mental health" doesn't double-match "health", sub-type details preserved per service, single/no service edge cases, extract_slots returns primary + additional_services, merge_slots skips additional_services, complex multi-intent, find() scans forward past overlaps, text-position ordering (forward and reversed), word-boundary position ordering |
| Location | 6 | Preposition patterns, known NYC names, "near me" detection, false positives |
| Age | 3 | Multiple formats, out-of-range rejection |
| Urgency | 2 | High and medium levels |
| Family status extraction | 5 | Children phrases (7 variants), single parent→with_children, partner/spouse→with_family, alone phrases (5 variants), no false positives on non-family messages |
| Family status false positives | 3 | "I have a question" not matched, "me and my friend" not matched, "feeling alone" (emotional) not matched |
| Family status combined | 1 | Children + service_type + location extracted together |
| Family follow-up | 3 | Shelter asks about family when age provided, food doesn't ask, already-set family not re-asked |
| Multi-slot | 2 | Single message filling multiple slots |
| Merge logic | 11 | New over empty, preserving existing, near-me sentinel overrides stale location, real location overrides sentinel, stale service_detail cleared on service change, detail persists when service unchanged, new detail replaces old, additional_services skipped during merge |
| Flow control | 5 | `is_enough_to_answer`, follow-up question routing |
| Word-boundary keywords | 12 | Restored collision-prone keywords (bed, wash, id, eat, hat) match correctly and don't false-positive on location names |
| New keywords | 10 | Expanded coverage across all 9 categories + urgency terms for target population |
| NYC zip codes | 7 | Specific zip→neighborhood mapping, borough fallback for unknown zips, non-NYC zip returns None, zip in sentence, zip with service, zip doesn't conflict with age, zip overridden by known location |

### `test_edge_cases.py` — 29 tests

Cross-cutting tests from the architecture spec and user testing plans.

| Category | Tests | What's covered |
|---|---|---|
| Location normalization | 4 | Borough → DB city mapping, neighborhood mapping, unknown locations, whitespace |
| Template resolution | 2 | All service types resolve. Unknown types return None |
| Multi-intent | 1 | "Food and shelter" extracts both; first is searched, second queued (PR 3) |
| Location edge cases | 4 | Non-NYC locations, mixed case, mid-conversation changes |
| Minor + urgency | 3 | 17-year-old shelter scenario from the architecture spec |
| PII + slot interaction | 3 | Name redacted but slots preserved. Age not treated as PII |
| Near-me multi-turn | 1 | Full simulation through to query |
| Empty / garbage input | 5 | Empty string, whitespace, single words, bare numbers |
| Keyword overlap | 5 | "Mental health" → mental_health, "food stamps" → other, etc. |

### `test_location_boundaries.py` — 65 tests

Validates location normalization, borough expansion, proximity search, and that queries stay within NYC boundaries.

| Category | Tests | What's covered |
|---|---|---|
| State filter | 3 | NY state filter present in all templates, preserved in relaxed queries |
| City filter | 2 | Exact match for strict, normalized borough names |
| Relaxed boundaries | 4 | City broadened to LIKE not dropped, eligibility/schedule dropped but location kept |
| Borough normalization | 3 | All 5 boroughs, all aliases, case-insensitive |
| Location extraction | 6 | Full sentences, non-NYC locations, "near me in Brooklyn" |
| Query builder | 6 | Filter presence based on params, defaults and overrides |
| Borough expansion | 7 | Queens/Brooklyn/Manhattan expand to neighborhoods, ANY() SQL |
| Neighborhood proximity | 15 | All neighborhoods have coordinates within NYC bounds, proximity search integration |
| DB connection | 1 | `test_connection` returns False without DATABASE_URL |

### `test_query_templates.py` — 118 tests

Validates query template correctness, SQL structure, service card formatting, schedule computation, result sorting, and shelter taxonomy enrichment.

| Category | Tests | What's covered |
|---|---|---|
| Taxonomy names | 10 | Every template's taxonomy_name validated against actual DB values |
| Base query structure | 5 | Required JOINs, phone priority, schedule LATERAL, location slug |
| Service card formatting | 10 | All fields, YourPeer URL, website fallback, URL normalization |
| Schedule status | 9 | None values, string/object times, midnight wrap, invalid inputs |
| Time formatting | 6 | Cross-platform (no %-I), all periods, no leading zeros |
| Deduplication | 5 | Removes by service_id, keeps first, edge cases |
| Generated SQL | 4 | Parameterized (no injection), strict vs relaxed params |
| Result sorting | 6 | Open-now priority, proximity-first with distance, freshness ordering, relaxed sort consistency |
| Shelter taxonomy enrichment | 8 | Youth (always, age eligibility handles exclusion), senior (age≥62), families (with_children), single adult (alone), LGBTQ Young Adult (always), base taxonomies preserved, food queries not enriched, TEMPLATES default_params not mutated |

### `test_crisis_detector.py` — 42 tests

Validates crisis detection across five categories with correct hotline resources and no false positives.

| Category | Tests | What's covered |
|---|---|---|
| Suicide / self-harm | 4 | Direct statements, self-harm, 988 + Crisis Text Line + Trevor Project |
| Violence | 2 | Threats, 911 in response |
| Domestic violence | 3 | Abuse language, National DV + NYC DV hotlines |
| Trafficking | 2 | Labor/sex trafficking, National Trafficking Hotline |
| Medical emergency | 3 | Emergency language, 911, Poison Control |
| False positive prevention | 3 | Service requests, conversational messages, "hurt" in non-crisis context |
| Priority / integration | 3 | `is_crisis()` helper, crisis in longer messages, crisis alongside service requests |

### Slot extraction tests

The slot extractor tests live in `test_slot_extraction.py` (246 unit tests, mocked-LLM) and `tests/integration/test_slot_extraction_live.py` (5 live tests, requires API key). These cover the unified extractor architecture introduced in the rev 17 migration.

<!-- drift:ignore: deletion-of-files historical references -->
The legacy `test_llm_slot_extractor.py`, `test_llm_classifier.py`, `test_llm_multi_service.py`, and `test_narrative_extraction.py` files were deleted in Phase 4 Stage 4a (April 2026); their coverage was ported into `test_slot_extraction.py` as part of the migration. See `docs/design/UNIFIED_EXTRACTOR_MIGRATION.md` for the full mapping.

| Category | Tests | Class in `test_slot_extraction.py` |
|---|---|---|
| Trust-model merge logic | 60+ | `TestTrustModel1*` through `TestTrustModel5*` |
| Tool output normalization | 35 | `TestNormalizeToolOutput`, `TestNormalizeToolOutputValidation` (age range, case + whitespace, empty-string handling) |
| Dispatch decisions | 16 | `TestExtract[NoApiKey|SimpleFastPath|NarrativePath|ShortPath]`, `TestIsNarrative`, `TestIsSimpleMessage` |
| Conversation history | 6 | `TestBuildMessagesWithHistory` (alternation enforcement, truncation, placeholder padding) |
| Multi-service extraction | 11 | `TestHybridAdditionalServices` (regex+LLM merge, dedup, primary exclusion) |
| Narrative regex fallback | 11 | `TestNarrativeRegexFallback` + `TestNarrativeRegexFallbackRealisticScenarios` (hospital/housing, runaway youth, eviction, reentry) |
| End-to-end narrative dispatch | 4 | `TestExtractEndToEndNarrative` (no-mock fallback chain) |
| Live API extraction | 5 | `tests/integration/test_slot_extraction_live.py` (skipped without API key) |

### `test_audit_log.py` — 70 tests

Validates all 13 public functions in the audit log module.

| Category | Tests | What's covered |
|---|---|---|
| Log conversation turn | 5 | Correct fields, internal slot stripping (`_pending_confirmation`, `transcript`, None values), quick reply label extraction, None slots, conversation registration |
| Request correlation IDs | 4 | request_id stored in turn events, defaults to None, stored in query execution, stored in crisis events |

### `test_semantic_router.py` — 41 tests

Validates the Tier 2 semantic routing module: route definitions, model initialization, classification, threshold behavior, population detection, integration with `pipeline._run_early_extraction()` (regex + semantic) and `slot_extraction.extract()` (LLM merge), graceful degradation, and observability. Uses mock embedding models with controlled vectors for deterministic testing — no real model download required.

| Category | Tests | What's covered |
|---|---|---|
| Route definitions | 9 | All service/population routes exist, minimum utterances per route (10 service, 6 population), no duplicates within or across routes, no empty utterances, phrases not keywords (≥3 words), eval failure scenarios covered (insulin→medical, felon→employment, foster care→shelter), route keys align with `SERVICE_KEYWORDS` |
| Initialization | 5 | Mock model init, not-available before init, classify returns None before init, reset clears state, init with populations |
| Service classification | 4 | High similarity matches, low similarity rejected, runner-up tracking, threshold override |
| False positive rejection | 2 | Casual greeting no match (orthogonal embeddings), per-route threshold for "other" |
| Population detection | 2 | Population detected alongside service, lower threshold than service routes |
| Threshold behavior | 2 | Exact threshold passes, below threshold rejected (controlled cosine similarity) |
| Diagnostics correctness | 2 | get_status returns scores for all routes sorted by max_similarity, includes population routes |
| Route alignment | 3 | SERVICE_ROUTES keys match SERVICE_KEYWORDS keys, no cross-route duplicates, all routes covered by SERVICE_KEYWORDS |
| Integration with `_run_early_extraction` | 5 | Semantic fills missing service_type, population merged with regex (set union), skips when regex resolves, short message LLM-gate short-circuits when semantic resolves, semantic miss leaves source=None |
| Integration fallthrough | 7 | Semantic returns None falls through, semantic unavailable falls through, narrative messages still run semantic (behavior change vs. legacy), Trust Model 3 sets-agree branch, sets-disagree without semantic source (LLM wins), sets-disagree with semantic source (semantic wins, Phase 4 Stage 3 follow-up), per-slot trust contract under semantic source (semantic→service_type, regex/LLM→location, LLM→additional_services) |

### `test_audit_log.py` (continued — the table below shows the audit log tests that follow the semantic router section)

| Category | Tests | What's covered |
|---|---|---|
| Log query execution | 1 | Dual insertion (events + query log), `max_results` stripped |
| Log crisis detected | 1 | Event fields, session association |
| Log session reset | 1 | Event logged |
| Get recent events | 3 | Limit, type filtering, returns latest not earliest |
| Get conversation | 2 | Multi-event-type sessions, empty for unknown IDs |
| Get conversations summary | 5 | Turn count aggregation, crisis flag, limit, recency sort, categories as lists (JSON-serializable) |
| Get query log | 2 | Only queries, limit |
| Get stats (basic) | 5 | All counters, category/service distributions, relaxed query rate, empty state |
| Get stats (pilot metrics) | 10 | Escalation count, service intent sessions, slot correction rate, confirmation breakdown, confirmation abandon rate, slot confirmation rate (partial + full), data freshness rate, no-query edge case, legacy queries without freshness |
| Get stats (conversation quality) | 7 | Emotional detection rate, emotional → escalation, emotional → service, bot question rate, bot question → frustration, conversational discovery rate, empty state |
| Eval results | 5 | Set/get round-trip, deep copy isolation, None when unset, file loading, missing file |
| Clear | 1 | Wipes everything including eval results |
| Ring buffer | 3 | Caps at MAX_EVENTS, evicts oldest, conversation index stays within MAX_CONVERSATIONS |
| Thread safety | 1 | 17 concurrent threads logging and reading simultaneously |

### `test_admin_api_routes.py` — 28 tests

HTTP-level tests for the admin API endpoints using FastAPI TestClient.

| Category | Tests | What's covered |
|---|---|---|
| Admin auth | 5 | Open when no key set, rejects missing header, rejects wrong key, accepts correct key, protects eval/run |
| Stats | 2 | Empty state returns zeros, populated state returns correct counts |
| Conversations list | 3 | Summaries, limit parameter, limit validation (rejects 0 and 999) |
| Conversation detail | 3 | All event types returned, 404 for unknown ID, crisis session includes both event types |
| Events | 5 | All events, type filtering, invalid type → 422, limit, limit validation |
| Queries | 2 | Returns only query executions, limit |
| Eval | 2 | 200 with null results when empty, returns data when set |
| Eval run guards | 2 | 500 when no API key, 409 when already running |
| Admin rate limits | 2 | 429 after exceeding IP limit, stricter eval/run limit |
| Health | 1 | `GET /api/health` returns ok |

### `test_http_routes_and_models.py` — 50 tests

HTTP-level tests for the chat endpoint and Pydantic model validation.

| Category | Tests | What's covered |
|---|---|---|
| ChatRequest model | 8 | Valid construction, optional session_id, missing message rejected, wrong type, empty string, accepts 1,000-char message, rejects 1,001-char message, rejects oversized at HTTP level (422) |
| Coordinate validation | 5 | Valid coordinates accepted, boundary values (±90/±180), invalid latitude rejected, invalid longitude rejected, invalid coordinates return 422 |
| Request correlation ID | 2 | X-Request-ID echoed in response, generated when not provided |
| ServiceCard model | 4 | Minimal (service_name only), full (all 13 fields), missing required rejected, serialization |
| QuickReply model | 3 | Valid, missing label rejected, missing value rejected |
| ChatResponse model | 4 | Minimal with defaults, nested ServiceCards, missing required rejected, JSON round-trip |
| HTTP basics | 8 | Valid 200, session_id generated/preserved, missing message 422, non-JSON 422, no body 422, empty message guard, response schema validation |
| HTTP multi-turn | 4 | Full conversation with service cards, slot accumulation, reset, quick reply structure |
| HTTP error handling | 2 | Crash returns yourpeer.nyc link (not 500), session_id preserved on crash |
| HTTP crisis | 1 | Returns 988 resources, no service cards, query_services not called |
| HTTP method | 1 | GET /chat/ returns non-200 |
| Session token validation | 5 | Forged session_id → 403, tampered signature → 403, valid signed token → 200, first message mints signed token, feedback rejects forged token |
| Serialization drift | 3 | New service card fields survive Pydantic serialization, quick reply href survives serialization, full response preserves new fields through HTTP |

### `test_pii_redactor.py` — 34 tests

Validates PII detection and redaction across eight PII types plus bot response redaction.

| Category | Tests | What's covered |
|---|---|---|
| Phone numbers | 1 | 5 formats |
| SSN | 1 | Hyphenated and space-separated |
| Email | 1 | Standard format |
| Dates of birth | 1 | 4 formats |
| Street addresses | 2 | Named streets (123 Main Street), numbered streets (456 West 42nd Street, 789 5th Avenue), Broadway. False positive prevention (bare street names without house numbers) |
| Names | 1 | Intro phrases with blocklist |
| Expanded names | varies | Extended name detection coverage |
| Credit card | varies | Credit card number detection |
| URL | varies | URL detection in messages |
| False positives | 2 | NYC locations and service keywords not redacted |
| Multiple PII | 1 | Combined detection |
| Clean passthrough | 1 | No PII → no changes |
| Quick check | 1 | `has_pii()` utility |
| Bot response redaction | varies | PII scrubbed from bot responses before audit log storage |
| ICE/police routing | varies | ICE and police mentions route correctly without PII false positives |
| Overlap handling | varies | Overlapping PII patterns handled correctly |
| Integration | varies | End-to-end PII redaction through the chatbot pipeline |

### `test_claude_client.py` — 19 tests

Unit tests for the Claude LLM client. All external calls mocked.

| Category | Tests | What's covered |
|---|---|---|
| Lazy initialization | 2 | First call creates client, subsequent calls reuse it |
| Missing env vars | 1 | Missing `ANTHROPIC_API_KEY` raises |
| Error caching | 2 | Init failure cached (no retry), `genai.Client()` failure cached |
| Reply success | 2 | Returns response text, None text → empty string |
| Reply failure | 2 | API exception → fallback string, init failure → fallback string |

### `test_session_store.py` — 7 tests

Validates session CRUD and thread safety.

| Category | Tests | What's covered |
|---|---|---|
| Basic operations | 5 | Save/get round-trip with deep copy, nonexistent returns {}, clear, clear nonexistent, overwrite |
| Thread safety | 2 | 30 concurrent threads (1,500 operations), lock existence |

### `test_session_token.py` — 12 tests

Validates HMAC-signed session token generation and verification.

| Category | Tests | What's covered |
|---|---|---|
| No secret (dev mode) | 2 | Unsigned tokens generated, any string accepted |
| With secret (production) | 5 | Signed tokens generated, generate-then-validate round-trip, unsigned rejected, forged signature rejected, tampered payload rejected |
| Edge cases | 3 | Empty string rejected, bare dot rejected, wrong secret rejected |
| Format robustness | 2 | Tokens with dots in raw portion handled correctly, constant-time comparison used |

### `test_browser_geolocation.py` — 14 tests

Validates browser geolocation support: coordinate acceptance, session storage, "near me" + coords flow, proximity query integration, and geolocation failure fallback.

| Category | Tests | What's covered |
|---|---|---|
| Pydantic model | 2 | ChatRequest accepts lat/lng, coordinates optional |
| Session storage | 2 | Coords stored when provided, absent when not |
| Near-me + coords | 3 | Triggers confirmation (not borough ask), shows "near your location", sentinel not exposed |
| Full flow | 1 | food near me → confirmation → confirm → results with coords passed to query_services |
| RAG integration | 2 | Direct coords build proximity params, coords override location name |
| Cross-turn persistence | 1 | Coords persist in session across messages |
| Geolocation failure fallback | 3 | Borough typed after failed geolocation, borough button tapped after failure, near-me sentinel replaced by real borough |

### `test_rate_limiter.py` — 14 tests

Validates the sliding-window rate limiter logic.

| Category | Tests | What's covered |
|---|---|---|
| Per-session limits | 4 | Messages per minute/hour/day, retry_after calculation |
| Per-IP limits | 3 | IP-level rate limiting, separate from session limits |
| Feedback limits | 2 | Feedback endpoint rate limiting |
| Bucket management | 3 | Sliding window cleanup, thread safety, clear() |
| Memory management | 2 | Forced eviction when bucket cap exceeded, no forced eviction under cap |

### Rate limiting (consolidated into integration suite)

HTTP-level rate limiting coverage was previously in a standalone file and is now exercised within the broader integration suite (see `tests/integration/test_admin_api_routes.py` for middleware-level tests, and `conftest.py` for the fixtures that back them). Behaviors covered: 429 responses with crisis resources, session-based vs IP-based limiting, and middleware-route attachment.

### DB integration (consolidated into `tests/integration/`)

Database integration tests that run against the real Streetlives PostgreSQL database are now distributed across the integration suite and automatically skipped when `DATABASE_URL` is not set. Behaviors covered: schema validation (11 tables, required columns, PostGIS geometry type, JSONB eligibility, timestamp freshness, taxonomy-name consistency), query execution (all 9 templates strict/relaxed, proximity search, distance ordering, age/gender eligibility, open-now and freshness sorts, city-list ANY(), combined filters), result formatting (real rows → valid service cards), and end-to-end `query_services()` pipelines (borough, neighborhood, coords, relaxed fallback, card field completeness).

### `test_main.py` — 14 tests

HTTP-level tests for the FastAPI app configuration (headless API mode).

| Category | Tests | What's covered |
|---|---|---|
| Health | 1 | `GET /api/health` |
| Root | 1 | `GET /` returns JSON message (no static file serving) |
| API routing | 3 | `/api/health`, `POST /chat/`, `/admin/api/stats` all routed correctly |
| CSRF protection | 6 | Valid origin allowed, evil origin → 403, non-browser (no headers) allowed, Sec-Fetch-Site without origin → 403, valid Referer allowed, evil Referer → 403 |
| CORS | 3 | Headers present for allowed origin, no headers for unknown origin, preflight OPTIONS |

### `test_phrase_audit.py` — 42 tests

Validates phrase additions from the P0–P3 audit (see PHRASE_LIST_AUDIT.md). Parametrized tests cover C-SSRS suicide ideation phrases, Joiner IPT burdensomeness markers, DV coercive control, youth safety/runaway, shame/stigma emotional phrases, grief with service routing, expanded frustration phrases, and confused/overwhelmed phrases.

### `test_contraction_normalization.py` — 19 tests

Validates `_normalize_contractions()`, `_strip_intensifiers()`, and their integration with `_classify_tone()`. Covers individual contraction expansions, full sentences, multiple contractions, non-contraction preservation, frustration/confused/emotional detection via normalization, help-negator handling ("doesn't help" → frustration not help), intensifier stripping for emotion/frustration/confused classification, and confirms normalization does not affect crisis detection (which uses explicit enumeration).

### `test_crisis_and_flow_regressions.py` — 28 tests

Regression tests for structural fixes across 8 test classes. Covers: PII safety warnings (SSN strong warning, phone light heads-up, combined with service flow), foster youth population (aging out → foster_youth not reentry, confirmation shows youth-friendly), pregnant ≠ with_children (pregnancy sets population tag only), youth_runaway crisis category (Runaway Safeline + Covenant House, distinct from DV), assault_victim crisis category (Safe Horizon Victim Services), safety_concern response de-DV'd (988 + 311, no DV hotlines), confirmation warm reframe ("I'll look for..." format), results personalization ("I found X option(s) for you"), and baseline warmth prefixes (random_warmth_prefix fires on routine service flows, doesn't override emotional/shame/urgent contexts).

### `test_targeted_bug_regressions.py` — 30 tests

Targeted regression tests for bugs 8–14 identified during PR 19 review. Organized by bug number:

| Bug | Tests | What's covered |
|---|---|---|
| Bug 8: `log_feedback` missing | 3 | Importable, stores event, works without optional comment |
| Bug 9: Confirmation missing "in" | 5 | "in Brooklyn", all 5 boroughs, neighborhoods, "near your location" no "in", end-to-end flow |
| Bug 10: "nobody cares" over-escalation | 8 | Bare phrase removed from crisis list, specific form retained, `detect_crisis` returns None for bare phrases, specific forms still trigger crisis, `_classify_tone` routes to emotional |
| Bug 11: Double `detect_crisis` call | 5 | Accepts pre-computed result, skips call when provided, calls when omitted, `generate_reply` calls once for normal messages, once for crisis messages |
| Bug 12: `_URGENT_PHRASES` module-level | 3 | Importable, identity stable across imports, urgent tone detected |
| Bug 13: Frustration normalization | 4 | Contraction variants detected by `_classify_message`, consistency with `_classify_tone` |
| Bug 14: Smart extractor fallback | 2 | Regex additional_services preserved, returned result matches direct regex |

### `test_post_results.py` — 69 tests

Post-results question handler — answers follow-up questions about displayed services using only stored card data (zero LLM). Covers 7 intent classification types, answer builder handlers, chatbot integration flows, safety (crisis after results), skip_llm optimization, call button `href` with `tel:` links, detail view with `also_available`, call QR deduplication, and no-cost variant handling.

### Research-sourced crisis edges (consolidated into `tests/unit/test_phrase_audit.py`)

Research-sourced crisis detection edge cases from C-SSRS (5 severity levels), HITS/SAFE DV screening, Polaris trafficking indicators, SAMHSA TIP 55 homeless population patterns, and Covenant House/Ali Forney youth research. Now exercised inside the phrase-audit suite. Coverage spans regex phrase-list coverage (what the instant check catches), LLM-dependent gap roadmap (phrases that require context the regex can't see), post-results safety, and false-positive guards.

### `test_utility_and_session_edges.py` — 36 tests

Coverage gap tests for 8 high/medium priority areas: zip code full flow (4), crisis step-down + multi-intent (2), LLM contradictory category (2), near-me sentinel safety (3), session_exists (3), get_client_ip (5), _extract_session_id (4), _normalize_url (9), feedback→stats (4).

### `test_format_pipeline_and_admin.py` — 41 tests

Comprehensive gap coverage for 9 areas identified during audit: `_compute_freshness` timezone/boundary handling (8), admin `/api/stats` response shape for routing/tone/multi_intent (6), post-results through `generate_reply` end-to-end (4), `skip_llm` through chatbot pipeline (2), `also_available` in post-results detail view (4), `last_validated_at` timezone edge cases (4), multi-intent queue decline with 2-item queue (2), prompt builder function shapes and guardrails (8), `format_service_card` deduplication and filtering (5).

### `test_persistence.py` — 27 tests

SQLite pilot persistence layer. Tests direct CRUD operations on all 3 tables (events, sessions, eval_data) including ordering, limits, upserts, and clears (12 tests). Disabled mode (PILOT_DB_PATH unset) verifies all operations are safe no-ops (6 tests). Audit log hydration round-trip: write events → clear in-memory → hydrate from SQLite → verify stats (4 tests). Session store hydration: write → clear → hydrate → verify slots (4 tests). Full restart simulation: user interaction → destroy in-memory state → hydrate → verify everything is restored (1 test).

### `test_bot_knowledge.py` — 44 tests

Validates the bot self-knowledge module: live capability sourcing from actual code, topic matching for 12+ question types, LLM context generation, static handler integration, bot question phrase classification, untested topic coverage, topic collision prevention, false positive guards, full chatbot routing for privacy/location/services questions, and freshness guards that fail loudly when bot_knowledge claims drift out of sync with live code.

| Category | Tests | What's covered |
|---|---|---|
| Live capability sourcing | 3 | Service categories sourced from code, PII categories sourced from code, location count sourced from code |
| Topic matching | 9 | Services, location failure, privacy (general, ICE, benefits), coverage, how-it-works, limitations, no-match returns None |
| Capability context | 6 | Includes service categories, PII types, location count, privacy, crisis, emotional sections |
| Static handler integration | 3 | Location question, privacy question, unknown question gets default |
| Bot question classification | varies | Privacy phrases classify as bot_question |
| Untested topics | 6 | Language, peer navigator, privacy delete, identity, police, visibility |
| Topic collisions | 5 | Location/privacy, police/location, ICE/share, delete/privacy, services/coverage collision prevention |
| False positives | varies | Service and action messages don't match topics |
| Bot question routing | 3 | Privacy/location/services questions route correctly through chatbot |
| **Freshness guards (April 2026)** | **7** | **PII-type claims match `_PLACEHOLDERS`, service-category count matches `SERVICE_KEYWORDS`, crisis-category list matches `_CRISIS_CATEGORIES`, friendly-name map covers every live crisis category, no "English only" regression, no stale monolith source refs (from the pre-Phase-3 era before the `chatbot/` package decomposition). Each guard has proven strip-discipline: reintroducing its specific drift produces a targeted failure message naming what to update.** |

### `test_schema_and_mock_sync.py` — 20 tests

Prevents silent data loss at serialization boundaries by asserting that mock fixtures, Pydantic models, SQL queries, and format functions all agree on the same field set. Catches the class of bug where new fields are added to one layer but not others.

| Category | Tests | What's covered |
|---|---|---|
| Mock drift | 3 | Mock service card has all Pydantic fields, no extra fields, required keys present |
| Format/Pydantic sync | 2 | Format output matches Pydantic fields, survives Pydantic round-trip |
| SQL/format sync | 1 | Format reads subset of SQL aliases |
| Reply/response sync | 1 | Reply keys match ChatResponse model |
| Full pipeline | 2 | Service fields survive full pipeline, quick reply href survives |
| Admin stats drift | 5 | Top-level keys, confirmation breakdown shape, conversation quality shape, tone distribution shape, multi-intent shape |
| Persistence failure isolation | 6 | log_conversation_turn, log_query_execution, log_feedback, save/clear session, full generate_reply all survive persistence failures |

### `test_multi_turn_and_context.py` — 56 tests

Comprehensive regression tests for multi-turn, multi-intent, and context-aware routing. Guards against state transition bugs, _last_action lifecycle issues, frustration counting, and handler interaction patterns found in eval analysis.

| Category | Tests | What's covered |
|---|---|---|
| _last_action lifecycle | 4 | Context handlers set _last_action, context shift clears it, help after emotional doesn't leak yes/no, service flow clears it |
| Confirm deny / service change | 6 | Change mind updates service, no with new service updates, deny with service+location change, plain deny preserves slots, "wait" is not deny, "hold on" lets message through |
| Yes after context | 5 | Yes after emotional/escalation/frustration/confused connects navigator, escalation shows distinct response with service buttons |
| No after context | 3 | No after emotional/escalation/frustration is gentle |
| Frustration counter | 5 | First sets count, second increments, second is shorter, persists across searches, reset clears |
| Emotional+service transitions | 5 | Emotional then service works, clears emotional state, shame gets normalizing prefix, pending confirmation then emotional, emotional adjective forms |
| Slot persistence | 4 | Location persists across service change, updates when provided, results then new service, age persists across turns |
| Complex flows | 5 | Emotional→service→frustration→navigator, escalation decline then service, service change then confirm, double emotional different emotions, frustrated reset clean slate |
| Unrecognized service escalation | 9 | Tiered responses (first lists categories, second adds navigator, third just navigator), responses differ, recovery after, reset clears count, sticky detection for nonsense, location preserved, no-location first turn |
| Other service type interception | 2 | "Other" without detail is unrecognized, with detail is legitimate |
| Implicit service change | 8 | Direct service change, negation with new service, same service different location, confirm_yes unaffected, location carries over, shows new confirmation, additive keeps primary, additive then confirm searches primary |

### `test_narrative_and_eval_scenarios.py` — 33 tests

Integration tests that send messages through the full `generate_reply` pipeline. Reproduces failing eval scenarios and tests cross-feature interactions: narrative + emotional, PII in narratives, shame prefix + narrative extraction, session isolation.

| Category | Tests | What's covered |
|---|---|---|
| Narrative integration | 7 | Hospital/housing, re-entry, eviction/family, runaway youth, narrative shows confirmation, queues additional services, short message not narrative path |
| Cross-feature interactions | 4 | Emotional narrative with service, shame narrative normalizing prefix, intensifiers in narrative, frustration then narrative |
| PII in narratives | 4 | Phone, name, SSN, multiple PII in narrative messages |
| Session isolation | 2 | Two sessions independent, emotional state doesn't leak |
| Eval scenario approximations | 12 | Emotional scared/feeling-down/rough-day, change mind, yes after escalation, frustration loop, long story, tell my story, re-entry, fake service, nonsense service, shame shelter stigma |

### `test_post_results_boundary.py` — 31 tests

Validates the boundary between post-results follow-up questions and new service requests. Tests that users are never trapped in the post-results handler when starting a new search. Covers the new-request escape hatch, location-based result clearing, name-match fallthrough, and disambiguation prompts.

| Category | Tests | What's covered |
|---|---|---|
| New request escapes | 10 | "I need X", "where can I go", "looking for", "can I get", "help me find", "search for", "is there", "do you have", new location clears results |
| Unrecognized service escapes | 3 | "What about financial services?", "What about detox?", narrative with shelter keyword after food results |
| Genuine post-results still work | 8 | Open filter, index, phone/address/hours fields, free filter, named result match, "what about [exact name]", show all |
| Ambiguous edge cases | 6 | Bare "where?", crisis trumps post-results, reset clears, emotional not intercepted, service keyword escapes, multiple new requests |
| Classifier unit tests | 2 | 17 parametrized new-request phrases return None, 6 genuine post-results phrases still classified |
| Name match fallthrough | 2 | Unmatched name returns None, matched name returns response |

### Ambiguity handling (consolidated into audit-regression + eval suites)

The four industry-recommended ambiguity handling patterns — confidence scoring, disambiguation prompts, correction recovery, and ambiguity logging — are now exercised across `tests/unit/test_audit_regression.py` (regression guards for the individual behaviors) and `tests/eval/eval_llm_judge.py` (end-to-end scoring of ambiguous scenarios). Behaviors covered: confidence scoring for regex/reset/keyword/correction/disambiguation cases, unmatched-name disambiguation prompts, the 5 correction phrases with their slot-clearing semantics, "Not what I meant" button wiring, and audit-event logging of the correction/disambiguation categories with confidence fields.

### `test_populations.py` — 93 tests

Validates Phase 3 (population context extraction and query boosts) and Phase 5 (DV crisis → population injection). Covers the full pipeline: regex extraction → session merge → query parameter generation → ORDER BY SQL → confirmation message → LLM schema compliance.

| Category | Tests | What's covered |
|---|---|---|
| Population extraction | 7 | Veteran (6 phrases), disabled (6), reentry (6), dv_survivor (6), pregnant (3), senior (4), no-population returns empty |
| Multiple populations | 5 | Disabled veteran (2 extracted), pregnant + DV, senior + disabled, reentry + veteran, sorted deterministic output |
| False positive guards | 5 | Salvation Army ≠ veteran, disabled account ≠ disabled, veterans day, veterans memorial, blind spot |
| Pregnant coexistence | 2 | family_status=with_children AND populations=pregnant both fire |
| extract_slots integration | 3 | Populations in extract_slots output, empty list when none, service type not shadowed |
| Merge semantics | 5 | List union across messages, deduplication, preserves when new empty, creates from empty, sorted output |
| Senior auto-infer | 4 | age=65 adds senior, age=30 no senior, age=62 boundary, explicit senior not doubled |
| Veteran taxonomy boost | 3 | veteran_boost set, non-veteran no boost, no population no boost |
| Description boost (ORDER BY) | 6 | Disabled pattern, reentry pattern, dv_survivor pattern, pregnant pattern, multiple populations combine, separate from Phase 4 sub-category filter |
| Confirmation message | 7 | Veteran-friendly, accessible, reentry-friendly, senior without age, senior suppressed with age, no prefix when empty, LGBTQ takes priority |
| No-boost guard | 2 | Empty list and None both produce no boost params |
| Accessibility on cards | 2 | Present and absent cases |
| has_new_slots guard | 2 | Empty _populations doesn't trigger, population + service_type does |
| Unified extractor schema | 4 | Tool schema includes populations field, _empty_slots returns [_populations], populations enum has canonical set, narrative prompt mentions populations |
| Word boundary | 7 | "vet" matches, not in veterinarian/veto/vetted, "army" matches, not in salvation army (2 variants) |
| Service keyword overlap | 6 | "disabled" as both service and population, disability services, wheelchair + food, disabled veteran food, reentry + employment |
| Confirmation prefix integrity | 3 | LGBTQ + veteran (LGBTQ wins), LGBTQ + disabled, no gender + veteran |
| LLM merge | 3 | Union of LLM + regex populations, LLM empty + regex has data, both empty |
| ORDER BY builder | 5 | pop_boost_pattern in SQL, absent when not set, LGBTQ coexist, veteran coexist, distance coexist |
| DV crisis injection | 8 | With service intent, without service intent, non-DV no injection, preserves existing, no duplicate, step-down offers search, persists after confirm, follow-up gets boost |

### `test_org_name_search.py` — 21 tests

Validates organization name search (Gap 3): regex extraction with false-positive guards, slot integration, confirmation messages, query template configuration, routing through query_services, and full chatbot pipeline integration.

| Category | Tests | What's covered |
|---|---|---|
| Org name extraction | 5 | Known orgs matched (8 names), abbreviations (4), false positives rejected (6), no-org messages (4), case insensitive |
| Slot integration | 5 | extract_slots includes org_name, no org returns None, org_name alone sufficient, service_type still needed without org, coexistence |
| Confirmation messages | 4 | Org only, org + location, org + service + location, normal without org |
| Query template | 3 | Template exists, no taxonomy filter required, uses org_name_pattern filter |
| Query routing | 2 | Routes to org_name template with ILIKE, location params applied alongside |
| Chatbot integration | 2 | Org name triggers confirmation, org + location confirms |

### `test_service_card_display.py` — 46 tests

Validates service card display features: phone extensions, eligibility formatting, sub-category labels, accessibility, review highlights, SQL structure, and pagination/show-more handler.

| Category | Tests | What's covered |
|---|---|---|
| _format_phone | 9 | None, no extension, empty, "None", "n/a", valid extension, whitespace, card with/without extension |
| _format_eligibility | 16 | None, empty, all_ages, age range/min/max, gender single/both, combined, familySize, unknown param, null/empty values, card integration |
| Also available labels | 6 | Taxonomy name mapping, "Other service" excluded, granular names preserved, deduplication, empty/null |
| Accessibility | 2 | Present and absent |
| Review highlights | 3 | Present, absent, SQL references location_comment_highlights |
| SQL structure | 4 | phone_extension, eligibility_rules, review_highlight, accessibility_info in _BASE_QUERY |
| Show more / pagination | 6 | Initial cap at 10, QR offered, no QR for few results, remainder returned, 6 patterns recognized, show all returns everything |

### `test_location_feedback.py` — 7 tests

Validates per-location feedback logging and stats aggregation.

| Category | Tests | What's covered |
|---|---|---|
| Audit log | 5 | Full ratings stored, partial ratings, no ratings, multi-location, conversation registration |
| Stats | 2 | Count included in stats, zero when none |

### `test_walk_in_and_card_extras.py` — 28 tests

Validates walk-in filter, required documents, and languages spoken on cards.

| Category | Tests | What's covered |
|---|---|---|
| Walk-in extraction | 8 | Walk-in phrases, no referral, no appointment, open to anyone, no membership, negatives, extract_slots integration |
| Walk-in routing | 2 | no_requirements param set when true, omitted when false |
| _clean_list | 7 | None, empty, all-None, "None" string, empty strings, normal list, single item |
| Required documents on cards | 4 | Present, absent, filters junk, empty list |
| Languages on cards | 4 | Present, absent, filters null, empty list |
| SQL structure | 3 | required_documents, languages_spoken, no_requirements filter exists |

### `test_results_enhancements.py` — 24 tests

Validates sort options, day-specific hours, and urgent auto-execute.

| Category | Tests | What's covered |
|---|---|---|
| Auto-execute | 4 | High urgency skips confirmation, high urgency + location executes, medium still confirms, no location still asks |
| Sort patterns | 5 | Sort by verified, by services, updates session, QR offered, unrecognized falls through |
| Day detection | 7 | Saturday/Sunday/Monday/abbreviated/weekend detected, day without hours context ignored, generic hours still works |
| ISODOW mapping | 3 | Monday=1, Saturday=6, Sunday=7, abbreviations match |
| Hours for day handler | 3 | Hours per service with mock DB, no-data message, weekend fetches both days |
| Schedule DB function | 2 | Empty input returns {}, SQL uses correct params |

## LLM-as-Judge Evaluation

End-to-end evaluation runs the full conversational pipeline against 171 scripted scenarios, has Claude Opus score each transcript across 11 weighted dimensions, and produces a structured JSON report with passing/failing breakdowns, critical-failure call-outs, and per-category averages. It costs ~$15-25 and ~30-60 minutes per full run, so it sits separately from the unit/integration suite covered above.

The eval script lives at `tests/eval/eval_llm_judge.py` and supports a `--subset failing` flag for fast inner-loop iteration after a targeted fix (~3-5 minutes, ~$1-2 instead of the full run cost).

```bash
# Full run
ANTHROPIC_API_KEY=sk-ant-... \
    python tests/eval/eval_llm_judge.py --output eval_report.json

# Re-run only the failing scenarios from that report
ANTHROPIC_API_KEY=sk-ant-... \
    python tests/eval/eval_llm_judge.py \
    --subset failing --subset-from eval_report.json
```

Full operator's manual including all CLI flags, workflow patterns, the 11-dimension rubric, output JSON schema, cost and time breakdowns, and how to add new scenarios: **[`EVALUATION_TESTING.md`](EVALUATION_TESTING.md)**.

## Known Limitations

These are documented behaviors, not bugs:

- **Bare numbers (regex only):** Replying with just "17" (no context like "I am" or "age") does not extract age with the regex extractor. LLM extraction handles this correctly when enabled.
- **Multi-intent:** "I need food and shelter" extracts all service types, searches the first, then offers remaining services sequentially via the queue. Known limitation: only one location is extracted per message — "food in Brooklyn and shelter in Manhattan" uses Brooklyn for both. User can correct via "change location" when the second service is offered. 30 eval scenarios cover this flow.
- **Name detection:** Heuristic-based (intro phrases like "my name is"). Won't catch names without an intro phrase. Acceptable tradeoff to avoid false positives on location names.
- **Borough typos (regex only):** Misspellings like "brookyln" are not corrected by regex. LLM extraction handles these.
- **Two boroughs in one message (regex only):** "I'm in Queens but looking for food in Brooklyn" extracts "Queens" (first preposition match), not Brooklyn. LLM extraction picks the intended location.
- **Manhattan / "New York" ambiguity:** Manhattan normalizes to DB city value "New York." PostGIS proximity search mitigates this for neighborhood-level queries.
- **Regex override vs LLM for contextual keywords:** The unified extractor prefers regex `service_type` when regex finds an explicit keyword, even when the LLM disagrees. This is correct for deterministic keywords ("dental" is literally in the text) but is a known edge case when a keyword appears as context, not the user's need (e.g., "I just got out of the hospital and need somewhere to stay" — "hospital" triggers medical via regex, but the user needs shelter). The narrative path's urgency reprioritization handles this for messages over 20 words; short messages still surface the regex pick. See `TestNarrativeRegexFallbackRealisticScenarios` for end-to-end coverage.
- **Audit log persistence:** Set `PILOT_DB_PATH` to enable SQLite persistence for pilot testing. When unset, data is in-memory only and lost on restart.
- **Frontend untested:** No frontend test infrastructure exists yet. The Next.js components in `frontend-next/` (chat UI, admin console, hooks, Zustand store) have no automated tests. Consider adding Playwright for E2E tests or Vitest for component tests when stabilizing for production.

### Expected Failures (xfail)

3 tests are marked `@pytest.mark.xfail` — they document known limitations, not regressions:

| Tests | File | Reason |
|---|---|---|
| `test_spoken_number_age_extraction` | `test_slot_extraction_regex.py` | Word-to-number conversion ("seventeen" → 17) not implemented in regex extractor |
| `test_auto_execute_urgent_query` (×2) | `test_results_enhancements.py` | Auto-execute for urgent queries not yet implemented — chatbot always confirms |

## Adding New Tests

For chatbot tests, prefer the `send()` helper which mocks all external dependencies:

```python
from conftest import send, send_multi

def test_your_new_test(fresh_session):
    result = send("I need food in Brooklyn", session_id=fresh_session)
    assert result["slots"]["service_type"] == "food"
    assert result["follow_up_needed"] is True

    # Simulate crisis if needed:
    result = send("I want to hurt myself", session_id=fresh_session,
                  mock_crisis_return=("suicide_self_harm", "Call 988."))
```

For tests that call `generate_reply` directly, always patch `detect_crisis`:

```python
@patch("app.services.chatbot.detect_crisis", return_value=None)
@patch("app.services.chatbot.query_services", return_value=MOCK_QUERY_RESULTS)
@patch("app.services.chatbot.claude_reply")
def test_your_direct_test(mock_claude, mock_query, mock_crisis):
    result = generate_reply("your message", session_id="test-id")
    assert result["services"] == []
```
