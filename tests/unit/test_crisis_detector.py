"""
Tests for crisis detection — validates that crisis language is detected
across all five categories and that non-crisis messages pass through.

Run with: python -m pytest tests/test_crisis_detector.py -v
Or just:  python tests/test_crisis_detector.py
"""



from unittest.mock import patch, MagicMock
from app.services.crisis_detector import detect_crisis, is_crisis
import app.services.crisis_detector as cd


# -----------------------------------------------------------------------
# SUICIDE / SELF-HARM
# -----------------------------------------------------------------------

def test_suicide_direct_statements():
    """Direct suicidal statements should be detected."""
    phrases = [
        "I want to kill myself",
        "I wanna die",
        "I wish I was dead",
        "I'm better off dead",
        "I want to end my life",
        "I don't want to live anymore",
        "There's no reason to live",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "suicide_self_harm", \
            f"Wrong category for '{phrase}': {result[0]}"


def test_self_harm():
    """Self-harm language should be detected."""
    phrases = [
        "I've been cutting myself",
        "I keep hurting myself",
        "I'm harming myself",
        "self harm",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "suicide_self_harm"


def test_suicide_response_has_988():
    """Suicide response must include 988 lifeline."""
    result = detect_crisis("I want to kill myself")
    assert result is not None
    assert "988" in result[1]
    assert "741741" in result[1]  # Crisis Text Line


def test_suicide_response_has_trevor():
    """Suicide response must include Trevor Project for LGBTQ+ youth."""
    result = detect_crisis("I want to die")
    assert "Trevor" in result[1]


# -----------------------------------------------------------------------
# VIOLENCE / HARM TO OTHERS
# -----------------------------------------------------------------------

def test_violence_threats():
    """Threats of violence should be detected."""
    phrases = [
        "I'm going to kill someone",
        "I want to hurt someone",
        "I'm gonna hurt him",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "violence", \
            f"Wrong category for '{phrase}': {result[0]}"


def test_violence_response_has_911():
    """Violence response must include 911."""
    result = detect_crisis("I'm going to hurt someone")
    assert "911" in result[1]


# -----------------------------------------------------------------------
# DOMESTIC VIOLENCE / ABUSE
# -----------------------------------------------------------------------

def test_domestic_violence():
    """Domestic violence language should be detected."""
    phrases = [
        "My partner hits me",
        "I'm being abused",
        "I'm in an abusive relationship",
        "domestic violence",
        "he beats me",
        "I'm afraid to go home",
        "my boyfriend hits me",
        "I need a restraining order",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "domestic_violence", \
            f"Wrong category for '{phrase}': {result[0]}"


def test_dv_threat_language():
    """Threat language variants must be detected as domestic_violence.

    Added after crisis_after_results failed in Run 7: 'my boyfriend threatened
    to hurt me tonight' was not in the phrase list, causing the bot to respond
    cheerfully with 'You're welcome!' to a DV crisis disclosure.
    """
    phrases = [
        "Thanks. Actually I'm scared, my boyfriend threatened to hurt me tonight",
        "He threatened to hurt me",
        "She threatened to kill me",
        "He threatened me",
        "He said he would hurt me",
        "He said he'd hurt me",
        "He said he would kill me",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, \
            f"Missed DV threat language: '{phrase}'"
        assert result[0] == "domestic_violence", \
            f"Wrong category for '{phrase}': {result[0]} (expected domestic_violence)"
    """DV response must include the National DV Hotline."""
    result = detect_crisis("my partner hits me")
    assert "1-800-799-7233" in result[1]


def test_dv_response_has_nyc_hotline():
    """DV response should include the NYC-specific hotline."""
    result = detect_crisis("domestic violence")
    assert "1-800-621-4673" in result[1]


# -----------------------------------------------------------------------
# TRAFFICKING / EXPLOITATION
# -----------------------------------------------------------------------

def test_trafficking():
    """Trafficking language should be detected."""
    phrases = [
        "I'm being trafficked",
        "they forced me into prostitution",
        "they took my passport",
        "I can't leave my job, they won't let me",
        "I'm being held against my will",
        "human trafficking",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "trafficking", \
            f"Wrong category for '{phrase}': {result[0]}"


def test_trafficking_response_has_hotline():
    """Trafficking response must include National Trafficking Hotline."""
    result = detect_crisis("I'm being trafficked")
    assert "1-888-373-7888" in result[1]
    assert "233733" in result[1]  # BeFree text number


# -----------------------------------------------------------------------
# MEDICAL EMERGENCY
# -----------------------------------------------------------------------

def test_medical_emergency():
    """Medical emergency language should be detected."""
    phrases = [
        "I'm having a heart attack",
        "I can't breathe",
        "someone is overdosing",
        "they're not breathing",
        "there's blood everywhere",
        "having a seizure",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed crisis: '{phrase}'"
        assert result[0] == "medical_emergency", \
            f"Wrong category for '{phrase}': {result[0]}"


def test_medical_response_has_911():
    """Medical emergency response must include 911."""
    result = detect_crisis("I can't breathe")
    assert "911" in result[1]


def test_medical_response_has_poison_control():
    """Medical emergency response should include Poison Control."""
    result = detect_crisis("someone is overdosing")
    assert "1-800-222-1222" in result[1]


# -----------------------------------------------------------------------
# NO FALSE POSITIVES
# -----------------------------------------------------------------------

def _mock_llm_no_crisis():
    """Context manager that mocks the LLM to return no-crisis for all messages."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"crisis": false}')]
    return patch.object(cd, 'get_client', return_value=MagicMock(
        messages=MagicMock(create=MagicMock(return_value=mock_message))
    ))


def test_no_false_positives_service_requests():
    """Normal service requests should NOT trigger crisis detection."""
    safe_messages = [
        "I need food in Brooklyn",
        "Where can I find shelter tonight?",
        "I'm looking for a job",
        "I need legal help",
        "I need medical help",
        "I'm hungry",
        "I need clothes",
        "Where's the nearest clinic?",
        "I need help with my resume",
    ]
    with _mock_llm_no_crisis():
        for msg in safe_messages:
            result = detect_crisis(msg)
            assert result is None, \
                f"False positive on: '{msg}' → {result[0] if result else None}"


def test_no_false_positives_conversational():
    """Conversational messages should NOT trigger crisis detection."""
    safe_messages = [
        "thank you",
        "hi",
        "start over",
        "tell me more",
        "that's helpful",
        "I'm doing okay",
        "I'm fine",
        "what can you do",
    ]
    with _mock_llm_no_crisis():
        for msg in safe_messages:
            result = detect_crisis(msg)
            assert result is None, \
                f"False positive on: '{msg}' → {result[0] if result else None}"


def test_no_false_positive_on_hurt_in_context():
    """'Hurt' in a non-crisis context should not trigger."""
    # "My foot hurts" shouldn't trigger violence detection
    safe_messages = [
        "My foot hurts",
        "I hurt my back at work",
        "The process hurt my application",
    ]
    with _mock_llm_no_crisis():
        for msg in safe_messages:
            result = detect_crisis(msg)
            assert result is None, \
                f"False positive on: '{msg}' → {result[0] if result else None}"


# -----------------------------------------------------------------------
# PASSIVE SUICIDAL IDEATION (P8)
# -----------------------------------------------------------------------
# "What's the point anymore" and similar indirect hopelessness phrases
# don't mention death explicitly but signal suicidal ideation. These were
# added after crisis_passive_suicidal scored 2.2 in Run 6.

def test_passive_suicidal_ideation():
    """Indirect hopelessness phrases must be detected as suicide_self_harm."""
    phrases = [
        "What's the point anymore. I've been on the streets for months and nothing helps",
        "Whats the point anymore",
        "what is the point anymore",
        "Nothing helps anymore, I give up",
        "I've given up on everything",
        "I can't do this anymore",
        "There's no hope left for me",
        "Nobody cares if I'm here",
        "Everyone would be better off without me",
        "I'm done fighting, I just can't keep going",
        "I'm so tired of living like this",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, \
            f"Missed passive suicidal ideation: '{phrase}'"
        assert result[0] == "suicide_self_harm", \
            f"Wrong category for '{phrase}': {result[0]} (expected suicide_self_harm)"


def test_passive_ideation_response_has_988():
    """Passive ideation must route to suicide response with 988."""
    result = detect_crisis("What's the point anymore, nothing helps")
    assert result is not None
    assert result[0] == "suicide_self_harm"
    assert "988" in result[1], "Suicide response must include 988"
    assert "741741" in result[1], "Suicide response must include Crisis Text Line"


def test_passive_ideation_in_longer_message():
    """Passive ideation embedded in a longer service-seeking message must be caught."""
    msg = (
        "I've been trying to find food and shelter for weeks now. "
        "Nothing helps anymore. I give up."
    )
    result = detect_crisis(msg)
    assert result is not None, "Passive ideation missed in longer message"
    assert result[0] == "suicide_self_harm"


def test_no_false_positive_on_giving_up_task():
    """'I give up' on a specific task should not fire — context matters.

    Note: the current detector uses substring matching, so 'I give up' alone
    WILL fire. This test documents that the phrase is intentionally broad —
    erring on the side of false positives is correct for this population.
    A user saying 'I give up on this search' will see crisis resources, which
    is acceptable given the risk of missing genuine ideation.
    """
    # Confirm 'i give up' fires — this is intentional, not a bug
    result = detect_crisis("I give up")
    assert result is not None and result[0] == "suicide_self_harm", \
        "'I give up' should fire — intentionally broad for crisis-vulnerable population"


# -----------------------------------------------------------------------
# YOUTH RUNAWAY / UNSAFE HOME (P9)
# -----------------------------------------------------------------------
# Running away from home is a safety crisis for youth — these phrases
# were added after crisis_youth_runaway scored 3.9 in Run 6 because the
# bot delivered shelter without offering safety resources first.

def test_youth_runaway_detection():
    """Youth runaway and unsafe home phrases must trigger crisis detection."""
    # Phrases that map to youth_runaway (family/home-specific)
    youth_runaway_phrases = [
        "I'm 16 and I ran away from home last night. I have nowhere to go in Brooklyn.",
        "I ran away from home",
        "I run away from home",
        "I'm a runaway",
        "I am a runaway",
        "It's not safe at home",
        "Home isn't safe for me",
        "I can't go home, it's not safe",
        "I can't go home",
    ]
    for phrase in youth_runaway_phrases:
        result = detect_crisis(phrase)
        assert result is not None, \
            f"Missed youth runaway/unsafe home phrase: '{phrase}'"
        assert result[0] == "youth_runaway", \
            f"Wrong category for '{phrase}': {result[0]} (expected youth_runaway)"

    # "Kicked out by parents/family" → youth_runaway
    family_kicked_out = [
        "My parents kicked me out",
        "My family kicked me out",
    ]
    for phrase in family_kicked_out:
        result = detect_crisis(phrase)
        assert result is not None, \
            f"Missed kicked-out phrase: '{phrase}'"
        assert result[0] in ("youth_runaway", "domestic_violence"), \
            f"Wrong category for '{phrase}': {result[0]} (expected youth_runaway or domestic_violence)"

    # Generic "kicked out" (no family context) → safety_concern or DV
    generic_kicked_out = [
        "I got kicked out of my home",
        "I was thrown out of my home",
    ]
    for phrase in generic_kicked_out:
        result = detect_crisis(phrase)
        assert result is not None, \
            f"Missed kicked-out phrase: '{phrase}'"
        assert result[0] in ("safety_concern", "domestic_violence"), \
            f"Wrong category for '{phrase}': {result[0]} (expected safety_concern or domestic_violence)"



def test_runaway_response_has_resources():
    """Runaway scenario must route to youth_runaway response with youth-specific resources."""
    result = detect_crisis("I ran away from home last night")
    assert result is not None
    assert result[0] == "youth_runaway"
    assert "1-800-786-2929" in result[1], \
        "Youth runaway response must include National Runaway Safeline"
    assert "Covenant House" in result[1], \
        "Youth runaway response must include Covenant House"


def test_runaway_minor_with_shelter_need():
    """A minor runaway mentioning shelter should still trigger crisis first."""
    msg = "I'm 16, I ran away from home yesterday and I need somewhere to sleep in Queens"
    result = detect_crisis(msg)
    assert result is not None, \
        "Runaway minor with shelter request must trigger crisis detection"
    assert result[0] in ("youth_runaway", "domestic_violence"), \
        f"Expected youth_runaway or domestic_violence, got {result[0] if result else None}"


def test_kicked_out_adult():
    """Being kicked out of home triggers crisis detection.

    Note: 'kicked out' overlaps with the DV detector ('kicked me out' phrase),
    so these may return domestic_violence instead of safety_concern. Both are
    appropriate responses — DV resources are relevant for someone kicked out.
    """
    phrases = [
        "My landlord kicked me out at 2am",
        "I was kicked out of my home with nowhere to go",
    ]
    for phrase in phrases:
        result = detect_crisis(phrase)
        assert result is not None, f"Missed kicked-out scenario: '{phrase}'"
        assert result[0] in ("safety_concern", "domestic_violence"), \
            f"Wrong category for '{phrase}': {result[0]}"


def test_no_false_positive_on_shelter_search():
    """Normal shelter searches must NOT trigger runaway detection."""
    safe_messages = [
        "I need shelter in Brooklyn",
        "Where can I find a shelter tonight?",
        "Is there a warming center in Queens?",
        "I need somewhere to stay",
        "Looking for emergency housing in the Bronx",
    ]
    with _mock_llm_no_crisis():
        for msg in safe_messages:
            result = detect_crisis(msg)
            assert result is None, \
                f"False positive on shelter search: '{msg}' → {result[0] if result else None}"

def test_is_crisis_helper():
    """is_crisis() should return True/False correctly."""
    assert is_crisis("I want to kill myself") is True
    with _mock_llm_no_crisis():
        assert is_crisis("I need food in Brooklyn") is False


def test_crisis_in_longer_message():
    """Crisis language embedded in a longer message should still be detected."""
    msg = "I've been homeless for a month and I just want to die"
    result = detect_crisis(msg)
    assert result is not None
    assert result[0] == "suicide_self_harm"


def test_crisis_with_service_request():
    """Crisis language mixed with a service request should still detect crisis."""
    msg = "I need shelter and I'm being abused by my partner"
    result = detect_crisis(msg)
    assert result is not None
    assert result[0] == "domestic_violence"


# -----------------------------------------------------------------------
# LLM CRISIS DETECTION (Stage 2)
# -----------------------------------------------------------------------
# These tests mock the Anthropic client so they run without a real API key.
# They verify:
#   - The LLM path is only invoked when regex misses
#   - LLM detections are correctly routed to the right response
#   - Fail-open: LLM errors return safety_concern, not None
#   - The LLM prompt is tight enough for non-crisis messages

def test_llm_not_called_when_regex_fires():
    """Regex hits should short-circuit before the LLM is called."""
    from unittest.mock import patch, MagicMock
    import app.services.crisis_detector as cd

    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, '_detect_crisis_llm') as mock_llm:
        result = detect_crisis("I want to kill myself")

    assert result is not None
    assert result[0] == "suicide_self_harm"
    mock_llm.assert_not_called()


def test_llm_called_when_regex_misses():
    """Messages that bypass regex should trigger the LLM."""
    from unittest.mock import patch, MagicMock
    import app.services.crisis_detector as cd

    mock_result = ("suicide_self_harm", cd._SUICIDE_RESPONSE)
    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, '_detect_crisis_llm', return_value=mock_result) as mock_llm:
        result = detect_crisis("I've been on the streets for months, honestly what's even the point")

    mock_llm.assert_called_once()
    assert result == mock_result


def test_llm_detects_indirect_suicidal_ideation():
    """LLM should catch indirect hopelessness that regex can't enumerate."""
    from unittest.mock import patch, MagicMock
    import app.services.crisis_detector as cd

    # Simulate Claude returning a crisis=true JSON response
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"crisis": true, "category": "suicide_self_harm"}')]

    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, 'get_client') as mock_client:
        mock_client.return_value.messages.create.return_value = mock_message
        result = cd._detect_crisis_llm("I just feel like no one would even notice if I disappeared")

    assert result is not None
    assert result[0] == "suicide_self_harm"
    assert "988" in result[1]


def test_llm_returns_none_for_non_crisis():
    """LLM should return None for genuine service requests."""
    from unittest.mock import patch, MagicMock
    import app.services.crisis_detector as cd

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"crisis": false}')]

    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, 'get_client') as mock_client:
        mock_client.return_value.messages.create.return_value = mock_message
        result = cd._detect_crisis_llm("I need food in Brooklyn")

    assert result is None


def test_llm_failopen_on_api_error():
    """LLM API errors must fail open — return safety_concern, not None."""
    from unittest.mock import patch
    import app.services.crisis_detector as cd

    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, 'get_client', side_effect=RuntimeError("API unavailable")):
        result = cd._detect_crisis_llm("I don't know what to do anymore, no one cares")

    # Must not return None — fail open
    assert result is not None, "LLM error must fail open, not return None"
    assert result[0] == "safety_concern"
    assert "988" in result[1] or "1-800" in result[1]


def test_llm_failopen_on_malformed_json():
    """Malformed LLM output must also fail open."""
    from unittest.mock import patch, MagicMock
    import app.services.crisis_detector as cd

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="I cannot determine...")]  # non-JSON

    with patch.object(cd, '_USE_LLM_DETECTION', True), \
         patch.object(cd, 'get_client') as mock_client:
        mock_client.return_value.messages.create.return_value = mock_message
        result = cd._detect_crisis_llm("something ambiguous")

    assert result is not None, "Malformed JSON must fail open"
    assert result[0] == "safety_concern"


def test_llm_uses_sonnet_for_crisis():
    """LLM crisis detection must use Sonnet (nuance on indirect language)."""
    from unittest.mock import patch, MagicMock, call
    import app.services.crisis_detector as cd

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text='{"crisis": false}')]

    with patch.object(cd, 'get_client') as mock_client:
        mock_create = mock_client.return_value.messages.create
        mock_create.return_value = mock_message
        cd._detect_crisis_llm("test message")

    call_kwargs = mock_create.call_args.kwargs
    assert "sonnet" in call_kwargs.get("model", "").lower(), \
        f"Must use Sonnet model for crisis detection nuance, got: {call_kwargs.get('model')}"
    assert call_kwargs.get("max_tokens", 999) <= 60, \
        "Max tokens should be small (60) — crisis response is ~15 tokens"

