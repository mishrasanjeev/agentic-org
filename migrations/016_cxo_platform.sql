-- migrations/016_cxo_platform.sql
-- v5.0.0: CxO Platform — KPI cache, agent results, connector configs,
--         workflow schedules, board reports, HITL requests.

-- ── 1. kpi_cache — real-time metric storage ────────────────────────────
CREATE TABLE IF NOT EXISTS kpi_cache (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    company_id      UUID REFERENCES companies(id),
    role            VARCHAR(20) NOT NULL,
    metric_name     VARCHAR(100) NOT NULL,
    metric_value    JSONB NOT NULL,
    source          VARCHAR(50) NOT NULL DEFAULT 'agent',
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_seconds     INT NOT NULL DEFAULT 3600,
    stale           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_kpi_cache_tenant_role ON kpi_cache(tenant_id, role);
CREATE INDEX IF NOT EXISTS ix_kpi_cache_metric ON kpi_cache(tenant_id, role, metric_name);
CREATE INDEX IF NOT EXISTS ix_kpi_cache_computed ON kpi_cache(computed_at);

ALTER TABLE kpi_cache ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='kpi_cache' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON kpi_cache USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 2. agent_task_results — execution history ──────────────────────────
CREATE TABLE IF NOT EXISTS agent_task_results (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    agent_id        UUID NOT NULL,
    agent_type      VARCHAR(100) NOT NULL,
    domain          VARCHAR(50) NOT NULL,
    task_type       VARCHAR(100) NOT NULL,
    task_input      JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_output     JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence      FLOAT,
    tool_calls      JSONB DEFAULT '[]'::jsonb,
    llm_model       VARCHAR(100),
    tokens_used     INT DEFAULT 0,
    cost_usd        FLOAT DEFAULT 0.0,
    duration_ms     INT DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'completed',
    error_message   TEXT,
    hitl_required   BOOLEAN NOT NULL DEFAULT FALSE,
    hitl_decision   VARCHAR(20),
    company_id      UUID REFERENCES companies(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_results_tenant ON agent_task_results(tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_results_agent ON agent_task_results(agent_id);
CREATE INDEX IF NOT EXISTS ix_agent_results_domain ON agent_task_results(tenant_id, domain);
CREATE INDEX IF NOT EXISTS ix_agent_results_created ON agent_task_results(created_at);
CREATE INDEX IF NOT EXISTS ix_agent_results_type ON agent_task_results(agent_type, task_type);

ALTER TABLE agent_task_results ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='agent_task_results' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON agent_task_results USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 3. connector_configs — per-tenant connector credentials ────────────
CREATE TABLE IF NOT EXISTS connector_configs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    connector_name  VARCHAR(100) NOT NULL,
    display_name    VARCHAR(255),
    auth_type       VARCHAR(50) NOT NULL DEFAULT 'api_key',
    credentials_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(20) NOT NULL DEFAULT 'configured',
    last_health_check TIMESTAMPTZ,
    health_status   VARCHAR(20) DEFAULT 'unknown',
    last_sync_at    TIMESTAMPTZ,
    sync_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    CONSTRAINT uq_connector_config_tenant UNIQUE (tenant_id, connector_name)
);

CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant ON connector_configs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_connector_configs_status ON connector_configs(tenant_id, status);

ALTER TABLE connector_configs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='connector_configs' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON connector_configs USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 4. workflow_schedules — cron-based workflow execution ───────────────
CREATE TABLE IF NOT EXISTS workflow_schedules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    workflow_id     UUID NOT NULL,
    workflow_name   VARCHAR(255) NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    timezone        VARCHAR(50) NOT NULL DEFAULT 'Asia/Kolkata',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    last_run_at     TIMESTAMPTZ,
    last_run_status VARCHAR(20),
    next_run_at     TIMESTAMPTZ,
    run_count       INT NOT NULL DEFAULT 0,
    failure_count   INT NOT NULL DEFAULT 0,
    company_id      UUID REFERENCES companies(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_workflow_schedules_tenant ON workflow_schedules(tenant_id);
CREATE INDEX IF NOT EXISTS ix_workflow_schedules_next ON workflow_schedules(next_run_at) WHERE enabled = TRUE;

ALTER TABLE workflow_schedules ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='workflow_schedules' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON workflow_schedules USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 5. board_reports — generated executive reports ─────────────────────
CREATE TABLE IF NOT EXISTS board_reports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    report_type     VARCHAR(50) NOT NULL,
    report_period   VARCHAR(20) NOT NULL,
    title           VARCHAR(500) NOT NULL,
    file_path       VARCHAR(1000),
    file_size_bytes BIGINT,
    format          VARCHAR(20) NOT NULL DEFAULT 'pdf',
    status          VARCHAR(20) NOT NULL DEFAULT 'generating',
    generated_by    VARCHAR(255),
    kpi_snapshot    JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_board_reports_tenant ON board_reports(tenant_id);
CREATE INDEX IF NOT EXISTS ix_board_reports_type ON board_reports(tenant_id, report_type);

ALTER TABLE board_reports ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='board_reports' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON board_reports USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;


-- ── 6. connector_health_log — health check history ─────────────────────
CREATE TABLE IF NOT EXISTS connector_health_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    connector_name  VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    response_ms     INT,
    error_message   TEXT,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_connector_health_tenant ON connector_health_log(tenant_id, connector_name);
CREATE INDEX IF NOT EXISTS ix_connector_health_time ON connector_health_log(checked_at);

ALTER TABLE connector_health_log ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='connector_health_log' AND policyname='tenant_isolation') THEN
        EXECUTE 'CREATE POLICY tenant_isolation ON connector_health_log USING (tenant_id = current_setting(''agenticorg.tenant_id'')::UUID)';
    END IF;
END $$;
