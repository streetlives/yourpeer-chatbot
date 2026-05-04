# Test Infrastructure Guide

This document covers how to use the test-quality and mutation-testing
tooling added to the project. Audience: any engineer working on this
codebase.

## Contents

1. [Running tests locally](#running-tests-locally)
2. [Coverage gates](#coverage-gates)
3. [The audit tool](#the-audit-tool)
4. [Mutation testing](#mutation-testing)
5. [The codemod for patch targets](#the-codemod-for-patch-targets)
6. [CI workflows](#ci-workflows)
7. [How to interpret a mutation score](#how-to-interpret-a-mutation-score)
8. [When to use `# pragma: no mutate`](#when-to-use-pragma-no-mutate)
9. [Adding a new critical module](#adding-a-new-critical-module)
10. [Regenerating the audit baseline](#regenerating-the-audit-baseline)

---

## Running tests locally

The short version:

```bash
make test              # full suite
make test-quick        # unit only (fast)
make coverage          # with line-coverage report
make coverage-branch   # + branch coverage, HTML report
```

If you're not on the Makefile path (e.g. running directly in an IDE):

```bash
# Env vars the app needs at import time. Fake values — nothing real.
export SESSION_SECRET="test"
export ADMIN_API_KEY="test"
export DATABASE_URL="postgresql://test:test@localhost/test"
unset ANTHROPIC_API_KEY  # omit unless testing live-LLM paths

python3 -m pytest tests/
```

**A note on `ANTHROPIC_API_KEY`:** leaving it unset is the norm. A few
tests marked with `@_skip_no_api_key` hit the real Anthropic API — those
skip when the key is absent. They are gated to avoid running in CI by
default.

---

## Coverage gates

The CI gate (in `test-quality.yml`) is:

- **Line coverage ≥ 85%** (CI gate; the `--cov-fail-under=85` flag is set in `.github/workflows/test-quality.yml`). Actual coverage typically hovers a few points above the gate; raise the gate when you confirm a comfortable margin.
- **Branch coverage** is measured but not yet gated. Will be gated at
  75% once we confirm it's above that with the current suite.

Per-module targets:

| Module type | Target |
|---|---|
| Safety-critical (crisis, PII, session) | 95%+ |
| Core dispatch (classifier, orchestrator) | 90%+ |
| Handlers | 85%+ |
| Main / bootstrap / admin routes | 75%+ |

To find modules below target locally:

```bash
make coverage | grep -E "^backend.*[0-6][0-9]%|^backend.*7[0-4]%"
```

---

## The audit tool

`tests/_tools/audit_tests.py` scans every test file for known
anti-patterns. It does NOT run the tests; the check is a few seconds.

```bash
make audit                        # full report, every category
python3 tests/_tools/audit_tests.py --summary   # counts only
python3 tests/_tools/audit_tests.py --category D2   # one category
```

Categories it checks:

| Code | Problem |
|---|---|
| D1 | Patch target doesn't exist as an attribute of the named module |
| D2 | Test function has no assertions at all |
| D4 | Mocks configured with return_value / side_effect but nothing verified |
| D5 | Test reads os.environ without using monkeypatch / patch.dict |
| D6 | Admin endpoint hit without auth header or 401 expectation |
| D7 | Patches `app.services.chatbot.X` at the package level when the runtime uses a submodule binding |
| D8 | Real-time comparisons on fields like `age` / `elapsed` / `expired` without `freeze_time` |
| D9 | Uses `time.sleep()` — flaky in CI |

The **CI gate** (`check_audit_baseline.py`) compares the current
findings against `tests/_tools/audit_baseline.txt`. The build fails if
any category's count INCREASES. Decreases are allowed silently but
should prompt a baseline regenerate.

**Current baseline (`tests/_tools/audit_baseline.txt`, regenerated post-April 2026 cleanup):**

| Category | Baseline count | In CI gate? | Notes |
|---|---|---|---|
| D1 Dead patch targets | 1 | ✅ Yes | Edge case — single legacy site retained as a documented exception |
| D2 No-assertion tests | 0 | ✅ Yes | All assertionless stubs replaced |
| D3 Mock-only assertions | 27 | ❌ Advisory | High false-positive rate; surfaced for human review but never gates the build |
| D4 Unverified mock return_value | 0 | ✅ Yes | Clean |
| D5 Env-dependent | 8 | ✅ Yes | Deliberate (tests that exercise env-var handling) |
| D6 Admin without auth | 0 | ✅ Yes | All admin tests carry `Authorization` header |
| D7 Package-level re-export patches | 0 | ✅ Yes | Codemod applied, 130 rewrites across 21 files |
| D8 Real-time comparisons | 1 | ✅ Yes | `test_ping_cache_expires` — legitimate |
| D9 `time.sleep()` | 1 | ✅ Yes | `test_audit_log` — flaky risk, accepted |
| **TOTAL (scan)** | **38** | — | What `audit_tests.py` reports against 77 scanned files |
| **TOTAL (gated)** | **11** | ✅ | What `check_audit_baseline.py` actually fails the build on (excludes D3) |

The build only fails when a **gated** category increases above its baseline. D3 is reported but never gates — its detection has a high false-positive rate (tests that deliberately verify a mock contract look identical to tests that forgot to verify behavior). Pre-cleanup the scan total was around 203 findings.

The actual current scan may report slightly higher totals than the baseline (most often in D3, since that's not in the gate) as new tests are added — that's fine. If a *gated* category drifts up legitimately (e.g., you added 5 new tests with deliberate env-var handling and they're each justified), regenerate the baseline deliberately:

```bash
make audit-baseline   # writes tests/_tools/audit_baseline.txt
git add tests/_tools/audit_baseline.txt
```

---

## Mutation testing

Mutation testing verifies that tests would catch bugs, not just that
they execute code. We use **cosmic-ray** (not mutmut — mutmut v3's
copy-to-`mutants/` model fought our project layout; cosmic-ray patches
in-place which just works).

### When to run it

- **Per-PR automatically**: if the PR touches one of the critical modules
  (see below), `mutation-testing-pr.yml` runs cosmic-ray on just that
  file. Fast — usually under 10 min.
- **Weekly automatically**: `mutation-testing.yml` runs every Sunday at
  03:00 UTC against all critical modules. Fails the workflow and
  opens a tracking issue if any module drops below its threshold.
- **Manually during development**: use `make mutation-module` when you
  want to check a specific module's test quality.

### Critical modules

These get mutation testing. Listed in priority order:

1. `backend/app/services/crisis_detector.py` — safety-critical
2. `backend/app/services/classifier.py` — routes everything; bugs silently misroute users
3. `backend/app/privacy/pii_redactor.py` — privacy-critical
4. `backend/app/services/chatbot/orchestrator.py` — main dispatch
5. `backend/app/services/session_token.py` — security-adjacent

### Per-module thresholds

| Module | Threshold | Why |
|---|---|---|
| crisis_detector.py | 50% | Has LLM-only paths that can't be mutation-tested without live API calls. Real mutants in testable code should score ~85%. |
| classifier.py | 70% | Pure logic; no excuse for much lower |
| pii_redactor.py | 85% | Pure regex logic; should be high |
| orchestrator.py | 70% | Dispatch logic; some paths are hard to isolate |
| session_token.py | 85% | Security; should be high |

Thresholds are enforced in the two CI workflows that need to fail builds:

- `.github/workflows/mutation-testing.yml` — for the weekly job
- `.github/workflows/mutation-testing-pr.yml` — for the PR job

Both define a `THRESHOLD_FOR_MODULE` associative array; the values must match. The `Makefile`'s `mutation-module` target runs cosmic-ray and prints the report but doesn't enforce a threshold itself — local runs are advisory.

### Running locally

```bash
# Run one module — takes 15-60 minutes depending on mutant count.
make mutation-module MODULE=backend/app/services/crisis_detector.py

# See the summary across all modules you've run:
make mutation-report

# Clean up:
make clean-mutation
```

Cosmic-ray stores state in `cr-<module>.sqlite` files. If a run is
interrupted, the next `cosmic-ray exec` picks up where it left off.

### Interpreting results

See [How to interpret a mutation score](#how-to-interpret-a-mutation-score) below.

---

## The codemod for patch targets

`tests/_tools/fix_patch_targets.py` rewrites the three function names
whose package-level patches are silent no-ops to their live bind
sites:

- `app.services.chatbot.claude_reply` → `app.services.chatbot.handlers.meta.claude_reply`
- `app.services.chatbot.detect_crisis` → `app.services.chatbot.orchestrator.detect_crisis`
- `app.services.chatbot._USE_LLM` → `app.services.chatbot.orchestrator._USE_LLM`

Note that `detect_crisis` has a second live bind site at
`app.services.classifier.detect_crisis` (reached via `_classify_tone`).
The codemod rewrites to the orchestrator target; tests that need
classifier-path coverage add a second patch manually. The
`conftest.py` helpers already do this for you.

This has been applied (130 rewrites across 21 test files; zero D7
findings remain). The codemod is idempotent — safe to re-run on a
clean tree. If a new test is added with an old target, the D7 audit
check catches it before merge.

```bash
# Dry run:
python3 tests/_tools/fix_patch_targets.py

# Apply:
python3 tests/_tools/fix_patch_targets.py --apply
```

The underlying rule (from the Python mocking literature): **patch
where the function is looked up, not where it's defined.** If module A
does `from B import foo`, patching `B.foo` does not affect `A.foo` —
you must patch `A.foo`.

---

## CI workflows

Three test-related workflows in `.github/workflows/` gate test quality (the directory also contains workflows for ruff, frontend checks, backend tests, and doc drift, which are out of scope here):

### `test-quality.yml` — every PR and push to main

Runs in ~5 minutes. Gates:

1. Audit baseline check (seconds)
2. Full test suite with coverage
3. Line coverage ≥ 85%
4. Branch coverage measured (not yet gated; will be 75% when enabled)

Test order is randomized via `pytest-randomly` to catch hidden
test-order dependencies. Coverage XML is uploaded as an artifact so
you can debug failures.

### `mutation-testing-pr.yml` — PR touching a critical module

Skipped entirely if the PR doesn't change any critical module. When
it does run, mutates ONLY the changed file(s) and runs the focused
test subset. Typically 5-15 minutes.

This is the "Google model" from Petrović & Ivanković, TSE 2021:
incremental mutation on changed code during review, rather than full-
suite mutation that's intractable at scale.

### `mutation-testing.yml` — weekly on Sunday 03:00 UTC

Parallel matrix over all 5 critical modules. ~60 minutes per module
but runs in parallel. If any module drops below threshold:

1. The workflow fails.
2. A tracking issue is auto-filed with label `test-quality`.
3. The per-module cr.sqlite is uploaded as an artifact for 30 days.

To trigger manually: Actions → "Mutation Testing (Weekly)" → Run workflow.

---

## How to interpret a mutation score

### Short version

- **≥ 85%**: strong. Your assertions would catch most regressions.
- **70-85%**: acceptable for most modules. Investigate surviving mutants as time permits.
- **50-70%**: below industry baseline. Look at the top 10 surviving mutants.
- **< 50%**: weak. Tests are probably exercising code without asserting on its behavior.

### Longer version

A mutation score alone isn't meaningful — you need to look at *which*
mutants survived and *where*. Some classes of surviving mutants are
acceptable; others are tests crying out for stronger assertions.

**Acceptable survivors:**

- Mutations inside API-call boundaries (like `_detect_crisis_llm`
  making a real Anthropic request). These can't be killed without
  live calls, and you don't want live calls in CI. Mark with
  `# pragma: no mutate` if they dominate a module's score.
- Mutations to log messages. `logger.info("User X did Y")` vs.
  `logger.info("User X did Y!")` is a mutation that survives and
  it's fine — we don't test log text.
- Mutations to error message content inside `raise ValueError("...")`.
  Same reason.

**Actionable survivors:**

- Boolean flip (`True` → `False`, `is` → `is not`) — if this survives,
  no test differentiates the two return values. The test is effectively
  not checking what was returned.
- Comparison operator changes (`<` → `<=`, `==` → `!=`) — if this
  survives, no test exercises the boundary. Add parametrized tests
  at the edge.
- Default argument value changes — if `def f(x, y=5)` → `y=4` survives,
  no test calls `f(x)` with the default and checks the result.
- Number replacements (e.g., `price * 0.95` → `price * 1.0`). If these
  survive in a discount calculator, you have no test for the discount.

### The crisis_detector.py example

On initial run, crisis_detector.py scored:

- Raw: **48.5%** (17 survived of 33 normal-outcome mutants)
- Adjusted (excluding untestable LLM path): **84.2%** (3 survived of 19)

The 3 actionable survivors:

1. detect_crisis() signature: `skip_llm: bool = False` — `False`→`True` survived.
   No test called `detect_crisis(text)` with default args and
   asserted the LLM path was exercised.
2. detect_crisis() LLM branch: `return _detect_crisis_llm(text)` — `AddNot` survived.
   Same gap: no test verifies this return value.
3. is_crisis(): `return detect_crisis(text) is not None` — `is not`→`is`
   survived. `is_crisis()` has no test distinguishing crisis from None.

These are each one-line test fixes. The 50% threshold for this module
assumes the 14 LLM-path survivors are accepted; once those are marked
with `# pragma: no mutate`, the threshold should move up to 85%.

---

## When to use `# pragma: no mutate`

Cosmic-ray respects `# pragma: no mutate` as a line-level exclusion.
Use it for:

- **LLM-API call bodies**: the arithmetic inside a prompt payload,
  token counts, timeout values. These can't be mutation-tested without
  real API calls.
- **Log message construction**: `logger.info(f"...{value}...")`. We
  don't test log contents.
- **Error messages in raises**: same reason.
- **Default config values** that are parameterized at deploy time.

DO NOT use `# pragma: no mutate` to suppress an actionable surviving
mutant. If a Boolean flip survives, the test is weak, not the code.
Fix the test.

---

## Adding a new critical module

When a new file crosses the safety / security / core-dispatch bar and
needs mutation testing:

1. **Add to the Makefile case block** (`mutation-module` target). Pick
   a focused test subset that exercises the module's public API.
2. **Add to `.github/workflows/mutation-testing.yml`** matrix and to
   the `TESTS_FOR_MODULE` map in `mutation-testing-pr.yml`.
3. **Add to `.github/workflows/mutation-testing-pr.yml`** `paths:` list
   so PRs touching it trigger the per-PR mutation run.
4. **Add the path and a threshold** to both workflows' threshold dicts.

Start with a low threshold (e.g., 50%) and walk it up as you strengthen
tests. Ratcheting is easier than setting a high bar and discovering
you can't meet it.

---

## Regenerating the audit baseline

The audit baseline at `tests/_tools/audit_baseline.txt` represents
the set of accepted findings. It's a ceiling, not a floor — new
findings fail the build; removed findings are silent wins.

Regenerate when:

- You've deliberately cleaned up old findings. Ratcheting the baseline
  downward means future regressions beyond that point fail CI too.
- You've accepted a new finding that's genuinely okay. Document why
  in the baseline file's comment block.

```bash
make audit-baseline
git diff tests/_tools/audit_baseline.txt   # sanity-check the change
git commit -m "audit: regenerate baseline after cleaning up D2 findings"
```
