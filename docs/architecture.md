# Architecture Overview

## System Diagram

```
User (browser)
  |
  +-- Chat UI (Next.js 15 / React 19)
  |   +-- Zustand store (localStorage persistence, 30-min TTL)
  |   +-- useBackendHealth hook (polls /api/health every 30s)
  |   +-- Three-state indicator (connected / degraded / offline)
  |   +-- Voice input (Web Speech API)
  |
  +-- POST /api/chat (Next.js API route -- proxy + rate limiting)
  |
  +-- FastAPI Backend (Python)
      |
      +-- GET /api/health
      |   +-- Checks: database, LLM key, semantic router -> 200/503
      |
      +-- POST /chat/
      |   +-- PII redaction (every message)
      |   +-- Crisis detection (regex + Claude Sonnet)
      |   +-- Message classification (hybrid 3-tier):
      |   |   1. Regex keywords (<1ms) ──┐
      |   |   2. Semantic embedding ─────┤── both always run, results merge
      |   |      all-MiniLM-L6-v2        │   (~95% of intents resolved here)
      |   |      (~2-5ms, zero cost)     │
      |   |   3. LLM — Claude Haiku ─────┘── only when 1+2 find nothing (~5%)
      |   |      (1-3s, ~$0.001/call)
      |   +-- Slot extraction -> confirmation -> query execution
      |   +-- Structured service cards (from DB, never LLM-generated)
      |
      +-- Streetlives PostgreSQL (read-only, AWS RDS)
      |   +-- 11 parameterized SQL templates
      |   +-- PostGIS proximity search (59 NYC neighborhoods)
      |   +-- Population-based ORDER BY boosts
      |
      +-- Admin Console (/admin)
          +-- System health card (real-time component status)
          +-- Anonymized transcripts, query logs, crisis events
          +-- 35+ metrics across 7 layers
          +-- LLM-as-Judge eval runner (167 scenarios, 11 dimensions, Opus judge)
```

## Key Design Principles

1. **No hallucination by design** -- The LLM handles conversation only. All service data comes from deterministic database queries using pre-reviewed SQL templates.

2. **Graceful degradation** -- The system works at three capability levels:
   - Full: regex + semantic routing + LLM (all features)
   - Degraded: regex + semantic routing (no API key -- keyword and embedding matching only)
   - Minimal: regex only (no API key, no model download -- keyword matching only)

3. **Safety first** -- Crisis detection runs before all other processing. Fail-open policy: if the LLM is unavailable during crisis detection, the system returns safety resources rather than falling through to normal conversation.

4. **Privacy by default** -- PII is redacted from every message before storage. No persistent user identification. Sessions expire after 30 minutes.

## Data Flow

```
User message
  -> PII redaction
  -> Crisis check (regex -> Sonnet LLM if needed)
  -> Action classification (greeting/reset/thanks/help/escalation)
  -> Tone classification (emotional/frustrated/confused/urgent)
  -> Slot extraction (hybrid: regex + semantic both run, LLM if both miss)
  -> Routing decision (confidence tagged: high/semantic/medium/low)
  -> Handler:
      +-- Service flow -> confirmation -> SQL query -> service cards
      +-- Crisis -> hotline resources (static, never LLM-generated)
      +-- Emotional -> AVR response + peer navigator offer
      +-- Post-results -> answer from stored card data (zero LLM)
      +-- General -> Haiku conversational fallback
  -> Audit log entry
  -> Response (text + service cards + quick replies)
```

## Component Dependencies

| Component | Failure Impact | Health Check |
|---|---|---|
| PostgreSQL DB | **Critical** -- no search results possible | SELECT 1 connectivity test |
| Anthropic API | Degraded -- regex-only mode, no LLM features | Live ping via minimal Haiku call (cached 90s), classifies 5 error types |
| Semantic router | Degraded -- skips Tier 2, falls to LLM or regex | Model loaded + route counts + utterance count + embedding dimensions |
| SQLite (pilot DB) | In-memory only -- no data loss, resets on restart | Optional, not checked |

See [CHATBOT_BEHAVIOR.md](CHATBOT_BEHAVIOR.md) for the full routing pipeline and [SEMANTIC_ROUTING_DESIGN.md](design/SEMANTIC_ROUTING_DESIGN.md) for the embedding model design.
