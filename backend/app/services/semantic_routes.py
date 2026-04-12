"""
Semantic route definitions for YourPeer chatbot.

Each route is a list of example utterances that define the semantic
neighborhood of a service category or population. These are full phrases
representing how real users describe their needs — not keywords.

Adding a new route or expanding an existing one requires no code changes,
no model retraining, and no deployment — just editing this file and
restarting the server. The model generalizes immediately from examples.

Sources:
    - Streetlives sample queries document (peer-sourced)
    - Eval scenario failures (Run 24–26)
    - Field experience from outreach workers
    - PHRASE_LIST_AUDIT.md coverage gaps

Maintenance:
    Review Tier 3 (LLM) fallback logs quarterly. Any message that reached
    the LLM but should have been Tier 2 is a candidate utterance to add
    here.
"""

# ---------------------------------------------------------------------------
# SERVICE ROUTES
# ---------------------------------------------------------------------------
# Each key maps to a service_type value in the slot extractor.
# Each list contains 10–20 example utterances.

SERVICE_ROUTES = {
    "medical": [
        "I need to see a doctor",
        "I ran out of my medication",
        "I'm diabetic and need insulin",
        "where can I get a prescription filled",
        "I need medical attention",
        "free clinic near me",
        "I have an infection and no insurance",
        "I need my blood pressure checked",
        "where can I get dental work done",
        "I need to get tested for STDs",
        "I have asthma and need an inhaler",
        "I need naloxone or narcan",
        "where can I get methadone",
        "I need prenatal care",
        "I have a wound that needs treatment",
        "my blood sugar is really high",
        "I need an eye exam",
        "I need to refill my prescription",
        "where can I get vaccinated",
        "I need to see a nurse about this wound",
        # Coverage for retired regex keywords (REGEX_AUDIT)
        "I'm sick and need to see someone",
        "I need a physical exam",
        "my vision is getting worse and I need glasses",
    ],

    "shelter": [
        "I need somewhere to sleep tonight",
        "I'm homeless and need a bed",
        "where can I find a shelter",
        "I got kicked out and have nowhere to go",
        "I'm sleeping on the street",
        "I need a safe place to stay",
        "I'm aging out of foster care and need housing",
        "I need transitional housing",
        "I was evicted and need help",
        "is there a warming center nearby",
        "I need a drop-in center",
        "my family needs emergency housing",
        "I'm couch surfing and need stability",
        "I need a place to crash tonight",
        "where is the DHS intake center",
        "I got put out of my apartment",
        "I've been sleeping in my car",
        "I need somewhere warm to stay",
        "I need a cot for tonight",
        "is there an overnight shelter I can go to",
        # Coverage for retired regex keywords (REGEX_AUDIT)
        "I need a room for tonight",
        "where can I do intake for a shelter",
    ],

    "food": [
        "I'm hungry and need food",
        "where is the nearest food pantry",
        "I need free meals",
        "where can I get groceries",
        "is there a soup kitchen nearby",
        "I need baby formula",
        "my kids need to eat",
        "I need help with food stamps",
        "where can I get a hot meal",
        "I haven't eaten today",
        "I need WIC assistance",
        "free food distribution near me",
        "I'm starving and need something to eat",
        "where can I grab a bite around here",
        "is there a mobile pantry this week",
    ],

    "clothing": [
        "I need clothes",
        "where can I get a winter coat",
        "I need shoes",
        "where can I get free clothing",
        "I need warm clothes for the winter",
        "I need an outfit for a job interview",
        "where is a clothing pantry",
        "I need socks and underwear",
        "I need boots",
        "I cannot afford to buy clothes",
        "is there a coat giveaway nearby",
        "I need a jacket",
        "where can I get gloves and a hat",
        "I need professional clothes for work",
    ],

    "personal_care": [
        "where can I take a shower",
        "I need to do laundry",
        "where can I get a haircut",
        "I need toiletries",
        "I need feminine hygiene products",
        "where can I get a toothbrush and toothpaste",
        "I need to freshen up and get clean",
        "I need a place to wash my clothes",
        "where can I use a bathroom",
        "I need soap and shampoo",
        "I need a hygiene kit",
        "I need pads and tampons",
        "where can I get deodorant",
        "I need to clean up before my appointment",
    ],

    "mental_health": [
        "I need to talk to a counselor",
        "I'm struggling with depression",
        "where can I find a therapist",
        "I need help with substance abuse",
        "I want to go to rehab",
        "I need to detox from alcohol",
        "where is an AA meeting",
        "I need psychiatric help",
        "I'm dealing with trauma and need support",
        "I need anger management classes",
        "where can I find a support group",
        "I need help with my addiction",
        "I need grief counseling",
        "I need to get into a treatment program",
        "I need sober living housing",
        "I need someone to talk to about my anxiety",
        "I need to detox from opiates",
    ],

    "legal": [
        "I need a lawyer",
        "I need help with my immigration case",
        "I'm facing eviction and need legal help",
        "I need help getting asylum",
        "where can I find legal aid",
        "I need help with my green card application",
        "I need a public defender",
        "I need help with tenant rights",
        "I need an order of protection",
        "I need help with my custody case",
        "I need immigration help",
        "I recently arrived and need help with papers",
        "I need legal representation for housing court",
        "I need help with my DACA renewal",
        "I'm undocumented and need legal assistance",
        # Coverage for retired regex keywords (REGEX_AUDIT)
        "I have to go to court next week",
        "I need help posting bail",
        "I need a visa to stay in the country",
    ],

    "employment": [
        "I need help finding a job",
        "where can I get job training",
        "I'm looking for work",
        "I need help with my resume",
        "are there any job placement programs",
        "I need vocational training",
        "where can I find day labor",
        "I'm a felon looking for employment",
        "I need help getting back to work after prison",
        "summer youth employment programs",
        "I need career counseling",
        "help finding work with a criminal record",
        "where can I get an apprenticeship",
        "I need job readiness training",
        "I need help preparing for interviews",
    ],

    "housing_assistance": [
        "I need help paying my rent",
        "I'm behind on rent and might get evicted",
        "where can I apply for Section 8",
        "I need rental assistance",
        "I need help with a housing application",
        "how do I apply for NYCHA",
        "I need eviction prevention help",
        "where can I find affordable housing",
        "I need a housing voucher",
        "I need help with Housing Connect",
        "I need homeless prevention services",
    ],

    "other": [
        "I need help applying for food stamps",
        "where can I get a free phone",
        "I need to get an ID",
        "I need help with my benefits",
        "where can I charge my phone",
        "I need a mailing address",
        "I need help with my SNAP application",
        "where can I get free wifi",
        "I need help with Medicaid",
        "I need financial counseling",
        "I need help getting my birth certificate",
        "I need help with budgeting",
        "I need a metro card",
        "where can I get a state ID",
        "I need help enrolling in health insurance",
        "I need help with tax preparation",
        "I need computer classes",
        "I need to learn English",
    ],
}


# ---------------------------------------------------------------------------
# POPULATION ROUTES
# ---------------------------------------------------------------------------
# Cross-cutting identity/context attributes. A lower confidence threshold
# (0.70) is used because population context often accompanies a service
# request rather than being the entire message.

POPULATION_ROUTES = {
    "reentry": [
        "I just got out of jail",
        "I have a criminal record",
        "I'm a felon",
        "I was incarcerated",
        "I'm on parole",
        "I'm on probation",
        "formerly incarcerated looking for help",
        "I did time in prison",
        "I have a felony conviction",
        "just released from Rikers",
        "I got out of prison and need help",
        "I have a record and nobody will hire me",
    ],

    "veteran": [
        "I'm a military veteran",
        "I served in the Army",
        "I'm a combat veteran",
        "veteran benefits",
        "VA services near me",
        "I served in Afghanistan",
        "honorably discharged veteran",
        "I served in the Marines",
        "I'm a Navy veteran",
    ],

    "senior": [
        "I'm an elderly person needing help",
        "senior services near me",
        "I'm 70 years old and need assistance",
        "resources for older adults",
        "senior center near me",
        "aging services",
        "help for elderly people",
    ],

    "pregnant": [
        "I'm pregnant and need help",
        "I'm expecting a baby",
        "I need maternity services",
        "prenatal care for uninsured",
        "I'm pregnant and homeless",
        "I need help with my pregnancy",
    ],

    "disabled": [
        "I'm in a wheelchair and need accessible services",
        "I have a disability",
        "I'm blind and need help",
        "I need disability services",
        "accessible services near me",
        "I'm deaf and need assistance",
        "I have a physical disability",
        "I need mobility assistance",
    ],

    "dv_survivor": [
        "I'm escaping domestic violence",
        "I left my abusive partner",
        "I need help fleeing abuse",
        "domestic violence services",
        "I'm a survivor of domestic abuse",
        "my partner is abusive",
        "I need a safe place away from my abuser",
        "I escaped an abusive relationship",
    ],
}
