# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
# ruff: noqa: S608 - every identifier interpolated into SQL here is a module constant
"""Per-tenant BI database access: read-only SQL logins, one tenant each.

Revision ID: v6z108_insights_bi
Revises: v6z107_insights_platform
Create Date: 2026-11-01

A tenant administrator may give a BI tool (Metabase and the like) its own SQL
login on the read replica. The ordinary tenant policies cannot isolate such a
login: they compare ``tenant_id`` with the ``agenticorg.tenant_id`` setting,
and any session may ``SET`` that. So a BI login is tied to its tenant by its
own name instead:

* ``agenticorg_bi`` (NOLOGIN, no attributes) is the group every BI login
  belongs to. It may read the views in schema ``insights_bi`` and, because the
  views are ``security_invoker``, the exposed columns of their source tables,
  and nothing else.
* ``insights_bi_logins`` maps each login (``role_name``) to its tenant. Forced
  tenant RLS, so the application lists only its own tenant's logins; the
  application role may only read it (writes go through the functions below).
* ``public.insights_bi_tenant()`` returns the tenant of ``session_user`` (not
  ``current_user``, which inside a SECURITY DEFINER function is the owner) while
  the login is not revoked. It is owned by ``agenticorg_bi_lookup``, which reads
  only the three columns it needs, and only ``agenticorg_bi`` may execute it.
* Each source table (``run_facts`` parent only, never a partition,
  ``insights_daily``, ``spend_usage_rollups``, ``agents``, ``hitl_queue``) gets
  a permissive ``FOR SELECT TO agenticorg_bi`` policy and a RESTRICTIVE one, both
  ``tenant_id = (SELECT public.insights_bi_tenant())``. The restrictive policy is
  what stops a BI session that sets ``agenticorg.tenant_id`` from widening its
  rows through the existing permissive tenant policy. The sub-select makes the
  lookup an init plan, evaluated once per statement rather than once per row.
* ``agenticorg_bi_admin`` (NOLOGIN NOINHERIT CREATEROLE, ADMIN OPTION on
  ``agenticorg_bi``) owns ``public.insights_bi_create_login`` and
  ``public.insights_bi_revoke_login`` (SECURITY DEFINER, search path
  ``pg_catalog``). On PostgreSQL 16 a CREATEROLE role administers only the roles
  it created, so these functions can create, alter and drop BI logins and no
  other role. The application calls them with EXECUTE only and never holds
  CREATEROLE. The password arrives as a SCRAM-SHA-256 verifier computed by the
  application (``core/insights/bi_access.py``); the plaintext never reaches the
  database or its logs.

The installer (the schema owner, which needs CREATEROLE but not superuser, as
on Cloud SQL) keeps no usable membership of the NOLOGIN roles afterwards, as in
v6z85: ownership transfers use a temporary membership and schema CREATE.

Downgrade refuses while any BI login exists (revoke them first), then drops the
functions, views, schema, policies, grants and the mapping table, and the three
roles unless another database on the cluster still uses them.
"""

from alembic import op
from sqlalchemy import text

revision = "v6z108_insights_bi"
down_revision = "v6z107_insights_platform"
branch_labels = None
depends_on = None

BI_ROLE = "agenticorg_bi"
LOOKUP_ROLE = "agenticorg_bi_lookup"
ADMIN_ROLE = "agenticorg_bi_admin"
SCHEMA = "insights_bi"
LOGINS = "insights_bi_logins"

# Login names the functions accept (core/insights/bi_access.py makes them).
ROLE_NAME_PATTERN = "^bi_[a-z0-9_]{8,48}$"
# RFC 5802/7677 verifier as PostgreSQL stores it; anything else is refused so a
# plaintext password can never be passed through.
VERIFIER_PATTERN = "^SCRAM-SHA-256[$][0-9]{4,7}:[A-Za-z0-9+/]{16,64}={0,2}[$][A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}=$"

# view -> (source table, exposed columns). No subject identifiers, no free
# text, no correlation, trace or idempotency keys.
VIEWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "run_facts": (
        "run_facts",
        (
            "id", "tenant_id", "company_id", "entrypoint", "agent_id", "agent_ref", "agent_type",
            "agent_version", "domain", "workflow_run_id", "step_id", "task_type", "use_case", "environment",
            "status", "confidence", "confidence_floor", "duration_ms", "hitl_required", "tokens_in",
            "tokens_out", "tokens_cached", "tokens_total", "cost_usd", "provider", "model", "error_class",
            "outcome_validated", "data_quality", "started_at", "created_at",
        ),
    ),
    "daily": (
        "insights_daily",
        (
            "id", "tenant_id", "day", "dims_hash", "company_id", "agent_id", "agent_ref", "agent_type",
            "domain", "entrypoint", "task_type", "status", "environment", "data_quality", "runs",
            "hitl_required", "outcome_validated", "tokens_in", "tokens_out", "tokens_cached", "tokens_total",
            "cost_usd", "duration_ms_sum", "duration_n", "latency_buckets", "confidence_sum", "confidence_n",
            "below_floor", "computed_at",
        ),
    ),
    "spend_daily": (
        "spend_usage_rollups",
        ("day", "provider", "model", "usage_type", "unit", "quantity", "amount_inr", "agent_id", "use_case",
         "record_count"),
    ),
    "agents": ("agents", ("id", "name", "agent_type", "domain", "status", "company_id")),
    "approvals": (
        "hitl_queue",
        ("id", "agent_id", "trigger_type", "priority", "status", "assignee_role", "created_at", "decision",
         "decision_at"),
    ),
}  # fmt: skip
SOURCES: tuple[str, ...] = tuple(source for source, _ in VIEWS.values())

READ_POLICY = "insights_bi_read"
ONLY_OWN_POLICY = "insights_bi_only_own"

TENANT_FN = "public.insights_bi_tenant()"
CREATE_FN = "public.insights_bi_create_login(uuid, text, text, uuid, integer)"
REVOKE_FN = "public.insights_bi_revoke_login(uuid, text)"

_TENANT_FN_SQL = f"""
    CREATE FUNCTION public.insights_bi_tenant()
    RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog
    AS $function$
        SELECT l.tenant_id FROM public.{LOGINS} l
        WHERE l.role_name = session_user::text AND l.revoked_at IS NULL
    $function$
"""

_CREATE_FN_SQL = f"""
    CREATE FUNCTION public.insights_bi_create_login(
        p_tenant uuid, p_role text, p_scram_verifier text, p_created_by uuid, p_max integer)
    RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog
    AS $function$
    DECLARE
        v_id uuid;
        v_active integer;
    BEGIN
        IF p_tenant IS NULL OR p_role IS NULL OR p_scram_verifier IS NULL OR p_max IS NULL THEN
            RAISE EXCEPTION 'insights_bi_invalid_argument' USING ERRCODE = '22004';
        END IF;
        -- Bound to the caller's tenant context, as every tenant write is.
        IF current_setting('agenticorg.tenant_id', true) IS DISTINCT FROM p_tenant::text THEN
            RAISE EXCEPTION 'insights_bi_tenant_mismatch' USING ERRCODE = '42501';
        END IF;
        IF p_role !~ '{ROLE_NAME_PATTERN}' THEN
            RAISE EXCEPTION 'insights_bi_invalid_role_name' USING ERRCODE = '22023';
        END IF;
        IF p_scram_verifier !~ '{VERIFIER_PATTERN}' THEN
            RAISE EXCEPTION 'insights_bi_invalid_verifier' USING ERRCODE = '22023';
        END IF;
        IF p_max < 1 OR p_max > 50 THEN
            RAISE EXCEPTION 'insights_bi_invalid_limit' USING ERRCODE = '22023';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.tenants t WHERE t.id = p_tenant AND t.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'insights_bi_unknown_tenant' USING ERRCODE = 'BI404';
        END IF;
        -- One creation at a time per tenant, so the limit holds under concurrency.
        PERFORM pg_advisory_xact_lock(hashtextextended('insights_bi_logins/' || p_tenant::text, 0));
        SELECT count(*) INTO v_active FROM public.{LOGINS} l
        WHERE l.tenant_id = p_tenant AND l.revoked_at IS NULL;
        IF v_active >= p_max THEN
            RAISE EXCEPTION 'insights_bi_limit_reached' USING ERRCODE = 'BI409';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname = p_role)
            OR EXISTS (SELECT 1 FROM public.{LOGINS} l WHERE l.role_name = p_role) THEN
            RAISE EXCEPTION 'insights_bi_role_exists' USING ERRCODE = '42710';
        END IF;
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS '
                       'INHERIT NOREPLICATION CONNECTION LIMIT 3', p_role, p_scram_verifier);
        IF current_setting('server_version_num')::integer >= 160000 THEN
            -- Privileges through inheritance only: the login cannot SET ROLE to the group.
            EXECUTE format('GRANT {BI_ROLE} TO %I WITH INHERIT TRUE, SET FALSE', p_role);
        ELSE
            EXECUTE format('GRANT {BI_ROLE} TO %I', p_role);
        END IF;
        EXECUTE format('ALTER ROLE %I SET search_path = {SCHEMA}', p_role);
        EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', p_role, '30s');
        EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', p_role);
        INSERT INTO public.{LOGINS} (id, tenant_id, role_name, created_by, created_at)
        VALUES (gen_random_uuid(), p_tenant, p_role, p_created_by, now())
        RETURNING id INTO v_id;
        RETURN v_id;
    END
    $function$
"""

_REVOKE_FN_SQL = f"""
    CREATE FUNCTION public.insights_bi_revoke_login(p_tenant uuid, p_role text)
    RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog
    AS $function$
    BEGIN
        IF p_tenant IS NULL OR p_role IS NULL THEN
            RAISE EXCEPTION 'insights_bi_invalid_argument' USING ERRCODE = '22004';
        END IF;
        IF current_setting('agenticorg.tenant_id', true) IS DISTINCT FROM p_tenant::text THEN
            RAISE EXCEPTION 'insights_bi_tenant_mismatch' USING ERRCODE = '42501';
        END IF;
        IF p_role !~ '{ROLE_NAME_PATTERN}' THEN
            RAISE EXCEPTION 'insights_bi_unknown_login' USING ERRCODE = 'BI404';
        END IF;
        -- The mapping row must be this tenant's. Revoking it first cuts data
        -- access at once, also for a session that is still connected.
        UPDATE public.{LOGINS} l SET revoked_at = COALESCE(l.revoked_at, now())
        WHERE l.tenant_id = p_tenant AND l.role_name = p_role;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'insights_bi_unknown_login' USING ERRCODE = 'BI404';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname = p_role) THEN
            RETURN 'dropped';
        END IF;
        EXECUTE format('ALTER ROLE %I NOLOGIN', p_role);
        BEGIN
            EXECUTE format('DROP ROLE %I', p_role);
        EXCEPTION WHEN dependent_objects_still_exist THEN
            -- Something still depends on the role: it stays, unable to log in
            -- and mapped to no tenant.
            RETURN 'nologin';
        END;
        RETURN 'dropped';
    END
    $function$
"""


def _roles_sql() -> str:
    return f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BI_ROLE}') THEN
                CREATE ROLE {BI_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{BI_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI group role';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{LOOKUP_ROLE}') THEN
                CREATE ROLE {LOOKUP_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
                    NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{LOOKUP_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI lookup role';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_ROLE}') THEN
                CREATE ROLE {ADMIN_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT NOREPLICATION
                    NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ADMIN_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolreplication OR rolinherit
                     OR NOT rolcreaterole)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI admin role';
            END IF;
            -- The BI group and the lookup role are never members of anything
            -- that could widen what a BI login reads.
            -- (The admin role's own ADMIN OPTION on the group is the one exception.)
            IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                       JOIN pg_roles g ON g.oid = m.roleid
                       WHERE r.rolname IN ('{BI_ROLE}', '{LOOKUP_ROLE}', '{ADMIN_ROLE}')
                         AND NOT (r.rolname = '{ADMIN_ROLE}' AND g.rolname = '{BI_ROLE}')) THEN
                RAISE EXCEPTION 'BI roles must not be members of other roles';
            END IF;
            IF current_setting('server_version_num')::integer >= 160000 THEN
                EXECUTE 'GRANT {BI_ROLE} TO {ADMIN_ROLE} WITH ADMIN OPTION, INHERIT FALSE, SET FALSE';
            ELSE
                GRANT {BI_ROLE} TO {ADMIN_ROLE} WITH ADMIN OPTION;
            END IF;
        END $$
    """


def _logins_table() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LOGINS} (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            role_name VARCHAR(63) NOT NULL,
            created_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ NULL,
            CONSTRAINT uq_insights_bi_logins_role_name UNIQUE (role_name)
        )
        """
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_insights_bi_logins_tenant ON {LOGINS}(tenant_id)")
    op.execute("ALTER TABLE insights_bi_logins ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE insights_bi_logins FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {LOGINS}_tenant_isolation ON {LOGINS}")
    op.execute(
        f"""
        CREATE POLICY {LOGINS}_tenant_isolation ON {LOGINS}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        """
    )
    # The lookup role reads every mapping (for session_user), three columns only.
    op.execute(f"REVOKE ALL ON public.{LOGINS} FROM {LOOKUP_ROLE}, {ADMIN_ROLE}, {BI_ROLE}")
    op.execute(f"GRANT SELECT (role_name, tenant_id, revoked_at) ON public.{LOGINS} TO {LOOKUP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS insights_bi_lookup_read ON public.{LOGINS}")
    op.execute(f"CREATE POLICY insights_bi_lookup_read ON public.{LOGINS} FOR SELECT TO {LOOKUP_ROLE} USING (true)")
    # The admin role writes mappings, through its functions only.
    op.execute(f"GRANT SELECT (id, tenant_id, role_name, revoked_at) ON public.{LOGINS} TO {ADMIN_ROLE}")
    op.execute(f"GRANT INSERT (id, tenant_id, role_name, created_by, created_at) ON public.{LOGINS} TO {ADMIN_ROLE}")
    op.execute(f"GRANT UPDATE (revoked_at) ON public.{LOGINS} TO {ADMIN_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS insights_bi_admin_write ON public.{LOGINS}")
    op.execute(
        f"CREATE POLICY insights_bi_admin_write ON public.{LOGINS} TO {ADMIN_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(f"REVOKE ALL ON public.tenants FROM {ADMIN_ROLE}")
    op.execute(f"GRANT SELECT (id, deleted_at) ON public.tenants TO {ADMIN_ROLE}")
    # docs/RLS_APP_ROLE.md: the application reads its tenant's mappings and
    # writes none; the default privileges would otherwise give it row DML.
    op.execute(
        f"""
        DO $$ BEGIN
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                REVOKE ALL ON public.{LOGINS} FROM agenticorg_app;
                GRANT SELECT ON public.{LOGINS} TO agenticorg_app;
            END IF;
        END $$
        """
    )


def _source_grants_and_policies() -> None:
    # Refuse a source without row-level security: its rows would be readable
    # through the column grants regardless of any policy.
    tables = ", ".join(f"'{t}'" for t in SOURCES)
    op.execute(
        f"""
        DO $$ DECLARE missing text; BEGIN
            SELECT string_agg(t, ', ') INTO missing FROM unnest(ARRAY[{tables}]) AS t
            WHERE NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                              WHERE n.nspname = 'public' AND c.relname = t AND c.relrowsecurity);
            IF missing IS NOT NULL THEN
                RAISE EXCEPTION 'BI source tables without row-level security: %', missing;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {BI_ROLE}")
    for source, columns in VIEWS.values():
        op.execute(f"REVOKE ALL ON public.{source} FROM {BI_ROLE}")
        op.execute(f"GRANT SELECT ({', '.join(columns)}) ON public.{source} TO {BI_ROLE}")
        op.execute(f"DROP POLICY IF EXISTS {READ_POLICY} ON public.{source}")
        op.execute(
            f"CREATE POLICY {READ_POLICY} ON public.{source} AS PERMISSIVE FOR SELECT TO {BI_ROLE} "
            f"USING (tenant_id = (SELECT public.insights_bi_tenant()))"
        )
        op.execute(f"DROP POLICY IF EXISTS {ONLY_OWN_POLICY} ON public.{source}")
        op.execute(
            f"CREATE POLICY {ONLY_OWN_POLICY} ON public.{source} AS RESTRICTIVE FOR SELECT TO {BI_ROLE} "
            f"USING (tenant_id = (SELECT public.insights_bi_tenant()))"
        )


def _schema_and_views() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF to_regnamespace('{SCHEMA}') IS NULL THEN
                CREATE SCHEMA {SCHEMA};
            ELSIF NOT EXISTS (SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
                              WHERE n.nspname = '{SCHEMA}' AND r.rolname = current_user) THEN
                RAISE EXCEPTION 'Unsafe preexisting schema {SCHEMA}';
            END IF;
        END $$
        """
    )
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {BI_ROLE}")
    for view, (source, columns) in VIEWS.items():
        op.execute(
            f"CREATE OR REPLACE VIEW {SCHEMA}.{view} WITH (security_invoker = true, security_barrier = true) "
            f"AS SELECT {', '.join(columns)} FROM public.{source}"
        )
        op.execute(f"REVOKE ALL ON {SCHEMA}.{view} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {SCHEMA}.{view} TO {BI_ROLE}")
    # Nothing but the views lives in the schema.
    names = ", ".join(f"'{v}'" for v in VIEWS)
    op.execute(
        f"""
        DO $$ DECLARE extra text; BEGIN
            SELECT string_agg(c.relname, ', ') INTO extra FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = '{SCHEMA}' AND c.relname NOT IN ({names});
            IF extra IS NOT NULL THEN
                RAISE EXCEPTION 'Unexpected relations in schema {SCHEMA}: %', extra;
            END IF;
        END $$
        """
    )


def _owned_function(signature: str, ddl: str, owner: str, returns: str, nargs: int, grantees: str) -> str:
    """Create ``signature`` owned by ``owner`` (or verify the existing one) and grant EXECUTE.

    Runs inside the DO block that holds the temporary membership of ``owner``.
    """
    return f"""
            IF to_regprocedure('{signature}') IS NULL THEN
                EXECUTE $ddl${ddl}$ddl$;
                REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
                ALTER FUNCTION {signature} OWNER TO {owner};
            ELSE
                IF NOT EXISTS (
                    SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
                    WHERE p.oid = '{signature}'::regprocedure
                        AND r.rolname = '{owner}' AND p.prosecdef
                        AND p.pronargs = {nargs} AND p.prorettype = '{returns}'::regtype
                        AND p.proconfig = ARRAY['search_path=pg_catalog']
                ) THEN
                    RAISE EXCEPTION 'Unsafe preexisting function {signature}';
                END IF;
                REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
            END IF;
            {grantees}
    """


def _functions() -> None:
    app_grants = (
        "EXECUTE format('GRANT EXECUTE ON FUNCTION {fn} TO %I', current_user);\n"
        "            IF to_regrole('agenticorg_app') IS NOT NULL THEN\n"
        "                GRANT EXECUTE ON FUNCTION {fn} TO agenticorg_app;\n"
        "            END IF;"
    )
    body = "\n".join(
        (
            _owned_function(TENANT_FN, _TENANT_FN_SQL, LOOKUP_ROLE, "uuid", 0, f"GRANT EXECUTE ON FUNCTION {TENANT_FN} TO {BI_ROLE};"),
            _owned_function(CREATE_FN, _CREATE_FN_SQL, ADMIN_ROLE, "uuid", 5, app_grants.format(fn=CREATE_FN)),
            _owned_function(REVOKE_FN, _REVOKE_FN_SQL, ADMIN_ROLE, "text", 2, app_grants.format(fn=REVOKE_FN)),
        )
    )  # fmt: skip
    # Transferring ownership needs temporary membership of the owners and
    # schema CREATE for them; the installer keeps neither (as in v6z85). The
    # creator's ADMIN-only grant on PostgreSQL 16+ stays: it confers no access.
    op.execute(
        f"""
        DO $$ BEGIN
            EXECUTE format('GRANT {LOOKUP_ROLE} TO %I', current_user);
            EXECUTE format('GRANT {ADMIN_ROLE} TO %I', current_user);
            GRANT USAGE, CREATE ON SCHEMA public TO {LOOKUP_ROLE}, {ADMIN_ROLE};
            {body}
            REVOKE CREATE ON SCHEMA public FROM {LOOKUP_ROLE}, {ADMIN_ROLE};
            EXECUTE format('REVOKE {LOOKUP_ROLE} FROM %I', current_user);
            EXECUTE format('REVOKE {ADMIN_ROLE} FROM %I', current_user);
            IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                NULL;
            ELSIF current_setting('server_version_num')::integer >= 160000 THEN
                IF pg_has_role(current_user, '{LOOKUP_ROLE}', 'USAGE') OR pg_has_role(current_user, '{LOOKUP_ROLE}', 'SET')
                    OR pg_has_role(current_user, '{ADMIN_ROLE}', 'USAGE') OR pg_has_role(current_user, '{ADMIN_ROLE}', 'SET')
                    OR pg_has_role(current_user, '{BI_ROLE}', 'USAGE') OR pg_has_role(current_user, '{BI_ROLE}', 'SET') THEN
                    RAISE EXCEPTION 'Installer retained BI role access';
                END IF;
            ELSIF pg_has_role(current_user, '{LOOKUP_ROLE}', 'MEMBER') OR pg_has_role(current_user, '{ADMIN_ROLE}', 'MEMBER')
                OR pg_has_role(current_user, '{BI_ROLE}', 'MEMBER') THEN
                RAISE EXCEPTION 'Installer retained BI role membership';
            END IF;
        END $$
        """
    )  # fmt: skip


def upgrade() -> None:
    op.execute(_roles_sql())
    _logins_table()
    _functions()
    _source_grants_and_policies()
    _schema_and_views()


def downgrade() -> None:
    bind = op.get_bind()
    # Inspect every tenant's mappings as the table owner; FORCE is restored
    # before any refusal.
    if bind.execute(text(f"SELECT to_regclass('public.{LOGINS}') IS NOT NULL")).scalar():
        op.execute(f"LOCK TABLE {LOGINS} IN ACCESS EXCLUSIVE MODE")
        op.execute(f"ALTER TABLE {LOGINS} NO FORCE ROW LEVEL SECURITY")
        active = bind.execute(text(f"SELECT count(*) FROM {LOGINS} WHERE revoked_at IS NULL")).scalar_one()
        op.execute(f"ALTER TABLE {LOGINS} FORCE ROW LEVEL SECURITY")
        if active:
            raise RuntimeError(
                f"{active} BI login(s) are still active: revoke them (DELETE /api/v1/insights/bi-access/<role>) "
                "before downgrading v6z108_insights_bi"
            )
    # Real members of the group: BI logins never hold ADMIN OPTION; the admin
    # role and the role that created the group (PostgreSQL 16+) hold only that.
    members = bind.execute(
        text(
            "SELECT string_agg(r.rolname, ', ') FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles r ON r.oid = m.member "
            "WHERE g.rolname = :group AND NOT m.admin_option"
        ),
        {"group": BI_ROLE},
    ).scalar()
    if members:
        raise RuntimeError(
            f"BI logins still exist on the cluster ({members}): drop them before downgrading v6z108_insights_bi"
        )
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{view}")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
    for source in SOURCES:
        op.execute(f"DROP POLICY IF EXISTS {ONLY_OWN_POLICY} ON public.{source}")
        op.execute(f"DROP POLICY IF EXISTS {READ_POLICY} ON public.{source}")
    # Each owner drops its own functions; the installer borrows the membership.
    op.execute(
        f"""
        DO $$ BEGIN
            IF to_regrole('{BI_ROLE}') IS NOT NULL THEN
                {"; ".join(f"REVOKE ALL ON public.{s} FROM {BI_ROLE}" for s in SOURCES)};
                REVOKE ALL ON SCHEMA public FROM {BI_ROLE};
            END IF;
            IF to_regprocedure('{TENANT_FN}') IS NOT NULL THEN
                EXECUTE format('GRANT {LOOKUP_ROLE} TO %I', current_user);
                DROP FUNCTION {TENANT_FN};
                EXECUTE format('REVOKE {LOOKUP_ROLE} FROM %I', current_user);
            END IF;
            IF to_regprocedure('{CREATE_FN}') IS NOT NULL OR to_regprocedure('{REVOKE_FN}') IS NOT NULL THEN
                EXECUTE format('GRANT {ADMIN_ROLE} TO %I', current_user);
                DROP FUNCTION IF EXISTS {CREATE_FN};
                DROP FUNCTION IF EXISTS {REVOKE_FN};
                EXECUTE format('REVOKE {ADMIN_ROLE} FROM %I', current_user);
            END IF;
            IF to_regrole('{ADMIN_ROLE}') IS NOT NULL THEN
                REVOKE ALL ON public.tenants FROM {ADMIN_ROLE};
                REVOKE ALL ON SCHEMA public FROM {ADMIN_ROLE};
            END IF;
            IF to_regrole('{LOOKUP_ROLE}') IS NOT NULL THEN
                REVOKE ALL ON SCHEMA public FROM {LOOKUP_ROLE};
            END IF;
        END $$
        """
    )  # fmt: skip
    op.execute(f"DROP TABLE IF EXISTS {LOGINS}")
    # Roles are cluster-wide: one that another database still uses stays.
    op.execute(
        f"""
        DO $$ DECLARE r text; BEGIN
            FOREACH r IN ARRAY ARRAY['{ADMIN_ROLE}', '{LOOKUP_ROLE}', '{BI_ROLE}'] LOOP
                CONTINUE WHEN to_regrole(r) IS NULL;
                BEGIN
                    EXECUTE format('DROP ROLE %I', r);
                EXCEPTION WHEN dependent_objects_still_exist THEN
                    RAISE NOTICE 'role % is still used by another database and was kept', r;
                END;
            END LOOP;
        END $$
        """
    )
