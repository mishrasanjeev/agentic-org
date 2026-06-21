from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.v1.report_schedules import (
    _allowed_report_types,
    _assert_report_type_allowed,
)


def test_cmo_report_schedule_types_are_domain_limited() -> None:
    claims = {
        "role": "cmo",
        "grantex:scopes": ["report_schedules.read", "report_schedules.write"],
    }

    assert _allowed_report_types(claims) == {"cmo_weekly", "campaign_report"}
    _assert_report_type_allowed("cmo_weekly", claims)
    _assert_report_type_allowed("campaign_report", claims)
    with pytest.raises(HTTPException) as exc:
        _assert_report_type_allowed("cfo_daily", claims)
    assert exc.value.status_code == 403


def test_admin_report_schedule_types_are_unrestricted() -> None:
    claims = {"role": "admin", "grantex:scopes": ["agenticorg:admin"]}

    assert _allowed_report_types(claims) is None
    _assert_report_type_allowed("cfo_daily", claims)
    _assert_report_type_allowed("cmo_weekly", claims)
