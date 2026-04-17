"""Strategy 2: VWAP Pullback + Previous Day Context + OI Confirmation.

Primary strategy. Combines:
1. Previous day analysis for directional bias
2. VWAP pullback for entry timing
3. Open Interest data for institutional confirmation
4. Candlestick patterns for entry confirmation

See docs/strategies/strategy-2-vwap-pullback.md for full specification.
"""

import logging

from app.core.constants import VWAP_PROXIMITY_PCT
from app.core.enums import DayBias, InstrumentType, SignalType, StrategyName
from app.indicators.candle_patterns import (
    average_volume,
    is_bearish_reversal,
    is_bullish_reversal,
)
from app.indicators.market_levels import select_index_sl_target
from app.indicators.open_interest import is_oi_supporting_direction
from app.indicators.vwap import is_pullback_to_vwap, price_distance_from_vwap
from app.strategies.base import (
    BaseStrategy,
    ExitSignal,
    MarketContext,
    StrategySignal,
)

logger = logging.getLogger(__name__)


class VWAPPullbackStrategy(BaseStrategy):
    name = StrategyName.VWAP_PULLBACK

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Evaluate VWAP pullback entry conditions."""

        # Need all indicators
        if not ctx.vwap or not ctx.previous_day or not ctx.cpr:
            logger.debug("Missing indicators for %s", ctx.symbol)
            return None

        if len(ctx.candles_5m) < 5:
            return None

        vwap = ctx.vwap.vwap
        price = ctx.current_price
        bias = ctx.previous_day.bias

        # Check VWAP proximity
        if not is_pullback_to_vwap(price, vwap, VWAP_PROXIMITY_PCT):
            return None

        distance = price_distance_from_vwap(price, vwap)

        # Determine direction based on bias + price vs VWAP
        signal = None

        if bias in (DayBias.BULLISH, DayBias.NEUTRAL) and distance > 0:
            # Price above VWAP, pulling back — look for bullish reversal
            signal = self._evaluate_call(ctx, vwap, distance)
        elif bias in (DayBias.BEARISH, DayBias.NEUTRAL) and distance < 0:
            # Price below VWAP, rallying back — look for bearish reversal
            signal = self._evaluate_put(ctx, vwap, distance)

        return signal

    def _evaluate_call(
        self, ctx: MarketContext, vwap: float, distance: float
    ) -> StrategySignal | None:
        """Evaluate conditions for a CALL buy."""

        # 1. Bullish reversal pattern at VWAP
        if not is_bullish_reversal(ctx.candles_5m):
            return None

        # 2. Volume check: pullback should have below-average volume
        avg_vol = average_volume(ctx.candles_5m, periods=20)
        curr_vol = ctx.candles_5m[-1].volume
        if avg_vol > 0 and curr_vol > avg_vol * 1.2:
            # High volume pullback = not a healthy pullback
            return None

        # 3. OI confirmation
        oi_confirmed = True
        confidence = 70.0
        if ctx.oi_analysis:
            oi_confirmed = is_oi_supporting_direction(
                ctx.current_price,
                ctx.oi_analysis.max_pe_oi_strike,
                ctx.oi_analysis.max_ce_oi_strike,
                "CALL",
            )
            if not oi_confirmed:
                confidence -= 20
                # Soft filter: still allow but with lower confidence

        # 4. Adjust confidence based on conditions
        if ctx.previous_day and ctx.previous_day.bias == DayBias.BULLISH:
            confidence += 10
        if ctx.cpr and ctx.cpr.cpr_type.value == "NARROW":
            confidence += 5  # Trending day = better for directional trades
        if ctx.india_vix and ctx.india_vix < 14:
            confidence += 5  # Cheap options

        # Compute index-level SL/target from market structure
        index_sl, index_target = select_index_sl_target(
            entry_price=ctx.current_price,
            signal_type=SignalType.BUY_CE,
            vwap=ctx.vwap,
            previous_day=ctx.previous_day,
            cpr=ctx.cpr,
            oi_analysis=ctx.oi_analysis,
            candles_5m=ctx.candles_5m,
        )

        # Build indicator snapshot
        indicators = self._build_indicator_snapshot(ctx, vwap, distance)

        if index_sl is not None and index_target is not None:
            indicators["index_sl"] = index_sl
            indicators["index_target"] = index_target
        else:
            # Fallback to fixed percentages when market structure is insufficient
            sl_pct = 0.30 if ctx.previous_day.bias == DayBias.BULLISH else 0.35
            indicators["sl_pct"] = sl_pct
            indicators["rr_multiplier"] = 1.5

        reason = (
            f"VWAP pullback BUY CE: {ctx.symbol} at {ctx.current_price:.2f}. "
            f"Bias: {ctx.previous_day.bias.value}. "
            f"VWAP: {vwap:.2f} (dist: {distance:.3f}%). "
            f"Bullish reversal confirmed. "
            f"OI: {'confirmed' if oi_confirmed else 'weak'}."
        )

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=SignalType.BUY_CE,
            instrument_type=InstrumentType.OPTION,
            strike_price=0,  # Resolved by option_resolver
            expiry_date=None,  # Resolved by option_resolver
            entry_price=ctx.current_price,  # Placeholder; replaced with premium
            stop_loss=0,  # Resolved by option_resolver
            target_price=None,  # Resolved by option_resolver
            confidence=min(confidence, 100),
            reason=reason,
            indicators=indicators,
            index_sl=index_sl,
            index_target=index_target,
        )

    def _evaluate_put(
        self, ctx: MarketContext, vwap: float, distance: float
    ) -> StrategySignal | None:
        """Evaluate conditions for a PUT buy."""

        # 1. Bearish reversal pattern at VWAP
        if not is_bearish_reversal(ctx.candles_5m):
            return None

        # 2. Volume check
        avg_vol = average_volume(ctx.candles_5m, periods=20)
        curr_vol = ctx.candles_5m[-1].volume
        if avg_vol > 0 and curr_vol > avg_vol * 1.2:
            return None

        # 3. OI confirmation
        oi_confirmed = True
        confidence = 70.0
        if ctx.oi_analysis:
            oi_confirmed = is_oi_supporting_direction(
                ctx.current_price,
                ctx.oi_analysis.max_pe_oi_strike,
                ctx.oi_analysis.max_ce_oi_strike,
                "PUT",
            )
            if not oi_confirmed:
                confidence -= 20

        # 4. Adjust confidence
        if ctx.previous_day and ctx.previous_day.bias == DayBias.BEARISH:
            confidence += 10
        if ctx.cpr and ctx.cpr.cpr_type.value == "NARROW":
            confidence += 5
        if ctx.india_vix and ctx.india_vix < 14:
            confidence += 5

        # Compute index-level SL/target from market structure
        index_sl, index_target = select_index_sl_target(
            entry_price=ctx.current_price,
            signal_type=SignalType.BUY_PE,
            vwap=ctx.vwap,
            previous_day=ctx.previous_day,
            cpr=ctx.cpr,
            oi_analysis=ctx.oi_analysis,
            candles_5m=ctx.candles_5m,
        )

        indicators = self._build_indicator_snapshot(ctx, vwap, distance)

        if index_sl is not None and index_target is not None:
            indicators["index_sl"] = index_sl
            indicators["index_target"] = index_target
        else:
            # Fallback to fixed percentages when market structure is insufficient
            sl_pct = 0.30 if ctx.previous_day.bias == DayBias.BEARISH else 0.35
            indicators["sl_pct"] = sl_pct
            indicators["rr_multiplier"] = 1.5

        reason = (
            f"VWAP pullback BUY PE: {ctx.symbol} at {ctx.current_price:.2f}. "
            f"Bias: {ctx.previous_day.bias.value}. "
            f"VWAP: {vwap:.2f} (dist: {distance:.3f}%). "
            f"Bearish reversal confirmed. "
            f"OI: {'confirmed' if oi_confirmed else 'weak'}."
        )

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=SignalType.BUY_PE,
            instrument_type=InstrumentType.OPTION,
            strike_price=0,  # Resolved by option_resolver
            expiry_date=None,  # Resolved by option_resolver
            entry_price=ctx.current_price,  # Placeholder; replaced with premium
            stop_loss=0,  # Resolved by option_resolver
            target_price=None,  # Resolved by option_resolver
            confidence=min(confidence, 100),
            reason=reason,
            indicators=indicators,
            index_sl=index_sl,
            index_target=index_target,
        )

    def should_exit(
        self,
        ctx: MarketContext,
        entry_price: float,
        stop_loss: float,
        target_price: float | None,
    ) -> ExitSignal | None:
        """Check exit conditions for open position."""

        price = ctx.current_price

        # SL hit
        if price <= stop_loss:
            return ExitSignal(reason="Stop loss hit", exit_type="SL_HIT")

        # Target hit
        if target_price and price >= target_price:
            return ExitSignal(reason="Target reached", exit_type="TARGET_HIT")

        # VWAP invalidation: if price was above VWAP (call) but now closes below
        if ctx.vwap:
            vwap = ctx.vwap.vwap
            if entry_price > vwap and price < vwap * 0.998:
                return ExitSignal(
                    reason="Price crossed below VWAP — signal invalidated",
                    exit_type="INVALIDATION",
                )

        return None

    def get_position_size(
        self,
        capital: float,
        risk_per_trade_pct: float,
        entry_price: float,
        stop_loss: float,
        lot_size: int,
        vix_multiplier: float = 1.0,
    ) -> int:
        """Calculate number of lots based on risk parameters."""
        risk_amount = capital * (risk_per_trade_pct / 100)
        risk_per_lot = abs(entry_price - stop_loss) * lot_size

        if risk_per_lot <= 0:
            return 1

        lots = int(risk_amount / risk_per_lot)
        lots = max(1, int(lots * vix_multiplier))
        return min(lots, 5)  # Cap at 5 lots

    def _build_indicator_snapshot(
        self, ctx: MarketContext, vwap: float, distance: float
    ) -> dict:
        snapshot = {
            "vwap": vwap,
            "vwap_distance_pct": distance,
            "price": ctx.current_price,
        }
        if ctx.previous_day:
            snapshot.update({
                "pdh": ctx.previous_day.pdh,
                "pdl": ctx.previous_day.pdl,
                "pdc": ctx.previous_day.pdc,
                "day_bias": ctx.previous_day.bias.value,
            })
        if ctx.cpr:
            snapshot.update({
                "cpr_pivot": ctx.cpr.pivot,
                "cpr_tc": ctx.cpr.tc,
                "cpr_bc": ctx.cpr.bc,
                "cpr_type": ctx.cpr.cpr_type.value,
            })
        if ctx.oi_analysis:
            snapshot.update({
                "pcr": ctx.oi_analysis.pcr,
                "max_ce_oi_strike": ctx.oi_analysis.max_ce_oi_strike,
                "max_pe_oi_strike": ctx.oi_analysis.max_pe_oi_strike,
                "oi_sentiment": ctx.oi_analysis.sentiment,
            })
        if ctx.india_vix:
            snapshot["india_vix"] = ctx.india_vix
        return snapshot
