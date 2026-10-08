import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.v1 import agent, auth, intraday_futures, intraday_hunter, intraday_hunter_v2, market_data, options, positions, research, risk, settings, signals, strategies, tasks, trades, watchlist, yolo_profiles
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
api_router.include_router(research.router, prefix="/api/v1/research", tags=["research"])
api_router.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])
api_router.include_router(intraday_futures.router, prefix="/api/v1/intraday-futures", tags=["intraday-futures"])
api_router.include_router(intraday_hunter.router, prefix="/api/v1/intraday-hunter", tags=["intraday-hunter"])
api_router.include_router(intraday_hunter_v2.router, prefix="/api/v1/intraday-hunter", tags=["intraday-hunter-v2"])
api_router.include_router(options.router, prefix="/api/v1/options", tags=["options"])
api_router.include_router(yolo_profiles.router, prefix="/api/v1/yolo-profiles", tags=["yolo-profiles"])


@api_router.get("/api/v1/health")
async def health_check():
    from app.config import settings
    from app.main import startup_state
    return {
        "status": "ok",
        "service": "stocktrading-backend",
        "version": settings.app_version,
        "deployed_at": settings.deployed_at,
        "data_feed_ready": startup_state.data_feed_ready,
        "startup_error": startup_state.startup_error,
    }


@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await ws_manager.handle_message(websocket, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
