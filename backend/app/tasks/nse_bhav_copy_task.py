"""NSE CM bhav copy task — downloads and parses delivery percentage data.

Runs daily at 7:30 AM IST. Downloads the previous trading day's bhav copy
from NSE archives, extracts per-stock delivery percentage, close, and
prev_close. Stores in Redis for consumption by the morning screener and
strategy evaluation pipeline.

Bhav copy URL pattern (new format, as of 2026):
    https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv
Requires cookie session (preflight GET to nseindia.com).

Gap-fill on startup: checks last 7 trading days in Redis, fetches any missing.
"""

import asyncio
import csv
import io
import json
import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST, NSE_HOLIDAYS
from app.core.utils import is_trading_day

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# NSE requires browser-like headers to avoid 403
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_BHAV_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/products/content"
    "/sec_bhavdata_full_{ddmmyyyy}.csv"
)
_NSE_HOME = "https://www.nseindia.com"

# Redis key pattern and TTL
_REDIS_KEY_PREFIX = "nse:bhav_copy"
_REDIS_TTL_DAYS = 90


def _previous_trading_day(ref_date: date) -> date:
    """Return the most recent NSE trading day before ref_date."""
    d = ref_date - timedelta(days=1)
    while d.weekday() >= 5 or d in NSE_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def _build_bhav_url(trade_date: date) -> str:
    """Build the NSE bhav copy download URL for a given date."""
    return _BHAV_URL_TEMPLATE.format(ddmmyyyy=trade_date.strftime("%d%m%Y"))


def _parse_bhav_csv(csv_text: str) -> dict[str, dict]:
    """Parse NSE CM bhav copy CSV and extract per-stock data for EQ series only.

    New-format columns (2026+): SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE,
    HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY,
    TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER

    Returns {symbol: {open, high, low, close, volume, delivery_pct, prev_close}}
    """
    result: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(csv_text))

    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items()}

        if row.get("SERIES") != "EQ":
            continue

        symbol = row.get("SYMBOL", "").strip()
        if not symbol:
            continue

        try:
            open_price = float(row.get("OPEN_PRICE", 0) or 0)
            high_price = float(row.get("HIGH_PRICE", 0) or 0)
            low_price = float(row.get("LOW_PRICE", 0) or 0)
            close = float(row.get("CLOSE_PRICE", 0) or 0)
            prev_close = float(row.get("PREV_CLOSE", 0) or 0)
            volume = int(float(row.get("TTL_TRD_QNTY", 0) or 0))
            delivery_pct = float(row.get("DELIV_PER", 0) or 0)
        except (ValueError, TypeError):
            logger.debug("Skipping %s — invalid numeric data", symbol)
            continue

        if close <= 0:
            continue

        result[symbol] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close,
            "volume": volume,
            "delivery_pct": delivery_pct,
            "prev_close": prev_close,
        }

    return result


def _download_and_parse(url: str) -> dict[str, dict] | None:
    """Download bhav copy CSV (with cookie session) and parse. Runs in thread.

    NSE requires a preflight GET to nseindia.com to establish cookies,
    otherwise it returns an HTML error page instead of CSV data.

    Returns parsed dict or None on failure.
    """
    import httpx

    try:
        with httpx.Client(
            headers=_NSE_HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        ) as client:
            # Preflight to establish NSE cookies
            client.get(_NSE_HOME)

            response = client.get(url)

            if response.status_code != 200:
                logger.warning(
                    "NSE bhav copy returned HTTP %d for %s",
                    response.status_code, url,
                )
                return None

            content_type = response.headers.get("content-type", "")
            if "html" in content_type.lower():
                logger.warning("NSE bhav copy returned HTML (blocked?) for %s", url)
                return None

            return _parse_bhav_csv(response.text)

    except httpx.HTTPError:
        logger.exception("HTTP error downloading bhav copy from %s", url)
        return None


async def _persist_daily_to_db(data: dict[str, dict], trade_date: date) -> int:
    """Upsert OHLCV rows from a parsed bhav copy dict into market_data_daily.

    Inserts fresh data straight from the downloaded CSV — never reads the Redis
    bhav copy cache (which only stores 3 fields for delivery % scoring).
    Returns the number of rows upserted.
    """
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.database import async_session_factory
    from app.models.market_data_daily import MarketDataDaily

    rows = [
        {
            "symbol": sym,
            "date": trade_date,
            "open": Decimal(str(v["open"])),
            "high": Decimal(str(v["high"])),
            "low": Decimal(str(v["low"])),
            "close": Decimal(str(v["close"])),
            "volume": int(v["volume"]),
            "delivery_pct": Decimal(str(v["delivery_pct"])) if v.get("delivery_pct") is not None else None,
        }
        for sym, v in data.items()
        if v.get("close", 0) > 0
    ]

    if not rows:
        return 0

    async with async_session_factory() as session:
        stmt = pg_insert(MarketDataDaily).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_market_data_daily_symbol_date",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "delivery_pct": stmt.excluded.delivery_pct,
            },
        )
        await session.execute(stmt)
        await session.commit()

    logger.info("market_data_daily: upserted %d rows for %s", len(rows), trade_date)
    return len(rows)


async def _download_bhav_copy(trade_date: date) -> dict[str, dict] | None:
    """Download and parse NSE CM bhav copy for the given date.

    Runs the sync download + ZIP extraction + CSV parsing in a thread
    to avoid blocking the event loop.
    """
    url = _build_bhav_url(trade_date)
    logger.info("Downloading NSE bhav copy for %s from %s", trade_date, url)
    return await asyncio.to_thread(_download_and_parse, url)


async def fetch_bhav_copy(trade_date: date | None = None) -> dict[str, dict] | None:
    """Orchestrator: download bhav copy and store in Redis.

    If no date provided, uses the previous trading day.
    Retries up to 3 times with exponential backoff.

    Returns the parsed data dict on success, None on failure.
    """
    from app.core.utils import now_ist

    if trade_date is None:
        trade_date = _previous_trading_day(now_ist().date())

    # Retry with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        data = await _download_bhav_copy(trade_date)
        if data is not None:
            break

        if attempt < max_retries - 1:
            wait = 2 ** (attempt + 1)  # 2, 4 seconds
            logger.warning(
                "Bhav copy download attempt %d/%d failed, retrying in %ds",
                attempt + 1, max_retries, wait,
            )
            await asyncio.sleep(wait)
    else:
        logger.error("Failed to download bhav copy for %s after %d attempts", trade_date, max_retries)
        return None

    # Store delivery_pct/close/prev_close in Redis (screener delivery % scoring)
    from app.core.redis import get_redis

    r = get_redis()
    key = f"{_REDIS_KEY_PREFIX}:{trade_date}"
    ttl = _REDIS_TTL_DAYS * 86400
    redis_payload = {
        sym: {"delivery_pct": v["delivery_pct"], "close": v["close"], "prev_close": v["prev_close"]}
        for sym, v in data.items()
    }
    await r.setex(key, ttl, json.dumps(redis_payload))
    logger.info(
        "Bhav copy for %s stored in Redis (%d stocks, key=%s, TTL=%dd)",
        trade_date, len(data), key, _REDIS_TTL_DAYS,
    )

    # Persist full OHLCV to market_data_daily (fresh from CSV, not from Redis)
    await _persist_daily_to_db(data, trade_date)

    return data


async def get_bhav_copy(trade_date: date) -> dict[str, dict] | None:
    """Read bhav copy data from Redis for a given date.

    Returns the parsed dict or None if not available.
    """
    from app.core.redis import get_redis

    r = get_redis()
    key = f"{_REDIS_KEY_PREFIX}:{trade_date}"
    raw = await r.get(key)
    if raw:
        return json.loads(raw)
    return None


async def _scheduled_fetch() -> None:
    """Scheduled wrapper — skips non-trading days."""
    from app.core.utils import now_ist

    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping bhav copy fetch")
        return

    # Fetch previous trading day's bhav copy
    await fetch_bhav_copy()


_GAP_FILL_LOOKBACK_DAYS = 7


async def _fill_bhav_copy_gaps() -> None:
    """Check last N trading days and fetch any missing bhav copies.

    Called once on startup. Skips days already in Redis.
    """
    from app.core.redis import get_redis
    from app.core.utils import now_ist

    today = now_ist().date()
    r = get_redis()

    days_to_check: list[date] = []
    d = today - timedelta(days=1)
    count = 0
    while count < _GAP_FILL_LOOKBACK_DAYS:
        if is_trading_day(d):
            days_to_check.append(d)
            count += 1
        d -= timedelta(days=1)

    if not days_to_check:
        return

    keys = [f"{_REDIS_KEY_PREFIX}:{d}" for d in days_to_check]
    pipe = r.pipeline()
    for k in keys:
        pipe.exists(k)
    results = await pipe.execute()

    missing = [d for d, exists in zip(days_to_check, results) if not exists]

    if not missing:
        logger.info("Bhav copy gap-fill: all %d recent trading days present", len(days_to_check))
        return

    logger.info(
        "Bhav copy gap-fill: %d missing days: %s",
        len(missing), [str(d) for d in sorted(missing)],
    )

    filled = 0
    for trade_date in sorted(missing):
        data = await fetch_bhav_copy(trade_date)
        if data:
            filled += 1
            logger.info("Gap-filled bhav copy for %s (%d stocks)", trade_date, len(data))
        else:
            logger.warning("Gap-fill failed for %s", trade_date)
        await asyncio.sleep(2.0)

    logger.info("Bhav copy gap-fill complete: %d/%d days filled", filled, len(missing))


async def start_nse_bhav_copy_scheduler() -> None:
    """Start the daily bhav copy scheduler (7:30 AM IST) and fill any gaps."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        _scheduled_fetch,
        CronTrigger(hour=7, minute=30, timezone=IST),
        id="nse_bhav_copy_fetch",
        name="Fetch NSE bhav copy",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("NSE bhav copy scheduler started (daily 7:30 AM IST)")

    # Fill gaps from missed days (non-blocking background task)
    task = asyncio.create_task(_fill_bhav_copy_gaps(), name="bhav_copy_gap_fill")
    from app.core.task_registry import task_registry
    task_registry.track_asyncio_task(
        "bhav_copy_gap_fill", task,
        metadata={"description": "Fill missing bhav copy days on startup"},
    )


async def stop_nse_bhav_copy_scheduler() -> None:
    """Stop the bhav copy scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("NSE bhav copy scheduler stopped")
