# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights exports as PowerPoint and Parquet.

Revision ID: v6z116_insights_export_formats
Revises: v6z115_insights_counters
Create Date: 2026-11-04

- ``insights_exports.kind`` also accepts ``view_pptx`` (a view as a PowerPoint
  deck: its tiles, each chart as a native chart with its text summary, and the
  chart's data as a table).
- ``insights_exports.format`` also accepts ``pptx`` and ``parquet`` (a metric or
  explorer table as typed Parquet columns, with its filters and definitions in
  the file's metadata).

Downgrade restores both checks, refusing while exports of the new kind or
formats exist.
"""

# SQL is built only from the module constants below; there is no runtime input.

from __future__ import annotations

from alembic import op

revision = "v6z116_insights_export_formats"
down_revision = "v6z115_insights_counters"
branch_labels = None
depends_on = None

_KINDS_V6Z113 = "'metric','explorer','run_bundle','view_pdf','finance_pack','evidence_pack','partner_pack'"
_KINDS = _KINDS_V6Z113 + ",'view_pptx'"
_FORMATS_V6Z110 = "'csv','xlsx','json','pdf','zip'"
_FORMATS = _FORMATS_V6Z110 + ",'pptx','parquet'"


def _checks(kinds: str, formats: str) -> None:
    op.execute("ALTER TABLE insights_exports DROP CONSTRAINT IF EXISTS ck_insights_exports_kind;")
    op.execute(f"ALTER TABLE insights_exports ADD CONSTRAINT ck_insights_exports_kind CHECK (kind IN ({kinds}));")
    op.execute("ALTER TABLE insights_exports DROP CONSTRAINT IF EXISTS ck_insights_exports_format;")
    op.execute(f"ALTER TABLE insights_exports ADD CONSTRAINT ck_insights_exports_format CHECK (format IN ({formats}));")


def upgrade() -> None:
    _checks(_KINDS, _FORMATS)


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM insights_exports WHERE kind = 'view_pptx' OR format IN ('pptx', 'parquet')
            ) THEN
                RAISE EXCEPTION 'PowerPoint or Parquet exports exist; delete them before downgrading v6z116';
            END IF;
        END $$
        """
    )
    _checks(_KINDS_V6Z113, _FORMATS_V6Z110)
