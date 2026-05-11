"""
Tests for the May 2026 audit follow-up fixes:

  1. `_extract_cold_context` — detects cold-weather / warming-center context
     in the user message, gates the inclusion of `Warming Center` (1 svc) in
     shelter taxonomy queries. Previously Warming Center was always in the
     shelter default; per TAXONOMY_AUDIT_MAY2026.md §VIII it should only
     surface on a seasonal/cold signal.

  2. `cold_context` slot threading — `_cold_context` flows from extract_slots
     through merge_slots (sticky/monotonic) to query_services to the shelter
     enrichment.

  3. `Veterans Short-Term Housing` is conditional on veteran population —
     removed from shelter default, added by enrichment when populations
     contains "veteran". The existing enrichment already supported this;
     this test pins the removal-from-default behavior.

  4. `homeless prevention programs` description regex — tightened to drop
     the bare `prevention` alternation that matched unrelated services
     (STD prevention, fall prevention, overdose prevention, etc.).

  5. NYC winter-season auto-enable — `_extract_cold_context` returns True
     in November through March regardless of message content, aligning
     with NYC's DSS Code Blue activation window. Tests below use injected
     dates (or a frozen-datetime patch) to keep behavior deterministic
     across CI runs that span seasons.
"""

import datetime
import re
import pytest
from unittest.mock import patch

from app.services.slot_extraction_regex import (
    _extract_cold_context,
    _is_nyc_winter_month,
    extract_slots,
    merge_slots,
)
from app.rag.query_templates import TEMPLATES


# ----------------------------------------------------------------------------
# Date fixtures — pin behavior deterministically.
# ----------------------------------------------------------------------------
# The seasonal auto-enable makes the cold_context return value depend on
# *when* the test runs. Existing message-trigger tests need a known
# non-winter date to isolate the message path; existing "no signal"
# tests likewise need to be out of season. Winter-specific tests pin
# inside the Nov-Mar window.

WINTER_DATE = datetime.date(2026, 1, 15)  # mid-January, deep in season
SHOULDER_DATE_OCT = datetime.date(2026, 10, 15)  # October — just outside
SUMMER_DATE = datetime.date(2026, 7, 15)  # mid-summer, well outside


def _make_frozen_datetime(fixed_date):
    """Build a `datetime.datetime` subclass whose `.now(tz)` is pinned.

    Returns a class suitable for `unittest.mock.patch` against the
    `datetime` symbol imported inside `app.services.slot_extraction_regex`.
    Mirrors the `_FrozenDatetime` pattern used in `test_query_templates.py`
    for the schedule-status TZ tests — project convention is to patch
    the module's `datetime` symbol rather than depend on `freezegun`.
    """
    fixed = datetime.datetime(
        fixed_date.year, fixed_date.month, fixed_date.day,
        12, 0, 0,  # noon — well clear of any TZ-boundary edge case
        tzinfo=datetime.timezone.utc,
    )
    real_datetime = datetime.datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed.replace(tzinfo=None)
            return fixed.astimezone(tz)
    return _FrozenDatetime


def _query_taxonomies(service_type, **kwargs):
    """Run query_services with a mock DB and capture the taxonomy_names list."""
    from app.rag import query_services
    captured = {}
    def cap(sql, params):
        captured.update(dict(params))
        return []
    with patch("app.rag.query_executor._execute_sql", side_effect=cap):
        query_services(service_type=service_type, **kwargs)
    return captured.get("taxonomy_names", [])


# ============================================================================
# 1. _extract_cold_context — detection
# ============================================================================


class TestExtractColdContextPositive:
    """Phrases that SHOULD trigger cold_context=True — using SUMMER_DATE
    to isolate the message-trigger path from the seasonal auto-enable.
    The seasonal gate is tested separately in TestColdContextSeasonalGate.
    """

    @pytest.mark.parametrize("phrase", [
        # Explicit warming-center asks
        "I need a warming center",
        "is there a warming center near me",
        "warming centre nearby",
        "warming site",
        "i need a warm shelter",
        "warm place to sleep",
        "warm place to stay tonight",
        "I need to get out of the cold",
        "I want to escape the cold",
        # Cold-weather context
        "I'm freezing out here",
        "im freezing tonight",
        "i'm cold and need somewhere to go",
        "we're cold",
        "it's so cold",
        "it's really cold outside",
        "cold tonight, need help",
        "cold out there",
        "I need somewhere warm",
        "need to stay warm",
        "trying to get warm",
        "I need warmth",
        "frostbite is setting in",
    ])
    def test_cold_phrase_triggers(self, phrase):
        # SUMMER_DATE keeps the seasonal gate off so the message path
        # is what's exercised.
        assert _extract_cold_context(phrase, today=SUMMER_DATE) is True, (
            f"'{phrase}' should set cold_context=True via message path "
            f"(today=SUMMER_DATE isolates seasonal auto-enable)"
        )


class TestExtractColdContextNegative:
    """Phrases that should NOT trigger cold_context=True — using
    SUMMER_DATE so the seasonal auto-enable doesn't mask the negative.
    """

    @pytest.mark.parametrize("phrase", [
        # No cold language at all
        "I need shelter",
        "I'm hungry",
        "Where can I find a doctor",
        "I need a place to stay tonight",
        # The bare word "cold" without paired shelter/warmth context
        # is not enough — pizza, shoulder, drinks
        "I'd like a cold drink",
        "He gave me the cold shoulder",
        # "Warm" without shelter context
        "I need warm clothes",
        # Not cold-related at all
        "I'm overheating",
        "It's hot outside",
        # Empty / whitespace
        "",
        "   ",
    ])
    def test_non_cold_phrase_does_not_trigger(self, phrase):
        assert _extract_cold_context(phrase, today=SUMMER_DATE) is False, (
            f"'{phrase}' should NOT set cold_context (today=SUMMER_DATE; "
            f"if this fails in winter, the seasonal gate masked the message path)"
        )


# ============================================================================
# 2. extract_slots → cold_context slot
# ============================================================================


class TestColdContextSeasonalGate:
    """The seasonal auto-enable fires Nov–Mar regardless of message content.

    Aligns with NYC's DSS Code Blue activation window. Outside this range
    (April–October), only explicit message signals trigger cold_context.
    """

    @pytest.mark.parametrize("month", [11, 12, 1, 2, 3])
    def test_winter_month_triggers_without_message(self, month):
        """In NYC winter months, even a non-cold message returns True."""
        winter = datetime.date(2026, month, 15)
        assert _is_nyc_winter_month(winter) is True
        # Message with no cold language → still True because of season
        assert _extract_cold_context("I need shelter", today=winter) is True
        # Empty message → still True
        assert _extract_cold_context("", today=winter) is True

    @pytest.mark.parametrize("month", [4, 5, 6, 7, 8, 9, 10])
    def test_non_winter_month_does_not_auto_trigger(self, month):
        """In April–October, the seasonal gate is off; only message
        signals trigger cold_context."""
        non_winter = datetime.date(2026, month, 15)
        assert _is_nyc_winter_month(non_winter) is False
        # Message with no cold language → False
        assert _extract_cold_context("I need shelter", today=non_winter) is False
        # Explicit cold-context message → still True (the message path
        # works year-round so a summer cold snap / illness / AC failure
        # still surfaces warming centers)
        assert _extract_cold_context(
            "I'm freezing, need shelter", today=non_winter,
        ) is True

    def test_season_boundary_oct_31_vs_nov_1(self):
        """October 31 is outside the winter window; November 1 is inside."""
        assert _is_nyc_winter_month(datetime.date(2026, 10, 31)) is False
        assert _is_nyc_winter_month(datetime.date(2026, 11, 1)) is True

    def test_season_boundary_mar_31_vs_apr_1(self):
        """March 31 is the last day of season; April 1 is the first day out."""
        assert _is_nyc_winter_month(datetime.date(2026, 3, 31)) is True
        assert _is_nyc_winter_month(datetime.date(2026, 4, 1)) is False

    def test_winter_uses_nyc_timezone_not_process_local(self):
        """The seasonal gate evaluates `now` in America/New_York, not
        process-local time. On Render the container clock is UTC; a
        query at 11pm ET on Oct 31 (= 03:00 UTC Nov 1) must still
        evaluate as October (out of season).

        Strategy: pin `datetime.datetime` in slot_extraction_regex to
        a known UTC moment that maps to Oct 31 ET, then call
        `_is_nyc_winter_month()` with no `today` argument. If the
        function reads UTC, it sees Nov → True. If it correctly uses
        America/New_York, it sees Oct → False.
        """
        # 03:00 UTC on Nov 1 2026 = 11:00 PM ET on Oct 31 2026.
        utc_at_nov1_0300 = datetime.datetime(
            2026, 11, 1, 3, 0, 0, tzinfo=datetime.timezone.utc,
        )
        real_datetime = datetime.datetime

        class _PinnedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    # Process-local (UTC on Render) → Nov 1
                    return utc_at_nov1_0300.replace(tzinfo=None)
                return utc_at_nov1_0300.astimezone(tz)

        with patch(
            "app.services.slot_extraction_regex.datetime.datetime",
            _PinnedDatetime,
        ):
            # No `today` arg → function reads its own clock. The pinned
            # clock shows Nov 1 UTC = Oct 31 ET. Correct ET resolution
            # gives False (still October); incorrect UTC fallback would
            # give True (already November). Reaching False confirms the
            # function uses the NY timezone correctly.
            result = _is_nyc_winter_month()
            assert result is False, (
                "Seasonal gate must evaluate in America/New_York. The pinned "
                "moment is Oct 31 11pm ET (= Nov 1 03:00 UTC); reading UTC "
                "would incorrectly enable the winter gate one day early."
            )


class TestColdContextInSlots:
    """The slot extractor surfaces cold_context as `_cold_context`.

    Each test patches the date deterministically so the result doesn't
    depend on when CI runs.
    """

    def test_cold_context_present_in_slots_summer_with_signal(self):
        """A summer query with cold message language → True."""
        frozen = _make_frozen_datetime(SUMMER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            slots = extract_slots("I'm freezing, need shelter tonight")
            assert slots.get("_cold_context") is True

    def test_cold_context_absent_summer_no_signal(self):
        """A summer query with no cold message language → False."""
        frozen = _make_frozen_datetime(SUMMER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            slots = extract_slots("I need shelter in Brooklyn")
            assert slots.get("_cold_context") is False

    def test_cold_context_present_winter_no_signal(self):
        """A winter query with no cold message language → True (seasonal auto-enable)."""
        frozen = _make_frozen_datetime(WINTER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            slots = extract_slots("I need shelter in Brooklyn")
            assert slots.get("_cold_context") is True

    def test_cold_context_with_explicit_ask_summer(self):
        """Explicit warming-center ask works year-round."""
        frozen = _make_frozen_datetime(SUMMER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            slots = extract_slots("where can I find a warming center")
            assert slots.get("_cold_context") is True


# ============================================================================
# 3. merge_slots — _cold_context is monotonic (sticky)
# ============================================================================


class TestColdContextMonotonicMerge:
    """Once _cold_context=True, subsequent turns without cold language
    must not silently flip it back to False.

    Tests run in SUMMER_DATE so the seasonal gate doesn't mask the
    merge logic — we want to exercise the sticky-merge code path,
    which is only observable when the seasonal gate is off.
    """

    def test_cold_context_persists_across_turns_summer(self):
        # Frozen-summer for both turns so seasonal gate is off
        frozen = _make_frozen_datetime(SUMMER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            # Turn 1: user mentions freezing
            turn1 = extract_slots("I'm freezing, need shelter tonight")
            # Turn 2: user just says location, no cold language
            turn2 = extract_slots("in Brooklyn")
            assert turn2["_cold_context"] is False  # off in summer w/o message
            # But after merge, the session keeps cold_context=True from turn 1
            merged = merge_slots(turn1, turn2)
            assert merged["_cold_context"] is True, (
                "_cold_context must be monotonic — a turn without cold "
                "language should not flip a previously-set True back to False."
            )

    def test_cold_context_can_be_set_in_later_turn_summer(self):
        frozen = _make_frozen_datetime(SUMMER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            # Turn 1: no cold language
            turn1 = extract_slots("I need shelter in Manhattan")
            # Turn 2: cold disclosure appears
            turn2 = extract_slots("actually I'm freezing")
            merged = merge_slots(turn1, turn2)
            assert merged["_cold_context"] is True

    def test_cold_context_always_true_in_winter_regardless_of_message(self):
        """The seasonal auto-enable means every turn in winter is True.
        Sticky merge is effectively a no-op in winter — every fresh
        extraction is already True."""
        frozen = _make_frozen_datetime(WINTER_DATE)
        with patch(
            "app.services.slot_extraction_regex.datetime.datetime", frozen,
        ):
            turn1 = extract_slots("I need shelter")
            turn2 = extract_slots("in Brooklyn")
            assert turn1["_cold_context"] is True
            assert turn2["_cold_context"] is True
            assert merge_slots(turn1, turn2)["_cold_context"] is True


# ============================================================================
# 4. Shelter enrichment — Warming Center conditional inclusion
# ============================================================================


class TestWarmingCenterEnrichment:

    def test_warming_center_not_in_default(self):
        """Warming Center must not be in the shelter template default
        (May 2026 audit follow-up — was in default, now conditional)."""
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "warming center" not in names

    def test_warming_center_absent_without_cold_context(self):
        """A plain shelter query without cold_context should not include
        warming center."""
        names = _query_taxonomies("shelter", location="Brooklyn")
        assert "warming center" not in names

    def test_warming_center_added_when_cold_context_true(self):
        """When cold_context=True, the shelter enrichment adds warming center."""
        names = _query_taxonomies(
            "shelter", location="Brooklyn", cold_context=True,
        )
        assert "warming center" in names, (
            f"warming center missing with cold_context=True. Got: {names}"
        )

    def test_warming_center_added_under_family_status_narrowing(self):
        """Cold-context enrichment fires even when family_status narrows."""
        names = _query_taxonomies(
            "shelter", location="Brooklyn",
            family_status="alone", age=30, cold_context=True,
        )
        assert "warming center" in names

    def test_warming_center_does_not_duplicate(self):
        """When cold_context=True, warming center appears exactly once."""
        names = _query_taxonomies(
            "shelter", location="Brooklyn", cold_context=True,
        )
        assert names.count("warming center") == 1


# ============================================================================
# 5. Shelter enrichment — Veterans Short-Term Housing conditional
# ============================================================================


class TestVeteransShortTermHousingEnrichment:

    def test_vsth_not_in_default(self):
        """Veterans Short-Term Housing must not be in the shelter template
        default (May 2026 audit follow-up — was in default, now conditional
        on veteran population)."""
        names = set(TEMPLATES["shelter"]["default_params"]["taxonomy_names"])
        assert "veterans short-term housing" not in names

    def test_vsth_absent_without_veteran_population(self):
        """A plain shelter query without veteran population should not
        include veterans short-term housing."""
        names = _query_taxonomies("shelter", location="Brooklyn")
        assert "veterans short-term housing" not in names

    def test_vsth_added_when_veteran_population(self):
        """When populations contains 'veteran', the shelter enrichment
        adds both 'veterans' and 'veterans short-term housing'."""
        names = _query_taxonomies(
            "shelter", location="Brooklyn", populations=["veteran"],
        )
        assert "veterans" in names
        assert "veterans short-term housing" in names

    def test_vsth_added_under_family_status_narrowing_for_veteran(self):
        """Veteran enrichment fires even when family_status narrows.
        A veteran user with family_status=alone should still see VSTH."""
        names = _query_taxonomies(
            "shelter", location="Brooklyn",
            populations=["veteran"], family_status="alone", age=30,
        )
        assert "veterans short-term housing" in names

    def test_vsth_does_not_appear_for_non_veteran_populations(self):
        """Non-veteran populations should not trigger VSTH."""
        for pop in ("disabled", "reentry", "dv_survivor", "senior",
                    "foster_youth", "immigration", "pregnant"):
            names = _query_taxonomies(
                "shelter", location="Brooklyn", populations=[pop],
            )
            assert "veterans short-term housing" not in names, (
                f"populations=[{pop!r}] should not trigger VSTH. Got: {names}"
            )


# ============================================================================
# 6. homeless prevention programs — regex tightening
# ============================================================================


class TestHomelessPreventionRegex:
    """The 'homeless prevention programs' description filter must not
    match unrelated 'prevention' services (STD prevention, fall prevention,
    overdose prevention, etc.)."""

    def _get_pattern(self):
        from app.rag import _DETAIL_DESCRIPTION_FILTERS
        return _DETAIL_DESCRIPTION_FILTERS["homeless prevention programs"]

    def _matches(self, description: str) -> bool:
        """Test if the PG-style regex matches description text."""
        # The pattern uses PG-style alternations; convert to Python regex.
        # Our pattern has no \m / \M anchors, so direct re.search works.
        pattern = self._get_pattern()
        return bool(re.search(pattern, description, re.IGNORECASE))

    @pytest.mark.parametrize("description", [
        # Genuine homeless-prevention service descriptions
        "Homeless prevention program serving NYC families.",
        "Homeless prevention assistance and case management.",
        "Housing prevention services for families at risk of eviction.",
        "Shelter diversion services help families stay housed.",
        "Diversion services connect callers to alternatives to shelter intake.",
        "Prevention of homelessness through rental assistance.",
    ])
    def test_homeless_prevention_descriptions_match(self, description):
        assert self._matches(description), (
            f"Should match homeless-prevention text: {description!r}"
        )

    @pytest.mark.parametrize("description", [
        # Unrelated services with "prevention" — must NOT match
        "STD prevention and testing services available daily.",
        "STI prevention counseling and condom distribution.",
        "Fall prevention classes for seniors aged 65+.",
        "Overdose prevention training including narcan distribution.",
        "Suicide prevention crisis line.",
        "Violence prevention program for at-risk youth.",
        "HIV prevention through PrEP enrollment.",
        # Just "prevention" with no relevant context
        "We offer prevention services for community members.",
    ])
    def test_unrelated_prevention_descriptions_do_not_match(self, description):
        assert not self._matches(description), (
            f"Must NOT match unrelated 'prevention' text: {description!r}. "
            f"The pre-fix regex matched all of these via the bare "
            f"`prevention` alternation."
        )
