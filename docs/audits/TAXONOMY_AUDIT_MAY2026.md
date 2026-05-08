# YourPeer Chatbot — Streetlives Taxonomy Audit

**Date:** May 2026
**Status:** Complete; informs Phase B / C ticket scoping
**Scope:** A characterization of the Streetlives taxonomy hierarchy as it exists in production, with explicit comparison to the YourPeer chatbot's nine-bucket `service_type` scheme. The audit identifies routing gaps, tagging debt, vestigial taxonomies, mis-routing risks, and concrete promotion candidates. It does not change any code; it is a reference document for subsequent work.

---

## Executive summary

The Streetlives database contains 3,546 services organized under six top-level taxonomy branches (Other service, Food, Health, Personal Care, Shelter, Clothing). The data discipline is exceptionally clean: zero services span multiple top-level branches, every service has at least one taxonomy (no orphans), and all but one service have exactly one taxonomy. The hierarchy is shallow — every taxonomy is either a top-level root or a direct child of one — and there is no `parent_id` / `parent_name` drift. Two minor data-quality artifacts surfaced during the audit (one taxonomy duplicated, one service-name shared by two records); these are flagged in Section I but do not affect routing.

The chatbot's nine-bucket `service_type` scheme (`food`, `shelter`, `clothing`, `personal_care`, `medical`, `mental_health`, `legal`, `employment`, `other`) is **identical to the YourPeer frontend's `CATEGORIES` array**, confirmed by inspection of `src/components/common.ts`. Both products treat Mental Health, Legal Services, and Employment as first-class top-level user-facing categories even though they are children of Health and Other service in the DB. The mapping from frontend category to DB taxonomy in YourPeer's code (`CATEGORY_TO_TAXONOMY_NAME_MAP`) is the canonical answer to "what taxonomies should each service_type query": six map to DB top-levels; three map to promoted children. The chatbot should adopt this same mapping.

The dominant finding of the audit is that **the `Other service` parent-direct bucket — services tagged at the parent with no specific child leaf — contains 1,105 services, or 31% of the entire database**. This bucket is not miscellaneous. Word-frequency analysis and a 100-row alphabetical sample reveal at least eight distinguishable sub-clusters (Education, Tech / digital literacy, Recovery support, Workforce / job prep, Crisis / hotline / survivor, Justice-impacted / re-entry, Family / parenting, Disability) plus systematic tagging debt where services that have an obvious leaf in the same subtree (Education, Benefits, Immigration, Case Workers) are tagged at the parent instead. The chatbot routes all of these to `service_type=other`, which is functionally invisible to users (Theme 13 from the May 2026 user-testing analysis: *"Other is not clear, basically"*). YourPeer's frontend has the same routing model and faces the same problem.

A second major finding is that **the `Shelter` branch has 20 child taxonomies and the chatbot has zero sub-narrowings for any of them**. YourPeer's frontend narrows shelter to 3 sub-routes (`/shelters-housing/{adult|families|youth}`), so the chatbot's gap is genuinely 3 narrowings worth of catch-up plus targeted population-specific extensions for the eval scenarios that need them — not 20 narrowings worth. The unconditional inclusion of `Shelter › Residential Recovery` in the default shelter taxonomy_names list is the root cause of the HIGH-severity user-testing bug (`"I need shelter"` returning a residential treatment program); fixing this is independent of any narrowing work.

A third finding from the YourPeer source review: **YourPeer's frontend has no population-modifier filter dimension** (LGBTQ, veteran, senior, disability, immigration status, etc.). The DB has the data, but neither product surfaces it as a filterable dimension today. If the chatbot adds population-aware routing, it is pioneering this dimension product-wide; coordination with the YourPeer team is appropriate but the chatbot can move first.

The audit recommends:

1. **Promotion of three new top-level service_types** (`education`, `benefits`, `case_management`) and reconsideration of `immigration` as a fourth, based on actual database coverage. These would be net-new features for the product line, not catch-up work — neither YourPeer nor the chatbot exposes them today.
2. **A shelter sub-narrowing pass** filling the 0-narrowings gap with the 3 YourPeer-equivalent routes plus population-specific extensions (LGBTQ Young Adult, Senior, Veterans) for the eval scenarios that need them.
3. **A separation of population modifiers from service categories** — disability, senior, veteran, LGBTQ, justice-impacted, immigrant — as a distinct routing dimension. This is product-pioneering territory; the YourPeer frontend doesn't do this today.
4. **A vestigial-taxonomy cleanup** removing six zero-service taxonomies from default `taxonomy_names` lists.
5. **Coordination with the Streetlives data team** on tagging-debt cleanup, since approximately 50-100% of the leaf-level coverage in Education, Benefits, Immigration, and Case Workers is presently sitting at the `Other service` parent.

These recommendations are scoped as Phase B and Phase C tickets, separate from the in-flight PR. The audit itself does not implement any of them.

---

## I. Setup and methodology

### Data source

All findings derive from the production Streetlives database (`service`, `service_taxonomy`, `taxonomies` tables). The audit was conducted via eight read-only SQL queries run against a recent dump; query text is preserved in Appendix B. Three returned no rows under their original syntax (`FILTER (WHERE ...)` aggregate clauses and CTE-with-DISTINCT-ORDER-BY combinations) and were re-issued with plain ANSI SQL substitutes.

### Tables involved

The relevant tables, confirmed via probe queries:

- `services` — 3,546 rows. Each represents a distinct service offering at a location.
- `taxonomies` — 69 rows. Each is a top-level or child taxonomy. Both `parent_id` (FK) and `parent_name` (denormalized text) are present and agree perfectly across all 69 rows.
- `service_taxonomy` (singular) — the service-to-taxonomy join table. The plural `service_taxonomies` does not exist; an earlier audit query failed with `ERROR: relation "service_taxonomies" does not exist` confirming the singular form is canonical.

### Data discipline check

A series of probes established that the dataset is structurally clean to a degree that simplifies all downstream analysis:

- **No orphans.** Of 3,546 services, 0 have no taxonomy assignment.
- **No dangling foreign keys.** All 3,547 rows in `service_taxonomy` reference services that exist in the `services` table.
- **Effectively 1:1 service-to-taxonomy mapping.** 3,545 services have exactly 1 taxonomy assignment. One service has 2 taxonomy assignments — its name is `Job Readiness`, and the two assignments are `Other service › Case Workers` and `Other service › Employment` (within the same top-level branch).
- **No cross-branch tagging.** Zero services span multiple top-level branches.
- **No parent_id / parent_name drift.** All 69 taxonomies have `parent_name` matching the row reachable via `parent_id`. The two columns can be used interchangeably.

The cleanliness of the dataset means that any mis-routing observed in the chatbot is **definitionally a routing-layer problem in our code**, not a data-tagging or data-quality problem in Streetlives. This is a useful constraint: it means every issue in this document can be addressed through chatbot code changes (or, in the case of tagging debt, by Streetlives data-team work which the chatbot can route around in the meantime).

### Three minor data-quality observations

These are surfaced for the Streetlives data team. They do not affect any chatbot routing recommendation and are listed here so they don't get lost.

1. **Two services share the name `Job Readiness`** (UUIDs `d92b9e8b…` and `413fb9d5…`). Service A has two taxonomies (Case Workers + Employment); Service B has one (Other service parent-direct). Possibly intentional — two distinct programs at different organizations both happen to be named "Job Readiness" — but worth confirming.
2. **Two distinct taxonomy rows share the name `Drop-in Center`** (UUIDs `1b6de3a4…` and `e7846c93…`), both under Shelter, with 1 and 5 services respectively. Almost certainly a duplicate that should be merged. The full Shelter branch reference table in Section II lists "Drop-in Center: 6 services" as the combined count; the two rows are functionally identical.
3. **Q4's original orphan count returned `total_services = 3,547`** because the `COUNT(*)` was over a `LEFT JOIN` and the joined table contains 3,547 rows (3,545 services × 1 row + 1 service × 2 rows + 0 orphans). The actual count of distinct services is 3,546. This was a query error, not a data error, but it's noted in case it confuses anyone re-reading the audit's query log in Appendix B.

---

## II. The Streetlives taxonomy structure

### Top-level distribution

The database has six top-level taxonomies. Their distribution by service count is heavily skewed:

| Top-level taxonomy | n children | Direct services | Subtree services | % of DB |
|---|---:|---:|---:|---:|
| Other service | 11 | 1,105 | 1,413 | 39.8% |
| Food | 11 | 95 | 1,048 | 29.5% |
| Health | 4 | 577 | 782 | 22.0% |
| Personal Care | 11 | 11 | 105 | 3.0% |
| Shelter | 20 | 18 | 104 | 2.9% |
| Clothing | 6 | 15 | 94 | 2.7% |
| **Total** | **63** | **1,821** | **3,546** | — |

(Subtree counts sum to 3,546, matching the total service count exactly. The earlier discrepancy noted in audit drafts was a query artifact, not real.)

Three of the six branches (Other service, Food, Health) account for 91% of all services. The other three (Personal Care, Shelter, Clothing) are small but functionally critical — they serve high-stakes use cases (where to sleep, what to wear, basic hygiene) that disproportionately drive user trust.

### Full taxonomy reference

The complete taxonomy hierarchy, organized by top-level branch, with service counts at each leaf:

#### Food (1,048 services in subtree, 95 parent-direct)

| Taxonomy | n services |
|---|---:|
| Food Pantry | 730 |
| Soup Kitchen | 167 |
| Brown Bag | 14 |
| Mobile Market | 13 |
| Mobile Soup Kitchen | 11 |
| Food Benefits | 6 |
| Mobile Pantry | 4 |
| Food Delivery / Meals on Wheels | 4 |
| Farmer's Markets | 3 |
| Appliances | 1 |
| Mobile Food Truck | 0 |

Food Pantry alone holds 70% of the Food subtree. Food is the most narrowing-friendly branch — its eleven children are functionally distinct service modes (sit-down vs. takeaway vs. delivery vs. SNAP enrollment).

#### Health (782 services in subtree, 577 parent-direct)

| Taxonomy | n services |
|---|---:|
| Mental Health | 128 |
| General Health | 57 |
| Substance Use Treatment | 12 |
| Support Groups | 8 |

Note: Health has 577 parent-direct services — services tagged at `Health` (no specific child) — which is more than three times the leaf coverage combined. This indicates either widespread tagging debt at the leaf level, or that "Health" is being used as a generic clinical-services bucket without further differentiation.

The chatbot's `mental_health` service_type maps to `Health › Mental Health` (128 services). The chatbot's `medical` service_type maps to the Health subtree as a whole, which means a `medical` query overlaps with `mental_health` (the 128) and with substance use treatment (12) unless the query template explicitly excludes them.

#### Personal Care (105 services in subtree, 11 parent-direct)

| Taxonomy | n services |
|---|---:|
| Shower | 31 |
| Toiletries | 25 |
| Laundry | 18 |
| Haircut | 5 |
| Restrooms | 4 |
| Community Services | 4 |
| Baby | 3 |
| Activities | 2 |
| Hygiene | 1 |
| Gym | 1 |
| Support Groups | 0 |

The chatbot has narrowings for Shower, Toiletries, Laundry, Haircut, Restrooms (the five highest-volume children) and now Baby (added in May 2026 as part of the diapers-routing fix). Community Services (4 services), Activities (2), Hygiene (1), and Gym (1) have no narrowing and are reachable only by the default Personal Care taxonomy_names list. Support Groups under Personal Care has zero services — vestigial.

The Community Services leaf is notable: it appears to be where parenting services live in the Streetlives DB. The chatbot currently routes "parenting class / parenting program / parenting support" to `service_type=other`, missing this leaf entirely.

#### Shelter (104 services in subtree, 18 parent-direct)

| Taxonomy | n services |
|---|---:|
| Single Adult | 38 |
| Crisis | 13 |
| Drop-in Center | 6 |
| Referral | 6 |
| Youth | 4 |
| Families | 3 |
| Transitional Independent Living (TIL) | 3 |
| Residential Recovery | 2 |
| LGBTQ Young Adult | 2 |
| Senior | 2 |
| Veterans Short-Term Housing | 2 |
| Veterans | 1 |
| Safe Haven | 1 |
| Warming Center | 1 |
| Housing Lottery | 1 |
| Assessment | 1 |
| Cooling Center | 0 |
| Intake | 0 |
| Supportive Housing | 0 |

Shelter is the most-fragmented branch in the dataset: 20 children for only 86 leaf-level services, an average of 4.3 services per leaf. Many leaves are population-specific (Single Adult, Youth, Families, LGBTQ Young Adult, Senior, Veterans, Veterans Short-Term Housing) and would benefit from being conditionally surfaced based on user context rather than blanketed into a default taxonomy_names list.

The chatbot has zero sub-narrowings for shelter. Every shelter query uses the same default taxonomy_names list, which surfaces all 20 children indiscriminately. This is the single largest architectural gap in the routing layer; see Section VIII.

#### Clothing (94 services in subtree, 15 parent-direct)

| Taxonomy | n services |
|---|---:|
| Clothing Pantry | 69 |
| Baby Supplies | 5 |
| Thrift Shop | 2 |
| Coat Drive | 1 |
| Interview-Ready Clothing | 1 |
| Professional Clothing | 1 |

Clothing Pantry is the dominant child. Baby Supplies is now the target of the new narrowing (May 2026 diapers fix). Coat Drive and Interview-Ready / Professional Clothing are tiny but distinct sub-types that could benefit from narrowing if a relevant `service_detail` is extracted ("interview clothing" / "winter coat").

A separate routing dimension exists for clothing that the chatbot is not currently using: `taxonomySpecificAttributes`. Inspection of YourPeer's frontend (`streetlives-api-service.ts`) shows that selecting Clothing › Casual passes `taxonomySpecificAttributes=clothingOccasion,Everyday` to the API, and Clothing › Professional passes `clothingOccasion,Job Interview`. A `FIXME` comment in YourPeer's source notes that this filter "does not seem to have any effect on the returned results", suggesting the data is incomplete in production today — but the API supports the dimension. Worth tracking as a future routing signal if the data fills in. Similar attributes may exist for other categories.

#### Other service (1,413 services in subtree, 1,105 parent-direct)

| Taxonomy | n services |
|---|---:|
| **(Other service parent, no child)** | **1,105** |
| Education | 101 |
| Legal Services | 92 |
| Employment | 35 |
| Benefits | 32 |
| Case Workers | 28 |
| Free Wifi | 8 |
| Mail | 6 |
| Internship | 3 |
| Immigration Services | 2 |
| Taxes | 2 |
| Pets | 0 |

Note: only 308 of the 1,413 services in the Other service subtree are at named child leaves. The remaining 1,105 — nearly four-fifths of the subtree — sit at the parent. This bucket is the focus of Sections III and IV.

### Vestigial taxonomies (zero services)

Six taxonomies have zero services tagged:

- Other service › Pets
- Food › Mobile Food Truck
- Shelter › Cooling Center
- Shelter › Intake
- Shelter › Supportive Housing
- Personal Care › Support Groups

These should be removed from any default taxonomy_names lists in the chatbot's query templates. They cost a SQL filter clause for guaranteed zero return. The `Supportive Housing` taxonomy is already known to be vestigial via a code comment elsewhere; the others should join it.

---

## III. The "Other service" parent-direct bucket

This section is the most consequential finding in the audit. The 1,105 services tagged at the `Other service` parent with no specific child leaf are not miscellaneous — they cluster into at least eight distinguishable sub-categories visible in service-name patterns and word frequencies.

### Methodology

A SQL word-frequency analysis (Appendix B, Query 7) over the 1,105 service names returned the 50 most-common words (length > 3, frequency ≥ 5, excluding stopwords). A separate alphabetical sample (Query 6) returned the first 100 service names by name for human pattern-spotting. Together these provide both quantitative weight and qualitative texture.

### Word-frequency clusters

The top non-stopword frequencies in the 1,105 parent-direct service names:

| Cluster | Anchor words and frequencies | Estimated coverage |
|---|---|---|
| Education / afterschool / ESL / clubhouse | literacy (9), afterschool (9), clubhouse (9), workshops (7), english (5), citizenship (5), college (6), attain (8) | ~60+ services |
| Tech / digital literacy / computer | computer (10), technology (10), tech (6), code (6), online (7), networking (8) | ~45+ services |
| Substance use / recovery | substance (11), (drug abuse) (10), prevention (8), meetings (7), treatment (6), groups (10) | ~40+ services |
| Workforce / job prep | prep (11), workforce (9) | ~30+ services |
| Crisis / hotline / survivor | hotline (9), crisis (7), emergency (7), survivor (7) | ~30+ services |
| Justice-impacted / re-entry | formerly (10), justice (7), court-involved (6) | ~25+ services |
| Family / parenting | child (9), parents (6), children (5) | ~20+ services |
| Disability | disability (7), disabilities (6) | ~13+ services |

These eight clusters are distinct in concept but overlap statistically — a single service name like "Adult Literacy and Career Center" hits both Education and Workforce clusters. The figures above are not unique-service counts; they are upper-bound estimates of cluster size that double-count multi-cluster services. The cluster *concepts*, however, are independent: a service either does education or it doesn't.

### Qualitative confirmation: the alphabetical sample

The first 100 service names from Query 6 (alphabetical, A through "Campus Sexual Assault Services") corroborate the clusters and add fine-grained patterns:

- **Education tagging debt:** "Adult Education" (×3), "Adult Education Enrollment" (×2), "Adult Education Sign-Up", "Adult Learning Center" (×2), "Adult Literacy" (×4), "Adult Literacy and Education", "Academic Enrichment", "Advance & Earn Plus High School Equivalency (HSE)" — at least 14 services in the A's alone whose names unambiguously identify them as education services. They sit at parent rather than at the `Other service › Education` leaf (which has 101 services). If this density holds across all 26 letters, an additional 80–150 education services are presently parent-tagged.

- **ATTAIN program:** "Advanced Technology Training and Information Networking (ATTAIN)" appears 8 times in the parent bucket, plus "ATTAIN" variants. ATTAIN is the SUNY adult tech-training program with multiple NYC sites. All 8 should be tagged at the Education leaf or a (currently nonexistent) Tech-Training leaf.

- **Asylum / immigration tagging debt:** "Ark Immigration Clinic", "Asylum and Immigration Services", "Asylum Applications", "Asylum Clinic", "Asylum Self-Petition" — five immigration-focused services in the A's, when the `Other service › Immigration Services` leaf only contains 2 services total. Extrapolating, the Immigration Services leaf is undercounting by an order of magnitude.

- **Benefits tagging debt:** "Benefit Assistance", "Benefits" (×3), "Benefits and Taxes", "Benefits Assistance" (×3), "Benefits Assistance and Immigration Services", "Benefits Assistance and Other Supports", "Benefits Enrollment" — 11 services in the B's that look like Benefits leaf candidates. The Benefits leaf has 32; this density suggests 40–80 additional services are parent-tagged.

- **12-Step / recovery:** "12-Step In-Person and Remote Meetings", "12-Step Meetings", "Admissisons to Treatment" (sic), "Anger Management" — 4+ services in the A's that fit the recovery-support cluster. Health › Substance Use Treatment has 12 services (treatment-grade). This is a different category — peer-led, prevention, support-group-style. There is no leaf for it.

- **Genuine miscellany:** "Action Center", "Additional Services", "Activities" (×3), "Activities and Classes" (×3), "Adult Drop-in Center", "Adult Programming", "Adult Services", "Afternoon Services", "Aging Services", "Andrew Fund for Pet Care", "Become a Member", "Brooklyn CRAN" — services whose names do not identify a specific category. These are the truly miscellaneous ones; routing them generically is appropriate.

### The character of the bucket

Synthesizing the word-frequency clusters and the alphabetical sample, the 1,105 parent-direct services break down approximately as follows:

| Type | Estimated share |
|---|---:|
| Tagging debt — services with a clear leaf elsewhere in the taxonomy that simply weren't tagged there | ~40–50% |
| Cluster gaps — coherent service categories with no leaf at all (Recovery support, Re-entry, Parenting, Disability) | ~25–35% |
| Genuine miscellany — services without any obvious category | ~15–25% |

The implication for the chatbot is significant. The current `service_type=other` route treats all 1,105 as undifferentiated. In reality, at most 200-300 of them are genuinely miscellaneous; the rest belong to identifiable sub-categories that could be routed specifically.

---

## IV. Tagging debt — observations for the Streetlives data team

This section is explicit feedback for the Streetlives data team. The chatbot can work around tagging debt to some extent (see Section X), but the cleanest fix is to assign services to the correct leaf taxonomy in the source data.

### Estimated tagging debt by leaf

Based on the alphabetical sample's density and conservative extrapolation:

| Leaf | Currently tagged | Estimated true coverage if tagged | Tagging debt |
|---|---:|---:|---:|
| Other service › Education | 101 | ~180–230 | ~80–130 services |
| Other service › Benefits | 32 | ~70–100 | ~40–70 services |
| Other service › Immigration Services | 2 | ~30–50 | ~28–48 services |
| Other service › Case Workers | 28 | ~50–70 | ~25–45 services |
| Health › Substance Use Treatment | 12 | (no debt at this leaf — see below) | 0 |

The Substance Use Treatment leaf is not undertagged in the same way; rather, the recovery-support cluster sitting in `Other service` parent represents a *different* category (peer support, prevention, 12-step meetings) from clinical substance use treatment. This is a missing-leaf problem, not a tagging-debt problem (Section V).

### Recommendation to Streetlives data team

Without prejudging priorities, the four leaves with substantial tagging debt are also the ones with the highest service counts to begin with. A targeted re-tagging pass on the 1,105 parent-direct services — driven by service-name patterns — would move 200-400 services from parent-direct to specific leaves. This would substantially clean up the largest bucket in the database without requiring any taxonomy structural changes.

A practical heuristic: any parent-direct service whose name contains "Education", "Literacy", "Learning", "ESL", "GED", "Citizenship", or "Afterschool" is almost certainly an Education-leaf candidate. Similar simple rules cover Benefits ("Benefits", "SNAP", "SSI", "Public Assistance") and Immigration ("Immigration", "Asylum", "Refugee", "Citizenship-related Legal").

The chatbot can route around this debt in the meantime by pattern-matching on `service.name` as a fallback signal when leaf coverage is sparse. This is described in Section X.

---

## V. Missing-leaf clusters

Distinct from tagging debt, several coherent sub-categories visible in the parent-direct bucket have no leaf taxonomy anywhere in the hierarchy. These are not a Streetlives data-team issue; they are a structure issue. The chatbot can group them via name-pattern matching, but until they have a leaf, sub-narrowing is constrained.

### Recovery support (~30–40 services)

Distinct from Health › Substance Use Treatment (12 services, treatment-grade clinical care). The recovery-support cluster includes:

- 12-Step meetings (in-person and online)
- Anger Management
- Smart Recovery / SMART meetings
- Peer support groups for substance use
- Prevention education
- Recovery community centers

These are not clinical treatment programs; they are community-led. A user looking for "AA meetings" or "a 12-step group" or "I want to quit drinking and need support" should not be routed to medical detox. There is no current leaf that captures this distinction.

### Justice-impacted / re-entry (~25 services)

Services for people formerly incarcerated, on probation/parole, or whose families are affected. Pattern: "Formerly Incarcerated", "Justice-Impacted", "Court-Involved", "Re-entry", "Children with Incarcerated Parents", "Alternative to Incarceration (ATI)".

Currently routed to `service_type=other`. The eval scenarios `peer_aging_out_foster` and `peer_felon_employment` brush against this cluster. There is no leaf and no chatbot routing path that surfaces these specifically.

### Family / parenting (~20 services)

Services that support parents and caregivers — parenting classes, family support groups, child advocacy, postpartum support, preventive family services. Pattern: "Parenting", "Family Services", "Family Support", "Family Preservation", "Postpartum Support", "Resources for Parents & Families", "Support for Parents".

Personal Care › Community Services has 4 services that look like parenting (e.g. "Parenting Services"), but the bulk of the cluster is at `Other service` parent. There is no proper leaf.

### Disability (~13 services)

Services for people with disabilities — disability advocacy, OPWDD services, Services for the Blind, ACCESS-VR. This cluster is small but distinct, and is properly understood as a *population modifier* rather than a *service category* (Section VI).

### Crisis / hotline / survivor (~30 services)

24/7 crisis hotlines, anti-violence initiatives, survivor advocacy programs, dating violence support, gun violence survivor advocacy. These overlap with the chatbot's existing Foundation 8 hotline-pinning logic but are not currently tied to a specific leaf for non-crisis-context surfacing.

---

## VI. Population modifiers vs service categories

A recurring confusion in both the Streetlives taxonomy and the chatbot's `service_type=other` keyword routing is the conflation of *who* a service is for (the population) and *what* the service does (the category). Several entries that look like service categories are actually population modifiers in disguise.

### What YourPeer's frontend does today (and doesn't)

`common.ts` reveals that **YourPeer's frontend has no population-modifier filter dimension**. The full filter set is:

- `SEARCH_PARAM` — free-text search
- `AGE_PARAM` — single age (numeric, e.g. "21")
- `OPEN_PARAM` — open now
- `REQUIREMENT_PARAM` — `no-requirements` / `referral-letter` / `registered-client`
- Per-category sub-routes (e.g. `/shelters-housing/adult`)
- `AMENITIES_PARAM` for personal-care multi-select

**Notably absent**: gender, LGBTQ status, veteran status, immigration status, justice-impacted status, disability status. These do exist as data on `ServiceData.Eligibilities[].EligibilityParameter` and `ServiceData.population_served` — but YourPeer doesn't expose them as filterable dimensions.

This means the chatbot's "Foundation 8 LGBTQ shelter pinning" mechanism (and any future population-aware routing) is **chatbot-pioneering territory**, not a port of existing YourPeer functionality. If we add population modifiers as a routing dimension, we are leading the product line; the website would then need to catch up if they wanted parity.

### The pattern in the data

The Streetlives DB has population-shaped taxonomies in several places:

- **Shelter children:** Youth, Families, Single Adult, Senior, Veterans, Veterans Short-Term Housing, LGBTQ Young Adult — these specify *who the shelter is for*, not what kind of service it provides.
- **Other service parent-direct:** "Older People", "Older Adults Programming", "People with Disabilities", "Children", "Immigrants", "LGBTQIA+ Support Groups" — same pattern, embedded in service names rather than in the taxonomy.

### The pattern in the chatbot

The chatbot's `SERVICE_KEYWORDS["other"]` includes population-shaped entries:

- Senior services, "I'm a senior", "for older people"
- Disability services, accessible services
- LGBTQ services (non-shelter)

When a user says "I need senior services" or "services for people with disabilities", we route to `service_type=other`. This collapses the population dimension into the catchall.

### Recommendation

Population should be a separate slot from service_type. The chatbot already has `audience` or population-detection logic in some scenarios; this should be made first-class as a re-rank / filter signal *orthogonal* to service_type rather than embedded in `service_type=other`.

Concretely:

- "I need senior food services" → `service_type=food, population=senior`. Search Food taxonomies, then re-rank or filter for senior-friendly providers.
- "LGBTQ-affirming shelter" → `service_type=shelter, population=lgbtq_young_adult`. Search Shelter taxonomies, then narrow to LGBTQ Young Adult leaf when appropriate.
- "Veterans shelter" → `service_type=shelter, population=veteran`. Narrow to Veterans / Veterans Short-Term Housing.

The chatbot already partially does this for the LGBTQ shelter case (Foundation 8 must-include pinning). Generalizing to other populations would dramatically reduce `service_type=other` usage and improve the precision of population-specific results.

This also addresses the Theme 8 user-testing finding: *"Demographic / identity filtering keeps recurring (especially from professional users)."*

A follow-on question for the YourPeer team: **should population modifiers also become first-class filters in the website?** The DB has the data (via `Eligibilities` and `population_served`); both products would benefit from exposing it. The chatbot can lead, but coordination on the eventual filter shape would let both products converge.

---

## VII. The chatbot's nine-bucket scheme vs the database's six top-levels

The chatbot has nine `service_type` values: `food`, `shelter`, `clothing`, `personal_care`, `medical`, `mental_health`, `legal`, `employment`, `other`. Inspection of YourPeer's frontend source (`src/components/common.ts`, May 2026) confirms this is *exactly* YourPeer's `CATEGORIES` array as well:

```typescript
const CATEGORIES = [
  "shelters-housing", "food", "clothing", "personal-care",
  "health-care", "mental-health", "legal-services",
  "employment", "other",
];
```

Both products treat Mental Health, Legal Services, and Employment as first-class top-level categories with their own dedicated routes (`/mental-health`, `/legal-services`, `/employment`), not sub-categories of Health or Other. The mapping from frontend category to DB taxonomy in YourPeer's code (`CATEGORY_TO_TAXONOMY_NAME_MAP`) is:

```typescript
{
  "health-care": "Health",            // DB top-level
  "other": "Other service",           // DB top-level
  "shelters-housing": "Shelter",      // DB top-level
  "food": "Food",                     // DB top-level
  "clothing": "Clothing",             // DB top-level
  "personal-care": "Personal Care",   // DB top-level
  "legal-services": "Legal Services", // DB child (of Other service)
  "mental-health": "Mental Health",   // DB child (of Health)
  "employment": "Employment",         // DB child (of Other service)
}
```

This is the canonical answer to "how do the chatbot's service_types map to DB taxonomies?" — both products use this same mapping. Six map to DB top-levels; three map to child taxonomies that have been promoted to top-level user-facing categories.

A separate observation from `common.ts` is that the frontend's `TAXONOMY_CATEGORIES` array contains ten entries: the six DB top-levels, the three promoted children, and `Advocates / Legal Aid`. The latter did not appear in our Query 1 / Query 2 results. Either (a) it has zero services and was filtered out of the GROUP BY, (b) it is a new taxonomy added after our DB snapshot, or (c) the frontend defines it speculatively in anticipation of data. A targeted lookup would confirm which.

### How the API actually works (from `streetlives-api-service.ts`)

The Streetlives API accepts a comma-separated list of taxonomy UUIDs as the `taxonomyId` query parameter. The API filters by **exact match** — it does not walk the `parent_id` tree to expand a parent into its children. Subtree expansion is therefore the **frontend's responsibility**, not the API's.

YourPeer's `getTaxonomies` function (in `streetlives-api-service.ts`) implements this expansion explicitly:

```typescript
case "health-care":
  taxonomies = taxonomyResponse.flatMap((r) =>
    r.name === parentTaxonomyName
      ? [r as Taxonomy].concat(r.children ? r.children : [])
      : [],
  );
```

When the user selects Health Care, the frontend enumerates the parent `Health` taxonomy plus all four children (Mental Health, General Health, Substance Use Treatment, Support Groups) and sends all five IDs. Without this, only the 577 parent-direct services would be returned; the 205 leaf-tagged services would be missed.

This validates the chatbot's existing pattern of maintaining explicit `EXPECTED_TAXONOMY_NAMES` lists with all relevant children enumerated. Simplifying to single-parent queries would not work — the API would silently drop child-tagged services. The chatbot does not need to coordinate with the API team; the current architecture is correct.

### The medical / mental_health overlap is solved client-side in YourPeer

`filter_services_by_name` in `streetlives-api-service.ts` excludes services tagged with `Mental Health` from the Health Care results:

```typescript
if (
  !(
    category_name === "health-care" &&
    taxonomiesForService.has("Mental Health")
  ) &&
  service["Taxonomies"].length !== 0
) {
  services.push(...);
}
```

The API call for Health Care includes Mental Health in its taxonomyId list (per the subtree expansion above), but the response is post-filtered to remove Mental Health-tagged services from the Health Care section. They surface only in the dedicated Mental Health view.

**Recommendation for the chatbot**: replicate this exclusion. The `medical` service_type's query template should either (a) exclude `Mental Health` from its taxonomy_names list outright, or (b) include it in the API call but post-filter the response. Option (a) is simpler and aligns with the chatbot's general pattern. This closes the medical/mental_health overlap I flagged earlier.

### Alignment table

| Chatbot service_type | Maps to (DB) | Alignment | Notes |
|---|---|---|---|
| `food` | Food (top-level) | Clean | Direct mapping. |
| `shelter` | Shelter (top-level) | Coarse | Top-level mapping is correct, but 20 children with no narrowing means routing is undifferentiated. See Section VIII. |
| `clothing` | Clothing (top-level) | Clean | Clothing Pantry dominates; Baby Supplies recently narrowed. |
| `personal_care` | Personal Care (top-level) | Mostly clean | 5/11 children narrowable (now 6/11 with Baby). Community Services leaf not surfaced. |
| `medical` | Health (top-level), excluding Mental Health | Partial overlap | Currently overlaps with `mental_health` route; needs explicit exclusion or sub-narrowing. |
| `mental_health` | Health › Mental Health (child) | Awkward | A child taxonomy promoted to top-level service_type. Works but creates the medical-overlap issue above. |
| `legal` | Other service › Legal Services (child) | Awkward | A child taxonomy promoted to top-level service_type. Implicit acknowledgment that "Other service" is too coarse. |
| `employment` | Other service › Employment (child) + Internship (child) | Awkward | Same as legal. |
| `other` | Other service (top-level) + Other service parent-direct + everything else | Catastrophic | Holds 60+ keyword routes, covers 1,105 parent-direct services, and three other DB leaves we don't promote. See Section III. |

Three observations:

1. **Three of our service_types (`legal`, `employment`, `mental_health`) are also chip-promotions in the YourPeer frontend.** The "promotion from child taxonomy to top-level" pattern isn't a chatbot quirk; it's a Streetlives-product-line convention. This means our recommendation to add `education`, `benefits`, `case_management`, and possibly `immigration` as top-level service_types is structurally consistent with both the chatbot's existing pattern AND YourPeer's frontend pattern. If we promote them, we should also coordinate with the YourPeer team — they may want to add the same chips to the website.

2. **The `medical` / `mental_health` overlap is unresolved.** A query for "medical" today probably surfaces mental health services (128 services, 24% of Health subtree by volume). This may be desirable in some cases (a user looking for "a doctor" might be open to mental health support) but in others (a user with a sprained ankle) it dilutes results. YourPeer separates Health Care and Mental Health into distinct top-level chips for the same reason; the chatbot should follow suit and exclude Mental Health from the `medical` taxonomy_names list.

3. **The `other` bucket is doing far more work than it should — and YourPeer faces the same problem.** Section III documented this in detail; the alignment table just confirms that "other" is bearing the load of every uncategorized intent. YourPeer also has an "Other Services" chip serving the same role, and its prominence in the live navigation suggests they are aware this is too broad. Theme 13 of the user-testing analysis ("Other is opaque") may therefore be a frontend critique as well as a chatbot critique. Coordination on what to promote out of "Other" benefits both products.

---

## VIII. The shelter sub-narrowing gap

The Shelter branch is the largest unfilled architectural gap in the chatbot's routing layer. The DB has 20 children; the chatbot has zero narrowings. Inspection of YourPeer's frontend (May 2026) reveals an important calibration: **YourPeer narrows shelter to only 3 sub-categories**, exposed at `/shelters-housing/{adult|families|youth}`. The frontend uses URL constants `SHELTER_PARAM_SINGLE_VALUE = "adult"`, `SHELTER_PARAM_FAMILY_VALUE = "families"`, `SHELTER_PARAM_YOUTH_VALUE = "youth"` mapping to the `Shelter › Single Adult`, `Shelter › Families`, and `Shelter › Youth` DB children respectively.

So the chatbot's gap is not 20 narrowings worth — it is 3 narrowings worth (matching what YourPeer already does), plus the question of whether to extend further than YourPeer does for the population-specific use cases the chatbot needs to handle.

### The 20 children, ranked by service count and YourPeer parity

| Shelter child | n services | YourPeer chip? | Population-specific? | Default-include risk |
|---|---:|---|---|---|
| Single Adult | 38 | ✅ `/shelters-housing/adult` | No | In default |
| Crisis | 13 | ❌ | Partially (DV-overlap) | Default with DV-aware narrowing |
| Drop-in Center | 6 | ❌ | No | Different service mode — narrowing target |
| Referral | 6 | ❌ | No | Should be in default |
| Youth | 4 | ⚠️ Chip exists at `/shelters-housing/youth` but is non-functional | Yes | Narrow when minor or "youth" signal |
| Families | 3 | ✅ `/shelters-housing/families` | Yes | Narrow when family / children context |
| Transitional Independent Living (TIL) | 3 | ❌ | Partially | Different shelter mode |
| Residential Recovery | 2 | ❌ | Yes (substance use) | High-risk default-include — see CREATE Inc. |
| LGBTQ Young Adult | 2 | ❌ | Yes | Narrow when LGBTQ + youth signal |
| Senior | 2 | ❌ | Yes | Narrow when senior signal |
| Veterans Short-Term Housing | 2 | ❌ | Yes | Narrow when veteran signal |
| Veterans | 1 | ❌ | Yes | Same as above |
| Safe Haven | 1 | ❌ | Partially | Specialized shelter type |
| Warming Center | 1 | ❌ | No | Seasonal — narrow when cold signal |
| Housing Lottery | 1 | ❌ | No | Application-based housing |
| Assessment | 1 | ❌ | No | Intake-shaped service |
| Cooling Center | 0 | ❌ | — | Vestigial |
| Intake | 0 | ❌ | — | Vestigial |
| Supportive Housing | 0 | ❌ | — | Vestigial |

YourPeer covers the three highest-volume children plus Youth — but with a twist. Inspection of `streetlives-api-service.ts` reveals that **the Youth sub-route at `/shelters-housing/youth` is non-functional in YourPeer's frontend**. The relevant code:

```typescript
case SHELTER_PARAM_YOUTH_VALUE:
  taxonomies = taxonomyResponse.flatMap((r) =>
    r.name === parentTaxonomyName ? [r as Taxonomy] : [],
  );
```

When a user clicks "Youth" on yourpeer.nyc, the frontend queries the Shelter parent only — exactly the same as the no-sub-category case. There is no filter to the `Shelter › Youth` child taxonomy (which contains 4 services in the DB). This appears to be either a bug or an incomplete feature implementation; the user-facing chip exists in the navigation but the routing logic is a no-op.

**Implication for the chatbot's Phase C work**: since YourPeer's Youth sub-route is not actually narrowing, the chatbot does not need to "match YourPeer parity" for Youth — there is no parity to match. The chatbot can implement actual Youth narrowing (filtering to `Shelter › Youth`) as a genuine improvement over the YourPeer frontend, addressing the eval scenarios `peer_aging_out_foster` and `shelter_queens_17` that need this routing.

This finding should also be reported to the YourPeer frontend team; their Youth chip is misleading users.

The remaining 16 children are "long-tail" — collectively 35 services across 16 leaves, average 2 services per leaf. The user-testing-confirmed bugs (CREATE Inc. shelter, Ali Forney LGBTQ youth coverage) are not in YourPeer's narrowing scope today either; they are problems both products share.

### Phase C scope for shelter narrowing

A reasonable Phase C goal is to **match YourPeer's three sub-routes plus add the population modifiers the chatbot's eval suite specifically requires**. Concretely:

1. **Match YourPeer (3 narrowings):** Single Adult, Families, Youth — these align with the existing frontend chips and address most generic shelter ask variants.
2. **Add population-specific narrowings the chatbot needs:** LGBTQ Young Adult (Foundation 8 LGBTQ pinning), Senior, Veterans, Veterans Short-Term Housing — each surfaces only when the relevant population signal is present in the user message.
3. **Gate the CREATE Inc. risk:** Either remove `Residential Recovery` from the default shelter taxonomy_names list, or include it only when substance-use context is present. This is independent of any YourPeer-side change — it's a chatbot-only mitigation.
4. **Vestigial cleanup:** remove `Cooling Center`, `Intake`, and `Supportive Housing` from default lists (zero services each).

### The CREATE Inc. story

The May 2026 user-testing analysis documented a HIGH-severity bug: a user typed *"I need shelter"* and the top result was CREATE Inc., a chemical-dependence treatment program. The user bailed to yourpeer.nyc rather than continuing.

Now traceable. CREATE Inc. is one of the 2 services tagged at `Shelter › Residential Recovery`. The shelter query template's default `taxonomy_names` list includes Residential Recovery (or includes the parent without exclusions). The bug is the unconditional inclusion of a population-specific child in the default route.

Fix options:

1. **Exclude Residential Recovery from default shelter taxonomy_names.** Cost: 2 services lose shelter-search reachability. Acceptable since they are really treatment programs, and a user asking for substance-use treatment would be routed via `mental_health` or a future `recovery_support` service_type anyway.

2. **Keep it but require a substance-use signal.** Conditional include: surface Residential Recovery only when the user message contains substance-use context. More flexible but more complex.

Either fix is one to three lines of code. Which to choose depends on whether the 2 services have any value as shelter results in any context. Argument for #1: the user-testing bug suggests no — these services confuse the "I need a place to sleep tonight" use case more than they help.

### Other population-specific defaults to gate

By the same pattern as Residential Recovery, the following children should not be in the default shelter route — they should be conditionally included based on population context:

- LGBTQ Young Adult (2 services) → include when LGBTQ + youth signal
- Senior (2) → include when senior signal
- Veterans / Veterans Short-Term Housing (3 combined) → include when veteran signal
- Youth (4) → include when minor signal (`age < 18` OR explicit youth-status)
- Families (3) → include when family context (`with_children` OR explicit "with my kids")
- Warming Center (1) → include when seasonal cold signal
- Cooling Center (0) → vestigial, remove

### Eval scenarios traceable to this gap

Several eval scenarios that have been failing or borderline trace directly to the shelter sub-narrowing gap:

- `peer_aging_out_foster` (3.55 in R32, still failing) — needs Youth narrowing for foster-aftercare context
- `natural_lgbtq_youth` (4.18 in R32) — needs LGBTQ Young Adult narrowing; Ali Forney coverage hinges on it
- `shelter_queens_17` — minor crisis resource gap; needs Youth narrowing
- The `wa_substance_use_shelter` family of scenarios — needs explicit substance-use → recovery-support routing rather than re-mapping to mental health

Filling the shelter narrowing gap addresses all four. It is the highest-leverage Phase C ticket.

---

## IX. Promotion candidates from "other" to top-level service_type

Important context from `common.ts`: YourPeer's frontend currently has the same 9-bucket scheme as our chatbot. **Education, Benefits, and Case Management are NOT chips in YourPeer today.** A user looking for adult literacy or benefits enrollment on yourpeer.nyc is routed through "Other Services" the same way our chatbot's `service_type=other` handles them.

This means our recommendations below are not "catch up to what YourPeer already exposes" — they are net-new product expansions. If the chatbot adds these service_types, the value proposition is genuine: the chatbot gains routing precision that the YourPeer website doesn't currently have. Coordination with the YourPeer frontend team is appropriate, but the chatbot can move first; the data and demand both support it.

Based on the database coverage analysis (Section III), the user-testing demand signals, and the existence of the legal/employment/mental_health precedent for promoting child taxonomies to top-level service_types, the following promotions are recommended:

### Strongly recommended (clear DB coverage + demand)

#### 1. `education`

- **DB leaf coverage:** 101 services in `Other service › Education`
- **Estimated true coverage with tagging-debt repair:** 180-230
- **Demand signal:** Recording 13 ESL ask; "Adult Literacy" / GED / ESL keywords currently in `service_type=other`
- **Effort:** Add `service_type=education`, query template, expected_taxonomy_names. Move existing keywords (esl, ged, adult education, literacy, computer classes, digital literacy) from `other` to `education`. Add description-regex narrowings for sub-types (ESL vs. GED vs. computer classes).
- **Notes:** This is the single biggest reduction of the `other` bucket. Recommended as the first Phase B promotion.

#### 2. `benefits`

- **DB leaf coverage:** 32 services in `Other service › Benefits`
- **Estimated true coverage with tagging-debt repair:** 70-100
- **Demand signal:** Recurring user-testing requests for vouchers, SNAP enrollment, public assistance, Section 8, financial assistance; Theme 4 *"end user 3: Section 8 voucher"*; a substantial chunk of `service_type=other` keyword entries today
- **Effort:** Same shape as education promotion. Consolidate benefits-related keywords (SNAP, EBT, food stamps, cash assistance, Medicaid enrollment, public assistance, SSI, SSD, vouchers, rental assistance) under one route.
- **Notes:** This consolidates many today-orphaned intents and addresses a high-volume demand signal.

#### 3. `case_management`

- **DB leaf coverage:** 28 services in `Other service › Case Workers`
- **Estimated true coverage with tagging-debt repair:** 50-70 (the parent bucket has many services with "Case Management" or "Care Management" in the name)
- **Demand signal:** Olive's specific user-testing finding (peer navigator unable to surface case management); multiple `Care Coordination` / `Care Management` services parent-tagged
- **Effort:** Smaller scope than Education or Benefits — fewer keywords today route here (case manager, care manager, social worker). Add the service_type, route the keywords, narrow.
- **Notes:** Validated by both data and user-testing. Should be a Phase B promotion.

### Worth considering (moderate coverage + specific demand)

#### 4. `immigration`

- **DB leaf coverage:** 2 services in `Other service › Immigration Services`
- **Estimated true coverage with tagging-debt repair:** 30-50 (the alphabetical sample suggests at least 9 immigration-related services in the A's alone; the leaf is severely undercounted)
- **Demand signal:** Ongoing immigration-related scenarios in the eval suite; multilingual users; recent-arrival user populations
- **Effort:** Could be promoted to its own service_type, OR upgraded as a sub-narrowing of `legal`. Given the projected true coverage (30-50), promotion is justified. Sub-narrowing would also work and would be lighter-weight.
- **Notes:** The dismissal in the May 2026 audit-prep ("too small to promote — keep in Legal") was based on the leaf count alone (2). The true coverage is much larger. Worth revisiting.

### Not recommended for promotion (small or specialized)

#### `recovery_support` (alternative: a sub-narrowing of `mental_health`)

- **DB coverage:** No leaf exists. ~30-40 services parent-tagged in Other service.
- **Demand signal:** Population-specific routing for substance-use peer support
- **Effort:** Cannot directly map to a leaf; requires service-name pattern matching as fallback
- **Notes:** The right answer here may be more nuanced. Two paths: (a) promote a `recovery_support` service_type that uses name-pattern matching to surface the parent-tagged services, or (b) extend `mental_health` to cover this cluster with an explicit `service_detail` for "12-step" / "AA" / "recovery support". Path (b) is lighter-weight.

#### `re_entry` (justice-impacted)

- **DB coverage:** No leaf exists. ~25 services parent-tagged in Other service.
- **Demand signal:** Eval scenarios `peer_aging_out_foster` (foster youth ≠ re-entry, but adjacent), `peer_felon_employment` (already routes via semantic router)
- **Effort:** Cannot directly map to a leaf; requires service-name pattern matching as fallback
- **Notes:** Better treated as a population modifier (Section VI) than a service_type. A user looking for re-entry services typically wants a specific service (housing, employment, legal) filtered for justice-impacted-friendly providers.

### Not recommended (too small)

- **Mail** (6 services) — drop-in service detail rather than a category
- **Free Wifi** (8 services) — same
- **Taxes** (2 services) — fold into Benefits
- **Pets** (0 services) — vestigial

### Summary of recommended promotions

After tagging-debt repair, the four recommended promotions move approximately **350-450 services** out of `service_type=other` and into specific routes. This reduces the residual `other` bucket from ~1,105 parent-direct services to ~600-700, putting it in the same order of magnitude as `Health`'s parent-direct count.

---

## X. The chatbot's options for working around tagging debt

The chatbot cannot move a parent-tagged service to a leaf — that requires a Streetlives data-team write. But the chatbot can route around the gap.

### Option A: Service-name pattern matching as a fallback

When a leaf returns sparse results, the chatbot can additionally query for services in the same subtree whose names match expected patterns. For example:

- `service_type=education` query: first run with `taxonomy_names=["education", "internship"]` (the named leaves); if results are low, supplement with services in the Other service subtree whose name matches a regex like `(?i)(literacy|esl|ged|attain|adult\s+education|adult\s+learning|college|academic)`.
- `service_type=benefits`: same pattern with the Benefits leaf and a regex for `(?i)(snap|ebt|food\s+stamps|cash\s+assistance|public\s+benefits|ssi|ssd|medicaid|voucher)`.

This is a code-level workaround that doesn't require Streetlives to change anything. It also positions the chatbot to absorb tagging-debt repair gracefully — as services migrate from parent to leaf, the leaf-tagged results subsume the regex-matched ones, and the chatbot's behavior improves without any code change.

### Option B: Description-regex filtering

The chatbot already has `_DETAIL_DESCRIPTION_FILTERS` in the routing layer. Extending these to cover the missing-leaf clusters (recovery support, re-entry, parenting) is a natural fit. Each description filter is a regex applied to `service.description` after taxonomy filtering. These can be paired with broad parent taxonomies to surface services that don't have a specific leaf.

### Option C: Adjacent-service / near-miss handling

When a search returns zero or few results, the chatbot can offer adjacent services. The user-testing analysis quotes Koula:

> *"if it had other services that were like it like notary → library or bookstores that would be helpful. Or pregnancy test → urgent cares or cityMD."*

For tagging-debt cases this means: when a `case_management` query returns only the 28 leaf-tagged services, surface a "you might also want to check parent-tagged services" prompt with a sample of name-matched results. This is honest about the data limitation while still serving the user.

---

## XI. Vestigial taxonomy cleanup

Six taxonomies in the database have zero services. They cost a SQL filter clause for guaranteed zero return, and they create the false impression that the chatbot supports a service category that doesn't exist in the data:

| Taxonomy | Status | Action |
|---|---|---|
| Other service › Pets | Vestigial | Remove from any default `taxonomy_names` list |
| Food › Mobile Food Truck | Vestigial | Remove |
| Shelter › Cooling Center | Vestigial | Remove |
| Shelter › Intake | Vestigial | Remove |
| Shelter › Supportive Housing | Vestigial | Remove (per existing code comment) |
| Personal Care › Support Groups | Vestigial | Remove |

This is the lowest-effort recommendation in the audit. A code search for each taxonomy name in the chatbot's query templates and `_DETAIL_TO_TAXONOMY_NARROWING` map, plus removal where present, is the entire scope. Should be a small Phase C ticket.

Note: a separate question is whether Streetlives should remove these from the taxonomy table itself. That is data-team scope, not chatbot scope.

---

## XII. Recommendations: discrete tickets

The audit findings translate into ten discrete tickets, all out-of-scope for the in-flight PR but documented here for sequencing.

### Streetlives data-team coordination

**Ticket A: Tagging-debt cleanup pass**
Owner: Streetlives data team
Scope: Re-tag the ~1,105 `Other service` parent-direct services where the service name unambiguously matches an existing leaf (Education, Benefits, Immigration Services, Case Workers). Heuristic-driven, can be partially automated.
Estimated effort: 1-2 weeks of data-team time.
Impact: 200-400 services move from parent to leaf, eliminating most chatbot tagging-debt workarounds.

**Ticket B: New leaf taxonomies for missing-cluster categories**
Owner: Streetlives data team (with chatbot-team input)
Scope: Add `Other service › Recovery Support`, `Other service › Re-entry`, and consider `Personal Care › Parenting` as a relabel/expansion of Community Services. Then re-tag relevant parent-direct services.
Estimated effort: 1 week of data-team time.
Impact: 60-90 services gain a specific leaf; chatbot can route to them without name-pattern fallback.

### Phase B: Top-level service_type promotions

**Ticket C: Promote `education` to top-level service_type**
Scope: New service_type, query template, expected_taxonomy_names entry. Move existing education keywords (ESL, GED, adult education, literacy, computer classes, digital literacy) from `service_type=other` to `service_type=education`. Add description-regex narrowings for sub-types. Update eval scenarios.
Estimated effort: ~1 day.
Acceptance: existing eval suite passes; Recording 13 ESL ask routes correctly.

**Ticket D: Promote `benefits` to top-level service_type**
Scope: Same shape as Ticket C. Consolidates benefits-related keywords.
Estimated effort: ~1 day.
Acceptance: end user 3's Section 8 / voucher gap addressed; existing eval passes.

**Ticket E: Promote `case_management` to top-level service_type**
Scope: Same shape as Ticket C. Smaller keyword set.
Estimated effort: ~0.5 day.
Acceptance: Olive's case-management gap addressed.

**Ticket F: Promote or sub-narrow `immigration` (decision pending)**
Scope: Decide between full promotion (own service_type) or sub-narrowing of `legal`. Implement chosen path.
Estimated effort: ~0.5-1 day.
Acceptance: alphabetical-sample immigration services route correctly.

### Phase C: Sub-narrowing and routing-layer improvements

**Ticket G: Shelter sub-narrowing pass**
Scope: Fill the 20-children / 0-narrowings gap. Implement narrowings for population-specific shelter children (Youth, LGBTQ Young Adult, Senior, Veterans, Families) and shelter-mode children (Drop-in Center, TIL, Warming Center). Gate Residential Recovery to substance-use context only (or remove from default).
Estimated effort: ~3-4 days.
Acceptance: the four eval scenarios listed in Section VIII pass; CREATE Inc. shelter-bug user-testing case is fixed.

**Ticket H: Population-modifier slot extraction**
Scope: Extract population (senior, veteran, lgbtq, justice-impacted, disability, immigrant, family) as a separate slot from service_type. Use it as a re-rank / narrowing signal orthogonal to the service-type query.
Estimated effort: ~2-3 days.
Acceptance: Theme 8 user-testing demand addressed; multi-population scenarios work.

**Ticket I: Adjacent-service / near-miss handling**
Scope: When a search returns zero or few results, surface adjacent services or "near miss" suggestions. Koula's notary case is the canonical example.
Estimated effort: ~2-3 days.
Acceptance: Koula's notary scenario routes to libraries / courthouses; pregnancy-test scenario routes to urgent care.

**Ticket J: Vestigial taxonomy cleanup**
Scope: Remove the six zero-service taxonomies (Pets, Mobile Food Truck, Cooling Center, Intake, Supportive Housing, Personal Care › Support Groups) from any default `taxonomy_names` lists or narrowing maps.
Estimated effort: ~0.5 day.
Acceptance: code search returns no remaining references.

**Ticket L: Mental Health exclusion from `medical` service_type**
Scope: Update `medical` service_type's query template to exclude `Mental Health` from its taxonomy_names list (matching YourPeer's pattern via `filter_services_by_name`). This closes the medical/mental_health overlap where queries like "I need a doctor" surface mental health services.
Estimated effort: ~0.5 day.
Acceptance: medical eval scenarios no longer surface mental-health-tagged services; mental_health eval scenarios still work.

### Cross-cutting: tagging-debt mitigation

**Ticket K: Service-name pattern matching as fallback**
Scope: Add a fallback layer to the query templates that supplements leaf-tagged results with parent-tagged services whose `service.name` matches expected patterns. Implement for Education, Benefits, Immigration, Case Workers, Recovery Support.
Estimated effort: ~2 days.
Acceptance: parent-tagged services begin surfacing in relevant queries; absorbed automatically as Streetlives ticket A progresses.

### Sequencing recommendation

Tickets A, J, L, and C are independent and can be parallelized.
Ticket K can start before Ticket A completes (gracefully absorbs tagging-debt repair).
Tickets D, E, F should follow C (similar pattern, build muscle on the first promotion).
Ticket G is independent of B/C and addresses a distinct architectural gap.
Tickets H and I are downstream of B and G respectively.

A reasonable two-month plan is: weeks 1-2 (A, J, L, C, K start), weeks 3-4 (D, E, G start), weeks 5-6 (F, H), weeks 7-8 (I, polish, eval validation).

---

## Appendix A: Full taxonomy reference (alphabetical by parent)

A complete listing of all 69 taxonomies, organized by top-level branch, with service counts. This serves as the canonical reference for any future routing work.

```
Clothing (parent-direct: 15, subtree: 94)
├── Baby Supplies                                       5
├── Clothing Pantry                                    69
├── Coat Drive                                          1
├── Interview-Ready Clothing                            1
├── Professional Clothing                               1
└── Thrift Shop                                         2

Food (parent-direct: 95, subtree: 1,048)
├── Appliances                                          1
├── Brown Bag                                          14
├── Farmer's Markets                                    3
├── Food Benefits                                       6
├── Food Delivery / Meals on Wheels                     4
├── Food Pantry                                       730
├── Mobile Food Truck                                   0  *vestigial
├── Mobile Market                                      13
├── Mobile Pantry                                       4
├── Mobile Soup Kitchen                                11
└── Soup Kitchen                                      167

Health (parent-direct: 577, subtree: 782)
├── General Health                                     57
├── Mental Health                                     128
├── Substance Use Treatment                            12
└── Support Groups                                      8

Other service (parent-direct: 1,105, subtree: 1,413)
├── Benefits                                           32
├── Case Workers                                       28
├── Education                                         101
├── Employment                                         35
├── Free Wifi                                           8
├── Immigration Services                                2
├── Internship                                          3
├── Legal Services                                     92
├── Mail                                                6
├── Pets                                                0  *vestigial
└── Taxes                                               2

Personal Care (parent-direct: 11, subtree: 105)
├── Activities                                          2
├── Baby                                                3
├── Community Services                                  4
├── Gym                                                 1
├── Haircut                                             5
├── Hygiene                                             1
├── Laundry                                            18
├── Restrooms                                           4
├── Shower                                             31
├── Support Groups                                      0  *vestigial
└── Toiletries                                         25

Shelter (parent-direct: 18, subtree: 104)
├── Assessment                                          1
├── Cooling Center                                      0  *vestigial
├── Crisis                                             13
├── Drop-in Center                                      6
├── Families                                            3
├── Housing Lottery                                     1
├── Intake                                              0  *vestigial
├── LGBTQ Young Adult                                   2
├── Referral                                            6
├── Residential Recovery                                2
├── Safe Haven                                          1
├── Senior                                              2
├── Single Adult                                       38
├── Supportive Housing                                  0  *vestigial
├── Transitional Independent Living (TIL)               3
├── Veterans                                            1
├── Veterans Short-Term Housing                         2
├── Warming Center                                      1
└── Youth                                               4
```

---

## Appendix B: Audit query log

The eight SQL queries used in this audit, in execution order. All are read-only.

### Query 1 — Full taxonomy tree with service counts

```sql
SELECT
  COALESCE(tp.name, '— ROOT —') as parent,
  t.name as taxonomy,
  COUNT(DISTINCT st.service_id) as n_services,
  COUNT(DISTINCT t2.id) as n_children
FROM taxonomies t
LEFT JOIN taxonomies tp ON t.parent_id = tp.id
LEFT JOIN taxonomies t2 ON t2.parent_id = t.id
LEFT JOIN service_taxonomy st ON st.taxonomy_id = t.id
GROUP BY parent, t.name
ORDER BY parent, t.name;
```

### Query 2 — Top-level taxonomy rollup

```sql
SELECT
  t.name as top_level_taxonomy,
  COUNT(DISTINCT t2.id) as n_children,
  COUNT(DISTINCT st_direct.service_id) as n_direct_services,
  (SELECT COUNT(DISTINCT st2.service_id)
   FROM service_taxonomy st2
   JOIN taxonomies tt ON st2.taxonomy_id = tt.id
   WHERE tt.id = t.id OR tt.parent_id = t.id) as n_total_services_in_subtree
FROM taxonomies t
LEFT JOIN taxonomies t2 ON t2.parent_id = t.id
LEFT JOIN service_taxonomy st_direct ON st_direct.taxonomy_id = t.id
WHERE t.parent_id IS NULL
GROUP BY t.id, t.name
ORDER BY n_total_services_in_subtree DESC;
```

### Query 3 — Cross-branch tagged services (re-issued without CTE)

```sql
SELECT
  s.name,
  string_agg(DISTINCT COALESCE(tp.name, t.name), ', ') as top_levels,
  COUNT(DISTINCT COALESCE(tp.name, t.name)) as n_top_levels
FROM services s
JOIN service_taxonomy st ON st.service_id = s.id
JOIN taxonomies t ON t.id = st.taxonomy_id
LEFT JOIN taxonomies tp ON tp.id = t.parent_id
GROUP BY s.id, s.name
HAVING COUNT(DISTINCT COALESCE(tp.name, t.name)) > 1
ORDER BY n_top_levels DESC, s.name
LIMIT 50;
```

Result: 0 rows. No services span multiple top-level branches.

### Query 4 — Orphan service count (re-issued with CASE WHEN)

```sql
SELECT
  SUM(CASE WHEN st.id IS NULL THEN 1 ELSE 0 END) as orphan_count,
  COUNT(*) as total_services,
  ROUND(100.0 * SUM(CASE WHEN st.id IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) as orphan_pct
FROM services s
LEFT JOIN service_taxonomy st ON st.service_id = s.id;
```

Result: 0 / 3,547 / 0.0%. Note: the `total_services` value of 3,547 is inflated by the LEFT JOIN — the actual service count is 3,546 (one service has 2 taxonomy rows, contributing an extra joined row). A reissued query (`SELECT COUNT(*) FROM services`) returned 3,546 directly. The orphan_count of 0 remains correct.

### Query 5 — Parent_id / parent_name drift sanity check

```sql
SELECT
  COUNT(*) FILTER (WHERE t.parent_name IS NULL AND t.parent_id IS NOT NULL) as id_set_name_missing,
  COUNT(*) FILTER (WHERE t.parent_name IS NOT NULL AND t.parent_id IS NULL) as name_set_id_missing,
  COUNT(*) FILTER (WHERE t.parent_name IS NOT NULL
                    AND t.parent_id IS NOT NULL
                    AND t.parent_name != tp.name) as name_id_mismatch,
  COUNT(*) as total_taxonomies
FROM taxonomies t
LEFT JOIN taxonomies tp ON tp.id = t.parent_id;
```

Result: 0 / 0 / 0 / 69. No drift.

### Query 6 — Sample of Other-service parent-direct service names

```sql
SELECT s.name as service_name
FROM services s
JOIN service_taxonomy st ON st.service_id = s.id
JOIN taxonomies t ON t.id = st.taxonomy_id
WHERE t.name = 'Other service' AND t.parent_id IS NULL
ORDER BY s.name
LIMIT 100;
```

### Query 7 — Word-frequency analysis of Other-service parent-direct names

```sql
WITH parent_other_services AS (
  SELECT s.name as service_name
  FROM services s
  JOIN service_taxonomy st ON st.service_id = s.id
  JOIN taxonomies t ON t.id = st.taxonomy_id
  WHERE t.name = 'Other service' AND t.parent_id IS NULL
)
SELECT
  word,
  COUNT(*) as freq
FROM (
  SELECT lower(unnest(string_to_array(service_name, ' '))) as word
  FROM parent_other_services
) words
WHERE length(word) > 3
  AND word NOT IN ('and', 'the', 'for', 'with', 'from', 'this', 'that',
                    'services', 'service', 'program', 'programs')
GROUP BY word
HAVING COUNT(*) >= 5
ORDER BY freq DESC
LIMIT 50;
```

### Query 8 — Distribution within Other-service subtree

```sql
SELECT
  COALESCE(child.name, '— Other service (parent direct, no child) —') as taxonomy,
  COUNT(DISTINCT s.id) as n_services
FROM services s
JOIN service_taxonomy st ON st.service_id = s.id
JOIN taxonomies t_root ON t_root.id = st.taxonomy_id
LEFT JOIN taxonomies child
  ON child.id = t_root.id AND child.parent_id IS NOT NULL
WHERE
  t_root.name = 'Other service' AND t_root.parent_id IS NULL
  OR (t_root.parent_id IS NOT NULL AND
      EXISTS (SELECT 1 FROM taxonomies p
              WHERE p.id = t_root.parent_id AND p.name = 'Other service'))
GROUP BY child.name
ORDER BY n_services DESC;
```

### Probe queries

Three smaller probe queries were also run to verify dataset integrity:

- Total service count: `SELECT COUNT(*) FROM services` → 3,546
- Multi-taxonomy services: `SELECT s.name, COUNT(DISTINCT t.id) FROM services s JOIN service_taxonomy st ... GROUP BY s.id, s.name HAVING COUNT > 1` → 1 row (Job Readiness, 2 taxonomies)
- Service-taxonomy / service-taxonomies disambiguation: `SELECT * FROM service_taxonomies` → ERROR: relation does not exist (singular form is canonical)

---

## Appendix C: Open questions

The audit's original open questions, with status updates after inspection of YourPeer's `src/components/common.ts` (May 2026).

1. **Is the YourPeer frontend's category structure documented somewhere?** ✅ **Resolved.** The canonical structure is the `CATEGORIES` array in `common.ts`: nine top-level categories matching the chatbot's nine service_types one-to-one. The mapping to DB taxonomies is `CATEGORY_TO_TAXONOMY_NAME_MAP`, also in `common.ts`. Sub-routes are defined per category via `*_PARAM_*_VALUE` constants; full sub-category set is documented in Section VIII (shelter), Section II (per-category), and is exhaustive. YourPeer narrows only 4 of 9 categories (shelter, food, clothing, personal-care). The chatbot can copy this set for the first wave of sub-narrowings and extend further only where eval-driven need exists.

2. **What is the "right" home for parenting / family services?** ⚠️ **Partially resolved — open question for Streetlives data team.** YourPeer's frontend has no parenting category. Personal Care › Community Services (4 services) is the closest leaf in the DB but undersized. A new leaf taxonomy is the cleanest solution; Section X-Ticket-B captures this as data-team scope.

3. **Should Mental Health become its own top-level taxonomy in the database?** ✅ **Resolved — already is, in practice.** Both YourPeer's frontend and our chatbot treat Mental Health as a top-level user-facing category. The DB has it as a child of Health, but the `CATEGORY_TO_TAXONOMY_NAME_MAP` in YourPeer's frontend already routes `mental-health` directly to the `Mental Health` taxonomy name. No DB structural change needed; the existing parent-child relationship works for both products.

4. **Is the `Health` parent-direct count of 577 services another tagging-debt situation?** ⚠️ **Deferred — not blocking.** `common.ts` doesn't address this. A Q7-style word-frequency analysis on Health parent-direct service names would clarify whether the 577 services are genuine generic-health entries or undifferentiated tagging-debt analogous to the Other-service-parent-direct cluster. This is now flagged as a deferred follow-up in `design/STREETLIVES_DATA_GOTCHAS.md` §I; it does not block Phase B/C work because the chatbot already routes `medical` to the Health subtree (parent + 4 children), which captures these 577 either way. Worth running before any future medical sub-narrowing work.

5. **What population modifiers should the chatbot extract?** ⚠️ **Reframed.** YourPeer's frontend currently has no population-modifier filter dimension at all (only AGE_PARAM as numeric, plus REQUIREMENT_PARAM for referral-letter / registered-client). The DB has population data on `Eligibilities` and `population_served`, but it isn't surfaced. If the chatbot adds population modifiers, it will be pioneering this dimension product-wide. Section VI now reflects this. The canonical list of populations is still TBD; recommended values based on DB taxonomies and user-testing demand: youth, senior, veteran, lgbtq, family, justice-impacted, immigrant, disability.

### New open questions arising from `common.ts` inspection

6. **Does `Advocates / Legal Aid` exist as a taxonomy in the DB?** ✅ **Resolved via existing audit.** The April 2026 `QUERY_PARITY_AUDIT.md` already investigated this in its "Data Quality Notes (for Streetlives team)" table: **`Advocates / Legal Aid` exists in the DB but has 0 services tagged**. It is a phantom taxonomy — included in YourPeer's `TAXONOMY_CATEGORIES` array and in YourPeer's "Other Services" client-side exclusion filter (via `setIntersection` in `map_gogetta_to_yourpeer`), but matches zero actual services. Our Q1 / Q13 didn't surface it because no `service_taxonomy` rows reference it; the GROUP BY collapsed an empty-aggregate row. This finding is documented in `design/STREETLIVES_DATA_GOTCHAS.md` §I. No action needed; no Phase B work touches this taxonomy.

7. **How does the Streetlives API handle subtree expansion?** ✅ **Resolved.** Inspection of `streetlives-api-service.ts` confirms the API does NOT walk parent_id trees. The API filters by exact taxonomy ID match. Subtree expansion is the frontend's responsibility — both YourPeer and our chatbot must enumerate parent and children explicitly when querying a parent taxonomy. The chatbot's existing `EXPECTED_TAXONOMY_NAMES` lists with all relevant children are correct and should not be simplified.

### Newly identified follow-ups from `streetlives-api-service.ts`

8. **YourPeer's Youth shelter sub-route is non-functional.** Reported separately to YourPeer team; documented in Section VIII. The chatbot can implement Youth narrowing as a net-new improvement.

9. **The `clothingOccasion` taxonomySpecificAttribute filter doesn't appear to work in production data.** YourPeer's source has a `FIXME` noting this. Worth a separate inspection of how many clothing services have the attribute populated, and whether other categories have analogous unused taxonomy-specific attributes the chatbot could leverage.

10. **The frontend's `shelter` query parameter is plumbed but unused.** `fetchLocationsData` accepts a `shelter` parameter but never appends it to the query URL. Combined with finding 8, this confirms shelter sub-narrowing is partially-implemented at the frontend level. The chatbot's Phase C work should not assume any shelter sub-narrowing is happening server-side; all narrowing is via taxonomy ID enumeration.

---

## Document history

- **May 2026 — initial audit**, based on production database snapshot (3,546 services, 69 taxonomies). Findings synthesized from eight SQL queries plus the May 2026 user-testing analysis (26 sessions, 16 themes).
- **May 2026 — YourPeer source-code review pass 1: `common.ts`.** Section VII rewritten and Section VI substantially updated. Confirmed the chatbot's nine-bucket scheme is identical to YourPeer's `CATEGORIES` array. Established that YourPeer narrows shelter to 3 sub-routes (not 20), revising Section VIII. Documented that YourPeer has no population-modifier filter dimension, revising Section VI. Resolved 3 of 5 original open questions; added 2 new ones (Q6: `Advocates / Legal Aid` taxonomy existence; Q7: API subtree expansion behavior).
- **May 2026 — YourPeer source-code review pass 2: `streetlives-api-service.ts`.** Resolved Q7 (API does not do subtree expansion; frontend enumeration is required and the chatbot's existing pattern is correct). Discovered YourPeer's Youth shelter sub-route is non-functional (Section VIII updated). Discovered YourPeer post-filters Mental Health from Health Care results (added Ticket L for chatbot to mirror this exclusion). Discovered the `clothingOccasion` taxonomySpecificAttributes filter exists in API but is not currently effective in production data (Section II clothing note added). Three new follow-ups added to Appendix C (Q8-Q10).
- **May 2026 — math reconciliation pass.** Discovered Q4's reported `total_services = 3,547` was a LEFT JOIN row-count artifact; actual count is 3,546. Updated headline numbers throughout. Discovered two additional minor data-quality observations (two services named `Job Readiness`, two taxonomy rows named `Drop-in Center` under Shelter); these are now flagged in Section I for the Streetlives data team. No substantive recommendations changed.
- **May 2026 — open-question closures + gotchas doc cross-reference.** Closed Appendix C Q4 (Health parent-direct word-frequency) as deferred follow-up — does not block Phase B/C since `medical` already routes to Health subtree. Closed Appendix C Q6 (`Advocates / Legal Aid`) — resolved via existing `QUERY_PARITY_AUDIT.md` Data Quality Notes table: phantom taxonomy with 0 services, no action needed. Both findings are now also captured in the new `design/STREETLIVES_DATA_GOTCHAS.md` reference doc, which consolidates Streetlives DB / API / YourPeer-frontend / chatbot-routing peculiarities into a single living catalog.
- **May 2026 — Pattern B prompt fixes validated.** Probe-ran the two target scenarios (`peer_young_mom_multiple_needs`, `peer_escaped_abuse_child_next_steps`) with the prompt-only Pattern B fix deployed. Transcript inspection confirmed the LLM extraction is working correctly: young-mom now extracts all 4 services (shelter + clothing/baby supplies + food + medical); DV next-steps now extracts shelter + legal in DV context. Both scenarios passed (≥4.0). Two follow-ups identified: (a) the young-mom expectation now correctly prefers co-located search over queue when a single location offers all four services — judge feedback was re-framed accordingly in the eval scenario notes; (b) crisis handlers silently drop `additional_services` from the LLM extraction (the legal queue was never offered to the DV user despite being correctly extracted) — filed in `design/STREETLIVES_DATA_GOTCHAS.md` §VII as a known limitation requiring dispatch-layer work, not blocking Pattern B prompt landing.
