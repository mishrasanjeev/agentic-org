# Decision requests: how a person decides a governed case

A governed case reaches `decided` only with decision grants (PRD G-3) — a second credential that
only a named person can mint, bound to one semantic action. **AgenticOrg cannot mint one, and no
screen in this product collects an approval.** The approval happens on the Grantex auth service's
own approval page, in the approver's browser, on the auth service's origin.

```text
 console / API            AgenticOrg                      Grantex auth service
 ─────────────            ──────────                      ────────────────────
 Request decision ─────►  POST .../decision-requests ───► PUT  /v1/decisions/cases/{case}
                                                          POST /v1/decisions/requests
                          record on the case         ◄─── requestId + approvalPage
 Open approval page ────────────────────────────────────► the approver signs in (OIDC),
                                                          steps up, reads the memo and the
                                                          policy score, approves;
                                                          the service measures the dwell
 Live status       ─────► GET .../decision-requests/{id} ► GET  /v1/decisions/requests/{id}
 Record decision   ─────► POST .../decision             ► POST /v1/decisions/consume
                          case → decided               ◄─── approvers, grant ids
```

## What each side is responsible for

| Concern | Decided by | Where |
| --- | --- | --- |
| Who may approve | the auth service's allow-listed identity providers | approval page |
| Step-up authentication | the identity provider and the auth service | approval page |
| How long the approver looked (dwell) | the auth service, from two of its own timestamps | approval page |
| Four eyes (two different approvers) | the auth service, on `(issuer, subject)` | approval page |
| What is being approved | AgenticOrg's semantic action, hashed by the service | decision request |
| Whether the case may be decided | AgenticOrg's case lifecycle and the consumed grants | case API |

The semantic action is `{case_id, action: "case_decision", decision, subject, extra: {tenant}}` —
never the raw tool payload, so re-planning or a new timestamp cannot invalidate a person's
decision, and a grant for one case, tenant or outcome cannot decide another. (`case_ref` is unique
per tenant, not per issuer developer, so the tenant is bound into the action.)

## Making a request

```
POST /api/v1/governed-cases/{case_ref}/decision-requests
{"outcome": "decline", "override_reason": "…", "client_dwell_ms": 45000}
```

- The case must be `awaiting_decision` and have a memo and a policy result.
- The request is bound to the case's **current version**. A later change to the case (an analyst
  review, a re-investigation) supersedes it: the status then reports `case_changed: true` and
  consumption is refused with `case_changed`. Ask for a new decision on the new memo. The new
  version is also registered with the issuer as the case changes, so it supersedes the open request
  and revokes grants that were minted but never used, instead of leaving them live until they
  expire.
- `override_reason` is for asking for an outcome other than the memo's recommendation. It is shown
  to the approver and recorded on the case.
- The answer carries `approval_page` (where the approver goes), `approvals_required` (2 when the
  outcome is in the manifest's `four_eyes_on`, `AGENTICORG_CASE_DECISION_FOUR_EYES_ON`), the action
  and its hash, and never a grant token.
- The record is written on the case as `decision_requests[]`, without bumping the case version
  (the request is bound to it).

## Watching the approvals

```
GET /api/v1/governed-cases/{case_ref}/decision-requests/{request_id}
```

Reports the live status from the issuer: each approval's subject, authentication method, position,
the issuer-measured `dwell_ms` with `dwell_source: "server"`, whether the grants are ready, and
whether the case has changed since. A request that is not recorded on this case is `404
decision_request_not_found`, so one case's screen cannot poll another's request.

**Dwell.** The authoritative dwell is the one the approval page measured. The console may send
`client_dwell_ms` — how long its own screen was open before the person acted — and that is recorded
as advisory telemetry only (`agenticorg_case_console_dwell_seconds{stage}`, logged with
`dwell_source: "console"`). It never reaches a decision grant and never gates anything.

## Recording the decision

```
POST /api/v1/governed-cases/{case_ref}/decision
{"outcome": "decline", "decision_request_id": "…", "client_dwell_ms": 120000}
```

The server fetches the minted grants from the issuer itself — **a decision grant never reaches the
browser** — and consumes them atomically for the exact action and the case's current version. Only
a confirmed consumption records the decision, with each approver and grant id on the case. Every
other answer is a refusal:

| Reason | Means |
| --- | --- |
| `decision_required` | no grants at all; nothing proves a person decided |
| `decision_not_approved` | the request has no usable grants yet (waiting for an approver) |
| `decision_outcome_mismatch` | the request was made for the other outcome |
| `action_mismatch`, `wrong_case` | the grants were minted for something else |
| `case_changed` | the case changed after the approval |
| `consumed`, `expired`, `revoked` | the grants are spent or no longer valid |
| `same_approver`, `four_eyes_incomplete` | four eyes is not satisfied |
| `decision_service_not_configured` | no issuer is configured here |
| `decision_service_unavailable`, `decision_service_disabled` | the issuer could not answer, or has decision grants switched off |
| `decision_request_not_found` | the issuer no longer holds that request (an unknown id, or one past its 24-hour ceiling) |

Passing `decision_grants` directly is still supported for callers that already hold tokens; the
same consumption and the same refusals apply.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_CASE_DECISION_SERVICE` | `""` (off) | `grantex` to enable decision requests |
| `AGENTICORG_CASE_DECISION_CONNECTOR` | `governed_cases` | connector the request is made under |
| `AGENTICORG_CASE_DECISION_FOUR_EYES_ON` | `decline` | outcomes needing two different approvers |
| `GRANTEX_BASE_URL`, `GRANTEX_API_KEY` | — | the issuer and the platform's developer key; **both are required** when the service is on, and an unset `GRANTEX_BASE_URL` is refused (`decision_service_not_configured`) rather than falling back to a default origin |

Decision requests also need the tenant's `governed_cases.enabled` flag. With the service off, the
case API behaves exactly as before: every decision is refused with `decision_required`.

The auth service must run with decision grants enabled and with the approver identity providers
allow-listed by its administrator; AgenticOrg's developer key cannot do either, by design.

## Enablement checklist

The issuer side is implemented and covered in the Grantex repository (the decision routes, the
approval page, four eyes and the browser end-to-end suite). What is not yet proven *here* is the
join between the two, because this stack pins an auth-service image that predates those routes.
Before turning `AGENTICORG_CASE_DECISION_SERVICE=grantex` on anywhere:

1. **Rebuild the development auth-service image** so `docker-compose.dev.yml` runs a Grantex build
   that serves `/v1/decisions/...` and the approval page, with `DECISION_GRANTS_ENABLED=true`.
2. **Allow-list an approver identity provider** for the developer, through the service
   administrator's API. The platform's own key cannot do this, by design.
3. **Take one genuine four-eyes approval end to end**: request a decline from the console, approve
   on the issuer's page as two different people with step-up, record the decision, and check the
   case's `decision.approvers`, the issuer-measured dwell and the audit chain on both sides.
4. **Set the issuer explicitly.** With the service on and no `GRANTEX_BASE_URL`, decision requests
   are refused: nothing should be asked of an issuer nobody chose.
5. **Consider verifying the grants here as well.** This platform consumes them at the issuer, which
   verifies the signature and key, the audience and issuer, the action hash, the dwell source, the
   memo and policy hashes and the four-eyes structure under its own row locks, and refuses anything
   that does not match. That is fail-closed but single-sided; verifying them locally too
   (decision-grant profile §6 steps 2 and 3) needs the Grantex Python SDK's `grantex.decisions`
   verifier once it is published.

Answers from the issuer are parsed strictly: any field the console states as fact - the action, its
hash, the case version, how many approvals are required, and each approval's subject,
authentication, position and dwell source - is refused when absent
(`decision_service_response_invalid`) rather than defaulted, so a renamed field fails loudly
instead of showing one approver where four eyes were required.

## What is recorded

- `governed_cases.decision_requests[]` — request id, the outcome asked for, the case version, the
  approval page, how many approvals are required, the action hash, who asked, when, any override
  reason and the advisory console dwell.
- `governed_cases.decision` — the outcome, each approver and the decision grant id consumed.
- `governed_case_transitions` — the move to `decided`, with the actor who recorded it.
- Metrics: `agenticorg_case_decision_requests_total{outcome,result}`,
  `agenticorg_case_decision_grants_consumed_total{outcome,result}`,
  `agenticorg_case_console_dwell_seconds{stage}`.

The issuer keeps its own audit chain of every sign-in, approval, consumption and refusal.

## Implementation notes

`core/cases/decision_requests.py` holds the service interface, the Grantex client and the verifier
that plugs into `CaseRuntime.decision_verifier`. **The endpoint shapes are provisional**: they
follow the published decision-grant API, which is still changing, and the Grantex Python SDK's
`decisions` client (grantex 0.6) will replace the hand-written request building once it is
released. Tests run against `core/test_doubles/fake_decision_grants.py`, which enforces the same
rules — four eyes, the same approver refused, single-use grants, action and case-version binding.
