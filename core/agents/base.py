"""Base agent class — all built-in + custom agents extend this."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import structlog

from core.llm.router import LLMResponse, llm_router
from core.schemas.messages import (
    DecisionOption,
    DecisionRequired,
    HITLAssignee,
    HITLContext,
    HITLRequest,
    PerformanceMetrics,
    TaskAssignment,
    TaskResult,
    ToolCallRecord,
)

logger = structlog.get_logger()
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")


class BaseAgent:
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
        llm_model: str | None = None,
        cost_controls: dict | None = None,
    ):
        self.agent_id = agent_id
        self.tenant_id = tenant_id
        self.authorized_tools = authorized_tools or []
        self.prompt_variables = prompt_variables or {}
        self.hitl_condition = hitl_condition
        self.output_schema = output_schema
        self.tool_gateway = tool_gateway
        self.llm_model = llm_model
        self.cost_controls = cost_controls or {}
        self._system_prompt: str | None = None

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            if self.prompt_file:
                # Built-in agent: load from filesystem
                path = os.path.join(PROMPTS_DIR, self.prompt_file)
                with open(path) as f:
                    template = f.read()
                for key, val in self.prompt_variables.items():
                    template = template.replace("{{" + key + "}}", val)
                self._system_prompt = template
            else:
                # Custom agent with no file — use a minimal default
                self._system_prompt = (
                    "You are an AI agent. Process the given task and return a JSON "
                    "response with 'status', 'confidence' (0.0-1.0), and 'processing_trace'."
                )
        return self._system_prompt

    async def execute(self, task: TaskAssignment) -> TaskResult:
        """Main execution pipeline — full LLM reasoning, validation, confidence, and HITL."""
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
                    task,
                    msg_id,
                    "failed",
                    {},
                    0.0,
                    trace,
                    tool_calls,
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
                    task,
                    msg_id,
                    "hitl_triggered",
                    output,
                    confidence,
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
                confidence,
                trace,
                tool_calls,
                start=start,
            )

        except Exception as e:
            logger.error("agent_execute_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "E5001", "message": str(e)},
                start=start,
            )

    def _resolve_llm_model(self) -> str | None:
        """Resolve which LLM model to use, with safe API key check.

        Returns model string if the agent's preferred model is usable,
        or None to fall back to global default (Gemini).
        """
        if not self.llm_model:
            return None  # Use global default

        model = self.llm_model.lower()

        # Gemini always works (production default)
        if "gemini" in model:
            return self.llm_model

        # Claude requires Anthropic API key
        if "claude" in model:
            from core.config import external_keys
            if external_keys.anthropic_api_key:
                return self.llm_model
            return None  # Fall back to global default

        # GPT requires OpenAI API key
        if "gpt" in model:
            from core.config import external_keys
            if external_keys.openai_api_key:
                return self.llm_model
            return None  # Fall back to global default

        return None  # Unknown model → global default

    async def _reason(self, context: dict, trace: list[str]) -> dict[str, Any]:
        """Call LLM with system prompt and task context."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        model_override = self._resolve_llm_model()
        trace.append(f"Calling LLM for reasoning (model: {model_override or 'default'})")
        response: LLMResponse = await llm_router.complete(
            messages, model_override=model_override
        )
        trace.append(f"LLM responded: {response.model}, {response.tokens_used} tokens")

        # Strip markdown code blocks (```json ... ```) that Gemini often wraps
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [line for line in lines if not line.strip().startswith("```")]
            content = "\n".join(lines).strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            trace.append("LLM output not valid JSON, wrapping")
            return {"raw_output": content, "status": "completed"}

    def _validate_output(self, output: dict[str, Any]) -> bool:
        """Validate output against declared schema."""
        if not self.output_schema:
            return True
        # In production, load schema from registry and validate with jsonschema
        required_field = "status"
        return required_field in output

    def _compute_confidence(self, output: dict[str, Any]) -> float:
        """Compute confidence score from output."""
        raw = output.get("confidence", output.get("agent_confidence", 0.85))
        try:
            return float(raw)
        except (ValueError, TypeError):
            # LLM returned non-numeric confidence like "high", "medium", "low"
            mapping = {"high": 0.95, "medium": 0.75, "low": 0.5}
            return mapping.get(str(raw).lower().strip(), 0.85)

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
        self,
        task,
        msg_id,
        status,
        output,
        confidence,
        trace,
        tool_calls,
        error=None,
        hitl_request=None,
        start=0,
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
