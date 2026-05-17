import pytest
from sqlalchemy.exc import SQLAlchemyError


class _FailingSession:
    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> object:
        raise SQLAlchemyError("database unavailable")


@pytest.mark.asyncio
async def test_gemini_spend_lookup_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.database
    from core.llm.router import DailyBudgetExceeded, assert_under_gemini_cap

    monkeypatch.setenv("AGENTICORG_GEMINI_DAILY_USD_CAP", "10.0")
    monkeypatch.setattr(core.database, "async_session_factory", lambda: _FailingSession())

    with pytest.raises(DailyBudgetExceeded, match="spend lookup unavailable"):
        await assert_under_gemini_cap()

