# BFSI Enterprise AI Platform baseline

This folder holds the capability baseline a bank or other regulated financial institution expects from an
enterprise AI platform, and how AgenticOrg and the Grantex authority layer measure against it today. The
baseline is written in the platform's own terms and identifiers; it is not tied to any single institution,
procurement or cloud provider. It is product documentation: what the platform provides, what it provides in
part, and what it does not provide yet, with the repository evidence for each answer.

| Document | Purpose |
|---|---|
| [capability-baseline.md](capability-baseline.md) | The catalogue: baseline conditions, technical capabilities and functional capabilities, grouped by domain. |
| [coverage-matrix.md](coverage-matrix.md) | Per-capability status for AgenticOrg and Grantex with repository evidence and the capability group each item belongs to. |
| [deployment-reference.md](deployment-reference.md) | How an in-country deployment satisfies the infrastructure items that belong to the hosting platform. |
| [capability-baseline.json](capability-baseline.json) | Machine-readable catalogue and status, consumed by the capability readiness report. |

## Status at a glance

| Section | Items | Covered | Partial | Gap |
|---|---:|---:|---:|---:|
| Baseline conditions | 5 | 0 | 4 | 1 |
| Technical capabilities | 50 | 4 | 32 | 14 |
| Functional capabilities | 150 | 14 | 101 | 35 |
| Total | 205 | 18 | 137 | 50 |

Status rules: **Covered** means the capability exists in product form with tests; **Partial** means part of it
exists or it exists for one domain only; **Gap** means nothing usable exists yet. A cloud-provider item counts
as covered only when the deployment reference names the control and the compliance report can attest it.

## How the two products divide the work

- **AgenticOrg** is the platform: agents, workflows, tool gateway, knowledge retrieval, governed cases, consoles,
  observability and FinOps. Most functional capabilities land here.
- **Grantex** is the authority layer: Agent Passports, grants, decision grants, trust registry and attestations,
  evidence packages, emergency stop and consent. It supplies the authorisation, approval and evidence primitives
  the platform enforces.

## Capability groups

Each capability in the matrix belongs to one group. A group names the product area that owns the capability;
it carries no delivery commitment.

| Group | Area | Repositories |
|---|---|---|
| WP-00 | In-country deployment reference architecture | agentic-org, grantex |
| WP-01 | Operator override for models, agents, workflows and tools | agentic-org, grantex |
| WP-02 | Model gateway: routing policy, access policy, limits, metrics and audit | agentic-org |
| WP-03 | Runtime guardrail pipeline | agentic-org |
| WP-04 | Knowledge retrieval v2 | agentic-org |
| WP-05 | Agent registry, lifecycle and certification | agentic-org, grantex |
| WP-06 | Prompt governance | agentic-org |
| WP-07 | Evaluation framework | agentic-org |
| WP-08 | AI governance inventory, risk tiers, policies and model cards | agentic-org, grantex |
| WP-09 | Observability and tamper-evident audit | agentic-org |
| WP-10 | FinOps | agentic-org |
| WP-11 | Conversational services for banking | agentic-org |
| WP-12 | Content services | agentic-org |
| WP-13 | Speech and conversation intelligence | agentic-org |
| WP-14 | Intelligent document processing | agentic-org |
| WP-15 | Transaction intelligence | agentic-org |
| WP-16 | Data acquisition, provenance and lineage | agentic-org |
| WP-17 | Agent runtime: builder, limits, memory, sandbox and debugging | agentic-org |
| WP-18 | Workbenches and business console | agentic-org |
| WP-19 | Residency, isolation and no-training controls | agentic-org, grantex |
| WP-20 | Personalisation service | agentic-org |
| WP-21 | Authority layer hardening | grantex |

## Conventions

- Every behaviour change on an existing path ships behind a flag that defaults to off.
- Vendor neutrality: provider names appear only as product integrations inside code.
- House terminology as set out in `AGENTS.md`.
- The matrix is regenerated whenever a capability lands, so it stays truthful.
