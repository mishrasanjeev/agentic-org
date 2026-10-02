# BFSI Enterprise AI Platform baseline

This folder holds the capability baseline a bank or other regulated financial institution expects from an
enterprise AI platform, how AgenticOrg and the Grantex authority layer measure against it today, and the
programme that closes the gaps. The baseline is written in the platform's own terms and identifiers; it is
not tied to any single institution, procurement or cloud provider.

| Document | Purpose |
|---|---|
| [capability-baseline.md](capability-baseline.md) | The catalogue: baseline conditions, technical capabilities and functional capabilities, grouped by domain. |
| [coverage-matrix.md](coverage-matrix.md) | Per-capability status for AgenticOrg and Grantex with repository evidence and the work package that closes each gap. |
| [programme-plan.md](programme-plan.md) | Work packages, phases, flags, acceptance criteria and the pull-request sequence. |
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

## Conventions

- Every behaviour change on an existing path ships behind a flag that defaults to off; the flag is named in the plan.
- Vendor neutrality: provider names appear only as product integrations inside code.
- Terminology: registry (not directory), operator override (not kill switch), issuer-branded (not white-label),
  irregularity (not anomaly), attestation (not verification result), accredited issuer (not trust provider),
  relying party (not consumer), Agent Passport and grant.
