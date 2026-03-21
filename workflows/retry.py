"""Exponential backoff with jitter."""
from __future__ import annotations

import asyncio
import random


async def retry_with_backoff(
    func, max_retries: int = 3, initial_delay: float = 1.0, max_delay: float = 60.0,
):
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception:
            if attempt == max_retries:
                raise
            delay = min(initial_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            await asyncio.sleep(delay)
