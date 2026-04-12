# Testing Guide

## Overview

The test suite covers ~1,900+ tests across 46 test files, plus an LLM-as-judge evaluation framework with 172 scenarios. Tests are organized into `tests/unit/` (31 files — no DB or LLM needed) and `tests/integration/` (15 files — use mocked DB/LLM via `send()`/`send_multi()` helpers), with a separate `tests/eval/` directory for the LLM judge. Tests validate every backend module: slot extraction (regex, semantic embedding, and LLM-based), gender/LGBTQ identity extraction, population context extraction (veteran, disabled, reentry, DV survivor, pregnant, senior — with false-positive guards, multi-population support, query boost verification, DV crisis injection), PII redaction (including gender identity terms), conversational routing, crisis detection, crisis step-down (including DV population injection), emotional handling (AVR pattern with 6 emotion-specific static responses), frustration routing (3-tier counter-based escalation), negative preference handling, conversational awareness guard, privacy routing exception, phrase list audit coverage (C-SSRS, Joiner IPT, DV control, shame/stigma, grief, NYC service terms), contraction normalization, intensifier stripping, post-normalization emotional phrase variants, location boundary enforcement, query template correctness (including dynamic ORDER BY generation with population boosts), confirmation flow (including population-aware prefixes), quick replies, audit logging, admin API routes, chat HTTP endpoint, Pydantic model validation, Claude client initialization, API configuration, session management, geolocation, rate limiting, request correlation IDs, privacy question handling, family composition, multi-service extraction, split classifier (action + tone), shelter taxonomy enrichment, word-boundary keyword collision prevention, nearby borough suggestions, bug fix regressions (7 targeted fixes with 30 tests), post-results question handling, crisis safety edge cases (research-sourced C-SSRS, HITS/SAFE, Polaris, SAMHSA), co-located multi-service queries, gap coverage (freshness, admin stats shape, skip_llm pipeline, prompt builders), quick reply button audit, SQLite pilot persistence (write-through, hydration, disabled mode), database schema/query integration, bot self-knowledge (live capability sourcing, topic matching), boundary drift detection (mock/Pydantic/SQL/format sync), context-aware routing (state transitions, frustration counting, implicit service changes), integration scenarios (narrative flows, cross-feature interactions, eval approximations), narrative extraction (urgency-aware slot extraction for long messages), ambiguity handling (confidence scoring, disambiguation prompts, correction recovery, "Not what I meant" button), post-results boundary routing (new-request escape hatch, location-based result clearing, name-match fallthrough), and semantic routing (route definitions, initialization, service classification, false positive rejection, population detection, threshold behavior, integration fallthrough, graceful degradation, observability, route alignment). Unit tests run without external services (database and Claude API are mocked). DB integration tests require DATABASE_URL and are automatically skipped without it.

## Running Tests

From the repo root with the virtual environment activated:

```
source backend/venv/bin/activate
```

**Run all tests with pytest (recommended):**

```
pip install pytest httpx
pytest tests/ -v
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
pytest tests/unit/test_slot_extractor.py -v
```

**Run a single test:**

```
pytest tests/integration/test_chatbot.py::test_confirm_deny_breaks_loop -v
```

**Run LLM-as-judge evaluation (requires API key):**

```
ANTHROPIC_API_KEY=sk-ant-... python tests/eval/eval_llm_judge.py
```

**Run LLM integration tests (requires API key):**

```
ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_llm_slot_extractor.py -v
```

Without `ANTHROPIC_API_KEY`, the 5 live LLM tests are automatically skipped.

## Test Coverage Map

All 21 backend modules and all public functions are covered. Tests are in `tests/unit/` (no external deps) and `tests/integration/` (mocked DB/LLM):

| Module | Test file(s) | Tests | Status |
|---|---|---|---|
| `chatbot.py` | `integration/test_chatbot.py`, `integration/test_bug_fixes.py`, `integration/test_context_routing.py`, `integration/test_integration_scenarios.py`, `integration/test_ambiguity_handling.py`, `unit/test_service_card_display.py`, `unit/test_results_enhancements.py` | 310+ | Full |
| `classifier.py` | `unit/test_contraction_normalization.py`, `unit/test_phrase_audit.py`, `integration/test_chatbot.py` | 60+ | Full |
| `phrase_lists.py` | `unit/test_phrase_audit.py` | 41 | Full |
| `responses.py` | `integration/test_chatbot.py`, `integration/test_gap_coverage.py` | (inline) | Full |
| `confirmation.py` | `unit/test_edge_cases.py`, `unit/test_gender_extraction.py`, `integration/test_chatbot.py` | (inline) | Full |
| `slot_extractor.py` | `unit/test_slot_extractor.py`, `unit/test_gender_extraction.py`, `unit/test_edge_cases.py`, `unit/test_location_boundaries.py`, `unit/test_populations.py`, `unit/test_org_name_search.py`, `unit/test_walk_in_and_card_extras.py` | 330+ | Full |
| `rag/__init__.py` | `unit/test_query_templates.py`, `unit/test_populations.py`, `unit/test_org_name_search.py`, `unit/test_walk_in_and_card_extras.py`, `integration/test_geolocation.py`, `integration/test_db_integration.py` | 125+ | Full |
| `query_templates.py` | `unit/test_query_templates.py`, `unit/test_location_boundaries.py`, `unit/test_service_card_display.py`, `unit/test_walk_in_and_card_extras.py` | 120+ | Full |
| `query_executor.py` | `unit/test_location_boundaries.py`, `unit/test_edge_cases.py` | 65 | Full |
| `audit_log.py` | `unit/test_audit_log.py`, `unit/test_location_feedback.py`, `integration/test_bug_fixes.py`, `integration/test_admin.py`, `integration/test_ambiguity_handling.py` | 77+ | Full |
| `crisis_detector.py` | `unit/test_crisis_detector.py`, `integration/test_bug_fixes.py`, `integration/test_crisis_safety_edges.py` | 60+ | Full |
| `llm_slot_extractor.py` | `unit/test_llm_slot_extractor.py`, `unit/test_narrative_extraction.py`, `unit/test_semantic_router.py` | 44+ | Full |
| `semantic_router.py` | `unit/test_semantic_router.py` | 54 | Full |
| `semantic_routes.py` | `unit/test_semantic_router.py` | 54 | Full |
| `llm_classifier.py` | `unit/test_llm_classifier.py` | 30 | Full |
| `bot_knowledge.py` | `unit/test_bot_knowledge.py` | 37 | Full |
| `post_results.py` | `unit/test_post_results.py`, `unit/test_post_results_boundary.py`, `unit/test_results_enhancements.py` | 125 | Full |
| `pii_redactor.py` | `unit/test_pii_redactor.py`, `unit/test_gender_extraction.py`, `unit/test_edge_cases.py` | 38+ | Full |
| `session_store.py` | `unit/test_session_store.py`, `integration/test_chatbot.py`, `integration/test_chat_route.py` | 7+ | Full |
| `session_token.py` | `unit/test_session_token.py`, `integration/test_chat_route.py` | 17 | Full |
| `rate_limiter.py` | `unit/test_rate_limiter.py`, `integration/test_rate_limit_integration.py` | 24 | Full |
| `chat_models.py` | `integration/test_chat_route.py`, `integration/test_boundary_drift.py` | 27+ | Full |
| `admin.py` (routes) | `integration/test_admin.py` | 28 | Full |
| `chat.py` (route) | `integration/test_chat_route.py` | 48 | Full |
| `claude_client.py` | `unit/test_claude_client.py` | 19 | Full |
| `main.py` | `unit/test_main.py` | 14 | Full |

**Not covered:** Frontend TypeScript/React components (`frontend-next/`). There is no frontend test infrastructure in the project yet. See "Known Limitations" section below.

## Test Suites

### `integration/test_chatbot.py` — 193 tests

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

### `test_slot_extractor.py` — 111 tests

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
| Merge logic | 11 | New over empty, preserving existing, near-me sentinel behavior, stale service_detail cleared on service change, detail persists when service unchanged, new detail replaces old, additional_services skipped during merge |
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

### `test_query_templates.py` — 105 tests

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
| Shelter taxonomy enrichment | 8 | Youth (age<18), senior (age≥62), families (with_children), single adult (alone), LGBTQ Young Adult (always), base taxonomies preserved, food queries not enriched, TEMPLATES default_params not mutated |

### `test_crisis_detector.py` — 36 tests

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

### `test_llm_slot_extractor.py` — 27 unit + 5 live tests

Validates the LLM-based slot extractor including conversation history passing. Live tests require `ANTHROPIC_API_KEY` and are automatically skipped without it.

| Category | Tests | What's covered |
|---|---|---|
| LLM extraction (mocked) | 6 | Service+location, age+gender+urgency, third-person, contradicting locations, empty messages, API failure |
| Smart extractor (tiered) | 5 | Regex sufficient → LLM skipped, regex partial → LLM called, ambiguous → LLM, merge logic, LLM failure falls back |
| Complexity routing | 3 | Short messages → simple, long messages → complex, unknown locations → complex |
| Conversation history | 5 | History passed to LLM, alternating messages enforced, None and empty handled, truncated to six messages, smart extractor passes history |
| Integration (live) | 5 | End-to-end extraction, skipped without API key |

### `test_audit_log.py` — 70 tests

Validates all 13 public functions in the audit log module.

| Category | Tests | What's covered |
|---|---|---|
| Log conversation turn | 5 | Correct fields, internal slot stripping (`_pending_confirmation`, `transcript`, None values), quick reply label extraction, None slots, conversation registration |
| Request correlation IDs | 4 | request_id stored in turn events, defaults to None, stored in query execution, stored in crisis events |

### `test_semantic_router.py` — 54 tests

Validates the Tier 2 semantic routing module: route definitions, model initialization, classification, threshold behavior, population detection, integration with `extract_slots_smart()`, graceful degradation, and observability. Uses mock embedding models with controlled vectors for deterministic testing — no real model download required.

| Category | Tests | What's covered |
|---|---|---|
| Route definitions | 9 | All service/population routes exist, minimum utterances per route (10 service, 6 population), no duplicates within or across routes, no empty utterances, phrases not keywords (≥3 words), eval failure scenarios covered (insulin→medical, felon→employment, foster care→shelter), route keys align with `SERVICE_KEYWORDS` |
| Initialization | 5 | Mock model init, not-available before init, classify returns None before init, reset clears state, init with populations |
| Service classification | 4 | High similarity matches, low similarity rejected, runner-up tracking, threshold override |
| False positive rejection | 2 | Casual greeting no match (orthogonal embeddings), per-route threshold for "other" |
| Population detection | 2 | Population detected alongside service, lower threshold than service routes |
| Threshold behavior | 2 | Exact threshold passes, below threshold rejected (controlled cosine similarity) |
| Per-route thresholds | 2 | Score 0.76 accepted for food (threshold 0.75) but rejected for other (threshold 0.78), housing_assistance elevated threshold |
| Multiple populations | 1 | Highest-scoring population selected when multiple exceed threshold |
| Edge cases | 4 | Single route → runner_up is None, empty string input, whitespace-only input, model-None-while-initialized guard |
| Initialize robustness | 2 | Idempotent second call, exception during init cleans state |
| Integration (extract_slots_smart) | 9 | Semantic fills missing service_type, population merged with regex, skips when regex has service, short message skips LLM, long message still calls LLM, semantic None falls through to LLM, unavailable falls through to LLM, narrative bypasses semantic, semantic service_type preserved through LLM merge |
| Graceful degradation | 3 | Returns None when not initialized, handles missing sentence-transformers, handles encode exception |
| Observability | 4 | Diagnostics returns scores for all routes, sorted by max_similarity, includes population routes, error when not available |
| Dataclass | 2 | Basic creation with defaults, full creation with population and runner-up |
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

### `test_admin.py` — 28 tests

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

### `test_chat_route.py` — 48 tests

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

### `test_geolocation.py` — 11 tests

Validates browser geolocation support: coordinate acceptance, session storage, "near me" + coords flow, and proximity query integration.

| Category | Tests | What's covered |
|---|---|---|
| Pydantic model | 2 | ChatRequest accepts lat/lng, coordinates optional |
| Session storage | 2 | Coords stored when provided, absent when not |
| Near-me + coords | 3 | Triggers confirmation (not borough ask), shows "near your location", sentinel not exposed |
| Full flow | 1 | food near me → confirmation → confirm → results with coords passed to query_services |
| RAG integration | 2 | Direct coords build proximity params, coords override location name |
| Cross-turn persistence | 1 | Coords persist in session across messages |

### `test_rate_limiter.py` — 14 tests

Validates the sliding-window rate limiter logic.

| Category | Tests | What's covered |
|---|---|---|
| Per-session limits | 4 | Messages per minute/hour/day, retry_after calculation |
| Per-IP limits | 3 | IP-level rate limiting, separate from session limits |
| Feedback limits | 2 | Feedback endpoint rate limiting |
| Bucket management | 3 | Sliding window cleanup, thread safety, clear() |
| Memory management | 2 | Forced eviction when bucket cap exceeded, no forced eviction under cap |

### `test_rate_limit_integration.py` — 10 tests

HTTP-level tests for rate limiting middleware via FastAPI TestClient.

| Category | Tests | What's covered |
|---|---|---|
| 429 responses | 4 | Rate limit exceeded returns 429, crisis resources included |
| Session tracking | 3 | Session-based vs IP-based limiting |
| Middleware integration | 3 | Middleware attached to routes, header extraction |

### `test_db_integration.py` — 27 tests

Database integration tests that run against the real Streetlives PostgreSQL database. Automatically skipped when DATABASE_URL is not set.

| Category | Tests | What's covered |
|---|---|---|
| Schema validation | 18 | All 11 tables exist, required columns present, PostGIS geometry type, JSONB eligibility, timestamp freshness column, taxonomy names match templates, eligibility parameters exist |
| Query execution | 21 | All 9 templates strict/relaxed, proximity search, distance ordering, age/gender eligibility, open-now sort, freshness sort, city list ANY(), weekday/open-now filters, all filters combined |
| Result formatting | 2 | Real rows format to valid service cards, proximity returns multiple results |
| End-to-end | 5 | Full query_services() pipeline: borough, neighborhood, coords, relaxed fallback, card field completeness |

### `test_main.py` — 14 tests

HTTP-level tests for the FastAPI app configuration (headless API mode).

| Category | Tests | What's covered |
|---|---|---|
| Health | 1 | `GET /api/health` |
| Root | 1 | `GET /` returns JSON message (no static file serving) |
| API routing | 3 | `/api/health`, `POST /chat/`, `/admin/api/stats` all routed correctly |
| CSRF protection | 6 | Valid origin allowed, evil origin → 403, non-browser (no headers) allowed, Sec-Fetch-Site without origin → 403, valid Referer allowed, evil Referer → 403 |
| CORS | 3 | Headers present for allowed origin, no headers for unknown origin, preflight OPTIONS |

### `test_phrase_audit.py` — 41 tests

Validates phrase additions from the P0–P3 audit (see PHRASE_LIST_AUDIT.md). Parametrized tests cover C-SSRS suicide ideation phrases, Joiner IPT burdensomeness markers, DV coercive control, youth safety/runaway, shame/stigma emotional phrases, grief with service routing, expanded frustration phrases, and confused/overwhelmed phrases.

### `test_contraction_normalization.py` — 19 tests

Validates `_normalize_contractions()`, `_strip_intensifiers()`, and their integration with `_classify_tone()`. Covers individual contraction expansions, full sentences, multiple contractions, non-contraction preservation, frustration/confused/emotional detection via normalization, help-negator handling ("doesn't help" → frustration not help), intensifier stripping for emotion/frustration/confused classification, and confirms normalization does not affect crisis detection (which uses explicit enumeration).

### `test_structural_fixes.py` — 28 tests

Regression tests for the 6 structural fixes targeting Run 16's 25 failing eval scenarios. Covers mental health keyword removal ("struggling" → not a service), crisis emotional guard ("feeling scared" → emotional not crisis), crisis step-down (service intent preserved alongside crisis), context-aware yes/no, frustration loop detection, and emotional phrase detection.

### `test_llm_multi_service.py` — 11 tests

Validates PR 4's LLM multi-service extraction. Covers `additional_service_types` in the LLM tool response, single-service returns empty additional list, multiple additional services, null handling, failure fallback, `extract_slots_smart` merging LLM and regex additional services, deduplication of primary service, and key cleanup.

### `test_bug_fixes.py` — 30 tests

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

### `test_crisis_safety_edges.py` — 25 tests (some with parametrized xfails)

Research-sourced crisis detection edge cases from C-SSRS (5 severity levels), HITS/SAFE DV screening, Polaris trafficking indicators, SAMHSA TIP 55 homeless population patterns, and Covenant House/Ali Forney youth research. Tests are organized into regex coverage (what the instant check catches), LLM-dependent gaps (xfailed with research citations), post-results safety (eval P10), and false positive guards. The 34 xfails serve as a roadmap: promoting a phrase from xfail to the regex list immediately upgrades it to instant detection.

### `test_coverage_gaps.py` — 36 tests

Coverage gap tests for 8 high/medium priority areas: zip code full flow (4), crisis step-down + multi-intent (2), LLM contradictory category (2), near-me sentinel safety (3), session_exists (3), get_client_ip (5), _extract_session_id (4), _normalize_url (9), feedback→stats (4).

### `test_gap_coverage.py` — 43 tests

Comprehensive gap coverage for 9 areas identified during audit: `_compute_freshness` timezone/boundary handling (8), admin `/api/stats` response shape for routing/tone/multi_intent (6), post-results through `generate_reply` end-to-end (4), `skip_llm` through chatbot pipeline (2), `also_available` in post-results detail view (4), `last_validated_at` timezone edge cases (4), multi-intent queue decline with 2-item queue (2), prompt builder function shapes and guardrails (8), `format_service_card` deduplication and filtering (5).

### `test_persistence.py` — 27 tests

SQLite pilot persistence layer. Tests direct CRUD operations on all 3 tables (events, sessions, eval_data) including ordering, limits, upserts, and clears (12 tests). Disabled mode (PILOT_DB_PATH unset) verifies all operations are safe no-ops (6 tests). Audit log hydration round-trip: write events → clear in-memory → hydrate from SQLite → verify stats (4 tests). Session store hydration: write → clear → hydrate → verify slots (4 tests). Full restart simulation: user interaction → destroy in-memory state → hydrate → verify everything is restored (1 test).

### `test_bot_knowledge.py` — 37 tests

Validates the bot self-knowledge module: live capability sourcing from actual code, topic matching for 12+ question types, LLM context generation, static handler integration, bot question phrase classification, untested topic coverage, topic collision prevention, false positive guards, and full chatbot routing for privacy/location/services questions.

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

### `test_boundary_drift.py` — 20 tests

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

### `test_context_routing.py` — 56 tests

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

### `test_integration_scenarios.py` — 29 tests

Integration tests that send messages through the full `generate_reply` pipeline. Reproduces failing eval scenarios and tests cross-feature interactions: narrative + emotional, PII in narratives, shame prefix + narrative extraction, session isolation.

| Category | Tests | What's covered |
|---|---|---|
| Narrative integration | 7 | Hospital/housing, re-entry, eviction/family, runaway youth, narrative shows confirmation, queues additional services, short message not narrative path |
| Cross-feature interactions | 4 | Emotional narrative with service, shame narrative normalizing prefix, intensifiers in narrative, frustration then narrative |
| PII in narratives | 4 | Phone, name, SSN, multiple PII in narrative messages |
| Session isolation | 2 | Two sessions independent, emotional state doesn't leak |
| Eval scenario approximations | 12 | Emotional scared/feeling-down/rough-day, change mind, yes after escalation, frustration loop, long story, tell my story, re-entry, fake service, nonsense service, shame shelter stigma |

### `test_narrative_extraction.py` — 17 tests

Validates narrative extraction — urgency-aware slot extraction for long messages (20+ words). Tests both the LLM path (mocked) and the regex fallback path.

| Category | Tests | What's covered |
|---|---|---|
| Narrative detection | 3 | Short message not narrative, long message is narrative, threshold boundary |
| Urgency hierarchy | 3 | Shelter highest, medical above food, food above employment |
| Regex fallback | 7 | Hospital/housing prioritizes shelter, runaway youth, eviction, re-entry all prioritize shelter, urgency inferred from context, single service no change, location preserved |
| Smart extractor narrative path | 4 | Narrative uses fallback without LLM, doesn't regex-override, short message uses standard path, additional services preserved |

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

### `test_ambiguity_handling.py` — 26 tests

Validates the four industry-recommended ambiguity handling patterns: confidence scoring, disambiguation prompts, correction recovery, and ambiguity logging.

| Category | Tests | What's covered |
|---|---|---|
| Confidence scoring | 6 | Regex match=high, reset=high, service keyword=high, correction=low, disambiguation=disambiguated, confidence stored in audit events |
| Disambiguation prompts | 4 | Unmatched name triggers disambiguation, offers search option, preserves session, matched name skips disambiguation |
| Correction handler | 11 | 5 phrases classified, clears pending/last_action/last_results, preserves service slots, shows buttons + navigator, context-aware message, no false positives on service requests, crisis trumps correction |
| "Not what I meant" button | 1 | Correction button on unrecognized service responses |
| Ambiguity logging | 4 | Correction category logged, disambiguation category logged, confidence field in events, high confidence stored |

### `test_populations.py` — 88 tests

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
| LLM schema | 3 | Tool schema includes populations, empty_slots returns [], classifier prompt mentions populations |
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

### `test_results_enhancements.py` — 25 tests

Validates sort options, day-specific hours, and urgent auto-execute.

| Category | Tests | What's covered |
|---|---|---|
| Auto-execute | 4 | High urgency skips confirmation, high urgency + location executes, medium still confirms, no location still asks |
| Sort patterns | 5 | Sort by verified, by services, updates session, QR offered, unrecognized falls through |
| Day detection | 7 | Saturday/Sunday/Monday/abbreviated/weekend detected, day without hours context ignored, generic hours still works |
| ISODOW mapping | 3 | Monday=1, Saturday=6, Sunday=7, abbreviations match |
| Hours for day handler | 3 | Hours per service with mock DB, no-data message, weekend fetches both days |
| Schedule DB function | 2 | Empty input returns {}, SQL uses correct params |

## LLM-as-Judge Evaluation (`eval_llm_judge.py`)

Beyond unit tests, the system includes an end-to-end evaluation framework that uses Claude as an impartial judge to score full conversations. This validates the chatbot holistically — not just whether individual functions return the right values, but whether the overall experience is safe, efficient, and appropriate for the population served.

### How it works

The evaluation runs a three-stage pipeline:

**Stage 1 — Conversation Simulator.** Each scenario defines a persona, an opening message, and expected behavior. The simulator drives multi-turn conversations through `generate_reply()` with mocked DB results. Pre-defined user messages are sent first. If the bot asks follow-up questions, Claude (Sonnet) generates natural user responses consistent with the scenario persona.

**Stage 2 — LLM Judge.** The completed transcript and scenario metadata are sent to a separate Claude call with a detailed scoring rubric. The judge scores 8 quality dimensions on a 1–5 scale with written justifications.

**Stage 3 — Report Generator.** Individual evaluations are aggregated into a structured report with overall average, per-dimension breakdowns, per-category averages, critical failure list, and per-scenario detail.

### Scoring dimensions

| Dimension | What it measures |
|---|---|
| Slot Extraction Accuracy | Did the system correctly identify service type, location, age, and urgency? |
| Dialog Efficiency | How many turns to reach a result? Were follow-ups necessary and well-targeted? |
| Response Tone | Warm, respectful, non-judgmental, trauma-informed? |
| Safety & Crisis Handling | Did crisis language trigger appropriate resources? |
| Confirmation UX | Was the confirmation step clear? Could the user change details? |
| Privacy Protection | Was PII avoided in responses? No echoing of names, phones, or addresses? |
| Hallucination Resistance | Did the system avoid fabricating service data? |
| Error Recovery | When things went wrong, did the system recover gracefully? |

### Running the evaluation

```bash
# Run all 114 scenarios
ANTHROPIC_API_KEY=sk-ant-... python tests/eval/eval_llm_judge.py

# Run only crisis scenarios
ANTHROPIC_API_KEY=sk-ant-... python tests/eval/eval_llm_judge.py --category crisis

# Run a single scenario and save JSON report
ANTHROPIC_API_KEY=sk-ant-... python tests/eval/eval_llm_judge.py --scenario-id shelter_queens_17 --output eval_report.json
```

### Scenario coverage

172 scenarios across 20 categories: happy_path, multi_turn, crisis, confirmation, privacy, edge_case, natural_language, adversarial, accessibility, taxonomy_regression, borough_filter, no_result, staten_island, neighborhood_routing, schedule, referral, data_quality, emotional, bot_question, guard (emotional+service overlap), and multi_intent.

Notable additions: 2 frustration escalation scenarios (repeated frustration loop with 3-tier counter, frustration-to-resolution arc), and 10 scenarios informed by the WA Homelessness Portal covering rough sleepers, unsafe housing, family with children, substance use + shelter, dual needs, negative preferences, non-English speakers, youth runaways, privacy around data sharing, and multi-need storytelling.

**Multi-intent queue flow (30 scenarios)** — core queue (food+shelter sequential,
shower+food drop-in pattern, clothing+food), three-service combos (DYCD drop-in
trio, asylum seeker trio), queue decline (2 phrasings), location change mid-queue
(typed and button), cross-service slot conflicts (cross-borough, cross-neighborhood),
emotional+multi-service empathetic framing (4 tone variants + second-service warmth),
shame/embarrassment tone (3 — food bank stigma, shelter stigma, single-service
normalizing), YourPeer personas (LGBTQ youth/Ali Forney, DYCD RHY runaway,
foster care aging-out, asylum seeker, re-entry from Rikers, family with children
via PATH), queue edge cases (ignore queue with new request, start over clears
queue), and complex natural language (substance use narrative, outreach worker
referral).

## Known Limitations

These are documented behaviors, not bugs:

- **Bare numbers (regex only):** Replying with just "17" (no context like "I am" or "age") does not extract age with the regex extractor. LLM extraction handles this correctly when enabled.
- **Multi-intent:** "I need food and shelter" extracts all service types, searches the first, then offers remaining services sequentially via the queue. Known limitation: only one location is extracted per message — "food in Brooklyn and shelter in Manhattan" uses Brooklyn for both. User can correct via "change location" when the second service is offered. 30 eval scenarios cover this flow.
- **Name detection:** Heuristic-based (intro phrases like "my name is"). Won't catch names without an intro phrase. Acceptable tradeoff to avoid false positives on location names.
- **Borough typos (regex only):** Misspellings like "brookyln" are not corrected by regex. LLM extraction handles these.
- **Two boroughs in one message (regex only):** "I'm in Queens but looking for food in Brooklyn" extracts "Queens" (first preposition match), not Brooklyn. LLM extraction picks the intended location.
- **Manhattan / "New York" ambiguity:** Manhattan normalizes to DB city value "New York." PostGIS proximity search mitigates this for neighborhood-level queries.
- **Regex override vs LLM for contextual keywords:** The smart extractor prefers regex `service_type` when regex finds an explicit keyword, even when the LLM disagrees. This is correct for deterministic keywords ("dental" is literally in the text) but incorrect when a keyword appears as context, not the user's need (e.g., "I just got out of the hospital and need somewhere to stay" — "hospital" triggers medical via regex, but the user needs shelter). Two tests are marked `xfail` for this.
- **Audit log persistence:** Set `PILOT_DB_PATH` to enable SQLite persistence for pilot testing. When unset, data is in-memory only and lost on restart.
- **Frontend untested:** No frontend test infrastructure exists yet. The Next.js components in `frontend-next/` (chat UI, admin console, hooks, Zustand store) have no automated tests. Consider adding Playwright for E2E tests or Vitest for component tests when stabilizing for production.

### Expected Failures (xfail)

38 tests are marked `@pytest.mark.xfail` — they document known limitations, not regressions:

| Tests | File | Reason |
|---|---|---|
| `test_spoken_number_age_extraction` | `test_slot_extractor.py` | Word-to-number conversion ("seventeen" → 17) not implemented in regex extractor |
| `test_family_status_with_children_prepositional` | `test_slot_extractor.py` | Prepositional family phrases ("for me and my kids", "I have a baby") not matched by current phrase list |
| `test_smart_uses_llm_for_long_messages` | `test_llm_slot_extractor.py` | Regex override replaces LLM's correct "shelter" with "medical" because "hospital" matches a medical keyword |
| `test_smart_regex_does_not_override_when_no_regex_match` | `test_llm_slot_extractor.py` | Same regex override issue — "hospital" is contextual, not the user's need |
| 34 parametrized xfails | `test_crisis_safety_edges.py` | LLM-dependent crisis phrases (C-SSRS indirect ideation, euphemistic language, method-specific plans, perceived burdensomeness) that regex can't catch without context. Each xfail has a research citation. Promoting a phrase to the regex list upgrades it to instant detection |

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
