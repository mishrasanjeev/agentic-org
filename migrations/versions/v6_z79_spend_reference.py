# SPDX-License-Identifier: Apache-2.0
"""Spend reference data: organisation tree, source mappings, rate cards, model aliases, commitments, FX rates.

Revision ID: v6z79_spend_reference
Revises: v6z78_security_release_merge
Create Date: 2026-10-10

``spend_org_nodes``: the organisation tree spend rolls up through (group,
business unit, department, team, cost centre), never deleted once used.
``spend_source_mappings``: an agent, application, workflow or legacy
cost-centre / department label mapped to a node, a product line and a use
case. ``spend_rate_cards``: effective-dated prices per provider, usage
type, model (or provider default), unit and source; a correction retires a
card and inserts its replacement. ``spend_model_aliases``: a model name as
called mapped to the SKU cards and invoices use. ``spend_commitments``:
committed or prepaid volume over a billing period. ``spend_fx_rates``: the
reference rate of a currency to INR per reporting date
(``core/spend/``). All tenant scoped under forced row-level policies.
Spend-to-spend foreign keys are composite on ``(tenant_id, id)`` so a row
can never reference another tenant's row; every foreign key carries a
leading index. No existing table is altered.
"""

from alembic import op

revision = "v6z79_spend_reference"
down_revision = "v6z78_security_release_merge"
branch_labels = None
depends_on = None

TABLES = (
    "spend_org_nodes",
    "spend_source_mappings",
    "spend_rate_cards",
    "spend_model_aliases",
    "spend_commitments",
    "spend_fx_rates",
)


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
        CREATE TABLE IF NOT EXISTS spend_org_nodes (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            code VARCHAR(64) NOT NULL,
            name VARCHAR(200) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            parent_id UUID NULL,
            owner_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            active BOOLEAN NOT NULL DEFAULT true,
            deactivated_at TIMESTAMPTZ NULL,
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ux_spend_org_nodes_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_spend_org_nodes_parent FOREIGN KEY (tenant_id, parent_id)
                REFERENCES spend_org_nodes(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_spend_org_nodes_kind
                CHECK (kind IN ('group','business_unit','department','team','cost_centre')),
            CONSTRAINT ck_spend_org_nodes_parent CHECK (parent_id IS NULL OR parent_id <> id),
            CONSTRAINT ck_spend_org_nodes_code CHECK (code <> '')
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_org_nodes_tenant_code ON spend_org_nodes(tenant_id, code);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_org_nodes_parent ON spend_org_nodes(tenant_id, parent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_org_nodes_owner ON spend_org_nodes(owner_user_id);")
    _tenant_policy("spend_org_nodes")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_source_mappings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            source_type VARCHAR(16) NOT NULL,
            source_ref VARCHAR(128) NOT NULL,
            org_node_id UUID NULL,
            product_line VARCHAR(64) NULL,
            use_case VARCHAR(64) NULL,
            active BOOLEAN NOT NULL DEFAULT true,
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_spend_source_mappings_org_node FOREIGN KEY (tenant_id, org_node_id)
                REFERENCES spend_org_nodes(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_spend_source_mappings_source_type
                CHECK (source_type IN ('agent','application','workflow','cost_center','department')),
            CONSTRAINT ck_spend_source_mappings_target
                CHECK (org_node_id IS NOT NULL OR product_line IS NOT NULL OR use_case IS NOT NULL)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_source_mappings_tenant_source "
        "ON spend_source_mappings(tenant_id, source_type, source_ref);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_source_mappings_org_node ON spend_source_mappings(tenant_id, org_node_id);"
    )
    _tenant_policy("spend_source_mappings")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_rate_cards (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            usage_type VARCHAR(32) NOT NULL,
            model_sku VARCHAR(128) NOT NULL DEFAULT '',
            unit VARCHAR(32) NOT NULL,
            unit_price NUMERIC(20,10) NOT NULL,
            currency CHAR(3) NOT NULL,
            cached_unit_price NUMERIC(20,10) NULL,
            batch_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
            volume_tiers JSONB NOT NULL DEFAULT '[]'::jsonb,
            tier_mode VARCHAR(16) NOT NULL DEFAULT 'graduated',
            effective_from DATE NOT NULL,
            effective_to DATE NULL,
            source VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            replaces_id UUID NULL,
            retired_at TIMESTAMPTZ NULL,
            reference VARCHAR(200) NOT NULL DEFAULT '',
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ux_spend_rate_cards_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT fk_spend_rate_cards_replaces FOREIGN KEY (tenant_id, replaces_id)
                REFERENCES spend_rate_cards(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_spend_rate_cards_usage_type CHECK (usage_type IN
                ('llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours')),
            CONSTRAINT ck_spend_rate_cards_unit CHECK (
                (usage_type = 'llm_tokens' AND unit IN
                    ('1m_input_tokens','1m_output_tokens','1m_cached_input_tokens','1m_tokens'))
                OR (usage_type = 'embedding_tokens' AND unit = '1m_embedding_tokens')
                OR (usage_type = 'ocr_pages' AND unit = 'ocr_page')
                OR (usage_type = 'speech_minutes' AND unit = 'audio_minute')
                OR (usage_type = 'tool_calls' AND unit = 'call')
                OR (usage_type = 'storage' AND unit IN ('gb_month','gb_day'))
                OR (usage_type = 'gpu_hours' AND unit = 'gpu_node_hour')),
            CONSTRAINT ck_spend_rate_cards_price
                CHECK (unit_price >= 0 AND (cached_unit_price IS NULL OR cached_unit_price >= 0)),
            CONSTRAINT ck_spend_rate_cards_cached CHECK (cached_unit_price IS NULL OR unit = '1m_input_tokens'),
            CONSTRAINT ck_spend_rate_cards_currency CHECK (currency ~ '^[A-Z]{3}$'),
            CONSTRAINT ck_spend_rate_cards_batch CHECK (batch_discount_pct >= 0 AND batch_discount_pct <= 100),
            CONSTRAINT ck_spend_rate_cards_tier_mode CHECK (tier_mode IN ('graduated','all_units')),
            CONSTRAINT ck_spend_rate_cards_dates CHECK (effective_to IS NULL OR effective_to > effective_from),
            CONSTRAINT ck_spend_rate_cards_source CHECK (source IN ('list','contract')),
            CONSTRAINT ck_spend_rate_cards_status CHECK (status IN ('active','retired')),
            CONSTRAINT ck_spend_rate_cards_retired CHECK ((status = 'retired') = (retired_at IS NOT NULL))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_rate_cards_key_from "
        "ON spend_rate_cards(tenant_id, provider, usage_type, model_sku, unit, source, effective_from) "
        "WHERE status = 'active';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_rate_cards_lookup "
        "ON spend_rate_cards(tenant_id, provider, usage_type, status, effective_from);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_rate_cards_replaces ON spend_rate_cards(tenant_id, replaces_id);")
    _tenant_policy("spend_rate_cards")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_model_aliases (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            alias VARCHAR(128) NOT NULL,
            model_sku VARCHAR(128) NOT NULL,
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_model_aliases_distinct CHECK (alias <> model_sku AND alias <> '' AND model_sku <> '')
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_model_aliases_tenant_alias "
        "ON spend_model_aliases(tenant_id, provider, alias);"
    )
    _tenant_policy("spend_model_aliases")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_commitments (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            usage_type VARCHAR(32) NULL,
            model_sku VARCHAR(128) NOT NULL DEFAULT '',
            unit VARCHAR(32) NULL,
            kind VARCHAR(16) NOT NULL,
            committed_quantity NUMERIC(24,6) NULL,
            committed_amount NUMERIC(24,10) NULL,
            currency CHAR(3) NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            overage_unit_price NUMERIC(20,10) NULL,
            overage_currency CHAR(3) NULL,
            drawn_quantity NUMERIC(28,6) NOT NULL DEFAULT 0,
            drawn_amount NUMERIC(28,10) NOT NULL DEFAULT 0,
            undrawn_records BIGINT NOT NULL DEFAULT 0,
            recomputed_through TIMESTAMPTZ NULL,
            recomputed_at TIMESTAMPTZ NULL,
            needs_full_recompute BOOLEAN NOT NULL DEFAULT true,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            reference VARCHAR(200) NOT NULL DEFAULT '',
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_commitments_kind CHECK (kind IN ('quantity','money')),
            CONSTRAINT ck_spend_commitments_shape CHECK (
                (kind = 'quantity' AND committed_quantity > 0 AND usage_type IS NOT NULL AND unit IS NOT NULL
                    AND unit <> 'gb_month')
                OR (kind = 'money' AND committed_amount > 0 AND currency IS NOT NULL)),
            CONSTRAINT ck_spend_commitments_usage_type CHECK (usage_type IS NULL OR usage_type IN
                ('llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours')),
            CONSTRAINT ck_spend_commitments_overage CHECK ((overage_unit_price IS NULL) = (overage_currency IS NULL)
                AND (overage_unit_price IS NULL OR kind = 'quantity')),
            CONSTRAINT ck_spend_commitments_period CHECK (period_end > period_start),
            CONSTRAINT ck_spend_commitments_drawn
                CHECK (drawn_quantity >= 0 AND drawn_amount >= 0 AND undrawn_records >= 0),
            CONSTRAINT ck_spend_commitments_status CHECK (status IN ('active','closed'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_commitments_lookup "
        "ON spend_commitments(tenant_id, provider, status, period_start);"
    )
    _tenant_policy("spend_commitments")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_fx_rates (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            rate_date DATE NOT NULL,
            currency CHAR(3) NOT NULL,
            rate_to_inr NUMERIC(20,8) NOT NULL,
            source VARCHAR(16) NOT NULL DEFAULT 'manual',
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_fx_rates_currency CHECK (currency ~ '^[A-Z]{3}$' AND currency <> 'INR'),
            CONSTRAINT ck_spend_fx_rates_rate CHECK (rate_to_inr > 0),
            CONSTRAINT ck_spend_fx_rates_source CHECK (source IN ('reference','manual','import'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_fx_rates_tenant_currency_date "
        "ON spend_fx_rates(tenant_id, currency, rate_date);"
    )
    _tenant_policy("spend_fx_rates")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS spend_fx_rates;")
    op.execute("DROP TABLE IF EXISTS spend_commitments;")
    op.execute("DROP TABLE IF EXISTS spend_model_aliases;")
    op.execute("DROP TABLE IF EXISTS spend_rate_cards;")
    op.execute("DROP TABLE IF EXISTS spend_source_mappings;")
    op.execute("DROP TABLE IF EXISTS spend_org_nodes;")
