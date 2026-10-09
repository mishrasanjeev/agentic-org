# SPDX-License-Identifier: Apache-2.0
"""Join the registry constraint hardening and functional feature migrations.

Revision ID: v6z78_security_release_merge
Revises: v6z49_registry_from_state, v6z77_personalisation

Both parent branches retain their forward migrations. No historical revision
is renumbered, and the merge itself changes no data or constraints.
"""

revision = "v6z78_security_release_merge"
down_revision = ("v6z49_registry_from_state", "v6z77_personalisation")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
