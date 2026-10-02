# Coverage matrix

Status per capability for AgenticOrg and Grantex, with repository evidence (paths relative to each repository)
and the capability group (see the [README](README.md)) each item belongs to. Combined status is the better
of the two products.

| Section | Items | Covered | Partial | Gap |
|---|---:|---:|---:|---:|
| Baseline conditions | 5 | 1 | 4 | 0 |
| Technical capabilities | 50 | 4 | 33 | 13 |
| Functional capabilities | 150 | 15 | 100 | 35 |
| Total | 205 | 20 | 137 | 48 |


## Baseline residency, isolation and control conditions

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| BASE-01 | In-country data residency | Partial | **Partial**. Residency enforcement refuses any provider without an active in-region attestation for the tenant region at the credential resolver, the retrieval service, the tool hub and tracing export (behind residency.enforce); the deploy script still defaults to a foreign region and the hosted stack uses external model APIs until attested. (`core/governance/residency.py`, `api/v1/residency.py`, `docs/governance/data-residency.md`) | **Partial**. A grant can be issued with a data region, and relying parties with the region check on refuse it elsewhere (both SDKs and the gateway, opt-in this release); the hosted service is still pinned to one foreign region. (`apps/auth-service/src/lib/purpose.ts`, `packages/sdk-ts/src/client.ts`, `packages/sdk-py/src/grantex/_client.py`) | WP-19 |
| BASE-02 | Isolated production instance | Partial | **Partial**. Shared-database multi-tenancy isolated by row-level security; the tenancy profile (shared or dedicated) is a reported deployment setting, and a dedicated instance is a self-host deployment rather than a packaged profile. (`core/governance/residency.py`, `docs/adr/0002-multi-tenancy-via-rls.md`, `docs/deployment-airgap.md`) | **Partial**. Self-hosting gives a dedicated instance; the hosted service is multi-tenant with application-level tenant scoping. (`deploy/helm/grantex/values.yaml`, `docker-compose.prod.yml`, `docs/self-hosting.md`) | WP-19 |
| BASE-03 | In-country disaster recovery | Partial | **Partial**. The disaster-recovery profile (standby region, conformance, last drill) is reported in the compliance package; the Terraform standby is still scaffolded, not deployed. (`core/governance/residency.py`, `infra/terraform/multi_region/main.tf`, `docs/BACKUP_AND_DR.md`) | **Gap**. Single region, no secondary DR site; backup and restore runbook not written. (`docs/compliance/data-residency.md`, `docs/self-hosting.md`) | WP-19 |
| BASE-04 | No training on institution data | Partial | **Partial**. No training pipeline exists; with residency enforcement on for the tenant, a provider is usable in its region only with an attestation that records a written no-training commitment and its evidence reference, revocable and audited; with enforcement off (the default) the attestation is not consulted. (`core/governance/residency.py`, `core/models/provider_attestation.py`, `docs/governance/data-residency.md`) | **Gap**. No prompt processing or training; no written no-training control. | WP-19 |
| BASE-05 | Emergency operator override | Covered | **Covered**. Operator override halts or throttles a provider, a model, one agent, every agent, a workflow or the tool pipeline at every enforcement point, with a signed audit row per change; behind the authority flag operator_override.enabled. (`core/governance/operator_override.py`, `api/v1/operator_overrides.py`, `docs/governance/operator-override.md`) | **Partial**. Emergency stop by grant/agent/principal/developer with issuance freeze and revocation feed; off unless EMERGENCY_STOP_ENABLED; cannot halt models or pipelines; the gateway checks JWTs locally with no online revocation. (`apps/auth-service/src/routes/emergency-stop.ts`, `apps/auth-service/src/lib/revocation/emergency-stop.ts`, `apps/auth-service/src/lib/revocation/issuance-freeze.ts`) | WP-01 |

## Compute and infrastructure resilience

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| INF-01 | Multi-zone architecture | Partial | **Partial**. Cloud infrastructure item: the deployment reference names the control and the compliance package records the operator attestation; nothing in code provisions it. (`core/governance/infrastructure.py`, `docs/bfsi/deployment-reference.md`) | **Gap**. Cloud infrastructure item. | WP-00 |
| INF-02 | Native disaster recovery | Partial | **Partial**. DR scaffold and runbook, not deployed. (`infra/terraform/multi_region/main.tf`, `docs/BACKUP_AND_DR.md`, `docs/RUNBOOKS.md`) | **Gap**. Backup guidance only. (`docs/self-hosting.md`) | WP-00 |
| INF-03 | AI accelerators | Partial | **Partial**. Self-hosted vLLM, Ollama and TEI paths; no GPU provisioning. (`core/langgraph/llm_factory.py`, `docs/deployment-airgap.md`, `Dockerfile.tei.baked`) | **Gap**. Cloud infrastructure item. | WP-00 |
| INF-04 | Managed Kubernetes | Partial | **Partial**. Kubernetes path marked legacy; production is serverless containers. (`scaling/hpa_integration.py`, `docs/deployment.md`) | **Partial**. Helm chart with autoscaler and disruption budget for managed Kubernetes. (`deploy/helm/grantex/templates/hpa.yaml`, `deploy/helm/grantex/templates/pdb.yaml`) | WP-00 |
| INF-05 | Managed serverless containers | Covered | **Covered**. API and UI run as serverless containers. (`scripts/deploy_cloud_run.sh`, `cloudbuild-api.yaml`, `Dockerfile.ui.cloudrun`) | **Partial**. Deploys to serverless containers on one provider. (`deploy/gcp/setup.sh`, `.github/workflows/deploy.yml`) | WP-00 |

## Security, identity and key management

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| SEC-01 | Hardware security module | Gap | **Gap**. No HSM. | **Gap**. No HSM integration. | WP-00 |
| SEC-02 | Key management and BYOK | Covered | **Covered**. Envelope encryption with a KMS key-encryption key, per-tenant BYOK, rotation and rewrap. (`core/crypto/envelope.py`, `core/crypto/tenant_secrets.py`, `core/crypto/rewrap.py`) | **Partial**. AES-256-GCM vault key and encrypted signing keys with publish-then-sign rotation; no KMS/HSM/BYOK. (`apps/auth-service/src/lib/vault-crypto.ts`, `apps/auth-service/src/lib/signing-keys.ts`, `apps/auth-service/src/cli/rotate-signing-key.ts`) | WP-00 |
| SEC-03 | Secrets management | Partial | **Partial**. Application keyring vault with quarterly rotation; environment secrets rely on the cloud secret manager. (`core/crypto/credential_vault.py`, `core/crypto/tenant_secrets.py`, `docs/SECRETS_ROTATION.md`) | **Partial**. Encrypted credential vault and secret-manager runtime secrets; no managed DB credential or certificate rotation. (`apps/auth-service/src/routes/vault.ts`, `deploy/gcp/setup.sh`, `apps/auth-service/src/routes/event-sources.ts`) | WP-00 |
| SEC-04 | Web application firewall | Partial | **Gap**. Application rate limits only. (`api/route_enforcement.py`, `core/tool_gateway/rate_limiter.py`) | **Partial**. Rate limiting only; no WAF ruleset. (`deploy/nginx/nginx.conf`, `apps/auth-service/src/plugins/dynamicRateLimit.ts`) | WP-00 |
| SEC-05 | Managed DDoS protection | Gap | **Gap**. Cloud infrastructure item. | **Gap**. Cloud infrastructure item. | WP-00 |
| SEC-06 | Security posture management | Partial | **Partial**. CI-time image scans, SBOM, CodeQL, dependency audit; no runtime posture management. (`.github/workflows/container-scan.yml`, `.github/workflows/security-scan.yml`, `.github/workflows/codeql.yml`) | **Partial**. CI-time image scans, CodeQL, secret scanning; no runtime posture management. (`.github/workflows/security-scan.yml`, `.github/workflows/codeql.yml`, `scripts/scan-container.sh`) | WP-00 |
| SEC-07 | Threat detection | Partial | **Gap**. Grant-denial alerts only. (`observability/alerting.py`) | **Partial**. Irregularity detection on authorization events; no control-plane or network detection. | WP-00 |
| SEC-08 | SIEM and security lake | Partial | **Partial**. Structured logs and audit query API; no open-schema export. (`core/logging_config.py`, `core/models/audit.py`, `api/v1/audit.py`) | **Partial**. SIEM sinks and audit export; no open-schema security lake. (`packages/destinations/src/destinations/splunk.ts`, `packages/destinations/src/destinations/datadog.ts`, `docs/guides/siem-splunk.mdx`) | WP-00 |
| SEC-09 | Identity and access management | Partial | **Partial**. RBAC, scopes, OIDC SSO, API keys, time-bound delegations; MFA delegated to the identity provider. (`core/rbac.py`, `auth/scopes.py`, `auth/sso/oidc.py`) | **Partial**. Scopes, short-lived DPoP-bound tokens, OIDC/SAML SSO with group mapping, SCIM, passkeys; no general role model; admin MFA delegated to the identity provider. (`apps/auth-service/src/routes/sso.ts`, `apps/auth-service/src/routes/scim.ts`, `apps/auth-service/src/routes/webauthn.ts`) | WP-00 |

## Data management, lakehouse and storage

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| DATA-01 | Scalable object storage | Gap | **Gap**. Storage region setting only. (`core/config.py`) | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-02 | Serverless SQL query engine | Gap | **Gap**. Cloud infrastructure item. | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-03 | Managed lakehouse | Gap | **Gap**. Cloud infrastructure item. | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-04 | Managed streaming | Gap | **Gap**. Redis and Celery queues only. (`core/tasks/celery_app.py`) | **Gap**. Kafka sink and SSE/WebSocket streams only. (`packages/destinations/src/destinations/kafka.ts`) | WP-00 |
| DATA-05 | Managed serverless ETL | Gap | **Gap**. Scheduled jobs only. (`core/tasks/celery_app.py`) | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-06 | Data catalogue and lineage | Gap | **Gap**. Chunk provenance and a classification doc; no catalogue or lineage. (`core/rag/ingest.py`, `docs/data-classification.md`) | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-07 | Managed search engine | Partial | **Partial**. pgvector search with keyword fallback. (`api/v1/knowledge.py`) | **Gap**. Cloud infrastructure item. | WP-00 |
| DATA-08 | Managed relational databases | Partial | **Partial**. Postgres assumed; HA and backups not verified. (`infra/terraform/multi_region/main.tf`, `docs/BACKUP_AND_DR.md`) | **Partial**. Managed Postgres without HA configuration. (`deploy/gcp/setup.sh`, `docs/self-hosting.md`) | WP-00 |

## AI infrastructure and foundation-model services

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| AIINF-01 | Multi-model foundation hub | Partial | **Partial**. Provider allowlist with cloud and local models and per-tenant credentials; no region pinning or multimodal hub. (`core/ai_providers/catalog.py`, `core/langgraph/llm_factory.py`, `core/llm/router.py`) | **Gap**. Not in scope. | WP-02 |
| AIINF-02 | Managed RAG service | Partial | **Partial**. Native extract, chunk, embed and vector pipeline; no agentic or graph retrieval. (`core/rag/ingest.py`, `core/rag/extractors.py`, `api/v1/knowledge.py`) | **Gap**. Not in scope. | WP-04 |
| AIINF-03 | Native vector database | Partial | **Partial**. pgvector cosine search filtered only by tenant and status. (`api/v1/knowledge.py`, `core/embeddings.py`, `migrations/versions/v4_9_4_multimodal_rag.py`) | **Gap**. Not in scope. | WP-04 |
| AIINF-04 | AI guardrails and safety | Partial | **Partial**. PII detection with India recognisers, toxicity check, regex injection check at agent generation, strict untrusted-text guard for governed cases; runtime check is opt-in and only flags. (`core/content_safety/checker.py`, `core/pii/redactor.py`, `core/extraction/context.py`) | **Gap**. No guardrails in code. | WP-03 |
| AIINF-05 | Multi-agent orchestration | Covered | **Covered**. Collaboration steps, intent decomposition, hierarchy, delegation, teams, A2A. (`workflows/collaboration.py`, `core/orchestrator/nexus.py`, `api/v1/agent_teams.py`) | **Gap**. Grant delegation and A2A auth only. (`apps/auth-service/src/routes/delegate.ts`, `packages/a2a/src/server.ts`) | WP-17 |
| AIINF-06 | Agent workflow builder | Partial | **Partial**. JSON or natural-language authoring with human-in-loop steps; the visual builder component is read-only and unused. (`ui/src/pages/WorkflowCreate.tsx`, `workflows/engine.py`, `ui/src/pages/WorkflowRun.tsx`) | **Gap**. No workflow builder. | WP-17 |
| AIINF-07 | Model evaluation framework | Partial | **Partial**. Golden datasets with deterministic scorer, retrieval eval gate, shadow comparator; no model-vs-model benchmarking. (`evals/runner.py`, `evals/scorer.py`, `core/rag/eval.py`) | **Gap**. Not in scope. | WP-07 |
| AIINF-08 | Dual-mode memory service | Partial | **Partial**. Redis chat history and encrypted checkpoints; no long-term memory store. (`api/v1/chat.py`, `core/langgraph/checkpointer.py`) | **Gap**. Not in scope. | WP-17 |
| AIINF-09 | Model gateway | Partial | **Partial**. Model gateway with tenant routing policies (use case, data sensitivity, agent, business unit, language) in front of the agent runner and the direct router, behind model_gateway.enabled; no per-model rate limits or usage controls yet. (`core/governance/model_gateway.py`, `api/v1/model_gateway.py`, `core/llm/router.py`) | **Gap**. Grant-token reverse proxy could front model endpoints; not model-aware. (`packages/gateway/src/proxy.ts`) | WP-02 |
| AIINF-10 | Agent tool gateway | Covered | **Covered**. Every tool call passes scope and grant checks, risk policy, rate limiting, idempotency and masked audit; MCP and Composio discovery. (`core/tool_gateway/gateway.py`, `core/tool_gateway/provider_gateway.py`, `auth/grant_enforcement.py`) | **Partial**. Grant-token proxy with audience and data-region checks, MCP resource guard, credential exchange and an MCP server registry; the gateway verifies tokens locally by default and against the issuer with currentAuthorityCheck on. (`packages/gateway/src/proxy.ts`, `packages/gateway/src/server.ts`, `packages/mcp-auth/src/resource/guard.ts`) | WP-01 |
| AIINF-11 | Agent lifecycle governance | Partial | **Partial**. Agent version snapshots, shadow-to-active gates, rollback, clone, workflow A/B; no traffic split between agent versions. (`api/v1/agents.py`, `core/models/agent.py`, `core/workflow_ab.py`) | **Partial**. Agent registration, active/suspended, key rotation, passport issue/revoke; no versioning, traffic routing or rollback. (`apps/auth-service/src/routes/agents.ts`, `apps/auth-service/src/routes/agent-keys.ts`, `apps/auth-service/src/routes/passport.ts`) | WP-05 |
| AIINF-12 | Sandboxed workload isolation | Partial | **Partial**. Out-of-process seccomp extraction; agent sessions and tools are not isolated. (`core/extraction/sandbox.py`, `core/extraction/_worker.py`, `docs/security/untrusted-content.md`) | **Gap**. No sandboxing. | WP-17 |
| AIINF-13 | Parameter-efficient fine-tuning | Gap | **Gap**. No fine-tuning. | **Gap**. Not in scope. | WP-00 |

## Networking and integration

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| NET-01 | Private service endpoints | Partial | **Gap**. No private endpoint configuration. | **Partial**. Private VPC for data stores. (`deploy/gcp/setup.sh`) | WP-00 |
| NET-02 | Enterprise API gateway | Partial | **Partial**. Application rate-limit classes, API keys, WebSocket feed, request ids. (`api/route_metadata.py`, `api/route_enforcement.py`, `api/websocket/feed.py`) | **Partial**. API-key auth, plan rate limits, WebSocket/SSE events, header-injecting proxy. (`packages/gateway/src/server.ts`, `apps/auth-service/src/plugins/dynamicRateLimit.ts`, `apps/auth-service/src/routes/events.ts`) | WP-00 |
| NET-03 | Managed secure file transfer | Gap | **Gap**. Category label only. (`core/workflow_generator.py`) | **Gap**. Cloud infrastructure item. | WP-00 |
| NET-04 | Managed service mesh | Gap | **Gap**. mTLS flag reported only. (`api/v1/compliance.py`) | **Gap**. Cloud infrastructure item. | WP-00 |

## DevOps, observability, resilience, data protection and FinOps

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| OPS-01 | Distributed tracing for AI | Partial | **Partial**. OpenTelemetry span catalogue defined but not wired; audit rows carry trace_id. (`observability/tracing.py`, `observability/langsmith.py`, `core/models/audit.py`) | **Partial**. OpenTelemetry in the auth service with agent and grant attributes. (`apps/auth-service/src/lib/tracing.ts`, `apps/auth-service/src/lib/traceAttributes.ts`, `docs/guides/opentelemetry.mdx`) | WP-09 |
| OPS-02 | Centralised platform logging | Partial | **Partial**. Structured logs, Prometheus metrics, dashboards. (`core/logging_config.py`, `observability/metrics.py`, `monitoring/grafana/agenticorg-dashboard.json`) | **Partial**. Structured logs, Prometheus metrics, Grafana dashboards. (`apps/auth-service/src/lib/logger.ts`, `apps/auth-service/src/lib/metrics.ts`, `deploy/grafana/overview-dashboard.json`) | WP-09 |
| OPS-03 | Infrastructure as code | Partial | **Partial**. Terraform for monitoring and DR scaffold; no drift detection. (`infra/terraform/monitoring/alerts.tf`, `infra/terraform/multi_region/main.tf`) | **Partial**. Terraform provider and Helm chart; no drift detection. (`packages/terraform-provider-grantex/internal/provider/provider.go`, `deploy/helm/grantex/Chart.yaml`, `docs/guides/pulumi.mdx`) | WP-00 |
| OPS-04 | Cost management and FinOps | Partial | **Partial**. Departments, cost centres, budget alerts, cost dashboard; no irregularity detection. (`api/v1/departments.py`, `core/billing/budget_evaluator.py`, `api/v1/costs.py`) | **Gap**. Only per-grant budgets. | WP-10 |
| OPS-05 | Fault injection and resilience testing | Partial | **Partial**. Mock fault injection and load tests only. (`connectors/providers/mock/config.py`, `tests/load/local_docker_resource_stress.py`, `docker-compose.performance.yml`) | **Gap**. Load scripts only. (`scripts/docker-stress-test.mjs`) | WP-00 |
| OPS-06 | Managed CI/CD | Partial | **Partial**. GitHub Actions and Cloud Build. (`.github/workflows/deploy.yml`, `cloudbuild-api.yaml`, `scripts/deploy_cloud_run.sh`) | **Partial**. GitHub Actions CI/CD. (`.github/workflows/ci.yml`, `.github/workflows/deploy.yml`, `.github/workflows/release.yml`) | WP-00 |
| OPS-07 | Immutable backup (WORM) | Partial | **Partial**. Append-only audit table with HMAC signatures; no WORM backup. (`migrations/versions/v4_8_0_baseline.py`, `core/models/audit.py`, `audit/signer.py`) | **Gap**. Tamper-evident hash chain; no WORM backups. (`apps/auth-service/src/lib/audit-chain.ts`) | WP-00 |
| OPS-08 | Confidential compute | Gap | **Gap**. Cloud infrastructure item. | **Gap**. Cloud infrastructure item. | WP-00 |

## Cloud governance, support and service levels

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| GOV-01 | Published per-service SLAs | Partial | **Partial**. SLA document with service credits for the hosted service; the compliance package records the operator attestation of the hosting provider SLA. (`docs/SLA.md`, `core/governance/infrastructure.py`) | **Gap**. No published SLAs. | WP-00 |
| GOV-02 | Proven in-country track record | Gap | **Gap**. Cloud provider item. | **Gap**. Cloud provider item. | WP-00 |
| GOV-03 | 24x7 enterprise support | Partial | **Partial**. 15-minute SEV-1 response documented; no technical account management. (`docs/SLA.md`, `docs/incident-response.md`) | **Gap**. Cloud provider item. | WP-00 |

## Content generation, summarisation and structuring

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| CONTENT-01 | Governed drafting | Partial | **Partial**. Agents draft with approval gates; information requests render from approved templates; no notice or circular drafting service. (`core/agents/marketing/content_factory.py`, `core/agents/business_underwriter/information_request.py`, `core/agents/packs/legal/prompts/document_drafting.prompt.txt`) | **Gap** | WP-12 |
| CONTENT-02 | Structured summarisation | Partial | **Partial**. Generic summaries and the cited underwriting memo; no multi-document summarisation service. (`core/agents/business_underwriter/memo.py`, `core/reports/generator.py`, `core/agents/ops/contract_intelligence.py`) | **Gap** | WP-12 |
| CONTENT-03 | Obligation extraction | Partial | **Partial**. Prompt-based compliance agents and deadline tracking; no obligation-extraction pipeline. (`core/agents/ops/compliance_guard.py`, `core/agents/ops/contract_intelligence.py`, `core/models/compliance_deadline.py`) | **Gap** | WP-12 |
| CONTENT-04 | Narrative to payload | Partial | **Partial**. Schema registry and strict validation for governed cases only; generic output barely checked; no XML. (`api/v1/schemas.py`, `core/domain_schemas.py`, `schemas/incident.schema.json`) | **Gap** | WP-12 |
| CONTENT-05 | Policy-grounded response drafts | Partial | **Partial**. Policy-grounded memos for cases; no configurable approved-source set. (`core/agents/business_underwriter/memo.py`, `core/agents/ops/support_deflector.py`) | **Gap** | WP-12 |
| CONTENT-06 | Audience-adaptive tone | Partial | **Partial**. Tone parameter in marketing content only. (`core/agents/marketing/content_factory.py`) | **Gap** | WP-12 |
| CONTENT-07 | Clause assembly | Partial | **Partial**. Fixed approved templates; no rule-driven clause library. (`core/agents/business_underwriter/information_request.py`) | **Gap** | WP-12 |
| CONTENT-08 | Multilingual translation | Gap | **Gap**. UI strings only; no document translation. (`ui/src/locales/hi.json`, `docs/roadmap/i18n_full_coverage.md`) | **Gap** | WP-12 |

## Speech, audio/video intelligence and conversational insight

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| SPEECH-01 | Transcription | Partial | **Partial**. Real-time speech-to-text via telephony provider or local whisper; batch audio disabled. (`core/voice/pipeline.py`, `api/v1/voice_runtime.py`, `ui/src/pages/VoiceSetup.tsx`) | **Gap** | WP-13 |
| SPEECH-02 | Speech synthesis | Partial | **Partial**. Several TTS engines with a language code; no accent or rate controls. (`core/voice/pipeline.py`, `core/voice/runtime.py`) | **Gap** | WP-13 |
| SPEECH-03 | Speaker channel separation | Gap | **Gap**. No diarisation. | **Gap** | WP-13 |
| SPEECH-04 | Call summaries | Gap | **Gap**. No call summaries. | **Gap** | WP-13 |
| SPEECH-05 | Emotion and empathy analytics | Gap | **Gap**. Sentiment only for brand monitoring. | **Gap** | WP-13 |
| SPEECH-06 | Live agent assist | Partial | **Partial**. Voice turns run an agent with tools; no knowledge surfacing. (`core/voice/livekit_agent.py`, `api/v1/voice_runtime.py`) | **Gap** | WP-13 |
| SPEECH-07 | Disclosure script compliance | Gap | **Gap**. No disclosure-script tracking. | **Gap** | WP-13 |
| SPEECH-08 | Spoken sensitive-data redaction | Gap | **Gap**. Number masking and encrypted transcripts; no spoken-PII redaction. (`api/v1/voice_runtime.py`) | **Gap** | WP-13 |

## Enterprise data acquisition and information collection

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| ACQ-01 | Lawful automated acquisition | Partial | **Partial**. Portal automations and registry connectors. (`rpa/scripts/mca_company_search.py`, `rpa/scripts/rbi_org_scraper.py`, `rpa/scripts/epfo_ecr_download.py`) | **Partial**. Purpose-bound manifests for registry connectors; no acquisition engine. (`packages/sdk-ts/src/manifests/epfo.ts`, `packages/sdk-ts/src/manifests/gstn.ts`, `packages/sdk-ts/src/manifests/mca_portal.ts`) | WP-16 |
| ACQ-02 | Provenance metadata | Partial | **Partial**. Case evidence refs and chunk hashes; no general processing-history lineage. (`core/cases/evidence.py`, `core/rag/ingest.py`, `core/extraction/excerpts.py`) | **Partial**. Evidence records carry keyed digests, provider, upstream record references, receipt time and chain hash. (`spec/evidence-package.md`, `apps/auth-service/src/lib/evidence/build.ts`, `apps/auth-service/src/routes/evidence.ts`) | WP-16 |
| ACQ-03 | Scheduled incremental sync | Partial | **Partial**. CDC webhooks, beat schedules, RPA schedules; no generic incremental sync. (`core/cdc/receiver.py`, `core/tasks/celery_app.py`, `api/v1/rpa_schedules.py`) | **Gap** | WP-16 |

## Conversational AI and interaction services

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| CONV-01 | Banking intent recognition | Gap | **Gap**. Domain keyword routing; no banking intents or parameter extraction. (`api/v1/chat.py`) | **Gap** | WP-11 |
| CONV-02 | Multi-turn context | Partial | **Partial**. History stored but not fed back on later turns. (`api/v1/chat.py`, `core/langgraph/checkpointer.py`) | **Gap** | WP-11 |
| CONV-03 | Clarification before action | Partial | **Partial**. Narrow clarification template for one agent. (`api/v1/chat.py`) | **Gap** | WP-11 |
| CONV-04 | Confirmed transaction invocation | Partial | **Partial**. Write tools gated by risk policy or approval; no slot-filling or user confirmation. (`core/tool_gateway/gateway.py`, `core/governance/action_policy.py`, `core/langgraph/hitl_condition.py`) | **Partial**. Human consent and action-bound decision grants before execution. (`apps/auth-service/src/routes/decisions.ts`, `packages/mcp-auth/src/resource/grantex-decisions.ts`, `apps/auth-service/src/routes/authorize.ts`) | WP-11 |
| CONV-05 | Escalation hand-off | Partial | **Partial**. Review-queue item and ticket connectors; no transfer with summary and intent tag. (`api/v1/chat.py`, `connectors/ops/zendesk.py`, `connectors/ops/servicenow.py`) | **Gap** | WP-11 |
| CONV-06 | Graceful fallbacks | Partial | **Partial**. Confidence floor, failover, timeouts. (`api/v1/chat.py`, `core/llm/router.py`, `core/langgraph/runner.py`) | **Gap** | WP-11 |

## Enterprise knowledge retrieval

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| RAG-01 | Multi-format ingestion | Covered | **Covered**. PDF, Office, text, CSV, HTML, JSON, email and images via OCR. (`core/rag/extractors.py`, `api/v1/knowledge.py`) | **Gap** | WP-04 |
| RAG-02 | Layout-preserving extraction | Partial | **Partial**. Plain text extraction; no layout preservation. (`core/rag/extractors.py`) | **Gap** | WP-04 |
| RAG-03 | Configurable chunking | Partial | **Partial**. Single chunker without overlap or per-type strategies. (`core/rag/ingest.py`) | **Gap** | WP-04 |
| RAG-04 | Query transformation | Gap | **Gap**. No query transformation. | **Gap** | WP-04 |
| RAG-05 | Incremental re-embedding | Partial | **Partial**. Embed on upload, backfill and rotation; no automatic re-index on change. (`core/rag/ingest.py`, `core/embeddings_backfill.py`, `scripts/embedding_rotate.py`) | **Gap** | WP-04 |
| RAG-06 | Re-ranking stage | Gap | **Gap**. No re-ranker natively. (`api/v1/knowledge.py`) | **Gap** | WP-04 |
| RAG-07 | Clickable citations | Partial | **Partial**. Case memos cite evidence; search returns document names though page provenance is stored. (`core/agents/business_underwriter/memo.py`, `api/v1/knowledge.py`, `migrations/versions/v4_9_4_multimodal_rag.py`) | **Gap** | WP-04 |
| RAG-08 | Source excerpt navigation | Partial | **Partial**. Case excerpts by reference; no in-document highlight. (`api/v1/governed_cases.py`, `ui/src/pages/GovernedCaseDetail.tsx`) | **Gap** | WP-04 |
| RAG-09 | Access-aware retrieval | Partial | **Gap**. Tenant isolation only; no per-document ACL. (`docs/adr/0002-multi-tenancy-via-rls.md`, `api/v1/knowledge.py`) | **Partial**. Per-connector and per-tool scope and purpose restriction; no document-level filtering. (`docs/concepts/scopes.mdx`, `docs/concepts/tool-manifests.mdx`) | WP-04 |
| RAG-10 | Conflict flagging | Partial | **Partial**. Ownership discrepancy detection for cases only. (`core/agents/business_underwriter/reconciliation.py`) | **Gap** | WP-04 |
| RAG-11 | Retrieval quality metrics | Partial | **Partial**. Retrieval-relevance gate; no faithfulness or hallucination metrics. (`core/rag/eval.py`, `scripts/rag_eval.py`, `.github/workflows/rag-eval.yml`) | **Gap** | WP-04 |
| RAG-12 | Automatic query decomposition | Gap | **Gap**. No decomposition. | **Gap** | WP-04 |
| RAG-13 | Grounding enforcement | Partial | **Partial**. Citation-bound memo narratives and shadow hallucination gate; not for general answers. (`core/agents/business_underwriter/memo.py`, `scaling/shadow_comparator.py`) | **Gap** | WP-04 |
| RAG-14 | Agentic retrieval with traces | Gap | **Gap**. No agentic retrieval traces. | **Gap** | WP-04 |
| RAG-15 | Graph retrieval | Gap | **Gap**. No graph retrieval. | **Gap** | WP-04 |

## Intelligent document processing and document intelligence

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| IDP-01 | Bundle splitting and classification | Gap | **Gap**. No bundle split or classification. | **Gap** | WP-14 |
| IDP-02 | OCR across Indian languages | Partial | **Partial**. OCR with Indic script detection; no handwriting. (`core/rag/extractors.py`) | **Gap** | WP-14 |
| IDP-03 | Key-value extraction | Partial | **Partial**. Typed extraction for registry documents and applicant uploads only. (`core/extraction/schema.py`, `core/extraction/_worker.py`) | **Gap** | WP-14 |
| IDP-04 | Table extraction | Partial | **Partial**. Tables flattened to text. (`core/rag/extractors.py`) | **Gap** | WP-14 |
| IDP-05 | Bounding-box review overlay | Gap | **Gap**. No bounding boxes. | **Gap** | WP-14 |
| IDP-06 | Confidence routing | Partial | **Partial**. Page-level OCR confidence and agent confidence floor; no per-field scores. (`core/rag/extractors.py`, `core/agents/base.py`, `api/v1/approvals.py`) | **Gap** | WP-14 |
| IDP-07 | Cross-document reconciliation | Partial | **Partial**. Reconciliation for ownership and screening identifiers only. (`core/agents/business_underwriter/reconciliation.py`, `core/agents/screening_disposition/comparison.py`) | **Gap** | WP-14 |
| IDP-08 | Stamp and seal verification | Gap | **Gap**. No stamp verification. | **Gap** | WP-14 |
| IDP-09 | Regional script processing | Partial | **Partial**. Indic-script OCR. (`core/rag/extractors.py`) | **Gap** | WP-14 |
| IDP-10 | Document analysis reports | Partial | **Partial**. Structured underwriting memo only. (`core/agents/business_underwriter/memo.py`, `schemas/underwriting_memo.schema.json`) | **Gap** | WP-14 |
| IDP-11 | Bank statement line items | Partial | **Partial**. Statements via Account Aggregator or accounting APIs; no scanned-statement extraction. (`connectors/finance/banking_aa.py`, `core/agents/finance/recon_agent.py`) | **Gap** | WP-14 |
| IDP-12 | Version comparison | Gap | **Gap**. No version comparison. | **Gap** | WP-14 |

## Transaction intelligence and behavioural analytics

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| TXN-01 | Entity-centric aggregation | Partial | **Partial**. Governed case aggregates evidence for one subject; no entity-alert aggregation. (`core/cases/store.py`, `core/cases/runtime.py`, `api/v1/governed_cases.py`) | **Partial**. Evidence grouped per case. (`apps/auth-service/src/routes/evidence.ts`, `spec/evidence-package.md`) | WP-15 |
| TXN-02 | Structuring detection | Gap | **Gap**. No transaction analytics. | **Gap** | WP-15 |
| TXN-03 | Pass-through detection | Gap | **Gap**. No transaction analytics. | **Gap** | WP-15 |
| TXN-04 | Fund-flow graphs | Gap | **Gap**. No fund-flow graph. | **Gap** | WP-15 |
| TXN-05 | Suspicious transaction narrative | Gap | **Gap**. No narrative drafting. | **Gap** | WP-15 |
| TXN-06 | Consolidated evidence context | Covered | **Covered**. Case export, signed hand-off push and evidence records. (`api/v1/governed_cases.py`, `audit/evidence_package.py`, `core/cases/evidence.py`) | **Covered**. Signed hash-chained per-case evidence package with export and offline verification. (`spec/evidence-package.md`, `apps/auth-service/src/routes/evidence.ts`, `apps/auth-service/src/lib/evidence/verify.ts`) | WP-15 |

## Lead intelligence and customer engagement

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| LEAD-01 | Personalised content | Partial | **Partial**. Personalisation in marketing agents only. (`core/agents/marketing/abm_agent.py`, `core/agents/marketing/content_factory.py`, `core/marketing/intent_aggregator.py`) | **Gap** | WP-20 |

## AI platform architecture and model gateway

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| GW-01 | Unified model abstraction | Partial | **Partial**. One model factory across cloud and local LLMs behind a policy-driven gateway; no vision or multimodal routing. (`core/langgraph/llm_factory.py`, `core/governance/model_gateway.py`, `core/ai_providers/catalog.py`) | **Gap** | WP-02 |
| GW-02 | Policy-driven routing | Partial | **Partial**. Policies route by use case, data sensitivity, agent, business unit and language with same-provider failover and in-region restriction; no cost, latency or throughput routing yet. (`core/governance/model_gateway.py`, `core/llm/router.py`, `docs/governance/model-gateway.md`) | **Gap** | WP-02 |
| GW-03 | Independent model onboarding | Partial | **Partial**. Models onboarded by editing the allowlist in code. (`core/ai_providers/catalog.py`, `api/v1/tenant_ai_credentials.py`, `core/ai_providers/health.py`) | **Gap** | WP-02 |
| GW-04 | Traffic allocation and concurrency | Partial | **Partial**. Admission control for local runtimes; no traffic allocation across endpoints. (`core/runtime_capacity.py`, `core/tool_gateway/rate_limiter.py`) | **Gap** | WP-02 |
| GW-05 | Tiered response-time service levels | Partial | **Partial**. No tiered latency objectives or progress indication. (`docs/PERFORMANCE.md`, `ui/src/pages/SLAMonitor.tsx`) | **Gap** | WP-02 |
| GW-06 | Model-level metrics | Partial | **Partial**. Token and cost counters per model; no error, throughput or utilisation metrics. (`observability/metrics.py`, `core/llm/router.py`, `scaling/cost_ledger.py`) | **Gap** | WP-02 |
| GW-07 | Model access policies | Partial | **Partial**. Policies fence providers per agent and business unit (allowed_providers) and keep restricted data in region; no per-user or application identity policies yet. (`core/governance/model_gateway.py`, `api/v1/model_gateway.py`, `core/models/model_routing_policy.py`) | **Partial**. Grant-token proxy can restrict any upstream endpoint by agent and grant scope. (`packages/gateway/src/proxy.ts`, `packages/gateway/gateway.example.yaml`) | WP-02 |
| GW-08 | Gateway audit trail | Partial | **Partial**. Every routing decision is logged with a correlation id, the policy, provider and model, and metered; not yet written to the audit trail. (`core/governance/model_gateway.py`, `observability/metrics.py`) | **Partial**. Evidence entries record model versions and policy evaluations. (`spec/evidence-package.md`, `apps/auth-service/src/lib/evidence/schema-1.0.ts`) | WP-02 |

## Knowledge and vector infrastructure

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| VEC-01 | Vector database | Partial | **Partial**. pgvector with local embeddings. (`core/embeddings.py`, `api/v1/knowledge.py`) | **Gap** | WP-04 |
| VEC-02 | Hybrid retrieval | Gap | **Gap**. No hybrid search or rank fusion. (`api/v1/knowledge.py`) | **Gap** | WP-04 |
| VEC-03 | Metadata filtering | Gap | **Gap**. No metadata filtering. | **Gap** | WP-04 |
| VEC-04 | Automated embedding pipeline | Partial | **Partial**. Same as re-indexing above. (`core/rag/ingest.py`, `core/embeddings_backfill.py`) | **Gap** | WP-04 |
| VEC-05 | Lineage visualisation | Gap | **Gap**. No lineage visualisation. | **Gap** | WP-16 |
| VEC-06 | Knowledge graph integration | Partial | **Partial**. Ownership graph for onboarding; no knowledge-graph store. (`schemas/ownership_graph.schema.json`, `core/agents/business_underwriter/reconciliation.py`) | **Gap** | WP-04 |

## AI engineering, MLOps and LLMOps

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| MLOPS-01 | Governed model registry | Partial | **Partial**. Agent versions with rollback; no model registry. (`core/models/agent.py`, `api/v1/agents.py`) | **Gap** | WP-05 |
| MLOPS-02 | Controlled promotion | Partial | **Partial**. Shadow-to-active promotion and workflow A/B; no environments. (`api/v1/agents.py`, `api/v1/workflow_variants.py`, `core/workflow_ab.py`) | **Gap** | WP-05 |

## Prompt engineering and context management

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| PROMPT-01 | Prompt playground | Partial | **Partial**. Single-agent playground; no side-by-side comparison. (`ui/src/pages/Playground.tsx`) | **Gap** | WP-06 |
| PROMPT-02 | Prompt version history | Partial | **Partial**. Edit history and rollback; no approval tracking. (`api/v1/prompt_templates.py`, `core/models/prompt_template.py`, `api/v1/agents.py`) | **Gap**. Prompt version references in evidence only. | WP-06 |
| PROMPT-03 | Prompt templating | Partial | **Partial**. Variable list and tool-reference validation; no defaults or typed parameters. (`api/v1/prompt_templates.py`, `scripts/check_prompt_tools.py`) | **Gap** | WP-06 |
| PROMPT-04 | Prompt repository | Partial | **Partial**. Central repository with domain RBAC and audit events. (`ui/src/pages/PromptTemplates.tsx`, `api/v1/prompt_templates.py`) | **Gap** | WP-06 |
| PROMPT-05 | Prompt optimisation | Gap | **Gap**. No optimisation tooling. | **Gap** | WP-06 |
| PROMPT-06 | Context-window optimisation | Gap | **Gap**. No context trimming or token management. | **Gap** | WP-06 |
| PROMPT-07 | Prompt evaluation against datasets | Partial | **Partial**. Golden datasets; no prompt-variant comparison. (`evals/runner.py`, `evals/golden_datasets`) | **Gap** | WP-06 |
| PROMPT-08 | Maker-checker for prompts | Gap | **Gap**. No maker-checker for prompts. (`api/v1/prompt_templates.py`, `core/cases/decision_requests.py`) | **Gap**. Four-eyes exists for agent actions, not prompt changes. | WP-06 |
| PROMPT-09 | Dynamic context assembly | Partial | **Partial**. Context from tool outputs; native knowledge base not wired into agent context. (`core/agents/base.py`, `connectors/ops/confluence.py`) | **Gap** | WP-06 |
| PROMPT-10 | Context safeguards | Partial | **Partial**. Pre-model pseudonymisation and untrusted-text guard; no relevance prioritisation. (`core/pii/pseudonymiser.py`, `core/extraction/context.py`) | **Gap** | WP-06 |
| PROMPT-11 | Structured output enforcement | Partial | **Partial**. Strict schemas only for governed-case agents. (`core/domain_schemas.py`, `core/agents/base.py`) | **Gap** | WP-06 |

## Enterprise agent registry and marketplace

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| REG-01 | Searchable agent catalogue | Partial | **Partial**. Search and domain/status filters; no channel, use-case or approval categories. (`ui/src/pages/Agents.tsx`, `api/v1/agents.py`) | **Partial**. Agent list, registry lookup by DID and key, trust-registry search with category filter. (`apps/auth-service/src/routes/agents.ts`, `apps/auth-service/src/routes/registry-lookup.ts`, `apps/auth-service/src/routes/trust-registry.ts`) | WP-05 |
| REG-02 | Agent card | Partial | **Partial**. Platform-level agent card and agent records; no per-agent standardised card. (`api/v1/a2a.py`, `core/models/agent.py`) | **Partial**. A2A agent card, passport claims, per-tool permission manifests; no model or I/O schema fields. (`packages/a2a/src/agent-card.ts`, `spec/agent-passport-1.0.md`, `packages/agent-passport/src/passport.ts`) | WP-05 |
| REG-03 | Agent lifecycle states | Partial | **Partial**. States shadow/active/paused/retired plus maturity label. (`api/v1/agents.py`, `core/models/agent.py`) | **Partial**. Lifecycle states draft, active, suspended and retired with a transition table and recorded reasons, behind AGENT_LIFECYCLE_STATES_ENABLED; no review, approved or published states and no approval attestation type in the registry. (`apps/auth-service/src/routes/agents.ts`, `docs/openapi.yaml`) | WP-05 |
| REG-04 | Agent templates | Partial | **Partial**. Packs and templates for other verticals; no banking pack. (`core/agents/packs/installer.py`, `api/v1/packs.py`, `core/workflows/template_catalog.py`) | **Gap**. Prebuilt tool manifests, no agent templates. (`packages/sdk-ts/src/manifests/index.ts`) | WP-05 |
| REG-05 | Dependency visualisation | Partial | **Partial**. Agent hierarchy chart only. (`ui/src/pages/OrgChart.tsx`) | **Gap**. No visual map. | WP-05 |
| REG-06 | Agent approval workflow | Partial | **Partial**. Shadow gates before promotion; no approval workflow. (`api/v1/agents.py`, `scaling/shadow_comparator.py`) | **Partial**. Accredited-issuer attestations and trust levels; no internal approval workflow. (`apps/auth-service/src/routes/registry-attestations.ts`, `apps/auth-service/src/lib/registry/trust-level.ts`, `spec/attestation-1.0.md`) | WP-05 |
| REG-07 | Environment version management | Partial | **Partial**. Shadow-to-active only. (`api/v1/agents.py`) | **Gap**. Sandbox and live only. | WP-05 |
| REG-08 | Ratings and reliability metrics | Partial | **Partial**. Feedback and tool-success metrics; no ratings. (`core/feedback/collector.py`, `api/v1/agents.py`, `observability/metrics.py`) | **Gap**. No ratings. | WP-05 |

## Agentic development and workflow orchestration

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| ORCH-01 | Visual workflow builder | Partial | **Partial**. Condition, loop, parallel and human steps; authoring is JSON or natural language. (`ui/src/pages/WorkflowCreate.tsx`, `workflows/parser.py`, `ui/src/components/WorkflowBuilder.tsx`) | **Gap** | WP-17 |
| ORCH-02 | Planning and re-planning | Partial | **Partial**. ReAct loop, replanning on failure, intent decomposition. (`core/orchestrator/nexus.py`, `workflows/replanner.py`, `core/langgraph/agent_graph.py`) | **Gap** | WP-17 |
| ORCH-03 | Multi-agent collaboration | Covered | **Covered**. Collaboration, delegation, teams, A2A. (`workflows/collaboration.py`, `api/v1/agents.py`, `api/v1/agent_teams.py`) | **Partial**. Scoped delegation with depth limits; A2A calls. (`apps/auth-service/src/routes/delegate.ts`, `packages/a2a/src/server.ts`, `docs/concepts/delegation.mdx`) | WP-17 |
| ORCH-04 | Tool registration | Partial | **Partial**. Connectors registered with base_url and auth; tools mostly in code; weak schema validation. (`api/v1/connectors.py`, `core/schemas/api.py`, `connectors/composio/adapter.py`) | **Partial**. Schema-validated tool manifests and MCP server registration. (`docs/guides/custom-manifests.mdx`, `spec/manifest-0.6.schema.json`, `apps/auth-service/src/routes/mcp-servers.ts`) | WP-17 |
| ORCH-05 | Sandboxed authorised tool execution | Partial | **Partial**. Strong authorisation and egress checks; execution not sandboxed. (`core/tool_gateway/gateway.py`, `auth/grant_enforcement.py`, `core/security/egress.py`) | **Partial**. enforce, the gateway and the MCP guard check scope, tool, purpose, caps and decisions fail-closed; no sandboxing. (`packages/sdk-ts/src/client.ts`, `packages/gateway/src/proxy.ts`, `packages/mcp-auth/src/resource/guard.ts`) | WP-17 |
| ORCH-06 | Persistent agent memory | Partial | **Partial**. Durable run state; no persistent customer memory. (`api/v1/chat.py`, `core/langgraph/checkpointer.py`) | **Gap** | WP-17 |
| ORCH-07 | Human approval checkpoints | Covered | **Covered**. Approval steps pause and resume from durable checkpoints with multi-step policies and audit. (`workflows/engine.py`, `api/v1/approvals.py`, `core/approvals/agent_run_resume.py`) | **Covered**. Decision grants with OIDC approver sign-in, step-up, four-eyes, dwell time, single-use consumption and audit. (`apps/auth-service/src/routes/decisions.ts`, `apps/auth-service/src/routes/decision-page.ts`, `spec/decision-grant.md`) | WP-17 |
| ORCH-08 | Execution limits and loop detection | Partial | **Partial**. Global step and duration limits from env; no per-agent limits or loop detection. (`core/langgraph/runner.py`, `workflows/engine.py`, `core/content_safety/checker.py`) | **Partial**. Per-tool caps and budgets; no step, duration or loop detection. (`packages/sdk-ts/src/caps/index.ts`, `docs/concepts/caps-and-metering.md`, `apps/auth-service/src/routes/budget.ts`) | WP-17 |
| ORCH-09 | Per-agent tool restrictions | Covered | **Covered**. Per-agent authorised tools, read versus write checks, action modes. (`core/tool_gateway/gateway.py`, `core/governance/action_policy.py`, `core/tool_gateway/provider_gateway.py`) | **Covered**. Manifests map tools to read, write, delete or admin; agent and grant scopes enforce it, and with tool-qualified scopes on, a scope naming a tool covers that tool only. (`docs/concepts/tool-manifests.mdx`, `packages/mcp-auth/src/resource/tool-policy.ts`, `spec/manifest-0.6.md`) | WP-17 |
| ORCH-10 | Debugging console | Partial | **Partial**. Traces and run detail; no step-through or variable inspection. (`ui/src/pages/Playground.tsx`, `ui/src/pages/WorkflowRun.tsx`, `ui/src/pages/Audit.tsx`) | **Partial**. Audit and enforcement log viewers. (`apps/portal/src/pages/audit/AuditLog.tsx`, `apps/portal/src/pages/enforce/EnforceLog.tsx`) | WP-17 |
| ORCH-11 | Agents as callable capabilities | Covered | **Covered**. A2A, MCP, sub-workflow steps, parent/child agents. (`api/v1/a2a.py`, `api/v1/mcp.py`, `mcp-server/src/index.ts`) | **Partial**. Agents exposed over A2A with grant auth. (`packages/a2a/src/server.ts`, `packages/a2a/src/agent-card.ts`) | WP-17 |

## AI evaluation framework and benchmarking

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| EVAL-01 | Evaluation datasets | Partial | **Partial**. Datasets are files; no management API or UI. (`evals/golden_datasets`, `evals/runner.py`, `core/rag/eval.py`) | **Gap** | WP-07 |
| EVAL-02 | Model-graded evaluation | Gap | **Gap**. Deterministic scorers only. (`evals/scorer.py`) | **Gap** | WP-07 |
| EVAL-03 | Deterministic metrics | Partial | **Partial**. Field match with tolerance; no precision, recall or F1. (`evals/scorer.py`, `core/rag/eval.py`) | **Gap** | WP-07 |
| EVAL-04 | Adversarial test sets | Partial | **Partial**. Adversarial suites in CI only. (`tests/security/test_untrusted_content_adversarial.py`, `tests/security/test_underwriter_adversarial.py`, `tests/security/test_disposition_adversarial.py`) | **Gap** | WP-07 |
| EVAL-05 | Feedback correlation | Partial | **Partial**. Feedback not linked to versions. (`core/feedback/collector.py`, `api/v1/agents.py`) | **Gap** | WP-07 |
| EVAL-06 | Pre-promotion regression | Partial | **Partial**. CI and release gates; shadow gate on promotion. (`.github/workflows/rag-eval.yml`, `scripts/release_acceptance.py`, `docs/release_acceptance_gate.md`) | **Gap** | WP-07 |
| EVAL-07 | Comparative dashboards | Partial | **Partial**. Scorecards only; no model ranking. (`ui/src/pages/Evals.tsx`, `scaling/shadow_comparator.py`) | **Gap** | WP-07 |

## Cross-cutting AI governance and asset inventory

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| AIGOV-01 | Live asset inventory | Partial | **Partial**. Separate lists; no unified inventory. (`api/v1/agents.py`, `api/v1/prompt_templates.py`, `api/v1/connectors.py`) | **Partial**. Inventory of agents, grants, MCP servers, manifests; software SBOM and provenance; no AIBOM. (`apps/auth-service/src/routes/agents.ts`, `apps/auth-service/src/routes/mcp-servers.ts`, `.github/workflows/publish-auth-service-image.yml`) | WP-08 |
| AIGOV-02 | Configurable AI policies | Partial | **Partial**. Action risk policy, approval policies, enforcement modes; no unified policy authoring. (`core/governance/action_policy.py`, `core/approvals/policy_engine.py`, `auth/grant_enforcement.py`) | **Partial**. Allow/deny policies with OPA and Cedar backends, tool, purpose and caps rules; no model, prompt or output governance. (`apps/auth-service/src/lib/policy.ts`, `apps/auth-service/src/lib/backends/opa.ts`, `apps/auth-service/src/lib/backends/cedar.ts`) | WP-08 |
| AIGOV-03 | Regulatory risk tiers | Partial | **Partial**. Action risk classes, case tiers and maturity labels; no regulatory risk tiers for AI workloads (the authority layer now carries tool risk tiers on manifests). (`core/governance/action_policy.py`, `core/policy/engine.py`, `core/models/agent.py`) | **Partial**. Tool manifests declare a risk tier and a high-risk tool needs a decision grant on every call in both SDKs and the MCP tool policy; the tiers classify tools, not AI workloads or models. (`spec/manifest-0.6.schema.json`, `packages/sdk-ts/src/manifest.ts`, `packages/sdk-py/src/grantex/manifest.py`) | WP-08 |
| AIGOV-04 | Operator override | Covered | **Covered**. Same control as the baseline override item: halt or throttle by target kind, re-checked at every boundary, throttles counted per dispatch. (`core/governance/operator_override.py`, `api/v1/operator_overrides.py`, `docs/governance/operator-override.md`) | **Partial**. Emergency stop for grant, agent, principal and developer; no model or pipeline throttling. (`apps/auth-service/src/routes/emergency-stop.ts`, `apps/auth-service/src/lib/revocation/emergency-stop.ts`, `apps/auth-service/src/routes/revocations.ts`) | WP-01 |
| AIGOV-05 | Dependency graph | Gap | **Gap**. No dependency graph. | **Gap**. No dependency graph. | WP-08 |
| AIGOV-06 | Model cards | Gap | **Gap**. No model cards. | **Gap**. Software SBOM and provenance only. (`.github/workflows/release.yml`) | WP-08 |

## Security, privacy and AI trust controls

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| TRUST-01 | Prompt-injection guardrails | Partial | **Partial**. Strong indirect-injection controls for governed cases only; elsewhere a regex check; not configurable. (`core/extraction/context.py`, `docs/security/untrusted-content.md`, `core/agent_generator.py`) | **Gap** | WP-03 |
| TRUST-02 | Sensitive-data controls | Covered | **Covered**. PII detection with Aadhaar, PAN and GSTIN recognisers, reversible pre-model tokenisation, masking in audit. (`core/pii/redactor.py`, `core/pii/pseudonymiser.py`, `core/pii/india_recognizers.py`) | **Partial**. DPDP consent lifecycle, pseudonyms in evidence, SD-JWT selective disclosure; no PII detection in AI inputs and outputs. (`apps/auth-service/src/routes/dpdp.ts`, `packages/dpdp/src/index.ts`, `apps/auth-service/src/lib/decisions/personal-data.ts`) | WP-03 |
| TRUST-03 | Factual consistency checks | Partial | **Partial**. Citation-bound checks for case memos only. (`core/agents/business_underwriter/memo.py`) | **Gap** | WP-03 |
| TRUST-04 | Output guardrails | Partial | **Partial**. Opt-in output check that flags but does not block. (`core/content_safety/checker.py`, `api/v1/content_safety.py`, `core/langgraph/runner.py`) | **Gap** | WP-03 |
| TRUST-05 | Agent access policies | Covered | **Covered**. Policies by agent, tool, action risk, company scope and grant. (`auth/grant_enforcement.py`, `auth/scopes.py`, `core/governance/action_policy.py`) | **Covered**. Access governed by agent, principal, tool, connector, purpose, caps, time, OPA and Cedar; no data-classification attribute. (`docs/concepts/tool-manifests.mdx`, `docs/concepts/purpose-bound-grants.md`, `apps/auth-service/src/lib/policy.ts`) | WP-08 |
| TRUST-06 | Approval for high-risk actions | Covered | **Covered**. Approval policies and HITL conditions. (`api/v1/approval_policies.py`, `core/langgraph/hitl_condition.py`, `docs/hitl-conditions.md`) | **Covered**. requires_decision and four_eyes tools need approval; fail closed. (`spec/decision-grant.md`, `apps/auth-service/src/routes/decisions.ts`, `packages/mcp-auth/README.md`) | WP-17 |
| TRUST-07 | Central credential vault | Covered | **Covered**. Credentials encrypted and resolved server-side. (`core/crypto/credential_vault.py`, `core/crypto/tenant_secrets.py`, `core/ai_providers/resolver.py`) | **Covered**. Encrypted vault; the exchange can hand out a short-lived credential reference (VAULT_CREDENTIAL_REFERENCES_ENABLED) that the gateway redeems with its own key and injects upstream (credentialReference: on), so the agent never holds the secret. (`apps/auth-service/src/routes/vault.ts`, `packages/gateway/src/credentials.ts`, `docs/api-reference/vault/resolve.mdx`) | WP-21 |

## AI observability and distributed tracing

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| OBS-01 | Waterfall execution traces | Partial | **Partial**. Text trace only; no waterfall view. (`ui/src/pages/Playground.tsx`, `observability/tracing.py`) | **Gap** | WP-09 |
| OBS-02 | Streaming latency metrics | Gap | **Gap**. Latency only; no time-to-first-token, tokens per second or queue wait. (`core/llm/router.py`) | **Gap** | WP-09 |
| OBS-03 | Live workload console | Partial | **Partial**. Live feed and uptime; no queue depth or SLA countdowns. (`ui/src/pages/Observatory.tsx`, `ui/src/pages/SLAMonitor.tsx`, `observability/metrics.py`) | **Gap** | WP-09 |
| OBS-04 | Scheduled synthetic checks | Partial | **Partial**. Nightly CI eval and health probes; no in-product scheduled evals. (`.github/workflows/rag-eval.yml`, `core/tasks/health_snapshot.py`, `scripts/prod_smoke_check.py`) | **Gap**. On-demand conformance runner only. (`packages/conformance/src/runner.ts`) | WP-07 |
| OBS-05 | Correlation identifiers | Partial | **Partial**. Request id bound to logs and audit rows; not propagated through model or retrieval calls. (`api/middleware/request_id.py`, `core/models/audit.py`, `core/tool_gateway/audit_logger.py`) | **Partial**. Request ids, OpenTelemetry spans, caseId linking evidence. (`apps/auth-service/src/server.ts`, `packages/sdk-ts/src/http.ts`, `apps/auth-service/src/lib/traceAttributes.ts`) | WP-09 |
| OBS-06 | Tamper-evident audit | Covered | **Partial**. Append-only trigger with per-row HMAC; no hash chain; model requests and responses not recorded. (`core/models/audit.py`, `audit/signer.py`, `migrations/versions/v4_8_0_baseline.py`) | **Covered**. Hash-chained audit log and signed, anchored evidence packages. (`apps/auth-service/src/lib/audit-chain.ts`, `apps/auth-service/src/routes/audit.ts`, `spec/evidence-package.md`) | WP-09 |

## FinOps

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| FIN-01 | Usage attribution | Partial | **Partial**. Per-agent ledger with cost-centre and department attribution; no use-case tags. (`scaling/cost_ledger.py`, `api/v1/costs.py`, `api/v1/departments.py`) | **Partial**. Usage metering per developer and per-grant cost units. (`apps/auth-service/src/lib/usage.ts`, `apps/auth-service/src/routes/usage.ts`, `apps/auth-service/src/routes/budget.ts`) | WP-10 |
| FIN-02 | Budget thresholds | Partial | **Partial**. Alerts, per-agent monthly cap, provider daily cap; no application or use-case thresholds. (`core/billing/budget_evaluator.py`, `api/v1/agents.py`, `core/llm/router.py`) | **Partial**. Budget alerts at 50 and 80 percent, caps that deny, plan rate limits. (`docs/guides/budget-controls.mdx`, `packages/sdk-ts/src/caps/index.ts`, `apps/auth-service/src/plugins/dynamicRateLimit.ts`) | WP-10 |
| FIN-03 | Cost comparison and routing | Partial | **Partial**. Quality and latency comparison; no cost comparison or cost-aware routing. (`scaling/shadow_comparator.py`) | **Gap** | WP-10 |
| FIN-04 | Cost forecasting | Gap | **Gap**. No forecasting. | **Gap** | WP-10 |

## Front-end experience, workbenches and channel enablement

| ID | Capability | Combined | AgenticOrg | Grantex | Group |
|---|---|---|---|---|---|
| FE-01 | Configurable conversational interfaces | Partial | **Partial**. Web chat, telephony voice, Teams bot and WhatsApp connector. (`api/v1/chat.py`, `api/v1/voice_runtime.py`, `connectors/microsoft/teams_bot.py`) | **Gap** | WP-11 |
| FE-02 | Intent, entity and context tracking | Partial | **Partial**. Keyword domain detection; no cross-turn context. (`api/v1/chat.py`) | **Gap** | WP-11 |
| FE-03 | Transactional invocation from chat | Partial | **Partial**. Chat runs agents with tools under grants. (`api/v1/chat.py`, `core/langgraph/tool_adapter.py`) | **Gap** | WP-11 |
| FE-04 | Multi-step scenarios | Gap | **Gap**. No dispute, loan or status flows. | **Gap** | WP-11 |
| FE-05 | Conversation summarisation | Gap | **Gap**. No conversation summarisation. | **Gap** | WP-11 |
| FE-06 | Escalation triggers | Partial | **Partial**. Confidence-based escalation to the review queue only. (`api/v1/chat.py`) | **Gap** | WP-11 |
| FE-07 | Supervisor live view | Gap | **Gap**. No supervisor view or takeover. | **Gap** | WP-11 |
| FE-08 | Governed API exposure | Covered | **Covered**. Scoped REST APIs, SDKs, MCP, A2A. (`api/main.py`, `sdk/agenticorg`, `sdk-ts/src`) | **Partial**. Grant-token governance in front of any API. (`packages/gateway/src/proxy.ts`, `docs/openapi.yaml`) | WP-00 |
| FE-09 | Accessibility | Partial | **Partial**. axe checks on some pages; no accessibility statement. (`ui/e2e/documentation.spec.ts`, `ui/e2e/helpers/governed-cases.ts`) | **Gap**. Basic ARIA only. | WP-18 |
| FE-10 | Interaction feedback capture | Partial | **Partial**. Agent feedback only; no satisfaction or sentiment. (`core/feedback/collector.py`, `api/v1/agents.py`) | **Gap** | WP-11 |
| FE-11 | Role-based workbenches | Partial | **Partial**. Dashboards, approvals and case queues; no RM or investigator workbench. (`ui/src/pages/CFODashboard.tsx`, `ui/src/pages/GovernedCases.tsx`, `ui/src/pages/Approvals.tsx`) | **Gap**. Developer portal only. | WP-18 |
| FE-12 | Paused-task review queue | Covered | **Covered**. Approve or reject with notes, expiry, delegation, quorum; limited payload editing. (`ui/src/pages/Approvals.tsx`, `api/v1/approvals.py`, `ui/src/pages/GovernedCaseDetail.tsx`) | **Partial**. Per-request approval page; no central queue. (`apps/auth-service/src/routes/decision-page.ts`, `apps/auth-service/src/routes/prepaid-wallets.ts`) | WP-18 |
| FE-13 | Business configuration console | Partial | **Partial**. Some settings in the UI; policies, flags and HITL conditions API-only. (`ui/src/pages/Settings.tsx`, `ui/src/pages/AIConfig.tsx`, `api/v1/approval_policies.py`) | **Partial**. Portal forms for policies, rules and budgets. (`apps/portal/src/pages/policies/PolicyForm.tsx`, `apps/portal/src/pages/budgets/BudgetList.tsx`) | WP-18 |
| FE-14 | Workbench search | Gap | **Gap**. Simple filters and name search only. (`api/v1/governed_cases.py`, `ui/src/pages/Agents.tsx`) | **Gap** | WP-18 |
| FE-15 | Workbench RBAC | Covered | **Covered**. Role mapping, scope-gated APIs, route guards. (`core/rbac.py`, `auth/scopes.py`, `ui/src/components/ProtectedRoute.tsx`) | **Gap**. Admin scope only; no per-tab roles. (`apps/auth-service/src/plugins/auth.ts`) | WP-18 |
