// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

export interface QuickReply {
  label: string;
  value: string;
  /** When present, the button renders as a link (e.g. "tel:" for phone calls). */
  href?: string;
}

export interface ServiceResult {
  service_id?: string;
  service_name?: string;
  organization?: string;
  address?: string;
  city?: string;
  phone?: string;
  email?: string;
  website?: string;
  description?: string;
  hours_today?: string;
  is_open?: "open" | "closed" | "unknown";
  fees?: string;
  requires_membership?: boolean;
  yourpeer_url?: string;
  last_validated_at?: string;
  also_available?: string[];
  accessibility?: string;
  eligibility_summary?: string;
  review_highlight?: string;
  required_documents?: string[];
  languages?: string[];
  /** Raw taxonomy tags for this service — used by post-results
   *  sub-category filtering. Example: ["Shelter", "Families", "Intake"] */
  service_taxonomies?: string[];
  /** True when this card was surfaced by the population-critical
   *  fallback (borough-wide, rare-taxonomy secondary query) rather
   *  than the main proximity-bounded query. Use to visually distinguish
   *  "also found, further away" cards from the primary results.
   *  See backend chatbot._run_population_fallback. */
  is_population_fallback?: boolean;
  /** Which rare population triggered the fallback for this card:
   *  "lgbtq", "youth", "senior", or "veteran". Paired with
   *  is_population_fallback. */
  fallback_population?: "lgbtq" | "youth" | "senior" | "veteran";
  /** Latitude from PostGIS l.position. May be null for pilot imports
   *  or manual entries without coordinates. See BOUNDARY_AUDIT.md. */
  latitude?: number | null;
  /** Longitude from PostGIS l.position. See latitude. */
  longitude?: number | null;
  /** The borough our NYC DCP polygon lookup says this service is
   *  physically in, based on (latitude, longitude). Null when the
   *  service is outside NYC or has no coordinates. */
  geographic_borough?:
    | "Manhattan"
    | "Brooklyn"
    | "Queens"
    | "Bronx"
    | "Staten Island"
    | null;
  /** True when geographic_borough (from coords) disagrees with the
   *  borough inferred from the city field (e.g., city='New York' but
   *  geographic_borough='Bronx'). Always false when either side is
   *  unknown — this flag only fires on confirmed disagreement.
   *  Cards are NOT filtered on this; surface in UI at your discretion.
   *  See BOUNDARY_AUDIT.md §Status. */
  borough_mismatch?: boolean;
}

export interface ChatResponse {
  session_id: string;
  response: string;
  services?: ServiceResult[];
  quick_replies?: QuickReply[];
  /** True when the bot needs more info (location, family status) before searching. */
  follow_up_needed?: boolean;
  /** Total results found before pagination (e.g., 25 found, 5 shown). */
  result_count?: number;
}

/**
 * Delivery status of a user message. Drives per-message visual state
 * (the "ticks" next to the bubble):
 *
 *  - sending: request in flight. Single faint tick or spinner.
 *  - sent: the server acknowledged. Two ticks.
 *  - pending: queued while offline. Clock icon; user can cancel.
 *  - failed: delivery gave up (e.g. queue expired). Warning icon.
 *  - cancelled: user cancelled before delivery. Message styled
 *      faded / struck-through.
 *
 * Bot messages have no status (they only appear after they exist).
 */
export type MessageStatus =
  | "sending"
  | "sent"
  | "pending"
  | "failed"
  | "cancelled";

export interface ChatMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  services?: ServiceResult[];
  quick_replies?: QuickReply[];
  showFeedback?: boolean;
  /** If set, this error message includes a Retry button that re-sends this text. */
  retryMessage?: string;
  /** Transient messages (e.g. "Getting your location…") are not persisted
   *  to localStorage. They're stripped during rehydration so they don't
   *  survive page refreshes. */
  transient?: boolean;
  /** Per-message delivery status. Only meaningful for user-role messages. */
  status?: MessageStatus;
  /**
   * Stable idempotency key for the underlying backend request. Attached
   * to user messages so the same key can be reused on retry/flush, and
   * the backend can dedupe. Omitted on bot messages.
   */
  requestId?: string;
  /**
   * Origin of this user message:
   *   • "typed"        — keyboard input via the chat input field
   *   • "quick_reply"  — tap on a bot-emitted button pill
   * Forwarded to the backend on every send for analytics and to drive
   * the confirmation-skip optimization for pure-tap sessions (see
   * `_pure_tap_session` in backend orchestrator). Optional on the type
   * because legacy persisted messages from before this field was added
   * may not have it; missing is treated identically to "typed" on the
   * backend. Only meaningful on user-role messages. */
  source?: "typed" | "quick_reply";
}

export type FeedbackRating = "up" | "down";

// Admin types

export interface ConfirmationBreakdown {
  confirm: number;
  change_service: number;
  change_location: number;
  deny: number;
  total_actions: number;
  confirm_rate: number | null;
  sessions_at_confirmation: number;
  sessions_abandoned: number;
  abandon_rate: number | null;
}

export interface DataFreshnessDetail {
  cards_served: number;
  cards_with_date?: number;
  cards_fresh: number;
}

export interface ConversationQuality {
  emotional_sessions: number;
  emotional_rate: number | null;
  emotional_to_escalation: number | null;
  emotional_to_service: number | null;
  bot_question_turns: number;
  bot_question_rate: number | null;
  bot_question_sessions: number;
  bot_question_to_frustration: number | null;
  conversational_discovery: number;
  conversational_discovery_rate: number | null;
}

export interface RoutingDistribution {
  category_distribution: Record<string, number>;
  total_categorized: number;
  general_rate: number | null;
  buckets: {
    service_flow: number;
    conversational: number;
    emotional: number;
    safety: number;
    recovery: number;
    general: number;
  };
}

export interface ToneDistribution {
  tones: Record<string, number>;
  total_with_tone: number;
  turns_without_tone: number;
}

export interface MultiIntentMetrics {
  queue_offers: number;
  queue_declines: number;
  multi_service_sessions: number;
}

export interface AdminStats {
  unique_sessions: number;
  total_turns: number;
  total_queries: number;
  total_crises: number;
  /**
   * Total crisis events in the last 24 hours, rolling. Sums to the
   * values in `crises_by_category_24h`. Rolling-window (rather than
   * calendar-today) to keep the most-recent activity visible
   * regardless of when an admin checks the dashboard — a calendar
   * approach would show an empty panel at 12:01 AM even with crises
   * 30 minutes earlier.
   */
  total_crises_24h: number;
  /**
   * All-time sparse breakdown of crisis events by category (8 canonical
   * categories plus an "uncategorized" fallback). Sums to `total_crises`.
   * Categories not seen across the entire dataset are absent — matches
   * the convention of `category_distribution` and
   * `service_type_distribution`.
   *
   * Backend source: `crisis_detector._CRISIS_CATEGORIES` defines the 8
   * canonical categories (suicide_self_harm, medical_emergency,
   * domestic_violence, youth_runaway, assault_victim, safety_concern,
   * trafficking, violence). Aggregated in `audit_log.get_stats()` from
   * each `crisis_detected` event's `crisis_category` field.
   */
  crises_by_category: Record<string, number>;
  /**
   * Same shape as `crises_by_category` but scoped to crises that
   * occurred in the last 24 hours, rolling. Sums to `total_crises_24h`.
   * Empty `{}` when there have been no crises in the last 24 hours
   * (sparse convention).
   */
  crises_by_category_24h: Record<string, number>;
  total_escalations: number;
  total_resets: number;
  service_intent_sessions: number;
  relaxed_query_rate: number;
  feedback_up: number;
  feedback_down: number;
  feedback_score: number | null;
  slot_confirmation_rate: number | null;
  slot_correction_rate: number | null;
  data_freshness_rate: number | null;
  data_freshness_detail: DataFreshnessDetail;
  conversation_quality: ConversationQuality;
  confirmation_breakdown: ConfirmationBreakdown;
  category_distribution: Record<string, number>;
  service_type_distribution: Record<string, number>;
  routing?: RoutingDistribution;
  tone_distribution?: ToneDistribution;
  multi_intent?: MultiIntentMetrics;
  // P0-P3 metrics (Run 23+)
  confidence?: ConfidenceMetrics;
  recovery_rates?: RecoveryMetrics;
  session_metrics?: SessionMetrics;
  no_result_by_service?: Record<string, { total_queries: number; no_result_rate: number }>;
  time_of_day?: TimeOfDayMetrics;
  post_results_engagement?: PostResultsEngagement;
  geographic_demand?: Record<string, { total_queries: number; share: number; no_result_rate: number }>;
  frustration_tiers?: FrustrationTiers;
  session_duration?: SessionDuration;
  repetition_rate?: RepetitionRate;
  llm_metrics?: LlmMetrics;
  /**
   * Tap-vs-type breakdown and confirmation-skip-optimization
   * effectiveness. Three sections:
   *   - turns:             per-turn origin distribution
   *   - sessions:          per-session pure-tap classification
   *   - confirmation_skip: how many confirmation prompts the
   *                        pure-tap optimization skipped
   * Optional because backend builds prior to May 2026 don't compute it
   * (so the deserialized stats payload from an older server omits the
   * key entirely; we render fallback "no-data" rows in that case).
   * Backend source: `audit_log._compute_navigation`. See also
   * `_pure_tap_session` in backend orchestrator for the runtime flag
   * that drives the skip optimization this measures.
   */
  navigation?: NavigationMetrics;
  // Overview headline metrics (pre-computed)
  task_completion_rate: number | null;
  avg_turns_to_result: number | null;
  no_result_rate: number | null;
}

// --- P0-P3 metric types ---

export interface ConfidenceMetrics {
  total_with_confidence: number;
  distribution: Record<string, number>;
  high_rate: number | null;
  low_rate: number | null;
}

export interface RecoveryMetrics {
  correction_turns: number;
  correction_session_rate: number | null;
  disambiguation_turns: number;
  disambiguation_session_rate: number | null;
  negative_preference_turns: number;
  negative_preference_session_rate: number | null;
}

export interface SessionMetrics {
  total_sessions: number;
  bounce_count: number;
  bounce_rate: number | null;
  avg_turns_per_session: number | null;
  median_turns_per_session: number | null;
  distribution?: Record<string, number>;
}

export interface TimeOfDayMetrics {
  total_events: number;
  peak_hour_utc: number | null;
  hourly?: Record<string, number>;
  daily?: Record<string, number>;
}

export interface PostResultsEngagement {
  sessions_with_results: number;
  sessions_engaged: number;
  engagement_rate: number | null;
}

export interface FrustrationTiers {
  total_frustrated_sessions: number;
  tiers: { tier_1: number; tier_2: number; tier_3_plus: number };
  tier_1_rate: number | null;
  tier_3_plus_rate: number | null;
}

export interface SessionDuration {
  total_multi_turn_sessions: number;
  avg_duration_sec: number | null;
  median_duration_sec: number | null;
  p95_duration_sec: number | null;
  buckets?: Record<string, number>;
}

export interface RepetitionRate {
  sessions_with_repetition: number;
  repetition_rate: number | null;
}

export interface LlmMetrics {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  estimated_cost: number | null;
  avg_calls_per_session: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  failure_rate: number | null;
  by_task?: Record<string, { calls: number; avg_latency_ms: number }>;
  by_model?: Record<string, number>;
}

/**
 * Tap-vs-type navigation pattern + confirmation-skip-optimization
 * metrics. See `audit_log._compute_navigation` for the computation.
 *
 * The `unknown` / `incomplete_signal` buckets catch turns and sessions
 * from clients (or pre-feature audit data) that don't carry the
 * `source` field. They shrink to zero once the source field is fully
 * rolled out and old data ages out of the audit window. The
 * `*_rate` denominators intentionally exclude them so the surfaced
 * percentages reflect the measurable population only.
 */
export interface NavigationMetrics {
  turns: {
    /** Turns submitted via keyboard. */
    typed: number;
    /** Turns submitted via quick-reply button tap. */
    quick_reply: number;
    /** Turns with no source field (legacy data or pre-feature clients). */
    unknown: number;
    /** typed + quick_reply (denominator for quick_reply_rate). */
    classified: number;
    /** quick_reply / classified. Null when classified is zero. */
    quick_reply_rate: number | null;
  };
  sessions: {
    /** Sessions where every turn was quick_reply. */
    pure_tap: number;
    /** Sessions where every turn was typed. */
    pure_typed: number;
    /** Sessions with both typed and quick_reply turns. */
    mixed: number;
    /** Sessions with at least one turn that had no source field. */
    incomplete_signal: number;
    /** pure_tap + pure_typed + mixed (denominator for pure_tap_rate). */
    classified: number;
    /** pure_tap / classified. The headline behavioral number. */
    pure_tap_rate: number | null;
  };
  confirmation_skip: {
    /** Turns logged under category "confirmation_skipped_pure_tap". */
    skipped: number;
    /** Turns logged under "confirmation" or "confirmation_nudge". */
    shown: number;
    /** skipped / (skipped + shown). The friction-reduction headline. */
    skip_rate: number | null;
  };
}

export interface ConversationSummary {
  session_id: string;
  turn_count: number;
  services_delivered: number;
  queries_executed: number;
  crisis_detected: boolean;
  final_slots: Record<string, string>;
  last_seen: string;
  /**
   * Session-level navigation classification, mirroring the per-session
   * buckets in `NavigationMetrics.sessions`:
   *   • true  — every turn arrived as a quick-reply tap (pure-tap session)
   *   • false — at least one turn was typed
   *   • null  — at least one turn had no source field (incomplete signal,
   *             typically pre-feature data)
   * Optional because pre-May-2026 backends don't compute it.
   */
  pure_tap_session?: boolean | null;
}

export interface AuditEvent {
  type: "conversation_turn" | "query_execution" | "crisis_detected" | "session_reset" | "feedback" | "location_feedback";
  timestamp: string;
  session_id?: string;
  user_message?: string;
  bot_response?: string;
  template_name?: string;
  result_count?: number;
  execution_ms?: number;
  relaxed?: boolean;
  crisis_category?: string;
  slots?: Record<string, string | null>;
  services_count?: number;
  quick_replies?: string[];
  /**
   * Origin of a user turn ("typed" or "quick_reply"). Set on
   * `type: "conversation_turn"` events emitted on or after May 2026
   * (audit_log._compute_navigation). Absent for pre-feature data and
   * for non-turn event types. Renderers should treat absent as
   * "unknown" rather than collapsing to a default — see
   * transcript-drawer's per-User-bubble badge.
   */
  source?: "typed" | "quick_reply";
  rating?: string;
  comment?: string;
  context?: {
    result_count?: number;
    service_names?: string[];
    organizations?: string[];
    bot_response?: string;
  };

  // ---------------------------------------------------------------
  // location_feedback-specific fields
  //
  // The backend's log_location_feedback persists these alongside the
  // generic comment/rating/context, but they used to be untyped here
  // and silently dropped by frontend renderers. Adding them lets the
  // event-feed, transcript-drawer, and the new Locations admin page
  // render the full per-criterion data.
  //
  // Optional because they only appear on `type: "location_feedback"`
  // events. Renderers should narrow on `ev.type === "location_feedback"`
  // before reading.
  // ---------------------------------------------------------------
  /** UUID of the physical service location the user rated. */
  location_id?: string;
  /** Human-readable location name at the time of feedback (snapshot). */
  location_name?: string | null;
  /** Per-criterion booleans. true = positive, false = negative; absent = not asked.
   *  Renderers count `false` values to display "flag" indicators. */
  ratings?: {
    safety?: boolean;
    friendliness?: boolean;
    cleanliness?: boolean;
    queer_friendly?: boolean;
  };
}

export interface QueryLogEntry {
  timestamp: string;
  session_id?: string;
  template_name: string;
  params: Record<string, unknown>;
  result_count: number;
  execution_ms: number;
  relaxed: boolean;
  /** True when the proximity (geolocation) query exceeded statement_timeout
   *  and the system fell back to a borough-level search. */
  proximity_timeout?: boolean;
}

export interface EvalDimensionScore {
  average: number;
  count: number;
}

export interface EvalScenarioResult {
  name: string;
  category?: string;
  average_score: number;
  overall_notes?: string;
  error?: string;
  scores?: Record<string, { score: number; justification: string }>;
}

export interface EvalReport {
  summary: {
    overall_average: number;
    scenarios_evaluated: number;
    critical_failure_count: number;
    scenarios_with_errors: number;
    dimension_averages: Record<string, EvalDimensionScore>;
    category_averages: Record<string, number>;
  };
  critical_failures?: Array<{ scenario: string; failure: string }>;
  scenarios?: EvalScenarioResult[];
}

/**
 * Response from GET /admin/api/eval. The endpoint either returns a full
 * EvalReport (with summary + scenarios + critical_failures), or — when no
 * report has been generated yet — `{ results: null }`.
 *
 * The two shapes are distinguished by the presence of `summary`. When the
 * backend is updated to return the report directly without a wrapper, this
 * type can be replaced with `EvalReport | { results: null }` and the
 * fetcher narrowed to use a discriminator. For now, this type encodes the
 * actual wire format so future drift is caught at the type level.
 */
export type EvalResultsResponse = EvalReport | { results: null };

export interface EvalRunStatus {
  running: boolean;
  message?: string;
  total?: number;
  completed?: number;
  started_at?: string;
  finished_at?: string;
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

export interface HealthComponentStatus {
  status: "up" | "down" | "degraded" | "unavailable" | "not_loaded";
  latency_ms?: number;
  error?: string;
  detail?: string;
  error_type?: string;
  cached?: boolean;
  mode?: string;
  model?: string;
  route_count?: number;
  service_routes?: number;
  population_routes?: number;
  total_utterances?: number;
  embedding_dim?: number;
  functional?: boolean;
  required?: boolean;
}

export interface HealthCheckResponse {
  status: "healthy" | "degraded" | "unhealthy";
  timestamp: string;
  uptime_seconds: number;
  checks: {
    database: HealthComponentStatus;
    llm: HealthComponentStatus;
    semantic_router: HealthComponentStatus;
  };
}
