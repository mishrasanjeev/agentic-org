# SPDX-License-Identifier: Apache-2.0
"""Spend usage: usage records (partitioned by month), the daily rollup, meter gaps and maintenance jobs.

Revision ID: v6z80_spend_usage
Revises: v6z79_spend_reference
Create Date: 2026-10-10

``spend_usage_records``: one record per billable quantity in one unit, with
its price in the card's currency and in INR and every attribution dimension
(``core/spend/``). It is range-partitioned by ``event_time``, one partition
per month from July 2026 to December 2028 plus a default partition, so rows
past the horizon are kept, never lost; a later migration adds months.
``spend_usage_rollups``: sums per reporting day and dimension combination,
rebuildable from the records. ``spend_meter_gaps``: counts of usage that
could not be metered, per day, usage type and reason. ``spend_jobs``: the
maintenance jobs (rebuild, backfill, restatement, FX settlement,
re-attribution, commitment recompute); at most one running job of a kind per
tenant, with a heartbeat so a lost worker's job can be taken over. Every table, and every partition, is tenant scoped under
forced row-level policies. The record's foreign keys are composite on
``(tenant_id, id)`` and carry leading indexes. No existing table is altered.
"""

from alembic import op

revision = "v6z80_spend_usage"
down_revision = "v6z79_spend_reference"
branch_labels = None
depends_on = None

TABLES = ("spend_usage_records", "spend_usage_rollups", "spend_meter_gaps", "spend_jobs")

# July 2026 to December 2028 (30 months); core/spend/partitions.py keeps the same list.
FIRST_MONTH = (2026, 7)
LAST_MONTH = (2028, 12)
DEFAULT_PARTITION = "spend_usage_records_default"


def _months() -> list[tuple[int, int]]:
    year, month = FIRST_MONTH
    out = []
    while (year, month) <= LAST_MONTH:
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _partition_name(year: int, month: int) -> str:
    return f"spend_usage_records_y{year:04d}m{month:02d}"


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
        CREATE TABLE IF NOT EXISTS spend_usage_records (
            id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            idempotency_key VARCHAR(160) NOT NULL,
            source_ref VARCHAR(64) NOT NULL DEFAULT '',
            correlation_ref CHAR(32) NOT NULL DEFAULT '',
            event_time TIMESTAMPTZ NOT NULL,
            event_date DATE NOT NULL,
            billing_date DATE NOT NULL,
            usage_type VARCHAR(32) NOT NULL,
            unit VARCHAR(32) NOT NULL,
            quantity NUMERIC(24,6) NOT NULL,
            calls SMALLINT NOT NULL DEFAULT 0,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL DEFAULT '',
            rate_card_id UUID NULL,
            blend_card_id UUID NULL,
            price_source VARCHAR(24) NOT NULL,
            unit_price NUMERIC(20,10) NULL,
            amount NUMERIC(24,10) NULL,
            currency CHAR(3) NULL,
            fx_rate NUMERIC(20,8) NULL,
            fx_rate_date DATE NULL,
            amount_inr NUMERIC(24,10) NULL,
            unpriced BOOLEAN NOT NULL DEFAULT false,
            fx_estimated BOOLEAN NOT NULL DEFAULT false,
            unconverted BOOLEAN NOT NULL DEFAULT false,
            overage BOOLEAN NOT NULL DEFAULT false,
            overage_quantity NUMERIC(24,6) NOT NULL DEFAULT 0,
            allocated BOOLEAN NOT NULL DEFAULT false,
            quantity_estimated BOOLEAN NOT NULL DEFAULT false,
            price_estimated BOOLEAN NOT NULL DEFAULT false,
            commitment_id UUID NULL,
            agent_id UUID NULL,
            agent_version VARCHAR(20) NULL,
            org_node_id UUID NULL,
            business_unit_node_id UUID NULL,
            attribution_path VARCHAR(24) NULL,
            unattributed_reason VARCHAR(24) NULL,
            product_line VARCHAR(64) NULL,
            use_case VARCHAR(64) NOT NULL DEFAULT '',
            application VARCHAR(16) NOT NULL,
            region VARCHAR(8) NULL,
            workflow_id UUID NULL,
            run_id VARCHAR(64) NULL,
            initiating_user_id UUID NULL,
            environment VARCHAR(32) NOT NULL DEFAULT '',
            risk_tier VARCHAR(16) NULL,
            billing_account VARCHAR(16) NULL,
            allocated_from VARCHAR(64) NULL,
            revised_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_spend_usage_records PRIMARY KEY (id, event_time),
            CONSTRAINT fk_spend_usage_records_rate_card FOREIGN KEY (tenant_id, rate_card_id)
                REFERENCES spend_rate_cards(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_spend_usage_records_org_node FOREIGN KEY (tenant_id, org_node_id)
                REFERENCES spend_org_nodes(tenant_id, id) ON DELETE RESTRICT,
            CONSTRAINT ck_spend_usage_records_usage_type CHECK (usage_type IN
                ('llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours')),
            CONSTRAINT ck_spend_usage_records_unit CHECK (
                (usage_type = 'llm_tokens' AND unit IN ('input_token','output_token','cached_input_token','token'))
                OR (usage_type = 'embedding_tokens' AND unit = 'embedding_token')
                OR (usage_type = 'ocr_pages' AND unit = 'ocr_page')
                OR (usage_type = 'speech_minutes' AND unit = 'audio_minute')
                OR (usage_type = 'tool_calls' AND unit = 'call')
                OR (usage_type = 'storage' AND unit = 'gb_day')
                OR (usage_type = 'gpu_hours' AND unit = 'gpu_node_hour')),
            CONSTRAINT ck_spend_usage_records_quantity
                CHECK (quantity >= 0 AND overage_quantity >= 0 AND overage_quantity <= quantity),
            CONSTRAINT ck_spend_usage_records_calls CHECK (calls IN (0, 1)),
            CONSTRAINT ck_spend_usage_records_price_source CHECK (price_source IN
                ('contract','list','fallback_override','fallback_list','in_house','none')),
            CONSTRAINT ck_spend_usage_records_priced CHECK (
                (unpriced AND amount IS NULL AND currency IS NULL AND amount_inr IS NULL AND NOT unconverted
                    AND price_source = 'none')
                OR (NOT unpriced AND amount IS NOT NULL AND currency IS NOT NULL AND price_source <> 'none'
                    AND ((unconverted AND amount_inr IS NULL) OR (NOT unconverted AND amount_inr IS NOT NULL)))),
            CONSTRAINT ck_spend_usage_records_application CHECK (application IN
                ('agents','chat','voice','workflows','a2a','mcp','api','console','knowledge','documents',
                 'speech','content','txn','system')),
            CONSTRAINT ck_spend_usage_records_attribution CHECK ((org_node_id IS NULL) = (unattributed_reason IS NOT NULL)),
            CONSTRAINT ck_spend_usage_records_attribution_path CHECK ((org_node_id IS NULL) = (attribution_path IS NULL)
                AND (attribution_path IS NULL OR attribution_path IN ('agent_mapping','cost_centre_mapping',
                'cost_centre_code','workflow_mapping','application_mapping','department_mapping','department_code'))),
            CONSTRAINT ck_spend_usage_records_unattributed_reason CHECK (unattributed_reason IS NULL OR
                unattributed_reason IN ('no_source','no_mapping','unknown_label','inactive_node','resolver_failed')),
            CONSTRAINT ck_spend_usage_records_risk_tier
                CHECK (risk_tier IS NULL OR risk_tier IN ('low','medium','high','critical')),
            CONSTRAINT ck_spend_usage_records_billing_account CHECK (billing_account IS NULL OR
                billing_account IN ('tenant_key','platform_key','in_house'))
        ) PARTITION BY RANGE (event_time);
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_usage_records_tenant_key "
        "ON spend_usage_records(tenant_id, idempotency_key, event_time);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_usage_records_tenant_time ON spend_usage_records(tenant_id, event_time);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_usage_records_rate_card "
        "ON spend_usage_records(tenant_id, rate_card_id, event_time) WHERE rate_card_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_usage_records_org_node "
        "ON spend_usage_records(tenant_id, org_node_id) WHERE org_node_id IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_usage_records_fx_pending "
        "ON spend_usage_records(tenant_id, event_time) WHERE fx_estimated OR unconverted;"
    )
    partitions = []
    for year, month in _months():
        following = (year + 1, 1) if month == 12 else (year, month + 1)
        name = _partition_name(year, month)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF spend_usage_records "
            f"FOR VALUES FROM ('{year:04d}-{month:02d}-01 00:00:00+00') "
            f"TO ('{following[0]:04d}-{following[1]:02d}-01 00:00:00+00');"
        )
        partitions.append(name)
    op.execute(f"CREATE TABLE IF NOT EXISTS {DEFAULT_PARTITION} PARTITION OF spend_usage_records DEFAULT;")
    partitions.append(DEFAULT_PARTITION)
    _tenant_policy("spend_usage_records")
    for name in partitions:
        _tenant_policy(name)

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_usage_rollups (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            day DATE NOT NULL,
            dims_hash CHAR(64) NOT NULL,
            billing_date DATE NOT NULL,
            org_node_id UUID NULL,
            business_unit_node_id UUID NULL,
            attribution_path VARCHAR(24) NULL,
            unattributed_reason VARCHAR(24) NULL,
            product_line VARCHAR(64) NULL,
            use_case VARCHAR(64) NOT NULL DEFAULT '',
            application VARCHAR(16) NOT NULL,
            agent_id UUID NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL DEFAULT '',
            usage_type VARCHAR(32) NOT NULL,
            unit VARCHAR(32) NOT NULL,
            currency CHAR(3) NULL,
            rate_card_id UUID NULL,
            price_source VARCHAR(24) NOT NULL,
            commitment_id UUID NULL,
            billing_account VARCHAR(16) NULL,
            region VARCHAR(8) NULL,
            environment VARCHAR(32) NOT NULL DEFAULT '',
            risk_tier VARCHAR(16) NULL,
            quantity NUMERIC(28,6) NOT NULL DEFAULT 0,
            amount NUMERIC(28,10) NOT NULL DEFAULT 0,
            amount_inr NUMERIC(28,10) NOT NULL DEFAULT 0,
            unconverted_amount NUMERIC(28,10) NOT NULL DEFAULT 0,
            unpriced_quantity NUMERIC(28,6) NOT NULL DEFAULT 0,
            overage_quantity NUMERIC(28,6) NOT NULL DEFAULT 0,
            record_count BIGINT NOT NULL DEFAULT 0,
            call_count BIGINT NOT NULL DEFAULT 0,
            unpriced_count BIGINT NOT NULL DEFAULT 0,
            unconverted_count BIGINT NOT NULL DEFAULT 0,
            fx_estimated_count BIGINT NOT NULL DEFAULT 0,
            overage_count BIGINT NOT NULL DEFAULT 0,
            allocated_count BIGINT NOT NULL DEFAULT 0,
            estimated_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_usage_rollups_counts CHECK (record_count >= 0 AND call_count >= 0
                AND unpriced_count >= 0 AND unconverted_count >= 0 AND fx_estimated_count >= 0
                AND overage_count >= 0 AND allocated_count >= 0 AND estimated_count >= 0)
        ) WITH (fillfactor = 70);
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_usage_rollups_tenant_day_dims "
        "ON spend_usage_rollups(tenant_id, day, dims_hash);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_usage_rollups_tenant_provider_billing "
        "ON spend_usage_rollups(tenant_id, provider, billing_date);"
    )
    _tenant_policy("spend_usage_rollups")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_meter_gaps (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            day DATE NOT NULL,
            usage_type VARCHAR(32) NOT NULL,
            reason VARCHAR(24) NOT NULL,
            detail VARCHAR(160) NOT NULL DEFAULT '',
            count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_spend_meter_gaps_usage_type CHECK (usage_type IN
                ('llm_tokens','embedding_tokens','ocr_pages','speech_minutes','tool_calls','storage','gpu_hours')),
            CONSTRAINT ck_spend_meter_gaps_reason CHECK (reason IN ('queue_full','spill_failed','shutdown_lost',
                'paused','tenant_mismatch','failed_no_usage','timeout_estimated','unpriced_tool')),
            CONSTRAINT ck_spend_meter_gaps_count CHECK (count >= 0)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_meter_gaps_key "
        "ON spend_meter_gaps(tenant_id, day, usage_type, reason, detail);"
    )
    _tenant_policy("spend_meter_gaps")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS spend_jobs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            kind VARCHAR(32) NOT NULL,
            params JSONB NOT NULL DEFAULT '{}'::jsonb,
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_code VARCHAR(64) NOT NULL DEFAULT '',
            requested_by VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ NULL,
            heartbeat_at TIMESTAMPTZ NULL,
            finished_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_spend_jobs_kind CHECK (kind IN
                ('rebuild','backfill','restate','settle_fx','reattribute','recompute_commitments')),
            CONSTRAINT ck_spend_jobs_status CHECK (status IN ('queued','running','succeeded','failed'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_jobs_running "
        "ON spend_jobs(tenant_id, kind) WHERE status = 'running';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_spend_jobs_open "
        "ON spend_jobs(tenant_id, kind, created_at) WHERE status IN ('queued','running');"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_spend_jobs_tenant_created ON spend_jobs(tenant_id, created_at);")
    _tenant_policy("spend_jobs")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS spend_jobs;")
    op.execute("DROP TABLE IF EXISTS spend_meter_gaps;")
    op.execute("DROP TABLE IF EXISTS spend_usage_rollups;")
    op.execute("DROP TABLE IF EXISTS spend_usage_records;")
