# SPDX-License-Identifier: Apache-2.0
"""Authority flags other than grant enforcement read global and tenant rows separately.

* ``pseudonymisation.pre_model`` is protective: it is on when either the global
  row or the tenant's row enables it, so a tenant row cannot switch off an
  operator's global setting.
* ``approvals.resume_agent_runs`` executes approved work: a global row that
  disables it keeps runs paused for every tenant; otherwise the tenant row
  decides, else the global row. An unreadable store keeps runs paused.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.feature_flags import FeatureFlagLookupError, FlagRows

TENANT = uuid.UUID(int=0x1F8A)
ON = {"enabled": True, "rollout_percentage": 100}
OFF = {"enabled": False, "rollout_percentage": 100}


def _store(global_row: dict[str, Any] | None, tenant_row: dict[str, Any] | None) -> AsyncMock:
    return AsyncMock(return_value=FlagRows(global_row=global_row, tenant_row=tenant_row))


@pytest.mark.parametrize(
    ("global_row", "tenant_row", "expected"),
    [
        (None, None, False),
        (ON, None, True),
        (None, ON, True),
        (ON, OFF, True),  # a tenant row cannot switch off the global setting
        (OFF, ON, True),
        (OFF, OFF, False),
    ],
)
async def test_pseudonymisation_is_on_when_either_row_enables_it(global_row, tenant_row, expected):
    from core.pii import pseudonymiser

    with patch("core.feature_flags.load_flag_rows_strict", _store(global_row, tenant_row)):
        assert await pseudonymiser.pseudonymisation_enabled(str(TENANT)) is expected


async def test_pseudonymisation_refuses_when_the_store_is_unreadable():
    from core.pii import pseudonymiser

    with patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=FeatureFlagLookupError("down"))):
        with pytest.raises(pseudonymiser.PseudonymisationError, match="flag_lookup_failed"):
            await pseudonymiser.pseudonymisation_enabled(str(TENANT))


def _paused_item() -> Any:
    item = MagicMock()
    item.id = uuid.uuid4()
    item.workflow_run_id = None
    item.checkpoint_thread_id = f"{TENANT}:thread"
    item.status = "decided"
    item.decision = "approve"
    item.decision_notes = ""
    return item


@pytest.mark.parametrize(
    ("global_row", "tenant_row", "expected", "reason"),
    [
        (None, None, False, "resume_flag_off"),
        (None, ON, True, None),
        (ON, None, True, None),
        (ON, OFF, False, "resume_flag_off"),
        (OFF, ON, False, "resume_flag_disabled_globally"),  # the global row disabling it wins
        (OFF, None, False, "resume_flag_disabled_globally"),
    ],
)
async def test_resume_needs_an_enabling_row_and_no_global_disable(global_row, tenant_row, expected, reason):
    from core.approvals import agent_run_resume as ar

    with (
        patch("core.feature_flags.load_flag_rows_strict", _store(global_row, tenant_row)),
        patch.object(ar.logger, "warning") as warn,
    ):
        assert await ar.should_resume(_paused_item(), TENANT) is expected
    if reason:
        assert warn.call_args.kwargs["reason"] == reason


async def test_resume_stays_paused_when_the_store_is_unreadable():
    from core.approvals import agent_run_resume as ar

    with (
        patch("core.feature_flags.load_flag_rows_strict", AsyncMock(side_effect=FeatureFlagLookupError("down"))),
        patch.object(ar.logger, "warning") as warn,
    ):
        assert await ar.should_resume(_paused_item(), TENANT) is False
    assert warn.call_args.kwargs["reason"] == "resume_flag_unavailable"
