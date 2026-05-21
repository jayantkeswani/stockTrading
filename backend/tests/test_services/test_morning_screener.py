"""Tests for morning screener and briefing service."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.indicators.candle_patterns import Candle
from app.services.morning_screener import (
    _BRIEFING_SCHEMA,
    _STAGE3_CONFIDENCE_SCHEMA,
    _compute_stock_score,
    _compute_trade_stats,
    _synthesize_briefing,
    get_agent_log,
    get_agent_status,
    get_morning_briefing,
    get_watchlist,
    set_agent_status,
    snapshot_global_cues,
)


def _candle(open_: float, high: float, low: float, close: float, volume: int = 100000) -> Candle:
    return Candle(open=open_, high=high, low=low, close=close, volume=volume)


class TestComputeStockScore:
    def test_basic_scoring(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        rs_pct = {"TEST": 80.0}
        result = _compute_stock_score("TEST", candles, rs_pct, date(2026, 4, 27))
        assert result is not None
        assert result["symbol"] == "TEST"
        assert 0 < result["composite_score"] <= 100
        assert result["price"] == 102

    def test_filters_low_price(self):
        candles = [_candle(50, 55, 45, 50) for _ in range(60)]
        result = _compute_stock_score("PENNY", candles, {}, date(2026, 4, 27))
        assert result is None

    def test_empty_candles(self):
        result = _compute_stock_score("EMPTY", [], {}, date(2026, 4, 27))
        assert result is None

    def test_zero_close(self):
        candles = [_candle(100, 105, 95, 0)]
        result = _compute_stock_score("ZERO", candles, {}, date(2026, 4, 27))
        assert result is None

    def test_includes_factors(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score("TEST", candles, {"TEST": 75.0}, date(2026, 4, 27))
        assert result is not None
        assert "rs_percentile" in result["factors"]
        assert "adr_pct" in result["factors"]
        assert "sector" in result["factors"]
        assert result["factors"]["rs_percentile"] == 75.0

    def test_high_rs_boosts_score(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        low_rs = _compute_stock_score("A", candles, {"A": 20.0}, date(2026, 4, 27))
        high_rs = _compute_stock_score("B", candles, {"B": 95.0}, date(2026, 4, 27))
        assert high_rs["composite_score"] > low_rs["composite_score"]

    def test_bias_from_previous_day(self):
        candles = [_candle(100, 110, 90, 109, 50000) for _ in range(60)]
        result = _compute_stock_score("BULL", candles, {}, date(2026, 4, 27))
        assert result is not None
        assert result["bias"] in ("BULLISH", "BEARISH", "NEUTRAL")

    def test_pdh_pdl_pdc_populated(self):
        candles = [_candle(100, 110, 90, 105, 50000) for _ in range(60)]
        result = _compute_stock_score("TEST", candles, {}, date(2026, 4, 27))
        assert result is not None
        assert result["pdh"] is not None
        assert result["pdl"] is not None
        assert result["pdc"] is not None


class TestOIScoring:
    """OI change scoring from oi_snapshots FUT rows."""

    def _score_with_oi(self, oi_change: int, price_up: bool) -> float:
        if price_up:
            candles = [_candle(200, 210, 195, 200, 50000) for _ in range(59)]
            candles.append(_candle(200, 212, 195, 205, 50000))  # close > prev close
        else:
            candles = [_candle(200, 210, 195, 200, 50000) for _ in range(59)]
            candles.append(_candle(200, 210, 190, 198, 50000))  # close < prev close
        result = _compute_stock_score(
            "TEST", candles, {}, date(2026, 4, 27),
            oi_change_data={"oi_change": oi_change, "latest_oi": 1000000},
        )
        return result["factors"]["oi_change"]

    def test_long_buildup(self):
        score = self._score_with_oi(oi_change=50000, price_up=True)
        assert score == 100.0

    def test_short_buildup(self):
        score = self._score_with_oi(oi_change=50000, price_up=False)
        assert score == 50.0

    def test_short_covering(self):
        score = self._score_with_oi(oi_change=-50000, price_up=True)
        assert score == 30.0

    def test_long_unwinding(self):
        score = self._score_with_oi(oi_change=-50000, price_up=False)
        assert score == 20.0

    def test_fallback_without_data(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score("TEST", candles, {}, date(2026, 4, 27))
        assert result["factors"]["oi_change"] == 50.0


class TestDeliveryScoring:
    """Delivery % scoring from NSE bhav copy."""

    def test_high_delivery(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score(
            "TEST", candles, {}, date(2026, 4, 27),
            bhav_data={"delivery_pct": 50.0, "close": 102.0, "prev_close": 100.0},
        )
        assert result["factors"]["delivery_pct"] == 100.0

    def test_mid_delivery(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score(
            "TEST", candles, {}, date(2026, 4, 27),
            bhav_data={"delivery_pct": 30.0, "close": 102.0, "prev_close": 100.0},
        )
        assert result["factors"]["delivery_pct"] == 50.0

    def test_low_delivery(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score(
            "TEST", candles, {}, date(2026, 4, 27),
            bhav_data={"delivery_pct": 10.0, "close": 102.0, "prev_close": 100.0},
        )
        assert result["factors"]["delivery_pct"] == 0.0

    def test_fallback_without_data(self):
        candles = [_candle(100, 105, 95, 102, 50000) for _ in range(60)]
        result = _compute_stock_score("TEST", candles, {}, date(2026, 4, 27))
        assert result["factors"]["delivery_pct"] == 50.0


class TestComputeTradeStats:
    def test_empty(self):
        stats = _compute_trade_stats([])
        assert stats["trade_count"] == 0
        assert stats["win_rate"] == 0.0

    def test_mixed_trades(self):
        t1 = MagicMock(pnl=1000)
        t2 = MagicMock(pnl=-500)
        t3 = MagicMock(pnl=200)
        stats = _compute_trade_stats([t1, t2, t3])
        assert stats["trade_count"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["net_pnl"] == 700.0
        assert stats["win_rate"] == 66.7

    def test_all_losses(self):
        trades = [MagicMock(pnl=-100) for _ in range(3)]
        stats = _compute_trade_stats(trades)
        assert stats["win_rate"] == 0.0
        assert stats["net_pnl"] == -300.0


class TestSnapshotGlobalCues:
    @pytest.mark.asyncio
    async def test_halts_on_high_vix(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=lambda k: {
            "strat5:global_cues:2026-04-27": None,
            "indicator:global:dow_futures_pct": "0.5",
            "indicator:global:sp500_close_pct": "0.3",
            "indicator:global:nasdaq_close_pct": "0.4",
            "indicator:global:nifty_pct": "0.2",
            "indicator:global:crude_pct": "-0.1",
            "indicator:global:usdinr_pct": "0.05",
            "indicator:global:dxy_pct": "0.1",
            "indicator:global:us_vix": "22.0",
        }.get(k))
        mock_redis.set = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.expire = AsyncMock()

        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            cues = await snapshot_global_cues(date(2026, 4, 27))

        assert cues["halted"] is True
        assert cues["us_vix"] == 22.0
        # Verify agent status was set to HALTED
        mock_redis.set.assert_any_call(
            "strat5:agent_status:2026-04-27", "HALTED", ex=90 * 86400
        )

    @pytest.mark.asyncio
    async def test_flags_volatile_open(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=lambda k: {
            "strat5:global_cues:2026-04-27": None,
            "indicator:global:nifty_pct": "1.5",
            "indicator:global:us_vix": "15.0",
        }.get(k))
        mock_redis.set = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.expire = AsyncMock()

        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            cues = await snapshot_global_cues(date(2026, 4, 27))

        assert cues["volatile_open"] is True
        assert cues["halted"] is False

    @pytest.mark.asyncio
    async def test_returns_cached(self):
        cached = json.dumps({"date": "2026-04-27", "halted": False})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached)

        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            cues = await snapshot_global_cues(date(2026, 4, 27))

        assert cues["date"] == "2026-04-27"


class TestRedisReaders:
    @pytest.mark.asyncio
    async def test_get_watchlist_empty(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await get_watchlist("2026-04-27")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_watchlist_with_data(self):
        data = [{"symbol": "TCS", "score": 75}]
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(data))
        mock_pipe = AsyncMock()
        mock_pipe.get = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(return_value=[json.dumps({"high": 100.5, "low": 98.0})])
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await get_watchlist("2026-04-27")
        assert len(result) == 1
        assert result[0]["symbol"] == "TCS"
        assert result[0]["orb_high"] == 100.5
        assert result[0]["orb_low"] == 98.0
        assert result[0]["orb_range"] == 2.5

    @pytest.mark.asyncio
    async def test_get_morning_briefing_empty(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await get_morning_briefing("2026-04-27")
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_agent_log(self):
        entries = [json.dumps({"category": "SCREENER", "message": "test"})]
        mock_redis = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=entries)
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await get_agent_log("2026-04-27")
        assert len(result) == 1
        assert result[0]["category"] == "SCREENER"

    @pytest.mark.asyncio
    async def test_agent_status_default(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            status = await get_agent_status("2026-04-27")
        assert status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_set_agent_status(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.expire = AsyncMock()
        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            await set_agent_status("2026-04-27", "PAUSED")
        mock_redis.set.assert_any_call(
            "strat5:agent_status:2026-04-27", "PAUSED", ex=90 * 86400
        )


class TestBriefingSchema:
    """Validate the briefing schema structure matches what the prompt expects."""

    def test_required_fields(self):
        assert set(_BRIEFING_SCHEMA["required"]) == {
            "approach", "summary", "max_lots_recommendation",
        }

    def test_approach_enum(self):
        approach = _BRIEFING_SCHEMA["properties"]["approach"]
        assert set(approach["enum"]) == {"aggressive", "normal", "conservative"}

    def test_array_fields(self):
        assert _BRIEFING_SCHEMA["properties"]["setup_priority"]["type"] == "array"
        assert _BRIEFING_SCHEMA["properties"]["flags"]["type"] == "array"


class TestStage3ConfidenceSchema:
    """Validate the Stage 3 confidence schema structure."""

    def test_required_fields(self):
        assert set(_STAGE3_CONFIDENCE_SCHEMA["required"]) == {
            "ratings", "correlated_groups",
        }

    def test_ratings_array_items(self):
        ratings = _STAGE3_CONFIDENCE_SCHEMA["properties"]["ratings"]
        assert ratings["type"] == "array"
        per_stock = ratings["items"]
        assert set(per_stock["required"]) == {"symbol", "confidence", "reason"}
        assert set(per_stock["properties"]["confidence"]["enum"]) == {
            "HIGH", "MEDIUM", "LOW",
        }

    def test_correlated_groups_shape(self):
        groups = _STAGE3_CONFIDENCE_SCHEMA["properties"]["correlated_groups"]
        item = groups["items"]
        assert set(item["required"]) == {"symbols", "sector", "keep"}


class TestSynthesizeBriefing:
    """Tests for _synthesize_briefing LLM call parameters."""

    @pytest.mark.asyncio
    async def test_passes_schema_and_max_tokens(self):
        """Verify response_schema and max_tokens=4096 are passed to generate_json."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={
            "approach": "normal",
            "summary": "VIX stable at 15.2, net +5K yesterday.",
            "sector_bias": "METALS",
            "sector_avoid": "none",
            "setup_priority": ["ORB"],
            "flags": [],
            "max_lots_recommendation": 2,
        })

        data = {
            "yesterday": {"trades": 3, "pnl": 5000},
            "recent_5d": {"trades": 12, "pnl": 8000},
        }

        result = await _synthesize_briefing(mock_llm, data)

        assert result["approach"] == "normal"
        assert result["summary"] == "VIX stable at 15.2, net +5K yesterday."

        call_kwargs = mock_llm.generate_json.call_args[1]
        assert call_kwargs["max_tokens"] == 4096
        assert call_kwargs["response_schema"] is _BRIEFING_SCHEMA

    @pytest.mark.asyncio
    async def test_fallback_on_empty_summary(self):
        """When LLM returns empty summary, fallback text is used."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={
            "approach": "aggressive",
        })

        data = {"yesterday": {}, "recent_5d": {}}
        result = await _synthesize_briefing(mock_llm, data)

        assert result["summary"] == "No briefing available."
        assert result["approach"] == "aggressive"

    @pytest.mark.asyncio
    async def test_fallback_on_exception(self):
        """When generate_json raises, hardcoded fallback is returned."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(side_effect=RuntimeError("Gemini down"))

        data = {"yesterday": {}, "recent_5d": {}, "consecutive_losses": 4}
        result = await _synthesize_briefing(mock_llm, data)

        assert result["approach"] == "conservative"
        assert "llm_unavailable" in result["flags"]
        assert result["max_lots_recommendation"] == 1
