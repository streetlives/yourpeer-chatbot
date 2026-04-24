"""YourPeer Chatbot package — decomposed in Phase 3 from the 3,290-line
monolithic ``chatbot.py``.

Module layout
-------------

  context         MessageContext, _empty_reply, _count_unique_locations,
                  _DISPLAY_PAGE_SIZE, _USE_LLM, conditional LLM imports
  pipeline        classification-stage helpers (redaction, extraction,
                  LLM gate, routing category, session geo)
  tone            _compute_tone_prefix + empathy-signal phrase lists
  execution       _execute_and_respond + population-critical fallback +
                  response-construction helpers
  logging         _log_turn audit-log wrapper
  orchestrator    generate_reply() dispatcher (thin)
  handlers/       one module per concern — meta / emotional / accessibility /
                  confirmation / post_results / general

This ``__init__`` re-exports the public symbols (those imported by the
test suite and by ``app.routes.chat``) so every caller continues to work
as ``from app.services.chatbot import X``.

It also binds the monkeypatch-target names (``detect_crisis``,
``claude_reply``, ``random_warmth_prefix``, ``_build_confirmation_message``)
at the package level so tests that do ``patch("app.services.chatbot.X")``
continue to intercept the call site — any submodule that uses these names
imports them from their original modules, so the module-level patching
pattern requires the names be present here too.
"""

# --- PUBLIC ENTRY POINT ------------------------------------------------------
from .orchestrator import generate_reply

# --- SHARED CONTEXT / CONSTANTS ---------------------------------------------
from .context import (
    MessageContext,
    _DISPLAY_PAGE_SIZE,
    _USE_LLM,
    _USE_UNIFIED_EXTRACTOR,
    _count_unique_locations,
    _empty_reply,
)

# --- PIPELINE HELPERS (tested directly in test_chatbot_extracted_helpers) ---
from .pipeline import (
    _PII_WARN_TYPES,
    _SKIP_UNIFIED_ACTIONS,
    _apply_session_geo,
    _compute_routing_category,
    _redact_with_safety_warning,
    _run_early_extraction,
    _run_llm_gate,
)

# --- TONE --------------------------------------------------------------------
from .tone import _compute_tone_prefix

# --- LOGGING -----------------------------------------------------------------
from .logging import _log_turn

# --- EXECUTION / POPULATION FALLBACK ----------------------------------------
from .execution import (
    _CITY_TO_BOROUGH,
    _POPULATION_FALLBACK_LABEL,
    _POPULATION_FALLBACK_MAX,
    _POPULATION_RARE_TAXONOMIES,
    _apply_queue_offer,
    _build_db_failure_message,
    _build_neighborhood_borough_table,
    _build_success_response,
    _compute_rare_population_taxonomies,
    _execute_and_respond,
    _nearest_borough_by_centroid,
    _resolve_borough_from_location,
    _run_population_fallback,
    _taxonomies_overlap,
)

# --- HANDLERS (re-exported so tests and older callers can still import) ----
from .handlers import (  # noqa: F401
    _handle_bot_capability_question,
    _handle_bot_identity,
    _handle_change_location_request,
    _handle_change_service_request,
    _handle_confused,
    _handle_context_aware_confirm,
    _handle_correction,
    _handle_crisis,
    _handle_demographic_skip,
    _handle_emotional,
    _handle_escalation,
    _handle_frustration,
    _handle_general_conversation,
    _handle_greeting,
    _handle_help,
    _handle_hours_for_day,
    _handle_location_unknown,
    _handle_negative_preference,
    _handle_pending_confirmation,
    _handle_post_pending_confirmation,
    _handle_post_results_interaction,
    _handle_post_results_question,
    _handle_reset,
    _handle_show_more,
    _handle_sort_results,
    _handle_spanish_detection,
    _handle_thanks,
    _validate_emotional_enhancement,
)

# --- PACKAGE-LEVEL RE-EXPORTS ------------------------------------------------
#
# These imports bind the names on the package (`app.services.chatbot.X`).
# In production they exist mostly for import-path convenience. In tests they
# are a known footgun — read this before writing new patches.
#
# Why the footgun: `patch("app.services.chatbot.X", ...)` only affects the
# name bound here. Submodules like `orchestrator.py` do
# `from app.services.crisis_detector import detect_crisis` at module-load
# time — that statement binds `orchestrator.detect_crisis` to the real
# function. Patching the package-level `detect_crisis` does NOT change
# what `orchestrator.detect_crisis` points to, so the real function
# runs in the hot path and the mock is never called. Tests "pass" only
# because the real function returns benign defaults for typical inputs
# (None from detect_crisis, etc.); the day you send a crisis-shaped
# input or set ANTHROPIC_API_KEY, they fail in confusing ways.
#
# CORRECT patch targets (patch where the function is looked up, not
# where it's defined):
#
#   detect_crisis         → app.services.chatbot.orchestrator.detect_crisis
#                           (also app.services.classifier.detect_crisis
#                            for tests that exercise the classifier path)
#   claude_reply (service flow) → app.services.chatbot.handlers.meta.claude_reply
#   claude_reply (fallback)     → app.services.responses.claude_reply
#   _USE_LLM (dispatch gate)    → app.services.chatbot.orchestrator._USE_LLM
#   _USE_LLM (classifier gate)  → app.services.chatbot.pipeline._USE_LLM
#   query_services              → app.services.chatbot.execution.query_services
#   classify_unified            → app.services.chatbot.pipeline.classify_unified
#                                 (bound only when _USE_LLM; see below)
#   save_session_slots          → the specific submodule using it; search
#                                 for `from app.services.session_store import
#                                 save_session_slots` to find bind sites
#
# The `send()` and `send_multi()` helpers in tests/conftest.py use the
# correct targets and should be the template for any new helpers.
#
# Why we still re-export here: some legacy unit tests and a codemod-
# applied rewrite target these names. Removing the re-exports would
# break those tests without improving anything. The fix belongs in
# the tests — migrate them to the correct targets listed above and
# eventually these `# noqa: F401` lines can go.
#
# classify_unified is bound conditionally (only when _USE_LLM is truthy,
# ie ANTHROPIC_API_KEY is set). In regex-only mode it's left unbound
# and test fixtures should patch `app.services.chatbot.pipeline.classify_unified`.
#
from app.llm.claude_client import claude_reply  # noqa: F401
from app.services.confirmation import _build_confirmation_message  # noqa: F401
from app.services.crisis_detector import detect_crisis  # noqa: F401
from app.services.responses import random_warmth_prefix  # noqa: F401
from app.services.session_store import save_session_slots  # noqa: F401
if _USE_LLM:
    from app.services.llm_classifier import classify_unified  # noqa: F401


__all__ = [
    # Public entry point
    "generate_reply",
    # Context / constants
    "MessageContext",
    "_DISPLAY_PAGE_SIZE",
    "_USE_LLM",
    "_USE_UNIFIED_EXTRACTOR",
    "_count_unique_locations",
    "_empty_reply",
    # Pipeline
    "_PII_WARN_TYPES",
    "_SKIP_UNIFIED_ACTIONS",
    "_apply_session_geo",
    "_compute_routing_category",
    "_redact_with_safety_warning",
    "_run_early_extraction",
    "_run_llm_gate",
    # Tone
    "_compute_tone_prefix",
    # Logging
    "_log_turn",
    # Execution / population fallback
    "_CITY_TO_BOROUGH",
    "_POPULATION_FALLBACK_LABEL",
    "_POPULATION_FALLBACK_MAX",
    "_POPULATION_RARE_TAXONOMIES",
    "_apply_queue_offer",
    "_build_db_failure_message",
    "_build_neighborhood_borough_table",
    "_build_success_response",
    "_compute_rare_population_taxonomies",
    "_execute_and_respond",
    "_nearest_borough_by_centroid",
    "_resolve_borough_from_location",
    "_run_population_fallback",
    "_taxonomies_overlap",
    # Handlers
    "_handle_bot_capability_question",
    "_handle_bot_identity",
    "_handle_change_location_request",
    "_handle_change_service_request",
    "_handle_confused",
    "_handle_context_aware_confirm",
    "_handle_correction",
    "_handle_crisis",
    "_handle_demographic_skip",
    "_handle_emotional",
    "_handle_escalation",
    "_handle_frustration",
    "_handle_general_conversation",
    "_handle_greeting",
    "_handle_help",
    "_handle_hours_for_day",
    "_handle_location_unknown",
    "_handle_negative_preference",
    "_handle_pending_confirmation",
    "_handle_post_pending_confirmation",
    "_handle_post_results_interaction",
    "_handle_post_results_question",
    "_handle_reset",
    "_handle_show_more",
    "_handle_sort_results",
    "_handle_spanish_detection",
    "_handle_thanks",
    "_validate_emotional_enhancement",
    # Monkeypatch targets
    "claude_reply",
    "detect_crisis",
    "random_warmth_prefix",
    "_build_confirmation_message",
]
