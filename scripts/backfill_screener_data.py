#!/usr/bin/env python3
"""Backfill screener data: NSE bhav copy (delivery %) + stock futures OI.

Fetches historical data from NSE archives (no Fyers token needed) and stores:
- Bhav copy → Redis (`nse:bhav_copy:{date}`, 90-day TTL)
- Stock futures OI → PostgreSQL (`oi_snapshots` table, option_type="FUT")

The morning screener needs ≥2 days of OI data to compute change deltas, and
bhav copy for delivery % scoring. This script seeds both from NSE archives.

NSE URL formats (as of 2026):
- CM bhav: https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv
- FO bhav: https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip

Both require a cookie session (preflight GET to nseindia.com).

Usage:
    source backend/.venv/bin/activate

    # Default: last 7 trading days, both sources
    python scripts/backfill_screener_data.py

    # Custom lookback
    python scripts/backfill_screener_data.py --days 14

    # Only bhav copy
    python scripts/backfill_screener_data.py --bhav-only

    # Only FO OI
    python scripts/backfill_screener_data.py --oi-only

    # Dry run
    python scripts/backfill_screener_data.py --dry-run
"""

import argparse
import asyncio
import csv
import io
import json
import logging
import sys
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_screener")

NSE_RATE_LIMIT = 2.0

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

_CM_BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
_FO_BHAV_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
_NSE_HOME = "https://www.nseindia.com"


def _collect_trading_days(ref_date: date, count: int) -> list[date]:
    """Collect the last `count` trading days before ref_date, oldest first."""
    from app.core.utils import is_trading_day

    days: list[date] = []
    d = ref_date - timedelta(days=1)
    while len(days) < count:
        if is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _get_nse_session():
    """Create an httpx client with NSE cookies."""
    import httpx

    client = httpx.Client(
        headers=_NSE_HEADERS,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    )
    client.get(_NSE_HOME)
    return client


def _build_cm_bhav_url(trade_date: date) -> str:
    return _CM_BHAV_URL.format(ddmmyyyy=trade_date.strftime("%d%m%Y"))


def _build_fo_bhav_url(trade_date: date) -> str:
    return _FO_BHAV_URL.format(yyyymmdd=trade_date.strftime("%Y%m%d"))


# ---------------------------------------------------------------------------
# CM bhav copy download + parse (new NSE format)
# ---------------------------------------------------------------------------


def _download_and_parse_cm_bhav(client, trade_date: date) -> dict[str, dict] | None:
    """Download CM bhav copy CSV and parse delivery % for EQ series."""
    url = _build_cm_bhav_url(trade_date)
    response = client.get(url)
    if response.status_code != 200:
        logger.warning("CM bhav HTTP %d for %s", response.status_code, url)
        return None

    content_type = response.headers.get("content-type", "")
    if "html" in content_type.lower():
        logger.warning("CM bhav returned HTML (blocked?) for %s", trade_date)
        return None

    return _parse_cm_bhav_csv(response.text)


def _parse_cm_bhav_csv(csv_text: str) -> dict[str, dict]:
    """Parse new-format NSE CM bhav CSV.

    Columns: SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE,
    LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY,
    TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
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
            delivery_pct = float(row.get("DELIV_PER", 0) or 0)
            close = float(row.get("CLOSE_PRICE", 0) or 0)
            prev_close = float(row.get("PREV_CLOSE", 0) or 0)
        except (ValueError, TypeError):
            continue

        result[symbol] = {
            "delivery_pct": delivery_pct,
            "close": close,
            "prev_close": prev_close,
        }

    return result


# ---------------------------------------------------------------------------
# FO bhav copy download + parse (new NSE format)
# ---------------------------------------------------------------------------


def _download_fo_bhav(client, trade_date: date) -> list[dict] | None:
    """Download FO bhav copy ZIP, extract stock futures rows."""
    url = _build_fo_bhav_url(trade_date)
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


def _parse_fo_bhav_csv(csv_text: str, trade_date: date) -> list[dict]:
    """Parse new-format NSE FO bhav CSV, extract nearest-expiry STF rows.

    Key columns: FinInstrmTp (STF=stock futures), TckrSymb, XpryDt,
    OpnIntrst, ChngInOpnIntrst, TtlTradgVol.
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

        # Only consider expiries >= trade_date (front month or later)
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

    # Pick nearest expiry per symbol
    results: list[dict] = []
    for entries in by_symbol.values():
        entries.sort(key=lambda e: e["expiry_date"])
        results.append(entries[0])

    return results


# ---------------------------------------------------------------------------
# Bhav copy backfill
# ---------------------------------------------------------------------------


async def backfill_bhav_copy(trading_days: list[date], dry_run: bool) -> int:
    from app.core.redis import get_redis
    from app.tasks.nse_bhav_copy_task import get_bhav_copy

    filled = 0
    client = None

    for trade_date in trading_days:
        existing = await get_bhav_copy(trade_date)
        if existing:
            logger.info("  %s — already in Redis (%d stocks), skip", trade_date, len(existing))
            continue

        if dry_run:
            logger.info("  %s — [dry-run] would fetch", trade_date)
            filled += 1
            continue

        if client is None:
            client = await asyncio.to_thread(_get_nse_session)

        data = await asyncio.to_thread(_download_and_parse_cm_bhav, client, trade_date)
        if data:
            r = get_redis()
            key = f"nse:bhav_copy:{trade_date}"
            await r.setex(key, 90 * 86400, json.dumps(data))
            logger.info("  %s — stored %d stocks", trade_date, len(data))
            filled += 1
        else:
            logger.warning("  %s — fetch FAILED", trade_date)

        await asyncio.sleep(NSE_RATE_LIMIT)

    if client:
        client.close()
    return filled


# ---------------------------------------------------------------------------
# FO OI backfill
# ---------------------------------------------------------------------------


async def backfill_fo_oi(trading_days: list[date], dry_run: bool) -> int:
    from app.core.constants import IST

    filled = 0
    client = None

    for trade_date in trading_days:
        if dry_run:
            logger.info("  %s — [dry-run] would fetch", trade_date)
            filled += 1
            continue

        if client is None:
            client = await asyncio.to_thread(_get_nse_session)

        rows = await asyncio.to_thread(_download_fo_bhav, client, trade_date)
        if rows is None:
            logger.warning("  %s — FO bhav fetch FAILED", trade_date)
            await asyncio.sleep(NSE_RATE_LIMIT)
            continue

        if not rows:
            logger.info("  %s — no STF rows found", trade_date)
            await asyncio.sleep(NSE_RATE_LIMIT)
            continue

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.core.database import async_session_factory
        from app.models.oi_snapshot import OISnapshot

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

        logger.info("  %s — inserted %d FUT OI rows (%d symbols)", trade_date, result.rowcount, len(rows))
        filled += 1

        await asyncio.sleep(NSE_RATE_LIMIT)

    if client:
        client.close()
    return filled


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill screener data (bhav copy + FO OI)")
    parser.add_argument("--days", type=int, default=7, help="Trading days to backfill (default: 7)")
    parser.add_argument("--bhav-only", action="store_true", help="Only backfill bhav copy")
    parser.add_argument("--oi-only", action="store_true", help="Only backfill FO OI")
    parser.add_argument("--dry-run", action="store_true", help="Log what would happen, don't write")
    args = parser.parse_args()

    from app.core.utils import now_ist

    today = now_ist().date()
    trading_days = _collect_trading_days(today, args.days)
    logger.info(
        "Backfilling %d trading days: %s → %s %s",
        len(trading_days),
        trading_days[0] if trading_days else "—",
        trading_days[-1] if trading_days else "—",
        "[DRY RUN]" if args.dry_run else "",
    )

    bhav_count = 0
    oi_count = 0

    if not args.oi_only:
        logger.info("\n=== Bhav Copy (delivery %%) ===")
        bhav_count = await backfill_bhav_copy(trading_days, args.dry_run)

    if not args.bhav_only:
        logger.info("\n=== FO Stock Futures OI ===")
        oi_count = await backfill_fo_oi(trading_days, args.dry_run)

    logger.info(
        "\nDone. Bhav copy: %d/%d days. FO OI: %d/%d days.",
        bhav_count, len(trading_days), oi_count, len(trading_days),
    )


if __name__ == "__main__":
    asyncio.run(main())
