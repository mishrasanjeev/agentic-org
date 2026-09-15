# ADR 0009: One provider-neutral interface for verification data

- **Status**: Accepted
- **Date**: 2026-09-15
- **Deciders**: Sanjeev, Engineering team

## Context

The business-onboarding and screening agents need registry, ownership,
screening, web-presence and monitoring data. That data comes from providers we
do not build and will not ship: national company registries, commercial data
aggregators, screening list services, an operator's in-house systems. Each
answers with its own shapes, its own identifiers and its own failure modes.

If the agents, the policy engine or the evidence package read a provider's
responses directly, every one of them is rewritten for every new source, a
policy rule keyed on one source's status string silently stops firing on
another, and nobody can run the workflow without that provider's account.

Three things about these sources shaped the decision:

- **They are asynchronous.** Verification and ownership resolution can take
  seconds to minutes; long synchronous calls time out.
- **They differ in coverage.** A public registry has no screening data; a
  screening service has no registry. "Not offered" is normal, not an error.
- **Their pushes cannot be trusted by default.** A webhook is only as good as
  its signature, and even a signed one says "something changed", not what the
  truth now is.

Options considered:

- **Per-provider connectors behind the existing `BaseConnector` tool
  interface.** Tools return free-form dictionaries; each agent prompt and each
  policy would have to know each provider's shape.
- **A lowest-common-denominator dictionary contract.** Easy to implement,
  impossible to validate, and it pushes interpretation into the model.
- **A typed domain interface with a declared capability set.** More upfront
  design, but the agents, policies and evidence format are written once.

## Decision

`connectors/framework/verification_provider.py` defines `VerificationProvider`
with the method set in PRD A-1, typed domain values
(`connectors/framework/verification_types.py`) and a closed error taxonomy.

- **Capabilities, not `NotImplementedError`.** A provider declares
  `capabilities: frozenset[Capability]`. Every method's default raises
  `CapabilityNotSupported`, a `ProviderError` carrying the capability.
  Callers go through `call_capability`, which never invokes an undeclared
  capability and returns `NotAvailable`; the workflow marks the section
  `not_available` (the memo schema allows exactly that, with a reason and no
  findings).
- **Start and poll.** `verify_business` returns a handle and
  `verification_result` returns `Pending` as an ordinary value until the
  result is ready. A synchronous source fits by returning the result on the
  first poll.
- **Deadlines and cancellation.** Every I/O method takes a keyword-only
  `deadline: Deadline` (an absolute monotonic instant shared by nested calls
  and retries). `Deadline.enforce` turns an overrun into `ProviderTimeout`;
  external cancellation propagates as `CancelledError`. `verify_webhook` is
  synchronous, must do no I/O and therefore takes no deadline.
- **Error taxonomy.** `CapabilityNotSupported`, `ProviderTimeout`,
  `ProviderUnavailable`, `ProviderRateLimited` (with `retry_after_seconds`),
  `InvalidQuery`, `NotFound`, `ProviderAuthenticationFailed` and
  `ProviderResponseInvalid`, each with a stable `reason` and a `retryable`
  flag. Nothing else may escape a provider.
- **Webhooks fail closed.** `verify_webhook(headers, body)` returns a
  `ProviderEvent` only when authenticity is proven and `None` otherwise, never
  raising. The event bridge treats a verified event as a trigger to re-query
  the provider, and handles replay by `event_id`.
- **Idempotent starts.** `VerifyOptions`, `ScreenOptions` and `MonitorOptions`
  require an `idempotency_key`, so a retried start does not start (and pay for)
  a second job.
- **Paging by offset.** `BusinessQuery` and `MonitorHandle` carry `offset` and
  `limit`; a short page is the last one. Opaque cursors were rejected because
  a `list[...]` return has nowhere to carry the next cursor.
- **Untrusted text is typed.** Website copy is `UntrustedText`, whose `str()`
  and `repr()` never show the content, so it cannot slip into a prompt through
  formatting.
- **Registry and plugins.** `connectors/providers/registry.py` holds providers
  by name. Native providers register on import; plugin packages add
  `VerificationProvider` subclasses through the `agenticorg.providers`
  entry-point group, loaded after natives, never replacing one.
- **Shapes match the published schemas.** `OwnershipGraph` and
  `ScreeningResult` serialise to the `ownership_graph` and `screening_result`
  schemas and `Evidence` to the shared evidence definition; a contract test
  round-trips the schema fixtures and compares vocabularies.

The mock provider is built from the domain, not from any provider's responses,
and the conformance suite (`agenticorg.testing.provider_conformance`) checks
any implementation against the behaviour above.

## Second implementation sketch

Before freezing the interface we mapped it onto a second, very different
source: a public government company registry, described generically. Such a
registry typically offers a name/number search, a company profile, an officers
list, and a list of persons with control whose control is reported in bands
(25–50%, 50–75%, 75–100%). It has no screening, web or monitoring-by-webhook
data, and its lookups are synchronous.

The sketch is executable: `tests/unit/test_provider_seam_registry_sketch.py`
implements `PublicRegistrySketch` over invented registry payloads, validates
its ownership graph against the published schema, and checks that the
capabilities it does not offer degrade.

| Interface field | Registry sketch | Mock provider (domain-built) |
|---|---|---|
| `BusinessCandidate.ref.provider_ref`, `identifiers` | registration number | fixture business key and reserved identifiers |
| `legal_name`, `registered_address` | profile name and office address | fixture record |
| `registry_status` | profile status mapped to the five normalised values | fixture record |
| `match_score` | `None` — results are ranked but unscored (optional) | deterministic name similarity |
| `BusinessVerification.entity_type`, `incorporated_on`, `dissolved_on` | profile kind and dates | fixture record |
| `officers[].role`, `appointed_on`, `date_of_birth` (partial), `nationalities` | officers list (birth month/year only) | fixture officers |
| `checks[]` | registration check from profile status | registration, name, address, identifiers |
| `OwnershipNode.kind` | individual / corporate controller | fixture owners, including corporate owners |
| `OwnershipEdge.share_pct`, `voting_pct` | control bands as ranges | fixture percentages as ranges |
| `OwnershipGraph.completeness` | `partial` when a corporate controller's own owners need a further lookup | per fixture |
| `Evidence.record_id`, `field` | registry resource and field | fixture record and field |
| `Pending` | never returned — first poll has the result | returned for a configurable number of polls |
| `ScreeningResult`, `WebPresence`, `MonitorAlert` | not offered: `CapabilityNotSupported` | fixture lists, pages and events |

Every field is either populated from both kinds of source or optional in the
domain because one kind of source legitimately cannot supply it (a score, a
full date of birth, a pending state). No field exists because one source
happens to return it; in particular there is no raw status string, no
source-specific control code and no provider score other than the optional,
normalised `match_score` and `name_similarity`.

## Consequences

- Agents, policies and the evidence package are written against one set of
  types; adding a provider is a package with an entry point and no change to
  this repository.
- A provider implementer must normalise: map statuses onto `RegistryStatus`,
  bands onto `PercentageRange`, roles onto `OfficerRole`. Anything that does
  not map is dropped or `unknown`, never passed through as free text.
- Enumerations are closed. Adding a value (for example a new registry status)
  is a schema minor version and a change to this interface, reviewed as a
  domain concept.
- Offset paging over a source that only offers cursors costs the provider a
  cursor walk; accepted for the small result sets involved.
- Monitoring through a registry's change stream, rather than a webhook, would
  need an adapter that polls and emits `MonitorAlert`s; the interface does not
  preclude it but nothing ships for it in this release.
