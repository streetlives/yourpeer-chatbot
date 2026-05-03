# Spanish Language Support — Design Document

**Date:** April 2026
**Status:** Proposal
**Author:** Engineering
**Audience:** Streetlives leadership, engineering, community information team

---

## 1. Goal

Full Spanish parity in the YourPeer chatbot. A Spanish-speaking user should be able to complete every flow — from greeting through service search to results — entirely in Spanish, with the same quality of tone, safety, and accuracy as the English experience. This includes crisis detection, emotional handling, confirmation dialogs, error messages, and all UI text.

The admin console remains English-only (staff tool, not user-facing).

---

## 2. Current State

The chatbot has minimal Spanish support today:

- **Detection:** a regex (`_SPANISH_RE`) matches ~15 Spanish phrases (necesito, ayuda, comida, refugio, etc.)
- **Acknowledgment:** when Spanish is detected with a service request, the bot prepends a bilingual note ("I can see you may prefer Spanish — lo siento, por ahora solo puedo ayudar en inglés") and processes the search in English.
- **Spanish-only messages** (no service intent) get a full bilingual response directing the user to a peer navigator who may speak Spanish.
- **Keywords:** only 5 Spanish service keywords exist (comida, tengo hambre, alimentos, refugio, albergue) across 2 of 9 categories.
- **Eval:** one scenario (`edge_spanish_input`) tests basic Spanish input. Scores 4.5-4.9 because the LLM handles it, not because the system does.
- **Service data:** all database content (organization names, descriptions, addresses, hours) is in English. The `languages` table tracks which services offer Spanish-speaking staff.

**Related pattern — immigration-context acknowledgment (A.1.b, April 2026).** The Spanish bilingual acknowledgment established the prefix-prepend pattern: a short validating note composed into `_prefix_prepend` in `orchestrator.py`, prepended to the tone prefix on confirmations. A.1.b uses the same pattern for a different signal — asylum or immigration context in the slot state. Both prefixes can fire together on a compound message. If the full Spanish parity work in this proposal lands, the prefix composition in `orchestrator.py` is where language-specific variants would slot in — the current English-only phrasing of the immigration acknowledgment would need a Spanish counterpart in `_immigration_acknowledgment()` selected based on `_language`. See `CHATBOT_BEHAVIOR.md` Response Prefix Chain section for the current composition.

---

## 3. Architecture Decision: Session-Level Language, Not Per-Message Translation

**Decision:** detect the user's language on their first message and set a `_language` session slot ("en" or "es") that persists for the entire conversation. All subsequent bot responses, button labels, and error messages use the session language. The user can switch at any time by typing in the other language.

**Why not per-message translation:** a user who starts in Spanish and occasionally uses an English word (a service name, a neighborhood) should not have the bot switch languages mid-conversation. Session-level language provides a consistent experience. The LLM naturally handles code-switching within a session language.

**Why not browser language detection:** the `Accept-Language` header reflects the device's language, not the user's preference. Many Spanish-speaking New Yorkers use phones set to English. Detecting from the user's first message is more reliable and respects the user's choice.

---

## 4. Language Detection

### 4.1 Detection logic

A new function `detect_language(message)` returns `"es"` or `"en"`. It runs once, on the first message of a session (before slot extraction). The result is stored as `_language` in the session.

Detection uses a lightweight classifier — not the LLM. Options ranked by recommendation:

**Option A (recommended): Expanded regex + word frequency.** Extend `_SPANISH_RE` to ~100 common Spanish words (articles, pronouns, prepositions, common verbs). If 40%+ of words in the message match the Spanish word list, classify as Spanish. This handles code-switching ("necesito food en Brooklyn" → Spanish, because 2 of 4 content words are Spanish). Cost: zero. Latency: <1ms.

**Option B: `langdetect` / `langid` library.** Off-the-shelf language ID libraries. Accurate for pure-language text but unreliable for short messages and code-switched input common in NYC bilingual speakers. Adds a dependency.

**Option C: LLM detection.** Accurate but wasteful — burns an API call for something regex handles.

### 4.2 Session behavior

- First message: `detect_language()` runs. Result stored as `_language: "es"` or `_language: "en"`.
- Subsequent messages: `_language` persists. Bot responds in the session language.
- Language switch: if a user who started in Spanish sends a message that's clearly English (>80% English words), the bot switches `_language` to `"en"` and responds in English. Vice versa.
- "Start over" clears `_language` along with all other slots.

### 4.3 Greeting detection

The welcome message must detect language before the user has sent a message. Two approaches:

**Approach A (recommended):** show a bilingual welcome with a language selector. Two quick-reply buttons: "English" and "Español". The button tap sets `_language` immediately.

**Approach B:** show the welcome in English. If the user's first message is in Spanish, switch and re-send the welcome in Spanish. Feels less polished.

---

## 5. Frontend Internationalization

### 5.1 Framework

Use a simple JSON-based i18n system — no heavy framework needed for two languages. A single `messages.ts` file exports an object keyed by language code, with all UI strings.

```
messages["en"].send = "Send"
messages["es"].send = "Enviar"
```

A `useLanguage()` hook reads the session language from the chat store and returns a `t()` function that looks up strings. Components call `t("send")` instead of hardcoding `"Send"`.

The `lang` attribute on the HTML root element updates to match the session language for screen readers and browser features.

### 5.2 Strings to translate

Every hardcoded user-facing string in the frontend needs a Spanish equivalent. These fall into groups:

**Chat input and controls:**
- Placeholder text ("Type a message…" → "Escribe un mensaje…")
- Send button aria-label
- Voice input button aria-label and status messages
- Character limit warning

**Status indicators:**
- Connection status ("Connected" / "Degraded" / "Offline" → "Conectado" / "Degradado" / "Sin conexión")
- Health banner messages ("LLM unavailable — search still works" → "LLM no disponible — la búsqueda aún funciona")
- Error messages (all 5 contextual error types)

**Service card labels:**
- "Call" → "Llamar"
- "Directions" → "Direcciones"
- "Website" → "Sitio web"
- "Details" → "Detalles"
- "Also here" → "También aquí"
- "Show N more results" → "Mostrar N resultados más"
- "Learn more →" → "Más información →"
- "Rate this location" → "Califica este lugar"
- "Call for hours" → "Llame para horarios"
- "✓ Validated X days ago" → "✓ Validado hace X días"
- "⚠️ Not recently verified" → "⚠️ No verificado recientemente"
- Eligibility badges ("Ages 18-24", "Female only")
- Fees badges ("Free", "Sliding scale")

**Call confirmation dialog:**
- "Cancel" → "Cancelar"
- "Call" → "Llamar"
- "This will start a call to this number…" → "Esto iniciará una llamada a este número…"

**Quick reply buttons:** all labels need Spanish equivalents. The backend sends these, not the frontend — see Section 6.

**Feedback widget:**
- "Safe?" → "¿Seguro?"
- "Friendly?" → "¿Amable?"
- "Clean?" → "¿Limpio?"
- "LGBTQ+ friendly?" → "¿LGBTQ+ amigable?"
- "Submit" → "Enviar"

### 5.3 What stays in English

- **Organization names** — "Catholic Worker", "Ali Forney Center", "Safe Horizon" are proper nouns. Translating them would break recognition and trust.
- **Addresses** — "123 E 3rd St, New York, NY" is a physical address. Keep as-is.
- **Phone numbers** — same format in English and Spanish.
- **Admin console** — entirely English (staff tool).
- **YourPeer URLs** — stay as-is.

---

## 6. Backend Conversation — Spanish Responses

### 6.1 Response architecture

Every static response in `responses.py` needs a Spanish equivalent. The response functions accept a `lang` parameter (default `"en"`) and return the appropriate version.

The system already has a pattern for this: `responses.py` contains all hardcoded messages as constants. The Spanish implementation mirrors this structure with a parallel set of `_ES` constants or a dictionary keyed by language.

**Recommended approach:** a `_RESPONSES` dictionary keyed by `(response_key, language)`. Response functions look up by key and session language. Fallback to English if a Spanish translation is missing (graceful degradation during rollout).

### 6.2 Messages to translate

Organized by the categories in `../audits/HARDCODED_MESSAGES_REVIEW.md`:

**Welcome and greeting (7 messages):**
- Welcome message with service category buttons
- Return greeting ("Welcome back!")
- Thanks response
- Bot identity disclosure

**Confirmation flow (15+ messages):**
- "I'll look for [service] in [location] — does that sound right?"
  → "Buscaré [servicio] en [ubicación] — ¿te parece bien?"
- "I found X option(s) for you:"
  → "Encontré X opción(es) para ti:"
- Borough follow-up question
- Age follow-up question
- Family status follow-up question
- All confirmation quick-reply labels ("Yes, search" / "Change location" / "Change service" / "Start over")

**Emotional responses (9 categories):**
- Scared, sad, rough day, shame, grief, alone, distrust, undeserving, anger at situation
- Each needs a culturally appropriate Spanish equivalent — not a literal translation. Shame normalization in Spanish may use different framing than English.
- The peer navigator offer ("Talk to a person" → "Hablar con una persona")

**Crisis responses (8 categories):**
- Each crisis response includes hotline numbers. Numbers stay the same; surrounding text translates.
- 988 Suicide & Crisis Lifeline has Spanish-language service — note this in the response.
- National DV Hotline has Spanish service — note this.
- Crisis Text Line: "Text HOME to 741741" → "Envíe HOLA al 741741" (Spanish keyword differs).
- Step-down offer translates: "I can also help you find shelter" → "También puedo ayudarte a encontrar refugio"

**Error and fallback messages:**
- "I wasn't able to find any [service] in [location]" → "No pude encontrar [servicio] en [ubicación]"
- "I'm having trouble connecting" → "Tengo problemas para conectarme"
- Borough suggestions for no-result fallback
- All error recovery messages

**Warmth prefixes (7 variants):**
- "Let me see what's available." → "Déjame ver qué hay disponible."
- "I can help with that." → "Puedo ayudarte con eso."
- Each needs natural Spanish equivalents that feel warm, not robotic.

**Service category labels (10):**
- Food → Comida, Shelter → Refugio, Clothing → Ropa, etc.
- These appear in confirmation messages and quick-reply buttons.

**Quick-reply button labels:**
- All button labels are generated server-side in the `backend/app/services/chatbot/handlers/*` package (each handler emits its own quick-replies) plus `backend/app/services/responses.py` for shared button-catalog strings. Spanish bilingual acknowledgment specifically fires from `chatbot/handlers/accessibility.py` (post-Phase-3 location; was all in `chatbot.py` pre-April 2026). <!-- drift:ignore: historical chatbot.py reference; package now lives at chatbot/ -->
- Category buttons on welcome: "🍽️ Food" → "🍽️ Comida"
- Borough buttons: "Manhattan", "Brooklyn" etc. — these don't translate.
- Confirmation buttons: "✅ Yes, search" → "✅ Sí, buscar"
- Post-results: "📋 Show more results" → "📋 Mostrar más resultados"

### 6.3 LLM prompts

The system prompt in `responses.py` needs a Spanish variant. When `_language == "es"`, the LLM system prompt instructs Claude to respond in Spanish while maintaining the same safety boundaries. Key sections:

- "You are a helpful assistant..." → "Eres un asistente útil..."
- The prohibition on generating service data stays the same in Spanish.
- The tone guidelines (warm, trauma-informed) apply equally.
- The slot extraction tool schema stays in English (it's a structured API, not user-facing).

The `bot_knowledge.py` context needs Spanish answers for all 12+ topic areas (privacy, capabilities, geolocation, etc.).

### 6.4 Tone and cultural considerations

Direct translation is insufficient. Spanish-speaking users in NYC are a diverse population (Mexican, Dominican, Puerto Rican, Central American, South American) with different dialects and cultural norms. The Spanish copy should:

- Use neutral Latin American Spanish, avoiding region-specific slang.
- Use "tú" (informal) not "usted" (formal) — matches the chatbot's warm, peer-level tone. The English version says "I can help you" not "I can assist you, sir."
- Avoid overly clinical language. "Servicios de salud mental" is correct but "ayuda emocional" may resonate better for emotional responses.
- Have all translations reviewed by Streetlives community information specialists who are native Spanish speakers.

---

## 7. Slot Extraction — Spanish Keywords

### 7.1 Service keywords

Every category in `SERVICE_KEYWORDS` needs Spanish equivalents. Currently only food (3 words) and shelter (2 words) have any. The full list:

| Category | English examples | Spanish additions needed |
|---|---|---|
| food | food, hungry, meal, pantry | comida, hambre, tengo hambre, alimentos, despensa, cocina comunitaria, comedor |
| shelter | shelter, bed, housing | refugio, albergue, cama, donde dormir, techo, un lugar seguro |
| clothing | clothes, coat, shoes | ropa, abrigo, zapatos, chaqueta, vestimenta |
| personal_care | shower, hygiene, laundry | ducha, baño, higiene, lavandería, aseo |
| health_care | doctor, medical, clinic | doctor, médico, clínica, hospital, salud, enfermo/a |
| mental_health | counseling, therapy, detox | consejería, terapia, desintoxicación, rehabilitación, adicción, salud mental |
| legal | lawyer, immigration, asylum | abogado, inmigración, asilo, papeles, documentos, corte, deportación |
| employment | job, work, resume | trabajo, empleo, currículum, entrevista |
| other | benefits, ID, money, rent, eviction | beneficios, identificación, dinero, ayuda financiera, SNAP, alquiler, renta, desalojo, desahucio, ayuda con vivienda |

> **Note**: `housing_assistance` was retired in the April 15 audit. Housing-program keywords (rent, eviction, etc.) and their Spanish translations now route to the `other` category.

### 7.2 Location keywords

NYC borough and neighborhood names are the same in English and Spanish — they're proper nouns. No changes needed.

### 7.3 Confirmation and action phrases

Spanish equivalents needed for all phrase lists in `classifier.py` and `phrase_lists.py`:

- Confirm yes: "sí", "claro", "dale", "está bien", "correcto", "sí, buscar"
- Confirm no: "no", "no gracias", "no quiero", "cambiemos"
- Reset: "empezar de nuevo", "otra vez", "comenzar de nuevo"
- Thanks: "gracias", "muchas gracias"
- Help: "ayuda", "qué puedes hacer", "cómo funciona"
- Frustration: "eso no sirve", "no me ayuda", "ya intenté eso"
- Emotional: "tengo miedo", "estoy triste", "me siento solo/a"

### 7.4 Gender and demographics

Spanish gender extraction needs:
- "Soy mujer" → female
- "Soy hombre" → male
- "Soy trans" → trans (bypass filter, taxonomy boost)
- "Tengo X años" → age extraction
- "Con mi hijo/hija/bebé" → family_status: with_children
- "Embarazada" → pregnant population
- "Veterano" → veteran population
- "Discapacitado/a" → disabled population

---

## 8. Semantic Routing — Spanish Utterances

The semantic router's `all-MiniLM-L6-v2` model is primarily trained on English text. It has some multilingual capability but is not optimized for Spanish. Two options:

**Option A (recommended for pilot): Add Spanish example utterances to existing routes.** The embedding model handles short Spanish phrases reasonably well when they're similar to the English training data. Add 5-10 Spanish utterances per route alongside the existing English ones: "Necesito comida", "Tengo hambre y no tengo dinero", "Dónde puedo comer". This is zero-cost, zero-latency, and consistent with how the semantic router already works.

**Option B (future): Switch to a multilingual embedding model.** `paraphrase-multilingual-MiniLM-L12-v2` is a sentence-transformers model trained on 50+ languages including Spanish. Same API, same cosine similarity approach, but with better cross-lingual semantic matching. Tradeoff: slightly larger model (~470MB vs ~80MB), slightly slower inference (~5-10ms vs ~2-5ms). Recommended if Spanish usage exceeds 15-20% of traffic.

---

## 9. Crisis Detection — Spanish Phrases

This is the most safety-critical section. Missing a crisis in Spanish is as dangerous as missing one in English.

### 9.1 Spanish crisis phrase lists

Each of the 8 crisis categories needs Spanish regex phrases. These must be developed with clinical and cultural input — direct translation of English phrases is not sufficient because suicidal ideation, domestic violence, and distress are expressed differently across cultures.

| Category | Example English | Example Spanish |
|---|---|---|
| suicide_self_harm | "I want to kill myself" | "quiero matarme", "ya no quiero vivir", "no vale la pena vivir" |
| domestic_violence | "he hits me", "fleeing abuse" | "me golpea", "me pega", "escapé de mi pareja", "violencia doméstica" |
| youth_runaway | "ran away from home" | "me escapé de casa", "me echaron de la casa" |
| assault_victim | "got beat up" | "me golpearon", "me asaltaron", "me atacaron" |
| safety_concern | "I don't feel safe" | "no me siento seguro/a", "me están siguiendo" |
| trafficking | "I can't leave" | "no me dejan salir", "me tienen controlado/a", "me quitaron mis papeles" |
| medical_emergency | "I'm bleeding" | "estoy sangrando", "necesito una ambulancia", "me siento muy mal" |
| violence | "I'm going to hurt someone" | "voy a lastimar a alguien" |

### 9.2 Spanish crisis responses

Each crisis response needs a Spanish version with correct hotline information:

- 988 Lifeline: available in Spanish — note "presione 2 para español" in the response.
- Crisis Text Line: Spanish keyword is "HOLA" (not "HOME").
- National DV Hotline (1-800-799-7233): has Spanish service — note this.
- Safe Horizon: check if Spanish-language services are available.
- Trafficking hotline: has Spanish service.

### 9.3 LLM crisis detection prompt

The Sonnet crisis detection prompt needs a Spanish variant that includes culturally specific indirect crisis expressions. Latin American cultural norms around family honor, machismo, and religious framing may influence how distress is expressed ("Dios ya no me escucha", "soy una carga para mi familia").

### 9.4 Emotional phrase guard

The `_SUB_CRISIS_EMOTIONAL` guard that prevents over-escalation of emotional-but-not-crisis phrases needs Spanish equivalents ("tengo miedo" → emotional, not crisis; "quiero morirme" → crisis).

---

## 10. Service Data Strategy

### 10.1 The database is in English

Organization names, service descriptions, eligibility text, and hours are all in English in the Streetlives database. Translating the database is outside the scope of the chatbot project — it's a Streetlives data team decision.

### 10.2 Recommended approach: bilingual framing

The bot's conversational messages are in Spanish. Service cards display English data (org names, addresses, descriptions) with Spanish labels around them. This is the standard pattern for bilingual service directories — users searching for services in NYC are accustomed to English organization names.

Specifically:
- Card labels (Call, Directions, Details, etc.) → Spanish
- Organization name → English (proper noun)
- Address → English (physical address)
- Hours → English format (but "Mon-Fri" → "Lun-Vie" is a small improvement)
- Description → English (from DB). Future: add a `description_es` column to the DB.
- "Also here" tag labels → Spanish equivalents from the taxonomy map
- Eligibility text → translate the template ("Ages 18-24" → "Edades 18-24")

### 10.3 Language-spoken boost

When `_language == "es"`, the query should boost services where the `languages` table includes Spanish. This surfaces services with Spanish-speaking staff — a significant quality-of-life improvement. Implementation: add a `FILTER_BY_LANGUAGE_BOOST` to the SQL templates that adds an ORDER BY rank for services with matching language.

---

## 11. PII Detection

The PII redactor needs minor updates for Spanish:
- Phone number patterns: same as English (US format).
- Name patterns: the regex for names should handle Spanish naming conventions (two last names, e.g., "María García López"). The current name detection is weak (known gap) — this is an incremental improvement, not a blocker.
- SSN patterns: same as English.
- Email patterns: same as English.
- Address patterns: same format in NYC.

---

## 12. Evaluation Framework

### 12.1 Spanish eval scenarios

Add 15-20 Spanish scenarios across the same 20 categories as English. Priority scenarios:

- `spanish_happy_path` — simple service request in Spanish ("Necesito comida en Brooklyn")
- `spanish_crisis_suicide` — suicidal ideation in Spanish
- `spanish_crisis_dv` — domestic violence in Spanish
- `spanish_multi_intent` — multi-service request in Spanish
- `spanish_emotional` — emotional message in Spanish
- `spanish_code_switch` — mixed Spanish/English ("Necesito shelter en Manhattan")
- `spanish_confirmation` — confirm/deny flow in Spanish
- `spanish_natural_language` — peer-written Spanish queries based on lived experience
- `spanish_lgbtq_youth` — LGBTQ youth query in Spanish
- `spanish_detox` — substance use in Spanish

### 12.2 Scoring

The same 11 dimensions apply. `Cultural Responsiveness` and `Equity of Access` become especially important — a Spanish speaker should not receive lower scores on any dimension compared to the English equivalent.

Add a new dimension or sub-metric: `Language Consistency` — did the bot respond in the same language the user used? Did it switch languages inappropriately?

---

## 13. Implementation Plan

### Phase 1: Detection + welcome (1 week)

- Implement `detect_language()` with expanded regex
- Add `_language` session slot
- Bilingual welcome screen with language selector buttons
- Frontend `useLanguage()` hook + `messages.ts` with 10-15 core strings
- No Spanish responses yet — just detection and UI scaffolding

### Phase 2: Core conversation in Spanish (2 weeks)

- Spanish response constants in `responses.py` (all confirmations, follow-ups, no-results)
- Spanish quick-reply button labels
- Spanish service category labels
- Spanish LLM system prompt
- Spanish keywords for all 9 service categories in `slot_extraction_regex.py`
- Spanish confirmation/action phrase lists in `classifier.py`
- 5-10 Spanish utterances per semantic route

### Phase 3: Crisis and emotional safety (1 week)

- Spanish crisis phrase lists for all 8 categories (requires clinical review)
- Spanish crisis response messages with correct hotline information
- Spanish emotional response messages (requires cultural review)
- Spanish emotional phrase guard
- LLM crisis detection prompt in Spanish

### Phase 4: Frontend completion (1 week)

- All remaining frontend strings translated
- Service card labels in Spanish
- Call confirmation dialog in Spanish
- Error messages in Spanish
- Accessibility: `lang="es"` attribute, Spanish aria-labels
- `languages_spoken` boost in SQL templates

### Phase 5: Evaluation and refinement (1 week)

- 15-20 Spanish eval scenarios
- Run eval suite, analyze gaps
- Cultural review by native Spanish-speaking community information specialists
- Iterate on tone, phrasing, and coverage based on feedback

### Total estimate: 6 weeks

---

## 14. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Literal translation sounds robotic | Users disengage, don't trust the bot | All translations reviewed by native speakers on the Streetlives team. Use "tú" form, natural phrasing. |
| Spanish crisis phrases are incomplete | Missed crisis = real harm | Clinical review of phrase lists. Fail-open policy applies: if Spanish LLM crisis check fails, show safety resources in Spanish anyway. |
| Code-switching confuses the classifier | Wrong language response, broken flow | Session-level language with >80% threshold for switching. LLM handles mixed input naturally. |
| Embedding model weak on Spanish | Semantic router misses Spanish intent → falls to LLM | Add Spanish utterances to routes. Monitor LLM fallthrough rate for Spanish sessions. Upgrade to multilingual model if >20% miss rate. |
| Database content is English only | Spanish user sees English descriptions on cards | Bilingual framing (Spanish labels, English data). Long-term: `description_es` column in DB. |
| Dialect variation across Latin American populations | Some phrases feel foreign to some users | Use neutral Latin American Spanish. Avoid region-specific slang. Community review. |
| Maintenance burden doubles | Every new feature needs both languages | Centralized `messages.ts` and `_RESPONSES` dictionary make additions mechanical. Lint check: every English key must have a Spanish key. |

---

## 15. Open Questions for Streetlives Team

1. **"Tú" vs "usted"?** The English version is informal and warm. Should Spanish match with "tú" (informal) or use "usted" (formal, shows respect)? Recommendation: "tú" — but the team should decide.

2. **Which community information specialists can review Spanish translations?** The tone and cultural sensitivity require native-speaker review, not machine translation.

3. **Priority for crisis phrase development?** Should we engage a clinical advisor with Spanish-language crisis experience, or rely on the LLM for indirect Spanish crisis detection while regex covers explicit phrases?

4. **Database translations?** Is there interest in adding `description_es` columns to the Streetlives database long-term? This would enable fully Spanish service cards but is a separate data project.

5. **Other languages?** NYC has large Chinese (Mandarin/Cantonese), Bengali, Haitian Creole, Russian, and Arabic-speaking populations. Should the architecture support arbitrary language expansion, or is Spanish the only target for the foreseeable future?

6. **Language preference persistence?** If a user returns (within the 30-minute session window), should their language preference be remembered? Currently sessions are anonymous — there's no mechanism to remember across sessions.
