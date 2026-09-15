# Findings

Defects found while doing other work and deliberately left out of that change.
Each entry says where it was found, what is wrong and what fixing it involves.
Remove an entry in the pull request that fixes it.

## A-1 — Console image base has fixable HIGH advisories in libuuid

- **Found:** container scan of `Dockerfile.ui` (2026-09-14).
- **What:** the pinned `nginx:alpine@sha256:72ba65eb…` base ships util-linux
  `libuuid` 2.42.1-r0, affected by CVE-2026-53612, CVE-2026-53613,
  CVE-2026-53614, CVE-2026-76642, CVE-2026-78408, CVE-2026-78409 and
  CVE-2026-78410 (fixed in 2.42.3-r0 / 2.42.3-r1). The nightly scan only
  covered the API image, so this was not reported before.
- **Fix:** move the digest in `Dockerfile.ui` to an `nginx:alpine` build with
  libuuid 2.42.3-r1 or later (`scripts/refresh_image_digests.sh`), rebuild,
  rescan and drop the seven entries from `.trivyignore.yaml`.

## A-2 — CONTRIBUTING.md overstates the coverage gate

- **Found:** editing `CONTRIBUTING.md` (2026-09-14).
- **What:** "Tests" says a minimum 80% coverage is enforced in CI; CI and
  `scripts/preflight.sh` enforce `--cov-fail-under=55` plus per-module floors
  (`scripts/check_module_coverage.py`).
- **Fix:** state the real gate. The governed-actions work raises it to 75% on
  changed code, so update the text in that change.

## A-3 — Preflight mypy and CI lint type-check different environments

- **Found:** running `scripts/preflight.sh` on `main` at 784cbd03 (2026-09-14).
- **What:** the CI `lint` job installs only `ruff mypy pydantic` before
  `mypy --ignore-missing-imports .`, so third-party packages such as structlog
  are untyped (`Any`) there. Preflight runs the same command inside a full
  `.[dev]` environment, where structlog's types apply, and fails on
  `core/logging_config.py:92` (`list-item`) while CI passes. The script says
  it mirrors CI exactly; it does not.
- **Fix:** type-check against the installed dependencies in both places
  (install `.[dev]` in the lint job) and fix the processor list's annotation,
  or make preflight mirror the lint job's minimal environment. The first
  catches real type errors; the second only restores agreement.

## A-4 — Registered Grantex scopes use a permission `enforce` cannot satisfy

- **Found:** wiring per-run grants for `grants.enforce_closed` (PRD F-1,
  2026-09-15).
- **What:** `auth/grantex_registration.py::_tools_to_scopes` registers agents
  with `tool:{connector}:execute:{tool}` scopes, and `PATCH /agents/{id}`
  refreshes `config.grantex.grantex_scopes` with the same function
  (`api/v1/agents.py`, BUG-07 block). `grantex.enforce` resolves the granted
  level from the third segment and only understands
  `read < write < delete < admin`, so an `execute` scope grants nothing and
  every call is denied. `core/langgraph/grantex_auth.py::_tools_to_scopes`
  already emits `read`/`write` from the shipped manifests, but nothing on the
  registration path uses it. A per-run grant delegated from these
  registrations therefore reports every tool call as `tool_not_granted` in
  warn mode.
- **Fix:** register and refresh scopes with the manifest-aware mapping, update
  already-registered agents on Grantex (`agents.update`) in a backfill, and
  re-check the warn-mode report before any tenant moves to deny.

## A-5 — The token pool's refresh path calls a grant type Grantex does not serve

- **Found:** extending `auth/token_pool.py` to obtain the first run token
  (2026-09-15).
- **What:** `TokenPool._refresh_after` refreshes through
  `auth/grantex.py::GrantexClient.delegate_agent_token`, which posts
  `grant_type=urn:grantex:agent_delegation` (after a `client_credentials`
  platform token) to `{GRANTEX_TOKEN_SERVER}/oauth2/token`. The Grantex auth
  service serves `/oauth/token` with `authorization_code`, `refresh_token` and
  token exchange only, so that refresh can never succeed against it. Nothing
  calls `token_pool.init()` or `set_agent_config_resolver()` either, so the
  refresh loop and revocation listener never run. Per-run grants added for
  F-1 use `grants.delegate` from the root grant instead and do not depend on
  this path.
- **Fix:** remove the dead refresh/revocation machinery or rebuild it on
  `grants.delegate` / `tokens.refresh`, and initialise the pool (with a Redis
  client per event loop) where the API and workers start.

## A-6 — Legacy scope validation calls the blocking `enforce` on the event loop

- **Found:** adding warn/deny modes to `validate_tool_scopes` (2026-09-15).
- **What:** in `off` mode `core/langgraph/agent_graph.py::validate_tool_scopes`
  still calls `grantex.enforce(...)` directly inside the async graph node.
  `enforce` can fetch the JWKS with a synchronous HTTP request, blocking the
  event loop. The warn/deny path and `ToolGateway.execute` run it with
  `asyncio.to_thread`; the legacy path was left byte-for-byte unchanged so
  `off` keeps today's behaviour.
- **Fix:** run the legacy call through `asyncio.to_thread` too.
