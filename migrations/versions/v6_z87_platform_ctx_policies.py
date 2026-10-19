# SPDX-License-Identifier: Apache-2.0
"""Explicit platform-context read policies for the pre-auth cross-tenant lookups.

Revision ID: v6z87_platform_ctx_policies
Revises: v6z86_telegram_answers

Row-level security is enabled and forced on every tenant table (v6z16,
v6z68 and the per-feature revisions since), but it is a control only for a
role that is neither a superuser nor ``BYPASSRLS``. To run the API as such a
role (``scripts/create_app_role.sql``, ``docs/RLS_APP_ROLE.md``) every
session must carry its context: tenant-scoped work binds
``agenticorg.tenant_id`` (``get_tenant_session`` / ``bind_tenant_context``);
the few flows that run before a tenant is known go through
``core.database.get_platform_session``, which sets the transaction-local GUC
``agenticorg.platform_ctx = '1'``.

This revision adds one permissive ``FOR SELECT`` policy per table below that
honours that marker. Permissive policies are OR-ed, so the existing
``*_tenant_isolation`` policies are untouched and tenant-bound sessions see
exactly what they saw before. No platform policy grants INSERT, UPDATE or
DELETE.

* ``sso_configs``     - ``GET /auth/sso/providers?email=`` resolves the
                        tenant from the e-mail domain before login.
* ``tenant_branding`` - public issuer-branded lookup by custom domain or slug.
* ``agents``          - Grantex agent DID to tenant resolution in the auth
                        middleware (the DID chooses the tenant).

The set is deliberately minimal. ``tenants``, ``demo_requests`` and
``health_check_history`` have no ``tenant_id`` and no RLS; ``users`` and
``api_keys`` already have the v6z16 pre-auth shape that allows the no-tenant
lookup. Cross-tenant beat jobs enumerate the ``tenants`` catalogue and then
work inside each tenant's own session, so they need no platform policy.

Idempotent and safe on partial schemas (``to_regclass`` guard). Downgrade
drops only the policies this revision created.
"""

from alembic import op

revision = "v6z87_platform_ctx_policies"
down_revision = "v6z86_telegram_answers"
branch_labels = None
depends_on = None

PLATFORM_CTX_PREDICATE = "current_setting('agenticorg.platform_ctx', true) = '1'"

PLATFORM_READ_TABLES: tuple[str, ...] = (
    "sso_configs",
    "tenant_branding",
    "agents",
)


def _policy_name(table: str) -> str:
    return f"{table}_platform_ctx"


def _guarded(table: str, body: str) -> str:
    # Table names are the hard-coded constants above, never input.
    return f"DO $$ BEGIN IF to_regclass('public.{table}') IS NOT NULL THEN {body} END IF; END $$;"


def upgrade() -> None:
    for table in PLATFORM_READ_TABLES:
        policy = _policy_name(table)
        body = (
            f"DROP POLICY IF EXISTS {policy} ON {table}; "
            f"CREATE POLICY {policy} ON {table} FOR SELECT USING ({PLATFORM_CTX_PREDICATE});"
        )
        op.execute(_guarded(table, body))  # noqa: S608  # nosec B608


def downgrade() -> None:
    for table in PLATFORM_READ_TABLES:
        op.execute(_guarded(table, f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table};"))  # noqa: S608  # nosec B608
