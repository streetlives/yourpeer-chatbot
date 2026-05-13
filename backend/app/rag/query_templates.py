"""
Query Templates — Parameterized SQL for each service category.

Architecture: These are the ONLY queries that touch the Streetlives DB.
Every query is pre-defined, parameterized, and auditable.
The LLM never generates SQL — it only fills slots that feed these templates.

Schema reference (Streetlives PostgreSQL):
    services (3,506 rows)          — id, name, description, fees, organization_id, ...
    locations (2,414 rows)         — id, name, position (PostGIS), organization_id, ...
    service_at_locations (3,405)   — service_id, location_id  (junction)
    taxonomies (39 rows)           — id, name, parent_id, parent_name
    service_taxonomy (3,507)       — service_id, taxonomy_id  (junction)
    eligibility (3,646 rows)       — service_id, parameter_id, eligible_values (JSONB)
    eligibility_parameters (9)     — id, name (gender/age/familySize/income/...)
    physical_addresses (2,569)     — location_id, address_1, city, state_province, postal_code
    regular_schedules (971)        — service_id, weekday, opens_at, closes_at (STALE — pre-COVID)
    holiday_schedules (10,593)     — service_id, weekday, opens_at, closes_at, occasion (CURRENT — 'COVID19')
    organizations (2,460)          — id, name, description, url
    phones (2,730)                 — location_id / service_id / organization_id, number
    accessibility_for_disabilities — location_id, accessibility, details

Key gotchas from schema exploration:
    - NO "borough" column anywhere — use physical_addresses.city
    - NO "type" column on services — use taxonomy junction
    - service_at_locations (with 's') is the active junction table
    - eligibility.eligible_values is JSONB (arrays/objects, varies by param)
    - locations.position is PostGIS USER-DEFINED geometry
"""

from app.utils.time_format import format_time



# ---------------------------------------------------------------------------
# BASE QUERY — shared by all templates
# ---------------------------------------------------------------------------
# This is the common join chain every service query needs. Individual
# templates add WHERE clauses via the `build_query()` function.

_BASE_QUERY = """
SELECT
    s.id              AS service_id,
    s.name            AS service_name,
    s.description     AS service_description,
    s.fees            AS fees,
    s.url             AS service_url,
    s.email           AS service_email,

    o.name            AS organization_name,
    o.url             AS organization_url,

    l.id              AS location_id,
    l.name            AS location_name,
    l.slug            AS location_slug,

    pa.address_1      AS address,
    pa.city           AS city,
    pa.state_province AS state,
    pa.postal_code    AS zip_code,

    -- Coordinates projected for coordinate→borough validation in
    -- query_executor._annotate_geographic_borough. PostGIS geometry
    -- uses (x, y) = (lon, lat). NULL-safe: services without position
    -- data (pilot imports, manual entries) get NULL lat/lon and skip
    -- validation. See docs/audits/BOUNDARY_AUDIT.md.
    ST_Y(l.position::geometry) AS latitude,
    ST_X(l.position::geometry) AS longitude,

    best_phone.number     AS phone,
    best_phone.extension  AS phone_extension,

    today_sched.opens_at   AS today_opens,
    today_sched.closes_at  AS today_closes,

    -- Returns true only when ALL eligible_values are ["true"] (referral required).
    -- Services with ["true","false"] or no membership rule return NULL (no badge shown).
    (
        membership_elig.eligible_values = '["true"]'::jsonb
        OR membership_elig.eligible_values = '[true]'::jsonb
    ) AS requires_membership,

    l.last_validated_at AS last_validated_at,

    -- Co-located services: other taxonomy categories at the same location.
    -- Lets the card show "Also here: Showers, Clothing, Health" so users
    -- can discover services they didn't think to ask about.
    (SELECT ARRAY_AGG(DISTINCT t_co.name ORDER BY t_co.name)
     FROM service_at_locations sal_co
       JOIN services s_co ON sal_co.service_id = s_co.id
       JOIN service_taxonomy st_co ON s_co.id = st_co.service_id
       JOIN taxonomies t_co ON st_co.taxonomy_id = t_co.id
     WHERE sal_co.location_id = l.id
       AND s_co.id != s.id
       AND t_co.name NOT IN ('Other service')
    ) AS also_available,

    -- Accessibility info from the accessibility_for_disabilities table.
    -- Surfaced on service cards so users can make informed decisions.
    (SELECT afd.accessibility
     FROM accessibility_for_disabilities afd
     WHERE afd.location_id = l.id
     LIMIT 1
    ) AS accessibility_info,

    -- Eligibility summary: aggregated eligibility rules for display on cards.
    -- Returns JSON array of {param, values} for each eligibility rule.
    (SELECT json_agg(json_build_object(
         'param', ep.name,
         'values', e.eligible_values
     ))
     FROM eligibility e
     JOIN eligibility_parameters ep ON e.parameter_id = ep.id
     WHERE e.service_id = s.id
       AND ep.name != 'membership'
    ) AS eligibility_rules,

    -- Review highlight: top positive peer comment for this location.
    -- Extracted from the LLM sentiment analysis in location_comment_highlights.
    -- 35 locations currently have highlights (~1.5% of 2,414 locations).
    -- Uses the most informative positive comment (array is pre-sorted by
    -- informativeness_score in the OpenAI pipeline).
    (SELECT lch.openai_output_json->'top_positive_comments'->0->>'comment'
     FROM location_comment_highlights lch
     WHERE lch.location_id = l.id
       AND lch.openai_output_json->'top_positive_comments' IS NOT NULL
       AND jsonb_array_length(lch.openai_output_json->'top_positive_comments') > 0
     ORDER BY lch.updated_at DESC
     LIMIT 1
    ) AS review_highlight,

    -- Required documents: what users need to bring (e.g. "State ID", "Proof of address").
    -- Filtered to exclude null/empty/'None' entries.
    (SELECT ARRAY_AGG(rd.document)
     FROM required_documents rd
     WHERE rd.service_id = s.id
       AND rd.document IS NOT NULL
       AND rd.document != ''
       AND rd.document != 'None'
    ) AS required_documents,

    -- Languages spoken at this service.
    -- Helps non-English speakers choose the right location.
    (SELECT ARRAY_AGG(DISTINCT lang.language ORDER BY lang.language)
     FROM languages lang
     JOIN service_languages sl ON lang.id = sl.language_id
     WHERE sl.service_id = s.id
    ) AS languages_spoken,

    -- This service's own taxonomy tags (not co-located — this service itself).
    -- Enables client-side sub-category filtering in post-results handler.
    -- Unlike also_available (which shows OTHER services at the same location),
    -- this shows what THIS service is classified as in the Streetlives taxonomy.
    -- Example: a shelter service might be tagged ["Shelter", "Families", "Intake"].
    -- Excludes "Other service" (catch-all with no filtering value).
    (SELECT ARRAY_AGG(DISTINCT t_own.name ORDER BY t_own.name)
     FROM service_taxonomy st_own
       JOIN taxonomies t_own ON st_own.taxonomy_id = t_own.id
     WHERE st_own.service_id = s.id
       AND t_own.name NOT IN ('Other service')
    ) AS service_taxonomies

FROM services s
    JOIN service_at_locations sal  ON s.id = sal.service_id
    JOIN locations l               ON sal.location_id = l.id
    LEFT JOIN organizations o      ON s.organization_id = o.id
    LEFT JOIN physical_addresses pa ON l.id = pa.location_id
    LEFT JOIN LATERAL (
        SELECT ph.number, ph.extension
        FROM phones ph
        WHERE ph.location_id = l.id
           OR ph.service_id = s.id
           OR ph.organization_id = o.id
        ORDER BY
            CASE
                WHEN ph.location_id = l.id THEN 1
                WHEN ph.service_id = s.id THEN 2
                WHEN ph.organization_id = o.id THEN 3
            END
        LIMIT 1
    ) best_phone ON TRUE
    -- Today's schedule: uses holiday_schedules (occasion='COVID19') which
    -- contains the current operating hours (10,593 rows covering 2,448
    -- services). Despite the table name, this is NOT holiday-specific —
    -- it became the de facto schedule source during COVID when orgs updated
    -- their hours en masse. regular_schedules (971 rows) is stale pre-COVID
    -- data. YourPeer.nyc uses this same table for its schedule display.
    -- Weekday convention: 1=Monday...7=Sunday (matches PostgreSQL ISODOW).
    --
    -- Timezone: ``CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York'`` evaluates
    -- "today's weekday" in NYC time so that, e.g., a Tuesday-evening NYC user
    -- sees Tuesday's hours rather than Wednesday's. The Streetlives DB lives
    -- on AWS RDS with a session timezone we don't control; without the
    -- explicit ``AT TIME ZONE``, ``CURRENT_DATE`` evaluates in the DB
    -- session's tz and silently rolls over before NYC midnight on whichever
    -- side of UTC the DB session is set to. This matches the Python-side
    -- ``zoneinfo.ZoneInfo("America/New_York")`` used by
    -- ``_compute_schedule_status``; both layers agree on what "today" means.
    -- See ``docs/audits/SCHEDULE_TZ_FIX.md`` for the full incident.
    LEFT JOIN holiday_schedules today_sched
        ON today_sched.service_id = s.id
        AND today_sched.weekday = EXTRACT(
            ISODOW FROM (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')
        )::int
        AND today_sched.occasion = 'COVID19'
    -- Membership eligibility: regular LEFT JOIN instead of LATERAL.
    -- Batches the lookup across all rows instead of per-row subquery.
    LEFT JOIN eligibility membership_elig
        ON membership_elig.service_id = s.id
        AND membership_elig.parameter_id = (
            SELECT ep.id FROM eligibility_parameters ep WHERE ep.name = 'membership' LIMIT 1
        )
"""

# ---------------------------------------------------------------------------
# FILTER FRAGMENTS — composable WHERE/AND clauses
# ---------------------------------------------------------------------------
# Each fragment is a tuple of (sql_clause, required_param_keys).
# The query builder picks only the fragments whose params are present.

# Taxonomy filters use EXISTS subqueries to avoid row multiplication.
# A service tagged with both "Food Pantry" and "Food Benefits" will only
# appear once, eliminating the need for Python-side deduplication.

# Multi-value taxonomy match — used when a service category maps to several
# taxonomy names in the DB (e.g. clothing services are split across
# "Clothing", "Clothing Pantry", "Interview-Ready Clothing", etc.).
# Passes a list of lowercase names; ANY() matches if t.name is in the list.
FILTER_BY_TAXONOMY_NAME_IN = (
    """EXISTS (
        SELECT 1 FROM service_taxonomy st
        JOIN taxonomies t ON st.taxonomy_id = t.id
        WHERE st.service_id = s.id AND LOWER(t.name) = ANY(:taxonomy_names)
    )""",
    ["taxonomy_names"],
)

# Taxonomy match OR service-name-pattern fallback for the tagging-debt bucket.
#
# Background — TAXONOMY_AUDIT_MAY2026.md §III + Ticket K (cross-cutting):
# The Streetlives DB has 1,105 services tagged directly at `Other service`
# parent (no specific child leaf) — 31% of the entire database. Word-frequency
# analysis on those names reveals systematic tagging debt where services that
# clearly belong under an existing leaf (Education, Benefits, Immigration
# Services, Case Workers) are tagged at the parent instead. For
# `service_type=education` (Phase B Ticket C), the DB has 101 services at the
# leaf but an estimated 80-130 additional education-shaped services sitting
# in the parent-direct bucket — services named "Adult Education", "ESL",
# "GED Prep", "Citizenship Classes", etc. that the data team hasn't yet
# retagged to the leaf.
#
# This filter lets a promoted service_type surface BOTH:
#   (a) services tagged at the relevant child leaf(s) via :taxonomy_names, AND
#   (b) services tagged at `Other service` parent-direct whose `services.name`
#       matches :service_name_pattern — the regex name-pattern fallback.
#
# The two branches are OR'd inside a single parenthesized group so the filter
# composes correctly with the AND'd chain of other WHERE clauses in
# `build_query`. The name-pattern branch is intentionally narrow: it ONLY
# matches services tagged at `Other service` parent-direct (`t.name = 'other
# service' AND t.parent_id IS NULL`). It does NOT scan the whole DB — a
# substring match for "Adult Education" against every service.name would
# pick up unrelated services (e.g. a shelter named "St. Patrick's Adult
# Center" wouldn't, but the principle stands).
#
# Forward-compat: when the Streetlives data team works through Ticket A and
# retags parent-direct services to their proper leaves, this filter
# gracefully degrades — leaf matches subsume name-pattern matches, and
# `SELECT DISTINCT s.id` in the base query collapses any double-counting.
# Tagging cleanup improves nothing visible to the user but doesn't break
# anything. That's the point: this filter makes the chatbot's behavior
# forward-compatible with the data-team work without depending on it.
#
# Templates using this filter MUST provide both `taxonomy_names` (list) and
# `service_name_pattern` (regex string) in their `default_params`. Templates
# that don't need name-pattern fallback should continue to use
# `FILTER_BY_TAXONOMY_NAME_IN`. The two filters are mutually exclusive —
# a template uses one or the other, never both.
#
# Why a single compound filter rather than two ANDed/ORed filters in the
# template list: `build_query` joins `required_filters` with `" AND "`.
# Splitting the OR across two list entries would require special-case logic
# (either rewriting the join or interpreting filter strings starting with
# "OR"). Wrapping the entire OR-group in one parenthesized SQL expression
# means the AND-join works correctly and the filter list stays a flat data
# structure of self-contained SQL fragments.
FILTER_BY_TAXONOMY_OR_NAME_PATTERN = (
    """(
        EXISTS (
            SELECT 1 FROM service_taxonomy st
            JOIN taxonomies t ON st.taxonomy_id = t.id
            WHERE st.service_id = s.id AND LOWER(t.name) = ANY(:taxonomy_names)
        )
        OR (
            s.name ~* :service_name_pattern
            AND EXISTS (
                SELECT 1 FROM service_taxonomy st_pd
                JOIN taxonomies t_pd ON st_pd.taxonomy_id = t_pd.id
                WHERE st_pd.service_id = s.id
                  AND LOWER(t_pd.name) = 'other service'
                  AND t_pd.parent_id IS NULL
            )
        )
    )""",
    ["taxonomy_names", "service_name_pattern"],
)

# Co-located service filter — finds locations where a DIFFERENT service
# at the same location matches a second set of taxonomy names.
# Used when the user asks for multiple services (e.g. "food and clothing").
FILTER_BY_COLOCATED_TAXONOMY = (
    """EXISTS (
        SELECT 1 FROM service_at_locations sal_co
          JOIN services s_co ON sal_co.service_id = s_co.id
          JOIN service_taxonomy st_co ON s_co.id = st_co.service_id
          JOIN taxonomies t_co ON st_co.taxonomy_id = t_co.id
        WHERE sal_co.location_id = l.id
          AND s_co.id != s.id
          AND LOWER(t_co.name) = ANY(:colocated_taxonomy_names)
    )""",
    ["colocated_taxonomy_names"],
)

# REMOVED (Apr 17, 2026): FILTER_BY_BOROUGH referenced pa.borough, which does
# NOT exist in the Streetlives DB. Every borough-level search had been silently
# erroring with `psycopg2.errors.UndefinedColumn: column pa.borough does not
# exist`, getting caught by _execute_sql's generic exception handler, and
# falling through to the relaxed query. Users saw "I broadened the search a
# bit" on every direct borough search. Borough filtering is now done via
# FILTER_BY_CITY_IN_BOROUGH (pa.city = ANY(:city_list)) — the only filter
# that was actually working. See docs/audits/BOUNDARY_AUDIT.md for the full story
# and follow-up plans (polygon-based geographic borough derivation from
# l.position using NYC DCP boundaries).

FILTER_BY_CITY = (
    "LOWER(pa.city) = LOWER(:city)",
    ["city"],
)

# Borough-level city match — matches any city value that belongs to the borough.
# When a user says "Queens", this matches "Queens", "Astoria", "Flushing",
# "Jamaica", "Long Island City", etc.
# The SQL uses ANY() with an array parameter, which SQLAlchemy handles natively.
#
# This is the de-facto borough filter. The city_list is built by
# get_borough_city_names() in query_executor.py by walking NYC_LOCATION_ALIASES.
# Known limitation: pa.city has casing/typo inconsistencies and sometimes
# wrong-borough assignments. A polygon-based filter using l.position against
# NYC DCP borough boundaries would be authoritative; see BOUNDARY_AUDIT.md.
FILTER_BY_CITY_IN_BOROUGH = (
    "LOWER(pa.city) = ANY(:city_list)",
    ["city_list"],
)

# Broader city match — matches if the city field contains the search term.
# Useful because some addresses store "East New York" not "Brooklyn".
FILTER_BY_CITY_LIKE = (
    "LOWER(pa.city) LIKE LOWER(:city_pattern)",
    ["city_pattern"],
)

# State filter — ensures results are within New York State.
# Prevents results from Poughkeepsie, Albany, etc. leaking in when
# the city filter is relaxed.
FILTER_BY_STATE_NY = (
    "LOWER(pa.state_province) = 'ny'",
    [],
)

# PostGIS proximity search (requires lat/lon).
# Returns services within :radius_meters of the given point.
FILTER_BY_PROXIMITY = (
    "ST_DWithin(l.position::geography, ST_MakePoint(:lon, :lat)::geography, :radius_meters)",
    ["lat", "lon", "radius_meters"],
)

# Age eligibility — checks the JSONB eligible_values for age ranges.
# A service matches if:
#   - It has no age eligibility rule (open to all), OR
#   - Its age rule includes all_ages = true, OR
#   - The user's age falls within [age_min, age_max]
FILTER_BY_AGE_ELIGIBILITY = (
    """
    NOT EXISTS (
        SELECT 1 FROM eligibility e
        JOIN eligibility_parameters ep ON e.parameter_id = ep.id
        WHERE e.service_id = s.id
          AND ep.name = 'age'
          AND NOT (
              e.eligible_values @> '[{"all_ages": true}]'::jsonb
              OR (
                  (e.eligible_values->0->>'age_min' IS NULL
                   OR (e.eligible_values->0->>'age_min')::int <= :age)
                  AND
                  (e.eligible_values->0->>'age_max' IS NULL
                   OR (e.eligible_values->0->>'age_max')::int >= :age)
              )
          )
    )
    """,
    ["age"],
)

# Gender eligibility — checks if the service accepts the user's gender.
# A service matches if:
#   - It has no gender eligibility rule, OR
#   - Its eligible_values array contains the user's gender
FILTER_BY_GENDER_ELIGIBILITY = (
    """
    NOT EXISTS (
        SELECT 1 FROM eligibility e
        JOIN eligibility_parameters ep ON e.parameter_id = ep.id
        WHERE e.service_id = s.id
          AND ep.name = 'gender'
          AND NOT e.eligible_values @> to_jsonb(:gender::text)
    )
    """,
    ["gender"],
)

# Schedule filter — only services open on a given weekday.
# weekday: 1=Monday … 7=Sunday (matches holiday_schedules.weekday / ISODOW)
FILTER_BY_WEEKDAY = (
    """
    EXISTS (
        SELECT 1 FROM holiday_schedules rs
        WHERE rs.service_id = s.id
          AND rs.weekday = :weekday
          AND rs.occasion = 'COVID19'
    )
    """,
    ["weekday"],
)

# Schedule filter — services open at a specific time on a given weekday.
#
# INTENTIONALLY UNUSED (as of Apr 2026). The chatbot uses sort-only semantics
# for open-now: services without schedule data stay in results (ranked lower)
# rather than being excluded. See QUERY_PARITY_AUDIT.md "Open-now behavior"
# section for the decision rationale (DB schedule coverage is sparse:
# ~40-80% for walk-in services, near-0% for others; exclude-semantics would
# silently hide majority of services in sparse-coverage categories).
#
# YourPeer diverges from the chatbot here and uses exclude-semantics via
# its `openAt` API param. The constant is retained for reference and to
# document the shape of the filter if exclude-semantics is ever adopted.
FILTER_BY_OPEN_NOW = (
    """
    EXISTS (
        SELECT 1 FROM holiday_schedules rs
        WHERE rs.service_id = s.id
          AND rs.weekday = :weekday
          AND rs.opens_at <= :current_time
          AND rs.closes_at >= :current_time
          AND rs.occasion = 'COVID19'
    )
    """,
    ["weekday", "current_time"],
)

# Exclude services hidden from search
FILTER_NOT_HIDDEN = (
    "l.hidden_from_search IS NOT TRUE",
    [],
)

# Description keyword filter — narrows results by matching against
# service descriptions using PostgreSQL regex. Used by Phase 4
# sub-category narrowing to distinguish services that share a
# parent taxonomy (e.g. dental vs vision under Health).
# The pattern is a PostgreSQL ~* regex (case-insensitive).
FILTER_BY_DESCRIPTION_KEYWORDS = (
    "s.description ~* :description_pattern",
    ["description_pattern"],
)

# Organization name filter — ILIKE match against the org name.
# Used by OrgNameQuery when users ask about a specific org by name.
FILTER_BY_ORG_NAME = (
    "o.name ILIKE :org_name_pattern",
    ["org_name_pattern"],
)

# Walk-in / no-requirements filter — excludes services that require
# referrals or registered membership. Used when user says "walk-in only",
# "no referral needed", "drop-in", etc.
FILTER_BY_NO_REQUIREMENTS = (
    """NOT EXISTS (
        SELECT 1 FROM eligibility e
        JOIN eligibility_parameters ep ON e.parameter_id = ep.id
        WHERE e.service_id = s.id
          AND ep.name = 'membership'
          AND (e.eligible_values = '["true"]'::jsonb
               OR e.eligible_values = '[true]'::jsonb)
    )""",
    ["no_requirements"],
)

# Clothing occasion filter — narrows clothing results to casual (Everyday)
# or professional (Job Interview) using the taxonomy_specific_attributes
# system. DB verified April 16, 2026: clothingOccasion attribute has 65
# services total (62 Everyday, 28 Job Interview — overlapping).
#
# YourPeer sends this as taxonomySpecificAttributes[0]=clothingOccasion&
# taxonomySpecificAttributes[1]=Everyday (or "Job Interview"). The chatbot
# queries the DB directly, so we use the JSONB @> containment operator
# against service_taxonomy_specific_attributes."values".
#
# The :clothing_occasion_value param is a JSON array string, e.g.
# '["Everyday"]' or '["Job Interview"]'.
FILTER_BY_CLOTHING_OCCASION = (
    """EXISTS (
        SELECT 1 FROM service_taxonomy_specific_attributes stsa
        JOIN taxonomy_specific_attributes tsa ON stsa.attribute_id = tsa.id
        WHERE stsa.service_id = s.id
          AND tsa.name = 'clothingOccasion'
          AND stsa."values" @> :clothing_occasion_value::jsonb
    )""",
    ["clothing_occasion_value"],
)

# ---------------------------------------------------------------------------
# ORDER + LIMIT
# ---------------------------------------------------------------------------
# Sorting priority (base):
#   1. Freshness tier (CASE 0/1/2)  — fresh ≤90d first, stale next, NULL last
#   2. Continuous timestamp          — most recently verified within a tier
#   3. Service name                  — stable alphabetical tiebreaker
#
# With optional layers (prepended/inserted in this order):
#   - Population boosts (LGBTQ, veteran, description) sort BEFORE the base
#     — see _LGBTQ_BOOST_RANK, _VETERAN_BOOST_RANK, _DESCRIPTION_BOOST_RANK
#   - Distance band (_DISTANCE_BAND_RANK) sorts BETWEEN boosts and freshness
#     — so freshness wins within a walking-time band
#   - Continuous distance (_DISTANCE_TIEBREAK) sorts AFTER freshness but
#     BEFORE name — a closer service beats a farther one when band + tier tie
#
# Open-now ordering is applied in Python by `_sort_open_first()` after the
# SQL query returns (see query_executor.py). This is the single source of
# truth for open-status sorting — SQL does not contribute.
#
# Why not SQL for open-now? The Python `_sort_open_first()` distinguishes
# three buckets (open < closed < unknown), while a SQL CASE expression
# conflates closed and unknown at 1. Earlier revisions had both layers
# running — Python's rank always overrode SQL's, making SQL's contribution
# cosmetic. One source of truth eliminates a drift vector.
#
# See docs/design/FRESHNESS_TIER_SPEC.md and docs/design/BUCKETED_DISTANCE_SORT_SPEC.md
# for the motivation behind the tiered freshness + distance-band design.

# Open-now sort expression — INTENTIONALLY NOT USED in _BASE_ORDER_PARTS
# (see comment above). Retained as documentation of the shape of a SQL-level
# open-now rank if ever reintroduced, and for reference from unit tests.
#
# Timezone note: uses ``CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York'``
# so a future reintroduction inherits the same NYC-time semantics that
# ``today_sched`` and Python's ``_compute_schedule_status`` already use.
# Do NOT replace with bare ``CURRENT_TIME`` — that evaluates in the DB
# session's timezone and reintroduces the May 2026 bug.
_OPEN_NOW_RANK = """CASE
    WHEN today_sched.opens_at IS NOT NULL
         AND today_sched.closes_at IS NOT NULL
         AND today_sched.opens_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::time
         AND today_sched.closes_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')::time
    THEN 0 ELSE 1
END"""

# LGBTQ taxonomy boost: returns 0 for services tagged "LGBTQ Young Adult",
# 1 for everything else. Floats affirming services (e.g., Ali Forney Center)
# to the top of results without excluding non-LGBTQ services.
# Only active when user identifies as LGBTQ/trans/nonbinary.
_LGBTQ_BOOST_RANK = """CASE
    WHEN EXISTS (
        SELECT 1 FROM service_taxonomy st_lgbtq
        JOIN taxonomies t_lgbtq ON st_lgbtq.taxonomy_id = t_lgbtq.id
        WHERE st_lgbtq.service_id = s.id
        AND LOWER(t_lgbtq.name) = 'lgbtq young adult'
    ) THEN 0 ELSE 1
END"""

# Veteran taxonomy boost: floats services tagged "Veterans" to top.
# Same pattern as LGBTQ boost. Only active when user identifies as veteran.
_VETERAN_BOOST_RANK = """CASE
    WHEN EXISTS (
        SELECT 1 FROM service_taxonomy st_vet
        JOIN taxonomies t_vet ON st_vet.taxonomy_id = t_vet.id
        WHERE st_vet.service_id = s.id
        AND LOWER(t_vet.name) = 'veterans'
    ) THEN 0 ELSE 1
END"""

# Description-based population boost: floats services whose description
# matches a regex pattern to the top. Used for disabled, reentry,
# dv_survivor, pregnant, senior populations. The pattern is passed as
# :pop_boost_pattern bind parameter.
_DESCRIPTION_BOOST_RANK = """CASE
    WHEN s.description ~* :pop_boost_pattern THEN 0 ELSE 1
END"""

# Distance expression for proximity-based ORDER BY.
#
# When proximity is active, distance is split into two ORDER BY keys:
#
#   _DISTANCE_BAND_RANK   — bucketed (0/1/2/3) band rank, used BEFORE freshness
#   _DISTANCE_TIEBREAK    — continuous meters, used AFTER freshness as tiebreak
#
# Rationale: a continuous distance sort lets a service 800m away always beat
# one 801m away, even if the closer one hasn't been verified in a year. By
# bucketing into walking-time bands (<500m / 500m-1km / 1km-2km / 2km+) and
# sorting by freshness WITHIN each band, recently verified services surface
# first among "equally walkable" options. Continuous distance is retained as
# a secondary tiebreaker to keep ordering stable when band + freshness tie.
#
# See docs/design/BUCKETED_DISTANCE_SORT_SPEC.md for the full design.
_DISTANCE_BAND_RANK = (
    "CASE"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 500 THEN 0"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 1000 THEN 1"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 2000 THEN 2"
    " ELSE 3"
    " END"
)

# Continuous distance — only used as a secondary tiebreaker after freshness
# when a proximity search is active. Within the same band + freshness tier,
# the physically closer service wins.
_DISTANCE_TIEBREAK = (
    "ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)"
)

# Back-compat alias — some external callers / tests may reference the old
# name. The old continuous-only behavior is equivalent to _DISTANCE_TIEBREAK.
_DISTANCE_RANK = _DISTANCE_TIEBREAK

# Freshness threshold in days. Shared with query_executor._compute_freshness
# (imported from here) so the SQL sort's "fresh" tier matches what the
# displayed "X of Y verified in last N days" stats report.
_FRESHNESS_DAYS = 90

# Tiered freshness CASE — 3 buckets:
#   0 = fresh (verified within _FRESHNESS_DAYS)
#   1 = stale (verified, but older than _FRESHNESS_DAYS)
#   2 = never verified (last_validated_at IS NULL)
#
# Within a distance band, this promotes "recent enough to trust" services
# over older-but-still-verified ones, and demotes unverified ones to the
# bottom. Paired with the continuous timestamp as a tiebreaker within
# each tier. See docs/design/FRESHNESS_TIER_SPEC.md for the full design.
_FRESHNESS_TIER_RANK = f"""CASE
    WHEN l.last_validated_at >= CURRENT_DATE - INTERVAL '{_FRESHNESS_DAYS} days' THEN 0
    WHEN l.last_validated_at IS NOT NULL THEN 1
    ELSE 2
END"""

# Base sort tiebreakers: tiered freshness, then continuous timestamp within
# each tier, then name.
# Open-now ordering is handled post-query by Python `_sort_open_first()`
# — see comment block above for rationale.
_BASE_ORDER_PARTS = [
    _FRESHNESS_TIER_RANK,
    "l.last_validated_at DESC NULLS LAST",
    "s.name",
]

_DEFAULT_MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# SERVICE-NAME PATTERNS — Phase B / Ticket K
# ---------------------------------------------------------------------------
# Regex patterns used by FILTER_BY_TAXONOMY_OR_NAME_PATTERN to surface
# `Other service` parent-direct services whose service.name matches a
# curated pattern. These supplement leaf-tagged matches when Streetlives
# tagging hasn't caught up to a service's true category.
#
# PostgreSQL POSIX (ARE) regex flavor:
#   - `\b` is the general word boundary (synonym for `\y` in ARE).
#   - `(?:...)` is the non-capturing group, supported in ARE.
#   - `~*` (used by the filter) is case-insensitive matching.
#   - No Python-specific syntax (no `(?P<name>...)`, no `(?i)` inline
#     flag — case-insensitivity comes from the operator).
#
# Patterns are written as parenthesized alternations of branches; each
# branch matches a distinct service name shape (e.g. "Adult Education"
# vs "ESL Plus" vs "Citizenship Classes"). Word-boundary anchors on
# both ends prevent substring false positives.
#
# DB-verification methodology: each pattern was sanity-checked against
# a sample of Other-service-parent-direct service names from the May
# 2026 audit (Appendix A's alphabetical sample). The intent is to
# match 50–150 additional services per promoted service_type without
# false-positive matches in unrelated trees (which the filter already
# rules out by anchoring to t.name = 'other service' AND parent_id IS NULL).

# Education (Ticket C — DB: 101 leaf + estimated 80–130 parent-direct)
# Branches cover the four sub-clusters observed in the parent-direct
# bucket: adult-ed, language/ESL, equivalency/GED, citizenship/civics,
# tech/digital-literacy, and college-access.
_EDUCATION_NAME_PATTERN = (
    r"\b("
    r"adult\s+education|adult\s+literacy|adult\s+learning|"
    r"academic\s+enrichment|"
    r"esl|english\s+(?:as\s+a\s+second|classes|language)|"
    r"ged|hse|high\s+school\s+equivalency|"
    r"citizenship\s+(?:class|test|prep)|"
    r"attain|"
    r"college\s+(?:access|prep|success|preparedness)|"
    r"continued\s+education|"
    r"literacy\s+(?:program|zone|project)|"
    r"educational\s+services|"
    r"early\s+childhood\s+education|"
    r"pre-?k\s+to\s+12|"
    r"head\s+start|"
    r"computer\s+(?:classes|skills|lab)|digital\s+literacy|"
    r"tech\s+training|advanced\s+technology\s+training"
    r")\b"
)


# ---------------------------------------------------------------------------
# TEMPLATE DEFINITIONS
# ---------------------------------------------------------------------------
# Each template specifies which filters are always applied and which are
# conditional (applied only if the user provided that slot).

TEMPLATES = {
    "food": {
        "name": "FoodQuery",
        "description": "Find food services (pantries, soup kitchens, meals) by location",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
            # Deliberately NOT including FILTER_BY_OPEN_NOW.
            # The chatbot uses sort-only semantics for open-now: services with
            # no schedule data (majority of the DB — see QUERY_PARITY_AUDIT.md
            # Section "Open-now behavior") get ranked below open services, but
            # are NOT excluded from results. YourPeer uses exclude-semantics
            # via its `openAt` API param; the chatbot diverges intentionally.
            # If reintroducing, coordinate with the schedule-sort logic in
            # query_executor._sort_open_first() and update the audit doc.
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "food",
                "food pantry",
                "food benefits",
                "mobile pantry",
                "mobile food truck",
                "mobile market",
                "food delivery / meals on wheels",
                "soup kitchen",
                "mobile soup kitchen",
                "brown bag",
                "farmer's markets",
            ]
        },
        "taxonomy_aliases": [
            "Food", "Food Pantry", "Food Benefits", "Mobile Pantry",
            "Mobile Food Truck", "Mobile Market", "Food Delivery / Meals on Wheels",
            "Soup Kitchen", "Mobile Soup Kitchen", "Brown Bag", "Farmer's Markets",
        ],
    },
    "shelter": {
        "name": "HousingEligibilityQuery",
        "description": "Find shelters and housing with eligibility checks",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            # Shelter parent + non-vestigial children in the Streetlives DB.
            # YourPeer sends just the "Shelter" parent ID and relies on the API to
            # expand to children server-side. The chatbot queries the DB directly,
            # so children must be enumerated explicitly to get equivalent coverage.
            #
            # Sub-category narrowing (family_status → families / single adult) is
            # handled in rag/__init__.py. As of the May 2026 taxonomy audit fix,
            # narrowing PRESERVES the generic shelter-mode children (crisis,
            # drop-in center, referral, etc.) instead of stripping them — this
            # fixes the regression where single adults asking for shelter lost
            # visibility into emergency beds, drop-in centers, and placement
            # referrals.
            #
            # DB verified April 16, 2026: 19 Shelter children total.
            # OMITTED from default:
            #   • Cooling Center (0 services)         — vestigial (Ticket J).
            #   • Intake (0 services)                  — vestigial (Ticket J).
            #   • Supportive Housing (0 services)      — vestigial (Ticket J,
            #     audit §IX). Was here for forward-compat; the audit confirms
            #     it has 0 services tagged and recommends removal.
            #   • Residential Recovery (2 services)    — REMOVED May 2026 per
            #     TAXONOMY_AUDIT_MAY2026.md §VIII (the CREATE Inc. bug). These
            #     are substance-use treatment programs, not shelter; default
            #     inclusion confused the "I need a place to sleep tonight" ask.
            #     Still reachable via the mental_health template (recovery
            #     programs) and via service_detail narrowing ("detox",
            #     "sober living", etc. — see rag/__init__.py
            #     _DETAIL_TO_TAXONOMY_NARROWING).
            "taxonomy_names": [
                # Parent + generic housing modes (always applicable)
                "shelter",
                "transitional independent living (til)",
                "housing lottery",
                "safe haven",
                # Population-specific shelter children (kept in default so a
                # generic "I need shelter" query without a family_status still
                # surfaces them; narrowing logic in rag/__init__.py strips the
                # population-children that don't match the user's family_status
                # while keeping the generic modes above intact).
                "youth",
                "families",
                "single adult",
                "senior",
                "lgbtq young adult",
                "veterans",
                # Service-mode shelter children (added Apr 16, 2026 after
                # Covenant House / Safe Horizon DB verification revealed these
                # were missing from the default list, making services like
                # Emergency Bed Placement and Shelter Placement invisible
                # to default shelter queries).
                "crisis",           # 13 services — emergency beds, crisis placement
                "drop-in center",   # 6 services — day sleeping rooms, drop-in
                "referral",         # 6 services — shelter placement referrals
                "assessment",       # 1 service — intake assessment
                # NOT in default (added conditionally via rag/__init__.py
                # enrichment when the relevant signal is present, per
                # TAXONOMY_AUDIT_MAY2026.md §VIII):
                #
                #   • veterans short-term housing (2 svc) — added when
                #     populations contains "veteran". Already population-
                #     specific to veterans; surfacing for non-veteran users
                #     is misleading. May 2026 audit follow-up removal.
                #
                #   • warming center (1 svc) — added when cold_context slot
                #     is True. Seasonal-only service that should only surface
                #     when the user signals cold-weather context (e.g.,
                #     "freezing", "out of the cold", "warming center").
                #     May 2026 audit follow-up removal.
            ]
        },
        "taxonomy_aliases": [
            "Shelter", "Transitional Independent Living (TIL)",
            "Housing Lottery", "Safe Haven",
            "Youth", "Families", "Single Adult", "Senior", "LGBTQ Young Adult", "Veterans",
            "Crisis", "Drop-in Center", "Referral", "Assessment",
            # Conditional-only (added via enrichment) — listed here for
            # taxonomy_aliases parity with the enriched query results.
            "Veterans Short-Term Housing", "Warming Center",
        ],
    },
    "clothing": {
        "name": "ClothingQuery",
        "description": "Find clothing distribution services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_CLOTHING_OCCASION,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "clothing",
                "clothing pantry",
                "interview-ready clothing",
                "professional clothing",
                "coat drive",
                "thrift shop",
            ]
        },
        "taxonomy_aliases": [
            "Clothing", "Clothing Pantry", "Interview-Ready Clothing",
            "Professional Clothing", "Coat Drive", "Thrift Shop",
        ],
    },
    "medical": {
        "name": "HealthcareQuery",
        "description": "Find medical and healthcare services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            # DB-verified Health parent + children (April 2026 prod audit):
            #   Health (parent, 588 services)
            #   ├── General Health (48)
            #   ├── Mental Health (128) — intentionally EXCLUDED, handled by
            #   │                         mental_health template. Matches
            #   │                         YourPeer's client-side Mental Health
            #   │                         exclusion from health-care view.
            #   ├── Substance Use Treatment (11)
            #   └── Support Groups (8)
            #
            # Previously included "crisis" here — that was a BUG. Crisis is a
            # Shelter child (13 services), not a Health child. It caused
            # medical queries to pull crisis shelter services. Fixed Apr 2026.
            "taxonomy_names": [
                "health",
                "general health",
                "substance use treatment",
                "support groups",
            ]
        },
        "taxonomy_aliases": [
            "Health", "General Health", "Substance Use Treatment", "Support Groups",
        ],
    },
    "legal": {
        "name": "LegalQuery",
        "description": "Find legal aid and immigration services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "legal services",
                "immigration services",
            ]
        },
        "taxonomy_aliases": ["Legal Services", "Immigration Services"],
    },
    "employment": {
        "name": "EmploymentQuery",
        "description": "Find job training and employment services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "employment",
                "internship",
            ]
        },
        "taxonomy_aliases": ["Employment", "Internship"],
    },
    "personal_care": {
        "name": "PersonalCareQuery",
        "description": "Find showers, laundry, toiletries, and hygiene services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "personal care",
                "shower",
                "laundry",
                "toiletries",
                "hygiene",
                "haircut",
                "restrooms",
            ]
        },
        "taxonomy_aliases": [
            "Personal Care", "Shower", "Laundry", "Toiletries",
            "Hygiene", "Haircut", "Restrooms",
        ],
    },
    "mental_health": {
        "name": "MentalHealthQuery",
        "description": "Find mental health, counseling, and substance use services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "mental health",
                "substance use treatment",
                "residential recovery",
                "support groups",
            ]
        },
        "taxonomy_aliases": [
            "Mental Health", "Substance Use Treatment",
            "Residential Recovery", "Support Groups",
        ],
    },
    "education": {
        "name": "EducationQuery",
        "description": (
            "Find education and learning programs (ESL, GED, adult education, "
            "computer classes, citizenship classes, college prep)"
        ),
        # Phase B Ticket C promotion. Uses the OR'd filter so the query
        # matches BOTH leaf-tagged services (Other service › Education, 101
        # services) AND parent-direct tagging-debt services whose name
        # matches _EDUCATION_NAME_PATTERN. See TAXONOMY_AUDIT_MAY2026.md
        # §IX and PHASE_B_PROMOTION_PLAN.md.
        "required_filters": [
            FILTER_BY_TAXONOMY_OR_NAME_PATTERN,
            FILTER_NOT_HIDDEN,
            FILTER_BY_STATE_NY,
        ],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            # Only the leaf — Education is a single Other-service child. The
            # parent-direct tagging-debt bucket is reached via the
            # service_name_pattern branch of FILTER_BY_TAXONOMY_OR_NAME_PATTERN.
            #
            # Internship deliberately omitted: the audit (TAXONOMY_AUDIT_MAY2026.md
            # Appendix A) shows ONE Internship taxonomy under `Other service`
            # with 3 services. It's already used by the `employment` template
            # (and conceptually belongs there — internships are employment-
            # adjacent). Including it here would double-route the same 3
            # services to both templates.
            "taxonomy_names": ["education"],
            "service_name_pattern": _EDUCATION_NAME_PATTERN,
        },
        "taxonomy_aliases": ["Education"],
    },
    "other": {
        "name": "OtherServicesQuery",
        "description": (
            "Find benefits, case workers, and miscellaneous Other-service-tree "
            "services (education/legal/employment/immigration are now their "
            "own service_types)"
        ),
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            # Other-service-tree taxonomies only.
            #
            # May 2026 fix (TAXONOMY_AUDIT_MAY2026.md §VIII follow-up):
            # the previous default included 15 taxonomies parented under
            # *other* DB trees, polluting `service_type=other` results with
            # shelter / personal-care / clothing services. Specifically the
            # 10 Shelter children listed below were causing shelter services
            # to surface in non-shelter queries (e.g., a "benefits in Brooklyn"
            # search returning a Single Adult shelter, because both `single
            # adult` and `benefits` were in the taxonomy_names IN clause).
            #
            # The eval suite already noted the symptom — see
            # `natural_drop_in_center` scenario description in
            # tests/eval/eval_llm_judge.py ("an 'other' query won't return
            # drop-in centers"). Drop-in centers route via shelter template;
            # they should never have been in `other`.
            #
            # REMOVED from default (DB tree → correct template):
            #   • drop-in center, referral, assessment, single adult,
            #     families, youth, senior, veterans, lgbtq young adult,
            #     intake               → Shelter tree (shelter template)
            #   • baby supplies        → Clothing tree (clothing template,
            #                            via service_detail="baby supplies"
            #                            narrowing)
            #   • baby, community services, activities, gym
            #                          → Personal Care tree (personal_care
            #                            template)
            #   • appliances           → PHANTOM (does not exist in DB,
            #                            April 2026 verification)
            #   • pets                 → Other-tree but VESTIGIAL (0 services,
            #                            audit Ticket J)
            #   • education            → PROMOTED to `service_type=education`
            #                            in Phase B (TAXONOMY_AUDIT_MAY2026.md
            #                            §IX Ticket C). The 101 leaf-tagged
            #                            Education services + name-pattern
            #                            matches against `Other service`
            #                            parent-direct are now reachable via
            #                            the education template's OR'd filter.
            #
            # KEPT (all Other-service-tree children with non-zero services
            # that aren't already promoted to a dedicated template):
            "taxonomy_names": [
                "other service",  # parent — 1,105 services tagged here directly
                "benefits",       # 32 svc — pending Phase B Ticket D promotion
                "case workers",   # 28 svc — pending Phase B Ticket E promotion
                "free wifi",      # 8 svc
                "mail",           # 6 svc
                "taxes",          # 2 svc
                # NOT included (promoted to their own templates):
                #   legal services, immigration services → legal template
                #   employment, internship               → employment template
                #   education                            → education template
            ]
        },
        "taxonomy_aliases": [
            "Other service", "Benefits", "Case Workers",
            "Free Wifi", "Mail", "Taxes",
        ],
    },
    "org_name": {
        "name": "OrgNameQuery",
        "description": "Find all services at a specific organization by name",
        "required_filters": [FILTER_BY_ORG_NAME, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
        ],
        "default_params": {},
        "taxonomy_aliases": [],
    },
}


# ---------------------------------------------------------------------------
# QUERY BUILDER
# ---------------------------------------------------------------------------

def build_query(template_key: str, user_params: dict) -> tuple[str, dict]:
    """
    Assemble a parameterized SQL query from a template and user-provided slots.

    Args:
        template_key: One of the keys in TEMPLATES (e.g. "food", "shelter")
        user_params:  Dict of slot values from the intake form, e.g.
                      {"city": "Brooklyn", "age": 17, "gender": "male"}

    Returns:
        (sql_string, bound_params) ready for SQLAlchemy text() execution.

    Raises:
        ValueError: If template_key is not recognized.
    """
    if template_key not in TEMPLATES:
        raise ValueError(
            f"Unknown template '{template_key}'. "
            f"Valid templates: {list(TEMPLATES.keys())}"
        )

    template = TEMPLATES[template_key]

    # Start with default params, then overlay user params
    params = dict(template["default_params"])
    params.update({k: v for k, v in user_params.items() if v is not None})

    # When taxonomy narrowing fires (e.g. "detox" → substance use treatment),
    # any default description_pattern from the template must be neutralized.
    # Otherwise the SQL applies BOTH the narrowed taxonomy IN-list AND the
    # original description filter — which are almost certainly incompatible
    # and return 0 results.
    #
    # Replace with a match-all pattern instead, which effectively disables
    # the filter while keeping the SQL bind variable satisfied.
    if params.pop("_skip_description_filter", False):
        if "description_pattern" in params:
            params["description_pattern"] = "."

    # Collect WHERE clauses
    where_clauses = []

    # Required filters — always applied
    for sql_fragment, required_keys in template["required_filters"]:
        where_clauses.append(sql_fragment)

    # Optional filters — only applied if user provided the required params
    for sql_fragment, required_keys in template["optional_filters"]:
        if all(k in params for k in required_keys):
            where_clauses.append(sql_fragment)

    # Universal optional filters — apply to any template when params present.
    # Co-location filter: when user asked for multiple services, restrict
    # results to locations that also have the additional service(s).
    _UNIVERSAL_OPTIONAL = [FILTER_BY_COLOCATED_TAXONOMY, FILTER_BY_NO_REQUIREMENTS]
    for sql_fragment, required_keys in _UNIVERSAL_OPTIONAL:
        if all(k in params for k in required_keys):
            where_clauses.append(sql_fragment)

    # Assemble the full query
    where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

    # Set max_results default
    if "max_results" not in params:
        params["max_results"] = _DEFAULT_MAX_RESULTS

    # -----------------------------------------------------------------
    # Dynamic ORDER BY builder
    # -----------------------------------------------------------------
    # Builds the ORDER BY clause from active boost ranks + base sort.
    # This replaces pre-defined ORDER BY constants (which required a
    # combinatorial explosion of variants for each boost combination).
    _lgbtq_boost = params.pop("lgbtq_boost", False)
    _veteran_boost = params.pop("veteran_boost", False)
    # no_requirements is a control flag (triggers a NOT EXISTS filter)
    # with no SQL bind variable — pop it so SQLAlchemy doesn't error.
    params.pop("no_requirements", None)
    _has_distance = "lat" in params and "lon" in params
    _has_pop_boost = "pop_boost_pattern" in params

    order_parts = []

    # 1. Population boosts (highest priority — floats matching services up)
    if _lgbtq_boost:
        order_parts.append(_LGBTQ_BOOST_RANK)
    if _veteran_boost:
        order_parts.append(_VETERAN_BOOST_RANK)
    if _has_pop_boost:
        order_parts.append(_DESCRIPTION_BOOST_RANK)

    # 2. Distance BAND (when proximity search is active) — bucketed so
    #    freshness can sort within each walking-time band.
    if _has_distance:
        order_parts.append(_DISTANCE_BAND_RANK)

    # 3. Base tiebreakers: tiered freshness, then continuous timestamp
    #    within each tier, then name.
    #    When proximity is active, continuous distance is inserted BEFORE
    #    name (but AFTER freshness) so a closer service still beats a
    #    farther one within the same band + freshness tier.
    if _has_distance:
        # Split _BASE_ORDER_PARTS into (fresh_parts, name_part): everything
        # that sorts before continuous distance comes first (freshness tier
        # + continuous timestamp), then distance goes in, then name is the
        # final stable tiebreaker. Unpacking rather than indexing keeps this
        # robust against future reshaping of _BASE_ORDER_PARTS.
        *fresh_parts, name_part = _BASE_ORDER_PARTS
        order_parts.extend(fresh_parts)
        order_parts.append(_DISTANCE_TIEBREAK)
        order_parts.append(name_part)
    else:
        order_parts.extend(_BASE_ORDER_PARTS)

    order_clause = f"\nORDER BY {', '.join(order_parts)}\nLIMIT :max_results\n"

    full_sql = f"{_BASE_QUERY}\nWHERE {where_sql}\n{order_clause}"

    return full_sql, params


def build_relaxed_query(template_key: str, user_params: dict) -> tuple[str, dict]:
    """
    Build a relaxed version of the query for when the strict version
    returns zero results. Drops filters progressively but KEEPS location
    boundaries to prevent out-of-area results:

    1. Drop time/schedule filters
    2. Drop eligibility filters (age, gender)
    3. Drop proximity filters (lat, lon, radius) — broadens from
       neighborhood-level to borough-level
    4. Broaden city match:
       - If _borough_city_list exists: promote to city_list for ANY() match
       - If city_list exists: keep it, drop exact city match
       - No expansion available: exact city → LIKE pattern
    5. State filter (NY) is NEVER dropped

    DESIGN DECISION: Taxonomy narrowing (taxonomy_names) and description
    filters (description_pattern) are intentionally KEPT. If someone
    asked for "detox in Staten Island" and SI has no detox services,
    the relaxed query looks for detox across NYC — not all mental
    health services. Showing counseling when the user asked for detox
    would be unhelpful. If the relaxed query ALSO returns 0, the
    chatbot's no-result handler shows the user what we searched for
    and suggests alternatives.

    Returns the broadest reasonable query. Caller should note to the user
    that results may be less precisely matched.
    """
    relaxed_params = dict(user_params)

    # Remove schedule-related params
    for key in ["weekday", "current_time"]:
        relaxed_params.pop(key, None)

    # Remove eligibility params
    for key in ["age", "gender"]:
        relaxed_params.pop(key, None)

    # Remove proximity params — broadens from neighborhood to full borough
    for key in ["lat", "lon", "radius_meters"]:
        relaxed_params.pop(key, None)

    # Note: the "borough" param used to be dropped here when FILTER_BY_BOROUGH
    # existed (against pa.borough, which doesn't exist in prod). Removed
    # Apr 17, 2026 along with the filter itself. Borough narrowing is now
    # entirely via city_list.

    # Promote _borough_city_list (from neighborhood searches) to city_list
    # so the relaxed query broadens from "Harlem" to all of Manhattan.
    if "_borough_city_list" in relaxed_params:
        relaxed_params["city_list"] = relaxed_params.pop("_borough_city_list")
        relaxed_params.pop("city", None)
    elif "city_list" in relaxed_params:
        # Borough expansion already covers neighborhoods — drop exact match
        relaxed_params.pop("city", None)
    elif "city" in relaxed_params:
        # No expansion available — broaden to LIKE
        city = relaxed_params.pop("city")
        relaxed_params["city_pattern"] = f"%{city}%"

    return build_query(template_key, relaxed_params)


# ---------------------------------------------------------------------------
# RESULT FORMATTER
# ---------------------------------------------------------------------------

def _normalize_url(url) -> str | None:
    """Ensure a URL has a protocol prefix so browsers open it as absolute."""
    url = _safe_str(url)
    if not url:
        return None
    if not url.startswith(("http://", "https://", "//")):
        return "https://" + url
    return url


def _format_eligibility(rules) -> str | None:
    """Format eligibility rules into a human-readable summary.

    Args:
        rules: JSON array of {param, values} from the eligibility subquery,
               or None if no eligibility rules exist.

    Returns:
        A concise string like "Ages 18–24 · Women only" or None.
    """
    if not rules:
        return None

    parts = []
    for rule in rules:
        param = rule.get("param", "")
        values = rule.get("values")
        if not values:
            continue

        if param == "age":
            # values: [{"age_min": 18, "age_max": 24}] or [{"all_ages": true}]
            if isinstance(values, list) and values:
                v = values[0] if isinstance(values[0], dict) else {}
                if v.get("all_ages"):
                    continue  # open to all ages — nothing to display
                age_min = v.get("age_min")
                age_max = v.get("age_max")
                if age_min and age_max:
                    parts.append(f"Ages {age_min}–{age_max}")
                elif age_min:
                    parts.append(f"Ages {age_min}+")
                elif age_max:
                    parts.append(f"Ages up to {age_max}")

        elif param == "gender":
            # values: ["male"] or ["female"] or ["male", "female"]
            if isinstance(values, list):
                genders = [str(g).capitalize() for g in values if g]
                if len(genders) == 1:
                    parts.append(f"{genders[0]} only")
                # If both genders listed, skip — it's open to all

        elif param == "familySize":
            # values: [{"min": 2}] or similar
            if isinstance(values, list) and values:
                v = values[0] if isinstance(values[0], dict) else {}
                if v.get("min") and int(v["min"]) > 1:
                    parts.append("Families")

    if not parts:
        return None
    return " · ".join(parts)


def _clean_list(values) -> list | None:
    """Return a cleaned list or None if empty/null.

    Filters out None, empty strings, and 'None' from DB array results.
    Returns None instead of [] so the frontend can use simple truthiness checks.
    """
    if not values:
        return None
    cleaned = [v for v in values if v and str(v).strip() not in ("", "None")]
    return cleaned if cleaned else None


def _safe_str(value) -> str | None:
    """Safely convert a DB value to string, or None if empty.

    PostgreSQL column types are not always what the schema says —
    migrations, defaults, and JSONB extraction can produce unexpected
    types. This function ensures .strip() and other string operations
    never crash on a non-string value.
    """
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _coerce_float(value) -> float | None:
    """Convert a DB value to float, or None if conversion fails.

    PostGIS ST_X/ST_Y return double precision, but psycopg2 + SQLAlchemy
    may surface them as Decimal in some environments. This keeps the
    card shape predictable for downstream JSON serialization and the
    borough validator.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_phone(number, extension) -> str | None:
    """Format a phone number with optional extension."""
    if not number:
        return None
    num = _safe_str(number) or ""
    ext = _safe_str(extension) or ""
    if ext and ext.lower() not in ("none", "n/a"):
        return f"{num} ext. {ext}"
    return num or None


def format_service_card(row: dict) -> dict:
    """
    Format a raw query result row into a structured service card.

    This is the ONLY place service data is assembled for display.
    No LLM synthesis — just field mapping.
    """
    # Build address string
    address_parts = [
        _safe_str(row.get("address")),
        _safe_str(row.get("city")),
        _safe_str(row.get("state")),
        _safe_str(row.get("zip_code")),
    ]
    full_address = ", ".join(p for p in address_parts if p)

    # Build YourPeer listing URL from location slug
    slug = row.get("location_slug")
    yourpeer_url = f"https://yourpeer.nyc/locations/{slug}" if slug else None

    # Build schedule / open status
    today_opens = row.get("today_opens")
    today_closes = row.get("today_closes")
    schedule_status = _compute_schedule_status(today_opens, today_closes)

    # Co-located services — show granular taxonomy names, not just top-level.
    # Matches what yourpeer.nyc displays under each location.
    _DISPLAY_CATEGORIES = {
        # Shelter & Housing
        "Shelter", "Drop-in Center", "Warming Center", "Crisis",
        "Families", "Single Adult", "Youth", "Senior",
        # Food
        "Food", "Food Pantry", "Soup Kitchen", "Mobile Pantry",
        "Mobile Soup Kitchen", "Mobile Market", "Food Benefits",
        "Farmer's Markets",
        # Clothing
        "Clothing", "Clothing Pantry", "Interview-Ready Clothing",
        # Personal Care
        "Shower", "Laundry", "Toiletries", "Haircut", "Restrooms",
        # Health (DB verified: parent + General Health, Mental Health,
        # Substance Use Treatment, Support Groups are the only Health children)
        "Health", "General Health", "Substance Use Treatment",
        # Mental Health
        "Mental Health",
        # Legal
        "Legal Services", "Immigration Services",
        # Employment & Education
        "Employment", "Education",
        # Benefits & Support
        "Benefits", "Case Workers", "Referral",
        "Support Groups", "Mail", "Free Wifi",
        # Other specific
        "Baby", "Baby Supplies", "Senior Center",
        "Financial Help", "Intake",
    }

    # User-friendly labels for DB-internal taxonomy names
    _TAXONOMY_DISPLAY_LABELS = {
        "General Health": "Health",
        "Substance Use Treatment": "Substance Use Help",
        "Case Workers": "Case Management",
        "Clothing Pantry": "Clothing",
        "Interview-Ready Clothing": "Interview Clothing",
        "Mobile Soup Kitchen": "Soup Kitchen",
        "Mobile Pantry": "Food Pantry",
        "Mobile Market": "Farmers Market",
        "Farmer's Markets": "Farmers Market",
        "Food Benefits": "Food Benefits (SNAP)",
        "Baby Supplies": "Baby Supplies",
    }

    raw_also = row.get("also_available") or []
    also_available = sorted({
        _TAXONOMY_DISPLAY_LABELS.get(name, name)
        for name in raw_also
        if name in _DISPLAY_CATEGORIES
    })

    return {
        "service_id": str(row.get("service_id", "")),
        "service_name": _safe_str(row.get("service_name")) or "Unknown Service",
        "organization": _safe_str(row.get("organization_name")),
        "description": _safe_str(row.get("service_description")),
        "address": full_address or None,
        "city": _safe_str(row.get("city")),
        # Coordinates from ST_Y/ST_X projection on l.position. Used by the
        # geographic-borough validator in query_executor; may also be used
        # by the frontend for mapping or distance display. NULL-safe —
        # services without position data pass through as None.
        "latitude": _coerce_float(row.get("latitude")),
        "longitude": _coerce_float(row.get("longitude")),
        "phone": _format_phone(row.get("phone"), row.get("phone_extension")),
        "email": _safe_str(row.get("service_email")),
        "website": _normalize_url(row.get("service_url") or row.get("organization_url")),
        "fees": _safe_str(row.get("fees")),
        "yourpeer_url": yourpeer_url,
        "hours_today": schedule_status["hours_today"],
        "is_open": schedule_status["is_open"],
        "requires_membership": bool(row.get("requires_membership")),
        "last_validated_at": (
            row["last_validated_at"].isoformat()
            if row.get("last_validated_at") and hasattr(row["last_validated_at"], "isoformat")
            else _safe_str(row.get("last_validated_at"))
        ),
        "also_available": also_available if also_available else None,
        "accessibility": _safe_str(row.get("accessibility_info")),
        "eligibility_summary": _format_eligibility(row.get("eligibility_rules")),
        "review_highlight": _safe_str(row.get("review_highlight")),
        "required_documents": _clean_list(row.get("required_documents")),
        "languages": _clean_list(row.get("languages_spoken")),
        # This service's own taxonomy tags — used for post-results
        # sub-category filtering. Kept as raw DB names (not display-
        # label-mapped) so filters match against canonical values.
        # Example: ["Shelter", "Families", "Intake"]
        "service_taxonomies": _clean_list(row.get("service_taxonomies")),
    }


def _compute_schedule_status(opens_at, closes_at, now=None) -> dict:
    """
    Compute human-readable hours and open/closed status.

    All comparisons are done in **America/New_York** time. The Streetlives
    DB stores ``holiday_schedules.opens_at`` / ``closes_at`` as naive
    ``time`` values that represent NYC-local hours (confirmed with
    Streetlives data team — every NYC org's hours are NYC-local).
    The bot's user base is also NYC-local, so the timezone of "now," the
    timezone of "user," and the timezone of the stored hours all share
    ``America/New_York``. ``zoneinfo.ZoneInfo`` handles DST transitions
    automatically.

    Why this matters: ``datetime.now()`` (no tz arg) returns
    ``datetime.now(LOCAL_TZ_OF_THE_PROCESS)``. On Render the container's
    ``TZ`` env is unset, so the process clock is **UTC**. Comparing UTC
    time-of-day against ET-stored ``opens_at`` / ``closes_at`` was the
    cause of the "service shows Closed when listed hours say it's Open"
    bug reported May 2026. See ``docs/audits/SCHEDULE_TZ_FIX.md`` for
    the full incident analysis.

    Args:
        opens_at: Time the service opens today, as a ``time`` object or
            ``HH:MM[:SS]`` string. Treated as NYC-local.
        closes_at: Time the service closes today, same shape as
            ``opens_at``. Treated as NYC-local. Values where ``closes_at
            < opens_at`` (e.g., ``22:00`` and ``06:00``) are interpreted
            as wrapping past midnight.
        now: Optional ``time`` object representing the current time
            in NYC. **Tests must always pass this explicitly** so that
            assertions are wall-clock-independent. In production this is
            ``None`` and resolves to ``datetime.now(ZoneInfo("America/
            New_York")).time()``.

    Returns:
        dict with keys:
            ``hours_today``: ``str`` like ``"9:00 AM – 5:00 PM"`` or
                ``None`` if either bound is missing or unparseable.
            ``is_open``: ``"open"`` | ``"closed"`` | ``None``.
                ``None`` only when no schedule data was supplied; the
                function never returns ``None`` for parseable input.
    """
    if opens_at is None or closes_at is None:
        return {"hours_today": None, "is_open": None}

    from datetime import datetime, time as dt_time

    # Parse the opens_at / closes_at values.
    # They come from the DB as time strings (HH:MM:SS) or time objects.
    try:
        if isinstance(opens_at, str):
            open_time = datetime.strptime(opens_at.strip(), "%H:%M:%S").time()
        elif isinstance(opens_at, dt_time):
            open_time = opens_at
        else:
            open_time = datetime.strptime(str(opens_at).strip()[:8], "%H:%M:%S").time()

        if isinstance(closes_at, str):
            close_time = datetime.strptime(closes_at.strip(), "%H:%M:%S").time()
        elif isinstance(closes_at, dt_time):
            close_time = closes_at
        else:
            close_time = datetime.strptime(str(closes_at).strip()[:8], "%H:%M:%S").time()
    except (ValueError, AttributeError):
        return {"hours_today": None, "is_open": None}

    # Format for display
    open_str = format_time(open_time)
    close_str = format_time(close_time)
    hours_today = f"{open_str} – {close_str}"

    # All-day detection. Some Streetlives entries use `00:00:00 – 23:59:00`
    # (or `:59:59`, or `00:00:00 – 00:00:00` interpreted as midnight-to-
    # midnight) to mean "open all day." Showing those literal endpoints
    # to users is confusing — `12:00 AM – 11:59 PM` reads as "narrowly
    # not 24 hours" rather than "yes, all day," and the visual bulk
    # competes with the actually-useful badge text. Replace the literal
    # range with the friendlier "Open 24 hours" so users see one tidy
    # label instead of two pieces of redundant information.
    #
    # Detection rules:
    #   • opens_at == 00:00:00 (start-of-day exactly), AND
    #   • closes_at is one of: 23:59:00, 23:59:59, 00:00:00.
    #     The first two are end-of-day-rounded; the third is the
    #     midnight-to-midnight wrap convention. Anything narrower
    #     (e.g. 23:00:00 close) keeps the original literal range so
    #     we don't over-claim 24-hour availability.
    is_all_day = (
        open_time == dt_time(0, 0, 0)
        and (
            close_time == dt_time(23, 59, 0)
            or close_time == dt_time(23, 59, 59)
            or close_time == dt_time(0, 0, 0)
        )
    )
    if is_all_day:
        hours_today = "Open 24 hours"

    # Determine if currently open. Default `now` to NYC time.
    # Tests inject `now` explicitly (see TestComputeScheduleStatus in
    # tests/unit/test_query_templates.py) so assertions don't depend on
    # wall-clock time at test execution.
    if now is None:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York")).time()
    if is_all_day:
        # All-day case is unambiguously open. The is_open determination
        # below uses range comparison that misbehaves for the
        # 00:00:00 – 00:00:00 wrap case (open <= now <= close yields
        # False at most times since both bounds are 00:00:00). Short-
        # circuit to avoid the false-closed result.
        is_open = "open"
    elif open_time <= close_time:
        is_open = "open" if open_time <= now <= close_time else "closed"
    else:
        # Wraps midnight (e.g. 8 PM – 6 AM)
        is_open = "open" if now >= open_time or now <= close_time else "closed"

    return {"hours_today": hours_today, "is_open": is_open}


def deduplicate_results(rows: list[dict]) -> list[dict]:
    """
    Remove duplicate service cards (same service can appear multiple times
    due to multiple phone numbers or addresses from the LEFT JOINs).

    Keep the first occurrence of each service_id.
    """
    seen = set()
    unique = []
    for row in rows:
        sid = row.get("service_id")
        if sid and sid not in seen:
            seen.add(sid)
            unique.append(row)
    return unique
