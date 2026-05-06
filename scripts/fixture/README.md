# Eval fixture population

SQL queries that pull a representative slice of Streetlives service data
from production into the eval-runner fixture file at
`tests/eval/fixtures/services.json`.

## Why this exists

The eval runner mocks `query_services` because hitting the real database
during evaluation would make runs non-hermetic — every run would get
slightly different scores depending on what data the team had updated
that day. But mocking has its own failure mode: hand-coded mock data
drifts from production reality. Earlier versions of the eval used a
single hardcoded "Brooklyn food pantry" return value (Bug 8), then a
search-aware dispatcher with its own hand-coded NYC geography table
(R39 false positives — see eval results doc).

The fix is to mock with **real data**, frozen as a fixture. Production
runs are reproducible because the fixture doesn't change between runs;
the data is realistic because it came out of the same database the bot
queries in production.

These queries are how that fixture gets populated. They're not run
during normal eval execution — only when refreshing the fixture
(quarterly, or whenever the data team has shipped a meaningful update).

## What's in here

Three SQL files, intended to run in order:

- **`01_diagnostic_count.sql`** — Counts rows per `(service_type, borough)`
  bucket. Run first to confirm the bucketing logic produces a sensible
  distribution (~30–40 buckets, 1–3 rows each).

- **`02_sanity_orgs.sql`** — Lists ~60 sample `(service, org, phone)`
  tuples. Run second, only if Query 1 looked reasonable. The point is
  an eyeball check that real NYC organizations appear (Bowery Mission,
  Catholic Charities, Covenant House, etc.) — not weird ghost rows
  from a misjoined query.

- **`03_extract_fixture.sql`** — The actual extraction. Returns flat
  columns ready for "Export → JSON" in any DB UI. Only run this after
  Queries 1 and 2 confirm the data looks right.

Each file is fully self-contained — a complete CTE-based query with
its own `WITH` block. Run them one at a time.

## How to run (DB UI workflow)

You're using a DB connection UI (DBeaver, DataGrip, TablePlus, pgAdmin,
Postico, Beekeeper, or similar) with read access to the Streetlives
production DB.

### Query 1 — diagnostic count

1. Open `01_diagnostic_count.sql` in your UI's SQL editor.
2. Connect to the Streetlives production read-replica.
3. Execute. Expected output: ~30–40 rows, each
   `(bot_service_type, borough, row_count)`.
4. Eyeball the bucket counts (see "What to look for" below). If
   anything looks off, stop here and report back — don't proceed
   to Query 3 yet.

### Query 2 — sanity check on org names

1. Open `02_sanity_orgs.sql` in your UI's SQL editor.
2. Execute. Expected output: up to 60 rows of
   `(bot_service_type, borough, organization_name, service_name, phone)`.
3. Eyeball the org column for recognizable NYC names (see "What to
   look for"). Stop here if most rows show NULL or unfamiliar names.

### Query 3 — extract fixture as JSON

1. Open `03_extract_fixture.sql` in your UI's SQL editor.
2. Execute. Expected output: ~80–120 rows with ~27 columns each.
3. **Export the result set as JSON.** Most UIs make this one click:

   | UI | How to export |
   |----|---------------|
   | DBeaver | Right-click results → Export Data → JSON |
   | DataGrip | Right-click results → Export Data → JSON |
   | TablePlus | Cmd-Shift-E → JSON |
   | pgAdmin | Right-click → Save as → JSON |
   | Postico | Cmd-Shift-E → JSON |
   | Beekeeper | Click "Export" at the bottom of results → JSON |

4. Save the export as `tests/eval/fixtures/services_raw.json`.

The result will be a JSON array of objects — one per service row,
with each column as a key. That's the input format the next step
(transformation into the final fixture) expects.

## What to look for

### Query 1 — bucket counts

Expected: ~30–40 buckets covering the 8 service types and 5 boroughs.

**Things that are fine:**

- Some buckets at 0 rows. Real data has uneven coverage — Staten Island
  has thin service coverage in real life, employment data isn't dense
  everywhere, etc.
- Bucket sizes between 1 and 3 (capped by the `rn <= 3` filter).

**Things to flag:**

- A core bucket missing entirely (e.g. `food / Manhattan` shows 0).
  That suggests the taxonomy CASE expression in the WITH block is
  filtering wrong. Report back — I'll adjust the bucket logic.
- All rows clustered in one or two buckets. Suggests the bucketing
  is too coarse or too narrow.

### Query 2 — sample org names

Look for recognizable NYC social-service organizations: Bowery Mission,
Catholic Charities, Covenant House, Safe Horizon, Mount Sinai, BRC,
Coalition for the Homeless, Legal Aid Society, Doe Fund, Project
Renewal, Ali Forney Center, etc.

**Things that are fine:**

- A few unfamiliar names mixed in. Streetlives covers many small,
  hyperlocal orgs that aren't household names.

**Things to flag:**

- Most rows showing NULL in the `organization_name` column. The
  `LEFT JOIN organizations` in the WITH block should be picking these
  up; if it isn't, the schema doesn't match expectations.
- The same org appearing in many service buckets. Some orgs really do
  cover many services (Bowery Mission, BRC) — that's fine — but if
  one org dominates everything, the dedupe in the query is broken.

### Query 3 — JSON export

Once Q3 runs and you've exported to JSON, before checking it in:

```bash
# Row count — should be 80–120 for the default rn <= 3 cap
python3 -c "
import json
data = json.load(open('tests/eval/fixtures/services_raw.json'))
print(f'{len(data)} rows')
"

# Unique phone numbers — judge uses these to verify echo vs fabrication.
# Duplicates would create ambiguity.
python3 -c "
import json
data = json.load(open('tests/eval/fixtures/services_raw.json'))
phones = [r['phone'] for r in data if r.get('phone')]
print(f'{len(phones)} rows with phones, {len(set(phones))} unique')
"

# Bucket distribution — manual cross-check against Query 1's output
python3 -c "
import json
from collections import Counter
data = json.load(open('tests/eval/fixtures/services_raw.json'))
buckets = Counter()
for r in data:
    buckets[(r['bot_service_type'], r['borough'])] += 1
for k, v in sorted(buckets.items()):
    print(f'  {k[0]:14s} {k[1]:14s} {v}')
"
```

If anything looks off — a row that fails JSON parsing, duplicate
phones, a bucket with way more rows than you saw in Query 1 — fix it
before the fixture is checked in. Once it's in, every eval run
reads it.

## How the fixture flows into the eval

(Filled in once Path C lands.)

The eval-runner replaces its hand-coded dispatcher with a fixture
loader. At module import:

```python
import json, pathlib

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "services.json"
_FIXTURE = json.loads(_FIXTURE_PATH.read_text())
```

`_mock_query_services(service_type, location, ...)` then filters
`_FIXTURE` by service type and borough using production's
`NEIGHBORHOOD_CENTERS` / `NYC_LOCATION_ALIASES` for the location lookup
— no parallel geography table.

The fixture is a list of service dicts; the eval treats it as
read-only.

## When to refresh

Refresh the fixture when any of:

- A scenario fails for a reason that traces back to "the fixture
  doesn't have the right kind of data" (e.g. a new service category
  was added in production, no rows match in the fixture).
- The data team announces a structural change (taxonomy reorganization,
  new fields on the service shape, etc.).
- It's been roughly a quarter since the last refresh.

Don't refresh casually. Every refresh is a non-trivial change to the
eval baseline — scenarios that scored high last quarter might score
differently against new data even when the bot is unchanged. The R28 /
R38 / future baselines are anchored to specific fixtures, so a refresh
effectively starts a new baseline. Note the refresh date and the row
counts in the eval results doc.

## Schema dependencies

The query joins these production tables (verified against
`backend/app/rag/query_templates.py`):

```
services
service_at_locations
locations
organizations
physical_addresses
phones
service_taxonomy
taxonomies
eligibility, eligibility_parameters
accessibility_for_disabilities
languages, service_languages
```

If any of these get renamed in production, the queries here will need
to be updated. The query templates are the canonical source — match
their JOIN structure exactly to avoid drift.

## Taxonomy bucketing — keep in sync with production

The CASE expression that buckets each service into one of the bot's 9
service_type categories is **copied verbatim** from the
`default_params.taxonomy_names` lists in
`backend/app/rag/query_templates.py`. Specifically:

| Bucket | Source |
|---|---|
| food | `query_templates.py` line 612, food template |
| shelter | line 659, HousingEligibilityQuery template (18 entries — full Shelter parent + children) |
| clothing | line 709, ClothingQuery template |
| medical | line 749, HealthcareQuery template |
| mental_health | line 841, MentalHealthQuery template |
| legal | line 772, LegalQuery template |
| employment | line 792, EmploymentQuery template |
| personal_care | line 813, PersonalCareQuery template |
| other | line 865, OtherServicesQuery template (catch-all, only used as ELSE fallback) |

**When production updates these lists, this query must update too.**
Otherwise the fixture will under-represent (or mis-bucket) services
that production handles correctly. The eval will then flag false
positives against bot behavior that's actually correct in production.

The April 16, 2026 update to the shelter template (adding `crisis`,
`drop-in center`, `referral`, `assessment`, `residential recovery`)
is a concrete example: services like Covenant House's "Emergency
Bed Placement" (tagged `Crisis`) only appear under shelter searches
because production added those keywords. A fixture query that omits
them would silently drop those services, then the eval would flag
"shelter search returns no Covenant House" as a bot failure, when
in fact production handles it correctly.

**Important wrinkle:** production's templates allow the same service
to appear under multiple `service_type` queries (e.g., a Youth
shelter service is returned by both `shelter` and `other` searches).
The fixture's CASE expression picks ONE bucket per service
(first-match-wins) since each fixture row needs a single
`bot_service_type`. The order is: shelter > medical > mental_health >
food > clothing > personal_care > legal > employment > other. This
prioritizes safety-relevant categories when taxonomies overlap.

The first-match-wins shortcut is safe for the eval — every query
still finds realistic services — but it means the fixture is not
literally identical to what production returns. If a future eval
scenario depends on a service appearing under multiple service_type
searches simultaneously, the fixture row shape would need to change
from `bot_service_type: string` to `bot_service_types: array`.

## What the queries do NOT capture

A few production fields are deliberately omitted from the fixture:

- **`holiday_schedules`** — The eval doesn't need today-vs-tomorrow
  schedule resolution; we use `is_open: 'open'` as a synthetic value.
- **`location_comment_highlights`** — Review highlights from the
  feedback feature. Useful in production, noise in eval.
- **Real schedule data** (`opens_at` / `closes_at`) — Same reason.
  The fixture provides plausible synthetic values.

If a future eval scenario starts caring about one of these fields,
add it to the query and refresh the fixture. Don't synthesize new
fields client-side in the eval — that reintroduces the drift problem
these queries exist to solve.

## Schema mismatch troubleshooting

If a query errors with "relation X does not exist" or "column Y does
not exist":

1. Check `backend/app/rag/query_templates.py` for the correct table
   and column names. The production query templates are authoritative.
2. Diff the WITH block against the canonical `FoodQuery` template
   (search for `FoodQuery` or `food_query` in `query_templates.py`).
3. Common drift surfaces: `service_taxonomy` (sometimes typed
   `services_taxonomies`), `service_at_locations` (the junction table
   between services and locations — easy to miss), `physical_addresses`
   (joined on `location_id`, not `service_id`).

## Outstanding things

- The query has been run against the production DB once (May 2026,
  pre-correction). The corrected bucketing logic (matched verbatim to
  production's `default_params.taxonomy_names` lists) has not yet been
  re-run. Expect more rows in the `shelter` bucket once it is —
  Covenant House's "Emergency Bed Placement" (tagged `Crisis`) and
  similar services were dropping into `other` under the previous
  guessed taxonomy list and should now land in `shelter`.
- The bucketing CASE expression mirrors production's lists at one
  point in time. If production's `default_params.taxonomy_names`
  are edited (e.g., a new shelter type is added), this query needs
  the matching update or the fixture will drift from production.
  Check the "Taxonomy bucketing" section above when refreshing.
- The `organization_name` join uses `LEFT JOIN`, but the WHERE clause
  filters `o.name IS NOT NULL`. Effectively this is an INNER JOIN.
  Worth knowing if you see fewer rows than expected — services without
  a parent org are dropped entirely.
