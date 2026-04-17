"""
Tests for the query narrowing system — taxonomy narrowing, description
filtering, word boundaries, mutual exclusion, sample queries, and coverage.

Tests mock _execute_sql and call query_services() to exercise the full
narrowing pipeline without a database connection.

Run with: python -m pytest tests/test_narrowing.py -v
"""

import re
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, ".")

from app.rag.query_templates import build_query, build_relaxed_query, TEMPLATES
from app.services.slot_extractor import (
    _NOTABLE_SUB_TYPES,
    SERVICE_KEYWORDS,
    extract_slots,
)


# -----------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------

def _query(service_type: str, **kwargs) -> dict:
    """Call query_services with mocked DB. Returns the SQL bound params."""
    captured = {}

    def mock_sql(sql, params):
        if not captured:
            captured["sql"] = sql
            captured["params"] = dict(params)
        return []

    with patch("app.rag.query_executor._execute_sql", side_effect=mock_sql):
        from app.rag import query_services
        query_services(service_type=service_type, **kwargs)

    return captured.get("params", {})


def _pg_to_py(pattern: str) -> str:
    """Convert PostgreSQL word boundaries to Python equivalents for testing."""
    return pattern.replace("\\m", r"\b").replace("\\M", r"\b")


def _matches(pattern: str, text: str) -> bool:
    """Test if a PG regex pattern matches text (case-insensitive substring)."""
    return bool(re.search(_pg_to_py(pattern), text, re.IGNORECASE))


# =====================================================================
# 1. TAXONOMY NARROWING — every entry
# =====================================================================

class TestTaxonomyNarrowingFood:
    """Food sub-types use taxonomy narrowing (distinct DB tags per sub-type)."""

    def test_soup_kitchens(self):
        p = _query("food", service_detail="soup kitchens")
        assert "soup kitchen" in p["taxonomy_names"]
        assert "food pantry" not in p["taxonomy_names"]

    def test_food_pantries(self):
        p = _query("food", service_detail="food pantries")
        assert "food pantry" in p["taxonomy_names"]
        assert "soup kitchen" not in p["taxonomy_names"]

    def test_groceries(self):
        p = _query("food", service_detail="groceries")
        names = set(p["taxonomy_names"])
        assert "food pantry" in names
        assert "farmer's markets" in names


class TestTaxonomyNarrowingPersonalCare:
    """Personal care sub-types use taxonomy narrowing."""

    def test_showers(self):
        assert _query("personal_care", service_detail="showers")["taxonomy_names"] == ["shower"]

    def test_laundry(self):
        assert _query("personal_care", service_detail="laundry")["taxonomy_names"] == ["laundry"]

    def test_haircuts(self):
        assert _query("personal_care", service_detail="haircuts")["taxonomy_names"] == ["haircut"]

    def test_toiletries(self):
        assert _query("personal_care", service_detail="toiletries")["taxonomy_names"] == ["toiletries"]

    def test_restrooms(self):
        assert _query("personal_care", service_detail="restrooms")["taxonomy_names"] == ["restrooms"]


class TestTaxonomyNarrowingSubstanceUse:
    """Substance use sub-types narrow mental_health to treatment taxonomies."""

    def test_detox(self):
        p = _query("mental_health", service_detail="detox")
        assert set(p["taxonomy_names"]) == {"substance use treatment", "residential recovery"}

    def test_substance_use_treatment(self):
        p = _query("mental_health", service_detail="substance use treatment")
        assert "substance use treatment" in p["taxonomy_names"]

    def test_substance_abuse_services(self):
        p = _query("mental_health", service_detail="substance abuse services")
        assert "substance use treatment" in p["taxonomy_names"]

    def test_addiction_services(self):
        p = _query("mental_health", service_detail="addiction services")
        assert "substance use treatment" in p["taxonomy_names"]

    def test_rehab_services(self):
        p = _query("mental_health", service_detail="rehab services")
        assert "substance use treatment" in p["taxonomy_names"]

    def test_inpatient(self):
        p = _query("mental_health", service_detail="inpatient treatment")
        assert set(p["taxonomy_names"]) == {"substance use treatment", "residential recovery"}

    def test_outpatient(self):
        p = _query("mental_health", service_detail="outpatient treatment")
        assert p["taxonomy_names"] == ["substance use treatment"]

    def test_sober_living(self):
        p = _query("mental_health", service_detail="sober living")
        # "supportive housing" removed April 16, 2026 — DB verified 0 services tagged.
        assert set(p["taxonomy_names"]) == {"residential recovery"}

    def test_halfway_houses(self):
        p = _query("mental_health", service_detail="halfway houses")
        # "supportive housing" removed April 16, 2026 — DB verified 0 services tagged.
        assert set(p["taxonomy_names"]) == {"residential recovery"}

    def test_recovery_services(self):
        p = _query("mental_health", service_detail="recovery services")
        assert "support groups" in p["taxonomy_names"]


class TestTaxonomyNarrowingDefaults:
    """No narrowing should preserve template defaults."""

    @pytest.mark.parametrize("template", [
        "food", "clothing", "personal_care",
        "medical", "mental_health", "legal", "employment",
    ])
    def test_no_detail_keeps_defaults(self, template):
        p = _query(template)
        assert p["taxonomy_names"] == TEMPLATES[template]["default_params"]["taxonomy_names"]

    def test_shelter_adds_lgbtq_young_adult(self):
        """Shelter always adds 'lgbtq young adult' and 'youth' even without service_detail."""
        p = _query("shelter")
        default = TEMPLATES["shelter"]["default_params"]["taxonomy_names"]
        assert "lgbtq young adult" in p["taxonomy_names"]
        assert "youth" in p["taxonomy_names"]

    @pytest.mark.parametrize("template", ["food", "medical", "legal", "mental_health"])
    def test_unknown_detail_keeps_defaults(self, template):
        p = _query(template, service_detail="xyzzy_nonexistent")
        assert p["taxonomy_names"] == TEMPLATES[template]["default_params"]["taxonomy_names"]


# =====================================================================
# 2. DESCRIPTION FILTERING — per-category coverage
# =====================================================================

class TestDescriptionFilterMedical:

    @pytest.mark.parametrize("detail,expected_word", [
        ("dental care", "dental"),
        ("vision care", "vision"),
        ("urgent care", "urgent care"),
        ("prenatal care", "prenatal"),
        ("diabetes / insulin care", "diabet"),
        ("HIV services", "HIV"),
        ("harm reduction services", "harm reduction"),
        ("HIV testing", "HIV"),
        ("STD testing", "STD"),
        ("hepatitis services", "hepatitis"),
        ("vaccinations", "vaccin"),
        ("asthma care", "asthma"),
        # "dialysis services" removed — DB verified April 16, 2026: 0 matches.
        ("needle exchange", "needle"),
        ("postpartum care", "postpartum"),
    ])
    def test_medical_sub_type(self, detail, expected_word):
        p = _query("medical", service_detail=detail)
        assert "description_pattern" in p, f"No description_pattern for '{detail}'"
        assert expected_word.lower() in p["description_pattern"].lower()


class TestDescriptionFilterLegal:

    @pytest.mark.parametrize("detail,expected_word", [
        ("immigration services", "immigra"),
        ("asylum services", "asylum"),
        ("eviction help", "evict"),
        ("domestic violence services", "domestic violence"),
        ("abuse counseling", "abuse"),
        ("citizenship services", "citizen"),
        ("naturalization services", "naturali"),
        ("order of protection", "protective order"),
    ])
    def test_legal_sub_type(self, detail, expected_word):
        p = _query("legal", service_detail=detail)
        assert "description_pattern" in p, f"No description_pattern for '{detail}'"
        assert expected_word.lower() in p["description_pattern"].lower()


class TestDescriptionFilterMentalHealth:

    @pytest.mark.parametrize("detail,expected_word", [
        ("AA meetings", "alcoholics anonymous"),
        ("NA meetings", "narcotics anonymous"),
        ("counseling", "counsel"),
        ("therapy", "therap"),
        ("treatment centers", "treatment center"),
        ("treatment programs", "treatment program"),
        ("anger management", "anger management"),
    ])
    def test_mental_health_sub_type(self, detail, expected_word):
        p = _query("mental_health", service_detail=detail)
        assert "description_pattern" in p, f"No description_pattern for '{detail}'"
        assert expected_word.lower() in p["description_pattern"].lower()


class TestDescriptionFilterOther:

    @pytest.mark.parametrize("detail,expected_word", [
        ("English classes", "ESL"),
        ("GED programs", "GED"),
        ("disability services", "disability"),
        ("financial services", "financial"),
        ("senior services", "senior"),
        ("re-entry services", "reentry"),
        ("baby supplies", "diaper"),
        ("LGBTQ services", "LGBTQ"),
        ("food stamps / SNAP", "food stamp"),
        ("Medicaid enrollment", "medicaid"),
        ("Social Security", "social security"),
        ("benefits enrollment", "benefit"),
        ("cash assistance", "cash assist"),
    ])
    def test_other_sub_type(self, detail, expected_word):
        p = _query("other", service_detail=detail)
        assert "description_pattern" in p, f"No description_pattern for '{detail}'"
        assert expected_word.lower() in p["description_pattern"].lower()


class TestDescriptionFilterHousing:
    """Housing program sub-types route via 'other' template (matches YourPeer)."""

    @pytest.mark.parametrize("detail,expected_word", [
        ("rental assistance", "rental assist"),
        ("Section 8 vouchers", "section 8"),
        ("NYCHA housing", "NYCHA"),
        ("affordable housing", "affordable housing"),
        ("eviction prevention", "eviction prevent"),
        ("housing vouchers", "housing voucher"),
    ])
    def test_housing_sub_type(self, detail, expected_word):
        p = _query("other", service_detail=detail)
        assert "description_pattern" in p
        assert expected_word.lower() in p["description_pattern"].lower()


# =====================================================================
# 3. MUTUAL EXCLUSION — taxonomy and description never both fire
# =====================================================================

class TestMutualExclusion:

    def test_taxonomy_blocks_description_detox(self):
        """Detox has taxonomy narrowing → description_pattern absent or '.'."""
        p = _query("mental_health", service_detail="detox")
        dp = p.get("description_pattern")
        assert dp is None or dp == "."

    def test_taxonomy_blocks_description_showers(self):
        p = _query("personal_care", service_detail="showers")
        dp = p.get("description_pattern")
        assert dp is None or dp == "."

    def test_description_fires_for_dental(self):
        """Dental has NO taxonomy narrowing → description filter fires."""
        p = _query("medical", service_detail="dental care")
        assert "dental" in p.get("description_pattern", "")
        # Taxonomy should be UNCHANGED
        assert p["taxonomy_names"] == TEMPLATES["medical"]["default_params"]["taxonomy_names"]

    def test_skip_flag_consumed(self):
        """_skip_description_filter must be consumed by build_query, not in params."""
        p = _query("mental_health", service_detail="detox")
        assert "_skip_description_filter" not in p

    def test_description_fires_for_counseling(self):
        """Counseling has NO taxonomy narrowing → description filter fires."""
        p = _query("mental_health", service_detail="counseling")
        assert "counsel" in p.get("description_pattern", "")

    def test_taxonomy_fires_for_rehab(self):
        """Rehab HAS taxonomy narrowing → description blocked."""
        p = _query("mental_health", service_detail="rehab services")
        assert "substance use treatment" in p["taxonomy_names"]
        dp = p.get("description_pattern")
        assert dp is None or dp == "."


# =====================================================================
# 4. POSTGRESQL WORD BOUNDARIES — \m...\M not \b
# =====================================================================

class TestWordBoundaryCorrectness:
    """No pattern in the codebase should use \\b (backspace in PostgreSQL)."""

    def _all_patterns(self) -> dict:
        with open("app/rag/__init__.py") as f:
            src = f.read()
        return dict(re.findall(r'"([^"]+)"\s*:\s*r"([^"]+)"', src))

    def test_no_backslash_b(self):
        for detail, pat in self._all_patterns().items():
            assert "\\b" not in pat, (
                f"'{detail}' uses \\b (backspace in PG, not word boundary). "
                f"Use \\m...\\M instead. Pattern: {pat}"
            )


class TestWordBoundaryFalsePositives:
    """Short acronyms must NOT match inside common words."""

    @pytest.mark.parametrize("detail,false_positive_text", [
        ("immigration services", "We provide food services"),
        ("immigration services", "Best practices for intake"),
        ("immigration services", "Advice and guidance available"),
        ("immigration services", "Police precinct community board"),
        ("domestic violence services", "Advisory committee meeting"),
        ("domestic violence services", "Individual counseling program"),
        ("abuse counseling", "Advisory board members"),
        ("disability services", "Our mission is community"),
        ("disability services", "Commission on human rights"),
        ("Social Security", "Our mission is to help"),
        ("Social Security", "Commission members present"),
        ("order of protection", "Loop bus transit route"),
        ("AA meetings", "Aardvark animal rescue"),
        ("NA meetings", "National Alliance chapter"),
        ("hepatitis services", "Community helper program"),
        ("hepatitis services", "Shepherd Center outreach"),
        ("Section 8 vouchers", "Archival records database"),
        ("housing vouchers", "Archival records access"),
        ("rental assistance", "Physical therapy center"),
        ("HIV services", "Pepper spray self defense"),
    ])
    def test_no_false_positive(self, detail, false_positive_text):
        p = _query(self._category_for(detail), service_detail=detail)
        pattern = p.get("description_pattern", "")
        assert not _matches(pattern, false_positive_text), (
            f"'{detail}' pattern falsely matches '{false_positive_text}'\n"
            f"Pattern: {pattern}"
        )

    def _category_for(self, detail: str) -> str:
        """Infer template key from detail name."""
        kw_to_cat = {}
        for cat, keywords in SERVICE_KEYWORDS.items():
            for kw in keywords:
                kw_to_cat[kw] = cat
        for kw, d in _NOTABLE_SUB_TYPES.items():
            if d == detail:
                return kw_to_cat.get(kw, "other")
        return "other"


class TestWordBoundaryTruePositives:
    """Acronyms MUST match when they appear as standalone words."""

    @pytest.mark.parametrize("detail,true_positive_text", [
        ("immigration services", "ICE enforcement actions"),
        ("immigration services", "Contact ICE for detained persons"),
        ("domestic violence services", "DV shelter available"),
        ("domestic violence services", "DV hotline 24/7"),
        ("disability services", "SSI benefits enrollment"),
        ("disability services", "Apply for SSDI today"),
        ("Social Security", "SSA office hours"),
        ("Social Security", "SSI application help"),
        ("order of protection", "File an OOP at family court"),
        ("AA meetings", "AA meetings every Monday"),
        ("NA meetings", "NA group Thursday 7pm"),
        ("hepatitis services", "Hep B and hep C testing"),
        ("HIV services", "PEP medication after exposure"),
    ])
    def test_true_positive(self, detail, true_positive_text):
        cat = self._category_for(detail)
        p = _query(cat, service_detail=detail)
        pattern = p.get("description_pattern", "")
        assert _matches(pattern, true_positive_text), (
            f"'{detail}' pattern should match '{true_positive_text}'\n"
            f"Pattern: {pattern}"
        )

    def _category_for(self, detail: str) -> str:
        kw_to_cat = {}
        for cat, keywords in SERVICE_KEYWORDS.items():
            for kw in keywords:
                kw_to_cat[kw] = cat
        for kw, d in _NOTABLE_SUB_TYPES.items():
            if d == detail:
                return kw_to_cat.get(kw, "other")
        return "other"


# =====================================================================
# 6. DV SHELTER TAXONOMY ENRICHMENT
# =====================================================================

class TestDvShelterEnrichment:

    def test_dv_survivor_gets_crisis(self):
        p = _query("shelter", populations=["dv_survivor"])
        assert "crisis" in p["taxonomy_names"]

    def test_dv_survivor_gets_drop_in(self):
        p = _query("shelter", populations=["dv_survivor"])
        assert "drop-in center" in p["taxonomy_names"]

    def test_lgbtq_gets_crisis_and_drop_in(self):
        p = _query("shelter", gender="transgender")
        assert "crisis" in p["taxonomy_names"]
        assert "drop-in center" in p["taxonomy_names"]

    def test_no_duplicate_when_lgbtq_and_dv(self):
        """When both LGBTQ and DV, crisis/drop-in should appear once each."""
        p = _query("shelter", gender="transgender", populations=["dv_survivor"])
        assert p["taxonomy_names"].count("crisis") == 1
        assert p["taxonomy_names"].count("drop-in center") == 1

    def test_family_with_dv_gets_families_and_crisis(self):
        p = _query("shelter", family_status="with_children", populations=["dv_survivor"])
        assert "families" in p["taxonomy_names"]
        assert "crisis" in p["taxonomy_names"]

    def test_plain_shelter_default_includes_crisis_and_drop_in(self):
        """Default shelter query includes crisis and drop-in center.

        As of April 16, 2026 DB verification, crisis (13 services) and
        drop-in center (6 services) are Shelter children with non-zero
        service counts — they're in the default list for full YourPeer
        parent-to-child expansion parity. The DV/LGBTQ safety enrichments
        still add them back when narrowing strips them.
        """
        p = _query("shelter")
        # All Shelter children with non-zero counts are in default
        assert "crisis" in p["taxonomy_names"]
        assert "drop-in center" in p["taxonomy_names"]
        assert "referral" in p["taxonomy_names"]
        assert "youth" in p["taxonomy_names"]
        assert "lgbtq young adult" in p["taxonomy_names"]

    def test_narrowing_strips_crisis_and_drop_in(self):
        """When narrowing fires, crisis/drop-in are stripped (not in narrow set).
        DV/LGBTQ safety enrichments add them back — tested in TestDvShelterEnrichment."""
        p = _query("shelter", family_status="alone", age=30)
        # Narrowed to single adult + parent only (no safety signals to add them back)
        assert "crisis" not in p["taxonomy_names"]
        assert "drop-in center" not in p["taxonomy_names"]
        assert "referral" not in p["taxonomy_names"]


# =====================================================================
# 7. COVERAGE — 103/103 service_details have narrowing
# =====================================================================

class TestNarrowingCoverage:

    def _get_handled_details(self) -> set:
        with open("app/rag/__init__.py") as f:
            src = f.read()
        narrowing = set(re.findall(r'"([^"]+)"\s*:\s*\[',
            src[src.find("_DETAIL_TO_TAXONOMY_NARROWING"):
                src.find("}", src.find("_DETAIL_TO_TAXONOMY_NARROWING")) + 1]))
        desc = set(re.findall(r'"([^"]+)"\s*:\s*r"',
            src[src.find("_DETAIL_DESCRIPTION_FILTERS"):
                src.find("        }", src.find("_DETAIL_DESCRIPTION_FILTERS")) + 1]))
        # Clothing occasion attribute filter — third narrowing mechanism
        # (April 16, 2026: uses taxonomy_specific_attributes instead of
        # description regex or taxonomy narrowing).
        occasion = set(re.findall(r'"([^"]+)"\s*:\s*\'\[',
            src[src.find("_CLOTHING_OCCASION_MAP"):
                src.find("}", src.find("_CLOTHING_OCCASION_MAP")) + 1]))
        return narrowing | desc | occasion

    def test_zero_gaps(self):
        handled = self._get_handled_details()
        unique_details = set(_NOTABLE_SUB_TYPES.values())
        gaps = unique_details - handled
        assert len(gaps) == 0, f"Unhandled service_detail values: {sorted(gaps)}"

    def test_count_at_least_95(self):
        """Sanity check: we should have at least 95 unique service_detail values."""
        assert len(set(_NOTABLE_SUB_TYPES.values())) >= 95


# =====================================================================
# 8. TEMPLATE WIRING — all templates support description filter
# =====================================================================

class TestTemplateWiring:

    SERVICE_TEMPLATES = [
        "food", "shelter", "clothing", "personal_care", "medical",
        "mental_health", "legal", "employment", "other",
    ]

    def test_all_templates_have_description_filter(self):
        from app.rag.query_templates import FILTER_BY_DESCRIPTION_KEYWORDS
        for key in self.SERVICE_TEMPLATES:
            tmpl = TEMPLATES[key]
            all_filters = tmpl["required_filters"] + tmpl["optional_filters"]
            assert FILTER_BY_DESCRIPTION_KEYWORDS in all_filters, \
                f"Template '{key}' missing FILTER_BY_DESCRIPTION_KEYWORDS"

    def test_no_template_has_required_description(self):
        """No template should have description filter as required without a default pattern.

        Previously housing_assistance had it as required; that template was removed
        to match YourPeer (which surfaces these services via 'Other service')."""
        from app.rag.query_templates import FILTER_BY_DESCRIPTION_KEYWORDS
        for key in self.SERVICE_TEMPLATES:
            assert FILTER_BY_DESCRIPTION_KEYWORDS not in TEMPLATES[key]["required_filters"], \
                f"'{key}' has description filter as required without a default pattern"

    def test_description_filter_in_sql_when_pattern_set(self):
        """When description_pattern is set, SQL should contain the ~* clause."""
        p = _query("medical", service_detail="dental care")
        sql, _ = build_query("medical", {
            "description_pattern": p["description_pattern"],
            "taxonomy_names": p["taxonomy_names"],
        })
        assert "description_pattern" in sql
        assert "~*" in sql

    def test_no_description_in_sql_without_pattern(self):
        """Without description_pattern, the optional filter should not appear."""
        sql, _ = build_query("medical", {
            "taxonomy_names": ["health"],
        })
        assert "description_pattern" not in sql


# =====================================================================
# 9. RELAXED FALLBACK
# =====================================================================

class TestRelaxedFallback:

    def test_relaxed_keeps_taxonomy_narrowing(self):
        _, relaxed_params = build_relaxed_query("mental_health", {
            "taxonomy_names": ["substance use treatment", "residential recovery"],
            "borough": "Staten Island",
        })
        assert set(relaxed_params["taxonomy_names"]) == {
            "substance use treatment", "residential recovery"
        }

    def test_relaxed_keeps_description_filter(self):
        _, relaxed_params = build_relaxed_query("medical", {
            "taxonomy_names": ["health", "general health", "crisis"],
            "description_pattern": r"dental|dentist",
            "borough": "Bronx",
        })
        assert "dental" in relaxed_params.get("description_pattern", "")

    # Removed test_relaxed_drops_borough (Apr 17, 2026): no caller sets the
    # "borough" param anymore — FILTER_BY_BOROUGH has been deleted because
    # pa.borough does not exist in the Streetlives DB. See BOUNDARY_AUDIT.md.
    # Regression guards in test_query_templates.py enforce the filter stays
    # gone; a separate drop-test here is redundant.

    def test_relaxed_drops_age(self):
        _, relaxed_params = build_relaxed_query("shelter", {
            "taxonomy_names": ["shelter"],
            "age": 17,
        })
        assert "age" not in relaxed_params


# =====================================================================
# 10. SLOT EXTRACTOR INTEGRATION
# =====================================================================

class TestSlotExtractorDetox:
    """End-to-end: user message → extract_slots → service_detail → narrowing."""

    def test_detox_keyword(self):
        slots = extract_slots("I need to detox from alcohol")
        assert slots["service_type"] == "mental_health"
        assert slots["service_detail"] == "detox"

    def test_detoxification_keyword(self):
        slots = extract_slots("Where can I go for detoxification?")
        assert slots["service_detail"] == "detox"

    def test_dental_keyword(self):
        slots = extract_slots("I need to see a dentist")
        assert slots["service_type"] == "medical"
        assert slots["service_detail"] == "dental care"

    def test_immigration_keyword(self):
        slots = extract_slots("I need immigration help in Manhattan")
        assert slots["service_type"] == "legal"
        assert slots["service_detail"] == "immigration services"

    def test_soup_kitchen_keyword(self):
        slots = extract_slots("Is there a soup kitchen near me?")
        assert slots["service_type"] == "food"
        assert slots["service_detail"] == "soup kitchens"

    def test_shower_keyword(self):
        slots = extract_slots("Where can I take a shower in Brooklyn?")
        assert slots["service_type"] == "personal_care"
        assert slots["service_detail"] == "showers"

    def test_ssi_keyword(self):
        slots = extract_slots("I need help applying for SSI")
        assert slots["service_detail"] is not None

    def test_generic_food_no_detail(self):
        slots = extract_slots("I need food in Brooklyn")
        assert slots["service_type"] == "food"
        assert slots["service_detail"] is None


# =====================================================================
# 11. END-TO-END SAMPLE QUERIES (from Cornell team doc)
# =====================================================================

class TestSampleQueries:
    """Verify slot extraction + narrowing for Cornell team sample queries."""

    def test_q8_detox_manhattan(self):
        """'I need to detox from Alcohol and Opiates. Where can I go in Manhattan?'"""
        slots = extract_slots("I need to detox from Alcohol and Opiates. Where can I go in Manhattan?")
        assert slots["service_type"] == "mental_health"
        assert slots["service_detail"] == "detox"
        assert slots["location"] is not None

        p = _query("mental_health", service_detail="detox", location=slots["location"])
        assert set(p["taxonomy_names"]) == {"substance use treatment", "residential recovery"}

    def test_q2_immigration_manhattan(self):
        """'Recently arrived in the US, need immigration help in Manhattan.'"""
        slots = extract_slots("Recently arrived in the US, need immigration help in Manhattan")
        assert slots["service_type"] == "legal"
        assert slots["service_detail"] == "immigration services"

        p = _query("legal", service_detail="immigration services")
        assert "description_pattern" in p
        assert "immigra" in p["description_pattern"]

    def test_q4_bad_with_money(self):
        """'I am so bad with money.'"""
        slots = extract_slots("I am so bad with money")
        if slots["service_type"] == "other":
            assert slots["service_detail"] is not None

    def test_q5_dv_toddler_covenant_house_visible(self):
        """'19, with a toddler, fleeing domestic violence, need somewhere safe tonight'
        Covenant House (youth shelter, ages 16-24) MUST be discoverable.
        Regression: previously 'youth' taxonomy was only added for age < 18,
        making Covenant House invisible to 18-24 year olds."""
        slots = extract_slots(
            "19, with a toddler, fleeing domestic violence, need somewhere safe tonight"
        )
        assert slots["service_type"] == "shelter"
        assert slots["age"] == 19
        assert slots["family_status"] == "with_children"

        p = _query("shelter", age=19, family_status="with_children",
                   populations=["dv_survivor"], location="Manhattan")
        assert "youth" in p["taxonomy_names"], \
            "19yo must have 'youth' taxonomy to see Covenant House"
        assert "families" in p["taxonomy_names"]
        assert "crisis" in p["taxonomy_names"]
        assert "drop-in center" in p["taxonomy_names"]

    def test_q1_lgbtq_youth_soho(self):
        """'21, LGBTQ, in Soho, need a bed tonight.'
        Ali Forney Center (LGBTQ youth shelter) MUST be discoverable."""
        p = _query("shelter", age=21, gender="lgbtq", location="Soho")
        assert "youth" in p["taxonomy_names"], \
            "21yo must have 'youth' taxonomy to see Ali Forney Center"
        assert "lgbtq young adult" in p["taxonomy_names"]
        assert "drop-in center" in p["taxonomy_names"]
        assert "crisis" in p["taxonomy_names"]


# =====================================================================
# 12a. YOUTH SHELTER VISIBILITY
# =====================================================================

class TestYouthShelterVisibility:
    """Youth shelters serve ages 16-24. The 'youth' taxonomy must be
    included for ALL users, not just under-18, because the age eligibility
    filter handles exclusion for older users."""

    def test_youth_always_in_shelter_taxonomy(self):
        """'youth' should be in every shelter query's taxonomy_names."""
        p = _query("shelter")
        assert "youth" in p["taxonomy_names"]

    @pytest.mark.parametrize("age", [16, 17, 18, 19, 20, 21, 24, 30, 50])
    def test_youth_present_at_all_ages(self, age):
        """'youth' should be in taxonomy_names regardless of user's age."""
        p = _query("shelter", age=age)
        assert "youth" in p["taxonomy_names"]

    def test_youth_present_without_age(self):
        """'youth' should be included even when age is unknown."""
        p = _query("shelter")
        assert "youth" in p["taxonomy_names"]


# =====================================================================
# 12. EDGE CASES
# =====================================================================

class TestEdgeCases:

    def test_empty_service_detail(self):
        """Empty string service_detail should behave like None."""
        p = _query("food", service_detail="")
        assert p["taxonomy_names"] == TEMPLATES["food"]["default_params"]["taxonomy_names"]

    def test_none_service_detail(self):
        p = _query("food", service_detail=None)
        assert p["taxonomy_names"] == TEMPLATES["food"]["default_params"]["taxonomy_names"]

    def test_description_filter_appears_in_sql(self):
        """When description_pattern is in params, SQL should include the filter."""
        sql, _ = build_query("legal", {
            "taxonomy_names": ["legal services"],
            "description_pattern": r"immigra|asylum",
        })
        assert "description_pattern" in sql

    def test_narrowing_taxonomy_names_are_lowercase(self):
        """All taxonomy narrowing values should be lowercase (DB comparison is LOWER)."""
        with open("app/rag/__init__.py") as f:
            src = f.read()
        block = src[src.find("_DETAIL_TO_TAXONOMY_NARROWING"):
                     src.find("}", src.find("_DETAIL_TO_TAXONOMY_NARROWING")) + 1]
        # Extract all values inside lists
        values = re.findall(r'"([^"]+)"', block)
        # Skip the keys (they have : after them)
        for v in values:
            if v + '"' + ":" not in block:
                # This is a taxonomy name value, not a key
                pass
        # Simpler: just check all taxonomy names in lists
        for m in re.finditer(r'\[([^\]]+)\]', block):
            names = re.findall(r'"([^"]+)"', m.group(1))
            for name in names:
                assert name == name.lower(), \
                    f"Taxonomy name '{name}' is not lowercase — DB LOWER() comparison will fail"


# =====================================================================
# RUN
# =====================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
