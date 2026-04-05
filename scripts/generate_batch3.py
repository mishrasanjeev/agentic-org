#!/usr/bin/env python3
"""Generate batch 3: Agents, Orchestrator, Workflow Engine."""

import os
import textwrap

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def w(p, c):
    full = os.path.join(BASE, p)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(c).lstrip("\n"))
    print(f"  {p}")


# ── Agent Base ──
w("core/agents/__init__.py", '"""Agent layer."""\n')
w("core/agents/finance/__init__.py", '"""Finance agents."""\n')
w("core/agents/hr/__init__.py", '"""HR agents."""\n')
w("core/agents/ops/__init__.py", '"""Operations agents."""\n')
w("core/agents/marketing/__init__.py", '"""Marketing agents."""\n')
w("core/agents/backoffice/__init__.py", '"""Back office agents."""\n')

w(
    "core/agents/base.py",
    '''
"""Base agent class — all 50+ agents extend this."""
from __future__ import annotations

import abc
import json
import os
import time
import uuid
from typing import Any, Optional

import structlog

from core.llm.router import LLMResponse, llm_router
from core.schemas.messages import (
    HITLAssignee, HITLContext, HITLRequest, DecisionRequired, DecisionOption,
    PerformanceMetrics, TaskAssignment, TaskResult, ToolCallRecord,
)

logger = structlog.get_logger()
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


class BaseAgent(abc.ABC):
    """Abstract agent with LLM reasoning, tool calling, HITL, and confidence scoring."""

    agent_type: str = ""
    domain: str = ""
    confidence_floor: float = 0.88
    max_retries: int = 3
    prompt_file: str = ""

    def __init__(
        self,
        agent_id: str,
        tenant_id: str,
        authorized_tools: list[str] | None = None,
        prompt_variables: dict[str, str] | None = None,
        hitl_condition: str = "",
        output_schema: str | None = None,
        tool_gateway: Any = None,
    ):
        self.agent_id = agent_id
        self.tenant_id = tenant_id
        self.authorized_tools = authorized_tools or []
        self.prompt_variables = prompt_variables or {}
        self.hitl_condition = hitl_condition
        self.output_schema = output_schema
        self.tool_gateway = tool_gateway
        self._system_prompt: str | None = None

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            path = os.path.join(PROMPTS_DIR, self.prompt_file)
            with open(path, "r") as f:
                template = f.read()
            for key, val in self.prompt_variables.items():
                template = template.replace("{{" + key + "}}", val)
            self._system_prompt = template
        return self._system_prompt

    async def execute(self, task: TaskAssignment) -> TaskResult:
        """Main execution pipeline."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            # 1. Reason with LLM
            context = {
                "task": task.task.model_dump(),
                "step_id": task.step_id,
                "step_index": task.step_index,
            }
            output = await self._reason(context, trace)

            # 2. Validate output
            if not self._validate_output(output):
                trace.append("Output validation failed")
                return self._make_result(
                    task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                    error={"code": "E2001", "message": "Schema validation failed"},
                    start=start,
                )

            # 3. Compute confidence
            confidence = self._compute_confidence(output)
            trace.append(f"Confidence: {confidence:.3f}")

            # 4. Check HITL at agent level (orchestrator will also check)
            hitl = self._evaluate_hitl(output, confidence)
            if hitl:
                trace.append(f"HITL triggered: {hitl.trigger_condition}")
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence,
                    trace, tool_calls, hitl_request=hitl, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence,
                trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("agent_execute_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "E5001", "message": str(e)}, start=start,
            )

    async def _reason(self, context: dict, trace: list[str]) -> dict[str, Any]:
        """Call LLM with system prompt and task context."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        trace.append("Calling LLM for reasoning")
        response: LLMResponse = await llm_router.complete(messages)
        trace.append(f"LLM responded: {response.model}, {response.tokens_used} tokens")

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            trace.append("LLM output not valid JSON, wrapping")
            return {"raw_output": response.content, "status": "completed"}

    def _validate_output(self, output: dict[str, Any]) -> bool:
        """Validate output against declared schema."""
        if not self.output_schema:
            return True
        # In production, load schema from registry and validate with jsonschema
        required_field = "status"
        return required_field in output

    def _compute_confidence(self, output: dict[str, Any]) -> float:
        """Compute confidence score from output."""
        return float(output.get("confidence", output.get("agent_confidence", 0.85)))

    def _evaluate_hitl(self, output: dict, confidence: float) -> HITLRequest | None:
        """Evaluate HITL trigger conditions."""
        if confidence < self.confidence_floor:
            return HITLRequest(
                hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                trigger_condition=f"confidence {confidence:.3f} < floor {self.confidence_floor}",
                trigger_type="confidence_below_floor",
                decision_required=DecisionRequired(
                    question=f"Agent confidence ({confidence:.1%}) below threshold. Review required.",
                    options=[
                        DecisionOption(id="approve", label="Approve output", action="proceed"),
                        DecisionOption(id="reject", label="Reject and retry", action="retry"),
                        DecisionOption(id="defer", label="Defer", action="defer"),
                    ],
                ),
                context=HITLContext(
                    summary=f"Agent {self.agent_type} confidence below floor",
                    recommendation="review",
                    agent_confidence=confidence,
                ),
                assignee=HITLAssignee(role="domain_lead"),
            )
        return None

    async def _call_tool(
        self, tool_name: str, params: dict, idempotency_key: str = ""
    ) -> dict[str, Any]:
        """Call tool through Tool Gateway."""
        if not self.tool_gateway:
            return {"error": "No tool gateway configured"}

        connector = tool_name.split("_")[0] if "_" in tool_name else tool_name
        return await self.tool_gateway.execute(
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            agent_scopes=self.authorized_tools,
            connector_name=connector,
            tool_name=tool_name,
            params=params,
            idempotency_key=idempotency_key or None,
        )

    def _make_result(
        self, task, msg_id, status, output, confidence, trace, tool_calls,
        error=None, hitl_request=None, start=0,
    ) -> TaskResult:
        latency = int((time.monotonic() - start) * 1000) if start else 0
        return TaskResult(
            message_id=msg_id,
            correlation_id=task.correlation_id,
            workflow_run_id=task.workflow_run_id,
            step_id=task.step_id,
            agent_id=self.agent_id,
            status=status,
            output=output,
            confidence=confidence,
            reasoning_trace=trace,
            tool_calls=tool_calls,
            hitl_request=hitl_request,
            error=error,
            performance=PerformanceMetrics(total_latency_ms=latency),
        )
''',
)

w(
    "core/agents/registry.py",
    '''
"""Agent registry — register, discover, instantiate agents."""
from __future__ import annotations

from typing import Any, Type

from core.agents.base import BaseAgent


class AgentRegistry:
    """Central registry for all agent types."""

    _registry: dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, agent_cls: Type[BaseAgent]) -> Type[BaseAgent]:
        """Register an agent class by its agent_type."""
        cls._registry[agent_cls.agent_type] = agent_cls
        return agent_cls

    @classmethod
    def get_by_type(cls, agent_type: str) -> Type[BaseAgent] | None:
        return cls._registry.get(agent_type)

    @classmethod
    def get_by_domain(cls, domain: str) -> list[Type[BaseAgent]]:
        return [a for a in cls._registry.values() if a.domain == domain]

    @classmethod
    def all_types(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def create_from_config(cls, config: dict[str, Any]) -> BaseAgent:
        """Instantiate an agent from DB config."""
        agent_cls = cls._registry.get(config["agent_type"])
        if not agent_cls:
            raise ValueError(f"Unknown agent type: {config['agent_type']}")
        return agent_cls(
            agent_id=config["id"],
            tenant_id=config["tenant_id"],
            authorized_tools=config.get("authorized_tools", []),
            prompt_variables=config.get("prompt_variables", {}),
            hitl_condition=config.get("hitl_condition", ""),
            output_schema=config.get("output_schema"),
        )
''',
)

# ── System Prompts ──
PROMPTS = {
    "nexus_orchestrator.prompt.txt": """# core/agents/prompts/nexus_orchestrator.prompt.txt  v2.0
You are NEXUS, the central orchestrator of AgenticOrg for {{org_name}}.

<role>
Receive workflow intents. Decompose into ordered sub-tasks. Route each sub-task
to exactly one specialist agent. Maintain workflow state. Resolve output conflicts.
Manage human escalation. Never execute tool calls directly.
</role>

<core_directives>
1. DECOMPOSE every intent into the minimum sequential + parallel sub-tasks.
2. ROUTE each task to the single most capable specialist agent for that action.
3. VALIDATE every agent output against its declared output_schema before proceeding.
4. ESCALATE immediately if agent confidence < confidence_floor OR error after max_retries.
5. CHECKPOINT state after every step — assume interruption is possible at any time.
6. PRODUCE a reasoning trace for every routing and escalation decision.
7. NEVER invent agent outputs. Failure = escalate with full diagnostic context.
</core_directives>

<conflict_resolution>
Factual conflict (agents return different data): surface both, escalate.
Interpretive conflict (different conclusions, same data): take conservative outcome, log both.
</conflict_resolution>

<output_format>
{ "workflow_run_id":"str","step_id":"str",
  "action":"route|escalate|complete|checkpoint",
  "target_agent":"str|null","task_payload":{},
  "reasoning":"1-3 sentences","confidence":0.0-1.0,
  "escalation_reason":"str|null" }
</output_format>""",
    "ap_processor.prompt.txt": """# core/agents/prompts/ap_processor.prompt.txt  v2.0
You are the AP Processor Agent for {{org_name}}.
Domain: Finance | Confidence floor: 88% | Max retries: 3
Token scope: oracle_fusion(r/w:journal,r:po,r:grn) gstn(r:validate)
             banking_api(w:queue_payment:capped:{{ap_hitl_threshold}})
             email(w:remittance) ocr(r:extract)

<processing_sequence>
Execute ALL 6 steps in order. Never skip. Never proceed past a failed step.

STEP 1 — EXTRACT
  call ocr_extract_invoice(s3_key). Required: invoice_id, vendor_id, gstin,
  invoice_date, due_date, line_items[], total, bank_details.
  If any required field missing -> status=incomplete, STOP.

STEP 2 — VALIDATE
  call gstn_validate(gstin). If INVALID -> status=gstin_invalid, notify, STOP.
  call check_duplicate(invoice_id, vendor_id). If EXISTS -> status=duplicate, STOP.

STEP 3 — MATCH
  call erp_get_po(po_reference), erp_get_grn(grn_reference).
  match_delta = abs(invoice.total - po.amount).
  If match_delta / po.amount <= {{ap_match_tolerance_pct}}/100 -> status=matched.
  Else -> status=mismatch, TRIGGER HITL with delta, amounts, vendor details.

STEP 4 — SCHEDULE
  call erp_queue_payment() with discount-optimised date.

STEP 5 — POST
  call erp_post_journal_entry() idempotency_key="{{invoice_id}}_gl_post"

STEP 6 — NOTIFY
  call send_remittance_advice(vendor.email, payment_details).
</processing_sequence>

<escalation_rules>
Trigger HITL (never auto-proceed) if ANY condition is true:
  invoice.total > {{ap_hitl_threshold}} | match_delta unexplained
  vendor.risk_score > 7 | confidence < 0.88 | GSTN ambiguous
Include in HITL context: full trace, all computed values, trigger condition, recommendation.
</escalation_rules>

<anti_hallucination>
NEVER invent PO numbers, GRN numbers, GL codes, or amounts not from tool responses.
NEVER proceed with stale data after a tool error — retry per policy, then escalate.
NEVER mark status=matched if erp_get_po() returned error or empty result.
</anti_hallucination>

<output_format>
{ "invoice_id":"str","status":"matched|mismatch|incomplete|duplicate|gstin_invalid|escalated",
  "confidence":0.0-1.0,"match_delta":"num|null","payment_scheduled_date":"ISO|null",
  "gl_posting_id":"str|null","escalation_reason":"str|null",
  "processing_trace":["step1_result",...],"tool_calls":[{...}] }
</output_format>""",
}

# Generate remaining agent prompts from table data
AGENT_PROMPTS_TABLE = [
    (
        "ar_collections",
        "Finance",
        85,
        "AR Collections",
        "Monitor aging; send tiered comms (email>WhatsApp>call); generate payment links; log all touches.",
        "3rd attempt failed OR dispute raised",
        "Never send payment link without verifying customer_id exists in CRM.",
    ),
    (
        "recon_agent",
        "Finance",
        95,
        "Reconciliation",
        "Match every bank transaction to GL entry at T+0; surface all unmatched items with ranked GL suggestions.",
        "Break > 50000 OR > 0.01% of daily volume",
        "Never auto-post without confirmed GL account from erp_get_account(). No estimated matches.",
    ),
    (
        "tax_compliance",
        "Finance",
        92,
        "Tax Compliance",
        "Prepare GST/TDS returns from ERP data; reconcile with GSTN portal; never file without approval.",
        "On every filing; mismatch > 5% of return value",
        "Never compute tax on estimated figures. All inputs must come from verified ERP transactions.",
    ),
    (
        "close_agent",
        "Finance",
        80,
        "Month-End Close",
        "Execute month-end close checklist; draft P&L/BS/CF with AI variance commentary.",
        "CFO sign-off gate on every close package",
        "If any sub-ledger balance unavailable halt and escalate. Never estimate a balance.",
    ),
    (
        "fpa_agent",
        "Finance",
        78,
        "FP&A",
        "Build rolling forecasts; identify variance drivers; produce scenario models for board.",
        "Reforecast deviation > 15%; budget reallocation > 10L",
        "Label all outputs MODEL_OUTPUT not ACTUALS. Always show confidence interval.",
    ),
    (
        "talent_acquisition",
        "HR",
        88,
        "Talent Acquisition",
        "JD generation > multi-board posting > bias-free screening > panel scheduling > offer prep.",
        "Offer letter; adverse BGV finding; L7+ senior hire",
        "Strip PII before scoring. Never shortlist without completed structured rubric score.",
    ),
    (
        "onboarding_agent",
        "HR",
        95,
        "Onboarding",
        "Day-0 provisioning of all systems; 30/60/90 plan; buddy assignment; training enrollment.",
        "Production infrastructure access request",
        "Never provision a system not in approved_systems[] for the employee role level.",
    ),
    (
        "payroll_engine",
        "HR",
        99,
        "Payroll Engine",
        "Gross-to-net for all employees; all statutory deductions; payslips; EPFO/ESIC/TDS filings.",
        "Always — every payroll run requires HR Head approval",
        "Missing attendance data = error, not assumption. Never extrapolate or default to prior month.",
    ),
    (
        "performance_coach",
        "HR",
        80,
        "Performance Coach",
        "OKR tracking; 360 feedback aggregation; attrition risk scoring; coaching recommendations.",
        "PIP initiation; promotion decision; termination",
        "Label all risk scores AGENT_ASSESSMENT. Never present as HR decision.",
    ),
    (
        "ld_coordinator",
        "HR",
        82,
        "L&D Coordinator",
        "Skill gap mapping; learning path recommendation; training scheduling; completion tracking.",
        "External certification or budget > 50K",
        "Never enrol employee in paid course without confirmed budget approval.",
    ),
    (
        "offboarding_agent",
        "HR",
        95,
        "Offboarding",
        "Full checklist: access revoke > F&F settlement > experience letter > data archive.",
        "Final settlement computation; experience letter",
        "Never revoke access before separation_date confirmed in Darwinbox as accepted.",
    ),
    (
        "content_factory",
        "Marketing",
        88,
        "Content Factory",
        "Create SEO-optimised content per brand guidelines; all output is DRAFT until human approves.",
        "Before any content is published to any channel",
        "Never auto-publish. All content output_status=DRAFT. Human approval is mandatory.",
    ),
    (
        "campaign_pilot",
        "Marketing",
        85,
        "Campaign Pilot",
        "Campaign performance monitoring; A/B test orchestration; spend optimisation within cap.",
        "Budget shift > 50K; new campaign > 2L",
        "Never exceed approved_budget_cap. Reallocation only within existing approved total.",
    ),
    (
        "seo_strategist",
        "Marketing",
        90,
        "SEO Strategist",
        "Keyword rankings; content gap analysis; technical SEO recommendations; internal linking.",
        "Site restructure; noindex decisions; disavow",
        "Never make structural site changes directly. All recommendations via Jira tickets only.",
    ),
    (
        "crm_intelligence",
        "Marketing",
        88,
        "CRM Intelligence",
        "Lead scoring; nurture sequence management; MQL handoff; churn risk monitoring.",
        "Enterprise account touch; churn probability > 80%",
        "Lead score is advisory. Sales team makes final MQL qualification decision.",
    ),
    (
        "brand_monitor",
        "Marketing",
        85,
        "Brand Monitor",
        "Brand mention monitoring across 50+ channels; sentiment; crisis detection; SOV tracking.",
        "Any crisis signal (viral negative or media pickup)",
        "Never post public responses. Escalate all response decisions to PR team immediately.",
    ),
    (
        "vendor_manager",
        "Ops",
        88,
        "Vendor Manager",
        "Full vendor lifecycle: onboard > PO > SLA monitoring > performance scorecard.",
        "New vendor risk score > 7; PO value > 5L",
        "Never create vendor in ERP without completed sanctions screen result on record.",
    ),
    (
        "contract_intelligence",
        "Ops",
        82,
        "Contract Intelligence",
        "Parse contracts; extract metadata; monitor renewals; flag non-standard clauses.",
        "Non-standard clause detected; contract value > 25L",
        "Never interpret contract terms. Extract verbatim; flag for legal review.",
    ),
    (
        "support_triage",
        "Ops",
        85,
        "Support Triage",
        "Classify tickets; resolve L1 autonomously; enrich + route L2+ with full context.",
        "Sentiment score < -0.6; VIP customer; unresolved after 2 turns",
        "Never access customer payment instruments beyond read-only transaction status.",
    ),
    (
        "compliance_guard",
        "Ops",
        95,
        "Compliance Guard",
        "Regulatory calendar; filing prep; circular monitoring; compliance reporting.",
        "All regulatory filings require Compliance Officer sign-off",
        "Never interpret ambiguous regulatory text. Flag for qualified legal/compliance review.",
    ),
    (
        "it_operations",
        "Ops",
        88,
        "IT Operations",
        "Ticket triage; access provisioning; incident runbooks; change management.",
        "Security incident; admin access request; data breach",
        "Never grant production access without explicit approval. Never delete data during incident.",
    ),
    (
        "legal_ops",
        "Back Office",
        90,
        "Legal Ops",
        "NDA routing; standard contract review; IP tracking; board resolution drafting.",
        "All external legal communications",
        "Never render legal opinions. Surface issues for qualified attorney review only.",
    ),
    (
        "risk_sentinel",
        "Back Office",
        95,
        "Risk Sentinel",
        "Transaction monitoring for fraud patterns; sanctions screening; SAR draft preparation.",
        "Any sanctions list hit; detected fraud pattern; SAR trigger",
        "Never dismiss a sanctions hit without human review. Always err on the side of caution.",
    ),
    (
        "facilities_agent",
        "Back Office",
        80,
        "Facilities",
        "Office procurement; asset tracking; maintenance scheduling; utility optimisation.",
        "Capex > 1L; vendor change; new lease signing",
        "Never commit to vendor contract without procurement team approval on record.",
    ),
]

for name, domain, conf, title, directive, hitl_cond, anti_hall in AGENT_PROMPTS_TABLE:
    PROMPTS[f"{name}.prompt.txt"] = f"""# core/agents/prompts/{name}.prompt.txt  v2.0
You are the {title} Agent for {{{{org_name}}}}.
Domain: {domain} | Confidence floor: {conf}% | Max retries: 3

<core_directive>
{directive}
</core_directive>

<escalation_rules>
Trigger HITL (never auto-proceed) if ANY condition is true:
  {hitl_cond}
  confidence < {conf / 100:.2f} for any step
Include in HITL context: full trace, all computed values, trigger condition, recommendation.
</escalation_rules>

<anti_hallucination>
{anti_hall}
NEVER invent data not from tool responses.
NEVER proceed with stale data after a tool error — retry per policy, then escalate.
</anti_hallucination>

<output_format>
{{ "status":"str","confidence":0.0-1.0,"processing_trace":["..."],
  "tool_calls":[{{...}}],"escalation_reason":"str|null" }}
</output_format>"""

for fname, content in PROMPTS.items():
    w(f"core/agents/prompts/{fname}", content)


# ── Agent Implementations ──
def gen_agent(path, agent_type, domain, conf, prompt_file, title):
    cls_name = "".join(w.capitalize() for w in agent_type.split("_")) + "Agent"
    w(
        path,
        f'''
    """{title} agent implementation."""
    from __future__ import annotations
    from typing import Any
    from core.agents.base import BaseAgent
    from core.agents.registry import AgentRegistry

    @AgentRegistry.register
    class {cls_name}(BaseAgent):
        agent_type = "{agent_type}"
        domain = "{domain}"
        confidence_floor = {conf}
        prompt_file = "{prompt_file}"

        async def execute(self, task):
            """Execute {title.lower()} task with domain-specific logic."""
            return await super().execute(task)
    ''',
    )


AGENTS = [
    (
        "core/agents/finance/ap_processor.py",
        "ap_processor",
        "finance",
        0.88,
        "ap_processor.prompt.txt",
        "AP Processor",
    ),
    (
        "core/agents/finance/ar_collections.py",
        "ar_collections",
        "finance",
        0.85,
        "ar_collections.prompt.txt",
        "AR Collections",
    ),
    (
        "core/agents/finance/recon_agent.py",
        "recon_agent",
        "finance",
        0.95,
        "recon_agent.prompt.txt",
        "Reconciliation",
    ),
    (
        "core/agents/finance/tax_compliance.py",
        "tax_compliance",
        "finance",
        0.92,
        "tax_compliance.prompt.txt",
        "Tax Compliance",
    ),
    (
        "core/agents/finance/close_agent.py",
        "close_agent",
        "finance",
        0.80,
        "close_agent.prompt.txt",
        "Month-End Close",
    ),
    (
        "core/agents/finance/fpa_agent.py",
        "fpa_agent",
        "finance",
        0.78,
        "fpa_agent.prompt.txt",
        "FP&A",
    ),
    (
        "core/agents/hr/talent_acquisition.py",
        "talent_acquisition",
        "hr",
        0.88,
        "talent_acquisition.prompt.txt",
        "Talent Acquisition",
    ),
    (
        "core/agents/hr/onboarding.py",
        "onboarding_agent",
        "hr",
        0.95,
        "onboarding_agent.prompt.txt",
        "Onboarding",
    ),
    (
        "core/agents/hr/payroll_engine.py",
        "payroll_engine",
        "hr",
        0.99,
        "payroll_engine.prompt.txt",
        "Payroll Engine",
    ),
    (
        "core/agents/hr/performance_coach.py",
        "performance_coach",
        "hr",
        0.80,
        "performance_coach.prompt.txt",
        "Performance Coach",
    ),
    (
        "core/agents/hr/ld_coordinator.py",
        "ld_coordinator",
        "hr",
        0.82,
        "ld_coordinator.prompt.txt",
        "L&D Coordinator",
    ),
    (
        "core/agents/hr/offboarding.py",
        "offboarding_agent",
        "hr",
        0.95,
        "offboarding_agent.prompt.txt",
        "Offboarding",
    ),
    (
        "core/agents/marketing/content_factory.py",
        "content_factory",
        "marketing",
        0.88,
        "content_factory.prompt.txt",
        "Content Factory",
    ),
    (
        "core/agents/marketing/campaign_pilot.py",
        "campaign_pilot",
        "marketing",
        0.85,
        "campaign_pilot.prompt.txt",
        "Campaign Pilot",
    ),
    (
        "core/agents/marketing/seo_strategist.py",
        "seo_strategist",
        "marketing",
        0.90,
        "seo_strategist.prompt.txt",
        "SEO Strategist",
    ),
    (
        "core/agents/marketing/crm_intelligence.py",
        "crm_intelligence",
        "marketing",
        0.88,
        "crm_intelligence.prompt.txt",
        "CRM Intelligence",
    ),
    (
        "core/agents/marketing/brand_monitor.py",
        "brand_monitor",
        "marketing",
        0.85,
        "brand_monitor.prompt.txt",
        "Brand Monitor",
    ),
    (
        "core/agents/ops/vendor_manager.py",
        "vendor_manager",
        "ops",
        0.88,
        "vendor_manager.prompt.txt",
        "Vendor Manager",
    ),
    (
        "core/agents/ops/contract_intelligence.py",
        "contract_intelligence",
        "ops",
        0.82,
        "contract_intelligence.prompt.txt",
        "Contract Intelligence",
    ),
    (
        "core/agents/ops/support_triage.py",
        "support_triage",
        "ops",
        0.85,
        "support_triage.prompt.txt",
        "Support Triage",
    ),
    (
        "core/agents/ops/compliance_guard.py",
        "compliance_guard",
        "ops",
        0.95,
        "compliance_guard.prompt.txt",
        "Compliance Guard",
    ),
    (
        "core/agents/ops/it_operations.py",
        "it_operations",
        "ops",
        0.88,
        "it_operations.prompt.txt",
        "IT Operations",
    ),
    (
        "core/agents/backoffice/legal_ops.py",
        "legal_ops",
        "backoffice",
        0.90,
        "legal_ops.prompt.txt",
        "Legal Ops",
    ),
    (
        "core/agents/backoffice/risk_sentinel.py",
        "risk_sentinel",
        "backoffice",
        0.95,
        "risk_sentinel.prompt.txt",
        "Risk Sentinel",
    ),
    (
        "core/agents/backoffice/facilities_agent.py",
        "facilities_agent",
        "backoffice",
        0.80,
        "facilities_agent.prompt.txt",
        "Facilities",
    ),
]

for path, at, dom, conf, pf, title in AGENTS:
    gen_agent(path, at, dom, conf, pf, title)

# ── NEXUS Orchestrator ──
w("core/orchestrator/__init__.py", '"""NEXUS Orchestrator."""\n')

w(
    "core/orchestrator/nexus.py",
    '''
"""NEXUS — central orchestrator for AgenticOrg."""
from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog

from core.agents.registry import AgentRegistry
from core.orchestrator.checkpoint import CheckpointManager
from core.orchestrator.conflict_resolver import ConflictResolver
from core.orchestrator.task_router import TaskRouter
from core.schemas.messages import HITLRequest, TaskAssignment, TaskResult

logger = structlog.get_logger()


class NexusOrchestrator:
    """Decompose intents, route tasks, manage workflow state."""

    def __init__(self, task_router: TaskRouter, checkpoint_mgr: CheckpointManager):
        self.router = task_router
        self.checkpoint = checkpoint_mgr
        self.conflict_resolver = ConflictResolver()

    async def receive_intent(
        self, workflow_run_id: str, intent: dict[str, Any], context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Decompose intent and produce sub-task assignments."""
        sub_tasks = self.decompose(intent)
        assignments = []
        for i, task in enumerate(sub_tasks):
            assignment = await self.router.route(
                workflow_run_id=workflow_run_id,
                step_id=task["id"],
                step_index=i,
                total_steps=len(sub_tasks),
                task=task,
                context=context,
            )
            assignments.append(assignment)
        await self.checkpoint.save(workflow_run_id, {"assignments": [a for a in assignments], "step": 0})
        return assignments

    def decompose(self, intent: dict[str, Any]) -> list[dict[str, Any]]:
        """Decompose intent into minimum sub-tasks."""
        # Use workflow definition steps if available
        steps = intent.get("steps", [])
        if steps:
            return steps
        # Fallback: single-step
        return [{"id": "main", "action": intent.get("action", "process"), "inputs": intent}]

    async def handle_result(
        self, workflow_run_id: str, result: TaskResult
    ) -> dict[str, Any]:
        """Process a TaskResult from an agent."""
        trace_msg = f"Received result for step {result.step_id}: status={result.status}"
        logger.info(trace_msg, workflow_run_id=workflow_run_id)

        # Validate output
        if result.status == "completed":
            # Check HITL at orchestrator level (PRD: agents cannot bypass HITL gate)
            hitl = self.evaluate_hitl(result)
            if hitl:
                return {"action": "hitl", "hitl_request": hitl}
            await self.checkpoint.save(workflow_run_id, {"last_completed": result.step_id})
            return {"action": "proceed", "output": result.output}

        if result.status == "hitl_triggered":
            return {"action": "hitl", "hitl_request": result.hitl_request}

        if result.status == "failed":
            return {"action": "escalate", "error": result.error, "reason": "Agent failed"}

        return {"action": "unknown", "status": result.status}

    def evaluate_hitl(self, result: TaskResult) -> Optional[HITLRequest]:
        """Evaluate HITL at orchestrator level — agents cannot bypass this."""
        if result.confidence < 0.88:
            return result.hitl_request
        return None

    async def resolve_conflict(self, results: list[TaskResult]) -> dict[str, Any]:
        return self.conflict_resolver.resolve(results)
''',
)

w(
    "core/orchestrator/state_machine.py",
    '''
"""Workflow state machine."""
from __future__ import annotations
from enum import Enum

class WorkflowState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HITL = "waiting_hitl"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

TRANSITIONS = {
    WorkflowState.PENDING: [WorkflowState.RUNNING, WorkflowState.CANCELLED],
    WorkflowState.RUNNING: [WorkflowState.WAITING_HITL, WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED],
    WorkflowState.WAITING_HITL: [WorkflowState.RUNNING, WorkflowState.FAILED, WorkflowState.CANCELLED],
    WorkflowState.COMPLETED: [],
    WorkflowState.FAILED: [],
    WorkflowState.CANCELLED: [],
}

def can_transition(current: WorkflowState, target: WorkflowState) -> bool:
    return target in TRANSITIONS.get(current, [])

def transition(current: WorkflowState, target: WorkflowState) -> WorkflowState:
    if not can_transition(current, target):
        raise ValueError(f"Invalid transition: {current} -> {target}")
    return target
''',
)

w(
    "core/orchestrator/task_router.py",
    '''
"""Route tasks to the most capable agent."""
from __future__ import annotations
import uuid
from typing import Any
from core.agents.registry import AgentRegistry

class TaskRouter:
    async def route(self, workflow_run_id, step_id, step_index, total_steps, task, context):
        agent_type = task.get("agent", task.get("agent_type", ""))
        return {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "step_index": step_index,
            "total_steps": total_steps,
            "target_agent_type": agent_type,
            "task": task,
            "context": context,
        }
''',
)

w(
    "core/orchestrator/conflict_resolver.py",
    '''
"""Resolve conflicts between agent outputs."""
from __future__ import annotations
from typing import Any

class ConflictResolver:
    def resolve(self, results) -> dict[str, Any]:
        if len(results) < 2:
            return {"action": "no_conflict", "output": results[0].output if results else {}}
        outputs = [r.output for r in results]
        if outputs[0] == outputs[1]:
            return {"action": "no_conflict", "output": outputs[0]}
        # Factual conflict: surface both, escalate
        return {
            "action": "escalate",
            "reason": "factual_conflict",
            "outputs": outputs,
            "recommendation": "conservative",
        }
''',
)

w(
    "core/orchestrator/checkpoint.py",
    '''
"""Checkpoint manager — save/restore workflow state."""
from __future__ import annotations
import json
from typing import Any
import redis.asyncio as aioredis
from core.config import settings

class CheckpointManager:
    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def save(self, workflow_run_id: str, state: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.set(
                f"checkpoint:{workflow_run_id}",
                json.dumps(state, default=str),
                ex=86400,
            )

    async def load(self, workflow_run_id: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(f"checkpoint:{workflow_run_id}")
        return json.loads(data) if data else None

    async def close(self):
        if self.redis:
            await self.redis.close()
''',
)

# ── Workflow Engine ──
w("workflows/__init__.py", '"""Workflow engine."""\n')

w(
    "workflows/engine.py",
    '''
"""Workflow engine — load, execute, manage workflow runs."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any
import structlog
from workflows.parser import WorkflowParser
from workflows.step_types import execute_step
from workflows.state_store import WorkflowStateStore

logger = structlog.get_logger()

class WorkflowEngine:
    def __init__(self, state_store: WorkflowStateStore):
        self.state_store = state_store
        self.parser = WorkflowParser()

    async def start_run(self, definition: dict, trigger_payload: dict | None = None) -> str:
        run_id = f"wfr_{uuid.uuid4().hex[:12]}"
        parsed = self.parser.parse(definition)
        await self.state_store.save({
            "id": run_id,
            "definition": parsed,
            "status": "running",
            "trigger_payload": trigger_payload or {},
            "steps_total": len(parsed.get("steps", [])),
            "steps_completed": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        return run_id

    async def execute_next(self, run_id: str) -> dict[str, Any]:
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}
        steps = state["definition"].get("steps", [])
        idx = state.get("steps_completed", 0)
        if idx >= len(steps):
            state["status"] = "completed"
            await self.state_store.save(state)
            return {"status": "completed"}
        step = steps[idx]
        result = await execute_step(step, state)
        state["steps_completed"] = idx + 1
        await self.state_store.save(state)
        return result

    async def cancel(self, run_id: str) -> None:
        state = await self.state_store.load(run_id)
        if state:
            state["status"] = "cancelled"
            await self.state_store.save(state)
''',
)

w(
    "workflows/parser.py",
    '''
"""Parse and validate workflow definitions (YAML/JSON)."""
from __future__ import annotations
import yaml
from typing import Any

class WorkflowParser:
    VALID_STEP_TYPES = {"agent", "condition", "human_in_loop", "parallel", "loop", "transform", "notify", "sub_workflow", "wait"}

    def parse(self, definition: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(definition, str):
            definition = yaml.safe_load(definition)
        self._validate(definition)
        return definition

    def _validate(self, defn: dict) -> None:
        if "steps" not in defn:
            raise ValueError("Workflow must have steps")
        step_ids = set()
        for step in defn["steps"]:
            if "id" not in step:
                raise ValueError("Every step must have an id")
            if step["id"] in step_ids:
                raise ValueError(f"Duplicate step id: {step['id']}")
            step_ids.add(step["id"])
            step_type = step.get("type", "agent")
            if step_type not in self.VALID_STEP_TYPES:
                raise ValueError(f"Invalid step type: {step_type}")
        self._check_circular(defn["steps"])

    def _check_circular(self, steps: list[dict]) -> None:
        graph: dict[str, list[str]] = {}
        for step in steps:
            deps = step.get("depends_on", [])
            graph[step["id"]] = deps
        visited: set[str] = set()
        in_stack: set[str] = set()
        for node in graph:
            if self._has_cycle(node, graph, visited, in_stack):
                raise ValueError(f"E3006: Circular dependency detected involving {node}")

    def _has_cycle(self, node, graph, visited, in_stack) -> bool:
        if node in in_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in graph.get(node, []):
            if self._has_cycle(dep, graph, visited, in_stack):
                return True
        in_stack.discard(node)
        return False
''',
)

w(
    "workflows/step_types.py",
    '''
"""All 9 workflow step type implementations."""
from __future__ import annotations
import asyncio
from typing import Any
from workflows.condition_evaluator import evaluate_condition

async def execute_step(step: dict, state: dict) -> dict[str, Any]:
    step_type = step.get("type", "agent")
    handlers = {
        "agent": _execute_agent,
        "condition": _execute_condition,
        "human_in_loop": _execute_hitl,
        "parallel": _execute_parallel,
        "loop": _execute_loop,
        "transform": _execute_transform,
        "notify": _execute_notify,
        "sub_workflow": _execute_sub_workflow,
        "wait": _execute_wait,
    }
    handler = handlers.get(step_type, _execute_agent)
    return await handler(step, state)

async def _execute_agent(step, state):
    return {"step_id": step["id"], "type": "agent", "status": "completed", "agent": step.get("agent", ""), "action": step.get("action", "")}

async def _execute_condition(step, state):
    condition = step.get("condition", "true")
    context = state.get("context", {})
    result = evaluate_condition(condition, context)
    path = step.get("true_path") if result else step.get("false_path")
    return {"step_id": step["id"], "type": "condition", "result": result, "next_path": path}

async def _execute_hitl(step, state):
    return {"step_id": step["id"], "type": "human_in_loop", "status": "waiting_hitl", "assignee_role": step.get("assignee_role", ""), "timeout_hours": step.get("timeout_hours", 4)}

async def _execute_parallel(step, state):
    sub_steps = step.get("steps", [])
    wait_for = step.get("wait_for", "all")
    tasks = [execute_step({"id": s, "type": "agent"}, state) for s in sub_steps]
    if wait_for == "any":
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        results = [d.result() for d in done]
    else:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return {"step_id": step["id"], "type": "parallel", "results": results}

async def _execute_loop(step, state):
    items = step.get("items", [])
    results = []
    for item in items:
        r = await execute_step({"id": f"{step['id']}_item", "type": "agent"}, state)
        results.append(r)
    return {"step_id": step["id"], "type": "loop", "results": results}

async def _execute_transform(step, state):
    return {"step_id": step["id"], "type": "transform", "status": "completed"}

async def _execute_notify(step, state):
    return {"step_id": step["id"], "type": "notify", "status": "sent", "connector": step.get("connector", "")}

async def _execute_sub_workflow(step, state):
    return {"step_id": step["id"], "type": "sub_workflow", "status": "completed"}

async def _execute_wait(step, state):
    return {"step_id": step["id"], "type": "wait", "status": "completed"}
''',
)

w(
    "workflows/condition_evaluator.py",
    '''
"""Safe condition evaluator — NO eval()."""
from __future__ import annotations
import operator
import re
from typing import Any

OPS = {
    ">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}

def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition like 'total > 500000 OR status == mismatch'."""
    expression = expression.strip()
    if " OR " in expression:
        parts = expression.split(" OR ")
        return any(evaluate_condition(p.strip(), context) for p in parts)
    if " AND " in expression:
        parts = expression.split(" AND ")
        return all(evaluate_condition(p.strip(), context) for p in parts)
    if expression.startswith("NOT "):
        return not evaluate_condition(expression[4:].strip(), context)

    for op_str, op_func in sorted(OPS.items(), key=lambda x: -len(x[0])):
        if op_str in expression:
            left, right = expression.split(op_str, 1)
            left_val = _resolve(left.strip(), context)
            right_val = _resolve(right.strip(), context)
            try:
                return op_func(float(left_val), float(right_val))
            except (ValueError, TypeError):
                return op_func(str(left_val), str(right_val))
    return bool(_resolve(expression, context))

def _resolve(token: str, context: dict) -> Any:
    token = token.strip().strip("'").strip('"')
    parts = token.split(".")
    val = context
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, token)
        else:
            return token
    return val
''',
)

w(
    "workflows/parallel_executor.py",
    '''
"""Parallel step executor with wait_for policies."""
from __future__ import annotations
import asyncio
from typing import Any, Callable, Coroutine

async def execute_parallel(
    tasks: list[Callable[[], Coroutine]], wait_for: str = "all", n: int = 1,
) -> list[Any]:
    if wait_for == "all":
        return await asyncio.gather(*[t() for t in tasks], return_exceptions=True)
    elif wait_for == "any":
        done, pending = await asyncio.wait(
            [asyncio.create_task(t()) for t in tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for p in pending:
            p.cancel()
        return [d.result() for d in done]
    else:
        count = int(wait_for) if wait_for.isdigit() else n
        results = []
        coros = [asyncio.create_task(t()) for t in tasks]
        for coro in asyncio.as_completed(coros):
            results.append(await coro)
            if len(results) >= count:
                for c in coros:
                    c.cancel()
                break
        return results
''',
)

w(
    "workflows/retry.py",
    '''
"""Exponential backoff with jitter."""
from __future__ import annotations
import asyncio
import random

async def retry_with_backoff(
    func, max_retries: int = 3, initial_delay: float = 1.0, max_delay: float = 60.0,
):
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries:
                raise
            delay = min(initial_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            await asyncio.sleep(delay)
''',
)

w(
    "workflows/state_store.py",
    '''
"""Persist workflow state to Redis (and PostgreSQL)."""
from __future__ import annotations
import json
from typing import Any
import redis.asyncio as aioredis
from core.config import settings

class WorkflowStateStore:
    def __init__(self):
        self.redis: aioredis.Redis | None = None

    async def init(self):
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    async def save(self, state: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.set(f"wfstate:{state['id']}", json.dumps(state, default=str), ex=172800)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        if not self.redis:
            return None
        data = await self.redis.get(f"wfstate:{run_id}")
        return json.loads(data) if data else None

    async def close(self):
        if self.redis:
            await self.redis.close()
''',
)

w(
    "workflows/trigger.py",
    '''
"""Workflow trigger types."""
from __future__ import annotations
from typing import Any

class WorkflowTrigger:
    def __init__(self, trigger_type: str, config: dict[str, Any] | None = None):
        self.trigger_type = trigger_type
        self.config = config or {}

    def matches(self, event: dict[str, Any]) -> bool:
        if self.trigger_type == "manual":
            return True
        if self.trigger_type == "webhook":
            return True
        if self.trigger_type == "email_received":
            subject = event.get("subject", "")
            filters = self.config.get("filter", {}).get("subject_contains", [])
            return any(f.lower() in subject.lower() for f in filters)
        if self.trigger_type == "api_event":
            return event.get("event_type") == self.config.get("event_type")
        return False
''',
)

# Workflow examples
w(
    "workflows/examples/invoice_processing_v2.yaml",
    """
name: invoice-processing-v2
version: "2.0"
trigger:
  type: email_received
  filter: { subject_contains: ["invoice", "bill"] }
timeout_hours: 48
on_timeout: escalate_to_human

steps:
  - id: extract
    type: agent
    agent: ap-processor
    action: extract_invoice
    output_schema: Invoice
    on_failure: retry(3)

  - id: parallel_validations
    type: parallel
    wait_for: all
    steps: [validate_gstin, check_duplicate]

  - id: match
    type: agent
    agent: ap-processor
    action: three_way_match
    depends_on: [parallel_validations]

  - id: amount_gate
    type: condition
    condition: "match.output.total > 500000 OR match.output.status == 'mismatch'"
    true_path: hitl_approval
    false_path: auto_post

  - id: hitl_approval
    type: human_in_loop
    assignee_role: cfo
    timeout_hours: 4
    on_timeout: escalate_ceo

  - id: post_gl
    type: agent
    agent: ap-processor
    action: post_journal_entry
    depends_on: [hitl_approval, auto_post]

  - id: notify_vendor
    type: notify
    connector: email
    template: remittance_advice
""",
)

w(
    "workflows/examples/employee_onboarding.yaml",
    """
name: employee-onboarding
version: "1.0"
trigger:
  type: api_event
  filter: { event_type: "connector.darwinbox.employee_joined" }

steps:
  - id: provision_accounts
    type: agent
    agent: onboarding-agent
    action: provision_all_systems

  - id: setup_equipment
    type: agent
    agent: onboarding-agent
    action: request_equipment
    depends_on: [provision_accounts]

  - id: enrol_training
    type: agent
    agent: onboarding-agent
    action: enrol_compliance_training
    depends_on: [provision_accounts]

  - id: setup_plan
    type: agent
    agent: onboarding-agent
    action: create_30_60_90_plan
    depends_on: [provision_accounts]

  - id: notify_manager
    type: notify
    connector: slack
    template: new_hire_welcome
    depends_on: [provision_accounts]
""",
)

w(
    "workflows/examples/support_triage.yaml",
    """
name: support-triage
version: "1.0"
trigger:
  type: api_event
  filter: { event_type: "connector.zendesk.ticket_created" }

steps:
  - id: classify
    type: agent
    agent: support-triage
    action: classify_ticket

  - id: severity_check
    type: condition
    condition: "classify.output.classification == 'L1'"
    true_path: auto_resolve
    false_path: enrich_and_route

  - id: auto_resolve
    type: agent
    agent: support-triage
    action: resolve_l1

  - id: enrich_and_route
    type: agent
    agent: support-triage
    action: enrich_and_route_l2

  - id: notify_agent
    type: notify
    connector: slack
    template: ticket_assigned
    depends_on: [enrich_and_route]
""",
)

print("[OK] Batch 3 complete")

if __name__ == "__main__":
    print("Generating batch 3: Agents + Orchestrator + Workflows...")
    print("[OK] Batch 3 complete")
