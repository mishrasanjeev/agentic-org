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
  in `ap_processor`, `jira(w:create_issue)` and `sanctions_api(r:batch_screen)`
  in `risk_sentinel`, `outlook(...)` in `email_agent`. The model reads them as
  capabilities. `scripts/check_prompt_tools.py` checks tool calls only and
  skips these lines because they are not tool names.
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

## A-15 — Existing files name commercial screening and business-data vendors

- **Found:** `python scripts/check_denylist.py audit` when adding the vendor
  denylist (2026-09-15).
- **What:** six tracked lines predate the vendor-neutral rule and name
  commercial vendors: two in `connectors/ops/sanctions_api.py`, one each in
  `core/agents/packs/insurance/prompts/underwriting_analyst.prompt.txt`,
  `docs/PRD_CxO_v5.0.md`, `docs/connector_production_readiness.md` and
  `scripts/generate_connectors.py`. The pull request check only looks at added
  lines, so these pass today but fail as soon as someone edits them.
- **Fix:** rename to provider-neutral terms (the sanctions connector's base URL
  and description become configuration or `acme_kyb`-style examples; the prompt
  and documents drop the vendor names), then confirm `audit` exits 0.

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

## A-28 — CLAUDE.md lists the preflight test suites without tests/contract

- **Found:** adding `tests/contract/` to the CI unit job and
  `scripts/preflight.sh` (2026-09-15).
- **What:** "Required Before Every Push" in `CLAUDE.md` still lists
  `pytest tests/regression/ tests/unit/ tests/security/ tests/connector_harness/`.
- **Fix:** add `tests/contract/` to that line.

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
  still calls `grantex.enforce(...)` directly inside the async graph node.
  `enforce` can fetch the JWKS with a synchronous HTTP request, blocking the
  event loop. The warn/deny path and `ToolGateway.execute` run it with
  `asyncio.to_thread`; the legacy path was left byte-for-byte unchanged so
  `off` keeps today's behaviour.
- **Fix:** run the legacy call through `asyncio.to_thread` too.

## A-38 — Workflow connector steps and unstored workflow agents have no grant principal

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

## A-40 — A built-in connector is tied to one commercial screening provider

- **Found:** `scripts/check_denylist.py audit` while removing vendor names
  from prompts and docs (2026-09-15).
- **What:** `connectors/ops/sanctions_api.py` (connector `sanctions_api`) and
  its generator entry in `scripts/generate_connectors.py` integrate with one
  commercial sanctions-screening provider: the module docstring and
  `base_url` name it. The release rules say no specific commercial
  verification or screening provider ships in this repository, and PRD §2
  lists that as a non-goal. The connector is live, so removing it breaks any
  tenant that configured it.
- **Fix:** move the connector into its own package that registers through the
  `agenticorg.connectors` entry point (plugin loading, F-6), keep
  `sanctions_api` resolvable during a deprecation window with a startup
  warning for tenants that use it, then delete the in-repo module and its
  generator entry. New screening integrations go through the
  `VerificationProvider` interface instead.

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

- **Found:** fixing the synchronous credential resolver (A-49, 2026-09-21).
- **What:** `core.database.run_db_coroutine_sync` lets a synchronous caller run
  a database coroutine on a private `NullPool` engine, and
  `get_tenant_session` / `get_session` honour it through
  `current_session_factory()`. Thirty-four other modules (89 references) bind
  `core.database.async_session_factory` directly instead. Those are correct on
  an async request path, but a coroutine reached from a synchronous bridge
  through one of them still borrows the shared pool, which is the defect A-49
  described; only the paths the fixed bridges reach were moved to
  `current_session_factory()`. Four of them are worse than the rest because
  they capture the factory **by value into a module-level singleton**, so the
  binding survives every later call and no context override can reach it:
  `core/live_feed.py:365`, `workflows/state_store.py:309`,
  `workflows/event_waits.py:377` and `bridge/state.py:1147`. Any new
  synchronous bridge that `asyncio.run`s a coroutine reaching one of these
  reopens A-49.
- **Fix:** have the session helpers be the only way to open a session (make
  `async_session_factory` private and route every caller through
  `current_session_factory()`), starting with the four singletons, which
  should resolve the factory per call as `core/cdc/receiver.py` now does; or
  add a check that refuses a direct import of `async_session_factory` outside
  `core/database.py`.

## A-54 — The Celery runner loop is unsafe in a process that also serves requests

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
  Every counter is therefore process-local and unobservable, so an alert on a
  denial-rate spike or on dwell collapsing towards zero (PRD §10) cannot be
  built from them, and each new metric adds an instrument nobody can read.
- **Fix:** mount the Prometheus ASGI app behind the platform's own
  authentication (or export through the existing observability pipeline),
  document the endpoint, and make one alert from an existing counter to prove
  the path end to end.

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
  same terms as a new one.
