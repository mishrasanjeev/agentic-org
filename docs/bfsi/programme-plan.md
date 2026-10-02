# Programme plan

The plan closes every Partial and Gap item in the [coverage matrix](coverage-matrix.md). Work is cut into
packages that each ship as a sequence of small pull requests, every behaviour change behind a flag that
defaults to off, tests written first, and a demo per package that a reviewer can run from the Makefile.

## Phases

| Phase | Scope | Indicative timing | Packages |
|---|---|---|---|
| Phase 0 | Deployment reference and programme setup | week 1 | WP-00 |
| Phase 1 | Platform trust core: override, residency, gateway, guardrails, observability, authority hardening | weeks 1 to 4 | WP-01, WP-02, WP-03, WP-09, WP-19, WP-21 |
| Phase 2 | Governed engineering: retrieval v2, registry, prompts, evaluation, inventory, FinOps, runtime | weeks 5 to 10 | WP-04, WP-05, WP-06, WP-07, WP-08, WP-10, WP-17 |
| Phase 3 | Banking services: conversation, content, document processing, workbenches | weeks 11 to 16 | WP-11, WP-12, WP-14, WP-18 |
| Phase 4 | Intelligence services: speech, transaction intelligence, lineage, personalisation | weeks 17 to 22 | WP-13, WP-15, WP-16, WP-20 |

Phase 1 packages are the dependencies for everything else: the override, residency enforcement, the
model gateway, guardrails and tracing are the enforcement points the later packages plug into.

## Package summary

| Package | Title | Repositories | Phase | Items | Open items |
|---|---|---|---|---:|---:|
| WP-00 | In-country deployment reference architecture | agentic-org, grantex | 0 | 36 | 36 |
| WP-01 | Operator override for models, agents, workflows and tools | agentic-org, grantex | 1 | 3 | 3 |
| WP-02 | Model gateway: routing policy, access policy, limits, metrics and audit | agentic-org | 1 | 10 | 10 |
| WP-03 | Runtime guardrail pipeline | agentic-org | 1 | 5 | 4 |
| WP-04 | Knowledge retrieval v2 | agentic-org | 2 | 22 | 21 |
| WP-05 | Agent registry, lifecycle and certification | agentic-org, grantex | 2 | 11 | 11 |
| WP-06 | Prompt governance | agentic-org | 2 | 11 | 11 |
| WP-07 | Evaluation framework | agentic-org | 2 | 9 | 9 |
| WP-08 | AI governance inventory, risk tiers, policies and model cards | agentic-org, grantex | 2 | 6 | 5 |
| WP-09 | Observability and tamper-evident audit | agentic-org | 1 | 7 | 6 |
| WP-10 | FinOps | agentic-org | 2 | 5 | 5 |
| WP-11 | Conversational services for banking | agentic-org | 3 | 14 | 14 |
| WP-12 | Content services | agentic-org | 3 | 8 | 8 |
| WP-13 | Speech and conversation intelligence | agentic-org | 4 | 8 | 8 |
| WP-14 | Intelligent document processing | agentic-org | 3 | 12 | 12 |
| WP-15 | Transaction intelligence | agentic-org | 4 | 6 | 5 |
| WP-16 | Data acquisition, provenance and lineage | agentic-org | 4 | 4 | 4 |
| WP-17 | Agent runtime: builder, limits, memory, sandbox and debugging | agentic-org | 2 | 16 | 10 |
| WP-18 | Workbenches and business console | agentic-org | 3 | 6 | 4 |
| WP-19 | Residency, isolation and no-training controls | agentic-org, grantex | 1 | 4 | 4 |
| WP-20 | Personalisation service | agentic-org | 4 | 1 | 1 |
| WP-21 | Authority layer hardening | grantex | 1 | 1 | 1 |

## Packages

### WP-00: In-country deployment reference architecture

**Repositories:** agentic-org, grantex. **Phase:** 0. **Flag:** `n/a (deployment)`.

Cloud-agnostic reference deployment that satisfies the infrastructure items through the hosting platform: three-zone regions, managed Kubernetes or serverless containers, HSM-backed key management, WAF and DDoS, posture management, SIEM export on an open schema, lakehouse and streaming services, private endpoints, service mesh, immutable backups, confidential compute and published service levels. Each item maps to a deployment control, a verification step and the platform setting that consumes it.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| INF-01 | Multi-zone architecture | Gap |
| INF-02 | Native disaster recovery | Partial |
| INF-03 | AI accelerators | Partial |
| INF-04 | Managed Kubernetes | Partial |
| INF-05 | Managed serverless containers | Covered |
| SEC-01 | Hardware security module | Gap |
| SEC-02 | Key management and BYOK | Covered |
| SEC-03 | Secrets management | Partial |
| SEC-04 | Web application firewall | Partial |
| SEC-05 | Managed DDoS protection | Gap |
| SEC-06 | Security posture management | Partial |
| SEC-07 | Threat detection | Partial |
| SEC-08 | SIEM and security lake | Partial |
| SEC-09 | Identity and access management | Partial |
| DATA-01 | Scalable object storage | Gap |
| DATA-02 | Serverless SQL query engine | Gap |
| DATA-03 | Managed lakehouse | Gap |
| DATA-04 | Managed streaming | Gap |
| DATA-05 | Managed serverless ETL | Gap |
| DATA-06 | Data catalogue and lineage | Gap |
| DATA-07 | Managed search engine | Partial |
| DATA-08 | Managed relational databases | Partial |
| AIINF-13 | Parameter-efficient fine-tuning | Gap |
| NET-01 | Private service endpoints | Partial |
| NET-02 | Enterprise API gateway | Partial |
| NET-03 | Managed secure file transfer | Gap |
| NET-04 | Managed service mesh | Gap |
| OPS-03 | Infrastructure as code | Partial |
| OPS-05 | Fault injection and resilience testing | Partial |
| OPS-06 | Managed CI/CD | Partial |
| OPS-07 | Immutable backup (WORM) | Partial |
| OPS-08 | Confidential compute | Gap |
| GOV-01 | Published per-service SLAs | Partial |
| GOV-02 | Proven in-country track record | Gap |
| GOV-03 | 24x7 enterprise support | Partial |
| FE-08 | Governed API exposure | Covered |

**Acceptance**

- Every infrastructure item has a named control, an owner and a verification command in the deployment reference.
- The platform's compliance report lists the infrastructure controls it depends on and whether the deployment attests them.

**Pull-request sequence**

1. docs: deployment reference with per-item control mapping
2. compliance report: infrastructure attestation section

### WP-01: Operator override for models, agents, workflows and tools

**Repositories:** agentic-org, grantex. **Phase:** 1. **Flag:** `operator_override.enabled (default off)`.

One administrative control that halts or throttles a provider, a model, an agent, every agent, a workflow or the whole tool pipeline in real time. Enforced at the model router, the agent runner, the workflow engine and the tool gateway, with a live status surface and a tamper-evident audit entry per change. Grantex emergency stops are consumed by the tool gateway through the revocation feed so a stop takes effect before token expiry.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| BASE-05 | Emergency operator override | Partial |
| AIINF-10 | Agent tool gateway | Covered |
| AIGOV-04 | Operator override | Partial |

**Acceptance**

- A halt on a model, agent, workflow or tool refuses new work within one scheduler tick and records who, what and why.
- A throttle applies a token bucket per target and reports rejections in metrics.
- A grantex emergency stop on an agent is honoured by the tool gateway without waiting for token expiry.
- The override state is visible in the console and exported in the compliance report.

**Pull-request sequence**

1. core: override registry, policy checks at the four enforcement points, metrics
2. api and console: override endpoints and status panel
3. grantex: revocation-feed subscriber in the tool gateway
4. docs and runbook

### WP-02: Model gateway: routing policy, access policy, limits, metrics and audit

**Repositories:** agentic-org. **Phase:** 1. **Flag:** `model_gateway.enabled (default off)`.

A unified model gateway in front of every provider: policy-driven routing by use case, data sensitivity, cost, latency, throughput, language and residency; model access policies by application, identity and business unit; per-model concurrency and rate limits with traffic allocation; model-level metrics including time-to-first-token, tokens per second, errors and queue wait; an audit trail of every routing decision and failover; correlation ids propagated from the request into the model call; cost comparison and cost-aware routing within quality thresholds.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-01 | Multi-model foundation hub | Partial |
| AIINF-09 | Model gateway | Partial |
| GW-01 | Unified model abstraction | Partial |
| GW-02 | Policy-driven routing | Partial |
| GW-03 | Independent model onboarding | Partial |
| GW-04 | Traffic allocation and concurrency | Partial |
| GW-05 | Tiered response-time service levels | Partial |
| GW-06 | Model-level metrics | Partial |
| GW-07 | Model access policies | Partial |
| GW-08 | Gateway audit trail | Partial |

**Acceptance**

- Consuming applications call one endpoint and the gateway selects the model from policy; changing policy needs no application change.
- A request tagged restricted never leaves the in-country provider set; the refusal is audited.
- Per-model limits reject above the configured concurrency and the rejection is metered.
- Every invocation writes a routing record with correlation id, policy evaluated, model chosen, fallback taken and cost.

**Pull-request sequence**

1. gateway core and routing policy model
2. access policy and limits
3. metrics, tracing and audit records
4. cost comparison and cost-aware routing
5. console pages

### WP-03: Runtime guardrail pipeline

**Repositories:** agentic-org. **Phase:** 1. **Flag:** `guardrails.enforce (default off; flag-only mode stays the default behaviour)`.

Configurable input, retrieval, output and action guardrails evaluated on every model call and tool call: prompt-injection and jailbreak detection (direct and indirect), sensitive-data detection with mask, redact, tokenise, block or flag, factual-consistency checks against retrieved sources, output policy checks, and configurable actions per tenant, agent and risk tier. Decisions are recorded with the correlation id.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-04 | AI guardrails and safety | Partial |
| TRUST-01 | Prompt-injection guardrails | Partial |
| TRUST-02 | Sensitive-data controls | Covered |
| TRUST-03 | Factual consistency checks | Partial |
| TRUST-04 | Output guardrails | Partial |

**Acceptance**

- An injected instruction inside a retrieved document is detected and the action configured for the agent is applied.
- A card number in a model output is redacted before delivery and the event is audited.
- An answer whose claims are not supported by the retrieved context is flagged or suppressed according to policy.
- Guardrail outcomes appear in metrics and in the evidence package.

**Pull-request sequence**

1. guardrail engine and policy schema
2. input and output detectors
3. grounding checker
4. console policy editor
5. adversarial evaluation set

### WP-04: Knowledge retrieval v2

**Repositories:** agentic-org. **Phase:** 2. **Flag:** `knowledge.v2 (default off)`.

Layout-preserving extraction, configurable chunking strategies, query rewriting and decomposition, hybrid dense and sparse retrieval with rank fusion, metadata filtering, a re-ranking stage, citations with page and paragraph, excerpt navigation in the source, document-level access control, conflict flagging, faithfulness metrics, incremental re-indexing, agentic retrieval with visible traces and graph retrieval over extracted entities.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-02 | Managed RAG service | Partial |
| AIINF-03 | Native vector database | Partial |
| RAG-01 | Multi-format ingestion | Covered |
| RAG-02 | Layout-preserving extraction | Partial |
| RAG-03 | Configurable chunking | Partial |
| RAG-04 | Query transformation | Gap |
| RAG-05 | Incremental re-embedding | Partial |
| RAG-06 | Re-ranking stage | Gap |
| RAG-07 | Clickable citations | Partial |
| RAG-08 | Source excerpt navigation | Partial |
| RAG-09 | Access-aware retrieval | Partial |
| RAG-10 | Conflict flagging | Partial |
| RAG-11 | Retrieval quality metrics | Partial |
| RAG-12 | Automatic query decomposition | Gap |
| RAG-13 | Grounding enforcement | Partial |
| RAG-14 | Agentic retrieval with traces | Gap |
| RAG-15 | Graph retrieval | Gap |
| VEC-01 | Vector database | Partial |
| VEC-02 | Hybrid retrieval | Gap |
| VEC-03 | Metadata filtering | Gap |
| VEC-04 | Automated embedding pipeline | Partial |
| VEC-06 | Knowledge graph integration | Partial |

**Acceptance**

- A query returns chunks ranked by fused dense and sparse scores with filters on date, branch and segment.
- Each answer carries citations with document, page and paragraph and the console opens the highlighted excerpt.
- A user without access to a document never sees its chunks in retrieval or citations.
- Retrieval quality metrics (context relevance, faithfulness, hallucination indicators) are computed per pipeline.

**Pull-request sequence**

1. extraction and chunking strategies
2. hybrid search, filters and re-ranking
3. citations and excerpt navigation
4. document ACLs
5. query transformation and agentic retrieval traces
6. graph retrieval
7. retrieval metrics and re-indexing

### WP-05: Agent registry, lifecycle and certification

**Repositories:** agentic-org, grantex. **Phase:** 2. **Flag:** `agent_registry.lifecycle (default off)`.

Per-agent agent cards (purpose, models, tools, permissions, schemas, risk tier, owner), lifecycle states Draft, Review, Approved, Published, Deprecated and Retired with an approval workflow, environments (development, staging, production) with controlled promotion and traffic allocation between versions, a categorised searchable catalogue, banking agent templates, a dependency graph across agents, models, knowledge bases, tools and policies, and ratings plus reliability metrics. Grantex trust-registry attestations and passports are attached to the card so certification status is verifiable.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-11 | Agent lifecycle governance | Partial |
| MLOPS-01 | Governed model registry | Partial |
| MLOPS-02 | Controlled promotion | Partial |
| REG-01 | Searchable agent catalogue | Partial |
| REG-02 | Agent card | Partial |
| REG-03 | Agent lifecycle states | Partial |
| REG-04 | Agent templates | Partial |
| REG-05 | Dependency visualisation | Partial |
| REG-06 | Agent approval workflow | Partial |
| REG-07 | Environment version management | Partial |
| REG-08 | Ratings and reliability metrics | Partial |

**Acceptance**

- An agent cannot be published to production without passing the configured approval workflow and evaluation gate.
- The catalogue filters by domain, use case, channel, risk tier and approval status.
- The dependency graph renders the agent's models, tools, knowledge sources, prompts and policies.
- Traffic can be split between two published versions and rolled back in one action.

**Pull-request sequence**

1. agent card model and lifecycle states
2. approval workflow and environments
3. catalogue, templates and banking pack
4. dependency graph
5. ratings and reliability metrics

### WP-06: Prompt governance

**Repositories:** agentic-org. **Phase:** 2. **Flag:** `prompts.maker_checker (default off)`.

Typed prompt parameters with defaults and validation, maker-checker approval before production, side-by-side comparison across models, prompt evaluation against reference datasets, token and context-window management with relevance prioritisation, and strict structured-output enforcement for every agent.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| PROMPT-01 | Prompt playground | Partial |
| PROMPT-02 | Prompt version history | Partial |
| PROMPT-03 | Prompt templating | Partial |
| PROMPT-04 | Prompt repository | Partial |
| PROMPT-05 | Prompt optimisation | Gap |
| PROMPT-06 | Context-window optimisation | Gap |
| PROMPT-07 | Prompt evaluation against datasets | Partial |
| PROMPT-08 | Maker-checker for prompts | Gap |
| PROMPT-09 | Dynamic context assembly | Partial |
| PROMPT-10 | Context safeguards | Partial |
| PROMPT-11 | Structured output enforcement | Partial |

**Acceptance**

- A prompt change cannot reach production without a second approver; the approval is recorded with the version.
- The playground runs one prompt against several models and shows outputs, latency and cost side by side.
- A prompt variant is scored against a reference dataset before release.
- An agent with an output schema never returns a payload that fails validation; failures are retried then escalated.

**Pull-request sequence**

1. typed parameters and validation
2. maker-checker workflow
3. side-by-side playground and prompt evaluation
4. context-window manager and structured output

### WP-07: Evaluation framework

**Repositories:** agentic-org. **Phase:** 2. **Flag:** `evals.v2 (default off)`.

Dataset management with an API and console, model-graded scorers for faithfulness, relevance, instruction adherence and context recall, deterministic metrics (accuracy, precision, recall, F1, exact match, retrieval metrics), adversarial suites run in-product, feedback correlated with model and prompt versions, pre-promotion regression gates, comparative dashboards and scheduled synthetic checks.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-07 | Model evaluation framework | Partial |
| EVAL-01 | Evaluation datasets | Partial |
| EVAL-02 | Model-graded evaluation | Gap |
| EVAL-03 | Deterministic metrics | Partial |
| EVAL-04 | Adversarial test sets | Partial |
| EVAL-05 | Feedback correlation | Partial |
| EVAL-06 | Pre-promotion regression | Partial |
| EVAL-07 | Comparative dashboards | Partial |
| OBS-04 | Scheduled synthetic checks | Partial |

**Acceptance**

- A dataset is created in the console, versioned and run against a model, prompt, agent or workflow.
- An adversarial suite runs on a schedule and its results feed the guardrail dashboard.
- Promotion to production is blocked when the regression suite regresses beyond the configured threshold.
- The comparison dashboard ranks candidate models by accuracy, latency, throughput and token cost.

**Pull-request sequence**

1. dataset API and console
2. scorers and metrics
3. adversarial and scheduled runs
4. promotion gates and dashboards

### WP-08: AI governance inventory, risk tiers, policies and model cards

**Repositories:** agentic-org, grantex. **Phase:** 2. **Flag:** `governance.inventory (default off)`.

A live inventory of models, prompts, agents, knowledge assets, tools and their owners (an AI bill of materials), standardised model cards, regulatory risk tiers with stricter validation and oversight per tier, a unified policy console for model calls, data access, prompts, outputs, tool invocation and workflow execution, and the dependency graph shared with the registry.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIGOV-01 | Live asset inventory | Partial |
| AIGOV-02 | Configurable AI policies | Partial |
| AIGOV-03 | Regulatory risk tiers | Partial |
| AIGOV-05 | Dependency graph | Gap |
| AIGOV-06 | Model cards | Gap |
| TRUST-05 | Agent access policies | Covered |

**Acceptance**

- The inventory lists every model, prompt, agent, knowledge base and tool with owner, version and risk tier and exports as JSON.
- A high-risk tier forces human approval and evaluation gates that cannot be bypassed by the agent owner.
- Policies written in the console are evaluated at the enforcement points with violations blocked, flagged or routed.

**Pull-request sequence**

1. inventory model and export
2. model cards
3. risk tiers
4. policy console

### WP-09: Observability and tamper-evident audit

**Repositories:** agentic-org. **Phase:** 1. **Flag:** `observability.tracing (default off)`.

OpenTelemetry spans wired through models, retrieval, tools and workflows with waterfall traces in the console, correlation ids propagated end to end, streaming latency metrics, a live workload console with queue depths and service-level countdowns, scheduled synthetic checks, and a hash-chained audit log that records model requests and responses, prompt versions, policy decisions and approvals.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| OPS-01 | Distributed tracing for AI | Partial |
| OPS-02 | Centralised platform logging | Partial |
| OBS-01 | Waterfall execution traces | Partial |
| OBS-02 | Streaming latency metrics | Gap |
| OBS-03 | Live workload console | Partial |
| OBS-05 | Correlation identifiers | Partial |
| OBS-06 | Tamper-evident audit | Covered |

**Acceptance**

- A single correlation id links the request, each model call, retrieval, tool call, guardrail decision and audit row.
- The console shows a waterfall of one agent run with model, tool and memory spans and their durations.
- The audit chain verifies end to end and detects a modified row.

**Pull-request sequence**

1. tracing wiring and correlation ids
2. waterfall and live console
3. hash-chained audit with model request and response records
4. synthetic checks

### WP-10: FinOps

**Repositories:** agentic-org. **Phase:** 2. **Flag:** `finops.thresholds (default off)`.

Use-case tags on every model and tool call, thresholds at organisation, application and use-case level with alerts, throttling or suspension, cost comparison across models and providers before and after deployment, and forecasting from history and growth assumptions.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| OPS-04 | Cost management and FinOps | Partial |
| FIN-01 | Usage attribution | Partial |
| FIN-02 | Budget thresholds | Partial |
| FIN-03 | Cost comparison and routing | Partial |
| FIN-04 | Cost forecasting | Gap |

**Acceptance**

- Every cost ledger row carries business unit, department, application and use case.
- A use-case threshold breach throttles or suspends that use case and notifies the owner.
- The forecast page projects tokens and cost per use case for the next quarter.

**Pull-request sequence**

1. use-case attribution
2. thresholds and actions
3. cost comparison and forecasting

### WP-11: Conversational services for banking

**Repositories:** agentic-org. **Phase:** 3. **Flag:** `conversation.v2 (default off)`.

Banking intent recognition with parameter extraction, multi-turn context, clarification before action, slot-filling with explicit confirmation before transaction tools run, graceful fallbacks, escalation hand-off with a summary and intent tag, supervisor live view and takeover, conversation summaries, feedback and satisfaction capture, and reference multi-step scenarios (dispute, loan enquiry, application status).

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| CONV-01 | Banking intent recognition | Gap |
| CONV-02 | Multi-turn context | Partial |
| CONV-03 | Clarification before action | Partial |
| CONV-04 | Confirmed transaction invocation | Partial |
| CONV-05 | Escalation hand-off | Partial |
| CONV-06 | Graceful fallbacks | Partial |
| FE-01 | Configurable conversational interfaces | Partial |
| FE-02 | Intent, entity and context tracking | Partial |
| FE-03 | Transactional invocation from chat | Partial |
| FE-04 | Multi-step scenarios | Gap |
| FE-05 | Conversation summarisation | Gap |
| FE-06 | Escalation triggers | Partial |
| FE-07 | Supervisor live view | Gap |
| FE-10 | Interaction feedback capture | Partial |

**Acceptance**

- A transfer request collects and validates parameters, shows a confirmation and only then invokes the tool under a grant.
- An ambiguous request yields a clarification question instead of an action.
- An escalation creates a ticket carrying the summary, intent tag and transcript reference.
- A supervisor can watch a live session and take it over.

**Pull-request sequence**

1. intent and slot-filling runtime
2. context and clarification
3. escalation and supervisor console
4. scenario templates and feedback

### WP-12: Content services

**Repositories:** agentic-org. **Phase:** 3. **Flag:** `content_services.enabled (default off)`.

Reusable capability APIs for governed drafting, multi-document summarisation, obligation and deadline extraction, narrative-to-schema conversion (JSON and XML), policy-grounded response drafts from an approved source set, audience-adaptive tone, rule-driven clause assembly and document translation across Indian languages.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| CONTENT-01 | Governed drafting | Partial |
| CONTENT-02 | Structured summarisation | Partial |
| CONTENT-03 | Obligation extraction | Partial |
| CONTENT-04 | Narrative to payload | Partial |
| CONTENT-05 | Policy-grounded response drafts | Partial |
| CONTENT-06 | Audience-adaptive tone | Partial |
| CONTENT-07 | Clause assembly | Partial |
| CONTENT-08 | Multilingual translation | Gap |

**Acceptance**

- Each service is an API with a schema, an evaluation dataset and a guardrail profile.
- Drafts carry the approved sources used and go through the approval queue when policy requires.

**Pull-request sequence**

1. drafting, summarisation and extraction services
2. structuring and clause assembly
3. translation service

### WP-13: Speech and conversation intelligence

**Repositories:** agentic-org. **Phase:** 4. **Flag:** `speech.intelligence (default off)`.

Batch transcription, speaker separation, call summaries, sentiment and empathy analytics, live agent assist with knowledge surfacing, disclosure-script tracking, spoken sensitive-data redaction and speech synthesis controls.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| SPEECH-01 | Transcription | Partial |
| SPEECH-02 | Speech synthesis | Partial |
| SPEECH-03 | Speaker channel separation | Gap |
| SPEECH-04 | Call summaries | Gap |
| SPEECH-05 | Emotion and empathy analytics | Gap |
| SPEECH-06 | Live agent assist | Partial |
| SPEECH-07 | Disclosure script compliance | Gap |
| SPEECH-08 | Spoken sensitive-data redaction | Gap |

**Acceptance**

- A recording is transcribed with speaker labels and summarised with intent, key points and next actions.
- A call missing a mandatory disclosure is flagged in real time.
- Spoken card numbers and one-time codes are redacted from the recording and the transcript.

**Pull-request sequence**

1. batch transcription and diarisation
2. summaries and analytics
3. agent assist and disclosure tracking
4. spoken-data redaction

### WP-14: Intelligent document processing

**Repositories:** agentic-org. **Phase:** 3. **Flag:** `idp.enabled (default off)`.

Bundle splitting and classification, key-value and table extraction with bounding boxes and per-field confidence, review routing, cross-document reconciliation, stamp and seal verification, structured analysis reports, scanned statement line items and version comparison.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| IDP-01 | Bundle splitting and classification | Gap |
| IDP-02 | OCR across Indian languages | Partial |
| IDP-03 | Key-value extraction | Partial |
| IDP-04 | Table extraction | Partial |
| IDP-05 | Bounding-box review overlay | Gap |
| IDP-06 | Confidence routing | Partial |
| IDP-07 | Cross-document reconciliation | Partial |
| IDP-08 | Stamp and seal verification | Gap |
| IDP-09 | Regional script processing | Partial |
| IDP-10 | Document analysis reports | Partial |
| IDP-11 | Bank statement line items | Partial |
| IDP-12 | Version comparison | Gap |

**Acceptance**

- A multi-document bundle is split and each part classified with a confidence score.
- Extracted fields show bounding boxes in the review UI and low-confidence documents land in the review queue.
- Fields that disagree across documents are listed with their sources.

**Pull-request sequence**

1. classification and extraction
2. review UI with overlays and confidence routing
3. reconciliation, stamps and reports
4. statements and version comparison

### WP-15: Transaction intelligence

**Repositories:** agentic-org. **Phase:** 4. **Flag:** `transaction_intelligence.enabled (default off)`.

Entity-centric aggregation of alerts and records, structuring and pass-through detection, interactive fund-flow graphs across hops, suspicious-transaction narrative drafting and consolidated evidence context, all under human disposition with the governed-case evidence chain.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| TXN-01 | Entity-centric aggregation | Partial |
| TXN-02 | Structuring detection | Gap |
| TXN-03 | Pass-through detection | Gap |
| TXN-04 | Fund-flow graphs | Gap |
| TXN-05 | Suspicious transaction narrative | Gap |
| TXN-06 | Consolidated evidence context | Covered |

**Acceptance**

- Deposits below a threshold across branches within a window raise a structuring finding with the supporting rows.
- The fund-flow graph expands counterparties across hops and exports with the case evidence.
- A narrative draft is produced from findings and sent to the investigator queue; nothing is filed automatically.

**Pull-request sequence**

1. aggregation and detectors
2. fund-flow graph
3. narrative drafting and evidence export

### WP-16: Data acquisition, provenance and lineage

**Repositories:** agentic-org. **Phase:** 4. **Flag:** `lineage.enabled (default off)`.

Provenance metadata (source, timestamp, version or hash, processing history) on every ingested record, scheduled incremental synchronisation and lineage visualisation from ingestion through embeddings to model use.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| ACQ-01 | Lawful automated acquisition | Partial |
| ACQ-02 | Provenance metadata | Partial |
| ACQ-03 | Scheduled incremental sync | Partial |
| VEC-05 | Lineage visualisation | Gap |

**Acceptance**

- Any chunk, record or embedding can be traced back to its source, version and processing steps.
- A sync job processes only records changed since the last run and records what it processed.

**Pull-request sequence**

1. provenance model
2. incremental sync
3. lineage graph

### WP-17: Agent runtime: builder, limits, memory, sandbox and debugging

**Repositories:** agentic-org. **Phase:** 2. **Flag:** `runtime.v2 (default off)`.

A visual workflow builder with branching and fallback, per-agent limits on steps and duration with loop detection, long-term memory with retention controls, schema-validated tool registration, sandboxed tool execution and a debugging console with step-through and variable inspection.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| AIINF-05 | Multi-agent orchestration | Covered |
| AIINF-06 | Agent workflow builder | Partial |
| AIINF-08 | Dual-mode memory service | Partial |
| AIINF-12 | Sandboxed workload isolation | Partial |
| ORCH-01 | Visual workflow builder | Partial |
| ORCH-02 | Planning and re-planning | Partial |
| ORCH-03 | Multi-agent collaboration | Covered |
| ORCH-04 | Tool registration | Partial |
| ORCH-05 | Sandboxed authorised tool execution | Partial |
| ORCH-06 | Persistent agent memory | Partial |
| ORCH-07 | Human approval checkpoints | Covered |
| ORCH-08 | Execution limits and loop detection | Partial |
| ORCH-09 | Per-agent tool restrictions | Covered |
| ORCH-10 | Debugging console | Partial |
| ORCH-11 | Agents as callable capabilities | Covered |
| TRUST-06 | Approval for high-risk actions | Covered |

**Acceptance**

- A workflow is drawn, saved and executed from the builder with human checkpoints.
- An agent exceeding its step limit or repeating a tool call pattern is stopped and the reason audited.
- Long-term memory entries have a retention policy and can be erased per subject.
- A registered tool rejects inputs that fail its schema before any call leaves the gateway.

**Pull-request sequence**

1. visual builder
2. limits and loop detection
3. long-term memory
4. tool registration and sandbox
5. debugging console

### WP-18: Workbenches and business console

**Repositories:** agentic-org. **Phase:** 3. **Flag:** `workbench.v2 (default off)`.

Role-based workbenches for review officers, relationship managers, investigators and supervisors, a unified review queue with edit before approval, a business configuration console for rules, thresholds and routing, faceted search across cases, documents, customers and accounts, and accessibility conformance.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| FE-09 | Accessibility | Partial |
| FE-11 | Role-based workbenches | Partial |
| FE-12 | Paused-task review queue | Covered |
| FE-13 | Business configuration console | Partial |
| FE-14 | Workbench search | Gap |
| FE-15 | Workbench RBAC | Covered |

**Acceptance**

- Each workbench shows only the tabs and actions its role allows and hides sensitive views by role.
- Facets and boolean filters work across cases, documents, customers and accounts.
- The accessibility suite passes on every workbench page.

**Pull-request sequence**

1. workbench shell and roles
2. review queue with edit
3. business console
4. search and accessibility

### WP-19: Residency, isolation and no-training controls

**Repositories:** agentic-org, grantex. **Phase:** 1. **Flag:** `residency.enforce (default off)`.

Enforcement of the data-region setting (providers, storage, memory and exports outside the region are refused), an in-country default region, a single-tenant deployment profile, per-provider no-training attestation records, an activated disaster-recovery profile and verification of database high availability.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| BASE-01 | In-country data residency | Partial |
| BASE-02 | Isolated production instance | Partial |
| BASE-03 | In-country disaster recovery | Partial |
| BASE-04 | No training on institution data | Gap |

**Acceptance**

- With enforcement on, a provider or storage location outside the configured region cannot be selected or used.
- The compliance report shows region, tenancy profile, DR status and each provider's no-training attestation.
- Grantex grant tokens carrying a data region are refused by the relying party outside that region.

**Pull-request sequence**

1. region enforcement and defaults
2. single-tenant profile and DR activation
3. no-training attestations
4. grantex data_region enforcement

### WP-20: Personalisation service

**Repositories:** agentic-org. **Phase:** 4. **Flag:** `personalisation.enabled (default off)`.

Content personalised from authorised attributes, profiles and configurable rules as a reusable service with consent checks.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| LEAD-01 | Personalised content | Partial |

**Acceptance**

- Personalised content is generated only for subjects with a valid consent record and the attributes used are recorded.

**Pull-request sequence**

1. personalisation service

### WP-21: Authority layer hardening

**Repositories:** grantex. **Phase:** 1. **Flag:** `per feature, default off`.

Online revocation checks in the gateway so emergency stops take effect immediately, a credential-exchange mode that never returns raw credentials to the agent, agent lifecycle states and approval attestations in the trust registry, environment-scoped agent versions, workload risk tiers on manifests, and tool-qualified scope enforcement in the SDKs.

**Capabilities closed**

| ID | Capability | Today |
|---|---|---|
| TRUST-07 | Central credential vault | Covered |

**Acceptance**

- An emergency-stopped agent is refused by the gateway on the next request.
- An agent can hold credentials by reference only; the gateway injects them upstream.
- A manifest can declare a risk tier that forces decision grants for high-risk tools.

**Pull-request sequence**

1. gateway online revocation
2. credential-by-reference mode
3. lifecycle states and attestation types
4. risk tiers on manifests
5. SDK tool-qualified scopes

## Working rules

- One pull request per change; tests first; existing tests are never weakened.
- Flags default to off. A package is complete when its flag can be turned on in the development stack and the
  acceptance list passes from the Makefile demo.
- Defects found on the way are recorded in FINDINGS.md with a reproduction, not fixed silently in passing.
- Synthetic data only. No institution's data, names or documents enter the repositories.
- Every package ends with the capability readiness report updated so the matrix stays truthful.
