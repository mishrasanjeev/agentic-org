## Hosted versus institution-managed

The hosted workspace and a self-managed deployment share product concepts, but operational responsibilities differ. A bank-managed environment must own network, identities, keys, database, backups, model/provider access, workers, updates, security monitoring and recovery. Self-hosting alone does not establish regulatory compliance or data residency.

For most business users, use the approved hosted or institution-provided URL. This guide is for IT/platform teams, not a requirement before completing [Your first agent](/docs/first-agent).

## Local learning environment

The repository provides a Docker Compose development stack and GNU Make targets. With reviewed prerequisites installed:

```bash
git clone https://github.com/mishrasanjeev/agentic-org.git
cd agentic-org
make dev
make seed
```

The current local quickstart binds the console to `http://127.0.0.1:3000` and API to `http://127.0.0.1:8000`. It includes local data services and development verification, identity and model stubs. The default fake/scripted model responses are explicitly synthetic; this environment does not prove a real provider, bank integration or customer transaction.

Use `make ps` for service status, `make logs` for logs, `make test`/`make check` for repository checks and `make e2e` for browser tests. `make down` preserves data; `make clean` deletes development volumes. Do not run destructive cleanup against a production environment.

## Prepare production separately

| Responsibility | Required decision/evidence |
| --- | --- |
| Identity | Named users, role/scope model, SSO/SCIM where configured |
| Network | Approved ingress/egress, TLS, provider endpoints and private access |
| Database | PostgreSQL/pgvector, tenant isolation, migrations, backup/restore proof |
| Runtime | API/UI, Redis, workers/schedulers, OCR and browser dependencies |
| Models | Allowed endpoints, credentials, retention, cost and region |
| Integrations | Bank/provider contracts, scopes, test accounts and ownership |
| Operations | Monitoring, alert ownership, capacity tests and rollback |

Do not copy development placeholder secrets, fake-model flags or seed identities into production. Air-gapped deployment has additional dependency/model/image and outbound-integration limits; review the operator guide before promising disconnected capability.

## Feature switches that ship off

The main feature switches that default to off are listed below; `core/config.py` lists every setting. A setting that is off leaves the earlier behaviour in place. Some also have a per-tenant flag, shown in parentheses. These are authority flags managed by the platform operator, except `prompts.maker_checker`, which a tenant admin can set. Set a switch the same way on every service of the deployment, and treat turning one on as a change to plan, test and record like a release.

| Setting | What it turns on |
| --- | --- |
| `AGENTICORG_MODEL_GATEWAY_ENABLED` (`model_gateway.enabled`) | Routing and access policies and per-model limits on live model calls |
| `AGENTICORG_MODEL_GATEWAY_RECORDS_ENABLED` | A signed routing record for each gateway-routed model call |
| `AGENTICORG_GUARDRAILS_HOOKS_ENABLED`, `AGENTICORG_GUARDRAILS_ENFORCE` (`guardrails.enforce`) | Guardrail evaluation at every stage, then enforcement instead of flag-only |
| `AGENTICORG_OPERATOR_OVERRIDE_ENABLED` (`operator_override.enabled`) | Halting or throttling a model, agent, workflow or the tool pipeline |
| `AGENTICORG_RESIDENCY_ENFORCE` (`residency.enforce`) | Refusing providers without an attestation for the tenant's data region |
| `AGENTICORG_TRACING_ENABLED`, `AGENTICORG_TRACING_TIMELINE_ENABLED` | OpenTelemetry spans and stored run timelines |
| `AGENTICORG_SYNTHETIC_CHECKS_ENABLED` | The scheduled synthetic-check sweep and the Observability Checks tab |
| `AGENTICORG_AUDIT_CHAIN_ENABLED` | Linking audit rows into a tamper-evident hash chain |
| `AGENTICORG_RUNTIME_LIMITS_ENABLED`, `AGENTICORG_RUNTIME_MEMORY_ENABLED`, `AGENTICORG_TOOL_REGISTRY_ENABLED`, `AGENTICORG_RUNTIME_DEBUG_CONSOLE_ENABLED` | Per-agent run limits and loop detection, long-term memory, tool schema checks and the step-through debugger |
| `AGENTICORG_WORKFLOW_BUILDER_V2_ENABLED` | The **Build visually** tab and definition checks on save |
| `AGENTICORG_OUTPUT_SCHEMA_ENFORCED`, `AGENTICORG_CONTEXT_WINDOW_MANAGED`, `AGENTICORG_PROMPT_TYPED_PARAMETERS_ENABLED` | Checking an agent's final answer against its output schema, with escalation to a person when it stays invalid; leaving older tool results out of a conversation that does not fit the model's window; typed prompt parameters |
| `AGENTICORG_PROMPTS_MAKER_CHECKER` (`prompts.maker_checker`), `AGENTICORG_PROMPT_COMPARE_ENABLED`, `AGENTICORG_EVALS_V2_ENABLED`, `AGENTICORG_EVAL_PROMOTION_GATE_ENABLED` | Prompt maker-checker, model comparison, evaluation datasets and the promotion gate |
| `AGENTICORG_AGENT_REGISTRY_ENABLED` | Agent registry cards, lifecycle states and the Agent Catalogue |
| `AGENTICORG_GOVERNANCE_INVENTORY_ENABLED`, `AGENTICORG_GOVERNANCE_MODEL_CARDS_ENABLED`, `AGENTICORG_GOVERNANCE_RISK_TIERS_ENABLED`, `AGENTICORG_GOVERNANCE_POLICY_CONSOLE_ENABLED` | AI asset inventory, model cards, risk tiers and the policy console |
| `AGENTICORG_FINOPS_ATTRIBUTION_ENABLED`, `AGENTICORG_FINOPS_THRESHOLDS_ENABLED`, `AGENTICORG_FINOPS_FORECAST_ENABLED` | Use-case cost attribution, thresholds that alert, throttle or suspend runs (they need attribution on), and forecasts |
| `AGENTICORG_SPEND_INTELLIGENCE_ENABLED` | [AI spend intelligence](/docs/ai-spend-intelligence) |
| `AGENTICORG_KNOWLEDGE_HYBRID_SEARCH`, `AGENTICORG_KNOWLEDGE_RERANK_ENABLED`, `AGENTICORG_KNOWLEDGE_QUERY_TRANSFORM_ENABLED`, `AGENTICORG_KNOWLEDGE_GRAPH_RETRIEVAL_ENABLED` | Additional knowledge retrieval stages |
| `AGENTICORG_CONVERSATION_V2_ENABLED` | Banking intents and earlier-turn context in chat, and the Conversations screen |
| `AGENTICORG_CONTENT_SERVICES_ENABLED` | Governed drafting, summarisation and extraction APIs |
| `AGENTICORG_IDP_ENABLED`, `AGENTICORG_WORKBENCH_V2_ENABLED`, `AGENTICORG_SPEECH_INTELLIGENCE_ENABLED`, `AGENTICORG_TRANSACTION_INTELLIGENCE_ENABLED`, `AGENTICORG_LINEAGE_ENABLED` | Document processing, workbenches, call recordings, transaction findings and lineage, with their [screens](/docs/review-and-operations-screens) |
| `AGENTICORG_PERSONALISATION_ENABLED` | Consent-checked personalisation |
| `AGENTICORG_PAUSED_AGENTS_REFUSED` | Refusing runs, chats and workflow steps for paused agents |
| `AGENTICORG_ROUTE_SCOPE_A2A_MCP` | Scope checks on the A2A task and MCP call routes |

## Release handoff

Use the repository's reviewed deployment path and migration-first process. Strict (non-local) runtimes make no schema changes at start-up and refuse to start unless the database is at the migration head the build carries, so apply migrations before the release that needs them. Verify exact commit, migration completion, API/UI revisions, traffic, health, database/Redis and required CI. Then test the institution's actual authorized flow with approved synthetic data. A successful build is not a completed deployment or provider canary.

## User documentation domain

The public user manual is available under `/docs` in the UI build. `docs.agenticorg.ai` should be a DNS **subdomain**, not a separate nameserver delegation by default. Hosting/TLS must route that hostname to the reviewed UI/static documentation service before publishing the link. The repository's documentation-domain runbook explains the release checks and ownership; no DNS change is made by reading this guide.
