"""Live data feed manager.

Manages Fyers WebSocket subscription for real-time price ticks.
Aggregates ticks into candles and publishes to Redis.
On candle close, triggers strategy evaluation via the strategy runner.
"""

import asyncio
import json
import logging
from datetime import datetime

from app.core.constants import IST
from app.core.redis import cache_price, publish_event
from app.services.strategy_runner import strategy_runner
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


class FeedManager:
    def __init__(self):
        self._running = False
        self._subscribed_symbols: set[str] = set()
        self._current_candles: dict[str, dict] = {}  # symbol -> candle being built
        self._task: asyncio.Task | None = None

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

    async def process_tick(self, symbol: str, tick_data: dict):
        """Process an incoming price tick.

        Updates Redis cache and broadcasts to WebSocket clients.
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

        # Cache in Redis
        await cache_price(symbol, price_data)

        # Broadcast to WebSocket clients
        await ws_manager.broadcast_price(symbol, price_data)

        # Aggregate into candles
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
        """Emit a completed candle to Redis pub/sub, WebSocket, and strategy runner."""
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
        await publish_event(f"candle:{symbol}", json.dumps(event_data))
        await ws_manager.broadcast("price:candle", event_data)

        # Trigger strategy evaluation on candle close.
        # Fire-and-forget via a task so that slow strategy evaluation
        # does not block tick processing.
        asyncio.create_task(
            self._run_strategy_evaluation(symbol, event_data),
            name=f"strategy_eval:{symbol}",
        )

    async def _run_strategy_evaluation(self, symbol: str, candle_data: dict):
        """Wrapper for strategy runner invocation with error isolation."""
        try:
            await strategy_runner.on_candle_close(symbol, candle_data)
        except Exception:
            logger.exception("Strategy evaluation failed for %s", symbol)


feed_manager = FeedManager()
