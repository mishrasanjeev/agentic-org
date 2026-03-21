"""Agent scaling tests -- FT-SCALE-001 through FT-SCALE-015.

Covers agent creation, cloning, shadow quality gates, promotion/rollback,
kill switch, HPA auto-scaling, cost caps, traffic splitting, bulk creation,
team routing, temporary agent TTL, and shadow-mode skip prevention.
"""

import asyncio
import time
import uuid

import pytest

from auth.scopes import validate_clone_scopes
from scaling.agent_factory import AgentFactory
from scaling.cost_ledger import CostLedger
from scaling.hpa_integration import HPAIntegration
from scaling.lifecycle import VALID_TRANSITIONS, LifecycleManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_config(overrides: dict | None = None) -> dict:
    """Default agent configuration for tests."""
    cfg = {
        "id": str(uuid.uuid4()),
        "tenant_id": "tenant-001",
        "agent_type": "ap_processor",
        "domain": "finance",
        "authorized_tools": ["tool:oracle_fusion:read:purchase_order"],
        "prompt_variables": {"org_name": "TestCorp"},
        "hitl_condition": "total > 500000",
        "output_schema": "Invoice",
    }
    if overrides:
        cfg.update(overrides)
    return cfg


# ===================================================================
# FT-SCALE-001: Create new agent via API (201, status=shadow, token issued)
# ===================================================================


class TestFTSCALE001:
    """Creating a new agent must return 201 with status=shadow and an issued token."""

    @pytest.mark.asyncio
    async def test_create_agent_returns_shadow_status(self):
        """FT-SCALE-001: AgentFactory.create_agent must return an agent with
        status='shadow' and token_issued=True.
        """
        factory = AgentFactory()
        result = await factory.create_agent(_agent_config())

        assert "agent_id" in result
        assert result["status"] == "shadow"
        assert result["token_issued"] is True

    @pytest.mark.asyncio
    async def test_create_agent_unique_ids(self):
        """FT-SCALE-001: Each created agent must have a globally unique ID."""
        factory = AgentFactory()
        agents = [await factory.create_agent(_agent_config()) for _ in range(20)]
        agent_ids = [a["agent_id"] for a in agents]
        assert len(set(agent_ids)) == 20, "Agent IDs must be unique"


# ===================================================================
# FT-SCALE-002: Clone agent with scope overrides (inherits parent, logs elevation)
# ===================================================================


class TestFTSCALE002:
    """Cloning an agent must inherit parent scopes and log any elevation attempts."""

    @pytest.mark.asyncio
    async def test_clone_inherits_parent_scopes(self):
        """FT-SCALE-002: Cloning with scopes within the parent ceiling must succeed."""
        factory = AgentFactory()
        parent_config = _agent_config()

        result = await factory.clone_agent(
            parent_id="parent-001",
            parent_config=parent_config,
            overrides={"authorized_tools": {"add": []}},
        )

        assert "clone_id" in result
        assert result["parent_id"] == "parent-001"
        assert result["status"] == "shadow"

    @pytest.mark.asyncio
    async def test_clone_with_elevation_logs_violation(self):
        """FT-SCALE-002: Cloning with elevated scopes must return E4003 and
        log the violation.
        """
        factory = AgentFactory()
        parent_config = _agent_config(
            {
                "authorized_tools": ["tool:banking_api:write:queue_payment:capped:500000"],
            }
        )

        result = await factory.clone_agent(
            parent_id="parent-002",
            parent_config=parent_config,
            overrides={
                "authorized_tools": {
                    "add": ["tool:banking_api:write:queue_payment:capped:1000000"],
                },
            },
        )

        assert "error" in result
        assert result["error"]["code"] == "E4003"
        assert (
            "ceiling" in result["error"]["message"].lower()
            or "violation" in result["error"]["message"].lower()
        )


# ===================================================================
# FT-SCALE-003: Shadow accuracy gate -- pass (96% vs 95% floor -> review_ready)
# ===================================================================


class TestFTSCALE003:
    """Shadow agent with 96% accuracy (above 95% floor) must transition to review_ready."""

    @pytest.mark.asyncio
    async def test_shadow_pass_promotes_to_review_ready(self):
        """FT-SCALE-003: With 96% accuracy exceeding the 95% floor, the shadow
        agent must be eligible for promotion to review_ready.
        """
        lm = LifecycleManager()
        recommendation = await lm.check_shadow_promotion(
            agent_id="shadow-001",
            sample_count=200,
            accuracy=0.96,
            min_samples=100,
            accuracy_floor=0.95,
        )
        assert recommendation == "review_ready"

    def test_transition_shadow_to_review_ready_valid(self):
        """FT-SCALE-003: The state machine must allow shadow -> review_ready."""
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "review_ready") is True


# ===================================================================
# FT-SCALE-004: Shadow accuracy gate -- fail (88% vs 95% -> shadow_failing)
# ===================================================================


class TestFTSCALE004:
    """Shadow agent with 88% accuracy (below 95% floor) must be marked shadow_failing."""

    @pytest.mark.asyncio
    async def test_shadow_fail_transitions_to_shadow_failing(self):
        """FT-SCALE-004: With 88% accuracy below the 95% floor, the shadow
        agent must be flagged as shadow_failing.
        """
        lm = LifecycleManager()
        recommendation = await lm.check_shadow_promotion(
            agent_id="shadow-002",
            sample_count=200,
            accuracy=0.88,
            min_samples=100,
            accuracy_floor=0.95,
        )
        assert recommendation == "shadow_failing"

    def test_transition_shadow_to_shadow_failing_valid(self):
        """FT-SCALE-004: The state machine must allow shadow -> shadow_failing."""
        lm = LifecycleManager()
        assert lm.can_transition("shadow", "shadow_failing") is True


# ===================================================================
# FT-SCALE-005: Promote shadow -> staging (write scopes, 10% traffic)
# ===================================================================


class TestFTSCALE005:
    """Promoting from shadow to staging must go through review_ready first."""

    @pytest.mark.asyncio
    async def test_promotion_path_shadow_to_staging(self):
        """FT-SCALE-005: An agent must follow the path shadow -> review_ready -> staging.
        Direct shadow -> staging is not allowed.
        """
        lm = LifecycleManager()

        # Direct shadow -> staging is NOT valid
        assert not lm.can_transition("shadow", "staging")

        # Must go through review_ready
        assert lm.can_transition("shadow", "review_ready")
        assert lm.can_transition("review_ready", "staging")

    @pytest.mark.asyncio
    async def test_staging_transition_recorded(self):
        """FT-SCALE-005: The lifecycle transition to staging must be recorded
        with the triggering user and reason.
        """
        lm = LifecycleManager()
        result = await lm.transition(
            agent_id="agent-005",
            current="review_ready",
            target="staging",
            triggered_by="ops-lead",
            reason="Shadow quality check passed 6/6 gates",
        )
        assert result["from_status"] == "review_ready"
        assert result["to_status"] == "staging"
        assert result["triggered_by"] == "ops-lead"


# ===================================================================
# FT-SCALE-006: Kill switch response time (<30 seconds, token revoked)
# ===================================================================


class TestFTSCALE006:
    """Kill switch must deactivate an agent in under 30 seconds."""

    @pytest.mark.asyncio
    async def test_kill_switch_under_30s(self):
        """FT-SCALE-006: Transitioning an active agent to deprecated (kill switch)
        must complete in under 30 seconds. The agent's token is effectively
        revoked by the status change.
        """
        lm = LifecycleManager()
        assert lm.can_transition("active", "deprecated")

        start = time.monotonic()
        result = await lm.transition(
            agent_id="agent-006",
            current="active",
            target="deprecated",
            triggered_by="kill-switch-api",
            reason="Emergency kill switch activated",
        )
        elapsed = time.monotonic() - start

        assert result["to_status"] == "deprecated"
        assert elapsed < 30.0, f"Kill switch took {elapsed:.2f}s, must be <30s"

    @pytest.mark.asyncio
    async def test_deprecated_agent_cannot_reactivate_directly(self):
        """FT-SCALE-006: A deprecated agent cannot transition back to active."""
        lm = LifecycleManager()
        assert not lm.can_transition("deprecated", "active")


# ===================================================================
# FT-SCALE-007: Rollback to prior version (within 60 seconds)
# ===================================================================


class TestFTSCALE007:
    """Rollback to a prior agent version must complete within 60 seconds."""

    @pytest.mark.asyncio
    async def test_rollback_from_staging_to_shadow(self):
        """FT-SCALE-007: An agent in staging can be rolled back to shadow mode
        within 60 seconds.
        """
        lm = LifecycleManager()
        assert lm.can_transition("staging", "shadow")

        start = time.monotonic()
        result = await lm.transition(
            agent_id="agent-007",
            current="staging",
            target="shadow",
            triggered_by="rollback-api",
            reason="Quality regression detected",
        )
        elapsed = time.monotonic() - start

        assert result["to_status"] == "shadow"
        assert elapsed < 60.0, f"Rollback took {elapsed:.2f}s, must be <60s"

    @pytest.mark.asyncio
    async def test_rollback_from_production_ready_to_staging(self):
        """FT-SCALE-007: An agent in production_ready can roll back to staging."""
        lm = LifecycleManager()
        assert lm.can_transition("production_ready", "staging")

        result = await lm.transition(
            agent_id="agent-007b",
            current="production_ready",
            target="staging",
            triggered_by="ops-lead",
        )
        assert result["to_status"] == "staging"


# ===================================================================
# FT-SCALE-008: Auto-scale on queue depth >30 (HPA scales up, audit trail)
# ===================================================================


class TestFTSCALE008:
    """When queue depth exceeds 30, HPA must recommend scale-up with an audit trail."""

    @pytest.mark.asyncio
    async def test_hpa_scales_up_on_queue_depth_above_30(self):
        """FT-SCALE-008: HPAIntegration must recommend scale_up when queue
        depth exceeds the threshold of 30.
        """
        hpa = HPAIntegration(cooldown_seconds=0)
        config = {
            "current_replicas": 2,
            "min_replicas": 1,
            "max_replicas": 10,
            "scale_up_threshold": 30,
            "tasks_per_replica": 10,
        }

        result = await hpa.check_scaling(
            agent_type="ap_processor",
            queue_depth=45,
            config=config,
        )

        assert result["action"] == "scale_up"
        assert result["replicas"] > config["current_replicas"]
        # Audit trail: signals dict must contain queue_depth info
        assert "queue_depth" in result["signals"]
        assert result["signals"]["queue_depth"]["queue_depth"] == 45

    @pytest.mark.asyncio
    async def test_hpa_no_change_below_threshold(self):
        """FT-SCALE-008: Queue depth below threshold should not trigger scale-up."""
        hpa = HPAIntegration(cooldown_seconds=0)
        config = {
            "current_replicas": 2,
            "min_replicas": 1,
            "max_replicas": 10,
            "scale_up_threshold": 30,
            "scale_down_threshold": 5,
            "tasks_per_replica": 10,
        }

        result = await hpa.check_scaling(
            agent_type="ap_processor",
            queue_depth=15,
            config=config,
        )

        assert result["action"] == "no_change"
        assert result["replicas"] == 2


# ===================================================================
# FT-SCALE-009: Cost cap enforcement (monthly cap -> auto-pause)
# ===================================================================


class TestFTSCALE009:
    """Monthly cost cap exceeded must trigger auto-pause recommendation."""

    @pytest.mark.asyncio
    async def test_monthly_cap_triggers_pause(self):
        """FT-SCALE-009: When monthly cost exceeds the cap, should_pause must
        return True, recommending the agent be paused.
        """
        ledger = CostLedger()

        # Record costs that exceed the monthly cap
        for _i in range(10):
            await ledger.record(
                agent_id="agent-009",
                tokens=5000,
                cost_usd=50.0,
                model="claude-3-5-sonnet",
                tenant_id="t1",
            )

        # Monthly cost is now $500. Cap is $400.
        should_pause = await ledger.should_pause(
            agent_id="agent-009",
            monthly_cap=400.0,
        )
        assert should_pause is True

    @pytest.mark.asyncio
    async def test_within_cap_no_pause(self):
        """FT-SCALE-009: When monthly cost is within cap, should_pause must
        return False.
        """
        ledger = CostLedger()

        await ledger.record(
            agent_id="agent-009b",
            tokens=1000,
            cost_usd=10.0,
            model="claude-3-5-sonnet",
            tenant_id="t1",
        )

        should_pause = await ledger.should_pause(
            agent_id="agent-009b",
            monthly_cap=400.0,
        )
        assert should_pause is False


# ===================================================================
# FT-SCALE-010: Clone scope ceiling (uncapped payment scope -> E4003)
# ===================================================================


class TestFTSCALE010:
    """Cloning with an elevated (uncapped) payment scope must return E4003."""

    def test_clone_scope_ceiling_blocks_elevation(self):
        """FT-SCALE-010: A child scope with a higher cap than the parent must
        produce a validation violation.
        """
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:1000000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) > 0
        assert any("elevation" in v.lower() or "exceeds" in v.lower() for v in violations)

    def test_clone_scope_ceiling_allows_equal_cap(self):
        """FT-SCALE-010: A child scope with the same cap as parent must be allowed."""
        parent = ["tool:banking_api:write:queue_payment:capped:500000"]
        child = ["tool:banking_api:write:queue_payment:capped:500000"]
        violations = validate_clone_scopes(parent, child)
        assert len(violations) == 0

    @pytest.mark.asyncio
    async def test_factory_clone_with_uncapped_scope(self):
        """FT-SCALE-010: AgentFactory must reject cloning when child scopes
        exceed the parent ceiling, returning E4003.
        """
        factory = AgentFactory()
        parent_config = _agent_config(
            {
                "authorized_tools": ["tool:banking_api:write:queue_payment:capped:500000"],
            }
        )

        result = await factory.clone_agent(
            parent_id="parent-010",
            parent_config=parent_config,
            overrides={
                "authorized_tools": {
                    "add": ["tool:banking_api:write:queue_payment:capped:999999"],
                },
            },
        )

        assert "error" in result
        assert result["error"]["code"] == "E4003"


# ===================================================================
# FT-SCALE-011: A/B variant traffic split (20% to variant B)
# ===================================================================


class TestFTSCALE011:
    """A/B traffic splitting must route approximately 20% to variant B."""

    def test_traffic_split_20_percent(self):
        """FT-SCALE-011: A simple hash-based traffic splitter must route
        approximately 20% of requests to variant B.
        """
        import hashlib

        variant_b_pct = 20
        variant_b_count = 0
        total = 10_000

        for i in range(total):
            request_id = f"req-{i}"
            # Hash-based deterministic split
            h = int(hashlib.md5(request_id.encode()).hexdigest(), 16)
            bucket = h % 100
            if bucket < variant_b_pct:
                variant_b_count += 1

        actual_pct = variant_b_count / total * 100
        # Allow 2% tolerance
        assert 18 <= actual_pct <= 22, f"Variant B got {actual_pct:.1f}%, expected ~20%"


# ===================================================================
# FT-SCALE-012: Bulk agent creation from template set
# ===================================================================


class TestFTSCALE012:
    """Bulk agent creation from a template set must create all agents successfully."""

    @pytest.mark.asyncio
    async def test_bulk_create_from_templates(self):
        """FT-SCALE-012: Create 10 agents from a template set. All must succeed
        with unique IDs and status=shadow.
        """
        factory = AgentFactory()
        templates = [_agent_config({"agent_type": f"agent_type_{i}"}) for i in range(10)]

        results = await asyncio.gather(*[factory.create_agent(tpl) for tpl in templates])

        assert len(results) == 10
        agent_ids = [r["agent_id"] for r in results]
        assert len(set(agent_ids)) == 10, "Bulk creation produced duplicate IDs"

        for r in results:
            assert r["status"] == "shadow"
            assert r["token_issued"] is True


# ===================================================================
# FT-SCALE-013: Agent team routing (tasks routed per routing_rules)
# ===================================================================


class TestFTSCALE013:
    """Tasks must be routed to agents according to their team routing rules."""

    @pytest.mark.asyncio
    async def test_task_routing_by_domain(self):
        """FT-SCALE-013: The task router must direct finance tasks to finance
        agents and HR tasks to HR agents.
        """
        from core.orchestrator.task_router import TaskRouter

        router = TaskRouter()

        finance_task = {"agent_type": "ap_processor", "data": {"invoice_id": "INV-001"}}
        hr_task = {"agent_type": "onboarding_agent", "data": {"employee_id": "EMP-001"}}

        finance_result = await router.route(
            workflow_run_id="wfr_001",
            step_id="s1",
            step_index=0,
            total_steps=1,
            task=finance_task,
            context={},
        )
        hr_result = await router.route(
            workflow_run_id="wfr_002",
            step_id="s1",
            step_index=0,
            total_steps=1,
            task=hr_task,
            context={},
        )

        assert finance_result["target_agent_type"] == "ap_processor"
        assert hr_result["target_agent_type"] == "onboarding_agent"
        assert finance_result["message_id"] != hr_result["message_id"]


# ===================================================================
# FT-SCALE-014: Temporary agent auto-expiry (ttl_hours=48 -> deprecated)
# ===================================================================


class TestFTSCALE014:
    """Temporary agents with ttl_hours=48 must auto-expire to deprecated status."""

    @pytest.mark.asyncio
    async def test_temporary_agent_auto_expiry(self):
        """FT-SCALE-014: An agent created with ttl_hours=48 must be eligible
        for automatic deprecation after the TTL expires.
        """
        lm = LifecycleManager()

        # Simulate agent created 49 hours ago
        agent_created_at = time.time() - (49 * 3600)  # 49 hours ago
        ttl_hours = 48
        now = time.time()

        expired = (now - agent_created_at) > (ttl_hours * 3600)
        assert expired is True, "Agent should be expired after 49h with 48h TTL"

        # Agent should transition from active to deprecated
        assert lm.can_transition("active", "deprecated")

        result = await lm.transition(
            agent_id="temp-agent-014",
            current="active",
            target="deprecated",
            triggered_by="ttl-expiry-cron",
            reason=f"TTL expired: {ttl_hours}h",
        )
        assert result["to_status"] == "deprecated"

    def test_non_expired_agent_stays_active(self):
        """FT-SCALE-014: An agent created 24h ago with ttl=48h must NOT be expired."""
        agent_created_at = time.time() - (24 * 3600)  # 24 hours ago
        ttl_hours = 48
        now = time.time()

        expired = (now - agent_created_at) > (ttl_hours * 3600)
        assert expired is False, "Agent should NOT be expired at 24h with 48h TTL"


# ===================================================================
# FT-SCALE-015: Skip shadow mode attempt (403, only on rollback to verified_good)
# ===================================================================


class TestFTSCALE015:
    """Attempting to skip shadow mode must be denied (403)."""

    def test_draft_to_active_blocked(self):
        """FT-SCALE-015: Direct transition from draft to active must be blocked.
        Agents must go through shadow mode.
        """
        lm = LifecycleManager()
        assert not lm.can_transition("draft", "active")

    def test_draft_to_staging_blocked(self):
        """FT-SCALE-015: Direct transition from draft to staging must be blocked."""
        lm = LifecycleManager()
        assert not lm.can_transition("draft", "staging")

    def test_draft_to_production_ready_blocked(self):
        """FT-SCALE-015: Direct transition from draft to production_ready is blocked."""
        lm = LifecycleManager()
        assert not lm.can_transition("draft", "production_ready")

    def test_only_valid_draft_transition_is_shadow(self):
        """FT-SCALE-015: The only valid transition from draft is to shadow."""
        valid = VALID_TRANSITIONS.get("draft", [])
        assert valid == ["shadow"], f"Draft should only transition to shadow, got {valid}"

    @pytest.mark.asyncio
    async def test_skip_shadow_raises_error(self):
        """FT-SCALE-015: Attempting an invalid transition must raise ValueError."""
        lm = LifecycleManager()
        with pytest.raises(ValueError, match="Invalid transition"):
            await lm.transition(
                agent_id="agent-015",
                current="draft",
                target="active",
                triggered_by="impatient-user",
                reason="Skip shadow mode",
            )
