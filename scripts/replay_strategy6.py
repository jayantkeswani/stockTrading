"""Replay Strategy 6 (Breakout-Retest) signal generation over a historical window.

Strategy 6 is stateful and evaluated on EVERY 1m candle close (unlike the S5 replay,
which only ticks on 5m boundaries) — the retest/reclaim machine needs 1m precision.
For each trading day this walks the universe minute-by-minute, drives a fresh strategy
instance per (symbol, day), and PERSISTS the generated signals into the `signals`
table as strategy_name='breakout_retest' so the existing accuracy tool can read them:

    DATABASE_URL=...stocktrading_bt python scripts/replay_strategy6.py --start 2026-04-29 --end 2026-06-02
    DATABASE_URL=...stocktrading_bt python scripts/analyze_strategy5_signal_accuracy.py \
        --strategy breakout_retest --start 2026-04-29 --end 2026-06-02 --basis-adjust

Universe: the symbols S5 generated signals for on each day (so the first-touch
accuracy is a clean head-to-head vs S5 on the same stocks/days). entry/SL/target are
SPOT prices (the strategy runs on spot 1m; basis ~0, so the accuracy walk is faithful).

Fidelity caveats (offline approximations, documented): intraday_bias is computed with
global_cues=None and nifty_bias_score=None; nifty_day_change_pct is index-aligned by
minute; FUT-OI/screener enrichment is omitted (oi_factor defaults neutral). These shift
the confidence composite and a few gate-edge cases slightly vs live — trust the
DIRECTION of the head-to-head, then confirm magnitude on the live shadow A/B.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import IST, MARKET_CLOSE, MARKET_OPEN
from app.core.database import async_session_factory
from app.indicators.atr import compute_atr
from app.indicators.candle_patterns import Candle
from app.indicators.intraday_bias import compute_intraday_bias
from app.indicators.previous_day import PreviousDayLevels, analyze_previous_day
from app.indicators.vwap import calculate_vwap
from app.services.strategy_params import BREAKOUT_RETEST_DEFAULTS
from app.strategies.base import MarketContext
from app.strategies.strategy_6_breakout_retest import BreakoutRetestStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


# ── DB helpers ──────────────────────────────────────────────────────────────

async def fetch_candles_1m(session, symbol, from_ts, to_ts) -> list[tuple[datetime, Candle]]:
    """Return (ist_timestamp, Candle) pairs — real timestamps so signals can be
    stamped with the actual reclaim-candle time (the accuracy walk anchors on it)."""
    from sqlalchemy import text
    result = await session.execute(
        text("""
            SELECT DISTINCT ON (date_trunc('minute', timestamp))
                timestamp, open, high, low, close, volume
            FROM market_data_1m
            WHERE symbol = :symbol AND timestamp BETWEEN :from_ts AND :to_ts
            ORDER BY date_trunc('minute', timestamp), volume DESC, timestamp
        """),
        {"symbol": symbol, "from_ts": from_ts, "to_ts": to_ts},
    )
    return [
        (r.timestamp.astimezone(IST),
         Candle(open=float(r.open), high=float(r.high), low=float(r.low),
                close=float(r.close), volume=int(r.volume or 0)))
        for r in result.all()
    ]


async def fetch_daily_candles(session, symbol, days=30) -> list[Candle]:
    from sqlalchemy import text
    result = await session.execute(
        text("""SELECT open, high, low, close, volume FROM market_data_daily
                WHERE symbol = :symbol ORDER BY date DESC LIMIT :days"""),
        {"symbol": symbol, "days": days},
    )
    return [Candle(open=float(r.open), high=float(r.high), low=float(r.low),
                   close=float(r.close), volume=int(r.volume or 0)) for r in reversed(result.all())]


async def get_previous_day_levels(session, symbol, today) -> PreviousDayLevels | None:
    from app.services.candle_backfill import _previous_trading_day
    prev = _previous_trading_day(today)
    candles = await fetch_candles_1m(
        session, symbol,
        datetime.combine(prev, MARKET_OPEN, tzinfo=IST),
        datetime.combine(prev, MARKET_CLOSE, tzinfo=IST),
    )
    candles = [c for _, c in candles]
    if not candles:
        return None
    return analyze_previous_day(
        open_price=candles[0].open, high=max(c.high for c in candles),
        low=min(c.low for c in candles), close=candles[-1].close,
    )


async def fetch_signal_universe(session, start, end) -> dict[date, list[str]]:
    """Map each trading day → the symbols S5 generated signals for (the replay universe)."""
    from sqlalchemy import text
    result = await session.execute(
        text("""
            SELECT DISTINCT (generated_at AT TIME ZONE 'Asia/Kolkata')::date AS d, symbol
            FROM signals
            WHERE strategy_name = 'intraday_futures'
              AND generated_at >= :start AND generated_at < :end
            ORDER BY d, symbol
        """),
        {"start": datetime.combine(start, dt_time(0, 0), tzinfo=IST),
         "end": datetime.combine(end + timedelta(days=1), dt_time(0, 0), tzinfo=IST)},
    )
    universe: dict[date, list[str]] = {}
    for r in result.all():
        universe.setdefault(r.d, []).append(r.symbol)
    return universe


def build_vwap(candles_1m: list[Candle]):
    if not candles_1m:
        return None
    return calculate_vwap([c.high for c in candles_1m], [c.low for c in candles_1m],
                          [c.close for c in candles_1m], [c.volume for c in candles_1m])


# ── Replay engine ───────────────────────────────────────────────────────────

async def replay_symbol_day(session, symbol, day, prev_day, daily, nifty_1m) -> list[dict]:
    """Drive Strategy 6 minute-by-minute over one symbol-day; return generated signals."""
    day_start = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
    day_end = datetime.combine(day, MARKET_CLOSE, tzinfo=IST)
    pairs = await fetch_candles_1m(session, symbol, day_start, day_end)
    if len(pairs) < 10:
        return []
    ts_list = [t for t, _ in pairs]
    candle_list = [c for _, c in pairs]

    nifty_candles = [c for _, c in nifty_1m]
    nifty_open = nifty_candles[0].open if nifty_candles else None
    strategy = BreakoutRetestStrategy()
    out: list[dict] = []

    for i in range(len(candle_list)):
        so_far = candle_list[: i + 1]
        sim_time = ts_list[i]                 # real candle timestamp (IST)
        price = so_far[-1].close
        vwap = build_vwap(so_far)
        atr_5m = compute_atr(so_far, period=14) if len(so_far) >= 15 else None

        bias = None
        if prev_day:
            bias = compute_intraday_bias(
                prev_day=prev_day, candles_1m=so_far, vwap=vwap, current_price=price,
                global_cues=None, as_of=sim_time, nifty_bias_score=None,
            )

        params = dict(BREAKOUT_RETEST_DEFAULTS)
        if nifty_open and nifty_candles:
            nclose = nifty_candles[min(i, len(nifty_candles) - 1)].close
            params["_nifty_day_change_pct"] = round((nclose - nifty_open) / nifty_open * 100, 2)

        ctx = MarketContext(
            symbol=symbol, current_price=price, candles_5m=[], vwap=vwap,
            previous_day=prev_day, cpr=None, oi_analysis=None, india_vix=None,
            current_time_ist=sim_time.isoformat(), candles_daily=daily or None,
            intraday_bias=bias, atr_5m=atr_5m, strategy_params=params,
            candles_1m=so_far,
        )
        with patch("app.core.utils.now_ist", return_value=sim_time):
            sig = strategy.evaluate(ctx)
        strategy.drain_pending_logs()
        if sig is not None:
            out.append({
                "symbol": symbol, "signal_type": sig.signal_type.value,
                "entry_price": round(sig.entry_price, 2), "stop_loss": round(sig.stop_loss, 2),
                "target_price": round(sig.target_price, 2) if sig.target_price else None,
                "confidence": sig.confidence, "reason": sig.reason,
                "generated_at": sim_time, "indicators": sig.indicators,
            })
    return out


async def persist_signals(session, signals: list[dict], start, end) -> None:
    """Replace breakout_retest signals in the window with the freshly replayed set."""
    from sqlalchemy import text
    await session.execute(
        text("""DELETE FROM signals WHERE strategy_name='breakout_retest'
                AND generated_at >= :start AND generated_at < :end"""),
        {"start": datetime.combine(start, dt_time(0, 0), tzinfo=IST),
         "end": datetime.combine(end + timedelta(days=1), dt_time(0, 0), tzinfo=IST)},
    )
    for s in signals:
        await session.execute(
            text("""
                INSERT INTO signals (
                    id, strategy_name, symbol, signal_type, strike_price, expiry_date,
                    entry_price, stop_loss, target_price, confidence, status, reason,
                    indicators, generated_at, executable, instrument_type, is_permanent_watchlist,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), 'breakout_retest', :symbol, :signal_type, 0,
                    :expiry_date, :entry_price, :stop_loss, :target_price, :confidence,
                    'PENDING', :reason, (:indicators)::jsonb, :generated_at, true, 'FUTURE', false,
                    now(), now()
                )
            """),
            # NOTE: expiry_date is a SEPARATE param, not (:generated_at)::date — reusing the
            # generated_at placeholder under a ::date cast makes asyncpg infer a DATE type for
            # the single placeholder, which silently truncates the generated_at timestamp too.
            {**{k: s[k] for k in ("symbol", "signal_type", "entry_price", "stop_loss",
                                  "target_price", "confidence", "reason", "generated_at")},
             "expiry_date": s["generated_at"].date(),
             "indicators": json.dumps(s["indicators"], default=str)},
        )
    await session.commit()


async def run(start, end, symbol_filter, persist):
    async with async_session_factory() as session:
        universe = await fetch_signal_universe(session, start, end)
        if not universe:
            logger.error("No S5 signals in window — nothing to define the replay universe.")
            return
        nifty_cache: dict[date, list[Candle]] = {}
        all_signals: list[dict] = []

        for day in sorted(universe):
            syms = universe[day]
            if symbol_filter:
                syms = [s for s in syms if s in symbol_filter]
            if not syms:
                continue
            if day not in nifty_cache:
                nifty_cache[day] = await fetch_candles_1m(
                    session, "NIFTY",
                    datetime.combine(day, MARKET_OPEN, tzinfo=IST),
                    datetime.combine(day, MARKET_CLOSE, tzinfo=IST),
                )
            day_sigs: list[dict] = []
            for sym in syms:
                prev_day = await get_previous_day_levels(session, sym, day)
                daily = await fetch_daily_candles(session, sym, 30)
                day_sigs += await replay_symbol_day(
                    session, sym, day, prev_day, daily, nifty_cache[day]
                )
            logger.info("%s: %d symbols → %d signals", day, len(syms), len(day_sigs))
            all_signals += day_sigs

        by_setup: dict[str, int] = {}
        for s in all_signals:
            k = s["indicators"].get("setup_type", "?")
            by_setup[k] = by_setup.get(k, 0) + 1
        logger.info("TOTAL: %d breakout_retest signals  by setup: %s", len(all_signals), by_setup)

        if persist:
            await persist_signals(session, all_signals, start, end)
            logger.info("Persisted %d signals to the DB (strategy_name=breakout_retest).", len(all_signals))


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def main():
    p = argparse.ArgumentParser(description="Replay Strategy 6 (Breakout-Retest) over a window")
    p.add_argument("--start", type=_parse_date, required=True)
    p.add_argument("--end", type=_parse_date, default=None)
    p.add_argument("--symbols", default=None, help="comma-separated symbol filter")
    p.add_argument("--no-persist", action="store_true", help="don't write signals to the DB")
    args = p.parse_args()
    asyncio.run(run(
        args.start, args.end or args.start,
        args.symbols.split(",") if args.symbols else None,
        not args.no_persist,
    ))


if __name__ == "__main__":
    main()
