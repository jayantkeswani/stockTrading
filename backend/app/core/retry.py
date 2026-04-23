"""Async retry helper with exponential backoff and jitter.

Used for safety-critical external calls: Fyers REST, Telegram, position price fetch.
No third-party deps — pure asyncio.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

logger = logging.getLogger(__name__)


async def async_retry(
    func: Callable[..., Awaitable[T]],
    *args,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.25,
    retry_on: tuple[type[BaseException], ...] = (
        httpx.HTTPError,
        asyncio.TimeoutError,
        ConnectionError,
    ),
    should_retry: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException], Awaitable[None]] | None = None,
    label: str = "call",
    **func_kwargs,
) -> T:
    """Retry an async function with exponential backoff and jitter.

    Args:
        func: Async callable to retry.
        *args: Positional arguments forwarded to func.
        retries: Maximum retry count (excluding initial attempt).
        base_delay: Base sleep duration in seconds for the first retry.
        max_delay: Maximum sleep duration cap.
        jitter: Fraction of delay added/subtracted randomly (e.g. 0.25 = ±25%).
        retry_on: Exception types that trigger a retry (used when should_retry is None).
        should_retry: Optional predicate — return True to retry, False to re-raise.
                      Overrides retry_on when provided.
        on_retry: Optional async hook called with (attempt_number, exc) before sleeping.
                  Use this to perform side-effects like token refresh before the next attempt.
        label: Human-readable label for log messages.
        **func_kwargs: Keyword arguments forwarded to func.

    Returns:
        The return value of func on success.

    Raises:
        The last exception after all retries are exhausted.
    """
    last_exc: BaseException | None = None

    for attempt in range(retries + 1):
        try:
            return await func(*args, **func_kwargs)
        except BaseException as exc:
            if should_retry is not None:
                do_retry = should_retry(exc)
            else:
                do_retry = isinstance(exc, retry_on)

            if not do_retry or attempt >= retries:
                raise

            last_exc = exc
            delay = min(max_delay, base_delay * (2 ** attempt))
            jitter_amount = delay * jitter
            sleep_time = max(0.0, delay + random.uniform(-jitter_amount, jitter_amount))

            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                label, attempt + 1, retries + 1, exc, sleep_time,
            )

            if on_retry is not None:
                await on_retry(attempt + 1, exc)

            await asyncio.sleep(sleep_time)

    raise last_exc  # type: ignore[misc]


def with_retry(**retry_kwargs) -> Callable:
    """Decorator that wraps an async function with async_retry.

    Retry params are specified at decoration time; function args/kwargs are
    forwarded transparently at call time.

    Example:
        @with_retry(retries=3, base_delay=2.0, label="telegram_send")
        async def send_telegram(message: str) -> bool: ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        _label = retry_kwargs.get("label", func.__name__)
        _kwargs = {**retry_kwargs, "label": _label}

        async def wrapper(*args, **func_kwargs) -> T:
            return await async_retry(func, *args, **_kwargs, **func_kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper  # type: ignore[return-value]

    return decorator
