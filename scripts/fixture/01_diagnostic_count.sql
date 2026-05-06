-- Q1 - diagnostic count. Conservative rewrite (ASCII only, no fancy chars).
WITH classified AS (
    SELECT
        s.id AS service_id,
        l.last_validated_at AS last_validated_at,
        (SELECT ph.number FROM phones ph
          WHERE ph.location_id = l.id OR ph.service_id = s.id OR ph.organization_id = o.id
          LIMIT 1) AS phone,
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
    bot_service_type,
    borough,
    COUNT(*) AS row_count
FROM ranked
WHERE rn <= 5
GROUP BY bot_service_type, borough
ORDER BY bot_service_type, borough;
