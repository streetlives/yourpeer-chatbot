<!-- drift:ignore-file: this file documents drift patterns using example text; checker would otherwise match its own examples -->
# Docs Drift Checker

`scripts/check_docs.py` validates that facts hardcoded in markdown files still match the actual codebase. It can auto-fix test counts and exits with a non-zero code if drift is detected, so it can run in CI.

---

## Why This Exists

Documentation in this project contains hardcoded numbers, model IDs, env var names, and file paths that drift silently when the code changes. We found test counts wrong in three places (TESTING.md said 381, README.md said 379, actual was 444), a model ID that didn't exist in the API (`claude-sonnet-4-6-20260217` caused every message to show crisis resources), and references to `GEMINI_API_KEY` in docs months after Gemini was removed.

None of these were caught by tests. This script exists so they're caught before they reach users or confuse a new engineer reading the docs.

---

## What It Checks

**Core checks (always-on):**

| Check | What It Validates |
|---|---|
| **Test counts** | Total test count in TESTING.md and README.md, plus per-file counts (`### test_chatbot.py — 50 tests`), compared against AST-walked counts of `def test_*` and `async def test_*` — both module-level functions and class methods. |
| **Model IDs** | Model ID strings in README.md, DEPLOY.md, and CRISIS_DETECTION.md compared against the `*_MODEL` constants in `backend/app/llm/claude_client.py` (AST-extracted). |
| **Env vars** | Env vars documented in DEPLOY.md verified against `render.yaml`. Flags vars that are documented but not deployed, and catches references to removed vars (e.g. `GEMINI_API_KEY`). |
| **File references** | Full-path backtick refs like `` `tests/unit/test_foo.py` `` verified to exist on disk. Missing refs suggest same-stem packages when available (`chatbot.py` → `chatbot/`). |
| **Bare filenames** | Backticked bare filenames like `` `chatbot.py` `` (no path prefix) matched against the full `*.py` inventory across `backend/`, `tests/`, `scripts/`. Catches drift that full-path matching misses. |
| **Internal links** | Markdown links like `[text](../other.md)` verified to resolve. Errors (not warnings) — broken docs are user-facing failures. |
| **Line anchors** | Flags markdown links containing `#L<number>` — these break on any code edit. |
| **Prose line numbers** | Flags plain-text line-number refs in prose like `"~line 940"` or `"L1153"`. Code-block-aware (won't flag line numbers inside example blocks). |
| **Code block commands** | `python foo.py` / `pytest foo.py` inside fenced code blocks verified that the referenced file exists. |
| **Dependency versions** | Version numbers in SETUP.md / DEPLOY.md compared against `render.yaml`. |

**Source-of-truth checks (AST-driven):**

| Check | What It Validates |
|---|---|
| **Numeric constants** | Claims like "displays the first 10" cross-referenced against AST-extracted int constants (e.g. `_DISPLAY_PAGE_SIZE = 5`). Configured in `NUMERIC_CONSTANTS` — adding a tracked constant is a one-line change. |
| **Category counts** | Claims like "7 crisis categories" cross-referenced against `len(_CRISIS_CATEGORIES)`. Supports word-form captures ("Eight crisis categories"). Configured in `CATEGORY_COUNTS`. |
| **Cross-doc contradictions** | When two docs claim different values for the same thing ("7 categories" vs "8 categories"), flag both — even if the source-of-truth is deleted. |

**Drift-of-stale-docs checks (May 2026 additions):**

| Check | What It Validates |
|---|---|
| **Status banners** | Top-of-file `**Status:**` banners on docs in `docs/design/` and `docs/audits/`. Flags pre-ship phrases (`Approved for implementation`, `Draft for review`, `Approved`, `Ready to implement`, `Active remediation in progress`) — which routinely outlive the work they describe. To opt out, either flip the banner to a post-ship phrase (`Shipped`, `Resolved`, `Complete`, `Closed`, `Fixed`) or use `<!-- drift:ignore-file -->` for genuinely-still-pre-ship docs. |
| **Audit-ID closure** | Catches the `PHASE_AC_AFTERMATH.md` failure mode: code has a `# LLM-1: <resolution>` comment marking that an audit ID is closed, but the doc's quick-status table still lists `LLM-1` as Open. Scans backend/ comments for audit-ID-shaped tokens (`LLM-1`, `D-5`, `BUG-1`, etc.), filters out known false-positive prefixes (`HTTP-`, `RFC-`, `N-` for immigration-form names), and warns when the doc disagrees with code. Only fires on the strong signal direction (code says closed, doc says open) — the inverse is too noisy because closures often happen as deletions with no leftover comment. |
| **Inventory table counts** | Validates multi-row inventory tables in audit/design docs against doc-declared HTML directives. A row's description cell can carry `<!-- check:phrase-count: app.services.phrase_lists._EMOTIONAL_PHRASES -->` and the checker will resolve the dotted reference, compute `len()` (or `sum(len(v) for v in d.values())` for dict-of-lists like `SERVICE_KEYWORDS`), and compare against the row's count. Subtotal rows and rows with composite formats use `<!-- check:phrase-count: skip -->`. The mapping lives in the doc itself, so adding a new row means adding a directive next to it — no separate update to this script. |

**Quality-of-life checks:**

| Check | What It Validates |
|---|---|
| **Deprecated patterns** | Catches `LINK-TO-OUR-REPO`, `TODO:`, `FIXME:`, `XXX:` left in docs. Info-level. |

---

## Usage

**Report only** — show drift without changing anything:

```
python scripts/check_docs.py
```

**Auto-fix** — fix test counts automatically, report the rest:

```
python scripts/check_docs.py --fix
```

**Skip a specific check** (useful when iterating on one file):

```
python scripts/check_docs.py --skip=bare-filenames,prose-line-nums
```

**In CI** — exits 0 if clean, exits 1 if drift detected. Severity threshold defaults to warning; use `--severity=error` to allow warnings through.

```yaml
# Example GitHub Actions step
- name: Check docs drift
  run: python scripts/check_docs.py --github --severity=warning
```

`--github` emits GitHub Actions annotations (`::warning file=X,line=N::...`) so findings surface inline on PR review.

**JSON for custom CI integration:**

```
python scripts/check_docs.py --json
```

Emits `{scanned_files, scanned_refs, issues: [...]}`.

---

## Example Output

```
Checking docs against codebase (15 checks)...

--- test-count (1) ---
  WARNING  docs/TESTING.md  says 381 total tests, actual 444

--- category-count (1) ---
  WARNING  docs/CLAUDE.md:148  claims "6 emotion-specific responses" but len(_EMOTIONAL_RESPONSES) = 9 in backend/app/services/responses.py
         → Update to 9 emotion-specific responses

--- file-ref (1) ---
  WARNING  scripts/DB_AUDIT.md:136  references `backend/app/services/chatbot.py` which doesn't exist
         → Did you mean the package `backend/app/services/chatbot/`? (Phase 3 decomposition)

============================================================
Scanned 31 markdown files, 411 file refs, 15 checks
  3 warning(s)
============================================================
```

Each finding includes severity (ERROR / WARNING / INFO), file:line location, category tag, the drift detail, and a suggested fix when the check can infer one.

---

## What It Can Auto-Fix

Only test counts — these are purely mechanical (count functions, update the number). Model IDs, env vars, and file references require human judgment and are reported but not auto-fixed.

---

## When to Run

Run `check_docs.py` after any of these changes:

- Adding, removing, or renaming test functions
- Changing model constants in `claude_client.py`
- Adding or removing env vars from `render.yaml`
- Renaming or deleting source files referenced in docs
- Refactoring code that shifts line numbers (the checker will flag `#L` anchors)
- Changing Node.js or Python version requirements
- Adding or removing API routes

The script has no dependencies beyond the Python standard library and runs in under a second.

---

## Check coverage (April 2026 rewrite)

Six limitations were surfaced during the Phase 3 decomposition audit. All six are now closed in `scripts/check_docs.py`. Historical notes kept here for context.

### Gap 1 — bare-filename file references ✅ CLOSED

Originally the file-reference check matched only **full paths** starting with `backend/`, `tests/`, or `frontend/`. Bare refs like `` `chatbot.py` `` slipped through and were how the Phase 3 drift survived undetected across many docs. The new `check_bare_filenames()` builds an inventory of every `*.py` basename in `backend/`, `tests/`, and `scripts/`, then matches `` `foo.py` `` regexes against that inventory. Handler-directory awareness: when the bare file doesn't exist but a same-stem directory does (e.g. `chatbot.py` vs `chatbot/` package), the warning suggests the package.

### Gap 2 — test-count globbing is non-recursive ✅ CLOSED

Swapped `TESTS_DIR.glob("test_*.py")` for `TESTS_DIR.rglob("test_*.py")`. Additionally replaced the regex-based test counter (`^def test_`, which missed class-method tests) with an AST-based counter that walks both `ast.FunctionDef` and `ast.AsyncFunctionDef` nodes — catching sync tests, async tests, module-level tests, and class-method tests uniformly. Test counts now resolve accurately against `tests/unit/` and `tests/integration/`.

### Gap 3 — hardcoded numeric constants in prose ✅ CLOSED

Added `check_numeric_constants()` driven by a config list. Each entry specifies a constant name, its source file, and regex patterns for how the constant appears in docs. The check AST-parses the source to get the actual int value, then scans every markdown file for claims that don't match. Currently configured for `_DISPLAY_PAGE_SIZE`. Adding a new tracked constant is a single-entry edit in `NUMERIC_CONSTANTS`.

### Gap 4 — category / feature counts duplicated across docs ✅ CLOSED

Added `check_category_counts()` driven by a `CATEGORY_COUNTS` config list. Uses AST to extract `len(_CRISIS_CATEGORIES)`, `len(_EMOTIONAL_RESPONSES)`, etc. from the source, then cross-references doc claims. Supports both numeric ("8 crisis categories") and word-form ("Eight crisis categories") captures via an optional `word_numbers` map. **This check immediately caught 3 real drift items the April 2026 manual scan missed** — validation evidence that the gap-closing was worth doing.

### Gap 5 — fragile prose line-number refs ✅ CLOSED

Added `check_prose_line_numbers()`. Regex patterns catch `line 940`, `~line 940`, `at line 940`, and `L940` (3+ digits to avoid false positives on `line 5` in quoted text). Tracks code-block state via `\`\`\`` counting so it doesn't flag line numbers inside example blocks. Skips lines that look like table separators or markdown headers.

### Gap 6 — table-cell current-state references ✅ CLOSED (subsumed)

In practice this collapses into Gap 1 + Gap 5 + the line-number tracking added throughout. Every file-ref warning now reports `file:line` so a reviewer can quickly see that a `` `chatbot.py` `` ref lives in the "Location" cell of a table. Combined with handler-directory awareness (Gap 1), this covers the table-cell case without needing markdown-table parsing.

---

## Industry-practice additions

The April 2026 rewrite also adopted patterns from established linter/drift ecosystems. Each adds measurable value and came from a specific source practice:

| Addition | Source practice | What it enables |
|---|---|---|
| **Severity levels** (error/warning/info) | markdownlint, ruff, shellcheck | CI can gate on `--severity=error` and ignore warnings, or `--severity=warning` to block merges. Not all drift is equally bad. |
| **Drift-ignore comments** | lychee's `lychee-ignore`, markdownlint's `markdownlint-disable` | Self-referential files (audit catalogs, the drift checker's own doc) can opt out cleanly with `<!-- drift:ignore-file -->` or `<!-- drift:ignore -->`. Supports optional trailing rationale. |
| **JSON output** (`--json`) | ruff, eslint, shellcheck | CI integrations parsing text break on format changes. JSON is stable. |
| **GitHub annotations** (`--github`) | GitHub Actions native format | Errors surface as inline PR comments on the exact line — no digging through logs. |
| **Line numbers on every finding** | Universal in modern linters | `file:line` format lets editors jump directly to the drift. |
| **AST-based source-of-truth extractors** | Standard Python tooling practice | Regex on `^_CONSTANT = \[` breaks on reformatting; `ast.walk` survives all whitespace/comment changes. |
| **Handler-directory awareness** | git's "did you mean", rustc/clang suggestion diagnostics | When `chatbot.py` is missing but `chatbot/` exists, the warning says so — cuts investigation time in half. |
| **Cross-doc contradictions** | docs-as-code practice (DITA, Sphinx validation) | Flags when two docs claim different values for the same thing, even if the source-of-truth is deleted. Caught 2 real contradictions on first run. |
| **Summary stats** | pytest, ruff, mypy | "Scanned 31 files, 411 refs, 15 checks — 2 error(s), 23 warning(s)" makes coverage legible. |

---

## Deliberately not added

The rewrite stopped short of three features that were considered and rejected. Listed here so future contributors don't re-litigate the same decisions:

- **SARIF output** — GitHub Code Scanning accepts SARIF and surfaces findings in the Security tab. Rejected because GitHub Actions annotations (`::warning file=...`) cover the 90% case of "find the drift in the PR review UI," and SARIF's spec is complex enough to risk version-drift in the tool itself. Revisit if Code Scanning integration becomes a hard requirement.

- **URL liveness checks** — Verifying external URLs don't 404. Rejected because this is a fundamentally different concern (network I/O, rate-limiting, retries) and dedicated tools (`lychee`, `linkchecker`) do it better. Should be a separate CI step, not bundled here.

- **Incremental / git-aware mode** — Only check files changed since `origin/main`. Rejected because the full run takes under 2 seconds on this repo and the complexity (git plumbing, merge-base detection, `--all-files` override for CI) isn't worth it. Revisit if the repo grows significantly or full-run time exceeds 30 seconds.

---

## Ignore comments

Suppress false positives with HTML comments in markdown:

```markdown
<!-- drift:ignore-file -->                  (applies to entire file)
<!-- drift:ignore-file: rationale -->       (with explanation)
<!-- drift:ignore -->                       (applies to next non-blank line)
<!-- drift:ignore-next-line -->             (alias for the above)
<!-- drift:ignore: rationale -->            (inline, same-line suppression)
```

**When to use file-wide ignore:** catalog/audit docs that intentionally reference fixed drift (e.g., `DOCS_AUDIT_2026-04.md` cataloging stale paths by name), and historical audits with explicit Post-Phase-3 header notes explaining that body refs are preserved for period accuracy.

**When to use inline ignore:** a single point-in-time commit annotation or eval-run summary that was accurate when written and shouldn't be rewritten retroactively.

**When NOT to use ignore markers:** to silence real current-state drift. That's what the `--skip` flag (suppress a category entirely) or code fixes are for.

