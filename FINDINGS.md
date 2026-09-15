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

## A-4 — Runner's `GraphInterrupt` fallback reads state synchronously

- **Found:** switching the LangGraph checkpointer to Postgres (PRD F-2, 2026-09-15).
- **What:** `core/langgraph/runner.py::run_agent` handles `GraphInterrupt` by
  calling `compiled.get_state(config)`, the synchronous API, from inside the
  event loop. `AsyncPostgresSaver` refuses synchronous reads from the loop
  thread, so with `AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres` that read
  always raises, is swallowed by the surrounding `except Exception`, and the
  result reports no output, confidence or token usage. LangGraph 1.x no longer
  raises `GraphInterrupt` from a top-level `ainvoke` (it returns
  `__interrupt__`), so the branch is only reached by older or nested
  invocations.
- **Fix:** use `await compiled.aget_state(config)` and update
  `tests/regression/test_bug_sheet_langgraph_20260914.py::TestSheet36HitlUsage::test_graph_interrupt_exception_path_reports_real_tokens`,
  which mocks the synchronous `get_state`, in the same change (or delete the
  branch if nested invocation is not supported).

## A-5 — Chat-created approvals cannot resume their run

- **Found:** wiring approval decisions to checkpoint resume (PRD F-2, 2026-09-15).
- **What:** `api/v1/chat.py::_record_chat_hitl` creates `hitl_queue` rows for
  chat turns that paused for approval but never passes a server-generated
  thread to `run_agent` or stores `checkpoint_thread_id`, so those approvals
  never resume the run even with `approvals.resume_agent_runs` on (the resume
  is not scheduled). Only `POST /agents/{id}/run` records the thread and the
  resume parameters.
- **Fix:** generate the thread with `core.langgraph.thread_ids.new_thread_id`
  in the chat path, store it and the `_checkpoint_resume` parameters on the
  row the way `api/v1/agents.py` does, and extend
  `tests/unit/test_approval_resumes_agent_run.py` to the chat route.

## A-6 — No automatic retention for the Postgres checkpoint store

- **Found:** documenting checkpoint retention (PRD F-2, 2026-09-15).
- **What:** with `AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres` every agent run
  writes checkpoints, including runs that never pause, and only runs resumed
  after approval are deleted. Nothing else removes them, so
  `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` grow with total run
  volume. `docs/RUNBOOKS.md` gives the manual cleanup SQL.
- **Fix:** a scheduled Celery task running that cleanup in batches (threads
  older than the approval window with no open approval), with a metric for
  rows removed; optionally delete a per-run thread as soon as its run ends
  without pausing (not voice threads, which rely on continuity).

## A-7 — A refused, failed or interrupted approval resume cannot be retried

- **Found:** implementing approval-driven resume (PRD F-2, 2026-09-15).
- **What:** `core/approvals/agent_run_resume.py` claims an approval once
  (`context.checkpoint_resume.state`) and refuses any later resume. A resume
  that failed on a transient store outage, or whose process died while
  `state` was `resuming`, leaves the run paused with no API or task to try
  again; `decide` returns 409 for the already-decided approval.
- **Fix:** an admin-only retry endpoint (or scheduled sweep) that re-claims
  approvals in `refused` with a transient reason (`checkpoint_store_unreachable`)
  or `resuming` older than the run timeout, with tests for double-claim safety.
