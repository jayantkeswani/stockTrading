"""Tests for the bid/ask paper fill helper (live_price.get_fill_price)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.constants import IST
from app.services.live_price import FillResult, get_fill_price


def _cfg(fill_model="BID_ASK"):
    cfg = MagicMock()
    cfg.fill_model = fill_model
    return cfg


def _cached(ltp=100.0, bid=99.5, ask=100.5, age_seconds=1):
    ts = datetime.now(IST) - timedelta(seconds=age_seconds)
    return {
        "symbol": "NSE:X",
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "timestamp": ts.isoformat(),
    }


def _patches(cfg, cached=None, rest=None):
    return (
        patch(
            "app.services.trading_config.get_trading_config",
            new_callable=AsyncMock, return_value=cfg,
        ),
        patch("app.core.redis.get_cached_price", new_callable=AsyncMock, return_value=cached),
        patch(
            "app.services.live_price._quote_from_rest",
            new_callable=AsyncMock, return_value=rest,
        ),
    )


class TestBidAskFills:

    @pytest.mark.asyncio
    async def test_buy_fills_at_ask(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached())
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.price == 100.5
        assert fill.model == "BID_ASK"
        assert fill.fallback_reason is None
        assert fill.spread_cost == 0.5  # ask − ltp

    @pytest.mark.asyncio
    async def test_sell_fills_at_bid(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached())
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "SELL")
        assert fill.price == 99.5
        assert fill.spread_cost == 0.5  # ltp − bid

    @pytest.mark.asyncio
    async def test_spread_bps_from_mid(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached())
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.spread_bps == pytest.approx(100.0, abs=0.5)  # 1.0 / 100 mid

    @pytest.mark.asyncio
    async def test_record_has_all_fields(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached())
        with p1, p2, p3:
            rec = (await get_fill_price("NSE:X", "BUY")).to_record()
        assert set(rec) == {
            "model", "fallback", "side", "price", "ltp", "bid", "ask",
            "spread_bps", "spread_cost", "ts",
        }


class TestLtpFallbacks:

    @pytest.mark.asyncio
    async def test_config_ltp_fills_at_ltp_but_records_book(self):
        p1, p2, p3 = _patches(_cfg("LTP"), cached=_cached())
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.price == 100.0
        assert fill.model == "LTP"
        assert fill.fallback_reason == "config"
        assert fill.bid == 99.5 and fill.ask == 100.5  # delta dataset intact

    @pytest.mark.asyncio
    async def test_missing_book_falls_back_to_ltp(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached(bid=0, ask=0))
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.price == 100.0
        assert fill.model == "LTP"
        assert fill.fallback_reason == "missing_bid_ask"

    @pytest.mark.asyncio
    async def test_crossed_book_treated_as_missing(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached(bid=101.0, ask=100.2))
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.model == "LTP"
        assert fill.fallback_reason == "missing_bid_ask"

    @pytest.mark.asyncio
    async def test_stale_cache_uses_rest_book(self):
        rest = {"ltp": 102.0, "bid": 101.5, "ask": 102.5, "fresh": True}
        p1, p2, p3 = _patches(_cfg(), cached=_cached(age_seconds=120), rest=rest)
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "BUY")
        assert fill.price == 102.5
        assert fill.model == "BID_ASK"

    @pytest.mark.asyncio
    async def test_stale_cache_and_rest_down_books_stale_ltp(self):
        p1, p2, p3 = _patches(_cfg(), cached=_cached(age_seconds=120), rest=None)
        with p1, p2, p3:
            fill = await get_fill_price("NSE:X", "SELL")
        assert fill.price == 100.0
        assert fill.model == "LTP"
        assert fill.fallback_reason == "stale_quote"

    @pytest.mark.asyncio
    async def test_no_price_anywhere_raises_503(self):
        p1, p2, p3 = _patches(_cfg(), cached=None, rest=None)
        with p1, p2, p3:
            with pytest.raises(HTTPException) as exc:
                await get_fill_price("NSE:X", "BUY")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_invalid_side_rejected(self):
        with pytest.raises(ValueError):
            await get_fill_price("NSE:X", "HOLD")


class TestFillResultMath:

    def test_spread_cost_zero_without_ltp(self):
        fill = FillResult(price=100.0, model="LTP", side="BUY", ltp=None, bid=None, ask=None)
        assert fill.spread_cost == 0.0
        assert fill.spread_bps is None

    def test_short_futures_exit_buys_at_ask_costs_vs_ltp(self):
        # Short exit = BUY at ask; cost = ask − ltp
        fill = FillResult(price=505.0, model="BID_ASK", side="BUY", ltp=504.0, bid=503.0, ask=505.0)
        assert fill.spread_cost == 1.0
