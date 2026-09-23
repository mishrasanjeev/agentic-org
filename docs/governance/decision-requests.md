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

### Trying it on the local stack

```
export AGENTICORG_DEV_DECISION_GRANTS=true
export AGENTICORG_DEV_GRANTEX_ADMIN_KEY=<a development administrator key you choose>
export AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex
export AGENTICORG_SEED_PASSWORD=<a local-only passphrase of at least 12 characters>
make dev
make seed seed-cases
make e2e-decisions
```

Without `AGENTICORG_DEV_DECISION_GRANTS=true` the auth service runs with decision grants off, no
administrator key and no relaxed outbound rules, and the identity provider approvers sign in with
(`oidc-approvers`, two fixture people, in the `decisions` compose profile) is not started at all —
so a stack that never asks for any of this does not get it. Allow-listing that provider is a
service-administrator action, with the administrator key above:

```
DEV=$(curl -fsS -H "Authorization: Bearer $GRANTEX_API_KEY" "$GRANTEX/v1/me" | jq -r .developerId)
curl -fsS -X POST -H "Authorization: Bearer $GRANTEX_ADMIN_API_KEY" -H 'Content-Type: application/json' \
  "$GRANTEX/v1/admin/developers/$DEV/decision-approver-idps" \
  -d '{"issuer":"…","clientId":"…","clientSecret":"…","acrValues":["urn:agenticorg:acr:step-up"],
       "requireVerifiedEmail":true,"displayName":"Development approvers","actor":"your name"}'
```

The auth service's approval page only runs on an https origin or a loopback one, so in the local
stack it publishes `http://127.0.0.1:<port>` and listens on that same port inside its container.
The decision-grant browser suite therefore runs in the auth service's network namespace; the
console is still reached by service name.

## Enablement checklist

### Proven

The join between the two systems runs, and is kept running by
`ui/e2e/decision-grants.spec.ts` (`make e2e-decisions`) against the local stack. Nothing in that
suite is stubbed: the console, the API and the database are this stack's; the decision request, the
approval page, the sign-in, the step-up, the dwell measurement, the four-eyes rule and the decision
grants are the auth service's.

1. **The development auth-service image serves the decision routes.** `docker-compose.dev.yml` pins
   `ghcr.io/mishrasanjeev/grantex-auth-service` by digest to a build of Grantex `main`
   (`5b867f68`), with `DECISION_GRANTS_ENABLED=true`, a vault key, an `ADMIN_API_KEY` and a
   step-up policy. `scripts/dev_stack_smoke.sh` checks that the approval page answers.
2. **An approver identity provider is allow-listed by the service administrator.** The suite does
   it with `ADMIN_API_KEY` and asserts that the platform's own developer key is refused (`401`)
   for the same call. The stack runs one for approvers only (`oidc-approvers`,
   `tools/oidc_stub/config.approvers.dev.json`), separate from the console's development SSO.
3. **A genuine four-eyes decline, end to end** (PRD §8.4 step 6). The console asks for a decline on
   a case in `awaiting_decision`; two different people sign in on the auth service's own page with
   a second factor and approve there; the console records the decision. The case carries two
   approvers with distinct namespaced subjects and two distinct `dgnt_…` grant ids, and the issuer
   carries the dwell it measured itself (`dwell_source: server`) and an audit chain entry for each
   sign-in, approval and consumption.
4. **A single approval with step-up** (PRD §8.4 step 5), on the same machinery with
   `approvals_required: 1`.
5. **The same approver is refused the second approval.** Signing in again as the first approver, in
   a clean browser, gets "You have already approved this decision"; the request stays at one of two.
6. **A case that changed after the approval cannot be decided on it.** Reviewing a screening
   disposition bumps the case version, which AgenticOrg registers with the issuer; the issuer
   supersedes the request and revokes the unconsumed grant with `case_changed`, and recording the
   decision is refused `409 case_changed` with the case still `awaiting_decision`.
7. **No decision grant reaches the browser.** The suite inspects every response the console is
   served for a `typ: "decision+jwt"` token and fails if one appears. The server fetches the grants
   from the issuer and consumes them itself.
8. **The issuer is named explicitly.** With the service on and no `GRANTEX_BASE_URL`, decision
   requests are refused (`decision_service_not_configured`): nothing is asked of an issuer nobody
   chose. Covered by `tests/unit/governed_cases/test_case_decision_requests.py`.

`AGENTICORG_CASE_DECISION_SERVICE` still defaults to off, in the development stack too. The local
stack serves decision grants only when a run sets `AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex`.

### Not yet proven

- **The grants are verified only at the issuer, not here as well.** This platform consumes them at
  the issuer, which verifies the signature and key, the audience and issuer, the action hash, the
  dwell source, the memo and policy hashes and the four-eyes structure under its own row locks, and
  refuses anything that does not match. That is fail-closed but single-sided; verifying them here
  too (decision-grant profile §6 steps 2 and 3) needs the Grantex Python SDK's `grantex.decisions`
  verifier once it is published. **Enabling this outside a development stack should wait for it.**
- **Only the development identity provider has been through the sign-in flow.** `tools/oidc_stub`
  implements discovery, PKCE, `nonce`, `max_age`, `acr_values`, `amr` and `auth_time`, but a real
  provider has not been tried, and neither has a rotation of the issuer's signing key while a grant
  is outstanding.
- **Expiry and cancellation have not been taken end to end** from the console: the issuer's own
  suites cover them, but the console's `expired` and `revoked` paths have only unit coverage here.
- **Nothing has been run at scale or against a hosted issuer.** The dwell floor, the rate limits and
  the 24-hour ceiling have only been exercised with the development defaults.

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
  `agenticorg_case_version_announcements_total{result}` (a rising `failed` count means the issuer
  is not superseding stale requests, which AgenticOrg still refuses locally),
  `agenticorg_case_decision_grants_consumed_total{outcome,result}`,
  `agenticorg_case_console_dwell_seconds{stage}`.

The issuer keeps its own audit chain of every sign-in, approval, consumption and refusal.

## Implementation notes

`core/cases/decision_requests.py` holds the service interface, the Grantex client and the verifier
that plugs into `CaseRuntime.decision_verifier`. The endpoint shapes were written against the
published decision-grant API before either side had talked to the other; they are now exercised
against the real issuer by `make e2e-decisions`, and the Grantex Python SDK's `decisions` client
(grantex 0.6) will replace the hand-written request building once it is released. Unit tests run
against `core/test_doubles/fake_decision_grants.py`, which enforces the same rules — four eyes, the
same approver refused, single-use grants, action and case-version binding.

One shape the first real run corrected: the issuer's consumption answer lists the approvers and the
grant ids it spent, and names the grant on each approver only in its audit entry, not in the
response. The two arrays cannot be paired by position — `jtis` is in the order the grants were
presented, `approvers` is in approval order — so for a four-eyes decision they can disagree, and a
client pairing them by index would record one person's approval against the other's credential.
When the answer does not name the grant on each approver, the pairing is taken from the issuer's
own record of the request (`GET /v1/decisions/requests/{id}`, whose `approvals[]` state the grant
and the approver together): each approver must match exactly one approval, and the grants that
resolves to must be exactly the ones the issuer said it consumed. Anything else is refused, because
an approver recorded against the wrong credential is worse than a decision not recorded. Grantex
will also return `approvers[].jti`
([grantex#1339](https://github.com/mishrasanjeev/grantex/pull/1339)); this prefers it when it is
there, which removes the second call.
