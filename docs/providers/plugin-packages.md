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
`ProviderRegistry.create(name)` fails closed with a reason when the name is
unknown (`unknown_provider`), the constructor raises (`construction_failed`)
or the instance is malformed (`invalid_provider`). See
`docs/adr/0009-provider-seam.md` for the interface.
