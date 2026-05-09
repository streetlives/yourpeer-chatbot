"""
Audit Log — event store for the staff review console.

Stores conversation turns, query executions, crisis events, session resets,
and user feedback in ring buffers. Provides aggregation functions for the
admin dashboard (stats, conversation summaries, query log).

Primary storage is in-memory for fast reads. When PILOT_DB_PATH is set,
events are also written to SQLite for persistence across server restarts.
On startup, call hydrate_from_db() to reload persisted data.

Thread-safe: all mutations and reads acquire _lock. FastAPI serves
concurrent requests, so unguarded dict/deque mutations would corrupt state.
"""

import json
import logging
import threading
from collections import deque, OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services import persistence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

MAX_EVENTS = 2000
MAX_CONVERSATIONS = 500

# ---------------------------------------------------------------------------
# STORAGE
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_events: deque = deque(maxlen=MAX_EVENTS)
_conversations: OrderedDict = OrderedDict()
_query_log: deque = deque(maxlen=MAX_EVENTS)
_eval_results: Optional[dict] = None


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_slots(slots: Optional[dict]) -> dict:
    if not slots:
        return {}
    return {
        k: v for k, v in slots.items()
        if v is not None and not k.startswith("_") and k != "transcript"
    }


def _extract_qr_labels(quick_replies: Optional[list]) -> list:
    if not quick_replies:
        return []
    return [qr["label"] if isinstance(qr, dict) else qr for qr in quick_replies]


def _register_conversation(session_id: str, event: dict) -> None:
    """MUST be called with _lock held."""
    if session_id not in _conversations:
        if len(_conversations) >= MAX_CONVERSATIONS:
            _conversations.popitem(last=False)
        _conversations[session_id] = []
    else:
        _conversations.move_to_end(session_id)
    _conversations[session_id].append(event)


# ---------------------------------------------------------------------------
# LOGGING FUNCTIONS
# ---------------------------------------------------------------------------

def log_conversation_turn(
    session_id="", user_message_redacted="", bot_response="",
    slots=None, category="", services_count=0, quick_replies=None,
    follow_up_needed=False, request_id=None, tone=None, **kwargs,
):
    event = {
        "type": "conversation_turn",
        "timestamp": _now_iso(),
        "session_id": session_id,
        "user_message": user_message_redacted,
        "bot_response": bot_response,
        "slots": _clean_slots(slots),
        "category": category,
        "services_count": services_count,
        "quick_replies": _extract_qr_labels(quick_replies),
        "follow_up_needed": follow_up_needed,
        "request_id": request_id,
        "tone": tone,
    }
    # Store additional fields (confidence, etc.) from kwargs
    for k, v in kwargs.items():
        if v is not None and k not in event:
            event[k] = v
    with _lock:
        _events.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


def log_query_execution(
    session_id="", template_name="", params=None, result_count=0,
    relaxed=False, execution_ms=0, freshness=None, request_id=None,
    proximity_timeout=False, **kwargs,
):
    clean_params = dict(params or {})
    clean_params.pop("max_results", None)
    event = {
        "type": "query_execution",
        "timestamp": _now_iso(),
        "session_id": session_id,
        "template_name": template_name,
        "params": clean_params,
        "result_count": result_count,
        "relaxed": relaxed,
        "proximity_timeout": proximity_timeout,
        "execution_ms": execution_ms,
        "freshness": freshness,
        "request_id": request_id,
    }
    with _lock:
        _events.append(event)
        _query_log.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


def log_crisis_detected(
    session_id="", crisis_category="", user_message_redacted="",
    request_id=None, **kwargs,
):
    event = {
        "type": "crisis_detected",
        "timestamp": _now_iso(),
        "session_id": session_id,
        "crisis_category": crisis_category,
        "user_message": user_message_redacted,
        "request_id": request_id,
    }
    with _lock:
        _events.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


def log_session_reset(session_id="", **kwargs):
    event = {
        "type": "session_reset",
        "timestamp": _now_iso(),
        "session_id": session_id,
    }
    with _lock:
        _events.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


def log_feedback(session_id="", rating="", comment=None, context=None, **kwargs):
    event = {
        "type": "feedback",
        "timestamp": _now_iso(),
        "session_id": session_id,
        "rating": rating,
        "comment": comment,
    }
    if context:
        event["context"] = context
    with _lock:
        _events.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


def log_location_feedback(
    session_id="", location_id="", location_name=None,
    safety=None, friendliness=None, cleanliness=None,
    queer_friendly=None, comment=None, **kwargs,
):
    event = {
        "type": "location_feedback",
        "timestamp": _now_iso(),
        "session_id": session_id,
        "location_id": location_id,
        "location_name": location_name,
        "ratings": {
            k: v for k, v in {
                "safety": safety,
                "friendliness": friendliness,
                "cleanliness": cleanliness,
                "queer_friendly": queer_friendly,
            }.items() if v is not None
        },
        "comment": comment,
    }
    with _lock:
        _events.append(event)
        _register_conversation(session_id, event)
    persistence.persist_event(event)


# ---------------------------------------------------------------------------
# RETRIEVAL
# ---------------------------------------------------------------------------

def get_recent_events(limit=100, event_type=None, n=None, **kwargs):
    if n is not None:
        limit = n
    with _lock:
        if event_type:
            filtered = [e for e in _events if e.get("type") == event_type]
            return list(filtered[-limit:])
        return list(list(_events)[-limit:])


def get_conversation(session_id: str) -> list:
    with _lock:
        return list(_conversations.get(session_id, []))


def get_conversations_summary(limit=50) -> list:
    with _lock:
        summaries = []
        for session_id in reversed(_conversations):
            events = _conversations[session_id]
            if not events:
                continue
            turns = [e for e in events if e.get("type") == "conversation_turn"]
            crisis = any(e.get("type") == "crisis_detected" for e in events)
            categories = list({t.get("category", "") for t in turns if t.get("category")})
            last_turn = turns[-1] if turns else {}
            queries_in_session = [e for e in events if e.get("type") == "query_execution"]
            max_services = max((t.get("services_count", 0) for t in turns), default=0)
            summaries.append({
                "session_id": session_id,
                "turn_count": len(turns),
                "services_delivered": max_services,
                "queries_executed": len(queries_in_session),
                "crisis_detected": crisis,
                "categories": categories,
                "final_slots": last_turn.get("slots", {}),
                "last_seen": events[-1].get("timestamp", ""),
            })
            if len(summaries) >= limit:
                break
        return summaries


def get_query_log(limit=100) -> list:
    with _lock:
        return list(list(_query_log)[-limit:])


def get_stats() -> dict:
    with _lock:
        all_events = list(_events)

    turns = [e for e in all_events if e.get("type") == "conversation_turn"]
    queries = [e for e in all_events if e.get("type") == "query_execution"]
    crises = [e for e in all_events if e.get("type") == "crisis_detected"]
    resets = [e for e in all_events if e.get("type") == "session_reset"]
    feedbacks = [e for e in all_events if e.get("type") == "feedback"]
    location_feedbacks = [e for e in all_events if e.get("type") == "location_feedback"]

    all_sessions = {e.get("session_id") for e in all_events if e.get("session_id")}

    # Per-session categories
    sess_cats: dict[str, set] = {}
    for t in turns:
        sid = t.get("session_id", "")
        sess_cats.setdefault(sid, set()).add(t.get("category", ""))

    # Crisis category breakdown — aggregated from the crisis_category
    # field on each crisis_detected event. The crisis_detector emits
    # one of 8 canonical categories (suicide_self_harm, medical_emergency,
    # domestic_violence, youth_runaway, assault_victim, safety_concern,
    # trafficking, violence) plus a fallback "safety_concern" when the
    # LLM stage misses or the category field is missing.
    #
    # Two breakdowns are surfaced:
    #
    # crises_by_category — ALL-TIME breakdown across every crisis event
    #   ever logged. Used for the historical/cumulative view on the
    #   admin Overview page. Sums to total_crises.
    #
    # crises_by_category_24h — rolling 24-hour window, anchored to now.
    #   Used for the recent-activity view on the Overview page. Rolling
    #   (rather than calendar-today) matters at the day boundary: an
    #   admin checking the dashboard at 12:01 AM would see an empty
    #   "today" panel under a calendar approach even if there were 5
    #   crises 30 minutes earlier. Rolling 24h keeps the most-recent
    #   activity visible regardless of when the admin checks. Sums to
    #   total_crises_24h.
    #
    # Both are sparse: only categories with N > 0 appear, matching the
    # convention of category_distribution / service_type_distribution.
    cutoff_24h_utc = datetime.now(timezone.utc) - timedelta(hours=24)
    crises_by_category: dict[str, int] = {}
    crises_by_category_24h: dict[str, int] = {}
    for c in crises:
        cat = c.get("crisis_category") or "uncategorized"
        crises_by_category[cat] = crises_by_category.get(cat, 0) + 1

        # 24h-bucket: only count if the event timestamp parses and
        # lands at or after the rolling cutoff. Events with missing or
        # malformed timestamps stay in the all-time bucket but are
        # excluded from the 24h count — silently dropping them from
        # "recent" is the right default (we can't prove they're recent)
        # while keeping them in all-time preserves the total_crises
        # invariant.
        ts = c.get("timestamp")
        if ts:
            try:
                event_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if event_dt >= cutoff_24h_utc:
                    crises_by_category_24h[cat] = (
                        crises_by_category_24h.get(cat, 0) + 1
                    )
            except (ValueError, AttributeError):
                continue

    total_crises_24h = sum(crises_by_category_24h.values())

    # Distributions
    cat_dist: dict[str, int] = {}
    svc_dist: dict[str, int] = {}
    for t in turns:
        cat = t.get("category", "general")
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        stype = t.get("slots", {}).get("service_type")
        if stype:
            svc_dist[stype] = svc_dist.get(stype, 0) + 1

    # Relaxed rate
    relaxed_rate = sum(1 for q in queries if q.get("relaxed")) / len(queries) if queries else 0

    # Escalations
    esc_sessions = {sid for sid, cats in sess_cats.items() if "escalation" in cats}

    # Service intent
    _svc = {"service", "confirmation", "confirm_yes", "confirm_change_service", "confirm_change_location"}
    svc_intent = {sid for sid, cats in sess_cats.items() if cats & _svc}

    # Slot correction rate
    at_conf = {sid for sid, cats in sess_cats.items() if "confirmation" in cats or "confirm_yes" in cats}
    with_corr = {sid for sid, cats in sess_cats.items() if "confirm_change_service" in cats or "confirm_change_location" in cats}
    slot_corr_rate = round(len(with_corr) / len(at_conf), 2) if at_conf else None

    # Slot confirmation rate
    q_sessions = {q.get("session_id") for q in queries if q.get("session_id")}
    confirmed_q = {sid for sid in q_sessions if sid in sess_cats and "confirm_yes" in sess_cats[sid]}
    slot_conf_rate = round(len(confirmed_q) / len(q_sessions), 2) if q_sessions else None

    # Confirmation breakdown
    ca = [t for t in turns if t.get("category", "").startswith("confirm_")]
    cb_yes = sum(1 for t in ca if t["category"] == "confirm_yes")
    cb_cl = sum(1 for t in ca if t["category"] == "confirm_change_location")
    cb_cs = sum(1 for t in ca if t["category"] == "confirm_change_service")
    cb_deny = sum(1 for t in ca if t["category"] == "confirm_deny")
    cb_total = len(ca)
    sess_at_c = {sid for sid, cats in sess_cats.items()
                 if "confirmation" in cats or any(c.startswith("confirm_") for c in cats)}
    sess_confirmed = {sid for sid, cats in sess_cats.items() if "confirm_yes" in cats}
    sess_abandoned = sess_at_c - sess_confirmed

    # Freshness
    cards_served = 0
    cards_fresh = 0
    for q in queries:
        f = q.get("freshness")
        if f and isinstance(f, dict) and f.get("total", 0) > 0:
            cards_served += f["total"]
            cards_fresh += f.get("fresh", 0)

    # Feedback
    fb_up = sum(1 for f in feedbacks if f.get("rating") == "up")
    fb_down = sum(1 for f in feedbacks if f.get("rating") == "down")
    fb_total = fb_up + fb_down

    cq = _conversation_quality(turns, queries, sess_cats)
    routing = _compute_routing(turns, cat_dist)
    tone_dist = _compute_tone_distribution(turns)
    multi = _compute_multi_intent(turns, queries, sess_cats)
    confidence = _compute_confidence(turns)
    recovery = _compute_recovery_rates(turns, sess_cats)
    session_metrics = _compute_session_metrics(turns)
    no_result_svc = _compute_no_result_by_service(queries)
    time_of_day = _compute_time_of_day(all_events)
    post_results_eng = _compute_post_results_engagement(turns, queries)

    # --- Overview headline metrics (pre-computed for overview page) ---
    _task_completion_rate = (
        round(len(q_sessions) / len(svc_intent), 2)
        if svc_intent and q_sessions else None
    )
    # Avg turns to result: mean turn count for sessions that completed a search
    _completed_turn_counts = []
    _turn_counts_by_session: dict[str, int] = {}
    for t in turns:
        sid = t.get("session_id", "")
        if sid:
            _turn_counts_by_session[sid] = _turn_counts_by_session.get(sid, 0) + 1
    for sid in q_sessions:
        if sid in _turn_counts_by_session:
            _completed_turn_counts.append(_turn_counts_by_session[sid])
    _avg_turns_to_result = (
        round(sum(_completed_turn_counts) / len(_completed_turn_counts), 1)
        if _completed_turn_counts else None
    )
    _zero_result_queries = sum(1 for q in queries if q.get("result_count", 0) == 0)
    _no_result_rate = (
        round(_zero_result_queries / len(queries), 2)
        if queries else None
    )

    return {
        "total_events": len(all_events),
        "total_turns": len(turns),
        "total_queries": len(queries),
        "total_crises": len(crises),
        # Crises that occurred in the last 24 hours, rolling. See the
        # aggregation block above for the rolling-vs-calendar rationale.
        "total_crises_24h": total_crises_24h,
        # All-time crisis breakdown by category. Sums to total_crises.
        "crises_by_category": crises_by_category,
        # Rolling-24h crisis breakdown by category. Sums to
        # total_crises_24h. Empty dict {} when no crises in the last
        # 24 hours (sparse convention).
        "crises_by_category_24h": crises_by_category_24h,
        "total_resets": len(resets),
        "unique_sessions": len(all_sessions),
        "total_escalations": len(esc_sessions),
        "service_intent_sessions": len(svc_intent),
        "category_distribution": cat_dist,
        "service_type_distribution": svc_dist,
        "relaxed_query_rate": relaxed_rate,
        "slot_correction_rate": slot_corr_rate,
        "slot_confirmation_rate": slot_conf_rate,
        "confirmation_breakdown": {
            "confirm": cb_yes, "change_location": cb_cl, "change_service": cb_cs,
            "deny": cb_deny, "total_actions": cb_total,
            "confirm_rate": round(cb_yes / cb_total, 2) if cb_total else None,
            "sessions_at_confirmation": len(sess_at_c),
            "sessions_abandoned": len(sess_abandoned),
            "abandon_rate": round(len(sess_abandoned) / len(sess_at_c), 2) if sess_at_c else None,
        },
        "data_freshness_rate": round(cards_fresh / cards_served, 2) if cards_served else None,
        "data_freshness_detail": {"cards_served": cards_served, "cards_fresh": cards_fresh},
        "feedback_up": fb_up,
        "feedback_down": fb_down,
        "feedback_score": round(fb_up / fb_total, 2) if fb_total else None,
        "location_feedback_count": len(location_feedbacks),
        "conversation_quality": cq,
        "routing": routing,
        "tone_distribution": tone_dist,
        "multi_intent": multi,
        # --- P0 metrics (Run 23+) ---
        "confidence": confidence,
        "recovery_rates": recovery,
        # --- P1 metrics (Run 23+) ---
        "session_metrics": session_metrics,
        "no_result_by_service": no_result_svc,
        "time_of_day": time_of_day,
        "post_results_engagement": post_results_eng,
        # --- P2 metrics (Run 23+) ---
        "geographic_demand": _compute_geographic_demand(queries),
        "frustration_tiers": _compute_frustration_tiers(turns),
        "session_duration": _compute_session_duration(all_events),
        "repetition_rate": _compute_repetition_rate(all_events),
        # --- P3 metrics (Run 23+) ---
        "llm_metrics": _compute_llm_metrics(),
        # --- Overview headline metrics ---
        "task_completion_rate": _task_completion_rate,
        "avg_turns_to_result": _avg_turns_to_result,
        "no_result_rate": _no_result_rate,
    }


def _conversation_quality(turns, queries, sess_cats):
    total_sess = len(sess_cats)
    total_turns = len(turns)

    emo = {sid for sid, cats in sess_cats.items() if "emotional" in cats}
    emo_rate = round(len(emo) / total_sess, 2) if total_sess else None
    emo_esc = (round(len({s for s in emo if "escalation" in sess_cats.get(s, set())}) / len(emo), 2)
               if emo else None)
    _s = {"service", "confirm_yes"}
    emo_svc = (round(len({s for s in emo if sess_cats.get(s, set()) & _s}) / len(emo), 2)
               if emo else None)

    bq_turns = sum(1 for t in turns if t.get("category") == "bot_question")
    bq_sess = {sid for sid, cats in sess_cats.items() if "bot_question" in cats}
    bq_rate = round(bq_turns / total_turns, 2) if total_turns else None
    bq_frust = (round(len({s for s in bq_sess if "frustration" in sess_cats.get(s, set())}) / len(bq_sess), 2)
                if bq_sess else None)

    q_sess = {q.get("session_id") for q in queries if q.get("session_id")}
    _conv = {"greeting", "emotional", "confused", "general", "bot_question"}
    conv_disc = {sid for sid in q_sess if sess_cats.get(sid, set()) & _conv}
    conv_rate = round(len(conv_disc) / len(q_sess), 2) if q_sess else None

    return {
        "emotional_sessions": len(emo), "emotional_rate": emo_rate,
        "emotional_to_escalation": emo_esc, "emotional_to_service": emo_svc,
        "bot_question_turns": bq_turns, "bot_question_rate": bq_rate,
        "bot_question_sessions": len(bq_sess), "bot_question_to_frustration": bq_frust,
        "conversational_discovery": len(conv_disc), "conversational_discovery_rate": conv_rate,
    }


# Routing bucket definitions — which categories map to which bucket.
_ROUTING_SERVICE_FLOW = {
    "service", "confirmation", "confirmation_nudge", "unrecognized_service",
    "confirm_yes", "confirm_deny", "confirm_change_service", "confirm_change_location",
    "queue_decline",
}
_ROUTING_CONVERSATIONAL = {
    "greeting", "thanks", "help", "bot_question", "bot_identity", "reset",
    "post_results",
}
_ROUTING_EMOTIONAL = {"emotional", "frustration", "confused"}
_ROUTING_SAFETY = {"crisis", "escalation"}
_ROUTING_RECOVERY = {"correction", "negative_preference", "disambiguation", "location_unknown"}


def _compute_routing(turns: list, cat_dist: dict) -> dict:
    """Compute routing distribution: how turns are bucketed across handlers."""
    total = len(turns)

    buckets = {
        "service_flow": 0,
        "conversational": 0,
        "emotional": 0,
        "safety": 0,
        "recovery": 0,
        "general": 0,
    }
    for t in turns:
        cat = t.get("category", "general")
        if cat in _ROUTING_SERVICE_FLOW:
            buckets["service_flow"] += 1
        elif cat in _ROUTING_CONVERSATIONAL:
            buckets["conversational"] += 1
        elif cat in _ROUTING_EMOTIONAL:
            buckets["emotional"] += 1
        elif cat in _ROUTING_SAFETY:
            buckets["safety"] += 1
        elif cat in _ROUTING_RECOVERY:
            buckets["recovery"] += 1
        else:
            buckets["general"] += 1

    general_rate = round(buckets["general"] / total, 2) if total else None

    return {
        "total_categorized": total,
        "buckets": buckets,
        "general_rate": general_rate,
        "category_distribution": cat_dist,
    }


def _compute_tone_distribution(turns: list) -> dict:
    """Compute tone distribution across all turns."""
    tones: dict[str, int] = {}
    total_with_tone = 0
    turns_without_tone = 0

    for t in turns:
        tone = t.get("tone")
        if tone and tone != "crisis":
            # "crisis" is a routing category, not a tone for display purposes.
            # The tone distribution tracks emotional modifiers:
            # emotional, frustrated, confused, urgent.
            tones[tone] = tones.get(tone, 0) + 1
            total_with_tone += 1
        else:
            turns_without_tone += 1

    return {
        "tones": tones,
        "total_with_tone": total_with_tone,
        "turns_without_tone": turns_without_tone,
    }


def _compute_multi_intent(turns: list, queries: list, sess_cats: dict) -> dict:
    """Compute multi-intent queue metrics.

    Queue offers: sessions where 2+ different query templates were executed
                  OR a queue_decline event occurred (an offer was made).
    Queue declines: turns with category 'queue_decline'.
    """
    # Count explicit queue declines
    queue_declines = sum(1 for t in turns if t.get("category") == "queue_decline")

    # Sessions with queue declines
    decline_sessions = {
        t.get("session_id") for t in turns
        if t.get("category") == "queue_decline"
    }

    # Sessions with 2+ query executions (user accepted at least one queue offer)
    session_queries: dict[str, set] = {}
    for q in queries:
        sid = q.get("session_id", "")
        template = q.get("template_name", "")
        session_queries.setdefault(sid, set()).add(template)

    multi_query_sessions = {
        sid for sid, templates in session_queries.items()
        if len(templates) >= 2
    }

    # Queue offers = sessions with decline + sessions with multiple queries
    queue_offer_sessions = decline_sessions | multi_query_sessions
    queue_offers = len(queue_offer_sessions)

    return {
        "queue_offers": queue_offers,
        "queue_declines": queue_declines,
        "queue_accept_sessions": len(multi_query_sessions),
    }


# ---------------------------------------------------------------------------
# P0: CONFIDENCE DISTRIBUTION
# ---------------------------------------------------------------------------

def _compute_confidence(turns: list) -> dict:
    """Aggregate confidence levels across all turns.

    Confidence is logged as a kwarg on every _log_turn() call:
    high = regex matched clearly (deterministic keyword),
    semantic = semantic embedding match (Tier 2, local model),
    medium = LLM classified (unified gate or fallback),
    low = no classification succeeded (fallback),
    disambiguated = user chose from options.
    """
    dist: dict[str, int] = {}
    total = 0
    for t in turns:
        conf = t.get("confidence")
        if conf:
            dist[conf] = dist.get(conf, 0) + 1
            total += 1
    return {
        "distribution": dist,
        "total_with_confidence": total,
        "high_rate": round(dist.get("high", 0) / total, 2) if total else None,
        "semantic_rate": round(dist.get("semantic", 0) / total, 2) if total else None,
        "low_rate": round(dist.get("low", 0) / total, 2) if total else None,
    }


# ---------------------------------------------------------------------------
# P0: CORRECTION / DISAMBIGUATION / NEGATIVE PREFERENCE RATES
# ---------------------------------------------------------------------------

def _compute_recovery_rates(turns: list, sess_cats: dict) -> dict:
    """Per-session rates for recovery categories (Run 22+).

    - correction: user said "not what I meant" — misclassification signal
    - disambiguation: bot showed clarifying options — genuine ambiguity
    - negative_preference: user rejected all results — poor match signal
    """
    total_sess = len(sess_cats) or 1

    correction_turns = sum(1 for t in turns if t.get("category") == "correction")
    correction_sessions = {s for s, c in sess_cats.items() if "correction" in c}

    disambig_turns = sum(1 for t in turns if t.get("category") == "disambiguation")
    disambig_sessions = {s for s, c in sess_cats.items() if "disambiguation" in c}

    negpref_turns = sum(1 for t in turns if t.get("category") == "negative_preference")
    negpref_sessions = {s for s, c in sess_cats.items() if "negative_preference" in c}

    return {
        "correction_turns": correction_turns,
        "correction_session_rate": round(len(correction_sessions) / total_sess, 3),
        "disambiguation_turns": disambig_turns,
        "disambiguation_session_rate": round(len(disambig_sessions) / total_sess, 3),
        "negative_preference_turns": negpref_turns,
        "negative_preference_session_rate": round(len(negpref_sessions) / total_sess, 3),
    }


# ---------------------------------------------------------------------------
# P1: TURNS PER SESSION + BOUNCE RATE
# ---------------------------------------------------------------------------

def _compute_session_metrics(turns: list) -> dict:
    """Turns per session, bounce rate, and distribution."""
    session_turn_counts: dict[str, int] = {}
    for t in turns:
        sid = t.get("session_id", "")
        if sid:
            session_turn_counts[sid] = session_turn_counts.get(sid, 0) + 1

    total_sessions = len(session_turn_counts)
    if total_sessions == 0:
        return {
            "avg_turns_per_session": None,
            "median_turns_per_session": None,
            "bounce_rate": None,
            "total_sessions": 0,
        }

    counts = sorted(session_turn_counts.values())
    avg = round(sum(counts) / len(counts), 1)
    median = counts[len(counts) // 2]
    bounces = sum(1 for c in counts if c == 1)
    bounce_rate = round(bounces / total_sessions, 2)

    # Distribution buckets
    buckets = {"1_turn": 0, "2-3_turns": 0, "4-6_turns": 0,
               "7-10_turns": 0, "11+_turns": 0}
    for c in counts:
        if c == 1:
            buckets["1_turn"] += 1
        elif c <= 3:
            buckets["2-3_turns"] += 1
        elif c <= 6:
            buckets["4-6_turns"] += 1
        elif c <= 10:
            buckets["7-10_turns"] += 1
        else:
            buckets["11+_turns"] += 1

    return {
        "avg_turns_per_session": avg,
        "median_turns_per_session": median,
        "bounce_rate": bounce_rate,
        "bounce_count": bounces,
        "total_sessions": total_sessions,
        "distribution": buckets,
    }


# ---------------------------------------------------------------------------
# P1: NO-RESULT RATE BY SERVICE TYPE
# ---------------------------------------------------------------------------

def _compute_no_result_by_service(queries: list) -> dict:
    """Break down no-result rate by service category."""
    by_service: dict[str, dict] = {}  # {svc: {total: N, no_result: N}}
    for q in queries:
        svc = q.get("params", {}).get("service_type") or q.get("template_name", "")
        if not svc:
            continue
        if svc not in by_service:
            by_service[svc] = {"total": 0, "no_result": 0}
        by_service[svc]["total"] += 1
        if q.get("result_count", 0) == 0:
            by_service[svc]["no_result"] += 1

    result = {}
    for svc, counts in sorted(by_service.items()):
        result[svc] = {
            "total_queries": counts["total"],
            "no_result_count": counts["no_result"],
            "no_result_rate": round(counts["no_result"] / counts["total"], 2)
            if counts["total"] else 0,
        }
    return result


# ---------------------------------------------------------------------------
# P1: TIME-OF-DAY DEMAND PATTERNS
# ---------------------------------------------------------------------------

def _compute_time_of_day(all_events: list) -> dict:
    """Distribution of events by hour (UTC) and day of week."""
    hourly: dict[int, int] = {}
    daily: dict[str, int] = {}

    for e in all_events:
        ts = e.get("timestamp", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour = dt.hour
            day = dt.strftime("%A")
            hourly[hour] = hourly.get(hour, 0) + 1
            daily[day] = daily.get(day, 0) + 1
        except (ValueError, AttributeError):
            continue

    # Fill missing hours
    for h in range(24):
        hourly.setdefault(h, 0)

    peak_hour = max(hourly, key=hourly.get) if hourly else None

    return {
        "hourly": dict(sorted(hourly.items())),
        "daily": daily,
        "peak_hour_utc": peak_hour,
        "total_events": sum(hourly.values()),
    }


# ---------------------------------------------------------------------------
# P1: POST-RESULTS ENGAGEMENT RATE
# ---------------------------------------------------------------------------

def _compute_post_results_engagement(turns: list, queries: list) -> dict:
    """Of sessions that received results, how many asked follow-ups?"""
    # Sessions that got query results
    q_sessions = {q.get("session_id") for q in queries
                  if q.get("session_id") and q.get("result_count", 0) > 0}

    # Sessions that had post_results turns
    pr_sessions = {t.get("session_id") for t in turns
                   if t.get("category") == "post_results"}

    engaged = q_sessions & pr_sessions

    # Count post_results turn types
    pr_types: dict[str, int] = {}
    for t in turns:
        if t.get("category") == "post_results":
            # The sub-type is in the bot response pattern or kwargs
            pr_types["total"] = pr_types.get("total", 0) + 1

    return {
        "sessions_with_results": len(q_sessions),
        "sessions_engaged": len(engaged),
        "engagement_rate": round(len(engaged) / len(q_sessions), 2)
        if q_sessions else None,
        "post_results_turns": pr_types.get("total", 0),
    }


# ---------------------------------------------------------------------------
# P2: GEOGRAPHIC DEMAND DISTRIBUTION
# ---------------------------------------------------------------------------

def _compute_geographic_demand(queries: list) -> dict:
    """Session count and service type breakdown by location.

    Identifies underserved areas: if 40% of searches are for Brooklyn
    but only 15% of database entries are Brooklyn locations, there's a
    coverage gap.
    """
    by_location: dict[str, dict] = {}
    for q in queries:
        loc = q.get("params", {}).get("location", "")
        if not loc:
            continue
        loc = loc.lower().strip()
        if loc not in by_location:
            by_location[loc] = {"total": 0, "services": {}, "no_result": 0}
        by_location[loc]["total"] += 1
        if q.get("result_count", 0) == 0:
            by_location[loc]["no_result"] += 1
        svc = q.get("params", {}).get("service_type", "unknown")
        by_location[loc]["services"][svc] = by_location[loc]["services"].get(svc, 0) + 1

    total_queries = sum(v["total"] for v in by_location.values())
    result = {}
    for loc in sorted(by_location, key=lambda x: by_location[x]["total"], reverse=True):
        info = by_location[loc]
        result[loc] = {
            "total_queries": info["total"],
            "share": round(info["total"] / total_queries, 2) if total_queries else 0,
            "no_result_rate": round(info["no_result"] / info["total"], 2) if info["total"] else 0,
            "top_services": dict(sorted(info["services"].items(), key=lambda x: x[1], reverse=True)[:5]),
        }
    return result


# ---------------------------------------------------------------------------
# P2: FRUSTRATION TIER DISTRIBUTION
# ---------------------------------------------------------------------------

def _compute_frustration_tiers(turns: list) -> dict:
    """Of sessions with frustration, how many reach tier 1/2/3+?

    Validates the 3-tier frustration escalation design.
    If most users hit tier 3, the bot is consistently failing.
    If most stay at tier 1, the first response defuses it.
    """
    # Count frustration turns per session
    session_frust: dict[str, int] = {}
    for t in turns:
        if t.get("category") == "frustration" or t.get("tone") == "frustrated":
            sid = t.get("session_id", "")
            if sid:
                session_frust[sid] = session_frust.get(sid, 0) + 1

    tiers = {"tier_1": 0, "tier_2": 0, "tier_3_plus": 0}
    for count in session_frust.values():
        if count == 1:
            tiers["tier_1"] += 1
        elif count == 2:
            tiers["tier_2"] += 1
        else:
            tiers["tier_3_plus"] += 1

    total_frustrated = len(session_frust)
    return {
        "total_frustrated_sessions": total_frustrated,
        "tiers": tiers,
        "tier_1_rate": round(tiers["tier_1"] / total_frustrated, 2) if total_frustrated else None,
        "tier_3_plus_rate": round(tiers["tier_3_plus"] / total_frustrated, 2) if total_frustrated else None,
    }


# ---------------------------------------------------------------------------
# P2: SESSION DURATION
# ---------------------------------------------------------------------------

def _compute_session_duration(all_events: list) -> dict:
    """Time elapsed from first to last message in a session.

    Combined with turns-per-session, reveals whether long sessions are
    productive (many turns) or stuck (few turns spread over time).
    The capacity scenarios doc models 3/7/15-minute session tiers.
    """
    session_times: dict[str, list] = {}
    for e in all_events:
        sid = e.get("session_id", "")
        ts = e.get("timestamp", "")
        if not sid or not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            session_times.setdefault(sid, []).append(dt)
        except (ValueError, AttributeError):
            continue

    durations_sec = []
    for sid, times in session_times.items():
        if len(times) < 2:
            continue
        times.sort()
        delta = (times[-1] - times[0]).total_seconds()
        durations_sec.append(delta)

    if not durations_sec:
        return {
            "avg_duration_sec": None, "median_duration_sec": None,
            "p95_duration_sec": None, "total_multi_turn_sessions": 0,
            "buckets": {},
        }

    durations_sec.sort()
    n = len(durations_sec)
    avg = round(sum(durations_sec) / n, 1)
    median = round(durations_sec[n // 2], 1)
    p95 = round(durations_sec[int(n * 0.95)], 1) if n >= 20 else None

    # Bucket into capacity model tiers
    buckets = {"under_1min": 0, "1_3min": 0, "3_7min": 0,
               "7_15min": 0, "over_15min": 0}
    for d in durations_sec:
        if d < 60:
            buckets["under_1min"] += 1
        elif d < 180:
            buckets["1_3min"] += 1
        elif d < 420:
            buckets["3_7min"] += 1
        elif d < 900:
            buckets["7_15min"] += 1
        else:
            buckets["over_15min"] += 1

    return {
        "avg_duration_sec": avg,
        "median_duration_sec": median,
        "p95_duration_sec": p95,
        "total_multi_turn_sessions": n,
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# P2: BOT REPETITION RATE
# ---------------------------------------------------------------------------

def _compute_repetition_rate(all_events: list) -> dict:
    """Percentage of sessions where the bot gives the exact same response
    on two consecutive turns.

    The eval judge flags repetition (see edge_frustration_loop scenario).
    This metric directly measures whether the 3-tier frustration fix works.
    """
    # Group turns by session, ordered by timestamp
    session_turns: dict[str, list] = {}
    for e in all_events:
        if e.get("type") != "conversation_turn":
            continue
        sid = e.get("session_id", "")
        if sid:
            session_turns.setdefault(sid, []).append(e)

    sessions_with_repetition = 0
    total_repetitions = 0
    total_sessions = len(session_turns)

    for sid, turns in session_turns.items():
        # Sort by timestamp to ensure order
        turns.sort(key=lambda x: x.get("timestamp", ""))
        prev_response = None
        has_repetition = False
        for t in turns:
            resp = t.get("bot_response", "")
            if resp and resp == prev_response:
                total_repetitions += 1
                has_repetition = True
            prev_response = resp
        if has_repetition:
            sessions_with_repetition += 1

    return {
        "sessions_with_repetition": sessions_with_repetition,
        "total_repetitions": total_repetitions,
        "repetition_rate": round(sessions_with_repetition / total_sessions, 2)
        if total_sessions else None,
        "total_sessions_checked": total_sessions,
    }


# ---------------------------------------------------------------------------
# P3: LLM CALL METRICS
# ---------------------------------------------------------------------------

# LLM call tracking — populated by record_llm_call() after each API call.
# Each entry:
#   {"timestamp": str, "session_id": str, "task": str,
#    "model": str, "input_tokens": int, "output_tokens": int,
#    "latency_ms": int, "success": bool}
_llm_calls: deque = deque(maxlen=MAX_EVENTS)


def record_llm_call(
    task: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    session_id: str = "",
) -> None:
    """Record an LLM API call for cost/latency/volume metrics.

    Called after each Anthropic API call in claude_client.py,
    crisis_detector.py, and llm_slot_extractor.py.
    """
    from datetime import datetime, timezone
    with _lock:
        _llm_calls.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "task": task,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "success": success,
        })


def _compute_llm_metrics() -> dict:
    """Aggregate LLM call metrics: cost, latency, fallback rate.

    Cost tracking is essential for the capacity model — the scenarios
    doc estimates 36,000 sessions/month at scale.
    """
    with _lock:
        calls = list(_llm_calls)

    if not calls:
        return {
            "total_calls": 0, "total_input_tokens": 0,
            "total_output_tokens": 0, "estimated_cost": 0,
            "by_task": {}, "by_model": {},
            "latency_p50_ms": None, "latency_p95_ms": None,
            "failure_rate": None,
        }

    total = len(calls)
    total_in = sum(c.get("input_tokens", 0) for c in calls)
    total_out = sum(c.get("output_tokens", 0) for c in calls)
    failures = sum(1 for c in calls if not c.get("success", True))

    # Cost estimate (Haiku: $1/$5 per MTok, Sonnet: $3/$15 per MTok)
    PRICING = {
        "claude-haiku-4-5-20251001": (1.0, 5.0),
        "claude-sonnet-4-6": (3.0, 15.0),
    }
    cost = 0.0
    for c in calls:
        model = c.get("model", "")
        inp_price, out_price = PRICING.get(model, (1.0, 5.0))
        cost += (c.get("input_tokens", 0) / 1e6 * inp_price +
                 c.get("output_tokens", 0) / 1e6 * out_price)

    # Latency percentiles
    latencies = sorted(c.get("latency_ms", 0) for c in calls if c.get("latency_ms"))
    p50 = latencies[len(latencies) // 2] if latencies else None
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else None

    # By task
    by_task: dict[str, dict] = {}
    for c in calls:
        task = c.get("task", "unknown")
        if task not in by_task:
            by_task[task] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_sum": 0}
        by_task[task]["calls"] += 1
        by_task[task]["input_tokens"] += c.get("input_tokens", 0)
        by_task[task]["output_tokens"] += c.get("output_tokens", 0)
        by_task[task]["latency_sum"] += c.get("latency_ms", 0)
    for task_info in by_task.values():
        task_info["avg_latency_ms"] = round(task_info.pop("latency_sum") / task_info["calls"])

    # By model
    by_model: dict[str, int] = {}
    for c in calls:
        model = c.get("model", "unknown")
        by_model[model] = by_model.get(model, 0) + 1

    # Per-session LLM call count
    session_calls: dict[str, int] = {}
    for c in calls:
        sid = c.get("session_id", "")
        if sid:
            session_calls[sid] = session_calls.get(sid, 0) + 1
    avg_per_session = (round(sum(session_calls.values()) / len(session_calls), 1)
                       if session_calls else None)

    return {
        "total_calls": total,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "estimated_cost": round(cost, 4),
        "failure_rate": round(failures / total, 3) if total else None,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "by_task": by_task,
        "by_model": by_model,
        "avg_calls_per_session": avg_per_session,
    }


# ---------------------------------------------------------------------------
# EVAL RESULTS
# ---------------------------------------------------------------------------

def set_eval_results(data: dict) -> None:
    global _eval_results
    with _lock:
        _eval_results = deepcopy(data)
    persistence.persist_eval_results(data)


def get_eval_results() -> Optional[dict]:
    with _lock:
        return deepcopy(_eval_results) if _eval_results is not None else None


def load_eval_results_from_file(path: str) -> bool:
    try:
        with open(path) as f:
            data = json.load(f)
        set_eval_results(data)
        return True
    except Exception as e:
        logger.error(f"Failed to load eval results from {path}: {e}")
        return False


# ---------------------------------------------------------------------------
# CLEAR
# ---------------------------------------------------------------------------

def clear_audit_log() -> None:
    global _eval_results
    with _lock:
        _events.clear()
        _conversations.clear()
        _query_log.clear()
        _llm_calls.clear()
        _eval_results = None
    persistence.clear_events()


# ---------------------------------------------------------------------------
# HYDRATION — load persisted data on startup
# ---------------------------------------------------------------------------

def hydrate_from_db() -> int:
    """Load persisted events from SQLite into in-memory stores.

    Returns the number of events loaded. Call once at startup.
    """
    if not persistence.is_enabled():
        return 0

    events = persistence.load_all_events(MAX_EVENTS)
    if not events:
        return 0

    with _lock:
        for event in events:
            _events.append(event)
            sid = event.get("session_id", "")
            if sid:
                _register_conversation(sid, event)
            if event.get("type") == "query_execution":
                _query_log.append(event)

    # Also load eval results
    eval_data = persistence.load_eval_results()
    if eval_data:
        global _eval_results
        with _lock:
            _eval_results = eval_data

    logger.info(f"Hydrated audit log from SQLite: {len(events)} events")
    return len(events)
