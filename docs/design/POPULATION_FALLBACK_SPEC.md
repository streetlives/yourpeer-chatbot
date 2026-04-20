# Population-Critical Fallback — Design Spec

**Date**: April 16, 2026 (spec), April 17, 2026 (scope change to citywide)
**Status**: Shipped. See §Scope for the Option B change.
**Priority**: P0 — directly affects the Cornell sample query Q1

---

## Status (Apr 17, 2026) — Option B shipped

The fallback is now **citywide**, not borough-scoped as originally specced.

**Why the change.** A Far Rockaway (Queens) GPS user asking for LGBTQ young
adult shelter got nothing. Under the original borough-scoped design, the
fallback resolved to Queens and searched Queens — but Ali Forney Center,
the only LGBTQ YA shelter in NYC, is in Manhattan. The rare-taxonomy
services this fallback is designed to surface are sparse by definition,
and scoping them to one borough defeats the purpose for ~80% of NYC.

**What's different.**
- `_run_population_fallback` no longer calls `_resolve_borough_from_location`
- The fallback query is issued with `location=None` (no borough filter)
- An unresolvable input location no longer blocks the fallback — rare
  taxonomies are returned citywide regardless
- The "further away" framing in the user-facing note is unchanged — it's
  still accurate (often more so — Ali Forney from Far Rockaway is further
  away than any same-borough fallback ever was)

**What's the same.**
- Trigger conditions: still fires only when the main query returned
  results AND none of them carry the rare taxonomy the user qualifies for
- Per-card `fallback_population` attribution
- Age filter still applied (17-year-old still shouldn't see adult-only)
- Gender filter still dropped
- Proximity still dropped
- `_POPULATION_FALLBACK_MAX` cap still 3

**Regression guard.** `TestCrossBoroughFallback.test_fallback_location_none_regardless_of_input`
parameterizes across every location-input pattern (Manhattan, soho,
Brooklyn, Staten Island, "the bronx", "somewhere unknown", None) and
asserts `location=None` on the fallback query for all of them. If this
ever fails, someone re-introduced borough scoping.

---

## Scope

The fallback is **citywide for rare populations only**. The full gate is:

1. Service type is `shelter`
2. Main query returned results but NOT via relaxation
3. No colocated-success path fired
4. User qualifies for a rare population (LGBTQ, youth 16–24, senior 62+,
   or veteran) per `_compute_rare_population_taxonomies`
5. None of the main results are tagged with any of the user's rare
   taxonomies

When all five conditions hold, the fallback runs with:
- `location=None` (citywide)
- `latitude=None, longitude=None` (no proximity)
- `gender=None` (rare-population services often serve multiple genders)
- `family_status=None, service_detail=None, populations=None`
- `age=<user's age>` (retained — protects minors from adult-only)
- `taxonomy_override=<user's rare taxonomies>`
- `max_results=3` (cap on how many extras we surface)

---

## Problem

A 21-year-old LGBTQ person in Soho asks for shelter. The chatbot's shelter enrichment
correctly adds `lgbtq young adult`, `drop-in center`, and `crisis` to the taxonomy list.
The strict query returns 5 generic shelters within 1,600m of Soho. None are tagged
`LGBTQ Young Adult`. Ali Forney Center (the primary LGBTQ youth shelter in NYC) is
3.6 km away in Midtown — outside the proximity radius. Because the strict query
returned results, the relaxed query never fires. Ali Forney is invisible.

This is a data sparsity problem: only 2 services in the entire NYC database are tagged
`LGBTQ Young Adult`. Population-specific services are rare and geographically sparse.
The proximity filter, which works well for common service types (food: 588 services,
shelter: 18 parent-level), systematically excludes rare population-specific services.

The same problem applies to:
- **Veterans**: 1 service tagged `Veterans`, 1 tagged `Veterans Short-Term Housing`
- **Seniors**: 2 services tagged `Senior`
- **LGBTQ Young Adult**: 2 services
- **DV survivors**: Safe Horizon services tagged `Referral` and `Drop-in Center` may
  be out of proximity range

Generic shelters exist everywhere. Population-specific shelters don't. The current
proximity filter treats them the same.

---

## Solution: Population-Critical Fallback

After the main query returns results, check whether any of them match the
population-specific enrichment taxonomies. If none do, run a secondary
borough-wide query for those taxonomies only, and append the results with a
contextual note.

---

## Design

### When does the fallback fire?

All three conditions must be true:

1. **Shelter enrichment was active**: the query used enrichment taxonomies beyond the
   base 18 (i.e., at least one of: youth, LGBTQ, senior, veteran, DV enrichment fired)
2. **Main results exist**: the strict query returned ≥1 result (if 0 results, the
   relaxed query handles broadening — that's a different code path)
3. **Zero main results match enrichment taxonomies**: none of the returned service
   cards have a `service_taxonomies` value that intersects with the enrichment set

### Which taxonomies are checked?

The enrichment taxonomies added by each population rule in `_apply_shelter_enrichments()`:

| Population | Enrichment taxonomies to check |
|------------|-------------------------------|
| Age 16–24 | `youth` |
| LGBTQ/trans/nonbinary | `lgbtq young adult` |
| Age ≥ 62 | `senior` |
| Veteran | `veterans`, `veterans short-term housing` |
| DV survivor | (no unique taxonomy — uses `drop-in center`, `crisis` which are in default list) |

Note: DV enrichment is excluded from fallback because `drop-in center` and `crisis`
are in the base default list and likely already have results. The fallback is for
taxonomies that are population-specific and rare.

### What does the fallback query look like?

> ⚠️ **Superseded.** The original spec below says borough-wide; the
> shipped implementation is citywide. See §Status above for the
> rationale. The rest of this paragraph is preserved for historical
> context — the non-location parts still apply.

```
Template: shelter
Taxonomy: ONLY the unmatched enrichment taxonomies (e.g., ["lgbtq young adult"])
Location: citywide (no location filter, no proximity)
Filters: hidden, state, age (if set) — NO proximity, NO gender
Max results: 3
```

This is a targeted query: "find the rare-taxonomy services anywhere in
NYC" — not "find all shelters near the user."

### How are fallback results presented?

Appended after main results with a contextual note:

```
[5 main shelter results as normal service cards]

I also found LGBTQ-friendly services further away that may be helpful:

[1-3 fallback service cards]
```

The note varies by population:

| Population | Note prefix |
|------------|------------|
| LGBTQ | "I also found LGBTQ-friendly services further away:" |
| Youth (16-24) | "I also found youth-specific services further away:" |
| Senior (62+) | "I also found senior-specific services further away:" |
| Veteran | "I also found veteran services further away:" |

### What if the fallback also returns 0?

No note shown. The user sees only the main results. This means no population-specific
services exist in the borough at all — the chatbot can't help further.

### What if the fallback results duplicate main results?

Deduplicate by `service_id`. If a fallback result was already in the main results,
skip it.

---

## Implementation

### Location in the code

This lives in `_execute_and_respond()` in `backend/app/services/chatbot/execution.py` (post-Phase-3 location; was `chatbot.py` pre-April 2026). It fires AFTER the main query returns results and BEFORE the response is formatted. <!-- drift:ignore: historical chatbot.py reference; package now lives at chatbot/ -->

### Data flow

```
_execute_and_respond()
  │
  ├── Main query: query_services("shelter", location=..., age=..., gender=..., ...)
  │     → main_results (5 service cards)
  │
  ├── IF shelter enrichment was active:
  │     │
  │     ├── Compute enrichment_taxonomies from session state
  │     │   (the taxonomies added by _apply_shelter_enrichments)
  │     │
  │     ├── Check: do any main_results have service_taxonomies ∩ enrichment_taxonomies?
  │     │
  │     └── IF zero matches:
  │           │
  │           ├── Fallback query: query_services("shelter",
  │           │     location=borough_only,    # drop proximity
  │           │     taxonomy_override=enrichment_taxonomies,  # only population-specific
  │           │     age=...,                  # keep age filter
  │           │     max_results=3)
  │           │
  │           ├── Deduplicate against main_results
  │           │
  │           └── Append to response with contextual note
  │
  └── Format and return response
```

### New parameters needed

`query_services()` needs a way to override the taxonomy list. Currently it always
uses the template's `default_params["taxonomy_names"]`. Options:

**Option A: New parameter `taxonomy_override`**
```python
def query_services(service_type, ..., taxonomy_override=None):
    if taxonomy_override:
        user_params["taxonomy_names"] = taxonomy_override
    # ... rest of query
```

**Option B: Call `build_query()` directly**
Skip `query_services()` and call `build_query("shelter", params)` + 
`execute_service_query()` directly with the custom taxonomy list.

**Recommendation**: Option A is cleaner and preserves all the existing location
resolution, age filtering, and result formatting. The override just swaps the
taxonomy list.

### Session state needed

The enrichment function must record WHICH enrichment taxonomies it added, so
`_execute_and_respond` can check later. Currently `_apply_shelter_enrichments()`
modifies the taxonomy list in place but doesn't record what it added.

Add to session state:
```python
existing["_enrichment_taxonomies"] = ["lgbtq young adult"]  # set by enrichment
```

Or, compute it at check time by comparing the actual taxonomy list against the
base default (simpler, no session state needed):

```python
base_default = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
actual = set(params["taxonomy_names"])
enrichment_added = actual - base_default
```

**Recommendation**: Compute at check time (no session state). The base default
list is stable (pinned at 18 by tests), so the diff is reliable.

### Taxonomy check on results

Each service card has `service_taxonomies` (an array of taxonomy names from the
`also_available` subquery or from the main taxonomy join). Check if any card's
taxonomies intersect with the enrichment set:

```python
enrichment_set = {"lgbtq young adult", "youth", ...}  # computed above
has_population_match = any(
    enrichment_set & set(card.get("service_taxonomies", []))
    for card in main_results
)
```

If the service cards don't currently include `service_taxonomies` as a field,
it needs to be added to the SQL SELECT. Check the current card format.

---

## Edge Cases

### User searches with no location
No proximity filter active → no geographic exclusion → fallback unnecessary.
The main query already searches borough-wide or city-wide.

### User searches with browser geolocation
Proximity filter is active (lat/lon + 1600m radius). Fallback drops
location entirely (citywide — see §Status). This is the primary use
case and the reason for the citywide scope change: a GPS user in
Far Rockaway would otherwise miss Manhattan-only rare services.

### Multiple enrichments active (e.g., trans + youth + veteran)
Compute the full enrichment set. Check if ANY enrichment taxonomy has a match.
If some do and some don't, run fallback only for the unmatched ones.

Example: trans 19-year-old veteran. Enrichment adds `youth`, `lgbtq young adult`,
`veterans`, `veterans short-term housing`, `drop-in center`, `crisis`. If main
results include a `drop-in center` but no `lgbtq young adult` or `veterans`,
fallback searches for `["lgbtq young adult", "veterans", "veterans short-term housing"]`.

### Fallback returns the same location as main results but different service
This is correct behavior. A location might have a `Shelter` service (in main results)
AND an `LGBTQ Young Adult` service (in fallback) — they're different services at the
same location. The user sees both.

### Non-shelter templates
This feature is shelter-only for now. Other templates don't have population-specific
enrichment. If enrichment is added to other templates in the future, the fallback
pattern can be reused.

---

## Acceptance Criteria

1. "21, LGBTQ, in Soho, need a bed tonight" surfaces Ali Forney Center in the
   fallback section with a note like "I also found LGBTQ-friendly services
   further away"
2. The 5 main results remain unchanged (generic shelters near Soho)
3. Fallback does NOT fire when enrichment taxonomies have matches in main results
4. Fallback does NOT fire for non-enrichment queries (generic shelter search)
5. Fallback results are deduplicated against main results
6. Fallback shows at most 3 additional service cards
7. Fallback note uses population-appropriate language
8. All 249 regression tests still pass
9. All 637 core suite tests still pass

---

## Estimated Effort

- **query_services taxonomy_override param**: 30 min
- **Enrichment taxonomy tracking**: 30 min
- **Fallback logic in _execute_and_respond**: 1-2 hours
- **Response formatting with contextual note**: 30 min
- **Tests**: 1 hour
- **Total**: 3-4 hours

---

## Future Extensions

- **Distance display**: show "2.3 miles away" on fallback cards so users know
  how far they need to travel
- **Transit directions**: deep link to Google Maps transit directions from
  user's location to the fallback service
- **Other templates**: if food or medical services get population-specific
  taxonomies (e.g., `hasHivNutrition`), reuse the same fallback pattern
- **Configurable radius threshold**: instead of hard 1600m cutoff, use a
  per-population threshold (e.g., LGBTQ services worth traveling further for)
