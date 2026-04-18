<!-- drift:ignore-file: historical audit — see the Post-Phase-3 location note in this file for current paths of body references -->
# Feature Parity Gap — Implementation Tasks (v2)

> **Status: ALL 16 GAPS IMPLEMENTED** as of April 2026. This document is retained as a historical reference for the implementation decisions made. See FEATURES.md for current feature descriptions.

> **📎 Post-Phase-3 location note (April 2026)**: this plan was written before the `chatbot.py` → `services/chatbot/` package decomposition. The two body references to `chatbot.py` (tasks 3.6 and 16.1) both describe orchestrator-level routing logic that now lives at `backend/app/services/chatbot/orchestrator.py`. Task 3.6's "skip 'what kind of help?' when org_name is present" and task 16.1's "skip `_pending_confirmation` on high urgency and go to `_execute_and_respond()`" are both implemented there; `_execute_and_respond()` itself now lives at `backend/app/services/chatbot/execution.py`.


> Cross-referenced against: yourpeer.nyc production codebase, IMPLEMENTATION_PLAN_v2.md, AUDIT.md, PHASE3_SPEC.md
> Phase 6 (Spanish) is deferred. Phase 3 (populations) has its own spec.

---


## Gap 1: Review Highlights on Service Cards

**Priority: High** · **Source:** yourpeer.nyc comparison
**Effort:** 2–3 days (depends on identifying the DB table)

yourpeer.nyc shows LLM-generated sentiment highlights from user reviews. The sentiment analysis code exists (`streetlives-api/src/controllers/openai.js`). Our service cards never surface these.

### Tasks

- **1.1** Identify the review highlights table/column in the Streetlives DB (likely a `highlight` or `summary` field on a comments/reviews table)
- **1.2** Add LEFT JOIN or subquery in `query_templates.py` → `_BASE_QUERY` to fetch the most recent highlight per location
- **1.3** Add `review_highlight: Optional[str]` to `ServiceCard` model and `format_service_card()`
- **1.4** Add `review_highlight?: string` to frontend `ServiceResult` type
- **1.5** Render highlight on `service-card.tsx` (quote block with `💬`, muted italic, truncated to ~120 chars)
- **1.6** Tests: unit test with/without highlight data, eval scenario for known reviewed location

---

## Gap 2: Granular Sub-Category Display in also_available

**Priority: High** · **Source:** yourpeer.nyc comparison + AUDIT 1.5
**Effort:** 1–2 days

yourpeer.nyc shows "Food Pantry • Hot Meals • Laundry • Toiletries • Shower." Phase 4 handles sub-category *filtering*, but `also_available` still returns coarse top-level names. AUDIT 1.5 notes these are DB-internal names, not user-friendly.

### Tasks

- **2.1** Update the `also_available` subquery in `query_templates.py` to return actual `taxonomies.name` values instead of mapping to top-level categories
- **2.2** Apply user-friendly label mapping in `format_service_card()` for DB-internal names (e.g., "Substance Use Treatment" → "Substance Use Help")
- **2.3** Expand `ALSO_EMOJI` map in `service-card.tsx` with new taxonomy names: "Immigration Services", "Financial Help", "Overdose Prevention", "Case Management", "Senior Center", "Hot Meals", "Mobile Pantry", etc.
- **2.4** Tests: verify multi-service location returns granular names, snapshot comparison against yourpeer.nyc

---

## Gap 3: Organization Name Search

**Priority: High** · **Source:** yourpeer.nyc comparison + sample queries doc
**Effort:** 3–4 days

Users say "tell me about Safe Horizon" or "where is Covenant House." Currently falls to the general LLM handler.

### Tasks

- **3.1** Add `org_name` slot to `slot_extractor.py` — regex match against top 50–100 org names from DB
- **3.2** Add `org_name` to LLM extractor schema in `llm_slot_extractor.py`
- **3.3** Add to unified classifier in `llm_classifier.py` — when org name detected, classify as `service`
- **3.4** Add `OrgNameQuery` template in `query_templates.py` — `organizations.name ILIKE '%{org_name}%'` with common abbreviation handling
- **3.5** Wire `org_name` into `query_executor.py` → `query_services()`
- **3.6** Update chatbot routing in `chatbot.py` — skip "what kind of help?" when org_name is present, go to confirmation
- **3.7** Tests: "tell me about covenant house" → results, "safe horizon in harlem" → filtered results, 3–4 eval scenarios

---

## Gap 4: Eligibility Display on Service Cards

**Priority: High** · **Source:** yourpeer.nyc comparison + IMPL_PLAN 7f
**Effort:** 2–3 days

yourpeer.nyc shows "Who is this for?" eligibility criteria. Users may travel to a service only to be turned away.

### Tasks

- **4.1** Query `eligibility` table (JSONB `eligible_values`) + `eligibility_parameters` for each service
- **4.2** Format into human-readable string in `format_service_card()` (e.g., "Ages 18–24", "Women only", "Must have ID")
- **4.3** Add `eligibility_summary: Optional[str]` to `ServiceCard` model
- **4.4** Add `eligibility_summary?: string` to frontend `ServiceResult` type
- **4.5** Render on `service-card.tsx` as informational badge (`👤 Ages 18–24 · Women only`)
- **4.6** Tests: service with age+gender eligibility → formatted string, service without → `None`

---

## Gap 5: Accessibility Info on Service Cards

**Priority: Medium** · **Source:** AUDIT 3.2, IMPL_PLAN Phase 3d, PHASE3_SPEC 3e
**Effort:** 1 day

PHASE3_SPEC.md describes the implementation (lateral join on `accessibility_for_disabilities`). Verify this is wired through to the frontend.

### Tasks

- **5.1** Verify `_BASE_QUERY` includes the accessibility lateral join from PHASE3_SPEC 3e — if not, add it
- **5.2** Verify `format_service_card()` maps `accessibility_info` → `accessibility` field
- **5.3** Render on `service-card.tsx`: `♿ Wheelchair accessible` line near address when present
- **5.4** Tests: location with/without accessibility data

---

## Gap 6: Location-Specific Feedback

**Priority: Medium** · **Source:** wireframe doc, AUDIT 7.3
**Effort:** 3 days

Wireframe specifies 5-question feedback per location (safety, friendliness, cleanliness, queer-friendliness, open text). Current chatbot only has general thumbs up/down.

### Tasks

- **6.1** New endpoint `POST /chat/location-feedback` in `routes/chat.py`
- **6.2** `log_location_feedback()` in `audit_log.py`, aggregate in stats
- **6.3** Offer "Rate this location" quick reply per card after results
- **6.4** Inline feedback flow (binary thumbs per dimension + optional text)
- **6.5** `sendLocationFeedback()` in frontend `api.ts` + proxy route
- **6.6** Tests: feedback logged correctly, admin stats include counts

---

## Gap 7: "Show More Results" / Pagination

**Priority: Medium** · **Source:** yourpeer.nyc comparison + IMPL_PLAN 7e
**Effort:** 2 days

Chatbot shows 3–5 results in a carousel. YourPeer paginates at 20.

### Tasks

- **7.1** Return `total_available` count in backend response (total matched, not just returned)
- **7.2** Ensure `_last_results` in session stores ALL results, not just displayed subset
- **7.3** Add "show more", "any others", "what else", "next results" patterns to `post_results.py`
- **7.4** Show "N more available" button at end of carousel in `service-carousel.tsx`, sends "Show all results"
- **7.5** Add `total_available?: number` to frontend `ChatResponse` type
- **7.6** Tests: query returns 10, chatbot shows 5, "show more" returns remaining

---

## Gap 8: Sort Options

**Priority: Low** · **Source:** yourpeer.nyc comparison
**Effort:** 2 days

yourpeer.nyc offers Nearby / Recently Updated / Most Services sort toggles.

### Tasks

- **8.1** Add `sort_by` slot to `slot_extractor.py` — detect "most recently updated", "newest", "closest"
- **8.2** Support three ORDER BY modes in `query_templates.py`: `nearby` (PostGIS), `recently_updated` (last_validated_at DESC), `most_services` (co-located count DESC)
- **8.3** Wire `sort_by` into `query_executor.py`
- **8.4** After 3+ results, show sort quick replies: "📍 Nearest" / "🕐 Recently updated" / "📋 Most services"
- **8.5** Tests: same query with different sort_by values → different orderings

---

## Gap 9: "Advocates / Legal Aid" Missing from Legal Template

**Priority: High** · **Source:** IMPL_PLAN 0b
**Effort:** 5 minutes

YourPeer includes this taxonomy for legal searches. The chatbot's legal template only queries `["legal services", "immigration services"]`. Services tagged "Advocates / Legal Aid" are invisible to chatbot legal searches.

### Tasks

- **9.1** Add `"advocates / legal aid"` to `TEMPLATES["legal"]["default_params"]["taxonomy_names"]` in `query_templates.py`
- **9.2** Add `"Advocates / Legal Aid"` to `taxonomy_aliases` list
- **9.3** Tests: verify legal search returns services tagged "Advocates / Legal Aid"

---

## Gap 10: Requirement Filter ("Walk-In Only")

**Priority: Medium** · **Source:** IMPL_PLAN 7a
**Effort:** 3 hours

YourPeer supports three requirement filters: no requirements, referral required, registered client required. Chatbot shows a badge but can't filter.

### Tasks

- **10.1** Add requirement phrases to `slot_extractor.py`: "walk-in only", "no referral", "no appointment", "drop-in"
- **10.2** Add `FILTER_BY_NO_REQUIREMENTS` SQL fragment in `query_templates.py` — exclude services where `eligibility.eligible_values @> '["true"]'` for `membership` parameter
- **10.3** Wire `no_requirements` param through `rag/__init__.py` → `query_services()`
- **10.4** Tests: "walk-in shelter in Manhattan" → results exclude referral-only shelters

---

## Gap 11: Required Documents on Service Cards

**Priority: Medium** · **Source:** IMPL_PLAN 7b
**Effort:** 2 hours

YourPeer shows "Bring state ID and proof of address." Prevents wasted trips.

### Tasks

- **11.1** Add lateral join for `required_documents` table to `_BASE_QUERY` in `query_templates.py`
- **11.2** Add `required_documents: Optional[List[str]]` to `ServiceCard` model
- **11.3** Populate in `format_service_card()`, filter out `None` and `'None'` values
- **11.4** Add `required_documents?: string[]` to frontend `ServiceResult` type
- **11.5** Render on `service-card.tsx`: `📄 Bring: State ID, proof of address` when present
- **11.6** Tests: service with/without required docs

---

## Gap 12: Languages Spoken on Service Cards

**Priority: Medium** · **Source:** IMPL_PLAN 7c
**Effort:** 2 hours

Critical for non-English speakers deciding which service to visit.

### Tasks

- **12.1** Add lateral join for `languages` + `service_languages` tables to `_BASE_QUERY`
- **12.2** Add `languages: Optional[List[str]]` to `ServiceCard` model
- **12.3** Populate in `format_service_card()`
- **12.4** Add `languages?: string[]` to frontend `ServiceResult` type
- **12.5** Render on `service-card.tsx`: `🗣️ English, Spanish, Mandarin` when present and not just English
- **12.6** Tests: service with/without language data

---

## Gap 13: Phone Extensions

**Priority: Medium** · **Source:** IMPL_PLAN 7d, AUDIT 3.3
**Effort:** 30 minutes

Users calling a large hospital without the extension can't reach the right department.

### Tasks

- **13.1** Update `best_phone` lateral join in `query_templates.py` to include `ph.extension`
- **13.2** Update `format_service_card()` to format as "212-555-1234 ext. 456" when extension present
- **13.3** Update `tel:` link construction on frontend to include extension (if supported) or show extension text
- **13.4** Tests: phone with/without extension

---

## Gap 14: Stale Data Warning

**Priority: Medium** · **Source:** AUDIT 3.4
**Effort:** 1 hour

Service cards show "Validated 1 year ago" in gray text, but no explicit warning. Users may visit services that no longer exist.

### Tasks

- **14.1** Update `ValidatedBadge` in `service-card.tsx`: when `diffDays > 180` or date is null, show `⚠️ Not recently verified — call ahead` in amber/warning color
- **14.2** When `last_validated_at` is null, show `⚠️ Unverified — call to confirm` instead of hiding the badge entirely
- **14.3** Tests: snapshot tests for each time bracket

---

## Gap 15: Hours for Specific Days

**Priority: Low** · **Source:** AUDIT 5.5
**Effort:** 2 hours

Users can only see today's hours. "Are they open Saturday?" can't be answered. The `holiday_schedules` table has all 7 days.

### Tasks

- **15.1** Add "are they open Saturday/Sunday/tomorrow" patterns to `post_results.py` classification
- **15.2** Query `holiday_schedules` for the requested weekday and return hours for specific services from `_last_results`
- **15.3** Format response: "Service Name is open Monday from 9 AM to 5 PM" or "No schedule data for that day — call to confirm"
- **15.4** Tests: "is the first one open Saturday?" → correct hours or "call to confirm"

---

## Gap 16: Auto-Execute for High-Urgency Queries

**Priority: Low** · **Source:** AUDIT 6.1
**Effort:** 2 hours

When urgency=high and all slots filled, the confirmation step adds friction. "I need shelter in Brooklyn tonight" shouldn't require an extra tap.

### Tasks

- **16.1** In `chatbot.py`, when `urgency == "high"` and `is_enough_to_answer(merged)`, skip `_pending_confirmation` and go straight to `_execute_and_respond()`
- **16.2** Add a note in the response: "I searched right away since this sounds urgent."
- **16.3** Guard: only auto-execute when BOTH service_type AND location are present (don't skip on incomplete info)
- **16.4** Tests: "I need shelter in Brooklyn tonight" → results immediately, "I need shelter" (no location) → still asks for location

---

## Implementation Sequence

### Immediate (< 2 hours total)

| Gap | Task | Effort |
|---|---|---|
| 9 | Add "Advocates / Legal Aid" to legal template | 5 min |
| 13 | Phone extensions | 30 min |
| 14 | Stale data warning | 1 hour |

### Phase 7A — High Priority (~10 days)

| Gap | Task | Effort |
|---|---|---|
| 3 | Org name search | 3–4 days |
| 4 | Eligibility display | 2–3 days |
| 2 | Sub-category display in also_available | 1–2 days |
| 1 | Review highlights | 2–3 days |

### Phase 7B — Medium Priority (~6 days)

| Gap | Task | Effort |
|---|---|---|
| 11 | Required documents | 2 hours |
| 12 | Languages spoken | 2 hours |
| 10 | Requirement filter ("walk-in only") | 3 hours |
| 5 | Accessibility info (verify PHASE3_SPEC wiring) | 1 day |
| 7 | Pagination / "show more" | 2 days |
| 6 | Location-specific feedback | 3 days |

### Phase 8 — Low Priority (~4 days)

| Gap | Task | Effort |
|---|---|---|
| 8 | Sort options | 2 days |
| 15 | Hours for specific days | 2 hours |
| 16 | Auto-execute for urgent queries | 2 hours |
