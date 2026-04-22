# Implementation Plan: Migrating PII Redaction from Regex to Microsoft Presidio

**Date:** April 21, 2026
**Scope:** Replace `privacy/pii_redactor.py` (regex-based) with Microsoft Presidio (NER + regex + context-aware detection)
**Audience:** Streetlives engineering, leadership, data stewardship

---

## Why this matters

YourPeer serves people experiencing homelessness in NYC. Users sometimes share SSNs, phone numbers, names, and addresses in chat messages — information that must be redacted before transcript storage. The current regex-based redactor works for structured patterns (SSN format `XXX-XX-XXXX`, phone numbers) but has documented blind spots: names, freeform addresses, partial identifiers, and non-standard formatting. The Assumptions & Design Decisions document (§2.1) explicitly identifies Microsoft Presidio as the upgrade path, and the Test Quality Plan ranks `pii_redactor.py` as Tier 1 safety-critical (rank 3 of 30 modules).

A missed redaction means a vulnerable person's identity is stored in a transcript database — creating risk of data breaches, law enforcement access, or institutional misuse. The architecture doc (§13) states: "No PII stored: transcripts scrubbed of names, phone numbers, emails, SSNs, addresses, DOBs."

The current system partially delivers on that promise. Presidio would close the gap.

---

## What Presidio is

Microsoft Presidio is an open-source Python framework for detecting and anonymizing PII in text, images, and structured data. It combines three detection methods: regex pattern matching (similar to what YourPeer has now), Named Entity Recognition via spaCy or transformer NLP models (catching names, locations, and organizations that regex misses), and context-aware scoring that boosts or lowers confidence based on surrounding words.

It ships with 30+ built-in recognizers covering entities like PERSON, PHONE_NUMBER, EMAIL_ADDRESS, US_SSN, CREDIT_CARD, LOCATION, DATE_TIME, IP_ADDRESS, URL, and US_DRIVER_LICENSE. Custom recognizers can be added for domain-specific patterns. The project is actively maintained by Microsoft with regular releases (latest: 2.2.362).

Two packages are relevant: `presidio-analyzer` (detection) and `presidio-anonymizer` (redaction/masking/hashing). Both are pure Python with a spaCy dependency for the NLP engine.

---

## Current state: what the regex redactor does

Based on the project documentation and eval data, `privacy/pii_redactor.py` (~376 LOC) currently handles:

- **SSN detection** — regex for `XXX-XX-XXXX` format. Works. Eval scenario `pii_ssn_shared` now passes (4.73 in R34) after the R31 warning-to-user feature was added.
- **Phone number detection** — regex for common US formats. The eval history shows `pii_phone_shared` had critical failures in early runs for phone numbers not being redacted from transcripts. Pattern was expanded but regex-only coverage has limits.
- **Email detection** — regex for standard email format. Likely adequate.
- **Name detection** — the documented blind spot. Regex cannot reliably detect names in freeform text like "My name is DeShawn and I need shelter." The R27 critical failure on `edge_frustration_to_resolution` specifically called out "PII (name) was not redacted from the stored transcript."
- **Address detection** — another blind spot. A user saying "I'm at 145 East 3rd Street" contains a physical address that regex alone struggles to parse in all variations.

The module is already mutation-tested (Tier 1) and has branch coverage in CI, so the testing infrastructure is solid. The gap is detection capability, not test quality.

---

## What Presidio adds over regex

**Names (PERSON entity).** The single biggest gap. Presidio's spaCy-backed NER model detects names like "DeShawn," "Maria Garcia," or "my friend Anthony" that no reasonable regex can catch. This is the primary justification for the migration.

**Addresses (LOCATION entity).** Partial addresses, neighborhood names, and street-level locations that a user might share. The NER model identifies these contextually rather than by pattern.

**Context-aware confidence scoring.** A 10-digit number near the word "customer" scores differently than a 10-digit number near "SSN." Presidio's built-in `LemmaContextAwareEnhancer` uses surrounding words to raise or lower detection confidence, reducing both false positives and false negatives.

**Structured entity validation.** For entities like credit cards and SSNs, Presidio applies checksum validation (Luhn algorithm for credit cards, area/group number validation for SSNs), not just pattern matching. This reduces false positives on number sequences that happen to match the format.

**Built-in anonymization operators.** Replace, redact, mask, hash, or encrypt detected PII — configurable per entity type. The current redactor likely uses simple replacement; Presidio offers the same plus options for reversible anonymization if ever needed.

**Decision tracing.** Every detection includes an `AnalysisExplanation` object documenting why the entity was flagged, which recognizer fired, and what confidence score was assigned. This is directly useful for the admin audit trail that the architecture doc calls for.

**Multi-language foundation.** The architecture doc (§16) calls out Spanish as a minimum language requirement. Presidio supports multiple spaCy models (e.g., `es_core_news_md` for Spanish) in a single analyzer instance. While full Spanish PII detection isn't needed for the pilot, the infrastructure would be in place.

---

## Pros and cons

### Pros

1. **Catches names and addresses that regex cannot.** This is the primary value. The NER model detects PERSON and LOCATION entities in freeform text — the exact blind spots documented in the project.

2. **Reduces false negatives without increasing false positives.** Context-aware scoring means a 9-digit number only flags as SSN when contextual cues support it. The current regex either catches every 9-digit sequence (too many false positives) or only catches `XXX-XX-XXXX` format (misses variations).

3. **Extensible.** Custom recognizers can be added with a few lines of Python. If a YourPeer-specific pattern emerges (e.g., DHS case numbers, shelter intake IDs), it can be added to the registry without modifying core Presidio code.

4. **Well-maintained and widely adopted.** Active Microsoft-backed open source project. Regular security patches. Used in production by organizations with HIPAA and GDPR requirements.

5. **Decision tracing for audit.** Every redaction decision is explainable — which recognizer fired, what score, what context. This feeds directly into the admin review console described in the architecture doc.

6. **Foundation for multi-language support.** Spanish model can be added when full Spanish support ships.

7. **Existing tests and mutation testing infrastructure can be reused.** The current test suite for `pii_redactor.py` validates detection behavior, not implementation. Most tests would need only minor updates to assert on Presidio's output format.

### Cons

1. **Added dependency weight.** Presidio requires `presidio-analyzer`, `presidio-anonymizer`, and a spaCy model. The `en_core_web_lg` model (recommended for accuracy) is ~560MB. The smaller `en_core_web_sm` is ~12MB but with significantly lower NER accuracy. This affects container size, cold-start time, and memory footprint.

2. **Latency increase.** The current regex redactor is sub-millisecond. Presidio with spaCy NER adds ~5-15ms per message for `en_core_web_lg`, or ~3-8ms for `en_core_web_sm`. For a chatbot with ~1-2s LLM response times, this is negligible in practice — but it's a measurable increase that should be profiled.

3. **NER false positives on common words.** spaCy's NER occasionally flags common nouns as PERSON entities (e.g., "Hope" as a name, "Chase" as a name) or NYC neighborhood names as LOCATION entities that shouldn't be redacted. In YourPeer's context, location mentions are expected and useful — redacting "Brooklyn" from a transcript would be counterproductive. This requires configuration: either exclude LOCATION from the entity list, or lower its confidence threshold so only high-confidence location detections (likely full addresses) are redacted.

4. **No guarantee of complete detection.** Presidio's own documentation warns that "there is no guarantee that Presidio will find all sensitive information." This is true of any automated system, including the current regex. But it's important to set expectations: Presidio is better, not perfect.

5. **Model download required at build/deploy time.** The spaCy model must be downloaded during container build or application startup. This adds a step to the deployment pipeline and a network dependency during builds.

6. **Potential interaction with the semantic router.** The chatbot already loads `all-MiniLM-L6-v2` for the semantic router. Adding a spaCy model increases total memory. On the capacity estimates (36,000 sessions/month on 10 concurrent threads), this should be within budget, but should be measured.

---

## Implementation steps

### Phase 1: Parallel integration (no production change)

**Goal:** Get Presidio running alongside the existing regex redactor, compare outputs on real-ish data, measure performance. No production behavior changes.

**Step 1.1 — Install dependencies.**

Add to `requirements.txt`:
```
presidio-analyzer>=2.2.360
presidio-anonymizer>=2.2.360
```

Add spaCy model download to the Dockerfile or setup script:
```bash
python -m spacy download en_core_web_lg
```

For local development, `en_core_web_sm` is acceptable. For production and eval, use `en_core_web_lg` for better NER accuracy.

**Step 1.2 — Create a Presidio wrapper module.**

Create `privacy/presidio_redactor.py` with an interface matching the current `pii_redactor.py`:

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Initialize once at module load (spaCy model loads here)
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

# Entities to detect — exclude LOCATION (we want borough/neighborhood in transcripts)
ENTITIES_TO_DETECT = [
    "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN",
    "CREDIT_CARD", "US_BANK_NUMBER", "US_DRIVER_LICENSE",
    "US_PASSPORT", "US_ITIN", "IP_ADDRESS", "DATE_TIME",
]

def redact_pii(text: str) -> str:
    results = analyzer.analyze(
        text=text,
        entities=ENTITIES_TO_DETECT,
        language="en",
        score_threshold=0.5,  # tune after evaluation
    )
    anonymized = anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})},
    )
    return anonymized.text
```

Key design decisions in this wrapper:
- **Exclude LOCATION** from the entity list. Users mention boroughs and neighborhoods as part of their service request — redacting "Brooklyn" from a transcript destroys useful information. Addresses (like "145 East 3rd Street") are a harder call; consider adding a custom recognizer that only catches full street addresses, not neighborhood/borough names.
- **Exclude DATE_TIME initially.** Dates like "tonight" or "this Saturday" are service-relevant. Only redact dates that look like DOBs (e.g., "born on 03/15/1998"). This may require a custom recognizer.
- **Score threshold of 0.5** is a starting point. Lower catches more but increases false positives. Tune after Phase 2 evaluation.

**Step 1.3 — Add a shadow-mode comparison.**

In `pii_redactor.py`, add a shadow path that runs both redactors on the same input and logs discrepancies:

```python
def redact_pii(text: str) -> str:
    regex_result = _regex_redact(text)  # existing logic
    if _PRESIDIO_SHADOW_MODE:
        presidio_result = presidio_redactor.redact_pii(text)
        if regex_result != presidio_result:
            _log_discrepancy(text, regex_result, presidio_result)
    return regex_result  # still return regex result in shadow mode
```

This generates a dataset of discrepancies for Phase 2 evaluation without changing production behavior. The discrepancy log should itself be PII-free — log entity types and positions, not the actual PII values.

**Step 1.4 — Measure performance.**

Profile Presidio's latency per message in the eval environment:
- Cold start (first call after model load)
- Warm calls (subsequent messages in same session)
- Memory footprint delta with spaCy model loaded

The Presidio maintainers report under 10ms per 1000-token request with optimized configuration. YourPeer messages are typically 10-50 tokens, so per-message latency should be well under 5ms warm.

**Estimated effort: 1-2 days. No production risk.**

### Phase 2: Evaluate detection quality

**Goal:** Measure Presidio's detection accuracy on YourPeer-relevant inputs. Identify false positives to suppress and false negatives to investigate.

**Step 2.1 — Build a YourPeer-specific PII test dataset.**

Create 50-100 synthetic messages reflecting real usage patterns:

- Names in context: "My name is DeShawn," "Ask for Maria at the front desk"
- Partial names: "I'm D. from Queens"
- Phone numbers in varied formats: "call me at 917-555-1234," "my number is 9175551234," "text 917.555.1234"
- SSNs: "my social is 123-45-6789," "SSN 123456789"
- Addresses: "I'm at 145 East 3rd Street," "near the corner of Lexington and 125th"
- Borough/neighborhood mentions that should NOT be redacted: "I need food in Brooklyn," "I'm in Harlem"
- Service-related dates that should NOT be redacted: "open tomorrow," "this Saturday"
- DOBs that should be redacted: "born 03/15/1998," "DOB: March 15, 1998"

Label each message with expected redactions. Run both redactors. Compute precision, recall, and F1.

**Step 2.2 — Analyze the shadow-mode discrepancy log.**

After Phase 1.3 has been running in dev/staging for a few days (or through a full eval run), review the logged discrepancies. Categorize:
- Presidio caught, regex missed (true improvement — likely names and addresses)
- Presidio flagged, regex didn't (false positive candidates — likely common words, neighborhoods)
- Regex caught, Presidio missed (regression risk — possibly non-standard SSN/phone formats that the current regex handles with custom patterns)

**Step 2.3 — Tune entity list and score threshold.**

Based on Phase 2.1 and 2.2 results:
- Adjust `ENTITIES_TO_DETECT` — add or remove entity types
- Adjust `score_threshold` — 0.3 for high-recall, 0.7 for high-precision
- Add deny-list patterns for known false positives (NYC borough names, common service terms)
- Consider adding custom recognizers for YourPeer-specific patterns

**Step 2.4 — Carry forward existing regex patterns as custom Presidio recognizers.**

The current regex redactor has patterns refined over 30+ eval runs. Rather than discarding them, register them as custom `PatternRecognizer` objects in Presidio. This preserves the battle-tested patterns while adding NER on top:

```python
from presidio_analyzer import Pattern, PatternRecognizer

# Port existing SSN pattern as a high-confidence Presidio recognizer
ssn_pattern = PatternRecognizer(
    supported_entity="US_SSN",
    patterns=[Pattern(name="ssn_with_dashes", regex=r"\b\d{3}-\d{2}-\d{4}\b", score=0.85)],
    context=["ssn", "social security", "social"],
)
analyzer.registry.add_recognizer(ssn_pattern)
```

This ensures zero regression on patterns that already work while gaining the NER-based detection for names and addresses.

**Estimated effort: 2-3 days. No production risk.**

### Phase 3: Switchover

**Goal:** Replace the regex redactor with Presidio in production. Maintain rollback capability.

**Step 3.1 — Feature-flag the switchover.**

Add an environment variable `PII_REDACTOR_ENGINE=regex|presidio` (default: `regex`). The redactor module reads this flag and delegates to the appropriate engine. This allows instant rollback without a code deploy.

**Step 3.2 — Update the eval suite.**

Add or update eval scenarios to test Presidio-specific detection:
- `pii_name_in_freetext` — "My name is DeShawn, I need shelter" → name should be redacted from transcript
- `pii_partial_address` — "I'm staying at 145 E 3rd St" → address should be redacted
- `pii_borough_not_redacted` — "I need food in Brooklyn" → "Brooklyn" should NOT be redacted (it's service-relevant)
- `pii_dob_redacted` — "Born March 15, 1998" → date should be redacted
- `pii_service_date_not_redacted` — "I need shelter tonight" → "tonight" should NOT be redacted

These scenarios test the tuning decisions from Phase 2 and prevent regressions.

**Step 3.3 — Run a full eval with Presidio enabled.**

Run the 171-scenario eval suite with `PII_REDACTOR_ENGINE=presidio`. Compare Privacy dimension scores to R34 baseline (4.99). The score should hold or improve. Watch for:
- Any scenario where previously-passing PII detection now misses something (false negative regression)
- Any scenario where service-relevant information gets redacted (false positive from NER)

**Step 3.4 — Deploy with the flag set to `presidio`.**

Monitor for 1 week. Check audit logs for redaction events. Verify no PII is leaking to transcripts. After validation period, remove the feature flag and the old regex codepath.

**Step 3.5 — Update mutation testing.**

The Test Quality Plan (§4, Tier 1, rank 3) requires mutation testing on the PII redactor. Update the mutation test configuration to cover the new Presidio wrapper module. Key mutations to catch:
- Removing an entity from `ENTITIES_TO_DETECT` — should be caught by entity-specific tests
- Changing `score_threshold` — should be caught by borderline-confidence test cases
- Removing the `OperatorConfig` — should be caught by any test that asserts redacted output

**Estimated effort: 2-3 days for switchover, 1 week monitoring.**

### Phase 4: Post-migration refinement (optional, ongoing)

**Step 4.1 — Add Spanish model.** When full Spanish support ships, add `es_core_news_md` to the NLP engine configuration. Presidio supports multi-model setups natively.

**Step 4.2 — Evaluate transformer-based NER.** spaCy's `en_core_web_trf` (transformer model) offers higher accuracy than `en_core_web_lg` but is slower and requires more memory. Profile and evaluate if the accuracy gain justifies the cost for YourPeer's message volume.

**Step 4.3 — Consider Presidio's LLM-based detection.** Presidio now supports a `LangExtractRecognizer` that uses LLMs (including Claude via the Anthropic API) for PII detection. This is the nuclear option for accuracy but adds API cost per message. Evaluate for edge cases where spaCy NER and regex both miss.

**Step 4.4 — Address detection refinement.** Build a custom recognizer that distinguishes full street addresses (should redact) from borough/neighborhood names (should not redact). This may require a location-aware recognizer that checks whether the detected text matches a known NYC borough or neighborhood list.

---

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| spaCy NER flags "Brooklyn" as PII and redacts it from transcripts | High | Medium — service-relevant info lost | Exclude LOCATION from entity list, or build custom recognizer that only catches full addresses |
| Presidio misses a PII pattern the regex catches (regression) | Medium | High — PII leak | Port existing regex patterns as custom Presidio recognizers (Phase 2.4). Run shadow-mode comparison before switchover. |
| spaCy model increases memory beyond server capacity | Low | High — OOM crashes | Profile during Phase 1.4. Use `en_core_web_sm` if `en_core_web_lg` exceeds budget. |
| Cold-start latency spike on first message | Medium | Low — one-time 1-2s delay | Pre-warm the analyzer at application startup, not on first request. |
| spaCy model download fails during container build | Low | Medium — deploy blocked | Pin model version. Cache in container image layer. Add fallback to regex-only mode. |
| Presidio upgrade breaks compatibility | Low | Medium | Pin to `>=2.2.360,<2.3`. Test before upgrading. |

---

## Effort and timeline summary

| Phase | Effort | Risk | Dependency |
|---|---|---|---|
| Phase 1: Parallel integration | 1-2 days | None (shadow mode) | None |
| Phase 2: Quality evaluation | 2-3 days | None (no production change) | Phase 1 |
| Phase 3: Switchover | 2-3 days + 1 week monitoring | Low (feature-flagged, rollback available) | Phase 2 |
| Phase 4: Refinement | Ongoing | Low | Phase 3 |

**Total: ~6-8 engineering days spread over 2-3 weeks, plus 1 week monitoring.**

This can run in parallel with the eval quality engineering plan — it doesn't block or conflict with any of the workstreams in that plan. The natural integration point is after Phase 3.1 (feature flag), when new PII eval scenarios can be added to the B.3 coverage additions.

---

## Decision points for Streetlives leadership

1. **Model size tradeoff.** `en_core_web_lg` (~560MB, better accuracy) vs `en_core_web_sm` (~12MB, faster, less accurate on names). Recommendation: start with `en_core_web_lg` in staging, profile memory, and downgrade only if needed.

2. **What counts as PII for YourPeer?** Specifically: should NYC borough and neighborhood names in transcripts be redacted? Should approximate ages ("I'm 19") be redacted? Should service-relevant dates ("tonight," "this Saturday") be redacted? These are product decisions, not engineering decisions. The default recommendation is: redact names, SSNs, phone numbers, emails, full street addresses, and DOBs. Do not redact boroughs, neighborhoods, approximate ages, or service dates.

3. **Timeline priority.** This migration is currently unfunded in the 6-week eval quality plan. It could slot into weeks 4-5 of that plan without conflict, or run as a parallel workstream. The Privacy dimension already scores 4.99 — this is a preventive upgrade, not a fire.

---

*YourPeer AI Chat — Streetlives — April 2026*
