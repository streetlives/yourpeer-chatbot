// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Stat-card explanations for the Overview tab dialogs.
 *
 * This file is intentionally separate from `metric-definitions.ts`
 * even though both surfaces show similar numbers, because the
 * audiences are different:
 *
 *   - `metric-definitions.ts` powers the Metrics tab. Audience:
 *     someone investigating "is this row red because of X or Y?".
 *     The fields there mirror METRICS.md — section number, formula
 *     in technical shorthand, pilot/post-pilot phase, status notes.
 *
 *   - This file powers the Overview tab. Audience: a staffer
 *     opening the console for the first time and asking "what does
 *     'Confirmation Confirm Rate' even mean?". The fields here are
 *     plain English: what the number measures, how it's computed in
 *     a single sentence (no SQL-flavored shorthand), what counts as
 *     good, and *why* the number is on the dashboard.
 *
 * Keep the technical definitions on the Metrics tab and the
 * first-read explanations here. The Overview dialog includes a
 * pointer to the Metrics tab section for users who want the full
 * technical version.
 */

export interface StatCardDefinition {
  /** Matches the StatCard `label` prop exactly so the page can do a
   *  direct lookup by clicked-card name. */
  label: string;
  /** One- or two-sentence plain-English answer to "what is this
   *  number?". First sentence should stand on its own. */
  definition: string;
  /** How the number is computed, in one sentence with no formula
   *  notation. The Metrics tab carries the formal formula. */
  howComputed: string;
  /** The target string as shown on the card ("≥ 80%", "≤ 5", etc.),
   *  with a short note on what crossing it means. Use `null` for
   *  cards that have no target (volume counts like Sessions). */
  target: string | null;
  /** 2–3 sentences on why this number is on the dashboard. This is
   *  the section that justifies the card's presence to a new
   *  reader. */
  whyItMatters: string;
  /** Optional cross-reference to the relevant Metrics tab section
   *  (e.g. "Section 1 — Is It Working?"). Rendered as a hint line
   *  at the bottom of the dialog for users who want the technical
   *  version. */
  metricsTabReference?: string;
  /**
   * Optional technical-detail callout for non-obvious definitions
   * the reader should know — like what the app actually classifies
   * as a "session", or how a "turn" is counted.
   *
   * Rendered in a neutral info box (not amber — this is context,
   * not a warning) between the "How it's computed" and "Target"
   * sections, so the structural definition lives near the formula
   * rather than appended at the end.
   *
   * Only use when the technical definition is doing real work for
   * the reader — don't pad every card with this just because the
   * field exists.
   */
  technicalNote?: {
    label: string;
    body: string;
  };
}

// Keys MUST match the `label` prop passed to <StatCard> in
// `src/app/admin/overview/page.tsx` — the lookup is exact-match.
// If a card's label changes there, update the key here too.
export const STAT_CARD_DEFINITIONS: Record<string, StatCardDefinition> = {
  Sessions: {
    label: "Sessions",
    definition:
      "How many distinct conversations the chatbot has handled since the audit log started recording. Each session covers one user's full back-and-forth, however many turns it took.",
    howComputed:
      "Count of unique session IDs in the audit log. One ID per user-conversation, regardless of how many messages were exchanged inside it.",
    target: null,
    technicalNote: {
      label: "What counts as a session",
      body:
        "A session is an anonymous, ephemeral token — 32 bytes of cryptographic randomness, HMAC-SHA256 signed in production so the server can verify the token came from us. The ID lives in browser localStorage and has a 30-minute idle TTL: close the tab and come back within the window and you keep the same session; 30 minutes of inactivity (or a logout, or a server restart without DB persistence enabled) starts a new one. No account, no login, no PII bound to the ID — the server only knows that requests carrying the same signed token belong to one conversation.",
    },
    whyItMatters:
      "This is a volume number, not a quality number, but every percentage on the rest of this page is computed against some slice of these sessions. A 100% task-completion rate based on 3 sessions tells a very different story than 100% based on 300. Read the other cards through this denominator.",
    metricsTabReference: "Section 1 — Is It Working?",
  },

  "Task Completion": {
    label: "Task Completion",
    definition:
      "Of the people who came to the chatbot looking for a service, what share of them actually got to a search result? Sessions that were only greetings, only help-requests, or only crises don't count — those users weren't trying to find a service.",
    howComputed:
      "Sessions that reached a confirmed database query, divided by sessions that expressed a service intent (a service turn, a confirmation turn, or a confirm-action turn).",
    target:
      "≥ 80%. Below 60% is off-target; 60–80% is a warning band. Falling here means people are giving up between expressing a need and getting results.",
    whyItMatters:
      "This is the single best 'is the bot doing its job?' number on the page. If users come in looking for a shelter and don't leave with a list of options, every other metric is window dressing. Drops in this number are usually traced to friction in the intake flow or the confirmation step.",
    metricsTabReference: "Section 1.1 — Task Completion Rate",
  },

  "Avg Turns to Result": {
    label: "Avg Turns to Result",
    definition:
      "On average, how many back-and-forth turns does it take a user to reach a search result? Counts only sessions that actually reached a result — incomplete sessions don't drag the number up or down.",
    howComputed:
      "Average of the user-turn count from session start to first executed database query, across all result-bearing sessions.",
    target:
      "≤ 5 turns. 5–7 is a warning band; above 7 means users are working too hard. Three to four is the sweet spot.",
    whyItMatters:
      "Every extra turn is another chance for a user to drop off. Long paths usually mean one of two things: the bot is asking for information it didn't need, or it kept misunderstanding and the user had to clarify themselves. Either way it's friction worth investigating in the Conversations transcript viewer.",
    metricsTabReference: "Section 1 — Is It Working? · Median Turns",
  },

  "Confirmation Confirm Rate": {
    label: "Confirmation Confirm Rate",
    definition:
      "When the bot summarizes what it heard ('You're looking for shelter in Brooklyn — should I search?'), what share of the time does the user say yes versus correcting it or denying?",
    howComputed:
      "Confirm-yes actions divided by total confirmation actions, where total = yes + change location + change service + deny.",
    target:
      "≥ 75%. The 50–75% band is a warning. Below 50% is a real signal that the slot extractor is consistently mishearing intent.",
    whyItMatters:
      "Even when Task Completion looks healthy, a low confirm rate tells you the bot is getting users wrong on the first try — they're correcting it before it can search. This is the upstream signal: it surfaces extractor problems before they show up as drop-offs. The count next to the percentage tells you how reliable the read is.",
    metricsTabReference: "Section 5 — Intake & Confirmation Flow",
  },

  "User Feedback": {
    label: "User Feedback",
    definition:
      "Of the users who tapped thumbs-up or thumbs-down on a bot response, what share tapped thumbs-up? Only users who actually left feedback count — passive readers don't.",
    howComputed:
      "Thumbs-up count divided by total thumbs (thumbs-up + thumbs-down). The sample size is shown next to the value.",
    target:
      "≥ 70% thumbs-up. Self-reported satisfaction is noisy with small samples — read the count alongside the percentage.",
    whyItMatters:
      "Direct read on whether the experience feels good to people, which is distinct from whether it works. A user can complete a successful search and still rate the experience as bad (slow, confusing, condescending), or vice versa. Pair this card with Task Completion to separate 'works but feels bad' from 'feels good but doesn't deliver'.",
    metricsTabReference: "Section 1 — Is It Working? · User Feedback Score",
  },

  "No-Result Rate": {
    label: "No-Result Rate",
    definition:
      "Of the searches the chatbot actually ran, what share came back with zero matching services? Counts only the final result after the relaxed-filter fallback has had its chance.",
    howComputed:
      "Queries that returned zero services (post-fallback) divided by total executed queries.",
    target:
      "≤ 15%. 15–25% is a warning band; above 25% is off-target. Pair with the Where (Top Locations) widget below to see if the misses cluster in specific boroughs.",
    whyItMatters:
      "Catches problems on both sides of the search. A high no-result rate either means the location catalog doesn't have enough partners for what users are asking about, or the bot is translating user intent into the wrong database question. The Locations tab → Service-type coverage view shows which side of that split a given category is on.",
    metricsTabReference: "Section 1 — Is It Working? · No-Result Rate",
  },
};

/**
 * Lookup helper for the click handler in the Overview page. Returns
 * `null` for unknown labels rather than throwing — a card with no
 * registered definition should silently behave as non-interactive
 * rather than crash the page.
 */
export function findStatCardDefinition(
  label: string,
): StatCardDefinition | null {
  return STAT_CARD_DEFINITIONS[label] ?? null;
}
