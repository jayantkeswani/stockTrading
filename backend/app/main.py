from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import settings
from app.core.task_registry import TaskStatus, TaskType, task_registry
from app.data_feed.fyers_ws_client import fyers_ws_client
from app.data_feed.symbol_master import symbol_master
from app.tasks.fyers_login_task import start_fyers_login_scheduler, stop_fyers_login_scheduler
from app.tasks.fundamental_data_task import (
    fetch_fundamentals,
    start_fundamental_data_scheduler,
    stop_fundamental_data_scheduler,
)
from app.tasks.oi_snapshot_task import start_oi_snapshot_scheduler, stop_oi_snapshot_scheduler
from app.tasks.daily_summary_task import start_daily_summary_scheduler, stop_daily_summary_scheduler
from app.tasks.symbol_master_task import start_symbol_master_scheduler, stop_symbol_master_scheduler
from app.tasks.global_market_task import start_global_market_scheduler, stop_global_market_scheduler
from app.tasks.morning_workflow_task import start_morning_workflow_scheduler, stop_morning_workflow_scheduler
from app.tasks.nse_bhav_copy_task import start_nse_bhav_copy_scheduler, stop_nse_bhav_copy_scheduler
from app.tasks.fo_ban_list_task import start_fo_ban_list_scheduler, stop_fo_ban_list_scheduler
from app.services import trading_config as _trading_config_svc
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
        s5_symbols = await _get_strat5_watchlist_symbols()
        position_symbols = await _get_open_position_symbols()

        # Extra symbols for WS subscription: dashboard watchlist + strategy configs
        # + S5 screener watchlist + open position contracts
        extra_ws_symbols = list(set(
            watchlist_symbols + list(strategy_symbols.values())
            + list(s5_symbols.values()) + position_symbols
        ))

        # Register symbol mappings so ticks are converted to short names
        fyers_ws_client.register_symbol_map(strategy_symbols)
        fyers_ws_client.register_symbol_map(s5_symbols)

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


async def _get_strat5_watchlist_symbols() -> dict[str, str]:
    """Load Strategy 5 screener watchlist from Redis for WS subscription.

    Returns {short_name: fyers_symbol} so register_symbol_map can be called.
    """
    import json
    from app.core.redis import get_redis
    from app.core.utils import now_ist

    try:
        r = get_redis()
        today = str(now_ist().date())
        raw = await r.get(f"strat5:watchlist:{today}")
        if not raw:
            return {}
        watchlist = json.loads(raw)
        return {item["symbol"]: f"NSE:{item['symbol']}-EQ" for item in watchlist if item.get("symbol")}
    except Exception:
        return {}


async def _get_open_position_symbols() -> list[str]:
    """Load Fyers symbols for all open positions so trade_monitor gets live ticks."""
    from sqlalchemy import select
    from app.core.database import async_session_factory
    from app.models.position import Position

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Position.fyers_option_symbol).where(
                    Position.fyers_option_symbol.isnot(None),
                )
            )
            return [row[0] for row in result.all() if row[0]]
    except Exception:
        return []


async def _fetch_fundamentals_background():
    """Fetch CAN SLIM fundamental data on startup (background, non-blocking)."""
    import asyncio

    # Wait for symbol master to load first (needed for RS calculation)
    await asyncio.sleep(5)
    try:
        count = await fetch_fundamentals()
        print(f"Fundamental data: updated {count} symbols")
    except Exception as e:
        print(f"Fundamental data fetch failed: {e}")


async def _deep_backfill_background():
    """Backfill extended candle history for CAN SLIM pattern detection (background).

    Only fetches for symbols with < 50 days of data. Once populated, the daily
    backfill keeps it current. Runs after normal backfill completes.
    """
    import asyncio

    await asyncio.sleep(10)  # Wait for normal backfill to finish
    try:
        from app.services.candle_backfill import backfill_deep_history
        await backfill_deep_history(days=120)
    except Exception as e:
        print(f"Deep backfill failed: {e}")


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

    # --- Trading config (seed + live-reload listener) ---
    await _trading_config_svc.ensure_seeded()
    t_cfg = asyncio.create_task(_trading_config_svc.start_config_listener(), name="trading_config_listener")
    task_registry.track_asyncio_task("trading_config_listener", t_cfg, metadata={"description": "Trading config pubsub reload"})

    # --- Schedulers (periodic jobs) ---
    await start_fyers_login_scheduler()
    task_registry.register("fyers_login_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 08:55 IST"})

    await start_symbol_master_scheduler()
    task_registry.register("symbol_master_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 08:00 IST"})

    await start_oi_snapshot_scheduler()
    task_registry.register("oi_snapshot_scheduler", TaskType.SCHEDULER, metadata={"schedule": "every 3m (market hours)"})

    await start_fundamental_data_scheduler()
    task_registry.register("fundamental_data_scheduler", TaskType.SCHEDULER, metadata={"schedule": "every 6h"})

    await start_daily_summary_scheduler()
    task_registry.register("daily_summary_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 15:35 IST"})

    await start_global_market_scheduler()
    # task_registry registration is handled inside start_global_market_scheduler

    await start_morning_workflow_scheduler()
    task_registry.register("morning_workflow_scheduler", TaskType.SCHEDULER, metadata={"schedule": "briefing 08:00, screener 08:30 IST"})

    await start_nse_bhav_copy_scheduler()
    task_registry.register("nse_bhav_copy_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 07:30 IST"})

    await start_fo_ban_list_scheduler()
    task_registry.register("fo_ban_list_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 07:00 IST"})

    # --- One-shot startup tasks (tracked via done callback) ---
    t1 = asyncio.create_task(_load_symbol_master_background(), name="symbol_master_load")
    task_registry.track_asyncio_task("symbol_master_load", t1, metadata={"description": "Load symbol master into memory"})

    t2 = asyncio.create_task(_fetch_fundamentals_background(), name="fundamental_data_startup")
    task_registry.track_asyncio_task("fundamental_data_startup", t2, metadata={"description": "Fetch CAN SLIM fundamentals"})

    # --- Data feed (service) ---
    await _start_data_feed_if_authenticated()
    task_registry.register("fyers_data_feed", TaskType.SERVICE, metadata={"description": "Fyers WebSocket live data feed"})

    # --- Deep backfill (startup task) ---
    t3 = asyncio.create_task(_deep_backfill_background(), name="deep_backfill")
    task_registry.track_asyncio_task("deep_backfill", t3, metadata={"description": "Backfill 120d candle history for CAN SLIM"})

    yield

    # Shutdown — mark services/schedulers as stopped
    await fyers_ws_client.stop()
    task_registry.update_status("fyers_data_feed", TaskStatus.STOPPED)

    await stop_fyers_login_scheduler()
    task_registry.update_status("fyers_login_scheduler", TaskStatus.STOPPED)

    await stop_symbol_master_scheduler()
    task_registry.update_status("symbol_master_scheduler", TaskStatus.STOPPED)

    await stop_oi_snapshot_scheduler()
    task_registry.update_status("oi_snapshot_scheduler", TaskStatus.STOPPED)

    await stop_fundamental_data_scheduler()
    task_registry.update_status("fundamental_data_scheduler", TaskStatus.STOPPED)

    await stop_daily_summary_scheduler()
    task_registry.update_status("daily_summary_scheduler", TaskStatus.STOPPED)

    await stop_global_market_scheduler()
    task_registry.update_status("global_market_task", TaskStatus.STOPPED)

    await stop_morning_workflow_scheduler()
    task_registry.update_status("morning_workflow_scheduler", TaskStatus.STOPPED)

    await stop_nse_bhav_copy_scheduler()
    task_registry.update_status("nse_bhav_copy_scheduler", TaskStatus.STOPPED)

    await stop_fo_ban_list_scheduler()
    task_registry.update_status("fo_ban_list_scheduler", TaskStatus.STOPPED)

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
