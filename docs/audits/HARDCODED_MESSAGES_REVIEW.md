# YourPeer Chatbot — Hardcoded Messages Review

**Purpose:** Every message below is shown to users exactly as written — no LLM involvement. The Streetlives team should review these for tone, accuracy, and cultural appropriateness. Edits to any message require only changing the string in the listed source file and restarting the server.

**Date:** April 15, 2026

---

## 1. Crisis Responses

These fire when crisis language is detected. Each message is paired with specific hotline resources. Crisis detection uses regex first (<1ms), then Claude Sonnet for indirect language. These are the highest-stakes messages in the system.

**Source file:** `backend/app/services/crisis_detector.py`

### 1.1 Suicide / Self-Harm

**Triggers:** "I want to kill myself", "I don't want to be here anymore", "no one would notice if I disappeared", and ~40 other phrases + LLM detection for indirect language.

> I hear you, and I'm glad you reached out. What you're feeling matters, and there are people who want to help right now.
>
> Please reach out to one of these free, confidential resources:
> • 988 Suicide & Crisis Lifeline — call or text 988 (24/7)
> • Crisis Text Line — text HOME to 741741
> • Trevor Project (LGBTQ+ youth) — call 1-866-488-7386 or text START to 678-678
>
> You can also ask me to connect you with a peer navigator. You don't have to go through this alone.

### 1.2 Domestic Violence

**Triggers:** "my partner hits me", "fleeing abuse", "domestic violence", "he threatens me", and ~30 other phrases.

> I'm sorry you're going through this. You deserve to be safe, and there is help available.
>
> • National Domestic Violence Hotline — 1-800-799-7233 (24/7) or text START to 88788
> • NYC Domestic Violence Hotline — 1-800-621-4673 (24/7, multilingual)
> • Safe Horizon — 1-800-621-HOPE (4673)
>
> If you're in immediate danger, please call 911.
>
> I can also help you find shelter or connect you with a peer navigator.

### 1.3 Youth Runaway

**Triggers:** "I ran away from home", "I'm a runaway", "I left home and I'm scared", and ~15 other phrases.

> I hear you, and I want to help you stay safe. You're not alone in this.
>
> Here are people who can help right now — they won't judge you:
> • National Runaway Safeline — 1-800-786-2929 (24/7, confidential) or chat at 1800runaway.org
> • Covenant House (NYC) — 1-800-388-3888 (shelter + support for youth)
> • Crisis Text Line — text HOME to 741741
> • If you're in immediate danger, call 911
>
> I can also help you find shelter or a drop-in center nearby.

### 1.4 Assault Victim

**Triggers:** "I got beat up", "I was attacked", "someone assaulted me", and ~15 other phrases.

> I'm sorry that happened to you. Your safety matters, and help is available.
>
> If you need medical attention right now, please call 911.
>
> Other resources:
> • Safe Horizon Victim Services — 1-800-621-HOPE (4673) (24/7)
> • NYC Health + Hospitals — call 311 for non-emergency medical help
> • 988 Suicide & Crisis Lifeline — call or text 988
>
> I can also help you find medical care or other services nearby.

### 1.5 Safety Concern (General)

**Triggers:** "I don't feel safe", "I'm in danger", "someone is following me", and ~15 other phrases.

> Your safety comes first. If you're in immediate danger, please call 911.
>
> Here are resources that can help right now:
> • 988 Suicide & Crisis Lifeline — call or text 988 (24/7)
> • Crisis Text Line — text HOME to 741741
> • NYC 311 — call 311 for shelter intake and social services
>
> If you need shelter right away, I can help you find somewhere safe. Just let me know what area you're in.

### 1.6 Medical Emergency

**Triggers:** "I'm having a heart attack", "I'm overdosing", "I'm bleeding badly", "I can't breathe", and ~20 other phrases.

> If this is a medical emergency, please call 911 immediately.
>
> • Emergency: 911
> • Poison Control: 1-800-222-1222
> • NYC Health + Hospitals: call 311 for non-emergency medical help
>
> Once you're safe, I can help you find nearby clinics or health services.

### 1.7 Trafficking

**Triggers:** "I'm being trafficked", "they won't let me leave", "someone is forcing me to work", and ~10 other phrases.

> What you're describing sounds very serious, and help is available. You are not in trouble.
>
> • National Human Trafficking Hotline — 1-888-373-7888 or text HELP to 233733 (BeFree)
> • If you're in immediate danger, call 911
>
> These services are confidential and available 24/7. I can also connect you with a peer navigator.

### 1.8 Violence / Danger (General)

**Triggers:** "someone is going to hurt me", "I saw someone get shot", and other general violence phrases.

> If you or someone else is in immediate danger, please call 911.
>
> If you'd like to talk to someone:
> • 988 Suicide & Crisis Lifeline — call or text 988 (24/7)
> • Crisis Text Line — text HOME to 741741
>
> I can also connect you with a peer navigator who can help you find support services.

---

## 2. Emotional Responses

These fire when the user expresses an emotion WITHOUT a service request. The design principle: acknowledge the feeling first, offer a peer navigator, never push services. Based on the Acknowledge-Validate-Redirect (AVR) pattern from clinical chatbot research (Woebot, Wysa).

**Source file:** `backend/app/services/responses.py`

### 2.1 Scared

> It's okay to feel scared — that's a completely understandable reaction to what you're going through. You're not alone in this.
>
> If you'd like to talk to someone, I can connect you with a peer navigator who can help you figure out next steps. No pressure.

### 2.2 Sad

> I'm sorry you're feeling this way. It takes courage to say that, and it's okay to not be okay right now.
>
> If you'd like to talk to someone who understands, I can connect you with a peer navigator. I'm here whenever you're ready.

### 2.3 Rough Day

> That sounds really hard. Some days are just heavy, and it's okay to feel that way.
>
> If you'd like to talk to someone, I can connect you with a peer navigator. Or just take your time — I'm here.

### 2.4 Shame

> You have nothing to be ashamed of. A lot of people use these services — it doesn't say anything about you as a person.
>
> It takes real strength to reach out, and I'm glad you did. I'm here to help however I can.

### 2.5 Grief

> I'm really sorry for your loss. That's an incredibly heavy thing to carry.
>
> If you'd like to talk to someone, I can connect you with a peer navigator. There's no rush — I'm here.

### 2.6 Alone / Isolated

> I hear you, and I'm sorry you're feeling that way. You're not invisible, and reaching out here took courage.
>
> If you'd like to talk to someone who understands, I can connect you with a peer navigator. I'm here.

### 2.7 Undeserving

**Research basis:** 41% of homeless people report feeling undeserving of help (PMC study).

> You absolutely deserve help — everyone does. These services exist for exactly this, and you have every right to use them.
>
> I'm glad you're here. I can help you find what you need, or connect you with a peer navigator.

### 2.8 Distrust

**Research basis:** Difficulty trusting people is a named trauma response (National Harm Reduction Coalition). Distrust of institutional services is a top barrier to care-seeking.

> That's a fair concern — I understand why you'd be cautious. Everything I show you comes from a real database of verified services, and I don't store any personal information.
>
> You're in control here. You can search, skip, or connect with a peer navigator — a real person — whenever you're ready.

### 2.9 Angry (at situation, not at bot)

**Note:** This is distinct from bot-directed frustration. SAMHSA identifies anger as a common trauma response.

> You have every right to feel angry. The situation you're in is not okay, and that frustration makes complete sense.
>
> When you're ready, I can help you find services — or I can connect you with a peer navigator who gets it.

---

## 3. Tone Prefixes

Short phrases prepended to the confirmation message when emotional context is detected. These ensure the bot doesn't feel "functional but flat" when the user is expressing distress alongside a service request. Example: user says "I'm really struggling and need food in Brooklyn" → bot says "_I hear you, and I want to help._ I'll look for food in Brooklyn — sound good?"

**Source files:** `backend/app/services/chatbot/tone.py` (warmth prefix selection, shame-normalization logic), `backend/app/services/responses.py` (static string catalog for each emotion)

| Context detected | Prefix |
|---|---|
| Shame + service request | "It takes real strength to reach out — a lot of people use these services, and there's no shame in it." |
| Medical urgency (medication depletion) | "That sounds urgent — let me help you find care right away." |
| Emotional + service request | "I hear you, and I want to help." |
| Frustrated + service request | "I understand this has been frustrating. Let me try something different." |
| Confused + service request | "No worries — let me help you with that." |
| Urgent + service request | "I can see this is urgent — let me find something right away." |
| Prior shame (follow-up turn) | "Still here with you." |
| Prior medical urgency (follow-up) | "Let's get you to the right place." |
| Prior emotional (follow-up) | "I'm still here with you." |

### Baseline warmth (no emotional context, routine flow)

One of these is randomly selected for routine service requests to prevent flat responses:

- "Let me see what's available."
- "Let's find something for you."
- "I'll look into that."
- "Let me see what I can find."
- "Absolutely! let me look."
- "I can help with that."
- "Ok, let me look."
- "On it!"
- "Let me see what I can find."
- "Absolutely! Let's see..."

---

## 4. Conversational Responses

Standard responses for common non-service interactions.

**Source file:** `backend/app/services/responses.py`

### 4.1 Greeting (first message or "hello")

> Hey! I'm here to help you find free services in NYC — things like food, shelter, showers, clothing, health care, and more.
>
> I'll ask a couple of questions to find what's near you. You can skip anything you're not comfortable sharing. What are you looking for?

### 4.2 Help ("what can you do?", "how does this work?")

> I can help you find free services in New York City. Just tell me what you need and where you are — for example, "I need food in Brooklyn" or "shelter in Queens." I'll search real, verified listings from YourPeer and show you what's available with addresses and hours.
>
> You can also say 'start over' anytime to begin a new search.

### 4.3 Thanks ("thank you", "thanks")

> You're welcome! Let me know if you need anything else — I can search for food, shelter, clothing, health care, legal help, jobs, and more.

### 4.4 Reset ("start over", "new search")

> No problem, let's start fresh. What kind of help are you looking for?

### 4.5 Bot Identity ("are you a robot?", "are you AI?")

> I'm an AI assistant for YourPeer. I help you find free services in NYC — things like food, shelter, clothing, and more — using verified information from our database.
>
> I don't make up information. All the services I show you come from real locations that have been checked by people who've used them.
>
> If you'd like to talk to a real person, I can connect you with a peer navigator. Otherwise, just tell me what you need help with!

### 4.6 Confused / Overwhelmed ("I don't know what I need")

> That's okay — you don't have to know exactly what you need. I can help you figure it out.
>
> Here are some things people often look for:
> • A meal or groceries
> • A place to stay tonight
> • A shower, clean clothes, or toiletries
> • A doctor or someone to talk to
> • Help with legal issues, a job, or benefits
>
> Tap any option below, or just tell me what's going on and I'll point you in the right direction.

### 4.7 Escalation ("connect me with a person", "peer navigator")

> I can connect you with a peer navigator who can help with your situation.
>
> You can reach the Streetlives team at:
> • Visit yourpeer.nyc and use the chat feature
> • Call 311 and ask for social services referrals
>
> If you're in crisis:
> • 988 Suicide & Crisis Lifeline — call or text 988
> • Crisis Text Line — text HOME to 741741
>
> Would you like me to keep searching for services, or is there anything else I can help with?

---

## 5. Frustration Handling

**Source file:** `backend/app/services/chatbot/handlers/emotional.py` (`_handle_frustration` — the 3-tier escalation counter lives here, along with the filter-aware post-routing cleanup)

### 5.1 First Frustration (negative preference)

> I understand — those options aren't what you need. I can search for a different type of service, or connect you with a peer navigator who might know of other resources. What would be most helpful?

### 5.2 Second Frustration

> I hear you — I'm clearly not finding what you need right now. A peer navigator would be more helpful — they're real people who know the system. You can also call 311 for live help.

### 5.3 Third+ Frustration

> I'm sorry I haven't been able to help. Let me connect you with a peer navigator — they can work with you directly.

### 5.4 Frustrated + Service Intent (skips confirmation)

> I hear you — searching right now.

---

## 6. Privacy & PII Warnings

**Source file:** `backend/app/services/chatbot/pipeline.py` (warning strings); detection is in `backend/app/privacy/pii_redactor.py`

### 6.1 SSN Detected

> For your safety, please don't share your Social Security number or other sensitive personal information in this chat. I've removed it from the conversation.

### 6.2 Phone Number Detected

> Just a heads up — I've removed your phone number from the conversation to protect your privacy. You don't need to share personal info to search for services.

---

## 7. Context-Aware "No" Responses

**Source file:** `backend/app/services/chatbot/handlers/confirmation.py` (the `_deny_contexts` dict and its `last_action`-aware dispatcher)

| After... | Bot says |
|---|---|
| Escalation offer | "No problem — I'm here if you change your mind. Is there anything else I can help you with?" |
| Emotional response | "That's okay. I'm here whenever you're ready. If there's anything practical I can help you find, just let me know." |
| Frustration | "No worries. If you'd like to try something else or talk to a real person, just let me know." |
| Confused response | "That's okay — no rush. I'm here when you're ready. You can also talk to a real person if that would help." |
| Crisis resources | "That's okay. The resources above are available anytime. If you'd like to search for services later, I'm here." |

---

## 8. Search Results Messages

**Source files:** `backend/app/services/chatbot/execution.py` (results-found phrasing and queue-offer message), `backend/app/services/confirmation.py` (no-results fallback via `_build_no_results_message`)

### 8.1 Results Found

> I found 5 option(s) for you — here are the top 5:

> I found 25 option(s) for you — here are the top 5:

### 8.2 Co-located Results Found

> I found 3 location(s) that offer both food and clothing:

### 8.3 No Results

> I wasn't able to find shelter services in Manhattan matching your criteria. Would you like me to try Brooklyn or Queens instead? You can also say "connect with peer navigator" to talk to a real person.

### 8.4 Queue Offer (multi-intent)

> You also mentioned food — would you like me to search for that too?

---

**Source file:** `backend/app/services/chatbot/handlers/accessibility.py`

### 9.1 Spanish Only (no service request)

> I'm sorry — right now I can only help in English. A peer navigator may be able to help in Spanish.
>
> Lo siento — por ahora solo puedo ayudar en inglés. Un navegador comunitario puede ayudarte en español.

### 9.2 Spanish + Service Request

A bilingual note is prepended to the normal confirmation flow (not shown here — the confirmation itself is in English).

---

## 10. Shame Normalization Prefix

When shame language is detected alongside a service request, this prefix is prepended to the confirmation.

**Source file:** `backend/app/services/chatbot/tone.py`

**Shame signals detected:** "embarrassed", "ashamed", "pathetic", "failure", "never thought I'd need", "hard for me to ask", "humiliating", "degrading", "burden", "swallow my pride", "first time asking", "never done this before", "never asked for help", "can't believe I'm", "can't afford to eat", "don't want anyone to know"

**Prefix:**
> It takes real strength to reach out — a lot of people use these services, and there's no shame in it.

---

## Review Questions for the Streetlives Team

1. **Crisis resources:** Are the hotline numbers and organizations still current? Are any missing for the NYC context?
2. **Tone:** Do the emotional responses feel authentic to the population? Too clinical? Too casual?
3. **Shame normalization:** Does the shame prefix feel natural, or does it draw more attention to the shame?
4. **Warmth prefixes:** Do any of these feel forced or out of place? Should any be removed or added?
5. **Frustration escalation:** Is three tiers the right number before directing to a navigator? Should the first tier be warmer? Should the third tier be shorter?
6. **PII warnings:** Is the SSN warning too alarming? Is the phone number warning clear enough?
7. **Spanish message:** Is the bilingual message appropriate? Should it include more languages?
8. **"Peer navigator" language:** Is this the right term? Does the population understand what it means?
9. **Bot identity:** Does the "I don't make up information" line build trust, or does it raise suspicion that it might?
10. **Crisis step-down:** After showing crisis resources, the bot offers to search for shelter. Is this appropriate, or should it wait longer before offering services?
