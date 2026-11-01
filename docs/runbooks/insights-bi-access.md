<!-- SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0 -->
# Insights BI database access

Tenant administrators can give a BI tool (Metabase, Superset, Power BI and the
like) its own read-only SQL login on a read replica of the production
database. This runbook covers turning the feature on, the network and TLS
set-up, connecting Metabase, revoking a login and what to do in an emergency.

Code: `core/insights/bi_access.py`, `api/v1/insights_bi.py`, migration
`migrations/versions/v6_z108_insights_bi.py`, page
`ui/src/pages/InsightsBiAccess.tsx` (`/dashboard/insights/bi-access`).
Tests: `tests/unit/test_insights_bi_access.py`,
`tests/integration/test_insights_bi_postgres.py`.

## How the isolation works

The application's own tenant policies compare `tenant_id` with the
`agenticorg.tenant_id` setting. Any SQL session can `SET` that, so those
policies cannot isolate a login that a customer controls. A BI login is tied to
its tenant by its own name instead:

- Every BI login is a member of the NOLOGIN group `agenticorg_bi` (inherit
  only; it cannot `SET ROLE` to the group). The group may read the five views
  in schema `insights_bi` and, because the views are `security_invoker`, the
  exposed columns of their source tables. Nothing else: no table-level
  privilege anywhere, no partition of `run_facts`, no write privilege, no
  provisioning function.
- `insights_bi_logins` maps each login (`role_name`) to its tenant.
  `public.insights_bi_tenant()` returns the tenant of `session_user` while the
  mapping is not revoked.
- Each source table (`run_facts`, `insights_daily`, `spend_usage_rollups`,
  `agents`, `hitl_queue`) has a permissive and a RESTRICTIVE `FOR SELECT TO
  agenticorg_bi` policy, both `tenant_id = (SELECT public.insights_bi_tenant())`.
  The restrictive one is what stops a BI session that sets
  `agenticorg.tenant_id` (or `agenticorg.platform_ctx`) from widening its rows.
- Logins are created and dropped only by `public.insights_bi_create_login` and
  `public.insights_bi_revoke_login`, SECURITY DEFINER functions owned by the
  NOLOGIN `agenticorg_bi_admin` role (CREATEROLE, ADMIN OPTION on the group).
  On PostgreSQL 16 a CREATEROLE role administers only the roles it created, so
  these functions cannot alter any other role. The application holds EXECUTE
  only, never CREATEROLE. Both functions refuse a tenant that differs from the
  session's tenant context.
- The application generates the password, shows it to the administrator once
  and sends the database only its SCRAM-SHA-256 verifier. The plaintext is not
  stored anywhere and is in no log or audit row.
- Each login gets `search_path = insights_bi`, `statement_timeout = 30s`,
  `default_transaction_read_only = on` and a connection limit of 3.

The views:

| view | source | columns |
|---|---|---|
| `insights_bi.run_facts` | `run_facts` | every column except `correlation_id`, `trace_id` and `run_key` |
| `insights_bi.daily` | `insights_daily` | every column |
| `insights_bi.spend_daily` | `spend_usage_rollups` | day, provider, model, usage_type, unit, quantity, amount_inr, agent_id, use_case, record_count |
| `insights_bi.agents` | `agents` | id, name, agent_type, domain, status, company_id |
| `insights_bi.approvals` | `hitl_queue` | id, agent_id, trigger_type, priority, status, assignee_role, created_at, decision, decision_at |

## Prerequisites

1. Migration `v6z108_insights_bi` is applied on the primary (it runs with the
   normal deploy migration; the migration user needs CREATEROLE, which the
   Cloud SQL `cloudsqlsuperuser` group gives, and must own the database, the
   `public` schema and the five source tables, as it does in production).
2. `scripts/create_app_role.sql` has been re-run after the migration if the
   application connects as `agenticorg_app` (the migration grants EXECUTE on the
   two functions to `agenticorg_app` itself when that role exists; the script
   keeps the mapping table read-only for it).
3. `PUBLIC` has no `CREATE` on schema `public` (the PostgreSQL 15+ default;
   `scripts/create_app_role.sql` refuses to finish otherwise). Check:

   ```sql
   SELECT has_schema_privilege('agenticorg_bi', 'public', 'CREATE');  -- must be false
   ```

## 1. Create the read replica

BI traffic never goes to the primary. Create a Cloud SQL read replica of the
primary instance (same PostgreSQL major version, 16), in the primary's region:

```bash
gcloud sql instances create <replica-name> \
  --master-instance-name=<primary-instance> \
  --region=<primary-region> \
  --tier=<tier> \
  --availability-type=ZONAL \
  --assign-ip
```

Roles, passwords, grants, policies and data all replicate from the primary:
logins are created on the primary (by the application) and can connect to the
replica once replication has caught up (normally seconds). Revocation reaches
the replica the same way.

Size the replica for BI queries, not for the application. Every BI statement
is cut off after 30 seconds, and each login has at most 3 connections.

## 2. Require TLS on the replica

Refuse unencrypted connections on the replica:

```bash
gcloud sql instances patch <replica-name> --ssl-mode=ENCRYPTED_ONLY
```

(Older gcloud releases: `--require-ssl`.) The connection details the page shows
always say `sslmode=require`. BI tools that can verify the server certificate
should use `verify-ca` with the replica's server CA
(`gcloud sql instances describe <replica-name> --format="value(serverCaCert.cert)"`).

## 3. Allow a tenant's BI host to connect

The replica accepts connections only from its authorised networks. When a
tenant asks for BI access, get the public egress address (or CIDR) of their BI
host (for Metabase Cloud, the published egress IPs of their region), and add it.
`--authorized-networks` REPLACES the list, so always read it first:

```bash
gcloud sql instances describe <replica-name> \
  --format="value(settings.ipConfiguration.authorizedNetworks[].value)"
# then pass the existing entries plus the new one, comma separated:
gcloud sql instances patch <replica-name> \
  --authorized-networks=<existing-1>,<existing-2>,<tenant-cidr>
```

Keep a record of which CIDR belongs to which tenant (the change ticket is
enough). Authorised networks are instance-wide: they control who can reach the
replica, not which login may be used from where. Tenant isolation does not
depend on them; it comes from the login and the policies above. Remove a
tenant's CIDR when they stop using BI access.

Prefer a `/32` per BI host. Never add `0.0.0.0/0`.

## 4. Turn the feature on

Set on the API service (Cloud Run), then redeploy:

| variable | value |
|---|---|
| `AGENTICORG_INSIGHTS_BI_ENABLED` | `true` |
| `AGENTICORG_INSIGHTS_BI_HOST` | the replica's address as BI tools should use it (its public IP, or a DNS name pointing at it) |
| `AGENTICORG_INSIGHTS_BI_DATABASE` | the application database name |
| `AGENTICORG_INSIGHTS_BI_MAX_LOGINS_PER_TENANT` | `3` (default; 1 to 50) |

While `ENABLED` is off, or the host or database is empty, every
`/insights/bi-access` route answers 404 `insights_bi_disabled` and the Insights
page shows no link. Workers do not need these variables.

Smoke test as a tenant administrator: Insights, then "BI database access",
then "Create login". Connect with `psql` from an authorised address:

```bash
psql "host=<replica> port=5432 dbname=<db> user=<role> sslmode=require"
SELECT count(*) FROM insights_bi.run_facts;      -- your tenant's facts
SELECT * FROM public.users LIMIT 1;              -- permission denied
```

Then revoke the login from the page.

## 5. Connecting Metabase

In Metabase: Admin settings, Databases, Add database.

1. Database type: PostgreSQL.
2. Host, port (5432) and database name: from the page's "How a BI tool
   connects" panel.
3. Username: the login (`bi_<8 hex>_<10 characters>`). Password: the one shown
   once when the login was created. If it was lost, revoke the login and create
   a new one; it cannot be shown again.
4. Use a secure connection (SSL): on. SSL mode: `require` (or `verify-ca` with
   the server CA).
5. Schemas: "Only these", `insights_bi`.
6. Save. Metabase syncs the five views. Queries that run longer than 30 seconds
   are cancelled by the database; add date filters.

Other tools take the same values, or the connection string the page shows once
(`postgresql://<login>:<password>@<host>:5432/<db>?sslmode=require`).

## Revoking a login

A tenant administrator revokes a login on the page (Revoke, then confirm) or
with `DELETE /api/v1/insights/bi-access/<login>`. The function marks the
mapping revoked (from that moment the login maps to no tenant and every view is
empty, also for a session that is still open), sets the role NOLOGIN and drops
it. The response says `dropped`, or `nologin` when something still depends on
the role; it then stays, unable to log in and mapped to nothing. To clean such a
role up, as the migration user on the primary:

```sql
-- what depends on it
SELECT * FROM pg_shdepend WHERE refobjid = '<login>'::regrole;
GRANT agenticorg_bi_admin TO CURRENT_USER;
SET ROLE agenticorg_bi_admin;
DROP ROLE "<login>";   -- after removing whatever depended on it
RESET ROLE;
REVOKE agenticorg_bi_admin FROM CURRENT_USER;
```

Every create and revoke writes a signed `audit_log` row
(`insights.bi_access.create`, `insights.bi_access.revoke`) with the login name,
never the password.

## Emergency: cut all BI access at once

1. Network: remove every tenant CIDR from the replica's authorised networks
   (step 3 with only the entries you keep). New connections stop immediately.
2. Data: on the primary, as the migration user, mark every mapping revoked.
   Every BI session, including open ones, then reads no rows:

   ```sql
   BEGIN;
   ALTER TABLE insights_bi_logins NO FORCE ROW LEVEL SECURITY;
   UPDATE insights_bi_logins SET revoked_at = now() WHERE revoked_at IS NULL;
   ALTER TABLE insights_bi_logins FORCE ROW LEVEL SECURITY;
   COMMIT;
   ```

3. Turn `AGENTICORG_INSIGHTS_BI_ENABLED` off so no new login is created.
4. Drop the roles: for each row with a role still in `pg_roles`, as the
   migration user with `SET ROLE agenticorg_bi_admin` (see above),
   `ALTER ROLE "<login>" NOLOGIN; DROP ROLE "<login>";`.

Turning the variable off alone does not stop existing logins.

## Downgrade

`v6z108_insights_bi` refuses to downgrade while any login is active or any BI
login role is still a member of `agenticorg_bi`. Revoke every login first. The
downgrade then drops the functions, views, schema, policies, grants, the mapping
table and the three roles (a role that another database on the same cluster
still uses is kept, with a notice).

## Known limits

- Role names are visible to every login in `pg_roles` (a PostgreSQL property).
  A BI login can therefore see other tenants' BI login names, which carry the
  first 8 hex digits of their tenant id, but nothing those logins can read.
- A BI login can create temporary tables (PostgreSQL grants `TEMP` to
  `PUBLIC`) once it turns off `default_transaction_read_only`; they live only in
  its own session. The physical replica refuses them anyway.
- A BI login has `CONNECT` on any database of the instance that grants it to
  `PUBLIC` (the default). The replica copies every database of the primary
  instance, so on the primary revoke `CONNECT` from `PUBLIC` on every database
  other than the application's (`REVOKE CONNECT ON DATABASE <other> FROM PUBLIC`).
- `statement_timeout = 30s` and `default_transaction_read_only = on` are role
  defaults: a session may change them for itself. Writes stay refused by
  privilege (the Postgres test turns read-only off and proves it), but a session
  can run longer queries; the replica takes that load, never the primary, and
  the connection limit of 3 bounds it.
- The login roles exist on the primary too (roles replicate from it). Never add
  a tenant CIDR to the primary's authorised networks: on the writable primary a
  login could create temporary tables and large objects (PostgreSQL allows both
  to every role), though it still reads only its tenant's rows and writes no
  table.
- A login can read the exposed columns of the five source tables directly
  (`public.run_facts` and so on), with the same tenant rows as the views: the
  views are `security_invoker`, which needs those column grants.
