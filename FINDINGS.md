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

## A-7 — Six built-in agent prompts are never loaded

- **Found:** aligning prompts with default tools (PRD F-3, 2026-09-15).
- **What:** the run, A2A and MCP paths load a built-in prompt with
  `importlib.import_module(f"core.langgraph.agents.{agent_type}")`
  (`api/v1/agents.py` run path, `api/v1/a2a.py`, `api/v1/mcp.py:_load_agent_prompt`).
  The modules for agent types `abm`, `treasury`, `rev_rec` and `fixed_assets`
  are `abm_agent.py`, `treasury_agent.py`, `rev_rec_agent.py` and
  `fixed_assets_agent.py`, so the import fails and those agents run on the
  one-line generic placeholder instead of `abm_agent.prompt.txt`,
  `treasury_agent.prompt.txt`, `rev_rec_agent.prompt.txt` and
  `fixed_assets_agent.prompt.txt`. `sales_agent.prompt.txt` and
  `nexus_orchestrator.prompt.txt` have no loader at all (only the claims
  linter reads the first).
- **Fix:** resolve the module by an explicit agent-type → module map (or the
  `_agent` suffix) in one shared helper used by all three paths, fail loudly
  when a built-in type has no prompt, and wire or delete the two orphan
  prompts. `scripts/check_prompt_tools.py` already maps `<type>_agent` stems
  to `<type>`.

## A-8 — The three default-tool sources disagree and still name dead tools

- **Found:** removing unregistered tools from `_AGENT_TYPE_DEFAULT_TOOLS`
  (PRD F-3, 2026-09-15).
- **What:** default tools live in `api/v1/agents.py`
  (`_AGENT_TYPE_DEFAULT_TOOLS`), `core/agent_generator.py` (its own copy) and
  each `core/langgraph/agents/*.py` module (`DEFAULT_TOOLS`,
  `AP_PROCESSOR_TOOLS`, ...), which `build_*_graph` binds when no tools are
  passed. For 27 of 37 agent types the three lists differ. The F-3 change
  removed unregistered names from the first two (and from `fpa_agent.py`,
  which a test pins equal), but the modules still name tools no connector
  registers, for example `check_order_status` (ap_processor,
  expense_manager), `get_post_analytics` (social_media, brand_monitor,
  content_factory, seo_strategist), `read_email`/`draft_email` (email_agent),
  `send_notification` (notification_agent) and `read_messages` (chat_agent).
- **Fix:** make `api/v1/agents.py` the only source (the generator and the
  modules import it), then delete the copies; update
  `tests/unit/test_new_agents.py` and `tests/unit/test_langgraph_runtime.py`,
  which pin the module lists.

## A-9 — Prompt token-scope lines name connectors and permissions agents lack

- **Found:** PRD F-3 cites `risk_sentinel.prompt.txt:4` (2026-09-15).
- **What:** the `Token scope:` line of most built-in prompts lists
  connector/permission pairs that are neither registered tools nor in the
  agent's defaults, e.g. `ocr(r:extract)` and `banking_api(w:queue_payment)`
  in `ap_processor`, `jira(w:create_issue)` and
  `sanctions_screening(r:batch_screen)` in `risk_sentinel`, `outlook(...)` in
  `email_agent`. The model reads them as capabilities.
  `scripts/check_prompt_tools.py` checks tool calls only and skips these lines
  because they are not tool names.
- **Fix:** rewrite each token-scope line from the agent's default tools
  (`connector(tool, ...)`) and extend the check to parse and verify it.

## A-10 — Default-tool derivation widens to every tool of a linked connector

- **Found:** removing unregistered defaults (PRD F-3, 2026-09-15).
- **What:** `_derive_default_tools` (`api/v1/agents.py`) returns every tool of
  the linked connectors when none of the agent type's defaults intersect
  them. `seo_strategist` now has no defaults, so linking any connector grants
  all of its tools, including writes; any agent linked to a connector outside
  its defaults gets the same.
- **Fix:** return an empty list (or only read tools) when nothing intersects
  and let the user choose tools explicitly; update
  `tests/unit/test_agent_default_tools_endpoint.py` accordingly.

## A-11 — Console permission badge misreads connector-qualified tools

- **Found:** qualifying ambiguous default tools (PRD F-3, 2026-09-15).
- **What:** `getToolPermission` in `ui/src/pages/AgentCreate.tsx` and
  `ui/src/pages/AgentDetail.tsx` matches the name prefix (`create_`,
  `delete_`, ...), so `jira:create_issue`, `zoho_books:create_bill` or
  `grantex_commerce:cart_create` show as READ and the "minimal scope set"
  preview understates write access. The tool picker can also offer the bare
  `create_issue` beside an already-selected `jira:create_issue`.
- **Fix:** strip the connector prefix before classifying (or use the backend's
  `classify_action`), and treat `connector:tool` and `tool` as the same entry
  in the picker when the connector is linked.

## A-12 — Industry pack prompts call tools nothing registers

- **Found:** running the prompt tool parser over `core/agents/packs` (2026-09-15).
- **What:** the insurance, legal and manufacturing pack prompts call
  `knowledge_base_search()`, which no connector registers, and the CA
  `tds_compliance` prompt calls bare `calculate_tds()` (registered by both
  `income_tax_india` and `zoho_books`) and `get_vendor_details()`.
  `scripts/check_prompt_tools.py` covers `core/agents/prompts` only, because
  pack agents also use `composio:` tools that are discovered over the network.
- **Fix:** register a knowledge-search tool or rewrite those steps, qualify
  the CA names, and extend the check to pack prompts against each pack's
  `tools:` list, treating `composio:` names as declared.

## A-13 — A test run rewrites the tracked coverage report

- **Found:** running `make test` and `make test-integration` in a fresh clone
  (2026-09-15); narrowed 2026-09-20.
- **What:** `tests/unit/test_check_module_coverage.py` runs
  `scripts/check_module_coverage.py`, which rewrites the tracked
  `coverage_report.json`, so every local run dirties the working tree and the
  change is easy to commit by accident. The encrypted-migration audit records
  under `migrations/audit/` had the same problem; the writer now honours
  `AGENTICORG_MIGRATION_AUDIT_DIR`, which the test session points at a
  temporary directory.
- **Fix:** let the report location be overridden the same way (an environment
  variable the test points at `tmp_path`), or have the test restore the file.

## A-14 — Shell scripts break on Windows checkouts with `core.autocrlf=true`

- **Found:** running `make test-integration` from a Windows worktree
  (2026-09-15).
- **What:** `.gitattributes` fixes line endings only for
  `core/policy/examples/*.yaml`, so Git for Windows' default
  `core.autocrlf=true` checks shell scripts out with CRLF endings.
  Bash inside the Linux containers then rejects them:
  `tests/regression/test_bug_sheet_platform_20260914.py::test_deploy_script_pins_worker_and_beat_entrypoints`
  fails on `bash -n scripts/deploy_cloud_run.sh`, and the scripts `make`
  runs in containers would fail the same way. A clone made with
  `core.autocrlf=false` is unaffected.
- **Fix:** add `.gitattributes` with `*.sh text eol=lf` (and the same for
  other files executed inside Linux containers), then renormalise.

## A-16 — Re-running `make dev` after an API change breaks the console proxy

- **Found:** re-running `make dev` on a running stack after the API image was
  rebuilt (2026-09-15).
- **What:** `ui/nginx.conf` proxies `/api` through an `upstream` block naming
  `agenticorg-api:8000`, which nginx resolves once at start. Compose recreates
  the `api` container (new address) but leaves `ui` running, so the console
  answers `/api/...` with 502 and `scripts/dev_stack_smoke.sh` fails on
  "console -> api proxy" until `ui` is restarted. A first `make dev` on a clean
  machine is unaffected.
- **Fix:** add `restart: true` to the `ui` service's `depends_on.api` entry in
  `docker-compose.dev.yml` (Compose restarts `ui` whenever it recreates
  `api`), or resolve the upstream at request time with a `resolver` directive
  and a variable in `proxy_pass`.

## A-17 — `.dockerignore` cache and bytecode patterns only match the repository root

- **Found:** `make dev` rebuilding the API image's dependency layers after a
  local pytest run (2026-09-15).
- **What:** `.dockerignore` lists `__pycache__/`, `*.py[cod]`, `.pytest_cache/`
  and similar without a `**/` prefix. Docker matches such patterns from the
  context root only, so `core/**/__pycache__` and other nested bytecode from a
  developer's test runs are sent in the build context. They change the
  checksum of `COPY core/ core/` in the builder stage, which reruns the full
  `pip install` (several minutes) and puts stale `.pyc` files into a locally
  built image. Clean CI checkouts are unaffected.
- **Fix:** prefix the cache and bytecode patterns with `**/` (for example
  `**/__pycache__/`, `**/*.py[cod]`) and confirm with a build after a test run
  that the builder layers stay cached.

## A-18 — `CONTRIBUTING.md` is committed with CRLF line endings

- **Found:** rebasing the local-stack changes onto `main` at 409fc44d
  (2026-09-15).
- **What:** b611368e rewrote `CONTRIBUTING.md` with CRLF line endings, while
  the rest of the repository stores LF. The commit shows every line as
  changed, and any branch that edited the file before it now conflicts on the
  whole file; an LF-only editor or a `core.autocrlf=input` checkout turns the
  next edit into another whole-file rewrite.
- **Fix:** renormalise the file to LF in its own commit and add a
  `.gitattributes` rule (`*.md text eol=lf`, together with A-14's `*.sh`
  rule) so line endings are fixed at the repository level.

## A-19 — Approval step conditions that fail to evaluate are skipped

- **Found:** reading `core/approvals/policy_engine.py` while adding the case
  policy engine (2026-09-15).
- **What:** `_condition_matches` catches every exception from
  `workflows.condition_evaluator.evaluate_condition`, logs a warning and
  returns `False`. `first_applicable_step` and `next_step_after` treat `False`
  as "this step does not apply", so an approval step whose condition is
  malformed, references a missing context key or raises for any other reason
  is silently skipped. In `api/v1/approvals.py` an `advance` with no further
  applicable step marks the item `decided`, so a broken condition on, for
  example, a second sign-off step lets the item complete with fewer approvals
  than the policy requires. This fails open on an authority path.
- **Fix:** validate step conditions when an approval policy is created or
  updated (refuse unparseable ones with a reason), and at decision time treat
  an evaluation error as "step applies" (or refuse the decision with a reason
  code) rather than skipping it, with a test for a malformed condition on a
  later step.

## A-20 — Runner's `GraphInterrupt` fallback reads state synchronously

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

## A-21 — Key rotation tooling does not cover checkpoint ciphertext

- **Found:** encrypting LangGraph checkpoints with the vault keyring (PRD F-2, 2026-09-15).
- **What:** `core/crypto/rewrap.py` and `core/crypto/verify_all.py` walk
  registered ORM columns holding key-stamped `agko_v{id}$` strings through
  tenant RLS scopes. Checkpoint payloads in `checkpoint_blobs.blob` and
  `checkpoint_writes.blob` are unstamped `MultiFernet` tokens in tables with no
  ORM model and no `tenant_id`, so `verify_all --check=<kid>` reports a key as
  unreferenced while paused runs still depend on it, and rewrap never moves
  them to the active key. Retiring a key strands those runs
  (`checkpoint_decrypt_failed`).
- **Fix:** add a checkpoint scanner to both tools that runs as the database
  owner over the checkpoint tables, using `MultiFernet.rotate` for rewrap and a
  trial decrypt per retired key for verification (or stamp the key id into the
  cipher name), with a test that a key referenced only by a checkpoint blocks
  retirement. Until then, keep retired keys in the keyring for longer than the
  approval window plus the checkpoint retention period.

## A-22 — No tenant offboarding path removes checkpoints

- **Found:** adding tenant checkpoint deletion (PRD F-2, 2026-09-15).
- **What:** `core.langgraph.checkpointer.delete_tenant_checkpoints(tenant_id)`
  deletes every `tenant:<id>:` thread, but the repository has no tenant
  deletion or offboarding flow to call it from. Subject-level DSAR erasure
  (`audit/dsar.py`) cannot reach checkpoint content either: it is encrypted and
  not indexed by subject, so a subject's data in a paused or finished run
  remains until the thread is deleted.
- **Fix:** call `delete_tenant_checkpoints` from the tenant offboarding job when
  one is built, and have DSAR erasure record that checkpoint content is
  removed by retention (or delete the tenant's threads older than the request)
  so the erasure status stays honest.

## A-23 — Chat-created approvals cannot resume their run

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

## A-24 — No automatic retention for the Postgres checkpoint store

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

## A-25 — A refused, failed or interrupted approval resume cannot be retried

- **Found:** implementing approval-driven resume (PRD F-2, 2026-09-15).
- **What:** `core/approvals/agent_run_resume.py` claims an approval once
  (`context.checkpoint_resume.state`) and refuses any later resume. A resume
  that failed on a transient store outage, or whose process died while
  `state` was `resuming`, leaves the run paused with no API or task to try
  again; `decide` returns 409 for the already-decided approval.
- **Fix:** an admin-only retry endpoint (or scheduled sweep) that re-claims
  approvals in `refused` with a transient reason (`checkpoint_store_unreachable`)
  or `resuming` older than the run timeout, with tests for double-claim safety.

## A-26 — Importing the provider interface loads every connector

- **Found:** building the provider conformance suite (2026-09-15).
- **What:** `connectors/__init__.py` imports all connector modules at package
  import. `connectors.framework.verification_provider`, which every provider
  package and `agenticorg.testing.provider_conformance` import, therefore
  pulls in every connector and its third-party dependencies, so a provider
  package's tests need the whole platform's dependency set installed.
- **Fix:** register native connectors from an explicit function called at
  application and worker startup (before plugin loading) instead of at
  package import, or move `connectors/framework` into a package with no
  import-time side effects. Keep the native-before-plugin ordering test.

## A-27 — mypy skips every module under connectors/

- **Found:** type-checking the provider seam (2026-09-15).
- **What:** `pyproject.toml` sets `ignore_errors = true` for `connectors.*`,
  so CI's `mypy .` reports nothing for the new provider interface, registry
  and mock provider, or for any connector. The new modules were checked
  separately with a stricter configuration and are clean.
- **Fix:** replace the blanket override with a per-module list of the legacy
  connectors that still fail, so new code under `connectors/` is checked.

## A-29 — Encrypted-migration gates cannot read JSONB ciphertext containers

- **Found:** re-running `v6z24_case_pseudonym_maps` on a table with rows
  (2026-09-15).
- **What:** `EncryptedMigrationContext.dry_run_decrypt_sample` and
  `assert_decrypt_after` (`core/crypto/migration_helpers.py`) only handle text
  ciphertext. A JSONB column holding `{"_encrypted": "<ciphertext>"}` comes back
  as a `dict` and fails with `AttributeError: 'dict' object has no attribute
  'decode'`, so the gate reports every row as undecryptable.
  `v6z12_voice_runtime` (`voice_calls.transcript_encrypted`) has the same
  shape and cannot be re-run once the table has rows. `v6z24` avoids it by
  skipping the gates when the table already exists.
- **Fix:** unwrap `_encrypted` containers (and the `env1:` envelope prefix) in
  both sampling methods the way `core.crypto.verify_all.parse_encrypted_container`
  does, with a test over a JSONB column.

## A-30 — Key rewrap silently skips JSONB ciphertext outside `*credentials_encrypted`

- **Found:** registering `case_pseudonym_maps.mapping_encrypted` with
  `core/crypto/verify_all.py` (2026-09-15).
- **What:** `core/crypto/rewrap.py::_extract_ciphertext` unwraps
  `{"_encrypted": ...}` only for labels ending in `credentials_encrypted`. For
  `voice_calls.transcript_encrypted` (and now
  `case_pseudonym_maps.mapping_encrypted`) it returns `None`, so those rows are
  never rewrapped and never counted, while `verify_all` still reports their key
  references. Key retirement is blocked (safe), but a rotation cannot complete
  and the rewrap run does not say why. Envelope (`env1:`) values are also not
  handled by the Fernet-only rewrap path.
- **Fix:** unwrap every JSONB `_encrypted` container in `_extract_ciphertext`
  and `_wrap_ciphertext_for_column`, skip or separately handle `env1:` values
  with an explicit count, and add both columns to the rewrap tests.

## A-31 — Some model calls send personal data without redaction

- **Found:** tracing every model caller for pre-model pseudonymisation
  (2026-09-15).
- **What:** with `pseudonymisation.pre_model` off, `core/langgraph/runner.py`
  de-anonymises the run output and trace before `generate_explanation`, which
  sends them to a model (`core/explainer.py`), so the `before_llm` redaction
  mode is undone for that call. Independently of the flag,
  `core/feedback/analyzer.py`, `core/langgraph/sop_parser.py`,
  `core/agent_generator.py`, `core/workflow_generator.py` and the completions in
  `core/agents/marketing/content_factory.py` call models with no redaction.
- **Fix:** pass the masked output and trace to the explainer on the legacy path
  too (as the pseudonymised path now does), and decide per caller whether its
  input can hold personal data; route those through a pseudonymisation session.

## A-32 — Multi-step approvals do not require distinct approvers

- **Found:** review of the development seed's approval policy (2026-09-15).
- **What:** `api/v1/approvals.py` only refuses a second vote by the same person
  on the *same* step ("This reviewer has already voted on the current approval
  step"). Nothing compares approvers across steps, and no code reads a
  distinct-approver setting, so one user holding the step roles can approve
  every step of a multi-step policy alone. A policy described as four-eyes is
  therefore not enforced as such.
- **Fix:** record approvers per item and refuse a decision by anyone who
  decided an earlier step when the policy requires distinct approvers, with a
  reason code and a test. The governed-actions decision grants will enforce
  four-eyes for case decisions; the generic approval flow still needs this.

## A-33 — Approval steps for an unknown role can be decided by any known role

- **Found:** same review (2026-09-15).
- **What:** `_can_decide` in `api/v1/approvals.py` compares role levels from
  `_ROLE_HIERARCHY`, where an unknown role is level 0. An item whose
  `assignee_role` is not in the map (the approval policy API accepts any
  string for `approver_role`) needs level 0, so every user with a known role
  passes the check. This fails open on an authority path.
- **Fix:** refuse to create or update a policy step whose `approver_role` is
  not a known role, and have `_can_decide` deny (with a reason) when the
  assignee role is unknown.

## A-37 — Legacy scope validation calls the blocking `enforce` on the event loop

- **Found:** adding warn/deny modes to `validate_tool_scopes` (2026-09-15).
- **What:** in `off` mode `core/langgraph/agent_graph.py::validate_tool_scopes`
  still calls `grantex.enforce(...)` (through `enforce_connector_grant`)
  directly inside the async graph node.
  `enforce` can fetch the JWKS with a synchronous HTTP request, blocking the
  event loop. The warn/deny path and `ToolGateway.execute` run it with
  `asyncio.to_thread`; the legacy path was left byte-for-byte unchanged so
  `off` keeps today's behaviour.
- **Fix:** run the legacy call through `asyncio.to_thread` too.

## A-39 — A legacy scope denial is reported as a completed run

- **Found:** making deny-mode runs report `failed` (PRD F-1c, 2026-09-15).
- **What:** in `off` mode an agent that carries a configured grant token still
  gets the legacy `validate_tool_scopes` check. When it denies, the node sets
  `status="failed"` and `error="Scope denied: ..."`, but the graph then routes
  to `evaluate`, which unconditionally returns `status="completed"`; the run
  result shows `completed` with an error string and the "Access denied" text
  as output. Deny mode now keeps its runs `failed` via `grant_denial`; the
  legacy path was left unchanged so `off` behaves exactly as before.
- **Fix:** have `evaluate` preserve a `failed` status set by scope validation
  (or set `grant_denial` from the legacy path too), and update the tests that
  describe the legacy result.

## A-38 — An empty web presence carries no evidence to cite

- **Found:** assembling memo sections for businesses with no website
  (2026-09-15).
- **What:** `WebPresence.evidence` may be empty, and the mock provider returns
  no evidence when a business has no web record
  (`connectors/providers/mock/provider.py::web_presence`). The absence of a web
  presence therefore cannot be cited on its own; the underwriter cites the
  resolved registry record instead.
- **Fix:** require at least one evidence entry on `WebPresence` (the search that
  found nothing) in the interface and the conformance suite.

## A-43 — Grants are per connector, not per tool; A2A and MCP run a type's default tools

- **Found:** binding caller tokens on every run route (PRD F-1b review,
  2026-09-15).
- **What:** registered Grantex scopes and `enforce` decide per connector and
  permission level (`tool:<connector>:<read|write|delete|admin>`), not per
  tool, so a grant that covers one write tool on a connector covers every
  write tool on it. A2A (`POST /a2a/tasks`) and MCP (`POST /mcp/call`) run an
  agent *type* with that type's default tool list rather than a stored
  agent's `authorized_tools`, so the tools such a call can reach are decided
  by the type, and only the connector-level grant limits them.
- **Fix:** register per-tool scopes (or a tool allow-list in the grant) and
  have `enforce` check the tool; run A2A and MCP calls with the shared agent's
  stored `authorized_tools` instead of the type defaults.

## A-44 — The Grantex Python SDK's `agents.update` calls a route the auth service does not serve

- **Found:** pushing agent scopes to Grantex on `PATCH /agents/{id}`, verified
  against the Grantex auth service image (PRD F-1 review, 2026-09-15).
- **What:** `grantex.resources._agents.AgentsClient.update` (0.5.0, 0.5.1 and
  the SDK's main branch) sends `POST /v1/agents/{id}`. The auth service serves
  `PATCH /v1/agents/{id}` and answers the `POST` with "Route not found", so
  every scope update through the SDK fails. `auth/grantex_registration.py::
  update_agent_scopes` sends the `PATCH` through the SDK's HTTP client as a
  marked compatibility path.
- **Fix:** change the SDK's `update` to `PATCH` (in the Grantex repository),
  publish it, pin it here, and call `agents.update` again from
  `update_agent_scopes`.

## A-45 — Unique and redundant indexes differ between the models and the migrations

- **Found:** comparing an empty database built by `alembic upgrade head` with
  `core.models` (2026-09-15).
- **What:** migrations create unique or partial indexes the models do not
  declare, among them `uq_agents_industry_pack_company_type`,
  `uq_c6z_connector_evidence_idempotency`, `uq_c6z_onboarding_scope`,
  `uq_c6z_pos_handoff_idempotency`, the four `uq_oacp_*` indexes and
  `ix_rpa_schedules_tenant_name`. Databases built with
  `BaseModel.metadata.create_all` (the integration `client` and `db_session`
  fixtures) therefore lack those uniqueness guarantees, so tests there cannot
  catch a duplicate that production rejects. In the other direction,
  `a2a_tasks.tenant_id`, `bridge_registry.tenant_id`,
  `ca_subscriptions.tenant_id` and `report_schedules.tenant_id` still declare
  `index=True`, although `v6z9_query_performance` drops those indexes as
  redundant; every empty-database bootstrap creates them and drops them again.
  Both sets are listed in `tests/integration/alembic_schema_drift_allowlist.py`.
- **Fix:** declare the unique and partial indexes on their models
  (`Index(..., unique=True, postgresql_where=...)`), remove `index=True` from
  the four columns, and delete the corresponding allowlist entries (the drift
  test fails on entries that no longer differ).

## A-46 — `developer` holds `approvals:write` and `analyst` does not

- **Found:** gating the human-only governed-case routes (PRD A-8 review,
  2026-09-20), reading `core/rbac.py::ROLE_SCOPES`.
- **What:** the `developer` role carries `approvals:write`, which is the write
  scope of the `approvals` family that the governed-case routes declare, so a
  developer session satisfies the RBAC check on decide, withdraw, review and
  approve. `core/ownership.py` limits developer approval decisions to their own
  personal agents, but governed cases are not agent-owned, so that limit does
  not reach them. The `analyst` role, whose job the disposition-review route
  exists for, carries only `approvals:read` and is refused instead.
- **Fix:** decide who may act on a governed case as a matter of product policy
  and either split a `governed_cases` scope family out of `approvals` or move
  the two roles' scopes; needs a data migration for existing tokens and roles,
  so it is not a side change to the route gate.

## A-47 — No mock fixture exercises an activity mismatch

- **Found:** aligning the example policies with the evidence mapping (2026-09-20).
- **What:** `web_presence.activity_mismatch` now resolves for seven of the
  twelve mock fixtures, but it is `false` on every one of them: no fixture's
  website states an activity that contradicts the declared one, so the example
  policies' `web_presence_activity_mismatch` rule is never exercised in its
  fired-on-true form, and neither is the memo's `activity_mismatch` finding.
  `us-hostile-web-glintmoor`, whose second page sells investment returns while
  the application declares solar installation, is the natural home for it.
- **Fix:** give that fixture's pages titles that state the two different
  activities, and update the sanitised mirror in
  `tests/security/test_underwriter_adversarial.py::_sanitised` (which must keep
  producing the same policy outcome as the hostile copy minus the injection),
  with a test that the case's policy result fires the rule on a true mismatch.

## A-50 — The console chrome fails the contrast check on every page

- **Found:** running the axe scan for the governed case screens against the
  local stack (PRD A-9, 2026-09-20).
- **What:** an axe scan of a whole console page reports `color-contrast`
  (serious) for the shared layout, not for the page content: the natural
  language query box (`ui/src/components/NLQueryBar.tsx`, `text-slate-300`
  placeholder and `text-slate-200` input on the light header) and the company
  switcher (`ui/src/components/CompanySwitcher.tsx`, `text-slate-300`). They are
  dark-theme colours on a light surface, so they fail WCAG 1.4.3 on every
  signed-in page. The governed case suite therefore scans `#main-content` only,
  which is the content those screens own.
- **Fix:** give the header components tokens that follow the theme
  (`text-muted-foreground` / `text-foreground`), then widen the accessibility
  scan in `ui/e2e/helpers/governed-cases.ts` back to the whole page.

## A-51 — The provider interface reports no filing status

- **Found:** removing the example policies' `filings_overdue` rule (2026-09-20).
- **What:** `connectors/framework/verification_types.py::BusinessVerification`
  carries status, addresses, identifiers and officers, but nothing about
  statutory filings, although most registries publish whether a company's
  accounts or confirmation statement are overdue. A policy therefore cannot
  score an overdue filing at all: the UK example's rule read
  `verification.overdue_filings`, which the evidence mapping always left
  unresolved, and it has been removed rather than left firing on every case.
- **Fix:** if the domain wants the signal, add it to `BusinessVerification`
  (for example a `filings` block with the last filing date and an overdue
  count), to `schemas/`, to the mock provider's fixtures and to the provider
  conformance suite, then reinstate the rule in the UK example; until then no
  policy may read a filing field.

## A-52 — Offline `--sql` migration scripts cannot be generated

- **Found:** documenting offline mode for the empty-database bootstrap
  (2026-09-20).
- **What:** `alembic upgrade <range> --sql` fails for any range that reaches
  head. `migrations/versions/v6_z26_case_push.py:120` and
  `v6_z24_case_pseudonym_maps.py:51` call
  `op.get_bind().execute(...)` to decide whether their table already exists,
  and `v6_z5_capability_readiness_ledger.py` inspects the bind; in offline
  mode there is no connection, so generation dies with
  `AttributeError: 'NoneType' object has no attribute 'scalar'`. Reproduced
  from `v6z24_case_pseudonym_maps:head` and from the single-step
  `v6z25_governed_cases:head`. An empty database cannot be scripted either:
  the bootstrap needs a connection to inspect. Teams that review SQL before a
  release therefore cannot get that SQL from Alembic.
- **Fix:** guard every bind query with `context.is_offline_mode()` and emit the
  unconditional DDL (or `DO $$ ... $$` blocks that make the same decision in
  SQL) in offline mode, then add a test that
  `alembic upgrade <baseline>:head --sql` renders for the whole chain.

## A-53 — Direct `async_session_factory` use bypasses the private-engine seam

- **Found:** fixing the synchronous credential resolver (A-49, 2026-09-21);
  narrowed 2026-09-22.
- **What:** `core.database.run_db_coroutine_sync` lets a synchronous caller run
  a database coroutine on a private `NullPool` engine, and
  `get_tenant_session` / `get_session` honour it through
  `current_session_factory()`. Thirty modules still bind
  `core.database.async_session_factory` directly. Those are correct on an async
  request path, but a coroutine reached from a synchronous bridge through one
  of them borrows the shared pool, which is the defect A-49 described. The four
  that cached the factory in a module-level singleton — `core/live_feed.py`,
  `workflows/state_store.py`, `workflows/event_waits.py`, `bridge/state.py` —
  now resolve it per call, so the remaining uses are all per-call bindings
  inside a single coroutine. The cross-loop guard catches such a caller only
  when it opens a *new* connection; one that is handed an idle pooled
  connection still fails the old way.
- **Fix:** have the session helpers be the only way to open a session (make
  `async_session_factory` private and route every caller through
  `current_session_factory()`), or add a check that refuses a direct import of
  `async_session_factory` outside `core/database.py`.

- **Found:** testing the report generator's synchronous bridge (2026-09-21).
- **What:** `core.tasks.async_runner.run_async` keeps one event loop per
  process, which is right for a Celery worker: every task shares the loop, so
  the shared engine's pooled connections stay on it. A synchronous caller in a
  process that *also* runs an API loop takes the same branch
  (`core/reports/generator.py::_run_coroutine` when no loop is running in the
  calling thread, for example from `asyncio.to_thread`), and its connections
  go into the shared pool bound to the runner loop; a later request on the API
  loop then fails on checkout, exactly as in A-49. Observed while writing
  `tests/integration/test_sync_credential_resolution_pool.py`: the shared pool
  gained a connection from the runner loop.
  It is not live today: every `run_async` caller is a Celery task module or
  `core/reports/generator.py`, and `ReportGenerator` is reached only through
  the `generate_report` task, which the API dispatches with `.delay()`. It
  becomes live the moment any path in the API process reaches `_run_coroutine`
  — or another `run_async` caller — from a thread with no running loop, and
  `asyncio.to_thread` already appears in 24 places across seven modules under
  `api/`, so that is one careless import away.
- **Fix (near-term, its own pull request):** decide the branch by the process's
  role rather than by whether this thread has a loop — use `run_async` only in
  a worker process (the Celery bootstrap can set a flag) and
  `run_db_coroutine_sync` everywhere else — or give `run_async` its own engine
  bound to the runner loop. Settle the runner's fork guard in the same change:
  fork a child, call `run_async` in parent and child, and assert the child
  built its own loop (no Redis or Celery needed; Linux CI only).

## A-55 — The enterprise stability gate cannot see a file that is not in the index

- **Found:** an unannotated broad exception in a new module passed the gate
  locally and failed in CI (PRD A-9 review follow-up, 2026-09-21).
- **What:** `scripts/check_enterprise_stability_gates.py:263` discovers what to
  scan with `git ls-files '*.py'`, which lists the index only. A module that is
  written but not yet staged is invisible to the gate, so `total_blocked: 0`
  locally while CI - which scans the committed tree - blocks it. The `rglob`
  fallback runs only when git is unavailable, so the blind spot is the normal
  path, and it is widest for new modules, which are exactly the files most
  likely to need an annotation.
- **Fix:** discover with `git ls-files --cached --others --exclude-standard`
  (and keep ignoring what `.gitignore` excludes), so a file the developer is
  about to commit is scanned before it is committed.

## A-56 — Prometheus counters are incremented but never exported

- **Found:** adding `agenticorg_case_excerpt_reads_total` and looking for where
  it could be read (2026-09-21).
- **What:** the API defines Prometheus metrics all over `core/` (the governed
  case transitions, decision requests and grants, provider calls, the new
  excerpt reads) but serves no `/metrics` endpoint: there is no
  `prometheus_client.make_asgi_app()` mount and no metrics route in `api/`.
  Every counter is therefore process-local: nothing outside the process that
  incremented it can read it, and each new metric adds an instrument nobody
  can see.

  To be accurate about what does exist: `observability/alerting.py` is an
  in-process alerter that reads the default `REGISTRY` directly and dispatches
  to Slack or email, so it is not true that no alert can be built at all. Its
  limits are the ones an exported registry fixes - it sees only the process it
  runs in, so on Cloud Run with several API instances plus a worker and beat it
  alerts on a fraction of the traffic and cannot know which fraction; it
  compares raw counter values rather than rates, which on autoscaled instances
  is a number without a meaning; and most of its rules have no sustain window.
  The PRD §10 alerts cannot be built on that foundation.
- **Fix:** export the registry (see `docs/operations/metrics.md`), and prove
  the whole path - process, endpoint, collector, managed Prometheus, policy,
  notification - by driving one alert end to end from a real metric to a real
  notification.
- **Status (2026-09-21):** partly addressed. The endpoint, the instruments and
  the committed alert definitions are on main. PRD §10 is **achievable, not
  met**: no sample has yet travelled the full path. What remains is the
  collector sidecar with the worker's volume, one real `terraform apply`, and
  one alert driven end to end.

## A-57 — The encrypted-migration gate waves through an empty exemption and never reads an edited migration

- **Found:** adding the exemption marker to the case-excerpt migration and
  reading what the marker actually has to satisfy (PRD A-9 review follow-up,
  2026-09-21).
- **What:** `scripts/check_encrypted_migration_uses_helpers.py` has two holes
  that between them let a real ciphertext transform ship unexamined.
  - The exemption is `if EXEMPT_MARKER in body: continue` - a substring test
    over the whole file. The reason the message promises (`# ENCRYPTED_MIGRATION_HELPER_EXEMPT: <reason>`)
    is never parsed and never required to be non-empty, so a bare marker, a
    marker inside a docstring, or a marker in a comment about something else
    exempts the file. The gate's own instruction ("read on review") is the only
    thing enforcing it, and a reviewer cannot notice a reason that is not there.
  - `_added_files` uses `git diff --diff-filter=A`, so the gate examines only
    migrations *added* in the branch. A migration that already exists on the
    base and is edited to backfill or re-encrypt a column - the change most
    likely to be written by hand against a live table - is never read at all.
    The same applies to a migration renamed into place (`R`).
- **Fix:** require the marker to match `^\s*#\s*ENCRYPTED_MIGRATION_HELPER_EXEMPT:\s*\S.+`
  at the start of a line with a reason of some minimum length, and widen the
  discovery to `--diff-filter=AMR` so an edited migration is scanned on the
  same terms as a new one. Do not over-trust the regex: a line-anchored match
  still does not parse Python, so a `#` line inside a triple-quoted string
  would satisfy it. The marker is always in the diff of the migration that
  claims it, so review remains the primary control; the regex only stops an
  empty or absent reason from passing silently.

## A-58 — The test suites use the shared engine from many event loops

- **Found:** measuring the cross-loop guard in CI (2026-09-22).
- **What:** the CI integration job (`pytest tests/integration/ tests/regression/`)
  trips the cross-loop guard **54** times; the unit job trips it 0 times.
  Synchronous test bodies call `asyncio.run`, or spawn a thread that does,
  against `core.database.engine`, so each one leaves the shared pool holding a
  connection bound to a loop that has ended — the same shape as A-49, in test
  code. They pass today because nothing later in the run happens to check that
  connection out, which is luck, and is a plausible source of the flakiness
  this suite has shown. Two cautions about the number: it counts **trips, not
  distinct violations** — one cross-loop use trips the guard once or three
  times, averaging about two, so 54 trips is roughly 27 uses. A violation
  against a warm pool trips once: the session wrapper sees it, and
  `pool_pre_ping` fails on the foreign loop before `checkout` is reached. That
  kills the pooled connection, so the next violation finds an empty pool and
  trips three times — the wrapper, then `connect` and `checkout` together for
  the replacement. The two pool hooks therefore always fire together, on about
  half the violations. Measured against this engine from a warm start: 1
  violation gives 1 trip, 2 give 4, 3 give 5, 5 give 9, 10 give 20, 20 give 40
  (a cold start adds 2 at small n and washes out by 10). And which files
  contribute depends on ordering and on which fixture
  bound the engine first — running a few files alone trips nothing, because
  their own synchronous engines never touch `core.database.engine`.
- **Fix:** move those bodies onto `core.database.run_db_coroutine_sync` (or an
  engine the test owns and disposes), lowering `cross_loop_baseline.txt` as
  they go — `scripts/check_cross_loop_baseline.py` refuses a rise — until it
  reaches 0 and the CI jobs can set `AGENTICORG_DB_CROSS_LOOP_GUARD=raise`.
  Nothing sets that variable today: CI runs on the `warn` default and relies on
  the ratchet.

## A-59 — Tests reach whatever Redis the machine runs, including security controls

- **Found:** chasing `tests/unit/test_run_grant_resolution.py` failing on its
  own while passing in the full run (2026-09-22); extent measured 2026-09-22.
- **What:** every lazy Redis client in the platform degrades quietly when Redis
  is unreachable, which is right in production and wrong in a test: the machine
  then decides the result, and the state outlives the run. The run-grant token
  pool was the instance that surfaced (fixed in #1389), but a broad unit slice
  writes these keys to a real Redis when one is listening:
  `auth:blacklist:<token hash>`, `auth:failures:<ip>`, `auth:signup:<ip>`,
  `auth:rl:<route>:t:<tenant>` and `tenant:<id>:stripe_customer_id`. Three are
  security controls — token blacklist, login-failure lockout, signup rate
  limiting — so a test asserting "this token is revoked" or "the sixth attempt
  is locked out" can be answered by what an earlier run left behind. Two
  test-side habits make it worse: `auth_state._redis = None` reads as "no
  Redis" but means "not created yet", so the next call connects; and
  `ABTestEngine.__init__` connects during construction, so clearing `_redis`
  afterwards is too late. 22 test files still reach a Redis if one is
  listening (`tests/ambient_redis_allowlist.txt`).
- **Fix:** an autouse fixture in `tests/conftest.py` refuses the socket unless
  the run declared a Redis (`AGENTICORG_REDIS_URL`, as the integration job
  does), the test lives in `tests/integration/`, or it carries the
  `ambient_redis` marker. The code under test then takes the path it takes
  against an unreachable Redis, and a test that connects without being on the
  allowlist fails. The list only shrinks: take a file off it by giving the code
  an explicit client or a fake (`no_auth_state_redis` in
  `tests/unit/test_v490_reqs.py` is the pattern), by moving the test to
  `tests/integration/`, or by marking it `ambient_redis` when it is about the
  lazy client itself.
- **Not this entry, but found while checking it:** `core/feature_flags.py` has
  no Redis at all — its fallback swallows exceptions on a flag path, which is a
  different defect worth its own look. `core/cdc/receiver.py` falls back to an
  in-memory store in relaxed environments, i.e. away from real infrastructure,
  which is the safe direction.

## A-60 — The auth controls' Redis path has no test at all

- **Found:** adding positive cases to the REQ-04 security tests (2026-09-22).
- **What:** `tests/unit/test_v490_reqs.py` covers the five controls in
  `core/auth_state.py` through their **in-memory fallback** only, because the
  suite must not reach a Redis (A-59). Nothing asserts the Redis path: not the
  key shapes (`auth:failures:{ip}`, `auth:blocked:{ip}`, `auth:signup:{ip}`,
  `auth:blacklist:{h}`), not the `setex` TTLs that make a lockout expire, and
  not the `_raise_if_strict` branches that are supposed to fail closed in a
  strict runtime rather than degrade to memory. In production every one of
  these runs on Redis, so the tested path is the one production does not use.
  `blacklist_token` narrows it further: it writes `_mem_blacklist`
  unconditionally before consulting Redis, so the blacklist test exercises the
  in-process cache and would pass even if the Redis write were removed.
- **Fix:** test the Redis path against a fake client — `fakeredis` injected
  through `_get_redis`, asserting the key, the value and the TTL of each
  write — or in `tests/integration/` against the real service, where
  `_raise_if_strict` can be exercised by pointing the client at an unreachable
  address in a strict runtime. Until then read "the token blacklist is tested"
  as "the in-process cache is tested".

## A-61 — Run-grant token tests can inherit real Redis state

- **Found:** chasing `tests/unit/test_run_grant_resolution.py` failing on its
  own while passing in the full run (2026-09-22).
- **What:** `auth/token_pool.py::TokenPool._redis_client` creates a Redis
  client lazily when none was set — right in production, wrong in a test. On
  any machine with Redis on `settings.redis_url` (a developer running the
  development stack; any runner with the service up), a test that mints a run
  grant wrote it to that **real** Redis, and the next test read it back:
  `test_pool_refuses_to_mint_without_a_root_grant` and four siblings were
  answered `source="pool_cache"` with `grant_id="grnt_placeholder"` and never
  reached the code that raises `GrantMintError("minting_unconfigured")`. They
  therefore reported DID NOT RAISE on a machine with Redis and passed on one
  without, which is how a fail-closed assertion on the grant-minting path came
  to depend on the environment. The cached tokens also outlived the run, so
  one test run seeded the next.
- **Fix:** an autouse fixture in `tests/conftest.py` now pins
  `TokenPool._redis_client` to whatever the test set on `pool.redis` outside
  `tests/integration/`, so a unit test cannot reach an ambient Redis at all;
  the one test that is about the lazy client carries `@pytest.mark.ambient_redis`.
  Fixed here; recorded because the same shape — a product fallback that is
  correct in production and ambient in a test — is worth looking for elsewhere
  (`core/cdc/receiver.py` and `core/feature_flags.py` have similar fallbacks).

## A-62 — `core.autocrlf` makes the stack's shell scripts unrunnable in its containers

- **Found:** running the new decision-grant browser suite on a Windows checkout
  (2026-09-21).
- **What:** `scripts/run_e2e.sh` runs inside the Playwright container of
  `docker-compose.dev.yml`. Git's `core.autocrlf=true`, the default on a
  Windows install, checks it out with CRLF line endings, and bash in the Linux
  container then fails on line 12 with `set: pipefail: invalid option name`.
  The committed blobs are LF; only the checkout is wrong. `make e2e` is
  therefore broken on a Windows workstation, silently and confusingly.
- **Fix:** this branch adds `*.sh text eol=lf` to `.gitattributes`, which
  covers every shell script. The same trap applies to any other file a Linux
  container reads verbatim and no attribute covers - the Dockerfiles and the
  compose entrypoint scripts among them - and a sweep for those would be worth
  a look.

## A-63 — Condition keywords split inside quoted strings

- **Found:** fixing the approval-policy bypass (review H-6, 2026-09-25).
- **What:** `workflows/condition_evaluator.py::_split_keyword` splits an
  expression on ` OR ` and ` AND ` wherever they appear, including inside a
  quoted string, so `region == 'NORTH OR SOUTH'` is evaluated as two broken
  halves. One half can be definitely false (`x == 'A AND B'` becomes
  `x == 'A` and `B'`), so the whole condition can come out false. Workflow
  conditions and approval-policy step conditions both use this grammar. For
  approval policies the strict evaluator added with H-6 treats a split that
  leaves unbalanced quotes as "unknown" and applies the step; workflow
  branching (`evaluate_condition`) still takes the wrong branch.
- **Fix:** tokenise the expression, skipping quoted spans, before splitting
  on keywords - the same fix `core/langgraph/hitl_condition.py` needs for the
  problem the review reported there, which rewrites keywords inside quotes
  rather than splitting on them. The same applies to ` in ` and to the
  comparison operators inside a quoted value.

## A-65 — An admin can vote once per API key on a personal-agent approval

- **Found:** review of the H-6 fix (2026-09-25).
- **What:** on an approval item for a personal agent, admin API keys pass the
  ownership check, and each key's session subject is `apikey:<prefix>`. The
  one-vote-per-person rule matches on the identifiers a session carries, so an
  administrator who holds several keys can cast one vote per key and satisfy a
  multi-person step alone. Admin-only, and present before H-6.
- **Fix:** refuse machine credentials on multi-person policy steps, or record
  the key's owning user and match on that.

## A-66 — The shared condition evaluator never matches a boolean field against `true`

- **Found:** review of the H-6 fix (2026-09-25).
- **What:** `workflows/condition_evaluator.py::evaluate_condition` compares
  `flag == true` against a real boolean `True` as the strings `"True"` and
  `"true"`, which differ, so the condition is always false. Workflow branches
  on boolean output fields take the wrong path. Approval policies use the
  strict evaluator, which compares booleans correctly since H-6.
- **Fix:** give `evaluate_condition` the same boolean comparison as
  `evaluate_condition_strict`, with a test per operator.

## A-67 — A stranded approval item can only wait to expire

- **Found:** review of the H-6 fix (2026-09-25).
- **What:** since H-6, an item whose policy was deleted or replaced mid-
  approval, or whose remaining steps need people who have already voted,
  refuses every decision. `POST /approvals/{id}/decide` is the only write on an
  item; nothing lets an administrator reset it, re-bind it to the current
  policy or cancel it, so it stays pending until `expires_at` (four hours for
  agent and chat approvals, the workflow timeout for workflow approvals).
- **Fix:** an admin-only, audited action that restarts an item under the
  policy that now resolves, carrying over who has already voted so they still
  cannot vote twice, or cancels it with a reason.

## A-68 — Unmapped route families are not scope-checked for any credential

- **Found:** review of the H-1 fix (2026-09-25).
- **What:** route scope checks cover only the families in
  `api/route_enforcement.py::SCOPE_FAMILIES`. The other families have around
  a hundred authenticated routes with no scope or admin dependency, so any
  authenticated credential - a tool-only agent token, a scope-less API key, a
  viewer session - reaches them. Among them are routes that run agents
  (`/a2a/tasks`, `/mcp/call`, `/sales/pipeline/process-lead`,
  `/sales/run-followups`, `/sales/process-inbox`), routes that return
  tenant-wide data (`/kpis/*`, `/costs/*`, `/sales/pipeline`,
  `/sales/metrics`, `/knowledge/search`, `/companies*`, `/abm/*`,
  `/prompt-templates`), and filing approvals
  (`/companies/{id}/approvals/{approval_id}/approve` and `/reject`, checked
  only against per-company roles). A-43 covers A2A and MCP; this is the wider
  gap. Not caused by H-1, which made agent tokens subject to the mapped
  families only.
- **Update (2026-09-27):** the `a2a` and `mcp` families are now mapped
  (`a2a:read` / `a2a:write`, `mcp:read` / `mcp:write`, with `mcp:call` as an
  alias) but enforced only when `AGENTICORG_ROUTE_SCOPE_A2A_MCP=true`, which
  defaults off; while it is off they stay unmapped as described above, and a
  domain-role session can also run an agent type outside its domains through
  them, since neither route checks the caller's domains. No role holds their
  scopes. Every other family listed here is still unmapped. Separately, an
  authenticated route reached with an unknown `auth_mode` is now logged, and
  refused whatever its family when `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE`
  is on (A-95).
- **Fix:** map every authenticated family to a read and a write scope, or
  refuse by default a family with no mapping, and add each to the unit test
  that pins the unmapped set. Turn `AGENTICORG_ROUTE_SCOPE_A2A_MCP` on in each
  deployment once its A2A integrations hold `a2a:write` (see
  `docs/operations/grant-enforcement.md`), then make it the default.

## A-70 — The tools and API images resolve dependency ranges, not pins

- **Found:** SQLAlchemy 2.1.0 failing `make check` on every pull request
  (2026-09-25).
- **What:** `Dockerfile.tools` installs `pyproject.toml`'s dependency ranges
  (it generates `requirements-project.txt` from them), and the production API
  image does the same (`Dockerfile` runs `pip install ".[v4]"`).
  `requirements.txt`'s exact pins are installed by neither. Every build
  therefore takes the newest release in each range: SQLAlchemy 2.1.0 changed
  its typing, failed mypy on five unchanged files, and the next API image
  would have shipped it untested. The cap in this change covers SQLAlchemy
  only; any other dependency can move the same way.
- **Fix:** install both images with `-c requirements.txt` as constraints (or
  from a hashed lock), so every build resolves to reviewed versions, and let
  dependency updates move the pins deliberately.
- **Related, from the same fix:**
  - The MinIO image comes from Chainguard's free tier, which serves only
    `:latest` and does not promise to keep old digests; a vanished digest
    breaks `make dev` as quay.io's removal did. A scheduled pull check would
    catch it early.
  - MinIO runs as root only so volumes the old root image wrote keep working;
    on a fresh volume the image's own uid 65532 works. A one-off
    `chown -R 65532:65532` of existing volumes (or `make clean`) would let it
    drop root.
  - The in-place `libexpat` upgrade in both UI Dockerfiles should be removed
    once an `nginx:alpine` digest ships 2.8.5-r0.

## A-71 — Strict runtimes may still derive the vault key from the JWT secret

- **Found:** closing the published-default vault key fallback (2026-09-25).
- **What:** with no `AGENTICORG_VAULT_KEYRING` or `AGENTICORG_VAULT_KEY`, the
  credential vault derives its key from `AGENTICORG_SECRET_KEY`, which also
  signs tokens. The fix for the published default kept this fallback so
  deployments that sealed credentials under it (`infra/gcp-setup-lean.sh`
  provisions only the secret key) keep decrypting them. One secret therefore
  protects two unrelated things, and rotating the signing key without first
  rewrapping silently breaks every stored credential. In production the Cloud
  Run beat service has only `AGENTICORG_SECRET_KEY`, so if it ever seals or
  opens a credential it uses a different key from the API and workers.
- **Fix:** give every deployment path a dedicated `AGENTICORG_VAULT_KEYRING`
  (with `legacy:<secret key>` as a decrypt-only entry where rows were sealed
  under it), rewrap, then refuse the secret-key fallback outside local and
  test runtimes.

## A-72 — Other runtime-default fallbacks treat an unset `AGENTICORG_ENV` as development

- **Found:** review of the vault key fallback fix (2026-09-25).
- **What:** `Settings.env` defaults to `"development"` (`core/config.py`), so
  `validate_production_secret` is skipped when `AGENTICORG_ENV` is unset. The
  same default lets two literal fallbacks through:
  - `core/auth_state.py` hashes blacklisted tokens with a fixed literal when
    `AGENTICORG_SECRET_KEY` is missing from the process environment, and
    refuses only when the env is exactly `production` or `staging`. An unset
    env, `prod` or ` Production` gets the literal.
  - `api/v1/cron.py` accepts the literal `dev-cron-key` whenever
    `settings.env` is dev or test, including an unset `AGENTICORG_ENV`.
  The production worker on Cloud Run has no `AGENTICORG_ENV`.
- **Fix:** default `Settings.env` to strict (or require it), use
  `core.config.is_relaxed_env` in both places, and set `AGENTICORG_ENV` on every
  deployed service.

## A-73 — Vault keys have no minimum strength

- **Found:** review of the vault key fallback fix (2026-09-25).
- **What:** in a strict runtime `AGENTICORG_SECRET_KEY` must be 32+ characters,
  but `AGENTICORG_VAULT_KEY` and keyring entries accept anything non-blank that
  is not a published placeholder, including a single character. The production
  keyring's key lengths were not checked (its values were not read).
- **Fix:** confirm the deployed keyring entries are long enough, then refuse
  entries under 32 characters in strict runtimes, with a keyring rotation note
  for any deployment that fails the check.

## A-74 — Connector filters and search only cover the current page

- **Found:** review of the connector readiness paging (2026-09-26).
- **What:** `ui/src/pages/Connectors.tsx` now pages 50 rows at a time, but the
  category filter and search still run in the browser over the loaded page, so
  a match on another page is not shown. The "on page" labels are accurate. A
  failed health check (the `catch` path) also does not refresh the list, so the
  row keeps its previous state until reload.
- **Fix:** pass category and search to `GET /connectors` as query parameters and
  filter server-side; refresh the list after a failed health check too.

## A-75 — Slow feed sockets delay the tenant's Redis listener

- **Found:** review of the tenant live feed (2026-09-26).
- **What:** `api/websocket/feed.py` `_fanout_local` sends in batches of 32, one
  batch after another, each waiting up to the 2s send timeout. The Redis
  listener awaits the whole fanout, so N slow sockets hold the next message for
  up to ceil(N/32) x 2s and the backlog sits in the pubsub buffer. Events
  published while the listener reconnects are only noticed as a gap on the next
  event.
- **Fix:** hand each socket its own bounded queue drained by a per-socket
  sender, so the listener never waits on a client, and close sockets whose queue
  overflows.

## A-76 — A token refresh counts as a passed health check for agent activation

- **Found:** review of connector readiness (2026-09-26).
- **What:** `core/tasks/token_refresh.py` marks a connector `healthy` whenever a
  refresh succeeds, and `api/v1/agents.py` `_assert_connectors_ready_for_activation`
  gates agents on `health_status == "healthy"`. A connector whose last health
  check failed for a reason a refresh does not fix (a missing scope, a broken
  endpoint) becomes activatable again after the next refresh. Readiness now shows
  it as needing a check, but the activation gate does not.
- **Fix:** keep refresh state and check state separate (for example a
  `refresh_status` column), and gate activation on a recent passing check.

## A-77 — Route enforcement treats `agenticorg.admin` as the admin scope

- **Found:** review of the admin-key compatibility migration (2026-09-26).
- **What:** `api/route_enforcement.py` `_expand_granted` adds the colon/dot
  separator variant of every granted scope, so a key or grant holding
  `agenticorg.admin` gains `agenticorg:admin` there and passes every
  route-family check (`agents:write`, `approvals:write`, `workflows:write`, ...).
  `core.rbac.has_admin_scope` and `require_scope` compare exactly, so it cannot
  create keys or pass ownership checks. Key creation now refuses the dot form,
  but keys issued with it before keep that route access.
- **Fix:** stop expanding separator variants for `agenticorg:admin` in
  `_expand_granted`, after checking with the CHANGELOG audit query that no live
  key depends on it.

## A-78 — The GDPR, DPDP and HIPAA pages misdescribe the DSAR endpoints

- **Found:** fixing DSAR erasure against the append-only audit log (2026-09-26).
- **What:** the DSAR routes are mounted at `/api/v1/dsar/...`, but
  `docs/GDPR.md`, `docs/DPDP_ACT.md` and `docs/HIPAA.md` give them as
  `/api/v1/compliance/dsar/...`. `docs/GDPR.md` lists `dsar/restrict`, which
  does not exist, and `dsar/export?format=jsonld`: `POST /api/v1/dsar/export`
  exists but produces JSON only (`audit/dsar.py`), and `format` is ignored.
  `docs/DPDP_ACT.md` lists `dsar/withdraw`, which does not exist. Both pages
  cite `PATCH /api/v1/users/{id}` for rectification, which no router defines,
  and `docs/GDPR.md` says admins can run requests from a Compliance tab that
  the console does not have. `docs/HIPAA.md` says data is hard-deleted on a
  DSAR and that the audit log records the deletion, while erasure anonymises
  the user record, pseudonymises feedback and keeps audit rows, and the
  request's audit entry is written as `received` before processing. The
  "Compliance Flow" diagram in `docs/api-reference.md` shows a scan of 18
  tables and an HMAC-signed audit entry; the handler reads three tables and
  nothing signs the entry.
- **Fix:** list only the routes in `api/v1/compliance.py` under their real
  prefix, say export is JSON only, mark restriction, withdrawal and
  rectification as handled by request to the controller until routes exist,
  drop the Compliance-tab claim, describe erasure in `docs/HIPAA.md` as
  `docs/GDPR.md` now does, and redraw the diagram from `audit/dsar.py`.

## A-79 — DSAR request audit entries name the subject as the actor

- **Found:** fixing DSAR erasure against the append-only audit log (2026-09-26).
- **What:** `api/v1/compliance.py` `_create_dsar_audit_entry` writes
  `actor_id=subject_email` for every DSAR request, although the actor is the
  tenant admin who made it (`requested_by`). The trail misattributes who acted,
  and each request writes the subject's e-mail into the append-only log twice
  (`actor_id` and `details.subject_email`), where erasure can no longer reach it.
- **Fix:** record `requested_by` as the actor and reference the subject by the
  DSAR request id (or `audit.dsar.pseudonymise`) in `details`.

## A-80 — Some audit writers record the session e-mail as the actor

- **Found:** review of the DSAR erasure fix (2026-09-26).
- **What:** the session token's `sub` is the user's e-mail. Most audit writers
  record the stable user id, but `api/v1/governance.py:113` falls back to `sub`
  when the token has no `agenticorg:user_id`, feedback records `sub`
  (`api/v1/agents.py:4847`), and DSAR request entries record the subject's
  e-mail (A-79). `audit_log` is append-only, so erasure keeps those rows and
  reports them as retained (GDPR Art. 17(3)(b)); the e-mail in them stays.
- **Fix:** record the user id (or `audit.dsar.pseudonymise`) as `actor_id` at
  write time and keep e-mail addresses out of `details`, so retained audit rows
  carry no direct identifier. Rows already written stay as they are.

## A-82 — Key rotation tooling does not see vault ciphertext outside five columns

- **Found:** rehearsing the vault key rotation runbook against a local database
  (2026-09-27).
- **What:** `core/crypto/verify_all.py` and `core/crypto/rewrap.py` walk only
  the five columns in `_SCANNERS`. `encrypt_for_tenant` and `encrypt_with_kek`
  seal with the vault keyring whenever the tenant has no KMS key, and four
  other places store the result: `sso_configs.config` (`client_secret_enc`,
  `api/v1/sso.py`), `case_push_endpoints.signing_keys_encrypted`
  (`core/cases/push.py`), `governed_cases.excerpts_encrypted` (each entry's
  `text_encrypted`, `core/cases/excerpts.py`) and `tenants.settings`
  (`voice_configs.*.credentials_encrypted`, `api/v1/voice.py`). After a
  promote and rewrap, `verify_all --check=<old id>` exited 0 while an SSO client
  secret and a case push signing key were still stamped with the old id; with
  the old key removed both failed with `InvalidToken`, which stops OIDC sign-in
  and case push signing for those tenants. The runbook's step 5 query finds
  such values meanwhile.
- **Fix:** register the four locations with both tools (a scanner that reads
  and writes ciphertext at a JSON path, since two of them sit inside a document
  or a list), with a test that a key referenced only there blocks retirement,
  and a test that fails when a new `encrypt_for_tenant` or `encrypt_with_kek`
  call site stores somewhere unregistered.

## A-83 — Rewrap can overwrite a credential written while it runs

- **Found:** rehearsing the vault key rotation runbook against a local database
  (2026-09-27).
- **What:** `core/crypto/rewrap.py` reads a batch with a plain `SELECT` and
  writes each row back with `UPDATE ... WHERE id = :id`, with no row lock and no
  check that the value is unchanged. A credential stored in between (the token
  refresh task rewrites `connector_configs.credentials_encrypted` every 15
  minutes; a reconnect does too) is replaced by the re-encrypted old value.
  Reproduced against Postgres: a write committed after rewrap read the row was
  reverted and rewrap exited 0. A provider that rotates refresh tokens then
  refuses the restored one.
- **Fix:** read each batch with `SELECT ... FOR UPDATE` in the transaction that
  writes it (or update only where the column still holds the value read, and
  count the rows skipped), with a test that commits a concurrent write between
  the read and the update.

## A-84 — The secrets rotation workflow accepts the vault's own secrets, and its runbook is stale

- **Found:** documenting vault key rotation (2026-09-27).
- **What:**
  - `.github/workflows/secrets-rotation.yml` refuses `AGENTICORG_SECRET_KEY` and
    externally issued secrets by ID, but not the secrets behind
    `AGENTICORG_VAULT_KEYRING` or `AGENTICORG_VAULT_KEY`, and replaces a value
    with `openssl rand -base64 48`. A keyring replaced that way has no `id:`
    entry, so new API and worker instances refuse to start; a single vault key
    replaced that way opens nothing already stored. Its comment gives the order
    "generate new key -> rewrap -> cut over", but rewrap only moves rows to the
    key that is already active.
  - `docs/SECRETS_ROTATION.md` still says the workflow runs quarterly on a
    schedule (it is `workflow_dispatch` only), and its verification and rollback
    sections use `kubectl` against the removed GKE deployment and mention a
    30-minute dual-read window.
- **Fix:** refuse the vault's secrets in the workflow (by ID, and by checking
  whether a service mounts the secret as a vault variable), correct the
  comment, and rewrite the page's schedule, verification and rollback sections
  for Cloud Run.

## A-85 — Migrating a fresh local database rewrites a committed audit record

- **Found:** migrating a scratch database for the vault key rotation rehearsal
  (2026-09-27).
- **What:** `python scripts/alembic_migrate.py`, the README's local setup step,
  rewrote the tracked `migrations/audit/v6z12_voice_runtime.json` with the local
  run's `started_at` and `completed_at`. `core.crypto.migration_helpers` writes
  audit records into the checkout unless `AGENTICORG_MIGRATION_AUDIT_DIR` is set,
  and only the test session sets it (A-13), so every developer who migrates an
  empty database gets a modified file that is easy to commit.
- **Fix:** write local and test runs' records outside the checkout by default
  (for example under the system's temporary files unless the runtime is strict
  or the variable is set), or document the variable in the README next to the
  migrate step.

## A-86 — WebSocket routes fail on the app-wide route enforcement dependency

- **Found:** fixing the route scope residual of H-1 (2026-09-27).
- **What:** `api/main.py` registers `api.route_enforcement.enforce_route_metadata`
  as an app-wide dependency, and it takes `request: Request`. FastAPI (0.139)
  passes no `Request` to a WebSocket route's dependencies, so a connection to
  `api.main.app` fails with `TypeError: enforce_route_metadata() missing 1
  required positional argument: 'request'` before the handler runs, for
  `/api/v1/ws/feed/{tenant_id}` and `/api/v1/ws/bridge/{bridge_id}` alike
  (reproduced with the test client, with and without a token). The WebSocket
  tests mount the routers on a bare `FastAPI()` without the dependency, so
  they pass. Deployed behaviour was not checked.
- **Fix:** have the dependency take `HTTPConnection` and leave WebSocket routes
  to the authentication their handlers already do, then add a test that
  connects through `api.main.app`. Do not run `_check_scope` on them as it
  stands: the auth middleware does not run for WebSockets, so the connection
  has no `auth_mode` or scopes and would be refused as an unknown mode with
  `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE` on, or judged on no scopes with
  it off.

## A-87 — The alert gate probe test rewrites tracked files in the working tree

- **Found:** running `tests/unit` on a Windows checkout (2026-09-27).
- **What:** `tests/unit/observability/test_probes.py` runs
  `scripts/probe_alert_gate.py`, which mutates the repository's own
  `monitoring/prometheus/agenticorg-alerts.yml` and
  `infra/terraform/monitoring/alerts.tf` in place and restores them with
  `newline="\n"`. On a checkout with CRLF line endings (A-62) both files are
  left rewritten with LF after every unit run, so `git status` shows them
  modified, and a run interrupted between mutation and restore leaves a broken
  alert rule in the working tree.
- **Fix:** have the probe (or its test) work on copies under `tmp_path`, or
  restore the exact original bytes (`read_bytes` / `write_bytes`).

## A-88 — An exempt auth prefix reaches into an authenticated route

- **Found:** pinning the route table against the auth middleware's exemptions
  for the route scope residual of H-1 (2026-09-27).
- **What:** `GrantexAuthMiddleware.EXEMPT_PREFIXES` holds
  `/api/v1/aa/consent/callback` as a prefix with no trailing slash, so every
  request path that begins with it skips the middleware. The authenticated
  route `GET /api/v1/aa/consent/{consent_handle}/status` falls under it when
  the handle begins with `callback` (for example
  `/api/v1/aa/consent/callback-0/status`): the credential is never read. With
  `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE` on, such a request is refused
  with `403 Unrecognised authentication mode; request refused` before the
  handler runs; with it off (the default) it is logged as an unknown mode and
  `get_current_tenant` answers `401`, as before. Either way nothing is
  exposed, but a valid caller with such a handle is refused too. Handles come
  from the account aggregator's response, or are a UUID when it returns none.
- **Fix:** exempt the callback as an exact path (`EXEMPT_PATHS`; the provider
  callback is the single route `POST /aa/consent/callback`) once the path the
  aggregator posts to is confirmed, and remove the pair from
  `KNOWN_EXEMPT_OVERLAPS` in
  `tests/regression/test_route_scope_unknown_mode_20260927.py`.

## A-89 — Defer ends an approval without a policy and fails under one

- **Found:** adding `approvals.unevaluable_condition` (2026-09-27).
- **What:** the approval card offers Approve, Reject and Defer
  (`ui/src/components/ApprovalCard.tsx`), and `HITLDecision.decision` accepts
  any string. On an item with no policy, `POST /approvals/{id}/decide` with
  `defer` takes the legacy path: the item is marked `decided` with decision
  `defer`, and a workflow item's run is resumed with that decision, so
  deferring ends the approval. Under a policy, `apply_decision` raises
  `ValueError("Unknown decision 'defer'")`, which the global handler returns
  as a generic `400 Invalid request`. With `approvals.unevaluable_condition`
  in `deny` mode, a defer (or any value other than `reject`) on an item whose
  policy has a condition that cannot be evaluated gets that flag's `409`
  instead, as an approval does, because where no policy applies it ends the
  item.
- **Fix:** decide what defer means (leave the item pending and record the
  deferral, or drop the button), validate `decision` against the item's
  `decision_options` at the boundary, and answer an unknown decision with a
  `422` that says why.

## A-91 — The approval policy model describes resolution at item creation

- **Found:** adding `approvals.unevaluable_condition` (2026-09-27).
- **What:** the docstring of `core/models/approval_policy.py` says a policy is
  resolved when an approval item is created, setting the item's assignee role,
  quorum and step index, and points to `docs/adr/0005-approval-policies.md`,
  which does not exist. Nothing resolves a policy at creation:
  `api/v1/approvals.py::decide` resolves it on the first decision and keeps the
  item's progress in `context.policy_state`. The docstring invites a
  creation-time check, which would miss policies created or edited while an
  item waits.
- **Fix:** describe decision-time resolution in the docstring and point it at
  `docs/approval-policies.md`.

## A-92 — A connector that screens through a provider cannot pass the activation gate

- **Found:** review of the sanctions screening connector (2026-09-27).
- **What:** the verification provider seam
  (`connectors/framework/verification_provider.py`) has no probe, so the
  `sanctions_screening` connector cannot tell a provider with wrong credentials
  from a working one and its health check reports `configured`, never
  `healthy`. `_assert_connectors_ready_for_activation` (`api/v1/agents.py`)
  requires `health_status == "healthy"` for every linked connector, so an
  agent that links the connector cannot be activated.
- **Fix:** add an optional probe to the seam that performs no screening and
  defaults to "not offered", have the connector report `healthy` only when the
  provider's probe succeeds, and document the probe in
  `docs/providers/writing-a-verification-provider.md`.

## A-93 — Legacy scope validation checks the first connector with a tool name

- **Found:** review of the sanctions screening rename (2026-09-27).
- **What:** in `off` mode `validate_tool_scopes`
  (`core/langgraph/agent_graph.py`) finds the connector of a bare tool name in
  the unfiltered `_build_tool_index`, which returns the first connector that
  registers the name. `build_tools_for_agent` binds the name to the agent's own
  connector, and the warn/deny path checks that one (`tool_refs`). 38 bare
  names are registered by more than one connector: a Jira-only agent's
  `create_issue` runs on `jira` and its grant holds `tool:jira:...` scopes
  (`auth/grantex_registration.py` resolves against the agent's connectors),
  but the legacy check asks Grantex about `github` and denies the call. The
  reverse also holds: a grant with only `tool:github:write:create_issue`
  passes the legacy check for that agent's `jira` call, so in `off` a scope
  for one connector covers another connector's tool of the same name. The
  runtime registration path (`core/langgraph/grantex_auth.py::_tools_to_scopes`,
  unfiltered index) issues exactly such scopes.
- **Fix:** look the call up in `tool_refs` first in the legacy path too, and
  fall back to the index only for names the agent did not register, as
  `_enforce_tool_grants` does. Refresh the scopes of agents registered through
  the runtime path first (`scripts/refresh_grantex_scopes.py`), or their calls
  start failing in `off`.

## A-94 — The deprecated `sanctions_api` connector has no removal date

- **Found:** reworking the screening connector for provider neutrality (2026-09-27).
- **What:** `connectors/ops/sanctions_api.py` stays in the tree as a deprecated
  legacy connector so tenants that use it keep working. It no longer names a
  provider (its base URL comes from connector config or
  `AGENTICORG_SANCTIONS_API_BASE_URL`), but it is still shaped around one
  service's API, and nothing tracks moving those tenants to
  `sanctions_screening` or deleting the module.
- **Fix:** list the tenants whose agents link `sanctions_api`, move them to
  `sanctions_screening` with a provider package, then delete the module, its
  registration and the grant alias.

## A-95 — An unrecognised authentication mode is refused only when `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE` is on

- **Found:** review of the route scope residual of H-1 (2026-09-27). The
  refusal was first written to apply by default, which the feature flag rule
  in `AGENTS.md` does not allow for a change on an existing path.
- **What:** the auth middleware sets `auth_mode` to `api_key`, `grantex` or
  `legacy` once it has verified a credential. `_check_scope` in
  `api/route_enforcement.py` logs `route_enforcement_unknown_auth_mode` for an
  authenticated route reached with any other mode, or none, but refuses it
  only while `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE` is on, and the
  setting defaults off. While it is off, a request whose mode was not set by a
  verified credential is judged on whatever scopes it carries, as before:
  `agenticorg:admin` among them passes every route, a route in an unmapped
  family (A-68) needs no scope, and `AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`
  lets a missing scope through. The mounted middleware sets scopes only
  together with a known mode, so today such a request carries no scopes (it
  arrives through an exemption, A-88) and the route's own dependencies refuse
  it. The exposure is any later code that sets `request.state.scopes` without
  a known mode, as the unmounted `auth.middleware.AuthMiddleware` does, or a
  new exemption that reaches an authenticated route.
- **Fix:** owned by the code owners of `api/route_enforcement.py` and
  `auth/grantex_middleware.py` (`.github/CODEOWNERS`). The warning is logged
  with the setting off too: watch staging for
  `route_enforcement_unknown_auth_mode`, and once it shows none, make `true`
  the default in a release that records the flip in `CHANGELOG.md` as a
  breaking change with `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE=false` as the
  explicit opt-out, then remove this entry.

## A-96 — `.dockerignore` only excludes Python caches at the repository root

- **Found:** running the decision-grant CI sequence: `make dev` rebuilt the API
  image's dependency layer
  after local test runs had written `auth/__pycache__` (2026-09-27).
- **What:** `__pycache__/` and `*.py[cod]` in `.dockerignore` match only at the
  context root (Docker's patterns are anchored), so every nested
  `__pycache__` is sent with `COPY core/ core/`, `COPY auth/ auth/` and the rest.
  A local test run then invalidates the `pip install` layer, which re-downloads
  every dependency, and workstation bytecode reaches the image the file says
  it keeps out.
- **Fix:** use `**/__pycache__` and `**/*.py[cod]` (and review the other
  unanchored-looking patterns in the file the same way).

## A-97 — `make down` and `make clean` leave the decision-grant profile behind

- **Found:** taking the local decision-grant run down (2026-09-27).
- **What:** both targets run `docker compose down` with no profile, so a stack
  started with `AGENTICORG_DEV_DECISION_GRANTS=true` keeps its
  `oidc-approvers` container running after `make clean` (in the network
  namespace of a `grantex` container that no longer exists), and the
  `e2e-node-modules` volume, used only by the `e2e` profile, survives
  `make clean`. A later `make dev` works around the container; CI runners are
  discarded, so only workstations are affected.
- **Fix:** pass the `decisions`, `e2e` and `tools` profiles to `down` in both
  targets.

## A-98 — Email webhook signatures are verified with one key per provider

- **Found:** binding email webhooks to the tenant by URL (2026-09-27).
- **What:** `api/v1/webhooks.py` verifies every SendGrid, Mailchimp and
  MoEngage delivery with one deployment-wide key (`SENDGRID_WEBHOOK_KEY`,
  `MAILCHIMP_WEBHOOK_KEY`, `MOENGAGE_WEBHOOK_KEY`), on the shared and the
  per-tenant URLs alike. A tenant whose provider account (or webhook) signs
  with its own key cannot be verified at all, so the per-tenant URLs only
  work when every tenant's webhook signs with the deployment's key, and any
  holder of that key can sign for any tenant whose path it knows.
- **Fix:** store a verification key per tenant and provider, encrypted like
  connector credentials, verify the per-tenant route with the tenant's key,
  and keep the deployment key only for the shared URLs while they exist.

## A-99 — The console and `GET /webhooks` list only the shared email webhook URLs

- **Found:** binding email webhooks to the tenant by URL (2026-09-27).
- **What:** the Webhooks card in `ui/src/pages/Settings.tsx` lists
  `/api/v1/webhooks/email/{sendgrid,mailchimp,moengage}` and says to use "the
  signing secret issued from Settings → API Keys". The signing keys come from
  the deployment's environment, not from API keys, and the tenant's own URLs
  (`GET /api/v1/email-webhook-inbox`) are not shown, so an administrator has
  to call the API to find them. The discovery route `GET /api/v1/webhooks`
  (`list_webhook_endpoints` in `api/v1/webhooks.py`) returns the same three
  shared paths and mentions neither the per-tenant paths nor the inbox route.
  With `AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS` on, both point a provider at
  URLs that answer 409 to tenant-tagged events.
- **Fix:** for an administrator, show the tenant's per-tenant URLs from
  `GET /api/v1/email-webhook-inbox` (as credentials: masked, copy on demand)
  and describe where the provider's signing key is configured. Add the
  per-tenant path pattern and the inbox route to `GET /api/v1/webhooks`.

## A-100 — `wait_for_event` can take its tenant from the trigger payload

- **Found:** binding email webhooks to the tenant by URL (2026-09-27).
- **What:** `workflows/step_types.py` `_execute_wait_for_event` registers the
  wait with `state["tenant_id"]`, or, when the run has none,
  `trigger_payload["tenant_id"]` / `["agenticorg:tenant_id"]`. The API start
  path always sets the run's tenant, so this only applies to runs started
  without one, but it is the same pattern as the webhook defect: a tenant
  chosen by the payload decides whose events can resume the step.
- **Fix:** register the wait only under the run's own tenant, and refuse to
  register one (fail the step) when the run has no tenant.

## A-101 — Email event history in Redis is not keyed by tenant

- **Found:** binding email webhooks to the tenant by URL (2026-09-27).
- **What:** `api/v1/webhooks.py` `_store_email_event` writes every event to
  the Redis hash `email_events:{campaign_id}:{email}`. Two tenants with the
  same campaign id and recipient address write into one hash, each
  overwriting the other's `tenant_id` and timestamps. Nothing in this
  repository reads the hash.
- **Fix:** key the hash by tenant (`email_events:{tenant_id}:...`) with a TTL,
  or remove the write if nothing is going to read it.

## A-102 — Email webhook handlers log recipient e-mail addresses

- **Found:** review of the per-tenant email webhook URLs (2026-09-27).
- **What:** `api/v1/webhooks.py` logs the recipient's e-mail address in
  `mailchimp_webhook_processed` and `moengage_webhook_processed` (shared
  URLs), and `_store_email_event` logs it in `workflow_event_match` and
  `email_event_redis_store_failed` on every URL. DSAR erasure
  (`audit/dsar.py`) does not reach application logs, so each processed event
  leaves a direct identifier there.
- **Fix:** log the event and campaign ids, or a keyed hash of the address,
  instead of the address itself.

## A-103 — The onboarding workflow's decision step presents grants nobody can hold

- **Found:** consuming governed-case decisions by request id ahead of the
  Grantex agent binding (2026-09-27).
- **What:** the `record_decision` step of
  `workflows/examples/business_onboarding.yaml` passes `$decision_grants` to
  `core/cases/runtime.py` `run_case_step`, which presents them to
  `POST /v1/decisions/consume`. Nothing in the workflow obtains them: the
  `human_in_loop` step yields only the outcome. With the issuer's
  `DECISION_GRANT_AGENT_BINDING` on, the grants of a request AgenticOrg makes
  (it names no agent) are never released to anyone, so the step can only be
  refused `decision_required`. `AGENTICORG_CASE_DECISION_GRANT_RELEASE` changes
  the console's route, not this step. It fails closed; it cannot succeed.
- **Fix:** let the step name a decision request (`decision_request_id`) and
  record it through the same checks the case API makes before consuming by id
  (the request is on this case, for this outcome, at this case version), then
  consume by request id; or drop `decision_grants` from the step and document
  that a workflow decision is recorded through the console.

## A-104 — Workflow connector steps and unstored workflow agents have no grant principal

- **Found:** covering run entry points for `grants.enforce_closed` (PRD F-1b,
  2026-09-15).
- **What:** a workflow `connector_tool` step calls a connector with no agent at
  all, and an agent step whose `agent_id` does not resolve to a stored agent
  runs as a synthetic `wf_agent_<step>` id. Neither has a Grantex agent
  registration, so no grant can be resolved for them: in `warn` every call is
  recorded as `grant_missing`/`no_agent` (or `lookup_failed`), in `deny` it is
  refused. A2A and MCP calls for an agent type with no shared agent of that
  type behave the same unless the caller brings its own Grantex token.
- **Fix:** give workflows a principal — register the workflow definition (or
  require a stored agent on every step) as a Grantex agent with scopes for its
  connector steps, and resolve the step's grant from it.

## A-105 — Nothing provisions or checks an application database role that row-level security binds

- **Found:** working out which role reads `feature_flags` in a deployment
  (2026-09-27).
- **What:** row-level security is a control only for a role that is neither a
  superuser nor `BYPASSRLS` (ADR 0002, revision `v6z16_rls_coverage`). The one
  provisioning script, `infra/gcp-setup-lean.sh`, points `AGENTICORG_DB_URL`
  at the Cloud SQL `postgres` administrator, the same secret the GKE migrate
  job reads, so the application runs as the migration role that owns every
  table; the Cloud Run services' role is configured outside the repository.
  Neither path creates a separate application role, and nothing at startup or
  in readiness reports whether the running role bypasses row-level security.
  The CI Postgres service runs every integration test as its superuser, so
  only the tests that create a probe role exercise the policies.
- **Fix:** provision separate migration and application roles (the
  application role `NOSUPERUSER NOBYPASSRLS`, DML only), log the running
  role's `rolsuper` / `rolbypassrls` at startup, and document the check in the
  deploy runbook.

## A-106 — The token pool's revocation listener times out every two seconds

- **Found:** CI logs of the dev stack while fixing the Grantex revocation
  check (2026-09-29).
- **What:** `auth/token_pool.py` listens on `agenticorg:token:revoke` with
  `pubsub.listen()` on a client built with `redis_socket_timeout_kwargs()`
  (2s socket timeout). An idle channel raises `TimeoutError` after 2s,
  `_supervise_revocations` logs `token_pool_revocation_listener_failed`,
  sleeps 1s and resubscribes, and the delay is reset on every subscribe, so the
  cycle repeats every ~3s for the life of the process. A token revocation
  published while the listener is not subscribed is lost (pub/sub keeps
  nothing), and the agent's cached token stays in Redis until it expires.
- **Fix:** give the listener its own client without a socket read timeout (or
  poll with `get_message(timeout=...)` and treat an idle timeout as normal),
  and keep the timeout only on command clients.

## A-107 — The legacy scope check calls Grantex on the event loop

- **Found:** same work (2026-09-29).
- **What:** `validate_tool_scopes` in `core/langgraph/agent_graph.py` calls
  `enforce_connector_grant` directly, unlike `check_tool_grant` and
  `ToolGateway`, which use `asyncio.to_thread`. From grantex 0.7 `enforce()`
  makes a synchronous HTTP request to `/v1/revocations/status` for every
  call (30s timeout, up to three retries with `time.sleep`), so one slow or
  unreachable auth service blocks the API's event loop for every request.
- **Fix:** run the call through `asyncio.to_thread` as the other two paths do,
  and decide per deployment between the online check and the SDK's revocation
  feed (`revocation_check="feed"`), which fails closed when it goes stale
  without a request per call.

## A-108 — `requirements.txt` pins a Grantex SDK the images do not install

- **Found:** same work (2026-09-29).
- **What:** `requirements.txt` pins `grantex==0.5.1`, but the API image, the
  tools image and the CI jobs install from `pyproject.toml`
  (`grantex>=0.5.1`) and got 0.7.0 as soon as it was published, which
  changed enforcement behaviour (revocation checked online by default) on
  `main` without a pull request. `pip-audit` audits the pin, not what runs.
- **Fix:** bound the SDK in `pyproject.toml` to the minor version tested
  (`grantex>=0.7,<0.8` once this change is in) and keep `requirements.txt` at
  the same version, so an SDK upgrade arrives as a reviewed dependency PR.

## A-109 — A tool-qualified scope is read by the SDK as the whole connector permission

- **Found:** running `make demo-case` (2026-10-01): the `screening_disposition`
  role, whose run grant carried only `tool:mock:read:screen_business` and
  `tool:mock:read:screen_person`, was allowed `ownership` on `mock`.
- **What:** agents are registered with per-tool scopes,
  `tool:<connector>:<permission>:<tool>` (`auth/scope_registry.py`), and the
  token pool delegates exactly those. The Grantex Python SDK's `enforce`
  (`_resolve_granted_permission`) splits a scope and reads `parts[2]` as the
  permission for `parts[1]`, ignoring a fourth segment, so any read scope on
  a connector covers every read tool of it. The TypeScript and Go SDKs read
  scopes the same way.
- **Impact:** the per-tool attenuation the governed-case design relies on
  ("each role with only its reference agent's read tools") was not enforced
  at the tool gateway: a run grant for one tool passed any tool of the same
  permission on the same connector. Purpose, caps and revocation were
  unaffected.
- **Fix (this change):** `auth.run_grants.tool_scope_denial` refuses, before
  the SDK is asked, a tool none of the grant's tool-qualified scopes for the
  connector names (`tool_not_granted` / `tool_scope_missing`; recorded and
  passed in `warn`). A connector-level scope keeps the SDK's reading.
- **Remaining:** the SDKs should honour the tool segment themselves (grantex
  FINDINGS G-144), behind a flag until the next major; the registry should
  refuse a grant request for a tool-qualified scope the agent did not
  register.

## A-110 — A paused agent can still be run directly (fixed, opt-in)

- **Found:** mapping the enforcement points for the operator override (2026-10-02).
- **What:** `POST /agents/{id}/run` refuses only `retired` agents; `paused`
  is respected by the task router and keyword chat routing, but a direct run,
  a chat with an explicit `agent_id`, an A2A task, an MCP call and a workflow
  agent step all run a paused agent. The console's per-agent pause therefore
  stops routing, not execution.
- **Fix:** refuse `paused` at the run endpoint and in the runner's step 0, behind
  a flag until the next minor; the operator override (`all_agents` or `agent`
  halt) is the control that stops execution today.
- **Fixed:** `AGENTICORG_PAUSED_AGENTS_REFUSED` (default off): the run endpoint
  and a chat that names the agent refuse a paused agent with 409, and a
  workflow agent step treats it as inactive (`core/governance/agent_status.py`).
  A2A and MCP run agents by type, not by registry row, and are unaffected.

## A-111 — The provider daily spend cap is never surfaced as an error status

- **Found:** same work.
- **What:** `DailyBudgetExceeded` says it is "surfaced as HTTP 429 by the API
  layer", but no handler catches it: `BaseAgent` folds it into a failed result
  with code `E5001`, the LangGraph runner returns `status: failed` with the
  message, and a direct run returns HTTP 500 when it escapes. Callers cannot
  tell a spend cap from a model outage.
- **Fix:** map `DailyBudgetExceeded` and `OperatorOverrideBlocked` to typed
  results (`E2008` budget, `E1013` override) in the runner and to 429 / 423 in
  the API error handlers.

## A-112 — The workflow replanner calls the model provider directly

- **Found:** same work.
- **What:** `workflows/replanner.py` imports the provider SDK and calls it
  directly, bypassing `LLMRouter` and `create_chat_model`: no tenant
  credentials, no routing policy, no spend cap, no pseudonymisation and no
  operator override apply to replanning calls.
- **Fix:** route the replanner through `LLMRouter.complete` with the run's
  tenant, behind a flag, and delete the direct SDK call.

## A-113 — The data-protection document describes residency controls that do not exist

- **Found:** mapping the residency enforcement points (2026-10-02).
- **What:** `docs/DPDP_ACT.md` ("Data localization") says inference is routed
  to one provider's foreign endpoint by default and that an
  `india_only_llm_routing` feature flag switches to a local model pack, citing
  `docs/INDIA_RESIDENCY.md`. Neither the flag nor that document exists, and
  `data_region` / `storage_region` were settings nothing read at runtime.
- **Fix (this change):** the paragraph now describes the real control
  (residency enforcement with provider attestations) and links to
  `docs/governance/data-residency.md`.

## A-114 — The compliance report claims internal mTLS by default (fixed)

- **Found:** same work.
- **What:** `GET /compliance/evidence-package` reports
  `encryption_in_transit.mtls_internal` from `AGENTICORG_MTLS`, which defaults
  to `"true"` and is read nowhere else, so the report asserts mutual TLS
  whether or not a mesh is configured.
- **Fix:** default the flag to false and have the deployment set it when a
  mesh with mutual TLS is in place, with the attestation recorded the same
  way as the residency deployment profile.
- **Fixed:** `AGENTICORG_MTLS` defaults to false; the deployment sets it when a
  mesh with mutual TLS is in place, and the mesh item (NET-04) is attested in the
  report's `infrastructure_controls` section like the other hosting controls.

