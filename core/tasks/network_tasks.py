# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The public AgenticOrg shopping bot: answer one Telegram update off the webhook request.

The webhook (``api.v1.network.network_telegram_webhook``) checks Telegram's secret header and
queues the update here, so Telegram gets its 200 at once. The answer is produced by
``core.commerce.network_telegram.handle_update`` and sent with one ``sendMessage`` attempt; an
update id is answered at most once. A no-op while the bot is off.
"""

from __future__ import annotations

from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app

logger = structlog.get_logger()


async def _send(chat_id: str, text: str) -> None:
    from core.channels.providers import send_reply  # noqa: PLC0415
    from core.config import settings  # noqa: PLC0415

    await send_reply("telegram", {"access_token": settings.network_telegram_bot_token}, chat_id, text)


async def _handle_async(update: Any) -> str:
    from core.commerce import network as net  # noqa: PLC0415
    from core.commerce.network_telegram import handle_update  # noqa: PLC0415
    from core.config import settings  # noqa: PLC0415

    if not (net.network_enabled() and settings.network_telegram_enabled and settings.network_telegram_bot_token):
        return "disabled"
    try:
        outcome = await handle_update(update, send=_send)
    # enterprise-gate: broad-except-ok reason=one-buyer-update-failing-never-retries-into-a-duplicate-reply
    except Exception as exc:
        logger.warning("network_telegram_update_failed", error_type=type(exc).__name__)
        return "failed"
    logger.info("network_telegram_update", outcome=outcome)
    return outcome


@app.task(name="core.tasks.network_tasks.handle_telegram_update")
def handle_telegram_update(update: Any) -> str:
    """Answer one update from the public network bot (behind the flags)."""
    return run_async(_handle_async(update))
