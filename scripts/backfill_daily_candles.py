"""One-time backfill: populate market_data_daily for all F&O stocks using Fyers resolution='D'.

Run this once before tomorrow's morning screener to seed historical daily candles.
The bhav copy task (7:30 AM daily) will keep the table current from here on.

Usage:
    cd /path/to/stockTrading
    source backend/.venv/bin/activate
    python scripts/backfill_daily_candles.py              # last 60 days, all F&O stocks
    python scripts/backfill_daily_candles.py --days 90    # longer lookback
    python scripts/backfill_daily_candles.py --symbols TCS,RELIANCE  # subset
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main(days: int, symbols_override: list[str] | None) -> None:
    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.data_sources.nse_client import get_fo_lot_sizes
    from app.data_feed.fyers_client import FyersClient
    from app.models.market_data_daily import MarketDataDaily
    from sqlalchemy import select, func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    today = date.today()
    from_date = today - timedelta(days=days + 10)
    yesterday = today - timedelta(days=1)

    # Resolve symbol list
    if symbols_override:
        symbols = symbols_override
        logger.info("Backfilling %d specified symbols", len(symbols))
    else:
        lot_sizes = await get_fo_lot_sizes()
        if not lot_sizes:
            logger.error("Could not fetch F&O lot sizes — aborting")
            return
        symbols = list(lot_sizes.keys())
        logger.info("Backfilling %d F&O symbols", len(symbols))

    # Find which symbols already have sufficient data
    async with async_session_factory() as session:
        counts_q = (
            select(MarketDataDaily.symbol, func.count().label("cnt"))
            .where(
                MarketDataDaily.symbol.in_(symbols),
                MarketDataDaily.date >= from_date,
                MarketDataDaily.date <= yesterday,
            )
            .group_by(MarketDataDaily.symbol)
        )
        rows = (await session.execute(counts_q)).all()

    existing_counts = {r.symbol: r.cnt for r in rows}
    min_candles = days - 15
    need_fetch = [s for s in symbols if existing_counts.get(s, 0) < min_candles]

    logger.info(
        "%d symbols already have sufficient data, %d need backfill",
        len(symbols) - len(need_fetch), len(need_fetch),
    )

    if not need_fetch:
        logger.info("Nothing to backfill — all symbols have sufficient daily data")
        return

    # Get Fyers token
    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.error("No Fyers token in Redis — start the backend first to auto-login")
        return

    # Fetch and persist
    sem = asyncio.Semaphore(2)
    inter_request_delay = 1.0
    success = 0
    failed = 0

    async def _fetch_and_persist(symbol: str) -> None:
        nonlocal success, failed
        async with sem:
            await asyncio.sleep(inter_request_delay)
            client = FyersClient(access_token=token)
            try:
                fyers_symbol = f"NSE:{symbol}-EQ"
                raw = await client.get_historical_data(
                    symbol=fyers_symbol,
                    resolution="D",
                    from_date=from_date,
                    to_date=yesterday,
                )
                if not raw:
                    logger.warning("No data returned for %s", symbol)
                    failed += 1
                    return

                rows = []
                for c in raw:
                    candle_date = date.fromtimestamp(c["timestamp"])
                    if c.get("close", 0) <= 0:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "date": candle_date,
                        "open": Decimal(str(c["open"])),
                        "high": Decimal(str(c["high"])),
                        "low": Decimal(str(c["low"])),
                        "close": Decimal(str(c["close"])),
                        "volume": int(c["volume"]),
                        "delivery_pct": None,
                    })

                if not rows:
                    failed += 1
                    return

                async with async_session_factory() as session:
                    stmt = pg_insert(MarketDataDaily).values(rows)
                    stmt = stmt.on_conflict_do_nothing(
                        constraint="uq_market_data_daily_symbol_date"
                    )
                    await session.execute(stmt)
                    await session.commit()

                logger.info("%-20s  %d rows  (latest: %s)", symbol, len(rows), rows[-1]["date"])
                success += 1

            except Exception as e:
                logger.warning("Failed for %s: %s", symbol, e)
                failed += 1
            finally:
                await client.close()

    await asyncio.gather(*[_fetch_and_persist(s) for s in need_fetch], return_exceptions=True)

    logger.info(
        "\nBackfill complete: %d succeeded, %d failed out of %d symbols",
        success, failed, len(need_fetch),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill market_data_daily from Fyers")
    parser.add_argument("--days", type=int, default=60, help="Lookback in calendar days (default: 60)")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol list (default: all F&O)")
    args = parser.parse_args()

    symbols_override = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    asyncio.run(main(days=args.days, symbols_override=symbols_override))
