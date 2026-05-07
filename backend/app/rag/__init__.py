"""
RAG module — Template-based query system for the Streetlives database.

This is NOT traditional RAG (retrieval-augmented generation).
The LLM is never used to generate service data. Instead:

    1. Slot extractor fills intake fields from conversation
    2. Template selector picks the right parameterized query
    3. Query executor runs it against the Streetlives DB
    4. Results come back as structured service cards

Public API:
    query_services(service_type, location, **slots) → dict
"""

from app.rag.query_executor import (
    execute_service_query,
    resolve_template_key,
    normalize_location,
    get_borough_city_names,
    get_neighborhood_center,
    is_borough,
    DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
)
from app.rag.query_templates import TEMPLATES


# ---------------------------------------------------------------------------
# Service-detail narrowing dictionaries (module-level, public for eval reuse)
# ---------------------------------------------------------------------------
# Two strategies, both keyed on the slot extractor's `service_detail` string:
#
#   - _DETAIL_TO_TAXONOMY_NARROWING  → swap taxonomy_names for the sub-list.
#       Used for categories with distinct sub-taxonomies (food→soup_kitchen
#       vs food_pantry; substance use→treatment + residential_recovery).
#       Strict: matched rows are tagged with one of the listed taxonomies.
#
#   - _DETAIL_DESCRIPTION_FILTERS    → add a description regex pattern.
#       Used for sub-types that share a parent taxonomy and can only be
#       distinguished by description text (English classes, dental care,
#       eviction prevention, AA meetings, etc.). Looser: regex match
#       against service_description.
#
# These were previously inlined inside ``prepare_query_params`` (lines 293
# and 342). Lifted to module level on May 5, 2026 so the eval mock
# dispatcher can import them directly and apply the same narrowing
# production applies — closing eval-fidelity Finding 5 from the May 5
# audit. Performance side-benefit: dicts are now built once at module
# import rather than on every prepare_query_params call.

_DETAIL_TO_TAXONOMY_NARROWING = {
    # Food sub-types (YourPeer supports soup-kitchen vs pantry filter)
    "soup kitchens": ["soup kitchen", "mobile soup kitchen"],
    "food pantries": ["food pantry", "mobile pantry"],
    "groceries": ["food pantry", "mobile pantry", "mobile market", "farmer's markets"],
    # Personal care sub-types (YourPeer supports per-amenity filter)
    "showers": ["shower"],
    "laundry": ["laundry"],
    "haircuts": ["haircut"],
    "toiletries": ["toiletries"],
    "restrooms": ["restrooms"],
    # Mental health — substance use sub-types
    # Without these, "detox" returns ALL mental health services
    # (counseling, support groups, etc.) instead of just treatment.
    "detox": ["substance use treatment", "residential recovery"],
    "substance use treatment": ["substance use treatment", "residential recovery"],
    "substance abuse services": ["substance use treatment", "residential recovery"],
    "addiction services": ["substance use treatment", "residential recovery"],
    "rehab services": ["substance use treatment", "residential recovery"],
    "inpatient treatment": ["substance use treatment", "residential recovery"],
    "outpatient treatment": ["substance use treatment"],
    # "supportive housing" removed from sober living / halfway houses —
    # DB verified April 16, 2026: 0 services tagged with Supportive Housing.
    "sober living": ["residential recovery"],
    "halfway houses": ["residential recovery"],
    "recovery services": ["substance use treatment", "residential recovery", "support groups"],
    # Baby supplies — DB-verified leaf taxonomies (May 6, 2026 inspection).
    # Diaper-distributing services tag under one of two leaves:
    #   - "Baby Supplies" (parent: Clothing): Mercy House Baby Supplies × 3,
    #     Emergency Diaper Distribution, Community Closet, etc.
    #   - "Baby" (parent: Personal Care): Resource Center Diaper Distribution,
    #     Bushwick "Diapers and Baby Food".
    # FILTER_BY_TAXONOMY_NAME_IN does not walk parent_id → must list the
    # leaf names explicitly. The keyword routing in slot_extraction_regex.py
    # routes diapers/baby supplies/stroller/car seat to service_type=clothing
    # with service_detail="baby supplies", which triggers this narrowing
    # and surfaces both Clothing-parented and Personal-Care-parented
    # services in a single search. The "Clothing" taxonomy itself is
    # excluded from this narrowing because it would over-broaden — every
    # generic clothing pantry would surface for a "diapers" query.
    "baby supplies": ["baby supplies", "baby"],
}

_DETAIL_DESCRIPTION_FILTERS = {
    # ── "other" sub-types ──
    "English classes": r"ESL|ESOL|english class|learn.*english",
    "GED programs": r"GED|high school equiv|HSE|diploma|equivalency",
    "adult education": r"adult education|adult literacy|continuing education",
    "computer classes": r"computer|digital literacy|computer skills|computer training",
    "digital literacy": r"computer|digital literacy|computer skills",
    "disability services": r"disability|disabled|\mSSI\M|\mSSDI\M|accessible|special needs",
    "financial services": r"financial|money management|budget|credit|debt|financial literacy",
    "financial literacy": r"financial literacy|financial education|money management|budget",
    "budgeting help": r"budget|financial|money management|savings",
    "senior services": r"senior|older adult|aging|elder|60\+|65\+|over 60",
    "re-entry services": r"reentry|re-entry|parole|probation|incarcerat|released|formerly",
    "anger management": r"anger management|violence prevention|conflict resolution",
    "parenting classes": r"parenting|parent class|parent support|fatherhood|motherhood",
    "baby supplies": r"diaper|baby|infant|stroller|car seat|formula",
    "transportation help": r"access.a.ride|metrocard|metro card|transit|transportation",
    "insurance enrollment": r"insurance|medicaid enroll|medicare enroll|health insurance",
    "health insurance enrollment": r"health insurance|insurance enroll|medicaid|marketplace",
    "LGBTQ services": r"LGBTQ|queer|transgender|gay|lesbian|bisexual|nonbinary",
    "LGBTQ support": r"LGBTQ|queer|transgender|gay|lesbian|bisexual|nonbinary",
    "DACA services": r"DACA|deferred action|dreamer",
    "accessibility services": r"accessibility|accessible|wheelchair|disability|ADA",
    # other — benefits & financial (previously unhandled)
    "Access-A-Ride help": r"access.a.ride|paratransit|disability.*transport",
    "EBT / food stamps": r"EBT|food stamp|SNAP|electronic benefit",
    "Medicaid enrollment": r"medicaid|health insurance|enroll",
    "Social Security": r"social security|\mSSA\M|\mSSI\M|\mSSDI\M|disability benefit",
    "benefits enrollment": r"benefit|enroll|eligib|public assist|apply",
    "cash assistance": r"cash assist|public assist|TANF|welfare|emergency.*cash",
    "financial advisors": r"financial advis|financial counsel|money manage|budget",
    "food stamps / SNAP": r"food stamp|SNAP|EBT|electronic benefit",
    "money management": r"money manage|budget|financial|savings|debt",
    "public assistance": r"public assist|welfare|benefit|TANF|cash assist",
    "SYEP programs": r"SYEP|summer youth|summer employment|youth employment",
    # ID-services sub-types — added with the IDNYC routing fix
    # (peer_free_id_manhattan). These match Streetlives services that
    # help users obtain ID documents. "IDNYC" is NYC's free city ID
    # program specifically; "ID services" is the broader category
    # (state ID, replacement IDs, identification help); "birth
    # certificate" is a separate vital record. PG word-boundary
    # anchors (\m...\M) on bare "ID" prevent substring matches
    # against "Medicaid", "ride", "video", etc. (\b is the backspace
    # character in PG regex, not a word boundary — see
    # TestWordBoundaryCorrectness.)
    "IDNYC": r"IDNYC|NYC.?ID|municipal.?ID|city.?ID",
    "ID services": r"\mID\M|identification|state.?ID|driver.?license|non.?driver",
    "birth certificate": r"birth certificate|vital record|certificate of birth",
    # ── health_care sub-types ──
    "dental care": r"dental|dentist|oral health|tooth|teeth",
    "vision care": r"vision|eye|optometr|ophthalmol|glasses|optical",
    "urgent care": r"urgent care|walk.in clinic|immediate care",
    "prenatal care": r"prenatal|maternity|pregnan|obstetric|OB.GYN",
    "diabetes / insulin care": r"diabet|insulin|blood sugar|endocrin|A1C",
    "HIV services": r"HIV|AIDS|antiretroviral|PrEP|\mPEP\M",
    "harm reduction services": r"harm reduction|needle|syringe|naloxone|narcan|overdose",
    # health_care — previously unhandled
    "HIV testing": r"HIV.*test|HIV.*screen|rapid.*test.*HIV",
    "STD testing": r"STD|STI|sexual.*health|sexually transmitted",
    "STI testing": r"STI|STD|sexual.*health|sexually transmitted",
    "PrEP services": r"PrEP|pre.exposure|HIV prevent|truvada|descovy",
    "allergy / EpiPen care": r"allerg|epipen|anaphyla",
    "asthma care": r"asthma|inhaler|respiratory|pulmon|breathing",
    "diabetes care": r"diabet|insulin|blood sugar|endocrin|A1C",
    # "dialysis services" removed — DB verified April 16, 2026: 0 matches.
    # No service description mentions dialysis, kidney, or renal.
    "hepatitis services": r"hepatitis|\mhep\M|liver",
    "hepatitis C services": r"hepatitis.*C|hep.*C|HCV",
    "maternity services": r"matern|pregnan|prenatal|postpartum|obstetric",
    "postpartum care": r"postpartum|after.*birth|newborn|maternal",
    "needle exchange": r"needle|syringe|harm reduction|safe.*inject",
    "syringe exchange": r"syringe|needle|harm reduction|safe.*inject",
    "vaccinations": r"vaccin|immuniz|flu.*shot|COVID.*shot|booster",
    # ── legal sub-types ──
    "immigration services": r"immigra|asylum|refugee|undocument|visa|green card|USCIS|naturali|citizen|deporta|removal|\mICE\M",
    "asylum services": r"asylum|refugee|persecution|fear|credible fear|withholding",
    "eviction help": r"evict|tenant|landlord|housing court|rental|lease",
    "domestic violence services": r"domestic violence|\mDV\M|intimate partner|protective order|abuse|safety plan",
    # legal — previously unhandled
    "abuse counseling": r"abuse|domestic violence|\mDV\M|survivor|violence.*counsel",
    "citizenship services": r"citizen|naturali|civics|passport|N-400",
    "naturalization services": r"naturali|citizen|civics|N-400|oath",
    "order of protection": r"order of protection|protective order|restraining order|\mOOP\M",
    # ── mental_health sub-types (previously unhandled) ──
    "AA meetings": r"\mAA\M|alcoholics anonymous|12.step|twelve.step|sobriety",
    "NA meetings": r"\mNA\M|narcotics anonymous|12.step|twelve.step|recovery.*meeting",
    "counseling": r"counsel|therap|talk.*someone|mental health.*support",
    "therapy": r"therap|counsel|psycho|CBT|DBT|mental health",
    "treatment centers": r"treatment center|treatment facility|rehab|recovery center",
    "treatment programs": r"treatment program|recovery program|rehab program",
    # ── housing program sub-types (routed via "other" — matches YourPeer) ──
    "Housing Connect": r"Housing Connect|housing lottery|affordable.*apply",
    "NYCHA housing": r"NYCHA|public housing|housing authority",
    "Section 8 vouchers": r"section 8|housing voucher|rental assist|\mHCV\M",
    "affordable housing": r"affordable housing|low.income housing|subsidiz|below market",
    "eviction prevention": r"eviction prevent|anti.eviction|stay.*home|keep.*housed",
    "homeless prevention programs": r"homeless prevent|prevention|diversion",
    "housing assistance": r"housing assist|housing help|housing support|find.*housing",
    "housing lottery": r"housing lottery|Housing Connect|affordable.*apply",
    "housing programs": r"housing program|housing service|housing support",
    "housing vouchers": r"housing voucher|section 8|rental voucher|\mHCV\M",
    "rental assistance": r"rental assist|rent help|rent subsid|emergency rent|\mERAP\M|one shot",
}


def query_services(
    service_type: str,
    location: str = None,
    age: int = None,
    gender: str = None,
    weekday: int = None,
    current_time: str = None,
    max_results: int = 10,
    latitude: float = None,
    longitude: float = None,
    family_status: str = None,
    colocated_service_types: list = None,
    service_detail: str = None,
    populations: list = None,
    org_name: str = None,
    no_requirements: bool = False,
    taxonomy_override: list = None,
) -> dict:
    """
    High-level entry point: go from intake slots to service results.

    Args:
        service_type: From slot extractor (e.g. "food", "shelter", "medical")
        location:     User's location (borough, neighborhood, or city)
        age:          User's age (for eligibility filtering)
        gender:       User's gender (for gendered services)
        weekday:      Day of week 0=Mon..6=Sun (for schedule filtering)
        current_time: HH:MM string (for "open now" filtering)
        max_results:  Max service cards to return
        latitude:     User's latitude from browser geolocation
        longitude:    User's longitude from browser geolocation
        family_status: Family composition ('with_children', 'with_family', 'alone')
        colocated_service_types: Additional service types that should be
                      co-located at the same location (e.g. ["clothing"]).
                      If provided, results are restricted to locations that
                      also have these services. Falls back to unrestricted
                      query if co-located search returns 0 results.
        taxonomy_override: If provided, skip the template's default taxonomy
                      list AND any enrichment logic, and use these taxonomy
                      names directly. Used by the population-critical
                      fallback (see chatbot.py `_execute_and_respond`) to
                      run a targeted second query for rare population-
                      specific taxonomies like "lgbtq young adult" when
                      the main proximity-bounded query returned none.

    Returns:
        dict with keys: services, result_count, template_used,
                        params_applied, relaxed, execution_ms
        On error: dict with error key and empty services list.
    """
    # Organization name search: when user asks about a specific org,
    # use the OrgNameQuery template regardless of service_type.
    if org_name:
        template_key = "org_name"
        user_params = {"org_name_pattern": f"%{org_name}%"}
    else:
        # Map slot value to template key
        template_key = resolve_template_key(service_type)
        if not template_key:
            return {
                "services": [],
                "result_count": 0,
                "template_used": None,
                "params_applied": {"service_type": service_type},
                "relaxed": False,
                "execution_ms": 0,
                "error": (
                    f"I don't have a search template for '{service_type}' yet. "
                    f"I can help with: {', '.join(sorted(TEMPLATES.keys()))}."
                ),
            }
        user_params = {}

    # Direct browser geolocation: use lat/lng for proximity search
    if latitude is not None and longitude is not None:
        user_params["lat"] = latitude
        user_params["lon"] = longitude
        user_params["radius_meters"] = DEFAULT_NEIGHBORHOOD_RADIUS_METERS
    elif location:
        normalized_city = normalize_location(location)
        user_location_is_borough = is_borough(location)

        if user_location_is_borough:
            # Borough-level search: filter via city_list (pa.city = ANY(...)).
            # Previously we also set user_params["borough"] here to drive a
            # FILTER_BY_BOROUGH clause against pa.borough — but that column
            # does not exist in the Streetlives DB, so the filter errored
            # on every borough query and fell through to the relaxed path.
            # Removed Apr 17, 2026; see docs/audits/BOUNDARY_AUDIT.md.
            city_list = get_borough_city_names(normalized_city)
            if len(city_list) > 1:
                user_params["city_list"] = city_list
        else:
            # Neighborhood-level search: use PostGIS proximity if we have
            # center coords, plus city filters as a safety net.
            center = get_neighborhood_center(location)

            if center:
                lat, lon = center
                user_params["lat"] = lat
                user_params["lon"] = lon
                user_params["radius_meters"] = DEFAULT_NEIGHBORHOOD_RADIUS_METERS

            # City-level filters keep results within the correct borough
            # even if PostGIS data is missing on some locations.
            user_params["city"] = normalized_city
            city_list = get_borough_city_names(normalized_city)
            if len(city_list) > 1:
                user_params["city_list"] = city_list
                user_params["_borough_city_list"] = city_list

    if age is not None:
        user_params["age"] = age
    if gender:
        # The DB only has "male" and "female" in eligibility.eligible_values
        # for the gender parameter. Values like "transgender", "nonbinary",
        # and "lgbtq" would fail the @> check and wrongly EXCLUDE services
        # that have gender rules (e.g., a trans man would be excluded from
        # clothing pantries that accept males).
        #
        # Mapping:
        #   "male" / "female" → pass directly (matches DB values)
        #   "transgender"     → skip filter (no direction specified)
        #   "nonbinary"       → skip filter (not in DB)
        #   "lgbtq"           → skip filter (handled via taxonomy boost)
        _DB_GENDER_VALUES = {"male", "female"}
        if gender in _DB_GENDER_VALUES:
            user_params["gender"] = gender
        # For LGBTQ/trans/nonbinary: skip the eligibility filter but
        # activate the sort boost so affirming services (e.g., Ali Forney
        # Center) float to the top of results without excluding anything.
        # Also check populations: "transman" → gender="male" but
        # populations=["lgbtq"] from the cross-population step.
        if gender in ("lgbtq", "transgender", "nonbinary") or (populations and "lgbtq" in populations):
            user_params["lgbtq_boost"] = True
    if weekday is not None:
        user_params["weekday"] = weekday
    if current_time:
        user_params["current_time"] = current_time
    if no_requirements:
        user_params["no_requirements"] = True

    # Shelter taxonomy enrichment.
    #
    # SKIPPED when taxonomy_override is set — the caller (population-critical
    # fallback) wants a targeted query with ONLY the rare population-specific
    # taxonomies, not the default list plus enrichment.
    #
    # YourPeer parity (as of April 2026 source review + DB verification):
    #   - Default shelter search → parent "Shelter" taxonomy, API expands to
    #     all children. Chatbot equivalent: default_params.taxonomy_names
    #     already lists every Shelter child (see query_templates.py).
    #   - "families" sub-filter → YourPeer REPLACES taxonomy with "Families"
    #     child only. Chatbot narrows to ["families", "shelter"] to preserve
    #     parent — DB shows "Families" child has only 3 services, so strict
    #     narrowing would often return 0 results. Parent preservation keeps
    #     the 18 generic Shelter-tagged services visible too.
    #   - "single adult" sub-filter → YourPeer REPLACES with "Single Adult"
    #     child + passes ageMin=18, ageMax=99 to API. Chatbot narrows to
    #     ["single adult", "shelter"] for the same parent-preservation reason.
    #
    # NOVEL safety enrichments (intentional divergence from YourPeer — flagged
    # in QUERY_PARITY_AUDIT.md). These add population-specific Shelter children
    # back on top of narrowing, because narrowing-by-family-composition strips
    # out services the user plausibly qualifies for based on age/identity:
    #
    #   (1) Age 16–24 → add "youth"               (Covenant House, Ali Forney)
    #   (2) LGBTQ/trans/nonbinary → add           (Ali Forney Center)
    #       "lgbtq young adult" + "drop-in
    #       center" + "crisis"
    #   (3) Age ≥ 62 → add "senior"               (senior-specific shelters)
    #   (4) Veteran → add "veterans" +            (VA shelters, veteran transitional)
    #       "veterans short-term housing"
    #   (5) DV survivor → add "drop-in center"    (Safe Horizon services)
    #       + "crisis"
    #
    # PREGNANT OVERRIDE: pregnant + family_status=alone overrides the
    # "single adult" narrowing to the families narrow — pregnant women
    # typically qualify for family shelter for prenatal services even
    # without existing children.
    if template_key == "shelter" and taxonomy_override is None:
        base_taxonomies = list(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        is_pregnant = bool(populations and "pregnant" in populations)

        # Step 1: Narrow taxonomy list based on family_status.
        # When narrowing fires, include the parent "shelter" taxonomy so
        # generic-tagged services remain visible (DB verification showed
        # the Families child has only 3 services, Single Adult has 38,
        # while the Shelter parent alone has 18 generic-tagged services
        # that a strict narrow would exclude).
        if family_status in ("with_children", "with_family"):
            narrowed = ["families", "shelter"]
        elif family_status == "alone" and is_pregnant:
            # Novel override: pregnant + alone → families (for prenatal services).
            narrowed = ["families", "shelter"]
        elif family_status == "alone":
            narrowed = ["single adult", "shelter"]
        else:
            # No family_status → use the full shelter taxonomy list (equivalent
            # to YourPeer's default: parent Shelter ID + API expansion).
            narrowed = base_taxonomies

        # Step 2: Population-specific safety enrichments (novel, additive).
        # These are no-ops when no narrowing fires (taxonomies already in
        # default list), and restorative when narrowing fires.
        safety_extras = []

        # (1) Youth age range (16–24) → ensure youth-serving shelters are visible
        if age is not None and 16 <= age <= 24:
            safety_extras.append("youth")

        # (2) LGBTQ/trans/nonbinary → affirming services visible
        # "drop-in center" + "crisis" are separate Shelter children that
        # contain LGBTQ-affirming services (DB verified: Drop-in Center
        # and Crisis are parented under Shelter, not Health).
        # "lgbtq young adult" is a Shelter child that narrowing strips out.
        #
        # Check both gender AND populations: "transman" maps to gender="male"
        # (the identified gender) but slot_extraction_regex adds "lgbtq" to
        # _populations to preserve the LGBTQ signal for enrichment.
        _is_lgbtq = (
            gender in ("lgbtq", "transgender", "nonbinary")
            or (populations and "lgbtq" in populations)
        )
        if _is_lgbtq:
            safety_extras.extend(["drop-in center", "crisis", "lgbtq young adult"])

        # (3) Senior age (≥ 62) → senior-specific shelters visible
        if age is not None and age >= 62:
            safety_extras.append("senior")

        # (4) Veteran → veteran-specific shelters visible
        if populations and "veteran" in populations:
            safety_extras.extend(["veterans", "veterans short-term housing"])

        # (5) DV survivor → DV-tagged services (often under Crisis/Drop-in Center)
        if populations and "dv_survivor" in populations:
            for tx in ("drop-in center", "crisis"):
                if tx not in safety_extras:
                    safety_extras.append(tx)

        # Compose final taxonomy list (dedupe while preserving order).
        final = list(narrowed)
        for tx in safety_extras:
            if tx not in final:
                final.append(tx)
        user_params["taxonomy_names"] = final

    # -----------------------------------------------------------------
    # Sub-category narrowing (Phase 4)
    # -----------------------------------------------------------------
    # When service_detail is set (e.g. "soup kitchens", "showers",
    # "English classes"), narrow results to that specific sub-type
    # instead of returning the entire parent category.
    #
    # Two strategies depending on template:
    #   4a. Taxonomy narrowing — for food, personal_care, clothing:
    #       Replace the broad taxonomy_names with the specific sub-taxonomy.
    #   4b. Description filter — for other:
    #       Add a description regex pattern (FILTER_BY_DESCRIPTION_KEYWORDS)
    #       because most "other" services share the same "Other service"
    #       taxonomy and can only be distinguished by description.

    if service_detail:
        # 4a. Taxonomy narrowing for categories with distinct sub-taxonomies
        narrowed_taxonomies = _DETAIL_TO_TAXONOMY_NARROWING.get(service_detail)
        if narrowed_taxonomies:
            user_params["taxonomy_names"] = narrowed_taxonomies
            # BUG FIX: When taxonomy narrowing fires, prevent any default
            # description_pattern from double-filtering. The narrowed taxonomy
            # is already specific enough — applying a description regex on top
            # would be overly restrictive and could return 0 results.
            #
            # We can't set description_pattern=None because build_query's
            # params.update() skips None values (so the default_params entry
            # would survive). Instead, set a flag that build_query checks.
            user_params["_skip_description_filter"] = True

        # 4b. Description filter for sub-types that share a generic taxonomy.
        # These services can't be distinguished by taxonomy alone — they need
        # a description keyword filter to narrow from the parent category.
        #
        # Originally only applied to "other" template, but health_care and
        # legal services also have sub-types (dental, immigration) that
        # share a parent taxonomy ("Health", "Legal") and can only be
        # narrowed by description.
        if not narrowed_taxonomies:
            pattern = _DETAIL_DESCRIPTION_FILTERS.get(service_detail)
            if pattern:
                user_params["description_pattern"] = pattern

    # -----------------------------------------------------------------
    # Clothing occasion filter (Phase 4b)
    # -----------------------------------------------------------------
    # When the user asks for casual or professional clothing, filter
    # by the clothingOccasion attribute in service_taxonomy_specific_attributes.
    # DB verified April 16, 2026: 62 services tagged "Everyday", 28 tagged
    # "Job Interview" (overlapping — many services carry both).
    #
    # This is better than taxonomy narrowing (Interview-Ready Clothing has
    # only 1 service) because the attribute system has 28 services tagged
    # for professional clothing.
    #
    # YourPeer uses taxonomySpecificAttributes API param for this.
    _CLOTHING_OCCASION_MAP = {
        "professional clothing": '["Job Interview"]',
        "interview clothing": '["Job Interview"]',
        "business clothing": '["Job Interview"]',
        "casual clothing": '["Everyday"]',
        "everyday clothing": '["Everyday"]',
        "winter clothing": '["Winter"]',
    }
    if template_key == "clothing" and service_detail:
        occasion_value = _CLOTHING_OCCASION_MAP.get(service_detail)
        if occasion_value:
            user_params["clothing_occasion_value"] = occasion_value

    # -----------------------------------------------------------------
    # Population-based query boost (Phase 3)
    # -----------------------------------------------------------------
    # Cross-cutting identity attributes that modify ALL searches.
    # Veterans get veteran-tagged services boosted via taxonomy rank.
    # Other populations get description-based sort boost via ORDER BY.
    #
    # IMPORTANT: This uses pop_boost_pattern (ORDER BY rank), NOT
    # description_pattern (WHERE filter). The difference:
    #   description_pattern → excludes non-matching services (Phase 4)
    #   pop_boost_pattern   → floats matching services to top, keeps all
    #
    # Auto-infer senior from age when not explicitly stated.
    _populations = list(populations or [])
    if age is not None and age >= 62 and "senior" not in _populations:
        _populations.append("senior")

    # Description-based boost patterns — used as ORDER BY rank expressions.
    # Services matching these patterns sort to the top without excluding
    # non-matching services.
    _POPULATION_DESCRIPTION_BOOSTS = {
        "disabled": r"disabilit|disabled|wheelchair|accessible|\mADA\M|blind|deaf|\mSSI\M|\mSSDI\M",
        "reentry": r"reentry|re-entry|parole|probation|incarcerat|released|formerly",
        "dv_survivor": r"domestic violence|\mDV\M|intimate partner|safety plan|abuse|protective order",
        "pregnant": r"prenatal|maternity|postpartum|WIC|pregnan|maternal|newborn",
        "senior": r"senior|older adult|aging|elder|60\+|65\+|over 60|NORC",
    }

    for pop in _populations:
        if pop == "veteran":
            # Taxonomy-based boost (same pattern as LGBTQ boost)
            user_params["veteran_boost"] = True
        else:
            boost_pattern = _POPULATION_DESCRIPTION_BOOSTS.get(pop)
            if boost_pattern:
                existing_boost = user_params.get("pop_boost_pattern", "")
                if existing_boost:
                    user_params["pop_boost_pattern"] = f"{existing_boost}|{boost_pattern}"
                else:
                    user_params["pop_boost_pattern"] = boost_pattern

    # Co-located service filter: restrict results to locations that also
    # have the additional service types the user asked for.
    colocated_names = []
    if colocated_service_types:
        for co_type in colocated_service_types:
            co_key = resolve_template_key(co_type)
            if co_key and co_key in TEMPLATES:
                co_tax = TEMPLATES[co_key]["default_params"].get("taxonomy_names", [])
                colocated_names.extend(co_tax)
        if colocated_names:
            user_params["colocated_taxonomy_names"] = colocated_names

    # Taxonomy override — last writer wins. Used by the population-critical
    # fallback to run a targeted query for rare population-specific taxonomies
    # (e.g., ["lgbtq young adult"]) instead of the full shelter default list
    # plus enrichment. Applied AFTER all other taxonomy logic so it cannot
    # be accidentally overwritten by service_detail narrowing or enrichment.
    if taxonomy_override is not None:
        user_params["taxonomy_names"] = list(taxonomy_override)

    result = execute_service_query(
        template_key=template_key,
        user_params=user_params,
        max_results=max_results,
    )

    # If co-located query returned 0 results, retry without the co-location
    # filter so the user still gets results for their primary service.
    if colocated_names and result.get("result_count", 0) == 0:
        user_params.pop("colocated_taxonomy_names", None)
        result = execute_service_query(
            template_key=template_key,
            user_params=user_params,
            max_results=max_results,
        )
        result["colocated_fallback"] = True

    # If co-located types were requested but none could be resolved to
    # taxonomy names (unrecognized service type), mark as fallback so
    # the chatbot doesn't claim co-located results were found.
    if colocated_service_types and not colocated_names:
        result["colocated_fallback"] = True

    return result
