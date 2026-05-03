# YourPeer Chatbot

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Claude API](https://img.shields.io/badge/Claude_API-Anthropic-D4A574?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
[![Render](https://img.shields.io/badge/Render-Deployed-46E3B7?logo=render&logoColor=white)](https://render.com/)
[![Streetlives](https://img.shields.io/badge/Streetlives-YourPeer-FF6B35)](https://www.streetlives.nyc/)

A conversational interface that helps people experiencing homelessness find free services in New York City — food, shelter, clothing, showers, health care, legal help, and more.

Built by [Streetlives](https://www.streetlives.nyc/) x Cornell Tech (PiTech prgram) as a front-end to the [YourPeer](https://yourpeer.nyc/) service directory.

## How It Works

A user describes what they need in plain language — by typing, tapping a quick-reply button, or using voice input. The chatbot extracts the service type and location through natural conversation, confirms the search parameters, then queries the Streetlives database and returns real, verified service listings as interactive cards — with addresses, hours, phone numbers, and links to the full YourPeer listing. The interface supports screen readers, keyboard navigation, and voice input for low-literacy and low-vision users.

```
User:  taps "🍽️ Food"
Bot:   "What neighborhood or borough are you in?"  [Manhattan] [Brooklyn] [Queens] [Bronx]
User:  taps "Brooklyn"
Bot:   "I'll search for food in Brooklyn."  [✅ Yes, search] [📍 Change location] [🔄 Change service]
User:  taps "✅ Yes, search"
Bot:   returns → 2 service cards with names, addresses, hours, and action buttons
```

**No hallucination by design.** The LLM handles conversation only — all service data comes from deterministic database queries using pre-reviewed templates. The bot never makes up service names, addresses, or eligibility rules.

## Quick Start

```bash
# Clone and set up backend
git clone https://github.com/ianlau20/yourpeer-chatbot.git
cd yourpeer-chatbot
python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r backend/requirements.txt

# Configure backend environment
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and DATABASE_URL

# Run backend (Terminal 1)
cd backend
uvicorn app.main:app --reload

# Set up and run frontend (Terminal 2 — requires Node.js 18.18+)
cd frontend-next
npm install
echo "CHAT_BACKEND_URL=http://localhost:8000" > .env.local
npm run dev

# Open http://localhost:3000/chat   (chat interface)
# Open http://localhost:3000/admin  (staff review console)
```

See [SETUP.md](docs/SETUP.md) for detailed instructions including prerequisites, IDE configuration, and Render deployment.

## Architecture Flow

```
User → Chat UI → FastAPI → Classifier → Slot Extraction → Confirmation → Query Templates → Streetlives DB
          ↑                      ↓            ↓                  ↓               ↓                     ↓
   Quick-reply           Crisis Detection  3-tier cascade:   PII Redaction   User confirms       Service Cards
   buttons               (regex + Sonnet)  1. Regex keywords      ↓         or changes slots           ↓
                        → Step-down when   2. Semantic embed  Session Store                      YourPeer links
                          service intent   3. LLM (Haiku)         ↓
                        Greeting / Reset                    Unified LLM Gate
                        Thanks / Help                      (Haiku — when regex+
                        Escalation                         semantic find nothing,
                        Frustration (AVR)                  returns service_type +
                        Emotional (AVR)                   tone + action in one call)
                        Bot identity                             ↓
                        Confused/overwhelmed               Claude Haiku (fallback
                        Confirmation                      for general conversation
                        handling                           and DB failures only)

Staff → Admin Console (/admin) → Audit Log API → Anonymized transcripts, query logs, crisis events, stats
                                       ↓
                                  Eval Results → LLM-as-judge scores (from eval_llm_judge.py)
                                  Model Analysis → Per-task cost/capability analysis
```

The system follows a **Safer, Limited RAG** pattern with four phases:

1. **Intake** — Slot extraction collects structured fields (service type, location, age, urgency, gender/LGBTQ identity, family status, population context) through multi-turn conversation. Quick-reply buttons let users tap instead of type.

   **Service type extraction** uses a 3-tier cascade: (1) regex keyword matching (<1ms, handles ~85%), (2) semantic embedding with `all-MiniLM-L6-v2` (~2-5ms, handles novel phrasings like "I ran out of insulin" → medical, runs locally, zero cost), (3) LLM classification via Claude Haiku (1-3s, handles complex multi-intent narratives). When `ANTHROPIC_API_KEY` is set, a unified LLM classification gate fires on messages where all three tiers find no service type, no action, and no tone — a single Haiku call returns all classification dimensions in one JSON response.

   **Multi-intent extraction** detects all services in a message ("I need food and shelter") through a hybrid approach: regex keywords catch exact matches, while the semantic router (`classify_all_services()`) scores all routes and catches novel phrasings regex missed (e.g., "anywhere to sleep" → shelter). Services are sorted by **need-based priority** grounded in Maslow/Housing First/SAMHSA: shelter and medical first, then food, then clothing, then stability services — not text position. Per-service location binding matches each service to its nearest location when different locations are mentioned ("food in Brooklyn and shelter in Manhattan").

   **Population context** (veteran, disabled, reentry, foster_youth, dv_survivor, pregnant, senior) is extracted as a cross-cutting identity attribute — separate from what service the user needs. A veteran searching for food gets veteran-tagged services ranked higher; a disabled user gets accessibility-related services boosted. Multiple populations are supported ("disabled veteran"). Key distinctions: foster youth ≠ reentry ("aging out of foster care" maps to `foster_youth`, NOT `reentry`), and pregnancy sets a `pregnant` tag only, NOT `family_status: with_children`.

   **Gender & identity** is extracted only when explicitly stated — never inferred from name or voice. LGBTQ, trans, and nonbinary identities trigger taxonomy boosts (prioritizing affirming services like Ali Forney Center) rather than eligibility filters, since the DB only contains binary gender values.

   **Crisis detection** runs on every message before anything else, using regex pre-check followed by Claude Sonnet LLM classification when regex misses — with an emotional phrase guard that prevents sub-crisis expressions ("feeling scared", "I'm struggling") from being over-escalated. When crisis fires alongside service intent, a step-down flow shows crisis resources while preserving the service context (supported for safety_concern, domestic_violence, youth_runaway, and assault_victim categories). For DV crises, `dv_survivor` is injected into the session's population context so that subsequent searches boost DV-specific services — even when the triggering phrase (e.g., "he hits me") doesn't explicitly mention "domestic violence."

   **Routing & tone** — the message classifier routes greetings, resets, escalation, frustration, bot-identity questions, confusion, and help before slot extraction runs. Emotional handling follows the Acknowledge-Validate-Redirect (AVR) pattern from clinical chatbot research. NYC youth slang is supported for confirmations ("bet", "aight", "word") and declines ("nah I'm good").

   **Privacy** — PII, including gender identity terms, is redacted from stored transcripts. When sensitive PII (SSN, phone) is detected, a safety warning is prepended to the bot's response.
2. **Confirmation** — When service type and location are filled, the bot summarizes the search ("I'll look for food in Brooklyn — does that sound right?") and presents quick-reply options: confirm, change location, change service, or start over. The database is only queried after explicit user confirmation. Every routine service flow gets a randomized baseline warmth prefix ("I can help with that.", "Let me see what's available." — 7 variants) to prevent flat, transactional responses. Benefits sub-types show specific labels ("food stamps / SNAP", "Medicaid enrollment") instead of the vague "other services." Spanish detected alongside a service request triggers a bilingual acknowledgment before processing the search.
3. **Query** — Pre-defined, parameterized SQL templates run against the Streetlives PostgreSQL database. Borough-level queries use the `pa.borough` column directly — more reliable than expanding city name lists. Neighborhood queries use PostGIS proximity search (`ST_DWithin`) with coordinates for 59 NYC neighborhoods. If the strict query returns no results, filters are automatically relaxed while keeping location boundaries. Data-informed nearby borough suggestions are offered when results are thin.
4. **Rendering** — Results are returned as structured service cards, never as LLM-generated text. Cards include address, hours, phone, fees, accessibility info (when available from the DB), a "Referral may be required" badge for membership-gated services, and direct links to YourPeer.

## Features

See [FEATURES.md](docs/FEATURES.md) for the full feature reference, organized by area: conversation & intake, crisis detection, search & results, service cards, privacy & safety, accessibility, and staff tools.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy |
| Slot Extraction | Regex (Tier 1) + Semantic embedding with all-MiniLM-L6-v2 (Tier 2) + Claude Haiku (Tier 3, complex inputs) |
| Crisis Detection | Regex pre-check + Claude Sonnet (LLM stage for nuanced/indirect language) |
| Conversational Fallback | Claude Haiku (dialog only, not for service data) |
| Database | Streetlives PostgreSQL on AWS RDS (read-only), PostGIS for neighborhood proximity |
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS, Zustand, Radix UI, Lucide icons |
| Deployment | Render (two services: FastAPI API + Next.js frontend) |

## Models

Three Claude models are used across the system, each assigned to specific tasks based on a cost/capability analysis (see the Model Analysis tab in the admin panel). None of them generate service data. Model selection is centralized in `backend/app/llm/claude_client.py` for production models and `tests/eval/eval_llm_judge.py` for the evaluation judge.

### Claude Haiku (`claude-haiku-4-5-20251001`)

**Used for:** Conversational fallback, slot extraction, and unified classification.

**Conversational fallback** — General responses to messages that don't match any routing category and don't contain service slots. Only runs when all other routing paths have been exhausted. The vast majority of messages never reach the LLM. It handles genuinely open-ended conversational turns: a user telling a story before stating their need, an ambiguous follow-up after results are delivered, or a message the classifier couldn't route. Also used as a database fallback — if a database query throws an exception, the bot calls Haiku with a prompt asking it to acknowledge the issue and keep the user engaged. Haiku never generates service data. Its system prompt explicitly prohibits fabricating service names, addresses, or phone numbers.

**Slot extraction** — Extracting structured fields (service type, location, age, urgency, gender, family status, populations) from natural language. Only runs for messages classified as "complex" by a lightweight complexity check. Simple, clear requests ("I need food in Brooklyn") are handled by regex alone. Haiku runs for long messages, implicit needs, slang, or conflicting signals. Uses the `extract_intake_slots` tool with a strict JSON schema — the model is constrained to return only the defined fields and enum values.

**Unified classification gate** — When regex finds no service type, no action, and no tone on a 4+ word message, a single Haiku call returns all classification dimensions (service_type, location, tone, action, additional_services, urgency, age, family_status, populations) in one JSON response. This fires on ~25% of messages — the ones where regex genuinely has nothing useful. The prompt distinguishes intent from mention ("I saw a doctor on TV" → null, not medical) to prevent false positives.

**Why Haiku:** Speed. Haiku is 4-5x faster than Sonnet, which directly improves chat UX for real-time conversation. All three tasks have simple output constraints (1-3 sentences for conversation, 5-field JSON for slots, 9-field JSON for classification) where Sonnet's deeper reasoning adds no measurable value.

**Requires:** `ANTHROPIC_API_KEY` in `.env`.

---

### Claude Sonnet (`claude-sonnet-4-6`)

**Used for:** Crisis detection (Stage 2 LLM classification).

**Crisis detection** — Only invoked when the regex pre-check returns no match. Clear crisis language ("I want to kill myself") is caught by regex in <1ms and never reaches the LLM. Sonnet handles indirect and paraphrased expressions — "I've been on the streets for months and nothing helps anymore", "no one would notice if I disappeared." `max_tokens` is capped at 60 — the JSON response (`{"crisis": true, "category": "..."}`) is about 15 tokens.

**Why Sonnet for crisis:** This is a safety-critical classification where false negatives have real consequences for vulnerable people. Sonnet's adaptive thinking adjusts reasoning depth to ambiguity, which is exactly what's needed for indirect crisis language. The volume is very low (~5% of turns reach the LLM stage) so the 3x cost premium over Haiku adds negligible total cost.

**Fail-open:** If the Sonnet call fails for any reason, the system returns a general safety response rather than falling through to normal conversation. See [CRISIS_DETECTION.md](docs/design/CRISIS_DETECTION.md) for full details.

**Requires:** `ANTHROPIC_API_KEY` in `.env`. If absent, the LLM crisis detection stage is disabled and only regex detection runs.

---

### Claude Opus (`claude-opus-4-6`)

**Used for:** LLM-as-judge evaluation only.

**LLM-as-judge** — `eval_llm_judge.py` uses Opus to score conversations across 11 dimensions (8 core + 3 domain-specific: dignity & anti-stigma, cultural responsiveness, equity of access). Reports both unweighted and weighted overall scores, where safety-critical dimensions carry higher weight. The judge uses a MORE capable model than the chatbot under evaluation (Haiku + Sonnet) to avoid same-family scoring bias.

**When it runs:** Only when the eval suite is triggered manually — either via `python tests/eval_llm_judge.py` on the command line or via the "Run Evals" button in the admin console. Never runs during normal user interactions.

**Not part of the production system.** The eval runner is a development and QA tool. It consumes API quota but has no effect on conversations.

## Known Limitations & Future Work

These are tracked issues identified during DB audits and pilot testing, deferred for post-pilot resolution.

**Result ordering.** Results are sorted by: (1) open now — services currently open appear first, (2) freshness tier — services verified within 90 days rank above stale results (3 tiers: fresh ≤90d, stale >90d, never verified), (3) recently verified timestamp within each tier, (4) service name as a stable tiebreaker. When browser geolocation is available, distance is the primary sort with open-now and freshness as secondary tiebreakers.

**Schedule data is sparse for most categories.** Only walk-in service types (Soup Kitchen 81%, Shower 55%, Clothing Pantry 64%, Food Pantry 40%) have meaningful schedule coverage. All other categories show 0% coverage. The `FILTER_BY_OPEN_NOW` and `FILTER_BY_WEEKDAY` query filters exist but are intentionally not passed from the chatbot — enabling them would silently exclude the majority of services. See `METRICS.md` section 2.4 for detail.

**Eval runs share the web server host.** The "Run Evals" button runs the LLM-as-judge suite in a subprocess (isolated from request handling via `asyncio.create_subprocess_exec`), but it still runs on the same machine as the web server. Acceptable for the pilot; for production, isolate into a separate worker or task queue to avoid resource contention during long runs.

## Documentation

The full directory map lives at **[docs/README.md](docs/README.md)** — it organizes all 25+ docs into "start here," `design/`, `audits/`, and `ops/`.

If you're just browsing the repo and want the highest-traffic entry points:

- [docs/ONBOARDING.md](docs/ONBOARDING.md) — primary onboarding guide
- [docs/SETUP.md](docs/SETUP.md) — local development setup
- [docs/FEATURES.md](docs/FEATURES.md) — full feature reference
- [docs/CHATBOT_BEHAVIOR.md](docs/CHATBOT_BEHAVIOR.md) — routing pipeline, guardrails, how to extend
- [docs/TESTING.md](docs/TESTING.md) — test suite and eval framework
- [docs/DEPLOY.md](docs/DEPLOY.md) — Render deployment notes

## Related Repositories

| Repo | Description |
|---|---|
| [streetlives/yourpeer.nyc](https://github.com/streetlives/yourpeer.nyc) | The YourPeer web application (Next.js) |
| [streetlives/chat-poc](https://github.com/streetlives/chat-poc) | Original chat proof-of-concept with database schema exploration |

## License

Copyright © 2026 Streetlives, Inc.
