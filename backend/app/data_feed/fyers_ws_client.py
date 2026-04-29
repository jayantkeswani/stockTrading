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
from datetime import datetime
from threading import Thread

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP
from app.core.redis import get_redis
from app.core.utils import is_market_open, now_ist

logger = logging.getLogger(__name__)

FYERS_TOKEN_KEY = "fyers:access_token"


class FyersWSClient:
    def __init__(self):
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = False
        self._symbols: list[str] = []
        # Reverse map: Fyers symbol → internal short name.
        # Seeded from FYERS_SYMBOL_MAP (indices), extended when stock symbols
        # are subscribed via register_symbol_map().
        self._reverse_map: dict[str, str] = {v: k for k, v in FYERS_SYMBOL_MAP.items()}
        # Reconnect tracking for gap backfill
        self._was_ever_connected: bool = False
        self._last_disconnect_at: datetime | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _get_access_token(self) -> str | None:
        """Get access token from Redis."""
        r = get_redis()
        return await r.get(FYERS_TOKEN_KEY)

    async def fetch_quotes_rest(self, extra_symbols: dict[str, str] | None = None):
        """Fetch latest quotes via Fyers SDK and push into feed manager.

        Works even when market is closed — returns last traded prices.
        Call this on startup and periodically to keep prices fresh.

        Args:
            extra_symbols: Additional {internal_name: fyers_symbol} pairs to fetch
                           (e.g. strategy-configured stocks). Merged with FYERS_SYMBOL_MAP.
        """
        token = await self._get_access_token()
        if not token:
            return

        # Build combined symbol map: defaults + extras
        all_symbols = dict(FYERS_SYMBOL_MAP)
        if extra_symbols:
            all_symbols.update(extra_symbols)

        # Build reverse map for looking up internal name from fyers symbol
        reverse_map = {v: k for k, v in all_symbols.items()}

        try:
            from fyers_apiv3.fyersModel import FyersModel

            fyers = FyersModel(client_id=settings.fyers_app_id, token=token)

            # Fyers quotes() accepts max 50 symbols at a time
            fyers_symbols = list(all_symbols.values())
            from app.data_feed.feed_manager import feed_manager

            fetched = 0
            for i in range(0, len(fyers_symbols), 50):
                batch = fyers_symbols[i:i + 50]
                result = fyers.quotes({"symbols": ",".join(batch)})

                if result.get("s") != "ok":
                    logger.error("Fyers quotes error: %s", result.get("message"))
                    continue

                for item in result.get("d", []):
                    v = item.get("v", {})
                    fyers_symbol = item.get("n", "")
                    internal_symbol = reverse_map.get(fyers_symbol)
                    if not internal_symbol:
                        # Fallback to existing reverse lookup
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

                    fyers_alias = fyers_symbol if fyers_symbol != internal_symbol else None
                    await feed_manager.process_tick(
                        internal_symbol, tick_data, fyers_alias=fyers_alias,
                    )
                    fetched += 1

            logger.info("REST quotes fetched for %d symbols", fetched)
        except Exception:
            logger.exception("Failed to fetch REST quotes")

    async def start(
        self,
        symbols: list[str] | None = None,
        extra_symbols: list[str] | None = None,
    ):
        """Start the Fyers WebSocket connection.

        Args:
            symbols: List of Fyers-format symbols to subscribe to.
                     Defaults to all index symbols from FYERS_SYMBOL_MAP.
            extra_symbols: Additional symbols to subscribe (e.g. watchlist items).
                           Merged with the main symbols list, deduplicated.
        """
        # Get access token from Redis
        access_token = await self._get_access_token()
        if not access_token:
            logger.error("No Fyers access token in Redis. Cannot start data feed.")
            return

        # Fetch initial quotes for default symbols via REST (works even after hours)
        await self.fetch_quotes_rest()
        # Note: strategy-configured symbols are fetched separately in main.py

        # Build full token string: "app_id:access_token"
        full_token = f"{settings.fyers_app_id}:{access_token}"

        # Default symbols: all index symbols + any extras (watchlist)
        base = symbols or list(FYERS_SYMBOL_MAP.values())
        all_symbols = list(dict.fromkeys(base + (extra_symbols or [])))
        self._symbols = all_symbols
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

            # Connect first (validates token, starts WS thread), then subscribe.
            # subscribe() requires __valid_token=True, which is set during connect().
            self._ws.connect()
            self._ws.subscribe(symbols=self._symbols, data_type="SymbolUpdate")

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

    def register_symbol_map(self, symbol_map: dict[str, str]):
        """Register internal→Fyers symbol mappings for reverse lookup.

        Call this whenever new stock symbols are added to strategies or watchlist
        so that _fyers_to_internal can convert them back to short names.

        Args:
            symbol_map: {internal_name: fyers_symbol} e.g. {"TCS": "NSE:TCS-EQ"}
        """
        for internal, fyers in symbol_map.items():
            self._reverse_map[fyers] = internal

    def is_symbol_subscribed(self, fyers_symbol: str) -> bool:
        """Check if a Fyers symbol is already in the subscription list."""
        return fyers_symbol in self._symbols

    async def subscribe_symbols(
        self,
        symbols: list[str],
        symbol_map: dict[str, str] | None = None,
    ):
        """Subscribe to additional symbols on an existing connection.

        Args:
            symbols: Fyers-format symbols to subscribe (e.g. ["NSE:TCS-EQ"])
            symbol_map: Optional {internal_name: fyers_symbol} for reverse lookup.
                        If provided, registers the mapping so ticks are correctly
                        converted to internal short names.
        """
        if symbol_map:
            self.register_symbol_map(symbol_map)
        if self._ws and self._connected:
            new_symbols = [s for s in symbols if s not in self._symbols]
            if not new_symbols:
                return
            try:
                self._ws.subscribe(symbols=new_symbols, data_type="SymbolUpdate")
                self._symbols.extend(new_symbols)
                logger.info("Subscribed to additional symbols: %s", new_symbols)
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

                # Pass fyers_alias so feed_manager can cache under both names.
                # This is needed when internal != fyers (e.g. "TCS" vs "NSE:TCS-EQ")
                # so that watchlist (which uses Fyers symbols) can still find prices.
                fyers_alias = fyers_symbol if fyers_symbol != internal_symbol else None

                # Schedule async processing in the main event loop
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._process_tick_async(internal_symbol, tick_data, fyers_alias),
                )
        except Exception:
            logger.exception("Error processing Fyers tick message")

    async def _process_tick_async(
        self, symbol: str, tick_data: dict, fyers_alias: str | None = None,
    ):
        """Async handler that feeds the tick into FeedManager."""
        from app.data_feed.feed_manager import feed_manager

        await feed_manager.process_tick(symbol, tick_data, fyers_alias=fyers_alias)

    def _on_connect(self):
        """Called when Fyers WebSocket connects (initial or reconnect)."""
        # Snapshot and clear the disconnect timestamp atomically before async work
        disconnect_at = self._last_disconnect_at
        self._last_disconnect_at = None
        is_reconnect = self._was_ever_connected
        self._was_ever_connected = True
        self._connected = True
        logger.info("Fyers WebSocket connected")

        # Re-subscribe ALL symbols on reconnect — the SDK only replays the
        # last subscribe() call; symbols added incrementally are lost.
        if is_reconnect and self._symbols and self._ws:
            try:
                self._ws.subscribe(symbols=self._symbols, data_type="SymbolUpdate")
                logger.info(
                    "Re-subscribed %d symbols after reconnect", len(self._symbols),
                )
            except Exception:
                logger.exception("Failed to re-subscribe after reconnect")

        if self._loop:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast_connection_status(True),
            )
            # Reconnect (not initial connect) — trigger gap backfill
            if disconnect_at is not None:
                reconnect_at = now_ist()
                logger.info(
                    "Reconnect detected — scheduling gap backfill %s → %s",
                    disconnect_at.strftime("%H:%M:%S"),
                    reconnect_at.strftime("%H:%M:%S"),
                )
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._run_gap_backfill(disconnect_at, reconnect_at),
                )

    def _on_close(self, *args):
        """Called when Fyers WebSocket disconnects."""
        self._connected = False
        logger.warning("Fyers WebSocket disconnected")

        # Record disconnect time only when mid-session during market hours
        if self._was_ever_connected and is_market_open():
            self._last_disconnect_at = now_ist()
            logger.info(
                "Disconnect at %s captured for gap backfill",
                self._last_disconnect_at.strftime("%H:%M:%S"),
            )
            if self._loop:
                self._loop.call_soon_threadsafe(
                    asyncio.ensure_future,
                    self._clear_in_progress_candles(),
                )

        if self._loop:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast_connection_status(False),
            )

    def _on_error(self, error):
        """Called on Fyers WebSocket error."""
        logger.error("Fyers WebSocket error: %s", error)

        # Detect Fyers auth failures and trigger automatic re-login
        error_str = str(error).lower() if error else ""
        auth_signals = ("invalid token", "token expired", "code -16", "code -17", "unauthoriz")
        if any(s in error_str for s in auth_signals) and self._loop:
            logger.warning("Auth failure on WS error — scheduling reauth + restart")
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._trigger_reauth_and_restart(),
            )

    async def _broadcast_connection_status(self, connected: bool):
        """Broadcast Fyers connection status to frontend via WebSocket."""
        from app.websocket.manager import ws_manager

        await ws_manager.broadcast("connection:status", {
            "connected": connected,
            "source": "fyers",
        })

    async def _clear_in_progress_candles(self) -> None:
        """Clear stale partial candles so the seam candle isn't emitted on reconnect."""
        from app.data_feed.feed_manager import feed_manager
        feed_manager.clear_in_progress_candles()

    async def _run_gap_backfill(self, start: datetime, end: datetime) -> None:
        """Fetch and persist 1m candles for the disconnect gap [start, end].

        Uses the same idempotent upsert as startup backfill — does NOT re-trigger
        strategy evaluation for gap candles, so strategy state picks up from live ticks.
        """
        import asyncio as _asyncio

        from app.core.retry import async_retry
        from app.services.candle_backfill import (
            _fetch_history_range_via_sdk,
            _get_all_backfill_symbols,
            _persist_candles,
        )

        logger.info(
            "Gap backfill: fetching %s → %s for all symbols",
            start.strftime("%H:%M:%S"), end.strftime("%H:%M:%S"),
        )

        r = get_redis()
        token = await r.get(FYERS_TOKEN_KEY)
        if not token:
            logger.warning("No Fyers token — skipping gap backfill")
            return

        symbols = await _get_all_backfill_symbols()
        from_date = start.date()
        to_date = end.date()
        total = 0

        for internal, fyers_symbol in symbols.items():
            try:
                candles = await async_retry(
                    _asyncio.to_thread,
                    _fetch_history_range_via_sdk, token, fyers_symbol, from_date, to_date,
                    retries=3,
                    base_delay=1.0,
                    label=f"gap_backfill:{internal}",
                )
                if candles:
                    count = await _persist_candles(internal, candles)
                    total += count
            except Exception:
                logger.exception("Gap backfill failed for %s", internal)

        logger.info("Gap backfill complete: %d candles inserted", total)

    async def _trigger_reauth_and_restart(self) -> None:
        """Handle WS auth failure: re-login and restart the connection."""
        try:
            from app.data_feed.fyers_auto_login import trigger_reauth
            await trigger_reauth()
            logger.info("Reauth completed — restarting WebSocket")
            await self.stop()
            await self.start(symbols=self._symbols)
        except Exception:
            logger.exception("Reauth + WS restart failed")

    def _fyers_to_internal(self, fyers_symbol: str) -> str | None:
        """Convert Fyers symbol format to our internal symbol name.

        E.g., "NSE:NIFTY50-INDEX" -> "NIFTY", "NSE:TCS-EQ" -> "TCS"

        Uses the instance _reverse_map which is seeded from FYERS_SYMBOL_MAP
        (indices) and extended via register_symbol_map() when stock symbols
        are subscribed.
        """
        # Fast O(1) lookup in the reverse map (indices + registered stocks)
        internal = self._reverse_map.get(fyers_symbol)
        if internal:
            return internal

        # For option/futures contracts (not registered), pass through as-is
        # e.g. "NSE:NIFTY2642124000CE", "NSE:TCS25AprFUT"
        if ":" in fyers_symbol:
            return fyers_symbol

        return None


# Singleton
fyers_ws_client = FyersWSClient()
