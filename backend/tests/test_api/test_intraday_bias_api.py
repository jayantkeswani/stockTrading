"""Tests for GET /api/v1/market/intraday-bias endpoint."""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.market_data import IntradayBiasResponse, get_intraday_bias


def _mock_redis(value):
    """Return a mock Redis whose .get() resolves to *value*."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=value)
    return r


@pytest.mark.asyncio
async def test_returns_parsed_bias():
    """A well-formed cached value is parsed into the response model."""
    raw = "BULLISH|STRONG|0.7321|2026-06-03T14:30:00+05:30"
    with patch("app.core.redis.get_redis", return_value=_mock_redis(raw)):
        result = await get_intraday_bias(symbol="NIFTY")
    assert isinstance(result, IntradayBiasResponse)
    assert result.symbol == "NIFTY"
    assert result.bias == "BULLISH"
    assert result.strength == "STRONG"
    assert result.score == pytest.approx(0.7321)
    assert result.updated_at == "2026-06-03T14:30:00+05:30"


@pytest.mark.asyncio
async def test_returns_none_when_absent():
    """No cached value (key expired / never computed) → null."""
    with patch("app.core.redis.get_redis", return_value=_mock_redis(None)):
        result = await get_intraday_bias(symbol="NIFTY")
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_malformed_value():
    """A value that doesn't split into 4 parts is treated as absent, not a 500."""
    with patch("app.core.redis.get_redis", return_value=_mock_redis("garbage")):
        result = await get_intraday_bias(symbol="NIFTY")
    assert result is None
