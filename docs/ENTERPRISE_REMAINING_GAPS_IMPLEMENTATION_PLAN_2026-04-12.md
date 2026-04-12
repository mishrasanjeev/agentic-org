# Enterprise Remaining Gaps Implementation Plan

Date: 2026-04-12

This document translates the remaining enterprise-readiness gaps into implementation-ready work items. It supersedes the stale portions of `docs/ENTERPRISE_GAP_ANALYSIS_RECHECK_2026-04-12.md` by separating:

- findings that are now actually fixed
- findings that remain open
- exact remediation steps
- acceptance criteria and verification for each fix

## Current Status

The following earlier findings appear closed and should not be reopened unless new evidence appears:

- Admin guards were added to `api/v1/connectors.py`, `api/v1/config.py`, and `api/v1/report_schedules.py`.
- `api/v1/org.py` now enforces admin access and validates invite roles with an allowlist.
- The duplicate `GET /billing/invoices` issue was removed from `api/v1/billing.py` in favor of the canonical invoices route.
- Production release gating now includes `approval-gate` for release tags and requires `"status":"healthy"` in `.github/workflows/deploy.yml`.

The remaining blockers below are still open and should be treated as the current enterprise remediation backlog.

## Priority Order

1. `P0` workflow admin authorization gap
2. `P0` connector secret storage and execution path
3. `P0` migration and tenant-isolation drift between Alembic and runtime DDL
4. `P1` non-durable auth, SSO, and billing state using sync Redis or process memory
5. `P1` Stripe cancellation legacy fallback
6. `P2` make the enterprise skill tracked and team-visible

## P0. Workflow Admin Authorization Gap

### Problem

`PUT /workflows/{wf_id}/replan-config` still mutates tenant-wide workflow configuration without explicit admin enforcement.

### Evidence

- `api/v1/workflows.py` defines `@router.put("/workflows/{wf_id}/replan-config")`
- The handler takes `tenant_id = Depends(get_current_tenant)` but does not enforce `require_tenant_admin`
- Other workflow mutation endpoints already do enforce admin checks

### Risk

- Non-admin users can alter workflow execution behavior for the tenant
- This is a control-plane mutation, not a user-local preference
- Enterprise customers will treat this as an authorization defect

### Required Fix

Update `api/v1/workflows.py` so `update_replan_config` uses the same authorization pattern as the other workflow mutation endpoints.

Recommended implementation:

1. Add `require_tenant_admin` to the endpoint signature, matching `create_workflow`, `delete_workflow`, and `generate_workflow_endpoint`.
2. Keep the dependency on the same `def` line if regression tests inspect source text for `Depends`.
3. Add or update tests to prove:
   - admin can update replan config
   - non-admin receives `403`
   - another tenant cannot update the workflow

### Acceptance Criteria

- Non-admin caller cannot change workflow replan settings
- Cross-tenant access is rejected
- Existing workflow admin tests still pass

### Verification

- Targeted pytest for workflow mutation auth
- Negative auth tests, not only happy path

## P0. Connector Secret Storage And Execution Path

### Problem

Connector secrets are still effectively supported in plaintext-style storage and fallback execution paths.

### Evidence

- `api/v1/connectors.py` still persists `auth_config=body.auth_config`
- `api/v1/connectors.py` still persists `secret_ref=body.secret_ref`
- `api/v1/connectors.py` update path still uses dynamic `setattr(...)` assignment
- `core/models/connector.py` still defines plaintext `auth_config`
- `core/tool_gateway/gateway.py` still falls back to `Connector.auth_config`
- `core/tool_gateway/gateway.py` reads `credentials_encrypted` but does not perform an explicit decrypt path using tenant-aware crypto before use

### Risk

- Secrets can end up persisted in legacy plaintext-style fields
- Execution behavior is ambiguous because secure and insecure paths coexist
- Enterprise audits will fail if secret storage and runtime decryption are not explicit and enforced

### Required Fix

Make `connector_configs` the only supported secret storage path and use tenant-aware encryption end to end.

Recommended implementation:

1. Change connector create and update flows in `api/v1/connectors.py`:
   - stop writing secrets to `Connector.auth_config`
   - treat `Connector.auth_config` as deprecated read-only legacy data
   - store secret material in `connector_configs.credentials_encrypted`
   - store non-secret config separately in `connector_configs.config`
2. Use `core.crypto.encrypt_for_tenant` before persistence.
3. In `core/tool_gateway/gateway.py`, explicitly decrypt via `core.crypto.decrypt_for_tenant` at execution time.
4. Remove fallback reads from `Connector.auth_config` after migration is complete.
5. Add a one-time migration or backfill plan:
   - read legacy connector rows
   - encrypt and move secret values into `connector_configs`
   - null or remove legacy plaintext fields if feasible
6. Prevent blind `setattr(...)` updates for secret-bearing fields.

### Acceptance Criteria

- New connector secrets never land in `Connector.auth_config`
- Runtime only executes with decrypted values derived from encrypted storage
- Legacy connector records have a migration path
- No logs, metrics, or exception messages contain raw connector credentials

### Verification

- Tests for create, update, and execute flows
- Negative test proving plaintext `auth_config` is no longer used for new writes
- Regression test covering back-compat only during migration window if temporarily retained

## P0. Migration And Tenant-Isolation Drift

### Problem

Tenant isolation and schema delivery still rely partly on startup-time `init_db()` behavior instead of Alembic as the authoritative source.

### Evidence

- `api/main.py` still calls `init_db()` on startup
- `core/database.py` creates `connector_configs` at runtime
- `core/database.py` applies v4.7 RLS and policies at runtime
- `migrations/versions/v4_7_0_sso_approvals_invoices.py` creates tenant-scoped tables but does not create the matching RLS policies there

### Risk

- Fresh environments can differ based on whether startup code ran
- Migration order becomes environment-dependent
- RLS may be missing in environments where Alembic ran but startup DDL or policy code did not
- Enterprise deployment governance requires migrations to fully represent schema and isolation state

### Required Fix

Move the source of truth for schema and tenant isolation into Alembic.

Recommended implementation:

1. Create an Alembic migration for `connector_configs` if one does not already exist.
2. Add RLS enablement and policy creation directly to the migration chain for:
   - `sso_configs`
   - `approval_policies`
   - `approval_steps`
   - `invoices`
   - `tenant_branding`
   - `workflow_variants`
3. Keep `init_db()` only as temporary compatibility scaffolding if needed, but align it exactly with the migrations.
4. Add comments or a follow-up ticket to remove runtime DDL once all deployed environments are migrated.

### Acceptance Criteria

- A fresh database built from Alembic alone has the required tables, indexes, RLS, and policies
- Startup no longer introduces enterprise-critical schema or policy deltas that migrations do not represent
- Migration upgrade path works from the previous released version

### Verification

- Validate on a clean database with Alembic only
- Validate on an upgrade from the previous release
- Confirm RLS policies exist via integration or SQL-level checks

## P1. Non-Durable Auth, SSO, And Billing State

### Problem

Several security and session-sensitive paths still rely on synchronous Redis clients inside async handlers or on in-memory state that does not survive restarts or horizontal scaling.

### Evidence

- `core/billing/usage_tracker.py` exposes a sync Redis client
- `api/v1/sso.py` uses that helper inside async handlers
- `api/v1/billing.py` cancellation flow also uses that sync Redis helper
- `api/v1/auth.py` signup, login, and reset throttling use in-process dictionaries
- `auth/grantex_middleware.py` tracks failed auth attempts in process memory
- `auth/jwt.py` still uses in-memory token blacklist as a primary path with Redis best effort

### Risk

- Rate limiting and revocation behavior changes across pods
- Restarting a process resets security state
- Sync Redis calls inside async paths can hurt latency and load behavior
- Enterprise environments will expect durable, shared enforcement

### Required Fix

Standardize on durable shared state for auth- and session-sensitive controls.

Recommended implementation:

1. Introduce an async Redis access layer for API handlers.
2. Move SSO state storage to async Redis only, with fail-closed behavior when unavailable.
3. Replace in-memory signup, login, password reset, and middleware failure tracking with shared Redis-backed counters and expirations.
4. Make token revocation primarily Redis-backed, with any in-memory cache treated as a local performance optimization rather than the security source of truth.
5. Add explicit timeout and error handling so auth flows fail safely rather than silently degrading.

### Acceptance Criteria

- Auth throttling works consistently across multiple app instances
- Token revocation survives process restart
- Async handlers do not call sync Redis helpers
- SSO state handling is durable and fail-closed

### Verification

- Unit and integration tests for throttling and token revocation
- Async path tests for SSO and billing cancellation
- Multi-instance or simulated shared-state tests if feasible

## P1. Stripe Cancellation Legacy Fallback

### Problem

Stripe cancellation still falls back to a caller-supplied `subscription_id` if no server-side subscription mapping is present.

### Evidence

- `api/v1/billing.py` resolves server-side `stripe_subscription_id`
- If not found, it still falls back to the request body `subscription_id`
- The code explicitly labels this as legacy compatibility behavior

### Risk

- Cross-tenant cancellation risk remains for legacy tenants
- A security exception is still embedded in the normal runtime path
- This weakens the otherwise correct tenant-bound cancellation model

### Required Fix

Remove the insecure fallback and replace it with a controlled migration path.

Recommended implementation:

1. Change the cancel endpoint to reject cancellation when no server-side subscription mapping exists.
2. Add a migration or repair job to populate missing `stripe_subscription_id` values for legacy tenants.
3. Add operational tooling or admin-only backfill endpoint if required, but do not leave the insecure public path in place.
4. Add explicit telemetry for tenants missing required billing state so operations can remediate them.

### Acceptance Criteria

- Public cancellation flow never trusts caller-supplied subscription IDs
- Legacy tenants have a supported recovery or backfill path
- Missing server-side billing state yields a safe error, not insecure behavior

### Verification

- Negative test proving caller-supplied `subscription_id` is ignored or rejected
- Integration test for normal cancellation using server-side state

## P2. Make The Enterprise Skill Team-Visible

### Problem

The local enterprise skill has been improved, but `.claude/` is currently gitignored, so the updated guidance is not shared through the repository by default.

### Evidence

- `.claude/skills/agenticorg-enterprise/SKILL.md` was updated locally
- `.gitignore` includes `.claude/`

### Risk

- Only the current workstation benefits from the tightened guidance
- Team behavior can drift even if the skill is correct locally

### Required Fix

Choose one of these options:

1. Preferred: mirror the important enterprise rules into tracked `CLAUDE.md`
2. Alternative: stop ignoring the specific tracked skill path
3. Alternative: keep the skill local but copy the rules into a repo-tracked engineering standards doc

### Acceptance Criteria

- The enterprise coding rules are visible to every developer and agent using the repo
- The guidance reflects the current live risks, not stale ones

## Suggested Execution Plan

### Phase 1

- Fix workflow admin enforcement
- Remove Stripe fallback

These are the smallest high-value security closures.

### Phase 2

- Rework connector secret persistence and execution
- Add migration and backfill for connector secret data

This is the highest-effort security fix and should be done deliberately.

### Phase 3

- Move schema and RLS source of truth into Alembic
- Reduce `init_db()` to compatibility scaffolding only

This stabilizes deployment correctness and tenant isolation.

### Phase 4

- Replace sync Redis and in-memory auth controls with durable async shared state

This improves scale-out correctness and operational reliability.

### Phase 5

- Publish the enterprise guidance in a tracked location

This keeps future changes from reintroducing the same problems.

## Definition Of Done For Enterprise Recheck

The enterprise recheck should only be marked complete when all of the following are true:

- No tenant-wide mutation endpoint is missing server-side admin enforcement
- Connector secrets are encrypted at rest and decrypted only at execution time
- Alembic alone can build the required schema and tenant-isolation policies
- Auth throttling, token revocation, and SSO state survive restarts and multiple instances
- Billing cancellation never trusts caller-supplied object identifiers
- Enterprise coding guidance is repo-visible and current

## Recommended Deliverables

Each gap above should produce:

- one code PR
- one focused test set
- one short release note or migration note if behavior changes

Do not combine all fixes into one oversized PR. The connector secret work and migration/RLS work should be reviewed separately.
