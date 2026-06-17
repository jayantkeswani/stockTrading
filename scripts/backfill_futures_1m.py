"""Backfill 1-minute SPOT candles for the full F&O futures universe (stocks) from Fyers.

Fetches NSE:{SYMBOL}-EQ 1m candles for the last N days and persists them to
market_data_1m under the SHORT name (e.g. "RELIANCE"), so the S5 replay can drive
PDH_PDL (and other setups) across the *whole* universe — testing whether the edge
generalises beyond the morning screener's ~15 picks, or whether it was selection.

SPOT (not futures) on purpose: spot is continuous and always served by Fyers, while
expired futures contracts return errors (the May contract is already gone); PDH/PDL
breaks are ~identical on spot vs near-month futures (basis is a near-constant offset).

Target DB = whatever DATABASE_URL points at. Run against the bt DB:
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/backfill_futures_1m.py --days 50

Requires a Fyers token in Redis (run auto_login_and_store() first). Idempotent:
persists via ON CONFLICT DO NOTHING and skips symbols already well-covered.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_futures_1m")

# Index names get_fo_lot_sizes may include — they have no NSE:{name}-EQ spot symbol.
_INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
                "SENSEX", "BANKEX", "NIFTYIT", "SENSEX50"}

_BATCH = 4000  # rows/insert; 4000 x 7 cols = 28k params < asyncpg's 32767 limit


async def main(days, symbols_override, concurrency, chunk_days):
    from sqlalchemy import func, select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.core.utils import is_trading_day
    from app.data_sources.nse_client import get_fo_lot_sizes
    from app.models.market_data import MarketData1m
    from app.services.candle_backfill import _fetch_history_range_via_sdk

    today = date.today()
    start = today - timedelta(days=days)
    yesterday = today - timedelta(days=1)

    token = await get_redis().get("fyers:access_token")
    if not token:
        logger.error("No Fyers token in Redis — run auto_login_and_store() first")
        return

    if symbols_override:
        symbols = symbols_override
    else:
        lots = await get_fo_lot_sizes()
        if not lots:
            logger.error("Could not fetch F&O lot sizes — aborting")
            return
        symbols = sorted(s for s in lots if s.upper() not in _INDEX_NAMES)

    logger.info("Universe: %d symbols | window %s -> %s | DB=%s",
                len(symbols), start, yesterday,
                os.environ.get("DATABASE_URL", "(default .env)"))

    floor = int(days * 0.55) * 300  # ~substantially covered already
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(MarketData1m.symbol, func.count().label("c"))
            .where(MarketData1m.symbol.in_(symbols),
                   MarketData1m.timestamp >= datetime.combine(start, datetime.min.time()))
            .group_by(MarketData1m.symbol)
        )).all()
    have = {x.symbol: x.c for x in rows}

    def to_rows(symbol, candles):
        out = []
        for c in candles:
            ts = datetime.fromtimestamp(c["timestamp"], tz=IST)
            if (not is_trading_day(ts.date())
                    or ts.time() < MARKET_OPEN or ts.time() > MARKET_CLOSE):
                continue
            out.append({"symbol": symbol, "timestamp": ts,
                        "open": Decimal(str(c["open"])), "high": Decimal(str(c["high"])),
                        "low": Decimal(str(c["low"])), "close": Decimal(str(c["close"])),
                        "volume": int(c["volume"])})
        return out

    async def persist(symbol, candles):
        rws = to_rows(symbol, candles)
        if not rws:
            return 0
        async with async_session_factory() as s:
            for i in range(0, len(rws), _BATCH):
                await s.execute(pg_insert(MarketData1m)
                                .values(rws[i:i + _BATCH])
                                .on_conflict_do_nothing(constraint="uq_market_data_symbol_time"))
            await s.commit()
        return len(rws)

    sem = asyncio.Semaphore(concurrency)
    stats = {"ok": 0, "skip": 0, "fail": 0, "rows": 0}

    async def work(sym):
        async with sem:
            if have.get(sym, 0) >= floor:
                stats["skip"] += 1
                return
            fy = f"NSE:{sym}-EQ"
            candles, cs = [], start
            while cs <= yesterday:
                ce = min(cs + timedelta(days=chunk_days - 1), yesterday)
                try:
                    candles += await asyncio.to_thread(
                        _fetch_history_range_via_sdk, token, fy, cs, ce)
                except Exception as e:
                    logger.warning("%s fetch %s..%s failed: %s", sym, cs, ce, str(e)[:160])
                cs = ce + timedelta(days=1)
                await asyncio.sleep(0.25)
            if not candles:
                logger.warning("%-14s no candles", sym)
                stats["fail"] += 1
                return
            try:
                ins = await persist(sym, candles)
            except Exception as e:
                logger.warning("%-14s persist failed: %s", sym, str(e)[:160])
                stats["fail"] += 1
                return
            stats["ok"] += 1
            stats["rows"] += ins
            logger.info("%-14s %6d fetched / %6d persisted", sym, len(candles), ins)

    await asyncio.gather(*[work(s) for s in symbols], return_exceptions=True)
    logger.info("DONE ok=%d skip=%d fail=%d rows=%d",
                stats["ok"], stats["skip"], stats["fail"], stats["rows"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Backfill 1m spot candles for F&O universe")
    p.add_argument("--days", type=int, default=50, help="lookback in calendar days")
    p.add_argument("--symbols", type=str, default=None, help="comma-separated subset")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--chunk-days", type=int, default=60, help="Fyers serves 50d 1m in one call")
    a = p.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else None
    asyncio.run(main(a.days, syms, a.concurrency, a.chunk_days))
