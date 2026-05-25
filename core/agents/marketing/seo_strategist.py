"""SEO Strategist agent implementation.

The SEO Strategist is deterministic and vendor-neutral: it analyzes structured
SEO, analytics, and CMS inputs supplied by connectors or tests, emits
contract-shaped CMO outputs, and fails closed before any technical site write.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.schemas.messages import (
    DecisionOption,
    DecisionRequired,
    HITLAssignee,
    HITLContext,
    HITLRequest,
    ToolCallRecord,
)

logger = structlog.get_logger()

SEO_AGENT_MATURITY = "beta"
SEO_CONFIDENCE_FLOOR = 0.84

READ_ONLY_ACTIONS = {
    "analyze_keyword_gaps",
    "bundle_recommendations",
    "compute_ranking_deltas",
    "content_optimization_recommendation",
    "get_keyword_rankings",
    "identify_content_gaps",
    "keyword_gap_analysis",
    "optimize_content",
    "plan_seo_sprint",
    "prioritize_technical_issues",
    "ranking_delta_computation",
    "recommendation_bundling",
    "seo_sprint_planning",
    "technical_issue_prioritization",
}
WRITE_ACTIONS = {
    "apply_redirect",
    "publish_landing_page",
    "publish_seo_change",
    "publish_to_wordpress",
    "submit_url_to_index",
    "update_canonical_tag",
    "update_landing_page",
    "update_page_metadata",
    "update_robots_txt",
    "update_sitemap",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

SEVERITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
EFFORT_WEIGHTS = {"low": 1, "medium": 2, "high": 3}


@AgentRegistry.register
class SeoStrategistAgent(BaseAgent):
    agent_type = "seo_strategist"
    domain = "marketing"
    confidence_floor = SEO_CONFIDENCE_FLOOR
    prompt_file = "seo_strategist.prompt.txt"

    async def execute(self, task):
        """Run deterministic SEO analysis or guarded technical site writes."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"SEO Strategist action={action}, mode={mode}")

            if action in READ_ONLY_ACTIONS:
                output = self._read_only_action(task, action, inputs, mode, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_site_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported SEO Strategist action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_seo_action",
                        "reason": "SEO Strategist only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported SEO Strategist action '{action}'."],
                policy_context={"workflow_mode": mode},
            )
            return self._make_result(
                task,
                msg_id,
                "failed",
                output,
                0.0,
                trace,
                tool_calls,
                error={"code": "SEO_STRATEGIST_ACTION_UNSUPPORTED", "message": output["blocked_reasons"][0]},
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("seo_strategist_agent_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "SEO_STRATEGIST_AGENT_ERR", "message": str(exc)},
                start=start,
            )

    def _read_only_action(
        self,
        task,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        trace: list[str],
    ) -> dict[str, Any]:
        keyword_gaps = _keyword_gap_analysis(inputs)
        ranking_deltas = _ranking_deltas(inputs)
        technical_issues = _prioritized_technical_issues(inputs)
        content_recommendations = _content_optimization_recommendations(inputs, keyword_gaps)
        bundled = _bundled_recommendations(keyword_gaps, technical_issues, content_recommendations)
        sprint_plan = _seo_sprint_plan(keyword_gaps, technical_issues, content_recommendations, inputs)
        degraded = _read_degraded_reasons(inputs)
        confidence = _analysis_confidence(
            keyword_gaps,
            ranking_deltas,
            technical_issues,
            content_recommendations,
            degraded,
        )
        source_refs = _source_refs(inputs, keyword_gaps, ranking_deltas, technical_issues)
        summary = {
            "keyword_gaps": keyword_gaps,
            "ranking_deltas": ranking_deltas,
            "technical_issues": technical_issues,
            "recommendation_bundles": bundled,
            "content_recommendations": content_recommendations,
            "sprint_plan": sprint_plan,
        }
        trace.append(
            "SEO analysis gaps="
            f"{len(keyword_gaps)}, deltas={len(ranking_deltas)}, "
            f"issues={len(technical_issues)}, degraded={len(degraded)}"
        )

        status = "seo_analysis_degraded" if degraded else "seo_analysis_ready"
        rationale = "Built deterministic SEO analysis from structured keyword, ranking, site audit, and content inputs."
        extra: dict[str, Any] = {"seo_analysis": summary}
        if action in {"keyword_gap_analysis", "analyze_keyword_gaps", "identify_content_gaps"}:
            status = "keyword_gaps_identified" if not degraded else "keyword_gaps_degraded"
            rationale = "Identified missing and underperforming keyword opportunities against competitors."
            extra.update({"keyword_gaps": keyword_gaps})
        elif action in {"get_keyword_rankings", "compute_ranking_deltas", "ranking_delta_computation"}:
            status = "ranking_deltas_computed" if not degraded else "ranking_deltas_degraded"
            rationale = "Compared current and previous rankings for improvements, drops, new, and lost terms."
            extra.update({"ranking_deltas": ranking_deltas})
        elif action in {"technical_issue_prioritization", "prioritize_technical_issues"}:
            status = "technical_issues_prioritized" if not degraded else "technical_issues_degraded"
            rationale = "Prioritized technical SEO issues by severity, impact, effort, and affected pages."
            extra.update({"technical_issues": technical_issues})
        elif action in {"recommendation_bundling", "bundle_recommendations"}:
            status = "recommendations_bundled"
            rationale = "Bundled SEO recommendations by effort and impact for planning."
            extra.update({"recommendation_bundles": bundled})
        elif action in {"content_optimization_recommendation", "optimize_content"}:
            status = "content_optimization_recommended" if not degraded else "content_optimization_degraded"
            rationale = "Recommended deterministic title, metadata, heading, internal-link, and content improvements."
            extra.update({"content_recommendations": content_recommendations})
        elif action in {"seo_sprint_planning", "plan_seo_sprint"}:
            status = "seo_sprint_planned" if not degraded else "seo_sprint_degraded"
            rationale = "Planned an SEO sprint from prioritized technical, keyword, and content opportunities."
            extra.update({"sprint_plan": sprint_plan})

        return self._base_output(
            task,
            action=action,
            status=status,
            confidence=confidence,
            rationale=rationale,
            recommended_actions=sprint_plan["actions"] or _default_recommendations(degraded),
            policy_context={"workflow_mode": mode, "workflow_id": "seo_sprint"},
            source_refs=source_refs,
            degraded_reasons=degraded,
            extra=extra,
        )

    async def _guarded_site_write(
        self,
        task,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        trace: list[str],
        tool_calls: list[ToolCallRecord],
    ) -> dict[str, Any]:
        policy_context = _policy_context_for_write(action, inputs, mode)
        policy = evaluate_marketing_policy(policy_context)
        escalation = _escalation_for_write(task, action, inputs, policy)
        blocked_reasons: list[str] = []
        hitl_reasons: list[str] = []

        if mode in SHADOW_MODES:
            blocked_reasons.append("Shadow/draft/internal SEO workflows are read-only.")
            trace.append("SEO site write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.76,
                rationale="Prepared technical SEO change recommendation without mutating the site.",
                recommended_actions=[
                    {
                        "action": "create_internal_approval",
                        "reason": "Review the SEO site change before any active external write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons,
                external_write_status="shadow_only",
                external_write_required=True,
                extra={"write_payload": _site_write_payload(action, inputs)},
            )

        write_safe, write_reason = _connector_write_safe(inputs)
        if not write_safe:
            blocked_reasons.append(write_reason)
        if policy.get("decision") in {"requires_approval", "requires_escalation"} and not _has_approval(inputs):
            hitl_reasons.append(str(policy.get("reason") or "Technical SEO site change requires approval."))
        if policy.get("decision") in {"blocked", "missing_policy"}:
            blocked_reasons.append(str(policy.get("reason") or "Marketing policy blocks this SEO site action."))
        escalation_needs_review = escalation.get("decision") not in {None, "", "no_escalation", "notify_owner"}
        if escalation_needs_review and not _has_escalation_approval(inputs):
            hitl_reasons.append("SEO site change requires escalation review.")

        if blocked_reasons or hitl_reasons:
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale="SEO Strategist external site action failed closed before vendor mutation.",
                recommended_actions=[
                    {
                        "action": "resolve_seo_write_prerequisites",
                        "reason": "; ".join(_dedupe(blocked_reasons + hitl_reasons)),
                    }
                ],
                approval_required=bool(hitl_reasons),
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons + hitl_reasons,
                external_write_status="write_unconfirmed",
                external_write_required=True,
                extra={"write_payload": _site_write_payload(action, inputs)},
            )

        result = _dict_or_none(inputs.get("external_write_result"))
        connector, tool = _connector_tool(action, inputs)
        idempotency_key = str(
            inputs.get("idempotency_key")
            or inputs.get("request_fingerprint")
            or f"seo_strategist:{task.correlation_id}:{task.step_id}:{action}"
        )
        if result is None:
            result = await self._safe_tool_call(
                connector,
                tool,
                _site_write_payload(action, inputs),
                trace,
                tool_calls,
                idempotency_key=idempotency_key,
            )

        write_status = _external_write_confirmation_status(result)
        write_confirmed = _is_confirmed_external_write(result)
        external_ref = _external_write_ref(result)
        blocked_reasons = [] if write_confirmed else ["SEO site write is unconfirmed and cannot be marked complete."]
        trace.append(f"SEO site write status={write_status}, confirmed={write_confirmed}")
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "write_unconfirmed",
            confidence=0.9 if write_confirmed else 0.0,
            rationale=(
                "SEO Strategist external site write was confirmed by the connector."
                if write_confirmed
                else "SEO Strategist external site write did not return confirmed vendor evidence."
            ),
            recommended_actions=[
                {
                    "action": "monitor_rankings_after_change" if write_confirmed else "verify_seo_write_before_retry",
                    "reason": (
                        "Track ranking and crawl impact after the confirmed technical change."
                        if write_confirmed
                        else "Check vendor state with the idempotency key before retrying."
                    ),
                }
            ],
            approval_required=False,
            hitl_required=not write_confirmed,
            policy_result=policy,
            approval_satisfied=_has_approval(inputs),
            escalation=escalation,
            blocked_reasons=blocked_reasons,
            external_write_status=write_status,
            external_write_required=True,
            external_write_ref=external_ref,
            extra={"write_payload": _site_write_payload(action, inputs)},
        )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        call_start = time.monotonic()
        try:
            result = await self._call_tool(
                connector_name=connector,
                tool_name=tool,
                params=params,
                idempotency_key=idempotency_key,
            )
            latency = int((time.monotonic() - call_start) * 1000)
            status = "error" if "error" in result else "success"
            trace.append(f"[tool] {connector}.{tool} -> {status} ({latency}ms)")
            tool_records.append(ToolCallRecord(tool_name=f"{connector}.{tool}", status=status, latency_ms=latency))
            return result
        # enterprise-gate: broad-except-ok reason=agent-tool-call-failure-returns-explicit-error-result
        except Exception as exc:
            latency = int((time.monotonic() - call_start) * 1000)
            trace.append(f"[tool] {connector}.{tool} -> exception: {exc} ({latency}ms)")
            tool_records.append(ToolCallRecord(tool_name=f"{connector}.{tool}", status="error", latency_ms=latency))
            return {"error": str(exc)}

    def _base_output(
        self,
        task,
        *,
        action: str,
        status: str,
        confidence: float,
        rationale: str,
        recommended_actions: list[dict[str, Any]],
        approval_required: bool = False,
        hitl_required: bool = False,
        policy_context: dict[str, Any] | None = None,
        policy_result: dict[str, Any] | None = None,
        approval_satisfied: bool = False,
        escalation: dict[str, Any] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        degraded_reasons: list[str] | None = None,
        blocked_reasons: list[str] | None = None,
        external_write_status: str = "not_required",
        external_write_required: bool = False,
        external_write_ref: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = policy_result or evaluate_marketing_policy(
            {
                "workflow_id": "seo_sprint",
                "workflow_mode": "shadow",
                "action": action,
                "external_write_required": external_write_required,
                **(policy_context or {}),
            }
        )
        escalation_ref = None
        if escalation and escalation.get("decision") != "no_escalation":
            escalation_ref = escalation.get("decision_audit_ref") or escalation.get("audit_reference")
        policy_needs_review = (
            policy.get("decision") in {"requires_approval", "requires_escalation", "read_only_only"}
            and not approval_satisfied
        )
        output: dict[str, Any] = {
            "status": status,
            "agent_type": self.agent_type,
            "action": action,
            "confidence": round(max(0.0, min(confidence, 1.0)), 3),
            "rationale": rationale,
            "recommended_actions": recommended_actions,
            "source_refs": source_refs or [],
            "policy_result": policy,
            "policy_ref": policy.get("decision_audit_ref") or policy.get("policy_id"),
            "approval_required": approval_required or policy_needs_review,
            "hitl_required": hitl_required,
            "escalation_ref": escalation_ref,
            "escalation_result": escalation,
            "audit_ref": f"seo_strategist:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": SEO_AGENT_MATURITY,
        }
        if extra:
            output.update(extra)
        return output

    def _complete_or_hitl(
        self,
        task,
        msg_id: str,
        output: dict[str, Any],
        trace: list[str],
        tool_calls: list[ToolCallRecord],
        start: float,
    ):
        if output.get("hitl_required") or output.get("approval_required") or output.get("blocked_reasons"):
            hitl = _hitl_request(task, output)
            return self._make_result(
                task,
                msg_id,
                "hitl_triggered",
                output,
                float(output.get("confidence") or 0.0),
                trace,
                tool_calls,
                hitl_request=hitl,
                start=start,
            )
        return self._make_result(
            task,
            msg_id,
            "completed",
            output,
            float(output.get("confidence") or 0.0),
            trace,
            tool_calls,
            start=start,
        )


def _task_inputs(task) -> dict[str, Any]:
    payload = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
    return payload if isinstance(payload, dict) else {}


def _task_action(task) -> str:
    return str(task.task.action if hasattr(task.task, "action") else task.task.get("action", "keyword_gap_analysis"))


def _normalize_action(action: str) -> str:
    normalized = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "content_gap_analysis": "keyword_gap_analysis",
        "get_keyword_rankings": "ranking_delta_computation",
        "identify_content_gaps": "keyword_gap_analysis",
        "keyword_gaps": "keyword_gap_analysis",
        "optimize_content": "content_optimization_recommendation",
        "ranking_delta": "ranking_delta_computation",
        "ranking_deltas": "ranking_delta_computation",
        "recommendation_bundle": "recommendation_bundling",
        "seo_sprint": "seo_sprint_planning",
        "technical_issue_prioritization": "technical_issue_prioritization",
        "technical_seo_issue_prioritization": "technical_issue_prioritization",
        "update_metadata": "update_page_metadata",
        "technical_site_change": "publish_seo_change",
    }
    return aliases.get(normalized, normalized)


def _keyword_gap_analysis(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    owned = {_keyword_key(row): row for row in _keyword_rows(inputs.get("owned_keywords") or inputs.get("keywords"))}
    current_rows = _keyword_rows(inputs.get("rankings") or inputs.get("current_rankings"))
    current = {_keyword_key(row): row for row in current_rows}
    for key, row in current.items():
        owned.setdefault(key, row)
    competitors = _keyword_rows(inputs.get("competitor_keywords") or inputs.get("target_keywords"))
    target_keywords = _keyword_rows(inputs.get("target_keywords"))
    if not competitors and target_keywords:
        competitors = target_keywords

    opportunities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in competitors:
        keyword = _keyword_key(row)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        owned_row = owned.get(keyword)
        own_rank = _rank_value(owned_row)
        competitor_rank = _rank_value(row)
        missing = owned_row is None or own_rank is None
        underperforming = own_rank is not None and competitor_rank is not None and own_rank - competitor_rank >= 5
        below_page_one = own_rank is not None and own_rank > 10
        if not (missing or underperforming or below_page_one):
            continue
        volume = _float(row.get("volume") or row.get("search_volume"), 0.0)
        difficulty = _float(row.get("difficulty") or row.get("keyword_difficulty"), 50.0)
        if missing:
            rank_gap = 20.0
        else:
            assert own_rank is not None
            rank_gap = max(0.0, own_rank - (competitor_rank or 1.0))
        opportunity_score = round(min(100.0, (volume / 100.0) + rank_gap * 2.0 + max(0.0, 70.0 - difficulty) * 0.6), 2)
        opportunities.append(
            {
                "keyword": keyword,
                "reason": "missing" if missing else ("underperforming" if underperforming else "below_page_one"),
                "own_rank": own_rank,
                "competitor_rank": competitor_rank,
                "search_volume": volume,
                "difficulty": difficulty,
                "intent": str(row.get("intent") or "unknown"),
                "opportunity_score": opportunity_score,
                "recommended_action": "create_or_refresh_content" if missing else "optimize_existing_page",
                "source": row.get("source") or "seo_keyword_input",
            }
        )
    opportunities.sort(key=lambda item: (-item["opportunity_score"], item["difficulty"], item["keyword"]))
    return opportunities


def _ranking_deltas(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    current_rows = _keyword_rows(inputs.get("rankings") or inputs.get("current_rankings"))
    current = {_keyword_key(row): row for row in current_rows}
    previous = {_keyword_key(row): row for row in _keyword_rows(inputs.get("previous_rankings"))}
    rows: list[dict[str, Any]] = []
    for keyword in sorted(set(current) | set(previous)):
        cur = current.get(keyword)
        prev = previous.get(keyword)
        current_rank = _rank_value(cur)
        previous_rank = _rank_value(prev)
        if previous_rank is None and current_rank is not None:
            status = "new"
            delta = None
        elif current_rank is None and previous_rank is not None:
            status = "lost"
            delta = None
        else:
            delta = round(float(previous_rank or 0) - float(current_rank or 0), 2)
            status = "improved" if delta > 0 else ("dropped" if delta < 0 else "unchanged")
        rows.append(
            {
                "keyword": keyword,
                "previous_rank": previous_rank,
                "current_rank": current_rank,
                "delta": delta,
                "status": status,
                "url": (cur or prev or {}).get("url") or (cur or prev or {}).get("page_url"),
                "source": (cur or prev or {}).get("source") or "seo_rank_input",
            }
        )
    status_order = {"dropped": 0, "lost": 1, "new": 2, "improved": 3, "unchanged": 4}
    rows.sort(key=lambda row: (status_order.get(row["status"], 9), row["keyword"]))
    return rows


def _prioritized_technical_issues(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, issue in enumerate(_dict_list(inputs.get("technical_issues") or inputs.get("site_audit_issues"))):
        severity = _severity(issue.get("severity"), "medium")
        impact = _float(issue.get("impact") or issue.get("impact_score"), 50.0)
        effort = _effort_score(issue.get("effort") or issue.get("effort_score"))
        pages = _int(issue.get("affected_pages") or issue.get("pages") or 1, 1)
        priority_score = round(
            SEVERITY_WEIGHTS[severity] * 25.0
            + impact * 0.7
            + min(pages, 100) * 0.1
            - effort * 10.0,
            2,
        )
        rows.append(
            {
                "issue_id": str(issue.get("issue_id") or issue.get("id") or f"issue-{index + 1}"),
                "type": str(issue.get("type") or issue.get("category") or "technical"),
                "severity": severity,
                "impact_score": round(impact, 2),
                "effort": _effort_label(issue.get("effort") or issue.get("effort_score")),
                "affected_pages": pages,
                "priority_score": priority_score,
                "recommended_action": _technical_action(issue),
                "source": issue.get("source") or "site_audit_input",
            }
        )
    rows.sort(key=lambda row: (-row["priority_score"], row["issue_id"]))
    return rows


def _content_optimization_recommendations(
    inputs: dict[str, Any],
    keyword_gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pages = _dict_list(inputs.get("pages") or inputs.get("content_items"))
    target_keywords = _string_list(inputs.get("keywords") or inputs.get("target_keywords"))
    if not target_keywords:
        target_keywords = [str(item["keyword"]) for item in keyword_gaps[:3]]
    if not pages and target_keywords:
        pages = [{"url": inputs.get("url") or "proposed_content", "title": inputs.get("title") or "", "word_count": 0}]
    recommendations: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        keyword = str(
            page.get("target_keyword")
            or (target_keywords[index % len(target_keywords)] if target_keywords else "")
        )
        title = str(page.get("title") or "")
        meta = str(page.get("meta_description") or page.get("meta") or "")
        headings = [item.lower() for item in _string_list(page.get("headings") or page.get("h1") or page.get("h2"))]
        word_count = _int(page.get("word_count") or page.get("words"), 0)
        actions: list[str] = []
        if keyword and keyword.lower() not in title.lower():
            actions.append("add_target_keyword_to_title")
        if keyword and keyword.lower() not in meta.lower():
            actions.append("refresh_meta_description_with_target_keyword")
        if keyword and not any(keyword.lower() in heading for heading in headings):
            actions.append("add_keyword_aligned_h1_or_h2")
        if word_count and word_count < 800:
            actions.append("expand_content_depth")
        if not page.get("internal_links"):
            actions.append("add_internal_links")
        if actions:
            recommendations.append(
                {
                    "url": str(page.get("url") or page.get("page_url") or f"page-{index + 1}"),
                    "target_keyword": keyword,
                    "actions": actions,
                    "impact": "high" if len(actions) >= 3 else "medium",
                    "effort": "low" if len(actions) <= 2 else "medium",
                    "reason": "Page can better satisfy the target search intent.",
                }
            )
    recommendations.sort(key=lambda row: (-len(row["actions"]), row["url"]))
    return recommendations


def _bundled_recommendations(
    keyword_gaps: list[dict[str, Any]],
    technical_issues: list[dict[str, Any]],
    content_recommendations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    bundles: dict[str, list[dict[str, Any]]] = {
        "quick_wins": [],
        "strategic": [],
        "maintenance": [],
        "deprioritized": [],
    }
    for issue in technical_issues:
        item = {
            "type": "technical_issue",
            "action": issue["recommended_action"],
            "impact": "high" if issue["impact_score"] >= 65 or issue["severity"] in {"critical", "high"} else "medium",
            "effort": issue["effort"],
            "priority_score": issue["priority_score"],
        }
        bundles[_bundle_key(item["impact"], item["effort"])].append(item)
    for gap in keyword_gaps:
        item = {
            "type": "keyword_gap",
            "action": gap["recommended_action"],
            "keyword": gap["keyword"],
            "impact": "high" if gap["opportunity_score"] >= 55 else "medium",
            "effort": "medium" if gap["reason"] == "missing" else "low",
            "priority_score": gap["opportunity_score"],
        }
        bundles[_bundle_key(item["impact"], item["effort"])].append(item)
    for rec in content_recommendations:
        item = {
            "type": "content_optimization",
            "action": "optimize_content",
            "url": rec["url"],
            "impact": rec["impact"],
            "effort": rec["effort"],
            "priority_score": len(rec["actions"]) * 20,
        }
        bundles[_bundle_key(item["impact"], item["effort"])].append(item)
    for rows in bundles.values():
        rows.sort(key=lambda row: (-_float(row.get("priority_score"), 0.0), str(row.get("action") or "")))
    return bundles


def _seo_sprint_plan(
    keyword_gaps: list[dict[str, Any]],
    technical_issues: list[dict[str, Any]],
    content_recommendations: list[dict[str, Any]],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    capacity = max(1, min(_int(inputs.get("sprint_capacity") or inputs.get("capacity"), 6), 12))
    actions: list[dict[str, Any]] = []
    for issue in technical_issues[:capacity]:
        actions.append(
            {
                "action": issue["recommended_action"],
                "type": "technical",
                "priority_score": issue["priority_score"],
                "expected_impact": issue["impact_score"],
                "reason": f"{issue['severity']} issue affects {issue['affected_pages']} page(s).",
            }
        )
    for gap in keyword_gaps[:capacity]:
        actions.append(
            {
                "action": gap["recommended_action"],
                "type": "keyword",
                "keyword": gap["keyword"],
                "priority_score": gap["opportunity_score"],
                "expected_impact": gap["search_volume"],
                "reason": f"Keyword is {gap['reason']} with opportunity score {gap['opportunity_score']}.",
            }
        )
    for rec in content_recommendations[:capacity]:
        actions.append(
            {
                "action": "optimize_content",
                "type": "content",
                "url": rec["url"],
                "priority_score": len(rec["actions"]) * 20,
                "expected_impact": rec["impact"],
                "reason": ", ".join(rec["actions"]),
            }
        )
    actions.sort(key=lambda row: (-_float(row.get("priority_score"), 0.0), str(row.get("type") or "")))
    actions = actions[:capacity]
    return {
        "capacity": capacity,
        "actions": actions,
        "expected_impact": {
            "technical_fixes": sum(1 for row in actions if row["type"] == "technical"),
            "keyword_opportunities": sum(1 for row in actions if row["type"] == "keyword"),
            "content_updates": sum(1 for row in actions if row["type"] == "content"),
        },
        "requires_approval_for_writes": True,
    }


def _read_degraded_reasons(inputs: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if inputs.get("connector_read_ready") is False or inputs.get("connector_degraded") is True:
        reasons.append("SEO connector is degraded or unavailable.")
    freshness = _normalize(inputs.get("data_freshness_status") or inputs.get("freshness_status"))
    if freshness in {"stale", "partial", "degraded"}:
        reasons.append(f"SEO source data is {freshness}.")
    if inputs.get("stale_data") is True:
        reasons.append("SEO source data is stale.")
    if inputs.get("partial_data") is True:
        reasons.append("SEO source data is partial.")
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        relevant = [contract for contract in contracts if _is_seo_read_contract(contract)]
        if relevant and not any(bool(contract.get("read_ready", contract.get("configured"))) for contract in relevant):
            reasons.append("SEO source connector is not read-ready.")
        if any(
            contract.get("degraded") or contract.get("health_status") in {"stale", "degraded"}
            for contract in relevant
        ):
            reasons.append("One or more SEO source connectors are degraded or stale.")
        if any(contract.get("mock_or_test_double") or contract.get("stub_only") for contract in relevant):
            reasons.append("Mock, test-double, or stub SEO connector proof is not production lineage.")
    return _dedupe(reasons)


def _analysis_confidence(
    keyword_gaps: list[dict[str, Any]],
    ranking_deltas: list[dict[str, Any]],
    technical_issues: list[dict[str, Any]],
    content_recommendations: list[dict[str, Any]],
    degraded: list[str],
) -> float:
    signal_count = len(keyword_gaps) + len(ranking_deltas) + len(technical_issues) + len(content_recommendations)
    if signal_count == 0:
        base = 0.52
    elif signal_count < 4:
        base = 0.74
    else:
        base = 0.88
    if degraded:
        base -= 0.22
    return round(max(0.0, min(base, 0.94)), 3)


def _policy_context_for_write(action: str, inputs: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "workflow_id": "seo_sprint",
        "workflow_mode": mode,
        "action": action,
        "external_write_required": True,
        "customer_facing": True,
        "channel": str(inputs.get("channel") or inputs.get("connector_key") or "cms"),
        "legal_claim": bool(inputs.get("legal_claim") or inputs.get("claims_review_required")),
        "pricing_claim": bool(inputs.get("pricing_claim")),
        "comparative_claim": bool(inputs.get("comparative_claim")),
        "high_risk_copy": bool(inputs.get("high_risk_copy")),
    }


def _escalation_for_write(
    task,
    action: str,
    inputs: dict[str, Any],
    policy_result: dict[str, Any],
) -> dict[str, Any]:
    high_risk = any(
        bool(inputs.get(field))
        for field in ("legal_claim", "pricing_claim", "comparative_claim", "claims_review_required", "high_risk_copy")
    )
    return evaluate_marketing_escalation(
        {
            "workflow_id": "seo_sprint",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "event_type": "pricing_or_legal_claim" if high_risk else "approval_timeout",
            "pricing_claim": bool(inputs.get("pricing_claim")),
            "legal_claim": bool(inputs.get("legal_claim") or inputs.get("claims_review_required")),
            "comparative_claim": bool(inputs.get("comparative_claim")),
            "high_risk_copy": bool(inputs.get("high_risk_copy")),
            "approval_sensitive": True,
            "severity": "high" if high_risk else "medium",
            "reason": "SEO technical site action requires owner routing.",
            "marketing_policy_decision": policy_result,
        }
    )


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "SEO/CMS connector is not write-safe."
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        for contract in contracts:
            if not _is_seo_write_contract(contract):
                continue
            if contract.get("mock_or_test_double"):
                return False, "Test-double connector proof cannot satisfy active SEO site writes."
            if bool(contract.get("write_safe", contract.get("write_ready"))):
                return True, ""
            status = str(contract.get("write_status") or contract.get("contract_state") or "unknown")
            return False, f"SEO/CMS connector contract is not write-safe ({status})."
        return False, "No SEO/CMS connector contract is write-safe for SEO site action."
    return False, "SEO/CMS connector write-readiness evidence is missing."


def _is_seo_read_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"seo", "analytics", "cms"} or key in {"ahrefs", "ga4", "google_search_console", "wordpress"}


def _is_seo_write_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"cms", "seo"} or key in {"wordpress", "webflow", "google_search_console"}


def _has_approval(inputs: dict[str, Any]) -> bool:
    return bool(
        inputs.get("approved")
        or inputs.get("approval_ref")
        or inputs.get("approval_result_ref")
        or inputs.get("policy_approval_ref")
    )


def _has_escalation_approval(inputs: dict[str, Any]) -> bool:
    return bool(inputs.get("escalation_ref") or inputs.get("escalation_result_ref") or inputs.get("approved"))


def _connector_tool(action: str, inputs: dict[str, Any]) -> tuple[str, str]:
    explicit = str(inputs.get("connector_key") or inputs.get("connector") or "").strip().lower()
    if action == "submit_url_to_index":
        return explicit or "google_search_console", "submit_url_to_index"
    return explicit or "wordpress", "apply_seo_site_change"


def _site_write_payload(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "url": inputs.get("url") or inputs.get("page_url"),
        "title": inputs.get("title"),
        "meta_description": inputs.get("meta_description"),
        "canonical_url": inputs.get("canonical_url"),
        "redirect_from": inputs.get("redirect_from"),
        "redirect_to": inputs.get("redirect_to"),
        "robots_txt": inputs.get("robots_txt"),
        "sitemap_url": inputs.get("sitemap_url"),
        "source_refs": _dict_list(inputs.get("source_refs")),
        "rollback_plan": inputs.get("rollback_plan") or "revert CMS metadata, redirect, robots, or sitemap change",
    }


def _external_write_confirmation_status(result: dict[str, Any] | None) -> str:
    if not result:
        return "write_unconfirmed"
    return str(
        result.get("external_write_confirmation_status")
        or result.get("write_confirmation_status")
        or result.get("status")
        or "write_unconfirmed"
    )


def _is_confirmed_external_write(result: dict[str, Any] | None) -> bool:
    if not result or "error" in result:
        return False
    status = _external_write_confirmation_status(result)
    return status == "write_confirmed" and bool(
        result.get("external_object_id") or result.get("external_id") or result.get("confirmed_at")
    )


def _external_write_ref(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        "connector_key": result.get("connector_key"),
        "external_object_id": result.get("external_object_id") or result.get("external_id"),
        "source_url": result.get("source_url") or result.get("url"),
        "idempotency_key": result.get("idempotency_key"),
        "confirmed_at": result.get("confirmed_at"),
        "audit_ref": result.get("audit_ref") or result.get("audit_reference"),
    }


def _keyword_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"keyword": item.strip()} for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        rows = []
        for item in value:
            if isinstance(item, dict):
                rows.append(dict(item))
            elif str(item).strip():
                rows.append({"keyword": str(item).strip()})
        return rows
    return []


def _keyword_key(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("keyword") or row.get("query") or row.get("term") or "").strip().lower()


def _rank_value(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    for key in ("rank", "position", "current_rank", "google_rank"):
        if row.get(key) not in {None, ""}:
            value = _float(row.get(key), 0.0)
            return value if value > 0 else None
    return None


def _technical_action(issue: dict[str, Any]) -> str:
    issue_type = _normalize(issue.get("type") or issue.get("category"))
    if "redirect" in issue_type:
        return "fix_redirect_chain"
    if "canonical" in issue_type:
        return "fix_canonical_tag"
    if "sitemap" in issue_type:
        return "update_sitemap"
    if "robots" in issue_type:
        return "review_robots_txt"
    if "speed" in issue_type or "core_web_vitals" in issue_type:
        return "improve_page_performance"
    if "metadata" in issue_type or "title" in issue_type:
        return "update_page_metadata"
    return "resolve_technical_seo_issue"


def _bundle_key(impact: str, effort: str) -> str:
    high_impact = impact == "high"
    low_effort = effort == "low"
    if high_impact and low_effort:
        return "quick_wins"
    if high_impact:
        return "strategic"
    if low_effort:
        return "maintenance"
    return "deprioritized"


def _default_recommendations(degraded: list[str]) -> list[dict[str, Any]]:
    if degraded:
        return [{"action": "restore_seo_data_readiness", "reason": "; ".join(degraded)}]
    return [{"action": "continue_seo_monitoring", "reason": "No material SEO blocker was detected."}]


def _source_refs(
    inputs: dict[str, Any],
    keyword_gaps: list[dict[str, Any]],
    ranking_deltas: list[dict[str, Any]],
    technical_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _dict_list(inputs.get("source_refs"))
    for gap in keyword_gaps[:10]:
        refs.append({"type": "seo_keyword", "ref_id": gap["keyword"], "source": gap.get("source")})
    for row in ranking_deltas[:10]:
        refs.append(
            {
                "type": "seo_ranking",
                "ref_id": row["keyword"],
                "source": row.get("source"),
                "url": row.get("url"),
            }
        )
    for issue in technical_issues[:10]:
        refs.append({"type": "site_audit", "ref_id": issue["issue_id"], "source": issue.get("source")})
    return _dedupe_refs(refs)


def _hitl_request(task, output: dict[str, Any]) -> HITLRequest:
    reasons = output.get("blocked_reasons") or []
    if not reasons and output.get("policy_result"):
        reasons = [str(output["policy_result"].get("reason") or "SEO Strategist action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "growth_lead"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "SEO Strategist review required.",
        trigger_type="seo_strategist_review",
        decision_required=DecisionRequired(
            question="Review SEO recommendation or technical site-change safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "SEO Strategist review",
            recommendation=output.get("recommended_actions", [{}])[0].get("action", "review"),
            agent_confidence=float(output.get("confidence") or 0.0),
            supporting_data={
                "policy_decision": (output.get("policy_result") or {}).get("decision"),
                "blocked_reasons": output.get("blocked_reasons") or [],
                "degraded_reasons": output.get("degraded_reasons") or [],
                "external_write_status": output.get("external_write_confirmation_status"),
            },
        ),
        assignee=HITLAssignee(role=str(role), notify_channels=["slack", "email"]),
    )


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return _normalize(inputs.get("workflow_mode") or inputs.get("mode") or "shadow")


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple | set):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("keyword") or item.get("query") or "").strip()
            else:
                text = str(item).strip()
            if text:
                result.append(text)
        return result
    return [str(value).strip()] if str(value).strip() else []


def _severity(value: Any, default: str) -> str:
    normalized = _normalize(value or default)
    return normalized if normalized in SEVERITY_WEIGHTS else default


def _effort_label(value: Any) -> str:
    if isinstance(value, int | float):
        if value <= 2:
            return "low"
        if value <= 5:
            return "medium"
        return "high"
    normalized = _normalize(value)
    return normalized if normalized in EFFORT_WEIGHTS else "medium"


def _effort_score(value: Any) -> float:
    if isinstance(value, int | float):
        return max(1.0, min(float(value), 10.0)) / 3.0
    return float(EFFORT_WEIGHTS.get(_effort_label(value), 2))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            str(ref.get("type") or ref.get("connector_key") or ""),
            str(ref.get("ref_id") or ref.get("object") or ref.get("url") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result
