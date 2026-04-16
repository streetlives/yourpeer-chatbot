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
    test_connection,
    DEFAULT_NEIGHBORHOOD_RADIUS_METERS,
)
from app.rag.query_templates import TEMPLATES


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
            # Borough-level search: pass the borough name directly to use the
            # clean pa.borough column (avoids city field casing chaos).
            # Also keep city_list as a fallback for records where borough is NULL.
            user_params["borough"] = normalized_city
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
        if gender in ("lgbtq", "transgender", "nonbinary"):
            user_params["lgbtq_boost"] = True
    if weekday is not None:
        user_params["weekday"] = weekday
    if current_time:
        user_params["current_time"] = current_time
    if no_requirements:
        user_params["no_requirements"] = True

    # Shelter taxonomy enrichment based on user profile.
    # The DB has shelter sub-categories as separate taxonomy names with
    # parent_name="Shelter": "Families", "Single Adult", "Youth", "Senior",
    # "LGBTQ Young Adult", "Veterans". Services tagged as e.g. "Families"
    # are NOT also tagged with the generic "Shelter" taxonomy, so they're
    # invisible to the base shelter query unless explicitly included.
    if template_key == "shelter":
        extra_taxonomies = []

        # Family composition
        if family_status in ("with_children", "with_family"):
            extra_taxonomies.append("families")
        elif family_status == "alone":
            extra_taxonomies.append("single adult")

        # Age-based sub-categories
        #
        # "youth" is ALWAYS included (like "lgbtq young adult") because
        # NYC DYCD and HUD define homeless youth as ages 16-24, and
        # youth shelters (Covenant House, Ali Forney, DYCD Youth Drop-in
        # Centers) serve this full range. The previous threshold (age < 18)
        # made these shelters invisible to 18-24 year olds — the exact
        # population they serve. The age eligibility filter handles
        # exclusion: a 40-year-old's query includes "youth" in the
        # taxonomy list, but Covenant House's age_max=24 eligibility
        # rule filters it out before results are returned.
        extra_taxonomies.append("youth")
        if age is not None and age >= 62:
            extra_taxonomies.append("senior")

        # Always include LGBTQ Young Adult — we can't detect this from
        # slots, so include it by default so these services are never
        # invisible to any shelter search.
        extra_taxonomies.append("lgbtq young adult")

        # When user explicitly identified as LGBTQ, trans, or nonbinary,
        # also include sub-categories that may be LGBTQ-affirming
        if gender in ("lgbtq", "transgender", "nonbinary"):
            extra_taxonomies.append("drop-in center")
            extra_taxonomies.append("crisis")

        # When user is a DV survivor, include crisis and drop-in center
        # taxonomies. DV-specific services like Safe Horizon Streetwork
        # may be tagged as "Crisis" or "Drop-in Center" rather than
        # generic "Shelter" — without this, they're invisible in the
        # shelter query for DV survivors who aren't LGBTQ.
        if populations and "dv_survivor" in populations:
            if "drop-in center" not in extra_taxonomies:
                extra_taxonomies.append("drop-in center")
            if "crisis" not in extra_taxonomies:
                extra_taxonomies.append("crisis")

        if extra_taxonomies:
            enriched = list(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
            enriched.extend(extra_taxonomies)
            user_params["taxonomy_names"] = enriched

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
            "sober living": ["residential recovery", "supportive housing"],
            "halfway houses": ["residential recovery", "supportive housing"],
            "recovery services": ["substance use treatment", "residential recovery", "support groups"],
            # Clothing sub-types — "interview clothing" intentionally omitted
            # because "interview" conflicts with the employment keyword.
            # TODO: revisit when phrase-level disambiguation is added.
        }

        narrowed_taxonomies = _DETAIL_TO_TAXONOMY_NARROWING.get(service_detail)
        if narrowed_taxonomies:
            user_params["taxonomy_names"] = narrowed_taxonomies
            # BUG FIX: When taxonomy narrowing fires, prevent any default
            # description_pattern (e.g. housing_assistance's required filter)
            # from double-filtering. The narrowed taxonomy is already specific
            # enough — applying a description regex on top would be overly
            # restrictive and could return 0 results.
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
            "dialysis services": r"dialysis|kidney|renal",
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
            # ── housing_assistance sub-types ──
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
        if not narrowed_taxonomies:
            pattern = _DETAIL_DESCRIPTION_FILTERS.get(service_detail)
            if pattern:
                user_params["description_pattern"] = pattern

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
