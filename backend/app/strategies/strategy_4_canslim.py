"""Strategy 4: CAN SLIM — Growth stock breakout strategy for stock futures.

Combines fundamental screening with technical breakout entry:
1. Pre-fetched CAN SLIM fundamentals (from stock_fundamentals table)
2. Chart base pattern detection (cup-with-handle, flat base, double bottom)
3. Volume breakout confirmation (> 1.5x 20-day average)
4. Market direction filter (NIFTY above 50 DMA, VIX < 20)

Entry: Buy stock futures when CAN SLIM candidate breaks out of a base pattern
       on above-average volume.
Exit:  8% stop loss, 20% profit target, trailing stop at breakeven after 10% gain.
Hold:  Positional (days to weeks), NOT intraday.

See docs/strategies/strategy-4-canslim.md for full specification.
"""

import logging
from datetime import date

from app.core.constants import (
    CANSLIM_BREAKOUT_VOLUME_MULTIPLIER,
    CANSLIM_MAX_PCT_FROM_52W_HIGH,
    CANSLIM_MAX_POSITIONAL_LOTS,
    CANSLIM_MAX_VIX,
    CANSLIM_MIN_TOTAL_SCORE,
    CANSLIM_SL_PCT,
    CANSLIM_TARGET_PCT,
    CANSLIM_TRAILING_SL_ACTIVATION_PCT,
)

from app.core.enums import InstrumentType, SignalType, StrategyName
from app.indicators.volume_analysis import is_volume_breakout, volume_ratio
from app.strategies.base import (
    BaseStrategy,
    ExitSignal,
    MarketContext,
    StrategySignal,
)
from app.strategies.canslim.base_patterns import detect_any_base_pattern

logger = logging.getLogger(__name__)


class CANSLIMStrategy(BaseStrategy):
    name = StrategyName.CAN_SLIM
    holding_type = "POSITIONAL"
    max_lots = CANSLIM_MAX_POSITIONAL_LOTS

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        """Evaluate CAN SLIM breakout entry conditions.

        Requires:
        - ctx.canslim_data: StockFundamental row (pre-fetched by fundamental_data_task)
        - ctx.candles_daily: Last 90 days of daily bars (for pattern detection)
        - ctx.volume_avg_20d: 20-day average volume
        - ctx.india_vix: For market direction scoring
        """
        p = ctx.strategy_params or {}
        min_total_score = p.get("min_total_score", CANSLIM_MIN_TOTAL_SCORE)
        max_vix = p.get("max_vix", CANSLIM_MAX_VIX)
        volume_mult = p.get("breakout_volume_multiplier", CANSLIM_BREAKOUT_VOLUME_MULTIPLIER)
        sl_pct = p.get("sl_pct", CANSLIM_SL_PCT)
        target_pct = p.get("target_pct", CANSLIM_TARGET_PCT)

        # 1. Check fundamental data exists
        if ctx.canslim_data is None:
            logger.debug("No CAN SLIM fundamental data for %s — skipping", ctx.symbol)
            return None

        canslim = ctx.canslim_data

        # 2. Check composite CAN SLIM score meets minimum
        score = float(canslim.canslim_score) if canslim.canslim_score else 0
        if score < min_total_score:
            logger.debug(
                "%s CAN SLIM score %.1f < %.1f minimum — skipping",
                ctx.symbol, score, min_total_score,
            )
            return None

        # 3. Check market direction (M factor) — VIX threshold from strategy params
        if ctx.india_vix is not None and ctx.india_vix > max_vix:
            logger.debug(
                "%s India VIX %.1f > %.1f — CAN SLIM skipping (bearish market)",
                ctx.symbol, ctx.india_vix, max_vix,
            )
            return None

        # 4. Check proximity to 52-week high (N factor — runtime check)
        if canslim.pct_from_52w_high is not None:
            pct_from_high = float(canslim.pct_from_52w_high)
            if pct_from_high > CANSLIM_MAX_PCT_FROM_52W_HIGH:
                logger.debug(
                    "%s is %.1f%% from 52w high (max %.1f%%) — skipping",
                    ctx.symbol, pct_from_high, CANSLIM_MAX_PCT_FROM_52W_HIGH,
                )
                return None

        # 5. Detect chart base pattern breakout
        if not ctx.candles_daily or len(ctx.candles_daily) < 25:
            logger.debug("Insufficient daily candles for %s pattern detection", ctx.symbol)
            return None

        pattern = detect_any_base_pattern(ctx.candles_daily)
        if pattern is None:
            logger.debug("No base pattern detected for %s", ctx.symbol)
            return None

        # Check if price is breaking out above the pattern's resistance level
        if ctx.current_price <= pattern.breakout_price:
            logger.debug(
                "%s price %.2f <= breakout %.2f — no breakout yet",
                ctx.symbol, ctx.current_price, pattern.breakout_price,
            )
            return None

        # 6. Volume confirmation
        if ctx.volume_avg_20d and ctx.volume_avg_20d > 0:
            # Use today's accumulated volume from intraday candles
            today_volume = sum(c.volume for c in ctx.candles_5m) if ctx.candles_5m else 0
            # For manual scans (no live candles), use the most recent daily bar's volume
            if today_volume == 0 and ctx.candles_daily:
                today_volume = ctx.candles_daily[-1].volume
            if not is_volume_breakout(
                today_volume, ctx.volume_avg_20d, volume_mult
            ):
                logger.debug(
                    "%s volume %d < %.1fx avg %d — no volume confirmation",
                    ctx.symbol, today_volume, volume_mult,
                    ctx.volume_avg_20d,
                )
                return None

        # All conditions met — generate signal
        entry_price = ctx.current_price

        # Pattern-based SL: just below the base low (2% buffer)
        pattern_sl = pattern.base_low * 0.98
        # Cap: never wider than sl_pct from entry
        max_sl = entry_price * (1 - sl_pct / 100)
        stop_loss = max(pattern_sl, max_sl)

        # Pattern-based target: measured move (base depth projected above breakout)
        measured_move = pattern.breakout_price - pattern.base_low
        pattern_target = pattern.breakout_price + measured_move
        # Floor: at least target_pct from entry
        min_target = entry_price * (1 + target_pct / 100)
        target_price = max(pattern_target, min_target)

        # Build reason string
        reason = (
            f"CAN SLIM breakout: {ctx.symbol} score={score:.0f}, "
            f"pattern={pattern.pattern_type} (depth={pattern.depth_pct:.1f}%, "
            f"{pattern.length_days}d), "
            f"breakout above {pattern.breakout_price:.2f}"
        )

        # Build indicators snapshot
        indicators = {
            "canslim_score": score,
            "c_score": float(canslim.c_score) if canslim.c_score else 0,
            "a_score": float(canslim.a_score) if canslim.a_score else 0,
            "n_score": float(canslim.n_score) if canslim.n_score else 0,
            "s_score": float(canslim.s_score) if canslim.s_score else 0,
            "l_score": float(canslim.l_score) if canslim.l_score else 0,
            "i_score": float(canslim.i_score) if canslim.i_score else 0,
            "pattern_type": pattern.pattern_type,
            "breakout_price": pattern.breakout_price,
            "pattern_depth_pct": pattern.depth_pct,
            "pattern_length_days": pattern.length_days,
            "pattern_base_low": pattern.base_low,
            "measured_move": measured_move,
            "india_vix": ctx.india_vix,
            "volume_avg_20d": ctx.volume_avg_20d,
            "rs_rating": ctx.relative_strength,
        }

        return StrategySignal(
            strategy_name=self.name,
            symbol=ctx.symbol,
            signal_type=SignalType.BUY_FUT,
            instrument_type=InstrumentType.FUTURE,
            strike_price=0,  # Not applicable for futures
            expiry_date=date.today(),  # Will be resolved by futures_resolver
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            confidence=score,
            reason=reason,
            indicators=indicators,
        )

    def should_exit(
        self,
        ctx: MarketContext,
        entry_price: float,
        stop_loss: float,
        target_price: float | None,
    ) -> ExitSignal | None:
        """Check CAN SLIM exit conditions.

        Multi-day exit rules (different from intraday — no time-based EOD exit):
        - 8% hard stop loss
        - 20% profit target
        - Trailing stop: move SL to breakeven after 10% gain
        """
        price = ctx.current_price

        # 1. Hard stop loss (8% below entry)
        if price <= stop_loss:
            return ExitSignal(
                reason=f"CAN SLIM 8% stop loss hit at {price:.2f} (SL={stop_loss:.2f})",
                exit_type="SL_HIT",
            )

        # 2. Profit target (20% above entry)
        if target_price and price >= target_price:
            return ExitSignal(
                reason=f"CAN SLIM 20% target reached at {price:.2f} (target={target_price:.2f})",
                exit_type="TARGET_HIT",
            )

        # 3. Trailing stop check is handled by trade_monitor (updates position.stop_loss)
        # We just flag if price retraces to a trailing SL level
        p = ctx.strategy_params or {}
        trailing_activation = p.get("trailing_sl_activation_pct", CANSLIM_TRAILING_SL_ACTIVATION_PCT)
        gain_pct = ((price - entry_price) / entry_price) * 100
        if gain_pct >= trailing_activation and stop_loss >= entry_price:
            # Trailing SL is active (trade_monitor moved SL to breakeven)
            # Check if price dropped back to breakeven
            if price <= entry_price * 1.01:  # 1% buffer above breakeven
                return ExitSignal(
                    reason="CAN SLIM trailing stop: price retraced to breakeven",
                    exit_type="TRAILING_SL",
                )

        return None

