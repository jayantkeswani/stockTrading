#!/usr/bin/env python3
"""Historical candle backfill script for backtesting.

Fetches 1-minute OHLCV data from Fyers for a configurable date range and
persists it to the MarketData1m table. Uses the same chunked fetching +
idempotent upsert as the live candle_backfill service.

Requires a valid Fyers token in Redis (run `make backend` and authenticate
via the dashboard first, or run the auto-login task).

Usage:
    # From repo root — activate virtualenv first
    source backend/.venv/bin/activate

    python scripts/backfill_for_backtest.py \\
        --symbols NIFTY,BANKNIFTY,SENSEX \\
        --start 2025-10-01 \\
        --end 2026-04-24

    # All active-strategy symbols (default)
    python scripts/backfill_for_backtest.py --start 2025-10-01 --end 2026-04-24

    # Dry-run (counts rows, doesn't insert)
    python scripts/backfill_for_backtest.py --start 2025-10-01 --end 2026-04-24 --dry-run
"""

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Add backend directory to path so imports work when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

CHUNK_DAYS = 6          # Fyers SDK works best with ≤7-day windows
RATE_LIMIT_SLEEP = 0.5  # Seconds between API chunks


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


async def _get_token() -> str:
    from app.core.redis import get_redis
    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        raise SystemExit(
            "No Fyers token found in Redis. "
            "Authenticate via the dashboard or run the auto-login task first."
        )
    return token


async def _get_symbols(requested: list[str] | None) -> dict[str, str]:
    """Return {short_name: fyers_symbol} for the symbols to backfill."""
    if requested:
        from app.core.constants import FYERS_SYMBOL_MAP
        from app.services.candle_backfill import _resolve_fyers_symbol
        return {
            sym: FYERS_SYMBOL_MAP.get(sym) or _resolve_fyers_symbol(sym)
            for sym in requested
        }
    # Default: all active strategy symbols
    from app.services.candle_backfill import _get_all_backfill_symbols
    return await _get_all_backfill_symbols()


async def backfill(
    symbols: dict[str, str],
    start: date,
    end: date,
    dry_run: bool = False,
) -> None:
    from app.services.candle_backfill import _fetch_history_range_via_sdk, _persist_candles

    token = await _get_token()
    today = date.today()
    end = min(end, today - timedelta(days=1))  # Cannot fetch future

    total_inserted = 0

    for sym, fyers_sym in symbols.items():
        logger.info("Backfilling %s (%s) from %s to %s", sym, fyers_sym, start, end)
        sym_total = 0
        chunk_start = start

        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
            try:
                candles = await asyncio.to_thread(
                    _fetch_history_range_via_sdk, token, fyers_sym, chunk_start, chunk_end
                )
                if candles:
                    if dry_run:
                        sym_total += len(candles)
                        logger.debug(
                            "  [dry-run] %s → %s: %d candles (not inserted)",
                            chunk_start, chunk_end, len(candles),
                        )
                    else:
                        inserted = await _persist_candles(sym, candles)
                        sym_total += inserted
                        logger.debug(
                            "  %s → %s: %d candles inserted",
                            chunk_start, chunk_end, inserted,
                        )
            except Exception:
                logger.exception("Chunk fetch failed for %s (%s → %s)", sym, chunk_start, chunk_end)

            chunk_start = chunk_end + timedelta(days=1)
            await asyncio.sleep(RATE_LIMIT_SLEEP)

        action = "found" if dry_run else "inserted"
        logger.info("  %s: %d candles %s", sym, sym_total, action)
        total_inserted += sym_total

    action = "found" if dry_run else "inserted"
    logger.info(
        "Backfill complete: %d total candles %s across %d symbols",
        total_inserted, action, len(symbols),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical candles for backtesting")
    parser.add_argument(
        "--symbols", "-s",
        help="Comma-separated list of symbols (e.g. NIFTY,BANKNIFTY). Default: all active strategy symbols.",
    )
    parser.add_argument("--start", required=True, type=_parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_parse_date, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count candles without inserting them",
    )
    args = parser.parse_args()

    requested = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    # Initialise DB + Redis connections
    from app.core.database import engine
    from app.core.redis import get_redis

    symbols = await _get_symbols(requested)
    if not symbols:
        raise SystemExit("No symbols to backfill.")

    logger.info(
        "Backfilling %d symbol(s): %s",
        len(symbols), ", ".join(symbols.keys()),
    )
    logger.info("Date range: %s → %s%s", args.start, args.end, " [DRY RUN]" if args.dry_run else "")

    await backfill(symbols, args.start, args.end, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
