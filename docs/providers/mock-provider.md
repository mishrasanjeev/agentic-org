# The mock verification provider

`mock` is the verification provider that ships in this repository. It is
backed by synthetic fixtures, needs no account or credentials and makes no
external network calls, so the provider seam, the reference workflow and the
demo run anywhere. It implements every capability of
`VerificationProvider` (see `docs/adr/0009-provider-seam.md`) and was built
from the domain, not from any real data source's responses.

It is for development, tests and demos only. **`AGENTICORG_ENV` must be set
explicitly** to `local`, `dev`, `development`, `test` or `ci`. The application
settings default an unset `AGENTICORG_ENV` to development, but the mock does
not: when the variable is unset or names any other environment, its service
refuses to start, `ProviderRegistry.names()` does not list `mock`, and
`ProviderRegistry.create("mock")` fails with `unavailable`.
`docker-compose.dev.yml` and the test suite set it for you.

## Two ways to run it

| | Where | When |
|---|---|---|
| In-process | `connectors.providers.mock.MockProvider` | unit tests, notebooks |
| Over HTTP | service `connectors/providers/mock/service.py`, client `connectors.providers.mock.MockHttpProvider` | the dev stack, integration tests |

`ProviderRegistry.create("mock")` returns the HTTP client when
`AGENTICORG_MOCK_PROVIDER_URL` is set and the in-process provider otherwise.
`make dev` starts the service as `mock-provider` (host port
`AGENTICORG_DEV_MOCK_PROVIDER_PORT`, default 8081) and points the API and
worker at it, so the seam is exercised over the network.

Run the service on its own with
`AGENTICORG_ENV=development uvicorn connectors.providers.mock.service:app --port 8080`.
It needs no database, cache or application secret; the compose service sets only
`AGENTICORG_ENV`, the seed and the admin switch.

## Fixtures

`connectors/providers/mock/fixtures/` holds a dozen businesses, a watchlist
and recorded webhook deliveries. Names are invented, identifiers are in
reserved ranges (UK company numbers `0000000N`, US EIN `00-000000N`, state file
numbers `00000NN`), addresses are `Example` streets and domains are under
`example.com`. Each business also carries a sample application — what an
applicant declared — for seeding cases.

| Fixture | Jurisdiction | Scenario |
|---|---|---|
| `us-clean-hollowbrook` | US-DE | clean |
| `us-clean-quillfeather` | US-DE | clean, with a corporate owner two levels up |
| `us-missing-owner-cinderpath` | US-TX | a declared owner is absent from the ownership graph |
| `us-undeclared-owner-larkspur` | US-NY | a 45% owner nobody declared |
| `us-false-positive-oakhollow` | US-OR | probable false-positive sanctions hit (one letter off, different date of birth) |
| `us-thin-file-brambleway` | US-WA | thin file: no registry match at all |
| `us-hostile-web-glintmoor` | US-AZ | website copy carrying hidden instructions to AI agents |
| `gb-clean-brightwater` | GB | clean |
| `gb-missing-owner-marlpit` | GB | a missing declared owner and a probable false-positive hit |
| `gb-true-match-corvane` | GB | true match on a person and on the business |
| `gb-dissolved-ashcombe` | GB | dissolved company with a `business.dissolved` event |
| `gb-adversarial-northgate` | GB | instructions in the company name and in a watchlist alias, on a true match |

Loading fails closed: a malformed fixture, a duplicate identifier or any file
the loader does not recognise stops the provider from being created. The
contract suite validates every ownership graph, screening result and sample
application against the domain schemas.

## Behaviour

- **Resolve** matches identifiers exactly (score 1.0) and names by a
  deterministic similarity; `jurisdiction="US"` matches `US-DE`. A query with
  neither a name nor an identifier, or a malformed reserved identifier, is
  `InvalidQuery`.
- **Verify** returns `Pending` for `polls_until_ready` polls (first `queued`,
  then `in_progress`), then a result that stays the same. Checks compare what
  was declared with the registry record.
- **Idempotency.** Repeating a start with the same `idempotency_key` returns the
  same handle or screening; reusing a key for a different request is
  `InvalidQuery`.
- **Screening** reports a hit when the name or an alias reaches a similarity of
  0.85, with the entry's dates of birth, nationalities and associated entities
  for the disposition to compare.
- **Web presence** returns page content only as `UntrustedText`, with a digest
  and an `excerpt_ref`.
- **Monitoring** pages alerts by offset; fixture events and emitted events are
  both included.
- **Unknown** references and handles are `NotFound`; a reference from another
  provider is `InvalidQuery`.

## Configuration

`MockConfig` in code, or `AGENTICORG_MOCK_PROVIDER_*` in the environment:

| Setting | Environment | Default | Meaning |
|---|---|---|---|
| `seed` | `..._SEED` | `0` | seeds latency and failure draws |
| `latency_ms` | `..._LATENCY_MS` | `0-0` | uniform latency range per call |
| `failure_rate` | `..._FAILURE_RATE` | `0` | probability a call fails as unavailable or rate limited |
| `polls_until_ready` | `..._POLLS_UNTIL_READY` | `1` | `Pending` polls before a verification completes |
| `capabilities` | `..._CAPABILITIES` | all | comma-separated subset, to exercise graceful degradation |
| `webhook_secret` | `..._WEBHOOK_SECRET` | a development placeholder | HMAC key for events |
| — | `..._URL` | empty | use the HTTP service at this URL |
| — | `..._ADMIN` | `false` | enable the service's `/v1/admin` endpoints (in `make dev`, set `AGENTICORG_DEV_MOCK_PROVIDER_ADMIN=true`) |

Latency and failures are drawn from the seed and the call itself, not from
call order, so the same calls fail the same way even when they interleave.

## Driving it from a test

`inject_fault` makes the next calls fail (`unavailable`, `rate_limited`,
`authentication_failed`, `response_invalid`), hang until the deadline (`hang`)
or answer late (`slow`). `emit_event` records an event and returns it as a
signed webhook delivery; a `business.dissolved` event also changes the
company's registry status. Over HTTP the same controls are
`POST /v1/admin/faults`, `/v1/admin/events` and `/v1/admin/reset`, and
`MockHttpProvider` has matching methods. They answer `404 not_found` unless
`AGENTICORG_MOCK_PROVIDER_ADMIN` is true; the dev stack leaves them off unless
started with `AGENTICORG_DEV_MOCK_PROVIDER_ADMIN=true`.

<!-- snippet: tests/unit/test_mock_verification_provider.py#mock-provider-example -->
```python
from connectors.framework.verification_provider import Capability, Deadline, ProviderEventType, ProviderUnavailable
from connectors.providers.mock import FaultKind, MockConfig, MockProvider

mock = MockProvider(MockConfig(seed=7, latency_ms=(0, 20), polls_until_ready=1))
ref = mock.ref_for("gb-dissolved-ashcombe")

mock.inject_fault(FaultKind.UNAVAILABLE, capability=Capability.OWNERSHIP)
with pytest.raises(ProviderUnavailable):
    await mock.ownership(ref, deadline=Deadline.after(5))
graph = await mock.ownership(ref, deadline=Deadline.after(5))  # the fault fired once

headers, body = mock.emit_event(ref, ProviderEventType.BUSINESS_DISSOLVED)
assert mock.verify_webhook(headers, body) is not None
assert mock.verify_webhook(headers, body.replace(b"00000004", b"00000001")) is None
```

## Webhooks

A delivery has `X-Mock-Event-Id`, `X-Mock-Timestamp` (Unix seconds) and
`X-Mock-Signature: v1=<hex HMAC-SHA256 over "<timestamp>." + body>`.
`verify_webhook` returns `None` — never raises — for a missing or malformed
header, a timestamp more than five minutes away, a signature that does not
match, a body that is not a valid event, or an event id that differs between
header and body. Header names are case-insensitive. Replay of a genuine
delivery within the window is for the caller to detect by `event_id`.

`fixtures/webhooks/` records a genuine `business.dissolved` delivery and forged
ones: a wrong key, a forged payload that reuses the genuine signature for a
different company, a stale timestamp, a missing signature and a mismatched event
id. Tests verify each at its recorded time.

## HTTP API

| Method and path | Body | Returns |
|---|---|---|
| `POST /v1/businesses/resolve` | `{"query"}` | `{"candidates": [...]}` |
| `POST /v1/verifications` | `{"ref", "opts"}` | verification handle |
| `POST /v1/verifications/result` | `{"handle"}` | `{"status": "pending", "pending"}` or `{"status": "complete", "verification"}` |
| `POST /v1/ownership` | `{"ref"}` | ownership graph |
| `POST /v1/screenings/person`, `/v1/screenings/business` | `{"subject", "opts"}` | screening result |
| `POST /v1/web-presence` | `{"ref"}` | web presence |
| `POST /v1/monitors` | `{"ref", "opts"}` | monitor handle |
| `POST /v1/monitors/alerts` | `{"handle"}` | `{"alerts": [...]}` |
| `GET /healthz`, `GET /v1/capabilities` | — | liveness, declared capabilities |

Every call may send `X-Request-Deadline-Ms` (1–120000, default 30000). Errors
are `{"error": {"reason", "message", "capability", "retry_after_seconds"}}`
with status 400 `invalid_query`, 401 `provider_authentication_failed`,
404 `not_found`, 429 `provider_rate_limited` (with `Retry-After`),
501 `capability_not_supported`, 502 `provider_response_invalid`,
503 `provider_unavailable` or 504 `provider_timeout`. An invalid body is 400.
The client raises the matching error; a transport failure is
`ProviderUnavailable`, a local timeout `ProviderTimeout`, and any body that is
not a valid domain value `ProviderResponseInvalid`, as are an undecodable body and a redirect loop;
any other request error is `ProviderUnavailable`. Page content is sent by the
service with `INCLUDE_UNTRUSTED_TEXT` and re-wrapped by the client as
`UntrustedText`, which redacts it again on any later serialisation.
