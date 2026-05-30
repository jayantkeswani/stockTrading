"""One-time audit: check freshness and availability of all data sources used by the morning screener.

Run from the backend virtualenv:
    cd /path/to/stockTrading
    source backend/.venv/bin/activate
    python scripts/audit_screener_data.py

No side effects — read-only queries.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

# Allow imports from backend/app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.constants import NSE_HOLIDAYS  # single source of truth — avoids stale duplicate

# ---------------------------------------------------------------------------
# Config — reads from env or uses project defaults
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_now() -> datetime:
    return datetime.utcnow() + IST_OFFSET


def _previous_trading_day(ref: date) -> date:
    d = ref - timedelta(days=1)
    while d.weekday() >= 5 or d in NSE_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def _ok(msg: str) -> str:
    return f"  \033[32m✓\033[0m  {msg}"


def _warn(msg: str) -> str:
    return f"  \033[33m⚠\033[0m  {msg}"


def _fail(msg: str) -> str:
    return f"  \033[31m✗\033[0m  {msg}"


def _section(title: str) -> None:
    print(f"\n\033[1m{'─' * 60}\033[0m")
    print(f"\033[1m  {title}\033[0m")
    print(f"\033[1m{'─' * 60}\033[0m")


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------


async def audit():
    today = _ist_now().date()
    yesterday = _previous_trading_day(today)

    print(f"\n\033[1mMorning Screener Data Freshness Audit\033[0m")
    print(f"  Audit date (IST): {today}")
    print(f"  Expected data for: {yesterday} (prev trading day)")

    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    r = await aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        async with AsyncSessionLocal() as session:

            # ----------------------------------------------------------------
            # 1. Daily candles (market_data_daily — source of truth)
            # ----------------------------------------------------------------
            _section("1. Daily Candles  (RS / ADR / volume trend / stock trend / 52w high / range position)")

            latest_candle_q = sa.text("""
                SELECT MAX(date) AS latest_date, COUNT(DISTINCT symbol) AS symbol_count
                FROM market_data_daily
            """)
            row = (await session.execute(latest_candle_q)).one()

            if row.latest_date is None:
                print(_fail("No rows in market_data_daily — run scripts/backfill_daily_candles.py first"))
            else:
                age_days = (yesterday - row.latest_date).days
                msg = f"Latest daily candle: {row.latest_date}  ({row.symbol_count} symbols)"
                if age_days == 0:
                    print(_ok(msg + "  — current ✓"))
                elif age_days == 1:
                    print(_warn(msg + "  — 1 day stale (may be holiday/weekend)"))
                else:
                    print(_fail(msg + f"  — {age_days} days stale ✗  (bhav copy task may have failed)"))

            # Per-symbol breakdown
            per_sym_q = sa.text("""
                SELECT symbol, MAX(date) AS latest_date, COUNT(*) AS candle_count
                FROM market_data_daily
                GROUP BY symbol
                ORDER BY latest_date ASC
            """)
            sym_rows = (await session.execute(per_sym_q)).all()

            stale_symbols = [r for r in sym_rows if r.latest_date < yesterday]
            current_symbols = [r for r in sym_rows if r.latest_date >= yesterday]

            print(f"       Symbols current (up to {yesterday}): {len(current_symbols)}")
            if stale_symbols:
                print(_warn(f"  Stale symbols ({len(stale_symbols)} total):"))
                for s in stale_symbols[:15]:
                    print(f"       {s.symbol:<20} latest={s.latest_date}  count={s.candle_count}")
                if len(stale_symbols) > 15:
                    print(f"       ... and {len(stale_symbols) - 15} more")

            # Symbols with < 20 rows (RS requires 20; will default to 50th percentile)
            thin_q = sa.text("""
                SELECT symbol, COUNT(*) AS cnt
                FROM market_data_daily
                GROUP BY symbol
                HAVING COUNT(*) < 20
                ORDER BY cnt
            """)
            thin_rows = (await session.execute(thin_q)).all()
            if thin_rows:
                print(_warn(f"  Symbols with < 20 rows (RS will default to 50.0): {len(thin_rows)}"))
                for t in thin_rows[:10]:
                    print(f"       {t.symbol:<20} rows={t.cnt}")

            # ----------------------------------------------------------------
            # 2. OI snapshots — FUT rows
            # ----------------------------------------------------------------
            _section("2. OI Snapshots  (oi_change factor)")

            oi_q = sa.text("""
                SELECT
                    MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS latest_ts,
                    COUNT(DISTINCT symbol) AS symbol_count,
                    COUNT(DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS date_count,
                    MIN((timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS oldest_date,
                    MAX((timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS newest_date
                FROM oi_snapshots
                WHERE option_type = 'FUT'
            """)
            oi_row = (await session.execute(oi_q)).one()

            if oi_row.latest_ts is None:
                print(_fail("No FUT OI snapshots found in oi_snapshots table"))
            else:
                newest = oi_row.newest_date
                age_days = (yesterday - newest).days
                msg = (
                    f"Latest FUT OI snapshot: {newest}  "
                    f"({oi_row.symbol_count} symbols, {oi_row.date_count} distinct dates)"
                )
                if age_days == 0:
                    print(_ok(msg + "  — current ✓"))
                elif age_days <= 1:
                    print(_warn(msg + f"  — {age_days} day stale"))
                else:
                    print(_fail(msg + f"  — {age_days} days stale ✗"))

            # Symbols that have only 1 distinct date (OI change will be 0)
            single_date_q = sa.text("""
                SELECT symbol, COUNT(DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date) AS date_count
                FROM oi_snapshots
                WHERE option_type = 'FUT'
                GROUP BY symbol
                HAVING COUNT(DISTINCT (timestamp AT TIME ZONE 'Asia/Kolkata')::date) < 2
                ORDER BY symbol
            """)
            single_rows = (await session.execute(single_date_q)).all()
            if single_rows:
                print(_warn(f"  Symbols with only 1 OI date (change will default to 0): {len(single_rows)}"))

            # ----------------------------------------------------------------
            # 3. Stock fundamentals (stage 3 LLM enrichment)
            # ----------------------------------------------------------------
            _section("3. Stock Fundamentals  (stage 3 LLM enrichment — 12h staleness threshold)")

            fund_q = sa.text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN last_refreshed_at > NOW() - INTERVAL '12 hours' THEN 1 END) AS fresh,
                    COUNT(CASE WHEN last_refreshed_at <= NOW() - INTERVAL '12 hours' THEN 1 END) AS stale,
                    COUNT(CASE WHEN last_refreshed_at <= NOW() - INTERVAL '48 hours' THEN 1 END) AS very_stale,
                    MIN(last_refreshed_at AT TIME ZONE 'Asia/Kolkata') AS oldest_refresh,
                    MAX(last_refreshed_at AT TIME ZONE 'Asia/Kolkata') AS newest_refresh
                FROM stock_fundamentals
            """)
            fund_row = (await session.execute(fund_q)).one()

            if fund_row.total == 0:
                print(_fail("No rows in stock_fundamentals — all 20 top candidates will trigger on-demand fetch"))
            else:
                print(f"       Total symbols: {fund_row.total}")
                msg_fresh = f"Fresh (< 12h): {fund_row.fresh}"
                msg_stale = f"Stale (12-48h): {fund_row.stale - fund_row.very_stale}"
                msg_very_stale = f"Very stale (> 48h): {fund_row.very_stale}"

                print(_ok(msg_fresh) if fund_row.fresh > 0 else _warn(msg_fresh))
                print(_warn(msg_stale) if (fund_row.stale - fund_row.very_stale) > 0 else _ok(msg_stale))
                print(_fail(msg_very_stale) if fund_row.very_stale > 0 else _ok(msg_very_stale))

                if fund_row.oldest_refresh:
                    print(f"       Oldest refresh: {fund_row.oldest_refresh.strftime('%Y-%m-%d %H:%M IST')}")
                    print(f"       Newest refresh: {fund_row.newest_refresh.strftime('%Y-%m-%d %H:%M IST')}")

            # Stale symbol list
            stale_fund_q = sa.text("""
                SELECT symbol, last_refreshed_at AT TIME ZONE 'Asia/Kolkata' AS refreshed_ist
                FROM stock_fundamentals
                WHERE last_refreshed_at <= NOW() - INTERVAL '48 hours'
                ORDER BY last_refreshed_at ASC
                LIMIT 20
            """)
            stale_fund_rows = (await session.execute(stale_fund_q)).all()
            if stale_fund_rows:
                print(_fail(f"  Symbols with fundamentals > 48h old (on-demand fetch will trigger):"))
                for sf in stale_fund_rows:
                    print(f"       {sf.symbol:<20} last_refresh={sf.refreshed_ist.strftime('%Y-%m-%d %H:%M')}")

        # ----------------------------------------------------------------
        # 4. Bhav copy (delivery %)
        # ----------------------------------------------------------------
        _section("4. NSE Bhav Copy  (delivery % factor)")

        bhav_key = f"nse:bhav_copy:{yesterday}"
        bhav_raw = await r.get(bhav_key)
        if bhav_raw is None:
            print(_fail(f"Redis key '{bhav_key}' NOT FOUND — delivery scores will default to 50.0 for all symbols"))
        else:
            import json
            bhav_data = json.loads(bhav_raw)
            sym_count = len(bhav_data)
            # Spot-check a common symbol
            sample = next((k for k in ["RELIANCE", "TCS", "INFY", "HDFCBANK"] if k in bhav_data), None)
            sample_val = f"  sample: {sample}={bhav_data[sample]}" if sample else "  (no common symbol found)"
            print(_ok(f"Key present for {yesterday}: {sym_count} symbols{sample_val}"))

        # Check if we have a gap (yesterday-1 also present)
        day_before = _previous_trading_day(yesterday)
        older_key = f"nse:bhav_copy:{day_before}"
        older_exists = await r.exists(older_key)
        if older_exists:
            print(_ok(f"Prior day key present: {older_key}"))
        else:
            print(_warn(f"Prior day key missing: {older_key}  (OI change computation needs 2 dates)"))

        # ----------------------------------------------------------------
        # 5. Global cues (Redis indicator:global:*)
        # ----------------------------------------------------------------
        _section("5. Global Market Cues  (stage 3 LLM context + halt/volatile flags)")

        global_fields = ["us_vix", "dow_futures_pct", "sp500_close_pct", "nasdaq_close_pct",
                         "nifty_pct", "crude_pct", "usdinr_pct", "dxy_pct"]
        all_present = True
        ttls = {}
        for field in global_fields:
            key = f"indicator:global:{field}"
            ttl = await r.ttl(key)
            val = await r.get(key)
            ttls[field] = (ttl, val)
            if ttl < 0:
                all_present = False

        max_ttl = 20 * 60  # 20 minutes
        missing = [f for f, (ttl, _) in ttls.items() if ttl < 0]
        present = [(f, ttl, v) for f, (ttl, v) in ttls.items() if ttl >= 0]

        if missing:
            print(_fail(f"Missing global cue keys: {missing}"))
            print("       These keys are written every 15 min by global_market_task.")
            print("       If missing, the halt/volatile check and stage 3 LLM will have no global context.")
        else:
            min_ttl = min(ttl for _, ttl, _ in present)
            age_seconds = max_ttl - min_ttl
            age_min = age_seconds / 60
            msg = f"All 8 global cue fields present  (oldest ~{age_min:.0f} min ago)"
            if age_min <= 20:
                print(_ok(msg))
            elif age_min <= 60:
                print(_warn(msg + "  — stale but within 1h"))
            else:
                print(_fail(msg + f"  — {age_min:.0f} min old, global_market_task may be down"))

            for field, ttl, val in present:
                age = (max_ttl - ttl) / 60
                indicator = f"{float(val):.3f}" if val else "N/A"
                flag = "✓" if age <= 20 else "⚠"
                print(f"       {flag}  {field:<25} value={indicator:<10} age~{age:.0f}m  ttl={ttl}s")

        # ----------------------------------------------------------------
        # 6. Strat5 screener Redis keys (today's run artifacts)
        # ----------------------------------------------------------------
        _section("6. Today's Screener Artifacts  (Redis strat5:* keys)")

        strat5_keys = {
            f"strat5:global_cues:{today}": "Global cues snapshot (8:00 AM)",
            f"strat5:morning_briefing:{today}": "Morning briefing (8:00 AM)",
            f"strat5:quant_scores:{today}": "Quant scores — Stage 1 (8:30 AM)",
            f"strat5:watchlist:{today}": "Final watchlist — Stage 3 (8:30 AM)",
            f"strat5:agent_status:{today}": "Agent status",
        }

        for key, label in strat5_keys.items():
            exists = await r.exists(key)
            ttl = await r.ttl(key)
            if exists:
                raw = await r.get(key)
                size = len(raw) if raw else 0
                print(_ok(f"{label:<45} key={key}  size={size}B"))
            else:
                print(_warn(f"{label:<45} key={key}  NOT FOUND (screener hasn't run yet or was cleared)"))

        # RVOL baselines — sample check
        rvol_keys = await r.keys("strat5:rvol_baseline:*")
        if rvol_keys:
            print(_ok(f"  RVOL baselines present: {len(rvol_keys)} symbols"))
        else:
            print(_warn("  RVOL baselines: none found (screener hasn't run yet today)"))

        # ----------------------------------------------------------------
        # Summary
        # ----------------------------------------------------------------
        _section("Summary")
        print("  Data sources checked:")
        print("    1. Daily candles        → market_data_1m (hour=0, minute=0 UTC)")
        print("    2. OI snapshots         → oi_snapshots (option_type='FUT')")
        print("    3. Stock fundamentals   → stock_fundamentals.last_refreshed_at")
        print("    4. Bhav copy            → Redis nse:bhav_copy:{prev_trading_day}")
        print("    5. Global cues          → Redis indicator:global:{field} (15-min TTL)")
        print("    6. Screener artifacts   → Redis strat5:* keys\n")

    finally:
        await r.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(audit())
