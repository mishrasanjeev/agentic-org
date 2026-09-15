# Developer entry points. See docs/quickstart-local.md.
#
#   make dev     build and start the local stack, wait until it is healthy, smoke-test it
#   make seed    development tenant, users, agents and sample data (idempotent)
#   make down   stop the stack (data volumes are kept)
#   make clean   stop the stack and delete its data volumes
#   make logs    follow the stack's logs
#   make ps      show service status
#   make test    unit + contract + integration tests (the same suites as CI)
#   make check   lint, types, security scans, schema validation, vendor denylist, licence headers
#   make e2e     browser end-to-end suite against the running stack
#
# `make test` and `make check` run in the agenticorg-tools image (Python 3.12,
# as in CI), so Docker and make are all they need. RUNNER=local runs them with
# the host's $(PYTHON) instead, which then needs the ".[dev]" extras, pip-audit
# and gitleaks installed.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE ?= docker compose -f docker-compose.dev.yml
# scripts/dev_stack_smoke.sh runs one check inside the api container through it.
export COMPOSE
RUNNER ?= docker
PYTHON ?= python
# Branch point for the checks that look only at this branch's changes.
BASE_REF ?= origin/main
# Extra arguments for every pytest run, e.g. PYTEST_ARGS="-x -k approvals".
PYTEST_ARGS ?=

UNIT_SUITES ?= tests/unit tests/security tests/regression
CONTRACT_SUITES ?= tests/connector_harness
INTEGRATION_SUITES ?= tests/integration tests/regression
COVERAGE_FLOOR ?= 55
# `make coverage-gate`: changed lines (diff-cover) and each new module.
DIFF_COVER_FLOOR ?= 75
NEW_MODULE_FLOOR ?= 75
# Extra coverage arguments for the integration run; `make test` appends it to
# the unit run's data so coverage.xml covers both.
INTEGRATION_COV_ARGS ?=

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
E2E_ARGS ?=

# The stack's own database and the placeholder key from docker-compose.dev.yml.
DEV_DB_NAME ?= agenticorg
DEV_SECRET_KEY ?= agenticorg-dev-only-do-not-use-in-production

.PHONY: help dev seed down clean logs ps \
	tools-image test test-unit test-contract test-integration test-db coverage-gate \
	check check-ruff check-mypy check-bandit check-secrets check-licence-headers check-schemas check-denylist check-pip-audit \
	e2e

help:
	@echo "make dev     build and start the local stack and smoke-test it"
	@echo "make seed    development tenant, users, agents and sample data (needs make dev)"
	@echo "make down    stop the stack (keeps data)"
	@echo "make clean   stop the stack and delete its data volumes"
	@echo "make logs    follow logs"
	@echo "make ps      service status"
	@echo "make test    unit + contract + integration tests (or test-unit, test-contract, test-integration)"
	@echo "make coverage-gate  after make test: 75% of changed lines and of each new module"
	@echo "make check   ruff, mypy, bandit, gitleaks, licence headers, schemas, vendor denylist, pip-audit"
	@echo "make e2e     Playwright suite against the running stack (needs make dev)"

dev:
	$(COMPOSE) build
	$(COMPOSE) up -d --wait --wait-timeout 600
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

# ── Tests ────────────────────────────────────────────────────────────────────

tools-image:
ifeq ($(RUNNER),docker)
	$(COMPOSE) --profile tools build tools
endif

# Unit and contract suites in one run with coverage and the global floor, as
# the CI unit-tests job runs them; then the integration suites.
test: tools-image
	$(TOOLS) $(TEST_ENV) $(PY) -m pytest $(UNIT_SUITES) $(CONTRACT_SUITES) \
		--cov=. --cov-report=xml --cov-fail-under=$(COVERAGE_FLOOR) $(PYTEST_ARGS)
	$(MAKE) --no-print-directory test-integration INTEGRATION_COV_ARGS="--cov=. --cov-append --cov-report=xml"

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
		--fail-under=$(DIFF_COVER_FLOOR)
	$(TOOLS) $(PY) scripts/check_new_module_coverage.py --coverage-xml coverage.xml \
		--base "$(BASE_REF)" --head HEAD --floor $(NEW_MODULE_FLOOR)

# ── Checks ───────────────────────────────────────────────────────────────────

check: check-ruff check-mypy check-bandit check-secrets check-licence-headers check-schemas check-denylist check-pip-audit
	@echo "make check: all checks passed"

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
		e2e bash scripts/run_e2e.sh $(E2E_CONFIG) $(E2E_ARGS)
