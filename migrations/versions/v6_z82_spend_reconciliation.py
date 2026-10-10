# SPDX-License-Identifier: Apache-2.0
"""Spend reconciliation: provider invoices and their lines, reconciliation runs and their items.

Revision ID: v6z82_spend_reconciliation
Revises: v6z81_spend_gpu
Create Date: 2026-10-10

``spend_invoices``: a provider's invoice or billing export for one billing
month (the provider's own calendar), imported from CSV or JSON; a re-import
of the same reference supersedes it, both kept. ``spend_invoice_lines``: its
lines with a kind (usage, credit, tax, fee, commitment), usage type, SKU,
card unit, quantity, amount and an optional billing date.
``spend_reconciliations``: one run of a provider's month, metered usage
against the invoice in two figures (as stored, and re-priced at the cards
known at run time), with the cards and FX rows it used; a re-run supersedes
the earlier run. ``spend_reconciliation_items``: the compared keys of a run
and its non-usage lines, with acceptances (``core/spend/``). All tenant
scoped under forced row-level policies. Foreign keys are composite on
``(tenant_id, id)`` so a row can never reference another tenant's row; every
foreign key carries a leading index. Nothing deletes these rows. No existing
table is altered.
"""

from alembic import op

revision = "v6z82_spend_reconciliation"
down_revision = "v6z81_spend_gpu"
branch_labels = None
depends_on = None

TABLES = ("spend_invoices", "spend_invoice_lines", "spend_reconciliations", "spend_reconciliation_items")


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_invoices (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            period_start DATE NOT NULL,
            invoice_ref VARCHAR(128) NOT NULL,
            currency CHAR(3) NOT NULL,
            total_amount NUMERIC(24,10) NOT NULL,
            usage_amount NUMERIC(24,10) NOT NULL,
            line_count INTEGER NOT NULL,
            source VARCHAR(8) NOT NULL,
            file_sha256 CHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'current',
            superseded_by UUID NULL,
            imported_by VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ux_spend_invoices_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_spend_invoices_superseded_by FOREIGN KEY (tenant_id, superseded_by)
                REFERENCES spend_invoices(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_spend_invoices_period CHECK (EXTRACT(DAY FROM period_start) = 1),
            CONSTRAINT ck_spend_invoices_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_spend_invoices_source CHECK (source IN ('csv','json')),
            CONSTRAINT ck_spend_invoices_status CHECK (status IN ('current','superseded'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_invoices_current "
        "ON spend_invoices(tenant_id, provider, period_start, invoice_ref) WHERE status = 'current';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_invoices_superseded_by ON spend_invoices(tenant_id, superseded_by);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_invoices_tenant_period ON spend_invoices(tenant_id, period_start, provider);"
    )
    _tenant_policy("spend_invoices")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_invoice_lines (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            invoice_id UUID NOT NULL,
            line_no INTEGER NOT NULL,
            line_kind VARCHAR(16) NOT NULL DEFAULT 'usage',
            usage_type VARCHAR(32) NULL,
            model_sku VARCHAR(128) NOT NULL DEFAULT '',
            unit VARCHAR(32) NULL,
            quantity NUMERIC(24,6) NULL,
            amount NUMERIC(24,10) NOT NULL,
            usage_date DATE NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_spend_invoice_lines_invoice FOREIGN KEY (tenant_id, invoice_id)
                REFERENCES spend_invoices(tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_spend_invoice_lines_kind CHECK (line_kind IN ('usage','credit','tax','fee','commitment')),
            CONSTRAINT ck_spend_invoice_lines_usage CHECK (line_kind <> 'usage' OR (usage_type IS NOT NULL AND amount >= 0)),
            CONSTRAINT ck_spend_invoice_lines_credit CHECK (line_kind <> 'credit' OR amount <= 0),
            CONSTRAINT ck_spend_invoice_lines_usage_type CHECK (usage_type IS NULL OR usage_type IN
                ('llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours')),
            CONSTRAINT ck_spend_invoice_lines_unit CHECK (unit IS NULL OR unit IN
                ('1m_input_tokens','1m_output_tokens','1m_cached_input_tokens','1m_tokens','1m_embedding_tokens',
                 'ocr_page','audio_minute','call','gb_month','gb_day','gpu_node_hour'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_invoice_lines_invoice_line "
        "ON spend_invoice_lines(tenant_id, invoice_id, line_no);"
    )
    _tenant_policy("spend_invoice_lines")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_reconciliations (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            period_start DATE NOT NULL,
            billing_timezone VARCHAR(64) NOT NULL,
            currency CHAR(3) NOT NULL,
            invoice_amount NUMERIC(24,10) NOT NULL,
            non_usage_amount NUMERIC(24,10) NOT NULL DEFAULT 0,
            stored_amount NUMERIC(24,10) NULL,
            repriced_amount NUMERIC(24,10) NULL,
            adjustments_amount NUMERIC(24,10) NOT NULL DEFAULT 0,
            stored_variance_amount NUMERIC(24,10) NULL,
            stored_variance_pct NUMERIC(14,6) NULL,
            repriced_variance_amount NUMERIC(24,10) NULL,
            repriced_variance_pct NUMERIC(14,6) NULL,
            tolerance_pct NUMERIC(5,2) NOT NULL DEFAULT 1.00,
            status VARCHAR(20) NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            needs_review_count INTEGER NOT NULL DEFAULT 0,
            unpriced_quantity_items INTEGER NOT NULL DEFAULT 0,
            unknown_account_records BIGINT NOT NULL DEFAULT 0,
            platform_billed_amount_inr NUMERIC(28,10) NOT NULL DEFAULT 0,
            invoice_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            card_ids UUID[] NOT NULL DEFAULT '{}',
            fx_rows JSONB NOT NULL DEFAULT '[]'::jsonb,
            retroactive JSONB NOT NULL DEFAULT '[]'::jsonb,
            superseded BOOLEAN NOT NULL DEFAULT false,
            accepted_by VARCHAR(128) NULL,
            accepted_at TIMESTAMPTZ NULL,
            accept_reason VARCHAR(500) NULL,
            run_by VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ux_spend_reconciliations_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT ck_spend_reconciliations_status CHECK (status IN ('within_tolerance','needs_review','accepted')),
            CONSTRAINT ck_spend_reconciliations_accepted CHECK ((accepted_at IS NULL) = (accept_reason IS NULL))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_reconciliations_tenant_period "
        "ON spend_reconciliations(tenant_id, period_start, provider, created_at);"
    )
    _tenant_policy("spend_reconciliations")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_reconciliation_items (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            reconciliation_id UUID NOT NULL,
            item_kind VARCHAR(16) NOT NULL DEFAULT 'usage',
            line_kind VARCHAR(16) NULL,
            usage_type VARCHAR(32) NULL,
            model_sku VARCHAR(128) NOT NULL DEFAULT '',
            unit VARCHAR(32) NULL,
            usage_date DATE NULL,
            invoice_quantity NUMERIC(24,6) NULL,
            metered_quantity NUMERIC(24,6) NOT NULL DEFAULT 0,
            invoice_amount NUMERIC(24,10) NOT NULL DEFAULT 0,
            stored_amount NUMERIC(24,10) NULL,
            repriced_amount NUMERIC(24,10) NULL,
            adjustments JSONB NOT NULL DEFAULT '[]'::jsonb,
            stored_variance_amount NUMERIC(24,10) NULL,
            stored_variance_pct NUMERIC(14,6) NULL,
            repriced_variance_amount NUMERIC(24,10) NULL,
            repriced_variance_pct NUMERIC(14,6) NULL,
            status VARCHAR(20) NOT NULL,
            fx_converted BOOLEAN NOT NULL DEFAULT false,
            unpriced_quantity NUMERIC(24,6) NOT NULL DEFAULT 0,
            unconverted_amount NUMERIC(24,10) NOT NULL DEFAULT 0,
            cards JSONB NOT NULL DEFAULT '[]'::jsonb,
            fx JSONB NOT NULL DEFAULT '[]'::jsonb,
            invoice_line_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            days JSONB NOT NULL DEFAULT '[]'::jsonb,
            accepted_by VARCHAR(128) NULL,
            accepted_at TIMESTAMPTZ NULL,
            accept_reason VARCHAR(500) NULL,
            carried_from UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_spend_reconciliation_items_run FOREIGN KEY (tenant_id, reconciliation_id)
                REFERENCES spend_reconciliations(tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_spend_reconciliation_items_kind CHECK (item_kind IN ('usage','non_usage_line')),
            CONSTRAINT ck_spend_reconciliation_items_status CHECK (
                status IN ('within_tolerance','needs_review','accepted','informational')
                AND ((item_kind = 'usage') = (status <> 'informational'))),
            CONSTRAINT ck_spend_reconciliation_items_accepted CHECK (
                (status = 'accepted') = (accepted_at IS NOT NULL AND accept_reason IS NOT NULL))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_reconciliation_items_reconciliation "
        "ON spend_reconciliation_items(tenant_id, reconciliation_id, status);"
    )
    _tenant_policy("spend_reconciliation_items")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
