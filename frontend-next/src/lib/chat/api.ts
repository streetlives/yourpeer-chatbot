// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import type {
  ChatResponse,
  FeedbackRating,
  AdminStats,
  ConversationSummary,
  AuditEvent,
  QueryLogEntry,
  EvalReport,
  EvalRunStatus,
} from "./types";

// ---------------------------------------------------------------------------
// Timeout helper (D4)
// ---------------------------------------------------------------------------

/** Return an AbortSignal that fires after `ms` milliseconds. */
function timeoutSignal(ms: number): AbortSignal {
  return AbortSignal.timeout(ms);
}

// Chat requests may include LLM + DB round-trips on the backend.
const CHAT_TIMEOUT_MS = 30_000;

// Admin/data endpoints are simple DB reads.
const ADMIN_TIMEOUT_MS = 15_000;

// ---------------------------------------------------------------------------
// Chat API
// ---------------------------------------------------------------------------

export async function sendChatMessage(
  message: string,
  sessionId: string | null,
  coords?: { latitude: number; longitude: number } | null,
): Promise<ChatResponse> {
  const requestId = crypto.randomUUID();
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
    },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      ...(coords && { latitude: coords.latitude, longitude: coords.longitude }),
    }),
    signal: timeoutSignal(CHAT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    if (res.status === 429 && data?.detail) {
      const msg = data.crisis_resources
        ? `${data.detail}\n\n${data.crisis_resources}`
        : data.detail;
      throw new Error(msg);
    }
    if (res.status === 503) {
      throw new Error(
        "The service is temporarily unavailable. Try again in a moment, or call 311 for help."
      );
    }
    if (res.status >= 500) {
      throw new Error(
        "Something went wrong on our end. Try again in a moment."
      );
    }
    throw new Error(`Request failed (${res.status}). Try again in a moment.`);
  }
  return res.json();
}

export async function sendFeedback(
  sessionId: string,
  rating: FeedbackRating,
  context?: Record<string, unknown>,
): Promise<void> {
  // Fire and forget — feedback loss is acceptable
  await fetch("/api/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, rating, ...(context && { context }) }),
    signal: timeoutSignal(ADMIN_TIMEOUT_MS),
  }).catch(() => {});
}

export interface LocationFeedbackData {
  session_id: string;
  location_id: string;
  location_name?: string;
  safety?: boolean;
  friendliness?: boolean;
  cleanliness?: boolean;
  queer_friendly?: boolean;
  comment?: string;
}

export async function sendLocationFeedback(
  data: LocationFeedbackData,
): Promise<void> {
  // Fire and forget — feedback loss is acceptable
  await fetch("/api/chat/location-feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal: timeoutSignal(ADMIN_TIMEOUT_MS),
  }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Admin API
// ---------------------------------------------------------------------------

const ADMIN_API = "/api/admin";

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await fetch(`${ADMIN_API}/stats`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load stats");
  return res.json();
}

export async function fetchConversations(
  limit = 100,
): Promise<ConversationSummary[]> {
  const res = await fetch(`${ADMIN_API}/conversations?limit=${limit}`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load conversations");
  return res.json();
}

export async function fetchConversationDetail(
  sessionId: string,
): Promise<AuditEvent[]> {
  const res = await fetch(`${ADMIN_API}/conversations/${sessionId}`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load conversation");
  return res.json();
}

export async function fetchEvents(
  limit = 100,
): Promise<AuditEvent[]> {
  const res = await fetch(`${ADMIN_API}/events?limit=${limit}`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load events");
  return res.json();
}

export async function fetchQueries(limit = 200): Promise<QueryLogEntry[]> {
  const res = await fetch(`${ADMIN_API}/queries?limit=${limit}`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load queries");
  return res.json();
}

export async function fetchEvalResults(): Promise<EvalReport | null> {
  const res = await fetch(`${ADMIN_API}/eval`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to load eval results");
  const data = await res.json();
  return data.results === null ? null : data;
}

export async function triggerEvalRun(
  scenarios?: number,
  category?: string,
): Promise<{ detail: string }> {
  const params = new URLSearchParams();
  if (scenarios) params.set("scenarios", String(scenarios));
  if (category) params.set("category", category);
  const res = await fetch(`${ADMIN_API}/eval/run?${params}`, {
    method: "POST",
    signal: timeoutSignal(ADMIN_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail || "Failed to start eval");
  }
  return res.json();
}

export async function fetchEvalStatus(): Promise<EvalRunStatus> {
  const res = await fetch(`${ADMIN_API}/eval/status`, { signal: timeoutSignal(ADMIN_TIMEOUT_MS) });
  if (!res.ok) throw new Error("Failed to check eval status");
  return res.json();
}
