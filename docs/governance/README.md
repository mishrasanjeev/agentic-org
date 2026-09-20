# Governance: grants, policy scores and human decisions

AgenticOrg's governed workflows - starting with business onboarding - are built so that no agent
can approve, decline, close, file or pay anything, however it is prompted and whoever calls it.
Three independent controls make that structural rather than a matter of instructions:

| Control | Answers | Decided by | Where |
|---|---|---|---|
| **Grant** | May this agent call this tool, for this purpose, now? | The Grantex grant attached to the run | Tool gateway, before every call |
| **Policy score** | How risky is this case on the evidence, and what should a human be told? | A versioned, deterministic policy over provider data | After the investigation, before the memo |
| **Human decision** | What happens to the case? | A named human, proven by a decision grant | Only path to `decided` |

Model output feeds none of the three. The model writes prose - memo section summaries and
disposition rationales - from statuses, codes and counts, and that prose is checked against the
evidence it cites before it is kept.

```text
 application ─► case (submitted)
                 │
                 ▼  investigate (agent run)
       ┌───────────────────────────────────────────────┐
       │ every tool call ─► grant check ─► provider     │  read tools only; a refusal fails the run
       │ provider data ─► reconciliation, screening,    │
       │                  extraction (untrusted text    │
       │                  never reaches the model)      │
       │ evidence fields ─► policy ─► tier, score,      │  deterministic, versioned
       │                              fired rules       │
       │ tier + missing items ─► recommendation         │  a proposal, never an action
       │ codes and counts ─► model ─► summaries         │  checked against citations
       └───────────────────────────────────────────────┘
                 │
                 ▼
          awaiting_decision ─► signed push / console
                 │
                 ▼  human decides, decision grant verified
             decided
```

## Grants: what an agent may do

Every provider call a reference agent makes passes through the provider tool gateway
(`core/tool_gateway/provider_gateway.py`):

1. The agent's tool set can contain only read tools - resolve, verify, ownership, screening and web
   presence. A tool set naming anything else (a decision, a filing, a deletion, a monitor enrolment)
   is refused when the gateway is built. The Screening Disposition agent holds only the two
   screening tools.
2. The run's grant is checked before the call. A refusal, or a grant check that cannot answer,
   stops the run before the provider is reached: the case goes to `failed` with
   `tool_refused:<reason>` (for example `tool_refused:grant_missing`). This is the seam PRD F-1's
   per-run grant (`grants.enforce_closed`) plugs into.
3. A capability the provider does not offer is `not_available`, not an error: the memo section says
   so and the missing-items list asks for it.

Grants bound *what* an agent can touch. They never let an agent decide: there is no decision tool
to grant.

## Policy scores: what the evidence says

The case policy (`core/policy/`, [authoring](../policies/authoring.md)) is evaluated over evidence
fields the agent computes from provider data - registry status, whether declared owners reconcile
with the ownership graph, unresolved screening matches, observed versus declared activity. It
returns a tier (`low` < `medium` < `high` < `blocked`), a score and every fired rule, and it is
recorded with its version and input digest in the memo, the case record and every push.

- **Missing evidence never passes.** A field the provider could not supply makes the rules that read
  it fire as indeterminate, moving the case towards the stricter tier.
- **Only human review resolves a screening hit.** A hit counts as an unresolved possible match until
  an analyst records a review; an agent's proposed disposition never changes the policy input.
- **The recommendation follows the tier.** `blocked` → `decline`, `high` → `refer`, missing items →
  `request_information`, `medium` → `refer`, `low` → `approve` - always marked
  `requires_human_decision: true` and `basis: policy_result`.
- **Model confidence is metadata.** It is recorded (`provenance.model_confidence`) and gates
  nothing. See [ADR 0011](../adr/0011-policy-over-confidence.md).

Shipped policies are examples. A strict runtime refuses to load them for cases; production needs a
reviewed policy in `AGENTICORG_CASE_POLICY_DIR`.

## Human decisions: what happens to the case

A case moves to `decided` only through `core.cases.decisions.record_decision`, and only when a
`DecisionVerifier` confirms decision grants that name the approvers for the exact semantic action:

```json
{"case_id": "case_…", "action": "case_decision", "decision": "approve", "subject": "acme_kyb:…"}
```

Until decision grants (PRD G-3) are wired in, the shipped verifier refuses every decision with
`decision_required`. That is deliberate: a decision nobody can prove a human made is not recorded.
The workflow's `human_in_loop` step, the console and `POST /api/v1/governed-cases/{case_ref}/decision`
all end at the same check.

Humans also act before the decision, always as the authenticated user, never as an identity in a
request body:

- **Screening dispositions** — an analyst accepts or overrides each agent proposal; an override
  needs a written reason; each review is written once
  ([Screening Disposition](../agents/screening-disposition.md#recording-an-analysts-review)).
- **Requests for more information** — the agent proposes items from an approved template; an
  analyst approves that exact proposal digest before any text is rendered for the applicant.

## Hand-off

At `awaiting_decision` and after every later change, the case is written to the push outbox in the
same transaction and delivered, signed, to the operator's system of record; the same document is
available over REST. Provider webhooks never change a case; they only trigger a re-investigation
from the provider. See [Case hand-off](case-hand-off.md).

## What is recorded for audit

| Record | Holds |
|---|---|
| `governed_case_transitions` | every state change with actor, reason and case version |
| `governed_cases.agent_records` | per agent run: prompt id, version and SHA-256; policy result and inputs; every tool call with outcome, request and response hashes and cited record ids; narrative refusals; whether pseudonymisation was on |
| `governed_cases.memo` / `policy_result` / `screening_dispositions` | the cited documents and every analyst review |
| `governed_cases.decision` | approvers and decision grant ids |
| `case_push_outbox` | every hand-off event, its payload digest and delivery history |
| `provider_webhook_receipts` | every inbound provider event's outcome, never its body |

`GET /api/v1/governed-cases/{case_ref}/case-record` returns the agent records for the evidence
package (PRD G-5).

## Reading on

- [Case lifecycle](case-lifecycle.md) — states, transitions, workflows, API
- [Case hand-off](case-hand-off.md) — signed push, REST retrieval, dead letters, provider webhooks
- [Business Onboarding Underwriter](../agents/business-underwriter.md)
- [Screening Disposition](../agents/screening-disposition.md)
- [Untrusted content](../security/untrusted-content.md) and [pseudonymisation](../security/pseudonymisation.md)
- [Writing a verification provider](../providers/writing-a-verification-provider.md)
