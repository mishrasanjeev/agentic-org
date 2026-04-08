"""Bank Reconciliation agent implementation."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
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

# Matching tolerances
AMOUNT_TOLERANCE = 1.0  # INR 1 tolerance for rounding
DATE_TOLERANCE_DAYS = 3  # Allow 3 days date mismatch
UNMATCHED_STALE_DAYS = 30  # Flag unmatched items older than 30 days
# HITL threshold: unmatched value > INR 1,00,000
HITL_UNMATCHED_THRESHOLD = 100_000


@AgentRegistry.register
class ReconAgentAgent(BaseAgent):
    agent_type = "recon_agent"
    domain = "finance"
    confidence_floor = 0.95
    prompt_file = "recon_agent.prompt.txt"

    async def execute(self, task):
        """Fetch bank statement, match against books, flag unmatched items >30 days."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            bank_account = inputs.get("bank_account", "")
            period_from = inputs.get("period_from", "")
            period_to = inputs.get("period_to", "")
            trace.append(f"Bank recon: account={bank_account}, period={period_from} to {period_to}")

            # --- Step 1: Fetch bank statement ---
            bank_result = await self._safe_tool_call(
                "icici_bank", "get_statement",
                {
                    "account_number": bank_account,
                    "from_date": period_from,
                    "to_date": period_to,
                },
                trace, tool_calls,
            )
            bank_txns = []
            if bank_result and "error" not in bank_result:
                bank_txns = bank_result.get("transactions", [])
                trace.append(f"Fetched {len(bank_txns)} bank transactions")
            else:
                trace.append("Bank statement fetch failed; trying alternate connector")
                bank_result = await self._safe_tool_call(
                    "razorpay", "get_settlements",
                    {"from": period_from, "to": period_to},
                    trace, tool_calls,
                )
                if bank_result and "error" not in bank_result:
                    bank_txns = bank_result.get("items", [])
                    trace.append(f"Fetched {len(bank_txns)} transactions from Razorpay")

            # --- Step 2: Fetch book entries from Tally ---
            book_result = await self._safe_tool_call(
                "tally", "get_bank_book",
                {
                    "ledger": bank_account,
                    "from_date": period_from,
                    "to_date": period_to,
                },
                trace, tool_calls,
            )
            book_entries = []
            if book_result and "error" not in book_result:
                book_entries = book_result.get("entries", book_result.get("vouchers", []))
                trace.append(f"Fetched {len(book_entries)} book entries")
            else:
                trace.append("Book entries fetch failed")

            # --- Step 3: Match bank txns against book entries ---
            matched: list[dict] = []
            unmatched_bank: list[dict] = []
            unmatched_book: list[dict] = []
            book_used: set[int] = set()

            now = datetime.now(tz=UTC)

            for b_txn in bank_txns:
                b_amount = float(b_txn.get("amount", 0))
                b_date_str = b_txn.get("date", b_txn.get("transaction_date", ""))
                b_ref = str(b_txn.get("reference", b_txn.get("utr", ""))).strip().lower()

                try:
                    b_date = datetime.fromisoformat(b_date_str) if b_date_str else now
                except (ValueError, TypeError):
                    b_date = now

                best_match_idx = None
                best_score = 0.0

                for idx, bk_entry in enumerate(book_entries):
                    if idx in book_used:
                        continue
                    bk_amount = float(bk_entry.get("amount", 0))
                    bk_date_str = bk_entry.get("date", "")
                    bk_ref = str(bk_entry.get("reference", bk_entry.get("narration", ""))).strip().lower()

                    try:
                        bk_date = datetime.fromisoformat(bk_date_str) if bk_date_str else now
                    except (ValueError, TypeError):
                        bk_date = now

                    # Amount match (within tolerance)
                    amount_match = abs(b_amount - bk_amount) <= AMOUNT_TOLERANCE

                    # Date match (within tolerance)
                    date_diff = abs((b_date - bk_date).days)
                    date_match = date_diff <= DATE_TOLERANCE_DAYS

                    # Reference match
                    ref_match = (b_ref and bk_ref and b_ref in bk_ref) or (bk_ref and b_ref and bk_ref in b_ref)

                    if amount_match and (date_match or ref_match):
                        score = 1.0
                        if date_match:
                            score += 0.5
                        if ref_match:
                            score += 0.5
                        if score > best_score:
                            best_score = score
                            best_match_idx = idx

                if best_match_idx is not None:
                    book_used.add(best_match_idx)
                    matched.append({
                        "bank_txn": b_txn,
                        "book_entry": book_entries[best_match_idx],
                        "match_score": round(best_score / 2.0, 2),
                    })
                else:
                    days_old = (now - b_date).days if b_date else 0
                    unmatched_bank.append({
                        **b_txn,
                        "days_old": days_old,
                        "stale": days_old > UNMATCHED_STALE_DAYS,
                    })

            # Unmatched book entries
            for idx, bk_entry in enumerate(book_entries):
                if idx not in book_used:
                    bk_date_str = bk_entry.get("date", "")
                    try:
                        bk_date = datetime.fromisoformat(bk_date_str) if bk_date_str else now
                    except (ValueError, TypeError):
                        bk_date = now
                    days_old = (now - bk_date).days
                    unmatched_book.append({
                        **bk_entry,
                        "days_old": days_old,
                        "stale": days_old > UNMATCHED_STALE_DAYS,
                    })

            total_txns = len(bank_txns) + len(book_entries)
            matched_count = len(matched)
            stale_count = (
                sum(1 for u in unmatched_bank if u.get("stale"))
                + sum(1 for u in unmatched_book if u.get("stale"))
            )

            trace.append(
                f"Matching done: matched={matched_count}, "
                f"unmatched_bank={len(unmatched_bank)}, unmatched_book={len(unmatched_book)}, "
                f"stale={stale_count}"
            )

            # --- Step 4: Compute confidence ---
            if total_txns > 0:
                match_rate = (matched_count * 2) / total_txns  # Each match covers 2 items
                confidence = round(min(match_rate, 1.0) * 0.85 + 0.10, 3)
                # Penalize for stale items
                if stale_count > 0:
                    confidence -= min(stale_count * 0.03, 0.20)
                confidence = round(min(max(confidence, 0.0), 1.0), 3)
            else:
                confidence = 0.5

            trace.append(f"Computed confidence: {confidence}")

            # --- Step 5: Compute unmatched value ---
            unmatched_value = (
                sum(abs(float(u.get("amount", 0))) for u in unmatched_bank)
                + sum(abs(float(u.get("amount", 0))) for u in unmatched_book)
            )

            # --- Step 6: HITL if unmatched value is high or many stale items ---
            hitl_reasons: list[str] = []
            if unmatched_value > HITL_UNMATCHED_THRESHOLD:
                hitl_reasons.append(f"unmatched value INR {unmatched_value:,.0f} > {HITL_UNMATCHED_THRESHOLD:,}")
            if stale_count >= 5:
                hitl_reasons.append(f"{stale_count} stale items (>30 days)")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor {self.confidence_floor}")

            output = {
                "status": "reconciled" if not hitl_reasons else "needs_review",
                "bank_account": bank_account,
                "period": {"from": period_from, "to": period_to},
                "summary": {
                    "total_bank_txns": len(bank_txns),
                    "total_book_entries": len(book_entries),
                    "matched": matched_count,
                    "unmatched_bank": len(unmatched_bank),
                    "unmatched_book": len(unmatched_book),
                    "stale_items": stale_count,
                    "unmatched_value": round(unmatched_value, 2),
                    "match_rate": round((matched_count * 2) / total_txns, 3) if total_txns > 0 else 0,
                },
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="recon_review",
                    decision_required=DecisionRequired(
                        question=f"Bank recon for {bank_account}: {'; '.join(hitl_reasons)}. Review required.",
                        options=[
                            DecisionOption(id="approve", label="Accept reconciliation", action="proceed"),
                            DecisionOption(id="investigate", label="Investigate mismatches", action="defer"),
                            DecisionOption(id="adjust", label="Post adjusting entries", action="adjust"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Bank recon review: {stale_count} stale, INR {unmatched_value:,.0f} unmatched",
                        recommendation="investigate" if stale_count > 3 else "review",
                        agent_confidence=confidence,
                        supporting_data=output["summary"],
                    ),
                    assignee=HITLAssignee(role="finance_lead", notify_channels=["email"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("recon_agent_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "RECON_ERR", "message": str(e)}, start=start,
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
