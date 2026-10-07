# AI asset inventory

With `AGENTICORG_GOVERNANCE_INVENTORY_ENABLED` on, a tenant administrator can read a live inventory
of what the tenant runs (`core/governance/inventory.py`): an AI bill of materials assembled from the
tenant's configuration, never from a run and never from prompt text.

| Kind | What is listed | Owner | Version | Risk tier |
|---|---|---|---|---|
| `agent` | every agent, with its runtime status and registry state | the owning user, or `platform` for a built-in | the agent version | the registry card's tier |
| `model` | the models the tenant's settings name (default, fallback, embedding) and the models agents call | who last changed the settings | the model name | the highest tier of the agents that call it |
| `prompt` | the prompt templates, and each agent's own prompt as a content hash | the template's author, or the agent's owner | the template's last change, or the hash | the highest tier of the agents that use it |
| `knowledge_base` | one per document domain, with document and chunk counts and the embedding models in use | unclaimed | the latest ingestion date | the highest tier of the agents that search it |
| `tool`, `connector` | the tools agents are authorised to call and the connectors behind them | `platform` for a built-in tool | | the highest tier of the agents that call it |

Every asset carries a reference (`kind:key`), a name, an owner, a version, a risk tier, a status
and the assets it depends on. An owner of `null` and an agent with no tier are gaps, and the
summary counts them (`unowned`, `untiered_agents`) beside the counts by kind and by tier.

`GET /governance/inventory?kind=&risk_tier=&q=` lists the assets (ordered by kind, then name)
with the summary; `kind` is one of the kinds above, `risk_tier` one of `low`, `medium`, `high`,
`critical` or `unset`, and `q` matches the name or reference. `GET /governance/inventory/export`
returns the same inventory as a bill-of-materials document: `bomFormat`, `specVersion`, a serial
number, metadata (when it was generated, for which tenant, the summary), `components` (one per
asset, with `bom-ref`, `type`, `name`, `version`, `owner`, `risk_tier`, `status` and the asset's
properties) and `dependencies` (each asset's `dependsOn` list). Both endpoints are tenant-admin
only, under the `governance.inventory.sensitive.read` scope, and audited.

The inventory is bounded (5000 assets) and read on each request; it stores nothing. Off, the
endpoints are not found and nothing here reads the database. Model cards, regulatory risk tiers
with their forced gates, and the policy console are the next parts of this package.
