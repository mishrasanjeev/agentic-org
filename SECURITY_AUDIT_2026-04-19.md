# Security Audit - 2026-04-19

## Scope

Static security review of the repository at `C:\Users\mishr\agentic-org`, with targeted code inspection across:

- `api/`, `auth/`, `core/`, `connectors/`
- `ui/`
- `sdk/`, `sdk-ts/`, `mcp-server/`
- security-related tests and deployment config

This is a source-code security review, not a live penetration test. Findings below are confirmed from code paths present in the repository. Additional runtime, infrastructure, supply-chain, and deployment issues may still exist outside what can be proven from source alone.

## Executive Summary

Confirmed security issues found in this review:

- `3` critical
- `6` high
- `5` medium/low

The most serious problems are:

- browser bearer tokens stored in `localStorage`
- broken authorization in company filing approval/rejection flows
- cross-tenant event leakage through global in-memory CDC/RPA state
- unauthenticated or fail-open webhook ingestion
- SSRF/server-side network probing surfaces in voice and connector flows

## Findings

### CRITICAL-01: Bearer tokens are stored in browser `localStorage`

**Evidence**

- `ui/src/contexts/AuthContext.tsx:30-32`
- `ui/src/contexts/AuthContext.tsx:48-49`
- `ui/src/contexts/AuthContext.tsx:70-71`
- `ui/src/contexts/AuthContext.tsx:88-89`
- `ui/src/contexts/AuthContext.tsx:99`
- `ui/src/contexts/AuthContext.tsx:108`
- `ui/src/lib/api.ts:7`
- `ui/src/pages/SSOCallback.tsx:9`
- `ui/src/pages/SSOCallback.tsx:23-25`
- `ui/src/pages/SSOCallback.tsx:43-48`
- `ui/src/pages/InviteAccept.tsx:51-52`

**Issue**

Access tokens are persisted in `localStorage` and then read back into API requests. The SSO callback also accepts a JWT in the URL fragment and persists it to `localStorage`.

**Impact**

Any XSS in the SPA, third-party script compromise, browser extension compromise, or malicious injected content can steal full-session bearer tokens. Because the token is bearer-based, theft is usually equivalent to full account takeover until expiry or revocation.

**Why this is severe**

The repository also ships a CSP that still allows `'unsafe-inline'` and `'unsafe-eval'`, which materially weakens the browser-side mitigation posture.

**Remediation direction**

Move session handling to `HttpOnly`, `Secure`, `SameSite` cookies, remove token persistence from `localStorage`, and convert the SSO callback to a one-time server exchange.

### CRITICAL-02: Filing approval/rejection authorization is broken

**Evidence**

- `api/v1/companies.py:1035-1045`
- `api/v1/companies.py:1114-1122`

**Issue**

If the current caller's email is not found in `company.user_roles`, the code iterates role values and grants partner or manager authority if any company user has that role. That means the caller is effectively authorized based on another user's role entry.

**Impact**

An authenticated tenant user can approve or reject filings they should not control, as long as the company has at least one partner or manager entry.

**Why this is severe**

This is a direct authorization bypass on a compliance-sensitive workflow.

**Remediation direction**

Authorization must bind to the current caller's identity only. Never infer caller authority by scanning whether the company has some user with the required role.

### CRITICAL-03: CDC events are stored globally and exposed without tenant isolation

**Evidence**

- `core/cdc/receiver.py:12-13`
- `core/cdc/receiver.py:79`
- `core/cdc/receiver.py:85`
- `core/cdc/receiver.py:94`
- `api/v1/cdc_webhooks.py:28-38`

**Issue**

CDC webhook events are appended to a global in-memory `_event_store`. Trigger evaluation is called with `tenant_id=""`, and the read API returns stored events with only connector and event-type filters, not tenant filters.

**Impact**

Users can potentially read CDC events originating from other tenants. This is a direct multi-tenant isolation failure.

**Remediation direction**

Persist CDC events with tenant ownership, require tenant-filtered queries, and remove all global unscoped event storage from request-serving paths.

### HIGH-04: Webhook ingestion is unauthenticated or fail-open

**Evidence**

- `api/v1/webhooks.py:79-94`
- `api/v1/webhooks.py:133`
- `api/v1/webhooks.py:182-223`
- `api/v1/webhooks.py:223-267`

**Issue**

- SendGrid verification returns `True` when `SENDGRID_WEBHOOK_KEY` is unset.
- Mailchimp webhook processing does not verify a signature.
- MoEngage webhook processing does not verify a signature.

**Impact**

Attackers can forge delivery/open/click/bounce events, poison campaign telemetry, and potentially trigger workflow resumes tied to those email events.

**Remediation direction**

Require signature verification on every externally reachable webhook endpoint. Do not ship fail-open verification logic.

### HIGH-05: Any authenticated tenant user can read and overwrite tenant-wide voice secrets

**Evidence**

- `api/v1/voice.py:136-138`
- `api/v1/voice.py:256-271`

**Issue**

Voice configuration and connection-test endpoints depend only on `get_current_tenant`. They are not admin-gated. The saved config includes sensitive telephony/API credentials and is returned back through `GET /voice/config`.

**Impact**

Any authenticated user inside a tenant can:

- read tenant-wide voice credentials
- replace voice credentials
- test outbound connectivity with attacker-supplied endpoints

**Remediation direction**

Require explicit admin or privileged-operator scope for voice configuration and return masked secrets by default.

### HIGH-06: Voice custom SIP connection testing is an SSRF/internal network probing primitive

**Evidence**

- `api/v1/voice.py:136`
- `api/v1/voice.py:205`
- `api/v1/voice.py:233`
- `api/v1/voice.py:240-245`

**Issue**

`POST /voice/test-connection` accepts a custom SIP endpoint, extracts host/port, and opens a raw TCP socket from the server to that target.

**Impact**

Authenticated users can make the backend probe internal IPs, private services, or cloud metadata-adjacent surfaces and use response differences for port scanning or network reconnaissance.

**Remediation direction**

Block private/reserved IP ranges, require strict allowlists for customer-configurable voice endpoints, and move reachability tests behind privileged operations only.

### HIGH-07: Knowledge-base integration appears to use a shared external dataset across tenants

**Evidence**

- `api/v1/knowledge.py:97-100`
- `api/v1/knowledge.py:111`
- `api/v1/knowledge.py:131`
- `api/v1/knowledge.py:146`
- `api/v1/knowledge.py:285`
- `api/v1/knowledge.py:359`
- `api/v1/knowledge.py:471`

**Issue**

RAGFlow operations are performed against `datasets/default` and searches use `dataset_ids: ["default"]`. Upload passes `tenant_id` as a query parameter, but list/search/delete paths do not partition at the dataset level.

**Impact**

If the backing RAGFlow deployment is shared as implied by this code, tenants may be able to search, list, or delete one another's knowledge documents through the external KB layer.

**Remediation direction**

Use dedicated tenant-scoped dataset IDs, not a shared `default` dataset, and enforce ownership checks on every list/search/delete path.

### HIGH-08: RPA execution history is stored globally and returned without tenant filtering

**Evidence**

- `api/v1/rpa.py:109`
- `api/v1/rpa.py:176-184`
- `api/v1/rpa.py:243`

**Issue**

RPA execution history is appended to a global `_execution_history` list. The `list_history` endpoint explicitly notes tenant filtering is a future item, but still returns the shared in-memory history.

**Impact**

Users can see other tenants' RPA execution metadata. Depending on failures and script naming, this can leak sensitive operational activity.

**Remediation direction**

Persist execution history with tenant ownership and reject all reads that are not filtered by tenant at the data layer.

### HIGH-09: Any authenticated tenant user can trigger server-side browser automation against arbitrary portals

**Evidence**

- `api/v1/rpa.py:65`
- `api/v1/rpa.py:147`
- `api/v1/rpa.py:192-197`
- `rpa/scripts/generic_portal.py:105-132`
- `rpa/scripts/generic_portal.py:192-193`
- `core/rpa/executor.py:102-103`

**Issue**

The generic portal automation flow accepts arbitrary `portal_url`, credentials, and optional `target_url`, then launches headless Chromium from the server and browses to those destinations. The API is not admin-gated.

**Impact**

This creates a high-risk abuse surface for:

- SSRF-like browser-based internal access
- credential handling on untrusted destinations
- automated interaction with attacker-controlled sites from trusted server egress

**Remediation direction**

Restrict RPA execution to privileged operators, enforce domain allowlists, and isolate browser automation workers from sensitive internal networks.

### MEDIUM-10: Invite and password-reset bearer tokens are placed in query strings

**Evidence**

- `api/v1/org.py:176-203`
- `api/v1/auth.py:415-424`

**Issue**

Invitation and password-reset links embed the bearer token directly in the URL query string.

**Impact**

Query-string tokens are more likely to leak into:

- browser history
- reverse-proxy logs
- email security scanners
- analytics/referrer chains
- screenshots and support tooling

**Remediation direction**

Use one-time opaque codes, POST-based completion, or short-lived exchange tokens delivered through safer flows.

### MEDIUM-11: Billing flows accept caller-controlled redirect/return URLs

**Evidence**

- `api/v1/billing.py:37-38`
- `api/v1/billing.py:60`
- `api/v1/billing.py:132-136`
- `api/v1/billing.py:331-341`
- `core/billing/stripe_client.py:130-144`
- `core/billing/stripe_client.py:379-383`

**Issue**

Stripe checkout and portal helpers accept `success_url`, `cancel_url`, and `return_url` directly from the caller and pass them through without allowlist validation.

**Impact**

This enables open-redirect style abuse and phishing opportunities after billing actions.

**Remediation direction**

Validate redirect targets against a strict allowlist of first-party domains and approved paths.

### MEDIUM-12: Admin-configurable connector `base_url` values can be abused for SSRF during test/connect flows

**Evidence**

- `api/v1/connectors.py:182`
- `api/v1/connectors.py:194`
- `api/v1/connectors.py:205`
- `api/v1/connectors.py:412-413`
- `connectors/framework/base_connector.py:42`
- `connectors/framework/base_connector.py:44-46`
- `connectors/framework/base_connector.py:89-90`
- `connectors/framework/base_connector.py:108-116`

**Issue**

Connector registration persists an arbitrary `base_url`, connector instances honor that override, and the connector test path calls `connect()` plus `health_check()`, which makes outbound requests to that configured host.

**Impact**

Tenant admins can turn the platform into an SSRF client against arbitrary destinations unless outbound access is heavily fenced at the network layer.

**Remediation direction**

Validate connector destinations, block private address space, and enforce per-connector host allowlists.

### MEDIUM-13: Production CSP still allows `'unsafe-inline'` and `'unsafe-eval'`

**Evidence**

- `ui/nginx.conf:43-48`

**Issue**

The shipped CSP allows inline scripts and `eval`-style execution.

**Impact**

This materially weakens XSS resistance. Combined with `localStorage` session tokens, the blast radius of any front-end injection is much higher.

**Remediation direction**

Adopt nonce- or hash-based CSP, remove `unsafe-eval`, and eliminate inline script dependencies.

### LOW-14: Public branding lookup accepts arbitrary `host` query values, enabling tenant-domain enumeration

**Evidence**

- `api/v1/branding.py:116-118`
- `api/v1/branding.py:151`

**Issue**

The public branding endpoint accepts `host` as a query parameter and uses it to look up `TenantBranding.custom_domain == host`.

**Impact**

Attackers can probe for known or guessed custom domains and enumerate whether those domains are configured in the platform.

**Remediation direction**

Use the actual request host/header path under trusted proxy handling, and rate-limit or anonymize existence checks.

### LOW-15: Predictable development secret values are tracked in repository config

**Evidence**

- `docker-compose.yml:63`
- `docker-compose.yml:87`
- `auth/jwt.py:51`
- `core/auth_state.py:131`

**Issue**

Tracked compose configuration includes predictable secret values, and some auth-supporting code paths still fall back to hard-coded default secret strings when environment configuration is absent.

**Impact**

This is not an immediate exploit by itself, but it increases the probability of insecure deployments using predictable secrets or derived token-blacklist keys.

**Remediation direction**

Remove predictable secret defaults from tracked runtime config and fail closed whenever security-critical secrets are missing.

## Notes On Coverage

- Existing security-oriented tests are present under `tests/security/` and `ui/e2e/security-tests.spec.ts`.
- Those tests do not currently prevent the vulnerabilities above.
- Several of the most serious issues are authorization, tenant-isolation, and trust-boundary defects that should be covered by explicit regression tests after remediation.

## Priority Order

1. Remove browser token storage and redesign session handling.
2. Fix filing authorization logic.
3. Eliminate global unscoped CDC/RPA stores and restore tenant isolation.
4. Lock down webhook verification.
5. Lock down voice/RPA privileged operations and SSRF surfaces.
6. Re-architect knowledge-base tenant partitioning.
7. Tighten redirect validation, CSP, and configuration hardening.
