# Semantic Routing Layer — Design Document

## Decision: Local Embedding Model with `all-MiniLM-L6-v2`

**Status:** ✅ Shipped (April 2026). Implementation lives in `backend/app/services/semantic_router.py`. The 3-tier classification cascade described below is what the chatbot runs in production.
**Author:** Engineering team
**Date:** April 2026

---

## 1. What This Is

A semantic routing layer that sits between the existing regex keyword matcher and the LLM fallback. Instead of matching exact keywords, it converts the user's message into a 384-dimensional vector and compares it against pre-embedded example utterances for each service category. The closest match above a confidence threshold determines the service type.

This eliminates the entire class of "missing keyword" failures. When a user says "I ran out of insulin," the embedding is semantically close to "I need my medication" and "where can I get a prescription filled" — even though these phrases share zero keywords. No keyword enumeration needed.

### The 3-Tier Cascade

```
User message
    │
    ▼
┌──────────────────────┐
│  Tier 1: Regex       │  <1ms, free, handles ~85%
│  Exact keyword match │  "food pantry" → food ✓
│  Fast, deterministic │  "insulin" → ? (miss)
└──────────┬───────────┘
           │ miss
           ▼
┌──────────────────────┐
│  Tier 2: Semantic    │  ~2-5ms, free (local), handles ~10%
│  Embedding similarity│  "insulin" → close to medical route ✓
│  Generalizes from    │  "felon looking for work" → close to
│  example utterances  │  employment+reentry routes ✓
└──────────┬───────────┘
           │ below threshold
           ▼
┌──────────────────────┐
│  Tier 3: LLM        │  1-3s, $0.001/msg, handles ~5%
│  Full NLU extraction │  Complex multi-intent narratives
│  Most flexible       │  Ambiguous, context-dependent
└──────────────────────┘
```

---

## 2. Why `all-MiniLM-L6-v2`

### Model Specifications

| Attribute | Value |
|---|---|
| Architecture | 6-layer MiniLM (distilled from BERT) |
| Parameters | 22 million |
| Embedding dimensions | 384 |
| Max sequence length | 128 tokens (~100 words) |
| Model size on disk | ~80 MB (PyTorch), ~30 MB (ONNX quantized) |
| License | Apache 2.0 |
| Training data | 1 billion+ sentence pairs |
| Framework | sentence-transformers (Hugging Face) |

### Why This Model Over Alternatives

We evaluated five approaches. Here's why local embedding with `all-MiniLM-L6-v2` is the right choice for YourPeer.

#### Option A: Local `all-MiniLM-L6-v2` (SELECTED)

The model runs entirely on CPU in the same process as the chatbot. No network calls, no API keys, no rate limits, no costs. It embeds a sentence in 2-5ms on commodity hardware. For YourPeer's volume (~2,000-3,500 sessions/month, each 2-5 messages), this means the model handles the entire monthly load in under 1 second of cumulative compute.

The model was fine-tuned on 1 billion+ sentence pairs for semantic similarity tasks — exactly what intent routing needs. It achieves 84-85% on the STS-B semantic similarity benchmark, which is more than sufficient for routing across 10-16 well-separated service categories. Production case studies report 92-96% precision for intent routing with just 10-15 example utterances per route.

**Pros:** Zero cost, zero latency, zero external dependencies, runs offline, data never leaves the server, Apache 2.0 license, widely deployed and battle-tested (150+ community stars on Hugging Face, most downloaded sentence-transformers model).

**Cons:** 80 MB added to deployment, ~500 MB with PyTorch dependency (can be reduced to ~30 MB with ONNX runtime), first-load latency of 1-2 seconds (amortized across all requests).

#### Option B: OpenAI `text-embedding-3-small` (API)

Higher-quality embeddings (1536 dimensions) but requires an API call for every message. At $0.02 per million tokens, cost is negligible (~$0.50/month at YourPeer's volume), but it introduces a network dependency, ~50ms latency per call, and sends user messages to a third party.

**Why not:** YourPeer handles sensitive population data (homelessness, DV, reentry). Sending messages to an external API — even for embedding, not generation — adds privacy risk and a single point of failure. The quality difference between MiniLM (384d) and OpenAI (1536d) is irrelevant for routing across 9 categories; both achieve >95% precision at this granularity.

#### Option C: Fine-tuned DistilBERT classifier

Train a supervised classification model on labeled intent data. Achieves the highest accuracy (98% reported in medical chatbot studies) but requires labeled training data, a training pipeline, periodic retraining, and GPU resources.

**Why not:** YourPeer doesn't have labeled intent data yet. Creating it requires annotating hundreds of real user messages. The semantic router approach achieves 92-96% precision with zero training — just example utterances. When YourPeer has production chat logs, a fine-tuned classifier could replace the semantic router, but it's premature now.

#### Option D: Aurelio `semantic-router` library

A purpose-built library for exactly this use case. Clean API, supports multiple encoders, handles route matching and thresholds. Built by the team that popularized the concept.

**Why not for now:** It adds an additional dependency layer on top of sentence-transformers and its API has changed between versions. Since YourPeer's routing logic is simple (cosine similarity against pre-embedded routes), implementing it directly with sentence-transformers gives us more control and fewer dependencies. If routing logic gets more complex (nested routes, dynamic thresholds), we could adopt it later.

#### Option E: Full LLM classification for all messages

Route every message through Claude Haiku or GPT-3.5 for intent extraction. Most flexible, handles arbitrary complexity.

**Why not:** At 1-3 seconds per call and ~$0.001 per message, this is too slow and expensive for a first-pass router. The LLM already exists as Tier 3 — the point of Tier 2 is to handle the 10-15% of messages that regex misses without paying LLM latency. Industry consensus is that embedding routing should handle the middle tier, with LLMs reserved for the hardest 5%.

### Model Comparison Summary

| Approach | Latency | Cost/msg | Accuracy | Privacy | Setup | Maintenance |
|---|---|---|---|---|---|---|
| **MiniLM local (A)** | **2-5ms** | **$0** | **92-96%** | **Full** | **Low** | **Low** |
| OpenAI API (B) | 50-100ms | $0.00002 | 95-98% | Shared | Low | Low |
| Fine-tuned BERT (C) | 5-50ms | $0 | 97-99% | Full | High | Medium |
| semantic-router (D) | 2-5ms | $0 | 92-96% | Full | Low | Low |
| LLM classify (E) | 1-3s | $0.001 | 95-99% | Shared | Low | Low |

---

## 3. How It Works in YourPeer

### Integration Points

> **Migration state (Phase 3, April 2026).** The Phase 0-3 unified-extractor migration changed which module the second integration point lives in. With `USE_UNIFIED_EXTRACTOR` default-ON (Phase 3), the safety-net call lives in the unified path at `app.services.slot_extraction` and the legacy `extract_slots_smart()` referenced in code snippets and tables below is still active behind `USE_UNIFIED_EXTRACTOR=0` (opt-out for emergency rollback). Phase 4 deletes the legacy module; the integration semantics described here apply to both paths until then. See `docs/design/UNIFIED_EXTRACTOR_MIGRATION.md`.

The semantic router fires at **two points** in the pipeline for maximum coverage:

**1. Hybrid multi-intent extraction in `backend/app/services/chatbot/pipeline.py`** (runs on every message, as part of the unified classification cascade — post-Phase-3 location; was `chatbot.py` pre-April 2026): <!-- drift:ignore: historical chatbot.py reference; package now lives at chatbot/ -->

```python
# After regex extraction — semantic always runs, even when regex found something
early_extracted = extract_slots(message)

from app.services.semantic_router import classify_all_services, is_available
if is_available():
    regex_found = set()
    if early_extracted.get("service_type"):
        regex_found.add(early_extracted["service_type"])
    for addl in (early_extracted.get("additional_services") or []):
        regex_found.add(addl[0])

    semantic_matches = classify_all_services(message, exclude=regex_found)
    for sm in semantic_matches:
        if early_extracted.get("service_type") is None:
            early_extracted["service_type"] = sm.service_type  # semantic becomes primary
        else:
            queued = early_extracted.get("additional_services") or []
            queued.append((sm.service_type, None, None))  # semantic adds to queue
            early_extracted["additional_services"] = queued
```

The `exclude` parameter skips routes that regex already found, avoiding duplicate work. The embedding is computed once (~5ms); scoring against ~10 routes is <0.1ms. This enables multi-intent extraction: regex catches "eat" → food, while semantic catches "anywhere to sleep" → shelter from the same message.

**2. Inside `pipeline._run_early_extraction()` in `chatbot/pipeline.py`** (single-match fallback after regex misses):

```python
# After regex, before LLM gate — single-match
if early_extracted.get("service_type") is None:
    match = classify_service(message)
    if match:
        early_extracted["service_type"] = match.service_type
```

This is the unified architecture's invocation point (Phase 4, April 2026). The legacy `extract_slots_smart()` orchestration that previously hosted this fallback was deleted; semantic routing now runs unconditionally in `_run_early_extraction` for every message that regex didn't resolve.

### Health Check API

The module exposes a `get_status()` function for the `/api/health` endpoint:

```python
from app.services.semantic_router import get_status
status = get_status()
# {"available": True, "model": "all-MiniLM-L6-v2", "route_count": 16}
```

### Route Definitions

Each service category gets 10-20 example utterances that define its semantic neighborhood. These are full phrases representing how real users describe this need — not keywords.

```python
# app/services/semantic_routes.py

SERVICE_ROUTES = {
    "medical": [
        "I need to see a doctor",
        "I ran out of my medication",
        "I'm diabetic and need insulin",
        "where can I get a prescription filled",
        "I need medical attention",
        "free clinic near me",
        "I have an infection and no insurance",
        "I need my blood pressure checked",
        "where can I get dental work done",
        "I need to get tested for STDs",
        "I have asthma and need an inhaler",
        "I need naloxone or narcan",
        "where can I get methadone",
        "I need prenatal care",
        "I have a wound that needs treatment",
    ],
    "shelter": [
        "I need somewhere to sleep tonight",
        "I'm homeless and need a bed",
        "where can I find a shelter",
        "I got kicked out and have nowhere to go",
        "I'm sleeping on the street",
        "I need a safe place to stay",
        "I'm aging out of foster care and need housing",
        "I need transitional housing",
        "I was evicted and need help",
        "is there a warming center nearby",
        "I need a drop-in center",
        "my family needs emergency housing",
        "I'm couch surfing and need stability",
        "I need a place to crash tonight",
        "where is the DHS intake center",
    ],
    "food": [
        "I'm hungry and need food",
        "where is the nearest food pantry",
        "I need free meals",
        "where can I get groceries",
        "is there a soup kitchen nearby",
        "I need baby formula",
        "my kids need to eat",
        "I need help with food stamps",
        "where can I get a hot meal",
        "I haven't eaten today",
        "I need WIC assistance",
        "free food distribution near me",
    ],
    "employment": [
        "I need help finding a job",
        "where can I get job training",
        "I'm looking for work",
        "I need help with my resume",
        "are there any job placement programs",
        "I need vocational training",
        "where can I find day labor",
        "I'm a felon looking for employment",
        "I need help getting back to work after prison",
        "summer youth employment programs",
        "I need career counseling",
        "help finding work with a criminal record",
    ],
    # ... similar for: clothing, personal_care, legal, mental_health, other
    # (housing_assistance was retired in the April 15 audit — housing-program
    # keywords now route to `other`)
}

POPULATION_ROUTES = {
    "reentry": [
        "I just got out of jail",
        "I have a criminal record",
        "I'm a felon",
        "I was incarcerated",
        "I'm on parole",
        "I'm on probation",
        "formerly incarcerated looking for help",
        "I did time in prison",
        "I have a felony conviction",
        "just released from Rikers",
    ],
    "veteran": [
        "I'm a military veteran",
        "I served in the Army",
        "I'm a combat veteran",
        "veteran benefits",
        "VA services near me",
        "I served in Afghanistan",
        "honorably discharged veteran",
    ],
    # ... similar for: senior, pregnant, disabled, dv_survivor
}
```

### Initialization (One-Time)

At server startup, all route utterances are pre-embedded and stored in memory. This takes ~1 second and produces ~200 vectors (15 routes × ~13 utterances each):

```python
# app/services/semantic_router.py

from sentence_transformers import SentenceTransformer
import numpy as np
from dataclasses import dataclass

_model = None
_route_embeddings = {}  # {"medical": np.array([...]), ...}

@dataclass
class SemanticMatch:
    service_type: str
    confidence: float
    population: str | None = None

def initialize():
    """Load model and pre-embed all routes. Call once at startup."""
    global _model, _route_embeddings
    _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    for route_name, utterances in SERVICE_ROUTES.items():
        embeddings = _model.encode(utterances, normalize_embeddings=True)
        _route_embeddings[route_name] = embeddings

    for pop_name, utterances in POPULATION_ROUTES.items():
        embeddings = _model.encode(utterances, normalize_embeddings=True)
        _route_embeddings[f"pop_{pop_name}"] = embeddings

def classify_service(message: str, threshold: float = 0.75) -> SemanticMatch | None:
    """Classify a message by semantic similarity to route utterances.
    Returns the single best match above threshold. Used as fallback
    in extract_slots_smart() when regex found nothing."""

def classify_all_services(message: str, threshold: float = None,
                          exclude: set[str] = None) -> list[SemanticMatch]:
    """Score ALL service routes and return every match above threshold.
    Used for hybrid multi-intent extraction — runs on every message.
    The exclude parameter skips routes already found by regex.
    Returns list sorted by confidence (highest first)."""
    if _model is None:
        initialize()

    query_embedding = _model.encode(message, normalize_embeddings=True)

    best_route = None
    best_score = 0.0

    for route_name, route_embeddings in _route_embeddings.items():
        if route_name.startswith("pop_"):
            continue
        similarities = np.dot(route_embeddings, query_embedding)
        max_sim = float(np.max(similarities))
        if max_sim > best_score:
            best_score = max_sim
            best_route = route_name

    if best_score < threshold:
        return None

    # Also check population routes
    best_pop = None
    for route_name, route_embeddings in _route_embeddings.items():
        if not route_name.startswith("pop_"):
            continue
        similarities = np.dot(route_embeddings, query_embedding)
        max_sim = float(np.max(similarities))
        if max_sim >= 0.70:  # Lower threshold for populations
            best_pop = route_name.replace("pop_", "")

    return SemanticMatch(
        service_type=best_route,
        confidence=best_score,
        population=best_pop,
    )
```

### Runtime Cost

| Operation | Time | Frequency |
|---|---|---|
| Model load + pre-embed routes | ~1-2 seconds | Once at startup |
| Embed user message (384d) | ~2-5ms | Every Tier 2 call |
| Cosine similarity (15 routes × ~13 embeddings) | <0.1ms | Every Tier 2 call |
| **Total per message** | **~2-5ms** | **~15% of messages** |
| Memory footprint | ~100 MB | Constant |

At YourPeer's current volume (3,500 sessions/month × ~3 messages × 15% Tier 2 hit rate = ~1,575 embeddings/month), the semantic layer adds less than 8 seconds of total compute per month.

---

## 4. What This Solves

### Keyword Gaps Eliminated

These are the specific eval failures that would not have occurred with a semantic layer:

| Scenario | User Said | Regex Extracted | Semantic Would Extract |
|---|---|---|---|
| `peer_diabetic_insulin` | "I'm diabetic and ran out of insulin" | None (insulin not in keywords) | medical (close to "medication", "prescription") |
| `peer_felon_employment` | "looking for a job that hires felons" | employment (but felon not in populations) | employment + reentry population |
| `peer_aging_out_foster` | "aging out of foster care" | None (foster care not in keywords) | shelter (close to "transitional housing") |
| Future: any novel phrasing | "I need a place to wash my clothes" | None (unless "wash" in keywords) | personal_care (close to "laundry") |

### False Positives Eliminated

The semantic layer doesn't have substring collision problems:

| User Said | Regex Extracts | Semantic Extracts |
|---|---|---|
| "I'm homesick" | medical (sick) | None (not close to any medical route) |
| "good job on that" | employment (job) | None (not close to employment routes) |
| "soap opera" | personal_care (soap) | None (not close to hygiene routes) |
| "email me" | other (mail) | None (not close to mail services) |
| "mathematical formula" | food (formula) | None (not close to food routes) |

### Regex Keywords That Can Be Retired

With the semantic layer handling generalization, the regex layer can be reduced to ~120 unambiguous, domain-specific terms. All short/ambiguous single-word keywords (`room`, `sick`, `job`, `mail`, `soap`, `pads`, `visa`, `wic`, `formula`) can be removed from regex — the semantic layer catches them via meaning, not substrings.

---

## 5. How This Scales

### Short Term (Pilot)

Define 11 service routes + 6 population routes with 10-15 utterances each from the sample queries document, field experience, and eval scenarios. This gives immediate coverage for novel phrasings without keyword patching.

### Medium Term (Post-Pilot)

Use production chat logs to expand route utterances. The process is:
1. Review messages where Tier 2 returned low confidence or None
2. Identify the correct route for each
3. Add the message as an utterance to the appropriate route
4. Re-embed (automatic on restart, <2 seconds)

No code changes, no model retraining, no deployment — just editing a list of phrases. Non-engineers can do this.

### Long Term (Scale)

As YourPeer grows beyond NYC or adds more service categories:

**Adding a new service category:** Define 10-15 example utterances, add to `SERVICE_ROUTES`, restart. The model generalizes immediately.

**Multi-language support:** `all-MiniLM-L6-v2` is English-only. For Spanish (the #2 language at YourPeer), swap to `paraphrase-multilingual-MiniLM-L12-v2` — same architecture, same size, 50+ languages, same API. This is a one-line change.

**Higher accuracy if needed:** Upgrade to `all-mpnet-base-v2` (110M params, 768d embeddings, ~200 MB). This achieves 87-88% on STS-B versus MiniLM's 84-85%. Same API, same integration point, just a model name swap.

**Fine-tuning if needed:** If production data reveals systematic misrouting, fine-tune `all-MiniLM-L6-v2` on labeled (message, route) pairs using the sentence-transformers training API. This is a weekend project, not a research program.

**Volume scaling:** At 100x YourPeer's current volume (350,000 sessions/month), the semantic layer would process ~157,500 embeddings/month — about 13 minutes of compute. No infrastructure changes needed. The model handles 40-100 sentences/second on CPU.

---

## 6. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Misroute between close categories (e.g., shelter vs other when the user's intent is rental help vs temporary bed) | Medium | Use per-route thresholds tuned on eval data. Add "anti-utterances" to routes that shouldn't match certain phrases. (The original version of this risk called out shelter-vs-housing_assistance; `housing_assistance` was retired in the April 15 audit, so the split now lives between `shelter` and `other`.) |
| Model too large for deployment | Low | Use ONNX quantized model (~30 MB). Or Model2Vec (~8 MB, 90% of MiniLM quality, 500x faster). |
| Utterances drift from actual user language | Medium | Quarterly review of Tier 3 fallback logs. Any message that reached the LLM but should have been Tier 2 is a candidate utterance. |
| Startup latency from model load | Low | 1-2 seconds, amortized. Can pre-warm in background thread. |
| Embedding model becomes outdated | Low | sentence-transformers is actively maintained. Model can be swapped without code changes. |
| Multi-intent messages (e.g., "food and shelter") | Medium | Run semantic matching for top-2 routes. If both exceed threshold, extract both as primary + additional_services. Regex already handles multi-intent well. |
| Confidence threshold too high (missed routes) or too low (false routes) | Medium | Start at 0.75, tune per-route using eval scenarios. Log all Tier 2 decisions with confidence scores for ongoing calibration. |

---

## 7. Implementation Plan

| Phase | Work | Effort | Dependencies |
|---|---|---|---|
| 1. Route definitions | Write 10-15 utterances for each of 15 routes | 2-3 hours | Sample queries doc, eval scenarios |
| 2. Core module | `semantic_router.py`: model load, embed, classify | 1-2 hours | `pip install sentence-transformers` |
| 3. Integration | Insert between regex and LLM in `extract_slots_smart()` | 1 hour | Phase 2 |
| 4. Testing | Unit tests: each route with known matches and non-matches | 2 hours | Phase 3 |
| 5. Eval validation | Run eval suite, verify keyword-gap scenarios improve | 1 hour | Phase 4 |
| 6. Regex pruning | Remove collision-prone keywords per REGEX_AUDIT.md | 1-2 hours | Phase 5 validated |

**Total estimated effort: 1-2 days.**

---

## 8. Observability

Every Tier 2 classification should be logged for ongoing calibration:

```python
{
    "tier": 2,
    "message": "[redacted]",        # or hash
    "route": "medical",
    "confidence": 0.82,
    "runner_up_route": "mental_health",
    "runner_up_confidence": 0.61,
    "latency_ms": 3.2,
    "fell_through_to_llm": false,
}
```

Key metrics to monitor:

- **Tier 2 hit rate**: % of messages resolved by semantic routing (target: 10-15%)
- **Confidence distribution**: histogram of match scores. A bimodal distribution (high cluster + low cluster) confirms the threshold is well-calibrated
- **Fallback rate**: % of Tier 2 attempts that fell through to Tier 3 (target: <30% of Tier 2 attempts)
- **Misroute rate**: from eval and manual review. Target: <5%

---

## 9. Decision Record

**Date:** April 2026
**Decision:** Implement local semantic routing with `all-MiniLM-L6-v2`
**Alternatives considered:** OpenAI API embeddings, fine-tuned DistilBERT, semantic-router library, full LLM classification
**Rationale:** Zero cost, zero latency, full privacy, sufficient accuracy for 10-category routing, minimal maintenance, battle-tested model with largest community adoption in the sentence-transformers ecosystem.
**Reversibility:** High. The integration point is 5 lines in `extract_slots_smart()`. Can be disabled with a feature flag or removed entirely without affecting Tiers 1 or 3.
