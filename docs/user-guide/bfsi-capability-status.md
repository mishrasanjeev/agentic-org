## What this status means

AgenticOrg can be configured for evidence-led BFSI workflows, but a process map
is not proof that a bank's systems, providers, controls or staff are connected.
The current repository assessment has **150 functional capabilities**: **20
covered, 100 partial and 30 gaps**. A covered component may still need a
tenant setting, approved provider, external system or human reviewer. No full
BFSI journey has end-to-end acceptance on the strength of these counts alone.

This page describes functional software coverage, not infrastructure location,
regulatory approval, certification or permission to process customer data. A
feature behind a disabled flag is unavailable in the default installation.

## Where the work stands

| Area | Working foundation | Still needs real implementation or validation |
|---|---|---|
| Agent governance | Agent execution, scoped grants, human review and case evidence paths | Registry-grade discoverability, dependency mapping, model cards and cross-service lifecycle proof |
| Knowledge and documents | Ingestion, OCR and source-aware answers on configured paths | Mixed bundles, page-region review, comparison, hybrid-ranking rollout and cited refusal across every tenant path |
| Conversation and speech | Web chat and configured voice runtime | Intent and channel parity, speaker separation, sensitive-data handling and supervised handoff |
| Transactions and commerce | Read-only source evidence and prepared agent handoffs | Bank-owned source connections, human disposition, provider-owned execution and reconciliation evidence |
| Operations | Logging, approval controls and evaluation hooks | Complete trace coverage, measured SLOs, budget enforcement, rollback and production-grade recovery |

The detailed [functional assessment](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/bfsi/functional-coverage.md)
and [capability matrix](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/bfsi/coverage-matrix.md)
identify individual gaps. The [product status](https://github.com/mishrasanjeev/agentic-org/blob/main/docs/PRODUCT_STATUS.md)
records other release boundaries. These files describe the current repository
assessment; verify your deployed version and configuration separately.

## What to prove in a pilot

1. Select one narrow use case and name its business owner, system-of-record
   owner, integration owner, reviewer and support owner.
2. Use fictional or institution-approved data. Connect each required system
   through its reviewed API or file contract; a diagram is not a connector.
3. Run normal, missing, stale, conflicting, unauthorized and cross-company
   cases. Inspect citations, grants, reviewer decisions and external receipts.
4. Demonstrate restart, replay, revocation, duplicate events and recovery with
   the same users and roles that will operate the pilot.
5. Confirm what the external system actually did. An agent draft, signed
   handoff or payment-capability reference is not an account change, payment,
   settlement or regulatory filing.

For a concrete teaching path, open the [business onboarding](/docs/bfsi-business-onboarding),
[reconciliation](/docs/bfsi-reconciliation), [customer service](/docs/bfsi-customer-service),
[insurance](/docs/bfsi-insurance) or [merchant services](/docs/bfsi-merchant-services)
playbook. Each is a fictional, configured example and carries its own blocked
paths and human ownership.
