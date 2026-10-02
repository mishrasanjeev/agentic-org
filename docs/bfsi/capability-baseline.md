# Capability baseline

Each capability has a stable identifier, a short title and the expectation in one or two sentences. The
identifiers are referenced by the coverage matrix, the programme plan and the capability readiness report.

## 1. Baseline conditions

Conditions a regulated institution treats as pass or fail before any capability scoring.

### Baseline residency, isolation and control conditions

| ID | Capability | Expectation |
|---|---|---|
| BASE-01 | In-country data residency | All institution data, prompts, retrieved context, embeddings, vector indexes, agent memory, model inference and AI outputs are stored and processed inside the institution's home jurisdiction. No cross-border processing, routing, caching or storage. |
| BASE-02 | Isolated production instance | Production workloads run in a logically isolated environment; institution data and processing are never mingled with another customer's data. |
| BASE-03 | In-country disaster recovery | Production services have an active disaster-recovery arrangement at a secondary site in the same jurisdiction; data does not leave the jurisdiction for DR or continuity operations. |
| BASE-04 | No training on institution data | Institution data, prompts, outputs, embeddings and derived artefacts are never used to train, fine-tune, evaluate or improve any shared, multi-tenant, public or vendor foundation model without explicit written authorisation for an institution-exclusive use. |
| BASE-05 | Emergency operator override | Administrators have a real-time operator override to halt, disable or throttle any active model, agent or automated pipeline. |

## 2. Technical capabilities

Platform and hosting capabilities. Many belong to the hosting provider and are satisfied by the deployment reference.

### Compute and infrastructure resilience

| ID | Capability | Expectation |
|---|---|---|
| INF-01 | Multi-zone architecture | Hosting infrastructure offers at least three physically independent availability zones in-country with dedicated power, cooling and network. |
| INF-02 | Native disaster recovery | Managed disaster-recovery and continuity capabilities support cross-zone and cross-region failover to institution-defined RTO/RPO targets. |
| INF-03 | AI accelerators | GPU and inference-optimised accelerator compute is available natively in-country for model hosting. |
| INF-04 | Managed Kubernetes | A fully managed container-orchestration service with automated cluster upgrades, node auto-scaling and health monitoring. |
| INF-05 | Managed serverless containers | Managed serverless container execution with automatic provisioning, elastic scaling including scale-to-zero and no customer-managed nodes. |
### Security, identity and key management

| ID | Capability | Expectation |
|---|---|---|
| SEC-01 | Hardware security module | Single-tenant dedicated HSM service compliant with FIPS 140-2 Level 2 or higher. |
| SEC-02 | Key management and BYOK | Customer-managed encryption keys, secure key import/export and integration with customer-controlled external key managers or HSMs for envelope encryption and key lifecycle. |
| SEC-03 | Secrets management | A managed service to store, rotate and retrieve database credentials, API keys and certificates across their lifecycle. |
| SEC-04 | Web application firewall | A managed WAF with custom rate-limiting rules, OWASP Top 10 protection and rate-based throttling. |
| SEC-05 | Managed DDoS protection | Managed layer 3/4 DDoS protection with 24x7 automated response. |
| SEC-06 | Security posture management | Centralised posture management scanning container images and compute instances for vulnerabilities and compliance deviations. |
| SEC-07 | Threat detection | Continuous threat detection over control-plane logs and network events identifying unauthorised behaviour and irregularities in real time. |
| SEC-08 | SIEM and security lake | Managed security-log collection and analytics on open schema standards, integrable with the institution's SIEM. |
| SEC-09 | Identity and access management | Fine-grained IAM with role-based access, temporary credential assumption and mandatory multi-factor authentication. |
### Data management, lakehouse and storage

| ID | Capability | Expectation |
|---|---|---|
| DATA-01 | Scalable object storage | Object storage with eleven-nines durability and automated replication across three in-country zones. |
| DATA-02 | Serverless SQL query engine | Serverless SQL over object storage without provisioning servers. |
| DATA-03 | Managed lakehouse | Managed table formats on object storage with automated compaction and ACID transactions. |
| DATA-04 | Managed streaming | Fully managed distributed streaming with auto-scaling and serverless options. |
| DATA-05 | Managed serverless ETL | Managed serverless ETL with visual authoring, transformations and scheduling. |
| DATA-06 | Data catalogue and lineage | Integrated catalogue with schema discovery, data classification and end-to-end lineage. |
| DATA-07 | Managed search engine | Managed search and analytics engine for log analytics, structured search, full-text indexing and dashboards. |
| DATA-08 | Managed relational databases | Managed relational engines (for example PostgreSQL) with multi-zone high availability and automated backups. |
### AI infrastructure and foundation-model services

| ID | Capability | Expectation |
|---|---|---|
| AIINF-01 | Multi-model foundation hub | Managed access to foundation models (LLMs, SLMs, multimodal) plus secure integration with approved external providers hosted in-country. |
| AIINF-02 | Managed RAG service | Native managed retrieval service (standard, agentic and graph) with automated chunking, embedding and vector retrieval. |
| AIINF-03 | Native vector database | Managed serverless vector store or vector-enabled database with similarity search and metadata filtering. |
| AIINF-04 | AI guardrails and safety | Native guardrails detecting and blocking prompt injection, sensitive-data leakage and toxic content in real time. |
| AIINF-05 | Multi-agent orchestration | Native framework for multi-agent collaboration, hierarchical supervision and task delegation. |
| AIINF-06 | Agent workflow builder | Front-end tool to author, execute and monitor multi-step agent workflows with human-in-the-loop review nodes. |
| AIINF-07 | Model evaluation framework | Benchmark foundation models against custom test datasets for accuracy, hallucination and relevance. |
| AIINF-08 | Dual-mode memory service | Managed short-term session memory and persistent long-term memory for agents. |
| AIINF-09 | Model gateway | Unified gateway for multiple foundation models with routing, authentication, rate limiting, usage controls and centralised API management. |
| AIINF-10 | Agent tool gateway | Managed gateway through which agents securely discover, authenticate to and execute enterprise APIs and tools. |
| AIINF-11 | Agent lifecycle governance | Deployment lifecycle management for agents with versioning, traffic routing and rollback. |
| AIINF-12 | Sandboxed workload isolation | Agent sessions and untrusted code execute in isolated sandboxes or micro-VMs. |
| AIINF-13 | Parameter-efficient fine-tuning | Managed PEFT pipelines (LoRA/QLoRA class) for foundation models in-country. |
### Networking and integration

| ID | Capability | Expectation |
|---|---|---|
| NET-01 | Private service endpoints | Private connectivity so services and databases talk to institution-hosted systems without the public internet. |
| NET-02 | Enterprise API gateway | Managed API gateway with throttling, rate limiting, API-key authentication, WebSocket streaming, logging, threat detection and request transformation. |
| NET-03 | Managed secure file transfer | Managed SFTP/FTPS into object storage for batch ingestion, integrable with the institution's file-transfer systems. |
| NET-04 | Managed service mesh | Service-to-service mutual TLS, traffic routing and telemetry across containers. |
### DevOps, observability, resilience, data protection and FinOps

| ID | Capability | Expectation |
|---|---|---|
| OPS-01 | Distributed tracing for AI | End-to-end latency traces across models, workflows and tools. |
| OPS-02 | Centralised platform logging | Centralised logging with real-time ingestion, search and metric dashboards across all components. |
| OPS-03 | Infrastructure as code | Declarative template deployments with automated drift detection. |
| OPS-04 | Cost management and FinOps | Granular resource tagging, department-level budget alerts and cost irregularity detection. |
| OPS-05 | Fault injection and resilience testing | Managed chaos-engineering service with reports. |
| OPS-06 | Managed CI/CD | Native CI/CD with source control, build and deployment pipelines available in-country. |
| OPS-07 | Immutable backup (WORM) | Write-once-read-many object backup that no user, including administrators, can alter or delete inside the retention period. |
| OPS-08 | Confidential compute | Encryption of data in use with hardware-isolated execution across processor architectures. |
### Cloud governance, support and service levels

| ID | Capability | Expectation |
|---|---|---|
| GOV-01 | Published per-service SLAs | Transparent binding SLAs (99.5% or better) with contractual service credits. |
| GOV-02 | Proven in-country track record | Five or more years of continuous compliant public-cloud operation in-country with published uptime. |
| GOV-03 | 24x7 enterprise support | 24x7 enterprise support with 15-minute critical response and in-country technical account management. |

## 3. Functional capabilities

Capabilities delivered by the platform's own services or through governed integrations.

### Content generation, summarisation and structuring

| ID | Capability | Expectation |
|---|---|---|
| CONTENT-01 | Governed drafting | Governed drafting of customer communications, formal notices, service letters and internal circulars. |
| CONTENT-02 | Structured summarisation | Synthesis of multi-page reports, circulars and dossier bundles into concise structured summaries. |
| CONTENT-03 | Obligation extraction | Extraction of actionable commitments, compliance obligations, turnaround times and deadlines from legal and regulatory text. |
| CONTENT-04 | Narrative to payload | Conversion of unstructured narrative and incident descriptions into predefined JSON/XML payloads for enterprise systems. |
| CONTENT-05 | Policy-grounded response drafts | Business response drafts generated from designated approved policy sources under configurable response constraints. |
| CONTENT-06 | Audience-adaptive tone | Output tone and complexity adapted (formal, professional, simplified, regional) to the audience profile. |
| CONTENT-07 | Clause assembly | Dynamic assembly of approved clauses and content components from configurable business parameters and rules. |
| CONTENT-08 | Multilingual translation | Translation of documents, policies and notices across English, Hindi and regional Indian languages. |
### Speech, audio/video intelligence and conversational insight

| ID | Capability | Expectation |
|---|---|---|
| SPEECH-01 | Transcription | Real-time and batch speech-to-text across English, Hindi and regional languages. |
| SPEECH-02 | Speech synthesis | Natural text-to-speech with configurable Indian accents, intonation and speaking rate. |
| SPEECH-03 | Speaker channel separation | Separation and labelling of customer versus staff dialogue in recordings. |
| SPEECH-04 | Call summaries | Structured call summaries capturing intent, key points and next actions. |
| SPEECH-05 | Emotion and empathy analytics | Customer emotion, sentiment trends, escalation indicators and agent-empathy metrics from speech and transcripts. |
| SPEECH-06 | Live agent assist | Real-time transcription surfacing relevant knowledge articles and policies and responding to users. |
| SPEECH-07 | Disclosure script compliance | Tracking of transcripts against mandatory regulatory disclosure scripts with real-time flagging of missing disclosures. |
| SPEECH-08 | Spoken sensitive-data redaction | Automatic detection and redaction of spoken card numbers, CVVs and OTPs from recordings. |
### Enterprise data acquisition and information collection

| ID | Capability | Expectation |
|---|---|---|
| ACQ-01 | Lawful automated acquisition | Automated lawful acquisition from authorised government portals, registries and public sources. |
| ACQ-02 | Provenance metadata | Provenance for ingested data: source, ingestion timestamp, version or hash and processing history, supporting traceability and audit. |
| ACQ-03 | Scheduled incremental sync | Scheduled synchronisation jobs capturing and incrementally processing new or modified records. |
### Conversational AI and interaction services

| ID | Capability | Expectation |
|---|---|---|
| CONV-01 | Banking intent recognition | Recognition of banking intents (balance, card block, transfer, statement) and extraction of transaction parameters. |
| CONV-02 | Multi-turn context | Conversational context maintained across turns with correct resolution of earlier references. |
| CONV-03 | Clarification before action | Clarification prompts generated when a query contains multiple ambiguous requests, before any action. |
| CONV-04 | Confirmed transaction invocation | Parameter collection, validation, confirmation and invocation of authorised transaction APIs/tools with explicit user confirmation before execution. |
| CONV-05 | Escalation hand-off | Transfer to a live agent or ticket with a concise summary and intent tag on escalation. |
| CONV-06 | Graceful fallbacks | Graceful fallback when confidence is below threshold or a backend times out. |
### Enterprise knowledge retrieval

| ID | Capability | Expectation |
|---|---|---|
| RAG-01 | Multi-format ingestion | Ingestion and parsing of PDF, Word, Excel, PowerPoint, text, CSV and HTML. |
| RAG-02 | Layout-preserving extraction | Reading order, headers, footers, multi-column layouts and tables preserved during extraction. |
| RAG-03 | Configurable chunking | Chunk size, boundaries and overlap configurable, with per-content-type or per-use-case strategies. |
| RAG-04 | Query transformation | Query rewriting, sub-query decomposition and contextual expansion. |
| RAG-05 | Incremental re-embedding | Scheduled or real-time incremental re-indexing on source updates. |
| RAG-06 | Re-ranking stage | Cross-encoder or equivalent re-ranking before generation. |
| RAG-07 | Clickable citations | Citations referencing source document name, page and paragraph. |
| RAG-08 | Source excerpt navigation | Direct navigation to the highlighted excerpt in the original document from a citation. |
| RAG-09 | Access-aware retrieval | Document- or source-level access control so results are restricted to what the requester may see. |
| RAG-10 | Conflict flagging | Identification of conflicting facts across retrieved context. |
| RAG-11 | Retrieval quality metrics | Context relevance, answer faithfulness and hallucination indicators across pipelines. |
| RAG-12 | Automatic query decomposition | Automatic decomposition, expansion and rewriting of complex queries into sub-queries. |
| RAG-13 | Grounding enforcement | Responses evaluated against retrieved sources; unsupported claims flagged or prevented. |
| RAG-14 | Agentic retrieval with traces | Agentic retrieval across knowledge bases with on-screen execution traces of intermediate steps. |
| RAG-15 | Graph retrieval | Graph-based retrieval with entity and relationship extraction and interactive multi-hop traversal. |
### Intelligent document processing and document intelligence

| ID | Capability | Expectation |
|---|---|---|
| IDP-01 | Bundle splitting and classification | Automatic splitting and classification of multi-page bundles into document types. |
| IDP-02 | OCR across Indian languages | Extraction of printed, typed and handwritten text from scans and images across Indian languages. |
| IDP-03 | Key-value extraction | Structured key-value extraction (names, dates, identifiers, addresses) from forms and certificates. |
| IDP-04 | Table extraction | Multi-row, multi-column financial and property schedule tables extracted into structured JSON. |
| IDP-05 | Bounding-box review overlay | Review UI showing bounding-box overlays for extracted fields. |
| IDP-06 | Confidence routing | Numeric confidence per field with automatic routing of low-confidence documents to human review. |
| IDP-07 | Cross-document reconciliation | Comparison and reconciliation of extracted fields across submitted documents highlighting discrepancies. |
| IDP-08 | Stamp and seal verification | Automated visual verification of stamps, seals and date-stamps. |
| IDP-09 | Regional script processing | Processing of major Indian regional scripts alongside English and Hindi. |
| IDP-10 | Document analysis reports | Automatic structured reports summarising extracted facts, defects and verification outcomes. |
| IDP-11 | Bank statement line items | Extraction of narration, debit, credit, balance and date line items from multi-page scanned statements. |
| IDP-12 | Version comparison | Comparison of revised document versions against baselines highlighting changes. |
### Transaction intelligence and behavioural analytics

| ID | Capability | Expectation |
|---|---|---|
| TXN-01 | Entity-centric aggregation | Aggregation of information, events, alerts and records for a common entity into a consolidated analysis context. |
| TXN-02 | Structuring detection | Detection of structuring patterns such as multiple sub-threshold cash deposits across branches. |
| TXN-03 | Pass-through detection | Detection of pass-through accounts with immediate onward transfer of incoming funds. |
| TXN-04 | Fund-flow graphs | Interactive transaction-flow graphs mapping counterparty networks across multiple hops. |
| TXN-05 | Suspicious transaction narrative | Automatic drafting of a structured suspicious-transaction report narrative detailing irregularities and counterparties. |
| TXN-06 | Consolidated evidence context | Aggregation of source data, analytical outputs, documents and evidence into a consolidated traceable context for review or export. |
### Lead intelligence and customer engagement

| ID | Capability | Expectation |
|---|---|---|
| LEAD-01 | Personalised content | Content personalised from authorised contextual attributes, profiles and configurable business rules. |
### AI platform architecture and model gateway

| ID | Capability | Expectation |
|---|---|---|
| GW-01 | Unified model abstraction | A unified interface routing across SLMs, LLMs, vision-language and multimodal models without changing consuming applications. |
| GW-02 | Policy-driven routing | Routing by use case, data sensitivity, cost, latency, throughput and language, with automated failover. |
| GW-03 | Independent model onboarding | Onboarding, deployment and governance of foundation models independently of consuming applications. |
| GW-04 | Traffic allocation and concurrency | Model abstraction, traffic allocation and concurrency controls across endpoints. |
| GW-05 | Tiered response-time service levels | Tier 1 conversational queries under 4 seconds for 90% of requests, progress indication for longer workflows. |
| GW-06 | Model-level metrics | Request volume, latency, token consumption, errors, throughput and utilisation per model. |
| GW-07 | Model access policies | Access policies restricting models and endpoints by application, identity, business unit or workload, with routing traceability. |
| GW-08 | Gateway audit trail | Audit trail of model selection, routing decisions, policy evaluations, failover and responses. |
### Knowledge and vector infrastructure

| ID | Capability | Expectation |
|---|---|---|
| VEC-01 | Vector database | High-performance vector store for semantic search and retrieval workflows. |
| VEC-02 | Hybrid retrieval | Dense plus sparse retrieval with rank-fusion scoring. |
| VEC-03 | Metadata filtering | Low-latency boolean and range metadata filters concurrent with similarity search. |
| VEC-04 | Automated embedding pipeline | Real-time and scheduled incremental re-indexing on source updates. |
| VEC-05 | Lineage visualisation | Lineage captured and visualised from ingestion through transformation, embedding and model consumption. |
| VEC-06 | Knowledge graph integration | Knowledge-graph modelling of entity relationships across customers, accounts and corporate hierarchies. |
### AI engineering, MLOps and LLMOps

| ID | Capability | Expectation |
|---|---|---|
| MLOPS-01 | Governed model registry | Model registry tracking versions with one-click rollback. |
| MLOPS-02 | Controlled promotion | Controlled promotion across environments, onboarding, replacement, retirement and traffic allocation between versions. |
### Prompt engineering and context management

| ID | Capability | Expectation |
|---|---|---|
| PROMPT-01 | Prompt playground | Interactive playground to author, test and compare prompts across models side by side. |
| PROMPT-02 | Prompt version history | Full version history for prompt templates and agent instructions with approval tracking and rollback. |
| PROMPT-03 | Prompt templating | Reusable templating and parameter management with validation and defaults. |
| PROMPT-04 | Prompt repository | Central repository for prompts and agent instructions with role-based access and audit trails. |
| PROMPT-05 | Prompt optimisation | Testing and optimisation of templates across models for clarity and token efficiency. |
| PROMPT-06 | Context-window optimisation | Context-window optimisation and token management. |
| PROMPT-07 | Prompt evaluation against datasets | Evaluation of prompt variations across models against reference datasets before release. |
| PROMPT-08 | Maker-checker for prompts | Prompt changes reviewed and approved before production deployment. |
| PROMPT-09 | Dynamic context assembly | Dynamic identification, retrieval and assembly of relevant context from enterprise sources. |
| PROMPT-10 | Context safeguards | Context relevance, prioritisation, session-memory management and data-masking safeguards in prompts. |
| PROMPT-11 | Structured output enforcement | Strict structured output conforming to predefined JSON schemas. |
### Enterprise agent registry and marketplace

| ID | Capability | Expectation |
|---|---|---|
| REG-01 | Searchable agent catalogue | Centralised searchable catalogue of agents by domain, use case, channel and approval status. |
| REG-02 | Agent card | Standardised metadata record per agent: purpose, models, tools, permissions and schemas. |
| REG-03 | Agent lifecycle states | Versioning and lifecycle across Draft, Review, Approved, Published, Deprecated and Retired. |
| REG-04 | Agent templates | Reusable templates for common banking patterns that can be cloned, customised and deployed. |
| REG-05 | Dependency visualisation | Visual map between agents, models, knowledge bases and tools. |
| REG-06 | Agent approval workflow | Verification and approval workflows governing states before production publication. |
| REG-07 | Environment version management | Agent versions managed across dev, staging and production. |
| REG-08 | Ratings and reliability metrics | User ratings, developer feedback and execution-reliability metrics per published agent. |
### Agentic development and workflow orchestration

| ID | Capability | Expectation |
|---|---|---|
| ORCH-01 | Visual workflow builder | Low-code builder for multi-step workflows with branching and fallback logic. |
| ORCH-02 | Planning and re-planning | Decomposition of goals into sub-tasks with dynamic plan adjustment from intermediate results. |
| ORCH-03 | Multi-agent collaboration | Specialised agents communicate, delegate sub-tasks and aggregate findings. |
| ORCH-04 | Tool registration | Authorised users register APIs, databases and services as standardised tools with schema validation. |
| ORCH-05 | Sandboxed authorised tool execution | Secure sandboxing and authorisation checks on every external tool or API invocation. |
| ORCH-06 | Persistent agent memory | Persistent memory across multi-turn interactions retaining customer context, reasoning and execution state. |
| ORCH-07 | Human approval checkpoints | Approval checkpoints that pause execution until authorised review, with asynchronous resumption and audit. |
| ORCH-08 | Execution limits and loop detection | Configurable limits on steps and duration with automated loop detection. |
| ORCH-09 | Per-agent tool restrictions | Administrators restrict tool access per agent, distinguishing read-only from write/transactional execution. |
| ORCH-10 | Debugging console | Console to inspect execution traces, step through workflows, inspect variables and analyse past logs. |
| ORCH-11 | Agents as callable capabilities | Deployed agents exposed for invocation by other agents in hierarchical or supervisor orchestration. |
### AI evaluation framework and benchmarking

| ID | Capability | Expectation |
|---|---|---|
| EVAL-01 | Evaluation datasets | Creation, management and execution of curated datasets with reference inputs, expected outputs and criteria. |
| EVAL-02 | Model-graded evaluation | Automated scoring of faithfulness, relevance, instruction adherence and context recall. |
| EVAL-03 | Deterministic metrics | Accuracy, precision, recall, F1, exact match and retrieval metrics against references. |
| EVAL-04 | Adversarial test sets | Automated adversarial sets for prompt injection, toxicity, disclosure and leakage resistance. |
| EVAL-05 | Feedback correlation | Explicit user feedback correlated with model versions and prompt templates. |
| EVAL-06 | Pre-promotion regression | Automated regression suites before promoting models, prompts, agents or workflows. |
| EVAL-07 | Comparative dashboards | Dashboards ranking candidate models by accuracy, latency, throughput and token cost. |
### Cross-cutting AI governance and asset inventory

| ID | Capability | Expectation |
|---|---|---|
| AIGOV-01 | Live asset inventory | Automated inventory of models, prompts, agents, knowledge assets, ownership and tools. |
| AIGOV-02 | Configurable AI policies | Policies over model calls, data access, prompts, outputs, tool invocation and workflow execution; violations blocked, flagged or routed for review. |
| AIGOV-03 | Regulatory risk tiers | AI workloads categorised into risk tiers with stricter validation and oversight for high-risk models. |
| AIGOV-04 | Operator override | Emergency operator override to halt, disable or throttle any model, agent, workflow or tool pipeline. |
| AIGOV-05 | Dependency graph | Interactive graph of applications, agents, prompts, models, retrieval sources, tools, policies and deployments. |
| AIGOV-06 | Model cards | Standardised model cards: provenance, architecture, risk classification, intended use, baselines, limitations and approvals. |
### Security, privacy and AI trust controls

| ID | Capability | Expectation |
|---|---|---|
| TRUST-01 | Prompt-injection guardrails | Configurable detection and mitigation of direct and indirect injection, jailbreaks and system overrides. |
| TRUST-02 | Sensitive-data controls | Detection of sensitive information in inputs and outputs with masking, redaction, tokenisation, blocking or flagging. |
| TRUST-03 | Factual consistency checks | Answers verified against retrieved sources; ungrounded statements flagged or suppressed. |
| TRUST-04 | Output guardrails | Configurable output guardrails acting on policy-violating or unsafe content before delivery. |
| TRUST-05 | Agent access policies | Policies governing agent access to tools, APIs, data and actions by user, application, agent, tool, data classification or context. |
| TRUST-06 | Approval for high-risk actions | Configurable human approval for designated high-risk AI-generated actions before execution. |
| TRUST-07 | Central credential vault | API keys, database and model credentials centrally vaulted and never exposed in prompts, contexts or client payloads. |
### AI observability and distributed tracing

| ID | Capability | Expectation |
|---|---|---|
| OBS-01 | Waterfall execution traces | Granular waterfall traces per model invocation, tool execution and memory lookup. |
| OBS-02 | Streaming latency metrics | Time-to-first-token, tokens per second, total duration and queue wait in real time. |
| OBS-03 | Live workload console | Active workload volumes, queue depths, agent states and SLA countdown timers. |
| OBS-04 | Scheduled synthetic checks | Scheduled automated runs verifying availability, response quality and guardrail integrity. |
| OBS-05 | Correlation identifiers | Unique correlation identifiers propagated across requests, invocations, retrievals, workflows, tool calls, guardrail evaluations and logs. |
| OBS-06 | Tamper-evident audit | Tamper-evident audit records of interactions and administration: actions, versions, requests and responses, executions, tool calls, decisions and approvals. |
### FinOps

| ID | Capability | Expectation |
|---|---|---|
| FIN-01 | Usage attribution | Token consumption, compute cost and API invocations attributed to business units, departments and use cases. |
| FIN-02 | Budget thresholds | Usage and cost thresholds at organisation, application and use-case level with alerts, throttling or suspension. |
| FIN-03 | Cost comparison and routing | Cost comparison across models and providers pre-deployment and continuously in production within quality thresholds. |
| FIN-04 | Cost forecasting | Token and cost forecasting from history and growth assumptions. |
### Front-end experience, workbenches and channel enablement

| ID | Capability | Expectation |
|---|---|---|
| FE-01 | Configurable conversational interfaces | Text and voice interfaces deployable across customer, employee and partner channels. |
| FE-02 | Intent, entity and context tracking | Intent understanding, entity extraction and context tracking across turns. |
| FE-03 | Transactional invocation from chat | Conversational invocation of APIs, tools and agents to initiate, track and complete transactions. |
| FE-04 | Multi-step scenarios | Conversational handling of disputes, loan enquiries and status tracking. |
| FE-05 | Conversation summarisation | Automatic summaries of conversations, actions and pending items for downstream systems or humans. |
| FE-06 | Escalation triggers | Escalation to humans on confidence, sentiment or explicit request. |
| FE-07 | Supervisor live view | Interface for supervisors to watch live interactions, guide or take over. |
| FE-08 | Governed API exposure | AI and conversational capabilities exposed through governed APIs for web, mobile, RM portals and core applications. |
| FE-09 | Accessibility | Accessible and inclusive navigation conforming to national accessibility standards. |
| FE-10 | Interaction feedback capture | Feedback, sentiment and satisfaction captured across interactions. |
| FE-11 | Role-based workbenches | Dedicated workbenches for review officers, relationship managers, investigators and supervisors. |
| FE-12 | Paused-task review queue | Centralised queue to review, edit, approve or reject paused agent tasks. |
| FE-13 | Business configuration console | Non-technical configuration of rules, thresholds, workflow parameters and routing without code. |
| FE-14 | Workbench search | Full-text search, boolean filters and faceted drill-down across cases, documents, customers and accounts. |
| FE-15 | Workbench RBAC | Role-based restriction of tabs, actions and sensitive views. |
