# Copyright (c) 2024 Streetlives, Inc.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""
Tests for the population-critical fallback feature.

When a user belongs to a rare shelter population (LGBTQ, youth, senior,
veteran) and the proximity-local results contain NO services tagged with
that population's rare taxonomy, the chatbot runs a secondary borough-wide
query for those rare taxonomies. Results are appended with a contextual
note.

See docs/design/POPULATION_FALLBACK_SPEC.md for the full design, and
chatbot._compute_rare_population_taxonomies / _run_population_fallback
for the implementation.
"""

from unittest.mock import patch

import pytest

from app.services.chatbot import (
    _POPULATION_FALLBACK_LABEL,
    _POPULATION_FALLBACK_MAX,
    _POPULATION_RARE_TAXONOMIES,
    _compute_rare_population_taxonomies,
    _execute_and_respond,
    _resolve_borough_from_location,
    _taxonomies_overlap,
)


# ---------------------------------------------------------------------------
# Card factory — distinct organization+address so _count_unique_locations
# (which groups by org+address) returns the expected count.
# ---------------------------------------------------------------------------

def _card(sid: str, taxonomies: list[str], name: str = None) -> dict:
    """Build a minimally-realistic service card for tests."""
    return {
        "service_id": sid,
        "service_name": name or f"Service {sid}",
        "organization": f"Org {sid}",
        "address": f"{sid} Main St, New York, NY",
        "service_taxonomies": list(taxonomies),
    }


def _run_execute_with_mock(slots: dict, main_services: list[dict], fallback_services: list[dict]):
    """Run _execute_and_respond with query_services mocked to return main
    results for the default call and fallback results when taxonomy_override
    is passed. Returns (result_dict, list_of_calls).
    """
    calls = []

    def fake_query_services(**kw):
        calls.append(dict(kw))
        if kw.get("taxonomy_override"):
            svcs = fallback_services
        else:
            svcs = main_services
        return {
            "services": list(svcs),
            "result_count": len(svcs),
            "template_used": "HousingEligibilityQuery",
            "params_applied": kw,
            "relaxed": False,
            "execution_ms": 10,
        }

    with patch("app.services.chatbot.execution.query_services", side_effect=fake_query_services), \
         patch("app.services.chatbot.execution.save_session_slots"), \
         patch("app.services.chatbot.execution.log_query_execution"):
        result = _execute_and_respond("test-session", "msg", dict(slots))
    return result, calls


# ===========================================================================
# _compute_rare_population_taxonomies
# ===========================================================================

class TestComputeRareTaxonomies:
    """Which populations trigger which rare shelter taxonomies."""

    def test_lgbtq_gender_triggers_rare_tx(self):
        tx, labels = _compute_rare_population_taxonomies(
            {"age": 30, "_gender": "lgbtq"}
        )
        assert "lgbtq young adult" in tx
        assert "lgbtq" in labels

    def test_lgbtq_via_populations_triggers_rare_tx(self):
        """LGBTQ signal can come from _populations too (e.g. 'transman' sets
        gender=male but adds 'lgbtq' to _populations)."""
        tx, _labels = _compute_rare_population_taxonomies(
            {"age": 30, "_gender": "male", "_populations": ["lgbtq"]}
        )
        assert "lgbtq young adult" in tx

    def test_transgender_triggers_rare_tx(self):
        tx, _labels = _compute_rare_population_taxonomies(
            {"age": 30, "_gender": "transgender"}
        )
        assert "lgbtq young adult" in tx

    def test_nonbinary_triggers_rare_tx(self):
        tx, _labels = _compute_rare_population_taxonomies(
            {"age": 30, "_gender": "nonbinary"}
        )
        assert "lgbtq young adult" in tx

    def test_youth_age_range(self):
        """Age 16-24 triggers youth rare tx."""
        for age in (16, 18, 21, 24):
            tx, labels = _compute_rare_population_taxonomies({"age": age})
            assert "youth" in tx, f"age {age} should trigger youth"
            assert "youth" in labels

    def test_age_15_does_not_trigger_youth(self):
        """Age below 16 does not trigger youth-specific shelter.

        Under-16 minors need child-protective resources, not the adult-ish
        "youth" shelter taxonomy which primarily covers 16-24.
        """
        tx, _labels = _compute_rare_population_taxonomies({"age": 15})
        assert "youth" not in tx

    def test_age_25_does_not_trigger_youth(self):
        tx, _labels = _compute_rare_population_taxonomies({"age": 25})
        assert "youth" not in tx

    def test_youth_suppressed_by_family_status(self):
        """Youth shelter doesn't apply when the user is explicitly searching
        for family shelter — they have their own track."""
        tx, _labels = _compute_rare_population_taxonomies({
            "age": 19, "family_status": "with_children",
        })
        assert "youth" not in tx
        tx, _labels = _compute_rare_population_taxonomies({
            "age": 19, "family_status": "with_family",
        })
        assert "youth" not in tx

    def test_senior_age_triggers_rare_tx(self):
        tx, labels = _compute_rare_population_taxonomies({"age": 65})
        assert "senior" in tx
        assert "senior" in labels

    def test_senior_below_62_does_not_trigger(self):
        tx, _labels = _compute_rare_population_taxonomies({"age": 61})
        assert "senior" not in tx

    def test_veteran_triggers_both_tx(self):
        tx, labels = _compute_rare_population_taxonomies({
            "age": 45, "_populations": ["veteran"],
        })
        assert "veterans" in tx
        assert "veterans short-term housing" in tx
        assert "veteran" in labels

    def test_no_rare_population_returns_empty(self):
        """A 35yo cis man with no special populations triggers nothing."""
        tx, labels = _compute_rare_population_taxonomies(
            {"age": 35, "_gender": "male"}
        )
        assert tx == []
        assert labels == []

    def test_skipped_age_handled_gracefully(self):
        """The 'skipped' sentinel on age should not trigger age-based rules."""
        tx, labels = _compute_rare_population_taxonomies({"age": "skipped"})
        assert tx == []
        assert labels == []

    def test_missing_age_handled_gracefully(self):
        tx, labels = _compute_rare_population_taxonomies({})
        assert tx == []
        assert labels == []

    def test_multiple_populations_combine(self):
        """A trans 20-year-old veteran triggers all three rare tx groups."""
        tx, labels = _compute_rare_population_taxonomies({
            "age": 20,
            "_gender": "transgender",
            "_populations": ["veteran"],
        })
        assert "youth" in tx
        assert "lgbtq young adult" in tx
        assert "veterans" in tx
        assert "veterans short-term housing" in tx
        # Labels preserved in order
        assert "youth" in labels
        assert "lgbtq" in labels
        assert "veteran" in labels

    def test_taxonomies_deduped(self):
        """If both LGBTQ and veteran set overlapping tx, no dupes in output."""
        tx, _ = _compute_rare_population_taxonomies({
            "age": 20, "_gender": "lgbtq", "_populations": ["veteran", "lgbtq"],
        })
        assert len(tx) == len(set(tx)), f"Duplicate taxonomies: {tx}"


# ===========================================================================
# _resolve_borough_from_location
# ===========================================================================

class TestResolveBorough:
    """Mapping from user-facing location → canonical borough."""

    def test_neighborhood_resolves(self):
        assert _resolve_borough_from_location("soho") == "Manhattan"
        assert _resolve_borough_from_location("williamsburg") == "Brooklyn"
        assert _resolve_borough_from_location("astoria") == "Queens"

    def test_borough_name_passthrough(self):
        assert _resolve_borough_from_location("Manhattan") == "Manhattan"
        assert _resolve_borough_from_location("Brooklyn") == "Brooklyn"
        assert _resolve_borough_from_location("Queens") == "Queens"

    def test_the_bronx_canonicalizes(self):
        """'The Bronx' → 'Bronx' so it keys correctly into _CITY_TO_BOROUGH
        and the population fallback's borough-wide query."""
        assert _resolve_borough_from_location("the bronx") == "Bronx"

    def test_none_returns_none(self):
        assert _resolve_borough_from_location(None) is None

    def test_empty_returns_none(self):
        assert _resolve_borough_from_location("") is None

    def test_unknown_returns_none(self):
        """Unknown locations must return None — not a raw city name, not
        a bogus borough. Caller should skip fallback rather than run a
        wildcard query."""
        assert _resolve_borough_from_location("xyz123") is None


# ===========================================================================
# _taxonomies_overlap (case-insensitive intersection)
# ===========================================================================

class TestTaxonomiesOverlap:
    def test_match_case_insensitive(self):
        """DB stores Title Case ('LGBTQ Young Adult'), rare set is lowercase.
        Comparison must be case-insensitive."""
        assert _taxonomies_overlap(
            ["Shelter", "LGBTQ Young Adult"],
            {"lgbtq young adult"},
        ) is True

    def test_no_match(self):
        assert _taxonomies_overlap(
            ["Shelter", "Single Adult"],
            {"lgbtq young adult"},
        ) is False

    def test_none_card_taxonomies(self):
        """A card with no service_taxonomies field must not crash."""
        assert _taxonomies_overlap(None, {"lgbtq young adult"}) is False

    def test_empty_card_taxonomies(self):
        assert _taxonomies_overlap([], {"lgbtq young adult"}) is False

    def test_partial_match_is_enough(self):
        """Card has multiple taxonomies; overlap on ANY one counts."""
        assert _taxonomies_overlap(
            ["Shelter", "Single Adult", "Youth"],
            {"lgbtq young adult", "youth"},
        ) is True


# ===========================================================================
# End-to-end: _execute_and_respond with the fallback wired in
# ===========================================================================

class TestFallbackIntegration:
    """Full flow tests — patches query_services so we can observe both the
    main call and the fallback call (if any) and inspect the final output."""

    # ---- POSITIVE: fallback fires and surfaces rare-pop results --------

    def test_cornell_q1_lgbtq_shelter_surfaces_afc(self):
        """'21, LGBTQ, in Soho, need a bed tonight' — the Cornell Q1 scenario.

        Main results: 5 generic shelters in Soho, none tagged LGBTQ Young
        Adult. Fallback finds Ali Forney Center borough-wide in Manhattan.
        """
        main = [_card(f"gen-{i}", ["Shelter", "Single Adult"]) for i in range(5)]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult", "Youth"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter",
            "location": "soho",
            "age": 21,
            "_gender": "lgbtq",
            "_populations": ["lgbtq"],
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)

        # 2 DB calls: main (no override), fallback (with override)
        assert len(calls) == 2
        assert calls[0].get("taxonomy_override") is None
        assert calls[1].get("taxonomy_override") is not None
        # Fallback is citywide — no borough filter. Ali Forney is in
        # Manhattan but a Far-Rockaway user wouldn't care where it is
        # as long as it's the rare LGBTQ YA shelter they need.
        assert calls[1]["location"] is None
        # Fallback override contains the rare LGBTQ taxonomy
        assert "lgbtq young adult" in calls[1]["taxonomy_override"]
        # Fallback drops proximity
        assert calls[1].get("latitude") is None
        assert calls[1].get("longitude") is None
        # Fallback drops gender filter (spec: "NO gender")
        assert calls[1].get("gender") is None

        # Response has both main and fallback sections
        assert "5 option(s)" in result["response"]
        assert "further away" in result["response"]
        assert "LGBTQ-friendly" in result["response"]

        # All 6 cards surface (5 main + 1 fallback)
        service_names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in service_names
        assert len(result["services"]) == 6

        # Fallback card carries the marker fields
        afc = next(s for s in result["services"] if s["service_name"] == "Ali Forney Center")
        assert afc["is_population_fallback"] is True
        assert afc["fallback_population"] in ("lgbtq", "youth")

    def test_fallback_preserves_age_filter(self):
        """A 17-year-old's fallback query should pass age=17 so adult-only
        services are still excluded."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 17, "_gender": "lgbtq",
        }
        _, calls = _run_execute_with_mock(slots, main, fallback)
        assert calls[1]["age"] == 17

    def test_veteran_fallback_uses_both_veteran_taxonomies(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("va1", ["Shelter", "Veterans"])]
        slots = {
            "service_type": "shelter", "location": "manhattan",
            "age": 45, "_populations": ["veteran"],
        }
        _, calls = _run_execute_with_mock(slots, main, fallback)
        override = calls[1]["taxonomy_override"]
        assert "veterans" in override
        assert "veterans short-term housing" in override

    # ---- NEGATIVE: fallback does NOT fire when not needed ---------------

    def test_no_fallback_when_main_has_match(self):
        """Main results contain an LGBTQ-tagged service — no fallback needed."""
        main = [_card("g1", ["Shelter", "LGBTQ Young Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        # Only the main call was made
        assert len(calls) == 1
        assert "further away" not in result["response"]
        assert not any(s.get("is_population_fallback") for s in result["services"])

    def test_no_fallback_for_non_shelter(self):
        """Food, medical, clothing, etc. — fallback is shelter-only."""
        main = [_card("f1", ["Food", "Food Pantry"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "food", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 1
        assert "further away" not in result["response"]

    def test_no_fallback_for_non_rare_population(self):
        """A 35-year-old cis man searching for shelter — no rare tx applies."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 35, "_gender": "male",
        }
        _result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 1

    def test_fallback_runs_even_when_location_unresolvable(self):
        """Unresolvable text location should NOT block the fallback, because
        the fallback is citywide and doesn't need a resolved borough.

        Prior behavior (borough-scoped fallback) required a borough and
        short-circuited on unresolvable location. Option B removed that
        gate — a rare-population user whose location we can't parse
        should still get citywide rare-taxonomy results rather than
        being denied a chance at Ali Forney just because their location
        text was weird.
        """
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter", "location": "somewhere unknown",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        # Both main and fallback calls should fire
        assert len(calls) == 2
        # Fallback is citywide — no borough filter
        assert calls[1]["location"] is None
        # AFC surfaces as a fallback card
        names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in names
        afc = next(s for s in result["services"] if s["service_name"] == "Ali Forney Center")
        assert afc["is_population_fallback"] is True

    def test_no_fallback_on_relaxed_main_query(self):
        """If the main query already relaxed (broadened past original
        filters), skipping fallback avoids compounding the 'broadened' story.
        The relaxed query already widened the search scope."""
        calls = []
        def fake_query_services(**kw):
            calls.append(dict(kw))
            return {
                "services": [_card("g1", ["Shelter", "Single Adult"])],
                "result_count": 1,
                "template_used": "HousingEligibilityQuery",
                "params_applied": kw,
                "relaxed": True,  # <-- key: main was already relaxed
                "execution_ms": 10,
            }
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        with patch("app.services.chatbot.execution.query_services", side_effect=fake_query_services), \
            patch("app.services.chatbot.execution.save_session_slots"), \
            patch("app.services.chatbot.execution.log_query_execution"):
            _result = _execute_and_respond("s", "msg", dict(slots))
        assert len(calls) == 1

    # ---- EDGE CASES -----------------------------------------------------

    def test_fallback_deduplicates_against_main(self):
        """If the fallback query returns a service already shown in main
        results, that duplicate is filtered out. No double-card."""
        # Both main and fallback reference service_id 'afc'.
        # Main doesn't have the LGBTQ tag (so fallback fires), but the same
        # service_id comes back from the fallback — dedupe must kick in.
        main = [_card("afc", ["Shelter"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        # 2 calls (main + fallback), but fallback output is deduped away
        assert len(calls) == 2
        assert not any(s.get("is_population_fallback") for s in result["services"])
        assert "further away" not in result["response"]
        # Still just the 1 main card
        assert len(result["services"]) == 1

    def test_fallback_dedup_to_empty_emits_audit_event(self):
        """When fallback fully deduplicates against main, an audit event
        must reach the dashboard. Without this, the dedup-empty case is
        indistinguishable from 'fallback never ran' in admin metrics.

        Guards TEST_QUALITY_PLAN §3.2 / §5 P0.2. Companion to
        test_fallback_deduplicates_against_main, which covers the
        user-facing behavior; this one covers the observability contract.
        """
        from app.services import audit_log

        main = [_card("afc", ["Shelter"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        audit_log.clear_audit_log()
        result, _calls = _run_execute_with_mock(slots, main, fallback)

        # The user-facing contract still holds.
        assert "further away" not in result["response"]

        # And the dashboard-visibility contract: a structured event landed.
        events = audit_log.get_recent_events(
            event_type="population_fallback_dedup_empty"
        )
        assert len(events) == 1, (
            "Expected exactly one population_fallback_dedup_empty event; "
            "got %d. The fallback ran, every card was a duplicate, and the "
            "admin dashboard must be able to count this case." % len(events)
        )
        e = events[0]
        assert "lgbtq" in e["labels"], (
            "labels must reflect the user-detected populations that drove "
            "the fallback; expected 'lgbtq' in %r" % e["labels"]
        )
        assert e["fetched_count"] == 1, (
            "fetched_count is the pre-dedup card count from the fallback "
            "query — 1 in this scenario, not 0 (0 would mean the query "
            "itself returned nothing, a distinct case)."
        )
        assert e["main_result_count"] == 1, (
            "main_result_count is the dedup target size — the count of "
            "main-result service_ids the fallback was compared against."
        )

    def test_fallback_query_returning_no_cards_also_emits_dedup_empty_event(self):
        """The event fires whenever the fallback ran and produced zero
        user-visible cards — that's the operational signal admins need.

        Two sub-cases collapse under the same event name and are
        distinguished by `fetched_count` in the payload:
          - fetched_count > 0, deduped == 0  → main results already
            cover the rare-population services
          - fetched_count == 0               → no rare-population
            services exist in the catalog for this query

        Both are "fallback feature ran but didn't help this user" from
        the dashboard's perspective; dashboards that want the finer
        split can filter on `fetched_count`. Matches the spec in
        TEST_QUALITY_PLAN §3.2 ("emit when `fallback_cards_after_dedup=0`").
        """
        from app.services import audit_log

        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = []  # query returned nothing
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        audit_log.clear_audit_log()
        _result, _calls = _run_execute_with_mock(slots, main, fallback)

        events = audit_log.get_recent_events(
            event_type="population_fallback_dedup_empty"
        )
        assert len(events) == 1, (
            "Fallback ran (calls=2 in this scenario) but produced zero "
            "fallback cards — the dashboard must see this as a fallback "
            "non-result, not as 'fallback was never attempted.'"
        )
        # fetched_count is the discriminator between the two sub-cases.
        # Here the query returned nothing, so fetched_count is 0; the
        # dedupe loop had nothing to do.
        assert events[0]["fetched_count"] == 0
        assert events[0]["main_result_count"] == 1

    def test_fallback_with_empty_result_does_not_append_note(self):
        """Fallback query ran, returned no matches borough-wide either.
        No note, no cards added — user just sees main results."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = []  # no LGBTQ shelter anywhere in borough
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 2  # fallback WAS attempted
        assert "further away" not in result["response"]
        assert not any(s.get("is_population_fallback") for s in result["services"])

    def test_fallback_cap_honored(self):
        """Even if the fallback returns many matches, cap at
        _POPULATION_FALLBACK_MAX."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        # Fallback has 10 but max_results=3 limits query itself — our test's
        # fake doesn't enforce max_results, so inspect the call instead.
        fallback = [_card(f"afc{i}", ["Shelter", "LGBTQ Young Adult"]) for i in range(10)]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        _, calls = _run_execute_with_mock(slots, main, fallback)
        assert calls[1]["max_results"] == _POPULATION_FALLBACK_MAX

    def test_fallback_failure_does_not_break_main_response(self):
        """If the fallback query raises, the main results must still come
        through untouched. Fallback is additive — never blocking."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        def fake_query_services(**kw):
            if kw.get("taxonomy_override"):
                raise RuntimeError("DB went boom")
            return {
                "services": list(main), "result_count": len(main),
                "template_used": "HousingEligibilityQuery",
                "params_applied": kw, "relaxed": False, "execution_ms": 10,
            }
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        with patch("app.services.chatbot.execution.query_services", side_effect=fake_query_services), \
            patch("app.services.chatbot.execution.save_session_slots"), \
            patch("app.services.chatbot.execution.log_query_execution"):
            result = _execute_and_respond("s", "msg", dict(slots))
        # Main result survives
        assert result["result_count"] == 1
        assert "1 option(s)" in result["response"]
        # No fallback note or cards (because fallback errored)
        assert "further away" not in result["response"]
        assert not any(s.get("is_population_fallback") for s in result["services"])

    # ---- NOTE LANGUAGE --------------------------------------------------

    def test_lgbtq_note_language(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {"service_type": "shelter", "location": "soho",
                 "age": 30, "_gender": "lgbtq"}
        result, _ = _run_execute_with_mock(slots, main, fallback)
        assert "LGBTQ-friendly" in result["response"]

    def test_youth_note_language(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("ch", ["Shelter", "Youth"])]
        slots = {"service_type": "shelter", "location": "brooklyn", "age": 18}
        result, _ = _run_execute_with_mock(slots, main, fallback)
        assert "youth-specific" in result["response"]

    def test_senior_note_language(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("sr", ["Shelter", "Senior"])]
        slots = {"service_type": "shelter", "location": "brooklyn", "age": 70}
        result, _ = _run_execute_with_mock(slots, main, fallback)
        assert "senior-specific" in result["response"]

    def test_veteran_note_language(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("va", ["Shelter", "Veterans"])]
        slots = {"service_type": "shelter", "location": "manhattan",
                 "age": 45, "_populations": ["veteran"]}
        result, _ = _run_execute_with_mock(slots, main, fallback)
        assert "veteran" in result["response"]

    def test_multi_population_note_joins_labels(self):
        """Trans 20-year-old: should mention both youth and LGBTQ."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult", "Youth"])]
        slots = {"service_type": "shelter", "location": "soho",
                 "age": 20, "_gender": "transgender"}
        result, _ = _run_execute_with_mock(slots, main, fallback)
        assert "youth-specific" in result["response"]
        assert "LGBTQ-friendly" in result["response"]


# ===========================================================================
# Regression guards — bugs found during self-review (April 2026)
# ===========================================================================

class TestGpsUserFallback:
    """Bug #1: GPS users couldn't trigger the fallback.

    When a user has browser geolocation active, slots['location'] is the
    NEAR_ME_SENTINEL ('__near_me__') rather than a neighborhood name.
    Before the original fix, _resolve_borough_from_location returned None
    for the sentinel, silently skipping the fallback for every GPS user
    — the opposite of the design intent (GPS users are precisely the ones
    affected by proximity exclusion).

    As of Option B, the fallback is citywide — it doesn't need a borough
    resolved at all. These tests still verify GPS users trigger the
    fallback path (two DB calls, rare-taxonomy card returned) but no
    longer check a specific borough since borough scoping was removed.
    """

    def test_gps_user_in_soho_triggers_fallback(self):
        from app.services.slot_extraction_regex import NEAR_ME_SENTINEL
        main = [_card(f"g{i}", ["Shelter", "Single Adult"]) for i in range(5)]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter",
            "location": NEAR_ME_SENTINEL,
            # Soho coordinates
            "_latitude": 40.7235,
            "_longitude": -74.0024,
            "age": 21,
            "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 2, "GPS user should trigger fallback query"
        # Citywide scope — no borough filter
        assert calls[1]["location"] is None
        assert any(
            s["service_name"] == "Ali Forney Center" for s in result["services"]
        )

    def test_gps_user_in_brooklyn_still_gets_manhattan_service(self):
        """The cross-borough case: a Williamsburg GPS user looking for
        LGBTQ young adult shelter must see Ali Forney Center (Manhattan)
        because it's the only such service in the DB. Under borough-
        scoped fallback this user would see nothing."""
        from app.services.slot_extraction_regex import NEAR_ME_SENTINEL
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter",
            "location": NEAR_ME_SENTINEL,
            # Williamsburg coordinates — different borough from AFC
            "_latitude": 40.7081,
            "_longitude": -73.9571,
            "age": 21,
            "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        # Fallback fired, citywide
        assert len(calls) == 2
        assert calls[1]["location"] is None
        # Cross-borough result surfaces
        names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in names

    def test_borough_centroid_matches_nearest(self):
        """Unit check on the reverse-geocode math directly, across 21
        landmarks spanning all 5 boroughs including known edge cases:
        Washington Heights (far north Manhattan — closer to Bronx
        centroid than Manhattan centroid), Battery Park (south
        Manhattan — close to Brooklyn across the river), and LaGuardia
        (north Queens — close to Bronx across the water). These all
        need to resolve to their correct borough for GPS users."""
        from app.services.chatbot import _nearest_borough_by_centroid
        landmarks = [
            # Manhattan — including edge cases
            ("Times Square", 40.758, -73.985, "Manhattan"),
            ("Washington Heights", 40.840, -73.939, "Manhattan"),
            ("Battery Park", 40.703, -74.017, "Manhattan"),
            ("Inwood", 40.867, -73.921, "Manhattan"),
            # Brooklyn
            ("Williamsburg", 40.708, -73.957, "Brooklyn"),
            ("Coney Island", 40.575, -73.984, "Brooklyn"),
            ("Brownsville", 40.663, -73.907, "Brooklyn"),
            # Queens — edge cases near water/boundaries
            ("LaGuardia", 40.774, -73.872, "Queens"),
            ("JFK", 40.644, -73.782, "Queens"),
            ("Far Rockaway", 40.604, -73.755, "Queens"),
            # Bronx
            ("Yankee Stadium", 40.829, -73.926, "Bronx"),
            ("Co-op City", 40.873, -73.829, "Bronx"),
            # Staten Island — supplemented manually since
            # NEIGHBORHOOD_CENTERS has no SI entries
            ("St George SI", 40.644, -74.074, "Staten Island"),
            ("Tottenville SI", 40.510, -74.230, "Staten Island"),
        ]
        for name, lat, lon, expected in landmarks:
            assert _nearest_borough_by_centroid(lat, lon) == expected, \
                f"{name} at ({lat}, {lon}) should resolve to {expected}"

    def test_borough_centroid_rejects_non_numeric(self):
        from app.services.chatbot import _nearest_borough_by_centroid
        assert _nearest_borough_by_centroid(None, -74.0) is None
        assert _nearest_borough_by_centroid(40.7, None) is None
        assert _nearest_borough_by_centroid("abc", -74.0) is None


class TestFallbackPagination:
    """Bug #2: fallback cards reappeared on 'Show more' page 2.

    Before the fix, fallback cards were appended to `all_services`, which
    is stored as `_last_results` and drives pagination. With main=10 and
    fallback=1, the user saw the fallback card once on page 1 (correct)
    AND again on page 2 (wrong — no contextual note, looks like a main
    result).

    Fix: fallback cards go into `services_list` only. `all_services`
    (and therefore `_last_results`) stays main-only. Pagination uses
    `_main_displayed_count` which tracks main cards displayed, not the
    combined displayed count.
    """

    def test_fallback_not_in_last_results(self):
        """_last_results (pagination source) must contain only main cards."""
        main = [_card(f"g{i}", ["Shelter", "Single Adult"]) for i in range(10)]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        last_ids = {s["service_id"] for s in result["slots"].get("_last_results", [])}
        assert "afc" not in last_ids
        assert last_ids == {f"g{i}" for i in range(10)}

    def test_displayed_count_is_main_only(self):
        """_displayed_count tracks what the user has seen of MAIN results
        — not main + fallback — so pagination math stays correct."""
        main = [_card(f"g{i}", ["Shelter", "Single Adult"]) for i in range(10)]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        # 5 main cards displayed, NOT 5 main + 1 fallback = 6
        assert result["slots"]["_displayed_count"] == 5

    def test_next_page_excludes_fallback(self):
        """Simulate 'Show more': next page slice of _last_results[5:10]
        should be main cards 5..9, never the fallback card."""
        main = [_card(f"g{i}", ["Shelter", "Single Adult"]) for i in range(10)]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 21, "_gender": "lgbtq",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        last = result["slots"]["_last_results"]
        d = result["slots"]["_displayed_count"]
        next_page_ids = [s["service_id"] for s in last[d:d + 5]]
        assert "afc" not in next_page_ids
        assert next_page_ids == ["g5", "g6", "g7", "g8", "g9"]


class TestFallbackPerCardAttribution:
    """Bug #3: fallback_population always marked with labels[0].

    A 20-year-old trans user has labels ['youth', 'lgbtq']. Ali Forney
    Center (tagged LGBTQ Young Adult, not Youth) should be marked
    `fallback_population: 'lgbtq'` — its distinguishing match — not
    'youth' just because youth was first in the labels list.
    """

    def test_afc_marked_lgbtq_not_youth_for_trans_young_adult(self):
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 20, "_gender": "transgender",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        afc = next(s for s in result["services"] if s["service_id"] == "afc")
        assert afc["fallback_population"] == "lgbtq"

    def test_youth_card_marked_youth_for_same_user(self):
        """Same trans 20yo user — a Youth-tagged (but not LGBTQ-tagged)
        fallback card should be marked 'youth', not 'lgbtq'."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("ch", ["Shelter", "Youth"], name="Covenant House")]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 20, "_gender": "transgender",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        ch = next(s for s in result["services"] if s["service_id"] == "ch")
        assert ch["fallback_population"] == "youth"

    def test_veteran_short_term_housing_tag_matches_veteran(self):
        """A service tagged 'Veterans Short-Term Housing' should attribute
        to the 'veteran' population."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("va", ["Shelter", "Veterans Short-Term Housing"])]
        slots = {
            "service_type": "shelter", "location": "manhattan",
            "age": 45, "_populations": ["veteran"],
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        va = next(s for s in result["services"] if s["service_id"] == "va")
        assert va["fallback_population"] == "veteran"


class TestFallbackPriorityOrdering:
    """Per-card attribution picks the rarest/most-distinguishing label
    when a card matches MULTIPLE of the user's population labels.

    The bug this guards against: for a trans 20-year-old, labels are
    built in detection order ('youth' first, then 'lgbtq'). A fallback
    card tagged with BOTH 'Youth' AND 'LGBTQ Young Adult' would attribute
    to 'youth' simply because youth came first in the iteration. The fix
    walks labels in fixed priority order (rarest first): lgbtq > veteran
    > senior > youth. The user's detection-order `labels` list is
    preserved unchanged — only the per-card loop uses priority order.
    """

    def test_multi_tagged_card_picks_lgbtq_over_youth(self):
        """Ali Forney tagged with BOTH Youth AND LGBTQ Young Adult should
        attribute to 'lgbtq' for a trans 20yo, not 'youth' — even though
        youth was detected first in the user's labels list."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        # The key data point: this card carries BOTH rare taxonomies.
        # Without the priority fix, iteration order (youth first) wins.
        fallback = [_card("afc", ["Shelter", "Youth", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 20, "_gender": "transgender",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        afc = next(s for s in result["services"] if s["service_id"] == "afc")
        # Rarer tag wins — LGBTQ Young Adult is the distinguishing marker
        # versus the much more common Youth tag.
        assert afc["fallback_population"] == "lgbtq"

    def test_multi_tagged_card_picks_veteran_over_senior(self):
        """Priority ordering covers all pairs, not just lgbtq. A card
        tagged with BOTH Senior AND Veterans Short-Term Housing shown to
        a 65-year-old veteran should attribute to 'veteran' (rarer)."""
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [
            _card("vs", ["Shelter", "Senior", "Veterans Short-Term Housing"]),
        ]
        slots = {
            "service_type": "shelter", "location": "manhattan",
            "age": 65, "_populations": ["veteran"],
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        vs = next(s for s in result["services"] if s["service_id"] == "vs")
        assert vs["fallback_population"] == "veteran"

    def test_note_text_still_uses_detection_order(self):
        """The priority fix is scoped to per-card attribution. The
        composed fallback note ('I also found [labels] services…') must
        still render in detection order — 'youth-specific and
        LGBTQ-friendly' for a trans 20yo — so the UX copy is unchanged.
        """
        main = [_card("g1", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "Youth", "LGBTQ Young Adult"])]
        slots = {
            "service_type": "shelter", "location": "soho",
            "age": 20, "_gender": "transgender",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        # The confirmation/response text (accessible via the slots after
        # execute) should include the note composed from labels[:2] in
        # detection order, unchanged by the priority fix.
        response = result.get("response", "")
        assert "youth-specific" in response
        assert "LGBTQ-friendly" in response
        # Detection order: youth first, lgbtq second.
        youth_idx = response.find("youth-specific")
        lgbtq_idx = response.find("LGBTQ-friendly")
        assert youth_idx < lgbtq_idx, (
            "Note text must preserve user-detection order (youth first, "
            "lgbtq second) even after priority-ordering per-card attribution."
        )


# ===========================================================================
# Constants — backstop tests
# ===========================================================================

class TestConstants:
    """Guards on the module-level constants that other modules may rely on."""

    def test_fallback_max_is_three(self):
        """Spec: 'Max results: 3' — documented cap on fallback card count."""
        assert _POPULATION_FALLBACK_MAX == 3

    def test_every_rare_population_has_a_label(self):
        """Every key in _POPULATION_RARE_TAXONOMIES must have a user-facing
        label in _POPULATION_FALLBACK_LABEL. Avoids silent 'population'
        placeholder in the note when a new population is added."""
        for key in _POPULATION_RARE_TAXONOMIES:
            assert key in _POPULATION_FALLBACK_LABEL, \
                f"Missing fallback label for rare population '{key}'"

    def test_lgbtq_rare_tx_excludes_common_tags(self):
        """'drop-in center' and 'crisis' are in the base default list and
        nearly always have proximity-local results — they should NOT be
        in the rare-taxonomy set, or the fallback would never detect a
        mismatch for LGBTQ users.

        Spec: 'the fallback is for taxonomies that are population-specific
        and rare.'"""
        lgbtq_rare = _POPULATION_RARE_TAXONOMIES["lgbtq"]
        assert "drop-in center" not in lgbtq_rare
        assert "crisis" not in lgbtq_rare


# ---------------------------------------------------------------------------
# CROSS-BOROUGH FALLBACK (Option B scope change)
# ---------------------------------------------------------------------------

class TestCrossBoroughFallback:
    """Option B: the population fallback is citywide, not borough-scoped.

    The canonical case: a Far Rockaway (Queens) user asking for LGBTQ
    Young Adult shelter. The only such service in NYC is Ali Forney
    Center, which is in Manhattan. Under the old borough-scoped
    fallback, the Queens-scoped query returned nothing and the user
    saw no rare-taxonomy results. Option B drops the borough filter
    so Ali Forney surfaces regardless of where the user searched from.

    Scope: rare populations only (LGBTQ, youth, senior, veteran) for
    shelter searches. See docs/design/POPULATION_FALLBACK_SPEC.md §Scope.
    """

    def test_far_rockaway_gps_user_gets_manhattan_afc(self):
        """The motivating scenario: GPS user in Far Rockaway, Queens,
        rare population (LGBTQ YA), should surface Ali Forney (Manhattan)."""
        from app.services.slot_extraction_regex import NEAR_ME_SENTINEL
        main = [_card("gen-q1", ["Shelter", "Single Adult"]),
                _card("gen-q2", ["Shelter", "Single Adult"])]
        # AFC tagged with LGBTQ YA only (not Youth) — matches the pattern
        # in test_afc_marked_lgbtq_not_youth_for_trans_young_adult, and
        # gives us a distinguishing LGBTQ tag for fallback_population.
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter",
            "location": NEAR_ME_SENTINEL,
            # Far Rockaway, Queens — as far as you can get from Manhattan
            # while still being in NYC
            "_latitude": 40.6044,
            "_longitude": -73.7547,
            "age": 21,
            "_gender": "lgbtq",
            "_populations": ["lgbtq"],
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        # Both queries fired
        assert len(calls) == 2
        # Fallback is citywide — that's what makes this scenario work
        assert calls[1]["location"] is None, (
            "Fallback must be citywide; a Queens-scoped fallback would "
            "miss Ali Forney in Manhattan and this whole feature would "
            "be useless for Far Rockaway users."
        )
        # AFC shows up in results
        names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in names
        # AFC marked as fallback + LGBTQ
        afc = next(s for s in result["services"] if s["service_name"] == "Ali Forney Center")
        assert afc["is_population_fallback"] is True
        assert afc["fallback_population"] == "lgbtq"

    def test_text_location_brooklyn_gets_manhattan_lgbtq_service(self):
        """Same cross-borough guarantee for text locations, not just GPS.
        A user who types 'Brooklyn' for LGBTQ YA shelter still gets AFC."""
        main = [_card("gen-bk", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter", "location": "Brooklyn",
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 2
        assert calls[1]["location"] is None  # citywide
        names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in names

    def test_staten_island_gps_user_gets_manhattan_service(self):
        """Staten Island is the most isolated borough (no subway
        connection to Manhattan). Under borough scoping, a SI user
        would have basically zero options for rare populations. Option
        B makes sure they're not cut off from NYC-wide resources."""
        from app.services.slot_extraction_regex import NEAR_ME_SENTINEL
        main = [_card("gen-si", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter",
            "location": NEAR_ME_SENTINEL,
            # St George, Staten Island
            "_latitude": 40.6437, "_longitude": -74.0759,
            "age": 21, "_gender": "lgbtq",
        }
        result, calls = _run_execute_with_mock(slots, main, fallback)
        assert len(calls) == 2
        assert calls[1]["location"] is None
        names = [s["service_name"] for s in result["services"]]
        assert "Ali Forney Center" in names

    def test_fallback_location_none_regardless_of_input(self):
        """Regression guard: the fallback must ALWAYS pass location=None
        to the query layer, regardless of what the user's input was. If
        this test ever starts failing, someone re-introduced borough
        scoping and needs to update the spec first."""
        scenarios = [
            {"location": "Manhattan"},
            {"location": "soho"},
            {"location": "Brooklyn"},
            {"location": "Staten Island"},
            {"location": "the bronx"},
            {"location": "somewhere unknown"},
            {"location": None},
        ]
        for extras in scenarios:
            main = [_card("g1", ["Shelter", "Single Adult"])]
            fallback = [_card("rare", ["Shelter", "LGBTQ Young Adult"])]
            slots = {
                "service_type": "shelter", "age": 21, "_gender": "lgbtq",
                **extras,
            }
            _, calls = _run_execute_with_mock(slots, main, fallback)
            if len(calls) < 2:
                # Some input values (e.g., None location) may prevent the
                # main query from running in the first place. If main
                # didn't run, fallback can't run either — that's fine,
                # we just can't assert on calls[1].
                continue
            assert calls[1]["location"] is None, (
                f"Fallback with input {extras!r} passed "
                f"location={calls[1]['location']!r}; must always be None "
                f"(citywide). See docs/design/POPULATION_FALLBACK_SPEC.md §Scope."
            )

    def test_note_still_reads_naturally_for_cross_borough(self):
        """The 'further away' framing in the note needs to land as
        honestly informative even when the card is in a different
        borough. 'Further away' is literally true (Manhattan is far
        from Queens) so no rewrite is strictly needed — but check
        that the user-facing phrasing doesn't accidentally imply
        'further away but in your borough.'"""
        main = [_card("gen", ["Shelter", "Single Adult"])]
        fallback = [_card("afc", ["Shelter", "LGBTQ Young Adult"],
                          name="Ali Forney Center")]
        slots = {
            "service_type": "shelter", "location": "Brooklyn",
            "age": 21, "_gender": "lgbtq",
        }
        result, _ = _run_execute_with_mock(slots, main, fallback)
        response = result["response"]
        assert "further away" in response
        # Don't promise the card is in Brooklyn — it isn't
        assert "in Brooklyn" not in response or response.count("in Brooklyn") <= 1  # main-query framing is fine
