from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.v1 import agent, auth, market_data, positions, risk, signals, strategies, tasks, trades, watchlist
from app.websocket.manager import ws_manager

api_router = APIRouter()

# REST API v1
api_router.include_router(trades.router, prefix="/api/v1/trades", tags=["trades"])
api_router.include_router(positions.router, prefix="/api/v1/positions", tags=["positions"])
api_router.include_router(signals.router, prefix="/api/v1/signals", tags=["signals"])
api_router.include_router(strategies.router, prefix="/api/v1/strategies", tags=["strategies"])
api_router.include_router(market_data.router, prefix="/api/v1/market", tags=["market"])
api_router.include_router(risk.router, prefix="/api/v1/risk", tags=["risk"])
api_router.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])
api_router.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
api_router.include_router(watchlist.router, prefix="/api/v1/watchlist", tags=["watchlist"])
api_router.include_router(tasks.router, prefix="/api/v1/tasks", tags=["tasks"])


@api_router.get("/api/v1/health")
async def health_check():
    return {"status": "ok", "service": "stocktrading-backend"}


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await ws_manager.handle_message(websocket, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
