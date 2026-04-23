# Roadmap: Workflow Durability — BackgroundTasks → Celery

**Status:** scheduled — blocker C from Codex 2026-04-23 re-verification.
**Owner:** platform / workflow engine
**Target release:** v3.3.x

## Problem

`api/v1/workflows.py` still launches long-running workflow runs via
`background_tasks.add_task(...)`:

```python
background_tasks.add_task(_execute_run, run_id, ...)
```

FastAPI `BackgroundTasks` is an in-process, in-event-loop helper. It
has three hard defects for enterprise durability:

1. **No restart survival.** If the API pod is rescheduled (rolling
   deploy, OOM kill, node drain), every in-flight run is silently
   dropped. The row in `workflow_runs` stays at `running` forever.
2. **No retry on failure.** A transient connector failure partway
   through a 20-step workflow aborts the whole run with no retry
   policy — the next attempt has to be initiated manually by the user.
3. **No backpressure.** A burst of 50 workflow kicks will spawn 50
   concurrent coroutines in the API process, competing with request
   handling for the same event loop and exhausting DB pool limits.

The workflow engine itself (`workflows/engine.py`) is already
Redis-checkpointed — `step_results`, `waiting_step_id`, `status` are
all persisted at `wfstate:{run_id}`. What's missing is a **durable
executor** that picks up from those checkpoints after a crash.

## Design

Move workflow execution onto Celery (already deployed for
`core.tasks.workflow_tasks`, which is where `resume_workflow_wait` and
`timeout_workflow_event` already run after K-A).

### Steps

1. Add `core.tasks.workflow_tasks.execute_run` Celery task.
   - Signature: `execute_run.delay(run_id, tenant_id, agent_id, input_data)`.
   - Body: load run row → initialize `WorkflowContext` → drive engine
     via the existing `workflows.engine.WorkflowEngine.run_once()` loop
     until the run reaches `completed`, `failed`, or
     `waiting_for_event`.
   - Idempotency: check `state["status"]` at entry; if already
     terminal or waiting, no-op.
2. Update `api/v1/workflows.py::create_run`:
   - Replace `background_tasks.add_task(_execute_run, ...)` with
     `execute_run.delay(...)`.
   - Keep the 202-response contract unchanged (caller-visible shape is
     identical; only the executor moves).
3. Add a restart sweeper: Celery beat schedule that every 60s picks
   up any `workflow_runs.status = 'running'` rows whose
   `wfstate:{run_id}` has a `last_heartbeat` older than 120s and
   requeues them via `execute_run.delay()`.
4. Run log: add a `workflow_run_events` DB table (run_id,
   event_type, step_id, created_at, payload_json) so the UI can
   display run progress without polling Redis.

### Metrics

- `workflow_runs_requeued_total{reason=}` — bump on sweeper requeue.
- `workflow_run_duration_seconds` histogram — keep labels low
  cardinality (tenant_tier, agent_type, NOT tenant_id).

### Rollback

Feature-flag via `AGENTICORG_WORKFLOW_EXECUTOR=celery|backgroundtasks`
for the first release. Default `celery` once soaked for a week.

## Tests

- `tests/integration/test_workflow_celery_executor.py` — full-stack:
  create run → kill worker mid-step → new worker picks up at the
  recorded step.
- `tests/unit/test_workflow_restart_sweeper.py` — stale-heartbeat
  requeue path.

## Non-goals

- Moving agent LLM calls to Celery (they already run in the Celery
  worker inside `workflow_tasks`).
- Migrating to Temporal / Airflow — explicit non-goal for this
  release. Celery is already operated in production and matches the
  existing checkpoint model.
