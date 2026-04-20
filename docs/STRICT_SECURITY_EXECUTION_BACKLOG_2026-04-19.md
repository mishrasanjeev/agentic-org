# Strict Security Execution Backlog - 2026-04-19

## Source

This backlog is derived from:

- [SECURITY_AUDIT_2026-04-19.md](../SECURITY_AUDIT_2026-04-19.md)

This document is an execution backlog only. It does not authorize implementation inside this task. No fixes are started under this document.

## Objective

Eliminate the confirmed security defects identified in the security audit and raise the repository to a defensible enterprise baseline for:

- authentication and session safety
- authorization correctness
- tenant isolation
- webhook trust validation
- SSRF and outbound egress control
- secret handling
- browser hardening
- security regression coverage

## Program Rules

- All work items in this document are mandatory unless the underlying feature is removed from product scope.
- No task may be marked complete without code review, automated verification, and documented residual risk.
- Critical and high findings must be closed before any new enterprise go-live claim.
- If any task reveals confirmed customer data exposure, incident response takes priority over backlog sequencing.
- A task is not complete because code compiles. It is complete only when acceptance criteria and security test gates pass.
- No production release may bypass the release gates defined here.

## Severity Mapping

- `P0`: critical security defect with direct compromise, cross-tenant exposure, or authorization bypass
- `P1`: high security defect with strong abuse potential or major control failure
- `P2`: medium security defect or hardening gap with meaningful risk
- `P3`: low security defect or defensive hygiene item

## Mandatory Execution Order

1. Program controls and containment
2. Session and browser trust-boundary redesign
3. Authorization and tenant-isolation corrections
4. Webhook authentication and ingress trust fixes
5. SSRF and outbound egress containment
6. Knowledge-base data partitioning
7. Redirect, CSP, and configuration hardening
8. Security regression expansion and release certification

## Workstream Summary

- `WS-01`: Program controls, containment, and release governance
- `WS-02`: Session security and browser token handling
- `WS-03`: Authorization and tenant isolation
- `WS-04`: Webhook authentication and ingress integrity
- `WS-05`: SSRF, voice, connectors, and RPA containment
- `WS-06`: Knowledge-layer tenant partitioning
- `WS-07`: Browser and configuration hardening
- `WS-08`: Test coverage, validation, and release gates

## Release Gates

### Gate A: Containment Gate

Must be passed before any customer-facing promotion after starting remediation.

- `WS-01` complete
- all `P0` tasks implemented
- no known active authorization bypass remains
- no known cross-tenant read path remains
- no known unauthenticated public webhook remains

### Gate B: High-Risk Closure Gate

Must be passed before production release.

- all `P1` tasks implemented
- security regression suite added for every fixed `P0` and `P1`
- manual abuse-case verification completed for authz, tenant isolation, webhook auth, and SSRF controls

### Gate C: Enterprise Certification Gate

Must be passed before claiming enterprise-grade security posture.

- all `P2` tasks implemented or explicitly risk-accepted by leadership
- browser hardening validated
- redirect allowlists validated
- secrets and environment controls validated
- security test evidence archived

## Detailed Backlog

## WS-01: Program Controls, Containment, and Release Governance

### SEC-P0-001: Open a security remediation program and freeze risky release claims

**Priority**

- `P0`

**Source Findings**

- applies to all findings in the source audit

**Purpose**

Create a formal security remediation stream so security fixes are sequenced, owned, verified, and not diluted by unrelated roadmap work.

**Scope**

- define program owner
- assign engineering owners by workstream
- assign security reviewer
- assign QA/security validation owner
- freeze any marketing or sales claims that imply enterprise-grade security until Gate C is passed

**Dependencies**

- none

**Deliverables**

- remediation owner matrix
- issue tracker epic for each workstream
- release hold note tied to this backlog

**Acceptance Criteria**

- every task in this document has an owner
- all `P0` and `P1` items are tracked individually
- product leadership acknowledges release gating in writing

### SEC-P0-002: Establish incident triage criteria for discovered data exposure

**Priority**

- `P0`

**Source Findings**

- `CRITICAL-02`
- `CRITICAL-03`
- `HIGH-07`
- `HIGH-08`

**Purpose**

Several findings could imply prior unauthorized access or cross-tenant exposure. The team must define when remediation becomes incident response.

**Scope**

- define what evidence triggers incident escalation
- define log review window
- define notification decision process

**Dependencies**

- `SEC-P0-001`

**Acceptance Criteria**

- written incident decision tree exists
- engineering and security leads know the escalation threshold

## WS-02: Session Security and Browser Token Handling

### SEC-P0-003: Replace browser bearer-token storage with secure session architecture

**Priority**

- `P0`

**Source Findings**

- `CRITICAL-01`

**Purpose**

Eliminate bearer token theft via browser JavaScript access.

**Scope**

- redesign auth to use `HttpOnly`, `Secure`, `SameSite` session cookies
- stop persisting access tokens in `localStorage`
- stop reading bearer token from `localStorage` in the API client
- remove token persistence from invite acceptance and SSO callback flows
- define CSRF strategy for cookie-based auth

**Primary Files Impacted**

- `ui/src/contexts/AuthContext.tsx`
- `ui/src/lib/api.ts`
- `ui/src/pages/SSOCallback.tsx`
- `ui/src/pages/InviteAccept.tsx`
- related backend auth/session endpoints

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- authenticated flow works without `localStorage` token access
- XSS simulation cannot read the session secret
- logout invalidates the server-side session
- refresh and tab restore behavior are tested

**Acceptance Criteria**

- no auth token is stored in `localStorage`
- no auth token is stored in `sessionStorage`
- no frontend request interceptor reads a bearer token from browser storage
- session cookies are `HttpOnly`, `Secure`, and `SameSite`
- CSRF protection exists and is tested

### SEC-P1-004: Redesign SSO callback to use one-time exchange instead of direct JWT-in-fragment persistence

**Priority**

- `P1`

**Source Findings**

- `CRITICAL-01`

**Purpose**

Reduce exposure of SSO session material in browser-visible locations.

**Scope**

- remove durable JWT handling from `#/token=...` flow
- exchange short-lived callback material server-side
- ensure callback artifacts are single-use and rapidly expire

**Dependencies**

- `SEC-P0-003`

**Acceptance Criteria**

- SSO callback no longer stores raw session token in browser storage
- callback artifacts are one-time and time-bound
- replay attempts fail

## WS-03: Authorization and Tenant Isolation

### SEC-P0-005: Fix filing approval authorization to bind privileges only to the current caller

**Priority**

- `P0`

**Source Findings**

- `CRITICAL-02`

**Purpose**

Remove direct authorization bypass from filing approvals and rejections.

**Scope**

- correct `approve_filing`
- correct `reject_filing`
- audit nearby company-role checks for the same anti-pattern
- validate role lookup by canonical user identity only

**Primary Files Impacted**

- `api/v1/companies.py`
- related tests under `tests/`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- positive tests for actual partners/managers
- negative tests for unrelated tenant users
- negative tests for users absent from the company role map
- regression tests for both email-keyed and UUID-keyed role maps

**Acceptance Criteria**

- callers cannot inherit authority from another user's role entry
- approval and rejection paths behave consistently
- unauthorized users receive `403`

### SEC-P0-006: Eliminate global unscoped CDC event storage and restore tenant-bound ownership

**Priority**

- `P0`

**Source Findings**

- `CRITICAL-03`

**Purpose**

Fix cross-tenant CDC event exposure.

**Scope**

- remove or replace global `_event_store`
- persist CDC events with tenant ownership
- require tenant context at ingest and read time
- remove empty `tenant_id=""` trigger evaluation

**Primary Files Impacted**

- `core/cdc/receiver.py`
- `api/v1/cdc_webhooks.py`
- related trigger code and tests

**Dependencies**

- `SEC-P0-002`

**Required Verification**

- cross-tenant reads are impossible
- trigger evaluation uses explicit tenant ownership
- pagination and filters operate only within tenant data

**Acceptance Criteria**

- CDC storage is tenant-scoped
- `GET /cdc/events` returns only caller-tenant data
- no execution path evaluates CDC triggers with blank tenant context

### SEC-P1-007: Eliminate global unscoped RPA execution history

**Priority**

- `P1`

**Source Findings**

- `HIGH-08`

**Purpose**

Remove multi-tenant leakage from the RPA history surface.

**Scope**

- replace `_execution_history` with tenant-owned persistence
- ensure read API filters at the data layer, not in memory
- review any other shared in-memory operational stores exposed through APIs

**Primary Files Impacted**

- `api/v1/rpa.py`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- tenant A cannot read tenant B execution history
- failed execution payloads do not leak tenant B metadata

**Acceptance Criteria**

- no cross-tenant RPA history visibility remains

### SEC-P1-008: Restrict voice configuration read/write to privileged operators and mask secrets by default

**Priority**

- `P1`

**Source Findings**

- `HIGH-05`

**Purpose**

Prevent tenant-wide secret exposure and unauthorized configuration changes.

**Scope**

- require tenant admin or equivalent privileged scope for voice config endpoints
- stop returning raw secrets to ordinary UI/API callers
- move voice secrets into the platform secret-management path

**Primary Files Impacted**

- `api/v1/voice.py`
- relevant secret-management modules under `core/crypto/`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- non-admin users cannot read or update voice config
- privileged users receive masked secret views unless explicitly rotating
- secret material is not serialized back to the client by default

**Acceptance Criteria**

- ordinary tenant users cannot read or modify tenant voice secrets
- stored voice secrets are managed through approved secret handling

## WS-04: Webhook Authentication and Ingress Integrity

### SEC-P0-009: Make SendGrid webhook verification fail closed

**Priority**

- `P0`

**Source Findings**

- `HIGH-04`

**Purpose**

Remove the current bypass where missing configuration causes webhook authentication to succeed.

**Scope**

- change verification behavior so missing verification material rejects requests
- define explicit local-development strategy that does not bleed into production behavior

**Primary Files Impacted**

- `api/v1/webhooks.py`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- missing verification key returns reject behavior
- valid signed webhook succeeds
- invalid signature fails

**Acceptance Criteria**

- there is no fail-open branch in SendGrid webhook verification

### SEC-P0-010: Add mandatory authentication to Mailchimp and MoEngage webhook endpoints

**Priority**

- `P0`

**Source Findings**

- `HIGH-04`

**Purpose**

Prevent spoofed campaign/webhook events from untrusted callers.

**Scope**

- implement provider-supported signature validation or equivalent secret validation
- define replay protection where provider semantics allow it
- reject unsigned or invalid requests

**Primary Files Impacted**

- `api/v1/webhooks.py`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- unsigned requests fail
- malformed signatures fail
- replay attempts are handled or explicitly bounded

**Acceptance Criteria**

- every public webhook endpoint has enforced request authenticity validation

### SEC-P1-011: Review event-driven workflow resume paths for forged input abuse

**Priority**

- `P1`

**Source Findings**

- `HIGH-04`

**Purpose**

Webhook spoofing currently feeds workflow wake-up logic. After auth fixes, the resume surface still needs explicit abuse testing.

**Scope**

- verify only authenticated webhook events can trigger workflow resumes
- test event payload tampering and duplicate delivery handling

**Dependencies**

- `SEC-P0-009`
- `SEC-P0-010`

**Acceptance Criteria**

- workflow resume behavior is covered by authenticated-webhook regression tests

## WS-05: SSRF, Voice, Connectors, and RPA Containment

### SEC-P1-012: Eliminate raw arbitrary host probing from voice custom SIP test flow

**Priority**

- `P1`

**Source Findings**

- `HIGH-06`

**Purpose**

Remove the backend's ability to act as an internal scanner through the SIP reachability probe.

**Scope**

- block private and reserved address space
- decide whether custom SIP probes remain supported at all
- if retained, enforce allowlists and strict validation
- require privileged role to run connection tests

**Primary Files Impacted**

- `api/v1/voice.py`

**Dependencies**

- `SEC-P1-008`

**Required Verification**

- RFC1918, loopback, link-local, and metadata-style destinations are rejected
- hostname rebinding and encoded-host edge cases are covered

**Acceptance Criteria**

- voice test flow cannot be used to probe arbitrary internal hosts

### SEC-P1-013: Constrain connector `base_url` overrides and connector test egress

**Priority**

- `P1`

**Source Findings**

- `MEDIUM-12`

**Purpose**

Prevent connector admin flows from becoming a server-side arbitrary request primitive.

**Scope**

- define supported connector host policy
- validate `base_url` against connector-specific allowlists or patterns
- block internal/private IP resolution
- review `health_check` behavior for safe request paths

**Primary Files Impacted**

- `api/v1/connectors.py`
- `connectors/framework/base_connector.py`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- connector creation rejects internal/private targets
- connector test flow cannot hit arbitrary hosts
- allowed legitimate connector hosts still function

**Acceptance Criteria**

- connector test path is no longer an open egress primitive

### SEC-P1-014: Restrict generic RPA portal automation to approved domains and privileged roles

**Priority**

- `P1`

**Source Findings**

- `HIGH-09`

**Purpose**

Reduce abuse of server-side browser automation.

**Scope**

- require privileged role to run RPA automation
- define allowlists for automatable domains or portal classes
- isolate browser workers from sensitive internal networks
- set download, navigation, and redirect safety policy

**Primary Files Impacted**

- `api/v1/rpa.py`
- `core/rpa/executor.py`
- `rpa/scripts/generic_portal.py`

**Dependencies**

- `SEC-P0-001`

**Required Verification**

- unauthorized users cannot run RPA jobs
- unapproved domains are rejected
- browser automation cannot access blocked internal destinations

**Acceptance Criteria**

- generic RPA automation is no longer available as an unrestricted server-side browser

## WS-06: Knowledge-Layer Tenant Partitioning

### SEC-P1-015: Replace shared `default` knowledge dataset usage with tenant-scoped datasets

**Priority**

- `P1`

**Source Findings**

- `HIGH-07`

**Purpose**

Restore hard tenant boundaries for external knowledge storage.

**Scope**

- remove use of shared `datasets/default`
- assign deterministic tenant-owned dataset identifiers
- enforce tenant ownership on upload, list, search, and delete
- review existing external KB documents for cross-tenant co-mingling

**Primary Files Impacted**

- `api/v1/knowledge.py`
- related data model and migration code if required

**Dependencies**

- `SEC-P0-002`

**Required Verification**

- tenant A cannot search, list, or delete tenant B documents
- migrations or backfill logic preserve document ownership

**Acceptance Criteria**

- there is no shared external knowledge dataset across tenants

### SEC-P1-016: Run retrospective ownership validation for existing knowledge assets

**Priority**

- `P1`

**Source Findings**

- `HIGH-07`

**Purpose**

If the current shared dataset already contains customer documents, ownership must be reconstructed and validated.

**Scope**

- inventory existing KB assets
- map each asset to tenant ownership
- identify orphaned or ambiguous records
- define cleanup or incident path for ambiguous assets

**Dependencies**

- `SEC-P1-015`

**Acceptance Criteria**

- a complete ownership report exists for existing knowledge assets
- ambiguous or cross-tenant assets are resolved before release

## WS-07: Browser and Configuration Hardening

### SEC-P2-017: Replace query-string invite and reset tokens with safer completion flows

**Priority**

- `P2`

**Source Findings**

- `MEDIUM-10`

**Purpose**

Reduce leakage of account bootstrap and recovery tokens.

**Scope**

- redesign invite and reset flows around opaque one-time codes or server-side exchange
- remove long-lived bearer tokens from query strings

**Primary Files Impacted**

- `api/v1/org.py`
- `api/v1/auth.py`
- related UI pages and email templates

**Dependencies**

- `SEC-P0-003`

**Acceptance Criteria**

- invite and reset links no longer expose bearer tokens in query parameters

### SEC-P2-018: Validate all billing redirect and return URLs against strict allowlists

**Priority**

- `P2`

**Source Findings**

- `MEDIUM-11`

**Purpose**

Prevent open redirect and phishing abuse around billing flows.

**Scope**

- define approved billing callback destinations
- enforce first-party domain/path allowlists
- reject arbitrary user-supplied URLs

**Primary Files Impacted**

- `api/v1/billing.py`
- `core/billing/stripe_client.py`

**Dependencies**

- `SEC-P0-001`

**Acceptance Criteria**

- arbitrary `success_url`, `cancel_url`, and `return_url` values are rejected

### SEC-P2-019: Replace weak CSP allowances with nonce or hash based policy

**Priority**

- `P2`

**Source Findings**

- `MEDIUM-13`

**Purpose**

Improve browser-side resistance to script injection.

**Scope**

- remove `'unsafe-inline'` from script policy where feasible
- remove `'unsafe-eval'`
- adjust frontend/tooling to operate under strict CSP

**Primary Files Impacted**

- `ui/nginx.conf`
- frontend runtime/config as needed

**Dependencies**

- `SEC-P0-003`

**Acceptance Criteria**

- production CSP no longer relies on `'unsafe-inline'` or `'unsafe-eval'` for scripts

### SEC-P3-020: Stop public domain enumeration through branding lookup behavior

**Priority**

- `P3`

**Source Findings**

- `LOW-14`

**Purpose**

Reduce public tenant-domain reconnaissance.

**Scope**

- stop trusting arbitrary `host` query input for public domain lookup
- use trusted request host resolution under proxy-aware configuration
- rate-limit or normalize no-match behavior

**Primary Files Impacted**

- `api/v1/branding.py`

**Dependencies**

- `SEC-P0-001`

**Acceptance Criteria**

- branding lookup does not expose custom-domain existence through arbitrary user input

### SEC-P3-021: Remove predictable secret defaults from tracked runtime configuration

**Priority**

- `P3`

**Source Findings**

- `LOW-15`

**Purpose**

Reduce insecure deployment drift and remove security-critical fallback assumptions.

**Scope**

- remove predictable tracked secret values from compose/runtime examples where appropriate
- fail closed in security-critical code when required secrets are absent
- review auth-supporting fallback defaults

**Primary Files Impacted**

- `docker-compose.yml`
- `auth/jwt.py`
- `core/auth_state.py`
- any related config modules

**Dependencies**

- `SEC-P0-001`

**Acceptance Criteria**

- no security-critical runtime path depends on predictable hard-coded secret fallback

## WS-08: Test Coverage, Validation, and Release Certification

### SEC-P0-022: Add mandatory regression tests for every `P0` and `P1` finding

**Priority**

- `P0`

**Source Findings**

- all `P0` and `P1` items

**Purpose**

Prevent reintroduction of the highest-risk defects.

**Scope**

- auth/session tests
- authz negative tests
- tenant isolation tests
- webhook signature tests
- SSRF denial tests
- privileged-access enforcement tests

**Dependencies**

- implementation tasks for each finding

**Acceptance Criteria**

- every `P0` and `P1` finding maps to one or more automated regression tests
- tests fail on the pre-fix behavior and pass on the corrected behavior

### SEC-P1-023: Build a dedicated abuse-case security suite for enterprise release gating

**Priority**

- `P1`

**Source Findings**

- all `P0` and `P1` items

**Purpose**

Unit tests alone are not enough. The program needs an abuse-oriented suite that validates trust boundaries end to end.

**Scope**

- session theft resistance assumptions
- cross-tenant read/write attempts
- forged webhook attempts
- internal-address SSRF attempts
- unprivileged config/read attempts
- redirect abuse attempts

**Dependencies**

- `SEC-P0-022`

**Acceptance Criteria**

- a release-blocking security abuse suite exists and is run in CI

### SEC-P1-024: Require manual security sign-off for authz, tenant isolation, and egress controls

**Priority**

- `P1`

**Purpose**

Certain security properties must be manually reviewed even after automated test coverage exists.

**Scope**

- manual review of session model
- manual review of company approval authz
- manual review of CDC and RPA tenant isolation
- manual review of webhook authentication
- manual review of voice/connector/RPA egress restrictions

**Dependencies**

- relevant implementation tasks complete

**Acceptance Criteria**

- security reviewer signs off each control area
- unresolved residual risks are documented explicitly

### SEC-P2-025: Produce final remediation evidence pack

**Priority**

- `P2`

**Purpose**

Create auditable proof that the fixes were implemented and validated.

**Scope**

- link merged changes to finding IDs
- attach test evidence
- attach reviewer sign-off
- record any accepted residual risk

**Dependencies**

- `SEC-P0-022`
- `SEC-P1-023`
- `SEC-P1-024`

**Acceptance Criteria**

- a complete remediation evidence pack exists for internal audit and enterprise diligence

## Dependency Matrix

- `SEC-P0-001` is the root prerequisite for the full program.
- `SEC-P0-003` must complete before `SEC-P1-004` and should precede `SEC-P2-017` and `SEC-P2-019`.
- `SEC-P0-005`, `SEC-P0-006`, `SEC-P0-009`, and `SEC-P0-010` are the highest-priority remediation tasks after program setup.
- `SEC-P1-008` should precede `SEC-P1-012`.
- `SEC-P1-015` should precede `SEC-P1-016`.
- `SEC-P0-022` depends on the implementation of each corresponding remediation item, but test design should start earlier.

## Done Criteria For Each Task

A task may be marked done only when all of the following are true:

- implementation is merged
- automated tests for the relevant abuse case exist
- negative-path behavior is verified
- logs and error handling do not leak sensitive details
- reviewer sign-off is captured
- the source finding is explicitly linked to the task closure

## Items Explicitly Out Of Scope For This Backlog

- feature redesign unrelated to the security findings
- performance optimization unless required for a security control
- UI polish unrelated to security behavior
- new feature delivery

## Final Program Exit Condition

This backlog is complete only when:

- all `P0` and `P1` tasks are closed
- all cross-tenant exposure paths identified in the audit are closed
- all public ingress paths identified in the audit have authenticated trust checks
- browser session handling no longer exposes bearer tokens to JavaScript
- SSRF-capable paths are blocked or tightly allowlisted
- security regression and abuse suites are green
- Gate C is passed or any remaining `P2` or `P3` items are formally risk-accepted
