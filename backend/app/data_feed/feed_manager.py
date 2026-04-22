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
        ltp = tick_data.get("ltp", 0)
        timestamp = datetime.now(IST).isoformat()

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
        """Aggregate ticks into 1-minute candles."""
        now = datetime.now(IST)
        minute_key = now.strftime("%Y-%m-%d %H:%M")

        if symbol not in self._current_candles:
            self._current_candles[symbol] = {}

        candle = self._current_candles[symbol]
        if candle.get("minute_key") != minute_key:
            # New minute — emit previous candle if exists
            if candle.get("minute_key"):
                await self._emit_candle(symbol, candle)

            # Start new candle
            self._current_candles[symbol] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "timestamp": now.isoformat(),
            }
        else:
            # Update current candle
            candle["high"] = max(candle["high"], price)
            candle["low"] = min(candle["low"], price)
            candle["close"] = price
            candle["volume"] += volume

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


feed_manager = FeedManager()
