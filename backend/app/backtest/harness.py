"""Backtest harness — replays historical 1m candles and evaluates strategies.

The harness walks minute-by-minute through historical data for a symbol,
builds a MarketContext at each step, calls strategy.evaluate(), and simulates
exits via exit_simulator.

Architecture:
    Backtester.run(strategy, symbol, start, end, mode, window_filter)
        -> per trading day:
            -> per trading minute (09:30 → 15:15):
                -> build_historical_context(symbol, as_of, session)
                -> strategy.evaluate(ctx) -> StrategySignal | None
                -> if signal and (not holding):
                    -> resolve option symbol (strike_selector)
                    -> simulate_exit(...)
                    -> if accurate mode returns NO_DATA → auto-fallback to fast
                    -> append SimulatedTrade to results
        -> build_report(trades, signals_meta)
        -> return BacktestReport

Mode notes:
    'fast' (default): delta-approximates option P&L from spot moves. Works for all
        historical dates. Fyers API not required. This is the correct mode for all
        backtests on the Fyers free plan.
    'accurate': intended to use real 1m option candles from Fyers, but confirmed that
        the Fyers FREE API does not serve 1m option candle history (returns no_data
        even for currently active contracts). Accurate mode auto-falls back to fast for
        every signal. It is kept for future compatibility with paid Fyers data plans.

No side effects: signals never persist to DB, no WS events, no agent execution.
strategy_runner is bypassed entirely.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, select

from app.backtest.context_builder import build_historical_context
from app.backtest.exit_simulator import ExitReason, SimulatedTrade, simulate_exit
from app.backtest.report import BacktestReport, build_report
from app.backtest.strike_selector import resolve_option_symbol
from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN, POSITION_CLOSE_DEADLINE
from app.core.database import async_session_factory
from app.core.enums import InstrumentType
from app.core.utils import is_trading_day, is_in_trading_window
from app.indicators.candle_patterns import Candle
from app.models.market_data import MarketData1m
from app.strategies.base import BaseStrategy, StrategySignal

logger = logging.getLogger(__name__)

BacktestMode = Literal["accurate", "fast"]

STEP_MINUTES = 1


class Backtester:
    """Replay historical data and simulate strategy signals."""

    def __init__(
        self,
        mode: BacktestMode = "fast",
        window_filter: bool = True,
    ) -> None:
        """
        mode:
            'fast' (default) — delta-approximate option P&L. Works for all historical dates.
            'accurate'       — fetch real 1m option candles from Fyers. Only works for
                               currently active contracts; auto-falls back to fast when
                               Fyers returns no data for expired contracts.
        window_filter:
            True  — only evaluate during active trading windows (09:45-11:00, 13:45-14:45).
            False — evaluate every market minute (useful to measure out-of-window signals).
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

        trading_days = [
            start + timedelta(days=i)
            for i in range((end - start).days + 1)
            if is_trading_day(start + timedelta(days=i))
        ]

        logger.info(
            "Backtesting %s on %s  %s → %s  [%d trading days, mode=%s]",
            strategy.name.value, symbol, start, end, len(trading_days), self.mode,
        )

        async with async_session_factory() as session:
            for day in trading_days:
                day_trades, day_meta = await self._replay_day(strategy, symbol, day, session)
                all_trades.extend(day_trades)
                signals_meta.extend(day_meta)

                if day_trades:
                    logger.info("  %s: %d signal(s), %d trade(s)", day, len(day_meta), len(day_trades))

        report = build_report(
            strategy_name=strategy.name.value,
            symbol=symbol,
            start_date=str(start),
            end_date=str(end),
            mode=self.mode,
            trades=all_trades,
            signals_meta=signals_meta,
        )
        report.trades = all_trades
        report._signals_meta = signals_meta  # carried for save_run callers
        return report

    async def _replay_day(
        self,
        strategy: BaseStrategy,
        symbol: str,
        day: date,
        session,
    ) -> tuple[list[SimulatedTrade], list[dict]]:
        """Replay a single trading day minute by minute."""
        close_deadline_dt = datetime.combine(day, POSITION_CLOSE_DEADLINE, tzinfo=IST)

        trades: list[SimulatedTrade] = []
        meta: list[dict] = []
        open_signal: StrategySignal | None = None

        # Start at 09:30 — need at least 15m of candle history for VWAP
        current_ts = datetime.combine(day, MARKET_OPEN, tzinfo=IST) + timedelta(minutes=15)

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
                # Resolve option strike + expiry + Fyers symbol
                fyers_sym = None
                strike = 0.0
                expiry = None

                if signal.instrument_type == InstrumentType.OPTION:
                    resolution = await resolve_option_symbol(
                        symbol=symbol,
                        index_price=ctx.current_price,
                        signal_type=signal.signal_type,
                        as_of_date=day,
                    )
                    if resolution is None:
                        current_ts += timedelta(minutes=STEP_MINUTES)
                        continue
                    strike, expiry, fyers_sym = resolution
                    signal.strike_price = strike
                    signal.expiry_date = expiry
                    signal.fyers_option_symbol = fyers_sym

                # Estimate entry premium for fast mode (ATM ~0.75% of index)
                fast_entry_premium = max(1.0, ctx.current_price * 0.0075)

                # Accurate mode: try to fetch real option candle at entry
                entry_premium = fast_entry_premium
                effective_mode = "fast" if self.mode == "fast" else "accurate"

                if self.mode == "accurate" and fyers_sym:
                    accurate_premium = await self._get_entry_premium_accurate(fyers_sym, current_ts)
                    if accurate_premium > 0:
                        entry_premium = accurate_premium
                    else:
                        # Fyers has no data (expired contract) → silently fall back to fast
                        effective_mode = "fast_fallback"
                        logger.debug("No Fyers data for %s at %s — using fast fallback", fyers_sym, current_ts)

                # Spot candles from entry to market close (for invalidation + fast mode P&L)
                spot_candles = await self._fetch_spot_with_ts(
                    session, symbol, current_ts,
                    datetime.combine(day, MARKET_CLOSE, tzinfo=IST),
                )

                trade = await simulate_exit(
                    signal=signal,
                    entry_ts=current_ts,
                    spot_candles_after=spot_candles,
                    fyers_option_symbol=fyers_sym if effective_mode == "accurate" else None,
                    entry_premium=entry_premium,
                    mode="fast" if effective_mode != "accurate" else "accurate",
                )

                trades.append(trade)
                meta.append({
                    "signal_type": signal.signal_type.value,
                    "confidence": float(signal.confidence) if signal.confidence else None,
                    "oi_available": ctx.oi_analysis is not None,
                    "effective_mode": effective_mode,
                    "entry_price_index": ctx.current_price,
                    "strike": strike,
                    "expiry": str(expiry) if expiry else None,
                })
                open_signal = signal

            # Release open_signal once the trade has exited
            if open_signal is not None and trades:
                last = trades[-1]
                if last.exit_ts and last.exit_ts <= current_ts:
                    open_signal = None

            current_ts += timedelta(minutes=STEP_MINUTES)

        return trades, meta

    async def _get_entry_premium_accurate(
        self, fyers_option_symbol: str, entry_ts: datetime
    ) -> float:
        """Return option LTP at entry from Fyers history. Returns 0.0 if no data."""
        from app.backtest.option_data_fetcher import ensure_option_candles

        candles = await ensure_option_candles(
            fyers_option_symbol,
            entry_ts,
            entry_ts + timedelta(minutes=5),
        )
        if candles:
            return float(candles[0][1].close)
        return 0.0

    async def _fetch_spot_with_ts(
        self,
        session,
        symbol: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[tuple[datetime, Candle]]:
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
