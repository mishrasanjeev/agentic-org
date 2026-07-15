"""Typed action taxonomy and fail-closed pre-dispatch containment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from core.config import is_strict_runtime_env

ACTION_TAXONOMY_VERSION = "2026-07-14.v1"
FORCE_SHADOW_FLAG = "safety.unsafe_actions.force_shadow"


class ActionDomain(StrEnum):
    PLATFORM = "platform"
    FINANCE = "finance"
    CA = "ca"
    MARKETING = "marketing"
    HR = "hr"
    OPERATIONS = "operations"
    CBO = "cbo"
    COMMERCE = "commerce"


class ActionRisk(StrEnum):
    READ = "read"
    DRAFT = "draft"
    INTERNAL_WRITE = "internal-write"
    CUSTOMER_WRITE = "customer-write"
    MONEY = "money"
    FILING = "filing"
    EMPLOYMENT = "employment"
    ACCESS = "access"
    DESTRUCTIVE = "destructive"
    SIGNATURE = "signature"
    DISCLOSURE = "disclosure"
    REGULATOR = "regulator"


class ActionMode(StrEnum):
    READ_ONLY = "read-only"
    DRAFT = "draft"
    SHADOW = "shadow"
    LIVE = "live"
    BLOCKED = "blocked"


UNSAFE_ACTION_RISKS = frozenset(set(ActionRisk) - {ActionRisk.READ, ActionRisk.DRAFT})
_ALIASES = {
    "security": "platform",
    "accounting": "finance",
    "tax": "finance",
    "cfo": "finance",
    "ca_firm": "ca",
    "chartered_accountant": "ca",
    "cmo": "marketing",
    "human_resources": "hr",
    "chro": "hr",
    "ops": "operations",
    "coo": "operations",
    "it": "operations",
    "support": "operations",
    "facilities": "operations",
    "backoffice": "cbo",
    "back_office": "cbo",
    "legal": "cbo",
    "risk": "cbo",
    "corporate": "cbo",
    "comms": "cbo",
    "sales": "commerce",
}
_NORMALIZE = re.compile(r"[^a-z0-9]+")


def _norm(value: object) -> str:
    return _NORMALIZE.sub("_", str(value or "").strip().lower()).strip("_")


def _domain(value: ActionDomain | str | None) -> ActionDomain | None:
    if isinstance(value, ActionDomain):
        return value
    normalized = _norm(value)
    normalized = _ALIASES.get(normalized, normalized)
    try:
        return ActionDomain(normalized)
    except ValueError:
        return None


def _set(*values: str) -> frozenset[str]:
    return frozenset(values)


_ALL = frozenset(ActionDomain)
_CUSTOMER = frozenset(set(ActionDomain) - {ActionDomain.PLATFORM})
_RULE_GROUPS: tuple[tuple[ActionRisk, frozenset[ActionDomain], frozenset[str]], ...] = (
    (
        ActionRisk.READ,
        _ALL,
        _set(
            "read_record",
            "get_record",
            "list_records",
            "search_records",
            "fetch_report",
            "check_status",
            "get_trial_balance",
            "get_ledger_balance",
            "get_profit_loss",
            "get_balance_sheet",
            "list_invoices",
            "list_overdue_invoices",
            "fetch_bank_statement",
            "check_account_balance",
            "get_transaction_list",
            "fetch_gstr2a",
            "check_tds_credit_in_26as",
            "apply_macro",
            "buyer_discovery_preview",
            "catalog_get_item",
            "catalog_search",
            "check_filing_status",
            "check_order_status",
            "get_access_log",
            "get_analytics",
            "get_applications",
            "get_attendance",
            "get_backlinks",
            "get_balance",
            "get_bill_by_id",
            "get_campaign_analytics",
            "get_campaign_insights",
            "get_campaign_performance",
            "get_campaign_performance_metrics",
            "get_campaign_report",
            "get_campaign_stats",
            "get_cash_position",
            "get_compliance_notice",
            "get_csat_score",
            "get_deal",
            "get_domain_rating",
            "get_employee",
            "get_invoice_by_id",
            "get_mentions",
            "get_org_chart",
            "get_organic_keywords",
            "get_page_tree",
            "get_payslip",
            "get_post_analytics",
            "get_project_metrics",
            "get_search_term_report",
            "get_search_terms",
            "get_share_of_voice",
            "get_sla_breach_status",
            "get_stats",
            "inventory_check",
            "list_active_sessions",
            "list_bills",
            "list_channel_videos",
            "list_contacts",
            "list_deals",
            "list_owners",
            "list_vendor_bills",
            "list_vendors",
            "merchant_get_profile",
            "payment_get_status",
            "query",
            "read_inbox",
            "search_bills",
            "search_campaigns",
            "search_candidates",
            "search_contacts",
            "search_content_fulltext",
            "search_emails",
            "search_invoices",
            "search_issues",
        ),
    ),
    (
        ActionRisk.DRAFT,
        _ALL,
        _set(
            "prepare_draft",
            "draft_customer_message",
            "draft_disclosure",
            "calculate_tds",
            "generate_tds_summary",
            "prepare_26q",
            "prepare_24q",
            "prepare_professional_tax_return",
            "get_professional_tax_challan_draft",
            "build_filing_payload",
            "propose_payment",
        ),
    ),
    (
        ActionRisk.INTERNAL_WRITE,
        _ALL,
        _set(
            "create_internal_record",
            "update_internal_record",
            "create_journal_entry",
            "create_tds_entry",
            "update_bill",
            "post_voucher",
            "reconcile_bank",
            "write_off_invoice",
            "acknowledge_incident",
            "add_comment",
            "add_list_member",
            "assign_contact_owner",
            "associate_contact_to_company",
            "create_ap_invoice",
            "create_bill",
            "create_contact",
            "create_incident",
            "create_invoice",
            "create_issue",
            "create_item",
            "create_ticket",
            "create_vendor",
            "escalate_to_group",
            "generate_postmortem_doc",
            "manage_on_call_schedule",
            "post_journal_entry",
            "record_expense",
            "trigger_alert_with_context",
            "update_contact",
        ),
    ),
    (
        ActionRisk.CUSTOMER_WRITE,
        _CUSTOMER,
        _set(
            "send_customer_message",
            "send_email",
            "send_message",
            "reply_ticket",
            "publish_content",
            "launch_campaign",
            "create_calendar_event",
            "create_tweet",
            "create_update",
            "post_alert",
            "schedule_social_post",
            "send_campaign",
            "slack_send_message",
            # Despite the provider's "draft" terminology, this creates
            # durable remote cart state and must not inherit DRAFT dispatch.
            "cart_create",
        ),
    ),
    (
        ActionRisk.MONEY,
        frozenset({ActionDomain.FINANCE, ActionDomain.CA, ActionDomain.COMMERCE}),
        _set(
            "queue_payment",
            "initiate_payment",
            "pay_tax_challan",
            "create_payment_intent",
            "capture_payment",
            "issue_refund",
            "transfer_funds",
            "create_payment_link",
            "checkout_create",
            "create_order",
            "payment_create_intent",
        ),
    ),
    (
        ActionRisk.MONEY,
        frozenset({ActionDomain.MARKETING}),
        _set("mutate_campaign_budget"),
    ),
    (
        ActionRisk.FILING,
        frozenset({ActionDomain.FINANCE, ActionDomain.CA}),
        _set(
            "push_gstr1_data",
            "file_gstr3b",
            "file_gstr9",
            "file_26q_return",
            "file_24q_return",
            "file_itr",
            "submit_professional_tax_return",
        ),
    ),
    (
        ActionRisk.EMPLOYMENT,
        frozenset({ActionDomain.HR}),
        _set(
            "shortlist_candidate",
            "reject_candidate",
            "hire_candidate",
            "create_employee",
            "terminate_employee",
            "update_compensation",
            "process_payroll",
            "post_job",
            "post_leave",
            "run_payroll",
            "schedule_interview",
            "send_inmail",
            "send_offer",
            "update_performance",
        ),
    ),
    (
        ActionRisk.ACCESS,
        _ALL,
        _set(
            "provision_access",
            "revoke_access",
            "disable_user",
            "enable_user",
            "reset_credentials",
            "assign_group",
            "consent_exchange",
            "consent_request",
            "deactivate_user",
            "manage_space_permissions",
            "provision_user",
            "remove_group",
        ),
    ),
    (
        ActionRisk.DESTRUCTIVE,
        _ALL,
        _set(
            "delete_resource",
            "purge_resource",
            "restart_service",
            "rollback_deployment",
            "terminate_instance",
            "delete_contact",
        ),
    ),
    (
        ActionRisk.SIGNATURE,
        frozenset({ActionDomain.CA, ActionDomain.HR, ActionDomain.CBO}),
        _set(
            "create_envelope",
            "send_for_signature",
            "apply_signature",
            "execute_contract",
        ),
    ),
    (
        ActionRisk.DISCLOSURE,
        frozenset({ActionDomain.MARKETING, ActionDomain.CBO}),
        _set(
            "publish_disclosure",
            "publish_crisis_response",
            "publish_board_material",
            "publish_press_release",
        ),
    ),
    (
        ActionRisk.REGULATOR,
        frozenset({ActionDomain.FINANCE, ActionDomain.CA, ActionDomain.HR, ActionDomain.CBO}),
        _set(
            "respond_to_notice",
            "submit_regulatory_response",
            "update_entity_registration",
            "generate_eway_bill",
            "generate_einvoice_irn",
        ),
    ),
)


def _rule(action: object) -> tuple[str, ActionRisk, frozenset[ActionDomain]] | None:
    raw = str(action or "").strip()
    candidates = {_norm(raw)}
    if ":" in raw:
        candidates.add(_norm(raw.rsplit(":", 1)[-1]))
    for risk, domains, actions in _RULE_GROUPS:
        match = next((candidate for candidate in candidates if candidate in actions), None)
        if match:
            return match, risk, domains
    return None


def classify_action(action: object, *, domain: ActionDomain | str | None = None) -> ActionRisk | None:
    rule = _rule(action)
    if not rule:
        return None
    canonical_domain = _domain(domain) if domain is not None else None
    if domain is not None and canonical_domain not in rule[2]:
        return None
    return rule[1]


def capability_flag_key(domain: ActionDomain, risk: ActionRisk) -> str:
    return f"safety.live_capability.{domain.value}.{risk.value.replace('-', '_')}"


@dataclass(frozen=True, slots=True)
class ActionContext:
    tenant_id: UUID | str | None
    company_id: UUID | str | None
    domain: ActionDomain | str | None
    runtime_env: str | None


@dataclass(frozen=True, slots=True)
class CapabilityAuthorization:
    authorization_id: str
    tenant_id: UUID | str
    company_id: UUID | str
    domain: ActionDomain | str
    action: str
    risk: ActionRisk | str

    def matches(
        self, *, tenant_id: UUID, company_id: UUID, domain: ActionDomain, action: str, risk: ActionRisk
    ) -> bool:
        try:
            auth_risk = ActionRisk(str(self.risk).replace("_", "-"))
        except ValueError:
            return False
        auth_rule = _rule(self.action)
        auth_action = auth_rule[0] if auth_rule else _norm(self.action)
        return (
            bool(self.authorization_id.strip())
            and _uuid(self.tenant_id) == tenant_id
            and _uuid(self.company_id) == company_id
            and _domain(self.domain) == domain
            and auth_action == action
            and auth_risk == risk
        )


@dataclass(frozen=True, slots=True)
class ActionDecision:
    mode: ActionMode
    dispatch_allowed: bool
    safe_mode_allowed: bool
    reason: str
    requested_action: str
    canonical_action: str | None
    risk: ActionRisk | None
    domain: ActionDomain | None
    strict_runtime: bool
    required_feature_flag: str | None = None
    taxonomy_version: str = ACTION_TAXONOMY_VERSION

    @property
    def blocked(self) -> bool:
        return self.mode is ActionMode.BLOCKED

    @property
    def shadow_only(self) -> bool:
        return self.mode is ActionMode.SHADOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "dispatch_allowed": self.dispatch_allowed,
            "safe_mode_allowed": self.safe_mode_allowed,
            "reason": self.reason,
            "requested_action": self.requested_action,
            "canonical_action": self.canonical_action,
            "risk": self.risk.value if self.risk else None,
            "domain": self.domain.value if self.domain else None,
            "strict_runtime": self.strict_runtime,
            "required_feature_flag": self.required_feature_flag,
            "taxonomy_version": self.taxonomy_version,
        }


class FeatureFlagResolver(Protocol):
    async def __call__(self, flag_key: str, *, tenant_id: UUID, company_id: UUID, default: bool) -> bool: ...


async def database_feature_flag_resolver(flag_key: str, *, tenant_id: UUID, company_id: UUID, default: bool) -> bool:
    from core.feature_flags import is_enabled

    return await is_enabled(flag_key, tenant_id=tenant_id, user_id=company_id, default=default)


async def evaluate_action(
    action: object,
    *,
    context: ActionContext,
    capability_authorization: CapabilityAuthorization | None = None,
    feature_flags: FeatureFlagResolver | None = None,
) -> ActionDecision:
    requested = str(action or "").strip()
    strict = is_strict_runtime_env(context.runtime_env)
    tenant_id, reason = _required_uuid(context.tenant_id, "tenant")
    if tenant_id is None:
        return _decision(ActionMode.BLOCKED, False, False, reason, requested, None, None, None, strict)
    company_id, reason = _required_uuid(context.company_id, "company")
    if company_id is None:
        return _decision(ActionMode.BLOCKED, False, False, reason, requested, None, None, None, strict)
    domain = _domain(context.domain)
    if domain is None:
        reason = "context_domain_missing" if not str(context.domain or "").strip() else "context_domain_unknown"
        return _decision(ActionMode.BLOCKED, False, False, reason, requested, None, None, None, strict)
    rule = _rule(action)
    if rule is None:
        reason = "action_missing" if not requested else "action_unknown"
        return _decision(ActionMode.BLOCKED, False, False, reason, requested, None, None, domain, strict)
    canonical, risk, domains = rule
    if domain not in domains:
        return _decision(
            ActionMode.BLOCKED, False, False, "action_domain_mismatch", requested, canonical, risk, domain, strict
        )
    if risk is ActionRisk.READ:
        return _decision(
            ActionMode.READ_ONLY, True, True, "read_action_allowed", requested, canonical, risk, domain, strict
        )
    if risk is ActionRisk.DRAFT:
        return _decision(
            ActionMode.DRAFT, True, True, "draft_action_allowed", requested, canonical, risk, domain, strict
        )
    required_flag = capability_flag_key(domain, risk)
    # W0 containment is irreversible in strict runtimes for this slice.
    # Feature flags and caller-provided authorization objects must not be able
    # to promote unsafe actions until the durable, signed PLAT-03/04 approval
    # protocol is implemented and verified.
    if strict:
        return _decision(
            ActionMode.SHADOW,
            False,
            True,
            "unsafe_action_force_shadow",
            requested,
            canonical,
            risk,
            domain,
            strict,
            required_flag,
        )
    resolver = feature_flags
    force_shadow = (
        False if resolver is None else await _flag(resolver, FORCE_SHADOW_FLAG, tenant_id, company_id, False, True)
    )
    if force_shadow:
        return _decision(
            ActionMode.SHADOW,
            False,
            True,
            "unsafe_action_force_shadow",
            requested,
            canonical,
            risk,
            domain,
            strict,
            required_flag,
        )
    live = False if resolver is None else await _flag(resolver, required_flag, tenant_id, company_id, False, False)
    if not live:
        return _decision(
            ActionMode.SHADOW,
            False,
            True,
            "live_capability_flag_disabled",
            requested,
            canonical,
            risk,
            domain,
            strict,
            required_flag,
        )
    if capability_authorization is None:
        return _decision(
            ActionMode.SHADOW,
            False,
            True,
            "capability_authorization_missing",
            requested,
            canonical,
            risk,
            domain,
            strict,
            required_flag,
        )
    if not capability_authorization.matches(
        tenant_id=tenant_id, company_id=company_id, domain=domain, action=canonical, risk=risk
    ):
        return _decision(
            ActionMode.SHADOW,
            False,
            True,
            "capability_authorization_mismatch",
            requested,
            canonical,
            risk,
            domain,
            strict,
            required_flag,
        )
    return _decision(
        ActionMode.LIVE,
        True,
        False,
        "live_capability_authorized",
        requested,
        canonical,
        risk,
        domain,
        strict,
        required_flag,
    )


async def _flag(
    resolver: FeatureFlagResolver, key: str, tenant_id: UUID, company_id: UUID, default: bool, failure: bool
) -> bool:
    try:
        return bool(await resolver(key, tenant_id=tenant_id, company_id=company_id, default=default))
    # enterprise-gate: broad-except-ok reason=flag-failure-preserves-unsafe-action-containment
    except Exception:
        return failure


def _uuid(value: UUID | str | None) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


def _required_uuid(value: UUID | str | None, name: str) -> tuple[UUID | None, str]:
    if not str(value or "").strip():
        return None, f"context_{name}_missing"
    parsed = _uuid(value)
    return (parsed, "") if parsed else (None, f"context_{name}_invalid")


def _decision(
    mode: ActionMode,
    dispatch: bool,
    safe: bool,
    reason: str,
    requested: str,
    action: str | None,
    risk: ActionRisk | None,
    domain: ActionDomain | None,
    strict: bool,
    flag: str | None = None,
) -> ActionDecision:
    return ActionDecision(mode, dispatch, safe, reason, requested, action, risk, domain, strict, flag)
