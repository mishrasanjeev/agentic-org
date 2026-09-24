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

Before enabling the flag, create exactly one active, shared, tenant-wide agent of each
`agent_type`: `business_underwriter` and `screening_disposition`. Supply only the
provider read-tool names in each agent's `authorized_tools`. Registration derives
`grantex_scopes` from the configured case provider's manifest and stores its own
`grantex_agent_id`; a missing manifest or undeclared tool leaves the role unregistered
and its case calls denied. Each role also needs an exact `case_purposes` list such as
`["aml.cdd.onboarding"]`; a missing or unmatched purpose refuses the call before
grant resolution. After creating and registering each shared role agent, a human
tenant admin sets its list with `PATCH /api/v1/agents/{id}` using the
`case_purposes` field; the update is audited. Provision `GRANTEX_ROOT_GRANT_TOKEN`
and `GRANTEX_API_KEY` from a
secret manager; the root grant must cover both agents' registered scopes. The platform
delegates a short-lived grant for the selected role. A legacy
`config.grantex.grant_token` is deliberately ignored on this path because it could
belong to another agent. Zero or multiple active agents for a role, a missing
registration, an unavailable issuer, or a denied tool all refuse the provider call.
Do not enable the flag until the role registrations and provider manifest have been
verified. The shipped `manifests/mock.json` lists exactly the mock provider's read
tools; other providers need their own manifest in `GRANTEX_MANIFESTS_DIR`.

Provider calls in governed cases always use strict grant checking, even when general
`grants.enforce_closed` is `off` or `warn`. A denied call is recorded in the case's
tool-call record and the investigation fails with `tool_refused:<reason>`; no provider
request is sent. The current published Python SDK checks the grant signature, connector,
tool and permission. AgenticOrg checks the stored case purpose against the selected
role's local `case_purposes`; the SDK does **not** yet enforce token-level purpose or per-case caps,
and the pooled token is not bound to a single case. Do not treat these as active
controls until the newer SDK is published and the case context is passed to it.

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

Four of them additionally need a **human session** and answer 403 `human_session_required` to an
API key or an agent token: withdraw, decision, disposition review and information-request approval
(marked *human* below). Agent tokens are exempt from the RBAC scope families by design - their tool
scopes are enforced at the tool gateway - so the route itself refuses them. Whatever acts is
recorded as `user:<id>`, `api_key:<prefix>` or `agent:<id>`, derived from the session and never from
the request body.

| Method and path | What it does |
|---|---|
| `POST /api/v1/governed-cases` | Submit `{application, purpose?, policy_id?}`; the policy defaults by jurisdiction (US, GB). |
| `GET /api/v1/governed-cases?state=` | List cases, newest first. |
| `GET /api/v1/governed-cases/stats` | Cases by state for the tenant. |
| `GET /api/v1/governed-cases/{case_ref}` | The `business_case` document, memo, policy result, ownership graph, screening results and dispositions, parties, information requests and the transition history. |
| `GET /api/v1/governed-cases/{case_ref}/case-record` | Agent case records for the evidence package: prompt id, version and digest, policy inputs, every tool call with request and response hashes and cited record ids. |
| `POST /api/v1/governed-cases/{case_ref}/investigate` | Start (or retry) the investigation in the background; 202. |
| `POST /api/v1/governed-cases/{case_ref}/withdraw` | Withdraw (*human*). |
| `POST /api/v1/governed-cases/{case_ref}/decision` | `{outcome: approve|decline, decision_grants: [...]}`; 403 `decision_required` unless the grants verify (*human*). |
| `POST /api/v1/governed-cases/{case_ref}/screening-dispositions/{hit_id}/review` | `{action: accepted|overridden, final_outcome, reason?}`; the analyst is the signed-in person; needs the case `awaiting_decision`; an override needs a reason; write-once (409) (*human*). |
| `POST /api/v1/governed-cases/{case_ref}/information-requests` | Propose a request for more information from an approved template and the memo's missing items. |
| `POST /api/v1/governed-cases/{case_ref}/information-requests/{proposal_sha256}/approve` | The signed-in person approves that exact proposal, with the case `awaiting_decision`; only then is the text rendered from the template (*human*). |

Errors are `{"error": {"reason": "<code>", "detail": "..."}}`.

## Decisions

Decisions are refused unless a `DecisionVerifier` confirms decision grants naming the approvers for
the exact semantic action `{case_id, action: "case_decision", decision, subject}`. The shipped
verifier (`RequireDecisionGrant`) refuses everything with `decision_required` until decision grants
(PRD G-3) are wired in: nothing - no agent, workflow step or API caller - can move a case to
`decided` without them.
