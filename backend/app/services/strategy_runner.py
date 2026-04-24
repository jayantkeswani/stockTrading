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

from app.core.constants import (
    INDEX_SYMBOLS,
    IST,
    LOT_SIZES,
    MARKET_OPEN,
    VIX_EXTREME,
)
from app.services.position_sizing import calculate_lots, vix_to_multiplier
from app.services.trading_config import get_trading_config
from app.core.database import async_session_factory
from app.core.enums import InstrumentType, SignalStatus, StrategyName
from app.core.redis import get_cached_price, get_redis
from app.core.utils import is_in_trading_window, is_past_close_deadline, now_ist, get_window_state
from app.indicators.intraday_bias import compute_intraday_bias
from app.tasks.global_market_task import _get_global_cues_from_redis
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

        # Index futures subscriptions for VWAP volume sourcing.
        # Maps index symbol → (fyers_symbol, expiry_date, internal_buffer_name)
        # e.g. "NIFTY" → ("NSE:NIFTY25MAYFUT", date(2025,5,27), "NIFTY_FUT")
        self._index_futures_info: dict[str, tuple[str, date, str]] = {}
        self._futures_init_done: bool = False

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

            # Futures-volume-only buffers: collect candles for VWAP, skip strategy eval
            if self._is_futures_volume_symbol(symbol):
                return

            # Subscribe near-month index futures for VWAP volume (lazy, runs once)
            if not self._futures_init_done:
                await self._init_index_futures()

            # Roll any expired index futures contracts (O(1) date check each tick)
            await self._check_index_futures_roll()

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
            window_state = get_window_state()
            signal.indicators["window_state"] = window_state
            if signal.instrument_type == InstrumentType.OPTION:
                signal, executable, blocked_reason = await self._resolve_option(
                    signal, ctx, executable, blocked_reason,
                )
            elif signal.instrument_type == InstrumentType.FUTURE:
                signal, executable, blocked_reason = await self._resolve_futures(
                    signal, ctx, executable, blocked_reason,
                )
            ai_fields = await self._run_ai_confidence_overlay(signal, ctx)
            await self._handle_signal(signal, executable, blocked_reason, ai_fields=ai_fields)

        return signal

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _check_hard_guardrails(self) -> bool:
        """Return True if strategy evaluation should proceed.

        Hard: past close deadline (3:15 PM) — no point generating any signal.
        Soft: outside trade windows → signals still generated but marked non-executable.
              This moved to _check_risk_limits so out-of-window signals are visible in UI.
        """
        if is_past_close_deadline():
            logger.debug("Past close deadline — no new signals")
            return False
        return True

    async def _check_risk_limits(self, symbol: str) -> tuple[bool, str | None]:
        """Check risk limits and return (executable, blocked_reason).

        Signals are always generated regardless of risk limits, but these checks
        determine whether the signal can actually be traded.
        Trade-window check moved here from _check_hard_guardrails so out-of-window
        signals are persisted and visible in the UI as informational (non-executable).
        """
        # Trade window — soft: signal still generated, just not executable
        if not is_in_trading_window():
            return False, "Outside trade window"

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

        cfg = await get_trading_config()
        max_trades = cfg.max_trades_per_day
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

        cfg = await get_trading_config()
        max_dd_amount = cfg.max_drawdown_amount

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

        # CAN SLIM extensions (populated only if CAN SLIM strategy is active for this symbol)
        candles_daily = None
        volume_avg_20d = None
        relative_strength = None
        canslim_data = None

        if await self._is_canslim_symbol(symbol):
            canslim_data = await self._get_canslim_fundamentals(symbol)
            candles_daily = await self._get_daily_candles(symbol)
            if candles_daily:
                from app.indicators.volume_analysis import compute_avg_volume
                daily_volumes = [c.volume for c in candles_daily]
                volume_avg_20d = compute_avg_volume(daily_volumes, period=20)
            if canslim_data and canslim_data.relative_strength_rating is not None:
                relative_strength = float(canslim_data.relative_strength_rating)

        # Global market cues from Redis (populated by global_market_task every 15 min)
        global_cues = await _get_global_cues_from_redis()

        # Composite intraday bias — uses 1m candle buffer from today
        buffer = self._candle_buffers.get(symbol, [])
        candles_1m_today = [
            Candle(open=c["o"], high=c["h"], low=c["l"], close=c["c"], volume=c.get("v", 0))
            for c in buffer
        ]
        intraday_bias = compute_intraday_bias(
            prev_day=prev_day_levels,
            candles_1m=candles_1m_today,
            vwap=vwap_result,
            current_price=current_price,
            global_cues=global_cues,
        )

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
            candles_daily=candles_daily,
            volume_avg_20d=volume_avg_20d,
            relative_strength=relative_strength,
            canslim_data=canslim_data,
            global_cues=global_cues,
            intraday_bias=intraday_bias,
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

    async def _is_canslim_symbol(self, symbol: str) -> bool:
        """Check if this symbol is configured for the CAN SLIM strategy."""
        from app.models.strategy_config import StrategyConfig

        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyConfig.symbols).where(
                    and_(
                        StrategyConfig.strategy_name == "can_slim",
                        StrategyConfig.is_active == True,  # noqa: E712
                    )
                )
            )
            symbols = result.scalar_one_or_none()

        return symbol in (symbols or [])

    async def _get_canslim_fundamentals(self, symbol: str):
        """Fetch CAN SLIM fundamental data from stock_fundamentals table."""
        from app.models.fundamental_data import StockFundamental

        async with async_session_factory() as session:
            result = await session.execute(
                select(StockFundamental).where(StockFundamental.symbol == symbol)
            )
            return result.scalar_one_or_none()

    async def _get_daily_candles(self, symbol: str) -> list[Candle] | None:
        """Get last 90 days of daily bars from MarketData1m (aggregated).

        Fetches raw 1m candles and aggregates to daily bars in Python
        (avoids mixing window functions with GROUP BY in SQL).
        """
        today = now_ist().date()
        start_date = today - timedelta(days=120)
        start_ts = datetime.combine(start_date, MARKET_OPEN, tzinfo=IST)

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
                        MarketData1m.timestamp >= start_ts,
                    )
                )
                .order_by(MarketData1m.timestamp)
            )
            rows = result.all()

        if not rows:
            return None

        # Aggregate 1m candles into daily bars
        from collections import defaultdict
        daily: dict[date, dict] = {}
        for row in rows:
            day = row.timestamp.date()
            if day not in daily:
                daily[day] = {
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": int(row.volume or 0),
                }
            else:
                d = daily[day]
                d["high"] = max(d["high"], float(row.high))
                d["low"] = min(d["low"], float(row.low))
                d["close"] = float(row.close)  # Last candle's close
                d["volume"] += int(row.volume or 0)

        if len(daily) < 10:
            return None

        daily_candles: list[Candle] = []
        for day_key in sorted(daily.keys()):
            d = daily[day_key]
            daily_candles.append(
                Candle(
                    open=d["open"],
                    high=d["high"],
                    low=d["low"],
                    close=d["close"],
                    volume=d["volume"],
                )
            )

        return daily_candles[-90:]

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
    # Index futures subscription for VWAP volume
    # ------------------------------------------------------------------

    def _is_futures_volume_symbol(self, symbol: str) -> bool:
        """True when symbol is subscribed only to supply futures volume for VWAP."""
        return any(symbol == info[2] for info in self._index_futures_info.values())

    async def _init_index_futures(self) -> None:
        """Subscribe near-month futures for every index — called once on first candle close."""
        from app.data_feed.fyers_ws_client import fyers_ws_client
        from app.services.futures_resolver import resolve_index_futures_symbol

        for index in INDEX_SYMBOLS:
            result = await resolve_index_futures_symbol(index)
            if result is None:
                logger.warning("Could not resolve index futures for VWAP: %s", index)
                continue
            fyers_sym, expiry = result
            internal_name = f"{index}_FUT"
            self._index_futures_info[index] = (fyers_sym, expiry, internal_name)
            await fyers_ws_client.subscribe_symbols(
                [fyers_sym],
                symbol_map={internal_name: fyers_sym},
            )
            logger.info(
                "Subscribed index futures for VWAP volume: %s → %s (expires %s)",
                index, fyers_sym, expiry,
            )

        self._futures_init_done = True

    async def _check_index_futures_roll(self) -> None:
        """Re-subscribe index futures when the current contract has expired."""
        from app.data_feed.fyers_ws_client import fyers_ws_client
        from app.services.futures_resolver import resolve_index_futures_symbol

        today = now_ist().date()
        for index, (fyers_sym, expiry, internal_name) in list(self._index_futures_info.items()):
            if today <= expiry:
                continue
            result = await resolve_index_futures_symbol(index)
            if result is None:
                logger.warning("Could not roll index futures for %s — keeping stale contract", index)
                continue
            new_fyers_sym, new_expiry = result
            self._index_futures_info[index] = (new_fyers_sym, new_expiry, internal_name)
            # Clear stale futures candles so they don't pollute VWAP
            self._candle_buffers.pop(internal_name, None)
            await fyers_ws_client.subscribe_symbols(
                [new_fyers_sym],
                symbol_map={internal_name: new_fyers_sym},
            )
            logger.info(
                "Rolled index futures: %s %s → %s (expires %s)",
                index, fyers_sym, new_fyers_sym, new_expiry,
            )

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
        """Compute VWAP from today's buffered 1m candles.

        For index symbols (no traded volume), volumes are sourced from the
        near-month futures buffer which is subscribed on startup.
        """
        buffer = self._candle_buffers.get(symbol, [])
        if len(buffer) < 2:
            return None

        highs = [c["h"] for c in buffer]
        lows = [c["l"] for c in buffer]
        closes = [c["c"] for c in buffer]
        volumes = [c["v"] for c in buffer]

        # Index symbols carry zero volume — use near-month futures volumes instead
        if sum(volumes) == 0 and symbol in self._index_futures_info:
            _, _, fut_name = self._index_futures_info[symbol]
            fut_buffer = self._candle_buffers.get(fut_name, [])
            if fut_buffer:
                n = min(len(buffer), len(fut_buffer))
                highs, lows, closes = highs[:n], lows[:n], closes[:n]
                volumes = [fut_buffer[i]["v"] for i in range(n)]

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
                    # Stamp window state onto the signal indicators
                    window_state = get_window_state()
                    signal.indicators["window_state"] = window_state

                    logger.info(
                        "Signal generated: %s %s %s (confidence=%.1f, executable=%s, window=%s)",
                        signal.strategy_name,
                        signal.symbol,
                        signal.signal_type,
                        signal.confidence,
                        executable,
                        window_state,
                    )
                    # Resolve instrument-specific details
                    if signal.instrument_type == InstrumentType.OPTION:
                        signal, executable, blocked_reason = await self._resolve_option(
                            signal, ctx, executable, blocked_reason,
                        )
                    elif signal.instrument_type == InstrumentType.FUTURE:
                        signal, executable, blocked_reason = await self._resolve_futures(
                            signal, ctx, executable, blocked_reason,
                        )

                    # LLM confidence overlay — after resolve, ctx still in scope
                    ai_fields = await self._run_ai_confidence_overlay(signal, ctx)

                    await self._handle_signal(signal, executable, blocked_reason, ai_fields=ai_fields)
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
            index_sl=signal.index_sl,
            index_target=signal.index_target,
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
        if signal.index_sl is not None:
            signal.indicators["index_sl"] = signal.index_sl
        if signal.index_target is not None:
            signal.indicators["index_target"] = signal.index_target

        # Snapshot position sizing at resolution time so preview == execute
        await self._snapshot_sizing(signal, ctx)

        logger.info(
            "Option resolved: %s %s strike=%.0f expiry=%s premium=%.2f lots=%s",
            signal.symbol, signal.fyers_option_symbol,
            resolution.strike_price, resolution.expiry_date, resolution.option_premium,
            signal.lots,
        )
        return signal, executable, blocked_reason

    async def _resolve_futures(
        self,
        signal: StrategySignal,
        ctx: MarketContext,
        executable: bool,
        blocked_reason: str | None,
    ) -> tuple[StrategySignal, bool, str | None]:
        """Resolve futures contract details for a FUTURE signal.

        Enriches the signal with the Fyers futures symbol, expiry, lot size, and LTP.
        If resolution fails, the signal is kept but marked non-executable.
        """
        from app.services.futures_resolver import resolve_futures_contract

        resolution = await resolve_futures_contract(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
        )

        if resolution is None:
            logger.warning(
                "Futures resolution failed for %s — signal kept but non-executable",
                signal.symbol,
            )
            return signal, False, blocked_reason or "Futures contract unavailable"

        # Enrich signal with resolved futures details
        signal.expiry_date = resolution.expiry_date
        signal.entry_price = resolution.ltp
        signal.fyers_futures_symbol = resolution.fyers_symbol
        signal.futures_resolved = True

        # Adjust SL/target proportionally for futures LTP vs spot price
        # This preserves pattern-based SL/target distances from the strategy
        if signal.entry_price > 0 and signal.stop_loss > 0:
            sl_pct = (signal.entry_price - signal.stop_loss) / signal.entry_price
            target_pct = (signal.target_price - signal.entry_price) / signal.entry_price
            signal.stop_loss = resolution.ltp * (1 - sl_pct)
            signal.target_price = resolution.ltp * (1 + target_pct)
        else:
            from app.core.constants import CANSLIM_SL_PCT, CANSLIM_TARGET_PCT
            signal.stop_loss = resolution.ltp * (1 - CANSLIM_SL_PCT / 100)
            signal.target_price = resolution.ltp * (1 + CANSLIM_TARGET_PCT / 100)

        # Append futures details to reason
        signal.reason += (
            f" Futures: {resolution.fyers_symbol}"
            f" LTP={resolution.ltp:.2f},"
            f" lot={resolution.lot_size},"
            f" margin={resolution.margin_required:.0f}."
        )

        signal.indicators["futures_symbol"] = resolution.fyers_symbol
        signal.indicators["futures_ltp"] = resolution.ltp
        signal.indicators["futures_lot_size"] = resolution.lot_size
        signal.indicators["futures_expiry"] = str(resolution.expiry_date)
        signal.indicators["futures_margin"] = resolution.margin_required

        # Snapshot position sizing at resolution time so preview == execute
        await self._snapshot_sizing(signal, ctx)

        logger.info(
            "Futures resolved: %s → %s expiry=%s ltp=%.2f lot=%d lots=%s",
            signal.symbol, resolution.fyers_symbol,
            resolution.expiry_date, resolution.ltp, resolution.lot_size,
            signal.lots,
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

    async def _run_ai_confidence_overlay(
        self,
        signal: StrategySignal,
        ctx: MarketContext,
    ) -> dict:
        """Call the LLM confidence overlay agent. Returns dict of ai_* fields (may be empty)."""
        from app.config import settings as _settings
        if not _settings.ai_confidence_enabled:
            return {}
        try:
            from app.research.agents.signal_confidence import score_signal
            result = await score_signal(signal, ctx)
            if result.confidence_adjustment != 0:
                old = float(signal.confidence) if signal.confidence else 0.0
                new_conf = max(0.0, min(100.0, old + result.confidence_adjustment))
                signal.confidence = new_conf
                logger.info(
                    "AI confidence overlay: %s %s adj=%+d → %.1f",
                    signal.symbol, signal.signal_type, result.confidence_adjustment, new_conf,
                )
            # Persist key_supports/key_risks in indicators JSONB
            if result.key_supports:
                signal.indicators["ai_key_supports"] = result.key_supports
            if result.key_risks:
                signal.indicators["ai_key_risks"] = result.key_risks

            return {
                "ai_summary": result.summary or None,
                "ai_rationale": result.rationale or None,
                "ai_adjustment": result.confidence_adjustment if result.confidence_adjustment != 0 else None,
                "ai_action": result.recommended_action or None,
            }
        except Exception:
            logger.exception("AI confidence overlay error — using deterministic score")
            return {}

    async def _handle_signal(
        self,
        signal: StrategySignal,
        executable: bool,
        blocked_reason: str | None,
        ai_fields: dict | None = None,
    ) -> None:
        """Deduplicate, persist, and broadcast a signal.

        Dedup rules (applies to ALL strategies):
        - If an identical PENDING signal exists (same strategy, symbol, signal_type,
          entry, SL, target) → skip entirely (true duplicate)
        - If a PENDING signal exists but values changed → update it in place
        - If the prior signal was EXECUTED → create a new signal
        - Otherwise → create a new signal
        """
        now = now_ist()

        # Skip if there's already an open position for this symbol + direction
        if await self._has_open_position(signal):
            logger.debug(
                "Signal skipped: open position exists for %s %s",
                signal.symbol, signal.signal_type,
            )
            return

        # Check for existing PENDING signal for same strategy + symbol + direction
        dedup_result = await self._dedup_signal(signal, now, executable, blocked_reason)
        if dedup_result == "skip":
            logger.debug(
                "Duplicate signal skipped: %s %s %s (identical PENDING exists)",
                signal.strategy_name, signal.symbol, signal.signal_type,
            )
            return
        if isinstance(dedup_result, Signal):
            # Updated existing signal — broadcast as update (not new)
            await self._broadcast_signal(
                signal, dedup_result.id, now, executable, blocked_reason,
                event="signal:updated",
            )
            return

        # No existing PENDING signal or prior was EXECUTED — create new
        signal_record = await self._persist_signal(signal, now, executable, blocked_reason,
                                                    ai_fields=ai_fields)
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

    async def _has_open_position(self, signal: StrategySignal) -> bool:
        """Check if there's already an open position for the same symbol + direction."""
        try:
            async with async_session_factory() as session:
                from app.models.position import Position

                direction = signal.signal_type.value.replace("BUY_", "") if signal.instrument_type.value == "OPTION" else None
                query = select(Position.id).where(Position.symbol == signal.symbol)
                if direction:
                    query = query.where(Position.option_type == direction)
                result = await session.execute(query.limit(1))
                return result.scalar_one_or_none() is not None
        except Exception:
            logger.exception("Error checking open position for %s", signal.symbol)
            return False

    async def _dedup_signal(
        self,
        signal: StrategySignal,
        now: datetime,
        executable: bool,
        blocked_reason: str | None,
    ) -> str | Signal | None:
        """Check for duplicate PENDING signals and handle accordingly.

        Returns:
            "skip"  — identical PENDING signal exists, do nothing
            Signal  — existing signal was updated with new values
            None    — no match, caller should create a new signal
        """
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Signal).where(
                        and_(
                            Signal.strategy_name == signal.strategy_name.value,
                            Signal.symbol == signal.symbol,
                            Signal.signal_type == signal.signal_type.value,
                            Signal.status == SignalStatus.PENDING.value,
                        )
                    )
                    .order_by(Signal.generated_at.desc())
                    .limit(1)
                )
                existing = result.scalar_one_or_none()

                if existing is None:
                    return None

                # Compare key values to determine if anything changed
                new_entry = Decimal(str(signal.entry_price))
                new_sl = Decimal(str(signal.stop_loss))
                new_target = (
                    Decimal(str(signal.target_price))
                    if signal.target_price is not None
                    else None
                )

                entry_same = existing.entry_price == new_entry
                sl_same = existing.stop_loss == new_sl
                target_same = existing.target_price == new_target
                confidence_same = existing.confidence == Decimal(str(signal.confidence))

                if entry_same and sl_same and target_same and confidence_same:
                    # Identical — skip
                    return "skip"

                # Values changed — update the existing PENDING signal
                existing.entry_price = new_entry
                existing.stop_loss = new_sl
                existing.target_price = new_target
                existing.confidence = Decimal(str(signal.confidence))
                existing.reason = signal.reason
                existing.indicators = signal.indicators
                existing.executable = executable
                existing.blocked_reason = blocked_reason
                existing.generated_at = now
                existing.expires_at = now + timedelta(minutes=5)
                if signal.index_entry_price is not None:
                    existing.index_entry_price = Decimal(str(signal.index_entry_price))
                if signal.fyers_option_symbol:
                    existing.fyers_option_symbol = signal.fyers_option_symbol
                if signal.fyers_futures_symbol:
                    existing.fyers_futures_symbol = signal.fyers_futures_symbol
                if signal.lots is not None:
                    existing.lots = signal.lots
                    existing.quantity = signal.quantity
                    existing.sizing_meta = signal.sizing_meta

                await session.commit()
                await session.refresh(existing)

                logger.info(
                    "Signal updated (dedup): %s %s %s — entry=%.2f→%.2f",
                    signal.strategy_name, signal.symbol, signal.signal_type,
                    float(existing.entry_price), float(new_entry),
                )
                return existing

        except Exception:
            logger.exception("Error during signal dedup for %s", signal.symbol)
            return None

    async def _snapshot_sizing(self, signal: StrategySignal, ctx: MarketContext) -> None:
        """Compute and store lot sizing on the signal so preview == execute.

        Wrapped in a broad try/except so a config miss never blocks signal generation.
        """
        try:
            from app.strategies.registry import get_strategy

            is_futures = (
                signal.instrument_type.value == "FUTURE"
                if hasattr(signal.instrument_type, "value")
                else signal.instrument_type == "FUTURE"
            )
            if is_futures:
                lot_size = int((signal.indicators or {}).get("futures_lot_size", 1))
            else:
                lot_size = LOT_SIZES.get(signal.symbol, 75)

            try:
                strat = get_strategy(signal.strategy_name)
                max_lots = getattr(strat, "max_lots", None)
            except Exception:
                max_lots = None

            vix_mult = vix_to_multiplier(getattr(ctx, "india_vix", None))
            cfg = await get_trading_config()

            lots = calculate_lots(
                capital=cfg.capital,
                risk_per_trade_pct=cfg.max_risk_per_trade_pct,
                entry_price=float(signal.entry_price),
                stop_loss=float(signal.stop_loss),
                lot_size=lot_size,
                vix_multiplier=vix_mult,
                max_lots=max_lots,
            )
            signal.lots = lots
            signal.quantity = lots * lot_size
            signal.sizing_meta = {
                "capital": cfg.capital,
                "risk_pct": cfg.max_risk_per_trade_pct,
                "vix": getattr(ctx, "india_vix", None),
                "vix_multiplier": vix_mult,
                "max_lots": max_lots,
                "lot_size": lot_size,
            }
        except Exception:
            logger.debug(
                "Could not snapshot sizing for %s — will be computed at execute time",
                signal.symbol,
            )

    async def _persist_signal(
        self,
        signal: StrategySignal,
        now: datetime,
        executable: bool,
        blocked_reason: str | None,
        ai_fields: dict | None = None,
    ) -> Signal | None:
        """Save the signal to the signals table."""
        ai = ai_fields or {}
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
                    fyers_futures_symbol=signal.fyers_futures_symbol,
                    lots=signal.lots,
                    quantity=signal.quantity,
                    sizing_meta=signal.sizing_meta,
                    ai_summary=ai.get("ai_summary"),
                    ai_rationale=ai.get("ai_rationale"),
                    ai_adjustment=Decimal(str(ai["ai_adjustment"])) if ai.get("ai_adjustment") is not None else None,
                    ai_action=ai.get("ai_action"),
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
        event: str = "signal:new",
    ) -> None:
        """Broadcast a signal to all WebSocket clients.

        Args:
            event: "signal:new" for new signals, "signal:updated" for dedup updates.
        """
        payload = {
            "id": str(signal_id),
            "strategy_name": signal.strategy_name.value,
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "instrument_type": signal.instrument_type.value,
            "strike_price": signal.strike_price,
            "expiry_date": signal.expiry_date.isoformat() if signal.expiry_date else None,
            "entry_price": signal.entry_price,
            "index_entry_price": signal.index_entry_price,
            "stop_loss": signal.stop_loss,
            "target_price": signal.target_price,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "status": "PENDING",
            "executable": executable,
            "blocked_reason": blocked_reason,
            "generated_at": generated_at.isoformat(),
            "lots": signal.lots,
            "quantity": signal.quantity,
        }
        await ws_manager.broadcast(event, payload)


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
