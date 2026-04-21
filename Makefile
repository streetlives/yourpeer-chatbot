.PHONY: help test test-quick coverage coverage-branch audit audit-baseline mutation mutation-module mutation-report clean-mutation

help:
	@echo "Setup:"
	@echo "  make setup            # Install test dependencies into active venv"
	@echo ""
	@echo "Test & quality targets:"
	@echo "  make test             # Run full test suite"
	@echo "Test & quality targets:"
	@echo "  make test             # Run full test suite"
	@echo "  make test-quick       # Fast subset (unit tests only)"
	@echo "  make coverage         # Run tests with coverage, term report"
	@echo "  make coverage-branch  # As above + branch coverage"
	@echo "  make audit            # Static test-quality audit (all categories)"
	@echo "  make audit-baseline   # Regenerate the audit baseline file"
	@echo "  make mutation         # Mutation test all critical modules (~hours)"
	@echo "  make mutation-module MODULE=backend/app/services/crisis_detector.py"
	@echo "  make mutation-report  # Summarize the latest mutation run"
	@echo "  make clean-mutation   # Remove cr.sqlite and mutation configs"

setup:
	@# Install everything the test + quality tooling needs.
	@# Assumes you're already inside a virtualenv — if not, create one first:
	@#     python3 -m venv backend/venv && source backend/venv/bin/activate
	pip install -r backend/requirements.txt
	pip install pytest pytest-cov pytest-mock pytest-asyncio pytest-randomly cosmic-ray
	@echo ""
	@echo "Setup complete. Test toolchain installed in $$(which python3)"

# Env vars the app requires at import time. These are fake values safe for
# CI/local — the test suite mocks anything that would actually touch a
# real service. Override in your shell if needed, e.g. for hitting a
# local Postgres instance.
export SESSION_SECRET ?= test-session-secret-local
export ADMIN_API_KEY ?= test-admin-key-local
export DATABASE_URL ?= postgresql://test:test@localhost/test_local

# Internal — verify pytest is installed before running tests.
_check_pytest:
	@python3 -c "import pytest" 2>/dev/null || (echo "❌ pytest not installed. Run: make setup" && exit 1)

test: _check_pytest
	python3 -m pytest tests/ -v

test-quick:
	python3 -m pytest tests/unit/ -x --tb=short -q

coverage: _check_pytest
	python3 -m pytest tests/ \
	    --cov=backend/app \
	    --cov-report=term-missing:skip-covered \
	    -p no:cacheprovider

coverage-branch:
	python3 -m pytest tests/ \
	    --cov=backend/app \
	    --cov-branch \
	    --cov-report=term-missing:skip-covered \
	    --cov-report=html:htmlcov \
	    -p no:cacheprovider
	@echo ""
	@echo "Branch coverage HTML report at: htmlcov/index.html"

audit:
	python3 tests/_tools/audit_tests.py

audit-baseline:
	python3 tests/_tools/audit_tests.py --summary > tests/_tools/audit_baseline.txt
	@echo "Regenerated tests/_tools/audit_baseline.txt"
	@cat tests/_tools/audit_baseline.txt

# Mutation testing — expensive. Use mutation-module during development,
# reserve `make mutation` for weekly / nightly runs.
CRITICAL_MODULES := \
    backend/app/services/crisis_detector.py \
    backend/app/services/classifier.py \
    backend/app/privacy/pii_redactor.py \
    backend/app/services/chatbot/orchestrator.py \
    backend/app/services/session_token.py

mutation:
	@echo "Running mutation testing on $(words $(CRITICAL_MODULES)) critical modules."
	@echo "This will take multiple hours. Interrupt with Ctrl-C and resume later."
	@for mod in $(CRITICAL_MODULES); do \
	    $(MAKE) mutation-module MODULE=$$mod; \
	done

# Single-module mutation run. The test subset is chosen per-module via
# the same map used by CI. Add new modules here and in
# .github/workflows/mutation-testing.yml in tandem.
mutation-module:
ifndef MODULE
	$(error MODULE is required, e.g. make mutation-module MODULE=backend/app/services/classifier.py)
endif
	@echo "Running mutation testing on $(MODULE)"
	@# Per-module focused tests. If you add a module, append its row.
	@case "$(MODULE)" in \
	    backend/app/services/crisis_detector.py) \
	        TESTS="tests/unit/test_phrase_audit.py tests/unit/test_contraction_normalization.py tests/unit/test_frustration_and_crisis.py" ;; \
	    backend/app/services/classifier.py) \
	        TESTS="tests/integration/test_classification_and_routing.py tests/unit/test_contraction_normalization.py" ;; \
	    backend/app/privacy/pii_redactor.py) \
	        TESTS="tests/unit/test_pii_redactor.py" ;; \
	    backend/app/services/chatbot/orchestrator.py) \
	        TESTS="tests/integration/test_classification_and_routing.py tests/integration/test_multi_turn_and_context.py" ;; \
	    backend/app/services/session_token.py) \
	        TESTS="tests/unit/test_session_store.py tests/unit/test_main.py" ;; \
	    *) \
	        echo "Unknown module $(MODULE) — add it to the Makefile case block"; \
	        exit 2 ;; \
	esac; \
	SAFE=$$(echo $(MODULE) | tr '/' '-'); \
	printf '[cosmic-ray]\nmodule-path = "%s"\ntimeout = 60\nexcluded-modules = []\ntest-command = "python -m pytest %s -x --tb=no -q"\n[cosmic-ray.distributor]\nname = "local"\n[cosmic-ray.interceptors]\nenabled = ["spor", "pragma_no_mutate", "operators-filter"]\n' "$(MODULE)" "$$TESTS" > cosmic-ray.toml; \
	rm -f "cr-$$SAFE.sqlite"; \
	cosmic-ray init cosmic-ray.toml "cr-$$SAFE.sqlite"; \
	cosmic-ray --verbosity WARNING baseline cosmic-ray.toml; \
	cosmic-ray exec cosmic-ray.toml "cr-$$SAFE.sqlite"; \
	cr-report "cr-$$SAFE.sqlite" | tail -20

mutation-report:
	@echo "Per-module mutation scores:"
	@for db in cr-*.sqlite; do \
	    [ -f "$$db" ] || continue; \
	    mod=$$(echo $$db | sed 's/cr-//; s/\.sqlite//; s/-/\//g'); \
	    score=$$(python3 -c "\
import sqlite3; \
con = sqlite3.connect('$$db'); \
killed = con.execute(\"SELECT COUNT(*) FROM work_results WHERE test_outcome='KILLED'\").fetchone()[0]; \
survived = con.execute(\"SELECT COUNT(*) FROM work_results WHERE test_outcome='SURVIVED'\").fetchone()[0]; \
total = killed + survived; \
print(f'{100 * killed / total:.1f}% ({killed}/{total})' if total else 'no data') \
"); \
	    printf "  %-60s %s\n" "$$mod" "$$score"; \
	done

clean-mutation:
	rm -f cr-*.sqlite cosmic-ray.toml
