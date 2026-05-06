"""OI snapshot task — periodically fetches option chain and futures OI from Fyers.

Three responsibilities:
1. Index option chain OI — every 3 minutes during market hours. Fetches the
   option chain for each index symbol and persists strike-level OI data to the
   oi_snapshots table.
2. Stock futures EOD OI — daily at 3:25 PM IST. Fetches OI for all F&O stock
   futures contracts and persists one row per symbol (option_type="FUT",
   strike_price=0). The morning screener compares two daily snapshots to
   compute OI change for each stock.
3. Gap-fill on startup — checks last 7 trading days in DB for FUT OI rows.
   Any missing days are backfilled from NSE FO bhav copy archives (no Fyers
   token needed).

NSE FO bhav copy URL (as of 2026):
    https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip
Requires cookie session (preflight GET to nseindia.com).

The strategy_runner reads from oi_snapshots to build OIAnalysis for
MarketContext, providing OI confirmation for trade signals.
"""

import asyncio
import csv
import io
import logging
import zipfile
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.constants import FYERS_SYMBOL_MAP, IST, MARKET_CLOSE, MARKET_OPEN
from app.core.utils import is_market_open, is_trading_day

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Symbols to fetch OI for — only indices (not VIX, not stocks)
OI_SYMBOLS = {
    k: v for k, v in FYERS_SYMBOL_MAP.items()
    if k != "INDIA VIX"
}

OI_FETCH_INTERVAL_MINUTES = 3
FYERS_QUOTES_BATCH_SIZE = 50  # Fyers get_quotes supports up to 50 symbols per call
FYERS_SEMAPHORE_LIMIT = 2    # Max concurrent Fyers API calls — keeps us under rate limit
FYERS_INTER_REQUEST_DELAY = 0.3  # seconds between requests within each semaphore slot


async def fetch_oi_snapshots():
    """Fetch option chain OI data for all index symbols and persist to DB.

    Skips if market is closed (OI doesn't change after hours).
    """
    if not is_market_open():
        return

    from app.core.redis import get_redis

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot fetch OI data")
        return

    total_rows = 0
    for symbol in OI_SYMBOLS:
        try:
            count = await _fetch_and_persist_oi(token, symbol)
            total_rows += count
        except Exception:
            logger.exception("Failed to fetch OI for %s", symbol)

    if total_rows > 0:
        logger.info("OI snapshot: persisted %d rows across %d symbols", total_rows, len(OI_SYMBOLS))


async def _fetch_and_persist_oi(token: str, symbol: str) -> int:
    """Fetch option chain for a single symbol and insert into oi_snapshots.

    Returns the number of rows inserted.
    """
    from app.data_feed.fyers_client import FyersClient

    client = FyersClient(access_token=token)
    try:
        data = await client.get_option_chain(symbol)
    finally:
        await client.close()

    if not data or data.get("s") != "ok":
        logger.warning(
            "Fyers option chain error for %s: %s",
            symbol, data.get("message", "unknown error") if data else "no response",
        )
        return 0

    return await _parse_and_store(symbol, data)


async def _parse_and_store(symbol: str, data: dict) -> int:
    """Parse Fyers option chain response and insert OI snapshots.

    Fyers v3 option chain response structure:
    {
        "s": "ok",
        "data": {
            "expiryData": [
                {"date": "21-04-2026", "expiry": "1776765600", "expiry_flag": "W"}
            ],
            "optionsChain": [
                {
                    "strike_price": -1, "option_type": "", "symbol": "NSE:NIFTY50-INDEX",
                    ...  (underlying row, skip)
                },
                {
                    "strike_price": 24000, "option_type": "CE", "oi": 1234567,
                    "oich": 5000, "volume": 98765, "ltp": 250.0,
                    "symbol": "NSE:NIFTY2642124000CE", ...
                },
                {
                    "strike_price": 24000, "option_type": "PE", "oi": 2345678,
                    "oich": -3000, "volume": 87654, "ltp": 180.0,
                    "symbol": "NSE:NIFTY2642124000PE", ...
                },
                ...
            ]
        }
    }
    """
    from decimal import Decimal
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session_factory
    from app.models.oi_snapshot import OISnapshot

    now = datetime.now(IST)
    # Round to nearest minute for consistent timestamps
    now = now.replace(second=0, microsecond=0)

    chain_data = data.get("data", {})
    options_chain = chain_data.get("optionsChain", [])

    if not options_chain:
        logger.debug("Empty option chain for %s", symbol)
        return 0

    # Get expiry date from the response (format: "DD-MM-YYYY")
    expiry_data = chain_data.get("expiryData", [])
    expiry_date = None
    if expiry_data:
        expiry_str = expiry_data[0].get("date", "")
        if expiry_str:
            for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
                try:
                    expiry_date = datetime.strptime(expiry_str, fmt).date()
                    break
                except ValueError:
                    continue

    if expiry_date is None:
        logger.warning("Could not determine expiry date for %s OI snapshot", symbol)
        return 0

    rows = []
    for strike_data in options_chain:
        strike_price = strike_data.get("strike_price", 0)
        if strike_price <= 0:
            continue

        option_type = strike_data.get("option_type", "")
        if option_type not in ("CE", "PE"):
            continue

        oi = int(strike_data.get("oi", 0))
        if oi <= 0:
            continue

        rows.append({
            "symbol": symbol,
            "expiry_date": expiry_date,
            "strike_price": Decimal(str(strike_price)),
            "option_type": option_type,
            "open_interest": oi,
            "oi_change": int(strike_data.get("oich", 0)),
            "volume": int(strike_data.get("volume", 0)),
            "timestamp": now,
        })

    if not rows:
        return 0

    async with async_session_factory() as session:
        stmt = pg_insert(OISnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_oi_snapshot")
        await session.execute(stmt)
        await session.commit()

    return len(rows)


async def fetch_stock_futures_oi():
    """Fetch EOD OI data for all F&O stock futures and persist to DB.

    Runs once daily at 3:25 PM IST. Resolves each F&O stock to its nearest
    futures contract, fetches quotes in batches of 50, and stores one row per
    symbol with option_type="FUT" and strike_price=0.
    """
    from app.core.redis import get_redis

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot fetch stock futures OI")
        return

    # 1. Get list of F&O stocks
    from app.data_sources.nse_client import get_fo_lot_sizes

    lot_sizes = await get_fo_lot_sizes()
    if not lot_sizes:
        logger.warning("No F&O lot sizes returned — skipping stock futures OI")
        return

    # 2. Resolve each stock to its nearest futures symbol
    from app.services.futures_resolver import resolve_futures_contract

    semaphore = asyncio.Semaphore(FYERS_SEMAPHORE_LIMIT)
    resolutions: dict[str, tuple[str, object]] = {}  # symbol -> (fyers_symbol, expiry_date)

    async def _resolve(sym: str):
        async with semaphore:
            try:
                result = await resolve_futures_contract(sym, entry_price=0)
                if result:
                    resolutions[sym] = (result.fyers_symbol, result.expiry_date)
            except Exception:
                logger.debug("Failed to resolve futures for %s", sym, exc_info=True)
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)

    await asyncio.gather(*[_resolve(sym) for sym in lot_sizes])

    if not resolutions:
        logger.warning("No futures symbols resolved — skipping stock futures OI")
        return

    logger.info("Resolved %d/%d F&O stocks to futures symbols", len(resolutions), len(lot_sizes))

    # 3. Batch-fetch quotes (up to 50 per call)
    from app.data_feed.fyers_client import FyersClient

    fyers_symbols = [fs for fs, _ in resolutions.values()]
    batches = [
        fyers_symbols[i : i + FYERS_QUOTES_BATCH_SIZE]
        for i in range(0, len(fyers_symbols), FYERS_QUOTES_BATCH_SIZE)
    ]

    all_quotes: dict[str, dict] = {}  # fyers_symbol -> quote data

    async def _fetch_batch(batch: list[str]):
        async with semaphore:
            client = FyersClient(access_token=token)
            try:
                result = await client.get_quotes(batch)
                if result and result.get("s") == "ok":
                    for q in result.get("d", []):
                        sym = q.get("n", "")
                        if sym:
                            all_quotes[sym] = q.get("v", {})
            except Exception:
                logger.exception("Failed to fetch quotes batch")
            finally:
                await client.close()
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)

    await asyncio.gather(*[_fetch_batch(b) for b in batches])

    # 4. Persist to oi_snapshots
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.database import async_session_factory
    from app.models.oi_snapshot import OISnapshot

    now = datetime.now(IST).replace(second=0, microsecond=0)

    rows = []
    for symbol, (fyers_symbol, expiry_date) in resolutions.items():
        quote = all_quotes.get(fyers_symbol, {})
        # Fyers REST quotes API returns "oi" for open interest (not "open_interest")
        oi = int(quote.get("oi", 0) or quote.get("open_interest", 0) or 0)

        rows.append({
            "symbol": symbol,
            "expiry_date": expiry_date,
            "strike_price": Decimal("0"),
            "option_type": "FUT",
            "open_interest": oi,
            "oi_change": 0,
            "volume": int(quote.get("volume", 0) or 0),
            "timestamp": now,
        })

    if not rows:
        return

    async with async_session_factory() as session:
        stmt = pg_insert(OISnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_oi_snapshot")
        await session.execute(stmt)
        await session.commit()

    logger.info(
        "Stock futures OI snapshot: persisted %d rows at %s",
        len(rows), now.strftime("%H:%M"),
    )


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
_NSE_HOME = "https://www.nseindia.com"
_FO_BHAV_URL = (
    "https://nsearchives.nseindia.com/content/fo"
    "/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
_GAP_FILL_LOOKBACK_DAYS = 7


def _download_fo_bhav(trade_date: date) -> list[dict] | None:
    """Download NSE FO bhav copy ZIP and extract stock futures rows.

    Runs in thread (called via asyncio.to_thread). Returns list of dicts
    with symbol/expiry_date/open_interest/volume, or None on failure.
    """
    import httpx

    url = _FO_BHAV_URL.format(yyyymmdd=trade_date.strftime("%Y%m%d"))
    try:
        with httpx.Client(
            headers=_NSE_HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        ) as client:
            client.get(_NSE_HOME)
            response = client.get(url)

            if response.status_code != 200:
                logger.warning("FO bhav HTTP %d for %s", response.status_code, url)
                return None

            zip_buffer = io.BytesIO(response.content)
            try:
                with zipfile.ZipFile(zip_buffer) as zf:
                    csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
                    if not csv_names:
                        logger.warning("No CSV in FO bhav ZIP for %s", trade_date)
                        return None
                    csv_text = zf.read(csv_names[0]).decode("utf-8")
            except zipfile.BadZipFile:
                logger.warning("Invalid ZIP for FO bhav %s", trade_date)
                return None

            return _parse_fo_bhav_csv(csv_text, trade_date)

    except httpx.HTTPError:
        logger.exception("HTTP error downloading FO bhav for %s", trade_date)
        return None


def _parse_fo_bhav_csv(csv_text: str, trade_date: date) -> list[dict]:
    """Parse NSE FO bhav CSV, extract nearest-expiry stock futures rows.

    Filters for FinInstrmTp == "STF" (stock futures), picks nearest expiry
    per symbol.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    by_symbol: dict[str, list[dict]] = {}
    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items()}
        if row.get("FinInstrmTp") != "STF":
            continue

        symbol = row.get("TckrSymb", "").strip()
        if not symbol:
            continue

        try:
            expiry_str = row.get("XpryDt", "").strip()
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            oi = int(float(row.get("OpnIntrst", 0) or 0))
            volume = int(float(row.get("TtlTradgVol", 0) or 0))
        except (ValueError, TypeError):
            continue

        if expiry_date < trade_date:
            continue

        if symbol not in by_symbol:
            by_symbol[symbol] = []
        by_symbol[symbol].append({
            "symbol": symbol,
            "expiry_date": expiry_date,
            "open_interest": oi,
            "volume": volume,
        })

    results: list[dict] = []
    for entries in by_symbol.values():
        entries.sort(key=lambda e: e["expiry_date"])
        results.append(entries[0])

    return results


async def _fill_stock_futures_oi_gaps() -> None:
    """Check last N trading days and fetch any missing stock futures OI from NSE.

    Called once on startup. Checks the oi_snapshots table for days with FUT rows;
    any missing trading days are backfilled from the NSE FO bhav copy archive.
    """
    from decimal import Decimal

    from sqlalchemy import func, select, text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.database import async_session_factory
    from app.core.utils import now_ist
    from app.models.oi_snapshot import OISnapshot

    today = now_ist().date()

    # Collect last N trading days
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

    # Check which days already have FUT OI rows in DB
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.date(OISnapshot.timestamp))
            .where(OISnapshot.option_type == "FUT")
            .where(func.date(OISnapshot.timestamp).in_(days_to_check))
            .distinct()
        )
        existing_dates = {row[0] for row in result.all()}

    missing = [d for d in days_to_check if d not in existing_dates]

    if not missing:
        logger.info("FUT OI gap-fill: all %d recent trading days present", len(days_to_check))
        return

    logger.info(
        "FUT OI gap-fill: %d missing days: %s",
        len(missing), [str(d) for d in sorted(missing)],
    )

    filled = 0
    for trade_date in sorted(missing):
        rows = await asyncio.to_thread(_download_fo_bhav, trade_date)
        if rows is None:
            logger.warning("FUT OI gap-fill failed for %s", trade_date)
            await asyncio.sleep(2.0)
            continue

        if not rows:
            logger.info("FUT OI gap-fill: no STF rows for %s", trade_date)
            await asyncio.sleep(2.0)
            continue

        ts = datetime.combine(trade_date, time(15, 25), tzinfo=IST)
        db_rows = []
        for r in rows:
            db_rows.append({
                "symbol": r["symbol"],
                "expiry_date": r["expiry_date"],
                "strike_price": Decimal("0"),
                "option_type": "FUT",
                "open_interest": r["open_interest"],
                "oi_change": 0,
                "volume": r["volume"],
                "timestamp": ts,
            })

        async with async_session_factory() as session:
            stmt = pg_insert(OISnapshot).values(db_rows)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_oi_snapshot")
            result = await session.execute(stmt)
            await session.commit()

        logger.info(
            "FUT OI gap-filled %s: %d symbols inserted", trade_date, result.rowcount,
        )
        filled += 1
        await asyncio.sleep(2.0)

    logger.info("FUT OI gap-fill complete: %d/%d days filled", filled, len(missing))


S5_OI_INTERVAL_MINUTES = 10  # kept for log message; actual trigger uses CronTrigger below


async def fetch_s5_watchlist_oi():
    """Fetch intraday OI for Strategy 5 watchlist futures (every 15 minutes).

    Reads today's screened watchlist from Redis, resolves each symbol to its
    near-month futures contract, and batch-fetches live quotes to capture
    current open_interest. Persists to oi_snapshots with option_type="FUT".

    This is targeted (10-15 symbols vs ~180 for the EOD job) so it runs
    cheaply every 15 minutes. The EOD job at 3:25 PM covers the full F&O
    universe for the morning screener's OI scoring.
    """
    if not is_market_open():
        return

    from app.core.redis import get_redis

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        return

    # Load today's S5 watchlist
    import json
    from datetime import datetime as _dt
    today_str = _dt.now(IST).strftime("%Y-%m-%d")
    raw = await r.get(f"strat5:watchlist:{today_str}")
    if not raw:
        return

    watchlist = json.loads(raw)
    symbols = [item["symbol"] for item in watchlist if item.get("symbol")]
    if not symbols:
        return

    # Resolve each symbol to its near-month futures contract
    from app.services.futures_resolver import resolve_futures_contract

    semaphore = asyncio.Semaphore(FYERS_SEMAPHORE_LIMIT)
    resolutions: dict[str, tuple[str, object]] = {}

    async def _resolve(sym: str):
        async with semaphore:
            try:
                result = await resolve_futures_contract(sym, entry_price=0)
                if result:
                    resolutions[sym] = (result.fyers_symbol, result.expiry_date)
            except Exception:
                logger.debug("S5 OI: failed to resolve futures for %s", sym, exc_info=True)
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)

    await asyncio.gather(*[_resolve(sym) for sym in symbols])

    if not resolutions:
        return

    # Batch-fetch quotes (all symbols fit in one call for typical watchlist sizes)
    from app.data_feed.fyers_client import FyersClient

    fyers_symbols = [fs for fs, _ in resolutions.values()]
    batches = [
        fyers_symbols[i: i + FYERS_QUOTES_BATCH_SIZE]
        for i in range(0, len(fyers_symbols), FYERS_QUOTES_BATCH_SIZE)
    ]

    all_quotes: dict[str, dict] = {}

    async def _fetch_batch(batch: list[str]):
        async with semaphore:
            client = FyersClient(access_token=token)
            try:
                result = await client.get_quotes(batch)
                if result and result.get("s") == "ok":
                    for q in result.get("d", []):
                        sym = q.get("n", "")
                        if sym:
                            all_quotes[sym] = q.get("v", {})
            except Exception:
                logger.exception("S5 OI: failed to fetch quotes batch")
            finally:
                await client.close()
            await asyncio.sleep(FYERS_INTER_REQUEST_DELAY)

    await asyncio.gather(*[_fetch_batch(b) for b in batches])

    # Persist to oi_snapshots
    from decimal import Decimal
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.core.database import async_session_factory
    from app.models.oi_snapshot import OISnapshot

    now = datetime.now(IST).replace(second=0, microsecond=0)

    rows = []
    for symbol, (fyers_symbol, expiry_date) in resolutions.items():
        quote = all_quotes.get(fyers_symbol, {})
        # Fyers REST quotes API returns "oi" for open interest (not "open_interest")
        oi = int(quote.get("oi", 0) or quote.get("open_interest", 0) or 0)
        rows.append({
            "symbol": symbol,
            "expiry_date": expiry_date,
            "strike_price": Decimal("0"),
            "option_type": "FUT",
            "open_interest": oi,
            "oi_change": 0,
            "volume": int(quote.get("volume", 0) or 0),
            "timestamp": now,
        })

    if not rows:
        return

    async with async_session_factory() as session:
        stmt = pg_insert(OISnapshot).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_oi_snapshot")
        await session.execute(stmt)
        await session.commit()

    logger.info(
        "S5 watchlist OI snapshot: %d symbols at %s",
        len(rows), now.strftime("%H:%M"),
    )


async def start_oi_snapshot_scheduler():
    """Start the periodic OI snapshot scheduler and fill any gaps."""
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=IST)
    _scheduler.add_job(
        fetch_oi_snapshots,
        trigger=IntervalTrigger(minutes=OI_FETCH_INTERVAL_MINUTES, timezone=IST),
        id="oi_snapshot_fetch",
        name="Fetch OI snapshots",
        replace_existing=True,
    )
    _scheduler.add_job(
        fetch_stock_futures_oi,
        trigger=CronTrigger(hour=15, minute=25, timezone=IST),
        id="stock_futures_oi_fetch",
        name="Fetch stock futures OI (EOD)",
        replace_existing=True,
    )
    _scheduler.add_job(
        fetch_s5_watchlist_oi,
        trigger=CronTrigger(minute="*/10", hour="9-15", timezone=IST),
        id="s5_watchlist_oi_fetch",
        name="Fetch S5 watchlist futures OI (intraday)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("OI snapshot scheduler started (every %d minutes)", OI_FETCH_INTERVAL_MINUTES)
    logger.info("Stock futures OI scheduler started (daily at 15:25 IST)")
    logger.info("S5 watchlist OI scheduler started (every 10 min from 9:20 IST)")

    # Fill gaps from missed days (non-blocking background task)
    task = asyncio.create_task(_fill_stock_futures_oi_gaps(), name="fut_oi_gap_fill")
    from app.core.task_registry import task_registry
    task_registry.track_asyncio_task(
        "fut_oi_gap_fill", task,
        metadata={"description": "Fill missing stock futures OI days on startup"},
    )


async def stop_oi_snapshot_scheduler():
    """Stop the OI snapshot scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("OI snapshot scheduler stopped")
