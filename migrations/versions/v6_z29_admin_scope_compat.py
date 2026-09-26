# SPDX-License-Identifier: Apache-2.0
"""Keep administrator access for API keys that held a colon-delimited admin sub-scope.

Revision ID: v6z29_admin_scope_compat
Revises: v6z28_case_excerpts
Create Date: 2026-09-26

Until the exact-match fix (``core.rbac.has_admin_scope``, merged 2026-09-25 05:58:53 UTC),
six admin checks accepted any scope *starting with* ``agenticorg:admin``. That let an agent
registered in a domain such as ``administration`` pass as an administrator, which is why the
check is now exact. It also meant an API key issued with a sub-scope such as
``agenticorg:admin:full`` was an administrator; API-key scopes are free-form, so such keys
may exist, and after the fix they silently lose admin.

This migration gives such a key the exact ``agenticorg:admin`` scope - the access it
already had - only when all of these hold:

* it holds a scope of the form ``agenticorg:admin:<anything>``. Look-alikes such as
  ``agenticorg:administration:read`` or ``agenticorg:adminx`` were never meant as admin and
  are exactly what the fix closed, so they are left alone;
* it was created before the exact-match fix was merged, so it was certainly an
  administrator under the old rule. A key created later may have been issued, or
  deliberately left as a sub-scope, in a deployment already running exact matching, so it
  is not elevated - even in a deployment that took the fix later, where the operator
  decides with the audit query;
* its owner is still an active administrator (``users.role = 'admin'`` and
  ``status = 'active'``, as ``get_active_human_admin`` requires). Before the fix the
  key-creation gate could itself be passed through the prefix hole; a key whose owner is
  not an admin is not restored.

Everything else stays for the operator: the audit query in the CHANGELOG still finds keys
with any other admin-looking scope. Status and other scopes are unchanged, and the update is
idempotent. The ids of the keys changed are logged (never key material).

``api_keys`` has FORCE ROW LEVEL SECURITY with a pre-auth policy that shows every row when no
tenant context is set, so the update reaches every key for any migration role. A tenant
context set on the connection would narrow it silently, so the migration refuses to run with
one.

``downgrade`` is a no-op: the added scope grants exactly what the key had before the
exact-match fix, and removing it could strip a scope an operator later set on purpose.
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "v6z29_admin_scope_compat"
down_revision = "v6z28_case_excerpts"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# The exact-match fix was merged at 2026-09-25 05:58:53 UTC; no deployment ran it earlier.
RESTORE_ADMIN_SQL = """
    UPDATE api_keys
       SET scopes = array_append(scopes, 'agenticorg:admin')
     WHERE NOT ('agenticorg:admin' = ANY(scopes))
       AND EXISTS (SELECT 1 FROM unnest(scopes) AS s WHERE s LIKE 'agenticorg:admin:%')
       AND created_at < TIMESTAMPTZ '2026-09-25 05:58:53+00'
       AND EXISTS (SELECT 1 FROM users AS u WHERE u.id = api_keys.user_id AND u.role = 'admin' AND u.status = 'active')
 RETURNING id, tenant_id
"""


def upgrade() -> None:
    bind = op.get_bind()
    tenant_context = bind.execute(sa.text("SELECT COALESCE(current_setting('agenticorg.tenant_id', true), '')")).scalar()
    if tenant_context:
        raise RuntimeError(
            "v6z29_admin_scope_compat must run without a tenant context: agenticorg.tenant_id is set, "
            "so row-level security would hide other tenants' API keys from the update"
        )
    restored = bind.execute(sa.text(RESTORE_ADMIN_SQL)).all()
    logger.info(
        "v6z29_admin_scope_compat restored agenticorg:admin on %d API key(s): %s",
        len(restored),
        ", ".join(f"{row.tenant_id}/{row.id}" for row in restored) or "none",
    )


def downgrade() -> None:
    pass
