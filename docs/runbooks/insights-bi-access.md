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
  only; it cannot `SET ROLE` to the group). The group may SELECT the five views
  in schema `insights_bi` and EXECUTE the five functions under them, and
  nothing else: no privilege on any table or column (so `EXPLAIN` and
  `pg_stats` tell it nothing about the tables' contents), no partition of
  `run_facts`, no write privilege, no provisioning function.
- Each view `insights_bi.<view>` is a plain `security_invoker` view over
  `insights_bi.<view>_rows()`, a SQL function without arguments that is
  `SECURITY DEFINER` with `search_path = pg_catalog, pg_temp` and owned by the
  NOLOGIN, NOBYPASSRLS role `agenticorg_bi_reader`. Such a function is never
  inlined: the BI query's own conditions apply to the function's output only,
  and the planner's estimate for it is a constant that no tenant's data moves.
  `run_facts_rows()` returns the last 400 days (and anything dated later).
- `agenticorg_bi_reader` holds the column grants on the source tables
  (`run_facts`, `insights_daily`, `spend_usage_rollups`, `agents`,
  `hitl_queue`; the `run_facts` parent only): the exposed columns and
  `tenant_id`. Nobody is a member of it. Each source has a permissive and a
  RESTRICTIVE `FOR SELECT TO agenticorg_bi_reader` policy, both `tenant_id =
  (SELECT public.insights_bi_tenant())`, and each function filters on the same
  tenant. The restrictive policy is what stops a BI session that sets
  `agenticorg.tenant_id` (or `agenticorg.platform_ctx`) from widening the rows
  through the application's own permissive policies.
- `insights_bi_logins` maps each login (`role_name`) to its tenant.
  `public.insights_bi_tenant()` (owned by `agenticorg_bi_lookup`, executable by
  the reader only) returns the tenant of `session_user`, which stays the BI
  login inside the SECURITY DEFINER functions, while the mapping is not revoked
  and the server is a read replica (see "Read replica only" below).
- Logins are created and dropped only by `public.insights_bi_create_login` and
  `public.insights_bi_revoke_login`, SECURITY DEFINER functions owned by the
  NOLOGIN `agenticorg_bi_admin` role (CREATEROLE, ADMIN OPTION on the group).
  On PostgreSQL 16 a CREATEROLE role administers only the roles it created, so
  these functions cannot alter any other role. The application holds EXECUTE
  only, never CREATEROLE. Both functions refuse a tenant that differs from the
  session's tenant context. Creation is refused (the page shows an error, the
  API answers 503 `insights_bi_unavailable`) while `PUBLIC` or `agenticorg_bi`
  may `CREATE` in schema `public`.
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
   `scripts/create_app_role.sql` refuses to finish otherwise). The migration
   does not change it, but it logs a NOTICE ("Schema public is writable ...")
   and `insights_bi_create_login` refuses to create logins until it is fixed.
   Check:

   ```sql
   SELECT has_schema_privilege('public', 'public', 'CREATE');          -- must be false
   SELECT has_schema_privilege('agenticorg_bi', 'public', 'CREATE');   -- must be false
   -- if not: REVOKE CREATE ON SCHEMA public FROM PUBLIC;
   ```

## Read replica only

BI logins read nothing on a writable primary: `public.insights_bi_tenant()`
answers only when `pg_is_in_recovery()` is true (a read replica) or the
one-row table `insights_bi_settings` has `allow_primary = true`. It is `false`
after the migration, and only the migration user may change it; the
application role cannot read or write it. A login that connects to the
primary sees every view empty. Leave it `false` in production. (The Postgres
tests set it to `true`, because their database is a primary.)

```sql
SELECT allow_primary FROM insights_bi_settings;   -- as the migration user: false
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

(Older gcloud releases: `--require-ssl`.)

The connection details the page shows say `sslmode=verify-ca` and
`channel_binding=require`:

- `verify-ca` checks the server certificate's chain against the replica's
  server CA, so a BI tool cannot be talked into sending its password to a
  machine in the middle. Not `verify-full`: Cloud SQL's server certificate is
  issued to the instance (its name, not the IP address or DNS name a BI tool
  dials), so the host name check of `verify-full` fails.
- `channel_binding=require` makes the SCRAM-SHA-256 exchange use
  SCRAM-SHA-256-PLUS, bound to that TLS session (libpq 13 and later; JDBC
  drivers that support it take `channelBinding=require`). A tool whose driver
  cannot do channel binding fails to connect with it; it may leave it out
  (`verify-ca` still protects the password) and should be upgraded.

Download the replica's server CA certificate and give it to the tenant's BI
administrator (it is public material, not a secret):

```bash
gcloud sql instances describe <replica-name> \
  --format="value(serverCaCert.cert)" > agenticorg-bi-server-ca.pem
```

The BI tool takes it as its root certificate (`sslrootcert` for libpq and
psql, "SSL root certificate" in Metabase). Download it again after the
replica's server CA is rotated (`gcloud sql ssl server-ca-certs list
--instance=<replica-name>`), and hand out the new one before the rotation
completes.

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
psql "host=<replica> port=5432 dbname=<db> user=<role> sslmode=verify-ca \
  sslrootcert=agenticorg-bi-server-ca.pem channel_binding=require"
SELECT pg_is_in_recovery();                      -- true: the replica
SELECT count(*) FROM insights_bi.run_facts;      -- your tenant's facts (last 400 days)
SELECT * FROM public.users LIMIT 1;              -- permission denied
SELECT id FROM public.run_facts LIMIT 1;         -- permission denied
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
4. Use a secure connection (SSL): on. SSL mode: `verify-ca`. SSL root
   certificate: the replica's server CA certificate (section 2). If the
   Metabase version offers additional JDBC options, add
   `channelBinding=require`.
5. Schemas: "Only these", `insights_bi`.
6. Save. Metabase syncs the five views. Queries that run longer than 30 seconds
   are cancelled by the database; add date filters.

Other tools take the same values, or the connection string the page shows once
(`postgresql://<login>:<password>@<host>:5432/<db>?sslmode=verify-ca&channel_binding=require`;
add `&sslrootcert=<path to the CA file>` where the tool reads it from the URL).

## 6. Connecting Power BI and Looker Studio

Both read the same `insights_bi` views with the same login, over the PostgreSQL connector. Nothing else is
installed on the platform side.

### Power BI

Power BI Desktop: Get data, PostgreSQL database.

1. Server: `<host>:5432` from the page's "How a BI tool connects" panel. Database: the database name there.
2. Data connectivity mode: Import for scheduled refreshes; DirectQuery sends every visual's query to the replica
   and is limited by the 30-second statement timeout.
3. Credentials: Database, with the login and the password shown once when it was created.
4. Encryption: keep "Encrypt connections" on. Power BI checks the server certificate against the machine's
   trusted roots, so install the replica's server CA certificate (section 2) in the Windows "Trusted Root
   Certification Authorities" store of every machine that connects, including the on-premises data gateway.
5. In the navigator, pick the views under `insights_bi`.

For refreshes in the Power BI service, the dataset connects through an on-premises data gateway. The address to
authorise on the replica (section 3) is the gateway machine's public address, not the person's laptop.

### Looker Studio

Looker Studio: Create, Data source, PostgreSQL.

1. Host name or IP, port 5432, database, username and password: as above.
2. Enable SSL: on. Enable client authentication: off (the login is a password login). Upload the replica's server
   CA certificate as the server certificate.
3. Pick a view under `insights_bi`, or use a custom query against one.

Looker Studio connects from Google-owned addresses that its PostgreSQL connector documentation lists, shared by
every Looker Studio user. Authorising them on the replica opens the network path to anyone on Looker Studio, so
the login, its password and TLS are what protect the tenant's rows: give each tenant its own login, revoke it when
a person leaves, and prefer Metabase or Power BI through a gateway where a fixed address is possible.

## Revoking a login

A tenant administrator revokes a login on the page (Revoke, then confirm) or
with `DELETE /api/v1/insights/bi-access/<login>`. The function marks the
mapping revoked (from that moment every new statement of the login, in any
transaction that started after the revoke, finds every view empty), sets the
role NOLOGIN and drops it, which ends the membership of `agenticorg_bi` and
with it every privilege. The response says `dropped`, or `nologin` when
something still depends on the role (for example a large object the login
created: any role may create one on a primary); the role then stays, unable to
log in, mapped to nothing and no longer a member of `agenticorg_bi`, so it
cannot read any view either. To clean such a role up, as the migration user on
the primary:

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

   A transaction a BI session opened before this (REPEATABLE READ) still sees
   the old mapping; step 4 ends that too.
3. Turn `AGENTICORG_INSIGHTS_BI_ENABLED` off so no new login is created.
4. Drop the roles: for each row with a role still in `pg_roles`, as the
   migration user with `SET ROLE agenticorg_bi_admin` (see above),
   `ALTER ROLE "<login>" NOLOGIN; REVOKE agenticorg_bi FROM "<login>"; DROP ROLE "<login>";`.
   Then, on the replica, end the sessions still open (as a user holding
   `pg_signal_backend`):
   `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename LIKE 'bi\_%';`

Turning the variable off alone does not stop existing logins.

## Downgrade

`v6z108_insights_bi` refuses to downgrade while any login is active or any BI
login role is still a member of `agenticorg_bi`. Revoke every login first. The
downgrade then drops the functions, views, schema, policies, grants, the mapping
and settings tables and the four roles (a role that another database on the
same cluster still uses is kept, with a notice).

Re-running the upgrade (for example after a failed deploy) is safe while logins
exist: the views and reader functions are rebuilt, the three public functions
are replaced in place, and the ADMIN-only membership PostgreSQL 16 gives
`agenticorg_bi_admin` in each login it creates is expected. Any other
membership of the four roles, or any role able to use the reader, lookup or
admin role, makes the upgrade fail.

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
  login reads no rows (`allow_primary` is off) but could still create
  temporary tables and large objects (PostgreSQL allows both to every role).
  A large object it owns makes its revocation answer `nologin`.
- A REPEATABLE READ transaction that a login opened before its revocation, and
  that already read a view, can read that view again at its old snapshot until
  the transaction ends or the next statement that touches a relation it has not
  locked yet (PostgreSQL applies the membership change to a session at that
  point). It sees no data newer than its snapshot. Step 4 of the emergency
  procedure ends such sessions.
- Catalogue-wide figures stay visible to every role, as PostgreSQL shows them
  to anyone: `pg_class.reltuples` and `pg_stat_user_tables` give the total rows
  of each table and partition across all tenants (not per tenant). The
  per-column statistics in `pg_stats` are not visible to BI logins.
- The views' row estimates are constants, so BI tools that plan by them may
  pick poor join orders on large results; add date filters.
- Each login gets `insights_bi.run_facts` for the last 400 days only; use
  `insights_bi.daily` for longer history.
