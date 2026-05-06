-- ============================================================================
-- QUERY 4 — HYBRID FIXTURE EXTRACTION (bucket + pinned + cohort)
-- ============================================================================
--
-- ⚠ DBEAVER USERS: this SQL contains Postgres cast operators (`::geometry`,
-- `::jsonb`). DBeaver's default SQL editor treats `:identifier` as a
-- named-parameter binding and will misparse `::jsonb` as the start of a
-- parameter, producing a syntax error like 'near "combined"' at a
-- position that doesn't match the file.
--
-- FIX: disable variable substitution for this query. Two options:
--
--   1. Per-editor: right-click in the SQL editor → Preferences →
--      uncheck "Variable substitution" → re-execute.
--   2. Globally: Window → Preferences → Editors → SQL Editor → SQL
--      Processing → uncheck "Use anonymous parameters in SQL queries"
--      and "Use named parameters for SQL queries".
--
-- Alternatively, run `04_extract_fixture_hybrid_cast.sql` (sibling
-- file) which uses CAST(expr AS type) syntax — DBeaver-immune.
--
-- ============================================================================
-- Successor to _q3_clean.sql / 03_extract_fixture.sql. Same column projection,
-- same FROM/JOIN, same classification CASE, same `filtered` predicate. The
-- difference is HOW rows are selected from `filtered`:
--
--   _q3_clean.sql: ROW_NUMBER() OVER (PARTITION BY service_type, borough)
--                  → take first 5 per bucket = 218 rows total.
--                  Result: severe coverage gaps for Families/LGBTQ/Youth
--                  cohorts (the May 5 R41 audit found 0 Families-tagged
--                  shelter rows, 0 LGBTQ young adult rows, 0 Youth rows
--                  across all 5 boroughs).
--
--   THIS QUERY:    Three selection strategies UNION'd, deduped by
--                  (service_id, location_id):
--
--                  (A) bucket_top_n  — same as _q3_clean: 5 per
--                      (service_type × borough). Preserves breadth.
--
--                  (B) pinned_named  — explicit selection of
--                      well-known service providers from the Cornell
--                      sample-queries doc (Ali Forney Center, Covenant
--                      House, DHS PATH, Mount Sinai Beth Israel
--                      Addiction Institute, Make the Road, etc.).
--                      Match by case-insensitive substring against
--                      organization_name / location_name / service_name.
--                      Caps at 5 per provider so a provider with many
--                      sites doesn't flood the fixture.
--
--                  (C) cohort_top_n  — for each (service_type × borough
--                      × population_cohort) triple, take the top 1
--                      row. Cohorts: Families, Youth, LGBTQ young adult,
--                      Drop-in Center, Substance Use Treatment. This
--                      ensures the eligibility filter (cluster 1 May 5
--                      patch) has data to filter on.
--
-- Expected row count: 350-450 (vs. 218). Larger fixture, slower mock
-- bootstrap by ~2-3ms, much better population coverage.
--
-- Workflow:
--   1. Run in DB UI (DBeaver, DataGrip, TablePlus, etc.).
--   2. Verify diagnostics (run after this script — see notes below).
--   3. Export results as JSON.
--   4. Save as: tests/eval/fixtures/services_raw.json
--   5. Run the existing post-processing script (_to_services_json.py
--      or equivalent) to write tests/eval/fixtures/services.json.
--
-- ============================================================================

WITH classified AS (
    -- IDENTICAL to _q3_clean.sql lines 3-148. Reproduced inline because
    -- we need the same projection. If _q3_clean.sql is modified in the
    -- future, mirror those changes here.
    SELECT
        s.id AS service_id,
        s.name AS service_name,
        o.name AS organization_name,
        s.description AS service_description,
        l.id AS location_id,
        l.name AS location_name,
        l.slug AS location_slug,
        l.last_validated_at AS last_validated_at,
        pa.address_1 AS address,
        pa.city AS city,
        pa.state_province AS state,
        pa.postal_code AS zip_code,
        pa.country AS country,
        ST_Y(l.position::geometry) AS latitude,
        ST_X(l.position::geometry) AS longitude,
        (SELECT ph.number FROM phones ph
          WHERE ph.location_id = l.id OR ph.service_id = s.id OR ph.organization_id = o.id
          ORDER BY CASE WHEN ph.location_id = l.id THEN 1
                        WHEN ph.service_id = s.id THEN 2 ELSE 3 END
          LIMIT 1) AS phone,
        s.email AS service_email,
        s.url AS service_url,
        o.url AS organization_url,
        s.fees AS fees,
        (SELECT ARRAY_AGG(DISTINCT t_own.name ORDER BY t_own.name)
           FROM service_taxonomy st_own
                JOIN taxonomies t_own ON st_own.taxonomy_id = t_own.id
          WHERE st_own.service_id = s.id
            AND t_own.name NOT IN ('Other service')) AS service_taxonomies,
        (SELECT ARRAY_AGG(DISTINCT t_co.name ORDER BY t_co.name)
           FROM service_at_locations sal_co
                JOIN services s_co ON sal_co.service_id = s_co.id
                JOIN service_taxonomy st_co ON s_co.id = st_co.service_id
                JOIN taxonomies t_co ON st_co.taxonomy_id = t_co.id
          WHERE sal_co.location_id = l.id
            AND s_co.id != s.id
            AND t_co.name NOT IN ('Other service')) AS also_available,
        (SELECT afd.accessibility
           FROM accessibility_for_disabilities afd
          WHERE afd.location_id = l.id
          LIMIT 1) AS accessibility_info,
        (SELECT ARRAY_AGG(DISTINCT lang.language ORDER BY lang.language)
           FROM languages lang
                JOIN service_languages sl ON lang.id = sl.language_id
          WHERE sl.service_id = s.id) AS languages_spoken,
        (SELECT (e.eligible_values = '["true"]'::jsonb
              OR e.eligible_values = '[true]'::jsonb)
           FROM eligibility e
                JOIN eligibility_parameters ep ON e.parameter_id = ep.id
          WHERE e.service_id = s.id
            AND ep.name = 'membership'
          LIMIT 1) AS requires_membership,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN (
                      'shelter', 'transitional independent living (til)',
                      'supportive housing', 'housing lottery',
                      'veterans short-term housing', 'warming center',
                      'safe haven', 'youth', 'families', 'single adult',
                      'senior', 'lgbtq young adult', 'veterans', 'crisis',
                      'drop-in center', 'referral', 'assessment',
                      'residential recovery'
                  )
            ) THEN 'shelter'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('health', 'general health', 'substance use treatment', 'support groups')
            ) THEN 'medical'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('mental health', 'substance use treatment', 'residential recovery', 'support groups')
            ) THEN 'mental_health'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN (
                      'food', 'food pantry', 'food benefits', 'mobile pantry',
                      'mobile food truck', 'mobile market', 'food delivery / meals on wheels',
                      'soup kitchen', 'mobile soup kitchen', 'brown bag', 'farmer''s markets'
                  )
            ) THEN 'food'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('clothing', 'clothing pantry', 'interview-ready clothing',
                      'professional clothing', 'coat drive', 'thrift shop')
            ) THEN 'clothing'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('personal care', 'shower', 'laundry', 'toiletries',
                      'hygiene', 'haircut', 'restrooms')
            ) THEN 'personal_care'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('legal services', 'immigration services')
            ) THEN 'legal'
            WHEN EXISTS (
                SELECT 1 FROM service_taxonomy st JOIN taxonomies t ON st.taxonomy_id = t.id
                WHERE st.service_id = s.id
                  AND LOWER(t.name) IN ('employment', 'internship')
            ) THEN 'employment'
            ELSE 'other'
        END AS bot_service_type,
        CASE LOWER(COALESCE(pa.city, ''))
            WHEN 'new york' THEN 'Manhattan'
            WHEN 'manhattan' THEN 'Manhattan'
            WHEN 'brooklyn' THEN 'Brooklyn'
            WHEN 'queens' THEN 'Queens'
            WHEN 'long island city' THEN 'Queens'
            WHEN 'astoria' THEN 'Queens'
            WHEN 'flushing' THEN 'Queens'
            WHEN 'jamaica' THEN 'Queens'
            WHEN 'bronx' THEN 'Bronx'
            WHEN 'staten island' THEN 'Staten Island'
            ELSE NULL
        END AS borough
    FROM services s
        JOIN service_at_locations sal ON s.id = sal.service_id
        JOIN locations l ON sal.location_id = l.id
        LEFT JOIN organizations o ON s.organization_id = o.id
        LEFT JOIN physical_addresses pa ON l.id = pa.location_id
    WHERE pa.country = 'US'
      AND pa.state_province = 'NY'
      AND s.name IS NOT NULL
      AND o.name IS NOT NULL
      AND pa.address_1 IS NOT NULL
      AND l.position IS NOT NULL
),
filtered AS (
    -- Same predicate as _q3_clean.sql. The three downstream selectors
    -- (bucket_top_n, pinned_named, cohort_top_n) all draw from this set,
    -- so each row is reachable by at most ONE strategy until we
    -- UNION-dedupe at the end.
    SELECT * FROM classified
    WHERE bot_service_type IS NOT NULL
      AND borough IS NOT NULL
      AND phone IS NOT NULL
),

-- ----------------------------------------------------------------------------
-- (A) BUCKET STRATEGY — preserves _q3_clean.sql's breadth-first behavior
-- ----------------------------------------------------------------------------
bucket_top_n AS (
    -- Project the 27 fixture columns + source_strategy. Drop the
    -- internal `rn` so the UNION below has matching column lists.
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        'bucket' AS source_strategy
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY bot_service_type, borough
                ORDER BY last_validated_at DESC NULLS LAST, service_id
            ) AS rn
        FROM filtered
    ) sub
    WHERE rn <= 5
),

-- ----------------------------------------------------------------------------
-- (B) PINNED STRATEGY — well-known service providers
-- ----------------------------------------------------------------------------
-- Match by case-insensitive substring on organization_name / location_name /
-- service_name. The set of pin patterns is curated from:
--
--   1. Cornell sample-queries doc (Ali Forney Center, Covenant House, DHS
--      PATH, Family Justice Center, Make the Road, Cabrini Immigrant
--      Services, UnLocal, Catholic Worker, Mount Sinai Beth Israel,
--      Realization Center, Project Renewal 3rd Street, RiseBoro,
--      DYCD Youth Drop-in, doobneek, Safe Horizon Streetwork).
--
--   2. R41 critical-failure scenarios (peer_free_id_manhattan needs
--      IDNYC; peer_lgbtq_youth_shelter_soho needs Ali Forney Center).
--
-- Cap per provider at 5 sites so a chain provider with many locations
-- doesn't dominate the fixture. Filter to filtered (post-classification).
pinned_named AS (
    -- Project the 27 fixture columns + source_strategy. Drop pin_rn
    -- and pin_key (internal scaffolding) so the UNION below has
    -- matching column lists.
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        'pinned' AS source_strategy
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY pin_key
                ORDER BY last_validated_at DESC NULLS LAST, service_id
            ) AS pin_rn
        FROM (
            SELECT
                f.*,
                CASE
                    -- LGBTQ youth (cluster 3, peer_lgbtq_youth_shelter_soho)
                    WHEN LOWER(organization_name) LIKE '%ali forney%'
                      OR LOWER(location_name) LIKE '%ali forney%'
                        THEN 'ali_forney'
                    WHEN LOWER(organization_name) LIKE '%sylvia%place%'
                      OR LOWER(location_name) LIKE '%sylvia%place%'
                        THEN 'sylvias_place'
                    WHEN LOWER(organization_name) LIKE '%trinity place%'
                      OR LOWER(location_name) LIKE '%trinity place%'
                        THEN 'trinity_place_shelter'

                    -- Youth / DV / family shelter (Cornell expectations)
                    WHEN LOWER(organization_name) LIKE '%covenant house%'
                        THEN 'covenant_house'
                    WHEN LOWER(organization_name) LIKE '%safe horizon%'
                      AND (
                          LOWER(organization_name) LIKE '%streetwork%'
                          OR LOWER(location_name) LIKE '%streetwork%'
                          OR LOWER(service_name) LIKE '%streetwork%'
                      )
                        THEN 'safe_horizon_streetwork'
                    WHEN LOWER(organization_name) LIKE '%family justice center%'
                        THEN 'family_justice_center'
                    WHEN LOWER(organization_name) LIKE '%riseboro%'
                        THEN 'riseboro'
                    -- DHS Family Intake — production data has both AFIC
                    -- (Adult Family Intake Center) and PATH (Prevention
                    -- Assistance and Temporary Housing). Both are DHS
                    -- family intake centers. The May 5 refresh ingested
                    -- AFIC; PATH may be added later. Match either.
                    WHEN (
                        LOWER(organization_name) LIKE '%dhs%'
                        OR LOWER(organization_name) LIKE '%department of homeless%'
                    ) AND (
                        LOWER(location_name) LIKE '%path%'
                        OR LOWER(service_name) LIKE '%path%'
                        OR LOWER(location_name) LIKE '%afic%'
                        OR LOWER(service_name) LIKE '%afic%'
                        OR LOWER(location_name) LIKE '%intake%'
                        OR LOWER(service_name) LIKE '%intake%'
                        OR LOWER(service_description) LIKE '%prevention assistance%'
                    )
                        THEN 'dhs_family_intake'
                    WHEN LOWER(organization_name) LIKE '%bowery mission%'
                        THEN 'bowery_mission'

                    -- Substance use treatment (peer_detox_manhattan)
                    WHEN LOWER(organization_name) LIKE '%mount sinai beth israel%'
                      AND (
                          LOWER(location_name) LIKE '%addiction%'
                          OR LOWER(service_name) LIKE '%addiction%'
                          OR LOWER(service_description) LIKE '%addiction institute%'
                      )
                        THEN 'msbi_addiction'
                    WHEN LOWER(organization_name) LIKE '%realization center%'
                        THEN 'realization_center'
                    WHEN LOWER(organization_name) LIKE '%project renewal%'
                      AND (
                          LOWER(location_name) LIKE '%3rd street%'
                          OR LOWER(location_name) LIKE '%third street%'
                          OR LOWER(service_name) LIKE '%820%rehab%'
                          OR LOWER(service_description) LIKE '%820%rehab%'
                      )
                        THEN 'project_renewal_3rd_st'

                    -- Immigration (Cornell expectation)
                    WHEN LOWER(organization_name) LIKE '%make the road%'
                        THEN 'make_the_road'
                    WHEN LOWER(organization_name) LIKE '%cabrini%'
                      AND LOWER(organization_name) LIKE '%immigrant%'
                        THEN 'cabrini_immigrant'
                    WHEN LOWER(organization_name) LIKE '%unlocal%'
                        THEN 'unlocal'

                    -- Clothing (peer_transman_clothing — Cornell)
                    WHEN LOWER(organization_name) LIKE '%catholic worker%'
                        THEN 'catholic_worker'

                    -- Youth financial / drop-in (peer_bad_with_money)
                    WHEN LOWER(organization_name) LIKE '%dycd%'
                      AND (
                          LOWER(service_name) LIKE '%drop-in%'
                          OR LOWER(service_name) LIKE '%drop in%'
                          OR LOWER(location_name) LIKE '%drop-in%'
                          OR LOWER(location_name) LIKE '%drop in%'
                      )
                        THEN 'dycd_youth_drop_in'
                    WHEN LOWER(organization_name) LIKE '%doobneek%'
                        THEN 'doobneek'

                    -- Free ID (peer_free_id_manhattan from R40)
                    WHEN LOWER(service_name) LIKE '%idnyc%'
                      OR LOWER(location_name) LIKE '%idnyc%'
                      OR LOWER(service_description) LIKE '%idnyc%'
                      OR LOWER(service_name) LIKE '%free id%'
                      OR LOWER(service_description) LIKE '%municipal id%'
                        THEN 'idnyc'

                    ELSE NULL
                END AS pin_key
            FROM filtered f
        ) keyed
        WHERE pin_key IS NOT NULL
    ) ranked_pins
    WHERE pin_rn <= 5
),

-- ----------------------------------------------------------------------------
-- (C) COHORT STRATEGY — at least 1 row per (service_type × borough × cohort)
-- ----------------------------------------------------------------------------
-- Critical for the cluster 1 eligibility filter. Without cohort coverage,
-- the May 5 audit found:
--   - 0 Families-tagged shelter rows (all 5 boroughs)
--   - 0 LGBTQ young adult rows
--   - 0 Youth-tagged rows
--   - 1 Drop-in Center row total (only Manhattan)
--
-- Each cohort is a tag in the row's service_taxonomies (ARRAY_AGG'd in
-- the classified CTE). We pivot per cohort, take 1 row per
-- (bot_service_type × borough × cohort), so 5 boroughs × 5 cohorts ×
-- ~3 service_types = up to 75 cohort rows. Most won't exist in the DB
-- and that's fine.
cohort_top_n AS (
    -- Project the 27 fixture columns + source_strategy. Drop the
    -- internal cohort_name and cohort_rn (a single row may qualify
    -- for multiple cohorts; the UNION-dedupe at the end keeps
    -- (service_id, location_id) unique).
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        'cohort' AS source_strategy
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY bot_service_type, borough, cohort_name
                ORDER BY last_validated_at DESC NULLS LAST, service_id
            ) AS cohort_rn
        FROM (
            SELECT
                f.*,
                cohort_name
            FROM filtered f
                CROSS JOIN LATERAL (
                    -- For each row, emit one synthetic row per cohort
                    -- the row qualifies for. A row tagged 'Families'
                    -- AND 'Youth' will appear twice in this CTE under
                    -- different cohort_name values — fine, dedup
                    -- happens in the final UNION.
                    SELECT cohort_name FROM (
                        VALUES
                          ('families'),
                          ('youth'),
                          ('lgbtq_young_adult'),
                          ('drop_in_center'),
                          ('substance_use_treatment')
                    ) cohorts(cohort_name)
                    WHERE
                        (cohort_name = 'families' AND EXISTS (
                            SELECT 1 FROM unnest(f.service_taxonomies) tx
                             WHERE LOWER(tx) = 'families'
                        ))
                        OR (cohort_name = 'youth' AND EXISTS (
                            SELECT 1 FROM unnest(f.service_taxonomies) tx
                             WHERE LOWER(tx) = 'youth'
                        ))
                        OR (cohort_name = 'lgbtq_young_adult' AND EXISTS (
                            SELECT 1 FROM unnest(f.service_taxonomies) tx
                             WHERE LOWER(tx) = 'lgbtq young adult'
                        ))
                        OR (cohort_name = 'drop_in_center' AND EXISTS (
                            SELECT 1 FROM unnest(f.service_taxonomies) tx
                             WHERE LOWER(tx) = 'drop-in center'
                        ))
                        OR (cohort_name = 'substance_use_treatment' AND EXISTS (
                            SELECT 1 FROM unnest(f.service_taxonomies) tx
                             WHERE LOWER(tx) = 'substance use treatment'
                        ))
                ) cs
        ) cohort_keyed
    ) ranked_cohorts
    WHERE cohort_rn <= 1
),

-- ----------------------------------------------------------------------------
-- UNION-DEDUPE — combine the three strategies, dedupe by (service_id, location_id)
-- ----------------------------------------------------------------------------
-- Each strategy emits the same 27 columns plus a `source_strategy` tag.
-- The internal rn/pin_rn/cohort_rn/cohort_name columns are dropped at
-- the boundary between each strategy CTE and the combined CTE.
combined AS (
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        source_strategy
    FROM bucket_top_n
    UNION ALL
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        source_strategy
    FROM pinned_named
    UNION ALL
    SELECT
        service_id, service_name, organization_name, service_description,
        location_id, location_name, location_slug, last_validated_at,
        address, city, state, zip_code, country,
        latitude, longitude, phone,
        service_email, service_url, organization_url, fees,
        service_taxonomies, also_available, accessibility_info,
        languages_spoken, requires_membership,
        bot_service_type, borough,
        source_strategy
    FROM cohort_top_n
)
-- ----------------------------------------------------------------------------
-- FINAL OUTPUT — flat columns, one row per (service_id, location_id)
-- ----------------------------------------------------------------------------
SELECT DISTINCT ON (service_id, location_id)
    service_id,
    service_name,
    organization_name,
    service_description,
    location_id,
    location_name,
    location_slug,
    last_validated_at,
    address,
    city,
    state,
    zip_code,
    country,
    latitude,
    longitude,
    phone,
    service_email,
    service_url,
    organization_url,
    fees,
    service_taxonomies,
    also_available,
    accessibility_info,
    languages_spoken,
    requires_membership,
    bot_service_type,
    borough
    -- source_strategy intentionally dropped from final output. The
    -- fixture JSON should match the existing schema so the eval mock
    -- doesn't need to be updated. If you want to debug which strategy
    -- emitted a row, run the diagnostic version (sibling file).
FROM combined
ORDER BY
    service_id, location_id,
    -- Strategy preference for tie-break: bucket > pinned > cohort.
    -- A row reachable by all three should be tagged as bucket (the
    -- "natural" fixture row) for cleanest provenance.
    CASE source_strategy
        WHEN 'bucket' THEN 1
        WHEN 'pinned' THEN 2
        WHEN 'cohort' THEN 3
    END;
