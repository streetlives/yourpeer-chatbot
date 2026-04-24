"""Post-results handlers — runs BEFORE the routing cascade.

Once service cards have been displayed, the user's next message is almost
always about those cards: "show more", "sort by newest", "what are the
hours", "tell me about the first one". These handlers match those
patterns and answer from cached session state without hitting the DB or
LLM.

The top-level entry point is ``_handle_post_results_interaction`` — it
dispatches to the specific sub-handler (show more / sort / question /
hours) and also owns the teardown logic for when the user's next
message is clearly a new search rather than a follow-up.

Audit invariant (tests/unit/test_audit_regression.py): the
``answer_from_results(`` call site must pass ``displayed_count`` from
the session so filter responses paginate correctly.
"""

from app.rag.query_executor import fetch_schedule_for_day
from app.rag.query_templates import _format_time
from app.services.phrase_lists import _WELCOME_QUICK_REPLIES
from app.services.post_results import (
    answer_from_results,
    classify_post_results_question,
)
from app.services.session_store import save_session_slots

from ..context import _DISPLAY_PAGE_SIZE, _count_unique_locations, _empty_reply
from ..logging import _log_turn


# Phrases that request pagination of prior results.
_SHOW_MORE_PATTERNS = (
    "show all results", "show results", "show all",
    "show more results", "show more", "more results",
    "any others", "what else", "next results",
    "any more", "see more",
)
# Subset of _SHOW_MORE_PATTERNS that explicitly mean "everything, unfiltered".
# Tapping these while a filter is active clears the filter.
_SHOW_ALL_PATTERNS = ("show all results", "show all", "show results")

# Sort-mode phrases → internal sort key.
_SORT_PATTERNS = {
    "sort by recently verified": "verified",
    "sort by recently updated": "verified",
    "sort by newest": "verified",
    "sort by most services": "services",
    "most services": "services",
}

# ISO day-of-week (1=Mon .. 7=Sun) for the hours-for-day formatter.
_ISODOW_NAMES = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
                 5: "Friday", 6: "Saturday", 7: "Sunday"}


def _handle_show_more(session_id, message, redacted_message, existing,
                      last_results, request_id):
    """Handle 'show more results' / 'show all' after results were displayed."""
    msg_lower = message.lower().strip()
    if msg_lower not in _SHOW_MORE_PATTERNS:
        return None

    # When a filter is active, "show all" escapes back to unfiltered results;
    # "show more" paginates through the filtered set.
    filtered = existing.get("_filtered_results")
    is_show_all = msg_lower in _SHOW_ALL_PATTERNS

    if filtered and is_show_all:
        # Escape the filter — clear state and re-display the first page
        # of the unfiltered set (same pagination UX as initial display).
        existing.pop("_filtered_results", None)
        existing.pop("_filter_phrase", None)
        first_page = last_results[:_DISPLAY_PAGE_SIZE]
        existing["_displayed_count"] = len(first_page)
        save_session_slots(session_id, existing)
        qr = [
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ]
        remaining = len(last_results) - len(first_page)
        if remaining > 0:
            remaining_locs = _count_unique_locations(
                last_results[len(first_page):len(first_page) + _DISPLAY_PAGE_SIZE]
            )
            qr.insert(0, {
                "label": f"📋 Show {remaining_locs} more result{'s' if remaining_locs != 1 else ''}",
                "value": "Show more results",
            })
        result = {
            "session_id": session_id,
            "response": "Here are all the results again:",
            "follow_up_needed": False,
            "slots": existing,
            "services": first_page,
            "result_count": len(first_page),
            "relaxed_search": False,
            "quick_replies": qr,
        }
        _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
        return result

    # Source set: filtered when active, otherwise the full last_results.
    source = filtered if filtered else last_results

    displayed = existing.get("_displayed_count", 0)
    if displayed and displayed < len(source):
        # Show the next page of results (not all remaining)
        next_page = source[displayed:displayed + _DISPLAY_PAGE_SIZE]
        new_displayed = displayed + len(next_page)
        existing["_displayed_count"] = new_displayed
        save_session_slots(session_id, existing)

        still_remaining = len(source) - new_displayed
        qr = [
            {"label": "🔍 New search", "value": "Start over"},
            {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
        ]
        if still_remaining > 0:
            remaining_locs = _count_unique_locations(
                source[new_displayed:new_displayed + _DISPLAY_PAGE_SIZE]
            )
            qr.insert(0, {
                "label": f"📋 Show {remaining_locs} more result{'s' if remaining_locs != 1 else ''}",
                "value": "Show more results",
            })

        page_loc_count = _count_unique_locations(next_page)
        result = {
            "session_id": session_id,
            "response": f"Here are {page_loc_count} more result{'s' if page_loc_count != 1 else ''}:",
            "follow_up_needed": False,
            "slots": existing,
            "services": next_page,
            "result_count": page_loc_count,
            "relaxed_search": False,
            "quick_replies": qr,
        }
    else:
        # No more to show — re-display everything from the active source.
        result = {
            "session_id": session_id,
            "response": "Here are all the results again:",
            "follow_up_needed": False,
            "slots": existing,
            "services": source,
            "result_count": len(source),
            "relaxed_search": False,
            "quick_replies": [
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ],
        }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


def _handle_sort_results(session_id, message, redacted_message, existing,
                         last_results, request_id):
    """Handle 'sort by recently verified' / 'most services' requests."""
    sort_key = _SORT_PATTERNS.get(message.lower().strip())
    if not (sort_key and last_results):
        return None

    if sort_key == "verified":
        sorted_results = sorted(
            last_results,
            key=lambda s: s.get("last_validated_at") or "",
            reverse=True,
        )
    else:  # "services"
        sorted_results = sorted(
            last_results,
            key=lambda s: len(s.get("also_available") or []),
            reverse=True,
        )
    existing["_last_results"] = sorted_results
    sort_page = sorted_results[:_DISPLAY_PAGE_SIZE]
    existing["_displayed_count"] = len(sort_page)
    save_session_slots(session_id, existing)

    sort_remaining = len(sorted_results) - len(sort_page)
    sort_qr = [
        {"label": "🔍 New search", "value": "Start over"},
        {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
    ]
    if sort_remaining > 0:
        next_sort_page = sorted_results[len(sort_page):len(sort_page) + _DISPLAY_PAGE_SIZE]
        show_next = _count_unique_locations(next_sort_page)
        sort_qr.insert(0, {
            "label": f"📋 Show {show_next} more result{'s' if show_next != 1 else ''}",
            "value": "Show more results",
        })

    sort_label = "most recently verified" if sort_key == "verified" else "most services at location"
    result = {
        "session_id": session_id,
        "response": f"Here are the results sorted by {sort_label}:",
        "follow_up_needed": False,
        "slots": existing,
        "services": sort_page,
        "result_count": len(sort_page),
        "relaxed_search": False,
        "quick_replies": sort_qr,
    }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


def _handle_hours_for_day(
    session_id, existing, last_results, post_intent, redacted_message, request_id,
):
    """Look up schedule for a specific day of the week for displayed services."""
    weekday = post_intent.get("weekday")
    is_weekend = post_intent.get("weekend", False)
    if weekday is None or not last_results:
        return None

    # Collect service IDs
    service_ids = [s["service_id"] for s in last_results if s.get("service_id")]
    if not service_ids:
        return None

    # Fetch from DB
    schedule = fetch_schedule_for_day(service_ids, weekday)

    # For weekend requests, also fetch Sunday
    if is_weekend:
        sunday_sched = fetch_schedule_for_day(service_ids, 7)
        for sid, slots in sunday_sched.items():
            schedule.setdefault(sid, []).extend(slots)

    day_name = _ISODOW_NAMES.get(weekday, f"day {weekday}")
    if is_weekend:
        day_name = "the weekend"

    # Build response lines
    lines = []
    for svc in last_results:
        sid = svc.get("service_id")
        name = svc.get("service_name", "Unknown")
        sched_entries = schedule.get(sid)
        if sched_entries:
            time_strs = []
            for entry in sched_entries:
                opens = _format_time(entry.get("opens_at"))
                closes = _format_time(entry.get("closes_at"))
                if opens and closes:
                    time_strs.append(f"{opens} – {closes}")
            if time_strs:
                lines.append(f"• **{name}**: {', '.join(time_strs)}")
            else:
                lines.append(f"• **{name}**: Hours not confirmed")
        else:
            lines.append(f"• **{name}**: No schedule data for {day_name}")

    response = f"Here are the hours for {day_name}:\n\n" + "\n".join(lines)
    response += "\n\nHours can change — I'd recommend calling ahead to confirm."

    result = {
        "session_id": session_id,
        "response": response,
        "follow_up_needed": False,
        "slots": existing,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": [
            {"label": "📋 Show results again", "value": "Show all results"},
            {"label": "🔍 New search", "value": "Start over"},
        ],
    }
    _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
    return result


def _handle_post_results_question(session_id, message, redacted_message, existing,
                                  last_results, request_id):
    """Handle questions about specific results ('what are the hours', 'tell me
    about the second one', etc.) after results were displayed."""
    post_intent = classify_post_results_question(message)
    if post_intent is None:
        return None

    # Day-specific hours — requires DB lookup
    if post_intent.get("type") == "ask_hours_day":
        result = _handle_hours_for_day(
            session_id, existing, last_results, post_intent, redacted_message, request_id,
        )
        if result:
            return result

    pr = answer_from_results(
        post_intent,
        last_results,
        existing.get("_displayed_count", len(last_results)),
    )
    if pr is not None:
        # Persist filter state when _handle_filter_subcategory matched.
        # _full_filtered is the complete filter result set (possibly larger
        # than the page shown); subsequent "show more" and questions about
        # the displayed results will operate on this filtered view until
        # it's cleared by a state transition (no-thanks / frustration /
        # new search).
        if pr.get("_filter_matched"):
            existing["_filtered_results"] = pr.get("_full_filtered", [])
            existing["_filter_phrase"] = pr.get("_filter_phrase")
            existing["_displayed_count"] = len(pr.get("services", []))
            save_session_slots(session_id, existing)

        result = {
            "session_id": session_id,
            "response": pr["response"],
            "follow_up_needed": False,
            "slots": existing,
            "services": pr.get("services", []),
            "result_count": len(pr.get("services", [])),
            "relaxed_search": False,
            "quick_replies": pr.get("quick_replies", []),
        }
        _log_turn(session_id, redacted_message, result, "post_results", request_id=request_id)
        return result

    if post_intent.get("type") == "specific_name":
        query = post_intent.get("query", "that")
        result = _empty_reply(
            session_id,
            "I'm not sure if you're asking about the results "
            "I showed, or if you'd like to search for "
            "something new. Which would you prefer?",
            existing,
            quick_replies=[
                {"label": f"🔍 Search for {query}", "value": f"I need {query}"},
                {"label": "📋 More about results", "value": "Tell me about the first one"},
                {"label": "🔍 New search", "value": "Start over"},
            ],
        )
        _log_turn(session_id, redacted_message, result, "disambiguation",
                  request_id=request_id, confidence="disambiguated")
        return result

    return None


def _handle_post_results_interaction(
    session_id: str,
    message: str,
    redacted_message: str,
    existing: dict,
    early_extracted: dict,
    has_service_intent: bool,
    action_pre: str | None,
    tone: str | None,
    request_id: str | None,
) -> dict | None:
    """Handle follow-up messages after results have been shown.

    This runs BEFORE the routing cascade — it catches "show more," sort
    requests, questions about specific results, and confirm_yes/confirm_deny
    that would otherwise fall through to extraction and re-trigger the same
    search. When the user has instead started a new search (new service
    intent, or a confirmation action), it clears the stale result state so
    the routing cascade can handle the new intent cleanly.

    Returns a result dict if a post-results interaction fired, ``None`` if
    the caller should continue to the normal routing cascade. May mutate
    ``existing`` (pops ``_last_results`` / ``_last_action`` and saves).
    """
    last_results = existing.get("_last_results")
    is_confirmation_action = action_pre in (
        "confirm_change_service", "confirm_change_location",
        "confirm_yes", "confirm_deny", "reset", "greeting",
    )

    # confirm_yes / confirm_deny after results when no pending confirmation.
    # Without this, those messages fall through to extraction and re-trigger
    # the same search.
    # Guard: when the message ALSO contains a new service intent (e.g.
    # "Search for employment in Manhattan"), the new intent should override
    # the confirm action. Without this guard, "search for" matches
    # confirm_yes and the user's new request gets swallowed.
    if (last_results
            and action_pre in ("confirm_yes", "confirm_deny")
            and not existing.get("_pending_confirmation")
            and not existing.get("_queue_offer_pending")
            and not existing.get("_queued_services")
            and not existing.get("_last_action")
            and not has_service_intent):

        if action_pre == "confirm_yes":
            existing.pop("_last_results", None)
            save_session_slots(session_id, existing)
            result = _empty_reply(
                session_id,
                "I've already shown the results above — you can tap on any "
                "service card for more details. Would you like to search for "
                "something else?",
                existing,
                quick_replies=[
                    {"label": "🔍 New search", "value": "Start over"},
                    {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
                ],
            )
            _log_turn(session_id, redacted_message, result, "post_results_confirm",
                      request_id=request_id, tone=tone)
            return result

        # confirm_deny
        # With filter active, treat as "escape the filter" rather than
        # "I'm done" — clear filter state, preserve _last_results, and
        # re-display the first page of the unfiltered set. This matches
        # the filter-pipeline design where "no thanks" after filter is
        # an escape path, not a session end.
        if existing.get("_filtered_results"):
            existing.pop("_filtered_results", None)
            existing.pop("_filter_phrase", None)
            first_page = last_results[:_DISPLAY_PAGE_SIZE]
            existing["_displayed_count"] = len(first_page)
            save_session_slots(session_id, existing)
            qr = [
                {"label": "🔍 New search", "value": "Start over"},
                {"label": "🤝 Peer navigator", "value": "Connect with peer navigator"},
            ]
            remaining = len(last_results) - len(first_page)
            if remaining > 0:
                remaining_locs = _count_unique_locations(
                    last_results[len(first_page):len(first_page) + _DISPLAY_PAGE_SIZE]
                )
                qr.insert(0, {
                    "label": f"📋 Show {remaining_locs} more result{'s' if remaining_locs != 1 else ''}",
                    "value": "Show more results",
                })
            result = {
                "session_id": session_id,
                "response": "No problem — here are all the results again:",
                "follow_up_needed": False,
                "slots": existing,
                "services": first_page,
                "result_count": len(first_page),
                "relaxed_search": False,
                "quick_replies": qr,
            }
            _log_turn(session_id, redacted_message, result, "post_results_filter_escape",
                      request_id=request_id, tone=tone)
            return result

        existing.pop("_last_results", None)
        save_session_slots(session_id, existing)
        result = _empty_reply(
            session_id,
            "No problem! Let me know if you need anything else.",
            existing,
            quick_replies=list(_WELCOME_QUICK_REPLIES),
        )
        _log_turn(session_id, redacted_message, result, "post_results_decline",
                  request_id=request_id, tone=tone)
        return result

    if last_results and not has_service_intent and not is_confirmation_action:
        # Clear stale _last_action: if the user is interacting with
        # results (asking questions, sorting, paginating), any prior
        # emotional/escalation/crisis context is no longer relevant.
        if existing.get("_last_action"):
            existing.pop("_last_action", None)
            save_session_slots(session_id, existing)

        is_frustration_or_rejection = (
            tone == "frustrated"
            or action_pre == "negative_preference"
            or action_pre == "correction"
        )
        if is_frustration_or_rejection:
            # Intentionally DO NOT pop _last_results here. The downstream
            # frustration handler (_handle_frustration in emotional.py)
            # uses _last_results to distinguish post-results frustration
            # ("these results weren't helpful" → escalate to navigator)
            # from pre-results frustration ("you keep re-asking" →
            # apologize + reconfirm). Popping it here blinds that branch.
            # _last_results is naturally replaced by the next successful
            # search or cleared on reset, so leaving it in place is safe.
            pass
        elif early_extracted.get("location"):
            existing.pop("_last_results", None)
            save_session_slots(session_id, existing)
        else:
            # "Show all results" / "Show more results"
            show_result = _handle_show_more(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if show_result:
                return show_result

            # Sort options
            sort_result = _handle_sort_results(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if sort_result:
                return sort_result

            # Questions about specific results
            post_intent_result = _handle_post_results_question(
                session_id, message, redacted_message, existing,
                last_results, request_id,
            )
            if post_intent_result:
                return post_intent_result

    if last_results and (has_service_intent or is_confirmation_action):
        existing.pop("_last_results", None)
        # When results were already shown and the user asks for something
        # new, treat it as a fresh search — clear the old service slots so
        # they don't compound with the new request. Multi-service should
        # only happen within a single message or before results.
        if has_service_intent:
            existing.pop("service_type", None)
            existing.pop("service_detail", None)
            existing.pop("_queued_services", None)
            existing.pop("_queued_services_original", None)
            existing.pop("_queue_offer_pending", None)
            existing.pop("_queued_offer", None)
            existing.pop("_queued_location", None)
            existing.pop("_pending_confirmation", None)
            existing.pop("_displayed_count", None)
            # Filter state was tied to the prior _last_results — clear it
            # so it doesn't bleed into the new search's results.
            existing.pop("_filtered_results", None)
            existing.pop("_filter_phrase", None)
        save_session_slots(session_id, existing)

    return None
