"""Tests for candlestick pattern detection."""

from app.indicators.candle_patterns import (
    Candle,
    average_volume,
    is_bearish_engulfing,
    is_bearish_pin_bar,
    is_bearish_reversal,
    is_bullish_engulfing,
    is_bullish_pin_bar,
    is_bullish_reversal,
    is_doji,
)


# ---------------------------------------------------------------------------
# Bullish engulfing
# ---------------------------------------------------------------------------

class TestBullishEngulfing:

    def test_classic_bullish_engulfing(self):
        """Prev bearish, curr bullish that engulfs prev body."""
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)  # bearish
        curr = Candle(open=99, high=108, low=98, close=107, volume=1500)   # bullish, engulfs
        assert is_bullish_engulfing(prev, curr)

    def test_not_bullish_engulfing_prev_bullish(self):
        """Previous candle is bullish => not bullish engulfing."""
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)  # bullish
        curr = Candle(open=99, high=108, low=98, close=107, volume=1500)
        assert not is_bullish_engulfing(prev, curr)

    def test_not_bullish_engulfing_curr_bearish(self):
        """Current candle is bearish => not bullish engulfing."""
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)
        curr = Candle(open=107, high=108, low=98, close=99, volume=1500)   # bearish
        assert not is_bullish_engulfing(prev, curr)

    def test_not_bullish_engulfing_no_engulf(self):
        """Current doesn't fully engulf previous body."""
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)  # body: 100-105
        curr = Candle(open=101, high=108, low=100, close=104, volume=1500) # open > prev.close
        assert not is_bullish_engulfing(prev, curr)

    def test_bullish_engulfing_exact_boundary(self):
        """curr.open == prev.close and curr.close == prev.open => engulfs."""
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)
        curr = Candle(open=100, high=106, low=99, close=105, volume=1500)
        assert is_bullish_engulfing(prev, curr)


# ---------------------------------------------------------------------------
# Bearish engulfing
# ---------------------------------------------------------------------------

class TestBearishEngulfing:

    def test_classic_bearish_engulfing(self):
        """Prev bullish, curr bearish that engulfs prev body."""
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)  # bullish
        curr = Candle(open=106, high=107, low=98, close=99, volume=1500)   # bearish, engulfs
        assert is_bearish_engulfing(prev, curr)

    def test_not_bearish_engulfing_prev_bearish(self):
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)  # bearish
        curr = Candle(open=106, high=107, low=98, close=99, volume=1500)
        assert not is_bearish_engulfing(prev, curr)

    def test_not_bearish_engulfing_curr_bullish(self):
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)
        curr = Candle(open=99, high=108, low=98, close=107, volume=1500)  # bullish
        assert not is_bearish_engulfing(prev, curr)

    def test_bearish_engulfing_exact_boundary(self):
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)
        curr = Candle(open=105, high=106, low=99, close=100, volume=1500)
        assert is_bearish_engulfing(prev, curr)


# ---------------------------------------------------------------------------
# Bullish pin bar
# ---------------------------------------------------------------------------

class TestBullishPinBar:

    def test_classic_bullish_pin_bar(self):
        """Long lower wick, small body at top, tiny upper wick."""
        # body = |102-100| = 2, lower_wick = 100-90 = 10, upper_wick = 103-102 = 1
        # lower_wick(10) >= 2*body(4) and upper_wick(1) <= body(2) => True
        candle = Candle(open=100, high=103, low=90, close=102, volume=1000)
        assert is_bullish_pin_bar(candle)

    def test_not_bullish_pin_bar_short_lower_wick(self):
        """Lower wick too short."""
        candle = Candle(open=100, high=103, low=99, close=102, volume=1000)
        assert not is_bullish_pin_bar(candle)

    def test_not_bullish_pin_bar_large_upper_wick(self):
        """Upper wick too large relative to body."""
        # body = 2, lower_wick = 10, upper_wick = 5 > body(2)
        candle = Candle(open=100, high=107, low=90, close=102, volume=1000)
        assert not is_bullish_pin_bar(candle)

    def test_zero_body_returns_false(self):
        """Open == close => body = 0 => False (guard)."""
        candle = Candle(open=100, high=110, low=90, close=100, volume=1000)
        assert not is_bullish_pin_bar(candle)

    def test_bearish_body_pin_bar(self):
        """Pin bar with bearish body (close < open) still valid if lower wick qualifies."""
        # body = |102-100| = 2, lower_wick = min(102,100)-90 = 10, upper_wick = 103-102 = 1
        candle = Candle(open=102, high=103, low=90, close=100, volume=1000)
        assert is_bullish_pin_bar(candle)


# ---------------------------------------------------------------------------
# Bearish pin bar
# ---------------------------------------------------------------------------

class TestBearishPinBar:

    def test_classic_bearish_pin_bar(self):
        """Long upper wick, small body at bottom, tiny lower wick."""
        # body = |102-100| = 2, upper_wick = 112-102 = 10, lower_wick = 100-99 = 1
        candle = Candle(open=102, high=112, low=99, close=100, volume=1000)
        assert is_bearish_pin_bar(candle)

    def test_not_bearish_pin_bar_short_upper_wick(self):
        candle = Candle(open=102, high=103, low=99, close=100, volume=1000)
        assert not is_bearish_pin_bar(candle)

    def test_not_bearish_pin_bar_large_lower_wick(self):
        # body=2, upper_wick=10, lower_wick=5 > body
        candle = Candle(open=102, high=112, low=95, close=100, volume=1000)
        assert not is_bearish_pin_bar(candle)

    def test_zero_body_returns_false(self):
        candle = Candle(open=100, high=110, low=90, close=100, volume=1000)
        assert not is_bearish_pin_bar(candle)


# ---------------------------------------------------------------------------
# Doji
# ---------------------------------------------------------------------------

class TestDoji:

    def test_classic_doji(self):
        """Body is 0 relative to a non-zero range."""
        candle = Candle(open=100, high=105, low=95, close=100, volume=1000)
        assert is_doji(candle)

    def test_near_doji(self):
        """Body is tiny relative to range: body/range = 0.2/10 = 0.02 <= 0.05."""
        candle = Candle(open=100, high=105, low=95, close=100.2, volume=1000)
        assert is_doji(candle)

    def test_not_doji(self):
        """Body is large relative to range."""
        candle = Candle(open=96, high=105, low=95, close=104, volume=1000)
        assert not is_doji(candle)

    def test_zero_range_is_doji(self):
        """All OHLC same => range=0 => returns True."""
        candle = Candle(open=100, high=100, low=100, close=100, volume=1000)
        assert is_doji(candle)

    def test_custom_threshold(self):
        """With a very strict threshold, a near-doji may not qualify."""
        candle = Candle(open=100, high=105, low=95, close=100.2, volume=1000)
        # body/range = 0.02, threshold=0.01 => not doji
        assert not is_doji(candle, threshold_pct=0.01)

    def test_equal_open_close(self):
        candle = Candle(open=100, high=110, low=90, close=100, volume=500)
        assert is_doji(candle)


# ---------------------------------------------------------------------------
# Bullish reversal (composite)
# ---------------------------------------------------------------------------

class TestBullishReversal:

    def test_via_bullish_engulfing(self):
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)
        curr = Candle(open=99, high=108, low=98, close=107, volume=1500)
        assert is_bullish_reversal([prev, curr])

    def test_via_bullish_pin_bar(self):
        prev = Candle(open=100, high=101, low=99, close=100.5, volume=1000)
        curr = Candle(open=100, high=103, low=90, close=102, volume=1500)  # pin bar
        assert is_bullish_reversal([prev, curr])

    def test_via_doji_followed_by_bullish(self):
        prev = Candle(open=100, high=105, low=95, close=100, volume=1000)  # doji
        curr = Candle(open=100, high=104, low=99, close=103, volume=1500)  # bullish
        assert is_bullish_reversal([prev, curr])

    def test_no_reversal(self):
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)  # bullish
        curr = Candle(open=104, high=106, low=99, close=103, volume=1500)  # mild bearish
        assert not is_bullish_reversal([prev, curr])

    def test_insufficient_candles(self):
        candle = Candle(open=100, high=105, low=95, close=102, volume=1000)
        assert not is_bullish_reversal([candle])
        assert not is_bullish_reversal([])

    def test_uses_last_two_candles(self):
        """Only the last 2 candles matter, prior ones are ignored."""
        old = Candle(open=50, high=55, low=45, close=52, volume=500)
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)
        curr = Candle(open=99, high=108, low=98, close=107, volume=1500)
        assert is_bullish_reversal([old, prev, curr])


# ---------------------------------------------------------------------------
# Bearish reversal (composite)
# ---------------------------------------------------------------------------

class TestBearishReversal:

    def test_via_bearish_engulfing(self):
        prev = Candle(open=100, high=106, low=99, close=105, volume=1000)
        curr = Candle(open=106, high=107, low=98, close=99, volume=1500)
        assert is_bearish_reversal([prev, curr])

    def test_via_bearish_pin_bar(self):
        prev = Candle(open=100, high=101, low=99, close=100.5, volume=1000)
        curr = Candle(open=102, high=112, low=99, close=100, volume=1500)  # bearish pin bar
        assert is_bearish_reversal([prev, curr])

    def test_via_doji_followed_by_bearish(self):
        prev = Candle(open=100, high=105, low=95, close=100, volume=1000)  # doji
        curr = Candle(open=102, high=103, low=97, close=98, volume=1500)   # bearish
        assert is_bearish_reversal([prev, curr])

    def test_no_reversal(self):
        prev = Candle(open=105, high=106, low=99, close=100, volume=1000)  # bearish
        curr = Candle(open=100, high=102, low=99, close=101, volume=1500)  # mild bullish
        assert not is_bearish_reversal([prev, curr])

    def test_insufficient_candles(self):
        assert not is_bearish_reversal([])
        assert not is_bearish_reversal([Candle(100, 105, 95, 102, 500)])


# ---------------------------------------------------------------------------
# average_volume
# ---------------------------------------------------------------------------

class TestAverageVolume:

    def test_basic(self):
        candles = [
            Candle(open=100, high=105, low=95, close=102, volume=1000),
            Candle(open=100, high=105, low=95, close=102, volume=2000),
            Candle(open=100, high=105, low=95, close=102, volume=3000),
        ]
        assert average_volume(candles) == 2000.0

    def test_empty_candles(self):
        assert average_volume([]) == 0

    def test_periods_larger_than_list(self):
        candles = [
            Candle(open=100, high=105, low=95, close=102, volume=600),
            Candle(open=100, high=105, low=95, close=102, volume=400),
        ]
        # periods=20 but only 2 candles => uses all 2
        assert average_volume(candles, periods=20) == 500.0

    def test_periods_subset(self):
        candles = [
            Candle(open=100, high=105, low=95, close=102, volume=100),
            Candle(open=100, high=105, low=95, close=102, volume=200),
            Candle(open=100, high=105, low=95, close=102, volume=300),
            Candle(open=100, high=105, low=95, close=102, volume=400),
        ]
        # periods=2 => last 2 candles: 300, 400 => avg = 350
        assert average_volume(candles, periods=2) == 350.0

    def test_single_candle(self):
        candles = [Candle(open=100, high=105, low=95, close=102, volume=777)]
        assert average_volume(candles) == 777.0
