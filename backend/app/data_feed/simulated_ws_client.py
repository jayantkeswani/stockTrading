"""Simulated WebSocket client for the Market Simulator service.

Drop-in replacement for FyersWSClient when MARKET_MODE=simulated.
Connects to the Market Simulator's WebSocket endpoint and feeds ticks
into FeedManager using the same interface and tick_data format.
"""

import asyncio
import json
import logging

import httpx

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_SEC = 3


class SimulatedWSClient:
    """WebSocket client that connects to the Market Simulator instead of Fyers."""

    def __init__(self):
        self._ws = None
        self._connected = False
        self._symbols: list[str] = []
        self._reverse_map: dict[str, str] = {v: k for k, v in FYERS_SYMBOL_MAP.items()}
        self._receive_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(
        self,
        symbols: list[str] | None = None,
        extra_symbols: list[str] | None = None,
    ):
        """Connect to the simulator WebSocket and subscribe to symbols."""
        if self._ws:
            await self.stop()

        await self.fetch_quotes_rest()

        base = symbols or list(FYERS_SYMBOL_MAP.values())
        self._symbols = list(dict.fromkeys(base + (extra_symbols or [])))

        ws_url = settings.simulator_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/ticks"

        try:
            import websockets
            self._ws = await websockets.connect(ws_url)
            self._connected = True

            await self._ws.send(json.dumps({
                "action": "subscribe",
                "symbols": self._symbols,
                "data_type": "SymbolUpdate",
            }))

            self._receive_task = asyncio.create_task(
                self._receive_loop(), name="simulated_ws_receive",
            )

            logger.info(
                "Simulated WS connected to %s (%d symbols)",
                ws_url, len(self._symbols),
            )
        except Exception:
            logger.exception("Failed to connect to market simulator at %s", ws_url)

    async def stop(self):
        """Disconnect from the simulator."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        self._receive_task = None
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        logger.info("Simulated WS stopped")

    def register_symbol_map(self, symbol_map: dict[str, str]):
        """Register internal-to-Fyers symbol mappings for reverse lookup."""
        for internal, fyers in symbol_map.items():
            self._reverse_map[fyers] = internal

    def is_symbol_subscribed(self, fyers_symbol: str) -> bool:
        return fyers_symbol in self._symbols

    async def subscribe_symbols(
        self,
        symbols: list[str],
        symbol_map: dict[str, str] | None = None,
    ):
        """Subscribe to additional symbols on the existing connection."""
        if symbol_map:
            self.register_symbol_map(symbol_map)

        new_symbols = [s for s in symbols if s not in self._symbols]
        if not new_symbols:
            return

        self._symbols.extend(new_symbols)

        if self._ws and self._connected:
            try:
                await self._ws.send(json.dumps({
                    "action": "subscribe",
                    "symbols": new_symbols,
                    "data_type": "SymbolUpdate",
                }))
                logger.info("Subscribed to additional symbols: %s", new_symbols)
            except Exception:
                logger.exception("Failed to subscribe to additional symbols")

    async def fetch_quotes_rest(self, extra_symbols: dict[str, str] | None = None):
        """Fetch latest quotes from the simulator REST endpoint."""
        all_symbols = dict(FYERS_SYMBOL_MAP)
        if extra_symbols:
            all_symbols.update(extra_symbols)

        reverse_map = {v: k for k, v in all_symbols.items()}

        try:
            from app.data_feed.feed_manager import feed_manager

            fyers_symbols = list(all_symbols.values())
            fetched = 0

            async with httpx.AsyncClient(timeout=30.0) as client:
                for i in range(0, len(fyers_symbols), 50):
                    batch = fyers_symbols[i : i + 50]
                    resp = await client.get(
                        f"{settings.simulator_url}/data/quotes",
                        params={"symbols": ",".join(batch)},
                    )
                    result = resp.json()

                    if result.get("s") != "ok":
                        logger.error("Simulator quotes error: %s", result.get("message"))
                        continue

                    for item in result.get("d", []):
                        v = item.get("v", {})
                        fyers_symbol = item.get("n", "")
                        internal = reverse_map.get(fyers_symbol) or self._fyers_to_internal(fyers_symbol)
                        if not internal:
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

                        fyers_alias = fyers_symbol if fyers_symbol != internal else None
                        await feed_manager.process_tick(internal, tick_data, fyers_alias=fyers_alias)
                        fetched += 1

            logger.info("REST quotes fetched for %d symbols (simulated)", fetched)
        except Exception:
            logger.exception("Failed to fetch simulated REST quotes")

    async def _receive_loop(self):
        """Receive ticks from the simulator WS and feed into FeedManager."""
        import websockets

        from app.data_feed.feed_manager import feed_manager

        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                fyers_symbol = msg.get("symbol", "")
                internal = self._fyers_to_internal(fyers_symbol)
                if not internal:
                    continue

                tick_data = {
                    "ltp": msg.get("ltp", 0),
                    "bid": msg.get("bid", msg.get("ltp", 0)),
                    "ask": msg.get("ask", msg.get("ltp", 0)),
                    "volume": msg.get("vol_traded_today", 0),
                    "change": msg.get("ch", 0),
                    "change_pct": msg.get("chp", 0),
                    "high": msg.get("high_price", 0),
                    "low": msg.get("low_price", 0),
                    "open": msg.get("open_price", 0),
                    "prev_close": msg.get("prev_close_price", 0),
                }

                fyers_alias = fyers_symbol if fyers_symbol != internal else None
                await feed_manager.process_tick(internal, tick_data, fyers_alias=fyers_alias)

        except websockets.ConnectionClosed:
            logger.warning("Simulator WS disconnected — scheduling reconnect")
            self._connected = False
            asyncio.create_task(self._reconnect())
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error in simulator WS receive loop")
            self._connected = False
            asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """Reconnect to the simulator after a disconnect."""
        await asyncio.sleep(_RECONNECT_DELAY_SEC)
        logger.info("Reconnecting to market simulator...")
        symbols = list(self._symbols)
        await self.stop()
        await self.start(symbols=symbols)

    def _fyers_to_internal(self, fyers_symbol: str) -> str | None:
        """Convert Fyers symbol to internal short name."""
        internal = self._reverse_map.get(fyers_symbol)
        if internal:
            return internal
        if isinstance(fyers_symbol, str) and ":" in fyers_symbol:
            return fyers_symbol
        return None


simulated_ws_client = SimulatedWSClient()
