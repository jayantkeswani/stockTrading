"""Backtest harness — replays historical 1m candles and evaluates strategies.

The harness walks minute-by-minute through the historical data for a symbol,
builds a MarketContext at each step using context_builder, calls the strategy's
evaluate() method, and simulates exits via exit_simulator.

Architecture:
    Backtester.run(strategy, symbol, start, end, mode, window_filter)
        -> per trading day:
            -> per trading minute (09:15 → 15:15):
                -> build_historical_context(symbol, as_of, session)
                -> strategy.evaluate(ctx) -> StrategySignal | None
                -> if signal and (not holding):
                    -> resolve option symbol (strike_selector)
                    -> get entry premium (first option candle at entry_ts)
                    -> simulate_exit(signal, spot_candles_after, ...)
                    -> append to results
        -> build_report(trades, signals_meta)
        -> return BacktestReport

No side effects: signals are never persisted, no WS events, no agent execution.
The strategy_runner is bypassed entirely.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, select

from app.backtest.context_builder import build_historical_context, _fetch_candles_1m
from app.backtest.exit_simulator import ExitReason, SimulatedTrade, simulate_exit
from app.backtest.report import BacktestReport, build_report, print_report
from app.backtest.strike_selector import resolve_option_symbol
from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN, POSITION_CLOSE_DEADLINE
from app.core.database import async_session_factory
from app.core.enums import InstrumentType
from app.core.utils import is_trading_day, is_in_trading_window, is_past_close_deadline
from app.strategies.base import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)

BacktestMode = Literal["accurate", "fast"]

STEP_MINUTES = 1  # Evaluate every minute


class Backtester:
    """Replay historical data and simulate strategy signals."""

    def __init__(
        self,
        mode: BacktestMode = "accurate",
        window_filter: bool = True,
    ) -> None:
        """
        mode:
            'accurate' — fetch real option 1m candles for each signal (default).
            'fast'     — delta-approximate option P&L, no Fyers API calls.
        window_filter:
            True  — only evaluate during live trading windows (09:45-11:00, 13:45-14:45).
            False — evaluate every market minute (useful for measuring out-of-window signals).
        """
        self.mode = mode
        self.window_filter = window_filter

    async def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        start: date,
        end: date,
    ) -> BacktestReport:
        """Run a full backtest and return a BacktestReport."""
        all_trades: list[SimulatedTrade] = []
        signals_meta: list[dict] = []
        total_signals = 0

        trading_days = [
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if is_trading_day(start + timedelta(days=i))
        ]

        logger.info(
            "Backtesting %s on %s from %s to %s [%d trading days, mode=%s]",
            strategy.name.value, symbol, start, end, len(trading_days), self.mode,
        )

        async with async_session_factory() as session:
            for day in trading_days:
                day_results, day_meta = await self._replay_day(
                    strategy, symbol, day, session
                )
                all_trades.extend(day_results)
                signals_meta.extend(day_meta)
                total_signals += len(day_meta)

                if day_results:
                    logger.info(
                        "  %s: %d signal(s), %d trade(s)",
                        day, len(day_meta), len(day_results),
                    )

        report = build_report(
            strategy_name=strategy.name.value,
            symbol=symbol,
            start_date=str(start),
            end_date=str(end),
            mode=self.mode,
            trades=all_trades,
            signals_meta=signals_meta,
        )
        return report

    async def _replay_day(
        self,
        strategy: BaseStrategy,
        symbol: str,
        day: date,
        session,
    ) -> tuple[list[SimulatedTrade], list[dict]]:
        """Replay a single trading day minute by minute."""
        day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
        # Stop building contexts at the position close deadline
        close_deadline_dt = datetime.combine(day, POSITION_CLOSE_DEADLINE, tzinfo=IST)

        trades: list[SimulatedTrade] = []
        meta: list[dict] = []

        # Track open signal to avoid overlapping trades (one at a time, same direction)
        open_signal: StrategySignal | None = None

        current_ts = day_start + timedelta(minutes=15)  # Start at 09:30 (need 15m buffer)

        while current_ts <= close_deadline_dt:
            if self.window_filter and not is_in_trading_window(as_of=current_ts):
                current_ts += timedelta(minutes=STEP_MINUTES)
                continue

            ctx = await build_historical_context(symbol, current_ts, session)
            if ctx is None:
                current_ts += timedelta(minutes=STEP_MINUTES)
                continue

            signal = strategy.evaluate(ctx)

            if signal is not None and open_signal is None:
                total_signals_count = len(meta)

                # Resolve option symbol for this signal
                fyers_sym = None
                entry_premium = 0.0
                if signal.instrument_type == InstrumentType.OPTION:
                    resolution = await resolve_option_symbol(
                        symbol=symbol,
                        index_price=ctx.current_price,
                        signal_type=signal.signal_type,
                        as_of_date=day,
                    )
                    if resolution:
                        strike, expiry, fyers_sym = resolution
                        signal.strike_price = strike
                        signal.expiry_date = expiry
                        signal.fyers_option_symbol = fyers_sym

                        if self.mode == "accurate" and fyers_sym:
                            entry_premium = await self._get_entry_premium_accurate(
                                fyers_sym, current_ts
                            )
                        else:
                            # Fast mode: use a placeholder premium for delta calc
                            entry_premium = max(1.0, ctx.current_price * 0.01)
                    else:
                        # Cannot resolve → skip
                        current_ts += timedelta(minutes=STEP_MINUTES)
                        continue

                # Collect spot candles after entry for exit simulation
                spot_candles_after = await _fetch_candles_1m(
                    session, symbol,
                    current_ts,
                    datetime.combine(day, MARKET_CLOSE, tzinfo=IST),
                )
                spot_tuples = [(datetime.combine(day, MARKET_OPEN, tzinfo=IST) + timedelta(minutes=i), c)
                               for i, c in enumerate(spot_candles_after)]
                # Use actual timestamps from DB
                spot_tuples_ts = await self._fetch_spot_candles_with_timestamps(
                    session, symbol, current_ts,
                    datetime.combine(day, MARKET_CLOSE, tzinfo=IST),
                )

                trade = await simulate_exit(
                    signal=signal,
                    entry_ts=current_ts,
                    spot_candles_after=spot_tuples_ts,
                    fyers_option_symbol=fyers_sym,
                    entry_premium=entry_premium,
                    mode=self.mode,
                )

                trades.append(trade)
                meta.append({
                    "signal_type": signal.signal_type.value,
                    "confidence": float(signal.confidence) if signal.confidence else None,
                    "oi_available": ctx.oi_analysis is not None,
                })
                open_signal = signal

            # Clear open signal if it has been resolved
            if open_signal is not None and trades:
                last = trades[-1]
                if last.exit_ts and last.exit_ts <= current_ts:
                    open_signal = None

            current_ts += timedelta(minutes=STEP_MINUTES)

        return trades, meta

    async def _get_entry_premium_accurate(
        self, fyers_option_symbol: str, entry_ts: datetime
    ) -> float:
        """Get the option premium at entry from the first available 1m candle."""
        from app.backtest.option_data_fetcher import ensure_option_candles

        candles = await ensure_option_candles(
            fyers_option_symbol,
            entry_ts,
            entry_ts + timedelta(minutes=5),
        )
        if candles:
            return float(candles[0][1].close)
        return 0.0

    async def _fetch_spot_candles_with_timestamps(
        self,
        session,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[tuple[datetime, "Candle"]]:
        from app.indicators.candle_patterns import Candle
        from app.models.market_data import MarketData1m

        result = await session.execute(
            select(
                MarketData1m.timestamp,
                MarketData1m.open,
                MarketData1m.high,
                MarketData1m.low,
                MarketData1m.close,
                MarketData1m.volume,
            )
            .where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp >= start_ts,
                    MarketData1m.timestamp <= end_ts,
                )
            )
            .order_by(MarketData1m.timestamp)
        )
        return [
            (
                r.timestamp,
                Candle(
                    open=float(r.open), high=float(r.high),
                    low=float(r.low), close=float(r.close),
                    volume=int(r.volume or 0),
                ),
            )
            for r in result.all()
        ]
