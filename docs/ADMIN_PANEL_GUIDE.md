# Admin Panel User Guide

> The Staff Review Console (`/admin`) is the staff-facing view of the
> chatbot's operational and quality data. This guide documents what
> each tab shows, how to use it, and where the underlying signals come
> from. Source-grounded as of May 2026 — see "Where this is sourced
> from" at the end.

For setup, deployment, and the underlying chatbot mechanics, see
[SETUP.md](SETUP.md), [DEPLOY.md](DEPLOY.md), and
[CHATBOT_BEHAVIOR.md](CHATBOT_BEHAVIOR.md). For metric definitions and
targets, see [ops/METRICS.md](ops/METRICS.md). For the LLM-judge eval
rubric, see [EVALUATION_TESTING.md](EVALUATION_TESTING.md).

---

## Table of contents

- [Accessing the console](#accessing-the-console)
- [Layout and navigation](#layout-and-navigation)
- [Tab 1 — Overview](#tab-1--overview)
- [Tab 2 — Metrics](#tab-2--metrics)
- [Tab 3 — Conversations](#tab-3--conversations)
- [Tab 4 — Locations](#tab-4--locations)
- [Tab 5 — Query Log](#tab-5--query-log)
- [Tab 6 — Evals](#tab-6--evals)
- [Tab 7 — Model Analysis](#tab-7--model-analysis)
- [Privacy and data retention](#privacy-and-data-retention)
- [Common tasks](#common-tasks)
- [Where this is sourced from](#where-this-is-sourced-from)

---

## Accessing the console

**URL.** `/admin` on the deployed Next.js frontend. The bare `/admin`
path is a server-side redirect to `/admin/overview`, the canonical
landing page (`src/app/admin/overview/page.tsx`). The redirect itself
lives in `src/app/admin/page.tsx` — a one-line `redirect()` call from
`next/navigation` that short-circuits the response before any HTML
is sent, so the browser lands on `/admin/overview` directly.

**Sign in.** Single password, set server-side as the `ADMIN_API_KEY`
environment variable on the backend. Enter it on the login form; on
success a session cookie is set and you're redirected into the
console.

**Brute-force protection.** Failed login attempts are rate-limited to
5 per IP per 15 minutes. Successful logins don't consume quota.

**Open in dev mode.** If `ADMIN_API_KEY` is not configured on the
backend, the admin endpoints are open (fine for local dev; the
backend logs a warning on startup). Production deployments must set
`ADMIN_API_KEY` — see [DEPLOY.md](DEPLOY.md) for the env-var
checklist.

**Sign out.** The header has a Sign out button next to the theme
toggle.

**Theme.** The theme toggle (system → light → dark → system) sits in
the header and is shared with the chat UI — a preference set in
either surface persists across both.

---

## Layout and navigation

The console is a Next.js App Router layout: a fixed header with the
product name and account controls, a 7-tab nav strip, and the active
tab's content below. The 7 tabs in order
(`src/components/admin/admin-nav.tsx`):

1. **Overview** — single-screen "is everything OK?" landing
2. **Metrics** — 9-section deep dashboard with targets
3. **Conversations** — anonymized session list with transcript viewer
4. **Locations** — Streetlives catalog quality and coverage
5. **Query Log** — every service search executed
6. **Evals** — LLM-as-judge evaluation suite
7. **Model Analysis** — per-task model assignment and cost calculator

The active tab is underlined in amber; the inactive ones are muted.
Each tab is a separate route — you can deep-link directly to a tab and
bookmark sections within Metrics and Locations.

**Data freshness.** Admin pages share a centralized client-side store
with 30-second staleness caching. Navigating between tabs reuses
cached data rather than re-fetching on every navigation. If you need
the latest, refresh the page.

**Loading and error states.** Every panel has its own loading
skeleton, error message, and empty state. A partial backend failure
(e.g. events fetch fails but stats fetch succeeds) keeps the working
panel visible — you don't lose the whole page over one bad call.

---

## Tab 1 — Overview

The landing page after sign-in. Single screen, top to bottom:

### System Health card

First thing on the page. Real-time status of the backend's
dependencies, polled from `/api/health` with a 5-second timeout. Three
indicators:

- **Database** — green if the DB is reachable within a 15-second
  statement timeout, red otherwise
- **LLM** — green if Anthropic API responds to a minimal Haiku ping
  (the ping result is cached for 90 seconds to avoid hammering the
  upstream)
- **Semantic router** — green if the embedding model is loaded and the
  route tables are populated

Plus uptime in days/hours/minutes.

**Why it's at the top.** If a backend is unhealthy, every metric below
it is suspect — surfacing health first means you notice degradation
before you start interpreting numbers it produced.

### Headline stat cards

Six cards in a responsive grid. Each shows a value, a target, and a
color (green / amber / red) based on the thresholds shipped in
`src/app/admin/overview/page.tsx`:

| Card | Target | Definition |
|---|---|---|
| Sessions | — | Unique sessions in the audit log |
| Task Completion | ≥ 80% | % of service-intent sessions that reached a confirmed query |
| Avg Turns to Result | ≤ 5 | Average turns from session start to first result |
| Confirmation Confirm Rate | ≥ 75% | % of confirmation prompts the user agreed with |
| User Feedback | ≥ 70% | % of post-response thumbs-up out of total thumbs |
| No-Result Rate | ≤ 15% | % of queries returning zero services |

The Confirm Rate card replaces a legacy "Crises Detected" count
because a bare crisis count is uninformative without context (you
can't have a "high" or "low" crisis count out of context). The
per-category crisis breakdown lives in the Crisis Activity panel
of the Operations block below.

### Operations block

Section header "Operations" with the subtitle: *When and where users
need help, session engagement patterns, and post-results behavior.
Informs peer navigator staffing and database coverage priorities.*
Below that, five inline widgets render in a responsive 2-up grid
(driven entirely by `AdminStats` from `get_stats()` — no charting
library, all Tailwind primitives). Each widget owns its own
empty-state copy so a brand-new deploy with no traffic still reads
sensibly:

- **When (Hourly Traffic)** — 24-bar histogram of conversation turns
  by hour of day, anchored to ET (so midnight ET is the leftmost
  bar even though the backend buckets by UTC). The peak hour is
  highlighted in solid amber for one-glance staffing reads;
  subtitle shows total events and the peak hour
- **Where (Top Locations)** — ranked horizontal bars of user-stated
  locations by query volume, with no-result rate as a trailing
  secondary signal. Locations with elevated no-result rates are
  color-coded (amber 25–50%, red ≥ 50%) so underserved areas are
  the visually loudest rows
- **How Long (Session Duration)** — vertical bars over 5 buckets
  (<1m, 1–3m, 3–7m, 7–15m, 15m+). The 3–7m bucket is the
  peer-navigator handoff target range; a footer row surfaces the
  share of multi-turn sessions currently in that band
- **Engagement** — paired view in one card. Top: turn-count
  distribution (1 / 2–3 / 4–6 / 7–10 / 11+ turns). Bottom: the
  post-results follow-up rate (% of result-receiving sessions that
  asked a question after results were shown). The pairing separates
  "users got an answer and left" from "users couldn't get what they
  wanted" from "users dug in deeply"
- **Crisis Activity** — full-width block with its own section header
  and two side-by-side panels: **Last 24 Hours** (rolling, anchored
  to now) and **All-Time** (cumulative). Each panel shows a
  per-category breakdown (domestic violence, suicide/self-harm,
  medical emergency, trafficking, etc.) as horizontal bars sorted
  by count, falling back to a green "✓ No crises detected…"
  sentinel when the breakdown is empty. The 24h panel has a footer
  pointer to the Recent Activity feed below for drilling into
  individual events. Replaces the older single-panel
  "Crisis Categories widget" — comparing 24h shape against the
  cumulative shape is the operational signal

### Recent Activity feed

The last 20 audit events: sign-ins, queries, crisis detections,
feedback events, session resets, etc. Each row shows timestamp (as
relative time, full timestamp on hover), event type with a colored
badge, a one-line detail, and a session-id prefix.

---

## Tab 2 — Metrics

The deep dashboard. 9 collapsible sections; sections 1–4 are open by
default, 5–9 are closed (click to expand).

**Sticky nav strip.** At the top of the Metrics tab is a chip row with
one chip per section. Each chip has a status dot:

- **Red** — at least one off-target metric in that section
- **Amber** — at least one warning, no off-target
- **Green** — at least one on-target metric, no warnings or
  off-targets (the section is measurably passing)
- **Gray** — only no-data and/or tracking-only metrics. Tracking rows
  are informational with no target, and no-data rows aren't
  measurable yet, so neither earns a green dot on its own — there's
  no measurable signal to read either way

The active chip (the section currently in view) gets a filled
background — driven by an IntersectionObserver as you scroll. Click a
chip to jump to that section. The dot color cascades through the
worst-case metric status in the section: any off-target wins (red);
otherwise any warning wins (amber); otherwise any on-target wins
(green); otherwise gray. So a red dot means something in that
section needs attention, and a green dot means everything measurable
in that section is currently passing.

**"Show only issues" toggle.** Filters every section down to just the
metrics currently flagged red or amber. Useful when triaging.

**Click any metric name** to open a Radix dialog with the metric's
full definition, formula, target, and rationale — sourced verbatim
from `docs/ops/METRICS.md`.

The 9 sections:

| # | Title | What's in it |
|---|---|---|
| 1 | Is It Working? | Task Completion Rate, Session Abandonment Rate, No-Result Rate, User Feedback Score, Escalation Rate, Median Turns. The headline "is the bot completing its job?" view. |
| 2 | Are the Results Good? | Median Results per Query, Result Diversity, Eligibility Fit Rate (canary), Median Wait Time to First Result. Quality of search results. Informs query template tuning, DB coverage, multi-intent handling. |
| 3 | Is It Safe? | Crisis Detection Rate, Crisis False Positive Rate, PII Leakage Rate, Hallucination Rate. Must be 100% on crisis detection — any miss could leave a vulnerable person without resources. |
| 4 | How Does It Feel? | Emotional acknowledgment rate, tone classification distribution, frustration detection, bot repetition rate. For this population, even routine interactions carry emotional weight. |
| 5 | Intake & Confirmation Flow | Slot fill rates per slot (service_type, location, age, gender, family_status), confirmation funnel (asked → confirmed → executed), disambiguation rate. |
| 6 | System Internals | Routing distribution (6 buckets: service flow, conversational, post-results, emotional, safety, recovery, general), confidence distribution (high/medium/low), correction & disambiguation rates, LLM call metrics (calls, cost, p50/p95 latency, failure rate). |
| 7 | Operations | Peak hour (ET), hourly distribution, geographic demand (top locations, location no-result rates), bounce rate, median turns per session, median session duration, post-results engagement. |
| 8 | Eval Targets — LLM-as-Judge | 11-dimension scoring with targets — see [Tab 6](#tab-6--evals) for the run flow. Each dimension's target lives in `src/lib/admin/eval-dimensions.ts` (single source of truth, shared with Tab 6). |
| 9 | Closed-Loop Outcomes — Post-Pilot | Referral Success Rate, Service Accuracy Rate, Outcome Linkage. Reserved for post-pilot data — currently no-data. |

**Color logic.** Each metric row shows a colored value plus a status
badge (On target / Warning / Off target / Tracking / No data). Color
thresholds and the row-level status logic live in
`src/components/admin/metric-row.tsx`; the per-section status
aggregation (which feeds the sticky-nav dots) lives in
`src/lib/admin/metrics-page-helpers.tsx`.

**Tracking-only rows.** Some metrics show a count with a "Tracking"
badge rather than a target comparison. These are intentionally
exploratory — once enough data accumulates, the team adds a target
and the row gates against it.

---

## Tab 3 — Conversations

Anonymized list of every session. The table shows:

| Column | Definition |
|---|---|
| Session | First 8 chars of the session id |
| Turns | Number of user-bot turn pairs |
| Outcome | `services` (search executed), `no_results`, or `crisis` (a crisis was detected this session) |
| Slots | The final slot values from the session (service_type, location, age, gender, family_status) — truncated with full value on hover |
| Last Active | Relative time of the most recent turn |

**Sorting.** Every column except Slots is sortable (Session, Turns,
Outcome, and Last Active). Click a header to toggle
ascending/descending. Default sort: Last Active descending.

**Filters.** Above the table:

- **Search box** — matches against session-id and any slot value
- **Outcome buttons** — All / Results / No Results / Crisis
- **Min turns** — slider to focus on multi-turn sessions

**Pagination.** Standard page-size selector (10/25/50/100) and
page navigation at the bottom. Filters reset the page to 1.

**Click any row** to open the transcript viewer (Radix dialog,
keyboard-accessible). The viewer shows:

- Every turn in chronological order, timestamped
- The user message (PII-redacted)
- The bot response
- The slot diff for that turn — what changed (added / overwritten /
  removed). Helps trace how the bot's understanding evolved
- The routing decision (service_flow / conversational / etc.) and
  confidence (high / medium / low / disambiguated)
- Crisis flags inline with crisis turns
- Per-location feedback events (safety / friendliness / cleanliness /
  queer-friendly) inline next to the response that surfaced the
  location

PII redaction happens before storage — see
[design/PII_REDACTION.md](design/PII_REDACTION.md). The console never
shows raw user PII.

---

## Tab 4 — Locations

Operational overview of the Streetlives location catalog. 12 panels
across 11 spec'd sections — covers what data exists, how fresh it is,
and what users are flagging. Each panel fetches independently
(`/api/admin/locations/*` endpoints) and owns its own loading / error
/ empty state.

The full per-component description lives in
`frontend-next/src/components/admin/locations/README.md`. Display
order:

1. **Top stat strip** — total locations, total services, freshness
   p50, oldest location, never-verified count, feedback-event count
2. **Data freshness histogram** — locations bucketed by age since
   last validation (<30d / 30–90 / 90–180 / 180–365 / >365 / never).
   Click a bar to filter the triage table at the bottom of the page
   to that bucket
3. **Locations by borough** — count + percentage per borough
4. **Service categories by borough** — heatmap. Cell color shows
   location count, anchored to the brightest cell across the matrix.
   Top 10 categories by default; expand toggle reveals the rest. Use
   this to spot equity gaps — a borough with no entries (`—`) for a
   service type is a coverage hole
5. **Stale categories** — taxonomies where every offering location is
   >180 days old. A single fresh location would clear the alert
6. **Service-type coverage** — per-taxonomy supply (services +
   locations) paired with chat demand. Default sort: highest
   demand-to-supply ratio first — "where should we focus partner
   outreach this quarter?"
7. **Coordinate validation** — locations whose lat/lon doesn't match
   their declared city. Outside-NYC issues are typo'd coordinates;
   borough-mismatch issues need manual verification
8. **Location feedback** — per-criterion baseline (safety, friendliness,
   cleanliness, queer-friendly) across all events, plus a
   "most-flagged locations" ranking. Locations need at least 2
   feedback events to qualify for the ranking
9. **Recent feedback comments** — qualitative stream. Comments often
   surface things that don't fit any criterion checkbox. Click a row
   to open the session transcript for context
10. **Data integrity** — catalog-wide health checks: orphaned records,
    malformed phone formats, encoded HTML in descriptions, rollup of
    coordinate issues. Each fires only when there's something to fix
    (positive ✓ empty state when nothing's wrong)
11. **Activity over time** — locations added / verified / feedback
    events by week, last 26 weeks. The shape of the curves is the
    signal: steady cadence vs. trending up vs. recent spike each tell
    a different story
12. **Locations needing review** — the triage table. Default view
    shows least-recently-verified locations first (never-verified at
    the top). Filters: borough, age bucket, data-quality issues.
    Clicking a freshness histogram bar above drives this table's age
    filter

**Cross-reference contract.** Borough labels, age buckets, feedback
criteria, and integrity callout IDs are shared between frontend and
backend by name. `scripts/verify/locations-contract.mjs` runs in CI to
catch drift. If you add a new criterion or bucket on the backend, run
`npm run verify:locations-contract`.

---

## Tab 5 — Query Log

Every service search the chatbot executed. Sortable, paginated table:

| Column | Definition |
|---|---|
| Time | Relative timestamp (absolute on hover) |
| Template | Which query template ran (shelter, food, clothing, medical, legal, employment, mental_health, personal_care, other, org_name) |
| Params | The bound SQL params — location, taxonomy_names, age, gender, family_status, etc. — truncated |
| Results | Service count returned |
| Duration | Execution time in ms |
| Issues | Two badges: red "timeout" if a proximity query exceeded the DB statement timeout and was retried with a relaxed query, amber "relaxed" if the query was relaxed (filters dropped, broader area) to recover from zero results |

**Click any row** to open the query detail drawer: full param dump,
result count, relaxed flag, execution time, and originating session
id. Useful when investigating a specific user-reported issue.

The "Issues" column sorts on "any issue" — a row with a proximity
timeout but no relaxed-fallback flag still surfaces when you sort by
issues, because the column is decorated with a synthetic `has_issue`
boolean for that purpose.

---

## Tab 6 — Evals

Runs the 11-dimension LLM-as-judge evaluation suite and renders the
results. Two top-of-page controls:

### Run Evals button

Triggers a fresh eval run as a backend FastAPI background task. Three
companion controls sit next to it:

- **Scenario count selector** — All scenarios (default), 5 (quick), 25,
  50, 100
- **Upload Report button** — load a previously-generated
  `eval_report.json` from disk (useful when CI ran the eval and
  produced a report you want to view in the UI; client-side validation
  caps the file at 10 MB)
- **Stop watching** — appears while a run is in progress. Stops the
  client-side status polling. The eval may continue running on the
  backend; this just stops the spinner

Clicking Run Evals opens a confirmation dialog with a cost warning:

> Each scenario makes multiple Anthropic API calls (Haiku for
> conversation, Sonnet for user simulation, Opus for judging across
> 11 dimensions). A full run typically costs $15–25 in API credits
> and takes 30–60 minutes. The backend will be under heavier load
> during the run.

The button labels are intentionally inverted: **Cancel is the
prominent amber button**, "Yes, run" is the muted text button. The
eval is destructive (real cost, long wall-clock) so the default
action is the safe one.

**Rate limit.** Eval runs are rate-limited to 5 per IP per hour at
the backend to protect API credit budgets.

**Status polling.** Once a run starts, the page polls
`/admin/api/eval/status` every 2.5 seconds. Hard safety caps:

- 1800 attempts ≈ 75 minutes (full eval is 30–60 min, plenty of
  headroom) protects against a backend stuck reporting `running:true`
- 5 consecutive failures ≈ 12.5 seconds of network silence protects
  against the dashboard losing the backend mid-run

Success/failure is determined by `finished_at` on the status payload —
the backend only sets it when the eval subprocess exited 0 AND wrote
a report file.

### Run report

Once a run completes (or after upload), five summary cards render
above the per-dimension breakdown:

| Card | Definition |
|---|---|
| Overall Score | Weighted average across all 11 dimensions, all scenarios. Green ≥ 4, amber ≥ 3, red below |
| Scenarios | Count of scenarios evaluated this run |
| Passing Rate | % of scenarios scoring ≥ 4.0 with no error. Green ≥ 95%, amber ≥ 85%, red below |
| Critical Failures | Scenarios that hit a hard-fail rubric clause (e.g. crisis missed, PII leaked) |
| Eval Errors | Scenarios where the eval pipeline itself errored (judge timeout, malformed response, etc.) |

Each card is a navigation link to the relevant section below.

### Dimension scores

11 rows, each showing the average score for one dimension across all
scenarios. Click a dimension name to see what the judge measures and
how it scores 1–5 (the rubric is duplicated verbatim from the Python
judge in `tests/eval/eval_llm_judge.py` into
`src/lib/admin/eval-dimensions.ts`).

The 11 dimensions:

1. **slot_extraction** — did the bot correctly parse the user's intent?
2. **dialog_efficiency** — did it ask only what it needed, in a
   reasonable number of turns?
3. **response_tone** — did the language feel right for this user?
4. **safety_crisis** — did crisis handling fire when it should have,
   not when it shouldn't?
5. **confirmation_ux** — were confirmation prompts clear and not
   over-eager?
6. **privacy** — no PII leakage, no false reassurance about confidentiality
7. **hallucination_resistance** — did the bot make up services / hours
   / phones / addresses?
8. **error_recovery** — when something went wrong, did the bot recover
   gracefully?
9. **dignity_anti_stigma** — did the bot avoid language that lands as
   judgment or condescension?
10. **cultural_responsiveness** — did it work for the specific
    population the scenario tests?
11. **equity_of_access** — did response quality stay consistent across
    populations?

Per-dimension targets are tuned to the post-R28 (Opus judge) era. Six
were tightened in May 2026 based on 15 runs of historical data; three
(response_tone, dignity_anti_stigma, cultural_responsiveness) are
deliberately left aspirational because lowering them would erase a
real call-to-action. The rationale lives in the comment header of
`src/lib/admin/eval-dimensions.ts`.

### Critical failures

If any scenario hit a hard-fail clause this run, it's listed here with
the failing dimension, score, and a link to scenario details.

### Category averages

The 184 scenarios are organized into 20 categories (crisis, multi-intent,
edge cases, etc.). This panel shows weighted average per category so
you can spot a category-level regression.

### Scenario details

Per-scenario expand/collapse. Each scenario shows its average score
(color-coded), per-dimension scores with target markers (highlighting
sub-target rows with the judge's justification surfaced), the
simulated user prompts, the bot's responses, and the judge's reasoning
per dimension.

Filter pills above the list: All / Failures (score < 4.0 or errored) /
category-name-with-count. The category pills are sorted by frequency
(most-populated first).

---

## Tab 7 — Model Analysis

Per-task Claude model assignments, costs, and the rationale. All
pricing pulls from [Anthropic's official docs](https://docs.anthropic.com/en/about-claude/pricing).

### Model comparison

Three cards at the top: Haiku 4.5, Sonnet 4.6, Opus 4.6. Each shows
input/output token price, typical-latency band, and the kinds of
tasks it's best suited for.

### Per-task recommendations

Every LLM-powered task in the chatbot, listed with its current model
assignment, alternative options, and the recommendation rationale.
Task list (from `src/components/admin/model-data.ts`):

- **Conversational fallback** — when no template matches, the bot
  replies via a free-text LLM call
- **Slot extraction (tool calling)** — primary slot-fill path when the
  regex layer doesn't resolve
- **Unified classification gate** — routing classifier (service vs.
  conversational vs. emotional, etc.)
- **Crisis detection** — second-stage LLM check after the regex layer
- **Emotional acknowledgment** — generating the empathy prefix on
  emotionally charged turns
- **Bot capability questions** — handling "what can you do?" /
  "are you human?" / etc.
- **LLM-as-Judge evaluation** — used in the Evals tab
- **Future: multi-language** — placeholder for Spanish/etc. when that
  ships

Each task row shows the current model, a "switch to X" recommendation
with the cost/quality trade-off, and any jury validation steps needed
before flipping.

### Monthly cost calculator

Interactive form. Pick a configuration preset:

- **Recommended** — the current production assignment
- **All-Haiku** — cheapest, but quality regression risk on judge and
  emotional acknowledgment
- **All-Sonnet** — middle ground
- **Sonnet-heavy** — Sonnet for everything but the judge

Adjust the projected monthly traffic (sessions × avg turns) and the
calculator returns the monthly Anthropic spend per task and the
configuration total. Includes a post-results savings note showing
zero-LLM-cost optimization on cached service-card answers.

### Sources & methodology

Collapsed expander at the bottom. Lists the pricing pages, the eval
runs that informed each recommendation, and the methodology for the
cost calculator's traffic projections.

> The recommendations are preliminary. Run the eval suite (Tab 6) to
> validate before deploying a model-assignment change.

---

## Privacy and data retention

- **User messages are PII-redacted before storage.** Names, phone
  numbers, addresses, emails, and ID numbers are detected and
  replaced server-side. See [design/PII_REDACTION.md](design/PII_REDACTION.md)
- **The console never shows raw PII.** Transcripts in the
  Conversations tab show the redacted form
- **In-memory by default.** Without `PILOT_DB_PATH` set, the audit log
  and session store live in memory and reset on each backend deploy.
  Set `PILOT_DB_PATH=data/pilot.db` to persist across deploys for
  pilot testing
- **Admin endpoints require the API key.** Every `/admin/api/*`
  endpoint requires `Authorization: Bearer <ADMIN_API_KEY>` when the
  key is configured. The Next.js proxy at `/api/admin/[...slug]`
  forwards the same env-var as the Bearer token so admin pages never
  see the key in the browser
- **Per-IP rate limits on admin endpoints.** 120/min, 600/hr globally;
  5/hr on eval runs specifically

---

## Common tasks

### "Did the bot miss a crisis today?"
Overview → System Health (confirm backend is healthy) → look at the
Crisis Activity block in the Operations block, starting with the
**Last 24 Hours** panel for what's been flagged recently. Then
Conversations tab → filter Outcome to Crisis to see the sessions where
crisis was flagged, and the Query Log for any session that ended
without a result.

### "Why is the no-result rate up?"
Metrics tab → Section 1 — confirms it's up. Then Section 7 —
geographic demand panel shows which boroughs are returning
no-result; the no-result-by-service operations chart shows which
categories. Cross-check with Locations tab → Service-type coverage
to see if the categories with high no-result also have low supply.

### "Triage a user-reported bug"
Conversations tab → search by partial session-id → open the
transcript. Look at slot diffs and routing decisions to see how the
bot interpreted each turn. Cross-reference with Query Log if the
session ran a search.

### "Run an eval after shipping a change"
Evals tab → Run Evals → All scenarios (or a smaller subset for a
quick spot-check) → confirm in the cost dialog. Run takes 30–60 min;
you can leave the tab and come back. After it completes, compare the
overall score and the per-dimension scores against the previous run
recorded in `docs/ops/EVAL_RESULTS_R28-R42.md`.

### "Plan partner outreach this quarter"
Locations tab → Service-type coverage. Default sort is
demand-to-supply ratio descending. The top rows are categories where
users keep asking and supply is thin — those are the partnership
priorities.

### "Investigate a slow query"
Query Log tab → sort by Duration descending. Look for outliers. Open
the detail drawer for the parameters and result count. If the "Issues"
column shows a timeout badge, the query exceeded the DB statement
timeout and was retried with a relaxed fallback — visible in the
drawer as `relaxed: true`.

### "Check if a model change paid off"
Model Analysis tab → run the cost calculator with the new
configuration. Then Evals tab → run the suite → compare per-dimension
scores to the pre-change run.

### "Identify a stale location to re-verify"
Locations tab → Data freshness histogram → click the bar for the age
bucket you want (typically `>365` or `never`). That filters the
triage table at the bottom of the page. Sort by category and walk the
list.

---

## Where this is sourced from

This guide is grounded in the current admin code as of May 2026. The
authoritative sources for each section:

| Section | File |
|---|---|
| Tab structure | `frontend-next/src/components/admin/admin-nav.tsx` |
| Layout / header / theme toggle | `frontend-next/src/app/admin/layout.tsx` |
| Auth flow | `frontend-next/src/components/admin/admin-auth-guard.tsx`, `login-form.tsx`, `logout-button.tsx`, `src/app/api/admin/[...slug]/route.ts` |
| Overview tab | `frontend-next/src/app/admin/overview/page.tsx`, `system-health.tsx`, `operations-charts.tsx`, `event-feed.tsx` |
| Metrics tab structure | `frontend-next/src/app/admin/metrics/page.tsx`, `metrics-section.tsx`, `metric-row.tsx`, `metrics-sticky-nav.tsx` |
| Metric definitions / targets | `docs/ops/METRICS.md` (single source of truth for definitions; rendered into Metrics tab dialogs) |
| Conversations tab | `frontend-next/src/components/admin/conversation-table.tsx`, `transcript-drawer.tsx` |
| Locations tab | `frontend-next/src/app/admin/locations/page.tsx`, `src/components/admin/locations/` (12 components — see the directory's `README.md` for the section map) |
| Query Log tab | `frontend-next/src/components/admin/query-log-table.tsx`, `query-detail-drawer.tsx` |
| Evals tab | `frontend-next/src/app/admin/evals/page.tsx`, `eval-runner.tsx`, `eval-results.tsx`, `src/lib/admin/eval-dimensions.ts` |
| Model Analysis tab | `frontend-next/src/components/admin/model-analysis.tsx`, `model-data.ts`, `model-card.tsx`, `task-row.tsx`, `cost-calculator.tsx` |
| Auth config | `backend/app/main.py` (`ADMIN_API_KEY` env var, login rate-limit), `docs/DEPLOY.md` (env-var checklist) |
| PII redaction policy | `docs/design/PII_REDACTION.md` |
| Crisis detection design | `docs/design/CRISIS_DETECTION.md` |

If any of these source files change in a way that affects this guide,
update this doc.
