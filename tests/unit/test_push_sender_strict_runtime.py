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


@pytest.mark.asyncio
async def test_send_push_runs_webpush_off_event_loop(monkeypatch):
    """pywebpush is blocking; the sender must dispatch it via a worker thread."""
    import sys
    import threading
    import types

    calls = []

    class WebPushException(Exception):  # noqa: N818 — mirrors pywebpush's real name
        pass

    def fake_webpush(**kwargs):
        calls.append((threading.get_ident(), kwargs["subscription_info"]["endpoint"]))

    fake_mod = types.ModuleType("pywebpush")
    fake_mod.webpush = fake_webpush
    fake_mod.WebPushException = WebPushException
    monkeypatch.setitem(sys.modules, "pywebpush", fake_mod)
    monkeypatch.setattr(sender, "get_vapid_keys", lambda: ("pub", "priv"))

    async def _subs(tenant_id):
        return [{"endpoint": "https://push.example/a"}, {"endpoint": "https://push.example/b"}]

    monkeypatch.setattr(sender, "_get_subscriptions", _subs)

    result = await sender.send_push_notification("tenant-a", "hi", "body")

    assert result == {"sent": 2, "failed": 0, "stale_removed": 0}
    assert {c[1] for c in calls} == {"https://push.example/a", "https://push.example/b"}
    assert all(tid != threading.get_ident() for tid, _ in calls)
