-- ═══════════════════════════════════════════════════════════════
-- QUERY 1: Description filter pattern validation (79 patterns)
-- Tests each regex from _DETAIL_DESCRIPTION_FILTERS against
-- services.description. Patterns with 0 matches are dead code.
-- Sorted by matching_services ASC so dead patterns appear first.
-- ═══════════════════════════════════════════════════════════════

SELECT p.detail, p.pattern,
       COUNT(DISTINCT s.id) AS matching_services
FROM (VALUES
    ('English classes', 'ESL|ESOL|english class|learn.*english'),
    ('GED programs', 'GED|high school equiv|HSE|diploma|equivalency'),
    ('adult education', 'adult education|adult literacy|continuing education'),
    ('computer classes', 'computer|digital literacy|computer skills|computer training'),
    ('digital literacy', 'computer|digital literacy|computer skills'),
    ('disability services', 'disability|disabled|\mSSI\M|\mSSDI\M|accessible|special needs'),
    ('financial services', 'financial|money management|budget|credit|debt|financial literacy'),
    ('financial literacy', 'financial literacy|financial education|money management|budget'),
    ('budgeting help', 'budget|financial|money management|savings'),
    ('senior services', 'senior|older adult|aging|elder|60\+|65\+|over 60'),
    ('re-entry services', 'reentry|re-entry|parole|probation|incarcerat|released|formerly'),
    ('anger management', 'anger management|violence prevention|conflict resolution'),
    ('parenting classes', 'parenting|parent class|parent support|fatherhood|motherhood'),
    ('baby supplies', 'diaper|baby|infant|stroller|car seat|formula'),
    ('transportation help', 'access.a.ride|metrocard|metro card|transit|transportation'),
    ('insurance enrollment', 'insurance|medicaid enroll|medicare enroll|health insurance'),
    ('health insurance enrollment', 'health insurance|insurance enroll|medicaid|marketplace'),
    ('LGBTQ services', 'LGBTQ|queer|transgender|gay|lesbian|bisexual|nonbinary'),
    ('LGBTQ support', 'LGBTQ|queer|transgender|gay|lesbian|bisexual|nonbinary'),
    ('DACA services', 'DACA|deferred action|dreamer'),
    ('accessibility services', 'accessibility|accessible|wheelchair|disability|ADA'),
    ('Access-A-Ride help', 'access.a.ride|paratransit|disability.*transport'),
    ('EBT / food stamps', 'EBT|food stamp|SNAP|electronic benefit'),
    ('Medicaid enrollment', 'medicaid|health insurance|enroll'),
    ('Social Security', 'social security|\mSSA\M|\mSSI\M|\mSSDI\M|disability benefit'),
    ('benefits enrollment', 'benefit|enroll|eligib|public assist|apply'),
    ('cash assistance', 'cash assist|public assist|TANF|welfare|emergency.*cash'),
    ('financial advisors', 'financial advis|financial counsel|money manage|budget'),
    ('food stamps / SNAP', 'food stamp|SNAP|EBT|electronic benefit'),
    ('money management', 'money manage|budget|financial|savings|debt'),
    ('public assistance', 'public assist|welfare|benefit|TANF|cash assist'),
    ('SYEP programs', 'SYEP|summer youth|summer employment|youth employment'),
    ('dental care', 'dental|dentist|oral health|tooth|teeth'),
    ('vision care', 'vision|eye|optometr|ophthalmol|glasses|optical'),
    ('urgent care', 'urgent care|walk.in clinic|immediate care'),
    ('prenatal care', 'prenatal|maternity|pregnan|obstetric|OB.GYN'),
    ('diabetes / insulin care', 'diabet|insulin|blood sugar|endocrin|A1C'),
    ('HIV services', 'HIV|AIDS|antiretroviral|PrEP|\mPEP\M'),
    ('harm reduction services', 'harm reduction|needle|syringe|naloxone|narcan|overdose'),
    ('HIV testing', 'HIV.*test|HIV.*screen|rapid.*test.*HIV'),
    ('STD testing', 'STD|STI|sexual.*health|sexually transmitted'),
    ('STI testing', 'STI|STD|sexual.*health|sexually transmitted'),
    ('PrEP services', 'PrEP|pre.exposure|HIV prevent|truvada|descovy'),
    ('allergy / EpiPen care', 'allerg|epipen|anaphyla'),
    ('asthma care', 'asthma|inhaler|respiratory|pulmon|breathing'),
    ('diabetes care', 'diabet|insulin|blood sugar|endocrin|A1C'),
    ('dialysis services', 'dialysis|kidney|renal'),
    ('hepatitis services', 'hepatitis|\mhep\M|liver'),
    ('hepatitis C services', 'hepatitis.*C|hep.*C|HCV'),
    ('maternity services', 'matern|pregnan|prenatal|postpartum|obstetric'),
    ('postpartum care', 'postpartum|after.*birth|newborn|maternal'),
    ('needle exchange', 'needle|syringe|harm reduction|safe.*inject'),
    ('syringe exchange', 'syringe|needle|harm reduction|safe.*inject'),
    ('vaccinations', 'vaccin|immuniz|flu.*shot|COVID.*shot|booster'),
    ('immigration services', 'immigra|asylum|refugee|undocument|visa|green card|USCIS|naturali|citizen|deporta|removal|\mICE\M'),
    ('asylum services', 'asylum|refugee|persecution|fear|credible fear|withholding'),
    ('eviction help', 'evict|tenant|landlord|housing court|rental|lease'),
    ('domestic violence services', 'domestic violence|\mDV\M|intimate partner|protective order|abuse|safety plan'),
    ('abuse counseling', 'abuse|domestic violence|\mDV\M|survivor|violence.*counsel'),
    ('citizenship services', 'citizen|naturali|civics|passport|N-400'),
    ('naturalization services', 'naturali|citizen|civics|N-400|oath'),
    ('order of protection', 'order of protection|protective order|restraining order|\mOOP\M'),
    ('AA meetings', '\mAA\M|alcoholics anonymous|12.step|twelve.step|sobriety'),
    ('NA meetings', '\mNA\M|narcotics anonymous|12.step|twelve.step|recovery.*meeting'),
    ('counseling', 'counsel|therap|talk.*someone|mental health.*support'),
    ('therapy', 'therap|counsel|psycho|CBT|DBT|mental health'),
    ('treatment centers', 'treatment center|treatment facility|rehab|recovery center'),
    ('treatment programs', 'treatment program|recovery program|rehab program'),
    ('Housing Connect', 'Housing Connect|housing lottery|affordable.*apply'),
    ('NYCHA housing', 'NYCHA|public housing|housing authority'),
    ('Section 8 vouchers', 'section 8|housing voucher|rental assist|\mHCV\M'),
    ('affordable housing', 'affordable housing|low.income housing|subsidiz|below market'),
    ('eviction prevention', 'eviction prevent|anti.eviction|stay.*home|keep.*housed'),
    ('homeless prevention programs', 'homeless prevent|prevention|diversion'),
    ('housing assistance', 'housing assist|housing help|housing support|find.*housing'),
    ('housing lottery', 'housing lottery|Housing Connect|affordable.*apply'),
    ('housing programs', 'housing program|housing service|housing support'),
    ('housing vouchers', 'housing voucher|section 8|rental voucher|\mHCV\M'),
    ('rental assistance', 'rental assist|rent help|rent subsid|emergency rent|\mERAP\M|one shot')
) AS p(detail, pattern)
LEFT JOIN services s ON s.description ~* p.pattern
GROUP BY p.detail, p.pattern
ORDER BY matching_services ASC, p.detail;


-- ═══════════════════════════════════════════════════════════════
-- QUERY 2: Taxonomy narrowing validation (18 entries)
-- Tests each taxonomy from _DETAIL_TO_TAXONOMY_NARROWING against
-- the service_taxonomy table. Shows service count per taxonomy.
-- Entries with 0 services mean the narrowing route returns empty.
-- ═══════════════════════════════════════════════════════════════

SELECT p.detail, p.taxonomy_name,
       COUNT(DISTINCT st.service_id) AS services_tagged
FROM (VALUES
    ('soup kitchens', 'soup kitchen'),
    ('soup kitchens', 'mobile soup kitchen'),
    ('food pantries', 'food pantry'),
    ('food pantries', 'mobile pantry'),
    ('groceries', 'food pantry'),
    ('groceries', 'mobile pantry'),
    ('groceries', 'mobile market'),
    ('groceries', 'farmer''s markets'),
    ('showers', 'shower'),
    ('laundry', 'laundry'),
    ('haircuts', 'haircut'),
    ('toiletries', 'toiletries'),
    ('restrooms', 'restrooms'),
    ('detox', 'substance use treatment'),
    ('detox', 'residential recovery'),
    ('substance use treatment', 'substance use treatment'),
    ('substance use treatment', 'residential recovery'),
    ('substance abuse services', 'substance use treatment'),
    ('substance abuse services', 'residential recovery'),
    ('addiction services', 'substance use treatment'),
    ('addiction services', 'residential recovery'),
    ('rehab services', 'substance use treatment'),
    ('rehab services', 'residential recovery'),
    ('inpatient treatment', 'substance use treatment'),
    ('inpatient treatment', 'residential recovery'),
    ('outpatient treatment', 'substance use treatment'),
    ('sober living', 'residential recovery'),
    ('sober living', 'supportive housing'),
    ('halfway houses', 'residential recovery'),
    ('halfway houses', 'supportive housing'),
    ('recovery services', 'substance use treatment'),
    ('recovery services', 'residential recovery'),
    ('recovery services', 'support groups')
) AS p(detail, taxonomy_name)
LEFT JOIN taxonomies t ON LOWER(t.name) = LOWER(p.taxonomy_name)
LEFT JOIN service_taxonomy st ON st.taxonomy_id = t.id
GROUP BY p.detail, p.taxonomy_name
ORDER BY services_tagged ASC, p.detail;


-- ═══════════════════════════════════════════════════════════════
-- QUERY 3: Check if taxonomy_specific_attributes table exists
-- YourPeer sends clothingOccasion=Everyday/Job Interview via this
-- parameter, but their own source has a FIXME saying it has no
-- effect. This query checks whether the table exists and has data.
-- ═══════════════════════════════════════════════════════════════

SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND (table_name ILIKE '%taxonomy%specific%'
       OR table_name ILIKE '%attribute%'
       OR table_name ILIKE '%clothing%');


-- ═══════════════════════════════════════════════════════════════
-- QUERY 4: Clothing taxonomy children with service counts
-- Shows what clothing sub-taxonomies exist and how many services
-- are tagged with each. Used to decide whether casual/professional
-- filtering via taxonomy narrowing is viable.
-- ═══════════════════════════════════════════════════════════════

SELECT t.name AS taxonomy_name, t.parent_name,
       COUNT(DISTINCT st.service_id) AS services_tagged
FROM taxonomies t
LEFT JOIN service_taxonomy st ON st.taxonomy_id = t.id
WHERE t.parent_name = 'Clothing'
   OR t.name = 'Clothing'
GROUP BY t.name, t.parent_name
ORDER BY services_tagged DESC;


-- ═══════════════════════════════════════════════════════════════
-- QUERY 5: Index coverage check for the Locations admin endpoints
--
-- The admin endpoints (PR #93) execute aggregations across these
-- tables. The PR description claims sub-5s response times, which
-- assumes the following indexes exist:
--
--   service_at_locations(location_id)   — heatmap, list enrichment,
--                                          M8 enrichment CTE
--   service_at_locations(service_id)   — heatmap, category coverage
--   service_taxonomy(service_id)       — heatmap, category coverage,
--                                          stale-categories
--   service_taxonomy(taxonomy_id)      — same group
--   taxonomies(name)                   — M7 taxonomy validation,
--                                          category filter on /list
--   locations(last_validated_at)       — stat strip, freshness
--                                          histogram, timeseries
--   physical_addresses(location_id)    — list query JOIN
--   phones(location_id)                — list query LATERAL
--   regular_schedules(service_id)      — has_hours EXISTS subquery
--
-- Run this query against staging or prod to confirm each. Any row
-- in the second result set (missing indexes) is a candidate
-- explanation for slow endpoints. Streetlives is a third-party DB,
-- so we can't assume any of these — verify before complaining
-- about latency.
-- ═══════════════════════════════════════════════════════════════

-- Part A: list existing indexes on the tables the admin queries hit.
-- Useful to see what's actually there, including indexes that
-- weren't anticipated but might still help.
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN (
    'service_at_locations',
    'service_taxonomy',
    'taxonomies',
    'locations',
    'physical_addresses',
    'phones',
    'regular_schedules'
  )
ORDER BY tablename, indexname;

-- Part B: list the EXPECTED column-coverages and flag any that are
-- missing. Returns one row per missing index. Empty result = all
-- expected indexes present.
WITH expected AS (
    SELECT 'service_at_locations'::text AS tablename, 'location_id'::text AS col
    UNION ALL SELECT 'service_at_locations', 'service_id'
    UNION ALL SELECT 'service_taxonomy',     'service_id'
    UNION ALL SELECT 'service_taxonomy',     'taxonomy_id'
    UNION ALL SELECT 'taxonomies',           'name'
    UNION ALL SELECT 'locations',            'last_validated_at'
    UNION ALL SELECT 'physical_addresses',   'location_id'
    UNION ALL SELECT 'phones',               'location_id'
    UNION ALL SELECT 'regular_schedules',    'service_id'
),
covered AS (
    SELECT
        t.relname AS tablename,
        a.attname AS col
    FROM pg_index ix
    JOIN pg_class t ON t.oid = ix.indrelid
    JOIN pg_attribute a ON a.attrelid = t.oid
                       AND a.attnum = ANY(ix.indkey)
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
)
SELECT
    e.tablename,
    e.col AS expected_indexed_column,
    'MISSING' AS status
FROM expected e
LEFT JOIN covered c USING (tablename, col)
WHERE c.tablename IS NULL
ORDER BY e.tablename, e.col;

-- Part C: EXPLAIN ANALYZE for the heaviest queries the admin hits.
-- Replace :TODAY with today's date before running. These are the
-- three queries most likely to be slow if an index is missing.
--
-- Caveat for Part C: the heatmap and borough-breakdown queries in
-- production wrap `pa.city` in a SQL CASE generated by
-- `_borough_case_sql()` — a ~80-branch expression that maps case
-- variants ("BROOKLYN"), aliases ("The Bronx"), and NYC neighborhood
-- names ("Astoria", "Flushing", "Jamaica") to their canonical
-- borough before grouping. The simplified `GROUP BY pa.city` below
-- is for *plan timing only* — it returns one row per raw city value
-- in the data (~100 rows), which is useful for index analysis but
-- doesn't reflect the borough labels admins actually see. Running
-- this query and reading the city values directly will reveal
-- data-quality issues (case duplicates, neighborhood-as-city, raw
-- addresses as city) but those are *fixed by the production CASE*,
-- not visible bugs in the admin page.

-- Heatmap aggregation (admin section 3b) — production version,
-- bucketed via the comprehensive CASE. This is what the deployed
-- query actually does; if you want plan timing only, the simpler
-- raw-city variant below is fine too.
-- EXPLAIN ANALYZE
-- SELECT t.name AS category,
--        CASE
--            WHEN LOWER(TRIM(pa.city)) IN
--                 ('manhattan','new york','brooklyn','queens','bronx',
--                  'the bronx','staten island','astoria','flushing',
--                  'jamaica','long island city',
--                  -- ...continues for ~80 entries
--                  ...)
--            THEN <mapped_borough>
--            ELSE 'Other'
--        END AS borough,
--        COUNT(DISTINCT l.id) AS location_count
-- FROM taxonomies t
-- JOIN service_taxonomy st ON st.taxonomy_id = t.id
-- JOIN service_at_locations sal ON sal.service_id = st.service_id
-- JOIN locations l ON l.id = sal.location_id
-- LEFT JOIN physical_addresses pa ON pa.location_id = l.id
-- GROUP BY t.name, borough;

-- Heatmap aggregation — simplified (raw city) for plan timing only.
-- See caveat above. Useful for spotting data-quality issues but the
-- borough column will be a raw pa.city string, not a canonical
-- borough label.
-- EXPLAIN ANALYZE
-- SELECT t.name AS category, pa.city AS borough,
--        COUNT(DISTINCT l.id) AS location_count
-- FROM taxonomies t
-- JOIN service_taxonomy st ON st.taxonomy_id = t.id
-- JOIN service_at_locations sal ON sal.service_id = st.service_id
-- JOIN locations l ON l.id = sal.location_id
-- JOIN physical_addresses pa ON pa.location_id = l.id
-- GROUP BY t.name, pa.city;

-- Category coverage (admin section 4a) — per-taxonomy rollup with
-- freshness ratio. This is the PRODUCTION version: no parent_name
-- filter, so all ~39 taxonomies (parents AND children) appear. The
-- deployed page sorts by demand_supply_ratio DESC; the diagnostic
-- below sorts by location_count DESC since EXPLAIN doesn't care
-- about output order.
--
-- Earlier revisions of this file had `WHERE t.parent_name IS NULL`
-- here, which collapsed the result to ~6 top-level categories. That
-- variant is useful for a quick "what are the top-level buckets"
-- summary (where one will find that "Other service" dominates the
-- catalog at ~57% of all tagged locations), but it does NOT reflect
-- the deployed query's behavior. Use the version below for actual
-- plan timing.
-- EXPLAIN ANALYZE
-- SELECT t.name AS taxonomy_name,
--        COUNT(DISTINCT s.id) AS service_count,
--        COUNT(DISTINCT l.id) AS location_count,
--        COUNT(DISTINCT l.id) FILTER (
--            WHERE l.last_validated_at >= NOW() - INTERVAL '90 days'
--        ) AS fresh_location_count
-- FROM taxonomies t
-- JOIN service_taxonomy st ON st.taxonomy_id = t.id
-- JOIN services s          ON s.id = st.service_id
-- JOIN service_at_locations sal ON sal.service_id = s.id
-- JOIN locations l         ON l.id = sal.location_id
-- GROUP BY t.name;

-- Top-level-only summary variant — useful for a quick read of
-- the catalog's coarse shape. Adds the parent_name filter; uses
-- LEFT JOINs so taxonomies with no tagged services appear as
-- 0/0/0 rows rather than silently disappearing.
-- EXPLAIN ANALYZE
-- SELECT t.name AS taxonomy_name,
--        COUNT(DISTINCT s.id) AS service_count,
--        COUNT(DISTINCT sal.location_id) AS location_count,
--        COUNT(DISTINCT sal.location_id) FILTER (
--            WHERE l.last_validated_at >= NOW() - INTERVAL '90 days'
--        ) AS fresh_location_count
-- FROM taxonomies t
-- LEFT JOIN service_taxonomy st ON st.taxonomy_id = t.id
-- LEFT JOIN services s ON s.id = st.service_id
-- LEFT JOIN service_at_locations sal ON sal.service_id = s.id
-- LEFT JOIN locations l ON l.id = sal.location_id
-- WHERE t.parent_name IS NULL
-- GROUP BY t.name;

-- List enrichment (admin section 2b, M8 fix) — runs once per page
-- with the page's location_ids in a bind param. Returns top_categories
-- and distinct_categories_count.
--
-- STEP 1: get 5 real location IDs to plug into the template. Production
-- uses UUIDs (or large sequential IDs), so the literal `ARRAY[1,2,3,4,5]`
-- placeholder previously here returned zero rows against real data.
-- Run this first:
--
--     SELECT id::text FROM locations
--     WHERE EXISTS (SELECT 1 FROM service_at_locations sal
--                    WHERE sal.location_id = locations.id)
--     LIMIT 5;
--
-- STEP 2: substitute the returned IDs into the ARRAY[] below.
-- (Replace '<id-1>' .. '<id-5>' with the actual values from step 1.)
-- EXPLAIN ANALYZE
-- WITH ranked AS (
--     SELECT sal.location_id, t.name,
--            COUNT(*) AS n,
--            ROW_NUMBER() OVER (
--                PARTITION BY sal.location_id
--                ORDER BY COUNT(*) DESC, t.name
--            ) AS rn
--     FROM service_at_locations sal
--     JOIN service_taxonomy st ON st.service_id = sal.service_id
--     JOIN taxonomies t ON t.id = st.taxonomy_id
--     WHERE sal.location_id::text = ANY(ARRAY['<id-1>','<id-2>','<id-3>','<id-4>','<id-5>'])
--     GROUP BY sal.location_id, t.name
-- )
-- SELECT location_id::text,
--        ARRAY_AGG(name ORDER BY rn) FILTER (WHERE rn <= 3) AS top_categories,
--        COUNT(DISTINCT name) AS distinct_categories_count
-- FROM ranked
-- GROUP BY location_id;


-- ═══════════════════════════════════════════════════════════════
-- QUERY 6: City coverage diagnostic — find pa.city values not in
-- the admin's city→borough mapping.
--
-- The locations admin's heatmap and borough-breakdown rely on
-- `_get_admin_city_to_borough()` to route every `pa.city` value to
-- one of the five NYC boroughs or "Other". If a real NYC
-- neighborhood appears in pa.city but isn't in the mapping, it
-- silently falls to "Other" — under-counting that borough.
--
-- This query is GENERATED from the live Python mapping and shipped
-- as a standalone .sql file that you can open directly in DBeaver,
-- pgAdmin, or any SQL client:
--
--     docs/audits/check_city_coverage.sql
--
-- If the mapping in aggregations.py changes, regenerate the file:
--
--     python tools/check_city_coverage.py --print-sql \
--         > docs/audits/check_city_coverage.sql
--
-- A drift-check test (test_check_city_coverage_sql_file_in_sync)
-- fires in CI if the file goes out of sync with the live mapping.
-- If you see that fail, run the regeneration command above.
--
-- The query groups results into three triage buckets:
--   1. NEEDS REVIEW — possibly missing NYC neighborhood
--      (eyeball each, decide if it's NYC, add to mapping if so)
--   2. non-NYC (correctly Other) — known Jersey/Westchester/LI/etc.
--   3. data-quality — addresses or junk that ended up in pa.city
--
-- Sorted with NEEDS REVIEW first (highest priority) and by
-- location_count DESC within each bucket.
-- ═══════════════════════════════════════════════════════════════
