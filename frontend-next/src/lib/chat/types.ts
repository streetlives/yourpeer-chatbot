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
}

export interface ChatResponse {
  session_id: string;
  response: string;
  services?: ServiceResult[];
  quick_replies?: QuickReply[];
}

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

export interface ConversationSummary {
  session_id: string;
  turn_count: number;
  services_delivered: number;
  crisis_detected: boolean;
  final_slots: Record<string, string>;
  last_seen: string;
}

export interface AuditEvent {
  type: "conversation_turn" | "query_execution" | "crisis_detected" | "session_reset" | "feedback";
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
  rating?: string;
  comment?: string;
}

export interface QueryLogEntry {
  timestamp: string;
  session_id?: string;
  template_name: string;
  params: Record<string, unknown>;
  result_count: number;
  execution_ms: number;
  relaxed: boolean;
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

export interface EvalRunStatus {
  running: boolean;
  message?: string;
  total?: number;
  completed?: number;
  started_at?: string;
  finished_at?: string;
}
