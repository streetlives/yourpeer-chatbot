# Portion of tests/unit/test_bot_knowledge.py added for the April 2026
# freshness-guard pass. Full file lives in the production tree.

# Imports that appear at top of the file (pytest, _get_*, TOPICS, etc.)
# are already present in test_bot_knowledge.py above this class.

class TestBotKnowledgeFreshness:
    """Drift guards — turn silent staleness into loud pytest failures.

    Added April 2026 after discovering bot_knowledge.py's ``language``
    topic still claimed "English only, multi-language planned" three
    months after partial Spanish support shipped. The module has a
    live-sourcing convention (helpers like ``_get_service_categories``
    pull from actual code), but topic ``answer`` and ``summary`` strings
    are hand-maintained and drift silently. These tests lock the
    hand-maintained strings against live code.

    **When a test in this class fails, the fix is to update
    bot_knowledge.py — not to loosen the assertion.** The whole point
    is to force the docstring / user-facing string to stay in sync.
    """

    def test_privacy_general_answer_mentions_every_pii_type(self):
        """``privacy_general`` is the user-facing explanation of what
        gets redacted. When a new PII type is added to
        ``pii_redactor._PLACEHOLDERS``, the answer must name it so users
        understand what's actually protected. Failure here means
        bot_knowledge.py was not updated alongside pii_redactor.py.
        """
        from app.privacy.pii_redactor import _PLACEHOLDERS
        from app.services.bot_knowledge import TOPICS

        answer = TOPICS["privacy_general"]["answer"].lower()

        # Map redactor placeholder keys → the user-facing phrasings the
        # answer should contain. Update both when a new PII type is
        # added in pii_redactor.py.
        friendly = {
            "phone": "phone",
            "ssn": "ssn",
            "email": "email",
            "address": "address",
            "name": "name",
            "dob": "date of birth",
            "credit_card": "credit card",
            "url": "url",
            "gender": "gender",
        }
        missing_from_mapping = set(_PLACEHOLDERS.keys()) - set(friendly)
        assert not missing_from_mapping, (
            f"_PLACEHOLDERS has new key(s) {missing_from_mapping} that this "
            f"test's friendly-name mapping doesn't know about. Add them "
            f"to both the mapping AND the privacy_general answer in "
            f"bot_knowledge.py."
        )
        missing_from_answer = [
            key for key in _PLACEHOLDERS
            if friendly[key] not in answer
        ]
        assert not missing_from_answer, (
            f"privacy_general answer omits PII type(s): {missing_from_answer}. "
            f"Update bot_knowledge.py TOPICS['privacy_general']['answer'] "
            f"to mention: "
            f"{[friendly[k] for k in missing_from_answer]}"
        )

    def test_privacy_general_summary_count_matches_placeholders(self):
        """Summary claim 'N PII types' must equal ``len(_PLACEHOLDERS)``."""
        import re
        from app.privacy.pii_redactor import _PLACEHOLDERS
        from app.services.bot_knowledge import TOPICS

        summary = TOPICS["privacy_general"]["summary"]
        match = re.search(r"(\d+)\s+PII\s+types", summary)
        assert match, (
            f"privacy_general summary must state the PII-type count as "
            f"'N PII types' so this test can verify it. Got: {summary!r}"
        )
        claimed = int(match.group(1))
        actual = len(_PLACEHOLDERS)
        assert claimed == actual, (
            f"privacy_general summary claims {claimed} PII types but "
            f"pii_redactor._PLACEHOLDERS has {actual}. "
            f"Update bot_knowledge.py TOPICS['privacy_general']['summary']."
        )

    def test_services_summary_count_matches_service_keywords(self):
        """Summary claim 'N service categories' must equal live count."""
        import re
        from app.services.slot_extraction_regex import SERVICE_KEYWORDS
        from app.services.bot_knowledge import TOPICS

        summary = TOPICS["services"]["summary"]
        match = re.search(r"(\d+)\s+service\s+categories", summary)
        assert match, (
            f"services summary must state 'N service categories' so this "
            f"test can verify it. Got: {summary!r}"
        )
        claimed = int(match.group(1))
        actual = len(SERVICE_KEYWORDS)
        assert claimed == actual, (
            f"services summary claims {claimed} categories but "
            f"SERVICE_KEYWORDS has {actual}. Update bot_knowledge.py "
            f"TOPICS['services']['summary']."
        )

    def test_capability_context_mentions_every_live_crisis_category(self):
        """``build_capability_context`` is the LLM's grounding prompt for
        bot_question answers. Missing crisis categories here means the
        LLM may tell users "I can detect X, Y, Z" while silently NOT
        listing a category that's actually live — e.g. youth_runaway.
        Failure means a new category in ``_CRISIS_CATEGORIES`` has no
        entry in ``_format_crisis_categories``'s friendly-name map.
        """
        from app.services.bot_knowledge import (
            _get_crisis_categories,
            build_capability_context,
        )

        ctx_lower = build_capability_context().lower()
        live_cats = _get_crisis_categories()
        assert live_cats, (
            "No crisis categories sourced — test self-check failed. "
            "_CRISIS_CATEGORIES may have changed shape."
        )

        # For each live category, some recognizable phrase should appear
        # in the context. Use substring markers that map to the friendly
        # names in _format_crisis_categories.
        markers = {
            "suicide_self_harm": "suicid",      # catches "suicidal"
            "domestic_violence": "domestic viol",
            "safety_concern": "unsafe",
            "trafficking": "trafficking",
            "medical_emergency": "medical emerg",
            "violence": "violence",
            "youth_runaway": "runaway",
            "assault_victim": "assault",
        }
        missing_from_map = set(live_cats) - set(markers)
        assert not missing_from_map, (
            f"_CRISIS_CATEGORIES has new entries {missing_from_map} with "
            f"no marker in this test. Add them to both the markers dict "
            f"above AND the _FRIENDLY map in bot_knowledge.py's "
            f"_format_crisis_categories()."
        )
        missing_from_context = [
            cat for cat in live_cats
            if markers[cat] not in ctx_lower
        ]
        assert not missing_from_context, (
            f"Capability context doesn't mention crisis categor"
            f"{'y' if len(missing_from_context) == 1 else 'ies'}: "
            f"{missing_from_context}. Check _FRIENDLY dict in "
            f"bot_knowledge.py._format_crisis_categories()."
        )

    def test_friendly_crisis_name_map_covers_live_categories(self):
        """The ``_FRIENDLY`` dict inside ``_format_crisis_categories`` must
        have an entry for every live crisis category. If it doesn't,
        the fallback (``name.replace("_", " ")``) will still produce
        output — but with machine-flavored names like
        "youth runaway" instead of curated copy. This test asserts the
        mapping stays complete.
        """
        import inspect
        from app.services import bot_knowledge
        from app.services.bot_knowledge import _get_crisis_categories

        # _FRIENDLY is local to _format_crisis_categories; pull the
        # source, find the dict literal, and parse it. Crude but
        # robust: the test can't import the dict directly (scoped to
        # the function), so it inspects the source.
        src = inspect.getsource(bot_knowledge._format_crisis_categories)
        assert "_FRIENDLY" in src, (
            "_format_crisis_categories no longer has a _FRIENDLY dict; "
            "adapt this test to whatever replaced it."
        )

        # Extract keys by regex (good enough — _FRIENDLY is a simple
        # literal inside the function).
        import re
        keys = set(re.findall(r'"([a-z_]+)"\s*:\s*"', src))
        live = set(_get_crisis_categories())
        missing = live - keys
        assert not missing, (
            f"_FRIENDLY dict in _format_crisis_categories is missing "
            f"entries for live crisis categor"
            f"{'y' if len(missing) == 1 else 'ies'}: {sorted(missing)}. "
            f"Add a user-facing phrase for each so the LLM prompt lists "
            f"them instead of falling back to snake_case → 'machine voice' "
            f"copy."
        )

    def test_no_english_only_claim_in_module(self):
        """Regression guard for the specific staleness that bit us.

        ``language`` topic + capability context previously claimed
        "English only" / "multi-language planned" for months AFTER
        partial Spanish support shipped. This test makes sure that
        exact claim never sneaks back in. If multi-language support is
        genuinely removed, update this test to match — but think hard
        about it first.
        """
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent.parent / (
            "backend/app/services/bot_knowledge.py"
        )
        src = path.read_text().lower()
        # "english only" is the red flag. Tolerates "english primary"
        # or "primarily in english" (accurate descriptions of partial
        # support), rejects the absolute claim.
        assert "english only" not in src, (
            "bot_knowledge.py contains the string 'English only'. This "
            "is the specific staleness that caused the April 2026 "
            "freshness audit — partial Spanish support has shipped. If "
            "Spanish support is genuinely removed, update this test."
        )
        assert "multi-language support is planned" not in src, (
            "bot_knowledge.py still says 'multi-language support is "
            "planned' — partial Spanish has shipped. Update the "
            "language topic + build_capability_context accordingly."
        )

    def test_no_source_refs_point_at_monolith_chatbot_py(self):
        """Regression guard for the Phase 3 decomposition.

        ``chatbot.py`` was split into the ``chatbot/`` package
        (orchestrator, execution, pipeline, handlers). ``source``
        fields in TOPICS pointing at ``chatbot.py`` are stale artifacts
        of the pre-decomposition era; they confuse new maintainers
        reading the module. A freshness test catches any new topic
        added with a copy-pasted stale source ref.
        """
        from app.services.bot_knowledge import TOPICS
        stale = [
            (tid, tdata["source"])
            for tid, tdata in TOPICS.items()
            if "chatbot.py" in tdata.get("source", "")
        ]
        assert not stale, (
            f"TOPICS with stale 'chatbot.py' source refs: {stale}. The "
            f"Phase 3 decomposition moved everything to chatbot/. Update "
            f"source fields to point at chatbot/<submodule>.py or "
            f"chatbot/handlers/<submodule>.py."
        )
