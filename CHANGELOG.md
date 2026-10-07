# Changelog

All notable changes to AgenticOrg are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - 2026-08-29

### Added - Agent runtime: execution limits and loop detection
- With `AGENTICORG_RUNTIME_LIMITS_ENABLED` on (off by default), an agent's
  own limits (`PUT /agents/{id}/limits`: model steps, duration, tool calls,
  and the loop rule) are enforced in the graph and the runner, bounded by
  the platform's maxima (`core/langgraph/limits.py`): a run over a limit, or
  repeating a tool call pattern, is stopped with the reason in its error and
  a `limit` block that the run's audit entry and a Prometheus counter carry.
- The step limit is checked before every model call, chat runs carry the
  agent's limits too, `POST /agents/{id}/run` returns the `limit` block, a
  run may make exactly `max_tool_calls` tool calls, and with the switch off
  a run that reaches the platform ceiling fails as it did before.

### Added - FinOps: cost comparison and forecasting
- With `AGENTICORG_FINOPS_FORECAST_ENABLED` on (off by default),
  `GET /finops/forecast` projects tokens and cost per use case for the next
  quarter from the attributed ledger's history and a growth assumption, and
  `GET /finops/comparison` folds the model calls per use case with the
  cheapest catalogue alternatives and a before-and-after around a change
  date (`core/finops/forecast.py`).

### Added - FinOps: thresholds and actions
- With `AGENTICORG_FINOPS_THRESHOLDS_ENABLED` on (off by default), a
  tenant administrator sets organisation, application, use-case or
  business-unit thresholds per day or month with an action
  (`core/finops/thresholds.py`, table `finops_thresholds`, migration
  `v6z56_finops_thresholds`, `/finops/thresholds`): a breached threshold
  alerts the owner once per period, throttles the run with a short delay,
  or suspends runs until the period resets or an administrator lifts it.

### Added - FinOps: use-case attribution
- With `AGENTICORG_FINOPS_ATTRIBUTION_ENABLED` on (off by default), a run
  binds its use case, application, business unit, department and cost
  centre (`core/finops/attribution.py`); its cost write adds a row per day,
  agent and attribution to `finops_cost_ledger` (migration
  `v6z55_finops_attribution`), each model call record carries the business
  unit and application, and `GET /finops/attribution` folds the ledger by
  any dimension with the unattributed share.

### Added - AI governance: policy console
- With `AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED` on (off by default),
  a tenant administrator sees every policy (model routing, access and
  limits, guardrail rules, approval policies, the action taxonomy) in one
  shape, writes and removes one through its own store's writer, and dry-runs
  a described call, text, tool or workflow across the enforcement points
  (`core/governance/policy_console.py`, `/governance/policies`).

### Added - AI governance: regulatory risk tiers
- With `AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED` on (off by default), an
  agent's risk tier forces controls whatever the separate switches say
  (`core/governance/risk_tiers.py`): medium needs registry approval; high
  adds a passed evaluation gate, a human oversight condition and 50 scored
  shadow samples; critical adds 200 samples and maker-checker. Promotion and
  resume refuse an unmet requirement; a tier is changed by an administrator
  and lowered by a second person; a regulated agent keeps its oversight and
  its gate. `GET /governance/risk-tiers` shows the policy and compliance.

### Added - AI governance: model cards
- With `AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED` on (off by default),
  every model a tenant uses has one standard card
  (`core/governance/model_cards.py`): catalogue facts, use and risk tier
  from the inventory, the policies and limits that name it, the residency
  decision, price, health, the newest evaluation run, and the part an
  administrator writes (table `model_cards`, migration `v6z54_model_cards`)
  with approval by a second person; `GET /governance/model-cards` lists the
  cards with what each still lacks.

### Added - AI governance: asset inventory and bill-of-materials export
- With `AGENTICORG_GOVERNANCE_INVENTORY_ENABLED` on (off by default), a
  tenant administrator reads a live inventory of the tenant's agents,
  models, prompts (as hashes), knowledge bases, tools and connectors with
  owner, version, risk tier, status and dependencies
  (`core/governance/inventory.py`, `GET /governance/inventory`), and
  exports it as a bill-of-materials document
  (`GET /governance/inventory/export`); the summary counts unowned assets
  and untiered agents.

### Added - Knowledge retrieval: quality metrics, grounding indicator and re-indexing
- With `AGENTICORG_KNOWLEDGE_METRICS_ENABLED` on (off by default), every
  knowledge search leaves a figures-only sample (`core/rag/metrics.py`,
  table `knowledge_retrieval_metrics`, migration
  `v6z53_knowledge_retrieval_metrics`) and feeds Prometheus series by
  retrieval path; `GET /knowledge/metrics` folds a tenant's window and
  `POST /knowledge/metrics/grounding` returns the share of an answer's
  sentences the given chunks support. With
  `AGENTICORG_KNOWLEDGE_REINDEX_ENABLED` on, `POST /knowledge/reindex`
  re-embeds chunks made by a stale model and records missing entities,
  bounded per call (`core/rag/reindex.py`).

### Added - Knowledge retrieval: graph retrieval over extracted entities
- With `AGENTICORG_KNOWLEDGE_GRAPH_RETRIEVAL_ENABLED` on (off by default),
  ingestion records the names, codes, amounts and dates each chunk mentions
  (`core/rag/entities.py`, table `knowledge_entities`, migration
  `v6z52_knowledge_entities`; identity numbers are never recorded), a search
  fuses in the chunks the graph reaches from the query (matched entities,
  their neighbours, the chunks that mention them) with a `graph` step in the
  trace, and `GET /knowledge/graph?q=` shows the matched entities, their
  neighbours and the links between them.

### Added - Knowledge retrieval: query transformation and retrieval traces
- With `AGENTICORG_KNOWLEDGE_QUERY_TRANSFORM_ENABLED` on (off by default),
  a knowledge search is planned before it runs (`core/rag/query.py`): the
  query is normalised, a compound question decomposed by named rules and a
  keyword form added, with a model proposing further queries when
  `AGENTICORG_KNOWLEDGE_QUERY_REWRITE_MODEL` names one. A weak first pass is
  expanded to the variants and fused by reciprocal rank; `"trace": true` on
  `POST /knowledge/search` returns every step with its counts, and the
  console shows them under the results.

### Added - Knowledge retrieval: document access control
- A knowledge document belongs to a domain or to the tenant as a whole
  (`knowledge_documents.domain`, migration
  `v6z51_knowledge_document_domain`; the upload names it with `?domain=`).
  A caller limited to some domains is shown chunks of documents in those
  domains and of shared documents only, in search results, citations,
  excerpts and the document list (`core/rag/access.py`); an unrestricted
  caller sees the tenant's documents as before, and existing documents stay
  shared.

### Added - Knowledge retrieval: citations and excerpt navigation
- Every knowledge search hit carries a `citation` (chunk id, source,
  chunk number, page, paragraph, heading, sheet, cell range;
  `core/rag/citations.py`) read through a join on the chunk provenance;
  older chunks carry `null` and the original fields are unchanged.
  `GET /knowledge/documents/{id}/excerpt?q=` returns the cited chunk
  whole with the query terms located and the neighbouring chunks, under
  the retrieval guardrails. The Knowledge Base page shows the citation
  beside each hit and opens the excerpt with the terms marked and
  previous/next links.

### Added - Knowledge retrieval: search filters and a re-ranking stage
- `POST /knowledge/search` takes `filters` (`category`, `source`,
  `file_type`, a `created_from`/`created_to` window; `core/rag/filters.py`)
  applied inside the dense and sparse rankings alike, so a narrowed
  search never widens past what was asked. Behind
  `AGENTICORG_KNOWLEDGE_RERANK_ENABLED` (off by default), the fused
  candidates of a hybrid search are re-scored on the query's own terms
  (coverage, phrase, proximity, title, fused score; `core/rag/rerank.py`)
  with no model call; off, the fused order is returned as it was.

### Added - Knowledge retrieval: layout-preserving extraction and chunking strategies
- PDF pages are split into numbered paragraphs with headings recognised
  from line shape, Word documents keep their heading styles and attach
  table rows to their section, and every chunk records its paragraph and
  nearest heading beside its page (`knowledge_chunk_sources.paragraph`,
  `.heading`; migration `v6z50_chunk_layout`). A tenant chooses how spans
  become chunks with `chunk_strategy` in the tenant AI settings
  (`core/rag/chunking.py`): `sentence` (the default, unchanged), `paragraph`
  or `heading`, sized by `chunk_size`. Existing chunks are not re-chunked.

### Added - Agent registry: ratings, reliability and certification
- The card carries reliability metrics over a window (runs by status,
  completion, failure and human-review rates, average and 95th-percentile
  duration, tokens and cost per run, feedback by type, shadow accuracy;
  `GET /agents/{id}/reliability?days=`), the rating summary
  (`POST /agents/{id}/rating`, one score per user and agent, table
  `agent_ratings`, migration `v6z49_agent_ratings`) and a certification
  section: registry approval, the evaluation gate verdict and the model
  provider attestation, with a plain statement that trust-registry
  attestations and passports are not attached.

### Added - Agent registry: dependency graph
- `GET /agents/{id}/dependencies` (`core/agent_registry/dependencies.py`)
  returns an agent's models, prompt, tools and connectors, knowledge base,
  governing policies (guardrail rules, review condition, output schema,
  evaluation gate dataset), related agents and teams as nodes and edges,
  with names and references only.

### Added - Agent registry: catalogue, templates and banking pack
- `GET /agent-registry` filters by state, risk tier, use case, channel,
  domain and a search term over the card text; the console page Agent
  catalogue shows it. `GET /agent-registry/templates` lists the agent
  templates the industry packs offer in the card's terms. A banking pack
  (`core/agents/packs/banking`) adds five installable templates for retail
  and SME banking operations (loan underwriting analyst, KYC reviewer,
  collections agent, complaint handler, bank reconciliation analyst), each
  with a review condition, a confidence floor of at least 85% and tools the
  platform has.

### Added - Agent registry: approval workflow, environments and traffic split
- With `AGENTICORG_AGENT_REGISTRY_GATES_PROMOTION` on (off by default),
  promotion and resume to active need an approved or published registry
  entry (`core/agent_registry/approval.py`), checked after the shadow
  evidence, the maker-checker check and the evaluation gate; promotion
  publishes an approved entry and retirement retires a published one, each
  a recorded transition. Environments (development, staging, production)
  are read from the state. `PUT /agents/{id}/traffic-split` sends a share
  of an agent's runs through the agents API to another active agent while
  `AGENTICORG_AGENT_TRAFFIC_SPLIT_ENABLED` is on (`core/agent_registry/
  traffic.py`), chosen from the run's thread or correlation id so a retry
  lands on the same agent; the response names the agent that served the
  run, and removing the split is the one-action rollback.

### Added - Agent registry: cards and lifecycle states
- Behind `AGENTICORG_AGENT_REGISTRY_ENABLED` (off by default), each agent
  has a card (`GET /agents/{id}/card`: identity, purpose, risk tier, use
  case and channels, models, tools, permissions, schemas, a prompt summary
  without the text, controls, the evaluation gate verdict and the
  lifecycle state) with the written fields set by `PUT /agents/{id}/card`,
  and a governance lifecycle (draft, review, approved, published,
  deprecated, retired) moved by `POST /agents/{id}/lifecycle` under a
  transition table: the submitter cannot approve and only an active agent
  is published. Every transition is recorded (`agent_registry`,
  `agent_registry_events`, migration `v6z48_agent_registry`);
  `GET /agent-registry` lists entries by state and risk tier. The
  lifecycle does not yet gate promotion. See
  `docs/governance/agent-registry.md`.

### Added - Evaluation framework: promotion gate and model comparison
- An agent may declare an evaluation gate (`PUT /agents/{id}/eval-gate`;
  `core/evals/gates.py`): a dataset, optionally a version, a minimum pass
  rate and a regression allowance. Behind
  `AGENTICORG_EVAL_PROMOTION_GATE_ENABLED` (off by default), promotion and
  resume to active refuse, after the maker-checker check, a prompt whose
  newest stored run of that version is missing, below the minimum, or
  regressed against the prompt it replaces; `GET /agents/{id}/eval-gate`
  reports the verdict either way. `POST /eval-datasets/{id}/run` takes
  `agent_id` to run an agent's prompt text so the gate can match it.
  `GET /eval-datasets/{id}/compare` ranks the models that ran a version from
  their newest stored runs by pass rate, latency and cost per case, with
  accuracy, answers a minute, tokens per case (migration
  `v6z47_eval_run_tokens`) and the judges' means; the console shows the
  table.

### Added - Evaluation framework: adversarial and scheduled runs
- Two synthetic check kinds (`observability/synthetic.py`, migration
  `v6z46_synthetic_check_kinds`): `adversarial` dry-runs the tenant's
  guardrail rules over the adversarial evaluation set on an interval and
  fails below a minimum recall or above a number of benign controls
  wrongly caught; `eval_dataset` scores a dataset version with a fixed
  prompt and model, keeps the run in the evaluation history and fails
  below a minimum pass rate. Results keep counts and case ids, never a
  text. The Guardrails page lists the adversarial checks with their latest
  result, and the checks panel offers both kinds.

### Added - Evaluation framework: scorers, metrics and stored runs
- A case may carry a `label` (the class the answer should name), a
  `reference` answer and a `context`. A run of a dataset version
  (`core/evals/runs.py`) reports exact-match and classification metrics
  (accuracy, macro precision, recall and F1 over the dataset's labels;
  `core/evals/metrics.py`) beside the pass rate, and any of four
  model-graded judges with a judge model (faithfulness, relevance,
  instruction adherence, context recall; `core/evals/scoring.py`), each one
  more billed call per case and judge, with failures kept apart from scores.
  Runs are stored (`eval_runs`, migration `v6z45_eval_runs`) as what was
  measured and what came out, with the prompt as a hash and an optional
  label; never an answer, an input or a judge's reason. `GET
  /eval-datasets/{id}/runs` and `GET /eval-runs/{run_id}` read them back,
  and the console panel chooses judges and lists earlier runs.

### Added - Evaluation framework: datasets
- Behind `AGENTICORG_EVALS_V2_ENABLED` (off by default), a tenant's
  administrators keep named evaluation datasets as immutable versions
  (`core/evals/datasets.py`, tables `eval_datasets` and
  `eval_dataset_versions` under row-level security, migration
  `v6z44_eval_datasets`). Each version carries the SHA-256 of its cases;
  identical cases are not stored twice and a dataset is archived, not
  deleted. `/eval-datasets` lists, creates, reads, versions and archives;
  `POST /eval-datasets/{id}/run` scores a prompt with one model against a
  version, 25 cases a request, and names the version and hash it measured.
  The Prompt Templates page has a panel for all of it. Runs are not stored.
  See `docs/governance/evaluation-datasets.md`.

### Added - Tenant-owned remote MCP tools
- Dedicated Streamable HTTP bearer connection, protocol discovery, encrypted
  token storage, schema-bound read review, read-only probe and credential rotation.
- Agent create/edit, save validation and runtime share tenant-scoped discovered
  tool identities; remote tool grants use isolated manifests and preserve signature,
  scope and revocation checks. Unlinking, archiving and schema drift refuse calls.
- Remote write tools remain contained. OAuth/stdio/legacy SSE connections and
  live provider speech, messaging or payment actions are not verified by this release.
- Added SDK protocol, Docker API/database and browser regressions, including the
  reported speech-tool names, tenant isolation and refusal paths.

### Added - Prompt governance: structured-output enforcement
- Behind `AGENTICORG_OUTPUT_SCHEMA_ENFORCED` (off by default), an agent's
  final answer is validated against the output schema it declares
  (`core/prompts/output_schema.py`): its own JSON Schema, set with
  `PUT /agents/{id}/output-schema` (locked on active agents), or a
  registered schema name. An invalid answer goes back to the model with
  what is wrong, up to two times; one that is still invalid, or a declared
  schema that cannot be used, is escalated to a human reviewer
  (`output_schema_invalid`, `output_schema_unusable`) instead of being
  returned as completed. Agents with no declared schema are not affected.
  Enforced on runs started through the agents API.
  `agenticorg_output_schema_checks_total{result}`.

### Added - Prompt governance: context-window management
- Behind `AGENTICORG_CONTEXT_WINDOW_MANAGED` (off by default), before each
  model call of an agent run the conversation is measured against the
  model's context window, less the room for its answer and a margin
  (`core/prompts/context_window.py`). When it does not fit, older tool
  results are omitted from the copy that is sent, least relevant to the
  latest user message first, each replaced by a marker so every tool call
  stays answered; the largest remaining results are cut if needed. System
  messages, the user's messages, the model's turns and the newest tool
  results are never dropped, the run's history is unchanged, and the
  grounding check still reads everything retrieved. Token counts are
  estimates. `agenticorg_context_window_trims_total{result}`.

### Added - Prompt governance: model comparison and dataset evaluation
- Behind `AGENTICORG_PROMPT_COMPARE_ENABLED` (off by default; tenant
  administrators; six requests a minute per tenant): `POST
  /api/v1/prompt-templates/compare` runs one prompt and one input against up
  to four models and returns each answer with its latency, tokens and cost,
  and `POST /api/v1/prompt-templates/evaluate` scores up to three prompt
  variants against a reference dataset of up to 25 cases with deterministic
  expectations, reporting pass rates and failed checks
  (`core/prompts/compare.py`). Calls go through the model gateway as the
  tenant; one model's failure is its own result; nothing is stored.
- The prompt templates page has a Compare models panel on a selected
  template.

### Added - Prompt governance: maker-checker for agent prompts
- With maker-checker on (`AGENTICORG_PROMPTS_MAKER_CHECKER` or the
  authority flag `prompts.maker_checker`), an agent whose prompt has changed
  since it was last active is promoted, or resumed to active, only by a
  signed-in user other than the one who last changed the prompt
  (`core/prompts/activation.py`); an agent is not created or cloned straight
  into `active`; a change with no recorded author is not activated; an
  unreadable flag refuses the activation. A pause and resume with no prompt
  change is unaffected. Off, activation is as it was.
- Creating or cloning an agent writes its first prompt into the agent's
  prompt history with who set it, so a new agent's prompt has an author.

### Fixed - Public search pages
- Pre-render substantive public pages at build time, preserve private-page
  noindex behavior, and return a real 404 for unknown public paths.
- Repair two public links, connect public pages through crawlable navigation,
  and show the approved founder bio with consistent article author metadata.

### Added - Prompt governance: maker-checker for prompt templates
- With `AGENTICORG_PROMPTS_MAKER_CHECKER` or the authority flag
  `prompts.maker_checker` on (both off by default), creating, changing,
  rolling back or deleting a prompt template is stored as a change request
  and answers 202; a different person approves or rejects it and only an
  approval applies it (`core/prompts/change_requests.py`). The proposer
  cannot decide their own change, a template has one pending change at a
  time, a change proposed against a template that has since changed becomes
  stale and is not applied, and an unreadable flag refuses the change.
- The template history records who proposed a change, who approved it and
  the request it came from. `GET /api/v1/prompt-templates/changes`, `GET
  .../changes/{id}` and `POST .../changes/{id}/approve|reject|withdraw`; the
  prompt templates page shows what is waiting. Migration
  `v6z43_prompt_change_requests`. An agent's own prompt is not covered yet.

### Added - Prompt governance: typed parameters
- A prompt template's variables can be declared as typed parameters
  (`core/prompts/parameters.py`): `string`, `integer`, `number`, `boolean`
  or `enum`, with required, default, range, length, pattern and choices. A
  variable declared by name only is a required string, as before.
- `POST /api/v1/prompt-templates/check` checks a template's text against
  its declarations without storing it, and `POST
  /api/v1/prompt-templates/{id}/render` fills a stored template after
  checking the values; a missing, unknown or mistyped value is refused with
  every problem listed, and no placeholder is left in rendered text.
- Behind `AGENTICORG_PROMPT_TYPED_PARAMETERS_ENABLED` (off by default),
  creating or changing a template checks its parameters and refuses a
  placeholder it does not declare. See `docs/governance/prompt-governance.md`.

### Added - Streaming latency: time to first token and task queue wait
- Behind `AGENTICORG_MODEL_STREAM_TIMING_ENABLED` (off by default) the
  reasoning node reads each model answer as a stream, times its first token
  and reassembles the same message (`observability/streaming.py`); the time
  is observed in `agenticorg_model_first_token_seconds{provider,model}` and
  set on the model call's span as `llm.first_token_ms`. Off, the call is
  made exactly as before.
- Behind `AGENTICORG_TASK_QUEUE_TIMING_ENABLED` (off by default) a
  published background task carries its publish time, and the worker
  observes how long it waited in
  `agenticorg_task_queue_wait_seconds{queue}` and on the task's span
  (`task.queue_wait_ms`); tasks scheduled for later, redeliveries and
  retries are not counted.

### Added - Guardrails: the adversarial evaluation set
- A fixed corpus of 46 synthetic cases
  (`core/governance/guardrails/adversarial.py`): direct and indirect
  injection, sensitive data in answers and tool calls, ungrounded answers
  and output-policy breaks, each with benign controls, and with attacks the
  pattern-based detectors are known to miss. `POST
  /api/v1/guardrails/adversarial/run` dry-runs the tenant's rules or the
  recommended baseline over it and reports per category what was detected,
  missed and wrongly caught, without any case text; the Guardrails console
  has a card for it. The baseline's result (25 of 31 attacks, 1 of 15
  controls wrongly caught) is pinned case by case in a test.
- `evaluate` takes a rule set for a dry run, so a set can be measured
  without storing it.

### Added - Guardrails console
- `/dashboard/settings/guardrails` (administrators): the mode in effect
  (hooks off, flag-only, enforcing), the rules with what each applies to,
  add, change, enable, disable and delete, and a dry run of a stage over a
  text with a retrieved context for a grounding rule. `GET
  /api/v1/guardrails/status` also reports whether the hooks are on and the
  stages, detectors, actions and risk tiers a rule may name.

### Added - Guardrails: the grounding checker
- A `grounding` detector for output-stage rules
  (`core/governance/guardrails/grounding.py`): each claim of an answer is
  held against the context the run retrieved (the conversation's tool
  results and, unless `include_user_input` is false, the user's words). A
  claim whose content words are supported below `min_support` is an
  `unsupported_claim`; a figure that occurs nowhere in the context is an
  `unsupported_number` whatever the claim's support; `require_context`
  reports an answer given with no retrieved context. The rule flags, or
  blocks the answer when `guardrails.enforce` is on; it never rewrites.
  Deterministic and lexical: no model call.
- The reasoning node passes the conversation to the output stage, and
  `POST /api/v1/guardrails/evaluate` takes `context` and `user_input` for a
  dry run. Only a detector that uses the context receives it. See
  `docs/governance/guardrails.md`.

### Added - Synthetic checks
- A tenant administrator defines scheduled probes of the tenant's own paths
  (`observability/synthetic.py`): `model` (a fixed prompt through the direct
  router, optionally expecting a text in the answer), `knowledge` (a fixed
  query expecting a minimum number of results), `guardrail` (a dry run of
  the rules for a stage expecting the text blocked, detected or clean) and
  `audit_chain` (a verification of the newest links). Each takes a latency
  limit. A run ends `ok`, `failed` (with reasons) or `error` (with the
  exception type) and is stored in `synthetic_check_results`; a result keeps
  counts and reasons, never an answer, retrieved text or the input.
- A run claims its check with one conditional update before it probes, so
  overlapping sweeps or a run by hand never probe a check twice; creations
  are serialised per tenant so the limit of 20 holds. Off, adding a check
  and running one are refused and the console shows no Checks tab.
- Behind `AGENTICORG_SYNTHETIC_CHECKS_ENABLED` (off by default) the sweep
  (`core.tasks.synthetic_tasks.run_synthetic_checks`, every five minutes)
  runs each tenant's due checks under the tenant's own row-level security
  context; runs count in `agenticorg_synthetic_checks_total{kind,result}`.
  Results are pruned after `AGENTICORG_SYNTHETIC_CHECKS_RETENTION_DAYS` (30).
- `GET/POST /api/v1/observability/checks`, `PATCH/DELETE /checks/{id}`,
  `POST /checks/{id}/run` and `GET /checks/{id}/results` (administrators
  only), and a Checks tab on the observability console page. Migration
  `v6z42_synthetic_checks`. See `docs/operations/synthetic-checks.md`.

### Added - Tamper-evident audit: the hash chain and the model call digests
- Behind `AGENTICORG_AUDIT_CHAIN_ENABLED` (off by default): the sealing task
  (`core.tasks.audit_chain_tasks.seal_audit_chains`, every five minutes)
  links each tenant's unsealed audit rows in write order with a sequence
  number, the previous link and a SHA-256 link hash over the previous link,
  the row's signed payload and its signature (migration
  `v6z41_tamper_evident_audit`). The chain head is logged after every sealing
  as the anchor. Verification recomputes every link and reports the first
  break with its sequence number and reason (`sequence_gap` for a removed
  row, `previous_link` for an inserted or reordered one, `link_hash` for an
  edited one, `signature` for a forged one). Every sealing also stores the
  head in `audit_chain_anchors` (tenant-scoped under row-level security), and
  verification holds the chain against it and against a head the caller
  supplies from the sealing log (`expected_seq`, `expected_hash`), so a chain
  cut at its end is reported as `truncated` or `anchor_mismatch`. The
  append-only trigger of `audit_log` admits exactly the sealing transition
  (the chain columns of an unsealed row filled, nothing else changed), and a
  tenant's sealers are serialised by an advisory lock. The daily task verifies every
  tenant and counts results in
  `agenticorg_audit_chain_verifications_total{result}`.
- `GET /api/v1/audit/chain` reports the head and the sealing backlog and
  `GET /api/v1/audit/chain/verify?from_seq=&limit=` runs a verification; the
  compliance evidence package's `audit_logs` section carries the head, the
  backlog and a verification of the newest thousand links.
- Routing records carry `prompt_digest`, `request_digest` and
  `response_digest` (SHA-256 over the system prompt, every message the model
  saw and its answer), signed with the record and never the content; records
  written before verify unchanged. See `docs/operations/audit-chain.md`.

### Added - Observability: run timelines, the waterfall and the live workload console
- Behind `AGENTICORG_TRACING_TIMELINE_ENABLED` (off by default; nothing
  while tracing is off): a span processor keeps the finished spans of each
  agent run (the run, its model calls, tool calls and knowledge searches) in
  memory per run, keyed by the run's root span so two runs sharing a trace
  never mix, and the runner stores them in the tenant-scoped `run_spans`
  table when the run ends, on the run's own event loop. Rows hold
  identifiers, timings, outcomes and the governance events, never content;
  a daily task prunes them after `AGENTICORG_TRACING_TIMELINE_RETENTION_DAYS`
  (migration `v6z39_run_spans`).
- `GET /api/v1/observability/runs` lists the newest stored runs (and says
  whether recording is on), `GET /api/v1/observability/runs/{run_id}`
  returns one run's spans with their offsets and durations, and
  `GET /api/v1/observability/workload` reports the tenant's pending reviews
  with the soonest deadline and the overdue count, and the last hour's run,
  model-call and guardrail outcomes, each part reported on its own. Admin-only
  reads of the tenant's own data (the shared task queues are not answered).
  The run response carries `trace_id` when tracing is on.
- Console page `/dashboard/observability` (administrators): the waterfall of
  one run (model, tool and retrieval spans with durations and the gateway and
  guardrail events) and the live workload with a countdown to the soonest
  review deadline.

### Added - Observability: tracing wiring and correlation ids
- Behind `AGENTICORG_TRACING_ENABLED` (off by default): the API's lifespan and
  every worker process install an OpenTelemetry tracer that exports to
  `OTEL_EXPORTER_OTLP_ENDPOINT` over `http/protobuf` or `grpc`
  (`AGENTICORG_TRACING_PROTOCOL`), sampled at `AGENTICORG_TRACING_SAMPLE_RATIO`.
  A strict runtime with tracing on and no endpoint refuses to start; off,
  every helper is a no-op that touches no tracer.
- Spans: `agenticorg.http.request` around every API request (continuing an
  incoming `traceparent`), `agenticorg.task.run` around every Celery task
  (the publisher's trace context travels in the task headers),
  `agenticorg.agent.run` and `agenticorg.agent.resume` around an agent
  graph's execution, `agenticorg.agent.reason` around every model call with
  the provider, model and token counts, `agenticorg.tool.call` around every
  connector dispatch with its outcome, `agenticorg.knowledge.search` around a
  knowledge search. The model gateway's decision and every guardrail outcome
  are events on the span in progress.
- Correlation: while a span records, the log context carries `trace_id`, and
  the signed audit rows the model gateway, operator overrides, residency
  attestations and guardrails write record the trace id (the request id when
  no trace is in progress). See `docs/operations/tracing.md`.
- A span names its tenant by `tenant.ref`, a keyed reference, never by the
  identifier. Residency: with deployment-wide enforcement no exporter is
  installed; with tenant-scoped enforcement a span of a tenant that enforces
  (or was never read) is withheld from export, as is a span naming no tenant
  while some tenant enforces. A worker refuses to start with tracing on and
  misconfigured, as the API does.

### Added - Runtime guardrails: call-site hooks, injection and output-policy detectors
- Behind `AGENTICORG_GUARDRAILS_HOOKS_ENABLED` (off by default; on, every
  stage is evaluated in flag-only mode until `guardrails.enforce` is on for
  the tenant): the reasoning node passes the newest message of each turn
  through the `input` stage and the model's answer through the `output`
  stage; knowledge search results and a governed case's rendered evidence
  pass the `retrieval` stage (a blocked chunk is withheld, a blocked case
  context skips the model call); a tool call's connector, tool and arguments
  pass the `action` stage at the dispatch boundary (flag or block only). A
  block ends an agent run with status `guardrail_blocked` and `E1016`, or
  returns `{"error": "guardrail_blocked"}` from the tool.
- Detectors `injection` (instruction overrides, system-prompt disclosure,
  persona switches, jailbreak markers, fake system blocks, standing orders,
  false authority and invisible characters, each with its own confidence,
  plus an administrator's own patterns) and `output_policy` (`max_length`,
  `require_json`, `required_keys`, `forbidden_phrases`, `no_urls`; output
  stage, flag or block). Transform actions are refused for structural
  detectors and for action-stage rules.
- The compliance evidence package gains a `guardrails` section: hooks and
  enforcement state, rules by stage, blocked and transformed outcomes over
  thirty days.

### Added - Runtime guardrails: engine, rules and the first detectors
- `core/governance/guardrails`: a tenant's rules say which detector runs at
  which stage of a call (`input`, `retrieval`, `output`, `action`) and what
  happens at or above the rule's threshold: `flag`, `mask`, `redact`,
  `tokenise` or `block`, narrowed to an agent, use case or risk tier. Every
  matching rule applies in priority order; a block wins over a transform.
  Detectors: `sensitive_data` (the PII analyser where installed, the regex
  recognisers otherwise, plus a Luhn-checked card-number check), `toxicity`
  (the content-safety classifier with its keyword fallback) and `pattern`
  (an administrator's regular expressions).
- Behind the authority flag `guardrails.enforce` (off by default;
  `AGENTICORG_GUARDRAILS_ENFORCE` for the deployment): off, every rule runs in
  flag-only mode, metered and logged with the text unchanged; on, transforms
  apply, a block refuses the stage with the new `E1016`, and each applied
  action writes a signed audit row. Outcomes are metered in
  `agenticorg_guardrail_outcomes_total{stage,detector,action,mode}` and logged
  with the request's correlation id.
- `GET /api/v1/guardrails/status`, `GET/POST /api/v1/guardrails/rules`,
  `PATCH/DELETE .../rules/{id}`, `POST .../evaluate` (dry run); tenant admin;
  each change writes a signed audit row. Table `guardrail_rules`
  (`v6z38_guardrail_rules`, tenant RLS). Docs: `docs/governance/guardrails.md`.
  The call-site hooks, the prompt-injection and output-policy detectors and the
  grounding checker follow in the package's next parts.
- Each detector runs off the event loop under
  `AGENTICORG_GUARDRAILS_DETECTOR_TIMEOUT_SECONDS` (2); a detector that fails or
  runs out of time fails closed for an enforced transform or block in a strict
  runtime. Pattern rules refuse expressions that nest or repeat unbounded
  quantifiers or use backreferences, take at most 32 patterns of 512
  characters, and scan at most `AGENTICORG_GUARDRAILS_PATTERN_MAX_CHARS`
  (50,000) characters. A rule's options must belong to its detector and
  `entities` must list supported kinds.

### Added - Model gateway: console page
- `/dashboard/settings/model-gateway` (tenant administrators): routing
  policies, access policies and per-model limits with create, enable, disable
  and delete; a dry run of a described request; the routing records with a
  correlation-id filter and the signature check; and the cost comparison.
  Everything reads and writes the `/api/v1/model-gateway` endpoints.

### Added - Model gateway: cost comparison and cost-aware routing
- `core/governance/model_pricing.py`: list prices per million tokens for the
  catalogue models (Gemini from the router's table), nothing per token for
  models inside the deployment, Azure deployments priced as their base model;
  `AGENTICORG_MODEL_PRICE_OVERRIDES_JSON` replaces list prices with negotiated
  ones (a negative or non-finite override is rejected). The routing records
  cost each call at its model's price when one is known; the direct router
  does so behind `AGENTICORG_MODEL_PRICING_FOR_ROUTER_COSTS` (off by default)
  and otherwise keeps its historical flat rates (FINDINGS A-116).
- `GET /api/v1/model-gateway/costs`: every catalogue model and every model seen
  in the records with its list price, blended rate and the observations over
  the window (calls, failures, failure rate, latency, cost), cheapest first.
- Routing policies gain `cost_aware` and `max_failure_rate`
  (migration `v6z37_cost_aware_routing`): with `cost_aware`, each call gets the
  cheapest of the policy's `targets` whose observed failure rate over
  `AGENTICORG_MODEL_GATEWAY_QUALITY_WINDOW_HOURS` (24) stays under the policy's
  threshold or `AGENTICORG_MODEL_GATEWAY_MAX_FAILURE_RATE` (0.05); no
  observations count as healthy, an unpriced target ranks last, unreadable
  records degrade to price alone, and the choice is named in the decision's
  reason. Observations are cached for
  `AGENTICORG_MODEL_GATEWAY_HEALTH_CACHE_SECONDS` (60).

### Added - Model gateway: model-level metrics, correlation ids and routing records
- Every model call on the agent path (the reasoning node) and the direct
  router is metered once it ends: `agenticorg_model_calls_total`,
  `model_call_latency_seconds`, `model_call_tokens_total`,
  `model_call_cost_usd_total`, `model_call_output_tokens_per_second`,
  `model_call_errors_total`, `model_fallbacks_total` and
  `model_admission_wait_seconds`, all by provider and model. The existing
  `agenticorg_llm_tokens_total` and `agenticorg_llm_cost_usd_total` are now fed.
- With `AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED=true` (off by default), each
  call made while the gateway is on for the tenant writes a signed routing
  record (`model_gateway_records`, migration `v6z36_model_gateway_records`):
  correlation id, use case, agent, routing and access policies evaluated,
  requested and chosen provider and model, fallback, outcome, error type,
  latency, admission wait, tokens and cost. `GET /api/v1/model-gateway/records`
  lists them (filters: correlation id, agent, outcome, before) and reports
  whether each signature still matches. Pruned daily after
  `AGENTICORG_MODEL_GATEWAY_RECORDS_RETENTION_DAYS` (90), deleted tenants
  included. Off, the metrics are still fed.
- The gateway's correlation id is the request id bound for the request (and
  propagated into worker tasks), so one id links the request, its routing
  decisions, its model calls and its audit rows. A per-model concurrency lease
  is now one per admission, not one per correlation id.
- Cost on the agent path uses the provider's list price where it is known
  (Gemini) and the platform's blended estimate otherwise; time to first token
  needs streaming, which the model calls do not use yet. `LLMResponse` now
  carries the provider's input and output token counts, so direct-router
  calls fill the directional token counters and the output-throughput
  histogram is observed only when the output count is known.

### Added - Model gateway: access policies, per-model limits and weighted targets
- Access policies (`model_access_policies`, `GET/POST /api/v1/model-gateway/access-policies`,
  `PATCH/DELETE .../access-policies/{id}`): who may use which provider or model.
  Evaluated after routing, first match in priority order; a policy matches on
  use case, sensitivity, agent, business unit, language, the calling
  `application` and `principal`, and the provider and model chosen; `deny`
  refuses, `allow` lets through fenced to `allowed_providers` and
  `allowed_models`; no match allows. A refusal is `E1014` with `kind: access`.
- The auth middleware binds the caller's identity for every authenticated
  request (`core/governance/caller_identity.py`): `application` is the API
  key's name, `agent:<id>` for an Agent Passport or `console` for a human
  session; `principal` is exactly the audit actor (`user:<id>` for a session
  with a user id, else mode and subject such as `api_key:apikey:<prefix>` or
  `grantex:<subject>`). Work outside a request carries none.
- Per-model limits (`model_limits`, `GET/POST /api/v1/model-gateway/limits`,
  `PATCH/DELETE .../limits/{id}`): `max_concurrency` and `requests_per_minute`
  per provider or per model, enforced in Redis at admission just before each
  model call is sent (every reasoning turn of an agent run, every direct
  completion) and released when the model returns; a slot never released
  expires after `AGENTICORG_MODEL_GATEWAY_LEASE_SECONDS`
  (600). A call above a limit is refused with the new retryable `E1015`
  carrying `retry_after_seconds`; every check is metered in
  `agenticorg_model_gateway_limit_outcomes_total{limit,outcome}`. An
  unreachable limit store admits the call and meters `unavailable`.
- Routing policies may split their matches by weight (`targets`:
  `[{provider, model, weight}]`), stable per correlation id; the dry run
  reports access decisions and takes `application` and `principal`.
- Migration `v6z35_model_access_limits`; each change writes a signed audit row.

### Fixed - A halted workflow stays recoverable when the task queue is unavailable
- When `resume_halted_workflow` cannot be queued (broker down), the background
  executor no longer leaves the run at `running` with nobody retrying it: it
  retries in process every `AGENTICORG_OPERATOR_HALT_RETRY_SECONDS` and offers
  the retry to the queue again on every pass, until the override is released,
  the run is cancelled or the queue takes it. The pending retry is recorded on
  the run, and the `recover_halted_workflows` beat sweep
  (`AGENTICORG_OPERATOR_HALT_RECOVERY_SWEEP_ENABLED`) re-queues a run whose
  record nobody has heartbeated for three retry intervals, so a broker outage
  followed by a restart of the retrying process cannot strand it either.
  Publishing to the broker runs off the event loop.

### Added - Model gateway: routing policies in front of every provider
- `core/governance/model_gateway.py`: tenant routing policies decide the
  provider and model of a model call from its use case, data sensitivity,
  agent, business unit and language; a policy routes (provider, model or cost
  tier), fences (`allowed_providers`, a refusal rather than a substitution) or
  restricts (`in_region_only`). A request tagged `restricted` may only use a
  provider inside the deployment or one attested for the tenant's region,
  whether or not residency enforcement is on; otherwise it is refused with
  `E1014`. Applied at the agent runner (run and resume, with the sensitivity
  recorded on the agent) and `LLMRouter.complete`; every decision is logged
  with a correlation id and counted in
  `agenticorg_model_gateway_decisions_total{outcome}`.
- `GET /api/v1/model-gateway/status`, `GET/POST /api/v1/model-gateway/policies`,
  `PATCH/DELETE .../policies/{id}`, `POST .../evaluate` (dry run); tenant
  admin; each change writes a signed audit row. Table `model_routing_policies`
  (`v6z34_model_routing_policies`, tenant RLS).
- Off by default: the authority flag `model_gateway.enabled` (operator
  managed) or `AGENTICORG_MODEL_GATEWAY_ENABLED` turns it on. Docs:
  `docs/governance/model-gateway.md`.
### Added - Infrastructure attestations in the compliance report
- `GET /compliance/evidence-package` gains `infrastructure_controls`: every hosting
  control of the BFSI deployment reference with the status an operator recorded in
  the JSON file named by `AGENTICORG_INFRASTRUCTURE_ATTESTATIONS_FILE` (`verified`,
  `not_applicable`, or `not_verified` when nothing is recorded), with a summary and
  the ids the file names that the reference does not. An unreadable file reports the
  section `unavailable`.

### Changed - The compliance report no longer claims internal mTLS by default
- `encryption_in_transit.mtls_internal` is false unless `AGENTICORG_MTLS=true` is set
  by a deployment with a mesh (FINDINGS A-114).
### Changed - A paused agent can be refused, not only left out of routing
- `AGENTICORG_PAUSED_AGENTS_REFUSED` (default off): `POST /agents/{id}/run` and a
  chat that names the agent refuse a `paused` agent with 409, and a workflow
  agent step treats it as inactive, so the console's pause stops execution
  (FINDINGS A-110). Routing already skipped paused agents.

### Fixed - Operator override error code
- The operator override refusal is `E1013`. It had been registered as `E1012`,
  the code the pseudonymisation refusal (`pseudonym_restore_failed`) already
  carried, so a client could not tell a halted agent from a refused tool
  argument.
### Added - Residency enforcement and provider attestations
- `core/governance/residency.py`: with enforcement on, a provider is refused
  unless an administrator has attested, for the tenant's data region, that
  processing stays in region and the provider does not train on the data.
  Enforced at the AI credential resolver (every LLM, embedding, retrieval and
  speech credential, with the tenant carried through the explanation, SOP
  parsing and feedback analysis model calls), the managed retrieval service's
  upload, search, list and statistics (deletion stays open), the third-party
  tool hub at the dispatch boundary and tracing export (off deployment-wide;
  under tenant-scoped enforcement an enforcing or unread tenant's payloads are
  withheld); providers inside the deployment need no attestation. Strict runtimes fail closed when the region or the
  attestations cannot be read. Refusals: `agenticorg_residency_refusals_total`.
- `GET /api/v1/residency/status`, `GET/POST /api/v1/residency/attestations`,
  `POST .../{id}/revoke` (tenant admin); each change writes a signed audit row.
  Table `provider_residency_attestations` (`v6z33_provider_attestations`,
  tenant RLS).
- The compliance evidence package gains a `data_residency` section (`RES-1`):
  region, enforcement state, storage-region conformance, tenancy profile
  (`AGENTICORG_TENANCY_PROFILE`), disaster-recovery profile
  (`AGENTICORG_DR_STANDBY_REGION`, `AGENTICORG_DR_LAST_DRILL_AT`) and the active
  attestations.
- Off by default: the authority flag `residency.enforce` (operator managed) or
  `AGENTICORG_RESIDENCY_ENFORCE` turns it on. Docs:
  `docs/governance/data-residency.md`.

### Added - Operator override (halt or throttle a model, agent, workflow or the tool pipeline)
- `core/governance/operator_override.py`: an administrator places an override
  on a provider, a model, one agent, every agent, a workflow definition, a
  connector, one tool or the whole tool pipeline, in `halt` or `throttle`
  (calls per minute) mode, with a reason and an optional expiry. Enforced at
  the model router and the LangGraph reason node (never falls back to another
  model), the agent runner and resume path, `BaseAgent.execute`, the workflow
  engine before every step (the run keeps status `running` and the
  `resume_halted_workflow` worker task retries it every
  `AGENTICORG_OPERATOR_HALT_RETRY_SECONDS`, 30 s by default, until released or
  cancelled), the connector dispatch boundary and `ToolGateway.execute`; the
  agent and workflow run endpoints, and chat before its deterministic route,
  refuse early with 423. Halt beats throttle; a throttle counts
  in Redis across replicas and fails closed in a strict runtime; blocks are
  counted in `agenticorg_operator_override_blocks_total`.
- `POST/GET /api/v1/operator-overrides`, `GET .../status`,
  `POST .../{id}/release` (tenant admin); every change writes a signed audit
  row. Table `operator_overrides` (`v6z32_operator_overrides`, tenant RLS).
- Off by default: the authority flag `operator_override.enabled` (operator
  managed) or `AGENTICORG_OPERATOR_OVERRIDE_ENABLED` turns it on. Docs:
  `docs/governance/operator-override.md`.

### Added - Evidence sink for governed cases, and `make demo-case`
- `AGENTICORG_CASE_EVIDENCE_SERVICE=grantex` (default `off`) records each
  governed-case agent run into the Grantex evidence service
  (`POST /v1/evidence/cases/{case}/records`): the run context (model, prompt
  and policy versions), every provider tool call with the run grant it was
  authorised under and the upstream records it returned, the policy
  evaluation (inputs recorded unsourced: the engine keeps no per-path
  provenance), the recommendation with each memo section's citations
  resolved to the call that retrieved them, and each proposed screening
  disposition with its hit and comparisons. The service exports the signed
  package. Recording is best effort for the case: a failure is logged and
  counted (`agenticorg_case_evidence_records_total`) and never changes the
  case. `AGENTICORG_CASE_EVIDENCE_TIMEOUT_SECONDS` (default 10).
- The grant a tool call was authorised under now travels with it:
  `ToolDecision.grant_id` from the case authorizer, `ToolCallRecord.grant_id`
  in the case record and `GET .../case-record`.
- `make demo-case` (`scripts/demo_case.py`): one governed case under a run
  grant against the mock provider with `grants.enforce_closed=deny` and the
  sink on: root grant, case, every provider call authorised, an out-of-scope
  call denied with its reason, the package exported, its root and anchor read
  from the tenant audit log and verified with the `grantex-evidence` CLI.
  Every step prints `live` or `fixture`. The dev stack's Grantex service is
  started with `EVIDENCE_EXPORT_ENABLED`; the Local Stack workflow runs the
  demo after `make seed-cases`.

### Changed - OpenTelemetry 1.45
- `requirements.txt` and `pyproject.toml` move `opentelemetry-api`,
  `opentelemetry-sdk` and `opentelemetry-exporter-otlp` to 1.45.0 and
  `opentelemetry-instrumentation-fastapi` to 0.66b0 together. The SDK and the
  exporter pin the API and the 0.66b0 semantic conventions exactly, so bumping
  one of them alone left `requirements.txt` uninstallable and `pip-audit`
  unable to audit it.

### Changed - SQLAlchemy 2.1
- `requirements.txt` pins SQLAlchemy 2.1.0 and `pyproject.toml` allows the
  2.1 series (`>=2.1.0,<2.2`), replacing the cap below 2.1 (FINDINGS A-70).
  2.1's result typing no longer infers a type for raw-SQL scalars, so the
  five values mypy reported are annotated; nothing else changes at runtime.

### Fixed - the local stack answers the Grantex SDK's revocation check
- From grantex 0.7 the Python SDK's `enforce()` asks the auth service's
  `GET /v1/revocations/status` about every grant before it allows a tool call
  (`revocation_check="online"`, the new default) and refuses the call when it
  gets no answer. `pyproject.toml` allows `grantex>=0.5.1`, so CI and the
  images picked up 0.7.0 without a change here, and the checks broke:
  - the dev stack's auth service image serves the revocation endpoints only
    with `REVOCATION_FEED_ENABLED=true` and answered 404, so every governed
    tool call was refused (`grant_revoked`, `status_unavailable`), the
    underwriter runs failed and `make e2e-decisions` failed;
  - two unit test modules build a real SDK client against
    `https://grantex.invalid`, so the check failed name resolution, was
    retried with blocking sleeps and ran past the test timeout.
- `docker-compose.dev.yml` sets `REVOCATION_FEED_ENABLED=true` on the
  `grantex` service. The tests answer the status endpoint with a live grant,
  and new tests check that a grant the service reports revoked, suspended or
  unknown, or cannot report on (404, unreachable), is refused on every tool in
  the legacy and deny modes. Revocation checking is unchanged: production
  clients use the SDK default and fail closed.
### Fixed - global feature-flag rows are read by a role subject to row-level security
- `feature_flags` is FORCE ROW LEVEL SECURITY with a policy that compares
  `tenant_id` with the session's tenant, so a global row (`tenant_id` NULL)
  was invisible to a database role that is neither a superuser nor
  `BYPASSRLS`: `core.feature_flags` read no global row in any tenant session
  or under the nil tenant, and every global default - including an
  operator's global rows of the authority flags such as
  `approvals.unevaluable_condition.deny`, `grants.enforce_closed.deny` and
  `pseudonymisation.pre_model` - was ignored. Roles that bypass row-level
  security (and the Postgres tests, which ran as one) were not affected.
- Revision `v6z30_flag_global_read` adds a SELECT-only policy for
  `tenant_id IS NULL` rows. A session now sees its tenant's rows and the
  global rows, and still no other tenant's rows; a tenant row still overrides
  the global row in `is_enabled`, and authority flags still take the stricter
  of the two. A global row holds no tenant data (flag key, enabled, rollout,
  description, timestamps). There is no write policy for global rows: a role
  subject to row-level security cannot insert, update or delete one.
- **Breaking (deployments whose application role is subject to row-level
  security):** global rows that already exist start to apply to every tenant
  that has no row of its own (for authority flags, to every tenant). There is
  no flag: a Postgres policy cannot be switched per request. The opt-out is to
  clear the global rows that should not apply. Migration steps: before
  deploying, list the global rows as a privileged role with
  `SELECT flag_key, enabled, rollout_percentage FROM feature_flags WHERE tenant_id IS NULL;`
  then delete or disable each one that should not apply
  (`scripts/authority_flags.py clear <key> --global` for an authority flag).
  Rollback: the same, or restore the pre-migration backup; migrations are
  forward-only.
- `scripts/authority_flags.py set --global` and `clear --global` need a
  privileged database role (superuser or `BYPASSRLS`). Run as a role subject
  to row-level security they now exit `2` with a message saying so, before
  reading or writing anything, instead of failing on the policy (`set`) or
  reporting the global row as absent or cleared while deleting nothing
  (`clear`). `list --global` and tenant rows work with the application's
  role.
- Deploy with `--with-migrations` so the revision runs before the new code
  serves traffic.
### Security - email webhooks can take the tenant from the URL, not the payload
- The SendGrid, Mailchimp and MoEngage webhooks read the tenant from the
  signed body (a `tenant:<id>` category, `custom_args.tenant_id`, a
  `tenant_id` field), and each provider signs with one key for the whole
  deployment, so a validly signed event could name any tenant and resume that
  tenant's `wait_for_event` steps.
- Each tenant now has its own URL per provider,
  `POST /api/v1/webhooks/email/{provider}/{tenant_id}/{path_token}`, whose
  path token is an HMAC of the tenant and provider under
  `AGENTICORG_SECRET_KEY`. The provider signature is still verified; a wrong
  path answers 404 before the body is read. An event whose payload names a
  different tenant, or a value that is not a tenant id, is refused: it is not
  stored and resumes no wait, and the response counts it in `refused`. An
  active human tenant administrator reads the paths from
  `GET /api/v1/email-webhook-inbox`; API keys and agent tokens are refused.
- New setting `AGENTICORG_WEBHOOKS_TENANT_BOUND_PATHS`, default off. On, the
  shared `/api/v1/webhooks/email/{provider}` URLs answer 409 to any delivery
  with an event that names a tenant and store nothing from it, so the
  provider records the failure and can redeliver; events that name no tenant
  are processed as before. Off, the shared URLs behave exactly as before.
- `agenticorg_email_webhook_tenant_binding_total{provider, outcome}` counts
  events accepted on per-tenant URLs (`bound`), refused there
  (`tenant_mismatch`, `unbound`), and shared-URL events that name a tenant
  (`shared_path_tenant_named` with the setting off, `shared_path_refused` with
  it on), so the setting can be switched on once the first is flat. Moving
  providers: `docs/RUNBOOKS.md#email-webhooks-per-tenant-urls`.
- The API's access log replaces the path token with `[redacted]` in the
  request line for these URLs and for the governed-case provider inbox
  (`/api/v1/webhooks/providers/...`), which carried the same kind of token
  in its path: uvicorn logs every request line, so each delivery used to copy
  a reusable path token into the logs.
### Added - the decision-grant join and the governed-case suites run in CI
- A new `make e2e-decisions` job in `.github/workflows/local-stack.yml` runs
  on every pull request and every push to `main`: it starts the development
  stack with decision grants on and `AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex`,
  then runs `make seed`, `make seed-cases` and `make e2e-decisions`
  (`ui/e2e/decision-grants.spec.ts`) against the real Grantex auth service,
  and uploads the Playwright report when it fails. Its check name,
  `make e2e-decisions`, is the one to require on `main`.
- The `make dev && make test` job now runs `make seed-cases` before `make e2e`,
  so the governed-case suites (`ui/e2e/governed-cases*.spec.ts`) run instead
  of skipping. They stay on that job's stack, which has no decision-grant
  issuer, because `governed-cases-decision.spec.ts` asserts that refusal.
- Both jobs generate the seed password, and the decision job the auth
  service's administrator key, with `openssl rand` for each run and mask them
  in the log. Nothing secret is written into the workflow.
- `make e2e-decisions` now also refuses to start without
  `AGENTICORG_DEV_GRANTEX_ADMIN_KEY` or with a case decision service other than
  `grantex`, before it touches the stack, and the suite's runner uses exactly
  the administrator key the auth service was given: the development
  placeholder it fell back to, which the service never had, is gone.
- In that job a missing seed password or seed file now fails the governed-case
  suites instead of skipping them (`AGENTICORG_E2E_REQUIRE_GOVERNED_CASES=true`),
  so a wiring change cannot leave the job green with nothing checked. Locally
  they still skip.
- The tools image now includes `make`, so the tests that run the
  `make e2e-decisions` guards run in `make test` too instead of skipping.
### Fixed - the dev stack's Postgres is healthy only once it accepts TCP connections
- The `postgres` healthcheck in `docker-compose.dev.yml` ran `pg_isready` over
  the Unix socket, which the image's init-time temporary server already
  answers on a fresh volume, so `grantex-db` or `migrate` could start, get
  `Connection refused` over TCP and fail `make dev`. It now checks
  `127.0.0.1`.
### Fixed - `make seed-cases` no longer leaves every sample case failed
- Every seeded case ended `failed` with `tool_refused:grant_missing`, because
  the development tenant had no agent for either governed-case role and the
  stack no root grant to delegate from. `make seed-cases` now creates one
  active, shared `business_underwriter` and `screening_disposition` agent
  with only its reference agent's read tools, registers them with the stack's
  Grantex service and allows them `aml.cdd.onboarding`, and obtains a root
  grant covering both for the run. It is held in the seed's process only and
  never stored or printed. Re-running reuses the agents and registrations.
- The root grant comes from a second, sandbox developer key the development
  Grantex service now seeds (`SEED_SANDBOX_KEY`), because a live developer's
  authorization waits for the principal's passkey. Only `make seed-cases`
  uses it; the API and worker keep the live key.
- The seed refuses a `GRANTEX_BASE_URL` that is unset or not the stack's own
  Grantex service, stops before registering anything when the tenant already
  has an active shared agent of either role it did not create, and fails
  instead of continuing when a registration cannot be made or read. The
  grant checks are unchanged: a call the delegated grant does not cover is
  still refused.
### Added - governed-case decisions consumed by request id at the issuer (off by default)
- `AGENTICORG_CASE_DECISION_GRANT_RELEASE` (default `false`). On, recording a
  case decision consumes the request at the Grantex auth service by its id
  (`POST /v1/decisions/requests/{id}/consume`) instead of reading
  `decisionGrants` from the request's status and presenting them, so
  AgenticOrg never presents, stores or forwards a decision grant. Until the
  issuer's `DECISION_GRANT_AGENT_BINDING` is on, the status answer still
  carries the grants and they are dropped; once it is on, the issuer never
  sends one here. A case decision is AgenticOrg's own: a person decides it and
  the request names no agent, so it is not fetched with an agent's grant token.
  Readiness comes from the request's state and, when the issuer sends it,
  `decisionGrantsReady`, which can only withhold it.
- This is what keeps decisions recordable once the issuer turns
  `DECISION_GRANT_AGENT_BINDING` on, which stops returning decision grants to
  the developer API key. Order: an issuer release that serves consumption by
  request id, then this change, then this setting on, then the issuer's
  binding on; roll back in reverse. With the setting off and the binding on,
  every decision is refused `decision_not_approved`.
- With the setting on, it fails closed: a request that names an agent is
  refused `wrong_agent` (the issuer's 403 `wrong_agent` is read as that, not as
  an authentication failure), a `decisionGrantsReady` that is not a boolean
  and any consumption answer that is not a confirmed consumption of that
  request naming the grant each approver spent are refused
  (`decision_service_response_invalid`), and nothing is recorded. The receipt
  is also held to the approvals the case recorded for the request: one
  approver where four eyes was needed, more approvers than needed, or the same
  person twice is refused `decision_service_response_invalid`, and a request
  the case has no usable record of is refused `decision_request_not_found`
  before the issuer is asked.
- Off, nothing changes. `ui/e2e/decision-grants.spec.ts` is unchanged and
  holds in both states; running it with the setting on
  (`AGENTICORG_DEV_CASE_DECISION_GRANT_RELEASE=true`) needs the development
  auth service pin moved to a build that serves consumption by request id.
  See `docs/governance/decision-requests.md`.

### Security - route scope checks can refuse unknown authentication modes and cover A2A and MCP
- Route scope checks never looked at how a request was authenticated. The
  auth middleware sets `api_key`, `grantex` or `legacy` once it has verified a
  credential; a request that reached an authenticated route with any other
  mode, or none, was checked against whatever scopes it carried, so
  `agenticorg:admin` passed every route and an unmapped family needed no scope.
  Such a request is now always logged as `route_enforcement_unknown_auth_mode`
  (path and mode only). New setting `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE`
  (default `false`): off, the request is then checked on its scopes exactly as
  before; set to `true`, it is refused with `403 Unrecognised authentication
  mode; request refused` before any scope is read, also in
  `AGENTICORG_ROUTE_ENFORCEMENT_MODE=log`. No credential the middleware
  accepts is affected either way; public routes are not checked. A later
  release will turn the refusal on by default, with this setting as the
  explicit opt-out, once staging shows no unknown-mode warnings (FINDINGS
  A-95). Rollback: set it back to `false`.
- New setting `AGENTICORG_ROUTE_SCOPE_A2A_MCP` (default `false`, nothing
  changes). Set to `true`, `POST /a2a/tasks` needs `a2a:write`,
  `GET /a2a/tasks/{id}` needs `a2a:read` and `POST /mcp/call` needs
  `mcp:write` (API keys' `mcp:call` is accepted as an alias), or
  `agenticorg:admin`; discovery stays public. No role carries these scopes,
  because the routes run any agent type without a domain check, so only
  administrators' sessions reach them. **Turning it on is breaking** for
  default API keys on `POST /a2a/tasks` (they carry `a2a:read`, not
  `a2a:write`), which is the SDKs' run-by-type path, and for non-admin
  sessions. Issue replacement keys and grant agents the scopes first; the four
  scopes can be granted to agents (`PATCH /agents/{id}` `route_scopes`) while
  the setting is off. Rollback: set it back to `false`. See
  `docs/operations/grant-enforcement.md` (FINDINGS A-68).
- Once on, the refusal leaves valid callers alone only while every
  authenticated route goes through the auth middleware. A regression test now
  pins that no authenticated route accepts OPTIONS or sits under one of the
  middleware's exempt paths or prefixes, and that the known modes are exactly
  the ones the middleware sets. It found one overlap (FINDINGS A-88): an account aggregator
  consent status path whose handle begins with `callback` skips the
  middleware. It is still answered `401` while the refusal is off, and `403`
  with `AGENTICORG_ROUTE_REFUSE_UNKNOWN_AUTH_MODE=true`.
- Docs: the Python and TypeScript SDK READMEs, the MCP product model and the
  API reference's SDK launch contract say which scope `client.mcp.call`
  (`mcp:write`, or the `mcp:call` alias) and a run by agent type
  (`a2a:write`) need with `AGENTICORG_ROUTE_SCOPE_A2A_MCP` on. The API
  reference's API key section now matches the code: the routes are under
  `/api/v1/org/api-keys`, the request field is `expires_days`, the default
  scopes use the canonical `agents:write` and `connectors.read`, and the list
  response is an array that includes revoked keys.
### Changed - sanctions screening runs through the verification provider seam
- The new `sanctions_screening` connector has no endpoint or API key of its
  own: every call goes to the verification provider named by `provider` in the
  connector config - `mock` by default, which runs only in local, development,
  test and CI environments, or a provider from a separately installed package
  registered through the `agenticorg.providers` entry point. Its tools are
  `screen_entity`, `screen_person`, `screen_business`, `screen_transaction` and
  `batch_screen`, and each returns `screening_result` records with every
  candidate the provider found. A name screened without a `type` is screened
  as a person and as a business. An unknown or unavailable provider, one
  without screening, or a screening the provider does not offer fails the call
  instead of returning an empty result. All the screenings of one tool call
  share one deadline, the connector's timeout from when the call starts, so a
  batch of 50 names cannot run for 100 timeouts. Grantex scopes for the new id
  come from `manifests/sanctions_screening.json`.
- `sanctions_api`, which was built on one commercial screening service's API
  and named it in its code, is deprecated and otherwise unchanged: the same
  five tools (`screen_entity`, `screen_transaction`, `get_alert`,
  `batch_screen`, `generate_report`), the same `api_key` authentication, the
  same requests and the same responses. Creating it logs
  `connector_id_deprecated`, and the connector catalog and product counts list
  only `sanctions_screening`. An agent moves to `sanctions_screening` only when
  its connectors are changed to link it. See "The sanctions screening
  connector" in `docs/providers/plugin-packages.md`.
- **Breaking for operators:** the address of the service `sanctions_api` calls
  is no longer in the code. It is the connector config's `base_url` or, when
  that is empty, the new setting `AGENTICORG_SANCTIONS_API_BASE_URL`, which has
  no default. Before deploying, set `AGENTICORG_SANCTIONS_API_BASE_URL` in every
  environment where tenants use `sanctions_api`, to the API base URL those
  tenants have been calling. Without it, and without a `base_url` in the
  tenant's connector config, every `sanctions_api` call fails with
  `SanctionsApiNotConfiguredError` before any request is sent, and the
  connector test reports `not_configured`.
- An agent's tools, bare names such as `screen_entity` included, bind to
  `sanctions_api` unless the agent's connectors name `sanctions_screening` and
  not `sanctions_api`, so a tool can meet a grant issued under the other id.
  Every grant check - LangGraph runs, `BaseAgent` (through
  `execute_agent_tool`) and the tool gateway, in `grants.enforce_closed` off,
  warn and deny alike - now accepts a scope held under either id for the tools
  the two connectors share (`enforce_connector_grant` in
  `auth/grant_enforcement.py`). New grants name `sanctions_screening` for those
  tools and `sanctions_api` for `get_alert` and `generate_report`, which only
  the deprecated connector has. Grants issued before keep covering the tools
  they listed; `screen_person` and `screen_business` are covered once they are
  added to the agent's tools, which recomputes its scopes.
- The `sanctions_screening` connector test reports `configured` and the
  provider's name, never `healthy`, because a provider cannot be probed yet, so
  an agent that links that connector cannot be activated until the provider
  seam gains a probe (tracked in `FINDINGS.md`). `sanctions_api` probes its
  service as before.
- The Risk Sentinel and Vendor Manager prompts name `sanctions_screening` in
  their token-scope lines.
### Added - the vendor denylist audit runs in CI and warns on house terminology
- `python scripts/check_denylist.py audit` passes on the whole tree, and the
  Vendor Denylist workflow now runs it on every pull request, except title
  and description edits, and on pushes to `main`; a denylisted term in any
  tracked file fails the job. `audit <path>...` checks only the tracked files
  under those paths and fails closed when they match none.
- `scan` and `audit` print a warning, with the location and the term to use,
  for kill switch, white-label, anomaly, trust provider, verification partner
  and verification result. The terms are listed in plain text in the script
  (`HOUSE_TERMS`), and a warning never changes the exit code. Directory,
  consumer and name / version have too many ordinary meanings to flag and stay
  a review check. See "Vendor-neutral names" in `CONTRIBUTING.md`.
### Fixed - the feed fan-out cap test no longer depends on runner speed
- `test_fanout_caps_parallel_sends_for_large_tenant` forced a 0.2 s send
  timeout on 100 sockets, so a busy CI runner timed out healthy 10 ms sends
  and the delivery count failed at random. Each mocked send now yields to the
  event loop once instead of sleeping, so the sends of a batch overlap
  deterministically, and the test sets its own 30 s send timeout, so neither
  the runner's speed nor a change to the service's timeout can fail it. It
  still checks that every socket is delivered to and that no more than 32
  sends run at once.
### Added - approval decisions can be refused when a policy condition cannot be evaluated
- A new operator-managed authority flag, `approvals.unevaluable_condition`
  (default `off`; decisions behave as before), decides what happens when a
  step of an item's approval policy has a condition that cannot be evaluated
  for the item. `off` keeps the current rule: the step applies and the vote
  counts. `deny` (an enabled `approvals.unevaluable_condition.deny` row,
  global or for the tenant) refuses every decision on the item that could
  move it forward - an approval, a `defer` or any other value - with `409`
  and `detail.reason_code` `approval_condition_unevaluable`, listing the steps
  in `detail.unevaluable_steps`. A rejection is still taken: it closes the
  item at the step it has reached and approves nothing. Every step of the
  policy is checked before any vote counts, so a later step that cannot be
  evaluated cannot route the item either.
- The refusal is committed before the `409` is returned: the item's
  `context.policy_state` records `last_action: "refused"`, the reason and the
  steps, and the audit log gets a `hitl.decision_refused` event (outcome
  `denied`). The refused vote is not counted, does not make its caller the
  decider and does not bind the item to the policy, so once the policy is
  recreated with a condition that evaluates (or the flag is cleared) the same
  reviewer can decide. The next decision recorded on the item removes the
  refusal keys from `policy_state`, so a decided item never reads as refused;
  the audit event stays.
- The check runs at decision time, the only place a policy is applied to an
  item: items are raised without consulting a policy, and a policy can be
  created or edited while they wait.
- The key is reserved (`403 flag_key_reserved` through the tenant
  feature-flag API); operators set it with `scripts/authority_flags.py`. An
  unreadable flag table resolves to `deny` and logs
  `approval_unevaluable_condition_mode_lookup_failed`; only decisions other
  than a rejection, on items with a condition that cannot be evaluated, are
  refused, and a rejection never reads the flag. A global row applies to every
  tenant, also on a database role subject to row-level security once revision
  `v6z30_flag_global_read` has run (see "global feature-flag rows" above). See
  "When a condition cannot be evaluated" in `docs/approval-policies.md`.
### Added - a runbook for rotating the credential-vault key
- `docs/runbooks/vault-key-rotation.md` rotates the vault key with the tools in
  the repository: add the new key to `AGENTICORG_VAULT_KEYRING` as a
  decrypt-only entry and roll the API, worker and beat; move it to the front and
  roll again; `python -m core.crypto.rewrap --dry-run`, the rewrap and
  `--verify`; `python -m core.crypto.verify_all --check=<old id>`; then remove
  the old key. It covers moving off `AGENTICORG_VAULT_KEY` or
  `AGENTICORG_SECRET_KEY` (and what else that key signs), rollback at each step,
  checks for what the tools cannot see, and a local rehearsal. It lists what
  `scripts/deploy_cloud_run.sh` re-applies on each roll, including the public
  commerce discovery flag taken from the operator's shell; counts a roll as
  complete only once the older revisions have no instances, which is also the
  checkpoint cutoff; stops the rotation on a row rewrap cannot decrypt instead
  of rerunning it; and runs the maintenance job on the services' image. The
  `rewrap` and `verify_all` commands, the SQL checks and the rehearsal were run
  against a local database, and the deploy script against local stand-ins for
  `gcloud`; no `gcloud` step was run against Cloud Run.
- `docs/SECRETS_ROTATION.md` said no secret has a second read path and listed
  the vault only as the `AGENTICORG_SECRET_KEY` fallback. The vault keyring
  decrypts under every key it holds; the page now says so and links the runbook,
  as do `docs/deployment.md`, `docs/RUNBOOKS.md` and the documentation index.
  `docs/architecture.md` no longer says `encryption_key_ref` selects a key
  (nothing reads it) and names both KMS keys a GSTN password can be sealed
  under. `rewrap --help` gave exit code 2 for a missing keyring; it exits 1. It
  also failed to print on a console that cannot encode `→`.
- A regression test starts `uvicorn api.main:app`, the API image's command, with
  `AGENTICORG_ENV=production` and no vault key in its process environment, and
  requires it to exit non-zero with the refusal before it serves or reaches the
  database. The existing lifespan test only read the source.
- Rehearsing the runbook found three gaps, now FINDINGS A-82 to A-84: vault
  ciphertext outside the five registered columns (SSO client secrets, case push
  signing keys, governed-case excerpts, voice SIP settings) is invisible to
  `verify_all`, which calls a key unreferenced while those values still need
  it; rewrap can overwrite a credential written while it runs; and the secrets
  rotation workflow accepts the vault's own secrets. Migrating the scratch
  database also rewrote a committed migration audit record (A-85); the
  rehearsal sets `AGENTICORG_MIGRATION_AUDIT_DIR` to avoid it.

### Fixed - a governed-case agent can no longer be wired without its grant check
- The provider tool gateway refused every call it had no authorizer for, but
  `ProviderToolGateway`, `UnderwriterDependencies` and
  `DispositionDependencies` still defaulted `authorizer` to `None`, so any
  caller could build them without a grant check. `authorizer` is now a
  required field of all three, and outside local and test runtimes building
  any of them with `None` raises `AuthorizerRequiredError`
  (`core/tool_gateway/provider_gateway.py`). Tests that prove the gateway's
  own refusal pass the named `NO_AUTHORIZER_FOR_TESTS`; a regression test fails
  if production code passes it, `authorizer=None` or `authorizer_factory=None`.
- A `CaseRuntime` whose `authorizer_factory` returned `None` still moved the
  case to `in_progress` and started the underwriter, which then failed on its
  first provider call, and one built with `authorizer_factory=None` failed
  with a `TypeError` on its first run instead of a named refusal. Outside local
  and test runtimes a `CaseRuntime` can no longer be built without a factory:
  construction raises `AuthorizerRequiredError` (`core/cases/runtime.py`).
  `CaseRuntime.authorizer_for` refuses a missing factory or a `None` result
  with `authorization_unavailable` (status 503) before the case moves or a
  provider is built, for investigations and screening dispositions alike; a
  workflow `case_agent` step fails with that reason.
- `scripts/seed_governed_cases.py` passes `case_authorizer` to the runtime
  explicitly instead of relying on its default. No feature flag guards this
  change, because every production caller - the API, the workflow step and the
  seed - already supplies a real grant check (`case_authorizer`), so none of
  them behaves differently.
### Fixed - four stale production specs and a smoke test that could not fail
- TC-API-003 (`qa-module-19-health-api.spec.ts`) read the API version from
  `/openapi.json`, which the strict runtime does not serve (`api/main.py`). It
  now reads the version from `/api/v1/health` and, on hosted targets, checks
  that the origin under test does not serve the OpenAPI document (the console
  origin, unless `API_URL` points at the API).
- `tests/e2e/test_smoke.py::test_openapi_docs_accessible` passed only because
  the console answers `/docs` with its own page and a 200. It is replaced by
  `test_api_docs_are_not_published`, which checks the content of
  `/openapi.json`, `/docs` and `/redoc` on the public origin, and on the API's
  own origin when `AGENTICORG_E2E_API_URL` is set.
- D2 Promote/Rollback clicked the first fleet card, but the fleet page lists
  only the selected company's agents and the E2E tenant's agents have none.
  It now opens a shadow agent returned by `GET /api/v1/agents?status=shadow`
  (Promote is disabled on an active one) and requires Promote to be enabled.
- AGENT-CONFIG-003 created an agent with no connector, which has had no tools
  since bug sheet #46 (2026-09-14). It now links Zendesk, checks for a tool
  badge and deletes the agent afterwards instead of leaving one in the
  production tenant on every attempt. The agent is created paused, so a full
  shadow-agent budget cannot fail it.
- `qa-cafirms-may01.spec.ts` fell back to the shared suite token, whose tenant
  cannot see the tester's agent and connector. It now signs in only as the
  tester (`RU_TESTER_EMAIL` / `RU_TESTER_PASSWORD`, passed by the deploy
  workflow) and fails without them, unless the workflow has declared them
  unavailable, in which case it is skipped with a warning on the run.
### Fixed - the production Playwright suite logs in again before its session expires
- The post-deploy suite runs for about 90 minutes on one session token that
  lasts 60, so every test after the first hour failed with 401s that read as
  product failures (28 of the 33 failures on 2026-09-26). Specs now take
  `test` from `ui/e2e/helpers/test.ts`, whose fixtures log in again with
  `E2E_EMAIL` / `E2E_PASSWORD` when less than 15 minutes are left, and read
  `E2E_TOKEN` as a live export of `ui/e2e/helpers/auth.ts` instead of a copy
  taken when the spec loads. The deploy workflow passes the two credentials to
  the Playwright step.
- A token that has expired and cannot be renewed now fails every test with
  the reason, instead of producing assertion failures. A failed login is
  retried at most once every 15 seconds across all workers of the run.
- Each renewed token is masked in the Actions log, and a new workflow step
  revokes every session of the E2E user (`/auth/logout-all`) before the
  Playwright artifacts are uploaded; if it cannot, the upload is skipped. The
  suite's own global teardown ends the demo accounts' sessions the same way
  when `E2E_REVOKE_DEMO_SESSIONS=1`, so the workflow names no demo account.
  Production runs of the suite are serialised, since that revocation would
  break a second run in progress. The default Playwright config now collects
  `*.spec.ts` only.
- `sop-flow.spec.ts` and `video-recordings.spec.ts` passed without a session:
  they accepted a 401 as a validation error, or the login page as a loaded
  page. They now require the expected `400`, and `expectSignedIn` checks for
  the signed-in layout.
### Fixed - DSAR erasure no longer fails on the append-only audit log
- Every `POST /api/v1/dsar/erase` failed with a 500. Erasure rewrote
  `actor_id` on the subject's `audit_log` rows, and the `audit_log_immutable`
  trigger rejects every UPDATE and DELETE on that table. The CI schema is
  built without the trigger, so the Postgres test passed.
- Erasure now leaves audit rows unchanged and reports them as retained under
  GDPR Art. 17(3)(b) (`audit_log_retained`, `audit_log_retention_basis`). The
  user record is still anonymised and feedback pseudonymised. The result no
  longer carries `audit_log_pseudonymised`; no erase request ever completed
  with it.
- A database error while processing a DSAR request is now persisted as
  `failed`. Before, the error aborted the transaction, so the `failed` status
  could not be written and the caller got an unrecorded 500.
### Changed - one engineering guide for every contributor
- `AGENTS.md` is the repository's engineering guide, and the other guide file
  in the root carries the same text, so every contributor and tool works from
  one set of rules. It adds the hard rules (no tool attribution in commits,
  branches or docs; vendor-neutral provider interfaces; house terminology; no
  new public exposure; synthetic data; placeholder secrets) to the existing
  conventions.
- The guide no longer points at the removed `helm/` directory or at version
  strings in `api/main.py` and `api/v1/health.py` (the API reads its version
  from `pyproject.toml`), and its preflight suite list includes
  `tests/contract/` (FINDINGS A-28).

### Fixed - the production Playwright suite no longer runs local-stack specs
- `ui/e2e/regression.config.ts` ran every `*.spec.ts` against production,
  including the specs owned by `dev-stack.config.ts` and
  `decision-grants.config.ts`, which need the dev stack's seeded data and
  dev-only secrets. `decision-grants.spec.ts` throws at load without its
  variables, so the post-deploy run collected no tests and failed. The
  regression config now ignores those specs (722 tests in 78 files remain), and
  a test fails if another config's spec is not ignored.

### Fixed - the post-deploy suite checks the AP Processor's real PineLabs tools
- `tests/e2e/test_cxo_flows.py` still required `check_order_status`, a name no
  connector registers (the PineLabs tool is `get_order_status`) and which was
  removed from the defaults on 2026-09-15, so the manual post-deploy run failed
  before its synthetic and browser steps. It now requires `create_order` from
  the PineLabs connector and that every AP Processor default is registered.

### Added - agents can be granted route scopes
- Route scope checks apply to agent tokens, but registration only ever gave an
  agent tool scopes, so an agent token was refused on every scoped route. A
  human tenant admin can now grant an agent named route scopes with
  `PATCH /agents/{id}` `{"route_scopes": [...]}`; they are added to the agent's
  Grantex registration so a grant issued to it can carry them.
- Only canonical route-family scopes are accepted (never `agenticorg:admin` or
  an alias; `422`); only an active human tenant admin, checked against the user
  row at request time, may set them (`403`); only on a shared agent registered
  on Grantex (`403` for a personal agent, `409` for an unregistered one).
  Grantex is updated before anything is stored, an explicit `route_scopes`
  PATCH always rewrites the registration so drift can be repaired, and every
  change is audited.
- Route scopes are stored apart from tool scopes, so run grants and delegated
  grants never carry them. A tools PATCH and the scope backfill keep them on
  the registration.
- Existing agents have none until an admin grants them; until then an agent
  token keeps getting `403` on scoped routes, as since the route-scope fix.

### Fixed - API keys that were administrators stay administrators
- Revision `v6z29_admin_scope_compat` gives an API key the exact
  `agenticorg:admin` scope - the access it had before admin became an exact
  match - when it holds a colon-delimited admin sub-scope such as
  `agenticorg:admin:full`, was created before the exact-match fix was merged
  (2026-09-25 05:58:53 UTC), and its owner is still an active administrator.
  Keys issued later (even where the fix was deployed afterwards), keys whose
  owner is not an active admin, and look-alikes such as
  `agenticorg:administration:read`, `agenticorg:adminx` or the dot form are not
  changed: those still lose admin as described for the exact-match fix below,
  and its audit query still finds them. Status and other scopes are unchanged;
  a second run changes nothing; the ids of the keys changed are logged.
- The revision also works for a migration role without BYPASSRLS, and refuses
  to run with a tenant context set, which would hide other tenants' keys.
- Creating an API key with a scope that looks like admin but is not exactly
  `agenticorg:admin` (a sub-scope, the dot form, another case or surrounding
  space) is refused with `422`, naming the scopes and the exact one to use.
- Deploy with `--with-migrations` so the revision runs before the new code
  serves traffic.

### Changed - live feed, LLM failover and connector readiness are bounded and truthful
- **Tenant live feed:** subscriptions are single-flight per tenant with bounded
  fanout, a reconnect loop and a subscribe timeout; browsers catch up through
  paginated history. Long-lived connections recheck their credential
  periodically: a revoked, expired, re-scoped or other-tenant API key, or a
  session whose subject or scopes changed, is disconnected (1008), and an auth
  backend error disconnects with 1013 rather than keeping the socket. A socket
  that stalls a delivery is closed (1013) so its client reconnects and catches
  up instead of staying "live" on heartbeats. A subscribe timeout releases the
  Redis connection it opened. No operational writer publishes to this feed yet,
  so the dashboard keeps its audit poll.
- **`LLMRouter.complete`:** bounded by `AGENTICORG_LLM_COMPLETE_TIMEOUT_SECONDS`
  (default 90s), with `AGENTICORG_LLM_PRIMARY_TIMEOUT_FRACTION` (default 0.7) for
  the primary. Only timeouts, connection errors, 429 and 5xx fall back; invalid
  requests, configuration errors and spend caps never do. An explicitly selected
  model, which agent runs normally pass, falls back only to a fallback model from
  the same provider, so an outage never moves a request to another provider.
- **Connectors:** list and detail return tenant- and company-scoped readiness
  evidence (credential presence, health freshness, disabled/missing states)
  without selecting or returning encrypted credentials, and the UI pages past 50
  rows. Replacing a connector's credentials clears its last health check, and a
  token refresh that revives a connector which was not healthy clears it too, so
  the list does not show "Recently checked" for credentials no check has used. A
  health check still does not prove provider scopes, contracts or sync.
- Public status, README, landing and resilience docs describe these limits.

### Fixed - the credential vault no longer falls back to a published key
- Outside a local, dev, development, test or CI runtime, the credential vault
  now refuses its code default (`dev-only-vault-key`), any secret value
  published in this repository (the code defaults, `.env.example`, the compose
  files, the `Makefile`, CI and scripts; compared ignoring case and surrounding
  whitespace), and a blank key. It needs `AGENTICORG_VAULT_KEYRING`, or
  `AGENTICORG_VAULT_KEY`, or as before `AGENTICORG_SECRET_KEY`, in the process
  environment. Before, a production runtime whose secrets were only in `.env`
  (loaded into settings, but not into the environment the vault reads) sealed
  every connector credential and LangGraph checkpoint under the default, which
  anyone with this repository can derive.
- An unset or unrecognised `AGENTICORG_ENV` counts as production here.
- The API refuses to start without a usable key. So does a worker: the check
  runs on Celery's `worker_init` in the main worker process and exits, and the
  Cloud Run entrypoint (`scripts/run_worker.py`) checks before it starts its
  health server. The checkpointer reports `checkpoint_encryption_key_missing`.
- Refusals and keyring parse errors name the setting and the entry's position
  or id, never key material. Before, an entry missing its `id:` prefix (which
  is the raw key) was quoted in the error.
- `Settings` in a strict runtime now refuses every published placeholder as
  `AGENTICORG_SECRET_KEY`, not only `dev-only-secret-key`.
- A keyring that is set but has no entries is refused in every runtime
  instead of falling through to the single-key path. A blank
  `AGENTICORG_VAULT_KEY` now counts as unset (the next fallback applies), and
  a keyring entry with no key material (`v1:`) is refused. Before, both
  derived the key from the empty string, which is public.
- **Breaking for operators:**
  - A strict runtime without a vault key in its process environment no longer
    starts; set `AGENTICORG_VAULT_KEYRING` (see `docs/deployment.md`). CI's
    background Celery worker now sets `AGENTICORG_ENV=ci`, and local
    development must export `AGENTICORG_ENV=development` (see the README).
  - A runtime that ran on the default has credentials sealed under a public
    key. Strict runtimes refuse the default even as a keyring entry, so rewrap
    those rows from a trusted machine with `AGENTICORG_ENV=local` and
    `AGENTICORG_VAULT_KEYRING=v2:<new>,legacy:dev-only-vault-key`, then rotate
    every affected provider credential.
  - Rows sealed under an empty-string key (a blank `AGENTICORG_VAULT_KEY`, or
    a `v1:` entry) can no longer be decrypted in any runtime. Treat those
    provider credentials as exposed and re-enter them.
- The `AGENTICORG_SECRET_KEY` fallback is unchanged; FINDINGS A-71 tracks
  giving every deployment a dedicated vault key.

### Fixed - approval policies cannot be satisfied by one person
- A reviewer may vote once per approval item, across every step of its
  policy. Before, the duplicate-vote check covered only the current step, and
  a step's count resets when the item advances, so one senior user could
  approve each step in turn and decide a multi-person policy alone. A second
  vote now gets `409`.
- A policy step whose condition cannot be evaluated - a field the item does
  not carry, a non-numeric ordering comparison, an unparseable expression -
  now applies instead of being skipped, so the item needs that step's
  approvals. So does a malformed operand (`status ==`, an unterminated
  quote). `NOT` over a missing field no longer counts as a match.
- If an item's policy is deleted mid-approval, another policy now resolves
  for it, or the step it is waiting on is removed, decisions on the item get
  `409` and it stays pending, instead of being decided on the next single
  vote with no policy applied.
- A reviewer is matched on every identifier their session carries (user id,
  subject and email), so an invite-acceptance session and a login session for
  the same person count as one reviewer.
- **Breaking for operators:** items whose policy conditions name fields their
  context lacks now need those steps' approvals. Someone who voted at one step
  cannot approve or reject at a later one, so a policy that needs the same
  person twice cannot complete. Editing (deleting and recreating) a policy, or
  adding one that now resolves instead, leaves items in flight under the old
  one refusing every decision until they expire; there is no override yet
  (FINDINGS A-67). See `docs/approval-policies.md`.

### Fixed - agent tokens need a route's scope, like API keys
- **Breaking:** route scope checks now apply to Grantex agent tokens. Before,
  any credential other than a user session or an API key skipped them, so an
  agent token granted only `tool:mock:read` could read the whole tenant audit
  trail and run any agent. An agent token must now carry the route family's
  scope (`agents:read`, `agents:run`, `audit:read`, ...) or `agenticorg:admin`,
  or it gets `403`. An authenticated request with an unrecognised
  authentication mode is refused the same way.
- A tenant admin grants an agent the route scopes its token needs with
  `PATCH /agents/{id}` `route_scopes` (see "Added - agents can be granted route
  scopes" above). A2A and MCP routes are unaffected.
  See `docs/operations/grant-enforcement.md`.

### Fixed - three external changes that broke CI on every pull request
- `make check`: SQLAlchemy 2.1.0 was released. The tools image and the
  production API image both install `pyproject.toml`'s ranges, so mypy ran
  against 2.1.0 and failed on five unchanged files, and the next API image
  would have shipped 2.1.0 untested. The range is capped below 2.1, which
  keeps both on the 2.0 line `requirements.txt` pins (FINDINGS A-70).
- `make dev`: quay.io removed the MinIO server image the stack pinned, and
  Docker Hub's `minio/minio` now needs credentials. Both compose files use
  `cgr.dev/chainguard/minio`, pinned by digest. It defaults to uid 65532,
  which cannot open a `miniodata` volume the old image wrote as root, so it
  runs as root as that image did and existing volumes keep working. The
  air-gap image list follows.
- UI container scan: CVE-2026-93990 in `libexpat` 2.8.4-r0, which every
  current `nginx:alpine` digest still ships. `Dockerfile.ui` and
  `Dockerfile.ui.cloudrun` - the image Cloud Run serves, which CI did not
  scan - upgrade it to 2.8.5-r0 and fail the build if that version is
  unavailable. The container scan now covers `Dockerfile.ui.cloudrun` too.

### Security - admin is the exact `agenticorg:admin` scope, never a prefix
- Six admin checks accepted any scope *starting with* `agenticorg:admin`.
  Every agent is registered with `agenticorg:{domain}:read` and its domain is
  free text, so a grant token for an agent in a domain such as
  `administration` passed `require_scope` - including the tenant-admin gate
  on API-key creation - and was treated as an administrator for agent,
  connector and approval visibility, report schedules, admin-only RPA
  scripts and merchant commerce configuration. Free-form API-key scopes had
  the same effect. All six now use `core.rbac.has_admin_scope`, an exact
  match, and a test fails if any production module matches the admin scope
  by prefix again.
- **Breaking:** an API key or grant holding a scope such as
  `agenticorg:admin:full` is no longer an administrator. Nothing in this
  repository issues such a scope, but API-key scopes are free-form. To find
  any in use:
  `SELECT tenant_id, id, name FROM api_keys WHERE EXISTS (SELECT 1 FROM unnest(scopes) s WHERE (s LIKE 'agenticorg:admin%' OR s LIKE 'agenticorg.admin%') AND s <> 'agenticorg:admin');`
  Replace such a scope with `agenticorg:admin` if the key should be an
  administrator. Revision `v6z29_admin_scope_compat` does this for keys with a
  colon-delimited sub-scope that predate the fix and belong to an
  administrator; the query still finds the rest.

### Fixed - governed-case provider authorization
- The reference underwriter and screening agent now refuse every provider call
  without an authorizer and a positive delegated-grant check. This applies even
  when general grant enforcement is off or in warn mode. Tenants must register
  one active, shared agent for each role before enabling governed cases; cases
  without that configuration fail closed rather than making unchecked calls.

### Added - the governed-case decision is proven end to end
- `make e2e-decisions` (`ui/e2e/decision-grants.spec.ts`) takes a real
  four-eyes decline across both systems in a browser, with nothing stubbed:
  the console asks for the decision, two different people sign in on the auth
  service's own approval page with a second factor and approve there, the same
  person is refused the second approval, and the console records the decision
  with both approvers and both grant ids. It also proves a single approval with
  step-up, and that a case which changed after the approval is refused with
  `case_changed`. No decision grant reaches the console's browser, and the
  suite fails if one does.

### Fixed
- A consumed decision is recorded with the decision grant each approver spent.
  The issuer lists the approvers and, separately, the grant ids it consumed,
  and those two arrays cannot be paired by position: `jtis` is in the order the
  grants were presented and `approvers` is in approval order, so for a
  four-eyes decision they can disagree and index pairing would put one person's
  approval against the other's credential. The pairing is now taken from the
  issuer's own record of the request, where the grant and the approver are
  stated together, and any approver that does not match exactly one approval -
  or that resolves to a grant the issuer did not say it consumed - is refused
  with `decision_service_response_invalid`. Before this the client read a grant
  id the issuer does not send, recorded an empty one, and the case document
  schema rejected it: the first real four-eyes decision could not be recorded
  at all.

### Added - the development stack serves decision grants
- The pinned Grantex auth-service image moves to a build of Grantex `main`
  (`5b867f68`, `ghcr.io/mishrasanjeev/grantex-auth-service@sha256:b73668a3...`),
  the first one that serves `/v1/decisions/...` and the approval page (PRD
  G-3). `docker-compose.dev.yml` configures it: `DECISION_GRANTS_ENABLED`, a
  vault key, an `ADMIN_API_KEY` and a step-up policy, plus an identity provider
  for approvers only (`oidc-approvers`, two fixture people, separate from the
  console's development SSO). The auth service now listens on the port it
  publishes, because its approval page has to be reached on the same origin it
  checks form posts against, and that origin must be https or loopback.
- **A stack that does not ask for decision grants runs without any of it.**
  `AGENTICORG_DEV_DECISION_GRANTS=true` turns on `DECISION_GRANTS_ENABLED` and
  the relaxed outbound rules the development identity provider needs, and the
  provider itself is in the `decisions` compose profile, so `make dev` does not
  start it. Without the opt-in the decision routes and the approval page answer
  404 and no administrator key is configured.
- **`AGENTICORG_CASE_DECISION_SERVICE` still defaults to off**, in the
  development stack included; a run opts in with
  `AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex`. Nothing changes for a stack
  that does not.
- `scripts/dev_stack_smoke.sh` checks the approval page and the approver
  identity provider's discovery document.

### Fixed
- The development OpenID Connect stub reads a chunked request body. Node's HTTP
  client sends a POST body with `Transfer-Encoding: chunked` when no
  `Content-Length` is set, which the stub read as an empty form and answered
  with an OAuth error about the wrong thing. It also logs the error it returns,
  and its health check honours `OIDC_STUB_PORT`.
- `.gitattributes` keeps shell scripts LF, so the stack's Linux containers can
  run them from a Windows checkout (FINDINGS A-60).

### Changed — breaking for tenants that turn it on
- `grants.enforce_closed` can now be set to `deny` (tenant flag
  `grants.enforce_closed.deny` or `AGENTICORG_GRANTS_ENFORCE_CLOSED=deny`).
  **This will refuse tool calls that have been quietly succeeding:** scope
  enforcement was effectively off on the common path, so an agent without a
  resolvable grant, or with one that does not cover a tool, now fails the
  run instead of calling the tool. Run the tenant in `warn` first and review
  `scripts/grant_enforcement_report.py`. A refused call fails the run with
  `error` `grant_denied: <reason>`; run results gain an optional
  `grant_denial` object, which `POST /agents/{id}/run` also returns and
  writes to its audit row. Default stays `off`; nothing changes unless a
  tenant or the deployment opts in. Rollback: disable the tenant's deny flag
  or reset the deployment default. See the runbook in
  `docs/operations/grant-enforcement.md`. Agents registered before this
  release carry scopes Grantex's check cannot satisfy: run
  `scripts/refresh_grantex_scopes.py --apply` before switching their tenant
  to `deny`.

### Added
- Prometheus metrics are now readable. Every service (API, Celery worker, beat)
  serves the registry at `GET /metrics` on `METRICS_PORT` (default 9090), a
  port Cloud Run does not route, so the endpoint has no external surface and no
  credential to manage; a service refuses to start the listener on the port it
  serves traffic on. The worker exports in multiprocess mode, because Celery's
  prefork pool means counters are incremented in forked children. Until now
  every instrument in the codebase was write-only (FINDINGS A-56).
- The six PRD §10 alerts and one companion, as committed definitions. This
  makes PRD §10 achievable, not met: no sample has yet travelled the whole path
  from a process to a notification. Files:
  `monitoring/prometheus/agenticorg-alerts.yml`, unit-tested with `promtool
  test rules` in CI, with the Cloud Monitoring policies in
  `infra/terraform/monitoring/` built from that same file and a dashboard in
  `monitoring/dashboards/`. `scripts/check_alert_rules.py` refuses a rule that
  reads a raw counter (instances scale to zero, so a counter's value depends on
  which are alive), a rule without a `for`, or a rule reading an instrument not
  declared in `observability/alert_contract.py`.
- `agenticorg_case_decision_dwell_seconds{dwell_source,approval_stage}`: the
  dwell the issuer's approval page measured, recorded when a decision is
  recorded. This is the authoritative figure the rubber-stamping alert reads;
  the console's own render-to-submit metric remains advisory telemetry and is
  never read by an alert.
- `agenticorg_chain_verifications_total` (cited passages and promotion-history
  chains verified against their digests) and
  `agenticorg_budget_cap_events_total` (spend caps warned and exhausted).
- Two more promtool fixture files (fire and no-fire for all seven alerts, and
  the exact `for` boundaries), and two probes:
  `scripts/probe_metrics_multiprocess.py`, which proves a forked child's
  metrics reach the exporter and runs in CI, and `scripts/probe_alert_gate.py`,
  which mutates the alert definitions and checks the gate catches each one.

### Removed
- `scripts/generate_batch2.py`, `generate_batch3.py`, `generate_batch4.py` and
  `generate_batch5.py`. They were the original scaffold for 107 paths, 85 of
  which exist today and have years of hand editing behind them - `api/main.py`,
  `api/deps.py`, `auth/jwt.py`, `auth/grantex.py`, `auth/scopes.py`,
  `observability/metrics.py`, `observability/alerting.py`, `core/agents/*`,
  `audit/*`, `scaling/*`. Each was written with a bare `open(path, "w")` and no
  guard, nothing referenced them, and the metrics they would restore carry
  per-tenant, per-agent labels that `observability/metrics.py` forbids. Git
  holds the day-one version of every generated file, and how it changed since,
  which the scripts cannot.
- The `budget_pct_high` threshold rule and the Grafana "Budget Utilization"
  panel, with the `agenticorg_agent_budget_pct` gauge behind them. The gauge
  has never been given a value by anything, so the rule could not fire and the
  panel could not draw: both were reporting on a metric that does not exist.
  Restoring them means writing the gauge first, with labels that are not
  per-tenant and per-agent.

### Fixed
- `docs/deployment.md` told operators to `curl http://localhost:8000/metrics`,
  which has never served anything.

### Security — breaking for machine callers of the governed-case routes
- Deciding, withdrawing, reviewing a screening disposition and approving an
  information request on a governed case now need a human session. API keys
  and Grantex agent tokens are refused with 403 `human_session_required`.
  Previously any caller with the `approvals:write` scope could take those
  actions, and agent tokens skipped the RBAC scope family check entirely, so
  an agent token issued for tool access could withdraw a case or approve an
  information request — and the action was recorded as `user:<the token's
  subject>`, which read like a person. The actor written onto a case is now
  derived from the session and says what acted: `user:<id>`, `api_key:<prefix>`
  or `agent:<id>`. A disposition review and an information-request approval
  additionally need the case to be `awaiting_decision`. Identities are still
  never read from the request body. Submitting a case and starting an
  investigation stay open to machine callers, recorded under their own labels.
  Rollback: revert this change; there is no flag, because leaving the routes
  open is the defect. See `docs/governance/case-lifecycle.md` and FINDINGS
  A-46 for the separate question of which roles should hold `approvals:write`.

### Security — breaking for anyone already receiving provider webhooks
- Inbound provider webhooks for governed cases no longer act on a delivery
  they cannot verify, and are bound to one tenant. Previously an unsigned or
  forged body posted to the unauthenticated route was used as a trigger: a
  subject reference was read out of it and every matching case awaiting a
  decision was re-investigated, which replaced the memo and cleared the
  analyst's screening-disposition reviews, and could fail the case outright if
  the provider was briefly unreachable. Now an unverifiable delivery is
  recorded and counted only (nothing is read from the body, no case is looked
  up, no investigation runs), and the route has moved to
  `POST /api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}` with an
  unguessable per-tenant path token, so an event signed with a provider's
  shared secret cannot be replayed at another tenant's inbox. A re-query
  triggered by a verified event records that event on the `in_progress`
  transition, and a case that was awaiting a decision returns to
  `awaiting_decision` with the memo it had (transition reason
  `re_evaluation_failed:<reason>`) instead of failing when the provider cannot
  be reached. Every refusal answers the same 202, and the body is read under
  the 256 KiB cap before any database work. **Action required:** re-point each
  provider at the tenant's new path, read from
  `GET /api/v1/case-push/provider-webhook-inbox?provider={provider}` (tenant
  admin); the old tokenless path is gone and answers 404. Rotating
  `AGENTICORG_SECRET_KEY` changes every tenant's path. New counter outcome
  `agenticorg_provider_webhook_receipts_total{outcome="unbound"}`.

### Added
- Citations that a reviewer can check end to end (PRD A-6, `core/tool_gateway/provider_gateway.py`,
  `core/cases/runtime.py`, `api/v1/governed_cases.py`, `ui/src/components/governed-cases/MemoView.tsx`).
  The tool gateway now captures the record each piece of provider evidence is attached to, exactly
  as it arrived; the underwriting memo attaches every excerpt reference its evidence cites (before,
  `memo.excerpts` was empty on every case, so every citation read "not attached to this memo"), and
  the case keeps the passages in `governed_cases.excerpts`.
  `GET /governed-cases/{case_ref}` gains `tool_calls` (each provider call with the provider and
  the record ids it returned) and `excerpts` (references only), and
  `GET /governed-cases/{case_ref}/excerpts/{excerpt_ref}` serves one passage, decrypted and
  re-hashed: a passage that no longer matches the digest the memo cites is refused
  (`excerpt_integrity_failed`), never shown. `DELETE /governed-cases/{case_ref}/excerpts` forgets
  the passages and keeps the references, and a case keeps at most its 200 most recent passages,
  newest capture per reference winning. The console shows a passage on request, marks a citation
  whose record the run never returned (provider and record id together), and says plainly when a
  case carries no tool calls to check citations against. Passages are stored encrypted with the
  tenant's key in `governed_cases.excerpts_encrypted` (migration `v6z28_case_excerpts`, additive
  and forward-only). Closes FINDINGS A-48.
- Decision requests for governed cases (PRD G-3, `core/cases/decision_requests.py`,
  `POST /api/v1/governed-cases/{case_ref}/decision-requests`,
  `GET .../decision-requests/{request_id}`): AgenticOrg asks the Grantex auth service for a
  decision on one semantic action, records the request on the case and reports the live status of
  the approvals; the person approves only on the auth service's own approval page, which handles
  sign-in, step-up, the four-eyes rule and the authoritative dwell measurement.
  `POST .../decision` accepts `decision_request_id`, fetches the minted grants server-side (a
  decision grant never reaches the browser) and consumes them for the exact action and the case's
  current version through `CaseRuntime.decision_verifier`, so a case that changed after the
  approval is refused with `case_changed`. `client_dwell_ms` on both routes is advisory telemetry
  only (`agenticorg_case_console_dwell_seconds{stage}`), never the authoritative dwell. Off by
  default: without `AGENTICORG_CASE_DECISION_SERVICE=grantex` decision requests answer
  `decision_service_not_configured` and every decision is still refused with `decision_required`.
  New metrics `agenticorg_case_decision_requests_total{outcome,result}` and
  `agenticorg_case_decision_grants_consumed_total{outcome,result}`; new documentation
  `docs/governance/decision-requests.md`. Migration `v6z27_case_decisions` adds
  `governed_cases.decision_requests` (additive, forward-only). The issuer's answers are parsed
  strictly - a field the console states as fact (the action and its hash, the case version, how
  many approvals are required, each approval's subject, authentication, position and dwell
  source) is refused when absent rather than defaulted - the semantic action is bound to the
  tenant as well as the case, `GRANTEX_BASE_URL` must be set explicitly when the service is on,
  and consumption runs with its own shorter deadline while the case row is locked. A case change
  registers the new version with the issuer, so it supersedes an open request and revokes unused
  grants; an issuer answering `404 NOT_FOUND` for a request it no longer holds is reported as
  `decision_request_not_found` rather than as the service being switched off. Registering a case
  version is counted in `agenticorg_case_version_announcements_total{result}`, so an issuer that is
  persistently unreachable is visible rather than only logged.
- Approvals console screens for governed cases (PRD A-9, `ui/src/pages/GovernedCases.tsx`,
  `ui/src/pages/GovernedCaseDetail.tsx`): a queue at `/dashboard/approvals/cases` with the
  state counts, policy tier and proposed recommendation, and a case screen with the cited
  underwriting memo (every evidence entry naming the provider, upstream record, field and
  retrieval time, linked to the cited-records index; an excerpt reference is linked when the
  memo carries the excerpt and labelled as not attached when it does not, which today is every
  case - see FINDINGS A-48), the
  policy score with every fired rule and the evidence values it read, and the case history.
  Sections the provider could not supply are shown as unchecked rather than clear. The
  screens are read-only: no decision, review or state change is made from them. A tenant
  without `governed_cases.enabled` is told so instead of seeing an empty queue.
- Screening disposition review in the approvals console
  (`ui/src/components/governed-cases/ScreeningDispositions.tsx`, PRD §3 US-3): each hit shows
  the list entry it concerns, the proposed outcome, the confidence band as metadata, the
  rationale, the per-identifier comparison table (name, date of birth, nationality, address,
  associated entities) and the cited evidence. An analyst accepts the proposal or overrides it
  with a different outcome and a mandatory written reason; the analyst identity comes from the
  session, a review is written once, and the form appears only while the case is awaiting a
  decision. Nothing closes a screening hit.
- The decision action in the approvals console
  (`ui/src/components/governed-cases/DecisionPanel.tsx`, PRD A-9): request a decision, open the
  decision-grant issuer's own approval page in a new window, watch the approvals arrive and record
  the decision once the grants exist. The console has no approve control at all - step-up, the
  dwell measurement and four eyes happen on the issuer's page - and "Record decision" stays
  disabled until the approvals are complete. The four-eyes state names the first approver and says
  the same person will be refused; a case that changed after the approval, a missing issuer and
  every refusal reason (`decision_required`, `decision_not_approved`, `same_approver`,
  `case_changed`) are surfaced with their code. An outcome that differs from the memo's
  recommendation needs a written reason, which is shown to the approver. The console's own dwell
  (case screen render to submit) is sent as advisory telemetry only; the authoritative dwell is the
  one the approval page measured, and it is shown per approval. A request that can never be
  approved (the case changed under it, or the issuer reports it superseded, cancelled or expired)
  stops the polling, drops the Record control and puts the request form back, so asking for a new
  decision is always possible; the approval page is opened only over https, except on a console
  served over http (a development stack).
- `make seed-cases` (`scripts/seed_governed_cases.py`): development-only sample governed
  cases - turns `governed_cases.enabled` on for the seeded tenant, submits one case per mock
  provider fixture and runs the reference agents against the stack, writing the new case
  references for the browser suite. It refuses any runtime that is not development or test.
- Browser end-to-end coverage for the console screens (`ui/e2e/governed-cases.spec.ts`), with
  an axe accessibility scan (WCAG 2.1 A and AA) and a phone-width pass; the run regenerates
  the screenshots in `docs/console/governed-cases.md`.
- Governance documentation (`docs/governance/README.md`): how grants, policy
  scores and human decisions interact for governed cases - read-only tool sets
  and the grant check at the tool gateway, deterministic policy tiers that
  gate the recommendation while model confidence stays metadata, decisions
  recorded only with verified decision grants, analyst reviews and approved
  information requests, the signed hand-off, and what is recorded for audit.
  Links the case lifecycle, hand-off, agent and security pages.
- Portable case hand-off for governed cases (`core/cases/push.py`, PRD A-8):
  a `case_push` event is written to `case_push_outbox` in the same transaction
  as the case change (`case.completed`, `case.updated`, `case.decided`) and
  delivered to the tenant's configured endpoint with an HMAC-SHA256 signature
  over `<event id>.<timestamp>.<body>` in `AgenticOrg-Signature`
  (`v1=<key_id>:<hex>`, one entry per key so keys rotate without downtime),
  retried with exponential backoff and dead-lettered on rejection or after 10
  attempts; dead letters replay with the same event id. REST retrieval of the
  same document. Inbound provider webhooks at
  `/api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}` are verified
  with the provider, de-duplicated by event id, and only ever trigger a
  re-investigation from the provider; unverifiable bodies are recorded and
  counted and change nothing (see the security entry above). Signing keys
  are stored encrypted per tenant. Migration `v6z26_case_push` (three tables,
  forced RLS). Celery tasks `dispatch_case_pushes` and `sweep_case_pushes` (the
  sweep is a no-op unless `AGENTICORG_CASE_PUSH_SWEEP_ENABLED=true`). Metrics
  `agenticorg_case_push_{enqueued,deliveries,dead_letters}_total`,
  `agenticorg_case_push_dead_letter_backlog`,
  `agenticorg_case_push_attempt_duration_seconds` and
  `agenticorg_provider_webhook_receipts_total{outcome}`; alert
  `case_push_dead_letters_present`. Requires `governed_cases.enabled` and a
  configured endpoint. See `docs/governance/case-hand-off.md` and the runbook.
- Governed business cases (`core/cases/`, PRD A-8), behind the per-tenant
  flag `governed_cases.enabled` (default off; unreadable counts as off): case
  lifecycle `submitted`/`in_progress`/`awaiting_decision`/`decided`/
  `withdrawn`/`failed` with version-guarded transitions recorded in
  `governed_case_transitions`; a runtime that runs the Business Onboarding
  Underwriter and Screening Disposition agents for a case and stores the memo,
  policy result, screening results, dispositions and agent case records;
  decisions recorded only with verified decision grants (the shipped verifier
  refuses everything with `decision_required` until decision grants land).
  New workflow step type `case_agent` and example workflows
  `workflows/examples/business_onboarding.yaml` and `screening_disposition.yaml`.
  New API under `/api/v1/governed-cases` (submit, list, stats, detail, case
  record, investigate, withdraw, decision, disposition review, information
  requests). Migration `v6z25_governed_cases` adds two tables with row-level
  security. Metric `agenticorg_governed_case_transitions_total{from_state,to_state}`.
  Settings `AGENTICORG_CASE_PROVIDER`, `AGENTICORG_CASE_POLICY_DIR`,
  `AGENTICORG_CASE_LLM_MODEL`. See `docs/governance/case-lifecycle.md`.
- Screening Disposition reference agent (`core/agents/screening_disposition/`,
  PRD A-7 / US-3): for one screening hit it re-screens the subject through
  the provider tool gateway to confirm the hit, compares name, date of birth,
  nationality, address and associated entities, and proposes `true_match`,
  `false_positive` or `insufficient_information` with a confidence band by
  fixed rules, producing a schema-valid, cited `screening_disposition` with
  `review: null`. The model writes the rationale from comparison results only;
  a rationale that states another outcome, talks of closing the hit or repeats
  untrusted text is replaced by a template rationale. No automatic closure in
  any configuration: the tool set is read-only screening and no closing tool
  can be configured. `apply_review` records an analyst's acceptance or
  override (reason required, analyst identity from the authenticated session,
  written once). Metric
  `agenticorg_screening_dispositions_proposed_total{outcome,band}`. Shared
  `core/agents/case_model_call.py` makes the guarded, pseudonymised prose call
  for both reference agents. Nothing in the platform runs the agent yet. See
  `docs/agents/screening-disposition.md`.
- Business Onboarding Underwriter reference agent
  (`core/agents/business_underwriter/`, PRD A-7): resolves an application
  through a verification provider, starts verification and polls while it is
  pending, reconciles ownership against declared owners (`missing_owner`,
  `undeclared_owner` at a configurable threshold, 25% by default), screens
  every party, analyses web presence only through the sandboxed extractor,
  evaluates the deterministic policy and assembles a schema-valid, cited
  `underwriting_memo` with a policy-gated recommendation and a missing-items
  list. The model writes section summaries only, from codes and counts, behind
  the untrusted-content guard and (when `pseudonymisation.pre_model` is on)
  pseudonymisation; each summary is checked against its section's citations.
  Every memo evidence entry is checked against the records returned in the run,
  and the run fails closed otherwise. A capability the provider does not offer
  yields a `not_available` section. Prompts are versioned and pinned by
  SHA-256, recorded with every tool call's request and response hashes in the
  case record. Requests for more information use approved templates released
  only by a human approval bound to the proposal digest. New provider tool
  gateway (`core/tool_gateway/provider_gateway.py`) holds read tools only and
  takes the run's grant check as an authorizer that fails closed. Metrics
  `agenticorg_provider_calls_total{capability,outcome}`,
  `agenticorg_provider_call_duration_seconds{capability}` and
  `agenticorg_case_agent_runs_total{agent,outcome}`. New
  `core.policy.document.policy_result_document`. Nothing in the platform runs
  the agent yet, so existing behaviour is unchanged. See
  `docs/agents/business-underwriter.md`.
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
  See `docs/operations/grant-enforcement.md`.
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
- **Break (Python API):** `core.langgraph.tool_adapter.execute_agent_tool` and
  `core.tool_gateway.gateway.ToolGateway.execute` take a required keyword
  `run_grant` as well. `BaseAgent` passes its run grant in every mode; tests
  that exercise only the legacy checks pass
  `auth.run_grants.NO_RUN_GRANT_FOR_TESTS`, which production code may not use.
- Grant enforcement now covers every agent run entry point: chat, A2A and
  MCP (the run agent's grant; a caller Grantex token issued to another agent
  must also allow every call), voice and
  per-type wrappers through the runner, `resume_agent`, and workflow agent
  steps, collaboration steps, workflow resume and the sales pipeline through
  `BaseAgent` and the tool gateway. Workflow `connector_tool` steps have no
  agent and therefore no grant: recorded in `warn`, refused in `deny`. In
  warn and deny the tool gateway runs the grant check and all of its legacy
  checks. `off` is unchanged.
- A caller Grantex token is bound on every route that starts a run:
  `POST /agents/{id}/run`, `POST /workflows/{id}/run` (every step type that
  calls tools, and sub-workflows) and the sales pipeline routes, as well as
  chat, A2A and MCP. A run started with a caller token and resumed later
  without it refuses tool calls in warn and deny
  (`grant_missing`/`caller_token_unavailable`); only the caller's agent id is
  stored, never the token.

### Fixed
- Storing a governed case's cited passages refuses a passage for which no
  tenant key was resolved (`excerpt_key_unresolved`) instead of falling back to
  the deployment's legacy key. Callers pass `None` for "not resolved"; `""`
  remains the legacy key and encrypts as before.

- Task code called outside a worker no longer runs on the Celery runner loop.
  `core.tasks.async_runner.run_async` kept one event loop per process, which is
  right in a worker — it is the only loop there — but an API process that
  reached a task body from a thread with no running loop (an `asyncio.to_thread`
  call, of which `api/` has two dozen) left shared-pool connections bound to a
  loop no request runs on, and the next request to check one out failed. The
  choice is now made by the role of the process: a worker or beat process
  (marked by the Celery signals, or by `AGENTICORG_WORKER_PROCESS=1`) keeps the
  persistent loop; everywhere else the work runs through
  `core.database.run_db_coroutine_sync` on a private engine.
- The shared database pool now reports when it is used from a second event
  loop: when a session is opened there (which covers a caller that binds
  `async_session_factory` itself and is handed a warm connection), when the
  pool opens a connection there, and when it hands an existing one out.
  It binds to the first loop that uses it and logs `db_cross_loop_use` once per
  foreign loop with the remedy, counting
  `agenticorg_db_cross_loop_checkouts_total{mode}`, instead of leaving the
  `'NoneType' object has no attribute 'send'` that used to surface in an
  unrelated request later. `AGENTICORG_DB_CROSS_LOOP_GUARD=raise` turns it into
  a `CrossLoopConnectionError` at the point of the mistake and `off` silences
  it; warn is the default in production, where the call fails either way and
  raising would turn latent pool problems into new 500s. A test run counts the
  guard's trips and fails when they exceed the committed
  `cross_loop_baseline.txt` (54 trips, measured in CI, roughly 27 distinct
  uses — one use trips the guard once or three times, averaging about two: the
  session check sees every violation and the two pool hooks fire together on
  about half of them; the unit job trips it 0 times), and `scripts/check_cross_loop_baseline.py` refuses a raised baseline.
  Both overrides remain deliberate and silent-free: `AGENTICORG_CROSS_LOOP_BASELINE`
  replaces the number for one run and `AGENTICORG_DB_CROSS_LOOP_GUARD=off`
  stops the counting, and a run with the guard off says so on its summary line, so the existing debt (FINDINGS
  A-58) burns down and a new violation fails immediately.
- `AGENTICORG_WORKER_PROCESS=1` is set on the Celery worker and beat
  entrypoints and in the development stack, so a worker started with
  `--pool=solo`, `threads` or gevent — which never fires
  `worker_process_init` — is still recognised as a worker. See `RUNBOOKS.md`. The live feed, workflow state, workflow event-wait and bridge
  state stores resolve their session factory per call rather than caching the
  shared one, so a synchronous caller's private engine reaches them too.
- Storing a governed case's cited passages no longer opens a database session
  per passage while the case row is locked. The tenant's key is resolved once,
  before the write session (`core.cases.excerpts.tenant_key`), and each passage
  is encrypted with it off the event loop; the retention bound is applied
  first, so a capture larger than the bound does no key work for the passages
  it is about to drop.
- Two migrate jobs started together no longer race on an empty database:
  `migrations/env.py` takes the same transaction-scoped advisory lock
  `init_db()` uses before deciding whether to build the baseline, and rechecks
  the recorded revision after acquiring it. A database holding only views,
  materialised views or sequences counts as occupied (the check reads
  `pg_class`, not just the tables), so a bare `alembic upgrade` refuses it
  with `unmanaged_database_not_empty` instead of creating a baseline beside
  them.
- Encrypted-column migrations write their audit record to
  `AGENTICORG_MIGRATION_AUDIT_DIR` when it is set, so a test run no longer
  rewrites the committed records under `migrations/audit/`.
- A synchronous credential lookup no longer breaks the next request. An
  asyncpg connection belongs to the event loop that opened it, and
  `get_provider_credential_sync` ran the resolver on a throwaway loop while
  using the shared pooled engine: the lookup itself failed with "got Future …
  attached to a different loop" — reported as a credential that "could not be
  decrypted", which also refuses the platform fallback — and the connection it
  left in the pool made an unrelated later request fail, which `pool_pre_ping`
  does not catch (a cross-loop error is not a disconnect). Synchronous entry
  points now run their coroutine through `core.database.run_db_coroutine_sync`,
  on a private `NullPool` engine (built on first use and disposed with the
  loop): the credential resolver, the report generator's KPI bridge
  (`core/reports/generator.py`, reached when the sandbox pilot runs the report
  task body inside its own loop), the weekly-report pilot-proof writer and the
  CDC store's sync helpers. The LangGraph credential prefetch stays as defence
  in depth and one fewer connection per model build.
- The example onboarding policies read only evidence the Business Onboarding
  Underwriter produces, and only registry statuses the provider interface can
  return. `business_onboarding_uk` dropped `filings_overdue`: it read
  `verification.overdue_filings`, which no provider field supplies, so it fired
  as indeterminate on every UK case and added a tier and score on nothing. Both
  examples now match `in_insolvency` for an insolvent registry record instead
  of `liquidation` (UK) and `revoked` (US), neither of which is a
  `RegistryStatus` member, so an insolvent business can reach `blocked`.
  `verification.overdue_filings` is gone from the evidence mapping; a
  filings-overdue signal has to reach the provider interface, the mock provider
  and the conformance suite first (FINDINGS A-51). Dropping a `medium` rule
  can make a case less strict, which `docs/policies/authoring.md` treats as a
  major change: UK example `2.0.0`, US example `1.1.0`.
- The declared-activity vocabulary is published
  (`core.agents.business_underwriter.facts.DECLARED_ACTIVITY_CATEGORIES`, see
  `docs/policies/authoring.md`) and the mock provider's website fixtures are
  HTML documents with a title and a heading, as a real site is. The extractor
  classifies a page's activity from its title, meta description and headings,
  so before this the observed activity was empty for every fixture and
  `web_presence.activity_mismatch` never resolved. It now resolves for seven of
  the twelve fixtures.
- Agents are registered on Grantex (and re-scoped on `PATCH /agents/{id}`)
  with `tool:{connector}:{read|write|delete|admin}:{tool}` scopes from the
  connector's Grantex manifest instead of `...:execute:...`, which Grantex's
  permission check never satisfies, so grants delegated for them allowed
  nothing. `scripts/refresh_grantex_scopes.py` re-scopes agents registered
  before (report only by default; `--apply` updates Grantex, then only
  `config.grantex.grantex_scopes`; deleted agents are skipped and a failure
  for one agent is reported without stopping the run).
- `PATCH /agents/{id}` updates a registered agent's scopes on Grantex before
  storing them, and refuses the change with a `reason_code` when Grantex
  cannot take them (`grantex_update_failed`, `grantex_unconfigured`,
  `scope_computation_failed`, or `scope_limit_exceeded` above 100 scopes).
  Previously it stored scopes the registration did not have.
- The token pool delegates only the stored scopes the agent's Grantex
  registration also carries.
- The token pool refreshes agent tokens by delegating from the root grant
  (`grants.delegate`) instead of an OAuth grant type the Grantex auth service
  does not serve.
- `alembic upgrade head` works on an empty database. It stopped at the first
  revision (`v400_apex`, which alters tables from the pre-Alembic SQL files)
  with `function uuid_generate_v4() does not exist`, so only
  `scripts/alembic_migrate.py` could build a fresh database. `migrations/env.py`
  creates the ORM baseline and stamps `v480_baseline` first, and the wrapper
  now uses that one path instead of its own copy.
- Only `alembic upgrade` bootstraps an empty database. `alembic current` (and
  any other command with no revision argument) raised
  `KeyError: 'destination_rev'`, and `alembic stamp <revision>` on an empty
  database created all 93 ORM tables before writing the version row. The
  environment now recognises the upgrade command itself. An upgrade of a
  database that has tables but no Alembic revision, or of an empty database to
  a revision before the baseline or to a relative target, is refused with a
  reason code instead of failing part-way.
- The integration suite runs `alembic upgrade head` on an empty database, and
  on a database already at head, and fails on any schema difference from the
  ORM models not listed in
  `tests/integration/alembic_schema_drift_allowlist.py`. Migrations stay
  forward-only; `migrations/README.md` documents the bootstrap, the refusals
  and the downgrade limits.
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
