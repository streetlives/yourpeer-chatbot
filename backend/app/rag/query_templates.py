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

from sqlalchemy import text


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
    s.additional_info AS additional_info,

    o.name            AS organization_name,
    o.url             AS organization_url,

    l.id              AS location_id,
    l.name            AS location_name,
    l.slug            AS location_slug,

    pa.address_1      AS address,
    pa.city           AS city,
    pa.state_province AS state,
    pa.postal_code    AS zip_code,

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
    ) AS accessibility_info

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
    LEFT JOIN holiday_schedules today_sched
        ON today_sched.service_id = s.id
        AND today_sched.weekday = EXTRACT(ISODOW FROM CURRENT_DATE)::int
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
FILTER_BY_TAXONOMY_NAME = (
    """EXISTS (
        SELECT 1 FROM service_taxonomy st
        JOIN taxonomies t ON st.taxonomy_id = t.id
        WHERE st.service_id = s.id AND LOWER(t.name) = LOWER(:taxonomy_name)
    )""",
    ["taxonomy_name"],
)

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

# Borough filter — uses the physical_addresses.borough column directly.
# This is the most reliable borough filter: the borough column is clean,
# consistently populated, and avoids the city-field casing chaos
# (e.g. "BRONX" vs "Bronx" vs "The Bronx" all in the same borough).
# Case-insensitive match handles any remaining inconsistencies.
FILTER_BY_BOROUGH = (
    "LOWER(pa.borough) = LOWER(:borough)",
    ["borough"],
)

FILTER_BY_CITY = (
    "LOWER(pa.city) = LOWER(:city)",
    ["city"],
)

# Borough-level city match — matches any city value that belongs to the borough.
# When a user says "Queens", this matches "Queens", "Astoria", "Flushing",
# "Jamaica", "Long Island City", etc.
# The SQL uses ANY() with an array parameter, which SQLAlchemy handles natively.
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
# service descriptions using PostgreSQL regex. Used by housing_assistance
# template (Phase 2) and will be reused by Phase 4 sub-category narrowing.
# The pattern is a PostgreSQL ~* regex (case-insensitive).
FILTER_BY_DESCRIPTION_KEYWORDS = (
    "s.description ~* :description_pattern",
    ["description_pattern"],
)

# ---------------------------------------------------------------------------
# ORDER + LIMIT
# ---------------------------------------------------------------------------
# Sorting priority:
#   1. Open now — services open right now appear first (when schedule exists)
#   2. Recently verified — freshest data first (NULLS LAST)
#   3. Service name — stable tiebreaker
#
# When proximity (lat/lon) is available, distance is the primary sort and
# open-now becomes secondary.

# Open-now sort expression: returns 0 for currently open, 1 for closed/unknown.
# Uses the today_opens/today_closes already selected by the lateral join.
_OPEN_NOW_RANK = """CASE
    WHEN today_sched.opens_at IS NOT NULL
         AND today_sched.closes_at IS NOT NULL
         AND today_sched.opens_at <= CURRENT_TIME
         AND today_sched.closes_at >= CURRENT_TIME
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
_DISTANCE_RANK = (
    "ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)"
)

# Base sort tiebreakers: open-now first, then freshness, then name.
_BASE_ORDER_PARTS = [
    _OPEN_NOW_RANK,
    "l.last_validated_at DESC NULLS LAST",
    "s.name",
]

_DEFAULT_MAX_RESULTS = 10


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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
            # FILTER_BY_OPEN_NOW is defined but the chatbot does not currently pass
            # weekday/current_time params. DB audit (Apr 2026) shows schedule data
            # is only populated for walk-in services: Soup Kitchen (81%), Shower (55%),
            # Clothing Pantry (64%), Food Pantry (40%). Enabling this filter would
            # silently exclude the majority of services with no schedule rows.
            # Re-enable only if schedule coverage improves substantially.
            FILTER_BY_OPEN_NOW,
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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
        ],
        "default_params": {
            "taxonomy_names": [
                "shelter",
                "transitional independent living (til)",
                "supportive housing",
                "housing lottery",
                "veterans short-term housing",
                "warming center",
                "safe haven",
            ]
        },
        "taxonomy_aliases": [
            "Shelter", "Transitional Independent Living (TIL)", "Supportive Housing",
            "Housing Lottery", "Veterans Short-Term Housing", "Warming Center", "Safe Haven",
        ],
    },
    "clothing": {
        "name": "ClothingQuery",
        "description": "Find clothing distribution services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
            FILTER_BY_GENDER_ELIGIBILITY,
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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
        ],
        "default_params": {
            "taxonomy_names": [
                "health",
                "general health",
                "crisis",
            ]
        },
        "taxonomy_aliases": ["Health", "General Health", "Crisis"],
    },
    "legal": {
        "name": "LegalQuery",
        "description": "Find legal aid and immigration services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_GENDER_ELIGIBILITY,
            FILTER_BY_WEEKDAY,
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
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_AGE_ELIGIBILITY,
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
    "housing_assistance": {
        "name": "HousingAssistanceQuery",
        "description": "Find rental assistance, eviction prevention, and housing programs (not emergency shelter)",
        "required_filters": [
            FILTER_BY_TAXONOMY_NAME_IN,
            FILTER_NOT_HIDDEN,
            FILTER_BY_STATE_NY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "optional_filters": [
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
        ],
        "default_params": {
            "taxonomy_names": [
                "other service",
                "benefits",
                "case workers",
                "referral",
                "housing lottery",
            ],
            # Description-level filter narrows results to housing programs.
            # Without this, the broad taxonomy list would return all 940+
            # "Other service" entries. The pattern matches rental assistance,
            # eviction prevention, Section 8, NYCHA, affordable housing, etc.
            "description_pattern": (
                "rental|rent assist|rent arrear|rent program"
                "|eviction prev|eviction defense|housing court"
                "|housing assist|housing program|housing support"
                "|housing applic|housing referral|housing voucher"
                "|section 8|voucher|SCRIE|DRIE"
                "|NYCHA|housing connect|affordable hous|subsidiz"
                "|homeless prevention|rapid rehousing|rapid re-housing"
            ),
        },
        "taxonomy_aliases": [
            "Other service", "Benefits", "Case Workers",
            "Referral", "Housing Lottery",
        ],
    },
    "other": {
        "name": "OtherServicesQuery",
        "description": "Find benefits, drop-in centers, case workers, and miscellaneous services",
        "required_filters": [FILTER_BY_TAXONOMY_NAME_IN, FILTER_NOT_HIDDEN, FILTER_BY_STATE_NY],
        "optional_filters": [
            FILTER_BY_BOROUGH,
            FILTER_BY_CITY,
            FILTER_BY_CITY_IN_BOROUGH,
            FILTER_BY_CITY_LIKE,
            FILTER_BY_PROXIMITY,
            FILTER_BY_DESCRIPTION_KEYWORDS,
        ],
        "default_params": {
            "taxonomy_names": [
                "other service",
                "benefits",
                "drop-in center",
                "case workers",
                "referral",
                "education",
                "mail",
                "free wifi",
                "taxes",
                "baby supplies",
                "baby",
                "assessment",
                "community services",
                "activities",
                "appliances",
                "gym",
                "pets",
                "single adult",
                "families",
                "youth",
                "senior",
                "veterans",
                "lgbtq young adult",
                "intake",
            ]
        },
        "taxonomy_aliases": [
            "Other service", "Benefits", "Drop-in Center", "Case Workers",
            "Referral", "Education", "Mail", "Free Wifi", "Taxes",
            "Baby Supplies", "Baby", "Assessment", "Community Services",
            "Activities", "Appliances", "Gym", "Pets", "Single Adult",
            "Families", "Youth", "Senior", "Veterans", "LGBTQ Young Adult", "Intake",
        ],
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
    _UNIVERSAL_OPTIONAL = [FILTER_BY_COLOCATED_TAXONOMY]
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

    # 2. Distance (when proximity search is active)
    if _has_distance:
        order_parts.append(_DISTANCE_RANK)

    # 3. Base tiebreakers: open-now, freshness, name
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

    # Drop borough filter — keep city_list as the broader fallback.
    # This ensures records where pa.borough is NULL can still be found.
    relaxed_params.pop("borough", None)

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

def _normalize_url(url: str | None) -> str | None:
    """Ensure a URL has a protocol prefix so browsers open it as absolute."""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://", "//")):
        return "https://" + url
    return url


def _format_phone(number: str | None, extension: str | None) -> str | None:
    """Format a phone number with optional extension."""
    if not number:
        return None
    ext = (extension or "").strip()
    if ext and ext.lower() not in ("none", "n/a", ""):
        return f"{number} ext. {ext}"
    return number


def format_service_card(row: dict) -> dict:
    """
    Format a raw query result row into a structured service card.

    This is the ONLY place service data is assembled for display.
    No LLM synthesis — just field mapping.
    """
    # Build address string
    address_parts = [
        row.get("address"),
        row.get("city"),
        row.get("state"),
        row.get("zip_code"),
    ]
    full_address = ", ".join(p for p in address_parts if p)

    # Build YourPeer listing URL from location slug
    slug = row.get("location_slug")
    yourpeer_url = f"https://yourpeer.nyc/locations/{slug}" if slug else None

    # Build schedule / open status
    today_opens = row.get("today_opens")
    today_closes = row.get("today_closes")
    schedule_status = _compute_schedule_status(today_opens, today_closes)

    # Co-located services — filter to user-relevant categories
    _DISPLAY_CATEGORIES = {
        "Shelter", "Shower", "Clothing Pantry", "Clothing",
        "Health", "Mental Health", "General Health",
        "Laundry", "Legal Services", "Benefits", "Education",
        "Employment", "Food", "Food Pantry", "Soup Kitchen",
        "Toiletries", "Mail", "Free Wifi", "Haircut",
        "Support Groups", "Drop-in Center", "Crisis",
        "Restrooms", "Warming Center",
    }
    raw_also = row.get("also_available") or []
    also_available = sorted(set(raw_also) & _DISPLAY_CATEGORIES)

    return {
        "service_id": str(row.get("service_id", "")),
        "service_name": row.get("service_name") or "Unknown Service",
        "organization": row.get("organization_name"),
        "description": row.get("service_description"),
        "address": full_address or None,
        "city": row.get("city"),
        "phone": _format_phone(row.get("phone"), row.get("phone_extension")),
        "email": row.get("service_email"),
        "website": _normalize_url(row.get("service_url") or row.get("organization_url")),
        "fees": row.get("fees"),
        "additional_info": row.get("additional_info"),
        "yourpeer_url": yourpeer_url,
        "hours_today": schedule_status["hours_today"],
        "is_open": schedule_status["is_open"],
        "requires_membership": bool(row.get("requires_membership")),
        "last_validated_at": (
            row["last_validated_at"].isoformat()
            if row.get("last_validated_at") and hasattr(row["last_validated_at"], "isoformat")
            else row.get("last_validated_at")  # pass through str or None
        ),
        "also_available": also_available if also_available else None,
        "accessibility": row.get("accessibility_info"),
    }


def _compute_schedule_status(opens_at, closes_at) -> dict:
    """
    Compute human-readable hours and open/closed status.

    Returns:
        {
            "hours_today": str or None,
                e.g. "9:00 AM – 5:00 PM", or None if no data
            "is_open": "open" | "closed" | None
                None means no schedule data available
        }
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
    open_str = _format_time(open_time)
    close_str = _format_time(close_time)
    hours_today = f"{open_str} – {close_str}"

    # Determine if currently open
    now = datetime.now().time()
    if open_time <= close_time:
        is_open = "open" if open_time <= now <= close_time else "closed"
    else:
        # Wraps midnight (e.g. 8 PM – 6 AM)
        is_open = "open" if now >= open_time or now <= close_time else "closed"

    return {"hours_today": hours_today, "is_open": is_open}


def _format_time(t) -> str:
    """Format a time object as '9:00 AM' style (cross-platform)."""
    from datetime import datetime
    # Use %I (zero-padded) then strip the leading zero manually.
    # %-I is macOS-only and crashes on Linux.
    formatted = datetime.combine(datetime.min, t).strftime("%I:%M %p")
    return formatted.lstrip("0") if formatted.startswith("0") else formatted


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
