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
)
from app.services.position_sizing import calculate_lots, vix_to_multiplier
from app.services.strategy_params import get_strategy_params, parse_trading_windows, parse_dead_zone
from app.services.trading_config import get_trading_config
from app.core.database import async_session_factory
from app.core.enums import InstrumentType, SignalStatus, StrategyName
from app.core.redis import get_cached_price, get_redis
from app.core.utils import (
    get_custom_window_state,
    is_in_custom_trading_window,
    is_past_close_deadline,
    now_ist,
)
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

            # Global risk limits (max trades, drawdown) — per-strategy checks run inside _evaluate_strategies
            executable, blocked_reason = await self._check_global_risk_limits(symbol)

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

        executable, blocked_reason = await self._check_global_risk_limits(symbol)

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

        # Load per-strategy params
        params = await get_strategy_params(strategy_name.value)
        if strategy_name == StrategyName.INTRADAY_FUTURES:
            await self._enrich_strategy5_params(symbol, params)
        ctx.strategy_params = params

        # Per-strategy risk limits (windows, VIX)
        strat_ok, strat_reason = self._check_strategy_risk_limits(params, ctx.india_vix)
        if not strat_ok and executable:
            executable = False
            blocked_reason = strat_reason

        # Compute window state from strategy's own windows
        windows = parse_trading_windows(params)
        dead_zone = parse_dead_zone(params)
        window_state = get_custom_window_state(windows=windows, dead_zone=dead_zone)

        signal = strategy.evaluate(ctx)
        await self._flush_strategy_logs(strategy)
        if signal is not None:
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

            # Confidence gating — execution threshold
            min_conf = params.get("min_confidence_for_execution")
            if min_conf is not None and executable and signal.confidence < min_conf:
                executable = False
                blocked_reason = f"Confidence below threshold ({signal.confidence:.0f} < {min_conf:.0f})"

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

    async def _check_global_risk_limits(self, symbol: str) -> tuple[bool, str | None]:
        """Check global risk limits (max trades/day, drawdown, F&O ban list).

        These are truly global — not strategy-specific. Trade windows and VIX
        thresholds are now per-strategy (see _check_strategy_risk_limits).
        """
        # F&O ban list — block new positions in MWPL-breached securities (safety net;
        # screener already filters these out pre-market, but intraday watchlist changes
        # or manual evaluations could still reach here)
        today = now_ist().date()
        try:
            from app.core.redis import get_redis
            import json as _json
            r = get_redis()
            ban_raw = await r.get(f"nse:fo_ban_list:{today}")
            if ban_raw:
                ban_set = set(_json.loads(ban_raw))
                if symbol.upper() in ban_set:
                    logger.info("Symbol %s is on the NSE F&O ban list — signal not executable", symbol)
                    return False, f"{symbol} is on NSE F&O ban list"
        except Exception:
            logger.debug("Could not read F&O ban list from Redis for %s — skipping check", symbol)

        # Max trades per day
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

    def _check_strategy_risk_limits(
        self, params: dict, india_vix: float | None,
    ) -> tuple[bool, str | None]:
        """Check per-strategy risk limits (trading windows, VIX threshold).

        Returns (executable, blocked_reason). Only called inside _evaluate_strategies.
        """
        # Trading window — only if strategy defines windows (CAN SLIM has none)
        windows = parse_trading_windows(params)
        if windows and not is_in_custom_trading_window(windows=windows):
            return False, "Outside trade window"

        # VIX threshold — strategy-specific key name
        vix_threshold = params.get("vix_extreme") or params.get("max_vix")
        if vix_threshold is not None and india_vix is not None and india_vix >= vix_threshold:
            logger.warning(
                "India VIX %.2f >= %.2f strategy threshold — signal not executable",
                india_vix, vix_threshold,
            )
            return False, f"VIX extreme ({india_vix:.1f} >= {vix_threshold})"

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

        # For index symbols, build 5m candles with futures volume (reliable)
        candles_5m_futures_volume = None
        if symbol in self._index_futures_info:
            candles_5m_futures_volume = self._aggregate_5m_candles_with_futures_volume(symbol)

        # ATR from 5-min candles (needs 15+ candles for a meaningful 14-period ATR)
        atr_5m = None
        if len(candles_5m) >= 15:
            from app.indicators.atr import compute_atr
            atr_5m = compute_atr(candles_5m, period=14)

        # Today's opening price from earliest candle in buffer
        buffer = self._candle_buffers.get(symbol, [])
        today_open = buffer[0]["o"] if buffer else None

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

        needs_daily = await self._is_canslim_symbol(symbol) or await self._is_strategy5_symbol(symbol)

        if await self._is_canslim_symbol(symbol):
            canslim_data = await self._get_canslim_fundamentals(symbol)
            if canslim_data and canslim_data.relative_strength_rating is not None:
                relative_strength = float(canslim_data.relative_strength_rating)

        if needs_daily:
            candles_daily = await self._get_daily_candles(symbol)
            if candles_daily:
                from app.indicators.volume_analysis import compute_avg_volume
                daily_volumes = [c.volume for c in candles_daily]
                volume_avg_20d = compute_avg_volume(daily_volumes, period=20)

        # Global market cues from Redis (populated by global_market_task every 15 min)
        global_cues = await _get_global_cues_from_redis()

        # Composite intraday bias — uses 1m candle buffer from today
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
            as_of=now_ist(),
        )

        # Publish bias to Redis + WS for index symbols so the dashboard header can display it
        if symbol in INDEX_SYMBOLS and intraday_bias is not None:
            try:
                r = get_redis()
                updated_at = now_ist().isoformat()
                await r.setex(
                    f"indicator:intraday_bias:{symbol}",
                    600,
                    f"{intraday_bias.bias.value}|{intraday_bias.strength}|{intraday_bias.score:.4f}|{updated_at}",
                )
                await ws_manager.broadcast("market:bias_update", {
                    "symbol": symbol,
                    "bias": intraday_bias.bias.value,
                    "strength": intraday_bias.strength,
                    "score": round(intraday_bias.score, 4),
                    "updated_at": updated_at,
                })
            except Exception:
                pass

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
            candles_5m_futures_volume=candles_5m_futures_volume,
            intraday_bias=intraday_bias,
            atr_5m=atr_5m,
            today_open=today_open,
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
        """Fetch India VIX LTP from the Fyers price cache (NSE:INDIAVIX-INDEX)."""
        cached = await get_cached_price("INDIA VIX")
        if cached:
            try:
                return float(cached["ltp"])
            except (ValueError, TypeError):
                pass
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

    async def _is_strategy5_symbol(self, symbol: str) -> bool:
        """Check if this symbol is on today's Strategy 5 watchlist."""
        from app.core.utils import now_ist as _now_ist
        today = _now_ist().date()
        r = get_redis()
        raw = await r.get(f"strat5:watchlist:{today}")
        if not raw:
            return False
        import json
        watchlist = json.loads(raw)
        return any(w.get("symbol") == symbol for w in watchlist)

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

    async def _enrich_strategy5_params(self, symbol: str, params: dict) -> None:
        """Load RVOL baseline + Nifty bias from Redis and inject into strategy_params."""
        # RVOL profile
        if params.get("_rvol_profile") is None:
            try:
                r = get_redis()
                raw = await r.get(f"strat5:rvol_baseline:{symbol}")
                if raw:
                    from app.indicators.rvol import deserialize_profile
                    params["_rvol_profile"] = deserialize_profile(raw)
            except Exception:
                logger.debug("Could not load RVOL profile for %s", symbol)

        # Cross-position counts for soft enforcement
        try:
            async with async_session_factory() as session:
                from app.models.trade import Trade
                from app.models.position import Position
                from sqlalchemy import func

                today = now_ist().date()
                pos_count = await session.scalar(
                    select(func.count()).where(
                        and_(
                            Position.strategy_name == "intraday_futures",
                            Position.status == "OPEN",
                        )
                    )
                )
                trade_count = await session.scalar(
                    select(func.count()).where(
                        and_(
                            Trade.strategy_name == "intraday_futures",
                            func.date(Trade.entry_time) == today,
                        )
                    )
                )
                params["_active_position_count"] = pos_count or 0
                params["_daily_trade_count"] = trade_count or 0
        except Exception:
            logger.debug("Could not fetch Strategy 5 position/trade counts")

        # Nifty intraday bias (for alignment gate)
        try:
            nifty_buffer = self._candle_buffers.get("NIFTY", [])
            if nifty_buffer:
                nifty_1m = [
                    Candle(open=c["o"], high=c["h"], low=c["l"], close=c["c"], volume=c.get("v", 0))
                    for c in nifty_buffer
                ]
                nifty_prev = await self._get_previous_day_levels("NIFTY")
                nifty_price = nifty_buffer[-1]["c"] if nifty_buffer else 0
                global_cues = await _get_global_cues_from_redis()
                nifty_vwap = self._calculate_vwap_from_buffer("NIFTY")
                nifty_bias = compute_intraday_bias(
                    prev_day=nifty_prev,
                    candles_1m=nifty_1m,
                    vwap=nifty_vwap,
                    current_price=nifty_price,
                    global_cues=global_cues,
                    as_of=now_ist(),
                )
                params["_nifty_bias"] = nifty_bias
        except Exception:
            logger.debug("Could not compute Nifty bias for Strategy 5")

        # ORB levels from Redis (restore after restart)
        try:
            r = get_redis()
            today = now_ist().date()
            orb_raw = await r.get(f"strat5:orb:{today}:{symbol}")
            if orb_raw:
                import json as _json
                orb_data = _json.loads(orb_raw)
                for s in self._active_strategies.values():
                    load_orb = getattr(s, "load_orb_from_redis", None)
                    if load_orb:
                        load_orb(symbol, orb_data)
        except Exception:
            logger.debug("Could not load ORB levels from Redis for %s", symbol)

        # Morning briefing output (approach + max_lots cap)
        try:
            r = get_redis()
            today = now_ist().date()
            raw = await r.get(f"strat5:morning_briefing:{today}")
            if raw:
                import json as _json
                briefing = _json.loads(raw)
                params["_briefing_approach"] = briefing.get("approach", "normal")
                params["_briefing_max_lots"] = briefing.get("max_lots_recommendation", 2)
                params["_briefing_sector_bias"] = briefing.get("sector_bias", "none")
                params["_briefing_sector_avoid"] = briefing.get("sector_avoid", "none")
        except Exception:
            logger.debug("Could not load morning briefing for Strategy 5")

        # Screener composite score + gap data (for position sizing + confidence)
        try:
            r = get_redis()
            today = now_ist().date()
            wl_raw = await r.get(f"strat5:watchlist:{today}")
            if wl_raw:
                import json as _json
                watchlist = _json.loads(wl_raw)
                for item in watchlist:
                    if item.get("symbol") == symbol:
                        params["_screener_score"] = item.get("composite_score", 0)
                        params["_stock_gap_pct"] = item.get("gap_pct")
                        params["_relative_gap_pct"] = item.get("relative_gap_pct")
                        params["_gap_direction"] = item.get("gap_direction")
                        params["_stock_bias"] = item.get("bias")
                        params["_stock_bias_source"] = item.get("bias_source")
                        params["_stock_trend_strength"] = item.get("trend_strength")
                        params["_stock_trend_score"] = item.get("trend_score")
                        break
        except Exception:
            logger.debug("Could not load screener score for %s", symbol)

        # India VIX (for position sizing cap)
        # Uses get_cached_price() — same JSON dict format as feed_manager.cache_price()
        # and run_preopen_reassessment(). Raw r.get() broke when formats diverged.
        try:
            vix_cached = await get_cached_price("INDIA VIX")
            if vix_cached:
                params["_india_vix"] = float(vix_cached["ltp"])
        except Exception:
            logger.debug("Could not load India VIX for Strategy 5")

        # Global cues mid-day shift detection
        try:
            r = get_redis()
            today = now_ist().date()
            import json as _json

            morning_raw = await r.get(f"strat5:global_cues:{today}")
            if morning_raw:
                morning_cues = _json.loads(morning_raw)
                current_cues = await _get_global_cues_from_redis()
                if current_cues is not None:
                    shifts: list[tuple[str, str]] = []

                    # Crude shift: +-2% from morning snapshot
                    morning_crude = morning_cues.get("crude_pct")
                    if morning_crude is not None and current_cues.crude_pct is not None:
                        crude_delta = current_cues.crude_pct - morning_crude
                        if abs(crude_delta) >= 2.0:
                            direction = "up" if crude_delta > 0 else "down"
                            shifts.append((
                                "crude",
                                f"Crude shifted {direction} {abs(crude_delta):.1f}% since morning "
                                f"({morning_crude:.1f}% -> {current_cues.crude_pct:.1f}%)",
                            ))

                    # VIX shift: +-2 absolute from morning snapshot
                    morning_vix = morning_cues.get("us_vix")
                    if morning_vix is not None and current_cues.us_vix is not None:
                        vix_delta = current_cues.us_vix - morning_vix
                        if abs(vix_delta) >= 2.0:
                            direction = "up" if vix_delta > 0 else "down"
                            shifts.append((
                                "vix",
                                f"VIX shifted {direction} {abs(vix_delta):.1f} since morning "
                                f"({morning_vix:.1f} -> {current_cues.us_vix:.1f})",
                            ))

                    # Log each shift with debounce (60-min TTL per shift key)
                    if shifts:
                        from app.services.morning_screener import _append_agent_log
                        for shift_key, message in shifts:
                            debounce_key = f"strat5:global_shift_logged:{today}:{shift_key}"
                            already_logged = await r.get(debounce_key)
                            if not already_logged:
                                await _append_agent_log(today, "GLOBAL_SHIFT", message)
                                await r.set(debounce_key, "1", ex=3600)  # 60-min TTL
        except Exception:
            logger.debug("Could not check global cues mid-day shift for Strategy 5")

        # Intraday FUT OI direction — 4-way classification (same logic as
        # morning screener _compute_stock_score): correlate OI change with
        # price change to distinguish long_buildup / short_buildup /
        # short_covering / long_unwinding.
        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(OISnapshot.open_interest, OISnapshot.timestamp)
                    .where(
                        and_(
                            OISnapshot.symbol == symbol,
                            OISnapshot.option_type == "FUT",
                        )
                    )
                    .order_by(OISnapshot.timestamp.desc())
                    .limit(2)
                )
                snapshots = rows.all()
            if len(snapshots) == 2:
                latest_oi = snapshots[0].open_interest
                prev_oi = snapshots[1].open_interest
                if prev_oi and prev_oi > 0:
                    oi_change_pct = (latest_oi - prev_oi) / prev_oi * 100
                    oi_up = oi_change_pct > 1.0
                    oi_down = oi_change_pct < -1.0

                    # Price direction: compare current candle to candle ~10 min ago
                    buffer = self._candle_buffers.get(symbol, [])
                    price_up = True  # default if insufficient data
                    if len(buffer) >= 10:
                        price_up = buffer[-1]["c"] > buffer[-10]["c"]
                    elif len(buffer) >= 2:
                        price_up = buffer[-1]["c"] > buffer[0]["c"]

                    if oi_up and price_up:
                        oi_direction = "long_buildup"
                    elif oi_up and not price_up:
                        oi_direction = "short_buildup"
                    elif oi_down and price_up:
                        oi_direction = "short_covering"
                    elif oi_down and not price_up:
                        oi_direction = "long_unwinding"
                    else:
                        oi_direction = "flat"

                    params["_oi_change_pct"] = oi_change_pct
                    params["_oi_direction"] = oi_direction
            elif len(snapshots) == 1:
                params["_oi_direction"] = "flat"
                params["_oi_change_pct"] = 0.0
        except Exception:
            logger.debug("Could not load intraday FUT OI for %s", symbol)

    async def _flush_strategy_logs(self, strategy: BaseStrategy) -> None:
        """Drain pending log entries, ORB writes, and phase updates from Strategy 5."""
        today = now_ist().date()
        r = get_redis()

        # Flush log entries
        drain = getattr(strategy, "drain_pending_logs", None)
        if drain is not None:
            logs = drain()
            if logs:
                try:
                    from app.services.morning_screener import _append_agent_log
                    for category, message in logs:
                        await _append_agent_log(today, category, message)
                except Exception:
                    logger.debug("Failed to flush strategy logs", exc_info=True)

        # Persist ORB levels to Redis
        drain_orb = getattr(strategy, "drain_pending_orb_writes", None)
        if drain_orb is not None:
            orb_writes = drain_orb()
            for symbol, levels in orb_writes.items():
                try:
                    import json as _json
                    key = f"strat5:orb:{today}:{symbol}"
                    await r.set(key, _json.dumps(levels), ex=86400 * 90)
                except Exception:
                    logger.debug("Failed to persist ORB levels for %s", symbol)

        # Persist phase to Redis
        get_phase = getattr(strategy, "get_pending_phase", None)
        if get_phase is not None:
            phase = get_phase()
            if phase:
                try:
                    await r.set(f"strat5:phase:{today}", phase, ex=86400)
                except Exception:
                    logger.debug("Failed to persist phase")

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
        """Load today's 1m candles from the database.

        Uses DISTINCT ON (minute) keeping the earliest row per minute. Backfill
        writes clean :00 timestamps; live WS is also normalized to :00, but for
        pre-Apr-24 data where duplicates exist with sub-second timestamps and
        inflated cumulative volume, preferring the earliest row picks the
        backfill (correct delta) over the WS (cumulative dump).
        """
        from sqlalchemy import text
        today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

        async with async_session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT DISTINCT ON (date_trunc('minute', timestamp))
                        open, high, low, close, volume, timestamp
                    FROM market_data_1m
                    WHERE symbol = :symbol
                      AND timestamp >= :today_start
                    ORDER BY date_trunc('minute', timestamp), timestamp
                """),
                {"symbol": symbol, "today_start": today_start},
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

    def _aggregate_5m_candles_with_futures_volume(self, symbol: str) -> list[Candle] | None:
        """Aggregate 5m candles using index OHLC but futures volume.

        Index volume from Fyers is unreliable. This uses the same futures
        buffer that _calculate_vwap_from_buffer uses, paired with index OHLC.
        """
        if symbol not in self._index_futures_info:
            return None

        buffer = self._candle_buffers.get(symbol, [])
        _, _, fut_name = self._index_futures_info[symbol]
        fut_buffer = self._candle_buffers.get(fut_name, [])

        if len(buffer) < 5 or not fut_buffer:
            return None

        n = min(len(buffer), len(fut_buffer))
        candles_5m: list[Candle] = []

        for i in range(0, n - 4, 5):
            idx_chunk = buffer[i : i + 5]
            fut_chunk = fut_buffer[i : i + 5]
            candles_5m.append(
                Candle(
                    open=idx_chunk[0]["o"],
                    high=max(c["h"] for c in idx_chunk),
                    low=min(c["l"] for c in idx_chunk),
                    close=idx_chunk[-1]["c"],
                    volume=sum(c["v"] for c in fut_chunk),
                )
            )

        remainder_start = (n // 5) * 5
        if remainder_start < n:
            idx_rem = buffer[remainder_start:n]
            fut_rem = fut_buffer[remainder_start:n]
            if idx_rem:
                candles_5m.append(
                    Candle(
                        open=idx_rem[0]["o"],
                        high=max(c["h"] for c in idx_rem),
                        low=min(c["l"] for c in idx_rem),
                        close=idx_rem[-1]["c"],
                        volume=sum(c["v"] for c in fut_rem),
                    )
                )

        return candles_5m if candles_5m else None

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

        # Index symbols have unreliable volume from Fyers (mostly zero with
        # sporadic cumulative spikes on reconnect). Always use near-month
        # futures volumes for index VWAP — futures are the actual traded
        # instrument and have consistent per-minute volume.
        if symbol in self._index_futures_info:
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
                # Load per-strategy params and set on context
                params = await get_strategy_params(strategy.name.value)
                if strategy.name == StrategyName.INTRADAY_FUTURES:
                    await self._enrich_strategy5_params(symbol, params)
                ctx.strategy_params = params

                # Per-strategy risk limits (windows, VIX) — may override executable
                strat_executable, strat_blocked = executable, blocked_reason
                strat_ok, strat_reason = self._check_strategy_risk_limits(params, ctx.india_vix)
                if not strat_ok and strat_executable:
                    strat_executable = False
                    strat_blocked = strat_reason

                # Compute window state from strategy's own windows
                windows = parse_trading_windows(params)
                dead_zone = parse_dead_zone(params)
                window_state = get_custom_window_state(windows=windows, dead_zone=dead_zone)

                signal = strategy.evaluate(ctx)
                await self._flush_strategy_logs(strategy)
                if signal is not None:
                    signal.indicators["window_state"] = window_state

                    logger.info(
                        "Signal generated: %s %s %s (confidence=%.1f, executable=%s, window=%s)",
                        signal.strategy_name,
                        signal.symbol,
                        signal.signal_type,
                        signal.confidence,
                        strat_executable,
                        window_state,
                    )
                    # Resolve instrument-specific details
                    if signal.instrument_type == InstrumentType.OPTION:
                        signal, strat_executable, strat_blocked = await self._resolve_option(
                            signal, ctx, strat_executable, strat_blocked,
                        )
                    elif signal.instrument_type == InstrumentType.FUTURE:
                        signal, strat_executable, strat_blocked = await self._resolve_futures(
                            signal, ctx, strat_executable, strat_blocked,
                        )

                    # LLM confidence overlay — after resolve, ctx still in scope
                    ai_fields = await self._run_ai_confidence_overlay(signal, ctx)

                    # Confidence gating — execution threshold
                    min_conf = params.get("min_confidence_for_execution")
                    if min_conf is not None and strat_executable and signal.confidence < min_conf:
                        strat_executable = False
                        strat_blocked = f"Confidence below threshold ({signal.confidence:.0f} < {min_conf:.0f})"

                    await self._handle_signal(signal, strat_executable, strat_blocked, ai_fields=ai_fields)
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
        spot_entry = signal.entry_price
        signal.entry_price = resolution.ltp
        signal.fyers_futures_symbol = resolution.fyers_symbol
        signal.futures_resolved = True

        # Adjust SL/target proportionally for futures LTP vs spot price
        # This preserves pattern-based SL/target distances from the strategy
        if spot_entry > 0 and signal.stop_loss > 0:
            sl_pct = abs(spot_entry - signal.stop_loss) / spot_entry
            target_pct = abs(signal.target_price - spot_entry) / spot_entry
            is_short = signal.stop_loss > spot_entry
            if is_short:
                signal.stop_loss = resolution.ltp * (1 + sl_pct)
                signal.target_price = resolution.ltp * (1 - target_pct)
            else:
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

            # Fetch today's prior signals for the same symbol to give the LLM repetition context
            prior_signals: list[dict] = []
            try:
                today_start = now_ist().replace(hour=0, minute=0, second=0, microsecond=0)
                async with async_session_factory() as _session:
                    _result = await _session.execute(
                        select(Signal)
                        .where(
                            Signal.symbol == signal.symbol,
                            Signal.strategy_name == signal.strategy_name,
                            Signal.generated_at >= today_start,
                        )
                        .order_by(Signal.generated_at.desc())
                        .limit(5)
                    )
                    for s in _result.scalars().all():
                        prior_signals.append({
                            "time_ist": s.generated_at.astimezone(IST).strftime("%H:%M"),
                            "direction": s.signal_type,
                            "entry_price": float(s.entry_price) if s.entry_price else None,
                            "raw_confidence": float(s.confidence) if s.confidence else None,
                            "ai_adjustment": float(s.ai_adjustment) if s.ai_adjustment is not None else None,
                            "ai_summary": s.ai_summary,
                        })
            except Exception:
                logger.debug("Could not fetch prior signals for AI overlay — proceeding without history")

            result = await score_signal(signal, ctx, prior_signals=prior_signals or None)
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
                event="signal:updated", ai_fields=ai_fields,
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
        await self._broadcast_signal(signal, signal_record.id, now, executable, blocked_reason,
                                         ai_fields=ai_fields)

        # Notify agent runner (Telegram + potential YOLO auto-execution)
        try:
            from app.agent.agent_runner import agent_runner
            await agent_runner.on_new_signal(signal_record.id)
        except Exception:
            logger.exception("Error notifying agent runner of new signal")

        # Shadow agent — fire-and-forget, no gating, for signal accuracy measurement
        try:
            import asyncio
            from app.agent.shadow_executor import shadow_execute_signal
            asyncio.create_task(shadow_execute_signal(signal_record.id))
        except Exception:
            logger.exception("Shadow execute failed for signal %s", signal_record.id)

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

    # Thresholds for deciding whether a price change is meaningful enough
    # to update an existing PENDING signal vs. treating it as noise.
    _DEDUP_ENTRY_CHANGE_PCT = 0.3   # 0.3% move in entry price
    _DEDUP_CONF_CHANGE = 5.0        # 5-point confidence shift
    _DEDUP_AGE_MINUTES = 15.0       # always refresh after 15 min regardless

    async def _dedup_signal(
        self,
        signal: StrategySignal,
        now: datetime,
        executable: bool,
        blocked_reason: str | None,
    ) -> str | Signal | None:
        """Check for duplicate PENDING signals and handle accordingly.

        Three cases:
          Case 1 — pure noise (tiny price move, <15 min old, no execution):
                   return "skip" — caller does nothing.
          Case 2 — meaningful change, not yet executed:
                   update existing signal in place, return it.
          Case 3 — existing signal was acted on (shadow or real trade exists):
                   return None — caller creates a brand-new signal and preserves
                   the original as an immutable audit record.
        Returns:
            "skip"  — suppress; no write
            Signal  — updated existing record
            None    — create new signal
        """
        from app.models.trade import Trade as TradeModel

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

                # Case 3: existing signal has been executed (real or shadow trade).
                # Preserve it as an immutable audit record; caller creates a new signal.
                has_execution = existing.executed_trade_id is not None
                if not has_execution:
                    trade_check = await session.execute(
                        select(TradeModel.id)
                        .where(TradeModel.signal_id == existing.id)
                        .limit(1)
                    )
                    has_execution = trade_check.scalar_one_or_none() is not None

                if has_execution:
                    logger.debug(
                        "Dedup Case 3: %s %s has execution — creating new signal",
                        signal.symbol, signal.signal_type,
                    )
                    return None

                # Case 1 / 2: no execution yet. Decide whether the change is meaningful.
                new_entry = Decimal(str(signal.entry_price))
                new_conf = Decimal(str(signal.confidence))

                entry_change_pct = (
                    abs(float(new_entry - existing.entry_price)) / float(existing.entry_price) * 100
                    if existing.entry_price
                    else 100.0
                )
                conf_change = abs(float(new_conf - (existing.confidence or 0)))

                existing_at = existing.generated_at
                now_cmp = now.replace(tzinfo=None) if existing_at.tzinfo is None else now
                age_minutes = (now_cmp - existing_at).total_seconds() / 60

                is_meaningful = (
                    entry_change_pct > self._DEDUP_ENTRY_CHANGE_PCT
                    or conf_change > self._DEDUP_CONF_CHANGE
                    or age_minutes > self._DEDUP_AGE_MINUTES
                )

                if not is_meaningful:
                    # Case 1: pure noise — suppress
                    return "skip"

                # Case 2: meaningful update — refresh in place
                new_sl = Decimal(str(signal.stop_loss))
                new_target = (
                    Decimal(str(signal.target_price))
                    if signal.target_price is not None
                    else None
                )

                existing.entry_price = new_entry
                existing.stop_loss = new_sl
                existing.target_price = new_target
                existing.confidence = new_conf
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
                    "Signal updated (dedup): %s %s %s — entry=%.2f→%.2f "
                    "(Δ%.2f%%, conf_Δ%.1f, age %.1fmin)",
                    signal.strategy_name, signal.symbol, signal.signal_type,
                    float(existing.entry_price), float(new_entry),
                    entry_change_pct, conf_change, age_minutes,
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
        ai_fields: dict | None = None,
    ) -> None:
        """Broadcast a signal to all WebSocket clients.

        Args:
            event: "signal:new" for new signals, "signal:updated" for dedup updates.
        """
        ai = ai_fields or {}
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
            "indicators": signal.indicators,
            "ai_summary": ai.get("ai_summary"),
            "ai_rationale": ai.get("ai_rationale"),
            "ai_adjustment": ai.get("ai_adjustment"),
            "ai_action": ai.get("ai_action"),
        }
        await ws_manager.broadcast(event, payload)


# Module-level singleton — imported by feed_manager and other services
strategy_runner = StrategyRunner()


async def get_auto_strategies_for_symbol(symbol: str) -> list[StrategyName]:
    """Return strategy names that have auto_mode=True and include this symbol."""
    from app.models.strategy_config import StrategyConfig
    from app.strategies.registry import get_strategy

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
    for name, db_symbols in rows:
        try:
            strat_name = StrategyName(name)
        except ValueError:
            continue
        if symbol in (db_symbols or []):
            matched.append(strat_name)
            continue
        # Check dynamic symbol override (e.g., Strategy 5 reads from Redis)
        strategy = get_strategy(strat_name)
        if strategy is not None:
            try:
                dynamic_symbols = await strategy.get_symbols()
            except Exception:
                continue
            if dynamic_symbols is not None and symbol in dynamic_symbols:
                matched.append(strat_name)

    return matched
