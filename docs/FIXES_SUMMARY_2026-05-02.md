# Fixes Summary — 2026-05-02

This doc tracks everything that shipped on 2026-05-02. The session
started with a red CI/CD pipeline (54.25% coverage gate) and a
customer-reported bug (Uday Chauhan, Edumatica CA Firms) that had
been "code-fixed" the previous day but still reproduced live in prod
under the deployed SHA. It ended with: zero open PRs, zero open
critical/high CodeQL alerts, the missing Celery worker + beat
services live in prod for the first time, and the May-01 bug closed
end-to-end with full tool-call telemetry visible in the API
response.

## Pull requests merged today

| PR | Branch / Title | Why it shipped |
|----|----------------|----------------|
| **#421** | `fix/ci-coverage-gate-include-regression` — include `tests/regression/` in the unit-tests CI job | Coverage gate had been red since PR-C #404. The `--cov=.` invocation was measuring `tests/regression/` (15K lines, 55 files) but pytest was never running it, so the whole tree showed 0% for that path. Adding it to the pytest invocation lifted coverage from 54.25% to ~67% naturally; threshold unchanged. |
| **#422** | `fix/bug-08-tool-gateway-fail-closed` — tool gateway must fail-closed when `connector_ids` unresolved | Discovered during May-01 post-deploy honesty check (PR #406 fix was deployed but symptom still red). When an agent declared `connector_ids=["registry-zoho_books"]` but no live `ConnectorConfig` matched, the runtime continued and `_build_tool_index` scanned every globally-registered connector. Both Zoho and Stripe register `list_invoices`; Stripe won the dict-assignment race, the LLM dispatched `stripe.list_invoices` with empty Bearer header, got 401. The fix plumbs the resolved-name allow-list from `_resolve_connector_configs` through `runner.run_agent` → `build_agent_graph` → `_build_tool_index`, distinguishing `None` (no caller constraint) from `[]` (explicit fail-closed). |
| **#423** | `deps/sec-008-pip-audit-bump` — pin `litellm>=1.83.7`, document `ecdsa` as residual | SEC-2026-05-P2-008 from `BRUTAL_SECURITY_SCAN_2026-05-01.md`. Two real findings (Pillow CVEs already documented as composio-core constraint residuals from PR-A): `litellm 1.83.0` GHSA-xqmj-j6mv-4862 SSTI on `/prompts/test`, pinned to `>=1.83.7`. `ecdsa 0.19.2` CVE-2024-23342 (Minerva timing attack) — upstream maintainers have stated they will NOT patch; documented in `pyproject.toml` as a residual that resolves only via a future `pyjwt[crypto]` migration. |
| **#424** | `fix/celery-beat-tmp-schedule` — celery beat schedule writes to `/tmp` | First boot of the new `agenticorg-beat` Cloud Run service crashed at startup with `PermissionError: [Errno 13] Permission denied: 'celerybeat-schedule'` because PersistentScheduler tries to write/rename the schedule file relative to cwd, which is mounted read-only on Cloud Run. The wrapper now passes `--schedule=/tmp/celerybeat-schedule` (with `AGENTICORG_BEAT_SCHEDULE_PATH` env override). |
| **#425** | `fix/celery-task-discovery` — celery worker registers tasks via `include=` | `app.autodiscover_tasks(["core.tasks"])` looks for a module literally named `tasks` inside the `core.tasks` package — i.e. `core/tasks/tasks.py`. We don't have one. Our task files are named `core/tasks/{report,budget,rpa,health_snapshot,...}_tasks.py`. Celery silently swallowed the ImportError, so **zero `@app.task` decorators ran on the worker**. The api request path was unaffected because each caller imports the relevant tasks module by hand. The fix passes `include=[<7 modules>]` to the `Celery()` constructor; `app.loader.import_default_modules()` then imports each at worker startup. |
| **#426** | `fix/bug-11-tool-calls-response-shape` — emit `tool_calls` alongside `tool_calls_log` | `api/v1/agents.py:2059` reads `lg_result.get("tool_calls", [])` while `core/langgraph/runner.py` emits the log under `tool_calls_log`. Every agent run response showed `tool_calls: []` even when tools fired. Dual-emit added at every return path in `runner.py:run_agent`. |
| **#427** | `fix/codeql-stack-trace-exposure` — drop `str(exc)` from API responses | CodeQL alerts #68 (`api/v1/connectors.py:798`) and #69 (`api/v1/tenant_ai_credentials.py:396`). `str(exc)` on driver errors carries URL fragments and on chained traces a truncated stack frame. The exception class name + the hand-mapped hint + a "see server logs" pointer is enough signal for operator triage; full traceback already captured via `logger.exception`. |
| **#428** | `fix/bug-11-followup-populate-tool-calls-log` — populate `tool_calls_log` from message stream | PR #426 fixed the alias but the underlying field stayed empty because nothing ever populated it. LangGraph's prebuilt `ToolNode` records its results in `state["messages"]` as `ToolMessage` objects; `tool_calls_log` was a declared-but-never-written field. `evaluate()` now scans the message stream and pairs each `ToolMessage` (result) with its invoking `AIMessage.tool_calls` entry (args) via `tool_call_id`. |

Plus 14 dependabot PRs merged or auto-closed (1 closed deliberately:
`fastembed >=0.8` was incompatible — same package was reverted before
in commit `f0932c1`).

## Production rollouts

Three image builds + Cloud Run rollouts during the session, plus one
new pair of services created from scratch.

```
api:  f0bacf2 → 5d799aa → a049126 → c865d52 → eecd78e → af037b8 → d90db46
                (#421)    (#422)    (#426)    (#424)    (#425)    (#428)
ui:   <unchanged on 5d799aa from prior deploy>
embeddings: <unchanged>
worker: NEW — agenticorg-worker on a049126, then af037b8
beat:   NEW — agenticorg-beat on eecd78e
```

`/api/v1/health.commit` returns `d90db46...` post-final-deploy.

## Customer-reported bugs (Ramesh/Uday CA Firms, 2026-05-01)

| Bug | Severity | Layer | Status |
|-----|----------|-------|--------|
| BUG-01 | CRITICAL | L5 connector cache | ✅ Verified GREEN end-to-end (no `tool_call_failed` in `reasoning_trace`) |
| BUG-02 | HIGH | L4 Zoho `organization_id` auto-fetch | ✅ Verified GREEN (`tool_executed connector=zoho_books status=success` in Cloud Run logs) |
| BUG-03 | HIGH | L2 agent connector_ids UUID lookup | ✅ Verified GREEN (`_resolve_connector_configs` correctly resolves `registry-zoho_books`) |
| BUG-04 | HIGH | L4 QuickBooks `realm_id` auto-fetch | ✅ Code path verified (parallel fix; not exercised by the test agent) |
| BUG-05 | MEDIUM | L3 Zoho ConnectorConfig data | ✅ Closed via tester live API fix (per xlsx) |
| BUG-06 | MEDIUM | L1 weak system prompt | ✅ Closed via tester PATCH (per xlsx) |
| BUG-07 | LOW | L1 wrong Grantex scopes | ✅ No-op — agent already has `grantex_scopes: null` |
| **BUG-08** | NEW | tool gateway fail-OPEN | ✅ Fixed in PR #422, deployed |
| **BUG-11** | NEW | API response `tool_calls` field empty | ✅ Fixed in PR #426 + #428, deployed, `tool_calls=[{tool: list_invoices, status: success}]` verified |

The May-01 verification spec at
`ui/e2e/qa-cafirms-may01.spec.ts` would now pass: confidence 0.97
> 0.5, tool_calls non-empty, no `tool_call_failed` in reasoning_trace.

## Code-scanning alerts (CodeQL)

Started 2026-05-02 with **9 open**.

| Alert | Status |
|-------|--------|
| #68 — `connectors.py:798` py/stack-trace-exposure | ✅ Fixed in PR #427 |
| #69 — `tenant_ai_credentials.py:396` py/stack-trace-exposure | ✅ Fixed in PR #427 |
| #66 — `abm.py:464` (over-conservative flow) | ✅ Dismissed as false positive |
| #67 — `knowledge.py:1159` (over-conservative flow) | ✅ Dismissed as false positive |
| #61 — `a2a.py:271` (already sanitized via `_sanitize()`) | ✅ Dismissed as false positive |
| #70/#71/#72/#73 — URL substring in test files | ✅ Dismissed as false positive (test source-code substring assertions, not user-input sanitization) |

The two real-fix alerts (#68, #69) auto-close on the next CodeQL run.

## Operational milestones

- **Celery worker + beat live in prod for the first time.** Per the
  `celery_beat_not_deployed.md` memory, every periodic task in
  `core/tasks/celery_app.py:beat_schedule` had been silently NOT
  firing since the schedule was first declared. After today: all 8
  beat-scheduled tasks are dispatching every 5 min /
  hourly / daily, and the worker is consuming + succeeding. The
  `record-health-snapshot` task wrote its first row to
  `health_check_history` at `2026-05-02T06:50:49Z`.
- **15 PRs landed in 14 hours** (#421..#428 + 14 dependabot, minus
  the 1 closed-on-purpose).
- **CI coverage gate unblocked** — every commit on main since PR-C
  #404 had been red on the coverage check. After PR #421, the gate
  reports ~67% (up from 54.25%) and clears `--cov-fail-under=55`.
- **Three brand-new operational landmines documented**, captured in
  `~/.claude/projects/.../memory/celery_cloudrun_deploy_lessons.md`:
  Cloud Run Services need an HTTP listener on `$PORT` (use the
  `scripts/run_{worker,beat}.py` wrappers); private Redis requires
  the VPC connector on every service; `autodiscover_tasks` is a
  no-op unless the modules are literally named `tasks`.

## Verification commands (for reproducing later)

Public health:

```
curl -sS https://app.agenticorg.ai/api/v1/health
```

May-01 spec replay (requires tester credentials):

```
TOKEN=$(curl -sS -X POST https://app.agenticorg.ai/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<TESTER_EMAIL>","password":"<TESTER_PW>"}' \
  | jq -r .access_token)

curl -sS -X POST https://app.agenticorg.ai/api/v1/agents/02ca34a7-2835-43e5-992d-cda4817c1497/run \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"inputs":{"task":"List my latest invoices"}}' \
  | jq '{confidence, tool_calls, reasoning_trace}'
```

Expected (post-deploy):

```json
{
  "confidence": 0.97,
  "tool_calls": [
    { "tool": "list_invoices", "status": "success", "args": {}, "tool_call_id": "..." }
  ],
  "reasoning_trace": ["Calling LLM (gemini-2.5-flash)", ..., "Confidence: 0.970"]
}
```

Celery beat firing (look for `Sending due task ... record-health-snapshot`):

```
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=agenticorg-beat" \
  --project=perfect-period-305406 --freshness=10m --limit=10
```

Worker consuming (look for `Task ... succeeded in Ns:`):

```
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=agenticorg-worker" \
  --project=perfect-period-305406 --freshness=10m --limit=10
```

## Files of record

- `docs/BRUTAL_SECURITY_SCAN_2026-05-01.md` — the original P0..P3
  blocker list. Drove the PR-A..PR-H program. Two items closed
  today (SEC-008 via PR #423; the rest were closed in the prior 24h
  by PR-A..PR-H).
- `feedback_runtime_path_walk_discipline.md` (memory) — the L1→L7
  layer-walk template that surfaced BUG-08.
- `feedback_28apr_reopen_autopsy.md` (memory) Rule 7: post-deploy
  login-as-tester reproduction is the only honest closure signal.
  This rule is what triggered today's BUG-08 + BUG-11 discoveries.
- `celery_cloudrun_deploy_lessons.md` (memory, NEW today) — the
  three landmines hit deploying worker + beat.
