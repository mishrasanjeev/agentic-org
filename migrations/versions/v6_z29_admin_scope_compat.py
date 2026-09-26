# SPDX-License-Identifier: Apache-2.0
"""Keep administrator access for API keys that held a colon-delimited admin sub-scope.

Revision ID: v6z29_admin_scope_compat
Revises: v6z28_case_excerpts
Create Date: 2026-09-26

Until the exact-match fix (``core.rbac.has_admin_scope``), six admin checks accepted any
scope *starting with* ``agenticorg:admin``. That let an agent registered in a domain such as
``administration`` pass as an administrator, which is why the check is now exact. It also
meant an API key issued with a sub-scope such as ``agenticorg:admin:full`` was an
administrator, and API-key scopes are free-form, so such keys may exist. After the fix they
silently lose admin.

This migration keeps those keys working: every API key holding a scope of the form
``agenticorg:admin:<anything>`` gains the exact ``agenticorg:admin`` scope, which is the
access it already had. Only the colon-delimited form is carried over. A look-alike such as
``agenticorg:administration:read`` or ``agenticorg:adminx`` was never meant as admin and is
exactly what the fix closed, so it is left alone. Nothing else about a key changes, and a
revoked or expired key stays revoked or expired.

``row_security`` is turned off for the statement so a row-level-security policy can never
silently hide a key from the update; a role that cannot bypass RLS gets an error instead.

``downgrade`` is a no-op: the added scope grants exactly what the key had before this
revision's parent, and removing it could strip a scope an operator later set on purpose.
"""

from alembic import op

revision = "v6z29_admin_scope_compat"
down_revision = "v6z28_case_excerpts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL row_security = off")
    op.execute(
        """
        UPDATE api_keys
           SET scopes = array_append(scopes, 'agenticorg:admin')
         WHERE NOT ('agenticorg:admin' = ANY(scopes))
           AND EXISTS (
                 SELECT 1 FROM unnest(scopes) AS s
                  WHERE s LIKE 'agenticorg:admin:%'
               )
        """
    )


def downgrade() -> None:
    pass
