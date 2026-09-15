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

## A-32 — Registered Grantex scopes use a permission `enforce` cannot satisfy

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

## A-33 — The token pool's refresh path calls a grant type Grantex does not serve

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

## A-34 — Legacy scope validation calls the blocking `enforce` on the event loop

- **Found:** adding warn/deny modes to `validate_tool_scopes` (2026-09-15).
- **What:** in `off` mode `core/langgraph/agent_graph.py::validate_tool_scopes`
  still calls `grantex.enforce(...)` directly inside the async graph node.
  `enforce` can fetch the JWKS with a synchronous HTTP request, blocking the
  event loop. The warn/deny path and `ToolGateway.execute` run it with
  `asyncio.to_thread`; the legacy path was left byte-for-byte unchanged so
  `off` keeps today's behaviour.
- **Fix:** run the legacy call through `asyncio.to_thread` too.
