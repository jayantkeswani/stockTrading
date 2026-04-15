"""Fyers WebSocket data feed client.

Connects to Fyers' live data WebSocket and feeds ticks into the FeedManager.
Runs in a background thread (Fyers SDK uses threading internally) with an
asyncio bridge to push ticks into the async FeedManager.

Lifecycle:
- On app startup (after successful Fyers auth), call start()
- It connects to Fyers WS, subscribes to index symbols
- Each tick is forwarded to feed_manager.process_tick() via asyncio
- On market close or app shutdown, call stop()
"""

import asyncio
import logging
from threading import Thread

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

FYERS_TOKEN_KEY = "fyers:access_token"


class FyersWSClient:
    def __init__(self):
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._symbols: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _get_access_token(self) -> str | None:
        """Get access token from Redis."""
        r = get_redis()
        return await r.get(FYERS_TOKEN_KEY)

    async def fetch_quotes_rest(self):
        """Fetch latest quotes via Fyers SDK and push into feed manager.

        Works even when market is closed — returns last traded prices.
        Call this on startup and periodically to keep prices fresh.
        """
        token = await self._get_access_token()
        if not token:
            return

        try:
            from fyers_apiv3.fyersModel import FyersModel

            fyers = FyersModel(client_id=settings.fyers_app_id, token=token)
            symbols = ",".join(FYERS_SYMBOL_MAP.values())
            result = fyers.quotes({"symbols": symbols})

            if result.get("s") != "ok":
                logger.error("Fyers quotes error: %s", result.get("message"))
                return

            from app.data_feed.feed_manager import feed_manager

            for item in result.get("d", []):
                v = item.get("v", {})
                fyers_symbol = item.get("n", "")
                internal_symbol = self._fyers_to_internal(fyers_symbol)
                if not internal_symbol:
                    continue

                tick_data = {
                    "ltp": v.get("lp", 0),
                    "bid": v.get("bid", v.get("lp", 0)),
                    "ask": v.get("ask", v.get("lp", 0)),
                    "volume": v.get("volume", 0),
                    "change": v.get("ch", 0),
                    "change_pct": v.get("chp", 0),
                    "high": v.get("high_price", 0),
                    "low": v.get("low_price", 0),
                    "open": v.get("open_price", 0),
                    "prev_close": v.get("prev_close_price", 0),
                }

                await feed_manager.process_tick(internal_symbol, tick_data)

            logger.info("REST quotes fetched for %d symbols", len(result.get("d", [])))
        except Exception:
            logger.exception("Failed to fetch REST quotes")

    async def start(self, symbols: list[str] | None = None):
        """Start the Fyers WebSocket connection.

        Args:
            symbols: List of Fyers-format symbols to subscribe to.
                     Defaults to all index symbols from FYERS_SYMBOL_MAP.
        """
        # Get access token from Redis
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("No Fyers access token in Redis. Cannot start data feed.")
            return

        # Fetch initial quotes via REST immediately (works even after hours)
        await self.fetch_quotes_rest()

        # Build full token string: "app_id:access_token"
        full_token = f"{settings.fyers_app_id}:{access_token}"

        # Default symbols: all index symbols
        self._symbols = symbols or list(FYERS_SYMBOL_MAP.values())
        self._loop = asyncio.get_running_loop()

        # Fyers SDK uses threading internally, so we need to import and create
        # the socket in a way that works with our async architecture
        try:
            from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

            self._ws = FyersDataSocket(
                access_token=full_token,
                litemode=False,
                reconnect=True,
                on_message=self._on_message,
                on_error=self._on_error,
                on_connect=self._on_connect,
                on_close=self._on_close,
                reconnect_retry=10,
            )

            # Subscribe and connect (runs in background thread)
            self._ws.subscribe(symbols=self._symbols, data_type="SymbolUpdate")
            self._ws.connect()

            logger.info(
                "Fyers WebSocket connecting for symbols: %s",
                [s.split(":")[-1] for s in self._symbols],
            )
        except Exception:
            logger.exception("Failed to start Fyers WebSocket")

    async def stop(self):
        """Stop the Fyers WebSocket connection."""
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                logger.exception("Error closing Fyers WebSocket")
        self._connected = False
        self._ws = None
        logger.info("Fyers WebSocket stopped")

    async def subscribe_symbols(self, symbols: list[str]):
        """Subscribe to additional symbols on an existing connection."""
        if self._ws and self._connected:
            try:
                self._ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
                self._symbols.extend(symbols)
                logger.info("Subscribed to additional symbols: %s", symbols)
            except Exception:
                logger.exception("Failed to subscribe to additional symbols")

    def _on_message(self, message):
        """Callback from Fyers SDK (runs in Fyers' thread).

        Bridges the tick data into our async event loop via call_soon_threadsafe.
        """
        if not self._loop or not message:
            return

        try:
            # Fyers sends a list of tick dicts or a single dict
            ticks = message if isinstance(message, list) else [message]

            for tick in ticks:
                if not isinstance(tick, dict):
                    continue

                # Extract symbol and normalize to our internal format
                fyers_symbol = tick.get("symbol", "")
                internal_symbol = self._fyers_to_internal(fyers_symbol)
                if not internal_symbol:
                    continue

                tick_data = {
                    "ltp": tick.get("ltp", 0),
                    "bid": tick.get("bid", tick.get("ltp", 0)),
                    "ask": tick.get("ask", tick.get("ltp", 0)),
                    "volume": tick.get("vol_traded_today", 0),
                    "change": tick.get("ch", 0),
                    "change_pct": tick.get("chp", 0),
                    "high": tick.get("high_price", 0),
                    "low": tick.get("low_price", 0),
                    "open": tick.get("open_price", 0),
                    "prev_close": tick.get("prev_close_price", 0),
                }

                # Schedule async processing in the main event loop
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._process_tick_async(internal_symbol, tick_data),
                )
        except Exception:
            logger.exception("Error processing Fyers tick message")

    async def _process_tick_async(self, symbol: str, tick_data: dict):
        """Async handler that feeds the tick into FeedManager."""
        from app.data_feed.feed_manager import feed_manager

        await feed_manager.process_tick(symbol, tick_data)

    def _on_connect(self):
        """Called when Fyers WebSocket connects."""
        self._connected = True
        logger.info("Fyers WebSocket connected")

        # Broadcast connection status to frontend
        if self._loop:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast_connection_status(True),
            )

    def _on_close(self):
        """Called when Fyers WebSocket disconnects."""
        self._connected = False
        logger.warning("Fyers WebSocket disconnected")

        if self._loop:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast_connection_status(False),
            )

    def _on_error(self, error):
        """Called on Fyers WebSocket error."""
        logger.error("Fyers WebSocket error: %s", error)

    async def _broadcast_connection_status(self, connected: bool):
        """Broadcast Fyers connection status to frontend via WebSocket."""
        from app.websocket.manager import ws_manager

        await ws_manager.broadcast("connection:status", {
            "connected": connected,
            "source": "fyers",
        })

    @staticmethod
    def _fyers_to_internal(fyers_symbol: str) -> str | None:
        """Convert Fyers symbol format to our internal symbol name.

        E.g., "NSE:NIFTY50-INDEX" -> "NIFTY"
        """
        # Reverse lookup from FYERS_SYMBOL_MAP
        for internal, fyers in FYERS_SYMBOL_MAP.items():
            if fyers == fyers_symbol:
                return internal

        # For option contracts, pass through as-is
        if ":" in fyers_symbol:
            return fyers_symbol

        return None


# Singleton
fyers_ws_client = FyersWSClient()
