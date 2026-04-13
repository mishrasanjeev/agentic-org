# AgenticOrg v4.9.0 Roadmap — What's Pending

**Date:** 2026-04-13
**Current version:** v4.8.0 deployed, 51/53 features working (96%)
**Context:** This document captures everything that remains after the April 2026 enterprise readiness program (v4.6.0 → v4.8.0).

---

## Status of what shipped

| Version | What shipped | Score |
|---|---|---|
| v4.6.0 | 17 enterprise gaps closed: departments, delegations, feature flags, budget alerts, audit immutability, agent maturity, i18n columns | — |
| v4.7.0 | SSO (OIDC), approval policies, cost dashboard, invoices, BYOK/CMEK, A/B variants, branding, status page, rate limiting, token refresh, secrets rotation | — |
| v4.7.0 audit | Wired all dead code: approval engine in decide(), pick_variant in workflows, envelope encryption in GSTN creds, delegation in approvals, SSOCallback page, BrandingProvider | — |
| v4.8.0 P1 | 45 new tests, branding hardening, mobile responsive, SAML ADR, Alembic baseline, Locust load harness | — |
| v4.8.0 security | 16 gap analysis findings closed: admin auth on all control planes, billing BOLA, PII masking, RLS on v4.7 tables, health split, worker entrypoint, CI gates | — |
| v4.8.0 recheck | 7 recheck findings closed: org/connectors/config admin auth, Stripe cancel server-side, duplicate routes, deploy gates, connector secret encryption | — |
| v4.8.0 bugs | 33 bugs from Bugs12Apr2026.xlsx: 15 fixed, 16 verified, 2 content | — |
| v4.8.0 functional | KPI NaN fix, billing plans public, Knowledge Base 500 fix, cron/webhooks/SOP list endpoints, demo seed script, ABM tables, generic RPA, CA knowledge docs | 51/53 (96%) |

---

## What's still pending — organized by priority

### P0 — Must fix before enterprise pilot (1-2 weeks)

#### 1. Composio marketplace still returns 0 apps
- **Status:** SDK installed (v0.7.21), API key set, but `libjpeg.so.62` missing at runtime caused an import failure. Runtime dep fix pushed but the deployed image may need a fresh rollout.
- **Fix:** Verify `libjpeg62-turbo` is in the runtime stage. If Composio SDK still fails, the API key (`ak_SVwqxI0...`) may be expired — get a fresh one from app.composio.dev.
- **Effort:** 1 hour

#### 2. Alembic as the sole DDL delivery path
- **Status:** `init_db()` is still the runtime schema authority. Alembic migration files exist but are documentation-only. Fresh environments built from Alembic alone may diverge.
- **Fix:** Generate `alembic.ini` + `env.py`, stamp prod at `v470_sso_invoices`, add CI guard that fails PRs touching models without a migration. Reduce `init_db()` to connectivity check only.
- **Effort:** 3-5 days

#### 3. `async_session_factory()` → `get_tenant_session()` migration
- **Status:** Several API files still use `async_session_factory()` for tenant-scoped queries. This bypasses RLS on tables with FORCE ROW LEVEL SECURITY enabled, returning 0 rows silently.
- **Affected files:** `invoices.py`, `workflow_variants.py`, `sso.py`, `branding.py` (admin CRUD)
- **Fix:** Replace with `get_tenant_session(tid)` in each file. Test that data appears.
- **Effort:** 1 day

#### 4. Auth throttling + token revocation to Redis
- **Status:** Failed auth attempts, signup/login throttle, and token blacklist are all in-memory dicts. They reset on pod restart and don't share across replicas.
- **Fix:** Move to Redis with atomic TTL-backed keys. Keep in-memory as L1 cache.
- **Effort:** 3-5 days

#### 5. Async Redis in SSO/billing handlers
- **Status:** `_get_redis()` returns a sync client used inside async handlers. This blocks the event loop under load.
- **Fix:** Create `_get_async_redis()` using `redis.asyncio` and migrate SSO state store, billing cancel, branding cache.
- **Effort:** 2-3 days

### P1 — Important for competitive parity (2-4 weeks)

#### 6. SAML 2.0 via xmlsec sidecar
- **Status:** ADR approved (`docs/adr/0007-saml-via-xmlsec-sidecar.md`). OIDC works but some Indian enterprise IdPs only speak SAML.
- **Fix:** Build the sidecar Docker image with `python3-saml`, add `auth/sso/saml.py`, wire into `api/v1/sso.py`, test with Shibboleth test IdP.
- **Effort:** 3-6 days

#### 7. Connector secret end-to-end encryption
- **Status:** The gateway now reads from `connector_configs.credentials_encrypted` and decrypts at runtime. But the connector CRUD API (`api/v1/connectors.py`) still writes to the legacy `Connector.auth_config` for backwards compatibility.
- **Fix:** Complete the migration: encrypt on write, backfill existing plaintext rows, remove the fallback read path.
- **Effort:** 3-5 days

#### 8. Real connector health checks
- **Status:** Base connector `health_check()` does a real HTTP probe, but most connectors don't override it with API-specific checks. The test endpoint exists (`POST /connectors/{id}/test`).
- **Fix:** Add per-connector health check overrides for the top 10 connectors (Zoho, Tally, GSTN, HubSpot, Salesforce, Slack, etc.).
- **Effort:** 5-7 days

#### 9. Chat with real tool calls
- **Status:** Chat always returns generic fallback responses because no connectors have credentials configured. Confidence is always 0.6.
- **Fix:** Configure at least Zoho Books credentials (already shared by user) so the CFO chat can execute real API calls. Then test invoice lookup, bank reconciliation, GST filing queries.
- **Effort:** 2-3 days (mostly credential setup + testing)

#### 10. Workflow builder visual improvements
- **Status:** `ui/src/components/WorkflowBuilder.tsx` exists with React Flow, but the UX needs work — template library, domain filter, step validation, preview mode.
- **Fix:** Add template picker, domain filter, step drag-and-drop validation, and a "Preview" tab that shows the workflow definition before deploy.
- **Effort:** 5-10 days (frontend-heavy)

### P2 — Nice to have for launch (4-8 weeks)

#### 11. Multi-region DR rehearsal
- **Status:** Terraform scaffold in `infra/terraform/multi_region/` (commented out). RTO/RPO documented. Never actually applied.
- **Fix:** Un-comment terraform, apply to a staging GCP project, run a cutover rehearsal, document results.
- **Effort:** 5-8 days (SRE + infra)

#### 12. Mobile responsive pass for all pages
- **Status:** All 6 CxO dashboards have responsive headers. Other pages (agents detail, workflows, connectors, approvals) still need touch-friendly layouts.
- **Fix:** Apply Tailwind responsive classes across all main pages.
- **Effort:** 5 days

#### 13. Accessibility (WCAG 2.1 AA) audit
- **Status:** Skip link, focus outlines, reduced-motion, and 44px tap targets are in place. But a full axe-core audit hasn't been run.
- **Fix:** Run axe-core on every page, fix all AA violations, test with NVDA screen reader.
- **Effort:** 3-5 days

#### 14. Load testing in CI
- **Status:** Locust harness exists (`tests/load/locustfile_connectors.py`) but isn't wired into a scheduled GitHub Action.
- **Fix:** Create a nightly CI job that runs Locust against staging and compares p95 against baseline.
- **Effort:** 2-3 days

#### 15. Bug bounty program
- **Status:** Vulnerability disclosure policy published (`docs/VULNERABILITY_DISCLOSURE.md`). No public bug bounty yet.
- **Fix:** Create private program on HackerOne, invite researchers after SSO + BYOK are customer-tested.
- **Effort:** 2 days (setup) + ongoing

### P3 — Future backlog

#### 16. Invoice PDF golden-file tests
- Tests exist for line-item math. PDF byte-level golden test deferred because reportlab embeds timestamps.

#### 17. OIDC callback CI test against real Okta dev tenant
- Stubbed IdP test exists. Real Okta integration needs CI secrets.

#### 18. BYOK integration test against real KMS project
- Envelope encryption has 9 unit tests with mocked KMS. Real KMS test needs Workload Identity Federation in CI.

#### 19. Approval engine E2E with testcontainers Postgres
- Unit tests cover quorum/condition/parallel logic. Real DB fixture test needs Docker-in-Docker in CI.

#### 20. RPA: user-defined custom scripts stored in DB
- Generic portal automator works. Next step: let users define multi-step scripts (login → navigate → fill form → submit → extract) via a visual editor and store them per-tenant.

#### 21. Voice agents (LiveKit integration)
- Code exists in `core/voice/`. Feature-flagged at 50% rollout. Needs LiveKit server deployment and SIP configuration.

#### 22. Real-time WebSocket dashboard updates
- WebSocket feed exists (`api/websocket/feed.py`). Dashboards still poll. Wire them to the WebSocket for live KPI updates.

---

## Metrics

| Metric | v4.6.0 start | v4.8.0 now | Target v4.9.0 |
|---|---|---|---|
| Features working | 21/53 (40%) | 51/53 (96%) | 53/53 (100%) |
| Unit tests | 2508 | ~2900 | 3200+ |
| Security gaps | 16 open | 0 critical, 2 deferred | 0 |
| CodeQL alerts | 4 open | 0 | 0 |
| CxO dashboards | NaN/zeros | Real data, all 6 working | Real-time updates |
| Connectors with creds | 0 | 0 (keys shared but not configured) | 5+ with real API calls |
| Knowledge docs | 0 | 6 (CA/GST compliance) | 20+ (auto-crawled) |
| RPA scripts | 0 | 3 (EPFO, MCA, Generic) | 10+ (user-defined) |

---

## Suggested sprint plan

### Sprint 1 (2 weeks): Foundation
- Composio fix (verify runtime deps)
- `async_session_factory` → `get_tenant_session` migration
- Configure Zoho Books credentials for real chat
- Alembic `env.py` + `stamp` + CI guard

### Sprint 2 (2 weeks): Security hardening
- Auth throttle to Redis
- Async Redis in SSO/billing
- Connector secret end-to-end encryption
- SAML sidecar prototype

### Sprint 3 (2 weeks): Product polish
- Connector health check overrides (top 10)
- Workflow builder UX improvements
- Mobile responsive pass
- Load test CI job

### Sprint 4 (2 weeks): Scale + launch
- Multi-region DR rehearsal
- Accessibility audit
- Bug bounty launch
- Customer pilot preparation
