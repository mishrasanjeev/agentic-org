# Functional coverage and verification

This reference covers software capabilities only. It does not approve an infrastructure profile,
change a hosted environment, or assert that an integrated financial-services workflow is
ready for use. The [machine-readable baseline](capability-baseline.json) owns the 150 stable
functional IDs; the [coverage matrix](coverage-matrix.md) records per-product evidence.

## Read this status correctly

At the current repository baseline, 15 functional capabilities are covered, 102 are partial,
and 33 are gaps. A combined `covered` result means at least one component has an implemented,
tested capability; it does not mean the capability is wired into every agent, channel or tenant.
Features behind disabled flags remain unavailable in the default installation. No domain or
journey below has end-to-end acceptance solely because its components appear in the matrix.

| Domain | Items | Covered | Partial | Gap | Functional closure needed |
|---|---:|---:|---:|---:|---|
| Acquisition | 3 | 0 | 3 | 0 | Permissioned ingestion, source lineage and refresh outcomes |
| AI governance | 6 | 1 | 3 | 2 | Dependency graph and model cards |
| Content | 8 | 0 | 7 | 1 | Approved-source drafting, clause assembly and translation |
| Conversation | 6 | 0 | 5 | 1 | Typed intent, stateful case handoff and channel parity |
| Evaluation | 7 | 0 | 6 | 1 | Real scored evaluations and governed release decisions |
| Front end | 15 | 3 | 8 | 4 | Multi-step journeys, supervisor view and search |
| FinOps | 4 | 0 | 3 | 1 | Forecasting, attributable spend and budget enforcement |
| Model gateway | 8 | 0 | 8 | 0 | SLO-aware routing, hard limits and complete telemetry |
| Document intelligence | 12 | 0 | 8 | 4 | Bundle, OCR, review geometry and comparison |
| Lead intelligence | 1 | 0 | 1 | 0 | Governed scoring and owner feedback |
| MLOps | 2 | 0 | 2 | 0 | Independent model lifecycle and rollback evidence |
| Observability | 6 | 1 | 5 | 0 | Complete trace coverage and streaming measurements |
| Orchestration | 11 | 4 | 7 | 0 | Durable multi-agent execution and human recovery |
| Prompt governance | 11 | 0 | 8 | 3 | Optimisation, context limits and maker-checker |
| Retrieval | 15 | 1 | 9 | 5 | Hybrid relevance, reranking and governed citations |
| Agent registry | 8 | 0 | 8 | 0 | Lifecycle, discoverability and authority revocation |
| Speech | 8 | 0 | 3 | 5 | Speaker separation, safe transcripts and review |
| Trust controls | 7 | 4 | 3 | 0 | Injection boundaries and blocking output policy |
| Transaction intelligence | 6 | 1 | 1 | 4 | Evidence-linked detection and human disposition |
| Vector infrastructure | 6 | 0 | 4 | 2 | Hybrid ranking rollout, metadata filters and lineage |
| **Total** | **150** | **15** | **102** | **33** | **No full-journey sign-off yet** |

These counts are a snapshot, not a target. A status can improve only after the real runtime
path and its regression tests are present, and the JSON and Markdown matrix agree.

## Product boundary

- AgenticOrg owns agent execution, enterprise connectors, knowledge, case workflows,
  human review, workbenches, channel experiences, model routing and functional telemetry.
- Grantex supplies identity, grants, policy and revocation authority. It should not be an
  online toll booth for every non-binding model or knowledge interaction.
- External systems remain authoritative for account, transaction and merchant records.
  An agent may read through an approved connector, but it cannot invent or silently post
  source-of-record changes. A human or separately governed external system owns final
  regulated decisions and filings.
- A feature flag or a sandbox example is not evidence that a tenant has enabled a feature.

## Verification scenarios

### 1. Governed agent and authority lifecycle

Create, version, assign, suspend and retire an agent in AgenticOrg. Bind its tool and data
permissions to a scoped grant; revoking or retiring the agent must invalidate subsequent
use, including cached authorisations. Reject an unknown approval role and prevent the same
person from satisfying a distinct-approver gate. Test different tenants, stale grants,
concurrent changes and restart/replay. Demonstrate the lifecycle in the operator UI and
show an auditable refusal, not merely a disabled button.

Relevant IDs: `REG-01` through `REG-08`, `AIGOV-01` through `AIGOV-06`, `TRUST-05`,
`TRUST-06`, and the related orchestration IDs. Remaining work includes registry-grade
discoverability, dependency mapping and model cards; the current lifecycle and approval
patches must pass tests before their status is raised.

### 2. Document-to-answer and document-to-case

Ingest a permissioned mixed bundle, classify and split its documents, OCR a scanned page,
preserve page/source lineage, and index only content visible to the requesting principal.
Ask a multi-part question: hybrid lexical and dense retrieval must rank tenant-authorised
evidence, return page-level citations, and decline an answer when evidence is absent or
conflicting. Route an uncertain extraction to a human review with a page-region overlay.
Compare a revised version without losing the original evidence.

Relevant IDs: `ACQ-01` through `ACQ-03`, `IDP-01` through `IDP-12`, `RAG-01` through
`RAG-15`, `VEC-01` through `VEC-06`, `TRUST-01` through `TRUST-04`. Native hybrid
retrieval is an opt-in native path; reranking, query decomposition, graph
retrieval, bundle handling and region-based review remain open until proven.

### 3. Assisted conversation and supervised handoff

A customer begins in an authenticated channel. The agent identifies the supported intent,
uses only approved context, states uncertainty, and hands an unresolved issue to a named
human queue with the source trail. For voice, verify consent, STT/TTS quality, redaction,
speaker separation, a reviewed summary and a blocked unsafe disclosure. A channel without
an inbound bridge or approved telephony credentials is not represented as operational.

Relevant IDs: `CONV-01` through `CONV-06`, `SPEECH-01` through `SPEECH-08`, `FE-01`
through `FE-07`. Intent classification, diarisation, call summaries, disclosure controls,
spoken sensitive-data redaction and supervisor visibility remain material gaps.

### 4. Evidence-led transaction and merchant workflows

Read synthetic or consented transaction and merchant-system records through a scoped,
read-only connector. Preserve source timestamps and reconcile mismatches before drafting
an explanation or case. Detect a demonstrable suspicious pattern with traceable features,
show the evidence to an authorised reviewer, and require a human disposition. Do not
describe a draft as an automatic report, credit decision, payment, refund or ledger post.

Relevant IDs: `TXN-01` through `TXN-06`, `FE-04`, `FE-05`, `FIN-01` through `FIN-04`.
Structuring and pass-through detection, fund-flow graphs and narrative generation are
currently gaps. A generic merchant workflow needs connector parity, inventory/source
freshness, failure handling and human confirmation before it can claim closure.

### 5. Model gateway and measurable operations

Route two eligible models under an explicit tenant policy. Enforce application/principal
access, budget and hard concurrency/rate limits; record the selected target, refusal,
fallback, cost and latency under the same trace. Induce a limit-store outage and specify
the safe response. Exercise a degraded model, a slow model and an inaccessible data region.
Operators should see bounded, tenant-safe metrics and a useful run timeline, not secret or
customer content. Record streaming first-token latency only after streaming is implemented.

Relevant IDs: `GW-01` through `GW-08`, `OBS-01` through `OBS-06`, `FIN-01` through
`FIN-04`, `EVAL-01` through `EVAL-07`. Existing weighted routing and signed call records
are real, but the default limit-store policy still admits requests on outage; strict refusal
requires an explicit deployment switch. Model utilisation, complete traces, first-token timing and forecasting remain
incomplete.

## Exact gap register

The IDs below currently have no usable product-form implementation in the combined matrix.
They are not waived by a design document or another product's marketing page.

| Area | Gap IDs |
|---|---|
| Content and conversation | `CONTENT-08`, `CONV-01` |
| Speech | `SPEECH-03`, `SPEECH-04`, `SPEECH-05`, `SPEECH-07`, `SPEECH-08` |
| Retrieval and documents | `RAG-04`, `RAG-06`, `RAG-12`, `RAG-14`, `RAG-15`, `IDP-01`, `IDP-05`, `IDP-08`, `IDP-12` |
| Transactions and vector | `TXN-02`, `TXN-03`, `TXN-04`, `TXN-05`, `VEC-03`, `VEC-05` |
| Prompt and evaluation | `EVAL-02` |
| Governance, FinOps and experience | `AIGOV-05`, `AIGOV-06`, `FIN-04`, `FE-04`, `FE-05`, `FE-07`, `FE-14` |

## Requirement-level acceptance rule

For each of the 150 IDs, record an owner, server-side entry point, tenant and role check,
persistence boundary, failure/refusal path, local executable test, operator or user journey,
and code evidence in the matrix. A partial item stays partial until its missing behavior is
shown through the same supported path a tenant would use. A gap becomes covered only after
runtime code, API/UI where applicable, adverse-case regression, and integrated local replay.
Test parity must include absent consent, cross-tenant access, bad source data, stale evidence,
duplicate requests, interrupted workflow, provider timeout and revoked authority where
relevant. Do not use a mock as the only proof of an external capability.

The first verification environment is the local Docker stack with synthetic data and approved
test-only credentials. Record command, commit, fixture, observed result and skipped external
steps for every journey. Infrastructure, residency and production deployment are separate
decisions and are not represented as verified here.
