# NYC Boundary Audit — PostGIS, Neighborhood, and Borough Grouping

**Date**: April 17, 2026
**Context**: User feedback reported a service categorized as "Manhattan" that
was actually in the Bronx. This audit examines every place in the codebase

> **📎 Post-Phase-3 location note (April 2026)**: this audit was written before
> the `chatbot.py` → `services/chatbot/` package decomposition. Body references
> to `chatbot.py` still apply conceptually; the helpers they describe now live
> at these current paths:
> - `_CITY_TO_BOROUGH` and `_BOROUGH_TO_PRIMARY_CITY` → `backend/app/services/chatbot/context.py`
> - `_BOROUGH_CENTROIDS` + nearest-neighborhood table → `backend/app/services/chatbot/context.py`
> - Population-critical fallback (`_run_population_fallback`) → `backend/app/services/chatbot/execution.py`
> - Borough polygon validation → `backend/app/rag/boundaries.py` + `backend/app/rag/data/nyc_boroughs.geojson`

that makes borough/neighborhood decisions, identifies the root cause, surveys
industry standards, and proposes a fix strategy.

**Scope**: `backend/app/rag/query_executor.py`,
`backend/app/rag/query_templates.py`, `backend/app/rag/__init__.py`, and the
`_BOROUGH_CENTROIDS` / `_build_neighborhood_borough_table` helpers I added in
`backend/app/services/chatbot.py` during the Population-Critical Fallback
work.

---

## Status (as of Apr 17, 2026)

**Parts 1 and 2 shipped.**

- ✅ **Part 1 — `pa.borough` cleanup**: removed `FILTER_BY_BOROUGH` (the
  column doesn't exist in prod; filter errored on every borough search).
  Borough filtering now goes via `pa.city = ANY(city_list)` exclusively.
  Error log storm stopped.

- ✅ **Part 2 — Geographic borough validator**: vendored NYC DCP Borough
  Boundaries GeoJSON (~195KB, simplified to 0.0001° tolerance → 0 detectable
  classification disagreements vs source) at
  `backend/app/rag/data/nyc_boroughs.geojson`. New `app.rag.boundaries`
  module provides `borough_from_coords(lat, lon)`. The base SELECT now
  projects `ST_Y(l.position)` and `ST_X(l.position)` as
  `latitude`/`longitude`. `query_executor._annotate_geographic_borough`
  compares each returned service's geographic borough against the borough
  inferred from `pa.city` and logs mismatches at WARNING level, with
  both boroughs captured for upstream triage. Cards are tagged with
  `geographic_borough` and `borough_mismatch` fields for downstream code
  to surface or filter as policy evolves.

  Test suite: 37 tests for `boundaries`, 42 for the validator, 5 for the
  `format_service_card` lat/lon additions.

- 📋 **Part 3 — File upstream on streetlives-api**: still recommended.
  The mitigation above is client-side; the real fix is to add a geography-
  derived `borough` column (or PostGIS-backed view) on the Streetlives
  schema so every consumer benefits.

- 📋 **Further improvements** (open): NTA-based neighborhood expansion to
  fix the Staten Island=0 / Bronx=5 / Queens=11 coverage gaps; radius
  tuning (1600m → 1000m); escalating mismatch policy from log-only to
  badge/filter once we have real prod data on mismatch frequency.

**Below is the original audit in full** — preserved as rationale for what
shipped and roadmap for what's left.

---

## TL;DR (original)

The reported bug is almost certainly an upstream data quality issue in the
Streetlives PostgreSQL database (`pa.borough` disagrees with the actual
coordinates of `l.position`). Our chatbot code trusts `pa.borough`
implicitly. We can mitigate this client-side by adding a PostGIS
`ST_Within`-based borough check against the authoritative NYC DCP borough
polygon dataset — either by embedding it in the chatbot backend or by
proposing the columns upstream to Streetlives. Separately, our neighborhood
coordinate coverage has significant gaps (Staten Island: 0, Bronx: 5,
Queens: 11) that are worth addressing regardless.

---

## 1. Current State Inventory

### 1.1 Boundary-related constants

| Constant | Location | Size | Purpose |
|---|---|---|---|
| `NYC_LOCATION_ALIASES` | `query_executor.py:401` | 68 entries | Maps lowercase neighborhood / borough aliases → canonical DB `pa.city` value |
| `NEIGHBORHOOD_CENTERS` | `query_executor.py:566` | 62 entries | Maps neighborhood → `(lat, lon)` for PostGIS `ST_DWithin` proximity search |
| `_BOROUGH_KEYS` | `query_executor.py:479` | 6 values | Set of strings recognized as "this is a borough, not a neighborhood" |
| `_BOROUGH_TO_PRIMARY_CITY` | `query_executor.py:485` | 5 entries | Canonical borough name → primary `pa.city` value used for that borough |
| `BOROUGH_TO_CITIES` | `query_executor.py:533` | 6 derived | Reverse map built at import from `NYC_LOCATION_ALIASES` |
| `_CITY_TO_BOROUGH` | `chatbot.py` (added this sprint) | 5 entries | Inverse of `_BOROUGH_TO_PRIMARY_CITY`, used by the population-critical fallback |
| `_BOROUGH_CENTROIDS` / nearest-neighborhood table | `chatbot.py` (added this sprint) | 59 neighborhoods + 3 SI anchors | Reverse-geocodes GPS to borough for the population fallback |
| `DEFAULT_NEIGHBORHOOD_RADIUS_METERS` | `query_executor.py:640` | `1600` (≈1 mile) | Radius for `ST_DWithin` proximity searches |

### 1.2 How a search actually flows

For user input **"Soho"** (a neighborhood):

1. `is_borough("Soho")` → `False`
2. `get_neighborhood_center("soho")` → `(40.7233, -73.9985)`
3. `normalize_location("soho")` → `"New York"` (DB city value for Manhattan)
4. `get_borough_city_names("New York")` → list of every Manhattan alias lowercased
5. Query params set: `lat=40.7233`, `lon=-73.9985`, `radius_meters=1600`, `city="New York"`, `city_list=[manhattan aliases]`, `_borough_city_list=[same]`
6. SQL `WHERE`: `ST_DWithin(l.position, point, 1600) AND pa.city='new york' AND pa.city = ANY(manhattan_list) AND ...`
7. If zero results → relaxed query drops `lat/lon/radius` and relies on `city_list`

For user input **"Manhattan"** (a borough):

1. `is_borough("Manhattan")` → `True`
2. Query params set: `borough="Manhattan"`, `city_list=[manhattan aliases]`
3. SQL `WHERE`: `LOWER(pa.borough) = 'manhattan' AND pa.city = ANY(manhattan_list) AND ...`

**What is NOT checked anywhere**: the service's actual geographic coordinates
(`l.position`) against the claimed borough. We trust `pa.borough` and
`pa.city` to be truthful.

### 1.3 Filters that could produce boundary errors

| Filter | Fires when | Failure mode |
|---|---|---|
| `FILTER_BY_BOROUGH` (`pa.borough=:borough`) | User named a borough | Fires if `pa.borough` is wrong in the DB; no geographic validation |
| `FILTER_BY_CITY_IN_BOROUGH` (`pa.city = ANY(:city_list)`) | User named a borough or neighborhood | If `pa.city` is wrong (e.g., a Bronx service with `pa.city='New York'`), it sneaks into Manhattan results |
| `FILTER_BY_PROXIMITY` (`ST_DWithin`) | User named a neighborhood with known coords | Crosses water/borough boundaries for 17 of 62 neighborhoods — but usually contained by the city filter above |
| `FILTER_BY_CITY_LIKE` (`pa.city LIKE :city_pattern`) | Relaxed query only | Broadens, so less accuracy-critical |

---

## 2. Coverage Gaps

Breakdown of `NEIGHBORHOOD_CENTERS` entries by borough:

| Borough | Count | Status |
|---|---|---|
| Manhattan | 30 | Good — dense, no gap |
| Brooklyn | 16 | Decent — missing Borough Park, Bensonhurst, Canarsie, Sheepshead Bay, Greenpoint, Midwood, etc. |
| Queens | 11 | **Poor** — missing Forest Hills, Rego Park, Bayside, Howard Beach, Whitestone, Ozone Park, Kew Gardens, Fresh Meadows, Maspeth, Middle Village, Glendale, Queens Village, St. Albans, Rosedale, etc. |
| Bronx | 5 | **Very Poor** — missing Riverdale, Bedford Park, Belmont, Kingsbridge, Castle Hill, Co-op City, Eastchester, Parkchester, Pelham Bay, Throgs Neck, Van Nest, Wakefield, Woodlawn |
| Staten Island | 0 | **Broken** — a user searching for "St. George" or "Tottenville" as a neighborhood gets `get_neighborhood_center` returning `None` → no proximity search fires at all |

The population-critical fallback I added last sprint patched this by
supplementing 3 Staten Island anchors in `_build_neighborhood_borough_table`
for the GPS reverse-geocode use case, but regular
"find food near [St. George]" style searches still fall through to a
city-wide Staten Island search with no proximity narrowing.

---

## 3. Cross-Border Proximity Leak Vectors

`DEFAULT_NEIGHBORHOOD_RADIUS_METERS = 1600` (1 mile). At NYC waterway widths,
this comfortably crosses borough boundaries. Neighborhoods with proximity
circles that extend into adjacent boroughs:

| Neighborhood | Leaks into | Approx. crossing width |
|---|---|---|
| Harlem | Bronx | ~400m (Harlem River at 145th St) |
| East Harlem | Bronx, Queens | ~500m / via Triboro |
| Washington Heights | Bronx | Immediate neighbor |
| Inwood | Bronx | Immediate neighbor |
| Lower East Side | Brooklyn | ~400m to Williamsburg |
| Financial District | Brooklyn | Brooklyn Bridge ~800m |
| Battery Park | Brooklyn | Across Upper Bay |
| UES / Midtown East | Queens | Queensboro Bridge |
| Williamsburg | Manhattan | ~300m |
| DUMBO / Brooklyn Heights | Manhattan | Brooklyn Bridge |
| Greenpoint | Manhattan, Queens | East River + Newtown Creek |
| Red Hook | Manhattan | Brooklyn-Battery Tunnel |
| Astoria | Manhattan, Bronx | ~500m to UES |
| Long Island City | Manhattan | ~400m |
| South Bronx / Mott Haven | Manhattan | Harlem River crossings |

**Total: 17 of 62 neighborhoods (27%) have 1 mi circles crossing borough
lines.** This does NOT necessarily produce wrong results because the
`pa.city = ANY(city_list)` filter usually excludes out-of-borough services
— but **only when `pa.city` is correctly populated**.

---

## 4. Root Cause Analysis of the Reported Bug

User report: "a service categorized as 'Manhattan' was actually in the Bronx."

There are three possible causes, in order of likelihood:

### Cause A (most likely): Upstream data quality in `pa.borough` / `pa.city`

A service has `pa.borough = 'Manhattan'` in the Streetlives DB but its
actual coordinates in `l.position` are in the Bronx. Nothing in our chatbot
— or in YourPeer, per the public repo — cross-validates these. This could
arise from:

- Manual data entry using the Streetlives Street Team Tool (SSTT) where a
  volunteer typed the wrong borough
- An organization headquartered in Manhattan with a service location in the
  Bronx where the data was inherited from the parent org record
- Address geocoding at data-entry time picking the wrong candidate (this
  is a known hazard — NYC has many streets with the same name in different
  boroughs)

**We cannot fix this in chatbot code alone.** But we can *detect* it and
either filter it or log it.

### Cause B (less likely): `pa.borough` is NULL, `pa.city` misleads

If a service's `pa.borough` is NULL but `pa.city` is "New York", and the
user searched for "Harlem" (near the Bronx line), the service could return
via:
- `ST_DWithin` hit (within 1600m of Harlem center, physically in Bronx)
- `pa.city = ANY(manhattan_list)` hit (because "New York" matched)
- No `FILTER_BY_BOROUGH` clause fires (user said "Harlem", not "Manhattan")

But this would display a Bronx-address service, not a "Manhattan-labeled"
one. So it's less consistent with the report.

### Cause C (unlikely but worth noting): Alias mapping is wrong

I audited all 62 `NEIGHBORHOOD_CENTERS` and 68 `NYC_LOCATION_ALIASES`
entries. Every neighborhood maps to the correct borough's primary city.
A few ambiguous ones (Ridgewood straddles Brooklyn/Queens historically but
is canonically Queens; Jamaica is both a neighborhood and a region name)
are correctly handled. **No alias→borough errors found.**

---

## 5. What YourPeer Does

Per the public `streetlives/yourpeer.nyc` README and the
`streetlives/streetlives-api` README:

- **YourPeer frontend** uses `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` for the map
  embed and likely Google Places autocomplete for address input. Location
  is ultimately a lat/lon handoff to the Streetlives API.
- **Streetlives API** requires PostGIS (per README: "Since Streetlives
  relies heavily on geographical data, it requires enabling PostGIS"). It
  uses Joi validation for request params
  (`src/controllers/validation/locations.js`) but — to the best of my
  ability to inspect the public repo without direct file access — does
  NOT do borough polygon validation on returned results.
- **Conclusion**: YourPeer relies on the same `pa.borough` / `pa.city`
  columns we do. If the reported bug reproduces on yourpeer.nyc, it's a
  shared problem, and filing an issue against `streetlives/streetlives-api`
  is warranted.

**There is no YourPeer-specific borough boundary implementation we can
borrow from.** The improvement path is NYC open data, not cross-repo reuse.

---

## 6. Industry Standards & NYC Open Data

NYC has best-in-class open data for this problem — there are several
authoritative, freely-licensed datasets.

### 6.1 NYC DCP Borough Boundaries

- **Dataset**: NYC Department of City Planning, "Borough Boundaries",
  published on NYC Open Data (gthc-hcne).
- **Format**: Shapefile, GeoJSON, or KML download. Water areas excluded
  (clipped to shoreline).
- **Size**: GeoJSON ~2MB uncompressed; simplified variants (1-3k points
  each) under 50KB.
- **Attributes**: `boro_code` (1-5), `boro_name` (Manhattan, Bronx, etc.),
  `shape_area`, `shape_leng`.
- **License**: NYC Open Data / public domain.

This is the authoritative source for "is this lat/lon in Manhattan?"

### 6.2 NYC DCP 2020 Neighborhood Tabulation Areas (NTAs)

- **Dataset**: "2020 Neighborhood Tabulation Areas (NTAs) — Mapped",
  NYC Open Data (4hft-v355 or 9nt8-h7nd).
- **Size**: 262 NTAs across NYC. Each NTA has a stable ID (e.g., "MN0101"
  for Central Park), borough, and polygon.
- **Caveat from DCP**: "NTAs are not intended to definitively represent
  neighborhoods, nor are they intended to be exhaustive." Two NTAs can
  share a colloquial name; one colloquial neighborhood can span multiple
  NTAs.

NTAs are what *NYC government uses* when they need a neighborhood boundary.
Better than any hand-curated alternative because they're stable and maintained.

### 6.3 NYC Planning Labs GeoSearch API

- **Endpoint**: `https://geosearch.planninglabs.nyc/v1/search?text=...`
- **Reverse geocode**: `/v1/reverse?point.lat=...&point.lon=...`
- **Returns**: structured address + PAD data (borough, BBL, BIN, NTA,
  community district, zip, etc.) — canonical NYC address resolution.
- **Cost**: Free, no API key, rate-limited but generous.
- **Built on**: Pelias (open-source geocoder) over NYC's authoritative
  Property Address Directory (PAD).
- **Used by**: ZoLa, Population Fact Finder, and most NYC government web
  apps (per the labs-geosearch-docs README).

This is the single most accurate tool for validating "what borough is this
address/coord in." It replaces the hand-rolled centroid approach entirely
for any code path that needs high accuracy.

### 6.4 NYC GeoSupport / GeoClient (lower relevance here)

- `geosupport` and `geoclient` APIs exist from DoITT and DCP for
  programmatic geocoding. More powerful (street-stretch, BBL lookup,
  elevation) but require API keys and are heavier integration.
- GeoSearch (above) is a friendlier subset and sufficient for our needs.

### 6.5 GeoJSON / topoJSON mirrors on GitHub

For offline / embedded use:
- `codeforgermany/click_that_hood` — simplified NYC boroughs GeoJSON
  (small, good for embedding)
- `nycehs/NYC_geography` — DoHMH-maintained simplified NTAs and boroughs
- `CityOfNewYork/nyc-geo-metadata` — official metadata registry

Any of these can be vendored into the chatbot repo as a static JSON file.

### 6.6 Existing npm / Python packages

- `nycgeo` (R package) — not usable directly (R-only), but its underlying
  data is the same NYC DCP source.
- No mainstream Python package ships NYC boundary polygons directly. The
  pattern is: fetch GeoJSON from NYC Open Data, load via `shapely`, use
  `Polygon.contains(Point(...))`.

---

## 7. Recommendations

### 7.1 Short-term: Defensive check on returned services

**Goal**: Detect and quarantine the "Manhattan-labeled, Bronx-located" bug
class without requiring upstream data fixes.

**Implementation**:

1. Vendor the NYC DCP Borough Boundaries as a simplified GeoJSON
   (~50KB) at `backend/app/rag/data/nyc_boroughs.geojson`.
2. Load polygons into memory at import using `shapely.geometry` (already
   installable).
3. Add a post-query validation step in `query_executor.execute_service_query`:
   for each returned row, if the row has both `pa.borough` AND
   `l.position.lat/lon`, check `polygon[pa.borough].contains(Point(lon, lat))`.
   On mismatch:
   - **Log** a warning with `service_id`, stated borough, actual borough
   - **Filter** the result from the user's view (conservative default)
   - Or flag it in the card with `?borough_mismatch: true` (less aggressive)
4. Expose the mismatch count on the admin audit log so the Community
   Information Specialists can triage upstream corrections.

**Effort**: ~1 day to embed GeoJSON, add shapely dep (already installed),
write the filter, add tests.

**Dependencies added**: `shapely>=2.0` (pure-python fallback exists).

### 7.2 Medium-term: Authoritative borough + NTA at query time

**Goal**: Derive borough and neighborhood from `l.position` directly,
rather than trusting `pa.borough` / `pa.city`.

**Option A — chatbot-side, all offline**:
- Embed NYC DCP Borough Boundaries + 2020 NTAs GeoJSON (~500KB total
  after simplification).
- Load into shapely `STRtree` spatial index at startup.
- Replace `FILTER_BY_BOROUGH` (which hits `pa.borough`) with a
  post-query Python filter using `contains()`. Or push a
  materialized-borough CTE into PostGIS if the DB team is open to
  adding `borough_from_coords` as a view.

**Option B — upstream, via NYC GeoSearch**:
- Call `geosearch.planninglabs.nyc/v1/reverse?point.lat=...&point.lon=...`
  for each unique location during the admin data import — store the
  authoritative borough/NTA alongside existing `pa.borough`. Zero chatbot
  code change needed once upstream lands.
- Not under our control — requires Streetlives buy-in and an ETL change.

**Option C — hybrid**:
- Option A for chatbot-read-path (defensive), file an issue upstream for
  Option B (curative). Option A becomes redundant once B ships; it's cheap
  enough to keep as belt-and-suspenders.

### 7.3 Longer-term: Neighborhood coverage expansion

**Goal**: Fix the Staten Island = 0, Bronx = 5, Queens = 11 neighborhood
gaps in `NEIGHBORHOOD_CENTERS`.

**Approach**:
- Replace the hand-maintained `NEIGHBORHOOD_CENTERS` dict with a
  generated lookup built from NYC DCP 2020 NTA centroids.
  (262 NTAs, each with borough + canonical name; centroid is straight
  arithmetic from the polygon.)
- Keep the colloquial `NYC_LOCATION_ALIASES` map as a text-lookup layer
  on top — "bed-stuy" → NTA "BK0101" (Bedford-Stuyvesant) etc.
- Benefits:
  - SI searches actually work
  - Every Bronx/Queens NTA has coords
  - DCP-official names are stable
  - Future updates (NYC updates NTAs every census) are mechanical

**Effort**: 1-2 days. Requires sourcing the 262 NTA polygons + authoring
an alias-to-NTA crosswalk for ~100 common neighborhood names.

### 7.4 Revisit the proximity radius

**Current**: `DEFAULT_NEIGHBORHOOD_RADIUS_METERS = 1600` (1 mile).

This is aggressive — a 1 mile radius from Harlem extends ~400m into the
Bronx, from Battery Park extends into the Upper Bay, from Astoria extends
into Manhattan. Consider:

- **Reduce to 1000m (~0.6 mi)** for urban-density searches. Less likely
  to cross water/borough.
- **Use NTA containment instead of radius** — if user says "Soho", find
  all services whose `l.position` is inside the Soho NTA (or an NTA
  adjacent to Soho). This is geographically correct by definition.
- **Keep 1600m only for low-density boroughs** where a single mile is a
  reasonable walking distance (Staten Island, outer Queens).

---

## 8. Concrete Proposal (what I'd build next)

In priority order, small → large:

### 8.1 File an issue upstream (1 hour)
- Open a ticket on `streetlives/streetlives-api` with a test case
  demonstrating the borough mismatch. Provide the specific `service_id`
  from the user's report if available (need to get it from the feedback
  logs).
- Propose NYC GeoSearch API integration on the SSTT import path so
  `pa.borough` is validated against coordinates at write time.

### 8.2 Ship the defensive filter (1-2 days)
- Vendor `data/nyc_boroughs.geojson` (NYC DCP, simplified) into
  `backend/app/rag/data/`.
- Add `shapely` to `backend/requirements.txt`.
- Implement `borough_from_coords(lat, lon) -> Optional[str]` in a new
  `backend/app/rag/boundaries.py` module.
- In `execute_service_query` + `_execute_and_respond`, validate each
  returned card: if `borough_from_coords(lat, lon)` disagrees with the
  borough the user searched for (when borough is known), log and
  optionally filter.
- Expose counts in admin audit log dashboard as a data-quality metric.

### 8.3 Replace `_BOROUGH_CENTROIDS`-based reverse geocode (1 day)
- The population-critical fallback's `_nearest_borough_by_centroid` is
  currently a nearest-neighborhood approximation. Once
  `borough_from_coords` exists (step 8.2), use it directly:
  `_resolve_borough_from_location(slots)` falls back to
  `borough_from_coords(_latitude, _longitude)` for GPS users.
- Delete `_build_neighborhood_borough_table` and the Staten Island
  anchors — no longer needed.

### 8.4 NTA-based neighborhood expansion (2-3 days, separate sprint)
- Source NYC DCP 2020 NTA polygons as GeoJSON.
- Compute centroids, generate a new `NEIGHBORHOOD_TABLE` with borough
  + centroid + polygon per NTA.
- Migrate `NEIGHBORHOOD_CENTERS` consumers to the new table; keep
  `NYC_LOCATION_ALIASES` as a colloquial-name crosswalk.
- Consider NTA-containment queries as an alternative to `ST_DWithin`
  proximity where geographically appropriate.

### 8.5 Radius tuning (bundled with 8.4)
- A/B the default radius down to 1000m. Measure no-result rate change.
- Where available, prefer NTA-containment over radius.

---

## 9. Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| GeoJSON polygon file adds ~50KB–500KB to the repo | Acceptable — already carries larger test fixtures |
| `shapely` has a native `libgeos` dependency | Pure-python shim available; we only need `contains()`. Could also use `pyproj` + manual ray-casting |
| Post-query polygon check adds latency per row | ~0.1ms per point with STRtree; negligible for 25 rows |
| Filtering data-quality-bad services reduces apparent result count | Expose as a soft badge ("location verified") rather than hard filter initially, so we don't silently hide services |
| Upstream borough data may be "wrong" for good reasons (e.g., a service with two locations, one in each borough, using the parent's borough) | Make the filter configurable per-template; log first, filter later |
| NYC NTA boundaries update every decennial census | Version the embedded file and log which version is loaded |

---

## 10. Open questions for the team

1. **Do we have the specific `service_id` from the user's report?** That
   would let us confirm it's a `pa.borough` data-quality issue vs. a
   different bug (e.g., a proximity leak).
2. **Is the Streetlives team open to a PR against `streetlives-api` that
   adds GeoSearch-based borough validation at write-time?** That's the
   curative fix; ours would be palliative.
3. **How aggressive should the initial filter be?** Options ranked from
   conservative to aggressive:
   - (a) Log only — track frequency, don't change user-visible behavior
   - (b) Badge the card with a "location may differ from labeled borough"
     indicator
   - (c) Filter mismatched cards silently
   - (d) Filter + log + surface in admin audit as a "needs review" queue
4. **Do we want to prioritize expanding neighborhood coverage (Bronx,
   Queens, Staten Island) or fixing the borough-mismatch filter first?**
   I'd lean toward the filter — a single fix addresses all five boroughs
   at once.

---

## Appendix A — Hard-won facts worth remembering

- **`pa.borough` is the reliable column** for stated borough, far better
  than `pa.city` (which has casing / typo issues per the code comments
  at `query_templates.py:234`). But it's still a stated value, not a
  derived-from-coordinates value.
- **The 1600m radius is wide enough to cross any NYC waterway.** The
  widest stretch of the Harlem River is ~400m; the narrowest East River
  crossing is ~280m.
- **NYC Planning Labs (`planninglabs.nyc`) is the authoritative source**
  for NYC geospatial APIs. GeoSearch is free and has no API key.
- **NTAs (262 total) are the closest thing NYC has to "official"
  neighborhoods.** They're 1:1 with borough (never cross borough lines),
  named, and versioned. They're what we'd want as the base primitive in
  a rewrite.
- **Staten Island currently has zero neighborhood-level support** in
  `NEIGHBORHOOD_CENTERS` — any "food in St. George" search degrades to
  a borough-wide search. This is a standing bug that the fallback
  indirectly exposed.
