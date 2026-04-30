"""Live data feed manager.

Manages Fyers WebSocket subscription for real-time price ticks.
Aggregates ticks into candles, persists them to MarketData1m, publishes
to Redis, and triggers strategy evaluation via the strategy runner.
"""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.redis import cache_price, publish_event
from app.models.market_data import MarketData1m
from app.services.strategy_runner import strategy_runner
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


class FeedManager:
    # Limit concurrent DB-heavy operations (candle persist + strategy eval)
    # to avoid exhausting the connection pool when many symbols close candles
    # at the same minute boundary.
    _db_semaphore: asyncio.Semaphore | None = None

    def __init__(self):
        self._running = False
        self._subscribed_symbols: set[str] = set()
        self._current_candles: dict[str, dict] = {}  # symbol -> candle being built
        self._task: asyncio.Task | None = None
        # Last seen vol_traded_today per symbol.
        # Fyers sends cumulative daily volume — we compute the delta to get
        # the actual volume traded since the previous tick.
        self._last_vol_today: dict[str, int] = {}
        # Timestamp of the most recent tick (WS or REST). Used by the liveness
        # watchdog in FyersWSClient to detect a silently frozen WS connection.
        self._last_tick_at: datetime | None = None

    @property
    def db_semaphore(self) -> asyncio.Semaphore:
        if self._db_semaphore is None:
            self._db_semaphore = asyncio.Semaphore(10)
        return self._db_semaphore

    async def start(self, symbols: list[str]):
        """Start receiving price data for given symbols."""
        self._subscribed_symbols = set(symbols)
        self._running = True
        logger.info("Feed manager started for: %s", symbols)

    async def stop(self):
        """Stop the feed manager."""
        self._running = False
        self._subscribed_symbols.clear()
        if self._task:
            self._task.cancel()
        logger.info("Feed manager stopped")

    async def process_tick(
        self, symbol: str, tick_data: dict, fyers_alias: str | None = None,
    ):
        """Process an incoming price tick.

        Updates Redis cache and broadcasts to WebSocket clients.

        Args:
            symbol: Internal short name (e.g. "TCS", "NIFTY"). Used for DB
                    persistence, candle aggregation, and strategy evaluation.
            tick_data: Tick fields (ltp, bid, ask, volume, change, change_pct).
            fyers_alias: Optional Fyers-qualified name (e.g. "NSE:TCS-EQ").
                         When provided, the price is ALSO cached and broadcast
                         under this name so that watchlist/positions (which use
                         Fyers symbols) can look up prices.
        """
        self._last_tick_at = datetime.now(IST)
        ltp = tick_data.get("ltp", 0)
        timestamp = self._last_tick_at.isoformat()

        price_data = {
            "symbol": symbol,
            "ltp": ltp,
            "bid": tick_data.get("bid", ltp),
            "ask": tick_data.get("ask", ltp),
            "volume": tick_data.get("volume", 0),
            "change": tick_data.get("change", 0),
            "change_pct": tick_data.get("change_pct", 0),
            "timestamp": timestamp,
        }

        # Cache in Redis under internal name
        await cache_price(symbol, price_data)

        # Also cache under Fyers alias (for watchlist/positions that use Fyers symbols)
        if fyers_alias:
            alias_data = {**price_data, "symbol": fyers_alias}
            await cache_price(fyers_alias, alias_data)

        # Broadcast to WebSocket clients under internal name
        await ws_manager.broadcast_price(symbol, price_data)
        # Also broadcast under Fyers alias so watchlist subscribers get updates
        if fyers_alias:
            await ws_manager.broadcast_price(fyers_alias, alias_data)

        # Aggregate into candles (always under internal name for DB consistency)
        await self._aggregate_candle(symbol, ltp, tick_data.get("volume", 0))

    async def _aggregate_candle(self, symbol: str, price: float, volume: int):
        """Aggregate ticks into 1-minute candles.

        `volume` from Fyers is vol_traded_today — cumulative since market open.
        We compute the per-tick delta against the last seen value so each candle
        accumulates only the volume actually traded in that minute.
        A drop in vol_traded_today (new day, reconnect reset) is treated as a
        full reset by clamping the delta to zero.
        """
        now = datetime.now(IST)
        minute_key = now.strftime("%Y-%m-%d %H:%M")

        last_vol = self._last_vol_today.get(symbol, 0)
        vol_delta = max(0, volume - last_vol)
        self._last_vol_today[symbol] = volume

        if symbol not in self._current_candles:
            self._current_candles[symbol] = {}

        candle = self._current_candles[symbol]
        if candle.get("minute_key") != minute_key:
            # New minute — emit previous candle if exists
            if candle.get("minute_key"):
                await self._emit_candle(symbol, candle)

            # Start new candle — first tick of the minute contributes its delta
            self._current_candles[symbol] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": vol_delta,
                "timestamp": now.isoformat(),
            }
        else:
            # Update current candle
            candle["high"] = max(candle["high"], price)
            candle["low"] = min(candle["low"], price)
            candle["close"] = price
            candle["volume"] += vol_delta

    async def _emit_candle(self, symbol: str, candle: dict):
        """Emit a completed candle: persist to DB, publish to Redis/WS, trigger strategy."""
        event_data = {
            "symbol": symbol,
            "timeframe": "1m",
            "o": candle["open"],
            "h": candle["high"],
            "l": candle["low"],
            "c": candle["close"],
            "v": candle["volume"],
            "timestamp": candle["timestamp"],
        }

        # Persist to MarketData1m (fire-and-forget, don't block tick processing)
        asyncio.create_task(
            self._persist_candle(symbol, candle),
            name=f"persist_candle:{symbol}",
        )

        await publish_event(f"candle:{symbol}", json.dumps(event_data))
        await ws_manager.broadcast("price:candle", event_data)

        # Trigger auto-mode strategy evaluation on candle close.
        # Only evaluates strategies that have auto_mode=True and include
        # this symbol in their configured symbols list.
        asyncio.create_task(
            self._run_auto_strategy_evaluation(symbol, event_data),
            name=f"strategy_eval:{symbol}",
        )

    async def _persist_candle(self, symbol: str, candle: dict):
        """Save a completed 1m candle to the MarketData1m table.

        Uses ON CONFLICT DO NOTHING to handle the case where today's
        backfill already inserted a candle for this minute.
        """
        try:
            ts = candle["timestamp"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)

            # Normalize to minute boundary so ON CONFLICT catches any backfill duplicate.
            # Backfill stores clean :00 timestamps; WS fires at :45–:59 of the minute.
            # Without normalization both rows pass the unique constraint and the zero-vol
            # WS entry pollutes VWAP and 5m bar aggregation.
            ts = ts.replace(second=0, microsecond=0)

            async with self.db_semaphore:
                async with async_session_factory() as session:
                    stmt = pg_insert(MarketData1m).values(
                        symbol=symbol,
                        timestamp=ts,
                        open=Decimal(str(candle["open"])),
                        high=Decimal(str(candle["high"])),
                        low=Decimal(str(candle["low"])),
                        close=Decimal(str(candle["close"])),
                        volume=int(candle["volume"]),
                    ).on_conflict_do_nothing(
                        constraint="uq_market_data_symbol_time",
                    )
                    await session.execute(stmt)
                    await session.commit()
        except Exception:
            logger.exception("Failed to persist candle for %s", symbol)

    async def _run_auto_strategy_evaluation(self, symbol: str, candle_data: dict):
        """Trigger strategy evaluation only for auto-mode strategies that cover this symbol."""
        try:
            from app.services.strategy_runner import get_auto_strategies_for_symbol

            async with self.db_semaphore:
                auto_strategies = await get_auto_strategies_for_symbol(symbol)
            if not auto_strategies:
                return
            await strategy_runner.on_candle_close(
                symbol, candle_data, strategy_filter=auto_strategies,
            )
        except Exception:
            logger.exception("Strategy evaluation failed for %s", symbol)


    def clear_in_progress_candles(self) -> None:
        """Clear partially-built candles on WS disconnect.

        Prevents the seam candle (built with pre-disconnect ticks) from being
        emitted with incorrect OHLC when ticks resume after reconnect.

        Preserves _last_vol_today baselines — Fyers vol_traded_today is
        cumulative and continues from where it left off after reconnect.
        Clearing the baseline would make the first post-reconnect tick dump
        the entire day's cumulative volume into a single candle. Keeping the
        old baseline produces the correct delta (volume traded during the gap).
        Day-boundary resets are handled by the max(0, ...) clamp in
        _aggregate_candle: when vol_traded_today drops (new session), delta = 0.
        """
        count = len(self._current_candles)
        self._current_candles.clear()
        if count:
            logger.info("Cleared %d in-progress candles on WS disconnect", count)


feed_manager = FeedManager()
