# Writing a verification provider

A verification provider connects AgenticOrg to a source of business
verification, ownership, screening, web-presence or monitoring data: a
national registry, a data service, or a system your organisation already runs.
You implement one interface; the reference agents, the policy engine and the
evidence package then work with your data unchanged. The provider ships as its
own package, found through an entry point, with no change to this repository.

This guide covers the interface, the capability model, the conformance suite
your provider must pass, and packaging. The in-repository `mock` provider is
the worked example throughout. The design is recorded in
`docs/adr/0009-provider-seam.md`. Examples use `acme_kyb` as a stand-in name.

## The interface

Everything is importable from `connectors.framework.verification_provider`.

| Method | Capability | Returns |
|---|---|---|
| `resolve_business(q, *, deadline)` | `RESOLVE` | `list[BusinessCandidate]`, best match first |
| `verify_business(ref, opts, *, deadline)` | `VERIFY` | `VerificationHandle` |
| `verification_result(h, *, deadline)` | `VERIFY` | `BusinessVerification` or `Pending` |
| `ownership(ref, *, deadline)` | `OWNERSHIP` | `OwnershipGraph` |
| `screen_person(s, opts, *, deadline)` | `SCREEN_PERSON` | `ScreeningResult` |
| `screen_business(s, opts, *, deadline)` | `SCREEN_BUSINESS` | `ScreeningResult` |
| `web_presence(ref, *, deadline)` | `WEB_PRESENCE` | `WebPresence` |
| `monitor_enroll(ref, opts, *, deadline)` | `MONITOR` | `MonitorHandle` |
| `monitor_result(h, *, deadline)` | `MONITOR` | `list[MonitorAlert]` |
| `verify_webhook(headers, body)` | — | `ProviderEvent` or `None` |

A provider class sets `name` (lower-case letters, digits and underscores) and
`capabilities`, a `frozenset` of `Capability`. The values it returns are frozen
Pydantic models that reject unknown fields; `OwnershipGraph` and
`ScreeningResult` serialise to the published `ownership_graph` and
`screening_result` schemas (`docs/schemas/domain-schemas.md`).

### Capabilities and graceful degradation

Declare only what you offer. Every method's default implementation raises
`CapabilityNotSupported`, so a provider that offers resolution and verification
overrides just those methods — never raise `NotImplementedError`. If your
capability set can vary per instance (for example by licence), call
`self.require(Capability.X)` at the top of each method.

Callers never invoke an undeclared capability: they go through
`call_capability`, which returns `NotAvailable`, and the workflow marks the
memo section `not_available`. A provider declaring only `RESOLVE` and `VERIFY`
therefore runs the reference workflow with ownership and screening sections
marked `not_available` rather than failing:

<!-- snippet: tests/unit/test_verification_provider_interface.py#graceful-degradation -->
```python
candidates = await call_capability(
    provider,
    Capability.RESOLVE,
    lambda: provider.resolve_business(BusinessQuery(name="Brightwater"), deadline=deadline),
)
ownership = await call_capability(
    provider, Capability.OWNERSHIP, lambda: provider.ownership(REF, deadline=deadline)
)
screening = await call_capability(
    provider, Capability.SCREEN_PERSON, lambda: provider.screen_person(person, opts, deadline=deadline)
)

assert isinstance(candidates, list) and candidates[0].ref == REF
assert ownership == NotAvailable(Capability.OWNERSHIP)
assert screening == NotAvailable(Capability.SCREEN_PERSON)
```

### Implementing a method

Here is part of an implementation against a public company registry (the full
sketch is `tests/unit/test_provider_seam_registry_sketch.py`):

<!-- snippet: tests/unit/test_provider_seam_registry_sketch.py#provider-skeleton -->
```python
class PublicRegistrySketch(VerificationProvider):
    """Registry data only: no screening, no web presence. Lookups are synchronous at the source."""

    name = "public_registry"
    capabilities = frozenset({Capability.RESOLVE, Capability.VERIFY, Capability.OWNERSHIP})

    def _ref(self, number: str) -> BusinessRef:
        return BusinessRef(
            provider=self.name,
            provider_ref=number,
            jurisdiction="GB",
            identifiers=(Identifier(scheme="gb_company_number", value=number),),
        )

    def _profile(self, ref: BusinessRef) -> dict[str, Any]:
        record = REGISTRY.get(ref.provider_ref)
        if record is None:
            raise NotFound(self.name, capability=Capability.VERIFY)
        return record

    def _address(self, raw: dict[str, str]) -> Address:
        return Address(lines=(raw["line_1"],), locality=raw.get("town"), postal_code=raw.get("postcode"), country="GB")

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        if not q.has_search_terms:
            raise InvalidQuery(self.name, "a name or an identifier is required", capability=Capability.RESOLVE)
        async with deadline.enforce(self.name, Capability.RESOLVE):
            wanted = {i.value for i in q.identifiers if i.scheme == "gb_company_number"}
            name = (q.name or "").upper()
            hits = [
                record["profile"]
                for number, record in sorted(REGISTRY.items())
                if number in wanted or (name and name in record["profile"]["entity_name"])
            ]
        return [
            BusinessCandidate(
                ref=self._ref(p["registration_number"]),
                legal_name=p["entity_name"],
                registry_status=_STATUS.get(p["status"], RegistryStatus.UNKNOWN),
                registered_address=self._address(p["office_address"]),
                match_score=None,  # the registry ranks results but publishes no score
                evidence=(_evidence(f"profile:{p['registration_number']}", "entity_name"),),
            )
            for p in hits[q.offset : q.offset + q.limit]
        ]
```

The rules every method follows:

- **Deadlines.** Every I/O method receives a `Deadline`, an absolute instant
  shared by everything done for one unit of work. Wrap network calls in
  `async with deadline.enforce(self.name, capability)`, and pass
  `deadline.remaining()` to your HTTP client's timeout. An expired deadline is
  `ProviderTimeout`, before any work starts.
- **Cancellation.** Let `asyncio.CancelledError` propagate. Never catch it to
  return a value or convert it into another error.
- **Start and poll.** `verify_business` starts work and returns a handle;
  `verification_result` returns `Pending` — a value, not an exception — until
  the result is ready, and the same result on every later poll. A source that
  answers synchronously simply returns the result on the first poll.
- **Idempotency.** `VerifyOptions`, `ScreenOptions` and `MonitorOptions` carry
  an `idempotency_key`. Repeating a call with the same key must return the same
  handle or result, not start and bill a second job. Pass the key to your
  source if it supports one.
- **Paging.** `BusinessQuery` and `MonitorHandle` page by `offset` and `limit`;
  return at most `limit` items, ordered the same way every time. A short page
  is the last.
- **Errors.** Raise only these, each with your provider's name:

  | Error | When |
  |---|---|
  | `InvalidQuery` | the request cannot be answered as asked (no name or identifier, a malformed identifier, a reference from another provider) |
  | `NotFound` | the business, verification or monitor does not exist |
  | `ProviderTimeout` | the deadline passed |
  | `ProviderUnavailable` | the source could not be reached or failed transiently |
  | `ProviderRateLimited` | the source refused for rate or quota reasons; set `retry_after_seconds` when known |
  | `ProviderAuthenticationFailed` | the source rejected your credentials |
  | `ProviderResponseInvalid` | the source answered with something you cannot map; never return part of it |
  | `CapabilityNotSupported` | the capability is not offered |

  Messages reach logs: never put personal data, credentials or raw responses
  in them.
- **Normalise.** Map the source's statuses onto `RegistryStatus`, officer roles
  onto `OfficerRole`, ownership bands onto `PercentageRange` (an exact 30% is
  `min=30, max=30`), dates you only know to the month onto partial dates such
  as `"1971-04"`. Anything that does not map becomes `unknown` or is left out;
  it is never passed through as free text.
- **Cite everything.** Every candidate, officer, owner, hit and page carries
  `Evidence` naming your provider, the source record id, the field, and when it
  was retrieved.
- **Untrusted content.** Website copy and similar attacker-controlled text is
  returned only as `UntrustedText`, which never renders its content through
  `str()` or `repr()`.
- **Webhooks.** `verify_webhook` is synchronous and does no I/O. Return a
  `ProviderEvent` only when the payload's authenticity is proven — signature
  over the exact body bytes, constant-time comparison, a bounded timestamp
  window, header names compared case-insensitively — and `None` for anything
  else. It must never raise. A verified event is still only a trigger: the
  platform re-queries you rather than trusting its body, and detects replays by
  `event_id`.
- **Credentials.** Read them from the environment or your secret manager when
  the provider is constructed. The registry constructs a plugin by calling its
  class with no arguments; a constructor that raises is reported as
  `construction_failed` and the provider is not used.

## The conformance suite

`agenticorg.testing.provider_conformance` checks an implementation against
everything above. Run it in your provider package's own test suite.

| Check | Verifies | Needs |
|---|---|---|
| `identity` | `name` and `capabilities` are well formed | — |
| `capability_honesty` | every declared capability answers for the target's inputs; every undeclared one raises `CapabilityNotSupported` (never `NotImplementedError`) | — |
| `pending_then_result` | polling returns `Pending` values, then a result that never changes or reverts | `VERIFY` |
| `deadline_expired` | an already-expired deadline is `ProviderTimeout`, promptly, for every capability and for `verification_result` and `monitor_result` polls | — |
| `deadline_overrun` | a call that would run past a short deadline stops with `ProviderTimeout` | a fault injector (`hang`) |
| `cancellation` | cancelling a call in flight raises `CancelledError` promptly and leaves the provider usable | a fault injector (`slow`) |
| `error_taxonomy` | unknown references and handles are `NotFound`, an empty query is `InvalidQuery`, transient failures are retryable, nothing outside the taxonomy escapes | a fault injector for the transient cases |
| `webhook_verification` | genuine deliveries verify (with any header-name case); forged, tampered, header-less, body-less and malformed deliveries are rejected without raising | genuine and forged samples |
| `webhook_replay_protection` | a correctly signed delivery outside the accepted time window is rejected; verifying the same delivery twice yields the same `event_id`, so the caller can reject the replay | genuine and stale samples |
| `pagination` | paging candidates and monitor alerts with `limit=1` reproduces the unpaged result in order; an offset past the end is empty | a query with at least two candidates; for `MONITOR`, an alert preparer |
| `idempotency` | a repeated start or screening with the same key returns exactly the same value; a repeated unkeyed read returns the same answer apart from when records were read (`retrieved_at`, `as_of`, `observed_at`, `fetched_at`) | — |
| `schema_conformance` | outputs validate against the published schemas and cite only your provider's records | — |

A failure names the check, the provider and what to fix, for example
`[capability_honesty] provider 'acme_kyb': ownership raised NotImplementedError;
a capability that is not offered must raise CapabilityNotSupported`. A check the
target cannot exercise is skipped with the reason — unless the target sets
`strict=True`, which turns every skip into a failure. The mock runs strictly;
run your provider strictly before you publish it.

### Running it

Subclass `ProviderConformanceSuite` with a name pytest collects and provide a
`conformance_target` fixture. The tests are synchronous — each check runs in
its own event loop — so no async pytest plugin is needed. This is how the mock
provider runs it, from outside the repository:

<!-- snippet: tests/contract/provider_example/conformance_example.py#conformance-imports -->
```python
import json
import time
from collections.abc import Iterator

import pytest
from agenticorg.testing.provider_conformance import ConformanceTarget, ProviderConformanceSuite, WebhookSample

from connectors.framework.verification_provider import (
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    Capability,
    Deadline,
    Identifier,
    MonitorHandle,
    PersonSubject,
    ProviderEventType,
    VerificationProvider,
)
from connectors.providers.mock import FaultKind, MockConfig, MockHttpProvider, MockProvider, webhooks
from connectors.providers.mock.service import serve_in_thread
```

<!-- snippet: tests/contract/provider_example/conformance_example.py#conformance-target -->
```python
KNOWN = BusinessRef(
    provider="mock",
    provider_ref="mock-gb-00000001",
    jurisdiction="GB",
    identifiers=(Identifier(scheme="gb_company_number", value="00000001"),),
)


def genuine_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    # Ask the provider for a delivery it signed itself. A provider without a way to produce one
    # records genuine deliveries as fixtures and sets its clock to when they were signed.
    assert isinstance(provider, MockProvider)
    headers, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    return [WebhookSample(headers=headers, body=body, description="a signed business.dissolved event")]


def forged_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    assert isinstance(provider, MockProvider)
    headers, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    forged_payload = body.replace(b"00000001", b"00000002")
    return [WebhookSample(headers=headers, body=forged_payload, description="a genuine signature on another company")]


def stale_webhooks(provider: VerificationProvider) -> list[WebhookSample]:
    # A genuine event re-signed two hours ago: the signature is valid, the delivery is a replay.
    assert isinstance(provider, MockProvider)
    _, body = provider.emit_event(KNOWN, ProviderEventType.BUSINESS_DISSOLVED)
    event_id = json.loads(body)["event_id"]
    two_hours_ago = int(time.time()) - 7200
    headers = webhooks.sign(provider.config.webhook_secret, body, event_id=event_id, timestamp=two_hours_ago)
    return [WebhookSample(headers=headers, body=body, description="a correctly signed delivery from two hours ago")]


def prepare_monitor_alerts(provider: VerificationProvider, handle: MonitorHandle) -> None:
    assert isinstance(provider, MockProvider)
    for _ in range(3):
        provider.emit_event(handle.ref, ProviderEventType.OFFICERS_CHANGED)


def inject_fault(provider: VerificationProvider, kind: str, capability: Capability | None, delay: float) -> None:
    assert isinstance(provider, MockProvider)
    provider.inject_fault(FaultKind(kind), capability=capability, delay_seconds=delay)


def mock_target() -> ConformanceTarget:
    return ConformanceTarget(
        factory=lambda: MockProvider(MockConfig(polls_until_ready=2)),
        known_business=KNOWN,
        resolvable_query=BusinessQuery(
            identifiers=tuple(Identifier(scheme="gb_company_number", value=f"0000000{n}") for n in (1, 2, 3))
        ),
        unknown_business=KNOWN.model_copy(update={"provider_ref": "mock-gb-99999999"}),
        person=PersonSubject(full_name="Orla Venncastle", date_of_birth="1971-04"),
        business=BusinessSubject(legal_name="Brightwater Lantern Works Ltd", jurisdiction="GB"),
        genuine_webhooks=genuine_webhooks,
        forged_webhooks=forged_webhooks,
        stale_webhooks=stale_webhooks,
        fault_injector=inject_fault,
        prepare_monitor_alerts=prepare_monitor_alerts,
        expects_pending=True,
        strict=True,  # a skipped check fails
    )


class TestMockProviderConformance(ProviderConformanceSuite):
    @pytest.fixture
    def conformance_target(self) -> ConformanceTarget:
        return mock_target()
```

What the target provides:

- `factory` builds a fresh provider for each check, inside that check's event
  loop; `close` (optional) releases it.
- `known_business` must exist at your source, `unknown_business` must be well
  formed but absent, and `resolvable_query` must return at least two
  candidates. Use sandbox or test records your source provides for this.
- `genuine_webhooks` returns deliveries you must accept — produced by your
  source's sandbox, or recorded, with your provider's clock set to when they
  were signed. `forged_webhooks` returns deliveries you must reject; the suite
  adds its own tampered and malformed variants. `stale_webhooks` returns
  correctly signed deliveries from outside your accepted time window.
- `fault_injector` makes the next call hang, answer slowly, fail as unavailable
  or be rate limited. Wrap your HTTP transport in a test double to provide it;
  without it the checks that need it are skipped, and the suite says so.
- `prepare_monitor_alerts` receives a fresh monitor handle and makes at least
  two alerts exist on it (for example by triggering sandbox events), so alert
  pages can be checked.
- `strict=True` fails any check that would otherwise be skipped.
- `expects_pending` requires at least one `Pending` before the result, if your
  source is always asynchronous.

The same suite runs against the mock over HTTP as well as in-process
(`TestMockProviderOverHttpConformance` in the same file), and both pass in CI.

### Where the suite comes from

The suite lives in this repository at `testing/provider_conformance` and is
published inside the full AgenticOrg distribution — the wheel built from the
repository root, which also contains `connectors` and therefore the interface
itself — as `agenticorg.testing.provider_conformance`
(`[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`). The
lightweight SDK published to PyPI as `agenticorg` is built from `sdk/` and does
not include it, because it has no provider interface. Develop and test a
provider against a tagged AgenticOrg release of at least 4.8 installed from
source as a regular (not editable) install, for example
`pip install "agenticorg @ git+https://github.com/mishrasanjeev/agentic-org@<tag>"`. An editable install of AgenticOrg does not
expose the published name, because it resolves `agenticorg` to `sdk/agenticorg`;
inside a checkout of this repository, import `testing.provider_conformance`
instead. A contract test lays the files out as the wheel does and runs the
example above in a separate process, so the published import path cannot
silently break.

## Packaging and entry points

Declare the provider class in the `agenticorg.providers` entry-point group,
and require AgenticOrg 4.8 or later, the first release with the provider
interface:

<!-- snippet: tests/unit/test_provider_plugin_packaging.py#provider-pyproject -->
```toml
[project]
name = "acme-kyb-agenticorg"
version = "0.1.0"
dependencies = ["agenticorg>=4.8"]

[project.entry-points."agenticorg.providers"]
acme_kyb = "acme_kyb_agenticorg.provider:AcmeKybProvider"
```

The requirement names the full AgenticOrg distribution, not the lightweight SDK
published to PyPI under the same name (whose versions are below 4.8). Install
the full distribution from source first (see "Where the suite comes from");
pip then treats the requirement as satisfied when it installs your package.
Without it, resolution fails instead of silently installing the SDK.

Install the package next to AgenticOrg (the API image and every worker) and
turn loading on:

- `AGENTICORG_PLUGIN_LOADING=true`
- `AGENTICORG_PLUGIN_ALLOWLIST=acme-kyb-agenticorg`

At startup native providers register first, then allowlisted plugins. A plugin
that is not a `VerificationProvider` subclass with a valid `name` and
`capabilities` is rejected as `invalid_type`; one whose name is already taken
is rejected as `name_conflict`, so a plugin can never replace `mock` or any
other native provider. Every decision is logged and counted; see
`docs/providers/plugin-packages.md`.

## The mock provider as a worked example

`connectors/providers/mock` implements every capability and passes every check,
in-process and over HTTP. It is worth reading alongside this guide:

- `provider.py` shows deadline enforcement, cancellation-safe state changes,
  idempotency keys, offset paging, evidence on every value, and `UntrustedText`
  for website copy.
- `webhooks.py` shows fail-closed webhook verification.
- `http_client.py` shows mapping a remote service's errors and malformed
  responses onto the taxonomy, and sending the remaining deadline.
- Its `inject_fault` and `emit_event` are what the example target's fault
  injector and webhook samples call.

See `docs/providers/mock-provider.md` for its fixtures and configuration.

## Before you publish

- The conformance suite passes with `strict=True`: a fault injector, genuine,
  forged and stale webhook samples and an alert preparer, and no skipped checks.
- `capabilities` lists only what your contract with the source covers.
- No field in your output is filled from free text the source did not
  structure, and nothing unmappable is passed through.
- Credentials come from configuration, never from code, and never reach logs or
  error messages.
