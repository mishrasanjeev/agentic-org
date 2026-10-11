## What this status means

AgenticOrg can be configured for evidence-led BFSI workflows, but a process map
is not proof that a bank's systems, providers, controls or staff are connected.
The current repository assessment has **150 functional capabilities**: **24
covered, 97 partial and 29 gaps**. A covered component may still need a
tenant setting, approved provider, external system or human reviewer. No full
BFSI journey has end-to-end acceptance on the strength of these counts alone.

Services built behind default-off settings after this baseline was last refreshed
are not yet reflected in these counts; where the table below names one, it is
marked (built, off by default). They are document processing, speech and call
intelligence, transaction intelligence, conversational services and the
supervisor view, workbenches, lineage, content services, personalisation, the
agent registry, model cards, and FinOps thresholds and forecast. Each is
unavailable until its setting is turned on; see
[feature switches that ship off](/docs/self-hosting#feature-switches-that-ship-off)
and [the six screens that ship switched off](/docs/review-and-operations-screens).
The Banking Pack can be installed without a setting, but it is not counted
either.

This page describes functional software coverage, not infrastructure location,
regulatory approval, certification or permission to process customer data. A
feature behind a disabled flag is unavailable in the default installation.

## Where the work stands

| Area | Working foundation | Still needs implementation, enabling or validation |
|---|---|---|
| Agent governance | Agent execution, scoped grants, human review and case evidence paths | Registry-grade discoverability, dependency mapping and model cards (built, off by default) and cross-service lifecycle proof |
| Knowledge and documents | Ingestion, OCR and source-aware answers on configured paths | Mixed bundles, page-region review and document comparison (built, off by default), hybrid-ranking rollout (off by default) and cited refusal across every tenant path |
| Conversation and speech | Web chat and configured voice runtime | Banking intents, speaker separation, spoken sensitive-data redaction and supervised takeover (built, off by default), and channel parity |
| Transactions and commerce | Read-only source evidence and prepared agent handoffs | Human disposition of transaction findings (built, off by default), bank-owned source connections, provider-owned execution and reconciliation evidence |
| Operations | Logging, approval controls and evaluation hooks | Budget thresholds and synthetic checks (built, off by default), complete trace coverage, measured SLOs, rollback and production-grade recovery |

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
