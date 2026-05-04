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
| `backend/app/main.py` | FastAPI app entry point, CORS, router registration, semantic router pre-warm at startup. Exposes two health endpoints: `/api/health/live` (tight, no DB/LLM/router — cheap enough for the frontend polling hook) and `/api/health` (deep check — DB with 15s probe timeout, LLM, semantic router status informational only). The backend is a Render Private Service, so Render does not probe either endpoint; both exist for our own callers (frontend health hook, admin dashboards, uptime monitors) |
| `backend/app/routes/chat.py` | `POST /chat/`, `/chat/feedback`, and `/chat/location-feedback` endpoints |
| `backend/app/routes/admin.py` | Admin API: conversations, events, stats, eval runner |
| `backend/app/services/chatbot/` | **Package** (was `chatbot.py` monolith until April 2026 Phase 3 decomposition). `__init__.py` re-exports `generate_reply` so older callers keep working unchanged | <!-- drift:ignore: historical chatbot.py reference; package now lives at chatbot/ -->
| `backend/app/services/chatbot/orchestrator.py` | Top-level dispatch: `generate_reply()`. PII redaction → crisis detection → classification → handler routing → logging |
| `backend/app/services/chatbot/pipeline.py` | Classification cascade: combines split classifier (action + tone), semantic router fallback, LLM classification gate. PII warning strings live here |
| `backend/app/services/chatbot/execution.py` | Post-confirmation query execution, Housing-First result ordering, service-card assembly, "I found N option(s) for you — showing the first M" pagination phrasing, co-located multi-service response, population-critical citywide fallback, queue-offer message. Also owns the `_CITY_TO_BOROUGH` lookup table for city-to-borough resolution |
| `backend/app/services/chatbot/context.py` | Shared helpers: `MessageContext` dataclass with the `snapshot_*` fields and `require_merged()` accessor (Phase D), `_DISPLAY_PAGE_SIZE`, `_empty_reply()` factory, `_count_unique_locations()`. (For `NEAR_ME_SENTINEL` see `slot_extraction_regex.py`; for `_CITY_TO_BOROUGH` see `execution.py`.) |
| `backend/app/services/chatbot/tone.py` | `random_warmth_prefix()`, SAMHSA-aligned warmth overlays, shame-normalization prefix logic |
| `backend/app/services/chatbot/logging.py` | `_log_turn()` — every handler calls this at its exit point |
| `backend/app/services/chatbot/handlers/emotional.py` | AVR-pattern handlers for frustration (3-tier escalation + filter-aware cleanup), shame, sadness, distrust, undeserving. `_handle_crisis` dispatches the 7-category step-down |
| `backend/app/services/chatbot/handlers/confirmation.py` | "Food in Brooklyn — sound good?" flow. Contradiction auto-execute, optional-slot re-nudge, context-aware `confirm_yes`/`confirm_deny`, `_deny_contexts` dict |
| `backend/app/services/chatbot/handlers/post_results.py` | After-results interactions: show-more pagination (routes through `_filtered_results` when filter active), sort, questions about specific cards, filter-phrase dispatch, filter-escape on "no thanks", new-search state reset |
| `backend/app/services/chatbot/handlers/general.py` | Greetings, resets, help, "what can you do", bot-identity questions |
| `backend/app/services/chatbot/handlers/meta.py` | Privacy questions, "are you a robot", meta-conversation about the chatbot itself |
| `backend/app/services/chatbot/handlers/accessibility.py` | Language preference hints, Spanish bilingual acknowledgment, immigration-context acknowledgment (A.1.b, April 2026 — fires when asylum/immigration appears as a secondary service behind a non-legal primary) |
| `backend/app/services/classifier.py` | Message classification: `_classify_action()` (includes `_BOROUGH_CHANGE_RE` for "change to Brooklyn" disambiguation), `_classify_tone()`, contraction normalization, intensifier stripping |
| `backend/app/services/phrase_lists.py` | All keyword/phrase lists, quick-reply definitions, service labels, borough suggestion data |
| `backend/app/services/responses.py` | Response strings, emotion-specific responses (9 categories), baseline warmth prefix catalog, LLM prompt builders, bot-question answers |
| `backend/app/services/confirmation.py` | `_build_confirmation_message()`, no-results fallback via `_build_no_results_message()`, borough-suggestion phrasing. (NOT the same file as `chatbot/handlers/confirmation.py` — this is the top-level confirmation module.) |
| `backend/app/services/bot_knowledge.py` | Bot self-knowledge: live capability sourcing, topic matching, LLM context generation |
| `backend/app/services/crisis_detector.py` | Two-stage crisis detection (regex + Sonnet LLM), 8 crisis categories with population-specific resources: suicide_self_harm, medical_emergency, domestic_violence, youth_runaway (Runaway Safeline, Covenant House), assault_victim (Safe Horizon), safety_concern (911, 988, 311 — no DV hotlines), trafficking, violence (threats to harm others, weapons) |
| `backend/app/services/slot_extraction_regex.py` | Regex-based slot extraction (9 service categories after April 15 housing_assistance retirement), `SERVICE_KEYWORDS` dict, `_SERVICE_NEED_PRIORITY` tier table for Housing First ordering, negation-phrase shelter keywords (Feature A — "nowhere to sleep"), gender/LGBTQ identity extraction, population context extraction, organization name extraction, walk-in/no-requirements detection, Spanish service keywords |
| `backend/app/services/post_results.py` | Filter-subcategory engine: `_handle_filter_subcategory()` returns paginated `services` + full `_full_filtered` set for session persistence; `classify_post_results_question()` disambiguates refinement phrases ("ones for families", "more like those", "exclude DHS") via two-phase regex |
| `backend/app/services/semantic_router.py` | Tier 2 semantic routing: `all-MiniLM-L6-v2` sentence embedding model, cosine similarity against pre-embedded route utterances, per-route confidence thresholds, population detection, `get_status()` for health checks, `SentenceTransformer = None` fallback for mocking |
| `backend/app/services/semantic_routes.py` | Route definitions: example utterances per service category and population category. No code changes needed to add utterances — just edit and restart |
<!-- drift:ignore: this row describes the unified package that REPLACED the named legacy modules -->
| `backend/app/services/slot_extraction/` | Unified Tier 3 LLM slot extractor (Phase 4, April 2026). Single Haiku tool_use call returning service_type, location, tone, action, and full slot set; replaces the legacy `llm_slot_extractor.py` + `llm_classifier.py` pair. Internal modules: `dispatch.py` (path selection + LLM calls), `merge.py` (5-trust-model merge), `prompts.py` (tool schema + system prompts), `__init__.py` (public `extract()` entry point). |
| `backend/app/services/session_store.py` | In-memory session state with 30-min TTL (max 500 sessions) |
| `backend/app/services/session_token.py` | Anonymous HMAC-signed session token generation/validation (no user identity) |
| `backend/app/services/persistence.py` | Optional SQLite write-through for session state — set `PILOT_DB_PATH` to enable survival across deploys |
| `backend/app/services/rate_limiter.py` | Per-session rate limiting with graceful fallback — returns "I need a moment" rather than an HTTP error |
| `backend/app/services/audit_log.py` | Anonymized event logging (capped ring buffer), P0-P3 metrics aggregation (confidence, recovery rates, session metrics, no-result by service, time-of-day, geographic demand, frustration tiers, session duration, repetition rate, LLM call metrics) |
| `backend/app/llm/claude_client.py` | Anthropic client (lazy init), model constants, exception classification (auth / rate-limit / overloaded), `ping_llm()` for health checking |
| `backend/app/rag/__init__.py` | `query_services()` entry point |
| `backend/app/rag/query_executor.py` | DB execution, location normalization, borough/neighborhood PostGIS logic, production-stability package (15s probe `statement_timeout`, TCP keepalives, 5-minute `pool_recycle`), relaxed-query fallback |
| `backend/app/rag/query_templates.py` | Parameterized SQL templates (food, shelter, clothing, etc. — `housing_assistance` redirects to `other` for legacy-caller safety), dynamic ORDER BY with population boosts, eligibility/review highlight/required docs/languages subqueries |
| `backend/app/rag/boundaries.py` | NYC borough polygon validation — `data/nyc_boroughs.geojson` is the polygon source. Used to confirm `pa.city` field hasn't drifted |
| `backend/app/rag/data/nyc_boroughs.geojson` | Polygon shapefile checked into the repo — don't delete |
| `backend/app/privacy/pii_redactor.py` | PII detection and redaction (phone, SSN, email, DOB, address, names, gender identity); emits user-facing warnings when sensitive PII (SSN strong warning, phone lighter heads-up) is detected |
| `backend/app/models/__init__.py` | Package init — was silently `.tarignore`'d in a prior release and had to be restored; if you see `ModuleNotFoundError: app.models`, check for this |
| `backend/app/models/chat_models.py` | Pydantic models: `ChatRequest`, `ChatResponse`, `ServiceCard` (includes `latitude`, `longitude`, `service_taxonomies` for frontend map + filtering), `QuickReply` |
| `backend/app/utils/` | Small shared helpers — currently minimal, grows as cross-cutting concerns accumulate |
| `frontend-next/src/components/chat/` | Chat UI components (ChatContainer with three-state health indicator, ServiceCard, QuickReplies, VoiceInputButton) |
| `frontend-next/src/hooks/use-backend-health.ts` | Backend health polling hook — polls every 60s, derives connected/degraded/unreachable status. Currently points at `/api/health` (deep check); worth pointing at `/api/health/live` to reduce log noise from slow DB queries |
| `frontend-next/src/components/admin/system-health.tsx` | Admin system health card — real-time component status (Backend, DB, LLM, Semantic Router) |
| `frontend-next/src/lib/chat/store.ts` | Zustand chat store with `localStorage` persistence |
| `frontend-next/src/lib/admin/store.ts` | Zustand admin store with staleness-based caching |
| `frontend-next/src/app/admin/` | Staff console pages (overview, conversations, metrics, queries, evals, models) |
| `frontend-next/next.config.js` | CSP + HSTS headers, security config |
| `tests/conftest.py` | Pytest fixtures, mock data, test helpers |
| `tests/eval/eval_llm_judge.py` | LLM-as-judge evaluation (167 scenarios, 11 dimensions, Opus judge, weighted scoring) |

## What's Working

- **9 service categories**: food, shelter, clothing, personal care, medical, mental health, legal, employment, other. (`housing_assistance` was retired in the April 15 audit — YourPeer had no equivalent. Housing-program keywords now route to `other`; enforcement lives in `tests/unit/test_audit_regression.py::TestHousingAssistanceRemoval`.)
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
- **PII redaction & safety warnings**: phone, SSN, email, DOB, address, name, gender identity detection/redaction on every message. When sensitive PII (SSN, phone) is detected, a safety warning is prepended to the response: SSN gets "For your safety, please don't share your Social Security number..."; phone gets a lighter heads-up. PII warning + service confirmation are combined seamlessly
- **Baseline warmth prefixes**: every routine service flow gets a randomized warmth prefix ("Let me see what's available.", "I can help with that.", "Let's find something for you." — 7 variants) to prevent "functional but flat" responses. Fires only when no emotional/shame/urgent context is detected. Stored in `responses.py` as `random_warmth_prefix()`
- **Spanish bilingual acknowledgment**: when Spanish is detected alongside a service request, the bot prepends a bilingual note ("I can see you may prefer Spanish — lo siento, por ahora solo puedo ayudar en inglés. I'll do my best to help.") and still processes the search. Spanish-only messages (no service intent) get a full bilingual response with peer navigator option. Basic Spanish service keywords added (comida, refugio, albergue, tengo hambre)
- **Benefits sub-type labels**: "food stamps" shows as "food stamps / SNAP", "benefits" as "benefits enrollment", "ebt" as "EBT / food stamps", "medicaid" as "Medicaid enrollment" in confirmation messages — instead of the vague "other services"
- **Warm confirmation reframe**: confirmation messages use "I'll look for food in Brooklyn — does that sound right?" instead of "Does this look right? food in brooklyn." Results delivery uses "I found X option(s) for you" instead of "Here are X options"
- **Service cards**: structured results with name, org, address, phone (with extensions), hours, fees, open/closed status, referral badges, eligibility summary, review highlights, required documents, languages spoken, accessibility info, stale data warnings, action links, and per-location feedback
- **Organization name search**: users can search by org name ("tell me about Covenant House", "Safe Horizon in Harlem"). 35+ multi-word names matched via substring, 5 abbreviations via word-boundary regex. Org name alone is sufficient (no location required). Returns all services at the matching organization
- **Walk-in / no-requirements filter**: 20 phrases ("walk-in only", "no referral needed", "without appointment") exclude services requiring membership or referral. Universal optional filter across all templates
- **Gender & LGBTQ identity filtering**: extracted only when explicitly stated (never inferred). Binary gender (male/female) passes to SQL filter. Transgender/nonbinary/LGBTQ bypass the eligibility filter and trigger taxonomy boosts for affirming services. Confirmation shows "LGBTQ-friendly" label. Gender terms redacted from stored transcripts
- **Population context extraction & query boosts**: `_populations` slot detects veteran, disabled, reentry, foster_youth, dv_survivor, pregnant, senior as cross-cutting identity attributes. Foster youth is distinct from reentry — "aging out of foster care" maps to `foster_youth`, NOT `reentry`. Pregnancy sets a `pregnant` population tag, NOT `family_status: with_children`. Veterans get taxonomy-based boost (services tagged "Veterans" rank higher). All other populations get description-based ORDER BY boost (dynamic `pop_boost_pattern` applied across all 10 templates). Senior auto-inferred from age ≥ 62. Multiple populations supported. Confirmation shows context-aware prefixes ("veteran-friendly food", "youth-friendly shelter"). Stored with `_` prefix for PII exclusion
- **DV crisis → population injection**: when crisis detector fires on `domestic_violence` category, `dv_survivor` is injected into session `_populations` regardless of whether the population extractor caught it. This bridges the 51-phrase gap between crisis detection (54 DV phrases) and population extraction (3 matching phrases). Fires in both step-down (service intent) and crisis-only (no service intent) branches
- **Accessibility on service cards**: `accessibility_for_disabilities` table is queried and surfaced on cards as informational text. Not used as a filter — negative values ("Not wheelchair accessible") are displayed so users can make informed decisions
- **Conversational routing**: greeting, thanks, help, reset, escalation, frustration, emotional, negative preference, bot identity, confusion, location-unknown, correction
- **Emotional handling (static-first)**: 9 emotion-specific static responses (scared, sad, rough_day, shame, grief, alone, undeserving, distrust, angry) selected by `_pick_emotional_response()` — LLM is NOT called. Single "Talk to a person" button, no service menu. Follows AVR pattern from clinical chatbot research
- **Frustration 3-tier escalation**: persistent `_frustration_count` counter with varied responses — 1st: full empathetic, 2nd: shorter/direct, 3rd+: immediate navigator only. Counter survives intermediate messages
- **Negative preference handling**: detects rejection of all offered options ("none of those", "not what I need", "already tried those", "this isn't helpful" — 35 phrases after B.1 expansion in April 2026). Acknowledges rejection explicitly, offers alternative service categories + peer navigator. Compound-intent override (B.2, April 2026): when the rejection message also carries a concrete new service intent (e.g., "I already tried those, I need shelter instead"), orchestrator downgrades the action to the service flow with frustration tone so the pivot is honored rather than buried behind a menu.
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
- **Result sorting & pagination**: open-now first, then recently verified, then name; proximity-first when geolocation available. Users can re-sort by "recently verified" or "most services" after results. Initial query fetches 25, displays first 5 (`_DISPLAY_PAGE_SIZE` in `chatbot/context.py`) — "📋 Show N more" for the rest
- **Auto-execute for urgent queries**: when urgency is high and slots are sufficient, skips confirmation and executes immediately. Medium urgency still confirms
- **Day-specific hours**: "are they open Saturday?" queries `holiday_schedules` for the requested weekday and returns per-service hours. Weekend queries fetch both Saturday and Sunday
- **Error boundaries**: route-level (chat, admin, global) + component-level (ServiceCarousel) + custom 404
- **Security**: CORS allowlist, CSRF middleware, HMAC-signed session tokens, admin API key auth, CSP/X-Frame-Options/Permissions-Policy headers, eval subprocess isolation
- **Stability**: 1,000-char message length limit (frontend + backend), coordinate validation (lat ±90, lng ±180), 10s LLM timeout, 5s DB statement timeout, 30s frontend fetch timeout, admin endpoint rate limiting (120/min IP + 5/hr eval), rate limiter memory cap (5,000 buckets)
- **Observability**: `X-Request-ID` correlation IDs flow from frontend → Next.js proxy → FastAPI backend → audit log, enabling end-to-end request tracing
- **Admin data caching**: centralized Zustand store with 30-second staleness threshold; navigating between admin tabs reuses cached data
- **Test suite**: 69 pytest files (~3,700 collected tests across unit + integration) organized into `tests/unit/` (57 files — no DB or LLM) and `tests/integration/` (12 files — mocked DB/LLM via `send()`/`send_multi()` helpers), plus a separate `tests/eval/` for the LLM judge. Three CI quality gates run on every PR: line coverage (≥85%), static audit (`tests/_tools/audit_tests.py` with baseline check), and — for safety-critical modules only — mutation testing via cosmic-ray. Current audit baseline: 29 findings total (D3=19 advisory, D5=8 deliberate, D8=1, D9=1; D2/D6/D7 all at 0). See `TEST_INFRASTRUCTURE.md` at repo root. LLM-as-judge evaluation: 167 scenarios across 20 categories, 11 dimensions, Opus judge

## Known Gaps / In Progress

- **Adversarial LLM false positives** — The unified classification gate classifies nonsensical service requests ("helicopter ride") as `service_type=other` instead of returning null. Fix: tighten the LLM prompt to restrict "other" to known social service subcategories.
- **Slot overwrite on contradiction** — `multiturn_change_mind` (3.25): when user says "actually, shelter" mid-conversation, the filled slot is not overwritten. Requires contradiction detection.
- **Keyword brittleness (addressed)** — The Tier 2 semantic router handles novel phrasings that regex keywords miss ("insulin" → medical, "felon looking for work" → employment). The regex layer has been audited per `audits/REGEX_AUDIT.md`: 11 collision-prone keywords moved from substring matching to word-boundary patterns (mail, soap, pads, wic, visa, meal, pants, and 4 from prior audits), 11 context-dependent keywords retired entirely to the semantic layer (formula, physical, vision, intake, court, bail, job, sick, room, snap, transit), 4 false-positive population phrases removed (have a record, did time, senior, navy), and 3 collision-prone word-boundary patterns removed (prep, parole, probation). The remaining 379 keywords (359 SERVICE_KEYWORDS + 20 word-boundary patterns) have zero known collision risks. The model must be downloaded on first startup (~80 MB) — requires internet access to `huggingface.co`.
- **Multilingual support** — partial Spanish. Spanish detection + bilingual acknowledgment are shipped; see the Spanish bullet above and `docs/design/SPANISH_LANGUAGE_DESIGN.md` for the full-parity proposal. The semantic router can be switched to `paraphrase-multilingual-MiniLM-L12-v2` for multilingual embedding support (one-line change); not yet done. Languages other than English and Spanish are undetected.
- **Schedule data coverage** — sparse; only walk-in services have >40% coverage. Day-specific hours queries are supported but coverage varies by service
- **Sort by nearest** — not implemented. Would require PostGIS distance calculation stored on service cards for client-side re-sort. Current sort options are "recently verified" and "most services"
- **LLM call instrumentation** — `log_llm_call()` was removed as dead code. LLM calls are tracked via `_track_llm_call()` in `claude_client.py` with daily call counting and budget warnings, but not persisted to the audit log. Add a `log_llm_call()` integration if per-call audit logging is needed.
- **Persistent storage** — when `PILOT_DB_PATH` is set, audit events and sessions are persisted to SQLite (WAL mode) and hydrated on startup. When unset, in-memory only
- **`housing_assistance` not in LLM enum** — the `_SERVICE_TYPE_ENUM` in `slot_extraction/prompts.py` has 9 values (no `housing_assistance`). Housing assistance keywords are routed via regex only. The LLM routes these to `other` or `shelter`. Low-impact since the regex keywords are specific ("rental assistance", "help with rent")
- **Disabled/service keyword overlap** — "disabled" exists in both `SERVICE_KEYWORDS["other"]` and `_POPULATION_PHRASES`. "I'm disabled" extracts both `service_type=other` and `_populations=["disabled"]`. Functionally correct but could cause unexpected primary service routing when combined with other services

## Running Tests

```bash
cd backend
source venv/bin/activate
pytest                                    # all tests (no API key or DB needed)
pytest tests/unit/                        # fast unit tests only
pytest tests/integration/                 # integration tests (mocked DB/LLM)
pytest tests/unit/test_slot_extraction_regex.py  # single file
pytest -k reset                           # filter by test name
```

All tests mock `claude_reply()`, `query_services()`, and `detect_crisis()` at their submodule bind sites — no live services required. The `send()`/`send_multi()` helpers patch `detect_crisis` on BOTH `orchestrator` and `classifier` (separate bindings, both reachable at runtime), which is what makes the suite deterministic with or without `ANTHROPIC_API_KEY` set.
Tests are organized into `tests/unit/` (57 files, no external deps) and `tests/integration/` (12 files, use `send()`/`send_multi()` helpers).
Shared fixtures and helpers live in `tests/conftest.py` (use `send()`, `send_multi()`,
`assert_classified()`). For live LLM integration tests:

```bash
ANTHROPIC_API_KEY=... pytest tests/integration/test_slot_extraction_live.py -v
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

## Drift Guards

This repo has a recurring bug class: a hand-maintained mapping (a count in prose, an enumeration in an answer, a workflow-YAML pair of module-to-test-files) drifts out of sync with the live code it was describing. Four cases were fixed in a single week in April 2026; each had gone undetected for months. The recurring pattern motivated a standard guard convention.

**If you're adding a hand-maintained claim about live code** — e.g., a new count in docs, a new enumeration in a topic answer, a new workflow pairing — **add a guard that locks the claim to the code at PR-edit time.** Don't let the next person discover the drift a month from now.

Guard locations by drift shape:

- **Module-internal prose-vs-code** (answer strings, friendly-name maps, summaries in the same module): pytest test class. Example: `tests/unit/test_bot_knowledge.py::TestBotKnowledgeFreshness` (7 guards covering PII-type enumeration, crisis-category naming, "no stale 'English only' claim", etc.).
- **Cross-file prose-vs-code** (markdown describing Python, Python describing YAML): check function in `scripts/check_docs.py`. Example: `check_service_category_enumeration` asserts every `SERVICE_KEYWORDS` key appears in the `FEATURES.md` enumeration bullet.
- **Workflow YAML vs code** (module-to-test pairings, env vars, etc.): pytest test parsing the YAML. Example: `tests/unit/test_mutation_workflow_pairings.py` asserts every mutation-tested module is paired with test files that actually exercise it.

Every guard's failure message should name **what** is wrong, **where** it is, and **how** to fix it — with both "update the prose to match code" and "update the code if the prose is the intended new state" stated as options. The message is the contract documentation.

See `docs/TESTING.md` → Drift Guards for the full catalog, decision table, and checklist for adding new guards.

**Common trigger points for adding a guard:**
- New count claim in a doc ("N crisis categories", "N service types") → check it against the live collection's length
- New enumeration in a user-facing answer or LLM prompt → check every live member appears
- New mapping in a workflow YAML (module → tests, env → config) → check every listed entry exercises its target

## Common Pitfalls

- Editing slot extraction logic without updating both `slot_extraction_regex.py` (regex) **and**
  `slot_extraction/` (LLM, Phase 4 unified) — they must stay in sync on supported slot
  names/values. The LLM tool schema lives in `slot_extraction/prompts.py:_EXTRACT_SLOTS_TOOL`.
- Adding a new service category requires updates in `query_templates.py` (SQL template),
  `slot_extraction_regex.py` (keywords), `semantic_routes.py` (example utterances), and
  `phrase_lists.py` (service label). The semantic route definitions must use the same
  category keys as `SERVICE_KEYWORDS` in `slot_extraction_regex.py`.
- Adding new example utterances to semantic routes requires no code changes — just edit
  `semantic_routes.py` and restart. But don't add the same utterance to two different
  routes (cross-route duplicates cause nondeterministic routing).
- Adding a new phrase list or keyword goes in `phrase_lists.py`, not the `chatbot/` package.
  Classification lives in `classifier.py` + `chatbot/pipeline.py`; response strings in
  `responses.py` and (emotional/handler-specific) in the relevant `chatbot/handlers/*.py`;
  confirmation logic in `confirmation.py` (top-level) and `chatbot/handlers/confirmation.py`
  (handler-level context-aware routing).
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
