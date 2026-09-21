# Developer entry points. See docs/quickstart-local.md.
#
#   make dev     build and start the local stack, wait until it is healthy, smoke-test it
#   make seed    development tenant, users, agents and sample data (idempotent)
#   make seed-cases  sample governed cases, investigated against the mock provider
#   make down   stop the stack (data volumes are kept)
#   make clean   stop the stack and delete its data volumes
#   make logs    follow the stack's logs
#   make ps      show service status
#   make test    unit + contract + integration tests (the same suites as CI)
#   make check   lint, types, security scans, schema validation, vendor denylist, licence headers
#   make e2e     browser end-to-end suite against the running stack
#   make e2e-decisions  the decision-grant end-to-end suite (needs the flag on)
#
# `make test` and `make check` run in the agenticorg-tools image (Python 3.12,
# as in CI), so Docker and make are all they need. RUNNER=local runs them with
# the host's $(PYTHON) instead, which then needs the ".[dev]" extras, pip-audit
# and gitleaks installed.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE ?= docker compose -f docker-compose.dev.yml
# Decision grants (PRD G-3) are opt-in: AGENTICORG_DEV_DECISION_GRANTS=true
# configures the auth service for them and starts the identity provider
# approvers sign in with, which is otherwise not started at all.
DECISIONS_PROFILE = $(if $(filter true,$(AGENTICORG_DEV_DECISION_GRANTS)),--profile decisions,)
# scripts/dev_stack_smoke.sh runs one check inside the api container through it.
export COMPOSE
RUNNER ?= docker
PYTHON ?= python
# Branch point for the checks that look only at this branch's changes.
BASE_REF ?= origin/main
# Extra arguments for every pytest run, e.g. PYTEST_ARGS="-x -k approvals".
PYTEST_ARGS ?=

UNIT_SUITES ?= tests/unit tests/security
CONTRACT_SUITES ?= tests/contract tests/connector_harness
INTEGRATION_SUITES ?= tests/integration tests/regression
COVERAGE_FLOOR ?= 55
# `make coverage-gate`: changed lines (diff-cover) and each new module.
DIFF_COVER_FLOOR ?= 75
NEW_MODULE_FLOOR ?= 75
# Extra coverage arguments for the integration run; `make test` appends it to
# the unit run's data so coverage.xml covers both.
INTEGRATION_COV_ARGS ?=
# One coverage source for the runs the gate reads. pyproject.toml's addopts add
# --cov=core --cov=api ...; together with --cov=. coverage.py writes some
# filenames relative to core/ or api/ instead of the repository root, which
# the new-module check cannot attribute reliably. -o addopts replaces them.
GATE_COV_ARGS = -o addopts=--basetemp=codex-pytest-basetemp --cov=. --cov-report=xml --cov-report=term
# Not gated for changed-line coverage: tests and test infrastructure.
DIFF_COVER_EXCLUDE ?= */tests/* */test_doubles/* */fixtures/* conftest.py

# Integration tests get their own database and Redis index on the stack's
# servers; the database is dropped and recreated at the start of every run.
TEST_DB_NAME ?= agenticorg_test
TEST_REDIS_DB ?= 15
TEST_SECRET_KEY ?= ci-test-secret-key-minimum-16

HOST_UID := $(shell id -u 2>/dev/null || echo 0)
HOST_GID := $(shell id -g 2>/dev/null || echo 0)

ifeq ($(RUNNER),docker)
TOOLS = $(COMPOSE) --profile tools run --rm --no-deps --user $(HOST_UID):$(HOST_GID) tools
PY = python
TEST_DB_HOST = postgres:5432
TEST_REDIS_HOST = redis:6379
SEED_TOOLS = $(COMPOSE) --profile tools run --rm --no-deps --user $(HOST_UID):$(HOST_GID) -e AGENTICORG_SEED_PASSWORD tools
else ifeq ($(RUNNER),local)
TOOLS =
SEED_TOOLS =
PY = $(PYTHON)
TEST_DB_HOST = 127.0.0.1:$(or $(AGENTICORG_DEV_POSTGRES_PORT),5432)
TEST_REDIS_HOST = 127.0.0.1:$(or $(AGENTICORG_DEV_REDIS_PORT),6379)
else
$(error RUNNER must be 'docker' or 'local', not '$(RUNNER)')
endif

TEST_ENV = env CI=true AGENTICORG_SECRET_KEY=$(TEST_SECRET_KEY)
INTEGRATION_ENV = $(TEST_ENV) \
	AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev@$(TEST_DB_HOST)/$(TEST_DB_NAME) \
	AGENTICORG_REDIS_URL=redis://$(TEST_REDIS_HOST)/$(TEST_REDIS_DB)

E2E_CONFIG ?= e2e/dev-stack.config.ts
# Where `make seed-cases` writes the case references the browser suite reads.
GOVERNED_CASES_SEED ?= ui/test-results/governed-cases-seed.json
E2E_ARGS ?=

# The stack's own database and the placeholder key from docker-compose.dev.yml.
DEV_DB_NAME ?= agenticorg
DEV_SECRET_KEY ?= agenticorg-dev-only-do-not-use-in-production

.PHONY: help dev seed seed-cases down clean logs ps \
	tools-image test test-unit test-contract test-integration test-db coverage-gate \
	check check-ruff check-mypy check-bandit check-secrets check-licence-headers check-schemas check-denylist check-pip-audit check-cross-loop-baseline check-ambient-redis-allowlist \
	e2e e2e-decisions

help:
	@echo "make dev     build and start the local stack and smoke-test it"
	@echo "make seed    development tenant, users, agents and sample data (needs make dev)"
	@echo "make seed-cases  sample governed cases for the approvals console (needs make seed)"
	@echo "make down    stop the stack (keeps data)"
	@echo "make clean   stop the stack and delete its data volumes"
	@echo "make logs    follow logs"
	@echo "make ps      service status"
	@echo "make test    unit + contract + integration tests (or test-unit, test-contract, test-integration)"
	@echo "make coverage-gate  after make test: 75% of changed lines and of each new module"
	@echo "make check   ruff, mypy, bandit, gitleaks, licence headers, schemas, vendor denylist, pip-audit"
	@echo "make e2e     Playwright suite against the running stack (needs make dev)"
	@echo "make e2e-decisions  decision-grant suite: a real approval on the issuer's approval page"

dev:
	$(COMPOSE) $(DECISIONS_PROFILE) build
	$(COMPOSE) $(DECISIONS_PROFILE) up -d --wait --wait-timeout 600
	bash scripts/dev_stack_smoke.sh

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down --volumes --remove-orphans

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

# Idempotent development data (scripts/seed_dev.py) in the running stack's
# database. When AGENTICORG_SEED_PASSWORD is set in your environment the
# seeded users can also sign in with email and that password.
seed: tools-image
	@SMOKE_ATTEMPTS=3 bash scripts/dev_stack_smoke.sh >/dev/null || \
		{ echo "make seed: the dev stack is not healthy; start it with 'make dev'" >&2; exit 1; }
	$(SEED_TOOLS) env AGENTICORG_ENV=development AGENTICORG_SECRET_KEY=$(DEV_SECRET_KEY) \
		AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev@$(TEST_DB_HOST)/$(DEV_DB_NAME) \
		$(PY) -m scripts.seed_dev

# Sample governed cases (scripts/seed_governed_cases.py) for the seeded tenant:
# turns governed_cases.enabled on, submits new cases from the mock provider's
# fixtures and runs the reference agents against the stack's mock provider and
# model stub. The summary names the new cases for the browser suite.
seed-cases: tools-image
	@SMOKE_ATTEMPTS=3 bash scripts/dev_stack_smoke.sh >/dev/null || \
		{ echo "make seed-cases: the dev stack is not healthy; start it with 'make dev'" >&2; exit 1; }
	$(TOOLS) env AGENTICORG_ENV=development AGENTICORG_SECRET_KEY=$(DEV_SECRET_KEY) \
		AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev@$(TEST_DB_HOST)/$(DEV_DB_NAME) \
		AGENTICORG_REDIS_URL=redis://$(TEST_REDIS_HOST)/0 \
		AGENTICORG_MOCK_PROVIDER_URL=http://mock-provider:8080 VLLM_BASE_URL=http://model-stub:8080 \
		$(PY) -m scripts.seed_governed_cases --output $(GOVERNED_CASES_SEED)

# ── Tests ────────────────────────────────────────────────────────────────────

tools-image:
ifeq ($(RUNNER),docker)
	$(COMPOSE) --profile tools build tools
endif

# Unit and contract suites in one run with coverage and the global floor, as
# the CI unit-tests job runs them; then the integration suites.
test: tools-image
	$(TOOLS) $(TEST_ENV) $(PY) -m pytest $(UNIT_SUITES) $(CONTRACT_SUITES) \
		$(GATE_COV_ARGS) --cov-fail-under=$(COVERAGE_FLOOR) $(PYTEST_ARGS)
	$(MAKE) --no-print-directory test-integration INTEGRATION_COV_ARGS="$(GATE_COV_ARGS) --cov-append"

test-unit: tools-image
	$(TOOLS) $(TEST_ENV) $(PY) -m pytest $(UNIT_SUITES) $(PYTEST_ARGS)

test-contract: tools-image
	$(TOOLS) $(TEST_ENV) $(PY) -m pytest $(CONTRACT_SUITES) $(PYTEST_ARGS)

test-db:
	$(COMPOSE) up -d --wait --wait-timeout 300 postgres redis
	$(TOOLS) $(INTEGRATION_ENV) $(PY) scripts/reset_test_database.py

test-integration: tools-image test-db
	$(TOOLS) $(INTEGRATION_ENV) $(PY) -m pytest $(INTEGRATION_SUITES) $(INTEGRATION_COV_ARGS) $(PYTEST_ARGS)

# Needs coverage.xml from `make test`. diff-cover gates the lines changed since
# BASE_REF; check_new_module_coverage.py gates every module added since then,
# including ones the tests never import.
coverage-gate: tools-image
	$(TOOLS) $(PY) -m diff_cover.diff_cover_tool coverage.xml --compare-branch="$(BASE_REF)" \
		--fail-under=$(DIFF_COVER_FLOOR) --exclude $(foreach p,$(DIFF_COVER_EXCLUDE),'$(p)')
	$(TOOLS) $(PY) scripts/check_new_module_coverage.py --coverage-xml coverage.xml \
		--base "$(BASE_REF)" --head HEAD --floor $(NEW_MODULE_FLOOR)

# ── Checks ───────────────────────────────────────────────────────────────────

check: check-ruff check-mypy check-bandit check-secrets check-licence-headers check-schemas check-denylist check-pip-audit check-cross-loop-baseline check-ambient-redis-allowlist
	@echo "make check: all checks passed"

check-cross-loop-baseline: tools-image
	$(TOOLS) $(PY) scripts/check_cross_loop_baseline.py --base "$(BASE_REF)"

check-ambient-redis-allowlist: tools-image
	$(TOOLS) $(PY) scripts/check_ambient_redis_allowlist.py --base "$(BASE_REF)"

check-ruff: tools-image
	$(TOOLS) $(PY) -m ruff check .

check-mypy: tools-image
	$(TOOLS) $(PY) -m mypy --ignore-missing-imports \
		--exclude '(codex-pytest-basetemp|codex-pytest-temp|^output/|^\.tmp/)' .

check-bandit: tools-image
	$(TOOLS) $(PY) -m bandit -r core/ connectors/ api/ auth/ -ll -q

# Commits on this branch since it left BASE_REF; an unresolvable base fails.
check-secrets: tools-image
	$(TOOLS) bash -eu -o pipefail -c 'base="$$(git merge-base "$(BASE_REF)" HEAD)"; bash scripts/scan-secrets.sh range "$$base" HEAD'

check-licence-headers: tools-image
	$(TOOLS) $(PY) scripts/check_license_headers.py --base "$(BASE_REF)" --head HEAD

check-schemas: tools-image
	$(TOOLS) $(PY) scripts/check_schemas.py

# Vendor names in this branch's lines, paths, commit messages and branch name.
check-denylist: tools-image
	$(TOOLS) $(PY) scripts/check_denylist.py scan --base "$(BASE_REF)" --head HEAD

# The project and both requirements files, with the reviewed exceptions in
# config/pip-audit-exceptions.toml.
check-pip-audit: tools-image
	$(TOOLS) $(PY) scripts/run_pip_audit.py

# ── Browser end-to-end ───────────────────────────────────────────────────────

# Runs in the official Playwright image on the stack's network against the
# console service, so the stack must already be up (make dev).
e2e:
	@SMOKE_ATTEMPTS=3 bash scripts/dev_stack_smoke.sh >/dev/null || \
		{ echo "make e2e: the dev stack is not healthy; start it with 'make dev'" >&2; exit 1; }
	$(COMPOSE) --profile e2e run --rm --no-deps -e HOST_UID=$(HOST_UID) -e HOST_GID=$(HOST_GID) \
		-e AGENTICORG_SEED_PASSWORD -e GOVERNED_CASES_SEED=$(GOVERNED_CASES_SEED) \
		e2e bash scripts/run_e2e.sh $(E2E_CONFIG) $(E2E_ARGS)

# The decision-grant suite (PRD G-3 / PRD 8.4 steps 5 and 6): the console, the
# API and a real approval taken in a browser on the Grantex auth service's own
# approval page. It needs the approver identity provider allow-listed (a
# service-administrator action) and the stack started with decision requests
# switched on:
#
#   AGENTICORG_DEV_DECISION_GRANTS=true AGENTICORG_DEV_GRANTEX_ADMIN_KEY=... \
#   AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex make dev seed seed-cases e2e-decisions
#
# The identity provider approvers sign in with is in the `decisions` compose
# profile, so `make dev` never starts it; this target does.
e2e-decisions: E2E_CONFIG = e2e/decision-grants.config.ts
e2e-decisions:
	@[ "$(AGENTICORG_DEV_DECISION_GRANTS)" = "true" ] || { echo \
		"make e2e-decisions: start the stack with AGENTICORG_DEV_DECISION_GRANTS=true" >&2; exit 1; }
	@SMOKE_ATTEMPTS=3 bash scripts/dev_stack_smoke.sh >/dev/null || \
		{ echo "make e2e-decisions: the dev stack is not healthy; start it with 'make dev'" >&2; exit 1; }
	$(COMPOSE) --profile decisions up -d --wait --wait-timeout 180 oidc-approvers
	$(COMPOSE) --profile e2e run --rm --no-deps -e HOST_UID=$(HOST_UID) -e HOST_GID=$(HOST_GID) \
		-e AGENTICORG_SEED_PASSWORD -e GOVERNED_CASES_SEED=$(GOVERNED_CASES_SEED) \
		e2e-decisions bash scripts/run_e2e.sh $(E2E_CONFIG) $(E2E_ARGS)
