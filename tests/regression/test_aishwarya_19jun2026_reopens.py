"""Aishwarya 19 June 2026 reopened bug regression pins."""

from __future__ import annotations

import pytest


class _StatsResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return [
            {
                "date": "2026-06-18",
                "stats": [{"metrics": {"requests": 7, "delivered": 6}}],
            }
        ]


class _StatsClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get(self, path: str, params: dict):
        self.calls.append({"path": path, "params": dict(params)})
        return _StatsResponse()


@pytest.mark.asyncio
async def test_sendgrid_get_stats_accepts_wrapped_valid_date_range() -> None:
    from connectors.comms.sendgrid import SendgridConnector

    connector = SendgridConnector({"api_key": "SG.test"})
    client = _StatsClient()
    connector._client = client

    result = await connector.get_stats(
        kwargs={"start_date": "2026-06-18", "end_date": "2026-06-19"}
    )

    assert result["stats"][0]["metrics"]["delivered"] == 6
    assert client.calls == [
        {
            "path": "/stats",
            "params": {"start_date": "2026-06-18", "end_date": "2026-06-19"},
        }
    ]


@pytest.mark.asyncio
async def test_sendgrid_get_stats_accepts_prompt_text_date_range() -> None:
    from connectors.comms.sendgrid import SendgridConnector

    connector = SendgridConnector({"api_key": "SG.test"})
    client = _StatsClient()
    connector._client = client

    await connector.get_stats(
        query="Fetch stats for start_date=2026-06-18 and end_date=2026-06-19"
    )

    assert client.calls[0]["params"] == {
        "start_date": "2026-06-18",
        "end_date": "2026-06-19",
    }


@pytest.mark.asyncio
async def test_sendgrid_get_stats_rejects_invalid_dates_before_vendor_call() -> None:
    from connectors.comms.sendgrid import SendgridConnector

    connector = SendgridConnector({"api_key": "SG.test"})
    client = _StatsClient()
    connector._client = client

    result = await connector.get_stats(start_date="2026-06-31")

    assert "YYYY-MM-DD" in result["error"]
    assert client.calls == []
