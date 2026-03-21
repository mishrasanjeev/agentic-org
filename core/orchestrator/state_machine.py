"""Workflow state machine."""

from __future__ import annotations

from enum import StrEnum


class WorkflowState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HITL = "waiting_hitl"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TRANSITIONS = {
    WorkflowState.PENDING: [WorkflowState.RUNNING, WorkflowState.CANCELLED],
    WorkflowState.RUNNING: [
        WorkflowState.WAITING_HITL,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
    ],
    WorkflowState.WAITING_HITL: [
        WorkflowState.RUNNING,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
    ],
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
