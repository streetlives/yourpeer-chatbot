"""
PII Redactor — detects and redacts personally identifiable information.

Six detection categories, run in fixed order (most specific first):
  1. SSN       → [SSN]
  2. Email     → [EMAIL]
  3. Phone     → [PHONE]
  4. DOB       → [DOB]
  5. Address   → [ADDRESS]
  6. Name      → [NAME]

Design: regex-only, no heavy dependencies. Precision over recall —
false positives are more disruptive than missed detections in a chatbot
where most messages are short service requests.

Detection order matters: SSN before phone (SSNs are a subset of phone
digit ranges), and each stage skips spans already claimed.

See docs/design/PII_REDACTION.md for full pattern details and tradeoffs.
"""

import re
from collections import namedtuple

Detection = namedtuple("Detection", ["pii_type", "start", "end"])


# ---------------------------------------------------------------------------
# 1. SSN — 3-2-4 digit structure
# ---------------------------------------------------------------------------
_SSN_RE = re.compile(
    r'\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{4})\b'
)

# ---------------------------------------------------------------------------
# 2. Email
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)

# ---------------------------------------------------------------------------
# 3. Credit Card — 13-19 digits with Luhn checksum validation
# ---------------------------------------------------------------------------
_CC_RE = re.compile(
    r'\b(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})[-\s]?(\d{1,7})\b'
)
# Also catch unseparated 13-19 digit runs
_CC_LONG_RE = re.compile(r'\b(\d{13,19})\b')


def _luhn_check(number_str: str) -> bool:
    """Luhn algorithm checksum — validates credit card numbers.
    Industry standard (ISO/IEC 7812-1) used by Visa, Mastercard, Amex, etc."""
    digits = [int(d) for d in number_str]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------------------
# 4. Phone — 10-11 digits with optional separators
# ---------------------------------------------------------------------------
_PHONE_RE = re.compile(
    r'(?<!\d)'                    # not preceded by another digit
    r'(?:\+?1[-.\s]?)?'          # optional country code
    r'(?:\(?\d{3}\)?[-.\s]?)'    # area code
    r'\d{3}[-.\s]?\d{4}'         # subscriber number
    r'(?!\d)'                     # not followed by another digit
)

# ---------------------------------------------------------------------------
# 4b. URL — may contain usernames or identifying paths
# ---------------------------------------------------------------------------
_URL_RE = re.compile(
    r'https?://[^\s<>\"\']+|'                          # full URL
    r'\b(?:facebook|instagram|twitter|tiktok|linkedin'  # social media bare domains
    r'|youtube|snapchat)\.com/[^\s<>\"\']+',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 4. DOB — date patterns
# ---------------------------------------------------------------------------
_DOB_NUMERIC_RE = re.compile(
    r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b'
)
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DOB_WRITTEN_RE = re.compile(
    rf'\b({_MONTHS})\s+\d{{1,2}},?\s+\d{{2,4}}\b',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 5. Address — number + street name + suffix, with optional apt
# ---------------------------------------------------------------------------
_STREET_SUFFIXES = (
    r"(?:Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?"
    r"|Drive|Dr\.?|Lane|Ln\.?|Place|Pl\.?|Court|Ct\.?"
    r"|Way|Terrace|Ter\.?)"
)
_APT_SUFFIX = r'(?:\s+(?:Apt|Unit|Suite|Ste|Fl|Rm|#)\s*\w+)?'

# Standard: "123 Main Street Apt 4B"
_ADDR_STANDARD_RE = re.compile(
    rf'\b\d+\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?\s+{_STREET_SUFFIXES}{_APT_SUFFIX}',
)

# Ordinal: "789 5th Avenue", "456 West 42nd Street"
_ADDR_ORDINAL_RE = re.compile(
    rf'\b\d+\s+(?:(?:North|South|East|West|N|S|E|W)\s+)?'
    rf'\d+(?:st|nd|rd|th)\s+{_STREET_SUFFIXES}{_APT_SUFFIX}',
    re.IGNORECASE,
)

# Broadway special case
_ADDR_BROADWAY_RE = re.compile(
    rf'\b\d+\s+Broadway{_APT_SUFFIX}',
    re.IGNORECASE,
)

# Preposition-prefixed: "at 123 Main"
_ADDR_PREP_RE = re.compile(
    rf'(?:at|on|to)\s+\d+\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?{_APT_SUFFIX}',
)

# ---------------------------------------------------------------------------
# 6. Name — intro phrase + capitalized word(s)
# ---------------------------------------------------------------------------
_IM_BLOCKLIST = {
    "in", "at", "on", "from", "near",
    "a", "an", "the", "not", "so", "just", "really", "very", "too",
    "looking", "searching", "trying", "feeling", "having", "doing",
    "going", "getting", "staying", "living", "sleeping", "working",
    "here", "there", "back", "out", "up", "down", "over", "around",
    "ok", "okay", "good", "fine", "bad", "great", "sure", "ready",
    "hungry", "tired", "sick", "cold", "hot", "scared", "lost",
    "alone", "homeless", "new", "old", "young",
    "struggling", "overwhelmed", "confused", "stressed", "depressed",
    "anxious", "ashamed", "embarrassed", "desperate", "pathetic",
    "manhattan", "brooklyn", "queens", "bronx", "harlem",
    "midtown", "downtown", "uptown",
    "interested", "calling", "wondering", "asking",
}

_NAME_FULL_RE = re.compile(
    r"(?:my name is|my name's|name's|call me|this is|i am called"
    r"|they call me|everyone calls me|you can call me)\s+"
    r"([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)?)",
    re.IGNORECASE,
)

# "Hi Bryan!" pattern — common in bot responses echoing a name
_NAME_HI_RE = re.compile(
    r"(?:^|[.!?]\s*)(?:hi|hey|hello|dear)\s+([A-Z][a-z]{2,})\b",
    re.IGNORECASE,
)

# Sign-off pattern: "Thanks, Sarah" / "— John" / "Sincerely, Maria"
_NAME_SIGNOFF_RE = re.compile(
    r"(?:thanks|thank you|sincerely|regards|cheers|best),?\s+"
    r"([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Bare name in bot response: "Sure Sarah," / "Okay Bryan,"
_NAME_BARE_RE = re.compile(
    r"(?:sure|okay|ok|alright|absolutely|of course|no problem|got it)\s+"
    r"([A-Z][a-z]{2,})\b",
    re.IGNORECASE,
)

_NAME_IM_RE = re.compile(
    r"(?:i'm|im|i am)\s+([A-Za-z]{3,15})\b",
    re.IGNORECASE,
)

# Shared blocklist for all name patterns
_NAME_BLOCKLIST = _IM_BLOCKLIST | {
    "there", "everyone", "all", "friend", "guys",
}


# ---------------------------------------------------------------------------
# Main detection and redaction logic
# ---------------------------------------------------------------------------

def _overlaps(start, end, claimed):
    for cs, ce in claimed:
        if start < ce and end > cs:
            return True
    return False


# ---------------------------------------------------------------------------
# 8. Gender Identity — PII-adjacent for homeless/at-risk population
# ---------------------------------------------------------------------------
# Gender identity is sensitive for this population — outing someone or
# storing their gender could be harmful. Redact self-identifying phrases
# from stored transcripts. Only matches identity declarations, not
# incidental uses (e.g., "the man at the counter" is not redacted).
_GENDER_IDENTITY_RE = re.compile(
    r'\b(?:'
    r'(?:i[\'  ]?m|i am|as) (?:a )?'  # "I'm a", "I am", "as a"
    r'(?:trans\s*(?:woman|man|gender|masculine|feminine)|'
    r'non[\- ]?binary|enby|genderqueer|gender[\- ]?fluid|agender|'
    r'lgbtq\+?|lgbt|queer|gay|lesbian|bisexual|'
    r'mtf|ftm)'
    r')\b',
    re.IGNORECASE,
)


def detect_pii(text):
    """Detect PII in text. Returns list of Detection(pii_type, start, end)."""
    detections = []
    claimed = []

    # 1. SSN
    for m in _SSN_RE.finditer(text):
        full = m.group().replace("-", "").replace(" ", "")
        if len(full) == 9 and not _overlaps(m.start(), m.end(), claimed):
            detections.append(Detection("ssn", m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    # 2. Email
    for m in _EMAIL_RE.finditer(text):
        if not _overlaps(m.start(), m.end(), claimed):
            detections.append(Detection("email", m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    # 3. Credit Card (before phone — CC numbers overlap with phone digit ranges)
    for pattern in [_CC_RE, _CC_LONG_RE]:
        for m in pattern.finditer(text):
            digits = re.sub(r'\D', '', m.group())
            if 13 <= len(digits) <= 19 and _luhn_check(digits):
                if not _overlaps(m.start(), m.end(), claimed):
                    detections.append(Detection("credit_card", m.start(), m.end()))
                    claimed.append((m.start(), m.end()))

    # 4. Phone
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r'\D', '', m.group())
        if len(digits) in (10, 11) and not _overlaps(m.start(), m.end(), claimed):
            detections.append(Detection("phone", m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    # 4b. URL
    for m in _URL_RE.finditer(text):
        if not _overlaps(m.start(), m.end(), claimed):
            detections.append(Detection("url", m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    # 4. DOB
    for m in _DOB_NUMERIC_RE.finditer(text):
        try:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                if not _overlaps(m.start(), m.end(), claimed):
                    detections.append(Detection("dob", m.start(), m.end()))
                    claimed.append((m.start(), m.end()))
        except ValueError:
            pass
    for m in _DOB_WRITTEN_RE.finditer(text):
        if not _overlaps(m.start(), m.end(), claimed):
            detections.append(Detection("dob", m.start(), m.end()))
            claimed.append((m.start(), m.end()))

    # 5. Address
    for pattern in [_ADDR_STANDARD_RE, _ADDR_ORDINAL_RE,
                    _ADDR_BROADWAY_RE, _ADDR_PREP_RE]:
        for m in pattern.finditer(text):
            if not _overlaps(m.start(), m.end(), claimed):
                detections.append(Detection("address", m.start(), m.end()))
                claimed.append((m.start(), m.end()))

    # 6. Name
    for m in _NAME_FULL_RE.finditer(text):
        name = m.group(1)
        # Check first word against blocklist
        first_word = name.split()[0].lower()
        if first_word not in _NAME_BLOCKLIST:
            s, e = m.start(1), m.end(1)
            if not _overlaps(s, e, claimed):
                detections.append(Detection("name", s, e))
                claimed.append((s, e))

    for m in _NAME_HI_RE.finditer(text):
        word = m.group(1).lower()
        if word not in _NAME_BLOCKLIST:
            s, e = m.start(1), m.end(1)
            if not _overlaps(s, e, claimed):
                detections.append(Detection("name", s, e))
                claimed.append((s, e))

    for m in _NAME_IM_RE.finditer(text):
        word = m.group(1)
        # Must start with uppercase in original text to be a name
        # (distinguishes "I'm Sarah" from "I'm scared")
        if word[0].isupper() and word.lower() not in _NAME_BLOCKLIST:
            s, e = m.start(1), m.end(1)
            if not _overlaps(s, e, claimed):
                detections.append(Detection("name", s, e))
                claimed.append((s, e))

    # Sign-off: "Thanks, Sarah" / "Sincerely, Maria Rodriguez"
    for m in _NAME_SIGNOFF_RE.finditer(text):
        name = m.group(1)
        first_word = name.split()[0].lower()
        if first_word not in _NAME_BLOCKLIST:
            s, e = m.start(1), m.end(1)
            if not _overlaps(s, e, claimed):
                detections.append(Detection("name", s, e))
                claimed.append((s, e))

    # Bare name in bot response: "Sure Sarah," / "Okay Bryan,"
    for m in _NAME_BARE_RE.finditer(text):
        word = m.group(1)
        if word[0].isupper() and word.lower() not in _NAME_BLOCKLIST:
            s, e = m.start(1), m.end(1)
            if not _overlaps(s, e, claimed):
                detections.append(Detection("name", s, e))
                claimed.append((s, e))

    # 8. Gender identity terms — PII-adjacent for this population.
    # Redact self-identifying gender/LGBTQ phrases from stored transcripts.
    for m in _GENDER_IDENTITY_RE.finditer(text):
        s, e = m.start(), m.end()
        if not _overlaps(s, e, claimed):
            detections.append(Detection("gender", s, e))
            claimed.append((s, e))

    return detections


_PLACEHOLDERS = {
    "ssn": "[SSN]",
    "email": "[EMAIL]",
    "credit_card": "[CREDIT_CARD]",
    "phone": "[PHONE]",
    "url": "[URL]",
    "dob": "[DOB]",
    "address": "[ADDRESS]",
    "name": "[NAME]",
    "gender": "[GENDER]",
}


def redact_pii(text):
    """Redact PII, returning (redacted_text, detections)."""
    detections = detect_pii(text)
    if not detections:
        return text, []

    sorted_dets = sorted(detections, key=lambda d: d.start, reverse=True)
    result = text
    for det in sorted_dets:
        placeholder = _PLACEHOLDERS.get(det.pii_type, "[PII]")
        result = result[:det.start] + placeholder + result[det.end:]

    return result, detections


def has_pii(text):
    """Quick boolean check for any PII."""
    return len(detect_pii(text)) > 0
