# Parity Audit: Chatbot Query Logic vs YourPeer Production App

Date: April 16, 2026 (DB-verified update)
Source of truth: [`streetlives/yourpeer.nyc`](https://github.com/streetlives/yourpeer.nyc) (`streetlives-api-service.ts`, `common.ts`, `get-side-panel-component-data.ts`)
API: [`streetlives/streetlives-api`](https://github.com/streetlives/streetlives-api) (Node.js, Sequelize, PostgreSQL)
Status: **DRAFT — active remediation in progress; DB verification complete**

---

## Executive Summary

The chatbot bypasses the YourPeer REST API and queries the Streetlives PostgreSQL database directly via parameterized SQL templates. This architectural decision (documented in Architecture Docs v0.3) was intentional — it eliminates hallucination risk by grounding all results in deterministic queries.

However, this means the chatbot's query logic was written independently from YourPeer's. This audit compared the chatbot's full query construction, filtering, sorting, and enrichment logic against what YourPeer actually does, using a line-by-line review of both codebases.

### Findings Summary

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 Critical divergence | 4 | Shelter sub-filter logic, health-care taxonomy scope, open-now semantics, category structure |
| 🟠 Novel behavior (needs validation) | 14 | Chatbot-invented features not in YourPeer |
| 🟡 Unvalidated business rules | 6 | Assumptions requiring data steward sign-off |
| 🟢 Validated equivalent | 5 | Chatbot correctly reimplements YourPeer behavior |
| ℹ️ Informational | 4 | Differences that may be intentional improvements |
| ⚠️ YourPeer bugs/gaps | 2 | Issues found in YourPeer itself during this audit |

**No parity audit was previously conducted against YourPeer's source code.** Prior audits validated taxonomy names against the DB schema, but never compared the chatbot's query construction, filtering, sorting, or enrichment logic against what YourPeer actually does.

---

## April 16, 2026 — DB Verification & Remediation Update

A prod DB query was run to verify taxonomy tree parentage and service counts. Several audit assumptions were overturned and remediation work was completed.

### Key DB Findings

| Assumption | Status | Action |
|-----------|--------|--------|
| `Crisis` is a Health child | ❌ WRONG — it's a Shelter child (13 services) | Removed from medical template |
| `Drop-in Center` is ambiguous | ✅ Confirmed Shelter child (6 services) | Existing shelter enrichment correct |
| `Referral` is ambiguous | ✅ Confirmed Shelter child (6 services) | Informational |
| `Advocates / Legal Aid` has unseen services | ❌ WRONG — 0 services tagged, phantom | Concern removed from audit |
| `Harm Reduction` / `Needle Exchange` / `Overdose Prevention` are Health children | ❌ WRONG — none exist as DB taxonomies | Removed from display whitelist |
| Health has many children | ❌ WRONG — only 4 children: General Health (48), Mental Health (128), Substance Use Treatment (11), Support Groups (8) | Medical template updated |
| Shelter children have reasonable service counts | ❌ WRONG — Families=3, LGBTQ Young Adult=2, Senior=2, Youth=4, Veterans=1 | Narrow-with-parent-preservation adopted |

### Completed Remediation

1. **Removed `housing_assistance` template** — YourPeer has no equivalent. Keywords (rental assistance, Section 8, eviction prevention) now route to `other` template, which is what YourPeer does via "Other service" taxonomy tree. Full details in audit diff Apr 15, 2026.

2. **Shelter default taxonomy expanded** from 7 to 18 taxonomies (all non-zero Shelter children) — matches YourPeer's parent-to-child API expansion behavior. Initial expansion to 13 (population-specific children); later expanded to 18 after DB verification revealed 5 service-type children were missing (item 8).

3. **Shelter narrowing with parent preservation** — `family_status=with_children` now produces `["families", "shelter"]` instead of strict YourPeer `["families"]`. Rationale: DB verification showed Families child has only 3 services; strict narrowing would regularly return 0 results. The 18 services tagged only with parent `Shelter` are preserved. Same pattern for `alone` → `["single adult", "shelter"]`.

4. **Shelter safety enrichments (5 additive rules)** — novel divergences from YourPeer, preserved because narrowing strips out population-specific children the user plausibly qualifies for:
   - Age 16–24 → add `youth` (Covenant House, Ali Forney NJ)
   - LGBTQ/trans/nonbinary → add `drop-in center, crisis, lgbtq young adult`
   - Age ≥ 62 → add `senior`
   - Veteran → add `veterans, veterans short-term housing`
   - Pregnant + `alone` → override to families narrow (prenatal services at family shelters)
   - DV survivor → add `drop-in center, crisis` (Safe Horizon)

5. **Medical template bug fix** — removed `crisis` (was a Shelter child, not Health). Added `substance use treatment` and `support groups` (confirmed Health children). Now matches the effective output of YourPeer's health-care view (API returns all Health children, client strips Mental Health).

6. **Display whitelist cleanup** — removed phantom taxonomies `Harm Reduction`, `Needle Exchange`, `Overdose Prevention` from `_DISPLAY_CATEGORIES`. They never existed in the DB.

7. **Open-now: sort-only semantics (intentional divergence)** — chatbot sorts open services first but never excludes closed services. YourPeer uses exclude-semantics via `openAt`. Decision rationale: sparse schedule coverage (40-80% walk-in, near-zero others) makes exclusion dangerous for this population. SQL `_OPEN_NOW_RANK` removed from ORDER BY (Python `_sort_open_first` is single source of truth). `FILTER_BY_OPEN_NOW` removed from food template optional_filters. Filter response handlers updated with `displayed_count` phrasing and `_DISPLAY_PAGE_SIZE` pagination cap.

8. **Shelter default list expanded from 13 to 18** — DB verification of Covenant House, Ali Forney, and Safe Horizon revealed 5 Shelter children with non-zero service counts were missing: `crisis` (13), `drop-in center` (6), `referral` (6), `assessment` (1), `residential recovery` (2). Safe Horizon's "Shelter Placement" services (tagged `Referral`) were invisible to ALL shelter queries. Now all non-zero Shelter children are in the default list.

9. **Description filter pattern validation** — ran all 79 description filter regex patterns against prod `services.description` (April 16, 2026). Found 1 dead pattern (`dialysis services`: 0 matches — removed) and 2 taxonomy narrowing entries with dead `supportive housing` (0 services tagged — removed from `sober living` and `halfway houses`). Also validated all 18 taxonomy narrowing entries have ≥1 match. 7 duplicate pattern pairs documented as intentional user-language synonyms. 7 very broad patterns (>200 matches) documented as functional but worth monitoring.

10. **Clothing casual/professional filter** — implemented via the DB's `taxonomy_specific_attributes` system, matching YourPeer's `taxonomySpecificAttributes` API parameter. New `FILTER_BY_CLOTHING_OCCASION` SQL filter uses JSONB `@>` containment operator on `service_taxonomy_specific_attributes."values"`. DB verified: `clothingOccasion` attribute has 62 Everyday and 28 Job Interview services (much better than taxonomy-only approach with 2 professional services). Slot extractor updated with 10 multi-word phrases ("interview clothes", "professional clothing", etc.) that resolve the "interview" keyword conflict with employment — multi-word phrases win in longest-first sort, blocking the single-word "interview" employment keyword via span overlap. Bonus DB attributes found for future features: `tgncClothing` (36 TGNC-friendly services), `wearerAge` (age/gender targeting), `hasHivNutrition` (23 HIV nutrition services).

### Known Intentional Divergences from YourPeer

1. **Shelter narrowing preserves parent** — YourPeer narrows to `["Families"]` / `["Single Adult"]` only. Chatbot adds parent `shelter` for recall. Justification: tiny DB counts + lack of pagination in chatbot.

2. **Shelter safety enrichments** — YourPeer has none. Chatbot adds 5 population-specific re-additions on top of narrowing. Justification: narrowing-by-family-composition strips population tags the user qualifies for.

3. **Medical/Mental Health shared taxonomies** — both templates include `substance use treatment` and `support groups`. YourPeer filters Mental Health out of health-care client-side; chatbot reflects real DB parent (both are Health children) and serves them from either template. A user asking "doctor for addiction" finds them via medical; a user asking "support group" finds them via mental_health.

### Data Quality Notes (for Streetlives team)

These are DB-side inconsistencies surfaced during the audit. Not chatbot bugs — noted for the Streetlives team's taxonomy cleanup backlog:

| Taxonomy | Parent | Services | Note |
|----------|--------|----------|------|
| `Baby Supplies` | Clothing | 5 | Semantically "Other service"; chatbot's `other` template finds it by name |
| `Baby` | Personal Care | 3 | Semantically "Other service"; chatbot's `other` template finds it by name |
| `Support Groups` (duplicate) | Personal Care | 0 | Phantom duplicate — real entry is under Health |
| `Immigration Services` | Other service | 2 | YourPeer treats as legal via "other → legal" sub-filter; chatbot includes in `legal` template directly |
| `Internship` | Other service | 3 | Same pattern as Immigration Services |
| `Mobile Food Truck` | Food | 0 | Taxonomy exists but unused |
| `Pets` | Other service | 0 | Taxonomy exists but unused |
| `Supportive Housing` | Shelter | 0 | Taxonomy exists but unused |
| `Intake` | Shelter | 0 | Taxonomy exists but unused |
| `Cooling Center` | Shelter | 0 | Taxonomy exists but unused |
| `Advocates / Legal Aid` | — | 0 | Phantom — included in YourPeer's "other" client-side exclusion filter but matches zero services |
| `Harm Reduction` | — | — | Does NOT exist in DB; referenced only in chatbot's display whitelist |
| `Needle Exchange` | — | — | Does NOT exist in DB; referenced only in chatbot's display whitelist |
| `Overdose Prevention` | — | — | Does NOT exist in DB; referenced only in chatbot's display whitelist |

### Remaining P0 Items (not yet addressed)

None. All P0 items have been resolved.

### Covenant House / Ali Forney / Safe Horizon — DB Verification (Resolved Apr 16, 2026)

All three orgs exist in the DB. Taxonomy tag verification revealed that the shelter default list was missing 5 Shelter children with non-zero service counts:

| Org | Service | Taxonomy tag | Was discoverable? | Now discoverable? |
|---|---|---|---|---|
| Covenant House NYC | Emergency Bed Placement | `Crisis` | ❌ default, ✅ DV/LGBTQ only | ✅ always |
| Ali Forney Center | LGBTQIA2S+ Young Adult Overnight | `LGBTQ Young Adult` | ✅ always | ✅ always |
| Safe Horizon (LES + Harlem) | Shelter Placement | `Referral` | ❌ **NOWHERE** | ✅ always |
| Safe Horizon (Harlem) | Day Sleeping Room | `Drop-in Center` | ❌ default, ✅ DV/LGBTQ only | ✅ always |
| Safe Horizon (Queens) | Respite and Community Bed | `Shelter` | ✅ always | ✅ always |

**Fix**: expanded shelter default taxonomy list from 13 to 18 entries, adding all 5 missing non-zero Shelter children: `crisis` (13 services), `drop-in center` (6), `referral` (6), `assessment` (1), `residential recovery` (2). Only `Cooling Center` (0 services) and `Intake` (0 services) are omitted.

**Impact**: Safe Horizon's "Shelter Placement" services were previously invisible to ALL shelter queries. Now discoverable in every default shelter query. Covenant House's "Emergency Bed Placement" was previously only visible when DV/LGBTQ safety enrichments fired; now visible in all shelter queries.

**Data quality note for Streetlives team**: Ali Forney Center's "Drop-in Space" service is tagged `Other service`, not `Drop-in Center`. It's a drop-in service at a youth shelter — likely a tagging oversight. Similarly, Safe Horizon's "Shelter Placement" services at LES and Harlem are tagged `Referral` rather than `Shelter` — functional but non-obvious.

**Known limitation — Referral under narrow**: DV safety enrichment adds `drop-in center` + `crisis` but NOT `referral`. When family-composition narrowing fires (e.g., DV survivor with children → `['families', 'shelter', 'drop-in center', 'crisis']`), Safe Horizon's "Shelter Placement" services (tagged `{Referral}`) become invisible. These services are still findable in DEFAULT shelter queries (no narrowing) because `referral` is in the full 18-taxonomy list. The gap only affects narrowed DV queries. If this becomes P0: add `referral` to the DV safety enrichment list. Documented in `test_audit_regression.py::TestDBVerifiedOrgDiscoverability::test_safe_horizon_shelter_placement_under_dv_narrow`.

### Open-Now Behavior — Intentional Divergence (Resolved Apr 16, 2026)

**Decision**: chatbot keeps sort-only semantics. YourPeer excludes closed services; chatbot ranks them lower but does not exclude. Documented divergence.

**Rationale**: DB schedule coverage is uneven — walk-in services (Soup Kitchen, Shower, Clothing Pantry, Food Pantry) have 40–80% coverage; everything else has near-zero. YourPeer's `openAt` filter on a medical or legal query would hide the majority of services. For a population where "the chatbot says there's nothing available" translates to "I give up", the cost of false exclusion outweighs the UX benefit of filtering to actually-open services.

**How it works** (as of Apr 16, 2026 cleanup):

- Every service query joins `holiday_schedules` with a `LEFT JOIN` keyed on `EXTRACT(ISODOW FROM CURRENT_DATE)`. This populates `today_opens` / `today_closes` columns; services without schedule rows get NULL, not excluded.
- `is_open` is computed per result in Python (`_compute_schedule_status`) from `today_opens`/`today_closes` using application-server `datetime.now().time()`. Handles overnight schedules. Produces `"open" | "closed" | None`.
- Results are sorted open-first via `_sort_open_first()` in `query_executor.py` — **the single source of truth for open-status sorting**. Three buckets: `open` (0) → `closed` (1) → unknown (2). Python stable sort preserves within-bucket order (freshness, distance, name).
- The SQL `_OPEN_NOW_RANK` constant is retained in `query_templates.py` for documentation but is NOT in `_BASE_ORDER_PARTS`. Prior revisions had SQL-level and Python-level ranks both running; Python always overrode SQL, and they differed in treatment of unknown-schedule services (SQL: unknown=closed=1; Python: unknown=2, closed=1). Removed SQL-side for single-source-of-truth — also drops one CASE expression from every query.
- `FILTER_BY_OPEN_NOW` is defined but intentionally unused — retained for documentation and potential future use if DB schedule coverage improves substantially.

**User-initiated "open now" queries** (`_handle_filter_open`) filter the cached result pool (up to `_FETCH_LIMIT=25` services — larger than what the user sees on screen). This was already in place as of the audit. Phrasing and pagination were polished Apr 16, 2026:

- Count denominator uses `displayed_count` from session (typically 5), not `len(services)` (up to 25). Fixes the "3 of 25 are open" UX issue where users saw 5 cards but were told about a 25-result pool they hadn't seen.
- When `filter_count > displayed_count` (the filter found more open services than the user has seen), response avoids ratio phrasing and uses `"I found N open services"` — honest about the situation.
- Returned services capped at `_DISPLAY_PAGE_SIZE=5` to preserve the 5-per-page UI model. If truncated, response says "Here are the first N". Users can use "Show all results" QR to see everything including un-shown open ones.
- Same treatment applied to `_handle_filter_free` and `_handle_filter_subcategory` for consistency.

**Known limitation**: for sparse-coverage categories (legal, medical), if the 25 fetched all lack schedule data, services 26+ in the DB with schedule data are invisible to the filter. Mitigation options (deferred to P2): increase `_FETCH_LIMIT`, or add a re-query fallback when cached-pool filter returns empty.

### Remaining P1/P2 Items

- Pagination (chatbot caps at 10, YourPeer defaults to 20 with paging) — actually already improved: chatbot fetches 25 and paginates display in 5-card chunks via `_displayed_count` session state. Still diverges from YourPeer's behavior, but the gap is narrower than the original audit framing.
- Proximity sort + search interaction (YourPeer disables when search active)
- Requirement filter granularity (chatbot has single flag vs YourPeer's 3 booleans)
- Free-text `searchString` parameter (not implemented in chatbot)
- Drift detection CI check (documented as needed, not implemented)
- **Sparse-category open-now gap**: for categories with near-zero schedule coverage (legal, medical), the 25 services in the cached pool may all lack schedule data, leaving filter responses unable to surface open services even when some exist deeper in the DB. Mitigation options: (a) raise `_FETCH_LIMIT` for specific categories, (b) add re-query fallback when cached filter returns empty. Both have costs — deferred pending usage signal.
- **`tgncClothing` attribute boost** — DB has 36 services marked as TGNC-friendly clothing. Could add a sort boost for LGBTQ/trans/nonbinary users in clothing queries (same pattern as the existing veteran/LGBTQ taxonomy boosts).
- **`wearerAge` attribute filter** — DB has age/gender targeting (men, women, adults, children, etc.). Could filter clothing results by user demographics.
- **`hasHivNutrition` attribute filter** — 23 food services marked as HIV-specific nutrition. Could surface for users who disclose HIV status.
- **Referral not re-added under narrow for DV survivors** — Safe Horizon's "Shelter Placement" is tagged `{Referral}`. Under narrowed shelter queries (family_status set), `referral` is stripped. DV enrichment adds `crisis` + `drop-in center` but not `referral`. This means DV survivors with family_status set can't find Safe Horizon's shelter placement service via the shelter query. Possible fix: add `referral` to DV safety enrichment.

---

## How YourPeer Queries Work

YourPeer (`streetlives-api-service.ts`) uses a two-step process:

1. **Taxonomy resolution**: `getTaxonomies()` fetches the full taxonomy tree from `/taxonomy`, then filters to the relevant taxonomy IDs based on the selected category and sub-filter (e.g., "shelters-housing" + "families" → Families taxonomy ID).

2. **API call**: `fetchLocationsData()` sends the taxonomy IDs plus optional filters (`age`, `openAt`, `referralRequired`, `membership`, `sortBy`, `latitude`/`longitude`, `searchString`) to `GET /locations`.

3. **Age-range injection** (shelter only): `get-side-panel-component-data.ts` adds `ageMin`/`ageMax` params for youth (16–24) and single adult (18–99) shelter sub-filters.

The API server handles all eligibility filtering, pagination, and sorting. The client does **zero** taxonomy enrichment, description filtering, or population-based boosting.

### YourPeer Query Parameters

| Parameter | Type | Used by |
|-----------|------|---------|
| `taxonomyId` | comma-separated UUIDs | All categories |
| `age` | integer | Passed to API for eligibility filtering |
| `ageMin`, `ageMax` | integers | Shelter youth (16–24) and single adult (18–99) sub-filters |
| `openAt` | ISO datetime | "Open now" — **excludes** closed services |
| `referralRequired` | boolean | Filter by referral requirement |
| `membership` | boolean | Filter by membership requirement |
| `searchString` | text | Free-text search (name, description) |
| `sortBy` | string | "nearby" with lat/lon; **disabled when `searchString` is set** |
| `latitude`, `longitude` | float | For proximity sorting |
| `occasion` | string | Always "COVID19" (schedule table) |
| `taxonomySpecificAttributes` | array | Clothing: casual vs professional |
| `pageNumber`, `pageSize` | integers | Pagination (default page size: 20) |
| `locationFieldsOnly` | boolean | Simplified response |

---

## Correction: Category Count

The previous version of this audit stated "YourPeer has 7 categories with sub-filters." This is incorrect. YourPeer's `CATEGORIES` array in `common.ts` lists **9 top-level categories**:

```
shelters-housing, food, clothing, personal-care, health-care,
mental-health, legal-services, employment, other
```

Each has its own route and entry in `CATEGORY_TO_TAXONOMY_NAME_MAP`. However, three of these (mental-health, legal-services, employment) have **no case in `getTaxonomies()`'s switch statement** — see "YourPeer Bugs/Gaps" section below.

Originally the chatbot had **10 categories** (adding `housing_assistance`, which had no YourPeer equivalent). `housing_assistance` was retired in the April 15, 2026 audit — housing-program keywords (rent, eviction, Section 8) now route to `other`, matching YourPeer's own coverage. The table below reflects the original 10-category state for historical reference; today there are 9 active chatbot categories.

### Category Mapping

| Chatbot category | YourPeer category | YP taxonomy name | getTaxonomies case? |
|-----------------|-------------------|-----------------|---------------------|
| food | food | Food | ✅ Yes |
| shelter | shelters-housing | Shelter | ✅ Yes |
| clothing | clothing | Clothing | ✅ Yes |
| personal_care | personal-care | Personal Care | ✅ Yes |
| medical | health-care | Health | ✅ Yes |
| mental_health | mental-health | Mental Health | ❌ No (works only as health-care sub-filter) |
| legal | legal-services | Legal Services | ❌ No (works only as other sub-filter) |
| employment | employment | Employment | ❌ No (works only as other sub-filter) |
| ~~housing_assistance~~ | ~~*(does not exist)*~~ | ~~*(novel)*~~ | ~~—~~ *(retired April 15, 2026 — rolled into `other`)* |
| other | other | Other service | ✅ Yes |

---

## Per-Category Comparison

### Food

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Food` (parent ID) | 11 explicit child names | ⚠️ Equivalent if API expands parent to children |
| Soup kitchen filter | Child taxonomy `Soup Kitchen` | Taxonomy narrowing: `["soup kitchen", "mobile soup kitchen"]` | ✅ Equivalent |
| Pantry filter | Child taxonomy `Food Pantry` | Taxonomy narrowing: `["food pantry", "mobile pantry"]` | ✅ Equivalent |
| Age filtering | `?age=N` query param | SQL `FILTER_BY_AGE_ELIGIBILITY` | ✅ Equivalent mechanism |
| Open-now | `?openAt=ISO` — **excludes** closed | SQL ORDER BY — **sorts** open first | 🔴 Different behavior |
| Description filter | None | Optional `~*` regex when `service_detail` set | 🆕 Novel |

### Shelter

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Shelter` (parent ID only) | `shelter, TIL, supportive housing, housing lottery, veterans short-term housing, warming center, safe haven` + always: `youth, lgbtq young adult` | ⚠️ Chatbot includes many more taxonomies |
| Youth sub-filter | Parent `Shelter` taxonomy + `ageMin=16, ageMax=24` | Adds `youth` to taxonomy list (always) + age eligibility filter | 🔴 **Different mechanism** |
| Family sub-filter | Child taxonomy `Families` only (replaces parent) | Adds `families` to list (doesn't replace) | 🔴 **Different** — YP narrows, chatbot widens |
| Single sub-filter | Child taxonomy `Single Adult` only (replaces parent) + `ageMin=18, ageMax=99` | Adds `single adult` to list | 🔴 **Different** — YP narrows + age range, chatbot widens without age range |
| LGBTQ enrichment | None | Adds `lgbtq young adult` always + `drop-in center, crisis` when LGBTQ | 🆕 Novel |
| DV enrichment | None | Adds `crisis, drop-in center` when DV survivor | 🆕 Novel |
| Senior enrichment | None | Adds `senior` when age ≥ 62 | 🆕 Novel |
| Age filtering | `?age=N` query param | SQL `FILTER_BY_AGE_ELIGIBILITY` | ✅ Equivalent |
| Gender filtering | None | SQL `FILTER_BY_GENDER_ELIGIBILITY` | 🆕 Novel |

**Critical divergence — shelter sub-filters**: YourPeer's shelter sub-filters (Family, Single, Youth) **replace** the taxonomy — showing ONLY that sub-category. The chatbot **adds** these to the list alongside the base taxonomies, producing a broader result set. Additionally, YourPeer injects `ageMin`/`ageMax` API params for youth (16–24) and single adult (18–99), which the chatbot does not.

**Critical divergence — youth mechanism**: YourPeer's youth filter sends the PARENT `Shelter` taxonomy (identical to the default) and narrows server-side via `ageMin=16, ageMax=24`. The chatbot adds the `youth` child taxonomy to the taxonomy list and relies on its own eligibility filter. These approaches can produce different result sets: the chatbot finds services tagged "Youth" at any age (filtered by the age eligibility check), while YourPeer finds any shelter accepting ages 16–24 regardless of taxonomy tag.

### Clothing

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Clothing` (parent ID) | 6 explicit child names | ⚠️ Equivalent if API expands |
| Casual/Professional | `taxonomySpecificAttributes` param | Not implemented | ❌ Missing feature |

### Personal Care

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Personal Care` (parent ID) | 7 explicit child names | ⚠️ Equivalent if API expands |
| Amenity sub-filter | Specific child taxonomy IDs (Shower, Laundry, etc.) | Taxonomy narrowing: replaces with specific child | ✅ Equivalent |

### Health Care / Medical

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Health` parent + **ALL children** (including Mental Health) | `health, general health, crisis` (3 only) | 🔴 Chatbot has fewer taxonomies |
| Mental health sub-filter | Child taxonomy `Mental Health` only | Separate `mental_health` template with 4 taxonomies | ⚠️ Structural divergence |
| Mental health exclusion | Client-side: `filter_services_by_name` **excludes** Mental Health services from health-care view | Not implemented — but currently irrelevant because chatbot's `medical` template doesn't include `mental health` taxonomy | ⚠️ Accidental alignment |
| Description filter | None | `~*` regex for dental, vision, HIV, etc. | 🆕 Novel |

**Critical divergence**: YourPeer includes ALL children of the Health taxonomy in its API query. It then client-side filters Mental Health services OUT of the health-care display. If new health sub-taxonomies are added to the DB, YourPeer shows them automatically; the chatbot does not.

**Mental Health exclusion detail**: In `filter_services_by_name`, YourPeer has:

```typescript
if (!(category_name === "health-care" && taxonomiesForService.has("Mental Health"))
    && service["Taxonomies"].length !== 0)
```

This explicitly filters Mental Health services OUT of the health-care view, even though the API query returns them. The chatbot achieves the same outcome accidentally by not including "mental health" in its medical taxonomy list — but this alignment is fragile and would break if "mental health" were added to the medical template.

### Mental Health

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Top-level category in CATEGORIES, but `getTaxonomies()` has no switch case (see bugs section) | Separate top-level template | ⚠️ See YourPeer bugs section |
| Via health-care sub-filter | `Mental Health` child of Health (works correctly) | N/A — separate template | ✅ Both surface mental health services |
| Default taxonomy | `Mental Health` (1 taxonomy) | `mental health, substance use treatment, residential recovery, support groups` (4 taxonomies) | ⚠️ Chatbot includes more |
| Substance use narrowing | None | Taxonomy narrowing for detox, rehab, inpatient, etc. | 🆕 Novel |

### Legal

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Top-level category in CATEGORIES, but `getTaxonomies()` has no switch case. Works via "other" sub-filter. | Separate top-level template | ⚠️ See YourPeer bugs section |
| Via other sub-filter | `Legal Services` child of Other service (works correctly) | N/A — separate template | ✅ Both surface legal services |
| Default taxonomy | `Legal Services` (1 taxonomy) | `legal services, immigration services` (2 taxonomies) | ⚠️ Chatbot adds immigration |
| Description filter | None | `~*` regex for immigration, asylum, DV, etc. | 🆕 Novel |

### Employment

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category structure | Top-level category in CATEGORIES, but `getTaxonomies()` has no switch case. Works via "other" sub-filter. | Separate top-level template | ⚠️ See YourPeer bugs section |
| Via other sub-filter | `Employment` child of Other service (works correctly) | N/A — separate template | ✅ Both surface employment services |
| Default taxonomy | `Employment` (1 taxonomy) | `employment, internship` (2 taxonomies) | ⚠️ Chatbot adds internship |

### Housing Assistance

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Category | Does not exist | `other service, benefits, case workers, referral, housing lottery` + required description filter | 🆕 Entirely novel category |

### Other

| Aspect | YourPeer | Chatbot | Match? |
|--------|----------|---------|--------|
| Default taxonomy | `Other service` parent + ALL children | 24 explicitly listed taxonomy names | ⚠️ Equivalent if API expands |
| "other" exclusion filter | Client-side: excludes services matching Health, Shelter, Food, Clothing, Personal Care, Mental Health, Legal Services, Employment, **Advocates / Legal Aid** | Not implemented — chatbot's `other` template includes everything in its taxonomy list | ⚠️ Different mechanism |
| Legal sub-filter | Child `Legal Services` | Separate template | ⚠️ Structural divergence |
| Employment sub-filter | Child `Employment` | Separate template | ⚠️ Structural divergence |

---

## Cross-Cutting Divergences

### 1. Open-Now Behavior (🔴 Critical)

| | YourPeer | Chatbot |
|---|----------|---------|
| Mechanism | `?openAt=<ISO datetime>` sent to API | `ORDER BY` clause sorting open services first |
| Effect | **Excludes** all closed services from results | **Sorts** open services to top, closed services still returned |
| User impact | User sees only open services | User sees all services, open ones first |

**Decision needed**: Should the chatbot exclude closed services (matching YourPeer) or keep the sort-only behavior (arguably better for the chatbot context where the user may want to plan ahead)?

### 2. Proximity Sorting + Search Interaction

YourPeer disables proximity sorting when a text search is active:

```typescript
if (sortBy && !search) {
    query_url += `&sortBy=${sortBy}`;
}
```

The chatbot has no equivalent — it applies proximity sorting regardless of whether description/org-name filters are active.

### 3. Pagination

| | YourPeer | Chatbot |
|---|----------|---------|
| Default page size | 20 | 10 |
| Pagination support | Full (`pageNumber`/`pageSize`) | `LIMIT :max_results` only |
| Total results visible | Unlimited (via paging) | Max 10 per query |

### 4. Requirement Filters

| | YourPeer | Chatbot |
|---|----------|---------|
| No requirements | `referralRequired=false` + `membership=false` | `FILTER_BY_NO_REQUIREMENTS` (checks membership only) |
| Referral only | `referralRequired=true` | ❌ Not supported |
| Membership only | `membership=true` | ❌ Not supported |
| Granularity | Three independent booleans | Single on/off flag |

YourPeer supports filtering FOR referral-required or membership-required services specifically (e.g., showing only services that need a referral letter). The chatbot only supports filtering OUT services with requirements.

### 5. Free-Text Search

YourPeer passes `searchString` to the API for server-side name/description matching. The chatbot has `org_name` (ILIKE pattern) and `description_pattern` (regex), but no general-purpose free-text search parameter equivalent.

### 6. "Advocates / Legal Aid" Taxonomy

`TAXONOMY_CATEGORIES` in YourPeer includes "Advocates / Legal Aid" — this taxonomy exists in the DB but is not in any chatbot template's taxonomy list. Services tagged with this taxonomy are:

- Excluded from YourPeer's "other" view (via `setIntersection` filter in `map_gogetta_to_yourpeer`)
- Invisible to the chatbot unless also tagged with another known taxonomy

**Action needed**: Query the DB to check how many services are tagged "Advocates / Legal Aid" and whether they overlap with other taxonomies.

---

## Chatbot-Only Features

### Validated (equivalent to API behavior)

| Feature | Status | Notes |
|---------|--------|-------|
| Age eligibility SQL filter | ✅ Validated | Reimplements API server-side logic in SQL |
| PostGIS proximity search | ✅ Validated | Reimplements API's `sortBy=nearby` |
| Taxonomy narrowing (food, personal care) | ✅ Validated | Matches YourPeer's sub-filter behavior |
| `occasion='COVID19'` schedule table | ✅ Validated | Matches YourPeer's `?occasion=COVID19` |
| `hidden_from_search` filter | ✅ Validated | Consistent with API behavior |

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
| Relaxed query fallback | Low | Novel; broadens search when strict returns 0 |
| Review highlights on service cards | Low | Novel; uses `location_comment_highlights` table |
| Required documents on service cards | Low | Novel; uses `required_documents` table |
| Languages spoken on service cards | Low | Novel; uses `languages` / `service_languages` tables |
| Eligibility summary on service cards | Low | Novel; aggregates eligibility rules for display |

### Unvalidated Business Rules (highest priority)

| Rule | Code location | Risk | Action required |
|------|--------------|------|-----------------|
| `youth` taxonomy always included in shelter | `rag/__init__.py:185` | **High** | YourPeer uses ageMin/ageMax (16–24) instead of taxonomy enrichment. Verify with DYCD: which approach is correct? |
| `senior` taxonomy added at age ≥ 62 | `rag/__init__.py:186` | Medium | Verify: is 62 the correct threshold? |
| `families` added for `with_children` | `rag/__init__.py:169` | **High** | YourPeer NARROWS to families (replaces parent taxonomy); chatbot ADDS — which is correct? |
| `single adult` added for `alone` | `rag/__init__.py:172` | **High** | Same question: narrow or add? YourPeer also adds ageMin=18, ageMax=99. |
| `lgbtq young adult` always included | `rag/__init__.py:192` | Low | Reasonable safety measure — but verify it doesn't clutter results |
| `crisis` + `drop-in center` for LGBTQ/DV | `rag/__init__.py:196-209` | Medium | Verify taxonomy tags on Safe Horizon, Ali Forney in DB |

---

## YourPeer Bugs/Gaps Found During Audit

### 1. `getTaxonomies()` missing switch cases for 3 categories

`getTaxonomies()` in `streetlives-api-service.ts` has a `switch (category)` with cases for `"clothing"`, `"food"`, `"health-care"`, `"other"`, `"personal-care"`, and `"shelters-housing"`. There are **no cases** for `"mental-health"`, `"legal-services"`, or `"employment"`, and no `default` case.

When these are accessed as standalone routes (not sub-filters), the function returns empty taxonomy arrays (`[]`), causing the API to return ALL locations with no taxonomy filtering.

**Impact**: Navigating directly to `/mental-health`, `/legal-services`, or `/employment` on YourPeer may return all locations. These categories work correctly only when accessed as sub-filters (e.g., `/health-care?health-care=mental-health` or `/other-services?other-services=legal-services`).

**Note for chatbot team**: The chatbot handles these categories BETTER than YourPeer by having dedicated templates. This is not a divergence to fix — it is an area where the chatbot is ahead.

### 2. Shelter youth sub-filter taxonomy inconsistency

When the youth sub-filter is selected, `getTaxonomies()` returns the PARENT `Shelter` taxonomy (identical to the default unfiltered shelter search), and `get-side-panel-component-data.ts` adds `ageMin=16, ageMax=24` to narrow server-side. This means the youth filter is entirely driven by age range — the "Youth" child taxonomy in the DB is not used for filtering. Services tagged only as "Youth" (not "Shelter") would be invisible even with the youth filter active.

---

## Taxonomy Drift Risk

YourPeer sends parent taxonomy IDs to the API, which expands to all children automatically. The chatbot lists all child taxonomy names explicitly in SQL. If a new taxonomy is added to the DB (e.g., a new child of "Food"), YourPeer includes it immediately; the chatbot does not until the template is updated.

**Specific risk for health-care**: YourPeer queries `Health` parent + ALL children. The chatbot only lists 3 of an unknown total. Any new Health children are invisible to the chatbot.

**Mitigation**: Add a CI check or periodic script that compares chatbot taxonomy lists against the live DB taxonomy tree and alerts on drift.

---

## Recommendations

### Before Production Launch (P0)

1. **Decide shelter sub-filter behavior**: Should "families" NARROW to family-only shelters (like YourPeer) or ADD family shelters to the mix (current chatbot behavior)? Same question for "single adult" and "youth".

2. **Decide shelter youth mechanism**: Should the chatbot match YourPeer's approach (use parent Shelter taxonomy + ageMin=16/ageMax=24) or keep the current taxonomy enrichment approach? Document the rationale either way.

3. **Validate shelter enrichment rules** with DYCD policy and data stewards — especially the `youth` always-include, `families` add-vs-narrow, and `senior` age threshold.

4. **Decide on open-now behavior**: YourPeer excludes closed services; chatbot sorts them lower. Which is correct for the conversational context?

5. **Verify taxonomy tags** for key locations (Covenant House, Ali Forney, Safe Horizon, DHS PATH) against actual DB data.

6. **Audit "Advocates / Legal Aid" taxonomy** — count tagged services and determine if any are invisible to the chatbot.

7. **Audit health-care taxonomy children** — query the DB for all children of "Health" and add any missing ones to the chatbot's `medical` template, or switch to a parent-ID-based approach.

### Before Scale (P1)

8. **Add taxonomy drift detection** — automated comparison against DB taxonomy tree.

9. **Review description filter patterns** against live DB — verify each pattern matches ≥1 service.

10. **Consider using the Streetlives API** instead of direct SQL for query execution — this would automatically inherit any API-level filtering improvements and eliminate the parity problem entirely.

11. **Implement clothing casual/professional filter** — the `taxonomySpecificAttributes` parameter is not yet supported.

12. **Consider pagination** — the chatbot's max 10 results may miss relevant services in dense categories.

### Future (P2)

13. **Reconcile category structure** — decide whether mental_health, legal, employment should remain separate or map to YourPeer's parent/sub-filter model.

14. **Document each intentional divergence** with rationale and data steward sign-off.

15. **Align requirement filter granularity** — support referral-required and membership-required as separate filter options.

16. **Implement free-text search** — equivalent to YourPeer's `searchString` parameter.
