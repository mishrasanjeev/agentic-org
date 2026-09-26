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
| Mock verification provider | <http://127.0.0.1:8081> (`/healthz`) |
| OIDC stub | <http://127.0.0.1:9400> (`/.well-known/openid-configuration`) |
| Model stub | <http://127.0.0.1:8090> (`/v1/models`, `/v1/chat/completions`) |
| Grantex auth service | <http://127.0.0.1:3001> (`/health`, `/.well-known/jwks.json`) |

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
- **mock-provider** — the fixture-backed `mock` verification provider as a
  separate HTTP service; the API and worker reach it at
  `http://mock-provider:8080` (`AGENTICORG_MOCK_PROVIDER_URL`). Its fault
  injection and event endpoints stay off unless you start the stack with
  `AGENTICORG_DEV_MOCK_PROVIDER_ADMIN=true`. See
  `docs/providers/mock-provider.md`
- **oidc-stub** — a development OpenID Connect provider with step-up; see
  [Development identity provider](#development-identity-provider)
- **model-stub** — an OpenAI-compatible model service answering from scripts
  and recorded cassettes; see [Model stub](#model-stub)
- **grantex-db** and **grantex** — the Grantex auth service from its published
  image, with its own database; see [Local Grantex](#local-grantex)

Every base image is pinned by digest. The API and worker set
`AGENTICORG_TEST_FAKE_LLM=1` (see `docs/hermetic_test_doubles.md`), so
completions made through the LLM router return stable, clearly synthetic output
without calling a model provider. LangGraph agent runs, which the fake does not
cover, use the model stub when the agent's model is a `vllm:` model: the API
and worker have `VLLM_BASE_URL=http://model-stub:8080`. The agents `make seed`
creates use `vllm:scripted/final-only`, so they run with no model credentials.

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
| `make test` | `tests/unit`, `tests/security` and the contract suites `tests/contract` and `tests/connector_harness` in one run with coverage and the 55% floor, then `make test-integration` with its coverage added, so `coverage.xml` covers both |
| `make test-unit` | the unit suites only, no coverage floor |
| `make test-contract` | the contract suites only |
| `make test-integration` | `tests/integration` and `tests/regression` against real Postgres and Redis. The regression suite runs once, here, so its database-backed tests run too |
| `make coverage-gate` | after `make test`: at least 75% of the lines changed since `BASE_REF` (diff-cover) and 75% of every Python module added since then; tests, test doubles and fixtures are not counted |
| `make check` | `ruff check .`, `mypy`, `bandit -ll` on `core/ connectors/ api/ auth/`, gitleaks over this branch's commits, SPDX headers on new files, JSON Schema validation of `schemas/`, the vendor-name denylist over this branch, `pip-audit` of the project and both requirements files with the reviewed exceptions in `config/pip-audit-exceptions.toml` |

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

### CI

`.github/workflows/local-stack.yml` runs this path on fresh GitHub runners for
every pull request and push to `main`: one job runs `make check`; another runs
`make dev`, `make test`, `make coverage-gate` (pull requests), `make seed` and
`make e2e`, then `make clean`. It uses nothing but Docker and make, so a green
run means a new contributor's clone works too. The coverage report and browser
reports are uploaded as the `local-stack-<sha>` artifact.

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
Redirect URIs must match exactly, with one exception: a registered plain
`http` redirect URI on a loopback host (`127.0.0.1`, `localhost` or `[::1]`)
also matches the same host, path and query on any other port, so moving the
stack's ports does not break them.

`/authorize` shows a sign-in page listing the users; choosing one is the
authentication (there are no passwords). Tokens carry `acr`, `amr` and
`auth_time`:

| How the browser session signed in | `acr` | `amr` |
|---|---|---|
| picked a user | `urn:agenticorg:acr:basic` | `["pwd"]` |
| picked a user and confirmed the simulated security key | `urn:agenticorg:acr:step-up` | `["pwd", "hwk"]` |

`acr` and `amr` describe the session, not the request: a request without
`acr_values` (or with `urn:agenticorg:acr:basic`) that arrives in a session
already stepped up gets `urn:agenticorg:acr:step-up`. Only
`acr_values=urn:agenticorg:acr:step-up` requires the security key.

A browser session that already satisfies the request is signed in without a
prompt. A session that is not stepped up, is older than `max_age`, or meets
`prompt=login` is asked to authenticate again, and re-authentication must be by
the same user. `prompt=none` returns `login_required` instead of prompting.
Sessions end eight hours after sign-in.

**Step-up clients must send `max_age`** (for example `max_age=300`) together
with `acr_values=urn:agenticorg:acr:step-up`, and check `auth_time` and `amr`
in the ID token. Without `max_age`, a security-key confirmation made hours
earlier in the same browser session satisfies the request again.

Every query and form parameter may appear only once; a repeated parameter is
rejected with `invalid_request` (or an error page on `/authorize`).
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
| Agents | "Risk Sentinel (development)" and "Compliance Guard (development)", in shadow mode with no tools authorised, model `vllm:scripted/final-only` (the model stub) |
| Approval policy | `two-step-dev`: two sequential steps, each for the `domain_lead` role. It does not require two different people; see FINDINGS A-32 |

Every row has a fixed id, so running `make seed` again changes nothing and puts
back any seeded field that was edited. It fails without writing anything if a
seeded name (the tenant slug, a user's email, the provider key, an agent or the
policy name) already belongs to a row it did not create. It refuses to run
unless `AGENTICORG_ENV` is a development or test runtime, or when `APP_ENV`,
`ENVIRONMENT`, `ENV` or `NODE_ENV` names a production-like runtime, and refuses
a database host other than `localhost`, `127.0.0.1`, `::1` or the stack's
`postgres` service unless `AGENTICORG_SEED_ALLOW_REMOTE_DB=1`. All names are
invented and all addresses use `example.com`.

The users have no password by default. To sign in to the console with email
and password, choose a local passphrase of at least 12 characters and pass it
in the environment; only its bcrypt hash is stored:

```bash
AGENTICORG_SEED_PASSWORD='choose-a-local-passphrase' make seed
```

## Model stub

The **model-stub** service (`tools/model_stub`) speaks the OpenAI chat
completions API, including tool calls, so the API can run agents without model
credentials. Point an agent at it with a `vllm:` model id; the API strips the
prefix and calls `http://model-stub:8080/v1`. It is development-only in the same
way as the OIDC stub, and never streams.

**Scripted models.** `vllm:scripted/<name>` answers from
`tools/model_stub/scripts/<name>.json`:

```json
{"steps": [
  {"tool_calls": [{"name": "lookup_case", "arguments": {"case_id": "CASE-0001"}}]},
  {"content": {"decision": "refer", "confidence": 0.5}}
]}
```

The step served is the number of assistant turns already in the conversation,
so the stub keeps no state. A `content` object is returned as JSON text.
Tool-call ids are derived from the tool name and arguments exactly as the
in-process scripted model (`core/test_doubles/scripted_model.py`) derives them.
A conversation longer than the script (409 `script_exhausted`), a tool the
request did not bind (400 `script_mismatch`) and an unknown script (404) are
errors. A malformed script (a step that is not exactly one of `tool_calls` or
`content`, arguments that are not an object, an argument named `call_id`)
answers 422 `invalid_script`. `final-only` ships as an example that answers
once, with no tools.

**Cassettes.** Any other model id is answered from
`tests/cassettes/model_stub/`, keyed and stored with `core/model_replay.py`
(model id, messages, tool schemas, temperature, max tokens, stop, and, when a
request sends them, `tool_choice`, `response_format`, `top_p`, `seed`,
`parallel_tool_calls`, `frequency_penalty`, `presence_penalty`, `logit_bias` and
`reasoning_effort`). A request field the stub neither keys nor understands is
rejected with 400 `unsupported_parameter` rather than ignored. In the
default `replay` mode a request with no cassette gets 404 `cassette_miss`, with
the same explanation of where it differs from the nearest recording as the
test harness gives, and nothing is forwarded. To record, run the stack with
`MODEL_STUB_MODE=record` and `MODEL_RECORD_API_KEY` set; requests are then
forwarded to `MODEL_STUB_UPSTREAM_URL` (default the OpenAI API) and saved. The
stub refuses to start in record mode without the key. Review recorded
cassettes like any other fixture before committing them.

## Local Grantex

The API authorises agents through Grantex. Locally it uses the **grantex**
service instead of the hosted one:

- Image `ghcr.io/mishrasanjeev/grantex-auth-service`, pinned by digest in
  `docker-compose.dev.yml`. Move the digest deliberately, like any base image.
  It is pulled from the GitHub Container Registry, so the first `make dev`
  needs network access to `ghcr.io` (no login: the image is public). Later runs
  use the local copy; to work offline, run `make dev` once while connected.
- **grantex-db** creates a `grantex` role and database on the stack's Postgres
  once (idempotent), so Grantex never shares tables with AgenticOrg. Grantex
  applies its own migrations at start and uses Redis index 2.
- It generates its signing keys at start (`AUTO_GENERATE_KEYS`), so tokens it
  issued do not survive `make clean`, and seeds a development developer key.
- The API and worker get `GRANTEX_BASE_URL=http://grantex:3001` and that key as
  `GRANTEX_API_KEY` (a development placeholder). Its issuer is
  `http://grantex:3001`.

The smoke test checks that Grantex is healthy, publishes its keys, and that the
API container reaches it and the key is accepted. `make clean` removes its data
with the rest of the stack.

## Changing ports

If a port is already taken, override it for the whole session:

```bash
AGENTICORG_DEV_API_PORT=18000 AGENTICORG_DEV_UI_PORT=13000 \
AGENTICORG_DEV_POSTGRES_PORT=15432 AGENTICORG_DEV_REDIS_PORT=16379 \
AGENTICORG_DEV_OIDC_PORT=19400 AGENTICORG_DEV_MOCK_PROVIDER_PORT=18081 \
AGENTICORG_DEV_MODEL_STUB_PORT=18090 AGENTICORG_DEV_GRANTEX_PORT=13001 make dev
```

The smoke test runs its API-to-Grantex check through `$COMPOSE` (make passes
its own); set `COMPOSE` when you run `scripts/dev_stack_smoke.sh` by hand
against a stack started with a different project name.

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
