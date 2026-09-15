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
  `http://mock-provider:8080` (`AGENTICORG_MOCK_PROVIDER_URL`). See
  `docs/providers/mock-provider.md`

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
| `make ps` | service status |
| `make logs` | follow logs from every service |
| `make down` | stop the stack, keep data |
| `make clean` | stop the stack and delete its volumes (fresh database next time) |

## Changing ports

If a port is already taken, override it for the whole session:

```bash
AGENTICORG_DEV_API_PORT=18000 AGENTICORG_DEV_UI_PORT=13000 \
AGENTICORG_DEV_POSTGRES_PORT=15432 AGENTICORG_DEV_REDIS_PORT=16379 \nAGENTICORG_DEV_MOCK_PROVIDER_PORT=18081 make dev
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
