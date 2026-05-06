-- Q3 - fixture extraction. Conservative ASCII-only rewrite.
-- Run in DB UI, then Export Data -> JSON. Save as services_raw.json.
WITH classified AS (
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
                      'shelter',
                      'transitional independent living (til)',
                      'supportive housing',
                      'housing lottery',
                      'veterans short-term housing',
                      'warming center',
                      'safe haven',
                      'youth',
                      'families',
                      'single adult',
                      'senior',
                      'lgbtq young adult',
                      'veterans',
                      'crisis',
                      'drop-in center',
                      'referral',
                      'assessment',
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
    SELECT * FROM classified
    WHERE bot_service_type IS NOT NULL
      AND borough IS NOT NULL
      AND phone IS NOT NULL
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY bot_service_type, borough
            ORDER BY last_validated_at DESC NULLS LAST, service_id
        ) AS rn
    FROM filtered
)
SELECT
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
FROM ranked
WHERE rn <= 5
ORDER BY bot_service_type, borough, rn;
