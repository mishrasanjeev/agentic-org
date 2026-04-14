"""v4.8.1 — ORM sync for pre-existing tables

No-op migration. Documents that ``core.models.kpi_cache.KPICache`` and
``core.models.industry_pack_install.IndustryPackInstall`` were added
to mirror tables that have always existed in this repo via raw DDL:

  - kpi_cache — created in v480_baseline (and originally in init_db())
  - industry_pack_installs — created in v400_apex

These ORM models let ``BaseModel.metadata.create_all`` produce the
full schema for hermetic tests (tests/e2e/test_cxo_flows.py). No DDL
changes are required in production — the tables are already there on
every deployed environment.

Revision ID: v481_orm_sync
Revises: v480_baseline
Create Date: 2026-04-14
"""

revision = "v481_orm_sync"
down_revision = "v480_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No DDL. The ORM models added in core/models/kpi_cache.py and
    # core/models/industry_pack_install.py describe tables that
    # already exist in every deployed environment.
    pass


def downgrade() -> None:
    pass
