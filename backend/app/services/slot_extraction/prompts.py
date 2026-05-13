"""Prompts and tool schema for the unified slot extractor.

Written during the `llm_slot_extractor` → `slot_extraction` migration
(Phase 1, April 2026). See UNIFIED_EXTRACTOR_MIGRATION.md rev 12 for full
design rationale.

The schema here differs from the legacy `_EXTRACT_SLOTS_TOOL` in
`llm_slot_extractor.py` in three approved ways:

    1. `service_detail` is now a declared field in the schema. The legacy
       tool didn't include it; `classify_unified` asked for it via plain
       JSON. Phase 0 decision (Option A) is to make it explicit with a
       canonical-form validator against `_NOTABLE_SUB_TYPES` in the
       merge layer.

    2. `additional_services` replaces `additional_service_types`. The
       legacy field was a `string[]`; the new field is a list of objects
       `{type, detail?, location?}` so the LLM can carry per-service
       location and detail when regex missed them. The regex side
       already produces 3-tuples of this shape.

    3. The narrative prompt's urgency hierarchy now places food at or
       above mental_health — a direct correction to the legacy prompt
       which listed mental_health first. Hunger undermines other
       interventions; a person in mental-health crisis who hasn't eaten
       still needs the food first.

The narrative prompt also now enumerates all 10 extractable fields in
its "extract ALL slots" directive (legacy prompt listed 7, which
correlated with `org_name` and several others silently going
unrequested — see Behavior #23 in the migration doc).
"""

# ---------------------------------------------------------------------------
# SERVICE-TYPE ENUM
# ---------------------------------------------------------------------------
# Must stay in sync with:
#   - slot_extractor._SERVICE_NEED_PRIORITY (regex side)
#   - chatbot/__init__.py routing
#   - merge._VALID_SERVICE_TYPES (validator set)

_SERVICE_TYPE_ENUM = [
    "food", "shelter", "clothing", "personal_care",
    "medical", "mental_health", "legal", "employment",
    # Promoted out of `other` per TAXONOMY_AUDIT_MAY2026.md §IX Ticket C
    # (Phase B). DB: 101 services at `Other service › Education` leaf +
    # ~80-130 additional name-pattern matches against `Other service`
    # parent-direct (ESL/GED/adult-education/etc.) via the OR'd query
    # template filter. See PHASE_B_PROMOTION_PLAN.md.
    "education",
    "other",
]

# ---------------------------------------------------------------------------
# TOOL SCHEMA
# ---------------------------------------------------------------------------
# Claude function-calling schema for slot extraction. Used by BOTH the
# short-path LLM call and the narrative-path LLM call — the prompts
# differ, but the tool contract is identical.

_EXTRACT_SLOTS_TOOL = {
    "name": "extract_intake_slots",
    "description": (
        "Extract structured service request information from a user's message. "
        "The user is seeking free social services in New York City."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "service_type": {
                "type": "string",
                "enum": _SERVICE_TYPE_ENUM,
                "description": (
                    "The primary type of service the user is looking for. "
                    "If the user mentions multiple needs, put the most urgent "
                    "or first-mentioned here. "
                    "food = meals, pantries, groceries. "
                    "shelter = housing, place to sleep, drop-in centers, "
                    "somewhere safe, transitional housing. "
                    "clothing = clothes, coats, shoes. "
                    "personal_care = showers, laundry, haircuts, hygiene. "
                    "medical = doctors, clinics, dental, vision, detox / "
                    "substance-use / addiction / rehab. "
                    "mental_health = counseling, therapy, depression, "
                    "anxiety, support groups (NOT substance use). "
                    "legal = lawyers, immigration, eviction, legal aid. "
                    "employment = jobs, resume help, training. "
                    "education = ESL, GED, adult education, literacy, "
                    "computer classes, citizenship classes, college prep. "
                    "other = benefits, SNAP, IDs, birth certificates, free phones."
                ),
            },
            "service_detail": {
                "type": "string",
                "description": (
                    "Narrow sub-type of the primary service, when the user "
                    "indicates one (e.g., they mentioned a specific medical "
                    "condition, a specific legal matter, a specific kind of "
                    "food program, a specific benefit). Use a short phrasing "
                    "in the user's own words rather than inventing "
                    "terminology. The merge layer maps the output to a "
                    "canonical value downstream or drops it. Omit if the "
                    "user didn't specify a sub-type, or if you're not sure."
                ),
            },
            "additional_services": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": _SERVICE_TYPE_ENUM,
                            "description": "The service type.",
                        },
                        "detail": {
                            "type": "string",
                            "description": (
                                "Sub-type for this service, when the user "
                                "indicates one. Same canonical form as "
                                "service_detail. Omit if not specified."
                            ),
                        },
                        "location": {
                            "type": "string",
                            "description": (
                                "The location for this service, if the user "
                                "stated a different location than the primary "
                                "service. Omit if same as the primary location."
                            ),
                        },
                    },
                    "required": ["type"],
                },
                "description": (
                    "Any additional service types the user needs beyond the "
                    "primary one. For example, if the user says 'I need food "
                    "in Brooklyn and shelter in Manhattan,' service_type is "
                    "'food' and additional_services is "
                    "[{\"type\": \"shelter\", \"location\": \"Manhattan\"}]. "
                    "Only include services the user explicitly or clearly "
                    "implicitly requests. Do not infer services that weren't "
                    "mentioned. Do not repeat the primary service_type here."
                ),
            },
            "location": {
                "type": "string",
                "description": (
                    "The NYC borough or neighborhood where the user wants services. "
                    "Extract ONLY the location name — never include surrounding words. "
                    "For example, 'in East New York but they can't keep me' → 'East New York'. "
                    "Extract the INTENDED location, not where they currently are if different. "
                    "For example, 'I'm in Queens but need food in Brooklyn' → 'Brooklyn'. "
                    "Use the neighborhood or borough name as stated by the user."
                ),
            },
            "age": {
                "type": "integer",
                "description": (
                    "The age of the person who needs services. "
                    "May be the user or someone they're asking about "
                    "(e.g., 'my son is 12' → 12). "
                    "Must be between 1 and 110."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": ["high", "medium"],
                "description": (
                    "high = tonight, right now, urgent, emergency, ASAP, "
                    "no place to sleep, can't stay, fleeing, not safe. "
                    "medium = soon, this week. "
                    "Omit if no urgency indicated."
                ),
            },
            "gender": {
                "type": "string",
                "description": (
                    "The gender of the person who needs services, if mentioned. "
                    "Used for gendered shelters and services."
                ),
            },
            "family_status": {
                "type": "string",
                "enum": ["with_children", "with_family", "alone"],
                "description": (
                    "with_children = user has children, kids, or a baby. "
                    "with_family = user is with a partner, spouse, or other family. "
                    "alone = user explicitly says they are alone or by themselves. "
                    "Pregnancy does NOT count as with_children — use the 'pregnant' population tag instead. "
                    "Omit if not mentioned."
                ),
            },
            "populations": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "veteran", "disabled", "reentry",
                        "dv_survivor", "pregnant", "senior",
                        "foster_youth", "immigration",
                    ],
                },
                "description": (
                    "Identity/context attributes for the person seeking services. "
                    "These are WHO they are, not WHAT service they need. "
                    "veteran = military service (army, navy, marines, etc.). "
                    "disabled = physical or cognitive disability, wheelchair user. "
                    "reentry = released from jail/prison, on parole/probation. "
                    "Do NOT use reentry for foster care — use foster_youth instead. "
                    "Do NOT use reentry for undocumented or asylum status — use immigration instead. "
                    "foster_youth = in foster care, aging out of foster care, aged out, former foster youth. "
                    "immigration = undocumented, asylum seeker, refugee, recently arrived, "
                    "needs help with papers/visa/green card/citizenship/deportation. "
                    "Use this for any immigration-status-related context, NOT reentry. "
                    "dv_survivor = escaping or recovering from domestic violence/abuse. "
                    "pregnant = currently expecting a baby. "
                    "senior = elderly or older adult. "
                    "May include multiple values (e.g. 'disabled veteran'). "
                    "Omit if not mentioned."
                ),
            },
            "org_name": {
                "type": "string",
                "description": (
                    "The name of a specific organization the user is asking about. "
                    "For example: 'tell me about Covenant House' → 'Covenant House', "
                    "'where is Safe Horizon in Harlem' → 'Safe Horizon'. "
                    "Only extract when the user is asking about a specific organization "
                    "by name, not when they mention an org in passing. "
                    "Use the canonical name (e.g. 'Ali Forney Center' not 'ali forney')."
                ),
            },
            "tone": {
                "type": "string",
                "enum": ["emotional", "frustrated", "urgent", "confused"],
                "description": (
                    "The emotional tone the user is conveying, if clearly present. "
                    "emotional = expressing feelings of fear, sadness, shame, grief, "
                    "loneliness, or distrust. "
                    "frustrated = annoyed with the bot or the situation, restating, "
                    "expressing impatience. "
                    "urgent = communicating time-sensitivity in their need (tonight, "
                    "right now, ASAP). "
                    "confused = doesn't understand what to do or where to start. "
                    "Omit when the message is neutral, transactional, or matter-of-fact. "
                    "Used as a fallback when fast regex/keyword tone classification "
                    "missed a clearer LLM-readable signal."
                ),
            },
            "action": {
                "type": "string",
                "enum": [
                    "greeting", "thanks", "help", "escalation", "reset",
                    "bot_identity", "bot_question",
                    "confirm_yes", "confirm_deny",
                    "confirm_change_service", "confirm_change_location",
                    "correction", "negative_preference",
                ],
                "description": (
                    "The dialog action the user is performing, if clearly present. "
                    "greeting = hi, hello. thanks = thank you, thanks. "
                    "help = asking for help in general (no specific service). "
                    "escalation = asking to speak to a person. reset = start over. "
                    "bot_identity = asking who/what the bot is. "
                    "bot_question = asking about the bot's capabilities or "
                    "limitations. "
                    "confirm_yes / confirm_deny = answering a pending yes/no. "
                    "confirm_change_service / confirm_change_location = changing a "
                    "previously-stated service or location. "
                    "correction = correcting prior info. "
                    "negative_preference = stating what they DON'T want. "
                    "Omit when the message is a normal service request without one of "
                    "these dialog roles. Used as a fallback when fast regex/keyword "
                    "action classification missed an LLM-readable signal."
                ),
            },
        },
        "required": [],
    },
}

# ---------------------------------------------------------------------------
# NARRATIVE THRESHOLD
# ---------------------------------------------------------------------------
# Messages this many words or longer take the narrative path.

_NARRATIVE_THRESHOLD = 20  # words

# ---------------------------------------------------------------------------
# URGENCY HIERARCHY (for _narrative_regex_fallback)
# ---------------------------------------------------------------------------
# Higher index = higher priority when multiple services are detected and
# the LLM is unavailable. Food and mental_health now TIE at tier 5; the
# regex side breaks the tie by text position, matching behavior
# documented in the migration doc. This overrides the legacy ordering
# (mental_health > food) per the Phase 0 priority-hierarchy decision.

_URGENCY_HIERARCHY = {
    "other":         0,
    "employment":    1,
    "legal":         2,
    "personal_care": 3,
    "clothing":      4,
    "food":          5,
    "mental_health": 5,
    "medical":       6,
    "shelter":       7,
}

# ---------------------------------------------------------------------------
# SHORT-PATH SYSTEM PROMPT
# ---------------------------------------------------------------------------
# Used for messages under _NARRATIVE_THRESHOLD words. Compact; the
# narrative prompt's FULL urgency hierarchy (9-way priority table) is
# not included here — short messages don't usually carry the ambiguity
# that full hierarchy resolves, and keeping the prompt small saves
# input tokens.
#
# The multi-intent block below (Option 4 hardening from Phase 2 of the
# llm_slot_extractor migration) teaches a simpler rule: first-mentioned
# is primary, UNLESS a safety signal is present. This aligns the LLM's
# short-path primary pick with the scenario-author convention that
# breaks when Trust Model 3's set-equality rule falls back to regex
# priority (regex priority-orders services, which over-promotes
# shelter/medical when the user mentioned them second without any
# urgency cue).
#
# Keep aligned with `_URGENCY_HIERARCHY` (above) when safety signals
# fire: the safety-signal override promotes shelter or medical, which
# matches the top two rungs of the hierarchy.

_SHORT_SYSTEM_PROMPT = (
    "You are a slot extraction engine for a social services chatbot in NYC. "
    "Extract structured information from the user's message using the "
    "extract_intake_slots tool. Only extract what is explicitly stated or "
    "strongly implied. Do not guess or assume. If the message doesn't contain "
    "any service-related information, call the tool with an empty object {}.\n\n"
    "Substance-use intent (detox, addiction, rehab, recovery, AA/NA, "
    "treatment programs, sober living) routes to medical, NOT mental_health. "
    "Streetlives data classifies all 'Substance Use Treatment' rows as "
    "medical, and 'medical' is also a more dignifying confirmation frame "
    "for someone seeking detox.\n\n"
    "When the user mentions MULTIPLE service needs, choose the primary "
    "service_type as follows:\n"
    "  1. If a SAFETY SIGNAL is present — 'tonight', 'right now', 'nowhere "
    "to sleep', \"can't stay\", 'urgent', 'help me now', 'kicked out', "
    "'evicted', 'nowhere to go', 'just got out' — shelter or medical wins "
    "as primary, EVEN IF mentioned second.\n"
    "  2. Otherwise, the FIRST-mentioned service is primary. Any other "
    "services go into additional_services. Do not repeat the primary "
    "service in additional_services.\n\n"
    "Examples (no safety signal → first-mentioned wins):\n"
    "  'I need food and a place to sleep in Brooklyn'\n"
    "  → service_type: 'food' (first-mentioned)\n"
    "  → additional_services: [{\"type\": \"shelter\"}]\n\n"
    "  'Where can I shower and get a meal in Manhattan?'\n"
    "  → service_type: 'personal_care' (first-mentioned)\n"
    "  → additional_services: [{\"type\": \"food\"}]\n\n"
    "  'I need some clean clothes and a meal in Harlem'\n"
    "  → service_type: 'clothing' (first-mentioned)\n"
    "  → additional_services: [{\"type\": \"food\"}]\n\n"
    "Examples (safety signal present → shelter/medical wins):\n"
    "  'I need food and somewhere to sleep tonight' (\"tonight\" = safety)\n"
    "  → service_type: 'shelter' (safety override)\n"
    "  → additional_services: [{\"type\": \"food\"}]\n\n"
    "  'I need a job but nowhere to go right now' (\"nowhere to go\" = safety)\n"
    "  → service_type: 'shelter' (safety override)\n"
    "  → additional_services: [{\"type\": \"employment\"}]\n\n"
    "Examples (3+ services — extract ALL of them):\n"
    "  '19-year-old mom with a baby, need shelter, diapers, food, "
    "and basic healthcare right now'\n"
    "  → service_type: 'shelter' (safety override: \"right now\")\n"
    "  → additional_services: [{\"type\": \"clothing\", \"detail\": \"baby supplies\"}, "
    "{\"type\": \"food\"}, {\"type\": \"medical\"}]\n"
    "  (Diapers route to clothing, NOT food. Three additional services, "
    "all preserved.)\n\n"
    "  'I need food, clothes, and showers in the Bronx'\n"
    "  → service_type: 'food' (first-mentioned)\n"
    "  → additional_services: [{\"type\": \"clothing\"}, {\"type\": \"personal_care\"}]\n"
    "  (Two additional services — do not drop the third.)\n\n"
    "Domestic-violence context — 'next steps' implies legal/advocacy:\n"
    "  'Escaped abuse with my child, safe for the moment, need help with "
    "shelter and next steps'\n"
    "  → service_type: 'shelter' (safety override + DV context)\n"
    "  → additional_services: [{\"type\": \"legal\"}]\n"
    "  (In DV escape context, \"next steps\" means legal advocacy / order "
    "of protection / case management. Outside DV context, \"next steps\" "
    "is too generic to extract — leave it out.)\n\n"
    "You may receive prior conversation turns for context. Use them to resolve "
    "references like 'there', 'that area', 'try Queens instead', or 'what about "
    "Brooklyn?' — but only extract slots from the LATEST user message."
)

# ---------------------------------------------------------------------------
# NARRATIVE SYSTEM PROMPT
# ---------------------------------------------------------------------------
# Used for messages ≥ _NARRATIVE_THRESHOLD words. Teaches the urgency
# hierarchy explicitly so the LLM prioritizes correctly when multiple
# services appear. Food is now listed at or above mental_health (Phase 0
# decision). The "Extract ALL slots" enumeration includes all 10 fields
# in the schema (Behavior #23 fix — legacy prompt listed only 7 and
# silently dropped org_name along with service_detail and
# additional_services).

_NARRATIVE_SYSTEM_PROMPT = (
    "You are a slot extraction engine for a social services chatbot in NYC. "
    "The user has written a long message describing their situation.\n\n"
    "Extract structured information using the extract_intake_slots tool. "
    "CRITICAL: When the user mentions MULTIPLE service needs, choose the "
    "PRIMARY service_type based on this urgency hierarchy (highest first):\n"
    "  1. shelter / housing / safety (place to stay, eviction, homelessness)\n"
    "  2. medical (health emergency, injury, illness, detox / substance-use treatment)\n"
    "  3. food (meals, pantries, hunger) — tied with mental_health\n"
    "  3. mental_health (counseling, therapy, depression, anxiety) — tied with food\n"
    "  4. clothing, personal_care, legal, employment, other\n\n"
    "Substance-use intent (detox, addiction, rehab, recovery, AA/NA, "
    "treatment programs) routes to medical, NOT mental_health. The "
    "Streetlives data classifies all 'Substance Use Treatment' rows as "
    "medical, and 'medical' is also a more dignifying confirmation "
    "frame for someone seeking detox.\n\n"
    "When food and mental_health are both present, break the tie by text "
    "position — whichever the user mentions first. Rationale: hunger is a "
    "physiological need that undermines other interventions; a person in "
    "mental-health crisis who hasn't eaten still needs the food first, or "
    "the mental-health referral doesn't land.\n\n"
    "For example:\n"
    "  'I just got out of the hospital and my housing fell through'\n"
    "  → service_type: 'shelter' (NOT medical — housing is more urgent)\n"
    "  → additional_services: [{\"type\": \"medical\"}]\n\n"
    "  'I need a job but I also have nowhere to sleep tonight'\n"
    "  → service_type: 'shelter' (NOT employment — tonight = urgent)\n"
    "  → additional_services: [{\"type\": \"employment\"}]\n\n"
    "  'I'm 17, I ran away and I need clothes and a place to stay'\n"
    "  → service_type: 'shelter' (NOT clothing — runaway youth = safety)\n"
    "  → additional_services: [{\"type\": \"clothing\"}]\n\n"
    "  'I was just released from Rikers and need a place to stay and a job'\n"
    "  → service_type: 'shelter' (re-entry = immediate housing need)\n"
    "  → additional_services: [{\"type\": \"employment\"}]\n\n"
    "When the user lists 3 OR MORE distinct service needs, extract ALL of "
    "them. Do not collapse, summarize, or drop any:\n"
    "  '19-year-old mom with a baby, need shelter, diapers, food, and "
    "basic healthcare right now'\n"
    "  → service_type: 'shelter' (most urgent — \"right now\" + housing)\n"
    "  → additional_services: [{\"type\": \"clothing\", \"detail\": \"baby supplies\"}, "
    "{\"type\": \"food\"}, {\"type\": \"medical\"}]\n"
    "  → family_status: 'with_children', age: 19, urgency: 'high'\n"
    "  (Diapers route to clothing › baby supplies, NOT food. The user listed "
    "FOUR distinct needs; the response queues three additional services after "
    "primary shelter.)\n\n"
    "Domestic-violence context — 'next steps' implies legal/advocacy:\n"
    "  'Escaped abuse with my child, safe for the moment, need help with "
    "shelter and next steps'\n"
    "  → service_type: 'shelter' (DV context + family safety priority)\n"
    "  → additional_services: [{\"type\": \"legal\"}]\n"
    "  → family_status: 'with_children', urgency: 'high'\n"
    "  (In DV-escape context, \"next steps\" reliably means legal advocacy, "
    "order of protection, or case management. The phrase is too generic to "
    "extract OUTSIDE DV context — only extract legal here because of the "
    "explicit DV framing.)\n\n"
    "Handle negation: 'I don't want food, I need shelter' → shelter only.\n"
    "Handle context clues: 'just got out of Rikers' = re-entry → shelter urgency. "
    "'evicted', 'lost my housing', 'kicked out' = housing crisis.\n\n"
    "Extract ALL slots: service_type, service_detail, additional_services, "
    "location, age, urgency, gender, family_status, populations, org_name. "
    "Only extract what is stated or strongly implied. If urgency is not "
    "explicit but the situation is clearly urgent (eviction, re-entry, "
    "runaway, tonight, nowhere to go), set urgency='high'.\n\n"
    "You may receive prior conversation turns for context. Only extract "
    "slots from the LATEST user message."
)
