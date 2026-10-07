# Policy console

With `AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED` on, a tenant administrator sees, writes and
dry-runs every policy of the tenant in one place (`core/governance/policy_console.py`). The console
stores nothing of its own: a policy written here is the same row the enforcement point reads, so
what the console shows is what is enforced.

| Kind | Enforcement point | Effect |
|---|---|---|
| `model_routing` | every model call, in the model gateway | `route` (to a provider, model, tier or weighted split; optionally in region only) |
| `model_access` | every model call, in the model gateway | `allow` or `deny` |
| `model_limit` | every model call, in the model gateway | `limit` (concurrency, requests a minute) |
| `guardrail` | the input, retrieval, output and action stages of a call | `flag`, `mask`, `redact`, `tokenise` or `block` |
| `approval` | workflow execution, when a human review is requested | `require_approval` (the approver steps) |
| `action` | tool invocation (read-only here) | the action taxonomy's risk class and containment |

Every entry carries the kind, id, name, enabled, priority, scope (use case, agent, sensitivity,
business unit, language, stage, risk tier, workflow, provider, model), effect, enforcement point,
reason and who last wrote it.

- `GET /governance/policies?kind=` lists them, with the enforcement points and kinds.
- `POST /governance/policies` with `{"kind": ..., "policy": {...}}` writes one; the policy is
  validated by the same input schema its own API uses, written through the same writer, and
  attributed to the same actor, so the gateway's cache, the guardrails' cache and the audit rows
  behave as they do for a write through the kind's own API.
- `DELETE /governance/policies/{kind}/{id}` removes one the same way.
- `POST /governance/policies/evaluate` is a dry run across the enforcement points for a described
  call: the gateway's decision for the use case, sensitivity, agent and requested model (routed,
  allowed or refused, with the policy that decided); the guardrail outcomes for a text at a stage
  (flagged, transformed or blocked, with the rules that fired); the approval policies that would
  apply to the workflow or agent; and the risk class of a tool. The verdict is `blocked`, `flagged`,
  `routed` or `allowed`, with the reasons, and nothing is metered, logged or audited.

Everything is tenant-admin only, under the `governance.policies.sensitive.read` and `.write`
scopes, and audited. Off, the endpoints are not found and nothing here reads or writes. The
kinds' own APIs (`/model-gateway/*`, `/guardrails/*`, `/approval-policies`) keep working as before.
