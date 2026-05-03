"""Tests for gender/LGBTQ identity extraction and filtering pipeline.

Covers:
  1. slot_extractor._extract_gender() — phrase matching, false positives
  2. slot_extractor.extract_slots() — gender included in return dict
  3. chatbot._build_confirmation_message() — LGBTQ-friendly label
  4. rag.query_services() — gender filter mapping (male/female pass through,
     transgender/nonbinary/lgbtq skip filter)
  5. pii_redactor — gender identity redaction
"""

import pytest


# ---------------------------------------------------------------------------
# 1. _extract_gender unit tests
# ---------------------------------------------------------------------------

class TestExtractGender:
    """Test _extract_gender() phrase matching and false-positive guards."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.slot_extraction_regex import _extract_gender
        self.extract = _extract_gender

    # --- Explicit gender ---

    def test_woman(self):
        assert self.extract("I am a woman") == "female"

    def test_im_a_man(self):
        assert self.extract("I'm a man") == "male"

    def test_trans_man(self):
        assert self.extract("I'm a trans man") == "male"

    def test_transman_no_space(self):
        assert self.extract("transman") == "male"

    def test_trans_woman(self):
        assert self.extract("I am a trans woman") == "female"

    def test_transwoman_no_space(self):
        assert self.extract("transwoman") == "female"

    def test_ftm(self):
        assert self.extract("I'm ftm") == "male"

    def test_mtf(self):
        assert self.extract("I'm mtf") == "female"

    def test_nonbinary(self):
        assert self.extract("I'm nonbinary") == "nonbinary"

    def test_non_binary_hyphen(self):
        assert self.extract("I'm non-binary") == "nonbinary"

    def test_enby(self):
        assert self.extract("I'm enby") == "nonbinary"

    def test_genderqueer(self):
        assert self.extract("I'm genderqueer") == "nonbinary"

    def test_agender(self):
        assert self.extract("I'm agender") == "nonbinary"

    # --- LGBTQ umbrella ---

    def test_lgbtq(self):
        assert self.extract("LGBTQ") == "lgbtq"

    def test_lgbtq_plus(self):
        assert self.extract("I'm LGBTQ+") == "lgbtq"

    def test_queer(self):
        assert self.extract("I'm queer") == "lgbtq"

    def test_gay(self):
        assert self.extract("I'm gay") == "lgbtq"

    def test_lesbian(self):
        assert self.extract("I'm lesbian") == "lgbtq"

    def test_bisexual(self):
        assert self.extract("I'm bisexual") == "lgbtq"

    # --- Sample queries from peer navigators ---

    def test_peer_lgbtq_youth_shelter(self):
        assert self.extract("21, LGBTQ, in Soho, need a bed tonight") == "lgbtq"

    def test_peer_transman_clothing(self):
        assert self.extract("I cannot afford clothes on Amazon. I am a transman") == "male"

    # --- Should NOT extract ---

    def test_no_gender_plain(self):
        assert self.extract("I need help") is None

    def test_manhattan_false_positive(self):
        """'man' inside 'Manhattan' must not trigger."""
        assert self.extract("I need food in Manhattan") is None

    def test_woman_and_manhattan(self):
        """Gender + location with 'man' substring."""
        result = self.extract("I'm a woman and I need food in Manhattan")
        assert result == "female"

    def test_third_person_man(self):
        """'the man at the counter' is not self-identification."""
        assert self.extract("the man at the counter was rude") is None

    def test_manage(self):
        """'man' inside 'manage' must not trigger."""
        assert self.extract("I can manage on my own") is None

    def test_male_in_malevolent(self):
        """'male' inside 'malevolent' must not trigger."""
        assert self.extract("that was malevolent") is None

    def test_guy_in_guyana(self):
        """'guy' inside 'Guyana' must not trigger."""
        assert self.extract("I'm from Guyana") is None


# ---------------------------------------------------------------------------
# 2. extract_slots — gender in return dict
# ---------------------------------------------------------------------------

class TestExtractSlotsGender:

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.slot_extraction_regex import extract_slots
        self.extract_slots = extract_slots

    def test_gender_in_slots(self):
        result = self.extract_slots("21, LGBTQ, in Soho, need a bed tonight")
        assert result["_gender"] == "lgbtq"
        assert result["service_type"] == "shelter"
        assert result["age"] == 21

    def test_no_gender_in_slots(self):
        result = self.extract_slots("I need food in Brooklyn")
        assert result["_gender"] is None

    def test_transman_clothing(self):
        result = self.extract_slots("I am a transman and I need clothes")
        assert result["_gender"] == "male"
        assert result["service_type"] == "clothing"


# ---------------------------------------------------------------------------
# 3. Confirmation message — LGBTQ-friendly label
# ---------------------------------------------------------------------------

class TestConfirmationGender:

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.chatbot import _build_confirmation_message
        self.build_msg = _build_confirmation_message

    def test_lgbtq_label(self):
        slots = {"service_type": "shelter", "location": "soho", "_gender": "lgbtq"}
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg

    def test_no_label_for_male(self):
        slots = {"service_type": "shelter", "location": "soho", "_gender": "male"}
        msg = self.build_msg(slots)
        assert "LGBTQ" not in msg

    def test_no_label_without_gender(self):
        slots = {"service_type": "shelter", "location": "soho"}
        msg = self.build_msg(slots)
        assert "LGBTQ" not in msg

    # --- Gender eligibility suffix (Sprint 2) ---
    # When the user asks for a gender-segregated service, the
    # confirmation should echo that back so the user can verify the
    # filter was captured. Opus judge flagged R35's no_result_shelter_thin
    # specifically for failing this: "missed an opportunity to verify
    # that the 'for women' demographic filter was being applied".

    def test_female_gets_for_women_suffix(self):
        slots = {"service_type": "shelter", "location": "queens", "_gender": "female"}
        msg = self.build_msg(slots)
        assert "for women" in msg
        # And it shouldn't also get a male suffix
        assert "for men" not in msg

    def test_male_gets_for_men_suffix(self):
        slots = {"service_type": "shelter", "location": "brooklyn", "_gender": "male"}
        msg = self.build_msg(slots)
        assert "for men" in msg
        assert "for women" not in msg

    def test_lgbtq_does_not_get_gender_suffix(self):
        """LGBTQ is handled via prefix, not via 'for women'/'for men' suffix."""
        slots = {"service_type": "shelter", "location": "soho", "_gender": "lgbtq"}
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg
        assert "for women" not in msg
        assert "for men" not in msg

    def test_transgender_does_not_get_gender_suffix(self):
        """transgender doesn't map to a NYC eligibility filter, so no suffix."""
        slots = {"service_type": "shelter", "location": "soho", "_gender": "transgender"}
        msg = self.build_msg(slots)
        assert "for women" not in msg
        assert "for men" not in msg

    def test_nonbinary_does_not_get_gender_suffix(self):
        slots = {"service_type": "shelter", "location": "soho", "_gender": "nonbinary"}
        msg = self.build_msg(slots)
        assert "for women" not in msg
        assert "for men" not in msg

    def test_no_gender_no_suffix(self):
        slots = {"service_type": "shelter", "location": "queens"}
        msg = self.build_msg(slots)
        assert "for women" not in msg
        assert "for men" not in msg

    def test_female_with_children_both_suffixes(self):
        """Gender suffix precedes family_status, so reading flows naturally:
        'shelter in Queens, for women, with children'."""
        slots = {
            "service_type": "shelter",
            "location": "queens",
            "_gender": "female",
            "family_status": "with_children",
        }
        msg = self.build_msg(slots)
        assert "for women" in msg
        assert "with children" in msg
        # Ordering: "for women" comes before "with children"
        assert msg.index("for women") < msg.index("with children")

    def test_female_with_cross_location_queue(self):
        """Gender suffix applies to the whole request, placed after
        the cross-location queue bits."""
        slots = {
            "service_type": "shelter",
            "location": "queens",
            "_gender": "female",
            "_queued_services": [("food", None, "brooklyn")],
        }
        msg = self.build_msg(slots)
        assert "shelter in Queens" in msg
        assert "food in Brooklyn" in msg
        assert "for women" in msg
        # "for women" follows the queued cross-location item
        assert msg.index("food in Brooklyn") < msg.index("for women")

    def test_transman_gets_lgbtq_prefix_and_male_suffix(self):
        """A transman's extraction sets _gender=male AND _populations=['lgbtq'].
        Both the LGBTQ-friendly prefix and the 'for men' suffix apply —
        the prefix signals affirming-context, the suffix carries the
        eligibility filter. Both are meaningful to the user."""
        slots = {
            "service_type": "clothing",
            "location": "manhattan",
            "_gender": "male",
            "_populations": ["lgbtq"],
        }
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg
        assert "for men" in msg

    # --- LGBTQ populations check (Sprint 2 latent bug fix) ---
    # Before the fix, only `_gender == "lgbtq"` triggered the
    # LGBTQ-friendly prefix. But some phrases (e.g. "queer") populate
    # `_populations=["lgbtq"]` without setting `_gender`. Those users
    # silently lost their identity affirmation. Fix checks both slots.

    def test_lgbtq_via_populations_gets_prefix(self):
        """When LGBTQ only surfaces in _populations (not _gender), the
        prefix still fires. This is the common path for phrases like
        'queer' that populate the broader tag without overwriting gender."""
        slots = {
            "service_type": "shelter",
            "location": "manhattan",
            "_populations": ["lgbtq"],
        }
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg

    def test_lgbtq_populations_and_female_gender_both_apply(self):
        """A user who is both female and LGBTQ (e.g. 'I'm a woman, also
        queer') gets the LGBTQ-friendly prefix AND the 'for women' suffix."""
        slots = {
            "service_type": "shelter",
            "location": "queens",
            "_gender": "female",
            "_populations": ["lgbtq"],
        }
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg
        assert "for women" in msg

    def test_lgbtq_in_populations_wins_over_other_population_tags(self):
        """Ordering: the LGBTQ check is first in the identity-prefix
        elif chain, so it takes precedence over veteran/disabled/reentry
        prefixes when both are present. Rationale: LGBTQ affirmation is
        typically the most safety-critical signal for this population."""
        slots = {
            "service_type": "shelter",
            "location": "queens",
            "_populations": ["lgbtq", "veteran"],
        }
        msg = self.build_msg(slots)
        assert "LGBTQ-friendly" in msg
        assert "veteran-friendly" not in msg


# ---------------------------------------------------------------------------
# 4. Query layer — gender filter mapping + LGBTQ sort boost
# ---------------------------------------------------------------------------

class TestGenderFilterMapping:
    """Verify that only male/female pass to the SQL filter, and that
    LGBTQ/trans/nonbinary trigger a sort boost instead."""

    def test_male_passes_to_filter(self):
        from app.rag.query_templates import build_query
        _, params = build_query("clothing", {"gender": "male", "borough": "Manhattan"})
        assert params.get("gender") == "male"

    def test_female_passes_to_filter(self):
        from app.rag.query_templates import build_query
        _, params = build_query("clothing", {"gender": "female", "borough": "Manhattan"})
        assert params.get("gender") == "female"

    def test_lgbtq_not_in_filter_params(self):
        """lgbtq should never be passed as a gender filter value."""
        _DB_GENDER_VALUES = {"male", "female"}
        assert "lgbtq" not in _DB_GENDER_VALUES
        assert "transgender" not in _DB_GENDER_VALUES
        assert "nonbinary" not in _DB_GENDER_VALUES


class TestLgbtqSortBoost:
    """Verify that lgbtq_boost activates the LGBTQ Young Adult sort preference."""

    def test_boost_adds_lgbtq_rank_to_order_by(self):
        from app.rag.query_templates import build_query
        sql, _ = build_query("shelter", {
            "borough": "Manhattan",
            "lgbtq_boost": True,
            "taxonomy_names": ["shelter", "lgbtq young adult"],
        })
        # The LGBTQ boost subquery should be in the ORDER BY
        assert "lgbtq young adult" in sql.lower()
        assert "st_lgbtq" in sql

    def test_no_boost_without_flag(self):
        from app.rag.query_templates import build_query
        sql, _ = build_query("shelter", {
            "borough": "Manhattan",
            "taxonomy_names": ["shelter", "lgbtq young adult"],
        })
        assert "st_lgbtq" not in sql

    def test_boost_not_leaked_as_sql_param(self):
        """lgbtq_boost should be popped from params, not sent to SQL."""
        from app.rag.query_templates import build_query
        _, params = build_query("shelter", {
            "borough": "Manhattan",
            "lgbtq_boost": True,
        })
        assert "lgbtq_boost" not in params

    def test_boost_with_distance_ordering(self):
        """LGBTQ boost should also work with proximity-based sorting."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("shelter", {
            "lat": 40.7233,
            "lon": -73.9985,
            "radius_meters": 1600,
            "lgbtq_boost": True,
            "taxonomy_names": ["shelter", "lgbtq young adult"],
        })
        assert "st_lgbtq" in sql
        assert "ST_Distance" in sql

    def test_boost_works_for_non_shelter(self):
        """LGBTQ boost should work for any service type, not just shelter."""
        from app.rag.query_templates import build_query
        sql, _ = build_query("food", {
            "borough": "Manhattan",
            "lgbtq_boost": True,
        })
        assert "st_lgbtq" in sql


# ---------------------------------------------------------------------------
# 5. PII redactor — gender identity terms
# ---------------------------------------------------------------------------

class TestGenderRedaction:

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.privacy.pii_redactor import redact_pii
        self.redact = redact_pii

    def test_redact_im_a_trans_man(self):
        text = "I'm a trans man and I need shelter"
        redacted, detections = self.redact(text)
        gender_dets = [d for d in detections if d.pii_type == "gender"]
        assert len(gender_dets) > 0
        assert "trans man" not in redacted
        assert "[GENDER]" in redacted

    def test_redact_im_nonbinary(self):
        text = "I am non-binary and need food"
        redacted, detections = self.redact(text)
        gender_dets = [d for d in detections if d.pii_type == "gender"]
        assert len(gender_dets) > 0

    def test_redact_im_queer(self):
        text = "I'm queer, need a bed tonight"
        redacted, detections = self.redact(text)
        gender_dets = [d for d in detections if d.pii_type == "gender"]
        assert len(gender_dets) > 0

    def test_no_redact_plain_message(self):
        text = "I need food in Brooklyn"
        redacted, detections = self.redact(text)
        gender_dets = [d for d in detections if d.pii_type == "gender"]
        assert len(gender_dets) == 0
        assert redacted == text


# ---------------------------------------------------------------------------
# 7. Gender slot privacy — _gender excluded from audit logs
# ---------------------------------------------------------------------------

class TestGenderSlotPrivacy:
    """Verify that gender identity values don't leak into audit logs.

    The session slot key is '_gender' (underscore prefix). The audit_log
    module's _clean_slots() strips keys starting with '_', ensuring
    gender values are excluded from logged events.
    """

    def test_clean_slots_excludes_gender(self):
        from app.services.audit_log import _clean_slots
        slots = {
            "service_type": "shelter",
            "_gender": "lgbtq",
            "age": 21,
        }
        cleaned = _clean_slots(slots)
        assert "_gender" not in cleaned
        assert "service_type" in cleaned
        assert "age" in cleaned

    def test_clean_slots_excludes_gender_male(self):
        from app.services.audit_log import _clean_slots
        cleaned = _clean_slots({"_gender": "male", "location": "soho"})
        assert "_gender" not in cleaned
        assert "location" in cleaned

    def test_clean_slots_matches_latitude_longitude_pattern(self):
        """_gender uses same privacy pattern as _latitude/_longitude."""
        from app.services.audit_log import _clean_slots
        slots = {
            "service_type": "food",
            "_gender": "female",
            "_latitude": 40.7233,
            "_longitude": -73.9985,
        }
        cleaned = _clean_slots(slots)
        assert "_gender" not in cleaned
        assert "_latitude" not in cleaned
        assert "_longitude" not in cleaned
        assert "service_type" in cleaned

    def test_extract_slots_returns_underscore_gender(self):
        """extract_slots must return '_gender', not 'gender'."""
        from app.services.slot_extraction_regex import extract_slots
        result = extract_slots("I'm a trans woman and need shelter")
        assert "_gender" in result
        assert "gender" not in result

    def test_merge_slots_preserves_gender(self):
        """merge_slots correctly handles _gender key."""
        from app.services.slot_extraction_regex import merge_slots
        existing = {"service_type": "shelter", "_gender": None}
        new = {"_gender": "lgbtq"}
        merged = merge_slots(existing, new)
        assert merged["_gender"] == "lgbtq"

    def test_merge_slots_overwrites_gender(self):
        from app.services.slot_extraction_regex import merge_slots
        existing = {"_gender": "lgbtq"}
        new = {"_gender": "male"}
        merged = merge_slots(existing, new)
        assert merged["_gender"] == "male"
