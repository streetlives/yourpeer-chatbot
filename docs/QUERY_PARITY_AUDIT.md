# Parity Audit: Chatbot Query Logic vs YourPeer Production App

Date: April 15, 2026  
Source of truth: [`streetlives/yourpeer.nyc`](https://github.com/streetlives/yourpeer.nyc) (`streetlives-api-service.ts`, `common.ts`)  
API: [`streetlives/streetlives-api`](https://github.com/streetlives/streetlives-api) (Node.js, Sequelize, PostgreSQL)  
Status: **DRAFT — requires Streetlives team review before production launch**

---

## Executive Summary

The chatbot bypasses the YourPeer REST API and queries the Streetlives PostgreSQL database directly via parameterized SQL templates. This architectural decision (documented in Architecture Docs v0.3) was intentional — it eliminates hallucination risk by grounding all results in deterministic queries.

However, this means the chatbot's query logic was written independently from YourPeer's. This audit found **14 chatbot-invented behaviors** that do not exist in the YourPeer production app, **3 structural divergences** in category organization, and **6 unvalidated business rules** that were assumed rather than derived from YourPeer, DYCD policy, or data steward guidance.

**No parity audit was previously conducted against YourPeer's source code.** Prior audits validated taxonomy names against the DB schema, but never compared the chatbot's query construction, filtering, sorting, or enrichment logic against what YourPeer actually does.

---

## How YourPeer Queries Work

YourPeer (`streetlives-api-service.ts`) uses a two-step process:

1. **Taxonomy resolution**: `getTaxonomies()` fetches the full taxonomy tree from `/taxonomy`, then filters to the relevant taxonomy IDs based on the selected category and sub-filter (e.g., "shelters-housing" + "family" → Families taxonomy ID).

2. **API call**: `fetchLocationsData()` sends the taxonomy IDs plus optional filters (`age`, `openAt`, `referralRequired`, `membership`, `sortBy`, `latitude`/`longitude`, `searchString`) to `GET /locations`.

The API server handles all eligibility filtering, pagination, and sorting. The client does **zero** taxonomy enrichment, description filtering, or population-based boosting.

### YourPeer Query Parameters

| Parameter | Type | Used by |
|-----------|------|---------|
| `taxonomyId` | comma-separated UUIDs | All categories |
| `age` | integer | Passed to API for eligibility filtering |
| `ageMin`, `ageMax` | integers | Alternative to `age` |
| `openAt` | ISO datetime | "Open now" — **excludes** closed services |
| `referralRequired` | boolean | Filter by referral requirement |
| `membership` | boolean | Filter by membership requirement |
| `searchString` | text | Free-text search (name, description) |
| `sortBy` | string | "nearby" with lat/lon |
| `latitude`, `longitude` | float | For proximity sorting |
| `occasion` | string | Always "COVID19" (schedule table) |
| `taxonomySpecificAttributes` | array | Clothing: casual vs professional |
| `pageNumber`, `pageSize` | integers | Pagination |
| `locationFieldsOnly` | boolean | Simplified response |

---

## Per-Category Comparison

### Food

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Food` (parent ID) | `food, food pantry, food benefits, mobile pantry, mobile food truck, mobile market, food delivery / meals on wheels, soup kitchen, mobile soup kitchen, brown bag, farmer's markets` | ⚠️ Equivalent if API expands parent to children |
| Soup kitchen filter | Child taxonomy `Soup Kitchen` | Taxonomy narrowing: `["soup kitchen", "mobile soup kitchen"]` | ✅ Equivalent |
| Pantry filter | Child taxonomy `Food Pantry` | Taxonomy narrowing: `["food pantry", "mobile pantry"]` | ✅ Equivalent |
| Age filtering | `?age=N` query param | SQL `FILTER_BY_AGE_ELIGIBILITY` | ✅ Equivalent mechanism |
| Open-now | `?openAt=ISO` — **excludes** closed | SQL ORDER BY — **sorts** open first | ⚠️ Different behavior |
| Description filter | None | Optional `~*` regex when `service_detail` set | 🆕 Novel |

### Shelter

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Shelter` (parent ID only) | `shelter, TIL, supportive housing, housing lottery, veterans short-term housing, warming center, safe haven` + always: `youth, lgbtq young adult` | ⚠️ Chatbot includes many more taxonomies |
| Youth sub-filter | Same as default (`Shelter` parent) | N/A — `youth` always in taxonomy list | ⚠️ Different mechanism |
| Family sub-filter | Child taxonomy `Families` only | Adds `families` to list (doesn't replace) | ❌ Different — YP narrows, chatbot widens |
| Single sub-filter | Child taxonomy `Single Adult` only | Adds `single adult` to list | ❌ Different — YP narrows, chatbot widens |
| LGBTQ enrichment | None | Adds `lgbtq young adult` always + `drop-in center, crisis` when LGBTQ | 🆕 Novel |
| DV enrichment | None | Adds `crisis, drop-in center` when DV survivor | 🆕 Novel |
| Senior enrichment | None | Adds `senior` when age ≥ 62 | 🆕 Novel |
| Age filtering | `?age=N` query param | SQL `FILTER_BY_AGE_ELIGIBILITY` | ✅ Equivalent |
| Gender filtering | None | SQL `FILTER_BY_GENDER_ELIGIBILITY` | 🆕 Novel |

**Critical divergence**: YourPeer's shelter sub-filters (Family, Single) **replace** the taxonomy — showing ONLY family shelters or ONLY single-adult shelters. The chatbot **adds** these to the list alongside the base taxonomies, showing family shelters mixed with all other shelters. This means the chatbot always returns a broader result set than YourPeer for sub-filtered searches.

### Clothing

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Clothing` (parent ID) | `clothing, clothing pantry, interview-ready clothing, professional clothing, coat drive, thrift shop` | ⚠️ Equivalent if API expands |
| Casual/Professional | `taxonomySpecificAttributes` param | Not implemented | ❌ Missing feature |

### Personal Care

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Personal Care` (parent ID) | `personal care, shower, laundry, toiletries, hygiene, haircut, restrooms` | ⚠️ Equivalent if API expands |
| Amenity sub-filter | Specific child taxonomy IDs (Shower, Laundry, etc.) | Taxonomy narrowing: replaces with specific child | ✅ Equivalent |

### Health Care / Medical

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Health` parent + ALL children | `health, general health, crisis` | ❌ Chatbot has fewer taxonomies |
| Mental health filter | Child taxonomy `Mental Health` only | Separate `mental_health` template with 4 taxonomies | ⚠️ Structural divergence |
| Description filter | None | `~*` regex for dental, vision, HIV, etc. | 🆕 Novel |

**Critical divergence**: YourPeer includes ALL children of the Health taxonomy (which may include Mental Health, General Health, Crisis, and others). The chatbot only lists 3. If new health sub-taxonomies are added to the DB, YourPeer shows them automatically; the chatbot does not.

### Mental Health

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Sub-filter of Health (not a separate category) | Separate top-level template | ⚠️ Structural divergence |
| Default taxonomy | `Mental Health` child of Health | `mental health, substance use treatment, residential recovery, support groups` | ⚠️ Chatbot includes more |
| Substance use narrowing | None (just Mental Health taxonomy) | Taxonomy narrowing for detox, rehab, inpatient, etc. | 🆕 Novel |

### Legal

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Child of `Other service` (via `OTHER_PARAM_LEGAL_VALUE`) | Separate top-level template | ⚠️ Structural divergence |
| Default taxonomy | `Legal Services` (child of Other service) | `legal services, immigration services` | ⚠️ Chatbot adds immigration |
| Description filter | None | `~*` regex for immigration, asylum, DV, etc. | 🆕 Novel |

### Employment

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Child of `Other service` (via `OTHER_PARAM_EMPLOYMENT_VALUE`) | Separate top-level template | ⚠️ Structural divergence |
| Default taxonomy | `Employment` (child of Other service) | `employment, internship` | ⚠️ Chatbot adds internship |

### Housing Assistance

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category | Does not exist | `other service, benefits, case workers, referral, housing lottery` + required description filter | 🆕 Entirely novel category |

### Other

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Other service` parent + ALL children | 24 explicitly listed taxonomy names | ⚠️ Equivalent if API expands |
| Legal sub-filter | Child `Legal Services` | Separate template (see Legal above) | ⚠️ Structural divergence |
| Employment sub-filter | Child `Employment` | Separate template (see Employment above) | ⚠️ Structural divergence |

---

## Chatbot-Only Features

These behaviors exist only in the chatbot and have no equivalent in YourPeer:

### Validated (equivalent to API behavior)

| Feature | Status | Notes |
|---------|--------|-------|
| Age eligibility SQL filter | ✅ Validated | Reimplements API server-side logic in SQL |
| PostGIS proximity search | ✅ Validated | Reimplements API's `sortBy=nearby` |
| Taxonomy narrowing (food, personal care) | ✅ Validated | Matches YourPeer's sub-filter behavior |

### Novel (not in YourPeer, needs validation)

| Feature | Risk | Validation needed |
|---------|------|-------------------|
| Description filtering (85 patterns) | Medium | Test patterns against live DB — false negatives possible |
| Taxonomy narrowing (substance use) | Low | Verify DB taxonomy tags for detox/rehab services |
| Population-based query boosts | Low | Review boost patterns with data stewards |
| Gender eligibility filtering | Medium | Verify DB eligibility data completeness |
| Freshness tier ranking | Low | Confirm 90-day threshold with data stewards |
| Open-now sorting (vs YourPeer's exclude) | Medium | Decide: should we match YourPeer (exclude) or keep sort? |
| Housing assistance template | Medium | Verify description_pattern against live DB |
| DV shelter enrichment (crisis, drop-in) | Medium | Verify Safe Horizon taxonomy tags in DB |
| Co-located service display | Low | Novel feature, no YourPeer equivalent |

### Unvalidated Business Rules (highest priority)

| Rule | Code location | Risk | Action required |
|------|--------------|------|-----------------|
| `youth` taxonomy always included in shelter | `rag/__init__.py:176` | **High** — was `age < 18`, now always | Verify with DYCD: what age range defines "youth"? |
| `senior` taxonomy added at age ≥ 62 | `rag/__init__.py:183` | Medium | Verify: is 62 the correct threshold? |
| `families` added for `with_children` | `rag/__init__.py:169` | Medium | YourPeer NARROWS to families; chatbot ADDS — which is correct? |
| `single adult` added for `alone` | `rag/__init__.py:172` | Medium | Same question: narrow or add? |
| `lgbtq young adult` always included | `rag/__init__.py:188` | Low | Reasonable safety measure — but verify it doesn't clutter results |
| `crisis` + `drop-in center` for LGBTQ/DV | `rag/__init__.py:192-205` | Medium | Verify taxonomy tags on Safe Horizon, Ali Forney in DB |

---

## Structural Divergences

The chatbot has 10 service categories. YourPeer has 7 categories with sub-filters:

| Chatbot category | YourPeer equivalent |
|-----------------|-------------------|
| food | food |
| shelter | shelters-housing |
| clothing | clothing |
| personal_care | personal-care |
| medical | health-care (parent + all children) |
| mental_health | health-care → mental health sub-filter |
| legal | other → legal sub-filter |
| employment | other → employment sub-filter |
| housing_assistance | *(does not exist)* |
| other | other (minus legal, employment) |

**Impact**: The chatbot treats mental_health, legal, and employment as independent categories with their own taxonomy lists. YourPeer treats them as sub-filters of Health and Other respectively. This means:

- A chatbot "mental health" search uses 4 taxonomies (mental health, substance use treatment, residential recovery, support groups). A YourPeer mental health search uses 1 (Mental Health child of Health).
- A chatbot "legal" search uses 2 taxonomies. A YourPeer legal search uses 1 (Legal Services child of Other service).
- The chatbot's broader taxonomy lists may return MORE results than YourPeer for the same query.

**Decision needed**: Are the broader results intentional (better recall for conversational search) or accidental (should match YourPeer exactly)?

---

## Taxonomy Drift Risk

YourPeer sends parent taxonomy IDs to the API, which expands to all children automatically. The chatbot lists all child taxonomy names explicitly in SQL. If a new taxonomy is added to the DB (e.g., a new child of "Food"), YourPeer includes it immediately; the chatbot does not until the template is updated.

**Mitigation**: Add a CI check or periodic script that compares chatbot taxonomy lists against the live DB taxonomy tree and alerts on drift.

---

## Recommendations

### Before Production Launch (P0)

1. **Validate shelter enrichment rules** with DYCD policy and data stewards — especially the `youth` always-include, `families` add-vs-narrow, and `senior` age threshold
2. **Verify taxonomy tags** for key locations (Covenant House, Ali Forney, Safe Horizon, DHS PATH) against actual DB data
3. **Decide on shelter sub-filter behavior**: should "families" NARROW to family-only shelters (like YourPeer) or ADD family shelters to the mix (current chatbot behavior)?
4. **Decide on open-now behavior**: YourPeer excludes closed services; chatbot sorts them lower. Which is correct for the conversational context?

### Before Scale (P1)

5. **Add taxonomy drift detection** — automated comparison against DB taxonomy tree
6. **Review description filter patterns** against live DB — verify each pattern matches ≥1 service
7. **Consider using the Streetlives API** instead of direct SQL for query execution — this would automatically inherit any API-level filtering improvements and eliminate the parity problem entirely

### Future (P2)

8. **Reconcile category structure** — decide whether mental_health, legal, employment should remain separate or map to YourPeer's parent/sub-filter model
9. **Document each intentional divergence** with rationale and data steward sign-off
