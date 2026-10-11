# Local channel validation

This harness runs the real API, PostgreSQL, Redis, Celery worker, beat, and UI.
Provider-shaped callbacks use synthetic credentials. They test signature
verification, account linking, review, deduplication, and durable workflow
execution, not actual Meta, Telegram, Viber, Mailgun, Twilio, or Google delivery.

Use a separate Compose project and unused ports. Never point these tests at
production or reuse a customer database. The browser spec rejects non-local
hosts and creates a disposable tenant.

## Build

From the repository root:

```powershell
docker compose -p ao-channels-local -f docker-compose.dev.yml -f testing/channels/compose.local.yml build migrate ui
docker build --target builder -f Dockerfile.ui -t agenticorg-channel-ui-tools:local .
docker build -f testing/channels/Dockerfile.browser -t agenticorg-channel-browser:local .
docker compose -p ao-channels-local -f docker-compose.dev.yml -f testing/channels/compose.local.yml build tools model-stub oidc-stub
docker build -f testing/channels/Dockerfile.checks -t agenticorg-channel-checks:local .
```

`Dockerfile.checks` also accepts `--build-arg CHECKS_BASE=<approved-local-tools-image>`.
For Windows, run the full preflight in a Linux checkout inside that checks
container rather than mounting Windows Git metadata: linked-worktree metadata
contains Windows paths that Linux Git cannot resolve.

## Run

Set unused `AGENTICORG_DEV_API_PORT`, `AGENTICORG_DEV_UI_PORT`,
`AGENTICORG_DEV_POSTGRES_PORT`, `AGENTICORG_DEV_REDIS_PORT`,
`AGENTICORG_DEV_GRANTEX_PORT`, `AGENTICORG_DEV_MODEL_STUB_PORT`, and
`AGENTICORG_DEV_MOCK_PROVIDER_PORT` using the environment options in
`docker-compose.dev.yml`. Then start the dedicated project:

```powershell
docker compose -p ao-channels-local -f docker-compose.dev.yml -f testing/channels/compose.local.yml up -d
docker compose -p ao-channels-local -f docker-compose.dev.yml -f testing/channels/compose.local.yml run --rm tools python -m pytest tests/e2e/test_channel_workflows.py --no-cov --basetemp=/tmp/channel-e2e
```

Run the browser image on that project's network, mount `ui/e2e` read-only at
`/app/ui/e2e`, mount a disposable screenshot directory at
`/app/ui/test-results`, and set `BASE_URL=http://ui`. The default command runs
`channel-workflows-local.spec.ts` with one worker and no automatic retries.
The test checks desktop/mobile onboarding, credential replacement, and the
public workflow guide. Callback processing is tested by the Python HTTP suite.

Also run the isolated, non-superuser PostgreSQL RLS regression:

```powershell
docker compose -p ao-channels-local -f docker-compose.dev.yml -f testing/channels/compose.local.yml run --rm tools python -m pytest tests/integration/test_channel_rls_postgres.py --no-cov --basetemp=/tmp/channel-rls
```

If recreating the local API changes its container IP, reload the local UI's
Nginx before browser tests. Do not reset unrelated containers or volumes.

## Real account acceptance

Follow [the customer setup guide](../../docs/user-guide/channel-workflows.md).
Enter credentials in the protected product forms, not chat or committed files.
For each provider, complete its approval/subscription steps and send one
authorized test message. Confirm callback receipt, delegation or review,
workflow outcome, and optional status reply. Phone testing requires an approved
destination and may incur charges. A local callback test must never be reported
as proof of real provider delivery.
