-- migrations/005_rls.sql

-- Tenants: allow admin access only (no tenant_id self-reference needed)
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_self_access ON tenants USING (id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agents USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE connectors ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connectors USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE schema_registry ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON schema_registry USING (tenant_id IS NULL OR tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_definitions USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workflow_runs USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE step_executions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON step_executions USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE tool_calls ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tool_calls USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE hitl_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON hitl_queue USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON documents USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE agent_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_versions USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE agent_lifecycle_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_lifecycle_events USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE agent_teams ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_teams USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE agent_team_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_members ON agent_team_members USING (team_id IN (SELECT id FROM agent_teams WHERE tenant_id = current_setting('agentflow.tenant_id')::UUID));

ALTER TABLE agent_cost_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_cost_ledger USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);

ALTER TABLE shadow_comparisons ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shadow_comparisons USING (tenant_id = current_setting('agentflow.tenant_id')::UUID);
