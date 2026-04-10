"""HTTP retry helpers for external API calls.

Provides exponential backoff with jitter for transient failures
(network errors, 5xx responses, rate limits). Use this wrapper for
any synchronous or async HTTP call to a third-party API.

Usage:
    from core.http_retry import retry_http, retry_http_async

    @retry_http(max_attempts=3)
    def call_stripe():
        return httpx.get("https://api.stripe.com/v1/balance")

    @retry_http_async(max_attempts=3)
    async def call_plural():
        async with httpx.AsyncClient() as client:
            return await client.post("...")
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")

# Retry on network errors, timeouts, and these HTTP status codes
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc: BaseException) -> bool:
    """Check if exception is worth retrying."""
    name = exc.__class__.__name__
    if name in (
        "ConnectError", "ReadError", "WriteError", "PoolTimeout",
        "ConnectTimeout", "ReadTimeout", "WriteTimeout", "RemoteProtocolError",
        "TimeoutException", "ConnectionError",
    ):
        return True
    # httpx HTTPStatusError with retryable status code
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status in RETRYABLE_STATUS_CODES:
            return True
    return False


def _backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with jitter: base * 2^attempt + random jitter."""
    delay = min(cap, base * (2 ** attempt))
    return delay + random.uniform(0, delay * 0.25)


def retry_http(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    cap: float = 30.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry decorator for synchronous HTTP calls.

    Retries on network errors and 5xx/429 responses with exponential backoff.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable_exception(exc) or attempt == max_attempts - 1:
                        raise
                    delay = _backoff_delay(attempt, base_delay, cap)
                    logger.info(
                        "http_retry",
                        function=func.__name__,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        delay_sec=round(delay, 2),
                        error=str(exc)[:200],
                    )
                    time.sleep(delay)
            # Unreachable, but mypy/ruff want it
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def retry_http_async(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    cap: float = 30.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Retry decorator for async HTTP calls.

    Retries on network errors and 5xx/429 responses with exponential backoff.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable_exception(exc) or attempt == max_attempts - 1:
                        raise
                    delay = _backoff_delay(attempt, base_delay, cap)
                    logger.info(
                        "http_retry_async",
                        function=func.__name__,
                        attempt=attempt + 1,
                        max_attempts=max_attempts,
                        delay_sec=round(delay, 2),
                        error=str(exc)[:200],
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
