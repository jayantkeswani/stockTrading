from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.data_feed.fyers_ws_client import fyers_ws_client
from app.data_feed.symbol_master import symbol_master
from app.tasks.fyers_login_task import start_fyers_login_scheduler, stop_fyers_login_scheduler
from app.tasks.oi_snapshot_task import start_oi_snapshot_scheduler, stop_oi_snapshot_scheduler
from app.tasks.symbol_master_task import start_symbol_master_scheduler, stop_symbol_master_scheduler
from app.websocket.manager import ws_manager


async def _start_data_feed_if_authenticated():
    """Start the Fyers live data feed if we have a valid token.

    Also backfills previous trading day's candles so strategies have
    previous-day context (PDH/PDL/PDC) available from the first candle.
    """
    from app.core.redis import get_redis
    from app.services.candle_backfill import (
        backfill_previous_day, backfill_today, _get_all_backfill_symbols,
    )

    r = get_redis()
    token = await r.get("fyers:access_token")
    if token:
        print("Fyers token found in Redis, backfilling candles...")
        try:
            await backfill_previous_day()
        except Exception as e:
            print(f"Previous day backfill failed: {e}")

        try:
            await backfill_today()
        except Exception as e:
            print(f"Today's backfill failed: {e}")

        # Collect all extra symbols to subscribe and fetch prices for
        watchlist_symbols = await _get_watchlist_symbols()
        strategy_symbols = await _get_all_backfill_symbols()

        # Extra symbols for WS subscription: watchlist + strategy Fyers symbols
        extra_ws_symbols = list(set(
            watchlist_symbols + list(strategy_symbols.values())
        ))

        print("Starting live data feed...")
        await fyers_ws_client.start(extra_symbols=extra_ws_symbols)

        # Fetch REST quotes for strategy symbols so Redis has prices on startup
        # (fyers_ws_client.start already fetches FYERS_SYMBOL_MAP via fetch_quotes_rest)
        print("Fetching prices for strategy-configured symbols...")
        try:
            await fyers_ws_client.fetch_quotes_rest(extra_symbols=strategy_symbols)
        except Exception as e:
            print(f"Strategy symbol price fetch failed: {e}")
    else:
        print("No Fyers token in Redis. Data feed will not start until auth completes.")


async def _get_watchlist_symbols() -> list[str]:
    """Load watchlist Fyers symbols from Redis for WS subscription."""
    import json
    from app.core.redis import get_redis

    try:
        r = get_redis()
        items = await r.hgetall("watchlist:items")
        # Keys in the watchlist hash are Fyers symbols (e.g. "NSE:TCS-EQ")
        return list(items.keys()) if items else []
    except Exception:
        return []


async def _load_symbol_master_background():
    """Load symbol master in background so it doesn't block startup."""
    import asyncio

    # Small delay to let the server finish starting
    await asyncio.sleep(1)
    try:
        await symbol_master.load()
        print(f"Symbol master loaded: {symbol_master.count} symbols")
    except Exception as e:
        print(f"Symbol master load failed (will retry on first search): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # Startup
    print("Starting StockTrading backend...")
    await start_fyers_login_scheduler()
    # Load symbol master in background — don't block startup
    asyncio.create_task(_load_symbol_master_background(), name="symbol_master_load")
    await start_symbol_master_scheduler()
    await start_oi_snapshot_scheduler()
    await _start_data_feed_if_authenticated()
    yield
    # Shutdown
    await fyers_ws_client.stop()
    await stop_fyers_login_scheduler()
    await stop_symbol_master_scheduler()
    await stop_oi_snapshot_scheduler()
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
