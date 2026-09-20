# Governed case lifecycle

A **governed case** is one business application under review. It is stored in `governed_cases`
(row-level security by tenant) with every document the reference agents produce, and it moves
through a fixed lifecycle (`core/cases/states.py`):

```text
submitted ──► in_progress ──► awaiting_decision ──► decided
    │             │   ▲              │   │
    │             ▼   │              │   └──► withdrawn
    │           failed ──────────────┘   (awaiting_decision ──► in_progress re-investigates)
    └──────────────────────────────────────────► withdrawn
```

| State | Meaning | Who moves it |
|---|---|---|
| `submitted` | Application accepted and schema-valid (`business_case`). | `POST /api/v1/governed-cases` |
| `in_progress` | The Business Onboarding Underwriter is investigating. | the `investigate` step or `POST …/investigate` |
| `awaiting_decision` | Memo, policy result, screening results (and dispositions) are stored. | the agent run completing |
| `failed` | The investigation could not produce a memo; `failure_reason` says why (for example `tool_refused:grant_missing`, `policy_not_configured`). | the agent run failing; retry re-enters `in_progress` |
| `decided` | A human decision with verified decision grants is recorded. Terminal. | `POST …/decision` or the `record_decision` step |
| `withdrawn` | The application was withdrawn. Terminal. | `POST …/withdraw` |

Every transition is written to `governed_case_transitions` with the actor (always from the
authenticated session or the workflow), the reason code and the case version, and counted in
`agenticorg_governed_case_transitions_total{from_state,to_state}`. Transitions use the case
`version` so a concurrent change is refused (`case_version_conflict`), never overwritten: a memo
produced while a case was withdrawn is discarded.

## Turning it on

Everything here is off unless the tenant's **`governed_cases.enabled`** flag is on
(`POST /api/v1/feature-flags`). While it is off, or if the flag cannot be read, every route answers
404 `governed_cases_disabled` and every `case_agent` workflow step fails with that reason.

| Setting | Default | Purpose |
|---|---|---|
| `AGENTICORG_CASE_PROVIDER` | `mock` | Registered verification provider for new cases. The mock refuses to run outside local and test environments. |
| `AGENTICORG_CASE_POLICY_DIR` | *(examples)* | Directory of case policies. A strict runtime refuses the shipped example policies, so production needs reviewed policies here. |
| `AGENTICORG_CASE_LLM_MODEL` | *(platform default)* | Model the agents use for prose. |

Migration: `v6z25_governed_cases` (additive, forward-only).

## Workflows

`workflows/examples/business_onboarding.yaml` runs a case end to end on the workflow engine using
the `case_agent` step type:

1. `investigate` — the underwriter produces the cited memo; the case reaches `awaiting_decision`
   (or `failed`).
2. `propose_screening_dispositions` — the Screening Disposition agent proposes a disposition for
   every hit.
3. `underwriting_decision` — a `human_in_loop` step for the underwriter.
4. `record_decision` — records the decision **only** if the decision grants verify; otherwise the
   step fails with `decision_required` and the case stays `awaiting_decision`.

`workflows/examples/screening_disposition.yaml` is the reusable per-case disposition workflow
(for example after a re-screen), ending in a `human_in_loop` step for the screening analyst.

A `case_agent` step takes `action` (`investigate`, `dispose_screening_hits`, `record_decision`),
`case_ref` (usually `$case_ref` from the trigger payload), and for `record_decision` the
`decision_step` whose human decision to record and `decision_grants`.

## API

All routes need an authenticated tenant user; reads need `approvals:read`, writes `approvals:write`.

| Method and path | What it does |
|---|---|
| `POST /api/v1/governed-cases` | Submit `{application, purpose?, policy_id?}`; the policy defaults by jurisdiction (US, GB). |
| `GET /api/v1/governed-cases?state=` | List cases, newest first. |
| `GET /api/v1/governed-cases/stats` | Cases by state for the tenant. |
| `GET /api/v1/governed-cases/{case_ref}` | The `business_case` document, memo, policy result, ownership graph, screening results and dispositions, parties, information requests and the transition history. |
| `GET /api/v1/governed-cases/{case_ref}/case-record` | Agent case records for the evidence package: prompt id, version and digest, policy inputs, every tool call with request and response hashes and cited record ids. |
| `POST /api/v1/governed-cases/{case_ref}/investigate` | Start (or retry) the investigation in the background; 202. |
| `POST /api/v1/governed-cases/{case_ref}/withdraw` | Withdraw. |
| `POST /api/v1/governed-cases/{case_ref}/decision` | `{outcome: approve|decline, decision_grants: [...]}`; 403 `decision_required` unless the grants verify. |
| `POST /api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review` | `{action: accepted|overridden, final_outcome, reason?}`; the analyst is the authenticated user; an override needs a reason; write-once (409). |
| `POST /api/v1/governed-cases/{case_ref}/information-requests` | Propose a request for more information from an approved template and the memo's missing items. |
| `POST /api/v1/governed-cases/{case_ref}/information-requests/{proposal_sha256}/approve` | The authenticated user approves that exact proposal; only then is the text rendered from the template. |

Errors are `{"error": {"reason": "<code>", "detail": "..."}}`.

## Decisions

Decisions are refused unless a `DecisionVerifier` confirms decision grants naming the approvers for
the exact semantic action `{case_id, action: "case_decision", decision, subject}`. The shipped
verifier (`RequireDecisionGrant`) refuses everything with `decision_required` until decision grants
(PRD G-3) are wired in: nothing - no agent, workflow step or API caller - can move a case to
`decided` without them.
