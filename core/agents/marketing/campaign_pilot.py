"""Campaign Pilot agent implementation."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry
from core.schemas.messages import (
    DecisionOption,
    DecisionRequired,
    HITLAssignee,
    HITLContext,
    HITLRequest,
    ToolCallRecord,
)

logger = structlog.get_logger()

# ROAS thresholds
ROAS_PAUSE_THRESHOLD = 1.0  # Pause channel if ROAS < 1.0
ROAS_SCALE_THRESHOLD = 3.0  # Scale channel if ROAS > 3.0
# Budget overspend alert
BUDGET_OVERSPEND_PCT = 10.0
# Default channel allocation weights
DEFAULT_CHANNEL_ALLOCATION = {
    "google_ads": 0.35,
    "facebook_ads": 0.25,
    "linkedin_ads": 0.15,
    "email": 0.15,
    "content": 0.10,
}
# HITL for high-budget campaigns
HITL_BUDGET_THRESHOLD = 500_000  # INR 5 lakhs


@AgentRegistry.register
class CampaignPilotAgent(BaseAgent):
    agent_type = "campaign_pilot"
    domain = "marketing"
    confidence_floor = 0.85
    prompt_file = "campaign_pilot.prompt.txt"

    async def execute(self, task):
        """Create campaign, allocate budget, monitor spend vs budget, pause/scale channels by ROAS."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            action = task.task.action if hasattr(task.task, "action") else task.task.get("action", "manage_campaign")

            campaign_id = inputs.get("campaign_id", "")
            campaign_name = inputs.get("campaign_name", inputs.get("name", ""))
            total_budget = float(inputs.get("budget", 0))
            channels = inputs.get("channels", list(DEFAULT_CHANNEL_ALLOCATION.keys()))
            objective = inputs.get("objective", "lead_generation")
            trace.append(
                f"Campaign: name={campaign_name}, budget={total_budget:,.0f}, "
                f"channels={len(channels)}, objective={objective}"
            )

            # --- Step 1: Get or create campaign structure ---
            if action in ("create", "setup") or not campaign_id:
                channel_budgets = inputs.get("channel_budgets", {})
                if not channel_budgets:
                    # Auto-allocate based on defaults
                    channel_budgets = {}
                    for ch in channels:
                        weight = DEFAULT_CHANNEL_ALLOCATION.get(ch, 1.0 / len(channels))
                        channel_budgets[ch] = round(total_budget * weight, 2)
                trace.append(f"Budget allocated: {channel_budgets}")
            else:
                channel_budgets = inputs.get("channel_budgets", {})

            # --- Step 2: Fetch current performance per channel ---
            channel_performance: dict[str, dict] = {}
            for channel in channels:
                connector_map = {
                    "google_ads": ("google_ads", "get_campaign_performance"),
                    "facebook_ads": ("facebook_ads", "get_campaign_insights"),
                    "linkedin_ads": ("linkedin_ads", "get_campaign_analytics"),
                    "email": ("sendgrid", "get_campaign_stats"),
                    "content": ("google_analytics", "get_page_performance"),
                }
                connector, tool = connector_map.get(channel, ("google_analytics", "get_performance"))

                perf_result = await self._safe_tool_call(
                    connector, tool,
                    {"campaign_id": campaign_id, "campaign_name": campaign_name},
                    trace, tool_calls,
                )

                if perf_result and "error" not in perf_result:
                    spend = float(perf_result.get("spend", perf_result.get("cost", 0)))
                    revenue = float(perf_result.get("revenue", perf_result.get("conversion_value", 0)))
                    impressions = int(perf_result.get("impressions", 0))
                    clicks = int(perf_result.get("clicks", 0))
                    conversions = int(perf_result.get("conversions", perf_result.get("leads", 0)))

                    roas = revenue / spend if spend > 0 else 0
                    ctr = clicks / impressions * 100 if impressions > 0 else 0
                    cpc = spend / clicks if clicks > 0 else 0
                    cpa = spend / conversions if conversions > 0 else 0

                    channel_performance[channel] = {
                        "spend": round(spend, 2),
                        "budget": channel_budgets.get(channel, 0),
                        "revenue": round(revenue, 2),
                        "impressions": impressions,
                        "clicks": clicks,
                        "conversions": conversions,
                        "roas": round(roas, 2),
                        "ctr": round(ctr, 2),
                        "cpc": round(cpc, 2),
                        "cpa": round(cpa, 2),
                        "budget_utilization": round(
                            spend / channel_budgets.get(channel, 1) * 100, 1
                        ) if channel_budgets.get(channel) else 0,
                    }
                else:
                    channel_performance[channel] = {
                        "spend": 0, "budget": channel_budgets.get(channel, 0),
                        "status": "data_unavailable",
                    }

            trace.append(f"Performance fetched for {len(channel_performance)} channels")

            # --- Step 3: Optimize — pause underperformers, scale winners ---
            actions_taken: list[dict] = []
            channels_paused: list[str] = []
            channels_scaled: list[str] = []

            for channel, perf in channel_performance.items():
                roas = perf.get("roas", 0)
                spend = perf.get("spend", 0)
                budget = perf.get("budget", 0)

                if roas > 0 and roas < ROAS_PAUSE_THRESHOLD and spend > 0:
                    # Pause underperforming channel
                    connector_map = {
                        "google_ads": "google_ads",
                        "facebook_ads": "facebook_ads",
                        "linkedin_ads": "linkedin_ads",
                    }
                    if channel in connector_map:
                        pause_result = await self._safe_tool_call(
                            connector_map[channel], "pause_campaign",
                            {"campaign_id": campaign_id, "campaign_name": campaign_name},
                            trace, tool_calls,
                        )
                        paused = pause_result and "error" not in pause_result
                        actions_taken.append({
                            "channel": channel,
                            "action": "paused",
                            "reason": f"ROAS {roas:.2f} < {ROAS_PAUSE_THRESHOLD}",
                            "success": paused,
                        })
                        if paused:
                            channels_paused.append(channel)

                elif roas >= ROAS_SCALE_THRESHOLD:
                    # Scale winning channel by reallocating budget from paused channels
                    scale_amount = round(budget * 0.25, 2)  # Increase by 25%
                    if channel in ("google_ads", "facebook_ads", "linkedin_ads"):
                        scale_result = await self._safe_tool_call(
                            channel, "update_budget",
                            {
                                "campaign_id": campaign_id,
                                "new_budget": round(budget + scale_amount, 2),
                            },
                            trace, tool_calls,
                        )
                        scaled = scale_result and "error" not in scale_result
                        actions_taken.append({
                            "channel": channel,
                            "action": "scaled_up",
                            "reason": f"ROAS {roas:.2f} > {ROAS_SCALE_THRESHOLD}",
                            "budget_increase": scale_amount,
                            "success": scaled,
                        })
                        if scaled:
                            channels_scaled.append(channel)

                # Check budget overspend
                if budget > 0 and spend > budget * (1 + BUDGET_OVERSPEND_PCT / 100):
                    actions_taken.append({
                        "channel": channel,
                        "action": "overspend_alert",
                        "reason": f"spend {spend:,.0f} > budget {budget:,.0f} by {((spend/budget)-1)*100:.1f}%",
                    })

            trace.append(
                f"Optimization: paused={channels_paused}, scaled={channels_scaled}, "
                f"total_actions={len(actions_taken)}"
            )

            # --- Step 4: Aggregate summary ---
            total_spend = sum(p.get("spend", 0) for p in channel_performance.values())
            total_revenue = sum(p.get("revenue", 0) for p in channel_performance.values())
            total_conversions = sum(p.get("conversions", 0) for p in channel_performance.values())
            overall_roas = total_revenue / total_spend if total_spend > 0 else 0
            budget_utilization = total_spend / total_budget * 100 if total_budget > 0 else 0

            summary = {
                "total_budget": total_budget,
                "total_spend": round(total_spend, 2),
                "total_revenue": round(total_revenue, 2),
                "total_conversions": total_conversions,
                "overall_roas": round(overall_roas, 2),
                "budget_utilization_pct": round(budget_utilization, 1),
            }

            # --- Step 5: Compute confidence ---
            channels_with_data = sum(1 for p in channel_performance.values() if p.get("spend", 0) > 0)
            factors: list[float] = []
            factors.append(
                min(0.5 + channels_with_data / max(len(channels), 1) * 0.5, 0.95)
            )
            action_success_rate = (
                sum(1 for a in actions_taken if a.get("success", True))
                / len(actions_taken) if actions_taken else 1.0
            )
            factors.append(0.5 + action_success_rate * 0.5)
            factors.append(0.90 if overall_roas >= 1.0 else 0.70)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: HITL ---
            hitl_reasons: list[str] = []
            if total_budget > HITL_BUDGET_THRESHOLD and action in ("create", "setup"):
                hitl_reasons.append(f"high-budget campaign INR {total_budget:,.0f}")
            if overall_roas > 0 and overall_roas < 0.5:
                hitl_reasons.append(f"overall ROAS {overall_roas:.2f} critically low")
            if budget_utilization > 100 + BUDGET_OVERSPEND_PCT:
                hitl_reasons.append(f"budget overspent by {budget_utilization - 100:.1f}%")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "optimized",
                "campaign_name": campaign_name,
                "campaign_id": campaign_id,
                "summary": summary,
                "channel_performance": channel_performance,
                "channel_budgets": channel_budgets,
                "actions_taken": actions_taken,
                "channels_paused": channels_paused,
                "channels_scaled": channels_scaled,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="campaign_review",
                    decision_required=DecisionRequired(
                        question=f"Campaign '{campaign_name}': {'; '.join(hitl_reasons)}. Approve actions?",
                        options=[
                            DecisionOption(id="approve", label="Approve optimizations", action="proceed"),
                            DecisionOption(id="pause_all", label="Pause entire campaign", action="pause"),
                            DecisionOption(id="adjust", label="Adjust strategy", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary="Campaign optimization review",
                        recommendation="approve" if overall_roas >= 1.0 else "adjust",
                        agent_confidence=confidence,
                        supporting_data={
                            "overall_roas": round(overall_roas, 2),
                            "budget_utilization": round(budget_utilization, 1),
                            "channels_paused": channels_paused,
                        },
                    ),
                    assignee=HITLAssignee(role="marketing_head", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("campaign_pilot_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "CAMPAIGN_ERR", "message": str(e)}, start=start,
            )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        call_start = time.monotonic()
        try:
            result = await self._call_tool(
                connector_name=connector, tool_name=tool, params=params,
            )
            latency = int((time.monotonic() - call_start) * 1000)
            status = "error" if "error" in result else "success"
            trace.append(f"[tool] {connector}.{tool} -> {status} ({latency}ms)")
            tool_records.append(ToolCallRecord(
                tool_name=f"{connector}.{tool}", status=status, latency_ms=latency,
            ))
            return result
        except Exception as exc:
            latency = int((time.monotonic() - call_start) * 1000)
            trace.append(f"[tool] {connector}.{tool} -> exception: {exc} ({latency}ms)")
            tool_records.append(ToolCallRecord(
                tool_name=f"{connector}.{tool}", status="error", latency_ms=latency,
            ))
            return {"error": str(exc)}
