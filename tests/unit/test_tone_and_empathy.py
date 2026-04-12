"""
Tests for tone overrides and empathetic routing.

Covers:
  - Sensitive context tone override (foster care, fleeing, reentry, DV)
  - Help + confused empathetic prefix
  - Escalation + yes acknowledgment (no repeat)

Run with: python -m pytest tests/unit/test_tone_and_empathy.py -v
"""

import pytest
from conftest import send_multi, MOCK_QUERY_RESULTS


# -----------------------------------------------------------------------
# SENSITIVE CONTEXT TONE OVERRIDE
# -----------------------------------------------------------------------

class TestSensitiveContextToneOverride:
    """Messages with sensitive life situations should get empathetic tone,
    never casual 'No worries'."""

    def test_foster_care_gets_empathy(self):
        r = send_multi(["I'm aging out of foster care and need help in the Bronx"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp
        assert "no worries" not in resp

    def test_just_got_out_of_jail_gets_empathy(self):
        r = send_multi(["just got out of jail, need shelter in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp
        assert "no worries" not in resp

    def test_domestic_violence_gets_empathy(self):
        """DV context should trigger empathetic tone."""
        r = send_multi(["domestic violence shelter in Manhattan"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp

    def test_fleeing_gets_empathy(self):
        r = send_multi(["fleeing and need somewhere to stay in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "difficult situation" in resp

    def test_normal_request_no_override(self):
        """Regular requests should NOT get the sensitive tone prefix."""
        r = send_multi(["I need food in Brooklyn"])
        resp = r[0]["response"].lower()
        assert "difficult situation" not in resp

    def test_override_when_tone_is_confused(self):
        """When tone=confused AND sensitive context, empathetic wins over casual."""
        r = send_multi(["just aged out of foster care, don't know what to do, Bronx"])
        resp = r[0]["response"].lower()
        assert "no worries" not in resp
        # Should have either 'difficult situation' or empathetic framing
        assert "difficult situation" in resp or "help" in resp


# -----------------------------------------------------------------------
# HELP + CONFUSED EMPATHETIC PREFIX
# -----------------------------------------------------------------------

class TestHelpConfusedEmpathy:
    """When user is confused/emotional AND asking for help, lead with empathy."""

    def test_dont_know_where_to_start(self):
        r = send_multi(["I don't know where to start, I need help with everything"])
        resp = r[0]["response"].lower()
        assert "overwhelming" in resp or "one step" in resp

    def test_overwhelmed_help(self):
        r = send_multi(["I'm overwhelmed, help"])
        resp = r[0]["response"].lower()
        assert "overwhelming" in resp or "one step" in resp

    def test_plain_help_no_empathy(self):
        """Non-confused 'help' should use standard response."""
        r = send_multi(["help"])
        resp = r[0]["response"].lower()
        assert "overwhelming" not in resp

    def test_help_still_shows_service_menu(self):
        """Empathetic help response should still include quick replies."""
        r = send_multi(["I don't know where to start, I need help"])
        qr_labels = [q["label"] for q in r[0].get("quick_replies", [])]
        assert len(qr_labels) > 0


# -----------------------------------------------------------------------
# ESCALATION + YES ACKNOWLEDGMENT
# -----------------------------------------------------------------------

class TestEscalationYesAcknowledgment:
    """'Yes' after escalation should acknowledge, not repeat the same info."""

    def test_yes_does_not_repeat_escalation(self):
        r = send_multi(["connect with peer navigator", "yes"])
        # T1 and T2 should be different
        assert r[1]["response"][:40] != r[0]["response"][:40]

    def test_yes_has_acknowledgment(self):
        r = send_multi(["connect with peer navigator", "yes"])
        resp = r[1]["response"].lower()
        assert "shared" in resp or "reach out" in resp or "contact" in resp

    def test_yes_offers_alternatives(self):
        """After acknowledging, should offer search or re-show contact."""
        r = send_multi(["connect with peer navigator", "yes"])
        qr_labels = [q["label"].lower() for q in r[1].get("quick_replies", [])]
        has_search = any("search" in l for l in qr_labels)
        has_contact = any("contact" in l for l in qr_labels)
        assert has_search or has_contact

    def test_show_contact_again_re_escalates(self):
        """Choosing 'Show contact info again' should re-show full info."""
        r = send_multi([
            "connect with peer navigator",
            "yes",
            "Connect with peer navigator",
        ])
        # T3 should show the full escalation response again
        assert "navigator" in r[2]["response"].lower()
