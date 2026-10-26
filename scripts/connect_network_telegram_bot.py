# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Point the public AgenticOrg shopping bot's Telegram webhook at the network webhook route.

Reads the bot token and webhook secret from the environment (never prints them) and registers
``<base>/api/v1/network/telegram/webhook`` with ``setWebhook`` (``core.channels.providers.connect_telegram``:
it refuses a bot whose webhook already points at another application). Run once after the
token and secret are in Secret Manager and the service carries them:

    AGENTICORG_NETWORK_TELEGRAM_BOT_TOKEN=... AGENTICORG_NETWORK_TELEGRAM_WEBHOOK_SECRET=... \\
        python -m scripts.connect_network_telegram_bot --base-url https://app.agenticorg.ai
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


async def _main(base_url: str) -> int:
    from core.channels.providers import ChannelError, connect_telegram, validate_credentials

    token = os.environ.get("AGENTICORG_NETWORK_TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.environ.get("AGENTICORG_NETWORK_TELEGRAM_WEBHOOK_SECRET", "").strip()
    credentials = {"access_token": token, "webhook_secret": secret}
    try:
        validate_credentials("telegram", credentials)
    except ChannelError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    callback = base_url.rstrip("/") + "/api/v1/network/telegram/webhook"
    try:
        bot = await connect_telegram(credentials, callback)
    except ChannelError as exc:
        print(f"Telegram refused the webhook: {exc}", file=sys.stderr)
        return 1
    print(f"Webhook set for @{bot}: {callback}")
    print(f"Set AGENTICORG_NETWORK_TELEGRAM_BOT_USERNAME={bot} so shops get t.me/{bot}?start=<slug> links.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://app.agenticorg.ai")
    return asyncio.run(_main(parser.parse_args().base_url))


if __name__ == "__main__":
    raise SystemExit(main())
