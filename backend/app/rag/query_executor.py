"""
Query Executor — runs parameterized template queries against the Streetlives DB.

Architecture: This is the ONLY module that talks to the database.
All queries come from query_templates.py — never from LLM output.

Usage:
    from app.rag.query_executor import execute_service_query

    results = execute_service_query(
        template_key="food",
        user_params={"city": "Brooklyn", "age": 25},
    )
    # results = {
    #     "services": [{"service_name": "...", "address": "...", ...}, ...],
    #     "result_count": 3,
    #     "template_used": "FoodQuery",
    #     "params_applied": {"taxonomy_name": "Food", "city": "Brooklyn", "age": 25},
    #     "relaxed": False,
    # }
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv

from app.rag.query_templates import (
    build_query,
    build_relaxed_query,
    format_service_card,
    deduplicate_results,
    TEMPLATES,
    _FRESHNESS_DAYS,
)

load_dotenv()
logger = logging.getLogger(__name__)


class QueryTimeoutError(Exception):
    """Raised when a database query exceeds the statement_timeout.

    Distinguished from other query errors so execute_service_query()
    can fall back to a faster relaxed query immediately rather than
    treating it as 'no results found'.
    """
    pass


class DatabaseUnreachableError(Exception):
    """Raised when the DB host can't be reached at all (TCP connect
    failure, host DNS unresolvable, network partition).

    Distinct from QueryTimeoutError — that one means "we connected and
    ran a query but it took too long"; this one means "we never
    got a connection in the first place." Different operational signal,
    different user-facing message.

    Distinct from the silent `return []` path used for unexpected
    SQLAlchemy errors. Connection-unreachable is recoverable infra
    state, not a "no rows match" result; surfacing it lets callers
    show an honest "service temporarily unavailable, please retry"
    instead of a misleading "no services found near you."

    Triggered by psycopg2.OperationalError messages containing
    transport-layer failure markers ("could not connect to server",
    "connection refused", "operation timed out", "no route to host",
    "name or service not known"). The string sniff is necessary
    because SQLAlchemy wraps everything as OperationalError and
    psycopg2's specific exception types aren't always importable
    in our environment (same constraint that motivated the string
    sniff for QueryCanceled above).
    """
    pass

# ---------------------------------------------------------------------------
# DATABASE CONNECTION
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")

_engine = None


def _get_engine():
    """Lazy-initialize the SQLAlchemy engine with connection pooling."""
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it to your .env file.\n"
                "Format: postgresql://user:password@host:port/streetlives"
            )
        _engine = create_engine(
            DATABASE_URL,
            poolclass=QueuePool,
            pool_size=3,            # 3 persistent connections (was 5)
            max_overflow=5,         # up to 8 total under burst (was 10→15)
            pool_timeout=10,        # fail fast — 10s, not default 30s
            pool_pre_ping=True,     # verify connections before use
            # Recycle after 5 min. Render's managed Postgres closes idle
            # connections more aggressively than 30 min; pre_ping papers
            # over it but we still occasionally lose the race and surface
            # "SSL connection has been closed unexpectedly" to callers.
            # 5 min keeps connections fresh without churning the pool.
            pool_recycle=300,
            pool_use_lifo=True,     # reuse most-recent connection first;
                                    # idle connections naturally expire,
                                    # reducing total open connections
            echo=False,
            # D3: prevent runaway queries from blocking indefinitely.
            # All queries in this app are parameterized lookups against
            # indexed tables — 5 seconds is generous.
            #
            # TCP keepalives: catch half-open sockets at the transport
            # layer instead of discovering them on next query. Without
            # these, a connection killed by a NAT / firewall / provider-
            # side idle timeout sits silently in the pool and surfaces
            # as OperationalError("SSL connection has been closed
            # unexpectedly") when pre_ping runs. With keepalives, the
            # kernel tears down the dead socket and the pool notices.
            connect_args={
                "options": "-c statement_timeout=5000"
                           " -c idle_in_transaction_session_timeout=10000",
                # Fail fast on dead RDS hosts. Without this, psycopg2
                # uses the OS-default TCP connect timeout (~75-130s
                # depending on kernel) — which means a brief AWS network
                # blip or RDS maintenance window ties up a backend worker
                # for over a minute on a single dead connection,
                # multiplied across the request burst that always hits
                # exactly when service is degraded. 5s is comfortably
                # above the actual us-east-1 RDS connect time (~50-200ms)
                # while bounding worst-case worker tieup.
                "connect_timeout": 5,
                "keepalives": 1,
                "keepalives_idle": 30,       # send keepalive after 30s idle
                "keepalives_interval": 10,   # retry every 10s
                "keepalives_count": 3,       # give up after 3 failed probes
            },
        )
    return _engine


def dispose_engine():
    """Dispose the connection pool, closing all checked-in connections.

    Called on shutdown so the DB doesn't see orphaned sessions after
    Render restarts or redeploys the service.
    """
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def test_connection() -> bool:
    """Verify the database is reachable.

    Uses a local statement_timeout override (15s) rather than the
    connection-level 5s cap that applies to business queries. The 5s
    ceiling exists to protect against runaway chatbot queries; a
    liveness probe has the opposite requirement — a briefly slow DB
    should register as "up but slow," not "down." 15s also absorbs the
    occasional transient stall on Render's managed Postgres (checkpoint,
    autovacuum spike, noisy neighbor) without flapping the health
    endpoint.

    Logged at WARNING, not ERROR: intermittent probe failures on shared
    managed Postgres are routine operational noise, not actionable
    errors. Alerting should fire on sustained failure rate, not on any
    single failure.
    """
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            # SET LOCAL scopes to the current transaction; rolls back
            # implicitly when the `with` block exits. The connection
            # returns to the pool with its original 5s ceiling intact.
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            result = conn.execute(text("SELECT 1"))
            return result.fetchone()[0] == 1
    except Exception as e:
        logger.warning(f"Database connection test failed: {e}")
        return False


# ---------------------------------------------------------------------------
# FRESHNESS STATS
# ---------------------------------------------------------------------------

# _FRESHNESS_DAYS is imported from query_templates to keep the SQL sort's
# "fresh" tier threshold in lockstep with the displayed freshness stats.
# If you change the threshold, change it in query_templates._FRESHNESS_DAYS
# only — this module picks up the new value automatically.


def _compute_freshness(rows: list[dict]) -> dict:
    """Count how many results were verified within the last _FRESHNESS_DAYS.

    Operates on raw query rows (before format_service_card drops
    the last_validated_at field).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_FRESHNESS_DAYS)
    total = len(rows)
    total_with_date = 0
    fresh = 0

    for row in rows:
        lva = row.get("last_validated_at")
        if lva is None:
            continue
        total_with_date += 1
        # Handle both timezone-aware and naive datetimes from the DB
        if not hasattr(lva, "tzinfo") or lva.tzinfo is None:
            lva = lva.replace(tzinfo=timezone.utc)
        if lva >= cutoff:
            fresh += 1

    return {
        "fresh": fresh,
        "total": total,
        "total_with_date": total_with_date,
    }


# ---------------------------------------------------------------------------
# GEOGRAPHIC BOROUGH VALIDATION
# ---------------------------------------------------------------------------
# Derive each service's borough from its (lat, lon) coordinates using NYC
# DCP polygons (see app.rag.boundaries). Compare against the borough
# inferred from pa.city to detect data-quality issues like the user-reported
# "Manhattan service that's actually in the Bronx."
#
# Why defensive-only (don't filter): this is shipping as a data-quality
# signal, not a behavior change. Filtering based on polygon mismatch has
# false-positive risk — a service could legitimately appear in one borough
# but serve another (e.g., mobile units), and the simplified polygon has
# ~0.56% disagreement with the full polygon near waterways. For the initial
# rollout we annotate + log mismatches and surface them in admin telemetry
# so Community Information Specialists can triage upstream.
#
# Once mismatch rates are known and triaged, the policy can escalate to
# filtering/badging based on real data.

# pa.city value (lowercased) → canonical borough name.
# Derived from NYC_LOCATION_ALIASES so adding a new neighborhood alias
# automatically extends the mismatch-check coverage. Computed lazily
# because NYC_LOCATION_ALIASES is defined later in this module.
_CITY_TO_STATED_BOROUGH: Optional[dict] = None
_CITY_TO_BOROUGH_LOCK = threading.Lock()


def _build_city_to_borough() -> dict:
    """Invert NYC_LOCATION_ALIASES: each pa.city value → canonical borough."""
    # Map: primary pa.city value -> canonical borough name
    # {"New York": "Manhattan", "Brooklyn": "Brooklyn", ...}
    primary_to_borough = {v: k for k, v in _BOROUGH_TO_PRIMARY_CITY.items()}

    city_to_borough: dict = {}
    # Start with primary values (covers the bulk of rows)
    for primary, borough in primary_to_borough.items():
        city_to_borough[primary.lower()] = borough

    # Add neighborhood-level city values that the DB sometimes stores
    # (e.g., pa.city='Astoria' → 'Queens'). NYC_LOCATION_ALIASES maps
    # alias → primary_city; we compose alias → primary_city → borough.
    for alias, primary_city in NYC_LOCATION_ALIASES.items():
        borough = primary_to_borough.get(primary_city)
        if borough:
            # alias may be lowercase ("manhattan") or titled ("Manhattan")
            # in DB; store lowercased for case-insensitive lookup
            city_to_borough[alias.lower()] = borough

    return city_to_borough


def _get_city_to_borough() -> dict:
    """Lazy-initialized _CITY_TO_STATED_BOROUGH accessor.

    Double-checked locking so concurrent first-requests don't both run
    _build_city_to_borough. Writes are idempotent so even without the
    lock nothing corrupts — but we save a few hundred µs of wasted work
    per cold start and keep this consistent with boundaries._load.
    """
    global _CITY_TO_STATED_BOROUGH
    # Fast path: already built. No lock needed because the assignment
    # at the end of the slow path is an atomic reference swap in CPython.
    if _CITY_TO_STATED_BOROUGH is not None:
        return _CITY_TO_STATED_BOROUGH

    with _CITY_TO_BOROUGH_LOCK:
        if _CITY_TO_STATED_BOROUGH is None:
            _CITY_TO_STATED_BOROUGH = _build_city_to_borough()
        return _CITY_TO_STATED_BOROUGH


def _stated_borough_from_city(city: Optional[str]) -> Optional[str]:
    """Infer the canonical borough from a pa.city value.

    Returns the borough name ("Manhattan", "Bronx", etc.) if city is a
    known NYC value, or None for out-of-town cities, unmapped values,
    or empty strings.
    """
    if not city:
        return None
    return _get_city_to_borough().get(city.strip().lower())


# Public alias for the city→borough resolver.
#
# Originally `_stated_borough_from_city` was private to this module
# because only `_annotate_geographic_borough` (below) consumed it.
# The locations admin section then started reading it directly for
# its coordinate-validation table, which created an awkward
# private-import dependency across modules. Exposing this name as
# the public-facing form (no leading underscore) gives external
# consumers a stable symbol to import without reaching into the
# module's internals.
#
# If the implementation ever needs to change shape (cached differently,
# moved to boundaries.py, etc.), the private name can move while this
# alias stays as the import-stable surface.
stated_borough_from_city = _stated_borough_from_city


# Public alias for the full city→borough dict (lowercased keys).
#
# The locations admin's `_borough_case_sql` builds a SQL CASE statement
# from this mapping so the deployed heatmap and borough-breakdown
# bucket raw `pa.city` values to the right borough — without it, the
# helper falls back to exact-string, case-sensitive matching on five
# names, which silently routes case variants ("BROOKLYN"), aliases
# ("The Bronx"), and Queens neighborhoods ("Astoria", "Flushing",
# "Jamaica", "Long Island City") into "Other".
#
# Same rationale as stated_borough_from_city above: keeping the public
# alias here means the admin code imports a stable symbol instead of
# reaching past the underscore.
get_nyc_city_to_borough = _get_city_to_borough


# Services we've already warned about this process. Rate-limits the
# borough_mismatch WARNING log so a popular wrong-borough service doesn't
# flood the logs each time it's returned from a search. Set membership is
# O(1), and `set.add` is atomic under CPython's GIL — safe without a lock.
# Bounded in practice by the DB size (~2,500 services), so memory growth
# is trivial. Reset via _reset_mismatch_warnings() (test-only).
_WARNED_MISMATCH_SERVICE_IDS: set[str] = set()


def _reset_mismatch_warnings() -> None:
    """Clear the "already warned" set. ONLY for tests. Not public API."""
    _WARNED_MISMATCH_SERVICE_IDS.clear()


def _annotate_geographic_borough(cards: list[dict]) -> list[dict]:
    """Tag each card with geographic_borough (from coords) and
    borough_mismatch (True if stated city disagrees with coords).

    Mutates and returns the input list for caller convenience.

    Policy: log every mismatch at WARNING level (so they surface in
    default log aggregation — this is a data-quality issue worth
    noticing). Do NOT filter — the data is still delivered to the
    user, just annotated so downstream code can surface/filter based
    on the flag. See boundaries module docstring.
    """
    # Import here to avoid a circular: boundaries has no deps, but
    # query_executor is imported early at app startup and we want to
    # keep the boundaries GeoJSON load lazy.
    from app.rag.boundaries import borough_from_coords

    for card in cards:
        lat = card.get("latitude")
        lon = card.get("longitude")
        if lat is None or lon is None:
            # No coordinates — nothing to validate. Skip silently; this
            # is a known gap for pilot imports and manual entries.
            card["geographic_borough"] = None
            card["borough_mismatch"] = False
            continue

        geo_borough = borough_from_coords(lat, lon)
        card["geographic_borough"] = geo_borough

        # A mismatch is when we CAN determine both boroughs (they're both
        # non-None) and they disagree. A missing geo_borough means the
        # point is outside NYC (possibly a legitimate regional service,
        # e.g., a NJ hotline with NYC presence) — not a mismatch per se.
        stated = _stated_borough_from_city(card.get("city"))
        mismatch = (
            geo_borough is not None
            and stated is not None
            and geo_borough != stated
        )
        card["borough_mismatch"] = mismatch

        if mismatch:
            # Rate-limit: only warn the first time we see each service_id.
            # The card flag (borough_mismatch=True) is still set on every
            # response, so downstream code always sees the mismatch — only
            # the log is deduplicated.
            service_id = card.get("service_id")
            if service_id and service_id not in _WARNED_MISMATCH_SERVICE_IDS:
                _WARNED_MISMATCH_SERVICE_IDS.add(service_id)
                logger.warning(
                    "borough_mismatch service_id=%s service=%r city=%r "
                    "stated_borough=%s geographic_borough=%s lat=%s lon=%s",
                    service_id,
                    card.get("service_name"),
                    card.get("city"),
                    stated, geo_borough, lat, lon,
                )

    return cards


# The SQL ORDER BY already includes an open-now rank, but schedule data is
# sparse (~40-80% coverage for walk-in services, 0% for others). This
# Python-side stable sort guarantees "Open now" services float to the top
# of the final card list regardless of DB-level sort behavior.
#
# Sort priority: open (0) > closed (1) > unknown/no data (2)
# Within each group, the original SQL order (freshness, distance, name)
# is preserved because Python's sort is stable.

_OPEN_RANK = {"open": 0, "closed": 1}


def _sort_open_first(cards: list[dict]) -> list[dict]:
    """Sort service cards so 'Open now' appear first, preserving order otherwise."""
    return sorted(cards, key=lambda c: _OPEN_RANK.get(c.get("is_open"), 2))


# ---------------------------------------------------------------------------
# QUERY EXECUTION
# ---------------------------------------------------------------------------

def execute_service_query(
    template_key: str,
    user_params: dict,
    max_results: int = 10,
    allow_relaxed: bool = True,
) -> dict:
    """
    Execute a service query using a pre-defined template.

    Args:
        template_key:  Key from TEMPLATES (e.g. "food", "shelter", "clothing")
        user_params:   Slot values from the intake form, e.g.
                       {"city": "Brooklyn", "age": 17, "gender": "male"}
        max_results:   Maximum number of service cards to return.
        allow_relaxed: If True and the strict query returns 0 results,
                       automatically retry with relaxed filters.

    Returns:
        dict with keys:
            services       — list of formatted service card dicts
            result_count   — number of results
            template_used  — human-readable template name
            params_applied — the actual parameters bound to the query
            relaxed        — True if the relaxed fallback was used
            execution_ms   — query execution time in milliseconds
    """
    if template_key not in TEMPLATES:
        return {
            "services": [],
            "result_count": 0,
            "template_used": None,
            "params_applied": user_params,
            "relaxed": False,
            "execution_ms": 0,
            "freshness": {"fresh": 0, "total": 0, "total_with_date": 0},
            "error": f"Unknown template: {template_key}",
        }

    params = dict(user_params)
    params["max_results"] = max_results

    # --- Strict query ---
    sql, bound_params = build_query(template_key, params)

    timed_out = False
    start = time.monotonic()
    try:
        rows = _execute_sql(sql, bound_params)
    except QueryTimeoutError:
        # Proximity query timed out — skip straight to relaxed fallback.
        # Don't waste time processing 0 rows; go directly to the faster
        # borough-level query that strips lat/lon/radius.
        timed_out = True
        rows = []
    except DatabaseUnreachableError:
        # No relaxed-fallback retry: the second connect would hit the
        # same dead host and waste another connect_timeout window
        # (5s) before failing identically. Surface the unreachable
        # state immediately so the chatbot orchestrator can return a
        # truthful "service temporarily unavailable" message instead
        # of falsely reporting "no services found near you" — the
        # latter is a worse experience because it suggests the user
        # should adjust their query when in fact they should retry
        # in a minute.
        raise
    elapsed_ms = int((time.monotonic() - start) * 1000)

    results = deduplicate_results(rows)
    freshness = _compute_freshness(results)
    cards = [format_service_card(r) for r in results]
    cards = _annotate_geographic_borough(cards)
    cards = _sort_open_first(cards)

    if cards or not allow_relaxed:
        return {
            "services": cards,
            "result_count": len(cards),
            "template_used": TEMPLATES[template_key]["name"],
            "params_applied": bound_params,
            "relaxed": False,
            "execution_ms": elapsed_ms,
            "freshness": freshness,
        }

    # --- Relaxed fallback ---
    if timed_out:
        _has_proximity = "lat" in params and "lon" in params
        logger.warning(
            f"{'Proximity' if _has_proximity else 'Strict'} query for "
            f"'{template_key}' timed out after {elapsed_ms}ms. "
            f"Falling back to relaxed query (strips proximity filters)."
        )
    else:
        logger.info(
            f"Strict query for '{template_key}' returned 0 results. "
            f"Retrying with relaxed filters."
        )

    sql_relaxed, relaxed_params = build_relaxed_query(template_key, params)

    start = time.monotonic()
    try:
        rows_relaxed = _execute_sql(sql_relaxed, relaxed_params)
    except QueryTimeoutError:
        # Both strict AND relaxed timed out — DB is under heavy load.
        # Return empty results so the chatbot shows a no-results message
        # instead of the scarier "I'm having trouble connecting" error.
        logger.error(
            f"Relaxed query for '{template_key}' also timed out. "
            f"Database may be under heavy load."
        )
        rows_relaxed = []
    except DatabaseUnreachableError:
        # See the strict-query handler above for the rationale —
        # propagate so the orchestrator can show "service temporarily
        # unavailable" instead of an incorrect "no results."
        raise
    elapsed_ms += int((time.monotonic() - start) * 1000)

    results_relaxed = deduplicate_results(rows_relaxed)
    freshness = _compute_freshness(results_relaxed)
    cards_relaxed = [format_service_card(r) for r in results_relaxed]
    cards_relaxed = _annotate_geographic_borough(cards_relaxed)
    cards_relaxed = _sort_open_first(cards_relaxed)

    return {
        "services": cards_relaxed,
        "result_count": len(cards_relaxed),
        "template_used": TEMPLATES[template_key]["name"],
        "params_applied": relaxed_params,
        "relaxed": True,
        "proximity_timeout": timed_out,
        "execution_ms": elapsed_ms,
        "freshness": freshness,
    }


def _execute_sql(sql: str, params: dict) -> list[dict]:
    """
    Execute a parameterized SQL query and return rows as dicts.

    All SQL passed here MUST come from query_templates.py.
    This function never constructs SQL — it only executes it.

    Raises QueryTimeoutError when the statement_timeout is exceeded,
    so the caller can fall back to a faster query immediately.
    Other exceptions return [] for backward compatibility.
    """
    engine = _get_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            columns = list(result.keys())
            return [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception as e:
        error_str = str(e).lower()
        # Detect statement_timeout (psycopg2.errors.QueryCanceled).
        # Check the string because psycopg2 might not be importable in
        # all environments, and the exception is wrapped by SQLAlchemy.
        if "querycanceled" in type(e).__name__.lower() or \
           "statement timeout" in error_str or \
           "canceling statement" in error_str:
            logger.warning("Query timed out (statement_timeout exceeded)")
            logger.debug(f"SQL: {sql}")
            logger.debug(f"Params: {params}")
            raise QueryTimeoutError("Query exceeded statement_timeout") from e

        # Detect connection-level failures (host unreachable, TCP
        # connect timeout, DNS failure, refused). String-sniff for the
        # same reason as QueryCanceled — psycopg2's specific exception
        # types aren't always importable, and SQLAlchemy wraps them.
        # All five markers below have been observed in this app's
        # production logs; the OR-list catches the union of "could not
        # establish a working connection" cases.
        if any(marker in error_str for marker in (
            "could not connect to server",
            "connection refused",
            "operation timed out",
            "no route to host",
            "name or service not known",
            "could not translate host name",
        )):
            logger.error(f"Database unreachable: {e}")
            raise DatabaseUnreachableError(
                "Database host unreachable — check RDS status / network"
            ) from e

        logger.error(f"Query execution error: {e}")
        logger.debug(f"SQL: {sql}")
        logger.debug(f"Params: {params}")
        return []


# ---------------------------------------------------------------------------
# SLOT-TO-TEMPLATE MAPPING
# ---------------------------------------------------------------------------

# Maps the service_type values from slot_extraction_regex.py to template keys.
# This bridges the gap between what the user says and what template to run.

SLOT_SERVICE_TO_TEMPLATE = {
    # Food
    "food":          "food",

    # Shelter & Housing
    "shelter":       "shelter",
    "housing":       "shelter",

    # Housing Assistance — retired as a dedicated service type (April 2026
    # audit; YourPeer has no equivalent). Keywords now route to 'other';
    # this redirect stays so legacy callers producing the old key still
    # resolve to a valid template. Removal invariant enforced by
    # tests/unit/test_audit_regression.py::TestHousingAssistanceRemoval.
    "housing_assistance": "other",

    # Clothing
    "clothing":      "clothing",

    # Personal Care (showers, laundry, toiletries, haircuts)
    "personal_care": "personal_care",
    "shower":        "personal_care",

    # Health Care
    "medical":       "medical",
    "healthcare":    "medical",
    "health":        "medical",

    # Mental Health
    "mental_health": "mental_health",

    # Legal
    "legal":         "legal",

    # Employment
    "employment":    "employment",
    "job":           "employment",

    # Other Services
    "other":         "other",
    "benefits":      "other",
}


def resolve_template_key(service_type: str) -> Optional[str]:
    """
    Map a slot extractor service_type to a template key.

    Returns None if the service type is not recognized.
    """
    if not service_type:
        return None
    return SLOT_SERVICE_TO_TEMPLATE.get(service_type.lower())


# ---------------------------------------------------------------------------
# CITY / BOROUGH NORMALIZATION
# ---------------------------------------------------------------------------

# physical_addresses in prod has NO `borough` column. Borough-level searches
# run via FILTER_BY_CITY_IN_BOROUGH (pa.city = ANY(:city_list)), with the
# city_list derived below. This is not ideal — pa.city has inconsistent
# casing, typos, and some wrong-borough assignments — but it's the only
# column available. See docs/audits/BOUNDARY_AUDIT.md for follow-up plan to
# derive geographic borough from l.position against NYC DCP polygons.
#
# Note that the "Manhattan" → "Manhattan" mappings below look redundant but
# are used by get_borough_city_names (which does an ALIAS → primary city
# lookup via _BOROUGH_TO_PRIMARY_CITY to build the city_list).

NYC_LOCATION_ALIASES = {
    # Boroughs → canonical city value used in pa.city for that borough
    "manhattan":      "Manhattan",
    "brooklyn":       "Brooklyn",
    "queens":         "Queens",
    "bronx":          "Bronx",
    "the bronx":      "Bronx",
    "staten island":  "Staten Island",

    # Common neighborhood → borough mappings
    "harlem":         "New York",
    "east harlem":    "New York",
    "midtown":        "New York",
    "midtown east":   "New York",
    "midtown west":   "New York",
    "soho":           "New York",
    "east village":   "New York",
    "west village":   "New York",
    "chelsea":        "New York",
    "tribeca":        "New York",
    "lower east side":"New York",
    "upper west side":"New York",
    "upper east side":"New York",
    "washington heights": "New York",
    "inwood":         "New York",
    "hells kitchen":  "New York",
    "hell's kitchen": "New York",
    "kips bay":       "New York",
    "murray hill":    "New York",
    "gramercy":       "New York",
    "chinatown":      "New York",
    "little italy":   "New York",
    "financial district": "New York",
    "battery park":   "New York",
    "nolita":         "New York",
    "noho":           "New York",
    "times square":   "New York",
    "port authority":  "New York",
    "penn station":   "New York",
    "grand central":  "New York",

    "williamsburg":   "Brooklyn",
    "bushwick":       "Brooklyn",
    "bed-stuy":       "Brooklyn",
    "bedford-stuyvesant": "Brooklyn",
    "east new york":  "Brooklyn",
    "crown heights":  "Brooklyn",
    "flatbush":       "Brooklyn",
    "brownsville":    "Brooklyn",
    "sunset park":    "Brooklyn",
    "bay ridge":      "Brooklyn",
    "dumbo":          "Brooklyn",
    "red hook":       "Brooklyn",
    "park slope":     "Brooklyn",
    "prospect heights": "Brooklyn",
    "fort greene":    "Brooklyn",
    "cobble hill":    "Brooklyn",

    "astoria":        "Queens",
    "flushing":       "Queens",
    "jamaica":        "Queens",
    "long island city": "Queens",
    "jackson heights": "Queens",
    "far rockaway":   "Queens",
    "ridgewood":      "Queens",
    "woodside":       "Queens",
    "sunnyside":      "Queens",
    "corona":         "Queens",
    "elmhurst":       "Queens",

    "south bronx":    "Bronx",
    "mott haven":     "Bronx",
    "fordham":        "Bronx",
    "hunts point":    "Bronx",
    "morrisania":     "Bronx",
}

# Borough-level entries — these get the full neighborhood expansion.
_BOROUGH_KEYS = {
    "manhattan", "brooklyn", "queens", "bronx", "the bronx", "staten island",
}

# Maps canonical borough names to the primary city value used in pa.city for
# that borough. Since physical_addresses has no borough column, this is the
# central mapping that drives borough-level filtering via pa.city.
_BOROUGH_TO_PRIMARY_CITY = {
    "Manhattan":   "New York",
    "Brooklyn":    "Brooklyn",
    "Queens":      "Queens",
    "Bronx":       "Bronx",
    "Staten Island": "Staten Island",
}


def is_borough(raw_location: str) -> bool:
    """Check if a location string is a borough (vs a neighborhood)."""
    if not raw_location:
        return False
    return raw_location.lower().strip() in _BOROUGH_KEYS


def normalize_location(raw_location: str) -> str:
    """
    Normalize a user-provided location string to the DB city value used in
    pa.city. Boroughs return their primary city ("manhattan" → "Manhattan",
    "the bronx" → "Bronx"); neighborhoods return the containing borough's
    primary city ("harlem" → "New York").

    Used by callers to populate the `city` / `city_list` query params, which
    drive FILTER_BY_CITY / FILTER_BY_CITY_IN_BOROUGH (the only location
    filters that actually work — pa.borough does not exist in the DB).
    """
    if not raw_location:
        return raw_location
    return NYC_LOCATION_ALIASES.get(raw_location.lower().strip(), raw_location.strip())


# ---------------------------------------------------------------------------
# BOROUGH → CITY EXPANSION
# ---------------------------------------------------------------------------
# pa.city is the only borough-identifying column in the DB. Values are
# inconsistent: "New York" covers all Manhattan addresses, but outer
# boroughs use both the borough name ("Brooklyn") AND neighborhood names
# ("Williamsburg", "Astoria", "Far Rockaway", etc.). This builds a reverse
# map so a "Queens" search catches all Queens cities.

def _build_borough_to_cities() -> dict:
    """Build a reverse map: primary city value → all city values in that borough."""
    borough_cities = {}
    for alias, city in NYC_LOCATION_ALIASES.items():
        if city not in borough_cities:
            borough_cities[city] = {city}
        alias_city = alias.title()
        borough_cities[city].add(alias_city)
    return {k: sorted(v) for k, v in borough_cities.items()}


BOROUGH_TO_CITIES = _build_borough_to_cities()


def get_borough_city_names(borough: str) -> list[str]:
    """
    Given a canonical borough name (e.g. "Queens", "Manhattan"), return all
    city values that might appear in pa.city for that borough.

    This is the core of borough-level search: the returned list drives
    FILTER_BY_CITY_IN_BOROUGH (pa.city = ANY(:city_list)). The DB has no
    borough column, so pa.city is all we have.

    Returns a lowercased list for case-insensitive SQL ANY() matching.

    Example:
        get_borough_city_names("Queens")
        → ["astoria", "far rockaway", "flushing", "jackson heights",
           "jamaica", "long island city", "queens"]
    """
    # Translate canonical borough name to the primary city value used as the
    # key in BOROUGH_TO_CITIES (e.g. "Manhattan" → "New York")
    primary_city = _BOROUGH_TO_PRIMARY_CITY.get(borough, borough)
    cities = BOROUGH_TO_CITIES.get(primary_city, [primary_city])
    return [c.lower() for c in cities]


# ---------------------------------------------------------------------------
# NEIGHBORHOOD CENTER COORDINATES (for PostGIS proximity search)
# ---------------------------------------------------------------------------
# Approximate center points for NYC neighborhoods. Used with ST_DWithin
# to find services within a radius of the neighborhood center.
# Coordinates are (latitude, longitude).
#
# Boroughs are NOT included — they use city-level filtering instead.
# Only neighborhoods that need proximity-based narrowing are listed.

NEIGHBORHOOD_CENTERS = {
    # Manhattan
    "chelsea":           (40.7465, -74.0014),
    "east village":      (40.7265, -73.9815),
    "west village":      (40.7336, -74.0027),
    "harlem":            (40.8116, -73.9465),
    "east harlem":       (40.7957, -73.9425),
    "midtown":           (40.7549, -73.9840),
    "midtown east":      (40.7540, -73.9720),
    "midtown west":      (40.7590, -73.9900),
    "soho":              (40.7233, -73.9985),
    "tribeca":           (40.7163, -74.0086),
    "lower east side":   (40.7150, -73.9843),
    "upper west side":   (40.7870, -73.9754),
    "upper east side":   (40.7736, -73.9566),
    "washington heights": (40.8417, -73.9394),
    "inwood":            (40.8677, -73.9212),
    "hells kitchen":     (40.7638, -73.9918),
    "hell's kitchen":    (40.7638, -73.9918),
    "kips bay":          (40.7420, -73.9800),
    "murray hill":       (40.7488, -73.9775),
    "gramercy":          (40.7382, -73.9860),
    "chinatown":         (40.7158, -73.9970),
    "little italy":      (40.7191, -73.9973),
    "financial district": (40.7075, -74.0113),
    "battery park":      (40.7033, -74.0170),
    "nolita":            (40.7231, -73.9946),
    "noho":              (40.7265, -73.9927),
    "times square":      (40.7580, -73.9855),
    "port authority":    (40.7569, -73.9900),
    "penn station":      (40.7506, -73.9935),
    "grand central":     (40.7527, -73.9772),

    # Brooklyn
    "williamsburg":      (40.7081, -73.9571),
    "bushwick":          (40.6942, -73.9215),
    "bed-stuy":          (40.6872, -73.9418),
    "bedford-stuyvesant": (40.6872, -73.9418),
    "east new york":     (40.6590, -73.8759),
    "crown heights":     (40.6694, -73.9422),
    "flatbush":          (40.6524, -73.9596),
    "brownsville":       (40.6614, -73.9056),
    "sunset park":       (40.6454, -74.0134),
    "bay ridge":         (40.6348, -74.0287),
    "dumbo":             (40.7033, -73.9887),
    "red hook":          (40.6734, -74.0080),
    "park slope":        (40.6728, -73.9778),
    "prospect heights":  (40.6775, -73.9692),
    "fort greene":       (40.6891, -73.9742),
    "cobble hill":       (40.6860, -73.9957),

    # Queens
    "astoria":           (40.7723, -73.9196),
    "flushing":          (40.7654, -73.8318),
    "jamaica":           (40.7029, -73.7898),
    "long island city":  (40.7425, -73.9536),
    "jackson heights":   (40.7557, -73.8831),
    "far rockaway":      (40.5998, -73.7448),
    "ridgewood":         (40.7043, -73.9055),
    "woodside":          (40.7454, -73.9030),
    "sunnyside":         (40.7433, -73.9196),
    "corona":            (40.7470, -73.8602),
    "elmhurst":          (40.7360, -73.8780),

    # Bronx
    "south bronx":       (40.8185, -73.9182),
    "mott haven":        (40.8089, -73.9230),
    "fordham":           (40.8619, -73.8976),
    "hunts point":       (40.8094, -73.8814),
    "morrisania":        (40.8291, -73.9065),
}

# Default search radius for neighborhood proximity queries (in meters).
# ~1.6 km ≈ 1 mile — covers most NYC neighborhoods comfortably.
DEFAULT_NEIGHBORHOOD_RADIUS_METERS = 1600


def get_neighborhood_center(location: str) -> tuple[float, float] | None:
    """
    Look up the center coordinates for a neighborhood.

    Returns (latitude, longitude) or None if the location is a borough
    or not in the lookup table.
    """
    if not location:
        return None
    return NEIGHBORHOOD_CENTERS.get(location.lower().strip())


# ---------------------------------------------------------------------------
# SCHEDULE LOOKUP (Gap 15)
# ---------------------------------------------------------------------------

_SCHEDULE_FOR_DAY_SQL = """
SELECT
    hs.service_id::text,
    hs.opens_at,
    hs.closes_at
FROM holiday_schedules hs
WHERE hs.service_id = ANY(:service_ids)
  AND hs.weekday = :weekday
  AND LOWER(hs.occasion) = 'covid19'
ORDER BY hs.service_id, hs.opens_at
"""


def fetch_schedule_for_day(
    service_ids: list[str], weekday: int
) -> dict[str, list[dict]]:
    """Fetch schedule data for specific service IDs on a given weekday.

    Args:
        service_ids: List of service UUID strings.
        weekday: ISO day of week (1=Monday, 7=Sunday), matching
            PostgreSQL EXTRACT(ISODOW ...) used in holiday_schedules.

    Returns:
        Dict mapping service_id → list of {opens_at, closes_at} dicts.
        Services with no schedule data are omitted from the dict.
    """
    if not service_ids:
        return {}

    try:
        engine = _get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text(_SCHEDULE_FOR_DAY_SQL),
                {"service_ids": service_ids, "weekday": weekday},
            )
            rows = [dict(r._mapping) for r in result]

        schedule: dict[str, list[dict]] = {}
        for row in rows:
            sid = row["service_id"]
            schedule.setdefault(sid, []).append({
                "opens_at": row["opens_at"],
                "closes_at": row["closes_at"],
            })
        return schedule

    except Exception as e:
        logger.error(f"Schedule lookup failed: {e}")
        return {}
