# ADR 0002: Multi-tenancy via Postgres Row-Level Security

- **Status**: Accepted
- **Date**: 2025-11-03
- **Deciders**: Sanjeev, Security team

## Context

AgenticOrg is multi-tenant: multiple customer tenants share the same
database cluster. We needed isolation that survives:
1. Bugs in application-layer `WHERE tenant_id = ...` clauses
2. Direct DB-admin access (support tickets that require ad-hoc SQL)
3. ORM model misuse

Options:
- **Schema per tenant**: strong isolation but expensive at 10K+ tenants
  and migrations become operational pain.
- **Database per tenant**: strongest isolation, unworkable cost profile.
- **Application-layer filtering only**: one missing `WHERE` clause
  leaks cross-tenant data. Too easy to get wrong.
- **Postgres Row-Level Security (RLS)**: enforced in the engine, not
  the app. Works with the existing shared-schema model.

## Decision

Use **Postgres RLS** on every table that carries tenant data. Each
session sets the GUC `agenticorg.tenant_id = '<uuid>'` and policies
filter rows based on that setting.

`core/database.py` provides `get_tenant_session(tenant_id)` which
`SET LOCAL`s the GUC in a transaction. All API handlers use this
session factory — they cannot accidentally bypass RLS because they
don't have direct access to the global engine.

## Consequences

- **Good**: Even if someone writes `SELECT * FROM agents` in a query
  window, they only see their tenant's rows. Closed an entire class
  of bugs.
- **Good**: Works with the existing shared-schema, shared-connection
  model — no per-tenant operational overhead.
- **Bad**: All policies must be defined in migrations and kept in
  sync with new tables. We have a test (`tests/security/`) that
  fails CI if a new table doesn't have RLS enabled.
- **Bad**: `BYPASSRLS` role exists for operator use; that role is
  restricted to SREs with break-glass access.
- **Performance note**: RLS adds a WHERE clause to every query. We
  add an index on `tenant_id` to keep this cheap.

## Related

- See `docs/SECURITY.md` section "Tenant isolation".
- See migration `v450_company_rls` for the most recent RLS rollout
  example.
