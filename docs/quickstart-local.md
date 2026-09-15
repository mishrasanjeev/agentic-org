# Local quickstart

Run AgenticOrg on your own machine with one command. You need **Docker**
(Compose v2.20 or newer) and **GNU make**. No accounts, API keys or model
credentials are required.

```bash
git clone https://github.com/mishrasanjeev/agentic-org.git
cd agentic-org
make dev
```

`make dev` builds the images, starts the stack, waits until every service is
healthy and runs a smoke test. The first build downloads base images and
Python and Node dependencies and takes several minutes; later runs reuse the
Docker build cache.

When it finishes:

| What | Where |
|---|---|
| Console | <http://127.0.0.1:3000> |
| API | <http://127.0.0.1:8000> (`/api/v1/health`, `/docs`) |
| Postgres | `127.0.0.1:5432`, database/user `agenticorg`, password `agenticorg_dev` |
| Redis | `127.0.0.1:6379` |
| OIDC stub | <http://127.0.0.1:9400> (`/.well-known/openid-configuration`) |

All ports bind to `127.0.0.1` only.

## What runs

`docker-compose.dev.yml` starts:

- **postgres** (pgvector 16) and **redis** — data kept in named volumes
- **minio** — object storage for uploaded documents (internal only)
- **migrate** — applies Alembic migrations once, then exits; the API and
  worker wait for it
- **api** — the FastAPI application
- **worker** — the Celery worker for workflows, reports and delivery
- **ui** — the console, served by nginx, proxying `/api` and `/ws` to the API
- **oidc-stub** — a development OpenID Connect provider with step-up; see
  [Development identity provider](#development-identity-provider)

Every base image is pinned by digest. The API and worker set
`AGENTICORG_TEST_FAKE_LLM=1` (see `docs/hermetic_test_doubles.md`), so
completions made through the LLM router return stable, clearly synthetic output
without calling a model provider.

**Current limitation:** LangGraph agent runs build their model through
`core/langgraph/llm_factory.py`, which the fake does not cover. Until the local
model stub service is added to this stack, running an agent needs a model
provider configured for the tenant; everything else works without one.

The credentials in `docker-compose.dev.yml` are development placeholders.
Production and staging configuration rejects them.

## Everyday commands

| Command | Does |
|---|---|
| `make dev` | build, start, wait for health, smoke test |
| `make seed` | development tenant, users, agents and sample data; safe to repeat |
| `make ps` | service status |
| `make logs` | follow logs from every service |
| `make down` | stop the stack, keep data |
| `make clean` | stop the stack and delete its volumes (fresh database next time) |
| `make test` | unit, contract and integration tests |
| `make check` | lint, types, security scans, schema validation, licence headers |
| `make e2e` | browser end-to-end suite against the running stack |

## Tests and checks

`make test` and `make check` run inside the `agenticorg-tools` image
(`Dockerfile.tools`: Python 3.12 like CI, the project's runtime and dev
dependencies, pip-audit and the pinned gitleaks). The checkout is mounted into
the container, so editing code never needs a rebuild; the image is rebuilt from
cache only when `pyproject.toml` or `requirements-dev.txt` changes. The first
build takes several minutes.

| Command | Runs |
|---|---|
| `make test` | `tests/unit`, `tests/security`, `tests/regression` and the contract suite `tests/connector_harness` in one run with coverage and the 55% floor (the CI unit job), then `make test-integration` |
| `make test-unit` | the unit suites only, no coverage floor |
| `make test-contract` | the contract suites only |
| `make test-integration` | `tests/integration` and `tests/regression` against real Postgres and Redis (the CI integration job) |
| `make check` | `ruff check .`, `mypy`, `bandit -ll` on `core/ connectors/ api/ auth/`, gitleaks over this branch's commits, SPDX headers on new files, JSON Schema validation of `schemas/`, the vendor-name denylist over this branch, `pip-audit` of the project and both requirements files |

Each check is also a target of its own (`make check-ruff`, `check-mypy`,
`check-bandit`, `check-secrets`, `check-licence-headers`, `check-schemas`,
`check-denylist`, `check-pip-audit`). `check-secrets`, `check-licence-headers`
and `check-denylist` compare against
`BASE_REF` (default `origin/main`) and fail if it does not resolve, so fetch
first in a fresh clone. `check-pip-audit` needs network access to the
vulnerability database.

The integration suites start the stack's `postgres` and `redis` services if
they are not running, and use a separate database, `agenticorg_test`, dropped
and recreated at the start of every run, plus Redis index 15. Your development
data is never touched: `scripts/reset_test_database.py` refuses any database
whose name does not end in `_test`.

Useful variables:

- `PYTEST_ARGS="-x -k approvals"` adds arguments to every pytest run.
- `UNIT_SUITES`, `CONTRACT_SUITES` and `INTEGRATION_SUITES` replace the suite
  lists, for example
  `make test-integration INTEGRATION_SUITES=tests/integration/test_api_integration.py`.
- `RUNNER=local PYTHON=.venv/bin/python` runs tests and checks with a local
  interpreter instead of the container. It needs `pip install -e ".[dev]"`,
  `pip-audit` and gitleaks 8.30.1; integration tests then reach the stack's
  Postgres and Redis on their published ports.

Test output (`coverage.xml`, pytest scratch directories) is written into the
checkout and owned by your user.

## Browser end-to-end tests

With the stack up (`make dev`), `make e2e` runs the Playwright suite
`ui/e2e/dev-stack.config.ts` in the official Playwright image (pinned to the
version in `ui/package-lock.json`) on the stack's network, against the console
service. It checks that the console serves the sign-in page and proxies the
API, that a signed-out visitor is sent to sign-in and that unknown credentials
are rejected. It refuses to start when the stack is not healthy.

The console's locked npm dependencies are installed into a container-only
volume on the first run. Reports go to `ui/playwright-report/dev-stack` and
failure traces to `ui/test-results/dev-stack`. Pass extra Playwright arguments
with `E2E_ARGS="--grep sign-in"` or another config with `E2E_CONFIG`; the other
configs under `ui/` target hosted environments and need credentials.

## Development identity provider

The **oidc-stub** service (`tools/oidc_stub`) is a small OpenID Connect
provider for exercising sign-in and step-up locally. It is development-only:
it refuses to start unless `AGENTICORG_ENV` is `development`, `local` or
`test`, and also when `AGENTICORG_ENV`, `APP_ENV`, `ENVIRONMENT`, `ENV` or
`NODE_ENV` names a production-like runtime. Its image is built from `tools/`,
which the API image excludes.

| Endpoint | |
|---|---|
| `/.well-known/openid-configuration` | discovery; `issuer` is `http://127.0.0.1:9400` |
| `/jwks` | the RS256 signing key (generated at start unless `OIDC_STUB_SIGNING_KEY_FILE` is set) |
| `/authorize` | authorization code flow; PKCE with `S256` is required |
| `/token` | `authorization_code` only; codes are single-use and expire after 60 seconds |
| `/userinfo` | claims for the stub's access tokens |

Other containers use `http://oidc-stub:9400` for `/token`, `/userinfo` and
`/jwks`, as advertised in discovery; the browser uses the published port.

Users and clients come from `tools/oidc_stub/config.dev.json`:

| User | `sub` | Email | Roles |
|---|---|---|---|
| Approver A | `dev-approver-a` | `approver.a@example.com` | underwriter, approver |
| Approver B | `dev-approver-b` | `approver.b@example.com` | approver |

The `agenticorg-api-dev` client is confidential (development placeholder
secret in the config file) and `agenticorg-dev-public` is a public client.
Redirect URIs on `http://127.0.0.1` match on any port, so moving the stack's
ports does not break them.

`/authorize` shows a sign-in page listing the users; choosing one is the
authentication (there are no passwords). Tokens carry `acr`, `amr` and
`auth_time`:

| Request | Sign-in | `acr` | `amr` |
|---|---|---|---|
| no `acr_values`, or `urn:agenticorg:acr:basic` | pick a user | `urn:agenticorg:acr:basic` | `["pwd"]` |
| `acr_values=urn:agenticorg:acr:step-up` | pick a user and confirm the simulated security key | `urn:agenticorg:acr:step-up` | `["pwd", "hwk"]` |

A browser session that already satisfies the request is signed in without a
prompt. A session that is not stepped up, is older than `max_age`, or meets
`prompt=login` is asked to authenticate again, and re-authentication must be by
the same user. `prompt=none` returns `login_required` instead of prompting.
Unknown `acr_values`, a missing PKCE challenge, an unregistered `redirect_uri`,
a wrong `code_verifier` or a replayed code are rejected, never downgraded.

The API does not sign users in through the stub yet: its OIDC client only
accepts HTTPS issuers on public hosts.

## Development data

With the stack up, `make seed` runs `scripts/seed_dev.py` in the tools image
against the stack's database and prints what it seeded:

| What | Seeded |
|---|---|
| Tenant | `acme-underwriting-dev`, "Acme Underwriting (development)", region EU |
| Users | Approver A (`approver.a@example.com`) and Approver B (`approver.b@example.com`), role `domain_lead`, domain `backoffice`; the same emails as the OIDC stub's users |
| Sign-in configuration | OIDC provider `dev-oidc` for the stub's public client `agenticorg-dev-public`, stored **disabled** (see the note above) |
| Agents | "Risk Sentinel (development)" and "Compliance Guard (development)", in shadow mode with no tools authorised |
| Approval policy | `four-eyes-dev`: step 1 `underwriter`, step 2 `approver` (a different person) |

Every row has a fixed id, so running `make seed` again changes nothing and puts
back any seeded field that was edited. It fails without writing anything if a
seeded name (the tenant slug, a user's email, the provider key, an agent or the
policy name) already belongs to a row it did not create. It refuses to run
unless `AGENTICORG_ENV` is a development or test runtime. All names are
invented and all addresses use `example.com`.

The users have no password by default. To sign in to the console with email
and password, choose a local passphrase of at least 12 characters and pass it
in the environment; only its bcrypt hash is stored:

```bash
AGENTICORG_SEED_PASSWORD='choose-a-local-passphrase' make seed
```

## Changing ports

If a port is already taken, override it for the whole session:

```bash
AGENTICORG_DEV_API_PORT=18000 AGENTICORG_DEV_UI_PORT=13000 \
AGENTICORG_DEV_POSTGRES_PORT=15432 AGENTICORG_DEV_REDIS_PORT=16379 \
AGENTICORG_DEV_OIDC_PORT=19400 make dev
```

Use the same variables with `make ps`, `make logs` and the smoke test.

## Troubleshooting

- **`make dev` times out waiting for health.** Run `make ps` to see which
  service is not healthy, then `make logs`. A failed `migrate` stops the API
  and worker from starting; its log names the migration that failed.
- **Smoke test fails on the console proxy but the API is healthy.** The
  console's nginx resolves the API as `agenticorg-api`; make sure you started
  the stack with `make dev` rather than starting the `ui` service alone.
- **Start from an empty database.** `make clean && make dev`.
- **Windows.** Use GNU make from Git Bash and clone with
  `git clone -c core.autocrlf=false ...`: shell scripts checked out with CRLF
  line endings fail inside the Linux containers. On Docker Desktop the
  mounted checkout does not support every file operation a Linux filesystem
  does, and `tests/regression/test_claude_mistakes.py::test_self_visible_to_pytest`
  (which runs pytest in a subprocess) fails there while passing on Linux.
