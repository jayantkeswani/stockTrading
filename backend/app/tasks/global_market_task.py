"""Global market data task — fetches world indices, commodities, and FX every 15 minutes.

Data flow:
    scheduler -> fetch_global_market_data()
        -> yfinance: Dow futures, S&P 500, Nasdaq, Nifty proxy, crude, USD/INR, DXY, US VIX
        -> write to Redis (TTL 20 min) for hot-path MarketContext reads
        -> insert GlobalMarketSnapshot row for history / backtest replay

Data is intentionally lightweight (8 tickers, daily/intraday) and cached in Redis so
strategy_runner never waits on this task. If yfinance is unreachable, the previous
Redis values are used until they expire.
"""

import asyncio
import logging
import time
from datetime import datetime
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.constants import IST
from app.indicators.global_market import GlobalCues, combined_global_score

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

FETCH_INTERVAL_MINUTES = 15
REDIS_TTL_SECONDS = 20 * 60  # 20 min — survives a missed tick

# yfinance tickers to fetch.
# DXY: "DX-Y.NYB" is the ICE US Dollar Index on the NYB exchange — more reliable
# than the futures contract "DX=F" which rolls and can disappear.
_TICKERS = {
    "dow_futures": "YM=F",
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "nifty": "^NSEI",
    "crude": "CL=F",
    "usdinr": "INR=X",
    "dxy": "DX-Y.NYB",
    "us_vix": "^VIX",
}


def _fetch_one_ticker_sync(ticker: str) -> tuple[float | None, float | None]:
    """Fetch the last two daily closes for a single ticker. Returns (latest, prev)."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            return None, None
        closes = hist["Close"].dropna()
        if len(closes) == 0:
            return None, None
        latest = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
        return latest, prev
    except Exception:
        return None, None


def _fetch_global_data_sync() -> dict[str, float | None]:
    """Fetch latest prices for all global tickers synchronously (yfinance).

    Fetches each ticker individually so one bad symbol never blocks the others.
    """
    data: dict[str, float | None] = {}
    prices: dict[str, float] = {}
    prev_prices: dict[str, float] = {}

    for i, (key, ticker) in enumerate(_TICKERS.items()):
        latest, prev = _fetch_one_ticker_sync(ticker)
        if latest is not None:
            prices[key] = latest
        if prev is not None:
            prev_prices[key] = prev
        if i < len(_TICKERS) - 1:
            time.sleep(1.5)

    data["dow_futures_price"] = prices.get("dow_futures")
    data["sp500_price"] = prices.get("sp500")
    data["nasdaq_price"] = prices.get("nasdaq")
    data["nifty_price"] = prices.get("nifty")
    data["crude_price"] = prices.get("crude")
    data["usdinr_price"] = prices.get("usdinr")
    data["dxy_price"] = prices.get("dxy")
    data["us_vix"] = prices.get("us_vix")

    def _pct_change(key: str) -> float | None:
        cur = prices.get(key)
        prev = prev_prices.get(key)
        if cur is None or prev is None or prev == 0:
            return None
        return (cur - prev) / prev * 100.0

    data["dow_futures_pct"] = _pct_change("dow_futures")
    data["sp500_close_pct"] = _pct_change("sp500")
    data["nasdaq_close_pct"] = _pct_change("nasdaq")
    data["nifty_pct"] = _pct_change("nifty")
    data["crude_pct"] = _pct_change("crude")
    data["usdinr_pct"] = _pct_change("usdinr")
    data["dxy_pct"] = _pct_change("dxy")

    return data


async def fetch_global_market_data() -> None:
    """Fetch global market data and write to Redis + DB."""
    try:
        raw = await asyncio.to_thread(_fetch_global_data_sync)
    except Exception:
        logger.exception("Failed to run global market data fetch")
        return

    cues = GlobalCues(
        dow_futures_pct=raw.get("dow_futures_pct"),
        sp500_close_pct=raw.get("sp500_close_pct"),
        nasdaq_close_pct=raw.get("nasdaq_close_pct"),
        nifty_pct=raw.get("nifty_pct"),
        crude_pct=raw.get("crude_pct"),
        usdinr_pct=raw.get("usdinr_pct"),
        dxy_pct=raw.get("dxy_pct"),
        us_vix=raw.get("us_vix"),
        dow_futures_price=raw.get("dow_futures_price"),
        sp500_price=raw.get("sp500_price"),
        nasdaq_price=raw.get("nasdaq_price"),
        nifty_price=raw.get("nifty_price"),
        crude_price=raw.get("crude_price"),
        usdinr_price=raw.get("usdinr_price"),
        dxy_price=raw.get("dxy_price"),
    )
    cues.global_score = combined_global_score(cues)

    await _write_to_redis(cues)
    await _persist_snapshot(cues)
    logger.info(
        "Global market data updated: Dow %.2f%%, SP500 %.2f%%, VIX %.1f, score %.2f",
        cues.dow_futures_pct or 0,
        cues.sp500_close_pct or 0,
        cues.us_vix or 0,
        cues.global_score or 0,
    )


async def _write_to_redis(cues: GlobalCues) -> None:
    from app.core.redis import get_redis

    r = get_redis()
    fields = {
        "dow_futures_pct": cues.dow_futures_pct,
        "sp500_close_pct": cues.sp500_close_pct,
        "nasdaq_close_pct": cues.nasdaq_close_pct,
        "nifty_pct": cues.nifty_pct,
        "crude_pct": cues.crude_pct,
        "usdinr_pct": cues.usdinr_pct,
        "dxy_pct": cues.dxy_pct,
        "us_vix": cues.us_vix,
        "pre_open_gap_pct": cues.pre_open_gap_pct,
        "global_score": cues.global_score,
        "dow_futures_price": cues.dow_futures_price,
        "sp500_price": cues.sp500_price,
        "nasdaq_price": cues.nasdaq_price,
        "nifty_price": cues.nifty_price,
        "crude_price": cues.crude_price,
        "usdinr_price": cues.usdinr_price,
        "dxy_price": cues.dxy_price,
    }
    pipe = r.pipeline()
    for key, val in fields.items():
        if val is not None:
            pipe.set(f"indicator:global:{key}", str(val), ex=REDIS_TTL_SECONDS)
    await pipe.execute()


async def _persist_snapshot(cues: GlobalCues) -> None:
    from datetime import datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.constants import IST
    from app.core.database import async_session_factory
    from app.models.global_market_snapshot import GlobalMarketSnapshot

    now = datetime.now(IST)

    def _dec(v: float | None):
        return Decimal(str(v)) if v is not None else None

    values = {
        "timestamp": now,
        "dow_futures_pct": _dec(cues.dow_futures_pct),
        "sp500_close_pct": _dec(cues.sp500_close_pct),
        "nasdaq_close_pct": _dec(cues.nasdaq_close_pct),
        "nifty_pct": _dec(cues.nifty_pct),
        "crude_pct": _dec(cues.crude_pct),
        "usdinr_pct": _dec(cues.usdinr_pct),
        "dxy_pct": _dec(cues.dxy_pct),
        "us_vix": _dec(cues.us_vix),
        "pre_open_gap_pct": _dec(cues.pre_open_gap_pct),
        "dow_futures_price": _dec(cues.dow_futures_price),
        "sp500_price": _dec(cues.sp500_price),
        "nasdaq_price": _dec(cues.nasdaq_price),
        "nifty_price": _dec(cues.nifty_price),
        "crude_price": _dec(cues.crude_price),
        "usdinr_price": _dec(cues.usdinr_price),
        "dxy_price": _dec(cues.dxy_price),
        "global_score": _dec(cues.global_score),
    }

    try:
        async with async_session_factory() as session:
            stmt = (
                pg_insert(GlobalMarketSnapshot)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_global_market_snapshot_ts")
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.exception("Failed to persist GlobalMarketSnapshot")


async def _get_global_cues_from_redis() -> GlobalCues | None:
    """Read latest global cues from Redis. Returns None if no data cached."""
    from app.core.redis import get_redis

    r = get_redis()
    keys = [
        "dow_futures_pct", "sp500_close_pct", "nasdaq_close_pct", "nifty_pct",
        "crude_pct", "usdinr_pct", "dxy_pct", "us_vix", "pre_open_gap_pct",
        "global_score", "dow_futures_price", "sp500_price", "nasdaq_price",
        "nifty_price", "crude_price", "usdinr_price", "dxy_price",
    ]
    values = await r.mget([f"indicator:global:{k}" for k in keys])
    data = {k: (float(v) if v is not None else None) for k, v in zip(keys, values)}

    if all(v is None for v in data.values()):
        return None

    return GlobalCues(**data)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

async def start_global_market_scheduler() -> None:
    global _scheduler

    from app.core.task_registry import TaskStatus, TaskType, task_registry

    if _scheduler and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        fetch_global_market_data,
        IntervalTrigger(minutes=FETCH_INTERVAL_MINUTES),
        id="global_market_fetch",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.start()

    task_registry.register(
        name="global_market_task",
        task_type=TaskType.SCHEDULER,
        status=TaskStatus.RUNNING,
        metadata={"interval_minutes": FETCH_INTERVAL_MINUTES},
    )

    logger.info("Global market scheduler started (every %d min)", FETCH_INTERVAL_MINUTES)


async def stop_global_market_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
