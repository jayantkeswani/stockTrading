"""Strategy runner — the critical connector between candle events and strategy evaluation.

Listens for candle close events from feed_manager, builds MarketContext from all
available data sources, evaluates active strategies, and handles signal persistence
and broadcasting.

Flow:
    feed_manager._emit_candle() → strategy_runner.on_candle_close()
        → _build_market_context()
        → _check_guardrails()
        → strategy.evaluate(ctx) for each active strategy
        → _persist_signal() + _broadcast_signal()
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import (
    DEFAULT_MAX_DAILY_DRAWDOWN_PCT,
    DEFAULT_MAX_TRADES_PER_DAY,
    IST,
    MARKET_OPEN,
    VIX_EXTREME,
)
from app.core.database import async_session_factory
from app.core.enums import InstrumentType, SignalStatus, StrategyName
from app.core.redis import get_cached_price, get_redis
from app.core.utils import is_in_trading_window, is_past_close_deadline, now_ist
from app.indicators.candle_patterns import Candle
from app.indicators.cpr import calculate_cpr
from app.indicators.open_interest import OIAnalysis, analyze_option_chain
from app.indicators.previous_day import PreviousDayLevels, analyze_previous_day
from app.indicators.vwap import VWAPResult, calculate_vwap
from app.models.market_data import MarketData1m
from app.models.oi_snapshot import OISnapshot
from app.models.signal import Signal
from app.models.trade import Trade
from app.strategies.base import BaseStrategy, MarketContext, StrategySignal
from app.strategies.registry import get_active_strategies
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


class StrategyRunner:
    """Orchestrates strategy evaluation on every candle close.

    Responsibilities:
    - Build MarketContext from Redis cache, DB candles, and computed indicators
    - Enforce trading window, drawdown, and max-trade guardrails
    - Evaluate all active strategies and persist / broadcast resulting signals
    """

    def __init__(self):
        # In-memory buffer of today's 1m candles per symbol, keyed symbol -> list[dict].
        # Populated from DB on first call and kept in sync by appending new candles.
        self._candle_buffers: dict[str, list[dict]] = {}

        # Cache previous-day levels per symbol (computed once per day)
        self._prev_day_cache: dict[str, tuple[date, PreviousDayLevels]] = {}

        # Today's signal count per symbol, reset on new trading day
        self._daily_signal_count: dict[str, int] = {}
        self._signal_count_date: date | None = None

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def on_candle_close(
        self,
        symbol: str,
        candle_data: dict,
        strategy_filter: list[StrategyName] | None = None,
    ) -> None:
        """Handle a completed 1-minute candle.

        Args:
            symbol: The index symbol (e.g. "NIFTY")
            candle_data: Dict with keys: o, h, l, c, v, timestamp, timeframe
            strategy_filter: If provided, only evaluate these strategies.
                             Used by auto-mode (candle-driven) and manual scan.
        """
        try:
            await self._append_candle_to_buffer(symbol, candle_data)

            # Hard guardrails — conditions where evaluation itself makes no sense
            if not self._check_hard_guardrails():
                return

            # Soft guardrails — risk limits that block execution but not signal generation
            executable, blocked_reason = await self._check_risk_limits(symbol)

            ctx = await self._build_market_context(symbol, candle_data)
            if ctx is None:
                logger.debug("Could not build MarketContext for %s — skipping evaluation", symbol)
                return

            await self._evaluate_strategies(symbol, ctx, executable, blocked_reason, strategy_filter)

        except Exception:
            logger.exception("Error in strategy runner for %s", symbol)

    async def evaluate_manual(
        self, symbol: str, strategy_name: StrategyName,
    ) -> StrategySignal | None:
        """Run a single strategy evaluation on demand (manual scan).

        Builds MarketContext from existing DB candles + Redis price cache.
        Returns the signal if one was generated, None otherwise.
        """
        # Build a synthetic candle_data from the latest cached price
        current_price = await self._get_current_price(symbol)
        if current_price is None:
            logger.warning("No price available for %s — cannot run manual evaluation", symbol)
            return None

        now = now_ist()
        candle_data = {
            "o": current_price,
            "h": current_price,
            "l": current_price,
            "c": current_price,
            "v": 0,
            "timestamp": now.isoformat(),
            "timeframe": "1m",
        }

        # Ensure candle buffer is loaded (don't append synthetic candle)
        today = now.date()
        if symbol not in self._candle_buffers or self._buffer_needs_reload(
            self._candle_buffers.get(symbol, []), today
        ):
            self._candle_buffers[symbol] = await self._load_todays_candles(symbol, today)

        executable, blocked_reason = await self._check_risk_limits(symbol)

        ctx = await self._build_market_context(symbol, candle_data)
        if ctx is None:
            logger.debug("Could not build MarketContext for %s — skipping manual evaluation", symbol)
            return None

        # Evaluate single strategy
        from app.strategies.registry import get_strategy
        strategy = get_strategy(strategy_name)
        if strategy is None:
            logger.warning("Strategy %s not found in registry", strategy_name)
            return None

        signal = strategy.evaluate(ctx)
        if signal is not None:
            if signal.instrument_type == InstrumentType.OPTION:
                signal, executable, blocked_reason = await self._resolve_option(
                    signal, ctx, executable, blocked_reason,
                )
            await self._handle_signal(signal, executable, blocked_reason)

        return signal

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _check_hard_guardrails(self) -> bool:
        """Return True if strategy evaluation should proceed.

        Hard guardrails are conditions where generating signals makes no sense
        (outside market hours, past close deadline, extreme VIX).
        """
        # 1. Trading window check
        if not is_in_trading_window():
            logger.debug("Outside trading window — skipping strategy evaluation")
            return False

        # 2. Position close deadline
        if is_past_close_deadline():
            logger.debug("Past close deadline — no new signals")
            return False

        return True

    async def _check_risk_limits(self, symbol: str) -> tuple[bool, str | None]:
        """Check risk limits and return (executable, blocked_reason).

        Signals are always generated regardless of risk limits, but these checks
        determine whether the signal can actually be traded.
        """
        # VIX extreme check (from Redis)
        vix = await self._get_india_vix()
        if vix is not None and vix >= VIX_EXTREME:
            logger.warning("India VIX %.2f >= %.2f extreme threshold — signal not executable", vix, VIX_EXTREME)
            return False, f"VIX extreme ({vix:.1f} >= {VIX_EXTREME})"

        # Max trades per day
        today = now_ist().date()
        if self._signal_count_date != today:
            self._daily_signal_count = {}
            self._signal_count_date = today

        max_trades = settings.max_trades_per_day or DEFAULT_MAX_TRADES_PER_DAY
        if self._daily_signal_count.get(symbol, 0) >= max_trades:
            logger.info("Max trades (%d) reached for %s today — signal not executable", max_trades, symbol)
            return False, f"Max trades reached ({max_trades}/day)"

        # Drawdown limit
        if await self._is_drawdown_breached():
            logger.warning("Daily drawdown limit breached — signal not executable")
            return False, "Drawdown limit breached"

        return True, None

    async def _is_drawdown_breached(self) -> bool:
        """Check if realized + unrealized losses exceed the daily drawdown cap."""
        today = now_ist().date()
        today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

        async with async_session_factory() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    and_(
                        Trade.entry_time >= today_start,
                        Trade.status == "CLOSED",
                    )
                )
            )
            realized_pnl = float(result.scalar_one())

        capital = settings.trading_capital
        max_dd_pct = settings.max_daily_drawdown_pct or DEFAULT_MAX_DAILY_DRAWDOWN_PCT
        max_dd_amount = capital * (max_dd_pct / 100.0)

        # Negative PnL means loss
        if realized_pnl < 0 and abs(realized_pnl) >= max_dd_amount:
            return True
        return False

    # ------------------------------------------------------------------
    # MarketContext construction
    # ------------------------------------------------------------------

    async def _build_market_context(
        self, symbol: str, candle_data: dict
    ) -> MarketContext | None:
        """Assemble all indicator data into a MarketContext."""

        # Current price from Redis (most recent tick) or fall back to candle close
        current_price = await self._get_current_price(symbol)
        if current_price is None:
            current_price = candle_data.get("c")
        if current_price is None:
            return None

        # 5-minute candles for pattern detection
        candles_5m = self._aggregate_5m_candles(symbol)

        # VWAP from today's 1m candles
        vwap_result = self._calculate_vwap_from_buffer(symbol)

        # Previous day levels (cached per day)
        prev_day_levels = await self._get_previous_day_levels(symbol)

        # CPR from previous day HLC
        cpr_result = None
        if prev_day_levels:
            cpr_result = calculate_cpr(
                high=prev_day_levels.pdh,
                low=prev_day_levels.pdl,
                close=prev_day_levels.pdc,
            )

        # OI analysis from latest snapshot
        oi_analysis = await self._get_oi_analysis(symbol)

        # India VIX
        india_vix = await self._get_india_vix()

        return MarketContext(
            symbol=symbol,
            current_price=current_price,
            candles_5m=candles_5m,
            vwap=vwap_result,
            previous_day=prev_day_levels,
            cpr=cpr_result,
            oi_analysis=oi_analysis,
            india_vix=india_vix,
            current_time_ist=now_ist().isoformat(),
        )

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    async def _get_current_price(self, symbol: str) -> float | None:
        """Fetch latest price from Redis cache."""
        cached = await get_cached_price(symbol)
        if cached:
            return float(cached["ltp"])
        return None

    async def _get_india_vix(self) -> float | None:
        """Fetch India VIX from Redis cache."""
        r = get_redis()
        vix_raw = await r.get("indicator:india_vix")
        if vix_raw:
            try:
                return float(vix_raw)
            except (ValueError, TypeError):
                pass
        # Also try the price cache (VIX may be tracked as a symbol)
        cached = await get_cached_price("INDIA VIX")
        if cached:
            return float(cached["ltp"])
        return None

    async def _get_previous_day_levels(self, symbol: str) -> PreviousDayLevels | None:
        """Get previous trading day's OHLC and compute directional bias.

        Caches the result for the entire trading day to avoid repeated DB queries.
        """
        today = now_ist().date()

        # Return cached value if already computed for today
        if symbol in self._prev_day_cache:
            cached_date, cached_levels = self._prev_day_cache[symbol]
            if cached_date == today:
                return cached_levels

        async with async_session_factory() as session:
            prev_day_levels = await self._query_previous_day(session, symbol, today)

        if prev_day_levels:
            self._prev_day_cache[symbol] = (today, prev_day_levels)
        return prev_day_levels

    async def _query_previous_day(
        self, session: AsyncSession, symbol: str, today: date
    ) -> PreviousDayLevels | None:
        """Query last trading day's 1m candles and derive OHLC."""
        # Find the most recent trading day before today
        yesterday_cutoff = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

        result = await session.execute(
            select(
                func.min(MarketData1m.open).label("day_open"),
                func.max(MarketData1m.high).label("day_high"),
                func.min(MarketData1m.low).label("day_low"),
                func.max(MarketData1m.timestamp).label("last_ts"),
            ).where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp < yesterday_cutoff,
                )
            )
        )
        row = result.one_or_none()
        if row is None or row.last_ts is None:
            return None

        # Get the actual previous day date from the last timestamp
        prev_day_date = row.last_ts.date()
        prev_day_start = datetime.combine(prev_day_date, MARKET_OPEN, tzinfo=IST)
        prev_day_end = prev_day_start + timedelta(hours=6, minutes=30)

        # Query full day's candles for accurate OHLC
        day_result = await session.execute(
            select(
                MarketData1m.open,
                MarketData1m.high,
                MarketData1m.low,
                MarketData1m.close,
                MarketData1m.timestamp,
            )
            .where(
                and_(
                    MarketData1m.symbol == symbol,
                    MarketData1m.timestamp >= prev_day_start,
                    MarketData1m.timestamp <= prev_day_end,
                )
            )
            .order_by(MarketData1m.timestamp)
        )
        candles = day_result.all()
        if not candles:
            return None

        # First candle's open = day open
        day_open = float(candles[0].open)
        day_high = max(float(c.high) for c in candles)
        day_low = min(float(c.low) for c in candles)
        # Last candle's close = day close
        day_close = float(candles[-1].close)

        return analyze_previous_day(
            open_price=day_open,
            high=day_high,
            low=day_low,
            close=day_close,
        )

    async def _get_oi_analysis(self, symbol: str) -> OIAnalysis | None:
        """Build OI analysis from the most recent oi_snapshots."""
        async with async_session_factory() as session:
            # Get the latest snapshot timestamp for this symbol
            ts_result = await session.execute(
                select(func.max(OISnapshot.timestamp)).where(
                    OISnapshot.symbol == symbol,
                )
            )
            latest_ts = ts_result.scalar_one_or_none()
            if latest_ts is None:
                return None

            # Fetch all strikes for that snapshot
            rows = await session.execute(
                select(OISnapshot).where(
                    and_(
                        OISnapshot.symbol == symbol,
                        OISnapshot.timestamp == latest_ts,
                    )
                )
            )
            snapshots = rows.scalars().all()
            if not snapshots:
                return None

        # Pivot into the format expected by analyze_option_chain:
        # [{strike_price, ce_oi, pe_oi, ce_volume, pe_volume}, ...]
        strike_map: dict[float, dict] = {}
        for snap in snapshots:
            sp = float(snap.strike_price)
            entry = strike_map.setdefault(sp, {
                "strike_price": sp,
                "ce_oi": 0,
                "pe_oi": 0,
                "ce_volume": 0,
                "pe_volume": 0,
            })
            if snap.option_type == "CE":
                entry["ce_oi"] = snap.open_interest
                entry["ce_volume"] = snap.volume
            elif snap.option_type == "PE":
                entry["pe_oi"] = snap.open_interest
                entry["pe_volume"] = snap.volume

        strikes = list(strike_map.values())
        return analyze_option_chain(strikes)

    # ------------------------------------------------------------------
    # Candle buffer management
    # ------------------------------------------------------------------

    async def _append_candle_to_buffer(self, symbol: str, candle_data: dict) -> None:
        """Append the newly closed 1m candle to the in-memory buffer.

        On first invocation for a symbol (or on a new trading day), pre-loads
        today's existing candles from the database.
        """
        today = now_ist().date()
        buffer = self._candle_buffers.get(symbol)

        # Initialise or reset buffer if it belongs to a different day
        if buffer is None or self._buffer_needs_reload(buffer, today):
            self._candle_buffers[symbol] = await self._load_todays_candles(symbol, today)

        self._candle_buffers[symbol].append({
            "o": candle_data["o"],
            "h": candle_data["h"],
            "l": candle_data["l"],
            "c": candle_data["c"],
            "v": candle_data["v"],
            "timestamp": candle_data["timestamp"],
        })

    @staticmethod
    def _buffer_needs_reload(buffer: list[dict], today: date) -> bool:
        """Return True if the buffer belongs to a previous trading day."""
        if not buffer:
            return True
        try:
            first_ts = buffer[0].get("timestamp", "")
            if isinstance(first_ts, str):
                first_date = datetime.fromisoformat(first_ts).date()
            else:
                first_date = first_ts.date()
            return first_date != today
        except (ValueError, AttributeError):
            return True

    async def _load_todays_candles(self, symbol: str, today: date) -> list[dict]:
        """Load today's 1m candles from the database."""
        today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    MarketData1m.open,
                    MarketData1m.high,
                    MarketData1m.low,
                    MarketData1m.close,
                    MarketData1m.volume,
                    MarketData1m.timestamp,
                )
                .where(
                    and_(
                        MarketData1m.symbol == symbol,
                        MarketData1m.timestamp >= today_start,
                    )
                )
                .order_by(MarketData1m.timestamp)
            )
            rows = result.all()

        return [
            {
                "o": float(r.open),
                "h": float(r.high),
                "l": float(r.low),
                "c": float(r.close),
                "v": int(r.volume),
                "timestamp": r.timestamp.isoformat(),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Indicator computation from buffer
    # ------------------------------------------------------------------

    def _aggregate_5m_candles(self, symbol: str) -> list[Candle]:
        """Aggregate 1m candles into 5m candles for pattern detection."""
        buffer = self._candle_buffers.get(symbol, [])
        if len(buffer) < 5:
            return []

        candles_5m: list[Candle] = []
        # Group in blocks of 5 from the start of the buffer
        for i in range(0, len(buffer) - 4, 5):
            chunk = buffer[i : i + 5]
            candles_5m.append(
                Candle(
                    open=chunk[0]["o"],
                    high=max(c["h"] for c in chunk),
                    low=min(c["l"] for c in chunk),
                    close=chunk[-1]["c"],
                    volume=sum(c["v"] for c in chunk),
                )
            )

        # Handle remaining candles (partial 5m bar) so the latest data is included
        remainder_start = (len(buffer) // 5) * 5
        remainder = buffer[remainder_start:]
        if remainder:
            candles_5m.append(
                Candle(
                    open=remainder[0]["o"],
                    high=max(c["h"] for c in remainder),
                    low=min(c["l"] for c in remainder),
                    close=remainder[-1]["c"],
                    volume=sum(c["v"] for c in remainder),
                )
            )

        return candles_5m

    def _calculate_vwap_from_buffer(self, symbol: str) -> VWAPResult | None:
        """Compute VWAP from today's buffered 1m candles."""
        buffer = self._candle_buffers.get(symbol, [])
        if len(buffer) < 2:
            return None

        highs = [c["h"] for c in buffer]
        lows = [c["l"] for c in buffer]
        closes = [c["c"] for c in buffer]
        volumes = [c["v"] for c in buffer]

        return calculate_vwap(highs, lows, closes, volumes)

    # ------------------------------------------------------------------
    # Strategy evaluation
    # ------------------------------------------------------------------

    async def _evaluate_strategies(
        self,
        symbol: str,
        ctx: MarketContext,
        executable: bool,
        blocked_reason: str | None,
        strategy_filter: list[StrategyName] | None = None,
    ) -> None:
        """Run each active strategy and handle any signals produced.

        Args:
            strategy_filter: If provided, only evaluate these specific strategies
                             (used by auto-mode and manual scan). If None, evaluates
                             all active strategies (legacy behavior).
        """
        if strategy_filter is not None:
            names = strategy_filter
        else:
            names = await self._get_active_strategy_names()

        if not names:
            logger.debug("No strategies to evaluate for %s", symbol)
            return

        strategies = get_active_strategies(names)

        for strategy in strategies:
            try:
                signal = strategy.evaluate(ctx)
                if signal is not None:
                    logger.info(
                        "Signal generated: %s %s %s (confidence=%.1f, executable=%s)",
                        signal.strategy_name,
                        signal.symbol,
                        signal.signal_type,
                        signal.confidence,
                        executable,
                    )
                    # Resolve option details for OPTION signals
                    if signal.instrument_type == InstrumentType.OPTION:
                        signal, executable, blocked_reason = await self._resolve_option(
                            signal, ctx, executable, blocked_reason,
                        )

                    await self._handle_signal(signal, executable, blocked_reason)
            except Exception:
                logger.exception(
                    "Error evaluating strategy %s for %s",
                    strategy.name,
                    symbol,
                )

    async def _resolve_option(
        self,
        signal: StrategySignal,
        ctx: MarketContext,
        executable: bool,
        blocked_reason: str | None,
    ) -> tuple[StrategySignal, bool, str | None]:
        """Resolve option strike, expiry, and premium for an OPTION signal.

        Enriches the signal with premium-based entry/SL/target. If resolution
        fails, the signal is kept with index-level prices but marked non-executable.
        """
        from app.services.option_resolver import resolve_option_details

        sl_pct = signal.indicators.get("sl_pct", 0.30)
        rr_multiplier = signal.indicators.get("rr_multiplier", 1.5)

        resolution = await resolve_option_details(
            symbol=signal.symbol,
            index_price=ctx.current_price,
            signal_type=signal.signal_type,
            sl_pct=sl_pct,
            rr_multiplier=rr_multiplier,
        )

        if resolution is None:
            logger.warning(
                "Option resolution failed for %s %s — signal kept but non-executable",
                signal.symbol, signal.signal_type,
            )
            # Keep the signal with index-level placeholder prices
            signal.index_entry_price = ctx.current_price
            return signal, False, blocked_reason or "Option premium unavailable"

        # Enrich signal with resolved option details
        signal.index_entry_price = ctx.current_price
        signal.strike_price = resolution.strike_price
        signal.expiry_date = resolution.expiry_date
        signal.entry_price = resolution.option_premium
        signal.stop_loss = resolution.sl_price
        signal.target_price = resolution.target_price
        signal.fyers_option_symbol = resolution.fyers_option_symbol
        signal.option_resolved = True

        # Append option details to the reason string
        signal.reason += (
            f" Option: {resolution.fyers_option_symbol}"
            f" premium={resolution.option_premium:.2f},"
            f" SL={resolution.sl_price:.2f},"
            f" target={resolution.target_price:.2f}."
        )

        # Add to indicators snapshot
        signal.indicators["option_strike"] = resolution.strike_price
        signal.indicators["option_expiry"] = str(resolution.expiry_date)
        signal.indicators["option_premium"] = resolution.option_premium
        signal.indicators["option_symbol"] = resolution.fyers_option_symbol
        signal.indicators["index_entry_price"] = ctx.current_price

        logger.info(
            "Option resolved: %s %s strike=%.0f expiry=%s premium=%.2f",
            signal.symbol, signal.fyers_option_symbol,
            resolution.strike_price, resolution.expiry_date, resolution.option_premium,
        )
        return signal, executable, blocked_reason

    async def _get_active_strategy_names(self) -> list[StrategyName]:
        """Load active strategy names from the strategy_configs table."""
        from app.models.strategy_config import StrategyConfig

        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyConfig.strategy_name).where(
                    StrategyConfig.is_active == True  # noqa: E712
                )
            )
            names = result.scalars().all()

        active: list[StrategyName] = []
        for name in names:
            try:
                active.append(StrategyName(name))
            except ValueError:
                logger.warning("Unknown strategy name in config: %s", name)
        return active

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    async def _handle_signal(
        self,
        signal: StrategySignal,
        executable: bool,
        blocked_reason: str | None,
    ) -> None:
        """Persist the signal to DB, increment daily count, and broadcast via WebSocket."""
        now = now_ist()
        signal_record = await self._persist_signal(signal, now, executable, blocked_reason)
        if signal_record is None:
            return

        # Track daily count (only count executable signals toward limit)
        if executable:
            count = self._daily_signal_count.get(signal.symbol, 0)
            self._daily_signal_count[signal.symbol] = count + 1

        # Broadcast to connected clients
        await self._broadcast_signal(signal, signal_record.id, now, executable, blocked_reason)

        # Notify agent runner for potential YOLO auto-execution
        if executable:
            try:
                from app.agent.agent_runner import agent_runner
                await agent_runner.on_new_signal(signal_record.id)
            except Exception:
                logger.exception("Error notifying agent runner of new signal")

    async def _persist_signal(
        self,
        signal: StrategySignal,
        now: datetime,
        executable: bool,
        blocked_reason: str | None,
    ) -> Signal | None:
        """Save the signal to the signals table."""
        try:
            async with async_session_factory() as session:
                record = Signal(
                    strategy_name=signal.strategy_name.value,
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    instrument_type=signal.instrument_type.value,
                    strike_price=Decimal(str(signal.strike_price)),
                    expiry_date=signal.expiry_date or now.date(),
                    entry_price=Decimal(str(signal.entry_price)),
                    stop_loss=Decimal(str(signal.stop_loss)),
                    target_price=(
                        Decimal(str(signal.target_price))
                        if signal.target_price is not None
                        else None
                    ),
                    confidence=Decimal(str(signal.confidence)),
                    status=SignalStatus.PENDING.value,
                    reason=signal.reason,
                    indicators=signal.indicators,
                    executable=executable,
                    blocked_reason=blocked_reason,
                    generated_at=now,
                    expires_at=now + timedelta(minutes=5),
                    index_entry_price=(
                        Decimal(str(signal.index_entry_price))
                        if signal.index_entry_price is not None
                        else None
                    ),
                    fyers_option_symbol=signal.fyers_option_symbol,
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
                logger.info("Signal persisted: %s (executable=%s)", record.id, executable)
                return record
        except Exception:
            logger.exception("Failed to persist signal for %s", signal.symbol)
            return None

    async def _broadcast_signal(
        self,
        signal: StrategySignal,
        signal_id,
        generated_at: datetime,
        executable: bool,
        blocked_reason: str | None,
    ) -> None:
        """Broadcast the new signal to all WebSocket clients."""
        payload = {
            "id": str(signal_id),
            "strategy_name": signal.strategy_name.value,
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "strike_price": signal.strike_price,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "target_price": signal.target_price,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "executable": executable,
            "blocked_reason": blocked_reason,
            "generated_at": generated_at.isoformat(),
        }
        await ws_manager.broadcast("signal:new", payload)


# Module-level singleton — imported by feed_manager and other services
strategy_runner = StrategyRunner()


async def get_auto_strategies_for_symbol(symbol: str) -> list[StrategyName]:
    """Return strategy names that have auto_mode=True and include this symbol."""
    from app.models.strategy_config import StrategyConfig

    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig.strategy_name, StrategyConfig.symbols).where(
                and_(
                    StrategyConfig.is_active == True,  # noqa: E712
                    StrategyConfig.auto_mode == True,  # noqa: E712
                )
            )
        )
        rows = result.all()

    matched: list[StrategyName] = []
    for name, symbols in rows:
        if symbol in (symbols or []):
            try:
                matched.append(StrategyName(name))
            except ValueError:
                pass
    return matched
