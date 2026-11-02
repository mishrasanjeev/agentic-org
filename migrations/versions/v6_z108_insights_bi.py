# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
# ruff: noqa: S608 - every identifier interpolated into SQL here is a module constant or a catalogue type name
"""Per-tenant BI database access: read-only SQL logins, one tenant each.

Revision ID: v6z108_insights_bi
Revises: v6z107_insights_platform
Create Date: 2026-11-01

A tenant administrator may give a BI tool (Metabase and the like) its own SQL
login on the read replica. The ordinary tenant policies cannot isolate such a
login: they compare ``tenant_id`` with the ``agenticorg.tenant_id`` setting,
and any session may ``SET`` that. So a BI login is tied to its tenant by its
own name instead, and it holds no privilege on any table:

* ``agenticorg_bi`` (NOLOGIN, no attributes) is the group every BI login
  belongs to. It may SELECT the views in schema ``insights_bi`` and EXECUTE the
  set-returning functions under them, and nothing else. In particular it has
  no privilege on any base table, so the planner's statistics (row estimates,
  histograms, most-common values) of the source tables never reach a BI
  session through ``EXPLAIN`` or ``pg_stats``.
* ``insights_bi.<view>`` is a plain ``security_invoker`` view over
  ``insights_bi.<view>_rows()``: ``LANGUAGE sql STABLE SECURITY DEFINER SET
  search_path = pg_catalog, pg_temp``, no arguments, owned by
  ``agenticorg_bi_reader``. A SECURITY DEFINER function is never inlined, so a
  BI query's own conditions are applied to the function's output only (they
  cannot run against other tenants' rows) and its estimates are the planner's
  constant defaults for a function scan. ``run_facts_rows()`` returns the last
  400 days only.
* ``agenticorg_bi_reader`` (NOLOGIN NOINHERIT NOBYPASSRLS, member of nothing,
  nobody a member of it) holds the column grants on the five source tables
  (``run_facts`` parent only, never a partition, ``insights_daily``,
  ``spend_usage_rollups``, ``agents``, ``hitl_queue``): the exposed columns
  plus ``tenant_id``. Each source gets a permissive ``FOR SELECT TO
  agenticorg_bi_reader`` policy and a RESTRICTIVE one, both ``tenant_id =
  (SELECT public.insights_bi_tenant())``. The restrictive policy is what stops
  a BI session that sets ``agenticorg.tenant_id`` (or the platform context)
  from widening the reader's rows through the existing permissive policies.
  Each function also filters on the same tenant itself.
* ``insights_bi_logins`` maps each login (``role_name``) to its tenant. Forced
  tenant RLS, so the application lists only its own tenant's logins; the
  application role may only read it (writes go through the functions below).
* ``insights_bi_settings`` (one row, ``allow_primary`` false) is written only
  by the migration owner. BI logins read nothing on a writable primary: the
  tenant lookup answers only on a hot standby (``pg_is_in_recovery()``) or
  when ``allow_primary`` is set (tests, and deliberate operator choice).
* ``public.insights_bi_tenant()`` returns the tenant of ``session_user`` (not
  ``current_user``, which inside a SECURITY DEFINER function is its owner;
  ``session_user`` stays the BI login through the whole chain) while the login
  is not revoked and the server is a standby or ``allow_primary`` is on. It is
  owned by ``agenticorg_bi_lookup``, which reads only the columns it needs, and
  only ``agenticorg_bi_reader`` may execute it.
* ``agenticorg_bi_admin`` (NOLOGIN NOINHERIT CREATEROLE, ADMIN OPTION on
  ``agenticorg_bi``) owns ``public.insights_bi_create_login`` and
  ``public.insights_bi_revoke_login`` (SECURITY DEFINER, search path
  ``pg_catalog, pg_temp``). On PostgreSQL 16 a CREATEROLE role administers only
  the roles it created, so these functions can create, alter and drop BI
  logins and no other role. The application calls them with EXECUTE only and
  never holds CREATEROLE. The password arrives as a SCRAM-SHA-256 verifier
  computed by the application (``core/insights/bi_access.py``); the plaintext
  never reaches the database or its logs. Creation is refused while PUBLIC or
  ``agenticorg_bi`` may CREATE in schema ``public`` (the migration only warns,
  since it cannot know how production was provisioned). Revocation that cannot
  drop the role (it still owns something) also revokes its ``agenticorg_bi``
  membership, which ends data access at once, even inside a transaction the
  login already has open.

The installer (the schema owner, which needs CREATEROLE but not superuser, as
on Cloud SQL) keeps no usable membership of the NOLOGIN roles afterwards, as in
v6z85: ownership transfers use a temporary membership and schema CREATE.

Re-running the upgrade is safe: the views and reader functions are rebuilt,
the three public functions are replaced in place by their owners, and the
membership PostgreSQL 16 gives ``agenticorg_bi_admin`` in each login it
creates is expected.

Downgrade refuses while any BI login exists (revoke them first), then drops the
functions, views, schema, policies, grants and the two tables, and the four
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
READER_ROLE = "agenticorg_bi_reader"
ROLES = (BI_ROLE, LOOKUP_ROLE, ADMIN_ROLE, READER_ROLE)
SCHEMA = "insights_bi"
LOGINS = "insights_bi_logins"
SETTINGS = "insights_bi_settings"

# Login names the functions accept (core/insights/bi_access.py makes them).
ROLE_NAME_PATTERN = "^bi_[a-z0-9_]{8,48}$"
# RFC 5802/7677 verifier as PostgreSQL stores it; anything else is refused so a
# plaintext password can never be passed through.
VERIFIER_PATTERN = "^SCRAM-SHA-256[$][0-9]{4,7}:[A-Za-z0-9+/]{16,64}={0,2}[$][A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}=$"
# Every SECURITY DEFINER function here: pg_catalog first, temporary objects last.
SEARCH_PATH = "pg_catalog, pg_temp"
SEARCH_PATH_CONFIG = f"search_path={SEARCH_PATH}"
# run_facts_rows() returns this many days back from now (and anything later).
RUN_FACTS_WINDOW_DAYS = 400

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
# view -> its reader function (schema insights_bi, no arguments).
READER_FUNCTIONS: dict[str, str] = {view: f"{view}_rows" for view in VIEWS}

READ_POLICY = "insights_bi_read"
ONLY_OWN_POLICY = "insights_bi_only_own"

TENANT_FN = "public.insights_bi_tenant()"
CREATE_FN = "public.insights_bi_create_login(uuid, text, text, uuid, integer)"
REVOKE_FN = "public.insights_bi_revoke_login(uuid, text)"

_TENANT_FN_SQL = f"""
    CREATE FUNCTION public.insights_bi_tenant()
    RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = {SEARCH_PATH}
    AS $function$
        SELECT l.tenant_id FROM public.{LOGINS} l
        WHERE l.role_name = session_user::text AND l.revoked_at IS NULL
          AND (pg_catalog.pg_is_in_recovery()
               OR COALESCE((SELECT s.allow_primary FROM public.{SETTINGS} s), false))
    $function$
"""

_CREATE_FN_SQL = f"""
    CREATE FUNCTION public.insights_bi_create_login(
        p_tenant uuid, p_role text, p_scram_verifier text, p_created_by uuid, p_max integer)
    RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = {SEARCH_PATH}
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
        -- A login that could create objects in schema public could plant
        -- objects other sessions resolve; refuse until CREATE is revoked.
        IF pg_catalog.has_schema_privilege('public', 'public', 'CREATE')
            OR pg_catalog.has_schema_privilege('{BI_ROLE}', 'public', 'CREATE') THEN
            RAISE EXCEPTION 'insights_bi_public_schema_writable' USING ERRCODE = 'BI503';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.tenants t WHERE t.id = p_tenant AND t.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'insights_bi_unknown_tenant' USING ERRCODE = 'BI404';
        END IF;
        -- One creation at a time per tenant, so the limit holds under concurrency.
        PERFORM pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended('insights_bi_logins/' || p_tenant::text, 0));
        SELECT count(*) INTO v_active FROM public.{LOGINS} l
        WHERE l.tenant_id = p_tenant AND l.revoked_at IS NULL;
        IF v_active >= p_max THEN
            RAISE EXCEPTION 'insights_bi_limit_reached' USING ERRCODE = 'BI409';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = p_role)
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
    SET search_path = {SEARCH_PATH}
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
        -- access for every new statement.
        UPDATE public.{LOGINS} l SET revoked_at = COALESCE(l.revoked_at, now())
        WHERE l.tenant_id = p_tenant AND l.role_name = p_role;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'insights_bi_unknown_login' USING ERRCODE = 'BI404';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = p_role) THEN
            RETURN 'dropped';
        END IF;
        EXECUTE format('ALTER ROLE %I NOLOGIN', p_role);
        BEGIN
            EXECUTE format('DROP ROLE %I', p_role);
        EXCEPTION WHEN dependent_objects_still_exist THEN
            -- Something still depends on the role (an object it owns): it
            -- stays, unable to log in and mapped to no tenant, and loses the
            -- group. Privilege checks read the catalogue, not the snapshot, so
            -- this also ends access inside a transaction it already has open.
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                       JOIN pg_catalog.pg_roles g ON g.oid = m.roleid
                       JOIN pg_catalog.pg_roles r ON r.oid = m.member
                       WHERE g.rolname = '{BI_ROLE}' AND r.rolname = p_role) THEN
                EXECUTE format('REVOKE {BI_ROLE} FROM %I', p_role);
            END IF;
            RETURN 'nologin';
        END;
        RETURN 'dropped';
    END
    $function$
"""


def _roles_sql() -> str:
    return f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BI_ROLE}') THEN
                CREATE ROLE {BI_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BI_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI group role';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOOKUP_ROLE}') THEN
                CREATE ROLE {LOOKUP_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
                    NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{LOOKUP_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI lookup role';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{READER_ROLE}') THEN
                CREATE ROLE {READER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
                    NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{READER_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI reader role';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{ADMIN_ROLE}') THEN
                CREATE ROLE {ADMIN_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT NOREPLICATION
                    NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{ADMIN_ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolreplication OR rolinherit
                     OR NOT rolcreaterole)) THEN
                RAISE EXCEPTION 'Unsafe preexisting BI admin role';
            END IF;
            -- The BI roles are never members of anything that could widen what
            -- a BI login reads. Two memberships are expected: the admin role's
            -- ADMIN OPTION on the group, and (PostgreSQL 16) the ADMIN-only
            -- membership CREATE ROLE gives the admin role in each BI login it
            -- creates (a BI login being a member of the group).
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
                       JOIN pg_catalog.pg_roles r ON r.oid = m.member
                       JOIN pg_catalog.pg_roles g ON g.oid = m.roleid
                       WHERE r.rolname IN ('{BI_ROLE}', '{LOOKUP_ROLE}', '{ADMIN_ROLE}', '{READER_ROLE}')
                         AND NOT (r.rolname = '{ADMIN_ROLE}' AND g.rolname = '{BI_ROLE}')
                         AND NOT (r.rolname = '{ADMIN_ROLE}' AND g.rolcanlogin AND EXISTS (
                             SELECT 1 FROM pg_catalog.pg_auth_members b
                             JOIN pg_catalog.pg_roles bg ON bg.oid = b.roleid
                             WHERE b.member = g.oid AND bg.rolname = '{BI_ROLE}'))) THEN
                RAISE EXCEPTION 'BI roles must not be members of other roles';
            END IF;
            -- Nobody may use the reader, lookup or admin role's privileges
            -- (an ADMIN-only membership, as PostgreSQL 16 gives a role's
            -- creator, confers none).
            IF current_setting('server_version_num')::integer >= 160000 THEN
                IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles g
                           WHERE g.rolname IN ('{READER_ROLE}', '{LOOKUP_ROLE}', '{ADMIN_ROLE}')
                             AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
                                         WHERE r.oid <> g.oid AND NOT r.rolsuper
                                           AND (pg_catalog.pg_has_role(r.oid, g.oid, 'USAGE')
                                                OR pg_catalog.pg_has_role(r.oid, g.oid, 'SET')))) THEN
                    RAISE EXCEPTION 'Roles hold access to the BI reader, lookup or admin role';
                END IF;
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
    op.execute(f"REVOKE ALL ON public.{LOGINS} FROM {LOOKUP_ROLE}, {ADMIN_ROLE}, {BI_ROLE}, {READER_ROLE}")
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


def _settings_table() -> None:
    """One row: whether BI logins may read on a server that is not a standby."""
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SETTINGS} (
            id BOOLEAN PRIMARY KEY DEFAULT true,
            allow_primary BOOLEAN NOT NULL DEFAULT false,
            CONSTRAINT ck_insights_bi_settings_single_row CHECK (id)
        )
        """
    )
    op.execute(f"INSERT INTO {SETTINGS} (id, allow_primary) VALUES (true, false) ON CONFLICT (id) DO NOTHING")
    # Written by the migration owner only; the lookup role reads the switch.
    op.execute(f"ALTER TABLE {SETTINGS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL ON public.{SETTINGS} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON public.{SETTINGS} FROM {LOOKUP_ROLE}, {ADMIN_ROLE}, {BI_ROLE}, {READER_ROLE}")
    op.execute(f"GRANT SELECT (allow_primary) ON public.{SETTINGS} TO {LOOKUP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS insights_bi_settings_lookup_read ON public.{SETTINGS}")
    op.execute(
        f"CREATE POLICY insights_bi_settings_lookup_read ON public.{SETTINGS} FOR SELECT TO {LOOKUP_ROLE} USING (true)"
    )
    op.execute(
        f"""
        DO $$ BEGIN
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                REVOKE ALL ON public.{SETTINGS} FROM agenticorg_app;
            END IF;
        END $$
        """
    )


def _public_schema_notice() -> None:
    # insights_bi_create_login refuses while schema public is writable by PUBLIC
    # or the BI group; the migration only says so (production's state is not
    # known here, and revoking CREATE could break other users of the schema).
    op.execute(
        f"""
        DO $$ BEGIN
            IF has_schema_privilege('public', 'public', 'CREATE')
                OR has_schema_privilege('{BI_ROLE}', 'public', 'CREATE') THEN
                RAISE NOTICE 'Schema public is writable by PUBLIC or {BI_ROLE}: BI logins cannot be created '
                    'until CREATE is revoked (REVOKE CREATE ON SCHEMA public FROM PUBLIC)';
            END IF;
        END $$
        """
    )


def _source_grants_and_policies() -> None:
    # Refuse a source without row-level security: its rows would be readable
    # through the reader's column grants regardless of any policy.
    tables = ", ".join(f"'{t}'" for t in SOURCES)
    op.execute(
        f"""
        DO $$ DECLARE missing text; BEGIN
            SELECT string_agg(t, ', ') INTO missing FROM unnest(ARRAY[{tables}]) AS t
            WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n
                              ON n.oid = c.relnamespace
                              WHERE n.nspname = 'public' AND c.relname = t AND c.relrowsecurity);
            IF missing IS NOT NULL THEN
                RAISE EXCEPTION 'BI source tables without row-level security: %', missing;
            END IF;
        END $$
        """
    )
    # BI logins hold nothing in schema public; only the reader reads there.
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {BI_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {READER_ROLE}")
    for source, columns in VIEWS.values():
        readable = tuple(dict.fromkeys(("tenant_id", *columns)))
        op.execute(f"REVOKE ALL ON public.{source} FROM {BI_ROLE}")
        op.execute(f"REVOKE ALL ON public.{source} FROM {READER_ROLE}")
        op.execute(f"GRANT SELECT ({', '.join(readable)}) ON public.{source} TO {READER_ROLE}")
        op.execute(f"DROP POLICY IF EXISTS {READ_POLICY} ON public.{source}")
        op.execute(
            f"CREATE POLICY {READ_POLICY} ON public.{source} AS PERMISSIVE FOR SELECT TO {READER_ROLE} "
            f"USING (tenant_id = (SELECT public.insights_bi_tenant()))"
        )
        op.execute(f"DROP POLICY IF EXISTS {ONLY_OWN_POLICY} ON public.{source}")
        op.execute(
            f"CREATE POLICY {ONLY_OWN_POLICY} ON public.{source} AS RESTRICTIVE FOR SELECT TO {READER_ROLE} "
            f"USING (tenant_id = (SELECT public.insights_bi_tenant()))"
        )


def _column_types(source: str, columns: tuple[str, ...]) -> dict[str, str]:
    """The catalogue type of each exposed column (built-in types only, without type modifiers)."""
    rows = (
        op.get_bind()
        .execute(
            text(
                "SELECT a.attname, pg_catalog.format_type(a.atttypid, NULL) AS type_name, "
                "t.typnamespace = 'pg_catalog'::regnamespace AS builtin "
                "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
                "WHERE a.attrelid = to_regclass(:rel) AND a.attnum > 0 AND NOT a.attisdropped"
            ),
            {"rel": f"public.{source}"},
        )
        .all()
    )
    found = {row.attname: row for row in rows}
    missing = [column for column in columns if column not in found]
    if missing:
        raise RuntimeError(f"public.{source} lacks BI columns {missing}")
    foreign = [column for column in columns if not found[column].builtin]
    if foreign:
        # A type outside pg_catalog would be resolved through a search path.
        raise RuntimeError(f"public.{source} BI columns {foreign} are not of built-in types")
    return {column: str(found[column].type_name) for column in columns}


def _reader_function_sql(view: str, source: str, columns: tuple[str, ...]) -> str:
    types = _column_types(source, columns)
    returns = ", ".join(f"{column} {types[column]}" for column in columns)
    select = ", ".join(f"s.{column}" for column in columns)
    where = "s.tenant_id = (SELECT public.insights_bi_tenant())"
    if view == "run_facts":
        where += f" AND s.started_at >= pg_catalog.now() - interval '{RUN_FACTS_WINDOW_DAYS} days'"
    return f"""
    CREATE FUNCTION {SCHEMA}.{READER_FUNCTIONS[view]}()
    RETURNS TABLE ({returns})
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = {SEARCH_PATH}
    AS $function$
        SELECT {select} FROM public.{source} s WHERE {where}
    $function$
    """


def _schema_and_views() -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF to_regnamespace('{SCHEMA}') IS NULL THEN
                CREATE SCHEMA {SCHEMA};
            ELSIF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner
                              WHERE n.nspname = '{SCHEMA}' AND r.rolname = current_user) THEN
                RAISE EXCEPTION 'Unsafe preexisting schema {SCHEMA}';
            END IF;
        END $$
        """
    )
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {BI_ROLE}, {READER_ROLE}, {LOOKUP_ROLE}, {ADMIN_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {BI_ROLE}")
    # Rebuilt on every run: the schema owner (the installer) may drop anything in it.
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{view}")
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{READER_FUNCTIONS[view]}()")
    # The reader functions, created by the installer and handed to the reader
    # through a temporary membership and schema CREATE, which it keeps neither of.
    created = "\n".join(
        f"""
            EXECUTE $ddl${_reader_function_sql(view, source, columns)}$ddl$;
            REVOKE ALL ON FUNCTION {SCHEMA}.{READER_FUNCTIONS[view]}() FROM PUBLIC;
            ALTER FUNCTION {SCHEMA}.{READER_FUNCTIONS[view]}() OWNER TO {READER_ROLE};
            GRANT EXECUTE ON FUNCTION {SCHEMA}.{READER_FUNCTIONS[view]}() TO {BI_ROLE};"""
        for view, (source, columns) in VIEWS.items()
    )
    op.execute(
        f"""
        DO $$ BEGIN
            EXECUTE format('GRANT {READER_ROLE} TO %I', current_user);
            GRANT CREATE ON SCHEMA {SCHEMA} TO {READER_ROLE};
            {created}
            REVOKE ALL ON SCHEMA {SCHEMA} FROM {READER_ROLE};
            EXECUTE format('REVOKE {READER_ROLE} FROM %I', current_user);
            IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
                NULL;
            ELSIF current_setting('server_version_num')::integer >= 160000 THEN
                IF pg_has_role(current_user, '{READER_ROLE}', 'USAGE')
                    OR pg_has_role(current_user, '{READER_ROLE}', 'SET') THEN
                    RAISE EXCEPTION 'Installer retained BI reader role access';
                END IF;
            ELSIF pg_has_role(current_user, '{READER_ROLE}', 'MEMBER') THEN
                RAISE EXCEPTION 'Installer retained BI reader role membership';
            END IF;
        END $$
        """
    )
    for view, (_source, columns) in VIEWS.items():
        # Plain view, invoker's rights: it reaches no table, only the function,
        # which the BI group may execute.
        op.execute(
            f"CREATE VIEW {SCHEMA}.{view} WITH (security_invoker = true, security_barrier = true) "
            f"AS SELECT {', '.join(columns)} FROM {SCHEMA}.{READER_FUNCTIONS[view]}()"
        )
        op.execute(f"REVOKE ALL ON {SCHEMA}.{view} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {SCHEMA}.{view} TO {BI_ROLE}")
    # Nothing but the views and the reader functions lives in the schema, and
    # each function is the reader's, SECURITY DEFINER with the fixed search path.
    names = ", ".join(f"'{v}'" for v in VIEWS)
    functions = ", ".join(f"'{f}'" for f in READER_FUNCTIONS.values())
    op.execute(
        f"""
        DO $$ DECLARE extra text; BEGIN
            SELECT string_agg(c.relname, ', ') INTO extra FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = '{SCHEMA}' AND c.relname NOT IN ({names});
            IF extra IS NOT NULL THEN
                RAISE EXCEPTION 'Unexpected relations in schema {SCHEMA}: %', extra;
            END IF;
            SELECT string_agg(p.proname, ', ') INTO extra FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = '{SCHEMA}'
              AND (p.proname NOT IN ({functions}) OR p.pronargs <> 0 OR NOT p.prosecdef
                   OR p.proowner <> '{READER_ROLE}'::regrole
                   OR p.proconfig IS DISTINCT FROM ARRAY['{SEARCH_PATH_CONFIG}']
                   OR has_function_privilege('public', p.oid, 'EXECUTE'));
            IF extra IS NOT NULL THEN
                RAISE EXCEPTION 'Unexpected or unsafe functions in schema {SCHEMA}: %', extra;
            END IF;
        END $$
        """
    )


def _owned_function(signature: str, ddl: str, owner: str, returns: str, nargs: int, grantees: str) -> str:
    """Create ``signature`` owned by ``owner`` (or replace the owner's existing one), verify it, grant EXECUTE.

    Runs inside the DO block that holds the temporary membership of ``owner``.
    """
    replace = ddl.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    return f"""
            IF to_regprocedure('{signature}') IS NULL THEN
                EXECUTE $ddl${ddl}$ddl$;
                REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
                ALTER FUNCTION {signature} OWNER TO {owner};
            ELSIF EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
                WHERE p.oid = '{signature}'::regprocedure AND r.rolname = '{owner}' AND p.prosecdef
            ) THEN
                -- The owner's own function: brought to this definition in place.
                EXECUTE $ddl${replace}$ddl$;
            ELSE
                RAISE EXCEPTION 'Unsafe preexisting function {signature}';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
                WHERE p.oid = '{signature}'::regprocedure
                    AND r.rolname = '{owner}' AND p.prosecdef
                    AND p.pronargs = {nargs} AND p.prorettype = '{returns}'::regtype
                    AND p.proconfig = ARRAY['{SEARCH_PATH_CONFIG}']
            ) THEN
                RAISE EXCEPTION 'Unsafe preexisting function {signature}';
            END IF;
            REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
            {grantees}
    """


def _functions() -> None:
    app_grants = (
        "EXECUTE format('GRANT EXECUTE ON FUNCTION {fn} TO %I', current_user);\n"
        "            IF to_regrole('agenticorg_app') IS NOT NULL THEN\n"
        "                GRANT EXECUTE ON FUNCTION {fn} TO agenticorg_app;\n"
        "            END IF;"
    )
    # Only the reader calls the tenant lookup (from its functions and policies).
    tenant_grants = (
        f"REVOKE ALL ON FUNCTION {TENANT_FN} FROM {BI_ROLE};\n"
        f"            GRANT EXECUTE ON FUNCTION {TENANT_FN} TO {READER_ROLE};"
    )
    body = "\n".join(
        (
            _owned_function(TENANT_FN, _TENANT_FN_SQL, LOOKUP_ROLE, "uuid", 0, tenant_grants),
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
            IF (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
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
    _settings_table()
    _functions()
    _source_grants_and_policies()
    _schema_and_views()
    _public_schema_notice()


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
            "SELECT string_agg(r.rolname, ', ') FROM pg_catalog.pg_auth_members m "
            "JOIN pg_catalog.pg_roles g ON g.oid = m.roleid JOIN pg_catalog.pg_roles r ON r.oid = m.member "
            "WHERE g.rolname = :group AND NOT m.admin_option"
        ),
        {"group": BI_ROLE},
    ).scalar()
    if members:
        raise RuntimeError(
            f"BI logins still exist on the cluster ({members}): drop them before downgrading v6z108_insights_bi"
        )
    # The installer owns the schema, so it may drop the reader's functions in it.
    for view in VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {SCHEMA}.{view}")
        op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.{READER_FUNCTIONS[view]}()")
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
            IF to_regrole('{READER_ROLE}') IS NOT NULL THEN
                {"; ".join(f"REVOKE ALL ON public.{s} FROM {READER_ROLE}" for s in SOURCES)};
                REVOKE ALL ON SCHEMA public FROM {READER_ROLE};
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
    op.execute(f"DROP TABLE IF EXISTS {SETTINGS}")
    op.execute(f"DROP TABLE IF EXISTS {LOGINS}")
    # Roles are cluster-wide: one that another database still uses stays.
    op.execute(
        f"""
        DO $$ DECLARE r text; BEGIN
            FOREACH r IN ARRAY ARRAY['{ADMIN_ROLE}', '{LOOKUP_ROLE}', '{READER_ROLE}', '{BI_ROLE}'] LOOP
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
