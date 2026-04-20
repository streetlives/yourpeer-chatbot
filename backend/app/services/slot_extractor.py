import re
import logging
import re as _re

from typing import Optional

logger = logging.getLogger(__name__)

# NOTE: This file contains a simple rule-based slot extractor used for early
# prototyping. It relies on keyword matching and basic regex patterns, so it
# may fail on nuanced or multi-part requests where context matters
# (e.g., "I'm in Queens but looking for a food bank in the Bronx").
# Future versions may replace or augment this logic with LLM-based slot filling
# for more flexible and accurate interpretation.

SERVICE_KEYWORDS = {
    # --- Food (taxonomy: Food, Food Pantry, Mobile Pantry, etc.) ---
    "food": [
        "food", "food bank", "food pantry", "meals", "groceries",
        "pantry", "hungry", "soup kitchen", "soup", "lunch", "dinner",
        "breakfast", "snack", "free food", "hot meal", "brown bag",
        "farmers market", "mobile pantry",
        "something to eat", "grab a bite", "canned food",
        # NYC-specific (P3 audit)
        "baby formula",
        # "formula" retired — "mathematical formula" false positive (REGEX_AUDIT)
        # "wic" moved to _WORD_BOUNDARY_KEYWORDS (REGEX_AUDIT)
        # "meal" moved to _WORD_BOUNDARY_KEYWORDS — "oatmeal" collision (REGEX_AUDIT)
        # Vernacular (Phase 1 audit)
        "starving", "feed my kids", "need to eat",
        # Spanish (basic bilingual support)
        "comida", "tengo hambre", "alimentos",
    ],

    # --- Shelter & Housing (taxonomy: Shelter) ---
    "shelter": [
        "shelter", "place to stay", "somewhere to stay", "housing",
        "sleep tonight",
        "place to sleep", "somewhere to sleep", "homeless", "unhoused",
        "drop-in center", "drop in center", "warming center",
        "overnight", "transitional housing", "safe haven",
        # "room" moved to _WORD_BOUNDARY_KEYWORDS — "classroom" collision (REGEX_AUDIT)
        "place to live", "somewhere to live",
        # "intake" retired — generic term, collides with all services (REGEX_AUDIT)
        "evicted", "kicked out", "kicked me out", "on the street",
        "sleeping outside", "somewhere safe", "safe place",
        # NYC-specific (P3 audit)
        "path center", "dhs intake", "domestic violence shelter",
        # Vernacular (Phase 1 audit)
        "place to crash", "got put out", "somewhere warm",
        "need a cot", "sleeping in my car", "couch surfing",
        # Natural phrasing (test_peer_pregnant_couple_tonight fix)
        # "need a place" alone is too broad — collides with "need a place
        # to eat" and "need a place to shower". Use purpose-specific phrases.
        "need a place to stay", "need a place tonight",
        "a place tonight", "a place to go",
        # Foster care / aging out (Run 24 eval gap)
        "aging out", "aged out", "foster care", "aging out of foster",
        # Spanish (basic bilingual support)
        "refugio", "albergue",
        # Negation-as-request phrases.
        #
        # "I have no place to sleep" is semantically a request for
        # shelter, but the bare keyword "place to sleep" at position
        # 10 in that sentence gets rejected by _is_negated because
        # the 25-char lookback ends in "no " (a negation prefix).
        # Including the negation word in the keyword itself means
        # the match starts earlier (position 7 for "I have no..."),
        # and the preceding window is "I have " — not a negation
        # prefix — so the keyword is accepted.
        #
        # Longest-first sort in _extract_all_service_types ensures
        # these phrases are tried before the bare "place to sleep" /
        # "place to stay" variants that would otherwise get negated.
        "no place to sleep", "no place to stay", "no place to go",
        "nowhere to sleep", "nowhere to stay", "nowhere to go",
        "don't have anywhere to sleep", "dont have anywhere to sleep",
        "don't have anywhere to stay", "dont have anywhere to stay",
        "don't have a place to sleep", "dont have a place to sleep",
        "don't have a place to stay", "dont have a place to stay",
    ],

    # --- Clothing (taxonomy: Clothing) ---
    "clothing": [
        "clothing", "clothes", "jacket", "coat", "shoes", "boots",
        "socks", "underwear", "warm clothes", "winter clothes",
        "free clothes", "outfit", "shirt",
        # "pants" moved to _WORD_BOUNDARY_KEYWORDS — "participants" collision (REGEX_AUDIT)
        "sweater", "sweatshirt", "hoodie", "gloves",
        "winter gear", "sneakers",
        # Clothing occasion phrases — multi-word entries that MUST beat the
        # single-word "interview" (employment keyword) in longest-first sort.
        # "interview clothes" (17 chars) > "interview" (9 chars) → matches
        # clothing first, blocking the employment keyword via span overlap.
        # Resolves the "interview" keyword conflict documented at
        # rag/__init__.py:294-296. DB verified: clothingOccasion attribute
        # has 62 Everyday and 28 Job Interview services.
        "interview clothes", "interview clothing", "interview outfit",
        "professional clothes", "professional clothing",
        "business clothes", "business clothing",
        "casual clothes", "casual clothing",
        "work clothes",
    ],

    # --- Personal Care (taxonomy: Personal Care → Shower, Laundry, etc.) ---
    "personal_care": [
        "shower", "showers", "hygiene", "clean up", "laundry",
        "toiletries", "restroom", "bathroom", "haircut", "barber",
        "toothbrush", "toothpaste", "shampoo", "deodorant",
        # "soap" moved to _WORD_BOUNDARY_KEYWORDS — "soap opera" collision (REGEX_AUDIT)
        # "pads" moved to _WORD_BOUNDARY_KEYWORDS — "iPads" collision (REGEX_AUDIT)
        "personal care", "grooming",
        "hygiene kit", "feminine products", "tampons",
        "menstrual", "razors", "freshen up", "get clean",
    ],

    # --- Health Care (taxonomy: Health) ---
    "medical": [
        "doctor", "clinic", "medical", "hospital", "medicine", "health",
        "health care", "healthcare", "prescription", "dental", "dentist",
        "eye doctor", "glasses", "urgent care", "checkup",
        # "vision" retired — "I have a vision for my future" collision (REGEX_AUDIT)
        # "physical" retired — "physical abuse" collision (REGEX_AUDIT)
        # "sick" moved to _WORD_BOUNDARY_KEYWORDS — "homesick" collision (REGEX_AUDIT)
        "vaccination", "vaccine", "std testing", "hiv testing",
        "nurse", "wound", "injury", "infection",
        "medication", "blood pressure", "sti testing",
        # Harm reduction / community health (P3 audit)
        "methadone", "suboxone", "narcan", "naloxone",
        "walk-in clinic", "walk in clinic",
        # HIV / Harm reduction (Phase 1 audit — 188 services)
        "harm reduction", "needle exchange", "syringe exchange",
        "hepatitis", "hep c",
        # Pregnancy / Maternal health (Phase 1 audit — 41 services)
        "prenatal care", "prenatal", "maternity", "ob-gyn", "obgyn",
        "postpartum",
        # Chronic conditions / medications (Run 24 eval gaps)
        "insulin", "diabetic", "diabetes", "inhaler", "asthma",
        "dialysis", "blood sugar", "epipen",
    ],

    # --- Mental Health (taxonomy: Mental Health) ---
    "mental_health": [
        "mental health", "counseling", "counselor", "therapist", "therapy",
        "depression", "anxiety", "trauma", "ptsd",
        "substance abuse", "addiction", "rehab", "recovery",
        "detox", "detoxification",
        "aa meeting", "na meeting", "narcotics anonymous", "alcoholics anonymous",
        "support group", "emotional support", "psychiatric",
        "psychiatrist", "crisis counseling",
        "grief", "grieving",
        # NOTE: "struggling", "having a hard time", "someone to talk to",
        # and "peer support" removed — they are emotional expressions or
        # escalation signals, not mental health service requests. Keeping
        # them here caused "I'm struggling and need shelter" to misclassify.
        # Substance use (Phase 1 audit — "substance use treatment" is exact
        # taxonomy name in DB, 6 services. These terms had 0% regex coverage)
        "substance use treatment", "treatment program", "treatment center",
        "inpatient", "outpatient", "sober living", "sober house",
        "halfway house", "residential treatment",
        # Anger management (Phase 1 audit — 11 services)
        "anger management",
    ],

    # --- Legal Services (taxonomy: Legal Services, Advocates / Legal Aid) ---
    "legal": [
        "legal", "lawyer", "attorney", "eviction", "immigration",
        # "court" retired — "food court" collision (REGEX_AUDIT). Keep "housing court"
        # "bail" retired — "bail out" collision (REGEX_AUDIT)
        "legal aid", "legal help", "legal services", "tenant rights",
        "asylum", "deportation", "green card", "work permit",
        # "visa" moved to _WORD_BOUNDARY_KEYWORDS (REGEX_AUDIT)
        "public defender", "advocate", "rights",
        "landlord", "tenant", "custody",
        "housing court", "discrimination",
        # DV-specific services (Phase 1 audit — 59 services searchable,
        # distinct from crisis detection phrases)
        "domestic violence help", "dv services", "abuse counseling",
        "order of protection", "protective order",
        "legal clinic", "legal representation",
        # Immigration advanced (Phase 1 audit — 66 services)
        "citizenship", "naturalization", "daca", "tps",
        "work authorization",
    ],

    # --- Employment (taxonomy: Employment) ---
    "employment": [
        "jobs", "employment", "hiring", "career",
        # "job" moved to _WORD_BOUNDARY_KEYWORDS — "good job" collision (REGEX_AUDIT)
        "resume", "interview", "job training", "vocational",
        "workforce", "job placement", "temp work", "day labor",
        "job search", "job help", "find work", "need work",
        "looking for work", "finding work", "help finding work",
        "finding a job", "help finding a job", "help with work",
        "need a job", "get a job", "want a job",
        "apprenticeship", "part-time", "gig work",
        # Trade / career training (Phase 1 audit — 15 services)
        "trade school", "hvac training", "construction training",
        "career training", "workforce development",
        "summer youth employment", "job readiness",
        "vocational training",
    ],

    # --- Other Services (taxonomy: Other service) ---
    # Includes housing assistance programs (rental assistance, Section 8,
    # eviction prevention, etc.) — YourPeer surfaces these under "Other service".
    # "housing" alone stays in shelter (ambiguous → urgent interpretation).
    #
    # NOTE (April 2026 audit): a dedicated "housing_assistance" service type
    # was briefly split out as a Phase 2 feature, then removed to match
    # YourPeer's taxonomy structure. The audit-regression tests in
    # TestHousingAssistanceRemoval enforce that these keywords stay here.
    # service_detail is still set (see _NOTABLE_SUB_TYPES below) so the
    # narrowing-within-other mechanism in rag/__init__.py can kick in.
    "other": [
        "other services", "other service",
        "benefits", "ebt", "food stamps", "medicaid",
        # "snap" moved to _WORD_BOUNDARY_KEYWORDS — "Snapchat" collision (REGEX_AUDIT)
        "social security", "disability", "public assistance",
        "identification", "birth certificate", "need an id",
        "free phone", "wifi", "internet", "charging",
        # "mail" moved to _WORD_BOUNDARY_KEYWORDS — "email"/"gmail" collision (REGEX_AUDIT)
        "mailing address", "storage", "locker",
        "welfare", "cash assistance", "state id", "nyc id",
        "metro card", "charger", "charging station",
        # "transit" moved to _WORD_BOUNDARY_KEYWORDS — "transition" collision (REGEX_AUDIT)
        # NYC-specific (P3 audit)
        "voter registration", "replacement id",
        "tax prep", "tax preparation", "free tax",
        # Housing programs (non-emergency — rental/eviction/vouchers)
        "rental assistance", "help with rent", "behind on rent",
        "rent arrears", "eviction prevention",
        "housing voucher", "housing assistance",
        "housing program", "housing application",
        "section 8", "rent program",
        "homeless prevention",
        "affordable housing", "nycha", "housing connect",
        "subsidized housing", "housing lottery",
        # --- Phase 1 audit: new clusters (886 services discovered) ---
        # Financial (32 services, 0% prior coverage)
        "financial help", "financial advice", "financial advisor",
        "money management", "budgeting", "credit counseling",
        "debt help", "financial literacy",
        "help with money", "bad with money", "money problems",
        # Education / ESL / GED (131 services)
        "english class", "english classes", "learn english",
        "high school equivalency", "adult education", "adult literacy",
        "computer class", "computer skills", "digital literacy",
        "computer training",
        # Senior services (23 services)
        "senior center", "senior services", "older adult",
        "aging services", "elder services",
        # Re-entry (40 services)
        "reentry", "re-entry", "released from jail",
        "released from prison", "just got out of jail",
        # Documents
        "social security card", "document translation",
        # Transit / mobility
        "access-a-ride", "transportation help",
        # Insurance enrollment
        "health insurance", "insurance enrollment",
        "enroll in insurance",
        # LGBTQ non-shelter services (13 services)
        "lgbtq services", "lgbtq support", "lgbtq center",
        "queer services", "queer community",
        # Parenting
        "parenting class", "parenting support", "parenting program",
        # Baby supplies (moved from food — diapers aren't food)
        "diapers", "baby supplies", "stroller", "car seat",
        # Disability (additional terms — "disability" already above)
        "disabled", "disability benefits", "disability services",
        "accessible services",
    ],
}

# Keywords that were previously removed due to substring collisions with
# location names or common phrases. These are matched using \b word
# boundaries instead of plain `in` to prevent false positives.
# Format: { keyword: service_type }
_WORD_BOUNDARY_KEYWORDS = {
    "bed": "shelter",          # was colliding with "bed-stuy", "bedford"
    "wash": "personal_care",   # was colliding with "washington heights"
    # NOTE: "work" not included here — "\bwork\b" still matches "how does
    # this work". Use the phrase-based keywords ("need work", "find work",
    # "looking for work") in SERVICE_KEYWORDS instead.
    "id": "other",             # was colliding with "side", "ridge", "midtown"
    "eat": "food",             # was colliding with "beat", "seat", "theater"
    "hat": "clothing",         # was colliding with "what", "that", "chat"
    "stress": "mental_health", # was colliding with "stressed out", "so stressed"
                               # (emotional expressions, not service requests)
    # Phase 1 audit additions — short terms that collide as substrings
    "ssi": "other",            # was in SERVICE_KEYWORDS, collides with "mission", "passion"
    "ssdi": "other",           # collides with nothing known but too short to risk
    "hiv": "medical",          # collides with "shiver", "archive"
    "esl": "other",            # collides with "diesel", "weasel"
    "ged": "other",            # collides with "aged", "managed", "changed"
    "syep": "employment",      # collides with nothing but 4 chars, be safe
    "sober": "mental_health",  # collides with nothing but contextually useful
    # --- REGEX_AUDIT: moved from SERVICE_KEYWORDS (substring collision risk) ---
    # These are legitimate service terms that collide as substrings of common
    # English words. Word-boundary matching prevents false positives while
    # keeping the fast regex path. The semantic layer (Tier 2) also handles
    # them via example utterances for novel phrasings.
    #
    # NOTE: "job", "sick", "room", "snap", "transit" were initially moved here
    # but removed after testing showed contextual false positives that word
    # boundaries can't prevent ("good job", "sick of this", "oh snap").
    # These are now handled exclusively by the semantic layer.
    "mail": "other",           # "email", "gmail" collision. "mailing address" stays in SERVICE_KEYWORDS
    "soap": "personal_care",   # "soap opera" collision
    "pads": "personal_care",   # "iPads" collision
    "wic": "food",             # "wicked" collision
    "visa": "legal",           # ambiguous (credit card vs immigration)
    "meal": "food",            # "oatmeal" collision (benign but inconsistent)
    "pants": "clothing",        # "participants" collision
    # --- REGEX_AUDIT: removed (collision risk outweighs value) ---
    # "prep" — PrEP collides with "food prep", "test prep". Semantic layer handles.
    # "parole" — population phrase only, not a service request. Stays in _POPULATION_PHRASES.
    # "probation" — population phrase only, not a service request. Stays in _POPULATION_PHRASES.
}

# Pre-compile word-boundary patterns for collision-prone keywords
_WORD_BOUNDARY_PATTERNS = {
    kw: (re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE), svc)
    for kw, svc in _WORD_BOUNDARY_KEYWORDS.items()
}

# Override "bed" with a stricter pattern that excludes hyphen-followed
# occurrences. Python's \b matches between \w and non-\w, so \bbed\b
# DOES match "bed" inside "bed-stuy" (hyphen is non-\w) and inside
# "bed-and-breakfast". The negative lookahead (?!-) blocks these
# without affecting normal uses like "I need a bed".
#
# "bedford" is already handled by plain \bbed\b — "f" is a word char,
# so the trailing \b fails to match between "d" and "f". Only the
# hyphen case needs special handling.
_WORD_BOUNDARY_PATTERNS["bed"] = (
    re.compile(r"\bbed\b(?!-)", re.IGNORECASE),
    "shelter",
)

# Phrases that mean "where I am" but don't contain an actual location.
# When detected, we store a sentinel so the follow-up logic knows to ask
# for a real neighborhood/borough instead of running a broken query.
_NEAR_ME_PHRASES = [
    "near me",
    "close to me",
    "around me",
    "close to",
    "close by",
    "nearby",
    "closest",
    "around here",
    "in my area",
    "where i am",
]

# Sentinel value stored when user says "near me" without a real location.
NEAR_ME_SENTINEL = "__near_me__"


# TODO: Current implementation assumes a single service intent.
# This will fail for multi-intent queries (e.g., "food and housing").
# Consider returning a list of services instead of a single value.

# Notable sub-types — specific keywords whose user-facing label differs
# from the parent category label. When one of these matches, the
# confirmation message echoes the user's specific term instead of the
# generic category name. Only includes keywords where the generic label
# would confuse the user (e.g., "dental" → "health care" loses context).
_NOTABLE_SUB_TYPES = {
    # medical sub-types
    "dental": "dental care",
    "dentist": "dental care",
    "eye doctor": "vision care",
    "vision": "vision care",
    "glasses": "vision care",
    "urgent care": "urgent care",
    "std testing": "STD testing",
    "sti testing": "STI testing",
    "hiv testing": "HIV testing",
    "vaccination": "vaccinations",
    "vaccine": "vaccinations",
    # medical — HIV / harm reduction (Phase 1 audit)
    "harm reduction": "harm reduction services",
    "needle exchange": "needle exchange",
    "syringe exchange": "syringe exchange",
    "hepatitis": "hepatitis services",
    "hep c": "hepatitis C services",
    # medical — pregnancy (Phase 1 audit)
    "prenatal care": "prenatal care",
    "prenatal": "prenatal care",
    "maternity": "maternity services",
    "postpartum": "postpartum care",
    # medical — chronic conditions / medications (peer_diabetic_insulin fix)
    # Without these, "I'm diabetic and ran out of insulin" shows as
    # generic "health care" in the confirmation — the user feels unheard.
    "insulin": "diabetes / insulin care",
    "diabetic": "diabetes / insulin care",
    "diabetes": "diabetes / insulin care",
    "blood sugar": "diabetes care",
    "inhaler": "asthma care",
    "asthma": "asthma care",
    # "dialysis" removed — DB verified April 16, 2026: 0 service descriptions
    # match dialysis|kidney|renal. Keeping the sub-type would set
    # service_detail="dialysis services" in the confirmation, but the
    # description filter no longer exists, so it wouldn't narrow results.
    "epipen": "allergy / EpiPen care",
    # mental_health sub-types
    "substance abuse": "substance abuse services",
    "addiction": "addiction services",
    "rehab": "rehab services",
    "recovery": "recovery services",
    "aa meeting": "AA meetings",
    "na meeting": "NA meetings",
    "counseling": "counseling",
    "therapy": "therapy",
    # mental_health — substance treatment (Phase 1 audit)
    "substance use treatment": "substance use treatment",
    "treatment program": "treatment programs",
    "treatment center": "treatment centers",
    "inpatient": "inpatient treatment",
    "outpatient": "outpatient treatment",
    "sober living": "sober living",
    "halfway house": "halfway houses",
    "anger management": "anger management",
    # mental_health — detox (missing from Phase 1 audit)
    "detox": "detox",
    "detoxification": "detox",
    # legal sub-types
    "immigration": "immigration services",
    "eviction": "eviction help",
    "asylum": "asylum services",
    # legal — DV services (Phase 1 audit)
    "domestic violence help": "domestic violence services",
    "dv services": "domestic violence services",
    "abuse counseling": "abuse counseling",
    "order of protection": "order of protection",
    # legal — immigration advanced (Phase 1 audit)
    "citizenship": "citizenship services",
    "naturalization": "naturalization services",
    "daca": "DACA services",
    # personal_care sub-types
    "shower": "showers",
    "laundry": "laundry",
    "haircut": "haircuts",
    "toiletries": "toiletries",
    "restroom": "restrooms",
    "bathroom": "restrooms",
    # clothing sub-types — maps to clothingOccasion attribute filter
    # (DB verified April 16, 2026: 62 Everyday, 28 Job Interview services)
    "interview clothes": "professional clothing",
    "interview clothing": "professional clothing",
    "interview outfit": "professional clothing",
    "professional clothes": "professional clothing",
    "professional clothing": "professional clothing",
    "business clothes": "professional clothing",
    "business clothing": "professional clothing",
    "work clothes": "professional clothing",
    "casual clothes": "casual clothing",
    "casual clothing": "casual clothing",
    # food sub-types
    "soup kitchen": "soup kitchens",
    "food pantry": "food pantries",
    "groceries": "groceries",
    # other — new clusters (Phase 1 audit)
    "financial help": "financial services",
    "financial advice": "financial services",
    "financial advisor": "financial advisors",
    "money management": "money management",
    "budgeting": "budgeting help",
    "financial literacy": "financial literacy",
    "help with money": "financial services",
    "bad with money": "financial services",
    "money problems": "financial services",
    # other — benefits enrollment (labels for confirmation messages)
    "benefits": "benefits enrollment",
    "food stamps": "food stamps / SNAP",
    "ebt": "EBT / food stamps",
    "medicaid": "Medicaid enrollment",
    "social security": "Social Security",
    "public assistance": "public assistance",
    "cash assistance": "cash assistance",
    "welfare": "public assistance",
    "english class": "English classes",
    "english classes": "English classes",
    "learn english": "English classes",
    "high school equivalency": "GED programs",
    "adult education": "adult education",
    "computer class": "computer classes",
    "computer skills": "computer classes",
    "digital literacy": "digital literacy",
    "rental assistance": "rental assistance",
    "help with rent": "rental assistance",
    "eviction prevention": "eviction prevention",
    "housing voucher": "housing vouchers",
    "housing assistance": "housing assistance",
    "housing program": "housing programs",
    "section 8": "Section 8 vouchers",
    "affordable housing": "affordable housing",
    "housing lottery": "housing lottery",
    "nycha": "NYCHA housing",
    "housing connect": "Housing Connect",
    "homeless prevention": "homeless prevention programs",
    "senior center": "senior services",
    "senior services": "senior services",
    "older adult": "senior services",
    "reentry": "re-entry services",
    "re-entry": "re-entry services",
    "released from jail": "re-entry services",
    "parenting class": "parenting classes",
    "baby supplies": "baby supplies",
    "diapers": "baby supplies",
    "disability services": "disability services",
    "accessible services": "accessibility services",
    "health insurance": "health insurance enrollment",
    "insurance enrollment": "insurance enrollment",
    "transportation help": "transportation help",
    "access-a-ride": "Access-A-Ride help",
    "lgbtq services": "LGBTQ services",
    "lgbtq support": "LGBTQ support",
    # Word-boundary keywords (Phase 4) — these need sub-type labels
    # so service_detail is set and narrowing/description filter triggers.
    "esl": "English classes",
    "ged": "GED programs",
    "hiv": "HIV services",
    "prep": "PrEP services",
    "ssi": "disability services",
    "ssdi": "disability services",
    "syep": "SYEP programs",
    "sober": "sober living",
    "parole": "re-entry services",
    "probation": "re-entry services",
}

# ---------------------------------------------------------------------------
# GENDER / LGBTQ IDENTITY EXTRACTION
# ---------------------------------------------------------------------------
# Extract ONLY when the user explicitly states their gender or identity.
# NEVER infer gender from name, voice, or phrasing.
# Maps stated identity to DB-compatible values for eligibility filtering.

_GENDER_PHRASES = {
    # Female-identifying
    "woman": "female", "female": "female", "girl": "female",
    "mom": "female", "mother": "female",

    # Male-identifying
    "man": "male", "male": "male", "guy": "male",
    "dad": "male", "father": "male",

    # Trans-identifying — map to the gender they identify AS
    # AND flag as transgender for LGBTQ-specific services
    "transwoman": "female", "trans woman": "female",
    "transman": "male", "trans man": "male",
    "transgender": "transgender",
    "mtf": "female", "ftm": "male",

    # Non-binary / gender non-conforming
    "nonbinary": "nonbinary", "non-binary": "nonbinary",
    "non binary": "nonbinary", "enby": "nonbinary",
    "genderqueer": "nonbinary", "gender fluid": "nonbinary",
    "agender": "nonbinary",

    # LGBTQ umbrella — doesn't specify gender but indicates need
    # for LGBTQ-affirming services
    "lgbtq": "lgbtq", "lgbtq+": "lgbtq", "lgbt": "lgbtq",
    "queer": "lgbtq", "gay": "lgbtq", "lesbian": "lgbtq",
    "bisexual": "lgbtq",
}

# Sorted longest-first so "trans woman" matches before "woman",
# "non-binary" before "non", etc.
_GENDER_PHRASES_SORTED = sorted(_GENDER_PHRASES.items(), key=lambda x: len(x[0]), reverse=True)

# Patterns that indicate the user is talking about THEMSELVES
# (vs. referring to someone else). We require one of:
#   - "I am a ...", "I'm a ...", "im a ..."
#   - Bare identity term as the subject: "transman, need shelter"
#   - Comma-separated list with age: "21, LGBTQ, in Soho"
_SELF_REFERENCE_RE = re.compile(
    r"(?:"
    r"\bi[' ]?m\s+(?:a\s+)?"          # "I'm a", "I'm", "im a"
    r"|\bi am\s+(?:a\s+)?"            # "I am a", "I am"
    r"|\bas a\s+"                       # "as a trans man"
    r"|\b\d{1,2}\s*,\s*"               # "21, LGBTQ" (age-prefixed)
    r")",
    re.IGNORECASE,
)


def _extract_gender(text: str) -> Optional[str]:
    """Extract gender or LGBTQ identity from user message.

    Returns one of: 'male', 'female', 'transgender', 'nonbinary',
    'lgbtq', or None. Only extracts when explicitly stated.
    """
    lower = text.lower()

    for phrase, value in _GENDER_PHRASES_SORTED:
        pos = lower.find(phrase)
        if pos == -1:
            continue

        end = pos + len(phrase)

        # Word boundary check: the character before/after must be
        # non-alphanumeric (or start/end of string)
        if pos > 0 and lower[pos - 1].isalpha():
            continue
        if end < len(lower) and lower[end].isalpha():
            # Exception: allow "transman" (no space) as a single word
            if phrase not in ("transman", "transwoman"):
                continue

        # Guard against "the man at the counter" — check that the gender
        # term is used as self-identification, not referring to someone else.
        # For short unambiguous identity terms (lgbtq, trans*, nonbinary, etc.)
        # we trust the match. For common words (man, woman, guy, girl, mom, dad)
        # we require a self-reference pattern nearby.
        _COMMON_GENDER_WORDS = {"man", "woman", "guy", "girl", "male", "female",
                                 "mom", "mother", "dad", "father"}
        if phrase in _COMMON_GENDER_WORDS:
            # Check for self-reference pattern in the ~30 chars before the match
            prefix = text[max(0, pos - 30):pos + end]
            if not _SELF_REFERENCE_RE.search(prefix):
                continue

        return value

    return None


# ---------------------------------------------------------------------------
# Negation detection for service keywords (Fix 2)
# ---------------------------------------------------------------------------

_NEGATION_PREFIXES = [
    "not ", "no ", "don't want ", "dont want ", "don't need ", "dont need ",
    "forget ", "forget about ", "skip ", "instead of ", "rather than ",
    "no more ", "not looking for ", "not interested in ",
]

# Only articles and close determiners/possessives are safe as fillers.
# Quantifiers ("all", "any", "some") and prepositions ("about") are
# excluded because they create too much distance between the negation
# word and the keyword, causing false negatives like:
#   "not all food is free"  →  strip "all" → "not" → false negation
#   "there is not a shelter" → strip "a" → "is not" → false negation
_NEGATION_FILLER_WORDS = frozenset({
    "the", "my", "this", "that",
})


def _is_negated(text: str, match_pos: int) -> bool:
    """Check if a keyword match at match_pos is preceded by a negation.

    Looks at the 25 characters before the match position for negation
    prefixes. Handles both direct adjacency ("forget food") and a
    single intervening article or determiner ("forget the food").

    Only strips ONE filler word to avoid chaining through multiple
    words and exposing coincidental negation prefixes (e.g.,
    "is not about the food" → strip "the" → strip "about" → "not").

    Examples:
        "not food, shelter"           → True  (direct)
        "I need food"                 → False
        "forget food, I need shelter" → True  (direct)
        "forget the food"             → True  (filler word "the")
        "skip my shelter"             → True  (filler word "my")
        "forget about the food"       → True  (filler "the", prefix "forget about")
        "not all food is free"        → False ("all" not a filler)
        "there is not a shelter"      → False ("a" not a filler)
    """
    # Look at the 25 chars before the match
    window_start = max(0, match_pos - 25)
    prefix = text[window_start:match_pos].lower().rstrip()

    # Direct check (existing behavior — handles "forget food")
    if any(prefix.endswith(neg.rstrip()) for neg in _NEGATION_PREFIXES):
        return True

    # Strip ONE trailing filler word and re-check.
    # Handles "forget the food", "not my shelter", "skip that food".
    parts = prefix.rsplit(None, 1)
    if len(parts) == 2 and parts[1] in _NEGATION_FILLER_WORDS:
        stripped = parts[0].rstrip()
        if any(stripped.endswith(neg.rstrip()) for neg in _NEGATION_PREFIXES):
            return True

    return False


# ---------------------------------------------------------------------------
# Contradiction signal detection (multiturn_change_mind fix)
# ---------------------------------------------------------------------------

# Phrases that signal the user is explicitly changing their mind.
# Only STRONG, unambiguous signals are included.  Common English
# words like "actually", "instead", and "wait" are excluded because
# they appear in non-contradicting multi-service messages:
#   "I actually need food and shelter"  — not a switch
#   "I need food instead of cooking"    — not a service switch
#   "Wait, I need food and shelter"     — not a switch
# The improved negation ("forget the food") and the existing
# confirm_change_service / confirm_deny handlers cover those cases.
_CONTRADICTION_SIGNALS = [
    "i changed my mind", "changed my mind",
    "never mind the", "never mind that", "nevermind the", "nevermind that",
    "forget that", "forget about", "scratch that",
    "no wait", "hold on",
]


def _find_contradiction_signal(text: str) -> int:
    """Return the position of an explicit change-of-mind signal, or -1.

    Used to reorder extracted services so the user's *new* intent becomes
    primary. Only fires when the signal appears BETWEEN two service
    keywords — "actually I need shelter" where shelter appears after
    "actually".
    """
    lower = text.lower()
    best_pos = -1
    for signal in _CONTRADICTION_SIGNALS:
        pos = lower.find(signal)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
    return best_pos


# ---------------------------------------------------------------------------
# Service-need priority ("Housing First" ordering)
# ---------------------------------------------------------------------------
# When a message mentions multiple service categories, we sort by priority
# tier first and text position second — so "I need food and a place to
# sleep" extracts shelter as the primary intent even though food came
# first textually. Explicit contradiction signals ("actually, I need X")
# still override this (handled after the sort, below).
#
# Research grounding:
#   * HUD Housing First principle — stable housing is the foundation;
#     other services are more effective once housing is in place.
#   * Fleury et al. 2021 (PMC) — Maslow adapted for homelessness:
#     basic needs > health > stability > growth.
#   * SAMHSA 2025 crisis care — safety above physiological when a
#     safety signal is present.
#   * Zheng et al. 2016 — safety above physiological in mental-health
#     contexts.
#
# Tiers (lower number = higher priority):
#   Tier 1 — life / safety:                shelter, medical
#   Tier 2 — survival + behavioral health: food, mental_health
#   Tier 3 — physiological, non-critical:  clothing, personal_care
#   Tier 4 — stability:                    legal, employment
#   Tier 5 — support:                      other
#
# If a new service category gets added to SERVICE_KEYWORDS, add it
# here too. Missing categories fall back to priority 99 (lowest).
# test_audit_regression + test_hybrid_multi_intent both enforce this.
_SERVICE_NEED_PRIORITY = {
    "shelter": 1,
    "medical": 1,
    "food": 2,
    "mental_health": 2,
    "clothing": 3,
    "personal_care": 3,
    "legal": 4,
    "employment": 4,
    "other": 5,
}


def _extract_all_service_types(text: str) -> list[tuple[str, Optional[str]]]:
    """Extract ALL service type categories from a message.

    Returns a list of (service_type, service_detail) tuples, deduplicated
    by category. Order reflects first appearance in text.

    Negation-aware: keywords preceded by "not", "forget", "don't want",
    "instead of", "skip" are excluded. This prevents "not food, shelter"
    from extracting "food" as the primary service.

    Contradiction-aware: when the message contains explicit change-of-mind
    signals ("actually", "instead", "I changed my mind"), services
    appearing AFTER the signal are promoted to primary. This handles
    "Actually forget the food, I really need shelter" → [("shelter", ...)].

    Examples:
        "I need food and shelter" → [("food", None), ("shelter", None)]
        "dental care in Brooklyn" → [("medical", "dental care")]
        "not food, shelter" → [("shelter", None)]
        "actually forget the food, I need shelter" → [("shelter", None)]
        "hello" → []
    """
    lower = text.lower()
    # Each entry: (text_position, service_type, service_detail)
    found = []
    seen_categories = set()
    matched_spans = []  # track matched positions to avoid sub-matches

    # Build a flat list of (keyword, service_type) sorted longest-first.
    # This ensures "mental health" matches before "health",
    # "food bank" before "food", etc.
    all_keywords = []
    for service, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            all_keywords.append((kw, service))
    all_keywords.sort(key=lambda x: len(x[0]), reverse=True)

    for keyword, service in all_keywords:
        # Scan for ALL occurrences of this keyword, not just the first.
        # This fixes the bug where find() returns a position inside an
        # already-matched longer keyword, causing later occurrences to
        # be missed (e.g., "food stamps and food" — first "food" at pos 7
        # is inside "food stamps", but "food" at pos 23 is independent).
        search_start = 0
        while True:
            pos = lower.find(keyword, search_start)
            if pos == -1:
                break

            end = pos + len(keyword)

            # Skip if this position overlaps with an already-matched span
            if any(pos < ms_end and end > ms_start
                   for ms_start, ms_end in matched_spans):
                search_start = pos + 1
                continue

            # Skip if this keyword is negated
            if _is_negated(lower, pos):
                matched_spans.append((pos, end))  # block sub-matches
                search_start = end
                break

            # Record the span (even for already-seen categories, to block
            # sub-matches at this position)
            matched_spans.append((pos, end))

            if service not in seen_categories:
                detail = _NOTABLE_SUB_TYPES.get(keyword)
                found.append((pos, service, detail))
                seen_categories.add(service)

            # Move past this match
            search_start = end
            break  # found a valid (non-overlapping) position for this keyword

    # Fallback: check collision-prone keywords using word boundaries
    for kw, (pattern, service) in _WORD_BOUNDARY_PATTERNS.items():
        if service not in seen_categories:
            m = pattern.search(text)
            if m:
                detail = _NOTABLE_SUB_TYPES.get(kw)
                found.append((m.start(), service, detail))
                seen_categories.add(service)

    # Sort by (priority tier, text position). Housing First / Maslow
    # grounding: a higher-priority service wins even if it was mentioned
    # later in the message. Within a tier, text position breaks the tie
    # so "I need a doctor and a bed tonight" (both tier 1) keeps medical
    # primary. See _SERVICE_NEED_PRIORITY above for the tier table and
    # the research citations that informed it.
    found.sort(key=lambda x: (_SERVICE_NEED_PRIORITY.get(x[1], 99), x[0]))

    # Contradiction reordering: when the user signals a change of mind
    # ("actually", "instead", "I changed my mind") and multiple services
    # were found, promote services that appear AFTER the signal.
    # "I need food... actually, I need shelter" → shelter becomes primary.
    if len(found) > 1:
        signal_pos = _find_contradiction_signal(text)
        if signal_pos >= 0:
            post_signal = [(p, s, d) for p, s, d in found if p > signal_pos]
            pre_signal = [(p, s, d) for p, s, d in found if p <= signal_pos]
            if post_signal:
                found = post_signal + pre_signal

    return [(svc, detail) for _, svc, detail in found]



def _extract_location(text: str) -> Optional[str]:
    lower = text.lower()

    # Check for "near me" phrases first — these are NOT real locations.
    # Important: check these BEFORE the preposition patterns so that
    # "near me" doesn't fall through to the "near <location>" pattern.
    for phrase in _NEAR_ME_PHRASES:
        if phrase in lower:
            # But only if there's no known location after the "near me" phrase.
            # "Food near me in Brooklyn" should extract Brooklyn, not sentinel.
            remainder = lower[lower.index(phrase) + len(phrase):]
            has_real_location_after = any(loc in remainder for loc in _KNOWN_LOCATIONS)
            if not has_real_location_after:
                return NEAR_ME_SENTINEL

    # Preposition + known location: "in Brooklyn", "near Queens",
    # "around Harlem", "by Midtown", "from the Bronx"
    # First, try to match a preposition followed by a KNOWN location.
    # This prevents the greedy capture bug where "in East New York but
    # they can't keep me anymore" grabs way past the location name.
    for loc in _KNOWN_LOCATIONS:
        pattern = r"\b(?:in|near|around|by|from|over in|out in)\s+" + re.escape(loc) + r"\b"
        if re.search(pattern, lower):
            return loc

    # Fallback: preposition + short capture (max 25 chars, stop at common
    # stop words to prevent grabbing sentence fragments)
    prep_match = re.search(
        r"\b(?:in|near|around|by|from|over in|out in)\s+"
        r"([a-zA-Z][a-zA-Z\s\-]{1,24}?)"
        r"(?:\s+(?:but|and|or|that|who|where|when|for|to|i|my|the|they|we|it|is|are|was|can|do)\b|[,.\?!]|$)",
        text,
        re.IGNORECASE,
    )
    if prep_match:
        candidate = prep_match.group(1).strip()
        candidate_lower = candidate.lower()
        # Filter out non-location phrases.
        # These are words that commonly follow prepositions ("by", "in")
        # but are NOT location names. Without this filter, messages like
        # "Sort by recently verified" extract location="recently verified".
        non_locations = [
            "need", "trouble", "danger", "a", "the", "my", "your",
            "here", "there", "me", "help", "this",
            # Sort/filter UI commands: "Sort by recently verified"
            "recently", "most", "sort", "all", "any", "every",
            # Temporal: "by tomorrow", "by next week"
            "tomorrow", "next", "last", "today",
            # Misc non-locations that follow prepositions
            "now", "then", "someone", "anyone", "myself",
            # Common nouns after "by" that are never locations
            # (verified: none conflict with _KNOWN_LOCATIONS)
            "name", "distance", "rating", "category", "phone",
            "date", "open", "close", "email", "text",
        ]
        if candidate_lower.split()[0] not in non_locations:
            return candidate

    # Known NYC boroughs and neighborhoods (bare mention without preposition)
    for loc in _KNOWN_LOCATIONS:
        if loc in lower:
            return loc

    # NYC zip codes → map to neighborhood or borough.
    # Checked last because "10035" is unambiguous and doesn't need
    # preposition context. Covers the most common zip codes for the
    # population this chatbot serves.
    zip_match = _extract_nyc_zip(text)
    if zip_match:
        return zip_match

    return None


# Known NYC locations — used for both bare-mention extraction and
# the "near me" override check above.
# Sorted longest-first so "east new york" matches before "new york",
# "long island city" before "island", etc.
_KNOWN_LOCATIONS = [
    # Multi-word (longest first)
    "long island city", "jackson heights", "washington heights",
    "financial district", "bedford-stuyvesant", "prospect heights",
    "lower east side", "upper west side", "upper east side",
    "east new york", "east village", "west village", "east harlem",
    "midtown east", "midtown west", "times square",
    "port authority", "penn station", "grand central",
    "crown heights", "cobble hill", "sunset park",
    "bay ridge", "far rockaway", "fort greene",
    "park slope", "red hook", "south bronx", "mott haven",
    "hunts point", "little italy", "battery park",
    "hells kitchen", "hell's kitchen", "kips bay",
    "murray hill", "staten island", "the bronx",
    # Single/short words
    "manhattan", "brooklyn", "queens", "bronx",
    "harlem", "midtown", "soho", "tribeca", "chelsea",
    "williamsburg", "bushwick", "bed-stuy", "flatbush",
    "brownsville", "inwood", "gramercy", "chinatown",
    "nolita", "noho", "dumbo",
    "astoria", "flushing", "jamaica", "ridgewood",
    "woodside", "sunnyside", "corona", "elmhurst",
    "fordham", "morrisania",
]


# ---------------------------------------------------------------------------
# NYC ZIP CODE → NEIGHBORHOOD MAPPING
# ---------------------------------------------------------------------------
# Maps NYC zip codes to neighborhood names that match _KNOWN_LOCATIONS
# (and NEIGHBORHOOD_CENTERS in query_executor.py for proximity search).
# Covers the most common zip codes for the population this chatbot serves.
# Zips not in this table fall back to borough based on range.

_NYC_ZIP_TO_NEIGHBORHOOD = {
    # Manhattan — East Harlem / Harlem (high service concentration)
    "10026": "harlem", "10027": "harlem", "10030": "harlem",
    "10037": "harlem", "10039": "harlem",
    "10029": "east harlem", "10035": "east harlem",
    # Manhattan — Washington Heights / Inwood
    "10031": "washington heights", "10032": "washington heights",
    "10033": "washington heights", "10034": "inwood", "10040": "washington heights",
    # Manhattan — Lower East Side / East Village / Chinatown
    "10002": "lower east side", "10003": "east village", "10009": "east village",
    "10013": "chinatown",
    # Manhattan — Chelsea / Hell's Kitchen / Midtown
    "10001": "chelsea", "10011": "chelsea",
    "10018": "midtown", "10019": "midtown west", "10036": "midtown",
    "10016": "murray hill", "10017": "midtown east",
    # Manhattan — Other
    "10004": "financial district", "10005": "financial district",
    "10006": "financial district", "10007": "financial district",
    "10012": "soho", "10014": "west village",
    "10010": "gramercy", "10021": "upper east side",
    "10023": "upper west side", "10024": "upper west side",
    "10025": "upper west side", "10028": "upper east side",
    "10038": "financial district",
    # Bronx — South Bronx / Mott Haven (high need area)
    "10451": "mott haven", "10452": "south bronx", "10453": "south bronx",
    "10454": "mott haven", "10455": "mott haven", "10456": "morrisania",
    "10457": "fordham", "10458": "fordham", "10459": "hunts point",
    "10460": "morrisania", "10462": "bronx", "10463": "bronx",
    "10467": "bronx", "10468": "fordham", "10472": "hunts point",
    "10474": "hunts point",
    # Brooklyn
    "11201": "brooklyn", "11205": "fort greene", "11206": "williamsburg",
    "11207": "east new york", "11208": "east new york",
    "11211": "williamsburg", "11212": "brownsville", "11213": "crown heights",
    "11215": "park slope", "11216": "bed-stuy", "11217": "park slope",
    "11221": "bushwick", "11225": "crown heights", "11226": "flatbush",
    "11231": "red hook", "11232": "sunset park", "11233": "bed-stuy",
    "11234": "flatbush", "11236": "brownsville", "11237": "bushwick",
    "11238": "prospect heights", "11249": "williamsburg",
    # Queens
    "11101": "long island city", "11102": "astoria", "11103": "astoria",
    "11104": "sunnyside", "11105": "astoria", "11106": "astoria",
    "11354": "flushing", "11355": "flushing",
    "11368": "corona", "11369": "jackson heights",
    "11372": "jackson heights", "11373": "elmhurst",
    "11377": "woodside", "11378": "ridgewood",
    "11432": "jamaica", "11433": "jamaica", "11434": "jamaica",
    "11691": "far rockaway", "11692": "far rockaway",
    # Staten Island
    "10301": "staten island", "10302": "staten island",
    "10303": "staten island", "10304": "staten island",
    "10305": "staten island", "10310": "staten island",
    "10314": "staten island",
}

# Borough fallback ranges for zips not in the specific lookup.
# NYC zip code ranges: Manhattan 10001-10282, Bronx 10451-10475,
# Brooklyn 11201-11256, Queens 11001-11697, Staten Island 10301-10314.
_NYC_ZIP_BOROUGH_RANGES = [
    (10301, 10314, "staten island"),  # Check before Manhattan (overlapping range)
    (10451, 10475, "bronx"),          # Check before Manhattan
    (10001, 10282, "manhattan"),
    (11201, 11256, "brooklyn"),
    (11001, 11109, "queens"),
    (11351, 11697, "queens"),
]

_NYC_ZIP_RE = re.compile(r"\b(1[01]\d{3})\b")


def _extract_nyc_zip(text: str) -> Optional[str]:
    """Extract a NYC zip code and map it to a neighborhood or borough.

    Returns a neighborhood name from _KNOWN_LOCATIONS if the zip is
    recognized, or a borough name as a fallback.
    Returns None if the zip is not a valid NYC zip code.
    """
    match = _NYC_ZIP_RE.search(text)
    if not match:
        return None

    zip_code = match.group(1)

    # Specific neighborhood mapping (best results — enables proximity search)
    neighborhood = _NYC_ZIP_TO_NEIGHBORHOOD.get(zip_code)
    if neighborhood:
        return neighborhood

    # Borough fallback based on zip range
    zip_int = int(zip_code)
    for low, high, borough in _NYC_ZIP_BOROUGH_RANGES:
        if low <= zip_int <= high:
            return borough

    return None


def _extract_urgency(text: str) -> Optional[str]:
    lower = text.lower()
    if any(x in lower for x in [
        "tonight", "urgent", "asap", "right now", "immediately",
        "emergency", "today", "before dark", "freezing",
    ]):
        return "high"
    # Medication depletion — running out of essential medication is
    # medically urgent even without explicit urgency words.
    # "Ran out of insulin" is as dangerous as "I need shelter tonight."
    #
    # NOTE: "need my medication" and "need my medicine" were intentionally
    # excluded — they self-match because "medication"/"medicine" appear in
    # both the depletion AND medication word lists, causing false positives
    # for routine requests like "I need my medication refilled."
    if any(x in lower for x in [
        "ran out of", "run out of", "running out of", "out of my",
        "don't have my", "dont have my", "lost my medication",
        "lost my medicine",
        "no more", "can't get my", "cant get my",
    ]) and any(x in lower for x in [
        "insulin", "medication", "medicine", "prescription",
        "inhaler", "epipen", "pills", "meds",
    ]):
        return "high"
    if any(x in lower for x in ["soon", "this week"]):
        return "medium"
    return None


def _extract_age(text: str) -> Optional[int]:
    # e.g. "I am 17", "age 22", "22 years old", "I'm 22", "19-year-old", "21, LGBTQ"
    patterns = [
        r"\bi[' ]?m (\d{1,3})\b",
        r"\bi am (\d{1,3})\b",
        r"\bage (\d{1,3})\b",
        r"\b(\d{1,3}) years old\b",
        # Hyphenated: "19-year-old", "21-yr-old"
        r"\b(\d{1,2})-?year-?old\b",
        r"\b(\d{1,2})-?yr-?old\b",
        # Bare number at start or after newline, followed by comma/space+context
        # "21, LGBTQ" or "19, with a toddler"
        r"(?:^|\n)(\d{1,2})\s*,",
        # Bare number after a comma, followed by comma/space+context
        # "aging out of foster care, 21, in the Bronx"
        r",\s*(\d{1,2})\s*,",
    ]
    for p in patterns:
        m = re.search(p, text.lower())
        if m:
            age = int(m.group(1))
            if 0 < age < 120:
                return age
    return None


def _extract_family_status(text: str) -> Optional[str]:
    """Extract family composition from user message.

    Returns one of: 'with_children', 'with_family', 'alone', or None.
    Only extracts when clearly stated — avoids guessing.

    Check order matters: children > family > alone.
    "single mother" must match children before "single" matches alone.
    """
    lower = text.lower()

    # With children — most specific, check first
    child_phrases = [
        "with my kid", "with my kids", "with my child", "with my children",
        "have kids", "have children", "have a kid", "have a child",
        "my son", "my daughter", "my baby", "my toddler", "my infant",
        "two kids", "three kids", "four kids",
        "2 kids", "3 kids", "4 kids",
        "with a baby", "with a toddler", "with an infant",
        "my kids are", "my children are",
        "year old daughter", "year old son", "year old child",
        # "pregnant" removed — pregnancy ≠ with_children. Use pregnant population instead.
        # "single parent/mother/father" = has children, not alone
        "single mother", "single mom", "single father", "single dad",
        "single parent",
    ]
    for phrase in child_phrases:
        if phrase in lower:
            return "with_children"

    # With family — broader family unit
    # NOTE: "me and my" removed — too broad ("me and my friend" is not family)
    family_phrases = [
        "with my family", "for my family", "my family needs",
        "me and my family", "our family",
        "family shelter",  # explicit request for family shelter
        "with my partner", "with my wife",
        "with my husband", "with my spouse",
        "with my girlfriend", "with my boyfriend",
        "me and my wife", "me and my husband", "me and my partner",
    ]
    for phrase in family_phrases:
        if phrase in lower:
            return "with_family"

    # Alone — explicit statements
    # NOTE: "single" alone is ambiguous — only match exact "i'm single"
    # or the word in clear context. "single mother" is caught above.
    alone_phrases = [
        "i'm alone", "im alone", "i am alone",
        "by myself", "on my own", "just me",
        "no family", "no one with me",
        "nobody with me",
    ]
    for phrase in alone_phrases:
        if phrase in lower:
            return "alone"

    return None


# -----------------------------------------------------------------------
# POPULATION / IDENTITY CONTEXT
# -----------------------------------------------------------------------
# Cross-cutting attributes representing WHO the user is, not what
# service they need. A veteran searching for food gets veteran-friendly
# food pantries ranked higher. A disabled user gets accessible locations
# boosted. These modify ALL searches, not just shelter.

_POPULATION_PHRASES = {
    # Veteran — military service
    "veteran": "veteran",
    "military": "veteran",
    "served in the": "veteran",
    # "navy" retired — "navy blue sweater" false positive (REGEX_AUDIT). Semantic layer handles.
    "marines": "veteran",
    "air force": "veteran",
    "national guard": "veteran",
    "coast guard": "veteran",

    # Disabled — physical/cognitive disability
    "disabled": "disabled",
    "disability": "disabled",
    "wheelchair": "disabled",
    "blind": "disabled",
    "deaf": "disabled",
    "hearing impaired": "disabled",
    "mobility impaired": "disabled",
    "vision impaired": "disabled",
    "handicapped": "disabled",

    # Reentry — criminal justice involvement
    "just got out of jail": "reentry",
    "just got out of prison": "reentry",
    "released from jail": "reentry",
    "released from prison": "reentry",
    "released from rikers": "reentry",
    "out of rikers": "reentry",
    "on parole": "reentry",
    "on probation": "reentry",
    "formerly incarcerated": "reentry",
    # Run 24 eval gaps — felon/criminal record terminology
    "felon": "reentry",
    "felony": "reentry",
    "ex-felon": "reentry",
    "criminal record": "reentry",
    # "have a record" retired — "I have a record of all my meetings" false positive (REGEX_AUDIT)
    "been to prison": "reentry",
    "was in prison": "reentry",
    "got out of prison": "reentry",
    # "did time" retired — "I did time management training" false positive (REGEX_AUDIT)

    # Foster youth — aging out of or formerly in foster care
    # NOT reentry — foster care ≠ incarceration
    "foster care": "foster_youth",
    "aging out": "foster_youth",
    "aged out": "foster_youth",
    "aging out of foster": "foster_youth",
    "former foster": "foster_youth",
    "foster youth": "foster_youth",
    "out of the system": "foster_youth",
    "out the system": "foster_youth",

    # DV survivor — domestic violence
    "escaped abuse": "dv_survivor",
    "fleeing abuse": "dv_survivor",
    "abusive relationship": "dv_survivor",
    "domestic violence": "dv_survivor",
    "abusive partner": "dv_survivor",
    "abusive husband": "dv_survivor",
    "abusive wife": "dv_survivor",
    "abusive boyfriend": "dv_survivor",
    "abusive girlfriend": "dv_survivor",
    "fleeing domestic": "dv_survivor",

    # Pregnant
    "pregnant": "pregnant",
    "expecting a baby": "pregnant",
    "having a baby": "pregnant",

    # Senior — useful when age isn't stated as a number.
    # When age >= 62 is extracted, query_services() auto-adds senior.
    # "senior" standalone retired — "senior developer" false positive (REGEX_AUDIT).
    # Semantic layer handles via example utterances.
    "elderly": "senior",
    "older adult": "senior",
    "senior citizen": "senior",
}

# Phrases that contain population keywords but aren't identity statements.
# Checked BEFORE the keyword scan; if any guard matches, skip that keyword.
_POPULATION_FALSE_POSITIVES = {
    "veteran": {"veterans day", "veterans memorial", "veterans affairs office",
                "veterans administration"},
    "disabled": {"disabled my", "disabled the", "disabled it",
                 "account disabled", "was disabled"},
    "army": {"salvation army"},
    "blind": {"blind spot", "color blind", "blind date"},
    "deaf": {"deaf ears", "fell on deaf"},
}

# "army" and "vet" need word-boundary matching to avoid false positives
# ("salvation army", "veterinarian"). Handled via the false positive guards
# and word-boundary regex below.
_POPULATION_WORD_BOUNDARY = {
    "vet": "veteran",  # avoid "veterinarian", "veto"
    "army": "veteran",  # avoid "salvation army" (guarded above)
}


def _extract_populations(text: str) -> list[str]:
    """Extract population/identity context from user message.

    Returns a deduplicated list of population identifiers. May return
    multiple values (e.g., "disabled veteran" → ["veteran", "disabled"]).
    """
    lower = text.lower()
    found = set()

    # Check longer phrases first to prevent sub-match shadowing
    sorted_phrases = sorted(_POPULATION_PHRASES.keys(), key=len, reverse=True)

    for phrase in sorted_phrases:
        if phrase not in lower:
            continue

        population = _POPULATION_PHRASES[phrase]

        # Check false positive guards
        guards = _POPULATION_FALSE_POSITIVES.get(phrase, set())
        if any(guard in lower for guard in guards):
            continue

        found.add(population)

    # Word-boundary keywords (short words that need boundaries)
    for keyword, population in _POPULATION_WORD_BOUNDARY.items():
        if population in found:
            continue  # already matched via a longer phrase
        if re.search(rf"\b{keyword}\b", lower):
            # Check guards
            guards = _POPULATION_FALSE_POSITIVES.get(keyword, set())
            if not any(guard in lower for guard in guards):
                found.add(population)

    return sorted(found)  # sorted for deterministic output


def _extract_all_locations(text: str) -> list[tuple[int, str]]:
    """Extract ALL location matches from text with their positions.

    Returns a list of (text_position, location_name) tuples, sorted by
    position. Used for per-service location binding when multiple services
    and multiple locations appear in the same message.

    Example:
        "food in Brooklyn and shelter in Manhattan"
        → [(8, "brooklyn"), (30, "manhattan")]
    """
    lower = text.lower()
    found = []
    matched_spans = []

    # Preposition + known location (highest priority)
    for loc in _KNOWN_LOCATIONS:
        pattern = r"\b(?:in|near|around|by|from|over in|out in)\s+" + re.escape(loc) + r"\b"
        for m in re.finditer(pattern, lower):
            pos = m.start()
            end = m.end()
            # Skip overlapping matches
            if any(pos < ms_end and end > ms_start for ms_start, ms_end in matched_spans):
                continue
            matched_spans.append((pos, end))
            found.append((pos, loc))

    # Bare mention of known locations (only if not already found via preposition)
    for loc in _KNOWN_LOCATIONS:
        for m in re.finditer(r"\b" + re.escape(loc) + r"\b", lower):
            pos = m.start()
            end = m.end()
            if any(pos < ms_end and end > ms_start for ms_start, ms_end in matched_spans):
                continue
            matched_spans.append((pos, end))
            found.append((pos, loc))

    found.sort(key=lambda x: x[0])
    return found


# ---------------------------------------------------------------------------
# Organization name extraction (Gap 3)
# ---------------------------------------------------------------------------
# Curated list of well-known NYC social service organizations that users
# commonly ask about by name. This handles the regex path; the LLM extractor
# catches orgs not on this list.

_KNOWN_ORGS = {
    # Shelter & Housing
    "covenant house": "Covenant House",
    "safe horizon": "Safe Horizon",
    "ali forney": "Ali Forney Center",
    "ali forney center": "Ali Forney Center",
    "bowery residents": "Bowery Residents' Committee",
    "project renewal": "Project Renewal",
    "breaking ground": "Breaking Ground",
    "women in need": "Women In Need",
    "urban pathways": "Urban Pathways",
    "goddard riverside": "Goddard Riverside",
    # Food
    "city harvest": "City Harvest",
    "food bank for new york": "Food Bank For New York City",
    "food bank nyc": "Food Bank For New York City",
    # Health & Mental Health
    "mount sinai": "Mount Sinai",
    "beth israel": "Beth Israel",
    "realization center": "Realization Center",
    "ryan health": "Ryan Health",
    # Legal & Immigration
    "legal aid society": "Legal Aid Society",
    "unlocal": "UnLocal",
    "make the road": "Make the Road New York",
    "cabrini immigrant": "Cabrini Immigrant Services",
    # Multi-service
    "catholic charities": "Catholic Charities",
    "catholic worker": "Catholic Worker",
    "salvation army": "Salvation Army",
    "riseboro": "RiseBoro",
    "boom health": "BOOM! Health",
    "boom! health": "BOOM! Health",
    # Youth
    "the door a center": "The Door",
    # DV
    "family justice center": "Family Justice Center",
    # Government
    "department of homeless services": "Department of Homeless Services",
}

# Short org abbreviations that need word-boundary matching to avoid
# false positives (e.g., "win" in "I need to win", "path" in "on the path").
_KNOWN_ORG_ABBREVIATIONS = {
    "ymca": "YMCA",
    "ywca": "YWCA",
    "mrny": "Make the Road New York",
    "camba": "CAMBA",
    "dycd": "DYCD",
}

_ORG_ABBREV_PATTERN = _re.compile(
    r"\b(" + "|".join(_re.escape(k) for k in _KNOWN_ORG_ABBREVIATIONS) + r")\b",
    _re.IGNORECASE,
)


def _extract_org_name(text: str) -> str | None:
    """Extract a known organization name from user message.

    Returns the canonical org name if found, None otherwise.
    Uses exact substring match for multi-word names and word-boundary
    match for abbreviations to avoid false positives.
    """
    lower = text.lower()

    # Check multi-word org names (longest match first to avoid partial matches)
    for phrase, canonical in sorted(_KNOWN_ORGS.items(), key=lambda x: -len(x[0])):
        if phrase in lower:
            return canonical

    # Check abbreviations with word boundaries
    m = _ORG_ABBREV_PATTERN.search(lower)
    if m:
        return _KNOWN_ORG_ABBREVIATIONS[m.group(1).lower()]

    return None


# ---------------------------------------------------------------------------
# Requirement preference extraction (Gap 10)
# ---------------------------------------------------------------------------

_NO_REQUIREMENTS_PHRASES = [
    "walk-in only", "walk in only", "walkin only",
    "no referral", "no referral needed", "without referral",
    "no appointment", "no appointment needed", "without appointment",
    "don't need a referral", "dont need a referral",
    "don't need an appointment", "dont need an appointment",
    "no membership", "no registration",
    "walk-in welcome", "walk in welcome",
    "open to anyone", "open to all",
    "no requirements",
]


def _extract_no_requirements(text: str) -> bool:
    """Detect if user wants walk-in / no-referral services only.

    Returns True if the user explicitly asks for services without
    referral or appointment requirements. Returns False otherwise
    (default — don't filter).

    Note: "drop-in" and "walk-in clinic" are already SERVICE KEYWORDS
    (shelter and medical respectively). This extractor catches the
    REQUIREMENT preference, not the service type.
    """
    lower = text.lower()
    return any(phrase in lower for phrase in _NO_REQUIREMENTS_PHRASES)


def extract_slots(message: str) -> dict:
    all_types = _extract_all_service_types(message)
    all_locations = _extract_all_locations(message)

    service_type = None
    service_detail = None
    additional_services = []
    primary_location_override = None

    if all_types:
        service_type, service_detail = all_types[0]

        if len(all_types) > 1 and len(all_locations) > 1:
            # Per-service location binding: match each service to its
            # nearest location by text position. Keep the priority order
            # from _extract_all_service_types (primary first) — don't
            # re-sort by text position, or Housing First gets silently
            # overridden and the primary ends up duplicated in
            # additional_services. See test_food_brooklyn_shelter_manhattan.
            lower = message.lower()

            def _first_keyword_pos(svc: str) -> int:
                """Earliest text position where any keyword for `svc` appears."""
                best = -1
                for kw in SERVICE_KEYWORDS.get(svc, []):
                    pos = lower.find(kw)
                    if pos >= 0 and (best < 0 or pos < best):
                        best = pos
                return best

            # Primary: its closest location becomes the primary location,
            # so "food in Brooklyn and shelter in Manhattan" with shelter
            # as priority-primary returns location="manhattan" (not
            # "brooklyn" — which was the first-mentioned fallback).
            primary_pos = _first_keyword_pos(service_type)
            if primary_pos >= 0:
                closest = min(all_locations, key=lambda x: abs(x[0] - primary_pos))
                primary_location_override = closest[1]

            # Each remaining service: bind to its own closest location
            # and append to additional_services in priority order.
            for svc, detail in all_types[1:]:
                pos = _first_keyword_pos(svc)
                if pos >= 0:
                    closest = min(all_locations, key=lambda x: abs(x[0] - pos))
                    additional_services.append((svc, detail, closest[1]))
                else:
                    additional_services.append((svc, detail, None))
        else:
            # Single location or single service — no per-service binding needed
            additional_services = [(s, d, None) for s, d in all_types[1:]]

    # Location: prefer the primary-service-bound location if we computed
    # one (cross-borough case); otherwise fall back to first-mentioned,
    # then to the generic single-location extractor.
    if primary_location_override is not None:
        primary_location = primary_location_override
    elif all_locations:
        primary_location = all_locations[0][1]
    else:
        primary_location = _extract_location(message)

    # --- Extract gender and populations ---
    gender = _extract_gender(message)
    populations = _extract_populations(message)

    # Cross-populate: when a trans/LGBTQ phrase is detected in the
    # gender extraction, add "lgbtq" to _populations so that the
    # shelter enrichment fires correctly.
    #
    # Without this, "I am a transman" → gender="male" (correct for
    # eligibility filtering) but the LGBTQ enrichment at
    # rag/__init__.py:229 never fires because it checks
    # gender in ("lgbtq", "transgender", "nonbinary") — "male" ≠ any.
    #
    # The gender mapping is intentionally "transman" → "male" (the
    # identified gender), so we can't change that. Instead, we detect
    # the LGBTQ signal separately and carry it in _populations.
    _LGBTQ_SIGNAL_PHRASES = (
        "transman", "trans man", "transwoman", "trans woman",
        "ftm", "mtf", "transgender", "nonbinary", "non-binary",
        "non binary", "enby", "genderqueer", "gender fluid", "agender",
        "lgbtq", "lgbtq+", "lgbt", "queer", "gay", "lesbian", "bisexual",
    )
    lower = message.lower()
    if any(phrase in lower for phrase in _LGBTQ_SIGNAL_PHRASES):
        if "lgbtq" not in populations:
            populations.append("lgbtq")

    return {
        "service_type": service_type,
        "service_detail": service_detail,
        "additional_services": additional_services,
        "location": primary_location,
        "urgency": _extract_urgency(message),
        "age": _extract_age(message),
        "family_status": _extract_family_status(message),
        "_gender": gender,
        "_populations": populations,
        "org_name": _extract_org_name(message),
        "no_requirements": _extract_no_requirements(message),
        "_contradiction": _find_contradiction_signal(message) >= 0,
    }


def merge_slots(existing: dict, new_values: dict) -> dict:
    """Merge newly extracted slots into the existing session state.

    Contradiction-aware: when the extraction flags ``_contradiction``
    (the user said "actually", "instead", "I changed my mind", etc.)
    and the new service_type matches the existing one — meaning the
    negation layer didn't catch the old service — fall back to the
    first ``additional_services`` entry as the intended new service.
    This fixes the ``multiturn_change_mind`` eval scenario.
    """
    merged = dict(existing)

    # --- Contradiction promotion (defense-in-depth) ---
    # If negation + reordering already produced the correct primary
    # service_type, this block is a no-op.  It only fires when the
    # old service survived extraction AND additional_services holds
    # the user's real intent.
    _is_contradiction = new_values.get("_contradiction", False)
    if (_is_contradiction
            and new_values.get("service_type") is not None
            and new_values["service_type"] == existing.get("service_type")
            and new_values.get("additional_services")):
        additional = new_values["additional_services"]
        if additional:
            promoted_svc, promoted_detail = additional[0][0], additional[0][1]
            logger.info(
                f"Contradiction promotion: '{new_values['service_type']}' "
                f"→ '{promoted_svc}' (from additional_services)"
            )
            new_values = dict(new_values)
            new_values["service_type"] = promoted_svc
            new_values["service_detail"] = promoted_detail
            # Remove the promoted service from additional_services
            new_values["additional_services"] = additional[1:]

    for key, value in new_values.items():
        # additional_services is transient extraction metadata —
        # never persist it in session state.
        if key == "additional_services":
            continue
        # _contradiction is a transient extraction flag —
        # never persist it in session state.
        if key == "_contradiction":
            continue
        # _populations is a list — merge by union, not replace.
        if key == "_populations":
            if value:
                existing_pops = set(merged.get("_populations", []))
                existing_pops.update(value)
                merged["_populations"] = sorted(existing_pops)
            continue
        if value not in (None, "", []):
            # If user provides a real location, replace a previous "near me"
            if key == "location" and value != NEAR_ME_SENTINEL:
                merged[key] = value
            elif key == "location" and value == NEAR_ME_SENTINEL:
                # Always store the sentinel — the user explicitly said "near me",
                # "close by", etc. This should override a stale location from a
                # previous search. If we kept the old location, the user's
                # explicit request for proximity search would be silently ignored.
                merged[key] = value
            else:
                merged[key] = value

    # When service_type changes, clear stale service_detail so the
    # confirmation message doesn't show the old sub-type label.
    # Also clear _pending_confirmation — the old confirmation is
    # no longer valid.
    if (new_values.get("service_type") is not None
            and new_values["service_type"] != existing.get("service_type")):
        if new_values.get("service_detail") is None:
            merged.pop("service_detail", None)
        if _is_contradiction:
            merged.pop("_pending_confirmation", None)

    return merged


def is_enough_to_answer(slots: dict) -> bool:
    # Org name search: org_name alone is sufficient (location optional)
    if slots.get("org_name"):
        return True
    # Service type search: need service type + a real location
    has_service = bool(slots.get("service_type"))
    has_location = bool(
        slots.get("location")
        and slots["location"] != NEAR_ME_SENTINEL
    )
    return has_service and has_location


def next_follow_up_question(slots: dict) -> str:
    # Ask only ONE targeted question
    if not slots.get("service_type"):
        return "What kind of help do you need right now? I can search for food, shelter, clothing, personal care (showers, laundry, haircuts), health care, mental health, legal help, jobs, or other services like benefits and IDs."

    if not slots.get("location") or slots.get("location") == NEAR_ME_SENTINEL:
        return (
            "What neighborhood or borough are you in? "
            "This helps me find what's closest to you."
        )

    if slots.get("service_type") == "shelter" and not slots.get("age"):
        return (
            "How old are you? Some shelters have specific programs "
            "for youth or adults — this helps me match you. "
            "You can skip this if you'd rather not say."
        )

    if slots.get("service_type") == "shelter" and not slots.get("family_status"):
        return (
            "Are you on your own, or do you have others with you "
            "(children, family, partner)? Some shelters have "
            "specific options depending on your situation."
        )

    return "Could you share one more detail to help me narrow options?"
