# SPDX-License-Identifier: Apache-2.0
"""Let a role bound by row-level security read global feature-flag rows.

Revision ID: v6z30_flag_global_read
Revises: v6z29_admin_scope_compat
Create Date: 2026-09-27

``feature_flags`` is FORCE ROW LEVEL SECURITY since ``v6z16_rls_coverage`` with the one
policy ``tenant_id::text = current_setting('agenticorg.tenant_id', true)``. A global row
has ``tenant_id`` NULL and never satisfies it, so for a role that is neither a superuser
nor ``BYPASSRLS`` ``core.feature_flags`` found no global row in any tenant session, nor
under the nil tenant: every global default, including the operator's global rows of the
authority flags (``core.feature_flags.RESERVED_FLAG_KEYS``), was ignored.

This revision adds a second, SELECT-only policy that admits ``tenant_id IS NULL`` rows.
Permissive policies are ORed, so a session sees its tenant's rows and the global rows,
and still no other tenant's rows. A global row carries no tenant data: its columns are
``id``, ``tenant_id`` (NULL), ``flag_key``, ``enabled``, ``rollout_percentage``,
``description`` and timestamps, and the only writer of global rows is the operator
script ``scripts/authority_flags.py``; the tenant feature-flag API reads and writes only
the caller's tenant rows.

There is deliberately no INSERT, UPDATE or DELETE policy for NULL-tenant rows: the tenant
policy's WITH CHECK refuses a NULL-tenant insert, and Postgres skips NULL-tenant rows in
an UPDATE or DELETE. Global rows are written by operators on a privileged role
(superuser or ``BYPASSRLS``).

``downgrade`` drops the policy (local use; migrations are forward-only in production).
"""

from alembic import op

revision = "v6z30_flag_global_read"
down_revision = "v6z29_admin_scope_compat"
branch_labels = None
depends_on = None

POLICY = "feature_flags_global_read"


def _guarded(body: str) -> str:
    # Safe on a partial schema: a database without the table is left alone.
    return f"DO $$ BEGIN IF to_regclass('public.feature_flags') IS NOT NULL THEN {body} END IF; END $$;"


def upgrade() -> None:
    op.execute(
        _guarded(
            f"DROP POLICY IF EXISTS {POLICY} ON feature_flags; "
            f"CREATE POLICY {POLICY} ON feature_flags FOR SELECT USING (tenant_id IS NULL);"
        )
    )


def downgrade() -> None:
    op.execute(_guarded(f"DROP POLICY IF EXISTS {POLICY} ON feature_flags;"))
