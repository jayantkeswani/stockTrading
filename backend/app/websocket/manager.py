"""WebSocket connection manager with Redis pub/sub backend."""

import json
from datetime import datetime

from fastapi import WebSocket

from app.core.constants import IST


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.subscriptions[websocket] = set()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.subscriptions.pop(websocket, None)

    async def disconnect_all(self):
        for ws in self.active_connections[:]:
            try:
                await ws.close()
            except Exception:
                pass
        self.active_connections.clear()
        self.subscriptions.clear()

    async def handle_message(self, websocket: WebSocket, data: dict):
        event = data.get("event")
        if event == "subscribe:symbol":
            symbols = data.get("data", {}).get("symbols", [])
            self.subscriptions.setdefault(websocket, set()).update(symbols)
        elif event == "unsubscribe:symbol":
            symbols = data.get("data", {}).get("symbols", [])
            self.subscriptions.get(websocket, set()).difference_update(symbols)
        elif event == "ping":
            await websocket.send_json({
                "event": "pong",
                "data": {},
                "timestamp": datetime.now(IST).isoformat(),
            })

    async def broadcast(self, event: str, data: dict):
        """Broadcast an event to all connected clients."""
        message = {
            "event": event,
            "data": data,
            "timestamp": datetime.now(IST).isoformat(),
        }
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_price(self, symbol: str, data: dict):
        """Broadcast price update only to subscribers of this symbol."""
        message = {
            "event": "price:update",
            "data": data,
            "timestamp": datetime.now(IST).isoformat(),
        }
        disconnected = []
        for ws in self.active_connections:
            subs = self.subscriptions.get(ws, set())
            if symbol in subs or not subs:  # Send to all if no specific subscriptions
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


ws_manager = ConnectionManager()
