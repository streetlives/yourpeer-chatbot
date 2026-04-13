"""
Phrase lists and data constants for the YourPeer chatbot.

This module contains all keyword/phrase lists, quick-reply definitions,
service labels, borough mappings, and other static data used by the
classification and response modules.

Separated from logic so that:
  - Adding a new phrase doesn't require reviewing 2,900 lines
  - Data changes are obvious in code review (no logic interleaved)
  - Multiple feature branches can edit different data sections
    without merge conflicts
"""

import re

# ---------------------------------------------------------------------------
# CONTRACTION NORMALIZATION (P4 audit)
# ---------------------------------------------------------------------------
# Expands common contractions to their full forms so phrase lists only need
# the expanded version (e.g., "not helpful") to match all contraction
# variants ("isn't helpful", "isnt helpful", "wasn't helpful", etc.).
#
# Applied to frustration, emotional, and confused matching in _classify_tone.
# NOT applied to crisis detection — crisis uses explicit enumeration for safety.

_CONTRACTION_MAP = {
    # Negative contractions → "not" form
    "isn't": "is not", "isnt": "is not",
    "wasn't": "was not", "wasnt": "was not",
    "aren't": "are not", "arent": "are not",
    "weren't": "were not", "werent": "were not",
    "doesn't": "does not", "doesnt": "does not",
    "didn't": "did not", "didnt": "did not",
    "don't": "do not", "dont": "do not",
    "can't": "can not", "cant": "can not",
    "won't": "will not", "wont": "will not",
    "hasn't": "has not", "hasnt": "has not",
    "haven't": "have not", "havent": "have not",
    "wouldn't": "would not", "wouldnt": "would not",
    "couldn't": "could not", "couldnt": "could not",
    "shouldn't": "should not", "shouldnt": "should not",
    # Pronoun contractions
    "i'm": "i am", "im": "i am",
    "i've": "i have", "ive": "i have",
    "i'll": "i will",
    "i'd": "i would",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "what's": "what is",
    "you're": "you are", "youre": "you are",
    "they're": "they are", "theyre": "they are",
    "we're": "we are",
}

# Sort by length descending so longer contractions match first
# ("wouldn't" before "won't" to avoid partial replacement)
_CONTRACTION_PAIRS = sorted(_CONTRACTION_MAP.items(), key=lambda x: -len(x[0]))


# ---------------------------------------------------------------------------
# INTENSIFIER STRIPPING
# ---------------------------------------------------------------------------
# Removes common intensifier adverbs that break substring contiguity in
# phrase matching. "I'm really scared" → "I'm scared" matches "i'm scared".

_INTENSIFIERS = {
    "really", "very", "so", "super", "extremely", "pretty", "quite",
    "totally", "absolutely", "incredibly", "truly", "deeply",
    "terribly", "horribly", "awfully", "genuinely", "particularly",
    "just", "kinda", "sorta",
}

_INTENSIFIER_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in sorted(_INTENSIFIERS, key=len, reverse=True)) + r')\b\s*',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# MESSAGE CLASSIFICATION PHRASES
# ---------------------------------------------------------------------------

_RESET_PHRASES = [
    "start over", "reset", "clear", "new search", "begin again",
    "restart", "start again", "nevermind", "never mind",
    "cancel my search", "please cancel", "i want to cancel",
    "cancel search", "cancel this",
]

# Short reset words that need exact-match to avoid false positives
# ("cancel" inside "I can't cancel my appointment" should not reset)
_RESET_EXACT = ["cancel"]

_GREETING_PHRASES = [
    "hi", "hey", "hello", "sup", "yo", "good morning", "good afternoon",
    "good evening", "whats up", "what's up",
]

_THANKS_PHRASES = [
    "thanks", "thank you", "appreciate it",
    "that helps", "perfect", "great thanks", "awesome",
]

# Short thanks words that need exact match to avoid substring collisions
# (e.g., "ty" in "city", "Port Authority"; "thx" unlikely to collide but safe)
_THANKS_EXACT = [
    "thx", "ty",
]

_HELP_PHRASES = [
    "what can you do", "how does this work",
    "what is this", "who are you", "what do you do",
    "how do i use this", "instructions",
    "what services", "what other services", "what else",
    "what do you offer", "what can i search for",
    "list services", "show services", "available services",
]

# "help" needs word-boundary matching to avoid colliding with
# "helpful", "unhelpful", "not helpful" (which are frustration phrases).
_HELP_WORD_RE = re.compile(r"\bhelp\b", re.IGNORECASE)

_ESCALATION_PHRASES = [
    "peer navigator", "talk to a person", "talk to someone",
    "speak to someone", "speak to a person", "real person",
    "human", "connect me", "connect with person",
    "connect with peer navigator", "call someone", "live chat",
    "case manager", "social worker", "counselor",
    # Missing variants (regex audit — 50% miss rate)
    "speak with someone", "speak with a person",
    "talk to a human", "speak to a human",
    "transfer me", "can someone call me",
    "person i can call", "someone to call",
    "is there a person", "actual person",
    "get me a person", "real human",
]

_FRUSTRATION_PHRASES = [
    "not helpful", "isn't helpful", "isnt helpful",
    "wasn't helpful", "wasnt helpful", "wasn't useful", "wasnt useful",
    "doesn't help", "doesnt help", "didn't help", "didnt help",
    "still not helpful", "still not useful", "still not working",
    "already tried", "tried that", "tried those",
    "none of those", "none of them", "doesn't work",
    "doesnt work", "didn't work", "didnt work",
    "useless", "waste of time", "not working", "nothing works",
    "can't find anything", "cant find anything",
    "not what i needed", "not what i need",
    "wrong results", "results are bad", "results are wrong",
    "thats not right", "that's not right", "thats wrong", "that's wrong",
    "not useful", "this sucks", "so unhelpful",
    # Missing contraction variants (P2 audit)
    "hasn't helped", "hasnt helped",
    "isn't working", "isnt working",
    "isn't useful", "isnt useful",
    "can't help me", "cant help me",
    "won't work", "wont work",
    # Stronger frustration / directed at bot (P2 audit)
    "this is ridiculous", "this is stupid",
    "you're not listening", "youre not listening",
    "you don't understand", "you dont understand",
    "same thing every time",
    "i keep getting the same results",
    # Resignation (P2 audit — route to frustration handler, not reset)
    "forget it", "this is pointless",
    # Informal / vernacular frustration (regex audit)
    "you're no help", "youre no help", "no help at all",
    "going in circles", "keep going in circles",
    "keeps asking the same thing", "asking me the same thing",
    "this is bs", "this is bull",
    "whatever",
    "smh",
    "bruh", "this ain't working", "this aint working",
    "yo this trash", "this is trash",
]

# Emotional expressions — sub-crisis distress that deserves warm
# acknowledgment rather than a service menu or a steer-back response.
# These must NOT overlap with service keywords ("feeling hungry" → service).
_EMOTIONAL_PHRASES = [
    "feeling down", "feeling really down",
    "really down", "so down",
    "feeling sad", "feeling really sad",
    "feeling bad", "feeling really bad",
    "feeling depressed", "so depressed", "really depressed", "very depressed",
    "feeling scared", "feeling really scared", "im scared", "i'm scared",
    "really scared", "so scared", "very scared",
    "feeling anxious", "feeling really anxious", "so anxious",
    "feeling lonely", "feeling alone", "so lonely", "all alone",
    "feeling hopeless", "feel hopeless",
    "feeling lost", "i feel lost",
    "feeling stuck", "i feel stuck",
    "not doing well", "not doing good", "not doing ok", "not doing okay",
    "im not okay", "i'm not okay", "i'm not ok", "im not ok",
    "having a hard time", "having a rough time", "having a tough time",
    "rough day", "bad day", "tough day", "hard day",
    "stressed out", "so stressed", "really stressed",
    "i'm stressed", "im stressed",
    # Adjective forms — users describe their SITUATION as emotional
    # ("this is depressing") rather than their STATE ("I'm depressed").
    "depressing", "overwhelming", "terrifying", "heartbreaking",
    "i'm struggling", "im struggling",
    "tired of everything", "exhausted",
    "i just need someone to talk to", "just need to talk",
    "nobody cares", "no one cares",
    # "i'm X" / "im X" forms — needed since intensifiers break contiguity.
    # "I'm really sad" needs "i'm sad" in the list for stripped matching.
    "i'm sad", "im sad",
    "i'm down", "im down",
    "i'm anxious", "im anxious",
    "i'm lonely", "im lonely",
    "i'm hopeless", "im hopeless",
    "i'm depressed", "im depressed",
    "i'm stuck", "im stuck",
    # Shame / stigma (P1 audit — #1 barrier to help-seeking in this population)
    "embarrassed to ask", "ashamed to ask", "ashamed of myself",
    "embarrassed to be here", "feel like a failure",
    "never thought i'd need help", "never thought id need help",
    "never thought i'd need a food bank",
    "i'm pathetic", "im pathetic", "ashamed",
    # Shame — indirect / vulnerability expressions (R27 tone gap)
    # These are how people express shame WITHOUT using the word "ashamed":
    "hard for me to say", "hard to say this", "hard for me to ask",
    "hard to ask for", "hard to admit",
    "difficult to ask", "difficult to say",
    "hate asking", "hate to ask", "hate having to ask",
    "humiliating", "degrading",
    "don't want to be a burden", "dont want to be a burden",
    "feel like a burden", "i'm a burden", "im a burden",
    "first time asking for help", "never done this before",
    "never had to ask", "never asked for help before",
    "swallow my pride", "swallowed my pride",
    "can't believe i'm doing this", "cant believe im doing this",
    "i can't afford to eat", "i cant afford to eat",
    "can't even feed myself", "cant even feed myself",
    # Grief / loss (P2 audit — common trigger for homelessness)
    "lost someone", "someone died", "my friend died",
    "grieving", "in mourning",
    # Post-normalization variants — _normalize_contractions() expands
    # "I'm" → "I am", so these forms must be in the list too.
    # Without them, "I'm scared" → normalized "I am scared" → no match.
    "i am scared", "i am feeling scared", "i feel scared",
    "i am sad", "i am feeling sad", "i feel sad",
    "i am down", "i am feeling down", "i feel down",
    "i am anxious", "i am feeling anxious", "i feel anxious",
    "i am lonely", "i am feeling lonely", "i feel lonely",
    "i am hopeless", "i am feeling hopeless", "i feel hopeless",
    "i am depressed", "i am feeling depressed", "i feel depressed",
    "i am stuck", "i am feeling stuck", "i feel stuck",
    "i am not okay", "i am not ok",
    "i am struggling", "i am pathetic",
    "i am stressed",
    # Post-normalization shame variants (R27 tone gap)
    "i am a burden",
    "do not want to be a burden",
    "can not believe i am doing this",
    "i can not afford to eat", "can not even feed myself",
    "never thought i would need",
    # Isolation (P2 audit — major factor in homeless population)
    "nobody understands", "no one understands",
    "completely alone", "i have no one",
    "no friends", "no family",
    # Despair without suicidality (P2 audit)
    "everything is falling apart", "my life is falling apart",
    "nothing ever works out", "things keep getting worse",
    "i can't catch a break", "i cant catch a break",
    # Indirect emotional expressions (regex audit — 0% detection rate)
    "can't take it anymore", "cant take it anymore",
    "i just cant take it", "i just can't take it",
    "at the end of my rope", "end of my rope",
    "crying all day", "been crying",
    "i hate my life", "hate my life",
    "i have nothing", "have nothing left", "nothing left",
    "whats the point", "what's the point",
    "i feel broken", "im broken", "i'm broken", "i am broken",
    "feel empty", "feel empty inside",
    "feel like giving up", "giving up",
    "so hard right now", "everything is so hard",
    "i don't know what to do with myself",
    "dont know what to do with myself",
    # --- Research-backed additions (R28) ---
    # Distrust / suspicion (National Harm Reduction Coalition: "difficulty
    # trusting people" is a named trauma response; PMC: distrust of
    # institutional services is a top barrier to care-seeking)
    "i don't trust", "i dont trust", "i do not trust",
    "is this legit", "is this real", "is this safe",
    "how do i know", "can i trust",
    "been burned before", "been lied to",
    "sounds too good", "what's the catch", "whats the catch",
    "how is this free", "is there a catch",
    # Feeling undeserving (PMC: 41% of homeless people feel undeserving
    # of help; distinct from shame — about self-worth, not stigma)
    "i don't deserve", "i dont deserve", "i do not deserve",
    "i'm not worth", "im not worth", "not worth it",
    "other people need it more", "others need it more",
    "people have it worse", "someone else needs",
    "don't want to take", "dont want to take", "do not want to take",
    "feel like such a burden",
    "i'm not worthy", "im not worthy", "i am not worthy",
    "unworthy", "undeserving",
    # Anger at situation (SAMHSA: anger is a common trauma response;
    # distinct from bot-directed frustration handled by _FRUSTRATION_PHRASES)
    "i'm so angry", "im so angry", "i am so angry",
    "i'm furious", "im furious", "i am furious",
    "i'm pissed", "im pissed",
    "so unfair", "not fair",
    "fed up with everything", "fed up with this",
    "sick of this", "tired of being",
    "why does this keep happening", "why me",
]

_BOT_IDENTITY_PHRASES = [
    "are you a robot", "are you a bot", "are you ai",
    "are you a real person", "are you human",
    "am i talking to a person", "am i talking to a human",
    "is this a bot", "is this ai", "is this a chatbot",
    "is this a real person", "who am i talking to",
    "are you a computer", "are you a machine",
    "talking to a robot",
]

# Bot capability questions — asking HOW the bot works, not WHO it is.
_BOT_QUESTION_PHRASES = [
    "why can't you", "why cant you", "why couldn't you", "why couldnt you",
    "why didn't you", "why didnt you", "why weren't you", "why werent you",
    "how do you", "how does this work", "how does this bot work",
    "what can you do", "what can you search", "what can you help with",
    "what services do you", "what services can you",
    "can you search outside", "do you work outside",
    "can you explain", "can you tell me how",
    "why did you show", "why did you give",
    "what happened to", "what went wrong",
    # Privacy questions
    "is this private", "is this confidential", "is this safe",
    "is this anonymous", "are you recording",
    "who can see", "can anyone see", "do you share",
    "do you store", "do you save", "do you track",
    "can ice see", "can the police", "will this affect my",
    "can my case worker", "can my caseworker", "can the shelter see",
    "how do i delete", "how do i clear",
    "do you know who i am", "do you know my name",
    # Additional privacy phrases (synced with bot_knowledge topics)
    "what happens to my", "what do you do with my",
    "where does my information", "where does my data",
    "is my information", "is my data",
    "do you sell", "do you keep",
    # Privacy questions that might co-occur with service keywords
    # (e.g., "If I search for shelter, do they get my info?")
    "do they get my information", "do they get my data",
    "do they know i searched", "does the shelter know",
    "does the provider", "who sees my", "who gets my",
    "will they know", "can they see my", "shared with",
    "is this anonymous", "is this confidential",
]

# Confusion / overwhelm — the user doesn't know what they need.
# These MUST be caught before hitting the LLM, which would otherwise
# interpret "I don't know what to do" as a mental health request.
_CONFUSED_PHRASES = [
    "don't know what to do", "dont know what to do",
    "idk what to do", "i don't know", "i dont know",
    "not sure what i need", "don't know what i need",
    "dont know what i need", "what should i do",
    "don't know where to start", "dont know where to start",
    "i'm confused", "im confused",
    "i'm lost", "im lost", "so lost",
    "i'm overwhelmed", "im overwhelmed", "so overwhelmed",
    "i'm not sure", "im not sure",
    "where do i start", "where do i begin",
    "what are my options", "what can i do",
    # Expanded confusion/overwhelm (P3 audit)
    "where do i even go", "who do i talk to",
    "this is confusing", "too many options",
    "everything is too much", "it's all too much",
    "i can't think straight", "i cant think straight",
    "so much going on",
]

_URGENT_PHRASES = [
    "right now", "tonight", "immediately", "asap", "urgent",
    "emergency", "before dark", "freezing",
    "nowhere to go", "have nowhere", "on the street",
    "please help", "please hurry", "desperate",
    "kicked out today", "evicted today",
]


# ---------------------------------------------------------------------------
# QUICK REPLY DEFINITIONS
# ---------------------------------------------------------------------------

# Welcome / entry-point category buttons (matches wireframe)
_WELCOME_QUICK_REPLIES = [
    {"label": "🍽️ Food", "value": "I need food"},
    {"label": "🏠 Shelter", "value": "I need shelter"},
    {"label": "🚿 Showers", "value": "I need a shower"},
    {"label": "👕 Clothing", "value": "I need clothing"},
    {"label": "🏥 Health Care", "value": "I need health care"},
    {"label": "💼 Jobs", "value": "I need help finding a job"},
    {"label": "⚖️ Legal Help", "value": "I need legal help"},
    {"label": "🧠 Mental Health", "value": "I need mental health support"},
    {"label": "📋 Other", "value": "I need other services"},
]

# Service-type labels for confirmation messages
_SERVICE_LABELS = {
    "food": "food",
    "shelter": "shelter",
    "clothing": "clothing",
    "personal_care": "showers / personal care",
    "medical": "health care",
    "mental_health": "mental health support",
    "legal": "legal help",
    "employment": "job help",
    "other": "other services",
}


# ---------------------------------------------------------------------------
# CONFIRMATION PHRASES
# ---------------------------------------------------------------------------

# Short words that need EXACT match to avoid false positives
_CONFIRM_YES_EXACT = [
    "yes", "yeah", "yea", "yep", "yup", "sure", "ok", "okay", "correct",
    "right", "go", "please", "do it", "find",
    # NYC youth slang / informal affirmation
    "bet", "aight", "ight", "word", "fasho", "fo sho",
    "facts", "say less", "cool", "def", "absolutely",
]

# Longer phrases that can use STARTS-WITH or CONTAINS matching
_CONFIRM_YES_STARTSWITH = [
    "yes ", "yeah ", "yea ", "yep ", "sure ", "ok ",
    "go ahead", "looks good", "looks right", "looks correct",
    "that's right", "thats right", "that's correct", "thats correct",
    "search for", "please search", "do the search",
    "yes search", "yes please",
    "confirm", "confirmed",
    # Informal / conversational
    "that works", "sounds good", "sounds right", "sounds correct",
    "lets go", "let's go", "for sure",
    "yea search", "bet ", "aight ",
]

_CONFIRM_CHANGE_SERVICE = [
    "change service", "different service",
    "wrong service", "change what i need",
    "change service type",
    # Run 24 eval fix — patterns that name the specific new service
    "change to", "switch to", "i'd rather have",
    "let me change", "can i change to", "can we do",
    "i want to switch",
]

_CONFIRM_CHANGE_LOCATION = [
    "change location", "different location", "wrong location",
    "different area", "different borough",
    "change borough", "change neighborhood",
]

# Denial phrases — user declines the pending confirmation.
# Treated as a soft reset: clears pending confirmation and offers options.
_CONFIRM_DENY_EXACT = [
    "no", "nah", "nope", "not yet", "wait", "hold on", "stop",
]

_CONFIRM_DENY_PHRASES = [
    "no thanks", "no thank you",
    # Narrowed: "i don't want" was too broad — matched "I don't want
    # anyone to know" (shame context). Adding "to" or "that" suffix.
    "i dont want to", "i don't want to",
    "i dont want that", "i don't want that",
    "not right now", "maybe later", "changed my mind",
    "i changed my mind",
    # Informal declines (multi_decline_with_different_phrasing)
    "i'm good", "im good", "nah i'm good", "nah im good",
    "all good", "no need",
    "i'm fine", "im fine", "no i'm fine", "no im fine",
]

# Deny words that remain denials even with trailing text.
# "nah food" during confirmation means "no, not food" — the user is
# declining, not requesting food. Without this, "nah" only matches as
# an exact phrase and "nah, food" falls through with no action detected.
_CONFIRM_DENY_STARTSWITH = [
    "nah ", "nope ", "no way",
]


# ---------------------------------------------------------------------------
# BOROUGH SUGGESTION DATA
# ---------------------------------------------------------------------------
# Borough suggestions when a query returns no results.
# Ordered by actual service availability from DB audit (Apr 2026),
# not just geographic proximity. Each service type lists boroughs
# from highest to lowest service count, excluding the user's own borough.

_NEARBY_BOROUGHS_BY_SERVICE = {
    "food": {
        "Manhattan":    ["Brooklyn", "Queens"],
        "Brooklyn":     ["Queens", "Bronx"],
        "Queens":       ["Brooklyn", "Bronx"],
        "Bronx":        ["Brooklyn", "Queens"],
        "Staten Island": ["Brooklyn", "Queens"],
    },
    "shelter": {
        # Shelter is thin everywhere — Manhattan has most (14), suggest it first
        "Manhattan":    ["Brooklyn", "Bronx"],
        "Brooklyn":     ["Manhattan", "Bronx"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
    "clothing": {
        # Manhattan-heavy (34). Queens only has 3 — always suggest Manhattan
        "Manhattan":    ["Brooklyn", "Bronx"],
        "Brooklyn":     ["Manhattan", "Bronx"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
    "personal_care": {
        # Shower: Manhattan (14), Bronx (5), Queens (4). Brooklyn only has 2
        "Manhattan":    ["Bronx", "Queens"],
        "Brooklyn":     ["Manhattan", "Bronx"],
        "Queens":       ["Manhattan", "Bronx"],
        "Bronx":        ["Manhattan", "Queens"],
        "Staten Island": ["Manhattan", "Queens"],
    },
    "medical": {
        # Health: Manhattan (237), Brooklyn (158), Bronx (118)
        "Manhattan":    ["Brooklyn", "Bronx"],
        "Brooklyn":     ["Manhattan", "Bronx"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
    "mental_health": {
        # Mental Health: Manhattan (40), Brooklyn (36), Queens (18)
        "Manhattan":    ["Brooklyn", "Queens"],
        "Brooklyn":     ["Manhattan", "Queens"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
    "legal": {
        # Legal: Manhattan (19), Brooklyn (9), Bronx (6)
        "Manhattan":    ["Brooklyn", "Bronx"],
        "Brooklyn":     ["Manhattan", "Bronx"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
    "employment": {
        # Employment: Manhattan (9), Brooklyn (6), Queens (2)
        "Manhattan":    ["Brooklyn", "Queens"],
        "Brooklyn":     ["Manhattan", "Queens"],
        "Queens":       ["Manhattan", "Brooklyn"],
        "Bronx":        ["Manhattan", "Brooklyn"],
        "Staten Island": ["Manhattan", "Brooklyn"],
    },
}

# Default fallback — geographic proximity when no service-specific data
_NEARBY_BOROUGHS_DEFAULT = {
    "Manhattan":    ["Brooklyn", "Queens"],
    "Brooklyn":     ["Manhattan", "Queens"],
    "Queens":       ["Brooklyn", "Manhattan"],
    "Bronx":        ["Manhattan", "Queens"],
    "Staten Island": ["Brooklyn", "Manhattan"],
    "New York":     ["Brooklyn", "Queens"],  # Manhattan alias
}

# Service types that map to each template key (mirrors SLOT_SERVICE_TO_TEMPLATE)
_SERVICE_TO_BOROUGH_KEY = {
    "food": "food",
    "shelter": "shelter", "housing": "shelter",
    "clothing": "clothing",
    "personal_care": "personal_care", "shower": "personal_care",
    "medical": "medical", "healthcare": "medical", "health": "medical",
    "mental_health": "mental_health",
    "legal": "legal",
    "employment": "employment", "job": "employment",
}
