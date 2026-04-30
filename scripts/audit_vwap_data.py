"""One-time audit: check freshness and availability of all data sources used by Strategy 2
(VWAP Pullback + Previous Day Context + OI Confirmation).

Run from the backend virtualenv:
    cd /path/to/stockTrading
    source backend/.venv/bin/activate
    python scripts/audit_vwap_data.py

No side effects — read-only queries.
"""

import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")

IST_OFFSET = timedelta(hours=5, minutes=30)
NSE_HOLIDAYS = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 3, 31), date(2025, 4, 14), date(2025, 4, 18),
    date(2025, 5, 1), date(2025, 8, 15), date(2025, 8, 27),
    date(2025, 10, 2), date(2025, 10, 24), date(2025, 11, 5),
    date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 2, 19), date(2026, 3, 20),
    date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 10),
    date(2026, 4, 14), date(2026, 4, 17), date(2026, 5, 1),
}

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
FULL_DAY_CANDLES = 375  # 9:15–15:30 inclusive = 375 one-minute candles
MIN_CANDLES_FOR_EVAL = 5  # strategy returns None if candles_5m < 5 (i.e. 25 1m candles)


def _ist_now() -> datetime:
    return datetime.utcnow() + IST_OFFSET


def _previous_trading_day(ref: date) -> date:
    d = ref - timedelta(days=1)
    while d.weekday() >= 5 or d in NSE_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def _is_market_open(now: datetime) -> bool:
    t = now.time()
    return now.weekday() < 5 and not now.date() in NSE_HOLIDAYS and MARKET_OPEN <= t <= MARKET_CLOSE


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
    now_ist = _ist_now()
    today = now_ist.date()
    yesterday = _previous_trading_day(today)
    market_open_now = _is_market_open(now_ist)

    print(f"\n\033[1mStrategy 2 (VWAP Pullback) — Data Freshness Audit\033[0m")
    print(f"  Audit time (IST): {now_ist.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Today: {today}  |  Prev trading day: {yesterday}")
    print(f"  Market is currently: {'OPEN' if market_open_now else 'CLOSED'}")

    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    r = await aioredis.from_url(REDIS_URL, decode_responses=True)

    try:
        async with AsyncSessionLocal() as session:

            # ----------------------------------------------------------------
            # 1. Strategy configuration
            # ----------------------------------------------------------------
            _section("1. Strategy Configuration  (DB strategy_configs)")

            cfg_q = sa.text("""
                SELECT strategy_name, is_active, auto_mode, symbols, parameters
                FROM strategy_configs
                WHERE strategy_name = 'vwap_pullback'
            """)
            cfg_row = (await session.execute(cfg_q)).one_or_none()

            if cfg_row is None:
                print(_fail("No 'vwap_pullback' row found in strategy_configs — strategy is not seeded"))
                configured_symbols = []
            else:
                active_flag = "ACTIVE" if cfg_row.is_active else "INACTIVE"
                auto_flag = "auto_mode=ON" if cfg_row.auto_mode else "auto_mode=OFF"
                print(_ok(f"Strategy found: {active_flag}  {auto_flag}"))

                configured_symbols = cfg_row.symbols or []
                if configured_symbols:
                    print(_ok(f"Configured symbols ({len(configured_symbols)}): {', '.join(configured_symbols)}"))
                else:
                    print(_warn("No symbols configured for strategy — evaluation will only run if symbols are set"))

                params = cfg_row.parameters or {}
                key_params = {
                    k: params[k] for k in [
                        "vwap_proximity_pct", "sl_pct_aligned", "sl_pct_unaligned",
                        "default_target_multiplier", "min_confidence_to_persist",
                    ] if k in params
                }
                if key_params:
                    print(f"       Key params (DB overrides): {key_params}")
                else:
                    print(f"       Key params: using defaults (no DB overrides)")

            # ----------------------------------------------------------------
            # 2. Previous day 1m candles — source of PDH/PDL/PDC/CPR/bias
            # ----------------------------------------------------------------
            _section(f"2. Previous Day 1m Candles  (market_data_1m, date={yesterday})")
            print(f"       Used for: PDH, PDL, PDC, CPR, intraday_bias (yesterday close pos)")

            symbols_to_check = configured_symbols if configured_symbols else ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"]

            for sym in symbols_to_check:
                prev_q = sa.text("""
                    SELECT
                        COUNT(*) AS candle_count,
                        MIN(timestamp AT TIME ZONE 'Asia/Kolkata') AS first_ts,
                        MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS last_ts,
                        MIN(low) AS day_low,
                        MAX(high) AS day_high,
                        (SELECT close FROM market_data_1m
                         WHERE symbol = :sym
                           AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :prev_day
                         ORDER BY timestamp DESC LIMIT 1) AS day_close
                    FROM market_data_1m
                    WHERE symbol = :sym
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :prev_day
                      AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'Asia/Kolkata') != 0
                """)
                row = (await session.execute(prev_q, {"sym": sym, "prev_day": yesterday})).one()

                if row.candle_count == 0:
                    print(_fail(f"  {sym:<15} NO candles for {yesterday}  → PDH/PDL/PDC/CPR will be None → strategy returns None"))
                else:
                    pct = row.candle_count / FULL_DAY_CANDLES * 100
                    msg = (
                        f"  {sym:<15} {row.candle_count:>3} candles ({pct:.0f}% of full day)"
                        f"  H={float(row.day_high):.2f}  L={float(row.day_low):.2f}"
                        f"  C={float(row.day_close):.2f}" if row.day_close else ""
                    )
                    if row.candle_count >= 300:
                        print(_ok(msg))
                    elif row.candle_count >= 100:
                        print(_warn(msg + "  — partial day (late start or holiday?)"))
                    else:
                        print(_fail(msg + "  — too few candles, bias/CPR unreliable"))

            # ----------------------------------------------------------------
            # 3. Today's 1m candles — VWAP source + 5m aggregation
            # ----------------------------------------------------------------
            _section(f"3. Today's 1m Candles  (market_data_1m, date={today})")
            print(f"       Used for: VWAP (from today's candles), 5m aggregation (min 25 candles needed)")

            for sym in symbols_to_check:
                today_q = sa.text("""
                    SELECT
                        COUNT(*) AS candle_count,
                        MIN(timestamp AT TIME ZONE 'Asia/Kolkata') AS first_ts,
                        MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS last_ts
                    FROM market_data_1m
                    WHERE symbol = :sym
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
                      AND EXTRACT(HOUR FROM timestamp AT TIME ZONE 'Asia/Kolkata') != 0
                """)
                row = (await session.execute(today_q, {"sym": sym, "today": today})).one()

                if row.candle_count == 0:
                    if market_open_now:
                        print(_fail(f"  {sym:<15} NO candles for today — VWAP will be None → strategy returns None (market is open, feed may be down)"))
                    else:
                        print(_warn(f"  {sym:<15} No candles for today (market is closed — expected)"))
                else:
                    last_ts_str = row.last_ts.strftime('%H:%M') if row.last_ts else "?"
                    can_eval = row.candle_count >= 25
                    can_eval_flag = "can evaluate" if can_eval else f"NEED ≥25 (strategy requires 5x 5m candles)"
                    msg = f"  {sym:<15} {row.candle_count:>3} candles  last={last_ts_str}  — {can_eval_flag}"
                    if can_eval:
                        print(_ok(msg))
                    else:
                        print(_warn(msg))

            # ----------------------------------------------------------------
            # 4. Index OI Snapshots (CE/PE) — OI analysis for signal + confidence
            # ----------------------------------------------------------------
            _section("4. Index OI Snapshots  (oi_snapshots, option_type=CE/PE)")
            print("       Used for: PCR, max pain, max CE/PE OI strike, oi_support confidence factor")

            for sym in symbols_to_check:
                oi_q = sa.text("""
                    SELECT
                        MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS latest_ts,
                        COUNT(DISTINCT option_type) AS ot_types,
                        COUNT(*) AS row_count,
                        COUNT(DISTINCT strike_price) AS strike_count
                    FROM oi_snapshots
                    WHERE symbol = :sym
                      AND option_type IN ('CE', 'PE')
                """)
                row = (await session.execute(oi_q, {"sym": sym})).one()

                if row.latest_ts is None:
                    print(_fail(f"  {sym:<15} NO OI snapshots found — oi_analysis will be None (OI factor defaults to 0.5)"))
                    continue

                age_min = (now_ist - row.latest_ts).total_seconds() / 60
                msg = (
                    f"  {sym:<15} latest={row.latest_ts.strftime('%Y-%m-%d %H:%M')}  "
                    f"age~{age_min:.0f}m  {row.strike_count} strikes  {row.row_count} rows"
                )

                if market_open_now:
                    if age_min <= 4:
                        print(_ok(msg + "  — current ✓"))
                    elif age_min <= 15:
                        print(_warn(msg + "  — slightly stale (oi_snapshot_task may be slow)"))
                    else:
                        print(_fail(msg + f"  — {age_min:.0f}m stale ✗  (oi_snapshot_task may be down)"))
                else:
                    # Outside market hours: data from last session is fine
                    age_days = (today - row.latest_ts.date()).days
                    if age_days == 0 or (age_days == 1 and today.weekday() == 0):
                        print(_ok(msg + "  — from last session ✓"))
                    else:
                        print(_warn(msg + f"  — {age_days} day(s) old (ok if weekend/holiday)"))

            # ----------------------------------------------------------------
            # 5. Index futures candles (for volume sourcing)
            # ----------------------------------------------------------------
            _section(f"5. Index Futures 1m Candles  (market_data_1m, *_FUT symbols)")
            print("       Used for: reliable futures volume in 5m candles → volume_quality confidence factor")
            print("       (Fyers index volume is unreliable — cumulative spikes on reconnect)")

            fut_symbols = [f"{sym}_FUT" for sym in symbols_to_check]
            for fut_sym in fut_symbols:
                today_q = sa.text("""
                    SELECT
                        COUNT(*) AS candle_count,
                        MAX(timestamp AT TIME ZONE 'Asia/Kolkata') AS last_ts
                    FROM market_data_1m
                    WHERE symbol = :sym
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
                """)
                row = (await session.execute(today_q, {"sym": fut_sym, "today": today})).one()

                prev_q = sa.text("""
                    SELECT COUNT(*) AS candle_count
                    FROM market_data_1m
                    WHERE symbol = :sym
                      AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :prev_day
                """)
                prev_row = (await session.execute(prev_q, {"sym": fut_sym, "prev_day": yesterday})).one()

                today_count = row.candle_count
                prev_count = prev_row.candle_count

                if today_count == 0 and prev_count == 0:
                    print(_warn(f"  {fut_sym:<18} No candles found — strategy falls back to index candle volume (less reliable)"))
                elif today_count == 0 and not market_open_now:
                    print(_ok(f"  {fut_sym:<18} prev_day={prev_count} candles  today=0 (market closed — expected)"))
                elif today_count > 0:
                    last_str = row.last_ts.strftime('%H:%M') if row.last_ts else "?"
                    print(_ok(f"  {fut_sym:<18} today={today_count} candles  last={last_str}  prev_day={prev_count}"))
                else:
                    print(_warn(f"  {fut_sym:<18} prev_day={prev_count} candles  today=0 (market open — futures feed may be lagging)"))

        # ----------------------------------------------------------------
        # 6. India VIX Redis price cache
        # ----------------------------------------------------------------
        _section("6. India VIX  (Redis price:INDIA VIX)")
        print("       Used for: vix_regime confidence factor, strategy-level VIX cap check")

        vix_raw = await r.get("price:INDIA VIX")
        if vix_raw is None:
            print(_fail("Redis key 'price:INDIA VIX' NOT FOUND — india_vix will be None"))
            print("       VIX regime factor defaults to 0.5 (neutral); VIX cap check will be skipped")
        else:
            vix_data = json.loads(vix_raw)
            ltp = vix_data.get("ltp", "?")
            print(_ok(f"Key present  ltp={ltp}"))
            if isinstance(ltp, (int, float)):
                vix_val = float(ltp)
                if vix_val < 14:
                    regime = "LOW (<14) — full position sizing"
                elif vix_val < 18:
                    regime = "NORMAL (14-18)"
                elif vix_val < 22:
                    regime = "HIGH (18-22) — reduced sizing"
                else:
                    regime = "EXTREME (≥22) — strategy blocked by VIX cap"
                print(f"       VIX regime: {regime}")

        # ----------------------------------------------------------------
        # 7. Global cues (Redis indicator:global:*)
        # ----------------------------------------------------------------
        _section("7. Global Market Cues  (Redis indicator:global:*)")
        print("       Used for: global_alignment confidence factor, intraday_bias global component")

        global_fields = ["us_vix", "dow_futures_pct", "sp500_close_pct", "nasdaq_close_pct",
                         "nifty_pct", "crude_pct", "usdinr_pct", "dxy_pct"]
        MAX_TTL = 20 * 60  # keys are written with 20-min TTL
        missing = []
        present = []
        for field in global_fields:
            key = f"indicator:global:{field}"
            ttl = await r.ttl(key)
            val = await r.get(key)
            if ttl < 0:
                missing.append(field)
            else:
                age_sec = MAX_TTL - ttl
                present.append((field, ttl, val, age_sec))

        if missing:
            print(_fail(f"Missing global cue keys: {missing}"))
            print("       global_alignment confidence factor will default to 0.5; intraday_bias global component = 0")
        else:
            min_age = min(age for _, _, _, age in present)
            max_age = max(age for _, _, _, age in present)
            age_min = max_age / 60
            msg = f"All 8 global cue fields present  (oldest ~{age_min:.0f} min ago)"
            if age_min <= 20:
                print(_ok(msg))
            elif age_min <= 60:
                print(_warn(msg + "  — stale but within 1h"))
            else:
                print(_fail(msg + f"  — {age_min:.0f} min old, global_market_task may be down"))

        for field, ttl, val, age_sec in present:
            age_m = age_sec / 60
            indicator = f"{float(val):.3f}" if val else "N/A"
            flag = "✓" if age_m <= 20 else "⚠"
            print(f"       {flag}  {field:<25} value={indicator:<10} age~{age_m:.0f}m  ttl={ttl}s")

        # ----------------------------------------------------------------
        # 8. Index price cache (Redis price:*)
        # ----------------------------------------------------------------
        _section("8. Index Price Cache  (Redis price:*)")
        print("       Used for: ctx.current_price — the live tick that triggers VWAP pullback check")

        index_symbols = configured_symbols if configured_symbols else ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"]
        for sym in index_symbols:
            price_raw = await r.get(f"price:{sym}")
            if price_raw is None:
                if market_open_now:
                    print(_fail(f"  {sym:<15} NOT in price cache — feed may be down (market is open)"))
                else:
                    print(_warn(f"  {sym:<15} Not in price cache (market is closed — expected)"))
            else:
                data = json.loads(price_raw)
                ltp = data.get("ltp", "?")
                print(_ok(f"  {sym:<15} ltp={ltp}"))

        # ----------------------------------------------------------------
        # 9. Symbol master freshness
        # ----------------------------------------------------------------
        _section("9. Symbol Master  (Redis symbols:master)")
        print("       Used by: option_resolver to find ATM/ITM option contract after signal fires")

        master_updated = await r.get("symbols:master:updated_at")
        master_exists = await r.exists("symbols:master")

        if not master_exists:
            print(_fail("Redis key 'symbols:master' NOT FOUND — option resolver will fail on every signal"))
        else:
            if master_updated:
                try:
                    updated_at = datetime.fromisoformat(master_updated)
                    age_hours = (datetime.utcnow() - updated_at).total_seconds() / 3600
                    msg = f"Symbol master present  updated_at={master_updated}  age={age_hours:.1f}h"
                    if age_hours <= 24:
                        print(_ok(msg))
                    elif age_hours <= 48:
                        print(_warn(msg + "  — stale (symbol_master_task may not have run today)"))
                    else:
                        print(_fail(msg + "  — very stale ✗  (rolled contracts may not be found)"))
                except ValueError:
                    print(_ok(f"Symbol master present  updated_at={master_updated}"))
            else:
                print(_warn("Symbol master key exists but updated_at not found — cannot verify freshness"))

        # ----------------------------------------------------------------
        # 10. Signal dedup — recent PENDING signals
        # ----------------------------------------------------------------
        async with AsyncSessionLocal() as session:
            _section("10. Recent PENDING Signals  (signals table)")
            print("       Strategy 2 deduplicates identical pending signals — shows current backlog")

            pending_q = sa.text("""
                SELECT symbol, signal_type, confidence, created_at AT TIME ZONE 'Asia/Kolkata' AS created_ist
                FROM signals
                WHERE strategy_name = 'vwap_pullback'
                  AND status = 'PENDING'
                ORDER BY created_at DESC
                LIMIT 10
            """)
            pending_rows = (await session.execute(pending_q)).all()

            if not pending_rows:
                print(_ok("No pending Strategy 2 signals (clean slate)"))
            else:
                print(_warn(f"  {len(pending_rows)} pending signal(s) — these will be updated in-place (not duplicated) on next eval:"))
                for p in pending_rows:
                    print(f"       {p.symbol:<15} {p.signal_type:<10} conf={p.confidence:.0f}  created={p.created_ist.strftime('%Y-%m-%d %H:%M')}")

            # Last 5 generated signals (any status)
            recent_q = sa.text("""
                SELECT symbol, signal_type, status, confidence, created_at AT TIME ZONE 'Asia/Kolkata' AS created_ist
                FROM signals
                WHERE strategy_name = 'vwap_pullback'
                ORDER BY created_at DESC
                LIMIT 5
            """)
            recent_rows = (await session.execute(recent_q)).all()

            if recent_rows:
                print(f"\n       Last 5 Strategy 2 signals (any status):")
                for p in recent_rows:
                    print(f"       {p.symbol:<15} {p.signal_type:<10} {p.status:<12} conf={p.confidence:.0f}  {p.created_ist.strftime('%Y-%m-%d %H:%M')}")

        # ----------------------------------------------------------------
        # Summary
        # ----------------------------------------------------------------
        _section("Summary — What each missing source breaks")
        print("  Source → Impact if missing:")
        print("    1. Strategy config          → Strategy not evaluated at all")
        print("    2. Previous day 1m candles  → PDH/PDL/PDC/CPR/bias all None → evaluate() returns None")
        print("    3. Today's 1m candles       → VWAP None, no 5m candles → evaluate() returns None")
        print("    4. Index OI (CE/PE)         → oi_analysis None → oi_support factor defaults to 0.5")
        print("    5. Index futures candles    → falls back to index volume (unreliable for volume filter)")
        print("    6. India VIX                → vix_regime factor defaults to 0.5; VIX cap skipped")
        print("    7. Global cues              → global_alignment factor defaults to 0.5; bias global=0")
        print("    8. Price cache              → ctx.current_price stale/missing → no evaluation")
        print("    9. Symbol master            → option resolver fails → signal not resolv'd to contract")
        print("   10. Pending signals          → dedup logic; stale PENDING signals are updated not re-created\n")

    finally:
        await r.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(audit())
