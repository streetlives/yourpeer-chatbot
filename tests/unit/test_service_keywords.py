"""Tests for SERVICE_KEYWORDS, _WORD_BOUNDARY_KEYWORDS, and _NOTABLE_SUB_TYPES
coverage in slot_extraction_regex.py.

Validates that all 17 keyword clusters from the service discovery audit
(April 2026) are correctly mapped, sub-type labels are set for confirmation
messages, word-boundary keywords don't false-positive on substrings, and
peer navigator sample queries extract correctly.

Run: pytest tests/unit/test_service_keywords.py -v
"""

import pytest
from app.services.slot_extraction_regex import extract_slots


class TestHIVHarmReduction:
    """188 services — previously only reachable via 'hiv testing'."""

    def test_harm_reduction(self):
        r = extract_slots("I need harm reduction services")
        assert r["service_type"] == "medical"
        assert r["service_detail"] == "harm reduction services"

    def test_needle_exchange(self):
        r = extract_slots("where can I get a needle exchange")
        assert r["service_type"] == "medical"

    def test_syringe_exchange(self):
        r = extract_slots("I need a syringe exchange")
        assert r["service_type"] == "medical"

    def test_hepatitis(self):
        r = extract_slots("I have hepatitis")
        assert r["service_type"] == "medical"

    def test_hep_c(self):
        r = extract_slots("I need help with hep c")
        assert r["service_type"] == "medical"

    def test_hiv_word_boundary(self):
        r = extract_slots("I need HIV services")
        assert r["service_type"] == "medical"

    def test_prep_with_medical_context(self):
        # "prep" retired from word-boundary (REGEX_AUDIT) — now handled by
        # semantic layer. Multi-word "PrEP medication" matches via "medication".
        r = extract_slots("I need PrEP medication")
        assert r["service_type"] == "medical"

    def test_prep_not_prepare(self):
        """'prep' should NOT match inside 'prepare'."""
        r = extract_slots("I need to prepare for tomorrow")
        assert r["service_type"] is None

    def test_hiv_not_shiver(self):
        """'hiv' should NOT match inside 'shiver'."""
        r = extract_slots("I shiver in the cold")
        assert r["service_type"] is None


class TestSubstanceTreatment:
    """6+ services with exact taxonomy name 'Substance Use Treatment'.

    Substance-use intent routes to service_type='medical', not
    'mental_health'. Rationale (May 5 routing fix):
      - All 'Substance Use Treatment' rows in the Streetlives DB are
        classified as bot_service_type='medical' (the SQL classification
        CASE evaluates the medical branch first).
      - The medical query template includes 'substance use treatment'
        in its taxonomy_names list, so a service_type=medical query
        surfaces the right rows.
      - 'Medical care for detox' is a more dignifying confirmation
        frame than 'Mental health' for someone seeking detox — it
        treats addiction as a medical condition rather than a
        psychological one.
    Mental-health-only intent (counseling, therapy, depression, anxiety)
    still routes to mental_health.
    """

    def test_substance_use_treatment(self):
        r = extract_slots("I need substance use treatment")
        assert r["service_type"] == "medical"
        assert r["service_detail"] == "substance use treatment"

    def test_treatment_program(self):
        r = extract_slots("I need a treatment program")
        assert r["service_type"] == "medical"

    def test_inpatient(self):
        r = extract_slots("I need inpatient treatment")
        assert r["service_type"] == "medical"

    def test_outpatient(self):
        r = extract_slots("I need outpatient treatment")
        assert r["service_type"] == "medical"

    def test_sober_living(self):
        r = extract_slots("I need sober living")
        assert r["service_type"] == "medical"

    def test_halfway_house(self):
        r = extract_slots("looking for a halfway house")
        assert r["service_type"] == "medical"

    def test_anger_management(self):
        r = extract_slots("I need anger management")
        assert r["service_type"] == "mental_health"
        assert r["service_detail"] == "anger management"


class TestDVServices:
    """59 services — previously only reachable via crisis handler."""

    def test_domestic_violence_help(self):
        r = extract_slots("I need domestic violence help")
        assert r["service_type"] == "legal"
        assert r["service_detail"] == "domestic violence services"

    def test_dv_services(self):
        r = extract_slots("I need dv services")
        assert r["service_type"] == "legal"

    def test_abuse_counseling(self):
        r = extract_slots("I need abuse counseling")
        assert r["service_type"] == "legal"

    def test_order_of_protection(self):
        r = extract_slots("I need an order of protection")
        assert r["service_type"] == "legal"


class TestImmigrationAdvanced:
    """66 services beyond basic 'immigration' and 'asylum'."""

    def test_citizenship(self):
        r = extract_slots("I need help with citizenship")
        assert r["service_type"] == "legal"
        assert r["service_detail"] == "citizenship services"

    def test_naturalization(self):
        r = extract_slots("help with naturalization")
        assert r["service_type"] == "legal"

    def test_daca(self):
        r = extract_slots("help with DACA renewal")
        assert r["service_type"] == "legal"
        assert r["service_detail"] == "DACA services"

    def test_tps(self):
        r = extract_slots("I need TPS help")
        assert r["service_type"] == "legal"

    def test_work_authorization(self):
        r = extract_slots("I need work authorization")
        assert r["service_type"] == "legal"


class TestFinancial:
    """32 services — sample query 'I am so bad with money' had 0% coverage."""

    def test_financial_help(self):
        r = extract_slots("I need financial help")
        assert r["service_type"] == "other"
        assert r["service_detail"] == "financial services"

    def test_bad_with_money(self):
        """The original sample query that failed."""
        r = extract_slots("I am so bad with money")
        assert r["service_type"] == "other"
        assert r["service_detail"] == "financial services"

    def test_budgeting(self):
        r = extract_slots("I need help with budgeting")
        assert r["service_type"] == "other"
        assert r["service_detail"] == "budgeting help"

    def test_financial_literacy(self):
        r = extract_slots("I need financial literacy")
        assert r["service_type"] == "other"

    def test_credit_counseling(self):
        r = extract_slots("I need credit counseling")
        assert r["service_type"] == "other"

    def test_money_management(self):
        r = extract_slots("I need money management")
        assert r["service_type"] == "other"


class TestEducation:
    """Education promotion (Phase B Ticket C — TAXONOMY_AUDIT_MAY2026.md §IX).

    Previously these keywords routed to `service_type=other`; after promotion
    they route to `service_type=education`, which queries the Education leaf
    (101 services) plus name-pattern matches against `Other service`
    parent-direct (~80-130 additional services) via the OR'd query filter.

    Word-boundary collision tests (test_esl_not_diesel, test_ged_not_managed,
    etc.) still expect `service_type is None` — those validate the boundary
    matching, not the routing destination.
    """

    def test_english_classes(self):
        r = extract_slots("I need English classes")
        assert r["service_type"] == "education"
        assert r["service_detail"] == "English classes"

    def test_learn_english(self):
        r = extract_slots("I want to learn english")
        assert r["service_type"] == "education"

    def test_adult_education(self):
        r = extract_slots("I need adult education")
        assert r["service_type"] == "education"
        assert r["service_detail"] == "adult education"

    def test_computer_class(self):
        r = extract_slots("I need a computer class")
        assert r["service_type"] == "education"

    def test_digital_literacy(self):
        r = extract_slots("I need digital literacy")
        assert r["service_type"] == "education"

    def test_esl_word_boundary(self):
        r = extract_slots("I need ESL")
        assert r["service_type"] == "education"

    def test_esl_not_diesel(self):
        """'esl' should NOT match inside 'diesel'."""
        r = extract_slots("the diesel engine broke")
        assert r["service_type"] is None

    def test_ged_word_boundary(self):
        r = extract_slots("I need my GED")
        assert r["service_type"] == "education"

    def test_ged_not_managed(self):
        """'ged' should NOT match inside 'managed'."""
        r = extract_slots("I managed to get here")
        assert r["service_type"] is None

    def test_high_school_equivalency(self):
        r = extract_slots("I need my high school equivalency")
        assert r["service_type"] == "education"

    # New tests for keywords added during the Phase B promotion that
    # weren't in the legacy `other` list — citizenship/college-prep cluster.

    def test_citizenship_class(self):
        r = extract_slots("I need a citizenship class")
        assert r["service_type"] == "education"

    def test_college_prep(self):
        r = extract_slots("I need college prep")
        assert r["service_type"] == "education"

    def test_head_start(self):
        r = extract_slots("looking for head start")
        assert r["service_type"] == "education"

    def test_literacy_program(self):
        r = extract_slots("I need a literacy program")
        assert r["service_type"] == "education"


class TestSenior:
    """23 services."""

    def test_senior_center(self):
        r = extract_slots("I need a senior center")
        assert r["service_type"] == "other"
        assert r["service_detail"] == "senior services"

    def test_older_adult(self):
        r = extract_slots("services for older adults")
        assert r["service_type"] == "other"

    def test_aging_services(self):
        r = extract_slots("I need aging services")
        assert r["service_type"] == "other"


class TestReentry:
    """40 services."""

    def test_released_from_jail(self):
        r = extract_slots("I was released from jail")
        assert r["service_type"] == "other"
        assert r["service_detail"] == "re-entry services"

    def test_reentry(self):
        r = extract_slots("I need reentry services")
        assert r["service_type"] == "other"

    def test_parole_with_reentry_context(self):
        # "parole" retired from word-boundary (REGEX_AUDIT) — now handled by
        # semantic layer. Matches here via "reentry" keyword in the phrase.
        r = extract_slots("I need reentry help, I'm on parole")
        assert r["service_type"] == "other"

    def test_probation_with_reentry_context(self):
        # "probation" retired from word-boundary (REGEX_AUDIT) — now handled
        # by semantic layer. Matches here via "reentry" keyword.
        r = extract_slots("I need reentry help for probation")
        assert r["service_type"] == "other"


class TestPregnancy:
    """41 services — previously only detected as family_status."""

    def test_prenatal_care(self):
        r = extract_slots("I need prenatal care")
        assert r["service_type"] == "medical"
        assert r["service_detail"] == "prenatal care"

    def test_maternity(self):
        r = extract_slots("I need maternity services")
        assert r["service_type"] == "medical"

    def test_postpartum(self):
        r = extract_slots("I need postpartum care")
        assert r["service_type"] == "medical"


class TestTradeCareer:
    """15 services."""

    def test_hvac_training(self):
        r = extract_slots("I need HVAC training")
        assert r["service_type"] == "employment"

    def test_vocational_training(self):
        r = extract_slots("I want vocational training")
        assert r["service_type"] == "employment"

    def test_workforce_development(self):
        r = extract_slots("I need workforce development")
        assert r["service_type"] == "employment"

    def test_syep_word_boundary(self):
        r = extract_slots("how do I sign up for SYEP")
        assert r["service_type"] == "employment"


class TestVernacular:
    """Phrases real users say that previously returned nothing."""

    def test_place_to_crash(self):
        r = extract_slots("I need a place to crash")
        assert r["service_type"] == "shelter"

    def test_got_put_out(self):
        r = extract_slots("I got put out")
        assert r["service_type"] == "shelter"

    def test_somewhere_warm(self):
        r = extract_slots("I need somewhere warm")
        assert r["service_type"] == "shelter"

    def test_starving(self):
        r = extract_slots("I'm starving")
        assert r["service_type"] == "food"

    def test_couch_surfing(self):
        r = extract_slots("I've been couch surfing")
        assert r["service_type"] == "shelter"

    def test_sleeping_in_car(self):
        r = extract_slots("I'm sleeping in my car")
        assert r["service_type"] == "shelter"


class TestReclassifications:
    """Items moved between categories."""

    def test_diapers_now_clothing(self):
        """Diapers route to clothing (May 6, 2026 routing fix).

        DB inspection confirmed all diaper-distributing services tag
        under Clothing › Baby Supplies (most common) or Personal Care ›
        Baby (second most common). The previous "other" routing
        surfaced the right services via the wide `other` taxonomy net
        but framed the user's need as "other services," which read as
        dismissive. See _DETAIL_TO_TAXONOMY_NARROWING["baby supplies"]
        for the leaf-taxonomy narrowing that pairs with this routing.
        """
        r = extract_slots("I need diapers")
        assert r["service_type"] == "clothing"
        assert r["service_detail"] == "baby supplies"

    def test_baby_supplies(self):
        r = extract_slots("I need baby supplies")
        assert r["service_type"] == "clothing"
        assert r["service_detail"] == "baby supplies"

    def test_stroller_routes_to_clothing(self):
        """Stroller routes to clothing with service_detail="baby
        supplies" so the leaf-taxonomy narrowing fires."""
        r = extract_slots("I need a stroller")
        assert r["service_type"] == "clothing"
        assert r["service_detail"] == "baby supplies"

    def test_car_seat_routes_to_clothing(self):
        """Car seat routes the same way as diapers/stroller."""
        r = extract_slots("I need a car seat for my baby")
        assert r["service_type"] == "clothing"
        assert r["service_detail"] == "baby supplies"

    def test_baby_formula_still_food(self):
        """Baby formula stays in food (it IS food)."""
        r = extract_slots("I need baby formula")
        assert r["service_type"] == "food"


class TestFalsePositives:
    """Ensure word-boundary keywords don't match substrings."""

    def test_ssi_not_mission(self):
        r = extract_slots("I'm on a mission")
        assert r["service_type"] is None

    def test_prep_not_prepare(self):
        """'prep' should NOT match inside 'prepare'."""
        r = extract_slots("I want to prepare my documents")
        assert r["service_type"] is None

    def test_ged_not_changed(self):
        r = extract_slots("things have changed")
        assert r["service_type"] is None

    def test_esl_not_weasel(self):
        r = extract_slots("that weasel stole my stuff")
        assert r["service_type"] is None

    def test_hiv_not_archive(self):
        r = extract_slots("check the archive")
        assert r["service_type"] is None


class TestPeerNavigatorSampleQueries:
    """All sample queries from the Cornell team document."""

    def test_lgbtq_youth_shelter(self):
        r = extract_slots("21, LGBTQ, in Soho, need a bed tonight")
        assert r["service_type"] == "shelter"
        assert r["location"] == "soho"
        assert r["age"] == 21
        assert r["_gender"] == "lgbtq"

    def test_immigration_manhattan(self):
        r = extract_slots("Recently arrived in the US, need immigration help in Manhattan")
        assert r["service_type"] == "legal"
        assert r["service_detail"] == "immigration services"
        assert r["location"] == "manhattan"

    def test_transman_clothing(self):
        r = extract_slots("I cannot afford clothes on Amazon. I am a transman")
        assert r["service_type"] == "clothing"
        assert r["_gender"] == "male"

    def test_bad_with_money(self):
        r = extract_slots("I am so bad with money.")
        assert r["service_type"] == "other"

    def test_detox_manhattan(self):
        # Substance-use intent (detox) routes to medical, not mental_health.
        # See TestSubstanceTreatment for full rationale. This is the
        # Cornell sample query that R42 surfaced as a routing bug —
        # bot was returning generic mental health results for users
        # disclosing alcohol+opiate dependence.
        r = extract_slots("I need to detox from Alcohol and Opiates. Where can I go in Manhattan?")
        assert r["service_type"] == "medical"
        assert r["service_detail"] == "detox"
        assert r["location"] == "manhattan"

    def test_dv_with_toddler(self):
        r = extract_slots("19, with a toddler, fleeing domestic violence, need somewhere safe tonight")
        assert r["service_type"] == "shelter"
        assert r["age"] == 19
        assert r["family_status"] == "with_children"
        assert r["urgency"] == "high"


# ---------------------------------------------------------------------------
# YourPeer alignment — template and query verification
# ---------------------------------------------------------------------------

class TestYourPeerAlignment:
    """Verify fixes from the YourPeer web app comparison audit.

    These tests ensure the chatbot's query layer matches YourPeer's
    search behavior for taxonomy coverage and schedule data source.
    """

    def test_legal_template_includes_legal_services(self):
        """Legal template must include 'legal services' taxonomy."""
        from app.rag.query_templates import TEMPLATES
        legal_taxonomies = TEMPLATES["legal"]["default_params"]["taxonomy_names"]
        assert "legal services" in legal_taxonomies

    def test_legal_template_includes_immigration(self):
        from app.rag.query_templates import TEMPLATES
        legal_taxonomies = TEMPLATES["legal"]["default_params"]["taxonomy_names"]
        assert "immigration services" in legal_taxonomies

    def test_legal_query_sql_includes_taxonomy(self):
        """Verify the generated SQL actually queries the taxonomy."""
        from app.rag.query_templates import build_query
        _, params = build_query("legal", {"borough": "Manhattan"})
        assert "legal services" in params["taxonomy_names"]

    def test_schedule_uses_holiday_schedules(self):
        """Phase 0a: chatbot must read holiday_schedules (10,593 rows,
        current hours) not regular_schedules (1,049 rows, stale).
        YourPeer uses HolidaySchedules for all schedule display."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("food", {"borough": "Manhattan"})
        assert "JOIN holiday_schedules" in sql
        assert "JOIN regular_schedules" not in sql

    def test_schedule_uses_covid19_occasion(self):
        """All current schedule data has occasion='COVID19'."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("food", {"borough": "Manhattan"})
        assert "occasion = 'COVID19'" in sql

    def test_schedule_weekday_isodow(self):
        """holiday_schedules uses 1-7 (ISODOW), not 0-6.
        The join must NOT subtract 1 from ISODOW.

        As of May 2026 the JOIN evaluates ISODOW against
        ``CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York'`` (instead of
        ``CURRENT_DATE``) so the weekday is picked in NYC time. The
        no-subtraction guarantee is the same; only the source expression
        changed. See ``docs/audits/SCHEDULE_TZ_FIX.md``.
        """
        from app.rag.query_templates import build_query
        sql, _ = build_query("food", {"borough": "Manhattan"})
        # The buggy form would subtract 1 from the ISODOW result.
        assert "::int - 1" not in sql, (
            "ISODOW must NOT be decremented — holiday_schedules uses "
            "1=Mon..7=Sun (matches PostgreSQL ISODOW directly)."
        )
        # Verify ISODOW is used (now sourced from NYC-time CURRENT_TIMESTAMP
        # rather than DB-tz CURRENT_DATE).
        assert "ISODOW FROM (CURRENT_TIMESTAMP AT TIME ZONE 'America/New_York')" in sql, (
            "Today's weekday must be evaluated in NYC time so a "
            "Tuesday-evening NYC user sees Tuesday's hours rather than "
            "Wednesday's. See SCHEDULE_TZ_FIX."
        )
        assert ")::int" in sql, "ISODOW result should be cast to int"



# ---------------------------------------------------------------------------
# Phase 4: Sub-Category Narrowing & Description-Based Search
# ---------------------------------------------------------------------------

class TestTaxonomyNarrowing:
    """4a: When service_detail is set, taxonomy_names should be narrowed
    to the specific sub-taxonomy instead of the full parent category."""

    def test_soup_kitchen_narrows_food(self):
        """'soup kitchen' should query only soup kitchen taxonomies,
        not all 11 food taxonomies."""
        from app.rag.query_templates import build_query, TEMPLATES
        default_count = len(TEMPLATES["food"]["default_params"]["taxonomy_names"])
        sql, params = build_query("food", {
            "borough": "Manhattan",
            "taxonomy_names": ["soup kitchen", "mobile soup kitchen"],
        })
        assert params["taxonomy_names"] == ["soup kitchen", "mobile soup kitchen"]
        assert len(params["taxonomy_names"]) < default_count

    def test_food_pantry_narrows_food(self):
        from app.rag.query_templates import build_query
        _, params = build_query("food", {
            "borough": "Manhattan",
            "taxonomy_names": ["food pantry", "mobile pantry"],
        })
        assert "food pantry" in params["taxonomy_names"]
        assert "soup kitchen" not in params["taxonomy_names"]

    def test_shower_narrows_personal_care(self):
        """'shower' should query only shower taxonomy, not laundry/haircuts."""
        from app.rag.query_templates import build_query
        _, params = build_query("personal_care", {
            "borough": "Manhattan",
            "taxonomy_names": ["shower"],
        })
        assert params["taxonomy_names"] == ["shower"]

    def test_laundry_narrows_personal_care(self):
        from app.rag.query_templates import build_query
        _, params = build_query("personal_care", {
            "borough": "Manhattan",
            "taxonomy_names": ["laundry"],
        })
        assert params["taxonomy_names"] == ["laundry"]

    def test_haircut_narrows_personal_care(self):
        from app.rag.query_templates import build_query
        _, params = build_query("personal_care", {
            "borough": "Manhattan",
            "taxonomy_names": ["haircut"],
        })
        assert params["taxonomy_names"] == ["haircut"]

    def test_no_narrowing_without_detail(self):
        """Without service_detail, full taxonomy list is used."""
        from app.rag.query_templates import build_query, TEMPLATES
        default_food = TEMPLATES["food"]["default_params"]["taxonomy_names"]
        _, params = build_query("food", {"borough": "Manhattan"})
        assert params["taxonomy_names"] == default_food

    def test_narrowing_map_covers_all_food_sub_types(self):
        """All food sub-type detail values should trigger taxonomy narrowing."""
        food_cases = [
            ("I need a soup kitchen", "soup kitchens"),
            ("I need a food pantry", "food pantries"),
            ("I need groceries", "groceries"),
        ]
        for query, expected_detail in food_cases:
            r = extract_slots(query)
            assert r["service_type"] == "food", f"Wrong type for: {query}"
            assert r["service_detail"] == expected_detail, f"Wrong detail for: {query}"

    def test_narrowing_map_covers_personal_care_sub_types(self):
        """All personal care sub-types should have narrowing entries."""
        pc_queries = [
            ("I need a shower", "showers"),
            ("I need laundry", "laundry"),
            ("I need a haircut", "haircuts"),
        ]
        for query, expected_detail in pc_queries:
            r = extract_slots(query)
            assert r["service_type"] == "personal_care"
            assert r["service_detail"] == expected_detail


class TestDescriptionFilter:
    """4b: For sub-types of `other` and `education`, description keyword
    filter narrows results by matching against service descriptions.

    The mechanism is the same in both service_types: when a sub-type
    (e.g. "English classes") sets service_detail, the description regex
    pattern from _DETAIL_DESCRIPTION_FILTERS gets bound into the optional
    FILTER_BY_DESCRIPTION_KEYWORDS filter. The only thing that changed
    in Phase B Ticket C is which template the filter runs against:
    English classes / GED programs / adult education / computer classes /
    digital literacy used to fire against the `other` template's
    taxonomy_names; they now fire against the `education` template's
    OR'd filter (taxonomy + name-pattern fallback)."""

    def test_esl_gets_description_filter(self):
        """'ESL' → education with description pattern for English classes."""
        r = extract_slots("I need ESL")
        assert r["service_type"] == "education"
        assert r["service_detail"] == "English classes"

    def test_ged_gets_description_filter(self):
        r = extract_slots("I need my GED")
        assert r["service_type"] == "education"
        assert r["service_detail"] == "GED programs"

    def test_english_classes_description_pattern(self):
        """Verify the description pattern matches real DB content."""
        import re
        # Simulate what query_services does
        _DETAIL_DESCRIPTION_PATTERNS = {
            "English classes": r"ESL|ESOL|english class|learn english",
            "GED programs": r"GED|high school equiv|HSE|diploma|equivalency",
            "disability services": r"disability|disabled|SSI|SSDI|accessible|special needs",
            "financial services": r"financial|money management|budget|credit|debt|financial literacy",
            "re-entry services": r"reentry|re-entry|parole|probation|incarcerat|released|formerly",
        }

        esl_pattern = re.compile(_DETAIL_DESCRIPTION_PATTERNS["English classes"], re.I)
        assert esl_pattern.search("Basic and Immediate ESOL Classes")
        assert esl_pattern.search("ESL classes, assistance in school enrollment")
        assert esl_pattern.search("Help with learning: ESL and literacy courses")
        assert not esl_pattern.search("Free hot meals served daily")

        ged_pattern = re.compile(_DETAIL_DESCRIPTION_PATTERNS["GED programs"], re.I)
        assert ged_pattern.search("They will help you attain your high school equivalency diploma")
        assert ged_pattern.search("GED support for continued education")
        assert not ged_pattern.search("Rental assistance program")

    def test_other_without_detail_no_description_filter(self):
        """'other services' with no detail → no description filter,
        returns all 940 entries."""
        from app.rag.query_templates import build_query
        sql, params = build_query("other", {"borough": "Manhattan"})
        assert "description ~*" not in sql
        assert "description_pattern" not in params

    def test_other_with_detail_has_description_filter(self):
        from app.rag.query_templates import build_query
        sql, params = build_query("other", {
            "borough": "Manhattan",
            "description_pattern": r"ESL|ESOL|english class",
        })
        assert "description ~*" in sql

    def test_description_filter_not_on_food(self):
        """Food uses taxonomy narrowing (4a), not description filter (4b)."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("food", {
            "borough": "Manhattan",
            "taxonomy_names": ["soup kitchen"],
        })
        assert "description ~*" not in sql

    def test_word_boundary_keywords_set_detail(self):
        """Word-boundary keywords must set service_detail so the
        description filter can trigger."""
        wb_cases = [
            # ESL/GED routed to education in Phase B Ticket C; rest unchanged.
            ("I need ESL", "education", "English classes"),
            ("I need my GED", "education", "GED programs"),
            ("I'm on SSI", "other", "disability services"),
            # "parole" and "prep" retired from word-boundary (REGEX_AUDIT) —
            # now handled by semantic layer. Remaining cases still test the
            # word-boundary → service_detail pipeline.
            ("I need SYEP info", "employment", "SYEP programs"),
        ]
        for query, exp_type, exp_detail in wb_cases:
            r = extract_slots(query)
            assert r["service_type"] == exp_type, f"Failed type for: {query}"
            assert r["service_detail"] == exp_detail, f"Failed detail for: {query}"

    def test_toiletries_sets_detail(self):
        r = extract_slots("I need toiletries")
        assert r["service_type"] == "personal_care"
        assert r["service_detail"] == "toiletries"

    def test_restroom_sets_detail(self):
        r = extract_slots("I need a restroom")
        assert r["service_type"] == "personal_care"
        assert r["service_detail"] == "restrooms"

    def test_bathroom_sets_detail(self):
        r = extract_slots("I need a bathroom")
        assert r["service_type"] == "personal_care"
        assert r["service_detail"] == "restrooms"


class TestNarrowingSyncIntegrity:
    """Verify that the taxonomy narrowing map and description pattern map
    keys stay in sync with _NOTABLE_SUB_TYPES values. If a narrowing
    entry has no matching sub-type label, it can never trigger."""

    def test_taxonomy_narrowing_keys_are_reachable(self):
        """Every key in _DETAIL_TO_TAXONOMY_NARROWING must be producible
        as a service_detail value from _NOTABLE_SUB_TYPES."""
        from app.services.slot_extraction_regex import _NOTABLE_SUB_TYPES

        # These are the narrowing map keys from rag/__init__.py
        narrowing_keys = [
            "soup kitchens", "food pantries", "groceries",
            "showers", "laundry", "haircuts", "toiletries", "restrooms",
        ]
        sub_type_values = set(_NOTABLE_SUB_TYPES.values())
        for key in narrowing_keys:
            assert key in sub_type_values, (
                f"Narrowing key '{key}' has no _NOTABLE_SUB_TYPES entry "
                f"that produces it — narrowing will never trigger"
            )

    def test_description_pattern_keys_are_reachable(self):
        """Every key in _DETAIL_DESCRIPTION_PATTERNS must be producible
        as a service_detail value from _NOTABLE_SUB_TYPES."""
        from app.services.slot_extraction_regex import _NOTABLE_SUB_TYPES

        # These are the description pattern keys from rag/__init__.py
        description_keys = [
            "English classes", "GED programs", "adult education",
            "computer classes", "digital literacy",
            "disability services", "financial services", "financial literacy",
            "budgeting help", "senior services", "re-entry services",
            "anger management", "parenting classes", "baby supplies",
            "transportation help", "insurance enrollment",
            "health insurance enrollment", "LGBTQ services", "LGBTQ support",
            "DACA services", "accessibility services",
        ]
        sub_type_values = set(_NOTABLE_SUB_TYPES.values())
        for key in description_keys:
            assert key in sub_type_values, (
                f"Description pattern key '{key}' has no _NOTABLE_SUB_TYPES "
                f"entry that produces it — filter will never trigger"
            )
