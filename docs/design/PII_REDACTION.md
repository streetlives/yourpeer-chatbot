# PII Redaction

This document describes how the YourPeer chatbot detects and redacts personally identifiable information, the tradeoffs behind each pattern, known gaps, and directions for future improvement.

## Overview

PII redaction runs on every incoming user message and on bot responses before they are stored in the session transcript or audit log. The original (unredacted) user text is still used for slot extraction so that location names and ages parse correctly, but the redacted version is all that persists.

The confirmation echo ("I'll search for food in Brooklyn") also runs through the redactor so that PII accidentally captured in a slot value (e.g. a street address extracted as a location) is never displayed back to the user.

## Design Principles

**Regex-only, no heavy dependencies.** The redactor uses compiled regular expressions with no external libraries (no spaCy, no Presidio). This keeps the deploy simple and fast (<1ms per message).

**Precision over recall.** Each pattern is tuned to minimize false positives. In a chatbot where most messages are short service requests like "I need food in Brooklyn", a false positive is more disruptive than a missed detection — redacting "Brooklyn" as a name or "17" as part of a phone number would break the conversation. Where a pattern has ambiguity, we err on the side of not redacting.

**Overlap resolution.** Detections are checked against all prior matches before being added. If a span was already matched (e.g. a 9-digit number as SSN), it won't also be matched as a phone number. SSN is checked before phone because SSNs are a strict subset of the phone digit range.

**Detection order.** Patterns run in a fixed order — SSN, email, credit card, phone, URL, DOB, address, name, gender identity — with each stage skipping spans already claimed by earlier stages. This prevents double-counting and ensures the most specific match wins. Credit card runs before phone because 16-digit CC numbers would otherwise partially match as 10-digit phone numbers.

## Categories

| PII Type | Placeholder | Example Input | Example Output |
|---|---|---|---|
| Phone number | `[PHONE]` | `Call me at 212-555-1234` | `Call me at [PHONE]` |
| Social Security # | `[SSN]` | `My SSN is 123-45-6789` | `My SSN is [SSN]` |
| Email address | `[EMAIL]` | `Email me at user@test.com` | `Email me at [EMAIL]` |
| Date of birth | `[DOB]` | `Born 01/15/1990` | `Born [DOB]` |
| Street address | `[ADDRESS]` | `I live at 123 Main Street Apt 4B` | `I live at [ADDRESS]` |
| Name | `[NAME]` | `My name is Sarah` | `My name is [NAME]` |
| Gender identity | `[GENDER]` | `I'm a trans man` | `[GENDER]` |
| Credit card | `[CREDIT_CARD]` | `4111 1111 1111 1111` | `[CREDIT_CARD]` |
| URL | `[URL]` | `facebook.com/john.smith` | `[URL]` |

## Pattern Details and Tradeoffs

### Phone Numbers

**Pattern:** Optional country code (`+1`), 3-digit area code (with or without parens), 3+4 digit subscriber number. Separators can be spaces, dashes, or dots. Requires 10-11 total digits.

**Catches:** `(212) 555-1234`, `212-555-1234`, `212.555.1234`, `2125551234`, `+1 212 555 1234`

**Tradeoff — 10-digit numeric IDs.** Any 10-digit number will match, including case numbers, benefit IDs, or reference codes a user might share. There is no way to distinguish these from phone numbers using regex alone. The 10-11 digit filter prevents shorter numbers (zip codes, ages, counts) from matching, but genuine 10-digit non-phone IDs will be over-redacted.

### Social Security Numbers

**Pattern:** Three groups in 3-2-4 digit structure, with optional dash or space separators.

**Catches:** `123-45-6789`, `123 45 6789`, `123456789`

**Tradeoff — any 3-2-4 digit grouping.** A case number or reference ID formatted as `XXX-XX-XXXX` will be flagged as an SSN. The grouping structure is the only distinguishing signal available to regex. SSN is checked before phone so that 9-digit matches are claimed as SSN rather than being misclassified as a short phone number.

### Email Addresses

**Pattern:** Standard `user@domain.tld` format.

**Catches:** `sarah@gmail.com`, `user.name+tag@example.co.uk`

**No significant tradeoffs.** Email format is distinctive enough that false positives are extremely rare in conversational text.

### Credit Cards

**Pattern:** 13-19 digit numbers in groups of 4 (with space, dash, or no separator), validated by the **Luhn algorithm** (ISO/IEC 7812-1). The Luhn checksum is the industry standard used by Visa, Mastercard, Amex, and all major card networks.

**Catches:** `4111 1111 1111 1111`, `4111-1111-1111-1111`, `4111111111111111`

**Why Luhn matters:** Without checksum validation, any 16-digit number would match — reference codes, case numbers, benefit IDs. The Luhn check reduces false positives dramatically: a random 16-digit number has only a 10% chance of passing. This is important for our population, who may share benefit IDs or case numbers that happen to be long digit strings.

**Tradeoff — valid Luhn numbers that aren't credit cards.** Some non-credit-card identifiers also use Luhn checksums (e.g., Canadian SIN numbers, IMEI numbers). These will be redacted as credit cards. This is acceptable — any long numeric identifier that passes Luhn is likely sensitive.

**Detection order:** Credit card runs before phone detection to prevent 16-digit CC numbers from being partially matched as 10-digit phone numbers.

### URLs

**Pattern:** Full URLs (`https://...`) and social media bare domain paths (`facebook.com/username`, `instagram.com/handle`, etc.).

**Catches:** `https://example.com/profile?user=123`, `facebook.com/john.smith`, `instagram.com/realSarah`

**Why URLs are PII:** Social media profile URLs directly identify individuals. Even non-social URLs may contain usernames, email addresses, or account IDs in the path or query string.

**Tradeoff — only known social platforms matched without https.** Bare domain matching (without `https://`) is limited to Facebook, Instagram, Twitter, TikTok, LinkedIn, YouTube, and Snapchat. Other domains require `https://` prefix to match. This prevents false positives on domain-like text ("yourpeer.nyc").

### Dates of Birth

**Pattern:** Two formats: numeric `MM/DD/YYYY` (or `M/D/YY`, with `/` or `-`) and written-out `January 15, 1990` (or `Jan 15 1990`).

**Catches:** `01/15/1990`, `1/15/90`, `01-15-1990`, `January 15, 1990`, `Jan 15 1990`

**Tradeoff — any date matches, not just birthdays.** The pattern has no context check for "born", "birthday", "DOB", etc. Any date in `MM/DD/YYYY` format will be redacted, including appointment dates, document dates, or references like "come back on 4/15/2026". This is a deliberate precision tradeoff: requiring a "birthday" keyword would miss most real DOB disclosures ("I was born 01/15/1990" doesn't always include the keyword), and over-redacting dates is less harmful than leaking a real DOB.

### Street Addresses

**Patterns:** Four complementary patterns, all requiring a leading house number:

1. **Standard:** `number + word name(s) + suffix` — e.g. "123 Main Street", "456 West Oak Avenue"
2. **Ordinal:** `number + optional direction + ordinal + suffix` — e.g. "789 5th Avenue", "456 West 42nd Street"
3. **Broadway:** `number + Broadway` — special case, no suffix needed
4. **Suffix-less with preposition:** `at/on/to + number + word(s)` — e.g. "I live at 123 Main"

All four patterns optionally capture a trailing apartment/unit identifier (`Apt 4B`, `#12`, `Unit 3`, `Suite 5A`, `Fl 2`, `Rm 8`).

**Street suffixes recognized:** Street, St, St., Avenue, Ave, Boulevard, Blvd, Road, Rd, Drive, Dr, Lane, Ln, Place, Pl, Court, Ct, Way, Terrace, Ter, Ter.

**Tradeoff — `St` and `Ter` abbreviations.** These were initially excluded because they collide with common words ("status", "still", "shelter", "terminal"). They are now included because the full pattern requires a leading house number + capitalized word before the suffix, which eliminates those false positives. The word "St" in isolation will never match — only "123 Main St" will.

**Tradeoff — suffix-less addresses require a preposition.** "I live at 123 Main" is detected, but bare "123 Main" is not. Without the preposition constraint, any `number + word` combination would match ("5 Borough", "2 Meals"), creating unacceptable false positives. This means a user who types just "123 Main" without a preposition will not be redacted.

**Gap — no PO Box detection.** "PO Box 1234" is not currently detected.

**Gap — no cross-street format.** "Corner of 5th and Main" or "5th and Lexington" are not detected.

### Names

**Pattern:** Heuristic matching based on six pattern groups, each followed by one or two capitalized words:

1. **Intro phrases:** "my name is", "name's", "call me", "this is", "i am called", "they call me", "everyone calls me", "you can call me"
2. **Greeting prefix:** "Hi", "Hey", "Hello", "Dear" — catches bot responses that echo names
3. **Sign-off:** "Thanks, [Name]", "Sincerely, [Name]", "Regards, [Name]" — catches user sign-offs
4. **Bare prefix in bot response:** "Sure [Name]", "Okay [Name]", "Alright [Name]" — catches LLM responses that echo names
5. **"I'm" pattern:** "I'm [Name]" — most aggressive, requires capitalization check AND 60-word blocklist

**Catches:** `My name is John Smith`, `I'm Sarah`, `Call me David`, `This is Maria`, `Hi Bryan!`, `Thanks, Sarah`, `Sure Bryan, I found results.`, `everyone calls me Rosa`

**Tradeoff — requires a context phrase.** A name mentioned without any context phrase ("Sarah needs food in Brooklyn") will not be detected. This is intentional: without a context phrase, there is no way to distinguish a name from a place name, service keyword, or any other capitalized word using regex alone.

**Tradeoff — manually maintained blocklist.** The `I'm` and bare-prefix patterns use a shared blocklist of ~60 words to prevent false positives on NYC boroughs, neighborhoods, service keywords, common adjectives, and prepositions. The blocklist must be updated when new neighborhoods or keywords are added.

**Gap — single-word names only for `I'm` pattern.** "I'm Sarah Johnson" will only capture "Sarah", not "Sarah Johnson". The intro and sign-off patterns capture up to two words.

## Where Redaction Runs

| Location | What Gets Redacted | Why |
|---|---|---|
| `chatbot.generate_reply()` | Every incoming user message | Redacted version stored in session transcript and audit log |
| `chatbot._log_turn()` | Bot response before audit log storage | Prevents PII echoed by the LLM (e.g. "Hi Bryan!") from persisting in transcripts |
| `chatbot._build_confirmation_message()` | Slot values echoed in confirmation | Prevents PII captured in location slot from being displayed back |

Slot extraction runs on the **original** (unredacted) text so that locations and ages still parse. Only redacted versions of both user messages and bot responses are stored.

## Code Location

All detection logic and response text lives in a single file:

```
backend/app/privacy/pii_redactor.py
```

Key functions:
- `detect_pii(text)` — returns a list of `PIIDetection` objects without modifying the text
- `redact_pii(text)` — returns `(redacted_text, detections)` with placeholders substituted
- `has_pii(text)` — quick boolean check

## Test Coverage

```bash
pytest tests/test_pii_redactor.py -v     # 80 tests covering all 8 categories
pytest tests/test_edge_cases.py -v        # PII + slot interaction tests
```

Tests cover:
- Each PII type with multiple format variations (SSN, email, phone, DOB, address, name)
- Name detection with intro phrases ("my name is", "call me", "I'm", "Hi") and false positive guards (60-word blocklist for "I'm" pattern)
- "Hi Bryan!" greeting pattern for bot response redaction
- False positive checks on NYC locations, service keywords, emotional phrases, and common adjectives
- Apartment/unit capture with addresses
- Abbreviated street suffixes (St, Ter), ordinal streets (5th Avenue), Broadway special case
- Suffix-less addresses with preposition context
- SSN/phone overlap resolution (SSN wins)
- PII not leaking through confirmation echo
- PII redaction not breaking slot extraction
- Age numbers (17) not redacted as PII
- Bot response PII redaction via `_log_turn` and audit log
- ICE/police routing in `_static_bot_answer` (ICE word-boundary prevents "police" collision)

## PII-Adjacent Session Data

Some session slot values are sensitive enough to exclude from audit log serialization but are NOT PII requiring redaction from transcripts. These use the `_` prefix naming convention:

| Slot | Convention | Rationale |
|---|---|---|
| `_gender` | `_` prefix | Gender identity is PII-adjacent. Stored in session for query filtering but excluded from audit log events |
| `_latitude`, `_longitude` | `_` prefix | Browser geolocation coordinates. Stored for proximity search but excluded from audit logs |
| `_populations` | `_` prefix | Population context (veteran, disabled, reentry, dv_survivor, pregnant, senior). Identity-revealing but needed for query boosts. Excluded from audit logs via the same `_` prefix convention |

The `_` prefix convention is enforced by audit log serialization — fields starting with `_` are automatically excluded when session state is written to audit events. This ensures these values influence search behavior without being persisted in reviewable logs.

**Note:** Gender identity *terms* in the raw user message (e.g., "I'm a trans man") ARE redacted from transcripts by the PII redactor (replaced with `[GENDER]`). The `_gender` slot stores the extracted value (`"male"`) separately — it's the extracted value that uses the `_` prefix convention, not the raw text.

## Future Improvements

**Context-aware DOB detection.** Add optional keyword proximity check ("born", "birthday", "DOB", "date of birth") within N words of the date pattern. This would reduce false positives on non-birthday dates while still catching most real disclosures. Start with both modes (strict keyword-required + current catch-all) and compare false positive rates before switching.

**PO Box and cross-street detection.** Add patterns for "PO Box NNNN" and "corner of X and Y" / "X and Y" where both X and Y match known street names or suffixes.

**Name detection beyond intro phrases.** The current approach is limited to names preceded by "my name is", "I'm", etc. An NER model (even a lightweight one) would catch names in other positions. However, this conflicts with the no-heavy-dependencies principle. A middle ground: expand the intro phrase list to cover more patterns ("they call me", "everyone calls me", "you can call me") and add a "sign-off" pattern ("Thanks, Sarah" / "— John").

**Confidence scoring.** Return a confidence level with each detection so downstream consumers can make threshold decisions. SSN and email patterns are near-certain; the `I'm` name pattern and suffix-less address pattern are lower confidence. The confirmation echo could redact only high-confidence detections while the audit log redacts everything.

**LLM-assisted PII detection.** For messages where regex returns nothing but the text is long and conversational, an LLM pass could catch PII the regex missed — similar to the two-stage approach used for crisis detection and slot extraction. This would only run on messages that pass through the LLM for other reasons (slot extraction or conversational fallback), adding no extra latency.
