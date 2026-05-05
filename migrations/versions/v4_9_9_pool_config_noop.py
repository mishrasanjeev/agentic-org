"""Runtime DB pool configuration guard migration.

Revision ID: v499_pool_config_noop
Revises: v498_health_check_history
Create Date: 2026-05-05

The paired code change only wires SQLAlchemy pool sizing from settings.
No database schema changes are required, but CI requires an Alembic
revision whenever ``core/database.py`` changes.
"""

from __future__ import annotations

revision = "v499_pool_config_noop"
down_revision = "v498_health_check_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
