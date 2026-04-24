"""Strategy 2: VWAP Pullback + Composite Intraday Bias + OI Confirmation.

Primary strategy. Phase 2 changes:
- Replaced yesterday-only hard bias gate with composite IntradayBias (live score).
- STRONG opposing bias blocks the trade; MODERATE/WEAK allows it with a confidence
  haircut via the bias_alignment factor.
- Confidence now computed by indicators/confidence.py (10-factor weighted composite)
  instead of inline constants.
- LLM overlay (signal_confidence.py) adjusts confidence ±15 and adds ai_summary /
  ai_rationale / key_supports / key_risks — applied in strategy_runner after resolve.
- Signal fires only when confidence >= FIRE_THRESHOLD (from config, default 55).
- intraday_bias components + confidence_factors persisted to signal.indicators JSONB.

See docs/strategies/strategy-2-vwap-pullback.md for full specification.
"""

import logging

from app.config import settings
from app.core.constants import VWAP_PROXIMITY_PCT
from app.core.enums import DayBias, InstrumentType, SignalType, StrategyName
from app.indicators.candle_patterns import (
    average_volume,
    is_bearish_reversal,
    is_bullish_reversal,
)
from app.indicators.confidence import compute_confidence
from app.indicators.intraday_bias import is_blocked_by_bias
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
    max_lots = 5

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Evaluate VWAP pullback entry conditions."""

        if not ctx.vwap or not ctx.previous_day or not ctx.cpr:
            logger.debug("Missing indicators for %s", ctx.symbol)
            return None

        if len(ctx.candles_5m) < 5:
            return None

        vwap = ctx.vwap.vwap
        price = ctx.current_price

        if not is_pullback_to_vwap(price, vwap, VWAP_PROXIMITY_PCT):
            return None

        distance = price_distance_from_vwap(price, vwap)

        # Determine candidate direction from pullback sign (structural rule, not bias)
        if distance > 0:
            signal = self._evaluate_call(ctx, vwap, distance)
        elif distance < 0:
            signal = self._evaluate_put(ctx, vwap, distance)
        else:
            signal = None

        return signal

    def _evaluate_call(
        self, ctx: MarketContext, vwap: float, distance: float
    ) -> StrategySignal | None:
        """Evaluate conditions for a CALL buy."""

        # Soft bias gate: STRONG bearish bias blocks CE
        if ctx.intraday_bias and is_blocked_by_bias("CE", ctx.intraday_bias):
            logger.debug("CE blocked by STRONG bearish intraday bias for %s", ctx.symbol)
            return None

        if not is_bullish_reversal(ctx.candles_5m):
            return None

        avg_vol = average_volume(ctx.candles_5m, periods=20)
        curr_vol = ctx.candles_5m[-1].volume
        if avg_vol > 0 and curr_vol > avg_vol * 1.2:
            return None

        return self._build_signal(ctx, SignalType.BUY_CE, vwap, distance)

    def _evaluate_put(
        self, ctx: MarketContext, vwap: float, distance: float
    ) -> StrategySignal | None:
        """Evaluate conditions for a PUT buy."""

        # Soft bias gate: STRONG bullish bias blocks PE
        if ctx.intraday_bias and is_blocked_by_bias("PE", ctx.intraday_bias):
            logger.debug("PE blocked by STRONG bullish intraday bias for %s", ctx.symbol)
            return None

        if not is_bearish_reversal(ctx.candles_5m):
            return None

        avg_vol = average_volume(ctx.candles_5m, periods=20)
        curr_vol = ctx.candles_5m[-1].volume
        if avg_vol > 0 and curr_vol > avg_vol * 1.2:
            return None

        return self._build_signal(ctx, SignalType.BUY_PE, vwap, distance)

    def _build_signal(
        self,
        ctx: MarketContext,
        signal_type: SignalType,
        vwap: float,
        distance: float,
    ) -> StrategySignal | None:
        """Common signal builder — resolves SL/target and computes confidence."""
        is_ce = signal_type == SignalType.BUY_CE
        direction = "CE" if is_ce else "PE"

        # Index-level SL/target from market structure
        index_sl, index_target = select_index_sl_target(
            entry_price=ctx.current_price,
            signal_type=signal_type,
            vwap=ctx.vwap,
            previous_day=ctx.previous_day,
            cpr=ctx.cpr,
            oi_analysis=ctx.oi_analysis,
            candles_5m=ctx.candles_5m,
        )

        # Deterministic confidence composite
        confidence_result = compute_confidence(
            signal_direction=direction,
            intraday_bias=ctx.intraday_bias,
            candles_5m=ctx.candles_5m,
            vwap=ctx.vwap,
            oi_analysis=ctx.oi_analysis,
            cpr=ctx.cpr,
            india_vix=ctx.india_vix,
            global_cues=ctx.global_cues,
            index_sl=index_sl,
            index_target=index_target,
            index_entry=ctx.current_price,
            current_time_ist=ctx.current_time_ist,
        )

        # Fire threshold gate
        if confidence_result.score < settings.fire_confidence_threshold:
            logger.debug(
                "Signal suppressed: confidence %.1f < threshold %.1f for %s %s",
                confidence_result.score, settings.fire_confidence_threshold, ctx.symbol, direction,
            )
            return None

        # OI confirmation (soft — already reflected in oi_support factor)
        oi_confirmed = True
        if ctx.oi_analysis:
            oi_confirmed = is_oi_supporting_direction(
                ctx.current_price,
                ctx.oi_analysis.max_pe_oi_strike,
                ctx.oi_analysis.max_ce_oi_strike,
                "CALL" if is_ce else "PUT",
            )

        # Build indicator snapshot — includes both bias + confidence factor breakdown
        indicators = self._build_indicator_snapshot(
            ctx, vwap, distance, oi_confirmed, confidence_result, index_sl, index_target
        )

        # SL/target fallback
        if index_sl is None or index_target is None:
            bias = ctx.intraday_bias.bias if ctx.intraday_bias else DayBias.NEUTRAL
            sl_pct = 0.30 if (is_ce and bias == DayBias.BULLISH) or (not is_ce and bias == DayBias.BEARISH) else 0.35
            indicators["sl_pct"] = sl_pct
            indicators["rr_multiplier"] = 1.5

        # Build human-readable reason string including bias and top confidence factors
        bias_str = ctx.intraday_bias.bias.value if ctx.intraday_bias else "unknown"
        bias_score = f"{ctx.intraday_bias.score:+.2f}" if ctx.intraday_bias else "n/a"
        reason = (
            f"VWAP pullback BUY {direction}: {ctx.symbol} at {ctx.current_price:.2f}. "
            f"Intraday bias: {bias_str} ({bias_score}). "
            f"VWAP: {vwap:.2f} (dist: {distance:.3f}%). "
            f"{'Bullish' if is_ce else 'Bearish'} reversal. "
            f"OI: {'confirmed' if oi_confirmed else 'weak'}. "
            f"Confidence: {confidence_result.score:.0f} ({confidence_result.rationale_short})."
        )

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=signal_type,
            instrument_type=InstrumentType.OPTION,
            strike_price=0,
            expiry_date=None,
            entry_price=ctx.current_price,
            stop_loss=0,
            target_price=None,
            confidence=confidence_result.score,
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
        price = ctx.current_price

        if price <= stop_loss:
            return ExitSignal(reason="Stop loss hit", exit_type="SL_HIT")

        if target_price and price >= target_price:
            return ExitSignal(reason="Target reached", exit_type="TARGET_HIT")

        if ctx.vwap:
            vwap = ctx.vwap.vwap
            if entry_price > vwap and price < vwap * 0.998:
                return ExitSignal(
                    reason="Price crossed below VWAP — signal invalidated",
                    exit_type="INVALIDATION",
                )

        return None

    def _build_indicator_snapshot(
        self,
        ctx: MarketContext,
        vwap: float,
        distance: float,
        oi_confirmed: bool,
        confidence_result,
        index_sl,
        index_target,
    ) -> dict:
        snapshot: dict = {
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
                "oi_confirmed": oi_confirmed,
            })
        if ctx.india_vix:
            snapshot["india_vix"] = ctx.india_vix
        if ctx.intraday_bias:
            snapshot["intraday_bias"] = ctx.intraday_bias.components
        if ctx.global_cues:
            snapshot["global_score"] = ctx.global_cues.global_score
        if index_sl is not None:
            snapshot["index_sl"] = index_sl
        if index_target is not None:
            snapshot["index_target"] = index_target

        # Confidence factor breakdown for UI/post-mortem
        snapshot["confidence_factors"] = confidence_result.factors
        snapshot["confidence_rationale"] = confidence_result.rationale_short

        return snapshot
