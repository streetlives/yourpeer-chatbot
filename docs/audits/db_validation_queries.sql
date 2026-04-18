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
