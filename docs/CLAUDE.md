# YourPeer Chatbot — Claude Code Context

## What This Project Is

A conversational chatbot for [YourPeer](https://yourpeer.nyc), built by Streetlives, that helps
unhoused New Yorkers find services (shelter, food, clothing, showers, benefits).

## Stack

| Layer | Tech |
|-------|------|
| LLM | Claude Haiku + Sonnet |
| Semantic Routing | sentence-transformers / all-MiniLM-L6-v2 (local, CPU) |
| Backend | FastAPI (Python) |
| Frontend | Next.js 15 / React 19 / Zustand |
| Database | Streetlives PostgreSQL (read-only) |

**Architecture:** `User → Chat UI → Backend → LLM + Query Templates → Streetlives API`

---

## Current State

A fully functional multi-turn chatbot that guides users through service discovery via
slot-filling conversation. The system uses a three-tier classification cascade (regex → semantic
embedding → LLM fallback) to route messages, extracts service needs (type, location, age,
urgency, gender, family status, population context), confirms with the user, then queries a
read-only Streetlives PostgreSQL database using parameterized SQL templates. Population context (veteran, disabled,
reentry, DV survivor, pregnant, senior) cross-cuts all searches — boosting relevant services
via taxonomy or description-based ORDER BY ranking. Results are returned as structured service
cards with contact info, hours, accessibility info, and directions links. Crisis detection, PII
redaction, and an admin console with LLM-as-judge evaluation are all implemented. The system
works in a degraded but functional regex-only mode when no API key is configured.

## Architecture (Actual)

```
User Message
  → POST /chat/ (FastAPI)
  → chatbot.generate_reply()
    ├─ PII redaction (every message)
    ├─ Message classification (3-tier: regex → semantic embedding → LLM fallback)
    ├─ Route by category:
    │   ├─ crisis → crisis_detector (regex + Sonnet LLM) → hotline resources
    │   ├─ correction → clears pending state, shows alternatives
    │   ├─ negative_preference → acknowledges rejection, offers alternatives
    │   ├─ greeting/thanks/help/reset/escalation → canned response
    │   ├─ frustration → 3-tier escalation (counter-based, varied responses)
    │   ├─ emotional → static emotion-specific response (no LLM, 6 emotion keys)
    │   ├─ post-results → deterministic answers from stored cards (no LLM)
    │   ├─ disambiguation → clarifying options when intent is ambiguous
    │   ├─ service request → slot extraction (3-tier: regex → semantic router → Haiku LLM)
    │   │   ├─ slots incomplete → follow-up question
    │   │   ├─ slots complete → confirmation prompt with quick replies
    │   │   └─ confirmed → query_services() → SQL template → DB → service cards
    │   └─ general → LLM conversational fallback (Haiku)
    ├─ Session state saved (in-memory, 30-min TTL)
    ├─ Audit log entry
    └─ Return ChatResponse (response text, slots, service cards, quick replies)
```

All service data comes from deterministic DB queries — the LLM never generates service
information, preventing hallucination.

## Key Files & Structure

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app entry point, CORS, router registration, enhanced `/api/health` endpoint (checks DB, LLM, semantic router, returns structured JSON with per-component status) |
| `backend/app/routes/chat.py` | `POST /chat/`, `/chat/feedback`, and `/chat/location-feedback` endpoints |
| `backend/app/routes/admin.py` | Admin API: conversations, events, stats, eval runner |
| `backend/app/services/chatbot.py` | Conversation router: `generate_reply()`, query execution, session orchestration |
| `backend/app/services/classifier.py` | Message classification: `_classify_action()`, `_classify_tone()`, contraction normalization, intensifier stripping |
| `backend/app/services/phrase_lists.py` | All keyword/phrase lists, quick-reply definitions, service labels, borough suggestion data |
| `backend/app/services/responses.py` | Response strings, emotion-specific responses, LLM prompt builders, bot-question answers |
| `backend/app/services/confirmation.py` | Confirmation messages, quick-reply builders, no-results messages, borough suggestions |
| `backend/app/services/bot_knowledge.py` | Bot self-knowledge: live capability sourcing, topic matching, LLM context generation |
| `backend/app/services/crisis_detector.py` | Two-stage crisis detection (regex + Sonnet LLM), category-specific hotlines |
| `backend/app/services/slot_extractor.py` | Regex-based slot extraction with keyword matching, gender/LGBTQ identity extraction, population context extraction (veteran, disabled, reentry, DV survivor, pregnant, senior), organization name extraction, walk-in/no-requirements detection |
| `backend/app/services/semantic_router.py` | Tier 2 semantic routing: `all-MiniLM-L6-v2` sentence embedding model, cosine similarity classification against pre-embedded route utterances, per-route confidence thresholds, population detection, `get_status()` for health checks |
| `backend/app/services/semantic_routes.py` | Route definitions: 10–20 example utterances per service category (10 routes) and 6–12 per population category (6 routes). No code changes needed to add utterances — just edit and restart |
| `backend/app/services/llm_slot_extractor.py` | LLM slot extraction via Claude Haiku tool calling, 3-tier cascade integration (regex → semantic → LLM) |
| `backend/app/services/llm_classifier.py` | Unified LLM classification gate — single Haiku call returning service_type, location, tone, action when regex fails |
| `backend/app/services/session_store.py` | In-memory session state with 30-min TTL (max 500 sessions) |
| `backend/app/services/audit_log.py` | Anonymized event logging (capped ring buffer), P0-P3 metrics aggregation (confidence, recovery rates, session metrics, no-result by service, time-of-day, geographic demand, frustration tiers, session duration, repetition rate, LLM call metrics) |
| `backend/app/llm/claude_client.py` | Anthropic client (lazy init), model constants, shared helpers |
| `backend/app/rag/__init__.py` | `query_services()` entry point |
| `backend/app/rag/query_executor.py` | DB execution, location normalization, borough/neighborhood PostGIS logic |
| `backend/app/rag/query_templates.py` | 11 parameterized SQL templates (food, shelter, clothing, etc. + org_name), dynamic ORDER BY with population boosts, eligibility/review highlight/required docs/languages subqueries |
| `backend/app/privacy/pii_redactor.py` | PII detection and redaction (phone, SSN, email, DOB, address, names, gender identity) |
| `backend/app/models/chat_models.py` | Pydantic models: ChatRequest, ChatResponse, ServiceCard, QuickReply |
| `frontend-next/src/components/chat/` | Chat UI components (ChatContainer with three-state health indicator, ServiceCard, QuickReplies, VoiceInputButton) |
| `frontend-next/src/hooks/use-backend-health.ts` | Backend health polling hook — polls `/api/health` every 30s, derives connected/degraded/unreachable status |
| `frontend-next/src/components/admin/system-health.tsx` | Admin system health card — real-time component status (Backend, DB, LLM, Semantic Router) |
| `frontend-next/src/lib/chat/store.ts` | Zustand chat store with `localStorage` persistence |
| `frontend-next/src/lib/admin/store.ts` | Zustand admin store with staleness-based caching |
| `frontend-next/src/app/admin/` | Staff console pages (overview, conversations, metrics, queries, evals, models) |
| `frontend-next/next.config.js` | CSP + HSTS headers, security config |
| `tests/conftest.py` | Pytest fixtures, mock data, test helpers |
| `tests/eval_llm_judge.py` | LLM-as-judge evaluation (167 scenarios, 11 dimensions, Opus judge, weighted scoring) |

## What's Working

- **10 service categories**: food, shelter, clothing, personal care, medical, mental health, legal, employment, housing assistance, other
- **3-tier classification cascade**: regex keyword matching (Tier 1, <1ms) → semantic embedding with `all-MiniLM-L6-v2` (Tier 2, ~2-5ms, handles novel phrasings regex misses) → LLM fallback with Claude Haiku (Tier 3, 1-3s, handles complex multi-intent narratives). Semantic routing eliminates the "missing keyword" class of failures — "I ran out of insulin" routes to medical even though "insulin" shares no keywords with the medical phrase list
- **Multi-turn slot-filling**: extracts service_type, location, age, urgency, gender across conversation turns
- **Two-stage classification**: regex for fast deterministic routing, LLM for ambiguous messages
- **Complexity-based LLM routing**: regex handles simple inputs, Claude Haiku handles complex/implicit/slang
- **Confirmation step**: user confirms before any DB query executes
- **Quick-reply buttons**: welcome categories, borough selection, geolocation ("Use my location"), confirmation actions
- **Browser geolocation**: opt-in "Use my location" via Geolocation API; falls back to borough buttons on denial
- **Borough + neighborhood search**: direct borough column filter or PostGIS proximity (59 NYC neighborhoods)
- **Relaxed fallback**: auto-broadens filters when 0 results, suggests boroughs with more data
- **Crisis detection**: regex + Sonnet LLM, covers suicide/self-harm, DV, trafficking, medical emergency, violence, youth runaway; fail-open policy returns safety response if LLM unavailable
- **PII redaction**: phone, SSN, email, DOB, address, name, gender identity detection/redaction on every message
- **Service cards**: structured results with name, org, address, phone (with extensions), hours, fees, open/closed status, referral badges, eligibility summary, review highlights, required documents, languages spoken, accessibility info, stale data warnings, action links, and per-location feedback
- **Organization name search**: users can search by org name ("tell me about Covenant House", "Safe Horizon in Harlem"). 35+ multi-word names matched via substring, 5 abbreviations via word-boundary regex. Org name alone is sufficient (no location required). Returns all services at the matching organization
- **Walk-in / no-requirements filter**: 20 phrases ("walk-in only", "no referral needed", "without appointment") exclude services requiring membership or referral. Universal optional filter across all templates
- **Gender & LGBTQ identity filtering**: extracted only when explicitly stated (never inferred). Binary gender (male/female) passes to SQL filter. Transgender/nonbinary/LGBTQ bypass the eligibility filter and trigger taxonomy boosts for affirming services. Confirmation shows "LGBTQ-friendly" label. Gender terms redacted from stored transcripts
- **Population context extraction & query boosts**: `_populations` slot detects veteran, disabled, reentry, DV survivor, pregnant, senior as cross-cutting identity attributes. Veterans get taxonomy-based boost (services tagged "Veterans" rank higher). All other populations get description-based ORDER BY boost (dynamic `pop_boost_pattern` applied across all 10 templates). Senior auto-inferred from age ≥ 62. Multiple populations supported. Confirmation shows context-aware prefixes ("veteran-friendly food", "accessible shelter"). Stored with `_` prefix for PII exclusion
- **DV crisis → population injection**: when crisis detector fires on `domestic_violence` category, `dv_survivor` is injected into session `_populations` regardless of whether the population extractor caught it. This bridges the 51-phrase gap between crisis detection (54 DV phrases) and population extraction (3 matching phrases). Fires in both step-down (service intent) and crisis-only (no service intent) branches
- **Accessibility on service cards**: `accessibility_for_disabilities` table is queried and surfaced on cards as informational text. Not used as a filter — negative values ("Not wheelchair accessible") are displayed so users can make informed decisions
- **Conversational routing**: greeting, thanks, help, reset, escalation, frustration, emotional, negative preference, bot identity, confusion, location-unknown, correction
- **Emotional handling (static-first)**: 6 emotion-specific static responses (scared, sad, rough_day, shame, grief, alone) selected by `_pick_emotional_response()` — LLM is NOT called. Single "Talk to a person" button, no service menu. Follows AVR pattern from clinical chatbot research
- **Frustration 3-tier escalation**: persistent `_frustration_count` counter with varied responses — 1st: full empathetic, 2nd: shorter/direct, 3rd+: immediate navigator only. Counter survives intermediate messages
- **Negative preference handling**: detects rejection of all offered options ("none of those", "not what I need" — 19 phrases). Acknowledges rejection explicitly, offers alternative service categories + peer navigator
- **Conversational awareness guard**: casual chat patterns ("how are you", "just wanted to chat") suppress service category buttons. Prevents first-turn casual greetings from showing the full service menu
- **Privacy routing exception**: `bot_question` overrides `has_service_intent` in routing — privacy questions like "do they get my info?" aren't swallowed by the service flow even when service keywords are present
- **Intensifier stripping**: `_strip_intensifiers()` removes 19 common adverbs (really, very, so, just, etc.) before phrase matching. Combined with contraction normalization, `_classify_tone()` checks 4 variants per message
- **Post-normalization emotional phrases**: `_EMOTIONAL_PHRASES` includes both contraction ("i'm scared") and expanded ("i am scared") forms for 13 emotional states (135 total phrases)
- **Location-unknown interceptor**: when the bot asks for location and the user says "I don't know" / "anywhere" / "here", offers geolocation and borough buttons instead of falling into the confused handler. Guards: only fires when service_type is set, location is missing, and no pending confirmation
- **Service flow continuation**: when a user already has a service_type and provides new slot data (e.g., "near me", "close by", "I'm 25", "with my kids") in a message not classified as "service", the system treats it as a service flow continuation rather than falling through to the LLM
- **Narrative extraction**: long messages (20+ words) are detected as narratives and processed with urgency-aware slot extraction that prioritizes shelter/safety over food/employment. Regex fallback handles narrative extraction when LLM is unavailable
- **Bot self-knowledge**: live capability sourcing from actual code (service categories, PII types, location count) rather than hardcoded facts. Topic matching for 12+ question types with LLM context generation
- **Confidence scoring**: every routing decision is tagged with a confidence level (high/semantic/medium/low/disambiguated) and stored in audit events. Regex matches = high, semantic embedding matches = semantic, LLM classification = medium, fallback = low. The audit log aggregates confidence distributions per session and tracks `semantic_rate` alongside `high_rate` and `low_rate`
- **Disambiguation prompts**: when a message is ambiguous between a post-results question and a new service request, the bot asks the user to clarify instead of guessing. Presents quick-reply buttons for both interpretations
- **"Not what I meant" recovery**: correction phrases ("not what I meant", "you misunderstood") trigger a handler that clears pending state, shows what the bot was doing, and offers alternatives. "❌ Not what I meant" button appears on low-confidence responses
- **Post-results escape hatch**: new service requests ("I need X", "where can I go", "looking for") are no longer intercepted by the post-results handler. Messages with a new location clear stored results automatically
- **LLM conversational fallback**: Haiku handles general/off-topic messages
- **Admin console**: conversation viewer, event log, metrics dashboard, in-browser eval runner
- **LLM-as-judge eval**: 167 scenarios scored on 11 dimensions — 8 core (slot accuracy, dialog efficiency, tone, safety, confirmation UX, privacy, hallucination resistance, error recovery) + 3 domain-specific (dignity & anti-stigma, cultural responsiveness, equity of access). Judge uses Claude Opus with weighted dimension scoring
- **Accessibility**: screen reader support, keyboard navigation, voice input (Web Speech API)
- **Anonymized audit logging**: conversation turns, query executions, crisis events
- **In-memory sessions**: no persistent conversation storage, 30-min TTL, LRU eviction at 500-session cap
- **Chat history persistence**: conversation survives page refresh via Zustand `localStorage` sync; auto-resets after 30-min inactivity to match backend TTL
- **Result sorting & pagination**: open-now first, then recently verified, then name; proximity-first when geolocation available. Users can re-sort by "recently verified" or "most services" after results. Initial query fetches 25, displays first 10 — "📋 Show N more" for the rest
- **Auto-execute for urgent queries**: when urgency is high and slots are sufficient, skips confirmation and executes immediately. Medium urgency still confirms
- **Day-specific hours**: "are they open Saturday?" queries `holiday_schedules` for the requested weekday and returns per-service hours. Weekend queries fetch both Saturday and Sunday
- **Error boundaries**: route-level (chat, admin, global) + component-level (ServiceCarousel) + custom 404
- **Security**: CORS allowlist, CSRF middleware, HMAC-signed session tokens, admin API key auth, CSP/X-Frame-Options/Permissions-Policy headers, eval subprocess isolation
- **Stability**: 1,000-char message length limit (frontend + backend), coordinate validation (lat ±90, lng ±180), 10s LLM timeout, 5s DB statement timeout, 30s frontend fetch timeout, admin endpoint rate limiting (120/min IP + 5/hr eval), rate limiter memory cap (5,000 buckets)
- **Observability**: `X-Request-ID` correlation IDs flow from frontend → Next.js proxy → FastAPI backend → audit log, enabling end-to-end request tracing
- **Admin data caching**: centralized Zustand store with 30-second staleness threshold; navigating between admin tabs reuses cached data
- **Test suite**: 46 pytest files (~1,900+ tests) organized into `tests/unit/` and `tests/integration/`, plus an `eval/` directory. LLM-as-judge evaluation: 167 scenarios across 20 categories, 11 dimensions, Opus judge

## Known Gaps / In Progress

- **Adversarial LLM false positives** — The unified classification gate classifies nonsensical service requests ("helicopter ride") as `service_type=other` instead of returning null. Fix: tighten the LLM prompt to restrict "other" to known social service subcategories.
- **Slot overwrite on contradiction** — `multiturn_change_mind` (3.25): when user says "actually, shelter" mid-conversation, the filled slot is not overwritten. Requires contradiction detection.
- **Keyword brittleness (addressed)** — The Tier 2 semantic router handles novel phrasings that regex keywords miss ("insulin" → medical, "felon looking for work" → employment). The regex layer has been audited per `REGEX_AUDIT.md`: 11 collision-prone keywords moved from substring matching to word-boundary patterns (mail, soap, pads, wic, visa, meal, pants, and 4 from prior audits), 11 context-dependent keywords retired entirely to the semantic layer (formula, physical, vision, intake, court, bail, job, sick, room, snap, transit), 4 false-positive population phrases removed (have a record, did time, senior, navy), and 3 collision-prone word-boundary patterns removed (prep, parole, probation). The remaining 379 keywords (359 SERVICE_KEYWORDS + 20 word-boundary patterns) have zero known collision risks. The model must be downloaded on first startup (~80 MB) — requires internet access to `huggingface.co`.
- **Multilingual support** — English only. Spanish keyword support is designed (Phase 6 in implementation plan) but not yet implemented. The semantic router can be switched to `paraphrase-multilingual-MiniLM-L12-v2` for Spanish support (one-line change).
- **Schedule data coverage** — sparse; only walk-in services have >40% coverage. Day-specific hours queries are supported but coverage varies by service
- **Sort by nearest** — not implemented. Would require PostGIS distance calculation stored on service cards for client-side re-sort. Current sort options are "recently verified" and "most services"
- **LLM call instrumentation** — `log_llm_call()` API is defined in audit_log.py but not yet wired into `claude_client.py` call sites. Metrics section shows "No data" until instrumentation is added.
- **Persistent storage** — when `PILOT_DB_PATH` is set, audit events and sessions are persisted to SQLite (WAL mode) and hydrated on startup. When unset, in-memory only
- **`housing_assistance` not in LLM enum** — the `_SERVICE_TYPE_ENUM` in `llm_slot_extractor.py` has 9 values (no `housing_assistance`). Housing assistance keywords are routed via regex only. The LLM routes these to `other` or `shelter`. Low-impact since the regex keywords are specific ("rental assistance", "help with rent")
- **Disabled/service keyword overlap** — "disabled" exists in both `SERVICE_KEYWORDS["other"]` and `_POPULATION_PHRASES`. "I'm disabled" extracts both `service_type=other` and `_populations=["disabled"]`. Functionally correct but could cause unexpected primary service routing when combined with other services

## Running Tests

```bash
cd backend
source venv/bin/activate
pytest                                    # all tests (no API key or DB needed)
pytest tests/unit/                        # fast unit tests only
pytest tests/integration/                 # integration tests (mocked DB/LLM)
pytest tests/unit/test_slot_extractor.py  # single file
pytest -k reset                           # filter by test name
```

All tests mock `claude_reply()` and `query_services()` — no live services required.
Tests are organized into `tests/unit/` (31 files, no external deps) and `tests/integration/` (15 files, use `send()`/`send_multi()` helpers).
Shared fixtures and helpers live in `tests/conftest.py` (use `send()`, `send_multi()`,
`assert_classified()`). For live LLM integration tests:

```bash
ANTHROPIC_API_KEY=... pytest tests/test_llm_slot_extractor.py -k live
```

## Code Conventions

- **Three-tier pattern**: slot extraction uses regex first (fast, deterministic), semantic
  embedding second (handles novel phrasings, ~2-5ms, free), LLM third (handles complex
  narratives, ~1-3s). Classification and crisis detection follow the same multi-stage approach.
  New detection features should follow this pattern: start with regex, add semantic routes
  for generalization, reserve LLM for genuinely ambiguous inputs.
- **No LLM-generated service data**: the LLM handles conversation only. All service
  results come from parameterized SQL templates in `query_templates.py`. Never let the
  LLM produce service names, addresses, or phone numbers.
- **Fail-open for safety**: if the LLM is unavailable during crisis detection, the system
  returns a safety response with hotline numbers rather than falling through to normal
  conversation.
- **Model constants** are centralized in `claude_client.py` — don't hardcode model IDs
  elsewhere.

## Common Pitfalls

- Editing slot extraction logic without updating both `slot_extractor.py` (regex) **and**
  `llm_slot_extractor.py` (LLM) — they must stay in sync on supported slot names/values.
- Adding a new service category requires updates in `query_templates.py` (SQL template),
  `slot_extractor.py` (keywords), `semantic_routes.py` (example utterances), and
  `phrase_lists.py` (service label). The semantic route definitions must use the same
  category keys as `SERVICE_KEYWORDS` in `slot_extractor.py`.
- Adding new example utterances to semantic routes requires no code changes — just edit
  `semantic_routes.py` and restart. But don't add the same utterance to two different
  routes (cross-route duplicates cause nondeterministic routing).
- Adding a new phrase list or keyword goes in `phrase_lists.py`, not `chatbot.py`.
  Classification logic is in `classifier.py`, response strings in `responses.py`,
  confirmation logic in `confirmation.py`.
- The DB is **read-only** — never add write queries.
- `conftest.py` defines mock data used across all test files. If you change response
  shapes (e.g. `ChatResponse` fields), update the mocks there too.

## Running Locally

See `docs/SETUP.md` for full instructions.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string for Streetlives DB |
| `ANTHROPIC_API_KEY` | Yes (for full features) | Enables LLM classification, slot extraction, crisis detection, and conversational fallback. System works in regex-only mode without it. |
| `CHAT_BACKEND_URL` | No | Frontend → backend URL (defaults to `http://localhost:8000`, set to Render URL in prod) |
| `RATE_LIMIT_SESSION_PER_MIN` | No | Per-session messages/minute (default: 12) |
| `RATE_LIMIT_SESSION_PER_HOUR` | No | Per-session messages/hour (default: 60) |
| `RATE_LIMIT_SESSION_PER_DAY` | No | Per-session messages/day (default: 200) |
| `RATE_LIMIT_IP_PER_MIN` | No | Per-IP messages/minute (default: 30) |
| `RATE_LIMIT_IP_PER_HOUR` | No | Per-IP messages/hour (default: 150) |
| `RATE_LIMIT_IP_PER_DAY` | No | Per-IP messages/day (default: 500) |
| `RATE_LIMIT_FEEDBACK_PER_MIN` | No | Feedback requests per session/minute (default: 10) |
| `SESSION_SECRET` | Yes (prod) | HMAC key for signing session tokens. If unset, tokens are unsigned (dev mode) |
| `ADMIN_API_KEY` | Yes (prod) | Bearer token required for all `/admin/api/*` endpoints. If unset, admin is open (dev mode) |
| `CORS_ALLOWED_ORIGINS` | Yes (prod) | Comma-separated list of allowed origins for CORS. If unset, allows all origins (dev mode) |
