// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Client-side PII redactor — strips personally identifiable information
 * from text before it is persisted in IndexedDB.
 *
 * Why this exists on the client:
 *
 * The send queue (send-queue.ts) and pending-responses store
 * (pending-responses.ts) hold user-typed text in IndexedDB for up to
 * an hour. With Background Sync, that text can also be DELIVERED to
 * the server even when the tab is closed. For a vulnerable population
 * — homeless, undocumented, fleeing DV, runaway minors — having "the
 * last thing I typed" sit on disk in cleartext is a meaningful threat
 * model: shared phones, lost devices, device seizure, family snooping.
 *
 * The server already runs a richer redactor (backend/app/privacy/
 * pii_redactor.py) before transcripts are written to its database. This
 * module mirrors a precision-focused subset of those patterns so that
 * we redact AT enqueue time, BEFORE anything PII-bearing reaches IDB.
 *
 * Trade-offs vs. server-side redaction:
 *
 * - When the user is online, we still send the ORIGINAL text — the
 *   server needs it to drive its PII-warning UX (e.g. "Don't share
 *   your SSN here") and applies its own redaction post-receipt before
 *   storing the transcript. Client-side redaction does not run on the
 *   live wire.
 * - When the user is offline (queued path), we redact BEFORE storing
 *   in IDB, and the redacted version is what eventually gets sent.
 *   Consequence: offline-typed PII does not trigger the server's live
 *   warning UX. We accept this — the safer outcome (no PII at rest, no
 *   PII transmitted) outweighs the lost warning.
 * - We do NOT redact what the user sees in their chat UI. They typed
 *   it; they expect to see it. The displayed message and the queued
 *   payload are intentionally distinct: the queue is about "what we
 *   persist", not "what we show". The display layer's localStorage
 *   persistence is a separate, larger initiative (see Presidio
 *   discussion in YourPeer Assumptions doc §2.1).
 *
 * Coverage decisions:
 *
 * The server runs a broad name-detection regex with a 100+ entry
 * blocklist to suppress false positives on phrases like "I'm scared".
 * On the client we are MORE conservative — only the highest-confidence
 * name introductions ("my name is X", "call me X") and bare-on-greeting
 * patterns are matched. Bare "I'm X" is intentionally NOT matched here
 * because the false-positive rate on short messages with adjectives is
 * too high, and getting a false positive would mean redacting the
 * user's actual question. The server can afford a richer pattern
 * because it sees full conversation context; the client sees one
 * message at a time.
 *
 * Address detection is also narrowed: we match the high-precision
 * "<number> <Capitalized> <suffix>" pattern, but skip the preposition
 * pattern ("at 123 Main") which has higher FP risk on the client.
 *
 * If a future iteration needs richer detection, the upgrade path is
 * Microsoft Presidio (NER-based) per the design doc — same as the
 * server.
 *
 * Pattern parity with the server:
 *
 * The regex patterns below are translated 1:1 from
 * backend/app/privacy/pii_redactor.py except where noted. JS regex is
 * a strict superset of what those patterns use (lookbehind, lookahead,
 * character classes, alternation), so the translation is mechanical.
 * The placeholders ([SSN], [EMAIL], etc.) match the server's exactly,
 * so a redacted message looks the same regardless of which side
 * produced it.
 *
 * Detection order is fixed and meaningful: more-specific patterns first,
 * each later pattern skipping spans already claimed. SSNs are a digit
 * subset of phone numbers; credit cards too — running them first
 * prevents a 10-digit phone match from eating an SSN-shaped substring.
 */

export type PIIType =
  | "ssn"
  | "email"
  | "credit_card"
  | "phone"
  | "url"
  | "dob"
  | "address"
  | "name"
  | "gender";

export interface Detection {
  type: PIIType;
  start: number;
  end: number;
}

export interface RedactionResult {
  /** Text with each detection replaced by its placeholder. */
  redacted: string;
  /** All detections found, in order of position in the original text. */
  detections: Detection[];
}

const PLACEHOLDERS: Record<PIIType, string> = {
  ssn: "[SSN]",
  email: "[EMAIL]",
  credit_card: "[CREDIT_CARD]",
  phone: "[PHONE]",
  url: "[URL]",
  dob: "[DOB]",
  address: "[ADDRESS]",
  name: "[NAME]",
  gender: "[GENDER]",
};

// ---------------------------------------------------------------------------
// 1. SSN — three-digit / two-digit / four-digit pattern
// ---------------------------------------------------------------------------
// The Python source uses \b boundaries; JS \b behaves identically for
// digits. The capture groups exist in the server pattern but we only
// use them for length validation — same here.
const SSN_RE = /\b(\d{3})[-\s]?(\d{2})[-\s]?(\d{4})\b/g;

// ---------------------------------------------------------------------------
// 2. Email
// ---------------------------------------------------------------------------
const EMAIL_RE = /\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b/g;

// ---------------------------------------------------------------------------
// 3. Credit card — separated and unseparated runs, both Luhn-validated
// ---------------------------------------------------------------------------
// Run BEFORE phone because card numbers overlap with phone digit
// ranges; without this order a 10-digit card prefix could be
// classified as a phone number.
const CC_SEPARATED_RE = /\b(\d{4})[-\s]?(\d{4})[-\s]?(\d{4})[-\s]?(\d{1,7})\b/g;
const CC_LONG_RE = /\b(\d{13,19})\b/g;

/** Luhn checksum — ISO/IEC 7812-1, used by Visa/Mastercard/Amex/etc.
 *  Matches backend/app/privacy/pii_redactor.py::_luhn_check. */
function luhnCheck(digits: string): boolean {
  let total = 0;
  // Process right-to-left; double every second digit.
  for (let i = 0; i < digits.length; i++) {
    const idxFromRight = digits.length - 1 - i;
    let d = parseInt(digits.charAt(idxFromRight), 10);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

// ---------------------------------------------------------------------------
// 4. Phone — 10-11 digit US-style numbers with optional separators
// ---------------------------------------------------------------------------
// Lookbehind/lookahead prevent partial matches inside longer digit runs
// (those would be a CC, SSN, or just garbage).
const PHONE_RE = new RegExp(
  // Not preceded by another digit
  "(?<!\\d)" +
    // Optional country code "+1" or "1"
    "(?:\\+?1[-.\\s]?)?" +
    // Area code, possibly parenthesized
    "(?:\\(?\\d{3}\\)?[-.\\s]?)" +
    // Subscriber number
    "\\d{3}[-.\\s]?\\d{4}" +
    // Not followed by another digit
    "(?!\\d)",
  "g",
);

// ---------------------------------------------------------------------------
// 4b. URL — full URLs and bare social-media domains
// ---------------------------------------------------------------------------
// Social handles can be identifying. We match either "https?://..." or
// "facebook.com/handle" style bare references, case-insensitive.
const URL_RE =
  /https?:\/\/[^\s<>"']+|\b(?:facebook|instagram|twitter|tiktok|linkedin|youtube|snapchat)\.com\/[^\s<>"']+/gi;

// ---------------------------------------------------------------------------
// 5. DOB — numeric (12/25/1990) and written (December 25, 1990)
// ---------------------------------------------------------------------------
const DOB_NUMERIC_RE = /\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b/g;
const MONTHS =
  "january|february|march|april|may|june|july|august|september|" +
  "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec";
const DOB_WRITTEN_RE = new RegExp(
  `\\b(${MONTHS})\\s+\\d{1,2},?\\s+\\d{2,4}\\b`,
  "gi",
);

// ---------------------------------------------------------------------------
// 6. Address — narrowed subset of the server patterns
// ---------------------------------------------------------------------------
// Standard "<num> <Word> [<Word>] <Suffix>" plus apartment/unit, ordinal
// "<num> [North|South|...] <Nth> <Suffix>", and the Broadway special
// case. We deliberately omit the preposition pattern ("at 123 Main")
// from the server — too noisy for a single-message client view.
const STREET_SUFFIXES =
  "(?:Street|St\\.?|Avenue|Ave\\.?|Boulevard|Blvd\\.?|Road|Rd\\.?" +
  "|Drive|Dr\\.?|Lane|Ln\\.?|Place|Pl\\.?|Court|Ct\\.?" +
  "|Way|Terrace|Ter\\.?)";
const APT_SUFFIX = "(?:\\s+(?:Apt|Unit|Suite|Ste|Fl|Rm|#)\\s*\\w+)?";

const ADDR_STANDARD_RE = new RegExp(
  `\\b\\d+\\s+[A-Z][a-zA-Z]+(?:\\s+[A-Z][a-zA-Z]+)?\\s+${STREET_SUFFIXES}${APT_SUFFIX}`,
  "g",
);
const ADDR_ORDINAL_RE = new RegExp(
  `\\b\\d+\\s+(?:(?:North|South|East|West|N|S|E|W)\\s+)?` +
    `\\d+(?:st|nd|rd|th)\\s+${STREET_SUFFIXES}${APT_SUFFIX}`,
  "gi",
);
const ADDR_BROADWAY_RE = new RegExp(`\\b\\d+\\s+Broadway${APT_SUFFIX}`, "gi");

// ---------------------------------------------------------------------------
// 7. Name — high-confidence introductions only
// ---------------------------------------------------------------------------
// "my name is X", "call me X", "this is X" — these are explicit name
// declarations. The server also runs a bare "I'm X" pattern but that
// has too many FPs on short messages (e.g. "I'm scared", "I'm 21",
// "I'm hungry" — a stray capitalized word would be misclassified).
//
// Bare-greeting "Hi Bryan" is included because the cost of redacting a
// non-name word in this position is low (it would have been a noun
// like "Hi mom" anyway, which is fine to redact in a stored transcript).
const NAME_INTRO_RE =
  /(?:my name is|my name's|name's|call me|this is|i am called|they call me|everyone calls me|you can call me)\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)?)/gi;
const NAME_HI_RE =
  /(?:^|[.!?]\s*)(?:hi|hey|hello|dear)\s+([A-Z][a-z]{2,})\b/gi;

// Words that LOOK like names per the regex but are common false
// positives. Subset of the server's blocklist — extending only as
// needed for the introduction patterns.
const NAME_BLOCKLIST = new Set<string>([
  "there",
  "everyone",
  "all",
  "friend",
  "guys",
  "back",
  "out",
  "home",
]);

// ---------------------------------------------------------------------------
// 8. Gender identity — sensitive for this population
// ---------------------------------------------------------------------------
// Outing someone or storing their gender disclosure could be harmful —
// shelter mismatches, family discovery, cultural risk. We redact
// self-identifying phrases in stored transcripts. The displayed
// message in the chat UI still reads as the user typed it (the bot
// also sees the original on online sends), so this only affects what
// goes to disk via the offline queue.
//
// Only matches identity DECLARATIONS, not incidental references — "I'm
// a trans woman" matches; "the man at the counter" does not.
const GENDER_IDENTITY_RE = new RegExp(
  "\\b(?:" +
    // Lead-in: "I'm", "I am", "as", optional "a"
    "(?:i['’]?m|i am|as)\\s+(?:a\\s+)?" +
    // Identity terms
    "(?:trans\\s*(?:woman|man|gender|masculine|feminine)|" +
    "non[\\- ]?binary|enby|genderqueer|gender[\\- ]?fluid|agender|" +
    "lgbtq\\+?|lgbt|queer|gay|lesbian|bisexual|" +
    "mtf|ftm)" +
    ")\\b",
  "gi",
);

// ---------------------------------------------------------------------------
// Detection orchestration
// ---------------------------------------------------------------------------

interface ClaimedSpan {
  start: number;
  end: number;
}

function overlaps(start: number, end: number, claimed: ClaimedSpan[]): boolean {
  for (const c of claimed) {
    if (start < c.end && end > c.start) return true;
  }
  return false;
}

function pushDetection(
  detections: Detection[],
  claimed: ClaimedSpan[],
  type: PIIType,
  start: number,
  end: number,
): void {
  detections.push({ type, start, end });
  claimed.push({ start, end });
}

/**
 * Run a global regex against the text and call `onMatch` for every
 * non-overlapping hit (relative to spans already claimed by earlier
 * passes). The callback is responsible for deciding whether to record
 * the match — it should call `pushDetection()` for hits it accepts
 * and simply return for hits it rejects (e.g. blocklist guard, length
 * check, Luhn check). Resetting `lastIndex` on each call keeps this
 * function safe to invoke repeatedly with the same compiled regex.
 */
function scan(
  re: RegExp,
  text: string,
  claimed: ClaimedSpan[],
  onMatch: (m: RegExpExecArray) => void,
): void {
  re.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (overlaps(m.index, m.index + m[0].length, claimed)) continue;
    if (m[0].length === 0) {
      // Pathological: a regex match of zero length would loop forever.
      // Defensive bump.
      re.lastIndex += 1;
      continue;
    }
    onMatch(m);
  }
}

/** Return all PII spans in `text`, in detection order. */
export function detectPII(text: string): Detection[] {
  if (!text) return [];

  const detections: Detection[] = [];
  const claimed: ClaimedSpan[] = [];

  // 1. SSN — must run first; it's a digit subset of phone/CC ranges
  scan(SSN_RE, text, claimed, (m) => {
    const digits = m[0].replace(/[-\s]/g, "");
    if (digits.length === 9) {
      pushDetection(detections, claimed, "ssn", m.index, m.index + m[0].length);
      return;
    }
    return;
  });

  // 2. Email
  scan(EMAIL_RE, text, claimed, (m) => {
    pushDetection(detections, claimed, "email", m.index, m.index + m[0].length);
    return;
  });

  // 3. Credit card — separated then unseparated, both Luhn-validated
  for (const re of [CC_SEPARATED_RE, CC_LONG_RE]) {
    scan(re, text, claimed, (m) => {
      const digits = m[0].replace(/\D/g, "");
      if (digits.length >= 13 && digits.length <= 19 && luhnCheck(digits)) {
        pushDetection(
          detections,
          claimed,
          "credit_card",
          m.index,
          m.index + m[0].length,
        );
        return;
      }
      return;
    });
  }

  // 4. Phone — 10 or 11 digits after stripping separators
  scan(PHONE_RE, text, claimed, (m) => {
    const digits = m[0].replace(/\D/g, "");
    if (digits.length === 10 || digits.length === 11) {
      pushDetection(detections, claimed, "phone", m.index, m.index + m[0].length);
      return;
    }
    return;
  });

  // 4b. URL
  scan(URL_RE, text, claimed, (m) => {
    pushDetection(detections, claimed, "url", m.index, m.index + m[0].length);
    return;
  });

  // 5. DOB — numeric form needs month/day plausibility
  scan(DOB_NUMERIC_RE, text, claimed, (m) => {
    const month = parseInt(m[1], 10);
    const day = parseInt(m[2], 10);
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      pushDetection(detections, claimed, "dob", m.index, m.index + m[0].length);
      return;
    }
    return;
  });
  scan(DOB_WRITTEN_RE, text, claimed, (m) => {
    pushDetection(detections, claimed, "dob", m.index, m.index + m[0].length);
    return;
  });

  // 6. Addresses — three patterns, each skipping prior claims
  for (const re of [ADDR_STANDARD_RE, ADDR_ORDINAL_RE, ADDR_BROADWAY_RE]) {
    scan(re, text, claimed, (m) => {
      pushDetection(
        detections,
        claimed,
        "address",
        m.index,
        m.index + m[0].length,
      );
      return;
    });
  }

  // 7. Names — high-confidence patterns only
  scan(NAME_INTRO_RE, text, claimed, (m) => {
    const firstWord = m[1].split(/\s+/)[0].toLowerCase();
    if (NAME_BLOCKLIST.has(firstWord)) return;
    // The capture group is what we redact, not the whole match.
    const capStart = m.index + m[0].lastIndexOf(m[1]);
    const capEnd = capStart + m[1].length;
    if (overlaps(capStart, capEnd, claimed)) return;
    pushDetection(detections, claimed, "name", capStart, capEnd);
    return;
  });

  scan(NAME_HI_RE, text, claimed, (m) => {
    const word = m[1];
    // Require ACTUAL uppercase first letter. The /i flag on the regex
    // is needed to match the greeting ("hi"/"Hi"/"HEY") but the
    // captured name word should be capitalized as a name. Without this
    // check, "hi this is Maria" would redact "this". Mirrors the
    // explicit case check the server applies in _NAME_IM_RE.
    if (word[0] !== word[0].toUpperCase() || word[0] === word[0].toLowerCase()) {
      return;
    }
    if (NAME_BLOCKLIST.has(word.toLowerCase())) return;
    const capStart = m.index + m[0].lastIndexOf(m[1]);
    const capEnd = capStart + m[1].length;
    if (overlaps(capStart, capEnd, claimed)) return;
    pushDetection(detections, claimed, "name", capStart, capEnd);
    return;
  });

  // 8. Gender identity — full-match redaction (the lead-in and the
  // identity term together; redacting just the term would leave a
  // dangling "I'm a" that's still informative).
  scan(GENDER_IDENTITY_RE, text, claimed, (m) => {
    pushDetection(detections, claimed, "gender", m.index, m.index + m[0].length);
    return;
  });

  // Sort by position so callers can slice the string in one pass
  detections.sort((a, b) => a.start - b.start);
  return detections;
}

/**
 * Redact PII from `text`, returning the redacted string and the list of
 * detections. The placeholders match the server's so a redacted-on-
 * client message is indistinguishable from a redacted-on-server one.
 */
export function redactPII(text: string): RedactionResult {
  const detections = detectPII(text);
  if (detections.length === 0) {
    return { redacted: text, detections: [] };
  }
  // Walk in reverse so earlier indices remain valid as we splice.
  let result = text;
  for (let i = detections.length - 1; i >= 0; i--) {
    const d = detections[i];
    const placeholder = PLACEHOLDERS[d.type];
    result = result.slice(0, d.start) + placeholder + result.slice(d.end);
  }
  return { redacted: result, detections };
}

/** Quick yes/no — useful for UI gating without paying for full redaction. */
export function hasPII(text: string): boolean {
  return detectPII(text).length > 0;
}
