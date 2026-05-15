"""Tests for pre-open reassessment (gap-adjusted bias, watchlist re-ranking)."""

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services.morning_screener import (
    _compute_gap_adjusted_bias,
    _compute_gap_alignment_bonus,
    run_preopen_reassessment,
)


def _find_redis_set(mock_redis, key_substring: str) -> dict | list | None:
    """Find a specific Redis set call by key substring and parse its JSON value."""
    for call in mock_redis.set.call_args_list:
        if key_substring in call[0][0]:
            return json.loads(call[0][1])
    return None


# ---------------------------------------------------------------------------
# Unit tests: _compute_gap_adjusted_bias
# ---------------------------------------------------------------------------

class TestComputeGapAdjustedBias:
    def test_hard_override_bullish(self):
        assert _compute_gap_adjusted_bias("BEARISH", 1.5) == "BULLISH"

    def test_hard_override_bearish(self):
        assert _compute_gap_adjusted_bias("BULLISH", -1.2) == "BEARISH"

    def test_hard_override_at_threshold(self):
        assert _compute_gap_adjusted_bias("NEUTRAL", 1.0) == "BULLISH"
        assert _compute_gap_adjusted_bias("NEUTRAL", -1.0) == "BEARISH"

    def test_nudge_conflicting_to_neutral(self):
        assert _compute_gap_adjusted_bias("BEARISH", 0.7) == "NEUTRAL"
        assert _compute_gap_adjusted_bias("BULLISH", -0.6) == "NEUTRAL"

    def test_nudge_neutral_to_gap_direction(self):
        assert _compute_gap_adjusted_bias("NEUTRAL", 0.8) == "BULLISH"
        assert _compute_gap_adjusted_bias("NEUTRAL", -0.5) == "BEARISH"

    def test_nudge_aligned_stays(self):
        assert _compute_gap_adjusted_bias("BULLISH", 0.7) == "BULLISH"
        assert _compute_gap_adjusted_bias("BEARISH", -0.6) == "BEARISH"

    def test_small_gap_no_change(self):
        assert _compute_gap_adjusted_bias("BEARISH", 0.3) == "BEARISH"
        assert _compute_gap_adjusted_bias("BULLISH", -0.2) == "BULLISH"
        assert _compute_gap_adjusted_bias("NEUTRAL", 0.1) == "NEUTRAL"

    def test_zero_gap_no_change(self):
        assert _compute_gap_adjusted_bias("BULLISH", 0.0) == "BULLISH"
        assert _compute_gap_adjusted_bias("BEARISH", 0.0) == "BEARISH"
        assert _compute_gap_adjusted_bias("NEUTRAL", 0.0) == "NEUTRAL"


# ---------------------------------------------------------------------------
# Unit tests: _compute_gap_alignment_bonus
# ---------------------------------------------------------------------------

class TestComputeGapAlignmentBonus:
    def test_aligned_bullish_gap_up(self):
        bonus = _compute_gap_alignment_bonus("BULLISH", 1.5)
        assert bonus == 3.0  # min(5.0, 1.5*2)

    def test_aligned_bearish_gap_down(self):
        bonus = _compute_gap_alignment_bonus("BEARISH", -2.0)
        assert bonus == 4.0  # min(5.0, 2.0*2)

    def test_capped_at_5(self):
        bonus = _compute_gap_alignment_bonus("BULLISH", 5.0)
        assert bonus == 5.0

    def test_misaligned_returns_zero(self):
        assert _compute_gap_alignment_bonus("BULLISH", -1.0) == 0.0
        assert _compute_gap_alignment_bonus("BEARISH", 1.0) == 0.0

    def test_neutral_returns_zero(self):
        assert _compute_gap_alignment_bonus("NEUTRAL", 2.0) == 0.0
        assert _compute_gap_alignment_bonus("NEUTRAL", -2.0) == 0.0

    def test_zero_gap_returns_zero(self):
        assert _compute_gap_alignment_bonus("BULLISH", 0.0) == 0.0


# ---------------------------------------------------------------------------
# Integration tests: run_preopen_reassessment
# ---------------------------------------------------------------------------

def _make_watchlist_item(symbol: str, score: float, bias: str, pdc: float) -> dict:
    return {
        "symbol": symbol,
        "composite_score": score,
        "bias": bias,
        "price": pdc + 10,
        "pdc": pdc,
        "lot_size": 100,
        "factors": {
            "rs_percentile": 70, "range_position": 60, "volume_trend": 50,
            "oi_change": 0, "adr_pct": 2.0, "adr_qualifies": True,
            "sector": None, "sector_score": 50, "delivery_pct": 0, "high_52w_proximity": 0,
        },
    }


def _mock_quotes_response(stocks: dict, nifty_ltp: float, nifty_prev: float, vix: float) -> dict:
    """Build a Fyers quotes response."""
    data = []
    for fyers_sym, (ltp, prev) in stocks.items():
        data.append({
            "v": {"symbol": fyers_sym, "lp": ltp, "prev_close_price": prev, "volume": 100000, "open_price": ltp},
        })
    data.append({
        "v": {"symbol": "NSE:NIFTY50-INDEX", "lp": nifty_ltp, "prev_close_price": nifty_prev, "volume": 0, "open_price": nifty_ltp},
    })
    data.append({
        "v": {"symbol": "NSE:INDIAVIX-INDEX", "lp": vix, "prev_close_price": vix - 0.5, "volume": 0, "open_price": vix},
    })
    return {"d": data}


@pytest.mark.asyncio
class TestRunPreopenReassessment:
    async def test_no_watchlist_skips(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert result["skipped"] is True
        assert result["reason"] == "no_watchlist"

    async def test_empty_watchlist_skips(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps([]))

        with patch("app.services.morning_screener.get_redis", return_value=mock_redis):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert result["skipped"] is True
        assert result["reason"] == "empty_watchlist"

    async def test_pdc_none_skips_stock_without_crash(self):
        """Permanent watchlist injected stocks have pdc=None — should be skipped, not crash."""
        watchlist = [
            _make_watchlist_item("REGULARSTOCK", 65.0, "BULLISH", 500.0),
            {  # Injected permanent stock with pdc=None (no daily candle data)
                "symbol": "NEWSTOCK",
                "composite_score": 0.0,
                "bias": "NEUTRAL",
                "price": 0,
                "pdc": None,
                "lot_size": 0,
                "manual": True,
                "factors": {},
            },
        ]
        mock_redis = AsyncMock()
        cues = {"india_vix": 14.5}

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps(cues)
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        quotes = _mock_quotes_response(
            stocks={"NSE:REGULARSTOCK-EQ": (505.0, 500.0)},
            nifty_ltp=24000.0, nifty_prev=24000.0,
            vix=14.0,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            # Must not raise TypeError: '<=' not supported between instances of 'NoneType' and 'int'
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert "error" not in result
        assert result.get("skipped") is not True

    async def test_bias_override_on_large_relative_gap(self):
        watchlist = [
            _make_watchlist_item("SUNPHARMA", 65.0, "BEARISH", 1800.0),
        ]
        mock_redis = AsyncMock()
        cues = {"india_vix": 14.5}

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps(cues)
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        quotes = _mock_quotes_response(
            stocks={"NSE:SUNPHARMA-EQ": (1830.0, 1800.0)},
            nifty_ltp=24100.0, nifty_prev=24000.0,  # Nifty +0.42%
            vix=14.2,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert result["bias_changes"] == 1
        assert "SUNPHARMA" in result["changes"][0]
        assert "BEARISH → BULLISH" in result["changes"][0]

        written_wl = _find_redis_set(mock_redis, "watchlist")
        item = written_wl[0]
        assert item["bias"] == "BULLISH"
        assert item["original_bias"] == "BEARISH"
        assert item["bias_source"] == "preopen_gap"
        assert item["gap_pct"] > 0

    async def test_no_change_when_gap_below_threshold(self):
        watchlist = [
            _make_watchlist_item("TCS", 70.0, "BULLISH", 3500.0),
        ]
        mock_redis = AsyncMock()

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps({})
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        quotes = _mock_quotes_response(
            stocks={"NSE:TCS-EQ": (3505.0, 3500.0)},
            nifty_ltp=24050.0, nifty_prev=24000.0,  # Nifty +0.21%
            vix=13.5,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert result["bias_changes"] == 0

    async def test_gap_alignment_bonus_applied(self):
        watchlist = [
            _make_watchlist_item("ADANIPORTS", 72.0, "BULLISH", 1200.0),
        ]
        mock_redis = AsyncMock()

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps({})
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        quotes = _mock_quotes_response(
            stocks={"NSE:ADANIPORTS-EQ": (1224.0, 1200.0)},  # +2%
            nifty_ltp=24050.0, nifty_prev=24000.0,  # +0.21%
            vix=14.0,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        written_wl = _find_redis_set(mock_redis, "watchlist")
        item = written_wl[0]
        assert item["gap_alignment_bonus"] > 0
        assert item["composite_score"] > 72.0

    async def test_vix_updated_in_global_cues(self):
        watchlist = [_make_watchlist_item("TCS", 70.0, "BULLISH", 3500.0)]
        cues = {"india_vix": 14.5}
        mock_redis = AsyncMock()

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps(cues)
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        quotes = _mock_quotes_response(
            stocks={"NSE:TCS-EQ": (3510.0, 3500.0)},
            nifty_ltp=24050.0, nifty_prev=24000.0,
            vix=15.8,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        assert result["live_vix"] == 15.8

        cues_written = _find_redis_set(mock_redis, "global_cues")
        assert cues_written.get("india_vix_live") == 15.8
        assert cues_written.get("nifty_gap_pct") is not None

    async def test_watchlist_re_sorted_by_score(self):
        watchlist = [
            _make_watchlist_item("STOCKA", 70.0, "BULLISH", 500.0),
            _make_watchlist_item("STOCKB", 60.0, "BEARISH", 800.0),
        ]
        mock_redis = AsyncMock()

        async def mock_get(key):
            if "watchlist" in key:
                return json.dumps(watchlist)
            if "global_cues" in key:
                return json.dumps({})
            if "agent_log" in key:
                return json.dumps([])
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        mock_redis.set = AsyncMock()

        # STOCKA gaps up +3% (aligned with BULLISH bias → big bonus)
        # STOCKB gaps down -0.2% (below threshold → no bonus, no override)
        quotes = _mock_quotes_response(
            stocks={
                "NSE:STOCKA-EQ": (515.0, 500.0),
                "NSE:STOCKB-EQ": (798.4, 800.0),
            },
            nifty_ltp=24000.0, nifty_prev=24000.0,  # Nifty flat
            vix=14.0,
        )
        mock_client = AsyncMock()
        mock_client.get_quotes = AsyncMock(return_value=quotes)

        with (
            patch("app.services.morning_screener.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener.FyersClient", return_value=mock_client),
        ):
            result = await run_preopen_reassessment(date(2026, 4, 28))

        written_wl = _find_redis_set(mock_redis, "watchlist")
        # STOCKA (70 + gap bonus ~5) should stay ahead of STOCKB (60 + no bonus)
        assert written_wl[0]["symbol"] == "STOCKA"
        assert written_wl[0]["composite_score"] > written_wl[1]["composite_score"]
