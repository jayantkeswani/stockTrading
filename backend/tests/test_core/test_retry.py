"""Tests for the async retry helper."""

import asyncio
from unittest.mock import AsyncMock, call

import httpx
import pytest

from app.core.retry import async_retry, with_retry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_flaky(fail_times: int, exc: Exception | None = None, return_val=True):
    """Return an async callable that fails fail_times then succeeds."""
    exc = exc or httpx.NetworkError("network error")
    attempts = {"n": 0}

    async def fn():
        attempts["n"] += 1
        if attempts["n"] <= fail_times:
            raise exc
        return return_val

    return fn


# ── async_retry ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_succeeds_immediately():
    fn = _make_flaky(0, return_val="ok")
    result = await async_retry(fn, retries=3, base_delay=0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_retries_then_succeeds():
    fn = _make_flaky(2)  # fails twice, then succeeds
    result = await async_retry(fn, retries=3, base_delay=0)
    assert result is True


@pytest.mark.asyncio
async def test_raises_after_exhausted():
    fn = _make_flaky(5, exc=httpx.NetworkError("boom"))
    with pytest.raises(httpx.NetworkError, match="boom"):
        await async_retry(fn, retries=2, base_delay=0)


@pytest.mark.asyncio
async def test_does_not_retry_unlisted_exception():
    async def fn():
        raise ValueError("not retried")

    with pytest.raises(ValueError, match="not retried"):
        await async_retry(fn, retries=3, base_delay=0)


@pytest.mark.asyncio
async def test_should_retry_overrides():
    attempts = {"n": 0}

    async def fn():
        attempts["n"] += 1
        raise ValueError("custom")

    # should_retry says True for ValueError
    result_attempted = 0
    try:
        await async_retry(
            fn,
            retries=2,
            base_delay=0,
            should_retry=lambda e: isinstance(e, ValueError),
        )
    except ValueError:
        result_attempted = attempts["n"]

    assert result_attempted == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_should_retry_false_raises_immediately():
    attempts = {"n": 0}

    async def fn():
        attempts["n"] += 1
        raise ValueError("stop")

    with pytest.raises(ValueError):
        await async_retry(
            fn,
            retries=3,
            base_delay=0,
            should_retry=lambda e: False,
        )

    assert attempts["n"] == 1  # no retries


@pytest.mark.asyncio
async def test_on_retry_hook_called():
    on_retry = AsyncMock()
    fn = _make_flaky(2)

    await async_retry(fn, retries=3, base_delay=0, on_retry=on_retry)

    assert on_retry.call_count == 2
    # First call: attempt=1, second: attempt=2
    assert on_retry.call_args_list[0][0][0] == 1
    assert on_retry.call_args_list[1][0][0] == 2


@pytest.mark.asyncio
async def test_raises_last_exception_verbatim():
    exc = httpx.NetworkError("specific error")

    async def fn():
        raise exc

    with pytest.raises(httpx.NetworkError) as exc_info:
        await async_retry(fn, retries=1, base_delay=0)

    assert exc_info.value is exc


# ── with_retry decorator ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_with_retry_decorator_succeeds():
    @with_retry(retries=3, base_delay=0, label="test")
    async def my_func(x: int) -> int:
        return x * 2

    assert await my_func(5) == 10


@pytest.mark.asyncio
async def test_with_retry_decorator_retries():
    attempts = {"n": 0}

    @with_retry(retries=3, base_delay=0)
    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.NetworkError("transient")
        return "done"

    result = await flaky()
    assert result == "done"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_with_retry_preserves_name():
    @with_retry(retries=1, base_delay=0)
    async def my_named_func():
        return 1

    assert my_named_func.__name__ == "my_named_func"
