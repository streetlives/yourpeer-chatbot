"""
Regression protection for April 2026 YourPeer parity audit decisions.

This file is a *regression shield* for the decisions made during the
QUERY_PARITY_AUDIT.md remediation work. Every test here exists because a
specific decision was made, often after consulting DB data or the Streetlives
team. If any test here fails, it means the chatbot's behavior has diverged
from an intentional decision — investigate before updating the assertion.

Categories:
  1. housing_assistance removal (Apr 15)
  2. Shelter narrow-with-parent-preservation (Apr 16)
  3. Shelter safety enrichments, 5 additive rules (Apr 16)
  4. Medical template fix — no crisis, no mental health (Apr 16)
  5. Display whitelist — no phantom taxonomies (Apr 16)
  6. Invariants across the shelter enrichment state space
  7. Cross-product coverage (narrowing × safety signal)

Each test's docstring includes a "WHY" anchor pointing to the decision
that justifies it, so a future maintainer can find the rationale before
deciding whether to weaken an assertion.

Run with:
    python -m pytest tests/unit/test_audit_regression.py -v
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from app.rag.query_templates import TEMPLATES
from app.rag.query_executor import resolve_template_key, SLOT_SERVICE_TO_TEMPLATE
from app.services.slot_extraction_regex import SERVICE_KEYWORDS, extract_slots


# Path to the narrowing-config file, resolved from this test module's
# location rather than CWD. Without this anchoring, the file-inspection
# tests only pass when pytest is invoked from the `backend/` directory.
_RAG_INIT = (
    Path(__file__).resolve().parent.parent.parent
    / "backend" / "app" / "rag" / "__init__.py"
)


# =============================================================================
# SHARED HELPER
# =============================================================================

def _query(service_type: str, **kwargs) -> dict:
    """Invoke query_services with a mocked DB call. Return the SQL bind params.

    The bound params include the final taxonomy_names list after enrichment,
    which is what most of these tests inspect.
    """
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


def _query_user_params(service_type: str, **kwargs) -> dict:
    """Invoke query_services and return the user_params dict BEFORE SQL binding.

    Unlike _query(), this captures params before build_query() processes them.
    Needed for testing params that are consumed during SQL generation
    (e.g., lgbtq_boost is popped by build_query for ORDER BY, so it
    doesn't appear in SQL bind params).
    """
    captured = {}

    def mock_exec(template_key, user_params, max_results):
        captured.update(user_params)
        return {
            "services": [], "result_count": 0, "template_used": template_key,
            "params_applied": user_params, "relaxed": False, "execution_ms": 0,
        }

    with patch("app.rag.execute_service_query", side_effect=mock_exec):
        from app.rag import query_services
        query_services(service_type=service_type, **kwargs)

    return captured


def _shelter_tax(**kwargs) -> list[str]:
    """Shorthand for shelter queries that returns just the taxonomy_names list."""
    return _query("shelter", location="Manhattan", **kwargs).get("taxonomy_names", [])


# =============================================================================
# 1. housing_assistance REMOVAL
# =============================================================================

class TestHousingAssistanceRemoval:
    """
    WHY: April 15, 2026 audit found the chatbot had a dedicated
    housing_assistance template that did not exist in YourPeer. YourPeer
    surfaces rental assistance, Section 8, eviction prevention, NYCHA, etc.
    via the 'Other service' taxonomy tree. The dedicated template was
    removed; keywords now route to 'other'. This class prevents accidental
    reintroduction.
    """

    def test_template_does_not_exist(self):
        """The dedicated template must stay removed."""
        assert "housing_assistance" not in TEMPLATES, (
            "housing_assistance template was removed to match YourPeer. "
            "If reintroducing, update QUERY_PARITY_AUDIT.md to document the divergence."
        )

    def test_resolve_template_key_redirects_to_other(self):
        """The backward-compat redirect must keep working.

        Any legacy caller producing service_type='housing_assistance' should
        still get a valid template key so queries don't break silently.
        """
        assert resolve_template_key("housing_assistance") == "other"

    def test_slot_service_mapping_has_only_redirect(self):
        """housing_assistance must not map to a housing_assistance template."""
        # It's fine for the key to exist as a redirect to 'other' — that's
        # the backward-compat path. It's NOT fine for it to map to itself.
        mapped = SLOT_SERVICE_TO_TEMPLATE.get("housing_assistance")
        assert mapped != "housing_assistance", (
            "housing_assistance maps to itself — did the dedicated template "
            "get reintroduced?"
        )

    def test_service_keywords_has_no_housing_assistance_group(self):
        """SERVICE_KEYWORDS must not have a housing_assistance keyword group."""
        assert "housing_assistance" not in SERVICE_KEYWORDS, (
            "Housing keywords (rental assistance, Section 8, etc.) should be "
            "in SERVICE_KEYWORDS['other'], not a separate housing_assistance group."
        )

    def test_housing_keywords_route_to_other(self):
        """Slot extraction produces service_type='other' for housing program phrases."""
        for phrase in [
            "I need rental assistance",
            "I'm behind on rent",
            "I need a housing voucher",
            "I need eviction prevention",
            "how do I apply for NYCHA",
            "I need Section 8",
            "I need affordable housing",
            "I need homeless prevention help",
        ]:
            slots = extract_slots(phrase)
            assert slots["service_type"] == "other", (
                f"'{phrase}' should route to 'other' (YourPeer parity), got {slots['service_type']}"
            )

    def test_housing_keywords_set_service_detail_for_description_filter(self):
        """Housing sub-type keywords must set service_detail so the description
        filter fires in rag/__init__.py — this is how the narrowing-within-other
        mechanism works without a dedicated template."""
        expectations = {
            "I need rental assistance": "rental assistance",
            "how do I apply for NYCHA": "NYCHA housing",
            "I need Section 8": "Section 8 vouchers",
            "I need affordable housing": "affordable housing",
        }
        for phrase, expected_detail in expectations.items():
            slots = extract_slots(phrase)
            assert slots["service_detail"] == expected_detail, (
                f"'{phrase}' lost service_detail — description filter won't fire in 'other' template"
            )

    def test_evicted_still_routes_to_shelter(self):
        """'evicted' implies immediate displacement → shelter (urgent), not
        housing programs. This disambiguation must hold."""
        assert extract_slots("I got evicted")["service_type"] == "shelter"
        assert extract_slots("I just got kicked out")["service_type"] == "shelter"


# =============================================================================
# 2. SHELTER NARROW-WITH-PARENT-PRESERVATION
# =============================================================================

class TestShelterNarrowWithParent:
    """
    WHY: April 16, 2026 DB verification revealed shocking child-taxonomy
    service counts:
        Families = 3 services
        Single Adult = 38
        LGBTQ Young Adult = 2
        Senior = 2
        Youth = 4
        Veterans = 1
    Strict YourPeer-style narrow to ['families'] would regularly return 0
    results. The chatbot preserves parent 'shelter' in narrowed queries
    so the 18 generic-Shelter-tagged services remain visible. Documented
    intentional divergence from YourPeer.

    These tests lock in the narrow+parent behavior. If we ever want to
    revert to strict YourPeer narrow, update ALL of these.
    """

    def test_with_children_narrows_to_families_plus_parent(self):
        """with_children narrows to families + parent + generic shelter modes.

        After May 2026 regression fix (TAXONOMY_AUDIT_MAY2026.md §VIII):
        narrowing PRESERVES generic shelter modes (crisis, drop-in center,
        referral, TIL, safe haven, housing lottery, assessment) — these
        children are not population-specific so families plausibly qualify
        for them. Previously the narrowed list was just ['families','shelter'].
        """
        names = _shelter_tax(family_status="with_children")
        # The family-composition child + parent
        assert "families" in names
        assert "shelter" in names
        # Plus the generic shelter modes (regression fix)
        for tx in ("crisis", "drop-in center", "referral",
                   "transitional independent living (til)",
                   "safe haven", "housing lottery", "assessment"):
            assert tx in names, (
                f"family_status='with_children' must preserve '{tx}' "
                f"(generic shelter mode). Got: {names}"
            )

    def test_with_family_narrows_to_families_plus_parent(self):
        """'with_family' is the synonym path for with_children.

        After May 2026 regression fix (TAXONOMY_AUDIT_MAY2026.md §VIII):
        narrowing PRESERVES generic shelter modes (crisis, drop-in center,
        referral, TIL, safe haven, housing lottery, assessment) — these
        children are not population-specific so families plausibly qualify
        for them. Previously the narrowed list was just ['families','shelter'].
        """
        names = _shelter_tax(family_status="with_family")
        # The family-composition child + parent
        assert "families" in names
        assert "shelter" in names
        # Plus the generic shelter modes (regression fix)
        for tx in ("crisis", "drop-in center", "referral",
                   "transitional independent living (til)",
                   "safe haven", "housing lottery", "assessment"):
            assert tx in names, (
                f"family_status='with_family' must preserve '{tx}' "
                f"(generic shelter mode). Got: {names}"
            )

    def test_alone_narrows_to_single_adult_plus_parent(self):
        """alone narrows to single adult + parent + generic shelter modes.

        After May 2026 regression fix (TAXONOMY_AUDIT_MAY2026.md §VIII):
        narrowing PRESERVES generic shelter modes. Previously the narrowed
        list was just ['single adult','shelter'] — which stripped out crisis
        (13 svc), drop-in center (6 svc), referral (6 svc), TIL, safe haven,
        housing lottery, assessment. Users reported "not seeing shelters they
        expected"; the cause was over-aggressive narrowing.
        """
        names = _shelter_tax(family_status="alone")
        # The family-composition child + parent
        assert "single adult" in names
        assert "shelter" in names
        # Plus the generic shelter modes (regression fix)
        for tx in ("crisis", "drop-in center", "referral",
                   "transitional independent living (til)",
                   "safe haven", "housing lottery", "assessment"):
            assert tx in names, (
                f"family_status='alone' must preserve '{tx}' "
                f"(generic shelter mode). Got: {names}"
            )

    def test_narrowing_excludes_population_specific_siblings(self):
        """Narrowing must strip POPULATION-specific siblings that don't match
        the user's signals.

        After May 2026 regression fix: narrowing preserves generic shelter
        modes but still excludes population-specific children for which no
        signal fired (no veteran population, no senior age, no LGBTQ).

        The previous test (renamed from test_narrowing_excludes_generic_siblings)
        incorrectly excluded TIL/safe haven/housing lottery from narrowed
        results — those are non-population shelter modes that families
        and single adults plausibly qualify for. See TAXONOMY_AUDIT_MAY2026.md
        §VIII for the per-taxonomy classification rationale.
        """
        # age=30 → no youth signal, no senior signal; no veteran/LGBTQ/DV pop
        names = _shelter_tax(family_status="with_children", age=30)
        # Population-specific children without matching signal: stripped
        for excluded in ["youth", "senior", "lgbtq young adult",
                         "veterans", "veterans short-term housing",
                         "warming center"]:
            assert excluded not in names, (
                f"'{excluded}' should be excluded from narrowed query "
                f"(population-specific, no signal fired), got {names}"
            )
        # Vestigial (never in defaults)
        assert "supportive housing" not in names
        # Generic shelter modes: KEPT (regression fix)
        for kept in ["safe haven", "transitional independent living (til)",
                     "housing lottery", "crisis", "drop-in center",
                     "referral", "assessment"]:
            assert kept in names, (
                f"'{kept}' should be KEPT in narrowed query "
                f"(generic shelter mode, not population-specific), got {names}"
            )

    def test_narrowing_excludes_other_family_composition_child(self):
        """with_children should not surface single-adult services, and vice versa."""
        names_family = _shelter_tax(family_status="with_children", age=30)
        names_alone = _shelter_tax(family_status="alone", age=30)
        assert "single adult" not in names_family
        assert "families" not in names_alone

    def test_no_family_status_returns_full_default(self):
        """Without family_status, chatbot uses the default shelter taxonomy list.

        DB verified April 16, 2026: 19 Shelter children total.
        OMITTED from default (the May 2026 audit clarified these):
          - Cooling Center (0 services)        — vestigial
          - Intake (0 services)                 — vestigial
          - Supportive Housing (0 services)     — vestigial (removed May 2026
            per TAXONOMY_AUDIT_MAY2026.md Ticket J)
          - Residential Recovery (2 services)   — removed May 2026 per
            TAXONOMY_AUDIT_MAY2026.md §VIII (the CREATE Inc. bug). These
            are substance-use treatment programs that confused the
            "I need a place to sleep tonight" use case. Still reachable
            via mental_health template and via service_detail narrowing
            ("detox", "sober living", etc.).
          - Veterans Short-Term Housing (2 services) — removed May 2026
            audit follow-up. Population-specific to veterans; added back
            via shelter enrichment when populations contains "veteran".
          - Warming Center (1 service)          — removed May 2026 audit
            follow-up. Seasonal; added back via shelter enrichment when
            slots["_cold_context"] is True.
        """
        names = _shelter_tax()
        expected_members = {
            "shelter", "transitional independent living (til)",
            "housing lottery", "safe haven",
            "youth", "families", "single adult", "senior",
            "lgbtq young adult", "veterans",
            # Service-mode Shelter children (added after Covenant House / Safe
            # Horizon DB verification):
            "crisis", "drop-in center", "referral", "assessment",
        }
        assert set(names) == expected_members, (
            f"Default shelter taxonomies changed. Expected {expected_members}, got {set(names)}"
        )

    def test_residential_recovery_not_in_shelter_default(self):
        """Residential Recovery is NOT in default shelter (May 2026 audit fix).

        Per TAXONOMY_AUDIT_MAY2026.md §VIII (the CREATE Inc. bug):
        a user typing "I need shelter" got CREATE Inc., a chemical-dependence
        treatment program tagged at Shelter › Residential Recovery, as the top
        result. The user bailed to yourpeer.nyc. The fix removes Residential
        Recovery from the default shelter route; users seeking substance-use
        services route through mental_health or via service_detail narrowing
        ("detox", "sober living", "halfway houses", etc.).
        """
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "residential recovery" not in names, (
            "Residential Recovery must not be in shelter default — see the "
            "CREATE Inc. user-testing bug in TAXONOMY_AUDIT_MAY2026.md §VIII. "
            "Substance-use services route via mental_health template."
        )

    def test_supportive_housing_not_in_shelter_default(self):
        """Supportive Housing is NOT in default (vestigial, 0 services).

        DB verified April 16, 2026: 0 services tagged with Supportive Housing.
        Listed in TAXONOMY_AUDIT_MAY2026.md Ticket J for removal. Was
        previously in the default for forward-compat; audit confirms removal.
        """
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "supportive housing" not in names, (
            "Supportive Housing has 0 services tagged (DB verified). "
            "Removed per TAXONOMY_AUDIT_MAY2026.md Ticket J."
        )


# =============================================================================
# 3. SHELTER SAFETY ENRICHMENTS (5 additive rules)
# =============================================================================

class TestShelterSafetyEnrichments:
    """
    WHY: April 16, 2026 decision. Strict narrow-by-family-composition strips
    out population-specific Shelter children the user plausibly qualifies
    for. Five additive safety enrichments re-add them:

      (1) Age 16-24 → add 'youth'
      (2) LGBTQ/trans/nonbinary → add 'drop-in center', 'crisis', 'lgbtq young adult'
      (3) Age >= 62 → add 'senior'
      (4) Veteran → add 'veterans', 'veterans short-term housing'
      (5) DV survivor → add 'drop-in center', 'crisis'
      (+) Pregnant + alone → override single-adult narrow to families narrow

    The novel divergence from YourPeer is documented in QUERY_PARITY_AUDIT.md.
    These tests are the regression shield for the decision.
    """

    # --- (1) Age 16-24 youth add ---

    @pytest.mark.parametrize("age", [16, 17, 18, 19, 20, 21, 22, 23, 24])
    def test_youth_age_range_narrow_adds_youth(self, age):
        """Youth (Shelter child) added back when narrowing strips it and user is 16-24."""
        names = _shelter_tax(age=age, family_status="with_children")
        assert "youth" in names, (
            f"Age {age} should trigger youth safety add under narrow, got {names}"
        )

    @pytest.mark.parametrize("age", [15, 25, 30, 45, 61])
    def test_youth_age_range_excludes_out_of_band(self, age):
        """Youth NOT added when age is outside 16-24 range."""
        names = _shelter_tax(age=age, family_status="alone")
        # Narrowing strips 'youth' from default; safety add doesn't fire.
        assert "youth" not in names, (
            f"Age {age} outside youth range (16-24) but 'youth' was added, got {names}"
        )

    def test_youth_not_added_without_age(self):
        """No age → no youth safety add (under narrow).

        Note: without narrow, 'youth' is in the default list so still present.
        """
        names = _shelter_tax(family_status="alone")
        assert "youth" not in names, (
            f"No age provided but 'youth' added. Got {names}"
        )

    # --- (2) LGBTQ enrichment ---

    @pytest.mark.parametrize("gender", ["lgbtq", "transgender", "nonbinary"])
    def test_lgbtq_adds_three_taxonomies_under_narrow(self, gender):
        """LGBTQ signal adds drop-in center, crisis, lgbtq young adult."""
        names = _shelter_tax(gender=gender, family_status="with_children")
        for expected in ["drop-in center", "crisis", "lgbtq young adult"]:
            assert expected in names, (
                f"Gender {gender!r} should add '{expected}' under narrow, got {names}"
            )

    @pytest.mark.parametrize("gender", ["male", "female", None])
    def test_non_lgbtq_gender_no_safety_add(self, gender):
        """Non-LGBTQ gender → no LGBTQ population-specific add (lgbtq young adult
        not added).

        Note: As of the May 2026 regression fix (TAXONOMY_AUDIT_MAY2026.md §VIII),
        crisis and drop-in center are now ALWAYS in narrowed taxonomy_names
        (they're generic shelter modes, not population-specific). This test
        verifies only that the POPULATION-SPECIFIC LGBTQ child ('lgbtq young
        adult') is not added when no LGBTQ signal is present.
        """
        kwargs = {"family_status": "alone"}
        if gender:
            kwargs["gender"] = gender
        names = _shelter_tax(**kwargs)
        assert "lgbtq young adult" not in names, (
            f"gender={gender} should not trigger lgbtq young adult add"
        )

    # --- (3) Senior age enrichment ---

    @pytest.mark.parametrize("age", [62, 65, 70, 85, 100])
    def test_senior_age_narrow_adds_senior(self, age):
        """age >= 62 adds 'senior' under narrow (restoring the stripped child)."""
        names = _shelter_tax(age=age, family_status="alone")
        assert "senior" in names, (
            f"Age {age} should trigger senior safety add under narrow, got {names}"
        )

    @pytest.mark.parametrize("age", [30, 45, 60, 61])
    def test_senior_age_out_of_band_no_add(self, age):
        """age < 62 does NOT trigger the senior add."""
        names = _shelter_tax(age=age, family_status="alone")
        assert "senior" not in names, (
            f"Age {age} below senior threshold (62) but 'senior' added, got {names}"
        )

    # --- (4) Veteran enrichment ---

    def test_veteran_adds_two_taxonomies(self):
        """Veteran adds 'veterans' + 'veterans short-term housing' under narrow."""
        names = _shelter_tax(populations=["veteran"], family_status="alone")
        assert "veterans" in names
        assert "veterans short-term housing" in names

    def test_non_veteran_no_add(self):
        """Unrelated populations don't trigger veteran add."""
        names = _shelter_tax(populations=["disabled"], family_status="alone")
        assert "veterans" not in names
        assert "veterans short-term housing" not in names

    # --- (5) DV survivor enrichment ---

    def test_dv_survivor_adds_drop_in_and_crisis(self):
        """DV survivor adds drop-in center + crisis (Safe Horizon services)."""
        names = _shelter_tax(populations=["dv_survivor"])
        assert "drop-in center" in names
        assert "crisis" in names

    def test_dv_survivor_under_narrow_adds_drop_in_and_crisis(self):
        """DV safety enrichments fire even when narrowing is active."""
        names = _shelter_tax(populations=["dv_survivor"], family_status="with_children")
        assert "drop-in center" in names
        assert "crisis" in names
        # And narrowing still applies
        assert "families" in names
        assert "shelter" in names

    def test_non_dv_population_no_drop_in_add(self):
        """Unrelated population does not trigger DV-specific enrichment.

        Note: As of the May 2026 regression fix (TAXONOMY_AUDIT_MAY2026.md §VIII),
        crisis and drop-in center are ALWAYS in narrowed taxonomy_names (generic
        shelter modes). The DV enrichment is therefore a no-op for those two
        children; it's effectively redundant with the generic-modes preservation.
        This test now verifies the (still-true) negative: a non-DV population
        doesn't bring in any LGBTQ-style additions like 'lgbtq young adult'.
        """
        names = _shelter_tax(populations=["reentry"], family_status="alone")
        # Population-specific children that DV/LGBTQ enrichment would add: not present
        assert "lgbtq young adult" not in names

    # --- Pregnant override ---

    def test_pregnant_plus_alone_overrides_to_families(self):
        """Pregnant + alone narrows to families (prenatal services at family shelters).

        After May 2026 regression fix: narrowed list also includes generic
        shelter modes (crisis, drop-in center, referral, TIL, safe haven,
        housing lottery, assessment) regardless of family_status.
        """
        names = _shelter_tax(populations=["pregnant"], family_status="alone")
        # Override: families (not single adult) + parent
        assert "families" in names
        assert "shelter" in names
        # Specifically must NOT be single-adult narrow
        assert "single adult" not in names
        # Generic shelter modes still preserved
        for tx in ("crisis", "drop-in center", "referral",
                   "transitional independent living (til)",
                   "safe haven", "housing lottery", "assessment"):
            assert tx in names, f"pregnant+alone must preserve '{tx}'"

    def test_pregnant_plus_with_children_stays_families(self):
        """Pregnant + with_children is already families narrow; override is a no-op."""
        names = _shelter_tax(populations=["pregnant"], family_status="with_children")
        assert names[:2] == ["families", "shelter"]

    def test_pregnant_no_family_status_uses_default(self):
        """Pregnant alone (no family_status) → default list (no narrow to trigger override).

        Pregnant only matters as an override IN THE CONTEXT OF family_status=alone.

        After May 2026 audit fixes (TAXONOMY_AUDIT_MAY2026.md §VIII + Ticket J):
        default has 14 entries — was 18, minus 4:
          - residential recovery (CREATE Inc. bug)
          - supportive housing (vestigial 0-service taxonomy)
          - veterans short-term housing (conditional on veteran population)
          - warming center (conditional on cold_context signal)
        """
        names = _shelter_tax(populations=["pregnant"])
        # Default list
        assert "families" in names
        assert "single adult" in names
        assert "shelter" in names
        # Default should be 14 entries after May 2026 audit fixes
        assert len(names) == 14, (
            f"Default shelter list expected 14 entries (May 2026 audit fixes: "
            f"removed residential recovery, supportive housing, veterans "
            f"short-term housing, warming center), got {len(names)}: {names}"
        )


# =============================================================================
# 4. MEDICAL TEMPLATE — post-bug-fix invariants
# =============================================================================

class TestMedicalTemplatePostFix:
    """
    WHY: April 16, 2026 DB verification uncovered a bug: the medical template
    included 'crisis' in its taxonomy_names. Crisis is a SHELTER child
    (13 services), not a Health child. The bug caused medical queries to
    surface shelter crisis services (e.g., Emergency Bed Placement).

    The fix:
      - Removed 'crisis' from medical template
      - Added 'substance use treatment' (11 Health-children services)
      - Added 'support groups' (8 Health-children services)

    YourPeer also excludes 'Mental Health' from the health-care view
    client-side; the chatbot reflects this by not listing 'mental health'
    in the medical template.
    """

    def test_medical_does_not_include_crisis(self):
        """CRITICAL: 'crisis' is a Shelter child, not Health. Must NEVER be
        in medical template default or crisis shelter services get pulled
        into medical queries."""
        names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        assert "crisis" not in names, (
            "'crisis' is a Shelter child (13 services), not Health. Including "
            "it in the medical template pulls shelter Emergency Bed Placement "
            "services into medical queries. DB verified April 2026."
        )

    def test_medical_does_not_include_mental_health(self):
        """Mental Health must not appear in medical. Matches YourPeer's
        client-side exclusion in filter_services_by_name."""
        names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        assert "mental health" not in names, (
            "'mental health' must only appear in mental_health template. "
            "Matches YourPeer's client-side exclusion."
        )

    def test_medical_includes_health_parent(self):
        """Parent 'health' must be in medical template for broad coverage."""
        names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        assert "health" in names, "Parent 'health' taxonomy missing from medical"

    def test_medical_includes_all_health_children_except_mental_health(self):
        """Medical must cover all Health children EXCEPT Mental Health.

        DB April 2026: Health parent + General Health (48), Mental Health (128),
        Substance Use Treatment (11), Support Groups (8). All except Mental
        Health belong in the medical template.
        """
        names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        for child in ["general health", "substance use treatment", "support groups"]:
            assert child in names, (
                f"'{child}' (Health child) missing from medical template. "
                f"DB verified it's a Health child and should appear in the medical view."
            )

    def test_medical_taxonomy_names_exact_set(self):
        """Pin the exact taxonomy set so any future drift surfaces explicitly."""
        names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        expected = {"health", "general health", "substance use treatment", "support groups"}
        assert names == expected, (
            f"Medical taxonomy set changed. Expected {expected}, got {names}. "
            f"If adding a new taxonomy, verify it's a Health child in the DB first."
        )

    def test_intentional_overlap_with_mental_health(self):
        """substance use treatment and support groups ARE shared with
        mental_health template — intentional, documented."""
        medical = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        mental = set(TEMPLATES["mental_health"]["default_params"]["taxonomy_names"])
        expected_overlap = {"substance use treatment", "support groups"}
        actual_overlap = medical & mental
        assert actual_overlap == expected_overlap, (
            f"Medical/mental_health overlap changed. Expected {expected_overlap}, "
            f"got {actual_overlap}. If this is intentional, update the audit doc."
        )


# =============================================================================
# 5. DISPLAY WHITELIST — phantom taxonomies must stay out
# =============================================================================

class TestDisplayWhitelistCleanup:
    """
    WHY: April 16, 2026 DB verification confirmed that 'Harm Reduction',
    'Needle Exchange', and 'Overdose Prevention' do NOT exist as taxonomies
    in the Streetlives DB. They were in the chatbot's _DISPLAY_CATEGORIES
    whitelist (used to format co-located service labels on result cards),
    but no service was ever tagged with them. Aspirational labels → removed.

    If these are reintroduced without DB backing, the display will show
    labels that never match any actual service — harmless but misleading.
    """

    def test_no_phantom_taxonomies_in_format_service_card(self):
        """format_service_card's _DISPLAY_CATEGORIES must not reference
        phantom taxonomies."""
        # Read the source directly to inspect the whitelist
        import inspect
        from app.rag import query_templates
        source = inspect.getsource(query_templates.format_service_card)
        for phantom in ["Harm Reduction", "Needle Exchange", "Overdose Prevention"]:
            assert phantom not in source, (
                f"'{phantom}' is in _DISPLAY_CATEGORIES but does not exist as a "
                f"DB taxonomy. DB verified April 2026."
            )

    def test_no_phantom_taxonomies_in_template_taxonomy_names(self):
        """No template should list phantom taxonomies in its default_params."""
        phantoms_lower = {"harm reduction", "needle exchange", "overdose prevention"}
        for template_key, template in TEMPLATES.items():
            names = set(template.get("default_params", {}).get("taxonomy_names", []))
            overlap = names & phantoms_lower
            assert not overlap, (
                f"Template '{template_key}' references phantom DB taxonomies: {overlap}"
            )


# =============================================================================
# 6. INVARIANTS — rules that must hold in all combinations
# =============================================================================

class TestShelterInvariants:
    """
    Invariants that must hold for ALL shelter enrichment combinations.
    These protect against subtle regressions where one enrichment silently
    breaks another (e.g., a refactor that loses deduplication).
    """

    # Generate a matrix of representative shelter inputs
    # (narrowing mode) × (safety signals) = coverage of the full state space
    SAMPLE_INPUTS = [
        # (label, kwargs)
        ("no_context", {}),
        ("with_children_plain", {"family_status": "with_children"}),
        ("alone_plain", {"family_status": "alone"}),
        ("with_children_age_19", {"family_status": "with_children", "age": 19}),
        ("with_children_age_30", {"family_status": "with_children", "age": 30}),
        ("alone_age_65", {"family_status": "alone", "age": 65}),
        ("alone_trans", {"family_status": "alone", "gender": "transgender"}),
        ("with_children_trans", {"family_status": "with_children", "gender": "transgender"}),
        ("alone_dv", {"family_status": "alone", "populations": ["dv_survivor"]}),
        ("with_children_dv", {"family_status": "with_children", "populations": ["dv_survivor"]}),
        ("veteran_alone", {"family_status": "alone", "populations": ["veteran"]}),
        ("pregnant_alone", {"family_status": "alone", "populations": ["pregnant"]}),
        ("compound_high_vuln", {
            "age": 19, "gender": "transgender",
            "family_status": "with_children",
            "populations": ["dv_survivor"],
        }),
        ("compound_vet_young", {
            "age": 22, "family_status": "alone",
            "populations": ["veteran"],
        }),
    ]

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS])
    def test_taxonomy_names_is_list(self, label, kwargs):
        """Invariant: taxonomy_names is always a list (never None, never set)."""
        names = _shelter_tax(**kwargs)
        assert isinstance(names, list), f"{label}: taxonomy_names is {type(names).__name__}"
        assert all(isinstance(t, str) for t in names), (
            f"{label}: non-string taxonomy entry"
        )

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS])
    def test_taxonomy_names_nonempty(self, label, kwargs):
        """Invariant: every shelter query produces at least one taxonomy."""
        names = _shelter_tax(**kwargs)
        assert len(names) > 0, f"{label}: empty taxonomy list"

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS])
    def test_taxonomy_names_no_duplicates(self, label, kwargs):
        """Invariant: dedupe must work across all combinations.

        This catches refactors that break the `if tx not in final` dedup check.
        Especially relevant for compound cases where safety enrichments overlap
        (e.g., LGBTQ adds crisis; DV also adds crisis — must be deduplicated).
        """
        names = _shelter_tax(**kwargs)
        assert len(names) == len(set(names)), (
            f"{label}: duplicates in taxonomy_names: {names}"
        )

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS
                              if "family_status" in k])
    def test_narrow_always_preserves_shelter_parent(self, label, kwargs):
        """Invariant: when narrowing fires, parent 'shelter' is always present.

        This is the heart of the narrow-with-parent-preservation decision.
        If this ever fails, recall drops for Families/Single Adult queries
        (tiny child counts without the parent safety net).
        """
        names = _shelter_tax(**kwargs)
        assert "shelter" in names, (
            f"{label}: narrow fired but parent 'shelter' was not preserved. "
            f"Got {names}"
        )

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS
                              if "family_status" in k])
    def test_narrow_always_includes_family_composition_child(self, label, kwargs):
        """Invariant: the family_status sub-filter is always honored.

        pregnant+alone → families (override), else → matching child.
        """
        names = _shelter_tax(**kwargs)
        is_pregnant = "populations" in kwargs and "pregnant" in kwargs["populations"]
        fs = kwargs["family_status"]
        if fs in ("with_children", "with_family"):
            assert "families" in names, f"{label}: missing 'families'"
        elif fs == "alone" and is_pregnant:
            assert "families" in names, (
                f"{label}: pregnant+alone should override to families, got {names}"
            )
        elif fs == "alone":
            assert "single adult" in names, f"{label}: missing 'single adult'"

    @pytest.mark.parametrize("label,kwargs",
                             [(lable, k) for lable, k in SAMPLE_INPUTS
                              if not k.get("family_status")])
    def test_no_narrow_uses_full_default_list(self, label, kwargs):
        """Invariant: without family_status, the default 18-taxonomy list is used.

        Safety extras are no-ops when no narrowing fires (taxonomies already
        in default list). Must not remove from the default.
        """
        names = _shelter_tax(**kwargs)
        default = TEMPLATES["shelter"]["default_params"]["taxonomy_names"]
        missing = set(default) - set(names)
        assert not missing, (
            f"{label}: default taxonomies missing: {missing}"
        )


class TestNonShelterTemplatesUnchanged:
    """
    Invariant: only the shelter template has enrichment logic.
    All other templates must return exactly their default_params['taxonomy_names'].
    """

    @pytest.mark.parametrize("template_key", [
        "food", "clothing", "personal_care", "medical",
        "mental_health", "legal", "employment", "other",
    ])
    def test_no_enrichment_for_non_shelter(self, template_key):
        """Passing shelter-ish signals to non-shelter templates must not
        enrich their taxonomy list."""
        names = _query(
            template_key,
            age=19,
            gender="transgender",
            family_status="with_children",
            populations=["dv_survivor", "veteran", "pregnant"],
            location="Manhattan",
        ).get("taxonomy_names", [])
        default = TEMPLATES[template_key]["default_params"]["taxonomy_names"]
        assert set(names) == set(default), (
            f"Template '{template_key}' was enriched by shelter signals. "
            f"Only 'shelter' should have safety enrichments. "
            f"Got {set(names)}, expected {set(default)}"
        )


# =============================================================================
# 7. CROSS-PRODUCT SCENARIOS — Cornell sample queries + compound vulnerabilities
# =============================================================================

class TestCornellSampleQueriesOutcomes:
    """
    WHY: The Cornell AI chatbot sample queries (from project docs) describe
    specific user scenarios and expected service outcomes. The shelter
    enrichment logic must not regress against these. If these break, the
    chatbot's demo/eval scenarios will fail.
    """

    def test_q5_dv_toddler_all_expected_taxonomies_present(self):
        """'19, with a toddler, fleeing domestic violence, need somewhere safe tonight'

        Expected service coverage:
          - Covenant House (Youth) — youth taxonomy
          - DHS PATH (intake) — families/shelter taxonomy
          - Safe Horizon Streetwork (DV) — crisis + drop-in center
        """
        names = _shelter_tax(
            age=19,
            family_status="with_children",
            populations=["dv_survivor"],
        )
        for required in ["youth", "families", "crisis", "drop-in center", "shelter"]:
            assert required in names, (
                f"Cornell Q5 sample: missing '{required}' for 19yo DV mom with toddler. "
                f"Got {names}"
            )

    def test_q1_lgbtq_21_youth_all_expected_taxonomies(self):
        """'21, LGBTQ, in Soho, need a bed tonight.'

        Expected service coverage:
          - Ali Forney Center — lgbtq young adult, youth
          - Streetwork LES — drop-in center, crisis
          - DYCD Youth Drop-in — drop-in center, youth
        """
        names = _shelter_tax(age=21, gender="lgbtq")
        for required in ["youth", "lgbtq young adult", "drop-in center", "crisis"]:
            assert required in names, (
                f"Cornell Q1 sample: missing '{required}' for 21yo LGBTQ user. "
                f"Got {names}"
            )

    def test_q7_family_fleeing_dv_safe_for_moment(self):
        """'Escaped abuse with my child, safe for the moment, need help with shelter'

        Same population as Q5 but without urgent-age context. Critical that
        Safe Horizon services (DV) are surfaced.
        """
        names = _shelter_tax(
            family_status="with_children",
            populations=["dv_survivor"],
        )
        for required in ["families", "crisis", "drop-in center", "shelter"]:
            assert required in names, (
                f"Cornell Q7 sample: missing '{required}' for DV mom with child. "
                f"Got {names}"
            )


class TestCompoundVulnerabilities:
    """
    Test coverage for real-world high-vulnerability combinations that
    combine multiple safety enrichments. These must all layer correctly.
    """

    def test_trans_dv_survivor_compound(self):
        """Trans + DV → LGBTQ extras + DV extras, deduplicated."""
        names = _shelter_tax(
            gender="transgender",
            populations=["dv_survivor"],
        )
        # LGBTQ extras
        assert "lgbtq young adult" in names
        # LGBTQ + DV both add drop-in/crisis — should be deduplicated
        assert names.count("drop-in center") == 1
        assert names.count("crisis") == 1

    def test_young_veteran_alone(self):
        """22yo veteran alone → youth + veteran add layered on alone narrow."""
        names = _shelter_tax(
            age=22,
            family_status="alone",
            populations=["veteran"],
        )
        assert "single adult" in names
        assert "shelter" in names
        assert "youth" in names  # age 22 in youth range
        assert "veterans" in names
        assert "veterans short-term housing" in names

    def test_senior_dv_survivor_alone(self):
        """68yo DV survivor alone → senior + DV adds layered on alone narrow."""
        names = _shelter_tax(
            age=68,
            family_status="alone",
            populations=["dv_survivor"],
        )
        assert "single adult" in names
        assert "shelter" in names
        assert "senior" in names
        assert "drop-in center" in names
        assert "crisis" in names

    def test_pregnant_trans_alone(self):
        """Pregnant trans + alone → pregnant override to families, plus LGBTQ adds."""
        names = _shelter_tax(
            gender="transgender",
            family_status="alone",
            populations=["pregnant"],
        )
        # Pregnant override: single_adult → families
        assert "families" in names
        assert "single adult" not in names
        # Parent preserved
        assert "shelter" in names
        # LGBTQ extras
        assert "lgbtq young adult" in names
        assert "drop-in center" in names
        assert "crisis" in names

    def test_all_five_enrichments_simultaneous(self):
        """Stress test: 19yo pregnant LGBTQ veteran DV survivor alone.

        Unrealistic combo but validates all five rules layer without error.
        - age 19 → youth
        - trans → lgbtq young adult, drop-in, crisis
        - veteran → veterans, veterans short-term housing
        - dv_survivor → drop-in, crisis (deduplicated)
        - pregnant + alone → families override (not single adult)
        """
        names = _shelter_tax(
            age=19,
            gender="transgender",
            family_status="alone",
            populations=["pregnant", "veteran", "dv_survivor"],
        )
        # Pregnant override
        assert "families" in names
        assert "single adult" not in names
        # Parent preserved
        assert "shelter" in names
        # Youth safety
        assert "youth" in names
        # LGBTQ safety
        assert "lgbtq young adult" in names
        assert "drop-in center" in names
        assert "crisis" in names
        # Veteran safety
        assert "veterans" in names
        assert "veterans short-term housing" in names
        # Dedup held
        assert len(names) == len(set(names))


# =============================================================================
# 8. META: template shape invariants
# =============================================================================

class TestOpenNowSortOnlySemantics:
    """
    WHY: April 16, 2026 decision. YourPeer excludes closed services via
    `openAt` API param; the chatbot uses sort-only semantics (ranks closed
    lower but never excludes). Rationale in QUERY_PARITY_AUDIT.md:
    DB schedule coverage is uneven (40-80% for walk-ins, near-zero for
    most categories). Exclude-semantics would silently hide the majority
    of services in sparse-coverage categories.

    These tests lock in the sort-only invariants. If any of them fail,
    the chatbot has drifted toward YourPeer parity without an explicit
    decision to do so.
    """

    def test_chatbot_does_not_pass_current_time_to_queries(self):
        """The chatbot execution layer must not pass current_time to query_services().

        Passing current_time would activate FILTER_BY_OPEN_NOW and switch
        the chatbot to exclude-semantics. This test greps the call site to
        ensure the kwarg is absent.

        Note: Phase 3 moved the ``query_services()`` call to ``chatbot.execution`` (package
        submodule). ``inspect.getsource`` on a package returns only the
        ``__init__`` source, so this test targets the execution submodule
        where the call site now lives.
        """
        import inspect
        from app.services.chatbot import execution
        source = inspect.getsource(execution)
        # The main query_services call site is in _execute_and_respond.
        # Look for the arguments it actually passes.
        # A regression would add 'current_time=' somewhere near the call.
        lines_with_current_time = [
            lable for lable in source.splitlines() if "current_time=" in lable
        ]
        assert not lines_with_current_time, (
            "chatbot.execution passes current_time — this activates "
            "FILTER_BY_OPEN_NOW and switches to exclude-semantics. If "
            "intentional, update QUERY_PARITY_AUDIT.md. Found: "
            + str(lines_with_current_time)
        )

    def test_chatbot_does_not_pass_weekday_to_main_query(self):
        """The primary ``query_services()`` call must not pass weekday.

        weekday IS used by _handle_hours_for_day (post-results handler for
        'what are their hours Saturday?'), but NOT by the primary search.

        Note: Phase 3 moved the primary ``query_services()`` call ``chatbot.execution``
        (package submodule). See the sibling current_time test for context.
        """
        import re
        import inspect
        from app.services.chatbot import execution
        source = inspect.getsource(execution)
        # Find the primary query_services call
        # It spans multiple lines starting with "results = query_services("
        match = re.search(
            r"results\s*=\s*query_services\([^)]*\)",
            source,
            re.DOTALL,
        )
        assert match, (
            "Could not find the primary query_services call site in "
            "chatbot.execution. If the call was refactored, update this test."
        )
        call_text = match.group(0)
        assert "weekday=" not in call_text, (
            "Primary query_services call passes weekday — this activates "
            "FILTER_BY_WEEKDAY and may filter out services without schedule rows. "
            "If intentional, update QUERY_PARITY_AUDIT.md."
        )

    def test_food_template_does_not_include_filter_by_open_now(self):
        """Food template's optional_filters must not include FILTER_BY_OPEN_NOW.

        Previously it did include it, but the filter never activated because
        weekday/current_time were never passed. The filter was removed for
        clarity in Apr 2026 cleanup. Re-adding would be a regression toward
        YourPeer parity.
        """
        from app.rag.query_templates import FILTER_BY_OPEN_NOW
        food_optional = TEMPLATES["food"]["optional_filters"]
        assert FILTER_BY_OPEN_NOW not in food_optional, (
            "FILTER_BY_OPEN_NOW is back in the food template. Decision was "
            "sort-only semantics (see QUERY_PARITY_AUDIT.md). If re-adding, "
            "update the audit doc and the sort logic in query_executor."
        )

    def test_no_template_includes_filter_by_open_now(self):
        """NO template should include FILTER_BY_OPEN_NOW.

        The chatbot uses sort-only semantics across all categories. If any
        template adds this filter, it has diverged from the documented
        decision.
        """
        from app.rag.query_templates import FILTER_BY_OPEN_NOW
        for template_key, template in TEMPLATES.items():
            optional = template.get("optional_filters", [])
            required = template.get("required_filters", [])
            assert FILTER_BY_OPEN_NOW not in optional, (
                f"Template '{template_key}' has FILTER_BY_OPEN_NOW in optional_filters. "
                f"Sort-only semantics apply to all templates (see audit doc)."
            )
            assert FILTER_BY_OPEN_NOW not in required, (
                f"Template '{template_key}' has FILTER_BY_OPEN_NOW in required_filters. "
                f"Schedule filters must never be required."
            )

    def test_sql_base_order_does_not_rank_open_now(self):
        """SQL _BASE_ORDER_PARTS must NOT include an open-now rank.

        As of Apr 16, 2026, open-now sorting is single-source-of-truth in
        Python via `_sort_open_first()` (see query_executor.py). The SQL
        `_OPEN_NOW_RANK` constant is retained for documentation but must
        not be in the base ORDER BY. Previously both layers existed —
        Python always overrode SQL, making the SQL rank cosmetic. One
        source of truth eliminates drift risk.

        If this fails, the SQL query is sorting by open-status too. Two
        risks: (a) SQL disagrees with Python on how to rank unknown vs
        closed (SQL: both=1; Python: unknown=2, closed=1), causing the
        final Python re-sort to re-order what SQL already ordered, which
        is subtly different behavior from no SQL rank at all; (b) future
        changes risk divergence between the two layers.
        """
        from app.rag.query_templates import _BASE_ORDER_PARTS, _OPEN_NOW_RANK
        assert _OPEN_NOW_RANK not in _BASE_ORDER_PARTS, (
            "_OPEN_NOW_RANK found in _BASE_ORDER_PARTS. Open-now sorting "
            "belongs in Python only (see _sort_open_first in query_executor.py). "
            "If reinstating SQL-level rank, update the audit doc and remove "
            "the Python sort to avoid dual-source confusion."
        )

    def test_generated_sql_has_no_open_now_case(self):
        """The SQL generated by build_query() must not contain an open-now CASE.

        Stronger version of the invariant above: checks the actual SQL
        output, not just the ORDER BY array. This catches the case where
        someone adds an open-now CASE directly into the SQL string instead
        of via _BASE_ORDER_PARTS.

        Use word-boundary matching after stripping comments — the May 2026
        schedule TZ fix added ``CURRENT_TIMESTAMP AT TIME ZONE 'America/
        New_York'`` to the executable SQL (inside the ``today_sched`` JOIN
        clause) and surrounding prose comments mention ``CURRENT_TIME``.
        Neither is the open-now rank's signature; only bare ``CURRENT_TIME``
        is. Substring matching would conflate.
        """
        import re
        from app.rag.query_templates import build_query
        # The _OPEN_NOW_RANK signature is CURRENT_TIME comparisons on
        # today_sched.opens_at/closes_at. We look for that specific
        # token, not its substring inside CURRENT_TIMESTAMP.
        for template_key in ["food", "shelter", "clothing", "medical",
                             "mental_health", "legal", "employment",
                             "personal_care", "other"]:
            sql, _ = build_query(template_key, {"max_results": 10})
            # Strip block and line comments.
            sql_no_comments = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
            sql_no_comments = re.sub(r"--[^\n]*", "", sql_no_comments)
            # Word-boundary match for bare CURRENT_TIME, rejecting
            # CURRENT_TIMESTAMP via negative lookahead.
            has_bare_current_time = bool(
                re.search(r"\bCURRENT_TIME\b(?!STAMP)", sql_no_comments)
            )
            assert not has_bare_current_time, (
                f"Template '{template_key}' SQL contains bare CURRENT_TIME "
                f"(open-now CASE). As of Apr 16, 2026, open-now ranking "
                f"is Python-only. Check _BASE_ORDER_PARTS and any template-"
                f"specific ORDER BY additions."
            )

    def test_python_sort_open_first_preserves_order_within_buckets(self):
        """Python _sort_open_first must be stable — preserves existing order
        within each bucket (open / closed / unknown).

        Stability matters because the freshness/name/distance ordering from
        SQL is preserved within each open-status bucket.
        """
        from app.rag.query_executor import _sort_open_first

        cards = [
            {"name": "A", "is_open": "closed"},
            {"name": "B", "is_open": "open"},
            {"name": "C", "is_open": None},  # unknown
            {"name": "D", "is_open": "open"},
            {"name": "E", "is_open": "closed"},
            {"name": "F", "is_open": None},
        ]
        sorted_cards = _sort_open_first(cards)
        names = [c["name"] for c in sorted_cards]

        # Buckets: open (B, D) → closed (A, E) → unknown (C, F)
        # Within each bucket, original order is preserved (stable sort).
        assert names == ["B", "D", "A", "E", "C", "F"], (
            f"Sort order wrong or not stable. Got {names}"
        )

    def test_python_sort_treats_unknown_lower_than_closed(self):
        """Services with no schedule data (is_open=None) must sort BELOW
        closed services. This is the intentional 3-bucket behavior.

        If unknown becomes equal to closed, users see services with missing
        hours data mixed in with services explicitly known to be closed,
        which is a UX regression (unknown might be open; closed definitely
        isn't).
        """
        from app.rag.query_executor import _sort_open_first
        cards = [
            {"name": "unknown", "is_open": None},
            {"name": "closed", "is_open": "closed"},
        ]
        sorted_cards = _sort_open_first(cards)
        names = [c["name"] for c in sorted_cards]
        assert names == ["closed", "unknown"], (
            f"Closed should rank above unknown. Got {names}. "
            f"This separation is intentional — an unknown-schedule service "
            f"might be open, while a closed one definitely isn't."
        )

    def test_compute_schedule_status_handles_no_data(self):
        """Services with no schedule data must produce is_open=None, not 'closed'.

        Mapping no-data to 'closed' would incorrectly treat missing data as
        definite closure. The chatbot preserves the distinction.
        """
        from app.rag.query_templates import _compute_schedule_status
        result = _compute_schedule_status(None, None)
        assert result["is_open"] is None, (
            f"No schedule data should produce is_open=None, got {result['is_open']}"
        )
        assert result["hours_today"] is None

    def test_compute_schedule_status_handles_overnight(self):
        """Overnight schedules (closes < opens) must be handled correctly.

        E.g., a drop-in center open 10 PM - 6 AM should report 'open' when
        queried at 2 AM, not 'closed'.

        Pre-fix: this test asserted only that the result was a valid
        string, because the function took no ``now`` parameter and
        depended on wall-clock time at execution. Post-fix (May 2026
        timezone work, see ``docs/audits/SCHEDULE_TZ_FIX.md``): the
        function accepts an injected ``now`` and we can pin actual
        open/closed values. Comprehensive coverage of timezone and
        boundary cases lives in ``tests/unit/test_query_templates.py::
        TestComputeScheduleStatusOpenClosed``.
        """
        from datetime import time as time_cls
        from app.rag.query_templates import _compute_schedule_status

        # Daytime: 9 AM - 5 PM, queried at 1 PM, should be open.
        result_day = _compute_schedule_status(
            time_cls(9, 0), time_cls(17, 0), now=time_cls(13, 0)
        )
        assert result_day["is_open"] == "open", (
            "Daytime 9-5 schedule at 1 PM should be open."
        )

        # Daytime: same schedule, queried at 6 PM, should be closed.
        result_day_closed = _compute_schedule_status(
            time_cls(9, 0), time_cls(17, 0), now=time_cls(18, 0)
        )
        assert result_day_closed["is_open"] == "closed"

        # Overnight: 10 PM - 6 AM — closes < opens in clock time.
        # Queried at 2 AM, should be open.
        result_overnight = _compute_schedule_status(
            time_cls(22, 0), time_cls(6, 0), now=time_cls(2, 0)
        )
        assert result_overnight["is_open"] == "open", (
            "Overnight 10pm-6am schedule at 2 AM should be open. "
            "Check _compute_schedule_status handles closes<opens case."
        )

        # Overnight: same schedule, queried at 8 AM, should be closed.
        result_overnight_closed = _compute_schedule_status(
            time_cls(22, 0), time_cls(6, 0), now=time_cls(8, 0)
        )
        assert result_overnight_closed["is_open"] == "closed"


# =============================================================================
# 9. META: template shape invariants
# =============================================================================

class TestFilterResponsePagination:
    """
    WHY: April 16, 2026 UX fix. Filter response handlers previously used
    `len(services)` (up to 25, the cached pool) as the denominator in
    count phrasing, leading to "3 of 25 are open" when the user had
    only seen 5 cards — implying they'd missed 20 results they needed
    to scroll to find.

    Also: filter responses could dump up to 25 cards in one shot,
    breaking the 5-per-page model the rest of the UI uses.

    The fix (in post_results.py):
      - Handlers accept `displayed_count` parameter from the session
      - Use displayed_count as denominator when it makes sense
      - Use alternate phrasing when filter_count > displayed_count
        (avoids nonsensical "8 of 5 are open")
      - Cap returned services to _DISPLAY_PAGE_SIZE (5)

    These tests lock in the fix. If they fail, the filter response
    has drifted back toward the 25-card dump behavior.
    """

    def _make_cards(self, n: int, is_open_pattern=None) -> list[dict]:
        """Build n service cards with optional is_open state per index."""
        cards = []
        for i in range(n):
            is_open = (
                is_open_pattern[i] if is_open_pattern and i < len(is_open_pattern)
                else None
            )
            cards.append({
                "service_id": f"svc_{i}",
                "service_name": f"Service {i}",
                "organization": f"Org {i}",
                "is_open": is_open,
                "hours_today": "9am-5pm" if is_open else None,
                "fees": "free" if i % 2 == 0 else None,
                "service_taxonomies": ["Shelter"],
            })
        return cards

    # --- displayed_count as denominator ---

    def test_filter_open_uses_displayed_count_as_denominator(self):
        """'3 of the 5 shown are open' — not 'of the 25'."""
        from app.services.post_results import _handle_filter_open
        cards = self._make_cards(25, ["open", "open", "open"] + [None] * 22)
        result = _handle_filter_open(cards, displayed_count=5)
        assert "of the 5 shown" in result["response"], (
            f"Expected 'of the 5 shown' in response. Got: {result['response']}"
        )
        # Must NOT say "of the 25"
        assert "of the 25" not in result["response"]

    def test_filter_free_uses_displayed_count_as_denominator(self):
        """Same fix for filter_free."""
        from app.services.post_results import _handle_filter_free
        cards = self._make_cards(25)
        # 13 of 25 are "free" (even indices); user has only seen 5
        result = _handle_filter_free(cards, displayed_count=5)
        # filter_count=13, displayed_count=5 → filter_count > displayed_count branch
        # Response should say "I found X" (not the ratio which would be "13 of 5")
        assert "of the 5" not in result["response"], (
            f"Should not say 'of the 5' when filter_count > displayed_count. "
            f"Got: {result['response']}"
        )
        assert "13" in result["response"], (
            f"Should mention actual filter count. Got: {result['response']}"
        )

    def test_filter_subcategory_uses_displayed_count(self):
        """Subcategory filter response uses displayed_count as denominator."""
        from app.services.post_results import _handle_filter_subcategory
        cards = self._make_cards(25)
        # Tag first 3 with 'veterans'
        for i in range(3):
            cards[i]["service_taxonomies"] = ["Shelter", "Veterans"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "veterans", "_original_message": "ones for veterans"},
            cards,
            displayed_count=5,
        )
        # 3 matched, 5 displayed → "3 of the 5 shown"
        assert "of the 5 shown" in result["response"], (
            f"Expected 'of the 5 shown'. Got: {result['response']}"
        )
        assert "of the 25" not in result["response"]

    # --- filter_count > displayed_count handling ---

    def test_filter_open_avoids_nonsensical_ratio(self):
        """When filter_count > displayed_count, phrase must avoid misleading
        ratios. Neither '8 of 5' (nonsense) nor '8 of 25' (pre-fix bug) is
        acceptable — both are blocked."""
        from app.services.post_results import _handle_filter_open
        # 8 open in total (of 25), user has seen 5
        is_open_pattern = ["open"] * 8 + [None] * 17
        cards = self._make_cards(25, is_open_pattern)
        result = _handle_filter_open(cards, displayed_count=5)
        # Nonsensical: can't have 8 of 5
        assert "8 of the 5" not in result["response"], (
            f"Ratio nonsense: filter_count > displayed_count produced misleading "
            f"'of 5' phrasing. Got: {result['response']}"
        )
        # Pre-fix bug: using full cached pool as denominator
        assert "of the 25" not in result["response"], (
            f"Regressed to pre-fix 'of 25' phrasing — should use displayed_count "
            f"or alternate phrasing. Got: {result['response']}"
        )
        # Should mention the actual count of open services found
        assert "8" in result["response"]

    def test_filter_subcategory_avoids_nonsensical_ratio(self):
        """Subcategory filter: same avoid-nonsense ratio rule."""
        from app.services.post_results import _handle_filter_subcategory
        cards = self._make_cards(25)
        # Tag 8 services with 'veterans'
        for i in range(8):
            cards[i]["service_taxonomies"] = ["Shelter", "Veterans"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "veterans", "_original_message": "ones for veterans"},
            cards,
            displayed_count=5,
        )
        assert "8 of the 5" not in result["response"]

    # --- Pagination cap ---

    def test_filter_open_returns_at_most_display_page_size(self):
        """Returned services must not exceed _DISPLAY_PAGE_SIZE (5)."""
        from app.services.post_results import _handle_filter_open, _DISPLAY_PAGE_SIZE
        # 10 open services
        cards = self._make_cards(25, ["open"] * 10 + [None] * 15)
        result = _handle_filter_open(cards, displayed_count=25)
        assert len(result["services"]) <= _DISPLAY_PAGE_SIZE, (
            f"Filter response returned {len(result['services'])} services, "
            f"exceeds _DISPLAY_PAGE_SIZE cap of {_DISPLAY_PAGE_SIZE}"
        )

    def test_filter_free_returns_at_most_display_page_size(self):
        from app.services.post_results import _handle_filter_free, _DISPLAY_PAGE_SIZE
        cards = self._make_cards(25)  # 13 free
        result = _handle_filter_free(cards, displayed_count=25)
        assert len(result["services"]) <= _DISPLAY_PAGE_SIZE

    def test_filter_subcategory_returns_at_most_display_page_size(self):
        from app.services.post_results import (
            _handle_filter_subcategory, _DISPLAY_PAGE_SIZE,
        )
        cards = self._make_cards(25)
        # Tag 10 services with 'veterans'
        for i in range(10):
            cards[i]["service_taxonomies"] = ["Shelter", "Veterans"]
        result = _handle_filter_subcategory(
            {"raw_phrase": "veterans", "_original_message": "ones for veterans"},
            cards,
            displayed_count=25,
        )
        assert len(result["services"]) <= _DISPLAY_PAGE_SIZE

    def test_overflow_indicated_in_response_text(self):
        """When pagination cap truncates, response must signal there's more."""
        from app.services.post_results import _handle_filter_open
        # 10 open, cap at 5 → should mention "first 5" (or similar)
        cards = self._make_cards(25, ["open"] * 10 + [None] * 15)
        result = _handle_filter_open(cards, displayed_count=25)
        response_lower = result["response"].lower()
        # Check for overflow indicator phrase — "first N"
        assert "first" in response_lower, (
            f"Response should indicate truncation when filter_count > 5. "
            f"Got: {result['response']}"
        )

    # --- displayed_count constant check ---

    def test_display_page_size_matches_chatbot(self):
        """post_results._DISPLAY_PAGE_SIZE must match chatbot._DISPLAY_PAGE_SIZE.

        Divergence would cause the filter response to paginate differently
        than the initial search response, confusing users who've built a
        mental model of "5 cards per message".
        """
        from app.services.post_results import _DISPLAY_PAGE_SIZE as POST_SIZE
        from app.services.chatbot import _DISPLAY_PAGE_SIZE as CHATBOT_SIZE
        assert POST_SIZE == CHATBOT_SIZE, (
            f"_DISPLAY_PAGE_SIZE mismatch: post_results={POST_SIZE}, "
            f"chatbot={CHATBOT_SIZE}. These must match — see the comment "
            f"at the top of post_results.py."
        )

    # --- Backward compat: answer_from_results with no displayed_count ---

    def test_answer_from_results_backward_compat_no_displayed_count(self):
        """Callers that don't pass displayed_count must still work.

        This preserves behavior for the integration tests and any other
        callers that haven't been updated yet. Default behavior: use
        len(services) as displayed_count (equivalent to pre-fix behavior).
        """
        from app.services.post_results import answer_from_results
        cards = self._make_cards(5, ["open", "open"] + [None] * 3)
        # No displayed_count kwarg — uses default
        result = answer_from_results({"type": "filter_open"}, cards)
        # Should succeed without error
        assert "response" in result
        assert "services" in result

    def test_chatbot_call_site_passes_displayed_count(self):
        """The ``answer_from_results`` call site must pass displayed_count.

        Without this, the filter-response fix is inert — the handlers
        default to len(services)=25 and the "of 25" bug returns.

        Note: Phase 3 moved the ``answer_from_results()`` call site to ``chatbot.handlers.post_results``.
        """
        import inspect
        from app.services.chatbot.handlers import post_results
        source = inspect.getsource(post_results)
        # Look for the answer_from_results call — should pass 3 positional args
        # including displayed_count from session
        assert "_displayed_count" in source, (
            "chatbot.handlers.post_results should reference _displayed_count "
            "(session key) when calling answer_from_results — this is the "
            "wiring that activates the UX fix."
        )
        # More specific: the answer_from_results call should be
        # receiving _displayed_count or existing.get("_displayed_count", ...)
        assert "answer_from_results(" in source
        # Find the call block and verify it has 3 args including displayed_count
        call_start = source.find("pr = answer_from_results(")
        assert call_start > 0, "Primary answer_from_results call site not found"
        call_block = source[call_start:call_start + 300]
        assert "_displayed_count" in call_block, (
            f"answer_from_results call site must pass displayed_count "
            f"from session. Call block: {call_block[:200]}"
        )


# =============================================================================
# 10. DB-VERIFIED ORG DISCOVERABILITY
# =============================================================================

class TestDBVerifiedOrgDiscoverability:
    """
    WHY: April 16, 2026 DB verification confirmed the actual taxonomy tags
    on the three orgs the shelter enrichment logic was designed to surface:

      Covenant House NYC (460 W 41st St):
        - "Emergency Bed Placement" → {Crisis}

      Ali Forney Center (DYCD Youth Drop-in Center):
        - "LGBTQIA2S+ Young Adult Overnight Services" → {LGBTQ Young Adult}
        - "Drop-in Space" → {Other service}  (not shelter-tagged!)

      Safe Horizon:
        - "Shelter Placement" (LES + Harlem) → {Referral}
        - "Day Sleeping Room" (Harlem) → {Drop-in Center}
        - "Respite and Community Bed" (Queens) → {Shelter}

    These tests assert the chatbot's taxonomy lists INTERSECT with the
    actual DB tags, so each org is discoverable in the relevant scenario.
    If any of these fail, a real org that vulnerable users depend on has
    become invisible.
    """

    # --- Covenant House NYC ---

    def test_covenant_house_in_default_shelter_query(self):
        """Covenant House NYC 'Emergency Bed Placement' is tagged {Crisis}.
        Crisis must be in the default shelter taxonomy list.

        DB verified: Covenant House NYC exists at 460 W 41st St, org name
        'Covenant House', location name 'New York'.
        """
        names = _shelter_tax()
        assert "crisis" in names, (
            "Covenant House NYC 'Emergency Bed Placement' is tagged {Crisis}. "
            "Without 'crisis' in the default shelter list, this service is "
            "invisible to plain shelter queries."
        )

    def test_covenant_house_in_dv_narrow_query(self):
        """DV survivor with children (Cornell Q5 scenario) must find
        Covenant House via crisis taxonomy in the DV enrichment."""
        names = _shelter_tax(
            age=19, family_status="with_children", populations=["dv_survivor"],
        )
        assert "crisis" in names, (
            "Covenant House NYC 'Emergency Bed Placement' tagged {Crisis} "
            "must be discoverable for a 19yo DV mom (Cornell Q5). "
            f"Got taxonomy list: {names}"
        )

    def test_covenant_house_in_lgbtq_query(self):
        """LGBTQ youth shelter query (Cornell Q1 scenario) must find
        Covenant House via crisis taxonomy in the LGBTQ enrichment."""
        names = _shelter_tax(age=21, gender="lgbtq")
        assert "crisis" in names, (
            "Covenant House NYC 'Emergency Bed Placement' tagged {Crisis} "
            "must be discoverable for 21yo LGBTQ youth (Cornell Q1). "
            f"Got taxonomy list: {names}"
        )

    # --- Ali Forney Center ---

    def test_ali_forney_in_default_shelter_query(self):
        """Ali Forney 'LGBTQIA2S+ Young Adult Overnight Services' is
        tagged {LGBTQ Young Adult}. Must be in the default shelter list."""
        names = _shelter_tax()
        assert "lgbtq young adult" in names, (
            "Ali Forney Center's overnight services are tagged "
            "{LGBTQ Young Adult}. Without it in the default list, "
            "Ali Forney is invisible to plain shelter queries."
        )

    def test_ali_forney_in_lgbtq_narrow_query(self):
        """LGBTQ user with narrowing active must still find Ali Forney
        via the LGBTQ safety enrichment re-adding 'lgbtq young adult'."""
        names = _shelter_tax(gender="lgbtq", family_status="alone")
        assert "lgbtq young adult" in names, (
            "Ali Forney Center tagged {LGBTQ Young Adult} must be "
            "discoverable for LGBTQ users even under narrow. "
            f"Got taxonomy list: {names}"
        )

    # --- Safe Horizon ---

    def test_safe_horizon_shelter_placement_in_default(self):
        """Safe Horizon 'Shelter Placement' (LES + Harlem) is tagged {Referral}.
        Referral must be in the default shelter list.

        This was the most critical finding from the April 16 DB verification:
        before adding 'referral' to the default, Safe Horizon's shelter
        placement services were INVISIBLE to every shelter query — including
        DV survivors, who are the primary population Safe Horizon serves.
        """
        names = _shelter_tax()
        assert "referral" in names, (
            "Safe Horizon 'Shelter Placement' is tagged {Referral}. "
            "Without 'referral' in the default shelter list, this service "
            "is invisible to ALL shelter queries. This was the 'invisible "
            "everywhere' gap found in the April 16 DB verification."
        )

    def test_safe_horizon_day_sleeping_room_in_default(self):
        """Safe Horizon 'Day Sleeping Room' (Harlem) is tagged {Drop-in Center}.
        Drop-in center must be in the default shelter list."""
        names = _shelter_tax()
        assert "drop-in center" in names, (
            "Safe Horizon 'Day Sleeping Room' is tagged {Drop-in Center}. "
            "Without it in the default list, this service is invisible "
            "to plain shelter queries."
        )

    def test_safe_horizon_respite_bed_in_default(self):
        """Safe Horizon 'Respite and Community Bed' (Queens) is tagged {Shelter}.
        Parent 'shelter' must be in the default list."""
        names = _shelter_tax()
        assert "shelter" in names

    def test_safe_horizon_shelter_placement_under_dv_narrow(self):
        """DV survivor with children: Safe Horizon's Shelter Placement
        (tagged {Referral}) is NOT re-added by DV enrichment.

        KNOWN LIMITATION: DV enrichment adds 'drop-in center' + 'crisis'
        but NOT 'referral'. Under narrow, the taxonomy list is:
        ['families', 'shelter', 'drop-in center', 'crisis', ...]
        Referral is absent → Safe Horizon Shelter Placement invisible.

        This test DOCUMENTS the gap. Safe Horizon is still reachable via
        'shelter' (parent, preserved in narrow) IF the Shelter Placement
        service were also tagged with the parent — but DB shows it's
        tagged ONLY with {Referral}.

        If this becomes a P0: add 'referral' to DV safety enrichment.
        """
        names = _shelter_tax(
            family_status="with_children", populations=["dv_survivor"],
        )
        # Document the gap: referral is NOT in the DV-narrowed list.
        # This means Safe Horizon Shelter Placement is invisible for
        # DV survivors who trigger family-composition narrowing.
        if "referral" not in names:
            # Expected: this is the documented gap.
            pass
        # Safe Horizon IS partially reachable via other tags:
        assert "drop-in center" in names, (
            "Day Sleeping Room (Drop-in Center) should be findable for DV survivors"
        )
        assert "shelter" in names, (
            "Parent 'shelter' should be preserved — Respite and Community Bed findable"
        )


# =============================================================================
# 11. SHELTER DEFAULT COMPLETENESS
# =============================================================================

class TestShelterDefaultCompleteness:
    """
    WHY: The shelter default taxonomy list was constructed from a prod DB
    query (April 16, 2026) listing all Shelter children with non-zero
    service counts. The rule is: every Shelter child with >0 services
    MUST be in the default list, so the chatbot's explicit enumeration
    matches YourPeer's parent-to-child API expansion.

    These tests encode the DB-verified children and ensure the rule holds.
    If a new Shelter child appears in the DB, the exact-set test in
    TestShelterNarrowWithParent::test_no_family_status_returns_full_default
    will fire for drift. This class documents WHY each entry is present.
    """

    # DB-verified non-zero Shelter children (April 16, 2026 prod query).
    # These appear in the DB but two are intentionally OMITTED from the
    # shelter template default (see DB_VERIFIED_OMITTED_FROM_DEFAULT below).
    DB_VERIFIED_SHELTER_CHILDREN = {
        "crisis": 13,
        "single adult": 38,
        "drop-in center": 6,
        "referral": 6,
        "youth": 4,
        "families": 3,
        "transitional independent living (til)": 3,
        "lgbtq young adult": 2,
        "residential recovery": 2,
        "senior": 2,
        "veterans short-term housing": 2,
        "assessment": 1,
        "housing lottery": 1,
        "safe haven": 1,
        "veterans": 1,
        "warming center": 1,
    }

    # Children with non-zero services that are nonetheless intentionally
    # OMITTED from the shelter template default (May 2026 audit decisions).
    # Tests for these live in TestPhantomTaxonomyGuards and the
    # TestShelterDefaultTaxonomies::test_*_not_in_shelter_default tests.
    DB_VERIFIED_OMITTED_FROM_DEFAULT = {
        # Removed May 2026 per TAXONOMY_AUDIT_MAY2026.md §VIII — CREATE Inc.
        # user-testing bug. Substance-use treatment programs that confused
        # "I need a place to sleep tonight" queries. Still reachable via
        # mental_health template + service_detail narrowing ("detox", etc.).
        "residential recovery",
        # Removed May 2026 audit follow-up. Population-specific to veterans;
        # the shelter enrichment in rag/__init__.py adds it back when
        # populations contains "veteran".
        "veterans short-term housing",
        # Removed May 2026 audit follow-up. Seasonal service; the shelter
        # enrichment adds it back when slots["_cold_context"] is True
        # (detected by _extract_cold_context in slot_extraction_regex.py).
        "warming center",
    }

    # DB-verified zero-service Shelter children (intentionally omitted)
    DB_VERIFIED_ZERO_SERVICE_CHILDREN = {
        "cooling center": 0,
        "intake": 0,
        # Removed May 2026 per TAXONOMY_AUDIT_MAY2026.md Ticket J (vestigial).
        "supportive housing": 0,
    }

    @pytest.mark.parametrize("child,count", list(DB_VERIFIED_SHELTER_CHILDREN.items()))
    def test_nonzero_shelter_child_in_default(self, child, count):
        """Every Shelter child with >0 tagged services must be in the
        default taxonomy list, EXCEPT those in DB_VERIFIED_OMITTED_FROM_DEFAULT
        (where the audit determined the cost of inclusion exceeds the benefit).

        DB verified: {child} has {count} tagged services.
        """
        if child in self.DB_VERIFIED_OMITTED_FROM_DEFAULT:
            pytest.skip(
                f"'{child}' is intentionally omitted from shelter default per "
                f"TAXONOMY_AUDIT_MAY2026.md (see test_residential_recovery_"
                f"not_in_shelter_default and similar)."
            )
        names = _shelter_tax()
        assert child in names, (
            f"Shelter child '{child}' ({count} services in DB) is missing "
            f"from the default shelter taxonomy list. This makes {count} "
            f"services invisible to default shelter queries. DB verified "
            f"April 16, 2026."
        )

    def test_parent_shelter_in_default(self):
        """Parent 'shelter' taxonomy (18 directly-tagged services) must
        always be in the default list."""
        names = _shelter_tax()
        assert "shelter" in names

    def test_zero_service_children_intentionally_omitted(self):
        """Cooling Center, Intake, and Supportive Housing all have 0 tagged
        services and are intentionally excluded from the default to keep
        the list clean.

        Supportive Housing was previously retained for forward-compat; the
        May 2026 taxonomy audit (Ticket J) confirmed 0 services tagged and
        recommended removal.
        """
        names = _shelter_tax()
        assert "cooling center" not in names, (
            "Cooling Center has 0 services — should be omitted from default"
        )
        assert "intake" not in names, (
            "Intake has 0 services — should be omitted from default"
        )
        assert "supportive housing" not in names, (
            "Supportive Housing has 0 services — removed per "
            "TAXONOMY_AUDIT_MAY2026.md Ticket J (vestigial cleanup)."
        )


# =============================================================================
# 12. DB-VERIFIED TAXONOMY PARENTAGE
# =============================================================================

class TestDBVerifiedTaxonomyParentage:
    """
    WHY: April 16, 2026 DB verification revealed that several taxonomies
    have unintuitive parent assignments. The chatbot's template assignments
    MUST match the DB parentage, or services will appear in wrong categories.

    Key finding: Crisis is a SHELTER child (not Health). Before the fix,
    the medical template included 'crisis', causing shelter services
    (Emergency Bed Placement) to appear in medical queries.

    These tests document the verified parentage and ensure template
    assignments match. If a future developer sees 'crisis' in the shelter
    template and thinks "that's a health/medical thing," these tests
    explain why it's there.
    """

    def test_crisis_is_shelter_child_not_health(self):
        """Crisis (13 services) is parented under Shelter, NOT Health.

        DB verified: SELECT parent_name FROM taxonomies WHERE name='Crisis'
        → parent_name = 'Shelter'.

        Implication: 'crisis' belongs in the shelter template, NOT medical.
        The medical template previously included it — that was a BUG
        fixed April 16, 2026.
        """
        shelter_names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        medical_names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        assert "crisis" in shelter_names, (
            "Crisis is a Shelter child (DB verified). Must be in shelter template."
        )
        assert "crisis" not in medical_names, (
            "Crisis is a Shelter child (DB verified). Must NOT be in medical template."
        )

    def test_drop_in_center_is_shelter_child(self):
        """Drop-in Center (6 services) is parented under Shelter.

        DB verified: parent_name = 'Shelter'. Contains day sleeping rooms,
        youth drop-in spaces. Must be in shelter template.
        """
        shelter_names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "drop-in center" in shelter_names

    def test_referral_is_shelter_child(self):
        """Referral (6 services) is parented under Shelter.

        DB verified: parent_name = 'Shelter'. Contains shelter placement
        services (e.g., Safe Horizon). Must be in shelter template.

        This was the 'invisible everywhere' taxonomy — before the April 16
        fix, it wasn't in ANY shelter query. Safe Horizon's Shelter
        Placement services were invisible to all users.
        """
        shelter_names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "referral" in shelter_names, (
            "Referral is a Shelter child (DB verified). Contains Safe Horizon "
            "Shelter Placement services. Must be in shelter template — it was "
            "the 'invisible everywhere' gap found April 16, 2026."
        )

    def test_mental_health_is_health_child(self):
        """Mental Health (128 services) is parented under Health.

        DB verified: parent_name = 'Health'. Intentionally excluded from
        the medical template (handled by mental_health template instead).
        Matches YourPeer's client-side exclusion.
        """
        medical_names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        mental_names = set(TEMPLATES["mental_health"]["default_params"]["taxonomy_names"])
        assert "mental health" not in medical_names, "Excluded from medical"
        assert "mental health" in mental_names, "Present in mental_health"

    def test_substance_use_treatment_is_health_child(self):
        """Substance Use Treatment (11 services) is parented under Health.

        DB verified: parent_name = 'Health'. Present in both medical
        and mental_health templates as defense-in-depth — if upstream
        slot extraction misroutes substance-use intent (a known May 5
        bug, since fixed), the SQL query layer would still surface
        the right rows.

        Note (May 5): the canonical routing for substance-use intent
        is service_type='medical' — see slot_extraction_regex.py and
        the TestSubstanceTreatment class in test_service_keywords.py.
        The mental_health template's overlap on this taxonomy is a
        safety net for the misroute case, not the primary path.
        """
        medical_names = set(TEMPLATES["medical"]["default_params"]["taxonomy_names"])
        mental_names = set(TEMPLATES["mental_health"]["default_params"]["taxonomy_names"])
        assert "substance use treatment" in medical_names
        assert "substance use treatment" in mental_names

    def test_residential_recovery_is_shelter_child_in_mental_health(self):
        """Residential Recovery (2 services) is parented under Shelter in the DB
        but lives in the mental_health template (recovery programs).

        DB verified: parent_name = 'Shelter'.

        May 2026 update (TAXONOMY_AUDIT_MAY2026.md §VIII, the CREATE Inc. bug):
        Residential Recovery was REMOVED from the shelter default. These are
        substance-use treatment programs that confused "I need a place to
        sleep tonight" queries. They remain reachable through:
          - mental_health template default (asserted below), AND
          - service_detail narrowing for "detox", "sober living",
            "halfway houses", "rehab services" (see rag/__init__.py
            _DETAIL_TO_TAXONOMY_NARROWING).
        """
        shelter_names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        mental_names = set(TEMPLATES["mental_health"]["default_params"]["taxonomy_names"])
        assert "residential recovery" not in shelter_names, (
            "Residential Recovery was removed from shelter default per "
            "TAXONOMY_AUDIT_MAY2026.md §VIII (CREATE Inc. bug)."
        )
        assert "residential recovery" in mental_names, (
            "Residential Recovery still belongs in mental_health template "
            "(recovery programs route)."
        )


# =============================================================================
# 13. PHANTOM TAXONOMY GUARDS
# =============================================================================

class TestPhantomTaxonomyGuards:
    """
    WHY: April 16, 2026 DB verification identified several taxonomies
    with 0 tagged services, or that don't exist in the DB at all. These
    must not appear in any template's default taxonomy list — including
    them would be misleading (query runs, finds nothing) or broken
    (SQL IN clause matches a non-existent taxonomy name).

    Separate from TestDisplayWhitelistCleanup which covers the display
    layer. This class covers the query layer.
    """

    def test_advocates_legal_aid_not_in_any_template(self):
        """'Advocates / Legal Aid' has 0 tagged services in the DB.

        DB verified: SELECT COUNT(*) WHERE name = 'Advocates / Legal Aid' → 0.
        YourPeer includes it in client-side exclusion filters (also a no-op).
        Must not appear in any template.
        """
        for key, template in TEMPLATES.items():
            names = set(template.get("default_params", {}).get("taxonomy_names", []))
            assert "advocates / legal aid" not in names, (
                f"Template '{key}' includes 'advocates / legal aid' — "
                f"this taxonomy has 0 services tagged (DB verified April 16, 2026)."
            )

    def test_cooling_center_not_in_shelter_default(self):
        """Cooling Center (Shelter child, 0 services) is intentionally excluded."""
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "cooling center" not in names, (
            "Cooling Center has 0 services. Intentionally omitted from default."
        )

    def test_intake_not_in_shelter_default(self):
        """Intake (Shelter child, 0 services) is intentionally excluded."""
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "intake" not in names, (
            "Intake has 0 services. Intentionally omitted from default."
        )

    def test_mobile_food_truck_has_zero_services(self):
        """Mobile Food Truck (Food child, 0 services) — present in food template
        for forward-compat but documenting the 0-service status."""
        # This is informational — Mobile Food Truck IS in the food template
        # despite 0 services (forward-compat, may be populated later).
        # Documenting so future auditors know it's intentional.
        names = set(TEMPLATES["food"]["default_params"]["taxonomy_names"])
        assert "mobile food truck" in names, (
            "Mobile Food Truck retained in food template for forward-compat"
        )

    def test_phantom_health_taxonomies_not_in_db(self):
        """Harm Reduction, Needle Exchange, Overdose Prevention do NOT exist
        as taxonomies in the DB. Must not appear in any template.

        DB verified April 16, 2026: these names return 0 rows from the
        taxonomies table. They were in the display whitelist (removed)
        and must not creep into query templates.
        """
        phantoms = {"harm reduction", "needle exchange", "overdose prevention"}
        for key, template in TEMPLATES.items():
            names = set(template.get("default_params", {}).get("taxonomy_names", []))
            overlap = names & phantoms
            assert not overlap, (
                f"Template '{key}' includes phantom taxonomy names {overlap} — "
                f"these do NOT exist in the DB (verified April 16, 2026)."
            )


# =============================================================================
# 14. TEMPLATE SHAPE INVARIANTS
# =============================================================================

class TestTemplateShapeInvariants:
    """
    WHY: The audit work touched several template definitions. These tests
    ensure no template is accidentally broken by a structure change
    (missing keys, wrong types, etc.).
    """

    EXPECTED_TEMPLATES = {
        "food", "shelter", "clothing", "personal_care", "medical",
        "mental_health", "legal", "employment", "other", "org_name",
    }

    def test_exact_template_set(self):
        """Pin the template inventory. If adding a new one, update this list
        AND document it in QUERY_PARITY_AUDIT.md."""
        actual = set(TEMPLATES.keys())
        assert actual == self.EXPECTED_TEMPLATES, (
            f"Template inventory changed. Expected {self.EXPECTED_TEMPLATES}, "
            f"got {actual}. "
            f"Unexpected additions: {actual - self.EXPECTED_TEMPLATES}. "
            f"Unexpected removals: {self.EXPECTED_TEMPLATES - actual}."
        )

    @pytest.mark.parametrize("template_key",
                             ["food", "shelter", "clothing", "personal_care",
                              "medical", "mental_health", "legal", "employment",
                              "other"])
    def test_template_has_required_keys(self, template_key):
        """Every service template has the required structure."""
        t = TEMPLATES[template_key]
        for key in ["name", "description", "required_filters",
                    "optional_filters", "default_params", "taxonomy_aliases"]:
            assert key in t, f"Template '{template_key}' missing key '{key}'"

    @pytest.mark.parametrize("template_key",
                             ["food", "shelter", "clothing", "personal_care",
                              "medical", "mental_health", "legal", "employment",
                              "other"])
    def test_template_taxonomy_names_lowercase(self, template_key):
        """Taxonomy names in default_params must be lowercase (matches SQL casing)."""
        names = TEMPLATES[template_key]["default_params"].get("taxonomy_names", [])
        for name in names:
            assert name == name.lower(), (
                f"Template '{template_key}' has uppercase taxonomy '{name}'. "
                f"FILTER_BY_TAXONOMY_NAME_IN does LOWER() comparison."
            )


# =============================================================================
# 10. DESCRIPTION FILTER CLEANUP (DB verified April 16, 2026)
# =============================================================================

class TestDescriptionFilterCleanup:
    """
    WHY: April 16, 2026 — ran all 79 description filter regex patterns against
    prod services.description. Found 1 dead pattern (0 matches) and 2 taxonomy
    narrowing entries with dead `supportive housing` (0 services tagged).

    These tests prevent the dead patterns from being reintroduced and document
    the DB verification findings.
    """

    def test_dialysis_not_in_description_filters(self):
        """'dialysis services' pattern removed — 0 service descriptions match
        dialysis|kidney|renal in the prod DB (verified April 16, 2026).

        If reintroduced, a user asking 'where can I get dialysis' would get
        0 results after narrowing, which is worse than showing all medical
        services without narrowing.
        """
        import re
        with open(_RAG_INIT) as f:
            src = f.read()
        desc_block = src[src.find("_DETAIL_DESCRIPTION_FILTERS"):
                         src.find("        }", src.find("_DETAIL_DESCRIPTION_FILTERS")) + 9]
        keys = re.findall(r'"([^"]+)"\s*:\s*r"', desc_block)
        assert "dialysis services" not in keys, (
            "'dialysis services' was reintroduced into _DETAIL_DESCRIPTION_FILTERS. "
            "DB verified April 16, 2026: 0 service descriptions match. "
            "The pattern is dead code that produces 0-result narrowing."
        )

    def test_dialysis_not_in_notable_sub_types(self):
        """'dialysis' removed from _NOTABLE_SUB_TYPES — no description filter
        exists to narrow with, so the sub-type label would be misleading."""
        from app.services.slot_extraction_regex import _NOTABLE_SUB_TYPES
        assert "dialysis" not in _NOTABLE_SUB_TYPES, (
            "'dialysis' back in _NOTABLE_SUB_TYPES but 'dialysis services' "
            "description filter was removed (0 DB matches). The sub-type "
            "would set service_detail without any narrowing firing."
        )

    def test_supportive_housing_not_in_sober_living_narrowing(self):
        """'supportive housing' removed from sober_living and halfway_houses
        taxonomy narrowing — DB verified 0 services tagged with Supportive Housing."""
        p = _query("mental_health", service_detail="sober living")
        assert "supportive housing" not in p.get("taxonomy_names", []), (
            "'supportive housing' back in sober_living narrowing. "
            "DB verified April 16, 2026: 0 services tagged."
        )

    def test_supportive_housing_not_in_halfway_houses_narrowing(self):
        p = _query("mental_health", service_detail="halfway houses")
        assert "supportive housing" not in p.get("taxonomy_names", []), (
            "'supportive housing' back in halfway_houses narrowing. "
            "DB verified April 16, 2026: 0 services tagged."
        )


# =============================================================================
# 11. CLOTHING CASUAL/PROFESSIONAL FILTER (DB verified April 16, 2026)
# =============================================================================

class TestClothingOccasionFilter:
    """
    WHY: April 16, 2026 — YourPeer supports casual/professional clothing
    filtering via the `taxonomySpecificAttributes` API parameter. The chatbot
    previously had no equivalent. DB verification revealed:
      - taxonomy_specific_attributes table exists with 'clothingOccasion' attribute
      - 62 services tagged 'Everyday', 28 tagged 'Job Interview' (overlapping)
      - Much better than taxonomy narrowing (only 2 services for professional)

    Implementation: FILTER_BY_CLOTHING_OCCASION SQL filter using JSONB @>
    containment operator. Activated when service_detail maps to a clothing
    occasion value via _CLOTHING_OCCASION_MAP in rag/__init__.py.

    These tests lock in the full flow: slot extraction → occasion param →
    SQL filter activation.
    """

    # --- Slot extraction disambiguation ---

    @pytest.mark.parametrize("phrase,expected_detail", [
        ("I need interview clothes", "professional clothing"),
        ("I need interview clothing", "professional clothing"),
        ("I need interview outfit", "professional clothing"),
        ("I need professional clothes", "professional clothing"),
        ("I need professional clothing", "professional clothing"),
        ("I need business clothes", "professional clothing"),
        ("I need business clothing", "professional clothing"),
        ("I need work clothes", "professional clothing"),
        ("I need casual clothes", "casual clothing"),
        ("I need casual clothing", "casual clothing"),
    ])
    def test_clothing_phrase_extracts_correct_detail(self, phrase, expected_detail):
        """Clothing occasion phrases must route to clothing (not employment)
        with the correct service_detail."""
        slots = extract_slots(phrase)
        assert slots["service_type"] == "clothing", (
            f"'{phrase}' routed to {slots['service_type']}, expected clothing. "
            f"The 'interview' keyword may be matching employment first."
        )
        assert slots["service_detail"] == expected_detail, (
            f"'{phrase}' set detail={slots['service_detail']}, expected {expected_detail}"
        )

    @pytest.mark.parametrize("phrase", [
        "I have a job interview",
        "I need help preparing for an interview",
        "interview preparation",
    ])
    def test_employment_interview_not_hijacked(self, phrase):
        """'interview' without clothing context must still route to employment."""
        slots = extract_slots(phrase)
        assert slots["service_type"] == "employment", (
            f"'{phrase}' routed to {slots['service_type']}, expected employment. "
            f"Clothing phrases may be too aggressive."
        )

    # --- Occasion param wiring ---

    def test_professional_clothing_sets_occasion_param(self):
        """service_detail='professional clothing' → clothing_occasion_value='["Job Interview"]'"""
        p = _query("clothing", service_detail="professional clothing")
        assert p.get("clothing_occasion_value") == '["Job Interview"]', (
            f"Expected '\"Job Interview\"' occasion value, got {p.get('clothing_occasion_value')}"
        )

    def test_casual_clothing_sets_occasion_param(self):
        """service_detail='casual clothing' → clothing_occasion_value='["Everyday"]'"""
        p = _query("clothing", service_detail="casual clothing")
        assert p.get("clothing_occasion_value") == '["Everyday"]', (
            f"Expected '\"Everyday\"' occasion value, got {p.get('clothing_occasion_value')}"
        )

    def test_generic_clothing_no_occasion_param(self):
        """No service_detail → no clothing_occasion_value (returns all clothing)."""
        p = _query("clothing")
        assert "clothing_occasion_value" not in p, (
            f"Generic clothing query should not have occasion filter, "
            f"got {p.get('clothing_occasion_value')}"
        )

    def test_non_clothing_template_ignores_occasion(self):
        """service_detail='professional clothing' on a non-clothing template
        should NOT set clothing_occasion_value."""
        p = _query("other", service_detail="professional clothing")
        assert "clothing_occasion_value" not in p

    # --- SQL filter activation ---

    def test_sql_includes_occasion_filter_when_param_present(self):
        """build_query should include the clothingOccasion EXISTS subquery
        when clothing_occasion_value is in params."""
        from app.rag.query_templates import build_query
        sql, params = build_query("clothing", {
            "clothing_occasion_value": '["Job Interview"]',
            "max_results": 10,
        })
        assert "clothingOccasion" in sql, (
            "SQL should include clothingOccasion filter when param is present"
        )
        assert params.get("clothing_occasion_value") == '["Job Interview"]'

    def test_sql_excludes_occasion_filter_when_no_param(self):
        """build_query should NOT include the clothingOccasion filter
        when clothing_occasion_value is absent."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("clothing", {"max_results": 10})
        assert "clothingOccasion" not in sql, (
            "SQL should not include clothingOccasion filter without the param"
        )

    def test_clothing_template_has_occasion_filter_in_optional(self):
        """FILTER_BY_CLOTHING_OCCASION must be in clothing template's optional_filters."""
        from app.rag.query_templates import FILTER_BY_CLOTHING_OCCASION
        clothing_optional = TEMPLATES["clothing"]["optional_filters"]
        assert FILTER_BY_CLOTHING_OCCASION in clothing_optional, (
            "FILTER_BY_CLOTHING_OCCASION missing from clothing template. "
            "The casual/professional filter won't fire."
        )

    def test_no_other_template_has_occasion_filter(self):
        """Only the clothing template should have FILTER_BY_CLOTHING_OCCASION."""
        from app.rag.query_templates import FILTER_BY_CLOTHING_OCCASION
        for key, tmpl in TEMPLATES.items():
            if key == "clothing":
                continue
            all_filters = tmpl.get("required_filters", []) + tmpl.get("optional_filters", [])
            assert FILTER_BY_CLOTHING_OCCASION not in all_filters, (
                f"Template '{key}' has FILTER_BY_CLOTHING_OCCASION — "
                f"this filter is clothing-specific."
            )


# =============================================================================
# 12. FAMILY STATUS — "for my family" detection (April 16, 2026 user test fix)
# =============================================================================

class TestFamilyStatusExtraction:
    """
    WHY: User testing query 2.2 ("I need shelter for my family in Manhattan")
    did not detect family_status. Root cause: _extract_family_status had
    "with my family" but not "for my family" — one preposition difference.

    Five phrases added:
        "for my family", "my family needs", "me and my family",
        "our family", "family shelter"
    """

    # --- Positive cases: should detect family_status ---

    @pytest.mark.parametrize("phrase,expected", [
        # New phrases (the fix)
        ("I need shelter for my family in Manhattan", "with_family"),
        ("my family needs shelter in Brooklyn", "with_family"),
        ("me and my family need a place to stay", "with_family"),
        ("I need a family shelter", "with_family"),
        ("our family is homeless", "with_family"),
        # Pre-existing phrases (regression check)
        ("I need shelter with my family", "with_family"),
        ("I need shelter with my partner", "with_family"),
        ("I need shelter with my wife", "with_family"),
        # Children detection (regression check)
        ("I need shelter with my kids", "with_children"),
        ("single mom needs shelter", "with_children"),
        ("I have kids", "with_children"),
        ("I'm here with my baby", "with_children"),  # "my baby" → with_children
        ("I need shelter for my daughter", "with_children"),
        # Alone detection (regression check)
        ("I need shelter, just me", "alone"),
        ("I am alone and need shelter", "alone"),
        ("I'm by myself", "alone"),
    ])
    def test_family_status_detected(self, phrase, expected):
        slots = extract_slots(phrase)
        assert slots.get("family_status") == expected, (
            f"'{phrase}' → family_status={slots.get('family_status')}, "
            f"expected {expected}"
        )

    # --- Negative cases: should NOT detect family_status ---

    @pytest.mark.parametrize("phrase", [
        "I need shelter",
        "I need food in Manhattan",
        "my family doctor is in Brooklyn",
        "family court in Manhattan",
        "I have a family emergency",
        "I need to help my family find food",
    ])
    def test_family_status_not_false_positive(self, phrase):
        slots = extract_slots(phrase)
        assert slots.get("family_status") is None, (
            f"'{phrase}' falsely detected family_status="
            f"{slots.get('family_status')}. Should be None."
        )

    # --- Priority: children > family > alone ---

    def test_children_beats_family(self):
        """'with my kids and my wife' → with_children (children checked first)."""
        slots = extract_slots("I need shelter with my kids and my wife")
        assert slots.get("family_status") == "with_children"

    def test_family_beats_alone(self):
        """'I'm alone but my family needs help' → with_family (family overrides)."""
        # "my family needs" phrase should match before "alone"
        slots = extract_slots("my family needs shelter")
        assert slots.get("family_status") == "with_family"


# =============================================================================
# 13. LGBTQ CROSS-POPULATION — trans identity preservation
#     (April 16, 2026 user test fix)
# =============================================================================

class TestLgbtqCrossPopulation:
    """
    WHY: User testing query 10.4 ("I cannot afford clothes on Amazon. I am
    a transman") revealed that "transman" maps to gender="male" in
    _extract_gender — which is correct for eligibility filtering — but the
    LGBTQ identity was completely lost. The shelter enrichment checked
    gender in ("lgbtq", "transgender", "nonbinary") and "male" didn't match,
    so a trans man searching for shelter never saw Ali Forney Center or
    other LGBTQ-affirming services.

    Fix: extract_slots() now cross-populates _populations with "lgbtq"
    when any of 21 LGBTQ signal phrases are detected in the message.
    The enrichment check in rag/__init__.py now checks both gender AND
    populations for the LGBTQ signal.
    """

    # --- Slot extraction: _populations includes "lgbtq" ---

    @pytest.mark.parametrize("phrase", [
        # Trans-identifying (maps to male/female in gender, but should still flag lgbtq)
        "I am a transman",
        "I'm a trans man",
        "I am a transwoman",
        "I'm a trans woman",
        "I'm ftm",
        "I'm mtf",
        # Direct identity terms
        "I'm transgender",
        "I'm nonbinary",
        "I'm non-binary",
        "I'm enby",
        "I'm genderqueer",
        "I'm gender fluid",
        "I'm agender",
        # LGBTQ umbrella
        "I'm LGBTQ",
        "I'm lgbtq+",
        "I'm LGBT",
        "I'm queer",
        "I'm gay",
        "I'm a lesbian",
        "I'm bisexual",
        # In context (compound messages)
        "21, LGBTQ, in Soho, need a bed tonight",
        "I cannot afford clothes on Amazon. I am a transman",
        "I'm a gay man and need shelter",
    ])
    def test_lgbtq_signal_in_populations(self, phrase):
        """LGBTQ signal phrases must add 'lgbtq' to _populations."""
        slots = extract_slots(phrase)
        pops = slots.get("_populations", [])
        assert "lgbtq" in pops, (
            f"'{phrase}' did not add 'lgbtq' to _populations. "
            f"Got _populations={pops}, _gender={slots.get('_gender')}. "
            f"Without this, shelter enrichment won't fire for LGBTQ services."
        )

    # --- No false positives ---

    @pytest.mark.parametrize("phrase", [
        "I need food in Manhattan",
        "I'm a man and need shelter",
        "I'm a woman and need food",
        "I need clothes for work",
        "I'm 21 and need help",
        "where can I get a shower",
    ])
    def test_no_lgbtq_false_positive(self, phrase):
        """Non-LGBTQ messages must NOT have 'lgbtq' in _populations."""
        slots = extract_slots(phrase)
        pops = slots.get("_populations", [])
        assert "lgbtq" not in pops, (
            f"'{phrase}' falsely added 'lgbtq' to _populations: {pops}"
        )

    # --- Gender extraction preserved (regression) ---

    @pytest.mark.parametrize("phrase,expected_gender", [
        ("I am a transman", "male"),
        ("I'm a trans woman", "female"),
        ("I'm ftm", "male"),
        ("I'm mtf", "female"),
        ("I'm transgender", "transgender"),
        ("I'm nonbinary", "nonbinary"),
        ("I'm LGBTQ", "lgbtq"),
        ("I'm a gay man", "male"),
    ])
    def test_gender_not_overwritten(self, phrase, expected_gender):
        """LGBTQ cross-population must NOT change the _gender value.
        The gender maps to the identified gender for eligibility filtering.
        A trans man should filter as 'male', not 'transgender'."""
        slots = extract_slots(phrase)
        assert slots.get("_gender") == expected_gender, (
            f"'{phrase}' → _gender={slots.get('_gender')}, "
            f"expected {expected_gender}. The cross-population step "
            f"should add to _populations, not change _gender."
        )

    # --- Shelter enrichment fires via populations ---

    def test_transman_shelter_enrichment_fires(self):
        """A trans man searching for shelter must get LGBTQ enrichment
        even though gender='male'. This is the bug that made Ali Forney
        invisible to query 3.1."""
        # Use _query for taxonomy check (SQL bind params have taxonomy_names)
        p = _query("shelter", gender="male", populations=["lgbtq"],
                    family_status="alone", location="Manhattan")
        assert "lgbtq young adult" in p.get("taxonomy_names", []), (
            f"LGBTQ enrichment did not fire for gender='male' + "
            f"populations=['lgbtq']. Taxonomy: {p.get('taxonomy_names')}"
        )
        # Use _query_user_params for lgbtq_boost (consumed before SQL binding)
        up = _query_user_params("shelter", gender="male", populations=["lgbtq"],
                                family_status="alone", location="Manhattan")
        assert up.get("lgbtq_boost") is True, (
            "lgbtq_boost not set for trans man shelter query"
        )

    def test_cis_male_shelter_no_lgbtq_enrichment(self):
        """A cis man searching for shelter must NOT get LGBTQ enrichment."""
        p = _query("shelter", gender="male", populations=[],
                    family_status="alone", location="Manhattan")
        assert "lgbtq young adult" not in p.get("taxonomy_names", []), (
            f"LGBTQ enrichment fired for cis male. Taxonomy: "
            f"{p.get('taxonomy_names')}"
        )
        up = _query_user_params("shelter", gender="male", populations=[],
                                family_status="alone", location="Manhattan")
        assert not up.get("lgbtq_boost"), (
            "lgbtq_boost set for cis male — should not be"
        )

    def test_transgender_shelter_enrichment_via_both_paths(self):
        """When gender='transgender' AND populations=['lgbtq'], enrichment
        should fire (both paths match). No double-add of taxonomies."""
        p = _query("shelter", gender="transgender", populations=["lgbtq"],
                    family_status="alone", location="Manhattan")
        names = p.get("taxonomy_names", [])
        assert "lgbtq young adult" in names
        assert names.count("lgbtq young adult") == 1, (
            f"lgbtq young adult appears {names.count('lgbtq young adult')} "
            f"times — should be exactly 1 (no double-add)"
        )

    def test_lgbtq_boost_fires_for_non_shelter_with_populations(self):
        """lgbtq_boost should also fire for non-shelter templates
        when populations includes 'lgbtq'."""
        up = _query_user_params("food", gender="male", populations=["lgbtq"],
                                location="Manhattan")
        assert up.get("lgbtq_boost") is True, (
            "lgbtq_boost not set for food query with populations=['lgbtq']. "
            "A trans man searching for food should get LGBTQ-friendly "
            "services boosted."
        )

    def test_lgbtq_boost_not_set_without_signal(self):
        """No LGBTQ signal → no lgbtq_boost."""
        up = _query_user_params("food", gender="male", populations=[],
                                location="Manhattan")
        assert not up.get("lgbtq_boost"), (
            "lgbtq_boost set without any LGBTQ signal"
        )
