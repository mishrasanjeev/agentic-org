# Changelog

All notable changes to AgenticOrg are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - 2026-08-29

### Added
- **Coverage gate (pull requests):** 75% of changed lines (diff-cover) and 75%
  of every new Python module (`scripts/check_new_module_coverage.py`; a new
  module no test imports counts as 0%), on top of the existing 55% total and
  per-module floors. Tests, test doubles and fixtures are not counted, renamed
  modules count as new, and a coverage report whose filenames cannot be
  attributed to exactly one module fails the gate. Runs as
  `make coverage-gate` and in the new Local Stack workflow. **Break:** pull requests that add or change Python code below
  these floors now fail.
- Local Stack workflow: `make check`, and `make dev && make test` followed by
  the coverage gate, `make seed` and `make e2e`, on fresh runners for every
  pull request and push to `main`.
- `pip-audit` now runs through `scripts/run_pip_audit.py` in CI, nightly and
  `make check`, with dated, owned exceptions in
  `config/pip-audit-exceptions.toml` (at most 90 days; expired or malformed
  entries fail). A dependency pip-audit could not audit fails too unless a
  `[[skip]]` entry accepts it.
  See "Dependency audit exceptions" in `CONTRIBUTING.md`.
- Nightly Cassette Re-record workflow: re-records every `model_cassette` test
  against a live model with the `MODEL_RECORD_API_KEY` secret and reports
  cassette differences without gating. Its dependencies are hash-pinned
  (`requirements-rerecord.lock`). See `docs/testing/record-replay.md`.
- Local Grantex in the development stack: the Grantex auth service from its
  published image, pinned by digest, with its own `grantex` role and database
  on the stack's Postgres (created once by `grantex-db`) and Redis index 2.
  The API and worker use it through `GRANTEX_BASE_URL` and a seeded
  development `GRANTEX_API_KEY`. The smoke test checks its health and keys and
  that the API container reaches it with the configured key. See "Local
  Grantex" in `docs/quickstart-local.md`.
- OpenAI-compatible model stub in the local stack (`model-stub`,
  `tools/model_stub`): `POST /v1/chat/completions` with tool calls, answered
  from scripted sequences (`scripted/<name>`, ids matching the in-process
  scripted model) or from cassettes keyed and stored by `core/model_replay.py`.
  Options that change the answer (`tool_choice`, `response_format`, `seed`, ...)
  are part of the key and unknown request fields are rejected. Replay misses
  are errors and are never forwarded; record mode forwards to a
  real provider and refuses to start without `MODEL_RECORD_API_KEY`. The API
  and worker send `vllm:` models to it, and the agents `make seed` creates use
  `vllm:scripted/final-only`, so agents run locally without model credentials.
  Refuses to start outside development and test. See "Model stub" in
  `docs/quickstart-local.md`.
- `make seed` (`scripts/seed_dev.py`): an idempotent development tenant with
  Approver A and Approver B (the OIDC stub's identities, matched by email), a
  disabled `dev-oidc` sign-in configuration for the stub, two sample agents in
  shadow mode with no tools, and a two-step sequential approval policy (it does
  not require distinct approvers). Fixed ids make repeated runs a no-op; a
  conflicting existing row fails the run without writes; it refuses
  production-like runtimes and non-local database hosts unless
  `AGENTICORG_SEED_ALLOW_REMOTE_DB=1`. Optional
  `AGENTICORG_SEED_PASSWORD` enables email sign-in. See "Development data" in
  `docs/quickstart-local.md`.
- Development OpenID Connect provider in the local stack (`oidc-stub`,
  `tools/oidc_stub`): discovery, JWKS, authorization code with mandatory PKCE,
  token and userinfo endpoints, and step-up through `acr_values`, `max_age`
  and `prompt=login`, with `acr`, `amr` (`["pwd"]` or `["pwd", "hwk"]`) and
  `auth_time` in its tokens. Seeds Approver A and Approver B from
  `tools/oidc_stub/config.dev.json`. Refuses to start unless `AGENTICORG_ENV`
  is development, local or test. Sessions last eight hours, repeated request
  parameters are rejected, and step-up clients must send `max_age`. `tools/` is
  excluded from the API image. See "Development identity provider" in
  `docs/quickstart-local.md`.
- Vendor-name denylist: `scripts/check_denylist.py` fails a change whose added
  lines, file paths, commit messages, branch name or pull request title and
  description name a denylisted verification, identity-data or screening
  vendor. Terms are matched through salted SHA-256 hashes in
  `config/denylist.sha256` (80 terms; the plain list is not committed, though
  the salted hashes are not secret), independent of case, spacing, punctuation
  and a term glued to the end of a word. Runs in the new Vendor Denylist workflow
  and in `make check`; `audit` checks the whole tree. See "Vendor-neutral
  names" in `CONTRIBUTING.md`.
- `make test`, `make check` and `make e2e`. `make test` runs the unit and
  contract suites with the 55% coverage floor, then the integration and
  regression suites against the local stack's Postgres and Redis in a separate `agenticorg_test`
  database that is recreated each run (the development database is never
  touched). `make check` runs ruff, mypy, bandit, gitleaks, the licence-header
  check, JSON Schema validation of `schemas/` and pip-audit. Both run in a new
  `agenticorg-tools` image (`Dockerfile.tools`, Python 3.12) so only Docker
  and make are needed; `RUNNER=local` uses a local interpreter. `make e2e`
  runs the new `ui/e2e/dev-stack.config.ts` Playwright suite against the
  running stack in the official Playwright image. See "Tests and checks" in
  `docs/quickstart-local.md`.

- Untrusted content extractor (`core/extraction/`): websites, registry
  documents and applicant uploads are parsed in a separate worker process
  with no network access and a wall-clock limit (on Linux a seccomp filter is
  required by default; an audit hook, resource limits and a network namespace
  are added where available) and return typed, length-capped,
  character-class-constrained fields only. Excerpts are stored separately and
  cited by `excerpt_ref`. Timeouts, crashes, oversized or off-schema output
  fail closed with a reason code and no fields. `build_model_context` renders
  evidence for a model with untrusted text replaced by references, and the
  new optional `build_agent_graph(context_guard=...)` stops a run before any
  model call that would carry untrusted text. Metrics
  `agenticorg_extraction_total{kind,outcome}` and
  `agenticorg_extraction_duration_seconds{kind}`. Nothing calls the extractor
  yet and `context_guard` defaults to `None`, so existing behaviour is
  unchanged. PDF and office documents are refused, not parsed. See
  `docs/security/untrusted-content.md`.
- Deterministic case policy engine (`core/policy/`): versioned YAML policies
  evaluated over a case's evidence fields into a tier (`low` < `medium` <
  `high` < `blocked`), a score and ordered reasons naming the rules that fired,
  with the policy version, file hash and inputs recorded in every result. No
  model is involved and model confidence is never an input. Policies load
  strictly and fail closed at load with a reason code; missing evidence moves
  a case towards the stricter tier. A policy can only be marked `production`
  with `reviewed_by`, and loading an example policy logs a warning. Ships
  `business_onboarding_us` and `business_onboarding_uk` **examples, which
  require a compliance owner's review before any real use**. Metrics
  `agenticorg_policy_evaluations_total{tier,policy_status}` and
  `agenticorg_policy_load_total{outcome,reason}`. Nothing calls the engine
  yet, so existing behaviour is unchanged. See `docs/policies/authoring.md`
  and ADR 0011.
- Secret scanning with gitleaks 8.30.1 on every pull request, every push to
  `main` and weekly over the full history, plus a pre-commit hook and a
  `scripts/preflight.sh` step (`SKIP_SECRETS=1` to skip). See "Secret
  scanning" in `CONTRIBUTING.md`.
- New source files must carry `SPDX-License-Identifier: Apache-2.0` in their
  first five lines; enforced on pull requests and in `scripts/preflight.sh`.
  Existing files are unaffected. See "Licence headers" in `CONTRIBUTING.md`.
- Container scanning of both the API and console images on every pull
  request, push to `main` and nightly (previously the API image only, nightly
  only), with a pinned Trivy 0.74.0, dated exceptions in `.trivyignore.yaml`
  and a CycloneDX SBOM artifact per image. See "Container scanning" in
  `CONTRIBUTING.md`. The console image's seven base-image `libuuid` findings
  are tracked in `FINDINGS.md` with exceptions expiring 2026-10-14.
- One-command local stack: `make dev` builds and starts Postgres, Redis,
  MinIO, migrations, the API, the worker and the console from
  `docker-compose.dev.yml` (base images pinned by digest, ports bound to
  127.0.0.1, no credentials needed), waits for health and runs a smoke test.
  `make down`, `make clean`, `make logs` and `make ps` manage it. See
  `docs/quickstart-local.md`.
- `ScriptedChatModel` and the `scripted_model` test fixture: fixed tool-call
  sequences for testing agent graph mechanics (tools, interrupts, resume)
  without model text. A script that is overrun, calls an unbound tool or is
  left partly unused fails the test. See `docs/hermetic_test_doubles.md`.
- Record and replay for model calls (`AGENTICORG_MODEL_MODE` =
  `live`/`record`/`replay`) covering LangGraph agents and `LLMRouter`
  completions. Cassettes are keyed by a hash of the rendered request, so a
  prompt, tool or tool-output change misses loudly instead of replaying stale
  text; replay never falls back to a live call and both non-live modes are
  refused outside local and test runtimes. Tests opt in with the
  `model_cassette` fixture and replay by default in CI. Production behaviour
  is unchanged when the variable is unset. See
  `docs/testing/record-replay.md` and ADR 0008.
- Connectors and agents can ship as separate packages through the
  `agenticorg.connectors` and `agenticorg.agents` entry-point groups
  (`agenticorg.workflows` is discovered and rejected until its registry
  exists). Off by default
  (`AGENTICORG_PLUGIN_LOADING`); only distributions in
  `AGENTICORG_PLUGIN_ALLOWLIST` are imported; native implementations keep
  priority; every rejection is logged with a reason and counted in
  `agenticorg_plugin_load_total`. See `docs/providers/plugin-packages.md`.
- Agent runs paused for human approval can be checkpointed in Postgres
  instead of process memory: `AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres`
  (default `memory`, unchanged behaviour). A new migration
  (`v6z22_langgraph_checkpoints`) creates the LangGraph checkpoint tables;
  they are not created at runtime. Checkpoint data is encrypted with the
  credential-vault keyring and bound to its thread, so a blob copied into
  another thread is refused; no channel value is stored in plaintext. With
  the Postgres store selected and unreachable, its schema missing or stale,
  its keyring malformed, or an unverified checkpoint library installed, the
  API refuses to start and agent runs fail (the run endpoint returns
  `503 agent_checkpoint_store_unavailable`); nothing falls back to memory.
  Celery workers open the store on their first agent run, so a store outage
  fails those runs but never stops a worker from starting. Refusals are
  counted in `agenticorg_checkpointer_unavailable_total` by reason. A keyring
  change needs a restart of the API and workers.
  `core.langgraph.checkpointer.delete_tenant_checkpoints` removes a tenant's
  checkpoints for offboarding. Adds `psycopg[binary]` 3.3.5 and `psycopg-pool`
  3.3.1 as direct dependencies and pins `langgraph-checkpoint-postgres` 3.1.2
  and `langgraph-checkpoint` 4.2.0 exactly (previously `>=3.1.2` and
  unpinned).
- Agent runs checkpoint under a server-generated thread id prefixed with the
  run's tenant (`tenant:<tenant id>:run:<random>`). A run paused for approval
  records that thread on its approval row (`hitl_queue.checkpoint_thread_id`,
  migration `v6z23_hitl_checkpoint_thread`, with a check constraint that the
  thread belongs to the row's tenant). The thread id is never returned by the
  API or accepted from a request, and resuming a thread outside the caller's
  tenant is refused (`checkpoint_thread_tenant_mismatch`).
- Approving a paused standalone agent run can resume it from its checkpoint,
  behind the per-tenant feature flag `approvals.resume_agent_runs` (default
  off; decisions behave as before). With the flag on, an `approve` or `reject`
  decision resumes the run in the background under the approval's tenant,
  using the parameters recorded when the run paused. The outcome is recorded
  in the approval's `context.checkpoint_resume`, in an `agent.run.resumed`
  audit event and in `agenticorg_agent_run_resumes_total{outcome}`, and a
  finished run's checkpoints are deleted. Any other decision leaves the run
  paused; an approve or reject left paused because the flag is off or cannot
  be read is logged (`agent_run_resume_skipped`) and counted as
  `outcome="skipped"`. A resume is refused, with a reason code, when the checkpoint is
  missing, not at the approval gate, undecryptable or outside the tenant.
  Approval responses gain `context.checkpoint_resume` for resumed runs; the
  resume parameters stored with a paused run are never returned. See
  "Agent runs paused for approval" in `docs/RUNBOOKS.md` for the flag,
  reason codes and checkpoint retention.
- Governed-case domain schemas (JSON Schema 2020-12, versioned `$id`s):
  `business_case`, `ownership_graph`, `screening_result`,
  `screening_disposition`, `policy_result`, `underwriting_memo` and
  `case_push`, with shared definitions in `common`. Every memo section and
  finding cites `evidence[]` of `{provider, record_id, field, retrieved_at,
  excerpt_ref}`. `core/domain_schemas.py` validates documents and fails closed
  with a reason code. A new `tests/contract/` suite, added to the CI unit job
  and `scripts/preflight.sh`, validates every fixture in `schemas/examples/`,
  fails on a fixture without a schema, and checks that documentation code
  examples match the tests they come from. The schemas are not seeded into
  tenant schema registries. See `docs/schemas/domain-schemas.md`.
- `VerificationProvider` (`connectors/framework/verification_provider.py`):
  one provider-neutral interface for business resolution, verification,
  ownership, person and business screening, web presence and monitoring.
  Providers declare a `Capability` set and callers degrade an undeclared
  capability to `not_available` (`call_capability`); verification is
  start-and-poll with `Pending` as a value; every I/O method takes a
  `Deadline`; errors form a closed taxonomy with reason codes; webhook
  verification returns `None` for anything unverifiable. Typed domain values
  serialise to the published schemas. Providers register in
  `connectors/providers/registry.py`, and plugin packages add them through the
  `agenticorg.providers` entry-point group (still behind
  `AGENTICORG_PLUGIN_LOADING` and the allowlist; natives keep priority). See
  ADR 0009 and `docs/providers/plugin-packages.md`.
- The `mock` verification provider (`connectors/providers/mock`), registered
  natively: twelve synthetic US and UK businesses covering clean cases, a
  missing and an undeclared owner, probable false-positive and true-match
  screening hits, a dissolved company, a thin file with no registry match, and
  adversarial text in website copy, a company name and a screening alias.
  Configurable latency, failure injection and pending polls, deterministic
  under a seed; HMAC-signed webhook events (including company dissolved) with
  recorded genuine and forged deliveries. It runs in-process or as a separate
  HTTP service with a client provider; `make dev` now starts it as
  `mock-provider` (host port `AGENTICORG_DEV_MOCK_PROVIDER_PORT`, default 8081;
  fault-injection and event endpoints only with
  `AGENTICORG_DEV_MOCK_PROVIDER_ADMIN=true`) and points the API and worker at
  it. It runs only when `AGENTICORG_ENV` is explicitly local, development or
  test; elsewhere the registry neither lists nor creates it. See
  `docs/providers/mock-provider.md`.
- Provider conformance suite, published in the full distribution as
  `agenticorg.testing.provider_conformance` (source: `testing/provider_conformance`).
  A provider package subclasses `ProviderConformanceSuite` and supplies a
  `ConformanceTarget`; twelve checks cover identity, capability honesty,
  pending-then-result, expired and overrun deadlines (including polls),
  cancellation, the error taxonomy, webhook verification including forged
  payloads, webhook replay protection (stale deliveries, stable event ids),
  pagination of candidates and monitor alerts, idempotency and schema
  conformance, each failing with a readable reason. `strict=True` turns a
  skipped check into a failure. The mock provider passes strictly in-process
  and over HTTP; deliberately broken providers fail each check. Documentation code examples are extracted from tests. See
  `docs/providers/writing-a-verification-provider.md`.
- Python and TypeScript SDK `0.4.0` resources for knowledge/OCR, voice, RPA,
  local bridges, connector diagnostics, workflow cancellation, and the
  seller/buyer commerce runtime.
- An idempotent migration that repairs the native `knowledge_documents` index
  on both legacy and ORM-bootstrap installations.
- Recognisers for United States SSN, ITIN and EIN, United Kingdom National
  Insurance and Companies House numbers, European VAT numbers and IBANs
  (`core/pii/international_recognizers.py`), alongside the Indian ones. IBANs
  must pass mod 97 and VAT numbers their national check digits where the
  scheme has one (17 country prefixes); shapes that are otherwise ordinary
  numbers are only recognised next to a label such as "SSN" or "company
  number". Used by pre-model pseudonymisation (below).
- Pseudonymisation before the model, per tenant behind the flag
  `pseudonymisation.pre_model` (off by default). Names, dates of birth,
  addresses and identifiers are replaced with placeholders such as
  `[[PERSON_1:3fa9c2]]` before every model call on both model paths
  (LangGraph agents and `LLMRouter`), system prompt included, and restored
  inside the tool boundary so connectors receive the real values. A value
  keeps its placeholder for the run and its resumes; the case is always the
  server-generated run id, never a `case_id` from a request, and only
  placeholders issued into the run's own conversation are restored. The map is
  stored encrypted per tenant in the new `case_pseudonym_maps` table (migration
  `v6z24_case_pseudonym_maps`, additive, row-level security). Fails closed: if
  the flag or the map cannot be read, or the map cannot be written, no model
  call is made; a tool call whose placeholder cannot be restored is refused
  with `E1012 pseudonym_restore_failed` and audited instead of being sent. New
  metrics `agenticorg_pii_pseudonymised_total{entity_type}`,
  `agenticorg_pii_pseudonym_restore_refused_total{reason}` and
  `agenticorg_pii_pseudonymisation_unavailable_total{reason}`. New
  `core.feature_flags.is_enabled_strict`, which raises on a failed lookup
  instead of returning the default. With the flag off behaviour is unchanged.
  See `docs/security/pseudonymisation.md`.
- HITL conditions can be checked when they are saved
  (`AGENTICORG_HITL_CONDITION_VALIDATION` = `off`/`warn`/`reject`, default
  `off`). Agent create, replace, update, generate-and-deploy and SOP deploy
  answer `422 invalid_hitl_condition` with a reason code (`syntax_error`,
  `unsupported_syntax`, `unsupported_operator`, `not_a_comparison`) in
  `reject`; `warn` accepts the condition but logs and counts it. Parse
  failures at save and at run time are counted in
  `agenticorg_hitl_condition_parse_failures_total` (labels `stage`, `reason`,
  `outcome`). **Break when set to `reject`:** a bare label such as
  `needs_review` must be written as a comparison (`needs_review == True`).
  See `docs/hitl-conditions.md`.
- `scripts/check_prompt_tools.py` fails CI (unit-tests job) and
  `scripts/preflight.sh` when a built-in agent prompt calls a tool that no
  connector registers or that is not in that agent's default tools, or when a
  default tool list names an unregistered tool. It runs with no baseline and
  fails closed if the registry cannot be loaded. See "Prompt tool references"
  in `CONTRIBUTING.md`.
- Grant enforcement modes for agent tool calls (`grants.enforce_closed`:
  `off`, `warn`, `deny`). The mode is the strictest of
  `AGENTICORG_GRANTS_ENFORCE_CLOSED` (default `off`, which keeps today's
  behaviour) and the global and tenant rows of the
  `grants.enforce_closed.warn` / `.deny` flags, each read on its own. In
  `warn`, runs from `POST /agents/{id}/run` and other callers of the LangGraph
  runner resolve a grant per run — the caller's token, the agent's configured
  token, or one the token pool now mints by delegating from
  `GRANTEX_ROOT_GRANT_TOKEN` to the agent's registered Grantex agent (cached
  per tenant, agent and scope set, refreshed before it expires, at most one
  mint per key per process) — and every tool call that grant would deny still
  runs (a token supplied by the caller or configured on the agent stays
  enforced as before) but is logged as `grant_enforcement_would_deny` and
  counted in `agenticorg_grant_enforcement_denials_total{mode,reason}` with
  the Grantex SDK's reason (exact messages of the pinned 0.5.x SDK mapped to
  the Appendix B reasons until the Grantex 0.6 SDK with reason codes is
  published), including runs with no grant at all
  (`grant_missing`). If the flag table cannot be read and the process has no
  recent mode for the tenant, the run falls back to the strictest mode.
  `deny` is not switchable yet and runs as `warn`. See
  `docs/operations/grant-enforcement.md`.
- The Grantex SDK pin moves from `grantex==0.5.0` to `grantex==0.5.1`
  (amount and malformed-cap checks in `enforce`; no API change).
- **Break:** the authority flags (`grants.enforce_closed.*`,
  `pseudonymisation.pre_model`, `approvals.resume_agent_runs`,
  `decisions.required`, `caps.enforce`) can no longer be created, changed or
  deleted through `/api/v1/feature-flags`; the API answers
  `403 flag_key_reserved`. Platform operators manage them with
  `scripts/authority_flags.py`.
- **Break (Python API):** `core.langgraph.agent_graph.build_agent_graph` and
  every `build_*_graph` builder in `core/langgraph/agents/` take a required
  keyword `run_grant`, so a graph can no longer be built with grant
  enforcement silently left off.

### Fixed
- Four shipped industry-pack agents no longer send every run to human review.
  Their HITL conditions were bare labels (`high_value_or_complex_risk`,
  `high_value_or_fraud_indicator`, `cancellation_or_major_endorsement`,
  `high_value_procurement`) that name no output key, so the fail-closed
  evaluator triggered on each run. They are now expressions over the keys in
  each prompt's output format; see `docs/hitl-conditions.md` for the
  thresholds. Agents already installed keep the old label until the pack is
  re-synced.
- The console images (`Dockerfile.ui`, `Dockerfile.ui.cloudrun`) report
  healthy. Their Docker healthcheck probed `localhost`, which resolves to
  `::1` in the nginx:alpine base while nginx listens on IPv4, so the
  containers showed unhealthy while serving traffic and anything waiting on
  their health never proceeded. The probe now requests
  `http://127.0.0.1/health`.
- Knowledge deletion now retires native vector chunks as well as RAGFlow and
  document records, preventing deleted content from remaining searchable.
- Plural billing callback OpenAPI operations now have unique GET/POST IDs for
  generated SDK clients.
- Connector harness and voice integration fixtures no longer emit avoidable
  async/Pydantic deprecation warnings.
- Agent default tool lists (`_AGENT_TYPE_DEFAULT_TOOLS` and
  `_DOMAIN_DEFAULT_TOOLS` in `api/v1/agents.py`, and the agent generator's
  copy) no longer name 20 tools that no connector registers, such as
  `get_post_analytics`, `schedule_social_post`, `slack_send_message` and
  `search_content_fulltext`. Those names were never bound at run time but
  were shown in the tool picker, MCP/A2A discovery and generated agents.
  Nothing is added in their place. `seo_strategist` is left with no default
  tools, so `POST /mcp/call` for `agenticorg_seo_strategist` now answers 400
  "No tools configured" instead of running an agent with no tools.
- Default tools that several connectors register now name the connector the
  agent's prompt uses, so they no longer resolve to whichever connector was
  imported first: `create_issue` is `jira:create_issue` for `vendor_manager`
  and `facilities_agent` (it resolved to GitHub), `query` and
  `search_contacts` are `salesforce:` for `abm` (it resolved to QuickBooks and
  HubSpot), and `get_analytics`, `create_page`, `send_email`,
  `create_campaign`, `create_incident` and `get_compliance_notice` are
  qualified for the agents that name LinkedIn Ads, Confluence, SendGrid or
  Gmail, Mailchimp, ServiceNow or GSTN. Accounting and HRMS tools shared by
  interchangeable systems (`get_trial_balance`, `get_employee`, ...) stay bare
  and resolve through the connectors linked to the agent.
  `GET /agents/default-tools/{type}` keeps a qualified default only when its
  connector is linked, and Grantex scopes for a qualified tool now name that
  connector (`tool:jira:execute:create_issue`); an unknown connector never
  matches.
- **Behaviour change:** the finance agent prompts (`ap_processor`,
  `ar_collections`, `close_agent`, `fpa_agent`, `recon_agent`,
  `tax_compliance`) no longer tell the model to call tools that do not exist
  (`ocr_extract_invoice`, `gstn_validate`, `erp_get_po`, `erp_get_grn`,
  `erp_queue_payment`, `erp_get_ar_aging`, `erp_get_transactions`,
  `banking_api_get_transactions`, ...). Steps now use the agent's registered
  tools, or read the data from the task input and escalate to human review
  when it is not there: AP needs the extracted invoice, the GSTIN verification
  result and the PO/GRN details in the input; close needs sub-ledger balances;
  FP&A needs the budget; reconciliation needs the GL entries; tax compliance
  needs the period's transactions. AP no longer sends remittance advice,
  reconciliation proposes entries instead of posting them, and AR hands Day
  60+ contact to the collections team.
- **Behaviour change:** the HR agent prompts (`ld_coordinator`,
  `offboarding_agent`, `onboarding_agent`, `payroll_engine`,
  `performance_coach`, `talent_acquisition`) no longer tell the model to call
  tools that do not exist (`get_performance_data`, `okta_deactivate_user`,
  `okta_provision_user`, `jira_create_issue`, `get_okr_progress`) or that the
  agent does not have (`get_leave_balance`, `check_availability`). Steps now
  use the registered equivalents (`deactivate_user`, `provision_user`,
  `assign_group`, `create_page`, `get_employee`) or read performance data,
  OKR progress, leave balances, panel availability and the separation record
  from the task input and escalate to human review when they are missing.
  Actions with no tool (equipment requests, course enrolment, GitHub/Jira/
  Slack removal, data archival) are listed for the responsible team instead
  of being claimed as done.
- **Behaviour change:** the marketing agent prompts (`brand_monitor`,
  `content_factory`, `crm_intelligence`, `seo_strategist`, `social_media`) no
  longer tell the model to call tools that do not exist
  (`get_brand_mentions`, `ahrefs_get_keywords`, `get_contacts`,
  `ahrefs_get_rankings`, `get_post_analytics`). CRM scoring uses
  `list_contacts` / `search_contacts`; social listening uses
  `get_campaign_insights` and `list_channel_videos`; brand mentions, keyword
  research, rankings and post analytics are read from the task input, with
  escalation to human review when they are missing. The SEO strategist
  writes ticket-ready recommendations instead of claiming to create Jira
  tickets.
- **Behaviour change:** the `vendor_manager` and `support_triage` prompts no
  longer tell the model to call `sanctions_screen`, `gstn_validate` and
  `mca_get_company_data` (not registered) or `get_ticket` (not in the
  agent's tools). Vendor onboarding now requires the sanctions screening,
  GSTIN verification and company registry results in the task input and
  stops for human review when any is missing; SLA monitoring uses
  `search_issues` and `get_project_metrics`. Support triage reads the ticket
  from the task input and uses `apply_macro` / `update_ticket`.

## [4.0.0] — 2026-04-05

### Added — Project Apex (22 Features)
- **1000+ Integrations**: Composio SDK (MIT) connector expansion layer alongside 54 native connectors
- **Smart LLM Routing**: RouteLLM multi-model routing across 3 tiers (85% cost savings)
- **Pre-LLM PII Redaction**: Microsoft Presidio anonymizes Aadhaar, PAN, GSTIN, UPI before data reaches LLM
- **NL Workflow Builder**: Describe business processes in English, auto-generates workflow
- **Persona Builder**: Describe the employee you need, auto-generates full agent config
- **Explainable AI**: Plain-English decision explanations with Flesch-Kincaid readability scoring
- **Self-Improving Agents**: Feedback loop with thumbs up/down, automatic prompt refinement
- **Dynamic Re-planning**: Workflows adapt when steps fail — LLM generates alternative plan
- **Voice Agents**: LiveKit + Pipecat foundation with Whisper local STT, SIP telephony
- **Browser RPA**: Playwright-based automation for legacy web portals (EPFO, MCA, Income Tax)
- **Multi-Language**: Hindi (HI) + English (EN) with language picker in header
- **Content Safety**: PII leakage + toxicity + duplicate detection on generated content
- **Air-Gapped Deployment**: Ollama (CPU) + vLLM (GPU) local LLM, zero internet required
- **Hosted Tier**: Stripe (global) + PineLabs Plural (India) billing with Free/Pro/Enterprise plans
- **Microsoft 365**: Teams bot + Outlook/SharePoint/OneDrive via Composio
- **Multi-Agent Collaboration**: Parallel agent execution with merge/vote/first_complete aggregation
- **Support Deflection Agent**: 60%+ auto-resolution with RAG knowledge base + FAQ matching
- **Industry Packs**: Healthcare, Legal, Insurance, Manufacturing — one-click install
- **SOC2/ISO 27001**: 10-point compliance control framework with evidence package API
- **Enterprise Onboarding**: 4-week guided deployment playbook with milestone tracking
- **Real-Time CDC**: Webhook + polling change data capture with workflow triggers
- **Billing Portal**: Usage meters, plan management, invoice history, India INR pricing toggle

### Changed
- Agents: 35 → 50+ (industry packs + support deflection + voice)
- Tools: 54 → 1000+ (Composio MIT integration)
- Workflows: 15 → 20+ (NL-generated + adaptive replanning)
- Dependencies: composio-core, routellm, presidio-analyzer, presidio-anonymizer (all optional [v4] group)
- UI: react-i18next for multi-language, Billing page, enhanced Onboarding
- Backend tests: 1,662 → 1,931+ (269 new tests across 22 features)
- Playwright E2E: 342 → 345+
- Version: 3.3.0 → 4.0.0

## [3.3.0] — 2026-04-04

### Added — Scope Enforcement Fix (Grantex SDK v0.3.3)
- **Manifest-based scope enforcement**: Replaced keyword-based permission guessing (`check_scope()`) with Grantex SDK `grantex.enforce()` — offline JWT verification + manifest permission lookup in <1ms per tool call
- **`validate_scopes` graph node**: New LangGraph node between `should_use_tools` and `execute_tools` that enforces Grantex scopes on every tool call. Graph flow: `reason → validate_scopes → execute_tools` (was: `reason → execute_tools`)
- **53 pre-built Grantex manifests**: All connector tool permissions loaded at startup from `grantex.manifests.*` — no manual permission mapping needed
- **Custom manifest support**: Load additional manifests from `GRANTEX_MANIFESTS_DIR` directory (JSON/YAML)
- **JWKS cache warm-up**: Dummy `enforce()` call at FastAPI startup pre-warms the JWKS cache (~300ms) so first real tool call is <1ms
- **Scope Dashboard** (`/dashboard/scopes`): New page showing all agents' scope coverage, permission levels, denial rates, and aggregate stats
- **Enforce Audit Log** (`/dashboard/enforce-audit`): Real-time feed of all `enforce()` decisions with filters (denied only, by agent, by connector), CSV export, pagination
- **Permission badges in AgentCreate**: Tool selector shows READ/WRITE/DELETE/ADMIN permission badges; yellow warning banner for destructive (DELETE/ADMIN) tools
- **Scopes tab in AgentDetail**: New tab showing resolved Grantex scopes, permission levels, grant token status (active/expiring/expired), and enforcement log
- **Org chart scope narrowing**: Visual indicators showing scope reduction in delegation chains (e.g., "write → read")
- **ToolGateway Grantex integration**: `execute()` now accepts `grant_token` parameter and uses `grantex.enforce()` as primary enforcement; legacy `check_scope()` retained as fallback for HS256 tokens

### Changed
- **Grantex SDK**: 0.2.5 → **0.3.3** (adds `enforce()`, `load_manifests()`, `load_manifests_from_dir()`, 53 pre-built manifests)
- **`check_scope()` deprecated**: `auth/scopes.py` now emits `DeprecationWarning` — use `grantex.enforce()` instead
- **Permission hierarchy**: Enforcement now uses Grantex's manifest-defined hierarchy (`admin > delete > write > read`) instead of keyword-guessing (`process_refund` was misclassified as "read")
- **Security**: LangGraph agents can no longer bypass scope restrictions — `grant_token` is verified at graph level before any tool executes
- Backend tests: 1,633 → **1,662** (29 new scope enforcement tests: 18 unit + 4 integration + 3 E2E + 4 UI)
- Version: 3.2.0 → **3.3.0**

### Fixed
- **Critical security fix**: LangGraph tool execution path (`ToolNode → _execute_connector_tool()`) now enforces Grantex scopes — previously, `grant_token` in `AgentState` was never read during tool execution
- **`process_refund` misclassification**: Keyword-based `check_scope()` classified `process_refund` as "read" (no write keyword match); manifest-based enforcement correctly identifies it as WRITE
- **Revoked token bypass**: Revoked grant tokens now fail JWT verification at `validate_scopes` node — previously, tools were built from a static list and ignored token revocation

## [3.2.0] — 2026-04-02

### Added — Tier 1: Marketing Automation
- **Web Push Notifications**: One-tap approve/reject HITL decisions from browser push notifications (ServiceWorker + VAPID). Notification bell dropdown in dashboard header. Push permission toggle per user
- **Email Drip Engine**: Behavior-triggered email sequences — trigger on open, click, or time delay. Re-engage non-openers. Rescore leads after drip completion. New `email_drip_sequence` workflow template
- **A/B Testing**: Create campaign variants, auto-select winners by open rate or CTR, CMO override before sending to remaining audience. New `ab_test_campaign` workflow template
- **Email Webhooks**: SendGrid, Mailchimp, and MoEngage open/click tracking via inbound webhooks (`POST /webhooks/email/{provider}`). Events stored and linked to drip sequences
- **Intent Data Aggregation**: Bombora + G2 + TrustRadius connectors with weighted scoring (40/30/30) for account-level buying signals
- **ABM Dashboard** (`/dashboard/abm`): Target account management, intent heatmap, CSV upload, tier filtering, and one-click campaign launch. Endpoints: `GET/POST /abm/accounts`, `POST /abm/accounts/upload`, `GET /abm/accounts/{id}/intent`, `POST /abm/accounts/{id}/campaign`, `GET /abm/dashboard`
- **Wait Step**: Real time delays in workflows (was stub) — supports minutes, hours, and day-based delays
- **Wait-for-Event Step**: Pause workflow until email opened, link clicked, or form submitted. Used in `lead_nurture` template
- **3 new connectors**: Bombora (intent data API), G2 (buyer intent signals), TrustRadius (review + intent data)
- **4 new workflow templates**: `email_drip_sequence`, `ab_test_campaign`, `abm_campaign`, plus `lead_nurture` now has `wait_for_event` steps
- **Push notification endpoints**: `POST /push/subscribe`, `POST /push/unsubscribe`, `GET /push/vapid-key`, `POST /push/test`

### Changed
- Connector count: 51 → **54** (3 new intent data connectors)
- Tool count: 320+ → **340+** (12 new tools across Bombora, G2, TrustRadius)
- Workflow templates: 11 → **15** (4 new marketing automation templates)
- Marketing connector group: 16 → **19** (added Bombora, G2, TrustRadius)
- Backend tests: 1,196+ → **1,633**
- Frontend vitest: **93** tests
- Playwright E2E: 14 → **17** spec files
- CI E2E now runs against production on every merge to main
- Version: 3.1.0 → **3.2.0**

## [3.1.0] — 2026-04-02

### Added
- **7 new connectors**: GA4, MoEngage, NetSuite, WordPress, Twitter/X, YouTube, Mailchimp — all with real API endpoints from official documentation
- **8 new agents**: Treasury (cash management, sweep, forecast), Expense Manager (receipt OCR, policy enforcement, reimbursement), Rev Rec ASC 606 (performance obligation identification, revenue allocation, journal entries), Fixed Assets (depreciation schedules, impairment testing, disposal), Email Marketing (campaign creation, list segmentation, A/B testing), Social Media (scheduling, engagement monitoring, analytics), ABM (account targeting, intent signals, personalized outreach), Competitive Intel (competitor monitoring, pricing analysis, feature comparison)
- **CFO Dashboard** (`/dashboard/cfo`): Cash Runway, Burn Rate, DSO, DPO, AR/AP Aging (30/60/90/120+), P&L Summary, Bank Balances (via AA), Tax Calendar with filing deadlines
- **CMO Dashboard** (`/dashboard/cmo`): CAC by channel, MQLs/SQLs pipeline, Pipeline Value by stage, ROAS by Channel (Google/Meta/LinkedIn), Email Performance (open/CTR/unsub), Brand Sentiment trend, Content Performance
- **NL Query interface**: Cmd+K global search bar + slide-out chat panel with full conversational UI, agent attribution on every answer, and persistent chat history
- **Multi-company support**: company switcher in top nav for CA firms managing multiple client entities, isolated data per company, cross-company consolidated reporting, RBAC per entity
- **Scheduled Report Engine**: Celery beat scheduler with cron expressions, PDF/Excel output with branded templates, delivery to email/Slack/WhatsApp, Report Scheduler UI for create/manage/toggle/run-now
- **8 new workflow templates**: `month_end_close` (trial balance through close), `daily_treasury` (cash position, sweep, forecast, report), `tax_calendar` (deadline tracking, filing prep, DSC signing), `invoice_to_pay_v3` (OCR through payment execution), `campaign_launch` (brief through monitoring), `content_pipeline` (ideation through publish), `lead_nurture` (scoring through sales handoff), `weekly_marketing_report` (collect metrics, build report, deliver)
- **Report Scheduler UI**: create, manage, toggle on/off, and run-now scheduled reports from the dashboard
- **3 new blog posts**: month-end close optimization, honest ROI measurement framework, CFO story (200-person IT company)

### Fixed
- All 38 stub connectors rewritten with real API endpoints from official documentation — zero stubs remain
- **Tally**: fake REST replaced with proper XML/TDL protocol + bridge agent for remote on-premise instances (WebSocket tunnel, auto-reconnect, heartbeats)
- **Banking AA**: removed illegal payment tools, implemented full RBI-compliant consent flow (create consent, redirect, callback, FI session, fetch data). Connector is now read-only
- **GSTN**: fixed base URL + implemented real Adaequare 2-step authentication (POST /authenticate for session token) + DSC signing (PKCS#1 v1.5 RSA-SHA256 via cryptography library)
- **AP Processor**: wired to PineLabs Plural for actual payment execution (not simulated)
- ROI claims in marketing copy replaced with honest "measured during pilot" language throughout
- mypy errors resolved: grantex module typing, ChatAnthropic imports, LangGraph overload signatures
- bandit security scan clean: defusedxml for all XML parsing, nosec annotations for GAQL/SOQL query strings

### Changed
- Agent count: 27 → **35** (8 new specialist agents across Finance and Marketing)
- Connector count: 43 → **51** (7 new connectors, all with real endpoints)
- Tool count: 273 → **320+** (new tools across all 8 new connectors)
- Workflow templates: 3 → **11** (8 new production-ready templates)
- Landing page updated with correct agent/connector/tool counts
- Documentation: added CFO Guide, CMO Guide, updated API Reference with 12 new endpoints
- Version: 2.3.0 → **3.1.0**

## [2.3.0] - 2026-03-31

### Added — Security, Error Handling, SDKs & New Features
- **Password Reset Flow**: Full forgot-password + reset-password with JWT tokens, rate-limited, email enumeration safe
- **Connector Detail Page**: View/edit individual connector auth config, secret references, health checks
- **Connector Registry Endpoint**: `GET /connectors/registry` returns all registered connectors with tool counts
- **Connector Create Page**: `/connector-create` UI for adding new connector configurations
- **Email Workflow Triggers**: `email_received` trigger type matches on subject keywords for inbox-driven workflows
- **API Event Triggers**: `api_event` trigger type for event-driven workflow automation
- **Agent Tool Auto-Population**: 25 agent types + 5 domain fallbacks auto-assign relevant tools on creation
- **Slack Full Configuration**: Bot token auth, connector detail edit, Slack tools in support/ops agent defaults
- **API Key Management**: `ao_sk_` prefixed keys, admin-only endpoints (`POST/GET/DELETE /org/api-keys`), bcrypt-hashed at rest
- **Shadow Limit Enforcement**: Agents must pass shadow quality gates before promotion to active status
- **HITL via GraphInterrupt**: LangGraph-based HITL with `GraphInterrupt` for pause/resume at approval nodes
- **Tool Validation**: Scope enforcement ensures agents cannot call tools outside their authorized set
- **Secret Manager Integration**: GCP Secret Manager via `secret_ref` field in connector config
- **Auth Failure Clearing**: IP-based failure tracking with auto-block + success clears failure count
- **Python SDK** (`pip install agenticorg`): client.agents.run(), client.sop.parse_text(), client.a2a.agent_card()
- **TypeScript SDK** (`npm i agenticorg-sdk`): full agent/SOP/A2A/MCP client
- **MCP Server** (`npx agenticorg-mcp-server`): exposes 340+ tools to Claude Desktop, Cursor, ChatGPT
- **CLI**: `agenticorg agents list`, `agenticorg agents run`, `agenticorg sop parse`, `agenticorg mcp tools`
- **Integration Workflow Page**: `/integration-workflow` with visual protocol guide + SDK quickstart
- **Developer Section**: Landing page developer section with SDK/CLI/MCP quickstart
- **Comms Domain**: 3 comms agent types (Ops Commander, DevOps Scout, Slack Notifier) — 6 domains total
- **Negative Test Suite**: 22 unit tests + 19 E2E tests covering error paths (401, 400, 404, 409, 410, 429)
- **Regression Tests**: 55 regression tests (40 March 2026 + 15 April 2026 PR fixes)

### Fixed — QA Bug List (7 bugs)
- **AUTH-RESET-001**: Password reset email flow (was just an alert() stub)
- **ORG-INV-002**: Invite accept "Invalid issuer" — dynamic issuer matching for production
- **AGENT-CONFIG-003**: Tools auto-populated based on agent_type/domain
- **HITL-COUNT-004**: Decided tab shows decision badge instead of action buttons
- **HITL-EXP-005**: Expired items filtered from Pending queue (backend + frontend)
- **WF-CONN-006**: Email trigger + api_event added to workflow UI (was missing)
- **CONN-SLACK-007**: Slack connector end-to-end config from UI

### Security — All CodeQL + Dependabot Resolved
- Fixed 17 CodeQL alerts: stack trace exposure, clear-text logging, XSS, socket binding, workflow permissions
- Fixed 2 Dependabot alerts: picomatch 2.3.1→2.3.2, 4.0.3→4.0.4
- Auth middleware: generic error messages (no internal details leaked)
- Sales API: whitelisted response fields (no agent internals exposed)
- API key endpoints admin-only (`agenticorg:admin` scope required)
- Secret key hardening via GCP Secret Manager (`_get_secret()` in BaseConnector)
- Auth failure clearing — successful auth clears IP-based failure count

### Changed
- Workflow UI: 5 trigger types (manual, schedule, webhook, api_event, email_received)
- Approval card: readonly mode for decided items with decision + timestamp
- ConnectorCard: clickable, navigates to detail page
- All form pages: extract and display API error details instead of generic messages
- Settings/Workflows: user-facing error messages instead of console.error
- Integrations page: replaced curl examples with SDK/CLI quickstart
- Agent domains: 5 → **6** (added Comms domain)
- Agent skills: 25 pre-built + 3 comms = **28 total skills**
- Automated tests: 1,031 → **1,196+** (821 unit + 86 security + 174 connector harness + 55 regression + 62 integration + 370+ Playwright E2E + 148 production E2E)
- Version: 2.2.0 → **2.3.0**

## [2.2.0] - 2026-03-29

### Added — Agent-to-Connector Bridge (Agents That Act)
- **Tool Calling Pipeline**: Agents now parse LLM output for `tool_calls`, execute them via Tool Gateway against real external APIs, and synthesize results in a second LLM pass
- **GitHub Connector**: 9 real API v3 tools (list_repos, get_repo, issues, PRs, releases, search_code, actions)
- **Jira Connector**: 11 real Atlassian REST API tools (projects, issues, JQL search, transitions, comments, sprints, metrics)
- **HubSpot Connector**: 13 real CRM API v3 tools (contacts, deals, companies, pipelines, analytics) with OAuth auto-refresh
- **3 New Agents**: Ops Commander (Jira triage), CRM Intelligence (HubSpot analysis), DevOps Scout (GitHub + Jira health)
- **3 Pre-built Workflows**: Incident Response Pipeline, Lead-to-Revenue Pipeline, Weekly DevOps Health Report
- **Production Connector Test Suite**: 17 tests hitting real Jira/HubSpot/GitHub APIs

### Fixed — Critical Production Bugs
- **Workflow Engine**: `run_workflow` now actually executes the WorkflowEngine in background — creates StepExecution DB records, updates progress, creates HITLQueue entries for approval steps
- **Token Blacklist**: Changed Redis key from `token[:32]` (shared by ALL HS256 JWTs) to SHA-256 hash — one logout was blocking every user
- **Playground 401**: Token validation now guards empty JWKS URL; frontend handles demo login failure properly
- **Agent Promote**: `shadow_min_samples=0` now bypasses shadow validation (was blocking all promotions)
- **Base Connector Auth**: `_authenticate()` now runs before HTTP client creation so auth headers are included
- **Jira Search API**: Migrated from deprecated `/rest/api/3/search` (410 Gone) to `/rest/api/3/search/jql`
- **HubSpot OAuth**: Auto-refresh on token expiry + 401 retry with re-authentication

### Changed
- **Workflow `_execute_agent`**: Replaced hardcoded stub with real agent instantiation and LLM execution
- **ToolGateway**: Optional dependencies (works without rate limiter/audit), dynamic connector resolution from registry + DB
- **Playground UI**: Displays tool call results with connector name, status, and latency
- **WorkflowRun UI**: Auto-polls every 3s while workflow is running
- **Version**: 2.1.0 → 2.2.0

### Metrics
- Automated tests: 353 → **1,031** (pytest) + 125 production E2E
- Production E2E: **125/125 (100%)** — all 21 sections, all demo users, full lifecycle
- Connector tools: 54 connectors × **273 total tools**
- Real API verified: GitHub (9), Jira (11), HubSpot (13) — 14 Jira tickets created on production

## [2.1.0] - 2026-03-21

### Added — Full PRD v4 Compliance
- **Workflow Engine**: Dependency graph resolution via topological sort, timeout enforcement, retry integration with exponential backoff, HITL pause/resume, sub-workflow execution
- **Schedule Trigger**: Full cron expression matching (5-field) for time-based workflow triggers
- **Token Bucket Rate Limiter**: Redis Lua-based atomic token bucket replacing simple counter
- **JWT Issuer Validation**: `iss` claim validation against Grantex token server
- **Token Pool Refresh**: Background refresh via `delegate_agent_token()` at 50% TTL
- **API Endpoints**: GET `/workflows/runs/{id}`, POST `/dsar/export`, POST `/schemas`, PUT `/agents/{id}`
- **WebSocket Feed**: Registered `/ws/feed/{tenant_id}` in main router
- **Agent Prompts**: Token scope declarations and `<processing_sequence>` steps for all 24 agents
- **OpenTelemetry**: All 7 spans with full PRD attributes and proper SpanKind
- **LangSmith Integration**: Full httpx-based trace logging (log_trace, log_batch, update_run)
- **Alert Manager**: 11 PRD-defined threshold checks with Slack/email notification
- **Shadow Comparator**: 6 quality gates (accuracy, confidence calibration, HITL rate, hallucination, tool errors, latency)
- **HPA Integration**: Queue depth + CPU + schedule-based scaling signals
- **Cost Ledger**: Redis+DB dual persistence with daily/monthly budget enforcement
- **RLS on Tenants**: Row-level security now covers all 18 tables
- **CI/CD Approval Gate**: Manual approval stage before production deployment (9/9 stages)
- **Test Suite**: 161 test functions across 13 files (Finance 15, HR 12, Ops/Mkt 13, Performance 9, Reliability 7, Security Auth+LLM 22, Security Data+Infra 25, Agent Scaling 31)
- **UI Pages**: All 10 pages fully implemented (Agents, Workflows, Approvals, Connectors, Schemas, Audit, Settings, AgentDetail, WorkflowRun, Dashboard)
- **BaseConnector._get_secret()**: Proper credential retrieval via config/env/secret_ref

## [2.0.0] - 2026-03-21

### Added — Initial Platform
- 24 specialist agents + NEXUS orchestrator
- 43 typed connectors (PineLabs Plural for payments, Gmail)
- Workflow engine with 9 step types
- Full PostgreSQL DDL with pgvector, RLS, and time-range partitioning (6 migrations)
- 18 JSON Schema data templates
- OAuth2/Grantex auth with JWT, token pool, scope enforcement
- Tool Gateway with rate limiting, idempotency, PII masking, audit logging
- React 18 + TypeScript + Shadcn/ui frontend (10 pages, 8 components)
- OpenTelemetry tracing + Prometheus metrics
- Agent Factory with shadow mode, lifecycle FSM, cost ledger
- 9-stage CI/CD pipeline with Docker + Helm charts
- SOC2/GDPR/DPDP compliance tools built-in
- Apache 2.0 license
