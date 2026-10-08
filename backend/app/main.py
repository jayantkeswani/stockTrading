import dataclasses
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

# File logging — captures all module loggers via root logger propagation
_log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "app.log")

_file_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class StartupState:
    """Tracks readiness of heavy background startup tasks (symbol master, data feed, agent)."""
    data_feed_ready: bool = False
    startup_error: str | None = None


startup_state = StartupState()

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
from app.tasks.sector_update_task import start_sector_update_scheduler, stop_sector_update_scheduler
from app.tasks.signal_expiry_task import start_signal_expiry_scheduler, stop_signal_expiry_scheduler
from app.tasks.intraday_hunter_v2_task import (
    JOB_SCHEDULE as IH_V2_JOB_SCHEDULE,
    start_intraday_hunter_v2_scheduler,
    stop_intraday_hunter_v2_scheduler,
)
from app.tasks.intraday_hunter_task import (
    start_intraday_hunter_scheduler,
    stop_intraday_hunter_scheduler,
)
from app.agent.agent_runner import agent_runner
from app.agent.telegram_bot import start_telegram_bot, stop_telegram_bot
from app.services import trading_config as _trading_config_svc
from app.services import yolo_profile_service as _yolo_profile_svc
from app.websocket.manager import ws_manager


async def _start_data_feed_if_authenticated():
    """Start the data feed if we have a valid token (live) or unconditionally (simulated).

    Also backfills previous trading day's candles so strategies have
    previous-day context (PDH/PDL/PDC) available from the first candle.
    Skips if data feed is already connected (e.g. fyers_login_task won the race).
    """
    if fyers_ws_client.is_connected:
        logger.info("Data feed already connected — skipping startup start")
        return
    from app.core.redis import get_redis
    from app.services.candle_backfill import (
        backfill_previous_day, backfill_today, _get_all_backfill_symbols,
    )

    is_simulated = settings.market_mode == "simulated"

    if not is_simulated:
        r = get_redis()
        token = await r.get("fyers:access_token")
        if not token:
            print("No Fyers token in Redis. Data feed will not start until auth completes.")
            return

    if not is_simulated:
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
    position_symbols = await _get_position_symbols()

    # Extra symbols for WS subscription: dashboard watchlist + strategy configs
    # + S5 screener watchlist + open position contracts
    extra_ws_symbols = list(set(
        watchlist_symbols + list(strategy_symbols.values())
        + list(s5_symbols.values()) + position_symbols
    ))

    # Register symbol mappings so ticks are converted to short names
    fyers_ws_client.register_symbol_map(strategy_symbols)
    fyers_ws_client.register_symbol_map(s5_symbols)

    mode_label = "simulated" if is_simulated else "live"
    print(f"Starting {mode_label} data feed...")
    await fyers_ws_client.start(extra_symbols=extra_ws_symbols)

    # Fetch REST quotes for strategy symbols so Redis has prices on startup
    print("Fetching prices for strategy-configured symbols...")
    try:
        await fyers_ws_client.fetch_quotes_rest(extra_symbols=strategy_symbols)
    except Exception as e:
        print(f"Strategy symbol price fetch failed: {e}")


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


async def _get_position_symbols() -> list[str]:
    """Load Fyers symbols for open positions + today's closed trades.

    Open positions need live ticks for trade_monitor. Today's closed trade
    symbols need continued data collection so hold analysis can query
    post-exit 1m candles through 15:30 IST.
    """
    from sqlalchemy import select
    from sqlalchemy.sql import union_all
    from app.core.database import async_session_factory
    from app.core.enums import TradeStatus
    from app.core.utils import now_ist
    from app.models.position import Position
    from app.models.trade import Trade

    try:
        today_start = now_ist().replace(hour=0, minute=0, second=0, microsecond=0)
        open_q = select(Position.fyers_option_symbol).where(
            Position.fyers_option_symbol.isnot(None),
        )
        closed_q = select(Trade.fyers_option_symbol).where(
            Trade.fyers_option_symbol.isnot(None),
            Trade.status == TradeStatus.CLOSED.value,
            Trade.exit_time >= today_start,
        )
        combined = union_all(open_q, closed_q)
        async with async_session_factory() as session:
            result = await session.execute(combined)
            return list({row[0] for row in result.all() if row[0]})
    except Exception:
        return []


async def _fetch_global_market_background():
    """Fetch global market data on startup (background, non-blocking)."""
    try:
        from app.tasks.global_market_task import fetch_global_market_data
        await fetch_global_market_data()
        logger.info("Global market data: initial fetch complete")
    except Exception as e:
        logger.error("Global market startup fetch failed: %s", e, exc_info=True)


async def _fetch_fundamentals_background():
    """Fetch CAN SLIM fundamental data on startup (background, non-blocking)."""
    import asyncio

    # Wait for symbol master to load first (needed for RS calculation)
    await asyncio.sleep(5)
    try:
        count = await fetch_fundamentals()
        logger.info("Fundamental data: updated %d symbols", count)
        if count == 0:
            logger.warning("Fundamental data: 0 symbols updated — check Yahoo Finance connectivity")
    except Exception as e:
        logger.error("Fundamental data fetch failed: %s", e, exc_info=True)


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


async def _background_startup():
    """Run heavy startup tasks after the server starts accepting requests."""
    import asyncio

    try:
        await symbol_master.refresh()
        print(f"Symbol master loaded: {symbol_master.count} symbols")
    except Exception as e:
        print(f"Symbol master refresh failed (will retry on first search): {e}")
        startup_state.startup_error = f"symbol_master: {e}"

    try:
        from app.data.sectors import load_db_sectors
        await load_db_sectors()
    except Exception as e:
        logger.error("load_db_sectors failed: %s", e)
        startup_state.startup_error = f"load_db_sectors: {e}"

    try:
        await _start_data_feed_if_authenticated()
        task_registry.register("fyers_data_feed", TaskType.SERVICE, metadata={"description": "Fyers WebSocket live data feed"})
    except Exception as e:
        logger.error("Data feed startup failed: %s", e, exc_info=True)
        startup_state.startup_error = f"data_feed: {e}"
        return

    try:
        await agent_runner.start()
        task_registry.register("agent_runner", TaskType.SERVICE, metadata={"description": "Trade monitor — SL/target/EOD exits"})
    except Exception as e:
        logger.error("agent_runner startup failed: %s", e, exc_info=True)
        startup_state.startup_error = f"agent_runner: {e}"
        return

    t2 = asyncio.create_task(_fetch_fundamentals_background(), name="fundamental_data_startup")
    task_registry.track_asyncio_task("fundamental_data_startup", t2, metadata={"description": "Fetch CAN SLIM fundamentals"})

    t3 = asyncio.create_task(_fetch_global_market_background(), name="global_market_startup")
    task_registry.track_asyncio_task("global_market_startup", t3, metadata={"description": "Initial global market data fetch"})

    t4 = asyncio.create_task(_deep_backfill_background(), name="deep_backfill")
    task_registry.track_asyncio_task("deep_backfill", t4, metadata={"description": "Backfill 120d candle history for CAN SLIM"})

    startup_state.data_feed_ready = True
    logger.info("Background startup complete — data feed ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # Startup
    print("Starting StockTrading backend...")

    # --- Trading config (seed + live-reload listener) ---
    await _trading_config_svc.ensure_seeded()
    t_cfg = asyncio.create_task(_trading_config_svc.start_config_listener(), name="trading_config_listener")
    task_registry.track_asyncio_task("trading_config_listener", t_cfg, metadata={"description": "Trading config pubsub reload"})

    t_profiles = asyncio.create_task(_yolo_profile_svc.start_profile_listener(), name="yolo_profile_listener")
    task_registry.track_asyncio_task("yolo_profile_listener", t_profiles, metadata={"description": "YOLO profiles pubsub reload"})

    # --- Schedulers (periodic jobs) ---
    await start_fyers_login_scheduler()
    task_registry.register("fyers_login_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 07:45 IST"})

    await start_symbol_master_scheduler()
    task_registry.register("symbol_master_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 08:00 IST"})

    await start_oi_snapshot_scheduler()
    task_registry.register("oi_snapshot_scheduler", TaskType.SCHEDULER, metadata={"schedule": "every 3m (market hours)"})

    await start_fundamental_data_scheduler()
    task_registry.register("fundamental_data_scheduler", TaskType.SCHEDULER, metadata={"schedule": "06:00, 12:00, 18:00 IST"})

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

    await start_signal_expiry_scheduler()
    task_registry.register("signal_expiry_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 15:30 IST"})

    await start_sector_update_scheduler()
    task_registry.register("sector_update_scheduler", TaskType.SCHEDULER, metadata={"schedule": "daily 07:00 IST"})

    await start_intraday_hunter_scheduler()
    task_registry.register("intraday_hunter_scheduler", TaskType.SCHEDULER, metadata={"schedule": "Call 1 thesis 08:45 IST"})

    await start_intraday_hunter_v2_scheduler()
    task_registry.register("intraday_hunter_v2_scheduler", TaskType.SCHEDULER, metadata={"schedule": IH_V2_JOB_SCHEDULE})

    # --- Telegram bot (inbound command polling — lightweight, stays before yield) ---
    t_tg = start_telegram_bot()
    if t_tg:
        task_registry.track_asyncio_task("telegram_bot_poll", t_tg, metadata={"description": "Telegram bot long-polling (/shadow)"})

    # --- Heavy startup tasks run in background after server is ready ---
    t_bg = asyncio.create_task(_background_startup(), name="background_startup")
    task_registry.track_asyncio_task("background_startup", t_bg, metadata={"description": "symbol_master → sectors → data_feed → agent"})

    yield

    # Shutdown — mark services/schedulers as stopped
    await stop_telegram_bot()
    await agent_runner.stop()
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

    await stop_signal_expiry_scheduler()
    task_registry.update_status("signal_expiry_scheduler", TaskStatus.STOPPED)

    await stop_sector_update_scheduler()
    task_registry.update_status("sector_update_scheduler", TaskStatus.STOPPED)

    await stop_intraday_hunter_scheduler()
    task_registry.update_status("intraday_hunter_scheduler", TaskStatus.STOPPED)

    await stop_intraday_hunter_v2_scheduler()
    task_registry.update_status("intraday_hunter_v2_scheduler", TaskStatus.STOPPED)

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
