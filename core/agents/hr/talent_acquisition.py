"""Talent Acquisition agent implementation."""

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

# Candidate scoring thresholds
SCORE_INTERVIEW_THRESHOLD = 70  # Minimum score to schedule interview
SCORE_AUTO_REJECT_THRESHOLD = 30  # Below this, auto-reject
TOP_N_CANDIDATES = 5  # Schedule interviews for top N
# HITL for senior roles (CTC > 25 LPA) or low candidate pool
HITL_CTC_THRESHOLD = 2_500_000
HITL_MIN_QUALIFIED = 3


@AgentRegistry.register
class TalentAcquisitionAgent(BaseAgent):
    agent_type = "talent_acquisition"
    domain = "hr"
    confidence_floor = 0.88
    prompt_file = "talent_acquisition.prompt.txt"

    async def execute(self, task):
        """Parse JD, screen resumes, score candidates, schedule interviews for top candidates."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            action = task.task.action if hasattr(task.task, "action") else task.task.get("action", "screen_candidates")

            job_id = inputs.get("job_id", "")
            jd = inputs.get("job_description", inputs.get("jd", {}))
            candidates = inputs.get("candidates", [])
            ctc_budget = float(inputs.get("ctc_budget", 0))
            trace.append(f"Talent acquisition: job={job_id}, candidates={len(candidates)}, action={action}")

            # --- Step 1: Parse JD requirements ---
            required_skills = set()
            preferred_skills = set()
            min_experience = 0
            required_education = ""

            if isinstance(jd, dict):
                required_skills = set(jd.get("required_skills", []))
                preferred_skills = set(jd.get("preferred_skills", []))
                min_experience = int(jd.get("min_experience", 0))
                required_education = jd.get("education", "")
            elif isinstance(jd, str) and jd:
                # If JD is raw text, try to fetch structured JD from HRMS
                jd_result = await self._safe_tool_call(
                    "greythr", "get_job_posting",
                    {"job_id": job_id},
                    trace, tool_calls,
                )
                if jd_result and "error" not in jd_result:
                    required_skills = set(jd_result.get("required_skills", []))
                    preferred_skills = set(jd_result.get("preferred_skills", []))
                    min_experience = int(jd_result.get("min_experience", 0))
                    required_education = jd_result.get("education", "")

            trace.append(
                f"JD parsed: required_skills={len(required_skills)}, "
                f"preferred={len(preferred_skills)}, min_exp={min_experience}"
            )

            # --- Step 2: Fetch candidates if not provided ---
            if not candidates:
                ats_result = await self._safe_tool_call(
                    "greythr", "get_applicants",
                    {"job_id": job_id, "status": "new"},
                    trace, tool_calls,
                )
                if ats_result and "error" not in ats_result:
                    candidates = ats_result.get("applicants", ats_result.get("candidates", []))
                    trace.append(f"Fetched {len(candidates)} candidates from ATS")

            # --- Step 3: Score each candidate ---
            scored_candidates: list[dict] = []
            for candidate in candidates:
                score, breakdown = self._score_candidate(
                    candidate, required_skills, preferred_skills,
                    min_experience, required_education,
                )
                scored_candidates.append({
                    "name": candidate.get("name", "Unknown"),
                    "email": candidate.get("email", ""),
                    "phone": candidate.get("phone", ""),
                    "score": score,
                    "breakdown": breakdown,
                    "experience_years": candidate.get("experience_years", candidate.get("experience", 0)),
                    "current_ctc": candidate.get("current_ctc", 0),
                    "expected_ctc": candidate.get("expected_ctc", 0),
                    "status": (
                        "shortlisted" if score >= SCORE_INTERVIEW_THRESHOLD
                        else "rejected" if score < SCORE_AUTO_REJECT_THRESHOLD
                        else "review"
                    ),
                })

            # Sort by score descending
            scored_candidates.sort(key=lambda x: x["score"], reverse=True)
            shortlisted = [c for c in scored_candidates if c["status"] == "shortlisted"][:TOP_N_CANDIDATES]
            rejected = [c for c in scored_candidates if c["status"] == "rejected"]
            review_pool = [c for c in scored_candidates if c["status"] == "review"]

            trace.append(
                f"Scoring done: shortlisted={len(shortlisted)}, "
                f"review={len(review_pool)}, rejected={len(rejected)}"
            )

            # --- Step 4: Schedule interviews for shortlisted candidates ---
            interviews_scheduled: list[dict] = []
            for candidate in shortlisted:
                if candidate.get("email"):
                    schedule_result = await self._safe_tool_call(
                        "google_calendar", "create_event",
                        {
                            "summary": f"Interview: {candidate['name']} for {job_id}",
                            "attendees": [candidate["email"]],
                            "duration_minutes": 60,
                            "description": f"Score: {candidate['score']}/100",
                        },
                        trace, tool_calls,
                    )
                    scheduled = schedule_result and "error" not in schedule_result
                    interviews_scheduled.append({
                        "candidate": candidate["name"],
                        "scheduled": scheduled,
                        "event_id": schedule_result.get("event_id", "") if scheduled else "",
                    })

            # --- Step 5: Update ATS status ---
            for candidate in shortlisted:
                await self._safe_tool_call(
                    "greythr", "update_applicant_status",
                    {"email": candidate["email"], "job_id": job_id, "status": "interview_scheduled"},
                    trace, tool_calls,
                )
            for candidate in rejected:
                await self._safe_tool_call(
                    "greythr", "update_applicant_status",
                    {"email": candidate["email"], "job_id": job_id, "status": "rejected"},
                    trace, tool_calls,
                )

            # --- Step 6: Compute confidence ---
            factors: list[float] = []
            if required_skills:
                factors.append(0.90)
            else:
                factors.append(0.60)  # No JD means lower confidence in screening
            if len(scored_candidates) > 0:
                quality_ratio = len(shortlisted) / len(scored_candidates)
                factors.append(0.5 + quality_ratio * 0.5)
            else:
                factors.append(0.40)
            # Interview scheduling success
            if interviews_scheduled:
                sched_success = sum(1 for i in interviews_scheduled if i["scheduled"]) / len(interviews_scheduled)
                factors.append(0.5 + sched_success * 0.5)
            else:
                factors.append(0.70)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 7: HITL for senior roles or thin pipeline ---
            hitl_reasons: list[str] = []
            if ctc_budget > HITL_CTC_THRESHOLD:
                hitl_reasons.append(f"senior role (CTC budget INR {ctc_budget:,.0f})")
            if len(shortlisted) < HITL_MIN_QUALIFIED:
                hitl_reasons.append(f"only {len(shortlisted)} qualified candidates (min {HITL_MIN_QUALIFIED})")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "screened",
                "job_id": job_id,
                "total_candidates": len(scored_candidates),
                "shortlisted": shortlisted,
                "review_pool": review_pool,
                "rejected_count": len(rejected),
                "interviews_scheduled": interviews_scheduled,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="recruitment_review",
                    decision_required=DecisionRequired(
                        question=f"Recruitment for {job_id}: {'; '.join(hitl_reasons)}. Review shortlist?",
                        options=[
                            DecisionOption(id="approve", label="Approve shortlist", action="proceed"),
                            DecisionOption(id="expand", label="Expand search criteria", action="retry"),
                            DecisionOption(id="defer", label="Defer hiring", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Recruitment screening for {job_id}",
                        recommendation="approve" if len(shortlisted) >= 2 else "expand",
                        agent_confidence=confidence,
                        supporting_data={
                            "shortlisted_count": len(shortlisted),
                            "top_score": shortlisted[0]["score"] if shortlisted else 0,
                        },
                    ),
                    assignee=HITLAssignee(role="hiring_manager", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("talent_acquisition_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "TA_ERR", "message": str(e)}, start=start,
            )

    @staticmethod
    def _score_candidate(
        candidate: dict,
        required_skills: set,
        preferred_skills: set,
        min_experience: int,
        required_education: str,
    ) -> tuple[int, dict]:
        """Score a candidate 0-100 based on skill match, experience, education."""
        breakdown: dict[str, Any] = {}
        total = 0
        max_total = 0

        # Skill match (50 points max)
        candidate_skills = {
            s.lower().strip()
            for s in candidate.get("skills", [])
        }
        req_lower = {s.lower().strip() for s in required_skills}
        pref_lower = {s.lower().strip() for s in preferred_skills}

        if req_lower:
            req_match = len(candidate_skills & req_lower) / len(req_lower)
            req_score = int(req_match * 35)
            breakdown["required_skills_match"] = round(req_match * 100, 1)
        else:
            req_score = 20  # No requirements means average score
            breakdown["required_skills_match"] = "N/A"
        total += req_score
        max_total += 35

        if pref_lower:
            pref_match = len(candidate_skills & pref_lower) / len(pref_lower)
            pref_score = int(pref_match * 15)
            breakdown["preferred_skills_match"] = round(pref_match * 100, 1)
        else:
            pref_score = 8
            breakdown["preferred_skills_match"] = "N/A"
        total += pref_score
        max_total += 15

        # Experience (25 points max)
        experience = float(candidate.get("experience_years", candidate.get("experience", 0)))
        if min_experience > 0:
            exp_ratio = min(experience / min_experience, 2.0)  # Cap at 2x
            exp_score = int(min(exp_ratio * 15, 25))
        else:
            exp_score = 15 if experience > 0 else 8
        breakdown["experience_score"] = exp_score
        total += exp_score
        max_total += 25

        # Education (15 points max)
        candidate_edu = str(candidate.get("education", candidate.get("degree", ""))).lower()
        if required_education:
            if required_education.lower() in candidate_edu:
                edu_score = 15
            elif any(k in candidate_edu for k in ["mba", "mtech", "ms", "phd", "ca", "cma"]):
                edu_score = 12
            elif any(k in candidate_edu for k in ["btech", "be", "bcom", "bba", "bsc"]):
                edu_score = 8
            else:
                edu_score = 3
        else:
            edu_score = 10
        breakdown["education_score"] = edu_score
        total += edu_score
        max_total += 15

        # Notice period / availability (10 points max)
        notice_days = int(candidate.get("notice_period_days", 90))
        if notice_days <= 15:
            avail_score = 10
        elif notice_days <= 30:
            avail_score = 8
        elif notice_days <= 60:
            avail_score = 5
        else:
            avail_score = 2
        breakdown["availability_score"] = avail_score
        total += avail_score
        max_total += 10

        # Normalize to 0-100
        final_score = int((total / max_total) * 100) if max_total > 0 else 50
        breakdown["total_raw"] = total
        breakdown["max_raw"] = max_total

        return final_score, breakdown

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
