from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.data_feed.fyers_ws_client import fyers_ws_client
from app.tasks.fyers_login_task import start_fyers_login_scheduler, stop_fyers_login_scheduler
from app.websocket.manager import ws_manager


async def _start_data_feed_if_authenticated():
    """Start the Fyers live data feed if we have a valid token."""
    from app.core.redis import get_redis

    r = get_redis()
    token = await r.get("fyers:access_token")
    if token:
        print("Fyers token found in Redis, starting live data feed...")
        await fyers_ws_client.start()
    else:
        print("No Fyers token in Redis. Data feed will not start until auth completes.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting StockTrading backend...")
    await start_fyers_login_scheduler()
    await _start_data_feed_if_authenticated()
    yield
    # Shutdown
    await fyers_ws_client.stop()
    await stop_fyers_login_scheduler()
    await ws_manager.disconnect_all()
    print("StockTrading backend stopped.")


app = FastAPI(
    title="StockTrading API",
    description="Indian stock market options trading system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
