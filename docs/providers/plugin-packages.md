# Shipping connectors, agents and providers as plugin packages

A connector, agent or verification provider can live in its own Python package and be picked up by
AgenticOrg at startup, with no change to this repository. AgenticOrg discovers
it through [entry points](https://packaging.python.org/en/latest/specifications/entry-points/).

## Declare entry points

In the plugin package's `pyproject.toml`:

```toml
[project]
name = "acme-kyb-agenticorg"

[project.entry-points."agenticorg.connectors"]
acme_kyb = "acme_kyb_agenticorg.connector:AcmeKybConnector"

[project.entry-points."agenticorg.agents"]
acme_reviewer = "acme_kyb_agenticorg.agents:AcmeReviewerAgent"

[project.entry-points."agenticorg.providers"]
acme_kyb = "acme_kyb_agenticorg.provider:AcmeKybProvider"
```

| Group | Must point at | Registered into |
|---|---|---|
| `agenticorg.connectors` | a `BaseConnector` subclass with a non-empty `name` | `ConnectorRegistry` |
| `agenticorg.agents` | a `BaseAgent` subclass with a non-empty `agent_type` | `AgentRegistry` |
| `agenticorg.providers` | a `VerificationProvider` subclass with a valid `name` and a `frozenset` of `Capability` as `capabilities` | `ProviderRegistry` (`connectors/providers/registry.py`) |
| `agenticorg.workflows` | — | discovered and rejected (`unsupported_group`) until a workflow registry exists |

Install the package into the same environment as AgenticOrg (the API image and
the worker image).

## Turn loading on

Loading an entry point imports and runs that package's code, so loading is off
by default and only distributions you name are imported:

| Setting | Default | Meaning |
|---|---|---|
| `AGENTICORG_PLUGIN_LOADING` | `false` | Load plugins at startup |
| `AGENTICORG_PLUGIN_ALLOWLIST` | empty | Comma-separated distribution names allowed to load, e.g. `acme-kyb-agenticorg` |

Names are compared after PEP 503 normalisation, so `Acme_KYB_AgenticOrg` and
`acme-kyb-agenticorg` match. Set both on the API and on every Celery worker:
the API loads plugins during startup, and each worker process loads them when
it starts.

## What happens at startup

Native connectors, agents and providers register first. Then, for each entry point in each
group, AgenticOrg either registers it or rejects it with one of these reasons:

| Reason | Cause |
|---|---|
| `not_allowlisted` | the distribution is not in `AGENTICORG_PLUGIN_ALLOWLIST` (the plugin is not imported) |
| `unknown_distribution` | the entry point does not belong to an installed distribution (not imported) |
| `load_failed` | importing the entry point raised; the log carries the exception type and message |
| `invalid_type` | the object is not the class the group requires (for a provider: also a malformed `name` or `capabilities`) |
| `name_conflict` | a connector, agent or provider with that name is already registered — native implementations always win |
| `unsupported_group` | the group has no registry in this release (not imported) |

A rejected plugin is not registered. Other plugins still load and the
application still starts. Each decision is logged as `plugin_loaded` or
`plugin_rejected` with the group, entry point, distribution, reason and detail,
and counted in the `agenticorg_plugin_load_total{group, outcome}` metric, where
`outcome` is `loaded` or one of the reasons above.

## Checking a deployment

- `plugin_rejected` log lines at startup name every plugin that did not load and why.
- `agenticorg_plugin_load_total{outcome="loaded"}` should match the plugins you expect.
- A plugin that is installed but missing from the allowlist shows up as
  `not_allowlisted`, never as a silent skip.

## Providers

A provider is created by calling its class with no arguments, so it reads its
own configuration (for example from environment variables) in `__init__`.
Declare `name` and `capabilities` as class attributes: they are validated when
the entry point is registered, before the class is constructed. An instance may
narrow its capabilities, never widen them. When the constructor raises, only
the exception type is logged, never its message, which may carry configuration.
`ProviderRegistry.create(name)` fails closed with a reason when the name is
unknown (`unknown_provider`), the constructor raises (`construction_failed`)
or the instance is malformed (`invalid_provider`). See
`docs/providers/writing-a-verification-provider.md` for the interface, the
conformance suite a provider must pass, and packaging.

## The sanctions screening connector

The `sanctions_screening` connector gives agents screening tools
(`screen_entity`, `screen_person`, `screen_business`, `screen_transaction`,
`batch_screen`) and runs every call through a provider. It has no endpoint or
API key of its own; its connector config names the provider:

```json
{"provider": "acme_kyb"}
```

Without `provider` it uses `mock`, which runs only where `AGENTICORG_ENV` is
local, dev, development, test or ci, so elsewhere the connector refuses to
connect until a provider package is installed, allowlisted and named. The
provider must declare `screen_person`, `screen_business` or both. A screening
the provider does not declare fails the call with `capability_not_supported`,
and `screen_entity` or `screen_transaction` without a party `type` needs both,
because the name is then screened as a person and as a business. Results are
`screening_result` records (`schemas/screening_result.schema.json`) with every
candidate the provider returned; there is no score threshold.

The connector test reports `configured` and the provider's name, never
`healthy`: a provider has no probe, so wrong credentials only show when the
first screening fails. Activating an agent checks that each linked connector
is `healthy`, so an agent that links this connector cannot be activated
until the provider seam gains a probe (tracked in `FINDINGS.md`).

`sanctions_api` is the connector's deprecated id. It resolves to the same
connector, so connector configurations saved under it keep working, and
creating one logs `connector_id_deprecated` (and a Python
`DeprecationWarning`). It no longer calls a screening service directly: set
`provider` in its connector config as above, or move the configuration to
`sanctions_screening`. Its `get_alert` and `generate_report` tools are gone,
because no provider-neutral equivalent exists.

An agent's tools, bare names such as `screen_entity` included, bind to
`sanctions_api` when the agent's connectors name that id and to
`sanctions_screening` otherwise. Grantex checks scopes per connector id, so
every grant check - LangGraph agents, `BaseAgent` and the tool gateway, in every
enforcement mode - also accepts a scope held under the connector's other id
(`enforce_connector_grant` in `auth/grant_enforcement.py`): grants issued
before the rename name `sanctions_api` and still cover the tools they
listed, and grants issued now name `sanctions_screening` whichever id the
agent uses. Such an old grant does not cover `screen_person` or
`screen_business`; adding either tool to the agent refreshes its scopes, as any
tool change does.

While the id is kept, a plugin connector cannot register as `sanctions_api`:
the loader rejects it with `name_conflict`, so a separately packaged screening
connector needs a name of its own.
