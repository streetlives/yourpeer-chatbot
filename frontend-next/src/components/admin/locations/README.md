# Locations admin components

The `/admin/locations` page renders 12 components across 11 spec'd sections plus the triage table. Each component fetches its own slice of data from a dedicated endpoint under `/admin/api/locations/*` and is responsible for its own loading / error / empty states. The page wrapper (`src/app/admin/locations/page.tsx`) handles section ordering and the cross-component state for the freshness-histogram → table click-through.

## Section map

```
Section               Component (this dir)              Endpoint
──────────────────────────────────────────────────────────────────────────────
1.  Stat strip        top-stat-strip.tsx                /stats
2a. Freshness         freshness-histogram.tsx           /freshness-histogram
3a. By borough        borough-breakdown.tsx             /by-borough
3b. Heatmap           service-borough-heatmap.tsx       /heatmap
3c. Coordinates       coordinate-issues-table.tsx       /coordinate-issues
4a. Coverage          category-coverage-table.tsx       /category-coverage
4b. Stale             stale-categories-list.tsx         /stale-categories
5ab. Feedback         feedback-aggregates.tsx           /feedback-aggregates
5c. Comments          feedback-comments-stream.tsx      /feedback-comments
6.  Integrity         data-integrity-callouts.tsx       /integrity-callouts
7.  Time series       locations-timeseries.tsx          /timeseries
2b. Triage table      locations-table.tsx               /list
```

`locations-table.tsx` lives at the bottom of the page (display order) but is section 2b in the spec — pairs with the freshness histogram (section 2a) above. Clicking a histogram bar sets the table's `ageBucket` filter via lifted state in `page.tsx`.

## Where state lives

Most state is component-local (each component owns its fetch + loading state). The only cross-component coordination is in `page.tsx`:

- **`ageBucket`** — controlled state for the freshness-histogram → locations-table filter handoff. Histogram emits via `onBucketSelect`; table receives via the `ageBucket` prop. The table accepts `ageBucket` as either uncontrolled (default) or controlled (when the prop is set).

- **Smooth-scroll anchor** — the histogram includes a `tableRef` callback so a bar click scrolls the page down to the locations table. Page-level concern, lives in `page.tsx`.

Beyond that, components are independent.

## Cross-component contracts

Several string sets are shared between backend and frontend by convention rather than by literal type. These are pinned in `src/lib/admin/locations-keys.ts` and verified by `scripts/verify/locations-contract.mjs`:

- `LOCATIONS_AGE_BUCKET_KEYS` — `lt30` / `30to90` / `90to180` / `180to365` / `gt365` / `never`. Histogram emits and table accepts.
- `LOCATIONS_BOROUGH_LABELS` — Manhattan / Brooklyn / Queens / Bronx / Staten Island / Other. Heatmap columns + borough table rows.
- `FEEDBACK_CRITERIA_KEYS` — safety / friendliness / cleanliness / queer_friendly. Per-criterion cards + criterion_counts micro-bars + comment-stream badges.
- `INTEGRITY_CALLOUT_IDS` — orphaned_locations / orphaned_services / malformed_phones / entity_encoded_html / coordinate_issues_ref.

If any of these change on the backend, run `npm run verify:locations-contract` to catch the drift.

## Visual conventions

All components share a few aesthetic decisions for visual consistency across the page:

- **Color scale**: amber for warnings, red for danger, emerald for positive states, blue for info. Same thresholds across components: red ≥ 50% / ≥ 1.0 ratio, amber ≥ 25% / ≥ 0.25 ratio, neutral or emerald otherwise.

- **Empty states**: positive empty states (✓ green callout) where the empty case is the wanted outcome — "no integrity issues," "no coordinate issues," "no stale categories." Soft empty states (italic neutral text) where the empty case is just "no data yet" — "no feedback events yet," "no taxonomy data available yet."

- **Numbers always visible**: heatmap cells, micro-bars, and ratio displays always show the underlying count. Color is decoration; the number is the truth.

- **Charts as inline SVG / CSS**: no chart library on this page. Bundle hygiene + visual consistency with the existing operations-charts. `locations-timeseries.tsx` is the most chart-shaped of the lot at ~140 LOC.

- **Type-safe response shapes**: every component reads a typed response from `src/lib/admin/locations-types.ts`. Never `any`, never `unknown` past the fetch boundary.

## Adding a new section

1. **Backend**: write the aggregation function in `services/locations_admin/aggregations.py`, export it from `__init__.py`, wire a `@router.get` endpoint in `routes/admin_locations.py`.
2. **Types**: add the response shape to `lib/admin/locations-types.ts`. If the response has stable string keys (criterion names, borough labels, callout ids) that the frontend reads by name, add them to `lib/admin/locations-keys.ts` AND to the verify script in `scripts/verify/locations-contract.mjs`.
3. **Component**: build it in this directory. Follow the loading / error / empty-state pattern of the existing components. Always handle the empty case before rendering the data shape — empty data should never look like a bug.
4. **Wire it into `app/admin/locations/page.tsx`** with a section header and a one-paragraph explanatory description.
5. **Tests**: add endpoint tests to `tests/integration/test_admin_locations.py`. The existing `_make_sql_responder` helper handles SQL marker-substring matching for mock responses. The route-wiring sanity test (`test_all_locations_endpoints_reachable_when_wired`) needs to be updated with the new endpoint path.

## Where the docs aren't

This README is a map. The "why" for each component's design decisions lives in the component file's docstring at the top, and the spec lives in `locations_page_spec/SPEC.md` (in the day-1 bundle, archived). For the PR-history rationale (e.g. why FEEDBACK_MIN_SAMPLE = 2, why even-split demand attribution), see the per-day CHANGES.md files in their respective `locations_day_N/` bundles.
