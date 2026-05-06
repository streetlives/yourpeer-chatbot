-- ============================================================================
-- QUERY 5 — DIAGNOSTIC: Cohort & Pinned-Provider Coverage
-- ============================================================================
-- Run BEFORE 04_extract_fixture_hybrid.sql to confirm the source DB has
-- the rows the new strategies expect to find. If a cohort or pinned
-- provider returns 0 in production, the hybrid extract won't include it
-- in the fixture either — and any eval scenario that depends on it
-- will need a different remedy.
--
-- This query produces three result sets. Most DB UIs will show them as
-- separate result tabs.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- (1) Cohort coverage matrix — by service_type × borough × cohort
-- ----------------------------------------------------------------------------
-- Output: one row per (cohort, service_type, borough), with the count
-- of services in production matching that triple. A cell of 0 means
-- the cohort_top_n CTE will produce no row for that triple.
WITH classified AS (
    -- IDENTICAL classification logic to _q3_clean.sql / 04_extract_fixture_hybrid.sql.
    -- Reproduced here so this diagnostic can be run standalone.
    SELECT
        s.id AS service_id,
        l.id AS location_id,
        (SELECT ARRAY_AGG(DISTINCT t_own.name ORDER BY t_own.name)
           FROM service_taxonomy st_own
                JOIN taxonomies t_own ON st_own.taxonomy_id = t_own.id
          WHERE st_own.service_id = s.id
            AND t_own.name NOT IN ('Other service')) AS service_taxonomies,
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
        END AS borough,
        s.name AS service_name,
        o.name AS organization_name,
        l.name AS location_name,
        s.description AS service_description,
        (SELECT ph.number FROM phones ph
          WHERE ph.location_id = l.id OR ph.service_id = s.id OR ph.organization_id = o.id
          LIMIT 1) AS phone
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
    SELECT * FROM classified
    WHERE bot_service_type IS NOT NULL
      AND borough IS NOT NULL
      AND phone IS NOT NULL
)
-- (1) Cohort coverage matrix
SELECT
    cohort_name,
    bot_service_type,
    borough,
    COUNT(*) AS available_count,
    CASE WHEN COUNT(*) = 0 THEN 'GAP' ELSE 'OK' END AS status
FROM filtered f
    CROSS JOIN LATERAL (
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
GROUP BY cohort_name, bot_service_type, borough
ORDER BY cohort_name, bot_service_type, borough;

-- ----------------------------------------------------------------------------
-- (2) Pinned-provider availability check
-- ----------------------------------------------------------------------------
-- Run this as a SEPARATE query in a new tab. It uses the same filtered
-- CTE as above but exists standalone for DB UIs that struggle with
-- multiple result sets in one execution.
--
-- Output: one row per pin pattern, with the count of matching rows.
-- A pattern with count=0 means the pinned_named strategy will produce
-- nothing for that pattern — the named provider is NOT in the source
-- DB and the eval scenario depending on it needs a different remedy
-- (e.g. add the provider to Streetlives data, or accept the eval gap).
--
-- WITH classified AS (...same as above...),
-- filtered AS (...same as above...)
-- SELECT
--     pin_pattern,
--     pin_match_count
-- FROM (
--     SELECT 'ali_forney' AS pin_pattern,
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%ali forney%'
--                 OR LOWER(location_name) LIKE '%ali forney%') AS pin_match_count
--     UNION ALL SELECT 'covenant_house',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%covenant house%')
--     UNION ALL SELECT 'safe_horizon_streetwork',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%safe horizon%'
--                AND (LOWER(location_name) LIKE '%streetwork%'
--                  OR LOWER(service_name) LIKE '%streetwork%'))
--     UNION ALL SELECT 'family_justice_center',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%family justice center%')
--     UNION ALL SELECT 'riseboro',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%riseboro%')
--     UNION ALL SELECT 'dhs_path',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%dhs%'
--                AND (LOWER(location_name) LIKE '%path%'
--                  OR LOWER(service_name) LIKE '%path%'
--                  OR LOWER(service_description) LIKE '%prevention assistance%'))
--     UNION ALL SELECT 'msbi_addiction',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%mount sinai beth israel%'
--                AND (LOWER(location_name) LIKE '%addiction%'
--                  OR LOWER(service_name) LIKE '%addiction%'
--                  OR LOWER(service_description) LIKE '%addiction institute%'))
--     UNION ALL SELECT 'realization_center',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%realization center%')
--     UNION ALL SELECT 'project_renewal_3rd_st',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%project renewal%'
--                AND (LOWER(location_name) LIKE '%3rd street%'
--                  OR LOWER(location_name) LIKE '%third street%'
--                  OR LOWER(service_name) LIKE '%820%rehab%'))
--     UNION ALL SELECT 'make_the_road',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%make the road%')
--     UNION ALL SELECT 'cabrini_immigrant',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%cabrini%'
--                AND LOWER(organization_name) LIKE '%immigrant%')
--     UNION ALL SELECT 'unlocal',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%unlocal%')
--     UNION ALL SELECT 'catholic_worker',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%catholic worker%')
--     UNION ALL SELECT 'dycd_youth_drop_in',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%dycd%'
--                AND (LOWER(service_name) LIKE '%drop-in%'
--                  OR LOWER(service_name) LIKE '%drop in%'
--                  OR LOWER(location_name) LIKE '%drop-in%'
--                  OR LOWER(location_name) LIKE '%drop in%'))
--     UNION ALL SELECT 'doobneek',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(organization_name) LIKE '%doobneek%')
--     UNION ALL SELECT 'idnyc',
--            (SELECT COUNT(*) FROM filtered
--              WHERE LOWER(service_name) LIKE '%idnyc%'
--                 OR LOWER(location_name) LIKE '%idnyc%'
--                 OR LOWER(service_description) LIKE '%idnyc%'
--                 OR LOWER(service_name) LIKE '%free id%'
--                 OR LOWER(service_description) LIKE '%municipal id%')
-- ) provider_counts
-- ORDER BY pin_pattern;
