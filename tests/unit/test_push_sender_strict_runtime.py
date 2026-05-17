import pytest

from core.config import settings
from core.push import sender
from core.push.sender import PushSubscriptionStoreUnavailableError


@pytest.mark.asyncio
async def test_push_subscription_save_fails_closed_without_redis_in_strict_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(sender, "_get_redis", lambda: None)
    sender._memory_store.clear()

    with pytest.raises(PushSubscriptionStoreUnavailableError):
        await sender.save_subscription(
            "tenant-1",
            {
                "endpoint": "https://push.example.com/sub",
                "keys": {"p256dh": "key", "auth": "auth"},
            },
        )

    assert sender._memory_store == {}
