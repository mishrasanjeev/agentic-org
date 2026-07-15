"""Action taxonomy and ToolGateway containment contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import core.langgraph.tool_adapter as tool_adapter_module
import core.tool_gateway.gateway as gateway_module
from core.governance.action_policy import (
    FORCE_SHADOW_FLAG,
    ActionContext,
    ActionDomain,
    ActionMode,
    ActionRisk,
    CapabilityAuthorization,
    capability_flag_key,
    classify_action,
    evaluate_action,
)
from core.tool_gateway.gateway import ToolGateway

TENANT_ID = "11111111-1111-1111-1111-111111111111"
COMPANY_ID = "22222222-2222-2222-2222-222222222222"


def _context(
    *,
    tenant_id: str | None = TENANT_ID,
    company_id: str | None = COMPANY_ID,
    domain: str | None = "finance",
    runtime_env: str = "production",
) -> ActionContext:
    return ActionContext(
        tenant_id=tenant_id,
        company_id=company_id,
        domain=domain,
        runtime_env=runtime_env,
    )


async def _live_flags(
    flag_key: str,
    *,
    tenant_id: object,
    company_id: object,
    default: bool,
) -> bool:
    del tenant_id, company_id, default
    return flag_key != FORCE_SHADOW_FLAG


class TestActionClassification:
    def test_connector_qualified_read_and_domain_alias_are_classified(self) -> None:
        assert classify_action("zoho_books:get_trial_balance", domain="cfo") is ActionRisk.READ

    def test_domain_mismatch_is_unknown(self) -> None:
        assert classify_action("queue_payment", domain="marketing") is None

    def test_unsafe_risk_has_stable_company_scoped_flag_key(self) -> None:
        assert capability_flag_key(ActionDomain.FINANCE, ActionRisk.MONEY) == "safety.live_capability.finance.money"

    def test_provider_mutating_cart_is_not_a_draft(self) -> None:
        assert classify_action("grantex_commerce:cart_create", domain="commerce") is ActionRisk.CUSTOMER_WRITE

    def test_api_default_tools_are_classified_or_explicitly_ambiguous(self) -> None:
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _DOMAIN_DEFAULT_TOOLS

        default_tools = {
            tool_name
            for tools in (
                *_AGENT_TYPE_DEFAULT_TOOLS.values(),
                *_DOMAIN_DEFAULT_TOOLS.values(),
            )
            for tool_name in tools
        }
        ambiguous_tools = {
            "approve_draft_post",
            "create_campaign",
            "create_page",
            "manage_publishing_queue",
            "run_automated_runbook",
            "update_ticket",
        }

        assert len(default_tools) == 135
        assert {tool_name for tool_name in default_tools if classify_action(tool_name) is None} == ambiguous_tools


class TestActionEvaluation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("context", "reason"),
        [
            (_context(tenant_id=None), "context_tenant_missing"),
            (_context(tenant_id="not-a-uuid"), "context_tenant_invalid"),
            (_context(company_id=None), "context_company_missing"),
            (_context(company_id="not-a-uuid"), "context_company_invalid"),
            (_context(domain=None), "context_domain_missing"),
            (_context(domain="unregistered-domain"), "context_domain_unknown"),
        ],
    )
    async def test_missing_or_invalid_context_fails_closed(
        self,
        context: ActionContext,
        reason: str,
    ) -> None:
        decision = await evaluate_action("get_trial_balance", context=context)

        assert decision.mode is ActionMode.BLOCKED
        assert decision.dispatch_allowed is False
        assert decision.safe_mode_allowed is False
        assert decision.reason == reason

    @pytest.mark.asyncio
    async def test_unknown_action_fails_closed(self) -> None:
        decision = await evaluate_action(
            "google_ads:not_registered",
            context=_context(domain="marketing"),
        )

        assert decision.mode is ActionMode.BLOCKED
        assert decision.reason == "action_unknown"
        assert decision.risk is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "expected_mode", "expected_risk"),
        [
            ("get_trial_balance", ActionMode.READ_ONLY, ActionRisk.READ),
            ("prepare_draft", ActionMode.DRAFT, ActionRisk.DRAFT),
        ],
    )
    async def test_read_and_draft_actions_can_dispatch_without_live_flags(
        self,
        action: str,
        expected_mode: ActionMode,
        expected_risk: ActionRisk,
    ) -> None:
        decision = await evaluate_action(action, context=_context())

        assert decision.mode is expected_mode
        assert decision.risk is expected_risk
        assert decision.dispatch_allowed is True
        assert decision.safe_mode_allowed is True

    @pytest.mark.asyncio
    async def test_strict_unsafe_action_defaults_to_shadow(self) -> None:
        decision = await evaluate_action("queue_payment", context=_context())

        assert decision.mode is ActionMode.SHADOW
        assert decision.dispatch_allowed is False
        assert decision.safe_mode_allowed is True
        assert decision.reason == "unsafe_action_force_shadow"
        assert decision.required_feature_flag == "safety.live_capability.finance.money"

    @pytest.mark.asyncio
    async def test_strict_runtime_ignores_flags_and_caller_authorization(self) -> None:
        flags = AsyncMock(return_value=True)
        authorization = CapabilityAuthorization(
            authorization_id="caller-constructed",
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="finance",
            action="queue_payment",
            risk=ActionRisk.MONEY,
        )

        decision = await evaluate_action(
            "queue_payment",
            context=_context(runtime_env="production"),
            capability_authorization=authorization,
            feature_flags=flags,
        )

        assert decision.mode is ActionMode.SHADOW
        assert decision.reason == "unsafe_action_force_shadow"
        assert decision.dispatch_allowed is False
        flags.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_feature_flag_failure_preserves_containment(self) -> None:
        async def failing_flags(*args: object, **kwargs: object) -> bool:
            del args, kwargs
            raise RuntimeError("flag service unavailable")

        decision = await evaluate_action(
            "queue_payment",
            context=_context(runtime_env="development"),
            feature_flags=failing_flags,
        )

        assert decision.mode is ActionMode.SHADOW
        assert decision.reason == "unsafe_action_force_shadow"
        assert decision.dispatch_allowed is False

    @pytest.mark.asyncio
    async def test_live_flag_alone_cannot_authorize_unsafe_action(self) -> None:
        decision = await evaluate_action(
            "queue_payment",
            context=_context(runtime_env="development"),
            feature_flags=_live_flags,
        )

        assert decision.mode is ActionMode.SHADOW
        assert decision.reason == "capability_authorization_missing"

    @pytest.mark.asyncio
    async def test_authorization_must_match_exact_company_and_action(self) -> None:
        authorization = CapabilityAuthorization(
            authorization_id="cap-auth-1",
            tenant_id=TENANT_ID,
            company_id="33333333-3333-3333-3333-333333333333",
            domain="finance",
            action="queue_payment",
            risk=ActionRisk.MONEY,
        )

        decision = await evaluate_action(
            "queue_payment",
            context=_context(runtime_env="development"),
            capability_authorization=authorization,
            feature_flags=_live_flags,
        )

        assert decision.mode is ActionMode.SHADOW
        assert decision.reason == "capability_authorization_mismatch"

    @pytest.mark.asyncio
    async def test_relaxed_runtime_can_exercise_exact_authorization_path(self) -> None:
        authorization = CapabilityAuthorization(
            authorization_id="cap-auth-1",
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="finance",
            action="banking_api:queue_payment",
            risk="money",
        )

        decision = await evaluate_action(
            "banking_api:queue_payment",
            context=_context(runtime_env="development"),
            capability_authorization=authorization,
            feature_flags=_live_flags,
        )

        assert decision.mode is ActionMode.LIVE
        assert decision.reason == "live_capability_authorized"
        assert decision.dispatch_allowed is True
        assert decision.safe_mode_allowed is False


class TestToolGatewayContainment:
    @pytest.fixture
    def audit(self) -> AsyncMock:
        audit = AsyncMock()
        audit.log = AsyncMock()
        return audit

    @pytest.fixture
    def connector(self) -> AsyncMock:
        connector = AsyncMock()
        connector.execute_tool.return_value = {"status": "ok"}
        return connector

    @pytest.mark.asyncio
    async def test_unknown_action_is_audited_and_never_dispatched(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "production")
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector("google_ads", connector)

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:google_ads:read:campaign_performance"],
            connector_name="google_ads",
            tool_name="not_registered",
            params={},
            company_id=COMPANY_ID,
            domain="marketing",
        )

        assert result["error"]["code"] == "E1011"
        assert result["governance"]["reason"] == "action_unknown"
        connector.execute_tool.assert_not_awaited()
        audit.log.assert_awaited_once()
        assert audit.log.await_args.kwargs["action"] == "action_contained"

    @pytest.mark.asyncio
    async def test_strict_runtime_requires_company_context_before_dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "production")
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector(
            "zoho_books",
            connector,
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
        )

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:zoho_books:read:trial_balance"],
            connector_name="zoho_books",
            tool_name="get_trial_balance",
            params={},
            domain="finance",
        )

        assert result["error"]["code"] == "E1011"
        assert result["governance"]["reason"] == "context_company_missing"
        connector.execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_known_read_action_reaches_connector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "production")
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector(
            "zoho_books",
            connector,
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
        )

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:zoho_books:read:trial_balance"],
            connector_name="zoho_books",
            tool_name="get_trial_balance",
            params={"as_of": "2026-06-30"},
            company_id=COMPANY_ID,
            domain="finance",
        )

        assert result == {"status": "ok"}
        connector.execute_tool.assert_awaited_once_with(
            "get_trial_balance",
            {"as_of": "2026-06-30"},
        )

    @pytest.mark.asyncio
    async def test_cart_create_is_contained_before_provider_dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "production")
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector(
            "grantex_commerce",
            connector,
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
        )

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:grantex_commerce:write:cart_create"],
            connector_name="grantex_commerce",
            tool_name="cart_create",
            params={"idempotency_key": "cart-1"},
            company_id=COMPANY_ID,
            domain="commerce",
        )

        assert result["error"]["code"] == "E1011"
        assert result["governance"]["risk"] == "customer-write"
        connector.execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_relaxed_live_flag_still_needs_authorization(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "development")
        monkeypatch.setattr(
            gateway_module,
            "database_feature_flag_resolver",
            _live_flags,
        )
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector("banking_api", connector)

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:banking_api:write:payment"],
            connector_name="banking_api",
            tool_name="queue_payment",
            params={"amount": 100},
            company_id=COMPANY_ID,
            domain="finance",
        )

        assert result["error"]["code"] == "E1011"
        assert result["governance"]["reason"] == "capability_authorization_missing"
        connector.execute_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_strict_runtime_blocks_exact_authorization_and_live_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
        audit: AsyncMock,
        connector: AsyncMock,
    ) -> None:
        monkeypatch.setattr(gateway_module.settings, "env", "production")
        monkeypatch.setattr(
            gateway_module,
            "database_feature_flag_resolver",
            _live_flags,
        )
        gateway = ToolGateway(audit_logger=audit)
        gateway.register_connector("banking_api", connector)
        authorization = CapabilityAuthorization(
            authorization_id="cap-auth-1",
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="finance",
            action="queue_payment",
            risk=ActionRisk.MONEY,
        )

        result = await gateway.execute(
            tenant_id=TENANT_ID,
            agent_id="agent-1",
            agent_scopes=["tool:banking_api:write:payment"],
            connector_name="banking_api",
            tool_name="queue_payment",
            params={"amount": 100},
            company_id=COMPANY_ID,
            domain="finance",
            capability_authorization=authorization,
        )

        assert result["error"]["code"] == "E1011"
        assert result["governance"]["reason"] == "unsafe_action_force_shadow"
        connector.execute_tool.assert_not_awaited()


class TestLangGraphConnectorContainment:
    @pytest.mark.asyncio
    async def test_unknown_action_is_blocked_before_connector_registry_lookup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tool_adapter_module.settings, "env", "production")
        registry_get = MagicMock()
        monkeypatch.setattr(tool_adapter_module.ConnectorRegistry, "get", registry_get)

        result = await tool_adapter_module._execute_connector_tool(
            "google_ads",
            "not_registered",
            {},
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="marketing",
        )

        assert result["error"] == "action_contained"
        assert result["governance"]["reason"] == "action_unknown"
        registry_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_strict_unsafe_action_ignores_live_flags_and_authorization(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(tool_adapter_module.settings, "env", "production")
        monkeypatch.setattr(
            tool_adapter_module,
            "database_feature_flag_resolver",
            _live_flags,
        )
        registry_get = MagicMock()
        monkeypatch.setattr(tool_adapter_module.ConnectorRegistry, "get", registry_get)
        authorization = CapabilityAuthorization(
            authorization_id="caller-constructed",
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="finance",
            action="queue_payment",
            risk=ActionRisk.MONEY,
        )

        result = await tool_adapter_module._execute_connector_tool(
            "banking_api",
            "queue_payment",
            {"amount": 100},
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            domain="finance",
            capability_authorization=authorization,
        )

        assert result["error"] == "action_contained"
        assert result["governance"]["reason"] == "unsafe_action_force_shadow"
        registry_get.assert_not_called()
