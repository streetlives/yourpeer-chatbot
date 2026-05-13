# Streetlives Data & API Gotchas

**Status:** Living reference. Update when new gotchas are discovered.
**Audience:** Anyone writing query templates, slot extractors, or routing logic that touches the Streetlives DB or YourPeer frontend.
**Purpose:** A single catalog of the non-intuitive, peculiar, or bespoke things you'll trip on when working with the Streetlives data layer or the YourPeer codebase. None of these are bugs in our code — they are facts about the system we work within. Knowing them up-front saves hours of "but the data should look like X" debugging.

This doc consolidates findings from `audits/QUERY_PARITY_AUDIT.md` (April 2026), `audits/BOUNDARY_AUDIT.md` (April 2026), `audits/TAXONOMY_AUDIT_MAY2026.md` (May 2026), and the historical YourPeer parity work. The audits are the historical "how we found this" record; this doc is the day-to-day reference. **If you discover a new gotcha, add it here AND link to the source audit if one exists.**

---

## Table of contents

- [I. Streetlives DB — taxonomy structure](#i-streetlives-db--taxonomy-structure)
- [II. Streetlives DB — service tagging patterns](#ii-streetlives-db--service-tagging-patterns)
- [III. Streetlives DB — naming and identity](#iii-streetlives-db--naming-and-identity)
- [IV. Streetlives DB — geography and addresses](#iv-streetlives-db--geography-and-addresses)
- [V. Streetlives API — query mechanics](#v-streetlives-api--query-mechanics)
- [VI. YourPeer frontend — known bugs and quirks](#vi-yourpeer-frontend--known-bugs-and-quirks)
- [VII. Chatbot routing assumptions](#vii-chatbot-routing-assumptions)
- [VIII. Cross-system numerical artifacts](#viii-cross-system-numerical-artifacts)

---

## I. Streetlives DB — taxonomy structure

### Six top-level taxonomies, not nine

The DB has exactly six top-level (parent_id IS NULL) taxonomies: **Other service, Food, Health, Personal Care, Shelter, Clothing.** YourPeer's frontend and our chatbot both expose nine user-facing categories, achieved by promoting three children (Mental Health, Legal Services, Employment) to top-level positions. The "9 vs 6" mismatch is intentional and consistent across both products. (Source: `TAXONOMY_AUDIT_MAY2026.md` §VII)

### "Other service" is 40% of the DB

The single largest taxonomy by service count is `Other service` (1,413 services in subtree, 1,105 of those tagged at the parent with no specific child leaf). When you route something to `service_type=other`, you are searching across nearly half the database. Theme 13 of the May 2026 user-testing analysis ("Other is opaque") is a direct consequence. (Source: `TAXONOMY_AUDIT_MAY2026.md` §III)

### `Crisis` is a Shelter child, not a Health child

This was a chatbot bug fixed in April 2026. The medical template originally included `Crisis` based on the assumption it was a clinical mental-health taxonomy. It is actually `Shelter › Crisis` (13 services), mostly DV-related shelters. Including it in medical surfaced shelters to "I need a doctor" queries. (Source: `QUERY_PARITY_AUDIT.md` §"Key DB Findings")

### `Drop-in Center` and `Referral` are Shelter children

Both sound generic but both live under Shelter:
- `Shelter › Drop-in Center` — 6 services
- `Shelter › Referral` — 6 services (this is where Safe Horizon's "Shelter Placement" services live)

Excluding either from the shelter default list will silently drop important locations. (Source: `QUERY_PARITY_AUDIT.md` §"Key DB Findings")

### `Health` has only 4 children

The Health subtree is structurally simple compared to its size: General Health (57), Mental Health (128), Substance Use Treatment (12), Support Groups (8). Notably, **577 services are tagged at the Health parent directly**, more than the 205 in named children combined. This is the same pattern as the `Other service` parent-direct tagging-debt cluster (1,105 services) and may indicate widespread under-tagging at Health leaves. **Deferred follow-up:** a word-frequency analysis on the 577 Health parent-direct service names would reveal the latent sub-clusters (clinic types, urgent-care, dental, vision, etc.) and quantify the tagging-debt size. Not blocking for current chatbot work — `service_type=medical` already routes to the full Health subtree (parent + 4 children), so these 577 are findable today; the analysis matters before any future medical sub-narrowing. (Source: `TAXONOMY_AUDIT_MAY2026.md` §II + Appendix C Q4)

### `Shelter` has 20 children but most are tiny

Shelter is the most-fragmented branch: 20 children for only 86 leaf-level services (average 4.3 services per leaf). Many population-specific children have ≤4 services (Youth=4, Families=3, LGBTQ Young Adult=2, Senior=2, Veterans=1). Strict narrowing to a population child often returns 0 results. The chatbot adds back the parent `Shelter` for recall, an intentional divergence from YourPeer (which strict-narrows). (Source: `QUERY_PARITY_AUDIT.md` §"Shelter narrowing with parent preservation")

### Six taxonomies have zero services

These exist as rows in `taxonomies` but have no services tagged. Including them in default `taxonomy_names` lists costs a SQL filter clause for guaranteed zero return:

- `Food › Mobile Food Truck`
- `Other service › Pets`
- `Personal Care › Support Groups`
- `Shelter › Cooling Center`
- `Shelter › Intake`
- `Shelter › Supportive Housing`

(Source: `TAXONOMY_AUDIT_MAY2026.md` §II.6, §XI)

### `Advocates / Legal Aid` is a phantom taxonomy

YourPeer's `TAXONOMY_CATEGORIES` array includes `Advocates / Legal Aid`. The taxonomy exists in the DB but **0 services are tagged with it**. YourPeer's "Other Services" view excludes it via `setIntersection` filter; our chatbot ignores it entirely. (Source: `QUERY_PARITY_AUDIT.md` §6, `TAXONOMY_AUDIT_MAY2026.md` §VII)

### Two taxonomies named "Drop-in Center" exist (duplicate)

Under `Shelter`, two distinct taxonomy rows share the name `Drop-in Center`:
- UUID `1b6de3a4-61d9-4d09-99a7-b988a82fa5dd` — 1 service
- UUID `e7846c93-b014-4400-a431-d626faa240c1` — 5 services

Almost certainly a duplicate that should be merged. Filtering by name (rather than UUID) accidentally hits both, which gives the right user-facing behavior but is fragile. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I.2)

### `Personal Care › Support Groups` is a duplicate of `Health › Support Groups`

The Health child has 8 services; the Personal Care child has 0. The Personal Care entry is likely a relabeling that never got cleaned up. Treat the Health child as canonical. (Source: `QUERY_PARITY_AUDIT.md` §"Data Quality Notes")

### Phantom taxonomies referenced in old chatbot code

The chatbot's display whitelist used to reference three taxonomies that **do not exist in the DB**:
- `Harm Reduction`
- `Needle Exchange`
- `Overdose Prevention`

These were removed from `_DISPLAY_CATEGORIES` in April 2026. If you find a reference to them in older docs, it's a phantom. (Source: `QUERY_PARITY_AUDIT.md` §"Data Quality Notes")

### `Baby Supplies` is under Clothing, not Personal Care

There are TWO baby-related taxonomies:
- `Clothing › Baby Supplies` — 5 services (diapers, etc.)
- `Personal Care › Baby` — 3 services (also diapers; overlaps semantically)

Both are findable via the chatbot's `baby supplies` narrowing (added May 2026 in the diapers-routing fix). The semantic overlap is genuine; both contain diaper-distribution services. (Source: `TAXONOMY_AUDIT_MAY2026.md` §II.3)

---

## II. Streetlives DB — service tagging patterns

### Most services have exactly one taxonomy assignment

3,545 of the 3,546 services have exactly one row in `service_taxonomy`. Cross-branch tagging does not exist (zero services span multiple top-level branches). This makes the data exceptionally clean for analysis — every service rolls up cleanly to one of the six top-level branches. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I)

### Exactly one service has two taxonomy assignments

A service named `Job Readiness` (UUID `d92b9e8b-a52d-4545-a33b-94d9b87b720a`) is tagged with both `Other service › Case Workers` AND `Other service › Employment`. Both assignments are within the same top-level branch. This is the only multi-taxonomy service in the DB. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I)

### Two services share the name "Job Readiness"

Independent of the multi-taxonomy outlier above, there are two distinct services both literally named `Job Readiness`:
- UUID `d92b9e8b…` (the multi-taxonomy one above)
- UUID `413fb9d5-ed6c-44bd-837b-83e1db884c23` (tagged at `Other service` parent only)

Possibly intentional (two programs at different organizations), possibly a near-duplicate. Don't assume service name uniqueness. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I)

### Tagging debt at the "Other service" parent is substantial

About 1,105 services (40% of the Other service subtree, 31% of the entire DB) are tagged at the `Other service` parent with no specific child leaf. Many of these *should* be at specific leaves but weren't tagged there:

| Leaf | Currently tagged | Estimated true coverage if tagged |
|---|---:|---:|
| `Education` | 101 | ~180–230 |
| `Benefits` | 32 | ~70–100 |
| `Immigration Services` | 2 | ~30–50 |
| `Case Workers` | 28 | ~50–70 |

Implication: queries that filter to a specific leaf miss services with the right names but wrong tagging. The chatbot's mitigation is service-name pattern matching as a fallback (Ticket K in `TAXONOMY_AUDIT_MAY2026.md` §XII). (Source: `TAXONOMY_AUDIT_MAY2026.md` §IV)

### Missing-leaf clusters in "Other service" parent

Distinct from tagging debt, these are coherent service categories with **no leaf at all** in the Streetlives taxonomy. Approximate counts based on word-frequency analysis of Other service parent-direct service names:

- Recovery support (12-step, AA, anger management) — ~30–40 services. Distinct from `Health › Substance Use Treatment` (clinical, 12 services).
- Justice-impacted / re-entry — ~25 services
- Family / parenting (separate from Personal Care › Community Services with 4) — ~20 services
- Disability advocacy / OPWDD / Services for the Blind — ~13 services
- Crisis / hotline / survivor — ~30 services

These can only be reached today via `service_type=other` plus name-pattern matching. (Source: `TAXONOMY_AUDIT_MAY2026.md` §V)

### `Internship` is small but distinct

`Other service › Internship` has only 3 services. The chatbot routes internship-related queries to the `employment` template, which includes both `Employment` (35) and `Internship` (3). YourPeer treats them similarly via the "other → employment" sub-filter. (Source: `QUERY_PARITY_AUDIT.md` §"Data Quality Notes")

---

## III. Streetlives DB — naming and identity

### Service names are not unique

Two services named `Job Readiness` (above). Service names are also frequently generic — there are 16+ services named `Case Management`, multiple `Drop-in Center`, multiple `Community Services`, multiple `Activities and Classes`, multiple `Homebase`, multiple `Senior Center`, etc. **Always identify services by `id` (UUID), never by name.** (Source: Q6 sample from `TAXONOMY_AUDIT_MAY2026.md` Appendix B)

### Taxonomy names are not unique

See `Drop-in Center` duplicate above. Filter by `taxonomy.id`, not `taxonomy.name`, when correctness matters.

### Service names contain typos and inconsistent casing

Q6's alphabetical sample turned up `Aftreschool` (sic), `Admissisons to Treatment` (sic), and inconsistent case patterns (`Adult Education` vs `adult education`). String matching against service names is unreliable for grouping; lowercase + typo-tolerant matching is needed if you're going to do name-pattern matching as a fallback. (Source: `TAXONOMY_AUDIT_MAY2026.md` Q6 sample)

### Some service names are program acronyms with low salience

Many service names are program-specific acronyms or proper names that wouldn't match any user query: `ATTAIN`, `LSEP`, `PEAR`, `SNAP-Ed Coordinator`, `OPWDD Services`, `JAG Youth Program`, `STEPS to End Family Violence`, `EAC Network`. These appear in name-pattern matching but require domain knowledge to interpret. Don't over-rely on regex name matching for routing. (Source: `TAXONOMY_AUDIT_MAY2026.md` §III)

---

## IV. Streetlives DB — geography and addresses

### `physical_addresses.city` is unreliable for borough determination

A service flagged as "Manhattan" might be in the Bronx. Many addresses have wrong, missing, or inconsistent `city` values. The fix is **PostGIS borough polygon validation**: use the lat/lng to determine the actual NYC borough, then warn or correct when the stated city disagrees. See `audits/BOUNDARY_AUDIT.md` for the full analysis and `backend/app/rag/boundaries.py` for the implementation. (Source: `audits/BOUNDARY_AUDIT.md`)

### Neighborhood data is sparse

`location_detail.neighborhood` is populated for some locations, missing for others. The chatbot's `_BOROUGH_CENTROIDS` and nearest-neighborhood table in `backend/app/services/chatbot/context.py` provide a fallback, but expect partial coverage. (Source: `audits/BOUNDARY_AUDIT.md`)

### Coordinates are stored as `[lng, lat]`, not `[lat, lng]`

The API returns positions as `{"coordinates": [longitude, latitude]}`, GeoJSON-style. The chatbot's `map_gogetta_to_yourpeer` function translates this — when reading raw API data, swap accordingly. (Source: `streetlives-api-service.ts` `map_gogetta_to_yourpeer`)

### Schedules are in a `holiday_schedules` table, not a regular schedule table

Despite the name, `HolidaySchedules` is the live schedule data. There's also a `RegularSchedules` field on services but it's typically empty (`[]` per `streetlives-api-service.ts`). Use `HolidaySchedules` for open-now logic. (Source: `streetlives-api-service.ts` `isServiceClosed`)

### Service open-now data is sparse

Roughly 40-80% of services have any walk-in schedule data; nearly zero have it for non-walk-in services. Open-now exclusion (filter where `now BETWEEN opens_at AND closes_at`) drops most services from results. The chatbot uses **open-now sort, not exclude** for this reason — sort open services first but keep closed ones in results. YourPeer uses exclude semantics; this is an intentional divergence. (Source: `QUERY_PARITY_AUDIT.md` §"Open-now: sort-only semantics")

---

## V. Streetlives API — query mechanics

### `service_at_locations` (singular service, plural locations) is the canonical join table

The DB has historically had confusion about which table joins services to locations. Three names show up in older docs and the wild:

| Name | Status | Evidence |
|---|---|---|
| **`service_at_locations`** (singular service, plural locations) | **Canonical. Use this one.** | Production query templates use this (`backend/app/rag/query_templates.py:164` and elsewhere). |
| `service_at_location` (singular both) | Does not exist | `SELECT FROM service_at_location` raises `relation "service_at_location" does not exist` (verified May 2026). Earlier versions of this doc incorrectly claimed this was canonical with 3,445 rows. |
| `services_at_locations` (plural both) | Legacy cruft, empty | Returns no rows but no error. The silent-empty failure mode that costs a new maintainer an afternoon. |

The naming is genuinely awkward — neither pure singular nor pure plural — which is probably why the doc got it wrong originally. Always check the production query files (`backend/app/rag/query_templates.py`) rather than relying on this entry's memory if in doubt.

(This singular/plural confusion does NOT exist for `service_taxonomy` (singular, canonical) vs. `service_taxonomies` (plural, **does not exist** — querying it raises `relation does not exist`, which at least fails loudly).)

**Recommendation**: this should also be noted in the Streetlives onboarding wiki at the top of any "DB schema overview" section. The row count for `service_at_locations` is not pinned in this doc because it drifts with data ingestion; run `SELECT COUNT(*) FROM service_at_locations` against prod if you need the current number.

### The API filters by exact `taxonomyId` match — no subtree expansion

When you send `&taxonomyId=<parent_uuid>`, you get only services tagged AT that parent. Services tagged at children are NOT included automatically. Subtree expansion is the **caller's responsibility**.

YourPeer's `getTaxonomies` function in `streetlives-api-service.ts` enumerates parent + children explicitly:
```typescript
case "health-care":
  taxonomies = taxonomyResponse.flatMap((r) =>
    r.name === parentTaxonomyName
      ? [r as Taxonomy].concat(r.children ? r.children : [])
      : [],
  );
```

The chatbot does the same via explicit `EXPECTED_TAXONOMY_NAMES` lists in query templates. **Do not simplify these lists to single parent taxonomies — services would silently disappear.** (Source: `streetlives-api-service.ts`, `TAXONOMY_AUDIT_MAY2026.md` §VII)

### The API uses taxonomy UUIDs, the chatbot uses taxonomy names

YourPeer's frontend resolves taxonomy names to UUIDs client-side, then passes UUIDs in the API call. The chatbot's `query_templates.py` passes taxonomy NAMES (which the chatbot's own SQL translates to a name-based filter). Both work, but they're different mechanics. If you need to debug an API call directly, you'll need to look up UUIDs first. (Source: `streetlives-api-service.ts`)

### `taxonomySpecificAttributes` filter is partially functional

The API supports filtering by attribute (e.g., `clothingOccasion=Everyday`). YourPeer's source has a `FIXME` noting this filter "does not seem to have any effect on the returned results" in some cases. **The chatbot now uses this filter via `FILTER_BY_CLOTHING_OCCASION` for casual/professional clothing routing**, and DB verification (April 2026) confirms `clothingOccasion` has 62 Everyday + 28 Job Interview services. Other potentially-useful attributes found during that work but not yet leveraged: `tgncClothing` (36 TGNC-friendly services), `wearerAge`, `hasHivNutrition` (23 services). (Source: `QUERY_PARITY_AUDIT.md` §10, `streetlives-api-service.ts`)

### The `&shelter=` query parameter is plumbed but unused

YourPeer's `fetchLocationsData` accepts a `shelter` parameter but never appends it to the URL. Don't pass it expecting any effect. (Source: `streetlives-api-service.ts`)

### `&occasion=COVID19` is hardcoded into every API call

Every API request to `/locations` passes `occasion=COVID19`. This is a legacy parameter that filters the `Schedules` table to the COVID19-occasion variant. The chatbot mirrors this in its SQL. Removing it would change schedule semantics — don't drop it unless you know what you're doing. (Source: `streetlives-api-service.ts` `fetchLocationsData`)

### The `Pagination-Count` header has an off-by-one bug

YourPeer's `fetchLocationsData` includes the comment:
> I think there's a bug where it's returning the wrong number of pages, so decrement by 1 here

When using `Pagination-Count`, decrement. Don't trust the raw value. (Source: `streetlives-api-service.ts`)

### Sort-by-`nearby` requires lat/lng or it errors

If you set `sortBy=nearby` without providing latitude and longitude, YourPeer throws. The chatbot's bucketed-distance sort requires the same. (Source: `streetlives-api-service.ts` `fetchLocationsData`)

---

## VI. YourPeer frontend — known bugs and quirks

These are bugs and gaps in YourPeer's frontend code that affect us indirectly (because the chatbot is positioned as YourPeer-equivalent for many use cases). Each should be reported upstream where appropriate.

### Mental Health, Legal Services, and Employment standalone routes return all locations

`getTaxonomies()` in `streetlives-api-service.ts` has no switch case for `"mental-health"`, `"legal-services"`, or `"employment"`, and no `default`. When users navigate directly to `/mental-health`, `/legal-services`, or `/employment`, the function returns `taxonomies = []`, which the API treats as "no filter" — returning ALL locations.

These categories work correctly only when accessed as sub-filters (e.g., `/health-care?health-care=mental-health`). **The chatbot handles these correctly via dedicated templates — this is an area where the chatbot is ahead of YourPeer.** (Source: `QUERY_PARITY_AUDIT.md` §"YourPeer Bugs/Gaps Found During Audit" #1)

### The Youth shelter sub-route is non-functional

`https://yourpeer.nyc/shelters-housing/youth` looks like a youth-specific shelter route, but the underlying `getTaxonomies()` returns the parent `Shelter` taxonomy — same as the no-sub-category case. There's no filter to `Shelter › Youth` (4 services). Filtering happens via `ageMin=16&ageMax=24` server-side, which means services tagged ONLY as `Youth` (not also `Shelter`) are invisible.

The chatbot's Phase C work can implement actual Youth narrowing as a net-new improvement; there is no parity to "match" here. (Source: `QUERY_PARITY_AUDIT.md` §"YourPeer Bugs/Gaps Found During Audit" #2, `TAXONOMY_AUDIT_MAY2026.md` §VIII)

### Health Care client-side filters out Mental Health (post-fetch)

`filter_services_by_name` in `streetlives-api-service.ts` excludes services tagged with `Mental Health` from Health Care results, AFTER the API returns them. The API call for Health Care includes Mental Health in the taxonomyId list (the parent + 4 children); the response is post-filtered.

The chatbot's `medical` template should mirror this — either exclude `Mental Health` from `EXPECTED_TAXONOMY_NAMES` outright, or include and post-filter. Current chatbot behavior already treats them as overlapping intentionally (Medical/Mental Health shared taxonomies divergence in `QUERY_PARITY_AUDIT.md` §"Known Intentional Divergences"). (Source: `streetlives-api-service.ts`, `TAXONOMY_AUDIT_MAY2026.md` Ticket L)

### YourPeer has no population-modifier filter dimension

Despite the DB having `Eligibilities` and `population_served` fields, YourPeer's frontend doesn't expose any filter for gender, LGBTQ status, veteran status, immigration status, justice-impacted status, or disability. The frontend's only population-related filter is `AGE_PARAM` (numeric age) and `REQUIREMENT_PARAM` (referral letter / registered client).

If the chatbot adds population-aware routing, it is **pioneering this dimension product-wide**. Coordinate with the YourPeer team if they want parity. (Source: `TAXONOMY_AUDIT_MAY2026.md` §VI)

### YourPeer has TWO active environments

`yourpeer.nyc` (production) and `staging.yourpeer.nyc` and `test.yourpeer.nyc` all exist and serve traffic, with potentially different builds and data states. When debugging, check which environment you're hitting. (Source: search results during May 2026 audit)

### Population filter parity is partial in YourPeer's data

Some population filters work via age range (Youth = 16-24 ageMin/ageMax), some via taxonomy children (Families, Single Adult), some via post-fetch filtering (LGBTQ Young Adult). The **chatbot's "shelter safety enrichments"** (5 additive rules in `rag/__init__.py:185-209`) implement consistent population routing across these mismatched mechanisms — see "Unvalidated Business Rules" below for the open questions. (Source: `QUERY_PARITY_AUDIT.md` §"Shelter safety enrichments")

---

## VII. Chatbot routing assumptions

These are decisions the chatbot makes that aren't directly mirrored in YourPeer or the DB. They're in production, they work, but they're worth knowing about.

### Shelter narrowing preserves the parent for recall

When a user message has `family_status=with_children`, the chatbot produces `["families", "shelter"]`. YourPeer would strict-narrow to `["families"]`. Justification: the Families child has only 3 services; strict narrowing returns 0 results often. The 18 services tagged at parent `Shelter` are preserved.

Same pattern for `alone` → `["single adult", "shelter"]`. (Source: `QUERY_PARITY_AUDIT.md` §"Shelter narrowing with parent preservation")

### Shelter safety enrichments — five additive rules

The chatbot adds population-specific shelter children based on demographic signals:

| Signal | Adds |
|---|---|
| Age 16–24 | `youth` |
| LGBTQ / trans / nonbinary | `drop-in center, crisis, lgbtq young adult` |
| Age ≥ 62 | `senior` |
| Veteran | `veterans, veterans short-term housing` |
| Pregnant + alone | (override to families narrow) |
| DV survivor | `drop-in center, crisis` |

These are novel divergences from YourPeer (which has no population-modifier dimension). They preserve population recall when narrowing strips out children the user qualifies for. (Source: `QUERY_PARITY_AUDIT.md` §"Shelter safety enrichments")

### Open-now is sort-only, not exclude

The chatbot sorts open services first but never excludes closed services. YourPeer uses exclude semantics. Justification: sparse schedule data (40–80% walk-in, near-zero non-walk-in) makes exclusion dangerous — you'd silently drop most services. (Source: `QUERY_PARITY_AUDIT.md` §"Open-now: sort-only semantics")

### Description-pattern filtering is a chatbot-only mechanism

The chatbot has 79+ description regex patterns (`_DETAIL_DESCRIPTION_FILTERS` in `rag/__init__.py`) that filter services post-taxonomy-match by matching against `service.description`. YourPeer doesn't do this. These patterns surface things the taxonomy alone can't (e.g., "harm reduction" as a description fragment when there's no Harm Reduction taxonomy).

Caveat: 79 patterns means 79 places for false positives. April 2026 verification ran each pattern against prod and found 1 dead pattern (`dialysis services`: 0 matches) — removed. 7 patterns are very broad (>200 matches) and are documented as functional but worth monitoring. Adding a new pattern: validate against prod first. (Source: `QUERY_PARITY_AUDIT.md` §9 "Description filter pattern validation")

### PostgreSQL POSIX regex: `\b` is the backspace character, not a word boundary

When binding regex patterns into SQL via `~*` (case-insensitive POSIX match), **`\b` is the backspace character `\x08`, not a word-boundary anchor** — despite some PostgreSQL documentation listing it as a synonym for `\y`. A pattern like `s.name ~* '\besl\b'` returns zero matches even when ESL services exist. The correct anchors in PostgreSQL ARE are `\m` (start of word) and `\M` (end of word).

Verified empirically, May 2026, against prod:

| Pattern | Matches | Interpretation |
|---|---:|---|
| `s.name ~* 'esl'` | 7 | Correct — no boundary, plain substring |
| `s.name ~* '\besl\b'` | **0** | `\b` interpreted as backspace; matches nothing |
| `s.name ~* '\mesl\M'` | 7 | Correct word-boundary behavior |

**Convention.** Use `\m...\M` in any regex that lands in SQL — `_DETAIL_DESCRIPTION_FILTERS` in `rag/__init__.py` (e.g. `r"\mSSI\M|\mSSDI\M"`), the service-name patterns in `rag/query_templates.py` (`_EDUCATION_NAME_PATTERN`, `_BENEFITS_NAME_PATTERN`), and any future SQL-bound regex constants. This is PostgreSQL-specific: Python's `re` module treats `\b` as a word boundary correctly, so the in-process regexes in `services/slot_extraction_regex.py` (compiled with `re.compile`, matched against user text in-process) use `\b` and that is correct for that layer.

**Enforced by:** `tests/unit/test_narrowing.py::TestWordBoundaryCorrectness` scans both `rag/__init__.py` and `rag/query_templates.py` for raw-string regex literals containing `\b` and fails the build on any hit (comment lines excluded). When you add a new file containing SQL-bound regex constants, extend that scan to cover it.

**How it was found.** Phase B Ticket C and Ticket D (May 2026) introduced `_EDUCATION_NAME_PATTERN` and `_BENEFITS_NAME_PATTERN` using `\b` (the author followed the PG ARE doc claim that `\b` is a synonym for `\y`; this setup doesn't honor that claim). The patterns silently matched zero services in production. Two eval scenarios (`peer_bad_with_money`, `peer_food_stamps_apply`) surfaced the bug by returning zero benefits results despite correct routing; the 5 static SQL-structure tests on the filter passed throughout because they never executed the SQL against a DB. The fix is mechanical (s/`\b`/`\m`/ leading, s/`\b`/`\M`/ trailing). The underlying lesson — that "compiles fine, returns zero rows" is invisible to static tests — is the more durable one.

(Source: Phase B coverage investigation, May 2026; see also the comment block at `rag/__init__.py:135-139` for the original `ID services` discovery that established the project convention.)

### Description filtering does not compose with narrow templates

`FILTER_BY_DESCRIPTION_KEYWORDS` was designed as a sub-narrowing mechanism for the wide `other` template (~1,400 services), keyed off `service_detail` and applied as an AND filter on top of the taxonomy IN-match. **It does not compose with narrow templates** — when AND'd onto a service-type that's already aggressively narrowed by taxonomy + name-pattern, the intersection collapses to near-zero before location filtering even runs.

Empirically verified, May 2026, post-`\m...\M` fix:

| Filter stage | Candidate count |
|---|---:|
| `service_type=benefits` taxonomy + name-pattern OR'd filter, NYC-wide | **96** |
| + `description ~* '\mfinancial\M\|money management\|budget\|credit\|debt'` | **6** |
| + `description ~* 'food stamp\|SNAP\|EBT\|electronic benefit'` | **22** |
| + Brooklyn / Manhattan / Bronx borough polygon | **0–3** |

The 0–3 outcome explains why the post-`\b`-fix eval still produced empty result sets for `peer_bad_with_money`, `peer_food_stamps_apply`, `benefits_queens`, and `natural_benefits_ebt` — the fix restored Tier 1's name-pattern matching, but the AND-chain with description filtering still produced zero. The relaxed-query fallback exists but does not reliably drop the description filter for the OR'd-filter templates.

**Convention.** Templates promoted out of `other` to their own narrow service_type omit `FILTER_BY_DESCRIPTION_KEYWORDS` from both `required_filters` and `optional_filters`. Currently this is `education` and `benefits` (Phase B Tickets C and D, May 2026); `case_management` will follow when promoted. The wide `other` template retains description filtering because its taxonomy alone returns ~1,400 services and description filtering is the primary narrowing mechanism there.

**What still works.** `service_detail` is still extracted, still appears in the confirmation message ("I'll look for ESL classes in Brooklyn"), and still routes correctly. The promoted templates simply return the full taxonomy + name-pattern slice in the user's location rather than sub-narrowing inside that slice. If post-SQL sub-narrowing turns out to be valuable for specific sub-types (e.g., to rank SNAP-mentioning services ahead of Medicaid-mentioning ones inside a "food stamps" query), it can be added at the response-building layer without re-introducing the SQL AND-chain.

**Enforced by:** `tests/unit/test_narrowing.py::TestPromotedTemplatesOmitDescriptionFilter` asserts the negative invariant — promoted narrow templates must not have `FILTER_BY_DESCRIPTION_KEYWORDS` in either filter list. Adding a new promoted template → add it to `PROMOTED_NARROW_TEMPLATES` in that class.

(Source: Phase B coverage investigation, May 2026; eval run 2026-05-13 — `peer_bad_with_money` zero-result diagnosis.)

### Some chatbot SERVICE_KEYWORDS are domain-specific and easy to mis-route

Some keywords look like everyday English but the chatbot routes them to specific service types:

- `wic` → food (NOT `wicked`, prevented via word-boundary)
- `prep` → medical (HIV PrEP medication, NOT food prep)
- `formula` → food (baby formula, NOT mathematical formula)
- `pads` → personal_care (hygiene, NOT iPads — word-boundary protected)
- `vision` → medical (eye care, NOT future plans)

See `audits/REGEX_AUDIT.md` for the full collision-risk analysis and remediation history. Word-boundary protection (`\b` in **Python** regex, used in `services/slot_extraction_regex.py`) is now standard for most of these — `\b` is the correct word-boundary anchor in Python's `re` module. **Do not** carry this pattern into SQL-bound regex: see the entry above on PostgreSQL POSIX regex, where `\b` is the backspace character and `\m`/`\M` are the correct anchors. (Source: `audits/REGEX_AUDIT.md`)

### Some "service categories" the chatbot uses are actually population modifiers

Senior services, disability services, and LGBTQ non-shelter services are all tagged in `SERVICE_KEYWORDS["other"]` today. Conceptually they are population dimensions, not service categories. Section VI of `TAXONOMY_AUDIT_MAY2026.md` recommends extracting them as a separate slot.

This is the recommendation — current code does NOT do this yet. (Source: `TAXONOMY_AUDIT_MAY2026.md` §VI)

### Crisis handlers silently drop `additional_services` from the LLM extraction

**Confirmed via Pattern B probe, May 2026.** When the crisis_detector fires (DV, suicide, trafficking, etc.), the dispatch path routes through dedicated crisis handlers in `services/chatbot/handlers/`. These handlers respond with the appropriate hotlines and offer to search for the primary service_type, but they **do not consult or surface `additional_services`** from the same LLM extraction.

Concrete example from the probe:

> User: "Escaped abuse with my child, safe for the moment, need help with shelter and next steps."
>
> LLM extraction (Pattern B fix landing correctly): `{service_type: shelter, additional_services: [{type: legal}], crisis_category: domestic_violence, family_status: with_children}`
>
> Bot turn 2: "I'm sorry you're going through this... I can also help you find shelter in your area — would you like me to search?"
>
> Bot turn 6 (after results): "I found 4 option(s) for you:" — followed by quick replies "New search" / "Peer navigator." **Legal queue never offered.**

The user's stated need ("next steps" → legal/advocacy in DV context) is silently dropped. Compare to the standard non-crisis flow, which carries additional_services through to a "I can also search for X next" follow-up.

**Why this matters:** for DV survivors, "what now" after escaping is overwhelmingly a legal/advocacy question (order of protection, custody, housing-program enrollment, immigration relief). The Family Justice Centers exist for exactly this need. Dropping the legal queue in the crisis path is a routing failure that affects the population most needing the breadth.

**Status:** filed as Gap 2 of Pattern B follow-up. Not addressed in the prompt-only Pattern B fix; needs dispatch-layer work in the relevant crisis handler(s) to consult `additional_services` and offer them post-results. Same shape probably affects suicide_self_harm + service_type combinations and the other 8 crisis categories — needs an audit, not just a single-handler fix.

(Source: `scenarios.jsonl` from probe run 2026-05-07; `peer_escaped_abuse_child_next_steps`)

### Unvalidated business rules

The following are routing rules in our chatbot that have not been validated against Streetlives policy or the YourPeer team. Each is open as a question for sign-off:

| Rule | Code location | Risk | Open question |
|---|---|---|---|
| `youth` taxonomy always added at age 16-24 | `rag/__init__.py:185` | High | YourPeer uses ageMin/ageMax instead — which is correct? |
| `senior` added at age ≥ 62 | `rag/__init__.py:186` | Medium | Is 62 the right threshold? |
| `families` ADDED for `with_children` | `rag/__init__.py:169` | High | YourPeer NARROWS (replaces parent); chatbot ADDS — which? |
| `single adult` ADDED for `alone` | `rag/__init__.py:172` | High | Same question; YourPeer also adds ageMin=18, ageMax=99 |
| `lgbtq young adult` always included | `rag/__init__.py:192` | Low | Reasonable safety measure but verify clutter |
| `crisis` + `drop-in center` for LGBTQ/DV | `rag/__init__.py:196-209` | Medium | Verify Safe Horizon, Ali Forney taxonomy tags in DB |

(Source: `QUERY_PARITY_AUDIT.md` §"Unvalidated Business Rules")

---

## VIII. Cross-system numerical artifacts

These are observations about counts and metrics that look weird at first glance.

### Total service count: 3,546, not 3,547

A common slip: SQL queries that `COUNT(*)` over a `LEFT JOIN service_taxonomy` return 3,547 because Job Readiness contributes 2 rows. The actual count of distinct services is 3,546. Use `SELECT COUNT(*) FROM services` for the canonical count, NOT `COUNT(*)` with a join. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I.3)

### Subtree counts sum to 3,546, not 3,547

Q2's subtree-count sum equals 3,546, matching the distinct-service count. No double-counting across branches because no services span multiple top-level branches. The "should it be 3,547" math intuition is wrong here. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I)

### `service_taxonomy` row count: 3,547

The join table itself has 3,547 rows. This is `total_services + 1` because Job Readiness has 2 rows. It is not a bug. (Source: `TAXONOMY_AUDIT_MAY2026.md` §I)

### Distinct taxonomies actually used: 63 of 69

Of the 69 taxonomies in the DB, 63 have at least one service tagged. The 6 zero-service taxonomies are listed in §I above. (Source: `TAXONOMY_AUDIT_MAY2026.md` §II.6)

### Health subtree (782) + Other service subtree (1,413) > Total services (3,546)

Wait — these don't sum past the total. Just noting that all six top-level subtrees sum to 3,546 cleanly. The historical confusion came from:
- 1,048 (Food) + 1,413 (Other service) + 782 (Health) + 105 (Personal Care) + 104 (Shelter) + 94 (Clothing) = 3,546 ✓

(Source: `TAXONOMY_AUDIT_MAY2026.md` §II)

---

## How to add to this doc

When you discover a new gotcha:

1. **Add it under the right section** (DB structure / DB tagging / API mechanics / YourPeer / Chatbot routing / Numerical artifacts).
2. **Cite the source** if there is one — the audit doc that found it, the user-testing doc that surfaced it, the eval scenario that hit it.
3. **Give the canonical mitigation** if there is one.
4. **Date it** if the underlying data could change (vestigial taxonomies that get cleaned up, FIXMEs that get fixed). Include a "verified" or "as of" date.
5. **Update the relevant audit doc** as well, if the finding belongs there.

The drift checker (`scripts/check_docs.py`) does not currently scan this doc. If a gotcha references a specific code location, file path, or count that could go stale, add a `<!-- check:... -->` annotation following the patterns used in other docs (e.g., `audits/PHRASE_LIST_AUDIT.md`).

---

## Document history

- **May 2026 — initial creation.** Consolidated findings from `audits/QUERY_PARITY_AUDIT.md` (April 2026), `audits/BOUNDARY_AUDIT.md` (April 2026), `audits/REGEX_AUDIT.md` (April 2026), `audits/TAXONOMY_AUDIT_MAY2026.md` (May 2026), and informal observations from `streetlives-api-service.ts` review.
- **May 2026 — PostgreSQL POSIX regex `\b`/`\m`/`\M` entry added** (§VII). Found during Phase B coverage investigation: `\b` is the backspace character in PG ARE, not a word boundary; project convention is `\m...\M` for SQL-bound regex. Existing SERVICE_KEYWORDS entry in §VII updated to disambiguate Python `\b` (works) from PG `\b` (does not).
- **May 2026 — Description filtering does not compose with narrow templates** (§VII). Added after the post-`\b`-fix eval showed all four benefits scenarios still returning 0 results. Diagnosis: `FILTER_BY_DESCRIPTION_KEYWORDS` AND'd onto `FILTER_BY_TAXONOMY_OR_NAME_PATTERN` reduces 96 candidates to 6–22 NYC-wide, then to 0 after borough narrowing. Mitigation: `education` and `benefits` templates now omit description filtering; promoted-template invariant enforced by `TestPromotedTemplatesOmitDescriptionFilter`.
- **May 2026 — `service_at_locations` table-name correction** (§V). The doc had previously claimed `service_at_location` (singular both) was canonical with 3,445 rows. Production code uses `service_at_locations` (singular service, plural locations); the previously-listed name does not exist as a table at all. Caught when Query B of the Phase B coverage investigation raised `relation does not exist`. Row count removed from the doc since it drifts with ingestion.
