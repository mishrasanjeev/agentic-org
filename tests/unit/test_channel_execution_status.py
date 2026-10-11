# SPDX-License-Identifier: Apache-2.0
import pytest

from core.channels.runtime import execution_status


@pytest.mark.parametrize("state", ["running", "pending", "failed", "cancelled", "unknown", None])
def test_incomplete_results_never_become_success(state):
    assert execution_status({"status": state}) == ("needs_review", "execution_not_completed")


@pytest.mark.parametrize("state", ["waiting_hitl", "waiting", "paused", "hitl_triggered"])
def test_approval_states_remain_approval_states(state):
    assert execution_status({"status": state}) == ("awaiting_approval", None)


def test_completion_requires_success_without_errors():
    assert execution_status({"status": "completed"}) == ("completed", None)
    assert execution_status({"status": "completed", "error": "failed tool"})[0] == "needs_review"
    assert execution_status({"status": "completed", "hitl_trigger": "budget_tracking_failed"})[0] == "awaiting_approval"
