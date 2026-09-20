# Case hand-off: signed push, REST retrieval and provider webhooks

A governed case lands in whatever system of record the operator already uses. The approvals console
is one destination; a **portable push** is the other: an HMAC-signed webhook backed by an outbox,
plus REST retrieval of the same document. Every delivery body is a `case_push` document
(`schemas/case_push.schema.json`): the `business_case`, the `underwriting_memo`, screening results
and dispositions, and the ownership graph.

Everything here needs the tenant's `governed_cases.enabled` flag, and pushes start only once a
tenant admin configures an endpoint. Code: `core/cases/push.py`, `core/cases/provider_webhooks.py`,
`api/v1/case_push.py`. Migration: `v6z26_case_push`.

## When a push is sent

| Case change | `event_type` |
|---|---|
| reaches `awaiting_decision` for the first time (memo ready) | `case.completed` |
| re-investigated back to `awaiting_decision`, dispositions proposed, a disposition reviewed, an information request proposed or approved, `withdrawn`, `failed` | `case.updated` |
| `decided` | `case.decided` |

The event is written to `case_push_outbox` **in the same database transaction** as the change. If
the push cannot be delivered - receiver down, rejected, or even a payload that fails schema
validation - the case change still commits and the event waits in the outbox (or dead-letters),
so a push failure never loses a case.

The event id is derived from the case, its version and the event type, so it is stable: retries
and replays of one event always carry the same id, and `GET …/push-payload` for that case version
returns the same id. **Receivers must treat `event_id` as the idempotency key.**

## Delivery

A worker is asked to deliver as soon as the change commits (`dispatch_case_pushes`), so a memo
normally reaches the receiver within seconds; the `sweep_case_pushes` beat task (every 30 s, enable
with `AGENTICORG_CASE_PUSH_SWEEP_ENABLED=true`) covers retries, a missed kick and a crashed worker.
The integration tests hold delivery to **60 seconds of case completion**, including after transient
failures.

| Receiver answer | What happens |
|---|---|
| 2xx | `delivered` |
| 408, 425, 429, 5xx, timeout, connection error | retried: 2 s doubling up to 15 minutes, ±20% deterministic jitter, 10 attempts |
| any other 4xx | `dead_lettered` with `endpoint_rejected:http_<status>` |
| 10th retryable failure | `dead_lettered` with `max_attempts_exceeded:<error>` |

Deliveries use no redirects and a 10-second timeout. In strict runtimes the endpoint must be HTTPS
to a public host, and connections are pinned to the validated DNS answer.

Dead letters are listed at `GET /api/v1/case-push/dead-letters` and replayed with
`POST /api/v1/case-push/dead-letters/{outbox_id}/replay`; see the runbook entry
[Governed case push: dead letters and replay](../RUNBOOKS.md#governed-case-push-dead-letters-and-replay).

## Signature

Every delivery carries:

```text
AgenticOrg-Event-Id: 3b1f…            the event id (UUID)
AgenticOrg-Event-Type: case.completed
AgenticOrg-Timestamp: 1788000000      Unix seconds when signed
AgenticOrg-Signature: v1=k_2c9e…:5f0a…, v1=k_81d4…:9b77…
Content-Type: application/json
```

Each `v1=<key_id>:<signature>` is the lower-case hex HMAC-SHA256, keyed by that signing key's secret,
of the bytes `"<event id>.<timestamp>."` followed by the raw request body. There is one entry per key
on the endpoint, the active key first.

A receiver verifies like this (`core.cases.push.verify_signature` is a reference implementation):

<!-- snippet: tests/unit/governed_cases/test_case_push_signing_units.py#verify-case-push -->
```python
from core.cases.push import verify_signature

reason = verify_signature(request_headers, raw_body, keys={"k_active": secret}, now=int(time.time()))
# reason is "" or signature_missing, timestamp_invalid, timestamp_outside_tolerance, signature_mismatch
accepted = reason == ""
```

Receivers should: compare in constant time; reject timestamps more than 5 minutes from their clock;
accept any `v1` entry whose key id they hold; and de-duplicate on the event id.

## Endpoint and key rotation

All tenant-admin only:

| Method and path | What it does |
|---|---|
| `GET /api/v1/case-push/endpoint` | URL, enabled, active key id and key ids. Never secrets. |
| `PUT /api/v1/case-push/endpoint` | `{url, enabled}`. Creating the endpoint generates the first key and returns its secret **once**. |
| `POST /api/v1/case-push/endpoint/rotate-key` | Adds a new active key and returns its secret once; the previous key keeps signing (at most two keys). |
| `POST /api/v1/case-push/endpoint/retire-previous-keys` | Keeps only the active key. |

Rotation without downtime: rotate, give the new secret to the receiver, let it accept either key,
then retire the previous key. Secrets are stored encrypted with the tenant's key
(`case_push_endpoints.signing_keys_encrypted`).

## REST retrieval

| Method and path | What it does |
|---|---|
| `GET /api/v1/governed-cases/{case_ref}/push-payload` | The `case_push` document for the case as it is now. |
| `GET /api/v1/governed-cases/{case_ref}/push-deliveries` | Every outbox event for the case: status, attempts, last error, payload SHA-256. |

## Inbound provider webhooks

Providers post events (for example `business.dissolved`) to
`POST /api/v1/webhooks/providers/{tenant_id}/{provider}/{path_token}`. The route carries no session -
the provider signs - and always answers 202, so it tells a caller neither which attempts were close
nor whether the tenant has governed cases enabled. The body is read under a 256 KiB cap (refused
with 413) before any database work.

The path token binds an inbox to one tenant: it is derived from the application secret key, and a
delivery whose token does not match is counted (`unbound`) and dropped before anything is read or
written, so an event signed with a provider's shared secret cannot be replayed at another tenant's
inbox. An active human administrator of the tenant reads the path to configure with the provider from
`GET /api/v1/case-push/provider-webhook-inbox?provider={provider}` (an API key or agent token with
the admin scope is refused); treat it as a credential, and note that rotating
`AGENTICORG_SECRET_KEY` changes every tenant's inbox path. Moving an already-configured provider
onto the path is an ordered procedure - see
[Moving a provider to the per-tenant inbox path](../RUNBOOKS.md#moving-a-provider-to-the-per-tenant-inbox-path-one-off-required).

`VerificationProvider.verify_webhook` decides authenticity, and a webhook **never changes a case**:

| Delivery | Recorded as | Effect |
|---|---|---|
| verified, new event id | `accepted` | up to 25 cases of the tenant awaiting a decision on the event's subject are re-investigated: all data is re-read from the provider through the tool gateway, producing a new memo and a `case.updated` push. The `in_progress` transition records the event (`provider_event:<type>:<event id>`), and if the provider cannot be reached the case returns to `awaiting_decision` with its existing memo and the reason `re_evaluation_failed:<reason>` |
| verified, event id already accepted | `duplicate` | nothing |
| forged, tampered, stale or unsigned | `unverified` | recorded and counted, nothing else: nothing is read out of the body, no case is looked up and no investigation runs |
| wrong or missing path token | `unbound` (counter only) | nothing; no database access at all |

`provider_webhook_receipts` records each delivery's outcome, event id (verified only), body SHA-256
and how many cases it re-queried; the body is never stored.
See [Provider webhooks: verification failures and replays](../RUNBOOKS.md#provider-webhooks-verification-failures-and-replays).

## Metrics and alerts

| Metric | Labels |
|---|---|
| `agenticorg_case_push_enqueued_total` | `event_type` |
| `agenticorg_case_push_deliveries_total` | `outcome` (`delivered`, `retry_scheduled`, `dead_lettered`) |
| `agenticorg_case_push_dead_letters_total` | `reason` (`endpoint_rejected`, `max_attempts_exceeded`, `payload_invalid`) |
| `agenticorg_case_push_dead_letter_backlog` | none (gauge, set by the sweep) |
| `agenticorg_case_push_attempt_duration_seconds` | none |
| `agenticorg_provider_webhook_receipts_total` | `outcome` (`accepted`, `duplicate`, `unverified`, `unbound`) |

Alert `case_push_dead_letters_present` (`observability/alerting.py`) fires while the dead-letter
backlog is above zero. Suggested Prometheus rules: dead-letter growth
`increase(agenticorg_case_push_dead_letters_total[15m]) > 0`; webhook verification failures
`rate(agenticorg_provider_webhook_receipts_total{outcome="unverified"}[5m]) > 0.1`.
