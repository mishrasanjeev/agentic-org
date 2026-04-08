"""Content Factory agent implementation."""

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

# Content type configurations
CONTENT_TYPES: dict[str, dict[str, Any]] = {
    "blog": {"min_words": 800, "max_words": 2000, "channels": ["website", "linkedin"]},
    "social": {"min_words": 20, "max_words": 280, "channels": ["twitter", "linkedin", "facebook"]},
    "email": {"min_words": 100, "max_words": 500, "channels": ["email"]},
    "landing_page": {"min_words": 200, "max_words": 1000, "channels": ["website"]},
    "case_study": {"min_words": 500, "max_words": 1500, "channels": ["website", "email"]},
    "whitepaper": {"min_words": 2000, "max_words": 5000, "channels": ["website"]},
}

# Compliance keywords to flag (India-specific: SEBI, RBI, MCA disclaimers)
COMPLIANCE_FLAG_KEYWORDS = [
    "guaranteed returns", "risk-free", "assured", "100% safe",
    "no loss", "double your money", "get rich", "invest now",
    "act now", "limited time", "once in a lifetime",
]

# Brand voice rules
BRAND_VOICE_CHECKS = {
    "professional": ["!!", "!!!", "lol", "omg", "wtf", "rofl", "lmao"],
    "inclusive": ["guys", "mankind", "manpower", "his/her"],
    "clarity": ["synergy", "leverage", "paradigm", "disrupt"],
}


@AgentRegistry.register
class ContentFactoryAgent(BaseAgent):
    agent_type = "content_factory"
    domain = "marketing"
    confidence_floor = 0.88
    prompt_file = "content_factory.prompt.txt"

    async def execute(self, task):
        """Accept brief, generate draft, apply brand voice, check compliance, suggest images, schedule."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})

            content_type = inputs.get("content_type", inputs.get("type", "blog")).lower()
            topic = inputs.get("topic", inputs.get("subject", ""))
            brief = inputs.get("brief", inputs.get("description", ""))
            target_audience = inputs.get("target_audience", "")
            tone = inputs.get("tone", "professional")
            keywords = inputs.get("keywords", [])
            publish_date = inputs.get("publish_date", "")
            channel = inputs.get("channel", "")
            trace.append(f"Content factory: type={content_type}, topic='{topic[:60]}', tone={tone}")

            type_config = CONTENT_TYPES.get(content_type, CONTENT_TYPES["blog"])

            # --- Step 1: Generate content draft using LLM ---
            generation_prompt = self._build_content_prompt(
                content_type, topic, brief, target_audience, tone, keywords, type_config,
            )

            # Use LLM router to generate the content
            from core.llm.router import llm_router

            llm_messages = [
                {"role": "system", "content": (
                    f"You are a professional content writer. Write in a {tone} tone. "
                    f"Content type: {content_type}. Target audience: {target_audience}. "
                    f"Include these keywords naturally: {', '.join(keywords)}."
                )},
                {"role": "user", "content": generation_prompt},
            ]

            model_override = self._resolve_llm_model()
            trace.append(f"Generating {content_type} content via LLM")
            llm_response = await llm_router.complete(llm_messages, model_override=model_override)
            draft_content = llm_response.content.strip()
            trace.append(f"Draft generated: {len(draft_content)} chars, {len(draft_content.split())} words")

            # --- Step 2: Apply brand voice rules ---
            brand_issues: list[dict] = []
            draft_lower = draft_content.lower()

            for category, flagged_words in BRAND_VOICE_CHECKS.items():
                for word in flagged_words:
                    if word.lower() in draft_lower:
                        brand_issues.append({
                            "category": category,
                            "word": word,
                            "severity": "warning",
                        })

            if brand_issues:
                trace.append(f"Brand voice issues: {len(brand_issues)}")
                # Ask LLM to fix brand voice issues
                fix_words = ", ".join({i["word"] for i in brand_issues})
                fix_messages = [
                    {"role": "system", "content": "You are an editor. Fix brand voice issues."},
                    {"role": "user", "content": (
                        f"Revise this content to remove or replace these words/phrases: {fix_words}. "
                        f"Maintain a {tone} brand voice.\n\n{draft_content}"
                    )},
                ]
                fix_response = await llm_router.complete(fix_messages, model_override=model_override)
                draft_content = fix_response.content.strip()
                trace.append("Brand voice issues fixed")

            # --- Step 3: Check for compliance issues ---
            compliance_issues: list[dict] = []
            for keyword in COMPLIANCE_FLAG_KEYWORDS:
                if keyword.lower() in draft_content.lower():
                    compliance_issues.append({
                        "keyword": keyword,
                        "severity": "critical",
                        "action": "must_remove",
                    })

            if compliance_issues:
                trace.append(f"Compliance issues found: {len(compliance_issues)}")

            # --- Step 4: Validate content quality ---
            word_count = len(draft_content.split())
            meets_length = type_config["min_words"] <= word_count <= type_config["max_words"]

            # Check keyword density
            keywords_found = []
            for kw in keywords:
                if kw.lower() in draft_content.lower():
                    count = draft_content.lower().count(kw.lower())
                    density = count / max(word_count, 1) * 100
                    keywords_found.append({"keyword": kw, "count": count, "density_pct": round(density, 2)})

            keyword_coverage = len(keywords_found) / len(keywords) * 100 if keywords else 100

            quality_metrics = {
                "word_count": word_count,
                "target_range": f"{type_config['min_words']}-{type_config['max_words']}",
                "meets_length": meets_length,
                "keyword_coverage_pct": round(keyword_coverage, 1),
                "keywords_found": keywords_found,
                "brand_issues_count": len(brand_issues),
                "compliance_issues_count": len(compliance_issues),
            }
            trace.append(
                f"Quality: {word_count} words, meets_length={meets_length}, "
                f"keyword_coverage={keyword_coverage:.0f}%"
            )

            # --- Step 5: Suggest images ---
            image_suggestions: list[dict] = []
            if content_type in ("blog", "landing_page", "case_study"):
                img_result = await self._safe_tool_call(
                    "unsplash", "search_photos",
                    {"query": topic, "per_page": 3},
                    trace, tool_calls,
                )
                if img_result and "error" not in img_result:
                    photos = img_result.get("results", img_result.get("photos", []))
                    for photo in photos[:3]:
                        image_suggestions.append({
                            "url": photo.get("urls", {}).get("regular", photo.get("url", "")),
                            "description": photo.get("alt_description", photo.get("description", "")),
                            "credit": photo.get("user", {}).get("name", ""),
                        })
                    trace.append(f"Suggested {len(image_suggestions)} images")

            # --- Step 6: Schedule publication ---
            scheduled = False
            schedule_result_data: dict[str, Any] = {}
            if publish_date and not compliance_issues:
                publish_channel = channel or (type_config["channels"][0] if type_config["channels"] else "")

                if publish_channel == "website":
                    sched_result = await self._safe_tool_call(
                        "wordpress", "create_draft",
                        {
                            "title": topic,
                            "content": draft_content,
                            "status": "scheduled",
                            "publish_date": publish_date,
                            "categories": keywords[:3],
                        },
                        trace, tool_calls,
                    )
                    if sched_result and "error" not in sched_result:
                        scheduled = True
                        schedule_result_data = {
                            "platform": "wordpress",
                            "post_id": sched_result.get("id", ""),
                            "url": sched_result.get("url", ""),
                        }
                elif publish_channel in ("linkedin", "twitter", "facebook"):
                    sched_result = await self._safe_tool_call(
                        "buffer", "create_post",
                        {
                            "text": draft_content[:280] if publish_channel == "twitter" else draft_content,
                            "channels": [publish_channel],
                            "scheduled_at": publish_date,
                        },
                        trace, tool_calls,
                    )
                    if sched_result and "error" not in sched_result:
                        scheduled = True
                        schedule_result_data = {
                            "platform": "buffer",
                            "post_id": sched_result.get("id", ""),
                        }
                elif publish_channel == "email":
                    sched_result = await self._safe_tool_call(
                        "sendgrid", "create_campaign",
                        {
                            "name": topic,
                            "subject": topic,
                            "html_content": draft_content,
                            "send_at": publish_date,
                        },
                        trace, tool_calls,
                    )
                    if sched_result and "error" not in sched_result:
                        scheduled = True
                        schedule_result_data = {
                            "platform": "sendgrid",
                            "campaign_id": sched_result.get("id", ""),
                        }

                if scheduled:
                    trace.append(f"Content scheduled on {publish_channel} for {publish_date}")

            # --- Step 7: Compute confidence ---
            factors: list[float] = []
            factors.append(0.90 if meets_length else 0.60)
            factors.append(min(0.5 + keyword_coverage / 200, 0.95))
            factors.append(0.95 if not compliance_issues else 0.40)
            factors.append(0.90 if not brand_issues else 0.75)
            if publish_date:
                factors.append(0.90 if scheduled else 0.60)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 8: HITL for compliance issues or low quality ---
            hitl_reasons: list[str] = []
            if compliance_issues:
                hitl_reasons.append(f"{len(compliance_issues)} compliance issues found")
            if not meets_length:
                hitl_reasons.append(
                    f"word count {word_count} outside range {type_config['min_words']}-{type_config['max_words']}"
                )
            if keyword_coverage < 50:
                hitl_reasons.append(f"low keyword coverage ({keyword_coverage:.0f}%)")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "generated",
                "content_type": content_type,
                "topic": topic,
                "draft": draft_content,
                "quality_metrics": quality_metrics,
                "brand_issues": brand_issues,
                "compliance_issues": compliance_issues,
                "image_suggestions": image_suggestions,
                "scheduled": scheduled,
                "schedule_details": schedule_result_data,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="content_review",
                    decision_required=DecisionRequired(
                        question=f"Content '{topic}': {'; '.join(hitl_reasons)}. Review draft?",
                        options=[
                            DecisionOption(id="approve", label="Approve and publish", action="proceed"),
                            DecisionOption(id="edit", label="Edit content", action="defer"),
                            DecisionOption(id="regenerate", label="Regenerate", action="retry"),
                            DecisionOption(id="reject", label="Reject", action="reject"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Content review: {content_type} on '{topic}'",
                        recommendation="edit" if compliance_issues else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "word_count": word_count,
                            "compliance_issues": len(compliance_issues),
                            "keyword_coverage": round(keyword_coverage, 1),
                        },
                    ),
                    assignee=HITLAssignee(role="content_lead", notify_channels=["slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("content_factory_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "CONTENT_ERR", "message": str(e)}, start=start,
            )

    @staticmethod
    def _build_content_prompt(
        content_type: str,
        topic: str,
        brief: str,
        target_audience: str,
        tone: str,
        keywords: list[str],
        type_config: dict,
    ) -> str:
        """Build the content generation prompt."""
        prompt = f"Write a {content_type} about: {topic}\n\n"
        if brief:
            prompt += f"Brief: {brief}\n\n"
        if target_audience:
            prompt += f"Target audience: {target_audience}\n"
        prompt += f"Tone: {tone}\n"
        prompt += f"Word count: {type_config['min_words']}-{type_config['max_words']} words\n"
        if keywords:
            prompt += f"Include these keywords naturally: {', '.join(keywords)}\n"
        prompt += (
            "\nRequirements:\n"
            "- Clear, engaging headline\n"
            "- Structured with subheadings\n"
            "- Include a strong call-to-action\n"
            "- Optimize for SEO\n"
            "- No compliance-risky language (no guarantees, no 'risk-free', etc.)\n"
        )
        return prompt

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
